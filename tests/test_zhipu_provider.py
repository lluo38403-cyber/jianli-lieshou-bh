"""Zhipu BigModel free-tier tests against a local stub OpenAI-compatible server.

No real Zhipu key is used anywhere here: the stub mimics
https://open.bigmodel.cn/api/paas/v4 (chat completions + models list).
Real calls still need a key in a configured environment.
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
	get_ai_api_key,
	get_ai_base_url,
	get_ai_service_label,
	get_openai_compatible_model,
	list_ai_models,
)
from bosshunter.ai.greeter import generate_greetings
from bosshunter.ai.resume import _resume_generation_source, generate_tailored_resume
from bosshunter.ai.scorer import _score_job_with_ai
from bosshunter.config import load_config
from bosshunter.db import get_db, insert_job, update_job_score, update_job_status
from bosshunter.executor.monitor import _fallback_resume_path
from bosshunter.web.preflight import check_ai_connection

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

BASE_RESUME = """# 张三
电话：13800000000｜邮箱：zhangsan@example.com｜上海

## 求职意向
Python 后端工程师，期望薪资 15-25K，base 上海。

## 教育背景
2019-2023 上海大学 计算机科学与技术 本科

## 工作经历
2023-2025 测试公司 Python 后端工程师，负责 FastAPI 服务开发与维护，参与订单系统重构，接口响应时间优化百分之三十。

## 项目经历
订单系统重构：使用 FastAPI 与 MySQL 搭建订单服务，支持每日十万订单处理，编写单元测试覆盖核心流程。
"""

TAILORED_RESUME = """# 张三
电话：13800000000｜邮箱：zhangsan@example.com｜上海

## 求职意向
Python 后端工程师，期望薪资 15-25K，base 上海，匹配后端开发岗位要求。

## 教育背景
2019-2023 上海大学 计算机科学与技术 本科

## 工作经历
2023-2025 测试公司 Python 后端工程师，负责 FastAPI 服务开发与维护，参与订单系统重构，接口响应时间优化百分之三十。

## 项目经历
订单系统重构：使用 FastAPI 与 MySQL 搭建订单服务，支持每日十万订单处理，编写单元测试覆盖核心流程，重点突出后端开发与 FastAPI 经验。
"""


class ZhipuStubHandler(BaseHTTPRequestHandler):
	"""Minimal OpenAI-compatible stub for the Zhipu endpoints."""

	mode = "ok"
	text = "ok"
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
		if self.path.endswith("/chat/completions"):
			mode = type(self).mode
			if mode == "rate_limit":
				return self._send_json(429, {"error": {"code": "1302", "message": "Rate limit exceeded, please retry later"}})
			if mode == "auth_error":
				return self._send_json(401, {"error": {"code": "invalid_api_key", "message": "Invalid API key"}})
			if mode == "empty":
				return self._send_json(200, {"choices": []})
			if mode == "strict_thinking" and "thinking" in data:
				return self._send_json(400, {"error": {"code": "invalid_request", "message": "thinking parameter is not supported by this model"}})
			return self._send_json(200, {"choices": [{"message": {"content": type(self).text}, "finish_reason": "stop"}]})
		return self._send_json(404, {"error": "not found"})

	def do_GET(self):
		type(self).requests.append(("GET", self.path, dict(self.headers), None))
		if self.path.endswith("/models"):
			mode = type(self).mode
			if mode == "rate_limit":
				return self._send_json(429, {"error": {"code": "1302", "message": "Rate limit exceeded, please retry later"}})
			if mode == "auth_error":
				return self._send_json(401, {"error": {"code": "invalid_api_key", "message": "Invalid API key"}})
			return self._send_json(200, {"object": "list", "data": [{"id": "glm-4.7-flash"}, {"id": "glm-4-flash-250414"}]})
		return self._send_json(404, {"error": "not found"})

	def log_message(self, *args):
		pass


class ZhipuProviderTests(unittest.TestCase):
	def setUp(self):
		ZhipuStubHandler.mode = "ok"
		ZhipuStubHandler.text = "ok"
		ZhipuStubHandler.requests = []
		self.server = ThreadingHTTPServer(("127.0.0.1", 0), ZhipuStubHandler)
		self.port = self.server.server_address[1]
		self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
		self.thread.start()

	def tearDown(self):
		self.server.shutdown()
		self.server.server_close()
		self.thread.join(timeout=5)

	def _zhipu_config(self, **ai_overrides):
		ai = {
			"service": "zhipu",
			"provider": "openai_compatible",
			"base_url": f"http://127.0.0.1:{self.port}/api/paas/v4",
			"model": "glm-4.7-flash",
			"services": {"zhipu": {"api_key": "sk-zhipu-test-key"}},
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
				"ai_greeting_enabled": True,
				"fixed_greeting": "您好，我对这个岗位很感兴趣。",
				"greeting_preference": "语气简洁",
			},
			"search": {"keywords": ["Python 后端"]},
		}

	def _seed_job(self, db, job_id):
		"""Insert a job and mark it ready + scored (insert_job resets both)."""
		insert_job(db, dict(BASE_JOB, id=job_id))
		update_job_score(db, job_id, 80, "测试评分")
		update_job_status(db, job_id, "ready")

	def _chat_requests(self):
		return [req for req in ZhipuStubHandler.requests if req[0] == "POST" and req[1].endswith("/chat/completions")]

	def test_zhipu_preset_resolves_base_url_and_model(self):
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(
				yaml.dump({"ai": {"service": "zhipu", "model": "glm-4.7-flash"}}),
				encoding="utf-8",
			)
			config = load_config(config_path)

		self.assertEqual(config["ai"]["service"], "zhipu")
		self.assertEqual(config["ai"]["provider"], "openai_compatible")
		self.assertEqual(get_ai_base_url(config), "https://open.bigmodel.cn/api/paas/v4")
		self.assertEqual(get_openai_compatible_model(config), "glm-4.7-flash")
		self.assertEqual(get_ai_service_label(config), "智谱免费")

		# 未填 Base URL 时也回落到智谱预设地址。
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(yaml.dump({"ai": {"service": "zhipu"}}), encoding="utf-8")
			self.assertEqual(
				get_ai_base_url(load_config(config_path)), "https://open.bigmodel.cn/api/paas/v4"
			)

	def test_ollama_service_is_rejected_by_config_validation(self):
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(yaml.dump({"ai": {"service": "ollama"}}), encoding="utf-8")
			with self.assertRaises(ValueError):
				load_config(config_path)

	def test_zhipu_key_resolves_per_service_without_legacy_fallback(self):
		config = self._zhipu_config()
		self.assertEqual(get_ai_api_key(config), "sk-zhipu-test-key")

		# 只有 legacy Key、没有分服务商 Key 时，智谱不能误拿别的服务商的 Key。
		legacy_only = {"ai": {"service": "zhipu", "api_key": "legacy-key-for-another-service"}}
		self.assertIsNone(get_ai_api_key(legacy_only))

		# DeepSeek 仍可回落 legacy Key（旧配置兼容）。
		deepseek = {"ai": {"service": "deepseek", "api_key": "legacy-deepseek-key"}}
		self.assertEqual(get_ai_api_key(deepseek), "legacy-deepseek-key")

	def test_call_ai_text_returns_stub_reply_with_bearer_key(self):
		ZhipuStubHandler.text = "你好，我是智谱。"
		config = self._zhipu_config()

		result = call_ai_text("请打招呼", config, 100)

		self.assertEqual(result, "你好，我是智谱。")
		requests = self._chat_requests()
		self.assertEqual(len(requests), 1)
		self.assertEqual(requests[0][2].get("Authorization"), "Bearer sk-zhipu-test-key")
		self.assertEqual(requests[0][3]["model"], "glm-4.7-flash")

	def test_call_ai_text_rate_limit_1302_maps_to_rate_limit(self):
		ZhipuStubHandler.mode = "rate_limit"
		with self.assertRaises(AIRequestError) as caught:
			call_ai_text("请打招呼", self._zhipu_config(), 100)
		self.assertEqual(caught.exception.kind, "rate_limit")

	def test_call_ai_text_auth_error_maps_to_auth(self):
		ZhipuStubHandler.mode = "auth_error"
		with self.assertRaises(AIRequestError) as caught:
			call_ai_text("请打招呼", self._zhipu_config(), 100)
		self.assertEqual(caught.exception.kind, "auth")

	def test_call_ai_text_empty_choices_raises_empty_response(self):
		ZhipuStubHandler.mode = "empty"
		with self.assertRaises(AIRequestError) as caught:
			call_ai_text("请打招呼", self._zhipu_config(), 100)
		self.assertEqual(caught.exception.kind, "empty_response")

	def test_call_ai_text_retries_without_thinking_when_stub_rejects_it(self):
		ZhipuStubHandler.mode = "strict_thinking"
		ZhipuStubHandler.text = "重试成功"
		config = self._zhipu_config(thinking="auto")

		result = call_ai_text("请打招呼", config, 100)

		self.assertEqual(result, "重试成功")
		requests = self._chat_requests()
		self.assertEqual(len(requests), 2)
		self.assertIn("thinking", requests[0][3])
		self.assertNotIn("thinking", requests[1][3])

	def test_list_ai_models_returns_stub_models(self):
		models = list_ai_models(self._zhipu_config())
		self.assertCountEqual(models, ["glm-4.7-flash", "glm-4-flash-250414"])

	def test_zhipu_scoring_uses_ai_result(self):
		ZhipuStubHandler.text = SCORE_REPLY
		outcome = _score_job_with_ai(dict(BASE_JOB), "简历摘要", self._zhipu_config(), max_attempts=1)

		self.assertIsNotNone(outcome.result)
		self.assertTrue(outcome.result.structured)
		self.assertEqual(outcome.result.score, 84)

	def test_zhipu_scoring_falls_back_to_rule_score_on_rate_limit(self):
		ZhipuStubHandler.mode = "rate_limit"
		outcome = _score_job_with_ai(dict(BASE_JOB), "简历摘要", self._zhipu_config(), max_attempts=1)

		self.assertIsNotNone(outcome.result)
		self.assertFalse(outcome.result.structured)
		self.assertGreater(outcome.result.score, 0)
		self.assertIn("规则评分", outcome.result.reason)
		self.assertTrue(ai_failure_falls_back(self._zhipu_config()))

	def test_zhipu_greeting_falls_back_to_fixed_template_on_rate_limit(self):
		with tempfile.TemporaryDirectory() as tmp:
			db_path = Path(tmp) / "bosshunter.db"
			resume_path = Path(tmp) / "resume.md"
			resume_path.write_text(BASE_RESUME, encoding="utf-8")
			config = self._zhipu_config()
			config["profile"]["resume_path"] = str(resume_path)
			db = get_db(db_path)
			try:
				self._seed_job(db, "job-zhipu-1")
				ZhipuStubHandler.mode = "rate_limit"
				count = generate_greetings(config, job_ids=["job-zhipu-1"], db_path=db_path)
				row = db.execute("SELECT greeting FROM jobs WHERE id = ?", ("job-zhipu-1",)).fetchone()
			finally:
				db.close()

		self.assertEqual(count, 1)
		self.assertEqual(row["greeting"], "您好，我对这个岗位很感兴趣。")

	def test_zhipu_greeting_uses_ai_reply_when_stub_succeeds(self):
		with tempfile.TemporaryDirectory() as tmp:
			db_path = Path(tmp) / "bosshunter.db"
			resume_path = Path(tmp) / "resume.md"
			resume_path.write_text(BASE_RESUME, encoding="utf-8")
			config = self._zhipu_config()
			config["profile"]["resume_path"] = str(resume_path)
			db = get_db(db_path)
			try:
				self._seed_job(db, "job-zhipu-2")
				ZhipuStubHandler.text = "您好，我是张三，有三年 Python 后端经验，做过 FastAPI 订单系统，想应聘这个岗位。"
				count = generate_greetings(config, job_ids=["job-zhipu-2"], db_path=db_path)
				row = db.execute("SELECT greeting FROM jobs WHERE id = ?", ("job-zhipu-2",)).fetchone()
			finally:
				db.close()

		self.assertEqual(count, 1)
		self.assertIn("张三", row["greeting"])

	def test_resume_generation_source_is_zhipu(self):
		self.assertEqual(_resume_generation_source(self._zhipu_config()), "zhipu")

	def test_resume_tailoring_succeeds_through_stub_and_records_source(self):
		with tempfile.TemporaryDirectory() as tmp:
			tmp_path = Path(tmp)
			resume_path = tmp_path / "resume.md"
			resume_path.write_text(BASE_RESUME, encoding="utf-8")
			db_path = tmp_path / "bosshunter.db"
			db = get_db(db_path)
			try:
				self._seed_job(db, "job-zhipu-3")
				ZhipuStubHandler.text = TAILORED_RESUME
				config = self._zhipu_config()
				config["profile"]["resume_path"] = str(resume_path)
				config["profile"]["resume_output_dir"] = str(tmp_path / "resumes")
				with patch("bosshunter.ai.resume.get_db", return_value=db):
					result = generate_tailored_resume("job-zhipu-3", config)
			finally:
				db.close()
			# generate_tailored_resume closes the passed connection; re-open to assert.
			check_db = get_db(db_path)
			try:
				row = check_db.execute(
					"SELECT resume_generation_source, resume_path FROM jobs WHERE id = ?",
					("job-zhipu-3",),
				).fetchone()
			finally:
				check_db.close()

			self.assertIsNotNone(result)
			self.assertEqual(row["resume_generation_source"], "zhipu")
			self.assertTrue(Path(row["resume_path"]).exists())

	def test_resume_tailoring_failure_returns_none_on_rate_limit(self):
		with tempfile.TemporaryDirectory() as tmp:
			tmp_path = Path(tmp)
			resume_path = tmp_path / "resume.md"
			resume_path.write_text(BASE_RESUME, encoding="utf-8")
			db_path = tmp_path / "bosshunter.db"
			db = get_db(db_path)
			try:
				self._seed_job(db, "job-zhipu-4")
				ZhipuStubHandler.mode = "rate_limit"
				config = self._zhipu_config()
				config["profile"]["resume_path"] = str(resume_path)
				config["profile"]["resume_output_dir"] = str(tmp_path / "resumes")
				with patch("bosshunter.ai.resume.get_db", return_value=db):
					result = generate_tailored_resume("job-zhipu-4", config)
			finally:
				db.close()

		self.assertIsNone(result)

	def test_monitor_tailor_failure_falls_back_to_original_resume_for_zhipu(self):
		with tempfile.TemporaryDirectory() as tmp:
			resume_path = Path(tmp) / "resume.md"
			resume_path.write_text(BASE_RESUME, encoding="utf-8")
			config = self._zhipu_config()
			config["profile"]["resume_path"] = str(resume_path)

			self.assertEqual(_fallback_resume_path(config), resume_path)

		deepseek_config = {"ai": {"service": "deepseek"}, "profile": {}}
		self.assertIsNone(_fallback_resume_path(deepseek_config))

	def test_preflight_pass_for_zhipu_stub(self):
		checks = check_ai_connection(self._zhipu_config(), required=True)

		check = next(item for item in checks if item["id"] == "ai_connection")
		self.assertEqual(check["status"], "pass")
		self.assertIn("智谱接口连接正常", check["message"])
		self.assertIn("仅检测模型列表接口", check["detail"])

	def test_preflight_zhipu_failure_is_warning_not_error(self):
		ZhipuStubHandler.mode = "rate_limit"
		checks = check_ai_connection(self._zhipu_config(), required=True)

		check = next(item for item in checks if item["id"] == "ai_connection")
		self.assertEqual(check["status"], "warning")


if __name__ == "__main__":
	unittest.main()
