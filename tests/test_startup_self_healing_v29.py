"""启动自愈与启动失败可见性（v29）：分支 A 复用核实、绑端口失败重试、
致命失败可见报错、关窗监视收紧、启动页失败态、launcher 兜底。"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import bosshunter.app_window as app_window


def _fake_clock(times):
    it = iter(times)

    def _clock():
        try:
            return next(it)
        except StopIteration:
            return times[-1]

    return _clock


class PanelTabWaitTests(unittest.TestCase):
    def test_returns_true_when_panel_tab_appears(self):
        self.assertTrue(
            app_window.wait_for_panel_tab(
                "http://127.0.0.1:8686",
                9222,
                timeout=5.0,
                interval=0.01,
                _list_tabs=lambda: ["http://127.0.0.1:8686/"],
            )
        )

    def test_returns_false_when_port_answers_but_no_panel_tab(self):
        # 调试端口应答、一直没看到面板标签页：窗口没开出来。
        self.assertFalse(
            app_window.wait_for_panel_tab(
                "http://127.0.0.1:8686",
                9222,
                timeout=0.05,
                interval=0.01,
                _list_tabs=lambda: ["https://www.zhipin.com/web/geek/job"],
            )
        )

    def test_returns_none_when_port_never_answers(self):
        # 端口一直没应答：无法核实，绝不能读成“没有标签页”。
        self.assertIsNone(
            app_window.wait_for_panel_tab(
                "http://127.0.0.1:8686",
                9222,
                timeout=0.05,
                interval=0.01,
                _list_tabs=lambda: None,
            )
        )

    def test_returns_none_without_debug_port(self):
        self.assertIsNone(app_window.wait_for_panel_tab("http://127.0.0.1:8686", None))


class RunServerReuseVerifyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._data_dir = Path(self._tmp.name) / "data"
        self.addCleanup(lambda: app_window.set_splash_failure_marker_path(None))

    def _patch_branch_b(self, app_run=None):
        from bosshunter.web import server

        self.enterContext(patch.object(server, "DATA_DIR", self._data_dir))
        self.enterContext(patch("bosshunter.app_window.launch_splash_window", return_value=(Mock(), None)))
        self.enterContext(patch("bosshunter.collection_session_store.list_sessions", return_value=[]))
        self.enterContext(patch("bosshunter.browser.runtime.set_data_dir"))
        self.enterContext(patch("bosshunter.browser.runtime.cleanup_stale_browser_resources", return_value=False))
        self.enterContext(patch("bosshunter.browser.runtime.write_instance_file"))
        self.enterContext(patch("bosshunter.web.server._install_shutdown_hooks"))
        self.enterContext(patch("bosshunter.web.server.start_login_patrol"))
        self.enterContext(patch("bosshunter.web.server.threading.Thread"))
        self.enterContext(patch("bosshunter.web.server._show_startup_error_box", return_value=False))
        app_mock = self.enterContext(patch.object(server, "app"))
        if app_run is not None:
            app_mock.run.side_effect = app_run
        return app_mock

    def test_window_verified_reuses_service_without_starting_a_new_one(self):
        from bosshunter.web import server

        app_mock = self._patch_branch_b()
        with (
            patch("bosshunter.app_window.is_our_service_alive", return_value=True),
            patch("bosshunter.app_window.launch_app_window") as launch,
            patch("bosshunter.app_window.wait_for_panel_tab", return_value=True) as wait,
            patch("bosshunter.web.server._stop_stale_our_service") as stop,
        ):
            server.run_server(host="127.0.0.1", port=8686, open_browser=True)

        launch.assert_called_once_with("http://127.0.0.1:8686")
        wait.assert_called_once()
        stop.assert_not_called()
        app_mock.run.assert_not_called()

    def test_window_never_appears_stops_stale_service_and_starts_a_new_one(self):
        from bosshunter.web import server

        app_mock = self._patch_branch_b()
        with (
            patch("bosshunter.app_window.is_our_service_alive", return_value=True),
            patch("bosshunter.app_window.launch_app_window") as launch,
            patch("bosshunter.app_window.wait_for_panel_tab", return_value=False),
            patch("bosshunter.web.server._stop_stale_our_service", return_value=True) as stop,
        ):
            server.run_server(host="127.0.0.1", port=8686, open_browser=True)

        # 调试端口应答、3 秒没看到面板标签页：无窗口残留旧服务。
        launch.assert_called_once_with("http://127.0.0.1:8686")
        stop.assert_called_once_with(self._data_dir, 8686)
        app_mock.run.assert_called_once()

    def test_stale_service_we_cannot_identify_keeps_reuse(self):
        # 残留服务认不出（instance 文件没有、或 pid 不是我们的 exe）
        # 就不动手、不自己起新服务：宁可保持复用，也不误杀别的进程。
        from bosshunter.web import server

        app_mock = self._patch_branch_b()
        with (
            patch("bosshunter.app_window.is_our_service_alive", return_value=True),
            patch("bosshunter.app_window.launch_app_window") as launch,
            patch("bosshunter.app_window.wait_for_panel_tab", return_value=False),
            patch("bosshunter.web.server._stop_stale_our_service", return_value=False) as stop,
        ):
            server.run_server(host="127.0.0.1", port=8686, open_browser=True)

        launch.assert_called_once_with("http://127.0.0.1:8686")
        stop.assert_called_once_with(self._data_dir, 8686)
        app_mock.run.assert_not_called()


class StopStaleServiceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._data_dir = Path(self._tmp.name) / "data"
        self._data_dir.mkdir(parents=True)

    def test_no_instance_file_means_do_not_touch_anything(self):
        from bosshunter.web import server

        with patch.object(server, "DATA_DIR", self._data_dir):
            self.assertFalse(server._stop_stale_our_service(self._data_dir, 8686))

    def test_instance_pid_that_is_not_our_exe_is_left_alone(self):
        from bosshunter.web import server

        # pid 1 是系统进程，不可能是我们的 exe：必须原样放过。
        (self._data_dir / ".instance.json").write_text(
            json.dumps({"pid": 1, "executable": "/sbin/init", "port": 8686}),
            encoding="utf-8",
        )
        with patch.object(server, "DATA_DIR", self._data_dir):
            self.assertFalse(server._stop_stale_our_service(self._data_dir, 8686))
        log = (self._data_dir / "logs" / "jianli-lieshou.log").read_text(encoding="utf-8")
        self.assertIn("不是我们的 exe", log)


class PortBindFailureTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._data_dir = Path(self._tmp.name) / "data"
        self.addCleanup(lambda: app_window.set_splash_failure_marker_path(None))

    def _patch_branch_b(self, app_run=None, sessions=None):
        from bosshunter.web import server

        self.enterContext(patch.object(server, "DATA_DIR", self._data_dir))
        self.enterContext(patch("bosshunter.app_window.is_our_service_alive", return_value=False))
        self.enterContext(patch("bosshunter.collection_session_store.list_sessions", return_value=[] if sessions is None else sessions))
        self.enterContext(patch("bosshunter.browser.runtime.set_data_dir"))
        self.enterContext(patch("bosshunter.browser.runtime.cleanup_stale_browser_resources", return_value=False))
        self.enterContext(patch("bosshunter.browser.runtime.write_instance_file"))
        self.enterContext(patch("bosshunter.web.server._install_shutdown_hooks"))
        self.enterContext(patch("bosshunter.web.server.start_login_patrol"))
        self.enterContext(patch("bosshunter.web.server.threading.Thread"))
        box = self.enterContext(patch("bosshunter.web.server._show_startup_error_box", return_value=False))
        app_mock = self.enterContext(patch.object(server, "app"))
        if app_run is not None:
            app_mock.run.side_effect = app_run
        return app_mock, box

    def test_bind_failure_retries_after_stopping_our_own_stale_service(self):
        from bosshunter.web import server

        app_mock, box = self._patch_branch_b(
            app_run=[OSError(98, "Address already in use"), None]
        )
        with patch("bosshunter.web.server._stop_stale_our_service", return_value=True) as stop:
            server.run_server(host="127.0.0.1", port=8686, open_browser=False)

        stop.assert_called_once_with(self._data_dir, 8686)
        self.assertEqual(app_mock.run.call_count, 2)
        box.assert_not_called()
        self.assertFalse((self._data_dir / ".startup-failed.json").exists())

    def test_bind_failure_still_fails_after_retry_is_visible_not_silent(self):
        from bosshunter.web import server

        app_mock, box = self._patch_branch_b(app_run=OSError(98, "Address already in use"))
        with patch("bosshunter.web.server._stop_stale_our_service", return_value=True):
            with self.assertRaises(SystemExit):
                server.run_server(host="127.0.0.1", port=8686, open_browser=False)

        self.assertEqual(app_mock.run.call_count, 2)
        box.assert_called_once()
        marker = json.loads((self._data_dir / ".startup-failed.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["reason_code"], "port_bind_failed")
        self.assertIn("端口 8686", marker["message"])

    def test_bind_failure_foreign_occupant_is_not_killed_and_is_visible(self):
        from bosshunter.web import server

        app_mock, box = self._patch_branch_b(app_run=OSError(98, "Address already in use"))
        with patch("bosshunter.web.server._stop_stale_our_service", return_value=False) as stop:
            with self.assertRaises(SystemExit):
                server.run_server(host="127.0.0.1", port=8686, open_browser=False)

        stop.assert_called_once_with(self._data_dir, 8686)
        # 占端口的不是我们：不重试、直接可见报错。
        self.assertEqual(app_mock.run.call_count, 1)
        box.assert_called_once()

    def test_non_bind_oserror_is_visible_too(self):
        from bosshunter.web import server

        _, box = self._patch_branch_b(app_run=OSError(13, "Permission denied"))
        with patch("bosshunter.web.server._stop_stale_our_service") as stop:
            with self.assertRaises(SystemExit):
                server.run_server(host="127.0.0.1", port=8686, open_browser=False)

        stop.assert_not_called()
        box.assert_called_once()
        marker = json.loads((self._data_dir / ".startup-failed.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["reason_code"], "server_start_failed")

    def test_frontend_missing_is_visible_not_silent(self):
        from bosshunter.web import server

        _, box = self._patch_branch_b()
        with patch.object(server, "FRONTEND_DIR", Path(self._tmp.name) / "no-frontend"):
            with self.assertRaises(SystemExit):
                server.run_server(host="127.0.0.1", port=8686, open_browser=False)

        box.assert_called_once()
        self.assertIn("前端资源未构建", box.call_args.args[0])
        marker = json.loads((self._data_dir / ".startup-failed.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["reason_code"], "frontend_missing")

    def test_unexpected_startup_error_is_visible_not_silent(self):
        from bosshunter.web import server

        self.enterContext(patch.object(server, "DATA_DIR", self._data_dir))
        self.enterContext(patch("bosshunter.app_window.is_our_service_alive", return_value=False))
        self.enterContext(
            patch(
                "bosshunter.collection_session_store.list_sessions",
                side_effect=RuntimeError("配置加载炸了"),
            )
        )
        self.enterContext(patch("bosshunter.browser.runtime.set_data_dir"))
        self.enterContext(patch("bosshunter.browser.runtime.cleanup_stale_browser_resources", return_value=False))
        self.enterContext(patch("bosshunter.browser.runtime.write_instance_file"))
        self.enterContext(patch("bosshunter.web.server._install_shutdown_hooks"))
        self.enterContext(patch("bosshunter.web.server.start_login_patrol"))
        self.enterContext(patch("bosshunter.web.server.threading.Thread"))
        box = self.enterContext(patch("bosshunter.web.server._show_startup_error_box", return_value=False))
        self.enterContext(patch.object(server, "app"))

        with self.assertRaises(SystemExit):
            server.run_server(host="127.0.0.1", port=8686, open_browser=False)

        # 没预料到的异常也要补报一次可见错误，只弹一次框。
        box.assert_called_once()
        marker = json.loads((self._data_dir / ".startup-failed.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["reason_code"], "startup_error")

    def test_startup_log_records_each_stage(self):
        from bosshunter.web import server

        self._patch_branch_b()
        server.run_server(host="127.0.0.1", port=8686, open_browser=False)

        log = (self._data_dir / "logs" / "jianli-lieshou.log").read_text(encoding="utf-8")
        self.assertIn("启动开始", log)
        self.assertIn("残留清理", log)
        self.assertIn("实例文件已写", log)
        self.assertIn("开始在 127.0.0.1:8686 上监听", log)


class WatchCloseV29Tests(unittest.TestCase):
    def test_never_saw_tab_proc_exited_after_grace_service_never_healthy(self):
        # v29：标签页从未出现过、服务也从未应答过——绝不退出进程。
        proc = Mock()
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
            _clock=_fake_clock([100.0, 103.0, 106.0, 106.0]),
            _sleep=lambda _: None,
            _panel_url="http://127.0.0.1:8686",
            _service_alive=lambda: False,
        )
        self.assertFalse(result)
        self.assertEqual(fired, [])

    def test_tab_seen_then_gone_but_service_never_healthy_no_exit(self):
        # 标签页出现过又没了，但服务从未应答过 /api/health：启动中途
        # 死掉的残留信号，不是用户关窗，不退出。
        proc = Mock()
        proc.poll.return_value = None
        fired = []
        tab_lists = iter([
            ["http://127.0.0.1:8686/"],
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
            _clock=_fake_clock([100.0, 100.0, 100.0, 100.0, 100.0]),
            _sleep=lambda _: None,
            _panel_url="http://127.0.0.1:8686",
            _list_tabs=lambda: next(tab_lists),
            _service_alive=lambda: False,
        )
        self.assertFalse(result)
        self.assertEqual(fired, [])

    def test_tab_seen_then_gone_and_service_healthy_exits(self):
        proc = Mock()
        proc.poll.return_value = None
        fired = []
        tab_lists = iter([
            ["http://127.0.0.1:8686/"],
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
            _clock=_fake_clock([100.0, 100.0, 100.0, 100.0, 100.0]),
            _sleep=lambda _: None,
            _panel_url="http://127.0.0.1:8686",
            _list_tabs=lambda: next(tab_lists),
            _service_alive=lambda: True,
        )
        self.assertTrue(result)
        self.assertEqual(fired, [True])

    def test_proc_exit_after_grace_tab_seen_service_healthy_exits(self):
        proc = Mock()
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
            _clock=_fake_clock([100.0, 103.0, 106.0, 106.0]),
            _sleep=lambda _: None,
            _panel_url="http://127.0.0.1:8686",
            _list_tabs=lambda: ["http://127.0.0.1:8686/"],
            _service_alive=lambda: True,
        )
        self.assertTrue(result)
        self.assertEqual(fired, [True])

    def test_handoff_tab_never_seen_service_never_healthy_gives_up(self):
        # 交接假退出后 60 秒一直没看到面板标签页：放弃监视，不退出。
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
            _clock=_fake_clock([100.0, 101.0, 101.0, 200.0, 300.0]),
            _sleep=lambda _: None,
            _panel_url="http://127.0.0.1:8686",
            _list_tabs=lambda: ["https://www.zhipin.com/web/geek/job"],
            _tab_watch_seconds=60.0,
            _service_alive=lambda: False,
        )
        self.assertFalse(result)
        self.assertEqual(fired, [])


class SplashFailureTests(unittest.TestCase):
    def setUp(self):
        self._servers = []
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def tearDown(self):
        for server in self._servers:
            app_window.stop_splash_server(server)

    def test_splash_health_reports_failure_marker(self):
        import urllib.request

        marker = Path(self._tmp.name) / ".startup-failed.json"
        marker.write_text(
            json.dumps({"message": "端口 8686 被占着", "reason_code": "port_bind_failed"}, ensure_ascii=False),
            encoding="utf-8",
        )
        server = app_window.start_splash_server("http://127.0.0.1:8686", failure_marker_path=marker)
        self.assertIsNotNone(server)
        self._servers.append(server)
        with patch("bosshunter.app_window.is_our_service_alive", return_value=False):
            with urllib.request.urlopen(server.splash_url + "splash-health", timeout=5) as resp:
                data = json.load(resp)
        self.assertEqual(data, {"ready": False, "failed": True, "error": "端口 8686 被占着"})

    def test_splash_page_shows_error_after_repeated_failures(self):
        import urllib.request

        server = app_window.start_splash_server("http://127.0.0.1:8686")
        self.assertIsNotNone(server)
        self._servers.append(server)
        with urllib.request.urlopen(server.splash_url, timeout=5) as resp:
            page = resp.read().decode("utf-8")
        self.assertIn("本地服务没有响应", page)
        self.assertIn("d.failed", page)

    def test_read_startup_failure_returns_none_without_marker(self):
        self.assertIsNone(app_window.read_startup_failure(Path(self._tmp.name) / "nope.json"))
        self.assertIsNone(app_window.read_startup_failure(None))


class LauncherFailureTests(unittest.TestCase):
    def setUp(self):
        self._old_out = sys.stdout
        self._old_err = sys.stderr
        self._old_argv = sys.argv
        self._old_cwd = os.getcwd()
        self._old_frozen = getattr(sys, "frozen", None)
        self._had_frozen = hasattr(sys, "frozen")

    def tearDown(self):
        sys.stdout = self._old_out
        sys.stderr = self._old_err
        sys.argv = self._old_argv
        os.chdir(self._old_cwd)
        if self._had_frozen:
            sys.frozen = self._old_frozen
        elif hasattr(sys, "frozen"):
            del sys.frozen

    def _close_log_streams(self):
        for stream in (sys.stdout, sys.stderr):
            for s in getattr(stream, "_streams", []):
                name = getattr(s, "name", "")
                if isinstance(name, str) and name.endswith(".log"):
                    try:
                        s.close()
                    except Exception:
                        pass

    def _run_main(self, tmp, argv, cli_side_effect):
        import launcher

        exe_dir = Path(tmp)
        sys.frozen = True
        sys.argv = ["简历猎手.exe", *argv]
        self._last_box = Mock(return_value=False)
        with (
            patch.object(launcher, "_exe_dir", return_value=exe_dir),
            patch.object(launcher, "_bundled_node", return_value=None),
            patch.object(launcher, "_ensure_config", return_value=exe_dir / "config.yaml"),
            patch.object(launcher, "_show_fatal_error_box", self._last_box),
            patch("bosshunter.main.cli", side_effect=cli_side_effect),
        ):
            try:
                launcher.main()
            finally:
                self._close_log_streams()

    def test_cli_systemexit_message_is_reported_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe_dir = Path(tmp)
            with self.assertRaises(SystemExit) as caught:
                self._run_main(tmp, [], SystemExit("启动炸了"))
            self.assertEqual(str(caught.exception.code), "启动炸了")
            marker = json.loads((exe_dir / "data" / ".startup-failed.json").read_text(encoding="utf-8"))
            self.assertEqual(marker["message"], "启动炸了")
            self.assertEqual(marker["reason_code"], "startup_failed")
            self._last_box.assert_called_once_with("启动炸了")
            log = (exe_dir / "data" / "logs" / "jianli-lieshou.log").read_text(encoding="utf-8")
            self.assertIn("启动炸了", log)

    def test_cli_unexpected_exception_becomes_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe_dir = Path(tmp)
            with self.assertRaises(SystemExit) as caught:
                self._run_main(tmp, [], RuntimeError("boom"))
            self.assertEqual(caught.exception.code, 1)
            marker = json.loads((exe_dir / "data" / ".startup-failed.json").read_text(encoding="utf-8"))
            self.assertEqual(marker["reason_code"], "startup_error")
            self._last_box.assert_called_once()

    def test_cli_clean_exit_zero_is_not_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe_dir = Path(tmp)
            with self.assertRaises(SystemExit) as caught:
                self._run_main(tmp, ["--version"], SystemExit(0))
            self.assertEqual(caught.exception.code, 0)
            self.assertFalse((exe_dir / "data" / ".startup-failed.json").exists())
            self._last_box.assert_not_called()

    def test_cli_already_reported_systemexit_is_not_reported_twice(self):
        exc = SystemExit("已报过")
        exc._startup_reported = True
        with tempfile.TemporaryDirectory() as tmp:
            exe_dir = Path(tmp)
            with self.assertRaises(SystemExit):
                self._run_main(tmp, [], exc)
            self.assertFalse((exe_dir / "data" / ".startup-failed.json").exists())
            self._last_box.assert_not_called()


if __name__ == "__main__":
    unittest.main()
