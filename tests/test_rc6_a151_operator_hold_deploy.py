"""A151: 28-Oct operator NO_GO is independent of the A125 live gate."""

from datetime import datetime, timezone
import hashlib
import json

import pytest

import rc6_preopen
from scripts import rc6_deploy_preopen_gate as deploy_gate
from scripts import rc6_deploy_scheduler_suspend as suspend


class Day28(datetime):
    @classmethod
    def now(cls, tz=None):
        value = datetime(2026, 10, 28, 12, 6, tzinfo=timezone.utc)
        return value if tz is None else value.astimezone(tz)


class Day29(datetime):
    @classmethod
    def now(cls, tz=None):
        value = datetime(2026, 10, 29, 12, 6, tzinfo=timezone.utc)
        return value if tz is None else value.astimezone(tz)


def write_json(path, content):
    path.write_text(json.dumps(content, sort_keys=True, separators=(',', ':')) + '\n')
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def a151(monkeypatch, tmp_path):
    monkeypatch.setattr(suspend, 'ROOT', tmp_path)
    monkeypatch.setattr(suspend, 'datetime', Day28)
    monkeypatch.setattr(suspend, 'operator_preopen_timer_disabled', lambda: True)
    original_marker = dict(candidate_sha=suspend.OPERATOR_PAUSE_SOURCE,
        deploy_sha=suspend.OPERATOR_PAUSE_PRODUCT,
        image_id=suspend.OPERATOR_PAUSE_IMAGE,
        validation_status='VALIDATED_RUNTIME', mode='PRODUCTION_PAPER',
        real_orders_sent=0, real_order_routes='NOT_CALLED')
    marker_sha = write_json(tmp_path/'CURRENT_STATE_V2.json', original_marker)
    old_gate = dict(schema='RC6_PAPER_PREOPEN_GATE_V1',
        status='NO_GO_PAPER_SCALPING', trade_day_ar='2026-10-27',
        candidate_sha=suspend.OPERATOR_PAUSE_SOURCE, phase='OPERATOR_PAUSE')
    old_gate_sha = write_json(tmp_path/'paper_scalping_preopen_gate_v1.json', old_gate)
    a125 = dict(schema='rc6.operator-preopen-pause.v1', status='PAUSED_NO_GO',
        operation_id=suspend.OPERATOR_PAUSE_RUN,
        issued_at_utc='2026-10-27T12:09:10+00:00',
        completed_at_utc='2026-10-27T12:09:18+00:00',
        trade_day_ar=suspend.OPERATOR_PAUSE_DAY,
        source_sha=suspend.OPERATOR_PAUSE_SOURCE,
        product_sha=suspend.OPERATOR_PAUSE_PRODUCT,
        image_id=suspend.OPERATOR_PAUSE_IMAGE,
        marker_sha256=marker_sha, gate_after_sha256=old_gate_sha,
        timer_before={'enabled':'enabled','active':'active'},
        timer_after={'enabled':'disabled','active':'inactive'},
        service_before={'enabled':'static','active':'inactive'},
        service_after={'enabled':'static','active':'inactive'},
        containers_before={'observer':{'id':'a125-id'}},
        containers_after={'observer':{'id':'a125-id'}},
        paper_before={'mode':'PRODUCTION_PAPER','real_orders_sent':0},
        paper_after={'mode':'PRODUCTION_PAPER','real_orders_sent':0},
        ppi_watch_accessed=False, provider_requests=0, admission_allowed=False,
        gate_after={k:old_gate[k] for k in
                    ('status','trade_day_ar','candidate_sha','phase')})
    a125_sha = write_json(tmp_path/'operator-preopen-pause-20261027.json', a125)
    a143_marker = dict(candidate_sha=suspend.A151_OPERATOR_PAUSE_SOURCE,
        deploy_sha=suspend.A151_OPERATOR_PAUSE_DEPLOY,
        image_id=suspend.A151_OPERATOR_PAUSE_IMAGE,
        validation_status='DEPLOYED_VALIDATION_PENDING', mode='PRODUCTION_PAPER',
        real_orders_sent=0)
    a143_marker_sha = write_json(tmp_path/'CURRENT_STATE_V2.json', a143_marker)
    op = 'A151-40650000000-1'
    sidecar = dict(schema='RC6_PAPER_OPERATOR_HOLD_V1',
        status='NO_GO_PAPER_SCALPING', phase='OPERATOR_PAUSE',
        operation_id=op, trade_day_ar='2026-10-28',
        issued_at_ar='2026-10-28T09:05:12-03:00',
        candidate_sha=suspend.A151_OPERATOR_PAUSE_SOURCE,
        source_sha=suspend.A151_OPERATOR_PAUSE_SOURCE,
        deploy_sha=suspend.A151_OPERATOR_PAUSE_DEPLOY,
        image_id=suspend.A151_OPERATOR_PAUSE_IMAGE,
        a125_receipt_sha256=a125_sha, gate_before_sha256=old_gate_sha,
        marker_before_sha256=a143_marker_sha,
        operator_paper_paused=True, execution_authorized=False,
        readiness_verified=False)
    sidecar_path = tmp_path/suspend.A151_PAPER_HOLD
    sidecar_sha = write_json(sidecar_path, sidecar)
    paper = dict(mode='PRODUCTION_PAPER', real_orders_sent=0,
                 open_spot=0, open_caucion=0, active_future=0)
    receipt = dict(schema='rc6.operator-preopen-pause.v2',
        status='PAUSED_NO_GO', operation_id=op,
        trade_day_ar='2026-10-28',
        issued_at_utc='2026-10-28T12:05:10+00:00',
        completed_at_utc='2026-10-28T12:05:20+00:00',
        source_sha=suspend.A151_OPERATOR_PAUSE_SOURCE,
        deploy_sha=suspend.A151_OPERATOR_PAUSE_DEPLOY,
        image_id=suspend.A151_OPERATOR_PAUSE_IMAGE,
        marker_before_sha256=a143_marker_sha, marker_before=a143_marker,
        a125_receipt_sha256=a125_sha, gate_before_sha256=old_gate_sha,
        timer_before={'enabled':'disabled','active':'inactive'},
        timer_after={'enabled':'disabled','active':'inactive'},
        service_before={'enabled':'static','active':'failed'},
        service_after={'enabled':'static','active':'failed'},
        paper_before=paper, paper_after=paper,
        containers_before={'observer':{'id':'a143-id'}},
        containers_after={'observer':{'id':'a143-id'}},
        operator_paper_paused=True, ppi_watch_accessed=False,
        provider_requests=0, admission_allowed=False,
        sidecar_after_sha256=sidecar_sha,
        sidecar_after={k:sidecar[k] for k in
                       ('status','trade_day_ar','candidate_sha','phase','operation_id')})
    receipt_path = tmp_path/suspend.A151_OPERATOR_RECEIPT
    receipt_sha = write_json(receipt_path, receipt)
    return (tmp_path, receipt_path, receipt, receipt_sha,
            sidecar_path, sidecar, old_gate_sha)


def test_a151_attests_both_sidecars_preserves_a125_and_survives_new_marker(a151):
    root, receipt_path, receipt, expected, sidecar_path, sidecar, old_gate_sha = a151
    original_gate = (root/'paper_scalping_preopen_gate_v1.json').read_bytes()
    assert suspend.exact_a151_operator_preopen_pause() == (expected, receipt['operation_id'])
    assert hashlib.sha256(original_gate).hexdigest() == old_gate_sha
    for status in ('DEPLOYED_VALIDATION_PENDING', 'VALIDATED_RUNTIME'):
        changed_marker = dict(candidate_sha='f'*40,
            deploy_sha='e'*40, image_id='sha256:'+'d'*64,
            validation_status=status, mode='PRODUCTION_PAPER',
            real_orders_sent=0)
        write_json(root/'CURRENT_STATE_V2.json', changed_marker)
        assert suspend.exact_a151_operator_preopen_pause()[0] == expected
    assert (root/'paper_scalping_preopen_gate_v1.json').read_bytes() == original_gate


@pytest.mark.parametrize('target,key,value,reason', [
    ('receipt','trade_day_ar','2026-10-29','EVIDENCE_RED'),
    ('receipt','operation_id','A125-40501523106-1','EVIDENCE_RED'),
    ('receipt','admission_allowed',True,'EVIDENCE_RED'),
    ('receipt','ppi_watch_accessed',True,'EVIDENCE_RED'),
    ('receipt','provider_requests',1,'EVIDENCE_RED'),
    ('receipt','gate_before_sha256','0'*64,'EVIDENCE_RED'),
    ('receipt','a125_receipt_sha256','0'*64,'EVIDENCE_RED'),
    ('receipt','sidecar_after_sha256','0'*64,'EVIDENCE_RED'),
    ('receipt','paper_after',{'mode':'PRODUCTION_PAPER','real_orders_sent':0,
                             'open_spot':1,'open_caucion':0,'active_future':0},'EVIDENCE_RED'),
    ('sidecar','status','GO_PAPER_SCALPING','EVIDENCE_RED'),
    ('sidecar','execution_authorized',True,'EVIDENCE_RED'),
])
def test_a151_changed_receipt_or_sidecar_fails_closed(a151,target,key,value,reason):
    _, receipt_path, receipt, _, sidecar_path, sidecar, _ = a151
    target_path, data = (receipt_path,receipt) if target=='receipt' else (sidecar_path,sidecar)
    write_json(target_path, dict(data, **{key:value}))
    with pytest.raises(ValueError, match=reason):
        suspend.exact_a151_operator_preopen_pause()


def test_a151_old_a125_gate_tamper_is_rejected(a151):
    root, *_ = a151
    old_path = root/'paper_scalping_preopen_gate_v1.json'
    saved = json.loads(old_path.read_text())
    write_json(old_path, dict(saved, trade_day_ar='2026-10-28'))
    with pytest.raises(ValueError, match='OPERATOR_PAUSE_EVIDENCE_RED'):
        suspend.exact_a151_operator_preopen_pause()


def test_a151_expires_on_29_even_when_sidecar_remains(a151, monkeypatch):
    monkeypatch.setattr(suspend, 'datetime', Day29)
    with pytest.raises(ValueError, match='DAY_EXPIRED'):
        suspend.exact_a151_operator_preopen_pause()


def test_a151_preopen_requires_both_chains_and_disabled_timer(a151, monkeypatch):
    root, receipt_path, receipt, receipt_sha, _, _, old_gate_sha = a151
    monkeypatch.setattr(rc6_preopen, 'ROOT', root)
    monkeypatch.setattr(rc6_preopen, 'datetime', Day28)
    monkeypatch.setattr(suspend, 'ppi_snapshot', lambda: [])
    units = sorted(set(rc6_preopen.REQUIRED_TIMERS) |
                   {f'porota-a151-fixture-{i:02d}-rc6.timer' for i in range(11)})
    assert len(units) == 16
    raw = json.dumps({'units': {'systemd/' + unit: {
        'role':'ACTIVE_TIMER','active':True} for unit in units}}).encode()
    policy = root/'ops/policy/host-control-plane-reconciliation-v2.json'
    policy.parent.mkdir(parents=True)
    policy.write_bytes(raw)
    candidate = 'b'*40
    old_sha = receipt['a125_receipt_sha256']
    scheduler = dict(candidate_sha=candidate, status='SUSPENDED',
        policy_sha256=hashlib.sha256(raw).hexdigest(),
        timers=units, attempted=units, ppi_before=[],
        operator_pause_timer=suspend.OPERATOR_PREOPEN_TIMER,
        operator_pause_receipt_sha256=old_sha)
    scheduler_path = root/'data/deploy'/f'scheduler-suspend-{candidate}.json'
    scheduler_path.parent.mkdir(parents=True)
    write_json(scheduler_path, scheduler)

    def cmd(args,timeout=20):
        unit=args[2]
        if args[1]=='is-enabled':
            return (1,'disabled','') if unit==suspend.OPERATOR_PREOPEN_TIMER else (0,'enabled','')
        if args[1]=='is-active':
            return (3,'inactive','')
        if args[1]=='show':
            return 0,unit.removesuffix('.timer')+'.service',''
        raise AssertionError(args)
    monkeypatch.setattr(rc6_preopen, 'cmd', cmd)
    check = rc6_preopen.required_timers(candidate)
    assert check['state']=='GREEN'
    assert check['operator_paper_paused'] is True
    assert check['operator_pause_day_ar'] == '2026-10-28'
    assert check['operator_pause_operation_id']==receipt['operation_id']
    assert check['operator_pause_receipt_sha256']==receipt_sha
    assert hashlib.sha256((root/'paper_scalping_preopen_gate_v1.json').read_bytes()).hexdigest()==old_gate_sha
    write_json(scheduler_path, dict(scheduler,operator_pause_receipt_sha256='0'*64))
    assert rc6_preopen.required_timers(candidate)['state']=='RED'
    write_json(scheduler_path,scheduler)
    monkeypatch.setattr(suspend,'operator_preopen_timer_disabled',lambda:False)
    assert rc6_preopen.required_timers(candidate)['state']=='RED'
    monkeypatch.setattr(suspend,'operator_preopen_timer_disabled',lambda:True)
    monkeypatch.setattr(rc6_preopen,'datetime',Day29)
    assert rc6_preopen.required_timers(candidate)['state']=='RED'


def a151_preopen_report():
    now = datetime(2026,10,28,9,5,tzinfo=deploy_gate.TZ)
    digest='e'*64
    operation='A151-40650000000-1'
    checks={key:{'state':'GREEN'} for key in deploy_gate.CHECKS}
    checks['required_rc6_timers'].update(operator_paper_paused=True,
        operator_pause_day_ar='2026-10-28',
        operator_pause_operation_id=operation,
        operator_pause_receipt_sha256=digest)
    report=dict(schema=deploy_gate.SCHEMA,generated_at_ar=now.isoformat(),
        status='PAUSED_NO_GO',phase='T_MINUS_10',read_only=True,
        network_order_test_performed=False,dry_equivalent=True,
        direct_telegram_send=False,red_checks=[],checks=checks,
        operator_paper_paused=True,operator_pause_day_ar='2026-10-28',
        operator_pause_operation_id=operation,
        operator_pause_receipt_sha256=digest)
    return now,report


def test_a151_deploy_acceptance_does_not_permit_paper_go():
    now,report=a151_preopen_report()
    output=deploy_gate.validate_report(report,phase='T_MINUS_10',return_code=0,
        now=now,is_operational=lambda _:True)
    assert output['preopen_status']=='PAUSED_NO_GO'
    assert output['accepted'] is True
    assert output['readiness_verified'] is False
    assert output['operator_paper_paused'] is True
    assert output['execution_authorized'] is False


@pytest.mark.parametrize('mutate', [
    lambda r:r.update(operator_pause_day_ar='2026-10-27'),
    lambda r:r.update(operator_pause_operation_id='A125-40501523106-1'),
    lambda r:r.update(operator_pause_receipt_sha256='bad'),
    lambda r:r.update(status='GREEN',operator_paper_paused=False),
    lambda r:r['checks']['required_rc6_timers'].update(operator_paper_paused=False),
    lambda r:r.update(red_checks=['required_rc6_timers']),
])
def test_a151_preopen_rejects_forged_pause_or_green(mutate):
    now,report=a151_preopen_report()
    mutate(report)
    with pytest.raises(deploy_gate.PreopenRejected):
        deploy_gate.validate_report(report,phase='T_MINUS_10',return_code=0,
            now=now,is_operational=lambda _:True)


def test_a151_preopen_day29_not_implicitly_authorized():
    _,report=a151_preopen_report()
    now=datetime(2026,10,29,9,5,tzinfo=deploy_gate.TZ)
    report['generated_at_ar']=now.isoformat()
    report['operator_pause_day_ar']='2026-10-29'
    report['checks']['required_rc6_timers']['operator_pause_day_ar']='2026-10-29'
    with pytest.raises(deploy_gate.PreopenRejected,match='DAY_EXPIRED'):
        deploy_gate.validate_report(report,phase='T_MINUS_10',return_code=0,
            now=now,is_operational=lambda _:True)
