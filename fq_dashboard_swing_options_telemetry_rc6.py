"""RC6 trader-facing overlay: SWING shadow + Opciones data telemetry + scalping revision telemetry.

Read-only presentation.  The module does not place orders, mutate SQLite, call
PPI, promote SHADOW policies, or change CURRENT_EOD.  It is installed after
the P0 EOD route repair so Trading -> Estrategias keeps the EOD evaluator and
adds SWING_PAPER beside it instead of replacing it.
"""
from __future__ import annotations

from contextlib import closing

import bg_paper_dashboard as bg
import fi_swing_paper_shadow_rc6 as swing_shadow
import fk_scalping_revision_metrics_rc6 as revision_metrics

_installed = False


def _esc(value):
    import html
    return html.escape(str(value if value not in (None, "") else "—"))


def _append(page: str, fragment: str, marker_id: str) -> str:
    if marker_id in page:
        return page
    marker = "</main>"
    return page.replace(marker, fragment + marker, 1) if marker in page else page + fragment


def _badge(label, css="s-amarillo"):
    return f"<span class='paper-status {_esc(css)}'>{_esc(label)}</span>"


def _revision_snapshot():
    empty = {
        "state": "NO_EVIDENCE", "total_valid": 0, "invalid_events": 0,
        "refresh_mutable": 0, "reject_closed_revision": 0,
        "threshold_seconds": 120, "age_p50_seconds": None,
        "age_p95_seconds": None, "age_max_seconds": None,
        "latest_received_at": None, "by_symbol": {},
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
    css = "s-verde" if state == "EVIDENCE_AVAILABLE" else "s-amarillo"
    symbols = list((data.get("by_symbol") or {}).items())[:5]
    symbol_rows = "".join(
        f"<tr><td><b>{_esc(symbol)}</b></td><td>{_esc(count)}</td></tr>"
        for symbol, count in symbols
    ) or "<tr><td colspan='2'>Sin revisiones persistidas todavía.</td></tr>"
    return f"""
    <section class='paper-card' id='rc6-scalping-revision-telemetry-live'>
      <h2>Calidad temporal de velas — telemetría RC6</h2>
      <p>{_badge(state, css)} <span class='paper-muted'>OBSERVABILITY_ONLY · no cambia el gate ni el umbral operativo.</span></p>
      <div class='paper-grid'>
        <div class='paper-card'><h3>Revisiones válidas</h3><b class='metric'>{_esc(data.get('total_valid'))}</b><div class='paper-muted'>Inválidas: {_esc(data.get('invalid_events'))}</div></div>
        <div class='paper-card'><h3>Dentro de ventana mutable</h3><b class='metric'>{_esc(data.get('refresh_mutable'))}</b><div class='paper-muted'>REFRESH_MUTABLE</div></div>
        <div class='paper-card'><h3>Punto cerrado revisado</h3><b class='metric'>{_esc(data.get('reject_closed_revision'))}</b><div class='paper-muted'>REJECT_CLOSED_REVISION</div></div>
        <div class='paper-card'><h3>Umbral actual</h3><b class='metric'>{_esc(data.get('threshold_seconds'))} s</b><div class='paper-muted'>Se mantiene en 120 s.</div></div>
      </div>
      <table class='paper-table classic-responsive-table'><thead><tr><th>Indicador</th><th>Valor</th></tr></thead><tbody>
        <tr><td>Edad p50</td><td>{_esc(data.get('age_p50_seconds'))} s</td></tr>
        <tr><td>Edad p95</td><td>{_esc(data.get('age_p95_seconds'))} s</td></tr>
        <tr><td>Edad máxima</td><td>{_esc(data.get('age_max_seconds'))} s</td></tr>
        <tr><td>Última evidencia</td><td>{_esc(bg._local_time(data.get('latest_received_at')))}</td></tr>
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


def swing_shadow_section():
    swing_shadow.assert_shadow_only()
    explicit_swing = intraday = eod_closed = 0
    for row in _recent_positions():
        style = swing_shadow.execution_style(row)
        if style == swing_shadow.SWING_STYLE:
            explicit_swing += 1
        if style in swing_shadow.INTRADAY_STYLES:
            intraday += 1
        reason = str(row.get("close_reason") or row.get("exit_reason") or "").upper()
        if str(row.get("status") or "").upper() == "CLOSED" and reason == "EOD_PAPER":
            eod_closed += 1
    state = "EXPLICIT_STYLE_PRESENT" if explicit_swing else "EVIDENCE_PENDING"
    return f"""
    <section class='paper-card' id='rc6-swing-paper-shadow-live'>
      <h2>SWING_PAPER — evaluación overnight</h2>
      <p>{_badge(swing_shadow.SHADOW_MODE,'s-amarillo')} {_badge(state,'s-amarillo')}</p>
      <p class='paper-muted'>Evaluador contrafactual. No reemplaza CURRENT_EOD y nunca reclasifica una posición por inferencia.</p>
      <table class='paper-table classic-responsive-table'><thead><tr><th>Indicador</th><th>Estado</th></tr></thead><tbody>
        <tr><td>Posiciones recientes con estilo SWING_PAPER explícito</td><td>{_esc(explicit_swing)}</td></tr>
        <tr><td>Posiciones intradía/scalping explícitas</td><td>{_esc(intraday)}</td></tr>
        <tr><td>Cierres EOD_PAPER observados</td><td>{_esc(eod_closed)}</td></tr>
        <tr><td>Economía overnight</td><td>SWING_NON_INTRADAY</td></tr>
        <tr><td>EOD exit binding</td><td><b>NO — CURRENT_EOD permanece vigente</b></td></tr>
        <tr><td>Ejecución real permitida</td><td><b>NO</b></td></tr>
      </tbody></table>
      <div class='paper-warning'>CARRY_OVERNIGHT sólo puede existir como veredicto SHADOW para una posición marcada explícitamente SWING_PAPER y con calendario, próxima sesión, mark fresco, tesis, horizonte y gap-risk válidos.</div>
    </section>"""


def _options_snapshot():
    result = {
        "total": 0, "available": 0, "can_simulate": 0,
        "snapshots": 0, "symbols": 0, "last_snapshot": None,
        "contract_states": {},
    }
    try:
        rows = bg._rows("""SELECT COUNT(*) total,
          SUM(CASE WHEN upper(status)='AVAILABLE' THEN 1 ELSE 0 END) available,
          SUM(CASE WHEN can_simulate=1 THEN 1 ELSE 0 END) can_simulate
          FROM candidate_universe WHERE upper(instrument_type)='OPCIONES'""")
        if rows:
            result.update({k: rows[0].get(k) or 0 for k in ("total", "available", "can_simulate")})
    except Exception:
        pass
    try:
        rows = bg._rows("""SELECT COUNT(*) snapshots,COUNT(DISTINCT symbol) symbols,MAX(observed_at) last_snapshot
          FROM market_snapshots
          WHERE upper(asset_class)='OPCIONES' AND substr(observed_at,1,10)=strftime('%Y-%m-%d','now')""")
        if rows:
            result.update({k: rows[0].get(k) for k in ("snapshots", "symbols", "last_snapshot")})
    except Exception:
        pass
    if bg._table("ppi_intraday_contract_state"):
        try:
            states = bg._rows("""SELECT state,COUNT(*) n FROM ppi_intraday_contract_state
              WHERE upper(asset_class)='OPCIONES' GROUP BY state ORDER BY state""")
            result["contract_states"] = {str(r.get("state") or "UNKNOWN"): int(r.get("n") or 0) for r in states}
        except Exception:
            pass
    return result


def options_telemetry_section():
    data = _options_snapshot()
    states = data.get("contract_states") or {}
    state_rows = "".join(
        f"<tr><td>{_esc(state)}</td><td>{_esc(count)}</td></tr>" for state, count in sorted(states.items())
    ) or "<tr><td colspan='2'>Sin evidencia intradía persistida todavía.</td></tr>"
    return f"""
    <section class='paper-card' id='rc6-options-data-telemetry-live'>
      <h2>Opciones — telemetría de datos y contrato</h2>
      <p class='paper-muted'>READ_ONLY / OBSERVABILITY_ONLY. Cobertura de datos no equivale a habilitación PAPER ni autoriza órdenes.</p>
      <div class='paper-grid'>
        <div class='paper-card'><h3>Universo</h3><b class='metric'>{_esc(data.get('total'))}</b><div class='paper-muted'>identidades observadas en candidate_universe</div></div>
        <div class='paper-card'><h3>AVAILABLE</h3><b class='metric'>{_esc(data.get('available'))}</b></div>
        <div class='paper-card'><h3>can_simulate</h3><b class='metric'>{_esc(data.get('can_simulate'))}</b><div class='paper-muted'>No se modifica desde esta vista.</div></div>
        <div class='paper-card'><h3>Snapshots hoy</h3><b class='metric'>{_esc(data.get('snapshots'))}</b><div class='paper-muted'>símbolos: {_esc(data.get('symbols'))}</div></div>
      </div>
      <p class='paper-muted'>Último snapshot: {_esc(bg._local_time(data.get('last_snapshot')))}</p>
      <table class='paper-table classic-responsive-table'><thead><tr><th>Estado contrato intradía</th><th>Identidades</th></tr></thead><tbody>{state_rows}</tbody></table>
    </section>"""


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True

    old_scalping = bg.scalping_page
    old_trading = bg.trading_page

    def scalping_page_with_telemetry():
        page = old_scalping()
        return _append(page, scalping_revision_section(), "rc6-scalping-revision-telemetry-live")

    def trading_page_with_strategy_telemetry(section=""):
        normalized = str(section or "").strip().lower()
        page = old_trading(section)
        if normalized == "estrategias":
            page = _append(page, swing_shadow_section(), "rc6-swing-paper-shadow-live")
        elif normalized == "opciones":
            page = _append(page, options_telemetry_section(), "rc6-options-data-telemetry-live")
        return page

    bg.scalping_page = scalping_page_with_telemetry
    bg.trading_page = trading_page_with_strategy_telemetry
