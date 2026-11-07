"""A662: review-mode PPI/BYMA option *evidence* is not PAPER admission.

All five-key PPI fixtures are deliberately synthetic.  Real SearchInstrument
settlement proof must be audited independently; these tests send no requests.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

import cp_contract_evidence_v2_hf6 as evidence
import cr_contract_evidence_v2_mass_hf6 as mass
import rc6_contract_bridge as bridge
import rc6_ppi_option_contract_policy as policy


NOW = "2026-10-03T01:36:06+00:00"
AT = datetime(2026, 10, 3, 2, 0, tzinfo=timezone.utc)


def ppi_row(ticker, family, description="", *, capability="READY_PAPER_SPOT"):
    literal = dict(ticker=ticker, type=family, market="BYMA", currency="ARS",
                   settlement="INMEDIATA")
    return dict(
        ticker=ticker, instrument_type=family, market="BYMA", currency="ARS",
        settlement="INMEDIATA", settlement_source="PPI_FIELD",
        status="AVAILABLE", capability=capability, last_seen_at=NOW,
        description=description,
        metadata_json=json.dumps({
            **literal, "_discovery_source": "PPI_PRIMARY",
            "_provider_instrument_type": family,
            "_fixture_synthetic_full_key": True,
        }),
    )


def inputs(*, review=True):
    underlying = ppi_row("YPFD", "ACCIONES")
    option = ppi_row(
        "YPFC40500O", "OPCIONES",
        "Opción compra YPFD AR$ 40500.00 Vto. 16/10/2026",
        capability=("CONTRACT_EVIDENCE_REVIEW_REQUIRED" if review
                    else "NEEDS_OPTION_CONTRACT"),
    )
    return underlying, option


def option_evidence_rows(rows):
    records, skipped, identities = mass.planned_records(rows)
    selected = [dict(r, evidence_hash=evidence.evidence_hash(r["evidence"]))
                for r in records if r["ticker"] == "YPFC40500O"]
    return selected, skipped, identities


def test_review_option_recovers_derived_contract_evidence_without_admission():
    underlying, option = inputs(review=True)
    fields = policy.standard_long_option_evidence(option, {"YPFD": "ACCIONES"})
    assert fields is not None
    assert fields["cash_multiplier"] == "100"
    assert fields["paper_quantity_min"] == "1"
    assert fields["broker_quantity_step"] == "NO_VERIFICADO"
    rows, skipped, identities = option_evidence_rows([underlying, option])
    assert identities == {"ACCIONES": 1, "OPCIONES": 1}
    assert [r["source_class"] for r in rows] == [
        "PPI_STRUCTURED_API", "DERIVED_OFFICIAL_RULE"]
    assert rows[1]["source_ref"] == policy.OPTION_DERIVED_SOURCE_REF
    assert "OPTION_STANDARD_CONTRACT_POLICY_UNRESOLVED" not in skipped
    # No quote, order or candidate ledger is created by this evidence planner.
    assert option["capability"] == "CONTRACT_EVIDENCE_REVIEW_REQUIRED"


@pytest.mark.parametrize("hazard", [
    "pending_material", "revoked", "adjusted_series_unverified",
])
def test_live_material_or_series_review_remains_blocked_even_with_new_terms(hazard):
    rows, _, _ = option_evidence_rows(inputs(review=True))
    assert len(rows) == 2
    if hazard == "pending_material":
        for row in rows:
            row["change_pending"] = True
    else:
        rows[0]["evidence"][hazard] = True
        rows[0]["evidence_hash"] = evidence.evidence_hash(rows[0]["evidence"])
    normalized = bridge.normalize_group(rows, now=AT)
    assert normalized["contract_bridge"]["status"] == "BLOCKED"
    assert "CHANGE_REVIEW_REQUIRED" in normalized["contract_bridge"]["gaps"]
    assert normalized["financial_contract_v17"] is None


@pytest.mark.parametrize("change", [
    "request_candidate", "not_ppi", "missing_literal_settlement",
    "wrong_literal_settlement", "wrong_literal_market", "wrong_literal_currency",
    "missing_recorded_clock", "wrong_market", "wrong_currency",
    "wrong_option_description", "nonavailable", "negative_conflicts",
])
def test_review_does_not_derive_without_exact_ppi_and_official_terms(change):
    underlying, option = inputs(review=True)
    raw = json.loads(option["metadata_json"])
    if change == "request_candidate":
        option["settlement_source"] = "REQUEST_CANDIDATE"
    elif change == "not_ppi":
        raw["_discovery_source"] = "IOL_COMPLEMENTARY"
    elif change == "missing_literal_settlement":
        raw.pop("settlement")
    elif change == "wrong_literal_settlement":
        raw["settlement"] = "A-24HS"
    elif change == "wrong_literal_market":
        raw["market"] = "A3"
    elif change == "wrong_literal_currency":
        raw["currency"] = "USD_MEP"
    elif change == "missing_recorded_clock":
        option["last_seen_at"] = "2026-10-03T01:36:06"
    elif change == "wrong_market":
        option["market"] = "A3"
    elif change == "wrong_currency":
        option["currency"] = "USD_MEP"
    elif change == "wrong_option_description":
        option["description"] = "YPFC40500O"
    elif change == "nonavailable":
        option["status"] = "STALE"
    elif change == "negative_conflicts":
        raw["_contract_conflicts"] = {"fields": ["strike"]}
    option["metadata_json"] = json.dumps(raw)
    assert policy.standard_long_option_evidence(option, {"YPFD": "ACCIONES"}) is None
    rows, _, _ = option_evidence_rows([underlying, option])
    assert not any(r["source_class"] == "DERIVED_OFFICIAL_RULE" for r in rows)


def test_underlying_without_literal_provider_settlement_cannot_supply_option_lot():
    underlying, option = inputs(review=True)
    raw = json.loads(underlying["metadata_json"])
    raw.pop("settlement")
    underlying["metadata_json"] = json.dumps(raw)
    assert policy.underlying_family_index([underlying, option]) == {}
    rows, _, _ = option_evidence_rows([underlying, option])
    assert not any(r["source_class"] == "DERIVED_OFFICIAL_RULE" for r in rows)


def test_previously_ready_option_does_not_emit_duplicate_derived_terms():
    underlying, option = inputs(review=False)
    option["capability"] = "READY_PAPER_OPTION_LONG"
    assert policy.standard_long_option_evidence(option, {"YPFD": "ACCIONES"}) is None
    rows, _, _ = option_evidence_rows([underlying, option])
    assert len(rows) == 1 and rows[0]["source_class"] == "PPI_STRUCTURED_API"


def test_recovery_planning_does_not_mutate_stored_evidence_or_input():
    underlying, option = inputs(review=True)
    before = deepcopy((underlying, option))
    option_evidence_rows([underlying, option])
    assert before == (underlying, option)
