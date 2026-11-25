"""Retry reason-code fix (v32, run 44873002310).

Regression: the v31 update-flow Retry run failed at stage=result with
reason_code "unknown" instead of "stuck"/"copy". Two product defects
collided, plus a test-timing gap:

1. The relaunched old exe consumed the pending marker while update.bat
   was still in its 180 s :fail_poll and wrote a final failed/unknown
   verdict before the bat wrote its own specific code — two writers
   on update-result.json with no ownership rule.
2. The bat polled the PRE-update service port (18686 in CI) for the
   relaunched instance, which always listens on the relaunch port
   8686 (the bat starts the exe with no arguments; the launcher
   defaults to `web` on 8686). :fail_poll could never see the old
   version, waited the full 180 s and wrote nostart_old instead of
   stuck — so the copy/stuck expectation could never be met at all.
3. stage=result read the file as soon as the old version answered on
   8686, without waiting for the bat to finish (the bat self-deletes
   update.bat only after writing its verdict).

These tests cover the backend side of the fix (the bat-side port fix
is covered by test_apply_passes_relaunch_port_to_update_bat, and the
ps1 wait-for-bat-exit is covered by the CI run itself): while the bat
is alive the backend writes nothing, consumes nothing and rescues
nothing; once the bat wrote stuck and self-deleted, the backend
respects it and the final update-result.json is stuck.

The fake new version here is 1.1.38, same as tests/test_auto_update.py.
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bosshunter import __version__, updater
from bosshunter.updater import (
    UPDATE_FAILURE_MESSAGES,
    consume_pending_update,
    get_last_update_failure,
    get_status,
    is_update_in_progress,
    read_update_result,
    reconcile_pending_update,
    write_pending_update,
    write_update_result,
)


def _reset_update_state() -> None:
    updater._STATE.update({
        "status": "idle",
        "update_available": False,
        "progress": 0,
        "message": "",
        "error": "",
        "info": None,
        "checked_at": None,
        "staging_dir": None,
        "zip_path": None,
        "reason_code": "",
        "check_reason": "",
    })


class BatAliveDeferTests(unittest.TestCase):
    """v32 根因 1：bat 还活着时，后端不写最终的 failed/unknown 结论。"""

    def setUp(self):
        _reset_update_state()

    def tearDown(self):
        _reset_update_state()

    def _install_with_live_bat(self, base: Path, expected: str = "1.1.38") -> Path:
        install = base / "install"
        install.mkdir()
        write_pending_update(install, expected)
        (install / "update.bat").write_text("rem fake bat, still running", encoding="utf-8")
        return install

    def test_consume_defers_while_bat_alive(self):
        # 复现 run 44873002310 Retry：pending 在、bat 还在 180 秒轮询里，
        # 后端绝不能先写 failed/unknown——pending 不消费、结果文件不写。
        with tempfile.TemporaryDirectory() as tmp:
            install = self._install_with_live_bat(Path(tmp))
            verdict = consume_pending_update(install)
            assert verdict is not None
            self.assertEqual(verdict["status"], "in_progress")
            self.assertEqual(verdict["expected_version"], "1.1.38")
            # pending 还在，留给 bat 退出后的那次核对。
            self.assertTrue((install / updater.UPDATE_PENDING_FILENAME).is_file())
            # 结果文件一个字节都不写：06:05:09 读到的 unknown 就是这么来的。
            self.assertFalse((install / updater.UPDATE_RESULT_FILENAME).exists())
            self.assertIsNone(get_last_update_failure(install))

    def test_reconcile_defers_without_error_state(self):
        # 让位期间不报失败、也不弹更新：状态原样 idle，等 bat 退出再定。
        with tempfile.TemporaryDirectory() as tmp:
            install = self._install_with_live_bat(Path(tmp))
            self.assertTrue(reconcile_pending_update(install))
            state = get_status()
            self.assertEqual(state["status"], "idle")
            self.assertEqual(state["error"], "")
            self.assertEqual(state.get("reason_code"), "")
            self.assertFalse(state["update_available"])
            # 让位不消费 pending：下次核对还能见到它。
            self.assertTrue((install / updater.UPDATE_PENDING_FILENAME).is_file())

    def test_no_bat_means_no_defer(self):
        # 没有 bat 文件 = bat 已退出（自删），老路不变：没写结果就是 unknown。
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            write_pending_update(install, "1.1.38")
            verdict = consume_pending_update(install)
            assert verdict is not None
            self.assertEqual(verdict["status"], "failed")
            self.assertEqual(verdict["reason_code"], "unknown")
            self.assertFalse((install / updater.UPDATE_PENDING_FILENAME).exists())


class BatStuckRespectedTests(unittest.TestCase):
    """v32：bat 写下 stuck 后，后端尊重它（不覆盖、不 rescue）。"""

    def setUp(self):
        _reset_update_state()

    def tearDown(self):
        _reset_update_state()

    def _install_with_bat_stuck(self, base: Path) -> Path:
        # bat 的 verdict 格式：只有 status/expected_version/reason_code，
        # 没有 actual_version、没有 reason；bat 写完就自删了。
        install = base / "install"
        install.mkdir()
        write_pending_update(install, "1.1.38")
        write_update_result(install, {
            "status": "failed",
            "expected_version": "1.1.38",
            "reason_code": "stuck",
        })
        return install

    def test_bat_stuck_becomes_final_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = self._install_with_bat_stuck(Path(tmp))
            verdict = consume_pending_update(install)
            assert verdict is not None
            self.assertEqual(verdict["status"], "failed")
            self.assertEqual(verdict["reason_code"], "stuck")
            self.assertEqual(verdict["reason"], UPDATE_FAILURE_MESSAGES["stuck"])
            self.assertIn("主程序关不掉", verdict["reason"])
            self.assertEqual(verdict["expected_version"], "1.1.38")
            self.assertEqual(verdict["actual_version"], __version__)
            # 最终文件内容就是 stuck：后端重写只补 reason/actual_version，
            # 不改原因码。
            final = json.loads(
                (install / updater.UPDATE_RESULT_FILENAME).read_text(encoding="utf-8")
            )
            self.assertEqual(final, {
                "status": "failed",
                "expected_version": "1.1.38",
                "actual_version": __version__,
                "reason": UPDATE_FAILURE_MESSAGES["stuck"],
                "reason_code": "stuck",
            })
            self.assertFalse((install / updater.UPDATE_PENDING_FILENAME).exists())

    def test_bat_stuck_survives_repeated_reconcile(self):
        # 第二次核对也不覆盖：失败记录一直是 stuck，直到手动重查清掉。
        with tempfile.TemporaryDirectory() as tmp:
            install = self._install_with_bat_stuck(Path(tmp))
            self.assertTrue(reconcile_pending_update(install))
            self.assertEqual(get_status().get("reason_code"), "stuck")
            self.assertIn("主程序关不掉", get_status()["error"])
            self.assertTrue(reconcile_pending_update(install))
            last = get_last_update_failure(install)
            assert last is not None
            self.assertEqual(last["reason_code"], "stuck")
            self.assertEqual(
                read_update_result(install).get("reason_code"), "stuck"
            )

    def test_stuck_is_not_rescued_even_when_service_reports_expected(self):
        # M-44 只救 nostart / 无结果：stuck 是换文件前就停下的失败，
        # 版本号对上也不能证明半截拷贝完整，绝不能翻成成功。
        with tempfile.TemporaryDirectory() as tmp:
            install = self._install_with_bat_stuck(Path(tmp))
            with mock.patch(
                "bosshunter.updater._service_reports_version", return_value=True
            ):
                verdict = consume_pending_update(install)
            assert verdict is not None
            self.assertEqual(verdict["status"], "failed")
            self.assertEqual(verdict["reason_code"], "stuck")


class StaleBatValveTests(unittest.TestCase):
    """过期阀门：被杀掉的 bat 不会自删，残留文件不能让 pending 永久卡住。"""

    def setUp(self):
        _reset_update_state()

    def tearDown(self):
        _reset_update_state()

    def test_stale_bat_file_does_not_block_forever(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            write_pending_update(install, "1.1.38")
            bat = install / "update.bat"
            bat.write_text("rem dead bat, never self-deleted", encoding="utf-8")
            old = time.time() - 16 * 60
            os.utime(bat, (old, old))
            self.assertFalse(is_update_in_progress(install))
            verdict = consume_pending_update(install)
            assert verdict is not None
            # bat 死了、没写结果：按“bat 没写结果”走 unknown——这是实话，
            # 不是抢跑；pending 正常消费，不永久卡住。
            self.assertEqual(verdict["status"], "failed")
            self.assertEqual(verdict["reason_code"], "unknown")
            self.assertFalse((install / updater.UPDATE_PENDING_FILENAME).exists())

    def test_is_update_in_progress_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            # pending + 新 bat = 进行中。
            install = base / "a"
            install.mkdir()
            write_pending_update(install, "1.1.38")
            (install / "update.bat").write_text("rem live", encoding="utf-8")
            self.assertTrue(is_update_in_progress(install))
            # pending 在、没 bat = 已结束。
            install2 = base / "b"
            install2.mkdir()
            write_pending_update(install2, "1.1.38")
            self.assertFalse(is_update_in_progress(install2))
            # 没 pending、有 bat 残留（bat 没拉起来那次）= 不算进行中。
            install3 = base / "c"
            install3.mkdir()
            (install3 / "update.bat").write_text("rem leftover", encoding="utf-8")
            self.assertFalse(is_update_in_progress(install3))
            # pending + 陈旧 bat = 已死，不算进行中。
            install4 = base / "d"
            install4.mkdir()
            write_pending_update(install4, "1.1.38")
            bat = install4 / "update.bat"
            bat.write_text("rem stale", encoding="utf-8")
            old = time.time() - 16 * 60
            os.utime(bat, (old, old))
            self.assertFalse(is_update_in_progress(install4))


class RelaunchPortTests(unittest.TestCase):
    """v32 根因 2：bat 轮询问的是重开端口 8686，不是更新前的服务端口。"""

    def setUp(self):
        _reset_update_state()

    def tearDown(self):
        _reset_update_state()

    def test_bat_polls_the_port_arg_it_is_given(self):
        # bat 的轮询 URL 一律问 %PORT%（第 7 个参数）；下面一个测试钉住
        # apply 传的第 7 个参数就是 8686，两者合起来才是修复。
        script = updater.build_update_script()
        self.assertIn('set "PORT=%~7"', script)
        self.assertIn('if "%PORT%"=="" set "PORT=8686"', script)
        self.assertIn("http://127.0.0.1:%PORT%/api/health", script)
        self.assertNotIn("http://127.0.0.1:8686/api/health", script)

    def test_apply_passes_relaunch_port_even_for_non_default_service(self):
        # 服务跑在哪个端口都不影响 bat：第 7 个参数永远是重开端口。
        # 顺带钉住接口：apply_update 不再接受端口参数，任何调用方都
        # 传不进自定义端口——v31 的 bug 就是从这个参数进来的。
        import inspect

        from unittest.mock import patch

        self.assertNotIn("port", inspect.signature(updater.apply_update).parameters)

        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "staging"
            (staging / "_internal").mkdir(parents=True)
            (staging / "简历猎手.exe").write_bytes(b"new-exe")
            (staging / "_internal" / "app.bin").write_bytes(b"new-app")
            updater._STATE.update({
                "status": "ready",
                "staging_dir": str(staging),
                "info": {"version": "1.1.38"},
            })
            install = Path(tmp) / "install"
            install.mkdir()
            (install / "简历猎手.exe").write_bytes(b"old-exe")
            with (
                patch("bosshunter.updater._is_windows", return_value=True),
                patch("bosshunter.app_window.close_app_window", return_value=True),
                patch("bosshunter.browser.runtime.stop_runtime", return_value=True),
                patch("bosshunter.browser.runtime.stop_launched_browser", return_value=True),
                patch("bosshunter.updater._kill_install_dir_processes", return_value=True),
                patch("bosshunter.updater.subprocess.Popen") as popen,
            ):
                updater.apply_update(install, "简历猎手.exe")
            argv = popen.call_args.args[0]
            self.assertEqual(argv[9], str(updater.RELAUNCH_PORT))
            self.assertEqual(argv[9], "8686")


if __name__ == "__main__":
    unittest.main()
