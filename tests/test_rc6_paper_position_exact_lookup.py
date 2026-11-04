"""Adversarial PAPER ledger identity tests; ephemeral SQLite, never a host DB."""

import tempfile
import unittest
from pathlib import Path

from be_paper_engine import PaperStore


class PaperPositionExactLookupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="rc6-paper-lookup-")
        self.store = PaperStore(str(Path(self.directory.name) / "paper.db"))

    def tearDown(self):
        self.directory.cleanup()

    def insert_open(self, paper_id, symbol, settlement, *, currency="ARS", market="BYMA"):
        values = {
            "paper_id": paper_id,
            "source": "PRODUCTION_PAPER",
            "strategy_version": "TEST_ONLY",
            "symbol": symbol,
            "asset_class": "CEDEARS",
            "settlement": settlement,
            "status": "OPEN",
            "quantity": "1",
            "entry_price": "100",
            "entry_cost": "0",
            "stop_price": "90",
            "target_price": "110",
            "opened_at": "2026-11-04T11:00:00-03:00",
            "features_json": "{}",
            "currency": currency,
            "market": market,
            "currency_source": "PPI_CATALOG",
        }
        columns = tuple(values)
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO paper_positions(" + ",".join(columns) + ") VALUES(" +
                ",".join("?" for _ in columns) + ")",
                tuple(values[column] for column in columns),
            )

    def test_single_legacy_lookup_and_ticker_veto_are_preserved(self):
        self.insert_open("GGAL_T0", "GGAL", "INMEDIATA")
        self.assertEqual(self.store.open_position("GGAL")["paper_id"], "GGAL_T0")
        self.assertTrue(self.store.has_open_symbol("GGAL"))
        self.assertFalse(self.store.has_open_symbol("OTHER"))
        exact = self.store.open_position(
            "GGAL", asset_class="CEDEARS", settlement="INMEDIATA",
            currency="ARS", market="BYMA",
        )
        self.assertEqual(exact["paper_id"], "GGAL_T0")

    def test_symbol_only_rejects_two_distinct_open_settlements_in_both_orders(self):
        for symbol, order in (
            ("AAPL", ("A-24HS", "INMEDIATA")),
            ("MSFT", ("INMEDIATA", "A-24HS")),
        ):
            for settlement in order:
                self.insert_open(f"{symbol}_{settlement}", symbol, settlement)
            with self.assertRaisesRegex(ValueError, "PAPER_POSITION_AMBIGUOUS_SYMBOL"):
                self.store.open_position(symbol)
            self.assertTrue(self.store.has_open_symbol(symbol))
            for settlement in ("INMEDIATA", "A-24HS"):
                exact = self.store.open_position(
                    symbol, asset_class="CEDEARS", settlement=settlement,
                    currency="ARS", market="BYMA",
                )
                self.assertEqual(exact["paper_id"], f"{symbol}_{settlement}")
            self.assertIsNone(self.store.open_position(
                symbol, asset_class="CEDEARS", settlement="INMEDIATA",
                currency="USD_MEP", market="BYMA",
            ))

    def test_partial_identity_and_duplicate_exact_key_fail_closed(self):
        self.insert_open("DUP1", "AAPL", "INMEDIATA")
        with self.assertRaisesRegex(ValueError, "PAPER_POSITION_FIVE_KEY_REQUIRED"):
            self.store.open_position("AAPL", asset_class="CEDEARS", settlement="INMEDIATA")
        self.insert_open("DUP2", "AAPL", "INMEDIATA")
        with self.assertRaisesRegex(ValueError, "PAPER_POSITION_DUPLICATE_EXACT_IDENTITY"):
            self.store.open_position(
                "AAPL", asset_class="CEDEARS", settlement="INMEDIATA",
                currency="ARS", market="BYMA",
            )


if __name__ == "__main__":
    unittest.main()
