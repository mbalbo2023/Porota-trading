"""End-of-day caucion cash-sweep planner for HF6 PAPER.

The planner never talks to PPI and never sends an order. It consumes already
verified CaucionOffer objects plus verified schedule/liquidity deadlines.
Execution remains a separate PAPER action behind the normal safety gates.

Policy:
- only settled/free cash of the same currency is considered;
- required reserve/commitments are removed before sizing;
- quote, minimum, step, depth, costs, maturity and schedule must be verified;
- maturity must not violate the requested liquidity deadline;
- non-positive net return is rejected;
- selection maximizes net profit among candidates that all satisfy liquidity;
- missing schedule evidence is FAIL_CLOSED.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_DOWN

from bs_instrument_contracts import aware_datetime, cash_currency, decimal_value
from bt_caucion_paper import CaucionOffer, money

ZERO = Decimal("0")


@dataclass(frozen=True)
class SweepPlan:
    state: str
    reason: str
    currency: str
    evaluated_at: str
    available_cash: Decimal
    required_reserve: Decimal
    sweep_budget: Decimal
    instrument_id: str | None = None
    principal: Decimal = ZERO
    cash_debit: Decimal = ZERO
    gross_interest: Decimal = ZERO
    total_fees: Decimal = ZERO
    net_profit: Decimal = ZERO
    maturity_at: str | None = None
    quote_at: str | None = None
    schedule_source: str | None = None


def _floor_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def plan_sweep(*, offers, currency, as_of, available_cash, required_reserve,
               sweep_start_at, order_cutoff_at, liquidity_deadline,
               schedule_source, participation=Decimal("0.10"),
               max_quote_age_seconds=60, maximum_principal=None) -> SweepPlan:
    at = aware_datetime(as_of)
    ccy = cash_currency(currency)
    cash = decimal_value(available_cash, "caja liquidada", nonnegative=True)
    reserve = decimal_value(required_reserve, "reserva requerida", nonnegative=True)
    participation = decimal_value(participation, "participación", positive=True)
    quote_age = decimal_value(max_quote_age_seconds, "antigüedad máxima", nonnegative=True)
    if participation > 1:
        raise ValueError("participación fuera de rango")
    if not str(schedule_source or "").strip():
        return SweepPlan("FAIL_CLOSED", "SCHEDULE_SOURCE_MISSING", ccy, at.isoformat(), cash,
                         reserve, ZERO)
    try:
        start = aware_datetime(sweep_start_at, "inicio de sweep")
        cutoff = aware_datetime(order_cutoff_at, "cutoff de caución")
        deadline = aware_datetime(liquidity_deadline, "deadline de liquidez")
    except (ValueError, TypeError):
        return SweepPlan("FAIL_CLOSED", "SCHEDULE_EVIDENCE_INVALID", ccy, at.isoformat(), cash,
                         reserve, ZERO, schedule_source=str(schedule_source))
    if not start < cutoff <= deadline:
        return SweepPlan("FAIL_CLOSED", "SCHEDULE_ORDER_INVALID", ccy, at.isoformat(), cash,
                         reserve, ZERO, schedule_source=str(schedule_source))
    budget = max(ZERO, cash - reserve)
    if at < start:
        return SweepPlan("WAITING_WINDOW", "SWEEP_WINDOW_NOT_OPEN", ccy, at.isoformat(), cash,
                         reserve, budget, schedule_source=str(schedule_source))
    if at >= cutoff:
        return SweepPlan("CLOSED", "CAUCION_ORDER_CUTOFF_REACHED", ccy, at.isoformat(), cash,
                         reserve, budget, schedule_source=str(schedule_source))
    if budget <= ZERO:
        return SweepPlan("NO_ACTION", "NO_FREE_SETTLED_CASH_AFTER_RESERVE", ccy, at.isoformat(),
                         cash, reserve, budget, schedule_source=str(schedule_source))

    max_principal = (None if maximum_principal is None else
                     decimal_value(maximum_principal, "capital máximo", positive=True))
    candidates = []
    for offer in offers:
        if not isinstance(offer, CaucionOffer) or offer.currency != ccy:
            continue
        try:
            quoted = aware_datetime(offer.quoted_at, "cotización")
            maturity = aware_datetime(offer.maturity_at, "vencimiento")
            if quoted > at or Decimal(str((at - quoted).total_seconds())) > quote_age:
                continue
            if maturity > deadline or maturity <= at:
                continue
            depth_cap = offer.available_principal * participation
            raw_cap = min(budget, depth_cap)
            if max_principal is not None:
                raw_cap = min(raw_cap, max_principal)
            principal = money(_floor_step(raw_cap, offer.principal_step))
            if principal < offer.minimum_principal or principal <= ZERO:
                continue
            interest, fees, net = offer.economics(principal)
            if net <= ZERO:
                continue
            debit = principal + (fees if offer.fee_payment == "UPFRONT" else ZERO)
            if debit > budget:
                # Re-floor once accounting for an upfront fee. Do not iterate by
                # cents or invent a fee curve; exact quoted fee contracts are
                # evaluated by CaucionOffer.economics().
                principal = money(_floor_step(max(ZERO, budget - fees), offer.principal_step))
                if principal < offer.minimum_principal:
                    continue
                interest, fees, net = offer.economics(principal)
                debit = principal + (fees if offer.fee_payment == "UPFRONT" else ZERO)
                if debit > budget or net <= ZERO:
                    continue
            candidates.append((net, -maturity.timestamp(), -debit, offer.instrument_id,
                               offer, principal, debit, interest, fees))
        except (ValueError, TypeError, ArithmeticError):
            continue

    if not candidates:
        return SweepPlan("NO_ACTION", "NO_VERIFIED_POSITIVE_NET_CAUCION_FITS_LIQUIDITY", ccy,
                         at.isoformat(), cash, reserve, budget,
                         schedule_source=str(schedule_source))

    # Net profit first. All candidates already satisfy the same liquidity
    # deadline; ties prefer earlier maturity and then lower cash debit.
    _, _, _, _, offer, principal, debit, interest, fees = max(candidates)
    return SweepPlan(
        state="PAPER_CANDIDATE",
        reason="VERIFIED_END_OF_DAY_CASH_SWEEP_CANDIDATE",
        currency=ccy,
        evaluated_at=at.isoformat(),
        available_cash=cash,
        required_reserve=reserve,
        sweep_budget=budget,
        instrument_id=offer.instrument_id,
        principal=principal,
        cash_debit=debit,
        gross_interest=interest,
        total_fees=fees,
        net_profit=interest-fees,
        maturity_at=offer.maturity_at,
        quote_at=offer.quoted_at,
        schedule_source=str(schedule_source),
    )
