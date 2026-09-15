"""RC6 dynamic validation evaluator.

Read-only, idempotent evaluator for /validacion. It derives the current
milestone state from runtime evidence and the append-only ledger. It never
writes the trading DB, never appends campaign records, and never authorizes
real-money operation.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from em_validation_campaign_rc6 import MILESTONES, latest_by_milestone


def _db_path():
    return os.getenv("POROTA_PAPER_DB", "/app/data/paper_v17/observer_v17.db")


def _db_ok():
    path = Path(_db_path())
    if not path.exists():
        return False, "Base PAPER no encontrada"
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3) as c:
            result = c.execute("PRAGMA quick_check").fetchone()
        return bool(result and result[0] == "ok"), str(result[0] if result else "sin resultado")
    except Exception as exc:
        return False, type(exc).__name__


def _runtime_safety():
    mode = os.getenv("POROTA_MODE", os.getenv("DASHBOARD_OPERATION_MODE", "")).upper()
    execution = os.getenv("EXECUTION", "").upper()
    routes = os.getenv("REAL_ORDER_ROUTES", os.getenv("REAL_ORDER_CAPABILITY", "")).upper()
    safe = mode in {"PRODUCTION_PAPER", "PAPER"} and execution in {"SIMULATED", "PAPER"} and (
        not routes or routes in {"BLOCKED", "NOT_CALLED", "NONE", "DISABLED"}
    )
    return safe, f"mode={mode or 'UNKNOWN'} execution={execution or 'UNKNOWN'} routes={routes or 'UNSET'}"


def _dynamic_row(milestone, prior):
    prior = dict(prior or {})
    code = milestone.code
    state = "GRAY"
    observed = "Evaluación dinámica pendiente"
    deviation = "Sin evidencia suficiente"
    blocker = "Requiere evidencia verificable"
    next_action = "Ejecutar la validación correspondiente y publicar su evidencia"
    pct = 0
    ok_db, db_detail = _db_ok()
    safe, safety_detail = _runtime_safety()

    if code == "M0" and ok_db:
        state, pct = "GREEN", 100
        observed = f"Base accesible en modo read-only; quick_check={db_detail}"
        deviation = "Ninguna detectada por este evaluador"
        blocker = "Ninguno en la evidencia local"
        next_action = "Mantener backup, timers y restart behavior bajo observación"
    elif code == "M1" and safe:
        state, pct = "GREEN", 100
        observed = f"Invariantes PAPER observadas: {safety_detail}; real_orders_sent=0 requerido"
        deviation = "Ninguna detectada por este evaluador"
        blocker = "Ninguno en la evidencia local"
        next_action = "Revalidar en cada deploy y no promover a real-money"
    elif code == "M11":
        state, pct = "RED", 0
        observed = "Órdenes con dinero real permanecen bloqueadas por política"
        deviation = "El objetivo real-money no está autorizado"
        blocker = "M11 bloqueado permanentemente en este alcance"
        next_action = "Ninguna: conservar bloqueado"
    elif prior:
        # Existing append-only evidence remains authoritative for campaign history,
        # but the dynamic evaluator never upgrades it without fresh evidence.
        state = str(prior.get("state") or "GRAY")
        pct = prior.get("compliance_pct") or 0
        observed = prior.get("observed_evidence") or prior.get("observed") or observed
        deviation = prior.get("deviation") or deviation
        blocker = prior.get("blocker") or blocker
        next_action = prior.get("next_action") or next_action

    return {
        "state": state,
        "compliance_pct": pct,
        "objective": milestone.goal,
        "expected_evidence": ", ".join(milestone.exit_criteria),
        "observed_evidence": observed,
        "deviation": deviation,
        "blocker": blocker,
        "next_action": next_action,
        "evidence_ref": "dynamic-read-only-evaluator",
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "dynamic": True,
    }


def evaluate(records=()):
    prior = latest_by_milestone(records or [])
    return {milestone.code: _dynamic_row(milestone, prior.get(milestone.code))
            for milestone in MILESTONES}


def summary(rows):
    values = list(rows.values())
    green = [code for code, row in rows.items() if row.get("state") == "GREEN"]
    critical_red = [code for code, row in rows.items()
                    if row.get("state") == "RED" and any(m.code == code and m.critical for m in MILESTONES)]
    return {
        "milestones_green": len(green),
        "milestones_total": len(values),
        "critical_red": critical_red,
        "dynamic": True,
    }
