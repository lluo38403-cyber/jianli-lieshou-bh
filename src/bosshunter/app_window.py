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

Closing the app window exits the whole program (v25): the service
used to keep running in the background after the window closed, which
left the runtime node and the automation Chrome orphaned in Task
Manager. Now run_server watches the held --app handle and, once it
has lived past a short grace period and then exits, shuts the runtime
and our automation Chrome down and exits the process. Limitation:
when Chrome hands the --app URL to an already-running instance, the
Popen handle exits at once and there is nothing to watch -- the
window close is undetectable and the service keeps its old behavior
(stays running). See watch_app_window_close.
"""

import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any

_logger = logging.getLogger(__name__)

# CREATE_NO_WINDOW 的真实值；Linux/macOS 上 subprocess 没有这个属性，
# fallback 写死真实值，传了也会被忽略，测试还能断言到。
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

_APP_WINDOW_PROC: subprocess.Popen | None = None
# --app 窗口拉起时的单调时钟（watch_app_window_close 用它算窗口活了
# 多久，过滤 Chrome 把 --app 交给已在跑实例时的“立刻退出”假信号）。
_APP_WINDOW_STARTED_AT: float | None = None


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


def wait_for_service_ready(host: str = "127.0.0.1", port: int = 8686, timeout: float = 15.0, interval: float = 0.5) -> bool:
    """Poll until our own panel answers /api/health, or the deadline passes.

    冷启动慢的机器上服务可能 1 秒还起不来：以前开窗线程只睡 1 秒就开
    --app 窗口，服务还没监听就打开一张连不上的空白页，而且开出来就
    一直空白、不会自动重试。先等就绪再开；15 秒还没好也返回 False，
    调用方照常开窗（和以前一样，用户刷新就能进），绝不因为等不到就
    让用户连窗口都没有。
    """
    deadline = time.monotonic() + timeout
    while True:
        if is_our_service_alive(host, port):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def find_app_browser(config: dict[str, Any] | None = None) -> str | None:
    """Return the selected browser executable path for --app mode, or None.

    走用户在“我的设置”里选的浏览器（自动 / Chrome / Edge / 手动路径，
    见 resolve_browser_executable）；所选浏览器找不到时按 Chrome→Edge
    回落，仍找不到返回 None，调用方降级走默认浏览器。主窗口与登录
    浏览器仍是两个独立窗口（见模块 docstring），这里只决定用哪个
    可执行文件开 --app 窗口。
    """
    try:
        from bosshunter.browser.diagnostics import resolve_browser_executable

        return resolve_browser_executable(config).get("executable")
    except Exception:
        return None


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
            global _APP_WINDOW_STARTED_AT
            _APP_WINDOW_PROC = proc
            _APP_WINDOW_STARTED_AT = time.monotonic()
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
    global _APP_WINDOW_STARTED_AT
    _APP_WINDOW_STARTED_AT = None
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


def watch_app_window_close(
    on_close,
    grace_seconds: float = 5.0,
    poll_interval: float = 0.5,
    wait_for_proc_seconds: float = 30.0,
    _get_proc=None,
    _started_at: float | None = None,
    _is_update_applying=None,
    _clock=None,
    _sleep=None,
) -> bool:
    """Watch the held --app window; call on_close() once the user closes it.

    run_server 在守护线程里跑这个函数。语义（v25）：
    - 先等 _APP_WINDOW_PROC 出现（开窗是异步线程做的），等不到就
      返回 False、不做任何事；
    - 句柄在 grace_seconds（默认 5 秒）内就退出，视为 Chrome 把
      --app 交给了已在跑的实例的假退出，直接返回 False、不触发关停；
    - 句柄活过 grace 后才退出，视为用户关窗，调一次 on_close()、
      返回 True；
    - 更新进行中（updater._UPDATE_APPLYING 置位，apply_update 自己
      会关窗、停 runtime、起 bat）绝不抢跑：直接返回 False。
    局限：Chrome 把 --app 交给已在跑实例时根本没有可监视的句柄，
    这种情况下关窗检测不到、服务保持旧行为（不停），不要假装修好了。
    on_close 里抛异常只记日志、不让监视线程崩掉。永不抛异常。
    """
    clock = _clock or time.monotonic
    sleep = _sleep or time.sleep
    get_proc = _get_proc or get_app_window_process
    if _is_update_applying is None:
        try:
            from bosshunter.updater import is_update_applying as _is_update_applying
        except Exception:
            _is_update_applying = lambda: False  # noqa: E731
    try:
        deadline = clock() + wait_for_proc_seconds
        proc = None
        while clock() < deadline:
            proc = get_proc()
            if proc is not None:
                break
            sleep(poll_interval)
        if proc is None:
            return False
        started_at = _started_at if _started_at is not None else _APP_WINDOW_STARTED_AT
        if started_at is None:
            # 句柄有了、启动时间没记上（不应发生）：从第一次看到句柄
            # 起算，宁可漏一次真关窗也不误杀刚交接的假退出。
            started_at = clock()
        while True:
            try:
                if _is_update_applying():
                    return False
            except Exception:
                pass
            try:
                exited = proc.poll() is not None
            except Exception:
                return False
            if exited:
                if clock() - started_at >= grace_seconds:
                    try:
                        on_close()
                    except Exception as exc:
                        _logger.warning("watch_app_window_close: on_close 失败：%s", exc)
                    return True
                return False
            sleep(poll_interval)
    except Exception as exc:
        _logger.warning("watch_app_window_close: 监视失败：%s", exc)
        return False
