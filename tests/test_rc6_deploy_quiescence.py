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
    states['porota_critical_approval_rc6'] = {'Id': 'critical_id', 'Image': 'old_image',
        'RestartCount': 0,
        'State': {'Status': 'running', 'OOMKilled': False, 'ExitCode': 0,
                  'Health': {'Status': 'healthy'}}}
    calls = []
    def command(args):
        calls.append(args)
        if args[1] == 'inspect': return json.dumps([states[args[2]]])
        if args[1] == 'exec': return 'RC6_CRITICAL_ISSUES_ONLY=GREEN\n'
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
    # A read-only Docker inspect may distinguish a prior stopped receipt from
    # a running scanner; an unsafe state must never stop/start either service.
    assert not any(call[1] in ('stop', 'start') for call in calls)


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


def test_exact_failed_install_continues_stopped_without_starting_prior_image(installation,monkeypatch):
    root,db,states,calls,_=installation
    monkeypatch.setattr(q,'FAILED_INSTALL_CANDIDATE',SHA)
    q.run(SHA,'drain');q.run(SHA,'install');q.run(SHA,'recover')
    calls.clear()
    successor='b'*40
    receipt=q.run(successor,'drain')
    assert receipt['status']=='DRAINED' and receipt['fix_forward_from']==SHA
    assert receipt['inflight']=='ABSENT_OWNED_RUNTIME_STOPPED'
    assert len(receipt['prior_receipt_sha256'])==64
    assert not any(c[1] in ('start','stop') for c in calls)
    assert all(states[n]['State']['Status']=='exited' for n in q.OWNED)
    assert q.run(successor,'install')['status']=='HOST_INSTALLATION_STARTED'


def test_stopped_successor_accepts_independent_healthy_issues_broker(installation, monkeypatch):
    root, db, states, calls, _ = installation
    monkeypatch.setattr(q, 'FAILED_INSTALL_CANDIDATE', SHA)
    q.run(SHA, 'drain'); q.run(SHA, 'install'); q.run(SHA, 'recover')
    states['porota_critical_approval_rc6']['Image'] = 'independent_critical_image'
    calls.clear()
    receipt = q.run('b' * 40, 'drain')
    assert receipt['critical_admission'] == {
        'image': 'independent_critical_image', 'status': 'healthy',
        'restart_count': 0, 'capability': 'issues_only'}
    assert any(c[1] == 'exec' for c in calls)
    assert not any(c[1] in ('start', 'stop') for c in calls)


@pytest.mark.parametrize('change', ['none', 'admin_receipt', 'sale', 'reopened'])
def test_failed_cold2_continues_closed_admin_ledger_without_repeat_cancel(
        installation, monkeypatch, change):
    """Run 39370186360 closed SUPV, then failed COLD2 before Docker restart."""
    root, db, states, calls, _ = installation
    monkeypatch.setattr(q, 'FAILED_COLD2_CANDIDATE', SHA)
    q.run(SHA, 'drain'); q.run(SHA, 'install'); q.run(SHA, 'recover')
    paper_id = 'PAPER-observed-issue624'
    with sqlite3.connect(db) as c:
        for column in ('paper_id TEXT', 'close_reason TEXT', 'exit_price REAL', 'net_pnl REAL'):
            c.execute('ALTER TABLE paper_positions ADD COLUMN '+column)
        c.execute('CREATE TABLE paper_admin_cancellations(paper_id TEXT,authority TEXT)')
        c.execute('CREATE TABLE paper_fills(paper_id TEXT,side TEXT)')
        c.execute('''INSERT INTO paper_positions(status,paper_id,close_reason)
                     VALUES (?,?,?)''', ('OPEN' if change == 'reopened' else 'CANCELLED_ADMIN',
                                         paper_id, q.ADMIN_CANCEL_REASON))
        if change == 'reopened':
            c.execute("INSERT INTO paper_positions(status,paper_id) VALUES ('OPEN','PAPER-other')")
        c.execute('INSERT INTO paper_admin_cancellations VALUES (?,?)',
                  (paper_id, 'ISSUE624_OPERATOR_REQUEST_20261019'))
        if change == 'sale':
            c.execute("INSERT INTO paper_fills VALUES (?,'SELL_SIMULATED')", (paper_id,))
        c.execute("UPDATE observer_state SET process_state='WAITING_MARKET'")
    prior_path = root/'data/deploy'/('quiescence-'+SHA+'.json')
    prior = json.loads(prior_path.read_text())
    prior.update(admin_cancel_paper_id=paper_id,
                 admin_cancel_status='UNKNOWN' if change == 'admin_receipt'
                 else 'APPLIED_NO_MARKET_SALE')
    prior_path.write_text(json.dumps(prior))
    calls.clear()
    successor = 'b'*40
    if change != 'none':
        with pytest.raises((ValueError, sqlite3.Error)):
            q.run(successor, 'drain', allow_admin_issue624=True)
        assert not (root/'data/deploy'/('quiescence-'+successor+'.json')).exists()
    else:
        receipt = q.run(successor, 'drain', allow_admin_issue624=True)
        assert receipt['status'] == 'DRAINED'
        assert receipt['admin_cancel_paper_id'] is None
        assert receipt['fix_forward_from'] == SHA
        assert q.run(successor, 'install')['status'] == 'HOST_INSTALLATION_STARTED'
    assert not any(c[1] in ('start', 'stop') for c in calls)


@pytest.mark.parametrize('change', ['restarts', 'missing_restarts', 'capability'])
def test_stopped_successor_rejects_unhealthy_independent_broker(
        installation, monkeypatch, change):
    root, db, states, calls, original_command = installation
    monkeypatch.setattr(q, 'FAILED_INSTALL_CANDIDATE', SHA)
    q.run(SHA, 'drain'); q.run(SHA, 'install'); q.run(SHA, 'recover')
    states['porota_critical_approval_rc6']['Image'] = 'independent_critical_image'
    if change == 'restarts':
        states['porota_critical_approval_rc6']['RestartCount'] = 1
    elif change == 'missing_restarts':
        del states['porota_critical_approval_rc6']['RestartCount']
    else:
        def no_issues_capability(args):
            if args[1] == 'exec': return 'capability=orders\n'
            return original_command(args)
        monkeypatch.setattr(q, 'command', no_issues_capability)
    calls.clear()
    with pytest.raises(ValueError, match='QUIESCENCE_CRITICAL_'):
        q.run('b' * 40, 'drain')
    assert not (root / 'data/deploy' / ('quiescence-' + 'b' * 40 + '.json')).exists()
    assert not any(c[1] in ('start', 'stop') for c in calls)


@pytest.mark.parametrize('change',['prior_receipt','image','exit_code','critical','position','mixed'])
def test_failed_install_continuation_rejects_changed_proof(installation,monkeypatch,change):
    root,db,states,calls,_=installation
    monkeypatch.setattr(q,'FAILED_INSTALL_CANDIDATE',SHA)
    q.run(SHA,'drain');q.run(SHA,'install');q.run(SHA,'recover')
    old=root/'data/deploy'/('quiescence-'+SHA+'.json')
    if change=='prior_receipt':
        receipt=json.loads(old.read_text());receipt['inflight']='UNKNOWN';old.write_text(json.dumps(receipt))
    elif change=='image':states[q.OWNED[0]]['Image']='changed'
    elif change=='exit_code':states[q.OWNED[0]]['State']['ExitCode']=137
    elif change=='critical':states['porota_critical_approval_rc6']['State']['Health']['Status']='unhealthy'
    elif change=='position':
        with sqlite3.connect(db) as c:c.execute("INSERT INTO paper_positions VALUES('OPEN')")
    else:states[q.OWNED[0]]['State']['Status']='running'
    calls.clear()
    with pytest.raises((ValueError,sqlite3.Error)):q.run('b'*40,'drain')
    assert not (root/'data/deploy'/('quiescence-'+'b'*40+'.json')).exists()
    assert not any(c[1] in ('start','stop') for c in calls)


def test_restart_between_drain_and_install_blocks_candidate_write(installation):
    root, db, states, calls, _ = installation
    q.run(SHA, 'drain')
    states[q.OWNED[0]]['State']['Status'] = 'running'
    with pytest.raises(ValueError, match='CHANGED_BEFORE_INSTALL'): q.run(SHA, 'install')


def test_workflow_drains_before_transfer_and_marks_before_bundle_install():
    source = Path('.github/workflows/porota-deploy-v2-promote.yml').read_text()
    assert source.index('id: drain') < source.index('id: transfer')
    assert source.index('--action install') < source.index('shutil.copy2(src,dst)')
    assert source.index('RC6_STAGED_BUNDLE_HASHES=GREEN') < source.index('--action admin-begin')
    assert source.index('--action admin-begin') < source.index('--apply') < source.index('--action admin-commit') < source.index('--action install')
    assert '--action recover' in source
    assert source.index('< scripts/rc6_offline_current_image_cleanup.py') < source.index("<<'PRECLEAN_REMOTE'")
    assert '< scripts/rc6_offline_failed_image_cleanup.py' not in source
    assert source.index('RC6_DISK_OFFLINE_PRECLEAN_INSUFFICIENT=RED') < source.index('id: drain')


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


def test_admin_resource_admission_waits_for_two_stable_samples(capsys):
    now = [0]
    samples = iter([(140 * 1024 * 1024, 21.09),
                    (140 * 1024 * 1024, 4.0),
                    (140 * 1024 * 1024, 6.0),
                    (140 * 1024 * 1024, 3.0),
                    (140 * 1024 * 1024, 2.0)])
    result = q.wait_for_admin_resources(sample=lambda: next(samples),
        clock=lambda: now[0], pause=lambda seconds: now.__setitem__(0, now[0] + seconds))
    assert result == (140 * 1024 * 1024, 2.0)
    assert now[0] == 20
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [event['consecutive_green'] for event in events] == [0, 1, 0, 1, 2]


@pytest.mark.parametrize('available,pressure', [(127 * 1024 * 1024, 1.0),
                                                 (140 * 1024 * 1024, 20.0)])
def test_admin_resource_admission_persistent_red_expires_without_relaxing_gate(
        available, pressure, capsys):
    now = [0]
    with pytest.raises(ValueError, match='QUIESCENCE_ADMIN_RESOURCE_ADMISSION_RED'):
        q.wait_for_admin_resources(sample=lambda: (available, pressure),
            clock=lambda: now[0], pause=lambda seconds: now.__setitem__(0, now[0] + seconds))
    assert now[0] == 90
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert all(not event['admitted'] for event in events)
    assert events[-1]['mem_available_bytes'] == available
    assert events[-1]['io_psi_avg10'] == pressure
