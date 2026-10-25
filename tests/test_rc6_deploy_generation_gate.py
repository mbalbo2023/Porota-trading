from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from scripts.rc6_deploy_generation_gate import advances, closed_idle_receipt, collect, receipt

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
    assert result['acceptance_mode'] == 'FRESH_GENERATIONS'
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


def idle_fixture(tmp_path):
    from tests.test_rc6_679_closed_idle import CLOSED
    from tests.test_rc6_shadow_runtime_wiring import make_store
    from rc6_shadow_runtime.worker import ShadowRuntime, publish_closed_idle
    store, _ = make_store(tmp_path, count=1)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path/'dynamic-shadow', source_roots=[])
    worker.tick(CLOSED)
    from rc6_shadow_runtime.persistence import EvidenceFiles
    with EvidenceFiles(worker.root) as files:
        ident = files.read('CURRENT.json')['generation_id']
    assert publish_closed_idle(worker, CLOSED+timedelta(seconds=30), ident)
    (tmp_path/'runtime-health.json').write_text(json.dumps(HEALTH))
    return worker, ident, CLOSED


def test_real_closed_idle_cut_has_separate_progressive_acceptance(tmp_path):
    from rc6_shadow_runtime.worker import publish_closed_idle
    worker, ident, closed = idle_fixture(tmp_path)
    elapsed = [0]; samples = []
    def wait(seconds):
        elapsed[0] += seconds
        if elapsed[0] == 60:
            assert publish_closed_idle(worker, closed+timedelta(seconds=91), ident)
    result = collect(worker.root, SHA, TREE, timeout=130,
                     minimum_generations=10, strict=True, allow_closed_idle=True,
                     clock=lambda:elapsed[0], wait=wait,
                     now=lambda:closed+timedelta(seconds=31+elapsed[0]),
                     sample=lambda:samples.append(elapsed[0]) or {'reserve':'GREEN'})
    assert result['status'] == 'GREEN' and result['acceptance_mode'] == 'CLOSED_IDLE'
    assert result['generations'] == [] and len(result['idle_observations']) >= 10
    assert result['idle_heartbeat_count'] == 2 and result['source_freshness_renewed'] is False
    assert len(samples) > 40 and len(result['resources']) == len(samples)
    assert result['rejection_count'] == 0


@pytest.mark.parametrize('change,reason', [
    ({'state':'SHADOW_OBSERVING'},'GENERATION_IDLE_INCONSISTENT'),
    ({'source_freshness_renewed':True},'GENERATION_IDLE_INCONSISTENT'),
    ({'real_orders_sent':1},'GENERATION_IDLE_INCONSISTENT'),
    ({'last_generation_id':'0'*64},'GENERATION_IDLE_INCONSISTENT'),
    ({'heartbeat_at':(AT+timedelta(days=3)).isoformat()},'GENERATION_IDLE_CLOCK_UNVERIFIED'),
    ({'heartbeat_at':AT.isoformat()},'GENERATION_IDLE_CLOCK_UNVERIFIED'),
])
def test_idle_receipt_rejects_forgery_future_stale_and_unsafe(tmp_path, change, reason):
    from rc6_shadow_runtime.persistence import EvidenceFiles
    worker, _, closed = idle_fixture(tmp_path)
    with EvidenceFiles(worker.root) as files:
        idle = files.read('idle.json')
        files.write('idle.json', {**idle, **change})
    with pytest.raises(ValueError,match=reason):
        closed_idle_receipt(worker.root, HEALTH, SHA, TREE,
                            closed+timedelta(seconds=31))


def test_idle_receipt_checks_calendar_status_identity_and_historical_safety(tmp_path):
    from rc6_shadow_runtime.persistence import EvidenceFiles
    worker, _, closed = idle_fixture(tmp_path)
    with pytest.raises(ValueError,match='WORKER_SOURCE_IDENTITY_UNVERIFIED'):
        closed_idle_receipt(worker.root, HEALTH, 'c'*40, TREE,
                            closed+timedelta(seconds=31))
    with pytest.raises(ValueError,match='GENERATION_IDLE_SESSION_UNVERIFIED'):
        closed_idle_receipt(worker.root, HEALTH, SHA, TREE, closed-timedelta(hours=6))
    with EvidenceFiles(worker.root) as files:
        status = files.read('status.json')
        files.write('status.json',{**status,'real_orders_sent':1})
    with pytest.raises(ValueError,match='GENERATION_IDLE_INCONSISTENT'):
        closed_idle_receipt(worker.root, HEALTH, SHA, TREE,
                            closed+timedelta(seconds=31))


def test_idle_resource_or_pointer_transition_is_red_not_fresh_claim(tmp_path):
    from rc6_shadow_runtime.persistence import EvidenceFiles
    worker, _, closed = idle_fixture(tmp_path)
    elapsed=[0]
    def wait(n):
        elapsed[0]+=n
        if elapsed[0]==10:
            with EvidenceFiles(worker.root) as files:
                idle=files.read('idle.json')
                files.write('idle.json',{**idle,'last_generation_id':'0'*64})
    result=collect(worker.root,SHA,TREE,strict=True,minimum_generations=10,
        allow_closed_idle=True,timeout=80,clock=lambda:elapsed[0],wait=wait,
        now=lambda:closed+timedelta(seconds=31+elapsed[0]),
        sample=lambda:{'available_memory_bytes':128*1024**2})
    assert result['status']=='RED' and result['error_reason']=='GENERATION_IDLE_TRANSITION_UNVERIFIED'
    assert result['generations']==[]


def test_idle_reserve_failure_stops_strict_soak(tmp_path):
    worker, _, closed=idle_fixture(tmp_path)
    samples=[0]
    def sample():
        samples[0]+=1
        if samples[0]==5:
            raise ValueError('NATIVE_SOAK_DISK_RESERVE_BREACHED')
        return {'free_disk_bytes':3*1024**3}
    elapsed=[0]
    result=collect(worker.root,SHA,TREE,strict=True,minimum_generations=10,
        allow_closed_idle=True,timeout=80,clock=lambda:elapsed[0],
        wait=lambda n:elapsed.__setitem__(0,elapsed[0]+n),
        now=lambda:closed+timedelta(seconds=31+elapsed[0]),sample=sample)
    assert samples[0]==5 and result['status']=='RED'
    assert result['error_reason']=='NATIVE_SOAK_DISK_RESERVE_BREACHED'


def test_predeploy_replays_a72_closed_idle_after_prior_soak():
    from pathlib import Path
    workflow=(Path(__file__).resolve().parents[1]/
              '.github/workflows/porota-deploy-v2-promote.yml').read_text()
    prior=workflow.index('RC6_EXIT139_SOAK=GREEN')
    gate=workflow.index('python /app/scripts/rc6_deploy_generation_gate.py')
    soak=workflow.index('python3 "$REPO/scripts/rc6_native_soak.py"')
    assert prior < gate < soak < workflow.index("state['validation_status'] = 'VALIDATED_RUNTIME'")
    assert '--allow-closed-idle' in workflow[gate:soak]
    assert '--allow-closed-idle' in workflow[soak:workflow.index('PPI_WATCH_AFTER=')]
    assert '--allow-closed-idle' not in workflow[workflow.index('native-postrestore-generation-acceptance.json'):]


def test_a72_host_incident_provenance_and_full_gate_transition(tmp_path):
    from pathlib import Path
    from rc6_shadow_runtime.persistence import EvidenceFiles
    from rc6_shadow_runtime.worker import ShadowRuntime, publish_closed_idle
    from tests.test_rc6_shadow_runtime_wiring import make_store
    fixture=json.loads((Path(__file__).resolve().parent/'fixtures'/
                       'rc6_a72_closed_idle_deploy_incident.json').read_text())
    assert fixture['schema']=='RC6_A72_CLOSED_IDLE_DEPLOY_INCIDENT_V1'
    assert fixture['provenance']['failed_deploy_run']==40210730520
    assert fixture['provenance']['readonly_artifact']==12605804079
    event=fixture['transition']
    assert event['prior_validation_status']=='DEPLOYED_VALIDATION_PENDING'
    assert event['mode']=='PRODUCTION_PAPER' and event['real_orders_sent']==0
    assert event['real_routes']=='NOT_CALLED' and event['ppi_watch_untouched'] is True
    assert event['before']['pid']==event['after']['pid']
    assert event['after']['paper_equity_max_id']-event['before']['paper_equity_max_id']==12
    assert round(event['after']['cpu_seconds']-event['before']['cpu_seconds'],2)==.66
    source=datetime.fromisoformat(event['source_as_of'])
    earlier=datetime.fromisoformat(event['before']['heartbeat_at'])
    later=datetime.fromisoformat(event['after']['heartbeat_at'])
    observed=datetime.fromisoformat(event['before']['observed_at'])
    assert source<earlier<observed<later
    store,_=make_store(tmp_path,count=1)
    worker=ShadowRuntime(store.path,evidence_root=tmp_path/'dynamic-shadow',source_roots=[])
    worker.tick(source)
    with EvidenceFiles(worker.root) as files:
        ident=files.read('CURRENT.json')['generation_id']
    assert publish_closed_idle(worker,earlier,ident)
    (tmp_path/'runtime-health.json').write_text(json.dumps(HEALTH))
    elapsed=[0]
    def wait(seconds):
        elapsed[0]+=seconds
        if elapsed[0]==10:
            assert publish_closed_idle(worker,earlier+timedelta(seconds=60),ident)
        if elapsed[0]==70:
            assert publish_closed_idle(worker,later,ident)
    def collect_incident(allow):
        return collect(worker.root,SHA,TREE,timeout=130,clock=lambda:elapsed[0],
                       wait=wait,now=lambda:observed+timedelta(seconds=elapsed[0]),
                       allow_closed_idle=allow)
    # The previous deployment gate saw only a revoked current generation.
    previous=collect(worker.root,SHA,TREE,timeout=4,clock=lambda:elapsed[0],
                     wait=lambda n:elapsed.__setitem__(0,elapsed[0]+n),
                     now=lambda:observed+timedelta(seconds=elapsed[0]))
    assert previous['status']=='RED' and previous['error_reason']==event['failure_code']
    elapsed[0]=0
    recovered=collect_incident(True)
    assert recovered['status']=='GREEN' and recovered['acceptance_mode']=='CLOSED_IDLE', recovered
    assert recovered['generations']==[] and recovered['source_freshness_renewed'] is False
