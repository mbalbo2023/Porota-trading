"""RC4 validation view-model for SHADOW -> BINDING governance."""
from datetime import datetime, timezone
from ck_policy_gate_hf6 import active_policies
from co_market_sessions_hf6 import session_for
from bq_exit_policy import PaperSessionPolicy



def max_hold_effectiveness(configured_minutes=360, policy=None):
    """Explain whether MAX_HOLD can fire before the conservative EOD exit."""
    policy = policy or PaperSessionPolicy()
    open_dt = datetime.combine(datetime.min.date(), policy.open_time)
    close_dt = datetime.combine(datetime.min.date(), policy.close_time)
    eod_exit_minutes = int((close_dt-open_dt).total_seconds()//60) - int(policy.exit_minutes)
    configured = int(configured_minutes)
    effective = min(configured, eod_exit_minutes) if policy.close_at_eod else configured
    return {
        "configured_minutes": configured,
        "earliest_open_to_forced_eod_minutes": eod_exit_minutes,
        "effective_ceiling_minutes": effective,
        "state": "DOMINATED_BY_EOD" if policy.close_at_eod and configured > eod_exit_minutes else "REACHABLE",
        "parameter_change_allowed": False,
        "reason": "Replay required before changing MAX_HOLD_MINUTES",
    }
MILESTONES=(
    ("INSTRUMENTATION","Evaluación y contrafáctico persistidos"),
    ("COST_MODEL","Modelo PAPER consistente; costo cobrado real = NOT_OBSERVABLE_IN_PAPER"),
    ("EXECUTION_MODEL","Book/spread/slippage PAPER observados"),
    ("CONSISTENCY_DECISION","Ventana estable; decisión manual requerida"),
)

def session_readiness():
    return {
        "BYMA": {
            "state":"UNVERIFIED_FAMILY_SESSION",
            "binding_eligible":False,
            "reason":"No se extrapola un horario global a todas las familias BYMA.",
        },
        "A3_FUTUROS_OPCIONES_MONEDAS": {
            "state":"VERIFIED_REFERENCE" if session_for("A3","MONEDAS") else "UNKNOWN",
            "binding_eligible":False,
            "reason":"Referencia de sesión A3; no cambia autoridad live PPI.",
        },
    }

def snapshot(rows=None):
    rows=list(rows or [])
    days=len({str(r.get("day")) for r in rows if r.get("valid")})
    return {
        "state":"COLLECTING_EVIDENCE" if days else "NOT_READY",
        "valid_sessions":days,
        "policies":active_policies(),
        "milestones":MILESTONES,
        "paper_billed_cost":"NOT_OBSERVABLE_IN_PAPER",
        "binding_change_allowed":False,
        "market_sessions":session_readiness(),
        "max_hold":max_hold_effectiveness(),
        "generated_at":datetime.now(timezone.utc).isoformat(),
    }
