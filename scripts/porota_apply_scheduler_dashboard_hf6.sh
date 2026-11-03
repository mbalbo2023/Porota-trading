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
text=path.read_text(encoding='utf-8')
marker='HF6_V2_SCHEDULER_DASHBOARD_PATCH'
if marker in text:
    print('SCHEDULER_DASHBOARD_PATCH=ALREADY_APPLIED')
    raise SystemExit(0)

# This patch deliberately depends on the UX/logs patch so deployment order is explicit.
anchor=("from db_dashboard_logs_hf6 import discover_sources, primary_source, source_by_id, tail_lines\n")
replacement=(anchor+
    "from de_scheduler_catalog_hf6 import internal_rows, load_systemd_snapshot, describe_systemd_timer\n"
    "# HF6_V2_SCHEDULER_DASHBOARD_PATCH\n")
if text.count(anchor)!=1:
    raise SystemExit('PATCH_ABORT_REQUIRES_UX_LOGS_PATCH_FIRST')
text=text.replace(anchor,replacement,1)

old_sections='''SYSTEM_SECTIONS = (
    ("introspeccion", "Introspección"),
    ("salud", "Salud y SRE"),
    ("configuracion", "Configuración"),
    ("telegram", "Telegram"),
    ("logs", "Logs"),
)
'''
new_sections='''SYSTEM_SECTIONS = (
    ("introspeccion", "Introspección"),
    ("salud", "Salud y SRE"),
    ("scheduler", "Scheduler"),
    ("configuracion", "Configuración"),
    ("telegram", "Telegram"),
    ("logs", "Logs"),
)
'''
if text.count(old_sections)!=1:
    raise SystemExit('PATCH_ABORT_SYSTEM_SECTIONS_ANCHOR')
text=text.replace(old_sections,new_sections,1)

system_anchor='''def system_page(section="introspeccion"):
'''
if text.count(system_anchor)!=1:
    raise SystemExit('PATCH_ABORT_SYSTEM_PAGE_ANCHOR')

scheduler_code=r'''
def _scheduler_cadence(seconds):
    if seconds in (None,0,''):
        return 'Condicional / no inferida'
    try:
        value=int(seconds)
    except Exception:
        return '—'
    if value % 86400 == 0:
        return f"cada {value//86400} día(s)"
    if value % 3600 == 0:
        return f"cada {value//3600} h"
    if value % 60 == 0:
        return f"cada {value//60} min"
    return f"cada {value} s"


def _scheduler_time(value):
    if not value:
        return '—'
    if value == 'DUE_NOW':
        return 'VENCIDO / al próximo tick'
    rendered=_local_time(value)
    return rendered if rendered != _e(value) else _e(value)


def scheduler_content():
    db_jobs=_rows('SELECT * FROM operational_jobs ORDER BY job_key') if _table('operational_jobs') else []
    internal=internal_rows(db_jobs)
    systemd=load_systemd_snapshot()
    timers=systemd.get('timers',[]) if isinstance(systemd,dict) else []

    internal_html=[]
    for row in internal:
        condition=row.get('condition') or 'Sin condición adicional'
        internal_html.append(
            f"<tr><td><b>{_e(row.get('label'))}</b><br><code>{_e(row.get('key'))}</code></td>"
            f"<td>{_e(row.get('description'))}<br><span class='paper-muted'>Condición: {_e(condition)}</span></td>"
            f"<td>{_status(row.get('state'))}<br><span class='paper-muted'>{_e(row.get('detail'))}</span></td>"
            f"<td>{_scheduler_cadence(row.get('cadence_seconds'))}</td>"
            f"<td>{_scheduler_time(row.get('last_run_at'))}</td>"
            f"<td>{_scheduler_time(row.get('last_success_at'))}</td>"
            f"<td>{_scheduler_time(row.get('next_run_at'))}</td></tr>")

    timer_html=[]
    legacy_warning=''
    for timer in timers:
        unit=str(timer.get('unit') or '')
        enabled=str(timer.get('enabled_state') or 'unknown')
        active=str(timer.get('active_state') or 'unknown')
        result=str(timer.get('last_result') or 'unknown')
        if unit=='porota-preopen.timer' and enabled not in {'disabled','masked'}:
            legacy_warning=("<div class='paper-warning'><b>Pre-open legacy todavía visible en systemd.</b> "
                            "HF6 v2 propone retirarlo de forma reversible y reemplazarlo por readiness continuo.</div>")
        timer_state='VERDE' if active=='active' and enabled not in {'disabled','masked'} else 'AMARILLO'
        if result not in {'success','unknown',''}:
            timer_state='ROJO'
        timer_html.append(
            f"<tr><td><b>{_e(unit)}</b></td><td>{_e(describe_systemd_timer(unit))}</td>"
            f"<td>{_status(timer_state)}<br><span class='paper-muted'>active={_e(active)} · enabled={_e(enabled)}</span></td>"
            f"<td>{_e(timer.get('last_trigger') or timer.get('service_inactive_exit') or '—')}</td>"
            f"<td>{_e(result)} / status {_e(timer.get('exec_main_status') or '—')}</td>"
            f"<td>{_e(timer.get('next_elapse') or '—')}</td></tr>")

    error_internal=sum(1 for r in internal if str(r.get('state') or '').upper() in {'ROJO','ERROR','FAILED'})
    error_timers=sum(1 for r in timers if str(r.get('last_result') or '').lower() not in {'success','unknown',''})
    cards=''.join((
        _card('Jobs internos',len(internal),f'{error_internal} con error persistido','red' if error_internal else 'green'),
        _card('Timers systemd',len(timers),f"snapshot {_e(systemd.get('state','UNKNOWN'))}",'red' if systemd.get('state')=='ERROR' else 'green' if timers else 'yellow'),
        _card('Snapshot scheduler',_local_time(systemd.get('recorded_at')), 'Actualizado por timer host read-only','green' if timers else 'yellow'),
    ))
    return ("<h1>Scheduler</h1>"
            "<p class='paper-muted'>Inventario consolidado de trabajos planificados. La pantalla es sólo lectura y no puede iniciar/detener jobs.</p>"
            f"{legacy_warning}<div class='paper-grid'>{cards}</div>"
            "<div class='paper-card'><h2>Jobs internos del observer</h2>"
            "<p>El próximo horario calculado por TTL es una estimación; si existe una condición de sesión/mercado también debe cumplirse.</p>"
            "<table class='paper-table'><tr><th>Trabajo</th><th>Qué hace</th><th>Último resultado</th><th>Periodicidad</th>"
            "<th>Última ejecución</th><th>Último éxito</th><th>Próxima ejecución estimada</th></tr>"+
            (''.join(internal_html) or "<tr><td colspan='7'>Sin jobs persistidos ni catalogados.</td></tr>")+"</table></div>"
            "<div class='paper-card'><h2>Timers systemd del host</h2>"
            "<p>Las fechas LAST/NEXT provienen de systemd y son las autoritativas para estos timers.</p>"
            "<table class='paper-table'><tr><th>Timer</th><th>Qué hace</th><th>Estado</th><th>Última ejecución</th>"
            "<th>Último resultado</th><th>Próxima ejecución</th></tr>"+
            (''.join(timer_html) or "<tr><td colspan='6'>Esperando primer snapshot de systemd.</td></tr>")+"</table></div>")


'''
text=text.replace(system_anchor,scheduler_code+system_anchor,1)

old_branch='''    elif section == "salud":
        content = _main_fragment(health_page()) + _main_fragment(sre_page())
    elif section == "configuracion":
'''
new_branch='''    elif section == "salud":
        content = _main_fragment(health_page()) + _main_fragment(sre_page())
    elif section == "scheduler":
        content = scheduler_content()
    elif section == "configuracion":
'''
if text.count(old_branch)!=1:
    raise SystemExit('PATCH_ABORT_SYSTEM_BRANCH_ANCHOR')
text=text.replace(old_branch,new_branch,1)

old_refresh='''    return _document("Sistema", body, refresh=30 if section=="introspeccion" else 60)
'''
new_refresh='''    return _document("Sistema", body, refresh=30 if section in {"introspeccion","scheduler"} else 60)
'''
if text.count(old_refresh)!=1:
    raise SystemExit('PATCH_ABORT_SYSTEM_REFRESH_ANCHOR')
text=text.replace(old_refresh,new_refresh,1)

backup=path.with_suffix(path.suffix+'.pre-hf6v2-scheduler.bak')
if not backup.exists():
    backup.write_text(path.read_text(encoding='utf-8'),encoding='utf-8')
fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=str(path.parent)); os.close(fd)
Path(tmp).write_text(text,encoding='utf-8')
os.replace(tmp,path)
print('SCHEDULER_DASHBOARD_PATCH=APPLIED')
print('SCHEDULER_DASHBOARD_BACKUP='+str(backup))
PY

python3 -m py_compile "$TARGET" "$ROOT/de_scheduler_catalog_hf6.py"
echo "SCHEDULER_DASHBOARD_PY_COMPILE=OK"
true
