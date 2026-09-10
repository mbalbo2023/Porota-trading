#!/usr/bin/env python3
"""Static RC4 module/reachability inventory. No mutations."""
from __future__ import annotations
import ast, re
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parent

EXPLICIT_ROLE_MAP={
    "aq_macro_backtest":"OPS_CLI_OFFLINE",
    "bj_sandbox_health_probe":"OPS_CLI_DIAGNOSTIC",
    "cy_a3_contract_bridge_hf6":"DEFERRED_INTEGRATION",
    "cn_ppi_authenticated_family_scraper_hf6":"OPS_CLI_MANUAL_LEGACY",
    "r_clear_kill_switch":"OPS_CLI_MANUAL",
    "fa_raw_evidence_store_rc6":"DEPLOYED_EVIDENCE_HELPER",
    "fm_critical_approval_unix_runtime_rc6":"HOST_RUNTIME_ACTIVE",
    "rc5_release_preflight":"RETIRED_LEGACY",
    "rc6_trusted_browser_contract_collector":"HOST_RUNTIME_ACTIVE",
}
EXPLICIT_ROLE_REASON={
    "aq_macro_backtest":"Backtest macro offline; se invoca a demanda.",
    "bj_sandbox_health_probe":"Probe Sandbox manual; no debe correr automáticamente en producción.",
    "cy_a3_contract_bridge_hf6":"A3 Primary privado no autenticado; bridge conservado sin inventar wiring.",
    "cn_ppi_authenticated_family_scraper_hf6":"Scraper HF6 legacy con login explícito. Se conserva sólo para trazabilidad/manual; los timers RC4 usan exclusivamente trusted-device GET-only.",
    "r_clear_kill_switch":"Emergency CLI humano; no debe ser llamado automáticamente.",
    "fa_raw_evidence_store_rc6":"Helper RC6 de archivo inmutable de evidencia; materializado y validado por el despliegue Wave 4, sin rutas de órdenes.",
    "fm_critical_approval_unix_runtime_rc6":"Entrypoint runtime RC6 invocado explícitamente por porota-critical-approval-rc6.service en el host.",
    "rc5_release_preflight":"Preflight exclusivo de RC5; sólo referenciado por workflows RC5 históricos y no pertenece al camino operativo RC6.",
    "rc6_trusted_browser_contract_collector":"Collector W12 RC6 invocado dinámicamente por porota-contract-evidence-rc6-runtime.sh; superficie GET/HEAD/OPTIONS y sin rutas operativas.",
}

def build(root=ROOT):
    root=Path(root); modules={p.stem:p for p in root.glob('*.py') if p.name!='__init__.py'}
    imported_by=defaultdict(set); imports=defaultdict(set)
    for mod,p in modules.items():
        try: tree=ast.parse(p.read_text(encoding='utf-8',errors='replace'))
        except Exception: continue
        for n in ast.walk(tree):
            name=None
            if isinstance(n,ast.Import):
                for a in n.names:
                    name=a.name.split('.')[0]
                    if name in modules: imports[mod].add(name); imported_by[name].add(mod)
            elif isinstance(n,ast.ImportFrom) and n.module:
                name=n.module.split('.')[0]
                if name in modules: imports[mod].add(name); imported_by[name].add(mod)
    text_refs=defaultdict(set)
    for p in list((root/'scripts').glob('*'))+list((root/'systemd').glob('*'))+list((root/'tests').glob('*.py')):
        if not p.is_file(): continue
        txt=p.read_text(encoding='utf-8',errors='replace')
        for mod in modules:
            if re.search(r'(?<![A-Za-z0-9_])'+re.escape(mod)+r'(?:\.py)?(?![A-Za-z0-9_])',txt): text_refs[mod].add(str(p.relative_to(root)))
    entry={'o_dashboard','bf_production_paper_observer','bg_paper_dashboard','entrypoint'}
    runtime=set(); stack=list(entry & modules.keys())
    while stack:
        m=stack.pop()
        if m in runtime: continue
        runtime.add(m); stack.extend(set(imports.get(m,()))-runtime)
    rows=[]
    for mod,p in sorted(modules.items()):
        refs=sorted(text_refs.get(mod,()))
        if mod in EXPLICIT_ROLE_MAP: status=EXPLICIT_ROLE_MAP[mod]
        elif mod in runtime: status='RUNTIME_ACTIVE'
        elif any(x.startswith('systemd/') for x in refs): status='SCHEDULER_ACTIVE'
        elif any(x.startswith('scripts/') for x in refs): status='OPS_CLI_OR_BUILD'
        elif refs and all(x.startswith('tests/') for x in refs): status='TEST_ONLY'
        elif imported_by.get(mod): status='LIBRARY_REACHABLE'
        elif mod.startswith(('rc4_','test_')): status='RC4_TOOL_OR_TEST'
        else: status='REVIEW_REQUIRED'
        rows.append({'module':mod,'status':status,'imported_by':sorted(imported_by.get(mod,())),'references':refs,'reason':EXPLICIT_ROLE_REASON.get(mod,'')})
    prefixes=defaultdict(list)
    for mod in modules:
        m=re.match(r'^([a-z]{1,3})(?:_|$)',mod)
        if m: prefixes[m.group(1)].append(mod)
    collisions={k:sorted(v) for k,v in prefixes.items() if len(v)>1}
    return rows,collisions

def markdown(rows,collisions):
    out=['# RC4 — Inventario de módulos y scripts','',
         '**Método:** AST imports + referencias literales en `systemd/`, `scripts/` y `tests/`. No elimina archivos.','',
         '## Resumen','']
    counts=defaultdict(int)
    for r in rows: counts[r['status']]+=1
    out += [f'- **{k}:** {v}' for k,v in sorted(counts.items())]
    out += ['',f'- **Colisiones de prefijo detectadas:** {len(collisions)}','',
            '## Decisión sobre aparentes huérfanos','',
            'La ausencia de import directo no implica que un módulo sea basura: puede ser CLI, scheduler, build tool, fixture o módulo cargado dinámicamente. RC4 clasifica explícitamente los CLI humanos/offline y el único bridge diferido; `REVIEW_REQUIRED` debe llegar a cero antes de TEST-READY.','',
            '## Tabla completa','', '| Módulo | Clasificación | Importado por | Referencias externas | Decisión / razón |','|---|---|---|---|---|']
    for r in rows:
        out.append(f"| `{r['module']}.py` | {r['status']} | {', '.join(r['imported_by']) or '—'} | {', '.join(r['references']) or '—'} | {r.get('reason') or '—'} |")
    out += ['','## Colisiones de prefijo','']
    for k,v in sorted(collisions.items()): out.append(f"- `{k}`: " + ', '.join(f'`{x}.py`' for x in v))
    out += ['','## Política RC4','',
            '- `REVIEW_REQUIRED` **no se elimina automáticamente**.',
            '- Toda eliminación futura requiere evidencia de no uso + tests + justificación en la auditoría.',
            '- Las colisiones de prefijo son deuda de nomenclatura, no motivo suficiente para renombrar en caliente y romper imports.']
    return '\n'.join(out)+'\n'

if __name__=='__main__':
    rows,col=build(); (ROOT/'RC4_MODULE_INVENTORY.md').write_text(markdown(rows,col),encoding='utf-8')
    print(f'modules={len(rows)} review_required={sum(r["status"]=="REVIEW_REQUIRED" for r in rows)} prefix_collisions={len(col)}')