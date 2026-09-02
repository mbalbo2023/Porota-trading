"""Read-only diagnostics for PAPER sale receivables shown by the dashboard.

The goal is to distinguish a legitimately pending T+1 receipt from a stale
persistent equity snapshot. This module never changes settlement dates and
never credits cash by inference.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from bs_instrument_contracts import aware_datetime

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
ZERO = Decimal("0")


def classify_receivable(row, as_of=None):
    at = aware_datetime(as_of or datetime.now(TZ).isoformat()).astimezone(TZ)
    basis = str(row.get("basis") or "")
    available_raw = row.get("available_at")
    available = aware_datetime(available_raw).astimezone(TZ) if available_raw else None

    if basis == "PENDING_CONFIRMATION" or available is None:
        state = "PENDING_CONFIRMATION"
    elif available > at:
        state = "PENDING_EXPECTED"
    else:
        state = "AVAILABLE_NOW"

    return {
        "paper_id": row.get("paper_id"),
        "ticker": row.get("ticker"),
        "currency": row.get("currency"),
        "settlement": row.get("settlement"),
        "closed_at": row.get("closed_at"),
        "net_proceeds": row.get("net_proceeds"),
        "available_at": available_raw,
        "basis": basis,
        "state": state,
    }


def snapshot(store, as_of=None):
    """Return current receivable state and latest stored equity snapshots."""
    at = aware_datetime(as_of or datetime.now(TZ).isoformat()).astimezone(TZ)
    with store.connect() as c:
        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "paper_sale_receivables" not in tables:
            return {"state": "MISSING_TABLE", "as_of": at.isoformat(), "rows": []}

        rows = [dict(r) for r in c.execute("""SELECT p.paper_id,p.ticker,p.currency,
          p.settlement,p.closed_at,r.net_proceeds,r.available_at,r.basis
          FROM paper_positions p JOIN paper_sale_receivables r USING(paper_id)
          WHERE p.status='CLOSED' ORDER BY datetime(p.closed_at) DESC""")]
        classified = [classify_receivable(r, at.isoformat()) for r in rows]

        latest=[]
        if "paper_equity_by_currency" in tables:
            latest=[dict(r) for r in c.execute("""SELECT e.*
              FROM paper_equity_by_currency e JOIN
              (SELECT currency,MAX(id) id FROM paper_equity_by_currency GROUP BY currency) x
              ON x.id=e.id ORDER BY e.currency""")]

    totals={"PENDING_EXPECTED": ZERO,
            "PENDING_CONFIRMATION": ZERO,
            "AVAILABLE_NOW": ZERO}
    counts={k:0 for k in totals}
    for row in classified:
        state=row["state"]
        counts[state]+=1
        totals[state]+=Decimal(str(row.get("net_proceeds") or "0"))

    snapshot_pending=sum((Decimal(str(r.get("pending_proceeds") or "0"))
                          for r in latest), ZERO)
    live_pending=totals["PENDING_EXPECTED"]+totals["PENDING_CONFIRMATION"]

    if snapshot_pending > ZERO and live_pending == ZERO:
        diagnosis="STALE_SNAPSHOT_HIGH_CONFIDENCE"
    elif snapshot_pending > live_pending:
        diagnosis="SNAPSHOT_GREATER_THAN_CURRENT_RECEIVABLES"
    elif totals["PENDING_EXPECTED"] > ZERO:
        diagnosis="PENDING_EXPECTED_BY_AVAILABLE_AT"
    elif totals["PENDING_CONFIRMATION"] > ZERO:
        diagnosis="PENDING_CONFIRMATION_REQUIRES_CONTRACT"
    else:
        diagnosis="NO_PENDING_EVIDENCE"

    return {
        "state": "READY",
        "as_of": at.isoformat(),
        "rows": classified,
        "counts": counts,
        "totals": {k:str(v) for k,v in totals.items()},
        "latest_equity_snapshots": latest,
        "snapshot_pending": str(snapshot_pending),
        "live_pending": str(live_pending),
        "diagnosis": diagnosis,
    }
