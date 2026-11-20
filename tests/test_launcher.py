"""exe 启动器（launcher.py）：无窗口模式下的日志重定向与 Tee。"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import launcher


class TeeOutputTests(unittest.TestCase):
    def test_writes_to_every_stream_and_returns_length(self):
        first, second = [], []

        class Sink:
            def __init__(self, bucket):
                self._bucket = bucket

            def write(self, data):
                self._bucket.append(data)

            def flush(self):
                pass

        tee = launcher._TeeOutput(Sink(first), Sink(second))
        self.assertEqual(tee.write("hello"), 5)
        self.assertEqual(first, ["hello"])
        self.assertEqual(second, ["hello"])
        self.assertFalse(tee.isatty())
        self.assertEqual(tee.encoding, "utf-8")

    def test_a_broken_stream_never_breaks_the_other(self):
        good = []

        class Broken:
            def write(self, data):
                raise OSError("句柄无效")

            def flush(self):
                raise OSError("句柄无效")

        class Sink:
            def write(self, data):
                good.append(data)

            def flush(self):
                pass

        # 无窗口 exe 双击启动时原来的 stdout/stderr 就是这种坏句柄：
        # 往里写直接报 OSError，必须悄悄跳过，只保证日志文件写进去。
        tee = launcher._TeeOutput(Broken(), Sink())
        self.assertEqual(tee.write("hello"), 5)
        tee.flush()
        self.assertEqual(good, ["hello"])

    def test_none_streams_are_skipped(self):
        # PyInstaller 无窗口模式下 sys.stdout 可能是 None。
        tee = launcher._TeeOutput(None, None)
        self.assertEqual(tee.write("hello"), 5)
        tee.flush()


class RedirectOutputToLogTests(unittest.TestCase):
    def setUp(self):
        self._old_out = sys.stdout
        self._old_err = sys.stderr

    def tearDown(self):
        sys.stdout = self._old_out
        sys.stderr = self._old_err

    def _close_tee(self, tee):
        # 只关日志文件那一路：别关掉测试进程自己的 stdout/stderr。
        for stream in getattr(tee, "_streams", []):
            name = getattr(stream, "name", "")
            if isinstance(name, str) and name.endswith(".log"):
                try:
                    stream.close()
                except Exception:
                    pass

    def test_redirects_both_streams_into_the_log_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe_dir = Path(tmp)
            tee = launcher._redirect_output_to_log(exe_dir)
            self.assertIsNotNone(tee)
            assert tee is not None
            self.assertIs(sys.stdout, tee)
            self.assertIsNot(sys.stderr, tee)
            sys.stdout.write("out-line\n")
            sys.stderr.write("err-line\n")
            self._close_tee(sys.stdout)
            self._close_tee(sys.stderr)
            log = exe_dir / "data" / "logs" / "jianli-lieshou.log"
            self.assertTrue(log.is_file())
            content = log.read_text(encoding="utf-8")
            self.assertIn("out-line", content)
            self.assertIn("err-line", content)

    def test_rotates_a_log_file_over_5mb(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe_dir = Path(tmp)
            log_dir = exe_dir / "data" / "logs"
            log_dir.mkdir(parents=True)
            log_path = log_dir / "jianli-lieshou.log"
            log_path.write_bytes(b"x" * (launcher._LOG_MAX_BYTES + 1))
            tee = launcher._redirect_output_to_log(exe_dir)
            self.assertIsNotNone(tee)
            self._close_tee(tee)
            old = log_dir / "jianli-lieshou.log.old"
            self.assertTrue(old.is_file())
            self.assertEqual(old.stat().st_size, launcher._LOG_MAX_BYTES + 1)
            self.assertTrue(log_path.is_file())
            self.assertLess(log_path.stat().st_size, 1024)

    def test_returns_none_and_changes_nothing_when_the_log_dir_is_unusable(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe_dir = Path(tmp)
            # data/logs 被一个同名文件占住：建目录失败。
            (exe_dir / "data").mkdir()
            (exe_dir / "data" / "logs").write_text("not a dir", encoding="utf-8")
            self.assertIsNone(launcher._redirect_output_to_log(exe_dir))
            self.assertIs(sys.stdout, self._old_out)
            self.assertIs(sys.stderr, self._old_err)


class LauncherMainTests(unittest.TestCase):
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

    def _run_main(self, tmp, argv):
        exe_dir = Path(tmp)
        sys.frozen = True
        sys.argv = ["简历猎手.exe", *argv]
        with (
            patch.object(launcher, "_exe_dir", return_value=exe_dir),
            patch.object(launcher, "_bundled_node", return_value=None),
            patch.object(launcher, "_ensure_config", return_value=exe_dir / "config.yaml"),
            patch("bosshunter.main.cli") as cli,
        ):
            launcher.main()
        return exe_dir, cli

    def test_frozen_web_redirects_output_to_the_log_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe_dir, cli = self._run_main(tmp, [])
            # 双击（无参数）默认就是 web：输出必须进日志文件。
            self.assertIsInstance(sys.stdout, launcher._TeeOutput)
            self.assertIsInstance(sys.stderr, launcher._TeeOutput)
            log = exe_dir / "data" / "logs" / "jianli-lieshou.log"
            self.assertTrue(log.is_file())
            cli.assert_called_once()
            # sys.argv 被改写成 bosshunter --config ... web。
            self.assertEqual(sys.argv[-1], "web")
            for tee in (sys.stdout, sys.stderr):
                for stream in getattr(tee, "_streams", []):
                    name = getattr(stream, "name", "")
                    if isinstance(name, str) and name.endswith(".log"):
                        try:
                            stream.close()
                        except Exception:
                            pass

    def test_frozen_version_keeps_the_console_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe_dir, cli = self._run_main(tmp, ["--version"])
            # 一次性命令的输出留在控制台上，不进日志文件。
            self.assertIs(sys.stdout, self._old_out)
            self.assertIs(sys.stderr, self._old_err)
            self.assertFalse((exe_dir / "data" / "logs" / "jianli-lieshou.log").exists())
            cli.assert_called_once()


if __name__ == "__main__":
    unittest.main()
