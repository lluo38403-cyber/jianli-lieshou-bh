"""投递时间窗口已取消的回归测试：无浏览器、无真实外发。

SendWindowChecker 类本身保留（兼容旧配置文件的校验），但发送、监测、
任务启动都不再按窗口或随机休息日拦截。
"""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from threading import Event
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


if __name__ == "__main__":
    unittest.main()
