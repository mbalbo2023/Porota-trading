"""Finalize exact release evidence on audit branch only; never accesses runtime."""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import time
import urllib.request
from datetime import datetime, timezone

CANDIDATE = 'b001ef1caf2f414a5022d2781016fa34f1611260'
PRODUCT = 'e9cf3fdbd9e6a71a0aa72365ff3b2727378db10f'
TREE = 'f3440892a5b3c14b5013f696710016209dc5a689'
DEPLOY_RUN = 37078173314
AUDIT_RUN = 37078669217
PINS = {DEPLOY_RUN: PRODUCT, AUDIT_RUN: '398b10eedbd7569fe4477782b2ee63e2153ce1f4'}
API = 'https://api.github.com/repos/mbalbo2023/Porota-trading'


def get(path: str) -> dict:
    request = urllib.request.Request(API + path, headers={
        'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
        'Accept': 'application/vnd.github+json',
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def wait() -> None:
    Path('evidence').mkdir(exist_ok=True)
    for attempt in range(70):
        result = {}
        for run, sha in PINS.items():
            data = get('/actions/runs/' + str(run))
            assert data['head_sha'] == sha, ('RUN_IDENTITY_DRIFT', run)
            result[str(run)] = {k: data[k] for k in ('id', 'head_sha', 'status', 'conclusion', 'run_attempt', 'html_url')}
            if data['status'] == 'completed':
                assert data['conclusion'] == 'success', ('DEPENDENCY_NOT_GREEN', run, data['conclusion'])
        print('FINALIZATION_BARRIER=' + json.dumps(result, sort_keys=True), flush=True)
        if all(r['status'] == 'completed' and r['conclusion'] == 'success' for r in result.values()):
            assert get('/git/ref/heads/deploy/rc6-pr69-isolated-20260915')['object']['sha'] == PRODUCT, 'PRODUCT_DRIFT'
            Path('evidence/dependencies.json').write_text(json.dumps(result, indent=2) + '\n')
            return
        time.sleep(60)
    raise SystemExit('BOUNDED_FINALIZATION_WAIT_EXHAUSTED_NO_WRITES')


def validate_snapshot(value: dict) -> None:
    state = value['current_state']
    assert state['candidate_sha'] == CANDIDATE and state['deploy_sha'] == PRODUCT
    assert state['validation_status'] == 'VALIDATED_RUNTIME'
    assert state['mode'] == 'PRODUCTION_PAPER' and state['real_orders_sent'] == 0
    assert state['real_order_routes'] == 'NOT_CALLED' and state['ppi_watch_untouched'] is True
    containers = value['containers']
    assert len(containers) == 3
    assert {c['name'] for c in containers} == {'porota_production_dashboard', 'porota_production_observer', 'porota_critical_approval_rc6'}
    assert all(c['status'] == 'running' and c['restarts'] == 0 and not c['oom'] and c['image'] == state['image_id'] for c in containers)
    assert value['ppi_watch_units'] == [] and value['dashboard_health_http'] == 200
    assert -5 <= value['heartbeat_age_seconds'] <= 300
    assert len(value['observer']) == 1 and value['observer'][0]['mode'] == 'PRODUCTION_PAPER'
    assert value['observer'][0]['real_orders_sent'] == 0
    assert value['ready_candidates'] == sum(r['n'] for r in value['candidates'] if r['status'] == 'READY' and r['can_simulate'] == 1)
    assert value['ready_candidates'] >= 5558


def selftest() -> None:
    state = {'candidate_sha': CANDIDATE, 'deploy_sha': PRODUCT, 'validation_status': 'VALIDATED_RUNTIME', 'mode': 'PRODUCTION_PAPER', 'real_orders_sent': 0, 'real_order_routes': 'NOT_CALLED', 'ppi_watch_untouched': True, 'image_id': 'SYNTHETIC_ONLY'}
    baseline = {'current_state': state, 'containers': [{'name': name, 'status': 'running', 'restarts': 0, 'oom': False, 'image': 'SYNTHETIC_ONLY'} for name in ('porota_production_dashboard', 'porota_production_observer', 'porota_critical_approval_rc6')], 'ppi_watch_units': [], 'dashboard_health_http': 200, 'heartbeat_age_seconds': 1, 'observer': [{'mode': 'PRODUCTION_PAPER', 'real_orders_sent': 0}], 'ready_candidates': 5558, 'candidates': [{'status': 'READY', 'can_simulate': 1, 'n': 5558}]}
    validate_snapshot(baseline)
    fixtures = {
        'wrong_candidate': lambda x: x['current_state'].update(candidate_sha='0' * 40),
        'pending_validation': lambda x: x['current_state'].update(validation_status='DEPLOYED_VALIDATION_PENDING'),
        'nonzero_orders': lambda x: x['current_state'].update(real_orders_sent=1),
        'old_image_pin': lambda x: x['containers'][2].update(image='OLD_SYNTHETIC'),
        'stale_heartbeat': lambda x: x.update(heartbeat_age_seconds=301),
        'false_ready_count': lambda x: x.update(ready_candidates=6000),
        'ppi_watch_change': lambda x: x.update(ppi_watch_units=['synthetic-ppi-watch.service']),
    }
    for name, mutate in fixtures.items():
        value = copy.deepcopy(baseline)
        mutate(value)
        try:
            validate_snapshot(value)
        except AssertionError:
            print('CLOSURE_NEGATIVE_FIXTURE=' + name + '|FAIL_CLOSED')
        else:
            raise AssertionError(name + '_ACCEPTED_BAD_EVIDENCE')
    print('CLOSURE_EVIDENCE_SELFTEST=GREEN')


def finalize() -> None:
    root = Path('/tmp/rc6-independent-evidence')
    proof = json.loads((root / 'frozen-package-proof.json').read_text())
    tests = json.loads((root / 'porota-governed-tests.json').read_text())
    assert proof['candidate_sha'] == CANDIDATE and proof['tree'] == TREE
    assert tests['status'] == 'GREEN' and tests['failures'] == tests['errors'] == 0
    snapshots = []
    for index in (1, 2):
        text = (root / f'runtime-snapshot-{index}.txt').read_text()
        assert 'READ_ONLY_AUDIT_FINISHED=true;RUNTIME_MUTATIONS=NONE;REAL_ORDER_CALLS=NONE' in text
        assert 'DASHBOARD_HEALTH_HTTP=200' in text and text.count('EXACT_SOURCE_GATES=GREEN') == 2
        def one(prefix: str) -> str:
            values = [line[len(prefix):] for line in text.splitlines() if line.startswith(prefix)]
            assert len(values) == 1, (index, prefix)
            return values[0]
        value = {'snapshot': index, 'current_state': json.loads(one('CURRENT_STATE=')), 'containers': [json.loads(line.split('=', 1)[1]) for line in text.splitlines() if line.startswith('CONTAINER=')], 'ready_candidates': int(one('READY_CANDIDATES=')), 'candidates': json.loads(one('CANDIDATES=')), 'observer': json.loads(one('OBSERVER=')), 'heartbeat_age_seconds': float(one('OBSERVER_HEARTBEAT_AGE_SECONDS=')), 'ppi_watch_units': json.loads(one('PPI_WATCH_UNITS=')), 'dashboard_health_http': 200}
        validate_snapshot(value)
        snapshots.append(value)
    assert snapshots[0]['current_state']['image_id'] == snapshots[1]['current_state']['image_id']
    pending = [
        'Caución: book PPI fresco y decisión/colocación del cash-sweep PAPER en ventana válida siguen NO_VERIFICADO. Las pruebas sintéticas no son evidencia de rueda.',
        'Scalping: capacidad de confirmación y operación simulada durante rueda válida siguen NO_VERIFICADO después de este deploy con mercado cerrado.',
        'Readiness es por identidad y fail-closed; filas no READY no se promueven ni borran automáticamente.',
        'La clasificación de units legacy fallidas y el backlog más amplio no se cierran silenciosamente por este release.',
        'Protecciones administrativas de rama: NO_VERIFICADAS por el conector; no se eludieron ni modificaron.',
    ]
    data = {'workstream': 'WS-INTEG-RC6-20261002', 'recorded_at': datetime.now(timezone.utc).isoformat(), 'technical_release_status': 'VALIDADO_RUNTIME', 'candidate_sha': CANDIDATE, 'candidate_tree': TREE, 'product_sha': PRODUCT, 'pr': 445, 'source_prs_absorbed_and_closed': [435, 436, 437, 438, 439, 440, 442, 443, 444], 'source_branches_preserved': True, 'predeploy_run': 37077028639, 'predeploy_artifact_id': 11256399584, 'artifact_zip_sha256': '0abd35ce25816692dc26f6c4678d9ea846c8b6c747103d46cc896632ea5f9142', 'deploy_run': DEPLOY_RUN, 'independent_audit_run': AUDIT_RUN, 'frozen_package_proof': proof, 'tests': tests, 'snapshots': snapshots, 'pending': pending, 'ownership': 'PENDING_EXPLICIT_RELEASE_IN_ISSUE_441'}
    directory = Path('docs/releases')
    output = directory / 'RC6_UNIFIED_FINAL_STATE_2026-10-02.json'
    output.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
    lines = [
        '# RC6 — cierre técnico validado del candidato unificado', '',
        '**VALIDADO_RUNTIME técnico.** No equivale a todas las familias READY ni a actividad intradiaria demostrada.', '',
        f'- PR #445; product SHA `{PRODUCT}`.', f'- Candidato `{CANDIDATE}`; tree `{TREE}`.',
        '- Predeploy V2 `37077028639`; artifact `11256399584`.',
        f'- Deploy V2 único `{DEPLOY_RUN}` SUCCESS; auditoría independiente `{AUDIT_RUN}` SUCCESS.',
        '- Dos snapshots independientes separados por 120 segundos.',
        f"- Suite gobernada: discovered={tests['discovered']}; executed={tests['executed']}; failures={tests['failures']}; errors={tests['errors']}; skipped={tests['skipped']}; xfail={tests['xfail']}.",
        f"- Candidatos READY: {snapshots[0]['ready_candidates']} / {snapshots[1]['ready_candidates']}.",
        f"- Tres contenedores running con la imagen cargada `{snapshots[1]['current_state']['image_id']}`, sin OOM ni reinicios automáticos en ambas lecturas.",
        '- PRODUCTION_PAPER; real_orders_sent=0; rutas reales NOT_CALLED; PPI Watch intacto; dashboard HTTP 200.',
        '- Nueve PR fuente cerrados como absorbidos; ramas/evidencia preservadas.',
        '', '## Pendientes explícitos', '', *[item + '\n' for item in pending],
        '## Evidencia y aprendizaje', '',
        'Contadores completos y procedencia: `RC6_UNIFIED_FINAL_STATE_2026-10-02.json`. Los errores/correcciones de los auditores auxiliares y de integración se conservan en el checkpoint histórico. No se ejecutó un segundo deploy, rollback ni rebuild en el droplet.', '',
        'El auditor inicial con esquema incorrecto quedó superado. El cierre usa el contrato canónico y el auditor corregido. El primer finalizador falló al parsear YAML por texto Python multilínea mal indentado: se separó en script Python compilado, con siete fixtures negativos antes de esperar o escribir. Ninguno de esos errores auxiliares se presenta como fallo del servidor.', '',
        'Ownership: pendiente liberación explícita en issue #441 tras revisar el cierre.', '',
    ]
    summary = '\n'.join(lines)
    (directory / 'RC6_UNIFIED_CLOSURE_2026-10-02.md').write_text(summary)
    checkpoint = directory / 'RC6_UNIFIED_EXECUTION_2026-10-02.md'
    notice = '> **ACTUALIZACIÓN FINAL:** cierre técnico VALIDADO_RUNTIME. Consultar `RC6_UNIFIED_CLOSURE_2026-10-02.md` y `RC6_UNIFIED_FINAL_STATE_2026-10-02.json`. Lo siguiente conserva el checkpoint histórico previo.\n\n'
    old = checkpoint.read_text()
    assert not old.startswith(notice)
    checkpoint.write_text(notice + old)
    Path('evidence/final-state.json').write_bytes(output.read_bytes())
    Path('evidence/closure.md').write_text(summary)
    print('TECHNICAL_RUNTIME_CLOSURE=GREEN;MARKET_WINDOW_VALIDATION=NO_VERIFICADO')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('selftest', 'wait', 'finalize'))
    arguments = parser.parse_args()
    {'selftest': selftest, 'wait': wait, 'finalize': finalize}[arguments.phase]()
