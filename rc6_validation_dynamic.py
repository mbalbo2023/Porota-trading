"""Read-only RC6 evaluator with explicit evidence truth labels."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from em_validation_campaign_rc6 import MILESTONES, latest_by_milestone
from rc6_validation_truth import historical_claim, implementation_status, iso_now
from cg_paper_workspace import database_path


def _db_path():
    return os.getenv("POROTA_PAPER_DB") or str(database_path())


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


def _observer_evidence(db_path=None):
    path = Path(db_path or _db_path())
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


def _row(milestone, prior, *, db_probe, observer, runtime):
    code, prior = milestone.code, dict(prior or {})
    state, pct = "GRAY", 0
    claim = "PENDING"
    observed, deviation = "Sin evidencia verificable actual", "Sin evidencia suficiente"
    blocker, next_action = "Requiere evidencia verificable", "Publicar evidencia verificable"
    ok_db, db_detail = db_probe
    supplied = runtime.get(code)
    criteria = ({label: supplied.get(label, "NO_VERIFICADO") for label in milestone.exit_criteria}
                if supplied else {})
    impl = implementation_status(prior)
    # These capabilities exist in the RC6 deployment; the audit still separates
    # them from a current verification claim.
    if code in {"M0", "M1", "M2", "M3", "M7"} and impl == "UNKNOWN":
        impl = "IMPLEMENTED"

    runtime_zero = type(observer.get("real_orders_sent")) is int and observer["real_orders_sent"] == 0
    runtime_paper = str(observer.get("mode") or "") == "PRODUCTION_PAPER"
    if code == "M11":
        claim = "BLOCKED_BY_POLICY"
        observed = "Dinero real bloqueado permanentemente por diseño RC6"
        deviation, blocker, next_action = "No es un fallo", "Política de alcance", "Conservar bloqueado"
    elif code in {"M0", "M1"} and criteria:
        verified = sum(status == "VERIFICADO" for status in criteria.values())
        pct = round(100 * verified / len(criteria))
        observed = " · ".join(f"{label}: {status}" for label, status in criteria.items())
        if pct == 100:
            state, claim = "GREEN", "VERIFIED_CURRENT"
            deviation, blocker, next_action = "Ninguna detectada", "Ninguno", "Revalidar en la próxima rueda."
        else:
            state = "YELLOW" if verified else "GRAY"
            blocker = ", ".join(label for label, status in criteria.items() if status != "VERIFICADO")
            next_action = "Acreditar los criterios faltantes con evidencia de la versión en ejecución."
    elif code == "M0" and ok_db:
        state = "YELLOW"
        observed = f"Lectura SQLite: {db_detail}"
        blocker = "Pendiente acreditar disco, Docker, backups/restore, timers y reinicio."
        next_action = "Publicar el recibo físico de infraestructura."
    elif code == "M1" and runtime_paper and runtime_zero:
        state = "YELLOW"
        observed = "PAPER y contador local 0 informados por SQLite."
        blocker = "Pendiente acreditar modo, rutas y contador con el recibo del despliegue activo."
        next_action = "Validar los invariantes con el próximo recibo de runtime."
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
    evidence_status = {
        "VERIFIED_CURRENT": "CURRENT", "VERIFIED_HISTORICAL": "HISTORICAL",
        "STALE": "STALE", "BLOCKED_BY_POLICY": "POLICY_BLOCKED",
    }.get(claim, str(prior.get("evidence_status") or "PENDING").upper())
    work_status = str(prior.get("work_status") or ("BLOCKED" if claim == "BLOCKED_BY_POLICY" else "PENDING")).upper()
    if work_status not in {"COMPLETED", "IN_PROGRESS", "PENDING", "BLOCKED", "UNKNOWN"}:
        work_status = "UNKNOWN"
    return {
        "state": state, "compliance_pct": pct, "claim_status": claim,
        "implementation_status": impl, "work_status": work_status,
        "evidence_status": evidence_status, "owner": str(prior.get("owner") or "POROTA"),
        "evidence_at": str((runtime.get("observed_at") if criteria else None)
                           or prior.get("evidence_at") or prior.get("recorded_at") or ""),
        "objective": milestone.goal,
        "expected_evidence": ", ".join(milestone.exit_criteria),
        "observed_evidence": observed, "deviation": deviation, "blocker": blocker,
        "next_action": next_action,
        "evidence_ref": ("deploy/CURRENT_STATE_V2.json · recibo rootless de cierre" if criteria
                         else str(prior.get("evidence_ref") or "dynamic-read-only-evaluator")),
        "criteria_status": criteria,
        "evaluated_at": iso_now(), "dynamic": True,
    }


def evaluate(records=(), *, deep_db_check=False, runtime=None, observer=None):
    prior = latest_by_milestone(records or [])
    db_probe = _db_ok(deep=deep_db_check)
    observer = _observer_evidence() if observer is None else observer
    return {m.code: _row(m, prior.get(m.code), db_probe=db_probe, observer=observer,
                         runtime=runtime or {}) for m in MILESTONES}


def summary(rows):
    values = list(rows.values())
    green = [code for code, row in rows.items() if row.get("claim_status") == "VERIFIED_CURRENT" and row.get("state") == "GREEN"]
    critical_red = [code for code, row in rows.items() if row.get("state") == "RED" and row.get("claim_status") != "BLOCKED_BY_POLICY" and any(m.code == code and m.critical for m in MILESTONES)]
    return {"milestones_green": len(green), "milestones_total": len(values), "critical_red": critical_red, "dynamic": True}
