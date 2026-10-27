"""Offline PAPER accounting for explicit fixed-income economic events.

This reducer consumes a position seed and *confirmed* event amounts. It does
not discover issuer terms, certify a source document, infer a coupon from a
rate, translate currencies, or write to the runtime PAPER database. A caller
must independently verify the position snapshot and the referenced evidence
before any eventual integration. IOL's same-provider schedule reference is
not a settlement receipt. No entry signal or broker route lives here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from bs_instrument_contracts import (aware_datetime, cash_currency, decimal_value,
                                     family_name, utc_microseconds)


PAPER_ONLY = True
ENTRY_AUTHORITY = False
REAL_ROUTES_USED = ()
_FIXED_INCOME = frozenset({"BONOS", "LETRAS", "OBLIGACIONES"})
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class PositionSeed:
    paper_id: str
    symbol: str
    family: str
    market: str
    settlement: str
    trading_currency: str
    initial_nominal: object
    nominal_unit: str
    opened_at: str
    position_reference: str
    position_sha256: str


@dataclass(frozen=True)
class EconomicEvent:
    event_id: str
    obligation_id: str
    paper_id: str
    kind: str
    # Economic date can precede publication of a confirmed payment/fixing.
    effective_at: str
    # Local observation/booking time controls point-in-time visibility.
    recorded_at: str
    source_kind: str
    source_reference: str
    source_sha256: str
    source_at: str
    # Cash is explicit in the payment currency. No pricing or FX is derived.
    payment_currency: str | None = None
    cash_amount: object | None = None
    nominal_base: object | None = None
    nominal_redeemed: object | None = None
    # A fixing is recorded without changing cash or residual nominal.
    fixing_code: str | None = None
    fixing_value: object | None = None
    fixing_unit: str | None = None


@dataclass(frozen=True)
class CashEntry:
    event_id: str
    obligation_id: str
    kind: str
    currency: str
    amount: Decimal
    residual_nominal: Decimal
    effective_at: str
    recorded_at: str
    source_at: str
    source_reference: str
    source_sha256: str


@dataclass(frozen=True)
class FixingEntry:
    event_id: str
    obligation_id: str
    code: str
    effective_at: str
    recorded_at: str
    value: Decimal
    unit: str
    source_at: str
    source_reference: str
    source_sha256: str


@dataclass(frozen=True)
class LedgerSnapshot:
    paper_id: str
    symbol: str
    family: str
    trading_currency: str
    position_sha256: str
    initial_nominal: Decimal
    residual_nominal: Decimal
    cash_by_currency: tuple[tuple[str, Decimal], ...]
    cash_entries: tuple[CashEntry, ...]
    fixings: tuple[FixingEntry, ...]
    applied_event_ids: tuple[str, ...]
    paper_only: bool = True
    entry_authority: bool = False
    real_routes_used: tuple = ()


def _required(value, field):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"FIXED_INCOME_{field}_REQUIRED")
    return value


def _digest(value, field):
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError(f"FIXED_INCOME_{field}_INVALID")
    return value


def _amount(value, field, *, positive=False):
    try:
        return decimal_value(value, field, positive=positive, nonnegative=not positive)
    except ValueError as exc:
        raise ValueError(f"FIXED_INCOME_{field}_INVALID") from exc


def _absent(event, *fields):
    if any(getattr(event, field) is not None for field in fields):
        raise ValueError("FIXED_INCOME_EVENT_FIELDS_CONFLICT")


def _instant(value, field):
    try:
        result = utc_microseconds(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"FIXED_INCOME_{field}_INVALID") from exc
    if result is None:
        raise ValueError(f"FIXED_INCOME_{field}_INVALID")
    return result


def reduce_events(position: PositionSeed, events: Iterable[EconomicEvent], *, as_of) -> LedgerSnapshot:
    """Fold a complete ordered event history; exact replays do not pay twice.

    Distinct receipt IDs for the same contractual obligation fail closed. New
    economic events must be observation-time ordered; a replay can occur at any point
    in the input stream. ``effective_at`` is the economic date; ``source_at``
    is evidence publication, and ``recorded_at`` is local knowledge. A record
    unknown by ``as_of`` raises rather than contributing future cash. Each
    invocation recomputes the immutable snapshot.
    """
    if not isinstance(position, PositionSeed):
        raise ValueError("FIXED_INCOME_POSITION_REQUIRED")
    family = family_name(position.family)
    if family not in _FIXED_INCOME:
        raise ValueError("FIXED_INCOME_FAMILY_UNSUPPORTED")
    for name in ("paper_id", "symbol", "market", "settlement", "position_reference"):
        _required(getattr(position, name), name.upper())
    cash_currency(position.trading_currency)
    _digest(position.position_sha256, "POSITION_SHA256")
    if position.nominal_unit != "VN":
        raise ValueError("FIXED_INCOME_VN_UNIT_REQUIRED")
    initial = _amount(position.initial_nominal, "INITIAL_NOMINAL", positive=True)
    opened_us = _instant(position.opened_at, "OPENED_AT")
    as_of_us = _instant(as_of, "AS_OF")
    if opened_us > as_of_us:
        raise ValueError("FIXED_INCOME_POSITION_IN_FUTURE")

    residual = initial
    cash: dict[str, Decimal] = {}
    cash_entries: list[CashEntry] = []
    fixings: list[FixingEntry] = []
    seen_ids: dict[str, tuple] = {}
    seen_obligations: set[tuple[str, str]] = set()
    seen_fixing_keys: set[tuple[str, int]] = set()
    last_new_us = opened_us
    for event in events:
        if not isinstance(event, EconomicEvent):
            raise ValueError("FIXED_INCOME_EVENT_REQUIRED")
        event_id = _required(event.event_id, "EVENT_ID")
        obligation_id = _required(event.obligation_id, "OBLIGATION_ID")
        if event.paper_id != position.paper_id:
            raise ValueError("FIXED_INCOME_POSITION_MISMATCH")
        kind = event.kind
        if kind not in {"COUPON", "AMORTIZATION", "FIXING"}:
            raise ValueError("FIXED_INCOME_EVENT_KIND_UNSUPPORTED")
        effective_us = _instant(event.effective_at, "EFFECTIVE_AT")
        recorded_us = _instant(event.recorded_at, "RECORDED_AT")
        source_us = _instant(event.source_at, "SOURCE_AT")
        # A historical payment or fixing may be observed later. Neither
        # future economic dates nor records received after as_of enter cash.
        if not effective_us <= source_us <= recorded_us <= as_of_us:
            raise ValueError("FIXED_INCOME_EVENT_CLOCK_INVALID")
        source_reference = _required(event.source_reference, "SOURCE_REFERENCE")
        source_sha = _digest(event.source_sha256, "SOURCE_SHA256")
        if kind == "FIXING":
            if event.source_kind != "OFFICIAL_FIXING":
                raise ValueError("FIXED_INCOME_FIXING_SOURCE_REQUIRED")
            _absent(event, "payment_currency", "cash_amount", "nominal_base", "nominal_redeemed")
            code = _required(event.fixing_code, "FIXING_CODE")
            unit = _required(event.fixing_unit, "FIXING_UNIT")
            try:
                fixing_value = decimal_value(event.fixing_value, "fixing value")
            except ValueError as exc:
                raise ValueError("FIXED_INCOME_FIXING_VALUE_INVALID") from exc
            fingerprint = (event.paper_id, obligation_id, kind, effective_us, recorded_us, source_us,
                           event.source_kind, source_reference, source_sha, code,
                           fixing_value, unit)
        else:
            if effective_us < opened_us:
                raise ValueError("FIXED_INCOME_PAYMENT_BEFORE_POSITION")
            if event.source_kind != "SETTLEMENT_PROOF":
                raise ValueError("FIXED_INCOME_SETTLEMENT_PROOF_REQUIRED")
            _absent(event, "fixing_code", "fixing_value", "fixing_unit")
            currency = cash_currency(event.payment_currency)
            amount = _amount(event.cash_amount, "CASH_AMOUNT", positive=kind == "COUPON")
            nominal_base = _amount(event.nominal_base, "NOMINAL_BASE", positive=True)
            redeemed = (_amount(event.nominal_redeemed, "NOMINAL_REDEEMED", positive=True)
                        if kind == "AMORTIZATION" else None)
            if kind == "COUPON":
                _absent(event, "nominal_redeemed")
            fingerprint = (event.paper_id, obligation_id, kind, effective_us, recorded_us, source_us,
                           event.source_kind, source_reference, source_sha, currency, amount,
                           nominal_base, redeemed)
        if event_id in seen_ids:
            if seen_ids[event_id] != fingerprint:
                raise ValueError("FIXED_INCOME_EVENT_ID_COLLISION")
            continue
        economic_key = (kind, obligation_id)
        if economic_key in seen_obligations:
            raise ValueError("FIXED_INCOME_OBLIGATION_REPLAY_CONFLICT")
        if kind == "FIXING" and (code, effective_us) in seen_fixing_keys:
            raise ValueError("FIXED_INCOME_FIXING_CONFLICT")
        if recorded_us < last_new_us:
            raise ValueError("FIXED_INCOME_EVENT_CLOCK_ROLLBACK")
        if kind != "FIXING":
            if nominal_base != residual:
                raise ValueError("FIXED_INCOME_NOMINAL_BASE_MISMATCH")
            if kind == "AMORTIZATION":
                if redeemed > residual:
                    raise ValueError("FIXED_INCOME_OVER_AMORTIZATION")
                residual -= redeemed
            cash[currency] = cash.get(currency, Decimal(0)) + amount
            cash_entries.append(CashEntry(event_id, obligation_id, kind, currency,
                                          amount, residual, aware_datetime(event.effective_at).isoformat(),
                                          aware_datetime(event.recorded_at).isoformat(),
                                          aware_datetime(event.source_at).isoformat(),
                                          source_reference, source_sha))
        else:
            fixings.append(FixingEntry(event_id, obligation_id, code,
                                       aware_datetime(event.effective_at).isoformat(),
                                       aware_datetime(event.recorded_at).isoformat(),
                                       fixing_value, unit, aware_datetime(event.source_at).isoformat(),
                                       source_reference, source_sha))
        seen_ids[event_id] = fingerprint
        seen_obligations.add(economic_key)
        if kind == "FIXING":
            seen_fixing_keys.add((code, effective_us))
        last_new_us = recorded_us
    return LedgerSnapshot(position.paper_id, position.symbol, family,
                          cash_currency(position.trading_currency), position.position_sha256,
                          initial, residual, tuple(sorted(cash.items())),
                          tuple(cash_entries), tuple(fixings), tuple(seen_ids))
