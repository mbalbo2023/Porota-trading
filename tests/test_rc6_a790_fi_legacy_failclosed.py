"""OFFHOST public-cash guards for *unverified* native FI sale/maturity.

No test pins current internal error messages or forbids future verified native
exit. The test fixture deliberately supplies no issuer review or signed book.
"""

import json
import unittest
from dataclasses import asdict
from decimal import Decimal

from be_paper_engine import PaperBroker, PaperStore
from bs_instrument_contracts import contract_from_metadata
from rc6_fixed_income_event_ledger import _canonical, _seed
from rc6_fixed_income_portfolio_bridge import native_exit_authorized


OPENED = "2026-10-27T14:00:00+00:00"
CLOSED = "2026-11-03T16:00:00+00:00"


class UnverifiedNativeFITransitionsRemainHeld(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.temp = tempfile.TemporaryDirectory(prefix="a790-fi-guard-")
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
            seed = _seed(db, "PAPER-AL30")
            db.execute("INSERT INTO paper_fi_lots(paper_id,seed_json,seed_sha256) VALUES(?,?,?)",
                       ("PAPER-AL30", _canonical(seed.__dict__), seed.position_sha256))
        self.broker = PaperBroker(self.store, initial_cash="1000")
        self.initial_available = self.broker._cash(as_of=CLOSED)
        self.assertGreater(self.initial_available, Decimal(0))
        self.assertLess(self.initial_available, self.broker.initial_balances["ARS"])

    def _assert_no_unverified_native_cash_release(self):
        with self.store.connect() as db:
            try:
                allowed, _ = native_exit_authorized(db, "PAPER-AL30", CLOSED)
            except ValueError:
                allowed = False  # malformed/unproven transition fails closed
            self.assertIs(allowed, False)
            self.assertEqual(db.execute(
                "SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0], 0)
            self.assertEqual(db.execute(
                "SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0], 0)
        try:
            after = self.broker._cash(as_of=CLOSED)
        except ValueError:
            return  # public account must not credit a malformed native exit
        self.assertLessEqual(after, self.initial_available,
                             "Una transición FI sin recibos no libera capital PAPER")

    def test_maturity_close_without_issuer_review_cannot_release_cash(self):
        with self.store.connect() as db:
            db.execute("""UPDATE paper_positions SET status='CLOSED',
                closed_at=?,close_reason='FI_NATIVE_MATURITY_REVIEWED' WHERE paper_id=?""",
                (CLOSED, "PAPER-AL30"))
        self._assert_no_unverified_native_cash_release()

    def test_sell_fill_without_causal_sale_receipt_cannot_release_cash(self):
        with self.store.connect() as db:
            db.execute("""INSERT INTO paper_fills
                (paper_id,source,side,filled_at,quantity,price,costs,slippage)
                VALUES(?,?,?,?,?,?,?,?)""",
                ("PAPER-AL30", "PRODUCTION_PAPER", "SELL_SIMULATED", CLOSED,
                 "100", "105", "0.64", "0"))
        self._assert_no_unverified_native_cash_release()

    def test_naked_market_sale_event_without_verifier_cannot_release_cash(self):
        from rc6_fixed_income_event_reducer import EconomicEvent

        event = EconomicEvent(
            event_id="FI_PAPER_SALE:synthetic", obligation_id="FI_PAPER_SALE:synthetic",
            paper_id="PAPER-AL30", kind="MARKET_SALE", effective_at=CLOSED,
            recorded_at=CLOSED, source_kind="PPI_LIVE_BOOK",
            source_reference="SYNTHETIC_OFFHOST_NOT_REAL_PPI",
            source_sha256="f" * 64, source_at=CLOSED,
            nominal_base="100", nominal_redeemed="100",
        )
        with self.store.connect() as db:
            # Negative fixture: no independent provider bytes, no native sale
            # receipt, no assigned fee/settlement source, no Book1/Book2 pair.
            db.execute("""INSERT INTO paper_fi_economic_events
                (paper_id,event_id,obligation_id,kind,event_json,receipt_sha256,
                 source_sha256,booked_at,entitlement_record_at)
                 VALUES(?,?,?,?,?,?,?,?,NULL)""",
                 ("PAPER-AL30", event.event_id, event.obligation_id, event.kind,
                  _canonical(asdict(event)), "f" * 64, "f" * 64, CLOSED))
        self._assert_no_unverified_native_cash_release()


if __name__ == "__main__":
    unittest.main()
