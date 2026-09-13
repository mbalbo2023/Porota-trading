from datetime import datetime
from decimal import Decimal

import pytest

from rc6_caucion_offer_adapter import (
    CANONICAL_SCHEMA,
    CaucionOfferAdapterError,
    offer_from_canonical_snapshot,
)

NOW = datetime.fromisoformat("2026-09-14T15:00:00-03:00")


def canonical_snapshot(**changes):
    data = {
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
        "provider_instrument_id": "ppi-caucion-pesos-1",
        "market": "BYMA",
        "currency": "ARS",
        "settlement": "INMEDIATA",
        "side": "COLOCADORA",
        "operation": "COLOCAR-CAUCION",
        "term_days": 1,
        "operable": True,
        "market_session_state": "OPEN",
        "annual_rate_fraction": "0.35",
        "available_principal": "500000",
        "principal_min": "100000",
        "principal_step": "1",
        "day_count_basis": 365,
        "fee_payment": "MATURITY",
        "quoted_total_fees": "50",
        "fee_quote_principal": "100000",
        "start_date": "2026-09-14",
        "maturity_at": "2026-09-15T15:00:00-03:00",
        "observed_at": "2026-09-14T14:59:30-03:00",
        "expiry_at": "2026-09-14T15:05:00-03:00",
        "metadata_source": "PPI_API_AUTHENTICATED",
        "evidence_id": "ev-caucion-001",
    }
    data.update(changes)
    return data


def test_validated_canonical_snapshot_builds_existing_paper_offer():
    offer = offer_from_canonical_snapshot(canonical_snapshot(), now=NOW)
    assert offer.instrument_id == "PESOS1"
    assert offer.currency == "ARS"
    assert offer.annual_rate_fraction == Decimal("0.35")
    assert offer.available_principal == Decimal("500000")
    assert offer.minimum_principal == Decimal("100000")
    assert offer.principal_step == Decimal("1")
    assert offer.quoted_total_fees == Decimal("50")
    assert offer.fee_quote_principal == Decimal("100000")
    assert "PPI_API_AUTHENTICATED" in offer.metadata_source
    assert "ev-caucion-001" in offer.metadata_source


@pytest.mark.parametrize("raw_field", ["price", "volume", "quantity", "bids", "offers", "current", "book"])
def test_raw_ppi_market_fields_are_never_interpreted(raw_field):
    snapshot = canonical_snapshot(**{raw_field: "AMBIGUOUS_RAW_PROVIDER_VALUE"})
    with pytest.raises(CaucionOfferAdapterError, match="raw/ambiguous"):
        offer_from_canonical_snapshot(snapshot, now=NOW)


def test_unvalidated_semantics_fail_closed():
    with pytest.raises(CaucionOfferAdapterError, match="not VALIDATED"):
        offer_from_canonical_snapshot(canonical_snapshot(semantics_status="UNVALIDATED"), now=NOW)


def test_missing_specific_semantic_proof_fails_closed():
    proof = dict(canonical_snapshot()["semantic_proof"])
    proof["depth"] = "volume_assumption"
    with pytest.raises(CaucionOfferAdapterError, match="semantic proof"):
        offer_from_canonical_snapshot(canonical_snapshot(semantic_proof=proof), now=NOW)


def test_stale_market_snapshot_fails_closed_at_five_minutes():
    with pytest.raises(CaucionOfferAdapterError, match="stale"):
        offer_from_canonical_snapshot(
            canonical_snapshot(observed_at="2026-09-14T14:54:59-03:00"),
            now=NOW,
        )


def test_caller_cannot_relax_dynamic_ttl():
    with pytest.raises(CaucionOfferAdapterError, match="cannot relax"):
        offer_from_canonical_snapshot(canonical_snapshot(), now=NOW, max_age_seconds=301)


def test_explicit_cost_budget_is_mandatory():
    snapshot = canonical_snapshot()
    del snapshot["quoted_total_fees"]
    with pytest.raises(CaucionOfferAdapterError, match="quoted_total_fees"):
        offer_from_canonical_snapshot(snapshot, now=NOW)


def test_wrong_side_or_operation_cannot_reach_paper_offer():
    with pytest.raises(CaucionOfferAdapterError, match="colocadora"):
        offer_from_canonical_snapshot(canonical_snapshot(side="TOMADORA"), now=NOW)
    with pytest.raises(CaucionOfferAdapterError, match="COLOCAR-CAUCION"):
        offer_from_canonical_snapshot(canonical_snapshot(operation="COMPRAR"), now=NOW)


def test_ticker_term_and_currency_conflicts_fail_closed():
    with pytest.raises(CaucionOfferAdapterError, match="term_days conflicts"):
        offer_from_canonical_snapshot(canonical_snapshot(term_days=7), now=NOW)
    with pytest.raises(CaucionOfferAdapterError, match="currency conflicts"):
        offer_from_canonical_snapshot(canonical_snapshot(currency="USD_MEP"), now=NOW)


def test_dynamic_operability_session_and_expiry_are_required():
    with pytest.raises(CaucionOfferAdapterError, match="not explicitly operable"):
        offer_from_canonical_snapshot(canonical_snapshot(operable=False), now=NOW)
    with pytest.raises(CaucionOfferAdapterError, match="not explicitly OPEN"):
        offer_from_canonical_snapshot(canonical_snapshot(market_session_state="CLOSED"), now=NOW)
    with pytest.raises(CaucionOfferAdapterError, match="expired"):
        offer_from_canonical_snapshot(canonical_snapshot(expiry_at="2026-09-14T15:00:00-03:00"), now=NOW)


def test_dolar_offer_requires_explicit_validated_costs_and_currency():
    snapshot = canonical_snapshot(
        ticker="DOLAR1",
        provider_instrument_id="ppi-caucion-dolar-1",
        currency="USD_MEP",
        principal_min="100",
        available_principal="1000",
        fee_quote_principal="100",
    )
    offer = offer_from_canonical_snapshot(snapshot, now=NOW)
    assert offer.currency == "USD_MEP"
    assert offer.quoted_total_fees == Decimal("50")
