"""A667: synthetic OFFHOST trust boundary; never PPI, Watch, or real orders."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from be_paper_engine import PaperStore, Quote
import rc6_paper_causal_decision_receipt as decision_signatures
from rc6_ppi_scanner_provenance import PIT_JOURNAL_FILENAME, capture_native_book_journal
from rc6_ppi_signed_postcommit_pit import inspect_pinned_signed_two_book
from scripts import rc6_paper_postcommit_signer as signer_module
from scripts.rc6_paper_postcommit_signer import attest_one_pinned


ART = timezone(timedelta(hours=-3))
AT = datetime(2026, 11, 4, 11, 0, 0, tzinfo=ART)
DECISION_AT = AT + timedelta(seconds=1)
KNOWN_AT = AT + timedelta(seconds=2)
RELEASE = "a" * 64
IDENTITY = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
KEY = "A667:GGAL:BUY:SIMULATION"


class TestPinnedPostcommitPIT(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.db = root / "paper.db"
        self.store = PaperStore(str(self.db))
        self.journal = root / "ppi" / PIT_JOURNAL_FILENAME
        self.journal.parent.mkdir()
        private = Ed25519PrivateKey.generate()
        self.signer_path = root / "independent-ed25519-0600.key"
        self.signer_path.write_bytes(private.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
            serialization.NoEncryption()))
        self.signer_path.chmod(0o600)
        self.public = private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.pin = hashlib.sha256(self.public).hexdigest()
        self.capture(AT)
        self.record()

    def quote(self, at, *, ask="100.20", ask_size="21"):
        stamp = at.isoformat()
        return Quote("GGAL", "ACCIONES", "A-24HS", Decimal("100"),
                     Decimal("100"), Decimal(ask), Decimal("14"),
                     Decimal(ask_size), stamp, currency="ARS", market="BYMA",
                     book_at=stamp, trade_at=stamp, last_kind="TRADE")

    def capture(self, at, *, ask="100.20", ask_size="21"):
        q = self.quote(at, ask=ask, ask_size=ask_size)
        raw = {"date": q.book_at,
               "bids": [{"position": 0, "price": "100", "quantity": "14"}],
               "offers": [{"position": 0, "price": ask, "quantity": ask_size}]}
        receipt = capture_native_book_journal(
            self.journal, q, raw_book=raw,
            request=("GGAL", "ACCIONES", "A-24HS"),
            identity=IDENTITY, observed_at=q.observed_at)
        self.assertEqual(receipt["status"], "CAPTURED")
        return receipt["event_sha256"]

    def record(self, *, supplied_claim=None):
        from rc6_ppi_scanner_provenance import verified_native_book_journal
        rows, receipt = verified_native_book_journal(self.journal)
        self.assertEqual(receipt["state"], "HASH_CHAIN_VERIFIED")
        q = replace(self.quote(AT), ppi_book1_event_sha256=rows[0]["event_sha256"])
        features = {"performance_lineage": {"decision_at": DECISION_AT.isoformat()},
                    "entry_authority": False, "real_routes_used": []}
        if supplied_claim is not None:
            features["ppi_book1_event_sha256"] = supplied_claim
        with patch.dict(os.environ, {"POROTA_RC6_RELEASE_SHA256": RELEASE}):
            self.assertTrue(self.store.record_decision(
                KEY, q, "BUY", Decimal("0.75"), "SYNTHETIC_ONLY", features))

    def signed(self, *, known_at=KNOWN_AT, release=RELEASE, signer_path=None,
               db_path=None, db_pin=None):
        with patch.object(decision_signatures, "PINNED_DECISION_SIGNER_KEY_SHA256", self.pin), \
             patch.object(decision_signatures, "PINNED_RELEASE_SHA256", RELEASE), \
             patch.object(decision_signatures, "_now", return_value=known_at.isoformat()), \
             patch.object(signer_module, "PINNED_DECISION_DB_ABSPATH",
                          self.db if db_pin is None else db_pin, create=True):
            return attest_one_pinned(
                db_path or self.db, decision_key=KEY,
                signing_key_path=signer_path or self.signer_path,
                expected_release_sha256=release)

    def test_pinned_signer_refuses_alternate_forged_sqlite_path(self):
        # The CLI's --db-path is caller-controlled. A release/key pin alone
        # must not allow it to sign a copied/fabricated PAPER database.
        alien = self.db.parent / "untrusted-paper.db"
        with sqlite3.connect(self.db) as source, sqlite3.connect(alien) as target:
            source.backup(target)
        result = self.signed(db_path=alien)
        self.assertEqual(result["reason"], "CAUSAL_SIGNER_DB_PATH_UNTRUSTED")
        self.assertFalse(result["signature_created"])
        with sqlite3.connect(alien) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM paper_causal_decision_receipts").fetchone()[0], 0)

    def test_pinned_signer_must_have_independent_db_anchor(self):
        result = self.signed(db_pin=False)
        self.assertEqual(result["reason"], "CAUSAL_SIGNER_DB_TRUST_UNAVAILABLE")
        self.assertFalse(result["signature_created"])

    def test_pinned_signer_refuses_symlinked_db_alias(self):
        alias = self.db.parent / "paper-alias.db"
        alias.symlink_to(self.db)
        result = self.signed(db_path=alias, db_pin=alias)
        self.assertEqual(result["reason"], "CAUSAL_SIGNER_DB_ALIAS_HOLD")
        self.assertFalse(result["signature_created"])

    def test_pinned_signer_refuses_hardlinked_db_alias(self):
        alias = self.db.parent / "paper-hardlink.db"
        os.link(self.db, alias)
        result = self.signed(db_path=alias, db_pin=alias)
        self.assertEqual(result["reason"], "CAUSAL_SIGNER_DB_FILE_UNTRUSTED")
        self.assertFalse(result["signature_created"])

    def inspect(self, *, trusted=True, **kwargs):
        args = {"decision_key": KEY, "family_group": "ACCIONES",
                "identity": IDENTITY, "latency_seconds": 1,
                "trusted_public_key_raw": self.public,
                "trusted_public_key_sha256": self.pin,
                "trusted_release_sha256": RELEASE}
        args.update(kwargs)
        if trusted:
            with patch.object(decision_signatures, "PINNED_DECISION_SIGNER_KEY_SHA256", self.pin), \
                 patch.object(decision_signatures, "PINNED_RELEASE_SHA256", RELEASE):
                return inspect_pinned_signed_two_book(self.journal, self.db, **args)
        return inspect_pinned_signed_two_book(self.journal, self.db, **args)

    def assert_evidence_only(self, receipt):
        self.assertFalse(receipt["fill_accredited"])
        self.assertFalse(receipt["family_ready"])
        self.assertFalse(receipt["source_artifact_independently_verified"])
        self.assertFalse(receipt["unit_contract_verified"])
        self.assertFalse(receipt["tariff_contract_verified"])
        self.assertEqual(receipt["provider_gets"], 0)
        self.assertEqual(receipt["real_orders_sent"], 0)

    def test_default_unpinned_signer_never_writes_receipt(self):
        response = attest_one_pinned(
            self.db, decision_key=KEY,
            signing_key_path=self.signer_path,
            expected_release_sha256=RELEASE)
        self.assertEqual(response["reason"], "CAUSAL_SIGNER_OR_RELEASE_TRUST_UNAVAILABLE")
        self.assertFalse(response["signature_created"])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM paper_causal_decision_receipts").fetchone()[0], 0)

    def test_signer_only_after_commit_and_book2_later_than_signed_known_at(self):
        attested = self.signed()
        self.assertEqual(attested["state"], "RECORDED_UNVERIFIED_UNTIL_PINNED_VERIFIER")
        self.assertTrue(attested["signature_created"])
        self.capture(AT + timedelta(seconds=4))
        verdict = self.inspect()
        self.assertEqual(verdict["state"], "EVIDENCE_ONLY_NO_GO")
        self.assertTrue(verdict["decision_receipt_known_at_verified"])
        self.assertTrue(verdict["decision_signature_verified"])
        self.assertTrue(verdict["book1_quote_bound"])
        self.assertTrue(verdict["book2_after_signed_known_at"])
        self.assertTrue(verdict["book2_first_top_positive"])
        self.assertEqual(verdict["decision_known_at"], KNOWN_AT.isoformat())
        self.assert_evidence_only(verdict)

    def test_even_a_signed_receipt_is_untrusted_without_release_pins(self):
        self.signed()
        self.capture(AT + timedelta(seconds=4))
        verdict = self.inspect(trusted=False)
        self.assertEqual(verdict["reason"], "CAUSAL_TRUST_ANCHOR_UNAVAILABLE")
        self.assertFalse(verdict["decision_receipt_known_at_verified"])
        self.assert_evidence_only(verdict)

    def test_valid_signature_from_wrong_key_or_wrong_release_never_writes(self):
        other = Ed25519PrivateKey.generate()
        wrong = self.signer_path.parent / "wrong.key"
        wrong.write_bytes(other.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
            serialization.NoEncryption()))
        wrong.chmod(0o600)
        denied_key = self.signed(signer_path=wrong)
        self.assertEqual(denied_key["reason"], "CAUSAL_SIGNER_KEY_UNPINNED")
        denied_release = self.signed(release="b" * 64)
        self.assertEqual(denied_release["reason"], "CAUSAL_SIGNER_RELEASE_UNPINNED")
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM paper_causal_decision_receipts").fetchone()[0], 0)

    def test_signer_replay_is_denied_without_new_known_at(self):
        first = self.signed()
        replay = self.signed(known_at=KNOWN_AT + timedelta(hours=1))
        self.assertEqual(replay["reason"], "CAUSAL_RECEIPT_ALREADY_COMMITTED_NO_REPLAY")
        self.assertFalse(replay["signature_created"])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT decision_known_at FROM paper_causal_decision_receipts").fetchone()[0],
                             first["decision_known_at"])

    def test_real_separate_process_observes_commit_and_signs_synthetic_row(self):
        # The private-key holder runs in another interpreter/PID, never inside
        # be_paper_engine's sqlite transaction or provider request callback.
        child = "\n".join((
            "import json, os, sys",
            "import rc6_paper_causal_decision_receipt as c",
            "from scripts import rc6_paper_postcommit_signer as signer",
            "from scripts.rc6_paper_postcommit_signer import attest_one_pinned",
            "from pathlib import Path",
            "c.PINNED_DECISION_SIGNER_KEY_SHA256 = sys.argv[4]",
            "c.PINNED_RELEASE_SHA256 = sys.argv[5]",
            "signer.PINNED_DECISION_DB_ABSPATH = Path(sys.argv[1])",
            "c._now = lambda: sys.argv[6]",
            "r = attest_one_pinned(Path(sys.argv[1]), decision_key=sys.argv[2],"
            " signing_key_path=Path(sys.argv[3]), expected_release_sha256=sys.argv[5])",
            "print(json.dumps({'pid': os.getpid(), 'result': r}, sort_keys=True))",
        ))
        project_root = Path(__file__).resolve().parents[1]
        isolated_env = {"PATH": os.environ.get("PATH", ""),
                        "PYTHONPATH": str(project_root),
                        "PYTHONDONTWRITEBYTECODE": "1"}
        completed = subprocess.run(
            [sys.executable, "-c", child, str(self.db), KEY,
             str(self.signer_path), self.pin, RELEASE, KNOWN_AT.isoformat()],
            cwd=project_root, env=isolated_env, capture_output=True,
            text=True, timeout=20, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertNotEqual(result["pid"], os.getpid())
        self.assertTrue(result["result"]["signature_created"])
        self.capture(AT + timedelta(seconds=4))
        verdict = self.inspect()
        self.assertEqual(verdict["state"], "EVIDENCE_ONLY_NO_GO")
        self.assert_evidence_only(verdict)

    def test_book2_exact_latency_cut_is_not_a_future_witness(self):
        self.signed()
        self.capture(AT + timedelta(seconds=3))
        result = self.inspect()
        self.assertEqual(result["reason"], "PPI_BOOK_NOT_STRICTLY_AFTER_SIGNED_KNOWN_AT_PLUS_LATENCY")
        self.assertFalse(result["book2_after_signed_known_at"])
        self.assert_evidence_only(result)

    def test_signer_receipt_created_after_book2_cannot_backdate_pit(self):
        self.capture(AT + timedelta(seconds=4))
        self.signed(known_at=AT + timedelta(seconds=5))
        result = self.inspect()
        self.assertEqual(result["reason"], "PPI_BOOK_NOT_STRICTLY_AFTER_SIGNED_KNOWN_AT_PLUS_LATENCY")
        self.assert_evidence_only(result)

    def test_first_future_zero_depth_cannot_be_replaced_by_later_favorable_book(self):
        self.signed()
        self.capture(AT + timedelta(seconds=4), ask="0", ask_size="0")
        self.capture(AT + timedelta(seconds=5), ask="100.40", ask_size="30")
        result = self.inspect()
        self.assertEqual(result["reason"], "PPI_FIRST_BOOK2_TOP_DEPTH_ZERO_OR_ABSENT")
        self.assertFalse(result["book2_first_top_positive"])
        self.assert_evidence_only(result)

    def test_cross_session_native_book_cannot_count(self):
        self.signed()
        self.capture(AT + timedelta(days=1))
        result = self.inspect()
        self.assertEqual(result["reason"], "PPI_BOOK2_CROSS_SESSION_HOLD")
        self.assert_evidence_only(result)

    def test_book1_must_match_freeze_sha_and_actual_quote(self):
        self.signed()
        self.capture(AT + timedelta(seconds=4))
        result = self.inspect(identity=("GGAL", "ACCIONES", "BYMA", "USD", "A-24HS"))
        self.assertEqual(result["reason"], "PIT_SIGNED_FREEZE_IDENTITY_MISMATCH")
        self.assert_evidence_only(result)

    def test_family_specific_nav_rejects_exchange_book_witness(self):
        self.signed()
        result = self.inspect(family_group="FCI")
        self.assertEqual(result["reason"], "FCI_NAV_CUTOFF_NOT_EXCHANGE_BOOK")
        self.assert_evidence_only(result)


if __name__ == "__main__":
    unittest.main()
