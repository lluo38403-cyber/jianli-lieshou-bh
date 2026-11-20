"""自动拉起带调试端口的浏览器：专用配置目录、端口选择、开关。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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


def _live_version():
    return {"Browser": "Chrome/138.0", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc"}


class LaunchDebuggableBrowserTests(unittest.TestCase):
    def _patches(self, testcase):
        profile_tmp = tempfile.TemporaryDirectory()
        testcase.addCleanup(profile_tmp.cleanup)
        patches = [
            patch("bosshunter.browser.runtime.time.sleep"),
            patch("bosshunter.browser.runtime.subprocess.Popen"),
            patch("bosshunter.browser.runtime._chrome_debug_version"),
            patch("bosshunter.browser.runtime._is_port_available", return_value=True),
            patch("bosshunter.browser.runtime.get_browser_profile_dir",
                  return_value=Path(profile_tmp.name) / "profile"),
            patch("bosshunter.browser.diagnostics.discover_browsers",
                  return_value={"chrome": "C:/Chrome/chrome.exe", "edge": None}),
        ]
        mocks = [p.start() for p in patches]
        for p in patches:
            testcase.addCleanup(p.stop)
        # 返回顺序与 patches 列表一致：sleep, popen, debug_version, port, profile, discover
        return mocks

    def test_returns_true_without_spawning_when_debug_port_already_live(self):
        _sleep, popen, debug_version, _port, _profile, _discover = self._patches(self)
        from bosshunter.browser.runtime import launch_debuggable_browser

        debug_version.return_value = _live_version()
        self.assertTrue(launch_debuggable_browser(_config()))
        popen.assert_not_called()

    def test_spawns_chrome_with_debug_args_and_dedicated_profile(self):
        _sleep, popen, debug_version, _port, profile, _discover = self._patches(self)
        from bosshunter.browser.runtime import _AUTOMATION_START_PAGE, launch_debuggable_browser

        # 前 3 次是拉起前对 9222/9229/9333 的探测，第 4 次是等启动应答。
        debug_version.side_effect = [None, None, None, _live_version()]
        self.assertTrue(launch_debuggable_browser(_config()))
        popen.assert_called_once()
        argv = popen.call_args.args[0]
        self.assertEqual(argv[0], "C:/Chrome/chrome.exe")
        self.assertIn("--remote-debugging-port=9222", argv)
        # 回归：--disable-blink-features=AutomationControlled 只会让
        # Chrome 顶上挂一条“不受支持的命令行标记”警告；CDP 直连的真人
        # Chrome 里 navigator.webdriver 本来就是 false，不需要它。
        self.assertNotIn("--disable-blink-features=AutomationControlled", argv)
        self.assertIn(f"--user-data-dir={profile.return_value}", argv)
        # 起始页不再是孤零零的 about:blank 白屏，是一句中文说明。
        self.assertNotIn("about:blank", argv)
        self.assertEqual(argv[-1], _AUTOMATION_START_PAGE)
        self.assertTrue(argv[-1].startswith("data:text/html,"))
        profile.assert_called_once()

    def test_skips_port_occupied_by_non_chrome_process(self):
        _sleep, popen, debug_version, port_available, _profile, _discover = self._patches(self)
        from bosshunter.browser.runtime import launch_debuggable_browser

        debug_version.side_effect = [None, None, None, _live_version()]
        port_available.side_effect = lambda host, port: port != 9222
        self.assertTrue(launch_debuggable_browser(_config()))
        argv = popen.call_args.args[0]
        self.assertIn("--remote-debugging-port=9229", argv)

    def test_uses_edge_when_chrome_missing(self):
        _sleep, popen, debug_version, _port, _profile, discover = self._patches(self)
        from bosshunter.browser.runtime import launch_debuggable_browser

        discover.return_value = {"chrome": None, "edge": "C:/Edge/msedge.exe"}
        debug_version.side_effect = [None, None, None, _live_version()]
        self.assertTrue(launch_debuggable_browser(_config()))
        self.assertEqual(popen.call_args.args[0][0], "C:/Edge/msedge.exe")

    def test_returns_false_without_any_browser(self):
        _sleep, popen, debug_version, _port, _profile, discover = self._patches(self)
        from bosshunter.browser.runtime import launch_debuggable_browser

        discover.return_value = {"chrome": None, "edge": None}
        debug_version.return_value = None
        self.assertFalse(launch_debuggable_browser(_config()))
        popen.assert_not_called()

    def test_returns_false_when_auto_launch_disabled(self):
        _sleep, popen, debug_version, _port, _profile, _discover = self._patches(self)
        from bosshunter.browser.runtime import launch_debuggable_browser

        debug_version.return_value = None
        self.assertFalse(launch_debuggable_browser(_config(auto_launch_browser=False)))
        popen.assert_not_called()

    def test_returns_false_when_runtime_not_builtin(self):
        _sleep, popen, debug_version, _port, _profile, _discover = self._patches(self)
        from bosshunter.browser.runtime import launch_debuggable_browser

        debug_version.return_value = None
        self.assertFalse(launch_debuggable_browser(_config(runtime="external")))
        popen.assert_not_called()

    def test_returns_false_when_browser_never_answers_debug_port(self):
        _sleep, popen, debug_version, _port, _profile, _discover = self._patches(self)
        from bosshunter.browser.runtime import launch_debuggable_browser

        # 假时钟：每次取时间前进 1 秒，让 15 秒的等启动循环立刻走完，
        # 不然这个测试要实等 15 秒。
        clock = {"t": 1000.0}

        def _fake_time():
            clock["t"] += 1.0
            return clock["t"]

        with patch("bosshunter.browser.runtime.time.time", side_effect=_fake_time):
            debug_version.return_value = None
            self.assertFalse(launch_debuggable_browser(_config()))
        popen.assert_called_once()


class EnsureRuntimeLaunchTests(unittest.TestCase):
    def _patches(self, testcase):
        patches = [
            patch("bosshunter.browser.runtime.time.sleep"),
            patch("bosshunter.browser.runtime._is_port_available", return_value=True),
            patch("bosshunter.browser.runtime.runtime_health", return_value=None),
            patch("bosshunter.browser.runtime.runtime_targets"),
            patch("bosshunter.browser.runtime.start_runtime"),
            patch("bosshunter.browser.runtime.check_node_available",
                  return_value={"available": True, "version": "v22.1.0"}),
            patch("bosshunter.browser.runtime.launch_debuggable_browser", return_value=True),
        ]
        mocks = [p.start() for p in patches]
        for p in patches:
            testcase.addCleanup(p.stop)
        # 返回顺序：sleep, port, health, targets, start, node, launch
        return mocks

    def test_launches_browser_when_proxy_up_but_chrome_missing(self):
        _sleep, _port, _health, targets, start, _node, launch = self._patches(self)
        from bosshunter.browser.runtime import ensure_runtime

        # 初始检查、启动后第一次探测都没目标；拉起后再次探测连上。
        targets.side_effect = [None, None, [{"targetId": "abc"}]]
        self.assertTrue(ensure_runtime(_config(), wait_seconds=0))
        launch.assert_called_once()
        start.assert_called_once()

    def test_does_not_launch_when_allow_launch_false(self):
        _sleep, _port, _health, targets, start, _node, launch = self._patches(self)
        from bosshunter.browser.runtime import ensure_runtime

        targets.return_value = None
        self.assertFalse(ensure_runtime(_config(), wait_seconds=0, allow_launch=False))
        launch.assert_not_called()
        # 重试那次也不拉起。
        self.assertEqual(start.call_count, 2)


class DiagnosticsLaunchFlagTests(unittest.TestCase):
    def test_run_browser_diagnostics_passes_allow_launch_through(self):
        from bosshunter.browser import diagnostics as diagnostics_module

        with (
            patch.object(diagnostics_module, "check_node_available",
                         return_value={"available": True, "version": "v22.1.0"}),
            patch.object(diagnostics_module, "ensure_runtime", return_value=True) as ensure,
            patch.object(diagnostics_module, "runtime_health",
                         return_value={"browserProduct": "Chrome/138.0", "browserName": "Google Chrome"}),
            patch.object(diagnostics_module, "runtime_targets",
                         return_value=[{"targetId": "1", "url": "https://www.zhipin.com"}]),
            patch.object(diagnostics_module, "find_boss_tab",
                         return_value={"targetId": "1", "url": "https://www.zhipin.com"}),
            patch.object(diagnostics_module, "find_zhilian_tab", return_value=None),
        ):
            diagnostics_module.run_browser_diagnostics(_config(), allow_launch=False)
            ensure.assert_called_once()
            self.assertEqual(ensure.call_args.kwargs, {"allow_launch": False})
            ensure.reset_mock()
            diagnostics_module.run_browser_diagnostics(_config())
            self.assertEqual(ensure.call_args.kwargs, {"allow_launch": True})


class CdpProxyEdgePortTests(unittest.TestCase):
    def test_proxy_discovers_edge_devtools_active_port(self):
        proxy = Path(__file__).resolve().parent.parent / "src" / "bosshunter" / "browser" / "runtime" / "cdp-proxy.mjs"
        text = proxy.read_text(encoding="utf-8")
        self.assertIn("Microsoft/Edge/User Data/DevToolsActivePort", text)
        self.assertIn("Microsoft Edge/DevToolsActivePort", text)
        self.assertIn(".config/microsoft-edge/DevToolsActivePort", text)


if __name__ == "__main__":
    unittest.main()
