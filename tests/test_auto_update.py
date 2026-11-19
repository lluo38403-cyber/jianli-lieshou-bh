"""Self-update (auto-update) tests.

Covers: version compare, relay version check, zip download + sha256
verify, safe extraction, backup/overlay that never touches config.yaml
or data/, the Windows batch file contents, and the /api/update routes.

The batch file itself is Windows-only: tests assert what it contains
(wait for pid, robocopy with /XD data /XF config.yaml, relaunch, cleanup)
but never execute it. The real file swap can only be proven on a real
Windows machine (see the verification report).
"""

import hashlib
import io
import json
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from bosshunter import __version__, updater
from bosshunter.updater import (
    UPDATE_FAILURE_MESSAGES,
    UpdateError,
    apply_update,
    build_update_script,
    check_for_update,
    clear_update_result,
    consume_pending_update,
    create_backup,
    download_update,
    extract_update,
    find_payload_root,
    get_last_update_failure,
    get_status,
    is_newer,
    overlay_staging,
    parse_version,
    prune_old_backups,
    prune_stale_files,
    reconcile_pending_update,
    run_check,
    start_download,
    validate_staged_payload,
    write_pending_update,
    write_update_result,
)
from bosshunter.web import server


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
    })


class VersionCompareTests(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(parse_version("1.0.1"), (1, 0, 1))
        self.assertEqual(parse_version("v1.2"), (1, 2))
        self.assertEqual(parse_version("1.0.1-rc1"), (1, 0, 1))
        self.assertEqual(parse_version("  1.10.0 "), (1, 10, 0))
        self.assertIsNone(parse_version(""))
        self.assertIsNone(parse_version("abc"))
        self.assertIsNone(parse_version("1.0.x"))
        self.assertIsNone(parse_version("1..2"))
        self.assertIsNone(parse_version(None))
        self.assertIsNone(parse_version(101))

    def test_is_newer_numeric_per_part(self):
        cases = [
            ("1.0.0", "1.0.1", True),
            ("1.0.0", "1.0.0", False),
            ("1.0.1", "1.0.0", False),
            ("1.0.9", "1.10.0", True),
            ("1.10.0", "1.0.9", False),
            ("v1.2.3", "1.2.4", True),
            ("1.0", "1.0.1", True),
            ("1.0.0", "1.0.0.1", True),
            ("1.0.0", "2.0.0", True),
            ("abc", "1.0.0", False),
            ("1.0.0", "", False),
            ("1.0.0", "1.0.x", False),
            ("", "1.0.0", False),
            (None, "1.0.0", False),
        ]
        for current, latest, expected in cases:
            with self.subTest(current=current, latest=latest):
                self.assertIs(is_newer(current, latest), expected)


class UpdateStubHandler(BaseHTTPRequestHandler):
    """Stub for the relay update contract: /api/version + /download/latest."""

    version_payload = {"version": "1.1.4", "size": 0, "sha256": "0" * 64, "notes": ""}
    zip_bytes = b""
    status = 200
    requests: list = []

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self):
        return self.headers.get("X-App-Token") == "test-app-token"

    def do_GET(self):
        type(self).requests.append(("GET", self.path, dict(self.headers)))
        if not self._auth_ok():
            return self._send_json(401, {"error": "unauthorized"})
        if self.path == "/api/version":
            if type(self).status != 200:
                return self._send_json(type(self).status, {"error": "boom"})
            return self._send_json(200, type(self).version_payload)
        if self.path == "/download/latest":
            if type(self).status != 200:
                return self._send_json(type(self).status, {"error": "boom"})
            data = type(self).zip_bytes
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        return self._send_json(404, {"error": "not found"})

    def log_message(self, *args):
        pass


def _make_zip(top_folder=True):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        prefix = "简历猎手-windows/" if top_folder else ""
        zf.writestr(prefix + "简历猎手.exe", b"new-exe")
        zf.writestr(prefix + "_internal/app.bin", b"new-app")
        zf.writestr(prefix + "config.yaml", b"packaged-default: true\n")
        zf.writestr(prefix + "data/should-not-appear.db", b"nope")
    return buf.getvalue()


class UpdateCheckTests(unittest.TestCase):
    def setUp(self):
        _reset_update_state()
        UpdateStubHandler.version_payload = {
            "version": "1.1.4",
            "size": 123,
            "sha256": hashlib.sha256(_make_zip()).hexdigest(),
            "notes": "更快更稳",
        }
        UpdateStubHandler.zip_bytes = _make_zip()
        UpdateStubHandler.status = 200
        UpdateStubHandler.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), UpdateStubHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        _reset_update_state()

    def _config(self, **ai_overrides):
        ai = {
            "service": "relay",
            "provider": "relay",
            "relay_url": f"http://127.0.0.1:{self.port}",
            "relay_token": "test-app-token",
        }
        ai.update(ai_overrides)
        return {"ai": ai}

    def test_newer_version_returns_info(self):
        info = check_for_update(self._config())
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info["version"], "1.1.4")
        self.assertEqual(info["current_version"], __version__)
        self.assertEqual(info["size"], 123)
        self.assertEqual(info["notes"], "更快更稳")
        self.assertTrue(info["update_available"])
        token_headers = [r[2].get("X-App-Token") for r in UpdateStubHandler.requests]
        self.assertIn("test-app-token", token_headers)

    def test_same_or_older_version_is_no_update(self):
        for version in (__version__, "1.0.0", "0.9.9"):
            with self.subTest(version=version):
                UpdateStubHandler.version_payload = {
                    "version": version,
                    "size": 1,
                    "sha256": "a" * 64,
                    "notes": "",
                }
                self.assertIsNone(check_for_update(self._config()))

    def test_404_means_no_release(self):
        UpdateStubHandler.status = 404
        self.assertIsNone(check_for_update(self._config()))

    def test_500_and_bad_payload_are_silent_no_update(self):
        UpdateStubHandler.status = 500
        self.assertIsNone(check_for_update(self._config()))
        UpdateStubHandler.status = 200
        for bad in (
            {"size": 1, "sha256": "a" * 64},
            {"version": "1.1.4", "size": 1},
            {"version": "1.1.4", "size": 1, "sha256": "not-a-hash"},
            {"version": "1.1.4", "size": 1, "sha256": "a" * 64, "notes": 5},
        ):
            with self.subTest(bad=bad):
                UpdateStubHandler.version_payload = bad
                if "notes" in bad and not isinstance(bad.get("notes"), str):
                    # notes 非字符串时按空处理，仍算有新版
                    self.assertIsNotNone(check_for_update(self._config()))
                else:
                    self.assertIsNone(check_for_update(self._config()))

    def test_no_token_means_no_check_and_no_request(self):
        self.assertIsNone(check_for_update(self._config(relay_token="")))
        self.assertEqual(UpdateStubHandler.requests, [])

    def test_wrong_token_is_silent_no_update(self):
        self.assertIsNone(check_for_update(self._config(relay_token="wrong")))
        self.assertIsNone(check_for_update(self._config(relay_url="http://127.0.0.1:1")))

    def test_run_check_caches_and_force_refreshes(self):
        config = self._config()
        first = run_check(config)
        self.assertTrue(first["update_available"])
        calls_after_first = len(UpdateStubHandler.requests)
        second = run_check(config)
        self.assertTrue(second["update_available"])
        self.assertEqual(len(UpdateStubHandler.requests), calls_after_first)
        run_check(config, force=True)
        self.assertGreater(len(UpdateStubHandler.requests), calls_after_first)


class UpdateDownloadTests(unittest.TestCase):
    def setUp(self):
        _reset_update_state()
        self.zip_bytes = _make_zip()
        UpdateStubHandler.zip_bytes = self.zip_bytes
        UpdateStubHandler.status = 200
        UpdateStubHandler.requests = []
        UpdateStubHandler.version_payload = {
            "version": "1.1.4",
            "size": len(self.zip_bytes),
            "sha256": hashlib.sha256(self.zip_bytes).hexdigest(),
            "notes": "",
        }
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), UpdateStubHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        _reset_update_state()

    def _config(self, **ai_overrides):
        ai = {
            "service": "relay",
            "provider": "relay",
            "relay_url": f"http://127.0.0.1:{self.port}",
            "relay_token": "test-app-token",
        }
        ai.update(ai_overrides)
        return {"ai": ai}

    def _info(self):
        return {
            "version": "1.1.4",
            "size": len(self.zip_bytes),
            "sha256": hashlib.sha256(self.zip_bytes).hexdigest(),
            "notes": "",
        }

    def test_download_verifies_sha256_and_reports_progress(self):
        seen: list[tuple[int, int]] = []
        with tempfile.TemporaryDirectory() as tmp:
            path = download_update(
                self._config(), self._info(), tmp,
                progress_cb=lambda done, total: seen.append((done, total)),
            )
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), self.zip_bytes)
            self.assertTrue(seen)
            self.assertEqual(seen[-1][0], len(self.zip_bytes))
            self.assertEqual(seen[-1][1], len(self.zip_bytes))

    def test_sha_mismatch_aborts_and_removes_file(self):
        bad = dict(self._info(), sha256="b" * 64)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(UpdateError) as caught:
                download_update(self._config(), bad, tmp)
            self.assertIn("对不上", str(caught.exception))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_download_404_and_unreachable_raise_plain_error(self):
        UpdateStubHandler.status = 404
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(UpdateError):
                download_update(self._config(), self._info(), tmp)
            self.assertEqual(list(Path(tmp).iterdir()), [])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(UpdateError):
                download_update(self._config(relay_url="http://127.0.0.1:1"), self._info(), tmp)

    def test_extract_rejects_zip_slip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../evil.txt", b"evil")
            zf.writestr("ok.txt", b"ok")
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "bad.zip"
            zip_path.write_bytes(buf.getvalue())
            staging = Path(tmp) / "staging"
            with self.assertRaises(UpdateError):
                extract_update(zip_path, staging)
            self.assertFalse((Path(tmp) / "evil.txt").exists())

    def test_extract_and_find_payload_root(self):
        for top_folder in (True, False):
            with self.subTest(top_folder=top_folder):
                with tempfile.TemporaryDirectory() as tmp:
                    zip_path = Path(tmp) / "rel.zip"
                    zip_path.write_bytes(_make_zip(top_folder=top_folder))
                    staging = extract_update(zip_path, Path(tmp) / "staging")
                    root = find_payload_root(staging)
                    self.assertTrue((root / "简历猎手.exe").is_file())
                    self.assertTrue((root / "_internal" / "app.bin").is_file())

    def test_double_nested_zip_still_finds_inner_root(self):
        # 回归：zip 里多套了一层文件夹时，旧实现只找一层，root 里
        # 既无 exe 也无 _internal，换上去的还是旧版、一直弹更新。
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("简历猎手-windows/简历猎手-windows/简历猎手.exe", b"new-exe")
            zf.writestr("简历猎手-windows/简历猎手-windows/_internal/app.bin", b"new-app")
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "rel.zip"
            zip_path.write_bytes(buf.getvalue())
            staging = extract_update(zip_path, Path(tmp) / "staging")
            root = find_payload_root(staging)
            self.assertTrue((root / "简历猎手.exe").is_file())
            self.assertTrue((root / "_internal" / "app.bin").is_file())
            validate_staged_payload(root)  # 不抛错

    def test_split_layout_is_rejected(self):
        # 回归：exe 和 _internal 分在两个文件夹时，旧实现只认 exe
        # 所在目录，_internal 就没换上去（新 exe + 旧 _internal）。
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("简历猎手-windows/sub/简历猎手.exe", b"new-exe")
            zf.writestr("简历猎手-windows/_internal/app.bin", b"new-app")
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "rel.zip"
            zip_path.write_bytes(buf.getvalue())
            staging = extract_update(zip_path, Path(tmp) / "staging")
            root = find_payload_root(staging)
            with self.assertRaises(UpdateError):
                validate_staged_payload(root)

    def test_bad_layout_zip_never_reaches_ready(self):
        # 回归：zip 里根本没有 exe 时，旧实现照样标 ready，点了更新
        # 等于白换一遍、版本不变、一直弹更新。
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("简历猎手-windows/readme.txt", b"hi")
        bad = buf.getvalue()
        UpdateStubHandler.zip_bytes = bad
        UpdateStubHandler.version_payload = {
            "version": "1.1.4",
            "size": len(bad),
            "sha256": hashlib.sha256(bad).hexdigest(),
            "notes": "",
        }
        _reset_update_state()
        run_check(self._config())
        start_download(self._config())
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if get_status()["status"] == "error":
                break
            time.sleep(0.2)
        state = get_status()
        self.assertEqual(state["status"], "error", state)
        self.assertIn("安装包", state["error"])


class UpdatePreserveTests(unittest.TestCase):
    def _fake_install(self, base: Path) -> Path:
        install = base / "install"
        (install / "_internal").mkdir(parents=True)
        (install / "data").mkdir(parents=True)
        (install / "简历猎手.exe").write_bytes(b"old-exe")
        (install / "_internal" / "app.bin").write_bytes(b"old-app")
        (install / "config.yaml").write_bytes(b"user: true\nsalary: 20\n")
        (install / "data" / "bosshunter.db").write_bytes(b"user-db-bytes")
        (install / "other.txt").write_bytes(b"keep-me")
        return install

    def _fake_staging(self, base: Path) -> Path:
        staging = base / "staging"
        (staging / "_internal").mkdir(parents=True)
        (staging / "data").mkdir(parents=True)
        (staging / "简历猎手.exe").write_bytes(b"new-exe")
        (staging / "_internal" / "app.bin").write_bytes(b"new-app")
        (staging / "config.yaml").write_bytes(b"packaged-default: true\n")
        (staging / "data" / "should-not-appear.db").write_bytes(b"nope")
        return staging

    def test_overlay_replaces_payload_but_keeps_config_and_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            install = self._fake_install(base)
            staging = self._fake_staging(base)
            overlay_staging(install, staging)
            self.assertEqual((install / "简历猎手.exe").read_bytes(), b"new-exe")
            self.assertEqual((install / "_internal" / "app.bin").read_bytes(), b"new-app")
            self.assertEqual((install / "config.yaml").read_bytes(), b"user: true\nsalary: 20\n")
            self.assertEqual((install / "data" / "bosshunter.db").read_bytes(), b"user-db-bytes")
            self.assertFalse((install / "data" / "should-not-appear.db").exists())
            self.assertEqual((install / "other.txt").read_bytes(), b"keep-me")

    def test_backup_excludes_config_data_and_old_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            install = self._fake_install(base)
            old_backup = install / "update-backup-1.0.0"
            old_backup.mkdir()
            (old_backup / "stale.txt").write_text("stale", encoding="utf-8")
            backup = create_backup(install, install / "update-backup-1.0.1")
            self.assertEqual((backup / "简历猎手.exe").read_bytes(), b"old-exe")
            self.assertEqual((backup / "_internal" / "app.bin").read_bytes(), b"old-app")
            self.assertFalse((backup / "config.yaml").exists())
            self.assertFalse((backup / "data").exists())
            self.assertFalse((backup / "update-backup-1.0.0").exists())
            self.assertEqual((backup / "other.txt").read_bytes(), b"keep-me")

    def test_overlay_purges_stale_files_from_internal(self):
        # 回归：旧版 _internal 里新版已删的文件，旧 overlay 会留下来，
        # 旧代码可能继续被加载，版本号/行为还是旧的。
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            install = self._fake_install(base)
            (install / "_internal" / "removed-in-new.dll").write_bytes(b"stale")
            (install / "_internal" / "old-sub").mkdir(exist_ok=True)
            (install / "_internal" / "old-sub" / "gone.bin").write_bytes(b"stale")
            staging = self._fake_staging(base)
            overlay_staging(install, staging)
            self.assertFalse((install / "_internal" / "removed-in-new.dll").exists())
            self.assertFalse((install / "_internal" / "old-sub").exists())
            self.assertEqual((install / "_internal" / "app.bin").read_bytes(), b"new-app")
            # 顶层用户自己的文件不动；设置和数据不动
            self.assertEqual((install / "other.txt").read_bytes(), b"keep-me")
            self.assertEqual((install / "config.yaml").read_bytes(), b"user: true\nsalary: 20\n")
            self.assertEqual((install / "data" / "bosshunter.db").read_bytes(), b"user-db-bytes")

    def test_prune_stale_files_keeps_shared_subtrees(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            src = base / "src"
            dst = base / "dst"
            (src / "keep").mkdir(parents=True)
            (src / "keep" / "a.txt").write_text("a", encoding="utf-8")
            (dst / "keep").mkdir(parents=True)
            (dst / "keep" / "a.txt").write_text("a", encoding="utf-8")
            (dst / "keep" / "stale.txt").write_text("s", encoding="utf-8")
            (dst / "gone-dir").mkdir()
            prune_stale_files(src, dst)
            self.assertTrue((dst / "keep" / "a.txt").is_file())
            self.assertFalse((dst / "keep" / "stale.txt").exists())
            self.assertFalse((dst / "gone-dir").exists())


class StagedPayloadValidationTests(unittest.TestCase):
    def _root(self, base: Path, exe=True, internal=True) -> Path:
        root = base / "payload"
        root.mkdir(parents=True, exist_ok=True)
        if exe:
            (root / "简历猎手.exe").write_bytes(b"exe")
        if internal:
            (root / "_internal").mkdir(exist_ok=True)
            (root / "_internal" / "app.bin").write_bytes(b"app")
        return root

    def test_good_payload_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(validate_staged_payload(self._root(Path(tmp))), Path(tmp) / "payload")

    def test_missing_exe_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(UpdateError) as caught:
                validate_staged_payload(self._root(Path(tmp), exe=False))
            self.assertIn("安装包", str(caught.exception))

    def test_missing_internal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(UpdateError) as caught:
                validate_staged_payload(self._root(Path(tmp), internal=False))
            self.assertIn("安装包", str(caught.exception))


class UpdatePendingTests(unittest.TestCase):
    def setUp(self):
        _reset_update_state()

    def tearDown(self):
        _reset_update_state()

    def test_consume_ok_when_version_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            write_pending_update(install, __version__)
            write_update_result(install, {"status": "applied", "expected_version": __version__, "reason_code": ""})
            verdict = consume_pending_update(install)
            assert verdict is not None
            self.assertEqual(verdict["status"], "ok")
            self.assertEqual(verdict["actual_version"], __version__)
            self.assertFalse((install / updater.UPDATE_PENDING_FILENAME).exists())
            self.assertIsNone(get_last_update_failure(install))
            self.assertFalse(reconcile_pending_update(install))

    def test_consume_mismatch_reports_failure_not_a_prompt(self):
        # 回归：换完打开还是旧版本时，旧实现毫无记录，启动检查立刻
        # 又弹同一个更新。核对后必须进 error 状态、不再自动检查。
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            write_pending_update(install, "9.9.9")
            write_update_result(install, {"status": "applied", "expected_version": "9.9.9", "reason_code": ""})
            verdict = consume_pending_update(install)
            assert verdict is not None
            self.assertEqual(verdict["status"], "failed")
            self.assertIn("旧版本", verdict["reason"])
            self.assertFalse((install / updater.UPDATE_PENDING_FILENAME).exists())
            last = get_last_update_failure(install)
            assert last is not None
            self.assertEqual(last["expected_version"], "9.9.9")
            self.assertTrue(reconcile_pending_update(install))
            state = get_status()
            self.assertEqual(state["status"], "error")
            self.assertFalse(state["update_available"])
            self.assertIn("旧版本", state["error"])
            # 失败记录在，后续启动核对仍挡住自动检查（不进循环）
            self.assertTrue(reconcile_pending_update(install))

    def test_consume_bat_failure_maps_reason_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            write_pending_update(install, "9.9.9")
            write_update_result(install, {"status": "failed", "expected_version": "9.9.9", "reason_code": "copy"})
            verdict = consume_pending_update(install)
            assert verdict is not None
            self.assertEqual(verdict["status"], "failed")
            self.assertEqual(verdict["reason"], UPDATE_FAILURE_MESSAGES["copy"])

    def test_consume_without_bat_result_is_unknown_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            write_pending_update(install, "9.9.9")
            verdict = consume_pending_update(install)
            assert verdict is not None
            self.assertEqual(verdict["status"], "failed")
            self.assertEqual(verdict["reason"], UPDATE_FAILURE_MESSAGES["unknown"])

    def test_stale_applied_result_is_not_misread_as_this_round(self):
        # 上轮换成的结果文件，不能被这轮没跑起来的 bat 误读：
        # pending 是新的、结果文件是旧的时，按“没换完”算。
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            write_update_result(install, {"status": "applied", "expected_version": __version__, "reason_code": ""})
            write_pending_update(install, "9.9.9")
            # apply_update 会先清结果文件；这里模拟清掉后的状态
            from bosshunter.updater import _remove_quietly
            _remove_quietly(install / updater.UPDATE_RESULT_FILENAME)
            verdict = consume_pending_update(install)
            assert verdict is not None
            self.assertEqual(verdict["status"], "failed")
            self.assertEqual(verdict["reason"], UPDATE_FAILURE_MESSAGES["unknown"])

    def test_manual_recheck_clears_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            write_pending_update(install, "9.9.9")
            write_update_result(install, {"status": "failed", "expected_version": "9.9.9", "reason_code": "stuck"})
            consume_pending_update(install)
            self.assertIsNotNone(get_last_update_failure(install))
            clear_update_result(install)
            self.assertIsNone(get_last_update_failure(install))
            self.assertFalse(reconcile_pending_update(install))

    def test_no_marker_means_nothing_to_reconcile(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(consume_pending_update(Path(tmp)))
            self.assertFalse(reconcile_pending_update(Path(tmp)))


class UpdatePreservePruneTests(unittest.TestCase):
    def test_prune_old_backups_keeps_newest(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name in ("update-backup-1.0.0", "update-backup-1.0.1", "update-backup-1.0.2"):
                (base / name).mkdir()
            prune_old_backups(base, keep=1)
            remaining = sorted(p.name for p in base.iterdir())
            self.assertEqual(remaining, ["update-backup-1.0.2"])


class UpdateScriptTests(unittest.TestCase):
    def test_batch_waits_for_pid_and_preserves_user_files(self):
        script = build_update_script()
        self.assertIn("PID", script)
        self.assertIn("robocopy", script)
        self.assertIn("/XD data", script)
        self.assertIn("/XF config.yaml", script)
        self.assertIn("rmdir /s /q", script)
        self.assertIn('start ""', script)
        self.assertIn("update.log", script)
        self.assertIn('del "%~f0"', script)
        # 批处理里绝不能出现会删用户数据的命令
        self.assertNotIn("del config.yaml", script)
        self.assertNotIn("rmdir /s /q \"%INSTALL_DIR%\"", script)
        self.assertNotIn("format", script.lower())

    def test_batch_kills_stuck_process_tree_and_other_instances(self):
        # 回归：旧 bat 只等主进程 pid；残留/多开的同名进程占住文件时
        # robocopy 直接半路失败。超时后要 taskkill 整棵进程树，换
        # 之前还要清掉其他同名进程。
        script = build_update_script()
        self.assertIn("taskkill /PID %PID% /T /F", script)
        self.assertIn('taskkill /F /IM "%EXE_NAME%" /T', script)

    def test_batch_waits_with_ping_not_timeout(self):
        # 回归：timeout /t N /nobreak 在 detached、无控制台的进程里
        # 不返回，bat 卡在等主进程退出的循环里走不到 robocopy
        # （CI 更新全流程测试两次复现；与 lucky 在无影云电脑上
        # v10 点更新的故障现象一致）。等待必须用 ping 延时。
        script = build_update_script()
        self.assertNotIn("timeout /t", script.lower())
        self.assertIn("ping -n 2 127.0.0.1 >nul", script)
        self.assertIn("ping -n 6 127.0.0.1 >nul", script)
        self.assertIn("ping -n 4 127.0.0.1 >nul", script)

    def test_batch_has_no_findstr_pipe(self):
        # 回归：tasklist | findstr 管道在全流程测试里三次卡死在
        # restart（run 44590350853、44612674852、44615545961）。
        # 诊断重跑抓到 bat 的 cmd.exe CPU 只有 0.03 秒、子进程
        # findstr.exe（findstr /I /C:"简历猎手.exe"）还活着：
        # tasklist 早退出了，findstr 还在等管道输入不返回。bat 里
        # 不许再出现 findstr、tasklist 或任何管道符。
        script = build_update_script()
        self.assertNotIn("findstr", script.lower())
        self.assertNotIn("tasklist", script.lower())
        self.assertNotIn("|", script)

    def test_batch_waits_for_pid_and_imagename_together(self):
        # 回归：只按 PID 等，Windows 把退出进程的 PID 很快分给别
        # 的进程后循环一直看到“还在”而永远不退出（v12→v13 全流程
        # 测试 run 44590350853 复现：PID 2684 被重用，循环 184 秒
        # 没退出，update.log 只有第一行，连 :killtree 都没走到）。
        # v14 的 PID+进程名双重匹配修法本身没错，但它还走 findstr
        # 管道，所以三次失败都是 findstr 卡死、与 PID 重用无关。
        # 现在等待判断改用 PowerShell 直查：Get-Process -Id 按
        # PID+进程名一起匹配（:waitloop 和 :killtree 重查两处），
        # 进程名在 PowerShell 里去掉 .exe 后缀再比对（bat 文件是
        # UTF-8 无 BOM，字面中文会被 cmd 按本地代码页读坏；名字
        # 来自启动参数 %EXE_NAME%，Unicode 直传）。
        script = build_update_script()
        pid_probes = [
            line
            for line in script.splitlines()
            if "Get-Process -Id %PID%" in line
        ]
        self.assertGreaterEqual(len(pid_probes), 2)
        for line in pid_probes:
            self.assertIn("powershell -NoProfile -NonInteractive", line)
            self.assertIn(
                "[System.IO.Path]::GetFileNameWithoutExtension('%EXE_NAME%')",
                line,
            )
            self.assertIn("<nul", line)
        # 只按名查的三处（:procsweep 两次、:fail_write 一次）也
        # 必须走同一套 PowerShell 探针，不许回退到管道。
        name_probes = [
            line
            for line in script.splitlines()
            if "Get-Process -Name" in line
        ]
        self.assertGreaterEqual(len(name_probes), 3)
        for line in name_probes:
            self.assertIn("powershell -NoProfile -NonInteractive", line)
            self.assertIn("<nul", line)

    def test_batch_wait_cap_fits_restart_window(self):
        # 回归：上限 120 次、每次 tasklist + ping 约 2–4 秒，最坏
        # 要 4–8 分钟，远超全流程测试 180 秒的 restart 窗口——
        # 即使 PID 没被重用也走不完。60 次约一到两分钟，之后强
        # 制结束、换文件、重开仍能落在 180 秒窗口里。
        script = build_update_script()
        self.assertIn("if %WAITED% geq 60 goto :killtree", script)
        self.assertNotIn("geq 120", script)
        self.assertNotIn("等了 120 秒", script)

    def test_batch_pid_reuse_does_not_block_wait(self):
        # 复现 v12→v13 测试的故障场景：旧进程（PID 2684）已退出，
        # Windows 把 2684 分给了一个无关进程。按 PID+进程名双重
        # 匹配建模两版等待条件（沙箱跑不了真 bat，模型只用来说明
        # 双重匹配为什么能修好它；机制本身由
        # test_batch_waits_for_pid_and_imagename_together 钉在
        # 生成的 bat 上——现在是 PowerShell Get-Process 探针，
        # 不再是 tasklist 双重过滤；v14 的双重过滤修法本身没错，
        # 只是它还走 findstr 管道才没起作用）。
        def old_condition(processes, pid):
            # 旧 bat：tasklist 只按 PID 过滤，findstr 只认 PID 串。
            return any(p == pid for _, p in processes)

        def new_condition(processes, pid, exe):
            # 新 bat：PowerShell 按 PID 和进程名一起查。
            return any(
                p == pid and name.lower() == exe.lower()
                for name, p in processes
            )

        exe = "简历猎手.exe"
        target = 2684
        still_running = [(exe, 2684), ("conhost.exe", 3000)]
        pid_reused = [("conhost.exe", 2684), ("explorer.exe", 1234)]
        gone_clean = [("explorer.exe", 1234)]
        # 主进程还在跑：两版都该继续等。
        self.assertTrue(old_condition(still_running, target))
        self.assertTrue(new_condition(still_running, target, exe))
        # PID 被重用：旧版误判“还在”一直等（故障本身），新版
        # 立刻退出循环去换文件。
        self.assertTrue(old_condition(pid_reused, target))
        self.assertFalse(new_condition(pid_reused, target, exe))
        # 正常退出、无重用：两版都退出循环。
        self.assertFalse(old_condition(gone_clean, target))
        self.assertFalse(new_condition(gone_clean, target, exe))

    def test_batch_never_exits_silently(self):
        # 回归：旧 bat 等 120 秒超时 / 复制失败时直接 goto :eof，
        # 不写结果、不重启旧版，用户重开又弹更新。每个失败出口都
        # 必须写 update-result.json 并尝试重开旧版。
        script = build_update_script()
        self.assertNotIn("goto :eof", script)
        self.assertIn("update-result.json", script)
        self.assertIn(":fail_stuck", script)
        self.assertIn(":fail_nopkg", script)
        self.assertIn(":fail_copy", script)
        self.assertIn(":rollback", script)
        self.assertIn("reason_code", script)

    def test_batch_validates_staging_and_purges_stale_internal(self):
        # 回归：旧 bat 不校验 staging、有错照换；且 robocopy /E /IS
        # 从不清残留，旧版删掉的文件一直留在 _internal 里。
        script = build_update_script()
        self.assertIn('if not exist "%STAGING%\\%EXE_NAME%"', script)
        self.assertIn('if not exist "%STAGING%\\_internal\\"', script)
        self.assertIn('robocopy "%STAGING%\\_internal" "%INSTALL_DIR%_internal" /MIR', script)
        self.assertIn("/R:5 /W:3", script)

    def test_batch_restores_backup_on_copy_failure(self):
        # 回归：旧 bat 复制失败就扔下半新半旧的安装目录走人。
        # 现在必须从备份恢复（包括 _internal 的 /MIR 还原），再报错。
        script = build_update_script()
        self.assertIn('robocopy "%INSTALL_DIR%%BACKUP%" "%INSTALL_DIR%"', script)
        self.assertIn('"%INSTALL_DIR%%BACKUP%\\_internal"', script)
        self.assertIn("%EXPECTED%", script)


class UpdateApplyTests(unittest.TestCase):
    def setUp(self):
        _reset_update_state()

    def tearDown(self):
        _reset_update_state()

    def test_apply_refuses_outside_windows(self):
        import sys

        if sys.platform.startswith("win"):
            self.skipTest("仅在非 Windows 上验证拒绝逻辑")
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "staging"
            (staging / "_internal").mkdir(parents=True)
            (staging / "简历猎手.exe").write_bytes(b"new-exe")
            (staging / "_internal" / "app.bin").write_bytes(b"new-app")
            updater._STATE.update({
                "status": "ready",
                "staging_dir": str(staging),
                "info": {"version": "1.1.4"},
            })
            install = Path(tmp) / "install"
            install.mkdir()
            (install / "简历猎手.exe").write_bytes(b"old-exe")
            with self.assertRaises(UpdateError) as caught:
                apply_update(install, "简历猎手.exe")
            self.assertIn("正式安装", str(caught.exception))
            # 拒绝时只写了备份和 update.bat，不动安装文件
            self.assertEqual((install / "简历猎手.exe").read_bytes(), b"old-exe")

    def test_apply_rejects_bad_staging_before_touching_install(self):
        # 回归：staging 里没有新版 payload 时，旧实现只查安装目录的
        # exe，照样写 bat、退出程序，白换一遍。
        import sys

        with tempfile.TemporaryDirectory() as tmp:
            updater._STATE.update({
                "status": "ready",
                "staging_dir": str(Path(tmp) / "empty-staging"),
                "info": {"version": "1.1.4"},
            })
            install = Path(tmp) / "install"
            install.mkdir()
            (install / "简历猎手.exe").write_bytes(b"old-exe")
            with self.assertRaises(UpdateError) as caught:
                apply_update(install, "简历猎手.exe")
            self.assertIn("安装包", str(caught.exception))
            self.assertEqual(list(install.iterdir()), [install / "简历猎手.exe"])


class UpdateRouteTests(unittest.TestCase):
    def setUp(self):
        _reset_update_state()
        self.original_base_dir = server.BASE_DIR
        self.zip_bytes = _make_zip()
        UpdateStubHandler.zip_bytes = self.zip_bytes
        UpdateStubHandler.status = 200
        UpdateStubHandler.requests = []
        UpdateStubHandler.version_payload = {
            "version": "1.1.4",
            "size": len(self.zip_bytes),
            "sha256": hashlib.sha256(self.zip_bytes).hexdigest(),
            "notes": "更快更稳",
        }
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), UpdateStubHandler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        server.set_base_dir(self.original_base_dir)
        _reset_update_state()

    def _request(self, path, method="GET", json_body=None):
        if "?" in path:
            path_info, query_string = path.split("?", 1)
        else:
            path_info, query_string = path, ""
        status_headers = {}

        def start_response(status, headers, exc_info=None):
            status_headers["status"] = status
            status_headers["headers"] = dict(headers)

        request_body = json.dumps(json_body).encode("utf-8") if json_body is not None else b""
        environ = {
            "REMOTE_ADDR": "127.0.0.1",
            "REQUEST_METHOD": method,
            "PATH_INFO": path_info,
            "QUERY_STRING": query_string,
            "SERVER_NAME": "127.0.0.1",
            "SERVER_PORT": "8686",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(request_body),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }
        if json_body is not None:
            environ["CONTENT_LENGTH"] = str(len(request_body))
            environ["CONTENT_TYPE"] = "application/json"
        response_iter = server.app(environ, start_response)
        try:
            body = b"".join(
                chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
                for chunk in response_iter
            ).decode("utf-8")
        finally:
            close = getattr(response_iter, "close", None)
            if close:
                close()
        return status_headers["status"], status_headers["headers"], body

    def _base_dir(self, tmp):
        base = Path(tmp)
        (base / "config.yaml").write_text(
            yaml.safe_dump({
                "ai": {
                    "service": "relay",
                    "provider": "relay",
                    "relay_url": f"http://127.0.0.1:{self.port}",
                    "relay_token": "test-app-token",
                },
            }),
            encoding="utf-8",
        )
        server.set_base_dir(base)
        return base

    def _wait_status(self, wanted, timeout=15.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status, _, body = self._request("/api/update/status")
            data = json.loads(body)
            if data.get("status") in wanted:
                return data
            time.sleep(0.2)
        self.fail(f"update status never reached {wanted}: {data!r}")

    def test_check_reports_new_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._base_dir(tmp)
            status, _, body = self._request("/api/update/check")
            self.assertTrue(status.startswith("200"), body)
            data = json.loads(body)
            self.assertTrue(data["update_available"])
            self.assertEqual(data["version"], "1.1.4")
            self.assertEqual(data["current_version"], __version__)
            self.assertEqual(data["notes"], "更快更稳")

    def test_check_suppressed_after_failed_update_until_manual_recheck(self):
        # 回归：上次更新换失败后，旧实现启动检查立刻又弹同一个更新。
        # 现在非手动检查保持报错；手动强制检查是出口，清掉失败记录
        # 后才能重新查到新版。
        with tempfile.TemporaryDirectory() as tmp:
            base = self._base_dir(tmp)
            updater.write_pending_update(base, "1.1.4")
            updater.write_update_result(
                base, {"status": "failed", "expected_version": "1.1.4", "reason_code": "copy"}
            )
            original_get_install_dir = updater.get_install_dir
            updater.get_install_dir = lambda: base
            try:
                status, _, body = self._request("/api/update/check")
                self.assertTrue(status.startswith("200"), body)
                data = json.loads(body)
                self.assertEqual(data["status"], "error")
                self.assertFalse(data["update_available"])
                self.assertIn("换回旧版本", data["error"])
                # 再查一次仍是报错，不进循环
                _, _, body = self._request("/api/update/check")
                self.assertEqual(json.loads(body)["status"], "error")
                # 手动强制检查：清掉失败记录，重新查到新版
                status, _, body = self._request("/api/update/check?force=1")
                self.assertTrue(status.startswith("200"), body)
                data = json.loads(body)
                self.assertTrue(data["update_available"])
                self.assertEqual(data["version"], "1.1.4")
            finally:
                updater.get_install_dir = original_get_install_dir

    def test_check_never_500s_when_relay_is_down(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._base_dir(tmp)
            (base / "config.yaml").write_text(
                yaml.safe_dump({"ai": {"relay_url": "http://127.0.0.1:1", "relay_token": "x"}}),
                encoding="utf-8",
            )
            status, _, body = self._request("/api/update/check?force=1")
            self.assertTrue(status.startswith("200"), body)
            self.assertFalse(json.loads(body)["update_available"])

    def test_start_without_check_is_plain_400(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._base_dir(tmp)
            status, _, body = self._request("/api/update/start", "POST", {})
            self.assertTrue(status.startswith("400"), body)
            self.assertIn("error", json.loads(body))

    def test_start_downloads_and_reaches_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._base_dir(tmp)
            status, _, body = self._request("/api/update/check")
            self.assertTrue(json.loads(body)["update_available"])
            status, _, body = self._request("/api/update/start", "POST", {})
            self.assertTrue(status.startswith("200"), body)
            data = self._wait_status({"ready", "error"})
            self.assertEqual(data["status"], "ready", data)
            self.assertTrue(Path(data["staging_dir"]).is_dir())
            self.assertTrue((Path(data["staging_dir"]) / "简历猎手.exe").is_file())

    def test_apply_refuses_outside_windows(self):
        import sys

        if sys.platform.startswith("win"):
            self.skipTest("仅在非 Windows 上验证拒绝逻辑")
        with tempfile.TemporaryDirectory() as tmp:
            self._base_dir(tmp)
            self._request("/api/update/check")
            self._request("/api/update/start", "POST", {})
            self._wait_status({"ready", "error"})
            status, _, body = self._request("/api/update/apply", "POST", {})
            self.assertTrue(status.startswith("400"), body)
            self.assertIn("正式安装", json.loads(body)["error"])


if __name__ == "__main__":
    unittest.main()
