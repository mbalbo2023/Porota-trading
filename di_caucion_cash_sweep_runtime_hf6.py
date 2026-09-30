"""HF6-v2 end-of-day caucion cash-sweep orchestration.

This module connects the pure sweep planner to the existing PAPER caucion
allocator. It has no market-data client and no real-order method. Offers,
schedule and obligations must already be verified/persisted by other workers.

Key policy: reserve is not a fixed percentage. It is the sum of verified cash
obligations due before the requested liquidity deadline. If completeness of
that obligation snapshot cannot be established, the sweep is fail-closed.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from zoneinfo import ZoneInfo

from bs_instrument_contracts import aware_datetime, cash_currency, decimal_value
from bt_caucion_paper import CaucionOffer
from ca_caucion_allocator import CaucionPolicy
from df_caucion_end_of_day_sweep_hf6 import plan_sweep

CASH_SWEEP_ORDER_ROUTING_ALLOWED = False
ZERO = Decimal("0")
TZ = ZoneInfo("America/Argentina/Buenos_Aires")
PAPER_SCHEDULE_SOURCE = "POROTA_PAPER_CAUCION_EOD_POLICY:v1"
CASH_SWEEP_HEALTHY_RUNTIME_STATES = frozenset({
    "WAITING_WINDOW", "WAITING_CALENDAR", "HOLD", "PLACED_SIMULATED",
})


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


def fee_authorized_offers(offers) -> list[CaucionOffer]:
    """Exact broker budgets or the versioned ARS PAPER tariff are admissible."""
    from au_fee_schedule import CAUCION_PAPER_FEE_AUTHORITY
    result = []
    for offer in offers or ():
        if not isinstance(offer, CaucionOffer):
            continue
        exact = (offer.quoted_total_fees is not None
                 and offer.fee_quote_principal is not None)
        paper = (offer.currency == "ARS"
                 and offer.paper_fill_policy == "CONSERVATIVE_NOTIONAL_CAP"
                 and offer.fee_authority == CAUCION_PAPER_FEE_AUTHORITY)
        if exact or paper:
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
    verified = fee_authorized_offers(offers)
    if not verified:
        return {"status":"HOLD","code":"EXACT_FEE_OR_VERSIONED_PAPER_TARIFF_MISSING","allocation":None}
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

    # Only the offer that generated the plan can enter the allocator.  Exact
    # budgets must match their principal; the ARS tariff policy is scalable
    # only inside its conservative notional cap.
    selected=[]
    for offer in verified:
        if (offer.instrument_id == plan.instrument_id and
                (offer.quoted_total_fees is None or
                 offer.fee_quote_principal == plan.principal) and
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


def init_runtime_schema(store) -> None:
    with store.connect() as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS paper_caucion_cash_sweep_state(
          id INTEGER PRIMARY KEY CHECK(id=1), heartbeat_at TEXT NOT NULL,
          state TEXT NOT NULL, last_attempt_at TEXT, request_id TEXT,
          code TEXT NOT NULL, paper_id TEXT, real_orders_sent INTEGER NOT NULL,
          routes_json TEXT NOT NULL, detail TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS paper_caucion_cash_sweep_attempts(
          attempt_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
          evaluated_at TEXT NOT NULL, evidence_hash TEXT NOT NULL,
          status TEXT NOT NULL, code TEXT NOT NULL, result_json TEXT NOT NULL);
        """)


def paper_schedule(at, *, start_minutes=30, cutoff_minutes=5):
    """Internal PAPER window; never represented as a broker order deadline."""
    from ak_byma_calendar import es_dia_habil_operativo
    from co_market_sessions_hf6 import (BYMA_HOURS_SOURCE,
                                        BYMA_PAPER_SPOT_CLOSE,
                                        BYMA_PAPER_SPOT_OPEN)
    local = aware_datetime(at).astimezone(TZ)
    start_minutes = int(start_minutes)
    cutoff_minutes = int(cutoff_minutes)
    if not 1 <= cutoff_minutes < start_minutes <= 180:
        raise ValueError("CASH_SWEEP_INTERNAL_WINDOW_INVALID")
    if not es_dia_habil_operativo(local.date()):
        return None
    close = datetime.combine(local.date(), BYMA_PAPER_SPOT_CLOSE, TZ)
    next_day = local.date() + timedelta(days=1)
    for _ in range(7):
        if es_dia_habil_operativo(next_day):
            break
        next_day += timedelta(days=1)
    else:
        return None
    # Free cash is swept only after reserving every modeled obligation through
    # the next regular close.  This is an internal liquidity horizon, not a
    # claim about a broker cutoff or a same-morning availability promise.
    deadline = datetime.combine(next_day, BYMA_PAPER_SPOT_CLOSE, TZ)
    return {
        "sweep_start_at": close - timedelta(minutes=start_minutes),
        "order_cutoff_at": close - timedelta(minutes=cutoff_minutes),
        "liquidity_deadline": deadline,
        "schedule_source": (PAPER_SCHEDULE_SOURCE +
                            ";LIQUIDITY_DEADLINE=NEXT_BYMA_REGULAR_CLOSE;MARKET_SESSION=" +
                            BYMA_HOURS_SOURCE),
    }


def obligation_snapshot_from_ledger(store, *, observed_at, liquidity_deadline):
    """Conservatively reserve negative family lifecycle balances.

    Spot positions, unsettled sale proceeds and open cauciones are already
    reflected by ``PaperBroker._cash``.  This snapshot covers the independent
    FCI/futures lifecycle ledger; an unknown schema fails closed.
    """
    obligations = []
    with store.connect() as connection:
        table = connection.execute("""SELECT 1 FROM sqlite_master
          WHERE type='table' AND name='paper_family_lifecycle'""").fetchone()
        columns = {row[1] for row in connection.execute(
            "PRAGMA table_info(paper_family_lifecycle)")} if table else set()
        required = {"lifecycle_id", "family", "instrument", "currency", "state",
                    "updated_at", "ledger_total", "metadata_json"}
        if not table or not required <= columns:
            return ObligationSnapshot(
                aware_datetime(observed_at).isoformat(),
                "PAPER_FAMILY_LIFECYCLE_LEDGER:v1", False, ())
        rows = connection.execute("SELECT * FROM paper_family_lifecycle").fetchall()
    for row in rows:
        total = decimal_value(row["ledger_total"], "saldo lifecycle")
        if total >= ZERO:
            continue
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, ValueError):
            return ObligationSnapshot(
                aware_datetime(observed_at).isoformat(),
                "PAPER_FAMILY_LIFECYCLE_LEDGER:v1", False, tuple(obligations))
        due = (metadata.get("due_at") or metadata.get("settlement_at")
               or metadata.get("liquidity_deadline")
               or aware_datetime(liquidity_deadline).isoformat())
        obligations.append(CashObligation(
            row["currency"], -total, due,
            f"{row['family']}:{row['state']}",
            "paper_family_lifecycle:" + str(row["lifecycle_id"])))
    return ObligationSnapshot(
        aware_datetime(observed_at).isoformat(),
        "PAPER_FAMILY_LIFECYCLE_LEDGER:v1", True, tuple(obligations))


def offers_from_store(store, *, now):
    """Bind current v2 evidence to exact PPI primary identities, read-only."""
    from cp_contract_evidence_v2_hf6 import current_records
    from rc6_contract_bridge import caucion_offer_from_evidence
    records = current_records(store, family="CAUCIONES")
    grouped = defaultdict(list)
    for record in records:
        key = tuple(record.get(name) for name in
                    ("ticker", "family", "market", "currency", "settlement"))
        grouped[key].append(record)
    with store.connect() as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        pending = set()
        if "contract_evidence_v2_changes" in tables:
            pending = {tuple(row) for row in connection.execute("""
              SELECT ticker,family,market,currency,settlement
              FROM contract_evidence_v2_changes
              WHERE status='CHANGED_REVIEW_REQUIRED'""")}
        primaries = [dict(row) for row in connection.execute("""
          SELECT * FROM financial_instrument_catalog
          WHERE instrument_type='CAUCIONES' AND status='AVAILABLE'
          ORDER BY ticker,market,currency,settlement""")]
    offers, errors = [], []
    for primary in primaries:
        key = tuple(primary.get(name) for name in
                    ("ticker", "instrument_type", "market", "currency", "settlement"))
        record_key = (key[0], "CAUCIONES", key[2], key[3], key[4])
        if record_key in pending:
            errors.append(f"{key[0]}:CHANGE_REVIEW_REQUIRED")
            continue
        primary["raw"] = json.loads(primary.pop("metadata_json") or "{}")
        try:
            offers.append(caucion_offer_from_evidence(
                grouped.get(record_key, ()), primary, now=aware_datetime(now)))
        except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
            errors.append(f"{key[0]}:{str(exc)}")
    return offers, errors


def _persist_runtime(store, *, at, state, code, request_id=None, paper_id=None,
                     detail="", result=None):
    init_runtime_schema(store)
    with store.connect() as connection:
        observer = connection.execute(
            "SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()
        real_orders = int(observer[0]) if observer else -1
        connection.execute("""INSERT OR REPLACE INTO paper_caucion_cash_sweep_state
          VALUES(1,?,?,?,?,?,?,?,?,?)""", (
            aware_datetime(at).isoformat(), state,
            aware_datetime(at).isoformat() if result is not None else None,
            request_id, code, paper_id, real_orders, "[]", detail))
        if result is not None:
            rendered = json.dumps(result, ensure_ascii=False, sort_keys=True,
                                  default=str, allow_nan=False)
            digest = sha256(rendered.encode()).hexdigest()
            attempt_id = sha256((str(request_id) + "|" + digest).encode()).hexdigest()
            connection.execute("""INSERT OR IGNORE INTO paper_caucion_cash_sweep_attempts
              VALUES(?,?,?,?,?,?,?)""", (
                attempt_id, request_id, aware_datetime(at).isoformat(), digest,
                result.get("status", "HOLD"), result.get("code", code), rendered))


def _existing_daily_allocation(store, request_id):
    with store.connect() as connection:
        row = connection.execute("""SELECT decision_json FROM paper_caucion_allocations
          WHERE request_id=?""", (request_id,)).fetchone()
    if not row:
        return None
    allocation = json.loads(row[0])
    return {"status":allocation.get("status", "HOLD"),
            "code":allocation.get("code", "UNKNOWN"),
            "allocation":allocation,"idempotent":True}


def run_worker(store, stop, *, clock_fn):
    """Canonical PAPER child: local DB/evidence only, with no order route."""
    from bv_paper_runtime import broker_from_environment
    assert_cash_sweep_invariants()
    init_runtime_schema(store)
    interval = max(15, int(os.getenv("PAPER_CAUCION_SWEEP_INTERVAL_SECONDS", "30")))
    start_minutes = int(os.getenv(
        "PAPER_CAUCION_SWEEP_START_MINUTES_BEFORE_CLOSE", "30"))
    cutoff_minutes = int(os.getenv(
        "PAPER_CAUCION_SWEEP_CUTOFF_MINUTES_BEFORE_CLOSE", "5"))
    broker = broker_from_environment(store, clock_fn=clock_fn)
    try:
        while not stop.is_set():
            at = aware_datetime(clock_fn())
            try:
                schedule = paper_schedule(
                    at, start_minutes=start_minutes, cutoff_minutes=cutoff_minutes)
                if schedule is None:
                    _persist_runtime(store, at=at, state="WAITING_CALENDAR",
                                     code="CALENDAR_UNAVAILABLE_OR_CLOSED",
                                     detail="PAPER only; no real routes")
                elif at < schedule["sweep_start_at"]:
                    _persist_runtime(store, at=at, state="WAITING_WINDOW",
                                     code="SWEEP_WINDOW_NOT_OPEN",
                                     detail="PAPER internal EOD window")
                else:
                    day = at.astimezone(TZ).date().isoformat()
                    request_id = f"paper-caucion-cash-sweep:{day}:ARS:v1"
                    result = _existing_daily_allocation(store, request_id)
                    if result is None:
                        offers, errors = offers_from_store(store, now=at)
                        snapshot = obligation_snapshot_from_ledger(
                            store, observed_at=at,
                            liquidity_deadline=schedule["liquidity_deadline"])
                        result = run_paper_sweep(
                            broker, offers, obligation_snapshot=snapshot,
                            currency="ARS", as_of=at, request_id=request_id,
                            participation=broker.participation,
                            max_quote_age_seconds=30, **schedule)
                    else:
                        offers, errors = (), ()
                    allocation = result.get("allocation") or {}
                    paper_id = allocation.get("paper_id")
                    state = ("PLACED_SIMULATED" if result.get("status") == "PLACED_SIMULATED"
                             else "HOLD")
                    detail = (f"offers={len(offers)};errors={len(errors)};"
                              "paper_only=true;real_routes=[]")
                    _persist_runtime(
                        store, at=at, state=state,
                        code=result.get("code", "UNKNOWN"),
                        request_id=request_id, paper_id=paper_id,
                        detail=detail, result=result)
            except Exception as exc:
                _persist_runtime(store, at=at, state="ERROR",
                                 code=type(exc).__name__,
                                 detail=str(exc)[:500] + "; paper_only=true")
            stop.wait(interval)
    finally:
        _persist_runtime(store, at=clock_fn(), state="STOPPED", code="STOPPED",
                         detail="No se enviaron órdenes; real_routes=[]")