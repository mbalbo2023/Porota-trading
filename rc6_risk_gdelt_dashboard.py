"""RC6 read-only Risk dashboard visibility for structured GDELT evidence.

This module never collects from the network.  It reads only the dedicated local
GDELT event-risk store through latest_status() and decorates the existing Risk
page.  The retired generic news feed remains intentionally OFF.
"""
from __future__ import annotations

import html
from urllib.parse import urlsplit

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


def _events_payload() -> tuple[list[dict], str]:
    try:
        return list(gdelt.latest_events(limit=100) or []), ""
    except Exception as exc:
        return [], f"{type(exc).__name__}:{exc}"


def _news_rows(events: list[dict]) -> str:
    if not events:
        return "<tr><td colspan='4'>Sin titulares guardados todavía.</td></tr>"
    rows = []
    for index, item in enumerate(events):
        url = str(item.get("provenance_url") or "")
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"https", "http"} or not parsed.hostname:
            link = _e(item.get("title") or "—")
        else:
            link = f"<a href='{_e(url)}' target='_blank' rel='noopener noreferrer'>{_e(item.get('title') or '—')}</a>"
        hidden = " class='gdelt-more-row' hidden style='display:none!important'" if index >= 10 else ""
        rows.append(
            f"<tr{hidden}><td>{_e(item.get('published_at'))}</td>"
            f"<td>{_e(item.get('event_type'))}</td><td data-wrap='true'>{link}</td>"
            f"<td>{_e(item.get('source_domain'))}</td></tr>"
        )
    return "".join(rows)


def render_section() -> str:
    status = _status_payload()
    events, events_error = _events_payload()
    state = str(status.get("state") or "NOT_RUN")
    state_label = {"NOT_RUN": "Sin corrida registrada", "STALE": "Evidencia vencida", "GREEN": "Con datos vigentes", "READ_ERROR": "Error de lectura", "AMARILLO_PARTIAL": "Parcial"}.get(state, state)
    good = state == "GREEN"
    partial = state in {"AMARILLO_PARTIAL", "NOT_RUN"}
    css = "s-verde" if good else ("s-amarillo" if partial else "s-rojo")
    errors = status.get("errors_json") or status.get("read_error") or "—"
    return f"""
    <section class='paper-card' id='rc6-gdelt-event-risk-status'>
      <h2>Event Risk estructurado — GDELT</h2>
      <p class='paper-muted'><b>Feed general de noticias: OFF intencional.</b> GDELT se usa únicamente como evidencia estructurada SHADOW/OBSERVE_ONLY; nunca habilita una orden. <b>Sin corrida registrada</b> indica que no hay run persistido y no confirma que el scheduler esté activo.</p>
      <div class='paper-grid'>
        <div class='paper-card'><h3>Estado</h3><span class='paper-status {css}'>{_e(state_label)}</span></div>
        <div class='paper-card'><h3>Autoridad</h3><b class='metric'>{_e(status.get('authority') or 'SHADOW_ONLY')}</b></div>
        <div class='paper-card'><h3>Eventos persistidos</h3><b class='metric'>{_e(status.get('events_total', 0))}</b></div>
        <div class='paper-card'><h3>Frescura</h3><b class='metric'>{_e(status.get('freshness'))}</b><div class='paper-muted'>{_e(status.get('freshness_seconds'))} s</div></div>
        <div class='paper-card'><h3>Último evento disponible</h3><b class='metric'>{_e(status.get('latest_event_available_at'))}</b></div>
      </div>
      <table class='paper-table classic-responsive-table'>
        <thead><tr><th>Run</th><th>Estado de corrida</th><th>Inicio</th><th>Fin</th><th>Tipos OK/solicitados</th><th>Fetched</th><th>Stored</th><th>Errores</th></tr></thead>
        <tbody><tr>
          <td>{_e(status.get('run_id'))}</td>
          <td>{_e(status.get('last_run_state', state))}</td>
          <td>{_e(status.get('started_at'))}</td>
          <td>{_e(status.get('finished_at'))}</td>
          <td>{_e(status.get('successful_event_types', 0))}/{_e(status.get('requested_event_types', 0))}</td>
          <td>{_e(status.get('fetched_events', 0))}</td>
          <td>{_e(status.get('stored_events', 0))}</td>
          <td data-wrap='true'>{_e(errors)}</td>
        </tr></tbody>
      </table>
      <h3>Noticias financieras y geopolíticas relevantes</h3>
      <p class='paper-muted'>Últimos {_e(len(events))} titulares guardados; límite de consulta de esta pantalla: 100. La ingesta está acotada en el job. SHADOW / OBSERVE_ONLY.</p>
      <table class='paper-table classic-responsive-table' id='rc6-gdelt-news-table'>
        <thead><tr><th>Publicado</th><th>Tipo</th><th>Titular</th><th>Fuente</th></tr></thead>
        <tbody>{_news_rows(events)}</tbody>
      </table>
      <p class='paper-muted' id='rc6-gdelt-news-read-error'>{_e(events_error)}</p>
      <button type='button' id='rc6-gdelt-show-more' aria-expanded='false' {'hidden' if len(events) <= 10 else ''}>Mostrar más</button>
      <script>
      (function(){{
        const table=document.getElementById('rc6-gdelt-news-table');
        const button=document.getElementById('rc6-gdelt-show-more');
        if(!table||!button)return;
        button.addEventListener('click',function(){{
          const hidden=Array.from(table.querySelectorAll('tr.gdelt-more-row[hidden]')).slice(0,10);
          hidden.forEach(row=>{{row.removeAttribute('hidden');row.style.removeProperty('display');}});
          button.setAttribute('aria-expanded','true');
          if(!table.querySelector('tr.gdelt-more-row[hidden]'))button.hidden=true;
        }});
      }})();
      </script>
      <p class='paper-muted'>Lectura local solamente. FRESH exige una corrida dentro del TTL configurado; STALE, NOT_RUN o READ_ERROR no se interpretan como datos válidos. Esta pantalla no dispara HTTP, scraping, broker ni BUY/SELL.</p>
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
