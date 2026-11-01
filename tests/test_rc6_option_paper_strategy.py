"""Fail-closed tests for the isolated long-only PAPER option candidate."""
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
import json
from zoneinfo import ZoneInfo

from be_paper_engine import Quote
from bs_instrument_contracts import InstrumentContract
from rc6_option_paper_strategy import (
    ENTRY_AUTHORITY, PAPER_ONLY, REAL_ROUTES_USED, UnderlyingEvidence,
    UnderlyingTrade, evaluate_option_entry,
)


ART = ZoneInfo("America/Argentina/Buenos_Aires")
AT = datetime(2026, 10, 29, 12, 0, tzinfo=ART)
EXPIRY = datetime(2026, 11, 20, 15, 30, tzinfo=ART).isoformat()


def _inputs(*, right="CALL", at=AT):
    contract = InstrumentContract(
        "GFGC1010NO", "OPCIONES", "ARS", "BYMA", "INMEDIATA", Decimal("100"),
        Decimal("1"), "PPI_SEARCH_INSTRUMENT_DESCRIPTION+BYMA_OPTION_CONTRACT_2026",
        expires_at=EXPIRY, underlying="GGAL", strike=Decimal("101"),
        option_right=right, minimum_quantity=Decimal("1"),
    )
    q = Quote(
        symbol=contract.symbol, asset_class="OPCIONES", settlement=contract.settlement,
        last=Decimal("1.9"), bid=Decimal("1.9"), ask=Decimal("2"),
        bid_size=Decimal("10"), ask_size=Decimal("10"),
        observed_at=(at - timedelta(seconds=5)).isoformat(),
        book_at=(at - timedelta(seconds=12)).isoformat(),
        contract=contract, currency="ARS", market="BYMA",
        metadata_source="PPI_CATALOG:GGAL_OPTION_SERIES",
    )
    change = Decimal("0.18") if right == "CALL" else Decimal("-0.18")
    prices = [Decimal("100") + change * index for index in range(8)]
    trades = tuple(UnderlyingTrade(
        price=price,
        traded_at=(at - timedelta(minutes=8-index)).isoformat(),
        received_at=(at - timedelta(minutes=8-index, seconds=-3)).isoformat(),
    ) for index, price in enumerate(prices))
    spot = prices[-1]
    u = UnderlyingEvidence(
        symbol="GGAL", family="ACCIONES", market="BYMA", currency="ARS",
        settlement="A-24HS", bid=spot - Decimal("0.05"),
        ask=spot + Decimal("0.05"), bid_size=Decimal("100"),
        ask_size=Decimal("100"), book_at=(at - timedelta(seconds=10)).isoformat(),
        received_at=(at - timedelta(seconds=6)).isoformat(),
        metadata_source="PPI_CATALOG:GGAL_BYMA", trades=trades,
    )
    kwargs = dict(decision_at=at, underlying_evidence=u, available_cash="10000",
                  risk_budget="1000", max_position_notional="500")
    return q, kwargs


def _decide(q, kwargs):
    return evaluate_option_entry(q, **kwargs)


def test_long_call_candidate_is_never_entry_authority_or_real_route():
    q, kwargs = _inputs()
    result = _decide(q, kwargs)
    assert result.action == "BUY", result.reason
    assert result.quantity == Decimal("1")
    assert result.reason == "OPTION_LONG_PAPER_CANDIDATE"
    assert result.features["mode"] == "PRODUCTION_PAPER"
    assert result.features["execution"] == "SIMULATION_CANDIDATE_ONLY"
    assert result.features["score_is_probability"] is False
    assert result.entry_authority is False and ENTRY_AUTHORITY is False
    assert result.paper_only and PAPER_ONLY
    assert result.real_routes_used == REAL_ROUTES_USED == ()
    assert result.features["real_routes_used"] == []
    assert result.features["iv"]["status"] == "NO_VERIFICADO"
    assert result.features["greeks"]["status"] == "NO_VERIFICADO"


def test_long_put_uses_bearish_underlying_rather_than_call_momentum():
    q, kwargs = _inputs(right="PUT")
    assert _decide(q, kwargs).action == "BUY"
    call = replace(q, contract=replace(q.contract, option_right="CALL"))
    assert _decide(call, kwargs).reason == "OPTION_UNDERLYING_DIRECTIONAL_SIGNAL_NOT_READY"


def test_complementary_iv_missing_is_warning_not_paper_deploy_blocker():
    q, kwargs = _inputs()
    no_iv = _decide(q, kwargs)
    kwargs["implied_volatility_evidence"] = {
        "value": "0.55", "source": "IOL_VERIFIED_PIT", "source_at": (AT-timedelta(seconds=20)).isoformat(),
        "received_at": (AT-timedelta(seconds=15)).isoformat(),
    }
    observed = _decide(q, kwargs)
    assert no_iv.action == observed.action == "BUY"
    assert "IV_NO_VERIFICADO" in no_iv.features["warnings"]
    assert observed.features["iv"]["status"] == "OBSERVED_FRESH"
    assert "IV_NO_VERIFICADO" not in observed.features["warnings"]
    kwargs["implied_volatility_evidence"]["source_at"] = (AT-timedelta(days=2)).isoformat()
    stale = _decide(q, kwargs)
    assert stale.action == "BUY" and stale.features["iv"]["status"] == "NO_VERIFICADO"


def test_missing_or_ambiguous_underlying_identity_is_hold():
    q, kwargs = _inputs()
    kwargs["underlying_evidence"] = None
    assert _decide(q, kwargs).reason == "OPTION_UNDERLYING_EVIDENCE_MISSING"
    _, kwargs = _inputs()
    kwargs["underlying_evidence"] = replace(kwargs["underlying_evidence"], symbol="PAMP")
    assert _decide(q, kwargs).reason == "OPTION_UNDERLYING_PPI_IDENTITY_MISMATCH"


def test_primary_source_and_full_option_contract_are_nonnegotiable():
    q, kwargs = _inputs()
    assert _decide(replace(q, metadata_source="IOL_MCP"), kwargs).reason == (
        "OPTION_PPI_PRIMARY_EXACT_IDENTITY_REQUIRED")
    no_contract = replace(q, contract=None)
    assert _decide(no_contract, kwargs).reason == "OPTION_EXACT_CONTRACT_REQUIRED"


def test_authenticated_bridge_contract_marker_is_valid_without_literal_ppi():
    q, kwargs = _inputs()
    normalized = replace(q, contract=replace(
        q.contract, metadata_source="CONTRACT_EVIDENCE_V2_BOUND"))
    assert _decide(normalized, kwargs).action == "BUY"
    assert _decide(replace(normalized, metadata_source="IOL_MCP"), kwargs).reason == (
        "OPTION_PPI_PRIMARY_EXACT_IDENTITY_REQUIRED")
    unknown = replace(q, contract=replace(q.contract, metadata_source="UNVERIFIED_ARBITRARY"))
    assert _decide(unknown, kwargs).reason == "OPTION_PPI_PRIMARY_EXACT_IDENTITY_REQUIRED"


def test_stale_books_and_noncausal_underlying_trades_hold():
    q, kwargs = _inputs()
    stale = replace(q, book_at=(AT-timedelta(minutes=4)).isoformat())
    assert _decide(stale, kwargs).reason == "OPTION_BOOK_STALE_OR_FUTURE"
    u = kwargs["underlying_evidence"]
    kwargs["underlying_evidence"] = replace(u, book_at=(AT-timedelta(minutes=4)).isoformat())
    assert _decide(q, kwargs).reason == "OPTION_UNDERLYING_BOOK_STALE_OR_FUTURE"
    kwargs["underlying_evidence"] = replace(
        u, trades=tuple(replace(item, received_at=(AT-timedelta(minutes=20)).isoformat())
                        if index == 4 else item for index, item in enumerate(u.trades)))
    assert _decide(q, kwargs).reason == "OPTION_UNDERLYING_TRADE_CLOCK_INVALID"


def test_insufficient_sequence_and_stale_signal_are_not_buy():
    q, kwargs = _inputs()
    u = kwargs["underlying_evidence"]
    kwargs["underlying_evidence"] = replace(u, trades=u.trades[-3:])
    assert _decide(q, kwargs).reason == "OPTION_UNDERLYING_SIGNAL_WINDOW_INCOMPLETE"
    kwargs["underlying_evidence"] = replace(u, trades=tuple(
        replace(item, traded_at=(AT-timedelta(minutes=30-index)).isoformat())
        for index, item in enumerate(u.trades)))
    assert _decide(q, kwargs).reason == "OPTION_UNDERLYING_TRADE_CLOCK_INVALID"


def test_spread_depth_and_full_premium_loss_guards():
    q, kwargs = _inputs()
    assert _decide(replace(q, bid=Decimal("0.5")), kwargs).reason == "OPTION_SPREAD_LIMIT"
    assert _decide(replace(q, ask_size=Decimal("1")), kwargs).reason == "OPTION_DEPTH_INSUFFICIENT"
    kwargs["risk_budget"] = "100"
    assert _decide(q, kwargs).reason == "OPTION_FULL_PREMIUM_LOSS_EXCEEDS_BUDGET"


def test_strategy_research_limits_cannot_be_disabled_by_configuration():
    q, kwargs = _inputs()
    assert evaluate_option_entry(q, **kwargs, max_spread_bps=2000).reason == (
        "OPTION_RESEARCH_LIMIT_CANNOT_BE_LOOSENED")
    assert evaluate_option_entry(q, **kwargs, max_moneyness_fraction="0.99").reason == (
        "OPTION_RESEARCH_LIMIT_CANNOT_BE_LOOSENED")
    assert evaluate_option_entry(q, **kwargs, depth_participation="1").reason == (
        "OPTION_RESEARCH_LIMIT_CANNOT_BE_LOOSENED")
    assert evaluate_option_entry(q, **kwargs, min_momentum_fraction="0.00001").reason == (
        "OPTION_RESEARCH_LIMIT_CANNOT_BE_LOOSENED")


def test_expiry_window_and_market_session_separate_from_deploy():
    q, kwargs = _inputs()
    expired = replace(q, contract=replace(q.contract, expires_at=(AT-timedelta(days=1)).isoformat()))
    assert _decide(expired, kwargs).reason == "OPTION_EXPIRY_OUTSIDE_SAFE_HORIZON"
    at = datetime(2026, 10, 29, 20, 0, tzinfo=ART)
    q_evening, evening = _inputs(at=at)
    assert _decide(q_evening, evening).reason.startswith("OPTION_OUTSIDE_PAPER_EXECUTION_WINDOW")


def test_iv_warnings_never_invent_greeks_or_theoretical_edge():
    q, kwargs = _inputs()
    result = _decide(q, kwargs)
    assert "delta" not in str(result.features).lower()
    assert "theoretical_price" not in result.features
    assert "EDGE_OOS_NO_VERIFICADO" in result.features["warnings"]


def test_long_cash_premium_plus_fee_exact_budget_and_one_micro_shortfall():
    q, kwargs = _inputs()
    accepted = _decide(q, kwargs)
    assert accepted.action == "BUY"
    premium = Decimal(accepted.features["premium_cash"])
    fee = Decimal(accepted.features["entry_fee"])
    max_loss = Decimal(accepted.features["maximum_simulated_loss"])
    assert max_loss == premium + fee and fee > 0
    kwargs["available_cash"] = str(max_loss)
    assert _decide(q, kwargs).action == "BUY"  # equality is funded
    kwargs["available_cash"] = str(max_loss - Decimal("0.000001"))
    assert _decide(q, kwargs).reason == "OPTION_FULL_PREMIUM_LOSS_EXCEEDS_BUDGET"
    kwargs["available_cash"] = "NaN"
    assert _decide(q, kwargs).reason == "OPTION_SIGNAL_INPUT_UNVERIFIED"


def test_aware_option_expiry_features_are_json_serializable_and_no_broker_authority():
    q, kwargs = _inputs()
    aware_expiry = datetime.fromisoformat(EXPIRY)
    q = replace(q, contract=replace(q.contract, expires_at=aware_expiry))
    decision = _decide(q, kwargs)
    assert decision.action == "BUY"
    assert json.loads(json.dumps(decision.features))["expires_at"] == aware_expiry.isoformat()
    assert decision.entry_authority is False and decision.real_routes_used == ()


def test_option_expiry_research_minimum_is_exact_to_microsecond():
    q, kwargs = _inputs()
    exact = AT + timedelta(days=1)
    q_exact = replace(q, contract=replace(q.contract, expires_at=exact))
    assert _decide(q_exact, kwargs).action == "BUY"
    too_soon = replace(q, contract=replace(
        q.contract, expires_at=exact - timedelta(microseconds=1)))
    assert _decide(too_soon, kwargs).reason == "OPTION_EXPIRY_OUTSIDE_SAFE_HORIZON"


def test_aware_underlying_pit_clocks_and_optional_iv_replay_serialize_without_future_leak():
    q, kwargs = _inputs()
    u = kwargs["underlying_evidence"]
    trades = tuple(replace(row,
                           traded_at=datetime.fromisoformat(row.traded_at),
                           received_at=datetime.fromisoformat(row.received_at))
                   for row in u.trades)
    kwargs["underlying_evidence"] = replace(
        u, book_at=datetime.fromisoformat(u.book_at), trades=trades)
    kwargs["implied_volatility_evidence"] = {
        "value": "0.55", "source": "SYNTHETIC_COMPLEMENTARY_IV",
        "source_at": AT - timedelta(seconds=20),
        "received_at": AT - timedelta(seconds=15),
    }
    decision = _decide(q, kwargs)
    assert decision.action == "BUY" and decision.entry_authority is False
    encoded = json.loads(json.dumps(decision.features))
    assert encoded["underlying_book_at"] == u.book_at
    assert encoded["trade_window"][1] == u.trades[-1].traded_at
    assert encoded["iv"]["source_at"] == (AT - timedelta(seconds=20)).isoformat()
