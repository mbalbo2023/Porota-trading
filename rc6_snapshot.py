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

IOL_SHADOW_CACHE = Path(os.getenv("POROTA_IOL_SHADOW_CACHE_PATH", "data/market/iol_shadow_latest.json"))
MAX_IOL_SHADOW_AGE_SECONDS = 6 * 60 * 60


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _parse_timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def build_postclose_evidence(now: datetime, cache_path: Path = IOL_SHADOW_CACHE) -> dict:
    """Summarize only the cache captured by IOL during market hours.

    This function performs no network I/O, never touches PPI Watch, and never
    upgrades READY/HOLD or enables a contractual gate.
    """
    base = {
        "schema_version": 1,
        "status": "INSUFFICIENT_EVIDENCE",
        "source": "IOL_MCP",
        "mode": "SHADOW",
        "decision_effect": "OBSERVE_ONLY",
        "ready_paper_authorized": False,
        "contract_gate": "SOURCE_UNAVAILABLE_BY_SCOPE",
        "generated_at": now.astimezone(TZ).isoformat(timespec="seconds"),
        "symbols": [],
        "counts": {"observed": 0, "ready": 0, "unavailable": 0},
    }
    payload = _read_json(cache_path)
    if payload.get("source") != "IOL_MCP" or payload.get("mode") != "SHADOW":
        return {**base, "reason": "IOL_SHADOW_CACHE_MISSING_OR_POLICY_INVALID"}

    refreshed_at = _parse_timestamp(payload.get("refreshed_at"))
    local_now = now.astimezone(TZ)
    if refreshed_at is None:
        return {**base, "reason": "IOL_SHADOW_TIMESTAMP_INVALID"}
    age_seconds = (local_now - refreshed_at.astimezone(TZ)).total_seconds()
    if age_seconds < 0 or age_seconds > MAX_IOL_SHADOW_AGE_SECONDS:
        return {**base, "reason": "IOL_SHADOW_CACHE_STALE", "refreshed_at": payload.get("refreshed_at")}

    rows = payload.get("symbols")
    if not isinstance(rows, list):
        return {**base, "reason": "IOL_SHADOW_SYMBOLS_INVALID", "refreshed_at": payload.get("refreshed_at")}

    symbols = []
    for row in rows[:50]:
        if not isinstance(row, dict) or not str(row.get("symbol") or "").strip():
            continue
        state = str(row.get("state") or "UNAVAILABLE").upper()
        symbols.append({
            "symbol": str(row.get("symbol")).upper(),
            "state": state,
            "captured_at": row.get("captured_at"),
            "asset_type": row.get("asset_type"),
            "currency": row.get("currency"),
            "units_per_lot": row.get("units_per_lot"),
            "primary_comparison": row.get("primary_comparison"),
        })
    ready = sum(1 for row in symbols if row["state"] == "READY")
    unavailable = sum(1 for row in symbols if row["state"] != "READY")
    if not symbols:
        return {**base, "reason": "IOL_SHADOW_NO_OBSERVATIONS", "refreshed_at": payload.get("refreshed_at")}

    return {
        **base,
        "status": "OBSERVED",
        "reason": "POSTCLOSE_CACHE_ONLY_NO_CONTRACTUAL_AUTHORITY",
        "refreshed_at": payload.get("refreshed_at"),
        "age_seconds": round(age_seconds, 1),
        "symbols": symbols,
        "counts": {"observed": len(symbols), "ready": ready, "unavailable": unavailable},
    }

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
    alerts = []
    if mode != "PRODUCTION_PAPER":
        alerts.append("MODE_NOT_PRODUCTION_PAPER")
    if real_orders_sent != 0:
        alerts.append("REAL_ORDERS_NOT_ZERO_OR_UNVERIFIED")
    status = "VERIFIED" if not alerts else "INSUFFICIENT_EVIDENCE"
    postclose_evidence = build_postclose_evidence(now) if phase == "postclose" else None
    return {
        "schema_version": 1,
        "status": status,
        "phase": phase,
        "generated_at": now.astimezone(TZ).isoformat(timespec="seconds"),
        "source": "/api/observer/state",
        "mode": mode,
        "real_orders_sent": real_orders_sent,
        "summary": "Snapshot RC6 de solo lectura; métricas exclusivamente PAPER/SIMULATED.",
        "operations": operations,
        "decision_count": min(len(decisions), MAX_DECISIONS),
        "external_sources": [],
        "postclose_contract_candidate": postclose_evidence,
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
