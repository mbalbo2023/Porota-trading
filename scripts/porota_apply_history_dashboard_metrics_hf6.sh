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
marker='HF6_V2_HISTORY_DASHBOARD_PATCH'
if marker in text:
    print('HISTORY_DASHBOARD_PATCH=ALREADY_APPLIED')
    raise SystemExit(0)

anchor='from _version import VERSION\n'
if anchor in text:
    replacement=(anchor+"from dd_history_metrics_hf6 import observer_history_metrics, v2_store_metrics\n"
                 "# HF6_V2_HISTORY_DASHBOARD_PATCH\n")
elif 'from db_dashboard_logs_hf6 import discover_sources, primary_source, source_by_id, tail_lines\n' in text:
    anchor='from db_dashboard_logs_hf6 import discover_sources, primary_source, source_by_id, tail_lines\n'
    replacement=(anchor+"from dd_history_metrics_hf6 import observer_history_metrics, v2_store_metrics\n"
                 "# HF6_V2_HISTORY_DASHBOARD_PATCH\n")
else:
    raise SystemExit('PATCH_ABORT_HISTORY_IMPORT_ANCHOR')
if text.count(anchor)!=1:
    raise SystemExit('PATCH_ABORT_HISTORY_IMPORT_ANCHOR_COUNT')
text=text.replace(anchor,replacement,1)

old='''    history_count=int(history.get('instruments') or 0); history_target=243
    history_pct=min(100.0,history_count/max(1,history_target)*100)
    cards="".join((_card("Catálogo PPI",catalog,"Inventario preapertura y poscierre; no cambia cada minuto","green" if catalog else "gray"),_card("Universo elegible",eligible,"Todos se evalúan por rotación; no todos tienen contrato ejecutable","green" if eligible else "gray"),_card("Cobertura histórica",f"{history_count}/{history_target}",f"{history_pct:.1f}% · {history.get('rows',0) or 0} filas; amarillo hasta completar cobertura","green" if history_count>=history_target else "yellow"),_card("Escaneo por ciclo",PAPER_ACTIVE_SYMBOL_LIMIT,"Ventana rotativa sobre todo el universo","green"),_card("Última fecha de mercado",_e(last_market),"Día bursátil contenido; no es hora de descarga","gray"),_card("Última corrida de ingesta",_local_time(last_attempt),"Cada 2 h fuera de rueda; no compite con current/book","gray"),_card("Última ingesta exitosa",_local_time(last_success),"Puede ser parcial mientras 65/243 no esté completo","green" if last_success else "yellow")))
'''
new='''    try:
        with closing(_conn()) as c:
            dynamic=observer_history_metrics(c)
    except Exception:
        dynamic={"target_total":0,"target_by_family":{},"source_capabilities":{}}
    store_v2=v2_store_metrics()
    history_target=int(dynamic.get('target_total') or 0)
    legacy_history_count=int(history.get('instruments') or 0)
    history_count=int(store_v2.get('identities') or 0) if store_v2.get('available') else legacy_history_count
    history_rows=int(store_v2.get('canonical_rows') or 0) if store_v2.get('available') else int(history.get('rows',0) or 0)
    history_pct=min(100.0,history_count/max(1,history_target)*100) if history_target else 0.0
    target_label=f"{history_count}/{history_target}" if history_target else f"{history_count}/objetivo dinámico pendiente"
    coverage_detail=(f"{history_pct:.1f}% · {history_rows} filas canónicas · objetivo derivado del universo AVAILABLE"
                     if history_target else f"{history_rows} filas; esperando universo dinámico")
    cards="".join((
        _card("Catálogo PPI",catalog,"Inventario PPI observado; no equivale a READY PAPER","green" if catalog else "gray"),
        _card("Universo elegible",eligible,"Elegible para motor PAPER; distinto del universo histórico","green" if eligible else "gray"),
        _card("Cobertura histórica",target_label,coverage_detail,
              "green" if history_target and history_count>=history_target else "yellow"),
        _card("History Store v2",f"{store_v2.get('canonical_rows',0)} filas",f"{store_v2.get('identities',0)} identidades financieras completas",
              "green" if store_v2.get('available') else "yellow"),
        _card("Escaneo por ciclo",PAPER_ACTIVE_SYMBOL_LIMIT,"Ventana rotativa del motor; no limita la cola histórica","green"),
        _card("Última fecha PPI legacy",_e(last_market),"Dato de production_history; History Store v2 puede contener otras fuentes","gray"),
        _card("Última corrida PPI",_local_time(last_attempt),"La cadencia real es por fuente; ver Sistema → Scheduler","gray"),
        _card("Última ingesta PPI exitosa",_local_time(last_success),"Una fuente puede quedar parcial sin bloquear otras fuentes/familias","green" if last_success else "yellow")))
'''
if text.count(old)!=1:
    raise SystemExit('PATCH_ABORT_HISTORY_CARD_ANCHOR')
text=text.replace(old,new,1)

old_body='''    body=f"<h1>Históricos y universo</h1><div class='paper-grid'>{cards}</div><div class='paper-notice'><b>Fecha del dato y fecha de ingesta son conceptos distintos.</b> Con la rueda cerrada es correcto que el último dato bursátil corresponda al cierre anterior; la última corrida indica si el proceso de background continúa activo. PPI históricos no queda limitado al lote visible de un ciclo.</div><div class='paper-card'><h2>Estado de la ingesta</h2><table class='paper-table'><tr><th>Fuente</th><th>Estado</th><th>Último intento</th><th>Último éxito</th><th>Ítems</th><th>Detalle</th></tr>{sync_rows}</table></div><div class='paper-card'><h2>Base objetiva para ampliar el lote por ciclo</h2><table class='paper-table'><tr><th>Ciclo</th><th>Seleccionados/elegibles</th><th>Correctos</th><th>Fallidos</th><th>Duración</th><th>Límite recomendado</th></tr>{cycle_rows}</table></div>"
'''
new_body='''    family_history_rows=[]
    by_family=store_v2.get('by_family',{}) if isinstance(store_v2,dict) else {}
    targets=dynamic.get('target_by_family',{}) if isinstance(dynamic,dict) else {}
    caps=dynamic.get('source_capabilities',{}) if isinstance(dynamic,dict) else {}
    all_families=sorted(set(targets)|set(by_family)|set(caps))
    for family in all_families:
        current=by_family.get(family,{})
        target=int(targets.get(family) or 0)
        symbols=int(current.get('symbols') or 0)
        family_history_rows.append(
            f"<tr><td><b>{_e(family)}</b></td><td>{symbols}/{target if target else '—'}</td>"
            f"<td>{_e(current.get('rows',0))}</td><td>{_e(current.get('first_date'))}</td>"
            f"<td>{_e(current.get('last_date'))}</td><td>{_e(', '.join(caps.get(family,())) or 'PROBE_REQUIRED')}</td></tr>")
    family_history=''.join(family_history_rows) or "<tr><td colspan='6'>Esperando métricas multi-familia.</td></tr>"
    body=f"<h1>Históricos y universo</h1><div class='paper-grid'>{cards}</div><div class='paper-notice'><b>Fecha del dato, fecha de ingesta y readiness PAPER son conceptos distintos.</b> Una familia HOLD puede acumular históricos si su identidad financiera está verificada. El denominador ya no es 243 fijo: surge del universo histórico disponible por familia. Para saber cuándo vuelve a ejecutarse cada trabajo, usar Sistema → Scheduler.</div><div class='paper-card'><h2>Cobertura History Store v2 por familia</h2><table class='paper-table'><tr><th>Familia</th><th>Identidades/objetivo</th><th>Filas</th><th>Desde</th><th>Hasta</th><th>Fuentes/capacidad</th></tr>{family_history}</table></div><div class='paper-card'><h2>Estado de ingesta PPI legacy</h2><table class='paper-table'><tr><th>Fuente</th><th>Estado</th><th>Último intento</th><th>Último éxito</th><th>Ítems</th><th>Detalle</th></tr>{sync_rows}</table></div><div class='paper-card'><h2>Base objetiva para ampliar el lote por ciclo</h2><table class='paper-table'><tr><th>Ciclo</th><th>Seleccionados/elegibles</th><th>Correctos</th><th>Fallidos</th><th>Duración</th><th>Límite recomendado</th></tr>{cycle_rows}</table></div>"
'''
if text.count(old_body)!=1:
    raise SystemExit('PATCH_ABORT_HISTORY_BODY_ANCHOR')
text=text.replace(old_body,new_body,1)

backup=path.with_suffix(path.suffix+'.pre-hf6v2-history-dashboard.bak')
if not backup.exists():
    backup.write_text(path.read_text(encoding='utf-8'),encoding='utf-8')
fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=str(path.parent)); os.close(fd)
Path(tmp).write_text(text,encoding='utf-8')
os.replace(tmp,path)
print('HISTORY_DASHBOARD_PATCH=APPLIED')
print('HISTORY_DASHBOARD_BACKUP='+str(backup))
PY

python3 -m py_compile "$TARGET" "$ROOT/dd_history_metrics_hf6.py"
echo "HISTORY_DASHBOARD_PY_COMPILE=OK"
true
