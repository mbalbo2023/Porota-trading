"""Fail-closed A207 recovery: a running unvalidated image never rearms timers."""

import json
from pathlib import Path
import sqlite3
import base64
import hashlib

import pytest

from scripts import rc6_deploy_scheduler_suspend as scheduler
from scripts import rc6_deploy_host_mutex as mutex

CANDIDATE = 'a' * 40


def _fixture(tmp_path, monkeypatch, *, status='ADMIN_WRITE_AUTHORIZED'):
    root = tmp_path / 'data' / 'deploy'
    root.mkdir(parents=True)
    monkeypatch.setattr(scheduler, 'ROOT', root)
    db_path = tmp_path / 'data' / 'paper_v17' / 'observer_v17.db'
    db_path.parent.mkdir()
    with sqlite3.connect(db_path) as c:
        c.execute('CREATE TABLE observer_state(id INTEGER,mode TEXT,real_orders_sent INTEGER)')
        c.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0)")
    receipt = {
        'candidate_sha': CANDIDATE,
        'admin_policy': 'A207_ALL_PAPER_REVERSAL_V1',
        'a207_approval_candidate': CANDIDATE,
        'a207_approval_tree': 'b' * 40,
        'a207_owner_approval': '6531975083',
        'status': status,
        'installation_started': status != 'ADMIN_DRAINED_PENDING',
        'real_orders_sent': 0,
        'ppi_watch_accessed': False,
        'admin_cancel_status': ('A207_ATOMIC_ACCOUNTING_REVERSED'
                                if status == 'DRAINED' else None),
    }
    path = root / ('quiescence-' + CANDIDATE + '.json')
    path.write_text(json.dumps(receipt))
    return path, db_path


@pytest.mark.parametrize('mode', ['running', 'exited'])
@pytest.mark.parametrize('status', ['ADMIN_DRAINED_PENDING', 'ADMIN_WRITE_AUTHORIZED',
                                    'DRAINED', 'FIX_FORWARD_VALIDATION_REQUIRED'])
def test_a207_hold_never_mutates_or_rearms_unvalidated_runtime(tmp_path,
                                                                monkeypatch, mode, status):
    _fixture(tmp_path, monkeypatch, status=status)
    seen = []

    def read_only(*command):
        seen.append(command)
        assert command[:3] == ('docker', 'inspect', '-f')
        return mode, 0

    monkeypatch.setattr(scheduler, 'command', read_only)
    assert scheduler.require_a207_failure_hold(CANDIDATE) == 'A207_FAILED_UNVALIDATED_TIMERS_HELD'
    assert len(seen) == 2


@pytest.mark.parametrize('tamper', ['a207_owner_approval', 'a207_approval_tree',
                                    'real_orders_sent', 'status'])
def test_a207_failure_hold_rejects_unsealed_proof(tmp_path, monkeypatch, tamper):
    path, _ = _fixture(tmp_path, monkeypatch)
    receipt = json.loads(path.read_text())
    receipt[tamper] = {'a207_owner_approval': 'not-a-comment',
                       'a207_approval_tree': 'invalid-tree',
                       'real_orders_sent': 1,
                       'status': 'UNAUTHORIZED_RESTART'}[tamper]
    path.write_text(json.dumps(receipt))
    monkeypatch.setattr(scheduler, 'command', lambda *args: ('running', 0))
    with pytest.raises(ValueError, match='SCHEDULER_A207_'):
        scheduler.require_a207_failure_hold(CANDIDATE)


def test_a207_failure_hold_rejects_live_order_counter(tmp_path, monkeypatch):
    _, db = _fixture(tmp_path, monkeypatch)
    with sqlite3.connect(db) as c:
        c.execute('UPDATE observer_state SET real_orders_sent=1')
    monkeypatch.setattr(scheduler, 'command', lambda *args: ('running', 0))
    with pytest.raises(ValueError, match='SCHEDULER_A207_PAPER_ZERO_CHANGED'):
        scheduler.require_a207_failure_hold(CANDIDATE)


def test_a207_workflow_never_falls_through_to_generic_restore():
    workflow = (Path(__file__).resolve().parents[1] / '.github' / 'workflows' /
                'porota-deploy-v2-promote.yml').read_text()
    recovery = workflow.split('SCHEDULER_ACTION=restore', 1)[1].split("A143_ENV=''", 1)[0]
    assert 'OPEN_ADMIN_A207' in recovery and 'CLOSED_ADMIN_A207' in recovery
    assert 'SCHEDULER_ACTION=recover-a207-pending' in recovery
    scheduler_source = Path(scheduler.__file__).read_text()
    assert "action == 'recover-a207-pending'" in scheduler_source
    assert 'require_a207_failure_hold(candidate)' in scheduler_source
    assert 'cleanup_admin_resume' in mutex.PHASES


@pytest.mark.parametrize('durable_status', ['RESTORED', 'SUSPENDED'])
def test_failed_after_or_during_timers_restored_repauses_without_runtime_restart(
        tmp_path, monkeypatch, durable_status):
    _, _ = _fixture(tmp_path, monkeypatch, status='FIX_FORWARD_VALIDATION_REQUIRED')
    policy = b'{"test":"a207-rehold"}'
    name = 'porota-a207-test.timer'
    marker = scheduler.ROOT / ('scheduler-suspend-' + CANDIDATE + '.json')
    marker.write_text(json.dumps({
        'candidate_sha': CANDIDATE,
        'policy_sha256': hashlib.sha256(policy).hexdigest(),
        'timers': [name], 'preserved_iol_timer': None,
        'ppi_before': {'state':'unchanged'},
        'attempted': [name], 'status': durable_status,
    }))
    monkeypatch.setattr(scheduler,'timer_policy',lambda _: [name])
    monkeypatch.setattr(scheduler,'preserved_iol_guard',lambda _: None)
    monkeypatch.setattr(scheduler,'ppi_snapshot',lambda: {'state':'unchanged'})
    state = {'timer':'active'}
    calls = []

    def command(*args):
        calls.append(args)
        if args[:3] == ('docker','inspect','-f'):
            return 'running', 0
        if args[:2] == ('systemctl','is-active'):
            return state['timer'] if args[2] == name else 'inactive', 0
        if args[:2] == ('systemctl','is-enabled'):
            return 'enabled', 0
        if args[:2] == ('systemctl','show'):
            return name.replace('.timer','.service'), 0
        if args[:2] == ('systemctl','stop'):
            assert args[2] == name
            state['timer'] = 'inactive'
            return '', 0
        raise AssertionError('unexpected command: ' + repr(args))

    monkeypatch.setattr(scheduler,'command',command)
    result=scheduler.run(CANDIDATE,'recover-a207-pending',base64.b64encode(policy).decode())
    assert result['status'] == 'SUSPENDED'
    assert state['timer'] == 'inactive'
    assert sum(args[:2] == ('systemctl','stop') for args in calls) == 1
    assert not any(args[:2] in {('systemctl','start'), ('docker','start'),
                                  ('docker','stop')} for args in calls)
