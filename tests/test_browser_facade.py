import unittest
from unittest.mock import Mock, patch


class BrowserFacadeTests(unittest.TestCase):
    @patch("bosshunter.browser.client.httpx.get")
    def test_runtime_client_allows_slow_new_tab_creation(self, get):
        from bosshunter.browser.client import RuntimeClient

        get.return_value.status_code = 200
        get.return_value.json.return_value = {"targetId": "target-1"}

        self.assertEqual(RuntimeClient({}).new_tab("https://example.com"), "target-1")
        self.assertEqual(get.call_args.kwargs["timeout"], 30)

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_new_tab_returns_target_id_from_runtime_client(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.new_tab.return_value = "target-1"

        result = browser.new_tab("https://example.com")

        self.assertEqual(result, "target-1")
        ensure_runtime.assert_called_once()
        client_cls.return_value.new_tab.assert_called_once_with("https://example.com", background=False)

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_new_tab_can_open_in_background(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.new_tab.return_value = "target-1"

        result = browser.new_tab("https://example.com", background=True)

        self.assertEqual(result, "target-1")
        client_cls.return_value.new_tab.assert_called_once_with("https://example.com", background=True)

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_new_tab_returns_none_when_runtime_unavailable(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = False

        result = browser.new_tab("https://example.com")

        self.assertIsNone(result)
        client_cls.assert_not_called()

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_evaluate_returns_runtime_value(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.evaluate.return_value = {"title": "ok"}

        result = browser.evaluate("target-1", "document.title", timeout=12)

        self.assertEqual(result, {"title": "ok"})
        client_cls.return_value.evaluate.assert_called_once_with("target-1", "document.title", 12)

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_click_returns_boolean(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.click.return_value = True

        self.assertTrue(browser.click("target-1", "button"))

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_type_text_can_request_human_input_events(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.type_text.return_value = True

        self.assertTrue(browser.type_text("target-1", "hello", human=True))
        client_cls.return_value.type_text.assert_called_once_with("target-1", "hello", human=True)

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_press_key_delegates_to_runtime_client(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.press_key.return_value = True

        self.assertTrue(browser.press_key("target-1", "SelectAll"))
        client_cls.return_value.press_key.assert_called_once_with("target-1", "SelectAll")

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_get_page_info_returns_dict(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.info.return_value = {"title": "T", "url": "https://example.com", "ready": "complete"}

        result = browser.get_page_info("target-1")

        self.assertEqual(result["ready"], "complete")

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_wait_for_load_polls_until_ready_complete(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.info.side_effect = [
            {"ready": "loading"},
            {"ready": "complete"},
        ]

        with patch("bosshunter.browser.time.sleep"):
            self.assertTrue(browser.wait_for_load("target-1", timeout=2))

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_wait_for_load_returns_quickly_when_stop_event_is_set(self, ensure_runtime, client_cls):
        import threading
        import time

        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.info.return_value = {"ready": "loading"}
        stop_event = threading.Event()
        stop_event.set()

        started = time.monotonic()
        self.assertFalse(browser.wait_for_load("target-1", timeout=10, stop_event=stop_event))
        self.assertLess(time.monotonic() - started, 1.0)

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_wait_for_load_returns_quickly_when_stop_event_set_mid_wait(self, ensure_runtime, client_cls):
        import threading
        import time

        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.info.return_value = {"ready": "loading"}
        stop_event = threading.Event()
        threading.Timer(0.2, stop_event.set).start()

        started = time.monotonic()
        self.assertFalse(browser.wait_for_load("target-1", timeout=10, stop_event=stop_event))
        self.assertLess(time.monotonic() - started, 1.0)

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_wait_for_load_without_stop_event_keeps_timeout_behavior(self, ensure_runtime, client_cls):
        import time

        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.info.return_value = {"ready": "loading"}

        started = time.monotonic()
        self.assertFalse(browser.wait_for_load("target-1", timeout=1))
        self.assertGreaterEqual(time.monotonic() - started, 1.0)

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_check_chrome_connection_returns_health(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.health.return_value = {"status": "ok", "runtime": "bosshunter"}

        result = browser.check_chrome_connection()

        self.assertEqual(result["runtime"], "bosshunter")

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_find_boss_tab_matches_zhipin_url(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.targets.return_value = [
            {"targetId": "1", "url": "https://example.com"},
            {"targetId": "2", "url": "https://www.zhipin.com/web/geek/job"},
        ]

        result = browser.find_boss_tab()

        self.assertEqual(result["targetId"], "2")

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_print_pdf_delegates_to_client(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.print_pdf.return_value = True

        self.assertTrue(browser.print_pdf("target-1", "out.pdf"))

    @patch("bosshunter.browser.client.httpx.get")
    def test_runtime_client_activate_hits_activate_endpoint(self, get):
        from bosshunter.browser.client import RuntimeClient

        get.return_value.status_code = 200

        self.assertTrue(RuntimeClient({}).activate("target-1"))
        called_url = str(get.call_args.args[0])
        self.assertTrue(called_url.endswith("/activate"), called_url)
        self.assertEqual(get.call_args.kwargs["params"], {"target": "target-1"})


class OpenPlatformTabTests(unittest.TestCase):
    """v27: 去登录复用已打开的对应标签页，不再每次新开一个。"""

    _BOSS_LOGIN = "https://www.zhipin.com/web/user/login"

    def _setup(self, ensure_runtime, client_cls, *, targets):
        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.targets.return_value = targets
        client_cls.return_value.new_tab.return_value = "t-new"
        client_cls.return_value.navigate.return_value = True
        client_cls.return_value.activate.return_value = True
        return browser

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    @patch("bosshunter.browser.runtime_targets")
    def test_returns_none_when_runtime_cannot_start(self, runtime_targets, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        runtime_targets.return_value = None
        ensure_runtime.return_value = False

        result = browser.open_platform_tab(self._BOSS_LOGIN)

        self.assertIsNone(result["target_id"])
        self.assertFalse(result["browser_started"])
        self.assertFalse(result["reused_tab"])
        self.assertIn("没连上", result["message"])
        client_cls.return_value.new_tab.assert_not_called()

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    @patch("bosshunter.browser.runtime_targets")
    def test_opens_new_tab_when_browser_already_up(self, runtime_targets, ensure_runtime, client_cls):
        runtime_targets.return_value = [{"targetId": "x", "url": "https://example.com"}]
        browser = self._setup(ensure_runtime, client_cls, targets=[])

        result = browser.open_platform_tab(self._BOSS_LOGIN)

        self.assertEqual(result["target_id"], "t-new")
        self.assertFalse(result["browser_started"])
        self.assertFalse(result["reused_tab"])
        client_cls.return_value.new_tab.assert_called_once_with(self._BOSS_LOGIN)

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    @patch("bosshunter.browser.runtime_targets")
    def test_reports_browser_started_when_it_was_down(self, runtime_targets, ensure_runtime, client_cls):
        runtime_targets.return_value = None
        browser = self._setup(ensure_runtime, client_cls, targets=[])

        result = browser.open_platform_tab(self._BOSS_LOGIN)

        self.assertEqual(result["target_id"], "t-new")
        self.assertTrue(result["browser_started"])
        self.assertFalse(result["reused_tab"])

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    @patch("bosshunter.browser.runtime_targets")
    def test_reuses_exact_login_tab_without_opening_a_new_one(self, runtime_targets, ensure_runtime, client_cls):
        runtime_targets.return_value = [{"targetId": "t-9", "url": self._BOSS_LOGIN}]
        browser = self._setup(ensure_runtime, client_cls, targets=[
            {"targetId": "t-9", "url": self._BOSS_LOGIN},
        ])

        result = browser.open_platform_tab(self._BOSS_LOGIN)

        self.assertEqual(result["target_id"], "t-9")
        self.assertTrue(result["reused_tab"])
        client_cls.return_value.navigate.assert_not_called()
        client_cls.return_value.activate.assert_called_once_with("t-9")
        client_cls.return_value.new_tab.assert_not_called()

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    @patch("bosshunter.browser.runtime_targets")
    def test_reuses_homepage_tab_by_navigating_it_to_login(self, runtime_targets, ensure_runtime, client_cls):
        runtime_targets.return_value = [{"targetId": "t-9", "url": "https://www.zhipin.com/"}]
        browser = self._setup(ensure_runtime, client_cls, targets=[
            {"targetId": "t-9", "url": "https://www.zhipin.com/"},
        ])

        result = browser.open_platform_tab(self._BOSS_LOGIN)

        self.assertEqual(result["target_id"], "t-9")
        self.assertTrue(result["reused_tab"])
        client_cls.return_value.navigate.assert_called_once_with("t-9", self._BOSS_LOGIN)
        client_cls.return_value.activate.assert_called_once_with("t-9")
        client_cls.return_value.new_tab.assert_not_called()

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    @patch("bosshunter.browser.runtime_targets")
    def test_does_not_hijack_a_job_list_tab(self, runtime_targets, ensure_runtime, client_cls):
        runtime_targets.return_value = [{"targetId": "t-9", "url": "https://www.zhipin.com/web/geek/job"}]
        browser = self._setup(ensure_runtime, client_cls, targets=[
            {"targetId": "t-9", "url": "https://www.zhipin.com/web/geek/job"},
        ])

        result = browser.open_platform_tab(self._BOSS_LOGIN)

        self.assertEqual(result["target_id"], "t-new")
        self.assertFalse(result["reused_tab"])
        client_cls.return_value.navigate.assert_not_called()
        client_cls.return_value.activate.assert_not_called()
        client_cls.return_value.new_tab.assert_called_once_with(self._BOSS_LOGIN)

    @patch("bosshunter.browser.pick_chrome_debug_port", return_value=9222)
    @patch("bosshunter.browser.runtime_health", return_value={"status": "ok", "runtime": "bosshunter", "chromePort": 9222})
    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    @patch("bosshunter.browser.runtime_targets")
    def test_reports_matching_chrome_port_after_open(self, runtime_targets, ensure_runtime, client_cls, health, pick):
        # A5：代理实际连的端口与期望端口一致时 port_mismatch=False。
        runtime_targets.return_value = [{"targetId": "x", "url": "https://example.com"}]
        browser = self._setup(ensure_runtime, client_cls, targets=[])

        result = browser.open_platform_tab(self._BOSS_LOGIN)

        self.assertEqual(result["target_id"], "t-new")
        self.assertFalse(result["port_mismatch"])
        self.assertEqual(result["chrome_port"], 9222)

    @patch("bosshunter.browser.pick_chrome_debug_port", return_value=9222)
    @patch("bosshunter.browser.runtime_health", return_value={"status": "ok", "runtime": "bosshunter", "chromePort": 9229})
    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    @patch("bosshunter.browser.runtime_targets")
    def test_reports_port_mismatch_when_runtime_connected_elsewhere(self, runtime_targets, ensure_runtime, client_cls, health, pick):
        # A5：代理实际连的端口（9229）不是期望的工作浏览器端口（9222）
        # 时上报 port_mismatch=True 与实际端口，不拦这次打开。
        runtime_targets.return_value = [{"targetId": "x", "url": "https://example.com"}]
        browser = self._setup(ensure_runtime, client_cls, targets=[])

        result = browser.open_platform_tab(self._BOSS_LOGIN)

        self.assertEqual(result["target_id"], "t-new")
        self.assertTrue(result["port_mismatch"])
        self.assertEqual(result["chrome_port"], 9229)

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    @patch("bosshunter.browser.runtime_targets")
    def test_ignores_tabs_from_other_sites(self, runtime_targets, ensure_runtime, client_cls):
        runtime_targets.return_value = [{"targetId": "t-9", "url": "https://www.zhaopin.com/"}]
        browser = self._setup(ensure_runtime, client_cls, targets=[
            {"targetId": "t-9", "url": "https://www.zhaopin.com/"},
        ])

        result = browser.open_platform_tab(self._BOSS_LOGIN)

        self.assertEqual(result["target_id"], "t-new")
        self.assertFalse(result["reused_tab"])
        client_cls.return_value.new_tab.assert_called_once_with(self._BOSS_LOGIN)


if __name__ == "__main__":
    unittest.main()
