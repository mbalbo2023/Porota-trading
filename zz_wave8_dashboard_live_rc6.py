"""RC6 dashboard live-wiring and accessibility closure.

Dashboard-only, read-only wiring. Keeps tabular data as real rows/columns,
adds the Risk destination, SHADOW learning evidence, stable top navigation,
section indexes and visible table captions. No broker/order capability.
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

/* RC6 accessibility/navigation closure: secondary navigation belongs above
   content, never in a left rail that loses alignment while scrolling. */
.system-layout{display:block!important;grid-template-columns:none!important}
.system-nav,.subnav{position:sticky!important;top:49px!important;z-index:45!important;display:flex!important;flex-direction:row!important;flex-wrap:nowrap!important;gap:7px!important;max-width:100%!important;overflow-x:auto!important;overflow-y:hidden!important;-webkit-overflow-scrolling:touch;background:var(--panel,#111827)!important;padding:8px!important;border-radius:10px!important;margin:0 0 10px!important}
.system-nav a,.subnav a{display:inline-flex!important;flex:0 0 auto!important;white-space:nowrap!important;align-items:center!important}
.system-content{min-width:0!important;width:100%!important}

/* Per-page index: always tells the operator what information exists below. */
.porota-page-index{position:sticky;top:96px;z-index:40;display:flex;align-items:center;gap:7px;max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch;padding:7px 9px;margin:8px 0 12px;border:1px solid var(--border,#334155);border-radius:10px;background:var(--panel,#111827)}
.porota-page-index strong{flex:0 0 auto;white-space:nowrap}
.porota-page-index a{flex:0 0 auto;white-space:nowrap;text-decoration:none;padding:5px 8px;border:1px solid var(--border,#334155);border-radius:8px}
h2[id],h3[id]{scroll-margin-top:150px}

/* Every table has a visible semantic title. */
.paper-table caption,.classic-responsive-table caption,.porota-table-title{display:table-caption!important;caption-side:top!important;text-align:left!important;font-weight:750!important;font-size:.98rem!important;line-height:1.25!important;white-space:normal!important;padding:8px 6px!important;color:inherit!important;background:transparent!important}

/* Keep the canonical top menu visible while scrolling long dashboards. */
#porota-canonical-nav{position:sticky!important;top:0!important;z-index:60!important;overflow-x:auto!important;flex-wrap:nowrap!important;-webkit-overflow-scrolling:touch}
#porota-canonical-nav a{flex:0 0 auto!important;white-space:nowrap!important}

@media(max-width:900px){.paper-table,.classic-responsive-table{font-size:.82rem!important}.paper-table th,.paper-table td,.classic-responsive-table th,.classic-responsive-table td{padding:6px 8px!important}.porota-page-index{top:92px}}
@media(max-width:620px){.paper-table,.classic-responsive-table{font-size:.78rem!important}.paper-table th,.paper-table td,.classic-responsive-table th,.classic-responsive-table td{padding:5px 7px!important}.system-nav,.subnav{top:46px!important}.porota-page-index{top:88px!important}}
</style>
"""

_TABLE_TAG=re.compile(r"<table\b[^>]*>",re.IGNORECASE)
_CLASS_ATTR=re.compile(r"\bclass=(['\"])(.*?)\1",re.IGNORECASE)
_HEADING=re.compile(r"<h([23])\b([^>]*)>(.*?)</h\1>",re.IGNORECASE|re.DOTALL)
_BAD_WRAP="overflow-wrap:"+"anywhere"


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


def _decorate_sections(text):
    """Add stable anchors, a sticky page index and visible table captions."""
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

    # Caption tables from the closest preceding section heading.
    section_re=re.compile(r"(<h([23])\b[^>]*>(.*?)</h\2>)(.*?)(?=<h[23]\b|</main>|$)",re.IGNORECASE|re.DOTALL)
    def section_cb(match):
        head,inner,tail=match.group(1),match.group(3),match.group(4)
        label=_plain(inner) or 'Detalle de datos'
        def table_cb(tm):
            tag=tm.group(0)
            return tag+f"<caption class='porota-table-title'>{html.escape(label)}</caption>"
        tail=re.sub(r"<table\b(?![^>]*data-porota-captioned)[^>]*>(?!\s*<caption\b)",table_cb,tail,flags=re.IGNORECASE)
        return head+tail
    text=section_re.sub(section_cb,text)
    # Residual tables before any h2/h3 still need a visible title.
    text=re.sub(r"(<table\b[^>]*>)(?!\s*<caption\b)",r"\1<caption class='porota-table-title'>Detalle de datos</caption>",text,flags=re.IGNORECASE)

    if len(headings)>=2 and 'id="porota-page-index"' not in text and "id='porota-page-index'" not in text:
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


def _normalize_live_html(text):
    """Apply RC6 presentation to the HTML actually served by every route."""
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
                body=_normalize_live_html(body.decode('utf-8','replace')).encode('utf-8')
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


def _risk_html():
    observer=(bg._rows('SELECT mode,process_state,session_state,ppi_auth,real_orders_sent,heartbeat_at FROM observer_state WHERE id=1') or [{}])[0]
    risk_tables=bg._rows("SELECT name FROM sqlite_master WHERE type='table' AND (lower(name) LIKE '%risk%' OR lower(name) LIKE '%gate%' OR lower(name) LIKE '%alert%') ORDER BY name")
    rows=[]
    for r in risk_tables:
        name=str(r.get('name') or '')
        try: n=(bg._rows(f'SELECT COUNT(*) AS n FROM "{name}"') or [{'n':'—'}])[0].get('n')
        except Exception: n='—'
        rows.append(f'<tr><td><b>{_esc(name)}</b></td><td>{_esc(n)}</td><td>Persistencia runtime</td></tr>')
    table=''.join(rows) or "<tr><td colspan='3'>No se encontraron tablas de riesgo; requiere reconciliación.</td></tr>"
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>{bg.THEME}{bg.TABLE_A11Y_CSS}{CLASSIC_CSS}</head><body>{bg.top_nav_html()}<main class='paper-page'>
    <h1>Riesgo</h1><div class='paper-grid'>
    <div class='paper-card'><h3>Modo</h3><b class='metric'>{_esc(observer.get('mode'))}</b></div>
    <div class='paper-card'><h3>Órdenes reales</h3><b class='metric'>{_esc(observer.get('real_orders_sent'))}</b><div class='paper-muted'>Debe ser 0</div></div>
    <div class='paper-card'><h3>PPI</h3><b class='metric'>{_esc(observer.get('ppi_auth'))}</b></div>
    <div class='paper-card'><h3>Event Risk</h3><b class='metric'>SHADOW</b><div class='paper-muted'>{len(event_contract.EVENT_TYPES)} tipos contractuales</div></div>
    </div>
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
          "<section><h2>Evaluadores por familia</h2><div class='paper-grid'>"+cards+"</div></section>"+
          "<section><h2>Scalping</h2><p>La estrategia de scalping se consulta exclusivamente en <a href='/scalping'>Scalping</a>.</p></section>")
    return bg._document('Trading — Estrategias y evaluadores',body,refresh=30)


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
        return _strategy_overview() if str(section or '').strip().lower()=='estrategias' else old_trading(section)
    bg.learning_page=learning_page_live; bg.validation_page=validation_page_live; bg.trading_page=trading_page_live

    @app.get('/riesgo',response_class=HTMLResponse)
    def riesgo(request:Request,token:str=Query(default=''),authorization:str|None=Header(default=None)):
        bg._authorize(check_auth,request,token,authorization)
        return HTMLResponse(_normalize_live_html(_risk_html()))
