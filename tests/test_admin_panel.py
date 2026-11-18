"""Admin panel API tests: password-protected AI configuration.

The user-facing config API never sees AI settings; only the admin panel
(``/api/admin/*``) can read or change them, and only with a valid session.
"""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from bosshunter.config import (
	hash_admin_password,
	migrate_legacy_credentials,
	verify_admin_password,
)
from bosshunter.web import server


class AdminPanelTests(unittest.TestCase):
	def setUp(self):
		self._tmp = tempfile.TemporaryDirectory()
		self.addCleanup(self._tmp.cleanup)
		self.base_dir = Path(self._tmp.name)
		self.config_path = self.base_dir / "config.yaml"
		self.config_path.write_text(
			yaml.dump(
				{
					"ai": {
						"service": "relay",
						"provider": "relay",
						"relay_url": "http://127.0.0.1:9",
						"relay_token": "test-app-token",
					},
					"admin": {"password": "lucky-admin-1"},
				},
				allow_unicode=True,
			),
			encoding="utf-8",
		)
		self.assertTrue(migrate_legacy_credentials(self.config_path))
		server.set_base_dir(self.base_dir)
		server._ADMIN_SESSIONS.clear()

	def _request(self, path, method="GET", json_body=None, cookie=None):
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
		if cookie:
			environ["HTTP_COOKIE"] = cookie
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

	def _login(self, password="lucky-admin-1"):
		status, headers, body = self._request("/api/admin/login", "POST", {"password": password})
		self.assertTrue(status.startswith("200"), body)
		token = headers.get("Set-Cookie", "").split(";")[0].split("=", 1)[1]
		self.assertTrue(token)
		return f"bh_admin_session={token}"

	def test_admin_status_reports_auth_state(self):
		_, _, body = self._request("/api/admin/status")
		payload = json.loads(body)
		self.assertFalse(payload["authorized"])
		self.assertTrue(payload["password_configured"])

		cookie = self._login()
		_, _, body = self._request("/api/admin/status", cookie=cookie)
		self.assertTrue(json.loads(body)["authorized"])

	def test_admin_status_reports_missing_password(self):
		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			(base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
			server.set_base_dir(base_dir)
			_, _, body = self._request("/api/admin/status")
		self.assertFalse(json.loads(body)["password_configured"])

	def test_admin_login_rejects_wrong_password(self):
		status, _, body = self._request("/api/admin/login", "POST", {"password": "wrong-password"})
		self.assertTrue(status.startswith("401"), body)
		self.assertIn("管理员密码不正确", body)

	def test_admin_login_without_configured_password_is_rejected(self):
		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			(base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
			server.set_base_dir(base_dir)
			status, _, body = self._request("/api/admin/login", "POST", {"password": "anything"})
		self.assertTrue(status.startswith("400"), body)

	def test_admin_config_requires_auth(self):
		for method, payload in (("GET", None), ("POST", {"ai": {"service": "deepseek"}})):
			status, _, body = self._request("/api/admin/config", method, payload)
			self.assertTrue(status.startswith("401"), body)

	def test_admin_config_returns_relay_status_only(self):
		cookie = self._login()
		health = {"ok": True, "provider": "zhipu", "model": "glm-4.7-flash", "ai_enabled": True}
		with patch.object(server, "get_relay_health", return_value=health):
			status, _, body = self._request("/api/admin/config", cookie=cookie)
		self.assertTrue(status.startswith("200"), body)
		payload = json.loads(body)
		self.assertEqual(payload["ai"]["relay"], {
			"url": "http://127.0.0.1:9",
			"reachable": True,
			"provider": "zhipu",
			"model": "glm-4.7-flash",
			"ai_enabled": True,
		})
		# 不含提供方/模型/凭证/中转 Token。
		for forbidden in ("service", "provider", "model", "base_url", "api_key", "auth_token", "services", "relay_token"):
			self.assertNotIn(forbidden, payload["ai"])
		self.assertNotIn("test-app-token", body)
		# 非凭证的调参字段保留。
		self.assertEqual(payload["ai"]["thinking"], "auto")

	def test_admin_config_returns_unreachable_relay_without_raising(self):
		cookie = self._login()
		status, _, body = self._request("/api/admin/config", cookie=cookie)
		self.assertTrue(status.startswith("200"), body)
		payload = json.loads(body)
		self.assertFalse(payload["ai"]["relay"]["reachable"])
		self.assertIsNone(payload["ai"]["relay"]["provider"])

	def test_admin_config_post_cannot_change_provider_model_or_key(self):
		cookie = self._login()
		status, _, body = self._request(
			"/api/admin/config", "POST",
			{"ai": {
				"service": "deepseek",
				"provider": "openai_compatible",
				"model": "hacker-model",
				"base_url": "https://evil.example/v1",
				"api_key": "evil-key",
				"services": {"deepseek": {"api_key": "evil-service-key"}},
				"clear_credentials": True,
				"resume_tailor_enabled": True,
			}},
			cookie=cookie,
		)
		self.assertTrue(status.startswith("200"), body)
		saved = server.load_config(server.CONFIG_PATH)
		# 提供方/模型/凭证一律改不了，强制走云端中转。
		self.assertEqual(saved["ai"]["service"], "relay")
		self.assertEqual(saved["ai"]["provider"], "relay")
		self.assertNotEqual(saved["ai"].get("model"), "hacker-model")
		self.assertNotIn("evil-key", str(saved))
		self.assertNotIn("evil-service-key", str(saved))
		self.assertNotIn("evil.example", str(saved))
		# 中转地址与 Token 不受影响；调参字段仍可改。
		self.assertEqual(saved["ai"]["relay_url"], "http://127.0.0.1:9")
		self.assertEqual(saved["ai"]["relay_token"], "test-app-token")
		self.assertTrue(saved["ai"]["resume_tailor_enabled"])

	def test_admin_config_post_ignores_unknown_service_and_forces_relay(self):
		cookie = self._login()
		status, _, body = self._request(
			"/api/admin/config", "POST", {"ai": {"service": "ollama"}}, cookie=cookie,
		)
		self.assertTrue(status.startswith("200"), body)
		self.assertEqual(server.load_config(server.CONFIG_PATH)["ai"]["service"], "relay")

	def test_admin_config_post_validates_greeting_fields(self):
		cookie = self._login()
		status, _, body = self._request(
			"/api/admin/config", "POST",
			{"profile": {"ai_greeting_enabled": False, "fixed_greeting": ""}},
			cookie=cookie,
		)
		self.assertTrue(status.startswith("400"), body)
		status, _, body = self._request(
			"/api/admin/config", "POST",
			{"profile": {
				"ai_greeting_enabled": False,
				"fixed_greeting": "您好，我想了解这个岗位。",
				"greeting_preference": "不要问问题",
			}},
			cookie=cookie,
		)
		self.assertTrue(status.startswith("200"), body)
		saved = server.load_config(server.CONFIG_PATH)["profile"]
		self.assertFalse(saved["ai_greeting_enabled"])
		self.assertEqual(saved["fixed_greeting"], "您好，我想了解这个岗位。")
		self.assertEqual(saved["greeting_preference"], "不要问问题")

	def test_admin_config_post_changes_password(self):
		cookie = self._login()
		status, _, body = self._request(
			"/api/admin/config", "POST", {"admin": {"new_password": "short"}}, cookie=cookie,
		)
		self.assertTrue(status.startswith("400"), body)
		status, _, body = self._request(
			"/api/admin/config", "POST", {"admin": {"new_password": "new-admin-9"}}, cookie=cookie,
		)
		self.assertTrue(status.startswith("200"), body)
		self.assertTrue(
			verify_admin_password(
				"new-admin-9",
				server.load_config(server.CONFIG_PATH)["admin"]["password_hash"],
			)
		)
		# 旧密码登不进，新密码可以。
		status, _, _ = self._request("/api/admin/login", "POST", {"password": "lucky-admin-1"})
		self.assertTrue(status.startswith("401"))
		self._login("new-admin-9")

	def test_user_config_get_has_no_ai_or_admin(self):
		cookie = self._login()
		status, _, body = self._request("/api/config", cookie=cookie)
		self.assertTrue(status.startswith("200"), body)
		payload = json.loads(body)
		self.assertNotIn("ai", payload)
		self.assertNotIn("admin", payload)

	def test_user_config_post_cannot_change_ai_or_admin_password(self):
		cookie = self._login()
		status, _, body = self._request(
			"/api/config", "POST",
			{
				"ai": {"service": "deepseek", "model": "hacker-model", "api_key": "evil-key"},
				"admin": {"password": "evil-admin-password"},
				"profile": {"education": "硕士"},
			},
			cookie=cookie,
		)
		self.assertTrue(status.startswith("200"), body)
		saved = server.load_config(server.CONFIG_PATH)
		self.assertEqual(saved["ai"]["service"], "relay")
		self.assertEqual(saved["ai"]["relay_token"], "test-app-token")
		self.assertNotIn("evil-key", str(saved))
		self.assertTrue(verify_admin_password("lucky-admin-1", saved["admin"]["password_hash"]))
		self.assertFalse(verify_admin_password("evil-admin-password", saved["admin"]["password_hash"]))
		self.assertEqual(saved["profile"]["education"], "硕士")

	def test_config_schema_has_no_ai_section(self):
		status, _, body = self._request("/api/config/schema")
		self.assertTrue(status.startswith("200"), body)
		keys = [section.get("key") for section in json.loads(body)["sections"]]
		self.assertNotIn("ai", keys)

	def test_diagnostics_requires_admin(self):
		status, _, body = self._request("/api/diagnostics/ai")
		self.assertTrue(status.startswith("401"), body)
		cookie = self._login()
		checks = [{"id": "ai_connection", "status": "pass", "message": "智谱接口连接正常", "detail": "仅检测模型列表接口"}]
		with patch.object(server, "check_ai_connection", return_value=checks):
			status, _, body = self._request("/api/diagnostics/ai", cookie=cookie)
		self.assertTrue(status.startswith("200"), body)
		self.assertEqual(json.loads(body)["checks"], checks)

	def test_admin_models_endpoint_is_gone(self):
		cookie = self._login()
		status, _, _ = self._request(
			"/api/admin/config/models", "POST", {"ai": {"service": "relay"}}, cookie=cookie,
		)
		# 模型由云端统一管理，模型列表接口已删除（404 未找到或 405 方法不允许）。
		self.assertTrue(status.startswith("404") or status.startswith("405"), status)

	def test_old_user_models_endpoint_is_gone(self):
		cookie = self._login()
		status, _, _ = self._request(
			"/api/config/models", "POST", {"ai": {"service": "zhipu"}}, cookie=cookie,
		)
		# 旧的用户端接口已删除（404 未找到或 405 方法不允许）。
		self.assertTrue(status.startswith("404") or status.startswith("405"), status)

	def test_admin_logout_invalidates_session(self):
		cookie = self._login()
		status, _, body = self._request("/api/admin/config", cookie=cookie)
		self.assertTrue(status.startswith("200"), body)
		status, _, body = self._request("/api/admin/logout", "POST", {}, cookie=cookie)
		self.assertTrue(status.startswith("200"), body)
		status, _, body = self._request("/api/admin/config", cookie=cookie)
		self.assertTrue(status.startswith("401"), body)

	def test_admin_password_hash_helpers(self):
		stored = hash_admin_password("lucky-admin-1")
		self.assertTrue(verify_admin_password("lucky-admin-1", stored))
		self.assertFalse(verify_admin_password("wrong", stored))
		self.assertFalse(verify_admin_password("lucky-admin-1", None))
		self.assertFalse(verify_admin_password("lucky-admin-1", "not-a-hash"))
		# 两次哈希用的盐不同，密文不同但都能验。
		self.assertNotEqual(stored, hash_admin_password("lucky-admin-1"))


if __name__ == "__main__":
	unittest.main()
