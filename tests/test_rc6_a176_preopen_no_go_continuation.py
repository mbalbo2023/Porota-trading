"""A176 29-Oct continuation is an exact receipt, never a calendar GO."""
from datetime import datetime
import hashlib
import json
from pathlib import Path

import pytest

import rc6_preopen as preopen
from scripts import rc6_deploy_preopen_gate as gate
from scripts import rc6_deploy_scheduler_suspend as suspend


NOW = datetime(2026, 10, 29, 10, 15, tzinfo=preopen.TZ)
SHA = 'a' * 40
OPERATION = 'A176-40796450488-1'
PROOF = {'status': 'NO_GO_PENDING_EXPLICIT_REARM',
         'business_day_art': '2026-10-29', 'receipt_sha256': 'b' * 64,
         'operation_id': OPERATION, 'a151_origin_sha256': 'c' * 64,
         'a175_quiescence_sha256': 'd' * 64,
         'a175_scheduler_sha256': 'e' * 64,
         'paper_execution_authorized': False}


class Day29(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz or preopen.TZ)


@pytest.fixture
def held_host(tmp_path, monkeypatch):
    raw = Path('ops/policy/host-control-plane-reconciliation-v2.json').read_bytes()
    units = suspend.timer_policy(raw)
    policy = tmp_path / 'ops/policy/host-control-plane-reconciliation-v2.json'
    policy.parent.mkdir(parents=True)
    policy.write_bytes(raw)
    receipt = tmp_path / 'data/deploy' / f'scheduler-suspend-{SHA}.json'
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({
        'candidate_sha': SHA, 'status': 'SUSPENDED',
        'policy_sha256': hashlib.sha256(raw).hexdigest(),
        'timers': units, 'attempted': units, 'ppi_before': [],
        'preserved_iol_timer': suspend.preserved_iol_guard(raw),
        'operator_pause_timer': suspend.OPERATOR_PREOPEN_TIMER,
        'operator_pause_receipt_sha256': 'f' * 64,
    }))
    monkeypatch.setattr(preopen, 'ROOT', tmp_path)
    monkeypatch.setattr(preopen, 'datetime', Day29)
    monkeypatch.setattr(suspend, 'ppi_snapshot', lambda: [])
    monkeypatch.setattr(suspend, 'operator_preopen_hold_evidence', lambda: 'f' * 64)
    monkeypatch.setattr(suspend, 'operator_preopen_timer_disabled', lambda: True)
    current_proof = dict(PROOF)
    monkeypatch.setattr(suspend, 'exact_a176_operator_no_go_continuation',
                        lambda: current_proof, raising=False)
    daily = {'enabled': False}

    def cmd(args, timeout=20):
        unit = args[2]
        if args[1] == 'is-enabled':
            return ((1, 'disabled', '') if unit == suspend.OPERATOR_PREOPEN_TIMER
                    else (0, 'enabled', ''))
        if args[1] == 'is-active':
            if unit.endswith('.service'):
                return 3, 'inactive', ''
            if unit == suspend.OPERATOR_PREOPEN_TIMER:
                return 3, 'inactive', ''
            if unit == suspend.IOL_OAUTH_GUARD_TIMER or daily['enabled']:
                return 0, 'active', ''
            return 3, 'inactive', ''
        if args[1] == 'show':
            return 0, unit.removesuffix('.timer') + '.service', ''
        raise AssertionError(args)

    monkeypatch.setattr(preopen, 'cmd', cmd)
    return current_proof, daily


def report(proof=PROOF):
    checks = {name: {'state': 'GREEN'} for name in gate.CHECKS}
    checks['required_rc6_timers'].update({
        'operator_paper_paused': True, 'operator_pause_day_ar': '2026-10-29',
        'operator_pause_operation_id': OPERATION,
        'operator_pause_receipt_sha256': PROOF['receipt_sha256'],
        'operator_no_go_continuation': proof,
    })
    return {'schema': gate.SCHEMA, 'generated_at_ar': NOW.isoformat(),
            'status': 'PAUSED_NO_GO', 'phase': 'T_MINUS_45',
            'read_only': True, 'network_order_test_performed': False,
            'dry_equivalent': True, 'direct_telegram_send': False,
            'red_checks': [], 'checks': checks, 'operator_paper_paused': True,
            'operator_pause_day_ar': '2026-10-29',
            'operator_pause_operation_id': OPERATION,
            'operator_pause_receipt_sha256': PROOF['receipt_sha256'],
            'operator_no_go_continuation': proof,
            'paper_admission_hold': 'OPERATOR_NO_GO_PENDING_EXPLICIT_REARM'}


def accepted(row):
    return gate.validate_report(row, phase='T_MINUS_45', return_code=0,
                                now=NOW, is_operational=lambda _: True)


def test_exact_a176_held_deploy_then_restored_daily_remain_no_go(held_host):
    _, daily = held_host
    held = preopen.required_timers(SHA)
    assert held['state'] == 'GREEN'
    assert held['operator_no_go_continuation'] == PROOF
    daily['enabled'] = True
    restored = preopen.required_timers()
    assert restored['state'] == 'GREEN'
    assert restored['operator_paper_paused'] is True
    assert restored['units'][suspend.OPERATOR_PREOPEN_TIMER]['active'] == 'inactive'
    row = report()
    proof = accepted(row)
    assert proof['preopen_status'] == 'PAUSED_NO_GO'
    assert proof['readiness_verified'] is False
    assert proof['execution_authorized'] is False


@pytest.mark.parametrize('key,value', [
    ('status', 'GREEN'), ('business_day_art', '2026-10-28'),
    ('paper_execution_authorized', True), ('receipt_sha256', 'bad'),
    ('operation_id', 'A151-40796450488-1'),
    ('a151_origin_sha256', 'bad'),
])
def test_receipt_mismatch_or_missing_rejects_deploy_and_daily(held_host, key, value):
    current, daily = held_host
    current[key] = value
    assert preopen.required_timers(SHA)['state'] == 'RED'
    daily['enabled'] = True
    assert preopen.required_timers()['state'] == 'RED'


@pytest.mark.parametrize('change', (
    'missing_proof', 'timer_mismatch', 'invalid_digest', 'old_operation',
    'wrong_day', 'paper_authorized', 'green', 'missing_hold', 'expired_28',
))
def test_deploy_result_gate_rejects_all_implicit_rearm_paths(change):
    row = report()
    if change == 'missing_proof':
        row.pop('operator_no_go_continuation')
    elif change == 'timer_mismatch':
        row['checks']['required_rc6_timers']['operator_no_go_continuation'] = dict(PROOF, receipt_sha256='0'*64)
    elif change == 'invalid_digest':
        row['operator_pause_receipt_sha256'] = '0'*64
    elif change == 'old_operation':
        row['operator_pause_operation_id'] = 'A151-40796450488-1'
    elif change == 'wrong_day':
        row['operator_pause_day_ar'] = '2026-10-28'
    elif change == 'paper_authorized':
        row['operator_no_go_continuation'] = dict(PROOF, paper_execution_authorized=True)
        row['checks']['required_rc6_timers']['operator_no_go_continuation'] = row['operator_no_go_continuation']
    elif change == 'green':
        row['status'] = 'GREEN'
    elif change == 'missing_hold':
        row.pop('paper_admission_hold')
    elif change == 'expired_28':
        row['operator_pause_operation_id'] = 'A151-40796450488-1'
        row['checks']['required_rc6_timers']['operator_pause_operation_id'] = 'A151-40796450488-1'
    with pytest.raises(gate.PreopenRejected):
        accepted(row)


def test_daily_manual_preopen_uses_durable_no_go_sidecar_without_overwriting_a125(
        held_host, monkeypatch, capsys):
    _, daily = held_host
    daily['enabled'] = True
    monkeypatch.setattr(preopen.byma, 'es_dia_habil_operativo', lambda _: True)
    monkeypatch.setattr(preopen.shutil, 'disk_usage', lambda _:
                        type('Usage', (), {'free': preopen.TARGET_FREE_BYTES + 1})())
    green = lambda *args, **kwargs: {'state': 'GREEN'}
    for name in ('release_identity', 'observer_db', 'container',
                 'byma_morning_watch', 'iol_reference_state',
                 'blocked_history_timers', 'foreign_market_policy', 'focus_contracts'):
        monkeypatch.setattr(preopen, name, green)
    monkeypatch.setattr(preopen, 'publish_preopen', lambda *args, **kwargs:
                        (_ for _ in ()).throw(AssertionError('A125 must be immutable')))
    assert preopen.main('T_MINUS_45', publish_paper_gate=True) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'PAUSED_NO_GO'
    assert result['paper_admission_hold'] == 'OPERATOR_NO_GO_PENDING_EXPLICIT_REARM'
    assert result['paper_admission_receipt']['status'] == 'NO_GO_PAPER_SCALPING'
    assert result['paper_admission_receipt']['new_receipt_written'] is False
    assert result['network_order_test_performed'] is False
