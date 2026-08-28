"""Dashboard 24x7 v16.3.5, independiente y sin credenciales PPI."""

from __future__ import annotations

import html
import json
import os
import re
import sqlite3
from contextlib import closing
from decimal import Decimal
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from bl_candle_engine import fingerprint
from cb_caucion_audit import allocation_history
import cd_spot_ledger as spot_ledger
from bs_instrument_contracts import aware_datetime
from bt_caucion_paper import validate_position


VERSION = "16.3.5"
DB_PATH = os.getenv("PAPER_DB_PATH", "data/observer/observer_production.db")
LEGACY_DB_PATH = os.getenv("DB_PATH", "data/trading_system.db")
MODE = os.getenv("DASHBOARD_OPERATION_MODE", "DETENIDO").upper()
TZ = ZoneInfo(os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires"))
PAPER_INITIAL_CAPITAL = float(os.getenv("PAPER_INITIAL_CAPITAL_ARS", "1000000"))
PAPER_ACTIVE_SYMBOL_LIMIT = int(os.getenv("PAPER_ACTIVE_SYMBOL_LIMIT", "20"))
REFRESH_SECONDS = int(os.getenv("DASHBOARD_REFRESH_SECONDS", "30"))
_installed = False

THEME = """
<style id='porota-paper-theme'>
:root{--bg:#f3f6fa;--panel:#fff;--ink:#172033;--muted:#667085;--line:#dce3ed;
--nav:#14213d;--blue:#1769aa;--green:#16833b;--green-bg:#eaf8ef;--yellow:#9a6500;
--yellow-bg:#fff6d9;--red:#c62828;--red-bg:#ffebee;--gray:#667085;--gray-bg:#eef1f5}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0!important;background:var(--bg)!important;
color:var(--ink)!important;font-family:system-ui,-apple-system,'Segoe UI',sans-serif!important;max-width:none!important;padding:0!important}
#porota-canonical-nav{position:sticky;top:0;z-index:10000;background:var(--nav);color:#fff;
display:flex;align-items:center;gap:5px;flex-wrap:wrap;padding:9px 14px;box-shadow:0 2px 10px #0002}
#porota-canonical-nav a{color:#fff;background:transparent;border:1px solid #ffffff38;border-radius:8px;
padding:7px 9px;text-decoration:none;font:600 13px system-ui}#porota-canonical-nav a:hover{background:#ffffff18}
#porota-paper-mode{max-width:1280px;margin:14px auto 0;padding:12px 16px;background:#e9eef5;
border:1px solid #c9d4e3;border-left:5px solid var(--nav);border-radius:10px;font-weight:750}
#porota-paper-mode small{display:block;color:#475467;font-weight:450;margin-top:3px}
.paper-page{max-width:1280px;margin:0 auto;padding:18px}.paper-page h1{font-size:1.55rem;margin:4px 0 12px}
.paper-page h2{font-size:1.08rem;margin:4px 0 12px}.paper-page h3{font-size:1rem;margin:8px 0}
.paper-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin:14px 0}
.paper-card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:15px;
box-shadow:0 3px 14px #14213d0c;margin:12px 0;overflow:auto}.paper-card b.metric{font-size:1.22rem}
.card-green{background:var(--green-bg);border-color:#8bd0a2}.card-red{background:var(--red-bg);border-color:#ef9a9a}
.card-yellow{background:var(--yellow-bg);border-color:#e4c46b}.card-gray{background:var(--gray-bg);border-color:#cfd5dd}
.positive{color:var(--green)!important;font-weight:800}.negative{color:var(--red)!important;font-weight:800}.neutral{color:var(--gray)}
.paper-table{width:100%;border-collapse:collapse;font-size:.86rem}.paper-table th{background:#e7edf4}
.paper-table th,.paper-table td{padding:9px;border-bottom:1px solid #dce3ed;text-align:left;vertical-align:top}
.paper-muted{color:var(--muted);font-size:.86rem}.paper-action{display:inline-block;background:var(--blue);color:#fff!important;
border:0;border-radius:8px;padding:9px 13px;text-decoration:none;font-weight:700;cursor:pointer;margin:2px}
.paper-status{display:inline-block;border-radius:999px;padding:3px 8px;font-weight:750;color:#fff;white-space:nowrap}
.s-verde{background:var(--green)}.s-amarillo{background:var(--yellow)}.s-rojo{background:var(--red)}.s-gris{background:var(--gray)}
.paper-notice{padding:11px 14px;border:1px solid #c9d4e3;background:#eef3f8;border-radius:9px;margin:10px 0}
.paper-warning{padding:11px 14px;border:1px solid #e7c979;background:#fff7df;border-radius:9px;margin:10px 0}
.subnav{display:flex;gap:7px;flex-wrap:wrap;margin:8px 0 16px}.subnav a{background:#fff;border:1px solid var(--line);
padding:7px 10px;border-radius:8px;text-decoration:none;color:var(--blue);font-weight:700}
details.paper-trade{background:#fff;border:1px solid var(--line);border-radius:11px;margin:10px 0;overflow:hidden}
details.paper-trade>summary{cursor:pointer;padding:13px 15px;font-weight:750;background:#f8fafc;list-style-position:inside}
.trade-body{padding:4px 15px 15px}.timeline{border-left:3px solid #bcc8d8;padding-left:15px;margin:10px 0}.timeline>div{margin:10px 0}
.paper-footer{max-width:1280px;margin:10px auto 30px;padding:0 18px;text-align:right}.up-link{display:inline-block;background:var(--nav);
color:#fff!important;padding:10px 14px;border-radius:9px;text-decoration:none;font-weight:800}
code{white-space:normal;overflow-wrap:anywhere}.legacy-shell{background:transparent}.legacy-shell>h1{margin-top:4px}
.legacy-shell table{width:100%!important;border-collapse:collapse!important}.legacy-shell th,.legacy-shell td{padding:9px!important;border-bottom:1px solid #e5e9f0!important}
@media(max-width:700px){#porota-canonical-nav{position:relative}.paper-page{padding:12px}.paper-table{font-size:.77rem}.paper-table th,.paper-table td{padding:6px}}
</style>"""

MODE_INFO = {
    "PRODUCTION_PAPER": ("MODO SIMULACIÓN PRODUCTIVA", "PPI Producción solo lectura; compras y ventas simuladas; órdenes reales: NINGUNA."),
    "SANDBOX": ("MODO SANDBOX", "PPI Sandbox; únicamente operaciones del entorno de pruebas."),
    "PRODUCTION_REAL": ("MODO PRODUCCIÓN REAL", "Las órdenes autorizadas pueden utilizar dinero real."),
    "DETENIDO": ("PLATAFORMA DETENIDA", "Dashboard disponible; ningún motor de trading está activo."),
}


def _e(value):
    return html.escape(str(value if value not in (None, "") else "—"))


def _num(value, default=0.0):
    try: return float(value)
    except Exception: return default


def _money(value):
    try: return f"$ {float(value):,.2f}"
    except Exception: return "—"


def _local_time(value):
    if not value: return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=TZ)
        return parsed.astimezone(TZ).strftime("%d/%m/%Y %H:%M:%S")
    except Exception: return _e(value)


def _conn(path=None):
    c = sqlite3.connect(Path(path or DB_PATH).resolve().as_uri()+'?mode=ro', uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def _rows(sql, params=(), path=None):
    try:
        with closing(_conn(path)) as c: return [dict(r) for r in c.execute(sql, params).fetchall()]
    except Exception: return []


def _table(name, path=None):
    try:
        with closing(_conn(path)) as c: return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())
    except Exception: return False


def _spot_warning(state):
    return "" if state=="READY" else "<div class='paper-warning'>No se pudo conciliar el ledger spot. Cantidades y resultados no disponibles; no interpretar como cero.</div>"


def _spot_snapshot():
    """Cantidades/PnL consistentes; los parciales no multiplican el win rate."""
    try:
        with closing(_conn()) as c:
            c.execute('BEGIN')
            opened, realized = spot_ledger.positions_at(c)
            closed = [dict(r) for r in c.execute("SELECT * FROM paper_positions WHERE status='CLOSED' ORDER BY closed_at DESC")]
            return {'open':opened,'closed':closed,'realized':realized,'state':'READY'}
    except Exception:
        return {'open':[],'closed':[],'realized':[],'state':'UNAVAILABLE'}


def _caucion_snapshot():
    """Validar todo el ledger antes de paginar; no ejecuta ni migra."""
    try:
        with closing(_conn()) as c:
            c.execute('BEGIN')
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='paper_cauciones' AND type='table'").fetchone():
                return {'state':'MISSING_TABLE','positions':[]}
            rows = [dict(r) for r in c.execute('SELECT * FROM paper_cauciones ORDER BY julianday(opened_at) DESC')]
            for p in rows:
                validate_position(p)
            return {'state':'READY','positions':rows}
    except ValueError:
        return {'state':'INVALID_LEDGER','positions':[]}
    except sqlite3.Error:
        return {'state':'UNAVAILABLE','positions':[]}


def _caucion_warning(state):
    return '' if state=='READY' else ("<div class='paper-warning'>Ledger de cauciones no disponible o inconsistente "
        f"({_e(state)}). Caja, patrimonio e importes de caución no confirmados; no interpretar como cero. "
        "Requiere conciliación, no se reparan registros automáticamente.</div>")


def _status(value):
    key = str(value or "").upper()
    css = "s-verde" if key in {"OK","VERDE","RUNNING","APPROVE","WIN","OPENED_SIMULATED","AVAILABLE"} else \
          "s-rojo" if key in {"ERROR","ROJO","FAILED","LOSS","VETO","BLOCKED","DEGRADED"} else \
          "s-amarillo" if key in {"HOLD","PARTIAL","COOLDOWN","WAITING","AMARILLO"} else "s-gris"
    return f"<span class='paper-status {css}'>{_e(key or 'GRIS')}</span>"


def _health_status(value):
    key = str(value or "GRIS").upper()
    normalized = "VERDE" if key in {"OK","SUCCESS","HEALTHY","VERDE"} else \
                 "ROJO" if key in {"ERROR","FAIL","FAILED","ROJO"} else \
                 "AMARILLO" if key in {"PARTIAL","DEGRADED","COOLDOWN","AMARILLO"} else "GRIS"
    return _status(normalized)


def _card(title, value, detail, state="gray", value_class=""):
    state = state if state in {"green","red","yellow","gray"} else "gray"
    return f"<div class='paper-card card-{state}'>{_e(title)}<br><b class='metric {value_class}'>{_e(value)}</b><br><span class='paper-muted'>{_e(detail)}</span></div>"


def _fresh(value, seconds=180):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=TZ)
        return (datetime.now(TZ) - parsed.astimezone(TZ)).total_seconds() <= seconds
    except Exception: return False


def mode_banner():
    title, detail = MODE_INFO.get(MODE, (f"MODO {MODE}", "Estado operativo no reconocido."))
    return (f"<div id='porota-paper-mode'>{_e(title)}<small>{_e(detail)} "
            f"Actualización visual única: cada {REFRESH_SECONDS} s.</small></div>")


def _nav():
    links = (("/", "Panel"), ("/motor-trading", "Motor de trading"), ("/salud", "Salud de APIs"),
             ("/historicos", "Históricos"), ("/aprendizaje", "Aprendizaje"),
             ("/informacion-financiera", "Información financiera"), ("/reportes", "Reportes"),
             ("/telegram", "Telegram"), ("/sre", "SRE"), ("/dashboard/logs", "Logs"),
             ("/config", "Configuración"))
    return "<nav id='porota-canonical-nav'>" + "".join(f"<a href='{href}'>{label}</a>" for href, label in links) + "</nav>"


def _dedupe_refresh(content):
    content = re.sub(r"<(?:div|p)[^>]*>[^<]*Actualizado:.*?Próxima actualización:.*?</(?:div|p)>", "", content, flags=re.I|re.S)
    content = re.sub(r"<(?:div|p)[^>]*>[^<]*Próxima actualización:.*?</(?:div|p)>", "", content, flags=re.I|re.S)
    return content


def _document(title, body, refresh=REFRESH_SECONDS):
    tag = f"<meta http-equiv='refresh' content='{int(refresh)}'>" if refresh else ""
    return ("<!doctype html><html lang='es'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"{tag}<title>{_e(title)}</title>{THEME}</head><body id='top'>{_nav()}{mode_banner()}"
            f"<main class='paper-page'>{body}</main><footer class='paper-footer'><a class='up-link' href='#top'>↑ Ir al principio</a></footer></body></html>")


def _canonicalize(content, path=""):
    if not content: return content
    if (content.count("id='porota-canonical-nav'")==1 and
        content.count("id='porota-paper-mode'")==1 and
        content.count("id='porota-paper-theme'")==1 and
        content.count("class='paper-footer'")==1):
        return content
    content = _dedupe_refresh(content)
    content = re.sub(r"<nav[^>]+id=['\"](?:porota-top-nav|porota-canonical-nav)['\"][^>]*>.*?</nav>", "", content, flags=re.I|re.S)
    content = re.sub(r"<div[^>]+class=['\"]nav['\"][^>]*>.*?</div>", "", content, flags=re.I|re.S)
    content = re.sub(r"<div[^>]+id=['\"]porota-paper-mode['\"][^>]*>.*?</div>", "", content, flags=re.I|re.S)
    content = re.sub(r"<style[^>]+id=['\"]porota-paper-theme['\"][^>]*>.*?</style>", "", content, flags=re.I|re.S)
    known = ("/vivo","/testing","/salud","/historicos","/aprendizaje","/telegram","/sre","/dashboard/logs","/config")
    def strip(block):
        return "" if sum(f"href='{route}" in block.group(0) or f'href="{route}' in block.group(0) for route in known) >= 3 else block.group(0)
    content = re.sub(r"<(?:p|div)[^>]*>.*?</(?:p|div)>", strip, content, flags=re.I|re.S)
    content = re.sub(r"</head>", THEME + "</head>", content, count=1, flags=re.I)
    body, closing = re.search(r"<body[^>]*>", content, flags=re.I), re.search(r"</body>", content, flags=re.I)
    if body and closing:
        inner = content[body.end():closing.start()]
        if "id='porota-legacy-shell'" not in inner:
            inner = f"<main id='porota-legacy-shell' class='paper-page legacy-shell'>{inner}</main>"
        content = content[:body.end()] + _nav() + mode_banner() + inner + "<footer class='paper-footer'><a class='up-link' href='#top'>↑ Ir al principio</a></footer>" + content[closing.start():]
    return content


def snapshot():
    state = (_rows("SELECT * FROM observer_state WHERE id=1") or [{"mode":MODE,"process_state":"STOPPED","session_state":"UNKNOWN","ppi_auth":"NOT_ATTEMPTED","real_orders_sent":0,"detail":"Observador todavía no iniciado."}])[0]
    state["real_orders_sent"] = 0
    identity_extra = ",currency,market" if any(r["name"] == "currency" for r in _rows("PRAGMA table_info(market_snapshots)")) else ""
    quotes = _rows(f"""SELECT s.* FROM market_snapshots s JOIN
      (SELECT symbol,asset_class,settlement,MAX(id) id FROM market_snapshots
       GROUP BY symbol,asset_class,settlement{identity_extra}) x ON x.id=s.id
      ORDER BY s.symbol,s.asset_class,s.settlement""")
    spot = _spot_snapshot()
    opened, closed = spot['open'],spot['closed']
    decisions = _rows("SELECT * FROM paper_decisions ORDER BY id DESC LIMIT 100")
    equity = (_rows("SELECT * FROM paper_equity ORDER BY id DESC LIMIT 1") or [{}])[0]
    learning = (_rows("SELECT COUNT(*) total,SUM(CASE WHEN label_timestamp IS NOT NULL THEN 1 ELSE 0 END) labeled FROM paper_learning_samples") or [{}])[0]
    caucion_data = _caucion_snapshot()
    cauciones = caucion_data['positions'][:500]
    balances = _rows("""SELECT e.* FROM paper_equity_by_currency e JOIN
      (SELECT currency,MAX(id) id FROM paper_equity_by_currency GROUP BY currency) latest ON latest.id=e.id
      ORDER BY e.currency""") if _table("paper_equity_by_currency") else []
    supervisor = (_rows("SELECT * FROM paper_supervisor_state WHERE id=1") or [{}])[0] if _table("paper_supervisor_state") else {}
    exit_reader = (_rows("SELECT * FROM paper_exit_reader_state WHERE id=1") or [{}])[0] if _table("paper_exit_reader_state") else {}
    exits = _rows("SELECT * FROM paper_exit_intents") if _table("paper_exit_intents") else []
    valuation_quality = _rows("SELECT * FROM paper_valuation_quality") if _table("paper_valuation_quality") else []
    return {"state":state,"quotes":quotes,"open":opened,"closed":closed,"realized":spot['realized'],"spot_state":spot['state'],"decisions":decisions,"equity":equity,"learning":learning,"cauciones":cauciones,"balances_by_currency":balances,
            "caucion_state":caucion_data['state'],"exit_supervisor":supervisor,"exit_reader":exit_reader,"exit_intents":exits,"valuation_quality":valuation_quality,
            "daily_risk":_rows('SELECT * FROM paper_daily_risk ORDER BY day DESC,currency LIMIT 16') if _table('paper_daily_risk') else [],
            "notification_worker":(_rows('SELECT * FROM paper_notification_worker WHERE id=1') or [{}])[0] if _table('paper_notification_worker') else {},
            "notification_counts":_rows('SELECT state,COUNT(*) total FROM paper_notification_outbox GROUP BY state') if _table('paper_notification_outbox') else []}


def _daily_risk_panel():
    rows = snapshot()['daily_risk']
    today = datetime.now(TZ).date().isoformat()
    current = [r for r in rows if r['day']==today]
    items = []
    for r in current:
        state = r['state']
        try:
            age = (datetime.now(TZ)-datetime.fromisoformat(r['evaluated_at'])).total_seconds()
            if not 0 <= age <= 20 and state!='LATCHED':
                state = 'STALE'
        except (ValueError,TypeError):
            state = 'UNKNOWN'
        items.append(f"<tr><td>{_e(r['currency'])}</td><td>{_e(state)}</td>"
            f"<td>{_e(r['baseline_equity'] or 'Sin base')}</td><td>{_e(r['daily_pnl'] or 'Sin valuación')}</td>"
            f"<td>{_e(r['loss_budget'] or '—')} ({_e(r['limit_pct'])}%)</td>"
            f"<td>{_e(r['detail'])}</td></tr>")
    return ("<div class='paper-card'><h2>Corte diario por moneda</h2>"
            "<p>El bloqueo sobrevive a reinicios. No mezcla monedas ni libera cauciones antes del vencimiento. "
            "Sin base o cotizaciones confiables se suspenden nuevas entradas; las salidas continúan.</p>"
            "<table class='paper-table'><tr><th>Moneda</th><th>Estado</th><th>Base</th><th>PnL neto diario</th>"
            "<th>Límite</th><th>Detalle</th></tr>"+(''.join(items) or
            "<tr><td colspan='6'>Sin evaluación del día actual; no asumir habilitación.</td></tr>")+"</table></div>")


def _exit_supervision_panel():
    data = snapshot()
    health = data["exit_supervisor"]
    # Sólo lectura: abrir el panel no crea bases, migra ni ejecuta supervisión.
    state = health.get("state", "NOT_STARTED")
    try:
        age = (datetime.now(TZ) - datetime.fromisoformat(health["heartbeat_at"])).total_seconds()
        if not 0 <= age <= 20:
            state = "STALE"
    except (KeyError, ValueError, TypeError):
        state = "UNKNOWN"
    reader = data["exit_reader"]
    reader_state = reader.get("state","NOT_STARTED")
    try:
        age = (datetime.now(TZ) - datetime.fromisoformat(reader["heartbeat_at"])).total_seconds()
        if not 0 <= age <= 20:
            reader_state = "STALE"
    except (KeyError, ValueError, TypeError):
        reader_state = "UNKNOWN"
    intents = {r["paper_id"]:r for r in data["exit_intents"]}
    rows = []
    for p in data["open"]:
        r = intents.get(p["paper_id"],{})
        rows.append(f"<tr><td>{_e(p['symbol'])} · {_e(p.get('currency','ARS'))}</td>"
                    f"<td>{_e(r.get('state','AWAITING_SUPERVISION'))}</td><td>{_e(r.get('cause') or '—')}</td>"
                    f"<td>{_local_time(r.get('due_at'))}</td><td>{_e(r.get('blocked_reason','Pendiente de revisión'))}</td>"
                    f"<td>{_local_time(r.get('supervised_at'))}</td></tr>")
    return ("<div class='paper-card'><h2>Supervisión de salidas</h2>"
            f"<p>Reloj independiente: <b>{_e(state)}</b> · último pulso: {_local_time(health.get('heartbeat_at'))}</p>"
            f"<p>Lector de salidas: <b>{_e(reader_state)}</b> · último pulso: {_local_time(reader.get('heartbeat_at'))}</p>"
            "<p>Una salida decidida no equivale a una venta. Sin libro fresco, liquidez o sesión habilitada, sigue pendiente. "
            "No se ejecuta con precios viejos ni fuera de la ventana paper. El estado STALE bloquea nuevas entradas.</p>"
            "<table class='paper-table'><tr><th>Instrumento</th><th>Estado de salida</th><th>Causa</th>"
            "<th>Decidida</th><th>Detalle</th><th>Supervisada</th></tr>" + ("".join(rows) or
            "<tr><td colspan='6'>Sin posiciones de compraventa abiertas.</td></tr>") + "</table></div>")


_ALLOCATION_REASONS = {
    'CANDIDATE_SELECTED':'Oferta elegida por el criterio configurado',
    'NO_ELIGIBLE_OFFER':'Ninguna oferta cumple las condiciones',
    'POLICY_NOT_KNOWN':'La política aún no estaba disponible al decidir',
    'OUTSIDE_CONFIRMED_SESSION':'Fuera de la sesión indicada en la política',
    'DAILY_RISK_NOT_CONFIGURED':'Falta configurar el corte diario',
    'DAILY_RISK_CURRENCY_MISMATCH':'El riesgo diario corresponde a otra moneda',
    'DAILY_RISK_PROJECTED_LOSS':'Los costos comprometidos alcanzarían el corte diario',
    'OTHER_CURRENCY':'Moneda o plaza distinta de la caja elegida',
    'CONFLICTING_BOOK_OR_BUDGET':'Libro o presupuesto contradictorio',
    'CAUCION_CONFLICTING_BOOK':'El libro contradice la fotografía ya utilizada',
    'CAUCION_OLDER_BOOK':'Libro anterior a otro ya utilizado',
    'UNKNOWN_CONTRACT_SOURCE':'Falta la fuente del contrato',
    'QUOTE_STALE_OR_FUTURE':'Cotización vencida o posterior a la decisión',
    'START_DATE_MISMATCH':'Fecha de inicio incompatible',
    'MATURITY_OUTSIDE_LIQUIDITY_WINDOW':'Vencimiento fuera del plazo permitido',
    'EXPLICIT_COST_BUDGET_REQUIRED':'Falta presupuesto completo para el capital exacto',
    'PRINCIPAL_CAP':'Supera el tope de capital por colocación',
    'DEPTH_EXHAUSTED':'Profundidad disponible insuficiente',
    'CASH_RESERVE_OR_FRACTION':'Excede la fracción disponible después de la reserva',
    'NET_PROFIT_TOO_LOW':'Beneficio neto insuficiente',
}


def _allocation_reason(code):
    if code in _ALLOCATION_REASONS:
        return _ALLOCATION_REASONS[code]
    if code.startswith('DAILY_RISK_'):
        return 'Corte diario no habilitado: '+code.removeprefix('DAILY_RISK_')
    return code


def _caucion_allocations_panel():
    data = allocation_history(DB_PATH)
    states = {'MISSING_DATABASE':'Base no disponible; no asumir ausencia de decisiones',
        'MISSING_TABLE':'Historial de asignación aún no disponible en esta base',
        'READ_ERROR':'No se pudo leer el historial; requiere revisión',
        'EMPTY':'Sin decisiones de asignación registradas', 'EMPTY_PAGE':'Página sin registros',
        'PARTIAL':'Hay registros inconsistentes en esta página; revisar',
        'READABLE':'Historial legible; concordancia interna de esta página'}
    cards = []
    for record in data['records']:
        label = f"Solicitud {_e(record['request_id'])} · {_local_time(record['evaluated_at'])}"
        if record['state'] != 'CONSISTENT':
            cards.append(f"<div class='paper-warning'><b>{label} · Registro inconsistente</b>"
                         f"<p>No se muestran importes ni se confirma una colocación: {_e(record['issue'])}</p></div>")
            continue
        decision = record['decision']
        policy, selected = decision['manifest']['policy'], decision['selected']
        metric = ('Mayor beneficio neto del contrato' if policy['ranking'] == 'NET_PROFIT'
                  else 'Vencimiento más próximo; luego mayor retorno neto por día' if policy['ranking']=='EARLIEST_MATURITY_NET_RETURN'
                  else 'Mayor retorno neto por día sobre el débito inicial')
        offers = {fingerprint(o):o for o in decision['manifest']['offers']}
        rows = []
        for candidate in decision['candidates']:
            offer = offers[candidate['candidate_id']]
            chosen = selected is not None and candidate['candidate_id'] == selected['candidate_id']
            reason = ('Elegida' if chosen else 'Elegible; no elegida por criterio o desempate'
                      if candidate['code'] == 'ELIGIBLE' else _allocation_reason(candidate['code']))
            rows.append(f"<tr><td>{_e(offer['instrument_id'])}<br>"
                f"Cotizada: {_local_time(offer['quoted_at'])}<br>Vence: {_local_time(offer['maturity_at'])}</td>"
                f"<td>{_e(offer['currency'])}</td><td>{_e(offer['fee_quote_principal'])}</td>"
                f"<td>{_e(candidate.get('fees', offer['quoted_total_fees']))} · {_e(offer['fee_payment'])}</td>"
                f"<td>{_e(candidate.get('net_profit'))}</td><td>{_e(candidate.get('net_return_per_day'))}</td>"
                f"<td>{_e(reason)}</td></tr>")
        placement = record['placement']
        placement_text = ('Sin colocación para esta solicitud' if placement is None else
            f"Registro {_e(placement['paper_id'])} · Estado guardado: {_e(placement['status'])} · "
            f"Acreditación simulada: {_local_time(placement['settled_at'])}")
        selected_text = ('Sin inversión' if selected is None else
            f"Capital colocado: {_e(selected['principal'])} · Débito inicial: {_e(selected['cash_debit'])} · "
            f"Neto estimado al vencimiento: {_e(selected['net_profit'])}")
        expanded = ' open' if record is data['records'][0] else ''
        cards.append(f"<details class='paper-trade'{expanded}><summary>{label} · {_e(decision['currency'])} · "
            f"{'Colocación simulada registrada' if selected else 'Abstención'}</summary><div class='trade-body'>"
            f"<p>{_e(_allocation_reason(decision['code']))}. {selected_text}</p><p>{placement_text}</p>"
            f"<p>Criterio: {_e(metric)}. Desempate: menor plazo, menor débito, identificador de oferta.</p>"
            f"<p>Caja liquidada evaluada: {_e(decision['cash'])} {_e(decision['currency'])} · "
            f"Reserva: {_e(policy['reserve_cash'])} · Fracción por solicitud (0–1): {_e(policy['maximum_cash_fraction'])} · "
            f"Presupuesto de débito: {_e(decision['cash_budget'])} · Tope de capital por colocación: {_e(policy['maximum_principal'])}</p>"
            f"<p>Liquidez requerida: {_local_time(policy['liquidity_deadline'])} · Participación efectiva (0–1): {_e(decision['participation'])} · "
            f"Antigüedad máxima: {_e(policy['maximum_quote_age_seconds'])} s · Neto mínimo: {_e(policy['minimum_net_profit'])}</p>"
            f"<p>Política: {_e(decision['policy_version'])} · Congelada: {_local_time(policy['frozen_at'])} · "
            f"Sesión: {_local_time(policy['session_open_at'])} a {_local_time(policy['session_close_at'])} · Fuente: {_e(policy['session_source'])}</p>"
            "<p>Costos: UPFRONT al inicio; MATURITY al vencimiento. Retorno diario expresado como fracción, "
            "sin suponer reinversión. Elegibles muestran el costo redondeado del simulador; descartadas, "
            "el presupuesto aportado. Un guion indica cálculo no habilitado, no beneficio cero.</p>"
            "<table class='paper-table'><tr><th>Oferta / fechas</th><th>Moneda</th><th>Capital presupuestado</th>"
            "<th>Costo total / pago</th><th>Neto estimado</th><th>Retorno neto diario</th><th>Resultado</th></tr>"
            + (''.join(rows) or "<tr><td colspan='7'>No se aportaron ofertas.</td></tr>") + "</table></div></details>")
    return ("<section class='paper-card' id='caucion-allocations'><h2>Decisiones de caución</h2>"
        "<p>Historial de simulación: sólo colocadoras con saldo liquidado de la misma moneda. "
        "La caja y los presupuestos corresponden al momento de decidir; no son el saldo actual. "
        "No certifica datos de PPI ni habilita operaciones reales. La selección automática todavía no está conectada.</p>"
        f"<p><b>{_e(states[data['state']])}</b> · Mostrando {len(data['records'])} de {_e(data['total'])} decisiones.</p>"
        "<p><a href='/api/paper/caucion-allocations?limit=100'>Ver historial JSON (hasta 100; admite offset)</a></p>"
        + ''.join(cards) + "</section>")


def _cauciones_panel():
    data = _caucion_snapshot()
    if data['state']!='READY':
        return "<div class='paper-card'><h2>Cauciones colocadoras</h2>"+_caucion_warning(data['state'])+'</div>'
    positions = data['positions'][:100]
    rows = "".join(
        f"<tr><td>{_e(p['instrument_id'])}</td><td>{_e(p['currency'])}</td>"
        f"<td>{_e(p['principal'])}</td><td>{_e(p['annual_rate_fraction'])}</td>"
        f"<td>{p['interest_days']}</td><td>{_local_time(p['maturity_at'])}</td>"
        f"<td>{_e(p['gross_interest'])}</td><td>{_e(p['total_fees'])}</td>"
        f"<td>{_status(p['status'])}</td></tr>" for p in positions
    ) or "<tr><td colspan='9'>Sin colocaciones simuladas. El alta requiere términos y cotización validados.</td></tr>"
    return ("<div class='paper-card'><h2>Cauciones colocadoras</h2>"
            "<p>Capital inmovilizado hasta el vencimiento. ARS, USD, MEP y CCL mantienen cajas separadas. "
            "Sin stop ni venta intradiaria. La tasa se muestra como fracción anual: 0,30 equivale a 30%.</p>"
            "<p class='paper-muted'>Vencimientos y acreditaciones son simulados; no confirman movimientos en PPI.</p>"
            "<table class='paper-table'><tr><th>Contrato</th><th>Moneda</th><th>Capital</th>"
            "<th>TNA (fracción)</th><th>Días corridos</th><th>Vencimiento</th>"
            "<th>Interés bruto</th><th>Costos totales</th><th>Estado</th></tr>" + rows + "</table></div>")


def _trade_metrics(closed):
    pnl = sum(_num(p.get("net_pnl")) for p in closed if p.get("currency", "ARS") == "ARS")
    wins = sum(1 for p in closed if _num(p.get("net_pnl")) > 0)
    return pnl, wins, (wins / len(closed) * 100 if closed else None)


def _balances_panel():
    data = snapshot()
    if data['caucion_state']!='READY':
        return "<div class='paper-card'><h2>Caja y patrimonio por moneda</h2>"+_caucion_warning(data['caucion_state'])+'</div>'
    balances = data["balances_by_currency"]
    quality = {r['currency']:r for r in data["valuation_quality"]}
    rows = "".join(f"<tr><td>{_e(r['currency'])}</td><td>{_e(r['cash'])}</td>"
                   f"<td>{_e(r['pending_proceeds'])}</td><td>{_e(r['caucion_principal'])}</td>"
                   f"<td>{_e(r['realized_pnl'])}</td><td>{_e(r['equity'])}</td>"
                   f"<td>{_e(quality.get(r['currency'],{}).get('state','UNKNOWN'))} · "
                   f"{_local_time(quality.get(r['currency'],{}).get('measured_at'))}</td></tr>" for r in balances)
    rows = rows or "<tr><td colspan='7'>Pendiente de la primera valuación por moneda.</td></tr>"
    return ("<div class='paper-card'><h2>Caja y patrimonio por moneda</h2>"
            "<p>Sin conversión automática: pesos, dólar billete/MEP, divisa/CCL y USD sin plaza "
            "no se suman ni se prestan saldo entre sí. Sólo cauciones colocadoras; sin financiación.</p>"
            "<table class='paper-table'><tr><th>Moneda/plaza</th><th>Disponible</th>"
            "<th>Ventas pendientes</th><th>Capital caucionado</th><th>PnL realizado</th>"
            "<th>Patrimonio estimado</th><th>Valuación</th></tr>" + rows + "</table>"
            "<p>STALE_MARKS conserva la última marca válida (o costo inicial sin marca); no es una valuación vigente.</p></div>")


def _report_state(family):
    for path in (Path(f"data/informe_api_{family}.json"), Path(f"data/informe_apis_{family}.json"), Path("data/informe_apis.json")):
        try:
            if not path.exists(): continue
            data = json.loads(path.read_text(encoding="utf-8")); values=[]
            def walk(node):
                if isinstance(node,dict):
                    for k,v in node.items():
                        if str(k).lower() in {"ok","status","state","estado","result","resultado"} and v is not None: values.append(str(v).upper())
                        walk(v)
                elif isinstance(node,list):
                    for v in node: walk(v)
            walk(data); modified=datetime.fromtimestamp(path.stat().st_mtime,TZ).isoformat()
            bad=any(v in {"ERROR","FAILED","FAIL","FALLA","ROJO","FALSE"} for v in values)
            good=any(v in {"OK","SUCCESS","CORRECTO","VERDE","TRUE","HEALTHY"} for v in values)
            return ("ROJO" if bad else "VERDE" if good else "GRIS", f"Informe persistido: {path.name}", modified, modified if good else None)
        except Exception: pass
    return "GRIS", "Sin verificación persistida.", None, None


def home_page():
    data=snapshot(); state=data["state"]; closed=data["closed"]
    today=datetime.now(TZ).date().isoformat(); today_closed=[p for p in closed if aware_datetime(p["closed_at"]).astimezone(TZ).date().isoformat()==today]
    today_pnl,today_wins,today_wr=_trade_metrics(today_closed); total_pnl,wins,total_wr=_trade_metrics(closed)
    today_pnl = sum((_num(p['net_pnl']) for p in data['realized'] if p.get('currency','ARS')=='ARS'
                     and aware_datetime(p['closed_at']).astimezone(TZ).date().isoformat()==today),0)
    heartbeat_ok=_fresh(state.get("heartbeat_at"),180)
    sre=(_rows("SELECT * FROM sre_snapshots ORDER BY id DESC LIMIT 1") or [{}])[0] if _table("sre_snapshots") else {}
    db_ok=sre.get("db_integrity")=="ok"
    telegram=_report_state("telegram")[0]
    api=(_rows("SELECT state FROM api_health") if _table("api_health") else [])
    critical_bad=sum(1 for r in api if str(r.get("state")).upper()=="ROJO")
    overall=heartbeat_ok and db_ok and critical_bad==0
    equity=_num(data["equity"].get("equity"),PAPER_INITIAL_CAPITAL)
    cards="".join((
        _card("Estado general", "TODO OPERATIVO" if overall else "REVISAR", f"{critical_bad} APIs en rojo", "green" if overall else "red"),
        _card("Dashboard 24x7", "ACTIVO", "Esta página responde aunque la rueda esté cerrada", "green"),
        _card("Observador / simulador", "ACTIVO" if heartbeat_ok else "SIN LATIDO", f"Último latido {_local_time(state.get('heartbeat_at'))}", "green" if heartbeat_ok else "red"),
        _card("Telegram", telegram, "Avisos de modo y resumen de cierre", "green" if telegram=="VERDE" else "red" if telegram=="ROJO" else "gray"),
        _card("Base paper", "OK" if db_ok else "REVISAR", f"Integridad {sre.get('db_integrity','sin medición')}", "green" if db_ok else "red"),
        _card("PPI solo lectura", state.get("ppi_auth"), "Órdenes reales bloqueadas por transporte", "green" if state.get("ppi_auth")=="OK" else "yellow" if state.get("ppi_auth") in {"NOT_ATTEMPTED","COOLDOWN"} else "red"),
        _card("Patrimonio paper ARS", _money(equity) if data['caucion_state']=='READY' else 's/d', "Capital completamente ficticio; sin consolidar dólares", "gray" if data['caucion_state']!='READY' else "green" if equity>=PAPER_INITIAL_CAPITAL else "red"),
        _card("Resultado de hoy ARS", _money(today_pnl) if data["spot_state"]=="READY" else "s/d", f"Win rate {'s/d' if today_wr is None else f'{today_wr:.1f}%'}", "green" if today_pnl>0 else "red" if today_pnl<0 else "gray", "positive" if today_pnl>0 else "negative" if today_pnl<0 else "neutral"),
        _card("Win rate acumulado", "s/d" if total_wr is None else f"{total_wr:.1f}%", f"{wins}/{len(closed)} cierres ganadores", "green" if total_wr is not None and total_wr>=50 else "red" if total_wr is not None else "gray"),
        _card("Órdenes reales", "0", "Barrera HTTP fail-closed", "green"),
    ))
    body=f"<h1>Panel ejecutivo — Porota Trading {VERSION}</h1><p class='paper-muted'>Una sola vista del sistema, infraestructura, APIs y desempeño paper.</p><div class='paper-grid'>{cards}</div>"
    return _document("Porota Trading",_spot_warning(data["spot_state"])+_caucion_warning(data['caucion_state'])+body)


def paper_page(compact=False):
    data=snapshot(); state=data["state"]
    latest=(_rows("SELECT * FROM universe_cycle_metrics ORDER BY id DESC LIMIT 1") or [{}])[0] if _table("universe_cycle_metrics") else {}
    pnl,wins,wr=_trade_metrics(data["closed"])
    pnl=sum(_num(p["net_pnl"]) for p in data["realized"] if p.get("currency","ARS")=="ARS")
    rows="".join(f"<tr><td>{_local_time(d['decided_at'])}</td><td><b>{_e(d['symbol'])}</b></td><td>{_status(d['action'])}</td><td>{_e(d['score'])}</td><td>{_e(d['reason'])}</td></tr>" for d in data["decisions"][:30]) or "<tr><td colspan='5'>Esperando decisiones.</td></tr>"
    cards="".join((
        _card("Motor paper", state.get("process_state"), state.get("detail"), "green" if _fresh(state.get("heartbeat_at"),180) else "red"),
        _card("Sesión BYMA", state.get("session_state"), "El dashboard sigue activo fuera de rueda", "green" if state.get("session_state")=="MARKET_OPEN" else "gray"),
        _card("PPI Producción", state.get("ppi_auth"), "Sólo lectura", "green" if state.get("ppi_auth")=="OK" else "yellow"),
        _card("Resultado acumulado ARS", _money(pnl) if data["spot_state"]=="READY" else "s/d", "Incluye fills parciales realizados", "green" if pnl>0 else "red" if pnl<0 else "gray", "positive" if pnl>0 else "negative" if pnl<0 else "neutral"),
        _card("Win rate", "s/d" if wr is None else f"{wr:.1f}%", f"{wins}/{len(data['closed'])} ganadoras", "green" if wr is not None and wr>=50 else "red" if wr is not None else "gray"),
        _card("Universo del ciclo", f"{latest.get('selected_count',0)}/{latest.get('eligible_total',0)}", f"Rotación activa; recomendación medida: {latest.get('recommended_limit','s/d')}", "green" if latest.get('successful_count')==latest.get('selected_count') and latest.get('selected_count') else "yellow"),
    ))
    # /vivo, /testing y /observacion comparten encabezado y explicación visible.
    heading = (
        "<h1>Panel de simulación productiva</h1>"
        "<p class='paper-muted'>Una sola vista para estado, actividad y simulación.</p>"
    )
    body=heading+f"<div class='paper-grid'>{cards}</div><div class='paper-notice'><b>El límite {PAPER_ACTIVE_SYMBOL_LIMIT} es por ciclo, no el universo total.</b> La ventana rota para cubrir todos los elegibles y registra latencia, errores y recomendación antes de ampliarse.</div><div class='paper-card'><h2>Decisiones y abstenciones actuales</h2><table class='paper-table'><tr><th>Hora</th><th>Instrumento</th><th>Acción</th><th>Score</th><th>Motivo</th></tr>{rows}</table></div>"
    body = _spot_warning(data['spot_state'])+_caucion_warning(data['caucion_state'])+body
    return _document("Simulación productiva",body) if compact else body


def _features(value):
    try: return json.loads(value or "{}")
    except Exception: return {}


def motor_page():
    spot=_spot_snapshot()
    positions=sorted(spot["open"]+spot["closed"],key=lambda p:aware_datetime(p["opened_at"]),reverse=True)[:100]
    gates=_rows("SELECT * FROM trade_gate_evaluations ORDER BY id DESC LIMIT 100") if _table("trade_gate_evaluations") else []
    gate_by_symbol={g["symbol"]:g for g in gates}
    cards=[]
    for p in positions:
        pnl=_num(p.get("net_pnl")); cls="positive" if pnl>0 else "negative" if pnl<0 else "neutral"
        gate=gate_by_symbol.get(p["symbol"],{})
        features=_features(p.get("features_json")); variables="".join(f"<tr><td>{_e(k)}</td><td>{_e(v)}</td></tr>" for k,v in sorted(features.items()))
        lesson=("Ganancia: el movimiento favorable superó costos y slippage." if pnl>0 else "Pérdida: revisar momentum, spread, profundidad, duración y contexto antes de ampliar exposición." if pnl<0 else "Resultado aún no cerrado; no cambia umbrales.")
        cards.append(f"""<details class='paper-trade'><summary>{_e(p['symbol'])} · {_e(p['status'])} · {_local_time(p['opened_at'])} · <span class='{cls}'>PnL {_money(pnl)} {_e(p.get('currency','ARS'))}</span></summary><div class='trade-body'>
        <h3>Secuencia de portones</h3><table class='paper-table'><tr><th>Técnico</th><th>IA Gemini</th><th>Patrimonial / liquidez</th><th>Resultado final</th></tr><tr><td>{_status(gate.get('technical_gate','APPROVE'))}</td><td>{_status(gate.get('ai_gate','SIN_REGISTRO'))}</td><td>{_status(gate.get('patrimonial_gate','SIN_REGISTRO'))}</td><td>{_status(gate.get('final_result',p['status']))}</td></tr></table>
        <p><b>Explicación:</b> {_e(gate.get('reason','Operación histórica sin secuencia completa persistida.'))}</p>
        <p class='paper-notice'><b>Importante:</b> Gemini no es el último filtro absoluto. Puede aprobar la tesis y aun así capital, exposición, cantidad máxima o profundidad pueden bloquear el fill. Esto es correcto y protege el patrimonio paper.</p>
        <p><b>Cantidad remanente:</b> {_e(p['quantity'] if p['status']=='OPEN' else '0')}. <b>PnL parcial realizado:</b> {_e(p.get('realized_net_pnl','—'))} {_e(p.get('currency','ARS'))}. Una operación abierta todavía no tiene resultado final.</p>
        <h3>Variables utilizadas</h3><table class='paper-table'><tr><th>Variable</th><th>Valor</th></tr>{variables}</table>
        <h3>Lección aprendida</h3><p class='{cls}'>{_e(lesson)}</p></div></details>""")
    gate_rows="".join(f"<tr><td>{_local_time(g['evaluated_at'])}</td><td><b>{_e(g['symbol'])}</b></td><td>{_status(g['ai_gate'])}</td><td>{_status(g['patrimonial_gate'])}</td><td>{_status(g['final_result'])}</td><td>{_e(g['reason'])}</td></tr>" for g in gates[:50]) or "<tr><td colspan='6'>Aún no hay secuencias nuevas.</td></tr>"
    trade_cards="".join(cards) or '<div class="paper-card">Sin operaciones simuladas todavía.</div>'
    body=f"<h1>Motor de trading</h1><p class='paper-muted'>Una única actualización visual; trazabilidad técnica → IA → patrimonio/liquidez → resultado.</p><div class='paper-warning'><b>Todas las operaciones de esta página son simuladas.</b> Nunca representan una orden enviada a PPI.</div>{trade_cards}<div class='paper-card'><h2>Decisiones bloqueadas o aprobadas</h2><table class='paper-table'><tr><th>Hora</th><th>Instrumento</th><th>IA</th><th>Patrimonial</th><th>Final</th><th>Explicación</th></tr>{gate_rows}</table></div>"
    return _document("Motor de trading",_spot_warning(spot["state"])+_daily_risk_panel() + _exit_supervision_panel() + body + _balances_panel() + _caucion_allocations_panel() + _cauciones_panel())


def _next_check(component, checked):
    cadence=21600 if "SANDBOX" in component or "BYMA" in component else 43200 if component in {"FINANCIAL_REFRESH","BCRA","INDEC"} else 3600 if "PPI" in component or "GEMINI" in component else 1800
    try:
        dt=datetime.fromisoformat(str(checked).replace("Z","+00:00"));
        if dt.tzinfo is None: dt=dt.replace(tzinfo=TZ)
        return _local_time(dt+timedelta(seconds=cadence))
    except Exception: return "Al activarse su planificador"


def health_page():
    persisted={r["component"]:r for r in _rows("SELECT * FROM api_health")} if _table("api_health") else {}
    jobs={r["job_key"]:r for r in _rows("SELECT * FROM operational_jobs")} if _table("operational_jobs") else {}
    components=[]
    def add(name,key,mode,use,default="Sin verificación persistida."):
        row=persisted.get(key) or jobs.get(key) or {}; checked=row.get("checked_at") or row.get("last_run_at")
        components.append((name,row.get("state","GRIS"),row.get("detail",default),checked,row.get("last_success_at"),_next_check(key,checked),mode,use))
    add("PPI Sandbox — autenticación","PPI_SANDBOX_AUTH","SANDBOX","Un login aislado; cero cuenta y cero órdenes")
    add("PPI Producción — autenticación","PPI_PRODUCTION_AUTH","SIMULACIÓN PRODUCTIVA","Login de sólo lectura")
    add("PPI Producción — catálogo","PPI_PRODUCTION_CATALOG","SIMULACIÓN PRODUCTIVA","Descubrimiento de instrumentos")
    add("PPI Producción — históricos","PPI_PRODUCTION_HISTORY","SIMULACIÓN PRODUCTIVA","Cobertura incremental de todo el universo")
    add("PPI Producción — market data","PPI_PRODUCTION_MARKETDATA","SIMULACIÓN PRODUCTIVA","Cotización y caja de puntas")
    add("Google Gemini","GEMINI_DECISION","SIMULACIÓN / SANDBOX","Portón crítico, seguido del portón patrimonial")
    tg=_report_state("telegram"); components.append(("Telegram",tg[0],tg[1],tg[2],tg[3],_next_check("TELEGRAM",tg[2]),"TODOS","Avisos de modo y resumen de cierre"))
    add("BCRA / INDEC","FINANCIAL_REFRESH","TODOS","Información financiera oficial")
    add("OPENBYMADATA / BYMA","BYMA_OPEN_DATA","TODOS","Referencia pública oficial; sin redistribuir market data")
    add("Feeds de noticias","NEWS_REFRESH","TODOS","Contexto financiero, económico y geopolítico")
    add("Base SQLite paper","SRE_SNAPSHOT","TODOS","Persistencia e integridad")
    rows="".join(f"<tr><td><b>{_e(n)}</b></td><td>{_health_status(s)}</td><td>{_e(d)}</td><td>{_local_time(c)}</td><td>{_local_time(ok)}</td><td>{_e(nx)}</td><td>{_e(m)}</td><td>{_e(u)}</td></tr>" for n,s,d,c,ok,nx,m,u in components)
    body=f"<h1>Salud de APIs y fuentes</h1><p class='paper-muted'>Abrir esta pantalla no consume APIs. Cada fila informa modo, alcance y próximo chequeo.</p><div class='paper-card'><table class='paper-table'><tr><th>API / fuente</th><th>Estado</th><th>Detalle</th><th>Último reporte / chequeo</th><th>Último éxito</th><th>Próximo chequeo</th><th>Modo</th><th>Uso</th></tr>{rows}</table></div>"
    return _document("Salud de APIs",body,refresh=60)


def history_page():
    catalog=(_rows("SELECT COUNT(*) n FROM instrument_catalog") or [{"n":0}])[0]["n"] if _table("instrument_catalog") else 0
    eligible=(_rows("SELECT COUNT(*) n FROM candidate_universe WHERE can_simulate=1 AND status='AVAILABLE'") or [{"n":0}])[0]["n"] if _table("candidate_universe") else 0
    history=(_rows("SELECT COUNT(*) instruments,SUM(row_count) rows,MAX(downloaded_at) latest FROM production_history") or [{}])[0] if _table("production_history") else {}
    cycles=_rows("SELECT * FROM universe_cycle_metrics ORDER BY id DESC LIMIT 30") if _table("universe_cycle_metrics") else []
    cycle_rows="".join(f"<tr><td>{_local_time(r['started_at'])}</td><td>{r['selected_count']}/{r['eligible_total']}</td><td>{r['successful_count']}</td><td>{r['failed_count']}</td><td>{r['duration_seconds']:.2f}s</td><td>{r['recommended_limit']}</td></tr>" for r in cycles) or "<tr><td colspan='6'>Esperando métricas.</td></tr>"
    cards="".join((_card("Catálogo PPI",catalog,"Todos los instrumentos devueltos por búsquedas validadas","green" if catalog else "gray"),_card("Universo elegible",eligible,"No equivale al lote de un ciclo","green" if eligible else "gray"),_card("Respuestas históricas guardadas",history.get('instruments',0),f"{history.get('rows',0) or 0} filas declaradas; no equivale a series validadas para backtest","yellow"),_card("Escaneo por ciclo",PAPER_ACTIVE_SYMBOL_LIMIT,"Ventana rotativa sobre todo el universo","green"),_card("Último histórico",_local_time(history.get("latest")),"Fecha de descarga, no de publicación original","gray")))
    body=f"<h1>Históricos y universo</h1><div class='paper-grid'>{cards}</div><div class='paper-notice'><b>PPI históricos no debe quedar limitado a 20 instrumentos.</b> Desde esta versión se conserva el catálogo completo y los históricos se descargan por lotes rotativos hasta cubrir todo el universo. Descargar cientos de series en una sola ráfaga elevaría timeouts, cuota y riesgo de bloqueo.</div><div class='paper-card'><h2>Base objetiva para ampliar el lote por ciclo</h2><table class='paper-table'><tr><th>Ciclo</th><th>Seleccionados/elegibles</th><th>Correctos</th><th>Fallidos</th><th>Duración</th><th>Límite recomendado</th></tr>{cycle_rows}</table></div>"
    return _document("Históricos",body+_candle_archive_panel(),refresh=60)


def _candle_archive_panel():
    # Sólo consultas: abrir el panel no materializa, migra ni descarga datos.
    worker=(_rows('SELECT * FROM candle_worker_state WHERE id=1') or [{}])[0] if _table('candle_worker_state') else {}
    state=worker.get('state','NOT_STARTED')
    try:
        if not 0<=(datetime.now(TZ)-datetime.fromisoformat(worker['heartbeat_at'])).total_seconds()<=15:
            state='STALE'
    except (KeyError,ValueError,TypeError):
        state='UNKNOWN'
    inventory=_rows('''SELECT s.identity_json,COUNT(DISTINCT v.bar_start) bars,
      COUNT(*) versions,MIN(v.bar_start) first_bar,MAX(v.bar_end) last_bar
      FROM candle_versions v JOIN candle_series s USING(series_id)
      WHERE julianday(v.known_at)<=julianday(?) AND julianday(v.bar_end)<=julianday(?)
      GROUP BY v.series_id ORDER BY s.identity_json LIMIT 100''',
      (datetime.now(TZ).isoformat(),datetime.now(TZ).isoformat())) if _table('candle_versions') else []
    items=[]
    for r in inventory:
        identity=json.loads(r['identity_json'])
        items.append(f"<tr><td>{_e(identity['symbol'])} / {_e(identity['asset_class'])}</td>"
            f"<td>{_e(identity['market'])} / {_e(identity['currency'])} / {_e(identity['settlement'])} · factor {_e(identity.get('cash_multiplier','UNKNOWN'))}</td>"
            f"<td>{_e(identity['source'])} / {_e(identity['resolution'])}</td>"
            f"<td>{_e(identity['adjustment'])} / {_e(identity['price_kind'])} / {_e(identity['volume_kind'])}</td>"
            f"<td>{r['bars']} / {r['versions']}</td><td>{_local_time(r['last_bar'])}</td></tr>")
    raw=(_rows('SELECT COUNT(*) n FROM historical_raw_archive') or [{'n':0}])[0]['n'] if _table('historical_raw_archive') else 0
    rejected=(_rows('SELECT COUNT(*) n FROM candle_rejections') or [{'n':0}])[0]['n'] if _table('candle_rejections') else 0
    attempts=_rows('SELECT * FROM production_history_attempts ORDER BY attempted_at DESC LIMIT 20') if _table('production_history_attempts') else []
    failed=sum(r['state']!='VALID_PAYLOAD' for r in attempts)
    return ("<div class='paper-card'><h2>Archivo versionado de barras</h2>"
        f"<p>Proceso: <b>{_e(state)}</b> · cursor {_e(worker.get('cursor','—'))} · {_e(worker.get('detail',''))}</p>"
        f"<p>Raw conservados: {raw}. Lecturas excluidas: {rejected}. Descargas no completas entre las últimas {len(attempts)}: {failed}.</p>"
        "<p>TRADE_SAMPLES son muestras del último negocio, no todos los negocios. Su volumen, número de operaciones y VWAP son desconocidos. "
        "No se rellenan huecos. Descargar una serie no confirma ajuste, unidad de volumen ni aptitud para operar.</p>"
        "<p>Las revisiones conservan cuándo se conocieron. Este archivo no aprueba rentabilidad ni habilita órdenes. Se muestran hasta 100 series.</p>"
        "<table class='paper-table'><tr><th>Instrumento</th><th>Identidad financiera</th><th>Fuente / período</th>"
        "<th>Ajuste / precio / volumen</th><th>Barras / versiones</th><th>Hasta</th></tr>"+
        (''.join(items) or "<tr><td colspan='6'>Todavía no hay barras cerradas del archivo nuevo.</td></tr>")+"</table></div>")


def learning_page():
    data=snapshot(); pnl,wins,wr=_trade_metrics(data["closed"])
    rows="".join(f"<tr class='{'card-green' if _num(p.get('net_pnl'))>0 else 'card-red' if _num(p.get('net_pnl'))<0 else 'card-gray'}'><td>{_local_time(p.get('opened_at'))}</td><td><b>{_e(p['symbol'])}</b></td><td>{_status('WIN' if _num(p.get('net_pnl'))>0 else 'LOSS' if _num(p.get('net_pnl'))<0 else p.get('status'))}</td><td class='{'positive' if _num(p.get('net_pnl'))>0 else 'negative' if _num(p.get('net_pnl'))<0 else 'neutral'}'>{_money(p.get('net_pnl'))} {_e(p.get('currency','ARS'))}</td><td>{_e(p.get('close_reason'))}</td></tr>" for p in data["closed"][:100]) or "<tr><td colspan='5'>Sin muestras cerradas.</td></tr>"
    cards="".join((_card("Muestras cerradas",len(data["closed"]),"Etiquetas para aprendizaje","green" if data["closed"] else "gray"),_card("Win rate","s/d" if wr is None else f"{wr:.1f}%",f"{wins}/{len(data['closed'])}","green" if wr is not None and wr>=50 else "red" if wr is not None else "gray"),_card("Resultado ARS",_money(pnl),"Neto de costos y slippage paper","green" if pnl>0 else "red" if pnl<0 else "gray")))
    body=f"<h1>Aprendizaje del sistema</h1><div class='paper-grid'>{cards}</div><div class='paper-notice'>Cada compra simulada conserva variables, decisión IA, portón patrimonial, resultado y lección. El archivo intensivo para discutir con una IA está en Reportes.</div><div class='paper-card'><table class='paper-table'><tr><th>Apertura</th><th>Instrumento</th><th>Etiqueta</th><th>PnL neto</th><th>Motivo</th></tr>{rows}</table></div>"
    return _document("Aprendizaje",_spot_warning(data["spot_state"])+body)


def financial_page():
    latest=_rows("""SELECT f.* FROM financial_series f JOIN (SELECT source,indicator,MAX(observed_date) d FROM financial_series GROUP BY source,indicator) x ON x.source=f.source AND x.indicator=f.indicator AND x.d=f.observed_date ORDER BY f.source,f.indicator""") if _table("financial_series") else []
    values="".join(f"<tr><td><b>{_e(r['indicator'])}</b></td><td>{_e(r['value'])}</td><td>{_e(r['unit'])}</td><td>{_e(r['observed_date'])}</td><td>{_e(r['source'])}</td></tr>" for r in latest) or "<tr><td colspan='5'>Esperando el primer refresco oficial.</td></tr>"
    spot=_spot_snapshot()
    amounts={}
    for p in spot['realized']:
        if p.get('currency','ARS')=='ARS':
            month=aware_datetime(p['closed_at']).astimezone(TZ).strftime('%Y-%m')
            amounts[month]=amounts.get(month,Decimal(0))+Decimal(p['net_pnl'])
    monthly=[{'month':key,'pnl':str(amounts[key])} for key in sorted(amounts,reverse=True)[:24]]
    ipc=_rows("SELECT substr(observed_date,1,7) month,value FROM financial_series WHERE indicator='IPC mensual INDEC' ORDER BY observed_date DESC LIMIT 24") if _table("financial_series") else []
    ipc_map={r['month']:r['value'] for r in ipc}
    compare="".join(f"<tr><td>{_e(r['month'])}</td><td>{_e(ipc_map.get(r['month'],'s/d'))}%</td><td class='{'positive' if _num(r['pnl'])>0 else 'negative' if _num(r['pnl'])<0 else 'neutral'}'>{_money(r['pnl'])} ARS</td><td>NO COMPARABLE: falta rentabilidad porcentual del período</td></tr>" for r in monthly) or "<tr><td colspan='4'>Aún no hay meses cerrados.</td></tr>"
    merval=_rows("SELECT * FROM production_history WHERE symbol IN ('MERVAL','SPMERVAL') ORDER BY downloaded_at DESC LIMIT 1") if _table("production_history") else []
    merval_state="Histórico PPI disponible" if merval else "Pendiente de validación PPI; no se scrapea ni redistribuye BYMA sin licencia"
    body=f"<h1>Información financiera</h1><p class='paper-muted'>Indicadores para preparar la operatoria diaria. Fuentes oficiales BCRA/INDEC y benchmark de mercado por canal autorizado.</p><div class='paper-grid'>{_card('S&P Merval',merval_state,'Benchmark contra performance paper','green' if merval else 'yellow')}{_card('Actualización macro','12 horas','Caché local; la página no llama APIs','green')}</div><div class='paper-card'><h2>Indicadores BCRA e INDEC</h2><table class='paper-table'><tr><th>Indicador</th><th>Valor</th><th>Unidad</th><th>Fecha</th><th>Fuente</th></tr>{values}</table></div><div class='paper-card'><h2>Inflación vs performance del bot</h2><table class='paper-table'><tr><th>Mes</th><th>Inflación mensual</th><th>PnL paper</th><th>Lectura</th></tr>{compare}</table></div><div class='paper-notice'>La comparación correcta a futuro será rentabilidad porcentual del patrimonio paper contra inflación y Merval del mismo período; se mostrará cuando exista un mes completo y un benchmark autorizado con fechas alineadas.</div>"
    return _document("Información financiera",_spot_warning(spot["state"])+body,refresh=300)


def reports_page():
    reports=_rows("SELECT * FROM report_registry ORDER BY period_key DESC,period_type") if _table("report_registry") else []
    rows=[]
    for r in reports:
        pdf=(f"<a class='paper-action' href='/api/reports/{r['id']}/pdf'>Descargar PDF</a>"
             if r.get("pdf_path") else "—")
        ai=(f"<a class='paper-action' href='/api/reports/{r['id']}/ai'>Paquete IA</a>"
            if r.get("ai_path") else "—")
        rows.append(f"<tr><td>{_e(r['period_type'])}</td><td>{_e(r['period_key'])}</td>"
                    f"<td>{_health_status(r['state'])}</td><td>{_local_time(r['created_at'])}</td>"
                    f"<td>{pdf}</td><td>{ai}</td><td>{_e(r['detail'])}</td></tr>")
    rows="".join(rows) or "<tr><td colspan='7'>El primer informe se genera al cierre.</td></tr>"
    body=f"<h1>Reportes</h1><p class='paper-muted'>PDF ejecutivo diario y mensual; el Paquete IA contiene variables, portones, motivos, noticias y lecciones sin secretos.</p><div class='paper-card'><table class='paper-table'><tr><th>Tipo</th><th>Período</th><th>Estado</th><th>Generado</th><th>PDF</th><th>Paquete IA</th><th>Contenido</th></tr>{rows}</table></div><div class='paper-notice'>Los semanales se usan como consolidación transitoria. Al crear el mensual se eliminan los semanales ya integrados; los diarios y mensuales se conservan.</div>"
    return _document("Reportes",body,refresh=300)


def sre_page(section="overview"):
    nav="<div class='subnav'>"+"".join(f"<a href='/sre?section={k}'>{v}</a>" for k,v in (("overview","Resumen"),("database","Base de datos"),("backups","Backups"),("infrastructure","Infraestructura"),("performance","Performance")))+"</div>"
    snap=(_rows("SELECT * FROM sre_snapshots ORDER BY id DESC LIMIT 1") or [{}])[0] if _table("sre_snapshots") else {}
    backups=_rows("SELECT * FROM backup_runs ORDER BY id DESC LIMIT 30") if _table("backup_runs") else []
    db_mb=(_num(snap.get('db_bytes'))+_num(snap.get('wal_bytes')))/1048576; free_pct=_num(snap.get('disk_free_bytes'))/_num(snap.get('disk_total_bytes'),1)*100
    if section=="backups":
        rows="".join(f"<tr><td>{_local_time(r['created_at'])}</td><td>{_e(r['kind'])}</td><td>{_e(Path(r['path']).name)}</td><td>{r['size_bytes']/1048576:.2f} MB</td><td>{_health_status(r['state'])}</td><td>{_e(r['restore_test'])}</td></tr>" for r in backups) or "<tr><td colspan='6'>Sin backup registrado.</td></tr>"
        content=f"<h2>Backups y restauración</h2><div class='paper-card'><table class='paper-table'><tr><th>Fecha</th><th>Tipo</th><th>Archivo</th><th>Tamaño</th><th>Estado</th><th>Restore test</th></tr>{rows}</table></div><div class='paper-notice'>Backup online diario, compresión, SHA-256, descompresión de prueba y PRAGMA quick_check. Retención diaria: 30 días.</div>"
    elif section=="database":
        counts=json.loads(snap.get("payload_json") or "{}").get("tables",{}) if snap else {}
        rows="".join(f"<tr><td>{_e(k)}</td><td>{_e(v)}</td></tr>" for k,v in counts.items())
        wal_mb=_num(snap.get("wal_bytes"))/1048576
        query_ms=_num(snap.get("db_query_ms"))
        integrity=snap.get("db_integrity","sin medición")
        cards="".join((
            _card("SQLite + WAL",f"{db_mb:.2f} MB",f"WAL {wal_mb:.2f} MB","green" if integrity=="ok" else "red"),
            _card("Integridad",integrity,"PRAGMA quick_check","green" if integrity=="ok" else "red"),
            _card("Latencia consulta",f"{query_ms:.2f} ms","Objetivo menor a 250 ms","green" if query_ms<250 else "red"),
        ))
        content=f"<h2>Base de datos</h2><div class='paper-grid'>{cards}</div><div class='paper-card'><table class='paper-table'><tr><th>Tabla</th><th>Filas</th></tr>{rows}</table></div>"
    elif section=="performance":
        cycles=_rows("SELECT * FROM universe_cycle_metrics ORDER BY id DESC LIMIT 100") if _table("universe_cycle_metrics") else []
        durations=[_num(r['duration_seconds']) for r in cycles]; failures=sum(int(r['failed_count']) for r in cycles); selected=sum(int(r['selected_count']) for r in cycles)
        p95=sorted(durations)[max(0,int(len(durations)*.95)-1)] if durations else 0
        failure_rate=failures/max(1,selected)
        memory_bytes=_num(snap.get("memory_rss_bytes"))
        cards="".join((
            _card("Duración p95 del ciclo",f"{p95:.2f} s","Presupuesto: 75% del intervalo","green" if p95<REFRESH_SECONDS*.75 else "red"),
            _card("Errores de instrumentos",f"{failures}/{selected}",f"{failure_rate*100:.1f}%","green" if failure_rate<.05 else "red"),
            _card("Memoria observador",f"{memory_bytes/1048576:.1f} MB","Máximo residente","green" if memory_bytes<512*1048576 else "red"),
        ))
        content=f"<h2>Performance</h2><div class='paper-grid'>{cards}</div>"
    elif section=="infrastructure":
        disk_free_gb=_num(snap.get("disk_free_bytes"))/1073741824
        cards="".join((
            _card("Disco libre",f"{free_pct:.1f}%",f"{disk_free_gb:.2f} GB libres","green" if free_pct>=20 else "red"),
            _card("Dashboard","ACTIVO 24x7","Contenedor independiente y sin credenciales","green"),
            _card("Observador","AISLADO","Sólo lectura PPI; órdenes bloqueadas","green"),
        ))
        content=f"<h2>Infraestructura</h2><div class='paper-grid'>{cards}</div>"
    else:
        last_backup=backups[0] if backups else {}
        integrity=snap.get("db_integrity","sin medición")
        query_ms=_num(snap.get("db_query_ms"),999)
        cards="".join((
            _card("Base",integrity,f"{db_mb:.2f} MB","green" if integrity=="ok" else "red"),
            _card("Último backup",_local_time(last_backup.get("created_at")),last_backup.get("restore_test","sin prueba"),"green" if last_backup.get("state")=="VERDE" else "red"),
            _card("Disco libre",f"{free_pct:.1f}%","Alerta debajo de 20%","green" if free_pct>=20 else "red"),
            _card("Consulta DB",f"{query_ms:.2f} ms","Performance persistida","green" if query_ms<250 else "red"),
        ))
        content=f"<h2>Resumen SRE</h2><div class='paper-grid'>{cards}</div>"
    return _document("SRE",f"<h1>SRE e infraestructura</h1>{nav}{content}",refresh=60)


def telegram_page():
    jobs=_rows("SELECT * FROM operational_jobs WHERE job_key LIKE 'TELEGRAM%' ORDER BY last_run_at DESC LIMIT 30") if _table("operational_jobs") else []
    report=_report_state("telegram"); rows="".join(f"<tr><td>{_e(r['job_key'])}</td><td>{_health_status(r['state'])}</td><td>{_local_time(r['last_run_at'])}</td><td>{_e(r['detail'])}</td></tr>" for r in jobs) or "<tr><td colspan='4'>Aún no hay cierre enviado en esta base.</td></tr>"
    body=f"<h1>Telegram</h1><div class='paper-grid'>{_card('Canal',report[0],report[1],'green' if report[0]=='VERDE' else 'red' if report[0]=='ROJO' else 'gray')}{_card('Autorización paper','NO APLICA','La simulación no espera autorización por Telegram','green')}</div><div class='paper-card'><h2>Resúmenes de cierre</h2><table class='paper-table'><tr><th>Evento</th><th>Estado</th><th>Hora</th><th>Detalle</th></tr>{rows}</table></div>"
    data=snapshot(); worker=data['notification_worker']
    notices=_rows('SELECT * FROM paper_notification_outbox ORDER BY id DESC LIMIT 100') if _table('paper_notification_outbox') else []
    worker_state=worker.get('state','NOT_STARTED')
    try:
        if not 0 <= (datetime.now(TZ)-datetime.fromisoformat(worker['heartbeat_at'])).total_seconds() <= 30:
            worker_state='STALE'
    except (KeyError,ValueError,TypeError):
        worker_state='UNKNOWN'
    delivery=''.join(f"<tr><td>{_e(r['event_key'])}</td><td>{_e(r['state'])}</td>"
        f"<td>{r['attempts']}</td><td>{_local_time(r['sent_at'])}</td><td>{_e(r['last_error'])}</td></tr>" for r in notices)
    body+=("<div class='paper-card'><h2>Cola persistente de avisos</h2>"
        f"<p>Proceso: {_e(worker_state)} · {_e(worker.get('detail',''))}</p>"
        "<p>PENDING espera; SENDING está en vuelo; SENT tiene ACK; DEAD requiere revisión. "
        "Un envío de resultado incierto puede repetirse con el mismo ID. No repite operaciones.</p>"
        "<table class='paper-table'><tr><th>ID</th><th>Estado</th><th>Intentos</th><th>ACK recibido</th><th>Error</th></tr>"
        +(delivery or "<tr><td colspan='5'>Sin avisos nuevos.</td></tr>")+"</table></div>")
    return _document("Telegram",body,refresh=60)


def logs_page():
    log=Path(os.getenv("LOG_DIR","data/logs"))/"trading_bot.log"; size=f"{log.stat().st_size/1024:.1f} KB" if log.exists() else "sin archivo"
    body=f"<h1>Gestión de logs</h1><div class='paper-grid'>{_card('Log operativo',size,str(log),'green' if log.exists() else 'gray')}{_card('Métricas de universo','PERSISTIDAS','SRE → Performance','green')}</div><div class='paper-notice'>El output del instalador y de los cambios de modo se copia automáticamente al portapapeles; los detalles extensos quedan en logs.</div>"
    return _document("Logs",body,refresh=None)


def _authorize(check_auth,request,token,authorization):
    try: check_auth(token,authorization,request.cookies.get("porota_dashboard_session"))
    except TypeError: check_auth(token,authorization)


def install(app,check_auth):
    global _installed
    if _installed or MODE not in MODE_INFO: return
    _installed=True
    def auth(request,token,authorization): _authorize(check_auth,request,token,authorization)
    @app.get("/observacion",response_class=HTMLResponse)
    def observacion(request:Request,token:str=Query(default=""),authorization:str|None=Header(default=None)): auth(request,token,authorization); return HTMLResponse(paper_page(True))
    @app.get("/motor-trading",response_class=HTMLResponse)
    def motor(request:Request,token:str=Query(default=""),authorization:str|None=Header(default=None)): auth(request,token,authorization); return HTMLResponse(motor_page())
    @app.get("/informacion-financiera",response_class=HTMLResponse)
    def financial(request:Request,token:str=Query(default=""),authorization:str|None=Header(default=None)): auth(request,token,authorization); return HTMLResponse(financial_page())
    @app.get("/reportes",response_class=HTMLResponse)
    def reports(request:Request,token:str=Query(default=""),authorization:str|None=Header(default=None)): auth(request,token,authorization); return HTMLResponse(reports_page())
    @app.get("/api/observer/state")
    def observer_state(request:Request,token:str=Query(default=""),authorization:str|None=Header(default=None)): auth(request,token,authorization); return JSONResponse(snapshot())
    @app.get("/api/paper/caucion-allocations")
    def caucion_allocations(request:Request,limit:int=Query(default=25,ge=1,le=100),
                            offset:int=Query(default=0,ge=0,le=100000),
                            token:str=Query(default=""),authorization:str|None=Header(default=None)):
        auth(request,token,authorization)
        data = allocation_history(DB_PATH,limit=limit,offset=offset)
        return JSONResponse(data,status_code=503 if data['state']=='READ_ERROR' else 200)
    @app.get("/api/reports/{report_id}/{kind}")
    def report_download(report_id:int,kind:str,request:Request,token:str=Query(default=""),authorization:str|None=Header(default=None)):
        auth(request,token,authorization)
        if kind not in {"pdf","ai"}: raise HTTPException(404,"Tipo no disponible")
        rows=_rows("SELECT pdf_path,ai_path FROM report_registry WHERE id=?",(report_id,)); path=Path(rows[0]["pdf_path" if kind=="pdf" else "ai_path"]) if rows and rows[0].get("pdf_path" if kind=="pdf" else "ai_path") else None
        root=Path("data/reports").resolve()
        if not path or not path.resolve().is_relative_to(root) or not path.exists(): raise HTTPException(404,"Informe no disponible")
        return FileResponse(path,media_type="application/pdf" if kind=="pdf" else "application/json",filename=path.name)
    @app.middleware("http")
    async def paper_truth(request,call_next):
        response=await call_next(request); ctype=response.headers.get("content-type","")
        if "text/html" not in ctype or response.status_code>=400: return response
        body=b"".join([chunk async for chunk in response.body_iterator]); content=body.decode("utf-8","replace")
        # /vivo es el nombre histórico de la actividad en tiempo real. En
        # simulación productiva debe ser un alias real del panel consolidado,
        # no una vista heredada meramente retocada por _canonicalize().
        replacements={"/":home_page,"/vivo":lambda:paper_page(True),"/testing":lambda:paper_page(True),"/salud":health_page,"/historicos":history_page,"/aprendizaje":learning_page,"/telegram":telegram_page,"/dashboard/logs":logs_page}
        if request.url.path=="/sre": content=sre_page(request.query_params.get("section","overview"))
        elif request.url.path in replacements: content=replacements[request.url.path]()
        else: content=_canonicalize(content,request.url.path)
        headers=dict(response.headers); headers.pop("content-length",None)
        return HTMLResponse(_dedupe_refresh(content),status_code=response.status_code,headers=headers)
