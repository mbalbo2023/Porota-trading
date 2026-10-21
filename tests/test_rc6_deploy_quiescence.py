"""Admission, drain races and recovery boundary without a live Docker host."""
from datetime import datetime, timedelta, timezone
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


def test_intraday_operator_suspend_requires_live_paper_and_stopped_ack(installation):
    root, db, states, calls, _ = installation
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET process_state='RUNNING',session_state='MARKET_OPEN'")
    receipt = q.run(SHA, 'drain', allow_market_open_suspend=True)
    assert receipt['market_open_suspended'] is True
    assert receipt['inflight'] == 'ABSENT_OWNED_RUNTIME_STOPPED'
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT session_state,process_state FROM observer_state').fetchone() == (
            'MARKET_OPEN', 'STOPPED')
    with pytest.raises(ValueError, match='INSTALL_SESSION_MODE_CHANGED'):
        q.run(SHA, 'install')
    assert q.run(SHA, 'install', allow_market_open_suspend=True)['installation_started'] is True


def test_paused_postclose_admits_only_exact_stopped_paper_caucion(installation, monkeypatch):
    root, db, states, calls, _ = installation
    class Postclose(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 20, 17, 40, tzinfo=tz)
    monkeypatch.setattr(q, 'datetime', Postclose)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET process_state='STOPPED',session_state='MARKET_OPEN'")
        c.execute('DROP TABLE paper_cauciones')
        c.execute('CREATE TABLE paper_cauciones(paper_id TEXT,status TEXT,maturity_at TEXT,principal TEXT)')
        c.execute("INSERT INTO paper_cauciones VALUES('overnight','OPEN','2026-10-21T17:00:00-03:00','100')")
    with pytest.raises(ValueError, match='QUIESCENCE_OPEN_POSITION'):
        q.admit(stopped=True, allow_market_open_suspend=True,
                allow_open_paper_carryover=True, stopped_receipt={})
    assert q.admit(stopped=True, allow_market_open_suspend=True,
                   allow_open_paper_carryover=True, allow_postclose_pending=True,
                   stopped_receipt={}) == 'STOPPED'
    caucion=q.pending_caucion_snapshot()
    assert caucion['open_count']==1 and caucion['maturity_at']=='2026-10-21T17:00:00-03:00'
    with sqlite3.connect(db) as c:
        c.execute("UPDATE paper_cauciones SET principal='999'")
    assert q.pending_caucion_snapshot()['sha256'] != caucion['sha256']
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO paper_future_positions VALUES('ACTIVE')")
    with pytest.raises(ValueError, match='QUIESCENCE_OPEN_POSITION'):
        q.admit(stopped=True, allow_market_open_suspend=True,
                allow_open_paper_carryover=True, allow_postclose_pending=True,
                stopped_receipt={})


@pytest.fixture
def carry_installation(installation):
    root, db, states, calls, command = installation
    with sqlite3.connect(db) as c:
        c.execute('DROP TABLE paper_positions')
        c.execute('CREATE TABLE paper_positions(paper_id TEXT PRIMARY KEY,status TEXT,'
                  'symbol TEXT,quantity REAL,entry_price REAL,opened_at TEXT)')
        c.execute('CREATE TABLE paper_fills(id INTEGER PRIMARY KEY,paper_id TEXT,side TEXT)')
        c.execute('CREATE TABLE paper_exit_intents(paper_id TEXT,state TEXT)')
        for i in range(4):
            c.execute('INSERT INTO paper_positions VALUES(?,?,?,?,?,?)',
                      (f"paper-{i}", 'OPEN', f"SYM{i}", 1.0, 10.0, '2026-10-20T13:00:00Z'))
            c.execute("INSERT INTO paper_fills(paper_id,side) VALUES(?,'BUY_SIMULATED')", (f"paper-{i}",))
        c.execute("UPDATE observer_state SET process_state='RUNNING',session_state='MARKET_OPEN'")
    return installation


def test_running_postclose_drains_exact_paper_carryover_and_caucion(carry_installation, monkeypatch):
    root, db, states, calls, _ = carry_installation
    class Postclose(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz == timezone.utc:
                return datetime.now(timezone.utc)
            return datetime(2026, 10, 20, 19, 55, tzinfo=tz)
    monkeypatch.setattr(q, 'datetime', Postclose)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET process_state='WAITING_MARKET',session_state='MARKET_CLOSED'")
        c.execute("DELETE FROM paper_positions WHERE paper_id IN ('paper-2','paper-3')")
        c.execute('DROP TABLE paper_cauciones')
        c.execute('CREATE TABLE paper_cauciones(paper_id TEXT,status TEXT,maturity_at TEXT,principal TEXT)')
        c.execute("INSERT INTO paper_cauciones VALUES('overnight','OPEN','2026-10-21T17:00:00-03:00','100')")
    flags = dict(allow_market_open_suspend=True, allow_open_paper_carryover=True,
                 allow_postclose_pending=True)
    receipt = q.run(SHA, 'drain', **flags)
    assert receipt['status'] == 'DRAINED' and receipt['paused_postclose'] is True
    assert receipt['spot_carryover']['open_count'] == 2
    assert receipt['caucion_carryover']['open_count'] == 1
    assert {states[name]['State']['Status'] for name in q.OWNED} == {'exited'}
    with sqlite3.connect(db) as c:
        c.execute("UPDATE paper_cauciones SET principal='999'")
    with pytest.raises(ValueError, match='INSTALL_CAUCION_CHANGED'):
        q.run(SHA, 'install', **flags)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE paper_cauciones SET principal='100'")
    assert q.run(SHA, 'install', **flags)['installation_started'] is True


def test_running_postclose_stale_db_ack_requires_exact_clean_stop(carry_installation, monkeypatch):
    root, db, states, calls, command = carry_installation
    class Postclose(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz == timezone.utc:
                return datetime.now(timezone.utc)
            return datetime(2026, 10, 20, 19, 55, tzinfo=tz)
    monkeypatch.setattr(q, 'datetime', Postclose)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET process_state='WAITING_MARKET',session_state='MARKET_CLOSED'")
        c.execute("DELETE FROM paper_positions WHERE paper_id IN ('paper-2','paper-3')")
        c.execute('DROP TABLE paper_cauciones')
        c.execute('CREATE TABLE paper_cauciones(paper_id TEXT,status TEXT,maturity_at TEXT)')
        c.execute("INSERT INTO paper_cauciones VALUES('overnight','OPEN','2026-10-21T17:00:00-03:00')")
    def no_ack(args):
        result = command(args)
        if args[1] == 'stop' and args[-1] == q.OWNED[0]:
            with sqlite3.connect(db) as c:
                c.execute("UPDATE observer_state SET process_state='WAITING_MARKET'")
        return result
    monkeypatch.setattr(q, 'command', no_ack)
    flags = dict(allow_market_open_suspend=True, allow_open_paper_carryover=True,
                 allow_postclose_pending=True)
    assert q.run(SHA, 'drain', **flags)['status'] == 'DRAINED'
    states[q.OWNED[0]]['State']['Status'] = 'running'
    with pytest.raises(ValueError, match='STOP_PROOF_CHANGED'):
        q.run(SHA, 'drain', **flags)


def test_postclose_failed_install_adopts_exact_validated_stopped_runtime(carry_installation, monkeypatch):
    root, db, states, calls, _ = carry_installation
    class Postclose(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz == timezone.utc:return datetime.now(timezone.utc)
            return datetime(2026, 10, 20, 20, 45, tzinfo=tz)
    monkeypatch.setattr(q, 'datetime', Postclose)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET process_state='WAITING_MARKET',session_state='MARKET_CLOSED'")
        c.execute("DELETE FROM paper_positions WHERE paper_id IN ('paper-2','paper-3')")
        c.execute('DROP TABLE paper_cauciones')
        c.execute('CREATE TABLE paper_cauciones(paper_id TEXT,status TEXT,maturity_at TEXT,principal TEXT)')
        c.execute("INSERT INTO paper_cauciones VALUES('overnight','OPEN','2026-10-21T17:00:00-03:00','100')")
    flags = dict(allow_market_open_suspend=True, allow_open_paper_carryover=True,
                 allow_postclose_pending=True)
    first=q.run(SHA,'drain',**flags)
    q.run(SHA,'install',**flags)
    q.run(SHA,'recover')
    (root/'data/deploy/CURRENT_STATE_V2.json').write_text(json.dumps({
        'candidate_sha':'9'*40,'image_id':'old_image',
        'recorded_at':(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat(),
        'validation_status':'VALIDATED_RUNTIME','mode':'PRODUCTION_PAPER',
        'real_orders_sent':0,'real_order_routes':'NOT_CALLED'}))
    calls.clear()
    successor='b'*40
    prior_path=root/'data/deploy'/('quiescence-'+SHA+'.json')
    original=prior_path.read_bytes()
    missing=json.loads(original)
    missing['postclose_live_closed']=False
    prior_path.write_text(json.dumps(missing))
    with pytest.raises(ValueError,match='PRIOR_POSTCLOSE_PROOF_MISSING'):
        q.run(successor,'drain',**flags)
    prior_path.write_bytes(original)
    with sqlite3.connect(db) as c:c.execute("UPDATE paper_cauciones SET principal='999'")
    with pytest.raises(ValueError,match='PRIOR_CAUCION_CHANGED'):
        q.run(successor,'drain',**flags)
    with sqlite3.connect(db) as c:c.execute("UPDATE paper_cauciones SET principal='100'")
    adopted=q.run(successor,'drain',**flags)
    assert adopted['fix_forward_from']==SHA and adopted['paused_postclose'] is True
    assert adopted['postclose_live_closed'] is True
    assert adopted['spot_carryover']==first['spot_carryover']
    assert adopted['caucion_carryover']==first['caucion_carryover']
    assert not any(call[1] in ('stop','start') for call in calls)
    with sqlite3.connect(db) as c:c.execute("UPDATE paper_cauciones SET principal='999'")
    with pytest.raises(ValueError,match='INSTALL_CAUCION_CHANGED'):
        q.run(successor,'install',**flags)


@pytest.mark.parametrize('change', ['open_session', 'stale_heartbeat', 'active_future'])
def test_running_postclose_rejects_unsafe_state_before_stop(carry_installation, monkeypatch, change):
    root, db, states, calls, _ = carry_installation
    class Postclose(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz == timezone.utc:
                return datetime.now(timezone.utc)
            return datetime(2026, 10, 20, 19, 55, tzinfo=tz)
    monkeypatch.setattr(q, 'datetime', Postclose)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET process_state='WAITING_MARKET',session_state='MARKET_CLOSED'")
        if change == 'open_session':
            c.execute("UPDATE observer_state SET session_state='MARKET_OPEN'")
        elif change == 'stale_heartbeat':
            c.execute("UPDATE observer_state SET heartbeat_at='2020-01-01T00:00:00+00:00'")
        else:
            c.execute("INSERT INTO paper_future_positions VALUES('ACTIVE')")
    with pytest.raises(ValueError):
        q.run(SHA, 'drain', allow_market_open_suspend=True,
              allow_open_paper_carryover=True, allow_postclose_pending=True)
    assert not any(call[1] in ('stop', 'start') for call in calls)


def test_intraday_four_paper_spots_carry_across_install_and_restart(carry_installation):
    root, db, states, calls, _ = carry_installation
    receipt = q.run(SHA, 'drain', allow_market_open_suspend=True,
                    allow_open_paper_carryover=True)
    assert receipt['spot_carryover']['open_count'] == 4
    assert receipt['installation_started'] is False
    assert q.run(SHA, 'install', allow_market_open_suspend=True,
                 allow_open_paper_carryover=True)['installation_started'] is True
    assert q.run(SHA, 'verify-carryover')['carryover_verified_count'] == 4
    with sqlite3.connect(db) as c:
        c.execute("UPDATE paper_positions SET status='CLOSED' WHERE paper_id='paper-0'")
        c.execute("INSERT INTO paper_fills(paper_id,side) VALUES('paper-0','SELL_SIMULATED')")
    assert q.run(SHA, 'verify-carryover')['carryover_verified_count'] == 4


def test_intraday_poststop_calendar_open_is_admitted_only_after_live_market_proof(carry_installation, monkeypatch):
    root, db, states, calls, command = carry_installation
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET session_state='OPEN'")
    with pytest.raises(ValueError, match='STATE_REQUIRED:RUNNING:OPEN'):
        q.run(SHA, 'drain', allow_market_open_suspend=True,
              allow_open_paper_carryover=True)
    assert not any(call[1] == 'stop' for call in calls)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET session_state='MARKET_OPEN'")
    def transitional_stop(args):
        result = command(args)
        if args[1] == 'stop' and args[-1] == q.OWNED[0]:
            with sqlite3.connect(db) as c:
                c.execute("UPDATE observer_state SET session_state='OPEN'")
        return result
    monkeypatch.setattr(q, 'command', transitional_stop)
    assert q.run(SHA, 'drain', allow_market_open_suspend=True,
                 allow_open_paper_carryover=True)['status'] == 'DRAINED'
    assert q.run(SHA, 'install', allow_market_open_suspend=True,
                 allow_open_paper_carryover=True)['installation_started'] is True


def test_intraday_clean_docker_stop_admits_stale_running_and_adopts_prepause(carry_installation, monkeypatch):
    root, db, states, calls, command = carry_installation
    def stale_stop(args):
        result = command(args)
        if args[1] == 'stop' and args[-1] == q.OWNED[0]:
            with sqlite3.connect(db) as c:
                c.execute("UPDATE observer_state SET process_state='RUNNING',session_state='MARKET_OPEN'")
        return result
    monkeypatch.setattr(q, 'command', stale_stop)
    receipt = q.run(SHA, 'drain', allow_market_open_suspend=True,
                    allow_open_paper_carryover=True)
    assert receipt['status'] == 'DRAINED'
    assert q.run(SHA, 'drain', allow_market_open_suspend=True,
                 allow_open_paper_carryover=True)['status'] == 'DRAINED'
    assert q.run(SHA, 'install', allow_market_open_suspend=True,
                 allow_open_paper_carryover=True)['installation_started'] is True


def test_intraday_stale_running_requires_exact_clean_stopped_container(carry_installation, monkeypatch):
    root, db, states, calls, command = carry_installation
    def stale_stop(args):
        result = command(args)
        if args[1] == 'stop' and args[-1] == q.OWNED[0]:
            with sqlite3.connect(db) as c: c.execute("UPDATE observer_state SET process_state='RUNNING'")
        return result
    monkeypatch.setattr(q, 'command', stale_stop)
    q.run(SHA, 'drain', allow_market_open_suspend=True, allow_open_paper_carryover=True)
    states[q.OWNED[0]]['State']['Status'] = 'running'
    with pytest.raises(ValueError, match='STOP_PROOF_CHANGED'):
        q.run(SHA, 'drain', allow_market_open_suspend=True,
              allow_open_paper_carryover=True)


def test_intraday_poststop_closed_phase_still_blocks_install(carry_installation, monkeypatch):
    root, db, states, calls, command = carry_installation
    def closed_stop(args):
        result = command(args)
        if args[1] == 'stop' and args[-1] == q.OWNED[0]:
            with sqlite3.connect(db) as c:
                c.execute("UPDATE observer_state SET session_state='MARKET_CLOSED'")
        return result
    monkeypatch.setattr(q, 'command', closed_stop)
    with pytest.raises(ValueError, match='STATE_REQUIRED:STOPPED:MARKET_CLOSED'):
        q.run(SHA, 'drain', allow_market_open_suspend=True,
              allow_open_paper_carryover=True)
    assert q.run(SHA, 'recover')['status'] == 'ORIGINAL_RUNTIME_RESUMED'


def test_intraday_carryover_rejects_ledger_drift_before_install(carry_installation):
    root, db, states, calls, _ = carry_installation
    q.run(SHA, 'drain', allow_market_open_suspend=True, allow_open_paper_carryover=True)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE paper_positions SET quantity=2 WHERE paper_id='paper-1'")
    with pytest.raises(ValueError, match='INSTALL_CARRYOVER_CHANGED'):
        q.run(SHA, 'install', allow_market_open_suspend=True,
              allow_open_paper_carryover=True)
    assert q.run(SHA, 'recover')['status'] == 'ORIGINAL_RUNTIME_RESUMED'


def test_intraday_carryover_requires_exact_mode_and_retains_future_guard(carry_installation):
    root, db, states, calls, _ = carry_installation
    with pytest.raises(ValueError, match='OPEN_POSITION'):
        q.run(SHA, 'drain', allow_market_open_suspend=True)
    assert not any(call[1] == 'stop' for call in calls)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO paper_future_positions VALUES('ACTIVE')")
    with pytest.raises(ValueError, match='OPEN_POSITION'):
        q.run(SHA, 'drain', allow_market_open_suspend=True,
              allow_open_paper_carryover=True)
    assert not any(call[1] == 'stop' for call in calls)


def test_intraday_carryover_detects_unbacked_close_and_identity_change(carry_installation):
    root, db, states, calls, _ = carry_installation
    q.run(SHA, 'drain', allow_market_open_suspend=True, allow_open_paper_carryover=True)
    q.run(SHA, 'install', allow_market_open_suspend=True, allow_open_paper_carryover=True)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE paper_positions SET status='CLOSED' WHERE paper_id='paper-2'")
    with pytest.raises(ValueError, match='CLOSE_UNBACKED'):
        q.run(SHA, 'verify-carryover')
    with sqlite3.connect(db) as c:
        c.execute("UPDATE paper_positions SET status='OPEN',symbol='ALTERED' WHERE paper_id='paper-2'")
    with pytest.raises(ValueError, match='IDENTITY_CHANGED'):
        q.run(SHA, 'verify-carryover')


@pytest.mark.parametrize('change', ['stale', 'position', 'closed', 'real'])
def test_intraday_suspend_rejects_unsafe_admission_before_stop(installation, change):
    root, db, states, calls, _ = installation
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET process_state='RUNNING',session_state='MARKET_OPEN'")
        if change == 'stale':
            c.execute("UPDATE observer_state SET heartbeat_at='2020-01-01T00:00:00+00:00'")
        elif change == 'position':
            c.execute("INSERT INTO paper_future_positions VALUES('ACTIVE')")
        elif change == 'closed':
            c.execute("UPDATE observer_state SET session_state='MARKET_CLOSED'")
        else:
            c.execute("UPDATE observer_state SET real_orders_sent=1")
    with pytest.raises(ValueError):
        q.run(SHA, 'drain', allow_market_open_suspend=True)
    assert not any(call[1] == 'stop' for call in calls)


def test_intraday_stale_database_ack_without_clean_stop_is_not_drained(installation, monkeypatch):
    root, db, states, calls, command = installation
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET process_state='RUNNING',session_state='MARKET_OPEN'")
    def no_ack(args):
        result = command(args)
        if args[1] == 'stop':
            with sqlite3.connect(db) as c:
                c.execute("UPDATE observer_state SET process_state='RUNNING'")
            states[args[-1]]['State']['ExitCode'] = 1
        return result
    monkeypatch.setattr(q, 'command', no_ack)
    with pytest.raises(ValueError, match='STOP_NONZERO_OR_UNKNOWN_EXIT'):
        q.run(SHA, 'drain', allow_market_open_suspend=True)
    assert q.run(SHA, 'recover')['status'] == 'ORIGINAL_RUNTIME_RESUMED'


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


@pytest.mark.parametrize('change', ['none','wrong_image','wrong_candidate','real_orders','old_receipt'])
def test_current_provenance_owns_stopped_successor_over_historical_receipt(
        installation, monkeypatch, change):
    root, db, states, calls, _ = installation
    image = 'sha256:' + 'c'*64
    for name in q.OWNED: states[name]['Image'] = image
    q.run(SHA, 'drain'); q.run(SHA, 'install'); q.run(SHA, 'recover')
    historical = root/'data/deploy'/('quiescence-'+'f'*40+'.json')
    historical.write_text(json.dumps({'candidate_sha':'f'*40,
                                      'recorded_at':'2020-01-01T00:00:00+00:00',
                                      'status':'FIX_FORWARD_VALIDATION_REQUIRED'}))
    monkeypatch.setattr(q, 'FAILED_COLD2_CANDIDATE', 'f'*40)
    state = {'candidate_sha':'9'*40,'image_id':image,
             'recorded_at':'2020-01-02T00:00:00+00:00',
             'validation_status':'DEPLOYED_VALIDATION_PENDING',
             'mode':'PRODUCTION_PAPER','real_orders_sent':0,'real_order_routes':'NOT_CALLED'}
    if change == 'wrong_image': state['image_id'] = 'sha256:'+'d'*64
    if change == 'wrong_candidate': state['candidate_sha'] = 'invalid'
    if change == 'real_orders': state['real_orders_sent'] = 1
    (root/'data/deploy/CURRENT_STATE_V2.json').write_text(json.dumps(state))
    if change == 'old_receipt':
        prior = root/'data/deploy'/('quiescence-'+SHA+'.json')
        value = json.loads(prior.read_text()); value['inflight'] = 'UNKNOWN'
        prior.write_text(json.dumps(value))
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET process_state='WAITING_MARKET'")
    calls.clear()
    if change == 'none':
        receipt = q.run('b'*40,'drain')
        assert receipt['status'] == 'DRAINED' and receipt['fix_forward_from'] == SHA
    else:
        with pytest.raises(ValueError): q.run('b'*40,'drain')
        assert not (root/'data/deploy'/('quiescence-'+'b'*40+'.json')).exists()
    assert not any(c[1] in ('start','stop') for c in calls)


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


def test_intraday_failed_install_adopts_latest_exact_stopped_receipt(carry_installation):
    root, db, states, calls, _ = carry_installation
    q.run(SHA, 'drain', allow_market_open_suspend=True, allow_open_paper_carryover=True)
    q.run(SHA, 'install', allow_market_open_suspend=True, allow_open_paper_carryover=True)
    q.run(SHA, 'recover')
    current = {'candidate_sha':'9'*40, 'image_id':'old_image',
               'recorded_at':'2026-10-20T12:23:38+00:00',
               'validation_status':'VALIDATED_RUNTIME', 'mode':'PRODUCTION_PAPER',
               'real_orders_sent':0, 'real_order_routes':'NOT_CALLED'}
    (root/'data/deploy/CURRENT_STATE_V2.json').write_text(json.dumps(current))
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET process_state='RUNNING',session_state='OPEN'")
    calls.clear()
    successor = 'b'*40
    row = q.run(successor, 'drain', allow_market_open_suspend=True,
                allow_open_paper_carryover=True)
    assert row['fix_forward_from'] == SHA and row['spot_carryover']['open_count'] == 4
    assert not any(call[1] in ('start', 'stop') for call in calls)
    assert q.run(successor, 'install', allow_market_open_suspend=True,
                 allow_open_paper_carryover=True)['installation_started'] is True


def test_intraday_failed_install_refuses_changed_book(carry_installation):
    root, db, states, calls, _ = carry_installation
    q.run(SHA, 'drain', allow_market_open_suspend=True, allow_open_paper_carryover=True)
    q.run(SHA, 'install', allow_market_open_suspend=True, allow_open_paper_carryover=True)
    q.run(SHA, 'recover')
    current = {'candidate_sha':'9'*40, 'image_id':'old_image',
               'recorded_at':'2026-10-20T12:23:38+00:00',
               'validation_status':'VALIDATED_RUNTIME', 'mode':'PRODUCTION_PAPER',
               'real_orders_sent':0, 'real_order_routes':'NOT_CALLED'}
    (root/'data/deploy/CURRENT_STATE_V2.json').write_text(json.dumps(current))
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET process_state='RUNNING',session_state='OPEN'")
        c.execute("UPDATE paper_positions SET quantity=99 WHERE paper_id='paper-1'")
    with pytest.raises(ValueError, match='PRIOR_CARRYOVER_CHANGED'):
        q.run('b'*40, 'drain', allow_market_open_suspend=True,
              allow_open_paper_carryover=True)
    assert not any(call[1] == 'start' for call in calls)
