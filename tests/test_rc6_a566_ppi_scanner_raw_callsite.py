"""A566: wire the existing authenticated PPI Book to private, causal PIT.

Synthetic SDK dictionaries are only guards/fixtures. No HTTP, runtime,
instrument promotion, orders, source impersonation or deployment.
"""
import ast
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import inspect
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import bf_production_paper_observer as observer
import bu_instrument_catalog as catalog
from be_paper_engine import PaperStore
from rc6_ppi_scanner_provenance import (FILENAME, PIT_JOURNAL_FILENAME,
                                        ScannerPrimaryRecorder,
                                        strictly_future_native_books,
                                        verified_native_book_journal)


REQUEST = ("GGAL", "ACCIONES", "A-24HS")
IDENTITY = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
BOOK_1_AT = "2026-11-03T11:00:01-03:00"
KNOWN_1_AT = "2026-11-03T11:00:02-03:00"
BOOK_2_AT = "2026-11-03T11:00:03-03:00"
KNOWN_2_AT = "2026-11-03T11:00:04-03:00"
LATE_BOOK_AT = "2026-11-03T17:29:59-03:00"
LATE_KNOWN_AT = "2026-11-03T17:30:00-03:00"


def _book(at, ask="101.0"):
    return {"date": at,
            "bids": [{"position": 0, "price": "99.5", "quantity": "10"},
                     {"position": 1, "price": "99.0", "quantity": "70"}],
            "offers": [{"position": 0, "price": ask, "quantity": "12"},
                       {"position": 1, "price": "101.5", "quantity": "80"}]}


class Reader:
    def __init__(self, books):
        self.books = list(books)
        self.calls = []
        self.scopes = []

    def read_scope(self, **scope):
        self.scopes.append(scope)
        return nullcontext()

    def current(self, *args):
        self.calls.append(("current", args))
        return {"price": "100.0", "date": self.books[0]["date"]}

    def book(self, *args):
        self.calls.append(("book", args))
        return self.books.pop(0)


def _store(path):
    result = PaperStore(str(path / "paper.db"))
    observer._support_schema(result)
    return result


def _catalog(store, *, currency="ARS", status="AVAILABLE"):
    raw = {"ticker": "GGAL", "type": "ACCIONES", "market": "BYMA",
           "currency": currency}
    record = catalog.normalize_record(raw, "A-24HS", KNOWN_1_AT, "a566-ppi-offhost")
    record["status"] = status
    with store.connect() as connection:
        catalog.persist(connection, record)


class NativeBookObserverHookTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rc6-a566-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = _store(self.root)
        _catalog(self.store)
        self.recorder = ScannerPrimaryRecorder(self.root / "market" / FILENAME)
        self.journal = self.recorder.path.with_name(PIT_JOURNAL_FILENAME)

    def _read(self, reader, known_at=KNOWN_1_AT, *, identity=IDENTITY):
        with patch.object(observer, "now_iso", return_value=known_at):
            return observer._read_scanner_quote(
                reader, self.store, *REQUEST, identity=identity,
                scanner_primary=self.recorder)

    def _hold_events(self):
        with self.store.connect() as connection:
            return connection.execute("""SELECT event_type,detail FROM paper_events
              WHERE event_type LIKE 'PPI_SCANNER_NATIVE_BOOK_JOURNAL_%'
              ORDER BY id""").fetchall()

    def test_same_book_get_preserves_full_depth_in_private_hash_journal(self):
        reader = Reader([_book(BOOK_1_AT)])
        quote = self._read(reader)
        self.assertEqual(reader.calls, [("current", REQUEST), ("book", REQUEST)])
        self.assertEqual(reader.scopes[0]["identity"], IDENTITY)
        self.assertEqual((quote.book_at, quote.trade_at, quote.observed_at),
                         (BOOK_1_AT, BOOK_1_AT, KNOWN_1_AT))
        self.assertEqual(stat.S_IMODE(os.stat(self.journal).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(str(self.journal) + ".lock").st_mode), 0o600)
        rows, receipt = verified_native_book_journal(self.journal)
        self.assertEqual((receipt["state"], len(rows)), ("HASH_CHAIN_VERIFIED", 1))
        self.assertEqual(rows[0]["canonical_identity"], list(IDENTITY))
        self.assertEqual(rows[0]["book"]["bids"][1]["quantity"], "70")
        self.assertEqual(rows[0]["book"]["offers"][1]["quantity"], "80")
        self.assertEqual(rows[0]["known_at"], KNOWN_1_AT)
        self.assertEqual(rows[0]["quantity_unit"], "NO_VERIFICADO")
        self.assertEqual(rows[0]["price_unit"], "NO_VERIFICADO")
        self.assertFalse(self._hold_events())

    def test_two_native_books_support_only_strict_future_book_not_fill(self):
        reader = Reader([_book(BOOK_1_AT), _book(BOOK_2_AT, ask="101.2")])
        self._read(reader, KNOWN_1_AT)
        self._read(reader, KNOWN_2_AT)
        self.assertEqual(reader.calls, [("current", REQUEST), ("book", REQUEST)] * 2)
        rows, verified = verified_native_book_journal(self.journal)
        self.assertEqual((len(rows), verified["state"]), (2, "HASH_CHAIN_VERIFIED"))
        future, receipt = strictly_future_native_books(
            self.journal, identity=IDENTITY, decision_at="2026-11-03T11:00:00-03:00",
            latency_seconds=1)
        self.assertEqual([row["native_book_at"] for row in future], [BOOK_2_AT])
        self.assertEqual(receipt["fill_accredited"], False)

    def test_ambiguous_three_key_blocks_even_journal_and_both_provider_gets(self):
        _catalog(self.store, currency="USD", status="STALE")
        reader = Reader([_book(BOOK_1_AT)])
        with self.assertRaisesRegex(ValueError, "PPI_SCANNER_PROVIDER_REQUEST_IDENTITY_AMBIGUOUS"):
            self._read(reader)
        self.assertEqual((reader.calls, reader.scopes), ([], []))
        self.assertFalse(self.journal.exists())

    def test_missing_native_side_remains_hold_without_synthetic_depth(self):
        book = _book(BOOK_1_AT)
        del book["offers"]
        reader = Reader([book])
        quote = self._read(reader)
        self.assertEqual(quote.book_at, BOOK_1_AT)
        self.assertEqual(reader.calls, [("current", REQUEST), ("book", REQUEST)])
        self.assertFalse(self.journal.exists())
        events = self._hold_events()
        self.assertEqual(len(events), 1)
        self.assertIn("PPI_BOOK_NATIVE_SHAPE_UNVERIFIED", events[0]["detail"])
        self.assertNotIn("READY", events[0]["event_type"])

    def test_provider_native_clock_without_timezone_never_becomes_pit(self):
        reader = Reader([_book("2026-11-03T11:00:01")])
        self._read(reader)
        self.assertFalse(self.journal.exists())
        self.assertIn("PPI_BOOK_NATIVE_PIT_CLOCK_INVALID", self._hold_events()[0]["detail"])

    def test_normalized_best_does_not_match_raw_book_so_no_journal(self):
        reader = Reader([_book(BOOK_1_AT)])
        real_normalizer = observer.normalize_quote

        def adversarial_normalizer(*args, **kwargs):
            return replace(real_normalizer(*args, **kwargs), bid=Decimal("98.5"))

        with patch.object(observer, "normalize_quote", side_effect=adversarial_normalizer):
            self._read(reader)
        self.assertFalse(self.journal.exists())
        self.assertIn("PPI_BOOK_NORMALIZED_BEST_LEVEL_MISMATCH", self._hold_events()[0]["detail"])

    def test_journal_writer_exception_never_raises_or_grants_ready(self):
        reader = Reader([_book(BOOK_1_AT)])
        with patch.object(self.recorder, "capture_raw_book", side_effect=RuntimeError("no journal")):
            quote = self._read(reader)
        self.assertEqual(quote.book_at, BOOK_1_AT)
        self.assertEqual(reader.calls, [("current", REQUEST), ("book", REQUEST)])
        self.assertFalse(self.journal.exists())
        events = self._hold_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "PPI_SCANNER_NATIVE_BOOK_JOURNAL_ERROR")
        self.assertEqual(events[0]["detail"], "RuntimeError")

    def test_both_existing_scanner_callsites_pass_private_recorder(self):
        module = ast.parse(inspect.getsource(observer))
        calls = [node for node in ast.walk(module)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id == "_read_scanner_quote"]
        self.assertEqual(len(calls), 2)
        # The main scanner always holds a recorder; T1 retains the old
        # optional signature when callers/test doubles provide no recorder.
        keywords = [{key.arg for key in node.keywords} for node in calls]
        self.assertIn("scanner_primary", set.union(*keywords))
        self.assertIn(None, set.union(*keywords))

    def test_t1_source_only_path_uses_same_native_book_without_paper_order(self):
        reader = Reader([_book(LATE_BOOK_AT)])
        observed = datetime.fromisoformat(LATE_KNOWN_AT)
        with patch.object(observer, "_equity_t1_readonly_window", return_value=True), \
             patch.object(observer, "_option_t0_late_readonly_identities", return_value=[]), \
             patch.object(observer, "_equity_t1_readonly_identities", return_value=[IDENTITY]), \
             patch.object(observer, "now_iso", return_value=LATE_KNOWN_AT):
            result = observer._equity_t1_readonly_tick(
                reader, self.store, now=observed, clock_fn=lambda: observed,
                scanner_primary=self.recorder)
        self.assertEqual(result["state"], "READONLY_T1")
        self.assertEqual(result["read"], 1)
        self.assertEqual(result["real_routes"], [])
        self.assertEqual(reader.calls, [("current", REQUEST), ("book", REQUEST)])
        rows, receipt = verified_native_book_journal(self.journal)
        self.assertEqual((len(rows), receipt["state"]), (1, "HASH_CHAIN_VERIFIED"))
        self.assertEqual(rows[0]["native_book_at"], LATE_BOOK_AT)


if __name__ == "__main__":
    unittest.main()
