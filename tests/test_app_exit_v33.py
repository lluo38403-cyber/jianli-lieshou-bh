"""v33 task 3: unified exit confirmation + full background cleanup.

Covers (mocked, no real processes/browsers except the POSIX sweep test,
which only ever kills a copy of `sleep` it started itself):
- _stop_active_tasks stops the running task once (no repeat, no loop),
  and swallows failures instead of blocking the exit;
- _perform_exit_cleanup runs the same steps for /api/exit and for the
  window-close watcher: stop task -> shutdown_browser_resources ->
  install-dir sweep (own processes only) -> clear instance file;
- the install-dir sweep only ever kills by ExecutablePath prefix
  (Windows PS script) / exe path under the dir (POSIX), never by image
  name, and always excludes our own PID;
- the window-close watcher (_on_close) calls the same shared cleanup
  as /api/exit;
- both Chrome launch sites pass --lang=zh-CN so the beforeunload
  dialog is Chinese instead of English "Leave app?".
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


def _exit_route_target(server):
    """Call api_exit() with Thread mocked and return its _exit target."""
    with (
        patch("bosshunter.web.server.threading.Thread") as thread_cls,
        patch("bosshunter.web.server.task_runner") as runner,
    ):
        thread_cls.return_value.start.return_value = None
        resp = server.api_exit()
        assert json.loads(resp)["exiting"] is True
        return thread_cls.call_args.kwargs["target"], runner


class StopActiveTasksTests(unittest.TestCase):
    def test_stops_the_one_active_task_once_even_if_status_keeps_returning_it(self):
        from bosshunter.web import server

        runner = Mock()
        # status() 永远返回同一个进行中任务（stop 后是 "stopping"，仍算
        # active）：只停一次，不重复、不死循环。
        runner.status.return_value = {"active": {"id": "task-1"}}
        with patch("bosshunter.web.server.task_runner", runner):
            server._stop_active_tasks()
        runner.stop.assert_called_once_with("task-1", "用户退出应用程序")
        runner.wait.assert_called_once_with(timeout=5)

    def test_no_active_task_stops_nothing(self):
        from bosshunter.web import server

        runner = Mock()
        runner.status.return_value = {"active": None}
        with patch("bosshunter.web.server.task_runner", runner):
            server._stop_active_tasks()
        runner.stop.assert_not_called()
        runner.wait.assert_not_called()

    def test_stop_failure_does_not_block_exit(self):
        from bosshunter.web import server

        runner = Mock()
        runner.status.return_value = {"active": {"id": "task-1"}}
        runner.stop.side_effect = KeyError("task-1")
        with patch("bosshunter.web.server.task_runner", runner):
            server._stop_active_tasks()  # 不抛异常
        runner.wait.assert_called_once_with(timeout=5)

    def test_status_failure_does_not_block_exit(self):
        from bosshunter.web import server

        runner = Mock()
        runner.status.side_effect = RuntimeError("boom")
        with patch("bosshunter.web.server.task_runner", runner):
            server._stop_active_tasks()  # 不抛异常
        runner.stop.assert_not_called()


class PerformExitCleanupTests(unittest.TestCase):
    def _run_cleanup(self, server, runner):
        with (
            patch("bosshunter.web.server.task_runner", runner),
            patch("bosshunter.browser.runtime.shutdown_browser_resources") as shutdown,
            patch("bosshunter.browser.runtime.clear_instance_file") as clear,
            patch("bosshunter.updater.get_install_dir", return_value=None),
        ):
            server._perform_exit_cleanup()
        return shutdown, clear

    def test_cleanup_order_stop_then_shutdown_then_clear_instance(self):
        from bosshunter.web import server

        order = []
        runner = Mock()
        runner.status.return_value = {"active": {"id": "task-1"}}
        runner.stop.side_effect = lambda *a, **k: order.append("stop")
        with (
            patch("bosshunter.web.server.task_runner", runner),
            patch("bosshunter.browser.runtime.shutdown_browser_resources",
                  side_effect=lambda: order.append("shutdown")),
            patch("bosshunter.browser.runtime.clear_instance_file",
                  side_effect=lambda *a, **k: order.append("clear")),
            patch("bosshunter.updater.get_install_dir", return_value=None),
        ):
            server._perform_exit_cleanup()
        self.assertEqual(order, ["stop", "shutdown", "clear"])

    def test_dev_run_skips_install_dir_sweep(self):
        from bosshunter.web import server

        runner = Mock()
        runner.status.return_value = {"active": None}
        with patch("bosshunter.updater._kill_install_dir_processes") as kill:
            shutdown, clear = self._run_cleanup(server, runner)
        kill.assert_not_called()
        shutdown.assert_called_once()
        clear.assert_called_once_with(server.DATA_DIR)

    def test_frozen_windows_sweeps_install_dir_excluding_own_pid(self):
        from bosshunter.web import server

        runner = Mock()
        runner.status.return_value = {"active": None}
        install = Path("C:/jianli-lieshou")
        with (
            patch("bosshunter.web.server.task_runner", runner),
            patch("bosshunter.browser.runtime.shutdown_browser_resources"),
            patch("bosshunter.browser.runtime.clear_instance_file"),
            patch("bosshunter.updater.get_install_dir", return_value=install),
            patch("bosshunter.browser.runtime._is_windows", return_value=True),
            patch("bosshunter.updater._kill_install_dir_processes") as kill,
        ):
            server._perform_exit_cleanup()
        kill.assert_called_once_with(install, exclude_pid=os.getpid())

    def test_frozen_posix_sweeps_install_dir_by_exe_path(self):
        from bosshunter.web import server

        runner = Mock()
        runner.status.return_value = {"active": None}
        install = Path("/opt/jianli-lieshou")
        with (
            patch("bosshunter.web.server.task_runner", runner),
            patch("bosshunter.browser.runtime.shutdown_browser_resources"),
            patch("bosshunter.browser.runtime.clear_instance_file"),
            patch("bosshunter.updater.get_install_dir", return_value=install),
            patch("bosshunter.browser.runtime._is_windows", return_value=False),
            patch("bosshunter.browser.runtime._kill_processes_with_exe_under") as sweep,
        ):
            server._perform_exit_cleanup()
        sweep.assert_called_once_with(install)

    def test_one_failing_step_does_not_stop_the_rest(self):
        from bosshunter.web import server

        runner = Mock()
        runner.status.return_value = {"active": None}
        with (
            patch("bosshunter.web.server.task_runner", runner),
            patch("bosshunter.browser.runtime.shutdown_browser_resources",
                  side_effect=RuntimeError("runtime 炸了")),
            patch("bosshunter.browser.runtime.clear_instance_file") as clear,
            patch("bosshunter.updater.get_install_dir", return_value=None),
        ):
            server._perform_exit_cleanup()  # 不抛异常
        clear.assert_called_once_with(server.DATA_DIR)

    def test_api_exit_runs_shared_cleanup_then_exits(self):
        from bosshunter.web import server

        target, runner = _exit_route_target(server)
        runner.status.return_value = {"active": None}
        install = Path("C:/jianli-lieshou")
        with (
            patch("bosshunter.web.server.time.sleep"),
            patch("bosshunter.web.server.os._exit") as exit_mock,
            patch("bosshunter.browser.runtime.shutdown_browser_resources"),
            patch("bosshunter.browser.runtime.clear_instance_file"),
            patch("bosshunter.updater.get_install_dir", return_value=install),
            patch("bosshunter.browser.runtime._is_windows", return_value=True),
            patch("bosshunter.updater._kill_install_dir_processes") as kill,
        ):
            target()
        kill.assert_called_once_with(install, exclude_pid=os.getpid())
        exit_mock.assert_called_once_with(0)


class WatchCloseUsesSharedCleanupTests(unittest.TestCase):
    def test_window_close_runs_same_cleanup_as_api_exit(self):
        from bosshunter import app_window
        from bosshunter.web import server

        # run_server 会改全局启动阶段文案（set_splash_stage）：跑完恢复，
        # 不然同进程里后面的 splash 测试会读到被污染的值（既有
        # RunServerFreshStartTests 也有同样问题，见 test_app_window 的
        # test_health_proxy_reports_not_ready 跨文件联跑失败）。
        stage_before = app_window.get_splash_stage()
        self.addCleanup(lambda: app_window.set_splash_stage(stage_before))
        with (
            patch("bosshunter.updater.cleanup_stale_update_processes"),
            patch("bosshunter.updater.ensure_desktop_shortcut"),
            patch("bosshunter.app_window.is_our_service_alive", return_value=False),
            patch("bosshunter.app_window.launch_splash_window", return_value=(Mock(), None)),
            patch("bosshunter.collection_session_store.list_sessions", return_value=[]),
            patch("bosshunter.browser.runtime.set_data_dir"),
            patch("bosshunter.browser.runtime.cleanup_stale_browser_resources"),
            patch("bosshunter.browser.runtime.write_instance_file"),
            patch("bosshunter.web.server._install_shutdown_hooks"),
            patch("bosshunter.web.server.threading.Thread") as thread_cls,
            patch("bosshunter.web.server.start_login_patrol"),
            patch.object(server, "app") as app,
        ):
            server.run_server(host="127.0.0.1", port=8686, open_browser=True)
        targets = {
            c.kwargs["target"].__name__: c.kwargs["target"]
            for c in thread_cls.call_args_list
        }
        self.assertIn("_watch_app_close", targets)
        with (
            patch("bosshunter.app_window.watch_app_window_close") as watch,
            patch("bosshunter.web.server._perform_exit_cleanup") as cleanup,
            patch("bosshunter.web.server.os._exit") as exit_mock,
        ):
            targets["_watch_app_close"]()
            watch.assert_called_once()
            on_close = watch.call_args.args[0]
            # on_close 必须在 patch 上下文里调：它最后调 os._exit(0)，
            # 出了 with 块就是真的退出进程，pytest 当场结束。
            on_close()
        cleanup.assert_called_once_with()
        exit_mock.assert_called_once_with(0)


class InstallDirKillScriptTests(unittest.TestCase):
    def test_prefix_is_install_dir_plus_separator(self):
        from bosshunter.updater import _install_dir_kill_script

        script = _install_dir_kill_script("C:/jianli-lieshou")
        # 判定核心：前缀 = 安装目录 + 一个反斜杠（目录边界在内），
        # 兄弟目录 C:\jianli-lieshou2 不会被 C:\jianli-lieshou 误杀。
        self.assertIn("$prefix = $installDir + '\\'; ", script)
        self.assertIn(
            "StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)",
            script,
        )

    def test_exclude_pid_goes_into_script_only_when_given(self):
        from bosshunter.updater import _install_dir_kill_script

        self.assertIn(
            "-and $p.ProcessId -ne 4242",
            _install_dir_kill_script("C:/jianli-lieshou", exclude_pid=4242),
        )
        self.assertNotIn(
            "ProcessId -ne",
            _install_dir_kill_script("C:/jianli-lieshou"),
        )

    def test_never_kills_by_image_name(self):
        from bosshunter.updater import _install_dir_kill_script

        script = _install_dir_kill_script("C:/jianli-lieshou", exclude_pid=1)
        self.assertNotIn("Get-Process -Name", script)
        self.assertNotIn("chrome.exe", script)
        self.assertNotIn("node.exe", script)
        self.assertNotIn("/IM node", script)


class KillProcessesWithExeUnderTests(unittest.TestCase):
    def test_kills_only_processes_whose_exe_is_under_the_dir(self):
        import bosshunter.browser.runtime as runtime

        sleep_bin = shutil.which("sleep")
        self.assertIsNotNone(sleep_bin, "需要系统 sleep 做路径判定测试")
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            owned = install / "sleep"
            shutil.copy(sleep_bin, owned)
            ours = subprocess.Popen(
                [str(owned), "60"], start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            foreign = subprocess.Popen(
                [sleep_bin, "60"], start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                self.assertIsNone(ours.poll())
                self.assertIsNone(foreign.poll())
                # 注意：被杀的子进程会先变僵尸进程，父进程（测试）不
                # poll() 回收，_pid_alive（kill(pid, 0)）就一直为真、
                # _kill_process_tree 会等满超时。生产环境里被杀的是孤
                # 儿进程（init 会回收），不存在这个问题；测试里开线程
                # 扫、主线程同时 poll() 回收，才能看到真实的杀掉结果。
                import threading

                result = {}
                thread = threading.Thread(
                    target=lambda: result.setdefault(
                        "killed", runtime._kill_processes_with_exe_under(install)
                    ),
                    daemon=True,
                )
                thread.start()
                deadline = time.time() + 10
                while thread.is_alive() and time.time() < deadline:
                    ours.poll()  # 回收已死的子进程，僵尸才算真的没了
                    time.sleep(0.05)
                ours.poll()
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive(), "扫尾清理 10 秒还没完")
                self.assertTrue(result.get("killed"), "安装目录里的 sleep 必须被杀掉")
                self.assertIsNotNone(ours.poll(), "安装目录里的 sleep 必须被杀掉")
                self.assertIsNone(foreign.poll(), "系统目录的 sleep 绝不能被碰")
            finally:
                for proc in (ours, foreign):
                    try:
                        if proc.poll() is None:
                            proc.kill()
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        pass


class ChromeLangFlagTests(unittest.TestCase):
    def test_work_browser_launch_passes_lang_zh_cn(self):
        from bosshunter.browser.runtime import launch_debuggable_browser

        config = {"browser": {
            "runtime": "builtin",
            "auto_start_proxy": True,
            "auto_launch_browser": True,
            "chrome_ports": [9222],
        }}
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "profile"
            with (
                patch("bosshunter.browser.runtime.pick_chrome_debug_port", return_value=9222),
                patch("bosshunter.browser.runtime._chrome_debug_version",
                      side_effect=[None, {"webSocketDebuggerUrl": "ws://x"}]),
                patch("bosshunter.browser.diagnostics.resolve_browser_executable",
                      return_value={"executable": "C:/Chrome/chrome.exe"}),
                patch("bosshunter.browser.runtime.get_browser_profile_dir", return_value=profile),
                patch("bosshunter.browser.runtime._record_launched_browser"),
                patch("bosshunter.browser.runtime.subprocess.Popen") as popen,
            ):
                self.assertTrue(launch_debuggable_browser(config))
        argv = popen.call_args.args[0]
        self.assertIn("--lang=zh-CN", argv)
        self.assertIn("--remote-debugging-port=9222", argv)


if __name__ == "__main__":
    unittest.main()
