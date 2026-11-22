"""简历猎手 v39（1.1.25）· 安装 / 卸载口径测试。

只测 src/bosshunter/installer.py 里的纯函数：默认路径、用户改目录、
安全谓词、卸载清理清单、停进程脚本、残留上报。Windows 真机行为
（向导、注册表、实际删除）不在 Linux CI 的覆盖范围，见改动清单。
"""

import tempfile
import unittest
from pathlib import Path

from bosshunter import installer


class DefaultInstallDirTests(unittest.TestCase):
    def test_default_is_installer_dir_plus_zhaopinglieshou(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                installer.default_install_dir(tmp),
                Path(tmp) / "zhaopinglieshou",
            )

    def test_default_rejects_empty_installer_dir(self):
        for bad in ("", "   ", None):
            with self.assertRaises(ValueError):
                installer.default_install_dir(bad)


class ResolveTargetDirTests(unittest.TestCase):
    def test_blank_choice_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            for blank in (None, "", "   "):
                self.assertEqual(
                    installer.resolve_target_dir(blank, tmp),
                    Path(tmp) / "zhaopinglieshou",
                )

    def test_user_choice_is_used_as_is_without_forced_subdir(self):
        # 用户亲手改目录：以所选为准，不再强行追加一层 zhaopinglieshou。
        with tempfile.TemporaryDirectory() as tmp:
            chosen = Path(tmp) / "my-place"
            self.assertEqual(
                installer.resolve_target_dir(str(chosen), tmp), chosen
            )
            self.assertEqual(
                installer.resolve_target_dir(chosen, tmp), chosen
            )

    def test_user_choice_inside_installer_dir_keeps_no_extra_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            chosen = Path(tmp) / "zhaopinglieshou"
            self.assertEqual(
                installer.resolve_target_dir(str(chosen), tmp), chosen
            )

    def test_relative_choice_is_resolved_under_installer_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                installer.resolve_target_dir("custom", tmp),
                Path(tmp) / "custom",
            )


class SafetyPredicateTests(unittest.TestCase):
    def test_rejects_empty_and_roots(self):
        for bad in ("", "   ", "C:\\", "C:", "/", "."):
            self.assertIsNotNone(
                installer.unsafe_reason(bad), f"{bad!r} 应该被拒绝"
            )
            self.assertFalse(installer.is_safe_install_dir(bad))

    def test_rejects_windows_system_dirs(self):
        for bad in (
            r"C:\Windows",
            r"C:\Windows\System32",
            r"C:\Windows\SysWOW64",
            r"D:\Windows\System32\drivers",
            r"C:\WINDOWS\system32",
        ):
            self.assertIsNotNone(
                installer.unsafe_reason(bad), f"{bad!r} 应该被拒绝"
            )

    def test_rejects_home_temp_and_their_roots(self):
        home = Path("/tmp/fake-home")
        tempdir = Path("/tmp/fake-temp")
        self.assertIsNotNone(
            installer.unsafe_reason(str(home), home=home, tempdir=tempdir)
        )
        self.assertIsNotNone(
            installer.unsafe_reason(str(tempdir), home=home, tempdir=tempdir)
        )
        # 临时目录下任何位置都不行；主目录下建子目录可以。
        self.assertIsNotNone(
            installer.unsafe_reason(
                str(tempdir / "foo"), home=home, tempdir=tempdir
            )
        )
        self.assertIsNone(
            installer.unsafe_reason(
                str(home / "zhaopinglieshou"), home=home, tempdir=tempdir
            )
        )

    def test_accepts_normal_dirs_including_program_files(self):
        for good in (
            r"C:\Users\lucky\Downloads\zhaopinglieshou",
            r"D:\zhaopinglieshou",
            r"C:\Program Files\zhaopinglieshou",
            "/home/lucky/zhaopinglieshou",
            "/opt/zhaopinglieshou",
        ):
            self.assertIsNone(
                installer.unsafe_reason(good), f"{good!r} 应该被接受"
            )

    def test_resolve_target_dir_refuses_system_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                installer.resolve_target_dir(r"C:\Windows\System32", tmp)
            with self.assertRaises(ValueError):
                installer.resolve_target_dir("C:\\", tmp)

    def test_uninstall_plan_refuses_unsafe_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                installer.uninstall_cleanup_paths(
                    r"C:\Windows", tmp, tmp
                )
            with self.assertRaises(ValueError):
                installer.uninstall_cleanup_paths("", tmp, tmp)


class UninstallCleanupTests(unittest.TestCase):
    def _layout(self, tmp):
        install = Path(tmp) / "zhaopinglieshou"
        home = Path(tmp) / "home"
        tempdir = Path(tmp) / "temp"
        for d in (install, home, tempdir):
            d.mkdir(parents=True)
        # 用户数据
        (install / "config.yaml").write_text("x: 1", encoding="utf-8")
        (install / "data").mkdir()
        (install / "data" / "bosshunter.db").write_bytes(b"db")
        (install / "data" / "resumes").mkdir()
        (install / "data" / "resumes" / "a.md").write_text("cv", encoding="utf-8")
        # 运行产物
        (install / "data" / "logs").mkdir()
        (install / "data" / "logs" / "jianli-lieshou.log").write_text(
            "log", encoding="utf-8"
        )
        (install / "data" / "update-cache").mkdir()
        (install / "data" / "update-cache" / "update-ready.json").write_text(
            "{}", encoding="utf-8"
        )
        for name in (
            ".instance.json",
            "launched-browser.json",
            "window-geometry.json",
            ".startup-failed.json",
            "desktop_shortcut_created",
        ):
            (install / "data" / name).write_text("{}", encoding="utf-8")
        (install / "update-pending.json").write_text("{}", encoding="utf-8")
        (install / "update.log").write_text("log", encoding="utf-8")
        (install / "update-backup-1.1.24").mkdir()
        # 工作浏览器配置目录
        profile = home / ".jianli-lieshou" / "browser-profile"
        profile.mkdir(parents=True)
        (profile / "Default").mkdir()
        # %TEMP% 里的本程序残留 + 别家文件
        (tempdir / "bosshunter_resume.html").write_text("<html>", encoding="utf-8")
        (tempdir / "bosshunter_resume_123.html").write_text(
            "<html>", encoding="utf-8"
        )
        (tempdir / "bosshunter-browser-abcdef0123456789abcd.lock").write_text(
            "0", encoding="utf-8"
        )
        (tempdir / "jianli-update-xyz").mkdir()
        (tempdir / "other-app.tmp").write_text("not ours", encoding="utf-8")
        (tempdir / "chrome-cache").mkdir()
        return install, home, tempdir

    def test_keep_user_data_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, home, tempdir = self._layout(tmp)
            plan = installer.uninstall_cleanup_paths(install, home, tempdir)
            self.assertEqual(
                plan.keep_paths,
                [
                    install / "config.yaml",
                    install / "data" / "bosshunter.db",
                    install / "data" / "resumes",
                ],
            )
            # 用户数据不在删除清单里，简历与数据库都在保留清单里。
            for kept in plan.keep_paths:
                self.assertNotIn(kept, plan.remove_paths)
            # 有用户数据在，安装目录本身不能整体删。
            self.assertFalse(plan.install_dir_removable)

    def test_cleanup_list_only_contains_our_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, home, tempdir = self._layout(tmp)
            plan = installer.uninstall_cleanup_paths(install, home, tempdir)
            # 运行产物都在清单里。
            for rel in (
                "data/logs",
                "data/update-cache",
                "data/.instance.json",
                "data/launched-browser.json",
                "data/window-geometry.json",
                "data/.startup-failed.json",
                "data/desktop_shortcut_created",
                "update-pending.json",
                "update.log",
                "update-backup-1.1.24",
            ):
                self.assertIn(install / rel, plan.remove_paths, rel)
            # 浏览器配置目录在清单里。
            self.assertIn(
                home / ".jianli-lieshou" / "browser-profile", plan.remove_paths
            )
            # %TEMP% 里只认本程序的三个前缀，别家文件不在清单里。
            self.assertIn(tempdir / "bosshunter_resume.html", plan.remove_paths)
            self.assertIn(
                tempdir / "bosshunter_resume_123.html", plan.remove_paths
            )
            self.assertIn(
                tempdir / "bosshunter-browser-abcdef0123456789abcd.lock",
                plan.remove_paths,
            )
            self.assertIn(tempdir / "jianli-update-xyz", plan.remove_paths)
            self.assertNotIn(tempdir / "other-app.tmp", plan.remove_paths)
            self.assertNotIn(tempdir / "chrome-cache", plan.remove_paths)
            # 清单里没有任何系统目录 / 别家路径。
            for path in plan.remove_paths:
                self.assertTrue(
                    str(path).startswith((str(install), str(home), str(tempdir))),
                    f"清单里混入了非本程序路径：{path}",
                )
                self.assertIsNone(
                    installer.unsafe_reason(path),
                    f"清单里的路径本身不安全：{path}",
                )

    def test_delete_user_data_removes_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, home, tempdir = self._layout(tmp)
            plan = installer.uninstall_cleanup_paths(
                install, home, tempdir, keep_user_data=False
            )
            self.assertEqual(plan.keep_paths, [])
            self.assertTrue(plan.install_dir_removable)
            for kept in (
                install / "config.yaml",
                install / "data" / "bosshunter.db",
                install / "data" / "resumes",
            ):
                self.assertIn(kept, plan.remove_paths)

    def test_install_dir_removable_when_no_user_data_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "zhaopinglieshou"
            home = Path(tmp) / "home"
            tempdir = Path(tmp) / "temp"
            for d in (install, home, tempdir):
                d.mkdir(parents=True)
            plan = installer.uninstall_cleanup_paths(install, home, tempdir)
            # 没产生过用户数据：保留选项下安装目录也可以整体删。
            self.assertTrue(plan.install_dir_removable)

    def test_temp_scan_never_touches_other_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "zhaopinglieshou"
            home = Path(tmp) / "home"
            tempdir = Path(tmp) / "temp"
            for d in (install, home, tempdir):
                d.mkdir(parents=True)
            # tempdir 不存在 / 是文件时返回空清单，不报错。
            missing = Path(tmp) / "no-such-temp"
            plan = installer.uninstall_cleanup_paths(install, home, missing)
            self.assertEqual(plan.temp_matches, [])
            stray = Path(tmp) / "stray.txt"
            stray.write_text("x", encoding="utf-8")
            plan = installer.uninstall_cleanup_paths(install, home, stray)
            self.assertEqual(plan.temp_matches, [])

    def test_browser_profile_dir(self):
        self.assertEqual(
            installer.browser_profile_dir("/home/lucky"),
            Path("/home/lucky") / ".jianli-lieshou" / "browser-profile",
        )


class StopProcessesScriptTests(unittest.TestCase):
    def test_kills_by_install_dir_prefix_and_excludes_self(self):
        script = installer.uninstall_stop_processes_script(
            "/tmp/app/zhaopinglieshou",
            "/tmp/home/.jianli-lieshou/browser-profile",
            exclude_pid=1234,
        )
        # 复用 updater 的红线：ExecutablePath 前缀 + 目录边界 + 排除自己。
        self.assertIn("ExecutablePath", script)
        self.assertIn("$prefix = $installDir + '\\'", script)
        self.assertIn("StartsWith($prefix", script)
        self.assertIn("ProcessId -ne 1234", script)
        # 绝不按映像名杀 node / chrome（会误杀用户浏览器和其他 node）。
        self.assertNotIn("Stop-Process -Name", script)
        self.assertNotIn("-Name chrome", script)
        self.assertNotIn("-Name node", script)

    def test_browser_kill_is_keyed_on_our_profile_dir(self):
        profile = "/tmp/home/.jianli-lieshou/browser-profile"
        script = installer.uninstall_stop_processes_script(
            "/tmp/app/zhaopinglieshou", profile, exclude_pid=1234
        )
        # 工作浏览器只按“命令行里带本程序配置目录”杀。
        self.assertIn("CommandLine", script)
        self.assertIn("Contains($profile)", script)
        self.assertIn("chrome.exe", script)
        self.assertIn("msedge.exe", script)
        self.assertIn(profile, script)
        self.assertIn("killed=", script)
        self.assertIn("browser=", script)


class LeftoverNoticeTests(unittest.TestCase):
    def test_empty_leftovers_means_no_notice(self):
        self.assertEqual(
            installer.format_leftover_notice([], "/tmp/uninstall.log"), ""
        )

    def test_leftovers_are_listed_honestly(self):
        notice = installer.format_leftover_notice(
            ["C:\\app\\zhaopinglieshou\\简历猎手.exe"],
            "C:\\Users\\x\\AppData\\Local\\Temp\\uninstall.log",
        )
        self.assertIn("简历猎手.exe", notice)
        self.assertIn("手动删除", notice)
        self.assertIn("卸载日志", notice)


class RegistryConstantsTests(unittest.TestCase):
    def test_uninstall_key_and_values(self):
        self.assertEqual(
            installer.UNINSTALL_REGISTRY_KEY,
            "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\"
            "简历猎手_is1",
        )
        for value in (
            "DisplayName",
            "DisplayVersion",
            "InstallLocation",
            "UninstallString",
            "Publisher",
            "DisplayIcon",
        ):
            self.assertIn(value, installer.UNINSTALL_REGISTRY_VALUES)

    def test_shortcut_name_matches_updater(self):
        from bosshunter import updater

        self.assertEqual(
            installer.DESKTOP_SHORTCUT_NAME, updater._DESKTOP_SHORTCUT_NAME
        )


if __name__ == "__main__":
    unittest.main()
