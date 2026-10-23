"""Publica una auditoría diaria compacta para el dashboard RC6.

Lee únicamente SQLite y escribe un JSON atómico dentro de los artefactos del
dashboard. No consulta broker, no envía órdenes y no contiene secretos.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

from cg_paper_workspace import artifact_root, database_path

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
FILENAME = "rc6_action4_auditoria_latest.json"


def validate_weekly_payload(payload, now=None):
    """Reject stale or non-PAPER Action 4 receipts before replacing the artifact."""
    now = now or datetime.now(timezone.utc)
    if (not isinstance(payload, dict) or payload.get("scope") != "RC6" or
            payload.get("source") != "/api/observer/state" or
            payload.get("database_access_from_action") != "NO" or
            payload.get("status") != "SUCCESS" or
            payload.get("mode") != "PRODUCTION_PAPER" or
            type(payload.get("real_orders_sent")) is not int or
            payload["real_orders_sent"] != 0 or
            payload.get("decision_snapshot_scope") != "LATEST_100_ONLY; INCOMPLETE_FOR_WEEKLY_TOTAL" or
            not isinstance(payload.get("operations"), list) or
            not isinstance(payload.get("separation"), dict)):
        raise ValueError("ACTION4_WEEKLY_PAPER_SCOPE_UNVERIFIED")
    origin = payload.get("origin") or {}
    if (not isinstance(origin, dict) or
            not str(origin.get("run_id") or "").isdigit() or
            not re.fullmatch(r"[0-9a-f]{40}", str(origin.get("commit") or ""))):
        raise ValueError("ACTION4_WEEKLY_ORIGIN_UNVERIFIED")
    window = payload.get("window") or {}
    try:
        start = datetime.fromisoformat(window["from_local"])
        end = datetime.fromisoformat(window["to_local"])
        generated = datetime.fromisoformat(str(payload["generated_at_utc"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("ACTION4_WEEKLY_WINDOW_UNVERIFIED") from None
    if (any(stamp.tzinfo is None for stamp in (start, end, generated, now)) or
            start.utcoffset() != TZ.utcoffset(start) or
            end.utcoffset() != TZ.utcoffset(end) or
            generated.utcoffset() != timedelta(0) or
            not timedelta(days=6, hours=23) <= end - start <= timedelta(days=7, minutes=1) or
            not timedelta(seconds=-60) <= now - generated <= timedelta(hours=1) or
            not timedelta(seconds=-60) <= generated - end <= timedelta(hours=1)):
        raise ValueError("ACTION4_WEEKLY_WINDOW_UNVERIFIED")
    return payload


def published_audit_is_safe(payload):
    """Historical display authority, distinct from freshness for today's wheel."""
    if not isinstance(payload, dict):
        return False
    if payload.get("status") == "PAPER_READ_ONLY":
        safety = payload.get("safety") or {}
        return (isinstance(safety, dict) and
                safety.get("observer_mode") == "PRODUCTION_PAPER" and
                safety.get("sqlite") == "READ_ONLY" and
                type(safety.get("real_orders_sent")) is int and
                safety["real_orders_sent"] == 0 and
                safety.get("broker_routes_called") is False)
    try:
        stamp = datetime.fromisoformat(str(payload["generated_at_utc"]).replace("Z", "+00:00"))
        validate_weekly_payload(payload, now=stamp)
        return True
    except (KeyError, TypeError, ValueError):
        return False


def _rows(connection, query, params=()):
    # A missing or unreadable required table must fail the audit. Treating a
    # database error as zero activity would manufacture a successful receipt.
    return [dict(row) for row in connection.execute(query, params)]


def _local_day(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(TZ).date().isoformat()
    except (TypeError, ValueError):
        return None


def _columns(connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
    except sqlite3.OperationalError:
        return set()


def build(db_path=None, now=None):
    """Construye un resumen compacto, reproducible y sin escritura en SQLite."""
    now = now or datetime.now(TZ)
    day = now.date().isoformat()
    db_path = Path(db_path or database_path()).resolve()
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=20)
    connection.row_factory = sqlite3.Row
    try:
        has_session_state = "session_state" in _columns(connection, "observer_state")
        observer = connection.execute(
            "SELECT mode,real_orders_sent" + (",session_state" if has_session_state else "") +
            " FROM observer_state WHERE id=1"
        ).fetchone()
        if (observer is None or observer["mode"] != "PRODUCTION_PAPER" or
                type(observer["real_orders_sent"]) is not int or
                observer["real_orders_sent"] != 0):
            raise ValueError("ACTION4_PAPER_SAFETY_NOT_VERIFIED")
        session_state = observer["session_state"] if has_session_state else None
        position_columns = _columns(connection, "paper_positions")
        has_asset_class = "asset_class" in position_columns
        position_scope = "ALL_PAPER_POSITION_FAMILIES" if has_asset_class else "LEGACY_SCHEMA_UNFILTERED"
        asset_select = ",asset_class" if has_asset_class else ""
        positions = _rows(connection, f"""
            SELECT paper_id,status,opened_at,closed_at,close_reason{asset_select}
            FROM paper_positions
            WHERE (date(opened_at, '-3 hours')=? OR date(closed_at, '-3 hours')=?)""",
            (day, day))
        gates = _rows(connection, """
            SELECT final_result FROM trade_gate_evaluations
            WHERE date(evaluated_at, '-3 hours')=?
        """, (day,))
        events = _rows(connection, """
            SELECT event_type, COUNT(*) AS total
            FROM paper_events
            WHERE date(event_at, '-3 hours')=?
            GROUP BY event_type
            ORDER BY event_type
        """, (day,)) if _columns(connection, "paper_events") else None
        learning = _rows(connection, """
            SELECT outcome, COUNT(*) AS total
            FROM paper_learning_samples
            WHERE date(COALESCE(label_timestamp, feature_timestamp), '-3 hours')=?
            GROUP BY outcome
            ORDER BY outcome
        """, (day,)) if _columns(connection, "paper_learning_samples") else None
    finally:
        connection.close()

    closed = [row for row in positions if row.get("status") == "CLOSED" and _local_day(row.get("closed_at")) == day]
    opened = [row for row in positions if row.get("status") == "OPEN" or _local_day(row.get("opened_at")) == day]
    final = Counter(str(row.get("final_result") or "UNKNOWN") for row in gates)
    reasons = Counter(str(row.get("close_reason") or "UNSPECIFIED") for row in closed)
    events_by_type = ({str(row.get("event_type") or "UNKNOWN"): int(row.get("total") or 0)
                       for row in events} if events is not None else None)
    outcomes = ({str(row.get("outcome") or "UNLABELED"): int(row.get("total") or 0)
                 for row in learning} if learning is not None else None)
    families = Counter(
        str(row.get("asset_class") or "UNKNOWN").upper()
        for row in positions if "asset_class" in row
    )
    lessons = []
    if closed:
        lessons.append(
            f"Cierres simulados del día: {len(closed)}; causas: "
            + (", ".join(f"{name}={count}" for name, count in sorted(reasons.items()))
               or "sin causa informada")
        )
    if final:
        lessons.append(
            "Decisiones finales: "
            + ", ".join(f"{name}={count}" for name, count in sorted(final.items()))
        )
    if outcomes:
        lessons.append(
            "Etiquetas de aprendizaje incorporadas: "
            + ", ".join(f"{name}={count}" for name, count in sorted(outcomes.items()))
        )
    if not lessons:
        lessons.append("Sin actividad PAPER verificable para esta jornada; no se infieren conclusiones.")
    return {
        "schema_version": 1,
        "status": "PAPER_READ_ONLY",
        "generated_at_utc": now.astimezone(ZoneInfo("UTC")).isoformat(),
        "window": {"from_local": f"{day}T00:00:00-03:00", "to_local": now.isoformat()},
        "origin": {
            "job": "action4_audit",
            "run_id": os.getenv("GITHUB_RUN_ID") or "LOCAL_DAILY",
            "commit": os.getenv("GITHUB_SHA") or "NOT_AVAILABLE",
        },
        "separation": {
            "executed_closed": len(closed),
            "opened_simulated": len(opened),
            "hold": final.get("HOLD", 0) + final.get("BLOCKED", 0),
            "buy": final.get("OPENED_SIMULATED", 0),
            "exit_reasons": dict(sorted(reasons.items())),
        },
        "dashboard_daily_report": {
            "periodo": day,
            "closure_state": ("POSTCLOSE_VERIFIED" if session_state == "MARKET_CLOSED"
                              else "NOT_POSTCLOSE"),
            "gate_final_counts": dict(sorted(final.items())),
            "event_counts": dict(sorted(events_by_type.items())) if events_by_type is not None else None,
            "learning_outcomes": dict(sorted(outcomes.items())) if outcomes is not None else None,
            "lessons": lessons,
            "scope": "ALL_CONTRACT_FAMILIES_PAPER",
            "scope_evidence": {
                "positions": position_scope,
                "position_families": dict(sorted(families.items())),
                "gates_events_learning": "LEGACY_ACTIVITY_UNATTRIBUTED_BY_ASSET_CLASS",
                "events": "AVAILABLE" if events is not None else "SOURCE_UNAVAILABLE",
                "learning": "AVAILABLE" if learning is not None else "SOURCE_UNAVAILABLE",
                "interpretation": "Las posiciones PAPER se informan para todas las familias persistidas. Los conteos legacy de gates, eventos y aprendizaje que no guardan familia no se atribuyen retroactivamente.",
            },
        },
        "safety": {
            "sqlite": "READ_ONLY",
            "observer_mode": observer["mode"],
            "observer_session_state": session_state,
            "real_orders_sent": observer["real_orders_sent"],
            "broker_routes_called": False,
        },
    }


def publish(db_path=None, root=None, now=None):
    payload = build(db_path=db_path, now=now)
    target_root = Path(root or artifact_root(db_path or database_path())).resolve() / "reports"
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / FILENAME
    with NamedTemporaryFile("w", encoding="utf-8", dir=target_root, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(target)
    return payload


if __name__ == "__main__":
    result = publish()
    print(f"ACTION4_AUDIT_PUBLISHED={result['dashboard_daily_report']['periodo']}")
