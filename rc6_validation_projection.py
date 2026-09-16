"""Compact read-only projection for the RC6 validation milestones.

The dashboard consumes this small snapshot only. Daily projection is bounded:
it evaluates live M0/M1 safety and retains previously verified evidence. Full
ledger-chain verification is explicit (POROTA_VALIDATION_FULL_VERIFY=1), never
part of an HTTP request or a deploy.
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
SCHEMA_VERSION = 2


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


def _read_raw(root: Path | str | None) -> dict[str, Any] | None:
    try:
        value = json.loads(snapshot_path(root).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) and isinstance(value.get("milestones"), dict) else None


def _latched_rows(previous: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Convert the compact prior projection to non-upgrading campaign evidence.

    Only a snapshot produced after a full chain verification can be used as a
    latch. This prevents a daily worker from turning an unverified old record
    into a GREEN milestone.
    """
    if not previous or previous.get("ledger_status") != "FULLY_VERIFIED":
        return []
    rows = []
    for code, row in previous.get("milestones", {}).items():
        if not isinstance(row, dict):
            continue
        rows.append({"milestone": code, "state": row.get("state", "GRAY"),
                     "compliance_pct": row.get("compliance_pct", 0),
                     "observed_evidence": row.get("observed_evidence", ""),
                     "deviation": row.get("deviation", ""),
                     "blocker": row.get("blocker", ""),
                     "next_action": row.get("next_action", "")})
    return rows


def refresh(root: Path | str | None = None, *, full_verify: bool | None = None) -> dict[str, Any]:
    """Persist a bounded daily projection.

    `full_verify=True` is an operator-triggered integrity task. The normal
    daily path never scans the append-only ledger: it evaluates M0/M1 directly
    and carries only already fully-verified evidence. Neither path writes the
    trading DB nor can authorize real-money operation.
    """
    generated_at = datetime.now(timezone.utc).isoformat()
    if full_verify is None:
        full_verify = os.getenv("POROTA_VALIDATION_FULL_VERIFY", "").strip() == "1"
    previous = _read_raw(root)
    ledger_error = None
    if full_verify:
        try:
            records = load_records(root, verify=True)
            ledger_status = "FULLY_VERIFIED"
        except Exception as exc:
            records = _latched_rows(previous)
            ledger_error = type(exc).__name__
            ledger_status = "VERIFY_ERROR"
    else:
        records = _latched_rows(previous)
        ledger_status = "DAILY_COMPACT"

    rows = dynamic.evaluate(records, deep_db_check=bool(full_verify))
    summary = dynamic.summary(rows)
    payload = {"schema_version": SCHEMA_VERSION, "generated_at": generated_at,
        "source": "rc6-validation-projection-worker", "ledger_status": ledger_status,
        "ledger_error": ledger_error,
        "verification_note": ("Cadena completa verificada" if full_verify and not ledger_error
                              else "Proyección diaria compacta; no recorre el ledger completo"),
        "milestones": rows, "summary": summary, "real_money_state": "BLOCKED"}
    _write_atomic(snapshot_path(root), payload)
    return payload


def read(root: Path | str | None = None) -> dict[str, Any] | None:
    """Return only the compact projection; never scan the ledger from HTTP."""
    value = _read_raw(root)
    if not value or value.get("schema_version") != SCHEMA_VERSION:
        return None
    return value
