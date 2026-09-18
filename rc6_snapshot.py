#!/usr/bin/env python3
"""RC6 read-only operational snapshots for the private operations Site."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import ak_byma_calendar as byma

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
BASE_URL = os.getenv("RC6_SNAPSHOT_DASHBOARD_URL", "http://127.0.0.1:8000")
OUTPUT = Path(os.getenv("RC6_SNAPSHOT_OUTPUT", "data/paper_v17/snapshots/latest.json"))
MAX_OPERATIONS = 500
MAX_DECISIONS = 5000

def _request_state() -> dict:
    token = os.getenv("DASHBOARD_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("DASHBOARD_ACCESS_TOKEN_NOT_AVAILABLE")
    request = urllib.request.Request(
        BASE_URL + "/api/observer/state",
        headers={"Authorization": "Bearer " + token, "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("OBSERVER_STATE_INVALID")
    return payload

def _local_day(value: object) -> str | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TZ)
        return parsed.astimezone(TZ).date().isoformat()
    except (TypeError, ValueError):
        return None

def _number(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def _operation(row: dict) -> dict:
    return {
        "symbol": row.get("symbol"),
        "type": "PAPER",
        "opened_at": row.get("opened_at"),
        "closed_at": row.get("closed_at"),
        "net_pnl_ars": _number(row.get("net_pnl")),
        "decision_reason": row.get("close_reason") or "NO_CLOSE_REASON",
        "external_sources": [],
        "counterfactual": "INSUFFICIENT_EVIDENCE",
    }

def build_snapshot(phase: str, now: datetime, payload: dict) -> dict:
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    closed = payload.get("closed") if isinstance(payload.get("closed"), list) else []
    decisions = payload.get("decisions") if isinstance(payload.get("decisions"), list) else []
    today = now.astimezone(TZ).date().isoformat()
    operations = [_operation(row) for row in closed if isinstance(row, dict) and _local_day(row.get("closed_at")) == today][:MAX_OPERATIONS]
    mode = state.get("mode")
    try:
        real_orders_sent = int(state.get("real_orders_sent"))
    except (TypeError, ValueError):
        real_orders_sent = None
    closed_pnl = [item["net_pnl_ars"] for item in operations if item.get("net_pnl_ars") is not None]
    winners = sum(value > 0 for value in closed_pnl)
    losers = sum(value < 0 for value in closed_pnl)
    flat = sum(value == 0 for value in closed_pnl)
    metrics = {
        "closed_operations": len(operations),
        "net_pnl_ars": round(sum(closed_pnl), 2) if closed_pnl else 0.0,
        "winners": winners,
        "losers": losers,
        "breakeven": flat,
        "win_rate_pct": round(winners * 100 / len(closed_pnl), 2) if closed_pnl else None,
        "decisions_observed": min(len(decisions), MAX_DECISIONS),
    }
    alerts = []
    if mode != "PRODUCTION_PAPER":
        alerts.append("MODE_NOT_PRODUCTION_PAPER")
    if real_orders_sent != 0:
        alerts.append("REAL_ORDERS_NOT_ZERO_OR_UNVERIFIED")
    status = "VERIFIED" if not alerts else "INSUFFICIENT_EVIDENCE"
    return {
        "schema_version": 1,
        "status": status,
        "phase": phase,
        "generated_at": now.astimezone(TZ).isoformat(timespec="seconds"),
        "source": "/api/observer/state",
        "mode": mode,
        "real_orders_sent": real_orders_sent,
        "summary": "Snapshot RC6 de solo lectura; métricas exclusivamente PAPER/SIMULATED.",
        "metrics": metrics,
        "operations": operations,
        "decision_count": metrics["decisions_observed"],
        "external_sources": [],
        "urgent_alerts": alerts,
    }

def write_snapshot(snapshot: dict, output: Path = OUTPUT) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".latest.", suffix=".json", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("preopen", "postclose"), required=True)
    args = parser.parse_args()
    now = datetime.now(TZ)
    if not byma.es_dia_habil_operativo(now.date()):
        print(json.dumps({"schema_version": 1, "phase": args.phase, "status": "NOT_DUE", "read_only": True}, sort_keys=True))
        return 0
    try:
        snapshot = build_snapshot(args.phase, now, _request_state())
        write_snapshot(snapshot)
        print(json.dumps({"schema_version": 1, "phase": args.phase, "status": snapshot["status"], "read_only": True, "real_orders_sent": snapshot["real_orders_sent"]}, sort_keys=True))
        return 0 if snapshot["status"] == "VERIFIED" else 2
    except Exception as exc:
        print(json.dumps({"schema_version": 1, "phase": args.phase, "status": "ERROR", "read_only": True, "error": type(exc).__name__}, sort_keys=True))
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
