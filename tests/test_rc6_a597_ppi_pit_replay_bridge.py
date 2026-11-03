"""A597 finite OFFHOST synthetic regression tests: NO live PPI Book/fills."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from be_paper_engine import Quote
import rc6_ppi_scanner_provenance as journal
from rc6_ppi_pit_replay_bridge import (
    NINE_FAMILY_GROUPS, causal_book_witness, nine_family_witness_matrix,
)


ART = timezone(timedelta(hours=-3))
START = datetime(2026, 11, 3, 11, 0, tzinfo=ART)
IDENTITY = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
FAKE_DIGEST = "a" * 64


class TestPpiCausalWitnessBridge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "market" / journal.PIT_JOURNAL_FILENAME
        self.path.parent.mkdir(parents=True)

    def capture(self, second, *, price="100.20", size="21"):
        at = START + timedelta(seconds=second)
        quote = Quote("GGAL", "ACCIONES", "A-24HS", Decimal("100"),
                      Decimal("100"), Decimal(price), Decimal("14"), Decimal(size),
                      at.isoformat(), currency="ARS", market="BYMA",
                      trade_at=at.isoformat(), book_at=at.isoformat(), last_kind="TRADE")
        book = {"date": at.isoformat(),
                "bids": [{"position": 0, "price": "100", "quantity": "14"}],
                "offers": [{"position": 0, "price": price, "quantity": size}]}
        result = journal.capture_native_book_journal(
            self.path, quote, raw_book=book,
            request=("GGAL", "ACCIONES", "A-24HS"), identity=IDENTITY,
            observed_at=quote.observed_at)
        self.assertEqual(result["status"], "CAPTURED")

    def witness(self, **kwargs):
        data = {"family_group": "ACCIONES", "identity": IDENTITY,
                "decision_at": START.isoformat(), "latency_seconds": 1,
                "side": "BUY", "decision_receipt_sha256": FAKE_DIGEST,
                "source_artifact_sha256": "b" * 64}
        data.update(kwargs)
        return causal_book_witness(self.path, **data)

    def test_missing_native_book_is_not_verified_or_filled(self):
        result = self.witness()
        self.assertEqual(result["state"], "NO_VERIFICADO")
        self.assertEqual(result["reason"], "PPI_BOOK_JOURNAL_ABSENT")
        self.assertFalse(result["fill_accredited"])
        self.assertEqual(result["provider_gets"], 0)

    def test_book_at_equal_to_decision_plus_latency_not_eligible(self):
        self.capture(1)
        result = self.witness()
        self.assertEqual(result["reason"], "PPI_BOOK_NOT_STRICTLY_AFTER_DECISION_PLUS_LATENCY")
        self.assertFalse(result["causal_clock_witness"])

    def test_first_strictly_future_event_is_pinned_but_never_fill(self):
        self.capture(1)
        self.capture(2)
        self.capture(3)
        result = self.witness()
        rows, receipt = journal.verified_native_book_journal(self.path)
        self.assertEqual(result["candidate_count"], 2)
        self.assertTrue(result["causal_clock_witness"])
        self.assertEqual(result["journal_event_sha256"], rows[1]["event_sha256"])
        self.assertEqual(result["journal_chain_tip_sha256"], receipt["latest_sha256"])
        self.assertEqual(result["dependency_edge"]["relation"],
                         "POST_DECISION_NATIVE_BOOK_OBSERVATION_ONLY")
        self.assertTrue(result["first_book_has_positive_native_top_level"])
        self.assertEqual(result["state"], "NO_VERIFICADO")
        self.assertFalse(result["fill_accredited"])
        self.assertFalse(result["unit_contract_verified"])
        self.assertFalse(result["decision_signature_verified"])
        self.assertFalse(result["source_artifact_independently_verified"])

    def test_zero_top_depth_does_not_cherry_pick_later_positive_book(self):
        self.capture(2, price="0", size="0")
        self.capture(3, price="100.20", size="21")
        result = self.witness()
        rows, _ = journal.verified_native_book_journal(self.path)
        self.assertEqual(result["journal_event_sha256"], rows[0]["event_sha256"])
        self.assertFalse(result["first_book_has_positive_native_top_level"])
        self.assertEqual(result["reason"], "PIT_NATIVE_BOOK_TOP_DEPTH_ZERO_OR_ABSENT")

    def test_no_identity_substitution_and_no_universal_iol_demand(self):
        self.capture(2)
        result = self.witness(identity=("GGAL", "ACCIONES", "BYMA", "USD", "A-24HS"))
        self.assertEqual(result["reason"], "PPI_BOOK_NOT_STRICTLY_AFTER_DECISION_PLUS_LATENCY")
        valid = self.witness()
        self.assertTrue(valid["causal_clock_witness"])
        self.assertFalse(valid["complementary_source_required_universally"])

    def test_hash_tampering_blocks_all_future_book_witnesses(self):
        self.capture(2)
        raw = self.path.read_text()
        self.path.write_text(raw.replace('"100.20"', '"100.21"', 1))
        self.path.chmod(0o600)
        result = self.witness()
        self.assertEqual(result["state"], "BLOQUEADO")
        self.assertIsNone(result["journal_event_sha256"])
        self.assertFalse(result["causal_clock_witness"])

    def test_untrusted_digest_or_naive_clock_fails_closed(self):
        self.capture(2)
        for changes in ({"source_artifact_sha256": "bogus"},
                        {"decision_at": "2026-11-03T11:00:00"},
                        {"latency_seconds": -1},
                        {"side": "SHORT"},
                        {"family_group": "ETFS"}):
            with self.subTest(changes=changes):
                result = self.witness(**changes)
                self.assertEqual(result["state"], "BLOQUEADO")
                self.assertFalse(result["fill_accredited"])

    def test_nine_groups_absent_decision_explicit_and_etf_excluded(self):
        matrix = nine_family_witness_matrix(self.path, {})
        self.assertEqual(tuple(matrix["families"]), NINE_FAMILY_GROUPS)
        self.assertEqual(matrix["family_count"], 9)
        self.assertNotIn("ETFS", matrix["families"])
        self.assertEqual(matrix["fills_accredited"], 0)
        self.assertTrue(all(row["reason"] == "DECISION_RECEIPT_NOT_SUPPLIED"
                            and row["state"] == "NO_VERIFICADO"
                            for row in matrix["families"].values()))

    def test_matrix_is_readonly_reuses_existing_verified_native_books(self):
        self.capture(2)
        original = self.path.read_bytes()
        matrix = nine_family_witness_matrix(self.path, {
            "ACCIONES": {"identity": IDENTITY, "decision_at": START.isoformat(),
                         "latency_seconds": 1, "side": "BUY"},
            "CAUCIONES": {"identity": ("CAUCION", "CAUCIONES", "BYMA", "ARS", "A-24HS"), "decision_at": START.isoformat(),
                          "latency_seconds": 1, "side": "BUY"}})
        self.assertEqual(matrix["families"]["ACCIONES"]["candidate_count"], 1)
        self.assertEqual(matrix["families"]["CAUCIONES"]["candidate_count"], 0)
        self.assertFalse(matrix["families"]["CAUCIONES"]["family_ready"])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(matrix["provider_gets"], 0)
        self.assertEqual(matrix["real_orders_sent"], 0)


if __name__ == "__main__":
    unittest.main()
