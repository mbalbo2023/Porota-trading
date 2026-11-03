"""A556 OFFHOST tests: *genuine-source callsite required*; fixtures are synthetic.

No SDK session/provider GET, no host mount, no PAPER order or fill is created.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from be_paper_engine import Quote
import rc6_ppi_scanner_provenance as pit


ART = timezone(timedelta(hours=-3))
START = datetime(2026, 11, 3, 11, 0, 0, tzinfo=ART)
IDENTITY = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
REQUEST = ("GGAL", "ACCIONES", "A-24HS")


def fixture(*, book_seconds=0, observed_seconds=None, book_price="100.20",
            trade_seconds=None, identity=IDENTITY):
    at_book = START + timedelta(seconds=book_seconds)
    at_seen = START + timedelta(seconds=book_seconds if observed_seconds is None else observed_seconds)
    at_trade = START + timedelta(seconds=book_seconds if trade_seconds is None else trade_seconds)
    quote = Quote(identity[0], identity[1], identity[4], Decimal("100"),
                  Decimal("100.00"), Decimal(book_price), Decimal("14"),
                  Decimal("21"), at_seen.isoformat(timespec="microseconds"),
                  currency=identity[3], market=identity[2],
                  trade_at=at_trade.isoformat(), book_at=at_book.isoformat(), last_kind="TRADE")
    book = {"date": at_book.isoformat(),
            "bids": [{"position": 1, "price": "100", "quantity": "14"},
                     {"position": 2, "price": "99.8", "quantity": "300"}],
            "offers": [{"position": 1, "price": book_price, "quantity": "21"},
                       {"position": 2, "price": "100.5", "quantity": "200"}]}
    return quote, book


class TestNativeBookCausalJournal(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.receipt = Path(self.temporary.name) / "market" / pit.FILENAME
        self.writer = pit.ScannerPrimaryRecorder(self.receipt)
        self.journal = self.receipt.with_name(pit.PIT_JOURNAL_FILENAME)

    def capture(self, *, book_seconds=0, observed_seconds=None, book_price="100.20",
                trade_seconds=None, identity=IDENTITY, request=REQUEST, book_override=None):
        quote, book = fixture(book_seconds=book_seconds, observed_seconds=observed_seconds,
                              book_price=book_price, trade_seconds=trade_seconds, identity=identity)
        return self.writer.capture_raw_book(
            quote, raw_book=book if book_override is None else book_override,
            request=request, identity=identity, observed_at=quote.observed_at)

    def test_persists_two_distinct_native_books_full_depth_and_hash_chain_0600(self):
        first = self.capture()
        second = self.capture(book_seconds=2, book_price="100.30")
        self.assertEqual([first["status"], second["status"]], ["CAPTURED", "CAPTURED"])
        self.assertEqual(os.stat(self.journal).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.journal.with_name(self.journal.name + ".lock")).st_mode & 0o777, 0o600)
        rows, receipt = pit.verified_native_book_journal(self.journal)
        self.assertEqual(receipt["state"], "HASH_CHAIN_VERIFIED")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["book"]["bids"][1]["quantity"], "300")
        self.assertEqual(rows[1]["book"]["offers"][1]["quantity"], "200")
        self.assertEqual(rows[0]["canonical_identity"], list(IDENTITY))
        self.assertEqual(rows[1]["previous_sha256"], rows[0]["event_sha256"])
        self.assertEqual(rows[0]["source"], "PPI")
        self.assertEqual(rows[0]["quantity_unit"], "NO_VERIFICADO")
        self.assertEqual(rows[0]["normalized_book_sha256"], pit._pit_hash(rows[0]["book"]))
        self.assertNotIn("raw_book_sha256", rows[0])
        self.assertEqual(rows[0]["known_at"], rows[0]["observed_at"])
        self.assertNotEqual(rows[0]["native_book_at"], rows[1]["native_book_at"])

    def test_native_book_equal_to_decision_plus_latency_never_qualifies(self):
        self.capture(book_seconds=1)
        self.capture(book_seconds=2, book_price="100.30")
        matching, receipt = pit.strictly_future_native_books(
            self.journal, identity=IDENTITY, decision_at=START.isoformat(), latency_seconds=1)
        self.assertEqual(receipt["eligible_future_books"], 1)
        self.assertEqual(receipt["fill_accredited"], False)
        self.assertEqual(matching[0]["native_book_at"], (START + timedelta(seconds=2)).isoformat())
        equality, _ = pit.strictly_future_native_books(
            self.journal, identity=IDENTITY, decision_at=START.isoformat(), latency_seconds=2)
        self.assertFalse(equality)

    def test_same_book_deduplicates_without_refreshing_known_at(self):
        first = self.capture()
        again = self.capture(observed_seconds=5, trade_seconds=4)
        self.assertEqual(again["status"], "DEDUPLICATED")
        self.assertEqual(first["event_sha256"], again["event_sha256"])
        rows, _ = pit.verified_native_book_journal(self.journal)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["known_at"], START.isoformat(timespec="microseconds"))

    def test_same_native_clock_conflicting_depth_is_failclosed(self):
        self.capture()
        rejected = self.capture(book_price="101.90", observed_seconds=5)
        self.assertEqual(rejected["status"], "CAPTURE_INVALID_HOLD")
        self.assertEqual(rejected["reason"], "PPI_BOOK_NATIVE_CLOCK_CONFLICT")
        self.assertEqual(pit.verified_native_book_journal(self.journal)[1]["records"], 1)

    def test_no_native_time_or_future_provider_clock_is_rejected_without_file(self):
        rejected = self.capture(book_seconds=5, observed_seconds=4)
        self.assertEqual(rejected["reason"], "PPI_BOOK_NATIVE_PIT_CLOCK_INVALID")
        self.assertFalse(self.journal.exists())
        quote, book = fixture()
        quote = Quote(**{**quote.__dict__, "book_at": None})
        rejected = self.writer.capture_raw_book(quote, raw_book=book,
                                                request=REQUEST, identity=IDENTITY,
                                                observed_at=quote.observed_at)
        self.assertEqual(rejected["reason"], "PPI_BOOK_NATIVE_PIT_CLOCK_INVALID")

    def test_missing_identity_and_conflicting_request_are_rejected(self):
        q, book = fixture()
        wrong = self.writer.capture_raw_book(q, raw_book=book, request=REQUEST,
                                             identity=("GGAL", "ACCIONES", "BYMA", "USD", "A-24HS"),
                                             observed_at=q.observed_at)
        self.assertEqual(wrong["reason"], "PPI_BOOK_FIVE_KEY_MISMATCH")
        wrong = self.writer.capture_raw_book(q, raw_book=book,
                                             request=("AAPL", "ACCIONES", "A-24HS"),
                                             identity=IDENTITY, observed_at=q.observed_at)
        self.assertEqual(wrong["reason"], "PPI_BOOK_REQUEST_OR_RECEIPT_IDENTITY_MISMATCH")
        self.assertFalse(self.journal.exists())

    def test_missing_provider_depth_never_gets_recreated_from_best_bid(self):
        quote, book = fixture()
        del book["bids"]
        result = self.writer.capture_raw_book(quote, raw_book=book, request=REQUEST,
                                              identity=IDENTITY, observed_at=quote.observed_at)
        self.assertEqual(result["reason"], "PPI_BOOK_NATIVE_SHAPE_UNVERIFIED")
        self.assertFalse(self.journal.exists())

    def test_adversarial_native_depth_cannot_disagree_with_normalized_best(self):
        # A560 P1: a Book of another instrument may carry the same provider
        # date, but mismatched best bid/ask or size. Never chain that evidence.
        for field, altered in (("bid", "99.99"), ("ask", "100.21"),
                               ("bid_size", "15"), ("ask_size", "22")):
            with self.subTest(field=field):
                quote, book = fixture()
                quote = Quote(**{**quote.__dict__, field: Decimal(altered)})
                result = self.writer.capture_raw_book(quote, raw_book=book,
                                                      request=REQUEST, identity=IDENTITY,
                                                      observed_at=quote.observed_at)
                self.assertEqual(result["status"], "CAPTURE_INVALID_HOLD")
                self.assertEqual(result["reason"], "PPI_BOOK_NORMALIZED_BEST_LEVEL_MISMATCH")
                self.assertFalse(self.journal.exists())

    def test_missing_book_side_requires_normalized_zero_not_fallback_price(self):
        quote, book = fixture()
        book["bids"] = []
        inconsistent = self.writer.capture_raw_book(
            quote, raw_book=book, request=REQUEST, identity=IDENTITY,
            observed_at=quote.observed_at)
        self.assertEqual(inconsistent["reason"], "PPI_BOOK_NORMALIZED_BEST_LEVEL_MISMATCH")
        self.assertFalse(self.journal.exists())
        quote = Quote(**{**quote.__dict__, "bid": Decimal(0), "bid_size": Decimal(0)})
        accepted = self.writer.capture_raw_book(
            quote, raw_book=book, request=REQUEST, identity=IDENTITY,
            observed_at=quote.observed_at)
        self.assertEqual(accepted["status"], "CAPTURED")
        rows, receipt = pit.verified_native_book_journal(self.journal)
        self.assertEqual(receipt["state"], "HASH_CHAIN_VERIFIED")
        self.assertEqual(rows[0]["book"]["bids"], [])
        self.assertEqual(len(rows[0]["book"]["offers"]), 2)

    def test_official_ppi_zero_based_positions_are_preserved_not_renumbered(self):
        # PPI REST MarketData/Book official example: position:0 on both sides.
        quote, book = fixture()
        book["bids"][0]["position"] = 0
        book["offers"][0]["position"] = 0
        result = self.writer.capture_raw_book(quote, raw_book=book, request=REQUEST,
                                              identity=IDENTITY, observed_at=quote.observed_at)
        self.assertEqual(result["status"], "CAPTURED")
        rows, _ = pit.verified_native_book_journal(self.journal)
        self.assertEqual(rows[0]["book"]["bids"][0]["position"], 0)
        self.assertEqual(rows[0]["book"]["offers"][0]["position"], 0)

    def test_official_ppi_zero_price_depth_is_evidence_not_fill(self):
        # PPI REST official example includes {position:0,price:0,quantity:0}.
        quote, book = fixture()
        book["bids"] = [{"position": 0, "price": 0, "quantity": 0}]
        book["offers"] = [{"position": 0, "price": 0, "quantity": 0}]
        quote = Quote(**{**quote.__dict__, "bid": Decimal(0), "bid_size": Decimal(0),
                         "ask": Decimal(0), "ask_size": Decimal(0)})
        result = self.writer.capture_raw_book(quote, raw_book=book, request=REQUEST,
                                              identity=IDENTITY, observed_at=quote.observed_at)
        self.assertEqual(result["status"], "CAPTURED")
        rows, receipt = pit.verified_native_book_journal(self.journal)
        self.assertEqual(receipt["state"], "HASH_CHAIN_VERIFIED")
        self.assertEqual(rows[0]["book"]["bids"][0]["price"], "0")
        # A timestamp after the decision can nominate a Book, but never
        # assert fill when depth price/units are unusable.
        future, receipt = pit.strictly_future_native_books(
            self.journal, identity=IDENTITY,
            decision_at=(START - timedelta(seconds=5)).isoformat(), latency_seconds=1)
        self.assertEqual(len(future), 1)
        self.assertIs(receipt["fill_accredited"], False)

    def test_bounded_session_blocks_second_without_replacing_evidence(self):
        first = self.capture()
        original = self.journal.read_bytes()
        with patch.object(pit, "PIT_SESSION_MAX_RECORDS", 1):
            second = self.capture(book_seconds=3)
        self.assertEqual(first["status"], "CAPTURED")
        self.assertEqual(second["status"], "CAPTURE_CAPACITY_HOLD")
        self.assertEqual(self.journal.read_bytes(), original)

    def test_total_bytes_cap_holds_without_loss(self):
        self.capture()
        original = self.journal.read_bytes()
        # Existing journal fits; the next event would exceed the fixed budget.
        with patch.object(pit, "PIT_JOURNAL_MAX_BYTES", len(original) + 4):
            response = self.capture(book_seconds=3)
        self.assertEqual(response["status"], "CAPTURE_CAPACITY_HOLD")
        self.assertEqual(response["reason"], "PPI_BOOK_TOTAL_BYTES_BUDGET")
        self.assertEqual(self.journal.read_bytes(), original)

    def test_raw_depth_and_numeric_caps_failclosed_before_writing(self):
        quote, book = fixture()
        book["bids"] = [{"price": "100", "quantity": "1"}] * 65
        capped = self.writer.capture_raw_book(quote, raw_book=book, request=REQUEST,
                                              identity=IDENTITY, observed_at=quote.observed_at)
        self.assertEqual(capped["status"], "CAPTURE_CAPACITY_HOLD")
        self.assertFalse(self.journal.exists())
        quote, book = fixture()
        book["bids"][0]["price"] = "1e100000"
        invalid = self.writer.capture_raw_book(quote, raw_book=book, request=REQUEST,
                                               identity=IDENTITY, observed_at=quote.observed_at)
        self.assertEqual(invalid["status"], "CAPTURE_INVALID_HOLD")
        self.assertFalse(self.journal.exists())

    def test_native_clock_naive_and_regressive_books_failclosed(self):
        quote, book = fixture()
        quote = Quote(**{**quote.__dict__, "book_at": "2026-11-03T11:00:00"})
        bad = self.writer.capture_raw_book(quote, raw_book=book, request=REQUEST,
                                           identity=IDENTITY, observed_at=quote.observed_at)
        self.assertEqual(bad["reason"], "PPI_BOOK_NATIVE_PIT_CLOCK_INVALID")
        self.assertFalse(self.journal.exists())
        self.capture(book_seconds=3)
        bad = self.capture(book_seconds=1, observed_seconds=4)
        self.assertEqual(bad["reason"], "PPI_BOOK_CLOCK_REGRESSION_OR_AMBIGUOUS_RECEIPT")
        self.assertEqual(pit.verified_native_book_journal(self.journal)[1]["records"], 1)

    def test_symlink_existing_journal_refused_without_touching_target(self):
        self.journal.parent.mkdir(parents=True)
        sentinel = Path(self.temporary.name) / "leave_intact.txt"
        sentinel.write_text("DO NOT TOUCH", encoding="utf-8")
        self.journal.symlink_to(sentinel)
        response = self.capture()
        self.assertEqual(response["status"], "CAPTURE_INTEGRITY_HOLD")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "DO NOT TOUCH")

    def test_overbroad_permissions_do_not_get_corrected_silently(self):
        self.capture()
        self.journal.chmod(0o644)
        response = self.capture(book_seconds=2)
        self.assertEqual(response["status"], "CAPTURE_INTEGRITY_HOLD")
        self.assertEqual(os.stat(self.journal).st_mode & 0o777, 0o644)

    def test_tamper_independent_hash_chain_must_be_verified_before_candidate_selection(self):
        self.capture()
        altered = self.journal.read_text().replace('"100.20"', '"102.20"', 1)
        self.assertNotEqual(altered, self.journal.read_text())
        self.journal.write_text(altered, encoding="utf-8")
        self.journal.chmod(0o600)
        chosen, receipt = pit.strictly_future_native_books(
            self.journal, identity=IDENTITY,
            decision_at=(START - timedelta(seconds=10)).isoformat(), latency_seconds=0)
        self.assertEqual(chosen, [])
        self.assertEqual(receipt["state"], "HOLD")


if __name__ == "__main__":
    unittest.main()
