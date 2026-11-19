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
    UpdateError,
    apply_update,
    build_update_script,
    check_for_update,
    create_backup,
    download_update,
    extract_update,
    find_payload_root,
    get_status,
    is_newer,
    overlay_staging,
    parse_version,
    prune_old_backups,
    run_check,
    start_download,
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

    version_payload = {"version": "1.0.5", "size": 0, "sha256": "0" * 64, "notes": ""}
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
            "version": "1.0.5",
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
        self.assertEqual(info["version"], "1.0.5")
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
            {"version": "1.0.5", "size": 1},
            {"version": "1.0.5", "size": 1, "sha256": "not-a-hash"},
            {"version": "1.0.5", "size": 1, "sha256": "a" * 64, "notes": 5},
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
            "version": "1.0.5",
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
            "version": "1.0.5",
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
        self.assertIn("tasklist", script)
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


class UpdateApplyTests(unittest.TestCase):
    def setUp(self):
        _reset_update_state()

    def tearDown(self):
        _reset_update_state()

    def test_apply_refuses_outside_windows(self):
        import sys

        if sys.platform.startswith("win"):
            self.skipTest("仅在非 Windows 上验证拒绝逻辑")
        updater._STATE.update({"status": "ready", "staging_dir": "/tmp/nowhere"})
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(UpdateError) as caught:
                apply_update(Path(tmp), "简历猎手.exe")
            self.assertIn("正式安装", str(caught.exception))
            # 拒绝时什么文件都不该留下
            self.assertEqual(list(Path(tmp).iterdir()), [])


class UpdateRouteTests(unittest.TestCase):
    def setUp(self):
        _reset_update_state()
        self.original_base_dir = server.BASE_DIR
        self.zip_bytes = _make_zip()
        UpdateStubHandler.zip_bytes = self.zip_bytes
        UpdateStubHandler.status = 200
        UpdateStubHandler.requests = []
        UpdateStubHandler.version_payload = {
            "version": "1.0.5",
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
            self.assertEqual(data["version"], "1.0.5")
            self.assertEqual(data["current_version"], __version__)
            self.assertEqual(data["notes"], "更快更稳")

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
