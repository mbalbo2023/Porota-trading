"""A125's exact PAUSED_NO_GO cannot be converted into PAPER permission."""

import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from scripts import rc6_deploy_scheduler_suspend as suspend
from scripts import rc6_deploy_preopen_gate as gate
from scripts import porota_apply_host_control_plane_v2 as host_units
import rc6_preopen


class OperatorDay(datetime):
    @classmethod
    def now(cls, tz=None):
        current = datetime(2026, 10, 27, 18, 0, tzinfo=timezone.utc)
        return current if tz is None else current.astimezone(tz)


class NextDay(datetime):
    @classmethod
    def now(cls, tz=None):
        current = datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc)
        return current if tz is None else current.astimezone(tz)


def write_json(path, content):
    path.write_text(json.dumps(content, sort_keys=True, separators=(',', ':')) + '\n')
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def operator_pause(monkeypatch, tmp_path):
    monkeypatch.setattr(suspend, 'ROOT', tmp_path)
    monkeypatch.setattr(suspend, 'datetime', OperatorDay)
    marker = {'candidate_sha': suspend.OPERATOR_PAUSE_SOURCE,
              'deploy_sha': suspend.OPERATOR_PAUSE_PRODUCT,
              'image_id': suspend.OPERATOR_PAUSE_IMAGE,
              'validation_status': 'VALIDATED_RUNTIME',
              'mode': 'PRODUCTION_PAPER', 'real_orders_sent': 0,
              'real_order_routes': 'NOT_CALLED'}
    marker_sha = write_json(tmp_path/'CURRENT_STATE_V2.json', marker)
    gate_data = {'schema': 'RC6_PAPER_PREOPEN_GATE_V1',
                 'status': 'NO_GO_PAPER_SCALPING',
                 'trade_day_ar': '2026-10-27',
                 'candidate_sha': suspend.OPERATOR_PAUSE_SOURCE,
                 'phase': 'OPERATOR_PAUSE'}
    gate_sha = write_json(tmp_path/'paper_scalping_preopen_gate_v1.json', gate_data)
    paused = {'schema': 'rc6.operator-preopen-pause.v1',
              'status': 'PAUSED_NO_GO',
              'operation_id': suspend.OPERATOR_PAUSE_RUN,
              'issued_at_utc': '2026-10-27T12:09:10+00:00',
              'completed_at_utc': '2026-10-27T12:09:18+00:00',
              'trade_day_ar': suspend.OPERATOR_PAUSE_DAY,
              'source_sha': suspend.OPERATOR_PAUSE_SOURCE,
              'product_sha': suspend.OPERATOR_PAUSE_PRODUCT,
              'image_id': suspend.OPERATOR_PAUSE_IMAGE,
              'marker_sha256': marker_sha,
              'gate_after_sha256': gate_sha,
              'timer_before': {'enabled': 'enabled', 'active': 'active'},
              'timer_after': {'enabled': 'disabled', 'active': 'inactive'},
              'service_before': {'enabled': 'static', 'active': 'inactive'},
              'service_after': {'enabled': 'static', 'active': 'inactive'},
              'containers_before': {'observer': {'id': 'before'}},
              'containers_after': {'observer': {'id': 'before'}},
              'paper_before': {'mode': 'PRODUCTION_PAPER', 'real_orders_sent': 0},
              'paper_after': {'mode': 'PRODUCTION_PAPER', 'real_orders_sent': 0},
              'ppi_watch_accessed': False, 'provider_requests': 0,
              'admission_allowed': False,
              'gate_after': {k:gate_data[k] for k in
                             ('status','trade_day_ar','candidate_sha','phase')}}
    receipt = tmp_path/'operator-preopen-pause-20261027.json'
    receipt_sha = write_json(receipt, paused)
    return tmp_path, receipt, paused, receipt_sha


def test_exact_operator_evidence_and_day_expiration(operator_pause, monkeypatch):
    tmp_path, receipt, content, sha = operator_pause
    assert suspend.exact_operator_preopen_pause(require_original_marker=True) == sha
    changed = dict(content, admission_allowed=True)
    write_json(receipt, changed)
    with pytest.raises(ValueError, match='OPERATOR_PAUSE_EVIDENCE_RED'):
        suspend.exact_operator_preopen_pause()
    write_json(receipt, content)
    monkeypatch.setattr(suspend, 'datetime', NextDay)
    with pytest.raises(ValueError, match='DAY_EXPIRED'):
        suspend.exact_operator_preopen_pause()


def test_operator_pause_scheduler_never_starts_preopen(operator_pause, monkeypatch):
    tmp_path, receipt, _, _ = operator_pause
    names = {'porota-preopen-rc6.timer', 'porota-runtime-evidence-rc6.timer'}
    states = {'porota-preopen-rc6.timer': ('inactive', 'disabled'),
              'porota-runtime-evidence-rc6.timer': ('active', 'enabled')}
    mutations = []

    def command(*args):
        if args[:2] == ('systemctl', 'list-unit-files'):
            return ('', 0)
        unit = args[2]
        if args[1] == 'is-active':
            value = states.get(unit, ('inactive', 'static'))[0]
            return value, 0 if value == 'active' else 3
        if args[1] == 'is-enabled':
            value = states.get(unit, ('inactive', 'static'))[1]
            return value, 1 if value == 'disabled' else 0
        if args[1] == 'show':
            return unit.removesuffix('.timer') + '.service', 0
        if args[1] in {'stop', 'start'}:
            assert unit in names and unit != 'porota-preopen-rc6.timer'
            mutations.append((args[1], unit))
            states[unit] = ('inactive' if args[1] == 'stop' else 'active', 'enabled')
            return '', 0
        raise AssertionError(args)

    policy = json.dumps({'units': {'systemd/' + name: {'role': 'ACTIVE_TIMER', 'active': True}
                                   for name in names}}).encode()
    policy_b64 = base64.b64encode(policy).decode()
    monkeypatch.setattr(suspend, 'command', command)
    monkeypatch.setattr(suspend, 'require_owned_running', lambda: None)
    candidate = 'a' * 40
    paused = suspend.run(candidate, 'suspend', policy_b64)
    assert paused['status'] == 'SUSPENDED'
    assert paused['operator_pause_timer'] == 'porota-preopen-rc6.timer'
    assert mutations == [('stop', 'porota-runtime-evidence-rc6.timer')]
    gate_path = tmp_path/'paper_scalping_preopen_gate_v1.json'
    saved_gate = gate_path.read_bytes()
    changed_gate = json.loads(saved_gate)
    changed_gate['status'] = 'GO_PAPER_SCALPING'
    write_json(gate_path, changed_gate)
    with pytest.raises(ValueError, match='OPERATOR_PAUSE_EVIDENCE_RED'):
        suspend.run(candidate, 'restore', policy_b64)
    assert mutations == [('stop', 'porota-runtime-evidence-rc6.timer')]
    gate_path.write_bytes(saved_gate)
    assert suspend.run(candidate, 'restore', policy_b64)['status'] == 'RESTORED'
    assert suspend.run(candidate, 'verify', policy_b64)['status'] == 'RESTORED'
    assert mutations == [('stop', 'porota-runtime-evidence-rc6.timer'),
                         ('start', 'porota-runtime-evidence-rc6.timer')]
    assert states['porota-preopen-rc6.timer'] == ('inactive', 'disabled')


def test_missing_a125_evidence_blocks_before_systemd_mutation(operator_pause, monkeypatch):
    _, receipt, _, _ = operator_pause
    receipt.unlink()
    monkeypatch.setattr(suspend, 'command', lambda *args:
                        ('', 0) if args[1] == 'list-unit-files' else
                        ('inactive', 3) if args[1] == 'is-active' else
                        ('disabled', 1) if args[1] == 'is-enabled' else
                        pytest.fail('systemd mutation or unexpected call'))
    policy = base64.b64encode(json.dumps({'units': {'systemd/porota-preopen-rc6.timer':
                            {'role': 'ACTIVE_TIMER', 'active': True}}}).encode()).decode()
    with pytest.raises(ValueError, match='PATH_UNSAFE'):
        suspend.run('b' * 40, 'suspend', policy)


def paused_preopen_report():
    now = datetime(2026, 10, 27, 12, 0, tzinfo=gate.TZ)
    digest = 'f' * 64
    checks = {name: {'state': 'GREEN'} for name in gate.CHECKS}
    checks['required_rc6_timers'].update(operator_paper_paused=True,
        operator_pause_day_ar='2026-10-27',
        operator_pause_operation_id=suspend.OPERATOR_PAUSE_RUN,
        operator_pause_receipt_sha256=digest)
    return now, dict(schema=gate.SCHEMA, generated_at_ar=now.isoformat(),
        status='PAUSED_NO_GO', phase='T_MINUS_45', read_only=True,
        network_order_test_performed=False, dry_equivalent=True,
        direct_telegram_send=False, red_checks=[], checks=checks,
        operator_paper_paused=True, operator_pause_day_ar='2026-10-27',
        operator_pause_operation_id=suspend.OPERATOR_PAUSE_RUN,
        operator_pause_receipt_sha256=digest)


def test_deploy_preopen_accepts_pause_without_authorizing_trading():
    now, report = paused_preopen_report()
    actual = gate.validate_report(report, phase='T_MINUS_45', return_code=0,
                                  now=now, is_operational=lambda _: True)
    assert actual['accepted'] is True
    assert actual['preopen_status'] == 'PAUSED_NO_GO'
    assert actual['operator_paper_paused'] is True
    assert actual['readiness_verified'] is False
    assert actual['execution_authorized'] is False


@pytest.mark.parametrize('field,value', [
    ('operator_paper_paused', False), ('operator_pause_day_ar', '2026-10-28'),
    ('operator_pause_operation_id', 'A125-impostor'),
    ('operator_pause_receipt_sha256', 'invalid'), ('status', 'GREEN'),
])
def test_deploy_preopen_rejects_forged_or_masked_pause(field, value):
    now, report = paused_preopen_report()
    report[field] = value
    with pytest.raises(gate.PreopenRejected):
        gate.validate_report(report, phase='T_MINUS_45', return_code=0,
                             now=now, is_operational=lambda _: True)


def test_host_unit_reconciliation_preserves_a125_disabled_preopen(monkeypatch, tmp_path):
    # Hermetic host-policy fixture: a sparse checkout may omit systemd/ even
    # though the packaged Predeploy artifact always includes these units.
    repo_root = tmp_path / 'isolated_repo'
    names = {'porota-preopen-rc6.timer', 'porota-runtime-evidence-rc6.timer'}
    (repo_root / 'scripts').mkdir(parents=True)
    (repo_root / 'scripts/rc6_deploy_scheduler_suspend.py').write_text('# fixture\n')
    (repo_root / 'systemd').mkdir()
    for unit_name in names:
        (repo_root / 'systemd' / unit_name).write_text('[Unit]\nDescription=test-only\n')
    policy = {'units': {'systemd/' + name: {'role': 'ACTIVE_TIMER', 'active': True,
                                         'enabled': True, 'install': True}
                        for name in names}}
    state = {'porota-preopen-rc6.timer': ('disabled', 'inactive'),
             'porota-runtime-evidence-rc6.timer': ('enabled', 'inactive')}
    mutations = []
    proofs = []

    def fake_run(*args, check=True):
        if args[1] == 'is-enabled':
            return subprocess.CompletedProcess(args, 0, stdout=state[args[2]][0])
        if args[1] == 'is-active':
            return subprocess.CompletedProcess(args, 0, stdout=state[args[2]][1])
        if args[1] == 'enable':
            assert args[-1] != 'porota-preopen-rc6.timer'
            mutations.append(args)
            return subprocess.CompletedProcess(args, 0, stdout='')
        if args[1] in {'daemon-reload', 'unmask'}:
            return subprocess.CompletedProcess(args, 0, stdout='')
        raise AssertionError(args)

    monkeypatch.setattr(host_units, '_run', fake_run)
    monkeypatch.setattr(suspend, 'exact_operator_preopen_pause',
                        lambda: proofs.append('A125') or ('f'*64))
    monkeypatch.setattr(suspend, 'operator_preopen_timer_disabled', lambda: True)
    result = host_units.apply(repo_root, policy, tmp_path,
                              keep_active_timers_paused=True)
    assert proofs == ['A125']
    assert mutations == [('systemctl', 'enable', 'porota-runtime-evidence-rc6.timer')]
    assert result['verification'] == [
        {'unit':'porota-preopen-rc6.timer', 'state':'disabled|inactive'},
        {'unit':'porota-runtime-evidence-rc6.timer', 'state':'enabled|inactive'}]
    with pytest.raises(RuntimeError, match='REARM_FORBIDDEN'):
        host_units.apply(repo_root, policy, tmp_path,
                         keep_active_timers_paused=False)
    assert mutations == [('systemctl', 'enable', 'porota-runtime-evidence-rc6.timer')]


def test_deploy_preopen_timer_check_requires_matching_a125_receipt(monkeypatch, tmp_path):
    units = sorted(set(rc6_preopen.REQUIRED_TIMERS) |
                   {f'porota-a131-fixture-{i:02d}-rc6.timer' for i in range(11)})
    assert len(units) == 16
    raw = json.dumps({'units': {'systemd/' + name: {
        'role': 'ACTIVE_TIMER', 'active': True} for name in units}}).encode()
    policy = tmp_path/'ops/policy/host-control-plane-reconciliation-v2.json'
    policy.parent.mkdir(parents=True)
    policy.write_bytes(raw)
    candidate = 'a' * 40
    receipt = tmp_path/'data/deploy'/('scheduler-suspend-' + candidate + '.json')
    receipt.parent.mkdir(parents=True)
    receipt_data = {'candidate_sha': candidate, 'status': 'SUSPENDED',
                    'policy_sha256': hashlib.sha256(raw).hexdigest(),
                    'timers': units, 'attempted': units, 'ppi_before': [],
                    'operator_pause_timer': 'porota-preopen-rc6.timer',
                    'operator_pause_receipt_sha256': 'f'*64}
    write_json(receipt, receipt_data)
    monkeypatch.setattr(rc6_preopen, 'ROOT', tmp_path)
    monkeypatch.setattr(suspend, 'ppi_snapshot', lambda: [])
    monkeypatch.setattr(suspend, 'exact_operator_preopen_pause', lambda: 'f'*64)
    monkeypatch.setattr(suspend, 'operator_preopen_timer_disabled', lambda: True)

    def cmd(args, timeout=20):
        unit = args[2]
        if args[1] == 'is-enabled':
            return ((1, 'disabled', '') if unit == 'porota-preopen-rc6.timer'
                    else (0, 'enabled', ''))
        if args[1] == 'is-active':
            return 3, 'inactive', ''
        if args[1] == 'show':
            return 0, unit.removesuffix('.timer') + '.service', ''
        raise AssertionError(args)

    monkeypatch.setattr(rc6_preopen, 'cmd', cmd)
    result = rc6_preopen.required_timers(candidate)
    assert result['state'] == 'GREEN'
    assert result['operator_paper_paused'] is True
    assert result['operator_pause_receipt_sha256'] == 'f'*64
    assert result['units']['porota-preopen-rc6.timer']['enabled'] == 'disabled'
    receipt_data['operator_pause_receipt_sha256'] = '0'*64
    write_json(receipt, receipt_data)
    assert rc6_preopen.required_timers(candidate)['state'] == 'RED'
