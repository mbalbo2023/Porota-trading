"""A572: only the official A3 *futures* price grid admits standard DLR PAPER.

All inputs are synthetic OFFHOST fixtures.  No PPI GET, host runtime, margin
claim or simulated/real order is created.  A3's DLR futures tick is ARS 0.5
per USD; the distinct DLR *option* tick ARS 0.01 must never be substituted.
"""
from datetime import datetime
from decimal import Decimal

import pytest

from bs_instrument_contracts import InstrumentContract
from rc6_ppi_future_contract_policy import (
    admission_error,
    paper_session_state,
    standard_dlr_terms,
)


AT = "2026-11-03T11:00:00-03:00"
SERIES = "DLR/DIC26"
TERMS = standard_dlr_terms(SERIES)


def contract(*, tick=None, source=None, known_at=None, effective_at=None):
    return InstrumentContract(
        SERIES, "FUTUROS", "ARS", "A3", "INMEDIATA",
        Decimal("1000"), Decimal("1"), "CONTRACT_EVIDENCE_V2_BOUND",
        expires_at=TERMS["expires_at"], minimum_quantity=Decimal("1"),
        paper_margin_policy="CONSERVATIVE_NOTIONAL_RATE",
        paper_margin_rate=Decimal("1"), underlying="DOLAR_A3500",
        price_tick=tick, price_tick_source=source,
        price_tick_known_at=known_at,
        price_tick_effective_at=effective_at,
    )


def test_legacy_dlr_diciembre_uses_narrow_a3_futures_tick_after_known_at():
    spec = contract()
    assert spec.execution_price_terms()["price_tick"] == Decimal("0.5")
    assert paper_session_state(AT, spec) == ""
    assert admission_error(AT, spec) == ""
    assert spec.price(Decimal("1500.5"), price_kind="QUOTE") == Decimal("1500.5")
    with pytest.raises(ValueError, match="EXECUTION_PRICE_OFF_GRID:QUOTE"):
        spec.price(Decimal("1500.01"), price_kind="QUOTE")


def test_explicit_exact_a3_dlr_tick_and_provenance_are_admitted_only_paper_window():
    spec = contract(
        tick=Decimal("0.5"), source=TERMS["price_tick_source"],
        known_at=TERMS["price_tick_known_at"],
        effective_at=TERMS["price_tick_effective_at"],
    )
    assert admission_error(AT, spec) == ""
    assert spec.executable_fill(Decimal("1500.26"), side="BUY") == Decimal("1500.5")
    assert spec.executable_fill(Decimal("1500.26"), side="SELL") == Decimal("1500.0")


@pytest.mark.parametrize("tick,source,known_at,reason", [
    ("0.01", "UNVERIFIED", None, "FUTURE_PRICE_TICK_CONTRACT_MISMATCH"),
    ("0.01", TERMS["price_tick_source"], TERMS["price_tick_known_at"],
     "FUTURE_PRICE_TICK_CONTRACT_MISMATCH"),
    ("0.5", "UNVERIFIED", TERMS["price_tick_known_at"],
     "FUTURE_PRICE_TICK_PROVENANCE_UNVERIFIED"),
    ("0.5", TERMS["price_tick_source"], None,
     "FUTURE_PRICE_TICK_PROVENANCE_UNVERIFIED"),
    ("0.5", TERMS["price_tick_source"], "2026-09-01T00:00:00+00:00",
     "FUTURE_PRICE_TICK_PROVENANCE_UNVERIFIED"),
])
def test_spoofed_grid_or_provenance_does_not_admit_dlr_future(
        tick, source, known_at, reason):
    spec = contract(tick=Decimal(tick), source=source, known_at=known_at)
    # Prove that the generic contract's explicit-value preference is real:
    # before A572 the same fixture passed session and admission unconditionally.
    assert spec.execution_price_terms()["price_tick"] == Decimal(tick)
    assert paper_session_state(AT, spec) == reason
    assert admission_error(AT, spec) == reason


def test_unproven_effectivity_claim_cannot_make_a3_future_grid_authoritative():
    spec = contract(tick=Decimal("0.5"), source=TERMS["price_tick_source"],
                    known_at=TERMS["price_tick_known_at"],
                    effective_at="2026-01-01T00:00:00+00:00")
    assert admission_error(AT, spec) == "FUTURE_PRICE_TICK_PROVENANCE_UNVERIFIED"


def test_static_a3_tick_cannot_be_borrowed_before_its_retrieval_in_historical_pit():
    spec = contract()
    earlier = "2026-10-02T11:00:00-03:00"
    assert datetime.fromisoformat(earlier) < datetime.fromisoformat(TERMS["price_tick_known_at"])
    assert paper_session_state(earlier, spec) == "FUTURES_EXECUTION_GRID_NOT_KNOWN_AT_PIT"
    assert admission_error(earlier, spec) == "FUTURES_EXECUTION_GRID_NOT_KNOWN_AT_PIT"
