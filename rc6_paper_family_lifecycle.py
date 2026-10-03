"""Isolated PAPER ledgers for fund and futures lifecycle design.

This module has no broker client and no real-order route.  It records explicit,
idempotent transitions in a caller-supplied SQLite store so missing executors
remain distinguishable from missing contract evidence.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from bs_instrument_contracts import (InstrumentContract, aware_datetime, cash_currency,\n                                     decimal_value)


PAPER_ONLY = True
REAL_ROUTES_USED = ()

TRANSITIONS = {
    "FCI": {
        None: {"SUBSCRIBE_REQUESTED", "SUBSCRIBE"},
        "SUBSCRIBE_REQUESTED": {"PENDING"},
        "SUBSCRIBE": {"PENDING"},  # legacy replay compatibility
        "PENDING": {"NAV_APPLIED"},
        "NAV_APPLIED": {"SETTLED"},
        "SETTLED": {"REDEEM_REQUESTED"},
        "REDEEM_REQUESTED": {"REDEEMED"},
    },
    "FUTUROS": {
        None: {"OPEN"},
        "OPEN": {"MARGIN_RESERVED"},
        "MARGIN_RESERVED": {"DAILY_VARIATION", "CLOSE", "EXPIRY"},
        "DAILY_VARIATION": {"MARGIN_OK", "MARGIN_DEFICIT", "CLOSE", "EXPIRY"},
        "MARGIN_OK": {"DAILY_VARIATION", "CLOSE", "EXPIRY"},
        "MARGIN_DEFICIT": {"DAILY_VARIATION", "CLOSE", "EXPIRY"},
    },
}


def _positive(value, name):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name}: número inválido") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{name}: valor fuera de rango")
    return result


@dataclass(frozen=True)
class PaperFundTerms:
    symbol: str
    family: str
    currency: str
    market: str
    settlement: str
    metadata_source: str
    paper_subscription_policy: str = ""
    paper_subscription_min: Decimal = Decimal("1000")
    paper_amount_unit: Decimal = Decimal("0.01")
    subscription_min: Decimal | None = None
    subscription_step: Decimal | None = None
    broker_subscription_min: str = "NO_VERIFICADO"
    broker_subscription_step: str = "NO_VERIFICADO"

    def __post_init__(self):
        if str(self.family).upper() not in {"FCI", "FCI_LOCAL"}:
            raise ValueError("PAPER_FUND_FAMILY_INVALID")
        object.__setattr__(self, "family", "FCI")
        object.__setattr__(self, "currency", cash_currency(self.currency))
        policy = str(self.paper_subscription_policy or "").upper()
        if policy and policy != "INTERNAL_RISK_BUDGET_BY_AMOUNT":
            raise ValueError("PAPER_FUND_POLICY_INVALID")
        object.__setattr__(self, "paper_subscription_policy", policy)
        object.__setattr__(self, "paper_subscription_min", _positive(
            self.paper_subscription_min, "paper_subscription_min"))
        object.__setattr__(self, "paper_amount_unit", _positive(
            self.paper_amount_unit, "paper_amount_unit"))
        if self.subscription_min is not None:
            object.__setattr__(self, "subscription_min", _positive(
                self.subscription_min, "subscription_min"))
        if self.subscription_step is not None:
            object.__setattr__(self, "subscription_step", _positive(
                self.subscription_step, "subscription_step"))
        if not policy and (self.subscription_min is None or self.subscription_step is None):
            raise ValueError("PAPER_FUND_POLICY_OR_BROKER_TERMS_REQUIRED")
        if not all(str(value or "").strip() for value in (
                self.symbol, self.market, self.settlement, self.metadata_source)):
            raise ValueError("PAPER_FUND_TERMS_INCOMPLETE")

    def subscription_amount(self, value):
        amount = _positive(value, "subscription_amount")
        if self.paper_subscription_policy == "INTERNAL_RISK_BUDGET_BY_AMOUNT":
            if amount < self.paper_subscription_min:
                raise ValueError("FCI_PAPER_SUBSCRIPTION_BELOW_MINIMUM")
            if amount % self.paper_amount_unit:
                raise ValueError("FCI_PAPER_AMOUNT_UNIT_INVALID")
            return amount
        if amount < self.subscription_min:
            raise ValueError("FCI_SUBSCRIPTION_BELOW_MINIMUM")
        if (amount - self.subscription_min) % self.subscription_step:
            raise ValueError("FCI_SUBSCRIPTION_STEP_INVALID")
        return amount


def fund_terms_from_metadata(symbol, metadata):
    fields = {name: metadata[name] for name in PaperFundTerms.__dataclass_fields__
              if name not in {"symbol", "family"} and name in metadata}
    try:
        return PaperFundTerms(symbol=symbol, family="FCI", **fields)
    except TypeError as exc:
        raise ValueError("PAPER_FUND_TERMS_INCOMPLETE") from exc


class FamilyPaperExecutor:
    """Motor durable de FCI/futuros, exclusivamente simulado y sin broker."""

    paper_only = True
    real_routes_used = ()

    def __init__(self, store):
        self.store = store
        init_schema(store)

    def subscribe_fund(self, terms, *, lifecycle_id, event_id, amount, occurred_at=None):
        if not isinstance(terms, PaperFundTerms):
            raise ValueError("FCI_TERMS_REQUIRED")
        subscribed = terms.subscription_amount(amount)
        return apply_paper_event(
            self.store, lifecycle_id=lifecycle_id, event_id=event_id,
            family="FCI", instrument=terms.symbol, currency=terms.currency,
            to_state="SUBSCRIBE_REQUESTED", amount=-subscribed, occurred_at=occurred_at,
            detail={"mode": "PRODUCTION_PAPER", "execution": "SIMULATION",
                    "subscription_amount": str(subscribed),
                    "metadata_source": terms.metadata_source})

    def future_event(self, contract, *, lifecycle_id, event_id, to_state,
                     amount="0", occurred_at=None, detail=None, connection=None):
        if not isinstance(contract, InstrumentContract) or contract.family != "FUTUROS":
            raise ValueError("FUTURES_CONTRACT_REQUIRED")
        return apply_paper_event(
            self.store, lifecycle_id=lifecycle_id, event_id=event_id,
            family="FUTUROS", instrument=contract.symbol,
            currency=contract.currency, to_state=to_state, amount=amount,
            occurred_at=occurred_at,
            detail={"mode": "PRODUCTION_PAPER", "execution": "SIMULATION",
                    "metadata_source": contract.metadata_source, **(detail or {})},
            connection=connection)

    def open_future(self, contract, *, lifecycle_id, event_id, entry_price,
                    quantity, entry_cost="0", occurred_at=None, side="LONG",
                    detail=None):
        """Reserve collateral and persist one simulated future atomically."""
        _validate_future_contract(contract)
        if str(side).upper() != "LONG":
            raise ValueError("FUTURES_PAPER_LONG_ONLY")
        stamp = aware_datetime(occurred_at or _now())
        expiry = aware_datetime(contract.expires_at, "vencimiento futuro")
        if stamp >= expiry:
            raise ValueError("FUTURE_EXPIRED")
        price = decimal_value(entry_price, "precio entrada", positive=True)
        qty = contract.quantity(quantity)
        cost = decimal_value(entry_cost, "costo entrada", nonnegative=True)
        reserve = contract.cash_required(price, qty)
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM paper_future_positions WHERE lifecycle_id=?",
                (str(lifecycle_id),)).fetchone()
            if existing:
                row = dict(existing)
                expected = (contract.symbol, contract.currency, contract.market,
                            contract.settlement, str(qty), str(price))
                observed = (row["symbol"], row["currency"], row["market"],
                            row["settlement"], row["quantity"], row["entry_price"])
                if expected != observed:
                    raise ValueError("FUTURES_LIFECYCLE_ID_COLLISION")
                prior = connection.execute(
                    "SELECT 1 FROM paper_family_lifecycle_events WHERE event_id=?",
                    (str(event_id),)).fetchone()
                if not prior:
                    raise ValueError("FUTURES_OPEN_IDEMPOTENCY_MISMATCH")
                return _future_result(row, idempotent=True)

            base_detail = {
                "mode": "PRODUCTION_PAPER", "execution": "SIMULATION",
                "side": "LONG", "quantity": str(qty),
                "entry_price": str(price), "entry_cost": str(cost),
                "cash_multiplier": str(contract.cash_multiplier),
                "margin_reserved": str(reserve),
                "expires_at": contract.expires_at,
                "paper_margin_policy": contract.paper_margin_policy,
                "paper_margin_rate": (str(contract.paper_margin_rate)
                                      if contract.paper_margin_rate is not None else None),
                "real_routes_used": [],
                **(detail or {}),
            }
            self.future_event(
                contract, lifecycle_id=lifecycle_id, event_id=event_id,
                to_state="OPEN", occurred_at=stamp.isoformat(),
                detail=base_detail, connection=connection)
            self.future_event(
                contract, lifecycle_id=lifecycle_id,
                event_id=f"{event_id}:MARGIN", to_state="MARGIN_RESERVED",
                amount=str(-(reserve + cost)), occurred_at=stamp.isoformat(),
                detail={**base_detail, "cash_effect": str(-(reserve + cost))},
                connection=connection)
            connection.execute("""INSERT INTO paper_future_positions
              (lifecycle_id,symbol,currency,market,settlement,side,quantity,
               cash_multiplier,entry_price,settlement_base_price,last_mark_price,
               margin_reserved,entry_cost,exit_cost,variation_realized,
               unrealized_pnl,opened_at,last_mark_at,last_book_at,expires_at,
               status,closed_at,close_reason,metadata_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'0','0','0',?,?,?,?,?,'ACTIVE',NULL,NULL,?)""",
              (str(lifecycle_id), contract.symbol, contract.currency, contract.market,
               contract.settlement, "LONG", str(qty), str(contract.cash_multiplier),
               str(price), str(price), str(price), str(reserve), str(cost),
               stamp.isoformat(), stamp.isoformat(), stamp.isoformat(),
               contract.expires_at, json.dumps(base_detail, ensure_ascii=False, sort_keys=True)))
            row = dict(connection.execute(
                "SELECT * FROM paper_future_positions WHERE lifecycle_id=?",
                (str(lifecycle_id),)).fetchone())
        return _future_result(row, idempotent=False)

    def mark_future(self, contract, *, lifecycle_id, event_id, mark_price,
                    book_at, occurred_at=None, settlement=False, detail=None):
        """Persist a fresh mark; settlement=True applies explicit daily variation."""
        _validate_future_contract(contract)
        stamp = aware_datetime(occurred_at or _now())
        source_at = aware_datetime(book_at, "book futuro")
        if source_at > stamp:
            raise ValueError("FUTURES_BOOK_TIME_FUTURE")
        price = decimal_value(mark_price, "mark futuro", positive=True)
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior_mark = connection.execute(
                "SELECT * FROM paper_future_marks WHERE event_id=?",
                (str(event_id),)).fetchone()
            if prior_mark:
                if prior_mark["lifecycle_id"] != str(lifecycle_id):
                    raise ValueError("FUTURES_MARK_EVENT_COLLISION")
                row = dict(connection.execute(
                    "SELECT * FROM paper_future_positions WHERE lifecycle_id=?",
                    (str(lifecycle_id),)).fetchone())
                return _future_result(row, idempotent=True)
            row = connection.execute(
                "SELECT * FROM paper_future_positions WHERE lifecycle_id=?",
                (str(lifecycle_id),)).fetchone()
            if not row or row["status"] != "ACTIVE":
                raise ValueError("FUTURES_ACTIVE_POSITION_REQUIRED")
            row = dict(row)
            _future_row_matches_contract(row, contract)
            if stamp < aware_datetime(row["opened_at"]) or source_at < aware_datetime(row["opened_at"]):
                raise ValueError("FUTURES_MARK_BEFORE_OPEN")
            qty = contract.quantity(row["quantity"])
            base = decimal_value(row["settlement_base_price"], "base futuro", positive=True)
            unrealized = contract.pnl(base, price, qty, side=row["side"])
            realized = decimal_value(row["variation_realized"], "variación realizada")
            if settlement:
                variation = unrealized
                realized += variation
                self.future_event(
                    contract, lifecycle_id=lifecycle_id, event_id=event_id,
                    to_state="DAILY_VARIATION", amount=str(variation),
                    occurred_at=stamp.isoformat(),
                    detail={"settlement_price": str(price),
                            "variation_pnl": str(variation),
                            "book_at": source_at.isoformat(),
                            "source": str((detail or {}).get("source") or "EXPLICIT_SETTLEMENT"),
                            "real_routes_used": []},
                    connection=connection)
                collateral = decimal_value(row["margin_reserved"], "garantía", positive=True) + realized
                deficit = contract.margin_deficit(collateral, qty, price=price)
                margin_state = "MARGIN_DEFICIT" if deficit > 0 else "MARGIN_OK"
                self.future_event(
                    contract, lifecycle_id=lifecycle_id,
                    event_id=f"{event_id}:MARGIN", to_state=margin_state,
                    occurred_at=stamp.isoformat(),
                    detail={"margin_deficit": str(deficit),
                            "collateral_after_variation": str(collateral),
                            "real_routes_used": []},
                    connection=connection)
                base = price
                unrealized = Decimal("0")
            connection.execute("""INSERT INTO paper_future_marks
              (event_id,lifecycle_id,mark_price,book_at,observed_at,
               is_settlement,unrealized_pnl,detail_json)
              VALUES(?,?,?,?,?,?,?,?)""",
              (str(event_id), str(lifecycle_id), str(price), source_at.isoformat(),
               stamp.isoformat(), 1 if settlement else 0, str(unrealized),
               json.dumps(detail or {}, ensure_ascii=False, sort_keys=True)))
            connection.execute("""UPDATE paper_future_positions
              SET settlement_base_price=?,last_mark_price=?,variation_realized=?,
                  unrealized_pnl=?,last_mark_at=?,last_book_at=?,metadata_json=?
              WHERE lifecycle_id=?""",
              (str(base), str(price), str(realized), str(unrealized),
               stamp.isoformat(), source_at.isoformat(),
               json.dumps({**json.loads(row["metadata_json"] or "{}"),
                           "last_mark_source": str((detail or {}).get("source") or "PPI_BOOK"),
                           "last_mark_settlement": bool(settlement)},
                          ensure_ascii=False, sort_keys=True),
               str(lifecycle_id)))
            row = dict(connection.execute(
                "SELECT * FROM paper_future_positions WHERE lifecycle_id=?",
                (str(lifecycle_id),)).fetchone())
        return _future_result(row, idempotent=False)

    def close_future(self, contract, *, lifecycle_id, event_id, exit_price,
                     book_at, occurred_at=None, reason="EXIT", expiry=False,
                     exit_cost="0", detail=None):
        """Release PAPER collateral and realize the final marked variation."""
        _validate_future_contract(contract)
        stamp = aware_datetime(occurred_at or _now())
        source_at = aware_datetime(book_at, "book futuro")
        if source_at > stamp:
            raise ValueError("FUTURES_BOOK_TIME_FUTURE")
        price = decimal_value(exit_price, "precio salida", positive=True)
        cost = decimal_value(exit_cost, "costo salida", nonnegative=True)
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                "SELECT 1 FROM paper_family_lifecycle_events WHERE event_id=?",
                (str(event_id),)).fetchone()
            row = connection.execute(
                "SELECT * FROM paper_future_positions WHERE lifecycle_id=?",
                (str(lifecycle_id),)).fetchone()
            if not row:
                raise ValueError("FUTURES_ACTIVE_POSITION_REQUIRED")
            row = dict(row)
            if prior:
                return _future_result(row, idempotent=True)
            if row["status"] != "ACTIVE":
                raise ValueError("FUTURES_ACTIVE_POSITION_REQUIRED")
            _future_row_matches_contract(row, contract)
            if stamp < aware_datetime(row["opened_at"]) or source_at < aware_datetime(row["opened_at"]):
                raise ValueError("FUTURES_EXIT_BEFORE_OPEN")
            qty = contract.quantity(row["quantity"])
            base = decimal_value(row["settlement_base_price"], "base futuro", positive=True)
            final_variation = contract.pnl(base, price, qty, side=row["side"])
            gross_realized = decimal_value(row["variation_realized"], "variación realizada") + final_variation
            net_realized = (gross_realized
                            - decimal_value(row["entry_cost"], "costo entrada", nonnegative=True)
                            - cost)
            reserve = decimal_value(row["margin_reserved"], "garantía", positive=True)
            terminal = "EXPIRY" if expiry else "CLOSE"
            self.future_event(
                contract, lifecycle_id=lifecycle_id, event_id=event_id,
                to_state=terminal, amount=str(reserve + final_variation - cost),
                occurred_at=stamp.isoformat(),
                detail={"exit_price": str(price), "final_variation": str(final_variation),
                        "gross_realized_pnl": str(gross_realized),
                        "net_realized_pnl": str(net_realized),
                        "margin_released": str(reserve), "exit_cost": str(cost),
                        "reason": str(reason), "book_at": source_at.isoformat(),
                        "real_routes_used": [], **(detail or {})},
                connection=connection)
            connection.execute("""INSERT INTO paper_future_marks
              (event_id,lifecycle_id,mark_price,book_at,observed_at,
               is_settlement,unrealized_pnl,detail_json)
              VALUES(?,?,?,?,?,0,'0',?)""",
              (f"{event_id}:EXIT_MARK", str(lifecycle_id), str(price),
               source_at.isoformat(), stamp.isoformat(),
               json.dumps({"reason": str(reason)}, ensure_ascii=False, sort_keys=True)))
            connection.execute("""UPDATE paper_future_positions
              SET last_mark_price=?,unrealized_pnl='0',last_mark_at=?,last_book_at=?,
                  exit_cost=?,status='CLOSED',closed_at=?,close_reason=?,
                  variation_realized=?,metadata_json=?
              WHERE lifecycle_id=?""",
              (str(price), stamp.isoformat(), source_at.isoformat(), str(cost),
               stamp.isoformat(), str(reason), str(gross_realized),
               json.dumps({**json.loads(row["metadata_json"] or "{}"),
                           "realized_pnl": str(net_realized), "terminal_state": terminal},
                          ensure_ascii=False, sort_keys=True),
               str(lifecycle_id)))
            row = dict(connection.execute(
                "SELECT * FROM paper_future_positions WHERE lifecycle_id=?",
                (str(lifecycle_id),)).fetchone())
            row["realized_pnl"] = str(net_realized)
        return _future_result(row, idempotent=False)

    def active_future(self, symbol):
        init_schema(self.store)
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM paper_future_positions WHERE symbol=? AND status='ACTIVE' ORDER BY opened_at",
                (str(symbol),)).fetchall()
        if len(rows) > 1:
            raise ValueError("FUTURES_MULTIPLE_ACTIVE_POSITIONS")
        return dict(rows[0]) if rows else None


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def init_schema(store):
    with store.connect() as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS paper_family_lifecycle(
          lifecycle_id TEXT PRIMARY KEY,
          family TEXT NOT NULL,
          instrument TEXT NOT NULL,
          currency TEXT NOT NULL,
          state TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          ledger_total TEXT NOT NULL DEFAULT '0',
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS paper_family_lifecycle_events(
          event_id TEXT PRIMARY KEY,
          lifecycle_id TEXT NOT NULL,
          family TEXT NOT NULL,
          from_state TEXT,
          to_state TEXT NOT NULL,
          amount TEXT NOT NULL,
          occurred_at TEXT NOT NULL,
          detail_json TEXT NOT NULL,
          FOREIGN KEY(lifecycle_id) REFERENCES paper_family_lifecycle(lifecycle_id)
        );
        CREATE INDEX IF NOT EXISTS idx_paper_family_lifecycle_state
          ON paper_family_lifecycle(family,state,updated_at);
        """)


def apply_paper_event(store, *, lifecycle_id, event_id, family, instrument,
                      currency, to_state, amount="0", occurred_at=None,
                      detail=None, connection=None):
    """Apply one idempotent PAPER transition or fail closed.

    A repeated event_id returns the original result without a second ledger
    mutation. No transition calls a broker or infers NAV/margin/settlement.
    """
    family = str(family or "").upper()
    to_state = str(to_state or "").upper()
    if family not in TRANSITIONS:
        raise ValueError("PAPER_LIFECYCLE_FAMILY_UNSUPPORTED")
    init_schema(store)
    if connection is None:
        with store.connect() as owned:
            owned.execute("BEGIN IMMEDIATE")
            return apply_paper_event(
                store, lifecycle_id=lifecycle_id, event_id=event_id,
                family=family, instrument=instrument, currency=currency,
                to_state=to_state, amount=amount, occurred_at=occurred_at,
                detail=detail, connection=owned)
    stamp = aware_datetime(occurred_at or _now()).isoformat()
    existing_event = connection.execute(
        "SELECT * FROM paper_family_lifecycle_events WHERE event_id=?",
        (str(event_id),),
    ).fetchone()
    if existing_event:
        row = dict(existing_event)
        if row["lifecycle_id"] != str(lifecycle_id):
            raise ValueError("PAPER_LIFECYCLE_EVENT_ID_COLLISION")
        return {"idempotent": True, "state": row["to_state"],
                "real_routes_used": [], "paper_only": True}

    current = connection.execute(
        "SELECT * FROM paper_family_lifecycle WHERE lifecycle_id=?",
        (str(lifecycle_id),),
    ).fetchone()
    from_state = current["state"] if current else None
    if current and (current["family"] != family
                    or current["instrument"] != str(instrument)
                    or current["currency"] != str(currency).upper()):
        raise ValueError("PAPER_LIFECYCLE_IDENTITY_MISMATCH")
    if to_state not in TRANSITIONS[family].get(from_state, set()):
        raise ValueError(f"PAPER_LIFECYCLE_INVALID_TRANSITION:{from_state}->{to_state}")

    try:
        delta = Decimal(str(amount))
        previous = Decimal(str(current["ledger_total"])) if current else Decimal("0")
    except InvalidOperation as exc:
        raise ValueError("PAPER_LIFECYCLE_AMOUNT_INVALID") from exc
    if not delta.is_finite():
        raise ValueError("PAPER_LIFECYCLE_AMOUNT_INVALID")
    total = previous + delta
    payload = json.dumps(detail or {}, ensure_ascii=False, sort_keys=True)
    connection.execute("""INSERT INTO paper_family_lifecycle
      (lifecycle_id,family,instrument,currency,state,updated_at,ledger_total,metadata_json)
      VALUES(?,?,?,?,?,?,?,?)
      ON CONFLICT(lifecycle_id) DO UPDATE SET state=excluded.state,
        updated_at=excluded.updated_at,ledger_total=excluded.ledger_total,
        metadata_json=excluded.metadata_json""",
      (str(lifecycle_id), family, str(instrument), str(currency).upper(),
       to_state, stamp, str(total), payload))
    connection.execute("""INSERT INTO paper_family_lifecycle_events
      (event_id,lifecycle_id,family,from_state,to_state,amount,occurred_at,detail_json)
      VALUES(?,?,?,?,?,?,?,?)""",
      (str(event_id), str(lifecycle_id), family, from_state, to_state,
       str(delta), stamp, payload))
    return {"idempotent": False, "state": to_state, "ledger_total": str(total),
            "real_routes_used": [], "paper_only": True}


def lifecycle_state(store, lifecycle_id):
    init_schema(store)
    with store.connect() as connection:
        row = connection.execute(
            "SELECT * FROM paper_family_lifecycle WHERE lifecycle_id=?",
            (str(lifecycle_id),),
        ).fetchone()
    return dict(row) if row else None


def _validate_future_contract(contract):
    if not isinstance(contract, InstrumentContract) or contract.family != "FUTUROS":
        raise ValueError("FUTURES_CONTRACT_REQUIRED")
    if contract.market != "A3":
        raise ValueError("FUTURES_A3_CONTRACT_REQUIRED")
    if contract.paper_margin_policy != "CONSERVATIVE_NOTIONAL_RATE":
        raise ValueError("FUTURES_PAPER_MARGIN_POLICY_REQUIRED")
    if contract.paper_margin_rate != Decimal("1"):
        raise ValueError("FUTURES_FULL_NOTIONAL_RESERVE_REQUIRED")


def _future_row_matches_contract(row, contract):
    observed = (row["symbol"], row["currency"], row["market"], row["settlement"],
                decimal_value(row["cash_multiplier"], "multiplicador", positive=True))
    expected = (contract.symbol, contract.currency, contract.market, contract.settlement,
                contract.cash_multiplier)
    if observed != expected:
        raise ValueError("FUTURES_CONTRACT_POSITION_MISMATCH")


def _future_result(row, *, idempotent):
    metadata = json.loads(row.get("metadata_json") or "{}") if row else {}
    return {
        "lifecycle_id": row.get("lifecycle_id") if row else None,
        "symbol": row.get("symbol") if row else None,
        "state": row.get("status") if row else None,
        "margin_reserved": row.get("margin_reserved") if row else None,
        "variation_realized": row.get("variation_realized") if row else None,
        "unrealized_pnl": row.get("unrealized_pnl") if row else None,
        "realized_pnl": metadata.get("realized_pnl"),
        "idempotent": bool(idempotent),
        "paper_only": True,
        "real_routes_used": [],
    }


def future_cash_effect(store, currency, at, *, connection=None):
    """Cash movement only: collateral reserve/release, variation and fees."""
    init_schema(store)
    point = aware_datetime(at)
    if connection is None:
        with store.connect() as owned:
            return future_cash_effect(store, currency, point, connection=owned)
    rows = connection.execute("""SELECT amount FROM paper_family_lifecycle_events
      WHERE family='FUTUROS' AND EXISTS(
        SELECT 1 FROM paper_family_lifecycle l
        WHERE l.lifecycle_id=paper_family_lifecycle_events.lifecycle_id
          AND l.currency=?
      ) AND julianday(occurred_at)<=julianday(?)""",
      (cash_currency(currency), point.isoformat())).fetchall()
    return sum((Decimal(str(row[0])) for row in rows), Decimal("0"))


def future_positions(store, currency=None, *, connection=None, active_only=False):
    init_schema(store)
    if connection is None:
        with store.connect() as owned:
            return future_positions(store, currency, connection=owned, active_only=active_only)
    where, params = [], []
    if currency is not None:
        where.append("currency=?"); params.append(cash_currency(currency))
    if active_only:
        where.append("status='ACTIVE'")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    return [dict(row) for row in connection.execute(
        "SELECT * FROM paper_future_positions" + clause + " ORDER BY opened_at,lifecycle_id",
        tuple(params)).fetchall()]


def future_risk_snapshot(store, currency, at, *, connection=None,
                         max_mark_age_seconds=120):
    """Current same-day futures PnL for DailyRisk; carry or stale mark blocks."""
    init_schema(store)
    point = aware_datetime(at)
    if connection is None:
        with store.connect() as owned:
            return future_risk_snapshot(
                store, currency, point, connection=owned,
                max_mark_age_seconds=max_mark_age_seconds)
    local_day = point.astimezone(__import__("zoneinfo").ZoneInfo(
        "America/Argentina/Buenos_Aires")).date()
    realized = unrealized = collateral = Decimal("0")
    stale = carry = False
    for row in future_positions(store, currency, connection=connection):
        opened = aware_datetime(row["opened_at"])
        closed = aware_datetime(row["closed_at"]) if row["closed_at"] else None
        if opened > point:
            continue
        if closed is not None and closed > point:
            # Current-row history cannot safely reconstruct a later close.
            stale = True
            continue
        if opened.astimezone(__import__("zoneinfo").ZoneInfo(
                "America/Argentina/Buenos_Aires")).date() < local_day and (
                closed is None or closed.astimezone(__import__("zoneinfo").ZoneInfo(
                    "America/Argentina/Buenos_Aires")).date() >= local_day):
            carry = True
        entry_cost = decimal_value(row["entry_cost"], "costo entrada", nonnegative=True)
        variation = decimal_value(row["variation_realized"], "variación realizada")
        if closed is not None and closed <= point:
            metadata = json.loads(row["metadata_json"] or "{}")
            realized += Decimal(str(metadata.get("realized_pnl") or
                                    (variation - entry_cost
                                     - decimal_value(row["exit_cost"], "costo salida", nonnegative=True))))
            continue
        collateral += decimal_value(row["margin_reserved"], "garantía", positive=True)
        realized += variation - entry_cost
        mark_at = aware_datetime(row["last_book_at"], "book futuro")
        age = (point - mark_at).total_seconds()
        if age < 0 or age > max_mark_age_seconds:
            stale = True
        unrealized += decimal_value(row["unrealized_pnl"], "PnL futuro")
    return {
        "realized": realized,
        "unrealized": unrealized,
        "collateral": collateral,
        "stale": stale,
        "carry": carry,
    }
