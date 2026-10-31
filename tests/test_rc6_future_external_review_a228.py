"""A228: bounded synthetic A3 DLR source/PIT replay without clearing authority.

These fixture bytes are not authentic A3 data. No order, margin reservation,
ledger or settlement writer is imported or called by this review.
"""
from decimal import Decimal
import hashlib
import json

import pytest

from bs_instrument_contracts import InstrumentContract
from rc6_ppi_future_contract_policy import (
    ENTRY_AUTHORITY, EXTERNAL_EVIDENCE_SCHEMA, PAPER_ONLY, REAL_ROUTES_USED,
    admission_error, paper_session_state,
    review_dlr_external_reference, standard_dlr_terms,
)


REPLAY_AT = "2026-10-30T15:07:00-03:00"
ISSUER_BYTES = b"SYNTHETIC A3/Argentina Clearing price replay; not provider authenticated"
MARGIN_BYTES = b"SYNTHETIC A3/Argentina Clearing margin replay; not broker authenticated"


def _row(symbol="DLR/OCT26"):
    return {
        "ticker": symbol, "instrument_type": "FUTUROS", "market": "A3",
        "currency": "ARS", "settlement": "INMEDIATA", "status": "AVAILABLE",
        "capability": "NEEDS_FUTURES_MARGIN_AND_CONTRACT",
        "metadata_json": json.dumps({"_discovery_source": "PPI_PRIMARY"}),
    }


def _reference(*, symbol="DLR/OCT26", kind="FINAL_SETTLEMENT_PRICE"):
    source_bytes = ISSUER_BYTES if kind == "FINAL_SETTLEMENT_PRICE" else MARGIN_BYTES
    terms = standard_dlr_terms(symbol)
    evidence = {
        "schema": EXTERNAL_EVIDENCE_SCHEMA,
        "source": "A3_ARGENTINA_CLEARING_OFFICIAL",  # CLAIM, not authentication
        "source_reference": "SYNTHETIC_ARCHIVED_A3_REPLAY_NOT_OFFICIAL",
        "document_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "identity": {
            "ticker": symbol, "family": "FUTUROS", "market": "A3", "currency": "ARS",
            "settlement": "INMEDIATA", "underlying": "DOLAR_A3500",
            "contract_size": "USD 1000", "settlement_type": "FINANCIAL_CASH_SETTLEMENT",
            "cash_multiplier": "1000", "expires_at": terms["expires_at"],
        },
        "kind": kind,
        "effective_at": "2026-10-30T15:00:00-03:00",
        "published_at": "2026-10-30T15:05:00-03:00",
        "received_at": "2026-10-30T15:06:00-03:00",
    }
    if kind == "FINAL_SETTLEMENT_PRICE":
        evidence.update(price_rule="PRESERVE_PUBLISHED_DECIMAL",
                        price_currency="ARS_PER_USD",
                        official_settlement_price="1501.37")
    else:
        evidence.update(margin_currency="ARS", official_margin_per_contract="50000")
    return evidence, source_bytes


@pytest.mark.parametrize("kind", ["FINAL_SETTLEMENT_PRICE", "MARGIN_SCHEDULE"])
def test_replayed_synthetic_price_or_margin_never_grants_settlement_or_margin(kind):
    reference, source_bytes = _reference(kind=kind)
    result = review_dlr_external_reference(
        _row(), reference, source_bytes=source_bytes, as_of=REPLAY_AT)
    assert result.status == "NO_VERIFICADO"
    assert result.reason_code == "FUTURE_EXTERNAL_AUTHENTICATION_AND_CLEARING_REQUIRED"
    assert result.identity_matched and result.source_kind == kind
    assert result.paper_settlement_authorized is False
    assert result.paper_margin_authorized is False
    assert result.paper_only and PAPER_ONLY
    assert not result.entry_authority and not ENTRY_AUTHORITY
    assert result.real_routes_used == REAL_ROUTES_USED == ()
    assert result == review_dlr_external_reference(
        _row(), reference, source_bytes=source_bytes, as_of=REPLAY_AT)


def test_historical_margin_notice_before_expiry_is_observation_not_broker_reserve():
    reference, raw = _reference(kind="MARGIN_SCHEDULE")
    reference.update(effective_at="2026-10-05T09:00:00-03:00",
                     published_at="2026-10-05T09:01:00-03:00",
                     received_at="2026-10-05T09:02:00-03:00")
    decision = review_dlr_external_reference(
        _row(), reference, source_bytes=raw, as_of="2026-10-05T09:03:00-03:00")
    assert decision.status == "NO_VERIFICADO"
    assert decision.paper_margin_authorized is False
    reference["official_margin_per_contract"] = "NaN"
    bad = review_dlr_external_reference(
        _row(), reference, source_bytes=raw, as_of="2026-10-05T09:03:00-03:00")
    assert bad.reason_code == "FUTURE_EXTERNAL_ECONOMIC_VALUE_INVALID"


@pytest.mark.parametrize("mutation,reason", [
    (lambda x: x[0].update(source="FAKE_PROVIDER"), "FUTURE_EXTERNAL_SOURCE_UNVERIFIED"),
    (lambda x: x[0]["identity"].update(ticker="DLR/NOV26"),
     "FUTURE_EXTERNAL_SERIES_IDENTITY_MISMATCH"),
    (lambda x: x[0]["identity"].update(cash_multiplier="100"),
     "FUTURE_EXTERNAL_CONTRACT_TERMS_MISMATCH"),
    (lambda x: x[0]["identity"].update(expires_at="2026-10-30T15:00:00.000001-03:00"),
     "FUTURE_EXTERNAL_CONTRACT_TERMS_MISMATCH"),
    (lambda x: x[0].update(published_at="2026-10-30T15:08:00-03:00"),
     "FUTURE_EXTERNAL_PIT_SEQUENCE_INVALID"),
    (lambda x: x[0].pop("effective_at"),
     "FUTURE_EXTERNAL_TERMS_OR_CLOCK_INVALID"),
    (lambda x: x[0].update(effective_at="2026-10-30T14:59:59.999999-03:00"),
     "FUTURE_FINAL_SETTLEMENT_BEFORE_EXPIRY"),
    (lambda x: x[0].update(official_settlement_price="NaN"),
     "FUTURE_EXTERNAL_ECONOMIC_VALUE_INVALID"),
    (lambda x: x[0].update(price_rule="INFER_FROM_BOOK"),
     "FUTURE_SETTLEMENT_PRICE_RULE_UNVERIFIED"),
    (lambda x: x[0].update(cash_posted="100000"),
     "FUTURE_REFERENCE_ECONOMIC_AUTHORITY_FORBIDDEN"),
    (lambda x: x.__setitem__(1, x[1] + b" tampered"),
     "FUTURE_EXTERNAL_SOURCE_BYTES_MISMATCH"),
])
def test_negative_archived_price_replay_is_fail_closed(mutation, reason):
    evidence, raw = _reference()
    pair = [evidence, raw]
    mutation(pair)
    result = review_dlr_external_reference(
        _row(), pair[0], source_bytes=pair[1], as_of=REPLAY_AT)
    assert result.status == "BLOQUEADO" and result.reason_code == reason
    assert result.real_routes_used == ()


@pytest.mark.parametrize("at,reason", [
    ("2026-10-03T12:00:00-03:00", "FUTURE_STATIC_TERMS_NOT_KNOWN_AT_PIT"),
    ("2026-10-30T15:05:59.999999-03:00", "FUTURE_EXTERNAL_PIT_SEQUENCE_INVALID"),
    ("2026-10-30T15:06:00-03:00", None),
    ("not-a-clock", "FUTURE_EVIDENCE_AS_OF_INVALID"),
])
def test_historical_point_in_time_cutoffs_are_explicit(at, reason):
    reference, source_bytes = _reference()
    result = review_dlr_external_reference(
        _row(), reference, source_bytes=source_bytes, as_of=at)
    assert result.reason_code == (reason or "FUTURE_EXTERNAL_AUTHENTICATION_AND_CLEARING_REQUIRED")
    assert not result.paper_settlement_authorized


@pytest.mark.parametrize("symbol", [
    "DLR/NOV26M", "DLR/OCT26-DLR/NOV26", "DLR/ENE27", "DLR/OCT2026", "MERV/OCT26",
])
def test_future_unknown_series_cannot_be_promoted_by_claimed_external_receipt(symbol):
    reference, source_bytes = _reference()
    reference["identity"]["ticker"] = symbol
    result = review_dlr_external_reference(
        _row(symbol), reference, source_bytes=source_bytes, as_of=REPLAY_AT)
    assert result.reason_code == "FUTURE_SERIES_OR_PPI_IDENTITY_UNVERIFIED"
    assert result.status == "BLOQUEADO"


def test_unsupported_series_even_with_future_expiry_is_never_open_session_authority():
    unknown = InstrumentContract(
        "DLR/ENE27", "FUTUROS", "ARS", "A3", "INMEDIATA",
        Decimal("1000"), Decimal("1"), "SYNTHETIC_UNSUPPORTED_2027",
        expires_at="2027-01-29T15:00:00-03:00", minimum_quantity=Decimal("1"),
        paper_margin_policy="CONSERVATIVE_NOTIONAL_RATE", paper_margin_rate=Decimal("1"),
        underlying="DOLAR_A3500")
    at = "2026-11-02T11:00:00-03:00"  # Monday OPEN-shaped instant, not live market evidence
    assert paper_session_state(at, unknown) == "FUTURE_SERIES_CONTRACT_NOT_REVIEWED"
    assert admission_error(at, unknown) == "FUTURE_SERIES_CONTRACT_NOT_REVIEWED"
    assert admission_error(at, None) == "FUTURE_EXACT_CONTRACT_REQUIRED"


def _listed_future(symbol):
    terms = standard_dlr_terms(symbol)
    return InstrumentContract(
        symbol, "FUTUROS", "ARS", "A3", "INMEDIATA",
        Decimal("1000"), Decimal("1"), "SYNTHETIC_EXACT_2026_DLR",
        expires_at=terms["expires_at"], minimum_quantity=Decimal("1"),
        paper_margin_policy="CONSERVATIVE_NOTIONAL_RATE", paper_margin_rate=Decimal("1"),
        underlying="DOLAR_A3500")


@pytest.mark.parametrize("at,symbol", [
    ("2026-10-12T11:00:00-03:00", "DLR/OCT26"),
    ("2026-11-09T11:00:00-03:00", "DLR/NOV26"),
    ("2026-11-10T11:00:00-03:00", "DLR/NOV26"),
    ("2026-11-23T11:00:00-03:00", "DLR/NOV26"),
    ("2026-12-07T11:00:00-03:00", "DLR/DIC26"),
    ("2026-12-08T11:00:00-03:00", "DLR/DIC26"),
    ("2026-12-25T11:00:00-03:00", "DLR/DIC26"),
    ("2026-12-25T14:00:00Z", "DLR/DIC26"),
    ("2026-12-26T11:00:00-03:00", "DLR/DIC26"),
])
def test_official_bank_holiday_floor_and_weekend_replay_never_admits_dlr(at, symbol):
    # BCRA 2026 calendar is a conservative NO-ENTRY floor, not authentication
    # of A3 trading status. Simulated market hours alone cannot reopen it.
    contract = _listed_future(symbol)
    assert paper_session_state(at, contract) == "MARKET_CLOSED_OR_CALENDAR_UNKNOWN"
    assert admission_error(at, contract) == "MARKET_CLOSED_OR_CALENDAR_UNKNOWN"


@pytest.mark.parametrize("at,symbol", [
    ("2026-10-05T11:00:00-03:00", "DLR/OCT26"),
    ("2026-11-02T11:00:00-03:00", "DLR/NOV26"),
    ("2026-12-09T11:00:00-03:00", "DLR/DIC26"),
])
def test_nonholiday_weekday_keeps_research_window_without_certifying_market_open(at, symbol):
    # The empty clock error only removes *this* calendar veto. Live PPI book,
    # operator receipt, contract and PAPER/0 remain independent locked gates.
    contract = _listed_future(symbol)
    assert paper_session_state(at, contract) == ""
    assert admission_error(at, contract) == ""
