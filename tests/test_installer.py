"""简历猎手 v41（1.1.27）· 安装 / 卸载口径测试。

只测 src/bosshunter/installer.py 里的纯函数：默认路径、用户改目录、
安全谓词、卸载清理清单、停进程脚本、残留上报。Windows 真机行为
（向导、注册表、实际删除）不在 Linux CI 的覆盖范围，见改动清单。
"""

import tempfile
import unittest
from pathlib import Path

from bosshunter import installer


class DefaultInstallDirTests(unittest.TestCase):
    def test_default_is_installer_dir_plus_jianpinglieshou(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                installer.default_install_dir(tmp),
                Path(tmp) / "jianpinglieshou",
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
                    Path(tmp) / "jianpinglieshou",
                )

    def test_user_choice_is_used_as_is_without_forced_subdir(self):
        # 用户亲手改目录：以所选为准，不再强行追加一层 jianpinglieshou。
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
            chosen = Path(tmp) / "jianpinglieshou"
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
                str(home / "jianpinglieshou"), home=home, tempdir=tempdir
            )
        )

    def test_accepts_normal_dirs_including_program_files(self):
        for good in (
            r"C:\Users\lucky\Downloads\jianpinglieshou",
            r"D:\jianpinglieshou",
            r"C:\Program Files\jianpinglieshou",
            "/home/lucky/jianpinglieshou",
            "/opt/jianpinglieshou",
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
        install = Path(tmp) / "jianpinglieshou"
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
            "desktop_shortcut_declined",
            "auth_cache.json",
            "cities.cache.json",
        ):
            (install / "data" / name).write_text("{}", encoding="utf-8")
        # WAL 边车：db 打开时才有，与 db 同生死（v41）。
        (install / "data" / "bosshunter.db-wal").write_bytes(b"wal")
        (install / "data" / "bosshunter.db-shm").write_bytes(b"shm")
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
                    # WAL 边车随 db 一起留（v41）。
                    install / "data" / "bosshunter.db-wal",
                    install / "data" / "bosshunter.db-shm",
                    install / "data" / "resumes",
                ],
            )
            # 用户数据不在删除清单里，简历与数据库都在保留清单里。
            for kept in plan.keep_paths:
                self.assertNotIn(kept, plan.remove_paths)
            # WAL 边车随 db 一起留（v41：只删 db 不删 -wal 会丢未
            # checkpoint 的事务；只删 -wal 不删 db 更会丢数据）。
            for wal in (
                install / "data" / "bosshunter.db-wal",
                install / "data" / "bosshunter.db-shm",
            ):
                self.assertNotIn(wal, plan.remove_paths)
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
                "data/desktop_shortcut_declined",
                # 两个缓存文件不属于用户数据，卸载时一律删（v40）。
                "data/auth_cache.json",
                "data/cities.cache.json",
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
                # WAL 边车随 db 一起删（v41）。
                install / "data" / "bosshunter.db-wal",
                install / "data" / "bosshunter.db-shm",
                install / "data" / "resumes",
            ):
                self.assertIn(kept, plan.remove_paths)

    def test_install_dir_removable_when_no_user_data_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "jianpinglieshou"
            home = Path(tmp) / "home"
            tempdir = Path(tmp) / "temp"
            for d in (install, home, tempdir):
                d.mkdir(parents=True)
            plan = installer.uninstall_cleanup_paths(install, home, tempdir)
            # 没产生过用户数据：保留选项下安装目录也可以整体删。
            self.assertTrue(plan.install_dir_removable)

    def test_temp_scan_never_touches_other_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "jianpinglieshou"
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
    # v41：脚本改成参数化的——安装目录、浏览器配置目录、卸载日志、
    # 要排除的 PID、exe 名都不写进脚本文件，由 Inno 的 Exec 命令行传
    # 参（脚本全文只用 ASCII；命令行是 Unicode，中文路径没问题）。
    # 所以 uninstall_stop_processes_script() 不再带参数。

    def test_param_block_and_ascii_only_and_balanced(self):
        script = installer.uninstall_stop_processes_script()
        self.assertTrue(
            script.startswith(
                "param([string]$installDir, [string]$profile,"
                " [string]$logPath, [int]$excludePid, [string]$exeName)"
            ),
            "脚本以 param 块开头（安装目录等由命令行传参）",
        )
        # 脚本由 Inno 的 SaveStringToFile 写临时 .ps1，非 ASCII
        # 可能走系统代码页；新脚本只用 ASCII 字面量，不写中文。
        self.assertEqual(
            [c for c in script if ord(c) > 127],
            [],
        )
        for open_c, close_c in (("{", "}"), ("(", ")"), ("[", "]")):
            self.assertEqual(
                script.count(open_c),
                script.count(close_c),
                f"括号不平衡：{open_c}{close_c}",
            )
        # 没有任何运行时值写死进脚本（安装目录/配置目录/PID 都不出现）。
        self.assertNotIn("C:\\", script)
        self.assertNotIn("/tmp/", script)

    def test_graceful_exit_first_then_kill(self):
        # 先走应用内优雅退出（/api/exit：停任务 → 停 runtime /
        # 拉起的 Chrome → 安装目录扫尾），杀进程是后备手段。
        script = installer.uninstall_stop_processes_script()
        self.assertIn("/api/exit", script)
        self.assertLess(
            script.index("/api/exit"),
            script.index("for ($round = 1"),
            "/api/exit 必须在杀进程循环之前",
        )

    def test_kills_by_install_dir_prefix_and_excludes_self(self):
        script = installer.uninstall_stop_processes_script()
        # 复用 updater 的红线：ExecutablePath 前缀 + 目录边界 + 排除自己。
        self.assertIn("ExecutablePath", script)
        self.assertIn("$prefix = $installDir + '\\'", script)
        self.assertIn("StartsWith($prefix", script)
        self.assertIn("ProcessId -ne $excludePid", script)
        # 绝不按映像名杀 node / chrome（会误杀用户浏览器和其他 node）。
        self.assertNotIn("Stop-Process -Name", script)
        self.assertNotIn("-Name chrome", script)
        self.assertNotIn("-Name node", script)

    def test_excludes_uninstaller_both_phases_by_name(self):
        script = installer.uninstall_stop_processes_script()
        # 卸载程序是两阶段的：第一阶段 unins000.exe 在安装目录里，第二阶段
        # _unins.tmp 在 %TEMP% 里；exclude_pid 只排除当前阶段的 PID，第二阶段
        # 跑脚本时第一阶段的 PID 不在排除里，只靠 PID 排除会把第一阶段杀掉
        # ——被杀的第一阶段退出码是 -1（TerminateProcess -1），等它退出的
        # 调用方就看到卸载失败（流水线 #9 重跑教训）。所以第一循环还要按名
        # 排除两个阶段的卸载程序。
        self.assertIn("$p.Name -ne 'unins000.exe'", script)
        self.assertIn("$p.Name -ne '_unins.tmp'", script)

    def test_browser_kill_is_keyed_on_our_profile_dir(self):
        script = installer.uninstall_stop_processes_script()
        # 工作浏览器只按“命令行里带本程序配置目录”杀。
        self.assertIn("CommandLine", script)
        self.assertIn("Contains($profile)", script)
        self.assertIn("chrome.exe", script)
        self.assertIn("msedge.exe", script)
        self.assertIn("killed=", script)

    def test_three_kill_rounds_wait_for_pids_to_exit(self):
        # v41：最多 3 轮，每轮杀完轮询等 PID 真正消失（最多 10 秒，
        # 每 500ms 查一次），不再是 v40 的“杀完睡固定 2 秒就当死了”
        # ——v40 就是在这里漏的：Stop-Process -Force 只发终止请求就
        # 返回，主 exe 退出慢（/api/exit 优雅清理要 2–35 秒）时扫尾
        # 已经扫完、删除已经开始，db 句柄还占着。
        script = installer.uninstall_stop_processes_script()
        self.assertIn("for ($round = 1; $round -le 3; $round++)", script)
        self.assertIn("Wait-PidsGone $targets 10", script)
        self.assertIn("Start-Sleep -Milliseconds 500", script)
        self.assertIn("stuck=$($stuck.Count)", script)
        self.assertIn("stuck-procs:", script)
        # 固定睡眠已删干净。
        self.assertNotIn("Start-Sleep -Seconds 2", script)
        self.assertNotIn("Start-Sleep -Seconds 4", script)
        self.assertNotIn("Start-Sleep -Seconds 1", script)

    def test_lock_probe_before_delete(self):
        # v41：杀完先探句柄——独占打开 exe 与 bosshunter.db，5 秒没
        # 释放就再 POST 一次 /api/exit 等 30 秒；还占着才走最后一招。
        # 探针必须用 ReadWrite：只读打开对运行中的 exe 也成功，
        # 探不出锁（v32 教训）。
        script = installer.uninstall_stop_processes_script()
        self.assertIn("Test-FileLocked", script)
        self.assertIn("'ReadWrite'", script)
        self.assertIn("Wait-FilesFree 5", script)
        self.assertIn("Wait-FilesFree 30", script)
        self.assertIn("probe-ok=", script)

    def test_last_resort_kills_by_pid_after_name_enumeration(self):
        # v41 最后一招：句柄探针已证明我方文件被占着、优雅退出已试过
        # 两轮的前提下，才按产品自己的 exe 名枚举 PID，按 PID 杀——
        # 不是 broad 杀映像名；提权进程在 WMI 里 ExecutablePath 为空
        # 时路径规则看不见它，只有这一招能兜住。
        script = installer.uninstall_stop_processes_script()
        self.assertIn(
            "Win32_Process -Filter \"Name = '$exeName'\"", script
        )
        self.assertIn("Stop-Process -Id $id", script)
        # 最后一招仍排除卸载程序自己的 PID。
        self.assertIn("Where-Object { $_ -ne $excludePid }", script)

    def test_sweep_writes_result_to_uninstall_log(self):
        # v40 的扫尾输出只打到 powershell stdout，Inno 的 Exec 不收，
        # 卸载日志里只剩一个退出码——扫尾跑没跑、杀没杀、谁还占着，
        # 事后全都看不见。v41 写进卸载日志。
        script = installer.uninstall_stop_processes_script()
        self.assertIn("Write-StopLog", script)
        self.assertIn("stop-sweep: killed=", script)
        self.assertIn("$logPath", script)

    def test_cmdline_loop_catches_off_dir_processes(self):
        # 命令行里带安装目录的进程也要杀——系统 node 跑的
        # cdp-proxy（可执行路径在安装目录外）、跑 update.bat 的
        # cmd.exe、更新时的 robocopy、bat 拉起的 powershell。
        # 只认这四个名字 + 命令行证据，不按映像名扫射。
        script = installer.uninstall_stop_processes_script()
        self.assertIn(
            "@('node.exe','cmd.exe','robocopy.exe','powershell.exe')",
            script,
        )
        self.assertIn("IndexOf($installDir", script)
        # 排除自己：卸载程序 PID、两阶段名、扫尾脚本自己的 $PID。
        self.assertIn("ProcessId -ne $excludePid", script)
        self.assertIn("ProcessId -ne $PID", script)
        self.assertIn("$p.Name -ne 'unins000.exe'", script)
        self.assertIn("$p.Name -ne '_unins.tmp'", script)


class LeftoverNoticeTests(unittest.TestCase):
    def test_empty_leftovers_means_no_notice(self):
        self.assertEqual(
            installer.format_leftover_notice([], "/tmp/uninstall.log"), ""
        )

    def test_leftovers_are_listed_honestly(self):
        notice = installer.format_leftover_notice(
            ["C:\\app\\jianpinglieshou\\简历猎手.exe"],
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

    def test_declined_marker_name_matches_updater(self):
        # v41：安装程序写 declined marker、程序启动时认它，两个模块
        # 必须用同一个文件名，否则勾选框等于没生效。
        from bosshunter import updater

        self.assertEqual(
            installer.DESKTOP_SHORTCUT_DECLINED_MARKER_FILENAME,
            updater.DESKTOP_SHORTCUT_DECLINED_MARKER_FILENAME,
        )
        self.assertEqual(
            installer.DESKTOP_SHORTCUT_DECLINED_MARKER_FILENAME,
            "desktop_shortcut_declined",
        )


if __name__ == "__main__":
    unittest.main()
