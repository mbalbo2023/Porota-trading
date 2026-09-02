"""Dashboard 24x7 v17 RC3, independiente y sin credenciales PPI."""

from __future__ import annotations

import html
import json
import os
import re
import sqlite3
import statistics
from contextlib import closing
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from bl_candle_engine import fingerprint
from cb_caucion_audit import allocation_history
from ch_empirical_learning import empirical_expectancy
from ci_operational_context import breadth_observation, sector_observation
import cd_spot_ledger as spot_ledger
from bs_instrument_contracts import aware_datetime
from bt_caucion_paper import validate_position, pending_proceeds
from cg_paper_workspace import database_path, checked_path, identity_from_connection, artifact_root
from _version import VERSION


DB_PATH = str(database_path())
CONFIGURED_MODE = os.getenv("DASHBOARD_OPERATION_MODE", "DETENIDO").upper()
# Alias de compatibilidad para fixtures antiguas; la aplicación viva usa
# _effective_mode() y no confía en este valor congelado.
MODE = CONFIGURED_MODE
TZ = ZoneInfo(os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires"))
PAPER_INITIAL_CAPITALS = {
    "ARS": float(os.getenv("PAPER_INITIAL_CAPITAL_ARS", "1000000")),
    "USD": float(os.getenv("PAPER_INITIAL_CAPITAL_USD", "0")),
    "USD_MEP": float(os.getenv("PAPER_INITIAL_CAPITAL_USD_MEP", "0")),
    "USD_CCL": float(os.getenv("PAPER_INITIAL_CAPITAL_USD_CCL", "0")),
}
PAPER_INITIAL_CAPITAL = PAPER_INITIAL_CAPITALS["ARS"]
PAPER_ACTIVE_SYMBOL_LIMIT = int(os.getenv("PAPER_ACTIVE_SYMBOL_LIMIT", "20"))
PAPER_ECONOMIC_GATE_MODE = os.getenv("PAPER_ECONOMIC_GATE_MODE", "BINDING").upper()
PAPER_SCALPING_MODE = os.getenv("PAPER_SCALPING_MODE", "ACTIVE_OBSERVE").upper()
REFRESH_SECONDS = max(0, int(os.getenv("DASHBOARD_REFRESH_SECONDS", "30")))
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
.system-layout{display:grid;grid-template-columns:minmax(180px,230px) minmax(0,1fr);gap:14px;align-items:start}
.system-nav{position:sticky;top:64px;display:flex;flex-direction:column;gap:7px}.system-nav a{background:#fff;
border:1px solid var(--line);border-radius:9px;padding:10px 12px;text-decoration:none;color:var(--blue);font-weight:750}
.system-nav a.active{background:var(--nav);color:#fff}.system-content{min-width:0}
details.paper-trade{background:#fff;border:1px solid var(--line);border-radius:11px;margin:10px 0;overflow:hidden}
details.paper-trade>summary{cursor:pointer;padding:13px 15px;font-weight:750;background:#f8fafc;list-style-position:inside}
.trade-body{padding:4px 15px 15px}.timeline{border-left:3px solid #bcc8d8;padding-left:15px;margin:10px 0}.timeline>div{margin:10px 0}
.paper-footer{max-width:1280px;margin:10px auto 30px;padding:0 18px;text-align:right}.up-link{display:inline-block;background:var(--nav);
color:#fff!important;padding:10px 14px;border-radius:9px;text-decoration:none;font-weight:800}
code{white-space:normal;overflow-wrap:anywhere}.legacy-shell{background:transparent}.legacy-shell>h1{margin-top:4px}
.legacy-shell table{width:100%!important;border-collapse:collapse!important}.legacy-shell th,.legacy-shell td{padding:9px!important;border-bottom:1px solid #e5e9f0!important}
@media(max-width:700px){#porota-canonical-nav{position:relative}.paper-page{padding:12px}.paper-table{font-size:.77rem}.paper-table th,.paper-table td{padding:6px}.system-layout{grid-template-columns:1fr}.system-nav{position:static}}
</style>"""

MODE_INFO = {
    "PRODUCTION_PAPER": ("MODO SIMULACIÓN PRODUCTIVA", "PPI Producción solo lectura; compras y ventas simuladas; órdenes reales: NINGUNA."),
    "SANDBOX": ("MODO SANDBOX", "PPI Sandbox; únicamente operaciones del entorno de pruebas."),
    "PRODUCTION_REAL": ("ESTADO INVÁLIDO — PRODUCCIÓN REAL BLOQUEADA", "Por política permanente, Porota nunca puede enviar órdenes con dinero real."),
    "DETENIDO": ("PLATAFORMA DETENIDA", "Dashboard disponible; ningún motor de trading está activo."),
}


def _e(value):
    return html.escape(str(value if value not in (None, "") else "—"))


def _num(value, default=0.0):
    try: return float(value)
    except Exception: return default


def _money(value):
    try: return "$ " + _locale_number(value)
    except Exception: return "—"


def _amount(value, currency):
    labels = {"ARS": "ARS $", "USD": "USD", "USD_MEP": "USD MEP", "USD_CCL": "USD CCL"}
    try: return f"{labels.get(currency, currency)} {_locale_number(value)}"
    except Exception: return "—"


def _locale_number(value, decimals=2):
    """Formato visual es-AR; SQLite/JSON conservan punto decimal canónico."""
    raw=f"{float(value):,.{decimals}f}"
    return raw.replace(",", "_").replace(".", ",").replace("_", ".")


def _local_time(value):
    if not value: return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=TZ)
        return parsed.astimezone(TZ).strftime("%d/%m/%Y %H:%M:%S")
    except Exception: return _e(value)


def _conn(path=None):
    c = sqlite3.connect(checked_path(path or DB_PATH).as_uri()+'?mode=ro', uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    try:
        identity_from_connection(c)
    except Exception:
        c.close()
        raise
    return c


def _rows(sql, params=(), path=None):
    try:
        with closing(_conn(path)) as c: return [dict(r) for r in c.execute(sql, params).fetchall()]
    except Exception: return []


def _table(name, path=None):
    try:
        with closing(_conn(path)) as c: return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())
    except Exception: return False


def _effective_mode():
    """Resolver el modo desde evidencia viva, no desde un env congelado.

    El manifiesto es la fuente autoritativa. Si falta o es inválido, el estado
    persistido del observador permite mostrar la situación sin inventarla. El
    valor del contenedor queda expuesto como diagnóstico, nunca como verdad.
    """
    if MODE != CONFIGURED_MODE and MODE in MODE_INFO:
        return MODE
    manifest = Path(DB_PATH).parents[1] / "operation_mode.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        mode = str(payload.get("mode") or "").upper()
        if mode in MODE_INFO:
            return mode
    except (OSError, ValueError, TypeError):
        pass
    rows = _rows("SELECT mode FROM observer_state WHERE id=1") if _table("observer_state") else []
    observed = str((rows[0] if rows else {}).get("mode") or "").upper()
    return observed if observed in MODE_INFO else CONFIGURED_MODE


def _mode_mismatch():
    effective = _effective_mode()
    return effective != CONFIGURED_MODE, effective


def _spot_warning(state):
    return "" if state=="READY" else "<div class='paper-warning'>No se pudo conciliar el ledger spot. Cantidades y resultados no disponibles; no interpretar como cero.</div>"


def _spot_snapshot():
    """Cantidades/PnL consistentes; los parciales no multiplican el win rate."""
    try:
        with closing(_conn()) as c:
            c.execute('BEGIN')
            opened, realized = spot_ledger.positions_at(c)
            closed = [dict(r) for r in c.execute("SELECT * FROM paper_positions WHERE status='CLOSED' ORDER BY closed_at DESC")]
            for currency in {p['currency'] for p in realized}:
                pending_proceeds(None,datetime.now(TZ).isoformat(),currency,connection=c)
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
          "s-amarillo" if key in {"HOLD","PARTIAL","COOLDOWN","WAITING","AMARILLO","PENDIENTE"} else "s-gris"
    return f"<span class='paper-status {css}'>{_e(key or 'GRIS')}</span>"


def _health_status(value):
    key = str(value or "GRIS").upper()
    if key == "PENDIENTE":
        return _status("PENDIENTE")
    if key in {"NO_APLICA", "NOT_APPLICABLE"}:
        return _status("NO_APLICA")
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
    mismatch, mode = _mode_mismatch()
    title, detail = MODE_INFO.get(mode, (f"MODO {mode}", "Estado operativo no reconocido."))
    dataset = (' Dataset PAPER v17 independiente: una migración verificada puede conservar catálogo e históricos; '
               'nunca traslada posiciones, saldos ni aprendizaje operativo.'
               if mode in {'PRODUCTION_PAPER', 'DETENIDO'} else '')
    refresh_text = (f"Actualización parcial: cada {REFRESH_SECONDS} s; conserva detalles y foco."
                    if REFRESH_SECONDS else
                    "Actualización visual única y manual; no interrumpe la lectura por voz.")
    warning = (f" Configuración del contenedor={CONFIGURED_MODE}; manifiesto/runtime={mode}. "
               "Se muestra la verdad operativa y se requiere regenerar el env."
               if mismatch else "")
    return (f"<div id='porota-paper-mode'>{_e(title)}<small>{_e(detail)} "
            f"{refresh_text}{_e(dataset) if dataset else ''}{_e(warning)}</small></div>")


def _nav():
    links = (("/", "Panel"), ("/en-vivo", "En vivo"), ("/motor-trading", "Motor de trading"),
             ("/scalping", "Scalping"),
             ("/historicos", "Históricos"), ("/aprendizaje", "Aprendizaje"),
             ("/informacion-financiera", "Información financiera"), ("/reportes", "Reportes"),
             ("/sistema", "Sistema"))
    return "<nav id='porota-canonical-nav'>" + "".join(f"<a href='{href}'>{label}</a>" for href, label in links) + "</nav>"


def _dedupe_refresh(content):
    content = re.sub(r"<meta[^>]+http-equiv=['\"]refresh['\"][^>]*>", "", content,
                     flags=re.I)
    content = re.sub(r"<(?:div|p)[^>]*>[^<]*Actualizado:.*?Próxima actualización:.*?</(?:div|p)>", "", content, flags=re.I|re.S)
    content = re.sub(r"<(?:div|p)[^>]*>[^<]*Próxima actualización:.*?</(?:div|p)>", "", content, flags=re.I|re.S)
    return content


def _document(title, body, refresh=REFRESH_SECONDS):
    interval = min(REFRESH_SECONDS, int(refresh or REFRESH_SECONDS)) if REFRESH_SECONDS else 0
    script = ""
    if interval:
        script = f"""<script>(function(){{
        const seconds={interval};
        async function refreshPorota(){{
          if(document.hidden || document.querySelector('dialog[open]') ||
             document.activeElement?.closest('.trade-body')) return;
          const opened=[...document.querySelectorAll('details.paper-trade[open]')]
            .map(x=>x.querySelector('[data-trade-id]')?.dataset.tradeId).filter(Boolean);
          const y=window.scrollY;
          try{{
            const response=await fetch(location.href,{{headers:{{'X-Porota-Partial':'1'}},cache:'no-store'}});
            if(!response.ok) return;
            const parsed=new DOMParser().parseFromString(await response.text(),'text/html');
            const next=parsed.querySelector('main.paper-page');
            const current=document.querySelector('main.paper-page');
            const banner=parsed.querySelector('#porota-paper-mode');
            if(next&&current) current.replaceWith(next);
            if(banner&&document.querySelector('#porota-paper-mode'))
              document.querySelector('#porota-paper-mode').replaceWith(banner);
            opened.forEach(id=>{{
              const node=[...document.querySelectorAll('details.paper-trade')]
                .find(x=>x.querySelector(`[data-trade-id="${{CSS.escape(id)}}"]`));
              if(node)node.open=true;
            }});
            window.scrollTo(0,y);
          }}catch(_error){{}}
        }}
        window.setInterval(refreshPorota,seconds*1000);
        window.refreshPorota=refreshPorota;
        }})();</script>"""
    controls = ("<div class='paper-actions'><a class='paper-action' "
                "href='javascript:window.refreshPorota?window.refreshPorota():location.reload()' id='actualizar-pagina'>"
                "🔄 Actualizar página</a></div>")
    return ("<!doctype html><html lang='es'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{_e(title)}</title>{THEME}</head><body id='top'>{_nav()}{mode_banner()}"
            f"<main class='paper-page'>{controls}{body}</main><footer class='paper-footer'><a class='up-link' href='#top'>↑ Ir al principio</a></footer>{script}</body></html>")


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
    state = (_rows("SELECT * FROM observer_state WHERE id=1") or [{"mode":_effective_mode(),"process_state":"STOPPED","session_state":"UNKNOWN","ppi_auth":"NOT_ATTEMPTED","real_orders_sent":0,"detail":"Observador todavía no iniciado."}])[0]
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
    if spot['state']!='READY' or caucion_data['state']!='READY':
        # No publicar una cifra anterior como si siguiera conciliada.
        balances, equity = [], {}
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
    soft_pct = Decimal(os.getenv("PAPER_DAILY_SOFT_STOP_PCT", "1.5"))
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
        try:
            soft_budget = Decimal(r['baseline_equity']) * soft_pct / 100
            soft_state = ("ALCANZADO — sin nuevas aperturas"
                          if r['daily_pnl'] is not None and Decimal(r['daily_pnl']) <= -soft_budget
                          else "DISPONIBLE")
            soft_text = f"{soft_budget} ({soft_pct}%) · {soft_state}"
        except (InvalidOperation, TypeError, ValueError):
            soft_text = f"Sin base ({soft_pct}%)"
        items.append(f"<tr><td>{_e(r['currency'])}</td><td>{_e(state)}</td>"
            f"<td>{_e(r['baseline_equity'] or 'Sin base')}</td><td>{_e(r['daily_pnl'] or 'Sin valuación')}</td>"
            f"<td>{_e(soft_text)}</td><td>{_e(r['loss_budget'] or '—')} ({_e(r['limit_pct'])}%)</td>"
            f"<td>{_e(r['detail'])}</td></tr>")
    return ("<div class='paper-card'><h2>Corte diario por moneda</h2>"
            "<p>El freno blando suspende aperturas sin liquidar; el límite duro crea salidas y sobrevive reinicios. No mezcla monedas ni libera cauciones antes del vencimiento. "
            "Sin base o cotizaciones confiables se suspenden nuevas entradas; las salidas continúan.</p>"
            "<table class='paper-table'><tr><th>Moneda</th><th>Estado</th><th>Base</th><th>PnL neto diario</th>"
            "<th>Freno de aperturas</th><th>Corte duro</th><th>Detalle</th></tr>"+(''.join(items) or
            "<tr><td colspan='7'>Sin evaluación del día actual; no asumir habilitación.</td></tr>")+"</table></div>")


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
    data = allocation_history(DB_PATH, require_workspace=True)
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
    if data['caucion_state']!='READY' or data['spot_state']!='READY':
        return ("<div class='paper-card'><h2>Caja y patrimonio por moneda</h2>"
                +_spot_warning(data['spot_state'])+_caucion_warning(data['caucion_state'])+'</div>')
    balances = data["balances_by_currency"]
    quality = {r['currency']:r for r in data["valuation_quality"]}
    today = datetime.now(TZ).date()
    realized_today = {}
    for position in data.get("closed", []):
        try:
            if aware_datetime(position.get("closed_at")).astimezone(TZ).date() == today:
                currency = position.get("currency", "ARS")
                realized_today[currency] = (realized_today.get(currency, Decimal("0"))
                                            + Decimal(str(position.get("net_pnl") or "0")))
        except (InvalidOperation, ValueError, TypeError):
            continue
    rows = "".join(f"<tr><td>{_e(r['currency'])}</td><td>{_e(r['cash'])}</td>"
                   f"<td>{_e(r['pending_proceeds'])}</td><td>{_e(r['caucion_principal'])}</td>"
                   f"<td>{_e(realized_today.get(r['currency'],0))}</td>"
                   f"<td>{_e(r['realized_pnl'])}</td><td>{_e(r['equity'])}</td>"
                   f"<td>{_e(quality.get(r['currency'],{}).get('state','UNKNOWN'))} · "
                   f"{_local_time(quality.get(r['currency'],{}).get('measured_at'))}</td></tr>" for r in balances)
    rows = rows or "<tr><td colspan='8'>Pendiente de la primera valuación por moneda.</td></tr>"
    return ("<div class='paper-card'><h2>Caja y patrimonio por moneda</h2>"
            "<p>Sin conversión automática: pesos, dólar billete/MEP, divisa/CCL y USD sin plaza "
            "no se suman ni se prestan saldo entre sí. Sólo cauciones colocadoras; sin financiación.</p>"
            "<table class='paper-table'><tr><th>Moneda/plaza</th><th>Disponible</th>"
            "<th>Ventas pendientes de liquidación</th><th>Capital caucionado</th>"
            "<th>PnL realizado hoy</th><th>PnL realizado acumulado</th>"
            "<th>Patrimonio estimado</th><th>Valuación</th></tr>" + rows + "</table>"
            "<p class='paper-muted'>Una venta A-24HS ya ejecutada permanece como crédito pendiente; "
            "no es una orden de venta abierta. HF6 mostrará por separado el horizonte diario y el acumulado.</p>"
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


def _mode_applies(component_mode):
    label = str(component_mode or "").upper()
    mode = _effective_mode()
    if label == "TODOS":
        return True
    if mode == "PRODUCTION_PAPER":
        return "SIMULACIÓN" in label
    if mode == "SANDBOX":
        return "SANDBOX" in label
    return False


def _effective_health_state(raw_state, *, present, applicable):
    if not applicable:
        return "NO_APLICA"
    key = str(raw_state or "").upper()
    if key in {"NO_APLICA", "NOT_APPLICABLE", "DISABLED"}:
        return "NO_APLICA"
    if not present or key in {"", "GRIS", "UNKNOWN", "NOT_ATTEMPTED", "NOT_STARTED"}:
        return "PENDIENTE"
    if key in {"OK", "SUCCESS", "HEALTHY", "VERDE"}:
        return "VERDE"
    if key in {"ERROR", "FAIL", "FAILED", "ROJO"}:
        return "ROJO"
    if key in {"PARTIAL", "DEGRADED", "COOLDOWN", "AMARILLO"}:
        return "AMARILLO"
    return "PENDIENTE"


def _health_components():
    persisted = {r["component"]: r for r in _rows("SELECT * FROM api_health")} if _table("api_health") else {}
    jobs = {r["job_key"]: r for r in _rows("SELECT * FROM operational_jobs")} if _table("operational_jobs") else {}
    components = []
    observer = (_rows("SELECT * FROM observer_state WHERE id=1") or [{}])[0] if _table("observer_state") else {}

    def add(name, key, mode, use, default="Sin verificación persistida."):
        row = persisted.get(key) or jobs.get(key)
        applicable = _mode_applies(mode)
        raw = row.get("state") if row else None
        checked = (row or {}).get("checked_at") or (row or {}).get("last_run_at")
        detail = (row or {}).get("detail", default)
        state = _effective_health_state(raw, present=bool(row), applicable=applicable)
        if state == "NO_APLICA":
            detail = f"No aplica al modo {_effective_mode()}; no es una falla."
        elif state == "PENDIENTE" and not row:
            detail = "Esperando la primera verificación persistida; no se considera saludable todavía."
        components.append({"name": name, "key": key, "state": state, "raw_state": raw,
            "detail": detail, "checked": checked, "last_success": (row or {}).get("last_success_at"),
            "next_check": _next_check(key, checked), "mode": mode, "use": use,
            "applicable": applicable})

    add("PPI Sandbox — autenticación", "PPI_SANDBOX_AUTH", "SANDBOX",
        "Un login aislado; cero cuenta y cero órdenes")
    add("PPI Producción — autenticación", "PPI_PRODUCTION_AUTH", "SIMULACIÓN PRODUCTIVA",
        "Login de sólo lectura")
    add("PPI Producción — catálogo", "PPI_PRODUCTION_CATALOG", "SIMULACIÓN PRODUCTIVA",
        "Descubrimiento de instrumentos")
    add("PPI Producción — históricos", "PPI_PRODUCTION_HISTORY", "SIMULACIÓN PRODUCTIVA",
        "Cobertura incremental de todo el universo")
    add("PPI Producción — ingesta histórica 24x7", "PPI_BACKGROUND_INGEST", "SIMULACIÓN PRODUCTIVA",
        "Lotes históricos fuera de rueda con TTL y sin current/book")
    ppi_error_types = ("DATA_ERROR", "EXIT_BOOK_ERROR", "EXIT_READER_LOGIN_ERROR",
                       "EXIT_READER_SESSION_INVALID", "PPI_SESSION_INVALID")
    placeholders = ",".join("?" for _ in ppi_error_types)
    recent_errors = (_rows(f"""SELECT event_type,COUNT(*) count,MAX(event_at) latest
      FROM paper_events WHERE event_type IN ({placeholders})
      AND julianday(event_at)>=julianday(?) GROUP BY event_type ORDER BY count DESC""",
      (*ppi_error_types,(datetime.now(TZ)-timedelta(hours=1)).isoformat()))
      if _table("paper_events") else [])
    total_errors = sum(int(row["count"]) for row in recent_errors)
    latest_error = max((str(row.get("latest") or "") for row in recent_errors), default="") or None
    components.append({
        "name": "PPI Producción — errores recientes", "key": "PPI_RECENT_ERRORS",
        "state": "AMARILLO" if total_errors else "VERDE", "raw_state": "WARN" if total_errors else "OK",
        "detail": (" · ".join(f"{row['event_type']}={row['count']}" for row in recent_errors)
                   if total_errors else "Sin errores PPI clasificados durante la última hora."),
        "checked": datetime.now(TZ).isoformat(), "last_success": None if total_errors else datetime.now(TZ).isoformat(),
        "next_check": "Continuo; ventana móvil de una hora", "mode": "SIMULACIÓN PRODUCTIVA",
        "use": "Diagnóstico de market data read-only; nunca habilita órdenes", "applicable": True,
        "latest_error": latest_error,
    })
    add("PPI Producción — market data", "PPI_PRODUCTION_MARKETDATA", "SIMULACIÓN PRODUCTIVA",
        "Cotización y caja de puntas")
    if components[-1]["applicable"] and observer.get("session_state") != "MARKET_OPEN":
        components[-1].update(
            state="NO_APLICA", applicable=False,
            detail="Rueda cerrada: current/book en vivo no se exige. Autenticación, catálogo e históricos continúan por separado.",
            next_check="Al abrir la próxima rueda")
    add("Cobertura del foco PAPER", "PAPER_FOCUS_COVERAGE", "SIMULACIÓN PRODUCTIVA",
        "Verifica todas las identidades prioritarias configuradas y bloquea aperturas si hay menos de cuatro")
    add("Muestreo del foco PAPER", "PAPER_SIGNAL_SAMPLING", "SIMULACIÓN PRODUCTIVA",
        "Capacidad de reunir seis muestras dentro de 90 minutos")
    add("Muestreo del universo rotativo", "PAPER_SIGNAL_ROTATION", "SIMULACIÓN PRODUCTIVA",
        "Cadencia estimada de instrumentos no prioritarios")
    add(f"Economía matemática {PAPER_ECONOMIC_GATE_MODE}", "PAPER_ECONOMIC_GATE_SHADOW", "SIMULACIÓN PRODUCTIVA",
        "BINDING debe bloquear toda apertura cuya economía resulte rechazada")

    tg = _report_state("telegram")
    tg_mode = "TODOS"
    tg_applicable = _mode_applies(tg_mode)
    tg_present = bool(tg[2])
    components.append({"name": "Telegram", "key": "TELEGRAM", "state":
        _effective_health_state(tg[0], present=tg_present, applicable=tg_applicable),
        "raw_state": tg[0], "detail": tg[1], "checked": tg[2], "last_success": tg[3],
        "next_check": _next_check("TELEGRAM", tg[2]), "mode": tg_mode,
        "use": "Avisos de modo y resumen de cierre", "applicable": tg_applicable})

    add("BCRA / INDEC", "FINANCIAL_REFRESH", "TODOS", "Información financiera oficial")
    add("OPENBYMADATA / BYMA", "BYMA_OPEN_DATA", "TODOS",
        "Referencia pública oficial; sin redistribuir market data")
    add("Feeds de noticias", "NEWS_REFRESH", "TODOS",
        "Deshabilitado por política; no interviene en decisiones ni aprendizaje")
    add("Base SQLite paper", "SRE_SNAPSHOT", "TODOS", "Persistencia e integridad")
    return components


def _daily_summary_panel(data=None):
    data = data or snapshot()
    if data["spot_state"] != "READY" or data["caucion_state"] != "READY":
        return ("<div class='paper-card'><h2>Resumen simulado del día</h2>"
                + _spot_warning(data["spot_state"]) + _caucion_warning(data["caucion_state"]) + "</div>")
    today = datetime.now(TZ).date()
    currencies = ("ARS", "USD", "USD_MEP", "USD_CCL")
    totals = {currency: {"buys": 0, "sells": 0, "realized": 0.0, "open": 0,
                         "unrealized": 0.0} for currency in currencies}
    fills = _rows("""SELECT f.side,f.filled_at,p.currency FROM paper_fills f
                     JOIN paper_positions p ON p.paper_id=f.paper_id
                     ORDER BY f.id DESC LIMIT 2000""") if _table("paper_fills") else []
    for fill in fills:
        try:
            if aware_datetime(fill["filled_at"]).astimezone(TZ).date() != today:
                continue
        except Exception:
            continue
        currency = fill.get("currency", "ARS")
        if currency not in totals:
            continue
        if fill.get("side") == "BUY_SIMULATED":
            totals[currency]["buys"] += 1
        elif fill.get("side") == "SELL_SIMULATED":
            totals[currency]["sells"] += 1
    for sale in data["realized"]:
        try:
            if aware_datetime(sale["closed_at"]).astimezone(TZ).date() == today:
                totals[sale.get("currency", "ARS")]["realized"] += _num(sale.get("net_pnl"))
        except (KeyError, ValueError, TypeError):
            continue
    for position in data["open"]:
        currency = position.get("currency", "ARS")
        if currency in totals:
            totals[currency]["open"] += 1
    for balance in data["balances_by_currency"]:
        currency = balance.get("currency")
        if currency in totals:
            totals[currency]["unrealized"] = _num(balance.get("unrealized_pnl"))
    rows = "".join(
        f"<tr><td><b>{_e(currency)}</b></td><td>{values['buys']}</td>"
        f"<td>{values['sells']}</td><td>{values['open']}</td>"
        f"<td class='{'positive' if values['realized']>0 else 'negative' if values['realized']<0 else 'neutral'}'>"
        f"{_amount(values['realized'],currency)}</td>"
        f"<td class='{'positive' if values['unrealized']>0 else 'negative' if values['unrealized']<0 else 'neutral'}'>"
        f"{_amount(values['unrealized'],currency)}</td></tr>"
        for currency, values in totals.items())
    return ("<div class='paper-card'><h2>Resumen simulado del día</h2>"
            "<p>Cuenta fills PAPER, no órdenes enviadas a PPI. Los resultados no mezclan ni convierten monedas.</p>"
            "<table class='paper-table'><tr><th>Moneda</th><th>Compras simuladas</th>"
            "<th>Ventas simuladas</th><th>Posiciones abiertas</th><th>PnL realizado hoy</th>"
            "<th>PnL no realizado actual</th></tr>" + rows + "</table></div>")


def home_page():
    data = snapshot()
    state = data["state"]
    heartbeat_ok = _fresh(state.get("heartbeat_at"), 180)
    sre = (_rows("SELECT * FROM sre_snapshots ORDER BY id DESC LIMIT 1") or [{}])[0] if _table("sre_snapshots") else {}
    db_ok = sre.get("db_integrity") == "ok"
    financial_ready = data["caucion_state"] == "READY" and data["spot_state"] == "READY"
    health = _health_components()
    applicable = [item for item in health if item["applicable"]]
    red = sum(item["state"] == "ROJO" for item in applicable)
    pending = sum(item["state"] in {"PENDIENTE", "AMARILLO"} for item in applicable)
    overall = heartbeat_ok and db_ok and financial_ready and red == 0 and pending == 0
    overall_label = ("TODO OPERATIVO" if overall else "REVISAR" if red or not heartbeat_ok or not db_ok
                     or not financial_ready else "VERIFICACIONES PENDIENTES")
    overall_color = "green" if overall else "red" if overall_label == "REVISAR" else "yellow"
    telegram = next((item["state"] for item in health if item["key"] == "TELEGRAM"), "PENDIENTE")
    balances = {row["currency"]: row for row in data["balances_by_currency"]}
    balance_cards = []
    for currency in ("ARS", "USD", "USD_MEP", "USD_CCL"):
        row = balances.get(currency)
        value = _num((row or {}).get("equity"), PAPER_INITIAL_CAPITALS[currency])
        ready = financial_ready and row is not None
        initial = PAPER_INITIAL_CAPITALS[currency]
        balance_cards.append(_card(
            f"Patrimonio paper {currency}", _amount(value, currency) if ready else "s/d",
            f"Capital ficticio inicial {_amount(initial,currency)}; caja independiente",
            "gray" if not ready else "green" if value >= initial else "red"))
    real_orders = int(state.get("real_orders_sent") or 0)
    cards = "".join((
        _card("Estado general", overall_label,
              f"{red} fuentes en rojo; {pending} pendientes/degradadas; NO APLICA no cuenta como falla",
              overall_color),
        _card("Dashboard 24x7", "ACTIVO", "Esta página responde aunque la rueda esté cerrada", "green"),
        _card("Observador / simulador", "ACTIVO" if heartbeat_ok else "SIN LATIDO",
              f"Último latido {_local_time(state.get('heartbeat_at'))}", "green" if heartbeat_ok else "red"),
        _card("Telegram", telegram, "Avisos de modo y resumen de cierre",
              "green" if telegram == "VERDE" else "red" if telegram == "ROJO" else "yellow"),
        _card("Base paper", "OK" if db_ok else "REVISAR",
              f"Integridad {sre.get('db_integrity','sin medición')}", "green" if db_ok else "red"),
        _card("PPI solo lectura", state.get("ppi_auth"), "Órdenes reales bloqueadas por transporte",
              "green" if state.get("ppi_auth") == "OK" else "yellow"
              if state.get("ppi_auth") in {"NOT_ATTEMPTED","COOLDOWN"} else "red"),
        *balance_cards,
        _card("Órdenes reales", real_orders, "Debe permanecer siempre en cero",
              "green" if real_orders == 0 else "red"),
    ))
    body = (f"<h1>Panel ejecutivo — Porota Trading {VERSION}</h1>"
            "<div class='paper-warning'><b>Ejecución exclusivamente simulada.</b> "
            "Producción significa datos reales y servicio continuo; nunca dinero real.</div>"
            "<p class='paper-muted'>Una sola vista del sistema, infraestructura, APIs y desempeño paper.</p>"
            f"<div class='paper-grid'>{cards}</div>")
    return _document("Porota Trading", _spot_warning(data["spot_state"])
        + _caucion_warning(data["caucion_state"]) + body
        + _daily_summary_panel(data) + _balances_panel())

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


def _economics_status(payload):
    economics = _features(payload).get("economics", {})
    if not economics:
        return "SIN_REGISTRO"
    return "APPROVE" if economics.get("passed") else \
           "BLOCKED" if PAPER_ECONOMIC_GATE_MODE == "BINDING" else "SHADOW_REVIEW"


def _economic_shadow_metrics():
    today = datetime.now(TZ).date()
    result = {"evaluated": 0, "passed": 0, "failed": 0,
              "opened_with_failure": 0, "blocked_with_failure": 0}
    if not _table("trade_gate_evaluations"):
        return result
    for row in _rows("""SELECT evaluated_at,final_result,detail_json
      FROM trade_gate_evaluations ORDER BY id DESC LIMIT 5000"""):
        try:
            if aware_datetime(row["evaluated_at"]).astimezone(TZ).date() != today:
                continue
            economics = _features(row.get("detail_json")).get("economics")
            if not isinstance(economics, dict) or "passed" not in economics:
                continue
        except (ValueError, TypeError):
            continue
        result["evaluated"] += 1
        if bool(economics["passed"]):
            result["passed"] += 1
        else:
            result["failed"] += 1
            if row.get("final_result") == "OPENED_SIMULATED":
                result["opened_with_failure"] += 1
            elif row.get("final_result") == "BLOCKED":
                result["blocked_with_failure"] += 1
    return result


def _economic_shadow_panel():
    metrics = _economic_shadow_metrics()
    cards = "".join((
        _card("Evaluaciones económicas", metrics["evaluated"], "Señales BUY evaluadas hoy", "gray"),
        _card("Aprueban", metrics["passed"], "Superan el umbral matemático vigente", "green"),
        _card("Fallan", metrics["failed"], "No superan el umbral económico obligatorio", "red" if PAPER_ECONOMIC_GATE_MODE == "BINDING" else "yellow"),
        _card("Abren pese al fallo", metrics["opened_with_failure"], "Debe permanecer en cero con BINDING", "red" if metrics["opened_with_failure"] else "green"),
    ))
    return (f"<div class='paper-card'><h2>Economía matemática {_e(PAPER_ECONOMIC_GATE_MODE)}</h2>"
            "<div class='paper-warning'><b>PORTÓN ECONÓMICO OBLIGATORIO.</b> "
            "Una señal que no cubre comisión, derechos, spread, deslizamiento y reward/risk neto no puede abrir. "
            "Las operaciones siguen siendo 100% simuladas.</div>"
            f"<div class='paper-grid'>{cards}</div></div>")


def scalping_page():
    worker=(_rows("SELECT * FROM intraday_scalping_worker_state WHERE id=1") or [{}])[0] if _table("intraday_scalping_worker_state") else {}
    state=worker.get("state","NOT_STARTED")
    try:
        age=(datetime.now(TZ)-aware_datetime(worker["heartbeat_at"]).astimezone(TZ)).total_seconds()
        if not 0 <= age <= 240:
            state="STALE"
    except (KeyError,ValueError,TypeError):
        state="UNKNOWN" if worker else "NOT_STARTED"
    point_count=(_rows("SELECT COUNT(*) n FROM ppi_intraday_points") or [{"n":0}])[0]["n"] if _table("ppi_intraday_points") else 0
    confirmed=(_rows("SELECT COUNT(*) n FROM ppi_intraday_contract_state WHERE state='CONFIRMED_INTERVAL_VOLUME'") or [{"n":0}])[0]["n"] if _table("ppi_intraday_contract_state") else 0
    contract_rows=_rows("""SELECT symbol,asset_class,market,currency,settlement,state,observations,
      stable_overlap,changed_closed_points,new_points,last_source_at,checked_at,detail
      FROM ppi_intraday_contract_state ORDER BY checked_at DESC LIMIT 50""") if _table("ppi_intraday_contract_state") else []
    decisions=_rows("""SELECT * FROM scalping_candidates ORDER BY evaluated_at DESC,id DESC LIMIT 100""") if _table("scalping_candidates") else []
    scalp_positions=_rows("""SELECT * FROM paper_positions
      WHERE features_json LIKE '%\"execution_style\": \"SCALPING_PAPER\"%'
      ORDER BY opened_at DESC LIMIT 100""") if _table("paper_positions") else []
    buy_candidates=sum(row.get("action")=="BUY_CANDIDATE" for row in decisions)
    contract_table="".join(
      f"<tr><td><b>{_e(r['symbol'])}</b> · {_e(r['asset_class'])}</td><td>{_e(r['market'])} / {_e(r['currency'])} / {_e(r['settlement'])}</td>"
      f"<td>{_status(r['state'])}</td><td>{_e(r['observations'])}</td><td>{_e(r['stable_overlap'])}</td>"
      f"<td>{_e(r['changed_closed_points'])}</td><td>{_local_time(r['last_source_at'])}</td></tr>" for r in contract_rows
    ) or "<tr><td colspan='7'>Esperando la primera rueda con el colector HF3.</td></tr>"
    candidate_table="".join(
      f"<tr><td>{_local_time(r['evaluated_at'])}</td><td><b>{_e(r['symbol'])}</b> · {_e(r['asset_class'])}</td>"
      f"<td>{_e(r['currency'])}</td><td>{_status(r['action'])}</td><td>{_e(r['score'])}</td>"
      f"<td>{_e(r['points'])}</td><td>{_e(r['reason'])}</td></tr>" for r in decisions
    ) or "<tr><td colspan='7'>Todavía no hay evaluaciones intradiarias.</td></tr>"
    cards="".join((
      _card("Scanner",state,f"Modo {PAPER_SCALPING_MODE}; pulso {_local_time(worker.get('heartbeat_at'))}","green" if state=="RUNNING" else "yellow"),
      _card("Puntos intradiarios",point_count,"PPI date/price/volume; inserción idempotente","green" if point_count else "gray"),
      _card("Contratos de volumen",confirmed,"Confirmados automáticamente durante rueda","green" if confirmed else "yellow"),
      _card("Candidatos recientes",buy_candidates,"No equivalen a fills; economía BINDING","green" if buy_candidates else "gray"),
      _card("Fills scalping PAPER",len(scalp_positions),"Separados del scanner y siempre simulados","green" if scalp_positions else "gray"),
      _card("Órdenes reales",worker.get("real_orders_sent",0),"Debe ser siempre cero","green" if worker.get("real_orders_sent",0)==0 else "red"),
    ))
    scalp_rows="".join(f"<tr><td>{_local_time(p['opened_at'])}</td><td>{_e(p['symbol'])}</td><td>{_e(p['currency'])}</td><td>{_status(p['status'])}</td><td>{_e(p['quantity'])}</td><td>{_money(p.get('net_pnl'))}</td><td>{_e(p.get('close_reason'))}</td></tr>" for p in scalp_positions) or "<tr><td colspan='7'>Todavía no hubo fills scalping PAPER.</td></tr>"
    execution_notice=("<div class='paper-notice'><b>ACTIVE_PAPER:</b> los candidatos validados pueden abrir sólo posiciones simuladas. Riesgo 0,10%, máximo una posición scalping y permanencia máxima 30 minutos. PPI Orders permanece bloqueado.</div>"
                      if PAPER_SCALPING_MODE=="ACTIVE_PAPER" else
                      "<div class='paper-warning'><b>ACTIVE_OBSERVE:</b> el scanner observa; no genera fills.</div>")
    body=(f"<h1>Scalping intradiario</h1><div class='paper-grid'>{cards}</div>{execution_notice}"
      f"<div class='paper-notice'>{_e(worker.get('detail','Esperando estado del proceso.'))}</div>"
      "<div class='paper-card'><h2>Contrato intradiario por identidad</h2><table class='paper-table'>"
      "<tr><th>Instrumento</th><th>Identidad</th><th>Estado</th><th>Observaciones</th><th>Solapamiento estable</th>"
      f"<th>Minutos modificados</th><th>Último minuto</th></tr>{contract_table}</table></div>"
      "<div class='paper-card'><h2>Decisiones del scanner</h2><table class='paper-table'>"
      "<tr><th>Hora</th><th>Instrumento</th><th>Moneda</th><th>Acción</th><th>Score</th><th>Puntos</th>"
      f"<th>Motivo</th></tr>{candidate_table}</table></div>"
      "<div class='paper-card'><h2>Operaciones scalping PAPER</h2><table class='paper-table'>"
      f"<tr><th>Apertura</th><th>Instrumento</th><th>Moneda</th><th>Estado</th><th>Cantidad</th><th>PnL</th><th>Cierre</th></tr>{scalp_rows}</table></div>")
    return _document("Scalping",body,refresh=30)


def _is_today(value):
    try:
        return aware_datetime(value).astimezone(TZ).date() == datetime.now(TZ).date()
    except (ValueError, TypeError):
        return False


def _position_mark(position):
    rows = _rows("""SELECT observed_at,book_at,bid,ask,bid_size,ask_size,last
      FROM market_snapshots WHERE symbol=? AND asset_class=? AND settlement=?
        AND currency=? AND market=? ORDER BY id DESC LIMIT 1""",
      tuple(position.get(key) for key in
            ("symbol", "asset_class", "settlement", "currency", "market")))
    return rows[0] if rows else {}


def _position_display_pnl(position, mark):
    """PnL neto cerrado o valuación PAPER conservadora al bid para una compra."""
    if position.get("status") == "CLOSED":
        return _num(position.get("net_pnl")), "realizado neto"
    try:
        quantity = Decimal(str(position["quantity"]))
        entry = Decimal(str(position["entry_price"]))
        bid = Decimal(str(mark["bid"]))
        entry_cost = Decimal(str(position.get("entry_cost") or "0"))
        # La estimación de salida conserva la tasa efectiva de la entrada.
        entry_notional = entry * quantity
        exit_cost = (entry_cost * (bid * quantity) / entry_notional
                     if entry_notional > 0 else Decimal(0))
        value = (bid - entry) * quantity - entry_cost - exit_cost
        return float(value), "no realizado al bid, costos estimados"
    except (KeyError, InvalidOperation, TypeError, ValueError, ZeroDivisionError):
        return 0.0, "sin marca ejecutable"


def _stop_forensic(position):
    if position.get('close_reason')!='STOP_PAPER' or not _table('paper_fills'):
        return ""
    fills=_rows("""SELECT filled_at,price,slippage,costs FROM paper_fills
      WHERE paper_id=? AND side='SELL_SIMULATED' ORDER BY id""",(position.get('paper_id'),))
    if not fills:
        return "<div class='paper-warning'>Stop disparado sin fill de venta conciliado.</div>"
    rows=''.join(f"<tr><td>{_local_time(r['filled_at'])}</td><td>{_e(position.get('stop_price'))}</td><td>{_e(r['price'])}</td><td>{_e(r['slippage'])}</td><td>{_e(r['costs'])}</td></tr>" for r in fills)
    return ("<div class='paper-card'><h3>Forense del stop</h3><p>El nivel de stop dispara la intención; el precio de fill se modela con bid, profundidad y deslizamiento. No es una orden stop garantizada.</p>"
            "<table class='paper-table'><tr><th>Fill</th><th>Stop</th><th>Precio ejecutado</th><th>Slippage</th><th>Costos</th></tr>"+rows+"</table></div>")


def _rejection_funnel():
    if not _table("paper_events"):
        return "<div class='paper-card'>Sin eventos persistidos.</div>"
    since = (datetime.now(TZ) - timedelta(hours=1)).isoformat()
    events = _rows("""SELECT event_type,detail FROM paper_events
      WHERE julianday(event_at)>=julianday(?) AND event_type IN ('DATA_ERROR','DATA_REJECTED')""", (since,))
    counts = {}
    for row in events:
        detail = str(row.get("detail") or "SIN_DETALLE")
        symbol, _, cause = detail.partition(": ")
        cause = cause or detail
        key = (row["event_type"], cause, symbol)
        counts[key] = counts.get(key, 0) + 1
    rows = "".join(
        f"<tr><td>{_e(kind)}</td><td>{_e(cause)}</td><td>{_e(symbol)}</td><td>{count}</td></tr>"
        for (kind, cause, symbol), count in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:25]
    ) or "<tr><td colspan='4'>Sin rechazos ni errores en la última hora.</td></tr>"
    return ("<div class='paper-card'><h2>Embudo de rechazos — última hora</h2>"
            "<p>Un rechazo de mercado o contrato no es una operación perdida; un DATA_ERROR sí requiere seguimiento.</p>"
            "<table class='paper-table'><tr><th>Clase</th><th>Causa</th><th>Instrumento</th><th>Cantidad</th></tr>"
            + rows + "</table></div>")


def _universe_execution_panel():
    if not _table("financial_instrument_catalog"):
        return "<div class='paper-card'>Catálogo financiero ampliado todavía no disponible.</div>"
    rows=_rows("""SELECT instrument_type,currency,capability,COUNT(*) total
      FROM financial_instrument_catalog WHERE status='AVAILABLE'
      GROUP BY instrument_type,currency,capability ORDER BY instrument_type,currency,capability""")
    items=''.join(f"<tr><td>{_e(r['instrument_type'])}</td><td>{_e(r['currency'])}</td><td>{_e(r['capability'])}</td><td>{r['total']}</td></tr>" for r in rows)
    usd_ready=sum(int(r['total']) for r in rows if r['currency'] in {'USD','USD_MEP','USD_CCL'} and r['capability']=='READY_PAPER_SPOT')
    usd_positions=(_rows("""SELECT COUNT(*) n FROM paper_positions
      WHERE date(opened_at,'-3 hours')=date('now','-3 hours') AND currency IN ('USD','USD_MEP','USD_CCL')""") or [{'n':0}])[0]['n']
    return ("<div class='paper-card'><h2>Universo por familia y moneda</h2>"
            f"<div class='paper-notice'>Contratos spot USD listos: {usd_ready}; operaciones USD hoy: {usd_positions}. "
            "Saldo intacto significa que ninguna señal USD atravesó todos los portones; nunca se fabrican transacciones para mover caja.</div>"
            "<table class='paper-table'><tr><th>Familia</th><th>Moneda/plaza</th><th>Capacidad contractual</th><th>Instrumentos</th></tr>"+items+"</table></div>")


def motor_page():
    spot=_spot_snapshot()
    all_positions=spot["open"]+spot["closed"]
    positions=[p for p in all_positions if p.get("status")=="OPEN" or _is_today(p.get("opened_at")) or _is_today(p.get("closed_at"))]
    positions=sorted(positions,key=lambda p:aware_datetime(p["opened_at"]),reverse=True)[:100]
    previous=max(0,len(all_positions)-len(positions))
    gates=_rows("SELECT * FROM trade_gate_evaluations WHERE date(evaluated_at,'-3 hours')=date('now','-3 hours') ORDER BY id DESC LIMIT 300") if _table("trade_gate_evaluations") else []
    gate_by_paper={g["paper_id"]:g for g in gates if g.get("paper_id")}
    gate_by_symbol={g["symbol"]:g for g in reversed(gates)}
    cards=[]
    for p in positions:
        mark=_position_mark(p) if p.get("status")=="OPEN" else {}
        pnl,pnl_kind=_position_display_pnl(p,mark)
        cls="positive" if pnl>0 else "negative" if pnl<0 else "neutral"
        gate=gate_by_paper.get(p.get("paper_id")) or gate_by_symbol.get(p["symbol"],{})
        features=_features(p.get("features_json")); variables="".join(f"<tr><td>{_e(k)}</td><td>{_e(v)}</td></tr>" for k,v in sorted(features.items()))
        economics=features.get("economics") if isinstance(features.get("economics"),dict) else {}
        economic_state=_economics_status(gate.get("detail_json")) if gate else ("APPROVE" if economics.get("passed") else "SIN_REGISTRO")
        contradictory=(gate.get("final_result")=="OPENED_SIMULATED" and
                       (gate.get("technical_gate")!="APPROVE" or gate.get("patrimonial_gate")!="APPROVE" or economic_state!="APPROVE"))
        lesson=("Ganancia: el movimiento favorable superó costos y slippage." if pnl>0 else "Pérdida: revisar momentum, spread, profundidad, duración y contexto antes de ampliar exposición." if pnl<0 else "Resultado aún no cerrado; no cambia umbrales.")
        mark_text=(f"Bid {_e(mark.get('bid'))}; libro {_local_time(mark.get('book_at') or mark.get('observed_at'))}"
                   if mark else "Sin marca viva")
        contradiction_html=("<div class='paper-warning'><b>INVARIANTE INCUMPLIDA:</b> la apertura no puede presentarse como aprobada. Requiere auditoría.</div>"
                            if contradictory else "")
        cards.append(f"""<details class='paper-trade'><summary data-trade-id='{_e(p['paper_id'])}'>{_e(p['symbol'])} · {_e(p['status'])} · {_local_time(p['opened_at'])} · <span class='{cls}'>PnL {_money(pnl)} {_e(p.get('currency','ARS'))}</span></summary><div class='trade-body'>
        <p><b>Valuación:</b> {_e(pnl_kind)}. {_e(mark_text)}</p>{contradiction_html}
        <h3>Secuencia de portones</h3><table class='paper-table'><tr><th>Técnico</th><th>Economía matemática</th><th>Patrimonial / liquidez</th><th>Resultado final</th></tr><tr><td>{_status(gate.get('technical_gate','SIN_REGISTRO'))}</td><td>{_status(economic_state)}</td><td>{_status(gate.get('patrimonial_gate','SIN_REGISTRO'))}</td><td>{_status(gate.get('final_result',p['status']))}</td></tr></table>
        <p><b>Explicación:</b> {_e(gate.get('reason','Operación histórica sin secuencia completa persistida.'))}</p>
        <p class='paper-notice'><b>Decisión reproducible:</b> la IA no participa de la rueda. Señal, economía, capital, exposición y profundidad se resuelven con reglas versionadas de Python.</p>
        <p><b>Cantidad remanente:</b> {_e(p['quantity'] if p['status']=='OPEN' else '0')}. <b>PnL parcial realizado:</b> {_e(p.get('realized_net_pnl','—'))} {_e(p.get('currency','ARS'))}. Una operación abierta todavía no tiene resultado final.</p>
        {_stop_forensic(p)}<h3>Variables utilizadas</h3><table class='paper-table'><tr><th>Variable</th><th>Valor</th></tr>{variables}</table>
        <h3>Lección aprendida</h3><p class='{cls}'>{_e(lesson)}</p></div></details>""")
    gate_rows="".join(f"<tr><td>{_local_time(g['evaluated_at'])}</td><td><b>{_e(g['symbol'])}</b></td><td>{_status(_economics_status(g.get('detail_json')))}</td><td>{_status(g['patrimonial_gate'])}</td><td>{_status(g['final_result'])}</td><td>{_e(g['reason'])}</td></tr>" for g in gates[:50]) or "<tr><td colspan='6'>Aún no hay secuencias nuevas.</td></tr>"
    trade_cards="".join(cards) or '<div class="paper-card">Sin operaciones simuladas del día.</div>'
    history_note=(f"<div class='paper-notice'>{previous} operaciones anteriores no se mezclan con la rueda actual. "
                  "Su Cantidad remanente, realizaciones y resultado permanecen conciliados en Aprendizaje y Reportes.</div>"
                  if previous else "")
    body=f"<h1>Motor de trading — rueda actual</h1><p class='paper-muted'>Trazabilidad técnica → economía matemática → patrimonio/liquidez → resultado.</p><div class='paper-warning'><b>Todas las operaciones de esta página son simuladas.</b> Nunca representan una orden enviada a PPI.</div>{history_note}{trade_cards}<div class='paper-card'><h2>Decisiones bloqueadas o aprobadas de hoy</h2><table class='paper-table'><tr><th>Hora</th><th>Instrumento</th><th>Economía</th><th>Patrimonial</th><th>Final</th><th>Explicación</th></tr>{gate_rows}</table></div>"
    return _document("Motor de trading",_spot_warning(spot["state"])+_daily_risk_panel() + _exit_supervision_panel() + _economic_shadow_panel() + body + _rejection_funnel() + _universe_execution_panel() + _balances_panel() + _caucion_allocations_panel() + _cauciones_panel())


def _next_check(component, checked):
    cadence = {
        "PPI_BACKGROUND_INGEST": int(os.getenv("PPI_BACKGROUND_INGEST_SECONDS", "7200")),
        "PPI_PRODUCTION_HISTORY": int(os.getenv("PPI_BACKGROUND_INGEST_SECONDS", "7200")),
        "PPI_PRODUCTION_CATALOG": int(os.getenv("PUBLIC_SOURCE_CHECK_SECONDS", "21600")),
        "BYMA_OPEN_DATA": int(os.getenv("PUBLIC_SOURCE_CHECK_SECONDS", "21600")),
        "FINANCIAL_REFRESH": 43200,
        "PAPER_FOCUS_COVERAGE": int(os.getenv("PAPER_READINESS_CHECK_SECONDS", "300")),
        "PAPER_SIGNAL_SAMPLING": int(os.getenv("PAPER_READINESS_CHECK_SECONDS", "300")),
        "PAPER_SIGNAL_ROTATION": int(os.getenv("PAPER_READINESS_CHECK_SECONDS", "300")),
        "SRE_SNAPSHOT": 1800,
    }.get(component)
    if cadence is None:
        return "Planificador interno; sin hora contractual"
    try:
        dt=datetime.fromisoformat(str(checked).replace("Z","+00:00"));
        if dt.tzinfo is None: dt=dt.replace(tzinfo=TZ)
        due=dt.astimezone(TZ)+timedelta(seconds=cadence)
        if due <= datetime.now(TZ):
            return f"ATRASADO desde {_local_time(due)}"
        return _local_time(due)
    except Exception: return "Al activarse su planificador"


def health_page():
    components = _health_components()
    rows = "".join(
        f"<tr><td><b>{_e(item['name'])}</b></td><td>{_health_status(item['state'])}</td>"
        f"<td>{_e(item['detail'])}</td><td>{_local_time(item['checked'])}</td>"
        f"<td>{_local_time(item['last_success'])}</td><td>{_e(item['next_check'])}</td>"
        f"<td>{_e(item['mode'])}</td><td>{_e(item['use'])}</td></tr>"
        for item in components)
    applicable = [item for item in components if item["applicable"]]
    red = sum(item["state"] == "ROJO" for item in applicable)
    pending = sum(item["state"] in {"PENDIENTE", "AMARILLO"} for item in applicable)
    summary = ("VERDE: verificación exitosa. AMARILLO/PENDIENTE: degradada o esperando la primera muestra. "
               "ROJO: falla comprobada. NO_APLICA: componente ajeno al modo actual; no es una falla.")
    body = (f"<h1>Salud de APIs y fuentes</h1><div class='paper-notice'><b>Resumen coherente con la portada:</b> "
            f"{red} en rojo y {pending} pendientes/degradadas entre las fuentes aplicables. {_e(summary)}</div>"
            "<p class='paper-muted'>Abrir esta pantalla no consume APIs. Cada fila informa modo, alcance y próximo chequeo.</p>"
            "<div class='paper-card'><table class='paper-table'><tr><th>API / fuente</th><th>Estado</th>"
            "<th>Detalle</th><th>Último reporte / chequeo</th><th>Último éxito</th><th>Próximo chequeo</th>"
            f"<th>Modo</th><th>Uso</th></tr>{rows}</table></div>")
    return _document("Salud de APIs", body, refresh=60)

def history_page():
    catalog=(_rows("SELECT COUNT(*) n FROM instrument_catalog") or [{"n":0}])[0]["n"] if _table("instrument_catalog") else 0
    eligible=(_rows("SELECT COUNT(*) n FROM candidate_universe WHERE can_simulate=1 AND status='AVAILABLE'") or [{"n":0}])[0]["n"] if _table("candidate_universe") else 0
    history=(_rows("SELECT COUNT(*) instruments,SUM(row_count) rows,MAX(downloaded_at) latest FROM production_history") or [{}])[0] if _table("production_history") else {}
    last_market=(_rows("SELECT MAX(date_to) latest FROM production_history") or [{}])[0].get("latest") if _table("production_history") else None
    sync=(_rows("""SELECT source,status,last_attempt_at,last_success_at,items,detail
      FROM source_sync WHERE source LIKE 'PPI_%' ORDER BY last_attempt_at DESC""")
      if _table("source_sync") else [])
    last_attempt=max((str(r.get("last_attempt_at") or "") for r in sync),default="") or None
    last_success=max((str(r.get("last_success_at") or "") for r in sync),default="") or None
    cycles=_rows("SELECT * FROM universe_cycle_metrics ORDER BY id DESC LIMIT 30") if _table("universe_cycle_metrics") else []
    cycle_rows="".join(f"<tr><td>{_local_time(r['started_at'])}</td><td>{r['selected_count']}/{r['eligible_total']}</td><td>{r['successful_count']}</td><td>{r['failed_count']}</td><td>{r['duration_seconds']:.2f}s</td><td>{r['recommended_limit']}</td></tr>" for r in cycles) or "<tr><td colspan='6'>Esperando métricas.</td></tr>"
    history_count=int(history.get('instruments') or 0); history_target=243
    history_pct=min(100.0,history_count/max(1,history_target)*100)
    cards="".join((_card("Catálogo PPI",catalog,"Inventario preapertura y poscierre; no cambia cada minuto","green" if catalog else "gray"),_card("Universo elegible",eligible,"Todos se evalúan por rotación; no todos tienen contrato ejecutable","green" if eligible else "gray"),_card("Cobertura histórica",f"{history_count}/{history_target}",f"{history_pct:.1f}% · {history.get('rows',0) or 0} filas; amarillo hasta completar cobertura","green" if history_count>=history_target else "yellow"),_card("Escaneo por ciclo",PAPER_ACTIVE_SYMBOL_LIMIT,"Ventana rotativa sobre todo el universo","green"),_card("Última fecha de mercado",_e(last_market),"Día bursátil contenido; no es hora de descarga","gray"),_card("Última corrida de ingesta",_local_time(last_attempt),"Cada 2 h fuera de rueda; no compite con current/book","gray"),_card("Última ingesta exitosa",_local_time(last_success),"Puede ser parcial mientras 65/243 no esté completo","green" if last_success else "yellow")))
    sync_rows="".join(f"<tr><td>{_e(r['source'])}</td><td>{_status(r['status'])}</td><td>{_local_time(r['last_attempt_at'])}</td><td>{_local_time(r['last_success_at'])}</td><td>{_e(r['items'])}</td><td>{_e(r['detail'])}</td></tr>" for r in sync) or "<tr><td colspan='6'>Sin corridas registradas.</td></tr>"
    body=f"<h1>Históricos y universo</h1><div class='paper-grid'>{cards}</div><div class='paper-notice'><b>Fecha del dato y fecha de ingesta son conceptos distintos.</b> Con la rueda cerrada es correcto que el último dato bursátil corresponda al cierre anterior; la última corrida indica si el proceso de background continúa activo. PPI históricos no queda limitado al lote visible de un ciclo.</div><div class='paper-card'><h2>Estado de la ingesta</h2><table class='paper-table'><tr><th>Fuente</th><th>Estado</th><th>Último intento</th><th>Último éxito</th><th>Ítems</th><th>Detalle</th></tr>{sync_rows}</table></div><div class='paper-card'><h2>Base objetiva para ampliar el lote por ciclo</h2><table class='paper-table'><tr><th>Ciclo</th><th>Seleccionados/elegibles</th><th>Correctos</th><th>Fallidos</th><th>Duración</th><th>Límite recomendado</th></tr>{cycle_rows}</table></div>"
    return _document("Históricos",body+_family_coverage_panel()+_candle_archive_panel(),refresh=60)


def _family_coverage_panel():
    # Sólo lectura: no importa la fixture del operador ni actualiza el servidor.
    records = _rows('SELECT * FROM catalog_family_coverage ORDER BY instrument_type') if _table('catalog_family_coverage') else []
    labels = {'NOT_ENUMERATED': 'No enumerada en la última configuración',
        'INSTRUMENTS_OBSERVED': 'Instrumentos encontrados',
        'OBSERVED_WITH_ERRORS': 'Instrumentos encontrados; consultas con errores',
        'QUERY_ERROR': 'Error de consulta o metadatos',
        'EMPTY_FILTER_RESULTS': 'Filtros sin coincidencias',
        'DECLARED_NO_QUERY': 'Declarada; sin consulta',
        'CONFIGURATION_UNAVAILABLE': 'Configuración no disponible'}
    items = []
    for r in records:
        declared = {1: 'Sí', 0: 'No enumerada', -1: 'Desconocido'}.get(r['declared'], 'Desconocido')
        items.append(f"<tr><td>{_e(r['instrument_type'])}</td><td>{declared}</td>"
            f"<td>{_e(labels.get(r['discovery_status'], r['discovery_status']))}</td>"
            f"<td>{_e(r['queries'])}</td><td>{_e(r['observed_count'])}</td>"
            f"<td>{_e(r['ready_paper_count'])}</td><td>{_local_time(r['checked_at'])}</td></tr>")
    return ("<div class='paper-card'><h2>Cobertura por familia</h2>"
        "<p>Última ejecución de catálogo, no estado en tiempo real. No acredita permisos ni habilita operaciones. "
        "Una familia declarada no garantiza instrumentos, cotizaciones o contratos ejecutables. "
        "Cero coincidencias no prueba indisponibilidad; sin consulta no significa cero instrumentos existentes. "
        "Compatibles PAPER cuenta sólo contratos de contado reconocidos; faltan los demás portones y no implica ejecución real.</p>"
        "<table class='paper-table'><tr><th>Familia</th><th>Declarada por PPI</th><th>Descubrimiento</th>"
        "<th>Consultas</th><th>Identidades encontradas</th><th>Compatibles PAPER</th><th>Consultado</th></tr>" +
        (''.join(items) or "<tr><td colspan='7'>Sin inventario de familias persistido.</td></tr>") + "</table></div>")


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
      COUNT(*) versions,MIN(v.bar_start) first_bar,MAX(v.bar_end) last_bar,MAX(v.known_at) last_known
      FROM candle_versions v JOIN candle_series s USING(series_id)
      WHERE julianday(v.known_at)<=julianday(?) AND julianday(v.bar_end)<=julianday(?)
      GROUP BY v.series_id ORDER BY s.identity_json LIMIT 100''',
      (datetime.now(TZ).isoformat(),datetime.now(TZ).isoformat())) if _table('candle_versions') else []
    items=[]
    for r in inventory:
        identity=json.loads(r['identity_json'])
        volume_label=("UNIDAD NO CONFIRMADA (raw preservado; no usar para señal)"
                      if identity.get('volume_kind')=='UNKNOWN' else identity.get('volume_kind'))
        items.append(f"<tr><td>{_e(identity['symbol'])} / {_e(identity['asset_class'])}</td>"
            f"<td>{_e(identity['market'])} / {_e(identity['currency'])} / {_e(identity['settlement'])} · factor {_e(identity.get('cash_multiplier','UNKNOWN'))}</td>"
            f"<td>{_e(identity['source'])} / {_e(identity['resolution'])}</td>"
            f"<td>{_e(identity['adjustment'])} / {_e(identity['price_kind'])} / {_e(volume_label)}</td>"
            f"<td>{r['bars']} / {r['versions']}</td><td>{_local_time(r['last_bar'])}</td><td>{_local_time(r['last_known'])}</td></tr>")
    raw=(_rows('SELECT COUNT(*) n FROM historical_raw_archive') or [{'n':0}])[0]['n'] if _table('historical_raw_archive') else 0
    rejected=(_rows('SELECT COUNT(*) n FROM candle_rejections') or [{'n':0}])[0]['n'] if _table('candle_rejections') else 0
    attempts=_rows('SELECT * FROM production_history_attempts ORDER BY attempted_at DESC LIMIT 20') if _table('production_history_attempts') else []
    failed=sum(r['state']!='VALID_PAYLOAD' for r in attempts)
    return ("<div class='paper-card'><h2>Archivo versionado de barras</h2>"
        f"<p>Proceso: <b>{_e(state)}</b> · cursor {_e(worker.get('cursor','—'))} · {_e(worker.get('detail',''))}</p>"
        f"<p>Raw conservados: {raw}. Lecturas excluidas: {rejected}. Descargas no completas entre las últimas {len(attempts)}: {failed}.</p>"
        "<p>TRADE_SAMPLES son muestras del último negocio, no todos los negocios. Su volumen, número de operaciones y VWAP son desconocidos. "
        "No se rellenan huecos y el número de muestras no equivale a series validadas. "
        "Descargar una serie no confirma ajuste, unidad de volumen ni aptitud para operar.</p>"
        "<p>Las revisiones conservan cuándo se conocieron. Este archivo no aprueba rentabilidad ni habilita órdenes. Se muestran hasta 100 series.</p>"
        "<table class='paper-table'><tr><th>Instrumento</th><th>Identidad financiera</th><th>Fuente / período</th>"
        "<th>Ajuste / precio / volumen</th><th>Barras / versiones</th><th>Barra hasta</th><th>Conocida por el sistema</th></tr>"+
        (''.join(items) or "<tr><td colspan='7'>Todavía no hay barras cerradas del archivo nuevo.</td></tr>")+"</table></div>")


def learning_page():
    data=snapshot(); _pnl,wins,wr=_trade_metrics(data["closed"])
    rows="".join(f"<tr class='{'card-green' if _num(p.get('net_pnl'))>0 else 'card-red' if _num(p.get('net_pnl'))<0 else 'card-gray'}'><td>{_local_time(p.get('opened_at'))}</td><td><b>{_e(p['symbol'])}</b></td><td>{_status('WIN' if _num(p.get('net_pnl'))>0 else 'LOSS' if _num(p.get('net_pnl'))<0 else p.get('status'))}</td><td class='{'positive' if _num(p.get('net_pnl'))>0 else 'negative' if _num(p.get('net_pnl'))<0 else 'neutral'}'>{_money(p.get('net_pnl'))} {_e(p.get('currency','ARS'))}</td><td>{_e(p.get('close_reason'))}</td></tr>" for p in data["closed"][:100]) or "<tr><td colspan='5'>Sin muestras cerradas.</td></tr>"
    sample, per_currency = [], {}
    for position in data["closed"]:
        currency = str(position.get("currency") or "ARS").upper()
        if per_currency.get(currency, 0) < 100:
            sample.append(position)
            per_currency[currency] = per_currency.get(currency, 0) + 1
    expectancy = empirical_expectancy(sample, minimum_sample=30)
    expectancy_rows = "".join(
        f"<tr><td>{_e(item['currency'])}</td><td>{item['samples']}</td><td>{_e(item['win_rate_pct'])}%</td>"
        f"<td>{_money(item['average_win'])}</td><td>{_money(item['average_loss'])}</td>"
        f"<td>{_money(item['empirical_expectancy'])}</td><td>{_e(item['profit_factor'] or 's/d')}</td>"
        f"<td>{_status(item['sample_state'])}</td></tr>" for item in expectancy
    ) or "<tr><td colspan='8'>Sin operaciones cerradas para calcular resultados empíricos.</td></tr>"
    cards="".join((
        _card("Muestras cerradas",len(data["closed"]),"Etiquetas para aprendizaje","green" if data["closed"] else "gray"),
        _card("Win rate","s/d" if wr is None else f"{wr:.1f}%",f"{wins}/{len(data['closed'])}","green" if wr is not None and wr>=50 else "red" if wr is not None else "gray"),
        *(_card(f"Resultado {item['currency']}", _money(item["net_total"]),
                "Neto de costos y slippage PAPER en su propia moneda",
                "green" if Decimal(item["net_total"])>0 else "red" if Decimal(item["net_total"])<0 else "gray")
          for item in expectancy),
    ))
    body=(f"<h1>Aprendizaje del sistema</h1><div class='paper-grid'>{cards}</div>"
          "<div class='paper-notice'>Cada compra simulada conserva señal, economía, riesgo, liquidez, resultado y lección. "
          "La expectativa mostrada es descriptiva y neta sobre fills PAPER cerrados; no prueba ventaja futura, no bloquea operaciones y no cambia parámetros automáticamente.</div>"
          "<div class='paper-card'><h2>Expectativa empírica por moneda — últimas 100 cerradas</h2>"
          "<table class='paper-table'><tr><th>Moneda</th><th>Muestras</th><th>Win rate</th><th>Ganancia media</th>"
          "<th>Pérdida media</th><th>Expectativa por operación</th><th>Profit factor</th><th>Estado muestral</th></tr>"
          f"{expectancy_rows}</table><p class='paper-muted'>Menos de 30 muestras se marca como insuficiente; aun con 30 o más permanece observacional hasta validación fuera de muestra.</p></div>"
          f"<div class='paper-card'><table class='paper-table'><tr><th>Apertura</th><th>Instrumento</th><th>Etiqueta</th><th>PnL neto</th><th>Motivo</th></tr>{rows}</table></div>")
    return _document("Aprendizaje",_spot_warning(data["spot_state"])+body)


def _porota_leaders_proxy():
    """Pulso propio sobre líderes observados; no replica ni nombra un índice."""
    focus = ("GGAL", "YPFD", "PAMP", "BMA", "BBAR", "SUPV", "CEPU", "AAPL")
    if not _table("market_snapshots"):
        return {"returns": [], "average": None, "breadth": "s/d", "as_of": None}
    placeholders = ",".join("?" for _ in focus)
    rows = _rows(f"""SELECT symbol,trade_at,last FROM market_snapshots
      WHERE symbol IN ({placeholders}) AND last_kind='TRADE' AND trade_at IS NOT NULL
      ORDER BY symbol,julianday(trade_at) DESC,id DESC""", focus)
    samples = {}
    for row in rows:
        values = samples.setdefault(row["symbol"], [])
        if row["trade_at"] not in {value[0] for value in values} and _num(row["last"]) > 0:
            values.append((row["trade_at"], _num(row["last"])))
    returns = []
    for symbol, values in samples.items():
        if len(values) >= 2 and values[1][1] > 0:
            returns.append({"symbol": symbol, "return": (values[0][1] / values[1][1] - 1) * 100,
                            "as_of": values[0][0]})
    returns.sort(key=lambda value: value["return"], reverse=True)
    if not returns:
        return {"returns": [], "average": None, "breadth": "s/d", "as_of": None}
    rising = sum(value["return"] > 0 for value in returns)
    falling = sum(value["return"] < 0 for value in returns)
    return {"returns": returns,
            "average": statistics.fmean(value["return"] for value in returns),
            "median": statistics.median(value["return"] for value in returns),
            "breadth": f"{rising} suben / {falling} bajan / {len(returns)-rising-falling} sin cambio",
            "as_of": max(value["as_of"] for value in returns)}


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
    proxy = _porota_leaders_proxy()
    proxy_value = "s/d" if proxy["average"] is None else f"{proxy['average']:+.2f}%"
    median_value = "s/d" if proxy["average"] is None else f"{proxy['median']:+.2f}%"
    proxy_asof = f"Última muestra {_local_time(proxy['as_of'])}"
    proxy_rows = "".join(f"<tr><td><b>{_e(r['symbol'])}</b></td><td class='{'positive' if r['return']>0 else 'negative' if r['return']<0 else 'neutral'}'>{r['return']:+.2f}%</td><td>{_local_time(r['as_of'])}</td></tr>" for r in proxy["returns"]) or "<tr><td colspan='3'>Esperando dos muestras de negocio por instrumento.</td></tr>"
    pulse_state=('green' if proxy['average'] is not None and proxy['average']>0 else
                 'red' if proxy['average'] is not None and proxy['average']<0 else
                 'gray')
    median_state=('green' if proxy['average'] is not None and proxy['median']>0 else
                  'red' if proxy['average'] is not None and proxy['median']<0 else
                  'gray')
    body=f"<h1>Información financiera</h1><p class='paper-muted'>Indicadores para preparar la operatoria diaria con datos observados y cálculos propios reproducibles.</p><div class='paper-grid'>{_card('Pulso Porota - líderes',proxy_value,proxy['breadth'],pulse_state,'negative' if pulse_state=='red' else 'positive' if pulse_state=='green' else 'neutral')}{_card('Mediana de líderes',median_value,proxy_asof,median_state,'negative' if median_state=='red' else 'positive' if median_state=='green' else 'neutral')}{_card('Actualización macro','12 horas','Caché local; la página no llama APIs','green')}</div><div class='paper-card'><h2>Componentes del pulso propio</h2><table class='paper-table'><tr><th>Instrumento</th><th>Variación entre muestras</th><th>Último negocio</th></tr>{proxy_rows}</table><p class='paper-muted'>Indicador interno equiponderado; sirve para amplitud y contexto. No representa un índice oficial ni reemplaza precios ejecutables.</p></div><div class='paper-card'><h2>Indicadores BCRA e INDEC</h2><table class='paper-table'><tr><th>Indicador</th><th>Valor</th><th>Unidad</th><th>Fecha</th><th>Fuente</th></tr>{values}</table></div><div class='paper-card'><h2>Inflación vs performance del bot</h2><table class='paper-table'><tr><th>Mes</th><th>Inflación mensual</th><th>PnL paper</th><th>Lectura</th></tr>{compare}</table></div><div class='paper-notice'>La comparación válida requiere rentabilidad porcentual del patrimonio PAPER y del pulso propio sobre períodos idénticos; se habilitará al completar el primer mes.</div>"
    return _document("Información financiera",_spot_warning(spot["state"])+body,refresh=300)


def reports_page():
    reports=_rows("SELECT * FROM report_registry ORDER BY period_key DESC,period_type") if _table("report_registry") else []
    today=datetime.now(TZ).date().isoformat()
    reports=[r for r in reports if r.get('period_type')!='DIARIO' or r.get('period_key')==today]
    rows=[]
    for r in reports:
        pdf=(f"<a class='paper-action' href='/api/reports/{r['id']}/pdf'>Descargar PDF</a>"
             if r.get("pdf_path") else "—")
        ai=(f"<a class='paper-action' href='/api/reports/{r['id']}/ai'>Paquete IA semanal consolidado</a>"
            if r.get("ai_path") and r.get('period_type')=='IA_SEMANAL' else "—")
        rows.append(f"<tr><td>{_e(r['period_type'])}</td><td>{_e(r['period_key'])}</td>"
                    f"<td>{_health_status(r['state'])}</td><td>{_local_time(r['created_at'])}</td>"
                    f"<td>{pdf}</td><td>{ai}</td><td>{_e(r['detail'])}</td></tr>")
    rows="".join(rows) or "<tr><td colspan='7'>El primer informe se genera al cierre.</td></tr>"
    body=f"<h1>Reportes</h1><p class='paper-muted'>PDF ejecutivo del día; paquete IA único con siete días de evidencia. La ingesta de noticias está deshabilitada y no se incluye en nuevas muestras.</p><div class='paper-card'><table class='paper-table'><tr><th>Tipo</th><th>Período</th><th>Estado</th><th>Generado</th><th>PDF</th><th>Paquete IA</th><th>Contenido</th></tr>{rows}</table></div><div class='paper-notice'>Los diarios anteriores se consultan desde Aprendizaje; el semanal consolida la evidencia para IA y el mensual reemplaza semanales ya integrados sin borrar ledger.</div>"
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
        schema=_rows("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        rows="".join(f"<tr><td>{_e(r['name'])}</td><td>{_e(counts.get(r['name'],'no contado en snapshot'))}</td><td><code>{_e(r.get('sql'))}</code></td></tr>" for r in schema)
        wal_mb=_num(snap.get("wal_bytes"))/1048576
        query_ms=_num(snap.get("db_query_ms"))
        integrity=snap.get("db_integrity","sin medición")
        cards="".join((
            _card("SQLite + WAL",f"{db_mb:.2f} MB",f"WAL {wal_mb:.2f} MB","green" if integrity=="ok" else "red"),
            _card("Integridad",integrity,"PRAGMA quick_check","green" if integrity=="ok" else "red"),
            _card("Latencia consulta",f"{query_ms:.2f} ms","Objetivo menor a 250 ms","green" if query_ms<250 else "red"),
        ))
        content=f"<h2>Base de datos</h2><div class='paper-grid'>{cards}</div><div class='paper-card'><p>Se listan todas las tablas. Para evitar bloquear SQLite durante rueda, sólo se muestran conteos ya persistidos por SRE.</p><table class='paper-table'><tr><th>Tabla</th><th>Filas</th><th>Esquema</th></tr>{rows}</table></div>"
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


def live_page():
    data=snapshot(); state=data['state']; now=datetime.now(TZ)
    workers=[]
    for label,table in (("Supervisor de salidas","paper_supervisor_state"),
                        ("Lector de libros de salida","paper_exit_reader_state"),
                        ("Velas","candle_worker_state"),
                        ("Scalping","intraday_scalping_worker_state"),
                        ("Telegram","paper_notification_worker")):
        row=(_rows(f"SELECT * FROM {table} WHERE id=1") or [{}])[0] if _table(table) else {}
        heartbeat=row.get('heartbeat_at'); age=None
        try: age=(now-aware_datetime(heartbeat).astimezone(TZ)).total_seconds()
        except (ValueError,TypeError): pass
        live=age is not None and 0<=age<=240
        workers.append(f"<tr><td>{_e(label)}</td><td>{_status(row.get('state','NOT_STARTED'))}</td><td>{_local_time(heartbeat)}</td><td>{_e('s/d' if age is None else f'{age:.1f} s')}</td><td>{_e(row.get('detail'))}</td></tr>")
    positions=data['open']
    position_rows=[]
    intents={r.get('paper_id'):r for r in data.get('exit_intents',[])}
    for p in positions:
        mark=_position_mark(p); pnl,kind=_position_display_pnl(p,mark); intent=intents.get(p.get('paper_id'),{})
        position_rows.append(f"<tr><td>{_e(p['symbol'])}</td><td>{_e(p.get('currency'))}</td><td>{_money(pnl)}</td><td>{_e(kind)}</td><td>{_status(intent.get('state','SIN_SUPERVISION'))}</td><td>{_local_time(intent.get('supervised_at'))}</td><td>{_local_time(mark.get('book_at') or mark.get('observed_at'))}</td></tr>")
    recent=_rows("""SELECT event_type,COUNT(*) total FROM paper_events
      WHERE julianday(event_at)>=julianday(?) GROUP BY event_type ORDER BY total DESC""",
      ((now-timedelta(hours=1)).isoformat(),)) if _table('paper_events') else []
    events=''.join(f"<tr><td>{_e(r['event_type'])}</td><td>{r['total']}</td></tr>" for r in recent)
    cards=''.join((
      _card('Motor',f"{state.get('process_state','UNKNOWN')} / {state.get('session_state','UNKNOWN')}",f"Pulso {_local_time(state.get('heartbeat_at'))}",'green' if state.get('process_state')=='RUNNING' else 'yellow'),
      _card('PPI autenticación',state.get('ppi_auth','UNKNOWN'),state.get('detail',''),'green' if state.get('ppi_auth')=='OK' else 'yellow'),
      _card('Posiciones abiertas',len(positions),'Cada una debe tener intención y marca fresca','green' if all(intents.get(p.get('paper_id'),{}).get('state') for p in positions) else 'red' if positions else 'gray'),
      _card('Órdenes reales',state.get('real_orders_sent',0),'Invariante permanente: cero','green' if state.get('real_orders_sent',0)==0 else 'red'),
    ))
    body=(f"<h1>Control operativo en vivo</h1><div class='paper-grid'>{cards}</div>"
          "<div class='paper-card'><h2>Motores y último pulso</h2><table class='paper-table'><tr><th>Motor</th><th>Estado</th><th>Pulso</th><th>Antigüedad</th><th>Detalle</th></tr>"+''.join(workers)+"</table></div>"
          "<div class='paper-card'><h2>Posiciones y supervisión</h2><table class='paper-table'><tr><th>Instrumento</th><th>Moneda</th><th>PnL</th><th>Método</th><th>Salida</th><th>Supervisada</th><th>Libro</th></tr>"+(''.join(position_rows) or "<tr><td colspan='7'>Sin posiciones abiertas.</td></tr>")+"</table></div>"
          "<div class='paper-card'><h2>Actividad de la última hora</h2><table class='paper-table'><tr><th>Evento</th><th>Cantidad</th></tr>"+(events or "<tr><td colspan='2'>Sin eventos.</td></tr>")+"</table></div>"+_rejection_funnel())
    return _document("En vivo",body,refresh=30)


SYSTEM_SECTIONS = (
    ("introspeccion", "Introspección"),
    ("salud", "Salud y SRE"),
    ("configuracion", "Configuración"),
    ("telegram", "Telegram"),
    ("logs", "Logs"),
)


def _system_nav(active):
    return ("<nav class='system-nav' aria-label='Secciones técnicas'>" +
            "".join(f"<a class='{'active' if key==active else ''}' "
                    f"href='/sistema?section={key}'>{label}</a>"
                    for key,label in SYSTEM_SECTIONS) + "</nav>")


def _main_fragment(page):
    match = re.search(r"<main[^>]*class=['\"]paper-page['\"][^>]*>(.*?)</main>",
                      page, flags=re.I|re.S)
    return match.group(1) if match else "<div class='paper-warning'>Vista técnica no disponible.</div>"


def _latest_introspection():
    directory = Path(DB_PATH).parents[1] / "introspection"
    files = sorted(directory.glob("porota_introspection_hf*_*.json")) if directory.exists() else []
    try:
        return json.loads(files[-1].read_text(encoding="utf-8")) if files else None
    except (OSError, ValueError, TypeError):
        return None


def _latest_publication_status():
    path = Path(DB_PATH).parents[1] / "introspection_publish/publication_status.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def introspection_content():
    report = _latest_introspection()
    if not report:
        return ("<h1>Introspección funcional</h1><div class='paper-warning'>"
                "Todavía no existe un snapshot de introspección legible. El dashboard no lo interpreta como estado sano.</div>")
    trading = report.get("trading", {})
    observer = report.get("observer", {})
    ingestion = report.get("ingestion", {})
    scalping = report.get("scalping", {})
    cauciones = report.get("caucion_readiness", {})
    regime = report.get("market_regime_observation", {})
    sectors = report.get("sector_concentration", {})
    storage = report.get("storage", {})
    publication = _latest_publication_status() or report.get("github_publication", {})
    warnings = report.get("warnings", [])
    anomalies = report.get("anomalies", [])
    verdict = report.get("verdict", "UNKNOWN")
    cards = "".join((
        _card("Dictamen", verdict, report.get("timestamp", "sin fecha"),
              "red" if verdict=="CRITICAL" else "yellow" if verdict=="WARN" else "green" if verdict=="OK" else "gray"),
        _card("Motor", f"{observer.get('process_state','UNKNOWN')} / {observer.get('session_state','UNKNOWN')}",
              f"Pulso {_local_time(observer.get('heartbeat_at'))}", "green" if observer.get('process_state')=='RUNNING' else "yellow"),
        _card("Posiciones", f"{trading.get('open',0)} abiertas / {trading.get('closed_today',0)} cerradas hoy",
              f"PnL neto hoy {_amount(trading.get('net_pnl_today',0),'ARS')}", "red" if _num(trading.get('net_pnl_today'))<0 else "green"),
        _card("Órdenes reales", observer.get("real_orders_sent",0), "Invariante permanente: cero",
              "green" if int(observer.get("real_orders_sent") or 0)==0 else "red"),
        _card("Históricos", f"{ingestion.get('instruments',0)} instrumentos / {ingestion.get('rows',0)} filas",
              f"Última descarga {_local_time(ingestion.get('last_download'))}", "yellow" if int(ingestion.get('instruments') or 0)<243 else "green"),
        _card("Disco", f"{storage.get('filesystem_used_pct','—')}% usado",
              f"DB {storage.get('database_bytes','—')} bytes · WAL {storage.get('wal_bytes',0)} bytes",
              "red" if _num(storage.get('filesystem_used_pct'))>=85 else "yellow" if _num(storage.get('filesystem_used_pct'))>=70 else "green"),
        _card("GitHub observabilidad", publication.get("status", "SIN_REGISTRO"),
              f"Última confirmación {_local_time(publication.get('recorded_at'))} · retención {publication.get('retention_days',90)} días",
              "green" if publication.get("status") in {"PUBLISHED", "UNCHANGED"} else "red"),
        _card("Cauciones", cauciones.get("state", "SIN_DATOS"),
              f"Contratos observados {cauciones.get('observed_contracts',0)} · ofertas completas {cauciones.get('complete_offers',0)} · colocadas hoy {cauciones.get('placed_today',0)}",
              "green" if cauciones.get("state")=="READY" else "yellow"),
        _card("Régimen observado", regime.get("state", "SIN_DATOS"),
              f"Suben {regime.get('rising',0)} · bajan {regime.get('falling',0)} · política ALERT_ONLY",
              "yellow" if regime.get("state")=="BEARISH_BREADTH" else "gray"),
        _card("Concentración sectorial", sectors.get("state", "SIN_DATOS"),
              f"Mapeadas {sectors.get('mapped_positions',0)} · sin mapa {sectors.get('unmapped_positions',0)} · sin límite vinculante",
              "gray"),
    ))
    warning_rows = "".join(f"<li>{_e(item)}</li>" for item in warnings) or "<li>Sin advertencias.</li>"
    anomaly_rows = "".join(f"<li>{_e(item)}</li>" for item in anomalies) or "<li>Sin anomalías de coherencia.</li>"
    worker_rows = "".join(
        f"<tr><td>{_e(name)}</td><td>{_status(item.get('state'))}</td>"
        f"<td>{_local_time(item.get('heartbeat_at'))}</td>"
        f"<td>{_e(item.get('heartbeat_age_seconds'))}</td><td>{_e(item.get('detail'))}</td></tr>"
        for name,item in report.get("workers",{}).items()) or "<tr><td colspan='5'>Sin workers informados.</td></tr>"
    ppi_rows = "".join(
        f"<tr><td>{_e(row.get('event_type'))}</td><td>{_e(row.get('detail'))}</td><td>{_e(row.get('count'))}</td></tr>"
        for row in report.get("ppi_errors_1h",[])) or "<tr><td colspan='3'>Sin errores PPI en la ventana.</td></tr>"
    currency_rows = "".join(
        f"<tr><td>{_e(row.get('currency'))}</td><td>{_e(row.get('observed_symbols'))}</td>"
        f"<td>{_local_time(row.get('last_observed_at'))}</td></tr>"
        for row in report.get("currency_funnel",[])) or "<tr><td colspan='3'>Sin observaciones monetarias.</td></tr>"
    expectancy_rows = "".join(
        f"<tr><td>{_e(row.get('currency'))}</td><td>{_e(row.get('samples'))}</td>"
        f"<td>{_e(row.get('win_rate_pct'))}%</td><td>{_e(row.get('empirical_expectancy'))}</td>"
        f"<td>{_e(row.get('profit_factor') or 's/d')}</td><td>{_status(row.get('sample_state'))}</td></tr>"
        for row in report.get("learning_expectancy",[])) or "<tr><td colspan='6'>Sin muestra cerrada.</td></tr>"
    caucion_missing_offer = ", ".join(cauciones.get("missing_offer_requirements") or []) or "ninguno"
    caucion_missing_policy = ", ".join(cauciones.get("missing_policy_requirements") or []) or "ninguno"
    family_rows = "".join(
        f"<tr><td>{_e(row.get('instrument_type'))}</td><td>{_e(row.get('observed_count'))}</td>"
        f"<td>{_e(row.get('ready_paper_count'))}</td><td>{_status(row.get('discovery_status'))}</td>"
        f"<td>{_e(', '.join(str(item.get('capability'))+'='+str(item.get('count')) for item in row.get('capabilities',[])) or 'sin capacidad observada')}</td>"
        f"<td>{_e('NO' if row.get('excluded_by_default') is False else 'DESCONOCIDO')}</td></tr>"
        for row in report.get("family_readiness",[])) or "<tr><td colspan='6'>Sin inventario contractual.</td></tr>"
    return (f"<h1>Introspección funcional</h1><p class='paper-muted'>Snapshot local generado por el control horario; "
            "GitHub recibe únicamente una copia sanitizada y nunca es dependencia del runtime.</p>"
            f"<div class='paper-grid'>{cards}</div>"
            f"<div class='paper-card'><h2>Advertencias</h2><ul>{warning_rows}</ul><h2>Anomalías</h2><ul>{anomaly_rows}</ul></div>"
            "<div class='paper-card'><h2>Workers</h2><table class='paper-table'><tr><th>Worker</th><th>Estado</th><th>Pulso</th><th>Edad (s)</th><th>Detalle</th></tr>"
            f"{worker_rows}</table></div>"
            "<div class='paper-card'><h2>Scalping</h2><p>"
            f"Evaluados: {_e(scalping.get('evaluated_1h',0))} · Rechazados: {_e(scalping.get('rejected_1h',0))} · "
            f"Aprobados: {_e(scalping.get('approved_1h',0))} · Fills PAPER hoy: {_e(scalping.get('paper_positions_today',0))}</p></div>"
            "<div class='paper-card'><h2>Errores PPI de la última hora</h2><table class='paper-table'><tr><th>Tipo</th><th>Detalle sanitizado</th><th>Cantidad</th></tr>"
            f"{ppi_rows}</table></div>"
            "<div class='paper-card'><h2>Embudo por moneda</h2><table class='paper-table'><tr><th>Moneda</th><th>Símbolos observados</th><th>Última observación</th></tr>"
            f"{currency_rows}</table></div>"
            "<div class='paper-card'><h2>Aprendizaje matemático</h2><p class='paper-muted'>Descriptivo, neto y no vinculante; no se interpreta como probabilidad futura.</p>"
            "<table class='paper-table'><tr><th>Moneda</th><th>Muestras</th><th>Win rate</th><th>Expectativa/operación</th><th>Profit factor</th><th>Estado</th></tr>"
            f"{expectancy_rows}</table></div>"
            "<div class='paper-card'><h2>Políticas de contexto autorizadas</h2>"
            "<p><b>Economía matemática:</b> BINDING. <b>Expectativa insuficiente:</b> OBSERVATION_ONLY. "
            "<b>Régimen:</b> ALERT_ONLY. <b>Sector:</b> OBSERVATION_ONLY, sin límite vinculante.</p>"
            "<p class='paper-muted'>La amplitud usa primera y última muestra de negocio observada; no se presenta como índice ni como OHLC completo.</p></div>"
            "<div class='paper-card'><h2>Cauciones — preparación contractual</h2>"
            f"<p>Estado: {_status(cauciones.get('state','SIN_DATOS'))}.</p>"
            f"<p><b>Oferta actual faltante:</b> {_e(caucion_missing_offer)}.</p>"
            f"<p><b>Política diaria faltante:</b> {_e(caucion_missing_policy)}.</p>"
            "<p class='paper-muted'>La incertidumbre se observa y conserva; sólo una oferta con todos los términos validados puede llegar al asignador PAPER.</p></div>"
            "<div class='paper-card'><h2>Familias financieras — descubrimiento y capacidad</h2>"
            "<table class='paper-table'><tr><th>Familia</th><th>Observadas</th><th>Contado PAPER listo</th><th>Descubrimiento</th><th>Capacidades/pendientes</th><th>Excluida por defecto</th></tr>"
            f"{family_rows}</table></div>")


def system_page(section="introspeccion"):
    if section not in {key for key,_ in SYSTEM_SECTIONS}:
        section = "introspeccion"
    if section == "introspeccion":
        content = introspection_content()
    elif section == "salud":
        content = _main_fragment(health_page()) + _main_fragment(sre_page())
    elif section == "configuracion":
        content = _main_fragment(config_page())
    elif section == "telegram":
        content = _main_fragment(telegram_page())
    else:
        content = _main_fragment(logs_page())
    body = ("<h1>Sistema</h1><p class='paper-muted'>Operación técnica consolidada para reducir navegación y desplazamiento.</p>"
            f"<div class='system-layout'>{_system_nav(section)}<section class='system-content'>{content}</section></div>")
    return _document("Sistema", body, refresh=30 if section=="introspeccion" else 60)


def config_page():
    keys=("PAPER_RISK_PER_TRADE","PAPER_MAX_OPEN_POSITIONS","PAPER_MAX_HOLD_MINUTES",
          "PAPER_MAX_POSITION_PCT","PAPER_MAX_TOTAL_EXPOSURE_PCT","PAPER_DAILY_SOFT_STOP_PCT","MAX_DAILY_LOSS_PCT",
          "PAPER_STOP_LOSS_PCT","PAPER_TARGET_GAIN_PCT","PAPER_ECONOMIC_GATE_MODE",
          "PAPER_EXPECTANCY_POLICY","PAPER_MARKET_REGIME_POLICY","PAPER_SECTOR_CONCENTRATION_POLICY",
          "PAPER_AI_GATE_MODE","PAPER_SCALPING_MODE","PAPER_SCALPING_RISK_PER_TRADE",
          "PAPER_SCALPING_MAX_OPEN_POSITIONS","PAPER_SCALPING_MAX_HOLD_MINUTES",
          "PPI_BACKGROUND_INGEST_SECONDS","PAPER_NEWS_INGEST_ENABLED","PAPER_FOCUS_SYMBOLS",
          "PAPER_INITIAL_CAPITAL_ARS","PAPER_INITIAL_CAPITAL_USD",
          "PAPER_INITIAL_CAPITAL_USD_MEP","PAPER_INITIAL_CAPITAL_USD_CCL")
    rows=''.join(f"<tr><td><code>{_e(key)}</code></td><td>{_e(os.getenv(key,'NO_DEFINIDO'))}</td><td>runtime env generado por gestor de modo {_e(VERSION)}</td></tr>" for key in keys)
    mismatch,effective=_mode_mismatch()
    body=(f"<h1>Configuración efectiva</h1><div class='paper-grid'>"
          f"{_card('Modo autoritativo',effective,'data/operation_mode.json','red' if mismatch else 'green')}"
          f"{_card('Env del dashboard',CONFIGURED_MODE,'Debe coincidir con el manifiesto','red' if mismatch else 'green')}"
          f"{_card('Órdenes PPI','BLOQUEADAS','No existe modo real habilitable','green')}</div>"
          "<div class='paper-card'><p>Esta tabla muestra valores efectivos no secretos. La procedencia se centraliza en el gestor de modo; las constantes financieras versionadas permanecen en código y se auditan por separado.</p>"
          f"<table class='paper-table'><tr><th>Variable</th><th>Valor efectivo</th><th>Procedencia</th></tr>{rows}</table></div>")
    return _document("Configuración",body,refresh=60)


def logs_page():
    log=Path(os.getenv("LOG_DIR","data/logs"))/"trading_bot.log"; size=f"{log.stat().st_size/1024:.1f} KB" if log.exists() else "sin archivo"
    tail=[]
    if log.exists():
        try:
            tail=log.read_text(encoding="utf-8",errors="replace").splitlines()[-50:]
        except OSError:
            tail=[]
    live="\n".join(tail) or "Sin líneas disponibles."
    download=("<a class='paper-action' href='/api/logs/current'>Descargar log operativo</a>" if log.exists() else "")
    body=f"<h1>Gestión de logs</h1><div class='paper-grid'>{_card('Log operativo',size,str(log),'green' if log.exists() else 'gray')}{_card('Métricas de universo','PERSISTIDAS','SRE → Performance','green')}</div>{download}<div class='paper-card'><h2>Últimas 50 líneas</h2><pre style='white-space:pre-wrap;overflow-wrap:anywhere'>{_e(live)}</pre></div><div class='paper-notice'>La vista se actualiza sin exponer secretos; la descarga exige la misma autenticación del dashboard.</div>"
    return _document("Logs",body,refresh=30)


def _authorize(check_auth,request,token,authorization):
    try: check_auth(token,authorization,request.cookies.get("porota_dashboard_session"))
    except TypeError: check_auth(token,authorization)


def install(app,check_auth):
    global _installed
    if _installed or _effective_mode() not in MODE_INFO: return
    _installed=True
    def auth(request,token,authorization): _authorize(check_auth,request,token,authorization)
    @app.get("/observacion",response_class=HTMLResponse)
    def observacion(request:Request,token:str=Query(default=""),authorization:str|None=Header(default=None)): auth(request,token,authorization); return HTMLResponse(paper_page(True))
    @app.get("/motor-trading",response_class=HTMLResponse)
    def motor(request:Request,token:str=Query(default=""),authorization:str|None=Header(default=None)): auth(request,token,authorization); return HTMLResponse(motor_page())
    @app.get("/en-vivo",response_class=HTMLResponse)
    def en_vivo(request:Request,token:str=Query(default=""),authorization:str|None=Header(default=None)): auth(request,token,authorization); return HTMLResponse(live_page())
    @app.get("/sistema",response_class=HTMLResponse)
    def sistema(request:Request,section:str=Query(default="introspeccion"),token:str=Query(default=""),authorization:str|None=Header(default=None)): auth(request,token,authorization); return HTMLResponse(system_page(section))
    @app.get("/scalping",response_class=HTMLResponse)
    def scalping(request:Request,token:str=Query(default=""),authorization:str|None=Header(default=None)): auth(request,token,authorization); return HTMLResponse(scalping_page())
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
        data = allocation_history(DB_PATH,limit=limit,offset=offset,require_workspace=True)
        return JSONResponse(data,status_code=503 if data['state']=='READ_ERROR' else 200)
    @app.get("/api/reports/{report_id}/{kind}")
    def report_download(report_id:int,kind:str,request:Request,token:str=Query(default=""),authorization:str|None=Header(default=None)):
        auth(request,token,authorization)
        if kind not in {"pdf","ai"}: raise HTTPException(404,"Tipo no disponible")
        rows=_rows("SELECT pdf_path,ai_path FROM report_registry WHERE id=?",(report_id,)); path=Path(rows[0]["pdf_path" if kind=="pdf" else "ai_path"]) if rows and rows[0].get("pdf_path" if kind=="pdf" else "ai_path") else None
        root=(artifact_root(DB_PATH) / 'reports').resolve()
        if not path or not path.resolve().is_relative_to(root) or not path.exists(): raise HTTPException(404,"Informe no disponible")
        return FileResponse(path,media_type="application/pdf" if kind=="pdf" else "application/json",filename=path.name)
    @app.get("/api/logs/current")
    def log_download(request:Request,token:str=Query(default=""),authorization:str|None=Header(default=None)):
        auth(request,token,authorization)
        path=(Path(os.getenv("LOG_DIR","data/logs"))/"trading_bot.log").resolve()
        allowed=(Path(os.getenv("LOG_DIR","data/logs"))).resolve()
        if not path.is_relative_to(allowed) or not path.exists():
            raise HTTPException(404,"Log no disponible")
        return FileResponse(path,media_type="text/plain",filename="porota_trading_actual.log")
    @app.middleware("http")
    async def paper_truth(request,call_next):
        response=await call_next(request); ctype=response.headers.get("content-type","")
        legacy_json={"/api/dashboard","/api/v16/estado","/api/v15/estado",
                     "/api/observation-instruments","/api/learning-logs",
                     "/api/failed-notifications","/reports/monthly"}
        if request.url.path in legacy_json and response.status_code < 400:
            return JSONResponse({
                "error":"LEGACY_DATASET_NOT_AVAILABLE_IN_PRODUCTION_PAPER",
                "detail":"Este endpoint pertenece al motor legacy y no describe HF4. Usar /api/observer/state.",
                "mode":_effective_mode(),
            },status_code=409)
        if "text/html" not in ctype or response.status_code>=400: return response
        body=b"".join([chunk async for chunk in response.body_iterator]); content=body.decode("utf-8","replace")
        # /vivo es el nombre histórico de la actividad en tiempo real. En
        # simulación productiva debe ser un alias real del panel consolidado,
        # no una vista heredada meramente retocada por _canonicalize().
        replacements={"/":home_page,"/vivo":lambda:paper_page(True),"/en-vivo":live_page,"/testing":lambda:paper_page(True),"/salud":health_page,"/scalping":scalping_page,"/historicos":history_page,"/aprendizaje":learning_page,"/telegram":telegram_page,"/dashboard/logs":logs_page,"/config":config_page}
        if request.url.path=="/sre": content=sre_page(request.query_params.get("section","overview"))
        elif request.url.path in replacements: content=replacements[request.url.path]()
        else: content=_canonicalize(content,request.url.path)
        headers=dict(response.headers); headers.pop("content-length",None)
        return HTMLResponse(_dedupe_refresh(content),status_code=response.status_code,headers=headers)
