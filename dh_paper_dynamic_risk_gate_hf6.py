"""HF6-v2 bridge between the PAPER ledger and dynamic concurrent-risk policy.

Read/calculation layer only. It never talks to PPI and never writes orders.
It reconstructs partial realizations with the canonical spot ledger so winning
fills cannot hide losing fills when calculating consumed daily risk.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

import cd_spot_ledger as spot_ledger
from bs_instrument_contracts import aware_datetime, cash_currency, decimal_value, family_name
from de_concurrent_risk_capacity_hf6 import capacity, full_trade_stop_risk

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
ZERO = Decimal("0")


class ConcurrentRiskGateError(ValueError):
    pass


def _day_start(at):
    local = aware_datetime(at).astimezone(TZ)
    return datetime.combine(local.date(), time.min, TZ)


def realized_losing_fills_today(broker, currency, at, *, connection) -> Decimal:
    """Sum absolute value of losing realized spot fills/closures for local day.

    `spot_ledger.positions_at` expands partial sales into their economic realized
    lots, so a profitable fill never nets away a losing fill for risk capacity.
    """
    currency = cash_currency(currency)
    at = aware_datetime(at)
    start = _day_start(at)
    _, realized = spot_ledger.positions_at(connection, at)
    consumed = ZERO
    for row in realized:
        if row.get("currency") != currency:
            continue
        closed_at = aware_datetime(row.get("closed_at"))
        if not start <= closed_at <= at:
            continue
        pnl = decimal_value(row.get("net_pnl"), "PnL realizado")
        if pnl < 0:
            consumed += -pnl
    return consumed


def position_stop_risk(broker, position) -> Decimal:
    """Full remaining-position risk using the financial family's loss contract.

    For a long option the loss is not the modeled stop distance: the premium
    can gap to zero. Concurrent risk therefore reserves the entire premium
    paid plus entry costs. Spot families retain the stop-loss model.
    """
    qty = decimal_value(position.get("quantity"), "cantidad", positive=True)
    entry = decimal_value(position.get("entry_price"), "entrada", positive=True)
    entry_cost = decimal_value(position.get("entry_cost"), "costo entrada", nonnegative=True)
    factor = broker._position_multiplier(position)
    family = family_name(position.get("asset_class"))
    if family == "OPCIONES":
        return entry * qty * factor + entry_cost
    stop = decimal_value(position.get("stop_price"), "stop", positive=True)
    modeled_stop_fill = (stop * (Decimal("1") - broker.slippage)).quantize(Decimal("0.0001"))
    exit_cost = broker._cost(modeled_stop_fill * factor, qty, position.get("asset_class"))
    return full_trade_stop_risk(
        entry_price=entry,
        modeled_stop_fill=modeled_stop_fill,
        quantity=qty,
        cash_multiplier=factor,
        entry_cost=entry_cost,
        modeled_exit_cost=exit_cost,
    )


def open_stop_risk(broker, currency, at, *, connection) -> Decimal:
    currency = cash_currency(currency)
    opened, _ = spot_ledger.positions_at(connection, aware_datetime(at))
    return sum((position_stop_risk(broker, p) for p in opened if p.get("currency") == currency), ZERO)


def candidate_stop_risk(broker, *, entry_price, stop_price, quantity,
                        cash_multiplier, asset_class) -> Decimal:
    qty = decimal_value(quantity, "cantidad candidata", positive=True)
    entry = decimal_value(entry_price, "entrada candidata", positive=True)
    factor = decimal_value(cash_multiplier, "multiplicador candidato", positive=True)
    entry_cost = broker._cost(entry * factor, qty, asset_class)
    if family_name(asset_class) == "OPCIONES":
        return entry * qty * factor + entry_cost
    stop = decimal_value(stop_price, "stop candidato", positive=True)
    modeled_stop_fill = (stop * (Decimal("1") - broker.slippage)).quantize(Decimal("0.0001"))
    exit_cost = broker._cost(modeled_stop_fill * factor, qty, asset_class)
    return full_trade_stop_risk(
        entry_price=entry,
        modeled_stop_fill=modeled_stop_fill,
        quantity=qty,
        cash_multiplier=factor,
        entry_cost=entry_cost,
        modeled_exit_cost=exit_cost,
    )


def portfolio_capacity(broker, currency, at, *, candidate_risk=ZERO,
                       connection=None, quotes=None):
    """Return canonical concurrent-risk snapshot for one cash currency.

    DailyRisk remains the owner of baseline/soft-stop state. This function only
    combines that persisted/evaluated baseline with realized losing fills and
    stop risk of the remaining open book.
    """
    if broker.daily_risk is None:
        raise ConcurrentRiskGateError("CONCURRENT_RISK_NOT_CONFIGURED")
    if connection is None:
        with broker.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                return portfolio_capacity(
                    broker, currency, at, candidate_risk=candidate_risk,
                    connection=c, quotes=quotes)
            except ConcurrentRiskGateError:
                # DailyRisk is allowed to persist a fail-closed latch, its audit
                # event and transactional notification before this read-side
                # capacity API reports the blocking state to its caller. Without
                # this explicit commit, sqlite's context manager would roll back
                # the safety transition merely because the gate correctly raised.
                c.commit()
                raise
    c = connection
    currency = cash_currency(currency)
    at = aware_datetime(at)
    risk_row = broker.daily_risk.evaluate(at, connection=c, quotes=quotes)[currency]
    if risk_row.get("state") != "READY":
        reason = "DAILY_RISK_" + str(risk_row.get("state") or "UNKNOWN")
        if risk_row.get("state") == "CLOCK_ROLLBACK":
            reason += (f" input_at={risk_row.get('input_at')} "
                       f"latest_evaluated_at={risk_row.get('latest_evaluated_at')}")
        raise ConcurrentRiskGateError(reason)
    baseline = risk_row.get("baseline_equity")
    if baseline in (None, ""):
        raise ConcurrentRiskGateError("CONCURRENT_RISK_BASELINE_UNAVAILABLE")
    realized_loss = realized_losing_fills_today(broker, currency, at, connection=c)
    open_risk = open_stop_risk(broker, currency, at, connection=c)
    return capacity(
        baseline_equity=baseline,
        soft_stop_pct=broker.daily_risk.soft_limit_pct,
        realized_loss_consumed=realized_loss,
        open_stop_risk=open_risk,
        candidate_stop_risk=candidate_risk,
    )


def snapshot_dict(snapshot) -> dict:
    return {key: str(value) if isinstance(value, Decimal) else value
            for key, value in asdict(snapshot).items()}


def assert_dynamic_gate_invariants() -> None:
    if realized_losing_fills_today.__doc__ is None:
        raise AssertionError("ledger-based realized-loss policy missing")
