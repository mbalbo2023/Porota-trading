"""HF6-v2 end-of-day caucion cash-sweep orchestration.

This module connects the pure sweep planner to the existing PAPER caucion
allocator. It has no market-data client and no real-order method. Offers,
schedule and obligations must already be verified/persisted by other workers.

Key policy: reserve is not a fixed percentage. It is the sum of verified cash
obligations due before the requested liquidity deadline. If completeness of
that obligation snapshot cannot be established, the sweep is fail-closed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal

from bs_instrument_contracts import aware_datetime, cash_currency, decimal_value
from bt_caucion_paper import CaucionOffer
from ca_caucion_allocator import CaucionPolicy
from df_caucion_end_of_day_sweep_hf6 import plan_sweep

CASH_SWEEP_ORDER_ROUTING_ALLOWED = False
ZERO = Decimal("0")


@dataclass(frozen=True)
class CashObligation:
    currency: str
    amount: Decimal
    due_at: str
    kind: str
    source: str

    def __post_init__(self):
        object.__setattr__(self, "currency", cash_currency(self.currency))
        object.__setattr__(self, "amount", decimal_value(self.amount, "obligación", nonnegative=True))
        aware_datetime(self.due_at, "vencimiento obligación")
        if not str(self.kind or "").strip() or not str(self.source or "").strip():
            raise ValueError("OBLIGATION_PROVENANCE_MISSING")


@dataclass(frozen=True)
class ObligationSnapshot:
    observed_at: str
    source: str
    complete: bool
    obligations: tuple[CashObligation, ...]

    def __post_init__(self):
        aware_datetime(self.observed_at, "snapshot obligaciones")
        if not str(self.source or "").strip():
            raise ValueError("OBLIGATION_SNAPSHOT_SOURCE_MISSING")
        object.__setattr__(self, "obligations", tuple(self.obligations or ()))


def required_reserve(snapshot: ObligationSnapshot, *, currency: str,
                     liquidity_deadline) -> Decimal:
    """Reserve verified obligations due no later than the liquidity deadline."""
    if not isinstance(snapshot, ObligationSnapshot) or not snapshot.complete:
        raise ValueError("OBLIGATION_SNAPSHOT_INCOMPLETE")
    ccy = cash_currency(currency)
    deadline = aware_datetime(liquidity_deadline, "deadline liquidez")
    if aware_datetime(snapshot.observed_at) > deadline:
        raise ValueError("OBLIGATION_SNAPSHOT_AFTER_DEADLINE")
    total = ZERO
    for item in snapshot.obligations:
        if item.currency == ccy and aware_datetime(item.due_at) <= deadline:
            total += item.amount
    return total


def exact_fee_offers(offers) -> list[CaucionOffer]:
    """Automatic sweep never extrapolates an unknown commission curve."""
    result=[]
    for offer in offers or ():
        if not isinstance(offer, CaucionOffer):
            continue
        if offer.quoted_total_fees is None or offer.fee_quote_principal is None:
            continue
        result.append(offer)
    return result


def run_paper_sweep(broker, offers, *, obligation_snapshot: ObligationSnapshot,
                    currency, as_of, sweep_start_at, order_cutoff_at,
                    liquidity_deadline, schedule_source, request_id,
                    participation=Decimal("0.10"), max_quote_age_seconds=30):
    """Plan and, only when fully verified, invoke the existing PAPER allocator.

    The allocator rechecks cash, risk, depth, fees and idempotency under its own
    SQLite transaction. This wrapper cannot route a real order.
    """
    if CASH_SWEEP_ORDER_ROUTING_ALLOWED:
        raise RuntimeError("CASH_SWEEP_ORDER_ROUTING_INVARIANT_BROKEN")
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("CASH_SWEEP_REQUEST_ID_MISSING")
    ccy = cash_currency(currency)
    at = aware_datetime(as_of)
    try:
        reserve = required_reserve(
            obligation_snapshot, currency=ccy, liquidity_deadline=liquidity_deadline)
    except ValueError as exc:
        return {"status":"HOLD","code":str(exc),"allocation":None}
    verified = exact_fee_offers(offers)
    if not verified:
        return {"status":"HOLD","code":"EXACT_FEE_BUDGET_MISSING","allocation":None}
    with broker.store.connect() as c:
        c.execute("BEGIN")
        cash = broker._cash(at, ccy, connection=c, for_execution=True)
    plan = plan_sweep(
        offers=verified, currency=ccy, as_of=at, available_cash=cash,
        required_reserve=reserve, sweep_start_at=sweep_start_at,
        order_cutoff_at=order_cutoff_at, liquidity_deadline=liquidity_deadline,
        schedule_source=schedule_source, participation=participation,
        max_quote_age_seconds=max_quote_age_seconds)
    result={"status":"HOLD","code":plan.reason,"plan":asdict(plan),"allocation":None}
    if plan.state != "PAPER_CANDIDATE":
        return result

    # Only the exact fee-budget offer that generated the selected principal can
    # enter the allocator. Ambiguous same-id snapshots are not combined.
    selected=[]
    for offer in verified:
        if (offer.instrument_id == plan.instrument_id and
                offer.fee_quote_principal == plan.principal and
                str(offer.quoted_at) == str(plan.quote_at)):
            selected.append(offer)
    if len(selected) != 1:
        result["code"]="SELECTED_OFFER_NOT_UNIQUE_OR_FEE_BUDGET_MISMATCH"
        return result
    policy = CaucionPolicy(
        frozen_at=at.isoformat(), currency=ccy, reserve_cash=reserve,
        maximum_cash_fraction=Decimal("1"), maximum_principal=plan.principal,
        liquidity_deadline=aware_datetime(liquidity_deadline).isoformat(),
        maximum_quote_age_seconds=decimal_value(max_quote_age_seconds,"antigüedad",nonnegative=True),
        participation=decimal_value(participation,"participación",positive=True),
        minimum_net_profit=Decimal("0.01"), ranking="NET_PROFIT",
        session_open_at=aware_datetime(sweep_start_at).isoformat(),
        session_close_at=aware_datetime(order_cutoff_at).isoformat(),
        session_source=str(schedule_source),
    )
    allocation = broker.allocate_caucion(selected, policy, request_id, as_of=at)
    result.update(status=allocation.get("status","HOLD"),
                  code=allocation.get("code","UNKNOWN"), allocation=allocation)
    return result


def assert_cash_sweep_invariants() -> None:
    if CASH_SWEEP_ORDER_ROUTING_ALLOWED is not False:
        raise AssertionError("cash sweep must remain PAPER-only")
