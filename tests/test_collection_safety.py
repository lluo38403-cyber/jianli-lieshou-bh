import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bosshunter.config import DEFAULTS, load_config
from bosshunter.collection.base import CollectorHooks
from bosshunter.db import (
    add_platform_access,
    clear_platform_safety_lock,
    count_platform_access_today,
    get_active_platform_safety_lock,
    get_db,
    set_platform_safety_lock,
)
from bosshunter.platform_safety import PlatformAccessGuard, PlatformSafetyStop
from bosshunter.scraper.jobs import scrape_jobs
from bosshunter.web.server import _execute_collect, _wait_for_collection_delivery_cooldown
from bosshunter.web.tasks import WorkbenchTask

class CollectionSafetyTests(unittest.TestCase):
    def test_default_limits_are_daily_only(self):
        collection = DEFAULTS["collection"]
        self.assertNotIn("daily_new_jobs_limit", collection)
        self.assertEqual(collection["daily_search_page_limit"], 60)
        self.assertEqual(collection["daily_detail_page_limit"], 900)
        self.assertEqual(DEFAULTS["safety"]["daily_platform_page_limit"], 1100)
        self.assertEqual(collection["risk_pause_min_minutes"], 5)
        self.assertEqual(collection["risk_pause_max_minutes"], 10)
        self.assertEqual(collection["collection_delay_multiplier"], 1.5)
        self.assertNotIn("max_new_jobs_per_cycle", collection)
        self.assertNotIn("max_search_pages_per_cycle", collection)

    def test_retired_count_limits_are_removed_from_existing_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text(
                """
search:
  target_count: 88
collection:
  default_target_count: 66
  daily_new_jobs_limit: 100
platforms:
  boss:
    search:
      target_count: 77
  zhilian:
    search:
      target_count: 55
  51job:
    search:
      target_count: 44
""",
                encoding="utf-8",
            )

            config = load_config(config_path)

        self.assertNotIn("target_count", config["search"])
        self.assertNotIn("default_target_count", config["collection"])
        self.assertNotIn("daily_new_jobs_limit", config["collection"])
        for platform in ("boss", "zhilian", "51job"):
            self.assertNotIn("target_count", config["platforms"][platform]["search"])

    def test_default_budget_allows_search_and_detail_then_preserves_shared_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            try:
                db.executemany(
                    "INSERT INTO platform_access_events (platform, stage, action) VALUES ('boss', 'collection', ?)",
                    [("search_page",)] * 60 + [("detail_page",)] * 899,
                )
                db.commit()
                guard = PlatformAccessGuard(db, DEFAULTS, "collection")
                detail_limit = DEFAULTS["collection"]["daily_detail_page_limit"]
                guard.reserve("detail_page", daily_limit=detail_limit)
                with self.assertRaises(PlatformSafetyStop) as raised:
                    guard.reserve("detail_page", daily_limit=detail_limit)
                self.assertEqual(raised.exception.reason, "daily_detail_page_limit")
                sender = PlatformAccessGuard(db, DEFAULTS, "send")
                for _ in range(140):
                    sender.reserve("job_page")
                with self.assertRaises(PlatformSafetyStop) as raised:
                    sender.reserve("job_page")
                self.assertEqual(raised.exception.reason, "daily_platform_page_limit")
                self.assertEqual(count_platform_access_today(db), 1100)
            finally:
                db.close()

    def test_daily_access_limit_stops_before_the_next_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            guard = PlatformAccessGuard(db, {"safety": {"daily_platform_page_limit": 500}}, "collection")
            for _ in range(30):
                guard.reserve("search_page", daily_limit=30)

            with self.assertRaises(PlatformSafetyStop) as raised:
                guard.reserve("search_page", daily_limit=30)

            self.assertEqual(raised.exception.reason, "daily_search_page_limit")
            self.assertEqual(
                count_platform_access_today(db, stage="collection", action="search_page"),
                30,
            )
            db.close()

    def test_global_page_budget_is_shared_across_workflows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            config = {"safety": {"daily_platform_page_limit": 2}}
            PlatformAccessGuard(db, config, "collection").reserve("search_page")
            PlatformAccessGuard(db, config, "send").reserve("job_page")

            with self.assertRaises(PlatformSafetyStop) as raised:
                PlatformAccessGuard(db, config, "monitor").reserve("monitor_page")

            self.assertEqual(raised.exception.reason, "daily_platform_page_limit")
            db.close()

    def test_external_page_events_do_not_consume_boss_page_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            add_platform_access(db, "collection", "search_page", platform="zhilian")
            guard = PlatformAccessGuard(db, {"safety": {"daily_platform_page_limit": 1}}, "collection")
            guard.reserve("search_page")
            with self.assertRaises(PlatformSafetyStop) as raised:
                guard.reserve("search_page")
            self.assertEqual(raised.exception.reason, "daily_platform_page_limit")
            self.assertEqual(count_platform_access_today(db, platform="boss"), 1)
            self.assertEqual(count_platform_access_today(db, platform="zhilian"), 1)
            db.close()

    def test_risk_lock_survives_a_new_database_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "bosshunter.db"
            db = get_db(db_path)
            set_platform_safety_lock(db, "captcha", minutes=30)
            db.close()

            reopened = get_db(db_path)
            lock = get_active_platform_safety_lock(reopened)
            self.assertEqual(lock["reason"], "captcha")
            reopened.close()

    def test_clear_platform_safety_lock_removes_the_lock(self):
        # v27: 立即重试 clears the cooldown early instead of waiting it out.
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            set_platform_safety_lock(db, "captcha", minutes=30)
            self.assertIsNotNone(get_active_platform_safety_lock(db))
            self.assertTrue(clear_platform_safety_lock(db))
            self.assertIsNone(get_active_platform_safety_lock(db))
            # Clearing again is a no-op, not an error.
            self.assertFalse(clear_platform_safety_lock(db))
            db.close()

    def test_collection_risk_stops_and_records_safe_reason(self):
        db = get_db(Path(":memory:"))
        progress = MagicMock()
        progress.add_task.return_value = "task"
        context = MagicMock()
        context.__enter__ = MagicMock(return_value=progress)
        context.__exit__ = MagicMock(return_value=False)
        config = {
            "profile": {"target_cities": ["北京"]},
            "search": {"max_pages": 1},
        }

        with patch("bosshunter.scraper.jobs.get_db", return_value=db), \
             patch("bosshunter.collection.platforms.boss.PlatformAccessGuard") as guard_cls, \
             patch("bosshunter.scraper.jobs.Progress", return_value=context), \
             patch("bosshunter.scraper.jobs.new_tab", return_value="worker"), \
             patch("bosshunter.scraper.jobs.wait_for_load"), \
             patch("bosshunter.scraper.jobs.evaluate", return_value=json.dumps({"risk": "captcha"})), \
             patch("bosshunter.scraper.jobs.close_tab"), \
             patch("bosshunter.scraper.jobs.time.sleep"):
            count = scrape_jobs(config, ["AI"])

        self.assertEqual(count, 0)
        self.assertEqual(config["_workbench_collect_report"]["stop_reason"], "captcha")
        guard_cls.return_value.lock.assert_called_once()
        lock_call = guard_cls.return_value.lock.call_args
        self.assertEqual(lock_call.args, ("captcha",))
        self.assertGreaterEqual(lock_call.kwargs["minutes"], 5)
        self.assertLessEqual(lock_call.kwargs["minutes"], 10)

    def test_transient_collection_risk_is_ignored_without_locking(self):
        db = get_db(Path(":memory:"))
        progress = MagicMock()
        progress.add_task.return_value = "task"
        context = MagicMock()
        context.__enter__ = MagicMock(return_value=progress)
        context.__exit__ = MagicMock(return_value=False)
        config = {
            "profile": {"target_cities": ["北京"]},
            "search": {"max_pages": 1},
        }

        with patch("bosshunter.scraper.jobs.get_db", return_value=db), \
             patch("bosshunter.collection.platforms.boss.PlatformAccessGuard") as guard_cls, \
             patch("bosshunter.scraper.jobs.Progress", return_value=context), \
             patch("bosshunter.scraper.jobs.new_tab", return_value="worker"), \
             patch("bosshunter.scraper.jobs.wait_for_load"), \
             patch(
                 "bosshunter.scraper.jobs.evaluate",
                 side_effect=[
                     json.dumps({"risk": "blocked", "evidence": "blocked_page"}),
                     json.dumps({"risk": None}),
                     json.dumps({}),  # Font discovery: this fixture has no custom font.
                     False,
                     json.dumps([]),
                     json.dumps({"risk": None}),
                     True,
                 ],
             ), \
             patch("bosshunter.scraper.jobs.scroll"), \
             patch("bosshunter.scraper.jobs.close_tab"), \
             patch("bosshunter.scraper.jobs.time.sleep"):
            count = scrape_jobs(config, ["AI"])

        self.assertEqual(count, 0)
        self.assertEqual(config["_workbench_collect_report"]["stop_reason"], "no_jobs_for_search")
        guard_cls.return_value.lock.assert_not_called()

    def test_consecutive_page_failures_end_collection_without_risk_lock(self):
        db = get_db(Path(":memory:"))
        progress = MagicMock()
        progress.add_task.return_value = "task"
        context = MagicMock()
        context.__enter__ = MagicMock(return_value=progress)
        context.__exit__ = MagicMock(return_value=False)
        config = {
            "profile": {"target_cities": ["北京"]},
            "search": {"max_pages": 3},
            "collection": {"max_consecutive_page_failures": 3},
        }

        with patch("bosshunter.scraper.jobs.get_db", return_value=db), \
             patch("bosshunter.collection.platforms.boss.PlatformAccessGuard") as guard_cls, \
             patch("bosshunter.scraper.jobs.Progress", return_value=context), \
             patch("bosshunter.scraper.jobs.new_tab", return_value=None), \
             patch("bosshunter.scraper.jobs.close_tab"), \
             patch("bosshunter.scraper.jobs.time.sleep"):
            count = scrape_jobs(config, ["AI"])

        self.assertEqual(count, 0)
        self.assertEqual(config["_workbench_collect_report"]["stop_reason"], "consecutive_page_failures")
        guard_cls.return_value.lock.assert_not_called()

    def test_frontend_task_log_explains_daily_limit(self):
        task = WorkbenchTask(id="collect", mode="collect", label="单独采集")
        config = {"search": {"keywords": ["AI"]}}

        def fake_scrape(collect_config, _keywords, *, collected_job_ids=None):
            collect_config["_workbench_collect_report"] = {"stop_reason": "daily_search_page_limit"}
            return 0

        with patch("bosshunter.scraper.jobs.scrape_jobs", side_effect=fake_scrape), \
             patch("bosshunter.ai.scorer.score_jobs", return_value=(0, 0)):
            _execute_collect(task, config)

        self.assertTrue(any("为了账户安全" in line and "单日搜索页上限" in line for line in task.logs))

    def test_collection_delivery_cooldown_is_cancellable(self):
        task = WorkbenchTask(id="full", mode="full", label="运行全流程")
        task.context["boss_collection_completed_monotonic"] = time.monotonic()
        task.stop_requested.set()
        self.assertTrue(
            _wait_for_collection_delivery_cooldown(
                task,
                {
                    "collection": {
                        "delivery_cooldown_min_minutes": 5,
                        "delivery_cooldown_max_minutes": 15,
                    }
                },
            )
        )

    def test_collection_delivery_cooldown_selects_one_random_value_per_flow(self):
        task = WorkbenchTask(id="full", mode="full", label="运行全流程")
        task.context["boss_collection_completed_monotonic"] = time.monotonic()
        task.stop_requested.set()
        config = {
            "collection": {
                "delivery_cooldown_min_minutes": 5,
                "delivery_cooldown_max_minutes": 15,
            }
        }

        with patch("bosshunter.web.server.random.uniform", return_value=11.25) as choose:
            self.assertTrue(_wait_for_collection_delivery_cooldown(task, config))
            self.assertTrue(_wait_for_collection_delivery_cooldown(task, config))

        choose.assert_called_once_with(5, 15)
        self.assertEqual(task.context["boss_delivery_cooldown_minutes"], 11.25)


class NonBossPlatformQuotaTests(unittest.TestCase):
    """v36：采集限额按实际平台算——智联 / 51job / 猎聘翻到上限也要停采，
    而不是只有 BOSS 受限。每个平台按自己的平台名计数（搜索页 / 详情页
    各走各的限额），超限返回 completed_with_shortage，编排器不中断
    后续平台；提示文案是人话，不把 daily_search_page_limit 这类原因码
    原样甩给用户。"""

    def _hooks(self, collected):
        return CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _candidate: True,
            on_candidate=lambda candidate: collected.append(candidate) or True,
            on_parse_failed=lambda _reason: None,
            on_event=lambda **_kwargs: None,
        )

    def test_liepin_search_page_limit_stops_on_second_page(self):
        from bosshunter.collection.models import PlatformCollectionRequest
        from bosshunter.collection.platforms.liepin import LiepinBrowser, LiepinCollector

        list_payload = json.dumps({"status": "ready", "jobs": [{
            "source_job_id": "job-1",
            "salary": "15-25K",
            "title": "AI 产品经理",
            "company": "示例公司",
            "city": "上海",
            "url": "https://www.liepin.com/job/1001.shtml",
        }]}, ensure_ascii=False)
        detail_payload = json.dumps({
            "status": "ready", "title": "AI 产品经理", "company": "示例公司",
            "city": "上海", "jd": "负责需求分析。",
        }, ensure_ascii=False)
        browser = LiepinBrowser(
            new_tab=lambda url, **_kwargs: url,
            close_tab=lambda _target: True,
            evaluate=lambda _target, script: list_payload if "job-detail-job-info" in script else detail_payload,
            scroll=lambda *_args, **_kwargs: True,
            wait_for_load=lambda *_args, **_kwargs: True,
        )
        collected = []
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            try:
                result = LiepinCollector(
                    config={"collection": {"daily_search_page_limit": 1, "daily_detail_page_limit": 900}},
                    safety_conn=db,
                    browser=browser,
                    sleep=lambda _seconds: None,
                    uniform=lambda _low, _high: 0.0,
                ).collect(
                    PlatformCollectionRequest("liepin", ["AI 产品"], ["上海"], {"上海": "020"}, max_pages=2),
                    self._hooks(collected),
                )
                self.assertEqual(result.status, "completed_with_shortage")
                self.assertEqual(result.reason_code, "daily_search_page_limit")
                self.assertIn("猎聘", result.message)
                self.assertNotIn("daily_search_page_limit", result.message)
                # 第 1 页的搜索 + 详情各记 1 次；第 2 页翻页被拦住，没记上。
                self.assertEqual(count_platform_access_today(db, platform="liepin", stage="collection", action="search_page"), 1)
                self.assertEqual(count_platform_access_today(db, platform="liepin", stage="collection", action="detail_page"), 1)
            finally:
                db.close()

    def test_51job_search_page_limit_stops_on_second_fetch(self):
        from bosshunter.collection.models import PlatformCollectionRequest
        from bosshunter.collection.platforms.job51 import Job51Browser, Job51Collector, _ApiRateLimiter

        body = json.dumps({
            "status": "1",
            "resultbody": {"job": {"items": [{
                "jobName": "AI 工程师", "companyName": "测试科技",
                "salary": "15-25K", "city": "上海",
                "jobDesc": "负责 AI 系统开发",
            }], "totalCount": 100}},
        }, ensure_ascii=False)
        raw = json.dumps({"http_status": 200, "content_type": "application/json", "body": body})
        browser = Job51Browser(
            get_page_targets=lambda: [],
            new_tab=lambda url, **_kwargs: "host-tab",
            close_tab=lambda _target: True,
            evaluate=lambda _target, _js, **_kwargs: raw,
        )
        collected = []
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            try:
                collector = Job51Collector(
                    config={"collection": {"daily_search_page_limit": 1}},
                    safety_conn=db,
                    browser=browser,
                    sleep=lambda _seconds: None,
                )
                with (
                    patch("bosshunter.collection.platforms.job51._wait_or_stop", return_value=False),
                    patch.object(_ApiRateLimiter, "wait_before_request", return_value=True),
                ):
                    result = collector.collect(
                        PlatformCollectionRequest("51job", ["AI"], ["上海"], {"上海": "020000"}, max_pages=5),
                        self._hooks(collected),
                    )
                self.assertEqual(result.status, "completed_with_shortage")
                self.assertEqual(result.reason_code, "daily_search_page_limit")
                self.assertIn("51job", result.message)
                self.assertNotIn("daily_search_page_limit", result.message)
                # 探针占了唯一 1 次搜索页名额；接下来的正式翻页被拦住。
                self.assertEqual(count_platform_access_today(db, platform="51job", stage="collection", action="search_page"), 1)
            finally:
                db.close()

    def test_zhilian_api_search_page_limit_stops_on_second_page(self):
        from bosshunter.collection.models import PlatformCollectionRequest
        from bosshunter.collection.platforms.zhilian import ZhilianBrowser, ZhilianCollector, _ApiRateLimiter

        def api_item(number):
            return {
                "number": number, "jobName": "AI 工程师", "companyName": "测试科技",
                "salary": "15-25K", "cityName": "北京", "jobDesc": "负责 AI 系统开发",
                "workingExp": "3-5年", "education": "本科", "url": f"/jobdetail/{number}.htm",
            }

        api_response = json.dumps({
            "http_status": 200,
            "content_type": "application/json",
            "body": json.dumps({
                "code": 200, "apiCode": 200, "message": "",
                "data": {
                    "results": [api_item(f"CC{i}") for i in range(20)],
                    "numTotal": 40, "numFound": 40, "taskId": "test-task",
                },
            }, ensure_ascii=False),
        })
        browser = ZhilianBrowser(
            get_page_targets=lambda: [],
            new_tab=lambda *a, **kw: "host-1",
            close_tab=lambda _target: True,
            evaluate=lambda *a, **kw: api_response,
            scroll=lambda *a, **kw: True,
            wait_for_load=lambda *a, **kw: True,
        )
        collected = []
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            try:
                collector = ZhilianCollector(
                    config={
                        "collection": {"daily_search_page_limit": 1, "daily_detail_page_limit": 900},
                        "platforms": {"zhilian": {"search": {"api_fetch": True}}},
                        "profile": {"deal_breakers": [], "blocked_companies": []},
                    },
                    safety_conn=db,
                    browser=browser,
                    sleep=lambda _seconds: None,
                )
                with patch.object(_ApiRateLimiter, "wait_before_request", return_value=True):
                    result = collector.collect(
                        PlatformCollectionRequest("zhilian", ["AI"], ["北京"], {"北京": "530"}, max_pages=3),
                        self._hooks(collected),
                    )
                self.assertEqual(result.status, "completed_with_shortage")
                self.assertEqual(result.reason_code, "daily_search_page_limit")
                self.assertIn("智联", result.message)
                self.assertNotIn("daily_search_page_limit", result.message)
                # 第 1 页占了唯一 1 次搜索页名额；第 2 页被拦住，没记上。
                self.assertEqual(count_platform_access_today(db, platform="zhilian", stage="collection", action="search_page"), 1)
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
