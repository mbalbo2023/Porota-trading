#!/usr/bin/env python3
"""Build a non-secret, atomic evidence summary for RC6 intramarket candles.

This program is intentionally side-effect free with respect to RC6 data: it
accepts values collected through read-only SQLite queries and writes only its
own JSON summary file.  A caller must not report GREEN unless every invariant
below is positively proven.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def _as_nonnegative_int(value: Any, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if parsed < 0:
        raise ValueError(f"{field} must be non-negative")
    return parsed


def build_summary(
    *,
    baseline_cursor: Any,
    current_cursor: Any,
    worker_state: str,
    heartbeat_at: str,
    quick_check: str,
    dirty_bars: Any,
    observed_at: str,
) -> dict[str, Any]:
    """Return proof; schema or input uncertainty is RED, never silently green."""
    baseline = _as_nonnegative_int(baseline_cursor, "baseline_cursor")
    current = _as_nonnegative_int(current_cursor, "current_cursor")
    dirty = _as_nonnegative_int(dirty_bars, "dirty_bars")

    checks = {
        "cursor_strictly_advanced": current > baseline,
        "candle_worker_running": worker_state == "RUNNING",
        "sqlite_quick_check_ok": quick_check == "ok",
        "dirty_bars_zero": dirty == 0,
    }
    status = "GREEN" if all(checks.values()) else "RED"
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "porota-rc6-candle-inmarket-verify",
        "mode": "READ_ONLY_EVIDENCE",
        "observed_at": observed_at,
        "status": status,
        "baseline_cursor": baseline,
        "current_cursor": current,
        "worker_state": worker_state,
        "heartbeat_at": heartbeat_at,
        "sqlite_quick_check": quick_check,
        "dirty_bars": dirty,
        "checks": checks,
        "data_mutation": "NONE",
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
    parser.add_argument("--current-cursor", required=True)
    parser.add_argument("--worker-state", required=True)
    parser.add_argument("--heartbeat-at", required=True)
    parser.add_argument("--quick-check", required=True)
    parser.add_argument("--dirty-bars", required=True)
    parser.add_argument("--observed-at", default=datetime.now(timezone.utc).isoformat())
    parser.add_argument("--summary-path", required=True, type=Path)
    args = parser.parse_args()
    summary = build_summary(**vars(args))
    atomic_write_json(args.summary_path, summary)
    print(f"INMARKET_CANDLE_EVIDENCE={summary['status']}|SUMMARY:{args.summary_path}")
    return 0 if summary["status"] == "GREEN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
