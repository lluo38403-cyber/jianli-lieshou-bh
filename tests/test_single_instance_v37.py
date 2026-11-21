"""简历猎手 v37 任务③：单实例互斥锁 + 启动提速。

背景（lucky 2026-11-22 03:30）：“启动 exe 很慢、启动后同时跳出四五个窗口”。
根因核实：代码里没有任何 Mutex——现行防多开只靠 data/.instance.json +
端口探测 + 无窗口复用（v29 那套）。快速双击两次时，第二个进程在第一个
还没把服务/窗口建好之前就开始自己的启动流程：要么以为端口没人占、自己
再起一份服务，要么走复用分支又开一个窗口——两个进程、启动页 + 面板 +
工作浏览器加起来四五个窗口。

修法：入口最顶端先抢 Windows Named Mutex（Global\\ 前缀，多会话唯一）；
已有实例时新进程立即退出，并把已有主窗口前置。非 Windows 保持现有文件
锁行为不变。Named Mutex 随进程死亡自动释放，强杀也挡不住下次启动。

本机是 Linux、没有 kernel32：Windows 语义用一个文件级 FakeKernel32
建模（CreateMutexW 第二次报 ERROR_ALREADY_EXISTS；持有者进程死了，
下一把就能抢到——这正是 OS 自动释放的语义），再用真子进程 + SIGKILL
走一遍“持有者被强杀 → 下一个进程仍能抢到锁”的完整链路。
"""

import http.server
import inspect
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import launcher
from bosshunter import app_window, single_instance

ROOT = Path(__file__).resolve().parent.parent


class _FakeKernel32:
    """Models Windows Named Mutex semantics with a file-backed registry.

    - 第一次 CreateMutexW：占住名字，GetLastError() == 0；
    - 持有者还活着时第二次 CreateMutexW：照样给句柄，但
      GetLastError() == 183（ERROR_ALREADY_EXISTS）；
    - 持有者进程死了（pid 不存在）：名字空出来，第二次也能抢到——
      这就是 Named Mutex 随进程死亡自动释放的语义（没有锁文件可残留）。
    每个实例只认自己发出去的句柄；CloseHandle 只在持有者是自己时
    才清掉注册表里的名字。
    """

    def __init__(self, state_path):
        self._state_path = Path(state_path)
        self._last_error = 0
        self._handles = {}
        self._next = 1

    def _read(self):
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write(self, data):
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(data), encoding="utf-8")

    @staticmethod
    def _pid_alive(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def CreateMutexW(self, _security, _initial_owner, name):
        registry = self._read()
        owner = registry.get(name)
        handle = self._next
        self._next += 1
        self._handles[handle] = name
        if owner is not None and self._pid_alive(owner):
            self._last_error = 183
        else:
            registry[name] = os.getpid()
            self._write(registry)
            self._last_error = 0
        return handle

    def GetLastError(self):
        return self._last_error

    def CloseHandle(self, handle):
        name = self._handles.pop(handle, None)
        if name is None:
            return False
        registry = self._read()
        if registry.get(name) == os.getpid():
            del registry[name]
            self._write(registry)
        return True


class _FakeUser32:
    """Models FindWindowW/ShowWindow/SetForegroundWindow for one window."""

    def __init__(self, windows=None):
        self.windows = dict(windows or {})
        self.calls = []

    def FindWindowW(self, _cls, title):
        return self.windows.get(title)

    def ShowWindow(self, hwnd, cmd):
        self.calls.append(("ShowWindow", hwnd, cmd))
        return True

    def SetForegroundWindow(self, hwnd):
        self.calls.append(("SetForegroundWindow", hwnd))
        return True


class _FakeClock:
    """Monotonic clock driven by the sleep under test (no real waiting)."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class MutexAcquireTests(unittest.TestCase):
    """双实例互斥：第二把抢不到、返回码/行为可断言；释放后又能抢。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._state = Path(self._tmp.name) / "mutex.json"
        self._fake = _FakeKernel32(self._state)
        self._old_lock = single_instance._ACTIVE_LOCK
        single_instance._ACTIVE_LOCK = None
        self.addCleanup(lambda: setattr(single_instance, "_ACTIVE_LOCK", self._old_lock))

    def test_first_acquire_wins_second_reports_already_exists(self):
        lock = single_instance.acquire_single_instance_mutex(
            _kernel32=self._fake, _is_windows=True
        )
        self.assertIsNotNone(lock)
        # 模拟第二个进程：本进程已持有的全局锁先清掉，再抢一次。
        single_instance._ACTIVE_LOCK = None
        second = single_instance.acquire_single_instance_mutex(
            _kernel32=self._fake, _is_windows=True
        )
        self.assertIsNone(second, "已有实例在跑，第二把锁必须抢不到")

    def test_same_process_reacquire_returns_the_same_lock(self):
        # launcher 抢过、cli 的 web 分支再抢：不能把自己误判成已有实例。
        first = single_instance.acquire_single_instance_mutex(
            _kernel32=self._fake, _is_windows=True
        )
        again = single_instance.acquire_single_instance_mutex(
            _kernel32=self._fake, _is_windows=True
        )
        self.assertIs(again, first)

    def test_release_then_reacquire_succeeds(self):
        lock = single_instance.acquire_single_instance_mutex(
            _kernel32=self._fake, _is_windows=True
        )
        single_instance.release_single_instance_mutex(lock, _kernel32=self._fake)
        single_instance._ACTIVE_LOCK = None
        again = single_instance.acquire_single_instance_mutex(
            _kernel32=self._fake, _is_windows=True
        )
        self.assertIsNotNone(again)
        single_instance.release_single_instance_mutex(again, _kernel32=self._fake)

    def test_mutex_name_is_fixed_and_global(self):
        self.assertEqual(
            single_instance.MUTEX_NAME, r"Global\JianLiLieShouSingleInstance"
        )

    def test_non_windows_proceeds_without_os_mutex(self):
        # 非 Windows：保持现有文件锁行为不变，这里直接放行（哨兵锁）。
        lock = single_instance.acquire_single_instance_mutex(_is_windows=False)
        self.assertIsNotNone(lock)
        self.assertIs(lock, single_instance._NO_OS_MUTEX)


class BringWindowToFrontTests(unittest.TestCase):
    """已有实例的窗口要被前置/激活；窗口还没建好时等一小会儿，不静默失败。"""

    def test_panel_window_is_restored_and_foregrounded(self):
        user32 = _FakeUser32({"简历猎手": 4321})
        self.assertTrue(single_instance.bring_existing_window_to_front(_user32=user32))
        self.assertIn(("ShowWindow", 4321, 9), user32.calls)
        self.assertIn(("SetForegroundWindow", 4321), user32.calls)

    def test_splash_window_counts_as_main_window(self):
        # 第一实例还在启动页阶段（面板标题的窗口还没建好）也要能唤醒。
        user32 = _FakeUser32({"简历猎手正在启动": 777})
        self.assertTrue(single_instance.bring_existing_window_to_front(_user32=user32))
        self.assertIn(("SetForegroundWindow", 777), user32.calls)

    def test_waits_a_while_when_window_not_built_yet(self):
        clock = _FakeClock()
        user32 = _FakeUser32({})

        def _appear():
            # 时钟走到 0.5 秒时窗口才建好。
            if clock.now >= 0.5:
                user32.windows["简历猎手"] = 4321
            return user32.windows.get("简历猎手")

        user32.FindWindowW = lambda _cls, title: _appear()
        ok = single_instance.bring_existing_window_to_front(
            timeout=5.0, interval=0.2, _user32=user32, _sleep=clock.sleep, _clock=clock
        )
        self.assertTrue(ok)
        self.assertGreaterEqual(clock.now, 0.5)

    def test_gives_up_after_timeout_when_no_window(self):
        clock = _FakeClock()
        user32 = _FakeUser32({})
        ok = single_instance.bring_existing_window_to_front(
            timeout=1.0, interval=0.2, _user32=user32, _sleep=clock.sleep, _clock=clock
        )
        self.assertFalse(ok)
        self.assertGreaterEqual(clock.now, 1.0)

    def test_non_windows_returns_false(self):
        # 本机 Linux：没有 user32，绝不假装唤醒成功。
        self.assertFalse(single_instance.bring_existing_window_to_front(timeout=0))


class MutexDeathReleaseTests(unittest.TestCase):
    """Named Mutex 随进程死亡自动释放：持有者被 SIGKILL 后下一把能抢到。"""

    def test_killed_holder_does_not_block_next_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "mutex.json"
            child_code = (
                "import sys, time; "
                "sys.path.insert(0, 'src'); sys.path.insert(0, 'tests'); "
                "from test_single_instance_v37 import _FakeKernel32; "
                "from bosshunter import single_instance; "
                f"fake = _FakeKernel32({str(state)!r}); "
                "lock = single_instance.acquire_single_instance_mutex(_kernel32=fake, _is_windows=True); "
                "print('ACQUIRED' if lock is not None else 'DENIED', flush=True); "
                "time.sleep(30)"
            )
            env = dict(os.environ)
            proc = subprocess.Popen(
                [sys.executable, "-c", child_code],
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            try:
                line = proc.stdout.readline().strip()
                self.assertEqual(line, "ACQUIRED")
                # 子进程活着时，第二把抢不到。
                single_instance._ACTIVE_LOCK = None
                blocked = single_instance.acquire_single_instance_mutex(
                    _kernel32=_FakeKernel32(state), _is_windows=True
                )
                self.assertIsNone(blocked, "持有者活着时第二把锁必须抢不到")
                # 强杀持有者（不给它任何释放机会），再抢必须成功。
                proc.kill()
                proc.wait(timeout=10)
                single_instance._ACTIVE_LOCK = None
                lock = single_instance.acquire_single_instance_mutex(
                    _kernel32=_FakeKernel32(state), _is_windows=True
                )
                self.assertIsNotNone(lock, "持有者死了，锁必须自动释放、下把能抢到")
                single_instance.release_single_instance_mutex(
                    lock, _kernel32=_FakeKernel32(state)
                )
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=10)
                single_instance._ACTIVE_LOCK = None


class LauncherGuardTests(unittest.TestCase):
    """launcher.main() 最顶端守门：已有实例时立即退出，不碰目录/配置/服务。"""

    def setUp(self):
        self._old_argv = sys.argv
        self._old_cwd = os.getcwd()
        self.addCleanup(lambda: setattr(sys, "argv", self._old_argv))
        self.addCleanup(lambda: os.chdir(self._old_cwd))

    def test_second_instance_exits_before_any_init(self):
        sys.argv = ["简历猎手.exe", "web"]
        with (
            patch(
                "bosshunter.single_instance.ensure_single_instance",
                return_value=None,
            ),
            patch.object(
                launcher,
                "_exe_dir",
                side_effect=AssertionError("已有实例时绝不能走到初始化"),
            ),
        ):
            launcher.main()  # 返回即退出，不抛、不做任何事。
        self.assertEqual(os.getcwd(), self._old_cwd)

    def test_first_instance_proceeds_to_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe_dir = Path(tmp)
            sys.argv = ["简历猎手.exe"]
            with (
                patch(
                    "bosshunter.single_instance.ensure_single_instance",
                    return_value=object(),
                ),
                patch.object(launcher, "_exe_dir", return_value=exe_dir),
                patch.object(launcher, "_bundled_node", return_value=None),
                patch.object(
                    launcher, "_ensure_config", return_value=exe_dir / "config.yaml"
                ),
                patch("bosshunter.main.cli") as cli,
            ):
                launcher.main()
            cli.assert_called_once()

    def test_version_command_is_not_gated(self):
        # --version 是一次性命令：即使已有实例在跑也要照常输出。
        with tempfile.TemporaryDirectory() as tmp:
            exe_dir = Path(tmp)
            sys.argv = ["简历猎手.exe", "--version"]
            with (
                patch(
                    "bosshunter.single_instance.ensure_single_instance",
                    side_effect=AssertionError("--version 不应抢锁"),
                ),
                patch.object(launcher, "_exe_dir", return_value=exe_dir),
                patch.object(launcher, "_bundled_node", return_value=None),
                patch.object(
                    launcher, "_ensure_config", return_value=exe_dir / "config.yaml"
                ),
                patch("bosshunter.main.cli") as cli,
            ):
                launcher.main()
            cli.assert_called_once()


class CliWebGuardTests(unittest.TestCase):
    """直接跑 bosshunter web 时 cli() 也在加载配置前守门。"""

    def test_second_instance_exits_before_config_load(self):
        from click.testing import CliRunner

        from bosshunter.main import cli

        with (
            patch(
                "bosshunter.single_instance.ensure_single_instance",
                return_value=None,
            ),
            patch(
                "bosshunter.config.load_config",
                side_effect=AssertionError("已有实例时绝不能走到加载配置"),
            ),
        ):
            result = CliRunner().invoke(cli, ["web"])
        self.assertEqual(result.exit_code, 0)


class HealthPollIntervalTests(unittest.TestCase):
    """启动提速：健康轮询 100–200ms 短间隔，不死等长超时。"""

    def test_wait_for_service_ready_default_interval_is_0_2(self):
        self.assertEqual(
            inspect.signature(app_window.wait_for_service_ready)
            .parameters["interval"]
            .default,
            0.2,
        )

    def test_wait_for_panel_tab_default_interval_is_0_2(self):
        self.assertEqual(
            inspect.signature(app_window.wait_for_panel_tab)
            .parameters["interval"]
            .default,
            0.2,
        )

    def test_is_our_service_alive_default_timeout_is_1_0(self):
        self.assertEqual(
            inspect.signature(app_window.is_our_service_alive)
            .parameters["timeout"]
            .default,
            1.0,
        )

    def test_service_ready_detected_within_one_short_interval(self):
        # 服务在 300ms 后才就绪：0.5 秒间隔要约 500ms 才检出，
        # 0.2 秒间隔约 400ms。检出耗时必须小于 470ms，否则就是旧间隔。
        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"status": "ok", "version": "1.1.22"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]

        def _late_start():
            time.sleep(0.30)
            threading.Thread(target=server.serve_forever, daemon=True).start()

        threading.Thread(target=_late_start, daemon=True).start()
        try:
            started = time.monotonic()
            ok = app_window.wait_for_service_ready("127.0.0.1", port, timeout=5.0)
            elapsed_ms = (time.monotonic() - started) * 1000
        finally:
            server.shutdown()
            server.server_close()
        self.assertTrue(ok)
        self.assertLess(elapsed_ms, 470, f"检出用了 {elapsed_ms:.0f}ms，仍是旧的长间隔")

    def test_splash_polls_every_200ms_and_reports_real_elapsed_seconds(self):
        page = app_window._splash_page_html("http://127.0.0.1:8686").decode("utf-8")
        self.assertIn("setTimeout(tick,200)", page)
        self.assertNotIn("setTimeout(tick,500)", page)
        # “已等秒数”按真实经过时间算，不再按轮次数推算（换间隔后轮次≠秒数）。
        self.assertIn("Date.now()-started", page)
        self.assertNotIn("tried>20", page)
        # 阶段文案照常：启动页要如实反映进度，不要假装。
        self.assertIn("启动有点慢，还在努力", page)
        self.assertIn("d.stage", page)

    def test_splash_health_still_reports_stage(self):
        # 启动页 /splash-health 仍把服务端阶段带回页面（v30 行为不变），
        # 只是单次探针超时从 1.0 秒缩到 0.5 秒（见 handler 源码）。
        import urllib.request

        self._servers = []
        self.addCleanup(lambda: [app_window.stop_splash_server(s) for s in self._servers])
        app_window.set_splash_stage("正在启动本地服务…")
        self.addCleanup(lambda: app_window.set_splash_stage("正在等本地服务起来…"))
        server = app_window.start_splash_server("http://127.0.0.1:8686")
        self.assertIsNotNone(server)
        self._servers.append(server)
        with patch("bosshunter.app_window.is_our_service_alive", return_value=False):
            with urllib.request.urlopen(server.splash_url + "splash-health", timeout=5) as resp:
                data = json.load(resp)
        self.assertEqual(data, {"ready": False, "stage": "正在启动本地服务…"})
        source = inspect.getsource(app_window._SplashRequestHandler.do_GET)
        self.assertIn("timeout=0.5", source)


if __name__ == "__main__":
    unittest.main()
