"""Cloud relay client tests against a local stub relay server.

No real provider key is used anywhere here: the stub mimics the relay
contract (``POST /api/ai/chat`` with ``X-App-Token``, OpenAI-compatible
JSON reply; ``GET /api/ai/health``). The exe never carries a provider
key; only the relay token (abuse guard, rotatable server-side) is in
the local config. Direct provider code paths are untouched (see
tests/test_zhipu_provider.py).
"""

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import yaml

from bosshunter.ai.credentials import (
	AIRequestError,
	ai_failure_falls_back,
	call_ai_text,
	call_relay_text,
	get_ai_api_key,
	get_ai_key_source,
	get_ai_service_label,
	get_relay_token,
	get_relay_url,
	list_ai_models,
)
from bosshunter.ai.scorer import _score_job_with_ai
from bosshunter.config import load_config
from bosshunter.web.preflight import check_ai_connection, collect_preflight_checks

BASE_JOB = {
	"title": "Python 后端工程师",
	"company": "测试公司",
	"salary": "15-25K",
	"experience": "3-5年",
	"city": "上海",
	"education": "本科",
	"jd": "负责 Python 后端开发，要求熟悉 FastAPI。",
	"url": "https://jobs.zhaopin.com/test",
}

SCORE_REPLY = json.dumps({
	"core_duties": {"score": 35, "evidence": "负责 Python 后端开发，匹配核心职责"},
	"transferable_evidence": {"score": 20, "evidence": "有 FastAPI 项目经验"},
	"hard_requirements": {"score": 12, "evidence": "本科学历满足要求"},
	"tools_industry": {"score": 8, "evidence": "熟悉 Python 技术栈"},
	"practical_fit": {"score": 9, "evidence": "薪资与城市匹配"},
	"caps": [],
	"reason": "岗位与简历高度匹配",
	"missing": "",
	"role_summary": "Python 后端开发",
	"hard_gaps": [],
}, ensure_ascii=False)


class RelayStubHandler(BaseHTTPRequestHandler):
	"""Minimal stub for the relay contract: /api/ai/chat + /api/ai/health."""

	mode = "ok"
	text = "ok"
	health = {"ok": True, "provider": "zhipu", "model": "glm-4.7-flash", "ai_enabled": True}
	requests: list = []

	def _send_json(self, status, payload):
		body = json.dumps(payload).encode("utf-8")
		self.send_response(status)
		self.send_header("Content-Type", "application/json")
		self.send_header("Content-Length", str(len(body)))
		self.end_headers()
		self.wfile.write(body)

	def _read_json(self):
		length = int(self.headers.get("Content-Length") or 0)
		raw = self.rfile.read(length) if length else b""
		try:
			return json.loads(raw.decode("utf-8") or "{}")
		except json.JSONDecodeError:
			return {}

	def do_POST(self):
		data = self._read_json()
		type(self).requests.append(("POST", self.path, dict(self.headers), data))
		if self.path == "/api/ai/chat":
			mode = type(self).mode
			if mode == "auth_error":
				return self._send_json(401, {"error": "invalid token"})
			if mode == "rate_limit":
				return self._send_json(429, {"error": "rate limited"})
			if mode == "upstream_502":
				return self._send_json(502, {"error": "upstream failed"})
			if mode == "upstream_503":
				return self._send_json(503, {"error": "ai disabled"})
			if mode == "empty":
				return self._send_json(200, {"choices": []})
			if mode == "output_text":
				return self._send_json(200, {"output_text": type(self).text})
			return self._send_json(200, {"choices": [{"message": {"content": type(self).text}, "finish_reason": "stop"}]})
		return self._send_json(404, {"error": "not found"})

	def do_GET(self):
		type(self).requests.append(("GET", self.path, dict(self.headers), None))
		if self.path == "/api/ai/health":
			if type(self).mode == "health_down":
				return self._send_json(500, {"ok": False})
			return self._send_json(200, type(self).health)
		return self._send_json(404, {"error": "not found"})

	def log_message(self, *args):
		pass


class RelayClientTests(unittest.TestCase):
	def setUp(self):
		RelayStubHandler.mode = "ok"
		RelayStubHandler.text = "ok"
		RelayStubHandler.health = {"ok": True, "provider": "zhipu", "model": "glm-4.7-flash", "ai_enabled": True}
		RelayStubHandler.requests = []
		self.server = ThreadingHTTPServer(("127.0.0.1", 0), RelayStubHandler)
		self.port = self.server.server_address[1]
		self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
		self.thread.start()

	def tearDown(self):
		self.server.shutdown()
		self.server.server_close()
		self.thread.join(timeout=5)

	def _relay_config(self, **ai_overrides):
		ai = {
			"service": "relay",
			"provider": "relay",
			"relay_url": f"http://127.0.0.1:{self.port}",
			"relay_token": "test-app-token",
			"timeout_seconds": 30,
		}
		ai.update(ai_overrides)
		return {
			"ai": ai,
			"profile": {
				"target_roles": ["Python 后端"],
				"target_cities": ["上海"],
				"education": "本科",
				"salary_min": 10,
				"salary_max": 30,
				"job_types": ["全职"],
				"posted_days": 7,
			},
			"search": {"keywords": ["Python 后端"]},
		}

	def _chat_requests(self):
		return [req for req in RelayStubHandler.requests if req[0] == "POST" and req[1] == "/api/ai/chat"]

	def test_relay_preset_is_accepted_by_config_validation(self):
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(
				yaml.dump({"ai": {"service": "relay", "provider": "relay"}}),
				encoding="utf-8",
			)
			config = load_config(config_path)
		self.assertEqual(config["ai"]["service"], "relay")
		self.assertEqual(config["ai"]["provider"], "relay")
		self.assertEqual(get_ai_service_label(config), "云端中转")

	def test_success_returns_text_and_sends_app_token(self):
		RelayStubHandler.text = "你好，我是中转。"
		result = call_relay_text("请打招呼", self._relay_config(), 100)

		self.assertEqual(result, "你好，我是中转。")
		requests = self._chat_requests()
		self.assertEqual(len(requests), 1)
		self.assertEqual(requests[0][2].get("X-App-Token"), "test-app-token")
		self.assertEqual(requests[0][3]["messages"], [{"role": "user", "content": "请打招呼"}])
		self.assertEqual(requests[0][3]["temperature"], 0.2)
		self.assertEqual(requests[0][3]["max_tokens"], 100)
		self.assertNotIn("api_key", json.dumps(requests[0][3]))

	def test_direct_output_text_is_accepted(self):
		RelayStubHandler.mode = "output_text"
		RelayStubHandler.text = "直接文本"
		self.assertEqual(call_relay_text("请打招呼", self._relay_config(), 100), "直接文本")

	def test_401_maps_to_relay_auth(self):
		RelayStubHandler.mode = "auth_error"
		with self.assertRaises(AIRequestError) as caught:
			call_relay_text("请打招呼", self._relay_config(), 100)
		self.assertEqual(caught.exception.kind, "relay_auth")
		self.assertEqual(caught.exception.status_code, 401)

	def test_429_maps_to_relay_rate_limited(self):
		RelayStubHandler.mode = "rate_limit"
		with self.assertRaises(AIRequestError) as caught:
			call_relay_text("请打招呼", self._relay_config(), 100)
		self.assertEqual(caught.exception.kind, "relay_rate_limited")
		self.assertIn("稍后重试", str(caught.exception))

	def test_502_and_503_map_to_relay_upstream(self):
		for mode in ("upstream_502", "upstream_503"):
			RelayStubHandler.mode = mode
			with self.assertRaises(AIRequestError) as caught:
				call_relay_text("请打招呼", self._relay_config(), 100)
			self.assertEqual(caught.exception.kind, "relay_upstream")

	def test_empty_response_raises(self):
		RelayStubHandler.mode = "empty"
		with self.assertRaises(AIRequestError) as caught:
			call_relay_text("请打招呼", self._relay_config(), 100)
		self.assertEqual(caught.exception.kind, "empty_response")

	def test_unreachable_relay_maps_to_relay_unavailable(self):
		config = self._relay_config(relay_url="http://127.0.0.1:1")
		with self.assertRaises(AIRequestError) as caught:
			call_relay_text("请打招呼", config, 100)
		self.assertEqual(caught.exception.kind, "relay_unavailable")

	def test_missing_token_returns_none(self):
		config = self._relay_config(relay_token="")
		self.assertIsNone(call_relay_text("请打招呼", config, 100))
		self.assertEqual(self._chat_requests(), [])
		config = self._relay_config()
		del config["ai"]["relay_token"]
		self.assertIsNone(call_relay_text("请打招呼", config, 100))

	def test_call_ai_text_dispatches_to_relay(self):
		RelayStubHandler.text = "中转回来的文本"
		self.assertEqual(call_ai_text("请打招呼", self._relay_config(), 100), "中转回来的文本")
		self.assertEqual(len(self._chat_requests()), 1)

	def test_relay_has_no_local_key_and_is_fallback_friendly(self):
		config = self._relay_config()
		self.assertIsNone(get_ai_api_key(config))
		self.assertEqual(get_ai_key_source(config), "云端中转")
		self.assertTrue(ai_failure_falls_back(config))
		self.assertEqual(get_relay_url(config), f"http://127.0.0.1:{self.port}")
		self.assertEqual(get_relay_token(config), "test-app-token")
		with self.assertRaises(AIRequestError) as caught:
			list_ai_models(config)
		self.assertEqual(caught.exception.kind, "unsupported")
		self.assertIn("云端统一管理", str(caught.exception))

	def test_scoring_goes_through_relay(self):
		RelayStubHandler.text = SCORE_REPLY
		outcome = _score_job_with_ai(dict(BASE_JOB), "简历摘要", self._relay_config(), max_attempts=1)

		self.assertIsNotNone(outcome.result)
		self.assertEqual(outcome.result.score, 84)
		self.assertTrue(outcome.result.structured)
		self.assertEqual(len(self._chat_requests()), 1)

	def test_scoring_falls_back_to_rules_when_relay_is_down(self):
		RelayStubHandler.mode = "upstream_502"
		outcome = _score_job_with_ai(dict(BASE_JOB), "简历摘要", self._relay_config(), max_attempts=1)

		# 中转失败不暂停整批：回落本地规则评分。
		self.assertIsNotNone(outcome.result)
		self.assertFalse(outcome.result.structured)
		self.assertIn("规则评分", outcome.result.reason)

	def test_preflight_passes_when_relay_health_is_ok(self):
		checks = check_ai_connection(self._relay_config(), required=True)

		self.assertEqual(checks[0]["id"], "ai_connection")
		self.assertEqual(checks[0]["status"], "pass")
		self.assertIn("云端中转连接正常（zhipu/glm-4.7-flash）", checks[0]["message"])
		self.assertNotIn("test-app-token", str(checks))
		health_requests = [req for req in RelayStubHandler.requests if req[1] == "/api/ai/health"]
		self.assertEqual(len(health_requests), 1)

	def test_preflight_warns_when_relay_is_down(self):
		RelayStubHandler.mode = "health_down"
		checks = check_ai_connection(self._relay_config(), required=True)

		self.assertEqual(checks[0]["id"], "ai_connection")
		self.assertEqual(checks[0]["status"], "warning")
		self.assertIn("中转连接失败，将回落本地规则", checks[0]["message"])

	def test_preflight_warns_when_relay_is_unreachable(self):
		config = self._relay_config(relay_url="http://127.0.0.1:1")
		checks = check_ai_connection(config, required=True)

		self.assertEqual(checks[0]["status"], "warning")
		self.assertIn("中转连接失败，将回落本地规则", checks[0]["message"])

	def test_collect_preflight_counts_relay_as_configured(self):
		config = self._relay_config()
		with (
			patch("bosshunter.web.preflight.check_ai_connection", return_value=[]),
			patch("bosshunter.web.preflight.check_browser_connection", return_value=[]),
		):
			checks = collect_preflight_checks("rescore", config)
		self.assertFalse(any("未配置 AI" in check["message"] for check in checks))


if __name__ == "__main__":
	unittest.main()
