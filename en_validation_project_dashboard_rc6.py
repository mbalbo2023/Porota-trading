"""RC6 compact validation dashboard.

The HTTP request reads a compact milestone projection only. A maintenance
worker evaluates the validation ledger and runtime evidence every ten minutes;
no raw operational or ledger history is loaded or rendered by this endpoint.
"""
from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

import bg_paper_dashboard as bg
import rc6_validation_projection as projection
from em_validation_campaign_rc6 import MILESTONES

_installed = False


def _state(value: str) -> str:
    names = {"GREEN": "VERDE", "YELLOW": "AMARILLO", "RED": "ROJO", "GRAY": "GRIS"}
    return bg._status(names.get(str(value).upper(), "GRIS"))


def _milestone_card(milestone, current: dict) -> str:
    state = str(current.get("state") or "GRAY")
    pct = current.get("compliance_pct")
    pct_text = "s/d" if pct is None else f"{int(pct)}%"
    criteria = "".join(f"<li>{bg._e(item)}</li>" for item in milestone.exit_criteria)
    return (
        "<details class='paper-trade'>"
        f"<summary>{bg._e(milestone.code)} · {bg._e(milestone.name)} · {_state(state)} · {pct_text}</summary>"
        "<div class='trade-body'>"
        f"<p><b>Objetivo:</b> {bg._e(current.get('objective') or milestone.goal)}</p>"
        f"<p><b>Evidencia esperada:</b> {bg._e(current.get('expected_evidence') or ', '.join(milestone.exit_criteria))}</p>"
        f"<p><b>Evidencia observada:</b> {bg._e(current.get('observed_evidence') or 'Sin proyección disponible todavía.')}</p>"
        f"<p><b>Desviación:</b> {bg._e(current.get('deviation') or 'Sin dato')}</p>"
        f"<p><b>Blocker:</b> {bg._e(current.get('blocker') or 'Sin dato')}</p>"
        f"<p><b>Próxima acción:</b> {bg._e(current.get('next_action') or 'Esperar evaluación del worker')}</p>"
        f"<p class='paper-muted'>Actualizado: {bg._e(current.get('evaluated_at') or 'pendiente')}</p>"
        "<h3>Criterios de salida</h3><ul>" + criteria + "</ul>"
        "</div></details>"
    )


def page() -> str:
    snapshot = projection.read()
    if snapshot is None:
        rows = {}
        summary = {"milestones_green": 0, "milestones_total": len(MILESTONES), "critical_red": []}
        source = (
            "<div class='paper-warning'><b>Proyección pendiente.</b> El worker de validación aún no publicó "
            "el primer estado. La pantalla sigue disponible y no carga historiales ni operaciones.</div>"
        )
    else:
        rows = snapshot.get("milestones") or {}
        summary = snapshot.get("summary") or {}
        ledger_status = snapshot.get("ledger_status") or "UNKNOWN"
        source = (
            "<div class='paper-notice'><b>Proyección dinámica:</b> "
            f"actualizada {bg._e(snapshot.get('generated_at') or 'sin fecha')} · ledger {bg._e(ledger_status)}. "
            "Esta pantalla lee sólo este resumen compacto; el worker analiza la evidencia fuera de la solicitud HTTP."
            "</div>"
        )

    total = int(summary.get("milestones_total") or len(MILESTONES))
    green = int(summary.get("milestones_green") or 0)
    critical_red = summary.get("critical_red") or []
    cards = "".join((
        bg._card("Hitos GREEN", f"{green}/{total}",
                 "Estado actual calculado en segundo plano; no autoriza dinero real",
                 "green" if green else "gray"),
        bg._card("Camino crítico RED", len(critical_red),
                 ", ".join(critical_red) or "Sin hitos críticos RED proyectados",
                 "red" if critical_red else "green"),
        bg._card("Real-money", "BLOCKED",
                 "M11 no se habilita por porcentaje, días verdes ni backtest", "green"),
        bg._card("Carga de la página", "LIVIANA",
                 "Sin consultas de historial, operaciones, fills ni ledger en la solicitud", "green"),
    ))
    roadmap = "".join(_milestone_card(m, dict(rows.get(m.code) or {})) for m in MILESTONES)
    body = (
        "<h1>Validación — Camino a Producción</h1>"
        + source
        + "<div class='paper-warning'><b>Este tablero gestiona evidencia; no habilita dinero real.</b> "
          "La evaluación es read-only y el motor permanece en PRODUCTION_PAPER / SIMULATED.</div>"
        + "<p class='paper-muted'>Los hitos M0–M11 se actualizan por worker cada 10 minutos. "
          "No se muestra ni se carga el historial de campañas, operaciones o registros de auditoría en esta pantalla.</p>"
        + f"<div class='paper-grid'>{cards}</div>"
        + "<section class='paper-card'><h2>Estado actual de los hitos M0–M11</h2>"
          "<p>Cada hito muestra su objetivo, evidencia actual, blocker y próximo paso.</p>"
          f"{roadmap}</section>"
    )
    return bg._document("Validación — Camino a Producción", body, refresh=60)


def install(app, check_auth):
    """Install the compact validation projection view."""
    global _installed
    if _installed:
        return
    _installed = True

    @app.middleware("http")
    async def rc6_validation_project_view(request, call_next):
        if request.url.path != "/validacion" or request.method != "GET":
            return await call_next(request)
        try:
            bg._authorize(check_auth, request, request.query_params.get("token", ""),
                          request.headers.get("authorization"))
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                headers=exc.headers)
        return HTMLResponse(page())
