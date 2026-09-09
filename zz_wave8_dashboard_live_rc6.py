"""RC6 dashboard live-wiring and operator accessibility closure.

Dashboard-only, read-only wiring. Keeps tabular data as real rows/columns,
restores visible column headers on tablet, avoids duplicate navigation/titles,
adds live family activity and expands the Risk destination. No broker/order
capability is introduced here.
"""
from __future__ import annotations
import html
import re
import unicodedata
from fastapi import Header, Query, Request
from fastapi.responses import HTMLResponse
import bg_paper_dashboard as bg
import et_shadow_learning_rc6 as shadow_learning
import fi_event_risk_shadow_rc6 as event_contract

_installed=False
CLASSIC_CSS="""
<style id='porota-rc6-classic-responsive'>
/* Canonical tablet rule: never crush table columns. Horizontal scrolling is
   preferable to unreadable character-by-character wrapping. */
.paper-card,.tarjeta{max-width:100%;overflow-x:auto!important;overflow-y:visible!important;-webkit-overflow-scrolling:touch}
.paper-table,.classic-responsive-table{display:table!important;width:max-content!important;min-width:100%!important;max-width:none!important;table-layout:auto!important;border-collapse:collapse!important}
.paper-table thead,.classic-responsive-table thead{display:table-header-group!important}
.paper-table tbody,.classic-responsive-table tbody{display:table-row-group!important}
.paper-table tr,.classic-responsive-table tr{display:table-row!important}
.paper-table th,.paper-table td,.classic-responsive-table th,.classic-responsive-table td{display:table-cell!important;white-space:nowrap!important;overflow-wrap:normal!important;word-break:keep-all!important;min-width:max-content!important;max-width:none!important;vertical-align:middle!important}
.paper-table td[data-wrap='true'],.classic-responsive-table td[data-wrap='true']{white-space:normal!important;min-width:16rem!important;max-width:32rem!important;overflow-wrap:break-word!important;word-break:normal!important}
.porota-cell-label{display:none!important}

/* RC5 compact-table CSS hid the header row on tablet. RC6 intentionally keeps
   real column headers visible and uses horizontal scrolling instead. */
.paper-table tr.porota-table-header,.classic-responsive-table tr.porota-table-header{position:static!important;width:auto!important;height:auto!important;padding:0!important;margin:0!important;overflow:visible!important;clip:auto!important;clip-path:none!important;white-space:normal!important;border:0!important;display:table-row!important}
.paper-table tr.porota-table-header th,.classic-responsive-table tr.porota-table-header th{display:table-cell!important;visibility:visible!important;white-space:nowrap!important}

/* RC6 accessibility/navigation closure: secondary navigation belongs above
   content, never in a left rail that loses alignment while scrolling. */
.system-layout{display:block!important;grid-template-columns:none!important}
.system-nav,.subnav{position:sticky!important;top:49px!important;z-index:45!important;display:flex!important;flex-direction:row!important;flex-wrap:nowrap!important;gap:7px!important;max-width:100%!important;overflow-x:auto!important;overflow-y:hidden!important;-webkit-overflow-scrolling:touch;background:var(--panel,#111827)!important;padding:8px!important;border-radius:10px!important;margin:0 0 10px!important}
.system-nav a,.subnav a{display:inline-flex!important;flex:0 0 auto!important;white-space:nowrap!important;align-items:center!important}
.system-content{min-width:0!important;width:100%!important}

/* Per-page index is only injected when the page has no domain-specific
   secondary navigation. */
.porota-page-index{position:sticky;top:96px;z-index:40;display:flex;align-items:center;gap:7px;max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch;padding:7px 9px;margin:8px 0 12px;border:1px solid var(--border,#334155);border-radius:10px;background:var(--panel,#111827)}
.porota-page-index strong{flex:0 0 auto;white-space:nowrap}
.porota-page-index a{flex:0 0 auto;white-space:nowrap;text-decoration:none;padding:5px 8px;border:1px solid var(--border,#334155);border-radius:8px}
h2[id],h3[id]{scroll-margin-top:150px}

/* Any legacy generated captions remain visually valid, but RC6 no longer
   generates a caption duplicating the section heading. */
.paper-table caption,.classic-responsive-table caption,.porota-table-title{display:table-caption!important;caption-side:top!important;text-align:left!important;font-weight:750!important;font-size:.98rem!important;line-height:1.25!important;white-space:normal!important;padding:8px 6px!important;color:inherit!important;background:transparent!important}

/* Keep the canonical top menu visible while scrolling long dashboards. */
#porota-canonical-nav{position:sticky!important;top:0!important;z-index:60!important;overflow-x:auto!important;flex-wrap:nowrap!important;-webkit-overflow-scrolling:touch}
#porota-canonical-nav a{flex:0 0 auto!important;white-space:nowrap!important}

@media(max-width:980px){
  .paper-table tr.porota-table-header,.classic-responsive-table tr.porota-table-header{position:static!important;width:auto!important;height:auto!important;padding:0!important;margin:0!important;overflow:visible!important;clip:auto!important;clip-path:none!important;white-space:normal!important;display:table-row!important}
  .paper-table tr.porota-table-header th,.classic-responsive-table tr.porota-table-header th{display:table-cell!important;visibility:visible!important}
}
@media(max-width:900px){.paper-table,.classic-responsive-table{font-size:.82rem!important}.paper-table th,.paper-table td,.classic-responsive-table th,.classic-responsive-table td{padding:6px 8px!important}.porota-page-index{top:92px}}
@media(max-width:620px){.paper-table,.classic-responsive-table{font-size:.78rem!important}.paper-table th,.paper-table td,.classic-responsive-table th,.classic-responsive-table td{padding:5px 7px!important}.system-nav,.subnav{top:46px!important}.porota-page-index{top:88px!important}}
</style>
"""

_TABLE_TAG=re.compile(r"<table\b[^>]*>",re.IGNORECASE)
_CLASS_ATTR=re.compile(r"\bclass=(['\"])(.*?)\1",re.IGNORECASE)
_HEADING=re.compile(r"<h([23])\b([^>]*)>(.*?)</h\1>",re.IGNORECASE|re.DOTALL)
_BAD_WRAP="overflow-wrap:"+"anywhere"
_PAGE_INDEX_RE=re.compile(r"<nav\b[^>]*id=['\"]porota-page-index['\"][^>]*>.*?</nav>",re.IGNORECASE|re.DOTALL)
_SECONDARY_NAV_RE=re.compile(r"<nav\b[^>]*class=['\"][^'\"]*(?:subnav|system-nav)[^'\"]*['\"][^>]*>.*?</nav>",re.IGNORECASE|re.DOTALL)
_GENERATED_CAPTION_RE=re.compile(r"<caption\b[^>]*class=['\"][^'\"]*porota-table-title[^'\"]*['\"][^>]*>.*?</caption>",re.IGNORECASE|re.DOTALL)


def _classicize_table_tag(match):
    tag=match.group(0)
    attr=_CLASS_ATTR.search(tag)
    if attr:
        classes=attr.group(2).split()
        if 'classic-responsive-table' not in classes:
            quote=attr.group(1)
            replacement=f"class={quote}{attr.group(2)} classic-responsive-table{quote}"
            tag=tag[:attr.start()]+replacement+tag[attr.end():]
        return tag
    return tag[:-1]+" class='classic-responsive-table'>"


def _plain(fragment):
    return html.unescape(re.sub(r'<[^>]+>',' ',fragment)).strip()


def _slug(label, used):
    raw=unicodedata.normalize('NFKD',label).encode('ascii','ignore').decode('ascii').lower()
    raw=re.sub(r'[^a-z0-9]+','-',raw).strip('-') or 'seccion'
    candidate=raw; n=2
    while candidate in used:
        candidate=f'{raw}-{n}'; n+=1
    used.add(candidate)
    return candidate


def _dedupe_secondary_navs(text):
    seen=set()
    def repl(match):
        block=match.group(0)
        signature=re.sub(r'\s+',' ',_plain(block)).strip().lower()
        if signature in seen:
            return ''
        seen.add(signature)
        return block
    return _SECONDARY_NAV_RE.sub(repl,text)


def _decorate_sections(text):
    """Add stable anchors and one contextual index when no subnav exists."""
    # Remove captions generated by the previous Wave8 implementation. The h2/h3
    # is the semantic table title; duplicating it was confusing on tablet.
    text=_GENERATED_CAPTION_RE.sub('',text)
    text=_dedupe_secondary_navs(text)
    used=set(re.findall(r"\bid=['\"]([^'\"]+)['\"]",text,re.IGNORECASE))
    headings=[]
    def heading_cb(match):
        level,attrs,inner=match.group(1),match.group(2),match.group(3)
        label=_plain(inner)
        id_match=re.search(r"\bid=['\"]([^'\"]+)['\"]",attrs,re.IGNORECASE)
        anchor=id_match.group(1) if id_match else _slug(label,used)
        if not id_match:
            attrs=attrs+f" id='{html.escape(anchor,quote=True)}'"
        headings.append((anchor,label,level))
        return f'<h{level}{attrs}>{inner}</h{level}>'
    text=_HEADING.sub(heading_cb,text)

    has_secondary_nav=bool(_SECONDARY_NAV_RE.search(text))
    has_index=('id="porota-page-index"' in text or "id='porota-page-index'" in text)
    if len(headings)>=2 and not has_secondary_nav and not has_index:
        links=''.join(f"<a href='#{html.escape(anchor,quote=True)}'>{html.escape(label)}</a>" for anchor,label,_ in headings)
        index=f"<nav id='porota-page-index' class='porota-page-index' aria-label='Secciones de esta página'><strong>En esta página:</strong>{links}</nav>"
        h1_end=re.search(r'</h1>',text,re.IGNORECASE)
        if h1_end:
            text=text[:h1_end.end()]+index+text[h1_end.end():]
        else:
            main=re.search(r'<main\b[^>]*>',text,re.IGNORECASE)
            if main:
                text=text[:main.end()]+index+text[main.end():]
    return text


def _strip_live_low_value_sections(text):
    """Keep /vivo focused on positions, decisions and settlement."""
    # If an earlier normalization already built the page index, rebuild it after
    # removing these sections so stale links do not remain.
    text=_PAGE_INDEX_RE.sub('',text)
    patterns=(
        r"<div\b[^>]*class=['\"][^'\"]*paper-card[^'\"]*['\"][^>]*>\s*<h2\b[^>]*>\s*4\.\s*Scalping\s*</h2>.*?</div>",
        r"<div\b[^>]*class=['\"][^'\"]*paper-card[^'\"]*['\"][^>]*>\s*<h2\b[^>]*>\s*5\.\s*Motores\s*/\s*workers\s*</h2>.*?</div>",
    )
    for pattern in patterns:
        text=re.sub(pattern,'',text,flags=re.IGNORECASE|re.DOTALL)
    return text


def _normalize_live_html(text,path=None):
    """Apply RC6 presentation to the HTML actually served by every route."""
    path=str(path or '')
    if path in ('/vivo','/en-vivo'):
        text=_strip_live_low_value_sections(text)
    text=text.replace(_BAD_WRAP,'overflow-wrap:normal')
    text=_TABLE_TAG.sub(_classicize_table_tag,text)
    text=_decorate_sections(text)
    if 'porota-rc6-classic-responsive' not in text:
        lower=text.lower(); idx=lower.find('</head>')
        text=(text[:idx]+CLASSIC_CSS+text[idx:]) if idx>=0 else (CLASSIC_CSS+text)
    return text


class _ClassicHTMLMiddleware:
    """Final safety net for legacy HTML routes that do not use bg._document."""
    def __init__(self,app): self.app=app
    async def __call__(self,scope,receive,send):
        if scope.get('type')!='http' or scope.get('method')=='HEAD':
            return await self.app(scope,receive,send)
        start=None; parts=[]
        async def capture(message):
            nonlocal start
            kind=message.get('type')
            if kind=='http.response.start': start=message; return
            if kind!='http.response.body': await send(message); return
            parts.append(message.get('body',b''))
            if message.get('more_body',False): return
            if start is None: await send(message); return
            headers=list(start.get('headers',[])); content_type=next((v for k,v in headers if k.lower()==b'content-type'),b'')
            body=b''.join(parts)
            if b'text/html' in content_type.lower():
                body=_normalize_live_html(body.decode('utf-8','replace'),scope.get('path')).encode('utf-8')
                headers=[(k,v) for k,v in headers if k.lower()!=b'content-length']
                headers.append((b'content-length',str(len(body)).encode('ascii')))
            out_start=dict(start); out_start['headers']=headers
            await send(out_start); await send({'type':'http.response.body','body':body,'more_body':False})
        await self.app(scope,receive,capture)


def _esc(v): return html.escape(str(v if v not in (None,'') else '—'))

def _learning_section():
    data=shadow_learning.collect(); rows=[]
    for key,item in sorted((data.get('policies') or {}).items()):
        m=item.get('metrics') or {}
        rows.append('<tr>'+f'<td><b>{_esc(key)}</b></td><td>{_esc(item.get("stage"))}</td>'+f'<td>{_esc(item.get("authority"))}</td><td>{_esc(item.get("sessions"))}</td>'+f'<td>{_esc(m.get("evaluated"))}</td><td>{_esc(item.get("automatic_promotion"))}</td></tr>')
    body=''.join(rows) or "<tr><td colspan='6'>Recolectando evidencia SHADOW.</td></tr>"
    return """<section class='paper-card' id='wave8-shadow-learning-live'><h2>Aprendizaje SHADOW — wiring vivo</h2>
    <p class='paper-muted'>Evidencia contrafactual de solo lectura. No autoriza promoción automática ni órdenes reales.</p>
    <table class='paper-table classic-responsive-table'><thead><tr><th>Política</th><th>Etapa</th><th>Autoridad</th><th>Ruedas</th><th>Evaluadas</th><th>Auto promoción</th></tr></thead><tbody>"""+body+"</tbody></table></section>"


def _risk_requirement_rows(all_tables):
    requirements=(
      ('Daily Risk ledger / pérdidas realizadas',('daily_risk','daily_loss','realized')),
      ('Soft / hard stop diario',('daily_risk','kill_switch')),
      ('Overnight / carry',('overnight','carry')),
      ('Exposición por trade + stop/target + RR',('paper_positions','trade_gate')),
      ('Fees / liquidez / slippage',('fee','liquidity','slippage','trade_gate')),
      ('Concentración / correlación / sector BINDING',('sector','concentration','correlation')),
      ('Patrimonial Gate',('trade_gate_evaluations',)),
      ('Settlement / family-data risk',('settlement','candidate_universe')),
      ('Kill switch persistido fail-closed',('kill','risk_state')),
      ('Alertas operativas de riesgo',('alert','outbox')),
    )
    rows=[]
    low=[x.lower() for x in all_tables]
    for label,patterns in requirements:
        matched=[all_tables[i] for i,name in enumerate(low) if any(p in name for p in patterns)]
        state='EVIDENCE' if matched else 'PENDIENTE'
        rows.append(f"<tr><td><b>{_esc(label)}</b></td><td>{_esc(state)}</td><td>{_esc(', '.join(matched) or 'Sin persistencia identificable')}</td></tr>")
    return ''.join(rows)


def _risk_html():
    observer=(bg._rows('SELECT mode,process_state,session_state,ppi_auth,real_orders_sent,heartbeat_at FROM observer_state WHERE id=1') or [{}])[0]
    all_tables=[str(r.get('name') or '') for r in bg._rows("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    risk_tables=[name for name in all_tables if any(k in name.lower() for k in ('risk','gate','alert','kill','sector','settlement'))]
    rows=[]
    for name in risk_tables:
        try: n=(bg._rows(f'SELECT COUNT(*) AS n FROM "{name}"') or [{'n':'—'}])[0].get('n')
        except Exception: n='—'
        rows.append(f'<tr><td><b>{_esc(name)}</b></td><td>{_esc(n)}</td><td>Persistencia runtime</td></tr>')
    table=''.join(rows) or "<tr><td colspan='3'>No se encontraron tablas de riesgo; requiere reconciliación.</td></tr>"
    requirements=_risk_requirement_rows(all_tables)
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>{bg.THEME}{bg.TABLE_A11Y_CSS}{CLASSIC_CSS}</head><body>{bg.top_nav_html()}<main class='paper-page'>
    <h1>Riesgo — controles RC6</h1><div class='paper-grid'>
    <div class='paper-card'><h3>Modo</h3><b class='metric'>{_esc(observer.get('mode'))}</b></div>
    <div class='paper-card'><h3>Órdenes reales</h3><b class='metric'>{_esc(observer.get('real_orders_sent'))}</b><div class='paper-muted'>Debe ser 0</div></div>
    <div class='paper-card'><h3>PPI</h3><b class='metric'>{_esc(observer.get('ppi_auth'))}</b></div>
    <div class='paper-card'><h3>Event Risk</h3><b class='metric'>SHADOW</b><div class='paper-muted'>{len(event_contract.EVENT_TYPES)} tipos contractuales; una noticia aislada nunca habilita trading.</div></div>
    </div>
    <section class='paper-card'><h2>Matriz de controles exigidos</h2><p class='paper-muted'>EVIDENCE significa que existe persistencia identificable; no implica por sí sola que el control ya esté BINDING. PENDIENTE queda visible hasta demostrar wiring live.</p><table class='paper-table classic-responsive-table'><thead><tr><th>Requisito RC6</th><th>Evidencia</th><th>Persistencia encontrada</th></tr></thead><tbody>{requirements}</tbody></table></section>
    <section class='paper-card'><h2>Persistencia de riesgo / gates / alertas</h2><table class='paper-table classic-responsive-table'><thead><tr><th>Tabla</th><th>Registros</th><th>Rol</th></tr></thead><tbody>{table}</tbody></table></section>
    {_learning_section()}</main></body></html>"""


def _strategy_overview():
    subnav=bg.trading_nav_html()
    links=(
      ('/trading/acciones-cedears','Acciones y CEDEAR','Evaluación spot y elegibilidad por familia.'),
      ('/trading/renta-fija','Renta fija','Bonos, Letras y ON.'),
      ('/trading/cauciones','Cauciones','Evaluación PAPER, términos, costos y gates.'),
      ('/trading/opciones','Opciones','Contratos y readiness de opciones.'),
      ('/trading/futuros','Futuros','Contratos, calendario y márgenes/readiness.'),
      ('/trading/fci','FCI','Fondos locales y exterior cuando haya evidencia.'),
      ('/trading/licitaciones','Licitaciones','Licitaciones y canjes.'),
    )
    cards=''.join(f"<a class='paper-card' href='{href}' style='text-decoration:none'><h3>{label}</h3><p class='paper-muted'>{desc}</p></a>" for href,label,desc in links)
    body=("<h1>Trading — Estrategias y evaluadores</h1>"+subnav+
          "<div class='paper-notice'><b>Scalping no se duplica en esta pantalla.</b> Tiene su destino principal propio en el menú superior. Aquí se muestran los evaluadores PAPER y criterios por familia.</div>"+
          "<section><h2>Evaluadores por familia</h2><div class='paper-grid'>"+cards+"</div></section>")
    return bg._document('Trading — Estrategias y evaluadores',body,refresh=30)


def _family_activity_section(section=''):
    families=tuple(bg.families_for_group(str(section or '').strip().lower())) if str(section or '').strip() else ()
    params=()
    where=''
    if families:
        marks=','.join('?' for _ in families); where=f' WHERE upper(instrument_type) IN ({marks})'; params=tuple(x.upper() for x in families)
    universe=bg._rows(f"SELECT upper(instrument_type) family,COUNT(*) total,SUM(CASE WHEN upper(status)='AVAILABLE' THEN 1 ELSE 0 END) available,SUM(CASE WHEN can_simulate=1 THEN 1 ELSE 0 END) can_simulate FROM candidate_universe{where} GROUP BY upper(instrument_type) ORDER BY family",params)
    if not universe:
        return ''
    decision_rows=bg._rows("""WITH u AS (SELECT DISTINCT ticker,upper(instrument_type) family FROM candidate_universe)
      SELECT u.family,COUNT(*) decisions,SUM(CASE WHEN d.action='BUY' THEN 1 ELSE 0 END) buys,SUM(CASE WHEN d.action='HOLD' THEN 1 ELSE 0 END) holds,MAX(d.decided_at) last_decision
      FROM paper_decisions d JOIN u ON u.ticker=d.symbol
      WHERE substr(d.decided_at,1,10)=strftime('%Y-%m-%d','now') GROUP BY u.family""")
    decisions={str(r.get('family') or '').upper():r for r in decision_rows}
    snapshot_rows=bg._rows("""SELECT upper(asset_class) family,COUNT(*) snapshots,COUNT(DISTINCT symbol) symbols,MAX(observed_at) last_snapshot
      FROM market_snapshots WHERE substr(observed_at,1,10)=strftime('%Y-%m-%d','now') GROUP BY upper(asset_class)""")
    snaps={str(r.get('family') or '').upper():r for r in snapshot_rows}
    blockers=bg._rows(f"SELECT upper(instrument_type) family,detail,COUNT(*) n FROM candidate_universe{where} GROUP BY upper(instrument_type),detail ORDER BY family,n DESC",params)
    blocker_map={}
    for r in blockers:
        blocker_map.setdefault(str(r.get('family') or '').upper(),str(r.get('detail') or '—'))
    rows=[]
    for u in universe:
        fam=str(u.get('family') or '').upper(); d=decisions.get(fam,{}); s=snaps.get(fam,{})
        rows.append('<tr>'+f"<td><b>{_esc(fam)}</b></td><td>{_esc(u.get('available'))}/{_esc(u.get('total'))}</td><td>{_esc(u.get('can_simulate'))}</td>"+f"<td>{_esc(s.get('symbols',0))}</td><td>{_esc(d.get('decisions',0))}</td><td>{_esc(d.get('buys',0))}</td><td>{_esc(d.get('holds',0))}</td><td>{_esc(blocker_map.get(fam,'—'))}</td><td>{_esc(bg._local_time(d.get('last_decision')))}</td></tr>")
    return """<section class='paper-card' id='rc6-family-live-activity'><h2>Actividad real de análisis por familia — hoy</h2><p class='paper-muted'>Esto muestra observación y decisiones PAPER aunque una familia siga bloqueada para abrir posiciones. AVAILABLE no equivale a can_simulate.</p><table class='paper-table classic-responsive-table'><thead><tr><th>Familia</th><th>AVAILABLE/Total</th><th>can_simulate</th><th>Símbolos observados</th><th>Decisiones</th><th>BUY</th><th>HOLD</th><th>Blocker principal</th><th>Última decisión</th></tr></thead><tbody>"""+''.join(rows)+"</tbody></table></section>"


def _append_before_main_end(page, fragment):
    marker='</main>'
    return page.replace(marker,fragment+marker,1) if marker in page else page+fragment


def install(app, check_auth):
    global _installed
    if _installed: return
    _installed=True
    if 'porota-rc6-classic-responsive' not in bg.TABLE_A11Y_CSS:
        bg.TABLE_A11Y_CSS += CLASSIC_CSS

    old_document=bg._document
    def document_live(*args,**kwargs): return _normalize_live_html(old_document(*args,**kwargs))
    bg._document=document_live

    app.add_middleware(_ClassicHTMLMiddleware)
    old_learning=bg.learning_page; old_validation=bg.validation_page; old_trading=bg.trading_page
    def learning_page_live(): return _append_before_main_end(old_learning(),_learning_section())
    def validation_page_live(): return _append_before_main_end(old_validation(),_learning_section())
    def trading_page_live(section=''):
        section=str(section or '').strip().lower()
        page=_strategy_overview() if section=='estrategias' else old_trading(section)
        return _append_before_main_end(page,_family_activity_section(section))
    bg.learning_page=learning_page_live; bg.validation_page=validation_page_live; bg.trading_page=trading_page_live

    @app.get('/riesgo',response_class=HTMLResponse)
    def riesgo(request:Request,token:str=Query(default=''),authorization:str|None=Header(default=None)):
        bg._authorize(check_auth,request,token,authorization)
        return HTMLResponse(_normalize_live_html(_risk_html(),'/riesgo'))
