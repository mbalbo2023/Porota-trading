#!/usr/bin/env python3
"""RC5 P0-4: inventario PAPER estrictamente read-only.

No corrige, no borra y no resetea estado. Produce evidencia compacta para que
el operador decida si el lunes puede comenzar con un estado interpretable.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from cg_paper_workspace import database_path
from _version import VERSION

DB = Path(os.getenv("POROTA_PAPER_DB", str(database_path())))


def scalar(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def table_exists(conn, name):
    return bool(scalar(conn, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))


def count(conn, table, where="", params=()):
    if not table_exists(conn, table):
        return None
    clause = f" WHERE {where}" if where else ""
    return int(scalar(conn, f"SELECT COUNT(*) FROM {table}{clause}", params) or 0)


def last_value(conn, table, column, order_column):
    if not table_exists(conn, table):
        return None
    row = conn.execute(
        f"SELECT {column} FROM {table} ORDER BY {order_column} DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def main():
    result = {
        "schema": "POROTA_RC5_PAPER_STATE_INVENTORY_V1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "version": VERSION,
        "db": str(DB),
        "read_only": True,
        "status": "RED",
        "problems": [],
    }
    if not DB.exists():
        result["problems"].append("PAPER_DB_MISSING")
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 2

    uri = f"file:{DB}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
    except Exception as exc:
        result["problems"].append(f"PAPER_DB_READ_OPEN_FAILED:{type(exc).__name__}")
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 2

    with conn:
        try:
            result["quick_check"] = scalar(conn, "PRAGMA quick_check")
        except Exception as exc:
            result["quick_check"] = f"ERROR:{type(exc).__name__}"

        if table_exists(conn, "observer_state"):
            row = conn.execute("SELECT * FROM observer_state WHERE id=1").fetchone()
            result["observer_state"] = dict(row) if row else None
        else:
            result["observer_state"] = None

        result["counts"] = {
            "positions_open": count(conn, "paper_positions", "status='OPEN'"),
            "positions_closed": count(conn, "paper_positions", "status='CLOSED'"),
            "fills": count(conn, "paper_fills"),
            "decisions": count(conn, "paper_decisions"),
            "events": count(conn, "paper_events"),
            "learning_samples": count(conn, "paper_learning_samples"),
            "market_snapshots": count(conn, "market_snapshots"),
            "failed_notifications": count(conn, "notification_outbox", "status='FAILED'"),
            "pending_notifications": count(conn, "notification_outbox", "status='PENDING'"),
        }
        result["latest"] = {
            "decision_at": last_value(conn, "paper_decisions", "decided_at", "id"),
            "snapshot_at": last_value(conn, "market_snapshots", "observed_at", "id"),
            "event_at": last_value(conn, "paper_events", "event_at", "id"),
        }

        state = result.get("observer_state") or {}
        if result.get("quick_check") != "ok":
            result["problems"].append("PAPER_DB_QUICK_CHECK_NOT_OK")
        if state and int(state.get("real_orders_sent") or 0) != 0:
            result["problems"].append("REAL_ORDERS_SENT_NONZERO")
        if state and str(state.get("mode") or "").upper() not in {"PRODUCTION_PAPER", ""}:
            result["problems"].append("UNEXPECTED_PAPER_MODE")

    result["status"] = "GREEN" if not result["problems"] else "RED"
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str))
    return 0 if result["status"] == "GREEN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
