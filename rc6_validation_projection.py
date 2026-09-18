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


def _apply_daily_operational_audit(rows: dict[str, dict]) -> dict[str, dict]:
    """Attach today's read-only IOL/history pulses only to the daily audit."""
    result = {code: dict(row) for code, row in rows.items()}
    observed, target = dynamic._iol_coverage()
    if target:
        pct = min(95, round(observed * 100 / target))
        result["M2"].update({
            "state": "YELLOW", "compliance_pct": pct, "claim_status": "PENDING",
            "implementation_status": "IMPLEMENTED",
            "observed_evidence": f"IOL SHADOW OBSERVE_ONLY: {observed}/{target} instrumentos cacheados; contratos fuera del universo actual permanecen sólo como auditoría.",
            "deviation": "Cobertura o evidencia contractual aún incompleta; no afecta READY/HOLD ni órdenes.",
            "blocker": "Completar rotación y publicar evidencia disponible, sin reactivar fuentes descartadas.",
            "next_action": "Auditoría diaria de cobertura IOL y contratos operativos.",
            "evidence_ref": "daily-read-only-iol-audit",
        })
    else:
        result["M2"].update({
            "implementation_status": "IMPLEMENTED",
            "observed_evidence": "Implementación IOL SHADOW disponible; cache de cobertura no observable desde este worker.",
            "next_action": "Revisar la próxima auditoría diaria.",
        })
    result["M3"].update({
        "state": "YELLOW", "compliance_pct": 60, "claim_status": "PENDING",
        "implementation_status": "IMPLEMENTED",
        "observed_evidence": "History Store v2 operativo se audita por cobertura, profundidad y freshness; esta auditoría no reingesta históricos.",
        "deviation": "La frescura se mide por instrumento y no se infiere como cobertura faltante.",
        "blocker": "Publicar el pulso consolidado de freshness.",
        "next_action": "Auditoría diaria de históricos sin catch-up automático.",
        "evidence_ref": "daily-read-only-history-audit",
    })
    return result


def refresh(root=None, *, full_verify=None):
    if full_verify is None:
        # This is the once-per-day audit: verify the append-only chain and run
        # the read-only DB integrity pulse. It never calls market ingestion.
        full_verify = os.getenv("POROTA_VALIDATION_FULL_VERIFY", "1").strip() != "0"
    previous, error = _read_raw(root), None
    if full_verify:
        try:
            records, ledger_status = load_records(root, verify=True), "FULLY_VERIFIED"
        except Exception as exc:
            records, ledger_status, error = _latched_rows(previous), "VERIFY_ERROR", type(exc).__name__
    else:
        records, ledger_status = _latched_rows(previous), "DAILY_COMPACT"
    rows = dynamic.evaluate(records, deep_db_check=bool(full_verify))
    if full_verify:
        rows = _apply_daily_operational_audit(rows)
    payload = {"schema_version": SCHEMA_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(), "source": "rc6-validation-projection-worker", "ledger_status": ledger_status, "ledger_error": error, "verification_note": "Auditoría diaria: cadena, DB y pulsos operativos verificados por lectura" if full_verify and not error else "Proyección diaria limitada: la evidencia histórica no se presenta como estado actual", "milestones": rows, "summary": dynamic.summary(rows), "real_money_state": "BLOCKED"}
    _write_atomic(snapshot_path(root), payload); return payload


def read(root=None):
    value = _read_raw(root)
    return value if value and value.get("schema_version") == SCHEMA_VERSION else None
