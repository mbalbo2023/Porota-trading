"""A605 OFFHOST: execution mode is not PPI provenance; receipts can bind it.

Synthetic fixtures only. No PPI SDK, broker GET, host access, order, or fill.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from be_paper_engine import PaperStore, Quote, SOURCE
from rc6_ppi_scanner_provenance import FILENAME, ScannerPrimaryRecorder
import scripts.rc6_full_universe_evidence as ledger


TIME = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone(timedelta(hours=-3)))
IDENTITY = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")


def sample(*, currency="ARS", book_at=None, last="100"):
    return Quote("GGAL", "ACCIONES", "A-24HS", Decimal(last), Decimal("99"),
                 Decimal("101"), Decimal("10"), Decimal("12"), TIME.isoformat(),
                 currency=currency, market="BYMA", book_at=book_at or TIME.isoformat(),
                 trade_at=TIME.isoformat(), last_kind="TRADE",
                 metadata_source="PPI_CATALOG:identity_only")


class TestA605ModeProvenanceGap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "paper.sqlite"
        self.store = PaperStore(self.db)
        self.current = TIME + timedelta(seconds=5)
        self.patches = [patch.object(ledger, "ROOT", self.root),
                        patch.object(ledger, "DB", str(self.db)),
                        patch.object(ledger, "now", lambda: self.current)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def publish(self, quote):
        recorder = ScannerPrimaryRecorder(self.root / FILENAME)
        recorder.capture(quote, request=(quote.symbol, quote.asset_class, quote.settlement))
        self.assertEqual(recorder.publish(current=self.current)["status"], "PUBLISHED")

    def test_mode_only_has_native_clocks_but_no_provenance_or_ppi_presence(self):
        q = sample()
        self.store.add_quote(q)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT source FROM market_snapshots").fetchone()[0],
                             SOURCE)
        self.assertEqual(ledger.primary_rows(self.current), {})
        receipt = ledger.ppi_mode_provenance_diagnostics(self.current)
        self.assertEqual(receipt["mode_only_sampled"], 1)
        self.assertEqual(receipt["mode_only_with_native_clocks"], 1)
        self.assertEqual(receipt["scanner_bound_exact_mode_rows"], 0)
        self.assertEqual(receipt["unbound_mode_rows"], 1)
        self.assertEqual(receipt["source_state"], "NO_VERIFICADO")
        self.assertEqual(receipt["reason"], "PAPER_MODE_NOT_BROKER_PROVENANCE")
        self.assertFalse(receipt["promotes_paper_readiness"])

    def test_genuine_scanner_receipt_exact_fields_bind_but_do_not_prove_fill(self):
        q = sample()
        self.store.add_quote(q)
        self.publish(q)
        provenance = ledger.ppi_mode_provenance_diagnostics(self.current)
        self.assertEqual(provenance["mode_only_sampled"], 1)
        self.assertEqual(provenance["scanner_bound_exact_mode_rows"], 1)
        self.assertEqual(provenance["unbound_mode_rows"], 0)
        self.assertEqual(provenance["source_state"], "PPI_SCANNER_RECEIPT_BOUND")
        self.assertFalse(provenance["causal_fill_verified"])
        self.assertFalse(provenance["promotes_paper_readiness"])
        indexed = ledger.primary_rows(self.current)
        self.assertEqual(indexed[IDENTITY]["provider_origin"],
                         "PPI_READONLY_SDK_CURRENT_AND_BOOK")

    def test_contract_catalog_marker_cannot_forge_broker_receipt(self):
        q = sample()
        self.store.add_quote(q)
        self.assertEqual(ledger.ppi_mode_provenance_diagnostics(self.current)[
            "scanner_bound_exact_mode_rows"], 0)

    def test_same_identity_but_new_book_or_price_does_not_falsely_bind(self):
        q = sample()
        self.store.add_quote(q)
        self.publish(sample(book_at=(TIME - timedelta(seconds=1)).isoformat()))
        result = ledger.ppi_mode_provenance_diagnostics(self.current)
        self.assertEqual(result["scanner_bound_exact_mode_rows"], 0)
        self.assertEqual(result["unbound_mode_rows"], 1)
        self.assertEqual(result["source_state"], "NO_VERIFICADO")

    def test_counterfactual_distinguishes_mode_contract_and_native_book(self):
        q = sample()
        self.store.add_quote(q)
        with patch.object(ledger, "catalog_rows", return_value=[
            {"symbol": "GGAL", "family": "ACCIONES", "market": "BYMA",
             "currency": "ARS", "settlement": "A-24HS",
             "capability": "READY_PAPER_SPOT"}]), \
             patch.object(ledger, "iol_rows", return_value={}), \
             patch.object(ledger, "byma_rows", return_value=({}, {})):
            missing = ledger.build()
            self.assertEqual(missing["counts"]["catalog_contract_candidates"], 1)
            self.assertEqual(missing["counts"]["ppi_fresh"], 0)
            self.assertEqual(missing["cycle"]["ppi_mode_provenance_gap"]["unbound_mode_rows"], 1)
            self.assertEqual(missing["counts"]["paper_contract_ready"], 0)
            self.publish(q)
            authenticated = ledger.build()
            self.assertEqual(authenticated["cycle"]["ppi_mode_provenance_gap"]["scanner_bound_exact_mode_rows"], 1)
            self.assertEqual(authenticated["counts"]["ppi_fresh"], 1)
            self.assertFalse(authenticated["instruments"][0]["signal_authorized"])
            self.assertFalse(authenticated["instruments"][0]["real_money_authorized"])
            self.assertFalse(authenticated["instruments"][0]["identity_valuation_boundary"]["causal_pit_verified"])


if __name__ == "__main__":
    unittest.main()
