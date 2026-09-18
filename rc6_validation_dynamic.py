"""Read-only RC6 evaluator with explicit evidence truth labels."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from em_validation_campaign_rc6 import MILESTONES, latest_by_milestone
from rc6_validation_truth import historical_claim, implementation_status, iso_now


def _db_path():
    return os.getenv("POROTA_PAPER_DB", "/app/data/paper_v17/observer_v17.db")


def _db_ok(*, deep=False):
    path = Path(_db_path())
    if not path.exists():
        return False, "Base PAPER no encontrada"
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3) as conn:
            if deep:
                value = conn.execute("PRAGMA quick_check").fetchone()
                return bool(value and value[0] == "ok"), str(value[0] if value else "sin resultado")
            value = conn.execute("SELECT 1 FROM observer_state WHERE id=1").fetchone()
        return bool(value and value[0] == 1), "OBSERVER_ROW_READ_ONLY"
    except Exception as exc:
        return False, type(exc).__name__


def _observer_evidence():
    path = Path(_db_path())
    if not path.exists():
        return {}
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT mode,process_state,session_state,ppi_auth,heartbeat_at,"
                "last_market_data_at,real_orders_sent FROM observer_state WHERE id=1"
            ).fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}


def _iol_coverage():
    for candidate in (os.getenv("POROTA_IOL_SHADOW_CACHE_PATH", "").strip(),
                      "/app/data/market/iol_shadow_latest.json",
                      "/opt/porota-trading/data/market/iol_shadow_latest.json"):
        if not candidate:
            continue
        try:
            payload = json.loads(Path(candidate).read_text(encoding="utf-8"))
            progress = payload.get("progress") if isinstance(payload.get("progress"), dict) else {}
            rows = payload.get("symbols") if isinstance(payload.get("symbols"), list) else []
            target = int(progress.get("scheduled") or 0)
            return len(rows), target
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return 0, 0


def _runtime_safety():
    mode = os.getenv("POROTA_MODE", os.getenv("DASHBOARD_OPERATION_MODE", "")).upper()
    execution = os.getenv("EXECUTION", "").upper()
    routes = os.getenv("REAL_ORDER_ROUTES", os.getenv("REAL_ORDER_CAPABILITY", "")).upper()
    safe = mode in {"PRODUCTION_PAPER", "PAPER"} and execution in {"SIMULATED", "PAPER"} and (not routes or routes in {"BLOCKED", "NOT_CALLED", "NONE", "DISABLED"})
    return safe, f"mode={mode or 'UNKNOWN'} execution={execution or 'UNKNOWN'} routes={routes or 'UNSET'}"


def _row(milestone, prior, *, deep_db_check=False):
    code, prior = milestone.code, dict(prior or {})
    state, pct = "GRAY", 0
    claim = "PENDING"
    observed, deviation = "Sin evidencia verificable actual", "Sin evidencia suficiente"
    blocker, next_action = "Requiere evidencia verificable", "Publicar evidencia verificable"
    ok_db, db_detail = _db_ok(deep=deep_db_check)
    safe, safety_detail = _runtime_safety()
    observer = _observer_evidence()
    impl = implementation_status(prior)
    # These capabilities exist in the RC6 deployment; the audit still separates
    # them from a current verification claim.
    if code in {"M0", "M1", "M2", "M3", "M7"} and impl == "UNKNOWN":
        impl = "IMPLEMENTED"

    runtime_zero = str(observer.get("real_orders_sent", "")) == "0"
    runtime_paper = str(observer.get("mode") or "") == "PRODUCTION_PAPER"
    if code == "M11":
        claim = "BLOCKED_BY_POLICY"
        observed = "Dinero real bloqueado permanentemente por diseño RC6"
        deviation, blocker, next_action = "No es un fallo", "Política de alcance", "Conservar bloqueado"
    elif code == "M0" and ok_db and deep_db_check:
        state, pct, claim = "GREEN", 100, "VERIFIED_CURRENT"
        observed, deviation, blocker = f"quick_check={db_detail} por lectura", "Ninguna detectada", "Ninguno"
        next_action = "Mantener controles de infraestructura"
    elif code == "M0" and ok_db:
        state, pct = "YELLOW", 60
        observed, blocker = "Base accesible por lectura acotada", "Control profundo pendiente"
        next_action = "Esperar/ejecutar auditoría profunda programada"
    elif code == "M1" and safe and runtime_paper and runtime_zero:
        state, pct, claim = "GREEN", 100, "VERIFIED_CURRENT"
        observed, deviation, blocker = f"PAPER observado; {safety_detail}; real_orders_sent=0", "Ninguna detectada", "Ninguno"
        next_action = "Revalidar en cada deploy; no promover dinero real"
    elif code == "M1" and runtime_paper and runtime_zero:
        state, pct = "YELLOW", 80
        observed = "PAPER observado y real_orders_sent=0; la capacidad de rutas no se puede probar desde este proceso de lectura."
        deviation = "Pulso de enrutamiento pendiente de evidencia de deploy."
        blocker = "Sin acceso de auditoría a las variables de rutas."
        next_action = "Conservar BLOCKED y validar rutas en el próximo deploy."
    elif prior:
        # Historical records remain visible, but are never a current GREEN.
        claim = historical_claim(prior)
        pct = int(prior.get("compliance_pct") or 0)
        observed = prior.get("observed_evidence") or prior.get("observed") or observed
        deviation = prior.get("deviation") or "Evidencia histórica no equivale a estado actual"
        blocker = prior.get("blocker") or "Falta pulso actual verificable"
        next_action = prior.get("next_action") or "Publicar evidencia actual"
        if claim == "VERIFIED_HISTORICAL":
            state = "YELLOW"
        else:
            state = str(prior.get("state") or "GRAY")
    return {
        "state": state, "compliance_pct": pct, "claim_status": claim,
        "implementation_status": impl, "objective": milestone.goal,
        "expected_evidence": ", ".join(milestone.exit_criteria),
        "observed_evidence": observed, "deviation": deviation, "blocker": blocker,
        "next_action": next_action, "evidence_ref": "dynamic-read-only-evaluator",
        "evaluated_at": iso_now(), "dynamic": True,
    }


def evaluate(records=(), *, deep_db_check=False):
    prior = latest_by_milestone(records or [])
    return {m.code: _row(m, prior.get(m.code), deep_db_check=deep_db_check) for m in MILESTONES}


def summary(rows):
    values = list(rows.values())
    green = [code for code, row in rows.items() if row.get("claim_status") == "VERIFIED_CURRENT" and row.get("state") == "GREEN"]
    critical_red = [code for code, row in rows.items() if row.get("state") == "RED" and row.get("claim_status") != "BLOCKED_BY_POLICY" and any(m.code == code and m.critical for m in MILESTONES)]
    return {"milestones_green": len(green), "milestones_total": len(values), "critical_red": critical_red, "dynamic": True}
