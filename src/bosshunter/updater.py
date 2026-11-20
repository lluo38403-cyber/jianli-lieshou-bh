"""One-click self-update for the installed exe (automatic update).

Flow: the panel asks the cloud relay for the newest published version
(GET {relay_url}/api/version, X-App-Token). When a newer version exists,
the user can press one button: the backend downloads the release zip
from the relay (GET {relay_url}/download/latest), verifies its sha256,
extracts it to a staging directory, validates the staged payload
(exe + _internal must both be there), backs up the current exe payload,
writes update-pending.json (expected version) and update.bat next to
the exe, then exits; the batch file waits for the process (and any
other copy of the exe, plus its child processes) to exit, copies
staging over the install directory, purges files the new version
removed from _internal, verifies the new exe exists, relaunches it
and cleans up. On any failure the batch file restores the backup,
writes update-result.json and relaunches the old exe; the restarted
process consumes update-pending.json, compares its own version with
the expected one, and — when they disagree — reports the failure
instead of prompting for the same update again.

Safety rules (do not weaken them):
- The download URL only ever comes from the configured relay_url; no
  caller-supplied URL is accepted anywhere.
- A sha256 mismatch aborts before anything on disk is touched.
- A staged payload without the exe or without _internal is rejected
  before anything on disk is touched: a wrong zip layout must never
  turn into a half-swapped install.
- config.yaml and data/ are never overwritten, deleted or moved, neither
  by the Python overlay nor by the batch file (robocopy /XD data
  /XF config.yaml); keep the two in sync.
- Every failure is reported in plain language and never blocks the
  normal job-hunting flow; update checks fail silent.
- The file swap itself is a Windows batch file and can only be proven
  on a real Windows machine: tests cover everything up to the batch
  file's contents, not its execution.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

import httpx

from bosshunter import __version__
from bosshunter.ai.credentials import get_relay_token, get_relay_url

CHECK_TIMEOUT = 10.0
DOWNLOAD_TIMEOUT = 300.0
CHECK_CACHE_SECONDS = 600
UPDATE_BACKUP_PREFIX = "update-backup-"
UPDATE_PENDING_FILENAME = "update-pending.json"
UPDATE_RESULT_FILENAME = "update-result.json"
EXE_NAME = "简历猎手.exe"

# Machine-readable failure codes the batch file writes into
# update-result.json. They stay ASCII on purpose: update.bat is written
# as UTF-8 without BOM and cmd.exe on a Chinese Windows box reads it
# in the local code page, so Chinese text in that JSON would come out
# garbled; the plain-language message lives here instead.
UPDATE_FAILURE_MESSAGES = {
    "stuck": "主程序关不掉，更新没换成。请先把程序完全退出，再重新点一次更新。",
    "nopkg": "安装包里找不到新版本，更新没换成。请重新点一次更新。",
    "copy": "换文件的时候出问题了，已经换回旧版本。请重新点一次更新。",
    "mismatch": "新版本换上去了，但打开还是旧版本，可能是旧文件没换掉。请重新点一次更新。",
    "unknown": "上次更新没换完，请重新点一次更新。",
}

# Top-level entries of an install directory that an update must never touch.
PRESERVED_TOP_LEVEL = ("config.yaml", "data")


class UpdateError(RuntimeError):
    """Update failure with a plain-language message safe to show the user."""

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


# ─── Version compare ────────────────────────────────────────

_VERSION_PART_RE = re.compile(r"^(\d+)")


def parse_version(value: object) -> tuple[int, ...] | None:
    """Parse "1.0.1" / "v1.2" / "1.10.0" into a numeric tuple.

    Returns None for anything that is not a dotted numeric version, so a
    bad value can never be mistaken for a newer release.
    """
    if not isinstance(value, str):
        return None
    text = value.strip().lstrip("vV").strip()
    if not text:
        return None
    parts: list[int] = []
    for raw in text.split("."):
        match = _VERSION_PART_RE.match(raw.strip())
        if not match:
            return None
        parts.append(int(match.group(1)))
    return tuple(parts) or None


def _pad(version: tuple[int, ...], length: int) -> tuple[int, ...]:
    return version + (0,) * (length - len(version))


def is_newer(current: object, latest: object) -> bool:
    """True when `latest` is numerically newer than `current`.

    Numeric per part, so 1.10.0 > 1.0.9; "1.0" counts as "1.0.0".
    Unparseable input is never newer.
    """
    cur = parse_version(current)
    new = parse_version(latest)
    if cur is None or new is None:
        return False
    length = max(len(cur), len(new))
    return _pad(new, length) > _pad(cur, length)


def _coerce_timeout(timeout: float, default: float) -> float:
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return min(value, 300.0)


# ─── Check for a newer release ──────────────────────────────

def check_for_update(config: dict, timeout: float = CHECK_TIMEOUT) -> dict | None:
    """Ask the relay for the newest published version.

    Returns the release info (version/size/sha256/notes) when a newer
    version exists, else None. Never raises: relay down, timeout,
    unexpected status or a bad payload all mean "no update".
    """
    url = get_relay_url(config).rstrip("/")
    token = get_relay_token(config)
    if not token:
        return None
    try:
        resp = httpx.get(
            f"{url}/api/version",
            headers={"X-App-Token": token},
            timeout=_coerce_timeout(timeout, CHECK_TIMEOUT),
            trust_env=False,
        )
    except Exception:
        return None
    if resp.status_code == 404:
        return None
    try:
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    version = payload.get("version")
    sha256 = payload.get("sha256")
    if not isinstance(version, str) or not is_newer(__version__, version):
        return None
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        return None
    size = payload.get("size")
    notes = payload.get("notes")
    return {
        "version": version,
        "current_version": __version__,
        "size": size if isinstance(size, int) and size > 0 else 0,
        "sha256": sha256.lower(),
        "notes": notes if isinstance(notes, str) else "",
        "update_available": True,
    }


# ─── Download + verify + stage ──────────────────────────────

def _remove_quietly(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()
    except OSError:
        pass


def download_update(
    config: dict,
    info: dict,
    target_dir: str | Path,
    progress_cb: Callable[[int, int], None] | None = None,
    timeout: float = DOWNLOAD_TIMEOUT,
) -> Path:
    """Download the release zip from the relay and verify its sha256.

    Only ever talks to the configured relay_url. Raises UpdateError
    (plain language) on any failure; a failed download never leaves a
    partial file behind.
    """
    url = get_relay_url(config).rstrip("/")
    token = get_relay_token(config)
    if not token:
        raise UpdateError("没有配好下载地址，先用现在的版本也行。")
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    zip_path = target / f"jianli-lieshou-{info['version']}.zip"
    digest = hashlib.sha256()
    downloaded = 0
    total = int(info.get("size") or 0)
    try:
        with httpx.stream(
            "GET",
            f"{url}/download/latest",
            headers={"X-App-Token": token},
            timeout=_coerce_timeout(timeout, DOWNLOAD_TIMEOUT),
            trust_env=False,
        ) as resp:
            resp.raise_for_status()
            with open(zip_path, "wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    if progress_cb is not None:
                        progress_cb(downloaded, total)
    except httpx.HTTPStatusError as exc:
        _remove_quietly(zip_path)
        if exc.response is not None and exc.response.status_code == 404:
            raise UpdateError("服务器上没有找到新版本，可能刚撤下来了。")
        raise UpdateError("新版本没下载下来，请检查网络后重试。")
    except Exception:
        _remove_quietly(zip_path)
        raise UpdateError("新版本没下载下来，请检查网络后重试。")
    if digest.hexdigest() != str(info["sha256"]).lower():
        _remove_quietly(zip_path)
        raise UpdateError("下载的文件对不上，怕文件坏了，这次先不换。")
    return zip_path


def extract_update(zip_path: str | Path, staging_dir: str | Path) -> Path:
    """Extract a verified release zip into a staging directory.

    Rejects zip-slip entries (absolute paths / "..") instead of
    extracting them. Returns the staging directory.
    """
    staging = Path(staging_dir)
    staging.mkdir(parents=True, exist_ok=True)
    root = staging.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            target = (staging / member.filename).resolve()
            if target != root and not str(target).startswith(str(root) + os.sep):
                raise UpdateError("安装包里的文件路径不对，这次先不换。")
        zf.extractall(staging)
    return staging


def find_payload_root(staging_dir: str | Path) -> Path:
    """Find the install-layout root inside staging.

    Release zips carry one top folder (简历猎手-windows/); the payload
    root is the directory that holds the exe *and* _internal. The
    search is recursive (a doubly-nested top folder still resolves)
    and prefers the shallowest match. Falls back to staging itself
    when no directory holds both — validate_staged_payload() then
    rejects the package instead of swapping in half a layout.
    """
    staging = Path(staging_dir)

    def _is_root(candidate: Path) -> bool:
        return (candidate / EXE_NAME).is_file() and (candidate / "_internal").is_dir()

    if _is_root(staging):
        return staging
    queue: list[tuple[int, Path]] = [(0, staging)]
    seen: set[Path] = set()
    while queue:
        depth, parent = queue.pop(0)
        if parent in seen or depth > 3:
            continue
        seen.add(parent)
        try:
            children = sorted(parent.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir():
                continue
            if _is_root(child):
                return child
            queue.append((depth + 1, child))
    return staging


def validate_staged_payload(payload_root: str | Path) -> Path:
    """Reject a staged payload that cannot replace the install.

    The root must hold the exe and _internal (the whole frozen
    program lives there, including the baked-in version number).
    A zip with a wrong layout — no exe, a doubly-nested folder, or
    the exe and _internal split across different folders — used to
    sail through here and come back as "update applied, version
    unchanged", i.e. the endless update prompt. Now it fails here,
    before anything on disk is touched.
    """
    root = Path(payload_root)
    if not (root / EXE_NAME).is_file():
        raise UpdateError("安装包里找不到新版本，这次先不换。")
    if not (root / "_internal").is_dir():
        raise UpdateError("安装包不完整，这次先不换。")
    return root


# ─── Backup + overlay (preserve user files) ─────────────────

def prune_old_backups(install_dir: str | Path, keep: int = 1) -> None:
    """Delete all but the newest `keep` update-backup-* directories."""
    backups = sorted(Path(install_dir).glob(UPDATE_BACKUP_PREFIX + "*"))
    for old in backups[:-keep] if keep > 0 else backups:
        shutil.rmtree(old, ignore_errors=True)


def create_backup(install_dir: str | Path, backup_dir: str | Path) -> Path:
    """Copy the current replaceable payload into backup_dir.

    Everything except config.yaml, data/, previous backups and the
    updater's own files is copied, so a failed update can be restored
    by hand from the backup.
    """
    install = Path(install_dir)
    backup = Path(backup_dir)
    if backup.exists():
        shutil.rmtree(backup)
    backup.mkdir(parents=True)
    for src in install.iterdir():
        if src.name in PRESERVED_TOP_LEVEL:
            continue
        if src.name.startswith(UPDATE_BACKUP_PREFIX):
            continue
        if src.name in {"update.bat", "update.log", UPDATE_PENDING_FILENAME, UPDATE_RESULT_FILENAME}:
            continue
        try:
            if src.resolve() == backup.resolve():
                continue
        except OSError:
            continue
        dst = backup / src.name
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    return backup


def prune_stale_files(source_dir: str | Path, target_dir: str | Path) -> None:
    """Delete target entries that no longer exist in the source tree.

    Only for fully-managed payload trees (currently _internal): every
    file there comes from the release package, so a file the new
    release dropped must go — otherwise a stale DLL/.pyc from the old
    release can stay loaded next to the new exe and the running
    program reports (or behaves like) the old version.
    """
    source = Path(source_dir)
    target = Path(target_dir)
    if not target.is_dir():
        return
    for entry in target.iterdir():
        counterpart = source / entry.name
        if not counterpart.exists():
            _remove_quietly(entry)
        elif entry.is_dir() and counterpart.is_dir():
            prune_stale_files(counterpart, entry)


def overlay_staging(install_dir: str | Path, staging_dir: str | Path) -> None:
    """Copy a staged payload over the install dir.

    Staging files replace their counterparts; config.yaml and data/
    are never touched. Files the new release removed are purged from
    _internal (the only fully-managed tree — top-level files that are
    not in staging are left alone, they may be the user's own).
    This is the policy the Windows batch file mirrors with robocopy
    /XD data /XF config.yaml plus a /MIR pass over _internal — keep
    the two in sync.
    """
    install = Path(install_dir)
    staging = Path(staging_dir)
    for src in staging.iterdir():
        if src.name in PRESERVED_TOP_LEVEL:
            continue
        dst = install / src.name
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
            if src.name == "_internal":
                prune_stale_files(src, dst)
        else:
            shutil.copy2(src, dst)


# ─── Windows batch file ─────────────────────────────────────

def build_update_script() -> str:
    """Return the update.bat contents.

    The batch file takes five arguments at launch: staging dir, exe
    file name, pid of the process to wait for, expected new version,
    backup directory name. It waits for the given pid to exit (up to
    60 wait rounds of ~1-2s each, then force-kills the whole process
    tree), makes sure no other copy of the exe is still running (a
    second instance would hold the exe/_internal files locked and
    robocopy would fail halfway), checks the staged payload actually
    holds the exe and _internal, then copies staging over the install
    directory (never touching config.yaml or data/), purges files the
    new release removed from _internal, verifies the new exe exists,
    and only then relaunches it. Every step is logged to update.log
    next to the exe. On any failure it restores the backup, writes
    update-result.json (ASCII reason code; the restarted program
    turns it into a plain-language message), relaunches the old exe
    when nothing is running, and deletes itself — it never leaves a
    half-swapped install behind, and never exits silently.

    The wait checks use a PowerShell Get-Process probe (PID + process
    name, no pipes): an earlier `tasklist | findstr` pipe hung three
    times in the CI update-flow test (runs 44590350853, 44612674852,
    44615545961) — diagnostics showed the bat's cmd.exe at 0.03s CPU
    with findstr.exe still alive waiting on pipe input after tasklist
    had already exited. No findstr pipe may come back. The probe
    takes the exe name from %EXE_NAME% (a launch argument, passed as
    Unicode) and strips the .exe suffix inside PowerShell, so the bat
    file itself holds no literal Chinese in the probe lines: it is
    written as UTF-8 without BOM and cmd.exe reads it in the local
    code page.

    The install dir from %~dp0 always ends with a backslash, and
    passing "%INSTALL_DIR%" bare to robocopy turns that trailing
    backslash into an escaped closing quote (CommandLineToArgvW
    treats \" as a literal quote): robocopy then swallows every
    switch into the destination path and fails instantly
    (update-flow v16→v17 run 44625451318: the wait loop was fixed,
    the copy failed in 0.22s and rolled back, with no robocopy
    output in update.log). The batch strips the trailing backslash
    right after setting INSTALL_DIR (and STAGING for safety) and
    every path join below spells the backslash out. Every robocopy
    also logs its exit code to update.log immediately (captured
    before the errorlevel check), so the next copy failure names
    its cause in the log instead of guessing.
    """
    return (
        "@echo off\r\n"
        "rem 简历猎手自动更新：等主程序退出后，把新版本换上去。\r\n"
        "rem 换的过程中不碰 config.yaml 和 data/（你的设置和数据都在那里面）。\r\n"
        "rem 换不成就从备份恢复旧版本，并把结果写进 update-result.json，\r\n"
        "rem 重开的程序看到这次失败会直接告诉你，不会马上又弹更新。\r\n"
        "rem 等待不用 timeout 命令：它在无控制台的 detached 进程里不返回，\r\n"
        "rem bat 会卡在等主程序退出的循环里走不到复制；改用 ping 延时。\r\n"
        "rem 等待不能只按 PID 匹配：Windows 会把退出进程的 PID 很快分给\r\n"
        "rem 别的进程，只认 PID 的循环会一直看到“还在”而永远不退出\r\n"
        "rem （v12→v13 全流程测试复现：PID 2684 被重用，循环 184 秒没\r\n"
        "rem 退出，连强制结束那一步都没走到）；所以 PID 和进程名一起\r\n"
        "rem 匹配，重用的 PID 名字对不上就立刻退出循环。上限 60 次\r\n"
        "rem （约一到两分钟），再久就强制结束整棵进程树。\r\n"
        "rem 等待判断不用管道查进程：全流程测试三次失败在 restart\r\n"
        "rem （v12→v13、v14→v15、诊断重跑 run 44615545961），诊断\r\n"
        "rem 抓到 bat 的 cmd.exe CPU 只有 0.03 秒、管道下游的查找\r\n"
        "rem 进程还活着：上游命令早退出了，它还在等管道输入不返回。\r\n"
        "rem 改用 PowerShell 按 PID+进程名直接查（无管道），每次约\r\n"
        "rem 1 秒，60 次上限仍在一到两分钟里。\r\n"
        "setlocal\r\n"
        'set "STAGING=%~1"\r\n'
        'if "%STAGING:~-1%"=="\\" set "STAGING=%STAGING:~0,-1%"\r\n'
        'set "EXE_NAME=%~2"\r\n'
        'set "PID=%~3"\r\n'
        'set "EXPECTED=%~4"\r\n'
        'set "BACKUP=%~5"\r\n'
        'set "INSTALL_DIR=%~dp0"\r\n'
        'rem dp0 自带末尾反斜杠：INSTALL_DIR 传给 robocopy 时，末尾反斜杠会把收尾引号转义掉，开关全被吞进路径里、robocopy 瞬间报错（v16 到 v17 全流程测试 run 44625451318：等待已修好，换文件 0.22 秒失败回滚）。先去掉末尾反斜杠，之后拼路径都显式加反斜杠。\r\n'
        'if "%INSTALL_DIR:~-1%"=="\\" set "INSTALL_DIR=%INSTALL_DIR:~0,-1%"\r\n'
        'set "LOG=%INSTALL_DIR%\\update.log"\r\n'
        'set "RESULT=%INSTALL_DIR%\\update-result.json"\r\n'
        'echo [%date% %time%] 开始更新，正在等主程序退出... > "%LOG%"\r\n'
        "set /a WAITED=0\r\n"
        ":waitloop\r\n"
        'powershell -NoProfile -NonInteractive -Command "$p = Get-Process -Id %PID% -ErrorAction SilentlyContinue; if ($p -and $p.ProcessName -eq [System.IO.Path]::GetFileNameWithoutExtension(\'%EXE_NAME%\')) { exit 0 } else { exit 1 }" <nul\r\n'
        "if %errorlevel% neq 0 goto :procsweep\r\n"
        "set /a WAITED+=1\r\n"
        "if %WAITED% geq 60 goto :killtree\r\n"
        "ping -n 2 127.0.0.1 >nul\r\n"
        "goto :waitloop\r\n"
        ":killtree\r\n"
        'echo [%date% %time%] 等了 60 次（约一分钟）主程序还没退出，正在强制结束它和它打开的子程序... >> "%LOG%"\r\n'
        'taskkill /PID %PID% /T /F >> "%LOG%" 2>&1\r\n'
        "ping -n 6 127.0.0.1 >nul\r\n"
        'powershell -NoProfile -NonInteractive -Command "$p = Get-Process -Id %PID% -ErrorAction SilentlyContinue; if ($p -and $p.ProcessName -eq [System.IO.Path]::GetFileNameWithoutExtension(\'%EXE_NAME%\')) { exit 0 } else { exit 1 }" <nul\r\n'
        "if %errorlevel% equ 0 goto :fail_stuck\r\n"
        ":procsweep\r\n"
        'powershell -NoProfile -NonInteractive -Command "if (Get-Process -Name ([System.IO.Path]::GetFileNameWithoutExtension(\'%EXE_NAME%\')) -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" <nul\r\n'
        "if %errorlevel% neq 0 goto :precopy\r\n"
        'echo [%date% %time%] 还有同名程序在跑（会占住要换的文件），正在结束... >> "%LOG%"\r\n'
        'taskkill /F /IM "%EXE_NAME%" /T >> "%LOG%" 2>&1\r\n'
        "ping -n 4 127.0.0.1 >nul\r\n"
        'powershell -NoProfile -NonInteractive -Command "if (Get-Process -Name ([System.IO.Path]::GetFileNameWithoutExtension(\'%EXE_NAME%\')) -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" <nul\r\n'
        "if %errorlevel% equ 0 goto :fail_stuck\r\n"
        ":precopy\r\n"
        'if not exist "%STAGING%\\%EXE_NAME%" goto :fail_nopkg\r\n'
        'if not exist "%STAGING%\\_internal\\" goto :fail_nopkg\r\n'
        ":copy\r\n"
        'echo [%date% %time%] 主程序已退出，正在换上新版本... >> "%LOG%"\r\n'
        'robocopy "%STAGING%" "%INSTALL_DIR%" /E /IS /R:5 /W:3 /XD data /XF config.yaml >> "%LOG%" 2>&1\r\n'
        'set "RC=%errorlevel%"\r\n'
        'echo [%date% %time%] robocopy 换主文件退出码 %RC% >> "%LOG%"\r\n'
        'if %RC% geq 8 goto :rollback\r\n'
        'robocopy "%STAGING%\\_internal" "%INSTALL_DIR%\\_internal" /MIR /R:3 /W:2 >> "%LOG%" 2>&1\r\n'
        'set "RC=%errorlevel%"\r\n'
        'echo [%date% %time%] robocopy 换 _internal 退出码 %RC% >> "%LOG%"\r\n'
        'if %RC% geq 8 goto :rollback\r\n'
        'if not exist "%INSTALL_DIR%\\%EXE_NAME%" goto :rollback\r\n'
        'echo [%date% %time%] 换好了，正在重新打开... >> "%LOG%"\r\n'
        '> "%RESULT%" echo {"status": "applied", "expected_version": "%EXPECTED%", "reason_code": ""}\r\n'
        'rmdir /s /q "%STAGING%"\r\n'
        'start "" "%INSTALL_DIR%\\%EXE_NAME%"\r\n'
        '(goto) 2>nul & del "%~f0"\r\n'
        ":rollback\r\n"
        'echo [%date% %time%] 复制失败，正在用备份恢复旧版本... >> "%LOG%"\r\n'
        'if "%BACKUP%"=="" goto :fail_copy\r\n'
        'robocopy "%INSTALL_DIR%\\%BACKUP%" "%INSTALL_DIR%" /E /IS /R:3 /W:2 /XD data /XF config.yaml >> "%LOG%" 2>&1\r\n'
        'set "RC=%errorlevel%"\r\n'
        'echo [%date% %time%] robocopy 恢复主文件退出码 %RC% >> "%LOG%"\r\n'
        'if not exist "%INSTALL_DIR%\\%BACKUP%\\_internal\\" goto :rollback_after_internal\r\n'
        'robocopy "%INSTALL_DIR%\\%BACKUP%\\_internal" "%INSTALL_DIR%\\_internal" /MIR /R:3 /W:2 >> "%LOG%" 2>&1\r\n'
        'set "RC=%errorlevel%"\r\n'
        'echo [%date% %time%] robocopy 恢复 _internal 退出码 %RC% >> "%LOG%"\r\n'
        ":rollback_after_internal\r\n"
        'echo [%date% %time%] 已恢复旧版本。 >> "%LOG%"\r\n'
        "goto :fail_copy\r\n"
        ":fail_stuck\r\n"
        'set "FAIL_CODE=stuck"\r\n'
        "goto :fail_write\r\n"
        ":fail_nopkg\r\n"
        'set "FAIL_CODE=nopkg"\r\n'
        "goto :fail_write\r\n"
        ":fail_copy\r\n"
        'set "FAIL_CODE=copy"\r\n'
        "goto :fail_write\r\n"
        ":fail_write\r\n"
        'echo [%date% %time%] 更新没换成，旧版本还能用。 >> "%LOG%"\r\n'
        '> "%RESULT%" echo {"status": "failed", "expected_version": "%EXPECTED%", "reason_code": "%FAIL_CODE%"}\r\n'
        'powershell -NoProfile -NonInteractive -Command "if (Get-Process -Name ([System.IO.Path]::GetFileNameWithoutExtension(\'%EXE_NAME%\')) -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" <nul\r\n'
        "if %errorlevel% equ 0 goto :fail_exit\r\n"
        'if exist "%INSTALL_DIR%\\%EXE_NAME%" start "" "%INSTALL_DIR%\\%EXE_NAME%"\r\n'
        ":fail_exit\r\n"
        '(goto) 2>nul & del "%~f0"\r\n'
    )


def write_update_script(install_dir: str | Path) -> Path:
    """Write update.bat into the install directory. Returns its path."""
    bat_path = Path(install_dir) / "update.bat"
    bat_path.write_text(build_update_script(), encoding="utf-8")
    return bat_path


# ─── Pending / result markers (version check after restart) ─

def write_pending_update(install_dir: str | Path, expected_version: str) -> Path:
    """Record that an update to `expected_version` was just applied.

    The restarted program consumes this file on boot and compares its
    own version with the expected one: if they disagree, the swap did
    not actually take (stale files, wrong layout, partial copy) and
    the program must report the failure instead of prompting for the
    same update again.
    """
    pending = Path(install_dir) / UPDATE_PENDING_FILENAME
    pending.write_text(
        json.dumps(
            {"expected_version": expected_version, "started_at": time.time()},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return pending


def read_update_result(install_dir: str | Path) -> dict | None:
    """Read update-result.json written by update.bat (or None)."""
    path = Path(install_dir) / UPDATE_RESULT_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def write_update_result(install_dir: str | Path, verdict: dict) -> Path:
    """Persist the final verdict of an update attempt (for later boots)."""
    path = Path(install_dir) / UPDATE_RESULT_FILENAME
    path.write_text(json.dumps(verdict, ensure_ascii=False), encoding="utf-8")
    return path


def clear_update_result(install_dir: str | Path) -> None:
    """Drop a stale failure record (manual re-check is the escape hatch)."""
    _remove_quietly(Path(install_dir) / UPDATE_RESULT_FILENAME)


def get_last_update_failure(install_dir: str | Path) -> dict | None:
    """Return the last failed-update verdict, if it is still relevant.

    Relevant means the update failed and the running version is still
    not the expected one — i.e. the user has not fixed it since. A
    manual re-check clears the record (see clear_update_result), and
    a later successful update overwrites it.
    """
    result = read_update_result(install_dir)
    if not isinstance(result, dict) or result.get("status") != "failed":
        return None
    expected = result.get("expected_version")
    if not isinstance(expected, str) or not expected:
        return None
    if expected == __version__:
        return None
    reason_code = result.get("reason_code")
    reason = result.get("reason")
    if not isinstance(reason, str) or not reason:
        reason = UPDATE_FAILURE_MESSAGES.get(reason_code, UPDATE_FAILURE_MESSAGES["unknown"]) \
            if isinstance(reason_code, str) else UPDATE_FAILURE_MESSAGES["unknown"]
    return {
        "status": "failed",
        "expected_version": expected,
        "actual_version": __version__,
        "reason": reason,
    }


def consume_pending_update(install_dir: str | Path) -> dict | None:
    """Consume update-pending.json left by apply_update.

    Returns the verdict dict (status "ok" or "failed" plus the plain
    reason) or None when no update was pending. The verdict is also
    written to update-result.json so later boots still know.
    """
    pending_path = Path(install_dir) / UPDATE_PENDING_FILENAME
    if not pending_path.is_file():
        return None
    try:
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
    except Exception:
        pending = {}
    try:
        pending_path.unlink()
    except OSError:
        pass
    expected = pending.get("expected_version") if isinstance(pending, dict) else None
    bat_result = read_update_result(install_dir)
    bat_status = bat_result.get("status") if isinstance(bat_result, dict) else None
    bat_code = bat_result.get("reason_code") if isinstance(bat_result, dict) else None
    if isinstance(expected, str) and expected and __version__ == expected and bat_status == "applied":
        verdict: dict = {
            "status": "ok",
            "expected_version": expected,
            "actual_version": __version__,
            "reason": "",
        }
    elif isinstance(expected, str) and expected:
        if bat_status == "failed":
            code = bat_code if isinstance(bat_code, str) else "unknown"
            reason = UPDATE_FAILURE_MESSAGES.get(code, UPDATE_FAILURE_MESSAGES["unknown"])
        elif bat_status == "applied":
            reason = UPDATE_FAILURE_MESSAGES["mismatch"]
        else:
            reason = UPDATE_FAILURE_MESSAGES["unknown"]
        verdict = {
            "status": "failed",
            "expected_version": expected,
            "actual_version": __version__,
            "reason": reason,
        }
    else:
        verdict = {
            "status": "failed",
            "expected_version": expected if isinstance(expected, str) else "",
            "actual_version": __version__,
            "reason": UPDATE_FAILURE_MESSAGES["unknown"],
        }
    write_update_result(install_dir, verdict)
    return verdict


def reconcile_pending_update(install_dir: str | Path) -> bool:
    """Handle a pending/failed update at startup.

    Consumes any pending marker and, when the previous update failed
    (or an earlier failure is still unfixed), puts the update state
    to "error" with the plain-language reason. Returns True when the
    caller should skip the automatic version check — the user should
    see the failure, not another prompt for the same update.
    """
    pending = consume_pending_update(install_dir)
    if pending is not None:
        if pending["status"] == "ok":
            return False
        _set_status(
            status="error",
            update_available=False,
            info=None,
            message="",
            error=pending["reason"],
        )
        return True
    last = get_last_update_failure(install_dir)
    if last is not None:
        _set_status(
            status="error",
            update_available=False,
            info=None,
            message="",
            error=last["reason"],
        )
        return True
    return False


# ─── Install location ───────────────────────────────────────

def get_install_dir() -> Path | None:
    """Install directory of the frozen exe, else None.

    Dev runs (not frozen) have no install directory to update, so
    one-click update refuses there instead of touching the repo.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


def get_exe_name() -> str:
    """Exe file name to relaunch after the update."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).name
    return EXE_NAME


def request_restart(delay: float = 1.5) -> None:
    """Exit this process after `delay` seconds (lets the HTTP reply flush)."""
    def _exit() -> None:
        time.sleep(max(0.0, delay))
        os._exit(0)

    threading.Thread(target=_exit, daemon=True).start()


# ─── Shared update state (server routes) ────────────────────

_STATE_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "status": "idle",  # idle|checking|downloading|verifying|ready|applying|error
    "update_available": False,
    "progress": 0,
    "message": "",
    "error": "",
    "info": None,
    "checked_at": None,
    "staging_dir": None,
    "zip_path": None,
}


def get_status() -> dict[str, Any]:
    """Return a JSON-safe copy of the current update state."""
    with _STATE_LOCK:
        state = dict(_STATE)
    info = state.get("info")
    state["info"] = dict(info) if isinstance(info, dict) else None
    return state


def _set_status(**kwargs: Any) -> None:
    with _STATE_LOCK:
        _STATE.update(kwargs)


def _progress(downloaded: int, total: int) -> None:
    if total > 0:
        _set_status(progress=min(99, int(downloaded * 100 / total)))
    else:
        _set_status(progress=-1)


def run_check(config: dict, timeout: float = CHECK_TIMEOUT, force: bool = False) -> dict[str, Any]:
    """Synchronous version check with a freshness cache. Never raises.

    A check done less than CHECK_CACHE_SECONDS ago is reused unless
    `force` is set. Check failures are silent: they leave the state
    as "no update known" instead of erroring.
    """
    now = time.monotonic()
    with _STATE_LOCK:
        checked_at = _STATE.get("checked_at")
        fresh = checked_at is not None and now - checked_at < CHECK_CACHE_SECONDS
        cached = dict(_STATE)
    if fresh and not force:
        return cached
    _set_status(status="checking", error="")
    try:
        info = check_for_update(config, timeout=timeout)
    except Exception:
        info = None
    _set_status(
        status="idle",
        update_available=bool(info),
        info=info,
        checked_at=time.monotonic(),
        message="有新版本了" if info else "",
    )
    return get_status()


def start_download(config: dict) -> dict[str, Any]:
    """Start the background download of the checked release.

    Raises UpdateError when there is nothing to download or a download
    is already running. Returns the current state immediately.
    """
    with _STATE_LOCK:
        info = _STATE.get("info")
        status = _STATE.get("status")
    if not info:
        raise UpdateError("还没有发现新版本，先点一下检查更新。")
    if status in {"downloading", "verifying"}:
        return get_status()
    if status == "ready":
        return get_status()
    _set_status(status="downloading", progress=0, message="正在下载新版本…", error="")
    thread = threading.Thread(target=_download_worker, args=(config, dict(info)), daemon=True)
    thread.start()
    return get_status()


def _download_worker(config: dict, info: dict) -> None:
    tmpdir = tempfile.mkdtemp(prefix="jianli-update-")
    try:
        zip_path = download_update(config, info, tmpdir, progress_cb=_progress)
        _set_status(status="verifying", progress=100, message="正在检查下载好的文件…")
        staging = extract_update(zip_path, Path(tmpdir) / "staging")
        payload_root = validate_staged_payload(find_payload_root(staging))
        _set_status(
            status="ready",
            progress=100,
            message="新版本下好了",
            zip_path=str(zip_path),
            staging_dir=str(payload_root),
        )
    except UpdateError as exc:
        _remove_quietly(Path(tmpdir))
        _set_status(status="error", progress=0, message="", error=exc.user_message, zip_path=None, staging_dir=None)
    except Exception:
        _remove_quietly(Path(tmpdir))
        _set_status(status="error", progress=0, message="", error="下载出问题了，稍后再试。", zip_path=None, staging_dir=None)


# Windows 创建标志的真实值（CREATE_NO_WINDOW 0x08000000、
# DETACHED_PROCESS 0x00000008、CREATE_NEW_PROCESS_GROUP 0x00000200）。
# getattr 的 fallback 写死真实值：这样 helper 在 Windows 上是对的，
# 在 Linux/macOS 上也能跑测试（那里的 subprocess 没有这些属性）。
_NO_WINDOW_FLAG = 0x08000000
_DETACHED_PROCESS_FLAG = 0x00000008
_NEW_PROCESS_GROUP_FLAG = 0x00000200


def _windows_launch_flags() -> int:
    """update.bat 的启动标志：不带控制台窗口。

    只用 DETACHED_PROCESS 不够：detached 的 cmd 自己没有控制台，它再
    拉起的 ping / PowerShell / robocopy / taskkill 各自会新开一个黑框
    （v1.0.8 用户实拍：findstr /C:"2108" 黑框弹在面板前面——旧 bat
    的 tasklist | findstr 里 findstr 卡在等管道输入，自己的黑框一直
    挂在前面）。CREATE_NO_WINDOW 让 cmd 和它拉起的控制台程序都不开
    窗口；bat 的输出本来就写 update.log，不靠控制台看。
    """
    return (
        getattr(subprocess, "CREATE_NO_WINDOW", _NO_WINDOW_FLAG)
        | getattr(subprocess, "DETACHED_PROCESS", _DETACHED_PROCESS_FLAG)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", _NEW_PROCESS_GROUP_FLAG)
    )


def _is_windows() -> bool:
    """是不是 Windows。单独成函数是为了测试能扮成 Windows 走一遍
    apply：直接 patch os.name 会连 pathlib.Path 一起改成
    WindowsPath，在 Linux 上根本建不出来。"""
    return os.name == "nt"


# 旧 bat 残留的进程超过这个时间才算“挂起的”：刚拉起的更新 bat 只有几秒，
# 绝不能碰。
_STALE_UPDATE_MAX_AGE_MINUTES = 10


def cleanup_stale_update_processes(install_dir: str | Path | None = None) -> bool:
    """杀掉旧版挂起的更新进程，顺手清掉它留下的 pending 标记。

    v1.0.8 及更早的 bat 用 tasklist | findstr 等主进程退出，findstr 会
    卡在等管道输入上不返回，自己的黑框一直挂在面板前面（用户实拍：
    findstr /C:"简历猎手.exe"）。光杀 findstr 不行：管子一断，旧 bat
    的等待循环就以为程序退了，直接去 robocopy，文件被占住就回滚——
    回滚会把备份里的旧版盖回刚手动重下的新版上。所以先杀跑 update.bat
    的 cmd（让它永远没机会往下走），再杀 findstr；两个都只认命令行里
    带简历猎手.exe 的、且已活超过 10 分钟的——刚拉起的新 bat 只有几秒，
    绝不能碰。杀掉残留后，那次挂起更新的 update-pending.json 也一并
    清掉，不然新版本启动时 reconcile 会把它当成一次失败的更新报错。

    只在 Windows 上做；任何一步出错都悄悄返回 False，绝不挡启动。
    只有真的杀掉进程才返回 True。
    """
    if not _is_windows():
        return False
    script = (
        "$doomed = @(Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | "
        "Where-Object { $_.CommandLine -like '*update.bat*' -and "
        f"$_.CommandLine -like '*{EXE_NAME}*' -and "
        "((Get-Date) - $_.CreationDate).TotalMinutes -gt "
        f"{_STALE_UPDATE_MAX_AGE_MINUTES} }}); "
        "$doomed += @(Get-CimInstance Win32_Process -Filter \"Name='findstr.exe'\" | "
        "Where-Object { "
        f"$_.CommandLine -like '*{EXE_NAME}*' -and "
        "((Get-Date) - $_.CreationDate).TotalMinutes -gt "
        f"{_STALE_UPDATE_MAX_AGE_MINUTES} }}); "
        "$doomed | ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
        "-ErrorAction SilentlyContinue }; "
        "Write-Output $doomed.Count"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=15,
            # 到这里一定是 Windows（上面已判过），CREATE_NO_WINDOW 不会炸。
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
    except Exception:
        return False
    try:
        killed = int((result.stdout or "").strip())
    except ValueError:
        return False
    if killed <= 0:
        return False
    # 那次挂起的更新已经死了：它的 pending 标记不能再留着，否则新版本
    # 启动时 reconcile 会把它当成一次失败的更新报错。
    target = Path(install_dir) if install_dir is not None else get_install_dir()
    if target is not None:
        _remove_quietly(target / UPDATE_PENDING_FILENAME)
    return True


# 桌面快捷方式的 marker：建过一次就留着，用户手动删了快捷方式也不再
# 补建；面板更新不碰 data/，所以更新后 marker 还在，不会重复建。
DESKTOP_SHORTCUT_MARKER_FILENAME = "desktop_shortcut_created"
_DESKTOP_SHORTCUT_NAME = "简历猎手.lnk"


def _is_frozen() -> bool:
    """是不是打包后的 exe。单独成函数是为了测试能扮 frozen：
    直接改 sys.frozen 会影响别的模块，_is_windows() 也是这个道理。"""
    return bool(getattr(sys, "frozen", False))


def _ps_quote(value: str) -> str:
    """包进 PowerShell 单引号字符串：单引号本身翻倍，路径里的反斜杠
    原样留着（单引号里反斜杠不是转义符，不用转义）。"""
    return "'" + value.replace("'", "''") + "'"


def ensure_desktop_shortcut(
    install_dir: str | Path | None = None,
    exe_path: str | Path | None = None,
) -> bool:
    """桌面上确保有一个「简历猎手.lnk」，指向正在跑的 exe。

    覆盖两种场景：面板更新后 bat 重拉 exe，启动时建；新下载解压后
    用户第一次双击 exe，启动时建。bat 里不另建，exe 启动这一处就够。

    幂等 + 尊重用户删除：data/ 下留 marker，建过一次就不再建——
    用户手动删了快捷方式就不再补建；新解压的包没有 marker，第一次
    启动就建；面板更新不删 marker 也不删快捷方式。

    只在 Windows + frozen 的 exe 里做；任何一步出错都悄悄返回
    False，绝不挡启动。建成了才返回 True。
    """
    if not _is_windows() or not _is_frozen():
        return False
    target = Path(install_dir) if install_dir is not None else get_install_dir()
    if target is None:
        return False
    exe = Path(exe_path) if exe_path is not None else Path(sys.executable).resolve()
    marker = target / "data" / DESKTOP_SHORTCUT_MARKER_FILENAME
    if marker.is_file():
        return False
    script = (
        "$desktop = [Environment]::GetFolderPath('Desktop'); "
        "$shell = New-Object -ComObject WScript.Shell; "
        "$lnkPath = Join-Path $desktop " + _ps_quote(_DESKTOP_SHORTCUT_NAME) + "; "
        "$lnk = $shell.CreateShortcut($lnkPath); "
        f"$lnk.TargetPath = {_ps_quote(str(exe))}; "
        f"$lnk.WorkingDirectory = {_ps_quote(str(exe.parent))}; "
        f"$lnk.IconLocation = {_ps_quote(str(exe))}; "
        "$lnk.Save()"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=15,
            # 到这里一定是 Windows（上面已判过），CREATE_NO_WINDOW 不会炸。
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
    except Exception:
        return False
    if result.returncode != 0:
        return False
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{__version__}\n", encoding="utf-8")
    except Exception:
        # 快捷方式已经建好了，marker 没写上只是下次启动会再建一次
        #（CreateShortcut 会覆盖同名文件），不算失败。
        pass
    return True


def apply_update(install_dir: str | Path, exe_name: str) -> Path:
    """Prepare the swap: backup, write update.bat, launch it detached.

    Only valid when a release is staged (status "ready") and only on
    Windows. The staged payload is validated first (a bad zip layout
    must fail here, not after the program has exited). The caller is
    responsible for exiting this process right after (see
    request_restart). Returns the batch file path.
    """
    with _STATE_LOCK:
        status = _STATE.get("status")
        staging_dir = _STATE.get("staging_dir")
        info = _STATE.get("info")
    if status != "ready" or not staging_dir:
        raise UpdateError("新版本还没下好，先等它下载完。")
    # Validate the staged payload before touching the install: a zip
    # whose layout is wrong must never get as far as update.bat.
    validate_staged_payload(staging_dir)
    if not _is_windows():
        raise UpdateError("一键更新只在正式安装的版本里能用。")
    install = Path(install_dir)
    if not (install / exe_name).is_file():
        raise UpdateError("找不到现在用的程序文件，这次先不换。")
    expected_version = info.get("version") if isinstance(info, dict) else None
    if not isinstance(expected_version, str) or not expected_version:
        raise UpdateError("新版本还没下好，先等它下载完。")
    prune_old_backups(install)
    backup_name = f"{UPDATE_BACKUP_PREFIX}{__version__}"
    backup_dir = install / backup_name
    create_backup(install, backup_dir)
    bat_path = write_update_script(install)
    # 旧的结果文件先清掉：新一轮 bat 万一没跑起来就写不出结果，
    # 上轮的 "applied" 不能被误读成这轮换成了。
    _remove_quietly(install / UPDATE_RESULT_FILENAME)
    write_pending_update(install, expected_version)
    # 先关主界面 --app 窗口：只关 exe 启动时持有的那个句柄对应的进程，
    # 绝不碰登录浏览器（登录浏览器是另一个独立进程、另一个 profile）。
    # 窗口不关，换文件时可能被占住、端口也交接不干净；关窗口不删
    # profile，登录会话下次还能恢复。关不掉也不挡更新，bat 的等待
    # 循环是后备。
    try:
        from bosshunter.app_window import close_app_window

        close_app_window()
    except Exception:
        pass
    flags = _windows_launch_flags()
    try:
        subprocess.Popen(
            ["cmd.exe", "/c", str(bat_path), str(staging_dir), exe_name, str(os.getpid()),
             expected_version, backup_name],
            creationflags=flags,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        _remove_quietly(install / UPDATE_PENDING_FILENAME)
        raise
    _set_status(status="applying", progress=100, message="正在换上新版本…")
    return bat_path
