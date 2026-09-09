"""RC6 Wave8 live-wiring closure.

Dashboard-only, read-only wiring. Adds the missing Risk destination,
keeps tabular data as real rows/columns, and wires SHADOW learning
evidence into live rendered pages. No broker/order capability.
"""
from __future__ import annotations
import html
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
.paper-card{max-width:100%;overflow-x:auto!important;overflow-y:visible!important;-webkit-overflow-scrolling:touch}
.paper-table,.classic-responsive-table{display:table!important;width:max-content!important;min-width:100%!important;max-width:none!important;table-layout:auto!important;border-collapse:collapse!important}
.paper-table thead,.classic-responsive-table thead{display:table-header-group!important}
.paper-table tbody,.classic-responsive-table tbody{display:table-row-group!important}
.paper-table tr,.classic-responsive-table tr{display:table-row!important}
.paper-table th,.paper-table td,.classic-responsive-table th,.classic-responsive-table td{display:table-cell!important;white-space:nowrap!important;overflow-wrap:normal!important;word-break:keep-all!important;min-width:max-content!important;max-width:none!important;vertical-align:middle!important}
.paper-table td[data-wrap='true'],.classic-responsive-table td[data-wrap='true']{white-space:normal!important;min-width:16rem!important;max-width:32rem!important;overflow-wrap:break-word!important;word-break:normal!important}
.porota-cell-label{display:none!important}
@media(max-width:900px){.paper-table,.classic-responsive-table{font-size:.82rem!important}.paper-table th,.paper-table td,.classic-responsive-table th,.classic-responsive-table td{padding:6px 8px!important}}
@media(max-width:620px){.paper-table,.classic-responsive-table{font-size:.78rem!important}.paper-table th,.paper-table td,.classic-responsive-table th,.classic-responsive-table td{padding:5px 7px!important}}
</style>
"""

def _esc(v): return html.escape(str(v if v not in (None,'') else '—'))

def _learning_section():
    data=shadow_learning.collect()
    rows=[]
    for key,item in sorted((data.get('policies') or {}).items()):
        m=item.get('metrics') or {}
        rows.append('<tr>'
          f'<td><b>{_esc(key)}</b></td><td>{_esc(item.get("stage"))}</td>'
          f'<td>{_esc(item.get("authority"))}</td><td>{_esc(item.get("sessions"))}</td>'
          f'<td>{_esc(m.get("evaluated"))}</td><td>{_esc(item.get("automatic_promotion"))}</td></tr>')
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

def _append_before_main_end(page, fragment):
    marker='</main>'
    return page.replace(marker,fragment+marker,1) if marker in page else page+fragment

def install(app, check_auth):
    global _installed
    if _installed: return
    _installed=True
    if 'porota-rc6-classic-responsive' not in bg.TABLE_A11Y_CSS:
        bg.TABLE_A11Y_CSS += CLASSIC_CSS
    old_learning=bg.learning_page
    old_validation=bg.validation_page
    def learning_page_live(): return _append_before_main_end(old_learning(),_learning_section())
    def validation_page_live(): return _append_before_main_end(old_validation(),_learning_section())
    bg.learning_page=learning_page_live
    bg.validation_page=validation_page_live
    @app.get('/riesgo',response_class=HTMLResponse)
    def riesgo(request:Request,token:str=Query(default=''),authorization:str|None=Header(default=None)):
        bg._authorize(check_auth,request,token,authorization)
        return HTMLResponse(_risk_html())
