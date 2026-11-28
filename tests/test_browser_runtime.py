import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

import httpx
import yaml

from bosshunter.config import load_config


class BrowserRuntimeConfigTests(unittest.TestCase):
    def test_browser_defaults_are_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(Path(tmp) / "missing.yaml")

        self.assertEqual(config["browser"]["runtime"], "builtin")
        self.assertEqual(config["browser"]["proxy_host"], "127.0.0.1")
        self.assertEqual(config["browser"]["proxy_port"], 3456)
        self.assertEqual(config["browser"]["chrome_ports"], [9222, 9229, 9333])
        self.assertTrue(config["browser"]["auto_start_proxy"])
        self.assertTrue(config["browser"]["enable_port_guard"])
        self.assertTrue(config["browser"]["site_patterns"])

    def test_browser_config_overrides_merge_with_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text(
                yaml.dump({"browser": {"proxy_port": 4567, "auto_start_proxy": False}}, sort_keys=False),
                encoding="utf-8",
            )

            config = load_config(config_path)

        self.assertEqual(config["browser"]["runtime"], "builtin")
        self.assertEqual(config["browser"]["proxy_port"], 4567)
        self.assertFalse(config["browser"]["auto_start_proxy"])
        self.assertEqual(config["browser"]["chrome_ports"], [9222, 9229, 9333])


class BrowserRuntimeManagerTests(unittest.TestCase):
    def test_get_runtime_url_uses_configured_host_and_port(self):
        from bosshunter.browser.runtime import get_runtime_url

        url = get_runtime_url({"browser": {"proxy_host": "localhost", "proxy_port": 4567}})

        self.assertEqual(url, "http://localhost:4567")

    def test_runtime_script_path_points_to_bundled_proxy(self):
        from bosshunter.browser.runtime import get_runtime_script_path

        path = get_runtime_script_path()

        self.assertEqual(path.name, "cdp-proxy.mjs")
        self.assertTrue(path.exists())

    @patch("bosshunter.browser.runtime.subprocess.run")
    @patch("bosshunter.browser.runtime._candidate_node_executables")
    def test_check_node_available_returns_version(self, candidates, run):
        from bosshunter.browser.runtime import check_node_available

        candidates.return_value = ["node"]
        run.return_value = Mock(returncode=0, stdout="v22.1.0\n")

        result = check_node_available()

        self.assertTrue(result["available"])
        self.assertEqual(result["version"], "v22.1.0")
        self.assertEqual(result["executable"], "node")

    @patch("os.name", "nt")
    @patch("bosshunter.browser.runtime.subprocess.run")
    @patch("bosshunter.browser.runtime._candidate_node_executables")
    def test_check_node_available_hides_the_console_window(self, candidates, run):
        # 回归：Windows 上 node --version 不带 CREATE_NO_WINDOW 会闪
        # 一下黑框。run 的 creationflags 必须带上它。
        # 注意：os.name 扮成 nt 时 _candidate_node_executables 已 mock，
        # 不走 Path 分支（pathlib.Path 会跟着 os.name 变成 WindowsPath）。
        from bosshunter.browser.runtime import check_node_available

        candidates.return_value = ["node"]
        run.return_value = Mock(returncode=0, stdout="v22.1.0\n")

        check_node_available()

        flags = run.call_args.kwargs.get("creationflags", 0)
        self.assertTrue(flags & 0x08000000, "CREATE_NO_WINDOW 缺失")

    @patch("os.name", "nt")
    def test_launch_kwargs_hide_the_console_window_on_windows(self):
        # 回归：只用 DETACHED_PROCESS 不够——detached 的进程自己没有
        # 控制台，但它拉起的控制台子程序会各自新开黑框；node 运行时
        # 和浏览器拉起都走这里，必须带 CREATE_NO_WINDOW。
        from bosshunter.browser.runtime import _launch_kwargs

        flags = _launch_kwargs().get("creationflags", 0)
        self.assertTrue(flags & 0x08000000, "CREATE_NO_WINDOW 缺失")
        self.assertTrue(flags & 0x00000008, "DETACHED_PROCESS 缺失")

    @patch("bosshunter.browser.runtime.subprocess.run")
    @patch("bosshunter.browser.runtime.Path.is_file", return_value=True)
    @patch("bosshunter.browser.runtime._candidate_node_executables")
    def test_check_node_available_reuses_ai_bundled_node(self, candidates, is_file, run):
        from bosshunter.browser.runtime import check_node_available

        candidates.return_value = ["node", "/ai/runtime/node/bin/node"]
        run.side_effect = [
            OSError("node missing from PATH"),
            Mock(returncode=0, stdout="v24.14.0\n", stderr=""),
        ]

        result = check_node_available()

        self.assertTrue(result["available"])
        self.assertEqual(result["version"], "v24.14.0")
        self.assertEqual(result["executable"], "/ai/runtime/node/bin/node")

    @patch("bosshunter.browser.runtime.subprocess.run")
    @patch("bosshunter.browser.runtime.Path.is_file", return_value=True)
    @patch("bosshunter.browser.runtime._candidate_node_executables")
    def test_check_node_available_skips_unsupported_version(self, candidates, is_file, run):
        from bosshunter.browser.runtime import check_node_available

        candidates.return_value = ["/old/node", "/new/node"]
        run.side_effect = [
            Mock(returncode=0, stdout="v20.18.0\n", stderr=""),
            Mock(returncode=0, stdout="v22.11.0\n", stderr=""),
        ]

        result = check_node_available()

        self.assertTrue(result["available"])
        self.assertEqual(result["executable"], "/new/node")

    @patch("bosshunter.browser.runtime.httpx.get")
    def test_runtime_targets_returns_array_from_bosshunter_runtime(self, http_get):
        from bosshunter.browser.runtime import runtime_targets

        health = Mock(status_code=200)
        health.json.return_value = {"status": "ok", "runtime": "bosshunter"}
        targets = Mock(status_code=200)
        targets.json.return_value = [{"targetId": "abc"}]
        http_get.side_effect = [health, targets]

        result = runtime_targets({"browser": {"proxy_host": "127.0.0.1", "proxy_port": 3456}})

        self.assertEqual(result, [{"targetId": "abc"}])
        self.assertEqual(http_get.call_args_list[0].args, ("http://127.0.0.1:3456/health",))
        self.assertEqual(http_get.call_args_list[0].kwargs, {"timeout": 3, "trust_env": False})
        self.assertEqual(http_get.call_args_list[1].args, ("http://127.0.0.1:3456/targets",))
        self.assertEqual(http_get.call_args_list[1].kwargs, {"timeout": 5, "trust_env": False})

    @patch("bosshunter.browser.runtime.httpx.get")
    def test_runtime_targets_rejects_non_bosshunter_runtime(self, http_get):
        from bosshunter.browser.runtime import runtime_targets

        health = Mock(status_code=200)
        health.json.return_value = {"status": "ok", "runtime": "web-access"}
        http_get.return_value = health

        result = runtime_targets({"browser": {"proxy_host": "127.0.0.1", "proxy_port": 3456}})

        self.assertIsNone(result)
        # A3 之后 runtime_health 在主端口返回非 bosshunter 时还会再探
        # 进程内端口（这里是前面用例残留的 3457），所以不再断言只调
        # 一次；仍断言主端口被探过、且 /targets 绝不能被调。
        self.assertIn(
            call("http://127.0.0.1:3456/health", timeout=3, trust_env=False),
            http_get.call_args_list,
        )
        self.assertTrue(all("/targets" not in call_.args[0] for call_ in http_get.call_args_list))

    @patch("bosshunter.browser.runtime.runtime_targets")
    @patch("bosshunter.browser.runtime.start_runtime")
    @patch("bosshunter.browser.runtime.check_node_available")
    def test_ensure_runtime_starts_builtin_runtime_when_auto_start_enabled(self, check_node, start_runtime, runtime_targets):
        from bosshunter.browser.runtime import ensure_runtime

        check_node.return_value = {"available": True, "version": "v22.1.0"}
        runtime_targets.side_effect = [None, [{"targetId": "abc"}]]

        result = ensure_runtime({"browser": {"runtime": "builtin", "auto_start_proxy": True}}, wait_seconds=0.01)

        self.assertTrue(result)
        start_runtime.assert_called_once()
        self.assertEqual(start_runtime.call_args.args[1], "node")

    @patch("bosshunter.browser.runtime.launch_debuggable_browser", return_value=False)
    @patch("bosshunter.browser.runtime.time.sleep")
    @patch("bosshunter.browser.runtime._is_port_available", return_value=True)
    @patch("bosshunter.browser.runtime.runtime_health", return_value=None)
    @patch("bosshunter.browser.runtime.runtime_targets")
    @patch("bosshunter.browser.runtime.start_runtime")
    @patch("bosshunter.browser.runtime.check_node_available", return_value={"available": True})
    def test_startup_probes_once_per_attempt_even_when_deadline_has_expired(
        self, check_node, start_runtime, runtime_targets, runtime_health, available, sleep, launch
    ):
        from bosshunter.browser.runtime import ensure_runtime

        for ready in ([], None):
            with self.subTest(ready=ready):
                runtime_targets.reset_mock()
                sleep.reset_mock()
                calls = {"n": 0}

                def _targets(browser=None, _ready=ready, _calls=calls):
                    # Attempt 1: initial check None, probe returns ready.
                    # On failure ensure_runtime retries once (3s gap) and
                    # probes the same way again.
                    _calls["n"] += 1
                    return None if _calls["n"] % 2 == 1 else _ready

                runtime_targets.side_effect = _targets
                result = ensure_runtime({"browser": {"auto_start_proxy": True}}, wait_seconds=0)
                self.assertEqual(result, ready is not None)
                if ready is None:
                    self.assertEqual(runtime_targets.call_count, 4)
                    sleep.assert_called_once_with(3)
                else:
                    self.assertEqual(runtime_targets.call_count, 2)
                    sleep.assert_not_called()

    @patch("bosshunter.browser.runtime.time.sleep")
    @patch("bosshunter.browser.runtime._is_port_available")
    @patch("bosshunter.browser.runtime.runtime_health")
    @patch("bosshunter.browser.runtime.runtime_targets")
    @patch("bosshunter.browser.runtime.start_runtime")
    @patch("bosshunter.browser.runtime.check_node_available")
    def test_ensure_runtime_uses_next_free_port_when_default_has_non_bosshunter_service(
        self,
        check_node,
        start_runtime,
        runtime_targets,
        runtime_health,
        is_port_available,
        sleep,
    ):
        from bosshunter.browser.runtime import ensure_runtime, get_runtime_url

        config = {"browser": {"runtime": "builtin", "auto_start_proxy": True, "proxy_host": "127.0.0.1", "proxy_port": 3456}}
        check_node.return_value = {"available": True, "version": "v22.1.0"}
        runtime_health.side_effect = lambda browser: (
            {"status": "ok", "runtime": "web-access"} if browser["proxy_port"] == 3456 else None
        )
        is_port_available.side_effect = lambda host, port: port == 3457
        runtime_targets.side_effect = lambda browser: [{"targetId": "abc"}] if browser["proxy_port"] == 3457 else None

        result = ensure_runtime(config, wait_seconds=0.01)

        self.assertTrue(result)
        self.assertEqual(config["browser"]["proxy_port"], 3457)
        self.assertEqual(start_runtime.call_args.args[0]["proxy_port"], 3457)
        self.assertEqual(start_runtime.call_args.args[1], "node")
        self.assertEqual(get_runtime_url(), "http://127.0.0.1:3457")

    @patch("bosshunter.browser.runtime.time.sleep")
    @patch("bosshunter.browser.runtime._is_port_available")
    @patch("bosshunter.browser.runtime.runtime_health")
    @patch("bosshunter.browser.runtime.runtime_targets")
    @patch("bosshunter.browser.runtime.start_runtime")
    @patch("bosshunter.browser.runtime.check_node_available")
    def test_ensure_runtime_uses_next_free_port_when_default_is_system_reserved(
        self,
        check_node,
        start_runtime,
        runtime_targets,
        runtime_health,
        is_port_available,
        sleep,
    ):
        from bosshunter.browser.runtime import ensure_runtime

        config = {"browser": {"runtime": "builtin", "auto_start_proxy": True, "proxy_host": "127.0.0.1", "proxy_port": 3456}}
        check_node.return_value = {"available": True, "version": "v22.1.0"}
        # Nothing responds on the configured port (no HTTP health) and the
        # port itself cannot be bound, mirroring Windows reserved port ranges.
        runtime_health.return_value = None
        is_port_available.side_effect = lambda host, port: port == 3457
        runtime_targets.side_effect = lambda browser: [{"targetId": "abc"}] if browser["proxy_port"] == 3457 else None

        result = ensure_runtime(config, wait_seconds=0.01)

        self.assertTrue(result)
        self.assertEqual(config["browser"]["proxy_port"], 3457)
        self.assertEqual(start_runtime.call_args.args[0]["proxy_port"], 3457)
        self.assertEqual(start_runtime.call_args.args[1], "node")

    @patch("bosshunter.browser.runtime.subprocess.Popen")
    def test_start_runtime_sets_bosshunter_environment(self, popen):
        from bosshunter.browser.runtime import start_runtime

        with patch.dict(os.environ, {}, clear=True):
            start_runtime({"browser": {"proxy_port": 4567, "chrome_ports": [9222, 9333], "enable_port_guard": False}})

        env = popen.call_args.kwargs["env"]
        self.assertEqual(env["BOSSHUNTER_BROWSER_PROXY_PORT"], "4567")
        self.assertEqual(env["BOSSHUNTER_CHROME_PORTS"], "9222,9333")
        self.assertEqual(env["BOSSHUNTER_ENABLE_PORT_GUARD"], "false")


class RuntimeHealthFallbackTests(unittest.TestCase):
    """A3：配置端口连不上时，读时兜底到进程内已切换的代理端口（不写盘）。"""

    def setUp(self):
        import bosshunter.browser.runtime as runtime

        self._runtime = runtime
        self._saved_config = runtime._browser_config
        # 模拟一次端口回退：进程内配置已切到 3457，config.yaml 仍是 3456。
        runtime.set_browser_config({"browser": {"proxy_host": "127.0.0.1", "proxy_port": 3457}})

    def tearDown(self):
        self._runtime._browser_config = self._saved_config

    def _bosshunter_health(self):
        response = Mock(status_code=200)
        response.json.return_value = {"status": "ok", "runtime": "bosshunter", "chromePort": 9222}
        return response

    def _foreign_health(self):
        response = Mock(status_code=200)
        response.json.return_value = {"status": "ok", "runtime": "web-access"}
        return response

    @patch("bosshunter.browser.runtime.httpx.get")
    def test_falls_back_to_in_process_port_when_configured_port_is_down(self, http_get):
        from bosshunter.browser.runtime import get_runtime_url, runtime_health

        def _get(url, **kwargs):
            if ":3456/" in url:
                raise httpx.ConnectError("connection refused", request=None)
            return self._bosshunter_health()

        http_get.side_effect = _get
        config = {"browser": {"proxy_host": "127.0.0.1", "proxy_port": 3456}}

        result = runtime_health(config)

        self.assertEqual(result, {"status": "ok", "runtime": "bosshunter", "chromePort": 9222})
        # 采用新端口：进程内配置与本次传入的 config dict 都同步（只改内存）。
        self.assertEqual(config["browser"]["proxy_port"], 3457)
        self.assertEqual(get_runtime_url(), "http://127.0.0.1:3457")
        fallback_call = next(call for call in http_get.call_args_list if ":3457/" in call.args[0])
        self.assertEqual(fallback_call.kwargs["timeout"], 1)

    @patch("bosshunter.browser.runtime.httpx.get")
    def test_returns_none_when_both_ports_are_down(self, http_get):
        from bosshunter.browser.runtime import runtime_health

        http_get.side_effect = httpx.ConnectError("connection refused", request=None)
        config = {"browser": {"proxy_host": "127.0.0.1", "proxy_port": 3456}}

        self.assertIsNone(runtime_health(config))
        self.assertEqual(config["browser"]["proxy_port"], 3456)
        self.assertEqual(http_get.call_count, 2)

    @patch("bosshunter.browser.runtime.httpx.get")
    def test_falls_back_when_configured_port_serves_a_foreign_runtime(self, http_get):
        from bosshunter.browser.runtime import runtime_health

        def _get(url, **kwargs):
            if ":3456/" in url:
                return self._foreign_health()
            return self._bosshunter_health()

        http_get.side_effect = _get
        config = {"browser": {"proxy_host": "127.0.0.1", "proxy_port": 3456}}

        result = runtime_health(config)

        self.assertEqual(result["runtime"], "bosshunter")
        self.assertEqual(config["browser"]["proxy_port"], 3457)

    @patch("bosshunter.browser.runtime.httpx.get")
    def test_fallback_is_not_adopted_when_it_is_not_bosshunter(self, http_get):
        from bosshunter.browser.runtime import runtime_health

        def _get(url, **kwargs):
            if ":3456/" in url:
                raise httpx.ConnectError("connection refused", request=None)
            return self._foreign_health()

        http_get.side_effect = _get
        config = {"browser": {"proxy_host": "127.0.0.1", "proxy_port": 3456}}

        self.assertIsNone(runtime_health(config))
        self.assertEqual(config["browser"]["proxy_port"], 3456)

    @patch("bosshunter.browser.runtime.httpx.get")
    def test_no_fallback_probe_when_ports_match(self, http_get):
        from bosshunter.browser.runtime import runtime_health

        self._runtime.set_browser_config({"browser": {"proxy_host": "127.0.0.1", "proxy_port": 3456}})
        http_get.side_effect = httpx.ConnectError("connection refused", request=None)

        self.assertIsNone(runtime_health({"browser": {"proxy_host": "127.0.0.1", "proxy_port": 3456}}))
        http_get.assert_called_once()


class BrowserRuntimeSourceTests(unittest.TestCase):
    def test_cdp_proxy_fetches_browser_websocket_url_for_fallback_ports(self):
        script = Path(__file__).parents[1] / "src" / "bosshunter" / "browser" / "runtime" / "cdp-proxy.mjs"
        source = script.read_text(encoding="utf-8")

        self.assertIn("/json/version", source)
        self.assertIn("webSocketDebuggerUrl", source)

    def test_cdp_proxy_probes_configured_ports_before_default_profile_port_file(self):
        # A4：配置端口（工作浏览器，默认 9222）优先于默认配置的
        # DevToolsActivePort 文件——个人 Chrome 曾带调试端口启动过时，
        # 先扫文件会先连上个人 Chrome，登录页落到另一个窗口。
        script = Path(__file__).parents[1] / "src" / "bosshunter" / "browser" / "runtime" / "cdp-proxy.mjs"
        source = script.read_text(encoding="utf-8")
        discover = source.split("async function discoverChromePort()", 1)[1]

        self.assertLess(
            discover.index("for (const port of COMMON_PORTS)"),
            discover.index("for (const filePath of activePortFiles())"),
        )

    def test_cdp_proxy_only_reuses_bosshunter_runtime_on_occupied_port(self):
        script = Path(__file__).parents[1] / "src" / "bosshunter" / "browser" / "runtime" / "cdp-proxy.mjs"
        source = script.read_text(encoding="utf-8")

        self.assertIn("const RUNTIME_NAME = 'bosshunter'", source)
        self.assertIn("health.runtime === RUNTIME_NAME", source)
        self.assertNotIn("data.includes", source)

    def test_cdp_proxy_supports_background_tabs_and_platform_select_all(self):
        script = Path(__file__).parents[1] / "src" / "bosshunter" / "browser" / "runtime" / "cdp-proxy.mjs"
        source = script.read_text(encoding="utf-8")

        self.assertIn("q.background === '1'", source)
        self.assertIn("{ url: targetUrl, background }", source)
        self.assertNotIn("'Page.bringToFront'", source)
        self.assertIn("process.platform === 'darwin'", source)
        self.assertIn("pathname === '/key'", source)

    def test_cdp_proxy_returns_new_target_without_waiting_for_full_page_load(self):
        script = Path(__file__).parents[1] / "src" / "bosshunter" / "browser" / "runtime" / "cdp-proxy.mjs"
        source = script.read_text(encoding="utf-8")
        new_route = source.split("pathname === '/new'", 1)[1].split("pathname === '/close'", 1)[0]

        self.assertIn("ensureSession(targetId)", new_route)
        self.assertNotIn("waitForLoad", new_route)

    def test_cdp_proxy_minimizes_before_binding_session(self):
        """v54 图7：最小化必须在绑定会话之前，否则窗口已经闪过。"""
        script = Path(__file__).parents[1] / "src" / "bosshunter" / "browser" / "runtime" / "cdp-proxy.mjs"
        source = script.read_text(encoding="utf-8")
        new_route = source.split("pathname === '/new'", 1)[1].split("pathname === '/close'", 1)[0]

        self.assertLess(
            new_route.index("minimizeWindowIfNew(targetId, knownWindows)"),
            new_route.index("ensureSession(targetId)"),
            "第一次最小化要在 ensureSession 之前",
        )
        self.assertIn("pathname === '/minimize'", source)

    def test_host_tabs_open_in_background_and_captcha_activates(self):
        """v54 图7：51job/智联宿主标签后台开；验证码 hold 建好后要置前。"""
        root = Path(__file__).parents[1] / "src" / "bosshunter"
        job51 = (root / "collection" / "platforms" / "job51.py").read_text(encoding="utf-8")
        zhilian = (root / "collection" / "platforms" / "zhilian.py").read_text(encoding="utf-8")
        self.assertIn("host_target = self.browser.new_tab(tmp_url, background=True)", job51)
        self.assertIn("host_target = self.browser.new_tab(tmp_url, background=True)", zhilian)
        antibot = (root / "antibot.py").read_text(encoding="utf-8")
        hold_block = antibot.split("create_verification_hold(", 1)[1].split("wait_for_manual_verification(", 1)[0]
        self.assertIn("activate_tab", hold_block)

    def test_check_runtime_requires_bosshunter_runtime_identity(self):
        script = Path(__file__).parents[1] / "src" / "bosshunter" / "browser" / "runtime" / "check-runtime.mjs"
        source = script.read_text(encoding="utf-8")

        self.assertIn("/health", source)
        self.assertIn("runtime === 'bosshunter'", source)


if __name__ == "__main__":
    unittest.main()
