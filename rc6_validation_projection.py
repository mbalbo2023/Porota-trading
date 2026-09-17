"""Compact read-only RC6 validation projection."""
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
SCHEMA_VERSION = 3


def snapshot_path(root=None):
    return (Path(root) if root is not None else Path(os.getenv("POROTA_VALIDATION_ROOT", "/app/data/validation"))) / SNAPSHOT_NAME


def _write_atomic(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".validation-", suffix=".tmp", delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":")); handle.write("\n"); handle.flush(); os.fsync(handle.fileno()); temp = Path(handle.name)
    os.replace(temp, path)


def _read_raw(root):
    try:
        value = json.loads(snapshot_path(root).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) and isinstance(value.get("milestones"), dict) else None


def _latched_rows(previous):
    if not previous or previous.get("ledger_status") != "FULLY_VERIFIED":
        return []
    return [{"milestone": code, **dict(row)} for code, row in previous.get("milestones", {}).items() if isinstance(row, dict)]


def refresh(root=None, *, full_verify=None):
    if full_verify is None:
        full_verify = os.getenv("POROTA_VALIDATION_FULL_VERIFY", "").strip() == "1"
    previous, error = _read_raw(root), None
    if full_verify:
        try:
            records, ledger_status = load_records(root, verify=True), "FULLY_VERIFIED"
        except Exception as exc:
            records, ledger_status, error = _latched_rows(previous), "VERIFY_ERROR", type(exc).__name__
    else:
        records, ledger_status = _latched_rows(previous), "DAILY_COMPACT"
    rows = dynamic.evaluate(records, deep_db_check=bool(full_verify))
    payload = {"schema_version": SCHEMA_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(), "source": "rc6-validation-projection-worker", "ledger_status": ledger_status, "ledger_error": error, "verification_note": "Cadena completa verificada" if full_verify and not error else "Proyección diaria: la evidencia histórica no se presenta como estado actual", "milestones": rows, "summary": dynamic.summary(rows), "real_money_state": "BLOCKED"}
    _write_atomic(snapshot_path(root), payload); return payload


def read(root=None):
    value = _read_raw(root)
    return value if value and value.get("schema_version") == SCHEMA_VERSION else None
