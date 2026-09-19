"""Read-only daily operation summaries for the HF6-v2 dashboard.

Purpose
-------
Answer the operator's first question after each wheel: did the PAPER book gain
or lose, by how much, in which currency, and what was operated?

Invariants
----------
- never adds monetary values from different currencies;
- daily return is calculated only from a persisted positive daily baseline;
- no missing PnL/baseline is invented as zero;
- fills and cauciones are descriptive evidence only;
- this module has no broker/network/order capability and never mutates SQLite.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from bs_instrument_contracts import aware_datetime
from ak_byma_calendar import es_dia_habil_operativo

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def _tables(connection) -> set[str]:
    return {str(r[0]) for r in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}


def _local_day(value) -> str | None:
    if not value:
        return None
    try:
        return aware_datetime(value).astimezone(TZ).date().isoformat()
    except (ValueError, TypeError):
        return None


def _decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _return_pct(pnl, baseline) -> Decimal | None:
    pnl = _decimal(pnl)
    baseline = _decimal(baseline)
    if pnl is None or baseline is None or baseline <= 0:
        return None
    return (pnl / baseline * Decimal("100")).quantize(Decimal("0.0001"))


def _day_state(results: dict[str, dict]) -> str:
    values = [_decimal(row.get("daily_pnl")) for row in results.values()]
    values = [v for v in values if v is not None]
    if not values:
        return "SIN_VALUACION"
    signs = {1 if v > 0 else -1 if v < 0 else 0 for v in values}
    nonzero = {s for s in signs if s}
    if nonzero == {1}:
        return "GANANCIA"
    if nonzero == {-1}:
        return "PERDIDA"
    if nonzero == {1, -1}:
        return "MIXTO_POR_MONEDA"
    return "NEUTRO"


def _load_risk(connection, limit_days: int) -> tuple[list[str], dict[str, dict[str, dict]]]:
    rows = [dict(r) for r in connection.execute(
        """SELECT day,currency,baseline_equity,last_equity,daily_pnl,state,evaluated_at,detail
           FROM paper_daily_risk ORDER BY day DESC,currency"""
    )]
    days = []
    by_day: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        day = str(row.get("day") or "")
        if not day:
            continue
        try:
            if not es_dia_habil_operativo(datetime.fromisoformat(day).date()):
                continue
        except ValueError:
            continue
        if day not in by_day:
            days.append(day)
            if len(days) > limit_days:
                break
        if day in days[:limit_days]:
            item = dict(row)
            item["return_pct"] = _return_pct(row.get("daily_pnl"), row.get("baseline_equity"))
            by_day[day][str(row.get("currency") or "UNKNOWN")] = item
    return days[:limit_days], by_day


def _load_fills(connection, first_day: str, day_set: set[str]) -> dict[str, dict]:
    out: dict[str, dict] = defaultdict(lambda: {
        "actions": Counter(), "symbols": set(), "families": set(),
        "fills": 0, "closed_positions": set(),
    })
    rows = connection.execute(
        """SELECT f.paper_id,f.side,f.filled_at,p.symbol,p.asset_class,p.status,
                  p.closed_at,p.currency
           FROM paper_fills f JOIN paper_positions p ON p.paper_id=f.paper_id
           WHERE julianday(f.filled_at)>=julianday(?)
           ORDER BY f.filled_at""",
        (first_day + "T00:00:00-03:00",),
    )
    for row in rows:
        d = dict(row)
        day = _local_day(d.get("filled_at"))
        if day not in day_set:
            continue
        bucket = out[day]
        side = str(d.get("side") or "UNKNOWN").upper()
        label = {
            "BUY_SIMULATED": "COMPRAS",
            "SELL_SIMULATED": "VENTAS",
        }.get(side, side)
        bucket["actions"][label] += 1
        bucket["fills"] += 1
        if d.get("symbol"):
            bucket["symbols"].add(str(d["symbol"]).upper())
        if d.get("asset_class"):
            bucket["families"].add(str(d["asset_class"]).upper())
        if str(d.get("status") or "").upper() == "CLOSED" and _local_day(d.get("closed_at")) == day:
            bucket["closed_positions"].add(str(d.get("paper_id")))
    return out


def _load_cauciones(connection, first_day: str, day_set: set[str]) -> dict[str, dict]:
    out: dict[str, dict] = defaultdict(lambda: {
        "opened": 0, "settled": 0, "instruments": set(), "currencies": set(),
    })
    rows = connection.execute(
        """SELECT instrument_id,currency,opened_at,settled_at
           FROM paper_cauciones
           WHERE julianday(opened_at)>=julianday(?) OR julianday(settled_at)>=julianday(?)""",
        (first_day + "T00:00:00-03:00", first_day + "T00:00:00-03:00"),
    )
    for row in rows:
        d = dict(row)
        for key, action in (("opened_at", "opened"), ("settled_at", "settled")):
            day = _local_day(d.get(key))
            if day not in day_set:
                continue
            out[day][action] += 1
            if d.get("instrument_id"):
                out[day]["instruments"].add(str(d["instrument_id"]))
            if d.get("currency"):
                out[day]["currencies"].add(str(d["currency"]).upper())
    return out


def summarize(connection, *, limit_days: int = 7) -> list[dict]:
    """Build newest-first summaries strictly from persisted PAPER evidence."""
    limit_days = max(1, min(int(limit_days), 31))
    tables = _tables(connection)
    if "paper_daily_risk" not in tables:
        return []

    days, risk = _load_risk(connection, limit_days)
    if not days:
        return []
    day_set = set(days)
    first_day = min(days)

    fills = _load_fills(connection, first_day, day_set) if {
        "paper_fills", "paper_positions"
    }.issubset(tables) else {}
    cauciones = _load_cauciones(connection, first_day, day_set) if "paper_cauciones" in tables else {}

    result = []
    for day in days:
        day_risk = risk.get(day, {})
        activity = fills.get(day, {})
        caucion = cauciones.get(day, {})
        actions = Counter(activity.get("actions") or {})
        if caucion.get("opened"):
            actions["CAUCIONES_COLOCADAS"] += int(caucion["opened"])
        if caucion.get("settled"):
            actions["CAUCIONES_ACREDITADAS"] += int(caucion["settled"])

        symbols = sorted(set(activity.get("symbols") or set()) |
                         set(caucion.get("instruments") or set()))
        families = sorted(set(activity.get("families") or set()) |
                          ({"CAUCIONES"} if (caucion.get("opened") or caucion.get("settled")) else set()))

        currencies = []
        for currency, row in sorted(day_risk.items()):
            currencies.append({
                "currency": currency,
                "baseline_equity": row.get("baseline_equity"),
                "last_equity": row.get("last_equity"),
                "daily_pnl": row.get("daily_pnl"),
                "return_pct": None if row.get("return_pct") is None else str(row["return_pct"]),
                "risk_state": row.get("state"),
                "evaluated_at": row.get("evaluated_at"),
            })

        result.append({
            "day": day,
            "state": _day_state(day_risk),
            "currencies": currencies,
            "actions": dict(sorted(actions.items())),
            "fills": int(activity.get("fills") or 0),
            "closed_positions": len(activity.get("closed_positions") or set()),
            "symbols": symbols,
            "families": families,
            "has_activity": bool(actions or symbols),
        })
    return result


def assert_daily_summary_invariants() -> None:
    if _return_pct("100", "10000") != Decimal("1.0000"):
        raise AssertionError("daily return calculation changed")
    if _return_pct("1", None) is not None:
        raise AssertionError("missing baseline must never become a return")
    if _day_state({"ARS": {"daily_pnl": "1"}, "USD": {"daily_pnl": "-1"}}) != "MIXTO_POR_MONEDA":
        raise AssertionError("cross-currency results must remain separate")
