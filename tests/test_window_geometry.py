"""窗口尺寸与位置记忆（v32）：默认几何、恢复、回退、持久化、多屏校验。"""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import bosshunter.app_window as app_window

# 1920x1080 屏的典型工作区（任务栏占 40px）。
WORK_1080P = [(0, 0, 1920, 1040)]
# 双屏：主屏 1920x1080 + 右边一块 2560x1440（工作区 2560x1400）。
WORK_DUAL = [(0, 0, 1920, 1040), (1920, 0, 2560, 1400)]


def _geom(**overrides):
    geom = {"x": 448, "y": 200, "width": 1024, "height": 640}
    geom.update(overrides)
    return geom


class DefaultGeometryTests(unittest.TestCase):
    def test_third_of_work_area_clamped_by_minimum_on_1080p(self):
        # 1920x1040 的 1/3 是 640x346，小于最小 1024x640，被顶上去，
        # 在工作区内居中：x=(1920-1024)//2=448，y=(1040-640)//2=200。
        self.assertEqual(
            app_window.default_window_geometry((0, 0, 1920, 1040)),
            {"x": 448, "y": 200, "width": 1024, "height": 640},
        )

    def test_third_wins_when_screen_is_big_enough(self):
        # 3840x2160（工作区 3840x2120）：1/3 是 1280x706，大于最小值，
        # 就按 1/3 来，居中。
        self.assertEqual(
            app_window.default_window_geometry((0, 0, 3840, 2120)),
            {"x": 1280, "y": 707, "width": 1280, "height": 706},
        )

    def test_small_screen_fills_work_area_instead_of_minimum(self):
        # 800x600 的小屏：最小值放不下，默认几何就是占满工作区，
        # 不能比屏幕还大。
        self.assertEqual(
            app_window.default_window_geometry((0, 0, 800, 600)),
            {"x": 0, "y": 0, "width": 800, "height": 600},
        )

    def test_no_work_area_means_no_default(self):
        self.assertIsNone(app_window.default_window_geometry(None))
        self.assertIsNone(app_window.default_window_geometry((0, 0, 0, 0)))


class ValidateGeometryTests(unittest.TestCase):
    def test_valid_saved_geometry_is_restored(self):
        self.assertEqual(
            app_window.validate_window_geometry(_geom(), WORK_1080P),
            _geom(),
        )

    def test_saved_geometry_on_second_monitor_is_valid(self):
        geom = _geom(x=2100, y=100, width=1400, height=900)
        self.assertEqual(
            app_window.validate_window_geometry(geom, WORK_DUAL),
            geom,
        )

    def test_second_monitor_unplugged_falls_back(self):
        # 上次窗口在右屏（x=2100），现在只剩主屏：可见部分为 0，
        # 校验不过，调用方回退默认几何。
        geom = _geom(x=2100, y=100, width=1400, height=900)
        self.assertIsNone(app_window.validate_window_geometry(geom, WORK_1080P))

    def test_mostly_off_screen_falls_back(self):
        # 只剩 100px 宽还露在屏内（底线是 200px），算跑出屏幕外。
        geom = _geom(x=1820, y=200, width=1024, height=640)
        self.assertIsNone(app_window.validate_window_geometry(geom, WORK_1080P))

    def test_small_visible_sliver_is_enough_to_keep(self):
        # 露出 300x200：用户还抓得到、拖得回，保留并把位置拉回屏内。
        geom = _geom(x=1700, y=200, width=1024, height=640)
        result = app_window.validate_window_geometry(geom, WORK_1080P)
        self.assertIsNotNone(result)
        self.assertEqual(result["width"], 1024)
        self.assertEqual(result["height"], 640)
        self.assertEqual(result["x"], 896)  # 1920-1024，拉回右沿
        self.assertEqual(result["y"], 200)

    def test_too_small_geometry_falls_back_to_default(self):
        # 小于最小尺寸（1024x640）不硬顶，直接回默认几何。
        self.assertIsNone(
            app_window.validate_window_geometry(_geom(width=800, height=500), WORK_1080P)
        )

    def test_oversized_geometry_is_clamped_not_dropped(self):
        # 位置在屏内、尺寸比新屏幕大（分辨率调小了）：尺寸钳进工作区，
        # 位置保留用户意图，不丢记录。
        geom = _geom(x=0, y=0, width=2000, height=1200)
        self.assertEqual(
            app_window.validate_window_geometry(geom, WORK_1080P),
            {"x": 0, "y": 0, "width": 1920, "height": 1040},
        )

    def test_missing_fields_and_wrong_types_are_invalid(self):
        self.assertIsNone(app_window.validate_window_geometry({"x": 1}, WORK_1080P))
        self.assertIsNone(app_window.validate_window_geometry(None, WORK_1080P))
        self.assertIsNone(
            app_window.validate_window_geometry(
                {"x": "448", "y": 200, "width": 1024, "height": 640}, WORK_1080P
            )
        )
        # bool 是 int 的子类，必须当脏数据排除。
        self.assertIsNone(
            app_window.validate_window_geometry(
                {"x": True, "y": 200, "width": 1024, "height": 640}, WORK_1080P
            )
        )

    def test_no_monitor_info_only_checks_size(self):
        # 拿不到各屏工作区（非 Windows）时只校验尺寸，不判位置。
        self.assertEqual(
            app_window.validate_window_geometry(_geom(x=99999, y=99999), None),
            _geom(x=99999, y=99999),
        )


class ResolveGeometryTests(unittest.TestCase):
    def test_no_saved_record_uses_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "window-geometry.json"
            self.assertEqual(
                app_window.resolve_window_geometry(WORK_1080P, path),
                {"x": 448, "y": 200, "width": 1024, "height": 640},
            )

    def test_saved_record_is_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "window-geometry.json"
            app_window.save_window_geometry(path, _geom(x=100, y=80, width=1400, height=900))
            self.assertEqual(
                app_window.resolve_window_geometry(WORK_1080P, path),
                {"x": 100, "y": 80, "width": 1400, "height": 900},
            )

    def test_out_of_range_saved_record_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "window-geometry.json"
            app_window.save_window_geometry(path, _geom(x=5000, y=0))
            self.assertEqual(
                app_window.resolve_window_geometry(WORK_1080P, path),
                {"x": 448, "y": 200, "width": 1024, "height": 640},
            )

    def test_no_work_areas_means_no_geometry(self):
        self.assertIsNone(app_window.resolve_window_geometry([], None))
        with patch("bosshunter.app_window.get_monitor_work_areas", return_value=None):
            self.assertIsNone(app_window.resolve_window_geometry())


class LoadSaveGeometryTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "window-geometry.json"
            self.assertTrue(
                app_window.save_window_geometry(path, _geom(x=100, y=80, width=1400, height=900))
            )
            self.assertEqual(
                app_window.load_window_geometry(path),
                {"x": 100, "y": 80, "width": 1400, "height": 900},
            )

    def test_save_creates_parent_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data" / "window-geometry.json"
            self.assertTrue(app_window.save_window_geometry(path, _geom()))
            self.assertTrue(path.is_file())

    def test_corrupt_file_loads_as_no_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "window-geometry.json"
            path.write_text("{不是 json", encoding="utf-8")
            self.assertIsNone(app_window.load_window_geometry(path))
            # 损坏文件也不影响下次保存（原子写覆盖）。
            self.assertTrue(app_window.save_window_geometry(path, _geom()))
            self.assertEqual(app_window.load_window_geometry(path), _geom())

    def test_missing_fields_load_as_no_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "window-geometry.json"
            path.write_text(json.dumps({"x": 1, "y": 2}), encoding="utf-8")
            self.assertIsNone(app_window.load_window_geometry(path))
            path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
            self.assertIsNone(app_window.load_window_geometry(path))
            path.write_text(json.dumps({"x": 1, "y": 2, "width": "a", "height": 3}), encoding="utf-8")
            self.assertIsNone(app_window.load_window_geometry(path))

    def test_missing_file_loads_as_no_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(
                app_window.load_window_geometry(Path(tmp) / "window-geometry.json")
            )

    def test_save_rejects_bad_geometry_and_missing_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "window-geometry.json"
            self.assertFalse(app_window.save_window_geometry(path, _geom(width=0)))
            self.assertFalse(app_window.save_window_geometry(path, {"x": 1}))
            self.assertFalse(app_window.save_window_geometry(path, None))
            self.assertFalse(path.exists())
        old_path = app_window.get_window_geometry_path()
        try:
            app_window.set_window_geometry_path(None)
            self.assertFalse(app_window.save_window_geometry(None, _geom()))
        finally:
            app_window.set_window_geometry_path(old_path)

    def test_save_is_atomic_no_partial_file(self):
        # 写一半崩掉也不会留下损坏文件：tmp 写完才 os.replace，
        # 读到的要么是完整旧文件、要么是完整新文件。
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "window-geometry.json"
            app_window.save_window_geometry(path, _geom())
            app_window.save_window_geometry(path, _geom(x=10, y=10, width=1200, height=800))
            self.assertEqual(
                app_window.load_window_geometry(path),
                {"x": 10, "y": 10, "width": 1200, "height": 800},
            )
            self.assertFalse((path.with_name(path.name + ".tmp")).exists())


class WindowSizeArgsTests(unittest.TestCase):
    def test_args_for_geometry(self):
        self.assertEqual(
            app_window.window_size_args(_geom()),
            ["--window-size=1024,640", "--window-position=448,200"],
        )

    def test_no_args_without_geometry(self):
        self.assertEqual(app_window.window_size_args(None), [])
        self.assertEqual(app_window.window_size_args({}), [])
        self.assertEqual(app_window.window_size_args(_geom(width=0)), [])


class LaunchWithGeometryTests(unittest.TestCase):
    def setUp(self):
        app_window._APP_WINDOW_PROC = None
        app_window._APP_WINDOW_STARTED_AT = None
        app_window._APP_WINDOW_DEBUG_PORT = None
        app_window._APP_WINDOW_URL = None
        app_window._APP_WINDOW_SPLASH_URL = None
        self._old_path = app_window.get_window_geometry_path()

    def tearDown(self):
        app_window._APP_WINDOW_PROC = None
        app_window._APP_WINDOW_STARTED_AT = None
        app_window._APP_WINDOW_DEBUG_PORT = None
        app_window._APP_WINDOW_URL = None
        app_window._APP_WINDOW_SPLASH_URL = None
        app_window.set_window_geometry_path(self._old_path)

    def _launch(self, popen):
        proc = Mock()
        popen.return_value = proc
        with (
            patch("bosshunter.app_window.find_app_browser", return_value="C:/Chrome/chrome.exe"),
            patch("bosshunter.browser.runtime.pick_chrome_debug_port", return_value=None),
        ):
            return app_window.launch_app_window("http://127.0.0.1:8686")

    @patch("bosshunter.app_window.subprocess.Popen")
    def test_saved_geometry_goes_into_launch_args(self, popen):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "window-geometry.json"
            app_window.save_window_geometry(path, _geom(x=100, y=80, width=1400, height=900))
            app_window.set_window_geometry_path(path)
            with patch(
                "bosshunter.app_window.get_monitor_work_areas", return_value=WORK_1080P
            ):
                self._launch(popen)
        argv = popen.call_args.args[0]
        self.assertIn("--window-size=1400,900", argv)
        self.assertIn("--window-position=100,80", argv)
        # 尺寸参数放在 --app 之前。
        self.assertLess(argv.index("--window-size=1400,900"), argv.index("--app=http://127.0.0.1:8686"))

    @patch("bosshunter.app_window.subprocess.Popen")
    def test_no_saved_record_uses_default_args(self, popen):
        with tempfile.TemporaryDirectory() as tmp:
            app_window.set_window_geometry_path(Path(tmp) / "window-geometry.json")
            with patch(
                "bosshunter.app_window.get_monitor_work_areas", return_value=WORK_1080P
            ):
                self._launch(popen)
        argv = popen.call_args.args[0]
        self.assertIn("--window-size=1024,640", argv)
        self.assertIn("--window-position=448,200", argv)

    @patch("bosshunter.app_window.subprocess.Popen")
    def test_no_work_area_means_no_size_args(self, popen):
        # 非 Windows：拿不到工作区，不加参数，走 Chrome 默认（旧行为）。
        with patch("bosshunter.app_window.get_monitor_work_areas", return_value=None):
            self._launch(popen)
        argv = popen.call_args.args[0]
        self.assertEqual(argv, ["C:/Chrome/chrome.exe", "--app=http://127.0.0.1:8686"])


class SaveCurrentWindowGeometryTests(unittest.TestCase):
    def test_saves_queried_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "window-geometry.json"
            self.assertTrue(
                app_window.save_current_window_geometry(
                    path, _query_bounds=lambda: (100, 80, 1400, 900)
                )
            )
            self.assertEqual(
                app_window.load_window_geometry(path),
                {"x": 100, "y": 80, "width": 1400, "height": 900},
            )

    def test_no_window_means_no_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "window-geometry.json"
            self.assertFalse(
                app_window.save_current_window_geometry(path, _query_bounds=lambda: None)
            )
            self.assertFalse(path.exists())

    def test_degenerate_bounds_are_not_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "window-geometry.json"
            self.assertFalse(
                app_window.save_current_window_geometry(
                    path, _query_bounds=lambda: (0, 0, 0, 0)
                )
            )
            self.assertFalse(path.exists())

    def test_query_failure_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "window-geometry.json"

            def _boom():
                raise RuntimeError("EnumWindows 炸了")

            self.assertFalse(
                app_window.save_current_window_geometry(path, _query_bounds=_boom)
            )
            self.assertFalse(path.exists())


class WatchPeriodicSaveTests(unittest.TestCase):
    @staticmethod
    def _fake_clock(times):
        it = iter(times)

        def _clock():
            try:
                return next(it)
            except StopIteration:
                return times[-1]

        return _clock

    def test_watch_saves_bounds_periodically_and_only_when_changed(self):
        # 监视循环每 5 秒问一次窗口外框：变了才存，没变不重复写。
        proc = Mock()
        proc.poll.return_value = None
        saved = []
        bounds = iter([(100, 80, 1400, 900), (100, 80, 1400, 900), (200, 100, 1500, 950)])
        tab_lists = iter([
            ["http://127.0.0.1:8686/"],
            ["http://127.0.0.1:8686/"],
            ["http://127.0.0.1:8686/"],
            ["http://127.0.0.1:8686/"],
            ["http://127.0.0.1:8686/"],
            [],
        ])
        fired = []
        result = app_window.watch_app_window_close(
            lambda: fired.append(True),
            grace_seconds=5.0,
            poll_interval=0.01,
            wait_for_proc_seconds=30.0,
            _get_proc=lambda: proc,
            _started_at=100.0,
            _is_update_applying=lambda: False,
            _clock=self._fake_clock([100.0, 100.0, 100.0, 106.0, 106.0, 112.0, 112.0, 112.0]),
            _sleep=lambda _: None,
            _panel_url="http://127.0.0.1:8686",
            _list_tabs=lambda: next(tab_lists),
            _service_alive=lambda: True,
            _get_window_bounds=lambda: next(bounds),
            _save_geometry=lambda b: saved.append(b) or True,
            _geometry_save_interval=5.0,
        )
        self.assertTrue(result)
        self.assertEqual(fired, [True])
        # 第 1 次（t=100）存 1400x900；t=106 没变不存；
        # t=112 变了存 1500x950。关窗本身照常触发。
        self.assertEqual(saved, [(100, 80, 1400, 900), (200, 100, 1500, 950)])

    def test_watch_without_saver_does_not_query_window(self):
        # 没配几何路径、没注入保存器时，监视循环不问窗口外框，
        # 关窗行为与以前完全一致。run_server 测试会把全局几何路径
        # 设成 DATA_DIR 下的真实路径，这里显式清掉再测。
        old_path = app_window.get_window_geometry_path()
        app_window.set_window_geometry_path(None)
        try:
            proc = Mock()
            proc.poll.return_value = None
            queried = []
            tab_lists = iter([["http://127.0.0.1:8686/"], []])
            fired = []
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
                _service_alive=lambda: True,
                _get_window_bounds=lambda: queried.append(True) or (100, 80, 1400, 900),
            )
            self.assertTrue(result)
            self.assertEqual(fired, [True])
            self.assertEqual(queried, [])
        finally:
            app_window.set_window_geometry_path(old_path)


class GeometryPathTests(unittest.TestCase):
    def test_set_and_get_path(self):
        old = app_window.get_window_geometry_path()
        try:
            app_window.set_window_geometry_path("/tmp/x/window-geometry.json")
            self.assertEqual(
                app_window.get_window_geometry_path(), Path("/tmp/x/window-geometry.json")
            )
            app_window.set_window_geometry_path(None)
            self.assertIsNone(app_window.get_window_geometry_path())
        finally:
            app_window.set_window_geometry_path(old)


if __name__ == "__main__":
    unittest.main()
