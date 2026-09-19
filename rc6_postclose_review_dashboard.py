"""Read-only dashboard page for the evidence-based RC6 post-close review."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

import bg_paper_dashboard as bg

MAX_OPERATIONS = 10
_installed = False


def _path() -> Path:
    return Path(bg.DB_PATH).parent / "reports" / "postclose_review_latest.json"


def _read() -> dict:
    try:
        value = json.loads(_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _tone(status: str) -> str:
    return "green" if status == "VERIFIED" else "yellow"


def page() -> str:
    review = _read()
    status = str(review.get("status") or "INSUFFICIENT_EVIDENCE").upper()
    metrics = review.get("metrics") if isinstance(review.get("metrics"), dict) else {}
    operations = [row for row in (review.get("operations") or []) if isinstance(row, dict)]
    findings = review.get("findings") if isinstance(review.get("findings"), list) else []
    actions = review.get("next_actions") if isinstance(review.get("next_actions"), list) else []
    cards = "".join((
        bg._card("Estado de evidencia", status,
                 "Sólo se consideran snapshots post-cierre verificables.", _tone(status)),
        bg._card("Operaciones cerradas", metrics.get("closed_operations", "—"),
                 "Operaciones PAPER cerradas durante la rueda.", "green" if status == "VERIFIED" else "yellow"),
        bg._card("P&L neto", bg._money(metrics.get("net_pnl_ars")),
                 "Resultado PAPER; no representa dinero real.", "green" if (metrics.get("net_pnl_ars") or 0) >= 0 else "red"),
        bg._card("Win rate", "—" if metrics.get("win_rate_pct") is None else f"{metrics.get('win_rate_pct')}%",
                 "No se calcula si no hubo operaciones cerradas.", "green" if (metrics.get("win_rate_pct") or 0) >= 50 else "yellow"),
        bg._card("Decisiones observadas", metrics.get("decisions_observed", "—"),
                 "Cantidad disponible en el snapshot; no habilita cambios automáticos.", "green"),
    ))
    rows = "".join(
        "<tr>"
        f"<td><b>{bg._e(row.get('symbol'))}</b></td>"
        f"<td>{bg._e(row.get('opened_at'))}</td>"
        f"<td>{bg._e(row.get('closed_at'))}</td>"
        f"<td>{bg._money(row.get('net_pnl_ars'))}</td>"
        f"<td>{bg._e(row.get('decision_reason'))}</td>"
        "</tr>"
        for row in operations[:MAX_OPERATIONS]
    ) or "<tr><td colspan='5' class='paper-muted'>No hay operaciones cerradas verificables en este cierre.</td></tr>"
    findings_html = "".join(f"<li>{bg._e(item)}</li>" for item in findings) or "<li>Sin hallazgos publicados.</li>"
    actions_html = "".join(f"<li>{bg._e(item)}</li>" for item in actions) or "<li>Esperar evidencia post-cierre.</li>"
    body = (
        "<h1>Cierre de rueda — revisión PAPER</h1>"
        "<div class='paper-warning'><b>Revisión humana obligatoria.</b> Esta página describe evidencia y propone qué revisar; no cambia reglas, señales, tamaños ni órdenes.</div>"
        f"<p class='paper-muted'>Generado: {bg._e(review.get('generated_at') or 'pendiente')} · "
        f"snapshot fuente: {bg._e(review.get('source_snapshot_generated_at') or 'pendiente')}</p>"
        f"<div class='paper-grid'>{cards}</div>"
        "<section class='paper-card'><h2>Operaciones cerradas</h2><div class='paper-table-wrap'><table class='paper-table'><thead><tr>"
        "<th>Especie</th><th>Apertura</th><th>Cierre</th><th>P&L neto</th><th>Motivo</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table></div>"
        f"<p class='paper-muted'>Se muestran hasta {MAX_OPERATIONS} operaciones; el informe conserva el detalle estructurado completo.</p></section>"
        "<section class='paper-card'><h2>Hallazgos</h2><ul>" + findings_html + "</ul>"
        "<h2>Próximas acciones de revisión</h2><ul>" + actions_html + "</ul></section>"
    )
    return bg._document("Cierre de rueda — Porota RC6", body, refresh=60)


def install(app, check_auth):
    global _installed
    if _installed:
        return
    _installed = True

    @app.middleware("http")
    async def postclose_review_view(request, call_next):
        if request.url.path != "/postcierre" or request.method != "GET":
            return await call_next(request)
        try:
            bg._authorize(check_auth, request, request.query_params.get("token", ""), request.headers.get("authorization"))
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
        return HTMLResponse(page())
