from datetime import datetime, timedelta
from decimal import Decimal

from rc6_caucion_intraday_opportunity import (
    OpportunityPolicy,
    OpportunityReference,
    evaluate_intraday_opportunities,
)
from rc6_caucion_offer_adapter import CANONICAL_SCHEMA

NOW = datetime.fromisoformat("2026-09-14T15:00:00-03:00")
DEADLINE = datetime.fromisoformat("2026-09-16T16:00:00-03:00")


def snapshot(**changes):
    value = {
        "schema": CANONICAL_SCHEMA,
        "semantics_status": "VALIDATED",
        "semantic_proof": {
            "rate": "TNA_FRACTION_VALIDATED",
            "depth": "COLOCADORA_EXECUTABLE_PRINCIPAL_VALIDATED",
            "side": "COLOCADORA_SIDE_VALIDATED",
            "fees": "TOTAL_FEES_FOR_PRINCIPAL_VALIDATED",
            "maturity": "MATURITY_EXPLICIT_VALIDATED",
            "freshness": "PROVIDER_OBSERVED_AT_VALIDATED",
        },
        "ticker": "PESOS1",
        "provider_instrument_id": "ppi-pesos1",
        "market": "BYMA",
        "currency": "ARS",
        "settlement": "INMEDIATA",
        "side": "COLOCADORA",
        "operation": "COLOCAR-CAUCION",
        "term_days": 1,
        "operable": True,
        "market_session_state": "OPEN",
        "annual_rate_fraction": "0.40",
        "available_principal": "500000",
        "principal_min": "100000",
        "principal_step": "1",
        "day_count_basis": 365,
        "fee_payment": "MATURITY",
        "quoted_total_fees": "5",
        "fee_quote_principal": "100000",
        "start_date": NOW.date().isoformat(),
        "maturity_at": (NOW + timedelta(days=1)).isoformat(),
        "observed_at": (NOW - timedelta(seconds=15)).isoformat(),
        "expiry_at": (NOW + timedelta(minutes=10)).isoformat(),
        "metadata_source": "PPI_API_AUTHENTICATED",
        "evidence_id": "op-001",
    }
    value.update(changes)
    return value


def gate(**changes):
    value = {
        "name": "CAUCION_FRESH_DATA_AGENT_GREEN",
        "family": "CAUCIONES",
        "green": True,
        "contract_status": "READY_PAPER_CANDIDATE",
        "real_order_capability": False,
        "evidence_id": "fresh-001",
    }
    value.update(changes)
    return value


def policy(**changes):
    value = dict(
        version="TEST-OPPORTUNITY-V1",
        currency="ARS",
        minimum_net_annual_rate_fraction=Decimal("0.30"),
        minimum_advantage_bps=Decimal("500"),
        minimum_reference_samples=8,
        reference_max_age_seconds=3600,
        maximum_quote_age_seconds=30,
    )
    value.update(changes)
    return OpportunityPolicy(**value)


def reference(**changes):
    value = dict(
        ticker="PESOS1",
        currency="ARS",
        net_annual_rate_fraction=Decimal("0.25"),
        observed_at=(NOW - timedelta(minutes=5)).isoformat(),
        sample_count=20,
        source="TEST_VALIDATED_REFERENCE",
    )
    value.update(changes)
    return OpportunityReference(**value)


def evaluate(**changes):
    value = dict(
        canonical_snapshots=[snapshot()],
        references={"PESOS1": reference()},
        freshness_gate=gate(),
        policy=policy(),
        now=NOW,
        liquidity_deadline=DEADLINE,
    )
    value.update(changes)
    return evaluate_intraday_opportunities(**value)


def test_high_net_rate_plus_reference_advantage_becomes_candidate_but_never_auto_promotes():
    result = evaluate()
    assert result["status"] == "OPPORTUNITY_CANDIDATE"
    assert result["selected"]["ticker"] == "PESOS1"
    assert Decimal(result["selected"]["advantage_bps"]) >= Decimal("500")
    assert result["promotion_allowed"] is False
    assert result["real_order_capability"] is False


def test_high_gross_rate_without_reference_is_hold():
    result = evaluate(references={})
    assert result["status"] == "HOLD"
    assert result["candidates"][0]["code"] == "REFERENCE_MISSING"


def test_reference_must_have_enough_samples_and_be_fresh():
    result = evaluate(references={"PESOS1": reference(sample_count=2)})
    assert result["status"] == "HOLD"
    assert result["candidates"][0]["code"] == "REFERENCE_SAMPLE_INSUFFICIENT"

    stale = reference(observed_at=(NOW - timedelta(hours=2)).isoformat())
    result = evaluate(references={"PESOS1": stale})
    assert result["status"] == "HOLD"
    assert result["candidates"][0]["code"] == "REFERENCE_STALE_OR_FUTURE"


def test_policy_floor_and_relative_advantage_are_both_binding():
    high_floor = evaluate(policy=policy(minimum_net_annual_rate_fraction=Decimal("0.60")))
    assert high_floor["status"] == "HOLD"
    assert high_floor["candidates"][0]["code"] == "NET_ANNUAL_RATE_BELOW_POLICY_FLOOR"

    high_advantage = evaluate(policy=policy(minimum_advantage_bps=Decimal("2000")))
    assert high_advantage["status"] == "HOLD"
    assert high_advantage["candidates"][0]["code"] == "ADVANTAGE_BELOW_POLICY_THRESHOLD"


def test_red_freshness_gate_blocks_before_opportunity_analysis():
    result = evaluate(freshness_gate=gate(green=False))
    assert result["status"] == "HOLD"
    assert result["code"] == "FRESHNESS_GATE_RED"
    assert result["candidates"] == []


def test_maturity_after_liquidity_deadline_is_hold_even_with_great_rate():
    late = snapshot(maturity_at=(NOW + timedelta(days=7)).isoformat(), term_days=1)
    result = evaluate(canonical_snapshots=[late])
    assert result["status"] == "HOLD"
    assert result["candidates"][0]["code"].startswith("INVALID_CANONICAL_OR_ECONOMIC_EVIDENCE")


def test_raw_ppi_field_cannot_create_opportunity():
    raw = snapshot(price="99.0")
    result = evaluate(canonical_snapshots=[raw])
    assert result["status"] == "HOLD"
    assert result["candidates"][0]["code"].startswith("INVALID_CANONICAL_OR_ECONOMIC_EVIDENCE")


def test_quote_ttl_cannot_be_relaxed_past_canonical_limit():
    try:
        policy(maximum_quote_age_seconds=301)
    except ValueError as exc:
        assert "cannot relax canonical TTL" in str(exc)
    else:
        raise AssertionError("policy unexpectedly relaxed canonical TTL")
