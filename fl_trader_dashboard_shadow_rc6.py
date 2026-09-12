"""RC6 trader-facing dashboard overlay for scalping telemetry and shadow policies.

Read-only presentation only.  This module does not create orders, mutate the
SQLite database, call PPI, infer missing SWING/caucion evidence, or promote a
SHADOW policy.  It decorates the already-installed PAPER dashboard functions so
that the operator can see the state of new RC6 capabilities in their natural
locations.
"""
from __future__ import annotations

import html
import os
from contextlib import closing

import bg_paper_dashboard as bg
import fi_swing_paper_shadow_rc6 as swing_shadow
import fj_caucion_cash_sweep_shadow_rc6 as caucion_shadow
import fk_scalping_revision_metrics_rc6 as revision_metrics


_installed = False


def _esc(value):
    return html.escape(str(value if value not in (None, "") else "—"))


def _append_before_main_end(page, fragment):
    marker = "</main>"
    return page.replace(marker, fragment + marker, 1) if marker in page else page + fragment


def _badge(label, css="s-amarillo"):
    return f"<span class='paper-status {_esc(css)}'>{_esc(label)}</span>"


def _revision_snapshot():
    empty = {
        "state": "NO_EVIDENCE",
        "total_valid": 0,
        "invalid_events": 0,
        "refresh_mutable": 0,
        "reject_closed_revision": 0,
        "threshold_seconds": 120,
        "age_p50_seconds": None,
        "age_p95_seconds": None,
        "age_max_seconds": None,
        "latest_received_at": None,
        "by_symbol": {},
    }
    if not bg._table("paper_events"):
        return empty | {"state": "MISSING_PAPER_EVENTS"}
    try:
        with closing(bg._conn()) as connection:
            summary = revision_metrics.read_revision_summary(connection, threshold_seconds=120)
        return empty | summary
    except Exception:
        return empty | {"state": "UNAVAILABLE"}


def scalping_revision_section():
    data = _revision_snapshot()
    state = str(data.get("state") or "NO_EVIDENCE")
    badge_css = "s-verde" if state == "EVIDENCE_AVAILABLE" else "s-amarillo"
    symbols = list((data.get("by_symbol") or {}).items())[:5]
    symbol_rows = "".join(
        f"<tr><td><b>{_esc(symbol)}</b></td><td>{_esc(count)}</td></tr>"
        for symbol, count in symbols
    ) or "<tr><td colspan='2'>Sin revisiones persistidas todavía.</td></tr>"
    return f"""
    <section class='paper-card' id='rc6-scalping-revision-observability'>
      <h2>Calidad temporal de velas — telemetría RC6</h2>
      <p class='paper-muted'>Observabilidad únicamente. El umbral operativo sigue en 120 s y esta vista no cambia ninguna decisión de trading.</p>
      <div class='paper-grid'>
        <div class='paper-card'><h3>Estado</h3>{_badge(state,badge_css)}<div class='paper-muted'>Última evidencia: {_esc(bg._local_time(data.get('latest_received_at')))}</div></div>
        <div class='paper-card'><h3>Revisiones observadas</h3><b class='metric'>{_esc(data.get('total_valid'))}</b><div class='paper-muted'>Inválidas: {_esc(data.get('invalid_events'))}</div></div>
        <div class='paper-card'><h3>Dentro de ventana mutable</h3><b class='metric'>{_esc(data.get('refresh_mutable'))}</b><div class='paper-muted'>REFRESH_MUTABLE</div></div>
        <div class='paper-card'><h3>Revisión de punto cerrado</h3><b class='metric'>{_esc(data.get('reject_closed_revision'))}</b><div class='paper-muted'>REJECT_CLOSED_REVISION</div></div>
      </div>
      <table class='paper-table classic-responsive-table'><thead><tr><th>Indicador</th><th>Segundos</th></tr></thead><tbody>
        <tr><td>Umbral actual</td><td>{_esc(data.get('threshold_seconds'))}</td></tr>
        <tr><td>Edad p50</td><td>{_esc(data.get('age_p50_seconds'))}</td></tr>
        <tr><td>Edad p95</td><td>{_esc(data.get('age_p95_seconds'))}</td></tr>
        <tr><td>Edad máxima</td><td>{_esc(data.get('age_max_seconds'))}</td></tr>
      </tbody></table>
      <h3>Símbolos con más revisiones</h3>
      <table class='paper-table classic-responsive-table'><thead><tr><th>Símbolo</th><th>Eventos</th></tr></thead><tbody>{symbol_rows}</tbody></table>
    </section>"""


def _recent_positions():
    if not bg._table("paper_positions"):
        return []
    try:
        return bg._rows("SELECT * FROM paper_positions ORDER BY id DESC LIMIT 250")
    except Exception:
        return []


def _swing_snapshot():
    swing_shadow.assert_shadow_only()
    rows = _recent_positions()
    explicit_swing = 0
    intraday = 0
    eod_closed = 0
    for row in rows:
        style = swing_shadow.execution_style(row)
        if style == swing_shadow.SWING_STYLE:
            explicit_swing += 1
        if style in swing_shadow.INTRADAY_STYLES:
            intraday += 1
        if str(row.get("status") or "").upper() == "CLOSED" and str(row.get("exit_reason") or "").upper() == "EOD_PAPER":
            eod_closed += 1
    return {
        "mode": swing_shadow.SHADOW_MODE,
        "explicit_swing": explicit_swing,
        "intraday": intraday,
        "eod_closed": eod_closed,
        "runtime_state": "EVIDENCE_PENDING" if explicit_swing == 0 else "EXPLICIT_STYLE_PRESENT",
        "economics_model": "SWING_NON_INTRADAY",
        "real_execution_allowed": False,
    }


def swing_shadow_section():
    data = _swing_snapshot()
    return f"""
    <section class='paper-card' id='rc6-swing-paper-shadow'>
      <h2>SWING_PAPER — evaluación overnight</h2>
      <p>{_badge(data['mode'],'s-amarillo')} {_badge(data['runtime_state'],'s-amarillo')}</p>
      <p class='paper-muted'>La política está certificada pero no reemplaza el cierre EOD vigente. Sólo posiciones marcadas explícitamente SWING_PAPER pueden ser evaluadas; nunca se reclasifica una posición por inferencia.</p>
      <table class='paper-table classic-responsive-table'><thead><tr><th>Indicador</th><th>Estado</th></tr></thead><tbody>
        <tr><td>Posiciones recientes con estilo SWING_PAPER explícito</td><td>{_esc(data['explicit_swing'])}</td></tr>
        <tr><td>Posiciones recientes explícitamente intradía/scalping</td><td>{_esc(data['intraday'])}</td></tr>
        <tr><td>Cierres EOD_PAPER observados en muestra</td><td>{_esc(data['eod_closed'])}</td></tr>
        <tr><td>Economía overnight</td><td>{_esc(data['economics_model'])}</td></tr>
        <tr><td>Puede cambiar el exit supervisor</td><td><b>NO</b></td></tr>
        <tr><td>Ejecución real permitida</td><td><b>NO</b></td></tr>
      </tbody></table>
      <div class='paper-warning'>Hasta que existan calendario, mark fresco, tesis, horizonte y gap-risk persistidos para una posición SWING explícita, el dashboard no mostrará “mantener overnight” como decisión runtime.</div>
    </section>"""


def _caucion_snapshot():
    caucion_shadow.assert_shadow_only()
    rows = bg._rows("SELECT * FROM paper_cauciones ORDER BY opened_at DESC LIMIT 25") if bg._table("paper_cauciones") else []
    auto_raw = str(os.getenv("CAUCIONES_AUTO_PLACEMENT", "false")).strip().lower()
    auto_enabled = auto_raw in {"1", "true", "yes", "on"}
    latest = rows[0] if rows else {}
    return {
        "mode": caucion_shadow.SHADOW_MODE,
        "policy_lead_minutes": caucion_shadow.DEFAULT_POLICY_LEAD_MINUTES,
        "lead_min": caucion_shadow.MIN_POLICY_LEAD_MINUTES,
        "lead_max": caucion_shadow.MAX_POLICY_LEAD_MINUTES,
        "paper_positions": len(rows),
        "latest_opened_at": latest.get("opened_at"),
        "auto_enabled": auto_enabled,
        "runtime_state": "POLICY_ONLY_WAITING_VERIFIED_SCHEDULE_AND_QUOTE",
        "real_execution_allowed": False,
    }


def caucion_shadow_section():
    data = _caucion_snapshot()
    auto_label = "ON — REVISAR" if data["auto_enabled"] else "OFF"
    auto_css = "s-rojo" if data["auto_enabled"] else "s-verde"
    return f"""
    <section class='paper-card' id='rc6-caucion-cash-sweep-shadow'>
      <h2>Caución — cash sweep de fin de rueda</h2>
      <p>{_badge(data['mode'],'s-amarillo')} {_badge(data['runtime_state'],'s-amarillo')}</p>
      <p class='paper-muted'>Planificador de tesorería en sombra. Nunca vende posiciones para generar caja y no cursa una caución.</p>
      <table class='paper-table classic-responsive-table'><thead><tr><th>Indicador</th><th>Estado</th></tr></thead><tbody>
        <tr><td>CAUCIONES_AUTO_PLACEMENT</td><td>{_badge(auto_label,auto_css)}</td></tr>
        <tr><td>Lead policy por defecto</td><td>{_esc(data['policy_lead_minutes'])} min antes del cutoff verificado</td></tr>
        <tr><td>Rango admitido de lead policy</td><td>{_esc(data['lead_min'])}–{_esc(data['lead_max'])} min</td></tr>
        <tr><td>Cauciones PAPER visibles en muestra</td><td>{_esc(data['paper_positions'])}</td></tr>
        <tr><td>Última caución PAPER</td><td>{_esc(bg._local_time(data['latest_opened_at']))}</td></tr>
        <tr><td>Ejecución real permitida por esta capa</td><td><b>NO</b></td></tr>
      </tbody></table>
      <div class='paper-warning'>Una sugerencia de monto sólo debe aparecer cuando haya horario/cutoff con fuente verificable, calendario de liquidación válido, oferta fresca, fees exactos y caja libre luego de reservas/obligaciones.</div>
    </section>"""


def live_operator_section():
    revision = _revision_snapshot()
    swing = _swing_snapshot()
    caucion = _caucion_snapshot()
    revision_state = revision.get("state") or "NO_EVIDENCE"
    return f"""
    <section class='paper-card' id='rc6-trader-new-capabilities-live'>
      <h2>RC6 — observabilidad y políticas nuevas</h2>
      <p class='paper-muted'>Resumen para el operador. SHADOW_ONLY significa que la función observa/calcula pero no cambia decisiones ni ejecuta órdenes.</p>
      <table class='paper-table classic-responsive-table'><thead><tr><th>Capacidad</th><th>Modo</th><th>Evidencia / estado</th><th>Binding</th></tr></thead><tbody>
        <tr><td><b>Scalping — revisiones intradía</b></td><td>OBSERVABILITY_ONLY</td><td>{_esc(revision_state)} · eventos={_esc(revision.get('total_valid'))} · p95={_esc(revision.get('age_p95_seconds'))} s</td><td>NO</td></tr>
        <tr><td><b>SWING_PAPER overnight</b></td><td>{_esc(swing['mode'])}</td><td>{_esc(swing['runtime_state'])} · estilos swing={_esc(swing['explicit_swing'])}</td><td>NO</td></tr>
        <tr><td><b>Caución cash sweep</b></td><td>{_esc(caucion['mode'])}</td><td>{_esc(caucion['runtime_state'])}</td><td>NO</td></tr>
      </tbody></table>
    </section>"""


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True

    old_scalping = bg.scalping_page
    old_trading = bg.trading_page
    old_live = bg.live_page

    def scalping_page_with_revision_metrics():
        return _append_before_main_end(old_scalping(), scalping_revision_section())

    def trading_page_with_shadow(section=""):
        normalized = str(section or "").strip().lower()
        page = old_trading(section)
        if normalized == "cauciones":
            page = _append_before_main_end(page, caucion_shadow_section())
        elif normalized == "estrategias":
            page = _append_before_main_end(page, swing_shadow_section())
        return page

    def live_page_with_operator_summary():
        return _append_before_main_end(old_live(), live_operator_section())

    bg.scalping_page = scalping_page_with_revision_metrics
    bg.trading_page = trading_page_with_shadow
    bg.live_page = live_page_with_operator_summary
