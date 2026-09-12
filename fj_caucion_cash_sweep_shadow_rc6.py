"""RC6 shadow-only end-of-day caucion cash-sweep policy.

This layer does not route orders and does not touch positions. It derives a
conservative treasury window from an externally verified caucion cutoff and
feeds only verified evidence into the existing pure planner.

The lead time is an operator policy, not an exchange schedule. The actual
cutoff, settlement/liquidity deadline and their provenance must be supplied by
verified sources. Missing calendar/schedule evidence fails closed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from bs_instrument_contracts import aware_datetime, cash_currency
from bt_caucion_paper import CaucionOffer
from df_caucion_end_of_day_sweep_hf6 import plan_sweep


SHADOW_MODE = "SHADOW_ONLY"
DEFAULT_POLICY_LEAD_MINUTES = 20
MIN_POLICY_LEAD_MINUTES = 10
MAX_POLICY_LEAD_MINUTES = 45


@dataclass(frozen=True)
class ShadowSweepDecision:
    mode: str
    state: str
    reason: str
    currency: str
    evaluated_at: str
    sweep_start_at: str | None
    order_cutoff_at: str | None
    liquidity_deadline: str | None
    schedule_source: str | None
    policy_lead_minutes: int
    available_cash: str
    required_reserve: str
    sweep_budget: str
    instrument_id: str | None
    principal: str
    net_profit: str
    real_execution_allowed: bool

    def as_dict(self):
        return asdict(self)


def _money(value, label):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(label + "_INVALID") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(label + "_INVALID")
    return amount


def _decision(*, state, reason, currency, at, lead, cash, reserve,
              start=None, cutoff=None, deadline=None, source=None,
              budget=Decimal("0"), instrument_id=None,
              principal=Decimal("0"), net_profit=Decimal("0")):
    return ShadowSweepDecision(
        mode=SHADOW_MODE,
        state=state,
        reason=reason,
        currency=currency,
        evaluated_at=at.isoformat(),
        sweep_start_at=None if start is None else start.isoformat(),
        order_cutoff_at=None if cutoff is None else cutoff.isoformat(),
        liquidity_deadline=None if deadline is None else deadline.isoformat(),
        schedule_source=None if source is None else str(source),
        policy_lead_minutes=lead,
        available_cash=str(cash),
        required_reserve=str(reserve),
        sweep_budget=str(budget),
        instrument_id=instrument_id,
        principal=str(principal),
        net_profit=str(net_profit),
        real_execution_allowed=False,
    )


def shadow_cash_sweep(
    offers,
    *,
    currency,
    as_of,
    available_cash,
    required_reserve,
    order_cutoff_at,
    liquidity_deadline,
    schedule_source,
    settlement_calendar_verified,
    already_swept_today=False,
    policy_lead_minutes=DEFAULT_POLICY_LEAD_MINUTES,
    participation=Decimal("0.10"),
    max_quote_age_seconds=30,
):
    """Return what the PAPER sweep *would* do, with no execution side effect."""
    ccy = cash_currency(currency)
    at = aware_datetime(as_of)
    cash = _money(available_cash, "AVAILABLE_CASH")
    reserve = _money(required_reserve, "REQUIRED_RESERVE")
    try:
        lead = int(policy_lead_minutes)
    except (TypeError, ValueError) as exc:
        raise ValueError("POLICY_LEAD_MINUTES_INVALID") from exc
    if not MIN_POLICY_LEAD_MINUTES <= lead <= MAX_POLICY_LEAD_MINUTES:
        raise ValueError("POLICY_LEAD_MINUTES_OUT_OF_RANGE")
    source = str(schedule_source or "").strip()
    if not source:
        return _decision(
            state="FAIL_CLOSED", reason="SCHEDULE_SOURCE_MISSING", currency=ccy,
            at=at, lead=lead, cash=cash, reserve=reserve)
    if not settlement_calendar_verified:
        return _decision(
            state="FAIL_CLOSED", reason="SETTLEMENT_CALENDAR_UNVERIFIED", currency=ccy,
            at=at, lead=lead, cash=cash, reserve=reserve, source=source)
    try:
        cutoff = aware_datetime(order_cutoff_at, "cutoff caucion")
        deadline = aware_datetime(liquidity_deadline, "deadline liquidez")
    except (ValueError, TypeError):
        return _decision(
            state="FAIL_CLOSED", reason="SCHEDULE_EVIDENCE_INVALID", currency=ccy,
            at=at, lead=lead, cash=cash, reserve=reserve, source=source)
    start = cutoff - timedelta(minutes=lead)
    if not start < cutoff <= deadline:
        return _decision(
            state="FAIL_CLOSED", reason="SCHEDULE_ORDER_INVALID", currency=ccy,
            at=at, lead=lead, cash=cash, reserve=reserve,
            start=start, cutoff=cutoff, deadline=deadline, source=source)
    if already_swept_today:
        return _decision(
            state="NO_ACTION", reason="ALREADY_SWEPT_TODAY", currency=ccy,
            at=at, lead=lead, cash=cash, reserve=reserve,
            start=start, cutoff=cutoff, deadline=deadline, source=source,
            budget=max(Decimal("0"), cash-reserve))

    verified = [
        offer for offer in (offers or ())
        if isinstance(offer, CaucionOffer)
        and offer.quoted_total_fees is not None
        and offer.fee_quote_principal is not None
    ]
    if not verified:
        return _decision(
            state="HOLD", reason="EXACT_FEE_BUDGET_MISSING", currency=ccy,
            at=at, lead=lead, cash=cash, reserve=reserve,
            start=start, cutoff=cutoff, deadline=deadline, source=source,
            budget=max(Decimal("0"), cash-reserve))

    plan = plan_sweep(
        offers=verified,
        currency=ccy,
        as_of=at,
        available_cash=cash,
        required_reserve=reserve,
        sweep_start_at=start,
        order_cutoff_at=cutoff,
        liquidity_deadline=deadline,
        schedule_source=source,
        participation=participation,
        max_quote_age_seconds=max_quote_age_seconds,
    )
    state = "SHADOW_CANDIDATE" if plan.state == "PAPER_CANDIDATE" else plan.state
    return _decision(
        state=state,
        reason=plan.reason,
        currency=ccy,
        at=at,
        lead=lead,
        cash=cash,
        reserve=reserve,
        start=start,
        cutoff=cutoff,
        deadline=deadline,
        source=source,
        budget=plan.sweep_budget,
        instrument_id=plan.instrument_id,
        principal=plan.principal,
        net_profit=plan.net_profit,
    )


def assert_shadow_only() -> None:
    assert SHADOW_MODE == "SHADOW_ONLY"
