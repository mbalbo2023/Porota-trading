"""Compact read-only RC6 validation projection."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import rc6_validation_dynamic as dynamic
from em_validation_campaign_rc6 import load_records
from ek_history_freshness_metrics_rc5 import freshness_qualified_metrics

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
    """Retain the last published evidence in a bounded daily projection.

    A daily compact audit is intentionally not a full hash-chain verification,
    but it must not erase implementation/progress fields that were already
    observed.  Live claims are still recomputed by dynamic.evaluate().
    """
    if not previous or not isinstance(previous.get("milestones"), dict):
        return []
    return [{"milestone": code, **dict(row)} for code, row in previous["milestones"].items() if isinstance(row, dict)]


def _freshness_pulse() -> dict:
    """Prefer the explicit pre-open evidence snapshot; never trigger ingestion."""
    root = Path(os.getenv("POROTA_VALIDATION_ROOT", "/app/data/validation"))
    try:
        audit = json.loads((root / "preopen_freshness_rc6.json").read_text(encoding="utf-8"))
        metrics = audit.get("metrics") if isinstance(audit.get("metrics"), dict) else {}
        if audit.get("state") and metrics:
            return dict(metrics, available=bool(metrics.get("available")), preopen_state=audit.get("state"),
                        expected_session_date=audit.get("expected_session_date"))
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    db_path = os.getenv("POROTA_PAPER_DB", "/app/data/paper_v17/observer_v17.db")
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5) as conn:
            conn.execute("PRAGMA query_only=ON")
            return freshness_qualified_metrics(conn)
    except (sqlite3.Error, OSError, ValueError) as exc:
        return {"available": False, "reason": f"FRESHNESS_PULSE_UNAVAILABLE:{type(exc).__name__}"}


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
    freshness = _freshness_pulse()
    if freshness.get("available"):
        target = max(0, int(freshness.get("target_total") or 0))
        fresh = max(0, int(freshness.get("fresh_total") or 0))
        pct = round(fresh * 100 / target) if target else 0
        stale = max(0, int(freshness.get("stale_ge90_count") or 0))
        result["M3"].update({
            "state": "GREEN" if target and fresh == target else "YELLOW",
            "compliance_pct": min(100, pct), "claim_status": "VERIFIED_CURRENT",
            "implementation_status": "IMPLEMENTED", "work_status": "IN_PROGRESS",
            "evidence_status": "CURRENT",
            "observed_evidence": f"Pulso de freshness de sólo lectura: {fresh}/{target} instrumentos frescos; stale >=90 barras: {stale}; sesión esperada={freshness.get('expected_session_date') or freshness.get('store_latest_date') or 'UNKNOWN'}.",
            "deviation": "La frescura se mide por instrumento y no se infiere como cobertura faltante.",
            "blocker": "Ninguno" if fresh == target and target else "Publicar auditoría pre-rueda contra la última sesión esperada.",
            "next_action": "Auditoría pre-rueda de freshness, sin catch-up automático ni escrituras históricas.",
            "evidence_ref": "daily-read-only-history-freshness-pulse",
        })
    else:
        result["M3"].update({
            "state": "YELLOW", "compliance_pct": 0, "claim_status": "PENDING",
            "implementation_status": "IN_PROGRESS", "work_status": "IN_PROGRESS",
            "evidence_status": "UNAVAILABLE",
            "observed_evidence": "El pulso de freshness no estuvo disponible desde esta proyección de sólo lectura.",
            "deviation": "No se inventa cobertura ni frescura.",
            "blocker": str(freshness.get("reason") or "FRESHNESS_PULSE_UNAVAILABLE"),
            "next_action": "Restablecer sólo la evidencia de freshness pre-rueda; no reingestar históricos.",
            "evidence_ref": "daily-read-only-history-audit",
        })
    return result


def refresh(root=None, *, full_verify=None, daily_audit=False):
    if full_verify is None:
        # Full hash-chain verification can be expensive. The daily worker is
        # intentionally bounded; a deep verification remains an explicit task.
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
    if full_verify or daily_audit:
        rows = _apply_daily_operational_audit(rows)
    payload = {"schema_version": SCHEMA_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(), "source": "rc6-validation-projection-worker", "audit_kind": "DEEP_READ_ONLY" if full_verify else ("DAILY_READ_ONLY" if daily_audit else "LIMITED_PROJECTION"), "ledger_status": ledger_status, "ledger_error": error, "verification_note": "Auditoría profunda: cadena y DB verificadas por lectura" if full_verify and not error else ("Auditoría diaria acotada: pulsos operativos actuales; la cadena profunda queda programada por separado" if daily_audit else "Proyección diaria limitada: la evidencia histórica no se presenta como estado actual"), "milestones": rows, "summary": dynamic.summary(rows), "real_money_state": "BLOCKED"}
    _write_atomic(snapshot_path(root), payload); return payload


def read(root=None):
    value = _read_raw(root)
    return value if value and value.get("schema_version") == SCHEMA_VERSION else None
