"""独立应用窗口（--app）：拉起、降级、关闭、端口复用、与登录浏览器分离。"""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import bosshunter.app_window as app_window


def _config(**overrides):
    browser = {
        "runtime": "builtin",
        "auto_start_proxy": True,
        "proxy_host": "127.0.0.1",
        "proxy_port": 3456,
        "chrome_ports": [9222, 9229, 9333],
        "auto_launch_browser": True,
    }
    browser.update(overrides)
    return {"browser": browser}


class LaunchAppWindowTests(unittest.TestCase):
    def setUp(self):
        app_window._APP_WINDOW_PROC = None
        app_window._APP_WINDOW_STARTED_AT = None
        app_window._APP_WINDOW_DEBUG_PORT = None
        app_window._APP_WINDOW_URL = None

    def tearDown(self):
        app_window._APP_WINDOW_PROC = None
        app_window._APP_WINDOW_STARTED_AT = None
        app_window._APP_WINDOW_DEBUG_PORT = None
        app_window._APP_WINDOW_URL = None

    @patch("os.name", "nt")
    @patch("bosshunter.app_window.find_app_browser", return_value="C:/Chrome/chrome.exe")
    @patch("bosshunter.app_window.subprocess.Popen")
    @patch("bosshunter.browser.runtime.pick_chrome_debug_port", return_value=9222)
    @patch("bosshunter.browser.runtime._chrome_debug_version", return_value=None)
    @patch("bosshunter.browser.runtime._record_launched_browser")
    def test_launches_chrome_app_mode_without_console_window(self, record, _debug_version, _pick, popen, _find):
        proc = Mock()
        popen.return_value = proc
        profile_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(profile_tmp.cleanup)
        # os.name 在这里被扮成 nt：pathlib.Path 会变成 WindowsPath，
        # 在 Linux 上建不出来；直接用 PosixPath。
        from pathlib import PosixPath

        profile = PosixPath(profile_tmp.name) / "profile"

        with patch("bosshunter.browser.runtime.get_browser_profile_dir", return_value=profile):
            result = app_window.launch_app_window("http://127.0.0.1:8686")

        self.assertIs(result, proc)
        popen.assert_called_once()
        argv = popen.call_args.args[0]
        # v27：--app 窗口与工作浏览器同一个实例——专用 profile + 调试端口。
        self.assertEqual(argv, [
            "C:/Chrome/chrome.exe",
            f"--user-data-dir={profile}",
            "--remote-debugging-port=9222",
            "--app=http://127.0.0.1:8686",
        ])
        flags = popen.call_args.kwargs.get("creationflags", 0)
        self.assertTrue(flags & 0x08000000, "CREATE_NO_WINDOW 必须在启动标志里")
        self.assertIs(app_window.get_app_window_process(), proc)
        self.assertEqual(app_window.get_app_window_debug_port(), 9222)
        self.assertEqual(app_window.get_app_window_url(), "http://127.0.0.1:8686")
        # 这次新拉的实例：记进 launched-browser state，退出清理按它停 Chrome。
        record.assert_called_once_with(proc, profile, 9222, "C:/Chrome/chrome.exe")

    @patch("bosshunter.app_window.find_app_browser", return_value=None)
    @patch("webbrowser.open")
    def test_falls_back_to_default_browser_when_no_browser_found(self, open_browser, _find):
        result = app_window.launch_app_window("http://127.0.0.1:8686")

        self.assertIsNone(result)
        open_browser.assert_called_once_with("http://127.0.0.1:8686")

    @patch("bosshunter.app_window.find_app_browser", return_value="C:/Chrome/chrome.exe")
    @patch("bosshunter.app_window.subprocess.Popen", side_effect=OSError("chrome 炸了"))
    @patch("bosshunter.browser.runtime.pick_chrome_debug_port", return_value=None)
    @patch("webbrowser.open")
    def test_falls_back_to_default_browser_when_app_launch_fails(self, open_browser, _pick, _popen, _find):
        result = app_window.launch_app_window("http://127.0.0.1:8686")

        self.assertIsNone(result)
        open_browser.assert_called_once_with("http://127.0.0.1:8686")

    @patch("bosshunter.app_window.find_app_browser", return_value="C:/Edge/msedge.exe")
    @patch("bosshunter.app_window.subprocess.Popen")
    @patch("bosshunter.browser.runtime.pick_chrome_debug_port", return_value=None)
    def test_uses_edge_when_chrome_missing(self, _pick, popen, _find):
        app_window.launch_app_window("http://127.0.0.1:8686")

        self.assertEqual(popen.call_args.args[0][0], "C:/Edge/msedge.exe")


class CloseAppWindowTests(unittest.TestCase):
    def setUp(self):
        app_window._APP_WINDOW_PROC = None

    def tearDown(self):
        app_window._APP_WINDOW_PROC = None

    def test_terminates_only_the_held_handle(self):
        proc = Mock()
        proc.poll.return_value = None
        app_window._APP_WINDOW_PROC = proc

        self.assertTrue(app_window.close_app_window())
        proc.terminate.assert_called_once()
        proc.wait.assert_called_once()
        # 句柄已清掉，再关一次什么都不做。
        self.assertFalse(app_window.close_app_window())
        proc.terminate.assert_called_once()

    def test_skips_process_that_already_exited(self):
        # Chrome 把 --app 交给已在跑的实例时，Popen 那个进程立刻就退了：
        # 句柄还在但进程已死，不能 terminate 一个死进程。
        proc = Mock()
        proc.poll.return_value = 0
        app_window._APP_WINDOW_PROC = proc

        self.assertFalse(app_window.close_app_window())
        proc.terminate.assert_not_called()

    def test_close_without_any_window_is_a_noop(self):
        self.assertFalse(app_window.close_app_window())


class ServiceAliveTests(unittest.TestCase):
    @patch("bosshunter.app_window.urllib.request.urlopen")
    def test_alive_when_health_reports_our_version(self, urlopen):
        resp = Mock()
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)
        resp.read.return_value = b'{"status": "ok", "version": "1.1.5"}'
        urlopen.return_value = resp

        self.assertTrue(app_window.is_our_service_alive("127.0.0.1", 8686))

    @patch("bosshunter.app_window.urllib.request.urlopen")
    def test_not_alive_when_health_is_not_ours(self, urlopen):
        resp = Mock()
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)
        resp.read.return_value = b'{"status": "ok"}'
        urlopen.return_value = resp

        self.assertFalse(app_window.is_our_service_alive("127.0.0.1", 8686))

    @patch("bosshunter.app_window.urllib.request.urlopen", side_effect=OSError("没人听"))
    def test_not_alive_when_port_is_closed(self, _urlopen):
        self.assertFalse(app_window.is_our_service_alive("127.0.0.1", 8686))


class RunServerReuseTests(unittest.TestCase):
    def test_reuses_existing_service_instead_of_starting_another(self):
        from bosshunter.web import server

        with (
            patch("bosshunter.app_window.is_our_service_alive", return_value=True) as alive,
            patch("bosshunter.app_window.launch_app_window") as launch,
            patch.object(server, "app") as app,
        ):
            server.run_server(host="127.0.0.1", port=8686, open_browser=True)

        alive.assert_called_once_with("127.0.0.1", 8686)
        launch.assert_called_once_with("http://127.0.0.1:8686")
        app.run.assert_not_called()

    def test_reuse_without_open_browser_starts_nothing(self):
        from bosshunter.web import server

        with (
            patch("bosshunter.app_window.is_our_service_alive", return_value=True) as alive,
            patch("bosshunter.app_window.launch_app_window") as launch,
            patch.object(server, "app") as app,
        ):
            server.run_server(host="127.0.0.1", port=8686, open_browser=False)

        alive.assert_called_once_with("127.0.0.1", 8686)
        launch.assert_not_called()
        app.run.assert_not_called()

    def test_run_server_cleans_up_stale_update_processes_at_startup(self):
        # 旧版挂起的更新进程要在 reconcile 之前清掉（reconcile 在启动
        # 3 秒后的后台线程里跑，残留的 pending 会被它当成一次更新失败）。
        # v28：清理改到守护线程里跑（不挡首屏开窗），这里等线程跑完再断言。
        from bosshunter.web import server

        with (
            patch("bosshunter.updater.cleanup_stale_update_processes") as cleanup,
            patch("bosshunter.app_window.is_our_service_alive", return_value=True),
            patch("bosshunter.app_window.launch_app_window"),
            patch.object(server, "app"),
        ):
            server.run_server(host="127.0.0.1", port=8686, open_browser=True)
            deadline = time.monotonic() + 5.0
            while not cleanup.called and time.monotonic() < deadline:
                time.sleep(0.05)

        cleanup.assert_called_once_with()


class AppWindowLoginSharingTests(unittest.TestCase):
    def setUp(self):
        app_window._APP_WINDOW_PROC = None
        app_window._APP_WINDOW_STARTED_AT = None
        app_window._APP_WINDOW_DEBUG_PORT = None
        app_window._APP_WINDOW_URL = None

    def tearDown(self):
        app_window._APP_WINDOW_PROC = None
        app_window._APP_WINDOW_STARTED_AT = None
        app_window._APP_WINDOW_DEBUG_PORT = None
        app_window._APP_WINDOW_URL = None

    def test_app_window_and_login_browser_share_one_instance(self):
        # v27：主窗口与工作浏览器同一个实例——--app 窗口先拉起专用
        # profile + 调试端口的实例并记下来，登录浏览器看到端口已在
        # 监听就直接复用，不再拉第二个 Chrome（任务栏只有一个图标，
        # 登录态也互通）。旧的“严格分离”契约已废弃。
        profile_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(profile_tmp.cleanup)
        app_proc = Mock()
        login_proc = Mock()
        app_proc.poll.return_value = None
        login_proc.poll.return_value = None
        with (
            patch("bosshunter.app_window.find_app_browser",
                  return_value="C:/Chrome/chrome.exe"),
            patch("subprocess.Popen", side_effect=[app_proc, login_proc]) as popen,
            patch("bosshunter.browser.runtime.time.sleep"),
            patch("bosshunter.browser.runtime._chrome_debug_version",
                  side_effect=[None, None, None, None,
                               {"webSocketDebuggerUrl": "ws://x"},
                               {"webSocketDebuggerUrl": "ws://x"}]),
            patch("bosshunter.browser.runtime._is_port_available", return_value=True),
            patch("bosshunter.browser.runtime.get_browser_profile_dir",
                  return_value=Path(profile_tmp.name) / "profile"),
            patch("bosshunter.browser.runtime._record_launched_browser") as record,
        ):
            from bosshunter.browser.runtime import launch_debuggable_browser

            app_window.launch_app_window("http://127.0.0.1:8686")
            self.assertTrue(launch_debuggable_browser(_config()))

        # 登录浏览器没再拉进程：只拉了一次，就是 --app 窗口那个实例。
        self.assertEqual(popen.call_count, 1)
        app_argv = popen.call_args_list[0].args[0]
        self.assertIn("--app=http://127.0.0.1:8686", app_argv)
        self.assertIn("--remote-debugging-port=9222", app_argv)
        self.assertIn(f"--user-data-dir={Path(profile_tmp.name) / 'profile'}", app_argv)
        record.assert_called_once_with(
            app_proc, Path(profile_tmp.name) / "profile", 9222, "C:/Chrome/chrome.exe")
        # 关主窗口只 terminate 那个实例的句柄。
        self.assertIs(app_window.get_app_window_process(), app_proc)
        app_window.close_app_window()
        app_proc.terminate.assert_called_once()
        login_proc.terminate.assert_not_called()


class WaitForServiceReadyTests(unittest.TestCase):
    @patch("bosshunter.app_window.is_our_service_alive", return_value=True)
    def test_ready_at_once_returns_true_without_waiting(self, _alive):
        self.assertTrue(app_window.wait_for_service_ready(timeout=15.0, interval=0.5))

    @patch("bosshunter.app_window.is_our_service_alive", side_effect=[False, False, True])
    def test_waits_until_ready(self, _alive):
        # 服务第 3 次轮询才好：不等到就绪不开窗，免得 --app 打开一张
        # 连不上的空白页；好了马上返回，不用等满超时。
        self.assertTrue(app_window.wait_for_service_ready(timeout=15.0, interval=0.01))

    @patch("bosshunter.app_window.is_our_service_alive", return_value=False)
    def test_never_ready_returns_false_at_deadline(self, _alive):
        # 15 秒还没好也返回（调用方照常开窗，用户刷新就能进），
        # 不能因为等不到就让用户连窗口都没有。
        self.assertFalse(app_window.wait_for_service_ready(timeout=0.05, interval=0.01))


class SplashServerTests(unittest.TestCase):
    def setUp(self):
        self._servers = []

    def tearDown(self):
        for server in self._servers:
            app_window.stop_splash_server(server)

    def _start(self, panel_url="http://127.0.0.1:8686"):
        server = app_window.start_splash_server(panel_url)
        self.assertIsNotNone(server)
        self._servers.append(server)
        return server

    def test_serves_startup_page_with_panel_url(self):
        import json as _json
        import urllib.request

        server = self._start()
        with urllib.request.urlopen(server.splash_url, timeout=5) as resp:
            page = resp.read().decode("utf-8")
        self.assertIn("简历猎手正在启动，请稍等", page)
        self.assertIn("正在等本地服务起来", page)
        self.assertIn(_json.dumps("http://127.0.0.1:8686"), page)
        self.assertTrue(server.splash_url.startswith("http://127.0.0.1:"))

    def test_health_proxy_reports_not_ready(self):
        import json as _json
        import urllib.request

        server = self._start()
        with patch("bosshunter.app_window.is_our_service_alive", return_value=False):
            with urllib.request.urlopen(server.splash_url + "splash-health", timeout=5) as resp:
                data = _json.load(resp)
        self.assertEqual(data, {"ready": False})

    def test_health_proxy_reports_ready_and_server_stops_itself(self):
        import json as _json
        import urllib.request

        server = self._start()
        with (
            patch("bosshunter.app_window.is_our_service_alive", return_value=True),
            patch.object(app_window, "_SPLASH_STOP_DELAY_SECONDS", 0.2),
        ):
            with urllib.request.urlopen(server.splash_url + "splash-health", timeout=5) as resp:
                data = _json.load(resp)
        self.assertEqual(data, {"ready": True})
        # 自停：延迟过后端口不再应答，不留后台服务。
        deadline = time.monotonic() + 5.0
        stopped = False
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(server.splash_url, timeout=1)
            except Exception:
                stopped = True
                break
            time.sleep(0.1)
        self.assertTrue(stopped, "启动页服务就绪后没有自停")

    def test_unknown_path_is_404(self):
        import urllib.error
        import urllib.request

        server = self._start()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(server.splash_url + "nope", timeout=5)
        self.assertEqual(caught.exception.code, 404)

    def test_stop_is_idempotent(self):
        server = self._start()
        app_window.stop_splash_server(server)
        app_window.stop_splash_server(server)
        app_window.stop_splash_server(None)


class LaunchSplashWindowTests(unittest.TestCase):
    def setUp(self):
        app_window._APP_WINDOW_PROC = None
        app_window._APP_WINDOW_STARTED_AT = None
        app_window._APP_WINDOW_DEBUG_PORT = None
        app_window._APP_WINDOW_URL = None
        app_window._APP_WINDOW_SPLASH_URL = None

    def tearDown(self):
        app_window._APP_WINDOW_PROC = None
        app_window._APP_WINDOW_STARTED_AT = None
        app_window._APP_WINDOW_DEBUG_PORT = None
        app_window._APP_WINDOW_URL = None
        app_window._APP_WINDOW_SPLASH_URL = None

    @patch("bosshunter.app_window.find_app_browser", return_value="C:/Chrome/chrome.exe")
    @patch("bosshunter.app_window.subprocess.Popen")
    @patch("bosshunter.browser.runtime.pick_chrome_debug_port", return_value=None)
    def test_opens_splash_url_but_watches_panel_url(self, _pick, popen, _find):
        proc = Mock()
        popen.return_value = proc
        result = app_window.launch_app_window(
            "http://127.0.0.1:8686",
            splash_url="http://127.0.0.1:46595/",
            panel_url="http://127.0.0.1:8686",
        )
        self.assertIs(result, proc)
        argv = popen.call_args.args[0]
        self.assertEqual(argv, ["C:/Chrome/chrome.exe", "--app=http://127.0.0.1:46595/"])
        # 窗口开的是启动页，watch 盯的还是面板地址。
        self.assertEqual(app_window.get_app_window_url(), "http://127.0.0.1:8686")
        self.assertEqual(app_window.get_app_window_splash_url(), "http://127.0.0.1:46595/")

    @patch("bosshunter.app_window.start_splash_server", return_value=None)
    @patch("bosshunter.app_window.launch_app_window")
    def test_splash_server_failure_falls_back_to_panel_window(self, launch, _start):
        proc = Mock()
        launch.return_value = proc
        result_proc, server = app_window.launch_splash_window("http://127.0.0.1:8686")
        self.assertIs(result_proc, proc)
        self.assertIsNone(server)
        launch.assert_called_once_with("http://127.0.0.1:8686", None)

    @patch("bosshunter.app_window.find_app_browser", return_value=None)
    @patch("webbrowser.open")
    def test_fallback_browser_opens_splash_url(self, open_browser, _find):
        result = app_window.launch_app_window(
            "http://127.0.0.1:8686",
            splash_url="http://127.0.0.1:46595/",
            panel_url="http://127.0.0.1:8686",
        )
        self.assertIsNone(result)
        open_browser.assert_called_once_with("http://127.0.0.1:46595/")
        self.assertEqual(app_window.get_app_window_url(), "http://127.0.0.1:8686")
        self.assertEqual(app_window.get_app_window_splash_url(), "http://127.0.0.1:46595/")


class WatchSplashTabTests(unittest.TestCase):
    @staticmethod
    def _fake_clock(times):
        it = iter(times)

        def _clock():
            try:
                return next(it)
            except StopIteration:
                return times[-1]

        return _clock

    def test_splash_tab_seen_then_gone_in_handoff_is_a_real_close(self):
        # v28：启动页阶段关窗不算交接假退出——句柄 grace 内退出，
        # 但启动页标签出现过、后来没了，就是用户关了窗口。
        proc = Mock()
        proc.poll.return_value = 0
        fired = []
        tab_lists = iter([
            ["http://127.0.0.1:46595/"],
            [],
        ])
        result = app_window.watch_app_window_close(
            lambda: fired.append(True),
            grace_seconds=5.0,
            poll_interval=0.01,
            wait_for_proc_seconds=30.0,
            _get_proc=lambda: proc,
            _started_at=100.0,
            _is_update_applying=lambda: False,
            _clock=self._fake_clock([100.0, 101.0, 101.0, 101.0, 101.0]),
            _sleep=lambda _: None,
            _panel_url="http://127.0.0.1:8686",
            _splash_url="http://127.0.0.1:46595/",
            _list_tabs=lambda: next(tab_lists),
        )
        self.assertTrue(result)
        self.assertEqual(fired, [True])

    def test_splash_tab_disappearing_while_proc_lingers_is_a_close(self):
        # v28：句柄还活着（ lingering 实例），启动页标签出现过后没了，
        # 同样是关窗；启动页跳面板后标签页 URL 会变成面板地址，
        # 两种都认。
        proc = Mock()
        proc.poll.return_value = None
        fired = []
        tab_lists = iter([
            ["http://127.0.0.1:46595/"],
            ["http://127.0.0.1:8686/"],
            ["https://www.zhipin.com/web/geek/job"],
        ])
        result = app_window.watch_app_window_close(
            lambda: fired.append(True),
            grace_seconds=5.0,
            poll_interval=0.01,
            wait_for_proc_seconds=30.0,
            _get_proc=lambda: proc,
            _started_at=100.0,
            _is_update_applying=lambda: False,
            _clock=self._fake_clock([100.0, 100.0, 100.0, 100.0, 100.0]),
            _sleep=lambda _: None,
            _panel_url="http://127.0.0.1:8686",
            _splash_url="http://127.0.0.1:46595/",
            _list_tabs=lambda: next(tab_lists),
        )
        self.assertTrue(result)
        self.assertEqual(fired, [True])


if __name__ == "__main__":
    unittest.main()
