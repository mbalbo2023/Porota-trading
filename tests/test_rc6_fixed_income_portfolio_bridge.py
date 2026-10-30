"""A199 FI accounting projections on synthetic PAPER lots, not issuer evidence."""
from decimal import Decimal

import pytest

from be_paper_engine import PaperStore
from rc6_fixed_income_portfolio_bridge import (
    ENTRY_AUTHORITY, PAPER_ONLY, REAL_ROUTES_USED, cash_effect_checked,
    native_exit_authorized, position_at, positions_at, valuation_at,
)
from test_rc6_fixed_income_event_ledger import (
    BOOKED, KNOWN, apply, lot, receipt,
)


def test_coupon_pit_and_cross_currency_pnl_never_infers_fx(tmp_path):
    store, settled = lot(tmp_path)
    apply(store, receipt(settled))  # USD coupon on an ARS-traded bond
    with store.connect() as db:
        early = position_at(db, "PAPER-AL30", KNOWN)
        assert early.initial_nominal == early.residual_nominal == Decimal(100)
        assert early.cash_by_currency == ()
        view = position_at(db, "PAPER-AL30", BOOKED)
        assert view.cash_by_currency == (("USD", Decimal(2)),)
        assert view.coupon_by_currency == (("USD", Decimal(2)),)
        assert view.principal_by_currency == ()
        assert cash_effect_checked(db, "USD", BOOKED) == Decimal(2)
        assert cash_effect_checked(db, "USD", BOOKED, income_only=True) == Decimal(2)
        assert cash_effect_checked(db, "ARS", BOOKED) == 0
        valuation = valuation_at(db, "PAPER-AL30", BOOKED, "101")
        assert valuation.exposure == Decimal("101")
        assert valuation.unrealized_pnl == Decimal("0.39")
        assert valuation.realized_pnl is None
        assert valuation.valuation_status == "FI_CROSS_CURRENCY_PNL_UNVERIFIED"
        assert native_exit_authorized(db, "PAPER-AL30", BOOKED) == (
            False, "FI_NATIVE_ENTITLEMENT_AWARE_MARKET_SALE_MISSING")
        assert len(positions_at(db, BOOKED)) == 1
    assert PAPER_ONLY and not ENTRY_AUTHORITY and REAL_ROUTES_USED == ()


def test_partial_amortization_redemption_cash_and_residual_cost_basis(tmp_path):
    store, settled = lot(tmp_path)
    amort = receipt(settled, kind="AMORTIZATION", event_id="a1",
                    redeemed="30", cash="30", unit="1", currency="ARS")
    apply(store, amort)
    coupon = receipt(settled, event_id="c2", nominal="70", cash="1.4",
                     unit="0.02", currency="ARS",
                     effective="2026-11-02T15:00:00+00:00",
                     recorded="2026-11-02T15:06:00+00:00")
    coupon["entitlement"].update(ex_at="2026-11-02T12:00:00+00:00",
                                 record_at="2026-11-02T13:00:00+00:00")
    later = "2026-11-02T16:00:00+00:00"
    apply(store, coupon, at=later)
    with store.connect() as db:
        view = position_at(db, "PAPER-AL30", later)
        assert view.initial_nominal == 100
        assert view.residual_nominal == 70
        assert view.redeemed_nominal == 30
        assert view.cash_by_currency == (("ARS", Decimal("31.4")),)
        assert view.principal_by_currency == (("ARS", Decimal(30)),)
        assert view.coupon_by_currency == (("ARS", Decimal("1.4")),)
        assert cash_effect_checked(db, "ARS", later, income_only=True) == Decimal("1.4")
        valuation = valuation_at(db, "PAPER-AL30", later, "101")
        assert valuation.exposure == Decimal("70.7")
        assert valuation.cost_basis_amortized == Decimal("70.427")
        assert valuation.unrealized_pnl == Decimal("0.273")
        assert valuation.realized_pnl == Decimal("1.217")
        assert valuation.valuation_status == "FI_PAPER_COST_BASIS_RECONCILED"
        # The spot ledger still commits the *original* ARS buy, while the FI
        # bridge adds confirmed cash and marks only residual VN. This identity
        # would fail if amortization also silently reduced spot buy commitment.
        initial_cash = Decimal(1000)
        original_entry_cash = Decimal("100.61")
        account_equity = (initial_cash - original_entry_cash
                          + cash_effect_checked(db, "ARS", later) + valuation.exposure)
        assert account_equity - initial_cash == (
            valuation.realized_pnl + valuation.unrealized_pnl) == Decimal("1.49")


def test_full_redemption_keeps_native_close_blocked_without_fake_sell(tmp_path):
    store, settled = lot(tmp_path)
    apply(store, receipt(settled, kind="AMORTIZATION", event_id="full",
                         redeemed="100", cash="100", unit="1", currency="ARS"))
    with store.connect() as db:
        view = position_at(db, "PAPER-AL30", BOOKED)
        assert view.residual_nominal == 0
        assert valuation_at(db, "PAPER-AL30", BOOKED, "100").exposure == 0
        assert native_exit_authorized(db, "PAPER-AL30", BOOKED) == (
            False, "FI_NATIVE_REDEMPTION_CLOSE_LEDGER_MISSING")
        assert db.execute("SELECT status FROM paper_positions").fetchone()[0] == "OPEN"
        assert db.execute("SELECT count(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0] == 0


def test_corrupt_cash_sidecar_fails_closed_instead_of_creating_equity(tmp_path):
    store, settled = lot(tmp_path)
    apply(store, receipt(settled))
    with store.connect() as db:
        db.execute("UPDATE paper_fi_cash_entries SET amount='999'")
    with store.connect() as db:
        with pytest.raises(ValueError, match="FI_PORTFOLIO_EVENT_CASH_CONFLICT"):
            cash_effect_checked(db, "USD", BOOKED)


def test_mutated_immutable_buy_cannot_inflate_projected_nominal(tmp_path):
    store, settled = lot(tmp_path)
    apply(store, receipt(settled))
    with store.connect() as db:
        db.execute("UPDATE paper_positions SET entry_price='90'")
        db.execute("UPDATE paper_fills SET price='90'")
    with store.connect() as db:
        with pytest.raises(ValueError, match="FI_PORTFOLIO_IMMUTABLE_SEED_CHANGED"):
            position_at(db, "PAPER-AL30", BOOKED)


def test_no_ledger_is_not_synthetic_income_and_missing_schema_is_explicit(tmp_path):
    store, _ = lot(tmp_path)
    with store.connect() as db:
        assert position_at(db, "PAPER-AL30", BOOKED) is None
        assert positions_at(db, BOOKED) == ()
        assert cash_effect_checked(db, "ARS", BOOKED) == 0
    other = PaperStore(str(tmp_path / "no-fi-schema.db"))
    with other.connect() as db:
        # A200: the parent now migrates this ledger during boot. Simulate
        # an actually incomplete/corrupted schema to preserve the guard.
        db.execute("DROP TABLE paper_fi_cash_entries")
        with pytest.raises(ValueError, match="FI_PORTFOLIO_LEDGER_SCHEMA_MISSING"):
            positions_at(db, BOOKED)


def test_exact_replay_not_double_paid_in_portfolio(tmp_path):
    store, settled = lot(tmp_path)
    data = receipt(settled)
    apply(store, data)
    apply(store, data)
    with store.connect() as db:
        assert cash_effect_checked(db, "USD", BOOKED) == Decimal(2)
        assert position_at(db, "PAPER-AL30", BOOKED).event_count == 1


def test_booked_at_causality_excludes_future_import_even_if_event_known(tmp_path):
    store, settled = lot(tmp_path)
    apply(store, receipt(settled), at=BOOKED)
    with store.connect() as db:
        assert position_at(db, "PAPER-AL30", "2026-10-29T15:59:59+00:00").cash_by_currency == ()
        assert cash_effect_checked(db, "USD", "2026-10-29T15:59:59+00:00") == 0
