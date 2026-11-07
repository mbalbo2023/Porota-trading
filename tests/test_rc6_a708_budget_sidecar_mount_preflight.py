"""A708 no-host/no-provider regressions for an existing PPI budget bind."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import rc6_a708_budget_sidecar_mount_preflight as guard


WORKFLOW = Path(__file__).resolve().parents[1] / (
    ".github/workflows/rc6-a669-ppi-current-book-20261105.yml")


def fixture(root):
    path = root / guard.RELATIVE_SIDECAR
    path.parent.mkdir(parents=True)
    path.write_bytes(b"synthetic-metadata-only")
    path.chmod(0o600)
    return path


class SidecarBindPreflight(unittest.TestCase):
    def test_absent_file_holds_before_any_sqlite_or_provider_access(self):
        with tempfile.TemporaryDirectory() as directory:
            result = guard.inspect_path(Path(directory), expected_uid=os.geteuid())
        self.assertEqual(result["state"], "HOLD")
        self.assertEqual(result["reason"], "PPI_BUDGET_SIDECAR_ABSENT")
        self.assertEqual(result["provider_requests"], 0)
        self.assertEqual(result["sqlite_connections"], 0)

    def test_artifact_directory_named_like_database_is_intentional_not_db_child(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "paper_v17/observer_v17.db"
            db.parent.mkdir(parents=True)
            db.write_bytes(b"synthetic-database-file")
            sidecar = fixture(root)
            self.assertTrue(db.is_file())
            self.assertEqual(sidecar.parent.parent.name, "observer_v17.db")
            self.assertNotIn(db, sidecar.parents)
            result = guard.inspect_path(root, expected_uid=os.geteuid())
            self.assertEqual(result["state"], "PRESENT_UNVERIFIED")
            self.assertFalse(result["budget_ready"])

    def test_parent_and_leaf_symlinks_are_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sidecar = fixture(root)
            sidecar.unlink()
            sidecar.symlink_to(root / "arbitrary-foreign-file")
            self.assertEqual(guard.inspect_path(root, expected_uid=os.geteuid())[
                "reason"], "PPI_BUDGET_SIDECAR_PATH_UNSAFE")
            sidecar.unlink()
            sidecar.write_bytes(b"safe")
            other = root / "foreign"
            other.mkdir()
            (root / "paper_v17/artifacts/observer_v17.db/ppi-budget").rename(other / "old")
            (root / "paper_v17/artifacts/observer_v17.db/ppi-budget").symlink_to(other / "old")
            self.assertEqual(guard.inspect_path(root, expected_uid=os.geteuid())[
                "reason"], "PPI_BUDGET_SIDECAR_PATH_UNSAFE")

    def test_root_alias_symlink_above_data_root_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory)
            root = outer / "data"
            fixture(root)
            (outer / "alias").symlink_to(root, target_is_directory=True)
            result = guard.inspect_path(outer / "alias", expected_uid=os.geteuid())
        self.assertEqual(result["state"], "HOLD")
        self.assertEqual(result["reason"], "PPI_BUDGET_SIDECAR_PATH_UNSAFE")

    def test_hardlink_and_world_writable_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sidecar = fixture(root)
            os.link(sidecar, root / "alias")
            self.assertEqual(guard.inspect_path(root, expected_uid=os.geteuid())[
                "reason"], "PPI_BUDGET_SIDECAR_PATH_UNSAFE")
            (root / "alias").unlink()
            sidecar.chmod(0o606)
            self.assertEqual(guard.inspect_path(root, expected_uid=os.geteuid())[
                "reason"], "PPI_BUDGET_SIDECAR_PATH_UNSAFE")

    def test_foreign_owner_holds_without_chowning_anything(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            self.assertEqual(guard.inspect_path(root, expected_uid=os.geteuid() + 1)[
                "reason"], "PPI_BUDGET_SIDECAR_OWNER_UNVERIFIED")

    def test_same_inode_only_proves_bind_path_not_budget_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            received = guard.inspect_path(fixture(Path(directory)).parents[4],
                                          expected_uid=os.geteuid())
            # The real wire boundary is UID 1000; synthetic fixture UID may
            # differ, so test pure comparison with the same safe UID literal.
            received["uid"] = guard.EXPECTED_RUNTIME_UID
            actual = guard.compare(received, dict(received))
        self.assertEqual(actual["state"], "PATH_MATCHED_NOT_READY")
        self.assertEqual(actual["budget_readiness"], "NO_VERIFICADO")
        self.assertEqual(actual["exit_reserve"], "NO_VERIFICADO")
        self.assertEqual(actual["provider_requests"], 0)

    def test_cross_namespace_inode_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            seen = guard.inspect_path(root, expected_uid=os.geteuid())
            seen["uid"] = guard.EXPECTED_RUNTIME_UID
            other = dict(seen, inode=seen["inode"] + 1)
            result = guard.compare(seen, other)
        self.assertEqual(result["state"], "HOLD")
        self.assertEqual(result["reason"], "PPI_BUDGET_SIDECAR_MOUNT_MISMATCH")

    def test_malformed_receipts_and_duplicate_json_keys_fail_closed(self):
        self.assertEqual(guard.compare(None, {} )["state"], "HOLD")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            path.write_text('{"schema":"x","schema":"y"}', encoding="utf-8")
            self.assertIsNone(guard._load_receipt(str(path)))
        self.assertEqual(guard.compare({"state": "PRESENT_UNVERIFIED"}, {})[
            "reason"], "PPI_BUDGET_SIDECAR_PREFLIGHT_UNAVAILABLE")

    def test_host_absent_preserves_specific_reason_and_skips_container(self):
        result = guard.compare(
            guard._hold("PPI_BUDGET_SIDECAR_ABSENT"), None)
        self.assertEqual(result["state"], "HOLD")
        self.assertEqual(result["reason"], "PPI_BUDGET_SIDECAR_ABSENT")
        self.assertEqual(result["provider_requests"], 0)

    def test_wrong_container_db_or_user_holds_without_probe(self):
        with patch.object(guard.os, "geteuid", return_value=1000):
            result = guard.observe("container", environment={"PAPER_V17_DB_PATH": "/tmp/wrong.db"})
        self.assertEqual(result["reason"], "PPI_BUDGET_SIDECAR_MOUNT_MISMATCH")
        with patch.object(guard.os, "geteuid", return_value=0):
            result = guard.observe("container", environment={
                "PAPER_V17_DB_PATH": guard.EXPECTED_CONTAINER_DB})
        self.assertEqual(result["reason"], "PPI_BUDGET_SIDECAR_MOUNT_MISMATCH")

    def test_action_tests_sidecar_offhost_and_holds_physical_preflight(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("test_rc6_a708_budget_sidecar_mount_preflight.py -v", source)
        self.assertIn("scripts/rc6_a708_budget_sidecar_mount_preflight.py", source)
        gate = source.split("  sample_main_only:", 1)[1].split(
            "    # A diagnostic", 1)[0]
        self.assertIn("false &&", gate)
        self.assertNotIn("github.event_name == 'schedule'", gate)
        helper = Path(guard.__file__).read_text(encoding="utf-8")
        for prohibited in ("sqlite3.connect", "requests.get", "urllib.request", "PPIClient("):
            self.assertNotIn(prohibited, helper)
        self.assertIn("budget_readiness", helper)
        self.assertIn("NO_VERIFICADO", helper)


if __name__ == "__main__":
    unittest.main()
