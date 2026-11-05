"""A653: diagnostic PPI Book1 hash must reach the atomic PAPER decision freeze.

OFFHOST fixtures only: no PPI requests, real orders, signer, or admission GO.
"""

from contextlib import nullcontext
from dataclasses import FrozenInstanceError
from datetime import datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bf_production_paper_observer as observer
import bu_instrument_catalog as catalog
from be_paper_engine import PaperStore
from rc6_ppi_scanner_provenance import (PIT_JOURNAL_FILENAME, ScannerPrimaryRecorder,
                                        verified_native_book_journal)


IDENTITY = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
REQUEST = (IDENTITY[0], IDENTITY[1], IDENTITY[4])
BOOK_AT = "2026-11-03T11:00:01-03:00"
KNOWN_AT = "2026-11-03T11:00:02-03:00"
LATER_AT = "2026-11-03T11:00:04-03:00"
DECISION_AT = "2026-11-03T11:00:05-03:00"
RELEASE = "a" * 64


class Reader:
    def __init__(self, book_at=BOOK_AT):
        self.book_at = book_at
        self.calls = []

    def read_scope(self, **_scope):
        return nullcontext()

    def current(self, *request):
        self.calls.append(("current", request))
        return {"price": "100", "date": self.book_at}

    def book(self, *request):
        self.calls.append(("book", request))
        return {"date": self.book_at,
                "bids": [{"position": 0, "price": "99.5", "quantity": "10"}],
                "offers": [{"position": 0, "price": "101", "quantity": "11"}]}


class Book1DecisionWiring(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="a653-book1-")
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.store = PaperStore(str(root / "paper.db"))
        observer._support_schema(self.store)
        self.recorder = ScannerPrimaryRecorder(root / "market" / "latest.json")
        self.journal = self.recorder.path.with_name(PIT_JOURNAL_FILENAME)
        self._catalog("ARS")

    def _catalog(self, currency):
        record = catalog.normalize_record(
            {"ticker": "GGAL", "type": "ACCIONES", "market": "BYMA",
             "currency": currency}, "A-24HS", KNOWN_AT, "a653-offhost")
        with self.store.connect() as connection:
            catalog.persist(connection, record)

    def _read(self, reader=None, at=KNOWN_AT):
        reader = reader or Reader()
        with patch.object(observer, "now_iso", return_value=at):
            quote = observer._read_scanner_quote(reader, self.store, *REQUEST,
                identity=IDENTITY, scanner_primary=self.recorder)
        return quote, reader

    def _record(self, quote, *, supplied_hash=None):
        features = {"performance_lineage": {"decision_at": DECISION_AT}}
        if supplied_hash is not None:
            features["ppi_book1_event_sha256"] = supplied_hash
        with patch.dict(os.environ, {"POROTA_RC6_RELEASE_SHA256": RELEASE}):
            result = self.store.record_decision(
                "a653:GGAL:BUY", quote, "BUY", Decimal("0.75"),
                "SYNTHETIC_ONLY", features)
        with self.store.connect() as connection:
            freeze = connection.execute("SELECT freeze_payload_json FROM "
                "paper_causal_decision_freezes WHERE decision_key=?",
                ("a653:GGAL:BUY",)).fetchone()
            decision = connection.execute("SELECT features_json FROM paper_decisions "
                "WHERE decision_key=?", ("a653:GGAL:BUY",)).fetchone()
            receipt_count = connection.execute("SELECT COUNT(*) FROM "
                "paper_causal_decision_receipts").fetchone()[0]
        return result, json.loads(freeze[0]) if freeze else None, \
               json.loads(decision[0]) if decision else None, receipt_count

    def test_real_recorder_hash_flows_into_same_transaction_freeze_without_extra_get(self):
        quote, reader = self._read()
        rows, state = verified_native_book_journal(self.journal)
        self.assertEqual(state["state"], "HASH_CHAIN_VERIFIED")
        self.assertEqual(quote.ppi_book1_event_sha256, rows[0]["event_sha256"])
        self.assertEqual(reader.calls, [("current", REQUEST), ("book", REQUEST)])
        with self.assertRaises(FrozenInstanceError):
            quote.ppi_book1_event_sha256 = "f" * 64
        committed, frozen, features, receipts = self._record(quote)
        self.assertTrue(committed)
        self.assertEqual(frozen["book1_event_sha256_claim"], rows[0]["event_sha256"])
        self.assertEqual(features["ppi_book1_event_sha256"], rows[0]["event_sha256"])
        self.assertEqual(frozen["identity"], list(IDENTITY))
        self.assertEqual(receipts, 0)

    def test_stale_book_keeps_observational_journal_but_cannot_bind_decision_hash(self):
        quote, _ = self._read(Reader("2026-11-03T10:00:00-03:00"))
        self.assertIsNone(quote.ppi_book1_event_sha256)
        self.assertTrue(self.journal.is_file())

    def test_replayed_native_book_is_not_rebound_to_new_receipt_clock(self):
        first, _ = self._read()
        second, _ = self._read(at=LATER_AT)
        self.assertIsNotNone(first.ppi_book1_event_sha256)
        self.assertIsNone(second.ppi_book1_event_sha256)
        rows, _ = verified_native_book_journal(self.journal)
        self.assertEqual(len(rows), 1)

    def test_ambiguous_currency_prevents_two_provider_gets_or_provenance(self):
        self._catalog("USD")
        reader = Reader()
        with self.assertRaisesRegex(ValueError, "PPI_SCANNER_PROVIDER_REQUEST_IDENTITY_AMBIGUOUS"):
            self._read(reader)
        self.assertEqual(reader.calls, [])
        self.assertFalse(self.journal.exists())

    def test_malformed_writer_hash_never_becomes_a_book1_claim(self):
        with patch.object(self.recorder, "capture_raw_book", return_value={
            "status": "CAPTURED", "event_sha256": "not-a-sha"}):
            quote, _ = self._read()
        self.assertIsNone(quote.ppi_book1_event_sha256)

    def test_conflicting_supplied_feature_drops_claim_not_paper_decision(self):
        quote, _ = self._read()
        committed, frozen, features, receipts = self._record(quote, supplied_hash="f" * 64)
        self.assertTrue(committed)
        self.assertIsNone(frozen["book1_event_sha256_claim"])
        self.assertNotIn("ppi_book1_event_sha256", features)
        self.assertEqual(features["ppi_book1_provenance_state"], "CLAIM_CONFLICT_HOLD")
        self.assertEqual(receipts, 0)

    def test_unbound_feature_cannot_invent_book1_claim(self):
        # A stale native Book is observed, but the scanner intentionally
        # refuses to bind it to the Quote. A caller-provided 64-hex digest
        # must not bypass the absent capture witness in the atomic freeze.
        quote, _ = self._read(Reader("2026-11-03T10:00:00-03:00"))
        self.assertIsNone(quote.ppi_book1_event_sha256)
        committed, frozen, features, receipts = self._record(
            quote, supplied_hash="f" * 64)
        self.assertTrue(committed)
        self.assertIsNone(frozen["book1_event_sha256_claim"])
        self.assertNotIn("ppi_book1_event_sha256", features)
        self.assertEqual(features["ppi_book1_provenance_state"], "CLAIM_UNBOUND_HOLD")
        self.assertEqual(receipts, 0)


if __name__ == "__main__":
    unittest.main()
