"""Admission, drain races and recovery boundary without a live Docker host."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from scripts import rc6_deploy_quiescence as q

SHA = 'a' * 40


@pytest.fixture
def installation(tmp_path, monkeypatch):
    monkeypatch.setattr(q, 'ROOT', tmp_path)
    (tmp_path / 'data/paper_v17').mkdir(parents=True)
    db = tmp_path / 'data/paper_v17/observer_v17.db'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE observer_state(id,mode,real_orders_sent,process_state,session_state,heartbeat_at)')
        c.execute('INSERT INTO observer_state VALUES(1,?,?,?,?,?)',
                  ('PRODUCTION_PAPER', 0, 'WAITING_MARKET', 'MARKET_CLOSED', datetime.now(timezone.utc).isoformat()))
        for table in ('paper_positions', 'paper_cauciones', 'paper_future_positions'):
            c.execute('CREATE TABLE ' + table + '(status)')
    states = {name: {'Id': name + '_id', 'Image': 'old_image',
                    'State': {'Status': 'running', 'OOMKilled': False, 'ExitCode': 0}} for name in q.OWNED}
    calls = []
    def command(args):
        calls.append(args)
        if args[1] == 'inspect': return json.dumps([states[args[2]]])
        if args[1] == 'stop':
            states[args[-1]]['State']['Status'] = 'exited'
            if args[-1] == q.OWNED[0]:
                with sqlite3.connect(db) as c: c.execute("UPDATE observer_state SET process_state='STOPPED'")
        if args[1] == 'start': states[args[-1]]['State']['Status'] = 'running'
        return ''
    monkeypatch.setattr(q, 'command', command)
    return tmp_path, db, states, calls, command


@pytest.mark.parametrize('change', ['open_spot', 'active_future', 'missing_ledger', 'real', 'open_session', 'old_heartbeat'])
def test_unsafe_or_unknown_cannot_stop_runtime(installation, change):
    root, db, states, calls, _ = installation
    with sqlite3.connect(db) as c:
        if change == 'open_spot': c.execute("INSERT INTO paper_positions VALUES('OPEN')")
        elif change == 'active_future': c.execute("INSERT INTO paper_future_positions VALUES('ACTIVE')")
        elif change == 'missing_ledger': c.execute('DROP TABLE paper_cauciones')
        elif change == 'real': c.execute('UPDATE observer_state SET real_orders_sent=1')
        elif change == 'open_session': c.execute("UPDATE observer_state SET session_state='MARKET_OPEN'")
        else: c.execute("UPDATE observer_state SET heartbeat_at='2020-01-01T00:00:00+00:00'")
    with pytest.raises((ValueError, sqlite3.Error)): q.run(SHA, 'drain')
    assert calls == []


def test_drain_proves_exited_then_rechecks_ledgers_before_install(installation):
    root, db, states, calls, _ = installation
    receipt = q.run(SHA, 'drain')
    assert receipt['status'] == 'DRAINED' and receipt['inflight'] == 'ABSENT_OWNED_RUNTIME_STOPPED'
    with sqlite3.connect(db) as c: c.execute("INSERT INTO paper_positions VALUES('OPEN')")
    with pytest.raises(ValueError, match='OPEN_POSITION'): q.run(SHA, 'install')
    assert q.run(SHA, 'recover')['status'] == 'ORIGINAL_RUNTIME_RESUMED'
    assert all(v['State']['Status'] == 'running' for v in states.values())


def test_docker_stop_timeout_after_effect_is_recoverable(installation, monkeypatch):
    root, db, states, calls, command = installation
    def timeout_after_stop(args):
        result = command(args)
        if args[1] == 'stop': raise TimeoutError('client timeout after daemon stop')
        return result
    monkeypatch.setattr(q, 'command', timeout_after_stop)
    with pytest.raises(TimeoutError): q.run(SHA, 'drain')
    assert q.run(SHA, 'recover')['status'] == 'ORIGINAL_RUNTIME_RESUMED'
    assert all(v['State']['Status'] == 'running' for v in states.values())


def test_recovery_never_restarts_old_runtime_after_install_boundary(installation):
    root, db, states, calls, _ = installation
    q.run(SHA, 'drain'); q.run(SHA, 'install')
    calls.clear()
    assert q.run(SHA, 'recover')['status'] == 'FIX_FORWARD_VALIDATION_REQUIRED'
    assert calls == []


def test_restart_between_drain_and_install_blocks_candidate_write(installation):
    root, db, states, calls, _ = installation
    q.run(SHA, 'drain')
    states[q.OWNED[0]]['State']['Status'] = 'running'
    with pytest.raises(ValueError, match='CHANGED_BEFORE_INSTALL'): q.run(SHA, 'install')


def test_workflow_drains_before_transfer_and_marks_before_bundle_install():
    source = Path('.github/workflows/porota-deploy-v2-promote.yml').read_text()
    assert source.index('id: drain') < source.index('id: transfer')
    assert source.index('--action install') < source.index('shutil.copy2(src,dst)')
    assert '--action recover' in source


def test_preexisting_historical_oom_flag_with_new_clean_exit_is_not_a_new_oom(installation, monkeypatch):
    root, db, states, calls, command = installation
    state = states[q.OWNED[0]]['State']
    state.update(OOMKilled=True, StartedAt='2026-10-16T04:58:27+00:00',
                 FinishedAt='2026-10-16T04:15:23+00:00')
    def retained_flag(args):
        value = command(args)
        if args[1] == 'stop': states[args[-1]]['State']['FinishedAt'] = datetime.now(timezone.utc).isoformat()
        return value
    monkeypatch.setattr(q, 'command', retained_flag)
    value = q.run(SHA, 'drain')
    assert value['status'] == 'DRAINED'
    assert value['stop_evidence'][q.OWNED[0]]['classification'] == 'INHERITED_OOM_FLAG_CLEAN_EXIT'


@pytest.mark.parametrize('change', ['new_oom', 'nonzero', 'unknown_prior', 'restarted'])
def test_new_or_unknown_stop_failure_remains_blocked_with_receipt(installation, monkeypatch, change):
    root, db, states, calls, command = installation
    def unsafe(args):
        value = command(args)
        if args[1] == 'stop':
            state = states[args[-1]]['State']
            state.update(StartedAt='2026-10-16T04:58:27+00:00', FinishedAt=datetime.now(timezone.utc).isoformat())
            if change in {'new_oom', 'unknown_prior'}: state['OOMKilled'] = True
            elif change == 'nonzero': state['ExitCode'] = 137
            else: states[args[-1]]['Id'] = 'changed'
        return value
    if change == 'unknown_prior': states[q.OWNED[0]]['State']['OOMKilled'] = True
    monkeypatch.setattr(q, 'command', unsafe)
    with pytest.raises(ValueError, match='QUIESCENCE_STOP_'): q.run(SHA, 'drain')
    receipt = json.loads((root / 'data/deploy' / ('quiescence-' + SHA + '.json')).read_text())
    assert receipt['stop_evidence'][q.OWNED[0]]['after']['ExitCode'] == (137 if change == 'nonzero' else 0)
    assert not receipt['installation_started']


@pytest.mark.parametrize('status,allow,expected', [
    ('FIX_FORWARD_VALIDATION_REQUIRED',False,2),
    ('FIX_FORWARD_VALIDATION_REQUIRED',True,0),
    ('RECOVERY_UNVERIFIED',True,2),
    ('ORIGINAL_RUNTIME_RESUMED',True,0),
])
def test_failure_cleanup_classifies_pending_installation_without_hiding_recovery_errors(monkeypatch,status,allow,expected):
    monkeypatch.setattr(q,'run',lambda *args:{'status':status})
    args=['--candidate-sha',SHA,'--action','recover']
    if allow:args.append('--allow-validation-pending')
    assert q.main(args)==expected


def test_pending_validation_option_is_for_failure_recovery_only(monkeypatch):
    calls=[];monkeypatch.setattr(q,'run',lambda *args:calls.append(args))
    with pytest.raises(SystemExit):
        q.main(['--candidate-sha',SHA,'--action','install','--allow-validation-pending'])
    assert calls==[]


def test_failure_cleanup_wires_explicit_pending_classification_and_keeps_failure_condition():
    source=(Path(__file__).resolve().parents[1]/'.github/workflows/porota-deploy-v2-promote.yml').read_text()
    block=source.split('- name: Failure-safe host cleanup',1)[1].split('- name:',1)[0]
    assert "steps.promotion.outcome == 'failure'" in block
    assert '--action recover --allow-validation-pending' in block
    assert 'original_failure_preserved=true' in block
    assert 'rc6_deploy_failure_cleanup.py' in block and 'test "$RECOVERY_RC" -eq 0' in block
