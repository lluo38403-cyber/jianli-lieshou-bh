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
removed from _internal, verifies the new exe exists, relaunches it,
polls its /api/health until the expected version answers (up to ~180s
on the configured port; no answer means the batch retries the launch
once and polls up to 60s more, then rolls back to the backup and
relaunches the old exe — failed/nostart_rolled_back — while staging
is kept for a retry), and cleans up. On any failure the batch file
restores the backup, verifies the restore (a failed restore is
failed/rollback_failed, never a false "restored"), writes
update-result.json and relaunches the old exe; the restarted
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
- A failed version check is logged with a reason code (timeout /
  unauthorized / no_release / bad_sha / network / ...) and reported
  to the panel as a check error (reason_code "check_failed") instead
  of being mistaken for "already latest": run_check keeps the
  previously known release info, does not cache the failure, and
  the panel's manual check can show "没查到，点再试一次".
- The file swap itself is a Windows batch file and can only be proven
  on a real Windows machine: tests cover everything up to the batch
  file's contents, not its execution.
"""

from __future__ import annotations

import hashlib
import json
import logging
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

_logger = logging.getLogger(__name__)

CHECK_TIMEOUT = 10.0
DOWNLOAD_TIMEOUT = 300.0
CHECK_CACHE_SECONDS = 600
UPDATE_BACKUP_PREFIX = "update-backup-"
UPDATE_PENDING_FILENAME = "update-pending.json"
UPDATE_RESULT_FILENAME = "update-result.json"
EXE_NAME = "简历猎手.exe"

# bat 重开 exe 时不带参数：启动器默认跑 `web`、端口 8686（launcher.py 的
# `args = sys.argv[1:] or ["web"]`，main.py 里 --port 默认 8686），所以 bat
# 里所有 /api/health 轮询问的都是这个重开端口——绝不是更新前的服务端口。
# v31 传当前端口是错的（M-41 的机制错了，意图没错）：CI 里服务跑 18686、
# 重开的跑 8686，bat 在 18686 上等 180 秒等不到旧版，:fail_poll 误报
# nostart_old；非默认端口的用户同样会被误判。bat 第 7 个参数就是这个端口。
RELAUNCH_PORT = 8686

# bat 还活着的最长合法耗时：更新 bat 的轮询到顶约 8 分钟（180 秒新版轮询
# + 60 秒重试 + 180 秒旧版轮询 + 等待/拷贝），这里留到 15 分钟；超过即
# 视为 bat 已死、文件是残留，消费 pending 时不再让位——不永久卡住。
_BAT_ALIVE_MAX_SECONDS = 900

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
    "nostart": "新版本换好了，但程序没重新打开。请重新点一次更新；如果一直这样，请把 update.log 发给客服。",
    "nostart_rolled_back": "新版本没能启动，已经换回旧版本。请重新点一次更新；如果一直这样，请把 update.log 发给客服。",
    "rollback_failed": "更新失败，回滚旧版本也没成功，安装可能新旧混装。请手动重装完整包；如果一直这样，请把 update.log 发给客服。",
    "nostart_old": "更新没换成，旧版本也没重新打开。请手动打开程序再重新点一次更新；如果一直这样，请把 update.log 发给客服。",
    "unknown": "上次更新没换完，请重新点一次更新。",
}

# Top-level entries of an install directory that an update must never touch.
PRESERVED_TOP_LEVEL = ("config.yaml", "data")

# v25：更新换版进行中标志。apply_update 置位后，--app 关窗监视线程
# （app_window.watch_app_window_close）跳过关停——apply 自己会关窗、
# 停 runtime、起 bat，监视线程绝不能在换文件途中抢跑关停。置位点在
# 校验通过之后、关窗之前；bat 拉起失败时清掉（那次更新没换成，进程
# 继续跑）；bat 拉起成功后不用清，进程随 request_restart 退出。
_UPDATE_APPLYING = False


def is_update_applying() -> bool:
    """True while apply_update is swapping in a new version."""
    return _UPDATE_APPLYING


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

# 检查失败的原因码（v29）：以前任何异常都 return None，前端分不清
# “已是最新”和“没查到”，run_check 还把 info 清掉、checked_at 写成
# 现在——中转一慢，手动查到的新版就被随后返回的自动检查盖没了。
# 现在每次失败都按原因分类，记一条日志，run_check 把失败原样报给
# 前端，手动检查才能显示“没查到，点再试一次”。
CHECK_FAILURE_MESSAGES = {
    "timeout": "查新版本超时了，网络可能比较慢，稍后再点一次。",
    "unauthorized": "中转那边不认这个账号，更新查不到，先用现在的版本。",
    "no_release": "服务器上还没有发布新版本。",
    "relay_error": "服务器那边出了点问题，稍后再点一次。",
    "bad_payload": "服务器返回的新版本信息对不上，稍后再点一次。",
    "bad_sha": "服务器上的新版本信息对不上（校验值不对），稍后再点一次。",
    "network": "没连上服务器，检查一下网络后，再点一次。",
    "unknown": "查新版本时出了点问题，稍后再点一次。",
}


def _log_check_failure(reason_code: str, detail: str = "") -> None:
    """记一条检查失败日志：logger 一行 + 安装目录日志文件一行。

    frozen 的无窗口 exe 里 logger 没有 handler、写了也看不见（见
    server.py 的 _startup_log），所以同时追加写安装目录下的
    data/logs/jianli-lieshou.log——和启动日志同一个文件，买家侧定位
    就看这个文件。开发环境没有安装目录，只有 logger 那一行。
    写失败不挡检查。
    """
    line = f"update check failed: reason={reason_code}" + (f" {detail}" if detail else "")
    try:
        _logger.warning("%s", line)
    except Exception:
        pass
    try:
        install = get_install_dir()
        if install is None:
            return
        log_dir = install / "data" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        with open(log_dir / "jianli-lieshou.log", "a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] 更新检查失败：原因={reason_code}" + (f" {detail}" if detail else "") + "\n")
    except OSError:
        pass


def _check_for_update_detailed(config: dict, timeout: float = CHECK_TIMEOUT) -> tuple[dict | None, dict | None]:
    """查新版并区分失败原因。

    返回 (info, failure)：info 非空 = 有新版；两个都空 = 查成功了、
    已经是最新（或没配好 token，根本没查）；failure 非空 = 没查到，
    {"reason_code": ..., "message": ...} 是白话原因。失败都记一条
    日志（原因码 + 底层错误，不含 token）。
    """
    url = get_relay_url(config).rstrip("/")
    token = get_relay_token(config)
    if not token:
        return None, {"reason_code": "no_token", "message": ""}
    try:
        resp = httpx.get(
            f"{url}/api/version",
            headers={"X-App-Token": token},
            timeout=_coerce_timeout(timeout, CHECK_TIMEOUT),
            trust_env=False,
        )
    except httpx.TimeoutException as exc:
        # TimeoutException 是 RequestError 的子类，必须先判。
        _log_check_failure("timeout", str(exc))
        return None, {"reason_code": "timeout", "message": CHECK_FAILURE_MESSAGES["timeout"]}
    except httpx.RequestError as exc:
        _log_check_failure("network", str(exc))
        return None, {"reason_code": "network", "message": CHECK_FAILURE_MESSAGES["network"]}
    except Exception as exc:
        _log_check_failure("unknown", str(exc))
        return None, {"reason_code": "unknown", "message": CHECK_FAILURE_MESSAGES["unknown"]}
    if resp.status_code == 404:
        _log_check_failure("no_release", f"HTTP 404 {url}/api/version")
        return None, {"reason_code": "no_release", "message": CHECK_FAILURE_MESSAGES["no_release"]}
    try:
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        code = "unauthorized" if status in (401, 403) else "relay_error"
        _log_check_failure(code, f"HTTP {status} {url}/api/version")
        return None, {"reason_code": code, "message": CHECK_FAILURE_MESSAGES[code]}
    except Exception as exc:
        _log_check_failure("bad_payload", f"bad response: {exc}")
        return None, {"reason_code": "bad_payload", "message": CHECK_FAILURE_MESSAGES["bad_payload"]}
    if not isinstance(payload, dict):
        _log_check_failure("bad_payload", f"payload is {type(payload).__name__}, not a dict")
        return None, {"reason_code": "bad_payload", "message": CHECK_FAILURE_MESSAGES["bad_payload"]}
    version = payload.get("version")
    sha256 = payload.get("sha256")
    if not isinstance(version, str) or not is_newer(__version__, version):
        return None, None
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        _log_check_failure("bad_sha", f"version={version} sha256={sha256!r}")
        return None, {"reason_code": "bad_sha", "message": CHECK_FAILURE_MESSAGES["bad_sha"]}
    size = payload.get("size")
    notes = payload.get("notes")
    return {
        "version": version,
        "current_version": __version__,
        "size": size if isinstance(size, int) and size > 0 else 0,
        "sha256": sha256.lower(),
        "notes": notes if isinstance(notes, str) else "",
        "update_available": True,
    }, None


def check_for_update(config: dict, timeout: float = CHECK_TIMEOUT) -> dict | None:
    """Ask the relay for the newest published version.

    Returns the release info (version/size/sha256/notes) when a newer
    version exists, else None. Never raises: relay down, timeout,
    unexpected status or a bad payload all mean "no update" to the
    caller — but the reason is now logged and available to run_check
    (see _check_for_update_detailed), so a failed check is no longer
    mistaken for "already latest".
    """
    try:
        info, _failure = _check_for_update_detailed(config, timeout=timeout)
    except Exception:
        return None
    return info


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


def _backup_looks_complete(backup_dir: str | Path) -> bool:
    """True when an existing backup already holds a restorable payload.

    A backup counts as complete when it has the _internal tree and at
    least one top-level exe (the frozen program). A partial backup
    (interrupted copy, empty dir) is not complete and may be replaced.
    """
    backup = Path(backup_dir)
    if not (backup / "_internal").is_dir():
        return False
    try:
        return any(
            entry.is_file() and entry.suffix.lower() == ".exe"
            for entry in backup.iterdir()
        )
    except OSError:
        return False


def create_backup(install_dir: str | Path, backup_dir: str | Path) -> Path:
    """Copy the current replaceable payload into backup_dir.

    Everything except config.yaml, data/, previous backups and the
    updater's own files is copied, so a failed update can be restored
    by hand from the backup.

    H-1: an existing complete backup is never overwritten. A failed
    rollback can leave a mixed install behind; the next retry must
    not copy that mixed install over the one good backup (the backup
    directory name is per-version, so the retry would otherwise
    destroy the only restorable copy).
    """
    install = Path(install_dir)
    backup = Path(backup_dir)
    if backup.exists():
        if _backup_looks_complete(backup):
            return backup
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

    The batch file takes seven arguments at launch: staging dir, exe
    file name, pid of the process to wait for, expected new version,
    backup directory name, current (old) version, and the port the
    relaunched instance listens on (seventh argument, always 8686 —
    the batch file starts the exe with no arguments and the launcher
    defaults to `web` on 8686, so the relaunched instance is always
    there; the pre-update service port must never be passed here, see
    apply_update — v31 did, and the batch waited the full 180 s on the
    wrong port and reported a false nostart_old instead of stuck).
    It waits for the given pid to exit (up to
    60 wait rounds of ~1-2s each, then force-kills the whole process
    tree), makes sure no other copy of the exe is still running (a
    second instance would hold the exe/_internal files locked and
    robocopy would fail halfway), checks the staged payload actually
    holds the exe and _internal, then copies staging over the install
    directory (never touching config.yaml or data/), purges files the
    new release removed from _internal, verifies the new exe exists,
    and only then relaunches it. Every step is logged to update.log
    next to the exe. On any failure it restores the backup, relaunches
    the old exe when nothing is running, polls /api/health until the
    old version reports in (up to ~180s, same loop as the new-version
    check below; if the old exe never reports in, the verdict is
    failed/nostart_old instead of the original reason code), and only
    then writes update-result.json (ASCII reason code; the restarted
    program turns it into a plain-language message) and deletes
    itself — it never leaves a half-swapped install behind, and never
    exits silently.

    Relaunching is not the end of it: after the copy the batch starts
    the new exe and polls http://127.0.0.1:%PORT%/api/health until it
    reports the expected version (up to ~180s, hidden PowerShell loop).
    Only then is the update recorded as applied and staging deleted.
    If the new exe never reports in, the batch first checks whether
    anything is answering on %PORT% at all: if something is, the
    verdict is failed/nostart and the install is left as-is (the late
    instance may still come up, and consume_pending_update re-probes
    before judging failure). If nothing answers, it starts the new
    exe once more and polls up to 60s more; if that still fails, it
    rolls back to the backup and relaunches the old exe (S-1: a bad
    new build must never permanently replace a working old one), and
    the verdict is failed/nostart_rolled_back once the old version
    reports in. Staging is kept on every failure path (a retry must
    not have to re-download), and the verdict is always written
    before self-delete. (run 44758466045: both robocopies exited 3,
    the log said "换好了", update-result.json said applied, but no
    exe was running and 8686 never reported v1.1.14 — the bare
    `start` swallowed the failed relaunch silently.)

    The rollback relaunch gets the same treatment: :fail_write starts
    the old exe (when nothing is running) and polls
    http://127.0.0.1:%PORT%/api/health until it reports the old version
    %CURRENT% — the sixth launch argument, the version the update
    started from (up to ~180s, same hidden PowerShell loop). Only then
    is the failure verdict written, keeping the original reason code
    (stuck/nopkg/copy/nostart_rolled_back) so the Retry expectation
    "update failed, old version is back" still holds. If the old exe
    never reports in, the batch records failed/nostart_old, starts the
    old exe once more as a last chance, and only then deletes itself.
    Staging is never deleted on any failure path (a retry must not
    re-download). Rollback itself is verified too (H-1): each restore
    robocopy is checked for RC>=8, and after the restore the old exe
    and _internal must exist — otherwise the verdict is
    failed/rollback_failed (never the false "已恢复旧版本"), and the
    reason code survives even if the old exe then fails to report in,
    so the user is told to reinstall by hand instead of retrying the
    same broken path.

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

    No bare console program in this file: CREATE_NO_WINDOW on the
    directly created cmd.exe does NOT hide the grandchildren it
    launches (robocopy / ping / taskkill would each allocate their own
    visible console — the black window users saw). Every console call
    therefore runs inside `powershell -WindowStyle Hidden -Command`,
    whose hidden console the child inherits; delays use Start-Sleep
    inside the same hidden PowerShell instead of ping, and robocopy's
    exit code is carried out with `exit $LASTEXITCODE` so the
    `set "RC=%errorlevel%"` lines still see the real robocopy code.
    `start /min` is not a substitute (still visible on the taskbar).

    Before any copy, :procsweep kills processes by ExecutablePath, not
    by image name: the browser runtime's node.exe
    (`_internal\\patchright\\driver\\node.exe`) becomes an orphan when
    the exe exits without killing its children, and the old same-name
    sweep could not reach it, so robocopy failed on _internal. Only
    processes whose executable lives inside the install directory are
    killed (they can only be our own packaged files); `taskkill
    node.exe` / `chrome.exe` by image name would kill the user's
    browser and unrelated node processes and must never come back.
    After the kill the script waits 2s and proves node.exe can be
    opened for read/write; if it is still locked it logs the fact and
    goes to :fail_stuck instead of failing halfway through the copy.

    Single-window handoff: the old panel closes itself first (the
    frontend closes its own window once apply succeeds, and
    apply_update also tries the held --app handle), the swap runs with
    no visible console at all, and the batch relaunches the new exe,
    which opens one fresh panel. At no point are two panels (or a
    panel plus a black console) on screen together. The batch never
    trusts that the panel is gone: :waitloop waits for the old exe
    PID to die, and :precopy re-probes that PID immediately before
    any robocopy — if it is still alive the update fails safe
    (:fail_stuck) instead of copying over a live process.
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
        "rem 黑窗口：CREATE_NO_WINDOW 只让直接创建的 cmd 不开窗口，cmd 拉起的\r\n"
        "rem robocopy / ping / taskkill 是控制台程序，没有可继承的控制台时会\r\n"
        "rem 自己分配可见窗口——这就是 v1.1.9 用户看到的黑窗口。所以 bat 里\r\n"
        "rem 所有控制台调用都经 powershell -WindowStyle Hidden 走：隐藏的\r\n"
        "rem PowerShell 自带隐藏控制台，子进程继承它、不再自己开可见窗口；\r\n"
        "rem 延时不用 ping，改用隐藏 PowerShell 里的 Start-Sleep；输出照旧\r\n"
        "rem 重定向到 update.log。start /min 不接受（任务栏仍可见）。\r\n"
        "rem 占住文件的进程：主程序退出时不杀子进程，浏览器运行时的 node.exe\r\n"
        "rem （安装目录下 _internal\\patchright\\driver\\node.exe）会变成孤儿，\r\n"
        "rem :procsweep 只杀同名 exe 杀不到它，robocopy 换 _internal 就失败。\r\n"
        "rem 所以换文件前先按 ExecutablePath 精确杀：只杀可执行路径在安装\r\n"
        "rem 目录里的进程（只能是我们自己的打包文件），绝不按映像名\r\n"
        "rem taskkill node.exe / chrome.exe（会误杀用户浏览器和其他 node）。\r\n"
        "rem 杀后等 2 秒、确认 node.exe 能打开再继续；还占着就写进 update.log\r\n"
        "rem 并走失败流程，不静默跳过。\r\n"
        "rem 单窗口接力：点更新后旧面板自己先关（前端 apply 成功就\r\n"
        "rem window.close() 自关，exe 这里再经句柄关一次），换文件全程\r\n"
        "rem 不弹黑窗口，换完 bat 重拉新 exe、新 exe 再开一个新面板；\r\n"
        "rem 任何时刻只能有一个窗口。旧面板没关掉时 bat 也绝不硬拷：\r\n"
        "rem :waitloop 等旧 exe PID 死透，:precopy 里任何 robocopy 之前\r\n"
        "rem 再查一次 PID，活着就走失败流程（用户可能在等待那几秒里\r\n"
        "rem 又双击打开了旧版）。\r\n"
        "rem 换完重开要核实：新 exe 拉起来不等于起来了（run 44758466045：\r\n"
        "rem robocopy 两次退出码都是 3、log 说“换好了”、结果写 applied，\r\n"
        "rem 但 8686 上 180 秒都没报新版、也没有 exe 在跑——裸 start 把\r\n"
        "rem 拉起失败静默吞掉了）。所以拉起后轮询 %PORT% /api/health 等\r\n"
        "rem 期望版本（最多约 180 秒），等到了才写 applied、删 staging；\r\n"
        "rem 等不到先探端口：已有实例在应答就只写 failed/nostart、不再拉、\r\n"
        "rem 也不回滚（迟到的实例随后会起来）；端口没应答就再拉一次新 exe、\r\n"
        "rem 再等 60 秒，还不起来就走 :rollback 换回旧版、按\r\n"
        "rem failed/nostart_rolled_back 写结果（S-1：坏新版绝不能永久换掉\r\n"
        "rem 能用的旧版）；staging 在任何失败路径都不删。\r\n"
        "rem 回滚重开也要核实：:fail_write 以前先写结果再裸 start 重拉旧版，\r\n"
        "rem 旧版拉不起来也照写 copy/stuck，用户看到的还是“旧版本还能用”。\r\n"
        "rem 现在照成功分支的写法：重拉旧 exe 后轮询 %PORT% /api/health 等\r\n"
        "rem 旧版本号 %CURRENT%（最多约 180 秒），等到了才按原原因码写 failed、\r\n"
        "rem 等不到写 failed/nostart_old、再拉一次旧 exe；staging 任何失败\r\n"
        "rem 路径都不删（重试不用重新下载）。\r\n"
        "rem 回滚本身也要核实（H-1）：恢复用的两条 robocopy 同样判 RC>=8，\r\n"
        "rem 恢复后校验旧 exe 与 _internal 都在；失败就写\r\n"
        "rem failed/rollback_failed，绝不写“已恢复旧版本”，原原因码也不再\r\n"
        "rem 被 nostart_old 覆盖。\r\n"
        "setlocal\r\n"
        'set "STAGING=%~1"\r\n'
        'if "%STAGING:~-1%"=="\\" set "STAGING=%STAGING:~0,-1%"\r\n'
        'set "EXE_NAME=%~2"\r\n'
        'set "PID=%~3"\r\n'
        'set "EXPECTED=%~4"\r\n'
        'set "BACKUP=%~5"\r\n'
        'set "CURRENT=%~6"\r\n'
        'set "PORT=%~7"\r\n'
        'rem 第 7 个参数是重开后的服务端口：bat 拉起 exe 时不带参数，\r\n'
        'rem 启动器默认跑 web --port 8686，重开的实例永远跑 8686，轮询\r\n'
        'rem 必须问这个端口；没传就默认 8686。传更新前的端口是错的\r\n'
        'rem （v31：服务跑 18686、重开的跑 8686，bat 在 18686 上等满\r\n'
        'rem 180 秒也等不到旧版，:fail_poll 误报 nostart_old）。\r\n'
        'if "%PORT%"=="" set "PORT=8686"\r\n'
        'set "INSTALL_DIR=%~dp0"\r\n'
        'rem dp0 自带末尾反斜杠：INSTALL_DIR 传给 robocopy 时，末尾反斜杠会把收尾引号转义掉，开关全被吞进路径里、robocopy 瞬间报错（v16 到 v17 全流程测试 run 44625451318：等待已修好，换文件 0.22 秒失败回滚）。先去掉末尾反斜杠，之后拼路径都显式加反斜杠。\r\n'
        'if "%INSTALL_DIR:~-1%"=="\\" set "INSTALL_DIR=%INSTALL_DIR:~0,-1%"\r\n'
        'set "LOG=%INSTALL_DIR%\\update.log"\r\n'
        'set "RESULT=%INSTALL_DIR%\\update-result.json"\r\n'
        'echo [%date% %time%] 开始更新，正在等主程序退出... > "%LOG%"\r\n'
        "set /a WAITED=0\r\n"
        ":waitloop\r\n"
        'powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command "$p = Get-Process -Id %PID% -ErrorAction SilentlyContinue; if ($p -and $p.ProcessName -eq [System.IO.Path]::GetFileNameWithoutExtension(\'%EXE_NAME%\')) { exit 0 } else { exit 1 }" <nul\r\n'
        "if %errorlevel% neq 0 goto :procsweep\r\n"
        "set /a WAITED+=1\r\n"
        "if %WAITED% geq 60 goto :killtree\r\n"
        'powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command "Start-Sleep -Seconds 1" <nul\r\n'
        "goto :waitloop\r\n"
        ":killtree\r\n"
        'echo [%date% %time%] 等了 60 次（约一分钟）主程序还没退出，正在强制结束它和它打开的子程序... >> "%LOG%"\r\n'
        "powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command \"& taskkill /PID %PID% /T /F; exit $LASTEXITCODE\" >> \"%LOG%\" 2>&1\r\n"
        'powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command "Start-Sleep -Seconds 5" <nul\r\n'
        'powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command "$p = Get-Process -Id %PID% -ErrorAction SilentlyContinue; if ($p -and $p.ProcessName -eq [System.IO.Path]::GetFileNameWithoutExtension(\'%EXE_NAME%\')) { exit 0 } else { exit 1 }" <nul\r\n'
        "if %errorlevel% equ 0 goto :fail_stuck\r\n"
        ":procsweep\r\n"
        'echo [%date% %time%] 正在结束安装目录里的残留进程（浏览器运行时的 node.exe 等）... >> "%LOG%"\r\n'
        "powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command \"$installDir = '%INSTALL_DIR%'; $nodePath = $installDir + '\\_internal\\patchright\\driver\\node.exe'; $doomed = @(); foreach ($p in Get-CimInstance Win32_Process) { if ($p.ExecutablePath -and $p.ExecutablePath.StartsWith($installDir + '\\', [System.StringComparison]::OrdinalIgnoreCase)) { $doomed += $p } }; foreach ($p in $doomed) { try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch { Write-Output 'kill failed' } }; Start-Sleep -Seconds 2; if (Test-Path $nodePath) { try { $fs = [System.IO.File]::Open($nodePath, 'Open', 'ReadWrite', 'None'); $fs.Close(); exit 0 } catch { Write-Output 'node.exe still locked'; exit 1 } } else { exit 0 }\" >> \"%LOG%\" 2>&1\r\n"
        'set "KILL_RC=%errorlevel%"\r\n'
        'if %KILL_RC% neq 0 echo [%date% %time%] 安装目录里的 node.exe 还占着，换文件会失败，先停在这里。 >> "%LOG%"\r\n'
        "if %KILL_RC% neq 0 goto :fail_stuck\r\n"
        'powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command "if (Get-Process -Name ([System.IO.Path]::GetFileNameWithoutExtension(\'%EXE_NAME%\')) -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" <nul\r\n'
        "if %errorlevel% neq 0 goto :precopy\r\n"
        'echo [%date% %time%] 还有同名程序在跑（会占住要换的文件），正在结束... >> "%LOG%"\r\n'
        "powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command \"& taskkill /F /IM '%EXE_NAME%' /T; exit $LASTEXITCODE\" >> \"%LOG%\" 2>&1\r\n"
        'powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command "Start-Sleep -Seconds 3" <nul\r\n'
        'powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command "if (Get-Process -Name ([System.IO.Path]::GetFileNameWithoutExtension(\'%EXE_NAME%\')) -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" <nul\r\n'
        "if %errorlevel% equ 0 goto :fail_stuck\r\n"
        ":precopy\r\n"
        'rem 最后关口：任何 robocopy 之前再确认一次旧 exe PID 已死。\r\n'
        'rem :waitloop 等过、:procsweep 杀过，但用户可能在这几秒里又双击\r\n'
        'rem 打开了旧版（或杀失败了）：活着就先停在这里走失败流程，\r\n'
        'rem 绝不带着活进程硬拷——硬拷必然撞锁，还可能把新旧文件混在一起。\r\n'
        'powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command "$p = Get-Process -Id %PID% -ErrorAction SilentlyContinue; if ($p -and $p.ProcessName -eq [System.IO.Path]::GetFileNameWithoutExtension(\'%EXE_NAME%\')) { exit 0 } else { exit 1 }" <nul\r\n'
        'if %errorlevel% equ 0 goto :fail_stuck\r\n'
        'if not exist "%STAGING%\\%EXE_NAME%" goto :fail_nopkg\r\n'
        'if not exist "%STAGING%\\_internal\\" goto :fail_nopkg\r\n'
        ":copy\r\n"
        'echo [%date% %time%] 主程序已退出，正在换上新版本... >> "%LOG%"\r\n'
        "powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command \"& robocopy '%STAGING%' '%INSTALL_DIR%' /E /IS /R:5 /W:3 /XD data /XF config.yaml; exit $LASTEXITCODE\" >> \"%LOG%\" 2>&1\r\n"
        'set "RC=%errorlevel%"\r\n'
        'echo [%date% %time%] robocopy 换主文件退出码 %RC% >> "%LOG%"\r\n'
        'if %RC% geq 8 goto :rollback\r\n'
        "powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command \"& robocopy '%STAGING%\\_internal' '%INSTALL_DIR%\\_internal' /MIR /R:3 /W:2; exit $LASTEXITCODE\" >> \"%LOG%\" 2>&1\r\n"
        'set "RC=%errorlevel%"\r\n'
        'echo [%date% %time%] robocopy 换 _internal 退出码 %RC% >> "%LOG%"\r\n'
        'if %RC% geq 8 goto :rollback\r\n'
        'if not exist "%INSTALL_DIR%\\%EXE_NAME%" goto :rollback\r\n'
        'echo [%date% %time%] 换好了，正在重新打开... >> "%LOG%"\r\n'
        'start "" "%INSTALL_DIR%\\%EXE_NAME%"\r\n'
        'echo [%date% %time%] 新版本已拉起，正在等它在 %PORT% 上报到期望版本（最多等 180 秒）... >> "%LOG%"\r\n'
        "powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command \"$deadline = (Get-Date).AddSeconds(180); $ok = $false; $seen = ''; while ((Get-Date) -lt $deadline) { try { $h = Invoke-RestMethod -Uri 'http://127.0.0.1:%PORT%/api/health' -TimeoutSec 5; $seen = $h.version; if ($seen -eq '%EXPECTED%') { $ok = $true; break } } catch { $seen = '' }; Start-Sleep -Seconds 3 }; Write-Output ('health poll: ok=' + $ok + ' last_seen_version=' + $seen); if ($ok) { exit 0 } else { exit 1 }\" >> \"%LOG%\" 2>&1\r\n"
        'set "HEALTH_RC=%errorlevel%"\r\n'
        'if %HEALTH_RC% neq 0 goto :fail_nostart\r\n'
        'echo [%date% %time%] 新版本已起来，版本对上了。 >> "%LOG%"\r\n'
        '> "%RESULT%" echo {"status": "applied", "expected_version": "%EXPECTED%", "reason_code": ""}\r\n'
        'rmdir /s /q "%STAGING%"\r\n'
        '(goto) 2>nul & del "%~f0"\r\n'
        ":fail_nostart\r\n"
        'echo [%date% %time%] 180 秒内新版本没在 %PORT% 上起来，更新没换完。 >> "%LOG%"\r\n'
        'rem M-44：先确认是不是已有实例在起来：端口已有应答就不再拉，\r\n'
        'rem 免得两个实例抢绑端口（第一个随后起来、第二个又绑不上还可能误杀健康实例）。\r\n'
        "powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command \"try { $h = Invoke-RestMethod -Uri 'http://127.0.0.1:%PORT%/api/health' -TimeoutSec 5; exit 0 } catch { exit 1 }\" <nul\r\n"
        'set "PROBE_RC=%errorlevel%"\r\n'
        'if %PROBE_RC% equ 0 goto :fail_nostart_answered\r\n'
        'echo [%date% %time%] 再拉一次新版本（最后一次重试机会），安装包留着、不用重新下载。 >> "%LOG%"\r\n'
        'start "" "%INSTALL_DIR%\\%EXE_NAME%"\r\n'
        'echo [%date% %time%] 第二次拉起后继续等新版本（最多 60 秒），还不起来就换回旧版本。 >> "%LOG%"\r\n'
        "powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command \"$deadline = (Get-Date).AddSeconds(60); $ok = $false; $seen = ''; while ((Get-Date) -lt $deadline) { try { $h = Invoke-RestMethod -Uri 'http://127.0.0.1:%PORT%/api/health' -TimeoutSec 5; $seen = $h.version; if ($seen -eq '%EXPECTED%') { $ok = $true; break } } catch { $seen = '' }; Start-Sleep -Seconds 3 }; Write-Output ('health poll (retry): ok=' + $ok + ' last_seen_version=' + $seen); if ($ok) { exit 0 } else { exit 1 }\" >> \"%LOG%\" 2>&1\r\n"
        'set "HEALTH2_RC=%errorlevel%"\r\n'
        'if %HEALTH2_RC% equ 0 goto :fail_nostart_recovered\r\n'
        'echo [%date% %time%] 第二次拉起也没起来，正在换回旧版本。 >> "%LOG%"\r\n'
        'set "FAIL_CODE=nostart_rolled_back"\r\n'
        'goto :rollback\r\n'
        ":fail_nostart_recovered\r\n"
        'echo [%date% %time%] 新版本第二次拉起后起来了，版本对上了。 >> "%LOG%"\r\n'
        '> "%RESULT%" echo {"status": "applied", "expected_version": "%EXPECTED%", "reason_code": ""}\r\n'
        'rmdir /s /q "%STAGING%"\r\n'
        'goto :fail_exit\r\n'
        ":fail_nostart_answered\r\n"
        'echo [%date% %time%] 端口 %PORT% 上已有实例在应答，不再重复拉起、也不回滚。 >> "%LOG%"\r\n'
        '> "%RESULT%" echo {"status": "failed", "expected_version": "%EXPECTED%", "reason_code": "nostart"}\r\n'
        'goto :fail_exit\r\n'
        ":rollback\r\n"
        'if "%FAIL_CODE%"=="" set "FAIL_CODE=copy"\r\n'
        'echo [%date% %time%] 正在用备份恢复旧版本... >> "%LOG%"\r\n'
        'if "%BACKUP%"=="" goto :fail_copy\r\n'
        'rem v33：回滚前先停掉还活着的程序。换版后拉起的新版还占着文件，\r\n'
        'rem 直接 robocopy 恢复会撞锁（v31→v32 全流程测试 run 44880570903：\r\n'
        'rem 新版在 8686 上跑着，bat 在 18686 上等它、误判后回滚，恢复退出码 11、\r\n'
        'rem verdict 是 rollback_failed）。:precopy 前有同样的关口（绝不带着\r\n'
        'rem 活进程硬拷），回滚这里补上；停不掉就如实写 rollback_failed，不硬拷。\r\n'
        'echo [%date% %time%] 正在结束还活着的主程序，免得它占住要恢复的文件... >> "%LOG%"\r\n'
        "powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command \"$installDir = '%INSTALL_DIR%'; $nodePath = $installDir + '\\_internal\\patchright\\driver\\node.exe'; $doomed = @(); foreach ($p in Get-CimInstance Win32_Process) { if ($p.ExecutablePath -and $p.ExecutablePath.StartsWith($installDir + '\\', [System.StringComparison]::OrdinalIgnoreCase)) { $doomed += $p } }; foreach ($p in $doomed) { try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch { Write-Output 'kill failed' } }; Start-Sleep -Seconds 2; if (Test-Path $nodePath) { try { $fs = [System.IO.File]::Open($nodePath, 'Open', 'ReadWrite', 'None'); $fs.Close(); exit 0 } catch { Write-Output 'node.exe still locked'; exit 1 } } else { exit 0 }\" >> \"%LOG%\" 2>&1\r\n"
        'set "KILL_RC=%errorlevel%"\r\n'
        'if %KILL_RC% neq 0 echo [%date% %time%] 主程序还占着文件，恢复旧版本会失败，先停在这里。 >> "%LOG%"\r\n'
        "if %KILL_RC% neq 0 goto :rollback_failed\r\n"
        'powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command "if (Get-Process -Name ([System.IO.Path]::GetFileNameWithoutExtension(\'%EXE_NAME%\')) -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" <nul\r\n'
        "if %errorlevel% neq 0 goto :rollback_restore\r\n"
        'echo [%date% %time%] 还有同名程序在跑（会占住要恢复的文件），正在结束... >> "%LOG%"\r\n'
        "powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command \"& taskkill /F /IM '%EXE_NAME%' /T; exit $LASTEXITCODE\" >> \"%LOG%\" 2>&1\r\n"
        'powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command "Start-Sleep -Seconds 3" <nul\r\n'
        'powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command "if (Get-Process -Name ([System.IO.Path]::GetFileNameWithoutExtension(\'%EXE_NAME%\')) -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" <nul\r\n'
        "if %errorlevel% equ 0 goto :rollback_failed\r\n"
        ":rollback_restore\r\n"
        "powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command \"& robocopy '%INSTALL_DIR%\\%BACKUP%' '%INSTALL_DIR%' /E /IS /R:3 /W:2 /XD data /XF config.yaml; exit $LASTEXITCODE\" >> \"%LOG%\" 2>&1\r\n"
        'set "RC=%errorlevel%"\r\n'
        'echo [%date% %time%] robocopy 恢复主文件退出码 %RC% >> "%LOG%"\r\n'
        'if %RC% geq 8 goto :rollback_failed\r\n'
        'if not exist "%INSTALL_DIR%\\%BACKUP%\\_internal\\" goto :rollback_after_internal\r\n'
        "powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command \"& robocopy '%INSTALL_DIR%\\%BACKUP%\\_internal' '%INSTALL_DIR%\\_internal' /MIR /R:3 /W:2; exit $LASTEXITCODE\" >> \"%LOG%\" 2>&1\r\n"
        'set "RC=%errorlevel%"\r\n'
        'echo [%date% %time%] robocopy 恢复 _internal 退出码 %RC% >> "%LOG%"\r\n'
        'if %RC% geq 8 goto :rollback_failed\r\n'
        ":rollback_after_internal\r\n"
        'if not exist "%INSTALL_DIR%\\%EXE_NAME%" goto :rollback_failed\r\n'
        'if not exist "%INSTALL_DIR%\\_internal\\" goto :rollback_failed\r\n'
        'echo [%date% %time%] 已恢复旧版本。 >> "%LOG%"\r\n'
        "goto :fail_write\r\n"
        ":rollback_failed\r\n"
        'echo [%date% %time%] 恢复旧版本失败：备份没能完整换回来，安装可能新旧混装，请手动重装完整包。 >> "%LOG%"\r\n'
        'set "FAIL_CODE=rollback_failed"\r\n'
        "goto :fail_write\r\n"
        ":fail_stuck\r\n"
        'set "FAIL_CODE=stuck"\r\n'
        "goto :fail_write\r\n"
        ":fail_nopkg\r\n"
        'set "FAIL_CODE=nopkg"\r\n'
        "goto :fail_write\r\n"
        ":fail_copy\r\n"
        'if "%FAIL_CODE%"=="" set "FAIL_CODE=copy"\r\n'
        "goto :fail_write\r\n"
        ":fail_write\r\n"
        'echo [%date% %time%] 更新没换成，正在重开旧版本并确认它起来了... >> "%LOG%"\r\n'
        'powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command "if (Get-Process -Name ([System.IO.Path]::GetFileNameWithoutExtension(\'%EXE_NAME%\')) -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" <nul\r\n'
        "if %errorlevel% equ 0 goto :fail_poll\r\n"
        'if exist "%INSTALL_DIR%\\%EXE_NAME%" start "" "%INSTALL_DIR%\\%EXE_NAME%"\r\n'
        ":fail_poll\r\n"
        'echo [%date% %time%] 旧版本已拉起，正在等它在 %PORT% 上报到旧版本（最多等 180 秒）... >> "%LOG%"\r\n'
        "powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command \"$deadline = (Get-Date).AddSeconds(180); $ok = $false; $seen = ''; while ((Get-Date) -lt $deadline) { try { $h = Invoke-RestMethod -Uri 'http://127.0.0.1:%PORT%/api/health' -TimeoutSec 5; $seen = $h.version; if (($seen -eq '%CURRENT%') -or ('%CURRENT%' -eq '' -and $seen -ne '' -and $seen -ne '%EXPECTED%')) { $ok = $true; break } } catch { $seen = '' }; Start-Sleep -Seconds 3 }; Write-Output ('health poll: ok=' + $ok + ' last_seen_version=' + $seen); if ($ok) { exit 0 } else { exit 1 }\" >> \"%LOG%\" 2>&1\r\n"
        'set "OLD_HEALTH_RC=%errorlevel%"\r\n'
        'if %OLD_HEALTH_RC% neq 0 goto :fail_nostart_old\r\n'
        'echo [%date% %time%] 旧版本已起来，版本对上了。 >> "%LOG%"\r\n'
        '> "%RESULT%" echo {"status": "failed", "expected_version": "%EXPECTED%", "reason_code": "%FAIL_CODE%"}\r\n'
        "goto :fail_exit\r\n"
        ":fail_nostart_old\r\n"
        'echo [%date% %time%] 180 秒内旧版本没在 %PORT% 上起来，更新没换成、旧版本也没起来。 >> "%LOG%"\r\n'
        'if "%FAIL_CODE%"=="rollback_failed" goto :fail_nostart_old_rb\r\n'
        '> "%RESULT%" echo {"status": "failed", "expected_version": "%EXPECTED%", "reason_code": "nostart_old"}\r\n'
        'goto :fail_nostart_old_retry\r\n'
        ":fail_nostart_old_rb\r\n"
        '> "%RESULT%" echo {"status": "failed", "expected_version": "%EXPECTED%", "reason_code": "rollback_failed"}\r\n'
        ":fail_nostart_old_retry\r\n"
        'echo [%date% %time%] 再拉一次旧版本（最后一次重试机会）。 >> "%LOG%"\r\n'
        'rem M-44：同 :fail_nostart，端口已有应答就不再拉，免得两个实例抢绑端口。\r\n'
        "powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command \"try { $h = Invoke-RestMethod -Uri 'http://127.0.0.1:%PORT%/api/health' -TimeoutSec 5; exit 0 } catch { exit 1 }\" <nul\r\n"
        'set "PROBE_RC=%errorlevel%"\r\n'
        'if %PROBE_RC% equ 0 echo [%date% %time%] 端口 %PORT% 上已有实例在应答，不再重复拉起。 >> "%LOG%"\r\n'
        'if %PROBE_RC% neq 0 if exist "%INSTALL_DIR%\\%EXE_NAME%" start "" "%INSTALL_DIR%\\%EXE_NAME%"\r\n'
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
        "reason_code": reason_code if isinstance(reason_code, str) else "",
    }


def _coerce_port(value: Any) -> int:
    """Normalize a listen port to 1-65535, falling back to 8686."""
    try:
        port = int(value)
    except (TypeError, ValueError):
        return 8686
    return port if 1 <= port <= 65535 else 8686


def _service_reports_version(port: int, expected: str) -> bool:
    """True when /api/health on ``port`` answers ok with ``expected``.

    Stdlib urllib on purpose (no httpx import chain here): a 2 s
    timeout, any failure (refused, timeout, bad JSON, wrong shape)
    means False — the caller keeps its original verdict.
    """
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/health", timeout=2
        ) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return False
    return (
        isinstance(payload, dict)
        and payload.get("status") == "ok"
        and payload.get("version") == expected
    )


def _update_health_port(port: Any = None) -> int:
    """Resolve the port the health re-probe should ask.

    Explicit port wins; otherwise the running instance's own record
    (data/.instance.json, written at startup with its listen port);
    otherwise the 8686 default. A stale record is harmless — the
    probe only ever turns a nostart verdict into ok when the service
    verifiably reports the expected version on that port.
    """
    if port is not None:
        return _coerce_port(port)
    try:
        from bosshunter.browser.runtime import _read_instance_file

        record = _read_instance_file()
    except Exception:
        record = None
    if isinstance(record, dict):
        return _coerce_port(record.get("port"))
    return 8686


def _bat_is_alive(install_dir: str | Path) -> bool:
    """True when the update batch file is still there and fresh.

    bat 写完 verdict 才自删（所有终局路径都经 :fail_exit 或 applied 自删），
    所以 update.bat 还在 = bat 还没写完结论。只认 15 分钟内的文件：bat 的
    合法耗时到顶约 8 分钟，超过即视为 bat 已死、文件是残留（被杀掉的 bat
    不会自删），不再让位。纯读文件状态，不加锁、不跨进程探查、不拖慢启动。
    """
    try:
        stamp = (Path(install_dir) / "update.bat").stat().st_mtime
    except OSError:
        return False
    return (time.time() - stamp) < _BAT_ALIVE_MAX_SECONDS


def is_update_in_progress(install_dir: str | Path) -> bool:
    """True when an update is still mid-flight (pending + live bat).

    启动线程用它等 bat 退出再核对；pending 与 bat 两者缺一不可——
    bat 没拉起来时 apply_update 会清 pending（update.bat 残留不算数），
    没有 pending 时更谈不上进行中。
    """
    if not (Path(install_dir) / UPDATE_PENDING_FILENAME).is_file():
        return False
    return _bat_is_alive(install_dir)


def consume_pending_update(install_dir: str | Path, port: Any = None) -> dict | None:
    """Consume update-pending.json left by apply_update.

    Returns the verdict dict (status "ok" or "failed" plus the plain
    reason), {"status": "in_progress"} while the batch file is still
    alive (the verdict is not final yet — pending is left untouched
    for a later call), or None when no update was pending. The verdict
    is also written to update-result.json so later boots still know.

    ``port`` is the port the local service listens on (M-44): when the
    bat wrote failed/nostart (or no result at all) the verdict is
    re-probed once against the live service before judging failure —
    a service that is already healthy on the expected version means
    the update succeeded late, not failed. Only nostart/missing-result
    is rescued: a version match cannot prove a partial copy (copy /
    stuck / nopkg / mismatch) is complete.
    """
    pending_path = Path(install_dir) / UPDATE_PENDING_FILENAME
    if not pending_path.is_file():
        return None
    if _bat_is_alive(install_dir):
        # v32（run 44873002310 Retry）：bat 还活着时，后端不写最终结论。
        # bat 写 verdict 是它自删前的最后一步，update.bat 还在 = 结论还没
        # 落定；这时写 failed/unknown 会抢在 bat 的具体原因码（stuck/copy）
        # 前面，两处写同一个文件撞上。pending 不消费、不写结果文件、不
        # rescue——留给 bat 写完、自删之后的那次核对。
        try:
            _pending = json.loads(pending_path.read_text(encoding="utf-8"))
        except Exception:
            _pending = {}
        _expected = _pending.get("expected_version") if isinstance(_pending, dict) else None
        return {
            "status": "in_progress",
            "expected_version": _expected if isinstance(_expected, str) else "",
            "actual_version": __version__,
            "reason": "",
            "reason_code": "",
        }
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
            "reason_code": "",
        }
        # 换版成功：缓存的包已经用完，清掉，免得下次更新误认旧包。
        clear_cached_update(install_dir)
    elif isinstance(expected, str) and expected:
        # M-44：bat 说 nostart（或根本没写结果），但本地服务可能
        # 只是起来得慢——重探一次，迟到成功不判失败。
        if (
            (bat_status == "failed" and bat_code in {"nostart", "nostart_old"})
            or bat_status is None
        ) and _service_reports_version(_update_health_port(port), expected):
            verdict = {
                "status": "ok",
                "expected_version": expected,
                "actual_version": __version__,
                "reason": "",
                "reason_code": "",
            }
            clear_cached_update(install_dir)
        else:
            if bat_status == "failed":
                code = bat_code if isinstance(bat_code, str) else "unknown"
                reason = UPDATE_FAILURE_MESSAGES.get(code, UPDATE_FAILURE_MESSAGES["unknown"])
            elif bat_status == "applied":
                code = "mismatch"
                reason = UPDATE_FAILURE_MESSAGES["mismatch"]
            else:
                code = "unknown"
                reason = UPDATE_FAILURE_MESSAGES["unknown"]
            verdict = {
                "status": "failed",
                "expected_version": expected,
                "actual_version": __version__,
                "reason": reason,
                "reason_code": code,
            }
    else:
        verdict = {
            "status": "failed",
            "expected_version": expected if isinstance(expected, str) else "",
            "actual_version": __version__,
            "reason": UPDATE_FAILURE_MESSAGES["unknown"],
            "reason_code": "unknown",
        }
    write_update_result(install_dir, verdict)
    return verdict


def reconcile_pending_update(install_dir: str | Path, port: Any = None) -> bool:
    """Handle a pending/failed update at startup.

    ``port`` is passed through to consume_pending_update (M-44 late-
    success re-probe); None resolves via the instance record / 8686.

    Consumes any pending marker and, when the previous update failed
    (or an earlier failure is still unfixed), puts the update state
    to "error" with the plain-language reason. Returns True when the
    caller should skip the automatic version check — the user should
    see the failure, not another prompt for the same update.
    """
    pending = consume_pending_update(install_dir, port=port)
    if pending is not None:
        if pending["status"] == "in_progress":
            # bat 还在换文件/等重开：结论没落定，不报失败、也不弹更新，
            # 等 bat 退出后的下次核对再定。状态原样不动（启动时是 idle）。
            return True
        if pending["status"] == "ok":
            return False
        _set_status(
            status="error",
            update_available=False,
            info=None,
            message="",
            error=pending["reason"],
            reason_code=pending.get("reason_code", ""),
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
            reason_code=last.get("reason_code", ""),
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
    # 上次检查失败的原因码（timeout / unauthorized / no_release /
    # bad_sha / network / ...）：status="error" + reason_code=
    # "check_failed" 时才有值，下游靠它区分“查失败了”和“上次更新
    # 换失败了”。成功检查会清掉。
    "check_reason": "",
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


def run_check(config: dict, timeout: float = CHECK_TIMEOUT, force: bool = False,
              install_dir: str | Path | None = None) -> dict[str, Any]:
    """Synchronous version check with a freshness cache. Never raises.

    A check done less than CHECK_CACHE_SECONDS ago is reused unless
    `force` is set. A failed check is now distinguishable from
    "already latest": it keeps the previously known info (a slow auto
    check must never wipe out a newer version a manual check just
    found), leaves checked_at alone (failures are not cached for
    600s — the next check retries promptly), and reports
    status="error" with reason_code="check_failed" plus the plain
    reason in `error`, so a manual check can show "没查到，点再试一次"
    instead of going silent. No token means the check never ran:
    that stays idle with no error, only clearing a stale reason code.
    When a newer version is found and the release zip is already cached
    on disk (see load_cached_update), the state goes straight to "ready"
    without downloading again.
    """
    now = time.monotonic()
    with _STATE_LOCK:
        checked_at = _STATE.get("checked_at")
        fresh = checked_at is not None and now - checked_at < CHECK_CACHE_SECONDS
        cached = dict(_STATE)
    if fresh and not force:
        return cached
    _set_status(status="checking", error="", reason_code="", check_reason="")
    try:
        info, failure = _check_for_update_detailed(config, timeout=timeout)
    except Exception as exc:
        _log_check_failure("unknown", str(exc))
        info, failure = None, {"reason_code": "unknown", "message": CHECK_FAILURE_MESSAGES["unknown"]}
    if failure is not None:
        if failure.get("reason_code") == "no_token":
            # 没配好 token：这轮根本没查，不算查失败——不留 error、
            # 不清已有 info、不写 checked_at，只把旧原因码清掉。
            _set_status(status="idle", error="", reason_code="", check_reason="")
            return get_status()
        with _STATE_LOCK:
            prev_info = _STATE.get("info")
            prev_available = bool(_STATE.get("update_available"))
        _set_status(
            status="error",
            update_available=prev_available,
            info=prev_info,
            error=failure.get("message") or CHECK_FAILURE_MESSAGES["unknown"],
            reason_code="check_failed",
            check_reason=failure.get("reason_code") or "unknown",
            message="",
        )
        return get_status()
    if info:
        target = Path(install_dir) if install_dir is not None else get_install_dir()
        if target is not None and _restore_from_cache(target, info):
            _set_status(
                status="ready",
                update_available=True,
                info=info,
                checked_at=time.monotonic(),
                message="新版本下好了",
                error="",
                reason_code="",
                check_reason="",
            )
            return get_status()
    _set_status(
        status="idle",
        update_available=bool(info),
        info=info,
        checked_at=time.monotonic(),
        message="有新版本了" if info else "",
        error="",
        reason_code="",
        check_reason="",
    )
    return get_status()


def start_download(config: dict, install_dir: str | Path | None = None) -> dict[str, Any]:
    """Start the background download of the checked release.

    Raises UpdateError when there is nothing to download or a download
    is already running. Returns the current state immediately. When
    the release zip is already cached on disk, restores the ready
    state from the cache instead of downloading again.
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
    target = Path(install_dir) if install_dir is not None else get_install_dir()
    if target is not None and _restore_from_cache(target, info):
        return get_status()
    _set_status(status="downloading", progress=0, message="正在下载新版本…", error="", reason_code="")
    thread = threading.Thread(target=_download_worker, args=(config, dict(info), target), daemon=True)
    thread.start()
    return get_status()


def _download_worker(config: dict, info: dict, install_dir: str | Path | None = None) -> None:
    tmpdir = tempfile.mkdtemp(prefix="jianli-update-")
    try:
        zip_path = download_update(config, info, tmpdir, progress_cb=_progress)
        _set_status(status="verifying", progress=100, message="正在检查下载好的文件…")
        staging = extract_update(zip_path, Path(tmpdir) / "staging")
        payload_root = validate_staged_payload(find_payload_root(staging))
        # 包下好、校验通过就把指针落盘：exe 退出时内存里的 tmpdir
        # 路径就丢了，下次启动只能认 data/update-cache 里的包。
        target = Path(install_dir) if install_dir is not None else get_install_dir()
        if target is not None:
            save_cached_update(target, info, zip_path)
        _set_status(
            status="ready",
            progress=100,
            message="新版本下好了",
            zip_path=str(zip_path),
            staging_dir=str(payload_root),
        )
    except UpdateError as exc:
        _remove_quietly(Path(tmpdir))
        _set_status(status="error", progress=0, message="", error=exc.user_message, zip_path=None, staging_dir=None, reason_code="", check_reason="")
    except Exception:
        _remove_quietly(Path(tmpdir))
        _set_status(status="error", progress=0, message="", error="下载出问题了，稍后再试。", zip_path=None, staging_dir=None, reason_code="", check_reason="")


# ─── 已下载包的落盘缓存 ─────────────────────────────────────
# 旧版 exe 退出时不杀子进程、内存里的 zip/staging 路径随 os._exit
# 一起消失：包其实还在磁盘上（bat 失败时不删 staging），但重开后
# 后端认不出它，只能重下。改成下载+校验通过就把包和指针落盘到
# 安装目录下的 data/update-cache/（data/ 已被换文件排除，更新卷
# 不进去；换版成功后清掉）。

UPDATE_CACHE_DIRNAME = "update-cache"
UPDATE_CACHE_MANIFEST = "update-ready.json"


def _update_cache_dir(install_dir: str | Path | None = None) -> Path | None:
    """data/update-cache/ 的位置；拿不到安装目录时返回 None（不缓存）。"""
    target = Path(install_dir) if install_dir is not None else get_install_dir()
    if target is None:
        return None
    return target / "data" / UPDATE_CACHE_DIRNAME


def _cached_zip_name(version: str) -> str:
    return f"jianli-lieshou-{version}.zip"


def save_cached_update(install_dir: str | Path, info: dict, zip_path: str | Path) -> dict | None:
    """把校验通过的 zip 存进缓存并写 manifest，返回 manifest 或 None。

    只存 sha256 与 info 一致的包；写文件先落 .tmp 再 os.replace，
    免得半截文件被当成好包。任何一步出错都只返回 None，绝不挡更新。
    """
    try:
        cache_dir = _update_cache_dir(install_dir)
        if cache_dir is None:
            return None
        version = info.get("version") if isinstance(info, dict) else None
        sha = str(info.get("sha256", "")).lower() if isinstance(info, dict) else ""
        if not isinstance(version, str) or not version:
            return None
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            return None
        src = Path(zip_path)
        if not src.is_file():
            return None
        digest = hashlib.sha256()
        size = 0
        with open(src, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
                size += len(chunk)
        if digest.hexdigest() != sha:
            return None
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached_zip = cache_dir / _cached_zip_name(version)
        tmp_zip = cache_dir / (cached_zip.name + ".tmp")
        shutil.copyfile(src, tmp_zip)
        os.replace(tmp_zip, cached_zip)
        # 新包进来，旧版本的缓存 zip 就没用了，顺手清掉。
        for old in cache_dir.glob("jianli-lieshou-*.zip"):
            if old != cached_zip:
                _remove_quietly(old)
        manifest = {
            "version": version,
            "sha256": sha,
            "size": size,
            "zip_path": str(cached_zip),
            "downloaded_at": time.time(),
        }
        tmp_manifest = cache_dir / (UPDATE_CACHE_MANIFEST + ".tmp")
        tmp_manifest.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_manifest, cache_dir / UPDATE_CACHE_MANIFEST)
        return dict(manifest)
    except Exception:
        return None


def load_cached_update(install_dir: str | Path | None = None) -> dict | None:
    """读缓存 manifest，校验通过才返回；否则返回 None。

    校验：manifest 是合法 JSON、版本非空、sha256 是 64 位 hex、
    zip_path 必须就是缓存目录里该版本的包（防篡改的 manifest 把
    路径指到别处）、文件存在、大小对得上、内容 sha256 对得上。
    只读不删：清缓存是调用方（恢复失败 / 版本不符 / 换版成功）的事。
    """
    cache_dir = _update_cache_dir(install_dir)
    if cache_dir is None:
        return None
    try:
        manifest = json.loads((cache_dir / UPDATE_CACHE_MANIFEST).read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(manifest, dict):
        return None
    version = manifest.get("version")
    sha = manifest.get("sha256")
    size = manifest.get("size")
    zip_path = manifest.get("zip_path")
    if not isinstance(version, str) or not version:
        return None
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha):
        return None
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        return None
    if not isinstance(zip_path, str) or not zip_path:
        return None
    expected = cache_dir / _cached_zip_name(version)
    try:
        if Path(zip_path).resolve() != expected.resolve():
            return None
    except OSError:
        return None
    if not expected.is_file():
        return None
    if expected.stat().st_size != size:
        return None
    digest = hashlib.sha256()
    try:
        with open(expected, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    if digest.hexdigest() != sha.lower():
        return None
    return {
        "version": version,
        "sha256": sha.lower(),
        "size": size,
        "zip_path": str(expected),
        "downloaded_at": manifest.get("downloaded_at"),
    }


def clear_cached_update(install_dir: str | Path | None = None) -> None:
    """清掉缓存 manifest 和缓存 zip（换版成功 / 版本不符 / 校验失败时调）。"""
    cache_dir = _update_cache_dir(install_dir)
    if cache_dir is None:
        return
    _remove_quietly(cache_dir / UPDATE_CACHE_MANIFEST)
    try:
        for old in list(cache_dir.glob("jianli-lieshou-*.zip")) + list(cache_dir.glob("*.tmp")):
            _remove_quietly(old)
    except OSError:
        pass


def _restore_from_cache(install_dir: str | Path, info: dict) -> bool:
    """从缓存恢复 ready 态：不重新下载。

    manifest 在、zip 在、sha256 对得上、且版本与 info 一致时，从
    缓存 zip 重新解压出 staging（旧 staging 可能随上次退出丢了）
    并跑 validate_staged_payload，成功就把状态置 ready。版本不符
    或任何一步校验失败都清掉缓存、返回 False 走正常重下。
    """
    manifest = load_cached_update(install_dir)
    cache_dir = _update_cache_dir(install_dir)
    if manifest is None:
        if cache_dir is not None and (cache_dir / UPDATE_CACHE_MANIFEST).is_file():
            clear_cached_update(install_dir)
        return False
    if manifest.get("version") != info.get("version") or \
            str(manifest.get("sha256", "")).lower() != str(info.get("sha256", "")).lower():
        clear_cached_update(install_dir)
        return False
    zip_path = Path(manifest["zip_path"])
    tmpdir = tempfile.mkdtemp(prefix="jianli-update-")
    try:
        staging = extract_update(zip_path, Path(tmpdir) / "staging")
        payload_root = validate_staged_payload(find_payload_root(staging))
    except Exception:
        _remove_quietly(Path(tmpdir))
        clear_cached_update(install_dir)
        return False
    _set_status(
        status="ready",
        progress=100,
        message="新版本下好了",
        zip_path=str(zip_path),
        staging_dir=str(payload_root),
    )
    return True


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
    挂在前面）。CREATE_NO_WINDOW 只作用于直接创建的 cmd.exe：它能
    让 cmd 本身不开窗口，但 cmd 拉起的孙进程（robocopy / ping /
    taskkill / PowerShell）并不会因此也不开窗口——旧注释称
    CREATE_NO_WINDOW 能让孙进程也不开窗口，这个说法是错的。孙进程
    的黑窗口靠 bat 里解决：所有控制台调用都经
    powershell -WindowStyle Hidden 调用，隐藏的 PowerShell 自带
    隐藏控制台，子进程继承它、不再自己开可见窗口。bat 的输出本来
    就写 update.log，不靠控制台看。
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

# 快捷方式带上产品 logo 的版本：1.1.12 起 spec 给 exe 嵌了 logo.ico，
# 快捷方式 IconLocation 指 exe 就显示 logo。marker 里记的是建快捷方式
# 时的版本号，低于这个版本说明快捷方式建在没图标的版本上，启动时要
# 重写一次 .lnk（只刷新已存在的，不复活用户删掉的）。
_DESKTOP_SHORTCUT_ICON_VERSION = "1.1.12"


def _parse_version_tuple(text: str) -> tuple[int, ...] | None:
    """把 "1.1.12" 这类版本号读成可比的整数元组；读不出来返回 None。"""
    parts = (text or "").strip().split(".")
    if not parts or any(not p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def _desktop_shortcut_script(exe: Path, refresh_only: bool = False) -> str:
    """建/刷新桌面快捷方式的 PowerShell 脚本。

    refresh_only=True 时只刷新已存在的 .lnk：先 Test-Path，桌面上没有
    就什么都不做（用户删了就不再补建），有才重写并输出 refreshed。
    """
    prefix = (
        "$desktop = [Environment]::GetFolderPath('Desktop'); "
        "$shell = New-Object -ComObject WScript.Shell; "
        "$lnkPath = Join-Path $desktop " + _ps_quote(_DESKTOP_SHORTCUT_NAME) + "; "
    )
    body = (
        "$lnk = $shell.CreateShortcut($lnkPath); "
        f"$lnk.TargetPath = {_ps_quote(str(exe))}; "
        f"$lnk.WorkingDirectory = {_ps_quote(str(exe.parent))}; "
        f"$lnk.IconLocation = {_ps_quote(str(exe))}; "
        "$lnk.Save()"
    )
    if not refresh_only:
        return prefix + body
    return (
        prefix
        + "if (Test-Path $lnkPath) { " + body + " Write-Output 'refreshed' } "
        + "else { Write-Output 'missing' }"
    )


def _refresh_desktop_shortcut_if_needed(marker: Path, exe: Path) -> bool:
    """marker 在但版本旧：刷新已存在的桌面快捷方式。

    marker 版本 >= 图标版本（1.1.12）就不用动；低于它才重写 .lnk。
    .lnk 不存在（用户删了）不复活，只把 marker 版本写到当前版；
    重写失败不改 marker，下次启动再试。返回 True 当且仅当重写成功。
    """
    try:
        marker_version = _parse_version_tuple(marker.read_text(encoding="utf-8"))
    except Exception:
        marker_version = None
    icon_version = _parse_version_tuple(_DESKTOP_SHORTCUT_ICON_VERSION)
    if (
        marker_version is not None
        and icon_version is not None
        and marker_version >= icon_version
    ):
        return False
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             _desktop_shortcut_script(exe, refresh_only=True)],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
    except Exception:
        return False
    if result.returncode != 0:
        return False
    stdout = result.stdout if isinstance(result.stdout, str) else ""
    refreshed = "refreshed" in stdout
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{__version__}\n", encoding="utf-8")
    except Exception:
        pass
    return refreshed


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

    图标刷新：1.1.12 起 exe 嵌了产品 logo，marker 里版本低于 1.1.12
    说明快捷方式建在没图标的版本上，启动时重写一次已存在的 .lnk
    （TargetPath/WorkingDirectory/IconLocation 都指回当前 exe），
    桌面图标跟着换成 logo；.lnk 已经被用户删了就不复活，只把 marker
    版本写到当前版，下次启动不再重复检查。

    只在 Windows + frozen 的 exe 里做；任何一步出错都悄悄返回
    False，绝不挡启动。新建或刷新成功才返回 True。
    """
    if not _is_windows() or not _is_frozen():
        return False
    target = Path(install_dir) if install_dir is not None else get_install_dir()
    if target is None:
        return False
    exe = Path(exe_path) if exe_path is not None else Path(sys.executable).resolve()
    marker = target / "data" / DESKTOP_SHORTCUT_MARKER_FILENAME
    if marker.is_file():
        return _refresh_desktop_shortcut_if_needed(marker, exe)
    script = _desktop_shortcut_script(exe)
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


def _install_dir_kill_script(install_dir: str | Path, exclude_pid: int | None = None) -> str:
    """Build the PowerShell script that ends processes under install_dir.

    v33 从 _kill_install_dir_processes 抽出：路径判定是红线，单测要能
    直接钉住它。只认 ExecutablePath 前缀：$prefix = 安装目录 + '\\'，
    再 StartsWith($prefix, OrdinalIgnoreCase)——目录边界在内，安装
    目录的兄弟目录（C:\\app2\\）不会被 C:\\app 的规则误杀；exclude_pid
    跳过指定 PID（调用方传自己，不自杀）。绝不按映像名杀 node.exe /
    chrome.exe（v28 教训：会误杀用户浏览器和其他 node）。
    """
    install = Path(install_dir)
    return (
        "$installDir = " + _ps_quote(str(install)) + "; "
        "$prefix = $installDir + '\\'; "
        "$doomed = @(); "
        "foreach ($p in Get-CimInstance Win32_Process) { "
        "if ($p.ExecutablePath -and $p.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)"
        + (f" -and $p.ProcessId -ne {int(exclude_pid)}" if exclude_pid is not None else "")
        + ") { $doomed += $p } }; "
        "$killed = 0; $failed = 0; "
        "foreach ($p in $doomed) { try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; $killed += 1 } catch { $failed += 1 } }; "
        # v35：没杀到东西就别睡这 1 秒——等进程退出只在真杀了进程
        # 时才有意义；每次启动都扫一遍时这 1 秒是白等的固定开销。
        "if ($killed -gt 0) { Start-Sleep -Seconds 1 }; "
        "Write-Output \"killed=$killed failed=$failed\""
    )


def _kill_install_dir_processes(install_dir: str | Path, exclude_pid: int | None = None) -> bool:
    """换文件前（Python 侧第一道防御）：结束可执行路径在安装目录里的进程。

    只认 ExecutablePath 前缀——安装目录里只能是我们自己的打包文件，
    包括变成孤儿的浏览器运行时 node.exe
    (`_internal\\patchright\\driver\\node.exe`)。绝不按映像名杀
    node.exe / chrome.exe（会误杀用户浏览器和其他 node）。只在
    Windows 上做；PowerShell 起不来或杀失败都只写进 update.log、
    返回 False，绝不阻断更新（bat 里的 :procsweep 是第二道防御，
    杀不掉会在那里走失败流程）。
    exclude_pid：跳过这个 PID——v25 的启动残留清理调这里时，当前
    进程自己就是安装目录里的新 exe，不排除会自杀；apply_update 里
    传当前 PID 也一样（旧 exe 由 bat 的 :precopy 门等它退出，不用
    在这里提前杀掉自己）。
    """
    if not _is_windows():
        return False
    install = Path(install_dir)
    log_path = install / "update.log"
    script = _install_dir_kill_script(install, exclude_pid)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
             "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
    except Exception as exc:
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(f"[{stamp}] 更新前结束安装目录内进程失败（没拦住更新）：{exc}\n")
        except OSError:
            pass
        return False
    killed = failed = 0
    match = re.search(r"killed=(\d+)\s+failed=(\d+)", result.stdout or "")
    if match:
        killed, failed = int(match.group(1)), int(match.group(2))
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] 更新前结束安装目录内进程：杀了 {killed} 个，失败 {failed} 个。\n")
    except OSError:
        pass
    return True


def apply_update(install_dir: str | Path, exe_name: str) -> Path:
    """Prepare the swap: backup, write update.bat, launch it detached.

    Only valid when a release is staged (status "ready") and only on
    Windows. The staged payload is validated first (a bad zip layout
    must fail here, not after the program has exited). The caller is
    responsible for exiting this process right after (see
    request_restart). Returns the batch file path.

    The bat polls /api/health on RELAUNCH_PORT while waiting for the
    new (or rolled-back) version: it relaunches the exe with no
    arguments, and the launcher defaults to `web` on port 8686, so the
    relaunched instance always listens there — never on the pre-update
    service port. v31 passed the current port (M-41) and that was
    wrong: in the CI flow the service runs on 18686 but the relaunched
    instance is on 8686, so the bat waited the full 180 s on 18686 and
    reported a false nostart_old instead of stuck (run 44873002310
    Retry); a user on a non-default port would be misjudged the same
    way. That is why apply_update takes no port at all — the relaunch
    port is a product constant, not a caller choice.
    """
    with _STATE_LOCK:
        status = _STATE.get("status")
        staging_dir = _STATE.get("staging_dir")
        info = _STATE.get("info")
    if status == "applying":
        # H-2：另一路 apply 已经占位换版中，第二路直接报“正在换版中”，
        # 不要报容易误导的“新版本还没下好”。
        raise UpdateError("正在换版中，请稍等。")
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
    # H-2：先占位再干活——在锁内把状态先置 applying，第二路
    # apply_update 到这里会看到 applying，直接报“正在换版中”，
    # 绝不能让两个请求同时进备份 + 双 bat 互毁。占位之后、起 bat
    # 之前任何一步失败，都要把状态放回 ready，否则用户永远点不了重试。
    with _STATE_LOCK:
        if _STATE.get("status") != "ready":
            raise UpdateError("正在换版中，请稍等。")
        _STATE.update(status="applying", progress=100, message="正在换上新版本…")
    try:
        prune_old_backups(install)
        backup_name = f"{UPDATE_BACKUP_PREFIX}{__version__}"
        backup_dir = install / backup_name
        create_backup(install, backup_dir)
        bat_path = write_update_script(install)
        # 旧的结果文件先清掉：新一轮 bat 万一没跑起来就写不出结果，
        # 上轮的 "applied" 不能被误读成这轮换成了。
        _remove_quietly(install / UPDATE_RESULT_FILENAME)
        write_pending_update(install, expected_version)
    except Exception:
        _set_status(status="ready", message="新版本下好了", error="")
        raise
    # 换版进行中：先置位，再关窗——关窗监视线程看到标志位就跳过，
    # 不会在换文件途中抢跑关停（见模块顶部的 _UPDATE_APPLYING）。
    global _UPDATE_APPLYING
    _UPDATE_APPLYING = True
    # 先关主界面 --app 窗口：只关 exe 启动时持有的那个句柄对应的进程，
    # 绝不碰登录浏览器（登录浏览器是另一个独立进程、另一个 profile）。
    # 窗口不关，换文件时可能被占住、端口也交接不干净；关窗口不删
    # profile，登录会话下次还能恢复。关不掉也不挡更新，bat 的等待
    # 循环是后备。
    # 注意：句柄关窗经常是空操作——Chrome 把 --app 交给已在跑的
    # Chrome 实例时，拉窗那个子进程立刻就退出了（poll() 不为 None），
    # close_app_window 直接返回 False，旧面板继续开着；真正关掉旧
    # 面板靠前端 apply 成功后 window.close() 自关。换文件的安全不
    # 靠关窗，靠 bat 等旧 exe PID 死透（:waitloop + :precopy 最后
    # 关口）。这里只把关没关掉记进 update.log，供排障时看。
    closed = False
    try:
        from bosshunter.app_window import close_app_window

        closed = bool(close_app_window())
    except Exception:
        closed = False
    if not closed:
        try:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            with open(install / "update.log", "a", encoding="utf-8") as fh:
                fh.write(
                    f"[{stamp}] 旧面板窗口没能经句柄关掉（可能是 Chrome 交给了已在跑的实例），"
                    "靠前端自关；换文件仍等旧进程退出后才开始。\n"
                )
        except OSError:
            pass
    # 先停浏览器运行时（另一位同事在 browser/runtime 里新增的
    # stop_runtime，停 _runtime_process 及其进程树）：这里懒导入，
    # 它还没合进来时 ImportError 直接跳过，不阻断更新。
    # 时机：起 bat 之前、杀 node 之前。
    try:
        from bosshunter.browser.runtime import stop_runtime

        stop_runtime()
    except Exception:
        pass
    # v25：再停我们拉起的自动化 Chrome（bat 的路径杀杀不到它，
    # Chrome 不在安装目录里，进程内停是唯一办法）。只停命令行含
    # 专用配置目录/调试端口的我们自己的 Chrome，用户浏览器不动。
    try:
        from bosshunter.browser.runtime import stop_launched_browser

        stop_launched_browser()
    except Exception:
        pass
    # Python 侧第一道防御：按 ExecutablePath 杀安装目录里的残留进程
    #（变成孤儿的 node.exe 等）；杀不掉只记日志，bat 里还有第二道。
    # 排除当前 PID：旧 exe 由 bat 的 :precopy 门等它退出，不用在这里
    # 提前杀掉自己（request_restart 还要 1.5 秒后才退出）。
    try:
        _kill_install_dir_processes(install, exclude_pid=os.getpid())
    except Exception:
        pass
    flags = _windows_launch_flags()
    try:
        # bat 第 6 个参数是当前（旧）版本号：回滚重开后 bat 轮询
        # /api/health 等这个版本出现，才算旧版真的回来了；等不到
        # 写 failed/nostart_old。只对从本版起的更新生效（更新跑的
        # 是旧版的 bat，旧版 bat 没有这段核实）。
        # bat 第 7 个参数是重开端口（RELAUNCH_PORT = 8686）：bat 拉起
        # exe 时不带参数，重开的实例永远跑 8686，轮询必须问这个端口。
        # 传更新前的服务端口是错的（v31 的 M-41 机制）：CI 里服务跑
        # 18686、重开的跑 8686，bat 在 18686 上等满 180 秒也等不到，
        # :fail_poll 误报 nostart_old，stuck 永远写不出来。
        subprocess.Popen(
            ["cmd.exe", "/c", str(bat_path), str(staging_dir), exe_name, str(os.getpid()),
             expected_version, backup_name, __version__, str(RELAUNCH_PORT)],
            creationflags=flags,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        _remove_quietly(install / UPDATE_PENDING_FILENAME)
        _UPDATE_APPLYING = False
        # H-2：bat 没拉起来 = 这次更新没换成，状态放回 ready，
        # 用户还能点重试；不能留在 applying 里卡死。
        _set_status(status="ready", message="新版本下好了", error="")
        raise
    _set_status(status="applying", progress=100, message="正在换上新版本…")
    return bat_path
