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
import json
import os
from pathlib import Path
import bg_paper_dashboard as bg
import et_shadow_learning_rc6 as shadow_learning
import fi_event_risk_shadow_rc6 as event_contract

_installed=False
CLASSIC_CSS="""
<style id='porota-rc6-classic-responsive'>
/* Tablet contract: preserve real tables, fit the viewport, and expose long
   values through ellipsis/title instead of forcing horizontal page scroll. */
.paper-card,.tarjeta{max-width:100%;min-width:0;overflow:hidden!important;box-sizing:border-box}
.paper-table,.classic-responsive-table{display:table!important;width:100%!important;min-width:0!important;max-width:100%!important;table-layout:fixed!important;border-collapse:collapse!important}
.paper-table thead,.classic-responsive-table thead{display:table-header-group!important}
.paper-table tbody,.classic-responsive-table tbody{display:table-row-group!important}
.paper-table tr,.classic-responsive-table tr{display:table-row!important}
.paper-table th,.paper-table td,.classic-responsive-table th,.classic-responsive-table td{display:table-cell!important;min-width:0!important;max-width:0!important;white-space:nowrap!important;overflow:hidden!important;text-overflow:ellipsis!important;overflow-wrap:normal!important;word-break:normal!important;vertical-align:middle!important}
.paper-table th,.paper-table td{padding:7px 8px!important}
.paper-table td[data-wrap='true'],.classic-responsive-table td[data-wrap='true'],.paper-table td[data-porota-expanded='1'],.classic-responsive-table td[data-porota-expanded='1']{white-space:normal!important;max-width:none!important;overflow:visible!important;overflow-wrap:anywhere!important;word-break:break-word!important;background:#fff!important}
.paper-table tr.porota-table-header,.classic-responsive-table tr.porota-table-header{display:table-row!important;position:static!important;visibility:visible!important}
.paper-table tr.porota-table-header th,.classic-responsive-table tr.porota-table-header th{display:table-cell!important;visibility:visible!important;white-space:nowrap!important}
.system-layout{display:block!important;grid-template-columns:none!important}
html,body,main,.paper-page,.paper-card,.paper-grid,.system-content,.legacy-shell{min-width:0!important;max-width:100vw!important;box-sizing:border-box}
html,body{overflow-x:hidden!important}
.system-nav,.subnav{position:sticky!important;top:49px!important;z-index:45!important;display:flex!important;flex-direction:row!important;flex-wrap:wrap!important;gap:7px!important;max-width:100%!important;overflow:visible!important;background:var(--panel,#111827)!important;padding:8px!important;border-radius:10px!important;margin:0 0 10px!important}
.system-nav a,.subnav a{display:inline-flex!important;flex:0 0 auto!important;white-space:nowrap!important;align-items:center!important}
.system-content{min-width:0!important;width:100%!important}
.porota-page-index{position:sticky;top:96px;z-index:40;display:flex;flex-wrap:wrap;align-items:center;gap:7px;max-width:100%;overflow:visible;padding:7px 9px;margin:8px 0 12px;border:1px solid var(--border,#334155);border-radius:10px;background:var(--panel,#111827)}
.porota-page-index strong,.porota-page-index a{flex:0 1 auto;white-space:normal}
#porota-canonical-nav{position:sticky!important;top:0!important;z-index:60!important;overflow:visible!important;flex-wrap:wrap!important}
#porota-canonical-nav a{flex:0 1 auto!important;white-space:normal!important}
@media(max-width:900px){.paper-table,.classic-responsive-table{font-size:.82rem!important}.paper-table th,.paper-table td,.classic-responsive-table th,.classic-responsive-table td{padding:6px 5px!important}}
@media(max-width:620px){.paper-table,.classic-responsive-table{font-size:.78rem!important}.paper-table th,.paper-table td,.classic-responsive-table th,.classic-responsive-table td{padding:5px 4px!important}.system-nav,.subnav{top:46px!important}.subnav{flex-wrap:wrap!important}.porota-page-index{top:88px!important}}
</style>
"""

RISK_READABLE_CSS="""
<style id='porota-rc6-risk-readable'>
/* Risk is an operator evidence surface: long control/evidence text must stay
   readable instead of inheriting the global max-width:0 + nowrap ellipsis. */
.porota-risk-page .paper-card{overflow-x:auto!important}
.porota-risk-page .paper-table,.porota-risk-page .classic-responsive-table{
  table-layout:auto!important;
}
.porota-risk-page .paper-table th,.porota-risk-page .paper-table td,
.porota-risk-page .classic-responsive-table th,.porota-risk-page .classic-responsive-table td{
  min-width:6rem!important;
  max-width:none!important;
  white-space:normal!important;
  overflow:visible!important;
  text-overflow:clip!important;
  overflow-wrap:anywhere!important;
  word-break:break-word!important;
  vertical-align:top!important;
}
.porota-risk-page .paper-table td[data-wrap='true'],
.porota-risk-page .classic-responsive-table td[data-wrap='true']{
  max-width:none!important;
  white-space:normal!important;
}
@media(max-width:620px){
  .porota-risk-page .paper-table,.porota-risk-page .classic-responsive-table{
    font-size:.78rem!important;
  }
}
</style>
"""

_TABLE_TAG=re.compile(r"<table\b[^>]*>",re.IGNORECASE)
_CLASS_ATTR=re.compile(r"\bclass=(['\"])(.*?)\1",re.IGNORECASE)
_HEADING=re.compile(r"<h([23])\b([^>]*)>(.*?)</h\1>",re.IGNORECASE|re.DOTALL)
_BAD_WRAP="overflow-wrap:"+"anywhere"
_PAGE_INDEX_RE=re.compile(r"<nav\b[^>]*id=['\"]porota-page-index['\"][^>]*>.*?</nav>",re.IGNORECASE|re.DOTALL)
_SECONDARY_NAV_RE=re.compile(r"<(?P<tag>nav|div|aside)\b[^>]*class=['\"][^'\"]*(?:subnav|system-nav)[^'\"]*['\"][^>]*>.*?</(?P=tag)>",re.IGNORECASE|re.DOTALL)
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
    # Normalization can run more than once (bg._document + ASGI middleware).
    # If a domain-specific subnav is present, remove any page index injected by
    # an earlier pass so the operator sees exactly one navigation surface.
    if _SECONDARY_NAV_RE.search(text):
        text=_PAGE_INDEX_RE.sub('',text)
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
    """Preserve the complete read-only /vivo operational surface.

    RC6 operator contract keeps Scalping and Motores / workers visible in the
    consolidated live view. Presentation normalization may still de-duplicate
    navigation, but it must not delete operational/introspection sections.
    """
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
      ('Settlement / family-data risk',('settlement','candidate_identity_v2')),
      ('Kill switch persistido fail-closed',('kill','risk_state')),
      ('Alertas operativas de riesgo',('alert','outbox')),
    )
    rows=[]
    low=[x.lower() for x in all_tables]
    for label,patterns in requirements:
        matched=[all_tables[i] for i,name in enumerate(low) if any(p in name for p in patterns)]
        state='EVIDENCE' if matched else 'PENDIENTE'
        rows.append(f"<tr><td data-wrap='true'><b>{_esc(label)}</b></td><td>{_esc(state)}</td><td data-wrap='true'>{_esc(', '.join(matched) or 'Sin persistencia identificable')}</td></tr>")
    return ''.join(rows)


def _risk_html():
    observer=(bg._rows('SELECT mode,process_state,session_state,ppi_auth,real_orders_sent,heartbeat_at FROM observer_state WHERE id=1') or [{}])[0]
    all_tables=[str(r.get('name') or '') for r in bg._rows("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    risk_tables=[name for name in all_tables if any(k in name.lower() for k in ('risk','gate','alert','kill','sector','settlement'))]
    rows=[]
    for name in risk_tables:
        try: n=(bg._rows(f'SELECT COUNT(*) AS n FROM "{name}"') or [{'n':'—'}])[0].get('n')
        except Exception: n='—'
        rows.append(f"<tr><td data-wrap='true'><b>{_esc(name)}</b></td><td>{_esc(n)}</td><td data-wrap='true'>Persistencia runtime</td></tr>")
    table=''.join(rows) or "<tr><td colspan='3'>No se encontraron tablas de riesgo; requiere reconciliación.</td></tr>"
    requirements=_risk_requirement_rows(all_tables)
    daily = bg._rows("""SELECT day,currency,state,baseline_equity,daily_pnl,loss_budget,evaluated_at,detail
        FROM paper_daily_risk ORDER BY julianday(evaluated_at) DESC LIMIT 12""") if bg._table('paper_daily_risk') else []
    daily_rows=''.join(
        "<tr>"+f"<td>{_esc(r.get('day'))}</td><td><b>{_esc(r.get('currency'))}</b></td>"
        +f"<td>{bg._status(r.get('state'))}</td><td>{_esc(r.get('daily_pnl'))}</td>"
        +f"<td>{_esc(r.get('loss_budget'))}</td><td>{_esc(r.get('evaluated_at'))}</td>"
        +f"<td data-wrap='true'>{_esc(r.get('detail'))}</td></tr>" for r in daily
    ) or "<tr><td colspan='7'>Sin evaluaciones persistidas.</td></tr>"
    health = bg._rows("""SELECT component,source,state,checked_at,last_success_at,detail
        FROM api_health ORDER BY julianday(checked_at) DESC LIMIT 30""") if bg._table('api_health') else []
    health_rows=''.join(
        "<tr>"+f"<td><b>{_esc(r.get('component'))}</b></td><td>{_esc(r.get('source'))}</td>"
        +f"<td>{bg._status(r.get('state'))}</td><td>{_esc(r.get('checked_at'))}</td>"
        +f"<td>{_esc(r.get('last_success_at'))}</td><td data-wrap='true'>{_esc(r.get('detail'))}</td></tr>"
        for r in health
    ) or "<tr><td colspan='6'>api_health no tiene filas; estado NO_VERIFICADO.</td></tr>"
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>{bg.THEME}{bg.TABLE_A11Y_CSS}{CLASSIC_CSS}{RISK_READABLE_CSS}</head><body>{bg.top_nav_html()}<main class='paper-page porota-risk-page'>
    <h1>Riesgo — controles RC6</h1><div class='paper-grid'>
    <div class='paper-card'><h3>Modo</h3><b class='metric'>{_esc(observer.get('mode'))}</b></div>
    <div class='paper-card'><h3>Órdenes reales</h3><b class='metric'>{_esc(observer.get('real_orders_sent'))}</b><div class='paper-muted'>Debe ser 0</div></div>
    <div class='paper-card'><h3>PPI</h3><b class='metric'>{_esc(observer.get('ppi_auth'))}</b></div>
    <div class='paper-card'><h3>Event Risk</h3><b class='metric'>SHADOW</b><div class='paper-muted'>{len(event_contract.EVENT_TYPES)} tipos contractuales; una noticia aislada nunca habilita trading.</div></div>
    </div>
    <section class='paper-card'><h2>Matriz de controles exigidos</h2><p class='paper-muted'>EVIDENCE significa que existe persistencia identificable; no implica por sí sola que el control ya esté BINDING. PENDIENTE queda visible hasta demostrar wiring live.</p><table class='paper-table classic-responsive-table'><thead><tr><th>Requisito RC6</th><th>Evidencia</th><th>Persistencia encontrada</th></tr></thead><tbody>{requirements}</tbody></table></section>
    <section class='paper-card'><h2>Estado financiero de riesgo</h2><p class='paper-muted'>Fuente: paper_daily_risk. Un estado CLOCK_ROLLBACK sólo es válido ante un retroceso real; las carreras de workers reutilizan el snapshot más fresco dentro del presupuesto de freshness.</p><table class='paper-table classic-responsive-table'><thead><tr><th>Día</th><th>Moneda</th><th>Estado</th><th>PnL diario</th><th>Presupuesto</th><th>Evaluado</th><th>Detalle</th></tr></thead><tbody>{daily_rows}</tbody></table></section>
    <section class='paper-card'><h2>Salud de APIs y fuentes</h2><p class='paper-muted'>Fuente directa: api_health. ROJO/AMARILLO describen el componente nombrado; no se extrapolan al proveedor completo.</p><table class='paper-table classic-responsive-table'><thead><tr><th>Componente</th><th>Fuente</th><th>Estado</th><th>Chequeado</th><th>Último éxito</th><th>Detalle</th></tr></thead><tbody>{health_rows}</tbody></table></section>
    <section class='paper-card'><h2>Persistencia de riesgo / gates / alertas</h2><table class='paper-table classic-responsive-table'><thead><tr><th>Tabla</th><th>Registros</th><th>Rol</th></tr></thead><tbody>{table}</tbody></table></section>
    {_learning_section()}</main></body></html>"""


def _strategy_overview():
    subnav=bg.trading_nav_html()
    truth=bg.truth_projection()
    by_family={str(row.get('family') or '').upper():row for row in truth.get('readiness',{}).get('families',[])}
    links=(
      ('/trading/acciones-cedears','Acciones y CEDEAR',('ACCIONES','CEDEARS'),'Readiness spot, PPI primario e IOL complementario.'),
      ('/trading/bonos','Bonos',('BONOS',),'Identidad, nominales, liquidación y evidencia.'),
      ('/trading/on','Obligaciones negociables',('OBLIGACIONES','ON'),'Nominal, flujo, vencimiento, costos y contrato.'),
      ('/trading/cauciones','Cauciones',('CAUCIONES',),'Colocadora PAPER: fuente, tasa, ventana, caja, reserva y liquidación.'),
      ('/trading/letras','Letras',('LETRAS',),'Nominales, vencimiento y contrato PPI.'),
      ('/trading/etf','ETF',('ETFS','ETF'),'Catálogo, identidad y datos de mercado.'),
      ('/trading/indices','Índices',('INDICES',),'Tipo de instrumento y fuente contractual.'),
      ('/trading/futuros','Futuros',('FUTUROS',),'Contrato, margen, vencimiento y riesgo.'),
      ('/trading/opciones','Opciones',('OPCIONES',),'Contrato, strike, vencimiento y prima.'),
      ('/scalping','Scalping',(),'Evidencia intradiaria, scanner y PAPER/SHADOW.'),
    )
    cards=[]
    for href,label,families,desc in links:
        rows=[by_family.get(f) for f in families if by_family.get(f)]
        ready=sum(int(r.get('runtime_ready') or 0) for r in rows)
        total=sum(int(r.get('candidate_total') or 0) for r in rows)
        states=sorted({str(r.get('state') or 'NO_VERIFICADO') for r in rows})
        state=(" / ".join(states) if states else
               (str(truth.get('scalping',{}).get('worker_state') or 'NO_VERIFICADO') if label=='Scalping' else 'NO_VERIFICADO'))
        tone='s-verde' if rows and all(x=='RUNTIME_READY' for x in states) else 's-amarillo' if rows else 's-gris'
        counts=(f"{bg._locale_number(ready,0)}/{bg._locale_number(total,0)} RUNTIME_READY" if rows else desc)
        cards.append(f"<a class='paper-card' href='{href}' style='text-decoration:none'><h3>{label}</h3><span class='paper-status {tone}'>{_esc(state)}</span><p><b>{_esc(counts)}</b></p><p class='paper-muted'>{desc}</p></a>")
    cards=''.join(cards)
    body=("<h1>Trading — Estrategias y readiness</h1>"+subnav+
          "<div class='paper-notice'><b>Readiness por familia:</b> cada pantalla muestra objetivo, evidencia observada, brechas y siguiente acción. Una familia visible no equivale a permiso operativo.</div>"+
          "<section><h2>Familias y evaluadores</h2><div class='paper-grid'>"+cards+"</div></section>")
    return bg._document('Trading — Estrategias y readiness',body,refresh=30)


def _family_activity_section(section=''):
    requested=tuple(bg.families_for_group(str(section or '').strip().lower())) if str(section or '').strip() else ()
    truth=bg.truth_projection()
    by_family={str(row.get('family') or '').upper():row for row in truth['readiness']['families']}
    families=tuple(bg.dashboard_truth_projection.normalize_family(x) for x in requested) or tuple(sorted(by_family))
    decisions=bg._rows("""WITH u AS (SELECT DISTINCT ticker,upper(instrument_type) family FROM candidate_identity_v2 WHERE can_simulate=1 AND upper(status)='AVAILABLE')
      SELECT u.family,COUNT(*) decisions,SUM(CASE WHEN d.action='BUY' THEN 1 ELSE 0 END) buys,SUM(CASE WHEN d.action='HOLD' THEN 1 ELSE 0 END) holds,MAX(d.decided_at) last_decision
      FROM paper_decisions d JOIN u ON u.ticker=d.symbol
      WHERE substr(d.decided_at,1,10)=strftime('%Y-%m-%d','now') GROUP BY u.family""") if bg._table('candidate_identity_v2') and bg._table('paper_decisions') else []
    decision_map={bg.dashboard_truth_projection.normalize_family(row.get('family')):row for row in decisions}
    objectives={
      'ACCIONES':'PPI/IOL spot fresco + contrato PPI',
      'CEDEARS':'PPI spot + ratio/moneda + validación IOL',
      'BONOS':'Nominal, moneda, liquidación y contrato',
      'OBLIGACIONES':'Nominal, flujo, vencimiento, costos y contrato',
      'CAUCIONES':'Colocadora PAPER: tasa, ventana, caja, reserva y liquidación',
      'LETRAS':'Nominal, vencimiento y contrato',
      'ETFS':'Identidad, mercado y datos de cotización',
      'FUTUROS':'Contrato, margen, vencimiento y riesgo',
      'OPCIONES':'Contrato, strike, vencimiento y prima',
      'INDICES':'Tipo de instrumento y fuente contractual',
    }
    gaps={
      'ACCIONES':'Conciliación PPI/IOL completa y fresca; IOL sigue SHADOW',
      'CEDEARS':'Comparación PPI/IOL completa; ratio/moneda faltante donde aplique',
      'BONOS':'Unidades nominales y contrato PPI',
      'OBLIGACIONES':'Unidades nominales, flujo y contrato PPI',
      'CAUCIONES':'Mostrar sólo faltantes persistidos por planner/allocator; garantía no es requisito genérico',
      'LETRAS':'Unidades nominales y contrato PPI',
      'ETFS':'Identidad/catálogo y mercado',
      'FUTUROS':'Contrato, margen y vencimiento',
      'OPCIONES':'Contrato, strike, vencimiento y prima',
      'INDICES':'Tipo de instrumento y contrato',
    }
    rows=[]
    for fam in families:
      row=by_family.get(fam,{})
      total=int(row.get('catalog_total') or 0); available=int(row.get('catalog_available') or 0)
      candidates=int(row.get('candidate_total') or 0); paper_ready=int(row.get('runtime_ready') or 0)
      paused=int(row.get('paused_explicit') or 0)
      d=decision_map.get(fam,{})
      state=str(row.get('state') or 'NO_VERIFICADO')
      if state=='RUNTIME_READY':
        state_css='s-verde'
      elif state in {'PARTIAL','PAUSED_EXPLICIT'}:
        state_css='s-amarillo'
      else:
        state_css='s-gris'
      evidence=(f"{bg._locale_number(paper_ready,0)}/{bg._locale_number(candidates,0)} RUNTIME_READY · "
                f"{bg._locale_number(available,0)}/{bg._locale_number(total,0)} catálogo disponible · "
                f"{bg._locale_number(paused,0)} PAUSED_EXPLICIT · "
                f"{bg._locale_number(int(d.get('decisions') or 0),0)} decisiones")
      next_action=('Aplicar gates de estrategia; READY no equivale a apertura.'
                   if state=='RUNTIME_READY' else gaps.get(fam,'Revisar detalle persistido en candidate_identity_v2.'))
      rows.append(f"<tr><td><b>{_esc(fam)}</b></td><td><span class='paper-status {state_css}'>{_esc(state)}</span></td><td title='{_esc(objectives.get(fam,''))}'>{_esc(objectives.get(fam,''))}</td><td title='{_esc(evidence)}'>{_esc(evidence)}</td><td title='{_esc(next_action)}'>{_esc(next_action)}</td></tr>")
    return """<section class='paper-card' id='rc6-family-readiness'><h2>Readiness por familia</h2><p class='paper-muted'>Fuente única: candidate_identity_v2. Catálogo, contrato, histórico y elegibilidad de estrategia se muestran como conceptos distintos. IOL complementa; no redefine READY.</p><table class='paper-table classic-responsive-table'><thead><tr><th>Familia</th><th>Estado</th><th>Objetivo</th><th>Evidencia/progreso</th><th>Siguiente etapa</th></tr></thead><tbody>"""+''.join(rows)+"</tbody></table></section>"


def _evidence_detail_section():
    """Evidence v2 coverage and current IOL health; neither is readiness."""
    truth=bg.truth_projection(); contract=truth['contract']; iol=truth['iol']
    family_rows=[]
    for item in contract.get('families',[]):
        family_rows.append("<tr>"+f"<td><b>{bg._e(item.get('family'))}</b></td>"+
            f"<td>{bg._e(item.get('identities'))}</td><td>{bg._e(item.get('evidence_rows'))}</td>"+
            f"<td>{bg._e(item.get('sources'))}</td><td>{bg._local_time(item.get('as_of'))}</td></tr>")
    sections=[]
    for name,item in (iol.get('families',{}).get('sections',{}) or {}).items():
        sections.append(f"<tr><td><b>{bg._e(name)}</b></td><td>{bg._status(item.get('state'))}</td>"+
                        f"<td>{bg._e(item.get('raw_source_state'))}</td><td>{bg._local_time(item.get('as_of'))}</td></tr>")
    return (
        "<section class='paper-card' id='rc6-evidence-progress'>"
        "<h2>Evidence v2 e IOL actual</h2>"
        "<p class='paper-muted'>Contrato usa contract_evidence_v2_current. Readiness usa candidate_identity_v2. "
        "IOL es complementario. SOURCE_UNAVAILABLE describe exclusivamente el collector/cache interno de la sección: "
        "no equivale a IOL/MCP caído ni cambia un READY de PPI. Se muestra timestamp/LKG para distinguir ambos conceptos.</p>"
        f"<p class='paper-muted'><b>IOL family cache:</b> {bg._status(iol.get('families',{}).get('state'))} · "
        f"refrescado {bg._local_time(iol.get('families',{}).get('as_of'))} · "
        f"LKG {bg._local_time(iol.get('families',{}).get('last_known_good_at'))}</p>"
        "<table class='paper-table classic-responsive-table'><thead><tr><th>Familia</th><th>Identidades</th>"
        "<th>Filas Evidence v2</th><th>Fuentes</th><th>Actualizado</th></tr></thead><tbody>"+
        (''.join(family_rows) or "<tr><td colspan='5'>Evidence v2 no disponible.</td></tr>")+"</tbody></table>"
        "<h3>Salud actual IOL por sección</h3><table class='paper-table classic-responsive-table'><thead><tr>"
        "<th>Sección</th><th>Estado visible</th><th>Estado source-path</th><th>Actualizado</th></tr></thead><tbody>"+
        (''.join(sections) or "<tr><td colspan='4'>SOURCE_UNAVAILABLE sin cache publicado.</td></tr>")+"</tbody></table>"
        "</section>"
    )

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
    old_analysis_render=bg.annual_instrument_analysis.render_page
    def learning_page_live(): return _append_before_main_end(old_learning(),_learning_section())
    def validation_page_live(): return _append_before_main_end(old_validation(),_learning_section())
    def analysis_render_live(*args,**kwargs):
        return old_analysis_render(*args,**kwargs)+_evidence_detail_section()
    def trading_page_live(section=''):
        section=str(section or '').strip().lower()
        page=_strategy_overview() if section=='estrategias' else old_trading(section)
        return _append_before_main_end(page,_family_activity_section(section)+_evidence_detail_section())
    bg.learning_page=learning_page_live; bg.validation_page=validation_page_live; bg.trading_page=trading_page_live
    bg.annual_instrument_analysis.render_page=analysis_render_live

    @app.get('/riesgo',response_class=HTMLResponse)
    def riesgo(request:Request,token:str=Query(default=''),authorization:str|None=Header(default=None)):
        bg._authorize(check_auth,request,token,authorization)
        return HTMLResponse(_normalize_live_html(_risk_html(),'/riesgo'))
