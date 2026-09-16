"""Compact read-only projection for the RC6 validation milestones.

The dashboard consumes this small snapshot only.  Ledger verification and runtime
checks run in a short-lived maintenance worker, never during an HTTP request and
never against the trading write path.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import rc6_validation_dynamic as dynamic
from em_validation_campaign_rc6 import load_records

SNAPSHOT_NAME = "validation_milestones_rc6.json"
SCHEMA_VERSION = 1


def snapshot_path(root: Path | str | None = None) -> Path:
    base = Path(root) if root is not None else Path(
        os.getenv("POROTA_VALIDATION_ROOT", "/app/data/validation")
    )
    return base / SNAPSHOT_NAME


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                            prefix=".validation-", suffix=".tmp", delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def refresh(root: Path | str | None = None) -> dict[str, Any]:
    """Evaluate all milestone evidence off-request and persist a compact snapshot.

    This reads the validation ledger and runtime evidence but does not append to
    the ledger, does not write to the trading database and cannot authorize
    real-money operation.
    """
    generated_at = datetime.now(timezone.utc).isoformat()
    ledger_error = None
    try:
        records = load_records(root, verify=True)
    except Exception as exc:
        records = []
        ledger_error = type(exc).__name__

    rows = dynamic.evaluate(records)
    summary = dynamic.summary(rows)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "source": "rc6-validation-projection-worker",
        "ledger_status": "OK" if ledger_error is None else "ERROR",
        "ledger_error": ledger_error,
        "milestones": rows,
        "summary": summary,
        "real_money_state": "BLOCKED",
    }
    _write_atomic(snapshot_path(root), payload)
    return payload


def read(root: Path | str | None = None) -> dict[str, Any] | None:
    """Return only the compact projection; never scan the ledger from HTTP."""
    path = snapshot_path(root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if value.get("schema_version") != SCHEMA_VERSION or not isinstance(value.get("milestones"), dict):
        return None
    return value
