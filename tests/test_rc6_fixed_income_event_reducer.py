"""Economic event accounting remains isolated from runtime entry authority."""
from dataclasses import replace
from decimal import Decimal

import pytest

import rc6_fixed_income_event_reducer as ledger


HASH = "a" * 64
T0 = "2026-10-27T10:00:00-03:00"
T1 = "2026-10-27T12:00:00-03:00"
T1_PROOF = "2026-10-27T12:05:00-03:00"
T2 = "2026-10-27T13:00:00-03:00"
T2_PROOF = "2026-10-27T13:05:00-03:00"
T3 = "2026-10-27T14:00:00-03:00"
AS_OF = "2026-10-27T18:00:00Z"


def position(**changes):
    return replace(ledger.PositionSeed(
        paper_id="paper-42", symbol="FIC", family="BONOS", market="BYMA",
        settlement="A-24HS", trading_currency="ARS", initial_nominal="100",
        nominal_unit="VN",
        opened_at=T0, position_reference="paper-fill:42", position_sha256=HASH), **changes)


def payment(kind, event_id, obligation_id, *, at=T2, **changes):
    return replace(ledger.EconomicEvent(
        event_id=event_id, obligation_id=obligation_id, paper_id="paper-42",
        kind=kind, effective_at=T1, recorded_at=at, source_kind="SETTLEMENT_PROOF",
        source_reference=f"fixture:confirmed:{obligation_id}", source_sha256=HASH,
        source_at=T1_PROOF, payment_currency="USD", cash_amount="2.50",
        nominal_base="100", nominal_redeemed="30" if kind == "AMORTIZATION" else None),
        **changes)


def fixing(**changes):
    return replace(ledger.EconomicEvent(
        event_id="fix-1", obligation_id="idx-2026-10-27", paper_id="paper-42",
        kind="FIXING", effective_at=T0, recorded_at=T2, source_kind="OFFICIAL_FIXING",
        source_reference="fixture:official:fixing", source_sha256=HASH,
        source_at=T1, fixing_code="A3500",
        fixing_value="1420.5", fixing_unit="ARS_PER_USD"), **changes)


@pytest.mark.parametrize("family", ["BONOS", "LETRAS", "OBLIGACIONES", "ON"])
def test_explicit_coupon_amortization_and_fixing_separate_trade_and_payment_currency(family):
    events = [fixing(), payment("COUPON", "coupon-1", "p1-interest", at=T2),
              payment("AMORTIZATION", "principal-1", "p1-principal", at=T2),
              payment("COUPON", "coupon-2", "p2-interest", at=T3,
                      effective_at=T2, source_at=T2_PROOF, nominal_base="70",
                      cash_amount="1.75", payment_currency="USD_MEP")]
    result = ledger.reduce_events(position(family=family), events, as_of=AS_OF)
    assert result.initial_nominal == Decimal("100")
    assert (result.family, result.trading_currency, result.symbol) == (
        "OBLIGACIONES" if family == "ON" else family, "ARS", "FIC")
    assert result.residual_nominal == Decimal("70")
    assert result.cash_by_currency == (("USD", Decimal("5.00")),
                                       ("USD_MEP", Decimal("1.75")))
    assert tuple(e.residual_nominal for e in result.cash_entries) == (
        Decimal("100"), Decimal("70"), Decimal("70"))
    assert result.fixings[0].value == Decimal("1420.5")
    assert result.cash_entries[0].effective_at == T1
    assert result.cash_entries[0].recorded_at == T2
    assert result.cash_entries[0].source_at == T1_PROOF
    assert result.paper_only and result.entry_authority is False
    assert result.real_routes_used == ledger.REAL_ROUTES_USED == ()


def test_replaying_before_and_after_later_events_is_idempotent_with_numeric_canonicalization():
    first = payment("AMORTIZATION", "principal-1", "p1-principal")
    next_coupon = payment("COUPON", "coupon-2", "p2-interest", at=T3,
                          effective_at=T2, source_at=T2_PROOF,
                          nominal_base="70", cash_amount="0.5")
    receipt_stream = [first, next_coupon, replace(first, cash_amount=Decimal("2.500"))]
    once = ledger.reduce_events(position(), [first, next_coupon], as_of=AS_OF)
    twice = ledger.reduce_events(position(), receipt_stream, as_of=AS_OF)
    assert twice == once
    assert len(twice.cash_entries) == 2


def test_receipt_collision_and_new_receipt_for_same_obligation_fail_closed():
    event = payment("COUPON", "coupon-1", "p1-interest")
    for second, code in (
        (replace(event, cash_amount="2.51"), "FIXED_INCOME_EVENT_ID_COLLISION"),
        (replace(event, event_id="coupon-1-again"), "FIXED_INCOME_OBLIGATION_REPLAY_CONFLICT"),
        (replace(event, event_id="principal-1", kind="AMORTIZATION",
                 nominal_redeemed="20", obligation_id="p1-principal"), None),
    ):
        if code:
            with pytest.raises(ValueError, match=code):
                ledger.reduce_events(position(), [event, second], as_of=AS_OF)
        else:
            assert len(ledger.reduce_events(position(), [event, second], as_of=AS_OF).cash_entries) == 2


@pytest.mark.parametrize("change,reason", [
    ({"nominal_redeemed": "101"}, "OVER_AMORTIZATION"),
    ({"nominal_base": "99"}, "NOMINAL_BASE_MISMATCH"),
    ({"nominal_redeemed": "NaN"}, "NOMINAL_REDEEMED_INVALID"),
    ({"cash_amount": "Infinity"}, "CASH_AMOUNT_INVALID"),
    ({"payment_currency": "UNKNOWN"}, "Moneda/plaza no reconocida"),
    ({"source_kind": "IOL_SAME_PROVIDER_REFERENCE_ONLY"}, "SETTLEMENT_PROOF_REQUIRED"),
    ({"source_kind": "ISSUER_TERMS"}, "SETTLEMENT_PROOF_REQUIRED"),
    ({"source_reference": ""}, "SOURCE_REFERENCE_REQUIRED"),
    ({"source_sha256": "not-a-digest"}, "SOURCE_SHA256_INVALID"),
    ({"effective_at": "2026-10-28T12:00:00-03:00"}, "EVENT_CLOCK_INVALID"),
    ({"recorded_at": "2026-10-28T12:00:00-03:00"}, "EVENT_CLOCK_INVALID"),
    ({"recorded_at": "2026-10-27T12:00:00"}, "RECORDED_AT_INVALID"),
    ({"source_at": "2026-10-27T11:00:00-03:00"}, "EVENT_CLOCK_INVALID"),
    ({"source_at": None}, "SOURCE_AT_INVALID"),
    ({"paper_id": "another"}, "POSITION_MISMATCH"),
])
def test_economic_event_rejects_unverified_units_provenance_and_future_cash(change, reason):
    with pytest.raises(ValueError, match=reason):
        ledger.reduce_events(position(), [payment("AMORTIZATION", "p1", "p1", **change)],
                             as_of=AS_OF)


def test_no_implicit_cash_from_fixing_or_remaining_principal_and_no_clock_rollback():
    value = ledger.reduce_events(position(), [fixing()], as_of=AS_OF)
    assert value.residual_nominal == Decimal("100")
    assert value.cash_by_currency == ()
    assert value.cash_entries == ()
    with pytest.raises(ValueError, match="CLOCK_ROLLBACK"):
        ledger.reduce_events(position(), [payment("COUPON", "new", "p1", at=T3),
                                          payment("COUPON", "old", "p2", at=T2)], as_of=AS_OF)


def test_late_settlement_receipt_has_economic_date_without_premature_pit_cash():
    late = payment("COUPON", "late-coupon", "period-interest", at=T3,
                   effective_at=T1, source_at=T2_PROOF)
    for as_of in ("2026-10-27T12:01:00-03:00", "2026-10-27T13:10:00-03:00"):
        with pytest.raises(ValueError, match="EVENT_CLOCK_INVALID"):
            ledger.reduce_events(position(), [late], as_of=as_of)
    with pytest.raises(ValueError, match="EVENT_CLOCK_INVALID"):
        ledger.reduce_events(position(), [replace(late, recorded_at=T2)], as_of=AS_OF)
    result = ledger.reduce_events(position(), [late], as_of=T3)
    assert result.cash_by_currency == (("USD", Decimal("2.50")),)
    assert result.cash_entries[0].effective_at == T1
    assert result.cash_entries[0].recorded_at == T3
    assert result.cash_entries[0].source_at == T2_PROOF
    assert result.cash_entries[0].source_reference == "fixture:confirmed:period-interest"
    assert result.cash_entries[0].source_sha256 == HASH


def test_cash_cannot_predate_paper_holding_even_with_later_proof():
    with pytest.raises(ValueError, match="PAYMENT_BEFORE_POSITION"):
        ledger.reduce_events(position(), [payment(
            "AMORTIZATION", "preholding", "preholding", effective_at="2026-10-26T12:00:00-03:00")],
            as_of=AS_OF)


def test_post_amortization_coupon_must_use_residual_nominal():
    principal = payment("AMORTIZATION", "principal-1", "p1-principal")
    bad_coupon = payment("COUPON", "coupon-2", "p2-interest", at=T2)
    with pytest.raises(ValueError, match="NOMINAL_BASE_MISMATCH"):
        ledger.reduce_events(position(), [principal, bad_coupon], as_of=AS_OF)
    final = payment("AMORTIZATION", "principal-2", "p2-principal", at=T2,
                    nominal_base="70", nominal_redeemed="70", cash_amount="70")
    result = ledger.reduce_events(position(), [principal, final], as_of=AS_OF)
    assert result.residual_nominal == 0


def test_same_day_interest_and_principal_require_explicit_order_and_base():
    principal = payment("AMORTIZATION", "principal", "period-principal")
    coupon = payment("COUPON", "coupon", "period-interest")
    assert ledger.reduce_events(position(), [coupon, principal], as_of=AS_OF).residual_nominal == 70
    with pytest.raises(ValueError, match="NOMINAL_BASE_MISMATCH"):
        ledger.reduce_events(position(), [principal, coupon], as_of=AS_OF)


def test_fixing_requires_official_attestation_and_has_no_cash_fields():
    for changes, code in (
        ({"source_kind": "IOL_SIMULATION"}, "FIXING_SOURCE_REQUIRED"),
        ({"cash_amount": "99", "payment_currency": "ARS"}, "EVENT_FIELDS_CONFLICT"),
        ({"effective_at": "2026-10-27T14:00:00-03:00"}, "EVENT_CLOCK_INVALID"),
        ({"fixing_value": "NaN"}, "FIXING_VALUE_INVALID"),
    ):
        with pytest.raises(ValueError, match=code):
            ledger.reduce_events(position(), [fixing(**changes)], as_of=AS_OF)


def test_same_index_and_effective_time_with_another_receipt_is_ambiguous():
    original = fixing()
    conflicting = replace(original, event_id="fix-2", obligation_id="revised-index",
                          fixing_value="1421")
    with pytest.raises(ValueError, match="FIXING_CONFLICT"):
        ledger.reduce_events(position(), [original, conflicting], as_of=AS_OF)


def test_position_seed_requires_identity_digest_and_positive_nominal():
    for changes, code in (
        ({"family": "ACCIONES"}, "FAMILY_UNSUPPORTED"),
        ({"position_sha256": ""}, "POSITION_SHA256_INVALID"),
        ({"initial_nominal": "0"}, "INITIAL_NOMINAL_INVALID"),
        ({"nominal_unit": "LOT"}, "VN_UNIT_REQUIRED"),
        ({"opened_at": "2026-10-28T10:00:00-03:00"}, "POSITION_IN_FUTURE"),
    ):
        with pytest.raises(ValueError, match=code):
            ledger.reduce_events(position(**changes), [], as_of=AS_OF)
