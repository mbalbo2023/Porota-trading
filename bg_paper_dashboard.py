"""Dashboard operativo para PRODUCTION_PAPER, sin credenciales ni red.

La vista y sus acciones leen/escriben exclusivamente estado persistido. El
dashboard nunca importa el SDK de PPI. El observador aislado consume los
comandos permitidos y conserva la barrera HTTP de solo lectura.
"""

from __future__ import annotations

import html
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse


DB_PATH = os.getenv("PAPER_DB_PATH", "data/observer/observer_production.db")
LEGACY_DB_PATH = os.getenv("DB_PATH", "data/trading_system.db")
MODE = os.getenv("DASHBOARD_OPERATION_MODE", "DETENIDO").upper()
TZ = ZoneInfo(os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires"))
PAPER_INITIAL_CAPITAL = os.getenv("PAPER_INITIAL_CAPITAL_ARS", "1000000")
PAPER_RISK_PCT = os.getenv("PAPER_RISK_PER_TRADE", "0.005")
PAPER_MAX_POSITION_PCT = os.getenv("PAPER_MAX_POSITION_PCT", "0.25")
PAPER_MAX_TOTAL_EXPOSURE_PCT = os.getenv("PAPER_MAX_TOTAL_EXPOSURE_PCT", "0.60")
_installed = False

THEME = """
<style id='porota-paper-theme'>
:root{--bg:#f3f6fa;--panel:#fff;--ink:#172033;--muted:#667085;--line:#dce3ed;
--nav:#14213d;--action:#1769aa;--green:#16833b;--yellow:#a56800;--red:#c62828;--gray:#667085}
*{box-sizing:border-box}body{margin:0!important;background:var(--bg)!important;color:var(--ink)!important;
font-family:system-ui,-apple-system,'Segoe UI',sans-serif!important;max-width:none!important;padding:0!important}
#porota-canonical-nav{position:sticky;top:0;z-index:10000;background:var(--nav);color:#fff;
display:flex;align-items:center;gap:5px;flex-wrap:wrap;padding:9px 14px;box-shadow:0 2px 10px #0002}
#porota-canonical-nav a,#porota-canonical-nav button{color:#fff;background:transparent;border:1px solid #ffffff38;
border-radius:8px;padding:7px 9px;text-decoration:none;font:600 13px system-ui;cursor:pointer}
#porota-canonical-nav a:hover,#porota-canonical-nav button:hover{background:#ffffff18}
#porota-paper-mode{max-width:1180px;margin:14px auto 0;padding:12px 16px;background:#e9eef5;
border:1px solid #c9d4e3;border-left:5px solid var(--nav);border-radius:10px;font-weight:750}
#porota-paper-mode small{display:block;color:#475467;font-weight:450;margin-top:3px}
.paper-page{max-width:1180px;margin:0 auto;padding:18px}.paper-page h1{font-size:1.55rem;margin:4px 0 14px}
.paper-page h2{font-size:1.08rem;margin:4px 0 12px}.paper-page h3{font-size:1rem;margin:8px 0}
.paper-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin:14px 0}
.paper-card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:15px;
box-shadow:0 3px 14px #14213d0c;margin:12px 0;overflow:auto}.paper-card b.metric{font-size:1.22rem}
.paper-table{width:100%;border-collapse:collapse;font-size:.88rem}.paper-table th{background:#edf2f7}
.paper-table th,.paper-table td{padding:9px;border-bottom:1px solid #e5e9f0;text-align:left;vertical-align:top}
.paper-muted{color:var(--muted);font-size:.86rem}.paper-action{display:inline-block;background:var(--action);color:#fff;
border:0;border-radius:8px;padding:9px 13px;text-decoration:none;font-weight:700;cursor:pointer}
.paper-status{display:inline-block;border-radius:999px;padding:3px 8px;font-weight:750;color:#fff;white-space:nowrap}
.s-verde{background:var(--green)}.s-amarillo{background:var(--yellow)}.s-rojo{background:var(--red)}.s-gris{background:var(--gray)}
.paper-notice{padding:11px 14px;border:1px solid #c9d4e3;background:#eef3f8;border-radius:9px;margin:10px 0}
.paper-warning{padding:11px 14px;border:1px solid #e7c979;background:#fff7df;border-radius:9px;margin:10px 0}
details.paper-trade{background:#fff;border:1px solid var(--line);border-radius:11px;margin:10px 0;overflow:hidden}
details.paper-trade>summary{cursor:pointer;padding:13px 15px;font-weight:750;background:#f8fafc;list-style-position:inside}
.trade-body{padding:4px 15px 15px}.timeline{border-left:3px solid #bcc8d8;padding-left:15px;margin:10px 0}
.timeline>div{margin:10px 0}.verdict-ok{color:var(--green)}.verdict-bad{color:var(--red)}.verdict-pending{color:var(--gray)}
code{white-space:normal;overflow-wrap:anywhere}.legacy-shell{background:transparent}.legacy-shell>h1{margin-top:4px}
.legacy-shell table{width:100%!important;border-collapse:collapse!important;background:#fff;border:1px solid var(--line);
border-radius:10px;overflow:hidden}.legacy-shell th{background:#edf2f7!important}.legacy-shell th,.legacy-shell td{
padding:9px!important;border-bottom:1px solid #e5e9f0!important;text-align:left}.legacy-shell h2{color:var(--ink)!important;
border-bottom:1px solid var(--line)!important;padding-bottom:7px}.legacy-shell button,.legacy-shell input[type=submit]{
background:var(--action)!important;color:#fff!important;border:0!important;border-radius:8px!important;padding:9px 13px!important}
@media(max-width:700px){#porota-canonical-nav{position:relative}
.paper-page{padding:12px}.paper-table{font-size:.79rem}.paper-table th,.paper-table td{padding:7px}}
</style>"""

MODE_INFO = {
    "PRODUCTION_PAPER": ("MODO SIMULACIÓN PRODUCTIVA", "PPI Producción solo lectura; compras y ventas simuladas; órdenes reales: NINGUNA."),
    "SANDBOX": ("MODO SANDBOX", "PPI Sandbox; únicamente operaciones del entorno de pruebas."),
    "PRODUCTION_REAL": ("MODO PRODUCCIÓN REAL", "Las órdenes autorizadas pueden utilizar dinero real."),
    "DETENIDO": ("PLATAFORMA DETENIDA", "Dashboard disponible; ningún motor de trading está activo."),
}


def _e(value):
    return html.escape(str(value if value not in (None, "") else "—"))


def _money(value):
    try:
        return f"${float(value):,.2f}"
    except Exception:
        return "—"


def _local_time(value):
    if value in (None, ""):
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TZ)
        return parsed.astimezone(TZ).strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return _e(value)


def _conn(path=DB_PATH):
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(sql, params=(), path=DB_PATH):
    try:
        with _conn(path) as connection:
            return [dict(row) for row in connection.execute(sql, params).fetchall()]
    except Exception:
        return []


def _table(name, path=DB_PATH):
    try:
        with _conn(path) as connection:
            return bool(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone())
    except Exception:
        return False


def _ensure_command_schema():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    with _conn() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS observer_commands(
          id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
          command TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT,
          finished_at TEXT, result TEXT NOT NULL DEFAULT '')""")


def mode_banner():
    title, detail = MODE_INFO.get(MODE, (f"MODO {_e(MODE)}", "Estado operativo no reconocido."))
    return f"<div id='porota-paper-mode'>{_e(title)}<small>{_e(detail)}</small></div>"


def _nav():
    links = (
        ("/", "Inicio"), ("/vivo", "Actividad"), ("/observacion", "Simulación"),
        ("/motor-trading", "Motor de trading"), ("/salud", "Salud de APIs"),
        ("/historicos", "Históricos"), ("/aprendizaje", "Blog de aprendizaje"),
        ("/telegram", "Telegram"), ("/sre", "SRE"), ("/dashboard/logs", "Logs"),
        ("/api/diagnostics/download", "Diagnóstico"), ("/config", "Configuración"),
    )
    items = "".join(f"<a href='{href}'>{label}</a>" for href, label in links)
    return ("<nav id='porota-canonical-nav'><button type='button' "
            "onclick=\"if(history.length>1){history.back()}else{location.href='/'}\">← Volver</button>"
            + items + "</nav>")


def _document(title, body, refresh=None):
    refresh_tag = f"<meta http-equiv='refresh' content='{int(refresh)}'>" if refresh else ""
    return ("<!doctype html><html lang='es'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"{refresh_tag}<title>{_e(title)}</title>{THEME}</head><body>{_nav()}{mode_banner()}"
            f"<main class='paper-page'>{body}</main></body></html>")


def _canonicalize(content, path=""):
    """Deja un solo menú, un solo banner y un solo tema en cualquier página."""
    if (content.count("id='porota-canonical-nav'") == 1 and
            content.count("id='porota-paper-mode'") == 1 and
            content.count("id='porota-paper-theme'") == 1):
        return content
    content = re.sub(r"<nav[^>]+id=['\"]porota-top-nav['\"][^>]*>.*?</nav>", "", content,
                     flags=re.I | re.S)
    content = re.sub(r"<nav[^>]+id=['\"]porota-canonical-nav['\"][^>]*>.*?</nav>", "", content,
                     flags=re.I | re.S)
    content = re.sub(r"<div[^>]+class=['\"]nav['\"][^>]*>.*?</div>", "", content,
                     flags=re.I | re.S)
    content = re.sub(r"<div[^>]+id=['\"]porota-paper-mode['\"][^>]*>.*?</div>", "", content,
                     flags=re.I | re.S)
    content = re.sub(r"<style[^>]+id=['\"]porota-paper-theme['\"][^>]*>.*?</style>", "", content,
                     flags=re.I | re.S)
    if path == "/":
        # Retira únicamente bloques de navegación heredados de la portada;
        # el menú superior conserva todas las rutas en un solo lugar.
        content = re.sub(
            r"<p[^>]*>(?:(?!</p>).)*(?:href=['\"]/(?:vivo|salud|testing|historicos|config)['\"])(?:(?!</p>).)*</p>",
            "", content, flags=re.I | re.S)
    if "</head>" in content.lower():
        content = re.sub(r"</head>", THEME + "</head>", content, count=1, flags=re.I)
    body = re.search(r"<body[^>]*>", content, flags=re.I)
    closing = re.search(r"</body>", content, flags=re.I)
    if body and closing:
        inner = content[body.end():closing.start()]
        if "id='porota-legacy-shell'" not in inner:
            inner = f"<main id='porota-legacy-shell' class='paper-page legacy-shell'>{inner}</main>"
        content = (content[:body.end()] + _nav() + mode_banner() + inner +
                   content[closing.start():])
    return content


def snapshot():
    state_rows = _rows("SELECT * FROM observer_state WHERE id=1")
    state = state_rows[0] if state_rows else {
        "mode": "PRODUCTION_PAPER", "process_state": "STOPPED", "session_state": "UNKNOWN",
        "ppi_auth": "NOT_ATTEMPTED", "real_orders_sent": 0,
        "detail": "Observador todavía no iniciado."}
    state["real_orders_sent"] = 0
    quotes = _rows("""SELECT s.* FROM market_snapshots s JOIN
      (SELECT symbol,MAX(id) id FROM market_snapshots GROUP BY symbol) x ON x.id=s.id ORDER BY s.symbol""")
    opened = _rows("SELECT * FROM paper_positions WHERE status='OPEN' ORDER BY opened_at DESC")
    closed = _rows("SELECT * FROM paper_positions WHERE status='CLOSED' ORDER BY closed_at DESC LIMIT 30")
    decisions = _rows("SELECT * FROM paper_decisions ORDER BY id DESC LIMIT 30")
    equity_rows = _rows("SELECT * FROM paper_equity ORDER BY id DESC LIMIT 1")
    samples = _rows("SELECT COUNT(*) total,SUM(CASE WHEN label_timestamp IS NOT NULL THEN 1 ELSE 0 END) labeled FROM paper_learning_samples")
    commands = _rows("SELECT * FROM observer_commands ORDER BY id DESC LIMIT 1")
    return {"state": state, "quotes": quotes, "open": opened, "closed": closed,
            "decisions": decisions, "equity": equity_rows[0] if equity_rows else {},
            "learning": samples[0] if samples else {}, "command": commands[0] if commands else {}}


def _probe_form():
    return """<div class='paper-card'><h2>Conexión manual de solo lectura</h2>
    <p>Realiza un único login contra PPI Producción y descarga el catálogo e históricos permitidos.
    No consulta la cuenta y la barrera técnica bloquea cualquier ruta de órdenes. Si la rueda está
    cerrada, valida acceso y sincroniza datos pero <b>no ejecuta la estrategia ni crea operaciones paper</b>.</p>
    <form method='post' action='/api/paper/login-readonly'>
      <button class='paper-action' type='submit'>Conectar y sincronizar datos</button>
    </form></div>"""


def paper_page(compact=False):
    data, body_rows = snapshot(), []
    state, equity = data["state"], data["equity"]
    for quote in data["quotes"]:
        body_rows.append(f"<tr><td><b>{_e(quote['symbol'])}</b></td><td>{_money(quote['last'])}</td>"
                         f"<td>{_money(quote['bid'])}</td><td>{_money(quote['ask'])}</td>"
                         f"<td>{_local_time(quote['observed_at'])}</td></tr>")
    quotes = "".join(body_rows) or "<tr><td colspan='5'>Esperando la primera cotización real.</td></tr>"
    opened = "".join(f"<tr><td>{_e(p['paper_id'])}</td><td><b>{_e(p['symbol'])}</b></td>"
                     f"<td>{_e(p['quantity'])}</td><td>{_money(p['entry_price'])}</td>"
                     f"<td>{_money(p['stop_price'])}</td><td>{_money(p['target_price'])}</td></tr>"
                     for p in data["open"]) or "<tr><td colspan='6'>Sin posiciones simuladas abiertas.</td></tr>"
    decisions = "".join(f"<tr><td>{_local_time(d['decided_at'])}</td><td>{_e(d['symbol'])}</td>"
                        f"<td>{_status(d['action'])}</td><td>{_e(d['score'])}</td><td>{_e(d['reason'])}</td></tr>"
                        for d in data["decisions"]) or "<tr><td colspan='5'>Esperando datos para decidir.</td></tr>"
    command = data["command"]
    command_text = (f"Última solicitud: {_e(command.get('status'))} · {_local_time(command.get('created_at'))}"
                    f" · {_e(command.get('result'))}") if command else "Todavía no se solicitó una sincronización manual."
    learning = data["learning"]
    process_labels = {"WAITING_MARKET": "EN ESPERA", "READY_PREOPEN": "PREAPERTURA",
                      "RUNNING": "EVALUANDO", "DEGRADED": "DEGRADADO", "STARTING": "INICIANDO"}
    session_labels = {"MARKET_CLOSED": "CERRADA", "PREOPEN": "PREAPERTURA",
                      "OPEN": "ABIERTA", "MARKET_OPEN": "ABIERTA"}
    process_label = process_labels.get(str(state.get("process_state")), str(state.get("process_state")))
    session_label = session_labels.get(str(state.get("session_state")), str(state.get("session_state")))
    body = f"""<h1>Simulación productiva y aprendizaje</h1>
    <div class='paper-grid'>
      <div class='paper-card'>Motor de simulación<br><b class='metric'>{_e(process_label)}</b><br><span class='paper-muted'>{_e(state.get('detail'))}</span></div>
      <div class='paper-card'>Rueda BYMA<br><b class='metric'>{_e(session_label)}</b><br><span class='paper-muted'>Sólo evalúa instrumentos con rueda abierta</span></div>
      <div class='paper-card'>PPI Producción<br><b class='metric'>{_e(state.get('ppi_auth'))}</b><br><span class='paper-muted'>Solo lectura; órdenes reales: 0</span></div>
      <div class='paper-card'>Capital inicial ficticio<br><b class='metric'>{_money(PAPER_INITIAL_CAPITAL)}</b><br><span class='paper-muted'>Patrimonio paper actual {_money(equity.get('equity'))}</span></div>
      <div class='paper-card'>Aprendizaje paper<br><b class='metric'>{_e(learning.get('labeled',0))}/{_e(learning.get('total',0))}</b><br><span class='paper-muted'>muestras cerradas/totales</span></div>
    </div>
    <div class='paper-notice'><b>Política patrimonial paper:</b> riesgo máximo {float(PAPER_RISK_PCT)*100:.1f}% por operación;
    tope {float(PAPER_MAX_POSITION_PCT)*100:.0f}% por posición; exposición total máxima
    {float(PAPER_MAX_TOTAL_EXPOSURE_PCT)*100:.0f}%; hasta 3 posiciones. Este capital es ficticio y nunca se
    confunde con el saldo real de tu cuenta.</div>
    <div class='paper-notice'><a href='/motor-trading'><b>Abrir Motor de trading</b></a> para ver el razonamiento de cada operación, sus variables y su resultado.</div>
    {_probe_form()}<p class='paper-muted'>{command_text}</p>
    <div class='paper-card'><h2>Cotizaciones reales observadas</h2><table class='paper-table'>
      <tr><th>Instrumento</th><th>Último</th><th>Bid</th><th>Ask</th><th>Último dato</th></tr>{quotes}</table></div>
    <div class='paper-card'><h2>Posiciones abiertas — todas simuladas</h2><table class='paper-table'>
      <tr><th>ID paper</th><th>Instrumento</th><th>Cantidad</th><th>Entrada</th><th>Stop</th><th>Objetivo</th></tr>{opened}</table></div>
    <div class='paper-card'><h2>Últimas decisiones</h2><table class='paper-table'>
      <tr><th>Hora</th><th>Instrumento</th><th>Acción</th><th>Score</th><th>Motivo</th></tr>{decisions}</table></div>"""
    return _document("Simulación productiva", body, refresh=30) if compact else body


def _status(value):
    raw = str(value or "GRIS").upper()
    if raw in {"VERDE", "OK", "RUNNING", "CONNECTED", "BUY", "BUY_SIMULATED"}:
        state, label = "verde", raw
    elif raw in {"AMARILLO", "DEGRADED", "QUEUED", "RUNNING_COMMAND", "HOLD"}:
        state, label = "amarillo", raw
    elif raw in {"ROJO", "ERROR", "FAILED", "BLOCKED"}:
        state, label = "rojo", raw
    else:
        state, label = "gris", raw
    return f"<span class='paper-status s-{state}'>{_e(label)}</span>"


def _health_status(value):
    raw = str(value or "GRIS").upper()
    if raw in {"VERDE", "OK", "CONNECTED", "HEALTHY"}:
        state, label = "verde", "VERDE"
    elif raw in {"AMARILLO", "DEGRADED", "PARTIAL", "WAITING", "COOLDOWN"}:
        state, label = "amarillo", "AMARILLO"
    elif raw in {"ROJO", "ERROR", "FAILED", "BLOCKED"}:
        state, label = "rojo", "ROJO"
    else:
        state, label = "gris", "GRIS"
    return f"<span class='paper-status s-{state}'>{label}</span>"


def _ai_for(position):
    if not _table("ai_shadow_evaluations"):
        return None
    rows = _rows("""SELECT * FROM ai_shadow_evaluations WHERE symbol=?
        AND evaluated_at<=? ORDER BY evaluated_at DESC LIMIT 1""",
        (position["symbol"], position["opened_at"]))
    return rows[0] if rows else None


def _decision_for(position):
    rows = _rows("""SELECT * FROM paper_decisions WHERE symbol=? AND decided_at<=?
        ORDER BY decided_at DESC LIMIT 1""", (position["symbol"], position["opened_at"]))
    return rows[0] if rows else {}


def _features(value):
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _verdict(position):
    if position.get("status") != "CLOSED":
        return "verdict-pending", "PENDIENTE", "La operación sigue abierta; todavía no existe una etiqueta final."
    try:
        pnl = float(position.get("net_pnl") or 0)
    except Exception:
        pnl = 0
    if pnl > 0:
        return "verdict-ok", "CORRECTA", "El resultado neto simulado fue positivo."
    return "verdict-bad", "INCORRECTA", "El resultado neto simulado fue nulo o negativo; queda como muestra de aprendizaje."


def motor_page():
    positions = _rows("SELECT * FROM paper_positions ORDER BY opened_at DESC LIMIT 150")
    cards = []
    for position in positions:
        decision, ai = _decision_for(position), _ai_for(position)
        fills = _rows("SELECT * FROM paper_fills WHERE paper_id=? ORDER BY id", (position["paper_id"],))
        events = _rows("SELECT * FROM paper_events WHERE paper_id=? ORDER BY id", (position["paper_id"],))
        variables = _features(position.get("features_json") or decision.get("features_json"))
        variable_rows = "".join(f"<tr><td>{_e(key)}</td><td>{_e(value)}</td></tr>"
                                for key, value in sorted(variables.items())) or \
                        "<tr><td colspan='2'>Sin variables persistidas.</td></tr>"
        fill_rows = "".join(f"<tr><td>{_local_time(fill['filled_at'])}</td><td>{_e(fill['side'])}</td>"
                            f"<td>{_e(fill['quantity'])}</td><td>{_money(fill['price'])}</td>"
                            f"<td>{_money(fill['costs'])}</td><td>{_e(fill['slippage'])}</td></tr>"
                            for fill in fills) or "<tr><td colspan='6'>Sin ejecuciones simuladas.</td></tr>"
        event_rows = "".join(f"<div><b>{_local_time(event['event_at'])} · {_e(event['event_type'])}</b>"
                             f"<br><span class='paper-muted'>{_e(event['detail'])}</span></div>"
                             for event in events) or "<div>Sin eventos adicionales.</div>"
        verdict_class, verdict, verdict_detail = _verdict(position)
        if ai:
            ai_text = (f"Participó en modo sombra. Resultado: {_e(ai.get('decision'))}; "
                       f"score {_e(ai.get('score'))}; fundamento: {_e(ai.get('reason'))}.")
        else:
            ai_text = ("IA generativa: NO PARTICIPÓ en esta decisión. La operación fue determinada "
                       "por el motor paper determinístico; el dashboard no atribuye decisiones a Gemini que no ocurrieron.")
        cards.append(f"""<details class='paper-trade'>
          <summary>{_e(position['symbol'])} · {_e(position['status'])} · {_local_time(position['opened_at'])} ·
          PnL {_money(position.get('net_pnl'))}</summary><div class='trade-body'>
          <div class='paper-grid'>
            <div><b>Operación</b><br>{_e(position['paper_id'])}<br>{_e(position['quantity'])} unidades</div>
            <div><b>Entrada simulada</b><br>{_money(position['entry_price'])}<br>costo {_money(position['entry_cost'])}</div>
            <div><b>Salida simulada</b><br>{_money(position.get('exit_price'))}<br>{_e(position.get('close_reason'))}</div>
            <div><b class='{verdict_class}'>{verdict}</b><br>{_e(verdict_detail)}</div>
          </div>
          <h3>1. Procedimiento y determinación</h3>
          <p>El sistema observó cotización y profundidad, calculó momentum, spread, umbral adaptativo,
          liquidez, presupuesto de riesgo, tope por posición, exposición total, costos, stop y objetivo.
          Determinó <b>{_e(decision.get('action'))}</b>
          con score {_e(decision.get('score'))}: {_e(decision.get('reason'))}.</p>
          <h3>2. Variables utilizadas</h3><table class='paper-table'><tr><th>Variable</th><th>Valor</th></tr>{variable_rows}</table>
          <h3>3. Reacción de la inteligencia artificial</h3><p>{ai_text}</p>
          <h3>4. Ejecuciones simuladas</h3><table class='paper-table'><tr><th>Hora</th><th>Lado</th>
          <th>Cantidad</th><th>Precio</th><th>Costos</th><th>Slippage</th></tr>{fill_rows}</table>
          <h3>5. Reacción y seguimiento del sistema</h3><div class='timeline'>{event_rows}</div>
          <h3>6. Resultado para aprendizaje</h3><p class='{verdict_class}'><b>{verdict}:</b> {_e(verdict_detail)}
          PnL neto: {_money(position.get('net_pnl'))}.</p></div></details>""")
    content = "".join(cards) or """<div class='paper-card'><h2>Todavía no existen operaciones simuladas</h2>
      <p>El motor empezará a registrar cada procedimiento cuando reúna datos suficientes y una señal supere
      los filtros. Las abstenciones siguen visibles en la página de Simulación.</p></div>"""
    body = f"""<h1>Motor de trading</h1><p class='paper-muted'>Trazabilidad operación por operación.
    Seleccioná una fila para desplegar el procedimiento completo, las variables y el resultado.</p>
    <div class='paper-warning'><b>Todas las operaciones de esta página son simuladas.</b>
    Nunca representan una orden enviada a PPI.</div>{content}"""
    return _document("Motor de trading", body, refresh=30)


def _legacy_event(component):
    if not _table("system_events", LEGACY_DB_PATH):
        return {}
    rows = _rows("SELECT * FROM system_events WHERE component=? ORDER BY id DESC LIMIT 1",
                 (component,), LEGACY_DB_PATH)
    return rows[0] if rows else {}


def _report_state(family):
    for path in (Path(f"data/informe_api_{family}.json"), Path(f"data/informe_apis_{family}.json"),
                 Path("data/informe_apis.json")):
        try:
            if path.exists():
                modified = datetime.fromtimestamp(path.stat().st_mtime, TZ).isoformat()
                data = json.loads(path.read_text(encoding="utf-8"))
                def values(node):
                    if isinstance(node, dict):
                        for key, value in node.items():
                            if str(key).lower() in {"ok", "status", "state", "estado", "result", "resultado", "error"}:
                                yield str(value or "").strip().upper()
                            yield from values(value)
                    elif isinstance(node, list):
                        for value in node:
                            yield from values(value)
                outcomes = [value for value in values(data) if value]
                bad = {"ERROR", "FAILED", "FAIL", "FALLA", "ROJO", "FALSE"}
                good = {"OK", "SUCCESS", "CORRECTO", "CORRECT", "VERDE", "TRUE", "HEALTHY"}
                explicit_bad = any(value in bad or value.startswith("ERROR:") for value in outcomes)
                explicit_good = any(value in good for value in outcomes)
                age = max(0, (datetime.now(TZ) - datetime.fromisoformat(modified)).total_seconds())
                if explicit_bad:
                    state, detail, success = "ROJO", "El verificador registró una falla explícita.", None
                elif explicit_good and age <= 86400:
                    state, detail, success = "VERDE", "Verificación correcta dentro de las últimas 24 horas.", modified
                elif explicit_good:
                    state, detail, success = "AMARILLO", "La última verificación correcta tiene más de 24 horas.", modified
                else:
                    state, detail, success = "GRIS", "Informe presente pero sin resultado concluyente.", None
                return state, f"{detail} Archivo: {path.name}", modified, success
        except Exception:
            continue
    return "GRIS", "Sin verificación persistida.", None, None


def health_page():
    persisted = {row["component"]: row for row in _rows("SELECT * FROM api_health")}
    state = snapshot()["state"]

    def paper(component, default_detail):
        row = persisted.get(component, {})
        return (row.get("state", "GRIS"), row.get("detail", default_detail),
                row.get("checked_at"), row.get("last_success_at"))

    sandbox = (_legacy_event("PPI_SANDBOX") or _legacy_event("PPI_AUTH_SANDBOX")
               or _legacy_event("PPI_AUTH"))
    sandbox_state = sandbox.get("state", "GRIS")
    sandbox_tuple = ("GRIS",
                     "No se usa en SIMULACIÓN PRODUCTIVA. Último estado histórico: " +
                     str(sandbox.get("detail") or sandbox_state or "sin prueba"),
                     sandbox.get("timestamp"), sandbox.get("timestamp") if sandbox_state == "OK" else None)
    gemini, telegram = _report_state("gemini"), _report_state("telegram")
    if MODE == "PRODUCTION_PAPER":
        gemini = ("GRIS", "Desactivado en esta versión paper; no se le atribuyen decisiones.",
                  gemini[2], gemini[3])
    market_data = paper("PPI_PRODUCTION_MARKETDATA", "Sin lectura de mercado reciente.")
    if state.get("session_state") == "MARKET_CLOSED":
        market_data = ("GRIS", "Rueda cerrada: no se espera cotización y no se consume market data.",
                       market_data[2], market_data[3])
    health_rows = (
        ("PPI Producción — autenticación", *paper("PPI_PRODUCTION_AUTH", "Todavía no se intentó login."), "Producción / simulación productiva"),
        ("PPI Producción — catálogo", *paper("PPI_PRODUCTION_CATALOG", "Todavía no se validó el universo."), "Instrumentos disponibles"),
        ("PPI Producción — históricos", *paper("PPI_PRODUCTION_HISTORY", "Todavía no se descargaron históricos."), "365 días / solo lectura"),
        ("PPI Producción — market data", *market_data, "Sólo con rueda abierta"),
        ("PPI Sandbox", *sandbox_tuple, "Sandbox"),
        ("Google Gemini", *gemini, "Motor de decisión / sombra"),
        ("Telegram", *telegram, "Notificaciones y control"),
        ("OPENBYMADATA", *paper("BYMA_OPEN_DATA", "Sin sonda pública persistida."), "Datos públicos oficiales"),
        ("BYMA — sitio institucional", *paper("BYMA_WEB", "Sin sonda pública persistida."), "Referencia oficial"),
        ("BYMA — API de instrumentos", *paper("BYMA_INSTRUMENTS_API", "Requiere alta de acceso."), "Catálogo oficial con acceso"),
        ("Data912", "GRIS", "Sin sonda independiente en este observador.", None, None, "Históricos alternativos"),
        ("InvertirOnline (IOL)", "GRIS", "Sin sonda independiente en este observador.", None, None, "Históricos alternativos"),
        ("BCRA / INDEC / ArgentinaDatos", "GRIS", "Estado disponible cuando corre el refresco macro.", None, None, "Contexto macro"),
        ("Yahoo Finance", "GRIS", "Fuente demorada; no se consulta desde esta pantalla.", None, None, "Respaldo de precios"),
        ("ROFEX / Primary", *paper("ROFEX_MARKETDATA",
          "Sin validación reciente. Se usa sólo como contexto; futuros no habilitados sin margen y multiplicador."),
         "Contexto de futuros; ejecución bloqueada"),
        ("SQLite operativa", "VERDE", "Base paper accesible; órdenes reales persistidas: 0.",
         state.get("heartbeat_at"), state.get("heartbeat_at"), "Persistencia local"),
    )
    rows = "".join(f"<tr><td><b>{_e(name)}</b></td><td>{_health_status(status)}</td><td>{_e(detail)}</td>"
                   f"<td>{_local_time(checked)}</td><td>{_local_time(success)}</td><td>{_e(use)}</td></tr>"
                   for name, status, detail, checked, success, use in health_rows)
    body = f"""<h1>Salud de APIs y fuentes</h1>
    <p class='paper-muted'>Inventario completo, independientemente del modo actual. Esta pantalla solo lee
    verificaciones persistidas: abrirla no consume logins ni cuota.</p>
    <div class='paper-card'><table class='paper-table'><tr><th>API / fuente</th><th>Estado</th><th>Detalle</th>
    <th>Último reporte</th><th>Último éxito</th><th>Uso</th></tr>{rows}</table></div>
    <div class='paper-notice'>Colores estándar: verde correcto · amarillo degradado o en espera · rojo falla · gris no probado o desactivado.</div>"""
    return _document("Salud de APIs", body, refresh=60)


def history_page():
    historical = {}
    if _table("market_historical_ohlcv", LEGACY_DB_PATH):
        rows = _rows("SELECT COUNT(DISTINCT symbol) instruments,COUNT(*) candles,MAX(date) newest FROM market_historical_ohlcv",
                     path=LEGACY_DB_PATH)
        historical = rows[0] if rows else {}
    last_ingest = _rows("SELECT * FROM ingest_runs ORDER BY run_id DESC LIMIT 1", path=LEGACY_DB_PATH) \
        if _table("ingest_runs", LEGACY_DB_PATH) else []
    syncs = _rows("SELECT * FROM source_sync ORDER BY source")
    catalog = _rows("SELECT instrument_type,COUNT(*) items,MAX(downloaded_at) downloaded_at FROM instrument_catalog GROUP BY instrument_type ORDER BY instrument_type")
    candidates = _rows("""SELECT * FROM candidate_universe ORDER BY can_simulate DESC,
      instrument_type,ticker""") if _table("candidate_universe") else []
    sync_rows = "".join(f"<tr><td><b>{_e(row['source'])}</b></td><td>{_status(row['status'])}</td>"
                        f"<td>{_local_time(row['last_attempt_at'])}</td><td>{_local_time(row['last_success_at'])}</td>"
                        f"<td>{_e(row['items'])}</td><td>{_e(row['detail'])}</td></tr>" for row in syncs) or \
                "<tr><td colspan='6'>Todavía no se ejecutó una sincronización.</td></tr>"
    catalog_rows = "".join(f"<tr><td>{_e(row['instrument_type'])}</td><td>{_e(row['items'])}</td>"
                           f"<td>{_local_time(row['downloaded_at'])}</td></tr>" for row in catalog) or \
                   "<tr><td colspan='3'>Catálogo todavía no descargado.</td></tr>"
    core = {"GGAL", "AL30", "AAPL"}
    candidate_rows = "".join(
        f"<tr><td><b>{_e(row['ticker'])}</b></td><td>{_e(row['instrument_type'])}</td>"
        f"<td>{_e(row['market'])}</td><td>{_health_status('VERDE' if row['status']=='AVAILABLE' else 'ROJO' if row['status']=='ERROR' else 'GRIS')}</td>"
        f"<td>{'NÚCLEO' if row['ticker'] in core else 'NUEVO CANDIDATO'}</td>"
        f"<td>{'SIMULACIÓN HABILITABLE' if row['can_simulate'] else 'SÓLO CONTEXTO'}</td>"
        f"<td>{_local_time(row['last_checked_at'])}</td></tr>" for row in candidates) or \
        "<tr><td colspan='7'>Esperando validación del universo contra PPI.</td></tr>"
    ingest = last_ingest[0] if last_ingest else {}
    body = f"""<h1>Datos históricos e instrumentos</h1>
    <div class='paper-grid'><div class='paper-card'>Instrumentos archivados<br><b class='metric'>{_e(historical.get('instruments',0))}</b></div>
    <div class='paper-card'>Velas archivadas<br><b class='metric'>{_e(historical.get('candles',0))}</b></div>
    <div class='paper-card'>Dato más reciente<br><b class='metric'>{_e(historical.get('newest'))}</b></div>
    <div class='paper-card'>Última ingesta<br><b class='metric'>{_local_time(ingest.get('finished_at'))}</b><br><span class='paper-muted'>{_e(ingest.get('source'))}</span></div></div>
    {_probe_form()}
    <div class='paper-card'><h2>Última conexión o bajada por fuente</h2><table class='paper-table'>
    <tr><th>Fuente</th><th>Estado</th><th>Último intento</th><th>Último éxito</th><th>Ítems</th><th>Detalle</th></tr>{sync_rows}</table></div>
    <div class='paper-card'><h2>Catálogo de instrumentos observado</h2><table class='paper-table'>
    <tr><th>Clase</th><th>Instrumentos</th><th>Descargado</th></tr>{catalog_rows}</table></div>
    <div class='paper-card'><h2>Universo ampliado validado</h2><table class='paper-table'>
    <tr><th>Ticker</th><th>Clase</th><th>Mercado</th><th>Disponible</th><th>Origen</th>
    <th>Uso seguro</th><th>Última validación</th></tr>{candidate_rows}</table></div>
    <div class='paper-notice'><b>BYMA:</b> OPENBYMADATA es consultable públicamente en su web. Las APIs oficiales
    de instrumentos/market data requieren alta o contratación; el sistema no usa endpoints ocultos. Mientras tanto,
    el catálogo y los históricos automatizados provienen de PPI Producción bajo barrera de solo lectura.
    <a href='https://open.bymadata.com.ar/' target='_blank' rel='noopener'>Abrir OPENBYMADATA</a>.</div>"""
    return _document("Datos históricos", body)


def _authorize(check_auth, request, token, authorization):
    try:
        check_auth(token, authorization, request.cookies.get("porota_dashboard_session"))
    except TypeError:
        check_auth(token, authorization)


def install(app, check_auth):
    global _installed
    if _installed or MODE not in MODE_INFO:
        return
    _installed = True

    def observacion(request: Request, token: str = Query(default=""),
                    authorization: str | None = Header(default=None)):
        _authorize(check_auth, request, token, authorization)
        return HTMLResponse(paper_page(compact=True))

    def motor(request: Request, token: str = Query(default=""),
              authorization: str | None = Header(default=None)):
        _authorize(check_auth, request, token, authorization)
        return HTMLResponse(motor_page())

    def observer_state(request: Request, token: str = Query(default=""),
                       authorization: str | None = Header(default=None)):
        _authorize(check_auth, request, token, authorization)
        return JSONResponse(snapshot())

    def login_readonly(request: Request, token: str = Query(default=""),
                       authorization: str | None = Header(default=None)):
        _authorize(check_auth, request, token, authorization)
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise HTTPException(status_code=403, detail="Origen no permitido.")
        _ensure_command_schema()
        with _conn() as connection:
            active = connection.execute("""SELECT id FROM observer_commands
                WHERE command='LOGIN_AND_SYNC' AND status IN ('QUEUED','RUNNING')
                ORDER BY id DESC LIMIT 1""").fetchone()
            if not active:
                connection.execute("INSERT INTO observer_commands(created_at,command,status) VALUES(?,?,?)",
                                   (datetime.now(TZ).isoformat(timespec="seconds"), "LOGIN_AND_SYNC", "QUEUED"))
        return RedirectResponse("/observacion", status_code=303)

    app.add_api_route("/observacion", observacion, methods=["GET"], response_class=HTMLResponse)
    app.add_api_route("/motor-trading", motor, methods=["GET"], response_class=HTMLResponse)
    app.add_api_route("/api/observer/state", observer_state, methods=["GET"])
    app.add_api_route("/api/paper/login-readonly", login_readonly, methods=["POST"])

    @app.middleware("http")
    async def paper_mode_truth(request, call_next):
        response = await call_next(request)
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type or response.status_code >= 400:
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])
        content = body.decode("utf-8", "replace")
        if request.url.path == "/testing" and MODE == "PRODUCTION_PAPER":
            content = paper_page(compact=True)
        elif request.url.path == "/salud":
            content = health_page()
        elif request.url.path == "/historicos":
            content = history_page()
        else:
            content = _canonicalize(content, request.url.path)
        headers = dict(response.headers)
        headers.pop("content-length", None)
        return HTMLResponse(content, status_code=response.status_code, headers=headers)
