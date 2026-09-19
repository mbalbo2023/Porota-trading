#!/usr/bin/env python3
"""RC6 live Decision Cockpit.

Read-only collector for the private operator site. It never calls broker APIs
directly and never mutates the trading engine. Observer state is read through
the already-running dashboard container; IOL is consumed cache-only.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from collections import Counter
from datetime import datetime, time as clock_time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
MARKET_OPEN = clock_time(10, 30)
MARKET_CLOSE = clock_time(17, 0)

ROOT = Path(os.getenv("POROTA_ROOT", "/opt/porota-trading"))
DATA = ROOT / "data"
OUTPUT = Path(os.getenv(
    "POROTA_LIVE_COCKPIT_OUTPUT",
    str(DATA / "paper_v17" / "snapshots" / "live_latest.json"),
))
PREOPEN = DATA / "paper_v17" / "snapshots" / "preopen_latest.json"
POSTCLOSE = DATA / "paper_v17" / "snapshots" / "postclose_latest.json"
IOL_CACHE = DATA / "market" / "iol_shadow_latest.json"
VALIDATION = DATA / "validation" / "validation_milestones_rc6.json"

MAX_ROWS = 10
IOL_FRESH_SECONDS = int(os.getenv("POROTA_LIVE_IOL_FRESH_SECONDS", "180"))


def _number(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _parse_dt(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=TZ)
    except (TypeError, ValueError):
        return None


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".live-cockpit.", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def is_operational_market_window(now: datetime) -> bool:
    local = now.astimezone(TZ)
    try:
        import ak_byma_calendar as byma
        business_day = bool(byma.es_dia_habil_operativo(local.date()))
    except Exception:
        # Fail closed: a weekday alone is not enough to certify BYMA open.
        return False
    return business_day and MARKET_OPEN <= local.time() < MARKET_CLOSE


def _observer_state_via_container() -> dict:
    """Read the existing dashboard API from inside its own container.

    This avoids loading dashboard credentials on the host and does not create
    a second broker/API reader.
    """
    code = r'''
import json, os, urllib.request
token=os.getenv("DASHBOARD_ACCESS_TOKEN","")
if not token:
    raise SystemExit("DASHBOARD_ACCESS_TOKEN_NOT_AVAILABLE")
req=urllib.request.Request(
    "http://127.0.0.1:8000/api/observer/state",
    headers={"Authorization":"Bearer "+token,"Cache-Control":"no-cache"},
)
with urllib.request.urlopen(req,timeout=20) as r:
    obj=json.loads(r.read().decode("utf-8"))
if not isinstance(obj,dict):
    raise SystemExit("OBSERVER_STATE_INVALID")
print(json.dumps(obj,ensure_ascii=False,separators=(",",":")))
'''
    proc = subprocess.run(
        ["/usr/bin/docker", "exec", "porota_production_dashboard", "python", "-c", code],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("OBSERVER_READ_FAILED:" + (proc.stderr or proc.stdout)[-240:])
    value = json.loads(proc.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("OBSERVER_STATE_INVALID")
    return value


def _compact_decision(row: dict) -> dict:
    return {
        "decided_at": row.get("decided_at"),
        "symbol": row.get("symbol"),
        "action": row.get("action"),
        "score": _number(row.get("score")),
        "reason": row.get("reason") or "NO_REASON",
    }


def _decision_views(decisions: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    compact = [_compact_decision(row) for row in decisions if isinstance(row, dict)]
    latest_by_symbol: dict[str, dict] = {}
    for row in compact:
        symbol = str(row.get("symbol") or "").upper().strip()
        if symbol and symbol not in latest_by_symbol:
            latest_by_symbol[symbol] = row

    opportunities = list(latest_by_symbol.values())
    opportunities.sort(
        key=lambda row: (
            "BUY" in str(row.get("action") or "").upper(),
            row.get("score") is not None,
            row.get("score") if row.get("score") is not None else -1e18,
        ),
        reverse=True,
    )
    why_not = [
        row for row in compact
        if "BUY" not in str(row.get("action") or "").upper()
    ]
    return opportunities[:MAX_ROWS], why_not[:MAX_ROWS], compact[:MAX_ROWS]


def _change_from_preopen(preopen: dict, opportunities: list[dict]) -> dict:
    baseline = preopen.get("decision_sample") if isinstance(preopen.get("decision_sample"), list) else []
    if not baseline:
        return {
            "status": "INSUFFICIENT_EVIDENCE",
            "label": "Sin muestra de decisiones en el snapshot pre-rueda",
            "changes": [],
        }
    before = {
        str(row.get("symbol") or "").upper(): str(row.get("action") or "")
        for row in baseline if isinstance(row, dict) and row.get("symbol")
    }
    changes = []
    for row in opportunities:
        symbol = str(row.get("symbol") or "").upper()
        old, new = before.get(symbol), str(row.get("action") or "")
        if old is not None and old != new:
            changes.append({"symbol": symbol, "from": old, "to": new})
    return {
        "status": "VERIFIED",
        "label": f"{len(changes)} cambios de acción respecto de pre-rueda",
        "changes": changes[:MAX_ROWS],
    }


def _iol_summary(iol: dict, now: datetime) -> dict:
    rows = [row for row in (iol.get("symbols") or []) if isinstance(row, dict)]
    fresh_ready = 0
    stale = 0
    divergence = 0
    ready = 0
    for row in rows:
        if row.get("state") == "READY":
            ready += 1
        captured = _parse_dt(row.get("captured_at"))
        age = (now.astimezone(timezone.utc) - captured.astimezone(timezone.utc)).total_seconds() if captured else None
        if row.get("state") == "READY" and age is not None and 0 <= age <= IOL_FRESH_SECONDS:
            fresh_ready += 1
        elif age is None or age > IOL_FRESH_SECONDS:
            stale += 1
        comparison = row.get("primary_comparison") if isinstance(row.get("primary_comparison"), dict) else {}
        if comparison.get("state") == "PRICE_DIVERGENCE":
            divergence += 1
    return {
        "source": iol.get("source") or "IOL_MCP",
        "mode": iol.get("mode") or "SHADOW",
        "decision_effect": iol.get("decision_effect") or "OBSERVE_ONLY",
        "refreshed_at": iol.get("refreshed_at"),
        "universe_observed": len(rows),
        "ready": ready,
        "fresh_ready": fresh_ready,
        "stale_or_unknown": stale,
        "price_divergence": divergence,
        "influence_on_live_decision": "NONE_OBSERVE_ONLY",
    }


def _risk_summary(opened: list[dict]) -> dict:
    notionals = []
    unrealized = []
    by_symbol = Counter()
    for row in opened:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "N/D")
        by_symbol[symbol] += 1
        q = _number(row.get("quantity"))
        px = _number(row.get("current_price"))
        if px is None:
            px = _number(row.get("entry_price"))
        if q is not None and px is not None:
            notionals.append(abs(q * px))
        upnl = _number(row.get("unrealized_pnl"))
        if upnl is not None:
            unrealized.append(upnl)
    return {
        "open_positions": len(opened),
        "estimated_notional_ars": round(sum(notionals), 2) if notionals else None,
        "unrealized_pnl_ars": round(sum(unrealized), 2) if unrealized else None,
        "symbols": dict(by_symbol),
        "risk_authority": "DISPLAY_ONLY",
    }


def capture_preopen_baseline(observer: dict, now: datetime, path: Path = PREOPEN) -> dict:
    """Attach a bounded read-only decision sample to the preserved preopen file."""
    preopen = _read_json(path)
    if preopen.get("phase") != "preopen":
        return {"status": "NOT_APPLICABLE", "reason": "PREOPEN_SNAPSHOT_NOT_AVAILABLE"}
    decisions = observer.get("decisions") if isinstance(observer.get("decisions"), list) else []
    sample = [_compact_decision(row) for row in decisions if isinstance(row, dict)][:MAX_ROWS]
    preopen["decision_sample"] = sample
    preopen["decision_sample_captured_at"] = now.astimezone(TZ).isoformat(timespec="seconds")
    preopen["decision_sample_read_only"] = True
    _atomic_json(path, preopen)
    return {"status": "VERIFIED", "decision_sample": len(sample)}


def build_payload(observer: dict, iol: dict, validation: dict, preopen: dict, postclose: dict, now: datetime) -> dict:
    state = observer.get("state") if isinstance(observer.get("state"), dict) else {}
    decisions = observer.get("decisions") if isinstance(observer.get("decisions"), list) else []
    opened = observer.get("open") if isinstance(observer.get("open"), list) else []
    closed = observer.get("closed") if isinstance(observer.get("closed"), list) else []

    opportunities, why_not, trace = _decision_views(decisions)

    try:
        real_orders_sent = int(state.get("real_orders_sent"))
    except (TypeError, ValueError):
        real_orders_sent = None
    mode = state.get("mode")

    alerts = []
    if mode != "PRODUCTION_PAPER":
        alerts.append("MODE_NOT_PRODUCTION_PAPER")
    if real_orders_sent != 0:
        alerts.append("REAL_ORDERS_NOT_ZERO_OR_UNVERIFIED")

    iol_state = _iol_summary(iol, now)
    if iol_state["universe_observed"] and not iol_state["fresh_ready"]:
        alerts.append("IOL_SHADOW_NO_FRESH_READY_ROWS")

    regime = state.get("market_regime") or state.get("regime") or "N/D"
    validation_summary = validation.get("summary") if isinstance(validation.get("summary"), dict) else {}

    return {
        "schema_version": 1,
        "phase": "live",
        "status": "VERIFIED" if not [a for a in alerts if a.startswith(("MODE_", "REAL_"))] else "INSUFFICIENT_EVIDENCE",
        "generated_at": now.astimezone(TZ).isoformat(timespec="seconds"),
        "read_only": True,
        "decision_authority": "OBSERVE_ONLY",
        "automatic_strategy_change": False,
        "real_orders_authorized": False,
        "mode": mode,
        "real_orders_sent": real_orders_sent,
        "market_regime": regime,
        "top_opportunities": opportunities,
        "why_not_traded": why_not,
        "traceability": trace,
        "changes_from_preopen": _change_from_preopen(preopen, opportunities),
        "iol": iol_state,
        "risk": _risk_summary(opened),
        "runtime": {
            "decisions_visible": len(decisions),
            "open_positions": len(opened),
            "closed_positions_visible": len(closed),
        },
        "validation": {
            "generated_at": validation.get("generated_at"),
            "ledger_status": validation.get("ledger_status"),
            "summary": validation_summary,
            "real_money_state": validation.get("real_money_state"),
        },
        "preopen_generated_at": preopen.get("generated_at"),
        "postclose_generated_at": postclose.get("generated_at"),
        "urgent_alerts": alerts,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-preopen-baseline", action="store_true")
    args = parser.parse_args()
    now = datetime.now(TZ)

    if args.capture_preopen_baseline:
        try:
            result = capture_preopen_baseline(_observer_state_via_container(), now)
            print(json.dumps({**result, "read_only": True, "phase": "preopen-baseline"}, sort_keys=True))
            return 0 if result.get("status") in {"VERIFIED", "NOT_APPLICABLE"} else 2
        except Exception as exc:
            print(json.dumps({"status": "ERROR", "phase": "preopen-baseline", "read_only": True, "error": type(exc).__name__}, sort_keys=True))
            return 2

    if not is_operational_market_window(now):
        print(json.dumps({"status": "NOT_DUE", "phase": "live", "read_only": True}, sort_keys=True))
        return 0
    try:
        payload = build_payload(
            _observer_state_via_container(),
            _read_json(IOL_CACHE),
            _read_json(VALIDATION),
            _read_json(PREOPEN),
            _read_json(POSTCLOSE),
            now,
        )
        _atomic_json(OUTPUT, payload)
        print(json.dumps({
            "status": payload["status"],
            "phase": "live",
            "read_only": True,
            "real_orders_sent": payload["real_orders_sent"],
            "opportunities": len(payload["top_opportunities"]),
        }, sort_keys=True))
        return 0 if payload["status"] == "VERIFIED" else 2
    except Exception as exc:
        print(json.dumps({
            "status": "ERROR", "phase": "live", "read_only": True,
            "error": type(exc).__name__,
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
