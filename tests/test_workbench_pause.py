"""Exercise WorkbenchTaskRunner.pause()/resume() and the pause gate.

A paused delivery task must hold its place (no other task can start),
wait between sends without burning the queue, and resume or stop on
request. The worker is a mock executor that drains a fake queue through
wait_while_paused, so no browser or network is involved.
"""

import threading
import time
import unittest

from bosshunter.cancellation import wait_while_paused
from bosshunter.web.tasks import TaskAlreadyRunningError, WorkbenchTaskRunner


class WorkbenchPauseTests(unittest.TestCase):
    def test_pause_resume_round_trip_and_paused_task_holds_the_active_slot(self):
        release = threading.Event()
        started = threading.Event()

        def hung_executor(task, config):
            started.set()
            release.wait(timeout=30)

        runner = WorkbenchTaskRunner({"collect": hung_executor})
        snapshot = runner.start("collect", {})
        task_id = snapshot["id"]
        self.assertTrue(started.wait(timeout=5))
        try:
            paused = runner.pause(task_id)
            self.assertEqual(paused["status"], "paused")
            self.assertTrue(paused["pause_requested"])
            # 暂停的任务仍占着活动位：新任务进不来。
            with self.assertRaises(TaskAlreadyRunningError):
                runner.start("score", {})
            resumed = runner.resume(task_id)
            self.assertEqual(resumed["status"], "running")
            self.assertFalse(resumed["pause_requested"])
        finally:
            runner.stop(task_id)
            release.set()
            runner.wait(timeout=5)

    def test_pause_on_terminal_task_is_a_noop(self):
        def quick_executor(task, config):
            return None

        runner = WorkbenchTaskRunner({"score": quick_executor})
        snapshot = runner.start("score", {})
        runner.wait(timeout=5)
        self.assertEqual(runner.status()["last_task"]["status"], "completed")
        paused = runner.pause(snapshot["id"])
        self.assertEqual(paused["status"], "completed")

    def test_resume_on_running_task_is_a_noop(self):
        release = threading.Event()
        started = threading.Event()

        def hung_executor(task, config):
            started.set()
            release.wait(timeout=30)

        runner = WorkbenchTaskRunner({"collect": hung_executor})
        snapshot = runner.start("collect", {})
        task_id = snapshot["id"]
        self.assertTrue(started.wait(timeout=5))
        try:
            resumed = runner.resume(task_id)
            self.assertEqual(resumed["status"], "running")
        finally:
            runner.stop(task_id)
            release.set()
            runner.wait(timeout=5)

    def test_pause_unknown_task_raises_key_error(self):
        runner = WorkbenchTaskRunner({})
        with self.assertRaises(KeyError):
            runner.pause("no-such-task")
        with self.assertRaises(KeyError):
            runner.resume("no-such-task")

    def test_paused_worker_makes_no_progress_until_resumed(self):
        progress = []

        def pausing_executor(task, config):
            for index in range(5):
                if wait_while_paused(task.pause_requested, task.stop_requested):
                    return
                progress.append(index)
                time.sleep(0.05)

        runner = WorkbenchTaskRunner({"deliver": pausing_executor})
        snapshot = runner.start("deliver", {})
        task_id = snapshot["id"]
        time.sleep(0.2)
        runner.pause(task_id)
        count_while_paused = len(progress)
        self.assertGreater(count_while_paused, 0)
        time.sleep(0.5)
        self.assertEqual(len(progress), count_while_paused)
        runner.resume(task_id)
        runner.wait(timeout=5)
        self.assertEqual(runner.status()["last_task"]["status"], "completed")
        self.assertEqual(len(progress), 5)

    def test_stop_from_paused_ends_the_worker(self):
        started = threading.Event()

        def pausing_executor(task, config):
            started.set()
            if wait_while_paused(task.pause_requested, task.stop_requested):
                return
            task.stop_requested.wait(timeout=30)

        runner = WorkbenchTaskRunner({"deliver": pausing_executor})
        snapshot = runner.start("deliver", {})
        task_id = snapshot["id"]
        self.assertTrue(started.wait(timeout=5))
        runner.pause(task_id)
        self.assertEqual(runner.status()["active"]["status"], "paused")
        stopped = runner.stop(task_id)
        self.assertEqual(stopped["status"], "stopping")
        runner.wait(timeout=5)
        last = runner.status()["last_task"]
        self.assertEqual(last["status"], "stopped")
        self.assertFalse(last["pause_requested"])


if __name__ == "__main__":
    unittest.main()
