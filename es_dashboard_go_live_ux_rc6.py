"""RC6 go-live operator UX layer.

Presentation/read-only semantics for the 07-Sep PAPER campaign.  This module
patches dashboard renderers only.  It never writes DBs, calls PPI, changes
strategy/gates, or enables real orders.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import sqlite3

import ak_byma_calendar as byma_calendar
import bg_paper_dashboard as bg

_installed = False
_original = {}
_NEUTRAL_HEALTH_KEYS = {
    "PPI_PRODUCTION_HISTORY",
    "PPI_BACKGROUND_INGEST",
    "PAPER_SIGNAL_ROTATION",
    "BYMA_INSTRUMENTS_API",
    "ROFEX_MARKETDATA",
}


def _inject_before_main_end(html: str, fragment: str) -> str:
    marker = "</main>"
    return html.replace(marker, fragment + marker, 1) if marker in html else html + fragment


def _remove_card_by_heading(html: str, heading: str) -> str:
    # live_page uses sibling paper-card blocks.  Stop at next card or main/footer.
    pattern = re.compile(
        r"<div class=['\"]paper-card[^'\"]*['\"]>\s*<h2>\s*" + re.escape(heading) +
        r"\s*</h2>.*?(?=<div class=['\"]paper-card|</main>|<footer|$)",
        re.I | re.S,
    )
    return pattern.sub("", html)


def _health_components_truth():
    rows = _original["health_components"]()
    result = []
    for item in rows:
        row = dict(item)
        key = str(row.get("key") or "").upper()
        if key in _NEUTRAL_HEALTH_KEYS and str(row.get("state") or "").upper() in {"AMARILLO", "GRIS", "PENDIENTE"}:
            row["state"] = "GRIS"
            row["detail"] = "Informativo / no bloqueante para PAPER. " + str(row.get("detail") or "")
        result.append(row)
    return result


def _operational_day(value) -> bool:
    try:
        day = bg.aware_datetime(value).astimezone(bg.TZ).date()
        return bool(byma_calendar.es_dia_habil_operativo(day))
    except Exception:
        return False


def _latest_five_valid_operations() -> str:
    if not bg._table("paper_positions"):
        return ("<section class='paper-card'><h2>Últimas 5 operaciones PAPER válidas</h2>"
                "<div class='paper-warning'>Ledger de posiciones no disponible.</div></section>")
    records = bg._rows("SELECT * FROM paper_positions ORDER BY COALESCE(closed_at,opened_at) DESC LIMIT 200")
    valid = []
    for row in records:
        stamp = row.get("closed_at") or row.get("opened_at")
        if not stamp or not _operational_day(stamp):
            continue
        valid.append(row)
        if len(valid) == 5:
            break
    body = []
    for row in valid:
        stamp = row.get("closed_at") or row.get("opened_at")
        pnl = row.get("net_pnl")
        pnl_num = bg._num(pnl) if pnl not in (None, "") else None
        cls = "positive" if pnl_num is not None and pnl_num > 0 else "negative" if pnl_num is not None and pnl_num < 0 else "neutral"
        body.append(
            f"<tr><td>{bg._local_time(stamp)}</td><td><b>{bg._e(row.get('symbol'))}</b></td>"
            f"<td>{bg._e(row.get('asset_class'))}</td><td>{bg._status(row.get('status'))}</td>"
            f"<td>{bg._e(row.get('currency') or 'ARS')}</td><td>{bg._e(row.get('quantity'))}</td>"
            f"<td>{bg._e(row.get('entry_price'))}</td><td>{bg._e(row.get('exit_price') if row.get('exit_price') is not None else '—')}</td>"
            f"<td class='{cls}'>{bg._e('—' if pnl_num is None else bg._locale_number(pnl_num))}</td>"
            f"<td>{bg._e(row.get('close_reason') or 'ABIERTA')}</td></tr>"
        )
    rows = "".join(body) or "<tr><td colspan='10'>Sin operaciones PAPER en ruedas operativas.</td></tr>"
    return ("<section class='paper-card' id='ultimas-cinco-operaciones'><h2>Últimas 5 operaciones PAPER válidas</h2>"
            "<p class='paper-muted'>Se excluyen sábados, domingos y feriados BYMA. La ingesta puede continuar fuera de rueda, pero no se presenta como trading.</p>"
            "<table class='paper-table'><tr><th>Fecha/hora</th><th>Instrumento</th><th>Familia</th><th>Estado</th>"
            "<th>Moneda</th><th>Cantidad</th><th>Entrada</th><th>Salida</th><th>PnL neto</th><th>Causa</th></tr>"
            + rows + "</table></section>")


def home_page():
    html = _original["home_page"]()
    return _inject_before_main_end(html, _latest_five_valid_operations())


def live_page(*args, **kwargs):
    html = _original["live_page"](*args, **kwargs)
    # Exact operator request: remove technical block number 5 from En vivo.
    html = _remove_card_by_heading(html, "5. Motores / workers")
    html = _remove_card_by_heading(html, "Motores / workers")
    return html


def _history_db_path() -> Path:
    return Path(bg.DB_PATH).resolve().parents[1] / "market_history.db"


def _history_query(sql: str, params=()):
    path = _history_db_path()
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA query_only=ON")
        try:
            return [dict(r) for r in c.execute(sql, params).fetchall()]
        finally:
            c.close()
    except Exception:
        return []


def history_page():
    sync = (bg._rows("""SELECT source,status,last_attempt_at,last_success_at,items,detail
        FROM source_sync WHERE source IN ('PPI_PRODUCTION_HISTORY','PPI_BACKGROUND_INGEST')
        ORDER BY last_attempt_at DESC""") if bg._table("source_sync") else [])
    attempts = (bg._rows("""SELECT state,COUNT(*) identities,SUM(valid_rows) valid_rows
        FROM production_history_attempts GROUP BY state ORDER BY state""")
        if bg._table("production_history_attempts") else [])
    recent = (bg._rows("""SELECT symbol,instrument_type,state,valid_rows,attempted_at,detail
        FROM production_history_attempts ORDER BY julianday(attempted_at) DESC LIMIT 20""")
        if bg._table("production_history_attempts") else [])
    canonical = _history_query("SELECT source,COUNT(*) rows,COUNT(DISTINCT symbol||'|'||instrument_type||'|'||market||'|'||settlement) identities,MAX(date) latest FROM history_canonical_v2 GROUP BY source ORDER BY rows DESC")
    totals = _history_query("SELECT COUNT(*) rows,COUNT(DISTINCT symbol||'|'||instrument_type||'|'||market||'|'||settlement) identities,MIN(date) first_day,MAX(date) latest_day FROM history_canonical_v2")
    close_only = _history_query("SELECT COUNT(*) rows,COUNT(DISTINCT symbol||'|'||instrument_type||'|'||market||'|'||settlement) identities,MAX(date) latest_day FROM history_close_canonical_v1")
    t = totals[0] if totals else {}
    co = close_only[0] if close_only else {}
    last_sync = sync[0] if sync else {}

    cards = "".join((
        bg._card("Store FULL_OHLC", f"{int(t.get('rows') or 0):,} filas".replace(',', '.'),
                 f"{int(t.get('identities') or 0)} identidades · {t.get('first_day') or '—'} → {t.get('latest_day') or '—'}", "green" if t else "yellow"),
        bg._card("Evidencia close-only", f"{int(co.get('rows') or 0):,} filas".replace(',', '.'),
                 f"{int(co.get('identities') or 0)} identidades · sólo contexto, nunca ejecución", "gray"),
        bg._card("PPI histórico", last_sync.get("status") or "SIN REGISTRO",
                 f"Último intento {bg._local_time(last_sync.get('last_attempt_at'))} · {int(last_sync.get('items') or 0)} filas válidas vistas en el lote", "green" if str(last_sync.get('status')).upper()=='VERDE' else "gray"),
        bg._card("Integridad", "READ-ONLY / VERSIONADO", "Filas defectuosas se rechazan; las válidas se conservan sin sintetizar OHLC", "green"),
    ))
    source_rows = "".join(
        f"<tr><td>{bg._e(r.get('source'))}</td><td>{bg._e(r.get('rows'))}</td><td>{bg._e(r.get('identities'))}</td><td>{bg._e(r.get('latest'))}</td></tr>"
        for r in canonical) or "<tr><td colspan='4'>Store histórico no disponible.</td></tr>"
    attempt_rows = "".join(
        f"<tr><td>{bg._status(r.get('state'))}</td><td>{bg._e(r.get('identities'))}</td><td>{bg._e(r.get('valid_rows') or 0)}</td></tr>"
        for r in attempts) or "<tr><td colspan='3'>Sin intentos persistidos.</td></tr>"
    recent_rows = "".join(
        f"<tr><td>{bg._local_time(r.get('attempted_at'))}</td><td><b>{bg._e(r.get('symbol'))}</b></td><td>{bg._e(r.get('instrument_type'))}</td>"
        f"<td>{bg._status(r.get('state'))}</td><td>{bg._e(r.get('valid_rows'))}</td><td>{bg._e(r.get('detail'))}</td></tr>"
        for r in recent) or "<tr><td colspan='6'>Sin intentos recientes.</td></tr>"
    body = ("<h1>Históricos — estado operativo</h1>"
            "<div class='paper-notice'>Esta vista prioriza lo útil para controlar mañana: store canónico, fuentes, estados de ingesta y últimas ejecuciones. Las métricas técnicas extensas permanecen disponibles para auditoría, no en la vista del operador.</div>"
            f"<div class='paper-grid'>{cards}</div>"
            "<div class='paper-card'><h2>Fuentes del store canónico</h2><table class='paper-table'><tr><th>Fuente</th><th>Filas</th><th>Identidades</th><th>Último día</th></tr>"
            f"{source_rows}</table></div>"
            "<div class='paper-card'><h2>Estado de intentos PPI</h2><table class='paper-table'><tr><th>Estado</th><th>Identidades</th><th>Filas válidas</th></tr>"
            f"{attempt_rows}</table></div>"
            "<div class='paper-card'><h2>Últimos 20 intentos</h2><table class='paper-table'><tr><th>Hora</th><th>Instrumento</th><th>Familia</th><th>Estado</th><th>Filas válidas</th><th>Detalle</th></tr>"
            f"{recent_rows}</table></div>")
    return bg._document("Históricos", body, refresh=60)


def reports_page():
    html = _original["reports_page"]()
    financial = bg._main_fragment(_original["financial_page"]())
    fragment = ("<section class='paper-card' id='macro-y-performance'><h2>BCRA / INDEC / macro y performance vs inflación</h2>"
                "<p class='paper-muted'>Se reincorpora al menú Reportes para control operativo. Cada dato conserva su fuente, fecha y limitaciones metodológicas.</p>"
                + financial + "</section>")
    return _inject_before_main_end(html, fragment)


def config_page():
    try:
        import bf_production_paper_observer as observer
        active_limit = observer.ACTIVE_SYMBOL_LIMIT
        hist_limit = observer.HISTORY_BATCH_LIMIT
        bg_seconds = observer.BACKGROUND_INGEST_SECONDS
        ready_seconds = observer.READINESS_CHECK_SECONDS
        signal_samples = observer.SIGNAL_MIN_SAMPLES
        signal_window = observer.SIGNAL_WINDOW_MINUTES
        focus_count = len(observer.FOCUS_SYMBOLS)
        open_time = f"{observer.MARKET_OPEN_HOUR:02d}:{observer.MARKET_OPEN_MINUTE:02d}"
        close_time = f"{observer.MARKET_CLOSE_HOUR:02d}:{observer.MARKET_CLOSE_MINUTE:02d}"
    except Exception:
        active_limit = hist_limit = bg_seconds = ready_seconds = signal_samples = signal_window = focus_count = "—"
        open_time = close_time = "—"
    rows = [
        ("Modo operativo", bg._effective_mode(), "operation_mode.json / observer_state"),
        ("Ejecución", "SIMULATED", "PAPER; no envía órdenes reales"),
        ("Capacidad de órdenes reales", "BLOCKED", "invariante de release RC6"),
        ("IA intradía", "OFF", "política RC6; no participa de decisiones"),
        ("Apertura BYMA", open_time, "runtime observer"),
        ("Cierre BYMA", close_time, "runtime observer"),
        ("Instrumentos activos por ciclo", active_limit, "PAPER_ACTIVE_SYMBOL_LIMIT efectivo"),
        ("Foco configurado", focus_count, "PAPER_FOCUS_SYMBOLS efectivo"),
        ("Muestras mínimas de señal", signal_samples, "PAPER_SIGNAL_MIN_SAMPLES efectivo"),
        ("Ventana de señal", f"{signal_window} min", "PAPER_SIGNAL_WINDOW_MINUTES efectivo"),
        ("Lote histórico PPI", hist_limit, "PPI_HISTORY_BATCH_LIMIT efectivo"),
        ("Cadencia mínima background", f"{bg_seconds} s", "PPI_BACKGROUND_INGEST_SECONDS efectivo"),
        ("Readiness", f"{ready_seconds} s", "PAPER_READINESS_CHECK_SECONDS efectivo"),
        ("Gate económico", bg.PAPER_ECONOMIC_GATE_MODE, "Python matemático; no habilita dinero real"),
        ("Scalping", bg.PAPER_SCALPING_MODE, "PAPER/observación según contrato"),
        ("Timezone", str(bg.TZ), "timezone operacional"),
    ]
    tr = "".join(f"<tr><td><b>{bg._e(k)}</b></td><td>{bg._e(v)}</td><td>{bg._e(src)}</td></tr>" for k,v,src in rows)
    body = ("<h1>Configuración efectiva RC6</h1>"
            "<div class='paper-notice'>Sólo variables que describen el runtime actual. Se omiten variables legacy/deprecadas para no confundir al operador.</div>"
            "<div class='paper-card'><table class='paper-table'><tr><th>Parámetro</th><th>Valor efectivo</th><th>Fuente / significado</th></tr>"
            + tr + "</table></div>")
    return bg._document("Configuración", body, refresh=60)


def logs_page():
    sources = bg.discover_sources()
    rows = []
    nonempty = []
    for source in sources:
        size = int(getattr(source, "size_bytes", 0) or 0)
        if size > 0:
            nonempty.append(source)
        status = "CON DATOS" if size else "ARCHIVO VACÍO"
        detail = ("Snapshot local disponible." if size else
                  "No significa ausencia de actividad: el runtime puede estar escribiendo a stdout; revisar exportador host.")
        rows.append(f"<tr><td><b>{bg._e(source.label)}</b></td><td><code>{bg._e(source.path)}</code></td>"
                    f"<td>{bg._e(size)}</td><td>{bg._status(status)}</td><td>{bg._e(detail)}</td>"
                    f"<td><a href='/api/logs/current?source={bg._e(source.source_id)}'>Descargar</a></td></tr>")
    chosen = nonempty[0] if nonempty else (sources[0] if sources else None)
    if chosen:
        live = "\n".join(bg.tail_lines(chosen, 50)) or "El snapshot elegido existe pero todavía no contiene líneas."
        preview = f"Vista previa: {chosen.label} · {chosen.path}"
    else:
        live = "Sin snapshot compartido."
        preview = "Sin fuente disponible"
    body = ("<h1>Logs</h1><div class='paper-notice'>Los tamaños son los archivos exportados que puede leer el dashboard. Un archivo de 0 bytes se marca explícitamente como vacío; no se transforma en un falso estado sano.</div>"
            "<div class='paper-card'><h2>Fuentes</h2><table class='paper-table'><tr><th>Fuente</th><th>Archivo</th><th>Bytes</th><th>Estado</th><th>Interpretación</th><th>Acción</th></tr>"
            + ("".join(rows) or "<tr><td colspan='6'>Sin fuentes.</td></tr>") + "</table></div>"
            f"<div class='paper-card'><h2>Últimas 50 líneas disponibles</h2><p class='paper-muted'>{bg._e(preview)}</p><pre style='white-space:pre-wrap;overflow-wrap:anywhere'>{bg._e(live)}</pre></div>")
    return bg._document("Logs", body, refresh=30)


def scheduler_content():
    html = _original["scheduler_content"]()
    try:
        snap = bg.load_systemd_snapshot()
    except Exception as exc:
        snap = {"state":"ERROR", "error":f"{type(exc).__name__}: {exc}"}
    if isinstance(snap, dict) and snap.get("error"):
        warning = ("<div class='paper-warning'><b>Snapshot scheduler con error:</b> "
                   + bg._e(snap.get("error")) + ". Se muestran las evidencias disponibles sin inventar estado.</div>")
        html = warning + html
    return html


def introspection_content():
    html = _original["introspection_content"]()
    # The old card used a fixed identity threshold and could make healthy progressive
    # ingestion look like a fault.  Replace only that card with current store truth.
    totals = _history_query("SELECT COUNT(*) rows,COUNT(DISTINCT symbol||'|'||instrument_type||'|'||market||'|'||settlement) identities,MAX(date) latest FROM history_canonical_v2")
    t = totals[0] if totals else {}
    replacement = bg._card("Históricos", f"{int(t.get('rows') or 0):,} filas".replace(',', '.'),
                           f"{int(t.get('identities') or 0)} identidades FULL_OHLC · último día {t.get('latest') or '—'} · ingesta progresiva", "green" if t else "yellow")
    html = re.sub(r"<div class='paper-card card-[^']+'>Históricos<br>.*?</div>", replacement, html, count=1, flags=re.I|re.S)
    return html


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    _original.update({
        "health_components": bg._health_components,
        "home_page": bg.home_page,
        "live_page": bg.live_page,
        "history_page": bg.history_page,
        "reports_page": bg.reports_page,
        "financial_page": bg.financial_page,
        "config_page": bg.config_page,
        "logs_page": bg.logs_page,
        "scheduler_content": bg.scheduler_content,
        "introspection_content": bg.introspection_content,
    })
    bg._health_components = _health_components_truth
    bg.home_page = home_page
    bg.live_page = live_page
    bg.history_page = history_page
    bg.reports_page = reports_page
    bg.config_page = config_page
    bg.logs_page = logs_page
    bg.scheduler_content = scheduler_content
    bg.introspection_content = introspection_content
