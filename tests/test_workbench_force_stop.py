"""Exercise WorkbenchTaskRunner.force_stop() and its run-generation guard.

The worker thread is a mock hung executor: it blocks until the test releases
it, so no real browser or network is involved. ``collect`` mode is used so the
executor runs inside ``browser_execution()`` exactly like production; the test
releases the lock before finishing.
"""

import threading
import unittest

from bosshunter.web.tasks import WorkbenchTaskRunner


class ForceStopTests(unittest.TestCase):
    def test_force_stop_marks_stopped_and_late_thread_return_is_discarded(self):
        release = threading.Event()
        started = threading.Event()

        def hung_executor(task, config):
            started.set()
            release.wait(timeout=30)

        runner = WorkbenchTaskRunner({"collect": hung_executor})
        snapshot = runner.start("collect", {})
        task_id = snapshot["id"]
        self.assertTrue(started.wait(timeout=5))
        self.assertEqual(runner.status()["active"]["status"], "running")

        stopped = runner.stop(task_id)
        self.assertEqual(stopped["status"], "stopping")

        # stop 后执行线程仍未结束（wait_seconds=0，不真等 10 秒）→ 强制标记。
        forced = runner.force_stop(task_id, wait_seconds=0)
        self.assertEqual(forced["status"], "stopped")
        self.assertEqual(forced["stop_reason"], "用户已强制停止")
        self.assertIsNone(forced["error"])

        # 旧线程最终返回时不得把任务状态复活：仍是 stopped，且原因未被覆盖。
        release.set()
        runner.wait(timeout=5)
        last = runner.status()["last_task"]
        self.assertEqual(last["id"], task_id)
        self.assertEqual(last["status"], "stopped")
        self.assertEqual(last["stop_reason"], "用户已强制停止")
        self.assertIsNone(last["error"])

    def test_force_stop_returns_natural_snapshot_when_thread_finishes_in_wait(self):
        def quick_executor(task, config):
            return None

        runner = WorkbenchTaskRunner({"score": quick_executor})
        snapshot = runner.start("score", {})
        forced = runner.force_stop(snapshot["id"], wait_seconds=5)
        self.assertEqual(forced["status"], "completed")

    def test_force_stop_on_terminal_task_is_a_noop(self):
        def quick_executor(task, config):
            return None

        runner = WorkbenchTaskRunner({"score": quick_executor})
        snapshot = runner.start("score", {})
        runner.wait(timeout=5)
        self.assertEqual(runner.status()["last_task"]["status"], "completed")
        forced = runner.force_stop(snapshot["id"], wait_seconds=0)
        self.assertEqual(forced["status"], "completed")

    def test_force_stop_unknown_task_raises_key_error(self):
        runner = WorkbenchTaskRunner({})
        with self.assertRaises(KeyError):
            runner.force_stop("no-such-task", wait_seconds=0)

    def test_new_task_after_force_stop_fails_fast_while_old_thread_alive(self):
        """force_stop 只改任务状态，不释放浏览器锁：旧线程还活着时新任务
        能 admission，但工作线程进 browser_execution() 时以 BrowserBusyError
        快速失败，不会撞进旧线程的浏览器会话。"""
        release = threading.Event()
        started = threading.Event()

        def hung_executor(task, config):
            started.set()
            release.wait(timeout=30)

        runner = WorkbenchTaskRunner({"collect": hung_executor})
        snapshot = runner.start("collect", {})
        self.assertTrue(started.wait(timeout=5))
        runner.stop(snapshot["id"])
        forced = runner.force_stop(snapshot["id"], wait_seconds=0)
        self.assertEqual(forced["status"], "stopped")

        try:
            new_snapshot = runner.start("collect", {})
            runner.wait(timeout=5)
            tasks = {task["id"]: task for task in runner.status()["tasks"]}
            self.assertEqual(tasks[new_snapshot["id"]]["status"], "failed")
            self.assertIn("浏览器正由其他后台任务使用", tasks[new_snapshot["id"]]["error"])
            # 旧任务仍是 stopped，没有被复活。
            self.assertEqual(tasks[snapshot["id"]]["status"], "stopped")
        finally:
            release.set()
            runner.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
