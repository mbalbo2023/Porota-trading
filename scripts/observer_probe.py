"""Inspect the existing PAPER observer without opening an external connection."""
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
import re
import sqlite3

from cg_paper_workspace import database_path, identity_from_connection
from rc6_ppi_gateway_reader_adapter import gateway_socket_path, local_gateway_owner_custody
from rc6_ppi_shared_feed import ReadOnlyUnixFeedClient

EVENTS = (
    "PPI_GATEWAY_OWNER_OPEN", "PPI_GATEWAY_ATTACHED", "PPI_GATEWAY_HOLD",
    "PPI_GATEWAY_AUTH_RECOVERY", "PPI_GATEWAY_CATALOG_REFRESHED", "PPI_GATEWAY_CATALOG_HOLD",
    "PPI_GATEWAY_REATTACH_REQUIRED", "PPI_DIRECT_REST_PROBE_HOLD",
    "PPI_DIRECT_REST_PROBE_OBSERVED", "PPI_DIRECT_REST_NATIVE_WIRE_WITNESS",
    "PPI_DIRECT_REST_REFERENCE_HOLD", "PPI_DIRECT_REST_REFERENCE_OBSERVED",
    "DATA_ERROR", "DATA_REJECTED", "DATA_TEMPORAL_HOLD",
    "DAILY_READONLY_SYNC_ERROR", "CATALOG_CONFIGURATION_UNAVAILABLE",
    "CAUCION_LIVE_BOOK_REFRESH_ERROR",
)
HEALTH = (
    "PPI_PRODUCTION_AUTH", "PPI_PRODUCTION_CATALOG", "PPI_PRODUCTION_MARKETDATA",
    "PPI_DIRECT_REST_RECOVERY", "PPI_PRODUCTION_DAILY_SYNC",
    "PPI_PRODUCTION_HISTORY", "PAPER_SPOT_PIT_READINESS",
)
REASON = re.compile(r"\b(?:PPI_[A-Z0-9_]{1,75}|NATIVE_CURRENT_OR_BOOK_STALE_OR_INVALID|REFERENCE_UNUSABLE|"
                    r"CatalogIdentityError|SharedFeedUnavailable|GatewayEvidenceRejected|BudgetBackpressure|"
                    r"OperationalError|TimeoutError|ConnectionError|ReadOnlyPolicyViolation)\b")
STATE = re.compile(r"[A-Z][A-Z0-9_]{0,47}\Z")
TELEGRAM_ERROR = re.compile(r"(?:[45][0-9]{2}|HTTPError|URLError|TimeoutError|ConnectionError|"
                            r"JSONDecodeError|INVALID_RESPONSE|MISSING_MESSAGE_ID|"
                            r"DELIVERY_UNKNOWN_AFTER_RESTART|NON_BUSINESS_DAY_ROUTINE)\Z")

def timestamp(value):
    try:
        dt = datetime.fromisoformat(value)
        return dt.astimezone(timezone.utc).isoformat() if dt.utcoffset() is not None else None
    except (TypeError, ValueError, OverflowError):
        return None

def label(value):
    return value if isinstance(value, str) and STATE.fullmatch(value) else "NO_VERIFICADO"

def count(value):
    return value if type(value) is int and 0 <= value <= 100000000 else None

def reason(value):
    match = REASON.search(value or "")
    return match[0] if match else "NO_CLASSIFIED_CODE"

database = database_path()
with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=.25)) as c:
    c.execute("PRAGMA query_only=ON")
    c.execute("BEGIN")
    c.set_progress_handler(lambda: int(__import__("time").monotonic() > deadline), 1000)
    deadline = __import__("time").monotonic() + 4
    identity_from_connection(c)
    mode, real_orders, phase, auth, last_mark = c.execute(
        "SELECT mode,real_orders_sent,session_state,ppi_auth,last_market_data_at FROM observer_state WHERE id=1"
    ).fetchone()
    if mode != "PRODUCTION_PAPER" or type(real_orders) is not int or real_orders != 0:
        raise ValueError("PAPER_ZERO_REQUIRED")
    output = {"session": label(phase), "auth": label(auth), "real_orders_sent": 0,
              "last_market_data_at": timestamp(last_mark)}
    placeholders = ",".join("?" for _ in HEALTH)
    output["health"] = {component: {"state": label(state), "reason": reason(detail),
                                   "checked_at": timestamp(checked), "last_success_at": timestamp(success)}
                        for component, state, detail, checked, success in c.execute(
                            f"SELECT component,state,detail,checked_at,last_success_at FROM api_health "
                            f"WHERE component IN ({placeholders})", HEALTH)}
    today = datetime.now(timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo(
        "America/Argentina/Buenos_Aires")).replace(hour=0, minute=0, second=0, microsecond=0)
    placeholders = ",".join("?" for _ in EVENTS)
    latest = {}
    counts = Counter()
    settlement_fields = Counter()
    for kind, at, detail in c.execute(f"""
        SELECT event_type,event_at,substr(detail,1,610) FROM (
            SELECT event_type,event_at,detail FROM paper_events ORDER BY id DESC LIMIT 12000
        ) WHERE event_type IN ({placeholders}) AND julianday(event_at) >= julianday(?)
    """, (*EVENTS, today.isoformat())):
        stamp = timestamp(at)
        if stamp is None:
            continue
        if kind == "PPI_DIRECT_REST_NATIVE_WIRE_WITNESS":
            endpoint = re.search(r"endpoint=(current|book);http_status=200;", detail)
            presence = re.search(r"response_settlement_status=([A-Z_]{1,40});", detail)
            if endpoint and presence and presence[1] in {"ABSENT", "LITERAL", "INVALID", "CONFLICT",
                                                         "PRESENT", "MISSING", "MATCHED"}:
                settlement_fields[(endpoint[1], presence[1])] += 1
        code = reason(detail)
        counts[(kind, code)] += 1
        key = (kind, code)
        if key not in latest or latest[key] < stamp:
            latest[key] = stamp
    output["today_events"] = [
        {"event": key[0], "reason": key[1], "count_in_tail": counts[key], "last_at": latest[key]}
        for key in sorted(latest, key=latest.get, reverse=True)[:45]
    ]
    output["native_response_settlement"] = [
        {"endpoint": key[0], "status": key[1], "count": value}
        for key, value in sorted(settlement_fields.items())
    ]
    output["catalog_publication"] = [
        {"status": label(state), "checked_at": timestamp(at)}
        for state, at in c.execute("SELECT state,checked_at FROM candidate_refresh_state WHERE id=1")
    ]
    output["latest_introspection_deliveries"] = [
        {"state": label(state), "created_at": timestamp(at), "sent_at": timestamp(sent),
         "ack": bool(message_id) and state == "SENT" and timestamp(sent) is not None,
         "attempts": count(attempts), "error": error if TELEGRAM_ERROR.fullmatch(error or "") else None,
         "reason_no_decisions": "no_decisions_during_open_market" in body}
        for state, at, sent, message_id, attempts, error, body in c.execute("""
            SELECT state,created_at,sent_at,message_id,attempts,last_error,substr(body,1,3100)
            FROM paper_notification_outbox WHERE kind='INTROSPECTION' ORDER BY id DESC LIMIT 8
        """)
    ]
try:
    status = ReadOnlyUnixFeedClient(socket_path=gateway_socket_path(database),
                                    timeout_seconds=2).owner_status()
    local_gateway_owner_custody(status)
    output["gateway"] = {k: count(status.get(k)) for k in (
        "guard_login_wire_attempts", "guard_http_allowed", "guard_http_blocked",
        "native_last_http_status", "aux_last_http_status")}
    output["gateway"]["recovery_pending"] = status.get("session_recovery_pending") is True
    output["gateway"]["state"] = "LOCAL_OWNER_OBSERVED"
except (OSError, RuntimeError, ValueError, TypeError) as error:
    output["gateway"] = {"state": "NO_VERIFICADO", "reason": reason(str(error))}
print(json.dumps(output, separators=(",", ":"), sort_keys=True))
