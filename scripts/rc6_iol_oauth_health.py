#!/usr/bin/env python3
"""RC6 IOL MCP OAuth guardian.

Outside BYMA market hours this performs only MCP initialize/tools-list through
the read-only adapter so OAuth can refresh without market-data polling.
During market hours a fresh LIVE_FRESH family cache proves that the real IOL
collector is already exercising the MCP; in that case the guardian does not
make a redundant MCP call.

On REAUTH_REQUIRED the guardian enqueues one priority-0 Telegram outbox event
per incident. It never calls account, validation, DDJJ or execution tools and
never sends Telegram directly.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from datetime import datetime, time as clock_time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ak_byma_calendar import es_dia_habil_operativo
from iol_mcp_readonly_adapter_rc6 import DEFAULT_STORE, OAuthStoreReadOnlyMCP

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
MARKET_OPEN = clock_time(10, 30)
MARKET_CLOSE = clock_time(17, 0)
ROOT = Path(os.getenv("POROTA_IOL_SHADOW_ROOT", "/opt/porota-trading/data/market"))
FAMILY_CACHE = ROOT / "iol_family_reference_latest.json"
STATE_FILE = ROOT / "iol_oauth_guardian_state.json"
DB = os.getenv("PAPER_V17_DB_PATH", "/opt/porota-trading/data/paper_v17/observer_v17.db")
COLLECTOR_FRESH_SECONDS = max(120, int(os.getenv("POROTA_IOL_COLLECTOR_FRESH_SECONDS", "240")))


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _metadata(path: Path) -> dict:
    if not path.is_file():
        return {
            "store_present": False,
            "access_token_present": False,
            "refresh_token_present": False,
            "expires_in": None,
            "estimated_seconds_remaining": None,
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    tokens = payload.get("tokens") if isinstance(payload.get("tokens"), dict) else {}
    try:
        expires_in = float(tokens.get("expires_in"))
    except (TypeError, ValueError):
        expires_in = None
    try:
        obtained = float(payload.get("token_obtained_at_epoch"))
    except (TypeError, ValueError):
        obtained = path.stat().st_mtime
    remaining = None
    if expires_in is not None and expires_in > 0:
        remaining = round(obtained + expires_in - time.time(), 1)
    return {
        "store_present": True,
        "mode": oct(path.stat().st_mode & 0o777),
        "access_token_present": bool(tokens.get("access_token")),
        "refresh_token_present": bool(tokens.get("refresh_token")),
        "expires_in": expires_in,
        "estimated_seconds_remaining": remaining,
        "token_endpoint_persisted": bool(payload.get("token_endpoint")),
        "token_obtained_at_persisted": payload.get("token_obtained_at_epoch") is not None,
    }


def _market_open(now: datetime) -> bool:
    local = now.astimezone(TZ)
    return (
        es_dia_habil_operativo(local.date())
        and MARKET_OPEN <= local.time() < MARKET_CLOSE
    )


def _collector_cache(now: datetime) -> dict:
    payload = _load_json(FAMILY_CACHE)
    observed = _parse_time(payload.get("last_known_good_at") or payload.get("refreshed_at"))
    age = None
    if observed is not None:
        age = (now.astimezone(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
    state = str(payload.get("cache_state") or "SOURCE_UNAVAILABLE").upper()
    live = state == "LIVE_FRESH" and age is not None and 0 <= age <= COLLECTOR_FRESH_SECONDS
    return {
        "state": state,
        "observed_at": observed.isoformat() if observed else None,
        "age_seconds": round(age, 1) if age is not None else None,
        "live_fresh": live,
    }


def _paper_safety() -> dict:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=3)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    row = c.execute(
        "SELECT mode,real_orders_sent,process_state,session_state FROM observer_state WHERE id=1"
    ).fetchone()
    c.close()
    if not row:
        raise RuntimeError("OBSERVER_STATE_MISSING")
    out = dict(row)
    if out["mode"] != "PRODUCTION_PAPER" or int(out["real_orders_sent"] or 0) != 0:
        raise RuntimeError("PAPER_SAFETY_INVARIANT_FAILED")
    return out


def _enqueue_reauth_alert(incident: int, at: str, reason: str) -> str:
    try:
        c = sqlite3.connect(DB, timeout=3)
        c.execute("PRAGMA busy_timeout=3000")
        table = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_notification_outbox'"
        ).fetchone()
        if not table:
            c.close()
            return "OUTBOX_MISSING"
        key = f"iol-mcp-reauth-required:{incident}"
        body = (
            "🔴 POROTA — IOL MCP REAUTH_REQUIRED\n"
            "La autorización OAuth del MCP de InvertirOnline en el Droplet no pudo renovarse.\n"
            "PPI continúa como fuente primaria; IOL queda fuera hasta reautorizar.\n"
            f"Motivo: {reason[:160]}\n"
            "PAPER/SHADOW ONLY · no se enviaron órdenes reales."
        )
        c.execute(
            """INSERT OR IGNORE INTO paper_notification_outbox
               (event_key,kind,body,priority,created_at,next_attempt_at)
               VALUES(?,?,?,?,?,?)""",
            (key, "IOL_MCP_REAUTH_REQUIRED", body, 0, at, at),
        )
        inserted = c.total_changes
        c.commit()
        c.close()
        return "ENQUEUED" if inserted else "ALREADY_ENQUEUED"
    except sqlite3.Error as exc:
        return "OUTBOX_ERROR:" + type(exc).__name__


def _write_state(previous: dict, result: dict, now: datetime) -> dict:
    state = str(result.get("state") or "UNKNOWN")
    previous_state = str(previous.get("state") or "UNKNOWN")
    incident = int(previous.get("incident_seq") or 0)
    if state == "REAUTH_REQUIRED" and previous_state != "REAUTH_REQUIRED":
        incident += 1
    payload = {
        "schema": "POROTA_RC6_IOL_OAUTH_GUARDIAN_STATE_V1",
        "state": state,
        "reason": result.get("reason"),
        "checked_at": now.isoformat(),
        "market_open": bool(result.get("market_open")),
        "collector": result.get("collector"),
        "incident_seq": incident,
        "last_authenticated_at": (
            now.isoformat() if state in {"AUTHENTICATED", "ACTIVE_COLLECTOR_HEALTHY"}
            else previous.get("last_authenticated_at")
        ),
        "last_reauth_required_at": (
            now.isoformat() if state == "REAUTH_REQUIRED" and previous_state != "REAUTH_REQUIRED"
            else previous.get("last_reauth_required_at")
        ),
    }
    _atomic_json(STATE_FILE, payload)
    return payload


def main() -> int:
    now = datetime.now(timezone.utc)
    safety = _paper_safety()
    store = Path(DEFAULT_STORE)
    before = _metadata(store)
    collector = _collector_cache(now)
    open_market = _market_open(now)
    result = {
        "schema": "POROTA_RC6_IOL_OAUTH_HEALTH_V2",
        "authority": "IOL_MCP_OAUTH",
        "decision_effect": "OBSERVE_ONLY_COMPLEMENTARY_TO_PPI",
        "real_order_routes": "NOT_PRESENT",
        "market_open": open_market,
        "collector": collector,
        "before": before,
        "paper_safety": {
            "mode": safety["mode"],
            "real_orders_sent": int(safety["real_orders_sent"] or 0),
        },
    }

    if not before["store_present"]:
        result["state"] = "REAUTH_REQUIRED"
        result["reason"] = "OAUTH_STORE_MISSING"
    elif open_market and collector["live_fresh"]:
        # During wheel, the real complementary collector is already exercising
        # IOL. Do not waste MCP calls on a synthetic keepalive.
        result["state"] = "ACTIVE_COLLECTOR_HEALTHY"
        result["reason"] = "MARKET_OPEN_LIVE_FRESH_COLLECTOR"
        result["mcp_probe_performed"] = False
    else:
        try:
            tools = OAuthStoreReadOnlyMCP(store, timeout_seconds=15).list_tools()
            result["state"] = "AUTHENTICATED"
            result["reason"] = (
                "OFFHOURS_OAUTH_GUARDIAN"
                if not open_market else "COLLECTOR_NOT_FRESH_OAUTH_DIAGNOSTIC"
            )
            result["tools_discovered"] = len(tools.get("tools") or []) if isinstance(tools, dict) else 0
            result["after"] = _metadata(store)
            result["mcp_probe_performed"] = True
        except Exception as exc:
            result["state"] = "REAUTH_REQUIRED"
            result["reason"] = type(exc).__name__ + ":" + str(exc)[:120]
            result["after"] = _metadata(store)
            result["mcp_probe_performed"] = True

    previous = _load_json(STATE_FILE)
    persisted = _write_state(previous, result, now)
    if result["state"] == "REAUTH_REQUIRED":
        result["telegram_alert"] = _enqueue_reauth_alert(
            persisted["incident_seq"], now.isoformat(), str(result.get("reason") or "UNKNOWN")
        )
    else:
        result["telegram_alert"] = "NOT_REQUIRED"

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 2 if result["state"] == "REAUTH_REQUIRED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
