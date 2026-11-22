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
from unittest import mock

import httpx
import yaml

from bosshunter import __version__, updater
from bosshunter.updater import (
    UPDATE_FAILURE_MESSAGES,
    UpdateError,
    apply_update,
    build_update_script,
    check_for_update,
    clear_cached_update,
    clear_update_result,
    consume_pending_update,
    create_backup,
    download_update,
    extract_update,
    find_payload_root,
    get_last_update_failure,
    get_status,
    is_newer,
    load_cached_update,
    overlay_staging,
    parse_version,
    prune_old_backups,
    prune_stale_files,
    reconcile_pending_update,
    run_check,
    save_cached_update,
    start_download,
    validate_staged_payload,
    write_pending_update,
    write_update_result,
    _kill_install_dir_processes,
    _restore_from_cache,
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

    version_payload = {"version": "1.1.26", "size": 0, "sha256": "0" * 64, "notes": ""}
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
            "version": "1.1.26",
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
        self.assertEqual(info["version"], "1.1.26")
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
            {"version": "1.1.26", "size": 1},
            {"version": "1.1.26", "size": 1, "sha256": "not-a-hash"},
            {"version": "1.1.26", "size": 1, "sha256": "a" * 64, "notes": 5},
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


class CheckFailureClassificationTests(unittest.TestCase):
    """v29：检查失败要分类记日志、run_check 要报出来，不再静默当“已是最新”。

    回归：中转一慢，自动检查随后回 None，旧 run_check 把 info 置空、
    checked_at 写成现在，手动查到的新版就被盖没了，且无任何文字残留。
    """

    def setUp(self):
        _reset_update_state()
        updater._STATE.update({"check_reason": ""})
        self.config = {
            "ai": {
                "service": "relay",
                "provider": "relay",
                "relay_url": "http://127.0.0.1:9",
                "relay_token": "test-app-token",
            }
        }

    def tearDown(self):
        _reset_update_state()
        updater._STATE.update({"check_reason": ""})

    def _fake_response(self, status=200, payload=None, json_error=False):
        req = httpx.Request("GET", "http://127.0.0.1:9/api/version")
        real = httpx.Response(status, request=req)

        class FakeResp:
            status_code = status

            def raise_for_status(self):
                real.raise_for_status()

            def json(self):
                if json_error:
                    raise ValueError("bad json")
                return payload

        return FakeResp()

    def _assert_logged_reason(self, reason, fn):
        with self.assertLogs("bosshunter.updater", level="WARNING") as logs:
            result = fn()
        self.assertTrue(
            any(f"reason={reason}" in line for line in logs.output),
            f"expected reason={reason} in logs: {logs.output}",
        )
        return result

    def test_timeout_is_classified_and_logged(self):
        req = httpx.Request("GET", "http://127.0.0.1:9/api/version")
        with mock.patch.object(
            updater.httpx, "get",
            side_effect=httpx.TimeoutException("timed out", request=req),
        ):
            result = self._assert_logged_reason(
                "timeout", lambda: check_for_update(self.config))
        self.assertIsNone(result)

    def test_connect_error_is_network(self):
        req = httpx.Request("GET", "http://127.0.0.1:9/api/version")
        with mock.patch.object(
            updater.httpx, "get",
            side_effect=httpx.ConnectError("down", request=req),
        ):
            result = self._assert_logged_reason(
                "network", lambda: check_for_update(self.config))
        self.assertIsNone(result)

    def test_401_is_unauthorized(self):
        with mock.patch.object(
            updater.httpx, "get", return_value=self._fake_response(401)
        ):
            result = self._assert_logged_reason(
                "unauthorized", lambda: check_for_update(self.config))
        self.assertIsNone(result)

    def test_404_is_no_release(self):
        with mock.patch.object(
            updater.httpx, "get", return_value=self._fake_response(404)
        ):
            result = self._assert_logged_reason(
                "no_release", lambda: check_for_update(self.config))
        self.assertIsNone(result)

    def test_500_is_relay_error(self):
        with mock.patch.object(
            updater.httpx, "get", return_value=self._fake_response(500)
        ):
            result = self._assert_logged_reason(
                "relay_error", lambda: check_for_update(self.config))
        self.assertIsNone(result)

    def test_bad_sha_is_classified(self):
        payload = {"version": "1.1.26", "size": 1, "sha256": "not-a-hash", "notes": ""}
        with mock.patch.object(
            updater.httpx, "get",
            return_value=self._fake_response(200, payload),
        ):
            result = self._assert_logged_reason(
                "bad_sha", lambda: check_for_update(self.config))
        self.assertIsNone(result)

    def test_non_dict_payload_is_bad_payload(self):
        with mock.patch.object(
            updater.httpx, "get",
            return_value=self._fake_response(200, ["not", "a", "dict"]),
        ):
            result = self._assert_logged_reason(
                "bad_payload", lambda: check_for_update(self.config))
        self.assertIsNone(result)

    def test_unparseable_body_is_bad_payload(self):
        with mock.patch.object(
            updater.httpx, "get",
            return_value=self._fake_response(200, None, json_error=True),
        ):
            result = self._assert_logged_reason(
                "bad_payload", lambda: check_for_update(self.config))
        self.assertIsNone(result)

    def test_run_check_failure_keeps_known_info_and_reports_check_failed(self):
        info = {
            "version": "1.1.26",
            "current_version": __version__,
            "size": 123,
            "sha256": "a" * 64,
            "notes": "更快更稳",
            "update_available": True,
        }
        updater._STATE.update({
            "status": "idle", "update_available": True,
            "info": dict(info), "checked_at": None,
        })
        dead = {
            "ai": {
                "service": "relay", "provider": "relay",
                "relay_url": "http://127.0.0.1:1", "relay_token": "x",
            }
        }
        state = run_check(dead, force=True)
        self.assertEqual(state["status"], "error")
        self.assertEqual(state["reason_code"], "check_failed")
        self.assertEqual(state["check_reason"], "network")
        self.assertEqual(state["info"], info)
        self.assertTrue(state["update_available"])
        # 失败不进 600 秒缓存：checked_at 不动，下次照常重查。
        self.assertIsNone(state["checked_at"])
        self.assertIn("没连上服务器", state["error"])

    def test_run_check_failure_then_success_clears_check_failed(self):
        dead = {
            "ai": {
                "service": "relay", "provider": "relay",
                "relay_url": "http://127.0.0.1:1", "relay_token": "x",
            }
        }
        run_check(dead, force=True)
        self.assertEqual(get_status()["reason_code"], "check_failed")
        payload = {
            "version": "1.1.26", "size": 123,
            "sha256": "a" * 64, "notes": "更快更稳",
        }
        with mock.patch.object(
            updater.httpx, "get",
            return_value=self._fake_response(200, payload),
        ):
            state = run_check(self.config, force=True)
        self.assertEqual(state["status"], "idle")
        self.assertTrue(state["update_available"])
        self.assertEqual(state["reason_code"], "")
        self.assertEqual(state["check_reason"], "")
        self.assertIsNotNone(state["checked_at"])

    def test_run_check_without_token_stays_idle_and_keeps_info(self):
        info = {
            "version": "1.1.26",
            "current_version": __version__,
            "size": 123,
            "sha256": "a" * 64,
            "notes": "更快更稳",
            "update_available": True,
        }
        updater._STATE.update({
            "status": "error", "update_available": True,
            "info": dict(info), "error": "old", "reason_code": "nostart",
            "checked_at": None,
        })
        state = run_check({}, force=True)
        self.assertEqual(state["status"], "idle")
        self.assertEqual(state["reason_code"], "")
        self.assertEqual(state["info"], info)

    def test_check_failure_is_written_to_install_log_file(self):
        req = httpx.Request("GET", "http://127.0.0.1:9/api/version")
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            with mock.patch.object(updater, "get_install_dir", return_value=install):
                with mock.patch.object(
                    updater.httpx, "get",
                    side_effect=httpx.TimeoutException("timed out", request=req),
                ):
                    self.assertIsNone(check_for_update(self.config))
            log = install / "data" / "logs" / "jianli-lieshou.log"
            self.assertTrue(log.is_file())
            # 文件里是中文“原因=…”，logger 那行是英文 reason=…，两个都要能定位。
            self.assertIn("原因=timeout", log.read_text(encoding="utf-8"))


class UpdateDownloadTests(unittest.TestCase):
    def setUp(self):
        _reset_update_state()
        self.zip_bytes = _make_zip()
        UpdateStubHandler.zip_bytes = self.zip_bytes
        UpdateStubHandler.status = 200
        UpdateStubHandler.requests = []
        UpdateStubHandler.version_payload = {
            "version": "1.1.26",
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
            "version": "1.1.26",
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
            "version": "1.1.26",
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

    def test_consume_removes_stale_bat_but_keeps_live_one(self):
        # 残留 bat 的兜底清理（v37）：bat 已死（mtime 超 15 分钟）时
        # consume_pending_update 在消费 pending 的同时删掉残留的
        # update.bat——bat 不会再写 verdict、也不会自删，文件留着只
        # 会让下次核对误会。活 bat（15 分钟内）绝不能动：consume 提
        # 前返回 in_progress，pending 和 bat 都原样留着。
        import os

        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            # 陈旧 bat：消费后 bat 被清掉、pending 也被消费。
            write_pending_update(install, "9.9.9")
            bat = install / "update.bat"
            bat.write_text("rem stale", encoding="utf-8")
            old = time.time() - 16 * 60
            os.utime(bat, (old, old))
            verdict = consume_pending_update(install)
            assert verdict is not None
            self.assertEqual(verdict["status"], "failed")
            self.assertFalse(bat.exists())
            self.assertFalse((install / updater.UPDATE_PENDING_FILENAME).exists())
            # 新鲜 bat：还在进行中，bat 和 pending 都不动。
            write_pending_update(install, "9.9.9")
            bat.write_text("rem live", encoding="utf-8")
            verdict = consume_pending_update(install)
            assert verdict is not None
            self.assertEqual(verdict["status"], "in_progress")
            self.assertTrue(bat.exists())
            self.assertTrue((install / updater.UPDATE_PENDING_FILENAME).exists())

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
            # verdict 要把 reason_code 留下来：update-result.json 重写后，
            # 后续启动的 get_last_update_failure / 横幅才能继续认出它。
            self.assertEqual(verdict["reason_code"], "copy")
            last = get_last_update_failure(install)
            assert last is not None
            self.assertEqual(last["reason_code"], "copy")

    def test_consume_bat_failure_nostart_maps_message(self):
        # run 44758466045 的新码：bat 换完文件、新版 180 秒没在
        # %PORT% 上起来，写 failed/nostart。重开的程序要认出这个码、报出
        # 对应的人话，并把码留在状态里给 /api/update/status 透出。
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            write_pending_update(install, "9.9.9")
            write_update_result(install, {"status": "failed", "expected_version": "9.9.9", "reason_code": "nostart"})
            verdict = consume_pending_update(install)
            assert verdict is not None
            self.assertEqual(verdict["status"], "failed")
            self.assertEqual(verdict["reason"], UPDATE_FAILURE_MESSAGES["nostart"])
            self.assertEqual(verdict["reason_code"], "nostart")
            # 措辞里的“没重新打开”不是随便写的：前端按文案关键词
            # 把失败分到下载 / 校验 / 替换三步（useUpdate.tsx 的
            # classifyUpdateError），“没重新打开”命中替换那步，
            # 横幅才显示“这次没换上”+“再试一次”；否则会按启动时的
            # 默认阶段错分到“新版本没下载下来”。
            self.assertIn("没重新打开", verdict["reason"])
            last = get_last_update_failure(install)
            assert last is not None
            self.assertEqual(last["reason_code"], "nostart")
            self.assertEqual(last["reason"], UPDATE_FAILURE_MESSAGES["nostart"])
            self.assertTrue(reconcile_pending_update(install))
            state = get_status()
            self.assertEqual(state["status"], "error")
            self.assertEqual(state.get("reason_code"), "nostart")
            self.assertIn("没重新打开", state["error"])

    def test_consume_bat_failure_nostart_old_maps_message(self):
        # v30 回滚重开核实的新码：bat 回滚重拉旧版、180 秒没在
        # %PORT% 上起来，写 failed/nostart_old。重开的程序要认出这个码、报出
        # 对应的人话，并把码留在状态里给 /api/update/status 透出。
        # 文案里的“没重新打开”让前端 classifyUpdateError 分到 apply
        # 步（和 nostart 一样），横幅才显示“这次没换上”+“再试一次”。
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            write_pending_update(install, "9.9.9")
            write_update_result(install, {"status": "failed", "expected_version": "9.9.9", "reason_code": "nostart_old"})
            verdict = consume_pending_update(install)
            assert verdict is not None
            self.assertEqual(verdict["status"], "failed")
            self.assertEqual(verdict["reason"], UPDATE_FAILURE_MESSAGES["nostart_old"])
            self.assertEqual(verdict["reason_code"], "nostart_old")
            self.assertIn("没重新打开", verdict["reason"])
            self.assertIn("旧版本", verdict["reason"])
            last = get_last_update_failure(install)
            assert last is not None
            self.assertEqual(last["reason_code"], "nostart_old")
            self.assertEqual(last["reason"], UPDATE_FAILURE_MESSAGES["nostart_old"])
            self.assertTrue(reconcile_pending_update(install))
            state = get_status()
            self.assertEqual(state["status"], "error")
            self.assertEqual(state.get("reason_code"), "nostart_old")
            self.assertIn("没重新打开", state["error"])

    def test_stale_reason_code_cleared_by_fresh_check(self):
        # error 态的 reason_code 不能粘住：一次新的检查开始后，
        # 状态里就不能再留着上轮失败的码，否则横幅会把旧原因
        # 安到新一轮上。
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            write_pending_update(install, "9.9.9")
            write_update_result(install, {"status": "failed", "expected_version": "9.9.9", "reason_code": "nostart"})
            consume_pending_update(install)
            self.assertTrue(reconcile_pending_update(install))
            self.assertEqual(get_status().get("reason_code"), "nostart")
            run_check({}, install_dir=install)
            self.assertEqual(get_status().get("reason_code"), "")

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

    def test_consume_nostart_rescued_when_service_reports_expected_version(self):
        # M-44（v30）：bat 写了 failed/nostart，但本地服务只是起来得
        # 慢——重探一次报到期望版本，迟到成功不判失败，不进 error 态。
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            write_pending_update(install, "9.9.9")
            write_update_result(install, {"status": "failed", "expected_version": "9.9.9", "reason_code": "nostart"})
            with patch("bosshunter.updater._service_reports_version", return_value=True):
                verdict = consume_pending_update(install)
            assert verdict is not None
            self.assertEqual(verdict["status"], "ok")
            self.assertEqual(verdict["expected_version"], "9.9.9")
            self.assertIsNone(get_last_update_failure(install))
            self.assertFalse(reconcile_pending_update(install))

    def test_consume_nostart_old_rescued_when_service_reports_expected_version(self):
        # 回滚的 nostart_old 同理：旧版迟到了、但确实起来了，也算成功。
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            write_pending_update(install, "9.9.9")
            write_update_result(install, {"status": "failed", "expected_version": "9.9.9", "reason_code": "nostart_old"})
            with patch("bosshunter.updater._service_reports_version", return_value=True):
                verdict = consume_pending_update(install)
            assert verdict is not None
            self.assertEqual(verdict["status"], "ok")

    def test_consume_nostart_stays_failed_when_service_not_reporting(self):
        # 重探没报到期望版本：原判失败不变，reason_code 仍是 nostart。
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            write_pending_update(install, "9.9.9")
            write_update_result(install, {"status": "failed", "expected_version": "9.9.9", "reason_code": "nostart"})
            with patch("bosshunter.updater._service_reports_version", return_value=False):
                verdict = consume_pending_update(install)
            assert verdict is not None
            self.assertEqual(verdict["status"], "failed")
            self.assertEqual(verdict["reason_code"], "nostart")
            self.assertEqual(verdict["reason"], UPDATE_FAILURE_MESSAGES["nostart"])

    def test_consume_copy_not_rescued_even_when_service_reports_version(self):
        # M-44 只救 nostart / 无结果：copy/stuck 这类失败，版本号对
        # 上也不能证明半截拷贝是完整的，绝不能翻成成功。
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            write_pending_update(install, "9.9.9")
            write_update_result(install, {"status": "failed", "expected_version": "9.9.9", "reason_code": "copy"})
            with patch("bosshunter.updater._service_reports_version", return_value=True):
                verdict = consume_pending_update(install)
            assert verdict is not None
            self.assertEqual(verdict["status"], "failed")
            self.assertEqual(verdict["reason_code"], "copy")

    def test_consume_bat_failure_rollback_failed_maps_message(self):
        # H-1（v31）：bat 写了 failed/rollback_failed，重开的程序要认
        # 出这个码、报出“回滚失败、请手动重装”，不能退回 unknown；
        # 服务报到期望版本也不能把它翻成成功——混装安装不是成功。
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            write_pending_update(install, "9.9.9")
            write_update_result(install, {"status": "failed", "expected_version": "9.9.9", "reason_code": "rollback_failed"})
            with patch("bosshunter.updater._service_reports_version", return_value=True):
                verdict = consume_pending_update(install)
            assert verdict is not None
            self.assertEqual(verdict["status"], "failed")
            self.assertEqual(verdict["reason_code"], "rollback_failed")
            self.assertEqual(verdict["reason"], UPDATE_FAILURE_MESSAGES["rollback_failed"])

    def test_consume_bat_failure_nostart_rolled_back_maps_message(self):
        # S-1（v31）：bat 写了 failed/nostart_rolled_back（新版没起来、
        # 已换回旧版），重开的程序要报出“已换回旧版本”；回滚成功不
        # 等于更新成功，同样不 Rescue 成 ok。
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            write_pending_update(install, "9.9.9")
            write_update_result(install, {"status": "failed", "expected_version": "9.9.9", "reason_code": "nostart_rolled_back"})
            with patch("bosshunter.updater._service_reports_version", return_value=True):
                verdict = consume_pending_update(install)
            assert verdict is not None
            self.assertEqual(verdict["status"], "failed")
            self.assertEqual(verdict["reason_code"], "nostart_rolled_back")
            self.assertEqual(verdict["reason"], UPDATE_FAILURE_MESSAGES["nostart_rolled_back"])

    def test_consume_missing_result_rescued_when_service_reports_version(self):
        # bat 根本没写结果文件（被杀掉/没跑完），服务却在期望版本上：
        # 同样算迟到成功，不判 unknown 失败。
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            write_pending_update(install, "9.9.9")
            with patch("bosshunter.updater._service_reports_version", return_value=True):
                verdict = consume_pending_update(install)
            assert verdict is not None
            self.assertEqual(verdict["status"], "ok")

    def test_service_reports_version_probes_real_health_endpoint(self):
        # _service_reports_version 的真机行为：对着一个真的
        # /api/health 探——版本对上才算、版本不对/状态不对/没服务都不算。
        import json as _json
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        from bosshunter.updater import _service_reports_version

        state = {"payload": {"status": "ok", "version": "9.9.9"}}

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = _json.dumps(state["payload"]).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]
        import threading

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.assertTrue(_service_reports_version(port, "9.9.9"))
            state["payload"] = {"status": "ok", "version": "1.1.16"}
            self.assertFalse(_service_reports_version(port, "9.9.9"))
            state["payload"] = {"status": "error", "version": "9.9.9"}
            self.assertFalse(_service_reports_version(port, "9.9.9"))
        finally:
            server.shutdown()
            server.server_close()
        # 没服务在听：连接被拒，False，不抛异常。
        self.assertFalse(_service_reports_version(port, "9.9.9"))


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
        # 重拉 exe 不用裸 start：窗口 exe 经隐藏 PowerShell 的
        # Start-Process 拉起（不带 -WindowStyle，面板正常显示、
        # 不分配控制台），4 处重拉（首轮 / nostart 重试 / 回滚重开 /
        # nostart_old 重试）都是同一写法。
        self.assertNotIn('start ""', script)
        self.assertEqual(
            script.count(
                "Start-Process -FilePath '%INSTALL_DIR%\\%EXE_NAME%'"
                " -WorkingDirectory '%INSTALL_DIR%'"
            ),
            4,
        )
        self.assertNotIn("Start-Process -WindowStyle", script)
        self.assertIn("update.log", script)
        self.assertIn('del "%~f0"', script)
        # 批处理里绝不能出现会删用户数据的命令
        self.assertNotIn("del config.yaml", script)
        self.assertNotIn("rmdir /s /q \"%INSTALL_DIR%\"", script)
        self.assertNotIn("format", script.lower())

    def test_batch_kills_stuck_process_tree_and_other_instances(self):
        # 回归：旧 bat 只等主进程 pid；残留/多开的同名进程占住文件时
        # robocopy 直接半路失败。超时后要 taskkill 整棵进程树，换
        # 之前还要清掉其他同名进程。taskkill 现在走隐藏 PowerShell
        # 调用（黑窗口修法），路径里的引号在 PowerShell 里是单引号。
        script = build_update_script()
        self.assertIn("taskkill /PID %PID% /T /F", script)
        self.assertIn("taskkill /F /IM '%EXE_NAME%' /T", script)

    def test_batch_rollback_stops_live_exe_before_restore(self):
        # 回归：v31→v32 全流程测试 run 44880570903 Main/Lock：换版成功后
        # bat 在错误的端口（18686）上等新版 180 秒，误判后回滚；:rollback
        # 直接 robocopy 恢复，而 bat 自己拉起的新版还跑着、占住了文件，
        # 恢复退出码 11、verdict 是 rollback_failed。:precopy 前有“绝不
        # 带着活进程硬拷”的关口，回滚这里必须先停掉还活着的程序再恢复；
        # 停不掉就如实写 rollback_failed，不硬拷。
        script = build_update_script()
        lines = script.splitlines()
        rollback = lines.index(":rollback")
        restore = next(
            i
            for i in range(rollback, len(lines))
            if "& robocopy" in lines[i] and "%BACKUP%" in lines[i]
        )
        section = "\n".join(lines[rollback:restore])
        # 先按安装目录路径清进程（:procsweep 同款），再按同名清残留，
        # 都在恢复 robocopy 之前；还有 :rollback_restore 落点。
        self.assertIn("ExecutablePath", section)
        self.assertIn("Stop-Process", section)
        self.assertIn("taskkill /F /IM '%EXE_NAME%' /T", section)
        self.assertIn(":rollback_restore", section)
        self.assertIn("goto :rollback_failed", section)

    def test_batch_waits_with_hidden_sleep_not_ping_or_timeout(self):
        # 回归：timeout /t N /nobreak 在 detached、无控制台的进程里
        # 不返回，bat 卡在等主进程退出的循环里走不到 robocopy
        # （CI 更新全流程测试两次复现；与 lucky 在无影云电脑上
        # v10 点更新的故障现象一致）。等待先用 ping 延时修过；但
        # ping 本身是控制台程序，没有可继承的控制台时会自己分配
        # 可见窗口——这就是黑窗口。现在延时改用隐藏 PowerShell 里
        # 的 Start-Sleep：1 秒（等主进程）、5 秒（强制结束之后）、
        # 3 秒（清同名进程之后），不许再出现裸 ping 或 timeout。
        script = build_update_script()
        self.assertNotIn("timeout /t", script.lower())
        self.assertEqual(
            [line for line in script.splitlines() if line.strip().lower().startswith("ping")],
            [],
        )
        self.assertIn('"Start-Sleep -Seconds 1"', script)
        self.assertIn('"Start-Sleep -Seconds 5"', script)
        self.assertIn('"Start-Sleep -Seconds 3"', script)
        for line in script.splitlines():
            if line.strip().lower().startswith("rem"):
                continue
            if "Start-Sleep" in line:
                self.assertIn("powershell -NoProfile -NonInteractive -WindowStyle Hidden", line)

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

    def test_bat_launch_flags_hide_the_console_window(self):
        # 回归（v37 根因核实）：CREATE_NO_WINDOW 只让直接创建的 cmd
        # 不开窗口，cmd 拉起的孙进程（PowerShell / robocopy /
        # taskkill）各自会新开一个黑框——powershell -WindowStyle
        # Hidden 只是窗口建出来后再藏起来，建窗到藏窗之间会闪一下；
        # 等主进程的轮询一秒调两次 PowerShell，黑框就一直闪、一直弹
        # （用户 2026-11-22 原话）。根治在发起方：cmd 出生就带一个
        # 隐藏控制台——CREATE_NEW_CONSOLE（0x00000010）+ STARTUPINFO
        # （STARTF_USESHOWWINDOW + SW_HIDE），孙进程继承这个已隐藏的
        # 控制台，不再自己分配窗口。CREATE_NO_WINDOW / DETACHED_PROCESS
        # 与 CREATE_NEW_CONSOLE 互斥（前两者 = 不要控制台），绝不能
        # 同在标志里；CREATE_NEW_PROCESS_GROUP（Ctrl+C 隔离）保留。
        from unittest.mock import patch

        from bosshunter.updater import (
            _windows_hidden_console_startupinfo,
            _windows_launch_flags,
        )

        flags = _windows_launch_flags()
        self.assertTrue(flags & 0x00000010, "CREATE_NEW_CONSOLE 缺失")
        self.assertFalse(flags & 0x08000000, "CREATE_NO_WINDOW 必须去掉")
        self.assertFalse(flags & 0x00000008, "DETACHED_PROCESS 必须去掉")
        self.assertTrue(flags & 0x00000200, "CREATE_NEW_PROCESS_GROUP 缺失")
        with patch("bosshunter.updater._is_windows", return_value=True):
            si = _windows_hidden_console_startupinfo()
        assert si is not None
        self.assertTrue(si.dwFlags & 0x00000001, "STARTF_USESHOWWINDOW 缺失")
        self.assertEqual(si.wShowWindow, 0, "wShowWindow 必须是 SW_HIDE(0)")

    def test_batch_strips_trailing_backslash_from_dirs(self):
        # 回归：INSTALL_DIR=%~dp0 自带末尾反斜杠，robocopy "%INSTALL_DIR%"
        # 变成 "%...\"——CommandLineToArgvW 把 \" 当转义引号，开关全被
        # 吞进路径里，robocopy 0.22 秒就报错回滚（v16→v17 全流程测试
        # run 44625451318：等待循环已修好，换文件那步瞬间失败，日志
        # 里只有 5 行 echo、两次 robocopy 的输出都没进来）。设好目录
        # 后立刻去掉末尾反斜杠，之后拼路径都显式加反斜杠。
        script = build_update_script()
        self.assertIn(
            'if "%INSTALL_DIR:~-1%"=="\\" set "INSTALL_DIR=%INSTALL_DIR:~0,-1%"',
            script,
        )
        self.assertIn(
            'if "%STAGING:~-1%"=="\\" set "STAGING=%STAGING:~0,-1%"',
            script,
        )
        # 去掉反斜杠后，拼路径必须显式加反斜杠，不许再出现靠末尾
        # 反斜杠拼出来的路径。robocopy 里的路径现在是隐藏 PowerShell
        # 调用里的单引号形式（黑窗口修法），反斜杠仍要显式拼。
        self.assertNotIn('"%INSTALL_DIR%%EXE_NAME%"', script)
        self.assertNotIn('"%INSTALL_DIR%%BACKUP%"', script)
        self.assertNotIn('"%INSTALL_DIR%_internal"', script)
        self.assertIn('"%INSTALL_DIR%\\%EXE_NAME%"', script)
        self.assertIn("'%INSTALL_DIR%\\%BACKUP%'", script)
        self.assertIn("'%INSTALL_DIR%\\_internal'", script)
        self.assertIn('set "LOG=%INSTALL_DIR%\\update.log"', script)
        self.assertIn('set "RESULT=%INSTALL_DIR%\\update-result.json"', script)

    def test_batch_logs_robocopy_exit_code(self):
        # 回归：v16→v17 测试里 robocopy 0.22 秒失败，但它的输出没进
        # update.log，失败原因只能猜。每条 robocopy 之后立刻把退出
        # 码写进日志（先 set RC 再写、再判断，判断本身不改码）。
        # robocopy 现在走隐藏 PowerShell 调用（黑窗口修法）：行里
        # 仍是同一条 robocopy 命令，退出码经 exit $LASTEXITCODE
        # 带出，下一行照旧是 set RC + 写日志。
        script = build_update_script()
        lines = script.splitlines()
        rc_lines = [
            i
            for i, line in enumerate(lines)
            if "& robocopy" in line.lower()
        ]
        self.assertEqual(len(rc_lines), 4)
        for i in rc_lines:
            line = lines[i]
            self.assertIn("powershell -NoProfile -NonInteractive -WindowStyle Hidden", line)
            self.assertIn("exit $LASTEXITCODE", line)
            self.assertIn('>> "%LOG%"', line)
            following = "\n".join(lines[i + 1 : i + 3])
            self.assertIn('set "RC=%errorlevel%"', following)
            self.assertIn("robocopy", following)
            self.assertIn("退出码", following)
            self.assertIn('>> "%LOG%"', following)

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
        # 只按名查的五处（:procsweep 两次、:rollback 两次、:fail_write 一次）也
        # 必须走同一套 PowerShell 探针，不许回退到管道。
        name_probes = [
            line
            for line in script.splitlines()
            if "Get-Process -Name" in line
        ]
        self.assertGreaterEqual(len(name_probes), 5)
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
        self.assertIn(":fail_nostart", script)
        self.assertIn(":rollback", script)
        self.assertIn("reason_code", script)

    def test_batch_verifies_new_version_before_reporting_applied(self):
        # 回归 run 44758466045：旧 bat 换完文件就先写 applied 再裸
        # start——新版 180 秒没在 8686 上起来、也没有 exe 在跑，
        # 结果文件却说 applied，失败被静默吞掉。现在必须先拉起、
        # 再轮询 %PORT% /api/health 等期望版本，等到了才写 applied、
        # 删 staging；等不到走 :fail_nostart。
        # M-41/M-44（v30）：轮询问的是 bat 第 7 个参数 %PORT%（默认
        # 8686），不再写死 8686；上限从 90 秒放宽到 180 秒。
        script = build_update_script()
        self.assertIn('set "PORT=%~7"', script)
        self.assertIn('if "%PORT%"=="" set "PORT=8686"', script)
        self.assertIn("http://127.0.0.1:%PORT%/api/health", script)
        self.assertNotIn("http://127.0.0.1:8686/api/health", script)
        self.assertIn("(Get-Date).AddSeconds(180)", script)
        self.assertNotIn("(Get-Date).AddSeconds(90)", script)
        self.assertIn("$h.version", script)
        self.assertIn("if ($seen -eq '%EXPECTED%')", script)
        self.assertIn(":fail_nostart", script)
        self.assertIn('reason_code": "nostart"', script)
        health_at = script.index("http://127.0.0.1:%PORT%/api/health")
        applied_at = script.index('echo {"status": "applied"')
        staging_at = script.index('rmdir /s /q "%STAGING%"')
        # 顺序不能反：轮询 → 写 applied → 删 staging。
        self.assertLess(health_at, applied_at)
        self.assertLess(applied_at, staging_at)
        # 失败分支必须在 applied 之前分流出去。
        self.assertIn("if %HEALTH_RC% neq 0 goto :fail_nostart", script)
        self.assertLess(
            script.index("if %HEALTH_RC% neq 0 goto :fail_nostart"), applied_at
        )

    def test_batch_nostart_retries_once_then_rolls_back(self):
        # S-1（v31）：nostart 不再是终局。180 秒等不到新版时先探端口：
        # 已有实例在应答就只写 failed/nostart（见下个测试）；端口没
        # 应答就再拉一次新 exe、再等 60 秒，还不起来就走 :rollback
        # 换回旧版（坏新版绝不能永久换掉能用的旧版），结果按
        # failed/nostart_rolled_back 写。旧断言“verdict 在第二次拉起
        # 之前写好”随之过期：verdict 现在要等第二次拉起 + 回滚的结果
        # 才能写，提前写 nostart 会把“已换回旧版”误报成“新版没起来”。
        # M-44（v30）：再拉之前先探端口——已有实例在应答就不再拉，
        # 免得两个实例抢绑端口；端口没应答才拉。
        script = build_update_script()
        # 两条 applied 分支（首轮 + 第二次拉起）都删 staging：失败
        # 路径绝不能删。
        self.assertEqual(script.count('rmdir /s /q "%STAGING%"'), 2)
        # 按行首标签定位，别撞上前面的 "goto :fail_nostart"；切到
        # :fail_nostart_recovered 标签（含 goto :rollback 那一行）。
        start = script.index("\r\n:fail_nostart\r\n")
        end = script.index("\r\n:fail_nostart_recovered\r\n", start)
        section = script[start:end]
        self.assertIn("Invoke-RestMethod -Uri 'http://127.0.0.1:%PORT%/api/health'", section)
        self.assertIn('set "PROBE_RC=%errorlevel%"', section)
        self.assertIn('if %PROBE_RC% equ 0 goto :fail_nostart_answered', section)
        self.assertIn("Start-Process -FilePath '%INSTALL_DIR%\\%EXE_NAME%'", section)
        self.assertIn("(Get-Date).AddSeconds(60)", section)
        self.assertIn('set "HEALTH2_RC=%errorlevel%"', section)
        self.assertIn('set "FAIL_CODE=nostart_rolled_back"', section)
        self.assertIn("goto :rollback", section)
        self.assertNotIn('rmdir /s /q "%STAGING%"', section)
        # 顺序不能反：探端口 → 再拉一次 → 60 秒轮询 → 回滚。
        self.assertLess(
            section.index('set "PROBE_RC=%errorlevel%"'),
            section.index("Start-Process -FilePath '%INSTALL_DIR%\\%EXE_NAME%'"),
        )
        self.assertLess(
            section.index("Start-Process -FilePath '%INSTALL_DIR%\\%EXE_NAME%'"),
            section.index("(Get-Date).AddSeconds(60)"),
        )
        self.assertLess(
            section.index("(Get-Date).AddSeconds(60)"),
            section.index("goto :rollback"),
        )

    def test_batch_nostart_port_already_answering_writes_nostart_without_rollback(self):
        # M-44（v30）+ S-1（v31）：端口已有实例在应答，说明有实例在
        # 起来、只是还没报到期望版本——不再重复拉起，也不回滚（回滚
        # 会杀掉正在起来的实例）；只写 failed/nostart、staging 保留，
        # 迟到的实例随后起来后由 consume_pending_update 重探改判。
        script = build_update_script()
        start = script.index("\r\n:fail_nostart_answered\r\n")
        end = script.index("\r\n:rollback\r\n", start)
        section = script[start:end]
        self.assertIn('echo {"status": "failed"', section)
        self.assertIn('reason_code": "nostart"', section)
        self.assertIn("goto :fail_exit", section)
        self.assertNotIn('rmdir /s /q "%STAGING%"', section)
        self.assertNotIn("goto :rollback", section)
        verdict_at = section.index('echo {"status": "failed"')
        self.assertLess(verdict_at, section.index("goto :fail_exit"))

    def test_batch_nostart_second_start_success_reports_applied(self):
        # S-1（v31）：第二次拉起 60 秒内起来了，版本对上了——这次更新
        # 算换成了，写 applied、删 staging（重试不用留），和首轮成功
        # 分支同等待遇。
        script = build_update_script()
        start = script.index("\r\n:fail_nostart_recovered\r\n")
        end = script.index("\r\n:fail_nostart_answered\r\n", start)
        section = script[start:end]
        self.assertIn('echo {"status": "applied"', section)
        self.assertIn('rmdir /s /q "%STAGING%"', section)
        self.assertIn("goto :fail_exit", section)
        self.assertLess(
            section.index('echo {"status": "applied"'),
            section.index('rmdir /s /q "%STAGING%"'),
        )

    def test_batch_fail_write_verifies_old_version_before_reporting_failure(self):
        # v30 回滚重开核实：:fail_write 以前先写 failed 再裸 start 重拉
        # 旧版——旧版拉不起来也照写 copy/stuck，用户看到的还是“旧版本
        # 还能用”。现在照成功分支的写法：重拉旧 exe 后轮询 %PORT%
        # /api/health 等旧版本号（bat 第 6 个参数 %CURRENT%，即更新
        # 前的版本），等到了才按原原因码写 failed；顺序必须是
        # start → 轮询 → 写 verdict，verdict 一定在 bat 自删之前。
        # M-41/M-44（v30）：轮询问 %PORT%（第 7 个参数，默认 8686），
        # 上限从 90 秒放宽到 180 秒。
        script = build_update_script()
        self.assertIn('set "CURRENT=%~6"', script)
        start = script.index("\r\n:fail_write\r\n")
        end = script.index("\r\n:fail_nostart_old\r\n", start)
        section = script[start:end]
        self.assertIn(":fail_poll", section)
        self.assertIn("http://127.0.0.1:%PORT%/api/health", section)
        self.assertIn("(Get-Date).AddSeconds(180)", section)
        self.assertIn("$seen -eq '%CURRENT%'", section)
        self.assertIn('if %OLD_HEALTH_RC% neq 0 goto :fail_nostart_old', section)
        self.assertIn('reason_code": "%FAIL_CODE%"', section)
        self.assertNotIn('rmdir /s /q "%STAGING%"', section)
        self.assertLess(
            section.index("Start-Process -FilePath '%INSTALL_DIR%\\%EXE_NAME%'"),
            section.index("http://127.0.0.1:%PORT%/api/health"),
        )
        self.assertLess(
            section.index("http://127.0.0.1:%PORT%/api/health"),
            section.index('reason_code": "%FAIL_CODE%"'),
        )
        # 原原因码必须保留：Retry 第一遍期望 failed/copy（旧版回来
        # 了、更新没换成），轮询成功绝不能把码改成别的。
        self.assertIn('set "FAIL_CODE=copy"', script)
        self.assertIn("goto :fail_write", script)

    def test_batch_fail_write_old_version_never_answers_reports_nostart_old(self):
        # 回滚重拉后旧版 180 秒一直不在 %PORT% 上应答：不能静默、不能
        # 照写 copy/stuck，必须写明确的新原因码 nostart_old，并再拉
        # 一次旧 exe 给最后一次机会；staging 照旧保留（重试不用重下）。
        # M-44（v30）：再拉之前先探端口，已有实例在应答就不再拉。
        # S-1（v31）：两条 applied 分支（首轮 + nostart 第二次拉起）
        # 都删 staging，失败路径仍绝不能删。
        script = build_update_script()
        self.assertEqual(script.count('rmdir /s /q "%STAGING%"'), 2)
        start = script.index("\r\n:fail_nostart_old\r\n")
        section = script[start:]
        self.assertIn('reason_code": "nostart_old"', section)
        self.assertIn("Start-Process -FilePath '%INSTALL_DIR%\\%EXE_NAME%'", section)
        self.assertIn("Invoke-RestMethod -Uri 'http://127.0.0.1:%PORT%/api/health'", section)
        self.assertIn("if %PROBE_RC% neq 0 if exist \"%INSTALL_DIR%\\%EXE_NAME%\" powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command \"Start-Process -FilePath '%INSTALL_DIR%\\%EXE_NAME%' -WorkingDirectory '%INSTALL_DIR%'\" >> \"%LOG%\" 2>&1", section)
        self.assertNotIn('rmdir /s /q "%STAGING%"', section)
        verdict_at = section.index('reason_code": "nostart_old"')
        self.assertLess(verdict_at, section.index('del "%~f0"'))
        self.assertLess(
            verdict_at,
            section.index("Start-Process -FilePath '%INSTALL_DIR%\\%EXE_NAME%'"),
        )
        # 分流必须在写原原因码之前：OLD_HEALTH_RC 非 0 就走新分支，
        # 不能先写 %FAIL_CODE% 再改——结果文件只写一次。
        self.assertLess(
            script.index("if %OLD_HEALTH_RC% neq 0 goto :fail_nostart_old"),
            start,
        )

    def test_batch_old_version_poll_semantics(self):
        # 回滚轮询的判定语义（模型）：等的是旧版本号，不是“随便哪个
        # 版本起来了”。新版应答不算回滚成功——回滚没把旧版换回来时
        # 不能报成功。机制本身（轮询行、180 秒上限、失败分支）由
        # test_batch_fail_write_verifies_old_version_before_reporting_failure
        # 和 test_batch_fail_write_old_version_never_answers_reports_nostart_old
        # 钉在生成的 bat 上。
        def poll(seen_versions, expected, deadline=180, interval=3):
            waited = 0
            for seen in seen_versions:
                if seen == expected:
                    return True, waited
                waited += interval
                if waited >= deadline:
                    return False, waited
            return False, waited

        # 第三次轮询报到旧版本：不等满，直接成功。
        self.assertEqual(poll(["", "1.1.26", "1.1.15"], "1.1.15"), (True, 6))
        # 一直没应答：等满 180 秒再判失败。
        self.assertEqual(poll([""] * 60, "1.1.15"), (False, 180))
        # 一直应答新版本：回滚没换回旧版，同样等满 180 秒判失败。
        self.assertEqual(poll(["1.1.26"] * 60, "1.1.15"), (False, 180))

    def test_batch_health_poll_semantics(self):
        # bat 本体跑不了（Windows 批处理），这里把轮询的判定语义
        # 钉成模型：期望版本出现就停、一直不出现就等满 180 秒再判
        # 失败。机制本身（轮询行、180 秒上限、失败分支）由
        # test_batch_verifies_new_version_before_reporting_applied
        # 钉在生成的 bat 上。
        def poll(seen_versions, expected, deadline=180, interval=3):
            waited = 0
            for seen in seen_versions:
                if seen == expected:
                    return True, waited
                waited += interval
                if waited >= deadline:
                    return False, waited
            return False, waited

        # 第三次轮询报到期望版本：不等满，直接成功。
        self.assertEqual(poll(["", "1.1.15", "1.1.26"], "1.1.26"), (True, 6))
        # 一直没应答：等满 180 秒再判失败。
        self.assertEqual(poll([""] * 60, "1.1.26"), (False, 180))
        # 一直应答旧版本：同样等满 180 秒（旧版不算数）。
        self.assertEqual(poll(["1.1.15"] * 60, "1.1.26"), (False, 180))

    def test_batch_validates_staging_and_purges_stale_internal(self):
        # 回归：旧 bat 不校验 staging、有错照换；且 robocopy /E /IS
        # 从不清残留，旧版删掉的文件一直留在 _internal 里。
        script = build_update_script()
        self.assertIn('if not exist "%STAGING%\\%EXE_NAME%"', script)
        self.assertIn('if not exist "%STAGING%\\_internal\\"', script)
        # _internal 的 /MIR 现在走隐藏 PowerShell 调用（黑窗口修法），
        # 命令本身不变，只是包在 powershell -WindowStyle Hidden 里。
        self.assertIn("robocopy '%STAGING%\\_internal' '%INSTALL_DIR%\\_internal' /MIR", script)
        self.assertIn("/R:5 /W:3", script)

    def test_batch_restores_backup_on_copy_failure(self):
        # 回归：旧 bat 复制失败就扔下半新半旧的安装目录走人。
        # 现在必须从备份恢复（包括 _internal 的 /MIR 还原），再报错。
        script = build_update_script()
        # 回滚的两条 robocopy 现在走隐藏 PowerShell 调用（黑窗口修法），
        # 命令本身不变，只是包在 powershell -WindowStyle Hidden 里。
        self.assertIn("robocopy '%INSTALL_DIR%\\%BACKUP%' '%INSTALL_DIR%'", script)
        self.assertIn("'%INSTALL_DIR%\\%BACKUP%\\_internal'", script)
        self.assertIn("%EXPECTED%", script)

    def test_batch_rollback_checks_restore_result(self):
        # H-1（v31）：回滚用的两条 robocopy 以前只把 %RC% 写进日志、
        # 没有任何判断，随后无条件 echo“已恢复旧版本”。现在每条恢复
        # 都判 RC>=8，恢复后还要校验旧 exe 与 _internal 都在；任一
        # 失败都走 :rollback_failed、写 failed/rollback_failed，绝不
        # 写“已恢复旧版本”。
        script = build_update_script()
        start = script.index("\r\n:rollback\r\n")
        end = script.index("\r\n:fail_stuck\r\n", start)
        section = script[start:end]
        self.assertEqual(section.count("if %RC% geq 8 goto :rollback_failed"), 2)
        self.assertIn('if not exist "%INSTALL_DIR%\\%EXE_NAME%" goto :rollback_failed', section)
        self.assertIn('if not exist "%INSTALL_DIR%\\_internal\\" goto :rollback_failed', section)
        self.assertIn(":rollback_failed", section)
        self.assertIn('set "FAIL_CODE=rollback_failed"', section)
        # “已恢复旧版本”只能出现在全部校验通过之后。
        restored_at = section.index("已恢复旧版本")
        self.assertLess(section.index("if %RC% geq 8 goto :rollback_failed"), restored_at)
        self.assertLess(
            section.index('if not exist "%INSTALL_DIR%\\%EXE_NAME%" goto :rollback_failed'),
            restored_at,
        )
        # 恢复成功直接去 :fail_write，不再经 :fail_copy（它会把原因码
        # 改回 copy）；:fail_copy 只留给 BACKUP 为空的手跑场景，且
        # 只在 FAIL_CODE 为空时才写 copy，nostart_rolled_back 不被覆盖。
        self.assertEqual(section.count("goto :fail_write"), 2)
        self.assertIn('if "%BACKUP%"=="" goto :fail_copy', section)
        self.assertIn('if "%FAIL_CODE%"=="" set "FAIL_CODE=copy"', section)

    def test_batch_rollback_failed_keeps_code_when_old_version_never_answers(self):
        # H-1（v31）：回滚失败后旧版 180 秒也没起来，原因码不能再被
        # nostart_old 覆盖——用户要看到的是“回滚失败、请手动重装”，
        # 不是“旧版没起来”。
        script = build_update_script()
        start = script.index("\r\n:fail_nostart_old\r\n")
        section = script[start:]
        self.assertIn('if "%FAIL_CODE%"=="rollback_failed" goto :fail_nostart_old_rb', section)
        self.assertIn(":fail_nostart_old_rb", section)
        self.assertIn('reason_code": "rollback_failed"', section)
        self.assertIn('reason_code": "nostart_old"', section)


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
                "info": {"version": "1.1.26"},
            })
            install = Path(tmp) / "install"
            install.mkdir()
            (install / "简历猎手.exe").write_bytes(b"old-exe")
            with self.assertRaises(UpdateError) as caught:
                apply_update(install, "简历猎手.exe")
            self.assertIn("正式安装", str(caught.exception))
            # 拒绝时只写了备份和 update.bat，不动安装文件
            self.assertEqual((install / "简历猎手.exe").read_bytes(), b"old-exe")

    def test_apply_closes_app_window_first_and_launches_bat_hidden(self):
        # 回归：更新换文件前必须先关主界面 --app 窗口（只关 exe 启动时
        # 持有的那个句柄，绝不碰登录浏览器），再以不带控制台窗口的
        # 方式拉起 update.bat。顺序不能反：窗口不关，文件可能被占住、
        # 端口也交接不干净。v24 的完整顺序是：关窗口 → 停浏览器运行
        # 时（stop_runtime，停 node 子进程树）→ 按 ExecutablePath
        # 杀安装目录里的残留进程 → 起 bat。Windows 路径在 Linux 上
        # 用 mock 走一遍（扮 _is_windows 而不是 os.name——patch os.name
        # 会连 pathlib.Path 一起改成 WindowsPath，在 Linux 上建不出来）。
        from unittest.mock import patch

        order: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "staging"
            (staging / "_internal").mkdir(parents=True)
            (staging / "简历猎手.exe").write_bytes(b"new-exe")
            (staging / "_internal" / "app.bin").write_bytes(b"new-app")
            updater._STATE.update({
                "status": "ready",
                "staging_dir": str(staging),
                "info": {"version": "1.1.26"},
            })
            install = Path(tmp) / "install"
            install.mkdir()
            (install / "简历猎手.exe").write_bytes(b"old-exe")
            with (
                patch("bosshunter.updater._is_windows", return_value=True),
                patch("bosshunter.app_window.close_app_window") as close,
                patch("bosshunter.browser.runtime.stop_runtime") as stop,
                patch("bosshunter.updater._kill_install_dir_processes") as kill,
                patch("bosshunter.updater.subprocess.Popen") as popen,
            ):
                close.side_effect = lambda: order.append("close") or True
                stop.side_effect = lambda *args, **kwargs: order.append("stop_runtime") or True
                kill.side_effect = lambda *args, **kwargs: order.append("kill") or True
                popen.side_effect = lambda *args, **kwargs: order.append("popen")
                apply_update(install, "简历猎手.exe")
            self.assertEqual(order, ["close", "stop_runtime", "kill", "popen"])
            close.assert_called_once()
            stop.assert_called_once()
            kill.assert_called_once()
            popen.assert_called_once()
            flags = popen.call_args.kwargs.get("creationflags", 0)
            # v37：拉起 update.bat 必须带 CREATE_NEW_CONSOLE（+ SW_HIDE
            # 的 STARTUPINFO，见 test_apply_launches_bat_with_hidden_console），
            # 不能只带 CREATE_NO_WINDOW——后者管不住 bat 拉起的孙进程，
            # 每次 PowerShell 都会闪一下黑窗口。
            self.assertTrue(flags & 0x00000010, "拉起 update.bat 必须带 CREATE_NEW_CONSOLE")
            self.assertFalse(flags & 0x08000000, "CREATE_NO_WINDOW 必须去掉")

    def test_apply_passes_current_version_to_update_bat(self):
        # v30 回滚重开核实：bat 第 6 个参数必须是当前（旧）版本号，
        # 回滚轮询等它出现才算旧版回来了。丢了这个参数，bat 的
        # %CURRENT% 为空、轮询永远等不到旧版，每次回滚都会误报
        # nostart_old。
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "staging"
            (staging / "_internal").mkdir(parents=True)
            (staging / "简历猎手.exe").write_bytes(b"new-exe")
            (staging / "_internal" / "app.bin").write_bytes(b"new-app")
            updater._STATE.update({
                "status": "ready",
                "staging_dir": str(staging),
                "info": {"version": "1.1.26"},
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
                apply_update(install, "简历猎手.exe")
            popen.assert_called_once()
            argv = popen.call_args.args[0]
            self.assertEqual(argv[0:2], ["cmd.exe", "/c"])
            self.assertEqual(argv[3], str(staging))
            self.assertEqual(argv[4], "简历猎手.exe")
            self.assertEqual(argv[6], "1.1.26")
            self.assertEqual(argv[7], f"update-backup-{__version__}")
            self.assertEqual(argv[8], __version__)

    def test_apply_passes_relaunch_port_to_update_bat(self):
        # v32（run 44873002310 Retry）：bat 第 7 个参数必须是重开端口
        # 8686，不是当前服务端口。bat 拉起 exe 时不带参数，启动器默认
        # 跑 web --port 8686，重开的实例永远跑 8686；v31 传当前端口
        # （M-41 的机制）是错的——CI 里服务跑 18686、重开的跑 8686，
        # bat 在 18686 上等满 180 秒也等不到旧版，:fail_poll 误报
        # nostart_old，stuck 永远写不出来。apply_update 不再接受端口
        # 参数，bat 拿到的永远是 RELAUNCH_PORT。
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "staging"
            (staging / "_internal").mkdir(parents=True)
            (staging / "简历猎手.exe").write_bytes(b"new-exe")
            (staging / "_internal" / "app.bin").write_bytes(b"new-app")
            updater._STATE.update({
                "status": "ready",
                "staging_dir": str(staging),
                "info": {"version": "1.1.26"},
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
                apply_update(install, "简历猎手.exe")
            popen.assert_called_once()
            argv = popen.call_args.args[0]
            self.assertEqual(argv[9], "8686")
            self.assertEqual(argv[9], str(updater.RELAUNCH_PORT))
            # 第 6 个参数仍是当前版本号，不能被端口顶掉。
            self.assertEqual(argv[8], __version__)

    def test_apply_launches_bat_with_hidden_console(self):
        # v37：起 bat 必须带 CREATE_NEW_CONSOLE + STARTUPINFO(SW_HIDE)，
        # 不能只传 CREATE_NO_WINDOW——后者只让 cmd 本身不开窗口，
        # bat 拉起的每个孙进程（PowerShell / robocopy / taskkill）会
        # 各自新开黑框、每次闪一下，轮询一秒两次就是“一直闪、一直弹”。
        # 隐藏控制台出生即有、孙进程继承它，才是根治。
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "staging"
            (staging / "_internal").mkdir(parents=True)
            (staging / "简历猎手.exe").write_bytes(b"new-exe")
            (staging / "_internal" / "app.bin").write_bytes(b"new-app")
            updater._STATE.update({
                "status": "ready",
                "staging_dir": str(staging),
                "info": {"version": "1.1.26"},
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
                apply_update(install, "简历猎手.exe")
            popen.assert_called_once()
            kwargs = popen.call_args.kwargs
            flags = kwargs.get("creationflags", 0)
            self.assertTrue(flags & 0x00000010, "CREATE_NEW_CONSOLE 缺失")
            self.assertFalse(flags & 0x08000000, "CREATE_NO_WINDOW 必须去掉")
            self.assertFalse(flags & 0x00000008, "DETACHED_PROCESS 必须去掉")
            si = kwargs.get("startupinfo")
            assert si is not None, "startupinfo 必须传（SW_HIDE 藏控制台）"
            self.assertTrue(si.dwFlags & 0x00000001, "STARTF_USESHOWWINDOW 缺失")
            self.assertEqual(si.wShowWindow, 0, "wShowWindow 必须是 SW_HIDE(0)")

    def test_apply_failure_removes_bat_and_pending(self):
        # bat 没拉起来（Popen 直接炸）：pending 和刚写好的 update.bat
        # 都要清掉——残留的 bat 不能留给下次核对误会。状态放回 ready
        # （H-2：用户还能点重试），安装文件原样不动。
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "staging"
            (staging / "_internal").mkdir(parents=True)
            (staging / "简历猎手.exe").write_bytes(b"new-exe")
            (staging / "_internal" / "app.bin").write_bytes(b"new-app")
            updater._STATE.update({
                "status": "ready",
                "staging_dir": str(staging),
                "info": {"version": "1.1.26"},
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
                popen.side_effect = RuntimeError("boom")
                with self.assertRaises(RuntimeError):
                    apply_update(install, "简历猎手.exe")
            self.assertFalse((install / "update.bat").exists())
            self.assertFalse((install / updater.UPDATE_PENDING_FILENAME).exists())
            self.assertEqual((install / "简历猎手.exe").read_bytes(), b"old-exe")
            self.assertEqual(updater._STATE.get("status"), "ready")

    def test_apply_excludes_own_pid_when_killing_install_dir_processes(self):
        # 回归（v27，run 44707216190）：v24 的 apply_update 调
        # _kill_install_dir_processes 时没传 exclude_pid，PowerShell 按
        # ExecutablePath 前缀把正在跑的 exe 自己也杀了——/api/update/apply
        # 还没回包进程就死了，三个 job 全在 stage=apply 报连接错误，
        # update.bat 也没来得及拉起来。apply 必须把当前 PID 排除在外。
        import os
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "staging"
            (staging / "_internal").mkdir(parents=True)
            (staging / "简历猎手.exe").write_bytes(b"new-exe")
            (staging / "_internal" / "app.bin").write_bytes(b"new-app")
            updater._STATE.update({
                "status": "ready",
                "staging_dir": str(staging),
                "info": {"version": "1.1.26"},
            })
            install = Path(tmp) / "install"
            install.mkdir()
            (install / "简历猎手.exe").write_bytes(b"old-exe")
            with (
                patch("bosshunter.updater._is_windows", return_value=True),
                patch("bosshunter.app_window.close_app_window", return_value=True),
                patch("bosshunter.browser.runtime.stop_runtime", return_value=True),
                patch("bosshunter.browser.runtime.stop_launched_browser", return_value=True),
                patch("bosshunter.updater._kill_install_dir_processes", return_value=True) as kill,
                patch("bosshunter.updater.subprocess.Popen"),
            ):
                apply_update(install, "简历猎手.exe")
            kill.assert_called_once()
            self.assertEqual(kill.call_args.kwargs.get("exclude_pid"), os.getpid())

    def test_apply_logs_when_app_window_handle_cannot_close_it(self):
        # 回归：Chrome 把 --app 交给已在跑的 Chrome 实例时，拉窗那个
        # 子进程立刻就退出了，close_app_window 直接返回 False、旧面板
        # 继续开着（2026-11-20 1.1.7 用户实拍：面板还开着、黑窗口弹
        # 在上面）。关不掉不挡更新，但要记进 update.log 供排障——
        # 真正关旧面板靠前端 apply 成功后 window.close() 自关，换文件
        # 的安全靠 bat 等旧 PID 死透，不靠关窗。
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "staging"
            (staging / "_internal").mkdir(parents=True)
            (staging / "简历猎手.exe").write_bytes(b"new-exe")
            (staging / "_internal" / "app.bin").write_bytes(b"new-app")
            updater._STATE.update({
                "status": "ready",
                "staging_dir": str(staging),
                "info": {"version": "1.1.26"},
            })
            install = Path(tmp) / "install"
            install.mkdir()
            (install / "简历猎手.exe").write_bytes(b"old-exe")
            with (
                patch("bosshunter.updater._is_windows", return_value=True),
                patch("bosshunter.app_window.close_app_window", return_value=False),
                patch("bosshunter.browser.runtime.stop_runtime", return_value=True),
                patch("bosshunter.updater._kill_install_dir_processes", return_value=True),
                patch("bosshunter.updater.subprocess.Popen"),
            ):
                apply_update(install, "简历猎手.exe")
            log = (install / "update.log").read_text(encoding="utf-8")
            self.assertIn("没能经句柄关掉", log)

    def test_apply_does_not_log_when_app_window_closed(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "staging"
            (staging / "_internal").mkdir(parents=True)
            (staging / "简历猎手.exe").write_bytes(b"new-exe")
            (staging / "_internal" / "app.bin").write_bytes(b"new-app")
            updater._STATE.update({
                "status": "ready",
                "staging_dir": str(staging),
                "info": {"version": "1.1.26"},
            })
            install = Path(tmp) / "install"
            install.mkdir()
            (install / "简历猎手.exe").write_bytes(b"old-exe")
            with (
                patch("bosshunter.updater._is_windows", return_value=True),
                patch("bosshunter.app_window.close_app_window", return_value=True),
                patch("bosshunter.browser.runtime.stop_runtime", return_value=True),
                patch("bosshunter.updater._kill_install_dir_processes", return_value=True),
                patch("bosshunter.updater.subprocess.Popen"),
            ):
                apply_update(install, "简历猎手.exe")
            self.assertFalse((install / "update.log").exists())

    def test_apply_rejects_bad_staging_before_touching_install(self):
        # 回归：staging 里没有新版 payload 时，旧实现只查安装目录的
        # exe，照样写 bat、退出程序，白换一遍。
        import sys

        with tempfile.TemporaryDirectory() as tmp:
            updater._STATE.update({
                "status": "ready",
                "staging_dir": str(Path(tmp) / "empty-staging"),
                "info": {"version": "1.1.26"},
            })
            install = Path(tmp) / "install"
            install.mkdir()
            (install / "简历猎手.exe").write_bytes(b"old-exe")
            with self.assertRaises(UpdateError) as caught:
                apply_update(install, "简历猎手.exe")
            self.assertIn("安装包", str(caught.exception))
            self.assertEqual(list(install.iterdir()), [install / "简历猎手.exe"])


    def test_apply_second_concurrent_call_is_rejected(self):
        # H-2（v31）：两个 ready 态请求同时 POST /api/update/apply，
        # 以前都会通过 ready 检查、同时 rmtree + copytree 同一个备份
        # 目录、拉起两个 bat。现在 apply_update 在锁内先把状态置
        # applying 再释放锁去做备份：第二路到占位检查时看到 applying，
        # 直接报“正在换版中”，只放行一个（谁先占位谁赢，结果恒为
        # 一成功一拒绝，与线程调度无关）。
        import threading
        import time
        from unittest.mock import patch

        self.addCleanup(lambda: setattr(updater, "_UPDATE_APPLYING", False))
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "staging"
            (staging / "_internal").mkdir(parents=True)
            (staging / "简历猎手.exe").write_bytes(b"new-exe")
            (staging / "_internal" / "app.bin").write_bytes(b"new-app")
            updater._STATE.update({
                "status": "ready",
                "staging_dir": str(staging),
                "info": {"version": "1.1.26"},
            })
            install = Path(tmp) / "install"
            install.mkdir()
            (install / "简历猎手.exe").write_bytes(b"old-exe")
            (install / "_internal").mkdir()
            (install / "_internal" / "app.bin").write_bytes(b"old-app")
            results: list[tuple[str, str]] = []
            barrier = threading.Barrier(2)

            def _call_apply():
                barrier.wait(timeout=10)
                try:
                    apply_update(install, "简历猎手.exe")
                    results.append(("ok", ""))
                except UpdateError as exc:
                    results.append(("error", str(exc)))

            def _slow_popen(*args, **kwargs):
                time.sleep(0.3)
                return None

            with (
                patch("bosshunter.updater._is_windows", return_value=True),
                patch("bosshunter.app_window.close_app_window", return_value=True),
                patch("bosshunter.browser.runtime.stop_runtime", return_value=True),
                patch("bosshunter.browser.runtime.stop_launched_browser", return_value=True),
                patch("bosshunter.updater._kill_install_dir_processes", return_value=True),
                patch("bosshunter.updater.subprocess.Popen", side_effect=_slow_popen) as popen,
            ):
                threads = [threading.Thread(target=_call_apply) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=10)
            kinds = sorted(kind for kind, _ in results)
            self.assertEqual(kinds, ["error", "ok"])
            self.assertIn("正在换版中", dict((kind, msg) for kind, msg in results)["error"])
            popen.assert_called_once()
            self.assertEqual(get_status()["status"], "applying")

    def test_apply_restores_ready_when_backup_fails(self):
        # H-2（v31）：占位之后备份失败，状态必须放回 ready——用户还
        # 能点重试，不能留在 applying 里卡死。
        from unittest.mock import patch

        # 既存泄漏：同进程里早先跑过的成功 apply 测试会把
        # _UPDATE_APPLYING 留在 True（它们从不复位）；这里先复位，
        # 后面的断言才只验“这次失败路径没置位”。
        updater._UPDATE_APPLYING = False
        self.addCleanup(lambda: setattr(updater, "_UPDATE_APPLYING", False))
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "staging"
            (staging / "_internal").mkdir(parents=True)
            (staging / "简历猎手.exe").write_bytes(b"new-exe")
            (staging / "_internal" / "app.bin").write_bytes(b"new-app")
            updater._STATE.update({
                "status": "ready",
                "staging_dir": str(staging),
                "info": {"version": "1.1.26"},
            })
            install = Path(tmp) / "install"
            install.mkdir()
            (install / "简历猎手.exe").write_bytes(b"old-exe")
            with (
                patch("bosshunter.updater._is_windows", return_value=True),
                patch("bosshunter.updater.create_backup", side_effect=OSError("磁盘满了")),
            ):
                with self.assertRaises(OSError):
                    apply_update(install, "简历猎手.exe")
            self.assertEqual(get_status()["status"], "ready")
            self.assertFalse(updater.is_update_applying())

    def test_apply_restores_ready_when_bat_launch_fails(self):
        # H-2（v31）：bat 没拉起来 = 这次更新没换成，pending 要清、
        # 状态放回 ready，用户还能点重试。
        from unittest.mock import patch

        # 同上：先复位既存泄漏，断言才只验这次失败路径。
        updater._UPDATE_APPLYING = False
        self.addCleanup(lambda: setattr(updater, "_UPDATE_APPLYING", False))
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "staging"
            (staging / "_internal").mkdir(parents=True)
            (staging / "简历猎手.exe").write_bytes(b"new-exe")
            (staging / "_internal" / "app.bin").write_bytes(b"new-app")
            updater._STATE.update({
                "status": "ready",
                "staging_dir": str(staging),
                "info": {"version": "1.1.26"},
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
                patch("bosshunter.updater.subprocess.Popen", side_effect=RuntimeError("起不来")),
            ):
                with self.assertRaises(RuntimeError):
                    apply_update(install, "简历猎手.exe")
            self.assertEqual(get_status()["status"], "ready")
            self.assertFalse((install / updater.UPDATE_PENDING_FILENAME).exists())
            self.assertFalse(updater.is_update_applying())


class CreateBackupTests(unittest.TestCase):
    def test_create_backup_keeps_complete_backup(self):
        # H-1（v31）：回滚失败留下的混装安装，重试时不能把唯一一份好
        # 备份覆盖掉——已有完整备份（exe + _internal 齐全）直接复用，
        # 不重拷。
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            (install / "简历猎手.exe").write_bytes(b"old-exe")
            (install / "_internal").mkdir()
            (install / "_internal" / "app.bin").write_bytes(b"old-app")
            backup = Path(tmp) / "update-backup-1.1.15"
            create_backup(install, backup)
            self.assertEqual((backup / "简历猎手.exe").read_bytes(), b"old-exe")
            # 模拟回滚失败后的混装安装：exe 被换成一半新的、_internal 残缺。
            (install / "简历猎手.exe").write_bytes(b"mixed-exe")
            (install / "_internal" / "app.bin").write_bytes(b"mixed-app")
            kept = create_backup(install, backup)
            self.assertEqual(kept, backup)
            self.assertEqual((backup / "简历猎手.exe").read_bytes(), b"old-exe")
            self.assertEqual((backup / "_internal" / "app.bin").read_bytes(), b"old-app")

    def test_create_backup_replaces_incomplete_backup(self):
        # 不完整的备份（上次拷一半就断了）没有保留价值，照常重拷。
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            (install / "简历猎手.exe").write_bytes(b"old-exe")
            (install / "_internal").mkdir()
            (install / "_internal" / "app.bin").write_bytes(b"old-app")
            backup = Path(tmp) / "update-backup-1.1.15"
            backup.mkdir()
            (backup / "stale.txt").write_bytes(b"half-copied")
            create_backup(install, backup)
            self.assertFalse((backup / "stale.txt").exists())
            self.assertEqual((backup / "简历猎手.exe").read_bytes(), b"old-exe")
            self.assertEqual((backup / "_internal" / "app.bin").read_bytes(), b"old-app")


class CleanupStaleUpdateTests(unittest.TestCase):
    def test_returns_false_off_windows_without_running_anything(self):
        from unittest.mock import Mock, patch

        with patch("bosshunter.updater.subprocess.run") as run:
            self.assertFalse(updater.cleanup_stale_update_processes())
        run.assert_not_called()

    def test_kills_stale_bat_cmd_first_then_findstr_and_drops_pending(self):
        from unittest.mock import Mock, patch

        # 回归：v1.0.8 的 bat 里 findstr /C:"简历猎手.exe" 卡住，黑框
        # 一直挂在面板前面。只杀 findstr 不够——管子一断旧 bat 就以为
        # 程序退了，直接去 robocopy，失败回滚会把备份里的旧版盖回刚
        # 手动重下的新版上。所以先杀跑 update.bat 的 cmd，再杀 findstr；
        # 两个都只认命令行里带简历猎手.exe、且已活超过 10 分钟的。
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            pending = install / "update-pending.json"
            pending.write_text('{"expected_version": "1.1.5"}', encoding="utf-8")
            with (
                patch("bosshunter.updater._is_windows", return_value=True),
                patch("bosshunter.updater.subprocess.run") as run,
            ):
                run.return_value = Mock(stdout="2\n", returncode=0)
                self.assertTrue(updater.cleanup_stale_update_processes(install))
            run.assert_called_once()
            argv = run.call_args.args[0]
            self.assertEqual(argv[0], "powershell.exe")
            self.assertIn("-NoProfile", argv)
            script = argv[-1]
            self.assertIn("update.bat", script)
            self.assertIn("findstr.exe", script)
            self.assertIn("简历猎手.exe", script)
            # 只杀超过 10 分钟的残留：刚拉起的新 bat 只有几秒，绝不能碰。
            self.assertIn("TotalMinutes", script)
            # cmd 在 findstr 前面：先杀 bat 的 cmd，它才没机会去换文件。
            self.assertLess(script.index("cmd.exe"), script.index("findstr.exe"))
            flags = run.call_args.kwargs.get("creationflags", 0)
            self.assertTrue(flags & 0x08000000, "清残留自己也不能弹黑框")
            # 那次挂起更新的 pending 标记清掉，不然新版本启动时会报
            # 一次并不存在的更新失败。
            self.assertFalse(pending.exists())

    def test_keeps_pending_when_nothing_was_killed(self):
        from unittest.mock import Mock, patch

        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            pending = install / "update-pending.json"
            pending.write_text('{"expected_version": "1.1.5"}', encoding="utf-8")
            with (
                patch("bosshunter.updater._is_windows", return_value=True),
                patch("bosshunter.updater.subprocess.run") as run,
            ):
                run.return_value = Mock(stdout="0\n", returncode=0)
                self.assertFalse(updater.cleanup_stale_update_processes(install))
            self.assertTrue(pending.exists())

    def test_returns_false_when_powershell_output_is_unexpected(self):
        from unittest.mock import Mock, patch

        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            pending = install / "update-pending.json"
            pending.write_text('{"expected_version": "1.1.5"}', encoding="utf-8")
            with (
                patch("bosshunter.updater._is_windows", return_value=True),
                patch("bosshunter.updater.subprocess.run") as run,
            ):
                run.return_value = Mock(stdout="oops\n", returncode=0)
                self.assertFalse(updater.cleanup_stale_update_processes(install))
            self.assertTrue(pending.exists())

    def test_returns_false_when_powershell_itself_fails(self):
        from unittest.mock import Mock, patch

        with (
            patch("bosshunter.updater._is_windows", return_value=True),
            patch("bosshunter.updater.subprocess.run", side_effect=OSError("no powershell")),
        ):
            self.assertFalse(updater.cleanup_stale_update_processes())


class UpdateRouteTests(unittest.TestCase):
    def setUp(self):
        _reset_update_state()
        self.original_base_dir = server.BASE_DIR
        self.zip_bytes = _make_zip()
        UpdateStubHandler.zip_bytes = self.zip_bytes
        UpdateStubHandler.status = 200
        UpdateStubHandler.requests = []
        UpdateStubHandler.version_payload = {
            "version": "1.1.26",
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
            self.assertEqual(data["version"], "1.1.26")
            self.assertEqual(data["current_version"], __version__)
            self.assertEqual(data["notes"], "更快更稳")

    def test_check_suppressed_after_failed_update_until_manual_recheck(self):
        # 回归：上次更新换失败后，旧实现启动检查立刻又弹同一个更新。
        # 现在非手动检查保持报错；手动强制检查是出口，清掉失败记录
        # 后才能重新查到新版。
        with tempfile.TemporaryDirectory() as tmp:
            base = self._base_dir(tmp)
            updater.write_pending_update(base, "1.1.26")
            updater.write_update_result(
                base, {"status": "failed", "expected_version": "1.1.26", "reason_code": "copy"}
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
                self.assertEqual(data["version"], "1.1.26")
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


class UpdatePathKillTests(unittest.TestCase):
    """bat :procsweep 按 ExecutablePath 杀残留进程（v24）。

    回归：旧版 exe 退出时不杀子进程，浏览器运行时的 node.exe
    （安装目录下 _internal\\patchright\\driver\\node.exe）变成孤儿，
    :procsweep 只杀同名 exe 杀不到它，robocopy 换 _internal 就报
    错误 32 文件被占用（2026-11-20 1.1.7 用户实拍：robocopy 拷到
    patchright\\driver\\node.exe 时错误 32）。换文件前必须按路径
    精确杀，绝不按映像名滥杀。
    """

    def test_bat_kills_by_executable_path_before_any_copy(self):
        script = build_update_script()
        lines = script.splitlines()
        first_robocopy = next(
            i for i, line in enumerate(lines) if "& robocopy" in line.lower()
        )
        procsweep = next(i for i, line in enumerate(lines) if line.strip() == ":procsweep")
        kill = next(
            i for i, line in enumerate(lines) if "Get-CimInstance Win32_Process" in line
        )
        self.assertLess(procsweep, kill)
        self.assertLess(kill, first_robocopy)
        self.assertIn("ExecutablePath", script)
        self.assertIn("_internal\\patchright\\driver\\node.exe", script)
        self.assertIn("Stop-Process", script)
        self.assertIn("StartsWith", script)
        # 只杀可执行路径在安装目录里的进程：前缀匹配，不是映像名。
        self.assertIn("%INSTALL_DIR%", lines[kill])

    def test_bat_never_kills_by_image_name(self):
        # 映像名滥杀会误杀用户浏览器和其他 node：taskkill node.exe /
        # chrome.exe、Get-Process -Name node 一律不许出现。同名 exe
        # 的 taskkill /IM '%EXE_NAME%' 是我们自己的程序，不在此限。
        script = build_update_script()
        lowered = script.lower()
        self.assertNotIn("taskkill /im node.exe", lowered)
        self.assertNotIn("taskkill /im chrome.exe", lowered)
        self.assertNotIn("get-process -name node", lowered)
        self.assertNotIn("/im node", lowered)
        self.assertNotIn("/im chrome", lowered)
        self.assertIn("taskkill /F /IM '%EXE_NAME%' /T", script)

    def test_bat_verifies_node_exe_is_free_after_kill(self):
        # 杀后等一等、确认文件能打开再继续；杀不掉要写进 update.log
        # 并走失败流程（:fail_stuck），不要静默跳过、不要硬拷。
        script = build_update_script()
        self.assertIn("[System.IO.File]::Open", script)
        self.assertIn("'node.exe still locked'", script)
        # 杀进程那行里的 Start-Sleep -Seconds 2 是 -Command 串里的
        # 一句，没有单独加引号（单独的延时行才有 "..." 包着）。
        self.assertIn("Start-Sleep -Seconds 2", script)
        self.assertIn('set "KILL_RC=%errorlevel%"', script)
        self.assertIn("if %KILL_RC% neq 0 goto :fail_stuck", script)
        self.assertIn("node.exe 还占着", script)


class UpdateHiddenConsoleTests(unittest.TestCase):
    """bat 里不许有裸控制台程序调用（v24 黑窗口修法，v37 根因重定）。

    回归：CREATE_NO_WINDOW 只作用于直接创建的 cmd.exe，cmd 拉起的
    孙进程（robocopy / ping / taskkill / PowerShell）没有可继承的
    控制台时会自己分配可见窗口——这就是用户看到的黑窗口。v24 曾
    以为 powershell -WindowStyle Hidden 是根治：隐藏的 PowerShell
    自带隐藏控制台、子进程继承它；v37 核实它只是第二层——窗口建出
    来后再藏起来，建窗到藏窗之间会闪一下，等主进程的轮询一秒调两
    次 PowerShell，黑框就一直闪、一直弹。根治在发起方：apply_update
    起 cmd 时带 CREATE_NEW_CONSOLE + STARTUPINFO(SW_HIDE)，cmd 出生
    就带隐藏控制台、孙进程继承它（见 test_bat_launch_flags_hide_
    the_console_window）。bat 里仍要求：所有控制台调用经
    powershell -WindowStyle Hidden 走（双击 bat 时没有隐藏控制台可
    继承，靠它兜底），重拉 exe 用 Start-Process、不用裸 start。
    """

    def test_no_bare_console_program_lines(self):
        script = build_update_script()
        for line in script.splitlines():
            stripped = line.strip().lower()
            if stripped.startswith("rem"):
                continue
            self.assertFalse(stripped.startswith("robocopy"), line)
            self.assertFalse(stripped.startswith("ping"), line)
            self.assertFalse(stripped.startswith("taskkill"), line)

    def test_no_bare_start_relaunch(self):
        # v37：4 处重拉 exe（首轮 / nostart 重试 / 回滚重开 /
        # nostart_old 重试）一律走隐藏 PowerShell 的 Start-Process，
        # 不许再有裸 start（裸 start 在无控制台继承时也会闪；且
        # Start-Process 带 -WorkingDirectory，工作目录明确是安装目录）。
        # Start-Process 不带 -WindowStyle：窗口 exe 正常显示面板、
        # 不分配控制台；带了 -WindowStyle Hidden 会把面板也藏起来。
        script = build_update_script()
        commands = [
            line
            for line in script.splitlines()
            if not line.strip().lower().startswith("rem")
        ]
        joined = "\n".join(commands)
        self.assertNotIn('start ""', joined)
        self.assertNotIn("Start-Process -WindowStyle", joined)
        relaunches = [
            line for line in commands if "Start-Process -FilePath" in line
        ]
        self.assertEqual(len(relaunches), 4)
        for line in relaunches:
            self.assertIn(
                "powershell -NoProfile -NonInteractive -WindowStyle Hidden",
                line,
                line,
            )
            self.assertIn(
                "Start-Process -FilePath '%INSTALL_DIR%\\%EXE_NAME%'"
                " -WorkingDirectory '%INSTALL_DIR%'",
                line,
                line,
            )
            self.assertIn('>> "%LOG%" 2>&1', line, line)

    def test_every_powershell_call_is_hidden(self):
        script = build_update_script()
        hidden = 0
        for line in script.splitlines():
            if line.strip().lower().startswith("rem"):
                continue
            if "powershell" not in line.lower():
                continue
            hidden += 1
            self.assertIn(
                "powershell -NoProfile -NonInteractive -WindowStyle Hidden",
                line,
                line,
            )
        # 探针 + 延时 + 杀进程 + 复制 + 回滚：隐藏调用必须有这么多，
        # 少一条就是漏了一处黑窗口。
        self.assertGreaterEqual(hidden, 10)

    def test_no_start_min(self):
        # start /min 不接受：任务栏仍可见，不算隐藏。只查真正的命令
        # 行，rem 注释里提到它不算。
        script = build_update_script()
        commands = [
            line
            for line in script.splitlines()
            if not line.strip().lower().startswith("rem")
        ]
        self.assertNotIn("start /min", "\n".join(commands).lower())


class UpdateFinalGateTests(unittest.TestCase):
    """bat :precopy 最后关口：任何 robocopy 之前再确认旧 exe PID 已死。

    回归（2026-11-20 1.1.7 用户实拍）：旧面板窗口还开着、黑窗口弹
    在上面，robocopy 撞锁。单窗口接力的要求是：旧面板先关、换文件
    全程无窗口、换完重开新面板。关窗可能失败（Chrome 交给已在跑的
    实例时句柄是空的），但换文件的安全不能靠关窗：:waitloop 等过、
    :procsweep 杀过，:precopy 里任何 robocopy 之前还要再查一次旧
    PID——活着就走失败流程，绝不带着活进程硬拷。
    """

    def test_precopy_reprobes_old_pid_before_any_robocopy(self):
        script = build_update_script()
        lines = script.splitlines()
        precopy = next(i for i, line in enumerate(lines) if line.strip() == ":precopy")
        first_robocopy = next(
            i for i, line in enumerate(lines) if "& robocopy" in line.lower()
        )
        gate = next(
            i
            for i in range(precopy, len(lines))
            if "Get-Process -Id %PID%" in lines[i]
        )
        self.assertLess(precopy, gate)
        self.assertLess(gate, first_robocopy)
        window = "\n".join(lines[gate:first_robocopy])
        self.assertIn("GetFileNameWithoutExtension('%EXE_NAME%')", window)
        self.assertIn("if %errorlevel% equ 0 goto :fail_stuck", window)

    def test_pid_is_probed_three_times(self):
        # :waitloop、:killtree 重查、:precopy 最后关口，三处都按
        # PID+进程名一起匹配（防 PID 重用误判）。
        script = build_update_script()
        probes = [
            line for line in script.splitlines() if "Get-Process -Id %PID%" in line
        ]
        self.assertEqual(len(probes), 3)
        for line in probes:
            self.assertIn("GetFileNameWithoutExtension('%EXE_NAME%')", line)


class KillInstallDirProcessesTests(unittest.TestCase):
    def test_returns_false_off_windows_without_running_anything(self):
        from unittest.mock import Mock, patch

        with patch("bosshunter.updater.subprocess.run") as run:
            self.assertFalse(_kill_install_dir_processes(Path("/tmp/nowhere")))
        run.assert_not_called()

    def test_kills_by_executable_path_prefix_and_logs(self):
        from unittest.mock import Mock, patch

        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            with (
                patch("bosshunter.updater._is_windows", return_value=True),
                patch("bosshunter.updater.subprocess.run") as run,
            ):
                run.return_value = Mock(stdout="killed=2 failed=1\n", returncode=0)
                self.assertTrue(_kill_install_dir_processes(install))
            run.assert_called_once()
            argv = run.call_args.args[0]
            self.assertEqual(argv[0], "powershell.exe")
            self.assertIn("-WindowStyle", argv)
            self.assertIn("Hidden", argv)
            script = argv[-1]
            self.assertIn("Get-CimInstance Win32_Process", script)
            self.assertIn("ExecutablePath", script)
            self.assertIn("StartsWith", script)
            self.assertIn("Stop-Process", script)
            # 只认路径前缀，绝不按映像名杀。
            self.assertNotIn("Get-Process -Name", script)
            self.assertNotIn("/IM node", script)
            self.assertNotIn("chrome.exe", script)
            flags = run.call_args.kwargs.get("creationflags", 0)
            self.assertTrue(flags & 0x08000000, "杀进程自己也不能弹黑框")
            log = (install / "update.log").read_text(encoding="utf-8")
            self.assertIn("杀了 2 个，失败 1 个", log)

    def test_excludes_given_pid_from_kill_list(self):
        # 回归（v27，run 44707216190）：传了 exclude_pid 就必须进
        # PowerShell 脚本的排除条件，否则按路径杀进程时会把自己也杀掉。
        from unittest.mock import Mock, patch

        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            with (
                patch("bosshunter.updater._is_windows", return_value=True),
                patch("bosshunter.updater.subprocess.run") as run,
            ):
                run.return_value = Mock(stdout="killed=0 failed=0\n", returncode=0)
                self.assertTrue(_kill_install_dir_processes(install, exclude_pid=4242))
            script = run.call_args.args[0][-1]
            self.assertIn("-and $p.ProcessId -ne 4242", script)

    def test_returns_false_when_powershell_itself_fails(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            with (
                patch("bosshunter.updater._is_windows", return_value=True),
                patch("bosshunter.updater.subprocess.run", side_effect=OSError("no powershell")),
            ):
                self.assertFalse(_kill_install_dir_processes(install))
            log = (install / "update.log").read_text(encoding="utf-8")
            self.assertIn("失败", log)


class UpdateCacheTests(unittest.TestCase):
    """已下载包落盘复用（v24）：data/update-cache/。

    回归：旧版 exe 退出时内存里的 zip/staging 路径随 os._exit 一起
    消失，包其实还在磁盘上，但重开后后端认不出它，只能重下。现在
    下载+校验通过就把包和指针落盘，启动/查状态时校验通过就恢复
    ready 态，不重新下载。
    """

    def setUp(self):
        _reset_update_state()
        self.zip_bytes = _make_zip()
        UpdateStubHandler.zip_bytes = self.zip_bytes
        UpdateStubHandler.status = 200
        UpdateStubHandler.requests = []
        UpdateStubHandler.version_payload = {
            "version": "1.1.26",
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

    def _info(self, version="1.1.14", zip_bytes=None):
        data = zip_bytes if zip_bytes is not None else self.zip_bytes
        return {
            "version": version,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "notes": "",
        }

    def _write_zip(self, tmp, zip_bytes=None):
        data = zip_bytes if zip_bytes is not None else self.zip_bytes
        zip_path = Path(tmp) / "rel.zip"
        zip_path.write_bytes(data)
        return zip_path

    def _cache_dir(self, install):
        return install / "data" / "update-cache"

    def test_save_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            zip_path = self._write_zip(tmp)
            manifest = save_cached_update(install, self._info(), zip_path)
            assert manifest is not None
            self.assertEqual(manifest["version"], "1.1.14")
            self.assertEqual(manifest["sha256"], hashlib.sha256(self.zip_bytes).hexdigest())
            self.assertEqual(manifest["size"], len(self.zip_bytes))
            self.assertIn("downloaded_at", manifest)
            cached_zip = self._cache_dir(install) / "jianli-lieshou-1.1.14.zip"
            self.assertTrue(cached_zip.is_file())
            self.assertEqual(cached_zip.read_bytes(), self.zip_bytes)
            self.assertEqual(manifest["zip_path"], str(cached_zip))
            loaded = load_cached_update(install)
            assert loaded is not None
            self.assertEqual(loaded["version"], "1.1.14")
            self.assertEqual(loaded["sha256"], manifest["sha256"])
            self.assertEqual(loaded["size"], len(self.zip_bytes))
            self.assertEqual(loaded["zip_path"], str(cached_zip))

    def test_load_rejects_corrupt_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            save_cached_update(install, self._info(), self._write_zip(tmp))
            cached_zip = self._cache_dir(install) / "jianli-lieshou-1.1.14.zip"
            with open(cached_zip, "ab") as fh:
                fh.write(b"junk")
            self.assertIsNone(load_cached_update(install))

    def test_load_rejects_missing_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            save_cached_update(install, self._info(), self._write_zip(tmp))
            (self._cache_dir(install) / "jianli-lieshou-1.1.14.zip").unlink()
            self.assertIsNone(load_cached_update(install))

    def test_load_rejects_missing_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            self.assertIsNone(load_cached_update(install))

    def test_load_rejects_tampered_zip_path(self):
        # manifest 把 zip_path 指到缓存目录外面：不认。
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            save_cached_update(install, self._info(), self._write_zip(tmp))
            elsewhere = Path(tmp) / "evil.zip"
            elsewhere.write_bytes(self.zip_bytes)
            manifest_path = self._cache_dir(install) / "update-ready.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["zip_path"] = str(elsewhere)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertIsNone(load_cached_update(install))

    def test_load_rejects_wrong_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            save_cached_update(install, self._info(), self._write_zip(tmp))
            manifest_path = self._cache_dir(install) / "update-ready.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["size"] = manifest["size"] + 1
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertIsNone(load_cached_update(install))

    def test_save_rejects_sha_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            bad_info = dict(self._info(), sha256="b" * 64)
            self.assertIsNone(save_cached_update(install, bad_info, self._write_zip(tmp)))
            self.assertFalse((self._cache_dir(install) / "update-ready.json").exists())

    def test_version_mismatch_clears_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            save_cached_update(install, self._info(), self._write_zip(tmp))
            other = dict(self._info(), version="1.1.14", sha256="c" * 64)
            self.assertFalse(_restore_from_cache(install, other))
            self.assertFalse((self._cache_dir(install) / "update-ready.json").exists())
            self.assertEqual(list(self._cache_dir(install).glob("*.zip")), [])

    def test_restore_rebuilds_staging_when_missing(self):
        from bosshunter.updater import _remove_quietly

        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            save_cached_update(install, self._info(), self._write_zip(tmp))
            self.assertTrue(_restore_from_cache(install, self._info()))
            first_staging = Path(get_status()["staging_dir"])
            self.assertTrue((first_staging / "简历猎手.exe").is_file())
            # 上次退出把 staging 丢了：从缓存 zip 重新解压，必须还能 ready。
            _remove_quietly(first_staging.parent)
            self.assertFalse(first_staging.exists())
            self.assertTrue(_restore_from_cache(install, self._info()))
            second_staging = Path(get_status()["staging_dir"])
            self.assertTrue((second_staging / "简历猎手.exe").is_file())
            _remove_quietly(second_staging.parent)

    def test_restore_rejects_bad_payload_and_clears_cache(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("简历猎手-windows/readme.txt", b"hi")
        bad = buf.getvalue()
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            save_cached_update(install, self._info(zip_bytes=bad), self._write_zip(tmp, bad))
            self.assertFalse(_restore_from_cache(install, self._info(zip_bytes=bad)))
            self.assertFalse((self._cache_dir(install) / "update-ready.json").exists())

    def test_download_worker_persists_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            run_check(self._config(), install_dir=install)
            self.assertTrue(get_status()["update_available"])
            start_download(self._config(), install_dir=install)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if get_status()["status"] == "ready":
                    break
                time.sleep(0.2)
            self.assertEqual(get_status()["status"], "ready", get_status())
            loaded = load_cached_update(install)
            assert loaded is not None
            self.assertEqual(loaded["version"], "1.1.26")
            self.assertTrue(Path(loaded["zip_path"]).is_file())

    def test_start_download_restores_from_cache_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            save_cached_update(install, self._info(), self._write_zip(tmp))
            updater._STATE.update({
                "status": "idle",
                "update_available": True,
                "info": self._info(),
                "staging_dir": None,
                "zip_path": None,
            })
            # 中转地址根本不通：恢复走缓存就一次也不用联网。
            dead = {"ai": {"relay_url": "http://127.0.0.1:1", "relay_token": "x"}}
            state = start_download(dead, install_dir=install)
            self.assertEqual(state["status"], "ready", state)
            self.assertTrue(Path(state["staging_dir"]).is_dir())
            self.assertTrue((Path(state["staging_dir"]) / "简历猎手.exe").is_file())
            self.assertEqual(UpdateStubHandler.requests, [])

    def test_successful_swap_clears_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            info = self._info(version=__version__)
            save_cached_update(install, info, self._write_zip(tmp))
            self.assertIsNotNone(load_cached_update(install))
            write_pending_update(install, __version__)
            write_update_result(install, {"status": "applied", "expected_version": __version__, "reason_code": ""})
            verdict = consume_pending_update(install)
            assert verdict is not None
            self.assertEqual(verdict["status"], "ok")
            self.assertIsNone(load_cached_update(install))

    def test_clear_cached_update_removes_manifest_and_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "install"
            install.mkdir()
            save_cached_update(install, self._info(), self._write_zip(tmp))
            clear_cached_update(install)
            self.assertFalse((self._cache_dir(install) / "update-ready.json").exists())
            self.assertEqual(list(self._cache_dir(install).glob("*.zip")), [])
            self.assertIsNone(load_cached_update(install))


if __name__ == "__main__":
    unittest.main()
