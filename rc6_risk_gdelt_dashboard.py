"""RC6 read-only Risk dashboard visibility for structured GDELT evidence.

This module never collects from the network.  It reads only the dedicated local
GDELT event-risk store through latest_status() and decorates the existing Risk
page.  The retired generic news feed remains intentionally OFF.
"""
from __future__ import annotations

import html

import rc6_gdelt_event_risk_job as gdelt
import zz_wave8_dashboard_live_rc6 as live

_installed = False
_original_risk_html = None


def _e(value):
    return html.escape(str(value if value not in (None, "") else "—"))


def _status_payload() -> dict:
    try:
        data = dict(gdelt.latest_status() or {})
        data.setdefault("state", "NOT_RUN")
        data["read_error"] = ""
        return data
    except Exception as exc:
        return {
            "state": "READ_ERROR",
            "authority": "SHADOW_ONLY",
            "read_error": f"{type(exc).__name__}:{exc}",
        }


def render_section() -> str:
    status = _status_payload()
    state = str(status.get("state") or "NOT_RUN")
    good = state == "GREEN"
    partial = state in {"AMARILLO_PARTIAL", "NOT_RUN"}
    css = "s-verde" if good else ("s-amarillo" if partial else "s-rojo")
    errors = status.get("errors_json") or status.get("read_error") or "—"
    return f"""
    <section class='paper-card' id='rc6-gdelt-event-risk-status'>
      <h2>Event Risk estructurado — GDELT</h2>
      <p class='paper-muted'><b>Feed general de noticias: OFF intencional.</b>
      GDELT se usa únicamente como evidencia estructurada SHADOW; nunca habilita una orden.</p>
      <div class='paper-grid'>
        <div class='paper-card'><h3>Estado</h3><span class='paper-status {css}'>{_e(state)}</span></div>
        <div class='paper-card'><h3>Autoridad</h3><b class='metric'>{_e(status.get('authority') or 'SHADOW_ONLY')}</b></div>
        <div class='paper-card'><h3>Eventos persistidos</h3><b class='metric'>{_e(status.get('events_total', 0))}</b></div>
        <div class='paper-card'><h3>Último evento disponible</h3><b class='metric'>{_e(status.get('latest_event_available_at'))}</b></div>
      </div>
      <table class='paper-table classic-responsive-table'>
        <thead><tr><th>Run</th><th>Inicio</th><th>Fin</th><th>Tipos OK/solicitados</th><th>Fetched</th><th>Stored</th><th>Errores</th></tr></thead>
        <tbody><tr>
          <td>{_e(status.get('run_id'))}</td>
          <td>{_e(status.get('started_at'))}</td>
          <td>{_e(status.get('finished_at'))}</td>
          <td>{_e(status.get('successful_event_types', 0))}/{_e(status.get('requested_event_types', 0))}</td>
          <td>{_e(status.get('fetched_events', 0))}</td>
          <td>{_e(status.get('stored_events', 0))}</td>
          <td data-wrap='true'>{_e(errors)}</td>
        </tr></tbody>
      </table>
      <p class='paper-muted'>Lectura local solamente. Esta pantalla no dispara HTTP, scraping, broker ni BUY/SELL.</p>
    </section>"""


def _decorate(page: str) -> str:
    section = render_section()
    marker = "</main>"
    return page.replace(marker, section + marker, 1) if marker in page else page + section


def install() -> None:
    global _installed, _original_risk_html
    if _installed:
        return
    _installed = True
    _original_risk_html = live._risk_html

    def risk_html_with_gdelt():
        return _decorate(_original_risk_html())

    live._risk_html = risk_html_with_gdelt
