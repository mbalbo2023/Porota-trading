"""A248: owner PAUSE V2 has its own root proof, never a relabeled A176."""
import copy
import json
from types import SimpleNamespace

import pytest

import rc6_preopen as preopen
from scripts import rc6_deploy_preopen_gate as gate
from test_rc6_a176_preopen_no_go_continuation import (
    held_host, NOW, SHA, report as historical_report,
)


PAUSE = {
    'status': 'PAPER_PAUSED_BY_OPERATOR',
    'receipt_sha256': '9' * 64,
    'operator_comment_id': 6548649429,
    'rearm_run_id': 41253985210,
    'issued_at_ar': '2026-10-29T10:00:00-03:00',
    'candidate_sha': 'a' * 40,
    'candidate_tree_sha': 'c' * 40,
}


def _install_v2_pause(monkeypatch):
    monkeypatch.setattr(preopen, 'evaluate_rearm', lambda **_: (
        False, 'OPERATOR_NO_GO_PENDING_EXPLICIT_REARM', None))
    monkeypatch.setattr(preopen, 'trusted_operator_pause', lambda **_: copy.deepcopy(PAUSE))
    # The prior chain is historical, not a valid receipt for a new V2 PAUSE.
    from scripts import rc6_deploy_scheduler_suspend as suspend
    monkeypatch.setattr(suspend, 'exact_a176_operator_no_go_continuation',
                        lambda **_: (_ for _ in ()).throw(
                            AssertionError('A176 reused as newer V2 PAUSE')))


def _v2_report(timers):
    report = historical_report()
    report['checks']['required_rc6_timers'] = copy.deepcopy(timers)
    report.update(
        operator_pause_day_ar=timers['operator_pause_day_ar'],
        operator_pause_operation_id=timers['operator_pause_operation_id'],
        operator_pause_receipt_sha256=timers['operator_pause_receipt_sha256'],
        operator_pause_v2=copy.deepcopy(PAUSE),
    )
    report.pop('operator_no_go_continuation')
    return report


def _validate(report, monkeypatch):
    monkeypatch.setattr(gate, 'trusted_operator_pause', lambda **_: copy.deepcopy(PAUSE))
    return gate.validate_report(report, phase='T_MINUS_45', return_code=0,
                                now=NOW, is_operational=lambda _: True)


def test_v2_owner_pause_replaces_historical_proof_in_daily_and_deploy_timers(
        held_host, monkeypatch):
    _, daily = held_host
    _install_v2_pause(monkeypatch)
    daily['enabled'] = True
    timers = preopen.required_timers()
    assert timers['state'] == 'GREEN'
    assert timers['operator_pause_v2'] == PAUSE
    assert timers['operator_pause_receipt_sha256'] == PAUSE['receipt_sha256']
    assert timers['operator_pause_operation_id'] == 'RC6-OWNER-PAUSE-V2-41253985210'
    assert 'operator_no_go_continuation' not in timers
    assert timers['units']['porota-preopen-rc6.timer'] == {
        'state':'GREEN', 'enabled':'disabled', 'active':'inactive'}

    result = _validate(_v2_report(timers), monkeypatch)
    assert result['preopen_status'] == 'PAUSED_NO_GO'
    assert result['execution_authorized'] is False
    assert result['readiness_verified'] is False

    daily['enabled'] = False
    deploy_timers = preopen.required_timers(SHA)
    assert deploy_timers['state'] == 'GREEN'
    assert deploy_timers['operator_pause_v2'] == PAUSE
    assert 'operator_no_go_continuation' not in deploy_timers


@pytest.mark.parametrize('mutation', (
    'changed_comment', 'changed_digest', 'changed_run_id', 'changed_operation',
    'changed_issued_at', 'missing_root_proof', 'replayed_a176',
    'timer_reenabled', 'bad_timer_state', 'forged_go_receipt',
    'missing_candidate_tree', 'masked_pause_as_green',
))
def test_v2_root_pause_validator_rejects_forged_or_disabled_proof(
        held_host, monkeypatch, mutation):
    _, daily = held_host
    _install_v2_pause(monkeypatch)
    daily['enabled'] = True
    report = _v2_report(preopen.required_timers())
    if mutation in {'changed_comment', 'changed_digest', 'changed_run_id', 'changed_issued_at'}:
        key = {'changed_comment':'operator_comment_id',
               'changed_digest':'receipt_sha256',
               'changed_run_id':'rearm_run_id',
               'changed_issued_at':'issued_at_ar'}[mutation]
        replacements = {'changed_comment':999, 'changed_digest':'f'*64,
                        'changed_run_id':999, 'changed_issued_at':'2026-10-29T09:59:00-03:00'}
        report['operator_pause_v2'][key] = replacements[mutation]
        report['checks']['required_rc6_timers']['operator_pause_v2'][key] = replacements[mutation]
        if mutation == 'changed_digest':
            report['operator_pause_receipt_sha256'] = replacements[mutation]
            report['checks']['required_rc6_timers']['operator_pause_receipt_sha256'] = replacements[mutation]
        if mutation == 'changed_run_id':
            report['operator_pause_operation_id'] = 'RC6-OWNER-PAUSE-V2-999'
            report['checks']['required_rc6_timers']['operator_pause_operation_id'] = (
                'RC6-OWNER-PAUSE-V2-999')
    elif mutation == 'changed_operation':
        report['operator_pause_operation_id'] = 'A180-NO-GO-' + PAUSE['receipt_sha256'][:16]
        report['checks']['required_rc6_timers']['operator_pause_operation_id'] = (
            report['operator_pause_operation_id'])
    elif mutation == 'missing_root_proof':
        monkeypatch.setattr(gate, 'trusted_operator_pause', lambda **_: None)
    elif mutation == 'replayed_a176':
        report['operator_no_go_continuation'] = {'status':'NO_GO_PENDING_EXPLICIT_REARM'}
    elif mutation == 'timer_reenabled':
        report['checks']['required_rc6_timers']['units']['porota-preopen-rc6.timer'][
            'enabled'] = 'enabled'
    elif mutation == 'bad_timer_state':
        report['checks']['required_rc6_timers']['units']['porota-preopen-rc6.timer'][
            'state'] = 'RED'
    elif mutation == 'forged_go_receipt':
        report['paper_admission_receipt'] = {'status':'GO_PAPER_SCALPING'}
    elif mutation == 'missing_candidate_tree':
        del report['operator_pause_v2']['candidate_tree_sha']
        del report['checks']['required_rc6_timers']['operator_pause_v2']['candidate_tree_sha']
    elif mutation == 'masked_pause_as_green':
        report['status'] = 'GREEN'
    if mutation != 'missing_root_proof':
        monkeypatch.setattr(gate, 'trusted_operator_pause', lambda **_: copy.deepcopy(PAUSE))
    with pytest.raises(gate.PreopenRejected):
        gate.validate_report(report, phase='T_MINUS_45', return_code=0,
                             now=NOW, is_operational=lambda _: True)


def test_preopen_v2_pause_never_calls_receipt_publisher_or_attributes_a176(
        held_host, monkeypatch, capsys):
    _, daily = held_host
    _install_v2_pause(monkeypatch)
    daily['enabled'] = True
    monkeypatch.setattr(preopen.byma, 'es_dia_habil_operativo', lambda _: True)
    monkeypatch.setattr(preopen.shutil, 'disk_usage', lambda _:
                        SimpleNamespace(free=preopen.TARGET_FREE_BYTES + 1))
    green = lambda *_, **__: {'state':'GREEN'}
    for name in ('release_identity', 'observer_db', 'container', 'byma_morning_watch',
                 'iol_reference_state', 'blocked_history_timers',
                 'foreign_market_policy', 'focus_contracts'):
        monkeypatch.setattr(preopen, name, green)
    monkeypatch.setattr(preopen, 'publish_preopen', lambda *_args, **_kwargs:
                        (_ for _ in ()).throw(AssertionError('PAUSE cannot issue a GO')))
    assert preopen.main('T_MINUS_45', publish_paper_gate=True) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'PAUSED_NO_GO'
    assert report['operator_pause_v2'] == PAUSE
    assert 'operator_no_go_continuation' not in report
    receipt = report['paper_admission_receipt']
    assert receipt['status'] == 'NO_GO_PAPER_SCALPING'
    assert receipt['new_receipt_written'] is False
    assert receipt['operator_intent_receipt_sha256'] == PAUSE['receipt_sha256']
    assert 'continuation_receipt_sha256' not in receipt
