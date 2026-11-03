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
import stat
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SCHEMA_VERSION = 4
FRESHNESS_TTL_SECONDS = 900
HEARTBEAT_TTL_SECONDS = 60
BASELINE_TTL_SECONDS = 15 * 60
DEFAULT_DB_PATH = "/app/data/paper_v17/observer_v17.db"
REQUIRED_CANDLE_TABLES = ("candle_dirty", "candle_worker_state")
REQUIRED_SOURCE_TABLE = "market_snapshots"
WORKER_IDENTITY = "candle_worker_state:1"
ART = ZoneInfo("America/Argentina/Buenos_Aires")
DELEGATED_CHECK = "DELEGATED_TO_RC6_FULL_DB_INTEGRITY"
GLOBAL_INTEGRITY_OWNER = "porota-full-db-integrity-rc6.service"
QUERY_BUDGET_SECONDS = 2.0


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


def _db_identity(db_path: str) -> str:
    """Bind adjacent evidence to one physical DB inode, never to a reused path."""
    metadata = os.stat(db_path)
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("CANDLE_DATABASE_IDENTITY_UNVERIFIED")
    return f"{metadata.st_dev}:{metadata.st_ino}"


def previous_session_baseline(*, path: Path, observed_at: str,
                              db_identity: str) -> tuple[int | None, str]:
    """Require a recent, same-ART-session sample from this DB/worker identity.

    A baseline captured on 17-Sep (or even Friday) cannot establish that the
    cursor advanced during Monday's rueda. Invalid/legacy state never votes
    GREEN; the next bounded observation becomes the new baseline instead.
    """
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None, "FIRST_SESSION_SAMPLE"
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
            or metadata.st_size > 65536):
        return None, "PREVIOUS_EVIDENCE_UNSAFE"
    try:
        prior = json.loads(path.read_text(encoding="utf-8"))
        now = _parse_iso(observed_at)
        when = _parse_iso(prior["observed_at"])
        cursor = _as_nonnegative_int(prior["current_cursor"], "previous_cursor")
        age = (now - when).total_seconds()
        if not (isinstance(prior, dict)
                and prior.get("schema_version") == SCHEMA_VERSION
                and prior.get("source") == "porota-rc6-candle-inmarket-verify"
                and prior.get("mode") == "READ_ONLY_EVIDENCE"
                and prior.get("status") in {"GREEN", "RED", "WAITING_SOURCE", "NO_VERIFICADO"}
                and prior.get("worker_identity") == WORKER_IDENTITY
                and prior.get("db_identity") == db_identity
                and prior.get("session_date") == now.astimezone(ART).date().isoformat()
                and when.astimezone(ART).date() == now.astimezone(ART).date()
                and 0 < age <= BASELINE_TTL_SECONDS):
            return None, "PREVIOUS_EVIDENCE_NOT_CONTIGUOUS"
        return cursor, "SAME_SESSION_PREVIOUS_SAMPLE"
    except (OSError, TypeError, ValueError, KeyError, UnicodeError):
        return None, "PREVIOUS_EVIDENCE_INVALID"


def collect_inputs(*, db_path: str, observed_at: str,
                   query_budget_seconds: float = QUERY_BUDGET_SECONDS) -> dict[str, Any]:
    """Check the owned candle btrees and inputs in one bounded RO snapshot.

    Global physical integrity belongs to the existing full-db audit. Repeating
    that unrelated multi-GB scan in this ten-minute evidence producer evicts
    the live PAPER working set; a candle proof must not claim global integrity.
    """
    if (type(query_budget_seconds) not in (int, float)
            or not 0 < query_budget_seconds <= QUERY_BUDGET_SECONDS):
        raise ValueError("CANDLE_EVIDENCE_QUERY_BUDGET_INVALID")
    observed = _parse_iso(observed_at).isoformat()
    identity = _db_identity(db_path)
    deadline = time.monotonic() + query_budget_seconds
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True,
                           timeout=min(query_budget_seconds, .25))
    try:
        conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 100)
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        required = set(REQUIRED_CANDLE_TABLES)
        missing = sorted((required | {REQUIRED_SOURCE_TABLE}) - tables)
        if missing:
            raise RuntimeError("MISSING_REQUIRED_TABLES:" + ",".join(missing))
        table_checks = {}
        for table in REQUIRED_CANDLE_TABLES:
            # Names are fixed code-owned identifiers, never SQL/user input.
            rows = conn.execute(f"PRAGMA quick_check('{table}')").fetchall()
            table_checks[table] = "ok" if rows == [("ok",)] else "FAILED"
        if any(value != "ok" for value in table_checks.values()):
            raise sqlite3.DatabaseError("CANDLE_TABLE_INTEGRITY_UNVERIFIED")
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
        # Indexed INTEGER PRIMARY KEY: bounded high-water distinguishes idle
        # source from a materializer that has accumulated unread snapshots.
        latest_snapshot_cursor = conn.execute(
            "SELECT COALESCE(MAX(id),0) FROM market_snapshots"
        ).fetchone()[0]
        if identity != _db_identity(db_path):
            raise RuntimeError("CANDLE_DATABASE_IDENTITY_CHANGED")
        return {
            "current_cursor": worker[0],
            "heartbeat_at": worker[1],
            "worker_state": worker[2],
            "worker_identity": WORKER_IDENTITY,
            "db_identity": identity,
            "latest_snapshot_cursor": latest_snapshot_cursor,
            "quick_check": DELEGATED_CHECK,
            "table_quick_checks": table_checks,
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
    table_quick_checks: dict[str, str] | None = None,
    baseline_reason: str = "BASELINE_UNVERIFIED",
    db_identity: str = "UNVERIFIED", worker_identity: str = WORKER_IDENTITY,
    latest_snapshot_cursor: Any = None,
) -> dict[str, Any]:
    """GREEN proves same-session advance, fresh heartbeat and no backlog.

    A healthy worker without new source events is WAITING_SOURCE, not RED or
    GREEN. No prior same-session sample is NO_VERIFICADO even if the cursor is
    greater than a historical marker. Confirmed stale health/backlog is RED.
    """
    baseline = (_as_nonnegative_int(baseline_cursor, "baseline_cursor")
                if baseline_cursor is not None else None)
    current = _as_nonnegative_int(current_cursor, "current_cursor")
    due = _as_nonnegative_int(dirty_due_bars, "dirty_due_bars")
    open_bars = _as_nonnegative_int(dirty_open_bars, "dirty_open_bars")
    invalid = _as_nonnegative_int(invalid_dirty_timestamp_count, "invalid_dirty_timestamp_count")
    observed = _parse_iso(observed_at)
    latest = (_as_nonnegative_int(latest_snapshot_cursor, "latest_snapshot_cursor")
              if latest_snapshot_cursor is not None else None)
    try:
        heartbeat_age = (observed - _parse_iso(heartbeat_at)).total_seconds()
    except (TypeError, ValueError):
        heartbeat_age = None
    heartbeat_fresh = (heartbeat_age is not None and
                       0 <= heartbeat_age <= HEARTBEAT_TTL_SECONDS)
    verified_baseline = baseline is not None and baseline_reason == "SAME_SESSION_PREVIOUS_SAMPLE"
    scoped = table_quick_checks is not None
    integrity_ok = (
        quick_check == DELEGATED_CHECK
        and type(table_quick_checks) is dict
        and set(table_quick_checks) == set(REQUIRED_CANDLE_TABLES)
        and all(value == "ok" for value in table_quick_checks.values())
    ) if scoped else quick_check == "ok"
    checks = {
        "schema_collected": not collection_error,
        "cursor_strictly_advanced": verified_baseline and current > baseline,
        "cursor_not_regressed": baseline is None or current >= baseline,
        "candle_worker_running": worker_state == "RUNNING",
        "worker_heartbeat_fresh": heartbeat_fresh,
        "market_snapshots_caught_up": latest is not None and latest <= current,
        ("candle_tables_quick_check_ok" if scoped else "sqlite_quick_check_ok"): integrity_ok,
        "dirty_due_bars_zero": due == 0,
        "dirty_timestamps_valid": invalid == 0,
    }
    health_checks = ("schema_collected", "cursor_not_regressed",
                     "candle_worker_running", "worker_heartbeat_fresh",
                     "market_snapshots_caught_up", "dirty_due_bars_zero",
                     "dirty_timestamps_valid",
                     "candle_tables_quick_check_ok" if scoped else "sqlite_quick_check_ok")
    if any(not checks[key] for key in health_checks):
        status = "RED"
    elif not verified_baseline:
        status = "NO_VERIFICADO"
    elif not checks["cursor_strictly_advanced"]:
        status = "WAITING_SOURCE"
    else:
        status = "GREEN"
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "porota-rc6-candle-inmarket-verify",
        "mode": "READ_ONLY_EVIDENCE",
        "observed_at": observed.isoformat(),
        "session_date": observed.astimezone(ART).date().isoformat(),
        "expires_at": (observed + timedelta(seconds=FRESHNESS_TTL_SECONDS)).isoformat(),
        "freshness_ttl_seconds": FRESHNESS_TTL_SECONDS,
        "status": status,
        "baseline_cursor": baseline,
        "baseline_reason": baseline_reason,
        "current_cursor": current,
        "db_identity": db_identity,
        "worker_identity": worker_identity,
        "latest_snapshot_cursor": latest,
        "worker_state": worker_state,
        "heartbeat_at": heartbeat_at,
        "heartbeat_age_seconds": heartbeat_age,
        "heartbeat_ttl_seconds": HEARTBEAT_TTL_SECONDS,
        "sqlite_quick_check": quick_check,
        "integrity_scope": "CANDLE_EVIDENCE_TABLES" if scoped else "FULL_DATABASE_LEGACY_CALLER",
        "table_quick_checks": dict(table_quick_checks) if type(table_quick_checks) is dict else None,
        "global_database_integrity_owner": GLOBAL_INTEGRITY_OWNER,
        "global_database_integrity_status": "NOT_ASSERTED_BY_THIS_EVIDENCE",
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
    # Compatibility input deliberately has no authority to bootstrap GREEN.
    parser.add_argument("--baseline-cursor", required=False)
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--observed-at", default=datetime.now(timezone.utc).isoformat())
    parser.add_argument("--summary-path", required=True, type=Path)
    args = parser.parse_args()
    try:
        collected = collect_inputs(db_path=args.db_path, observed_at=args.observed_at)
        baseline, reason = previous_session_baseline(
            path=args.summary_path, observed_at=args.observed_at,
            db_identity=collected["db_identity"])
        summary = build_summary(
            baseline_cursor=baseline, baseline_reason=reason,
            observed_at=args.observed_at, **collected
        )
    except Exception as exc:
        summary = build_summary(
            baseline_cursor=None, current_cursor=0, worker_state="UNKNOWN",
            heartbeat_at="", quick_check="UNKNOWN", dirty_due_bars=0, dirty_open_bars=0,
            invalid_dirty_timestamp_count=0, observed_at=args.observed_at,
            collection_error=f"{type(exc).__name__}:{exc}",
            table_quick_checks={},
        )
    atomic_write_json(args.summary_path, summary)
    print(f"INMARKET_CANDLE_EVIDENCE={summary['status']}|SUMMARY:{args.summary_path}")
    return 0 if summary["status"] in {"GREEN", "WAITING_SOURCE", "NO_VERIFICADO"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
