"""OFFHOST adversarial guards: legacy FI positive writes must remain blocked.

This exercises the exact R4 buy-seed reader in an in-memory PAPER SQLite
fixture.  A future design must preserve immutable buy provenance after a
fully reviewed maturity or an independently evidenced market sale.
"""

import json
import unittest
from dataclasses import asdict

from be_paper_engine import PaperStore
from bs_instrument_contracts import contract_from_metadata
from rc6_fixed_income_event_ledger import _seed


OPENED = "2026-10-27T14:00:00+00:00"
CLOSED = "2026-11-03T16:00:00+00:00"


class LegacyNativeFIBuySeedHolds(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.temp = tempfile.TemporaryDirectory(prefix="a790-fi-red-")
        self.addCleanup(self.temp.cleanup)
        self.store = PaperStore(str(Path(self.temp.name) / "fi.db"))
        contract = contract_from_metadata("AL30", "BONOS", {
            "market": "BYMA", "currency": "ARS", "settlement": "A-24HS",
            "cash_multiplier": "0.01", "quantity_step": "1",
            "minimum_quantity": "1", "metadata_source": "SYNTHETIC_PPI_EXACT_CONTRACT",
        })
        features = {
            "financial_contract": asdict(contract),
            "contract_cash_multiplier": "0.01",
        }
        with self.store.connect() as db:
            db.execute("""INSERT INTO paper_positions
                (paper_id,source,strategy_version,symbol,asset_class,settlement,status,
                 quantity,entry_price,entry_cost,stop_price,target_price,opened_at,
                 features_json,currency,market,currency_source)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("PAPER-AL30", "PRODUCTION_PAPER", "SYNTHETIC_NOT_ENTITLEMENT",
                 "AL30", "BONOS", "A-24HS", "OPEN", "100", "100", "0.61",
                 "90", "110", OPENED, json.dumps(features, default=str),
                 "ARS", "BYMA", "SYNTHETIC"))
            db.execute("""INSERT INTO paper_fills
                (paper_id,source,side,filled_at,quantity,price,costs,slippage)
                VALUES(?,?,?,?,?,?,?,?)""",
                ("PAPER-AL30", "PRODUCTION_PAPER", "BUY_SIMULATED", OPENED,
                 "100", "100", "0.61", "0"))

    def test_unreviewed_maturity_close_does_not_bypass_buy_seed_guard(self):
        with self.store.connect() as db:
            before = _seed(db, "PAPER-AL30")
            db.execute("""UPDATE paper_positions SET status='CLOSED',
                closed_at=?,close_reason='FI_NATIVE_MATURITY_REVIEWED' WHERE paper_id=?""",
                (CLOSED, "PAPER-AL30"))
            # Native terminal integration is not implemented: do not present
            # this legacy transition as a valid closing receipt.
            with self.assertRaisesRegex(ValueError, "FI_LEDGER_INTACT_BUY_LOT_REQUIRED"):
                _seed(db, "PAPER-AL30")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM paper_fi_cash_entries").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0], 0)

    def test_unreviewed_sell_fill_does_not_bypass_buy_seed_guard(self):
        with self.store.connect() as db:
            before = _seed(db, "PAPER-AL30")
            db.execute("""INSERT INTO paper_fills
                (paper_id,source,side,filled_at,quantity,price,costs,slippage)
                VALUES(?,?,?,?,?,?,?,?)""",
                ("PAPER-AL30", "PRODUCTION_PAPER", "SELL_SIMULATED", CLOSED,
                 "100", "105", "0.64", "0"))
            # A direct old-style SELL fill is not an authorized native sale.
            with self.assertRaisesRegex(ValueError, "FI_LEDGER_INTACT_BUY_LOT_REQUIRED"):
                _seed(db, "PAPER-AL30")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM paper_fi_cash_entries").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0], 0)

    def test_legacy_market_sale_kind_is_not_an_issuer_economic_event(self):
        from rc6_fixed_income_event_reducer import EconomicEvent, reduce_events

        with self.store.connect() as db:
            seed = _seed(db, "PAPER-AL30")
        event = EconomicEvent(
            event_id="FI_PAPER_SALE:synthetic", obligation_id="FI_PAPER_SALE:synthetic",
            paper_id="PAPER-AL30", kind="MARKET_SALE", effective_at=CLOSED,
            recorded_at=CLOSED, source_kind="PPI_LIVE_BOOK",
            source_reference="SYNTHETIC_OFFHOST_NOT_REAL_PPI",
            source_sha256="f" * 64, source_at=CLOSED,
            nominal_base="100", nominal_redeemed="100",
        )
        with self.assertRaisesRegex(ValueError, "FIXED_INCOME_EVENT_KIND_UNSUPPORTED"):
            reduce_events(seed, [event], as_of=CLOSED)


if __name__ == "__main__":
    unittest.main()
