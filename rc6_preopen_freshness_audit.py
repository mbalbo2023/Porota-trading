"""Read-only pre-open freshness audit for RC6 ACCIONES/CEDEARs.

It does not ingest, repair, backfill or mutate market history.  It publishes
only a compact evidence snapshot so the dashboard and validation campaign can
distinguish freshness from coverage before the trading session.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

import ak_byma_calendar as byma_calendar
from ek_history_freshness_metrics_rc5 import freshness_qualified_metrics

TZ_AR = ZoneInfo("America/Argentina/Buenos_Aires")
DEFAULT_DB = os.getenv("POROTA_PAPER_DB", "/app/data/paper_v17/observer_v17.db")
DEFAULT_ROOT = Path(os.getenv("POROTA_VALIDATION_ROOT", "/app/data/validation"))
SNAPSHOT_NAME = "preopen_freshness_rc6.json"


def snapshot_path(root: Path | str | None = None) -> Path:
    return (Path(root) if root is not None else DEFAULT_ROOT) / SNAPSHOT_NAME


def previous_expected_byma_session(as_of: date) -> date:
    """Return the last audited BYMA session strictly before the pre-open date."""
    day = as_of - timedelta(days=1)
    for _ in range(400):
        if day.year not in byma_calendar.ANIOS_AUDITADOS:
            raise ValueError("PREOPEN_CALENDAR_YEAR_NOT_AUDITED")
        if byma_calendar.es_dia_habil_operativo(day):
            return day
        day -= timedelta(days=1)
    raise RuntimeError("PREOPEN_SESSION_LOOKBACK_GUARD")


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".preopen-freshness-", suffix=".tmp", delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def collect(*, db_path: str = DEFAULT_DB, root: Path | str | None = None, as_of: date | None = None) -> dict:
    run_day = as_of or datetime.now(TZ_AR).date()
    expected = previous_expected_byma_session(run_day)
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5) as conn:
            conn.execute("PRAGMA query_only=ON")
            metrics = freshness_qualified_metrics(conn, expected_session_date=expected)
    except (sqlite3.Error, OSError, ValueError) as exc:
        metrics = {"available": False, "reason": f"PREOPEN_FRESHNESS_UNAVAILABLE:{type(exc).__name__}", "expected_session_date": expected.isoformat()}
    target = int(metrics.get("target_total") or 0)
    fresh = int(metrics.get("fresh_total") or 0)
    state = "READY" if metrics.get("available") and target and fresh == target else ("DEGRADED" if metrics.get("available") else "UNAVAILABLE")
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "rc6-preopen-freshness-audit/read-only",
        "scope": ["ACCIONES", "CEDEARS"],
        "state": state,
        "expected_session_date": expected.isoformat(),
        "fresh_total": fresh,
        "target_total": target,
        "stale_ge90_count": int(metrics.get("stale_ge90_count") or 0),
        "metrics": metrics,
        "readiness_implication": "NONE",
        "execution_price_implication": "NONE",
        "history_writes": False,
    }


def refresh(**kwargs) -> dict:
    payload = collect(**kwargs)
    _write_atomic(snapshot_path(kwargs.get("root")), payload)
    return payload


def main() -> int:
    payload = refresh()
    print(f"RC6_PREOPEN_FRESHNESS={payload['state']} FRESH={payload['fresh_total']}/{payload['target_total']} EXPECTED={payload['expected_session_date']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
