from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from engine.runtime_profiles.errors import (
    RuntimeProfileConflict,
    RuntimeProfileError,
)
from engine.runtime_profiles.models import RuntimeProfile
from engine.runtime_profiles.service import RuntimeProfileService
from engine.runtime_profiles.store import RuntimeProfileStore
from engine.storage.adapters.legacy_sqlite import LegacySqliteAdapter


class RuntimeProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "engine.db"
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY)")
            connection.commit()
        self.store = RuntimeProfileStore(
            self.root / "runtime_profiles",
            allowed_roots=[self.root],
            legacy_db_path=self.db_path,
            relative_root=self.root,
        )
        self.store.initialize()
        self.service = RuntimeProfileService(self.store)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _candidate_payload(self, profile_id: str = "legacy-candidate-v1") -> dict:
        return {
            "profile_id": profile_id,
            "display_name": "候选 Legacy 配置",
            "environment": "test",
            "storage": {
                "type": "legacy_sqlite",
                "adapter_version": "1.0.0",
                "root": str(self.db_path),
                "contract_version": 1,
            },
            "catalog": {
                "catalog_id": f"{profile_id}-catalog",
                "catalog_hash": "fixture-hash",
                "datasets": {"fixture": "legacy-live"},
            },
            "semantics": {"foundation_version": 1},
            "topology": {"growth_threshold": 1000},
        }

    def test_bootstrap_creates_active_legacy_profile(self) -> None:
        pointer, profile = self.store.get_active()
        self.assertEqual(profile.profile_id, "legacy-local-v1")
        self.assertEqual(profile.storage.type, "legacy_sqlite")
        self.assertEqual(pointer["revision"], 1)
        self.assertEqual(profile.acceptance.status, "passed")

    def test_candidate_must_verify_and_be_ready_before_atomic_activation(self) -> None:
        created = self.service.create_profile(
            self._candidate_payload(), actor="test", reason="create fixture"
        )
        verified = self.service.verify_profile(
            created["profile_id"],
            expected_version=created["profile_version"],
            actor="test",
            reason="verify fixture",
        )
        self.assertEqual(verified["acceptance"]["status"], "passed")
        ready = self.service.mark_ready(
            created["profile_id"],
            expected_version=verified["profile_version"],
            actor="test",
            reason="ready fixture",
        )
        result = self.service.activate(
            created["profile_id"],
            expected_revision=1,
            actor="test",
            reason="activate fixture",
        )
        self.assertEqual(result["pointer"]["revision"], 2)
        self.assertEqual(result["active"]["profile_id"], created["profile_id"])
        self.assertTrue(result["active"]["is_active"])

    def test_legacy_health_is_a_fast_read_only_connectivity_check(self) -> None:
        _, profile = self.store.get_active()
        health = LegacySqliteAdapter().health(profile)
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["check_mode"], "read_only_connectivity")
        self.assertIn("快速检查", health["message"])

    def test_activation_uses_compare_and_swap_revision(self) -> None:
        with self.assertRaises(RuntimeProfileConflict):
            self.store.activate(
                "legacy-local-v1",
                expected_revision=99,
                actor="test",
                reason="stale request",
            )

    def test_storage_root_cannot_escape_allowlist(self) -> None:
        payload = self._candidate_payload("outside-root-v1")
        payload["state"] = "draft"
        payload["profile_version"] = 1
        payload["acceptance"] = {"status": "not_run"}
        payload["storage"]["root"] = str(self.root.parent / "outside.db")
        profile = RuntimeProfile.from_dict(payload)
        with self.assertRaises(RuntimeProfileError):
            self.store.create(profile, actor="test", reason="reject escape")

    def test_missing_cnts_catalog_fails_without_fallback(self) -> None:
        payload = self._candidate_payload("cnts-candidate-v1")
        payload["storage"] = {
            "type": "cnts",
            "adapter_version": "1.0.0",
            "root": str(self.root / "cnts"),
            "contract_version": 1,
        }
        (self.root / "cnts").mkdir()
        created = self.service.create_profile(payload, actor="test", reason="cnts draft")
        verified = self.service.verify_profile(
            created["profile_id"],
            expected_version=created["profile_version"],
            actor="test",
            reason="cnts verify",
        )
        self.assertEqual(verified["acceptance"]["status"], "failed")
        health = verified["acceptance"]["checks"]["storage_health"]
        self.assertIn("Catalog", health["message"])
        self.assertEqual(health["fallback"], "forbidden")

    def test_corrupt_active_pointer_is_not_silently_repaired(self) -> None:
        self.store.active_file.write_text("profile_id: missing\n", encoding="utf-8")
        with self.assertRaises(RuntimeProfileError):
            self.store.get_active()


if __name__ == "__main__":
    unittest.main()
