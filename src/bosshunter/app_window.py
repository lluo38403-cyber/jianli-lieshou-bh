"""Standalone app window: open the panel in a browser --app window.

The panel is a local web page; opening it in a normal browser tab works
but it competes with the user's tabs. An --app window (Edge/Chrome
--app=<url>) looks like a desktop app: no address bar, no bookmarks bar,
no extra tabs. The window title comes from the page <title> (简历猎手);
the window icon is the browser's own -- --app mode takes the browser
executable's icon and there is no light way to override it, so we accept
browser icon + "简历猎手" title instead of a heavy workaround.

Separation from the login browser matters: the login/apply browser is
launched by launch_debuggable_browser with a dedicated profile
(~/.jianli-lieshou/browser-profile) and a debug port. The app window
uses NO --user-data-dir and NO --remote-debugging-port, so it runs in
the browser's default profile -- a different process from the login
browser. Closing one never closes the other, and the update path only
ever terminates the Popen handle held here, never the login browser.

Closing the app window never exits the service: the web server keeps
running in the background (same as closing a tab today). Re-running the
exe just opens another app window onto the same service.
"""

import json
import os
import subprocess
import urllib.error
import urllib.request
from typing import Any

# CREATE_NO_WINDOW 的真实值；Linux/macOS 上 subprocess 没有这个属性，
# fallback 写死真实值，传了也会被忽略，测试还能断言到。
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

_APP_WINDOW_PROC: subprocess.Popen | None = None


def is_our_service_alive(host: str = "127.0.0.1", port: int = 8686, timeout: float = 2.0) -> bool:
    """Return True when our own panel service already answers on host:port.

    Used at startup: if the previous run's service is still there, just
    open a window onto it instead of starting a second service (which
    would fail to bind the port). Only our own /api/health counts --
    a foreign program squatting on the port must not be mistaken for us.
    Never raises.
    """
    try:
        req = urllib.request.Request(f"http://{host}:{port}/api/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except Exception:
        return False
    return (
        isinstance(data, dict)
        and data.get("status") == "ok"
        and isinstance(data.get("version"), str)
        and bool(data.get("version"))
    )


def find_app_browser(config: dict[str, Any] | None = None) -> str | None:
    """Return a Chrome/Edge executable path for --app mode, or None.

    Chrome first, Edge second -- same order as the login browser. The
    import is function-level: diagnostics imports browser config, and
    module-level imports here would risk a cycle.
    """
    try:
        from bosshunter.browser.diagnostics import discover_browsers

        found = discover_browsers(config)
    except Exception:
        return None
    return found.get("chrome") or found.get("edge")


def launch_app_window(url: str, config: dict[str, Any] | None = None) -> subprocess.Popen | None:
    """Open url in a standalone --app window; fall back to the default browser.

    Returns the Popen handle when an --app window was launched (the exe
    holds it so the update path can close exactly this window later),
    or None when it fell back to webbrowser.open. Never raises: in any
    environment the user still reaches the panel.
    """
    global _APP_WINDOW_PROC
    executable = find_app_browser(config)
    if executable:
        # creationflags 只在 Windows 上传：CPython 在非 Windows 上收到
        # 非零 creationflags 直接抛 ValueError（之前无条件传，verify
        # 全量跑出 16 个失败，全是这一句）。
        kwargs: dict[str, Any] = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = _CREATE_NO_WINDOW
        try:
            proc = subprocess.Popen([executable, f"--app={url}"], **kwargs)
        except OSError:
            proc = None
        if proc is not None:
            _APP_WINDOW_PROC = proc
            return proc
    # 没找到 Edge/Chrome，或拉起失败：走默认浏览器，总能进界面。
    import webbrowser

    webbrowser.open(url)
    return None


def close_app_window(timeout: float = 3.0) -> bool:
    """Terminate exactly the held --app window process, nothing else.

    Returns True when a live window process was terminated. The login
    browser is a different Popen handle (different profile, debug port)
    and is never touched here. A window process that already exited
    (e.g. Chrome handed the --app URL to an existing instance) is left
    alone. Closing the window never deletes the browser profile.
    """
    global _APP_WINDOW_PROC
    proc = _APP_WINDOW_PROC
    _APP_WINDOW_PROC = None
    if proc is None or proc.poll() is not None:
        return False
    try:
        proc.terminate()
    except Exception:
        return False
    try:
        proc.wait(timeout=timeout)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    return True


def get_app_window_process() -> subprocess.Popen | None:
    """Return the held --app window handle (None when there is none)."""
    return _APP_WINDOW_PROC
