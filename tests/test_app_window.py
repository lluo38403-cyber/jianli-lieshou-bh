"""独立应用窗口（--app）：拉起、降级、关闭、端口复用、与登录浏览器分离。"""

import tempfile
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

    def tearDown(self):
        app_window._APP_WINDOW_PROC = None

    @patch("os.name", "nt")
    @patch("bosshunter.app_window.find_app_browser", return_value="C:/Chrome/chrome.exe")
    @patch("bosshunter.app_window.subprocess.Popen")
    def test_launches_chrome_app_mode_without_console_window(self, popen, _find):
        proc = Mock()
        popen.return_value = proc

        result = app_window.launch_app_window("http://127.0.0.1:8686")

        self.assertIs(result, proc)
        popen.assert_called_once()
        argv = popen.call_args.args[0]
        self.assertEqual(argv, ["C:/Chrome/chrome.exe", "--app=http://127.0.0.1:8686"])
        # --app 窗口不用登录浏览器的专用 profile，也不要调试端口。
        self.assertFalse(any("user-data-dir" in arg for arg in argv))
        self.assertFalse(any("remote-debugging-port" in arg for arg in argv))
        flags = popen.call_args.kwargs.get("creationflags", 0)
        self.assertTrue(flags & 0x08000000, "CREATE_NO_WINDOW 必须在启动标志里")
        self.assertIs(app_window.get_app_window_process(), proc)

    @patch("bosshunter.app_window.find_app_browser", return_value=None)
    @patch("webbrowser.open")
    def test_falls_back_to_default_browser_when_no_browser_found(self, open_browser, _find):
        result = app_window.launch_app_window("http://127.0.0.1:8686")

        self.assertIsNone(result)
        open_browser.assert_called_once_with("http://127.0.0.1:8686")

    @patch("bosshunter.app_window.find_app_browser", return_value="C:/Chrome/chrome.exe")
    @patch("bosshunter.app_window.subprocess.Popen", side_effect=OSError("chrome 炸了"))
    @patch("webbrowser.open")
    def test_falls_back_to_default_browser_when_app_launch_fails(self, open_browser, _popen, _find):
        result = app_window.launch_app_window("http://127.0.0.1:8686")

        self.assertIsNone(result)
        open_browser.assert_called_once_with("http://127.0.0.1:8686")

    @patch("bosshunter.app_window.find_app_browser", return_value="C:/Edge/msedge.exe")
    @patch("bosshunter.app_window.subprocess.Popen")
    def test_uses_edge_when_chrome_missing(self, popen, _find):
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
        from bosshunter.web import server

        with (
            patch("bosshunter.updater.cleanup_stale_update_processes") as cleanup,
            patch("bosshunter.app_window.is_our_service_alive", return_value=True),
            patch("bosshunter.app_window.launch_app_window"),
            patch.object(server, "app"),
        ):
            server.run_server(host="127.0.0.1", port=8686, open_browser=True)

        cleanup.assert_called_once_with()


class AppWindowLoginSeparationTests(unittest.TestCase):
    def setUp(self):
        app_window._APP_WINDOW_PROC = None

    def tearDown(self):
        app_window._APP_WINDOW_PROC = None

    def test_app_window_and_login_browser_are_different_processes_and_profiles(self):
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
                  side_effect=[None, None, None, {"webSocketDebuggerUrl": "ws://x"}]),
            patch("bosshunter.browser.runtime._is_port_available", return_value=True),
            patch("bosshunter.browser.runtime.get_browser_profile_dir",
                  return_value=Path(profile_tmp.name) / "profile"),
            patch("bosshunter.browser.diagnostics.discover_browsers",
                  return_value={"chrome": "C:/Chrome/chrome.exe", "edge": None}),
        ):
            from bosshunter.browser.runtime import launch_debuggable_browser

            app_window.launch_app_window("http://127.0.0.1:8686")
            self.assertTrue(launch_debuggable_browser(_config()))

        self.assertEqual(popen.call_count, 2)
        app_argv = popen.call_args_list[0].args[0]
        login_argv = popen.call_args_list[1].args[0]
        # 主窗口：--app 模式，不用专用 profile、不用调试端口。
        self.assertIn("--app=http://127.0.0.1:8686", app_argv)
        self.assertFalse(any("user-data-dir" in arg for arg in app_argv))
        self.assertFalse(any("remote-debugging-port" in arg for arg in app_argv))
        # 登录浏览器：专用 profile + 调试端口，和主窗口严格分离。
        self.assertIn("--remote-debugging-port=9222", login_argv)
        self.assertIn(f"--user-data-dir={Path(profile_tmp.name) / 'profile'}", login_argv)
        # 两个是不同的 Popen 句柄：关主窗口只会 terminate app_proc。
        self.assertIsNot(app_proc, login_proc)
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


if __name__ == "__main__":
    unittest.main()
