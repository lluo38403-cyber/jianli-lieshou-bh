"""简历猎手 v30 启动提速：启动页不被残留清理杀掉、服务就绪后无窗重开、
复用核实收紧、重导入延迟加载。

对照测量报告 ~/workspace/v30-startup-measurement-2026-11-21.md：
H1 启动页 Chrome 被当次启动的残留清理杀掉、H2 复用路径 10 秒核实 +
5 秒优雅停、H3 冷导入 pypdf/httpx。
"""

import inspect
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import bosshunter.app_window as app_window
import bosshunter.browser.runtime as runtime

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "resume_samples"


def _spawn_marker_proc(profile_dir: Path, port: int = 19222):
    """Spawn a process whose cmdline carries our browser markers.

    复现测量报告的伪造场景：命令行含专用 profile 与调试端口，
    与启动页 Chrome 同款标记。start_new_session 让 killpg 能按
    进程组杀它（和真 Chrome 一样）。
    """
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
            f"--user-data-dir={profile_dir}",
            f"--remote-debugging-port={port}",
        ],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class ExcludePidTests(unittest.TestCase):
    """H1：本次启动刚拉起的浏览器，残留清理必须放过它。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._data_dir = Path(self._tmp.name) / "data"
        self._data_dir.mkdir(parents=True)
        self._profile_dir = Path(self._tmp.name) / "profile"
        self._profile_dir.mkdir(parents=True)
        self._old_override = runtime._DATA_DIR_OVERRIDE
        self._old_launched = runtime._launched_browser
        runtime.set_data_dir(self._data_dir)
        self._procs = []

    def tearDown(self):
        for proc in self._procs:
            try:
                if proc.poll() is None:
                    proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                pass
        runtime._launched_browser = self._old_launched
        runtime.set_data_dir(self._old_override)

    def _record(self, proc):
        runtime._record_launched_browser(proc, self._profile_dir, 19222, sys.executable)
        return self._data_dir / "launched-browser.json"

    def test_cleanup_with_exclude_leaves_this_run_browser_alive(self):
        # 测量员的复现场景：启动页 Chrome 刚被记下来，清理就跑——
        # 带 exclude_pid 时它必须活着，记录与 state 文件都留着，
        # 且清理返回 False（没清掉任何东西）。
        proc = _spawn_marker_proc(self._profile_dir)
        self._procs.append(proc)
        state_path = self._record(proc)
        self.assertTrue(state_path.is_file())

        cleaned = runtime.cleanup_stale_browser_resources(
            data_dir=self._data_dir, port=18686, exclude_pid=proc.pid
        )

        self.assertFalse(cleaned)
        self.assertIsNone(proc.poll(), "本次启动的浏览器被残留清理杀掉了（H1 复现）")
        self.assertTrue(state_path.is_file(), "被排除的浏览器 state 文件不应被清掉")
        self.assertIsNotNone(runtime._launched_browser)
        self.assertEqual(runtime._launched_browser.get("pid"), proc.pid)

    def test_stop_without_exclude_kills_marked_chrome(self):
        # 对照：不带 exclude 时，同样标记的进程就是要被清掉的残留。
        # POSIX 上僵尸进程会让 _kill_process_tree 的存活轮询多等一会儿，
        # 所以这里不断言它的返回值，只断言进程确实死了、state 文件清了。
        proc = _spawn_marker_proc(self._profile_dir)
        self._procs.append(proc)
        state_path = self._record(proc)

        runtime.stop_launched_browser(timeout=0.5)

        self.assertIsNotNone(proc.wait(timeout=5), "没带 exclude 也没杀掉标记进程")
        self.assertFalse(state_path.exists())

    def test_exclude_is_selective_other_marked_chrome_still_cleaned(self):
        # exclude 只护住给定的那一个 PID：另一个带同样标记的残留
        # Chrome（state 文件里记的）照样要被清掉。
        proc_a = _spawn_marker_proc(self._profile_dir)
        proc_b = _spawn_marker_proc(self._profile_dir)
        self._procs.extend([proc_a, proc_b])
        self._record(proc_a)
        # state 文件改记 B：内存记录是 A、落盘记录是 B。
        state_path = self._data_dir / "launched-browser.json"
        state_path.write_text(
            json.dumps(
                {
                    "pid": proc_b.pid,
                    "executable": sys.executable,
                    "profile_dir": str(self._profile_dir),
                    "port": 19222,
                }
            ),
            encoding="utf-8",
        )

        runtime.stop_launched_browser(timeout=0.5, exclude_pid=proc_a.pid)

        self.assertIsNone(proc_a.poll(), "被排除的浏览器不应被杀")
        self.assertIsNotNone(proc_b.wait(timeout=5), "没被排除的残留 Chrome 应该被清掉")


class StartupOrderTests(unittest.TestCase):
    """H1：残留清理在启动页之前；复用分支先拉启动页、再停旧服务。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._data_dir = Path(self._tmp.name) / "data"
        self.addCleanup(lambda: app_window.set_splash_failure_marker_path(None))
        self.addCleanup(lambda: app_window.set_splash_stage("正在等本地服务起来…"))

    def _patch_run_server(self, alive, verify):
        from bosshunter.web import server

        self.enterContext(patch.object(server, "DATA_DIR", self._data_dir))
        self.enterContext(patch("bosshunter.app_window.is_our_service_alive", return_value=alive))
        self.enterContext(patch("bosshunter.collection_session_store.list_sessions", return_value=[]))
        self.enterContext(patch("bosshunter.browser.runtime.set_data_dir"))
        self.enterContext(patch("bosshunter.browser.runtime.write_instance_file"))
        self.enterContext(patch("bosshunter.web.server._install_shutdown_hooks"))
        self.enterContext(patch("bosshunter.web.server.start_login_patrol"))
        self.enterContext(patch("bosshunter.web.server.threading.Thread"))
        self.enterContext(patch("bosshunter.web.server._show_startup_error_box", return_value=False))
        app_mock = self.enterContext(patch.object(server, "app"))
        return app_mock

    def test_fresh_start_cleans_before_splash(self):
        # 全新启动：清理 → 启动页 → 监听，清理绝不能在启动页之后。
        from bosshunter.web import server

        app_mock = self._patch_run_server(alive=False, verify=None)
        calls: list[str] = []
        cleanup = self.enterContext(
            patch(
                "bosshunter.browser.runtime.cleanup_stale_browser_resources",
                side_effect=lambda *a, **k: (calls.append("cleanup"), False)[1],
            )
        )
        splash = self.enterContext(
            patch(
                "bosshunter.app_window.launch_splash_window",
                side_effect=lambda *a, **k: (calls.append("splash"), (Mock(), Mock(splash_url="http://127.0.0.1:1/")))[1],
            )
        )
        self.enterContext(patch("bosshunter.app_window.get_app_window_process", return_value=None))
        app_mock.run.side_effect = lambda *a, **k: calls.append("run")

        server.run_server(host="127.0.0.1", port=8686, open_browser=True)

        self.assertEqual(calls, ["cleanup", "splash", "run"])
        cleanup.assert_called_once_with(data_dir=self._data_dir, port=8686, exclude_pid=None)
        splash.assert_called_once_with("http://127.0.0.1:8686")

    def test_reuse_failure_shows_splash_before_stopping_stale_service(self):
        # 复用失败：开窗复用 → 拉启动页给用户看 → 停旧服务 → 清理
        # （带 exclude_pid 护着刚拉的启动页）→ 监听，启动页只拉一次。
        from bosshunter.web import server

        app_mock = self._patch_run_server(alive=True, verify=False)
        calls: list[str] = []
        launch = self.enterContext(
            patch(
                "bosshunter.app_window.launch_app_window",
                side_effect=lambda *a, **k: calls.append("app_window"),
            )
        )
        splash_proc = Mock()
        splash_proc.pid = 12345
        splash_proc.poll.return_value = None
        splash = self.enterContext(
            patch(
                "bosshunter.app_window.launch_splash_window",
                side_effect=lambda *a, **k: (
                    calls.append("splash"),
                    (splash_proc, Mock(splash_url="http://127.0.0.1:1/")),
                )[1],
            )
        )
        stop = self.enterContext(
            patch(
                "bosshunter.web.server._stop_stale_our_service",
                side_effect=lambda *a, **k: (calls.append("stop"), True)[1],
            )
        )
        cleanup = self.enterContext(
            patch(
                "bosshunter.browser.runtime.cleanup_stale_browser_resources",
                side_effect=lambda *a, **k: (calls.append("cleanup"), False)[1],
            )
        )
        self.enterContext(
            patch("bosshunter.app_window.wait_for_panel_tab", return_value=False)
        )
        self.enterContext(
            patch("bosshunter.app_window.get_app_window_process", return_value=splash_proc)
        )
        app_mock.run.side_effect = lambda *a, **k: calls.append("run")

        server.run_server(host="127.0.0.1", port=8686, open_browser=True)

        self.assertEqual(calls, ["app_window", "splash", "stop", "cleanup", "run"])
        launch.assert_called_once_with("http://127.0.0.1:8686")
        splash.assert_called_once_with("http://127.0.0.1:8686")
        stop.assert_called_once_with(self._data_dir, 8686)
        cleanup.assert_called_once_with(data_dir=self._data_dir, port=8686, exclude_pid=12345)
        app_mock.run.assert_called_once()


class FallbackReopenTests(unittest.TestCase):
    """兜底：服务就绪后无面板标签页，再开一次窗。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._data_dir = Path(self._tmp.name) / "data"
        self._panel = "http://127.0.0.1:8686"
        self._splash = "http://127.0.0.1:46595/"

    def _run(self, ready, tab, urls, debug_port=19222):
        from bosshunter.web import server

        with (
            patch.object(server, "DATA_DIR", self._data_dir),
            patch("bosshunter.app_window.wait_for_service_ready", return_value=ready),
            patch("bosshunter.app_window.wait_for_panel_tab", return_value=tab),
            patch("bosshunter.app_window.get_app_window_debug_port", return_value=debug_port),
            patch("bosshunter.app_window.get_app_window_splash_url", return_value=self._splash),
            patch("bosshunter.app_window._list_debug_page_urls", return_value=urls),
            patch("bosshunter.app_window.launch_app_window") as launch,
        ):
            result = server._ensure_panel_window_after_ready(
                "127.0.0.1", 8686, self._panel, redirect_grace=0, tab_timeout=0.01
            )
        return result, launch

    def test_reopens_when_ready_but_no_panel_tab_and_splash_tab_alive(self):
        result, launch = self._run(
            ready=True, tab=False, urls=[self._splash, "https://www.zhipin.com/web/geek/job"]
        )
        self.assertTrue(result)
        launch.assert_called_once_with(self._panel)

    def test_no_reopen_when_panel_tab_seen(self):
        result, launch = self._run(ready=True, tab=True, urls=[self._panel + "/"])
        self.assertFalse(result)
        launch.assert_not_called()

    def test_no_reopen_when_user_closed_window(self):
        # 面板标签页没有、启动页标签也没了 = 用户自己关了窗，绝不擅自再开。
        result, launch = self._run(ready=True, tab=False, urls=[])
        self.assertFalse(result)
        launch.assert_not_called()

    def test_no_reopen_when_service_never_ready(self):
        result, launch = self._run(ready=False, tab=False, urls=[self._splash])
        self.assertFalse(result)
        launch.assert_not_called()

    def test_no_reopen_when_debug_port_unverifiable(self):
        result, launch = self._run(
            ready=True, tab=None, urls=[self._splash], debug_port=None
        )
        self.assertFalse(result)
        launch.assert_not_called()


class ReuseVerifyTighteningTests(unittest.TestCase):
    """H2：复用核实 10 秒→3 秒、interval 0.5→0.2 秒；停旧服务先确认无窗口。"""

    def test_default_timeout_is_3_seconds(self):
        from bosshunter.web import server

        self.assertEqual(
            inspect.signature(server._verify_reused_service_window).parameters["timeout"].default,
            3.0,
        )

    def test_verify_passes_short_timeout_and_interval(self):
        from bosshunter.web import server

        with patch("bosshunter.app_window.wait_for_panel_tab", return_value=True) as wait:
            self.assertTrue(server._verify_reused_service_window("http://127.0.0.1:8686"))
        _, kwargs = wait.call_args
        self.assertEqual(kwargs["timeout"], 3.0)
        self.assertEqual(kwargs["interval"], 0.2)

    def test_verify_false_means_no_window(self):
        from bosshunter.web import server

        with patch("bosshunter.app_window.wait_for_panel_tab", return_value=False):
            self.assertFalse(server._verify_reused_service_window("http://127.0.0.1:8686"))

    def test_verify_none_means_unverifiable_pass(self):
        from bosshunter.web import server

        with patch("bosshunter.app_window.wait_for_panel_tab", return_value=None):
            self.assertTrue(server._verify_reused_service_window("http://127.0.0.1:8686"))

    def _windowless(self, alive, debug_port, urls):
        from bosshunter.web import server

        with (
            patch("bosshunter.app_window.is_our_service_alive", return_value=alive),
            patch("bosshunter.app_window.get_app_window_debug_port", return_value=debug_port),
            patch("bosshunter.app_window._list_debug_page_urls", return_value=urls),
            patch("bosshunter.app_window.get_app_window_url", return_value="http://127.0.0.1:8686"),
        ):
            return server._reused_service_is_windowless(8686)

    def test_windowless_when_service_answers_and_no_panel_tab(self):
        self.assertTrue(
            self._windowless(True, 19222, ["https://www.zhipin.com/web/geek/job"])
        )

    def test_not_windowless_when_panel_tab_present(self):
        self.assertFalse(
            self._windowless(True, 19222, ["http://127.0.0.1:8686/"])
        )

    def test_not_windowless_when_debug_port_silent(self):
        self.assertFalse(self._windowless(True, 19222, None))

    def test_not_windowless_without_debug_port(self):
        self.assertFalse(self._windowless(True, None, []))

    def test_not_windowless_when_service_not_answering(self):
        self.assertFalse(self._windowless(False, 19222, []))


class StopStaleFastPathTests(unittest.TestCase):
    """停旧服务：已确认无窗口就跳过 5 秒优雅停、直接强制结束。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._data_dir = Path(self._tmp.name) / "data"
        self._data_dir.mkdir(parents=True)
        self._proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)", "bosshunter"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self._kill_proc)
        (self._data_dir / ".instance.json").write_text(
            json.dumps({"pid": self._proc.pid, "executable": sys.executable, "port": 8686}),
            encoding="utf-8",
        )

    def _kill_proc(self):
        try:
            if self._proc.poll() is None:
                os.killpg(self._proc.pid, signal.SIGTERM)
            self._proc.wait(timeout=5)
        except Exception:
            pass

    def test_windowless_skips_graceful_wait_and_force_kills(self):
        from bosshunter.web import server

        killed: list[int] = []

        def _fake_kill(pid, timeout=10.0):
            killed.append(pid)
            os.killpg(pid, signal.SIGTERM)
            return True

        with (
            patch.object(server, "DATA_DIR", self._data_dir),
            patch("bosshunter.web.server._reused_service_is_windowless", return_value=True),
            patch(
                "bosshunter.web.server._terminate_process_gracefully",
                side_effect=AssertionError("无窗口已确认，不应再优雅等 5 秒"),
            ),
            patch("bosshunter.browser.runtime._kill_process_tree", side_effect=_fake_kill),
            # POSIX 上子进程死后要等父进程 wait 才消失（僵尸进程），
            # _pid_alive 会一直报活着；这里模拟已回收，让测试只验
            # “跳过优雅停、直接强制结束”这个决策本身。
            patch("bosshunter.browser.runtime._pid_alive", return_value=False),
        ):
            self.assertTrue(server._stop_stale_our_service(self._data_dir, 8686))
        self.assertEqual(killed, [self._proc.pid])
        self.assertFalse((self._data_dir / ".instance.json").exists())

    def test_window_present_keeps_graceful_wait(self):
        from bosshunter.web import server

        with (
            patch.object(server, "DATA_DIR", self._data_dir),
            patch("bosshunter.web.server._reused_service_is_windowless", return_value=False),
            patch(
                "bosshunter.web.server._terminate_process_gracefully", return_value=True
            ) as graceful,
            patch("bosshunter.browser.runtime._kill_process_tree") as kill,
            patch("bosshunter.browser.runtime._pid_alive", return_value=False),
        ):
            self.assertTrue(server._stop_stale_our_service(self._data_dir, 8686))
        graceful.assert_called_once_with(self._proc.pid, timeout=5.0)
        kill.assert_not_called()


class LazyImportTests(unittest.TestCase):
    """H3：pypdf/httpx 延迟导入——启动不加载，用到时功能照常。"""

    def test_server_import_skips_httpx_and_pypdf(self):
        # 全新解释器里只 import server：两个重依赖都不应被拉进来。
        env = dict(os.environ)
        env["PYTHONPATH"] = f"{ROOT}:{ROOT / 'src'}"
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import bosshunter.web.server; "
                "print('httpx' in sys.modules, 'pypdf' in sys.modules)",
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "False False")

    def test_lazy_httpx_attribute_still_patchable(self):
        # 代理上的 patch 必须照常工作：mock 盖住 .get，函数就走 mock；
        # patch 退出后代理回到懒加载（不残留 mock）。
        fake_resp = Mock(status_code=200)
        fake_resp.json.return_value = {"status": "ok"}
        with patch("bosshunter.browser.runtime.httpx.get", return_value=fake_resp) as fake_get:
            result = runtime._fetch_runtime_health("http://127.0.0.1:9/health", 1)
        self.assertEqual(result, {"status": "ok"})
        fake_get.assert_called_once()
        self.assertIs(runtime.httpx._load(), sys.modules["httpx"])

    def test_lazy_httpx_proxy_resolves_real_module(self):
        from bosshunter.ai import credentials

        self.assertEqual(credentials.httpx.__name__, "httpx")
        self.assertIn("httpx", sys.modules)

    def test_pdf_reader_still_works_after_lazy_import(self):
        from bosshunter.web.resume_upload import pdf_to_markdown

        text = pdf_to_markdown((FIXTURES / "reportlab.pdf").read_bytes())
        self.assertIn("Zhang San", text)

    def test_splash_health_reports_stage(self):
        # 启动页文案按阶段更新：服务端设的阶段要经 /splash-health 回到页面。
        import urllib.request

        self._servers = []
        self.addCleanup(lambda: [app_window.stop_splash_server(s) for s in self._servers])
        app_window.set_splash_stage("正在清理残留文件…")
        server = app_window.start_splash_server("http://127.0.0.1:8686")
        self.assertIsNotNone(server)
        self._servers.append(server)
        with patch("bosshunter.app_window.is_our_service_alive", return_value=False):
            with urllib.request.urlopen(server.splash_url + "splash-health", timeout=5) as resp:
                data = json.load(resp)
        self.assertEqual(data, {"ready": False, "stage": "正在清理残留文件…"})
        with urllib.request.urlopen(server.splash_url, timeout=5) as resp:
            page = resp.read().decode("utf-8")
        self.assertIn("启动有点慢，还在努力", page)
        self.assertIn("d.stage", page)


class StartupLogMillisTests(unittest.TestCase):
    def test_startup_log_has_millisecond_elapsed(self):
        from bosshunter.web import server

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            with patch.object(server, "DATA_DIR", data_dir):
                server._startup_log("测试探针")
            log = (data_dir / "logs" / "jianli-lieshou.log").read_text(encoding="utf-8")
        self.assertRegex(log, r"\+\s*\d+\.\d{3}s\] 启动：测试探针")


if __name__ == "__main__":
    unittest.main()
