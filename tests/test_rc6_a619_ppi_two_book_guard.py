"""A619: OFFHOST regression. Synthetic facts are never live PPI validation."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from be_paper_engine import Quote
import rc6_ppi_scanner_provenance as journal
from rc6_ppi_pit_two_book_guard import diagnose_two_book_pit, diagnostics_json


ART = timezone(timedelta(hours=-3))
START = datetime(2026, 11, 3, 11, 0, tzinfo=ART)
IDENTITY = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
KEY = "A619:GGAL:BUY:OFFHOST"


class TestA619OffhostTwoBookGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        directory = Path(self.tmp.name)
        self.journal = directory / "journal" / journal.PIT_JOURNAL_FILENAME
        self.journal.parent.mkdir()
        self.db = directory / "paper-snapshot.db"
        self.capture(-2)
        with sqlite3.connect(self.db) as connection:
            connection.execute("CREATE TABLE decision_evidence_snapshots ("
                               "decision_key TEXT PRIMARY KEY, captured_at TEXT, "
                               "schema_version TEXT,payload_sha256 TEXT,payload_json TEXT)")
        self.write_decision()

    def capture(self, seconds, *, ask="100.20", ask_size="21", symbol="GGAL"):
        at = START + timedelta(seconds=seconds)
        quote = Quote(symbol, "ACCIONES", "A-24HS", Decimal("100"),
                      Decimal("100"), Decimal(ask), Decimal("14"), Decimal(ask_size),
                      at.isoformat(), currency="ARS", market="BYMA",
                      trade_at=at.isoformat(), book_at=at.isoformat(), last_kind="TRADE")
        book = {"date": at.isoformat(),
                "bids": [{"position": 0, "price": "100", "quantity": "14"}],
                "offers": [{"position": 0, "price": ask, "quantity": ask_size}]}
        receipt = journal.capture_native_book_journal(
            self.journal, quote, raw_book=book,
            request=(symbol, "ACCIONES", "A-24HS"),
            identity=(symbol, "ACCIONES", "BYMA", "ARS", "A-24HS"),
            observed_at=quote.observed_at)
        self.assertEqual(receipt["status"], "CAPTURED")

    def payload(self):
        book1 = (START - timedelta(seconds=2)).isoformat()
        at = START.isoformat()
        return {"schema": "rc6.decision-inputs.v1",
                "capture_phase": "NATIVE_DECISION",
                "decision_key": KEY, "decision_at": at,
                "captured_at": book1,
                "decision": {"symbol": "GGAL", "action": "BUY"},
                "quote_used": {"symbol": "GGAL", "asset_class": "ACCIONES",
                               "market": "BYMA", "currency": "ARS",
                               "settlement": "A-24HS", "book_at": book1,
                               "observed_at": book1,
                               "bid": "100", "ask": "100.20",
                               "bid_size": "14", "ask_size": "21"},
                "runtime": {"execution_mode": "PRODUCTION_PAPER_SIMULATED",
                            "real_money_authorized": False, "decision_at": at}}

    def write_decision(self, payload=None, *, corrupt_digest=False):
        payload = self.payload() if payload is None else payload
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(raw.encode()).hexdigest()
        if corrupt_digest:
            digest = "0" * 64
        with sqlite3.connect(self.db) as connection:
            connection.execute("DELETE FROM decision_evidence_snapshots")
            connection.execute("INSERT INTO decision_evidence_snapshots VALUES(?,?,?,?,?)",
                               (KEY, payload.get("captured_at"),
                                "rc6.decision-inputs.v1", digest, raw))

    def analyze(self, **changes):
        args = {"decision_key": KEY, "family_group": "ACCIONES",
                "identity": IDENTITY, "latency_seconds": 1}
        args.update(changes)
        result = diagnose_two_book_pit(self.journal, self.db, **args)
        self.assertIs(result["fill_accredited"], False)
        self.assertIs(result["family_ready"], False)
        self.assertIs(result["decision_signature_verified"], False)
        self.assertIs(result["source_artifact_independently_verified"], False)
        self.assertIs(result["decision_receipt_known_at_verified"], False)
        self.assertIsNone(result["decision_receipt_known_at"])
        self.assertIs(result["two_book_clock_witness"], False)
        self.assertIs(result["quantity_unit_verified"], False)
        self.assertIs(result["tariff_contract_verified"], False)
        self.assertEqual(result["provider_gets"], 0)
        self.assertEqual(result["real_orders_sent"], 0)
        return result

    def test_book1_present_but_no_future_book2_is_not_a_fill(self):
        result = self.analyze()
        self.assertTrue(result["book1_asof_quote_bound"])
        self.assertTrue(result["decision_snapshot_digest_verified"])
        self.assertEqual(result["reason"], "PPI_BOOK_NOT_STRICTLY_AFTER_DECISION_PLUS_LATENCY")
        self.assertFalse(result["two_book_clock_witness"])

    def test_strict_latency_first_book2_then_witness_not_authority(self):
        self.capture(1)
        equal_cutoff = self.analyze()
        self.assertFalse(equal_cutoff["book2_strictly_future"])
        self.capture(2)
        receipt = self.analyze()
        rows, chain = journal.verified_native_book_journal(self.journal)
        self.assertTrue(receipt["market_clock_pair_relative_to_claimed_decision"])
        self.assertFalse(receipt["two_book_clock_witness"])
        self.assertTrue(receipt["book2_strictly_future"])
        self.assertTrue(receipt["book2_first_top_positive"])
        self.assertEqual(receipt["book1_event_sha256"], rows[0]["event_sha256"])
        self.assertEqual(receipt["book2_event_sha256"], rows[2]["event_sha256"])
        self.assertEqual(receipt["journal_chain_tip_sha256"], chain["latest_sha256"])
        self.assertEqual(receipt["reason"], "PIT_MARKET_CLOCK_PAIR_ONLY_DECISION_RECEIPT_KNOWN_AT_UNVERIFIED")
        self.assertEqual(json.loads(diagnostics_json(self.journal, self.db,
                         decision_key=KEY, family_group="ACCIONES",
                         identity=IDENTITY, latency_seconds=1)), receipt)

    def test_first_zero_depth_cannot_cherry_pick_later_book(self):
        self.capture(2, ask="0", ask_size="0")
        self.capture(3, ask="100.40", ask_size="30")
        receipt = self.analyze()
        rows, _ = journal.verified_native_book_journal(self.journal)
        self.assertEqual(receipt["book2_event_sha256"], rows[1]["event_sha256"])
        self.assertEqual(receipt["reason"], "PPI_BOOK2_FIRST_TOP_DEPTH_ZERO_OR_ABSENT")
        self.assertFalse(receipt["two_book_clock_witness"])

    def test_digest_is_integrity_not_independent_signature(self):
        self.capture(2)
        # A caller can forge BOTH content and hash. This must never become
        # an external decision authentication or an executable PAPER fill.
        fake = self.payload()
        fake["inputs_used"] = {"untrusted_user_claim": "SIGNED"}
        self.write_decision(fake)
        receipt = self.analyze()
        self.assertTrue(receipt["decision_snapshot_digest_verified"])
        self.assertFalse(receipt["decision_signature_verified"])
        self.assertFalse(receipt["decision_origin_independently_verified"])
        self.assertFalse(receipt["fill_accredited"])
        self.write_decision(corrupt_digest=True)
        corrupt = self.analyze()
        self.assertEqual(corrupt["state"], "BLOQUEADO")
        self.assertEqual(corrupt["reason"], "DECISION_SNAPSHOT_DIGEST_MISMATCH")

    def test_snapshot_written_after_book2_cannot_claim_prebook2_decision_known(self):
        """Adversarial post-hoc receipt: captured_at is not a trusted known_at."""
        self.capture(2)
        frozen = self.payload()
        frozen["capture_phase"] = "ATOMIC_PAPER_ADMISSION"
        # The PAPER engine writes this phase only after admission. A self-
        # described decision_at in the past cannot establish an early receipt.
        frozen["captured_at"] = (START + timedelta(seconds=12)).isoformat()
        self.write_decision(frozen)
        receipt = self.analyze()
        self.assertTrue(receipt["market_clock_pair_relative_to_claimed_decision"])
        self.assertFalse(receipt["two_book_clock_witness"])
        self.assertIsNone(receipt["decision_receipt_known_at"])
        self.assertFalse(receipt["decision_receipt_known_at_verified"])
        self.assertFalse(receipt["decision_signature_verified"])

    def test_native_phase_captured_at_is_quote_receipt_not_commit_clock(self):
        self.capture(2)
        frozen = self.payload()
        frozen["captured_at"] = (START + timedelta(seconds=9)).isoformat()
        self.write_decision(frozen)
        receipt = self.analyze()
        self.assertEqual(receipt["reason"], "DECISION_CAPTURE_PHASE_CLOCK_INVALID")
        self.assertFalse(receipt["market_clock_pair_relative_to_claimed_decision"])

    def test_book1_quote_must_exactly_match_predecision_native_event(self):
        self.capture(2)
        fake = self.payload()
        fake["quote_used"]["ask"] = "99.00"
        self.write_decision(fake)
        mismatch = self.analyze()
        self.assertEqual(mismatch["reason"], "PPI_BOOK1_DECISION_QUOTE_MISMATCH")
        self.assertFalse(mismatch["book1_asof_quote_bound"])

    def test_book1_staleness_and_cross_session_block(self):
        self.assertEqual(self.analyze(max_book1_age_seconds=1)["reason"],
                         "PPI_BOOK1_STALE_AT_DECISION")
        # A later-session market book is not an intraday IOC continuation.
        next_day = START + timedelta(days=1, seconds=2)
        at = next_day
        quote = Quote("GGAL", "ACCIONES", "A-24HS", Decimal("100"),
                      Decimal("100"), Decimal("100.30"), Decimal("14"), Decimal("21"),
                      at.isoformat(), currency="ARS", market="BYMA",
                      trade_at=at.isoformat(), book_at=at.isoformat(), last_kind="TRADE")
        book = {"date": at.isoformat(),
                "bids": [{"position": 0, "price": "100", "quantity": "14"}],
                "offers": [{"position": 0, "price": "100.30", "quantity": "21"}]}
        receipt = journal.capture_native_book_journal(
            self.journal, quote, raw_book=book,
            request=("GGAL", "ACCIONES", "A-24HS"),
            identity=IDENTITY, observed_at=quote.observed_at)
        self.assertEqual(receipt["status"], "CAPTURED")
        across = self.analyze()
        self.assertEqual(across["reason"], "PPI_BOOK2_CROSS_SESSION_OR_ORDER_INVALID")
        self.assertFalse(across["two_book_clock_witness"])

    def test_book1_no_asof_and_malformed_identity_fail_closed(self):
        no_journal = self.journal.parent / "absent.json"
        absent = diagnose_two_book_pit(
            no_journal, self.db, decision_key=KEY, family_group="ACCIONES",
            identity=IDENTITY, latency_seconds=1)
        self.assertEqual(absent["reason"], "PPI_BOOK_JOURNAL_ABSENT")
        self.assertFalse(absent["fill_accredited"])
        no_book1 = self.analyze(identity=("OTHER", "ACCIONES", "BYMA", "ARS", "A-24HS"))
        self.assertEqual(no_book1["reason"], "DECISION_SNAPSHOT_IDENTITY_OR_MODE_INVALID")
        invalid = self.analyze(identity=("GGAL", "BONOS", "BYMA", "ARS", "A-24HS"))
        self.assertEqual(invalid["state"], "BLOQUEADO")
        self.assertEqual(invalid["reason"], "PIT_TWO_BOOK_INPUT_INVALID")

    def test_decision_clock_cannot_be_reconstructed_from_a_future_book(self):
        snapshot = self.payload()
        future = (START + timedelta(seconds=5)).isoformat()
        snapshot["quote_used"]["observed_at"] = future
        snapshot["quote_used"]["book_at"] = future
        self.write_decision(snapshot)
        result = self.analyze()
        self.assertEqual(result["reason"], "DECISION_CAUSAL_CLOCK_UNATTESTED")
        self.assertFalse(result["book1_asof_quote_bound"])

    def test_tampered_native_journal_chain_blocks_book1(self):
        self.capture(2)
        original = self.journal.read_text()
        self.journal.write_text(original.replace('"100.20"', '"100.21"', 1))
        self.journal.chmod(0o600)
        result = self.analyze()
        self.assertEqual(result["state"], "BLOQUEADO")
        self.assertFalse(result["book1_asof_quote_bound"])
        self.assertFalse(result["two_book_clock_witness"])

    def test_fci_nav_cutoff_never_uses_exchange_book(self):
        result = self.analyze(family_group="FCI", identity=(
            "FUNDO", "FCI", "PPI", "ARS", "CI"))
        self.assertEqual(result["reason"], "FCI_NAV_CUTOFF_SETTLEMENT_AUTHORITY_REQUIRED")
        self.assertFalse(result["decision_snapshot_digest_verified"])

    def test_readonly_never_mutates_journal_or_offhost_db(self):
        self.capture(2)
        before_journal = self.journal.read_bytes()
        before_db = self.db.read_bytes()
        self.analyze()
        self.assertEqual(before_journal, self.journal.read_bytes())
        self.assertEqual(before_db, self.db.read_bytes())


if __name__ == "__main__":
    unittest.main()
