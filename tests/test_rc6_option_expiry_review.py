"""A222: expiry evidence is never automatic exercise or cash authority."""

from copy import deepcopy
from decimal import Decimal

import pytest

from bs_instrument_contracts import InstrumentContract
from rc6_option_expiry_review import (
    EXPIRY_REFERENCE_SCHEMA,
    PAPER_ONLY,
    REAL_ROUTES_USED,
    contract_snapshot_sha256,
    review_long_option_expiry_reference,
)


EXPIRY = "2026-11-20T15:30:00-03:00"
AS_OF = "2026-11-20T16:10:00-03:00"


def option_contract(*, right="CALL"):
    return InstrumentContract(
        symbol="GFGC7000OC" if right == "CALL" else "GFGV7000OC",
        family="OPCIONES", currency="ARS", market="BYMA",
        settlement="INMEDIATA", cash_multiplier=Decimal("100"),
        quantity_step=Decimal("1"),
        metadata_source="PPI_SEARCH_INSTRUMENT+BYMA_OPTION_SERIES_REVIEW",
        expires_at=EXPIRY, underlying="GGAL", strike=Decimal("7000"),
        option_right=right,
    )


def official_reference(contract, *, price="7050"):
    return {
        "schema": EXPIRY_REFERENCE_SCHEMA,
        "source": "BYMA_CLEARING_OFFICIAL",
        "document_sha256": "a" * 64,
        "contract_sha256": contract_snapshot_sha256(contract),
        "identity": {
            "symbol": contract.symbol,
            "family": "OPCIONES",
            "market": "BYMA",
            "currency": "ARS",
            "settlement": "INMEDIATA",
            "underlying": contract.underlying,
            "option_right": contract.option_right,
            "strike": "7000.00",
            "cash_multiplier": "100.0",
            "quantity_step": "1.0",
            "expires_at": "2026-11-20T18:30:00Z",
        },
        "adjustment_status": "UNCHANGED_REVIEWED",
        "delivery_rule": "PHYSICAL",
        "official_underlying_price": price,
        "official_underlying_price_at": EXPIRY,
        "effective_at": "2026-11-20T15:45:00-03:00",
        "published_at": "2026-11-20T15:46:00-03:00",
        "received_at": "2026-11-20T15:47:00-03:00",
    }


@pytest.mark.parametrize(
    "right,price,intrinsic",
    [
        ("CALL", "7050", "50"),
        ("CALL", "6950", "0"),
        ("PUT", "6950", "50"),
        ("PUT", "7050", "0"),
    ],
)
def test_matched_official_reference_remains_nonposting_without_holder_instruction(
    right, price, intrinsic
):
    contract = option_contract(right=right)
    review = review_long_option_expiry_reference(
        contract, official_reference(contract, price=price), as_of=AS_OF,
        position_side="LONG")
    assert review.status == "NO_VERIFICADO"
    assert review.contract_matched is True
    assert review.reason_code == "OPTION_HOLDER_INSTRUCTION_AND_CLEARING_OUTCOME_REQUIRED"
    assert review.indicative_intrinsic_per_unit == Decimal(intrinsic)
    assert review.paper_exercise_authorized is False
    assert review.paper_cash_post_authorized is False
    assert review.real_routes_used == ()
    assert PAPER_ONLY and REAL_ROUTES_USED == ()


def test_expiry_reference_cannot_authorize_early_exercise():
    contract = option_contract()
    reference = official_reference(contract)
    result = review_long_option_expiry_reference(
        contract, reference, as_of="2026-11-19T15:00:00-03:00",
        position_side="LONG")
    assert result.reason_code == "OPTION_EXPIRY_NOT_DUE"
    assert result.status == "BLOQUEADO"
    assert result.indicative_intrinsic_per_unit is None


@pytest.mark.parametrize(
    "path,value,reason",
    [
        (("schema",), "unknown", "OPTION_OFFICIAL_REFERENCE_SCHEMA_UNVERIFIED"),
        (("source",), "IOL", "OPTION_OFFICIAL_REFERENCE_SOURCE_UNVERIFIED"),
        (("document_sha256",), "not-a-hash", "OPTION_OFFICIAL_DOCUMENT_DIGEST_MISSING"),
        (("contract_sha256",), "0" * 64, "OPTION_EXPIRY_CONTRACT_SNAPSHOT_MISMATCH"),
        (("identity", "symbol"), "GGAL", "OPTION_EXPIRY_IDENTITY_MISMATCH"),
        (("identity", "underlying"), "PAMP", "OPTION_EXPIRY_IDENTITY_MISMATCH"),
        (("identity", "market"), "A3", "OPTION_EXPIRY_IDENTITY_MISMATCH"),
        (("identity", "option_right"), "PUT", "OPTION_EXPIRY_IDENTITY_MISMATCH"),
        (("identity", "strike"), "7001", "OPTION_EXPIRY_ECONOMIC_TERMS_MISMATCH"),
        (("identity", "cash_multiplier"), "10", "OPTION_EXPIRY_ECONOMIC_TERMS_MISMATCH"),
        (("identity", "quantity_step"), "0.5", "OPTION_EXPIRY_ECONOMIC_TERMS_MISMATCH"),
        (("identity", "expires_at"), "2026-11-21T15:30:00-03:00", "OPTION_EXPIRY_ECONOMIC_TERMS_MISMATCH"),
        (("adjustment_status",), "ADJUSTED", "OPTION_CORPORATE_ACTION_REVIEW_REQUIRED"),
        (("adjustment_status",), None, "OPTION_CORPORATE_ACTION_REVIEW_REQUIRED"),
        (("delivery_rule",), "UNKNOWN", "OPTION_EXERCISE_DELIVERY_RULE_UNVERIFIED"),
        (("official_underlying_price",), "NaN", "OPTION_EXPIRY_UNDERLYING_REFERENCE_INVALID"),
        (("official_underlying_price",), "0", "OPTION_EXPIRY_UNDERLYING_REFERENCE_INVALID"),
    ],
)
def test_any_mismatch_blocks_expiry_evidence(path, value, reason):
    contract = option_contract()
    evidence = official_reference(contract)
    location = evidence
    for key in path[:-1]:
        location = location[key]
    location[path[-1]] = value
    result = review_long_option_expiry_reference(contract, evidence, as_of=AS_OF,
                                                  position_side="LONG")
    assert result.status == "BLOQUEADO"
    assert result.reason_code == reason
    assert not result.contract_matched
    assert not result.paper_exercise_authorized
    assert not result.paper_cash_post_authorized


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("effective_at", "2026-11-20T15:29:59-03:00", "OPTION_EXPIRY_PIT_SEQUENCE_INVALID"),
        ("published_at", "2026-11-20T15:40:00-03:00", "OPTION_EXPIRY_PIT_SEQUENCE_INVALID"),
        ("received_at", "2026-11-20T16:11:00-03:00", "OPTION_EXPIRY_PIT_SEQUENCE_INVALID"),
        ("received_at", "2026-11-19T15:47:00-03:00", "OPTION_EXPIRY_PIT_SEQUENCE_INVALID"),
        ("effective_at", "invalid", "OPTION_EXPIRY_PROVENANCE_CLOCK_UNVERIFIED"),
        ("official_underlying_price_at", "2026-11-20T16:00:00-03:00", "OPTION_EXPIRY_UNDERLYING_REFERENCE_CLOCK_INVALID"),
        ("official_underlying_price_at", "2026-11-19T15:30:00-03:00", "OPTION_EXPIRY_UNDERLYING_REFERENCE_CLOCK_INVALID"),
    ],
)
def test_future_leak_and_causal_clock_reversal_fail_closed(field, value, reason):
    contract = option_contract()
    evidence = official_reference(contract)
    evidence[field] = value
    result = review_long_option_expiry_reference(contract, evidence, as_of=AS_OF,
                                                  position_side="LONG")
    assert result.reason_code == reason
    assert result.status == "BLOQUEADO"


def test_missing_document_or_identity_never_implies_lapse_or_exercise():
    contract = option_contract()
    missing = review_long_option_expiry_reference(contract, None, as_of=AS_OF,
                                                  position_side="LONG")
    assert missing.reason_code == "OPTION_OFFICIAL_REFERENCE_MISSING"
    evidence = official_reference(contract)
    evidence["identity"] = None
    unknown = review_long_option_expiry_reference(contract, evidence, as_of=AS_OF,
                                                  position_side="LONG")
    assert unknown.reason_code == "OPTION_EXPIRY_IDENTITY_UNVERIFIED"
    for row in (missing, unknown):
        assert row.indicative_intrinsic_per_unit is None
        assert not row.paper_cash_post_authorized


def test_candidate_is_pure_and_cannot_mutate_provenance_or_contract():
    contract = option_contract()
    evidence = official_reference(contract)
    original = deepcopy(evidence)
    first = review_long_option_expiry_reference(contract, evidence, as_of=AS_OF,
                                                position_side="LONG")
    second = review_long_option_expiry_reference(contract, evidence, as_of=AS_OF,
                                                 position_side="LONG")
    assert evidence == original
    assert first == second
    assert first.contract_matched
    assert first.paper_cash_post_authorized is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("paper_exercise_authorized", True),
        ("paper_cash_post_authorized", True),
        ("real_order_id", "invented-real-order"),
        ("broker_order_id", "invented-broker-order"),
        ("cash_posted", "1000000"),
        ("real_routes_used", ["POST /orders"]),
    ],
)
def test_reference_cannot_smuggle_execution_or_cash_authority(field, value):
    contract = option_contract()
    evidence = official_reference(contract)
    evidence[field] = value
    result = review_long_option_expiry_reference(contract, evidence, as_of=AS_OF,
                                                  position_side="LONG")
    assert result.status == "BLOQUEADO"
    assert result.reason_code == "OPTION_REFERENCE_ECONOMIC_AUTHORITY_NOT_ACCEPTED"
    assert result.paper_exercise_authorized is False
    assert result.paper_cash_post_authorized is False
    assert result.real_routes_used == ()


def test_non_option_contract_stays_blocked():
    contract = option_contract()
    unrelated = InstrumentContract(
        symbol="GD35", family="BONOS", currency="ARS", market="BYMA",
        settlement="INMEDIATA", cash_multiplier=Decimal("1"),
        quantity_step=Decimal("1"), metadata_source="PPI")
    result = review_long_option_expiry_reference(
        unrelated, official_reference(contract), as_of=AS_OF, position_side="LONG")
    assert result.reason_code == "OPTION_EXACT_LONG_CONTRACT_REQUIRED"


@pytest.mark.parametrize("side", ["SHORT", "UNKNOWN", "", None])
def test_unproven_long_position_cannot_reuse_expiry_reference(side):
    contract = option_contract()
    result = review_long_option_expiry_reference(
        contract, official_reference(contract), as_of=AS_OF, position_side=side)
    assert result.status == "BLOQUEADO"
    assert result.reason_code == "OPTION_LONG_POSITION_SIDE_UNVERIFIED"
    assert not result.paper_exercise_authorized
