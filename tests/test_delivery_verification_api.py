"""Tests for the manual-verification resolve endpoint."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bosshunter.db import create_verification_hold, get_db, get_open_verification_hold
from bosshunter.web import server


class DeliveryVerificationApiTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "verify.db"
        self.db = get_db(self.db_path)
        # Each request opens its own connection (like the real _get_web_db),
        # so the endpoint's finally-close never touches the seeding connection.
        self._patcher = patch.object(
            server, "_get_web_db", side_effect=lambda: get_db(self.db_path)
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self.db.close()
        self._tmpdir.cleanup()

    def test_resolve_endpoint_closes_the_open_hold(self):
        hold_id = create_verification_hold(self.db, "boss", url="https://x/verify")
        self.assertIsNotNone(get_open_verification_hold(self.db))
        payload = json.loads(server.api_delivery_verification_resolve(str(hold_id)))
        self.assertTrue(payload["ok"])
        self.assertIsNone(get_open_verification_hold(self.db))

    def test_resolve_endpoint_is_idempotent(self):
        hold_id = create_verification_hold(self.db, "boss")
        json.loads(server.api_delivery_verification_resolve(str(hold_id)))
        payload = json.loads(server.api_delivery_verification_resolve(str(hold_id)))
        self.assertFalse(payload["ok"])

    def test_resolve_endpoint_rejects_bad_id(self):
        payload = json.loads(server.api_delivery_verification_resolve("abc"))
        self.assertIn("error", payload)


if __name__ == "__main__":
    unittest.main()
