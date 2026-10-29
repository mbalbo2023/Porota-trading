"""Pure synthetic tests: no provider calls, PAPER fills or live OOS claims."""
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from be_paper_engine import Quote
from bs_instrument_contracts import InstrumentContract
from rc6_fixed_income_strategy_shadow import ShadowScenario
from rc6_fixed_income_paper_strategy import (
    PAPER_ONLY, REAL_ROUTES_USED, STRATEGY_VERSION,
    decide_fixed_income, decide_fixed_income_from_inputs,
)


AT = datetime(2026, 10, 29, 14, tzinfo=timezone.utc)
LATER = AT + timedelta(hours=2)
YESTERDAY = AT - timedelta(days=1)
SHA = "b" * 64


def fixture(family="BONOS", *, symbol="AL30"):
    contract = InstrumentContract(
        symbol, family, "ARS", "BYMA", "A-24HS", Decimal("0.01"),
        Decimal("1"), "SYNTHETIC_FIXTURE", minimum_quantity=Decimal("100"),
    )
    quote = Quote(symbol, family, "A-24HS", Decimal("100"), Decimal("99.9"),
                  Decimal("100.1"), Decimal("1000"), Decimal("1000"),
                  AT.isoformat(), contract=contract, currency="ARS",
                  market="BYMA", book_at=AT.isoformat(), trade_at=AT.isoformat())
    key = contract.key
    scenario = ShadowScenario(
        key, AT.isoformat(), LATER.isoformat(), "MARKET_EXIT", Decimal("100"),
        Decimal("1000"), Decimal("1001"), (("ARS", Decimal("19")),),
        Decimal("20"), Decimal("20"), SHA, (), "SHADOW_HYPOTHESIS",
        "STATIC_BID_OR_FIXED_REDEMPTION_SCENARIO", "POSITIVE_SCENARIO",
        observed_book_source="PPI_PRIMARY")
    terms = {
        "identity": key, "currency": "ARS", "source_authority": "OFFICIAL_ISSUER_VERIFIED",
        "source_reference": "fixture:independent-issuer-terms", "source_sha256": SHA,
        "source_at": YESTERDAY.isoformat(), "known_at": YESTERDAY.isoformat(),
        "cashflows_verified": True, "price_basis_verified": True,
        "minimum_scenario_net_bps": "100",
        "native_market_exit_supported": True, "duration_years": "1.2",
        "native_exit_contract":
            "FIXED_INCOME_MARKET_EXIT_WITH_ECONOMIC_EVENT_LEDGER_V1",
        "native_exit_proof_verified": True,
        "coupon_calendar_verified": True, "amortization_calendar_verified": True,
        "issuer_id": "YMC", "issuer_credit_status": "IN_GOOD_STANDING_VERIFIED",
        "issuer_credit_stress_bps": "150", "indexation": "NONE",
        "redemption_basis": "FIXED_KNOWN_FACE",
        "redemption_face_official": True,
        "redemption_source_at": YESTERDAY.isoformat(),
        "redemption_known_at": YESTERDAY.isoformat(),
        "maturity_at": LATER.isoformat(),
        "native_maturity_redemption_supported": False,
        "maturity_redemption_receipt_contract": "EXACT_NOMINAL_EVENT_REDUCER_V1",
    }
    validation = {
        "identity": key, "instrument_family": family,
        "status": "OOS_VERIFIED_BY_INDEPENDENT_PIPELINE",
        "strategy_version": STRATEGY_VERSION,
        "verification_is_independent": True,
        "artifact_attested_by_release": True,
        "artifact_sha256": SHA, "dataset_sha256": "c" * 64,
        "source_at": YESTERDAY.isoformat(), "known_at": YESTERDAY.isoformat(),
        "out_of_sample_closed_trades": 48,
        "train_until": "2026-09-25T13:00:00+00:00",
        "test_from": "2026-09-26T13:00:00+00:00",
        "test_until": "2026-10-27T13:00:00+00:00",
        "net_after_cost_bps": "350", "max_drawdown_bps": "100",
    }
    return quote, scenario, terms, validation


def decide(quote, scenario, terms, validation, **kw):
    return decide_fixed_income_from_inputs(quote, decision_at=AT.isoformat(),
                                           scenario=scenario, terms=terms,
                                           validation=validation, **kw)


def test_bond_market_exit_signal_requires_independent_oos_and_official_terms():
    q, s, t, v = fixture()
    action, score, reason, features = decide(q, s, t, v)
    assert (action, reason) == ("BUY", "FI_PAPER_CANDIDATE_VALIDATED")
    assert score > 0 and features["scenario_is_forecast"] is False
    assert features["paper_only"] is True and features["real_routes_used"] == []
    assert features["entry_authority"] is False
    assert features["strategy"] == "FI_BONO_PAPER_V1"
    assert features["fixed_income_exit_plan"]["mode"] == "FIXED_INCOME_EVENT_DRIVEN_MARKET_EXIT"
    assert features["fixed_income_exit_plan"]["coupon_ledger_required"] is True
    assert PAPER_ONLY and REAL_ROUTES_USED == ()


@pytest.mark.parametrize("item,expected", [
    ({"source_authority": "IOL_SAME_PROVIDER_REFERENCE_ONLY"}, "FI_OFFICIAL_TERMS_UNVERIFIED"),
    ({"identity": ("AL30", "BONOS", "BYMA", "USD", "A-24HS")}, "FI_OFFICIAL_TERMS_UNVERIFIED"),
    ({"cashflows_verified": False}, "FI_OFFICIAL_TERMS_UNVERIFIED"),
    ({"source_at": (AT + timedelta(seconds=1)).isoformat()}, "FI_OFFICIAL_TERMS_UNVERIFIED"),
    ({"duration_years": "0"}, "FI_BOND_DURATION_OR_COUPON_EVIDENCE_RED"),
    ({"coupon_calendar_verified": False}, "FI_BOND_DURATION_OR_COUPON_EVIDENCE_RED"),
    ({"minimum_scenario_net_bps": "250"}, "FI_NET_EDGE_AFTER_FEES_TOO_LOW"),
    ({"native_market_exit_supported": False}, "FI_NATIVE_EXIT_LIFECYCLE_MISMATCH"),
    ({"native_exit_proof_verified": False}, "FI_NATIVE_EXIT_LIFECYCLE_MISMATCH"),
])
def test_bond_exact_contract_and_safety_gates(item, expected):
    q, s, terms, v = fixture()
    assert decide(q, s, {**terms, **item}, v)[2] == expected


@pytest.mark.parametrize("item,expected", [
    ({"status": "OOS_NO_VERIFICADO"}, "FI_OOS_INDEPENDENT_PROOF_MISSING"),
    ({"artifact_attested_by_release": False}, "FI_OOS_INDEPENDENT_PROOF_MISSING"),
    ({"out_of_sample_closed_trades": 2}, "FI_OOS_SAMPLE_INSUFFICIENT"),
    ({"train_until": "2026-10-28T14:00:00+00:00"}, "FI_OOS_TEMPORAL_LEAKAGE"),
    ({"net_after_cost_bps": "-1"}, "FI_OOS_EDGE_OR_DRAWDOWN_RED"),
    ({"max_drawdown_bps": "500"}, "FI_OOS_EDGE_OR_DRAWDOWN_RED"),
])
def test_out_of_sample_not_assumed_from_pit_shadow_hypothesis(item, expected):
    q, s, t, v = fixture()
    assert decide(q, s, t, {**v, **item})[2] == expected


def test_static_bid_scenario_is_not_forecast_or_authoritative_entry():
    q, s, t, v = fixture()
    assert s.entry_authority is False and s.oos_status == "OOS_NO_VERIFICADO"
    assert decide(q, s, t, {**v, "verification_is_independent": False})[0] == "HOLD"
    assert decide(q, replace(s, observed_book_source="SYNTHETIC_TEST"), t, v)[0] == "HOLD"
    assert decide(q, replace(s, scenario_net=Decimal("-1")), t, v)[0] == "HOLD"
    assert decide(q, replace(s, decision_at=YESTERDAY.isoformat()), t, v)[0] == "HOLD"
    assert decide(q, replace(s, planned_exit_at=(AT + timedelta(hours=8)).isoformat()),
                  t, v)[2] == "FI_NATIVE_EXIT_LIFECYCLE_MISMATCH"
    assert decide(q, replace(s, stress_loss=Decimal("400")), t, v)[2] == \
        "FI_BOND_DOWNSIDE_BUDGET_RED"


def test_letter_requires_fixed_face_and_separate_native_redemption_lifecycle():
    q, s, t, v = fixture("LETRAS", symbol="D30N6")
    maturity = replace(s, exit_mode="MATURITY_REDEMPTION",
                       exit_cash_scenario=Decimal("0"))
    assert decide(q, maturity, t, v)[2] == "FI_NATIVE_MATURITY_LIFECYCLE_MISSING"
    assert decide(q, maturity, {**t, "native_maturity_redemption_supported": True}, v)[2] == \
        "FI_NATIVE_MATURITY_LIFECYCLE_MISSING"
    assert decide(q, maturity,
                  {**t, "native_maturity_redemption_supported": True,
                   "indexation": "CER_FUTURE_FIXING_UNKNOWN"}, v)[0] == "HOLD"
    assert decide(q, maturity,
                  {**t, "native_maturity_redemption_supported": True,
                   "redemption_face_official": False}, v)[0] == "HOLD"


def test_on_credit_exclusions_and_coupon_gate():
    q, s, t, v = fixture("OBLIGACIONES", symbol="YMCJO")
    assert decide(q, s, t, v)[0] == "BUY"
    assert decide(q, s, {**t, "issuer_credit_status": "UNKNOWN"}, v)[2] == \
        "FI_ON_CREDIT_OR_CARRY_EVIDENCE_RED"
    assert decide(q, s, {**t, "issuer_credit_stress_bps": "500"}, v)[0] == "HOLD"
    assert decide(q, replace(s, stress_loss=Decimal("50")), t, v)[2] == \
        "FI_ON_DOWNSIDE_BUDGET_RED"


def test_adapter_producer_absent_or_unattested_holds_without_network_or_order():
    q, s, terms, validation = fixture()
    assert decide_fixed_income(q, SimpleNamespace(store=object()), AT.isoformat())[2] == \
        "FI_INPUT_PRODUCER_UNAVAILABLE"
    class TestStore:
        def fixed_income_strategy_inputs(self, *_args):
            return {"scenario": s, "terms": terms, "validation": validation,
                    "upstream_authenticated": False,
                    "receipt_attested_by_release": True, "source_kind": "PPI_PRIMARY"}
    engine = SimpleNamespace(store=TestStore())
    assert decide_fixed_income(q, engine, AT.isoformat())[2] == \
        "FI_SOURCE_ATTESTATION_MISSING"
    engine.store.fixed_income_strategy_inputs = lambda *_a: {
        "scenario": s, "terms": terms, "validation": validation,
        "upstream_authenticated": True,
        "receipt_attested_by_release": True, "source_kind": "PPI_PRIMARY"}
    assert decide_fixed_income(q, engine, AT.isoformat())[2] == \
        "FI_NATIVE_EVENT_EXIT_ENGINE_NOT_BOUND"
    engine.fixed_income_native_exit_authorized = lambda *_a: True
    action, _, _, features = decide_fixed_income(q, engine, AT.isoformat())
    assert action == "BUY" and features["paper_only"] is True


def test_unsupported_and_malformed_inputs_fail_closed():
    q, s, t, v = fixture()
    invalid = replace(q, contract=replace(q.contract, symbol="GGAL"))
    assert decide(invalid, s, t, v)[2] == "FI_NOMINAL_CONTRACT_UNVERIFIED"
    assert decide(q, None, t, v)[0] == "HOLD"
    assert decide(q, s, [], v)[0] == "HOLD"
    assert decide(q, s, t, {**v, "net_after_cost_bps": "NaN"})[0] == "HOLD"
    bad_family = replace(q, asset_class="ACCIONES")
    assert decide(bad_family, s, t, v)[2] == "FI_FAMILY_UNSUPPORTED"
