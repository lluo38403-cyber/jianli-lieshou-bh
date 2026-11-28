"""v54 图10：等待可跳过 + 倒计时上报——sleep_with_skip 与岗位间等待。

覆盖：跳过提前结束长等待但不停任务；停止仍优先；小于 5 秒的
等待不报倒计时；开始时已置上的跳过只跳当前一轮；自然归零清掉
倒计时；岗位间随机等待（RequestThrottle.wait）同样可报可跳。
"""

import threading
import time
import unittest
from threading import Event

from bosshunter.throttle import RequestThrottle, sleep_with_skip


class SleepWithSkipTests(unittest.TestCase):
    def test_skip_ends_long_wait_early_without_stopping(self):
        skip = Event()
        seen = []
        started = time.monotonic()
        # 300 秒的等待，0.2 秒后点跳过：立即结束、返回 False（没停）。
        threading.Timer(0.2, skip.set).start()
        stopped = sleep_with_skip(
            300, Event(), skip_event=skip, report=seen.append,
            kind="round_rest", label="已连续投递 20 个",
        )
        self.assertFalse(stopped)
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual(seen[0]["kind"], "round_rest")
        self.assertEqual(seen[0]["label"], "已连续投递 20 个")
        self.assertEqual(seen[0]["total_seconds"], 300)
        self.assertGreater(seen[0]["ends_at"], time.time())
        self.assertIsNone(seen[-1])  # 等完清掉倒计时

    def test_stop_still_wins_and_clears_report(self):
        stop = Event()
        seen = []
        threading.Timer(0.2, stop.set).start()
        stopped = sleep_with_skip(
            300, stop, skip_event=Event(), report=seen.append,
            kind="batch_rest", label="",
        )
        self.assertTrue(stopped)
        self.assertIsNone(seen[-1])

    def test_short_wait_is_not_reported(self):
        seen = []
        stopped = sleep_with_skip(
            2, Event(), skip_event=Event(), report=seen.append,
            kind="interval", label="",
        )
        self.assertFalse(stopped)
        self.assertEqual(seen, [])  # 小于 5 秒不报，倒计时闪不出来

    def test_skip_set_before_wait_skips_that_round_only(self):
        skip = Event()
        skip.set()
        seen = []
        stopped = sleep_with_skip(
            300, Event(), skip_event=skip, report=seen.append,
            kind="interval", label="",
        )
        self.assertFalse(stopped)
        self.assertFalse(skip.is_set())  # 一次点击只跳一轮，用完清掉
        self.assertEqual(seen, [])

    def test_natural_zero_clears_report(self):
        seen = []
        stopped = sleep_with_skip(
            5, Event(), skip_event=Event(), report=seen.append,
            kind="cooldown", label="安全冷却",
        )
        self.assertFalse(stopped)
        self.assertEqual(seen[0]["kind"], "cooldown")
        self.assertIsNone(seen[-1])


class RequestThrottleWaitTests(unittest.TestCase):
    def test_interval_wait_reports_and_is_skippable(self):
        # 区间 6 秒：mark 后 elapsed 吃掉零点几秒，total 仍稳在 5 秒以上才上报。
        throttle = RequestThrottle(delay_min=6, delay_max=6)
        throttle.mark()  # 没 mark 过时 elapsed 是十亿秒级，等待直接归零
        skip = Event()
        seen = []
        threading.Timer(0.2, skip.set).start()
        stopped = throttle.wait(Event(), skip_event=skip, report=seen.append)
        self.assertFalse(stopped)
        self.assertEqual(seen[0]["kind"], "interval")
        self.assertIsNone(seen[-1])

    def test_interval_wait_without_hooks_behaves_as_before(self):
        throttle = RequestThrottle(delay_min=0, delay_max=0)
        self.assertFalse(throttle.wait(Event()))


class WaitWithCountdownTests(unittest.TestCase):
    """v54 图10：发送端落点——没接倒计时/跳过时走老 _sleep_or_stop。

    轮休 300–600 秒、批次休息 60–180 秒：测试 mock _sleep_or_stop
    才不用真睡；这条分流一旦绕过 mock，全量测试会卡几分钟。
    """

    def test_unconfigured_delegates_to_sleep_or_stop(self):
        from unittest.mock import patch

        from bosshunter.executor import sender

        seen = []
        with patch.object(sender, "_sleep_or_stop", side_effect=lambda s, e: seen.append(s) or False):
            stopped = sender._wait_with_countdown(300, Event(), config={}, kind="round_rest", label="x")
        self.assertFalse(stopped)
        self.assertEqual(seen, [300])

    def test_configured_reports_and_is_skippable(self):
        from bosshunter.executor import sender

        skip = Event()
        seen = []
        threading.Timer(0.2, skip.set).start()
        config = {"_workbench_wait_skip_event": skip, "_workbench_wait_progress": seen.append}
        stopped = sender._wait_with_countdown(300, Event(), config=config, kind="batch_rest", label="")
        self.assertFalse(stopped)
        self.assertEqual(seen[0]["kind"], "batch_rest")
        self.assertIsNone(seen[-1])


if __name__ == "__main__":
    unittest.main()
