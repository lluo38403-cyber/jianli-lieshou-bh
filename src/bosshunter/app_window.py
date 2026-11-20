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

import http.server
import json
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
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
# 启动页地址（v28）：窗口先开启动页时记这里；watch 的标签页检测
# 要认启动页标签（见 watch_app_window_close）。
_APP_WINDOW_SPLASH_URL: str | None = None
# 启动失败标记文件名（v29）：run_server / launcher 在 data/ 下写
# .startup-failed.json，启动页服务的 /splash-health 读到它就向页面
# 报 failed，页面不再无限轮询。
STARTUP_FAILURE_FILENAME = ".startup-failed.json"
# 启动失败标记文件的路径（v29）：run_server 在拉启动页前经
# set_splash_failure_marker_path 记在这里，launch_splash_window
# 再把它传给启动页服务。
_SPLASH_FAILURE_MARKER_PATH = None
# launch_splash_window 最近一次是否连窗口都没拉起来（v29）：
# 只有 launch_app_window 抛异常才置 True；走 webbrowser 回落
# 也算拉起来了（窗口已经开了，只是没有句柄）。
_LAST_SPLASH_LAUNCH_FAILED = False
# 启动页当前阶段文案（v30）：run_server 在清理/绑定等关键点上调
# set_splash_stage 更新，启动页 /splash-health 把它带回页面显示，
# 用户能看到现在卡在哪一步，而不是一直只有一句“正在等本地服务起来…”。
_SPLASH_STAGE = "正在等本地服务起来…"


def set_splash_stage(text: str) -> None:
    """Set the stage line the splash page shows (v30). Never raises."""
    global _SPLASH_STAGE
    try:
        if text:
            _SPLASH_STAGE = str(text)
    except Exception:
        pass


def get_splash_stage() -> str:
    """Return the current splash stage line (v30)."""
    return _SPLASH_STAGE


def set_splash_failure_marker_path(path) -> None:
    """Set the startup-failure marker path the splash server should read (v29)."""
    global _SPLASH_FAILURE_MARKER_PATH
    _SPLASH_FAILURE_MARKER_PATH = path


def last_splash_launch_failed() -> bool:
    """True when the last launch_splash_window call failed to open any window (v29)."""
    return _LAST_SPLASH_LAUNCH_FAILED


def read_startup_failure(marker_path) -> str | None:
    """Return the recorded startup-failure message, or None when no failure is recorded.

    只读标记文件里的 message/reason，永不抛异常。标记不存在、
    读不懂、没有 message 都返回 None——调用方按“没有失败”处理。
    """
    if marker_path is None:
        return None
    try:
        data = json.loads(Path(marker_path).read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    message = data.get("message") or data.get("reason") or ""
    text = str(message).strip()
    return text if text else "启动失败"


def get_app_window_splash_url() -> str | None:
    """Return the splash URL the --app window was launched with, if any."""
    return _APP_WINDOW_SPLASH_URL


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


# 启动页服务在面板就绪后多久自停（给页面留出跳转时间，见
# _SplashRequestHandler.do_GET 的 /splash-health 分支）。
_SPLASH_STOP_DELAY_SECONDS = 5.0


def _splash_page_html(panel_url: str) -> bytes:
    """Render the static splash page (v28).

    只写明“正在启动 + 明确状态行”，不依赖任何外部资源。页面用
    同源 fetch 轮询启动页服务自己的 /splash-health（服务在后台帮
    我们问面板 /api/health），通了就 location.replace 跳面板——
    启动页与面板不在同源，直接 fetch 面板会被 CORS 挡住，所以
    不直接问面板。panel_url 只进 JS 字符串（json.dumps 转义）。
    """
    panel_json = json.dumps(panel_url).replace("<", "\\u003c")
    page = (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<title>简历猎手正在启动</title>"
        "<style>body{font-family:sans-serif;display:flex;min-height:100vh;"
        "margin:0;align-items:center;justify-content:center;background:#f4f6f8;"
        "color:#222}main{text-align:center}h1{font-size:20px;margin:0 0 12px}"
        "p{font-size:14px;color:#666;margin:0}</style></head><body><main>"
        "<h1>简历猎手正在启动，请稍等&hellip;</h1>"
        "<p id=\"status\">正在等本地服务起来&hellip;</p></main>"
        "<script>var panelUrl=" + panel_json + ";var tried=0;var fails=0;"
        "function showError(msg){document.getElementById('status').textContent=msg;}"
        "function tick(){tried++;"
        "fetch('/splash-health').then(function(r){return r.json()}).then(function(d){"
        "if(d.ready){location.replace(panelUrl);return}"
        # v29：服务明确死了（启动失败标记）就显示错误、不再无限轮询；
        # 联系不上启动页服务本身（进程已死）连续失败 10 次也一样，
        # 显示明确错误而不是一直转圈。
        "if(d.failed){showError(d.error||'本地服务启动失败，请重新双击「简历猎手」再试一次。');return}"
        # v30：超过 10 秒（tried>20，每 500 ms 一轮）未就绪就显示
        # 已等秒数；没到 10 秒时显示服务端推来的当前阶段（清残留 /
        # 启动服务…），用户能看到卡在哪一步。
        "if(tried>20){showError('启动有点慢，还在努力…（已等 '+Math.round(tried/2)+' 秒）');}"
        "else if(d.stage){document.getElementById('status').textContent=d.stage;}"
        "setTimeout(tick,500)}).catch(function(){fails++;"
        "if(fails>=10){showError('本地服务没有响应，程序可能没有启动成功。请重新双击「简历猎手」再试一次；如果一直这样，请把 data/logs/jianli-lieshou.log 发给客服。');return}"
        "setTimeout(tick,500)})}"
        "tick();</script></body></html>"
    )
    return page.encode("utf-8")


class _SplashRequestHandler(http.server.BaseHTTPRequestHandler):
    """Serve the splash page + a same-origin readiness proxy (v28).

    只认两个路径：/（启动页）和 /splash-health（问面板好了没，
    后台用 is_our_service_alive 问面板 /api/health）。其余 404。
    protocol_version 用 HTTP/1.1 + 显式 Content-Length，避免 Chrome
    --app 里页面一直转圈。只在本机监听，见 start_splash_server。
    """

    panel_url = "http://127.0.0.1:8686"
    # 启动失败标记文件路径（v29）：/splash-health 读到它就向页面报
    # failed（见 do_GET），由 start_splash_server 填。
    failure_marker_path = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # 不往 stderr 刷请求日志
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:
        try:
            path = urllib.parse.urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, _splash_page_html(self.panel_url), "text/html; charset=utf-8")
            elif path == "/splash-health":
                parsed = urllib.parse.urlparse(self.panel_url)
                ready = is_our_service_alive(
                    parsed.hostname or "127.0.0.1",
                    parsed.port or 8686,
                    timeout=1.0,
                )
                if ready and not getattr(self.server, "_splash_stop_scheduled", False):
                    # 服务好了：页面下次轮询就会跳面板；5 秒后自停，
                    # 给页面留出跳转时间——调用方不用抢着停（停早了
                    # 页面还在轮询，会卡在启动页跳不过去）。
                    self.server._splash_stop_scheduled = True
                    timer = threading.Timer(
                        _SPLASH_STOP_DELAY_SECONDS, stop_splash_server, args=(self.server,)
                    )
                    timer.daemon = True
                    timer.start()
                if ready:
                    self._send(200, json.dumps({"ready": True}).encode("utf-8"), "application/json")
                else:
                    # v29：服务没好、且启动失败标记已写（端口被占、
                    # 前端缺失等），直接告诉页面“明确死了”，页面
                    # 显示错误、不再无限轮询。
                    failed_error = None
                    marker_path = getattr(self, "failure_marker_path", None)
                    if marker_path is not None:
                        failed_error = read_startup_failure(marker_path)
                    if failed_error is not None:
                        self._send(
                            200,
                            json.dumps(
                                {"ready": False, "failed": True, "error": failed_error},
                                ensure_ascii=False,
                            ).encode("utf-8"),
                            "application/json",
                        )
                    else:
                        self._send(200, json.dumps({"ready": False, "stage": get_splash_stage()}, ensure_ascii=False).encode("utf-8"), "application/json")
            else:
                self._send(404, b"not found", "text/plain")
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_HEAD = do_GET


def start_splash_server(panel_url: str, host: str = "127.0.0.1", failure_marker_path=None):
    """Start the lightweight splash server on a temp localhost port (v28).

    只用标准库 http.server，不引新依赖。返回 server（带 .splash_url
    属性），失败返回 None、永不抛异常。线程是 daemon：进程退出时
    不会拖住；正常流程由 run_server 在服务就绪后调 stop_splash_server
    干净停掉（见汇报里的 run_server 改动规格）。
    failure_marker_path（v29）：启动失败标记文件路径，/splash-health
    读到它就向页面报 failed。
    """
    try:
        handler = type(
            "SplashHandler",
            (_SplashRequestHandler,),
            {"panel_url": panel_url, "failure_marker_path": failure_marker_path},
        )
        server = http.server.ThreadingHTTPServer((host, 0), handler)
        server.daemon_threads = True
        server.splash_url = f"http://{host}:{server.server_address[1]}/"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server
    except Exception as exc:
        _logger.warning("start_splash_server: 启动页服务起不来：%s", exc)
        return None


def stop_splash_server(server) -> None:
    """Stop a splash server started by start_splash_server. Idempotent, never raises."""
    if server is None:
        return
    try:
        server.shutdown()
    except Exception:
        pass
    try:
        server.server_close()
    except Exception:
        pass


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


def launch_app_window(
    url: str,
    config: dict[str, Any] | None = None,
    *,
    splash_url: str | None = None,
    panel_url: str | None = None,
) -> subprocess.Popen | None:
    """Open url in a standalone --app window; fall back to the default browser.

    v28 启动页：传了 splash_url 就先开启动页（窗口立刻出现），
    panel_url（默认就是 url）才是 watch_app_window_close 要盯的
    面板地址——_APP_WINDOW_URL 记面板地址，启动页地址另记
    _APP_WINDOW_SPLASH_URL。启动页自己会跳面板，标签页检测认
    得启动页标签（见 watch_app_window_close）。

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
    global _APP_WINDOW_STARTED_AT
    global _APP_WINDOW_DEBUG_PORT
    global _APP_WINDOW_URL
    global _APP_WINDOW_SPLASH_URL
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
        if splash_url is not None:
            # v28：先开启动页，窗口立刻出现；面板地址另记（watch 目标）。
            args = [executable, f"--app={splash_url}"]
        if debug_port is not None and profile_dir is not None:
            args = [
                executable,
                f"--user-data-dir={profile_dir}",
                f"--remote-debugging-port={debug_port}",
            ] + args[1:]
        try:
            proc = subprocess.Popen(args, **kwargs)
        except OSError:
            proc = None
        if proc is not None:
            _APP_WINDOW_PROC = proc
            _APP_WINDOW_STARTED_AT = time.monotonic()
            _APP_WINDOW_DEBUG_PORT = debug_port
            _APP_WINDOW_URL = panel_url if panel_url is not None else url
            _APP_WINDOW_SPLASH_URL = splash_url
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
    # v28：有启动页就开启动页（它自己会跳面板）。句柄没有，但
    # URL 还是记下来，watch 的状态与 --app 路径一致。
    _APP_WINDOW_URL = panel_url if panel_url is not None else url
    _APP_WINDOW_SPLASH_URL = splash_url
    import webbrowser

    webbrowser.open(splash_url if splash_url is not None else url)
    return None


def launch_splash_window(
    panel_url: str, config: dict[str, Any] | None = None
) -> tuple[subprocess.Popen | None, object | None]:
    """Start the splash server and open the --app window onto it (v28).

    返回 (proc, splash_server)：启动页服务在面板就绪后 5 秒自停，
    调用方不用抢着停；关窗退出时进程直接退出，服务随之消失。
    启动页服务起不来就直接开面板窗口（旧行为），不挡启动。
    永不抛异常。v29：连窗口都没拉起来（launch_app_window 抛异常）
    时记 last_splash_launch_failed()，调用方（run_server）要走
    可见报错、不要继续静默往下走。
    """
    global _LAST_SPLASH_LAUNCH_FAILED
    _LAST_SPLASH_LAUNCH_FAILED = False
    try:
        server = start_splash_server(panel_url, failure_marker_path=_SPLASH_FAILURE_MARKER_PATH)
    except Exception:
        server = None
    if server is None:
        return launch_app_window(panel_url, config), None
    try:
        proc = launch_app_window(
            panel_url, config, splash_url=server.splash_url, panel_url=panel_url
        )
    except Exception:
        # 窗口都没拉起来：启动页服务也没人看了，直接停掉。
        stop_splash_server(server)
        _LAST_SPLASH_LAUNCH_FAILED = True
        return None, None
    return proc, server


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
    global _APP_WINDOW_STARTED_AT
    global _APP_WINDOW_SPLASH_URL
    proc = _APP_WINDOW_PROC
    _APP_WINDOW_PROC = None
    _APP_WINDOW_STARTED_AT = None
    _APP_WINDOW_SPLASH_URL = None
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


def wait_for_panel_tab(
    panel_url: str,
    debug_port: int | None,
    timeout: float = 10.0,
    interval: float = 0.5,
    splash_url: str | None = None,
    _list_tabs=None,
    _clock=None,
    _sleep=None,
) -> bool | None:
    """Poll a Chrome debug port until our panel (or splash) tab appears (v29).

    True：看到了面板/启动页标签页。False：调试端口应答、但 timeout
    内一直没看到我们的标签页——窗口没开出来（无窗口残留旧服务）。
    None：无法核实（没有调试端口，或端口一直没应答）——调用方
    绝不能把它读成“没有标签页”，只能按旧行为放行。永不抛异常。
    """
    clock = _clock or time.monotonic
    sleep = _sleep or time.sleep
    if debug_port is None and _list_tabs is None:
        return None
    if _list_tabs is not None:
        list_tabs = _list_tabs
    else:
        list_tabs = lambda: _list_debug_page_urls(debug_port)  # noqa: E731
    ever_answered = False
    deadline = clock() + timeout
    while True:
        try:
            urls = list_tabs()
        except Exception:
            urls = None
        if urls is not None:
            ever_answered = True
            for url in urls:
                if url.startswith(panel_url) or (splash_url and url.startswith(splash_url)):
                    return True
        if clock() >= deadline:
            return False if ever_answered else None
        sleep(interval)


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
    _splash_url: str | None = None,
    _service_alive=None,
) -> bool:
    """Watch the held --app window; call on_close() once the user closes it.

    run_server 在守护线程里跑这个函数。语义（v25，v27 补了标签页信号，
    v29 收紧了退出条件）：
    - 先等 _APP_WINDOW_PROC 出现（开窗是异步线程做的），等不到就
      返回 False、不做任何事；
    - 句柄在 grace_seconds（默认 5 秒）内就退出，视为 Chrome 把
      --app 交给了已在跑的实例的假退出：没有标签页检测就直接返回
      False（旧行为）；有检测就改看标签页，面板标签页在已在跑的
      实例里——60 秒内一直没看到面板标签页也放弃，返回 False；
    - 句柄活过 grace 后才退出，视为用户关窗，调一次 on_close()、
      返回 True——但 v29 起还要同时满足：服务应答过 /api/health，
      且（有标签页观测时）面板标签页确实出现过；启动中途死掉的
      进程绝不能靠一个关窗信号就把自己退掉；
    - 句柄还活着但面板标签页已经出现过、现在又没了，也视为关窗：
      带调试端口的 Chrome 关掉最后一个窗口后进程可能还留着，只看
      句柄就看不到这次关窗；端口没应答时不判（那不是“标签页没了”，
      只是暂时问不到）；v29 起同样要求服务应答过 /api/health，
      标签页从未出现过时绝不调 on_close；
    - 更新进行中（updater._UPDATE_APPLYING 置位，apply_update 自己
      会关窗、停 runtime、起 bat）绝不抢跑：直接返回 False。
    _service_alive（测试注入）：无参可调用，返回服务是否应答过
    /api/health；默认按面板地址实时问 /api/health。on_close 里抛
    异常只记日志、不让监视线程崩掉。永不抛异常。
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
        splash_url = _splash_url if _splash_url is not None else _APP_WINDOW_SPLASH_URL
        debug_port = _debug_port if _debug_port is not None else _APP_WINDOW_DEBUG_PORT
        if _list_tabs is not None:
            list_tabs = _list_tabs
        elif panel_url is not None and debug_port is not None:
            list_tabs = lambda: _list_debug_page_urls(debug_port)  # noqa: E731
        else:
            list_tabs = None
        # v29：服务是否应答过 /api/health（sticky，一旦见过就一直算见过）。
        # 默认按面板地址实时问；测试经 _service_alive 注入。
        if _service_alive is not None:
            service_alive = _service_alive
        elif panel_url:
            _parsed = urllib.parse.urlparse(panel_url)
            _host = _parsed.hostname or "127.0.0.1"
            _port = _parsed.port or 8686
            service_alive = lambda: is_our_service_alive(_host, _port, timeout=1.0)  # noqa: E731
        else:
            service_alive = None
        seen_panel = False
        seen_healthy = False

        def _fire(require_tab: bool) -> bool:
            # v29 铁律：只在“标签页确实出现过、后来又消失、且服务
            # 应答过 /api/health”时退出；句柄路径（看不到标签页）
            # 同样要求服务应答过。任一条件不满足都不是真关窗，
            # 直接返回 False、不退出进程。
            if require_tab and not seen_panel:
                return False
            if not seen_healthy:
                return False
            try:
                on_close()
            except Exception as exc:
                _logger.warning("watch_app_window_close: on_close 失败：%s", exc)
            return True

        tab_only = False
        tab_only_deadline: float | None = None
        while True:
            try:
                if _is_update_applying():
                    return False
            except Exception:
                pass
            if service_alive is not None and not seen_healthy:
                try:
                    if service_alive():
                        seen_healthy = True
                except Exception:
                    pass
            if not tab_only:
                try:
                    exited = proc.poll() is not None
                except Exception:
                    return False
                if exited:
                    if clock() - started_at >= grace_seconds:
                        # 句柄活过 grace 才退出：有标签页观测时还要求
                        # 标签页出现过；无论如何都要求服务应答过。
                        if _fire(require_tab=list_tabs is not None):
                            return True
                        return False
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
                    # v28：启动页标签也算“我们的标签页”——启动页阶段
                    # 关窗是真关窗，不是交接假退出：启动页出现过、后
                    # 来没了，就触发 on_close（tab-only 路径下 grace
                    # 内退出的句柄也不会把它吞掉）。
                    def _is_our_tab(url: str) -> bool:
                        if panel_url and url.startswith(panel_url):
                            return True
                        return bool(splash_url and url.startswith(splash_url))

                    if any(_is_our_tab(url) for url in urls):
                        seen_panel = True
                    elif seen_panel:
                        # v29：标签页没了、但服务从未应答过 /api/health
                        # （启动中途死掉）时不退出——那不是用户关窗，
                        # 是启动失败的残留信号。
                        if _fire(require_tab=True):
                            return True
                        return False
            sleep(poll_interval)
    except Exception as exc:
        _logger.warning("watch_app_window_close: 监视失败：%s", exc)
        return False
