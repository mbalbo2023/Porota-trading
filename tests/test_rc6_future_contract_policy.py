from datetime import datetime, timezone
import json
from decimal import Decimal

import bs_instrument_contracts as contracts
import cp_contract_evidence_v2_hf6 as evidence
import cr_contract_evidence_v2_mass_hf6 as mass
import rc6_contract_bridge as bridge
import rc6_ppi_future_contract_policy as policy


NOW = "2026-10-03T16:00:00+00:00"


def future_row(ticker="DLR/OCT26", *, capability="NEEDS_FUTURES_MARGIN_AND_CONTRACT",
               status="AVAILABLE", market="A3", currency="ARS",
               settlement="INMEDIATA", discovery="PPI_PRIMARY"):
    return {
        "ticker": ticker,
        "instrument_type": "FUTUROS",
        "market": market,
        "currency": currency,
        "settlement": settlement,
        "settlement_source": "PPI_FIELD",
        "status": status,
        "capability": capability,
        "last_seen_at": NOW,
        "description": ticker,
        "metadata_json": json.dumps({
            "_discovery_source": discovery,
            "_provider_instrument_type": "FUTUROS",
        }),
    }


def test_standard_dlr_policy_uses_exact_a3_terms_and_paper_full_notional():
    row = future_row()
    value = policy.standard_dlr_future_evidence(row)
    assert value["cash_multiplier"] == "1000"
    assert value["paper_quantity_min"] == "1"
    assert value["paper_quantity_step"] == "1"
    assert value["underlying"] == "DOLAR_A3500"
    assert value["expires_at"] == "2026-10-30T15:00:00-03:00"
    assert value["paper_margin_policy"] == "CONSERVATIVE_NOTIONAL_RATE"
    assert value["paper_margin_rate"] == "1"
    assert value["broker_margin_requirement"] == "NO_VERIFICADO_DYNAMIC"


def test_spreads_suffixes_and_unproved_years_stay_fail_closed():
    for ticker in (
        "DLR/OCT26M",
        "DLR/OCT26-DLR/NOV26",
        "DLR/ENE27",
        "DLR/OCT2026",
        "MERV/OCT26",
    ):
        assert policy.standard_dlr_future_evidence(future_row(ticker)) is None


def test_identity_dimensions_and_nonmissing_contract_cohorts_stay_blocked():
    bad = [
        future_row(market="BYMA"),
        future_row(currency="USD"),
        future_row(settlement="A-24HS"),
        future_row(discovery="IOL_COMPLEMENTARY"),
        future_row(status="STALE"),
        future_row(capability="CONTRACT_EVIDENCE_REVIEW_REQUIRED"),
    ]
    assert all(policy.standard_dlr_future_evidence(row) is None for row in bad)


def test_mass_evidence_normalizes_to_existing_future_contract_path():
    records, skipped, identities = mass.planned_records([future_row()])
    assert identities == {"FUTUROS": 1}
    future_records = [r for r in records if r["ticker"] == "DLR/OCT26"]
    assert len(future_records) == 2
    derived = next(r for r in future_records if r["source_class"] == "DERIVED_OFFICIAL_RULE")
    assert derived["source_ref"] == policy.A3_DLR_SOURCE_REF
    for record in future_records:
        record["evidence_hash"] = evidence.evidence_hash(record["evidence"])
    claim = bridge.normalize_group(
        future_records, now=datetime(2026, 10, 3, 17, 0, tzinfo=timezone.utc))
    assert claim["contract_bridge"]["status"] == "NORMALIZED"
    contract = contracts.contract_from_metadata(
        "DLR/OCT26", "FUTUROS", claim["financial_contract_v17"])
    assert contract.market == "A3"
    assert contract.cash_multiplier == Decimal("1000")
    assert contract.quantity_step == Decimal("1")
    assert contract.minimum_quantity == Decimal("1")
    assert contract.paper_margin_policy == "CONSERVATIVE_NOTIONAL_RATE"
    assert contract.paper_margin_rate == Decimal("1")
    assert contract.cash_required("1500", "1") == Decimal("1500000")


def test_unresolved_future_keeps_explicit_mass_skip_reason():
    records, skipped, identities = mass.planned_records([future_row("DLR/OCT26M")])
    assert identities == {"FUTUROS": 1}
    assert len(records) == 1
    assert skipped["FUTURE_STANDARD_DLR_POLICY_UNRESOLVED"] == 1


def test_future_paper_window_is_a3_specific_and_has_eod_buffers():
    contract = contracts.InstrumentContract(
        "DLR/OCT26", "FUTUROS", "ARS", "A3", "INMEDIATA",
        Decimal("1000"), Decimal("1"), "TEST",
        expires_at="2026-10-30T15:00:00-03:00",
        minimum_quantity=Decimal("1"),
        paper_margin_policy="CONSERVATIVE_NOTIONAL_RATE",
        paper_margin_rate=Decimal("1"),
        underlying="DOLAR_A3500",
    )
    assert policy.admission_error("2026-10-05T13:00:00-03:00", contract) == ""
    assert policy.admission_error("2026-10-05T14:35:00-03:00", contract) == "FUTURES_EOD_NO_NEW_ENTRIES"
    assert policy.exit_due("2026-10-05T14:50:00-03:00", contract) is True
    assert policy.paper_session_state("2026-10-05T15:00:00-03:00", contract) == "OUTSIDE_FUTURES_PAPER_WINDOW"
