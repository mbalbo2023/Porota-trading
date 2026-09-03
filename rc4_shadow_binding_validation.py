"""RC4 — governance model for SHADOW -> BINDING evidence.

This module is pure. It does not change policy modes, access a broker, write a
DB, or generate lessons. It only evaluates evidence supplied by runtime or
post-close jobs.

Two evidence grades are intentionally separate:

* PAPER_BINDING_EVIDENCE: validates model consistency, instrumentation,
  simulated execution fidelity and counterfactual policy behaviour.
* REAL_BINDING_EVIDENCE: requires externally observed/real broker facts (for
  example fees actually billed). Those facts are NOT OBSERVABLE in
  PRODUCTION_PAPER and are never fabricated.

Passing this module yields ELIGIBLE_FOR_BINDING_DECISION, never BINDING.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable


DEFAULT_REFERENCE_WHEELS = 20
DEFAULT_REFERENCE_OBSERVATIONS = 20


@dataclass(frozen=True)
class CriterionResult:
    key: str
    label: str
    state: str
    observed: str
    target: str
    remaining: str
    evidence_available: bool

    @property
    def passed(self) -> bool:
        return self.state == "PASS"


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
    paper_evidence_state: str
    real_evidence_state: str
    criteria: tuple[CriterionResult, ...]
    milestones: tuple[MilestoneResult, ...]
    criteria_passed: int
    criteria_total: int
    observations: int
    observations_remaining: int
    wheels_elapsed: int | None
    wheels_remaining: int | None
    counterfactual_reject_pct: Decimal | None
    counterfactual_assessment: str
    window_invalidated: bool
    invalidation_reason: str | None


def _D(value) -> Decimal | None:
    if value in (None, ""):
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


def _bool(value) -> bool:
    return value is True


def _criterion(key, label, *, passed=False, observed="SIN_DATO", target="",
               remaining="", evidence_available=False,
               not_applicable=False) -> CriterionResult:
    state = "NOT_APPLICABLE" if not_applicable else (
        "PASS" if evidence_available and passed else
        "PENDING" if evidence_available else "WAITING_EVIDENCE")
    return CriterionResult(
        key=key, label=label, state=state, observed=str(observed),
        target=str(target), remaining=str(remaining),
        evidence_available=bool(evidence_available),
    )


def frozen_configuration_changed(expected_fingerprint: str | None,
                                 current_fingerprint: str | None) -> bool:
    return bool(expected_fingerprint and current_fingerprint and
                str(expected_fingerprint) != str(current_fingerprint))


def counterfactual_assessment(value) -> tuple[Decimal | None, str]:
    """Diagnostic only; rejection percentage is never a tuning target itself."""
    pct = _D(value)
    if pct is None:
        return None, "PENDIENTE_DE_MEDICION"
    if pct < 0 or pct > 100:
        return pct, "INVALIDO"
    if pct == 0:
        return pct, "NO_FILTRO_EN_LA_MUESTRA_REVISAR_EVIDENCIA_Y_UMBRAL"
    if pct == 100:
        return pct, "BLOQUEARIA_TODO_NO_ACTIVAR_SIN_INVESTIGAR"
    return pct, "DIAGNOSTICO_REQUIERE_ANALISIS_DE_CALIDAD_NO_OPTIMIZAR_POR_PORCENTAJE"


def _milestone_state(criteria: Iterable[CriterionResult]) -> str:
    items=tuple(criteria)
    applicable=[c for c in items if c.state != "NOT_APPLICABLE"]
    if not applicable:
        return "NOT_APPLICABLE"
    if all(c.passed for c in applicable):
        return "PASS"
    if any(c.evidence_available for c in applicable):
        return "IN_PROGRESS"
    return "WAITING_EVIDENCE"


def evaluate(metrics: dict, *, lessons: dict | None = None,
             expected_fingerprint: str | None = None,
             current_fingerprint: str | None = None) -> ValidationResult:
    """Evaluate PAPER governance facts without inventing real-broker evidence."""
    metrics=dict(metrics or {})
    lessons=dict(lessons or {})
    mode=str(metrics.get("economic_gate_mode") or "UNKNOWN").upper()

    ref_wheels=max(1,_I(metrics.get("reference_wheels") or DEFAULT_REFERENCE_WHEELS))
    ref_obs=max(1,_I(metrics.get("reference_observations") or DEFAULT_REFERENCE_OBSERVATIONS))
    wheels=(None if metrics.get("wheels_elapsed") is None else _I(metrics.get("wheels_elapsed")))
    observations=_I(metrics.get("paper_economic_evaluations"))

    tariff_present=_bool(metrics.get("official_tariff_snapshot_available"))
    tariff_fresh=_bool(metrics.get("official_tariff_snapshot_fresh"))
    tariff_versioned=_bool(metrics.get("official_tariff_versioned"))
    fee_tests=_bool(metrics.get("fee_model_invariants_verified"))
    economics_persisted=_bool(metrics.get("economic_inputs_persisted"))
    cf_persisted=_bool(metrics.get("economic_counterfactual_persisted"))
    execution_report=_bool(metrics.get("execution_fidelity_report_available"))
    exit_causes=_bool(metrics.get("exit_cause_distribution_available"))
    slippage_measured=_bool(metrics.get("slippage_gap_measured"))
    depth_measured=_bool(metrics.get("book_exceedance_measured"))

    c_tariff=_criterion(
        "official_tariff", "Tarifario oficial versionado y fresco",
        passed=tariff_present and tariff_fresh and tariff_versioned,
        observed=f"presente={tariff_present}; fresco={tariff_fresh}; versionado={tariff_versioned}",
        target="fuente oficial + hash/version + freshness valida",
        remaining="0" if tariff_present and tariff_fresh and tariff_versioned else "completar evidencia tarifaria",
        evidence_available=any(k in metrics for k in (
            "official_tariff_snapshot_available","official_tariff_snapshot_fresh","official_tariff_versioned")))
    c_fee=_criterion(
        "fee_model", "Invariantes del modelo de costos",
        passed=fee_tests,
        observed="VERIFICADAS" if fee_tests else "PENDIENTE",
        target="tests determinísticos verdes con unidades correctas",
        remaining="0" if fee_tests else "ejecutar/cerrar tests",
        evidence_available="fee_model_invariants_verified" in metrics)
    c_inputs=_criterion(
        "economic_inputs", "Inputs económicos persistidos por evaluación",
        passed=economics_persisted,
        observed="PERSISTIDOS" if economics_persisted else "PENDIENTE",
        target="costos+spread+slippage+reward/loss+breakeven neto trazables",
        remaining="0" if economics_persisted else "instrumentar ledger",
        evidence_available="economic_inputs_persisted" in metrics)
    c_cf=_criterion(
        "counterfactual", "Contrafáctico SHADOW del portón económico",
        passed=cf_persisted and observations >= ref_obs,
        observed=f"persistido={cf_persisted}; evaluaciones={observations}",
        target=f"persistido y >= {ref_obs} evaluaciones de referencia",
        remaining=max(0,ref_obs-observations) if cf_persisted else "instrumentar y acumular",
        evidence_available="economic_counterfactual_persisted" in metrics or observations>0)
    c_exec=_criterion(
        "execution_fidelity", "Fidelidad de ejecución PAPER medida",
        passed=execution_report,
        observed="DISPONIBLE" if execution_report else "PENDIENTE",
        target="reporte reproducible por operación/familia",
        remaining="0" if execution_report else "implementar/ejecutar replay de ejecución",
        evidence_available="execution_fidelity_report_available" in metrics)
    c_exit=_criterion(
        "exit_causes", "Distribución de causas de salida y PnL",
        passed=exit_causes,
        observed="MEDIDA" if exit_causes else "PENDIENTE",
        target="por causa, moneda y familia",
        remaining="0" if exit_causes else "instrumentar reporte",
        evidence_available="exit_cause_distribution_available" in metrics)
    c_slip=_criterion(
        "slippage_depth", "Slippage y profundidad cuantificados",
        passed=slippage_measured and depth_measured,
        observed=f"slippage={slippage_measured}; profundidad={depth_measured}",
        target="ambos medidos sobre la misma evidencia PAPER",
        remaining="0" if slippage_measured and depth_measured else "medir faltantes",
        evidence_available=("slippage_gap_measured" in metrics or "book_exceedance_measured" in metrics))
    c_window=_criterion(
        "valid_wheels", "Ruedas completas con instrumentación estable",
        passed=wheels is not None and wheels >= ref_wheels,
        observed=(wheels if wheels is not None else "SIN_DATO"),
        target=f">= {ref_wheels} ruedas de referencia",
        remaining=("MEDIR" if wheels is None else max(0,ref_wheels-wheels)),
        evidence_available=wheels is not None)

    criteria=(c_tariff,c_fee,c_inputs,c_cf,c_exec,c_exit,c_slip,c_window)

    real_source=_bool(metrics.get("real_broker_cost_source_available"))
    real_reconciled=_I(metrics.get("real_broker_cost_reconciled_operations"))
    runtime_mode=str(metrics.get("runtime_mode") or "PRODUCTION_PAPER").upper()
    if real_source and real_reconciled>0:
        real_state="REAL_EVIDENCE_AVAILABLE"
    elif runtime_mode=="PRODUCTION_PAPER":
        real_state="NOT_OBSERVABLE_IN_PAPER"
    else:
        real_state="WAITING_REAL_EVIDENCE"

    invalidated=frozen_configuration_changed(expected_fingerprint,current_fingerprint)
    passed=sum(c.passed for c in criteria)
    pct,cf_state=counterfactual_assessment(metrics.get("counterfactual_reject_pct"))
    paper_all=all(c.passed for c in criteria)

    if invalidated:
        state="WINDOW_INVALIDATED_RESTART_REQUIRED"
    elif mode != "SHADOW":
        state="SHADOW_REQUIRED_FOR_VALIDATION"
    elif paper_all:
        state="ELIGIBLE_FOR_BINDING_DECISION"
    else:
        state="COLLECTING_EVIDENCE"

    paper_state=("PAPER_BINDING_EVIDENCE_COMPLETE" if paper_all and not invalidated
                 else "PAPER_BINDING_EVIDENCE_INCOMPLETE")

    def milestone(key,label,items):
        lesson=lessons.get(key)
        if isinstance(lesson,dict):
            text=lesson.get("text"); source=lesson.get("source")
        else:
            text=lesson; source=None
        return MilestoneResult(key,label,_milestone_state(items),tuple(items),text,source)

    milestones=(
        milestone("H1","Instrumentación y economía modelada",(c_tariff,c_fee,c_inputs)),
        milestone("H2","Contrafáctico SHADOW y ventana estable",(c_cf,c_window)),
        milestone("H3","Fidelidad de ejecución PAPER",(c_exec,c_exit,c_slip)),
    )

    return ValidationResult(
        state=state, mode=mode, paper_evidence_state=paper_state,
        real_evidence_state=real_state, criteria=criteria, milestones=milestones,
        criteria_passed=passed, criteria_total=len(criteria), observations=observations,
        observations_remaining=max(0,ref_obs-observations),
        wheels_elapsed=wheels,
        wheels_remaining=(None if wheels is None else max(0,ref_wheels-wheels)),
        counterfactual_reject_pct=pct,
        counterfactual_assessment=cf_state,
        window_invalidated=invalidated,
        invalidation_reason=("FROZEN_CONFIGURATION_CHANGED" if invalidated else None),
    )
