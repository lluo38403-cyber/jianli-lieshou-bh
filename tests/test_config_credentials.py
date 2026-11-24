import os
import stat
import tempfile
import unittest
from pathlib import Path

import yaml
from click.testing import CliRunner

from bosshunter.config import (
    OLD_LEAKED_RELAY_TOKEN,
    credentials_path_for,
    get_admin_password_hash,
    hash_admin_password,
    load_config,
    migrate_leaked_relay_token,
    migrate_legacy_credentials,
    save_config,
    verify_admin_password,
)
from bosshunter.main import cli


class ConfigCredentialsTests(unittest.TestCase):
    def test_save_config_keeps_credentials_out_of_public_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            private_path = credentials_path_for(config_path)
            api_key = "sentinel-api-key-that-must-not-enter-config"

            save_config(
                {
                    "ai": {
                        "service": "deepseek",
                        "provider": "openai_compatible",
                        "model": "deepseek-chat",
                        "api_key": api_key,
                    }
                },
                config_path,
            )

            public_text = config_path.read_text(encoding="utf-8")
            public_config = yaml.safe_load(public_text)
            self.assertNotIn(api_key, public_text)
            self.assertNotIn("api_key", public_config["ai"])
            self.assertTrue(private_path.exists())
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(private_path.stat().st_mode), 0o600)
            self.assertEqual(load_config(config_path)["ai"]["api_key"], api_key)

    def test_migrate_legacy_credentials_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            private_path = credentials_path_for(config_path)
            api_key = "legacy-api-key-that-must-be-moved"
            auth_token = "legacy-auth-token-that-must-be-moved"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "profile": {"resume_path": "resume.md"},
                        "ai": {
                            "service": "anthropic",
                            "api_key": api_key,
                            "auth_token": auth_token,
                        },
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            self.assertTrue(migrate_legacy_credentials(config_path))
            self.assertFalse(migrate_legacy_credentials(config_path))

            public_text = config_path.read_text(encoding="utf-8")
            self.assertNotIn(api_key, public_text)
            self.assertNotIn(auth_token, public_text)
            self.assertNotIn("api_key", yaml.safe_load(public_text)["ai"])
            self.assertTrue(private_path.exists())
            loaded = load_config(config_path)
            self.assertEqual(loaded["ai"]["api_key"], api_key)
            self.assertEqual(loaded["ai"]["auth_token"], auth_token)

    def test_save_config_removes_cleared_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            private_path = credentials_path_for(config_path)
            save_config({"ai": {"api_key": "temporary-api-key"}}, config_path)

            save_config({"ai": {"service": "anthropic"}}, config_path)

            self.assertFalse(private_path.exists())
            self.assertNotIn("api_key", load_config(config_path)["ai"])

    def test_migrate_admin_password_is_idempotent_and_hashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            private_path = credentials_path_for(config_path)
            config_path.write_text(
                yaml.safe_dump({"admin": {"password": "lucky-admin-1"}}, sort_keys=False),
                encoding="utf-8",
            )

            self.assertTrue(migrate_legacy_credentials(config_path))
            self.assertFalse(migrate_legacy_credentials(config_path))

            public_text = config_path.read_text(encoding="utf-8")
            self.assertNotIn("lucky-admin-1", public_text)
            self.assertNotIn("password", yaml.safe_load(public_text).get("admin", {}))
            self.assertTrue(private_path.exists())
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(private_path.stat().st_mode), 0o600)
            loaded = load_config(config_path)
            stored = get_admin_password_hash(loaded)
            self.assertIsNotNone(stored)
            self.assertTrue(stored.startswith("pbkdf2_sha256$"))
            self.assertTrue(verify_admin_password("lucky-admin-1", stored))
            self.assertFalse(verify_admin_password("wrong-password", stored))

    def test_save_config_hashes_admin_password_without_leaving_plaintext(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            private_path = credentials_path_for(config_path)

            save_config({"admin": {"password": "lucky-admin-1"}}, config_path)

            public_text = config_path.read_text(encoding="utf-8")
            self.assertNotIn("lucky-admin-1", public_text)
            self.assertNotIn("password", yaml.safe_load(public_text).get("admin", {}))
            self.assertTrue(private_path.exists())
            stored = get_admin_password_hash(load_config(config_path))
            self.assertTrue(verify_admin_password("lucky-admin-1", stored))

    def test_save_config_keeps_admin_hash_when_ai_config_is_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text(
                yaml.safe_dump({"admin": {"password": "lucky-admin-1"}}, sort_keys=False),
                encoding="utf-8",
            )
            self.assertTrue(migrate_legacy_credentials(config_path))

            # 用户端保存配置（不含 admin）不能把管理员密码哈希清掉。
            save_config({"profile": {"education": "本科"}}, config_path)

            stored = get_admin_password_hash(load_config(config_path))
            self.assertTrue(verify_admin_password("lucky-admin-1", stored))

    def test_cli_startup_migrates_legacy_credentials_without_printing_them(self):
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / "runtime"
            runtime_dir.mkdir()
            config_path = runtime_dir / "config.yaml"
            private_path = credentials_path_for(config_path)
            api_key = "startup-api-key-that-must-not-be-printed"
            config_path.write_text(
                yaml.safe_dump({"ai": {"api_key": api_key}}, sort_keys=False),
                encoding="utf-8",
            )

            try:
                os.chdir(workspace)
                result = CliRunner().invoke(cli, ["--config", "runtime/config.yaml"])
            finally:
                os.chdir(original_cwd)

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertNotIn(api_key, result.output)
            self.assertNotIn(api_key, config_path.read_text(encoding="utf-8"))
            self.assertTrue(private_path.exists())


class LeakedRelayTokenMigrationTests(unittest.TestCase):
    """v49：config.yaml 里存着已泄露旧钥匙时，启动换成随包的新钥匙。"""

    def _write(self, directory, name, mapping):
        path = Path(directory) / name
        path.write_text(
            yaml.safe_dump(mapping, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return path

    def _stored_token(self, config_path):
        return yaml.safe_load(config_path.read_text(encoding="utf-8"))["ai"]["relay_token"]

    def test_old_token_is_replaced_with_bundled_new_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._write(
                tmp, "config.yaml", {"ai": {"relay_token": OLD_LEAKED_RELAY_TOKEN}}
            )
            self._write(
                tmp, "config.example.yaml", {"ai": {"relay_token": "brand-new-token-from-build"}}
            )
            self.assertTrue(migrate_leaked_relay_token(config_path))
            self.assertEqual(self._stored_token(config_path), "brand-new-token-from-build")
            # 再跑一次不再换：存的已经不是旧值了。
            self.assertFalse(migrate_leaked_relay_token(config_path))

    def test_custom_token_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._write(tmp, "config.yaml", {"ai": {"relay_token": "my-own-token"}})
            self._write(
                tmp, "config.example.yaml", {"ai": {"relay_token": "brand-new-token-from-build"}}
            )
            self.assertFalse(migrate_leaked_relay_token(config_path))
            self.assertEqual(self._stored_token(config_path), "my-own-token")

    def test_empty_bundled_token_does_not_migrate(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._write(
                tmp, "config.yaml", {"ai": {"relay_token": OLD_LEAKED_RELAY_TOKEN}}
            )
            self._write(tmp, "config.example.yaml", {"ai": {"relay_token": ""}})
            self.assertFalse(migrate_leaked_relay_token(config_path))
            self.assertEqual(self._stored_token(config_path), OLD_LEAKED_RELAY_TOKEN)

    def test_bundled_token_still_old_does_not_migrate(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._write(
                tmp, "config.yaml", {"ai": {"relay_token": OLD_LEAKED_RELAY_TOKEN}}
            )
            self._write(
                tmp, "config.example.yaml", {"ai": {"relay_token": OLD_LEAKED_RELAY_TOKEN}}
            )
            self.assertFalse(migrate_leaked_relay_token(config_path))
            self.assertEqual(self._stored_token(config_path), OLD_LEAKED_RELAY_TOKEN)

    def test_missing_token_does_not_migrate(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._write(tmp, "config.yaml", {"ai": {"relay_url": "http://127.0.0.1:8687"}})
            self._write(
                tmp, "config.example.yaml", {"ai": {"relay_token": "brand-new-token-from-build"}}
            )
            self.assertFalse(migrate_leaked_relay_token(config_path))

    def test_cli_startup_migrates_leaked_token(self):
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / "runtime"
            runtime_dir.mkdir()
            config_path = runtime_dir / "config.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {"ai": {"relay_token": OLD_LEAKED_RELAY_TOKEN}}, sort_keys=False
                ),
                encoding="utf-8",
            )
            (runtime_dir / "config.example.yaml").write_text(
                yaml.safe_dump(
                    {"ai": {"relay_token": "brand-new-token-from-build"}}, sort_keys=False
                ),
                encoding="utf-8",
            )
            try:
                os.chdir(workspace)
                result = CliRunner().invoke(cli, ["--config", "runtime/config.yaml"])
            finally:
                os.chdir(original_cwd)
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(self._stored_token(config_path), "brand-new-token-from-build")


if __name__ == "__main__":
    unittest.main()
