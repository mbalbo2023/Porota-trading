"""A200 FI risk input contract. Every fixture is synthetic and PAPER-only."""
import json
from dataclasses import replace
from decimal import Decimal

import pytest

from be_paper_engine import Quote
from bs_instrument_contracts import InstrumentContract
from rc6_fixed_income_risk_projection import (
    ENTRY_AUTHORITY, PAPER_ONLY, REAL_ROUTES_USED, risk_inputs_at,
)
from test_rc6_fixed_income_event_ledger import BOOKED, apply, lot, receipt


def quote_for(store, when=BOOKED):
    with store.connect() as db:
        row = db.execute("SELECT * FROM paper_positions WHERE paper_id='PAPER-AL30'").fetchone()
    contract = InstrumentContract(**json.loads(row["features_json"])["financial_contract"])
    return Quote(symbol=row["symbol"], asset_class=row["asset_class"],
                 settlement=row["settlement"], last=Decimal(101), bid=Decimal(101),
                 ask=Decimal(102), bid_size=Decimal(100), ask_size=Decimal(100),
                 observed_at=when, book_at=when, currency=row["currency"],
                 market=row["market"], contract=contract)


def risk(store, when=BOOKED, *, quote=None):
    with store.connect() as db:
        db.execute("BEGIN")
        return risk_inputs_at(db, "PAPER-AL30", when, quote or quote_for(store, when))


def test_coupon_fx_is_separate_and_never_grants_native_authority(tmp_path):
    store, settled = lot(tmp_path)
    apply(store, receipt(settled))  # USD coupon for ARS-traded bond
    result = risk(store)
    assert result.identity == ("AL30", "BONOS", "BYMA", "ARS", "A-24HS")
    assert result.opening_nominal == result.residual_nominal == 100
    assert result.spot_buy_cash_commitment == Decimal("100.61")
    assert result.remaining_full_loss_risk_reserve == Decimal("100.61")
    assert result.residual_bid_exposure == Decimal(101)
    assert result.unrealized_before_unverified_exit_costs == Decimal("0.39")
    assert result.confirmed_event_cash_by_currency == (("USD", Decimal(2)),)
    assert result.known_realized_by_currency == (("USD", Decimal(2)),)
    assert result.total_realized_native is None
    assert "FI_CROSS_CURRENCY_FX_UNVERIFIED" in result.blockers
    assert "FI_NATIVE_ENTITLEMENT_AWARE_MARKET_SALE_MISSING" in result.blockers
    assert "FI_ACCOUNT_COSTS_AND_EXIT_FEES_UNVERIFIED" in result.blockers
    assert result.preserve_original_spot_cash_commitment
    assert not result.entry_authority and not result.native_exit_authorized
    assert PAPER_ONLY and not ENTRY_AUTHORITY and REAL_ROUTES_USED == ()
    with store.connect() as db:
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


def test_partial_amortization_allocates_basis_and_tracks_loss_per_event(tmp_path):
    store, settled = lot(tmp_path)
    apply(store, receipt(settled, kind="AMORTIZATION", event_id="a1",
                         redeemed="30", cash="30", unit="1", currency="ARS"))
    later = "2026-11-02T16:00:00+00:00"
    coupon = receipt(settled, event_id="c2", nominal="70", cash="1.4",
                     unit="0.02", currency="ARS",
                     effective="2026-11-02T15:00:00+00:00",
                     recorded="2026-11-02T15:06:00+00:00")
    coupon["entitlement"].update(ex_at="2026-11-02T12:00:00+00:00",
                                 record_at="2026-11-02T13:00:00+00:00")
    apply(store, coupon, at=later)
    result = risk(store, later)
    assert result.residual_nominal == 70
    assert result.redeemed_nominal == 30
    assert result.remaining_entry_cost_basis == Decimal("70.427")
    assert result.amortized_entry_cost_basis == Decimal("30.183")
    assert result.remaining_full_loss_risk_reserve == Decimal("70.427")
    assert result.residual_bid_exposure == Decimal("70.7")
    assert result.realized_components[0].realized_before_unverified_fees == Decimal("-0.183")
    assert result.realized_components[1].realized_before_unverified_fees == Decimal("1.4")
    assert result.known_realized_by_currency == (("ARS", Decimal("1.217")),)
    # A losing redemption must not be hidden by a profitable coupon when
    # reserving daily risk. Aggregate realized gain is a different statistic.
    assert result.realized_losses_by_currency == (("ARS", Decimal("0.183")),)
    assert result.total_realized_native == Decimal("1.217")
    assert result.confirmed_principal_cash_by_currency == (("ARS", Decimal(30)),)
    assert result.confirmed_coupon_cash_by_currency == (("ARS", Decimal("1.4")),)


def test_undervalue_redemption_explicitly_consumes_loss_budget(tmp_path):
    store, settled = lot(tmp_path)
    apply(store, receipt(settled, kind="AMORTIZATION", event_id="loss",
                         redeemed="30", cash="9", unit="0.3", currency="ARS"))
    result = risk(store)
    assert result.realized_losses_by_currency == (("ARS", Decimal("21.183")),)
    assert result.total_realized_native == Decimal("-21.183")
    assert result.remaining_full_loss_risk_reserve == Decimal("70.427")


def test_cross_currency_amortization_never_converts_principal_without_fx(tmp_path):
    store, settled = lot(tmp_path)
    apply(store, receipt(settled, kind="AMORTIZATION", event_id="cross",
                         redeemed="30", cash="30", unit="1", currency="USD"))
    result = risk(store)
    assert result.realized_components[0].realized_before_unverified_fees is None
    assert result.total_realized_native is None
    assert result.known_realized_by_currency == ()
    assert "FI_CROSS_CURRENCY_FX_UNVERIFIED" in result.blockers


def test_full_redemption_does_not_fabricate_market_sale(tmp_path):
    store, settled = lot(tmp_path)
    apply(store, receipt(settled, kind="AMORTIZATION", event_id="maturity",
                         redeemed="100", cash="100", unit="1", currency="ARS"))
    result = risk(store)
    assert result.residual_nominal == result.residual_bid_exposure == 0
    assert result.remaining_full_loss_risk_reserve == 0
    assert "FI_NATIVE_REDEMPTION_CLOSE_LEDGER_MISSING" in result.blockers
    assert result.native_exit_authorized is False
    with store.connect() as db:
        assert db.execute("SELECT status FROM paper_positions").fetchone()[0] == "OPEN"
        assert db.execute("SELECT count(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0] == 0


def test_absent_ledger_and_ambiguous_settlement_block_risk(tmp_path):
    store, settled = lot(tmp_path)
    with pytest.raises(ValueError, match="FI_RISK_EVENT_LEDGER_MISSING"):
        risk(store)
    apply(store, receipt(settled))
    with store.connect() as db:
        db.execute("UPDATE paper_positions SET settlement='UNKNOWN'")
    with pytest.raises(ValueError, match="FI_RISK_SETTLEMENT_AMBIGUOUS"):
        risk(store)


def test_transaction_requirement_guards_two_phase_snapshot_races(tmp_path):
    store, settled = lot(tmp_path)
    apply(store, receipt(settled))
    with store.connect() as db:
        with pytest.raises(ValueError, match="FI_RISK_SNAPSHOT_TRANSACTION_REQUIRED"):
            risk_inputs_at(db, "PAPER-AL30", BOOKED, quote_for(store))


@pytest.mark.parametrize("mutator,error", [
    (lambda q: replace(q, symbol="GD30"), "FI_RISK_MARK_IDENTITY_MISMATCH"),
    (lambda q: replace(q, book_at="2026-10-29T17:00:00+00:00"), "FI_RISK_MARK_CLOCK_OR_FRESHNESS_INVALID"),
    (lambda q: replace(q, observed_at="2026-10-29T15:00:00+00:00"), "FI_RISK_MARK_CLOCK_OR_FRESHNESS_INVALID"),
    (lambda q: replace(q, bid=Decimal(104), ask=Decimal(102)), "FI_RISK_MARK_CROSSED_BOOK"),
    (lambda q: replace(q, contract=None), "FI_RISK_MARK_CONTRACT_MISSING_OR_MISMATCH"),
])
def test_bad_market_mark_cannot_be_used_for_risk(tmp_path, mutator, error):
    store, settled = lot(tmp_path)
    apply(store, receipt(settled))
    with pytest.raises(ValueError, match=error):
        risk(store, quote=mutator(quote_for(store)))


def test_corrupted_event_cash_still_fails_reconciled_risk(tmp_path):
    store, settled = lot(tmp_path)
    apply(store, receipt(settled))
    with store.connect() as db:
        db.execute("UPDATE paper_fi_cash_entries SET amount='999'")
    with pytest.raises(ValueError, match="FI_PORTFOLIO_EVENT_CASH_CONFLICT"):
        risk(store)
