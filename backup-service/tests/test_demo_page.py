"""The independent form saves a synthetic handoff once, without a QShield caller."""

import base64
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import creator
from storage import ConflictError


class MemoryStore:
    rows = {}

    def get(self, request_id):
        return self.rows.get(request_id)

    def create(self, record):
        if record["request_id"] in self.rows:
            raise ConflictError("Duplicate")
        self.rows[record["request_id"]] = record


class DemoPageTests(unittest.TestCase):
    def setUp(self):
        MemoryStore.rows = {}
        self.environment = patch.dict(os.environ, {
            "BACKUP_DEMO_PASSCODE": "example-demo-code-123456",
            "PRIMARY_TO_BACKUP_SECRET": "example-creator-secret-123456",
            "BACKUP_AES_KEY_B64": base64.b64encode(bytes(range(32))).decode(),
        })
        self.store = patch.object(creator, "NeonStore", MemoryStore)
        self.environment.start()
        self.store.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.store.stop)
        self.client = TestClient(creator.app)

    def test_independent_form_is_fake_only_and_idempotent(self):
        page = self.client.get("/demo")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("name='card", page.text.lower())
        form = {"request_id": "TEST-ORDER-42", "amount": "499",
                "passcode": "example-demo-code-123456"}
        first = self.client.post("/demo", data=form)
        self.assertEqual(first.status_code, 200)
        self.assertIn("Wallet status is pending", first.text)
        saved = MemoryStore.rows["TEST-ORDER-42"]
        self.assertEqual(saved["wallet_status"], "pending")
        self.assertEqual(self.client.post("/demo", data=form).status_code, 200)
        self.assertEqual(len(MemoryStore.rows), 1)
        self.assertEqual(self.client.post("/demo", data={**form, "amount": "500"}).status_code, 409)

    def test_wrong_code_and_extra_fields_cannot_write(self):
        form = {"request_id": "TEST-ORDER-42", "amount": "499",
                "passcode": "wrong"}
        self.assertEqual(self.client.post("/demo", data=form).status_code, 403)
        self.assertEqual(self.client.post("/demo", data={**form, "card": "1234"}).status_code, 400)
        self.assertFalse(MemoryStore.rows)


if __name__ == "__main__":
    unittest.main()
