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
import math
import os
import sqlite3
import tempfile
import time
from datetime import datetime, time as clock_time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ak_byma_calendar import es_dia_habil_operativo
from iol_mcp_readonly_adapter_rc6 import DEFAULT_STORE, IOLMCPError, OAuthStoreReadOnlyMCP

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
MARKET_OPEN = clock_time(10, 30)
MARKET_CLOSE = clock_time(17, 0)
ROOT = Path(os.getenv("POROTA_IOL_SHADOW_ROOT", "/opt/porota-trading/data/market"))
FAMILY_CACHE = ROOT / "iol_family_reference_latest.json"
STATE_FILE = ROOT / "iol_oauth_guardian_state.json"
DB = os.getenv("PAPER_V17_DB_PATH", "/opt/porota-trading/data/paper_v17/observer_v17.db")
COLLECTOR_FRESH_SECONDS = max(120, int(os.getenv("POROTA_IOL_COLLECTOR_FRESH_SECONDS", "240")))
ALERT_STATES = frozenset({"REAUTH_REQUIRED", "DEGRADED_RETRYABLE", "DEGRADED_UNVERIFIED"})
AUTH_FAILURE_CODES = frozenset({
    "IOL_MCP_OAUTH_STORE_UNAVAILABLE", "IOL_MCP_OAUTH_NOT_READY",
    "IOL_MCP_OAUTH_REFRESH_UNAVAILABLE", "IOL_MCP_OAUTH_STORE_WRITE_FAILED",
})
SAFE_ERROR_CODES = AUTH_FAILURE_CODES | frozenset({
    "IOL_MCP_OAUTH_REFRESH_FAILED", "IOL_MCP_OAUTH_DISCOVERY_FAILED",
    "IOL_MCP_TRANSPORT_UNAVAILABLE", "IOL_MCP_INVALID_RESPONSE",
    "IOL_MCP_TOOL_ERROR", "IOL_MCP_RPC_ERROR",
})


def _failure(exc: Exception) -> dict:
    """Classify a failed market-data probe without serializing an exception.

    HTTPError bodies, endpoint URLs and OAuth tokens are never evidence. An
    unknown error remains a separate fail-closed state, not a claim that the
    user must reauthorize.
    """
    if not isinstance(exc, IOLMCPError):
        return {"state": "DEGRADED_UNVERIFIED", "reason": "IOL_MCP_UNCLASSIFIED",
                "failure_class": "UNCLASSIFIED", "http_status": None}
    status = exc.status_code if type(exc.status_code) is int and 100 <= exc.status_code <= 599 else None
    raw = str(exc)
    if raw in SAFE_ERROR_CODES:
        code = raw
    elif status is not None and raw == "IOL_MCP_HTTP_" + str(status):
        code = raw
    elif raw.startswith("IOL_MCP_RPC_ERROR:"):
        code = "IOL_MCP_RPC_ERROR"
    else:
        code = "IOL_MCP_UNCLASSIFIED"
    kind = ("HTTP" if status is not None else
            "TRANSPORT" if raw == "IOL_MCP_TRANSPORT_UNAVAILABLE" else
            exc.failure_class if exc.failure_class in {"TRANSPORT", "INVALID_JSON"}
            else "APPLICATION")
    transient = (status in {408, 425, 429} or
                 status is not None and 500 <= status <= 599 or
                 kind == "TRANSPORT")
    auth = (status in {400, 401, 403} or code in AUTH_FAILURE_CODES)
    return {"state": "DEGRADED_RETRYABLE" if transient else
            "REAUTH_REQUIRED" if auth else "DEGRADED_UNVERIFIED",
            "reason": code, "failure_class": kind, "http_status": status}


def _retry_delay(exc: Exception) -> float | None:
    if not isinstance(exc, IOLMCPError):
        return None
    after = exc.retry_after
    if after is None:
        return 1.0
    # Respect a longer provider Retry-After by deferring to the next timer.
    return (max(.5, after) if type(after) in {int, float} and
            math.isfinite(after) and 0 <= after <= 2 else None)


def _probe_tools(store: Path, *, clock=time.monotonic, pause=time.sleep) -> dict:
    """At most two read-only initialize/tools-list probes within the service.

    The unit has TimeoutStartSec=60. A 6s per-request timeout, one 2s backoff
    and a first-attempt <=20s retry budget leave room for the second probe.
    """
    started = clock()
    recovered = None
    for attempt in (1, 2):
        try:
            tools = OAuthStoreReadOnlyMCP(store, timeout_seconds=6).list_tools()
            return {"tools": tools, "attempts": attempt,
                    "failure": None, "recovered_failure": recovered}
        except Exception as exc:
            failure = _failure(exc)
            delay = _retry_delay(exc)
            if (attempt == 1 and failure["state"] == "DEGRADED_RETRYABLE" and
                    clock() - started <= 20 and delay is not None):
                recovered = failure
                pause(delay)
                continue
            return {"tools": None, "attempts": attempt,
                    "failure": failure, "recovered_failure": recovered}
    raise AssertionError("unreachable")


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


def _enqueue_alert(incident: int, at: str, reason: str, state: str) -> str:
    try:
        c = sqlite3.connect(DB, timeout=3)
        c.execute("PRAGMA busy_timeout=3000")
        table = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_notification_outbox'"
        ).fetchone()
        if not table:
            c.close()
            return "OUTBOX_MISSING"
        if state == "REAUTH_REQUIRED":
            key = f"iol-mcp-reauth-required:{incident}"
            kind = "IOL_MCP_REAUTH_REQUIRED"
            message = "Autorización OAuth rechazada o credenciales incompletas; requiere revisión."
        elif state == "DEGRADED_RETRYABLE":
            key = f"iol-mcp-retryable-degraded:{incident}"
            kind = "IOL_MCP_RETRYABLE_DEGRADED"
            message = "Error transitorio de transporte/proveedor; el guardian reintentará."
        elif state == "DEGRADED_UNVERIFIED":
            key = f"iol-mcp-unverified-degraded:{incident}"
            kind = "IOL_MCP_UNVERIFIED_DEGRADED"
            message = "Error no clasificado; IOL sigue sin certificación."
        elif state == "RECOVERED":
            key = f"iol-mcp-recovered:{incident}"
            kind = "IOL_MCP_RECOVERED"
            message = "Autenticación de sólo lectura recuperada; esto no habilita operaciones."
        else:
            raise ValueError("IOL_GUARDIAN_ALERT_STATE_INVALID")
        continuity = ("PPI continúa como fuente primaria; IOL queda fuera hasta verificar."
                      if state != "RECOVERED" else
                      "PPI continúa como fuente primaria; se requiere control operacional separado.")
        icon = "🟢" if state == "RECOVERED" else "🔴"
        body = (f"{icon} POROTA — IOL MCP {state}\n{message}\n{continuity}\n"
                f"Motivo: {reason}\n"
                "PAPER/SHADOW ONLY · no se enviaron órdenes reales.")
        c.execute(
            """INSERT OR IGNORE INTO paper_notification_outbox
               (event_key,kind,body,priority,created_at,next_attempt_at)
               VALUES(?,?,?,?,?,?)""",
            (key, kind, body, 0, at, at),
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
    if state in ALERT_STATES and state != previous_state:
        incident += 1
    payload = {
        "schema": "POROTA_RC6_IOL_OAUTH_GUARDIAN_STATE_V1",
        "state": state,
        "reason": result.get("reason"),
        "failure_class": result.get("failure_class"),
        "http_status": result.get("http_status"),
        "probe_attempts": result.get("probe_attempts", 0),
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
        "last_retryable_degraded_at": (
            now.isoformat() if state == "DEGRADED_RETRYABLE" and previous_state != state
            else previous.get("last_retryable_degraded_at")
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
        result["probe_attempts"] = 0
    elif open_market and collector["live_fresh"]:
        # During wheel, the real complementary collector is already exercising
        # IOL. Do not waste MCP calls on a synthetic keepalive.
        result["state"] = "ACTIVE_COLLECTOR_HEALTHY"
        result["reason"] = "MARKET_OPEN_LIVE_FRESH_COLLECTOR"
        result["mcp_probe_performed"] = False
    else:
        probe = _probe_tools(store)
        result["probe_attempts"] = probe["attempts"]
        if probe["failure"] is None:
            tools = probe["tools"]
            result["state"] = "AUTHENTICATED"
            result["reason"] = (
                "OFFHOURS_OAUTH_GUARDIAN"
                if not open_market else "COLLECTOR_NOT_FRESH_OAUTH_DIAGNOSTIC"
            )
            result["tools_discovered"] = len(tools.get("tools") or []) if isinstance(tools, dict) else 0
            result["after"] = _metadata(store)
            result["mcp_probe_performed"] = True
            if probe["recovered_failure"] is not None:
                result["recovered_transient"] = probe["recovered_failure"]
        else:
            result.update(probe["failure"])
            result["after"] = _metadata(store)
            result["mcp_probe_performed"] = True

    previous = _load_json(STATE_FILE)
    persisted = _write_state(previous, result, now)
    if result["state"] in ALERT_STATES:
        result["telegram_alert"] = _enqueue_alert(
            persisted["incident_seq"], now.isoformat(),
            str(result.get("reason") or "UNKNOWN"), result["state"]
        )
    else:
        result["telegram_alert"] = "NOT_REQUIRED"
        if previous.get("state") in ALERT_STATES and result["state"] == "AUTHENTICATED":
            result["recovery_alert"] = _enqueue_alert(
                persisted["incident_seq"], now.isoformat(), "AUTHENTICATED", "RECOVERED")

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 2 if result["state"] in ALERT_STATES else 0


if __name__ == "__main__":
    raise SystemExit(main())
