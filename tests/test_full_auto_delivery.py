"""全自动投递回归测试：无浏览器（浏览器调用全部 stub）、无真实外发。

覆盖：无 AI Key 时规则评分兜底、四平台分发、静默模式、自动确认、
每日 120 上限、匹配度阈值 60、预检不强制 AI。
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bosshunter.ai.scorer import _score_job_with_ai
from bosshunter.db import (
    add_history,
    auto_approve_pending,
    get_db,
    get_jobs_ready_to_send,
    insert_job,
    update_job_greeting,
    update_job_score,
    update_job_status,
)
from bosshunter.executor import platform_delivery, sender
from bosshunter.web import server
from bosshunter.web.preflight import collect_preflight_checks

BASE_JOB = {
    "title": "Python 后端工程师",
    "company": "测试公司",
    "salary": "15-25K",
    "experience": "3-5年",
    "city": "上海",
    "education": "本科",
    "jd": "负责 Python 后端开发，要求熟悉 FastAPI。",
    "url": "https://jobs.zhaopin.com/test",
}

NO_KEY_CONFIG = {
    "profile": {
        "target_roles": ["Python 后端"],
        "target_cities": ["上海"],
        "education": "本科",
        "salary_min": 10,
        "salary_max": 30,
        "job_types": ["全职"],
        "posted_days": 7,
        # 无 Key 用户默认关闭智能定制打招呼，走固定文案（与配置默认值一致）。
        "ai_greeting_enabled": False,
        "fixed_greeting": "您好，我对贵公司的这个岗位很感兴趣，简历已附上，期待与您进一步沟通，谢谢！",
    },
    "search": {"keywords": ["Python 后端"]},
    "ai": {},
}


def _seed_job(db, job_id, platform="boss", status="ready", score=0, greeting=None):
    job = dict(BASE_JOB, id=job_id, source_platform=platform)
    insert_job(db, job)
    if score:
        update_job_score(db, job_id, score, "测试评分")
    update_job_status(db, job_id, status)
    if greeting:
        update_job_greeting(db, job_id, greeting)
    return job


class RuleScoreFallbackTests(unittest.TestCase):
    def test_no_key_scores_by_rule_when_ai_returns_nothing(self):
        with patch("bosshunter.ai.scorer.get_ai_api_key", return_value=""):
            outcome = _score_job_with_ai(dict(BASE_JOB), "简历摘要", NO_KEY_CONFIG, max_attempts=1)
        self.assertGreater(outcome.result.score, 0)
        self.assertFalse(outcome.result.structured)
        self.assertIn("规则评分", outcome.result.reason)

    def test_rule_score_calibration(self):
        """校准：标题命中+薪资符合过线；无命中/JD 弱命中不过线。"""
        from bosshunter.ai.prefilter import rule_score

        perfect, _ = rule_score(dict(BASE_JOB), NO_KEY_CONFIG)
        self.assertGreaterEqual(perfect, 60, "标题命中+薪资+城市+学历全符合必须过 60 门槛")

        no_keyword, _ = rule_score(
            dict(BASE_JOB, title="电话销售代表", jd="负责电话销售，维护客户关系。",
                 salary="15-25K", city="上海", education="本科"),
            NO_KEY_CONFIG,
        )
        self.assertLess(no_keyword, 60, "无关键词命中时薪资/城市/学历再符合也不能过线")

        jd_only, _ = rule_score(
            dict(BASE_JOB, title="销售代表", jd="负责 Python 后端开发，要求熟悉 FastAPI。",
                 salary="15-25K", city="北京", education="大专"),
            NO_KEY_CONFIG,
        )
        self.assertLess(jd_only, 60, "仅 JD 命中且城市/学历不符合时不过线")


class ReadyToSendTests(unittest.TestCase):
    def test_non_boss_job_needs_no_greeting(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "jobs.db")
            _seed_job(db, "zl-1", platform="zhilian", status="ready", score=80)
            _seed_job(db, "boss-1", platform="boss", status="ready", score=80)
            _seed_job(db, "boss-2", platform="boss", status="ready", score=80, greeting="您好")
            ready = {job["id"] for job in get_jobs_ready_to_send(db)}
            self.assertIn("zl-1", ready)
            self.assertNotIn("boss-1", ready)
            self.assertIn("boss-2", ready)
            db.close()


class DispatchTests(unittest.TestCase):
    def test_boss_goes_to_greeting_flow(self):
        job = dict(BASE_JOB, source_platform="boss")
        with patch.object(sender, "_send_greeting_once", return_value=({"success": True}, None)) as greet, \
                patch.object(sender, "deliver_job") as deliver:
            result, _ = sender._dispatch_send(job, "您好", {})
        self.assertTrue(result["success"])
        greet.assert_called_once_with(job, "您好", {})
        deliver.assert_not_called()

    def test_boss_silent_mode_sends_no_custom_greeting(self):
        job = dict(BASE_JOB, source_platform="boss")
        with patch.object(sender, "_send_greeting_once", return_value=({"success": True}, None)) as greet:
            sender._dispatch_send(job, "您好，我是求职者", {"silent_mode": True})
        greet.assert_called_once_with(job, "", {"silent_mode": True})

    def test_other_platforms_go_to_detail_page_delivery(self):
        for platform in ("zhilian", "51job", "liepin"):
            with self.subTest(platform=platform):
                job = dict(BASE_JOB, source_platform=platform)
                with patch.object(sender, "_send_greeting_once") as greet, \
                        patch.object(sender, "deliver_job", return_value=({"success": True}, None)) as deliver:
                    result, _ = sender._dispatch_send(job, "", {})
                self.assertTrue(result["success"])
                deliver.assert_called_once_with(job, {})
                greet.assert_not_called()


class PlatformDeliveryTests(unittest.TestCase):
    def _run_delivery(self, platform, evaluate_responses):
        job = dict(BASE_JOB, source_platform=platform,
                   url=f"https://example.com/{platform}/job-1")
        calls = {"evaluate": list(evaluate_responses)}
        with patch.object(platform_delivery, "new_tab", return_value="tab-1"), \
                patch.object(platform_delivery, "wait_for_load", return_value=True), \
                patch.object(platform_delivery, "close_tab", return_value=True), \
                patch.object(platform_delivery, "evaluate", side_effect=lambda _t, _js: calls["evaluate"].pop(0)):
            return platform_delivery.deliver_job(job, {})

    def test_zhilian_apply_flow_success(self):
        result, _ = self._run_delivery("zhilian", [
            {"state": "ok"},                       # 页面状态
            {"applied": False},                    # 事前核验：未投过
            {"success": True, "clicked": "投递简历"},  # 点投递按钮
            {"success": True, "actions": []},      # 确认弹窗处理
            {"applied": True, "how": "button_text"},  # 事后核验：已投递
        ])
        self.assertTrue(result["success"])

    def test_no_apply_button_reports_clear_error(self):
        result, _ = self._run_delivery("51job", [
            {"state": "ok"},
            {"applied": False},
            *[{"success": False} for _ in range(10)],  # 10 次都没找到按钮
            {"state": "ok"},
        ])
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "no_apply_button")

    def test_login_wall_reports_needs_login(self):
        result, _ = self._run_delivery("liepin", [{"state": "login_wall"}])
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "needs_login")


class AutoApproveTests(unittest.TestCase):
    def test_threshold_blocks_below_60(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "jobs.db")
            _seed_job(db, "low", status="ready", score=59)
            _seed_job(db, "edge", status="ready", score=60)
            _seed_job(db, "high", status="ready", score=85)
            approved = auto_approve_pending(db, threshold=60)
            self.assertEqual(set(approved), {"edge", "high"})
            statuses = {row["id"]: row["status"] for row in db.execute("SELECT id, status FROM jobs").fetchall()}
            self.assertEqual(statuses["low"], "ready")
            self.assertEqual(statuses["edge"], "approved")
            self.assertEqual(statuses["high"], "approved")
            reasons = db.execute(
                "SELECT detail FROM history WHERE action='approved' AND job_id='high'"
            ).fetchone()
            self.assertIn("全自动", reasons["detail"])
            db.close()


class DailyLimitTests(unittest.TestCase):
    def test_daily_120_cap_stops_sending(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "jobs.db"
            db = get_db(db_path)
            _seed_job(db, "boss-1", status="ready", score=80, greeting="您好")
            for _ in range(120):
                add_history(db, "boss-1", "sent", "测试已发")
            db.close()
            config = {"throttle": {"daily_limit": 120}}
            with patch.object(sender, "_send_greeting_once") as outbound:
                sent = sender.send_greetings(config, db_path=db_path)
            self.assertEqual(sent, 0)
            outbound.assert_not_called()
            self.assertEqual(config["_workbench_send_report"]["stop_reason"], "daily_limit")
            self.assertEqual(config["_workbench_send_report"]["daily_limit"], 120)


class PreflightNoKeyTests(unittest.TestCase):
    def test_rescore_preflight_passes_without_ai_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            resume = Path(tmp) / "resume.md"
            resume.write_text("# 简历\n## 基本信息\n姓名：测试\n", encoding="utf-8")
            config = {"ai": {}, "profile": {"resume_path": str(resume)}, "search": {}, "collection": {}}
            passing_browser = [{"id": "browser_runtime", "title": "浏览器连接", "status": "pass",
                                "message": "已连接", "detail": "", "action": ""}]
            with patch("bosshunter.web.preflight.check_browser_connection", return_value=passing_browser):
                checks = collect_preflight_checks("rescore", config)
            by_id = {check["id"]: check for check in checks}
            self.assertEqual(by_id["ai_connection"]["status"], "pass")
            self.assertIn("本地规则评分", by_id["ai_connection"]["message"])
            self.assertFalse([c for c in checks if c["status"] == "error"])


if __name__ == "__main__":
    unittest.main()


class PreflightNoKeyTests(unittest.TestCase):
    """任务启动门槛不得再要求 AI Key（规则评分兜底已接管）。"""

    def test_full_and_rescore_start_without_ai_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            resume = Path(tmp) / "resume.md"
            resume.write_text("# 简历\nPython 后端。", encoding="utf-8")
            config = dict(NO_KEY_CONFIG)
            config["profile"] = dict(NO_KEY_CONFIG["profile"], resume_path=str(resume))
            for mode in ("full", "rescore"):
                messages = server._preflight_messages(mode, config)
                self.assertEqual(messages, [], f"{mode} 不应被 AI Key 门槛拦住：{messages}")
                self.assertFalse(any("API Key" in m for m in messages))

    def test_collect_auto_score_starts_without_ai_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            resume = Path(tmp) / "resume.md"
            resume.write_text("# 简历\nPython 后端。", encoding="utf-8")
            config = dict(NO_KEY_CONFIG)
            config["profile"] = dict(NO_KEY_CONFIG["profile"], resume_path=str(resume))
            messages = server._preflight_messages("collect", config, {"auto_score": True})
            self.assertFalse(any("API Key" in m for m in messages), messages)

    def test_missing_resume_still_blocks(self):
        config = dict(NO_KEY_CONFIG)
        config["profile"] = dict(NO_KEY_CONFIG["profile"], resume_path="/tmp/does-not-exist-resume.md")
        messages = server._preflight_messages("rescore", config)
        self.assertTrue(any("简历" in m for m in messages), messages)
