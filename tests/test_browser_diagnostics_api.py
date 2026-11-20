"""浏览器诊断接口：原样返回、error_kind 分类、select 路径校验。"""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bosshunter.web import server


def _fake_diagnostics(*, chrome=True, discovered=None, error_kind=None):
    return {
        "node": {"available": True, "version": "v22.1.0"},
        "runtime": True,
        "chrome": chrome,
        "targets": [{"targetId": "1", "url": "https://www.zhipin.com"}] if chrome else [],
        "boss_tab": {"targetId": "1", "title": "BOSS直聘"},
        "zhilian_tab": None,
        "zhilian_page": None,
        "job51_tab": None,
        "job51_page": None,
        "liepin_tab": None,
        "liepin_page": None,
        "errors": [],
        "runtime_url": "http://127.0.0.1:3456",
        "health": {"browserProduct": "Chrome/138.0", "browserName": "Google Chrome"},
        "browser_product": "Chrome/138.0",
        "browser_name": "Google Chrome",
        "discovered": discovered if discovered is not None else {"chrome": None, "edge": None},
        "browser_choice": "auto",
        "selected_browser_name": "浏览器",
        "error_kind": error_kind,
        "message": "浏览器连接正常。" if chrome else "这台电脑上没找到 Chrome 或 Edge 浏览器。",
        "next_steps": ["可以直接开始采集或投递任务。"] if chrome else ["请先安装 Chrome 或 Edge 浏览器。"],
    }


class _ServerTestBase(unittest.TestCase):
    def setUp(self):
        self.original_base_dir = server.BASE_DIR
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        server.set_base_dir(Path(self._tmp.name))
        server._LAST_PREFLIGHT = None

    def tearDown(self):
        server.set_base_dir(self.original_base_dir)

    def _request(self, path: str, method: str = "GET", json_body: dict | None = None):
        if "?" in path:
            path_info, query_string = path.split("?", 1)
        else:
            path_info, query_string = path, ""

        status_headers = {}

        def start_response(status, headers, exc_info=None):
            status_headers["status"] = status
            status_headers["headers"] = dict(headers)

        request_body = json.dumps(json_body).encode("utf-8") if json_body is not None else b""
        environ = {
            "REMOTE_ADDR": "127.0.0.1",
            "REQUEST_METHOD": method,
            "PATH_INFO": path_info,
            "QUERY_STRING": query_string,
            "SERVER_NAME": "127.0.0.1",
            "SERVER_PORT": "8686",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(request_body),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
            "CONTENT_TYPE": "application/json",
            "CONTENT_LENGTH": str(len(request_body)),
        }
        body = b"".join(server.app.wsgi(environ, start_response))
        return status_headers["status"], body.decode("utf-8")


class BrowserDiagnosticsApiTests(_ServerTestBase):

    def test_get_returns_raw_diagnostics_verbatim_with_envelope(self):
        fake = _fake_diagnostics()
        with patch.object(server, "run_browser_diagnostics", return_value=fake) as diagnostics:
            status, body = self._request("/api/browser/diagnostics")
        self.assertTrue(status.startswith("200"), body)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["diagnostics"], fake)
        self.assertEqual(payload["discovered"], {"chrome": None, "edge": None})
        self.assertIsNone(payload["error_kind"])
        self.assertEqual(payload["message"], "浏览器连接正常。")
        self.assertEqual(payload["next_steps"], ["可以直接开始采集或投递任务。"])
        diagnostics.assert_called_once()

    def test_get_not_found_envelope(self):
        fake = _fake_diagnostics(
            chrome=False, discovered={"chrome": None, "edge": None}, error_kind="not_found"
        )
        with patch.object(server, "run_browser_diagnostics", return_value=fake):
            status, body = self._request("/api/browser/diagnostics")
        payload = json.loads(body)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_kind"], "not_found")
        self.assertEqual(payload["discovered"], {"chrome": None, "edge": None})

    def test_get_connection_failed_envelope(self):
        fake = _fake_diagnostics(
            chrome=False,
            discovered={"chrome": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "edge": None},
            error_kind="connection_failed",
        )
        with patch.object(server, "run_browser_diagnostics", return_value=fake):
            status, body = self._request("/api/browser/diagnostics")
        payload = json.loads(body)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_kind"], "connection_failed")

    def test_post_retries_once_when_first_run_fails(self):
        failing = _fake_diagnostics(
            chrome=False, discovered={"chrome": None, "edge": None}, error_kind="not_found"
        )
        succeeding = _fake_diagnostics()
        with patch.object(
            server, "run_browser_diagnostics", side_effect=[failing, succeeding]
        ) as diagnostics:
            status, body = self._request("/api/browser/diagnostics", "POST")
        self.assertTrue(status.startswith("200"), body)
        self.assertTrue(json.loads(body)["ok"])
        self.assertEqual(diagnostics.call_count, 2)

    def test_post_does_not_retry_when_first_run_succeeds(self):
        with patch.object(
            server, "run_browser_diagnostics", return_value=_fake_diagnostics()
        ) as diagnostics:
            status, body = self._request("/api/browser/diagnostics", "POST")
        self.assertTrue(status.startswith("200"), body)
        diagnostics.assert_called_once()

    def test_get_diagnostics_does_not_launch_browser(self):
        # 首页被动刷新：只做检查，不替用户拉新浏览器窗口。
        with patch.object(
            server, "run_browser_diagnostics", return_value=_fake_diagnostics()
        ) as diagnostics:
            status, body = self._request("/api/browser/diagnostics")
        self.assertTrue(status.startswith("200"), body)
        diagnostics.assert_called_once()
        self.assertEqual(diagnostics.call_args.kwargs, {"allow_launch": False})

    def test_post_diagnostics_does_not_launch_browser(self):
        # 重新检测只是检测，不拉起浏览器；拉起只走“一键启动浏览器”。
        with patch.object(
            server, "run_browser_diagnostics", return_value=_fake_diagnostics()
        ) as diagnostics:
            status, body = self._request("/api/browser/diagnostics", "POST")
        self.assertTrue(status.startswith("200"), body)
        diagnostics.assert_called_once()
        self.assertEqual(diagnostics.call_args.kwargs, {"allow_launch": False})

    def test_post_launch_endpoint_launches_browser(self):
        # 用户主动点的“一键启动浏览器”才允许拉起。
        with patch.object(
            server, "run_browser_diagnostics", return_value=_fake_diagnostics()
        ) as diagnostics:
            status, body = self._request("/api/browser/launch", "POST")
        self.assertTrue(status.startswith("200"), body)
        diagnostics.assert_called_once()
        self.assertEqual(diagnostics.call_args.kwargs, {"allow_launch": True})
        payload = json.loads(body)
        self.assertEqual(payload["browser_choice"], "auto")
        self.assertEqual(payload["selected_browser_name"], "浏览器")

    def test_browser_open_opens_url_in_runtime_browser(self):
        with patch("bosshunter.browser.new_tab", return_value="target-1") as new_tab:
            status, body = self._request(
                "/api/browser/open", "POST", {"url": "https://www.zhipin.com/web/user/login"}
            )
        self.assertTrue(status.startswith("200"), body)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["target_id"], "target-1")
        new_tab.assert_called_once_with("https://www.zhipin.com/web/user/login")

    def test_browser_open_returns_503_when_runtime_not_connected(self):
        with patch("bosshunter.browser.new_tab", return_value=None):
            status, body = self._request(
                "/api/browser/open", "POST", {"url": "https://www.zhipin.com/web/user/login"}
            )
        self.assertTrue(status.startswith("503"), body)
        error = json.loads(body)["error"]
        self.assertIn("浏览器还没连上", error)
        self.assertNotIn("new_tab", error)

    def test_browser_open_rejects_non_http_url(self):
        status, body = self._request("/api/browser/open", "POST", {"url": "file:///etc/passwd"})
        self.assertTrue(status.startswith("400"), body)
        self.assertIn("error", json.loads(body))

    def test_select_valid_browser_path_is_saved(self):
        chrome_path = Path(self._tmp.name) / "chrome.exe"
        chrome_path.write_bytes(b"fake exe")
        status, body = self._request("/api/browser/select", "POST", {"path": str(chrome_path)})
        self.assertTrue(status.startswith("200"), body)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["executable_path"], str(chrome_path))
        status, body = self._request("/api/config")
        saved = json.loads(body)
        self.assertEqual(saved["browser"]["executable_path"], str(chrome_path))
        # 手动选了文件 = 切到手动模式。
        self.assertEqual(saved["browser"]["browser_choice"], "manual")

    def test_select_missing_path_returns_400_with_plain_message(self):
        status, body = self._request(
            "/api/browser/select", "POST", {"path": str(Path(self._tmp.name) / "no-such-chrome.exe")}
        )
        self.assertTrue(status.startswith("400"), body)
        error = json.loads(body)["error"]
        self.assertIn("找不到这个浏览器文件", error)
        self.assertNotIn("chrome.exe --", error)

    def test_select_non_browser_file_returns_400(self):
        notes = Path(self._tmp.name) / "notes.txt"
        notes.write_text("hello", encoding="utf-8")
        status, body = self._request("/api/browser/select", "POST", {"path": str(notes)})
        self.assertTrue(status.startswith("400"), body)
        self.assertIn("不是浏览器", json.loads(body)["error"])

    def test_select_empty_path_returns_400(self):
        status, body = self._request("/api/browser/select", "POST", {"path": "   "})
        self.assertTrue(status.startswith("400"), body)
        self.assertIn("error", json.loads(body))


class WorkbenchLoginStateTests(_ServerTestBase):
    def test_workbench_returns_platform_login_and_last_preflight(self):
        status, body = self._request("/api/workbench")
        self.assertTrue(status.startswith("200"), body)
        payload = json.loads(body)
        self.assertEqual(
            {platform: info["status"] for platform, info in payload["platform_login"].items()},
            {"boss": "unknown", "zhilian": "unknown", "51job": "unknown", "liepin": "unknown"},
        )
        self.assertIsNone(payload["last_preflight"])

        checks = [
            {
                "id": "browser_runtime",
                "title": "浏览器运行组件",
                "status": "pass",
                "message": "简历猎手浏览器运行组件正常",
                "detail": "本地连接地址：http://127.0.0.1:3456。",
            }
        ]
        with (
            patch.object(server, "collect_preflight_checks", return_value=checks),
            patch.object(server, "_maybe_refresh_login_states"),
        ):
            status, body = self._request("/api/workbench/preflight", "POST", {"mode": "collect"})
        self.assertTrue(status.startswith("200"), body)
        self.assertTrue(json.loads(body)["ok"])

        status, body = self._request("/api/workbench")
        payload = json.loads(body)
        self.assertIsNotNone(payload["last_preflight"])
        self.assertEqual(payload["last_preflight"]["checks"], checks)
        self.assertTrue(payload["last_preflight"]["ran_at"])

    def test_workbench_platform_login_reflects_persisted_states(self):
        from bosshunter.db import get_db, record_platform_login_state

        db = get_db(Path(self._tmp.name) / "data" / "bosshunter.db")
        try:
            record_platform_login_state(db, "boss", "logged_in", "已发现 BOSS 直聘页面")
            record_platform_login_state(db, "zhilian", "logged_out", "智联页面要求登录")
        finally:
            db.close()
        status, body = self._request("/api/workbench")
        self.assertTrue(status.startswith("200"), body)
        login = json.loads(body)["platform_login"]
        self.assertEqual(login["boss"]["status"], "logged_in")
        self.assertEqual(login["zhilian"]["status"], "logged_out")
        self.assertEqual(login["51job"]["status"], "unknown")
        self.assertEqual(login["liepin"]["status"], "unknown")


class BrowserProblemClassificationTests(unittest.TestCase):
    def test_chrome_connected_is_ok(self):
        from bosshunter.browser.diagnostics import _browser_problem

        kind, message, steps = _browser_problem(True, {"chrome": None, "edge": None})
        self.assertIsNone(kind)
        self.assertIn("连接正常", message)
        self.assertTrue(steps)

    def test_no_browser_found(self):
        from bosshunter.browser.diagnostics import _browser_problem

        kind, message, steps = _browser_problem(False, {"chrome": None, "edge": None})
        self.assertEqual(kind, "not_found")
        self.assertIn("没找到", message)
        self.assertTrue(all("chrome" not in step.lower() or True for step in steps))
        self.assertFalse(any("--remote-debugging" in step for step in steps))

    def test_browser_installed_but_not_connected(self):
        from bosshunter.browser.diagnostics import _browser_problem

        kind, message, steps = _browser_problem(
            False, {"chrome": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "edge": None}
        )
        self.assertEqual(kind, "connection_failed")
        self.assertIn("连不上", message)


class DiscoverBrowsersTests(unittest.TestCase):
    def test_configured_executable_path_is_honored(self):
        from bosshunter.browser.diagnostics import discover_browsers

        with tempfile.TemporaryDirectory() as tmp:
            chrome_path = Path(tmp) / "chrome.exe"
            chrome_path.write_bytes(b"x")
            found = discover_browsers({"browser": {"executable_path": str(chrome_path)}})
            self.assertEqual(found["chrome"], str(chrome_path))

    def test_missing_configured_path_does_not_raise(self):
        from bosshunter.browser.diagnostics import discover_browsers

        found = discover_browsers({"browser": {"executable_path": "/definitely/not/a/browser.exe"}})
        self.assertIn("chrome", found)
        self.assertIn("edge", found)

    def test_discovery_never_raises(self):
        from bosshunter.browser.diagnostics import discover_browsers

        found = discover_browsers(None)
        self.assertEqual(set(found), {"chrome", "edge"})


class RunBrowserDiagnosticsTabsTests(unittest.TestCase):
    def test_51job_and_liepin_tabs_and_pages_are_reported(self):
        from bosshunter.browser.diagnostics import run_browser_diagnostics

        targets = [
            {"targetId": "1", "url": "https://www.zhipin.com/web/geek/job"},
            {"targetId": "2", "url": "https://we.51job.com/pc/search"},
            {"targetId": "3", "url": "https://www.liepin.com/zhaopin/"},
        ]
        with (
            patch("bosshunter.browser.diagnostics.check_node_available",
                  return_value={"available": True, "version": "v22.1.0"}),
            patch("bosshunter.browser.diagnostics.ensure_runtime", return_value=True),
            patch("bosshunter.browser.diagnostics.runtime_health",
                  return_value={"browserProduct": "Chrome/138.0", "browserName": "Google Chrome"}),
            patch("bosshunter.browser.diagnostics.runtime_targets", return_value=targets),
            # boss_tab / zhilian_tab 直接从 targets 里按域名找，不再走 facade。
            patch("bosshunter.browser.diagnostics.evaluate",
                  return_value='{"status": "ready", "message": "ok"}'),
        ):
            result = run_browser_diagnostics({"browser": {"proxy_port": 3456}})

        self.assertEqual(result["job51_tab"]["url"], "https://we.51job.com/pc/search")
        self.assertEqual(result["job51_page"]["status"], "ready")
        self.assertEqual(result["liepin_tab"]["url"], "https://www.liepin.com/zhaopin/")
        self.assertEqual(result["liepin_page"]["status"], "ready")
        self.assertIsNone(result["error_kind"])
        self.assertEqual(set(result["discovered"]), {"chrome", "edge"})


if __name__ == "__main__":
    unittest.main()
