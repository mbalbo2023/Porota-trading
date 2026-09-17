#!/usr/bin/env python3
"""Read-only, durable intramarket-candle evidence for RC6.

The only write is an atomic JSON publication under the supplied evidence path.
SQLite is always opened mode=ro/query_only; no PPI route, order route, history
backfill, or PPI Watch resource is accessed.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
FRESHNESS_TTL_SECONDS = 900
DEFAULT_DB_PATH = "/app/data/paper_v17/observer_v17.db"


def _as_nonnegative_int(value: Any, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if parsed < 0:
        raise ValueError(f"{field} must be non-negative")
    return parsed


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must carry timezone")
    return parsed.astimezone(timezone.utc)


def is_fresh(summary: dict[str, Any], *, now: datetime | None = None) -> bool:
    """Consumers must recompute freshness; a previous GREEN never stays current."""
    now = now or datetime.now(timezone.utc)
    try:
        observed = _parse_iso(str(summary["observed_at"]))
        ttl = _as_nonnegative_int(summary["freshness_ttl_seconds"], "freshness_ttl_seconds")
    except (KeyError, ValueError):
        return False
    age = (now.astimezone(timezone.utc) - observed).total_seconds()
    return 0 <= age <= ttl


def collect_inputs(*, db_path: str, observed_at: str) -> dict[str, Any]:
    """Collect the exact evidence inputs via schema-validated read-only SQLite."""
    observed = _parse_iso(observed_at).isoformat()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=20)
    try:
        conn.execute("PRAGMA query_only=ON")
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        required = {"candle_worker_state", "candle_dirty"}
        missing = sorted(required - tables)
        if missing:
            raise RuntimeError("MISSING_REQUIRED_TABLES:" + ",".join(missing))
        quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]
        worker = conn.execute(
            "SELECT cursor,heartbeat_at,state FROM candle_worker_state WHERE id=1"
        ).fetchone()
        if worker is None:
            raise RuntimeError("CANDLE_WORKER_STATE_MISSING")
        # candle_dirty is a queue: rows whose bar_end is in the future are normal
        # open bars, not an integrity defect. Only overdue rows block GREEN.
        invalid_dirty_timestamp_count = conn.execute(
            "SELECT COUNT(*) FROM candle_dirty WHERE julianday(bar_end) IS NULL"
        ).fetchone()[0]
        dirty_due_bars = conn.execute(
            "SELECT COUNT(*) FROM candle_dirty "
            "WHERE julianday(bar_end) <= julianday(?)", (observed,)
        ).fetchone()[0]
        dirty_open_bars = conn.execute(
            "SELECT COUNT(*) FROM candle_dirty "
            "WHERE julianday(bar_end) > julianday(?)", (observed,)
        ).fetchone()[0]
        return {
            "current_cursor": worker[0],
            "heartbeat_at": worker[1],
            "worker_state": worker[2],
            "quick_check": quick_check,
            "dirty_due_bars": dirty_due_bars,
            "dirty_open_bars": dirty_open_bars,
            "invalid_dirty_timestamp_count": invalid_dirty_timestamp_count,
            "required_tables": sorted(required),
        }
    finally:
        conn.close()


def build_summary(
    *, baseline_cursor: Any, current_cursor: Any, worker_state: str,
    heartbeat_at: str, quick_check: str, dirty_due_bars: Any,
    dirty_open_bars: Any, invalid_dirty_timestamp_count: Any,
    observed_at: str, collection_error: str | None = None,
    required_tables: list[str] | None = None,
) -> dict[str, Any]:
    """Build fail-closed proof. Open bars are reported but never treated as dirty."""
    baseline = _as_nonnegative_int(baseline_cursor, "baseline_cursor")
    current = _as_nonnegative_int(current_cursor, "current_cursor")
    due = _as_nonnegative_int(dirty_due_bars, "dirty_due_bars")
    open_bars = _as_nonnegative_int(dirty_open_bars, "dirty_open_bars")
    invalid = _as_nonnegative_int(invalid_dirty_timestamp_count, "invalid_dirty_timestamp_count")
    observed = _parse_iso(observed_at)
    checks = {
        "schema_collected": not collection_error,
        "cursor_strictly_advanced": current > baseline,
        "candle_worker_running": worker_state == "RUNNING",
        "sqlite_quick_check_ok": quick_check == "ok",
        "dirty_due_bars_zero": due == 0,
        "dirty_timestamps_valid": invalid == 0,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "porota-rc6-candle-inmarket-verify",
        "mode": "READ_ONLY_EVIDENCE",
        "observed_at": observed.isoformat(),
        "expires_at": (observed + timedelta(seconds=FRESHNESS_TTL_SECONDS)).isoformat(),
        "freshness_ttl_seconds": FRESHNESS_TTL_SECONDS,
        "status": "GREEN" if all(checks.values()) else "RED",
        "baseline_cursor": baseline,
        "current_cursor": current,
        "worker_state": worker_state,
        "heartbeat_at": heartbeat_at,
        "sqlite_quick_check": quick_check,
        "dirty_due_bars": due,
        "dirty_open_bars": open_bars,
        "invalid_dirty_timestamp_count": invalid,
        "required_tables": required_tables or [],
        "checks": checks,
        "collection_error": collection_error,
        "data_mutation": "EVIDENCE_JSON_ONLY",
        "orders": "NOT_CALLED",
    }


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Publish complete JSON or retain the previous file; never a partial file."""
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-cursor", required=True)
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--observed-at", default=datetime.now(timezone.utc).isoformat())
    parser.add_argument("--summary-path", required=True, type=Path)
    args = parser.parse_args()
    try:
        collected = collect_inputs(db_path=args.db_path, observed_at=args.observed_at)
        summary = build_summary(
            baseline_cursor=args.baseline_cursor, observed_at=args.observed_at, **collected
        )
    except Exception as exc:
        summary = build_summary(
            baseline_cursor=args.baseline_cursor, current_cursor=0, worker_state="UNKNOWN",
            heartbeat_at="", quick_check="UNKNOWN", dirty_due_bars=0, dirty_open_bars=0,
            invalid_dirty_timestamp_count=0, observed_at=args.observed_at,
            collection_error=f"{type(exc).__name__}:{exc}",
        )
    atomic_write_json(args.summary_path, summary)
    print(f"INMARKET_CANDLE_EVIDENCE={summary['status']}|SUMMARY:{args.summary_path}")
    return 0 if summary["status"] == "GREEN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
