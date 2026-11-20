"""多浏览器选择 + 自动化浏览器按需拉起（v20 补完：任务 E / 任务 F）。

- browser_choice（auto / chrome / edge / manual）+ executable_path（只在
  manual 时生效）的解析、保存/加载、回落；
- launch_debuggable_browser 按所选浏览器拉起、目标 URL 直达、无目标
  时才用 data: 说明起始页；
- 检测路径（诊断 / preflight / 巡检）不拉起浏览器；只有去登录、
  一键启动、启动任务时才拉起。
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


def _browser_config(**overrides):
    browser = {
        "runtime": "builtin",
        "auto_start_proxy": True,
        "proxy_host": "127.0.0.1",
        "proxy_port": 3456,
        "chrome_ports": [9222, 9229, 9333],
        "auto_launch_browser": True,
        "browser_choice": "auto",
        "executable_path": "",
    }
    browser.update(overrides)
    return {"browser": browser}


def _live_version():
    return {"Browser": "Chrome/138.0", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc"}


class ResolveBrowserExecutableTests(unittest.TestCase):
    def test_auto_prefers_chrome_over_edge(self):
        from bosshunter.browser.diagnostics import resolve_browser_executable

        with patch(
            "bosshunter.browser.diagnostics.discover_browsers",
            return_value={"chrome": "C:/Chrome/chrome.exe", "edge": "C:/Edge/msedge.exe"},
        ):
            resolved = resolve_browser_executable(_browser_config())
        self.assertEqual(resolved["executable"], "C:/Chrome/chrome.exe")
        self.assertEqual(resolved["name"], "Chrome")
        self.assertFalse(resolved["fallback"])

    def test_auto_falls_back_to_edge_when_chrome_missing(self):
        from bosshunter.browser.diagnostics import resolve_browser_executable

        with patch(
            "bosshunter.browser.diagnostics.discover_browsers",
            return_value={"chrome": None, "edge": "C:/Edge/msedge.exe"},
        ):
            resolved = resolve_browser_executable(_browser_config())
        self.assertEqual(resolved["executable"], "C:/Edge/msedge.exe")
        self.assertEqual(resolved["name"], "Edge")

    def test_choice_chrome_falls_back_to_edge_when_chrome_missing(self):
        from bosshunter.browser.diagnostics import resolve_browser_executable

        with patch(
            "bosshunter.browser.diagnostics.discover_browsers",
            return_value={"chrome": None, "edge": "C:/Edge/msedge.exe"},
        ):
            resolved = resolve_browser_executable(_browser_config(browser_choice="chrome"))
        self.assertEqual(resolved["executable"], "C:/Edge/msedge.exe")
        self.assertEqual(resolved["name"], "Edge")
        self.assertTrue(resolved["fallback"])

    def test_choice_edge_falls_back_to_chrome_when_edge_missing(self):
        from bosshunter.browser.diagnostics import resolve_browser_executable

        with patch(
            "bosshunter.browser.diagnostics.discover_browsers",
            return_value={"chrome": "C:/Chrome/chrome.exe", "edge": None},
        ):
            resolved = resolve_browser_executable(_browser_config(browser_choice="edge"))
        self.assertEqual(resolved["executable"], "C:/Chrome/chrome.exe")
        self.assertEqual(resolved["name"], "Chrome")
        self.assertTrue(resolved["fallback"])

    def test_nothing_found_reports_no_executable_and_generic_name(self):
        from bosshunter.browser.diagnostics import resolve_browser_executable

        with patch(
            "bosshunter.browser.diagnostics.discover_browsers",
            return_value={"chrome": None, "edge": None},
        ):
            resolved = resolve_browser_executable(_browser_config())
        self.assertIsNone(resolved["executable"])
        self.assertEqual(resolved["name"], "浏览器")

    def test_manual_path_is_used_when_file_exists(self):
        from bosshunter.browser.diagnostics import resolve_browser_executable

        with tempfile.NamedTemporaryFile(suffix="chrome.exe", delete=False) as tmp:
            manual = tmp.name
        self.addCleanup(lambda: Path(manual).unlink(missing_ok=True))
        with patch(
            "bosshunter.browser.diagnostics.discover_browsers",
            return_value={"chrome": "C:/Chrome/chrome.exe", "edge": None},
        ):
            resolved = resolve_browser_executable(
                _browser_config(browser_choice="manual", executable_path=manual)
            )
        self.assertEqual(resolved["executable"], manual)
        self.assertEqual(resolved["name"], "Chrome")
        self.assertFalse(resolved["fallback"])

    def test_manual_missing_path_falls_back_to_detected_chrome(self):
        from bosshunter.browser.diagnostics import resolve_browser_executable

        with patch(
            "bosshunter.browser.diagnostics.discover_browsers",
            return_value={"chrome": "C:/Chrome/chrome.exe", "edge": None},
        ):
            resolved = resolve_browser_executable(
                _browser_config(browser_choice="manual", executable_path="/no/such/browser.exe")
            )
        self.assertEqual(resolved["executable"], "C:/Chrome/chrome.exe")
        self.assertTrue(resolved["fallback"])

    def test_manual_path_is_ignored_when_choice_is_not_manual(self):
        from bosshunter.browser.diagnostics import resolve_browser_executable

        with patch(
            "bosshunter.browser.diagnostics.discover_browsers",
            return_value={"chrome": "C:/Chrome/chrome.exe", "edge": None},
        ) as discover:
            resolved = resolve_browser_executable(
                _browser_config(browser_choice="chrome", executable_path="C:/Edge/msedge.exe")
            )
        self.assertEqual(resolved["executable"], "C:/Chrome/chrome.exe")
        # 非手动模式下传给检测的配置里手动路径已被清掉，不会抢占选择。
        passed = discover.call_args.args[0]
        self.assertEqual(passed["browser"]["executable_path"], "")

    def test_invalid_choice_falls_back_to_auto(self):
        from bosshunter.browser.diagnostics import resolve_browser_executable

        with patch(
            "bosshunter.browser.diagnostics.discover_browsers",
            return_value={"chrome": "C:/Chrome/chrome.exe", "edge": None},
        ):
            resolved = resolve_browser_executable(_browser_config(browser_choice="bogus"))
        self.assertEqual(resolved["choice"], "auto")
        self.assertEqual(resolved["executable"], "C:/Chrome/chrome.exe")


class BrowserChoiceConfigTests(unittest.TestCase):
    def test_choice_and_manual_path_roundtrip_through_save_load(self):
        from bosshunter.config import load_config, save_config

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            config = load_config(path)
            config["browser"]["browser_choice"] = "manual"
            config["browser"]["executable_path"] = "C:/Edge/msedge.exe"
            save_config(config, path)
            reloaded = load_config(path)
        self.assertEqual(reloaded["browser"]["browser_choice"], "manual")
        self.assertEqual(reloaded["browser"]["executable_path"], "C:/Edge/msedge.exe")

    def test_invalid_choice_is_normalized_to_auto_on_load(self):
        from bosshunter.config import load_config

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text("browser:\n  browser_choice: bogus\n", encoding="utf-8")
            config = load_config(path)
        self.assertEqual(config["browser"]["browser_choice"], "auto")

    def test_default_choice_is_auto(self):
        from bosshunter.config import load_config

        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(Path(tmp) / "missing.yaml")
        self.assertEqual(config["browser"]["browser_choice"], "auto")
        self.assertEqual(config["browser"]["executable_path"], "")


class LaunchSelectedBrowserTests(unittest.TestCase):
    def _patches(self, testcase, discovered):
        profile_tmp = tempfile.TemporaryDirectory()
        testcase.addCleanup(profile_tmp.cleanup)
        patches = [
            patch("bosshunter.browser.runtime.time.sleep"),
            patch("bosshunter.browser.runtime.subprocess.Popen"),
            patch("bosshunter.browser.runtime._chrome_debug_version"),
            patch("bosshunter.browser.runtime._is_port_available", return_value=True),
            patch("bosshunter.browser.runtime.get_browser_profile_dir",
                  return_value=Path(profile_tmp.name) / "profile"),
            patch("bosshunter.browser.diagnostics.discover_browsers", return_value=discovered),
        ]
        mocks = [p.start() for p in patches]
        for p in patches:
            testcase.addCleanup(p.stop)
        return mocks

    def test_choice_edge_spawns_edge_not_chrome(self):
        _sleep, popen, debug_version, _port, _profile, _discover = self._patches(
            self, {"chrome": "C:/Chrome/chrome.exe", "edge": "C:/Edge/msedge.exe"}
        )
        from bosshunter.browser.runtime import launch_debuggable_browser

        debug_version.side_effect = [None, None, None, _live_version()]
        self.assertTrue(
            launch_debuggable_browser(_browser_config(browser_choice="edge"))
        )
        argv = popen.call_args.args[0]
        self.assertEqual(argv[0], "C:/Edge/msedge.exe")
        self.assertNotIn("--disable-blink-features=AutomationControlled", argv)

    def test_target_url_is_opened_directly_without_start_page(self):
        _sleep, popen, debug_version, _port, _profile, _discover = self._patches(
            self, {"chrome": "C:/Chrome/chrome.exe", "edge": None}
        )
        from bosshunter.browser.runtime import launch_debuggable_browser

        debug_version.side_effect = [None, None, None, _live_version()]
        self.assertTrue(launch_debuggable_browser(
            _browser_config(), target_url="https://www.zhipin.com/web/user/login"
        ))
        argv = popen.call_args.args[0]
        self.assertEqual(argv[-1], "https://www.zhipin.com/web/user/login")
        self.assertNotIn("about:blank", argv)

    def test_no_target_url_falls_back_to_data_start_page(self):
        _sleep, popen, debug_version, _port, _profile, _discover = self._patches(
            self, {"chrome": "C:/Chrome/chrome.exe", "edge": None}
        )
        from bosshunter.browser.runtime import _AUTOMATION_START_PAGE, launch_debuggable_browser

        debug_version.side_effect = [None, None, None, _live_version()]
        self.assertTrue(launch_debuggable_browser(_browser_config()))
        self.assertEqual(popen.call_args.args[0][-1], _AUTOMATION_START_PAGE)

    def test_no_browser_found_returns_false_without_spawning(self):
        _sleep, popen, debug_version, _port, _profile, _discover = self._patches(
            self, {"chrome": None, "edge": None}
        )
        from bosshunter.browser.runtime import launch_debuggable_browser

        debug_version.return_value = None
        self.assertFalse(launch_debuggable_browser(_browser_config()))
        popen.assert_not_called()


class EnsureRuntimeTargetUrlTests(unittest.TestCase):
    @patch("bosshunter.browser.runtime.launch_debuggable_browser", return_value=True)
    @patch("bosshunter.browser.runtime._wait_for_targets", side_effect=[False, True])
    @patch("bosshunter.browser.runtime.start_runtime")
    @patch("bosshunter.browser.runtime.runtime_health", return_value=None)
    @patch("bosshunter.browser.runtime.runtime_targets", return_value=None)
    @patch("bosshunter.browser.runtime.check_node_available", return_value={"available": True})
    def test_ensure_runtime_passes_target_url_to_launch(
        self, _node, _targets, _health, _start, _wait, launch
    ):
        from bosshunter.browser.runtime import ensure_runtime

        result = ensure_runtime(
            _browser_config(), wait_seconds=0.01,
            target_url="https://www.zhipin.com/web/user/login",
        )
        self.assertTrue(result)
        launch.assert_called_once()
        self.assertEqual(
            launch.call_args.kwargs.get("target_url"),
            "https://www.zhipin.com/web/user/login",
        )


class DiagnosticsDoesNotLaunchTests(unittest.TestCase):
    @patch("bosshunter.browser.diagnostics.discover_browsers",
           return_value={"chrome": None, "edge": None})
    @patch("bosshunter.browser.diagnostics.check_node_available",
           return_value={"available": True, "version": "v22.1.0"})
    @patch("bosshunter.browser.diagnostics.runtime_health", return_value=None)
    @patch("bosshunter.browser.diagnostics.runtime_targets",
           return_value=[{"targetId": "1", "url": "https://www.zhipin.com"}])
    @patch("bosshunter.browser.diagnostics.ensure_runtime", return_value=True)
    def test_run_diagnostics_forwards_allow_launch_false(
        self, ensure_runtime, _targets, _health, _node, _discover
    ):
        from bosshunter.browser.diagnostics import run_browser_diagnostics

        result = run_browser_diagnostics(_browser_config(), allow_launch=False)
        ensure_runtime.assert_called_once()
        self.assertEqual(ensure_runtime.call_args.kwargs, {"allow_launch": False, "wait_seconds": 5.0})
        self.assertTrue(result["chrome"])
        self.assertEqual(result["browser_choice"], "auto")
        self.assertEqual(result["selected_browser_name"], "浏览器")

    @patch("bosshunter.browser.diagnostics.discover_browsers",
           return_value={"chrome": None, "edge": None})
    @patch("bosshunter.browser.diagnostics.check_node_available",
           return_value={"available": True, "version": "v22.1.0"})
    @patch("bosshunter.browser.diagnostics.runtime_health", return_value=None)
    @patch("bosshunter.browser.diagnostics.runtime_targets",
           return_value=[{"targetId": "1", "url": "https://www.zhipin.com"}])
    @patch("bosshunter.browser.diagnostics.ensure_runtime", return_value=True)
    def test_boss_tab_comes_from_targets_without_facade_launch(
        self, ensure_runtime, runtime_targets, _health, _node, _discover
    ):
        # 回归：诊断里找标签页绝不走 browser facade（那条路会经
        # ensure_runtime 默认拉起浏览器，检测就变拉起了）。
        from bosshunter.browser.diagnostics import run_browser_diagnostics

        with patch(
            "bosshunter.browser.get_page_targets",
            side_effect=AssertionError("diagnostics must not call the facade"),
        ):
            result = run_browser_diagnostics(_browser_config(), allow_launch=False)
        self.assertEqual(result["boss_tab"]["url"], "https://www.zhipin.com")
        self.assertIsNone(result["zhilian_tab"])

    def test_preflight_check_does_not_launch(self):
        from bosshunter.web.preflight import check_browser_connection

        with patch(
            "bosshunter.web.preflight.run_browser_diagnostics",
            return_value={"node": {"available": True}, "runtime": False, "chrome": False,
                          "errors": [], "runtime_url": "http://127.0.0.1:3456"},
        ) as diagnostics:
            check_browser_connection(_browser_config())
        diagnostics.assert_called_once()
        self.assertEqual(diagnostics.call_args.kwargs, {"allow_launch": False})


class AppWindowRespectsChoiceTests(unittest.TestCase):
    def test_find_app_browser_uses_selected_browser(self):
        from bosshunter.app_window import find_app_browser

        with patch(
            "bosshunter.browser.diagnostics.resolve_browser_executable",
            return_value={"executable": "C:/Edge/msedge.exe", "name": "Edge",
                          "choice": "edge", "fallback": False},
        ):
            self.assertEqual(
                find_app_browser(_browser_config(browser_choice="edge")),
                "C:/Edge/msedge.exe",
            )

    def test_find_app_browser_returns_none_when_no_browser_found(self):
        from bosshunter.app_window import find_app_browser

        with patch(
            "bosshunter.browser.diagnostics.resolve_browser_executable",
            return_value={"executable": None, "name": "浏览器",
                          "choice": "auto", "fallback": False},
        ):
            self.assertIsNone(find_app_browser(_browser_config()))


class NewTabLaunchesToTargetTests(unittest.TestCase):
    def test_new_tab_passes_login_url_as_launch_target(self):
        import bosshunter.browser as facade

        client = Mock()
        client.new_tab.return_value = "target-1"
        with (
            patch.object(facade, "ensure_runtime", return_value=True) as ensure,
            patch.object(facade, "_client", return_value=client),
        ):
            target_id = facade.new_tab("https://www.zhipin.com/web/user/login")
        self.assertEqual(target_id, "target-1")
        ensure.assert_called_once()
        self.assertEqual(
            ensure.call_args.kwargs.get("target_url"),
            "https://www.zhipin.com/web/user/login",
        )


if __name__ == "__main__":
    unittest.main()
