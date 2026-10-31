"""Isolated PAPER ledgers for fund and futures lifecycle design.

This module has no broker client and no real-order route.  It records explicit,
idempotent transitions in a caller-supplied SQLite store so missing executors
remain distinguishable from missing contract evidence.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from bs_instrument_contracts import (InstrumentContract, aware_datetime, cash_currency,
                                     decimal_value, register_exact_time_sql,
                                     utc_microseconds)


PAPER_ONLY = True
REAL_ROUTES_USED = ()


def _number_text(value):
    result = decimal_value(value, "intent amount")
    if not result:
        return "0"
    text = format(result, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _within_mark_age(stamp, source, maximum_seconds):
    elapsed = utc_microseconds(stamp) - utc_microseconds(source)
    maximum = decimal_value(maximum_seconds, "mark age", nonnegative=True) * Decimal("1000000")
    return 0 <= elapsed <= maximum


def _json_default(value):
    if isinstance(value, Decimal):
        return _number_text(value)
    if isinstance(value, datetime):
        return aware_datetime(value).astimezone(timezone.utc).isoformat(timespec="microseconds")
    raise TypeError("unsupported intent detail")


def _canonical_json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False, default=_json_default)
    except (TypeError, ValueError) as exc:
        raise ValueError("PAPER_LIFECYCLE_DETAIL_INVALID") from exc


def intent_fingerprint(*, lifecycle_id, family, instrument, currency,
                       to_state, amount, occurred_at, detail):
    """Full request semantics, derived from immutable events without migration."""
    return hashlib.sha256(_canonical_json({
        "lifecycle_id": str(lifecycle_id), "family": str(family).upper(),
        "instrument": str(instrument), "currency": cash_currency(currency),
        "to_state": str(to_state).upper(), "amount": _number_text(amount),
        "occurred_at_us": utc_microseconds(occurred_at), "detail": detail or {},
    }).encode()).hexdigest()


def _future_request(contract, *, operation, occurred_at, book_at, detail, **values):
    return {"operation": operation, "contract": _contract_snapshot(contract),
            "occurred_at_us": utc_microseconds(occurred_at),
            "book_at_us": utc_microseconds(book_at), "detail": detail or {},
            **{key: _number_text(value) if isinstance(value, Decimal) else value
               for key, value in values.items()}}


def _assert_future_request(prior_detail, request, error):
    observed = prior_detail.get("paper_request_v1")
    if observed is not None and _canonical_json(observed) != _canonical_json(request):
        raise ValueError(error)


_OPEN_SYSTEM_FIELDS = frozenset({
    "mode", "execution", "side", "quantity", "entry_price", "entry_cost",
    "cash_multiplier", "margin_reserved", "expires_at", "paper_margin_policy",
    "paper_margin_rate", "real_routes_used", "financial_contract",
    "contract_snapshot_sha256", "opening_book_at", "price_kind",
    "execution_price_terms", "paper_request_v1",
})
_CLOSE_SYSTEM_FIELDS = frozenset({
    "mode", "execution", "metadata_source", "exit_price", "final_variation",
    "gross_realized_pnl", "net_realized_pnl", "margin_released", "exit_cost",
    "reason", "book_at", "real_routes_used", "price_kind", "price_source",
    "price_rule", "paper_request_v1",
})

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
        "MARGIN_RESERVED": {"DAILY_VARIATION", "CLOSE", "EXPIRY", "ADMIN_CANCEL"},
        "DAILY_VARIATION": {"MARGIN_OK", "MARGIN_DEFICIT", "CLOSE", "EXPIRY", "ADMIN_CANCEL"},
        "MARGIN_OK": {"DAILY_VARIATION", "CLOSE", "EXPIRY", "ADMIN_CANCEL"},
        "MARGIN_DEFICIT": {"DAILY_VARIATION", "CLOSE", "EXPIRY", "ADMIN_CANCEL"},
    },
}

# An initial FCI commitment in the effective runtime book must pass the
# executor's locked catalog, cash and preopen checks. The generic lifecycle
# primitive remains available for offline fixtures and subsequent NAV events.
_FCI_LOCKED_SUBSCRIPTION = object()
# A207's compensating cash event must never be writable through the public
# generic lifecycle primitive. Only the reconciled administrative writer may
# supply this module-private capability within its caller-owned transaction.
_FUTURE_LOCKED_ADMIN_CANCEL = object()


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
        ensure_initialized(store)

    def subscribe_fund(self, terms, *, lifecycle_id, event_id, amount, occurred_at=None,
                       cash_guard=None, admission_guard=None):
        if not isinstance(terms, PaperFundTerms):
            raise ValueError("FCI_TERMS_REQUIRED")
        subscribed = terms.subscription_amount(amount)
        detail = {"mode": "PRODUCTION_PAPER", "execution": "SIMULATION",
                  "subscription_amount": _number_text(subscribed),
                  "metadata_source": terms.metadata_source,
                  "fund_terms": json.loads(_canonical_json(asdict(terms)))}
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            # A retry is checked against the immutable original intent; it
            # creates no new commitment when the current gate is closed.
            prior = connection.execute(
                "SELECT 1 FROM paper_family_lifecycle_events WHERE event_id=?",
                (str(event_id),)).fetchone()
            if not prior:
                try:
                    from cg_paper_workspace import database_path
                    runtime_book = Path(self.store.path).resolve() == database_path()
                except (OSError, ValueError, TypeError) as exc:
                    raise ValueError("PAPER_PREOPEN_GATE_UNAVAILABLE") from exc
                if runtime_book and (cash_guard is None or admission_guard is None):
                    raise ValueError("FCI_PAPER_ADMISSION_GUARDS_REQUIRED")
                if runtime_book:
                    from bu_instrument_catalog import lookup, contract_for
                    record = lookup(self.store, terms.symbol, "FCI", terms.settlement,
                                    market=terms.market, currency=terms.currency)
                    if (not record or record.get("status") != "AVAILABLE"
                            or record.get("capability") != "READY_PAPER_FCI_SUBSCRIPTION"
                            or record.get("_admission_reason")):
                        raise ValueError("FCI_EXACT_CATALOG_NOT_READY")
                    try:
                        current_terms = contract_for(record)
                    except (ValueError, TypeError) as exc:
                        raise ValueError("FCI_EXACT_TERMS_UNAVAILABLE") from exc
                    if (not isinstance(current_terms, PaperFundTerms)
                            or asdict(current_terms) != asdict(terms)
                            or (record["market"], record["currency"], record["settlement"])
                               != (terms.market, terms.currency, terms.settlement)):
                        raise ValueError("FCI_EXACT_TERMS_MISMATCH")
                if cash_guard is not None:
                    available = decimal_value(cash_guard(connection),
                                              "caja FCI disponible", nonnegative=True)
                    if available < subscribed:
                        raise ValueError("FCI_PAPER_INSUFFICIENT_CASH")
                if admission_guard is not None:
                    blocked = str(admission_guard(connection) or "")
                    if blocked:
                        raise ValueError(blocked)
                if runtime_book:
                    try:
                        from rc6_paper_admission_gate import admission_status
                        allowed, reason = admission_status()
                    except Exception as exc:
                        raise ValueError("PAPER_PREOPEN_GATE_UNAVAILABLE") from exc
                    if not allowed:
                        raise ValueError(reason or "NO_GO_PAPER_SCALPING")
            return apply_paper_event(
                self.store, lifecycle_id=lifecycle_id, event_id=event_id,
                family="FCI", instrument=terms.symbol, currency=terms.currency,
                to_state="SUBSCRIBE_REQUESTED", amount=-subscribed, occurred_at=occurred_at,
                detail=detail, connection=connection,
                _subscription_authority=_FCI_LOCKED_SUBSCRIPTION)

    def future_event(self, contract, *, lifecycle_id, event_id, to_state,
                     amount="0", occurred_at=None, detail=None, connection=None):
        if not isinstance(contract, InstrumentContract) or contract.family != "FUTUROS":
            raise ValueError("FUTURES_CONTRACT_REQUIRED")
        return apply_paper_event(
            self.store, lifecycle_id=lifecycle_id, event_id=event_id,
            family="FUTUROS", instrument=contract.symbol,
            currency=contract.currency, to_state=to_state, amount=amount,
            occurred_at=occurred_at,
            detail={**(detail or {}), "mode": "PRODUCTION_PAPER", "execution": "SIMULATION",
                    "metadata_source": contract.metadata_source,
                    "financial_contract": _contract_snapshot(contract)},
            connection=connection)

    def open_future(self, contract, *, lifecycle_id, event_id, entry_price,
                    quantity, entry_cost="0", occurred_at=None, side="LONG",
                    detail=None, cash_guard=None, admission_guard=None,
                    book_at=None, max_mark_age_seconds=120, commit_evidence=None):
        """Reserve collateral, position and optional evidence in one transaction.

        ``commit_evidence(connection, result)`` runs once for a new fill, after
        all financial rows exist and before commit. Exceptions roll back the
        entire fill. A valid retry never recaptures its original evidence.
        """
        _validate_future_contract(contract)
        if commit_evidence is not None and not callable(commit_evidence):
            raise ValueError("FUTURES_COMMIT_EVIDENCE_CALLABLE_REQUIRED")
        if str(side).upper() != "LONG":
            raise ValueError("FUTURES_PAPER_LONG_ONLY")
        stamp = aware_datetime(occurred_at or _now())
        native_book = aware_datetime(book_at or stamp)
        price = contract.price(entry_price, price_kind="FILL")
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
                _future_row_matches_contract(row, contract)
                if occurred_at is None:
                    stamp = aware_datetime(row["opened_at"])
                    native_book = aware_datetime(book_at or json.loads(
                        row["metadata_json"])["opening_book_at"])
                expected = (contract.symbol, contract.currency, contract.market,
                            contract.settlement, qty, price)
                observed = (row["symbol"], row["currency"], row["market"],
                            row["settlement"], Decimal(row["quantity"]), Decimal(row["entry_price"]))
                if expected != observed:
                    raise ValueError("FUTURES_LIFECYCLE_ID_COLLISION")
                if (Decimal(row["entry_cost"]) != cost
                        or aware_datetime(row["opened_at"]) != stamp):
                    raise ValueError("FUTURES_OPEN_IDEMPOTENCY_MISMATCH")
                prior = connection.execute(
                    "SELECT * FROM paper_family_lifecycle_events WHERE event_id=?",
                    (str(event_id),)).fetchone()
                if not prior or prior["lifecycle_id"] != str(lifecycle_id) or prior["to_state"] != "OPEN":
                    raise ValueError("FUTURES_OPEN_IDEMPOTENCY_MISMATCH")
                prior_detail = json.loads(prior["detail_json"])
                request = _future_request(
                    contract, operation="OPEN", occurred_at=stamp, book_at=native_book,
                    detail=detail, entry_price=price, quantity=qty, entry_cost=cost, side="LONG")
                _assert_future_request(prior_detail, request, "FUTURES_OPEN_IDEMPOTENCY_MISMATCH")
                if (utc_microseconds(prior_detail.get("opening_book_at")) != utc_microseconds(native_book)
                        or ("paper_request_v1" not in prior_detail
                            and _canonical_json(detail or {}) != _canonical_json({
                                key: value for key, value in prior_detail.items()
                                if key not in _OPEN_SYSTEM_FIELDS}))):
                    raise ValueError("FUTURES_OPEN_IDEMPOTENCY_MISMATCH")
                return _future_result(row, idempotent=True)

            if not _within_mark_age(stamp, native_book, max_mark_age_seconds):
                raise ValueError("FUTURES_OPEN_BOOK_STALE_OR_FUTURE")
            if utc_microseconds(stamp) >= utc_microseconds(contract.expires_at):
                raise ValueError("FUTURE_EXPIRED")

            if connection.execute("""SELECT 1 FROM paper_future_positions
                WHERE symbol=? AND currency=? AND market=? AND settlement=? AND status='ACTIVE'""",
                (contract.symbol, contract.currency, contract.market, contract.settlement)).fetchone():
                raise ValueError("FUTURES_POSITION_ALREADY_OPEN")

            if cash_guard is not None:
                available = decimal_value(
                    cash_guard(connection), "caja futura disponible", nonnegative=True)
                if available < reserve + cost:
                    raise ValueError("FUTURES_INSUFFICIENT_CASH")
            if admission_guard is not None:
                blocked = str(admission_guard(connection) or "")
                if blocked:
                    raise ValueError(blocked)
            # The executor is also callable directly. The normal broker passes
            # a locked guard, but a missing callback must not bypass the global
            # receipt when this is the effective PAPER runtime database.
            try:
                from cg_paper_workspace import database_path
                runtime_book = Path(self.store.path).resolve() == database_path()
            except (OSError, ValueError, TypeError) as exc:
                raise ValueError("PAPER_PREOPEN_GATE_UNAVAILABLE") from exc
            if runtime_book:
                if admission_guard is None:
                    raise ValueError("PAPER_ADMISSION_GUARD_MISSING")
                try:
                    from rc6_paper_admission_gate import admission_status
                    allowed, reason = admission_status()
                except Exception as exc:
                    raise ValueError("PAPER_PREOPEN_GATE_UNAVAILABLE") from exc
                if not allowed:
                    raise ValueError(reason or "NO_GO_PAPER_SCALPING")
            base_detail = {
                **(detail or {}),
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
                "financial_contract": _contract_snapshot(contract),
                "contract_snapshot_sha256": _contract_digest(contract),
                "opening_book_at": native_book.isoformat(),
                "price_kind": "FILL",
                "execution_price_terms": json.loads(_canonical_json(contract.execution_price_terms())),
                "paper_request_v1": _future_request(
                    contract, operation="OPEN", occurred_at=stamp, book_at=native_book,
                    detail=detail, entry_price=price, quantity=qty, entry_cost=cost, side="LONG"),
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
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'0','0','0',?,?,?,?,'ACTIVE',NULL,NULL,?)""",
              (str(lifecycle_id), contract.symbol, contract.currency, contract.market,
               contract.settlement, "LONG", str(qty), str(contract.cash_multiplier),
               str(price), str(price), str(price), str(reserve), str(cost),
               stamp.isoformat(), stamp.isoformat(), native_book.isoformat(),
               contract.expires_at, _canonical_json(base_detail)))
            row = dict(connection.execute(
                "SELECT * FROM paper_future_positions WHERE lifecycle_id=?",
                (str(lifecycle_id),)).fetchone())
            result = _future_result(row, idempotent=False)
            if commit_evidence is not None:
                commit_evidence(connection, result)
        return result

    def mark_future(self, contract, *, lifecycle_id, event_id, mark_price,
                    book_at, occurred_at=None, settlement=False, detail=None,
                    max_mark_age_seconds=120, price_kind=None, price_source=None,
                    price_rule=None):
        """Persist a fresh mark; settlement=True applies explicit daily variation."""
        _validate_future_contract(contract)
        stamp = aware_datetime(occurred_at or _now())
        source_at = aware_datetime(book_at, "book futuro")
        kind = str(price_kind or ("PAPER_SETTLEMENT" if settlement else "BOOK_MARK")).upper()
        if kind not in ({"PAPER_SETTLEMENT", "OFFICIAL_SETTLEMENT"} if settlement else
                        {"BOOK_MARK", "OFFICIAL_MARK"}):
            raise ValueError("FUTURES_MARK_PRICE_KIND_INVALID")
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            register_exact_time_sql(connection)
            prior_mark = connection.execute(
                "SELECT * FROM paper_future_marks WHERE event_id=? OR "
                "(lifecycle_id=? AND rc6_instant_us(book_at)=? AND is_settlement=?)",
                (str(event_id), str(lifecycle_id), utc_microseconds(source_at), int(settlement))).fetchone()
            if prior_mark and occurred_at is None:
                stamp = aware_datetime(prior_mark["observed_at"])
            if utc_microseconds(source_at) > utc_microseconds(stamp):
                raise ValueError("FUTURES_BOOK_TIME_FUTURE")
            if not _within_mark_age(stamp, source_at, max_mark_age_seconds):
                raise ValueError("FUTURES_BOOK_STALE")
            price = contract.price(mark_price, price_kind=kind, source=price_source,
                                   rule=price_rule, at=stamp)
            request = _future_request(
                contract, operation="MARK", occurred_at=stamp, book_at=source_at,
                detail=detail, mark_price=price, settlement=bool(settlement),
                price_kind=kind, price_source=price_source, price_rule=price_rule)
            if prior_mark:
                prior_position = connection.execute(
                    "SELECT * FROM paper_future_positions WHERE lifecycle_id=?",
                    (str(lifecycle_id),)).fetchone()
                if not prior_position or prior_mark["lifecycle_id"] != str(lifecycle_id):
                    raise ValueError("FUTURES_MARK_EVENT_COLLISION")
                row = dict(prior_position)
                _future_row_matches_contract(row, contract)
                if (prior_mark["lifecycle_id"] != str(lifecycle_id)
                        or Decimal(prior_mark["mark_price"]) != price
                        or aware_datetime(prior_mark["book_at"]) != source_at
                        or aware_datetime(prior_mark["observed_at"]) != stamp
                        or bool(prior_mark["is_settlement"]) != bool(settlement)):
                    raise ValueError("FUTURES_MARK_EVENT_COLLISION")
                prior_detail = json.loads(prior_mark["detail_json"])
                _assert_future_request(prior_detail, request, "FUTURES_MARK_EVENT_COLLISION")
                if ("paper_request_v1" not in prior_detail
                        and _canonical_json(prior_detail) != _canonical_json(detail or {})):
                    raise ValueError("FUTURES_MARK_EVENT_COLLISION")
                return _future_result(row, idempotent=True)
            if connection.execute(
                    "SELECT 1 FROM paper_family_lifecycle_events WHERE event_id=?",
                    (str(event_id),)).fetchone():
                raise ValueError("FUTURES_MARK_EVENT_COLLISION")
            row = connection.execute(
                "SELECT * FROM paper_future_positions WHERE lifecycle_id=?",
                (str(lifecycle_id),)).fetchone()
            if not row or row["status"] != "ACTIVE":
                raise ValueError("FUTURES_ACTIVE_POSITION_REQUIRED")
            row = dict(row)
            _future_row_matches_contract(row, contract)
            if (utc_microseconds(stamp) < utc_microseconds(row["last_mark_at"])
                    or utc_microseconds(source_at) < utc_microseconds(row["last_book_at"])):
                raise ValueError("FUTURES_CLOCK_ROLLBACK")
            if (utc_microseconds(stamp) < utc_microseconds(row["opened_at"])
                    or utc_microseconds(source_at) < utc_microseconds(row["opened_at"])):
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
                            "source": str(price_source or (detail or {}).get("source") or "EXPLICIT_PAPER_SETTLEMENT"),
                            "price_kind": kind, "price_rule": price_rule,
                            "paper_request_v1": request,
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
               _canonical_json({**(detail or {}), "price_kind": kind,
                                "price_source": price_source, "price_rule": price_rule,
                                "paper_request_v1": request})))
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
                     exit_cost="0", detail=None, max_mark_age_seconds=120,
                     price_kind="FILL", price_source=None, price_rule=None):
        """Release PAPER collateral and realize the final marked variation."""
        _validate_future_contract(contract)
        stamp = aware_datetime(occurred_at or _now())
        source_at = aware_datetime(book_at, "book futuro")
        kind = str(price_kind).upper()
        if kind != "FILL" and not (expiry and kind in {"OFFICIAL_SETTLEMENT", "PAPER_SETTLEMENT"}):
            raise ValueError("FUTURES_EXIT_PRICE_KIND_INVALID")
        cost = decimal_value(exit_cost, "costo salida", nonnegative=True)
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                "SELECT * FROM paper_family_lifecycle_events WHERE event_id=?",
                (str(event_id),)).fetchone()
            row = connection.execute(
                "SELECT * FROM paper_future_positions WHERE lifecycle_id=?",
                (str(lifecycle_id),)).fetchone()
            if not row:
                raise ValueError("FUTURES_ACTIVE_POSITION_REQUIRED")
            row = dict(row)
            if prior and occurred_at is None:
                stamp = aware_datetime(prior["occurred_at"])
            if utc_microseconds(source_at) > utc_microseconds(stamp):
                raise ValueError("FUTURES_BOOK_TIME_FUTURE")
            if not _within_mark_age(stamp, source_at, max_mark_age_seconds):
                raise ValueError("FUTURES_BOOK_STALE")
            price = contract.price(exit_price, price_kind=kind, source=price_source,
                                   rule=price_rule, at=stamp)
            request = _future_request(
                contract, operation="CLOSE", occurred_at=stamp, book_at=source_at,
                detail=detail, exit_price=price, exit_cost=cost, reason=str(reason),
                expiry=bool(expiry), price_kind=kind, price_source=price_source,
                price_rule=price_rule)
            if prior:
                _future_row_matches_contract(row, contract)
                prior_detail = json.loads(prior["detail_json"])
                if (prior["lifecycle_id"] != str(lifecycle_id)
                        or prior["to_state"] != ("EXPIRY" if expiry else "CLOSE")
                        or Decimal(prior_detail.get("exit_price", "-1")) != price
                        or Decimal(prior_detail.get("exit_cost", "-1")) != cost
                        or aware_datetime(prior_detail.get("book_at")) != source_at
                        or aware_datetime(prior["occurred_at"]) != stamp
                        or prior_detail.get("reason") != str(reason)):
                    raise ValueError("FUTURES_CLOSE_EVENT_COLLISION")
                _assert_future_request(prior_detail, request, "FUTURES_CLOSE_EVENT_COLLISION")
                if ("paper_request_v1" not in prior_detail and _canonical_json(detail or {}) !=
                        _canonical_json({key: value for key, value in prior_detail.items()
                                         if key not in _CLOSE_SYSTEM_FIELDS})):
                    raise ValueError("FUTURES_CLOSE_EVENT_COLLISION")
                return _future_result(row, idempotent=True)
            if connection.execute("SELECT 1 FROM paper_future_marks WHERE event_id=?",
                                  (str(event_id),)).fetchone():
                raise ValueError("FUTURES_CLOSE_EVENT_COLLISION")
            if row["status"] != "ACTIVE":
                raise ValueError("FUTURES_ACTIVE_POSITION_REQUIRED")
            _future_row_matches_contract(row, contract)
            if (utc_microseconds(stamp) < utc_microseconds(row["last_mark_at"])
                    or utc_microseconds(source_at) < utc_microseconds(row["last_book_at"])):
                raise ValueError("FUTURES_CLOCK_ROLLBACK")
            if expiry and utc_microseconds(stamp) < utc_microseconds(contract.expires_at):
                raise ValueError("FUTURES_EXPIRY_NOT_DUE")
            if (utc_microseconds(stamp) < utc_microseconds(row["opened_at"])
                    or utc_microseconds(source_at) < utc_microseconds(row["opened_at"])):
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
                detail={**(detail or {}), "exit_price": str(price), "final_variation": str(final_variation),
                        "gross_realized_pnl": str(gross_realized),
                        "net_realized_pnl": str(net_realized),
                        "margin_released": str(reserve), "exit_cost": str(cost),
                        "reason": str(reason), "book_at": source_at.isoformat(),
                        "price_kind": kind, "price_source": price_source,
                        "price_rule": price_rule, "paper_request_v1": request,
                        "real_routes_used": []},
                connection=connection)
            connection.execute("""INSERT INTO paper_future_marks
              (event_id,lifecycle_id,mark_price,book_at,observed_at,
               is_settlement,unrealized_pnl,detail_json)
              VALUES(?,?,?,?,?,0,'0',?)
              ON CONFLICT(lifecycle_id,book_at,is_settlement) DO NOTHING""",
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
        BEGIN IMMEDIATE;
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
        CREATE INDEX IF NOT EXISTS idx_paper_family_events_family_lifecycle
          ON paper_family_lifecycle_events(family,lifecycle_id);
        CREATE TABLE IF NOT EXISTS paper_family_schema(
          schema_key TEXT PRIMARY KEY, version INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS paper_future_positions(
          lifecycle_id TEXT PRIMARY KEY REFERENCES paper_family_lifecycle(lifecycle_id),
          symbol TEXT NOT NULL, currency TEXT NOT NULL, market TEXT NOT NULL,
          settlement TEXT NOT NULL, side TEXT NOT NULL CHECK(side='LONG'),
          quantity TEXT NOT NULL, cash_multiplier TEXT NOT NULL,
          entry_price TEXT NOT NULL, settlement_base_price TEXT NOT NULL,
          last_mark_price TEXT NOT NULL, margin_reserved TEXT NOT NULL,
          entry_cost TEXT NOT NULL, exit_cost TEXT NOT NULL,
          variation_realized TEXT NOT NULL, unrealized_pnl TEXT NOT NULL,
          opened_at TEXT NOT NULL, last_mark_at TEXT, last_book_at TEXT,
          expires_at TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('ACTIVE','CLOSED')),
          closed_at TEXT, close_reason TEXT, metadata_json TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_future_active_identity
          ON paper_future_positions(symbol,currency,market,settlement) WHERE status='ACTIVE';
        CREATE TABLE IF NOT EXISTS paper_future_marks(
          event_id TEXT PRIMARY KEY,
          lifecycle_id TEXT NOT NULL REFERENCES paper_future_positions(lifecycle_id),
          mark_price TEXT NOT NULL, book_at TEXT NOT NULL, observed_at TEXT NOT NULL,
          is_settlement INTEGER NOT NULL CHECK(is_settlement IN (0,1)),
          unrealized_pnl TEXT NOT NULL, detail_json TEXT NOT NULL,
          UNIQUE(lifecycle_id,book_at,is_settlement));
        CREATE INDEX IF NOT EXISTS idx_future_marks_asof
          ON paper_future_marks(lifecycle_id,observed_at,book_at);
        INSERT OR REPLACE INTO paper_family_schema VALUES('FCI_FUTURES',1);
        COMMIT;
        """)
    store._paper_family_schema_ready = True


def ensure_initialized(store):
    """Bootstrap once; runtime children verify the parent's migration read-only."""
    if getattr(store, "_paper_family_schema_ready", False):
        return
    if os.getenv("POROTA_RUNTIME_SCHEMA_READY", "").strip() == "1":
        with store.connect() as connection:
            try:
                version = connection.execute(
                    "SELECT version FROM paper_family_schema WHERE schema_key='FCI_FUTURES'").fetchone()
                connection.execute("SELECT lifecycle_id FROM paper_future_positions LIMIT 0")
                connection.execute("SELECT event_id FROM paper_future_marks LIMIT 0")
            except __import__('sqlite3').OperationalError as exc:
                raise RuntimeError("FUTURES_PARENT_SCHEMA_REQUIRED") from exc
            if not version or version[0] != 1:
                raise RuntimeError("FUTURES_PARENT_SCHEMA_REQUIRED")
        store._paper_family_schema_ready = True
        return
    init_schema(store)


def apply_paper_event(store, *, lifecycle_id, event_id, family, instrument,
                      currency, to_state, amount="0", occurred_at=None,
                      detail=None, connection=None, _subscription_authority=None,
                      _admin_cancel_authority=None):
    """Apply one idempotent PAPER transition or fail closed.

    A repeated event_id returns the original result without a second ledger
    mutation. No transition calls a broker or infers NAV/margin/settlement.
    """
    family = str(family or "").upper()
    to_state = str(to_state or "").upper()
    if family not in TRANSITIONS:
        raise ValueError("PAPER_LIFECYCLE_FAMILY_UNSUPPORTED")
    if (family == 'FUTUROS' and to_state == 'ADMIN_CANCEL'
            and _admin_cancel_authority is not _FUTURE_LOCKED_ADMIN_CANCEL):
        raise ValueError('FUTURES_ADMIN_DIRECT_TRANSITION_FORBIDDEN')
    if connection is None:
        ensure_initialized(store)
        with store.connect() as owned:
            owned.execute("BEGIN IMMEDIATE")
            return apply_paper_event(
                store, lifecycle_id=lifecycle_id, event_id=event_id,
                family=family, instrument=instrument, currency=currency,
                to_state=to_state, amount=amount, occurred_at=occurred_at,
                detail=detail, connection=owned,
                _subscription_authority=_subscription_authority,
                _admin_cancel_authority=_admin_cancel_authority)
    existing_event = connection.execute(
        "SELECT * FROM paper_family_lifecycle_events WHERE event_id=?",
        (str(event_id),),
    ).fetchone()
    if family == "FCI" and to_state in {"SUBSCRIBE", "SUBSCRIBE_REQUESTED"} and not existing_event:
        try:
            from cg_paper_workspace import database_path
            runtime_book = Path(store.path).resolve() == database_path()
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError("PAPER_PREOPEN_GATE_UNAVAILABLE") from exc
        if runtime_book and _subscription_authority is not _FCI_LOCKED_SUBSCRIPTION:
            raise ValueError("FCI_DIRECT_RUNTIME_SUBSCRIPTION_BLOCKED")
    # Retrying an implicitly timestamped request reuses its original instant.
    # Supplying another explicit instant remains a conflicting intention.
    stamp = aware_datetime(occurred_at or (existing_event["occurred_at"]
                                         if existing_event else _now())).isoformat()
    currency = cash_currency(currency)
    delta = decimal_value(amount, "PAPER_LIFECYCLE_AMOUNT_INVALID")
    if detail is not None and not isinstance(detail, dict):
        raise ValueError("PAPER_LIFECYCLE_DETAIL_INVALID")
    payload = _canonical_json(detail or {})
    current = connection.execute(
        "SELECT * FROM paper_family_lifecycle WHERE lifecycle_id=?",
        (str(lifecycle_id),),
    ).fetchone()
    if existing_event:
        row = dict(existing_event)
        original = connection.execute(
            "SELECT instrument,currency FROM paper_family_lifecycle WHERE lifecycle_id=?",
            (row["lifecycle_id"],)).fetchone()
        if not original:
            raise ValueError("PAPER_LIFECYCLE_EVENT_ID_COLLISION")
        requested = intent_fingerprint(
            lifecycle_id=lifecycle_id, family=family, instrument=instrument,
            currency=currency, to_state=to_state, amount=delta,
            occurred_at=stamp, detail=json.loads(payload))
        stored = intent_fingerprint(
            lifecycle_id=row["lifecycle_id"], family=row["family"],
            instrument=original["instrument"], currency=original["currency"],
            to_state=row["to_state"], amount=row["amount"],
            occurred_at=row["occurred_at"], detail=json.loads(row["detail_json"]))
        if requested != stored:
            raise ValueError("PAPER_LIFECYCLE_EVENT_ID_COLLISION")
        return {"idempotent": True, "state": row["to_state"],
                "real_routes_used": [], "paper_only": True}

    from_state = current["state"] if current else None
    if current and (current["family"] != family
                    or current["instrument"] != str(instrument)
                    or current["currency"] != str(currency).upper()):
        raise ValueError("PAPER_LIFECYCLE_IDENTITY_MISMATCH")
    if to_state not in TRANSITIONS[family].get(from_state, set()):
        raise ValueError(f"PAPER_LIFECYCLE_INVALID_TRANSITION:{from_state}->{to_state}")

    previous = decimal_value(current["ledger_total"], "PAPER_LIFECYCLE_AMOUNT_INVALID") if current else Decimal("0")
    if current and utc_microseconds(current["updated_at"]) > utc_microseconds(stamp):
        raise ValueError("PAPER_LIFECYCLE_CLOCK_ROLLBACK")
    total = previous + delta
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
    ensure_initialized(store)
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
    from rc6_ppi_future_contract_policy import standard_dlr_terms
    terms = standard_dlr_terms(contract.symbol)
    if (not terms or contract.currency != "ARS" or contract.settlement != "INMEDIATA"
            or contract.cash_multiplier != Decimal("1000")
            or contract.quantity_step != Decimal("1")
            or contract.minimum_quantity != Decimal("1")
            or contract.initial_margin is not None
            or contract.underlying != terms["underlying"]
            or aware_datetime(contract.expires_at) != aware_datetime(terms["expires_at"])):
        raise ValueError("FUTURES_EXACT_STANDARD_DLR_REQUIRED")
    if (contract.price_tick is not None and (
            contract.price_tick != Decimal(terms["price_tick"])
            or contract.price_tick_source != terms["price_tick_source"]
            or (contract.price_tick_known_at is not None and
                aware_datetime(contract.price_tick_known_at) != aware_datetime(terms["price_tick_known_at"]))
            or contract.price_tick_effective_at is not None)):
        raise ValueError("FUTURES_STANDARD_DLR_PRICE_RULE_MISMATCH")


def _contract_snapshot(contract):
    return {key: str(value) if isinstance(value, Decimal) else value
            for key, value in asdict(contract).items()}


def _contract_digest(contract):
    return hashlib.sha256(json.dumps(_contract_snapshot(contract), sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def future_position_contract(row):
    """Exit authority is the immutable, digest-checked contract captured at OPEN."""
    metadata = json.loads(row["metadata_json"])
    snapshot = metadata.get("financial_contract")
    if not isinstance(snapshot, dict):
        raise ValueError("FUTURES_DURABLE_CONTRACT_REQUIRED")
    # Validate exactly the bytes/fields captured at OPEN before adding defaults
    # introduced by a later dataclass version. No stored digest is rewritten.
    raw_digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True,
                                          separators=(",", ":")).encode()).hexdigest()
    if metadata.get("contract_snapshot_sha256") != raw_digest:
        raise ValueError("FUTURES_CONTRACT_SNAPSHOT_DIGEST_MISMATCH")
    from bs_instrument_contracts import contract_from_metadata
    contract = contract_from_metadata(row["symbol"], "FUTUROS", snapshot)
    _validate_future_contract(contract)
    if (contract.key != (row["symbol"], "FUTUROS", row["market"], row["currency"], row["settlement"])
            or contract.cash_multiplier != Decimal(row["cash_multiplier"])
            or aware_datetime(contract.expires_at) != aware_datetime(row["expires_at"])):
        raise ValueError("FUTURES_CONTRACT_POSITION_MISMATCH")
    return contract


def _future_row_matches_contract(row, contract):
    stored = future_position_contract(row)
    price_fields = {"price_tick", "price_tick_source", "price_tick_known_at", "price_tick_effective_at"}
    stored_terms = {key: value for key, value in _contract_snapshot(stored).items() if key not in price_fields}
    requested_terms = {key: value for key, value in _contract_snapshot(contract).items() if key not in price_fields}
    if stored_terms != requested_terms:
        raise ValueError("FUTURES_CONTRACT_POSITION_MISMATCH")
    _validate_future_contract(contract)
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


def future_cash_effect(store, currency, at, *, connection=None, exclusive=False):
    """Cash movement only: collateral reserve/release, variation and fees."""
    point = aware_datetime(at)
    if connection is None:
        ensure_initialized(store)
        with store.connect() as owned:
            return future_cash_effect(store, currency, point, connection=owned, exclusive=exclusive)
    required = {"paper_family_lifecycle_events", "paper_family_lifecycle"}
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if not required.issubset(tables):
        raise RuntimeError("FUTURES_PARENT_SCHEMA_REQUIRED")
    register_exact_time_sql(connection)
    operator = "<" if exclusive else "<="
    rows = connection.execute("""SELECT amount FROM paper_family_lifecycle_events
      WHERE family='FUTUROS' AND EXISTS(
        SELECT 1 FROM paper_family_lifecycle l
        WHERE l.lifecycle_id=paper_family_lifecycle_events.lifecycle_id
          AND l.currency=?
      ) AND rc6_instant_us(occurred_at)""" + operator + "?",
      (cash_currency(currency), utc_microseconds(point)))
    return sum((decimal_value(row[0], "flujo futuro") for row in rows), Decimal("0"))


FUTURE_ADMIN_CANCEL_AUTHORITY_A207 = 'ISSUE773_A207_PAPER_ACCOUNTING_REVERSAL_20261030'
FUTURE_ADMIN_CANCEL_REASON = 'ADMIN_CANCEL_NO_MARKET_EXECUTION'


def plan_open_future_admin(connection, lifecycle_id, at):
    """Check the *individual* immutable OPEN and MARGIN funding events.

    Matching only the aggregate cash can accept a malicious positive OPEN
    event plus an offsetting margin event. Neither a non-PAPER transition nor
    a broker-routed detail may become administrative buying power.
    """
    at = aware_datetime(at)
    row = connection.execute('SELECT * FROM paper_future_positions WHERE lifecycle_id=?',
                             (str(lifecycle_id),)).fetchone()
    if row is None or row['status'] != 'ACTIVE':
        raise ValueError('FUTURES_ADMIN_ACTIVE_POSITION_REQUIRED')
    contract = future_position_contract(row)
    events = connection.execute('''SELECT * FROM paper_family_lifecycle_events
        WHERE lifecycle_id=? ORDER BY rowid''', (str(lifecycle_id),)).fetchall()
    parent = connection.execute('''SELECT * FROM paper_family_lifecycle
        WHERE lifecycle_id=?''', (str(lifecycle_id),)).fetchone()
    if not parent or parent['family'] != 'FUTUROS' or (
            parent['instrument'], parent['currency']) != (row['symbol'], row['currency']):
        raise ValueError('FUTURES_ADMIN_LIFECYCLE_IDENTITY_INVALID')
    if (parent['state'] != 'MARGIN_RESERVED'
            or utc_microseconds(at) < max(utc_microseconds(row['opened_at']),
                                           utc_microseconds(parent['updated_at']),
                                           utc_microseconds(row['last_mark_at']))):
        raise ValueError('FUTURES_ADMIN_INVALID_STATE_OR_CLOCK')
    if connection.execute('''SELECT 1 FROM paper_future_marks
            WHERE lifecycle_id=? AND is_settlement=1 LIMIT 1''',
            (str(lifecycle_id),)).fetchone():
        raise ValueError('FUTURES_ADMIN_OFFICIAL_OR_SETTLEMENT_MARK_PRESENT')
    if (len(events) != 2 or events[0]['from_state'] is not None
            or events[0]['to_state'] != 'OPEN'
            or events[1]['from_state'] != 'OPEN'
            or events[1]['to_state'] != 'MARGIN_RESERVED'
            or any(e['family'] != 'FUTUROS' for e in events)):
        raise ValueError('FUTURES_ADMIN_OPEN_RESERVE_EVENTS_INVALID')
    booked = decimal_value(parent['ledger_total'], 'ledger futuro')
    reserve = decimal_value(row['margin_reserved'], 'garantía', positive=True)
    fee = decimal_value(row['entry_cost'], 'costo futuro', nonnegative=True)
    if (decimal_value(events[0]['amount'], 'OPEN_AMOUNT') != 0
            or decimal_value(events[1]['amount'], 'MARGIN_AMOUNT') != -(reserve + fee)
            or booked != -(reserve + fee)
            or decimal_value(row['variation_realized'], 'variación futuro') != 0):
        raise ValueError('FUTURES_ADMIN_MARGIN_RECONCILIATION_REQUIRED')
    try:
        opening = json.loads(events[0]['detail_json'])
        margin = json.loads(events[1]['detail_json'])
        metadata = json.loads(row['metadata_json'])
    except (ValueError, TypeError) as exc:
        raise ValueError('FUTURES_ADMIN_ORIGINAL_DETAIL_INVALID') from exc
    if not all(isinstance(obj, dict) for obj in (opening, margin, metadata)):
        raise ValueError('FUTURES_ADMIN_ORIGINAL_DETAIL_INVALID')
    original = {key: value for key, value in margin.items() if key != 'cash_effect'}
    if (opening != original or
            any(opening.get(key) != value for key, value in (
                ('mode','PRODUCTION_PAPER'), ('execution','SIMULATION'),
                ('side','LONG'), ('real_routes_used',[]), ('price_kind','FILL'))) or
            opening.get('cash_multiplier') != row['cash_multiplier'] or
            opening.get('quantity') != row['quantity'] or
            opening.get('margin_reserved') != row['margin_reserved'] or
            opening.get('entry_cost') != row['entry_cost'] or
            opening.get('entry_price') != row['entry_price'] or
            decimal_value(margin.get('cash_effect'), 'MARGIN_CASH_EFFECT') != -(reserve + fee) or
            any(metadata.get(key) != value for key, value in opening.items()
                if key not in {'metadata_source'}) or
            opening.get('financial_contract') is None or
            opening.get('contract_snapshot_sha256') != _contract_digest(contract)):
        raise ValueError('FUTURES_ADMIN_ORIGINAL_ROUTE_OR_TERMS_INVALID')
    return contract, booked, reserve, fee, metadata


def cancel_open_future_admin(connection, lifecycle_id, at, authority):
    """Void one *unsettled* PAPER future under the caller's exclusive TX.

    All previously booked PAPER cash events are exactly compensated by one
    append-only event. No synthetic exit price, exchange settlement or PnL is
    invented. The caller must have independently stopped the owned runtime,
    pinned the full book fingerprint and validated an owner authority receipt.
    """
    if authority != FUTURE_ADMIN_CANCEL_AUTHORITY_A207 or not connection.in_transaction:
        raise ValueError('FUTURES_ADMIN_AUTHORITY_OR_TRANSACTION_REQUIRED')
    at = aware_datetime(at)
    contract, booked, reserve, fee, metadata = plan_open_future_admin(
        connection, lifecycle_id, at)
    event_id = 'A207-FUTURE-' + hashlib.sha256(
        (str(lifecycle_id) + '|' + authority).encode()).hexdigest()
    detail = {'authority': authority, 'reason': FUTURE_ADMIN_CANCEL_REASON,
              'cash_reversal': str(-booked), 'margin_released': str(reserve),
              'entry_fee_reversed': str(fee), 'real_routes_used': [],
              'no_exit_price_or_pnl': True}
    apply_paper_event(None, lifecycle_id=lifecycle_id, event_id=event_id,
                      family='FUTUROS', instrument=contract.symbol, currency=contract.currency,
                      to_state='ADMIN_CANCEL', amount=str(-booked),
                      occurred_at=at.isoformat(), detail=detail, connection=connection,
                      _admin_cancel_authority=_FUTURE_LOCKED_ADMIN_CANCEL)
    connection.execute('''UPDATE paper_future_positions SET
        status='CLOSED',closed_at=?,close_reason=?,exit_cost='0',
        variation_realized='0',unrealized_pnl='0',metadata_json=?
        WHERE lifecycle_id=? AND status='ACTIVE' ''',
        (at.isoformat(), FUTURE_ADMIN_CANCEL_REASON,
         _canonical_json({**metadata, 'terminal_state': 'ADMIN_CANCEL',
                          'realized_pnl': '0', 'admin_cancel_event_id': event_id,
                          'admin_cancel_authority': authority}), str(lifecycle_id)))
    # Decimal preserves the original monetary scale: -105.00 + 105.00 is
    # numerically zero but serializes as '0.00', not the literal string '0'.
    # Compare the exact value without discarding significant ledger evidence.
    recorded_total = connection.execute(
        'SELECT ledger_total FROM paper_family_lifecycle WHERE lifecycle_id=?',
        (str(lifecycle_id),)).fetchone()
    if (recorded_total is None or
            decimal_value(recorded_total[0], 'saldo futuro') != Decimal('0')):
        raise ValueError('FUTURES_ADMIN_CASH_NOT_NEUTRAL')
    return {'status': 'CLOSED', 'cancel_event_id': event_id,
            'cash_reversal': str(-booked), 'real_routes_used': []}


def fund_cash_effect(store, currency, at, *, connection=None, exclusive=False):
    """FCI cash committed on request and released only on recorded redemption.

    NAV application changes fund units/value, not settled cash. The legacy
    lifecycle permits nonzero NAV events; counting its ledger_total as cash
    would manufacture buying power. This projection uses immutable events and
    preserves exact currency and event-time boundaries.
    """
    point = aware_datetime(at)
    if connection is None:
        ensure_initialized(store)
        with store.connect() as owned:
            return fund_cash_effect(store, currency, point, connection=owned,
                                    exclusive=exclusive)
    register_exact_time_sql(connection)
    operator = "<" if exclusive else "<="
    rows = connection.execute("""SELECT e.to_state,e.amount FROM paper_family_lifecycle_events e
      JOIN paper_family_lifecycle l ON l.lifecycle_id=e.lifecycle_id
      WHERE e.family='FCI' AND l.family='FCI' AND l.currency=?
        AND e.to_state IN ('SUBSCRIBE_REQUESTED','SUBSCRIBE','REDEEMED')
        AND rc6_instant_us(e.occurred_at)""" + operator + "?",
      (cash_currency(currency), utc_microseconds(point)))
    total = Decimal("0")
    for state, raw_amount in rows:
        amount = decimal_value(raw_amount, "flujo FCI")
        if ((state in {"SUBSCRIBE_REQUESTED", "SUBSCRIBE"} and amount > 0)
                or (state == "REDEEMED" and amount < 0)):
            raise ValueError("FCI_CASH_EVENT_DIRECTION_INVALID")
        total += amount
    return total


FUTURE_PROJECTION_FIELDS = (
    "lifecycle_id", "symbol", "currency", "market", "settlement", "side",
    "quantity", "cash_multiplier", "entry_price", "settlement_base_price",
    "last_mark_price", "margin_reserved", "entry_cost", "exit_cost",
    "variation_realized", "unrealized_pnl", "opened_at", "last_mark_at",
    "last_book_at", "expires_at", "status", "closed_at", "close_reason",
)
FUTURE_PROJECTION_JSON_BYTES = 32768


def _bounded_projection_json_column(column):
    # Only static column names from this module are passed here. Oversized JSON
    # is never transferred into Python, including on a paginated render path.
    return ("CASE WHEN length(CAST(" + column + " AS BLOB))<="
            + str(FUTURE_PROJECTION_JSON_BYTES) + " THEN " + column + " END AS " + column)


def _projection_json(value):
    if value is None or len(value.encode("utf-8")) > FUTURE_PROJECTION_JSON_BYTES:
        raise ValueError("FUTURES_PROJECTION_JSON_BUDGET_EXHAUSTED")
    try:
        result = json.loads(value)
    except (ValueError, TypeError) as exc:
        raise ValueError("FUTURES_PROJECTION_JSON_INVALID") from exc
    if not isinstance(result, dict):
        raise ValueError("FUTURES_PROJECTION_JSON_INVALID")
    return result


def future_positions(store, currency=None, *, connection=None, active_only=False,
                     as_of=None, exclusive=False, lifecycle_ids=None):
    """Canonical rows; an explicit cut reconstructs state from visible events.

    `lifecycle_ids` lets paginated readers project just their SQL-selected page.
    A current CLOSED row cannot remove an ACTIVE position from a historical cut.
    """
    if connection is None:
        ensure_initialized(store)
        with store.connect() as owned:
            return future_positions(store, currency, connection=owned, active_only=active_only,
                                    as_of=as_of, exclusive=exclusive, lifecycle_ids=lifecycle_ids)
    if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_future_positions'"
            ).fetchone():
        raise RuntimeError("FUTURES_PARENT_SCHEMA_REQUIRED")
    where, params = [], []
    if currency is not None:
        where.append("currency=?"); params.append(cash_currency(currency))
    if lifecycle_ids is not None:
        identifiers = tuple(dict.fromkeys(str(value) for value in lifecycle_ids))
        if not identifiers:
            return []
        if len(identifiers) > 500:
            raise ValueError("FUTURES_PROJECTION_ID_BUDGET_EXHAUSTED")
        where.append("lifecycle_id IN (" + ",".join("?" for _ in identifiers) + ")")
        params.extend(identifiers)
    if active_only and as_of is None:
        where.append("status='ACTIVE'")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    fields = ",".join(FUTURE_PROJECTION_FIELDS) + "," + _bounded_projection_json_column("metadata_json")
    rows = [dict(row) for row in connection.execute(
        "SELECT " + fields + " FROM paper_future_positions" + clause + " ORDER BY opened_at,lifecycle_id",
        tuple(params)).fetchall()]
    for row in rows:
        _projection_json(row["metadata_json"])
    if as_of is None:
        return rows
    point = aware_datetime(as_of)
    register_exact_time_sql(connection)
    projected = []
    for row in rows:
        opened = aware_datetime(row["opened_at"])
        if utc_microseconds(opened) > utc_microseconds(point) or (exclusive and utc_microseconds(opened) == utc_microseconds(point)):
            continue
        projected_row = _future_position_at(row, point, connection=connection, exclusive=exclusive)
        if not active_only or projected_row["status"] == "ACTIVE":
            projected.append(projected_row)
    return sorted(projected, key=lambda row: (utc_microseconds(row["opened_at"]), row["lifecycle_id"]))


def _future_position_at(row, point, *, connection, exclusive=False):
    contract = future_position_contract(row)
    operator = "<" if exclusive else "<="
    cutoff = utc_microseconds(point)
    events = connection.execute(
        "SELECT rowid AS event_sequence,event_id,lifecycle_id,family,from_state,to_state,"
        "amount,occurred_at," + _bounded_projection_json_column("detail_json")
        + " FROM paper_family_lifecycle_events WHERE lifecycle_id=? "
        "AND rc6_instant_us(occurred_at)" + operator + "? "
        "ORDER BY rc6_instant_us(occurred_at),event_sequence,event_id",
        (row["lifecycle_id"], cutoff))
    opening = terminal = None
    metadata = None
    variation = cash_effect = Decimal("0")
    base_price = row["entry_price"]
    for event in events:
        detail = _projection_json(event["detail_json"])
        native = detail.get("book_at", detail.get("opening_book_at"))
        if native is not None and utc_microseconds(native) > utc_microseconds(event["occurred_at"]):
            raise ValueError("FUTURES_EVENT_SOURCE_CLOCK_INVALID")
        cash_effect += decimal_value(event["amount"], "flujo futuro")
        if event["to_state"] == "OPEN" and opening is None:
            opening, metadata = event, detail
        elif event["to_state"] == "DAILY_VARIATION":
            variation += decimal_value(event["amount"], "variación futura")
            base_price = detail["settlement_price"]
        elif event["to_state"] in {"CLOSE", "EXPIRY", "ADMIN_CANCEL"}:
            terminal = (event, detail)
    if not opening:
        raise ValueError("FUTURES_OPEN_EVENT_REQUIRED")
    original_metadata = _projection_json(row["metadata_json"])
    if (metadata.get("financial_contract") != original_metadata.get("financial_contract")
            or metadata.get("contract_snapshot_sha256") != original_metadata.get("contract_snapshot_sha256")):
        raise ValueError("FUTURES_OPEN_CONTRACT_EVENT_MISMATCH")
    for field in ("entry_price", "entry_cost", "quantity", "margin_reserved"):
        if decimal_value(metadata.get(field), field) != decimal_value(row[field], field):
            raise ValueError("FUTURES_OPEN_POSITION_EVENT_MISMATCH")
    marks = connection.execute(
        "SELECT rowid AS mark_sequence,event_id,lifecycle_id,mark_price,book_at,observed_at,"
        "is_settlement,unrealized_pnl," + _bounded_projection_json_column("detail_json")
        + " FROM paper_future_marks WHERE lifecycle_id=? "
        "AND rc6_instant_us(observed_at)" + operator + "? "
        "ORDER BY rc6_instant_us(observed_at) DESC,mark_sequence DESC,event_id DESC LIMIT 1",
        (row["lifecycle_id"], cutoff)).fetchone()
    entry = decimal_value(row["entry_price"], "entrada futura", positive=True)
    entry_cost = decimal_value(row["entry_cost"], "costo entrada", nonnegative=True)
    qty = contract.quantity(row["quantity"])
    mark_price = decimal_value(marks["mark_price"], "mark futuro", positive=True) if marks else entry
    book_at = marks["book_at"] if marks else metadata.get("opening_book_at")
    observed_at = marks["observed_at"] if marks else row["opened_at"]
    if not marks and (not row["last_book_at"] or not original_metadata.get("opening_book_at")):
        book_at = None
    if book_at is not None and utc_microseconds(book_at) > utc_microseconds(observed_at):
        raise ValueError("FUTURES_MARK_SOURCE_CLOCK_INVALID")
    if marks and (utc_microseconds(book_at) < utc_microseconds(row["opened_at"])
                  or utc_microseconds(observed_at) < utc_microseconds(row["opened_at"])):
        raise ValueError("FUTURES_MARK_BEFORE_OPEN")
    if marks:
        mark_detail = _projection_json(marks["detail_json"])
        metadata["last_mark_source"] = (mark_detail.get("price_source") or mark_detail.get("source")
                                        or "PPI_BOOK")
        metadata["last_mark_settlement"] = bool(marks["is_settlement"])
        metadata["last_mark_price_kind"] = mark_detail.get("price_kind") or (
            "PAPER_SETTLEMENT" if marks["is_settlement"] else "BOOK_MARK")
    result = {**row, "status": "ACTIVE", "closed_at": None, "close_reason": None,
              "exit_cost": "0", "variation_realized": str(variation),
              "daily_variation_cash": str(variation), "close_variation_cash": "0",
              "settlement_base_price": str(base_price), "last_mark_price": str(mark_price),
              "last_book_at": book_at, "last_mark_at": observed_at,
              "unrealized_pnl": str(contract.pnl(entry, mark_price, qty) - variation),
              "realized_pnl": str(variation - entry_cost),
              "collateral": row["margin_reserved"],
              "exposure": str(contract.notional(mark_price, qty)),
              "as_of": point.isoformat(), "exclusive": bool(exclusive),
              "reserve_kind": "CONSERVATIVE_PAPER_RESERVE_NOT_BROKER_MARGIN"}
    if terminal:
        terminal_event, detail = terminal
        if terminal_event['to_state'] == 'ADMIN_CANCEL':
            if (detail.get('authority') != FUTURE_ADMIN_CANCEL_AUTHORITY_A207
                    or detail.get('reason') != FUTURE_ADMIN_CANCEL_REASON
                    or detail.get('no_exit_price_or_pnl') is not True
                    or detail.get('real_routes_used') != []
                    or cash_effect != 0):
                raise ValueError('FUTURES_ADMIN_EVENT_NOT_RECONCILED')
            metadata.update(realized_pnl='0', terminal_state='ADMIN_CANCEL')
            result.update(status='CLOSED', closed_at=terminal_event['occurred_at'],
                          close_reason=FUTURE_ADMIN_CANCEL_REASON,
                          exit_cost='0', variation_realized='0',
                          gross_realized_pnl='0', close_variation_cash='0',
                          realized_pnl='0', unrealized_pnl='0', collateral='0',
                          exposure='0', admin_reversal_cash=detail['cash_reversal'])
            result['cash_effect'] = str(cash_effect)
            result['metadata_json'] = _canonical_json(metadata)
            return result
        final_variation = decimal_value(detail["final_variation"], "variación cierre")
        exit_cost = decimal_value(detail["exit_cost"], "costo salida", nonnegative=True)
        gross = variation + final_variation
        net = gross - entry_cost - exit_cost
        metadata.update(realized_pnl=str(net), terminal_state=terminal_event["to_state"])
        result.update(status="CLOSED", closed_at=terminal_event["occurred_at"],
                      close_reason=detail["reason"], exit_cost=str(exit_cost),
                      variation_realized=str(gross), gross_realized_pnl=str(gross),
                      close_variation_cash=str(final_variation), realized_pnl=str(net),
                      unrealized_pnl="0", collateral="0", exposure="0",
                      last_mark_price=detail["exit_price"], last_mark_at=terminal_event["occurred_at"],
                      last_book_at=detail["book_at"])
    result["cash_effect"] = str(cash_effect)
    result["metadata_json"] = _canonical_json(metadata)
    _projection_json(result["metadata_json"])
    return result


def future_risk_snapshot(store, currency, at, *, connection=None,
                         max_mark_age_seconds=120, exclusive=False):
    """Reconstruct PnL at the cut from durable events/marks, without future rows."""
    point = aware_datetime(at)
    if connection is None:
        ensure_initialized(store)
        with store.connect() as owned:
            return future_risk_snapshot(
                store, currency, point, connection=owned,
                max_mark_age_seconds=max_mark_age_seconds, exclusive=exclusive)
    local_day = point.astimezone(__import__("zoneinfo").ZoneInfo(
        "America/Argentina/Buenos_Aires")).date()
    realized = unrealized = collateral = exposure = Decimal("0")
    stale = carry = False
    active_count, mark_timestamps = 0, []
    for row in future_positions(store, currency, connection=connection, as_of=point, exclusive=exclusive):
        opened = aware_datetime(row["opened_at"])
        if utc_microseconds(opened) > utc_microseconds(point) or (exclusive and utc_microseconds(opened) == utc_microseconds(point)):
            continue
        closed = aware_datetime(row["closed_at"]) if row["closed_at"] else None
        if opened.astimezone(__import__("zoneinfo").ZoneInfo(
                "America/Argentina/Buenos_Aires")).date() < local_day and (
                closed is None or closed.astimezone(__import__("zoneinfo").ZoneInfo(
                    "America/Argentina/Buenos_Aires")).date() >= local_day):
            carry = True
        realized += decimal_value(row["realized_pnl"], "PnL futuro realizado")
        if row["status"] == "CLOSED":
            continue
        active_count += 1
        collateral += decimal_value(row["margin_reserved"], "garantía", positive=True)
        mark_at = aware_datetime(row["last_book_at"], "book futuro") if row["last_book_at"] else None
        marked_at = aware_datetime(row["last_mark_at"])
        if (mark_at is None or utc_microseconds(mark_at) > utc_microseconds(marked_at)
                or not _within_mark_age(point, mark_at, max_mark_age_seconds)):
            stale = True
        mark_timestamps.append(mark_at.isoformat() if mark_at else None)
        unrealized += decimal_value(row["unrealized_pnl"], "PnL futuro no realizado")
        exposure += decimal_value(row["exposure"], "exposición futura", positive=True)
    return {
        "realized": realized,
        "unrealized": unrealized,
        "collateral": collateral,
        "stale": stale,
        "carry": carry,
        "active_count": active_count,
        "exposure": exposure,
        "mark_timestamps": mark_timestamps,
        "cash_effect": future_cash_effect(store, currency, point, connection=connection, exclusive=exclusive),
    }
