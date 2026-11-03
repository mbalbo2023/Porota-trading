"""A516 contracts: one metadata-only global budget diagnosis; never a GET/GO."""

import json
import os
from pathlib import Path
import tempfile
import unittest

from scripts.rc6_a516_host_budget_metadata_ro import (
    build_receipt, capacity_mode, sidecar_kind, verify_receipt,
)


class A516BudgetMetadataTests(unittest.TestCase):
    def test_missing_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(sidecar_kind(Path(directory) / "absent.sqlite"), "ABSENT")
        receipt = build_receipt("ABSENT", {"status": "ABSENT"}, "OFF")
        self.assertEqual(receipt["diagnosis"], "SIDECAR_ABSENT")
        self.assertTrue(verify_receipt(receipt))

    def test_real_regular_single_link_classified_without_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "global.sqlite"
            path.write_bytes(b"not a database")
            # Only file metadata is inspected by sidecar_kind, never its bytes.
            self.assertEqual(sidecar_kind(path), "REGULAR_SINGLE_LINK")

    def test_symlink_and_multilink_never_certified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "global.sqlite"
            target.write_bytes(b"test")
            link = root / "symlink"
            link.symlink_to(target)
            self.assertEqual(sidecar_kind(link), "UNSAFE_OR_ALIAS")
            hardlink = root / "hardlink"
            os.link(target, hardlink)
            self.assertEqual(sidecar_kind(target), "UNSAFE_OR_ALIAS")
            self.assertEqual(sidecar_kind(hardlink), "UNSAFE_OR_ALIAS")

    def test_observed_without_exit_suspension_still_not_go(self):
        data = build_receipt("REGULAR_SINGLE_LINK",
            {"status": "OBSERVED", "exit_service": {"lower_suspended": False}},
            "APPROVED")
        self.assertEqual(data["diagnosis"], "BUDGET_OBSERVED_NOT_AUTHORITY")
        self.assertTrue(verify_receipt(data))
        for key in ("paper_ready", "book_replay_verified", "budget_lease_acquired",
                    "budget_sidecar_written", "trading_database_opened", "ppi_watch_touched"):
            self.assertIs(data[key], False)
        self.assertEqual(data["provider_calls"], 0)
        self.assertEqual(data["real_orders_sent"], 0)

    def test_exit_suspended_remains_hold(self):
        data = build_receipt("REGULAR_SINGLE_LINK",
            {"status": "OBSERVED", "exit_service": {"lower_suspended": True}},
            "SHADOW")
        self.assertEqual(data["diagnosis"], "EXIT_SERVICE_SUSPENDED")
        self.assertTrue(verify_receipt(data))

    def test_degraded_and_unverified(self):
        for state, expected in (("DEGRADED", "BUDGET_DEGRADED"),
                                ("UNVERIFIED", "SNAPSHOT_UNAVAILABLE")):
            with self.subTest(state=state):
                value = build_receipt("REGULAR_SINGLE_LINK", {"status": state}, "OFF")
                self.assertEqual(value["diagnosis"], expected)
                self.assertTrue(verify_receipt(value))

    def test_inconsistent_sidecar_fail_closed(self):
        value = build_receipt("ABSENT", {"status": "OBSERVED"}, "OFF")
        self.assertEqual(value["diagnosis"], "METADATA_INCONSISTENT")
        self.assertTrue(verify_receipt(value))

    def test_untrusted_status_and_mode_rejected(self):
        for fn in (
            lambda: build_receipt("REGULAR_SINGLE_LINK", {"status": "GREEN"}, "OFF"),
            lambda: build_receipt("FILE_ALIAS", {"status": "ABSENT"}, "OFF"),
            lambda: build_receipt("ABSENT", {"status": "ABSENT"}, "ALWAYS_ALLOW"),
        ):
            with self.assertRaises(ValueError):
                fn()

    def test_adversarial_receipt_cannot_promote_or_inject(self):
        baseline = build_receipt("REGULAR_SINGLE_LINK",
            {"status": "OBSERVED", "exit_service": {"lower_suspended": False}}, "OFF")
        for mutation in (
            {"paper_ready": True}, {"provider_calls": 1}, {"real_orders_sent": 1},
            {"trading_database_opened": True}, {"snapshot_scope": "/secret/path"},
            {"book_replay_verified": True}, {"unexpected": "raw token"},
            {"paper_ready": 0}, {"provider_calls": False},
            {"budget_sidecar_written": 0}, {"budget_lease_acquired": 0},
            {"ppi_watch_touched": 0}, {"book_replay_verified": 0},
        ):
            with self.subTest(mutation=mutation):
                wrong = dict(baseline, **mutation)
                self.assertFalse(verify_receipt(wrong))

    def test_output_contains_only_bounded_closed_vocabulary(self):
        for kind in ("ABSENT", "REGULAR_SINGLE_LINK", "UNSAFE_OR_ALIAS", "UNAVAILABLE"):
            for state in ("ABSENT", "OBSERVED", "DEGRADED", "UNVERIFIED"):
                value = build_receipt(kind, {"status": state,
                    "reason_codes": ["SECRET_WOULD_NEVER_BE_EMITTED"],
                    "exit_service": {"lower_suspended": False}}, "OTHER_OR_UNSET")
                encoded = json.dumps(value)
                self.assertNotIn("SECRET", encoded)
                self.assertNotIn("/app/data", encoded)
                self.assertTrue(verify_receipt(value))

    def test_capacity_environment_without_approval_creation(self):
        self.assertEqual(capacity_mode({}), "OTHER_OR_UNSET")
        self.assertEqual(capacity_mode({"POROTA_DYNAMIC_CAPACITY_MODE": "off"}), "OFF")
        self.assertEqual(capacity_mode({"POROTA_DYNAMIC_CAPACITY_MODE": "approved"}), "APPROVED")
        self.assertEqual(capacity_mode({"POROTA_DYNAMIC_CAPACITY_MODE": "evil token"}), "OTHER_OR_UNSET")

    def test_existing_action_requires_explicit_main_dispatch_and_immutable_image(self):
        source = (Path(__file__).resolve().parents[1] / ".github" / "workflows"
                  / "rc6-history-fresh-offhost-evidence.yml").read_text(encoding="utf-8")
        self.assertIn("inputs.execution_mode == 'DROPLET_BUDGET_METADATA'", source)
        self.assertIn("github.ref == 'refs/heads/main'", source)
        self.assertIn("group: rc6-unified-paper-deploy", source)
        self.assertIn("test \\\"\\$image\\\" = '$RC6_EXPECTED_IMAGE_ID'", source)
        self.assertIn("< scripts/rc6_a516_host_budget_metadata_ro.py", source)
        self.assertIn("'book_gets':0,'host_files_written':0", source)


if __name__ == "__main__":
    unittest.main()
