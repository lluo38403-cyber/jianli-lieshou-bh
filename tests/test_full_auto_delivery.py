"""全自动投递回归测试：无浏览器（浏览器调用全部 stub）、无真实外发。

覆盖：无 AI Key 时规则评分兜底、四平台分发、自动确认、
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
                 salary="15-25K", city="北京", education="硕士"),
            NO_KEY_CONFIG,
        )
        self.assertLess(jd_only, 60, "仅 JD 命中且城市/学历不符合时不过线")

    def test_rule_score_unlimited_education_counts_as_match(self):
        """学历不限的岗位：任何学历都符合，给 5 分（v47：以前白白扣 5 分）。

        复现：岗位“学历不限”，用户学历本科，旧代码两边都对不上得 0 分；
        仓库管理员那例就是 40 + 关键词 10 + 薪资 10 + 城市 5 + 学历 0 = 65，
        修好后应为 70 分。
        """
        from bosshunter.ai.prefilter import rule_score

        config = {
            "profile": {
                "target_roles": [],
                "target_cities": ["潮州"],
                "education": "本科",
                "salary_min": 3,
                "salary_max": 7,
            },
            "search": {"keywords": ["仓库"]},
            "ai": {},
        }
        job = dict(BASE_JOB, title="服装仓库管理员", company="广东佳人服饰有限公司",
                   jd="负责服装仓库管理，要求会 p 图。",
                   salary="5-7K", city="潮州", education="学历不限")
        score, reason = rule_score(job, config)
        self.assertIn("学历5分（符合）", reason)
        self.assertEqual(score, 70)
        # “不限”两个字也算。
        _, reason2 = rule_score(dict(job, education="不限"), config)
        self.assertIn("学历5分（符合）", reason2)
        # 真不匹配的还是 0 分，不动。
        _, reason3 = rule_score(dict(job, education="硕士"), config)
        self.assertIn("学历0分（没对上）", reason3)

    def test_rule_score_education_level_comparison(self):
        """学历按等级算：高学历满足低要求（v50）。

        何铜浩反馈：他是大专，岗位要求“初中及以下”或“高中·社招”时
        学历都是 0 分（“大专打不过高中和初中”）。旧逻辑只按子串比对，
        “大专”和“初中及以下”/“高中·社招”互相不包含，所以 0 分。
        等级（从低到高）：初中 → 高中/中专/中技/职高 → 大专 → 本科
        → 硕士/研究生 → 博士；用户等级 >= 岗位等级就给 5 分。
        下面用标题命中关键词的固定搭配（基础 40 + 关键词 10 + 薪资 10
        + 城市 5 + 学历 5/0），学历符合时 70 分、不符合时 65 分。
        """
        from bosshunter.ai.prefilter import rule_score

        def score_for(user_edu, job_edu):
            config = {
                "profile": {
                    "target_roles": [],
                    "target_cities": ["潮州"],
                    "education": user_edu,
                    "salary_min": 3,
                    "salary_max": 7,
                },
                "search": {"keywords": ["仓库"]},
                "ai": {},
            }
            job = {
                "title": "服装仓库管理员",
                "company": "示例公司",
                "salary": "5-7K",
                "city": "潮州",
                "education": job_edu,
                "jd": "负责服装仓库管理。",
            }
            return rule_score(job, config)

        cases = [
            # (用户学历, 岗位学历, 期望总分, 学历分文案)
            ("大专", "初中及以下", 70, "学历5分（符合）"),
            ("大专", "高中·社招", 70, "学历5分（符合）"),
            ("初中", "本科", 65, "学历0分（没对上）"),
            ("大专", "大专及以上", 70, "学历5分（符合）"),
            ("本科", "大专", 70, "学历5分（符合）"),
            ("大专", "本科", 65, "学历0分（没对上）"),
            ("中专", "高中", 70, "学历5分（符合）"),
            ("本科", "学历不限", 70, "学历5分（符合）"),
            ("", "本科", 65, "学历0分（没对上）"),
            ("大专", "", 65, "学历0分（没对上）"),
        ]
        for user_edu, job_edu, expected_score, expected_note in cases:
            with self.subTest(user_edu=user_edu, job_edu=job_edu):
                score, reason = score_for(user_edu, job_edu)
                self.assertEqual(score, expected_score)
                self.assertIn(expected_note, reason)

    def test_rule_score_education_level_he_tonghao_screenshots(self):
        """何铜浩两张截图的原样复现（无关键词命中）。

        截图 1：大专 vs 初中及以下，薪资/城市符合。学历分 0→5，
        原始分 55→60；但无关键词命中时总分封顶 55，所以面板最终分
        仍是 55——“55 变 60”说的是原始分，不是最终分。
        截图 2：大专 vs 高中·社招，薪资 3 分、城市 5 分。学历分 0→5，
        原始分 48→53，未触顶，最终分 53。
        """
        from bosshunter.ai.prefilter import rule_score

        config = {
            "profile": {
                "target_roles": [],
                "target_cities": ["潮州"],
                "education": "大专",
                "salary_min": 3,
                "salary_max": 7,
            },
            "search": {"keywords": ["仓库"]},
            "ai": {},
        }
        job1 = {
            "title": "仓储库维,快递打包",
            "company": "我能云仓",
            "salary": "4-6K",
            "city": "潮州",
            "education": "初中及以下",
            "jd": "库维岗：负责日常收货上架，库位维护，打包岗：负责日常订单打包，"
                  "核实订单信息。装卸：负责常来货，发货，装卸工作。"
                  "做过仓储云仓优先上班时间：9:30-21:30左右公司包吃住",
        }
        score1, reason1 = rule_score(job1, config)
        self.assertIn("学历5分（符合）", reason1)
        self.assertIn("= 60分", reason1)
        self.assertIn("封顶55分", reason1)
        self.assertEqual(score1, 55)

        job2 = {
            "title": "仓储物流经理",
            "company": "爱丽斯",
            "salary": "8-14K",
            "city": "潮州",
            "education": "高中·社招",
            "jd": "职责：仓位编制管理进出库执行管理进出库台账管理物流周期管理"
                  "与进度跟踪技能需求：精通Excel作数据统计应用具备WMS、"
                  "ERP等信息化系统的操作经验",
        }
        score2, reason2 = rule_score(job2, config)
        self.assertIn("学历5分（符合）", reason2)
        self.assertEqual(score2, 53)

    def test_rule_score_reason_lists_each_factor_points(self):
        """打分原因要让用户对得上分数：每项写清加了几分，封顶也要写出来（图1）。"""
        from bosshunter.ai.prefilter import rule_score

        score, reason = rule_score(
            dict(BASE_JOB, title="电话销售代表", jd="负责电话销售，维护客户关系。",
                 salary="15-25K", city="上海", education="本科"),
            NO_KEY_CONFIG,
        )
        # 基础 40 + 关键词 0 + 薪资 10 + 城市 5 + 学历 5 = 60，无命中封顶 55。
        self.assertEqual(score, 55)
        self.assertIn("基础40分", reason)
        self.assertIn("关键词0分", reason)
        self.assertIn("薪资10分", reason)
        self.assertIn("城市5分", reason)
        self.assertIn("学历5分", reason)
        self.assertIn("= 60分", reason)
        self.assertIn("封顶55分", reason)
        self.assertIn("最终55分", reason)

        matched_score, matched_reason = rule_score(dict(BASE_JOB), NO_KEY_CONFIG)
        self.assertEqual(matched_score, 70)
        self.assertIn("关键词10分（命中：Python 后端）", matched_reason)
        self.assertIn("= 70分", matched_reason)
        self.assertNotIn("封顶", matched_reason)


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

    def test_boss_greeting_passes_through_unchanged(self):
        # v48：安静投递已删——BOSS 分发不再把招呼语清空，原样发出去。
        job = dict(BASE_JOB, source_platform="boss")
        with patch.object(sender, "_send_greeting_once", return_value=({"success": True}, None)) as greet:
            sender._dispatch_send(job, "您好，我是求职者", {})
        greet.assert_called_once_with(job, "您好，我是求职者", {})

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
