"""RC6 `/validacion` project-management view.

Read-only HTTP surface over the append-only validation campaign ledger plus a
separate read-only operational PAPER daily view.  Operational activity never
promotes milestones and never writes to the trading database.
"""
from __future__ import annotations

from collections import defaultdict

from fastapi.responses import HTMLResponse

import bg_paper_dashboard as bg
import ev_shadow_validation_view_rc6 as shadow_view
import rc6_validation_operational_daily as operational_daily
import rc6_validation_dynamic as dynamic_validation
from em_validation_campaign_rc6 import (
    MILESTONES,
    load_records,
    latest_by_milestone,
    project_summary,
)

_installed = False


def _state(value: str) -> str:
    mapping = {"GREEN": "VERDE", "YELLOW": "AMARILLO", "RED": "ROJO", "GRAY": "GRIS"}
    return bg._status(mapping.get(str(value).upper(), "GRIS"))


def _safe_records():
    try:
        return load_records(verify=True), None
    except Exception as exc:
        return [], type(exc).__name__


def _milestone_card(milestone, current):
    state = str(current.get("state") or "GRAY")
    pct = current.get("compliance_pct") if current else None
    exit_items = "".join(f"<li>{bg._e(item)}</li>" for item in milestone.exit_criteria)
    observed = bg._e(current.get("observed_evidence") or "Todavía no hay evidencia registrada para este hito.")
    expected = bg._e(current.get("expected_evidence") or milestone.goal)
    deviation = bg._e(current.get("deviation") or "Sin desviación registrada.")
    blocker = bg._e(current.get("blocker") or "Sin blocker registrado.")
    next_action = bg._e(current.get("next_action") or "Registrar nueva evidencia cuando corresponda.")
    evidence = bg._e(current.get("evidence_ref") or "Sin enlace/evidencia todavía.")
    release = bg._e(current.get("commit_release") or "Sin release/commit asociado todavía.")
    pct_text = "s/d" if pct is None else f"{int(pct)}%"
    return (
        "<details class='paper-trade'>"
        f"<summary>{bg._e(milestone.code)} · {bg._e(milestone.name)} · {_state(state)} · {pct_text}</summary>"
        "<div class='trade-body'>"
        f"<p><b>Objetivo:</b> {bg._e(current.get('objective') or milestone.goal)}</p>"
        f"<p><b>Evidencia esperada:</b> {expected}</p>"
        f"<p><b>Evidencia observada:</b> {observed}</p>"
        f"<p><b>Desviación:</b> {deviation}</p>"
        f"<p><b>Blocker:</b> {blocker}</p>"
        f"<p><b>Próxima acción:</b> {next_action}</p>"
        f"<p><b>Evidencia:</b> {evidence}</p>"
        f"<p><b>Commit/release:</b> {release}</p>"
        f"<p><b>Camino crítico:</b> {'SÍ' if milestone.critical else 'NO'}</p>"
        "<h3>Criterios de salida</h3><ul>" + exit_items + "</ul>"
        "</div></details>"
    )


def _campaign_daily_history(records, limit_days=10):
    grouped = defaultdict(list)
    for row in records:
        grouped[str(row.get("date_ar") or "SIN_FECHA")].append(row)
    days = sorted(grouped, reverse=True)
    if limit_days > 0:
        days = days[:limit_days]
    chunks = []
    for day in days:
        rows = sorted(grouped[day], key=lambda r: int(r.get("seq") or 0), reverse=True)
        entries = []
        for row in rows:
            entries.append(
                "<div class='paper-card'>"
                f"<h3>{bg._e(row.get('milestone'))} · {_state(row.get('state'))} · {bg._e(row.get('compliance_pct'))}%</h3>"
                f"<p><b>Objetivo:</b> {bg._e(row.get('objective'))}</p>"
                f"<p><b>Esperado:</b> {bg._e(row.get('expected'))}</p>"
                f"<p><b>Ocurrió:</b> {bg._e(row.get('observed'))}</p>"
                f"<p><b>Desviación:</b> {bg._e(row.get('deviation'))}</p>"
                f"<p><b>Causa raíz:</b> {bg._e(row.get('root_cause'))}</p>"
                f"<p><b>Lección:</b> {bg._e(row.get('lesson'))}</p>"
                f"<p><b>Decisión:</b> {bg._e(row.get('decision'))}</p>"
                f"<p><b>Blocker:</b> {bg._e(row.get('blocker'))}</p>"
                f"<p><b>Próxima acción:</b> {bg._e(row.get('next_action'))}</p>"
                f"<p class='paper-muted'>Owner {bg._e(row.get('owner'))} · evidencia {bg._e(row.get('evidence_ref'))} · "
                f"release {bg._e(row.get('commit_release'))} · registro #{bg._e(row.get('seq'))}</p>"
                "</div>"
            )
        chunks.append(
            f"<details class='paper-trade'><summary>{bg._e(day)} · {len(rows)} registro(s)</summary>"
            f"<div class='trade-body'>{''.join(entries)}</div></details>"
        )
    return "".join(chunks) or (
        "<div class='paper-notice'>El ledger de campaña RC6 todavía no tiene observaciones append-only. "
        "Esto no significa que no haya habido actividad PAPER: la actividad operacional se muestra por separado arriba.</div>"
    )


def _operational_daily_section(limit_days=10):
    try:
        data = operational_daily.collect(limit_days=limit_days)
    except Exception as exc:
        return (
            "<section class='paper-card'><h2>Actividad PAPER por jornada</h2>"
            "<div class='paper-warning'><b>Evidencia operacional no disponible.</b> "
            f"{bg._e(type(exc).__name__)}. No se inventan jornadas ni se modifica el ledger de campaña.</div></section>"
        )
    rows = []
    for row in data.get("days") or []:
        event_types = ", ".join(f"{k}:{v}" for k, v in (row.get("event_types") or {}).items()) or "—"
        rows.append(
            "<tr>"
            f"<td><b>{bg._e(row.get('date_ar'))}</b></td>"
            f"<td>{bg._e(row.get('event_count'))}</td>"
            f"<td>{bg._e(row.get('paper_positions'))}</td>"
            f"<td>{bg._e(row.get('opened'))}</td>"
            f"<td>{bg._e(row.get('closed'))}</td>"
            f"<td>{bg._e(row.get('decisions'))}</td>"
            f"<td>{bg._e(row.get('fills'))}</td>"
            f"<td>{bg._e(row.get('realized_net_pnl'))}</td>"
            f"<td data-wrap='true'>{bg._e(event_types)}</td>"
            "</tr>"
        )
    body = "".join(rows) or "<tr><td colspan='9'>Todavía no hay actividad PAPER fechable en las fuentes operacionales.</td></tr>"
    return (
        "<section class='paper-card'><h2>Actividad PAPER por jornada</h2>"
        "<p class='paper-muted'>Fuente read-only: observer_v17.db. Agrupación America/Argentina/Buenos_Aires. "
        "No modifica ni promociona M0–M11.</p>"
        "<table class='paper-table classic-responsive-table'><thead><tr>"
        "<th>Fecha AR</th><th>Eventos</th><th>Posiciones</th><th>Aperturas</th><th>Cierres</th>"
        "<th>Decisiones</th><th>Fills</th><th>P&amp;L realizado</th><th>Tipos de evento</th>"
        f"</tr></thead><tbody>{body}</tbody></table></section>"
    )


def page(limit_days=10):
    records, ledger_error = _safe_records()
    latest = dynamic_validation.evaluate(records)
    summary = dynamic_validation.summary(latest)
    ledger_summary = project_summary(records)
    cards = "".join((
        bg._card("Hitos GREEN", f"{summary['milestones_green']}/{summary['milestones_total']}",
                 "Conteo descriptivo; no autoriza real-money", "green" if summary['milestones_green'] else "gray"),
        bg._card("Avance dinámico", f"{summary['milestones_green']}/{summary['milestones_total']}",
                 "Objetivos evaluados con evidencia actual; no es readiness ponderado", "gray"),
        bg._card("Avance ponderado", "NO CALCULADO",
                 "Los pesos deben ser aprobados por el operador antes de usarse", "yellow"),
        bg._card("Camino crítico RED", len(summary['critical_red']),
                 ", ".join(summary['critical_red']) or "Sin hitos críticos RED registrados", "red" if summary['critical_red'] else "green"),
        bg._card("Real-money", "BLOCKED",
                 "M11 no se habilita por porcentaje, días verdes ni backtest", "green"),
        bg._card("Ledger de campaña", "OK" if ledger_error is None else "ERROR",
                 f"{ledger_summary['records']} registros · SHA-256 chain · evaluación dinámica activa" if ledger_error is None else f"{ledger_error}; revisar antes de confiar en historial",
                 "green" if ledger_error is None else "red"),
    ))
    roadmap = "".join(_milestone_card(m, latest.get(m.code, {})) for m in MILESTONES)
    dynamic_notice = "<div class='paper-notice'><b>Evaluación dinámica activa:</b> los estados se recalculan en cada carga usando evidencia read-only del runtime y del ledger. No se escriben registros ni se habilitan órdenes. La historia append-only se conserva por separado.</div>"
    campaign_history = _campaign_daily_history(records, limit_days=limit_days)
    campaign_controls = (
        "<section class='paper-card'><h2>Ledger de campaña / auditoría por día</h2>"
        "<p class='paper-muted'>Append-only de hitos M0–M11. No representa por sí solo la actividad diaria del motor PAPER.</p>"
        "<a class='paper-action' href='/validacion?days=10'>Últimos 10 días</a>"
        "<a class='paper-action' href='/validacion?days=30'>Últimos 30 días</a>"
        "<a class='paper-action' href='/validacion?days=0'>Todo</a>"
        f"{campaign_history}</section>"
    )
    body = (
        "<h1>Validación — Camino a Producción</h1>"
        + dynamic_notice
        "<div class='paper-warning'><b>Este tablero gestiona evidencia; no habilita dinero real.</b> "
        "El último hito M11 continúa BLOCKED hasta un proyecto futuro explícito de governance, permisos y canary.</div>"
        "<p class='paper-muted'>Horizonte: Infraestructura → Safety → Fuentes/Contratos → Históricos → Estabilidad PAPER → "
        "Realismo de ejecución → Evidencia estadística → Operabilidad/A11Y → Campaña sostenida → Auditoría/consenso → "
        "Governance futura → Real-money.</p>"
        f"<div class='paper-grid'>{cards}</div>"
        + _operational_daily_section(limit_days=limit_days)
        + shadow_view.render()
        + "<section class='paper-card'><h2>Hitos del camino crítico M0–M11</h2>"
        "<p>Cada hito muestra objetivo, evidencia esperada/observada, desviación, blocker y próximo paso.</p>"
        f"{roadmap}</section>"
        + campaign_controls
    )
    return bg._document("Validación — Camino a Producción", body, refresh=0)


def install(app, check_auth):
    """Install an outer middleware that owns only `/validacion`.

    The module is intended to be installed AFTER bg_paper_dashboard.install().
    """
    global _installed
    if _installed:
        return
    _installed = True

    @app.middleware("http")
    async def rc6_validation_project_view(request, call_next):
        if request.url.path != "/validacion" or request.method != "GET":
            return await call_next(request)
        token = request.query_params.get("token", "")
        authorization = request.headers.get("authorization")
        bg._authorize(check_auth, request, token, authorization)
        try:
            days = int(request.query_params.get("days", "10"))
        except ValueError:
            days = 10
        days = max(0, min(days, 365))
        return HTMLResponse(page(limit_days=days))
