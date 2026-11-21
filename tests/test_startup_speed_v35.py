"""简历猎手 v35 启动提速：残留清理只在有证据时扫安装目录、杀进程脚本
只在真杀了进程时睡 1 秒、重型 import 懒加载。

背景（lucky 2026-11-21 确认）：启动慢是每次打开都慢，不是只有
第一次慢。沙箱实测：进程启动到 /api/health 通只要 0.47 秒，但那是
Linux 沙箱；真机是无影云电脑（慢速共享盘 + 慢 CPU）上的 frozen exe，
每次启动固定要付：① PowerShell 冷启动 + 全进程 CIM 枚举 +
脚本里固定睡 1 秒（残留清理在启动页之前跑，窗口出现前就得等完）；
② import bosshunter.web.server 约 0.77 秒（含只在打分说明接口用
一次的 scorer，约 65ms）。实测发现：三个平台城市快照加载器懒加载
无效——采集编排器（顶层 import）本来就会拉起整个平台注册表，
三个模块启动时已经在内存里了，所以只懒加载 scorer。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bosshunter.browser.runtime as runtime

ROOT = Path(__file__).resolve().parent.parent


class SweepGateTests(unittest.TestCase):
    """残留清理的安装目录横扫只在有残留证据时跑。

    正常退出后实例文件与浏览器 state 都已清掉，安装目录里不可能
    有残留；每次启动都跑 PowerShell 全进程枚举是白等的固定开销。
    残留只可能来自上次没正常退出：那时实例文件一定还在（只有正常
    退出才会清掉它），或浏览器 state 文件还在。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._data_dir = Path(self._tmp.name) / "data"
        self._data_dir.mkdir(parents=True)
        self._install_dir = Path(self._tmp.name) / "install"
        self._install_dir.mkdir(parents=True)
        self._old_override = runtime._DATA_DIR_OVERRIDE
        self._old_launched = runtime._launched_browser
        runtime.set_data_dir(self._data_dir)
        runtime._launched_browser = None
        self.addCleanup(lambda: runtime.set_data_dir(self._old_override))
        self.addCleanup(lambda: setattr(runtime, "_launched_browser", self._old_launched))
        # 沙箱是 Linux：传 install_dir 才能走到横扫分支（frozen 的
        # exe 在 Windows 上不用传，target 就是安装目录）。
        self.enterContext(patch("bosshunter.app_window.is_our_service_alive", return_value=False))

    def _cleanup(self, **kwargs):
        return runtime.cleanup_stale_browser_resources(
            data_dir=self._data_dir, port=18686, install_dir=self._install_dir, **kwargs
        )

    def test_no_evidence_skips_install_dir_sweep(self):
        # 干净启动：没有实例文件、没有浏览器 state——横扫一次也不跑。
        with patch(
            "bosshunter.browser.runtime._kill_processes_with_exe_under"
        ) as sweep:
            cleaned = self._cleanup()
        sweep.assert_not_called()
        self.assertFalse(cleaned)

    def test_stale_instance_file_still_sweeps(self):
        # 上次没正常退出：实例文件记了一个已死的 PID——横扫照常跑。
        (self._data_dir / runtime.INSTANCE_FILENAME).write_text(
            json.dumps({"pid": 999999, "executable": "x", "port": 8686}),
            encoding="utf-8",
        )
        with (
            patch("bosshunter.browser.runtime._is_pid_our_exe", return_value=False),
            patch(
                "bosshunter.browser.runtime._kill_processes_with_exe_under",
                return_value=True,
            ) as sweep,
        ):
            cleaned = self._cleanup()
        sweep.assert_called_once_with(self._install_dir)
        self.assertTrue(cleaned)
        # 陈旧实例文件照常清掉。
        self.assertFalse((self._data_dir / runtime.INSTANCE_FILENAME).exists())

    def test_browser_state_only_still_sweeps(self):
        # 只有浏览器 state 文件（残留 Chrome 的记录）——横扫照常跑。
        (self._data_dir / runtime.BROWSER_STATE_FILENAME).write_text(
            json.dumps(
                {
                    "pid": 777,
                    "executable": "C:/Chrome/chrome.exe",
                    "profile_dir": "C:/Users/x/.jianli-lieshou/browser-profile",
                    "port": 9222,
                }
            ),
            encoding="utf-8",
        )
        with (
            patch("bosshunter.browser.runtime.stop_launched_browser", return_value=True),
            patch(
                "bosshunter.browser.runtime._kill_processes_with_exe_under",
                return_value=True,
            ) as sweep,
        ):
            cleaned = self._cleanup()
        sweep.assert_called_once_with(self._install_dir)
        self.assertTrue(cleaned)

    def test_excluded_browser_state_alone_does_not_sweep(self):
        # 唯一的浏览器记录就是本次启动刚拉起的（exclude_pid）——
        # 它不是残留，不构成横扫的证据。
        (self._data_dir / runtime.BROWSER_STATE_FILENAME).write_text(
            json.dumps(
                {
                    "pid": 555,
                    "executable": "C:/Chrome/chrome.exe",
                    "profile_dir": "C:/Users/x/.jianli-lieshou/browser-profile",
                    "port": 9222,
                }
            ),
            encoding="utf-8",
        )
        with patch(
            "bosshunter.browser.runtime._kill_processes_with_exe_under"
        ) as sweep:
            cleaned = self._cleanup(exclude_pid=555)
        sweep.assert_not_called()
        self.assertFalse(cleaned)
        # 被排除的记录留着（v30 的 H1 行为不变）。
        self.assertTrue((self._data_dir / runtime.BROWSER_STATE_FILENAME).exists())


class KillScriptSleepTests(unittest.TestCase):
    """杀进程脚本：没杀到东西就不睡那 1 秒。"""

    def test_sleep_only_when_something_was_killed(self):
        from bosshunter.updater import _install_dir_kill_script

        script = _install_dir_kill_script("C:/jianli-lieshou")
        # 等进程退出只在真杀了进程时才有意义。
        self.assertIn("if ($killed -gt 0) { Start-Sleep -Seconds 1 }", script)
        # 旧写法是无条件睡 1 秒（每次启动都白等），不许回来。
        self.assertNotIn("Start-Sleep -Seconds 1; ", script)


class LazyImportTests(unittest.TestCase):
    """v35：scorer 启动不加载（只在打分说明接口用一次）。"""

    def test_server_import_skips_scorer(self):
        # 全新解释器里只 import server：scorer 不应被拉进来（之前
        # 它一家就占约 65ms，还会拉 rich）。
        env = dict(os.environ)
        env["PYTHONPATH"] = f"{ROOT}:{ROOT / 'src'}"
        code = (
            "import sys; import bosshunter.web.server; "
            "print('bosshunter.ai.scorer' in sys.modules)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "False")

    def test_city_snapshot_loaders_stay_top_level(self):
        # 反向钉住一条实测结论：三个平台城市快照加载器不要懒加载——
        # 采集编排器（server 顶层 import）本来就会拉起整个平台注册表，
        # 三个模块启动时已经在内存里了，懒加载省不出时间还多一层绕路。
        # 这里只确认它们仍是 server 的顶层 import（改动时别又绕回去）。
        source = (ROOT / "src" / "bosshunter" / "web" / "server.py").read_text(
            encoding="utf-8"
        )
        expected = {
            "load_zhilian_city_snapshot": "bosshunter.collection.platforms.zhilian",
            "load_51job_city_snapshot": "bosshunter.collection.platforms.job51",
            "load_liepin_city_snapshot": "bosshunter.collection.platforms.liepin",
        }
        for name, module in expected.items():
            self.assertIn(f"from {module} import {name}", source)


if __name__ == "__main__":
    unittest.main()
