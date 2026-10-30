"""A200 PAPER core accounting: synthetic fixtures are not real source receipts."""
from decimal import Decimal

import pytest

from be_paper_engine import PaperBroker, Quote
from dh_paper_dynamic_risk_gate_hf6 import open_stop_risk
from test_rc6_fixed_income_event_ledger import BOOKED, KNOWN, apply, lot, receipt


def broker_for(store):
    return PaperBroker(store, initial_cash="1000", initial_cash_usd="100",
                       daily_loss_pct=None)


def test_fx_coupon_credits_only_payout_currency_and_once(tmp_path):
    store, settled = lot(tmp_path)
    broker = broker_for(store)
    data = receipt(settled)  # coupon in USD; buy lot in ARS
    assert broker._cash(as_of=KNOWN, currency="ARS") == Decimal("899.39")
    assert broker._cash(as_of=KNOWN, currency="USD") == Decimal("100")
    apply(store, data)
    apply(store, data)
    assert broker._cash(as_of=BOOKED, currency="ARS") == Decimal("899.39")
    assert broker._cash(as_of=BOOKED, currency="USD") == Decimal("102")
    equity = broker.mark_equity({}, as_of=BOOKED)
    assert equity["ARS"]["equity"] == Decimal("999.39")
    assert equity["USD"]["equity"] == Decimal("102")
    with store.connect() as db:
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0] == 0


def test_amortization_reduces_exposure_not_original_buy_commitment(tmp_path):
    store, settled = lot(tmp_path)
    broker = broker_for(store)
    apply(store, receipt(settled, kind="AMORTIZATION", event_id="a1",
                         redeemed="30", cash="30", unit="1", currency="ARS"))
    assert broker._cash(as_of=BOOKED, currency="ARS") == Decimal("929.39")
    values = broker.mark_equity({}, as_of=BOOKED)["ARS"]
    assert values["exposure"] == Decimal("70")
    assert values["equity"] == Decimal("999.39")
    assert values["realized_pnl"] == Decimal("-0.183")
    assert values["unrealized_pnl"] == Decimal("-0.427")
    with store.connect() as db:
        assert open_stop_risk(broker, "ARS", BOOKED, connection=db) == Decimal("70.427")


def test_full_redemption_no_phantom_equity_or_stop_reserve(tmp_path):
    store, settled = lot(tmp_path)
    broker = broker_for(store)
    apply(store, receipt(settled, kind="AMORTIZATION", event_id="full",
                         redeemed="100", cash="100", unit="1", currency="ARS"))
    assert broker._cash(as_of=BOOKED, currency="ARS") == Decimal("999.39")
    assert broker.mark_equity({}, as_of=BOOKED)["ARS"]["exposure"] == 0
    with store.connect() as db:
        assert open_stop_risk(broker, "ARS", BOOKED, connection=db) == 0
        assert db.execute("SELECT status FROM paper_positions").fetchone()[0] == "OPEN"
        assert db.execute("SELECT count(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0] == 0


def test_corrupt_sidecar_fails_cash_reconciliation_without_credit(tmp_path):
    store, settled = lot(tmp_path)
    broker = broker_for(store)
    apply(store, receipt(settled))
    with store.connect() as db:
        db.execute("UPDATE paper_fi_cash_entries SET amount='999'")
    with pytest.raises(ValueError, match="FI_PORTFOLIO_EVENT_CASH_CONFLICT"):
        broker._cash(as_of=BOOKED, currency="USD")


def test_paper_cash_execution_cannot_rewind_across_fi_booking(tmp_path):
    store, settled = lot(tmp_path)
    broker = broker_for(store)
    apply(store, receipt(settled))
    with store.connect() as db:
        with pytest.raises(ValueError, match="CASH_CLOCK_ROLLBACK"):
            broker._cash(as_of=KNOWN, currency="USD", connection=db, for_execution=True)


def _quote(at=BOOKED):
    return Quote("AL30", "BONOS", "A-24HS", Decimal("100"),
                 Decimal("100"), Decimal("101"), Decimal("100"),
                 Decimal("100"), at, currency="ARS", market="BYMA",
                 book_at=at, trade_at=at, last_kind="TRADE")


def _seed_same_day_baseline(store):
    # Synthetic reconciled carry baseline, never a runtime recovery shortcut.
    with store.connect() as db:
        db.execute("""INSERT INTO paper_daily_risk
          (day,currency,baseline_equity,limit_pct,loss_budget,last_equity,
           daily_pnl,state,latched_at,evaluated_at,detail)
          VALUES ('2026-10-29','ARS','1000','2.5','25','1000',
                  '0','READY',NULL,'2026-10-29T12:00:00+00:00','SYNTHETIC_BASELINE')""")


def test_daily_risk_books_amortization_realized_without_double_principal(tmp_path):
    store, settled = lot(tmp_path)
    broker = PaperBroker(store, initial_cash="1000", daily_loss_pct="2.5")
    _seed_same_day_baseline(store)
    apply(store, receipt(settled, kind="AMORTIZATION", event_id="a1",
                         redeemed="30", cash="30", unit="1", currency="ARS"))
    results = broker.daily_risk.evaluate(BOOKED, quotes={"AL30": _quote()})
    assert results["ARS"]["state"] == "READY"
    assert Decimal(results["ARS"]["daily_pnl"]) < 0
    assert Decimal(results["ARS"]["daily_pnl"]) > Decimal("-3")


def test_daily_risk_cross_currency_coupon_keeps_fx_unverified(tmp_path):
    store, settled = lot(tmp_path)
    broker = PaperBroker(store, initial_cash="1000", initial_cash_usd="100",
                         daily_loss_pct="2.5")
    _seed_same_day_baseline(store)
    apply(store, receipt(settled))  # USD coupon, no approved ARS FX basis
    results = broker.daily_risk.evaluate(BOOKED, quotes={"AL30": _quote()})
    assert results["ARS"]["state"] == "STALE_MARKS"
    assert results["ARS"]["daily_pnl"] is None


def test_daily_risk_full_redemption_has_no_zero_quantity_exit_fee(tmp_path):
    store, settled = lot(tmp_path)
    broker = PaperBroker(store, initial_cash="1000", daily_loss_pct="2.5")
    _seed_same_day_baseline(store)
    apply(store, receipt(settled, kind="AMORTIZATION", event_id="full",
                         redeemed="100", cash="100", unit="1", currency="ARS"))
    results = broker.daily_risk.evaluate(BOOKED, quotes={"AL30": _quote()})
    assert results["ARS"]["state"] == "READY"
    assert Decimal(results["ARS"]["daily_pnl"]) == Decimal("-0.61")
