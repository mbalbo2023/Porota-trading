"""RC4 — modelo puro de validación SHADOW -> BINDING.

Este módulo NO cambia flags, NO habilita portones, NO accede al broker y NO
escribe en runtime. Convierte evidencia persistida por otros componentes en un
estado auditable de avance de la ventana de validación del portón económico.

Principios:
- Q1 (costos) y Q2 (ejecución) son prerequisitos para darle autoridad BINDING
  al portón económico; Q3 (edge de la estrategia) es una validación distinta.
- cumplir métricas produce ELIGIBLE_FOR_BINDING_REVIEW, nunca BINDING.
- cualquier cambio en parámetros congelados invalida la ventana.
- una lección aprendida sólo se muestra si existe evidencia persistida; este
  módulo no inventa explicaciones ni usa IA.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable


PROPOSED_START = date(2026, 9, 7)
PROPOSED_END = date(2026, 10, 2)
EXPECTED_WHEELS = 20
TARGET_RECONCILED = 60

FROZEN_KEYS = (
    "PAPER_STOP_LOSS_PCT",
    "PAPER_TARGET_GAIN_PCT",
    "PAPER_MIN_NET_REWARD_RISK",
    "PAPER_RISK_PER_TRADE",
    "PAPER_FOCUS_SYMBOLS",
    "AU_FEE_SCHEDULE_SHA256",
)


@dataclass(frozen=True)
class CriterionResult:
    key: str
    label: str
    passed: bool
    observed: str
    target: str
    remaining: str
    evidence_available: bool = True


@dataclass(frozen=True)
class MilestoneResult:
    key: str
    label: str
    state: str
    criteria: tuple[CriterionResult, ...]
    lesson_learned: str | None = None
    lesson_source: str | None = None


@dataclass(frozen=True)
class ValidationResult:
    state: str
    mode: str
    criteria: tuple[CriterionResult, ...]
    milestones: tuple[MilestoneResult, ...]
    criteria_passed: int
    criteria_total: int
    reconciled: int
    reconciled_remaining: int
    wheels_elapsed: int | None
    wheels_remaining: int | None
    counterfactual_reject_pct: Decimal | None
    counterfactual_assessment: str
    window_invalidated: bool
    invalidation_reason: str | None


def _D(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _I(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _B(value) -> bool:
    return value is True


def _criterion(key, label, *, passed, observed, target, remaining,
               evidence_available=True) -> CriterionResult:
    return CriterionResult(
        key=key, label=label, passed=bool(passed), observed=str(observed),
        target=str(target), remaining=str(remaining),
        evidence_available=bool(evidence_available),
    )


def frozen_configuration_changed(expected_fingerprint: str | None,
                                 current_fingerprint: str | None) -> bool:
    """Fail closed sólo si existen ambos fingerprints y son distintos.

    La ausencia de fingerprint no se presenta como cambio demostrado: debe
    mostrarse como evidencia faltante en la capa de persistencia/dashboard.
    """
    if not expected_fingerprint or not current_fingerprint:
        return False
    return str(expected_fingerprint) != str(current_fingerprint)


def counterfactual_assessment(value) -> tuple[Decimal | None, str]:
    pct = _D(value)
    if pct is None:
        return None, "PENDIENTE_DE_MEDICION"
    if pct < 0 or pct > 100:
        return pct, "INVALIDO"
    if pct < Decimal("5"):
        return pct, "CASI_NO_FILTRA_REVISAR_UMBRAL"
    if pct > Decimal("90"):
        return pct, "CASI_BLOQUEA_TODO_NO_ACTIVAR"
    if Decimal("20") <= pct <= Decimal("70"):
        return pct, "DISCRIMINACION_UTIL_PARA_REVISION"
    return pct, "ZONA_INTERMEDIA_REQUIERE_JUSTIFICACION"


def _milestone_state(criteria: Iterable[CriterionResult]) -> str:
    items=tuple(criteria)
    if not items:
        return "NOT_STARTED"
    if all(item.passed for item in items):
        return "PASS"
    if any(item.evidence_available for item in items):
        return "IN_PROGRESS"
    return "WAITING_EVIDENCE"


def evaluate(metrics: dict, *, lessons: dict | None = None,
             expected_fingerprint: str | None = None,
             current_fingerprint: str | None = None) -> ValidationResult:
    """Evalúa la ventana sin mutar ninguna política.

    ``metrics`` debe contener hechos medidos/persistidos. Los faltantes no se
    infieren y por lo tanto quedan pendientes.
    """
    metrics = dict(metrics or {})
    lessons = dict(lessons or {})
    mode = str(metrics.get("economic_gate_mode") or "UNKNOWN").upper()

    closed = _I(metrics.get("closed_operations"))
    reconciled = _I(metrics.get("reconciled_operations"))
    coverage = _D(metrics.get("reconciliation_coverage_pct"))
    if coverage is None and closed > 0:
        coverage = (Decimal(reconciled) / Decimal(closed) * Decimal(100))

    median_gap = _D(metrics.get("median_gap_bp"))
    p90_gap = _D(metrics.get("p90_gap_bp"))
    winners = _I(metrics.get("reconciled_winners"))
    losers = _I(metrics.get("reconciled_losers"))
    families = _I(metrics.get("reconciled_families"))
    unexplained = _I(metrics.get("unexplained_divergences"))
    drift = _D(metrics.get("week3_week4_median_drift_bp"))
    new_categories = _I(metrics.get("new_divergence_categories"))

    c1 = _criterion(
        "reconciled_60", "Operaciones reconciliadas",
        passed=reconciled >= 60, observed=reconciled, target=">= 60",
        remaining=max(0, 60-reconciled), evidence_available=True)
    c2 = _criterion(
        "coverage_90", "Cobertura de reconciliación",
        passed=coverage is not None and coverage >= Decimal("90"),
        observed=(f"{coverage:.2f}%" if coverage is not None else "SIN_DATO"),
        target=">= 90%", remaining=(
            "0" if coverage is not None and coverage >= 90 else
            "MEDIR" if coverage is None else f"{Decimal('90')-coverage:.2f} pp"),
        evidence_available=coverage is not None)
    c3 = _criterion(
        "median_gap", "Brecha mediana modelo vs broker",
        passed=median_gap is not None and median_gap < Decimal("3"),
        observed=(f"{median_gap} pb" if median_gap is not None else "SIN_DATO"),
        target="< 3 pb", remaining="0" if median_gap is not None and median_gap < 3 else "REVISAR/MEDIR",
        evidence_available=median_gap is not None)
    c4 = _criterion(
        "p90_gap", "Brecha p90",
        passed=p90_gap is not None and p90_gap < Decimal("10"),
        observed=(f"{p90_gap} pb" if p90_gap is not None else "SIN_DATO"),
        target="< 10 pb", remaining="0" if p90_gap is not None and p90_gap < 10 else "REVISAR/MEDIR",
        evidence_available=p90_gap is not None)
    cases_ok = winners >= 1 and losers >= 1 and families >= 2
    c5 = _criterion(
        "case_coverage", "Cobertura de casos",
        passed=cases_ok,
        observed=f"ganadoras={winners}; perdedoras={losers}; familias={families}",
        target=">=1 ganadora + >=1 perdedora + >=2 familias",
        remaining=("0" if cases_ok else
                   f"ganadoras {max(0,1-winners)}, perdedoras {max(0,1-losers)}, familias {max(0,2-families)}"))
    c6 = _criterion(
        "unexplained_zero", "Divergencias sin explicar",
        passed=("unexplained_divergences" in metrics and unexplained == 0),
        observed=(unexplained if "unexplained_divergences" in metrics else "SIN_DATO"),
        target="0", remaining=("0" if "unexplained_divergences" in metrics and unexplained == 0 else "EXPLICAR/MEDIR"),
        evidence_available="unexplained_divergences" in metrics)
    c7 = _criterion(
        "median_drift", "Deriva de mediana semanas 3 a 4",
        passed=drift is not None and drift < Decimal("2"),
        observed=(f"{drift} pb" if drift is not None else "SIN_DATO"),
        target="< 2 pb", remaining="0" if drift is not None and drift < 2 else "ESPERAR H4/MEDIR",
        evidence_available=drift is not None)
    c8 = _criterion(
        "alert_e2e", "Alerta de divergencia punta a punta",
        passed=_B(metrics.get("divergence_alert_e2e_verified")),
        observed="VERIFICADA" if _B(metrics.get("divergence_alert_e2e_verified")) else "PENDIENTE",
        target="verificada", remaining="0" if _B(metrics.get("divergence_alert_e2e_verified")) else "1 prueba controlada",
        evidence_available="divergence_alert_e2e_verified" in metrics)
    final_criteria=(c1,c2,c3,c4,c5,c6,c7,c8)

    h1_extra=(
        _criterion("truth_source", "Fuente de verdad del broker",
                   passed=_B(metrics.get("broker_cost_truth_source_available")),
                   observed="DISPONIBLE" if _B(metrics.get("broker_cost_truth_source_available")) else "SIN_FUENTE",
                   target="disponible", remaining="0" if _B(metrics.get("broker_cost_truth_source_available")) else "definir/capturar fuente",
                   evidence_available="broker_cost_truth_source_available" in metrics),
        _criterion("reconciler_daily", "Reconciliador diario con evidencia",
                   passed=_B(metrics.get("daily_reconciler_evidence")),
                   observed="OK" if _B(metrics.get("daily_reconciler_evidence")) else "PENDIENTE",
                   target="registro diario", remaining="0" if _B(metrics.get("daily_reconciler_evidence")) else "instrumentar job",
                   evidence_available="daily_reconciler_evidence" in metrics),
        _criterion("shadow_mode", "Portón económico en SHADOW",
                   passed=mode == "SHADOW", observed=mode, target="SHADOW",
                   remaining="0" if mode == "SHADOW" else "cambio explícito requerido"),
        c2, c8,
    )
    h2=(
        _criterion("h2_reconciled_20", ">=20 operaciones con costo broker",
                   passed=reconciled >= 20, observed=reconciled, target=">=20",
                   remaining=max(0,20-reconciled)), c3,c4,c5,c6,
    )
    h3=(
        _criterion("execution_report", "Reporte de fidelidad de ejecución",
                   passed=_B(metrics.get("execution_fidelity_report_available")),
                   observed="DISPONIBLE" if _B(metrics.get("execution_fidelity_report_available")) else "PENDIENTE",
                   target="semanal", remaining="instrumentar/ejecutar" if not _B(metrics.get("execution_fidelity_report_available")) else "0",
                   evidence_available="execution_fidelity_report_available" in metrics),
        _criterion("exit_causes", "Distribución de causas de salida",
                   passed=_B(metrics.get("exit_cause_distribution_available")),
                   observed="MEDIDA" if _B(metrics.get("exit_cause_distribution_available")) else "PENDIENTE",
                   target="publicada", remaining="0" if _B(metrics.get("exit_cause_distribution_available")) else "medir"),
        _criterion("slippage_measured", "Slippage implícito vs modelado",
                   passed=_B(metrics.get("slippage_gap_measured")),
                   observed="MEDIDO" if _B(metrics.get("slippage_gap_measured")) else "PENDIENTE",
                   target="brecha cuantificada", remaining="0" if _B(metrics.get("slippage_gap_measured")) else "medir"),
        _criterion("depth_measured", "Fills que exceden profundidad",
                   passed=_B(metrics.get("book_exceedance_measured")),
                   observed="MEDIDO" if _B(metrics.get("book_exceedance_measured")) else "PENDIENTE",
                   target="porcentaje publicado", remaining="0" if _B(metrics.get("book_exceedance_measured")) else "medir"),
        _criterion("close_entry_median", "Mediana close_vs_entry",
                   passed=metrics.get("median_close_vs_entry_pct") is not None,
                   observed=(metrics.get("median_close_vs_entry_pct") if metrics.get("median_close_vs_entry_pct") is not None else "SIN_DATO"),
                   target="publicada", remaining="0" if metrics.get("median_close_vs_entry_pct") is not None else "medir",
                   evidence_available=metrics.get("median_close_vs_entry_pct") is not None),
    )
    h4=(c1,c7,
        _criterion("no_new_categories", "Sin categorías nuevas de divergencia",
                   passed=("new_divergence_categories" in metrics and new_categories == 0),
                   observed=(new_categories if "new_divergence_categories" in metrics else "SIN_DATO"),
                   target="0", remaining="0" if "new_divergence_categories" in metrics and new_categories == 0 else "ESPERAR H4/MEDIR",
                   evidence_available="new_divergence_categories" in metrics),
        _criterion("month_end", "Fin de mes sin divergencia inexplicada",
                   passed=_B(metrics.get("month_end_checked_clean")),
                   observed="OK" if _B(metrics.get("month_end_checked_clean")) else "PENDIENTE",
                   target="verificado", remaining="0" if _B(metrics.get("month_end_checked_clean")) else "cruzar/verificar fin de mes",
                   evidence_available="month_end_checked_clean" in metrics),
    )

    def milestone(key,label,criteria):
        lesson=lessons.get(key)
        if isinstance(lesson,dict):
            text=lesson.get("text"); source=lesson.get("source")
        else:
            text=lesson; source=None
        return MilestoneResult(key,label,_milestone_state(criteria),tuple(criteria),text,source)

    milestones=(
        milestone("H1","Semana 1 — Instrumentación",h1_extra),
        milestone("H2","Semana 2 — Fidelidad de costos",h2),
        milestone("H3","Semana 3 — Fidelidad de ejecución",h3),
        milestone("H4","Semana 4 — Consistencia y decisión",h4),
    )

    invalidated=frozen_configuration_changed(expected_fingerprint,current_fingerprint)
    passed=sum(item.passed for item in final_criteria)
    pct,cf_state=counterfactual_assessment(metrics.get("counterfactual_reject_pct"))

    if invalidated:
        state="WINDOW_INVALIDATED_RESTART_REQUIRED"
    elif mode != "SHADOW":
        state="SHADOW_REQUIRED_FOR_VALIDATION"
    elif all(item.passed for item in final_criteria) and all(m.state=="PASS" for m in milestones):
        state="ELIGIBLE_FOR_BINDING_REVIEW"
    else:
        state="VALIDATING"

    wheels_elapsed=(None if metrics.get("wheels_elapsed") is None else _I(metrics.get("wheels_elapsed")))
    wheels_remaining=(None if wheels_elapsed is None else max(0,EXPECTED_WHEELS-wheels_elapsed))
    return ValidationResult(
        state=state, mode=mode, criteria=final_criteria, milestones=milestones,
        criteria_passed=passed, criteria_total=len(final_criteria),
        reconciled=reconciled, reconciled_remaining=max(0,TARGET_RECONCILED-reconciled),
        wheels_elapsed=wheels_elapsed, wheels_remaining=wheels_remaining,
        counterfactual_reject_pct=pct, counterfactual_assessment=cf_state,
        window_invalidated=invalidated,
        invalidation_reason=("FROZEN_CONFIGURATION_FINGERPRINT_CHANGED" if invalidated else None),
    )


def assert_validation_invariants() -> None:
    """Pruebas mínimas ejecutables sin pytest."""
    base=evaluate({"economic_gate_mode":"SHADOW"})
    if base.state != "VALIDATING":
        raise AssertionError("una ventana vacía en SHADOW debe seguir validando")
    if base.criteria_passed:
        raise AssertionError("evidencia ausente no puede darse por aprobada")

    complete={
        "economic_gate_mode":"SHADOW", "closed_operations":65,
        "reconciled_operations":60, "reconciliation_coverage_pct":"95",
        "median_gap_bp":"1.2", "p90_gap_bp":"7.0", "reconciled_winners":2,
        "reconciled_losers":40, "reconciled_families":2,
        "unexplained_divergences":0, "week3_week4_median_drift_bp":"1.0",
        "new_divergence_categories":0, "divergence_alert_e2e_verified":True,
        "broker_cost_truth_source_available":True, "daily_reconciler_evidence":True,
        "execution_fidelity_report_available":True,
        "exit_cause_distribution_available":True, "slippage_gap_measured":True,
        "book_exceedance_measured":True, "median_close_vs_entry_pct":"0.4",
        "month_end_checked_clean":True, "counterfactual_reject_pct":"45",
    }
    done=evaluate(complete,expected_fingerprint="A",current_fingerprint="A")
    if done.state != "ELIGIBLE_FOR_BINDING_REVIEW":
        raise AssertionError(done)
    if evaluate(complete,expected_fingerprint="A",current_fingerprint="B").state != "WINDOW_INVALIDATED_RESTART_REQUIRED":
        raise AssertionError("un cambio de parámetros congelados debe invalidar la ventana")
    if evaluate(complete | {"economic_gate_mode":"BINDING"}).state != "SHADOW_REQUIRED_FOR_VALIDATION":
        raise AssertionError("la ventana no puede autovalidarse ya en BINDING")


if __name__ == "__main__":
    assert_validation_invariants()
    print("SHADOW_BINDING_VALIDATION_INVARIANTS=OK")
