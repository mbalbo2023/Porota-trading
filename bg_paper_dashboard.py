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
PAPER_ACTIVE_SYMBOL_LIMIT = os.getenv("PAPER_ACTIVE_SYMBOL_LIMIT", "20")
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
.paper-refresh{color:var(--muted);font-size:.78rem;text-align:right;margin:0 0 8px}
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
        raw = f"{float(value):,.2f}"
        return "$ " + raw.replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "—"


def _pct(value):
    try:
        return f"{float(value):.2f}".replace(".", ",") + " %"
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
        ("/", "Panel"), ("/motor-trading", "Motor de trading"), ("/salud", "Salud de APIs"),
        ("/historicos", "Históricos"), ("/aprendizaje", "Blog de aprendizaje"),
        ("/telegram", "Telegram"), ("/sre", "SRE"), ("/dashboard/logs", "Logs"),
        ("/config", "Configuración"),
    )
    items = "".join(f"<a href='{href}'>{label}</a>" for href, label in links)
    return ("<nav id='porota-canonical-nav'><button type='button' "
            "onclick=\"if(history.length>1){history.back()}else{location.href='/'}\">← Volver</button>"
            + items + "</nav>")


def _document(title, body, refresh=None):
    seconds = int(refresh or 60)
    refreshed = datetime.now(TZ).strftime("%d/%m/%Y %H:%M:%S")
    refresh_bar = (f"<div class='paper-refresh'>Actualizado: {refreshed} · "
                   f"Próxima actualización: <b id='porota-refresh-count'>{seconds}</b> s</div>")
    refresh_script = f"""<script>(function(){{let left={seconds};let out=document.getElementById('porota-refresh-count');
    setInterval(function(){{if(document.querySelector('details[open]')){{out.textContent='pausada';return;}}
    left-=1;if(left<=0){{location.reload();return;}}out.textContent=left;}},1000);}})();</script>"""
    return ("<!doctype html><html lang='es'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{_e(title)}</title>{THEME}</head><body>{_nav()}{mode_banner()}"
            f"<main class='paper-page'>{refresh_bar}{body}</main>{refresh_script}</body></html>")


def _legacy_refresh_controls(seconds=60):
    refreshed = datetime.now(TZ).strftime("%d/%m/%Y %H:%M:%S")
    return (f"<div class='paper-refresh'>Actualizado: {refreshed} · Próxima actualización: "
            f"<b id='porota-refresh-count'>{seconds}</b> s</div>"
            f"<script>(function(){{let n={seconds},o=document.getElementById('porota-refresh-count');"
            "setInterval(function(){if(document.querySelector('details[open]')){o.textContent='pausada';return;}"
            "n-=1;if(n<=0){location.reload();return;}o.textContent=n;},1000);})();</script>")


def _canonicalize(content, path=""):
    """Deja un solo menú, un solo banner y un solo tema en cualquier página."""
    if (content.count("id='porota-canonical-nav'") == 1 and
            content.count("id='porota-paper-mode'") == 1 and
            content.count("id='porota-paper-theme'") == 1 and
            "porota-top-nav" not in content and
            not re.search(r"<div[^>]+class=['\"]nav['\"]", content, flags=re.I)):
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
    known = ("/vivo", "/testing", "/salud", "/historicos", "/aprendizaje",
             "/telegram", "/sre", "/dashboard/logs", "/config")
    def strip_duplicate_links(match):
        block = match.group(0)
        matches = sum(f"href='{route}" in block or f'href="{route}' in block
                      for route in known)
        return "" if matches >= 3 else block
    content = re.sub(r"<(?:p|div)[^>]*>.*?</(?:p|div)>", strip_duplicate_links,
                     content, flags=re.I | re.S)
    if "</head>" in content.lower():
        content = re.sub(r"</head>", THEME + "</head>", content, count=1, flags=re.I)
    body = re.search(r"<body[^>]*>", content, flags=re.I)
    closing = re.search(r"</body>", content, flags=re.I)
    if body and closing:
        inner = content[body.end():closing.start()]
        if "id='porota-legacy-shell'" not in inner:
            inner = (f"<main id='porota-legacy-shell' class='paper-page legacy-shell'>"
                     f"{_legacy_refresh_controls()}{inner}</main>")
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
    events = _rows("SELECT * FROM paper_events ORDER BY id DESC LIMIT 50")
    equity_rows = _rows("SELECT * FROM paper_equity ORDER BY id DESC LIMIT 1")
    samples = _rows("SELECT COUNT(*) total,SUM(CASE WHEN label_timestamp IS NOT NULL THEN 1 ELSE 0 END) labeled FROM paper_learning_samples")
    commands = _rows("SELECT * FROM observer_commands ORDER BY id DESC LIMIT 1")
    active = _rows("SELECT * FROM active_instruments ORDER BY role,ticker") \
        if _table("active_instruments") else []
    quarantined = _rows("""SELECT * FROM instrument_runtime WHERE quarantined_until IS NOT NULL
      AND quarantined_until>datetime('now') ORDER BY quarantined_until""") \
        if _table("instrument_runtime") else []
    rotation_rows = _rows("SELECT * FROM observer_rotation WHERE id=1") \
        if _table("observer_rotation") else []
    eligible_rows = _rows("""SELECT COUNT(*) total FROM candidate_universe
      WHERE can_simulate=1 AND status='AVAILABLE'""") if _table("candidate_universe") else []
    initial = float(PAPER_INITIAL_CAPITAL)
    today = datetime.now(TZ).date()
    closed_all = _rows("SELECT * FROM paper_positions WHERE status='CLOSED' ORDER BY closed_at DESC LIMIT 1000")
    closed_today = []
    for row in closed_all:
        try:
            parsed = datetime.fromisoformat(str(row.get("closed_at")))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=TZ)
            if parsed.astimezone(TZ).date() == today:
                closed_today.append(row)
        except Exception:
            pass
    wins_all = sum(1 for row in closed_all if float(row.get("net_pnl") or 0) > 0)
    wins_today = sum(1 for row in closed_today if float(row.get("net_pnl") or 0) > 0)
    realized_today = sum(float(row.get("net_pnl") or 0) for row in closed_today)
    unrealized = float((equity_rows[0] if equity_rows else {}).get("unrealized_pnl") or 0)
    day_pnl = realized_today + unrealized
    committed = sum(float(row.get("entry_price") or 0) * float(row.get("quantity") or 0) +
                    float(row.get("entry_cost") or 0) for row in opened)
    stats = {"initial": initial, "day_pnl": day_pnl,
             "day_pct": (day_pnl / initial * 100) if initial else 0,
             "realized_today": realized_today, "unrealized": unrealized,
             "committed": committed, "wins_today": wins_today,
             "closed_today": len(closed_today), "wins_all": wins_all,
             "closed_all": len(closed_all),
             "win_rate_today": (wins_today / len(closed_today) * 100) if closed_today else None,
             "win_rate_all": (wins_all / len(closed_all) * 100) if closed_all else None}
    return {"state": state, "quotes": quotes, "open": opened, "closed": closed,
            "decisions": decisions, "events": events,
            "equity": equity_rows[0] if equity_rows else {},
            "learning": samples[0] if samples else {}, "command": commands[0] if commands else {},
            "active": active, "quarantined": quarantined,
            "rotation": rotation_rows[0] if rotation_rows else {},
            "eligible": eligible_rows[0].get("total", 0) if eligible_rows else 0,
            "stats": stats}


def _trend(value):
    number = float(value or 0)
    if number > 0:
        return "verdict-ok", "↑"
    if number < 0:
        return "verdict-bad", "↓"
    return "verdict-pending", "→"


def _overview_body():
    data = snapshot()
    state, equity, stats = data["state"], data["equity"], data["stats"]
    trend_class, arrow = _trend(stats["day_pnl"])
    decisions = "".join(
        f"<tr><td>{_local_time(row['decided_at'])}</td><td><b>{_e(row['symbol'])}</b></td>"
        f"<td>{_status(row['action'])}</td><td>{float(row.get('score') or 0):.3f}</td>"
        f"<td>{_e(row['reason'])}</td></tr>" for row in data["decisions"][:20]
    ) or "<tr><td colspan='5'>Esperando datos para la primera evaluación.</td></tr>"
    marks = {row["symbol"]: float(row.get("bid") or row.get("last") or 0)
             for row in data["quotes"]}
    position_rows = []
    for row in data["open"]:
        entry, quantity = float(row["entry_price"]), float(row["quantity"])
        committed = entry * quantity + float(row["entry_cost"])
        mark = marks.get(row["symbol"]) or entry
        pnl = (mark - entry) * quantity - float(row["entry_cost"])
        pnl_pct = pnl / committed * 100 if committed else 0
        trend, arrow_row = _trend(pnl)
        position_rows.append(
            f"<tr><td>{_local_time(row['opened_at'])}</td><td><b>{_e(row['symbol'])}</b></td>"
            f"<td>{_e(row['quantity'])}</td><td>{_money(row['entry_price'])}</td>"
            f"<td><b>{_money(committed)}</b></td><td>{_money(mark)}</td>"
            f"<td class='{trend}'><b>{arrow_row} {_money(pnl)} · {_pct(pnl_pct)}</b></td>"
            f"<td>Capital ficticio paper</td></tr>")
    positions = "".join(position_rows) or "<tr><td colspan='8'>Sin posiciones simuladas abiertas.</td></tr>"
    quality = [row for row in data["events"]
               if row.get("event_type") in {"DATA_ERROR", "DATA_REJECTED", "INSTRUMENT_QUARANTINED"}]
    quality_rows = "".join(
        f"<tr><td>{_local_time(row.get('event_at'))}</td><td>{_status('AMARILLO')}</td>"
        f"<td>{_e(row.get('detail'))}</td></tr>" for row in quality[:12]
    ) or "<tr><td colspan='3'>Sin errores de datos recientes.</td></tr>"
    return f"""<h1>Panel de simulación productiva</h1>
    <p class='paper-muted'>Una sola vista para estado, actividad y simulación. Datos reales de PPI;
    patrimonio y operaciones 100 % ficticios; órdenes reales: NINGUNA.</p>
    <div class='paper-grid'>
      <div class='paper-card'>Motor paper<br><b class='metric'>{_e(state.get('process_state'))}</b><br><span class='paper-muted'>{_e(state.get('detail'))}</span></div>
      <div class='paper-card'>Sesión BYMA<br><b class='metric'>{_e(state.get('session_state'))}</b><br><span class='paper-muted'>Latido {_local_time(state.get('heartbeat_at'))}</span></div>
      <div class='paper-card'>PPI Producción<br><b class='metric'>{_e(state.get('ppi_auth'))}</b><br><span class='paper-muted'>Sólo lectura · último dato {_local_time(state.get('last_market_data_at'))}</span></div>
      <div class='paper-card'>Patrimonio paper<br><b class='metric'>{_money(equity.get('equity') or PAPER_INITIAL_CAPITAL)}</b><br><span class='paper-muted'>Capital inicial {_money(PAPER_INITIAL_CAPITAL)}</span></div>
      <div class='paper-card'>Resultado de hoy<br><b class='metric {trend_class}'>{arrow} {_money(stats['day_pnl'])} · {_pct(stats['day_pct'])}</b><br><span class='paper-muted'>Realizado {_money(stats['realized_today'])} · no realizado {_money(stats['unrealized'])}</span></div>
      <div class='paper-card'>Capital comprometido<br><b class='metric'>{_money(stats['committed'])}</b><br><span class='paper-muted'>{len(data['open'])} posiciones paper abiertas</span></div>
      <div class='paper-card'>Win rate de hoy<br><b class='metric'>{_pct(stats['win_rate_today'])}</b><br><span class='paper-muted'>{stats['wins_today']}/{stats['closed_today']} cierres ganadores</span></div>
      <div class='paper-card'>Win rate acumulado<br><b class='metric'>{_pct(stats['win_rate_all'])}</b><br><span class='paper-muted'>{stats['wins_all']}/{stats['closed_all']} cierres ganadores</span></div>
      <div class='paper-card'>Universo del ciclo<br><b class='metric'>{len(data['active'])}/{_e(data['eligible'])}</b><br><span class='paper-muted'>{len(data['quarantined'])} en pausa automática</span></div>
      <div class='paper-card'>Órdenes reales<br><b class='metric'>0</b><br><span class='paper-muted'>Bloqueadas por transporte</span></div>
    </div>
    <div class='paper-card'><h2>Dinero invertido ahora — simulación</h2><table class='paper-table'>
    <tr><th>Hora</th><th>Instrumento</th><th>Cantidad</th><th>Entrada</th><th>Importe comprometido</th><th>Precio actual</th><th>PnL estimado</th><th>Origen</th></tr>{positions}</table></div>
    <div class='paper-card'><h2>Decisiones recientes</h2><p class='paper-muted'>HOLD significa que fue evaluado y descartado. APPROVE de Gemini no equivale por sí solo a una posición abierta.</p>
    <table class='paper-table'><tr><th>Hora</th><th>Instrumento</th><th>Acción</th><th>Score</th><th>Motivo</th></tr>{decisions}</table></div>
    <div class='paper-card'><h2>Calidad de datos y cuarentena</h2>
    <p class='paper-muted'>Una respuesta vacía o no JSON no se interpreta como precio. Tras fallas consecutivas,
    el instrumento se pausa temporalmente y la cohorte rotativa ocupa su lugar.</p>
    <table class='paper-table'><tr><th>Hora</th><th>Estado</th><th>Detalle</th></tr>{quality_rows}</table></div>"""


def home_page():
    return _document("Porota Trading", _overview_body(), refresh=30)


def logs_page():
    log_file = Path(os.getenv("LOG_DIR", "data/logs")) / "trading_bot.log"
    size = f"{log_file.stat().st_size / 1024:.1f} KB" if log_file.exists() else "todavía no creado"
    categories = ("all", "critical", "trading", "system", "ia_fallback", "ppi",
                  "telegram", "sre")
    rows = "".join(
        f"<tr><td>{_e('COMPLETO' if category=='all' else category.upper())}</td>"
        f"<td><a class='paper-action' href='/api/logs/download/{category}'>Descargar</a></td></tr>"
        for category in categories)
    body = f"""<h1>Gestión de logs</h1><p class='paper-muted'>Vista unificada con descargas saneadas.</p>
    <div class='paper-grid'><div class='paper-card'>Archivo operativo<br><b class='metric'>{_e(size)}</b><br>
    <span class='paper-muted'>{_e(log_file)}</span></div>
    <div class='paper-card'>Diagnóstico integral<br><a class='paper-action' href='/api/diagnostics/download'>Descargar ZIP</a></div></div>
    <div class='paper-card'><table class='paper-table'><tr><th>Categoría</th><th>Acción</th></tr>{rows}</table></div>"""
    return _document("Gestión de logs", body)


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
    active_core = sum(1 for row in data["active"] if row.get("role") == "NUCLEO")
    active_rotating = sum(1 for row in data["active"] if row.get("role") == "ROTATIVO")
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
      <div class='paper-card'>Universo rotativo<br><b class='metric'>{len(data['active'])}/{_e(data['eligible'])}</b><br><span class='paper-muted'>{active_core} núcleo · {active_rotating} rotativos · {len(data['quarantined'])} en pausa</span></div>
    </div>
    <div class='paper-notice'><b>Política patrimonial paper:</b> riesgo máximo {float(PAPER_RISK_PCT)*100:.1f}% por operación;
    tope {float(PAPER_MAX_POSITION_PCT)*100:.0f}% por posición; exposición total máxima
    {float(PAPER_MAX_TOTAL_EXPOSURE_PCT)*100:.0f}%; hasta 3 posiciones. Este capital es ficticio y nunca se
    confunde con el saldo real de tu cuenta.</div>
    <div class='paper-notice'><a href='/motor-trading'><b>Abrir Motor de trading</b></a> para ver el razonamiento de cada operación, sus variables y su resultado.</div>
    <div class='paper-notice'><b>Sincronización automática:</b> catálogo e históricos se actualizan
    en segundo plano una vez por jornada; no requiere intervención del usuario.</div><p class='paper-muted'>{command_text}</p>
    <div class='paper-card'><h2>Cotizaciones reales observadas</h2><table class='paper-table'>
      <tr><th>Instrumento</th><th>Último</th><th>Bid</th><th>Ask</th><th>Último dato</th></tr>{quotes}</table></div>
    <div class='paper-card'><h2>Posiciones abiertas — todas simuladas</h2><table class='paper-table'>
      <tr><th>ID paper</th><th>Instrumento</th><th>Cantidad</th><th>Entrada</th><th>Stop</th><th>Objetivo</th></tr>{opened}</table></div>
    <div class='paper-card'><h2>Últimas decisiones</h2><table class='paper-table'>
      <tr><th>Hora</th><th>Instrumento</th><th>Acción</th><th>Score</th><th>Motivo</th></tr>{decisions}</table></div>"""
    return _document("Simulación productiva", body, refresh=30) if compact else body


def learning_page():
    data = snapshot()
    stats = data["stats"]
    samples = _rows("""SELECT l.*,p.symbol,p.opened_at,p.closed_at,p.net_pnl,p.close_reason
      FROM paper_learning_samples l JOIN paper_positions p ON p.paper_id=l.paper_id
      ORDER BY p.opened_at DESC LIMIT 100""") if _table("paper_learning_samples") else []
    sample_rows = "".join(
        f"<tr><td>{_local_time(row.get('opened_at'))}</td><td><b>{_e(row.get('symbol'))}</b></td>"
        f"<td>{_e(row.get('outcome') or 'PENDIENTE')}</td><td>{_money(row.get('net_pnl'))}</td>"
        f"<td>{_pct(float(row.get('net_return_pct') or 0)) if row.get('net_return_pct') is not None else '—'}</td>"
        f"<td>{_e(row.get('duration_minutes'))}</td><td>{_e(row.get('close_reason'))}</td></tr>"
        for row in samples
    ) or "<tr><td colspan='7'>Todavía no hay operaciones paper etiquetadas.</td></tr>"
    current_threshold = "0,62"
    decisions = data.get("decisions") or []
    if decisions:
        value = _features(decisions[0].get("features_json")).get("paper_threshold")
        if value not in (None, ""):
            current_threshold = str(value).replace(".", ",")
    body = f"""<h1>Aprendizaje del sistema</h1>
    <p class='paper-muted'>Muestra observable de decisiones y resultados paper. Nunca mezcla el capital
    ficticio con el saldo real de la cuenta.</p>
    <div class='paper-grid'>
      <div class='paper-card'>Muestras cerradas<br><b class='metric'>{_e(data['learning'].get('labeled',0))}/{_e(data['learning'].get('total',0))}</b></div>
      <div class='paper-card'>Win rate de hoy<br><b class='metric'>{_pct(stats['win_rate_today'])}</b><br><span class='paper-muted'>{stats['wins_today']}/{stats['closed_today']} cierres</span></div>
      <div class='paper-card'>Win rate acumulado<br><b class='metric'>{_pct(stats['win_rate_all'])}</b><br><span class='paper-muted'>{stats['wins_all']}/{stats['closed_all']} cierres</span></div>
      <div class='paper-card'>Umbral técnico vigente<br><b class='metric'>{_e(current_threshold)}</b><br><span class='paper-muted'>se adapta sólo con muestra cerrada suficiente</span></div>
      <div class='paper-card'>Resultado de hoy<br><b class='metric'>{_money(stats['day_pnl'])}</b><br><span class='paper-muted'>{_pct(stats['day_pct'])}</span></div>
    </div>
    <div class='paper-notice'><b>Cómo aprende:</b> cada compra simulada conserva variables, IA, precio,
    costos y resultado. Al cerrar se etiqueta ganadora o perdedora. Con menos de cinco cierres se mantiene
    el umbral conservador 0,62; después puede endurecerse o relajarse según el win rate reciente.</div>
    <div class='paper-card'><h2>Resultados que alimentan el aprendizaje</h2><table class='paper-table'>
    <tr><th>Apertura</th><th>Instrumento</th><th>Etiqueta</th><th>PnL neto</th><th>Retorno</th><th>Minutos</th><th>Motivo</th></tr>
    {sample_rows}</table></div>"""
    return _document("Aprendizaje", body, refresh=60)


def _status(value):
    raw = str(value or "GRIS").upper()
    if raw in {"VERDE", "OK", "RUNNING", "CONNECTED", "BUY", "BUY_SIMULATED", "APPROVE", "APPROVED"}:
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


def _win_rate_before(opened_at):
    rows = _rows("""SELECT net_pnl FROM paper_positions WHERE status='CLOSED'
      AND closed_at<? ORDER BY closed_at""", (opened_at,))
    if not rows:
        return "Sin muestra cerrada previa"
    wins = sum(1 for row in rows if float(row.get("net_pnl") or 0) > 0)
    return f"{_pct(wins / len(rows) * 100)} ({wins}/{len(rows)})"


def _ai_execution(row):
    positions = _rows("""SELECT paper_id,status FROM paper_positions
      WHERE symbol=? AND opened_at=? LIMIT 1""", (row.get("symbol"), row.get("evaluated_at")))
    if positions:
        return f"POSICIÓN PAPER {positions[0]['status']} · {positions[0]['paper_id']}"
    if str(row.get("decision")).upper() == "APPROVE":
        return "APROBACIÓN IA SIN APERTURA: otro portón patrimonial o de liquidez la bloqueó"
    return "SIN APERTURA: la IA se abstuvo o vetó"


def _features(value):
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _verdict(position):
    if position.get("status") == "CANCELLED":
        return "verdict-pending", "CANCELADA", "La simulación se cerró de forma neutral durante un despliegue; no etiqueta acierto ni error."
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
            ai_text = (f"Participó como portón crítico. Resultado: {_e(ai.get('decision'))}; "
                       f"score {_e(ai.get('score'))}; fundamento: {_e(ai.get('reason'))}.")
        else:
            ai_text = ("Esta operación histórica no tiene evaluación Gemini asociada. En la versión actual, "
                       "una nueva compra paper queda bloqueada si Gemini no participa y aprueba.")
        cards.append(f"""<details class='paper-trade'>
          <summary>{_e(position['symbol'])} · {_e(position['status'])} · {_local_time(position['opened_at'])} ·
          PnL {_money(position.get('net_pnl'))}</summary><div class='trade-body'>
          <div class='paper-grid'>
            <div><b>Operación</b><br>{_e(position['paper_id'])}<br>{_e(position['quantity'])} unidades</div>
            <div><b>Entrada simulada</b><br>{_money(position['entry_price'])}<br>costo {_money(position['entry_cost'])}</div>
            <div><b>Importe comprometido</b><br>{_money(float(position['entry_price'])*float(position['quantity'])+float(position['entry_cost']))}<br>capital ficticio paper</div>
            <div><b>Salida simulada</b><br>{_money(position.get('exit_price'))}<br>{_e(position.get('close_reason'))}</div>
            <div><b class='{verdict_class}'>{verdict}</b><br>{_e(verdict_detail)}</div>
            <div><b>Win rate al abrir</b><br>{_win_rate_before(position['opened_at'])}</div>
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
    ai_only = _rows("SELECT * FROM ai_shadow_evaluations ORDER BY id DESC LIMIT 30") \
        if _table("ai_shadow_evaluations") else []
    ai_rows = "".join(
        f"<tr><td>{_local_time(row['evaluated_at'])}</td><td>{_e(row['symbol'])}</td>"
        f"<td>{_status(row['decision'])}</td><td>{_e(row['model'])}</td><td>{_e(row['reason'])}</td>"
        f"<td>{_e(_ai_execution(row))}</td></tr>"
        for row in ai_only
    ) or "<tr><td colspan='6'>Todavía no existen evaluaciones Gemini.</td></tr>"
    model_rows = _rows("""SELECT model,COUNT(*) evaluations,
      SUM(CASE WHEN decision='APPROVE' THEN 1 ELSE 0 END) approvals,
      SUM(CASE WHEN decision IN ('HOLD','VETO') THEN 1 ELSE 0 END) abstentions,
      MAX(evaluated_at) last_used FROM ai_shadow_evaluations GROUP BY model ORDER BY last_used DESC""") \
      if _table("ai_shadow_evaluations") else []
    model_table = "".join(
        f"<tr><td><b>{_e(row['model'])}</b></td><td>{_e(row['evaluations'])}</td>"
        f"<td>{_e(row['approvals'])}</td><td>{_e(row['abstentions'])}</td>"
        f"<td>{_local_time(row['last_used'])}</td></tr>" for row in model_rows
    ) or "<tr><td colspan='5'>Todavía no hay muestra para comparar modelos.</td></tr>"
    decisions = _rows("SELECT * FROM paper_decisions ORDER BY id DESC LIMIT 60")
    decision_rows = "".join(
        f"<tr><td>{_local_time(row['decided_at'])}</td><td><b>{_e(row['symbol'])}</b></td>"
        f"<td>{_status(row['action'])}</td><td>{float(row.get('score') or 0):.3f}</td>"
        f"<td>{_e(_features(row.get('features_json')).get('paper_threshold'))}</td>"
        f"<td>{_e(row['reason'])}</td></tr>" for row in decisions
    ) or "<tr><td colspan='6'>Esperando las primeras evaluaciones paper.</td></tr>"
    content = "".join(cards) or """<div class='paper-card'><h2>Todavía no existen operaciones simuladas</h2>
      <p>El motor empezará a registrar cada procedimiento cuando reúna datos suficientes y una señal supere
      los filtros. Las abstenciones siguen visibles en la página de Simulación.</p></div>"""
    body = f"""<h1>Motor de trading</h1><p class='paper-muted'>Trazabilidad operación por operación.
    Seleccioná una fila para desplegar el procedimiento completo, las variables y el resultado.</p>
    <div class='paper-warning'><b>Todas las operaciones de esta página son simuladas.</b>
    Nunca representan una orden enviada a PPI.</div>
    <div class='paper-card'><h2>Decisiones y abstenciones actuales</h2>
    <p class='paper-muted'>HOLD significa que el motor sí evaluó el instrumento y decidió no abrir. Gemini se consulta
    únicamente cuando los filtros técnicos producen BUY; por eso puede no aparecer en una abstención.</p>
    <table class='paper-table'><tr><th>Hora</th><th>Instrumento</th><th>Acción</th><th>Score</th>
    <th>Umbral</th><th>Motivo</th></tr>{decision_rows}</table></div>{content}
    <div class='paper-card'><h2>Últimos veredictos de Gemini</h2><table class='paper-table'>
    <tr><th>Hora</th><th>Instrumento</th><th>Decisión</th><th>Modelo</th><th>Fundamento</th><th>Efecto real en paper</th></tr>
    {ai_rows}</table></div>
    <div class='paper-card'><h2>Benchmark histórico de modelos IA</h2><table class='paper-table'>
    <tr><th>Modelo</th><th>Evaluaciones</th><th>Aprobaciones</th><th>Abstenciones/vetos</th><th>Último uso</th></tr>{model_table}</table>
    <p class='paper-muted'>El modelo operativo queda fijado en Gemini 3.7 Flash estable. Gemini 3.1 Pro Preview
    no se intercala durante la rueda: podrá evaluarse en sombra cuando exista una muestra comparable, sin decidir operaciones.</p></div>"""
    return _document("Motor de trading", body, refresh=30)


def live_page():
    """Actividad veraz del observador paper; nunca mezcla el motor legacy detenido."""
    data = snapshot()
    state = data["state"]
    state_label = {"RUNNING": "EVALUANDO", "WAITING_MARKET": "EN ESPERA",
                   "READY_PREOPEN": "PREAPERTURA", "DEGRADED": "DEGRADADO",
                   "STARTING": "INICIANDO"}.get(str(state.get("process_state")),
                                                 str(state.get("process_state")))
    position_rows = "".join(
        f"<tr><td>{_local_time(p['opened_at'])}</td><td><b>{_e(p['symbol'])}</b></td>"
        f"<td>{_e(p['quantity'])}</td><td>{_money(p['entry_price'])}</td>"
        f"<td>{_money(p['stop_price'])}</td><td>{_money(p['target_price'])}</td></tr>"
        for p in data["open"]
    ) or "<tr><td colspan='6'>Sin posiciones simuladas abiertas.</td></tr>"
    decision_rows = "".join(
        f"<tr><td>{_local_time(row['decided_at'])}</td><td><b>{_e(row['symbol'])}</b></td>"
        f"<td>{_status(row['action'])}</td><td>{float(row.get('score') or 0):.3f}</td>"
        f"<td>{_e(row['reason'])}</td></tr>" for row in data["decisions"]
    ) or "<tr><td colspan='5'>Esperando datos para la primera evaluación.</td></tr>"
    active_rows = "".join(
        f"<tr><td><b>{_e(row['ticker'])}</b></td><td>{_e(row['instrument_type'])}</td>"
        f"<td>{_e(row['role'])}</td><td>{_local_time(row['selected_at'])}</td></tr>"
        for row in data["active"]
    ) or "<tr><td colspan='4'>La cohorte activa se publicará al comenzar el próximo ciclo.</td></tr>"
    body = f"""<h1>Actividad en vivo</h1>
    <div class='paper-notice'><b>Motor real:</b> desactivado intencionalmente. <b>Observador paper:</b>
    { _e(state_label) }. Lee PPI Producción y simula decisiones; órdenes reales: NINGUNA.</div>
    <div class='paper-grid'>
      <div class='paper-card'>Estado del observador<br><b class='metric'>{_e(state_label)}</b><br><span class='paper-muted'>{_e(state.get('detail'))}</span></div>
      <div class='paper-card'>Rueda BYMA<br><b class='metric'>{_e(state.get('session_state'))}</b><br><span class='paper-muted'>Latido {_local_time(state.get('heartbeat_at'))}</span></div>
      <div class='paper-card'>PPI solo lectura<br><b class='metric'>{_e(state.get('ppi_auth'))}</b><br><span class='paper-muted'>Último dato {_local_time(state.get('last_market_data_at'))}</span></div>
      <div class='paper-card'>Universo del ciclo<br><b class='metric'>{len(data['active'])}/{_e(data['eligible'])}</b><br><span class='paper-muted'>{len(data['quarantined'])} instrumentos en pausa automática</span></div>
      <div class='paper-card'>Órdenes reales<br><b class='metric'>0</b><br><span class='paper-muted'>Transporte bloqueado</span></div>
    </div>
    <div class='paper-card'><h2>Posiciones paper abiertas</h2><table class='paper-table'>
    <tr><th>Hora</th><th>Instrumento</th><th>Cantidad</th><th>Entrada</th><th>Stop</th><th>Objetivo</th></tr>{position_rows}</table></div>
    <div class='paper-card'><h2>Evaluaciones paper recientes</h2><p class='paper-muted'>Cada fila incluye
    fecha y hora. HOLD es una decisión de abstención, no un instrumento ignorado.</p><table class='paper-table'>
    <tr><th>Hora</th><th>Instrumento</th><th>Acción</th><th>Score</th><th>Explicación</th></tr>{decision_rows}</table></div>
    <div class='paper-card'><h2>Instrumentos del ciclo actual</h2><table class='paper-table'>
    <tr><th>Instrumento</th><th>Clase</th><th>Rol</th><th>Seleccionado</th></tr>{active_rows}</table></div>"""
    return _document("Actividad en vivo", body, refresh=30)


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

    sandbox_tuple = paper(
        "PPI_SANDBOX_REACHABILITY",
        "Sin sonda de conectividad reciente. No se ejecutan credenciales Sandbox en modo productivo paper.",
    )
    telegram = _report_state("telegram")
    if _table("observer_notifications"):
        delivered = _rows("""SELECT attempted_at,status,detail FROM observer_notifications
          WHERE status='ENTREGADO' ORDER BY attempted_at DESC LIMIT 1""")
        attempted = _rows("""SELECT attempted_at,status,detail FROM observer_notifications
          ORDER BY attempted_at DESC LIMIT 1""")
        if delivered:
            telegram = ("VERDE", "Último aviso operativo paper entregado correctamente.",
                        attempted[0]["attempted_at"] if attempted else delivered[0]["attempted_at"],
                        delivered[0]["attempted_at"])
        elif attempted:
            telegram = ("ROJO", "El último aviso paper no pudo entregarse: " +
                        str(attempted[0].get("detail") or attempted[0].get("status")),
                        attempted[0]["attempted_at"], None)
    gemini = paper("GEMINI_DECISION", "Sin verificación del portón crítico Gemini.")
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
        ("Google Gemini", *gemini, "Portón crítico de cada compra simulada"),
        ("Telegram", *telegram, "Notificaciones y control"),
        ("OPENBYMADATA", *paper("BYMA_OPEN_DATA", "Sin sonda pública persistida."), "Datos públicos oficiales"),
        ("BYMA — sitio institucional", *paper("BYMA_WEB", "Sin sonda pública persistida."), "Referencia oficial"),
        ("BYMA — API de instrumentos", *paper("BYMA_INSTRUMENTS_API", "Requiere alta de acceso."), "Catálogo oficial con acceso"),
        ("BYMA — calendario operativo", *paper("BYMA_CALENDAR", "Sin validación persistida."), "Horarios, feriados y portón de rueda"),
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
    eligible = sum(1 for row in candidates if row.get("can_simulate") and row.get("status") == "AVAILABLE")
    catalog_total = sum(int(row.get("items") or 0) for row in catalog)
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
    <div class='paper-card'>Catálogo PPI observado<br><b class='metric'>{catalog_total}</b><br><span class='paper-muted'>{eligible} habilitables para paper</span></div>
    <div class='paper-card'>Escaneo por ciclo<br><b class='metric'>hasta {_e(PAPER_ACTIVE_SYMBOL_LIMIT)}</b><br><span class='paper-muted'>tope para controlar cuota y memoria</span></div>
    <div class='paper-card'>Última ingesta<br><b class='metric'>{_local_time(ingest.get('finished_at'))}</b><br><span class='paper-muted'>{_e(ingest.get('source'))}</span></div></div>
    <div class='paper-notice'><b>Actualización automática:</b> el observador sincroniza catálogo e históricos
    una vez por jornada en preapertura o al iniciar la rueda. Reiniciarlo no duplica la ingesta y esta pantalla
    no ofrece botones que consuman cuota del bróker.</div>
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
        return HTMLResponse(home_page())

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
            content = home_page()
        elif request.url.path == "/vivo" and MODE == "PRODUCTION_PAPER":
            content = home_page()
        elif request.url.path == "/salud":
            content = health_page()
        elif request.url.path == "/historicos":
            content = history_page()
        elif request.url.path == "/aprendizaje":
            content = learning_page()
        elif request.url.path == "/":
            content = home_page()
        elif request.url.path == "/dashboard/logs":
            content = logs_page()
        else:
            content = _canonicalize(content, request.url.path)
        headers = dict(response.headers)
        headers.pop("content-length", None)
        return HTMLResponse(content, status_code=response.status_code, headers=headers)
