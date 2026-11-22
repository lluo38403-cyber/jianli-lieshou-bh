import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from bosshunter.web import server


class WebConfigApiTests(unittest.TestCase):
	def test_redacted_config_does_not_return_ai_section(self):
		config = {
			"ai": {"api_key": "test-api-key-12345678", "model": "claude", "service": "anthropic"},
			"profile": {"education": "本科"},
		}

		redacted = server._redact_config_for_response(config)

		self.assertNotIn("ai", redacted)
		self.assertNotIn("test-api-key-12345678", str(redacted))
		self.assertEqual(config["ai"]["api_key"], "test-api-key-12345678")
		self.assertEqual(redacted["profile"]["education"], "本科")

	def test_redacted_config_returns_switch_and_fixed_greeting_but_strips_preference(self):
		config = {
			"profile": {
				"education": "本科",
				"ai_greeting_enabled": False,
				"fixed_greeting": "您好，我是求职者。",
				"greeting_preference": "语气简洁",
			}
		}

		redacted = server._redact_config_for_response(config)

		# v38：招呼语开关与固定文案是投递设置页与管理员面板共用的，
		# 用户端接口都要返回；只有偏好仍由管理员面板管理，继续脱敏。
		self.assertIs(redacted["profile"]["ai_greeting_enabled"], False)
		self.assertEqual(redacted["profile"]["fixed_greeting"], "您好，我是求职者。")
		self.assertNotIn("greeting_preference", redacted["profile"])
		self.assertEqual(redacted["profile"]["education"], "本科")

	def test_config_download_payload_does_not_include_raw_credentials(self):
		payload = server._config_download_payload({
			"ai": {
				"api_key": "test-api-key-12345678",
				"auth_token": "auth-token-12345678",
				"model": "claude",
			}
		})

		self.assertNotIn("test-api-key-12345678", payload)
		self.assertNotIn("auth-token-12345678", payload)
		self.assertNotIn("api_key", payload)
		self.assertNotIn("auth_token", payload)

	def test_write_config_drops_posted_credentials_and_keeps_saved_ai(self):
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text("ai:\n  model: old\n", encoding="utf-8")

			with patch.object(server, "CONFIG_PATH", config_path):
				cleaned = server._sanitize_config_for_write({
					"ai": {"model": "new", "api_key": "web-api-key-that-must-not-enter-config"},
					"profile": {"education": "本科"},
				})
				server._write_config(cleaned)

			public_text = config_path.read_text(encoding="utf-8")
			public_config = yaml.safe_load(public_text)
			self.assertEqual(public_config["ai"]["model"], "old")
			self.assertEqual(public_config["profile"]["education"], "本科")
			self.assertNotIn("web-api-key-that-must-not-enter-config", public_text)
			self.assertFalse((Path(tmp) / ".config.credentials.yaml").exists())
			self.assertEqual(list(Path(tmp).glob(".config.*.tmp")), [])

	def test_sanitize_config_discards_retired_collection_count_fields_without_ai_section(self):
		cleaned = server._sanitize_config_for_write({
			"search": {"target_count": 9},
			"collection": {"default_target_count": 8, "daily_new_jobs_limit": 100},
			"platforms": {
				"boss": {"search": {"target_count": 7}},
				"zhilian": {"search": {"target_count": 6}},
				"51job": {"search": {"target_count": 5}},
			},
		})

		self.assertEqual(cleaned["search"], {})
		self.assertEqual(cleaned["collection"], {})
		for platform in ("boss", "zhilian", "51job"):
			self.assertEqual(cleaned["platforms"][platform]["search"], {})

	def test_sanitize_config_drops_posted_ai_and_admin_but_accepts_greeting_switch_and_fixed(self):
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(
				yaml.dump(
					{
						"ai": {
							"service": "zhipu",
							"model": "glm-4.7-flash",
							"services": {"zhipu": {"api_key": "saved-zhipu-key"}},
						},
						"profile": {
							"education": "本科",
							"ai_greeting_enabled": True,
							"fixed_greeting": "saved fixed greeting",
							"greeting_preference": "saved preference",
						},
					},
					sort_keys=False,
				),
				encoding="utf-8",
			)

			with patch.object(server, "CONFIG_PATH", config_path):
				cleaned = server._sanitize_config_for_write({
					"ai": {
						"service": "deepseek",
						"model": "hacker-model",
						"api_key": "evil-key",
						"services": {"zhipu": {"api_key": "evil-zhipu-key"}},
					},
					"admin": {"password": "evil-admin-password"},
					"profile": {
						"education": "硕士",
						"ai_greeting_enabled": False,
						"fixed_greeting": "user edited greeting",
						"greeting_preference": "evil preference",
					},
				})

		self.assertEqual(cleaned["ai"]["service"], "zhipu")
		self.assertEqual(cleaned["ai"]["model"], "glm-4.7-flash")
		self.assertEqual(cleaned["ai"]["services"]["zhipu"]["api_key"], "saved-zhipu-key")
		self.assertNotIn("evil-key", str(cleaned))
		self.assertNotIn("evil-zhipu-key", str(cleaned))
		self.assertNotIn("admin", cleaned)
		self.assertEqual(cleaned["profile"]["education"], "硕士")
		# v38：招呼语开关与固定文案是投递设置页与管理员面板共用的，
		# 用户提交的合规值都生效；偏好仍只认服务端已保存的值。
		self.assertFalse(cleaned["profile"]["ai_greeting_enabled"])
		self.assertEqual(cleaned["profile"]["fixed_greeting"], "user edited greeting")
		self.assertEqual(cleaned["profile"]["greeting_preference"], "saved preference")

	def test_sanitize_config_ignores_non_bool_greeting_switch_and_keeps_saved_value(self):
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(
				yaml.dump({"profile": {"ai_greeting_enabled": True, "fixed_greeting": "saved fixed greeting"}}, sort_keys=False),
				encoding="utf-8",
			)

			with patch.object(server, "CONFIG_PATH", config_path):
				cleaned = server._sanitize_config_for_write({
					# 开关不是布尔值、固定文案不是字符串时，都沿用已存值。
					"profile": {"ai_greeting_enabled": "yes", "fixed_greeting": 123},
				})

		self.assertTrue(cleaned["profile"]["ai_greeting_enabled"])
		self.assertEqual(cleaned["profile"]["fixed_greeting"], "saved fixed greeting")

	def test_sanitize_config_preserves_saved_greeting_fields_when_user_post_omits_them(self):
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(
				yaml.dump(
					{
						"profile": {
							"ai_greeting_enabled": False,
							"fixed_greeting": "saved fixed greeting",
						}
					},
					sort_keys=False,
				),
				encoding="utf-8",
			)

			with patch.object(server, "CONFIG_PATH", config_path):
				cleaned = server._sanitize_config_for_write({
					"profile": {"education": "本科"},
				})

		self.assertFalse(cleaned["profile"]["ai_greeting_enabled"])
		self.assertEqual(cleaned["profile"]["fixed_greeting"], "saved fixed greeting")


if __name__ == "__main__":
	unittest.main()
