#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT="${1:-/opt/porota-trading}"
TARGET="$ROOT/bg_paper_dashboard.py"

python3 - "$TARGET" <<'PY'
from __future__ import annotations
from pathlib import Path
import os,sys,tempfile

path=Path(sys.argv[1])
text=path.read_text(encoding="utf-8")

marker="HF6_V2_DASHBOARD_UX_LOGS_PATCH"
if marker in text:
    print("DASHBOARD_UX_LOGS_PATCH=ALREADY_APPLIED")
    raise SystemExit(0)

import_anchor="from _version import VERSION\n"
imports=(
    "from _version import VERSION\n"
    "from da_dashboard_ux_hf6 import (TOP_NAV, TRADING_NAV, FAMILY_GROUPS, FAMILY_LABELS, "
    "top_nav_html, trading_nav_html, families_for_group)\n"
    "from db_dashboard_logs_hf6 import discover_sources, primary_source, source_by_id, tail_lines\n"
    "\n# HF6_V2_DASHBOARD_UX_LOGS_PATCH\n"
)
if text.count(import_anchor)!=1:
    raise SystemExit("PATCH_ABORT_IMPORT_ANCHOR")
text=text.replace(import_anchor,imports,1)

old_nav='''def _nav():
    links = (("/", "Panel"), ("/en-vivo", "En vivo"), ("/motor-trading", "Motor de trading"),
             ("/scalping", "Scalping"),
             ("/historicos", "Históricos"), ("/aprendizaje", "Aprendizaje"),
             ("/informacion-financiera", "Información financiera"), ("/reportes", "Reportes"),
             ("/sistema", "Sistema"))
    return "<nav id='porota-canonical-nav'>" + "".join(f"<a href='{href}'>{label}</a>" for href, label in links) + "</nav>"
'''
new_nav='''def _nav():
    return top_nav_html()
'''
if text.count(old_nav)!=1:
    raise SystemExit("PATCH_ABORT_NAV_ANCHOR")
text=text.replace(old_nav,new_nav,1)

insert_anchor="\n\nSYSTEM_SECTIONS = (\n"
if text.count(insert_anchor)!=1:
    raise SystemExit("PATCH_ABORT_SYSTEM_ANCHOR")

new_pages=r'''

def _family_ux_snapshot(families):
    families=tuple(str(x).upper() for x in families)
    if not families:
        return []
    coverage={r.get('instrument_type'):r for r in (_rows(
        'SELECT * FROM catalog_family_coverage ORDER BY instrument_type')
        if _table('catalog_family_coverage') else [])}
    evidence_counts={}
    changed_counts={}
    if _table('contract_evidence_v2_current'):
        for r in _rows('SELECT family,COUNT(*) n,MAX(observed_at) observed_at FROM contract_evidence_v2_current GROUP BY family'):
            evidence_counts[str(r.get('family') or '').upper()]=r
    if _table('contract_evidence_v2_changes'):
        for r in _rows("SELECT family,COUNT(*) n FROM contract_evidence_v2_changes WHERE status='CHANGED_REVIEW_REQUIRED' GROUP BY family"):
            changed_counts[str(r.get('family') or '').upper()]=int(r.get('n') or 0)
    result=[]
    for family in families:
        row=coverage.get(family,{})
        explicit_ready=int(row.get('ready_paper_count') or 0)
        observed=int(row.get('observed_count') or 0)
        evidence=evidence_counts.get(family,{})
        changes=changed_counts.get(family,0)
        state='READY_PAPER' if explicit_ready>0 else 'HOLD'
        result.append({
            'family':family,
            'label':FAMILY_LABELS.get(family,family),
            'state':state,
            'observed':observed,
            'ready':explicit_ready,
            'discovery':row.get('discovery_status','SIN_REGISTRO'),
            'evidence':int(evidence.get('n') or 0),
            'evidence_at':evidence.get('observed_at'),
            'changed':changes,
        })
    return result


def _family_ux_table(families):
    rows=[]
    for item in _family_ux_snapshot(families):
        detail=('READY_PAPER explícito en catálogo/runtime' if item['ready'] else
                'Visible para ingesta/evidencia; no habilitada para operar')
        rows.append(
            f"<tr><td><b>{_e(item['label'])}</b></td><td>{_status(item['state'])}</td>"
            f"<td>{_e(item['observed'])}</td><td>{_e(item['ready'])}</td>"
            f"<td>{_e(item['evidence'])}</td><td>{_e(item['changed'])}</td>"
            f"<td>{_e(item['discovery'])}</td><td>{_e(detail)}</td></tr>")
    return ''.join(rows) or "<tr><td colspan='8'>Sin evidencia para este grupo.</td></tr>"


def trading_page(section=''):
    section=str(section or '').strip().lower()
    subnav=trading_nav_html()
    if section=='estrategias':
        body=("<h1>Trading — Estrategias</h1>"+subnav+
              "<div class='paper-notice'><b>Scalping deja de ser una sección principal aislada.</b> "
              "Es una estrategia del motor PAPER y conserva su URL histórica sólo por compatibilidad.</div>"+
              _main_fragment(scalping_page()))
        return _document('Trading — Estrategias',body,refresh=30)
    families=families_for_group(section)
    if families:
        title=dict(TRADING_NAV).get('/trading/'+section,section) if False else section.replace('-',' ').title()
        table=_family_ux_table(families)
        body=(f"<h1>Trading — {_e(title)}</h1>{subnav}"
              "<div class='paper-notice'>Una familia visible puede seguir HOLD. Descubrimiento, histórico y evidencia contractual "
              "no equivalen a permiso PAPER.</div>"
              "<div class='paper-card'><table class='paper-table'><tr><th>Familia</th><th>Readiness</th>"
              "<th>Observadas</th><th>READY PAPER</th><th>Evidencias contractuales</th><th>Cambios a revisar</th>"
              f"<th>Descubrimiento</th><th>Interpretación</th></tr>{table}</table></div>")
        return _document('Trading — '+title,body,refresh=30)

    cards=[]
    for group,families in FAMILY_GROUPS.items():
        snap=_family_ux_snapshot(families)
        ready=sum(x['ready'] for x in snap)
        observed=sum(x['observed'] for x in snap)
        evidence=sum(x['evidence'] for x in snap)
        cards.append(
            f"<a class='paper-card' style='text-decoration:none;color:inherit' href='/trading/{_e(group)}'>"
            f"<h2>{_e(group.replace('-',' ').title())}</h2><b class='metric'>{ready} READY PAPER</b>"
            f"<p class='paper-muted'>{observed} observadas · {evidence} evidencias contractuales</p></a>")
    body=("<h1>Trading</h1>"+subnav+
          "<div class='paper-notice'>PPI es la fuente live primaria. A3/CEM/Data912 no agregan latencia al camino de decisión. "
          "Los datos background enriquecen contratos/históricos y nunca habilitan una familia por sí solos.</div>"
          "<div class='paper-grid'>"+''.join(cards)+"</div>"
          "<div class='paper-card'><h2>Motor general</h2><p>La URL histórica <code>/motor-trading</code> permanece disponible, "
          "pero el acceso canónico se organiza por familia y estrategia.</p>"
          "<a class='paper-action' href='/motor-trading'>Ver trazabilidad completa del motor</a></div>")
    return _document('Trading',body,refresh=30)


def instruments_page():
    families=[]
    for values in FAMILY_GROUPS.values():
        for family in values:
            if family not in families:
                families.append(family)
    for family in ('ETF','ACCIONES_USA'):
        if family not in families:
            families.append(family)
    table=_family_ux_table(families)
    body=("<h1>Instrumentos y contratos</h1>"
          "<div class='paper-notice'>Fuente de verdad visual de cobertura contractual. "
          "MISSING/STALE/HOLD se muestran; no se esconden familias por no estar operativas.</div>"
          "<div class='paper-card'><table class='paper-table'><tr><th>Familia</th><th>Readiness</th>"
          "<th>Observadas</th><th>READY PAPER</th><th>Evidencias</th><th>Cambios</th>"
          f"<th>Descubrimiento</th><th>Interpretación</th></tr>{table}</table></div>")
    return _document('Instrumentos y contratos',body,refresh=60)
'''
text=text.replace(insert_anchor,new_pages+insert_anchor,1)

old_logs=r'''def logs_page():
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
'''
new_logs=r'''def logs_page():
    sources=discover_sources()
    chosen=primary_source()
    cards=[]
    actions=[]
    for source in sources:
        size=f"{source.size_bytes/1024:.1f} KB"
        cards.append(_card(source.label,size,str(source.path),'green'))
        actions.append(f"<a class='paper-action' href='/api/logs/current?source={_e(source.source_id)}'>Descargar {_e(source.label)}</a>")
    if chosen:
        live="\n".join(tail_lines(chosen,50)) or "El archivo existe pero todavía no contiene líneas."
        source_text=f"Fuente mostrada: {chosen.label} · {chosen.path}"
    else:
        live="Sin líneas disponibles. El exportador host todavía no generó un snapshot."
        source_text="Sin snapshot compartido"
        cards.append(_card('Logs runtime','SIN SNAPSHOT','Esperando porota-log-export-hf6.timer','yellow'))
    body=("<h1>Gestión de logs</h1><div class='paper-grid'>"+''.join(cards)+"</div>"+
          ''.join(actions)+"<div class='paper-card'><h2>Últimas 50 líneas</h2>"
          f"<p class='paper-muted'>{_e(source_text)}</p>"
          f"<pre style='white-space:pre-wrap;overflow-wrap:anywhere'>{_e(live)}</pre></div>"
          "<div class='paper-notice'>Los snapshots son sanitizados y acotados en el host. "
          "El dashboard no recibe acceso al socket de Docker.</div>")
    return _document("Logs",body,refresh=30)
'''
if text.count(old_logs)!=1:
    raise SystemExit("PATCH_ABORT_LOGS_PAGE_ANCHOR")
text=text.replace(old_logs,new_logs,1)

route_anchor='''    @app.get("/en-vivo",response_class=HTMLResponse)
    def en_vivo(request:Request,token:str=Query(default=""),authorization:str|None=Header(default=None)): auth(request,token,authorization); return HTMLResponse(live_page())
'''
new_routes=route_anchor+'''    @app.get("/trading",response_class=HTMLResponse)
    def trading(request:Request,token:str=Query(default=""),authorization:str|None=Header(default=None)): auth(request,token,authorization); return HTMLResponse(trading_page())
    @app.get("/trading/{section}",response_class=HTMLResponse)
    def trading_section(section:str,request:Request,token:str=Query(default=""),authorization:str|None=Header(default=None)): auth(request,token,authorization); return HTMLResponse(trading_page(section))
    @app.get("/instrumentos",response_class=HTMLResponse)
    def instrumentos(request:Request,token:str=Query(default=""),authorization:str|None=Header(default=None)): auth(request,token,authorization); return HTMLResponse(instruments_page())
'''
if text.count(route_anchor)!=1:
    raise SystemExit("PATCH_ABORT_ROUTE_ANCHOR")
text=text.replace(route_anchor,new_routes,1)

old_download=r'''    @app.get("/api/logs/current")
    def log_download(request:Request,token:str=Query(default=""),authorization:str|None=Header(default=None)):
        auth(request,token,authorization)
        path=(Path(os.getenv("LOG_DIR","data/logs"))/"trading_bot.log").resolve()
        allowed=(Path(os.getenv("LOG_DIR","data/logs"))).resolve()
        if not path.is_relative_to(allowed) or not path.exists():
            raise HTTPException(404,"Log no disponible")
        return FileResponse(path,media_type="text/plain",filename="porota_trading_actual.log")
'''
new_download=r'''    @app.get("/api/logs/current")
    def log_download(request:Request,source:str=Query(default="observer"),token:str=Query(default=""),authorization:str|None=Header(default=None)):
        auth(request,token,authorization)
        selected=source_by_id(source) or (primary_source() if source=="observer" else None)
        if selected is None:
            raise HTTPException(404,"Log no disponible")
        return FileResponse(selected.path,media_type="text/plain",filename=f"porota_{selected.source_id}.log")
'''
if text.count(old_download)!=1:
    raise SystemExit("PATCH_ABORT_LOG_DOWNLOAD_ANCHOR")
text=text.replace(old_download,new_download,1)

# Write atomically. Backup is local staging evidence and is cleaned by deploy lifecycle.
backup=path.with_suffix(path.suffix+'.pre-hf6v2-ux.bak')
if not backup.exists():
    backup.write_text(path.read_text(encoding='utf-8'),encoding='utf-8')
fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=str(path.parent))
os.close(fd)
Path(tmp).write_text(text,encoding='utf-8')
os.replace(tmp,path)
print('DASHBOARD_UX_LOGS_PATCH=APPLIED')
print('DASHBOARD_BACKUP='+str(backup))
PY

python3 -m py_compile "$TARGET" "$ROOT/da_dashboard_ux_hf6.py" "$ROOT/db_dashboard_logs_hf6.py"
echo "DASHBOARD_PY_COMPILE=OK"
true
