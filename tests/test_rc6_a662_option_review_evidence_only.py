"""R132 selective A662 negatives: synthetic option evidence is not admission.

No positive REVIEW relaxation, provider capture, DB writer or trusted-clock
claim. The shared mass identity planner remains under separate DATA ownership.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

import cp_contract_evidence_v2_hf6 as evidence
import cr_contract_evidence_v2_mass_hf6 as mass
import rc6_contract_bridge as bridge
import rc6_ppi_option_contract_policy as policy
from tests.test_rc6_ppi_option_contract_policy import option, row


def _inputs():
    return row("YPFD", "ACCIONES"), option()


def _derive(target, record):
    if target == "underlying":
        return policy.underlying_family_index([record])
    return policy.standard_long_option_evidence(record, {"YPFD": "ACCIONES"})


@pytest.mark.parametrize("target", ["underlying", "option"])
@pytest.mark.parametrize("missing", ["ticker", "type", "market", "currency", "settlement"])
def test_missing_raw_provider_field_cannot_be_filled_from_catalog(target, missing):
    underlying, option_row = _inputs()
    record = underlying if target == "underlying" else option_row
    raw = json.loads(record["metadata_json"])
    del raw[missing]
    record["metadata_json"] = json.dumps(raw)
    before = deepcopy(record)
    assert not _derive(target, record)
    assert record == before
    records, _, _ = mass.planned_records([underlying, option_row])
    assert not any(r["ticker"] == option_row["ticker"]
                   and r["source_class"] == "DERIVED_OFFICIAL_RULE" for r in records)


@pytest.mark.parametrize("target", ["underlying", "option"])
@pytest.mark.parametrize("field,value", [
    ("ticker", "OTHER"), ("type", "BONOS"), ("market", "A3"),
    ("currency", "USD_MEP"), ("settlement", "A-24HS"),
])
def test_contradictory_raw_provider_field_never_attests_option_terms(target, field, value):
    underlying, option_row = _inputs()
    record = underlying if target == "underlying" else option_row
    raw = json.loads(record["metadata_json"])
    raw[field] = value
    record["metadata_json"] = json.dumps(raw)
    assert not _derive(target, record)


@pytest.mark.parametrize("target", ["underlying", "option"])
def test_complete_raw_does_not_make_request_candidate_provider_authority(target):
    underlying, option_row = _inputs()
    record = underlying if target == "underlying" else option_row
    record["settlement_source"] = "REQUEST_CANDIDATE"
    assert not _derive(target, record)


@pytest.mark.parametrize("target", ["underlying", "option"])
@pytest.mark.parametrize("observed", [None, "", "2026-10-03T01:36:06", "INVALID", 17])
def test_missing_invalid_or_naive_observation_clock_denies_terms(target, observed):
    underlying, option_row = _inputs()
    record = underlying if target == "underlying" else option_row
    record["last_seen_at"] = observed
    assert not _derive(target, record)


@pytest.mark.parametrize("currency,settlement", [
    ("USD_MEP", "INMEDIATA"), ("ARS", "A-24HS"), ("USD_MEP", "A-24HS"),
])
def test_underlying_full_key_must_be_option_eligible_ars_immediate(currency, settlement):
    foreign = row("YPFD", "ACCIONES", currency=currency, settlement=settlement)
    assert policy.underlying_family_index([foreign]) == {}
    # Filtering the other row first must not hide ticker-level ambiguity.
    assert policy.underlying_family_index([row("YPFD", "ACCIONES"), foreign]) == {}


def test_conflicting_primary_row_cannot_be_dropped_to_apparently_unique_underlying():
    valid = row("YPFD", "ACCIONES")
    invalid = deepcopy(valid)
    raw = json.loads(invalid["metadata_json"])
    raw["_contract_conflicts"] = {"currency": ["ARS", "USD_MEP"]}
    invalid["metadata_json"] = json.dumps(raw)
    assert policy.underlying_family_index([valid, invalid]) == {}


@pytest.mark.parametrize("capability", ["CONTRACT_EVIDENCE_REVIEW_REQUIRED", "READY_PAPER_OPTION_LONG"])
def test_full_raw_key_does_not_relax_review_or_rederive_ready_option(capability):
    underlying, option_row = _inputs()
    option_row["capability"] = capability
    before = deepcopy((underlying, option_row))
    assert policy.standard_long_option_evidence(option_row, {"YPFD": "ACCIONES"}) is None
    records, _, _ = mass.planned_records([underlying, option_row])
    assert not any(r["ticker"] == option_row["ticker"]
                   and r["source_class"] == "DERIVED_OFFICIAL_RULE" for r in records)
    assert (underlying, option_row) == before


@pytest.mark.parametrize("hazard", ["pending_material", "revoked", "adjusted_series_unverified"])
def test_real_bridge_review_blocks_even_valid_derived_static_option_terms(hazard):
    records, _, _ = mass.planned_records(_inputs())
    selected = [r for r in records if r["ticker"] == "YPFC40500O"]
    assert {r["source_class"] for r in selected} == {"PPI_STRUCTURED_API", "DERIVED_OFFICIAL_RULE"}
    for record in selected:
        record["evidence_hash"] = evidence.evidence_hash(record["evidence"])
    if hazard == "pending_material":
        for record in selected:
            record["change_pending"] = True
    else:
        selected[0]["evidence"][hazard] = True
        selected[0]["evidence_hash"] = evidence.evidence_hash(selected[0]["evidence"])
    normalized = bridge.normalize_group(selected, now=datetime(2026, 10, 3, 2, tzinfo=timezone.utc))
    assert normalized["contract_bridge"]["status"] == "BLOCKED"
    assert "CHANGE_REVIEW_REQUIRED" in normalized["contract_bridge"]["gaps"]
    assert normalized["financial_contract_v17"] is None


def test_identical_underlying_key_deduplicates_without_changing_input():
    underlying, option_row = _inputs()
    before = deepcopy((underlying, option_row))
    assert policy.underlying_family_index([underlying, deepcopy(underlying)]) == {"YPFD": "ACCIONES"}
    terms = policy.standard_long_option_evidence(option_row, {"YPFD": "ACCIONES"})
    assert terms["cash_multiplier"] == "100"
    assert terms["broker_quantity_step"] == "NO_VERIFICADO"
    assert (underlying, option_row) == before
