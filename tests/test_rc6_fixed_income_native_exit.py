"""Synthetic FI maturity regression: settled cash is never paid or sold twice.

No fixture is issuer/PPI market proof; this suite tests the economic and
transactional contract that future source-approval workflows must satisfy.
"""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import sqlite3
import unittest

from be_paper_engine import PaperBroker, PaperStore
from rc6_fixed_income_native_exit import (
    ENTRY_AUTHORITY, MARKET_SALE_AUTHORITY, PAPER_ONLY, REAL_ROUTES_USED,
    REASON, apply_reviewed_maturity, terminal_at,
)
from rc6_fixed_income_portfolio_bridge import cash_effect_checked
from tests import test_rc6_fixed_income_native_redemption as fi_fixture

NEXT = "2026-10-30T16:00:00+00:00"


class NativeFiMaturityTest(unittest.TestCase):
    def make_lot(self, family="BONOS"):
        fx = fi_fixture.NativeRedemptionReviewTest(
            "test_bond_full_redemption_review_is_idempotent_and_never_pays_or_closes")
        fx.setUp()
        self.addCleanup(fx.doCleanups)
        if family != "BONOS":
            fx._set_family(family)
        fx._book()
        review = fx._apply()
        paper = PaperBroker(fx.store, initial_cash="1000", daily_loss_pct=None)
        return fx, review, paper

    @staticmethod
    def mature(fx, review, *, request_id="terminal-1", at=NEXT):
        return apply_reviewed_maturity(
            fx.store, paper_id="PAPER-AL30", request_id=request_id,
            approved_review_receipt_sha256=review["receipt_sha256"], applied_at=at)

    def test_all_three_families_atomic_maturity_no_fake_sell_or_cash(self):
        for family in ("BONOS", "LETRAS", "OBLIGACIONES"):
            with self.subTest(family=family):
                fx, review, paper = self.make_lot(family)
                initial = paper._cash(as_of=NEXT, currency="ARS")
                self.assertEqual(initial, Decimal("999.39"))
                first = self.mature(fx, review)
                self.assertEqual(first["realized_pnl"], "-0.61")
                self.assertEqual(first["sell_fills_created"], 0)
                self.assertEqual(first["economic_cash_written"], "0")
                self.assertEqual(first["residual_nominal"], "0")
                self.assertTrue(PAPER_ONLY)
                self.assertFalse(ENTRY_AUTHORITY or MARKET_SALE_AUTHORITY or REAL_ROUTES_USED)
                with fx.store.connect() as db:
                    self.assertEqual(db.execute("SELECT status FROM paper_positions").fetchone()[0],
                                     "CLOSED")
                    self.assertEqual(db.execute("SELECT count(*) FROM paper_fills").fetchone()[0], 1)
                    self.assertEqual(db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0], 1)
                    self.assertEqual(db.execute("SELECT count(*) FROM paper_fi_native_maturities").fetchone()[0], 1)
                    self.assertEqual(cash_effect_checked(db, "ARS", NEXT), Decimal(100))
                    self.assertEqual(db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0], 0)
                self.assertEqual(paper._cash(as_of=NEXT, currency="ARS"), initial)
                opened, closed = paper._positions_at(NEXT, self._connection(fx))
                self.assertFalse(opened)
                self.assertEqual(len(closed), 1)
                self.assertEqual(closed[0]["close_reason"], REASON)
                self.assertEqual(fx.store.recent_closed(limit=5)[0]["paper_id"], "PAPER-AL30")
                self.assertEqual(fx.store.exit_positions(), ([], []))
                risk = PaperBroker(fx.store, initial_cash="1000", daily_loss_pct="5")
                self.assertNotEqual(risk.daily_risk.evaluate(NEXT)["ARS"]["state"], "INVALID_LEDGER")
                self.assertEqual(self.mature(fx, review, at="2026-10-31T16:00:00+00:00"), first)
                self.assertEqual(paper._cash(as_of=NEXT, currency="ARS"), initial)

    def _connection(self, fx):
        db = fx.store.connect()
        self.addCleanup(db.close)
        db.execute("BEGIN")
        return db

    def test_historical_cutoff_returns_open_before_native_maturity(self):
        fx, review, paper = self.make_lot()
        before = paper._cash(as_of=fi_fixture.BOOKED, currency="ARS")
        self.mature(fx, review)
        with fx.store.connect() as db:
            db.execute("BEGIN")
            self.assertIsNone(terminal_at(db, "PAPER-AL30", fi_fixture.BOOKED))
            previous, realized = paper._positions_at(fi_fixture.BOOKED, db)
            self.assertEqual(len(previous), 1)
            self.assertFalse(realized)
            _, settled = paper._positions_at(NEXT, db)
            self.assertEqual(len(settled), 1)
        self.assertEqual(paper._cash(as_of=fi_fixture.BOOKED, currency="ARS"), before)
        self.assertEqual(paper._cash(as_of=NEXT, currency="ARS"), before)
        with self.assertRaisesRegex(ValueError, "CASH_CLOCK_ROLLBACK"):
            paper._cash(as_of=fi_fixture.BOOKED, currency="ARS", for_execution=True)

    def test_required_review_hash_clock_mode_and_replay_conflict(self):
        fx, review, paper = self.make_lot()
        before = paper._cash(as_of=NEXT, currency="ARS")
        with self.assertRaisesRegex(ValueError, "FI_TERMINAL_APPROVED_REVIEW_REQUIRED"):
            apply_reviewed_maturity(fx.store, paper_id="PAPER-AL30", request_id="wrong",
                approved_review_receipt_sha256="a" * 64, applied_at=NEXT)
        with self.assertRaisesRegex(ValueError, "FI_TERMINAL_REVIEW_NOT_YET_KNOWN"):
            self.mature(fx, review, at="2026-10-28T16:00:00+00:00")
        with fx.store.connect() as db:
            db.execute("UPDATE observer_state SET real_orders_sent=1 WHERE id=1")
        with self.assertRaisesRegex(ValueError, "FI_TERMINAL_PAPER_SAFETY_REQUIRED"):
            self.mature(fx, review)
        with fx.store.connect() as db:
            db.execute("UPDATE observer_state SET real_orders_sent=0 WHERE id=1")
        self.assertEqual(paper._cash(as_of=NEXT, currency="ARS"), before)
        self.mature(fx, review)
        with self.assertRaisesRegex(ValueError, "FI_TERMINAL_REPLAY_CONFLICT"):
            self.mature(fx, review, request_id="other")
        with self.assertRaisesRegex(ValueError, "FI_TERMINAL_CLOCK_ROLLBACK"):
            self.mature(fx, review, at=fi_fixture.BOOKED)

    def test_db_error_rolls_back_maturity_position_and_receipt(self):
        fx, review, paper = self.make_lot()
        before = paper._cash(as_of=NEXT, currency="ARS")
        with fx.store.connect() as db:
            db.execute("""CREATE TRIGGER block_terminal_event
              BEFORE INSERT ON paper_events
              WHEN NEW.event_type='FI_NATIVE_MATURITY_NO_SELL'
              BEGIN SELECT RAISE(ABORT,'synthetic event failure'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "synthetic event failure"):
            self.mature(fx, review)
        with fx.store.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM paper_positions").fetchone()[0], "OPEN")
            self.assertEqual(db.execute("SELECT count(*) FROM paper_fi_native_maturities").fetchone()[0], 0)
            db.execute("DROP TRIGGER block_terminal_event")
        self.assertEqual(paper._cash(as_of=NEXT, currency="ARS"), before)
        self.mature(fx, review)

    def test_reviewed_ledger_cannot_drift_or_append_after_close(self):
        fx, review, _ = self.make_lot()
        with fx.store.connect() as db:
            db.execute("UPDATE paper_fi_cash_entries SET amount='999'")
        with self.assertRaisesRegex(ValueError, "FI_PORTFOLIO_EVENT_CASH_CONFLICT"):
            self.mature(fx, review)
        with fx.store.connect() as db:
            db.execute("UPDATE paper_fi_cash_entries SET amount='100'")
        self.mature(fx, review)
        with fx.store.connect() as db:
            db.execute("UPDATE paper_fi_economic_events SET receipt_sha256=?", ("a" * 64,))
            with self.assertRaisesRegex(ValueError, "FI_TERMINAL_EVENT_LEDGER_CHANGED"):
                terminal_at(db, "PAPER-AL30")

    def test_parallel_exact_retry_and_clean_restart_do_not_create_sale_receipt(self):
        fx, review, paper = self.make_lot()
        initial = paper._cash(as_of=NEXT, currency="ARS")
        with ThreadPoolExecutor(max_workers=4) as pool:
            completed = list(pool.map(lambda _: self.mature(fx, review), range(4)))
        self.assertEqual(completed, [completed[0]] * 4)
        store = PaperStore(fx.store.path)
        with store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM paper_fi_native_maturities").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM paper_sale_receivables").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM paper_spot_sales").fetchone()[0], 0)
            self.assertEqual(len(store.recent_closed()), 1)
        self.assertEqual(PaperBroker(store, initial_cash="1000", daily_loss_pct=None)
                         ._cash(as_of=NEXT, currency="ARS"), initial)

    def test_corrupt_terminal_pnl_or_review_receipt_never_passes_reader(self):
        fx, review, paper = self.make_lot()
        self.mature(fx, review)
        with fx.store.connect() as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "FI_TERMINAL_RECEIPT_IMMUTABLE"):
                db.execute("UPDATE paper_fi_native_maturities SET closed_at=?", (NEXT,))
            with self.assertRaisesRegex(sqlite3.IntegrityError, "FI_TERMINAL_RECEIPT_IMMUTABLE"):
                db.execute("DELETE FROM paper_fi_native_maturities")
            db.execute("UPDATE paper_positions SET gross_pnl='123'")
            with self.assertRaisesRegex(ValueError, "FI_TERMINAL_PNL_OR_SETTLEMENT_CONFLICT"):
                terminal_at(db, "PAPER-AL30")
            db.execute("UPDATE paper_positions SET gross_pnl='0.00'")
            db.execute("UPDATE paper_fi_native_redemption_reviews SET review_json='{}'")
            with self.assertRaisesRegex(ValueError, "FI_TERMINAL_REVIEW_CONTENT_CHANGED"):
                terminal_at(db, "PAPER-AL30")
        with self.assertRaisesRegex(ValueError, "FI_TERMINAL_REVIEW_CONTENT_CHANGED"):
            paper._cash(as_of=NEXT, currency="ARS")

    def test_native_terminal_prevents_later_economic_receipt_even_with_same_paper_lot(self):
        fx, review, _ = self.make_lot()
        self.mature(fx, review)
        later = fx._economic_receipt(event_id="later-coupon")
        later["event"].update(kind="COUPON", cash_amount="1", nominal_redeemed=None)
        later["cash_per_nominal"] = "0.01"
        with self.assertRaisesRegex(ValueError, "FI_LEDGER_TERMINAL_EVENT_APPEND_FORBIDDEN"):
            fx._book(economic=later, at="2026-10-31T16:00:00+00:00")
        with fx.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM paper_fi_economic_events").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0], 1)

    def test_cross_currency_principal_does_not_gain_fake_ars_pnl(self):
        fx = fi_fixture.NativeRedemptionReviewTest(
            "test_bond_full_redemption_review_is_idempotent_and_never_pays_or_closes")
        fx.setUp()
        self.addCleanup(fx.doCleanups)
        paid = fx._economic_receipt()
        paid["event"]["payment_currency"] = "USD"
        fx._book(economic=paid)
        schedule = fx._review()
        schedule["issuer_schedule"]["principal_currency"] = "USD"
        schedule["issuer_schedule"]["obligations"][0]["payment_currency"] = "USD"
        schedule["expected_principal_by_currency"] = [["USD", "100"]]
        schedule["expected_cash_by_currency"] = [["USD", "100"]]
        review = fx._apply(schedule)
        paper = PaperBroker(fx.store, initial_cash="1000", daily_loss_pct=None)
        ars_before = paper._cash(as_of=NEXT, currency="ARS")
        usd_before = paper._cash(as_of=NEXT, currency="USD")
        self.assertEqual(ars_before, Decimal("899.39"))
        self.assertEqual(usd_before, Decimal("100"))
        result = self.mature(fx, review)
        self.assertIsNone(result["realized_pnl"])
        self.assertEqual(result["pnl_quality"], "FI_CROSS_CURRENCY_PNL_UNVERIFIED")
        self.assertEqual(paper._cash(as_of=NEXT, currency="ARS"), ars_before)
        self.assertEqual(paper._cash(as_of=NEXT, currency="USD"), usd_before)
        equity = paper.mark_equity({}, as_of=NEXT)
        self.assertEqual(equity["ARS"]["cash"], ars_before)
        self.assertEqual(equity["USD"]["cash"], usd_before)
        risk = PaperBroker(fx.store, initial_cash="1000", daily_loss_pct="5")
        risk_result = risk.daily_risk.evaluate(NEXT)["ARS"]
        self.assertIn(risk_result["state"], {"STALE_MARKS", "BASELINE_UNAVAILABLE"})
        self.assertIsNone(risk_result["daily_pnl"])
        with fx.store.connect() as db:
            self.assertIsNone(db.execute("SELECT net_pnl FROM paper_positions").fetchone()[0])
            self.assertEqual(db.execute(
                "SELECT stale_positions FROM paper_valuation_quality WHERE currency='ARS'"
            ).fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
