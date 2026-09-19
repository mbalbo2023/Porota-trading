"""RC6 compact validation dashboard: code status is not evidence status."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

import bg_paper_dashboard as bg
import ez_iol_shadow_validation_view_rc6 as iol_shadow_view
import rc6_validation_projection as projection
import rc6_validation_operational_daily as operational_daily
import rc6_action4_audit as action4_audit
from cg_paper_workspace import artifact_root, database_path
from em_validation_campaign_rc6 import MILESTONES

_installed = False

_CLAIMS = {
    "VERIFIED_CURRENT": ("VERIFICADO ACTUAL", "green"),
    "VERIFIED_HISTORICAL": ("VERIFICADO HISTÓRICO", "yellow"),
    "STALE": ("EVIDENCIA VENCIDA", "yellow"),
    "PENDING": ("PENDIENTE", "gray"),
    "BLOCKED_BY_POLICY": ("BLOQUEADO POR POLÍTICA", "gray"),
}


def _state(value):
    return bg._status({"GREEN": "VERDE", "YELLOW": "AMARILLO", "RED": "ROJO", "GRAY": "GRIS"}.get(str(value).upper(), "GRIS"))


def _claim(value):
    name, tone = _CLAIMS.get(str(value).upper(), ("SIN CLASIFICAR", "gray"))
    return f"<span class='paper-status {tone}'>{bg._e(name)}</span>"


def _milestone_card(milestone, current):
    current = dict(current or {})
    pct = current.get("compliance_pct")
    implementation = {"IMPLEMENTED": "IMPLEMENTADO", "IN_PROGRESS": "EN PROGRESO", "NOT_IMPLEMENTED": "NO IMPLEMENTADO", "UNKNOWN": "IMPLEMENTACIÓN SIN AUDITAR"}.get(str(current.get("implementation_status")).upper(), "IMPLEMENTACIÓN SIN AUDITAR")
    work_status = {"COMPLETED": "CUMPLIDO", "IN_PROGRESS": "EN PROGRESO", "PENDING": "PENDIENTE", "BLOCKED": "BLOQUEADO", "UNKNOWN": "SIN CLASIFICAR"}.get(str(current.get("work_status")).upper(), "SIN CLASIFICAR")
    evidence_status = {"CURRENT": "ACTUAL", "HISTORICAL": "HISTÓRICA", "STALE": "VENCIDA", "PENDING": "PENDIENTE", "UNAVAILABLE": "NO DISPONIBLE", "POLICY_BLOCKED": "BLOQUEADA POR POLÍTICA", "UNKNOWN": "SIN CLASIFICAR"}.get(str(current.get("evidence_status")).upper(), "SIN CLASIFICAR")
    criteria = "".join(f"<li>{bg._e(item)}</li>" for item in milestone.exit_criteria)
    return (
        "<details class='paper-trade'><summary>"
        f"{bg._e(milestone.code)} · {bg._e(milestone.name)} · {_claim(current.get('claim_status'))} · {_state(current.get('state'))} · {'s/d' if pct is None else f'{int(pct)}%'}"
        "</summary><div class='trade-body'>"
        f"<p><b>Estado de implementación:</b> {bg._e(implementation)} · <b>Trabajo:</b> {bg._e(work_status)}</p>"
        f"<p><b>Estado de evidencia:</b> {_claim(current.get('claim_status'))} · {bg._e(evidence_status)}</p>"
        f"<p><b>Objetivo:</b> {bg._e(current.get('objective') or milestone.goal)}</p>"
        f"<p><b>Evidencia esperada:</b> {bg._e(current.get('expected_evidence') or ', '.join(milestone.exit_criteria))}</p>"
        f"<p><b>Evidencia observada:</b> {bg._e(current.get('observed_evidence') or 'Sin proyección disponible todavía.')}</p>"
        f"<p><b>Brecha:</b> {bg._e(current.get('deviation') or 'Sin dato')}</p>"
        f"<p><b>Blocker:</b> {bg._e(current.get('blocker') or 'Sin dato')}</p>"
        f"<p><b>Próxima acción:</b> {bg._e(current.get('next_action') or 'Esperar evaluación del worker')}</p>"
        f"<p><b>Responsable:</b> {bg._e(current.get('owner') or 'POROTA')} · <b>Referencia:</b> {bg._e(current.get('evidence_ref') or 'pendiente')}</p>"
        f"<p class='paper-muted'>Evidencia: {bg._e(current.get('evidence_at') or current.get('evaluated_at') or 'pendiente')} · Evaluado: {bg._e(current.get('evaluated_at') or 'pendiente')}</p>"
        "<h3>Criterios de salida</h3><ul>" + criteria + "</ul></div></details>"
    )



def _campaign_daily_history(records, limit_days=10):
    """Campaign evidence remains separate from operational activity."""
    if not records:
        return ("<p class='paper-muted'>El ledger de campaña RC6 todavía no tiene observaciones; "
                "esto no significa que no haya habido actividad PAPER.</p>")
    return f"<p class='paper-muted'>Registros de campaña visibles: {min(len(records), limit_days)}.</p>"


def _operational_daily_section(limit_days=10):
    """Bounded, read-only activity evidence; it never promotes M0–M11."""
    try:
        evidence = operational_daily.collect(limit_days=limit_days)
        days = evidence.get("days") if isinstance(evidence.get("days"), list) else []
    except Exception:
        return ("<section class='paper-card'><h2>Actividad PAPER por jornada</h2>"
                "<p class='paper-warning'>Evidencia operacional no disponible. No se inventan jornadas.</p></section>")
    if not days:
        return ("<section class='paper-card'><h2>Actividad PAPER por jornada</h2>"
                "<p class='paper-muted'>No hay jornadas PAPER verificables en esta lectura. No modifica ni promociona M0–M11.</p></section>")
    body = []
    for row in days[:max(1, min(int(limit_days), 30))]:
        if not isinstance(row, dict):
            continue
        events = ", ".join(f"{key}={value}" for key, value in sorted((row.get("event_types") or {}).items()))
        body.append("<tr>"
                    f"<td>{bg._e(row.get('date_ar') or '—')}</td>"
                    f"<td>{bg._e(row.get('decisions', 0))}</td>"
                    f"<td>{bg._e(row.get('fills', 0))}</td>"
                    f"<td>{bg._e(row.get('opened', 0))}/{bg._e(row.get('closed', 0))}</td>"
                    f"<td>{bg._e(row.get('realized_net_pnl') or '0')}</td>"
                    f"<td>{bg._e(events or '—')}</td></tr>")
    return ("<section class='paper-card'><h2>Actividad PAPER por jornada</h2>"
            "<p class='paper-muted'>Evidencia read-only: describe la rueda. No modifica ni promociona M0–M11.</p>"
            "<div class='paper-table-wrap'><table><thead><tr><th>Fecha AR</th><th>Decisiones</th><th>Fills</th>"
            "<th>Abiertas/Cerradas</th><th>PnL realizado</th><th>Eventos</th></tr></thead><tbody>"
            + ("".join(body) or "<tr><td colspan='6'>Sin filas verificables.</td></tr>")
            + "</tbody></table></div></section>")


def _action4_section():
    """Render the published Action 4 artifact only; HTTP never runs the audit."""
    try:
        target = Path(artifact_root(database_path())).resolve() / "reports" / action4_audit.FILENAME
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return ("<section class='paper-card'><h2>Action 4 — auditoría diaria de la rueda</h2>"
                "<p class='paper-warning'>Aún no hay una auditoría Action 4 publicada. Se mostrará al finalizar la próxima corrida diaria; no se completan datos con supuestos.</p></section>")
    report = payload.get("dashboard_daily_report") if isinstance(payload.get("dashboard_daily_report"), dict) else {}
    separation = payload.get("separation") if isinstance(payload.get("separation"), dict) else {}
    safety = payload.get("safety") if isinstance(payload.get("safety"), dict) else {}
    lessons = report.get("lessons") if isinstance(report.get("lessons"), list) else []
    cards = (
        bg._card("Período", report.get("periodo") or "—", "Auditoría diaria publicada", "green")
        + bg._card("Cierres simulados", separation.get("executed_closed", 0), "Sin órdenes reales", "green")
        + bg._card("Aperturas simuladas", separation.get("opened_simulated", 0), "Sólo PAPER", "green")
        + bg._card("Seguridad", "READ_ONLY · 0 reales" if safety.get("real_orders_sent") == 0 else "EVIDENCIA INCOMPLETA", "No consulta broker ni ejecuta órdenes", "green" if safety.get("real_orders_sent") == 0 else "yellow")
    )
    lesson_html = "".join(f"<li>{bg._e(item)}</li>" for item in lessons[:10]) or "<li>Sin lecciones publicadas.</li>"
    return ("<section class='paper-card'><h2>Action 4 — auditoría diaria de la rueda</h2>"
            "<p class='paper-muted'>Resumen publicado por el scheduler; lectura exclusivamente informativa.</p>"
            f"<div class='paper-grid'>{cards}</div><h3>Lecciones y hallazgos</h3><ul>{lesson_html}</ul></section>")

def page():
    snapshot = projection.read()
    if snapshot is None:
        rows, summary = {}, {"milestones_green": 0, "milestones_total": len(MILESTONES), "critical_red": []}
        source = "<div class='paper-warning'><b>Proyección pendiente.</b> Aún no hay evidencia publicada.</div>"
    else:
        rows, summary = snapshot.get("milestones") or {}, snapshot.get("summary") or {}
        source = ("<div class='paper-notice'><b>Proyección dinámica:</b> "
                  f"actualizada {bg._e(snapshot.get('generated_at') or 'sin fecha')} · ledger {bg._e(snapshot.get('ledger_status') or 'UNKNOWN')}. "
                  "La evidencia histórica nunca se cuenta como confirmación actual.</div>")
    total, green, critical = int(summary.get("milestones_total") or len(MILESTONES)), int(summary.get("milestones_green") or 0), summary.get("critical_red") or []
    cards = "".join((
        bg._card("Verificados actuales", f"{green}/{total}", "Sólo pulsos live vigentes; no incluye histórico", "green" if green else "gray"),
        bg._card("Camino crítico RED", len(critical), ", ".join(critical) or "Sin fallas críticas proyectadas", "red" if critical else "green"),
        bg._card("Dinero real", "BLOCKED", "Política permanente RC6; no es un avance pendiente", "green"),
        bg._card("Lectura", "COMPACTA", "No consulta operaciones, historial ni ledger en HTTP", "green"),
    ))
    roadmap = "".join(_milestone_card(m, rows.get(m.code)) for m in MILESTONES)
    body = ("<h1>Validación — Camino a Producción</h1>" + source +
            "<div class='paper-warning'><b>Regla de verdad:</b> implementación, evidencia histórica y verificación actual son capas distintas. Ninguna habilita dinero real.</div>" +
            f"<div class='paper-grid'>{cards}</div>" + _action4_section() + _operational_daily_section() +
            iol_shadow_view.render() + "<section class='paper-card'><h2>Objetivo, evidencia y brecha por hito</h2>" + roadmap + "</section>")
    return bg._document("Validación — Camino a Producción", body, refresh=60)


def install(app, check_auth):
    global _installed
    if _installed: return
    _installed = True
    @app.middleware("http")
    async def rc6_validation_project_view(request, call_next):
        if request.url.path != "/validacion" or request.method != "GET": return await call_next(request)
        try:
            bg._authorize(check_auth, request, request.query_params.get("token", ""), request.headers.get("authorization"))
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
        return HTMLResponse(page())
