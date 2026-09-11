"""RC6 read-only operational PAPER activity grouped by Argentina trading day.

This is deliberately separate from the M0-M11 validation campaign ledger. It
never promotes a milestone and never writes to the PAPER database. The module
exists so /validacion can show what actually happened each day without
inventing campaign observations.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import inspect
import os
import sqlite3
from zoneinfo import ZoneInfo

TZ_AR = ZoneInfo("America/Argentina/Buenos_Aires")
DEFAULT_DB = os.environ.get("POROTA_PAPER_DB", "/app/data/paper_v17/observer_v17.db")


class OperationalDailyEvidenceError(RuntimeError):
    pass


def _connect_ro(path: str):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _as_ar_day(value) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ_AR).date().isoformat()


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def collect(*, db_path: str = DEFAULT_DB, limit_days: int = 30, event_limit: int = 250000) -> dict:
    """Return evidence derived from the operational PAPER DB, without writes."""
    limit_days = max(0, min(int(limit_days), 365))
    event_limit = max(1, min(int(event_limit), 500000))
    by_day = defaultdict(lambda: {
        "event_count": 0,
        "event_types": Counter(),
        "paper_ids": set(),
        "opened": 0,
        "closed": 0,
        "decisions": 0,
        "fills": 0,
        "realized_net_pnl": Decimal("0"),
    })
    with _connect_ro(db_path) as conn:
        state = conn.execute("SELECT mode,real_orders_sent,process_state,session_state,heartbeat_at FROM observer_state WHERE id=1").fetchone()
        if not state:
            raise OperationalDailyEvidenceError("OBSERVER_STATE_MISSING")
        if str(state["mode"]) != "PRODUCTION_PAPER" or int(state["real_orders_sent"] or 0) != 0:
            raise OperationalDailyEvidenceError("PAPER_SAFETY_INVARIANT_FAILED")

        for row in conn.execute("SELECT event_at,event_type,paper_id FROM paper_events ORDER BY id DESC LIMIT ?", (event_limit,)):
            day = _as_ar_day(row["event_at"])
            if not day:
                continue
            slot = by_day[day]
            slot["event_count"] += 1
            slot["event_types"][str(row["event_type"] or "UNKNOWN")] += 1
            if row["paper_id"]:
                slot["paper_ids"].add(str(row["paper_id"]))

        for row in conn.execute("SELECT paper_id,opened_at,closed_at,status,net_pnl FROM paper_positions"):
            opened = _as_ar_day(row["opened_at"])
            if opened:
                by_day[opened]["opened"] += 1
                by_day[opened]["paper_ids"].add(str(row["paper_id"]))
            closed = _as_ar_day(row["closed_at"])
            if closed:
                by_day[closed]["closed"] += 1
                by_day[closed]["paper_ids"].add(str(row["paper_id"]))
                by_day[closed]["realized_net_pnl"] += _decimal(row["net_pnl"])

        for row in conn.execute("SELECT decided_at FROM paper_decisions"):
            day = _as_ar_day(row["decided_at"])
            if day:
                by_day[day]["decisions"] += 1

        for row in conn.execute("SELECT filled_at FROM paper_fills"):
            day = _as_ar_day(row["filled_at"])
            if day:
                by_day[day]["fills"] += 1

    days = sorted(by_day, reverse=True)
    if limit_days:
        days = days[:limit_days]
    rows = []
    for day in days:
        slot = by_day[day]
        rows.append({
            "date_ar": day,
            "event_count": int(slot["event_count"]),
            "event_types": dict(slot["event_types"].most_common()),
            "paper_positions": len(slot["paper_ids"]),
            "opened": int(slot["opened"]),
            "closed": int(slot["closed"]),
            "decisions": int(slot["decisions"]),
            "fills": int(slot["fills"]),
            "realized_net_pnl": str(slot["realized_net_pnl"]),
        })
    return {
        "mode": "PRODUCTION_PAPER",
        "real_orders_sent": 0,
        "source": "observer_v17.db/read-only",
        "timezone": "America/Argentina/Buenos_Aires",
        "days": rows,
    }


def assert_read_only_contract() -> None:
    source = inspect.getsource(_connect_ro)
    assert "mode=ro" in source
    assert "query_only" in source
    assert all(word not in source.upper() for word in ("INSERT ", "UPDATE ", "DELETE ", "REPLACE "))
