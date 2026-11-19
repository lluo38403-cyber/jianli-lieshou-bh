"""Time-based card auth tests.

Covers: HWID hashing (same input -> same id, different input ->
different id, only the hash leaves the module), HMAC request/response
signing and verification, the monotonic countdown, the offline grace
cache, and the full activation flow against a stub relay that
implements the same contract as the real one (trial 2h, day +86400,
week +604800, stacking, single-machine card binding).

The stub relay verifies the request signature and signs its
responses, so a bad signature or a tampered response is caught
exactly like it would be against the real relay.
"""

import hashlib
import hmac
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx

from bosshunter.auth import client as auth_client_module
from bosshunter.auth.client import (
    AuthClient,
    get_auth_display_config,
    normalize_card_code,
    sign_request,
    sign_response,
    verify_response_signature,
)
from bosshunter.auth.hwid import collect_hardware_values, get_machine_id, machine_id


TEST_TOKEN = "test-app-token-abcdef"
TEST_MACHINE = "a" * 64
DAY = 86400
WEEK = 604800


class StubRelay:
    """In-memory relay implementing the /api/auth contract."""

    def __init__(self, now: int = 1780000000):
        self.now = now
        self.records: dict[str, int] = {}
        self.cards: dict[str, dict] = {
            "JL-D-AAAA-BBBB-CCCC": {"kind": "day", "bound": None, "used": False},
            "JL-W-DDDD-EEEE-FFFF": {"kind": "week", "bound": None, "used": False},
            "JL-D-GGGG-HHHH-JJJJ": {"kind": "day", "bound": None, "used": False},
        }

    def _sign_response(self, machine: str, expire_at: int, remaining: int) -> str:
        return sign_response(TEST_TOKEN, machine, expire_at, remaining, self.now)

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("X-App-Token") != TEST_TOKEN:
            return httpx.Response(401, json={"error": "unauthorized"})
        try:
            body = json.loads(request.content.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return httpx.Response(400, json={"error": "bad_request"})
        machine = str(body.get("machine_id") or "")
        timestamp = body.get("timestamp")
        signature = str(body.get("signature") or "")
        if not isinstance(timestamp, int) or abs(timestamp - self.now) > 300:
            return httpx.Response(400, json={"error": "stale_timestamp"})
        expected = sign_request(TEST_TOKEN, machine, timestamp)
        if not hmac.compare_digest(expected, signature):
            return httpx.Response(400, json={"error": "bad_signature"})
        if request.url.path == "/api/auth/info":
            return self._info(machine)
        if request.url.path == "/api/auth/activate":
            return self._activate(machine, str(body.get("card_code") or ""))
        return httpx.Response(404, json={"error": "not_found"})

    def _info(self, machine: str) -> httpx.Response:
        if machine not in self.records:
            self.records[machine] = self.now + 7200
            status = "trial"
        else:
            status = "active" if self.records[machine] > self.now else "expired"
        expire_at = self.records[machine]
        remaining = max(0, expire_at - self.now)
        return httpx.Response(
            200,
            json={
                "machine_id": machine,
                "expire_at": expire_at,
                "remaining_seconds": remaining,
                "status": status,
                "signature": self._sign_response(machine, expire_at, remaining),
                "server_time": self.now,
            },
        )

    def _activate(self, machine: str, code: str) -> httpx.Response:
        card = self.cards.get(code)
        if card is None:
            return httpx.Response(400, json={"error": "invalid_card"})
        if card["used"]:
            if card["bound"] == machine:
                return httpx.Response(400, json={"error": "card_used"})
            return httpx.Response(400, json={"error": "card_bound_other"})
        added = DAY if card["kind"] == "day" else WEEK
        current = self.records.get(machine, self.now)
        new_expire = max(self.now, current) + added
        self.records[machine] = new_expire
        card["used"] = True
        card["bound"] = machine
        return httpx.Response(
            200,
            json={
                "expire_at": new_expire,
                "remaining_seconds": new_expire - self.now,
                "added_seconds": added,
                "card_type": card["kind"],
            },
        )


def make_client(relay: StubRelay, tmpdir: str, machine: str = TEST_MACHINE) -> AuthClient:
    transport = httpx.MockTransport(relay.handler)
    config = {"ai": {"relay_url": "http://stub-relay", "relay_token": TEST_TOKEN}}
    return AuthClient(config, cache_path=Path(tmpdir) / "auth_cache.json", transport=transport, machine_id=machine)


class HwidTests(unittest.TestCase):
    def test_same_input_same_id(self):
        values = {"baseboard": "ABC123", "cpu": "CPU9", "disk": "DISK7"}
        self.assertEqual(machine_id(values), machine_id(values))

    def test_different_input_different_id(self):
        base = {"baseboard": "ABC123", "cpu": "CPU9", "disk": "DISK7"}
        other = dict(base, disk="DISK8")
        self.assertNotEqual(machine_id(base), machine_id(other))

    def test_output_is_sha256_hex_only(self):
        result = machine_id({"baseboard": "x", "cpu": "y", "disk": "z"})
        self.assertEqual(len(result), 64)
        int(result, 16)
        expected = hashlib.sha256("x|y|z".encode("utf-8")).hexdigest()
        self.assertEqual(result, expected)

    def test_raw_values_never_in_output(self):
        values = {"baseboard": "SECRET-BOARD-1", "cpu": "SECRET-CPU-1", "disk": "SECRET-DISK-1"}
        result = machine_id(values)
        for raw in values.values():
            self.assertNotIn(raw.lower(), result)

    def test_injected_collectors_used(self):
        values = collect_hardware_values(
            collectors={"baseboard": lambda: "B1", "cpu": lambda: "C1", "disk": lambda: "D1"}
        )
        self.assertEqual(values["baseboard"], "b1")
        self.assertEqual(values["cpu"], "c1")
        self.assertEqual(values["disk"], "d1")

    def test_collector_failure_yields_empty_not_crash(self):
        def _boom() -> str:
            raise RuntimeError("no wmic here")

        values = collect_hardware_values(
            collectors={"baseboard": _boom, "cpu": lambda: "C1", "disk": lambda: "D1"}
        )
        self.assertEqual(values["baseboard"], "")
        self.assertTrue(machine_id(values))

    def test_get_machine_id_returns_hash_and_flag(self):
        result, dev_fallback = get_machine_id()
        self.assertEqual(len(result), 64)
        self.assertIsInstance(dev_fallback, bool)


class HmacTests(unittest.TestCase):
    def test_sign_request_deterministic(self):
        self.assertEqual(sign_request("tok", "m1", 1000), sign_request("tok", "m1", 1000))
        self.assertNotEqual(sign_request("tok", "m1", 1000), sign_request("tok", "m1", 1001))
        self.assertNotEqual(sign_request("tok", "m1", 1000), sign_request("other", "m1", 1000))

    def test_verify_response_signature(self):
        payload = {
            "machine_id": TEST_MACHINE,
            "expire_at": 1780007200,
            "remaining_seconds": 7200,
            "signature": sign_response(TEST_TOKEN, TEST_MACHINE, 1780007200, 7200, 1780000000),
            "server_time": 1780000000,
        }
        self.assertTrue(verify_response_signature(TEST_TOKEN, payload))
        tampered = dict(payload, remaining_seconds=999999)
        self.assertFalse(verify_response_signature(TEST_TOKEN, tampered))
        self.assertFalse(verify_response_signature("wrong-token", payload))
        self.assertFalse(verify_response_signature(TEST_TOKEN, {"machine_id": TEST_MACHINE}))


class CountdownTests(unittest.TestCase):
    def test_monotonic_countdown_ignores_wall_clock_jumps(self):
        relay = StubRelay()
        with tempfile.TemporaryDirectory() as tmpdir:
            client = make_client(relay, tmpdir)
            with mock.patch("time.time", return_value=relay.now), mock.patch(
                "time.monotonic", return_value=1000.0
            ):
                self.assertTrue(client.sync())
                self.assertEqual(client.remaining_seconds(), 7200)
            # Wall clock jumps forward 10 years: live countdown must not move.
            with mock.patch("time.time", return_value=relay.now + 10 * 365 * 86400), mock.patch(
                "time.monotonic", return_value=1000.0
            ):
                self.assertEqual(client.remaining_seconds(), 7200)
            # Monotonic advances 60 s: countdown drops by 60.
            with mock.patch("time.monotonic", return_value=1060.0):
                self.assertEqual(client.remaining_seconds(), 7140)
            # Monotonic past expiry clamps at zero.
            with mock.patch("time.monotonic", return_value=1000.0 + 7200 + 5):
                self.assertEqual(client.remaining_seconds(), 0)
                self.assertFalse(client.is_active())
                self.assertTrue(client.known_expired())

    def test_bad_signature_response_is_distrusted(self):
        relay = StubRelay()
        original = relay._sign_response

        def _bad_sign(machine: str, expire_at: int, remaining: int) -> str:
            return sign_response("someone-elses-token", machine, expire_at, remaining, relay.now)

        relay._sign_response = _bad_sign  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory() as tmpdir:
            client = make_client(relay, tmpdir)
            with mock.patch("time.time", return_value=relay.now), mock.patch(
                "time.monotonic", return_value=1000.0
            ):
                self.assertFalse(client.sync())
                self.assertIsNone(client.remaining_seconds())
                self.assertEqual(client.status, "unknown")
        relay._sign_response = original

    def test_offline_grace_uses_cache_up_to_24h(self):
        relay = StubRelay()
        with tempfile.TemporaryDirectory() as tmpdir:
            client = make_client(relay, tmpdir)
            with mock.patch("time.time", return_value=relay.now), mock.patch(
                "time.monotonic", return_value=1000.0
            ):
                self.assertTrue(client.sync())
            # Relay goes away; a fresh client loads the cache (grace mode).
            # The fresh client must be built under the same mocked clock,
            # or its cache load would see the real wall time and drop it.
            with mock.patch("time.time", return_value=relay.now + 3600):
                relay_down = make_client(relay, tmpdir)

                def _down(request: httpx.Request) -> httpx.Response:
                    raise httpx.ConnectError("down", request=request)

                relay_down._transport = httpx.MockTransport(_down)
                self.assertTrue(relay_down.offline)
                self.assertEqual(relay_down.remaining_seconds(), 7200 - 3600)
                self.assertTrue(relay_down.is_active())
                self.assertFalse(relay_down.sync())
            # Past 24 h of staleness the grace ends.
            with mock.patch("time.time", return_value=relay.now + 25 * 3600):
                self.assertEqual(relay_down.remaining_seconds(), 0)
                self.assertTrue(relay_down.known_expired())


class ActivationFlowTests(unittest.TestCase):
    def _synced(self, relay: StubRelay, tmpdir: str, machine: str = TEST_MACHINE) -> AuthClient:
        client = make_client(relay, tmpdir, machine)
        with mock.patch("time.time", return_value=relay.now), mock.patch(
            "time.monotonic", return_value=1000.0
        ):
            self.assertTrue(client.sync())
        return client

    def test_new_machine_gets_2h_trial(self):
        relay = StubRelay()
        with tempfile.TemporaryDirectory() as tmpdir:
            client = self._synced(relay, tmpdir)
            self.assertEqual(client.status, "trial")
            with mock.patch("time.monotonic", return_value=1000.0):
                self.assertEqual(client.remaining_seconds(), 7200)
                self.assertTrue(client.is_active())

    def test_day_card_stacks_on_trial(self):
        relay = StubRelay()
        with tempfile.TemporaryDirectory() as tmpdir:
            client = self._synced(relay, tmpdir)
            with mock.patch("time.time", return_value=relay.now), mock.patch(
                "time.monotonic", return_value=1000.0
            ):
                result = client.activate("JL-D-AAAA-BBBB-CCCC")
            self.assertTrue(result["ok"])
            self.assertEqual(result["added_seconds"], DAY)
            self.assertEqual(result["card_type"], "day")
            with mock.patch("time.monotonic", return_value=1000.0):
                self.assertEqual(client.remaining_seconds(), 7200 + DAY)

    def test_week_card_stacks_on_top_of_day_card(self):
        relay = StubRelay()
        with tempfile.TemporaryDirectory() as tmpdir:
            client = self._synced(relay, tmpdir)
            with mock.patch("time.time", return_value=relay.now), mock.patch(
                "time.monotonic", return_value=1000.0
            ):
                self.assertTrue(client.activate("JL-D-AAAA-BBBB-CCCC")["ok"])
                result = client.activate("JL-W-DDDD-EEEE-FFFF")
            self.assertTrue(result["ok"])
            self.assertEqual(result["added_seconds"], WEEK)
            with mock.patch("time.monotonic", return_value=1000.0):
                self.assertEqual(client.remaining_seconds(), 7200 + DAY + WEEK)

    def test_card_reuse_by_same_machine_reports_used(self):
        relay = StubRelay()
        with tempfile.TemporaryDirectory() as tmpdir:
            client = self._synced(relay, tmpdir)
            with mock.patch("time.time", return_value=relay.now), mock.patch(
                "time.monotonic", return_value=1000.0
            ):
                self.assertTrue(client.activate("JL-D-AAAA-BBBB-CCCC")["ok"])
                result = client.activate("JL-D-AAAA-BBBB-CCCC")
            self.assertFalse(result["ok"])
            self.assertIn("已经用过", result["error"])

    def test_card_bound_to_other_machine_reports_bound_other(self):
        relay = StubRelay()
        other_machine = "b" * 64
        with tempfile.TemporaryDirectory() as tmpdir:
            first = self._synced(relay, tmpdir)
            with mock.patch("time.time", return_value=relay.now), mock.patch(
                "time.monotonic", return_value=1000.0
            ):
                self.assertTrue(first.activate("JL-D-AAAA-BBBB-CCCC")["ok"])
            second = self._synced(relay, tmpdir, machine=other_machine)
            with mock.patch("time.time", return_value=relay.now), mock.patch(
                "time.monotonic", return_value=1000.0
            ):
                result = second.activate("JL-D-AAAA-BBBB-CCCC")
            self.assertFalse(result["ok"])
            self.assertIn("别的电脑", result["error"])

    def test_invalid_card_reports_plain_message(self):
        relay = StubRelay()
        with tempfile.TemporaryDirectory() as tmpdir:
            client = self._synced(relay, tmpdir)
            with mock.patch("time.time", return_value=relay.now), mock.patch(
                "time.monotonic", return_value=1000.0
            ):
                result = client.activate("JL-D-XXXX-YYYY-ZZZZ")
            self.assertFalse(result["ok"])
            self.assertIn("不对", result["error"])

    def test_card_code_normalized(self):
        self.assertEqual(normalize_card_code(" jl-d-aaaa-bbbb-cccc "), "JL-D-AAAA-BBBB-CCCC")
        self.assertEqual(normalize_card_code(""), "")

    def test_activate_offline_reports_network_message(self):
        relay = StubRelay()
        with tempfile.TemporaryDirectory() as tmpdir:
            client = self._synced(relay, tmpdir)

            def _down(request: httpx.Request) -> httpx.Response:
                raise httpx.ConnectError("down", request=request)

            client._transport = httpx.MockTransport(_down)
            with mock.patch("time.time", return_value=relay.now):
                result = client.activate("JL-D-GGGG-HHHH-JJJJ")
            self.assertFalse(result["ok"])
            self.assertIn("连不上", result["error"])


class NoAuthSyncEnvTests(unittest.TestCase):
    """JIANLI_NO_AUTH_SYNC=1 (CI Windows 冒烟测试) 跳过中转同步：
    临时跑机不拿机器码去中转薅试用，也不产生任何试用记录。"""

    def test_sync_skipped_without_touching_relay(self):
        relay = StubRelay()
        calls: list[str] = []

        def counting_handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            return relay.handler(request)

        config = {"ai": {"relay_url": "http://stub-relay", "relay_token": TEST_TOKEN}}
        with tempfile.TemporaryDirectory() as tmpdir:
            client = AuthClient(
                config,
                cache_path=Path(tmpdir) / "auth_cache.json",
                transport=httpx.MockTransport(counting_handler),
                machine_id=TEST_MACHINE,
            )
            with mock.patch.dict("os.environ", {"JIANLI_NO_AUTH_SYNC": "1"}):
                self.assertFalse(client.sync())
            self.assertEqual(calls, [])
            self.assertEqual(relay.records, {})

    def test_sync_still_works_without_env_flag(self):
        relay = StubRelay()
        with tempfile.TemporaryDirectory() as tmpdir:
            client = make_client(relay, tmpdir)
            with mock.patch.dict("os.environ", {}, clear=False):
                os.environ.pop("JIANLI_NO_AUTH_SYNC", None)
                with mock.patch("time.time", return_value=relay.now), mock.patch(
                    "time.monotonic", return_value=1000.0
                ):
                    self.assertTrue(client.sync())
            self.assertEqual(client.status, "trial")


class DisplayConfigTests(unittest.TestCase):
    def test_defaults_apply_when_section_missing(self):
        cfg = get_auth_display_config({})
        # 购买链接默认留空：没有链接时状态栏不显示购买按钮。
        self.assertEqual(cfg["buy_url"], "")
        self.assertEqual(cfg["wechat_id"], "jmlq2004")
        self.assertEqual(cfg["wechat_qr_path"], "/wechat-qr.png")

    def test_config_values_override_defaults(self):
        cfg = get_auth_display_config(
            {"auth": {"buy_url": "https://example.com/shop", "wechat_id": "wx123", "wechat_qr_path": "/qr.png"}}
        )
        self.assertEqual(cfg["buy_url"], "https://example.com/shop")
        self.assertEqual(cfg["wechat_id"], "wx123")
        self.assertEqual(cfg["wechat_qr_path"], "/qr.png")


from http.server import BaseHTTPRequestHandler


class _AuthStubHTTPHandler(BaseHTTPRequestHandler):
    """Adapts the in-memory StubRelay to a real local HTTP server."""

    relay: StubRelay

    def log_message(self, *args):
        pass

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler naming
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        req = httpx.Request(
            "POST",
            f"http://127.0.0.1{self.path}",
            headers=dict(self.headers.items()),
            content=raw,
        )
        resp = _AuthStubHTTPHandler.relay.handler(req)
        self.send_response(resp.status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp.content)))
        self.end_headers()
        self.wfile.write(resp.content)


class AuthRouteTests(unittest.TestCase):
    def setUp(self):
        from http.server import ThreadingHTTPServer

        from bosshunter.web import server

        self.server = server
        # The stub relay runs on real wall time here so the route's
        # background sync thread (which runs outside any mock) succeeds.
        self.relay = StubRelay(now=int(time.time()))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _AuthStubHTTPHandler)
        _AuthStubHTTPHandler.relay = self.relay
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp.name
        self._orig_get_auth_client = server.get_auth_client
        self._orig_base_dir = server.BASE_DIR

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        self.server.get_auth_client = self._orig_get_auth_client
        self.server.reset_auth_client()
        self.server.set_base_dir(self._orig_base_dir)
        self._tmp.cleanup()

    def _client(self, machine: str = TEST_MACHINE) -> AuthClient:
        config = {"ai": {"relay_url": f"http://127.0.0.1:{self.port}", "relay_token": TEST_TOKEN}}
        return AuthClient(config, cache_path=Path(self.tmpdir) / "auth_cache.json", machine_id=machine)

    def _request(self, path, method="GET", json_body=None):
        import io

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
        response_iter = self.server.app(environ, start_response)
        try:
            body = b"".join(
                chunk if isinstance(chunk, bytes) else chunk.encode("utf-8") for chunk in response_iter
            ).decode("utf-8")
        finally:
            close = getattr(response_iter, "close", None)
            if close:
                close()
        return status_headers["status"], body

    def test_status_reports_trial_after_sync(self):
        client = self._client()
        self.server.get_auth_client = lambda: client
        # First call returns the not-yet-synced state and kicks off a
        # background sync; the next call reports the fresh state.
        status, body = self._request("/api/auth/status")
        self.assertTrue(status.startswith("200"), body)
        self.assertEqual(json.loads(body)["status"], "unknown")
        deadline = time.monotonic() + 5.0
        while client.status == "unknown" and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(client.status, "trial")
        status, body = self._request("/api/auth/status")
        self.assertTrue(status.startswith("200"), body)
        data = json.loads(body)
        self.assertEqual(data["status"], "trial")
        self.assertGreaterEqual(data["remaining_seconds"], 7190)
        self.assertLessEqual(data["remaining_seconds"], 7200)
        self.assertEqual(data["machine_id_short"], TEST_MACHINE[:12])

    def test_activate_route_adds_day_card(self):
        client = self._client()
        self.server.get_auth_client = lambda: client
        self.assertTrue(client.sync())  # trial first, then the card stacks
        status, body = self._request(
            "/api/auth/activate", method="POST", json_body={"card_code": "JL-D-AAAA-BBBB-CCCC"}
        )
        self.assertTrue(status.startswith("200"), body)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertGreaterEqual(data["remaining_seconds"], 7200 + DAY - 10)
        self.assertLessEqual(data["remaining_seconds"], 7200 + DAY)

    def test_activate_route_rejects_bad_card_with_plain_message(self):
        client = self._client()
        self.server.get_auth_client = lambda: client
        self.assertTrue(client.sync())
        status, body = self._request(
            "/api/auth/activate", method="POST", json_body={"card_code": "JL-D-NOPE-NOPE-NOPE"}
        )
        self.assertTrue(status.startswith("400"), body)
        self.assertIn("不对", json.loads(body)["error"])

    def test_config_route_returns_display_values(self):
        import yaml

        base = Path(self.tmpdir)
        (base / "config.yaml").write_text(
            yaml.safe_dump({"auth": {"buy_url": "https://example.com/shop", "wechat_id": "wx9"}}),
            encoding="utf-8",
        )
        self.server.set_base_dir(base)
        status, body = self._request("/api/auth/config")
        self.assertTrue(status.startswith("200"), body)
        data = json.loads(body)
        self.assertEqual(data["buy_url"], "https://example.com/shop")
        self.assertEqual(data["wechat_id"], "wx9")
        self.assertEqual(data["wechat_qr_path"], "/wechat-qr.png")


class DeliveryGateTests(unittest.TestCase):
    def _fake_task(self):
        from types import SimpleNamespace

        return SimpleNamespace(
            stop_requested=threading.Event(), stop_reason=None, logs=[], context={}
        )

    def _expired_client(self, relay: StubRelay, tmpdir: str) -> AuthClient:
        relay.records[TEST_MACHINE] = relay.now - 100
        client = make_client(relay, tmpdir)
        with mock.patch("time.time", return_value=relay.now), mock.patch(
            "time.monotonic", return_value=5000.0
        ):
            self.assertTrue(client.sync())
        self.assertTrue(client.known_expired())
        return client

    def test_expired_auth_stops_delivery_task(self):
        from bosshunter.web import server

        relay = StubRelay()
        with tempfile.TemporaryDirectory() as tmpdir:
            client = self._expired_client(relay, tmpdir)
            task = self._fake_task()
            with mock.patch("time.monotonic", return_value=5000.0):
                stopped = server._auth_stop_if_expired(task, client)
            self.assertTrue(stopped)
            self.assertTrue(task.stop_requested.is_set())
            self.assertEqual(task.stop_reason, "授权已到期，请输入天卡或周卡继续使用")
            self.assertTrue(any("授权已到期" in entry for entry in task.logs))

    def test_active_auth_does_not_stop_delivery_task(self):
        from bosshunter.web import server

        relay = StubRelay()
        with tempfile.TemporaryDirectory() as tmpdir:
            client = make_client(relay, tmpdir)
            with mock.patch("time.time", return_value=relay.now), mock.patch(
                "time.monotonic", return_value=5000.0
            ):
                self.assertTrue(client.sync())
            task = self._fake_task()
            with mock.patch("time.monotonic", return_value=5000.0):
                self.assertFalse(server._auth_stop_if_expired(task, client))
            self.assertFalse(task.stop_requested.is_set())

    def test_watchdog_stops_task_when_time_runs_out(self):
        from bosshunter.web import server

        relay = StubRelay()
        with tempfile.TemporaryDirectory() as tmpdir:
            client = self._expired_client(relay, tmpdir)
            task = self._fake_task()
            worker = threading.Thread(
                target=server._auth_watchdog, args=(task, client), kwargs={"interval_seconds": 0.05}
            )
            worker.start()
            worker.join(timeout=5)
            self.assertTrue(task.stop_requested.is_set())
            self.assertEqual(task.stop_reason, "授权已到期，请输入天卡或周卡继续使用")

    def test_preflight_blocks_full_mode_when_expired(self):
        from bosshunter.web import server

        relay = StubRelay()
        with tempfile.TemporaryDirectory() as tmpdir:
            client = self._expired_client(relay, tmpdir)
            original = server.get_auth_client
            server.get_auth_client = lambda: client
            try:
                with mock.patch("time.monotonic", return_value=5000.0):
                    messages = server._preflight_messages("full", {"profile": {}})
            finally:
                server.get_auth_client = original
                server.reset_auth_client()
            self.assertTrue(any("授权已到期" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
