"""平台登录态持久化：DB 读写、unknown 默认、upsert 覆盖、熔断联动。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bosshunter.db import (
    breaker_is_suspended,
    get_all_platform_login_states,
    get_db,
    get_platform_login_state,
    record_platform_login_state,
    upsert_platform_login_state,
)
from bosshunter.web.preflight import derive_platform_login_states, refresh_platform_login_states


def _fresh_db(testcase):
    tmp = tempfile.TemporaryDirectory()
    testcase.addCleanup(tmp.cleanup)
    return get_db(Path(tmp.name) / "bosshunter.db")


class PlatformLoginStateDbTests(unittest.TestCase):
    def test_unknown_default_for_all_platforms(self):
        db = _fresh_db(self)
        try:
            states = get_all_platform_login_states(db)
            self.assertEqual(set(states), {"boss", "zhilian", "51job", "liepin"})
            for platform, state in states.items():
                self.assertEqual(state["status"], "unknown")
                self.assertIsNone(state["checked_at"])
                self.assertIsNone(state["detail"])
                self.assertEqual(state["platform"], platform)
            self.assertEqual(get_platform_login_state(db, "boss")["status"], "unknown")
        finally:
            db.close()

    def test_upsert_and_get_round_trip(self):
        db = _fresh_db(self)
        try:
            upsert_platform_login_state(db, "boss", "logged_in", "已发现 BOSS 直聘页面")
            state = get_platform_login_state(db, "boss")
            self.assertEqual(state["status"], "logged_in")
            self.assertEqual(state["detail"], "已发现 BOSS 直聘页面")
            self.assertTrue(state["checked_at"])
        finally:
            db.close()

    def test_upsert_overwrites_previous_state(self):
        db = _fresh_db(self)
        try:
            upsert_platform_login_state(db, "zhilian", "logged_in", "第一次")
            upsert_platform_login_state(db, "zhilian", "logged_out", "第二次掉线")
            state = get_platform_login_state(db, "zhilian")
            self.assertEqual(state["status"], "logged_out")
            self.assertEqual(state["detail"], "第二次掉线")
            self.assertEqual(len(get_all_platform_login_states(db)), 4)
        finally:
            db.close()

    def test_invalid_status_is_rejected(self):
        db = _fresh_db(self)
        try:
            with self.assertRaises(ValueError):
                upsert_platform_login_state(db, "boss", "bogus")
            with self.assertRaises(ValueError):
                record_platform_login_state(db, "boss", "bogus")
        finally:
            db.close()

    def test_logged_out_suspends_platform_for_today(self):
        db = _fresh_db(self)
        try:
            self.assertFalse(breaker_is_suspended(db, "boss"))
            record_platform_login_state(db, "boss", "logged_out", "页面停在登录页")
            self.assertEqual(get_platform_login_state(db, "boss")["status"], "logged_out")
            self.assertTrue(breaker_is_suspended(db, "boss"))
        finally:
            db.close()

    def test_repeated_logged_out_does_not_stack_breaker(self):
        db = _fresh_db(self)
        try:
            record_platform_login_state(db, "liepin", "logged_out", "掉线")
            failures = db.execute(
                "SELECT consecutive_failures FROM platform_breakers WHERE platform = ?",
                ("liepin",),
            ).fetchone()["consecutive_failures"]
            record_platform_login_state(db, "liepin", "logged_out", "仍掉线")
            record_platform_login_state(db, "liepin", "logged_out", "还掉线")
            again = db.execute(
                "SELECT consecutive_failures FROM platform_breakers WHERE platform = ?",
                ("liepin",),
            ).fetchone()["consecutive_failures"]
            self.assertEqual(failures, 1)
            self.assertEqual(again, 1)
            self.assertTrue(breaker_is_suspended(db, "liepin"))
        finally:
            db.close()

    def test_logged_in_does_not_clear_breaker_suspension(self):
        db = _fresh_db(self)
        try:
            record_platform_login_state(db, "boss", "logged_out", "掉线")
            self.assertTrue(breaker_is_suspended(db, "boss"))
            record_platform_login_state(db, "boss", "logged_in", "又登录了")
            self.assertEqual(get_platform_login_state(db, "boss")["status"], "logged_in")
            # 熔断挂起的当天任务按原有规则到当天结束才恢复，不因一次
            # 登录态回升就悄悄清掉。
            self.assertTrue(breaker_is_suspended(db, "boss"))
        finally:
            db.close()


class DerivePlatformLoginStatesTests(unittest.TestCase):
    def _result(self, **overrides):
        result = {
            "chrome": True,
            "boss_tab": {"targetId": "1", "url": "https://www.zhipin.com/web/geek/job"},
            "zhilian_tab": {"targetId": "2", "url": "https://www.zhaopin.com/"},
            "zhilian_page": {"status": "ready", "message": "已发现智联搜索框，未发现登录墙"},
            "job51_tab": {"targetId": "3", "url": "https://we.51job.com/pc/search"},
            "job51_page": {"status": "ready", "message": "已发现前程无忧搜索框，未发现登录墙"},
            "liepin_tab": {"targetId": "4", "url": "https://www.liepin.com/zhaopin/"},
            "liepin_page": {"status": "ready", "message": "已发现猎聘搜索框，未发现登录墙"},
        }
        result.update(overrides)
        return result

    def test_all_logged_in_when_pages_ready(self):
        states = derive_platform_login_states(self._result())
        self.assertEqual(
            {platform: state["status"] for platform, state in states.items()},
            {"boss": "logged_in", "zhilian": "logged_in", "51job": "logged_in", "liepin": "logged_in"},
        )

    def test_boss_login_url_is_logged_out(self):
        states = derive_platform_login_states(
            self._result(boss_tab={"targetId": "1", "url": "https://www.zhipin.com/web/user/login"})
        )
        self.assertEqual(states["boss"]["status"], "logged_out")

    def test_login_required_pages_are_logged_out(self):
        states = derive_platform_login_states(
            self._result(
                zhilian_page={"status": "login_required", "message": "智联页面要求登录"},
                job51_page={"status": "login_required", "message": "前程无忧页面要求登录"},
                liepin_page={"status": "login_required", "message": "猎聘页面要求登录"},
            )
        )
        self.assertEqual(states["zhilian"]["status"], "logged_out")
        self.assertEqual(states["51job"]["status"], "logged_out")
        self.assertEqual(states["liepin"]["status"], "logged_out")

    def test_blocked_or_missing_pages_are_unknown(self):
        states = derive_platform_login_states(
            self._result(
                boss_tab=None,
                zhilian_page={"status": "blocked", "message": "智联页面受到拦截"},
                job51_tab=None,
                job51_page=None,
                liepin_page={"status": "selector_changed", "message": "未识别到猎聘搜索页结构"},
            )
        )
        self.assertEqual(states["boss"]["status"], "unknown")
        self.assertEqual(states["zhilian"]["status"], "unknown")
        self.assertEqual(states["51job"]["status"], "unknown")
        self.assertEqual(states["liepin"]["status"], "unknown")

    def test_browser_not_connected_is_all_unknown(self):
        states = derive_platform_login_states({"chrome": False})
        self.assertTrue(all(state["status"] == "unknown" for state in states.values()))

    def test_refresh_persists_states_and_suspends_logged_out(self):
        db = _fresh_db(self)
        try:
            states = refresh_platform_login_states(
                db,
                result=self._result(
                    zhilian_page={"status": "login_required", "message": "智联页面要求登录"},
                ),
            )
            self.assertEqual(states["zhilian"]["status"], "logged_out")
            self.assertEqual(get_platform_login_state(db, "zhilian")["status"], "logged_out")
            self.assertEqual(get_platform_login_state(db, "boss")["status"], "logged_in")
            self.assertTrue(breaker_is_suspended(db, "zhilian"))
            self.assertFalse(breaker_is_suspended(db, "boss"))
        finally:
            db.close()

    def test_refresh_when_browser_down_writes_unknown_without_suspension(self):
        db = _fresh_db(self)
        try:
            states = refresh_platform_login_states(db, result={"chrome": False})
            self.assertTrue(all(state["status"] == "unknown" for state in states.values()))
            self.assertTrue(
                all(
                    get_platform_login_state(db, platform)["status"] == "unknown"
                    for platform in ("boss", "zhilian", "51job", "liepin")
                )
            )
            self.assertFalse(any(breaker_is_suspended(db, p) for p in ("boss", "zhilian", "51job", "liepin")))
        finally:
            db.close()


class NeedsLoginMarksLoggedOutTests(unittest.TestCase):
    def test_sender_page_risk_login_required_marks_logged_out(self):
        from bosshunter.executor.sender import _page_risk_result as sender_risk

        db = _fresh_db(self)
        try:
            with patch("bosshunter.executor.sender.handle_page_risk", return_value="login_required"):
                result = sender_risk("target-1", "boss", {"_db": db})
            self.assertEqual(result["error"], "needs_login")
            self.assertEqual(get_platform_login_state(db, "boss")["status"], "logged_out")
            self.assertTrue(breaker_is_suspended(db, "boss"))
        finally:
            db.close()

    def test_sender_page_risk_without_db_still_returns_needs_login(self):
        from bosshunter.executor.sender import _page_risk_result as sender_risk

        with patch("bosshunter.executor.sender.handle_page_risk", return_value="login_required"):
            result = sender_risk("target-1", "boss", {})
        self.assertEqual(result["error"], "needs_login")

    def test_platform_delivery_page_risk_login_required_marks_logged_out(self):
        from bosshunter.executor.platform_delivery import _page_risk_result as delivery_risk

        db = _fresh_db(self)
        try:
            with patch(
                "bosshunter.executor.platform_delivery.handle_page_risk",
                return_value="login_required",
            ):
                result = delivery_risk("target-1", "zhilian", {"_db": db})
            self.assertEqual(result["error"], "needs_login")
            self.assertEqual(get_platform_login_state(db, "zhilian")["status"], "logged_out")
            self.assertTrue(breaker_is_suspended(db, "zhilian"))
        finally:
            db.close()


class PlatformLoginUrlConstantsTests(unittest.TestCase):
    def test_login_urls(self):
        from bosshunter.config import (
            DEFAULTS,
            PLATFORM_LOGIN_URL_IS_HOMEPAGE,
            PLATFORM_LOGIN_URLS,
        )

        self.assertEqual(
            PLATFORM_LOGIN_URLS["boss"], "https://www.zhipin.com/web/user/login"
        )
        # 智联 / 51job / 猎聘未核实到专用登录页直链，用已验证的官网首页作入口。
        self.assertEqual(PLATFORM_LOGIN_URLS["zhilian"], "https://www.zhaopin.com/")
        self.assertEqual(PLATFORM_LOGIN_URLS["51job"], "https://www.51job.com/")
        self.assertEqual(PLATFORM_LOGIN_URLS["liepin"], "https://www.liepin.com/")
        self.assertEqual(
            PLATFORM_LOGIN_URL_IS_HOMEPAGE,
            {"boss": False, "zhilian": True, "51job": True, "liepin": True},
        )
        self.assertEqual(DEFAULTS["browser"]["executable_path"], "")


if __name__ == "__main__":
    unittest.main()
