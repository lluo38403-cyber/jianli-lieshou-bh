import io
import unittest
from unittest.mock import Mock, patch

from rich.console import Console


class BrowserDiagnosticsTests(unittest.TestCase):
    def test_zhilian_page_script_recognizes_current_login_wall_markers(self):
        from bosshunter.browser.diagnostics import ZHILIAN_PAGE_STATE_SCRIPT

        self.assertIn("登录查看更多", ZHILIAN_PAGE_STATE_SCRIPT)
        self.assertIn("立即登录", ZHILIAN_PAGE_STATE_SCRIPT)

    @patch("bosshunter.browser.diagnostics.httpx.get")
    def test_browser_identity_detects_edge_from_older_runtime_health(self, http_get):
        from bosshunter.browser.diagnostics import _browser_identity

        response = Mock()
        response.json.return_value = {"Browser": "Edg/138.0"}
        http_get.return_value = response

        product, name = _browser_identity({"chromePort": 9222})

        self.assertEqual(product, "Edg/138.0")
        self.assertEqual(name, "Microsoft Edge")

    @patch("bosshunter.browser.diagnostics.runtime_targets")
    @patch("bosshunter.browser.diagnostics.ensure_runtime")
    @patch("bosshunter.browser.diagnostics.check_node_available")
    def test_run_browser_diagnostics_reports_ready_runtime(self, check_node, ensure_runtime, runtime_targets):
        from bosshunter.browser.diagnostics import run_browser_diagnostics

        check_node.return_value = {"available": True, "version": "v22.1.0"}
        ensure_runtime.return_value = True
        # boss_tab 现在直接从本轮已取到的 targets 里按域名找，不再走
        # facade 的 find_boss_tab（那条路会拉起浏览器）。
        runtime_targets.return_value = [{"targetId": "1", "url": "https://www.zhipin.com", "title": "BOSS直聘"}]

        result = run_browser_diagnostics({"browser": {"proxy_port": 3456}})

        self.assertTrue(result["node"]["available"])
        self.assertTrue(result["runtime"])
        self.assertTrue(result["chrome"])
        self.assertEqual(result["boss_tab"]["title"], "BOSS直聘")
        self.assertEqual(result["runtime_url"], "http://127.0.0.1:3456")

    @patch("bosshunter.browser.diagnostics.evaluate")
    @patch("bosshunter.browser.diagnostics.runtime_health")
    @patch("bosshunter.browser.diagnostics.runtime_targets")
    @patch("bosshunter.browser.diagnostics.ensure_runtime")
    @patch("bosshunter.browser.diagnostics.check_node_available")
    def test_multi_tab_prefers_logged_in_page_over_login_wall(
        self, check_node, ensure_runtime, runtime_targets, runtime_health, evaluate
    ):
        # v37②b：同站多个标签页时（登录页 + 已登录的搜索页并存），只查
        # 第一个会把已登录误判成未登录——取状态最好的那一页。
        from bosshunter.browser.diagnostics import run_browser_diagnostics

        check_node.return_value = {"available": True, "version": "v22.1.0"}
        ensure_runtime.return_value = True
        runtime_health.return_value = {"runtime": "bosshunter", "browserName": "Google Chrome"}
        runtime_targets.return_value = [
            {"targetId": "1", "url": "https://www.zhipin.com/web/user/login", "title": "登录"},
            {"targetId": "2", "url": "https://www.zhipin.com/web/geek/job", "title": "BOSS直聘"},
            {"targetId": "3", "url": "https://www.zhaopin.com/", "title": "智联"},
            {"targetId": "4", "url": "https://www.zhaopin.com/job/", "title": "智联职位"},
        ]

        def _page_state(target_id, script, timeout=5):
            if target_id == "3":
                return {"status": "login_required", "message": "智联页面要求登录"}
            return {"status": "ready", "message": "已发现智联搜索框，未发现登录墙"}

        evaluate.side_effect = _page_state

        result = run_browser_diagnostics({"browser": {"proxy_port": 3456}})

        # BOSS：登录页在前，已登录的岗位页在后——取岗位页。
        self.assertEqual(result["boss_tab"]["targetId"], "2")
        # 智联：登录墙页在前，已登录的搜索页在后——取搜索页。
        self.assertEqual(result["zhilian_tab"]["targetId"], "4")
        self.assertEqual(result["zhilian_page"]["status"], "ready")

    @patch("bosshunter.browser.diagnostics.runtime_health")
    @patch("bosshunter.browser.diagnostics.runtime_targets")
    @patch("bosshunter.browser.diagnostics.ensure_runtime")
    @patch("bosshunter.browser.diagnostics.check_node_available")
    def test_boss_tab_falls_back_to_login_page_when_nothing_else_open(
        self, check_node, ensure_runtime, runtime_targets, runtime_health
    ):
        from bosshunter.browser.diagnostics import run_browser_diagnostics

        check_node.return_value = {"available": True, "version": "v22.1.0"}
        ensure_runtime.return_value = True
        runtime_health.return_value = {"runtime": "bosshunter", "browserName": "Google Chrome"}
        runtime_targets.return_value = [
            {"targetId": "1", "url": "https://www.zhipin.com/web/user/login", "title": "登录"},
        ]

        result = run_browser_diagnostics({"browser": {"proxy_port": 3456}})

        self.assertEqual(result["boss_tab"]["targetId"], "1")

    @patch("bosshunter.browser.diagnostics.runtime_targets")
    @patch("bosshunter.browser.diagnostics.runtime_health")
    @patch("bosshunter.browser.diagnostics.ensure_runtime")
    @patch("bosshunter.browser.diagnostics.check_node_available")
    def test_run_browser_diagnostics_reports_updated_url_after_runtime_port_fallback(
        self,
        check_node,
        ensure_runtime,
        runtime_health,
        runtime_targets,
    ):
        from bosshunter.browser.diagnostics import run_browser_diagnostics

        config = {"browser": {"proxy_port": 3456}}
        check_node.return_value = {"available": True, "version": "v22.1.0"}
        ensure_runtime.side_effect = lambda cfg, **kwargs: cfg["browser"].update({"proxy_port": 3457}) or True
        runtime_health.return_value = {"status": "ok", "runtime": "bosshunter"}
        runtime_targets.return_value = [{"targetId": "1", "url": "https://www.zhipin.com", "title": "BOSS直聘"}]

        result = run_browser_diagnostics(config)

        self.assertTrue(result["runtime"])
        self.assertEqual(result["runtime_url"], "http://127.0.0.1:3457")
        self.assertFalse(any("Runtime port is occupied" in error for error in result["errors"]))

    @patch("bosshunter.browser.diagnostics.ensure_runtime")
    @patch("bosshunter.browser.diagnostics.check_node_available")
    def test_run_browser_diagnostics_reports_missing_node(self, check_node, ensure_runtime):
        from bosshunter.browser.diagnostics import run_browser_diagnostics

        check_node.return_value = {"available": False, "version": None, "error": "node missing"}
        ensure_runtime.return_value = False

        result = run_browser_diagnostics({})

        self.assertFalse(result["node"]["available"])
        self.assertFalse(result["runtime"])
        self.assertIn("Node.js", result["errors"][0])

    @patch("bosshunter.browser.diagnostics.runtime_targets")
    @patch("bosshunter.browser.diagnostics.ensure_runtime")
    @patch("bosshunter.browser.diagnostics.check_node_available")
    def test_passive_diagnostics_waits_shorter_than_launch(self, check_node, ensure_runtime, runtime_targets):
        """被动检查（首页/设置页每次打开都调）只等 5 秒：runtime 没起来
        时 15 秒白等还会重试一次，单次调用能占服务端线程 30 多秒；
        主动拉起才保持 15 秒。"""
        from bosshunter.browser.diagnostics import run_browser_diagnostics

        check_node.return_value = {"available": True, "version": "v22.1.0"}
        ensure_runtime.return_value = False
        runtime_targets.return_value = None

        run_browser_diagnostics({"browser": {"proxy_port": 3456}}, allow_launch=False)
        _, passive_kwargs = ensure_runtime.call_args
        self.assertEqual(passive_kwargs.get("wait_seconds"), 5.0)
        self.assertEqual(passive_kwargs.get("allow_launch"), False)

        run_browser_diagnostics({"browser": {"proxy_port": 3456}}, allow_launch=True)
        _, launch_kwargs = ensure_runtime.call_args
        self.assertEqual(launch_kwargs.get("wait_seconds"), 15.0)
        self.assertEqual(launch_kwargs.get("allow_launch"), True)

    @patch("bosshunter.browser.diagnostics.runtime_targets")
    @patch("bosshunter.browser.diagnostics.ensure_runtime")
    @patch("bosshunter.browser.diagnostics.check_node_available")
    def test_running_runtime_does_not_require_node_on_path(
        self,
        check_node,
        ensure_runtime,
        runtime_targets,
    ):
        from bosshunter.browser.diagnostics import run_browser_diagnostics

        check_node.return_value = {"available": False, "version": None, "error": "node missing"}
        ensure_runtime.return_value = True
        runtime_targets.return_value = [{"targetId": "1", "url": "https://www.zhipin.com", "title": "BOSS直聘"}]

        result = run_browser_diagnostics({})

        self.assertTrue(result["runtime"])
        self.assertFalse(any("Node.js" in error for error in result["errors"]))

    @patch("bosshunter.browser.diagnostics.runtime_health")
    @patch("bosshunter.browser.diagnostics.ensure_runtime")
    @patch("bosshunter.browser.diagnostics.check_node_available")
    def test_run_browser_diagnostics_reports_non_bosshunter_service_on_runtime_port(self, check_node, ensure_runtime, runtime_health):
        from bosshunter.browser.diagnostics import run_browser_diagnostics

        check_node.return_value = {"available": True, "version": "v22.1.0"}
        ensure_runtime.return_value = False
        runtime_health.return_value = {"status": "ok", "connected": True}

        result = run_browser_diagnostics({})

        self.assertFalse(result["runtime"])
        self.assertTrue(any("non-BossHunter" in error for error in result["errors"]))

    @patch("bosshunter.browser.diagnostics.run_browser_diagnostics")
    def test_print_browser_diagnostics_shows_non_bosshunter_service_message(self, run_browser_diagnostics):
        from bosshunter.browser.diagnostics import print_browser_diagnostics

        run_browser_diagnostics.return_value = {
            "node": {"available": True, "version": "v22.1.0"},
            "runtime": False,
            "chrome": False,
            "targets": [],
            "boss_tab": None,
            "errors": ["Runtime port is occupied by a non-BossHunter service."],
            "runtime_url": "http://127.0.0.1:3456",
            "health": {"status": "ok", "connected": True},
        }
        buffer = io.StringIO()
        console = Console(file=buffer, force_terminal=False, color_system=None, width=120)

        result = print_browser_diagnostics({}, console)

        self.assertFalse(result)
        self.assertIn("端口已被非 BossHunter 服务占用", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
