"""Standalone app window: open the panel in a browser --app window.

The panel is a local web page; opening it in a normal browser tab works
but it competes with the user's tabs. An --app window (Edge/Chrome
--app=<url>) looks like a desktop app: no address bar, no bookmarks bar,
no extra tabs. The window title comes from the page <title> (简历猎手);
the window icon is the browser's own -- --app mode takes the browser
executable's icon and there is no light way to override it, so we accept
browser icon + "简历猎手" title instead of a heavy workaround.

One instance for the panel and the work browser (v27): the --app
window runs in the SAME Chrome instance as the login/apply browser --
the dedicated profile (~/.jianli-lieshou/browser-profile) and the same
debug port (pick_chrome_debug_port: an already-listening port wins,
otherwise the first free configured port). One taskbar icon, and the
panel and the work tabs share login state, so the panel no longer
reports "浏览器没连上" while BOSS is logged in. Before v27 the --app
window used NO --user-data-dir and NO --remote-debugging-port, so it
ran in the browser's default profile -- a different process from the
login browser, two taskbar icons, desynced state.

Closing the app window exits the whole program (v25): the service
used to keep running in the background after the window closed, which
left the runtime node and the automation Chrome orphaned in Task
Manager. Now run_server watches the held --app handle and, once it
has lived past a short grace period and then exits, shuts the runtime
and our automation Chrome down and exits the process. Two wrinkles
with one shared instance (v27):
- A Chrome launched with --remote-debugging-port can stay alive after
  its last window closes, so the held handle may never exit -- the
  watcher ALSO watches the panel tab itself over the debug port
  (/json/list): once the panel tab has been seen and then disappears,
  that is a close too.
- When Chrome hands the --app URL to an already-running instance, the
  Popen handle exits at once; the watcher then switches to tab-only
  mode instead of giving up (the panel tab lives in that instance).
  If the panel tab never shows up there within 60 seconds, it gives up.
  Closing the window never deletes the browser profile.
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
# --app 窗口用的调试端口与面板地址（watch_app_window_close 的标签页
# 检测用：带调试端口的 Chrome 关掉最后一个窗口后进程可能还留着，
# 只看句柄就看不到关窗；面板标签页没了才是真关窗）。
_APP_WINDOW_DEBUG_PORT: int | None = None
_APP_WINDOW_URL: str | None = None


def get_app_window_debug_port() -> int | None:
    """Return the debug port the --app window was launched with, if any."""
    return _APP_WINDOW_DEBUG_PORT


def get_app_window_url() -> str | None:
    """Return the panel URL the --app window was launched with, if any."""
    return _APP_WINDOW_URL


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
    回落，仍找不到返回 None，调用方降级走默认浏览器。v27 起主窗口
    与工作浏览器跑在同一个实例里（见模块 docstring），这里只决定
    用哪个可执行文件开 --app 窗口。
    """
    try:
        from bosshunter.browser.diagnostics import resolve_browser_executable

        return resolve_browser_executable(config).get("executable")
    except Exception:
        return None


def launch_app_window(url: str, config: dict[str, Any] | None = None) -> subprocess.Popen | None:
    """Open url in a standalone --app window; fall back to the default browser.

    v27：--app 窗口与工作浏览器跑在同一个 Chrome 实例里——同一个
    专用配置目录（~/.jianli-lieshou/browser-profile）+ 同一个调试
    端口（pick_chrome_debug_port 的 pick：已在监听的端口优先复用，
    否则第一个空闲端口）。以前 --app 不带 --user-data-dir /
    --remote-debugging-port，走默认配置，是另一个进程：任务栏出现
    两个 Chrome 图标，主界面与工作浏览器登录态还不同步。
    这次新拉起的实例会记进 launched-browser state（v25 的退出清理
    按这条记录停它）；端口已在监听时是交接给已在跑的实例，不记——
    那个实例不是这次拉的，Popen 的那个进程也立刻就退了。
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
        # 共享 pick：端口已在监听说明实例已在跑，--app 交接给它；
        # 没在监听就由这次拉起的新实例占这个端口。任何一步失败都
        # 回落到不带 profile/端口的旧行为，总能开出窗口。
        debug_port: int | None = None
        profile_dir = None
        port_listening = False
        try:
            from bosshunter.browser.runtime import (
                _chrome_debug_version,
                get_browser_profile_dir,
                pick_chrome_debug_port,
            )

            debug_port = pick_chrome_debug_port(config)
            if debug_port is not None:
                profile_dir = get_browser_profile_dir()
                try:
                    profile_dir.mkdir(parents=True, exist_ok=True)
                except OSError:
                    profile_dir = None
                    debug_port = None
                else:
                    port_listening = _chrome_debug_version(debug_port) is not None
        except Exception:
            debug_port = None
            profile_dir = None
        args = [executable, f"--app={url}"]
        if debug_port is not None and profile_dir is not None:
            args = [
                executable,
                f"--user-data-dir={profile_dir}",
                f"--remote-debugging-port={debug_port}",
                f"--app={url}",
            ]
        try:
            proc = subprocess.Popen(args, **kwargs)
        except OSError:
            proc = None
        if proc is not None:
            global _APP_WINDOW_STARTED_AT
            global _APP_WINDOW_DEBUG_PORT
            global _APP_WINDOW_URL
            _APP_WINDOW_PROC = proc
            _APP_WINDOW_STARTED_AT = time.monotonic()
            _APP_WINDOW_DEBUG_PORT = debug_port
            _APP_WINDOW_URL = url
            if debug_port is not None and profile_dir is not None and not port_listening:
                # 这次新拉的实例：记下来，v25 的退出清理按它停 Chrome。
                # 交接的不记——那是已在跑实例的进程，不是这次拉的。
                try:
                    from bosshunter.browser.runtime import _record_launched_browser

                    _record_launched_browser(proc, profile_dir, debug_port, executable)
                except Exception:
                    pass
            return proc
    # 没找到 Edge/Chrome，或拉起失败：走默认浏览器，总能进界面。
    import webbrowser

    webbrowser.open(url)
    return None


def close_app_window(timeout: float = 3.0) -> bool:
    """Terminate exactly the held --app window process, nothing else.

    Returns True when a live window process was terminated. v27：主窗口
    与工作浏览器共用一个 Chrome 实例，held 句柄是浏览器主进程时
    （--app 窗口拉起了这个实例）terminate 会连工作标签页一起关掉——
    调用的地方是更新换版（apply_update 关窗后本来就要停 runtime、
    停自己拉的 Chrome），不丢东西。Chrome 把 --app 交给已在跑实例
    时句柄早已退出，直接放过。关窗从不删浏览器配置。
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


def _list_debug_page_urls(port: int, timeout: float = 1.5) -> list[str] | None:
    """Return the URLs of page targets on a Chrome debug port, or None.

    None 表示端口没应答（浏览器没起、端口不是调试端口）——调用方
    绝不能把它读成“没有面板标签页”。返回的空列表才是真的“一个
    页面都没有”。永不抛异常。
    """
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/json/list", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    return [
        str(item.get("url") or "")
        for item in data
        if isinstance(item, dict) and item.get("type") == "page"
    ]


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
    _panel_url: str | None = None,
    _debug_port: int | None = None,
    _list_tabs=None,
    _tab_watch_seconds: float = 60.0,
) -> bool:
    """Watch the held --app window; call on_close() once the user closes it.

    run_server 在守护线程里跑这个函数。语义（v25，v27 补了标签页信号）：
    - 先等 _APP_WINDOW_PROC 出现（开窗是异步线程做的），等不到就
      返回 False、不做任何事；
    - 句柄在 grace_seconds（默认 5 秒）内就退出，视为 Chrome 把
      --app 交给了已在跑的实例的假退出：没有标签页检测就直接返回
      False（旧行为）；有检测就改看标签页，面板标签页在已在跑的
      实例里——60 秒内一直没看到面板标签页也放弃，返回 False；
    - 句柄活过 grace 后才退出，视为用户关窗，调一次 on_close()、
      返回 True；
    - 句柄还活着但面板标签页已经出现过、现在又没了，也视为关窗：
      带调试端口的 Chrome 关掉最后一个窗口后进程可能还留着，只看
      句柄就看不到这次关窗；端口没应答时不判（那不是“标签页没了”，
      只是暂时问不到）；
    - 更新进行中（updater._UPDATE_APPLYING 置位，apply_update 自己
      会关窗、停 runtime、起 bat）绝不抢跑：直接返回 False。
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
        panel_url = _panel_url if _panel_url is not None else _APP_WINDOW_URL
        debug_port = _debug_port if _debug_port is not None else _APP_WINDOW_DEBUG_PORT
        if _list_tabs is not None:
            list_tabs = _list_tabs
        elif panel_url is not None and debug_port is not None:
            list_tabs = lambda: _list_debug_page_urls(debug_port)  # noqa: E731
        else:
            list_tabs = None
        seen_panel = False
        tab_only = False
        tab_only_deadline: float | None = None
        while True:
            try:
                if _is_update_applying():
                    return False
            except Exception:
                pass
            if not tab_only:
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
                    # grace 内的假退出：交接给已在跑实例。没检测就返回；
                    # 有检测就改看标签页，60 秒没看到面板标签页也放弃。
                    # 一旦进了 tab-only 就不再回头看句柄——否则同一个
                    # 已退的句柄在 5 秒后会被重算成“活过 grace 的真关窗”。
                    if list_tabs is None:
                        return False
                    tab_only = True
                    tab_only_deadline = clock() + _tab_watch_seconds
            if list_tabs is not None:
                if tab_only and not seen_panel and tab_only_deadline is not None and clock() >= tab_only_deadline:
                    return False
                try:
                    urls = list_tabs()
                except Exception:
                    urls = None
                if urls is not None:
                    if any(url.startswith(panel_url) for url in urls) if panel_url else False:
                        seen_panel = True
                    elif seen_panel:
                        try:
                            on_close()
                        except Exception as exc:
                            _logger.warning("watch_app_window_close: on_close 失败：%s", exc)
                        return True
            sleep(poll_interval)
    except Exception as exc:
        _logger.warning("watch_app_window_close: 监视失败：%s", exc)
        return False
