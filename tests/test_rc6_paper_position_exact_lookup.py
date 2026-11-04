"""Adversarial PAPER ledger identity tests; ephemeral SQLite, never a host DB."""

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from be_paper_engine import PaperBroker, PaperStore, Quote


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

    @staticmethod
    def quote(symbol, settlement="INMEDIATA", *, currency="ARS", market="BYMA"):
        return Quote(
            symbol=symbol, asset_class="CEDEARS", settlement=settlement,
            currency=currency, market=market,
            last=Decimal("100"), bid=Decimal("100"), ask=Decimal("101"),
            bid_size=Decimal("10"), ask_size=Decimal("10"),
            observed_at="2026-11-04T11:00:00-03:00",
            book_at="2026-11-04T11:00:00-03:00",
        )

    def broker(self):
        return PaperBroker(
            self.store, ai_mode="OFF", economics_mode="SHADOW",
            daily_loss_pct=None, clock_fn=lambda: "2026-11-04T11:00:00-03:00",
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

    def test_exit_supervisor_receives_exact_open_position_not_sibling(self):
        self.insert_open("AAPL_T1", "AAPL", "A-24HS")
        self.insert_open("AAPL_T0", "AAPL", "INMEDIATA")
        broker = self.broker()
        with patch("bm_exit_supervisor.PositionExitSupervisor") as supervisor:
            supervisor.return_value.supervise.return_value.state = "WATCH"
            self.assertFalse(broker._maybe_close(self.quote("AAPL")))
            self.assertEqual(supervisor.return_value.supervise.call_count, 1)
            args, _ = supervisor.return_value.supervise.call_args
            self.assertEqual(args[0]["paper_id"], "AAPL_T0")

    def test_wrong_wallet_quote_cannot_supervise_or_bypass_ticker_veto(self):
        self.insert_open("AAPL_ARS", "AAPL", "INMEDIATA", currency="ARS")
        broker = self.broker()
        with patch("bm_exit_supervisor.PositionExitSupervisor") as supervisor:
            with patch.object(broker, "decide", side_effect=AssertionError("ENTRY_EVALUATED")):
                broker.on_quote(self.quote("AAPL", currency="USD"))
            supervisor.assert_not_called()
        self.assertTrue(self.store.has_open_symbol("AAPL"))
        with self.store.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM paper_positions WHERE symbol='AAPL' AND status='OPEN'"
            ).fetchone()[0], 1)

    def test_duplicate_exact_quote_cannot_supervise_or_bypass_ticker_veto(self):
        self.insert_open("AAPL_ONE", "AAPL", "INMEDIATA")
        self.insert_open("AAPL_TWO", "AAPL", "INMEDIATA")
        broker = self.broker()
        with patch("bm_exit_supervisor.PositionExitSupervisor") as supervisor:
            with patch.object(broker, "decide", side_effect=AssertionError("ENTRY_EVALUATED")):
                broker.on_quote(self.quote("AAPL"))
            supervisor.assert_not_called()
        self.assertTrue(self.store.has_open_symbol("AAPL"))

    def test_real_exit_supervisor_does_not_sell_wrong_sibling(self):
        self.insert_open("AAPL_T1", "AAPL", "A-24HS")
        self.insert_open("AAPL_T0", "AAPL", "INMEDIATA")
        broker = self.broker()
        self.assertFalse(broker._maybe_close(self.quote("AAPL")))
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'"
            ).fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
