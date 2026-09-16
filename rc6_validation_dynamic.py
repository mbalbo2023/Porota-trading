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


def _db_ok(*, deep: bool = False):
    path = Path(_db_path())
    if not path.exists():
        return False, "Base PAPER no encontrada"
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3) as c:
            if deep:
                result = c.execute("PRAGMA quick_check").fetchone()
                return bool(result and result[0] == "ok"), str(result[0] if result else "sin resultado")
            result = c.execute("SELECT 1 FROM observer_state WHERE id=1").fetchone()
        return bool(result and result[0] == 1), "OBSERVER_ROW_READ_ONLY"
    except Exception as exc:
        return False, type(exc).__name__


def _observer_evidence():
    """Read one bounded runtime row; never scan market/history ledgers."""
    path = Path(_db_path())
    if not path.exists():
        return {}
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3) as c:
            c.row_factory = sqlite3.Row
            row = c.execute(
                "SELECT mode,process_state,session_state,ppi_auth,heartbeat_at,"
                "last_market_data_at,real_orders_sent FROM observer_state WHERE id=1"
            ).fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}


def _bounded_data_evidence():
    """Return only aggregate freshness indicators for the daily projection."""
    path = Path(_db_path())
    if not path.exists():
        return {}
    queries = {
        "history_at": "SELECT MAX(downloaded_at) FROM production_history",
        "candle_at": "SELECT MAX(known_at) FROM candle_versions",
        "positions": "SELECT COUNT(*) FROM paper_positions",
        "gates": "SELECT COUNT(*) FROM trade_gate_evaluations",
    }
    result = {}
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3) as c:
            for key, query in queries.items():
                try:
                    result[key] = c.execute(query).fetchone()[0]
                except sqlite3.OperationalError:
                    result[key] = None
    except Exception:
        return {}
    return result


def _runtime_safety():
    mode = os.getenv("POROTA_MODE", os.getenv("DASHBOARD_OPERATION_MODE", "")).upper()
    execution = os.getenv("EXECUTION", "").upper()
    routes = os.getenv("REAL_ORDER_ROUTES", os.getenv("REAL_ORDER_CAPABILITY", "")).upper()
    safe = mode in {"PRODUCTION_PAPER", "PAPER"} and execution in {"SIMULATED", "PAPER"} and (
        not routes or routes in {"BLOCKED", "NOT_CALLED", "NONE", "DISABLED"}
    )
    return safe, f"mode={mode or 'UNKNOWN'} execution={execution or 'UNKNOWN'} routes={routes or 'UNSET'}"


def _dynamic_row(milestone, prior, *, deep_db_check: bool = False):
    prior = dict(prior or {})
    code = milestone.code
    state = "GRAY"
    observed = "Evaluación dinámica pendiente"
    deviation = "Sin evidencia suficiente"
    blocker = "Requiere evidencia verificable"
    next_action = "Ejecutar la validación correspondiente y publicar su evidencia"
    pct = 0
    ok_db, db_detail = _db_ok(deep=deep_db_check)
    safe, safety_detail = _runtime_safety()
    observer = _observer_evidence()
    data = _bounded_data_evidence()

    runtime_orders = observer.get("real_orders_sent")
    runtime_mode = str(observer.get("mode") or "")
    try:
        runtime_zero_orders = int(runtime_orders) == 0
    except (TypeError, ValueError):
        runtime_zero_orders = False
    runtime_paper = runtime_mode == "PRODUCTION_PAPER"

    if code == "M0" and ok_db and deep_db_check:
        state, pct = "GREEN", 100
        observed = f"Base accesible en modo read-only; quick_check={db_detail}"
        deviation = "Ninguna detectada por este evaluador"
        blocker = "Ninguno en la evidencia local"
        next_action = "Mantener backup, timers y restart behavior bajo observación"
    elif code == "M0" and ok_db:
        state, pct = "YELLOW", 60
        observed = "Base PAPER accesible por lectura acotada; el quick_check completo queda reservado al control profundo"
        deviation = "La proyección diaria no recorre toda la base"
        blocker = "Falta evidencia de control profundo para GREEN"
        next_action = "Conservar el último control profundo y ejecutar quick_check sólo en la auditoría programada"
    elif code == "M1" and safe and runtime_paper and runtime_zero_orders:
        state, pct = "GREEN", 100
        observed = (f"Invariantes PAPER observadas: {safety_detail}; "
                    f"runtime mode={runtime_mode}; real_orders_sent={runtime_orders}")
        deviation = "Ninguna detectada por este evaluador"
        blocker = "Ninguno en la evidencia local"
        next_action = "Revalidar en cada deploy y no promover a real-money"
    elif code == "M1":
        observed = (f"Invariantes incompletas: {safety_detail}; "
                    f"runtime mode={runtime_mode or 'UNKNOWN'}; "
                    f"real_orders_sent={runtime_orders if runtime_orders is not None else 'UNKNOWN'}")
        deviation = "No se puede demostrar simultáneamente PAPER, SIMULATED y cero órdenes"
        blocker = "Observer runtime o configuración no verificables"
        next_action = "Restablecer evidencia runtime antes de considerar el hito GREEN"
    elif code == "M2" and not prior:
        auth = str(observer.get("ppi_auth") or "UNKNOWN")
        scope = "ACCIONES/CEDEARS" if safe else "SIN_SCOPE_CONFIRMADO"
        if auth == "OK" and safe:
            state, pct = "YELLOW", 60
            observed = f"PPI read-only autenticado; alcance {scope}; contrato/settlement requieren evidencia cerrada"
            deviation = "Contract Evidence no se promueve automáticamente"
            blocker = "Falta evidencia contractual cerrada y versionada"
            next_action = "Publicar evidencia de contratos o mantener instrumentos fuera de alcance"
        else:
            observed = f"Auth observada={auth}; {safety_detail}"
            deviation = "No hay autenticación PPI verificable en el snapshot"
            blocker = "PPI/contratos sin evidencia suficiente"
            next_action = "Esperar pulso PPI válido y revisar contratos"
    elif code == "M3" and not prior:
        history_at, candle_at = data.get("history_at"), data.get("candle_at")
        if history_at and candle_at:
            state, pct = "YELLOW", 50
            observed = f"Históricos hasta {history_at}; velas versionadas hasta {candle_at}"
            deviation = "La frescura y cobertura no se consideran GREEN sin prueba por instrumento"
            blocker = "Falta cobertura/frescura por acciones y CEDEARs operables"
            next_action = "Ejecutar ingesta acotada y publicar reporte de cobertura"
        else:
            observed = "No se encontró evidencia compacta de históricos y velas versionadas"
            deviation = "Datos insuficientes"
            blocker = "Ingesta o versionado pendientes"
            next_action = "Verificar ingesta legacy/PPI y el worker de velas"
    elif code == "M5" and not prior:
        positions, gates = data.get("positions"), data.get("gates")
        if positions is not None or gates is not None:
            state, pct = "YELLOW", 40
            observed = f"Posiciones PAPER={positions if positions is not None else 'n/d'}; gates={gates if gates is not None else 'n/d'}"
            deviation = "Costos, settlement y Take Profit permanecen en diagnóstico SHADOW"
            blocker = "No hay campaña cerrada que pruebe realismo de ejecución"
            next_action = "Medir sesiones PAPER y conciliar salidas antes de promover evidencia"
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


def evaluate(records=(), *, deep_db_check: bool = False):
    prior = latest_by_milestone(records or [])
    return {milestone.code: _dynamic_row(milestone, prior.get(milestone.code), deep_db_check=deep_db_check)
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
