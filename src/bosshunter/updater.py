"""One-click self-update for the installed exe (automatic update).

Flow: the panel asks the cloud relay for the newest published version
(GET {relay_url}/api/version, X-App-Token). When a newer version exists,
the user can press one button: the backend downloads the release zip
from the relay (GET {relay_url}/download/latest), verifies its sha256,
extracts it to a staging directory, backs up the current exe payload,
writes update.bat next to the exe, then exits; the batch file waits for
the process to exit, copies staging over the install directory, relaunches
the exe and cleans up.

Safety rules (do not weaken them):
- The download URL only ever comes from the configured relay_url; no
  caller-supplied URL is accepted anywhere.
- A sha256 mismatch aborts before anything on disk is touched.
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
EXE_NAME = "简历猎手.exe"

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
    root is the directory that holds the exe. Falls back to staging
    itself when the exe is already at the top level.
    """
    staging = Path(staging_dir)
    if (staging / EXE_NAME).is_file():
        return staging
    for child in sorted(staging.iterdir()):
        if child.is_dir() and (child / EXE_NAME).is_file():
            return child
    return staging


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
        if src.name in {"update.bat", "update.log"}:
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


def overlay_staging(install_dir: str | Path, staging_dir: str | Path) -> None:
    """Copy a staged payload over the install dir.

    Staging files replace their counterparts; config.yaml and data/
    are never touched; install files absent from staging are left
    alone. This is the policy the Windows batch file mirrors with
    robocopy /XD data /XF config.yaml — keep the two in sync.
    """
    install = Path(install_dir)
    staging = Path(staging_dir)
    for src in staging.iterdir():
        if src.name in PRESERVED_TOP_LEVEL:
            continue
        dst = install / src.name
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)


# ─── Windows batch file ─────────────────────────────────────

def build_update_script() -> str:
    """Return the update.bat contents.

    The batch file takes three arguments at launch: staging dir, exe
    file name, pid of the process to wait for. It waits for the given
    pid to exit (up to 120s), then copies staging over the install
    directory (never touching config.yaml or data/), relaunches the
    exe, deletes staging and deletes itself. Every step is logged to
    update.log next to the exe so a failed update can be diagnosed
    by hand.
    """
    return (
        "@echo off\r\n"
        "rem 简历猎手自动更新：等主程序退出后，把新版本换上去。\r\n"
        "rem 换的过程中不碰 config.yaml 和 data/（你的设置和数据都在那里面）。\r\n"
        "setlocal\r\n"
        f'set "STAGING=%~1"\r\n'
        f'set "EXE_NAME=%~2"\r\n'
        f'set "PID=%~3"\r\n'
        'set "INSTALL_DIR=%~dp0"\r\n'
        'set "LOG=%INSTALL_DIR%update.log"\r\n'
        'echo [%date% %time%] 开始更新，正在等主程序退出... > "%LOG%"\r\n'
        "set /a WAITED=0\r\n"
        ":waitloop\r\n"
        'tasklist /FI "PID eq %PID%" 2>nul | findstr /C:"%PID%" >nul\r\n'
        "if %errorlevel% neq 0 goto :copy\r\n"
        "set /a WAITED+=1\r\n"
        "if %WAITED% geq 120 (\r\n"
        '  echo [%date% %time%] 等了 120 秒主程序还没退出，这次先不换，旧版本还能用。 >> "%LOG%"\r\n'
        "  goto :eof\r\n"
        ")\r\n"
        "timeout /t 1 /nobreak >nul\r\n"
        "goto :waitloop\r\n"
        ":copy\r\n"
        'echo [%date% %time%] 主程序已退出，正在换上新版本... >> "%LOG%"\r\n'
        'robocopy "%STAGING%" "%INSTALL_DIR%" /E /IS /R:2 /W:2 /XD data /XF config.yaml >> "%LOG%" 2>&1\r\n'
        "if errorlevel 8 (\r\n"
        '  echo [%date% %time%] 复制失败，旧版本还在，备份在 update-backup 开头的文件夹里。 >> "%LOG%"\r\n'
        "  goto :eof\r\n"
        ")\r\n"
        'echo [%date% %time%] 换好了，正在重新打开... >> "%LOG%"\r\n'
        'rmdir /s /q "%STAGING%"\r\n'
        'start "" "%INSTALL_DIR%%EXE_NAME%"\r\n'
        '(goto) 2>nul & del "%~f0"\r\n'
    )


def write_update_script(install_dir: str | Path) -> Path:
    """Write update.bat into the install directory. Returns its path."""
    bat_path = Path(install_dir) / "update.bat"
    bat_path.write_text(build_update_script(), encoding="utf-8")
    return bat_path


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
        payload_root = find_payload_root(staging)
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


def apply_update(install_dir: str | Path, exe_name: str) -> Path:
    """Prepare the swap: backup, write update.bat, launch it detached.

    Only valid when a release is staged (status "ready") and only on
    Windows. The caller is responsible for exiting this process right
    after (see request_restart). Returns the batch file path.
    """
    if os.name != "nt":
        raise UpdateError("一键更新只在正式安装的版本里能用。")
    with _STATE_LOCK:
        status = _STATE.get("status")
        staging_dir = _STATE.get("staging_dir")
    if status != "ready" or not staging_dir:
        raise UpdateError("新版本还没下好，先等它下载完。")
    install = Path(install_dir)
    if not (install / exe_name).is_file():
        raise UpdateError("找不到现在用的程序文件，这次先不换。")
    prune_old_backups(install)
    backup_dir = install / f"{UPDATE_BACKUP_PREFIX}{__version__}"
    create_backup(install, backup_dir)
    bat_path = write_update_script(install)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(
        ["cmd.exe", "/c", str(bat_path), str(staging_dir), exe_name, str(os.getpid())],
        creationflags=flags,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _set_status(status="applying", progress=100, message="正在换上新版本…")
    return bat_path
