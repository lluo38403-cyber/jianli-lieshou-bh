"""桌面快捷方式：启动时确保桌面上有「简历猎手.lnk」。

lucky 2026-11-20 要求：更新之后或者下载之后，桌面自动创建一个快捷方式。
实现在 bosshunter.updater.ensure_desktop_shortcut，run_server 启动时调一次。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bosshunter import updater


class EnsureDesktopShortcutTests(unittest.TestCase):
    def _install(self, tmp):
        install = Path(tmp) / "install"
        install.mkdir()
        exe = install / "简历猎手.exe"
        exe.write_bytes(b"MZ")
        return install, exe

    def _windows_frozen(self):
        return (
            patch("bosshunter.updater._is_windows", return_value=True),
            patch("bosshunter.updater._is_frozen", return_value=True),
        )

    def test_returns_false_off_windows_without_running_anything(self):
        with patch("bosshunter.updater.subprocess.run") as run:
            self.assertFalse(updater.ensure_desktop_shortcut())
        run.assert_not_called()

    def test_returns_false_when_not_frozen(self):
        # 开发模式（没打包）不建快捷方式。
        with (
            patch("bosshunter.updater._is_windows", return_value=True),
            patch("bosshunter.updater._is_frozen", return_value=False),
            patch("bosshunter.updater.subprocess.run") as run,
        ):
            self.assertFalse(updater.ensure_desktop_shortcut())
        run.assert_not_called()

    def test_creates_shortcut_and_writes_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, exe = self._install(tmp)
            win, frozen = self._windows_frozen()
            with (
                win,
                frozen,
                patch("bosshunter.updater.subprocess.run") as run,
            ):
                run.return_value = Mock(returncode=0)
                self.assertTrue(updater.ensure_desktop_shortcut(install, exe))
            run.assert_called_once()
            argv = run.call_args.args[0]
            self.assertEqual(argv[0], "powershell.exe")
            self.assertIn("-NoProfile", argv)
            self.assertIn("-NonInteractive", argv)
            script = argv[-1]
            # 桌面路径用 .NET 取，能处理 OneDrive 重定向的桌面。
            self.assertIn("GetFolderPath('Desktop')", script)
            self.assertIn("WScript.Shell", script)
            self.assertIn("简历猎手.lnk", script)
            self.assertIn(str(exe), script)
            self.assertIn("TargetPath", script)
            self.assertIn("WorkingDirectory", script)
            self.assertIn("IconLocation", script)
            flags = run.call_args.kwargs.get("creationflags", 0)
            self.assertTrue(flags & 0x08000000, "建快捷方式自己也不能弹黑框")
            marker = install / "data" / "desktop_shortcut_created"
            self.assertTrue(marker.is_file())

    def test_defaults_exe_to_sys_executable(self):
        # 不传 exe 时用 frozen 的 sys.executable 当目标。
        with tempfile.TemporaryDirectory() as tmp:
            install, exe = self._install(tmp)
            win, frozen = self._windows_frozen()
            with (
                win,
                frozen,
                patch.object(sys, "executable", str(exe)),
                patch("bosshunter.updater.subprocess.run") as run,
            ):
                run.return_value = Mock(returncode=0)
                self.assertTrue(updater.ensure_desktop_shortcut(install))
            script = run.call_args.args[0][-1]
            self.assertIn(str(exe), script)

    def test_marker_means_do_not_recreate(self):
        # 用户手动删了快捷方式也不再补建：marker 旧版 + 桌面上没 .lnk，
        # 刷新脚本跑一次（Test-Path 守着不重建），返回 False，marker
        # 版本写到当前版，下次启动不再重复检查。
        with tempfile.TemporaryDirectory() as tmp:
            install, exe = self._install(tmp)
            marker = install / "data" / "desktop_shortcut_created"
            marker.parent.mkdir(parents=True)
            marker.write_text("1.1.7\n", encoding="utf-8")
            win, frozen = self._windows_frozen()
            with (
                win,
                frozen,
                patch("bosshunter.updater.subprocess.run") as run,
            ):
                run.return_value = Mock(returncode=0, stdout="missing\n")
                self.assertFalse(updater.ensure_desktop_shortcut(install, exe))
            run.assert_called_once()
            script = run.call_args.args[0][-1]
            self.assertIn("if (Test-Path $lnkPath)", script)
            self.assertIn("Write-Output 'missing'", script)
            self.assertEqual(marker.read_text(encoding="utf-8").strip(), updater.__version__)

    def test_old_marker_refreshes_existing_shortcut(self):
        # marker 是 1.1.7（没图标的版本）+ 桌面上有 .lnk：重写一次，
        # TargetPath/IconLocation 都指回当前 exe，marker 写到当前版。
        with tempfile.TemporaryDirectory() as tmp:
            install, exe = self._install(tmp)
            marker = install / "data" / "desktop_shortcut_created"
            marker.parent.mkdir(parents=True)
            marker.write_text("1.1.7\n", encoding="utf-8")
            win, frozen = self._windows_frozen()
            with (
                win,
                frozen,
                patch("bosshunter.updater.subprocess.run") as run,
            ):
                run.return_value = Mock(returncode=0, stdout="refreshed\n")
                self.assertTrue(updater.ensure_desktop_shortcut(install, exe))
            run.assert_called_once()
            script = run.call_args.args[0][-1]
            self.assertIn("if (Test-Path $lnkPath)", script)
            self.assertIn(str(exe), script)
            self.assertIn("IconLocation", script)
            self.assertEqual(marker.read_text(encoding="utf-8").strip(), updater.__version__)

    def test_current_marker_does_nothing(self):
        # marker 已经是当前版：什么都不做，PowerShell 一次都不跑。
        with tempfile.TemporaryDirectory() as tmp:
            install, exe = self._install(tmp)
            marker = install / "data" / "desktop_shortcut_created"
            marker.parent.mkdir(parents=True)
            marker.write_text(f"{updater.__version__}\n", encoding="utf-8")
            win, frozen = self._windows_frozen()
            with (
                win,
                frozen,
                patch("bosshunter.updater.subprocess.run") as run,
            ):
                self.assertFalse(updater.ensure_desktop_shortcut(install, exe))
            run.assert_not_called()

    def test_unparseable_marker_treated_as_old(self):
        # marker 内容读不懂：当旧版处理，刷新走一遍。
        with tempfile.TemporaryDirectory() as tmp:
            install, exe = self._install(tmp)
            marker = install / "data" / "desktop_shortcut_created"
            marker.parent.mkdir(parents=True)
            marker.write_text("garbage\n", encoding="utf-8")
            win, frozen = self._windows_frozen()
            with (
                win,
                frozen,
                patch("bosshunter.updater.subprocess.run") as run,
            ):
                run.return_value = Mock(returncode=0, stdout="refreshed\n")
                self.assertTrue(updater.ensure_desktop_shortcut(install, exe))
            run.assert_called_once()

    def test_refresh_failure_keeps_old_marker(self):
        # 刷新失败不改 marker，下次启动再试。
        with tempfile.TemporaryDirectory() as tmp:
            install, exe = self._install(tmp)
            marker = install / "data" / "desktop_shortcut_created"
            marker.parent.mkdir(parents=True)
            marker.write_text("1.1.7\n", encoding="utf-8")
            win, frozen = self._windows_frozen()
            with (
                win,
                frozen,
                patch("bosshunter.updater.subprocess.run") as run,
            ):
                run.return_value = Mock(returncode=1)
                self.assertFalse(updater.ensure_desktop_shortcut(install, exe))
            self.assertEqual(marker.read_text(encoding="utf-8").strip(), "1.1.7")

    def test_parse_version_tuple(self):
        self.assertEqual(updater._parse_version_tuple("1.1.12"), (1, 1, 12))
        self.assertEqual(updater._parse_version_tuple("1.1.7\n"), (1, 1, 7))
        self.assertIsNone(updater._parse_version_tuple("garbage"))
        self.assertIsNone(updater._parse_version_tuple(""))
        self.assertTrue(updater._parse_version_tuple("1.1.7") < updater._parse_version_tuple("1.1.12"))

    def test_powershell_failure_returns_false_and_writes_no_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, exe = self._install(tmp)
            win, frozen = self._windows_frozen()
            with (
                win,
                frozen,
                patch("bosshunter.updater.subprocess.run") as run,
            ):
                run.return_value = Mock(returncode=1)
                self.assertFalse(updater.ensure_desktop_shortcut(install, exe))
            self.assertFalse((install / "data" / "desktop_shortcut_created").exists())

    def test_subprocess_exception_returns_false(self):
        # PowerShell 起不来也不挡启动。
        with tempfile.TemporaryDirectory() as tmp:
            install, exe = self._install(tmp)
            win, frozen = self._windows_frozen()
            with (
                win,
                frozen,
                patch(
                    "bosshunter.updater.subprocess.run",
                    side_effect=OSError("no powershell"),
                ),
            ):
                self.assertFalse(updater.ensure_desktop_shortcut(install, exe))
            self.assertFalse((install / "data" / "desktop_shortcut_created").exists())

    def test_declined_marker_means_do_not_create(self):
        # v41：安装时用户不勾选“创建桌面快捷方式”，安装程序写
        # desktop_shortcut_declined marker——启动时见到它就不再补建，
        # 否则向导里的勾选框等于没生效（v41 核实前程序每次启动都会
        # 自建桌面快捷方式）。
        with tempfile.TemporaryDirectory() as tmp:
            install, exe = self._install(tmp)
            declined = install / "data" / "desktop_shortcut_declined"
            declined.parent.mkdir(parents=True)
            declined.write_text("1.1.27\n", encoding="utf-8")
            win, frozen = self._windows_frozen()
            with (
                win,
                frozen,
                patch("bosshunter.updater.subprocess.run") as run,
            ):
                self.assertFalse(updater.ensure_desktop_shortcut(install, exe))
            run.assert_not_called()
            # declined marker 本身不动，created marker 不写。
            self.assertTrue(declined.is_file())
            self.assertFalse(
                (install / "data" / "desktop_shortcut_created").exists()
            )

    def test_declined_marker_beats_created_marker(self):
        # 两个 marker 理论上互斥（安装程序写一个就删另一个），万一
        # 同时存在，declined 优先：不建。
        with tempfile.TemporaryDirectory() as tmp:
            install, exe = self._install(tmp)
            for name in (
                "desktop_shortcut_created",
                "desktop_shortcut_declined",
            ):
                marker = install / "data" / name
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("1.1.27\n", encoding="utf-8")
            win, frozen = self._windows_frozen()
            with (
                win,
                frozen,
                patch("bosshunter.updater.subprocess.run") as run,
            ):
                self.assertFalse(updater.ensure_desktop_shortcut(install, exe))
            run.assert_not_called()

    def test_declined_marker_name_matches_installer(self):
        # 安装程序写 marker、程序启动时认它，两个模块必须用同一个
        # 文件名，否则勾选框等于没生效。
        from bosshunter import installer

        self.assertEqual(
            updater.DESKTOP_SHORTCUT_DECLINED_MARKER_FILENAME,
            installer.DESKTOP_SHORTCUT_DECLINED_MARKER_FILENAME,
        )

    def test_ps_quote_doubles_single_quotes(self):
        self.assertEqual(updater._ps_quote("C:\\a'b"), "'C:\\a''b'")


if __name__ == "__main__":
    unittest.main()
