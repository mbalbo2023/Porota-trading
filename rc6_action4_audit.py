"""Publica una auditoría diaria compacta para el dashboard RC6.

Lee únicamente SQLite y escribe un JSON atómico dentro de los artefactos del
dashboard. No consulta broker, no envía órdenes y no contiene secretos.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

from cg_paper_workspace import artifact_root, database_path

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
FILENAME = "rc6_action4_auditoria_latest.json"


def _rows(connection, query, params=()):
    try:
        return [dict(row) for row in connection.execute(query, params)]
    except sqlite3.OperationalError:
        return []


def _local_day(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(TZ).date().isoformat()
    except (TypeError, ValueError):
        return None


def build(db_path=None, now=None):
    """Construye un resumen compacto, reproducible y sin escritura en SQLite."""
    now = now or datetime.now(TZ)
    day = now.date().isoformat()
    db_path = Path(db_path or database_path()).resolve()
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=20)
    connection.row_factory = sqlite3.Row
    try:
        positions = _rows(connection, """
            SELECT paper_id,status,opened_at,closed_at,close_reason
            FROM paper_positions
            WHERE date(opened_at, '-3 hours')=? OR date(closed_at, '-3 hours')=?
        """, (day, day))
        gates = _rows(connection, """
            SELECT final_result FROM trade_gate_evaluations
            WHERE date(evaluated_at, '-3 hours')=?
        """, (day,))
    finally:
        connection.close()

    closed = [row for row in positions if row.get("status") == "CLOSED" and _local_day(row.get("closed_at")) == day]
    opened = [row for row in positions if row.get("status") == "OPEN" or _local_day(row.get("opened_at")) == day]
    final = Counter(str(row.get("final_result") or "UNKNOWN") for row in gates)
    reasons = Counter(str(row.get("close_reason") or "UNSPECIFIED") for row in closed)
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
            "gate_final_counts": dict(sorted(final.items())),
            "scope": "ACCIONES_Y_CEDEARS_PAPER",
        },
        "safety": {
            "sqlite": "READ_ONLY",
            "real_orders_sent": 0,
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
