"""Vista segura del modo PRODUCTION_PAPER para el dashboard existente."""

from __future__ import annotations

import html
import json
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import Header, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse


DB_PATH = os.getenv("PAPER_DB_PATH", "data/observer/observer_production.db")
MODE = os.getenv("DASHBOARD_OPERATION_MODE", "DETENIDO").upper()
TZ = ZoneInfo(os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires"))
_installed = False

THEME = """
<style id='porota-paper-theme'>
.paper-banner{background:linear-gradient(135deg,#5b21b6,#7c3aed);color:white;padding:14px 18px;
border-radius:13px;margin:14px 0 20px;box-shadow:0 8px 24px #5b21b633;font:600 15px system-ui}
.paper-banner small{display:block;font-weight:400;opacity:.92;margin-top:4px}
.paper-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin:16px 0}
.paper-card{background:#fff;border:1px solid #dce3ed;border-radius:13px;padding:15px;box-shadow:0 4px 16px #1f29370d}
.paper-card b{font-size:1.25rem}.paper-table{width:100%;border-collapse:collapse;font-size:.88rem}
.paper-table th,.paper-table td{padding:8px;border-bottom:1px solid #e5e7eb;text-align:left}
.paper-pill{display:inline-block;padding:3px 8px;border-radius:999px;background:#ede9fe;color:#5b21b6;font-weight:700}
</style>"""

MODE_INFO = {
    "PRODUCTION_PAPER": ("🟣", "MODO SIMULACIÓN PRODUCTIVA",
        "PPI Producción solo lectura · compras y ventas 100% simuladas · órdenes reales: NINGUNA"),
    "SANDBOX": ("🧪", "MODO SANDBOX",
        "API PPI Sandbox · órdenes únicamente en el entorno de pruebas · dinero real: NINGUNO"),
    "PRODUCTION_REAL": ("🔴", "MODO PRODUCCIÓN REAL",
        "API PPI Producción · las órdenes autorizadas pueden utilizar dinero real"),
    "DETENIDO": ("⚪", "PLATAFORMA DETENIDA",
        "Dashboard disponible · ningún motor de trading está activo"),
}


def mode_banner():
    icon, title, detail = MODE_INFO.get(MODE, ("🟡", f"MODO {_e(MODE)}", "Estado operativo no reconocido"))
    return f"<div id='porota-paper-mode' class='paper-banner'>{icon} {title}<small>{detail}</small></div>"


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(sql, params=()):
    try:
        with _conn() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]
    except Exception:
        return []


def snapshot():
    state_rows = _rows("SELECT * FROM observer_state WHERE id=1")
    state = state_rows[0] if state_rows else {
        "mode": "PRODUCTION_PAPER", "process_state": "STOPPED", "session_state": "UNKNOWN",
        "ppi_auth": "NOT_ATTEMPTED", "real_orders_sent": 0, "detail": "Observador todavía no iniciado."}
    state["real_orders_sent"] = 0
    latest = _rows("""SELECT s.* FROM market_snapshots s JOIN
      (SELECT symbol,MAX(id) id FROM market_snapshots GROUP BY symbol) x ON x.id=s.id ORDER BY s.symbol""")
    open_positions = _rows("SELECT * FROM paper_positions WHERE status='OPEN' ORDER BY opened_at DESC")
    closed = _rows("SELECT * FROM paper_positions WHERE status='CLOSED' ORDER BY closed_at DESC LIMIT 30")
    decisions = _rows("SELECT * FROM paper_decisions ORDER BY id DESC LIMIT 30")
    equity_rows = _rows("SELECT * FROM paper_equity ORDER BY id DESC LIMIT 1")
    equity = equity_rows[0] if equity_rows else {}
    samples = _rows("SELECT COUNT(*) total, SUM(CASE WHEN label_timestamp IS NOT NULL THEN 1 ELSE 0 END) labeled FROM paper_learning_samples")
    return {"state": state, "quotes": latest, "open": open_positions, "closed": closed,
            "decisions": decisions, "equity": equity, "learning": samples[0] if samples else {}}


def _e(value):
    return html.escape(str(value if value not in (None, "") else "—"))


def _money(value):
    try:
        return f"${float(value):,.2f}"
    except Exception:
        return "—"


def paper_page(compact=False):
    data = snapshot()
    s, eq = data["state"], data["equity"]
    quotes = "".join(f"<tr><td><b>{_e(q['symbol'])}</b></td><td>{_money(q['last'])}</td>"
                     f"<td>{_money(q['bid'])}</td><td>{_money(q['ask'])}</td><td>{_e(q['observed_at'])}</td></tr>"
                     for q in data["quotes"]) or "<tr><td colspan='5'>Esperando la primera cotización real.</td></tr>"
    opens = "".join(f"<tr><td>{_e(p['paper_id'])}</td><td><b>{_e(p['symbol'])}</b></td>"
                    f"<td>{_e(p['quantity'])}</td><td>{_money(p['entry_price'])}</td>"
                    f"<td>{_money(p['stop_price'])}</td><td>{_money(p['target_price'])}</td></tr>"
                    for p in data["open"]) or "<tr><td colspan='6'>Sin posiciones simuladas abiertas.</td></tr>"
    closed = "".join(f"<tr><td>{_e(p['symbol'])}</td><td>{_money(p['entry_price'])}</td>"
                     f"<td>{_money(p['exit_price'])}</td><td>{_money(p['net_pnl'])}</td>"
                     f"<td>{_e(p['close_reason'])}</td></tr>" for p in data["closed"]) or \
             "<tr><td colspan='5'>Todavía no cerraron operaciones simuladas.</td></tr>"
    decisions = "".join(f"<tr><td>{_e(d['decided_at'])}</td><td>{_e(d['symbol'])}</td>"
                        f"<td><span class='paper-pill'>{_e(d['action'])} SIMULADO</span></td>"
                        f"<td>{_e(d['score'])}</td><td>{_e(d['reason'])}</td></tr>"
                        for d in data["decisions"]) or "<tr><td colspan='5'>Esperando datos para decidir.</td></tr>"
    learning = data["learning"]
    content = f"""<h1>🧪 Simulación productiva y aprendizaje</h1>{mode_banner()}
    <div class='paper-grid'>
      <div class='paper-card'>Estado<br><b>{_e(s.get('process_state'))}</b><br><small>{_e(s.get('detail'))}</small></div>
      <div class='paper-card'>PPI solo lectura<br><b>{_e(s.get('ppi_auth'))}</b><br><small>Órdenes reales: 0</small></div>
      <div class='paper-card'>Patrimonio ficticio<br><b>{_money(eq.get('equity'))}</b><br><small>PnL realizado {_money(eq.get('realized_pnl'))}</small></div>
      <div class='paper-card'>Aprendizaje paper<br><b>{_e(learning.get('labeled',0))}/{_e(learning.get('total',0))}</b><br><small>muestras cerradas/totales</small></div>
    </div>
    <div class='paper-card'><h2>Cotizaciones reales observadas</h2><table class='paper-table'>
      <tr><th>Instrumento</th><th>Último</th><th>Bid</th><th>Ask</th><th>Hora</th></tr>{quotes}</table></div>
    <div class='paper-card'><h2>Posiciones abiertas — todas simuladas</h2><table class='paper-table'>
      <tr><th>ID paper</th><th>Instrumento</th><th>Cantidad</th><th>Entrada</th><th>Stop</th><th>Objetivo</th></tr>{opens}</table></div>
    <div class='paper-card'><h2>Últimas decisiones</h2><table class='paper-table'>
      <tr><th>Hora</th><th>Instrumento</th><th>Acción</th><th>Score</th><th>Motivo</th></tr>{decisions}</table></div>
    <div class='paper-card'><h2>Operaciones paper cerradas</h2><table class='paper-table'>
      <tr><th>Instrumento</th><th>Entrada</th><th>Salida</th><th>PnL neto estimado</th><th>Cierre</th></tr>{closed}</table></div>"""
    if compact:
        return "<!doctype html><html lang='es'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>" + THEME + "</head><body><main style='max-width:1200px;margin:auto;padding:20px'>" + content + "</main></body></html>"
    return content


def health_page():
    d = snapshot()["state"]
    auth = d.get("ppi_auth")
    color = "🟢" if auth == "OK" else "⚪" if auth == "NOT_ATTEMPTED" else "🔴"
    return f"""<!doctype html><html lang='es'><head><meta charset='utf-8'>{THEME}</head>
    <body><main style='max-width:1000px;margin:auto;padding:20px'><h1>🚦 Salud — simulación productiva</h1>{mode_banner()}
    <div class='paper-card'><table class='paper-table'><tr><th>Componente</th><th>Estado real</th><th>Detalle</th></tr>
    <tr><td>{color} PPI Producción — solo lectura</td><td>{_e(auth)}</td><td>{_e(d.get('detail'))}</td></tr>
    <tr><td>🟣 Motor paper</td><td>{_e(d.get('process_state'))}</td><td>Compras y ventas simuladas; órdenes reales: 0</td></tr>
    <tr><td>⚪ Telegram</td><td>DESACTIVADO INTENCIONALMENTE</td><td>No autoriza ni bloquea la simulación productiva.</td></tr>
    </table></div></main></body></html>"""


def _inject(content):
    if "id='porota-paper-mode'" in content or 'id="porota-paper-mode"' in content:
        return content
    if "porota-paper-theme" not in content:
        content = content.replace("</head>", THEME + "</head>", 1)
    marker = "<body>"
    pos = content.lower().find(marker)
    if pos >= 0:
        pos += len(marker)
        return content[:pos] + mode_banner() + content[pos:]
    return content


def install(app, check_auth):
    global _installed
    if _installed or MODE not in MODE_INFO:
        return
    _installed = True

    def observacion(request: Request, token: str = Query(default=""),
                    authorization: str | None = Header(default=None)):
        try:
            check_auth(token, authorization, request.cookies.get("porota_dashboard_session"))
        except TypeError:
            check_auth(token, authorization)
        return HTMLResponse(paper_page(compact=True))

    def observer_state(request: Request, token: str = Query(default=""),
                       authorization: str | None = Header(default=None)):
        try:
            check_auth(token, authorization, request.cookies.get("porota_dashboard_session"))
        except TypeError:
            check_auth(token, authorization)
        return JSONResponse(snapshot())

    app.add_api_route("/observacion", observacion, methods=["GET"], response_class=HTMLResponse)
    app.add_api_route("/api/observer/state", observer_state, methods=["GET"])

    @app.middleware("http")
    async def paper_mode_truth(request, call_next):
        response = await call_next(request)
        ctype = response.headers.get("content-type", "")
        if "text/html" not in ctype or response.status_code >= 400:
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])
        content = body.decode("utf-8", "replace")
        if request.url.path == "/testing" and MODE == "PRODUCTION_PAPER":
            content = paper_page(compact=True)
        elif request.url.path == "/salud" and MODE == "PRODUCTION_PAPER":
            content = health_page()
        else:
            content = _inject(content)
            if "</nav>" in content and "/observacion" not in content:
                content = content.replace("</nav>", " <a href='/observacion'>🧪 Simulación</a></nav>", 1)
        headers = dict(response.headers)
        headers.pop("content-length", None)
        return HTMLResponse(content, status_code=response.status_code, headers=headers)
