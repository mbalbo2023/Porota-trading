from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from scripts.rc6_deploy_generation_gate import advances, collect, receipt

AT = datetime(2026, 10, 15, 14, 30, tzinfo=timezone.utc)
SHA = 'a' * 40
TREE = 'b' * 40
HEALTH = {'source_sha': SHA, 'candidate_tree_sha': TREE}


def cut():
    return {'pointer': {'generation_id': '1' * 64, 'sequence': 1, 'manifest_sha256': '2' * 64},
            'status': {'as_of': AT.isoformat(), 'mode': 'SHADOW', 'status': 'SHADOW_OBSERVING',
                'provider_requests': 0, 'real_orders_sent': 0, 'real_routes': 'NOT_CALLED',
                'configuration_fingerprint': '3' * 64,
                'generation_timing': {'poll_seconds': 30, 'tick_seconds': 1},
                'runtime_sql_diagnostics': {'budget_ms': 2000, 'sql_elapsed_ms': 100,
                    'transform_elapsed_ms': 200, 'stages': [
                        {'stage': 'CATALOG_FULL', 'deadline_elapsed': False, 'interrupted_by_deadline': False}]}}}


@pytest.mark.parametrize('field,value', [
    ('status', 'FAIL_CLOSED'), ('real_orders_sent', True), ('real_orders_sent', 1),
    ('provider_requests', 1), ('real_routes', 'CALLED'),
    ('as_of', (AT + timedelta(seconds=2)).isoformat()),
    ('as_of', (AT - timedelta(seconds=121)).isoformat()),
    ('generation_timing', None),
])
def test_receipt_rejects_stale_future_legacy_and_unsafe(field, value):
    value_cut = cut(); value_cut['status'][field] = value
    with pytest.raises(ValueError): receipt(value_cut, HEALTH, SHA, TREE, AT)


@pytest.mark.parametrize('field,value', [('sql_elapsed_ms', 2001), ('transform_elapsed_ms', 2001),
    ('sql_elapsed_ms', float('nan')), ('transform_expired_stage', 'INTRADAY_CONTRACTS'), ('stages', [])])
def test_native_budget_is_not_inferred_from_http_health(field, value):
    value_cut = cut(); value_cut['status']['runtime_sql_diagnostics'][field] = value
    with pytest.raises(ValueError): receipt(value_cut, HEALTH, SHA, TREE, AT)


def test_unknown_source_and_same_or_regressing_generation_do_not_pass():
    with pytest.raises(ValueError): receipt(cut(), {}, SHA, TREE, AT)
    first = receipt(cut(), HEALTH, SHA, TREE, AT)
    assert not advances(first, first)
    second = {**first, 'generation_id': '4' * 64, 'sequence': 2,
              'as_of': (AT + timedelta(seconds=30)).isoformat()}
    assert advances(first, second)
    for key, value in [('sequence', 1), ('as_of', first['as_of']),
                       ('configuration_fingerprint', '5' * 64), ('source_sha', None)]:
        assert not advances(first, {**second, key: value})


def test_collect_timeout_preserves_red_for_revoked_generation_without_exception_text(tmp_path):
    root = tmp_path / 'dynamic-shadow'
    (tmp_path / 'runtime-health.json').write_text(json.dumps(HEALTH))
    tick = [0]
    def wait(seconds): tick[0] += seconds
    def reader(*a, **k): raise ValueError('PRIVATE_TEST_PAYLOAD')
    result = collect(root, SHA, TREE, timeout=5, clock=lambda: tick[0], wait=wait, now=lambda: AT, reader=reader)
    assert result['status'] == 'RED' and result['generations'] == []
    assert 'PRIVATE_TEST_PAYLOAD' not in json.dumps(result)


def test_two_real_canonical_generations_pass_without_source_writes(tmp_path, monkeypatch):
    from tests.test_rc6_shadow_runtime_wiring import make_store, PRE
    from rc6_shadow_runtime.worker import ShadowRuntime
    from rc6_shadow_runtime.persistence import read_committed_generation
    store, _ = make_store(tmp_path)
    root = tmp_path / 'dynamic-shadow'
    worker = ShadowRuntime(store.path, evidence_root=root, source_roots=[])
    (tmp_path / 'runtime-health.json').write_text(json.dumps(HEALTH))
    tick = [0]; reads = [0]
    def reader(path, **kwargs):
        worker.tick(PRE + timedelta(seconds=30 * reads[0])); reads[0] += 1
        assert kwargs['require_current_status'] is True
        return read_committed_generation(path, **kwargs)
    def wait(seconds): tick[0] += seconds
    result = collect(root, SHA, TREE, timeout=5, clock=lambda: tick[0], wait=wait,
                     now=lambda: PRE + timedelta(seconds=31), reader=reader)
    assert result['status'] == 'GREEN'
    assert len(result['generations']) == 2
    assert result['generations'][1]['sequence'] > result['generations'][0]['sequence']
    with store.connect() as c:
        assert c.execute('SELECT count(*) FROM paper_positions').fetchone()[0] == 0


def test_canonical_gate_precedes_validated_state():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    text = (root / '.github/workflows/porota-deploy-v2-promote.yml').read_text()
    assert text.index('RC6_EXIT139_SOAK=GREEN') < text.index('rc6_deploy_generation_gate.py')
    final = text.index("state['validation_status'] = 'VALIDATED_RUNTIME'")
    assert text.index('rc6_deploy_generation_gate.py') < final
    assert text.index('id: flat_book') < text.index('id: timers_restored') < text.index('id: backup_retire') < final
    assert text.index('RC6_CURRENT_STATE_STILL_PENDING=1') < final


def test_native_red_preserves_reviewed_freshness_cause_and_bounded_costs(tmp_path):
    root=tmp_path/'dynamic-shadow'
    (tmp_path/'runtime-health.json').write_text(json.dumps(HEALTH))
    value=cut()
    value['status']['as_of']=(AT-timedelta(seconds=247)).isoformat()
    value['status']['generation_timing']['tick_seconds']=116.365
    value['status']['private_detail']='PRIVATE_TEST_PAYLOAD'
    value['status']['generation_timing']['stages_seconds']={'family_routing':10.5,'PRIVATE_TEST_PAYLOAD':999,'preopen':float('nan')}
    value['status']['generation_timing']['stages_cpu_seconds']={'family_routing':3.5,'PRIVATE_TEST_PAYLOAD':999}
    value['status']['generation_timing']['tick_cpu_seconds']=30
    tick=[0]
    def wait(seconds):tick[0]+=seconds
    result=collect(root,SHA,TREE,timeout=3,clock=lambda:tick[0],wait=wait,now=lambda:AT,
                   reader=lambda *args,**kwargs:value)
    assert result['status']=='RED' and result['generations']==[]
    assert result['error_reason']=='GENERATION_STALE_OR_FUTURE'
    assert result['last_observation']['age_seconds']==247
    assert result['last_observation']['tick_seconds']==116.365
    assert result['last_observation']['sql_elapsed_ms']==100
    assert result['last_observation']['stages_seconds']=={'family_routing':10.5}
    assert result['last_observation']['stages_cpu_seconds']=={'family_routing':3.5}
    assert result['last_observation']['tick_cpu_seconds']==30
    assert 'PRIVATE_TEST_PAYLOAD' not in json.dumps(result)


def test_native_reader_failure_only_reports_allowlisted_literal_code(tmp_path):
    root=tmp_path/'dynamic-shadow'
    (tmp_path/'runtime-health.json').write_text(json.dumps(HEALTH))
    tick=[0]
    def wait(seconds):tick[0]+=seconds
    def reader(*args,**kwargs):raise ValueError('GENERATION_MEMBER_MISMATCH')
    result=collect(root,SHA,TREE,timeout=3,clock=lambda:tick[0],wait=wait,now=lambda:AT,reader=reader)
    assert result['status']=='RED' and result['error_reason']=='GENERATION_MEMBER_MISMATCH'
    assert result['last_observation']=={}
