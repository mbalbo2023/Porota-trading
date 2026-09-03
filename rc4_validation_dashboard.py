"""Read-only HTML fragment for RC4 validation governance.

No DB, no network, no configuration writes. The caller supplies an already
computed ValidationResult and optional policy-readiness facts.
"""
from __future__ import annotations

import html
from dataclasses import asdict

from rc4_shadow_binding_validation import ValidationResult


def _e(value) -> str:
    return html.escape(str(value if value is not None else "—"), quote=True)


def _state_class(value: str) -> str:
    state=str(value or "").upper()
    if state in {"PASS","ELIGIBLE_FOR_BINDING_REVIEW","READY","VERDE"}:
        return "card-green"
    if state in {"BLOCKED","FAIL","ROJO","WINDOW_INVALIDATED_RESTART_REQUIRED"}:
        return "card-red"
    if state in {"VALIDATING","IN_PROGRESS","WAITING_EVIDENCE","SHADOW_REQUIRED_FOR_VALIDATION","AMARILLO"}:
        return "card-yellow"
    return "card-gray"


def render_validation(result: ValidationResult, *, policy_readiness=None,
                      source_grade="SIN_CLASIFICAR",
                      actual_start=None, planned_end=None) -> str:
    policy_readiness=dict(policy_readiness or {})
    progress=(result.criteria_passed/result.criteria_total*100) if result.criteria_total else 0
    wheel_text=("SIN_DATO" if result.wheels_elapsed is None else
                f"{result.wheels_elapsed} transcurridas · {result.wheels_remaining} restantes de referencia")

    milestone_html=[]
    for item in result.milestones:
        criteria_passed=sum(c.passed for c in item.criteria)
        lesson=(
            f"<p><b>Lección aprendida:</b> {_e(item.lesson_learned)}"
            + (f" <span class='paper-muted'>({_e(item.lesson_source)})</span>" if item.lesson_source else "")
            + "</p>"
            if item.lesson_learned else
            "<p class='paper-muted'><b>Lección aprendida:</b> PENDIENTE — el hito aún no tiene una conclusión respaldada por evidencia.</p>"
        )
        milestone_html.append(
            f"<details class='paper-card {_state_class(item.state)}'>"
            f"<summary><b>{_e(item.label)}</b> · {_e(item.state)} · "
            f"{criteria_passed}/{len(item.criteria)} condiciones</summary>"
            f"<table class='paper-table'><tr><th>Condición</th><th>Observado</th><th>Objetivo</th><th>Falta</th><th>Estado</th></tr>"
            + "".join(
                f"<tr><td>{_e(c.label)}</td><td>{_e(c.observed)}</td><td>{_e(c.target)}</td>"
                f"<td>{_e(c.remaining)}</td><td>{'CUMPLIDO' if c.passed else 'PENDIENTE'}</td></tr>"
                for c in item.criteria
            ) + "</table>" + lesson + "</details>"
        )

    criteria_rows="".join(
        f"<tr class='{_state_class('PASS' if c.passed else 'IN_PROGRESS')}'>"
        f"<td>{idx}</td><td>{_e(c.label)}</td><td>{_e(c.observed)}</td>"
        f"<td>{_e(c.target)}</td><td>{_e(c.remaining)}</td>"
        f"<td>{'CUMPLIDO' if c.passed else 'PENDIENTE'}</td></tr>"
        for idx,c in enumerate(result.criteria,1)
    )

    other=[]
    for key,label in (
        ("expectancy","Expectancy"),
        ("market_regime","Régimen de mercado"),
        ("sector_concentration","Concentración sectorial"),
    ):
        item=dict(policy_readiness.get(key) or {})
        state=str(item.get("state") or "SIN_EVIDENCIA")
        other.append(
            f"<tr class='{_state_class(state)}'><td>{_e(label)}</td>"
            f"<td>{_e(item.get('mode','—'))}</td><td>{_e(state)}</td>"
            f"<td>{_e(item.get('evidence','—'))}</td><td>{_e(item.get('remaining','—'))}</td></tr>"
        )

    cf=("SIN_DATO" if result.counterfactual_reject_pct is None else
        f"{result.counterfactual_reject_pct}%")
    invalid=(
        "<div class='paper-warning'><b>VENTANA INVALIDADA.</b> Cambió la configuración congelada. "
        "No se siguen acumulando días/operaciones como si pertenecieran al mismo experimento.</div>"
        if result.window_invalidated else ""
    )

    return (
        "<h1>Validación — SHADOW → BINDING</h1>"
        "<div class='paper-notice'><b>Este panel gobierna evidencia, no cambia autoridad.</b> "
        "Cumplir todos los criterios sólo habilita una revisión humana; nunca cambia un flag a BINDING automáticamente.</div>"
        + invalid +
        "<div class='paper-grid'>"
        f"<div class='paper-card {_state_class(result.state)}'><h2>Decisión permitida hoy</h2><b>{_e(result.state)}</b><p>Modo económico observado: {_e(result.mode)}</p></div>"
        f"<div class='paper-card'><h2>Avance por evidencia</h2><b>{result.criteria_passed}/{result.criteria_total} · {progress:.1f}%</b><p>No equivale al tiempo transcurrido.</p></div>"
        f"<div class='paper-card'><h2>Ventana</h2><b>{_e(wheel_text)}</b><p>Inicio real: {_e(actual_start)} · fin planificado: {_e(planned_end)}</p></div>"
        f"<div class='paper-card'><h2>Muestra</h2><b>{result.reconciled} reconciliadas</b><p>Faltan {result.reconciled_remaining} para la referencia de 60.</p></div>"
        f"<div class='paper-card'><h2>Calidad de fuente de costos</h2><b>{_e(source_grade)}</b><p>Estimación oficial y costo efectivamente cobrado son evidencias distintas.</p></div>"
        f"<div class='paper-card'><h2>Contrafáctico BINDING</h2><b>{_e(cf)}</b><p>{_e(result.counterfactual_assessment)}</p></div>"
        "</div>"
        "<div class='paper-card'><h2>Los 8 criterios para revisión BINDING</h2>"
        "<table class='paper-table'><tr><th>#</th><th>Criterio</th><th>Observado</th><th>Objetivo</th><th>Falta</th><th>Estado</th></tr>"
        + criteria_rows + "</table></div>"
        "<h2>Hitos y lecciones</h2>" + "".join(milestone_html) +
        "<div class='paper-card'><h2>Otras políticas — no mezclar validaciones</h2>"
        "<p>La ventana económica no certifica expectancy, régimen ni concentración. Cada política muestra sus prerequisitos propios.</p>"
        "<table class='paper-table'><tr><th>Política</th><th>Modo</th><th>Estado</th><th>Evidencia</th><th>Falta</th></tr>"
        + "".join(other) + "</table></div>"
    )


def serializable(result: ValidationResult) -> dict:
    """Payload seguro para snapshot/tests; no contiene secretos."""
    return asdict(result)
