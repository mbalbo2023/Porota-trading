"""A715 R1 Current/Book control-plane contract: no host or provider operations."""
from __future__ import annotations

from hashlib import sha1, sha256
from pathlib import Path
import unittest

from scripts import rc6_a630_host_ppi_current_book as engine
from scripts import rc6_a669_ppi_day_slot_guard as guard

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/rc6-a669-ppi-current-book-20261105.yml"
R1_SHA = "eefcf6d8c93f3eddfc23bd471d9d5b41d9a31a9f"
R1_IMAGE = "sha256:fc5a974c38d5d0c434608e5c486924e37341c4a90b83bf04d53bfa6a337ced5a"
OLD_SHA = "7e1e744d6ca9ab30c51b5747dd4467af5ece0145"
OLD_IMAGE = "sha256:4a8f7306188979fe6976f400209151bd529bf0d9f46f068b50bbc54fc96fd70b"


class A715R1A670Controls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.engine_path = ROOT / "scripts/rc6_a630_host_ppi_current_book.py"
        cls.guard_path = ROOT / "scripts/rc6_a669_ppi_day_slot_guard.py"
        cls.helper_path = ROOT / "scripts/rc6_a689_slot_cas.py"

    def test_r1_pins_are_atomic_and_old_product_rejected(self):
        self.assertEqual(engine.PRODUCT_SHA, R1_SHA)
        self.assertEqual(guard.PRODUCT_SHA, R1_SHA)
        self.assertEqual(self.source.count(R1_SHA), 3)
        self.assertIn(R1_IMAGE, self.source)
        for source in (self.source, self.engine_path.read_text(), self.guard_path.read_text()):
            self.assertNotIn(OLD_SHA, source)
            self.assertNotIn(OLD_IMAGE, source)

    def test_engine_git_blob_and_helper_sha256_match_exact_checkout(self):
        raw = self.engine_path.read_bytes()
        git_blob = sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
        self.assertEqual(guard.ENGINE_GIT_BLOB, git_blob)
        self.assertEqual(engine.A689_HELPER_SHA256,
                         sha256(self.helper_path.read_bytes()).hexdigest())

    def test_no_scheduled_or_default_manual_provider_job(self):
        source = self.source
        job = source.split("  sample_main_only:\n", 1)[1].split("    steps:\n", 1)[0]
        self.assertIn("github.event_name == 'workflow_dispatch'", job)
        self.assertIn("inputs.capture_mode == 'GUARDED_CURRENT_BOOK'", job)
        self.assertNotIn("github.event_name == 'schedule'", job)
        self.assertIn("default: OFFHOST_CONTRACTS_ONLY", source)
        self.assertIn("options: ['OFFHOST_CONTRACTS_ONLY', 'GUARDED_CURRENT_BOOK']", source)
        self.assertIn("cancel-in-progress: false", source)
        self.assertIn("group: rc6-unified-paper-deploy", source)

    def test_a708_metadata_hold_precedes_a689_and_provider(self):
        source = self.source
        pos = source.index("A708 existing budget bind identity preflight")
        provider = source.index("At most one Current then one Book")
        self.assertLess(pos, provider)
        self.assertIn("evidence/a708_budget_sidecar_preflight.json", source)
        self.assertIn("scripts/rc6_a708_budget_sidecar_mount_preflight.py", source)
        self.assertIn("-e RC6_A689_SOURCE_B64=$RC6_A689_SOURCE_B64", source)
        self.assertIn("from scripts.rc6_a630_host_ppi_current_book import A689_HELPER_SHA256", source)

    def test_cas_runs_before_reader_and_unsigned_budget_never_imports_reader(self):
        method = self.engine_path.read_text().split("def container_main():", 1)[1]
        self.assertLess(method.index("budget, budget_reason ="),
                        method.index("_claim_a689_before_provider(target)"))
        self.assertLess(method.index("_claim_a689_before_provider(target)"),
                        method.index("from bd_ppi_readonly_guard import ProductionMarketReader"))
        self.assertIn("if credentials and budget:", method)
        self.assertIn("PPI_BUDGET_SIDECAR_ABSENT", self.engine_path.read_text())
        self.assertIn("A689_DURABLE_SLOT_CAS_HOLD", method)

    def test_legacy_nonatomic_github_artifact_claim_not_admission(self):
        self.assertNotIn("rc6_a685_slot_claim.py", self.source)
        self.assertNotIn("rc6-a670-claim-", self.source)
        self.assertNotIn("actions: read", self.source)
        self.assertIn("actions/upload-artifact@v4", self.source)
        self.assertIn("ppi_watch_mutations_by_action", self.source)
        self.assertIn("real_orders_sent_by_action", self.source)


if __name__ == "__main__":
    unittest.main()
