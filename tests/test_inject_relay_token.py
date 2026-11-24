"""scripts/ci/inject-relay-token.py 的测试：只测注入行为，不碰真 secret。"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ci" / "inject-relay-token.py"
EXAMPLE = 'ai:\n  relay_url: "http://127.0.0.1:8687"\n  relay_token: ""  # 由打包时注入，仓库里不放真值\n'


class InjectRelayTokenTests(unittest.TestCase):
    def _run(self, example_text, env_token):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.example.yaml"
            target.write_text(example_text, encoding="utf-8")
            env = dict(os.environ)
            env.pop("RELAY_APP_TOKEN", None)
            if env_token is not None:
                env["RELAY_APP_TOKEN"] = env_token
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), str(target)],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )
            return proc, target.read_text(encoding="utf-8")

    def test_missing_secret_fails_loudly(self):
        proc, _ = self._run(EXAMPLE, None)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("RELAY_APP_TOKEN", proc.stderr)

    def test_empty_secret_fails_loudly(self):
        proc, _ = self._run(EXAMPLE, "   ")
        self.assertNotEqual(proc.returncode, 0)

    def test_valid_token_is_injected_once(self):
        proc, text = self._run(EXAMPLE, "new-token-from-secret")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(text.count("new-token-from-secret"), 1)
        self.assertNotIn('relay_token: ""', text)

    def test_nonempty_placeholder_is_refused(self):
        proc, text = self._run(EXAMPLE.replace('""', '"already-there"'), "new-token-from-secret")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("already-there", text)
        self.assertNotIn("new-token-from-secret", text)

    def test_token_with_quote_is_refused(self):
        proc, _ = self._run(EXAMPLE, 'bad"token')
        self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
