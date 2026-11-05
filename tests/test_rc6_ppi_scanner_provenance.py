"""R1 OFFHOST regressions: scanner provenance without extra PPI GET or PAPER GO."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import inspect
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from be_paper_engine import Quote
from rc6_ppi_scanner_provenance import (
    FILENAME, MAX_BYTES, MAX_RECORDS, ORIGIN, ScannerPrimaryRecorder,
    native_trade_book_ready, primary_records,
)
from scripts import rc6_full_universe_evidence as evidence


class ReceiptFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "market"
        self.path = self.root / FILENAME
        self.clock = datetime(2026, 11, 5, 18, 0, tzinfo=timezone.utc)
        self.recorder = ScannerPrimaryRecorder(self.path)

    def quote(self, *, symbol="GGAL", market="BYMA", currency="ARS",
              settlement="A-24HS", last="5000", trade_age=2, book_age=1,
              received_age=0, last_kind="TRADE"):
        def when(age):
            return (self.clock - timedelta(seconds=age)).isoformat() if age is not None else None
        return Quote(symbol, "ACCIONES", settlement, Decimal(last), Decimal("4990"),
                     Decimal("5010"), Decimal("10"), Decimal("15"), when(received_age),
                     market=market, currency=currency, trade_at=when(trade_age),
                     book_at=when(book_age), last_kind=last_kind)

    def emit(self, quote=None):
        quote = quote or self.quote()
        self.recorder.capture(quote, request=(quote.symbol, quote.asset_class, quote.settlement))
        self.recorder.publish(current=self.clock)
        return primary_records(self.root, self.clock)


class ScannerReceiptTests(ReceiptFixture, unittest.TestCase):

    def test_valid_receipt_private_atomic_and_native(self):
        rows, status = self.emit()
        self.assertEqual(status["eligible_records"], 1)
        self.assertEqual(rows[0]["source"], "PPI")
        self.assertEqual(rows[0]["provider_origin"], ORIGIN)
        self.assertEqual(rows[0]["canonical_identity"], ["GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS"])
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertLessEqual(self.path.stat().st_size, MAX_BYTES)
        self.assertEqual(list(self.root.glob(".ppi-scanner-*")), [])
        self.assertEqual(self.recorder.capture_count, 0)

    def test_same_uid_separate_host_process_reads_private_receipt(self):
        self.emit()
        code = ("import sys; from datetime import datetime, timezone; from pathlib import Path; "
                "from rc6_ppi_scanner_provenance import primary_records; "
                "rows, status = primary_records(Path(sys.argv[1]), "
                "datetime.fromisoformat(sys.argv[2])); "
                "assert len(rows) == 1 and status['eligible_records'] == 1")
        result = subprocess.run([sys.executable, "-c", code, str(self.root),
                                 self.clock.isoformat()], capture_output=True,
                                text=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_absent_receipt_is_unavailable(self):
        rows, status = primary_records(self.root, self.clock)
        self.assertEqual(rows, [])
        self.assertEqual(status["reason"], "SCANNER_PRIMARY_RECEIPT_NOT_PUBLISHED")

    def test_only_existing_scanner_request_identity_is_accepted(self):
        self.recorder.capture(self.quote(), request=("GGAL", "CEDEARS", "A-24HS"))
        rows, status = self.emit(self.quote(currency=None))
        self.assertEqual(rows, [])
        self.assertEqual(status["eligible_records"], 0)
        self.assertEqual(self.recorder.invalid_identity, 0)  # Reset after publication.

    def test_missing_or_conflicting_currency_market_is_rejected(self):
        for quote in (self.quote(market=None), self.quote(currency=None)):
            self.recorder.capture(quote, request=(quote.symbol, quote.asset_class, quote.settlement))
        self.recorder.publish(current=self.clock)
        self.assertEqual(primary_records(self.root, self.clock)[0], [])

    def test_exact_five_keys_do_not_mix_same_ticker(self):
        a, b = self.quote(currency="ARS"), self.quote(currency="USD")
        for q in (a, b):
            self.recorder.capture(q, request=(q.symbol, q.asset_class, q.settlement))
        rows, status = self.emit(self.quote(settlement="INMEDIATA"))
        self.assertEqual(status["eligible_records"], 3)
        self.assertEqual({tuple(r["canonical_identity"]) for r in rows}, {
            ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS"),
            ("GGAL", "ACCIONES", "BYMA", "USD", "A-24HS"),
            ("GGAL", "ACCIONES", "BYMA", "ARS", "INMEDIATA"),
        })

    def test_native_clock_and_last_kind_fail_closed(self):
        for overrides in (
            {"trade_age": None}, {"book_age": None}, {"trade_age": 301},
            {"book_age": 301}, {"trade_age": -1}, {"book_age": -1},
            {"trade_age": -30, "received_age": -10},
            {"last_kind": "CLOSE"}, {"last": "0"}, {"last": "NaN"},
        ):
            with self.subTest(overrides=overrides):
                self.recorder = ScannerPrimaryRecorder(self.path)
                rows, status = self.emit(self.quote(**overrides))
                if overrides.get("received_age", 0) < 0:
                    self.assertEqual(rows, [])  # Future receipts are not published.
                else:
                    self.assertEqual(rows[0]["state"], "UNAVAILABLE")
                self.assertEqual(status["eligible_records"], 0)

    def test_naive_timestamp_and_future_receipt_rejected(self):
        naive = replace(self.quote(), book_at="2026-11-05T18:00:00")
        self.assertFalse(native_trade_book_ready({"book_at": naive.book_at}, self.clock))
        rows, status = self.emit(naive)
        self.assertEqual(rows[0]["state"], "UNAVAILABLE")
        self.recorder = ScannerPrimaryRecorder(self.path)
        rows, _ = self.emit(self.quote(received_age=-5))
        self.assertEqual(rows, [])  # Future receive time not published.

    def test_48h_diagnostic_retention_does_not_extend_300s_freshness(self):
        stale = self.quote(trade_age=3600, book_age=3600, received_age=3000)
        rows, _ = self.emit(stale)
        self.assertEqual(rows[0]["state"], "UNAVAILABLE")
        self.assertFalse(native_trade_book_ready(rows[0], self.clock))

    def test_conflict_same_receipt_clock_stays_blocked(self):
        first = self.quote(last="5000")
        second = self.quote(last="5001")
        for q in (first, second, first):
            self.recorder.capture(q, request=(q.symbol, q.asset_class, q.settlement))
        self.recorder.publish(current=self.clock)
        rows, status = primary_records(self.root, self.clock)
        self.assertEqual(status["eligible_records"], 0)
        self.assertEqual(rows[0]["review_status"], "CONFLICT_REVIEW_REQUIRED")
        indexed = evidence._indexed_records(rows)
        self.assertEqual(indexed["GGAL"]["review_status"], "CONFLICT_REVIEW_REQUIRED")

    def test_duplicate_identity_payload_rejected_entirely(self):
        self.emit()
        data = json.loads(self.path.read_text())
        data["records"].append(dict(data["records"][0]))
        data["record_count"] = 2
        self.path.write_text(json.dumps(data))
        rows, status = primary_records(self.root, self.clock)
        self.assertEqual(rows, [])
        self.assertEqual(status["reason"], "SCANNER_PRIMARY_RECEIPT_DUPLICATE_IDENTITY")

    def test_forged_origin_and_source_clock_cannot_become_fresh(self):
        self.emit()
        document = json.loads(self.path.read_text())
        document["records"][0]["source_at"] = self.clock.isoformat()
        self.path.write_text(json.dumps(document))
        rows, status = primary_records(self.root, self.clock)
        self.assertEqual(rows[0]["state"], "UNAVAILABLE")
        self.assertEqual(status["eligible_records"], 0)
        document["records"][0]["source_at"] = document["records"][0]["trade_at"]
        document["provider_origin"] = "PRODUCTION_PAPER"
        self.path.write_text(json.dumps(document))
        self.assertEqual(primary_records(self.root, self.clock)[0], [])

    def test_private_file_and_directory_symlink_guards(self):
        self.emit()
        os.chmod(self.path, 0o644)
        self.assertEqual(primary_records(self.root, self.clock)[0], [])
        os.chmod(self.path, 0o600)
        target = self.root / "private-target.json"
        os.replace(self.path, target)
        self.path.symlink_to(target)
        self.assertEqual(primary_records(self.root, self.clock)[0], [])
        with self.assertRaises(ValueError):
            self.recorder.publish(current=self.clock)
        linked_parent = Path(self.temp.name) / "linked_market"
        linked_parent.symlink_to(self.root, target_is_directory=True)
        rows, status = primary_records(linked_parent, self.clock)
        self.assertEqual(rows, [])
        self.assertEqual(status["reason"], "SCANNER_PRIMARY_RECEIPT_INVALID_OR_UNREADABLE")

    def test_hardlink_and_oversize_are_unreadable(self):
        self.emit()
        hardlink = self.root / "other-private.json"
        os.link(self.path, hardlink)
        self.assertEqual(primary_records(self.root, self.clock)[0], [])
        hardlink.unlink()
        with self.path.open("wb") as stream:
            stream.truncate(MAX_BYTES + 1)
        self.assertEqual(primary_records(self.root, self.clock)[0], [])

    def test_max_record_count_bounded(self):
        for i in range(MAX_RECORDS + 3):
            q = self.quote(symbol=f"TEST{i:04d}")
            self.recorder.capture(q, request=(q.symbol, q.asset_class, q.settlement))
        result = self.recorder.publish(current=self.clock)
        self.assertEqual(result["records"], MAX_RECORDS)
        self.assertEqual(result["overflow_records"], 3)
        self.assertEqual(len(primary_records(self.root, self.clock)[0]), MAX_RECORDS)


class ExportAndObserverTests(ReceiptFixture, unittest.TestCase):
    def test_overlay_only_exact_key_and_no_paper_execution(self):
        self.emit()
        with mock.patch.object(evidence, "ROOT", self.root), mock.patch.object(
            evidence, "DB", str(self.root / "missing.db")):
            rows = evidence.primary_rows(self.clock)
            key = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
            self.assertEqual(rows[key]["source"], "PPI")
            self.assertTrue(evidence.source_view(rows[key], "PPI", self.clock,
                                                 expected_identity=key)["selection_eligible"])
            wrong = ("GGAL", "ACCIONES", "BYMA", "USD", "A-24HS")
            self.assertFalse(evidence.source_view(rows[key], "PPI", self.clock,
                                                  expected_identity=wrong)["selection_eligible"])
            with mock.patch.object(evidence, "now", return_value=self.clock), mock.patch.object(
                evidence, "catalog_rows", return_value=[{
                    "symbol": "GGAL", "family": "ACCIONES", "market": "BYMA",
                    "currency": "ARS", "settlement": "A-24HS", "capability": "HOLD",
                }]), mock.patch.object(evidence, "iol_rows", return_value={}), mock.patch.object(
                evidence, "byma_rows", return_value=({}, {})):
                output = evidence.build()
            self.assertEqual(output["universe"]["ledger_count"], 1)
            self.assertEqual(output["families"][0]["ppi_scanner_observed"], 1)
            self.assertEqual(output["families"][0]["ppi_scanner_eligible"], 1)
            self.assertFalse(output["instruments"][0]["entry_authority"])
            self.assertFalse(output["instruments"][0]["paper_auto_enabled"])
            self.assertFalse(output["instruments"][0]["signal_authorized"])
            self.assertFalse(output["real_money_authorized"])

    def test_mode_or_catalog_cannot_be_promoted_to_ppi(self):
        row = {"symbol": "GGAL", "asset_class": "ACCIONES", "settlement": "A-24HS",
               "market": "BYMA", "currency": "ARS", "source": "PRODUCTION_PAPER",
               "trade_at": self.clock.isoformat(), "book_at": self.clock.isoformat(),
               "received_at": self.clock.isoformat(), "last": "5000", "last_kind": "TRADE",
               "source_at": self.clock.isoformat()}
        result = evidence.source_view(row, "PPI", self.clock)
        self.assertFalse(result["selection_eligible"])
        self.assertEqual(result["source_authority"], "NO_VERIFICADO")

    def test_open_clock_without_native_book_does_not_select(self):
        self.emit(self.quote(book_age=None))
        with mock.patch.object(evidence, "ROOT", self.root), mock.patch.object(
            evidence, "DB", str(self.root / "missing.db")):
            key = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
            result = evidence.source_view(evidence.primary_rows(self.clock)[key],
                                          "PPI", self.clock, expected_identity=key)
        self.assertTrue(result["present"])
        self.assertFalse(result["selection_eligible"])

    def test_existing_current_book_exactly_once_and_no_recorder_network(self):
        # Observer's original read helper remains unchanged. Import is deferred
        # so pure receipt/export tests can still run when optional deps differ.
        from bf_production_paper_observer import _read_scanner_quote
        provider_at = datetime.now(timezone.utc).isoformat()
        reader = mock.Mock(spec=["current", "book"])
        reader.current.return_value = {"price": 5000, "date": provider_at}
        reader.book.return_value = {"date": provider_at,
                                    "bids": [{"price": 4990, "quantity": 10}],
                                    "offers": [{"price": 5010, "quantity": 15}]}
        with mock.patch("bf_production_paper_observer.financial_catalog.lookup",
                        return_value=None), mock.patch(
                            "bf_production_paper_observer.financial_catalog.quote_terms",
                            return_value={"market": "BYMA", "currency": "ARS"}):
            quote = _read_scanner_quote(reader, object(), "GGAL", "ACCIONES", "A-24HS")
        self.assertEqual(reader.current.call_count, 1)
        self.assertEqual(reader.book.call_count, 1)
        self.recorder.capture(quote, request=("GGAL", "ACCIONES", "A-24HS"))
        self.recorder.publish(current=datetime.now(timezone.utc))
        self.assertEqual(primary_records(self.root, datetime.now(timezone.utc))[1]["eligible_records"], 1)
        self.assertEqual(reader.current.call_count, 1)
        self.assertEqual(reader.book.call_count, 1)
        self.assertNotIn("reader.current", inspect.getsource(ScannerPrimaryRecorder))
        self.assertNotIn("reader.book", inspect.getsource(ScannerPrimaryRecorder))


if __name__ == "__main__":
    unittest.main()
