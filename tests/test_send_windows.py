"""投递时间窗口已取消的回归测试：无浏览器、无真实外发。

SendWindowChecker 类本身保留（兼容旧配置文件的校验），但发送、监测、
任务启动都不再按窗口或随机休息日拦截。
"""

import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch

import yaml

from bosshunter.config import load_config, save_config
from bosshunter.db import get_db, insert_job, update_job_greeting, update_job_status
from bosshunter.executor import monitor, sender
from bosshunter.throttle import SendWindowChecker
from bosshunter.web.tasks import WorkbenchTaskRunner

# None 表示“没配窗口”，新语义下是合法的（等同于字段缺失），不参与非法用例。
INVALID_WINDOWS = [
    {}, "09:00-16:00", [None], [123], [""], ["garbage"],
    ["9:00-16:00"], ["09:00:00-16:00"], ["09:00-24:00"], ["09:60-16:00"],
    ["09:00-09:00"], ["16:00-09:00"], ["23:00-01:00"],
    ["09:00-12:00", "09:00-12:00"], ["09:00-12:00", "11:00-16:00"],
    ["09:00-16:00", "10:00-12:00"], ["09:00-16:00", "bad"],
]


class SendWindowCheckerTests(unittest.TestCase):
    def test_none_window_means_no_window_configured(self):
        # None 等同于字段缺失：校验通过，保存/读取都不报错。
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            save_config({"throttle": {"send_windows": None}}, path)
            self.assertIsNone(load_config(path)["throttle"]["send_windows"])

    def test_invalid_windows_still_rejected_by_checker_and_config_io(self):
        for windows in INVALID_WINDOWS:
            with self.subTest(windows=windows):
                with self.assertRaises(ValueError):
                    SendWindowChecker(windows)
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "config.yaml"
                    config = {"throttle": {"send_windows": windows}}
                    with self.assertRaises(ValueError):
                        save_config(config, path)
                    self.assertFalse(path.exists())
                    path.write_text(yaml.safe_dump(config), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_config(path)

    def test_boundaries_and_final_deadline(self):
        cases = [
            (539, False, False), (540, True, False), (719, True, False), (720, False, False),
            (839, False, False), (840, True, False), (959, True, False), (960, False, True),
        ]
        for minute, active, finished in cases:
            with self.subTest(minute=minute):
                checker = SendWindowChecker(["14:00-16:00", "09:00-12:00"])
                with patch.object(checker, "_current_minutes", return_value=minute):
                    self.assertIs(checker.is_active(), active)
                    self.assertIs(checker.latest_end_time_reached(), finished)
        # The scheduler deliberately uses naive local wall-clock time, as the YAML has no timezone.
        checker = SendWindowChecker(["14:00-16:00", "09:00-12:00"])
        self.assertEqual(
            checker.latest_end_datetime(datetime(2026, 10, 8, 10, 0, 59)),
            datetime(2026, 10, 8, 16),
        )

    def test_adjacent_and_empty_windows_keep_legacy_behavior(self):
        checker = SendWindowChecker([" 12:00 - 16:00 ", "09:00-12:00"])
        with patch.object(checker, "_current_minutes", return_value=720):
            self.assertTrue(checker.is_active())
        empty = SendWindowChecker([])
        self.assertTrue(empty.is_active())
        self.assertIsNone(empty.latest_end_datetime())
        self.assertFalse(empty.latest_end_time_reached())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            save_config({"throttle": {"send_windows": []}}, path)
            self.assertEqual(load_config(path)["throttle"]["send_windows"], [])

    def test_yaml_round_trip(self):
        windows = ["14:00-16:00", "09:00-12:00"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            save_config({"throttle": {"send_windows": windows}}, path)
            self.assertEqual(yaml.safe_load(path.read_text(encoding="utf-8"))["throttle"]["send_windows"], windows)
            self.assertEqual(load_config(path)["throttle"]["send_windows"], windows)


class NoWindowGatingTests(unittest.TestCase):
    def test_task_start_has_no_window_deadline(self):
        """旧配置里即使写着窗口，任务启动也不再设截止时间、不再到点停。"""
        windows = ["14:00-16:00", "09:00-12:00"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            save_config({"throttle": {"send_windows": windows}}, path)
            config = load_config(path)
        executed = Event()
        runner = WorkbenchTaskRunner({"monitor": lambda *_: executed.set()})
        now = datetime(2026, 10, 8, 16)
        with patch("bosshunter.web.tasks.datetime") as clock:
            clock.now.return_value = now
            result = runner.start("monitor", config)
        self.assertIsNone(result.get("deadline_at"))
        self.assertNotEqual(result.get("status"), "stopped")
        self.assertTrue(executed.wait(timeout=5), "任务应在后台正常执行，不再被窗口拦住")

    def test_sender_sends_all_ready_jobs_without_window_check(self):
        """旧配置带窗口 + 休息日概率，发送也不再中途停。"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "jobs.db"
            db = get_db(db_path)
            for number in range(2):
                job_id = str(number)
                insert_job(db, {"id": job_id, "title": "Test", "company": "Test", "url": "https://www.zhipin.com/test"})
                update_job_status(db, job_id, "ready")
                update_job_greeting(db, job_id, "Test greeting")
            db.close()
            config = {"throttle": {"send_windows": ["09:00-12:00", "14:00-16:00"], "day_off_probability": 1.0}}
            with patch.object(sender.RequestThrottle, "wait", return_value=False), \
                    patch.object(sender, "_send_greeting_once", return_value=({"success": True}, None)) as outbound:
                self.assertEqual(sender.send_greetings(config, db_path=db_path), 2)
            self.assertEqual(outbound.call_count, 2)
            self.assertIsNone(config["_workbench_send_report"]["stop_reason"])
            db = get_db(db_path)
            self.assertEqual(db.execute("SELECT count(*) FROM jobs WHERE status='sent'").fetchone()[0], 2)
            db.close()

    def test_monitor_message_send_ignores_window_config(self):
        with patch.object(monitor, "evaluate", return_value={"success": True}) as outbound:
            self.assertTrue(monitor._send_message_in_chat("test", "test", {"throttle": {"send_windows": ["09:00-16:00"]}}))
        outbound.assert_called_once()

    def test_preset_greeting_confirms_without_window(self):
        # _confirm_preset_greeting 真实返回的就是 preset_confirmed（不是
        # first_contact_submitted）：预设确认后 BOSS 已经发了预设文案，
        # 投递流程里它按“已发过”走，不再发第二条（v44）。
        with patch.object(sender, "_detect_greet_popup", return_value={"success": True}), \
                patch.object(sender, "_is_preset_greeting_popup", return_value=True), \
                patch.object(sender, "_confirm_preset_greeting", return_value={"success": True, "action": "preset_confirmed"}) as confirm:
            result = sender._handle_greet_popup("test", "test")
        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "preset_confirmed")
        confirm.assert_called_once()

class BackgroundSubmitFallbackTests(unittest.TestCase):
    """后台提交报失败时，手填兜底之前必须先复核会话里有没有这条消息。

    v46 回归：v45 上一次投递发了两条一模一样的固定招呼。预设弹窗那条
    路 v44 已经堵住了（preset_confirmed 按已发送走）；剩下的另一条路
    在这里——_submit_chat_message_background 的 evaluate 超时/解析失败
    时，vue.handleSubmit() 可能已经把消息发出去了，旧代码不复核就直接
    手填再点一次发送，第二条就这么出来的。
    """

    def _run_send(self, delivery_states, fill_result=None, submit_result=None):
        job = {"id": "job-1", "title": "测试岗位", "company": "测试公司", "url": "https://www.zhipin.com/job_detail/xxx"}
        throttle_config = {
            "browse_before_greet": False,
            "_chat_button_attempts": 1,
            "_chat_navigation_attempts": 1,
            "_send_verification_attempts": 2,
        }
        with patch.object(sender, "get_page_targets", return_value=[]), \
                patch.object(sender, "new_tab", return_value="tab-1"), \
                patch.object(sender, "close_tab"), \
                patch.object(sender, "evaluate", return_value='{"success": true}'), \
                patch.object(sender, "micro_pause", return_value=False), \
                patch.object(sender, "_page_risk_result", return_value=None), \
                patch.object(sender, "_click_chat_button", return_value={"success": True}), \
                patch.object(sender, "_handle_greet_popup", return_value={"success": True, "action": "no_popup"}), \
                patch.object(sender, "_wait_for_chat_page", return_value={"success": True, "target_id": "tab-1"}), \
                patch.object(sender, "_sleep_or_stop", return_value=False), \
                patch.object(sender, "_message_delivery_state", side_effect=delivery_states) as state, \
                patch.object(sender, "_submit_chat_message_background", return_value=submit_result or {"success": False, "error": "no_response"}) as submit, \
                patch.object(sender, "_fill_chat_input", return_value=fill_result or {"success": True}) as fill, \
                patch.object(sender, "click_at", return_value=True) as click:
            result = sender._send_greeting_once(job, "您好，我对贵公司的这个岗位很感兴趣，期待与您进一步沟通，谢谢！", throttle_config)
        return result, fill, click, state, submit

    def test_failed_background_submit_does_not_resend_when_message_is_already_there(self):
        # 入口复核 missing → 后台提交报失败 → 再复核已是 delivered：
        # 消息已经发出去了，手填一次都不能碰，直接走核验记成功。
        result, fill, click, _, _ = self._run_send(["missing", "delivered", "delivered", "delivered"])
        self.assertTrue(result[0]["success"])
        self.assertTrue(result[0].get("verified"))
        fill.assert_not_called()
        click.assert_not_called()

    def test_preset_message_already_sent_is_not_sent_again(self):
        # v50 第三条路：我们的文案查不到（missing），但会话里已有我方
        # 已送达的消息（BOSS 预设文案）：不发，直接按已发送记成功；
        # 后台提交和手填一次都不能碰。
        result, fill, click, _, submit = self._run_send(["missing", "delivered"])
        self.assertTrue(result[0]["success"])
        self.assertTrue(result[0].get("already_present"))
        submit.assert_not_called()
        fill.assert_not_called()
        click.assert_not_called()

    def test_pending_own_message_blocks_send(self):
        # v50：会话里已有发送中的消息：不发，报错请人工检查。
        result, fill, click, _, submit = self._run_send(["missing", "pending"])
        self.assertFalse(result[0]["success"])
        self.assertIn("人工检查", result[0].get("history_detail") or "")
        submit.assert_not_called()
        fill.assert_not_called()
        click.assert_not_called()

    def test_failed_own_message_does_not_block_send(self):
        # v50：之前那条没发出去（failed）：继续正常发我们的。
        result, fill, click, _, submit = self._run_send(
            ["missing", "failed", "delivered", "delivered"],
            submit_result={"success": True},
        )
        self.assertTrue(result[0]["success"])
        self.assertTrue(result[0].get("verified"))
        submit.assert_called_once()
        fill.assert_not_called()
        click.assert_not_called()

    def test_no_own_message_sends_as_before(self):
        # v50：会话里什么都没有：回归现有行为，照常发。
        result, fill, click, _, submit = self._run_send(
            ["missing", "missing", "delivered", "delivered"],
            submit_result={"success": True},
        )
        self.assertTrue(result[0]["success"])
        self.assertTrue(result[0].get("verified"))
        submit.assert_called_once()
        fill.assert_not_called()
        click.assert_not_called()

class BatchRestTests(unittest.TestCase):
    """按批投递（v47）：每批投 3–5 个，批与批之间按档位歇一会。

    第一批直接开始投，等待只发生在批次之间；每批个数重新抽；
    休息时长按档位走（标准 1–3 分钟）；开关关了就不歇。
    """

    def _ready_db(self, tmp, count):
        db_path = Path(tmp) / "jobs.db"
        db = get_db(db_path)
        for number in range(count):
            job_id = str(number)
            insert_job(db, {"id": job_id, "title": "Test", "company": "Test", "url": "https://www.zhipin.com/test"})
            update_job_status(db, job_id, "ready")
            update_job_greeting(db, job_id, "Test greeting")
        db.close()
        return db_path

    def test_batch_rest_fires_once_between_two_batches(self):
        """7 个岗位、每批 3 个再 4 个：中间只歇一次，时长在标准档 60–180 秒内。"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._ready_db(tmp, 7)
            logs: list[str] = []
            sleeps: list[float] = []
            config = {
                "throttle": {"risk_tier": "standard", "stage_rest": True},
                "_workbench_log": logs.append,
            }
            with patch.object(sender.RequestThrottle, "wait", return_value=False), \
                    patch.object(sender, "_send_greeting_once", return_value=({"success": True}, None)), \
                    patch.object(sender, "sample_batch_size", side_effect=[3, 4]), \
                    patch.object(sender, "_sleep_or_stop", side_effect=lambda seconds, _e: sleeps.append(seconds) or False):
                self.assertEqual(sender.send_greetings(config, db_path=db_path), 7)
            self.assertEqual(len(sleeps), 1)
            self.assertGreaterEqual(sleeps[0], 60.0)
            self.assertLessEqual(sleeps[0], 180.0)
            self.assertTrue(any("这批投了 3 个" in message for message in logs))
            self.assertTrue(any("下一批投 4 个" in message for message in logs))

    def test_batch_rest_off_means_no_rest(self):
        """阶段休息关了：7 个岗位一批投完，中间一次也不歇。"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._ready_db(tmp, 7)
            sleeps: list[float] = []
            config = {"throttle": {"risk_tier": "standard", "stage_rest": False}}
            with patch.object(sender.RequestThrottle, "wait", return_value=False), \
                    patch.object(sender, "_send_greeting_once", return_value=({"success": True}, None)), \
                    patch.object(sender, "sample_batch_size", side_effect=[3, 4]), \
                    patch.object(sender, "_sleep_or_stop", side_effect=lambda seconds, _e: sleeps.append(seconds) or False):
                self.assertEqual(sender.send_greetings(config, db_path=db_path), 7)
            self.assertEqual(sleeps, [])


class RoundRestTests(unittest.TestCase):
    """投递轮休（v48）：每投完一轮（默认 20 个）歇一次，时长在配置范围内随机。

    轮休叠加在 v47 批次休息之上：轮休先判，歇完批计数清零、下一批重抽，
    不再叠加一次批次休息；未满一轮不歇；最后一轮后面没岗位了也不歇。
    """

    def _ready_db(self, tmp, count):
        db_path = Path(tmp) / "jobs.db"
        db = get_db(db_path)
        for number in range(count):
            job_id = str(number)
            insert_job(db, {"id": job_id, "title": "Test", "company": "Test", "url": "https://www.zhipin.com/test"})
            update_job_status(db, job_id, "ready")
            update_job_greeting(db, job_id, "Test greeting")
        db.close()
        return db_path

    def _run(self, db_path, config, sleeps):
        with patch.object(sender.RequestThrottle, "wait", return_value=False), \
                patch.object(sender, "_send_greeting_once", return_value=({"success": True}, None)), \
                patch.object(sender, "sample_batch_size", return_value=3), \
                patch.object(sender, "_sleep_or_stop", side_effect=lambda seconds, _e: sleeps.append(seconds) or False):
            return sender.send_greetings(config, db_path=db_path)

    def test_full_round_rests_once_per_round(self):
        """25 个岗位、一轮 5 个、阶段休息关了：只歇 4 次（5/10/15/20 个时），每次 5–10 分钟。"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._ready_db(tmp, 25)
            logs: list[str] = []
            sleeps: list[float] = []
            config = {
                "throttle": {
                    "risk_tier": "standard", "stage_rest": False,
                    "delivery_round_size": 5,
                    "delivery_round_rest_min_minutes": 5,
                    "delivery_round_rest_max_minutes": 10,
                },
                "_workbench_log": logs.append,
            }
            self.assertEqual(self._run(db_path, config, sleeps), 25)
            self.assertEqual(len(sleeps), 4)
            for rest_s in sleeps:
                self.assertGreaterEqual(rest_s, 300.0)
                self.assertLessEqual(rest_s, 600.0)
            self.assertEqual(sum("这轮投了 5 个" in message for message in logs), 4)

    def test_incomplete_round_does_not_rest(self):
        """4 个岗位、一轮 5 个：没满一轮，一次也不歇。"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._ready_db(tmp, 4)
            sleeps: list[float] = []
            config = {
                "throttle": {
                    "risk_tier": "standard", "stage_rest": False,
                    "delivery_round_size": 5,
                    "delivery_round_rest_min_minutes": 5,
                    "delivery_round_rest_max_minutes": 10,
                },
            }
            self.assertEqual(self._run(db_path, config, sleeps), 4)
            self.assertEqual(sleeps, [])

    def test_round_rest_duration_is_within_configured_range(self):
        """歇的时长落在配置范围：最少=最多=7 分钟时，每次正好 420 秒。"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._ready_db(tmp, 12)
            sleeps: list[float] = []
            config = {
                "throttle": {
                    "risk_tier": "standard", "stage_rest": False,
                    "delivery_round_size": 5,
                    "delivery_round_rest_min_minutes": 7,
                    "delivery_round_rest_max_minutes": 7,
                },
            }
            self.assertEqual(self._run(db_path, config, sleeps), 12)
            self.assertEqual(sleeps, [420.0, 420.0])

    def test_round_rest_does_not_stack_with_batch_rest(self):
        """10 个岗位、一轮 5 个、每批 3 个：轮休处只歇轮休那一次，不叠加批次休息。"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._ready_db(tmp, 10)
            logs: list[str] = []
            sleeps: list[float] = []
            config = {
                "throttle": {
                    "risk_tier": "standard", "stage_rest": True,
                    "delivery_round_size": 5,
                    "delivery_round_rest_min_minutes": 5,
                    "delivery_round_rest_max_minutes": 10,
                },
                "_workbench_log": logs.append,
            }
            self.assertEqual(self._run(db_path, config, sleeps), 10)
            # 3 个时批次休息、5 个时轮休、8 个时批次休息；第 10 个是最后
            # 一个，后面没岗位了，不再歇。
            self.assertEqual(len(sleeps), 3)
            round_rests = [rest for rest in sleeps if 300.0 <= rest <= 600.0]
            batch_rests = [rest for rest in sleeps if 60.0 <= rest <= 180.0]
            self.assertEqual(len(round_rests), 1)
            self.assertEqual(len(batch_rests), 2)
            self.assertTrue(any("这轮投了 5 个" in message for message in logs))


class PauseRefreshTests(unittest.TestCase):
    """暂停后改设置（v52）：恢复时按新档位走，不再用任务启动时的快照。

    任务以稳妥档位启动，投出第 1 个后暂停；暂停期间把已保存的配置
    改成标准档位，恢复后下一批休息落在标准档 60–180 秒内（不是稳妥
    档的 180–300 秒）；7 个岗位全部投完，进度不丢。
    """

    def _ready_db(self, tmp, count):
        db_path = Path(tmp) / "jobs.db"
        db = get_db(db_path)
        for number in range(count):
            job_id = str(number)
            insert_job(db, {"id": job_id, "title": "Test", "company": "Test", "url": "https://www.zhipin.com/test"})
            update_job_status(db, job_id, "ready")
            update_job_greeting(db, job_id, "Test greeting")
        db.close()
        return db_path

    @staticmethod
    def _write_throttle_config(path, tier):
        path.write_text(
            "throttle:\n"
            f"  risk_tier: {tier}\n"
            "  stage_rest: true\n"
            "  delivery_round_size: 20\n",
            encoding="utf-8",
        )

    def test_pause_then_change_tier_resume_uses_new_tier(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._ready_db(tmp, 7)
            config_path = Path(tmp) / "config.yaml"
            self._write_throttle_config(config_path, "steady")
            logs: list[str] = []
            sleeps: list[float] = []
            pause_event = Event()
            send_started = Event()
            release_first_send = Event()
            send_calls: list[str] = []

            def fake_send(job, greeting, throttle_config):
                send_calls.append(job["id"])
                if len(send_calls) == 1:
                    send_started.set()
                    # 等主线程把暂停挂上、把配置改掉，再放行第一条。
                    self.assertTrue(release_first_send.wait(timeout=10))
                return {"success": True}, None

            config = {
                "throttle": {"risk_tier": "steady", "stage_rest": True},
                "_workbench_log": logs.append,
                "_workbench_pause_event": pause_event,
                "_workbench_config_path": str(config_path),
            }
            result: dict[str, int] = {}

            def run_send():
                with patch.object(sender.RequestThrottle, "wait", return_value=False), \
                        patch.object(sender, "_send_greeting_once", side_effect=fake_send), \
                        patch.object(sender, "sample_batch_size", side_effect=[3, 4]), \
                        patch.object(sender, "_sleep_or_stop", side_effect=lambda seconds, _e: sleeps.append(seconds) or False):
                    result["sent"] = sender.send_greetings(config, db_path=db_path)

            worker = Thread(target=run_send, daemon=True)
            worker.start()
            self.assertTrue(send_started.wait(timeout=10))
            pause_event.set()
            self._write_throttle_config(config_path, "standard")
            release_first_send.set()
            # 等投递循环真的进暂停（日志“已暂停”就是信号），再点继续。
            deadline = time.monotonic() + 10
            while not any("已暂停" in message for message in logs):
                self.assertLess(time.monotonic(), deadline, "投递循环没有进暂停")
                time.sleep(0.05)
            pause_event.clear()
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
            self.assertEqual(result.get("sent"), 7)
            self.assertEqual(len(sleeps), 1)
            self.assertGreaterEqual(sleeps[0], 60.0)
            self.assertLessEqual(sleeps[0], 180.0)
            self.assertTrue(any("这轮已投" in message for message in logs))

    def test_refresh_with_missing_config_file_keeps_old_settings(self):
        """配置文件不在了：重读失败不改参数、不打断投递。"""
        with tempfile.TemporaryDirectory() as tmp:
            config = {
                "throttle": {"risk_tier": "steady", "stage_rest": True},
                "_workbench_config_path": str(Path(tmp) / "no-such.yaml"),
            }
            throttle_config = dict(config["throttle"])
            self.assertEqual(sender.refresh_throttle_settings(config, throttle_config), {})
            self.assertEqual(throttle_config["risk_tier"], "steady")

    def test_update_delay_range_keeps_last_request_time(self):
        """换等待区间不动上次发送时间：下一条照样扣掉已过去的时间。"""
        from bosshunter.throttle import RequestThrottle

        throttle = RequestThrottle(delay_min=120.0, delay_max=300.0)
        throttle.mark()
        before = throttle._last_request_time
        throttle.update_delay_range(60.0, 180.0)
        self.assertEqual(throttle._last_request_time, before)
        self.assertEqual((throttle._delay_min, throttle._delay_max), (60.0, 180.0))


if __name__ == "__main__":
    unittest.main()
