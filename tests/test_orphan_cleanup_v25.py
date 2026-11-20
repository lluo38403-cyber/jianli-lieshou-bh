"""v25 孤儿进程清理：统一清理、启动残留清理、关窗监视、退出接口。

覆盖（mock，不碰真实进程/浏览器）：
- shutdown_browser_resources 停 runtime 树；
- stop_launched_browser 只杀命令行含我们标记的进程、不碰其他 Chrome；
- 启动清理在有活实例时跳过、无实例时清残留；
- ensure_runtime(allow_launch=False) 不调 start_runtime；
- 关窗监视线程忽略 5 秒内退出、真关窗触发清理、更新进行中跳过；
- POST /api/exit 先停任务再统一清理最后退出进程。
Windows 相关逻辑一律用 _is_windows() 小函数守、测试里 mock，
绝不 patch os.name（会连 pathlib 一起改成 WindowsPath）。
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


def _browser_config(**overrides):
    browser = {
        "runtime": "builtin",
        "auto_start_proxy": True,
        "proxy_host": "127.0.0.1",
        "proxy_port": 3456,
        "chrome_ports": [9222, 9229, 9333],
        "auto_launch_browser": True,
    }
    browser.update(overrides)
    return {"browser": browser}


class ShutdownRuntimeTests(unittest.TestCase):
    def test_shutdown_stops_runtime_tree(self):
        import bosshunter.browser.runtime as runtime

        proc = Mock()
        proc.poll.return_value = None
        proc.wait.return_value = None
        runtime._runtime_process = proc
        self.addCleanup(lambda: setattr(runtime, "_runtime_process", None))
        with (
            patch("bosshunter.browser.runtime._is_windows", return_value=True),
            patch("bosshunter.browser.runtime.subprocess.run") as run,
            patch("bosshunter.browser.runtime.stop_launched_browser", return_value=True) as stop_chrome,
        ):
            proc.poll.return_value = None
            # stop_runtime 在 taskkill 后调 proc.wait；wait 后 poll
            # 返回 None 才能算“停掉了”——这里让第二次 poll 返回 0。
            proc.poll.side_effect = [None, 0]
            self.assertTrue(runtime.shutdown_browser_resources())
        run.assert_called_once()
        argv = run.call_args.args[0]
        self.assertEqual(argv[:2], ["taskkill", "/PID"])
        self.assertIn(str(proc.pid), argv)
        self.assertIn("/T", argv)
        stop_chrome.assert_called_once()

    def test_shutdown_is_idempotent_when_nothing_running(self):
        import bosshunter.browser.runtime as runtime

        runtime._runtime_process = None
        runtime._launched_browser = None
        with patch("bosshunter.browser.runtime.stop_launched_browser", return_value=True):
            self.assertTrue(runtime.shutdown_browser_resources())
            self.assertTrue(runtime.shutdown_browser_resources())


class StopLaunchedBrowserTests(unittest.TestCase):
    def _record(self, runtime, pid, profile_dir, port):
        proc = Mock()
        proc.poll.return_value = None
        proc.pid = pid
        runtime._launched_browser = {
            "pid": pid,
            "executable": "C:/Chrome/chrome.exe",
            "profile_dir": str(profile_dir),
            "port": port,
            "proc": proc,
        }
        return proc

    def test_kills_only_chrome_with_our_markers(self):
        import bosshunter.browser.runtime as runtime

        profile = Path("C:/Users/x/.jianli-lieshou/browser-profile")
        self._record(runtime, 1234, profile, 9222)
        self.addCleanup(lambda: setattr(runtime, "_launched_browser", None))
        cmdline = (
            "C:/Chrome/chrome.exe --remote-debugging-port=9222 "
            f"--user-data-dir={profile} --no-first-run"
        )
        with (
            patch("bosshunter.browser.runtime._process_info",
                  return_value={"executable": "C:/Chrome/chrome.exe", "cmdline": cmdline}),
            patch("bosshunter.browser.runtime._kill_process_tree", return_value=True) as kill,
            patch("bosshunter.browser.runtime._clear_browser_state"),
        ):
            self.assertTrue(runtime.stop_launched_browser())
        kill.assert_called_once_with(1234, 10.0)

    def test_does_not_kill_chrome_without_our_markers(self):
        import bosshunter.browser.runtime as runtime

        profile = Path("C:/Users/x/.jianli-lieshou/browser-profile")
        self._record(runtime, 1234, profile, 9222)
        self.addCleanup(lambda: setattr(runtime, "_launched_browser", None))
        # 用户自己的 Chrome：默认配置目录、没有调试端口。
        cmdline = "C:/Chrome/chrome.exe --user-data-dir=C:/Users/x/AppData/Local/Google/Chrome/User Data"
        with (
            patch("bosshunter.browser.runtime._process_info",
                  return_value={"executable": "C:/Chrome/chrome.exe", "cmdline": cmdline}),
            patch("bosshunter.browser.runtime._kill_process_tree") as kill,
            patch("bosshunter.browser.runtime._clear_browser_state"),
        ):
            self.assertFalse(runtime.stop_launched_browser())
        kill.assert_not_called()

    def test_already_exited_chrome_is_a_noop(self):
        import bosshunter.browser.runtime as runtime

        proc = self._record(runtime, 1234, Path("C:/p"), 9222)
        proc.poll.return_value = 0
        self.addCleanup(lambda: setattr(runtime, "_launched_browser", None))
        with (
            patch("bosshunter.browser.runtime._process_info") as info,
            patch("bosshunter.browser.runtime._kill_process_tree") as kill,
            patch("bosshunter.browser.runtime._clear_browser_state"),
        ):
            self.assertTrue(runtime.stop_launched_browser())
        info.assert_not_called()
        kill.assert_not_called()

    def test_state_file_record_is_used_when_memory_record_is_gone(self):
        # 强杀 exe 后进程内记录丢了，下次启动靠 state 文件清 Chrome。
        import bosshunter.browser.runtime as runtime

        runtime._launched_browser = None
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            runtime.set_data_dir(data_dir)
            self.addCleanup(lambda: runtime.set_data_dir(None))
            profile = Path("C:/Users/x/.jianli-lieshou/browser-profile")
            payload = {
                "pid": 777,
                "executable": "C:/Chrome/chrome.exe",
                "profile_dir": str(profile),
                "port": 9222,
            }
            (data_dir / runtime.BROWSER_STATE_FILENAME).write_text(
                json.dumps(payload), encoding="utf-8"
            )
            cmdline = f"C:/Chrome/chrome.exe --remote-debugging-port=9222 --user-data-dir={profile}"
            with (
                patch("bosshunter.browser.runtime._process_info",
                      return_value={"executable": "C:/Chrome/chrome.exe", "cmdline": cmdline}),
                patch("bosshunter.browser.runtime._kill_process_tree", return_value=True) as kill,
            ):
                self.assertTrue(runtime.stop_launched_browser())
            kill.assert_called_once_with(777, 10.0)
            self.assertFalse((data_dir / runtime.BROWSER_STATE_FILENAME).exists())


class StartupCleanupTests(unittest.TestCase):
    def setUp(self):
        import bosshunter.browser.runtime as runtime

        self.runtime = runtime
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name)
        runtime.set_data_dir(self.data_dir)
        self.addCleanup(lambda: runtime.set_data_dir(None))
        runtime._launched_browser = None
        self.addCleanup(lambda: setattr(runtime, "_launched_browser", None))

    def test_skips_when_live_instance_pid_is_ours(self):
        instance = {"pid": os.getpid(), "executable": "python", "port": 8686}
        (self.data_dir / self.runtime.INSTANCE_FILENAME).write_text(
            json.dumps(instance), encoding="utf-8"
        )
        with (
            patch("bosshunter.app_window.is_our_service_alive", return_value=False),
            patch("bosshunter.browser.runtime._is_pid_our_exe", return_value=True),
            patch("bosshunter.browser.runtime.stop_launched_browser") as stop_chrome,
        ):
            self.assertFalse(
                self.runtime.cleanup_stale_browser_resources(data_dir=self.data_dir, port=8686)
            )
        stop_chrome.assert_not_called()
        # 活实例的实例文件不删。
        self.assertTrue((self.data_dir / self.runtime.INSTANCE_FILENAME).exists())

    def test_skips_when_service_answers_on_port(self):
        with (
            patch("bosshunter.app_window.is_our_service_alive", return_value=True),
            patch("bosshunter.browser.runtime.stop_launched_browser") as stop_chrome,
        ):
            self.assertFalse(
                self.runtime.cleanup_stale_browser_resources(data_dir=self.data_dir, port=8686)
            )
        stop_chrome.assert_not_called()

    def test_cleans_stale_state_when_no_instance_alive(self):
        profile = Path("C:/Users/x/.jianli-lieshou/browser-profile")
        payload = {
            "pid": 777,
            "executable": "C:/Chrome/chrome.exe",
            "profile_dir": str(profile),
            "port": 9222,
        }
        (self.data_dir / self.runtime.BROWSER_STATE_FILENAME).write_text(
            json.dumps(payload), encoding="utf-8"
        )
        # 旧实例文件记了一个已死的 PID。
        (self.data_dir / self.runtime.INSTANCE_FILENAME).write_text(
            json.dumps({"pid": 999999, "executable": "x", "port": 8686}), encoding="utf-8"
        )
        cmdline = f"C:/Chrome/chrome.exe --remote-debugging-port=9222 --user-data-dir={profile}"
        with (
            patch("bosshunter.app_window.is_our_service_alive", return_value=False),
            patch("bosshunter.browser.runtime._is_pid_our_exe", return_value=False),
            patch("bosshunter.browser.runtime._process_info",
                  return_value={"executable": "C:/Chrome/chrome.exe", "cmdline": cmdline}),
            patch("bosshunter.browser.runtime._kill_process_tree", return_value=True) as kill,
        ):
            self.assertTrue(
                self.runtime.cleanup_stale_browser_resources(data_dir=self.data_dir, port=8686)
            )
        kill.assert_called_once_with(777, 10.0)
        self.assertFalse((self.data_dir / self.runtime.BROWSER_STATE_FILENAME).exists())
        self.assertFalse((self.data_dir / self.runtime.INSTANCE_FILENAME).exists())


class EnsureRuntimeNoLaunchTests(unittest.TestCase):
    def test_allow_launch_false_never_starts_runtime(self):
        from bosshunter.browser.runtime import ensure_runtime

        with (
            patch("bosshunter.browser.runtime.time.sleep"),
            patch("bosshunter.browser.runtime._is_port_available", return_value=True),
            patch("bosshunter.browser.runtime.runtime_health", return_value=None),
            patch("bosshunter.browser.runtime.runtime_targets", return_value=None),
            patch("bosshunter.browser.runtime.start_runtime") as start,
            patch("bosshunter.browser.runtime.check_node_available",
                  return_value={"available": True, "version": "v22.1.0"}),
            patch("bosshunter.browser.runtime.launch_debuggable_browser") as launch,
        ):
            self.assertFalse(ensure_runtime(_browser_config(), wait_seconds=0, allow_launch=False))
        start.assert_not_called()
        launch.assert_not_called()

    def test_allow_launch_false_returns_true_when_runtime_already_up(self):
        from bosshunter.browser.runtime import ensure_runtime

        with (
            patch("bosshunter.browser.runtime.time.sleep"),
            patch("bosshunter.browser.runtime.runtime_targets",
                  return_value=[{"targetId": "abc"}]),
            patch("bosshunter.browser.runtime.start_runtime") as start,
        ):
            self.assertTrue(ensure_runtime(_browser_config(), wait_seconds=0, allow_launch=False))
        start.assert_not_called()


class WatchAppWindowCloseTests(unittest.TestCase):
    def _fake_clock(self, times):
        it = iter(times)

        def _clock():
            try:
                return next(it)
            except StopIteration:
                return times[-1]

        return _clock

    def test_ignores_exit_within_grace_seconds(self):
        import bosshunter.app_window as app_window

        proc = Mock()
        proc.poll.return_value = 0  # 已经退了
        fired = []
        # 窗口拉起在 t=100，监视在 t=101 看到它已退出：只活了 1 秒，
        # 是 Chrome 交给已在跑实例的假退出，不触发。
        result = app_window.watch_app_window_close(
            lambda: fired.append(True),
            grace_seconds=5.0,
            poll_interval=0.01,
            wait_for_proc_seconds=30.0,
            _get_proc=lambda: proc,
            _started_at=100.0,
            _is_update_applying=lambda: False,
            _clock=self._fake_clock([101.0, 101.0, 101.0]),
            _sleep=lambda _: None,
        )
        self.assertFalse(result)
        self.assertEqual(fired, [])

    def test_genuine_close_after_grace_triggers_on_close(self):
        import bosshunter.app_window as app_window

        proc = Mock()
        # 前两次轮询还活着，第三次退了；退出时已活过 5 秒。
        proc.poll.side_effect = [None, None, 0]
        fired = []
        result = app_window.watch_app_window_close(
            lambda: fired.append(True),
            grace_seconds=5.0,
            poll_interval=0.01,
            wait_for_proc_seconds=30.0,
            _get_proc=lambda: proc,
            _started_at=100.0,
            _is_update_applying=lambda: False,
            _clock=self._fake_clock([100.0, 103.0, 106.0, 106.0]),
            _sleep=lambda _: None,
        )
        self.assertTrue(result)
        self.assertEqual(fired, [True])

    def test_skips_while_update_is_applying(self):
        import bosshunter.app_window as app_window

        proc = Mock()
        proc.poll.return_value = 0
        fired = []
        result = app_window.watch_app_window_close(
            lambda: fired.append(True),
            grace_seconds=5.0,
            poll_interval=0.01,
            wait_for_proc_seconds=30.0,
            _get_proc=lambda: proc,
            _started_at=100.0,
            _is_update_applying=lambda: True,
            _clock=self._fake_clock([200.0, 200.0]),
            _sleep=lambda _: None,
        )
        self.assertFalse(result)
        self.assertEqual(fired, [])

    def test_returns_false_when_no_window_proc_appears(self):
        import bosshunter.app_window as app_window

        fired = []
        result = app_window.watch_app_window_close(
            lambda: fired.append(True),
            grace_seconds=5.0,
            poll_interval=0.01,
            wait_for_proc_seconds=0.05,
            _get_proc=lambda: None,
            _started_at=100.0,
            _is_update_applying=lambda: False,
            _clock=self._fake_clock([100.0, 100.1, 100.2]),
            _sleep=lambda _: None,
        )
        self.assertFalse(result)
        self.assertEqual(fired, [])

    def test_panel_tab_disappearing_while_proc_lingers_triggers_on_close(self):
        # v27：带调试端口的 Chrome 关掉最后一个窗口后进程可能还留着，
        # 只看句柄看不到关窗；面板标签页出现过、后来没了，就是关窗。
        import bosshunter.app_window as app_window

        proc = Mock()
        proc.poll.return_value = None  # 进程一直活着（ lingering 实例）
        fired = []
        tab_lists = iter([
            ["http://127.0.0.1:8686/"],
            ["http://127.0.0.1:8686/", "https://www.zhipin.com/web/geek/job"],
            ["https://www.zhipin.com/web/geek/job"],
        ])
        result = app_window.watch_app_window_close(
            lambda: fired.append(True),
            grace_seconds=5.0,
            poll_interval=0.01,
            wait_for_proc_seconds=30.0,
            _get_proc=lambda: proc,
            _started_at=100.0,
            _is_update_applying=lambda: False,
            _clock=self._fake_clock([100.0, 100.0, 100.0, 100.0, 100.0]),
            _sleep=lambda _: None,
            _panel_url="http://127.0.0.1:8686",
            _list_tabs=lambda: next(tab_lists),
        )
        self.assertTrue(result)
        self.assertEqual(fired, [True])

    def test_handoff_fake_exit_switches_to_tab_watch(self):
        # v27：句柄在 grace 内就退出（交接给已在跑实例），不再直接
        # 返回 False——改看标签页；面板标签页后来没了，照样触发。
        import bosshunter.app_window as app_window

        proc = Mock()
        proc.poll.return_value = 0  # 交接，立刻就退了
        fired = []
        tab_lists = iter([
            ["http://127.0.0.1:8686/"],
            [],
        ])
        result = app_window.watch_app_window_close(
            lambda: fired.append(True),
            grace_seconds=5.0,
            poll_interval=0.01,
            wait_for_proc_seconds=30.0,
            _get_proc=lambda: proc,
            _started_at=100.0,
            _is_update_applying=lambda: False,
            _clock=self._fake_clock([100.0, 101.0, 101.0, 101.0, 101.0]),
            _sleep=lambda _: None,
            _panel_url="http://127.0.0.1:8686",
            _list_tabs=lambda: next(tab_lists),
        )
        self.assertTrue(result)
        self.assertEqual(fired, [True])

    def test_tab_watch_gives_up_when_panel_never_appears(self):
        # v27：交接后 60 秒一直没看到面板标签页（看的不是我们的
        # 实例），放弃监视，不是一直轮询下去。
        import bosshunter.app_window as app_window

        proc = Mock()
        proc.poll.return_value = 0
        fired = []
        result = app_window.watch_app_window_close(
            lambda: fired.append(True),
            grace_seconds=5.0,
            poll_interval=0.01,
            wait_for_proc_seconds=30.0,
            _get_proc=lambda: proc,
            _started_at=100.0,
            _is_update_applying=lambda: False,
            _clock=self._fake_clock([100.0, 101.0, 101.0, 200.0, 300.0]),
            _sleep=lambda _: None,
            _panel_url="http://127.0.0.1:8686",
            _list_tabs=lambda: ["https://www.zhipin.com/web/geek/job"],
            _tab_watch_seconds=60.0,
        )
        self.assertFalse(result)
        self.assertEqual(fired, [])

    def test_unreachable_debug_port_is_not_a_close(self):
        # v27：端口没应答（问不到标签页）不能读成“面板关了”——
        # 否则浏览器一卡就把程序退了。
        import bosshunter.app_window as app_window

        proc = Mock()
        proc.poll.side_effect = [None, None, 0]
        fired = []
        tab_lists = iter([
            ["http://127.0.0.1:8686/"],
            None,
            None,
        ])
        result = app_window.watch_app_window_close(
            lambda: fired.append(True),
            grace_seconds=5.0,
            poll_interval=0.01,
            wait_for_proc_seconds=30.0,
            _get_proc=lambda: proc,
            _started_at=100.0,
            _is_update_applying=lambda: False,
            _clock=self._fake_clock([100.0, 103.0, 106.0, 106.0]),
            _sleep=lambda _: None,
            _panel_url="http://127.0.0.1:8686",
            _list_tabs=lambda: next(tab_lists),
        )
        self.assertTrue(result)
        self.assertEqual(fired, [True])


class ApiExitTests(unittest.TestCase):
    def test_exit_stops_task_then_cleans_up_then_exits(self):
        import json as _json

        from bosshunter.web import server

        with (
            patch("bosshunter.web.server.threading.Thread") as thread_cls,
            patch("bosshunter.web.server.task_runner") as runner,
        ):
            thread_cls.return_value.start.return_value = None
            resp = server.api_exit()
            self.assertTrue(_json.loads(resp)["exiting"])
            target = thread_cls.call_args.kwargs["target"]
            runner.status.return_value = {"active": {"id": "task-1"}}
            with (
                patch("bosshunter.web.server.time.sleep"),
                patch("bosshunter.web.server.os._exit") as exit_mock,
                patch("bosshunter.browser.runtime.shutdown_browser_resources") as shutdown,
            ):
                target()
            runner.stop.assert_called_once_with("task-1", "用户退出应用程序")
            runner.wait.assert_called_once_with(timeout=5)
            shutdown.assert_called_once()
            exit_mock.assert_called_once_with(0)

    def test_exit_without_active_task_still_cleans_up(self):
        import json as _json

        from bosshunter.web import server

        with (
            patch("bosshunter.web.server.threading.Thread") as thread_cls,
            patch("bosshunter.web.server.task_runner") as runner,
        ):
            thread_cls.return_value.start.return_value = None
            resp = server.api_exit()
            self.assertTrue(_json.loads(resp)["exiting"])
            target = thread_cls.call_args.kwargs["target"]
            runner.status.return_value = {"active": None}
            with (
                patch("bosshunter.web.server.time.sleep"),
                patch("bosshunter.web.server.os._exit") as exit_mock,
                patch("bosshunter.browser.runtime.shutdown_browser_resources") as shutdown,
            ):
                target()
            runner.stop.assert_not_called()
            shutdown.assert_called_once()
            exit_mock.assert_called_once_with(0)


class RunServerFreshStartTests(unittest.TestCase):
    def test_fresh_start_cleans_stale_resources_writes_instance_and_watches_window(self):
        from bosshunter.web import server

        with (
            patch("bosshunter.updater.cleanup_stale_update_processes"),
            patch("bosshunter.updater.ensure_desktop_shortcut"),
            patch("bosshunter.app_window.is_our_service_alive", return_value=False),
            patch("bosshunter.app_window.launch_splash_window") as splash,
            patch("bosshunter.collection_session_store.list_sessions", return_value=[]),
            patch("bosshunter.browser.runtime.set_data_dir") as set_data,
            patch("bosshunter.browser.runtime.cleanup_stale_browser_resources") as stale,
            patch("bosshunter.browser.runtime.write_instance_file") as write_inst,
            patch("bosshunter.web.server._install_shutdown_hooks") as hooks,
            patch("bosshunter.web.server.threading.Thread") as thread_cls,
            patch("bosshunter.web.server.start_login_patrol"),
            patch.object(server, "app") as app,
        ):
            server.run_server(host="127.0.0.1", port=8686, open_browser=True)
        set_data.assert_called_once_with(server.DATA_DIR)
        stale.assert_called_once_with(data_dir=server.DATA_DIR, port=8686)
        write_inst.assert_called_once()
        hooks.assert_called_once_with(server.DATA_DIR, 8686)
        app.run.assert_called_once()
        # v28：启动页替代了 _open 线程的“等服务就绪再开窗”；重型清理
        # 改到守护线程，不挡首屏。
        splash.assert_called_once_with("http://127.0.0.1:8686")
        targets = [c.kwargs["target"].__name__ for c in thread_cls.call_args_list]
        self.assertIn("_startup_housekeeping", targets)
        self.assertIn("_watch_app_close", targets)
        self.assertNotIn("_open", targets)


if __name__ == "__main__":
    unittest.main()
