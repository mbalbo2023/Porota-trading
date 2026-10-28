"""A111: the observed 26 October A92 degraded/flat runtime is not healthy."""
import base64
import gzip
from datetime import date as RealDate, datetime as RealDateTime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from types import ModuleType, SimpleNamespace

import pytest

from scripts import rc6_deploy_quiescence as q
from scripts import rc6_deploy_scheduler_suspend as scheduler
from be_paper_engine import PaperStore


SHA = 'a' * 40
NEXT = 'b' * 40
NOW = RealDateTime(2026, 10, 26, 23, 5, tzinfo=timezone.utc)
SQLITE_FULL_TRACE = ('Traceback (most recent call last):\n'
    '  File "/opt/porota-trading/bi_operational_services.py", line 424, '
    'in _period_data\n'
    '    SELECT * FROM paper_decisions ORDER BY decided_at\n'
    'sqlite3.OperationalError: database or disk is full\n')


class FrozenDateTime(RealDateTime):
    current = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


def write_marker(root, candidate=q.RESCUE_SOURCE_SHA, image=q.RESCUE_IMAGE_ID,
                 status='VALIDATED_RUNTIME'):
    path=root/'data/deploy/CURRENT_STATE_V2.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'schema_version':2,'recorded_at':NOW.isoformat(),
                                'candidate_sha':candidate,'image_id':image,
                                'validation_status':status,'mode':'PRODUCTION_PAPER',
                                'real_orders_sent':0,'real_order_routes':'NOT_CALLED'}))
    return path


@pytest.fixture
def incident(tmp_path, monkeypatch):
    monkeypatch.setattr(q, 'ROOT', tmp_path)
    monkeypatch.setattr(q, 'datetime', FrozenDateTime)
    shutil.copyfile(Path(__file__).resolve().parents[1] / 'ak_byma_calendar.py',
                    tmp_path / 'ak_byma_calendar.py')
    target = tmp_path / 'data/paper_v17'
    target.mkdir(parents=True)
    db = target / 'observer_v17.db'
    with sqlite3.connect(db) as c:
        c.execute('''CREATE TABLE observer_state(
                     id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT NOT NULL,
                     process_state TEXT NOT NULL, session_state TEXT NOT NULL,
                     ppi_auth TEXT NOT NULL, heartbeat_at TEXT, last_market_data_at TEXT,
                     real_orders_sent INTEGER NOT NULL DEFAULT 0,
                     http_allowed INTEGER NOT NULL DEFAULT 0,
                     http_blocked INTEGER NOT NULL DEFAULT 0,
                     detail TEXT NOT NULL DEFAULT '')''')
        c.execute('''INSERT INTO observer_state
                     (id,mode,process_state,session_state,ppi_auth,heartbeat_at,
                      real_orders_sent) VALUES(1,?,?,?,?,?,0)''',
                  ('PRODUCTION_PAPER','DEGRADED','BOOT_CALENDAR','OK',
                   (NOW-timedelta(seconds=5)).isoformat()))
        for table in ('paper_positions', 'paper_cauciones', 'paper_future_positions'):
            c.execute('CREATE TABLE ' + table + '(status)')
    write_marker(tmp_path)
    health_path=target/'artifacts/observer_v17.db/runtime-health.json'
    health_path.parent.mkdir(parents=True)
    health_path.write_text(json.dumps({'schema':'rc6.runtime-child-health.v1',
        'recorded_at':(NOW-timedelta(seconds=5)).isoformat(),
        'source_sha':q.RESCUE_SOURCE_SHA,'mode':'PRODUCTION_PAPER',
        'real_orders_sent':0,'real_routes':'NOT_CALLED',
        'children':{'scanner':{'state':'RUNNING','pid':1234,'restarts':0}}}))
    states = {name: {'Id': str(index) * 64, 'Image': q.RESCUE_IMAGE_ID,
                     'State': {'Status': 'running', 'OOMKilled': False, 'ExitCode': 0,
                               'StartedAt': (NOW-timedelta(hours=1)).isoformat(),
                               'FinishedAt': '2020-01-01T00:00:00+00:00'}}
              for index, name in enumerate(q.OWNED, 1)}
    states['porota_critical_approval_rc6'] = {
        'Id': '3' * 64, 'Image': 'sha256:' + 'b' * 64, 'RestartCount': 0,
        'State': {'Status': 'running', 'OOMKilled': False,
                  'Health': {'Status': 'healthy'}}}
    calls = []

    def command(args):
        calls.append(args)
        if args[1] == 'inspect': return json.dumps([states[args[2]]])
        if args[1] == 'image' and args[2] == 'inspect':
            return json.dumps([{'Id': 'sha256:'+'4'*64}])
        if args[1] == 'exec': return 'RC6_CRITICAL_ISSUES_ONLY=GREEN\n'
        if args[1] == 'stop':
            states[args[-1]]['State'].update(Status='exited',
                FinishedAt=FrozenDateTime.now(timezone.utc).isoformat())
            with sqlite3.connect(db) as c:
                c.execute("UPDATE observer_state SET process_state='STOPPED'")
        if args[1] == 'start': states[args[-1]]['State']['Status'] = 'running'
        return ''

    monkeypatch.setattr(q, 'command', command)
    return db, states, calls


def test_rescue_fixture_observer_schema_matches_production(incident, tmp_path):
    fixture_db,_,_=incident
    production_db=tmp_path/'production-observer.db'
    PaperStore(str(production_db))
    with sqlite3.connect(fixture_db) as fixture, sqlite3.connect(production_db) as production:
        fixture_columns=[row[1] for row in fixture.execute('PRAGMA table_info(observer_state)')]
        production_columns=[row[1] for row in production.execute('PRAGMA table_info(observer_state)')]
    assert fixture_columns == production_columns
    assert 'source_sha' not in production_columns
    assert q.rescue_flat_book()['source_sha'] == q.RESCUE_SOURCE_SHA


def stopped_crash_incident(incident, monkeypatch, *, running_child=False):
    db, states, calls = incident
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET process_state='STOPPED',session_state='CLOSED'")
    path = q.ROOT/'data/paper_v17/artifacts/observer_v17.db/runtime-health.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    health = {'schema':'rc6.runtime-child-health.v1',
              'recorded_at':(NOW-timedelta(seconds=5)).isoformat(),
              'source_sha':q.RESCUE_SOURCE_SHA, 'mode':'PRODUCTION_PAPER',
              'real_orders_sent':0, 'real_routes':'NOT_CALLED',
              'children':{'scanner':{'state':'RUNNING' if running_child else 'CRASH_BACKOFF',
                 'pid':1234 if running_child else None, 'restarts':130,
                 'last_exit_at':(NOW-timedelta(seconds=25)).isoformat(),
                 'started_at':(NOW-timedelta(seconds=20)).isoformat() if running_child else None,
                 'last_error':None if running_child else 'CHILD_EXITED'}}}
    path.write_text(json.dumps(health))
    log = {'text':SQLITE_FULL_TRACE}
    def docker_log(args,**kwargs):
        assert args==['docker','logs','--since','90s','--tail','500',q.OWNED[0]]
        assert kwargs=={'capture_output':True,'text':True,'timeout':8}
        return SimpleNamespace(returncode=0,stdout='',stderr=log['text'])
    monkeypatch.setattr(q.subprocess,'run',docker_log)
    return db, states, calls, path, log


@pytest.mark.parametrize('running_child',[False,True])
def test_exact_a92_scanner_full_closed_drain_and_red_recovery(
        incident, monkeypatch, running_child):
    db, states, _, _, _ = stopped_crash_incident(
        incident,monkeypatch,running_child=running_child)
    receipt = q.run(SHA,'drain',allow_degraded_closed_rescue=True)
    assert receipt['rescue_pre_state']==['STOPPED','CLOSED']
    assert receipt['rescue_child_crash_proof']['scanner_state'] == (
        'RUNNING' if running_child else 'CRASH_BACKOFF')
    assert receipt['rescue_child_crash_proof']['failure']=='A92_REPORT_SQLITE_FULL'
    q.run(SHA,'install',allow_degraded_closed_rescue=True)
    with pytest.raises(ValueError,match='POSTSTART'):
        q.admit_poststart_flat(q.RESCUE_MODE,SHA)
    recovered=q.run(SHA,'recover')
    assert recovered['status']=='FIX_FORWARD_VALIDATION_REQUIRED'
    assert recovered['rescue_pre_state']==['STOPPED','CLOSED']
    assert scheduler.require_rescue_degraded_hold(SHA)=='RESCUE_DEGRADED_HELD'


@pytest.mark.parametrize('change',['stale_health','old_exit','few_restarts','wrong_source','wrong_schema',
    'live_routes','scanner_unknown','missing_trace','wrong_trace','image','book','critical'])
def test_a92_scanner_full_uncertainty_never_stops(incident,monkeypatch,change):
    db,states,calls,path,log=stopped_crash_incident(incident,monkeypatch)
    health=json.loads(path.read_text())
    scanner=health['children']['scanner']
    if change=='stale_health': health['recorded_at']=(NOW-timedelta(seconds=31)).isoformat()
    elif change=='old_exit': scanner['last_exit_at']=(NOW-timedelta(seconds=61)).isoformat()
    elif change=='few_restarts': scanner['restarts']=89
    elif change=='wrong_source': health['source_sha']='c'*40
    elif change=='wrong_schema': health['schema']='unexpected'
    elif change=='live_routes': health['real_routes']='CALLED'
    elif change=='scanner_unknown': scanner['state']='SPAWN_FAILED'
    elif change=='missing_trace': log['text']='no traceback'
    elif change=='wrong_trace': log['text']=SQLITE_FULL_TRACE.replace('line 424','line 425')
    elif change=='image': states[q.OWNED[0]]['Image']='sha256:'+'f'*64
    elif change=='book':
        with sqlite3.connect(db) as c: c.execute("INSERT INTO paper_cauciones VALUES('OPEN')")
    elif change=='critical':
        states['porota_critical_approval_rc6']['State']['Health']['Status']='unhealthy'
    path.write_text(json.dumps(health))
    with pytest.raises((ValueError,sqlite3.Error)):
        q.run(SHA,'drain',allow_degraded_closed_rescue=True)
    assert not any(call[1]=='stop' for call in calls)


def test_a92_scanner_full_no_receipt_cleanup_holds_timers(incident,monkeypatch):
    _, _, calls, _, _ = stopped_crash_incident(incident,monkeypatch)
    assert scheduler.require_rescue_degraded_hold(SHA)==(
        'RESCUE_NO_DRAIN_A92_UNVALIDATED_HELD')
    assert not any(call[1] in {'stop','start'} for call in calls)


def test_a92_phase_change_before_stop_rejects_without_container_mutation(
        incident,monkeypatch):
    db,_,calls,_,_=stopped_crash_incident(incident,monkeypatch)
    original=q.admit
    calls_to_admit=[]
    def switching_admit(**kwargs):
        phase=original(**kwargs)
        calls_to_admit.append(phase)
        if len(calls_to_admit)==1:
            with sqlite3.connect(db) as c:
                c.execute("UPDATE observer_state SET process_state='DEGRADED',"
                          "session_state='BOOT_CALENDAR'")
        return phase
    monkeypatch.setattr(q,'admit',switching_admit)
    with pytest.raises(ValueError,match='RESCUE_PHASE_CHANGED_BEFORE_STOP'):
        q.run(SHA,'drain',allow_degraded_closed_rescue=True)
    assert calls_to_admit==['STOPPED','DEGRADED']
    assert not any(call[1]=='stop' for call in calls)


@pytest.mark.parametrize('change',['image','source','open_book','wrong_phase','critical'])
def test_no_drain_cleanup_rejects_changed_a92_invariants(incident,change):
    db,states,calls=incident
    if change=='image': states[q.OWNED[0]]['Image']='sha256:'+'f'*64
    elif change=='critical':
        states['porota_critical_approval_rc6']['State']['Health']['Status']='unhealthy'
    elif change=='source': write_marker(q.ROOT,candidate='c'*40)
    else:
        with sqlite3.connect(db) as c:
            if change=='open_book': c.execute("INSERT INTO paper_positions VALUES('OPEN')")
            else: c.execute("UPDATE observer_state SET process_state='RUNNING'")
    with pytest.raises(ValueError,match='SCHEDULER|QUIESCENCE'):
        scheduler.require_rescue_degraded_hold(SHA)
    assert not any(call[1] in {'stop','start'} for call in calls)


def test_exact_incident_drain_install_and_healthy_closed_poststart(incident):
    db, states, calls = incident
    receipt = q.run(SHA, 'drain', allow_degraded_closed_rescue=True)
    assert receipt['status'] == 'DRAINED'
    assert receipt['rescue_pre_state'] == ['DEGRADED', 'BOOT_CALENDAR']
    assert receipt['rescue_calendar_and_hours']['byma_hours_com18782_sha256'] == q.RESCUE_HOURS_SHA256
    assert [call[-1] for call in calls if call[1] == 'stop'] == list(q.OWNED)
    installed = q.run(SHA, 'install', allow_degraded_closed_rescue=True)
    assert installed['status'] == 'HOST_INSTALLATION_STARTED'
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET process_state='WAITING_MARKET',session_state='CLOSED',"
                  "heartbeat_at=?", (FrozenDateTime.now(timezone.utc).isoformat(),))
    assert q.admit_poststart_flat(q.RESCUE_MODE, SHA) == 'CLOSED'


@pytest.mark.parametrize('change', ['early', 'next_day', 'calendar', 'source', 'marker_missing',
    'marker_image','child_source','child_schema','image','heartbeat', 'open_spot',
    'open_caucion', 'active_future', 'real_orders', 'critical'])
def test_uncertain_incident_cannot_stop_either_container(incident, change):
    db, states, calls = incident
    if change == 'early': FrozenDateTime.current = RealDateTime(2026,10,26,22,59,tzinfo=timezone.utc)
    elif change == 'next_day': FrozenDateTime.current = RealDateTime(2026,10,27,3,0,tzinfo=timezone.utc)
    elif change == 'calendar': (q.ROOT/'ak_byma_calendar.py').write_text('tampered')
    elif change == 'marker_missing': (q.ROOT/'data/deploy/CURRENT_STATE_V2.json').unlink()
    elif change == 'marker_image': write_marker(q.ROOT,image='sha256:'+'f'*64)
    elif change in {'child_source','child_schema'}:
        path=q.ROOT/'data/paper_v17/artifacts/observer_v17.db/runtime-health.json'
        health=json.loads(path.read_text())
        health['source_sha' if change=='child_source' else 'schema']='wrong'
        path.write_text(json.dumps(health))
    elif change == 'image': states[q.OWNED[1]]['Image'] = 'sha256:'+'f'*64
    elif change == 'source': write_marker(q.ROOT,candidate='c'*40)
    elif change == 'critical': states['porota_critical_approval_rc6']['State']['Health']['Status'] = 'unhealthy'
    else:
        with sqlite3.connect(db) as c:
            if change == 'heartbeat': c.execute("UPDATE observer_state SET heartbeat_at='2020-01-01T00:00:00+00:00'")
            elif change == 'real_orders': c.execute('UPDATE observer_state SET real_orders_sent=1')
            else:
                table, status = {'open_spot':('paper_positions','OPEN'),
                                 'open_caucion':('paper_cauciones','OPEN'),
                                 'active_future':('paper_future_positions','ACTIVE')}[change]
                c.execute('INSERT INTO '+table+' VALUES(?)',(status,))
    try:
        with pytest.raises((ValueError,sqlite3.Error)):
            q.run(SHA, 'drain', allow_degraded_closed_rescue=True)
        assert not any(call[1] == 'stop' for call in calls)
    finally:
        FrozenDateTime.current = NOW


def test_install_requires_same_mode_receipt_and_flat_book(incident):
    db, _, _ = incident
    q.run(SHA, 'drain', allow_degraded_closed_rescue=True)
    with pytest.raises(ValueError, match='INSTALL_RESCUE_MODE_CHANGED'):
        q.run(SHA, 'install')
    with sqlite3.connect(db) as c: c.execute("INSERT INTO paper_positions VALUES('OPEN')")
    with pytest.raises(ValueError, match='OPEN_POSITION'):
        q.run(SHA, 'install', allow_degraded_closed_rescue=True)


def test_install_rejects_future_or_replayed_rescue_receipt(incident):
    _, _, _ = incident
    q.run(SHA, 'drain', allow_degraded_closed_rescue=True)
    path = q.ROOT/'data/deploy'/('quiescence-' + SHA + '.json')
    original = json.loads(path.read_text())
    for change in ('future', 'wrong_offset', 'earlier_recorded', 'wrong_hours'):
        row = json.loads(json.dumps(original))
        if change == 'future':
            row['rescue_calendar_and_hours']['observed_at_utc'] = '2026-10-27T02:00:00+00:00'
        elif change == 'wrong_offset':
            row['rescue_calendar_and_hours']['observed_at_art'] = '2026-10-26T23:05:00+00:00'
        elif change == 'earlier_recorded':
            row['recorded_at'] = '2026-10-26T23:04:00+00:00'
        else:
            row['rescue_calendar_and_hours']['byma_hours_com18782_sha256'] = 'wrong'
        path.write_text(json.dumps(row))
        with pytest.raises(ValueError, match='RESCUE_RECEIPT'):
            q.run(SHA, 'install', allow_degraded_closed_rescue=True)
    path.write_text(json.dumps(original))
    assert q.run(SHA, 'install', allow_degraded_closed_rescue=True)['installation_started'] is True


def test_failed_clean_stop_never_reaches_install(incident, monkeypatch):
    _, states, calls = incident
    original = q.command
    def dirty_stop(args):
        result = original(args)
        if args[1] == 'stop': states[args[-1]]['State']['ExitCode'] = 137
        return result
    monkeypatch.setattr(q, 'command', dirty_stop)
    with pytest.raises(ValueError, match='NONZERO_OR_UNKNOWN_EXIT'):
        q.run(SHA, 'drain', allow_degraded_closed_rescue=True)
    assert len([c for c in calls if c[1] == 'stop']) == 1
    row = json.loads((q.ROOT/'data/deploy'/('quiescence-' + SHA + '.json')).read_text())
    assert row['installation_started'] is False


def test_preinstall_recovery_reports_original_degraded_and_holds_timers(incident):
    db, states, _ = incident
    q.run(SHA, 'drain', allow_degraded_closed_rescue=True)
    recovered = q.run(SHA, 'recover')
    assert recovered['status'] == 'ORIGINAL_RUNTIME_DEGRADED_RESUMED'
    assert recovered['runtime_health'] == 'DEGRADED_UNVERIFIED_AFTER_RECOVERY'
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET process_state='DEGRADED',heartbeat_at=?",
                  (FrozenDateTime.now(timezone.utc).isoformat(),))
    assert all(states[name]['State']['Status'] == 'running' for name in q.OWNED)
    assert scheduler.require_rescue_degraded_hold(SHA) == 'RESCUE_DEGRADED_HELD'


def test_suspend_then_no_drain_receipt_holds_original_degraded(incident):
    _, _, calls = incident
    assert scheduler.require_rescue_degraded_hold(SHA) == (
        'RESCUE_NO_DRAIN_A92_UNVALIDATED_HELD')
    assert not any(call[1] in {'stop', 'start'} for call in calls)
    FrozenDateTime.current = RealDateTime(2026,10,27,3,0,tzinfo=timezone.utc)
    try:
        assert scheduler.require_rescue_degraded_hold(SHA) == (
            'RESCUE_NO_DRAIN_A92_UNVALIDATED_HELD')
    finally:
        FrozenDateTime.current = NOW


def test_cleanup_no_drain_persists_explicit_hold_without_timer_start(incident,monkeypatch):
    names=['porota-preopen-rc6.timer','porota-runtime-evidence-rc6.timer']
    raw=json.dumps({'units':{'systemd/'+n:{'role':'ACTIVE_TIMER','active':True}
                             for n in names}}).encode()
    policy=base64.b64encode(raw).decode()
    target=q.ROOT/'data/deploy'
    target.mkdir(parents=True,exist_ok=True)
    row={'candidate_sha':SHA,'policy_sha256':hashlib.sha256(raw).hexdigest(),
         'timers':names,'ppi_before':[],'attempted':names,'status':'SUSPENDED'}
    (target/('scheduler-suspend-'+SHA+'.json')).write_text(json.dumps(row))
    actions=[]
    def systemctl(*args):
        actions.append(args)
        if args[1]=='list-unit-files': return ('',0)
        if args[1]=='is-enabled': return ('enabled',0)
        if args[1]=='is-active': return ('inactive',0)
        if args[1]=='show': return (args[2].removesuffix('.timer')+'.service',0)
        raise AssertionError(args)
    monkeypatch.setattr(scheduler,'ROOT',target)
    monkeypatch.setattr(scheduler,'command',systemctl)
    assert scheduler.run(SHA,'recover-degraded-closed',policy)['rescue_failure_disposition'] == (
        'RESCUE_NO_DRAIN_A92_UNVALIDATED_HELD')
    assert not any(call[1] in {'start','stop'} for call in actions)


def test_postinstall_replaced_ids_fail_cleanup_without_rearming(incident, monkeypatch):
    _, states, _ = incident
    q.run(SHA, 'drain', allow_degraded_closed_rescue=True)
    q.run(SHA, 'install', allow_degraded_closed_rescue=True)
    q.run(SHA, 'recover')
    for name in q.OWNED:
        states[name]['Id'] = 'f' * 64
        states[name]['Image'] = 'sha256:' + 'e' * 64
    with pytest.raises(ValueError, match='RUNTIME_IDENTITY_CHANGED'):
        scheduler.require_rescue_degraded_hold(SHA)
    workflow = (Path(__file__).resolve().parents[1] /
                '.github/workflows/porota-deploy-v2-promote.yml').read_text()
    assert 'SCHEDULER_ACTION=recover-degraded-closed' in workflow
    assert "[ '${{ steps.suspend.outcome }}' = success ]" in workflow
    assert 'RC6_PAPER_SESSION=RESCUE_ADMITTED|runtime=UNHEALTHY_A92|health=RED' in workflow


def test_candidate_image_intent_and_exact_exited_replacement_hold(incident):
    db, states, _ = incident
    q.run(SHA, 'drain', allow_degraded_closed_rescue=True)
    q.run(SHA, 'install', allow_degraded_closed_rescue=True)
    frozen = q.ROOT/'frozen.json'
    frozen.write_text(json.dumps({'candidate_sha':SHA,'candidate_tree_sha':'6'*40,
                                  'image_tar_sha256':'5'*64}))
    intent = q.run(SHA,'image-intent',candidate_image_id='sha256:'+'4'*64,
                   frozen_manifest=str(frozen))['candidate_image_intent']
    assert intent['image_tar_sha256'] == '5'*64
    for index,name in enumerate(q.OWNED, 7):
        states[name]['Id'] = str(index)*64
        states[name]['Image'] = intent['image_id']
        states[name]['State'].update(Status='exited',ExitCode=0,OOMKilled=False,
                                      FinishedAt=NOW.isoformat())
    write_marker(q.ROOT,SHA,'sha256:'+'4'*64,'DEPLOYED_VALIDATION_PENDING')
    for name in q.OWNED:
        states[name]['State']['Status']='running'
    captured=q.run(SHA,'capture-runtime')['candidate_runtime_initial']
    assert captured['status']=='running'
    for name in q.OWNED:
        states[name]['State']['Status']='exited'
    recovered = q.run(SHA,'recover')
    assert recovered['status']=='FIX_FORWARD_VALIDATION_REQUIRED'
    assert recovered['candidate_runtime_after_failure']['status']=='exited'
    assert scheduler.require_rescue_degraded_hold(SHA)=='RESCUE_CANDIDATE_UNVALIDATED_HELD'
    states[q.OWNED[0]]['Id']='f'*64
    with pytest.raises(ValueError,match='CANDIDATE_IDENTITY_CHANGED'):
        scheduler.require_rescue_degraded_hold(SHA)


def test_image_loaded_but_original_stop_intact_is_held(incident):
    _,_,_=incident
    q.run(SHA,'drain',allow_degraded_closed_rescue=True)
    q.run(SHA,'install',allow_degraded_closed_rescue=True)
    frozen=q.ROOT/'frozen.json'
    frozen.write_text(json.dumps({'candidate_sha':SHA,'candidate_tree_sha':'6'*40,
                                  'image_tar_sha256':'5'*64}))
    q.run(SHA,'image-intent',candidate_image_id='sha256:'+'4'*64,
          frozen_manifest=str(frozen))
    receipt=q.run(SHA,'recover')
    assert receipt['status']=='FIX_FORWARD_VALIDATION_REQUIRED'
    assert receipt.get('candidate_runtime_after_failure') is None
    assert scheduler.require_rescue_degraded_hold(SHA)=='RESCUE_DEGRADED_HELD'


def make_a138_original_stopped(incident):
    """Exact A138 failure: bundle loaded, old A92 STOPPED/BOOT_CALENDAR."""
    db,states,calls=incident
    prior=q.FAILED_A92_RESCUE_CANDIDATE
    q.run(prior,'drain',allow_degraded_closed_rescue=True)
    q.run(prior,'install',allow_degraded_closed_rescue=True)
    frozen=q.ROOT/'prior-frozen.json'
    frozen.write_text(json.dumps({'candidate_sha':prior,
                                  'candidate_tree_sha':'6'*40,
                                  'image_tar_sha256':'5'*64}))
    q.run(prior,'image-intent',candidate_image_id='sha256:'+'4'*64,
          frozen_manifest=str(frozen))
    assert all(states[name]['State']['Status']=='exited' for name in q.OWNED)
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT process_state,session_state FROM observer_state').fetchone()==(
            'STOPPED','BOOT_CALENDAR')
    return db,states,calls


def test_a139_failed_a138_old_a92_stopped_can_drain_without_stopping_again(incident):
    _,_,calls=make_a138_original_stopped(incident)
    before=len([call for call in calls if call[1]=='stop'])
    preview=q.adopt_stopped_a92_after_failed_rescue(
        NEXT,q.ROOT/'data/deploy',{name:q.inspect(name) for name in q.OWNED})
    assert preview['adopted_stopped_a92_rescue'] is True
    assert preview['rescue_pre_state']==['DEGRADED','BOOT_CALENDAR']
    assert len(preview['prior_receipt_sha256'])==64
    receipt=q.run(NEXT,'drain',allow_degraded_closed_rescue=True)
    assert receipt['status']=='DRAINED'
    assert len([call for call in calls if call[1]=='stop'])==before
    installed=q.run(NEXT,'install',allow_degraded_closed_rescue=True)
    assert installed['status']=='HOST_INSTALLATION_STARTED'
    assert installed['real_orders_sent']==0


@pytest.mark.parametrize('change',['prior_status','missing_intent','prior_hash',
    'old_id','dirty_stop','book','orders','marker','critical','wrong_phase'])
def test_a139_adoption_rejects_any_missing_stopped_a92_evidence(incident,change):
    db,states,calls=make_a138_original_stopped(incident)
    prior_path=q.ROOT/'data/deploy'/('quiescence-'+q.FAILED_A92_RESCUE_CANDIDATE+'.json')
    previous=json.loads(prior_path.read_text())
    if change=='prior_status': previous['status']='DRAINED'
    elif change=='missing_intent': previous.pop('candidate_image_intent')
    elif change=='prior_hash': previous['rescue_calendar_and_hours']['calendar_sha256']='x'*64
    elif change=='old_id': states[q.OWNED[0]]['Id']='f'*64
    elif change=='dirty_stop': states[q.OWNED[1]]['State']['ExitCode']=137
    elif change=='marker': write_marker(q.ROOT,candidate='c'*40)
    elif change=='critical': states['porota_critical_approval_rc6']['State']['Health']['Status']='unhealthy'
    elif change=='wrong_phase':
        with sqlite3.connect(db) as c:
            c.execute("UPDATE observer_state SET session_state='MARKET_OPEN'")
    else:
        with sqlite3.connect(db) as c:
            if change=='book': c.execute("INSERT INTO paper_positions VALUES('OPEN')")
            else: c.execute('UPDATE observer_state SET real_orders_sent=1')
    prior_path.write_text(json.dumps(previous))
    before=len([call for call in calls if call[1] in {'stop','start'}])
    with pytest.raises((ValueError,KeyError,TypeError)):
        q.run(NEXT,'drain',allow_degraded_closed_rescue=True)
    assert not (q.ROOT/'data/deploy'/('quiescence-'+NEXT+'.json')).exists()
    assert len([call for call in calls if call[1] in {'stop','start'}])==before


def test_a139_failure_before_new_drain_holds_timers_on_prior_a92_stop(incident):
    _,states,calls=make_a138_original_stopped(incident)
    assert scheduler.require_rescue_degraded_hold(NEXT)==(
        'RESCUE_A92_STOPPED_A138_UNVALIDATED_HELD')
    assert not any(states[name]['State']['Status']=='running' for name in q.OWNED)
    assert not any(call[1]=='start' for call in calls)


def test_a139_classifier_adopts_exact_prior_stopped_a92_readonly(
        incident,monkeypatch,capsys):
    db,_,calls=make_a138_original_stopped(incident)
    installed_source=q.ROOT/'scripts/rc6_deploy_quiescence.py'
    installed_source.parent.mkdir(parents=True,exist_ok=True)
    installed_source.write_bytes(b'A138_FULLY_HASHED_STAGED_SOURCE_FIXTURE')
    real_sha256=hashlib.sha256
    def sha256(data=b''):
        if data==b'A138_FULLY_HASHED_STAGED_SOURCE_FIXTURE':
            return SimpleNamespace(hexdigest=lambda:
                'dcb0ed996a6be9aefb0a609139285f6160248909ccde92f189a24b77fb617662')
        return real_sha256(data)
    monkeypatch.setattr(hashlib,'sha256',sha256)
    backup=ModuleType('scripts.rc6_679_backup_suspension')
    backup.inventory=lambda:[]
    monkeypatch.setitem(sys.modules,backup.__name__,backup)
    monkeypatch.setitem(sys.modules,'scripts.rc6_deploy_quiescence',q)
    monkeypatch.setattr(sys,'argv',['-',NEXT,'',''])
    real_connect=sqlite3.connect
    def connect(target,*args,**kwargs):
        if str(target).startswith('file:///opt/porota-trading/data/paper_v17/'):
            target=db.as_uri()+'?mode=ro'
        return real_connect(target,*args,**kwargs)
    monkeypatch.setattr(sqlite3,'connect',connect)
    workflow=(Path(__file__).resolve().parents[1]/
              '.github/workflows/porota-deploy-v2-promote.yml').read_text()
    block=workflow.split('- name: Classify current flat PAPER session before host writes',1)[1]
    code=block.split("<<'PY'\n",1)[1].split('\n          PY',1)[0]
    code='\n'.join(line.removeprefix('          ') for line in code.splitlines())
    capsys.readouterr()
    before=len(calls)
    exec(compile(code,'a139-preflight-classifier','exec'),{})
    out=capsys.readouterr()
    assert out.out==q.RESCUE_MODE+'\n'
    assert 'RC6_A139_A92_FAILED_RESCUE_PRIOR_STOP=GREEN' in out.err
    assert not any(call[1] in {'stop','start'} for call in calls[before:])


@pytest.mark.parametrize('change',['missing_intent','mixed_image','unclean_exit'])
def test_failed_replacement_never_rearms_without_provenance(incident,change):
    _,states,_=incident
    q.run(SHA,'drain',allow_degraded_closed_rescue=True)
    q.run(SHA,'install',allow_degraded_closed_rescue=True)
    if change != 'missing_intent':
        frozen=q.ROOT/'frozen.json'
        frozen.write_text(json.dumps({'candidate_sha':SHA,'candidate_tree_sha':'6'*40,
                                      'image_tar_sha256':'5'*64}))
        q.run(SHA,'image-intent',candidate_image_id='sha256:'+'4'*64,
              frozen_manifest=str(frozen))
    states[q.OWNED[0]]['Id']='7'*64
    states[q.OWNED[0]]['Image']='sha256:'+'4'*64
    if change != 'mixed_image':
        states[q.OWNED[1]]['Id']='8'*64
        states[q.OWNED[1]]['Image']='sha256:'+'4'*64
    if change == 'unclean_exit':
        states[q.OWNED[0]]['State']['ExitCode']=137
    recovered=q.run(SHA,'recover')
    assert recovered['status']=='RECOVERY_UNVERIFIED'
    assert recovered['installation_started'] is True
    with pytest.raises(ValueError,match='RECEIPT|RUNTIME|CANDIDATE'):
        scheduler.require_rescue_degraded_hold(SHA)


def test_classifier_pins_same_a92_and_calendar_as_drain():
    workflow = (Path(__file__).resolve().parents[1] /
                '.github/workflows/porota-deploy-v2-promote.yml').read_text()
    block = workflow.split('- name: Classify current flat PAPER session before host writes',1)[1]
    block = block.split('- name: Dynamic pre-transfer disk guard',1)[0]
    for value in (q.RESCUE_SOURCE_SHA,q.RESCUE_IMAGE_ID,q.RESCUE_CALENDAR_SHA256,
                  q.RESCUE_HOURS_SHA256):
        assert value in block
    assert 'DEGRADED_CLOSED_20261026' in block
    assert 'critical_admission()' in block
    assert 'CANDIDATE_A125_B64' in block
    assert 'require_original_marker=True' in block
    assert 'RC6_A125_CANDIDATE_SOURCE_GZIP_B64' in workflow
    assert 'RC6_A125_CANDIDATE_SOURCE_SHA256' in workflow
    assert 'RESCUE_ADMITTED|runtime=UNHEALTHY_A92|health=RED' in block
    assert 'RC6_RESCUE_DISK_PREFLIGHT=RED|no cleanup or host transfer' in workflow


def test_a138_exact_a92_rescue_admits_operator_paused_27oct_not_unproven_day(
        incident, monkeypatch):
    """Owner-approved PAPER pause can admit 27 Oct without clock magic."""
    previous = FrozenDateTime.current
    try:
        calls = []
        def exact_pause(*, require_original_marker=False):
            calls.append(require_original_marker)
            return 'e'*64
        monkeypatch.setattr(scheduler,'exact_operator_preopen_pause',exact_pause)
        monkeypatch.setattr(scheduler,'operator_preopen_timer_disabled',lambda:True)
        FrozenDateTime.current = RealDateTime(2026, 10, 27, 21, 27,
                                              tzinfo=timezone.utc)
        early = q.degraded_closed_rescue_proof()
        assert early['business_day']=='2026-10-27'
        assert early['operator_paper_early'] is True
        assert early['operator_pause_sha256']=='e'*64
        assert calls==[True]
        monkeypatch.setattr(scheduler,'operator_preopen_timer_disabled',lambda:False)
        with pytest.raises(ValueError,match='RESCUE_OPERATOR_PAUSE_RED'):
            q.degraded_closed_rescue_proof()
        FrozenDateTime.current = RealDateTime(2026, 10, 27, 23, 5,
                                              tzinfo=timezone.utc)
        proof = q.degraded_closed_rescue_proof()
        assert proof['business_day'] == '2026-10-27'
        assert proof['operator_paper_early'] is False
        assert proof['byma_latest_session_end_art'] == '19:00'
        FrozenDateTime.current = RealDateTime(2026, 10, 31, 23, 5,
                                              tzinfo=timezone.utc)
        with pytest.raises(ValueError, match='RESCUE_WINDOW_RED'):
            q.degraded_closed_rescue_proof()
    finally:
        FrozenDateTime.current = previous


def test_a138_rescue_receipt_business_day_cannot_be_relabelled(incident):
    receipt = q.run(SHA, 'drain', allow_degraded_closed_rescue=True)
    # The original incident's postclose receipt predates these optional fields.
    legacy = json.loads(json.dumps(receipt))
    legacy['rescue_calendar_and_hours'].pop('operator_paper_early')
    legacy['rescue_calendar_and_hours'].pop('operator_pause_sha256')
    assert q.exact_rescue_receipt(legacy)['business_day']=='2026-10-26'
    altered = json.loads(json.dumps(receipt))
    altered['rescue_calendar_and_hours']['business_day'] = '2026-10-27'
    with pytest.raises(ValueError, match='RESCUE_RECEIPT_CLOCK_RED'):
        q.exact_rescue_receipt(altered)


def test_a138_early_rescue_accepts_actual_open_only_with_paper_no_go(
        incident, monkeypatch):
    db, states, calls = incident
    previous = FrozenDateTime.current
    now = RealDateTime(2026, 10, 27, 21, 27, tzinfo=timezone.utc)
    FrozenDateTime.current = now
    try:
        with sqlite3.connect(db) as c:
            c.execute('UPDATE observer_state SET heartbeat_at=?',
                      ((now-timedelta(seconds=5)).isoformat(),))
        health_path=(q.ROOT/'data/paper_v17/artifacts/observer_v17.db/'
                     'runtime-health.json')
        health=json.loads(health_path.read_text())
        health['recorded_at']=(now-timedelta(seconds=5)).isoformat()
        health_path.write_text(json.dumps(health))
        monkeypatch.setattr(scheduler,'exact_operator_preopen_pause',
                            lambda **_: 'e'*64)
        monkeypatch.setattr(scheduler,'operator_preopen_timer_disabled',
                            lambda:True)
        receipt=q.run(SHA,'drain',allow_degraded_closed_rescue=True)
        assert receipt['rescue_calendar_and_hours']['operator_paper_early'] is True
        q.run(SHA,'install',allow_degraded_closed_rescue=True)
        with sqlite3.connect(db) as c:
            c.execute("UPDATE observer_state SET process_state='RUNNING',"
                      "session_state='MARKET_OPEN',heartbeat_at=?",
                      ((now-timedelta(seconds=2)).isoformat(),))
        assert q.admit_poststart_flat(q.RESCUE_MODE,SHA)=='OPEN'
        monkeypatch.setattr(scheduler,'operator_preopen_timer_disabled',
                            lambda:False)
        with pytest.raises(ValueError,match='RESCUE_EARLY_PAUSE_LOST'):
            q.admit_poststart_flat(q.RESCUE_MODE,SHA)
    finally:
        FrozenDateTime.current=previous


@pytest.mark.parametrize('when,paused,admitted,error', [
    (RealDateTime(2026, 10, 27, 21, 27, tzinfo=timezone.utc), True, True, ''),
    (RealDateTime(2026, 10, 27, 21, 27, tzinfo=timezone.utc), False, False,
     'RC6_RESCUE_OPERATOR_PAUSE_RED'),
    (RealDateTime(2026, 10, 27, 23, 5, tzinfo=timezone.utc), False, True, ''),
    (RealDateTime(2026, 10, 31, 23, 5, tzinfo=timezone.utc), False, False,
     'RC6_RESCUE_WINDOW_RED'),
])
def test_a138_pretransfer_classifier_window_fails_closed(
        incident, monkeypatch, capsys, when, paused, admitted, error):
    db, states, calls = incident
    previous = FrozenDateTime.current
    FrozenDateTime.current = when
    try:
        with sqlite3.connect(db) as c:
            c.execute('UPDATE observer_state SET heartbeat_at=?',
                      ((when-timedelta(seconds=5)).isoformat(),))
        health_path = (q.ROOT/'data/paper_v17/artifacts/observer_v17.db/'
                       'runtime-health.json')
        health = json.loads(health_path.read_text())
        health['recorded_at'] = (when-timedelta(seconds=5)).isoformat()
        health_path.write_text(json.dumps(health))
        workflow = (Path(__file__).resolve().parents[1] /
                    '.github/workflows/porota-deploy-v2-promote.yml').read_text()
        block = workflow.split(
            '- name: Classify current flat PAPER session before host writes', 1)[1]
        code = block.split("<<'PY'\n", 1)[1].split('\n          PY', 1)[0]
        code = '\n'.join(line.removeprefix('          ')
                         for line in code.splitlines())
        module = ModuleType('scripts.rc6_deploy_quiescence')
        module.ROOT, module.OWNED = q.ROOT, q.OWNED
        module.inspect = lambda name: states[name]
        module.admit = lambda **_: pytest.fail('NO_UNPROVEN_HEALTHY_CLOSED')
        module.exact_clean_stopped = lambda *_: pytest.fail('NO_PRIOR_STOP')
        module.adopt_stopped_after_failed_install = (
            lambda *_, **__: pytest.fail('NO_PRIOR_STOP'))
        critical = []
        module.critical_admission = lambda: critical.append('issues_only')
        backup = ModuleType('scripts.rc6_679_backup_suspension')
        backup.inventory = lambda: []
        operator_source = (
            "def exact_operator_preopen_pause(*,require_original_marker=False):\n"
            "    if require_original_marker is not True:\n"
            "        raise ValueError('ORIGINAL_MARKER_REQUIRED')\n"
            "    return 'e'*64\n"
            "def operator_preopen_timer_disabled():\n"
            f"    return {paused!r}\n"
        )
        clock = ModuleType('datetime')
        clock.datetime, clock.timezone, clock.date = (
            FrozenDateTime, timezone, RealDate)
        monkeypatch.setitem(sys.modules, module.__name__, module)
        monkeypatch.setitem(sys.modules, backup.__name__, backup)
        monkeypatch.setitem(sys.modules, 'datetime', clock)
        operator_bytes = operator_source.encode()
        monkeypatch.setattr(sys, 'argv', ['-', SHA, '',
            base64.b64encode(gzip.compress(operator_bytes, mtime=0)).decode(),
            '', hashlib.sha256(operator_bytes).hexdigest()])
        real_connect = sqlite3.connect
        def connect(target, *args, **kwargs):
            if str(target).startswith(
                    'file:///opt/porota-trading/data/paper_v17/'):
                target = db.as_uri() + '?mode=ro'
            return real_connect(target, *args, **kwargs)
        monkeypatch.setattr(sqlite3, 'connect', connect)
        if admitted:
            exec(compile(code, 'a138-classifier', 'exec'), {})
            assert capsys.readouterr().out == q.RESCUE_MODE + '\n'
            assert critical == ['issues_only']
        else:
            with pytest.raises(SystemExit, match=error):
                exec(compile(code, 'a138-classifier', 'exec'), {})
            assert critical == []
        assert not any(call[1] in {'stop', 'start'} for call in calls)
    finally:
        FrozenDateTime.current = previous


@pytest.mark.parametrize('source_ok', [True, False])
def test_pretransfer_classifier_replays_observed_host_without_writes(
        incident, monkeypatch, capsys, source_ok):
    db, states, _ = incident
    if not source_ok:
        write_marker(q.ROOT,candidate='c'*40)
    workflow = (Path(__file__).resolve().parents[1] /
                '.github/workflows/porota-deploy-v2-promote.yml').read_text()
    block = workflow.split('- name: Classify current flat PAPER session before host writes',1)[1]
    code = block.split("<<'PY'\n",1)[1].split('\n          PY',1)[0]
    code = '\n'.join(line.removeprefix('          ') for line in code.splitlines())
    module = ModuleType('scripts.rc6_deploy_quiescence')
    module.ROOT, module.OWNED = q.ROOT, q.OWNED
    module.inspect = lambda name: states[name]
    module.admit = lambda **_: pytest.fail('CLASSIFIER_MUST_NOT_NORMALIZE_DEGRADED_TO_CLOSED')
    module.exact_clean_stopped = lambda *_: pytest.fail('NO_PRIOR_STOP')
    module.adopt_stopped_after_failed_install = lambda *_,**__: pytest.fail('NO_PRIOR_STOP')
    critical = []
    module.critical_admission = lambda: critical.append('issues_only')
    backup = ModuleType('scripts.rc6_679_backup_suspension')
    backup.inventory = lambda: []
    clock_module = ModuleType('datetime')
    clock_module.datetime, clock_module.timezone, clock_module.date = FrozenDateTime, timezone, RealDate
    monkeypatch.setitem(sys.modules,module.__name__,module)
    monkeypatch.setitem(sys.modules,backup.__name__,backup)
    monkeypatch.setitem(sys.modules,'datetime',clock_module)
    monkeypatch.setattr(sys,'argv',['-',SHA,''])
    real_connect = sqlite3.connect
    def connect(target,*args,**kwargs):
        if str(target).startswith('file:///opt/porota-trading/data/paper_v17/'):
            target = db.as_uri()+'?mode=ro'
        return real_connect(target,*args,**kwargs)
    monkeypatch.setattr(sqlite3,'connect',connect)
    if source_ok:
        exec(compile(code,'deploy-v2-rescue-classifier','exec'),{})
        assert capsys.readouterr().out == q.RESCUE_MODE+'\n'
        assert critical == ['issues_only']
    else:
        with pytest.raises(SystemExit,match='RC6_RESCUE_CURRENT_SOURCE_RED'):
            exec(compile(code,'deploy-v2-rescue-classifier','exec'),{})
        assert critical == []


@pytest.mark.parametrize('running_child',[False,True])
@pytest.mark.parametrize('valid_trace',[True,False])
def test_pretransfer_classifier_requires_recent_a92_sqlite_full_trace(
        incident,monkeypatch,capsys,running_child,valid_trace):
    db,states,calls,_,log=stopped_crash_incident(
        incident,monkeypatch,running_child=running_child)
    if not valid_trace: log['text']='unrelated scanner failure'
    workflow=(Path(__file__).resolve().parents[1]/
              '.github/workflows/porota-deploy-v2-promote.yml').read_text()
    block=workflow.split('- name: Classify current flat PAPER session before host writes',1)[1]
    code=block.split("<<'PY'\n",1)[1].split('\n          PY',1)[0]
    code='\n'.join(line.removeprefix('          ') for line in code.splitlines())
    module=ModuleType('scripts.rc6_deploy_quiescence')
    module.ROOT,module.OWNED=q.ROOT,q.OWNED
    module.inspect=lambda name:states[name]
    module.admit=lambda **_:pytest.fail('CLASSIFIER_MUST_NOT_ASSUME_HEALTHY_CLOSED')
    module.exact_clean_stopped=lambda *_:pytest.fail('NO_PRIOR_STOP')
    module.adopt_stopped_after_failed_install=lambda *_,**__:pytest.fail('NO_PRIOR_STOP')
    checks=[]
    module.critical_admission=lambda:checks.append('issues_only')
    backup=ModuleType('scripts.rc6_679_backup_suspension')
    backup.inventory=lambda:[]
    clock=ModuleType('datetime')
    clock.datetime,clock.timezone,clock.date=FrozenDateTime,timezone,RealDate
    monkeypatch.setitem(sys.modules,module.__name__,module)
    monkeypatch.setitem(sys.modules,backup.__name__,backup)
    monkeypatch.setitem(sys.modules,'datetime',clock)
    monkeypatch.setattr(sys,'argv',['-',SHA,''])
    original=sqlite3.connect
    def connect(target,*args,**kwargs):
        if str(target).startswith('file:///opt/porota-trading/data/paper_v17/'):
            target=db.as_uri()+'?mode=ro'
        return original(target,*args,**kwargs)
    monkeypatch.setattr(sqlite3,'connect',connect)
    if valid_trace:
        exec(compile(code,'deploy-v2-stopped-rescue-classifier','exec'),{})
        assert capsys.readouterr().out==q.RESCUE_MODE+'\n'
        assert checks==['issues_only']
    else:
        with pytest.raises(SystemExit,match='RC6_RESCUE_SQLITE_FULL_TRACE_MISSING'):
            exec(compile(code,'deploy-v2-stopped-rescue-classifier','exec'),{})
        assert checks==[]
    assert not any(call[1] in {'stop','start'} for call in calls)


def failed_candidate_stopped(incident,monkeypatch=None,*,from_stopped=False):
    if from_stopped:
        assert monkeypatch is not None
        stopped_crash_incident(incident,monkeypatch)
    db,states,calls=incident
    q.run(SHA,'drain',allow_degraded_closed_rescue=True)
    q.run(SHA,'install',allow_degraded_closed_rescue=True)
    frozen=q.ROOT/'frozen.json'
    frozen.write_text(json.dumps({'candidate_sha':SHA,'candidate_tree_sha':'6'*40,
                                  'image_tar_sha256':'5'*64}))
    q.run(SHA,'image-intent',candidate_image_id='sha256:'+'4'*64,
          frozen_manifest=str(frozen))
    for index,name in enumerate(q.OWNED,7):
        states[name]['Id']=str(index)*64
        states[name]['Image']='sha256:'+'4'*64
        states[name]['State'].update(Status='exited',ExitCode=0,OOMKilled=False,
                                      FinishedAt=NOW.isoformat())
    write_marker(q.ROOT,SHA,'sha256:'+'4'*64,'DEPLOYED_VALIDATION_PENDING')
    assert q.run(SHA,'recover')['status']=='FIX_FORWARD_VALIDATION_REQUIRED'
    FrozenDateTime.current=RealDateTime(2026,10,27,23,5,tzinfo=timezone.utc)
    return db,states,calls


def test_failed_candidate_without_provisional_marker_cannot_be_adopted(incident):
    db,states,_=incident
    q.run(SHA,'drain',allow_degraded_closed_rescue=True)
    q.run(SHA,'install',allow_degraded_closed_rescue=True)
    frozen=q.ROOT/'frozen.json'
    frozen.write_text(json.dumps({'candidate_sha':SHA,'candidate_tree_sha':'6'*40,
                                  'image_tar_sha256':'5'*64}))
    q.run(SHA,'image-intent',candidate_image_id='sha256:'+'4'*64,
          frozen_manifest=str(frozen))
    for index,name in enumerate(q.OWNED,7):
        states[name]['Id']=str(index)*64
        states[name]['Image']='sha256:'+'4'*64
        states[name]['State'].update(Status='exited',ExitCode=0,OOMKilled=False,
                                      FinishedAt=NOW.isoformat())
    assert q.run(SHA,'recover')['status']=='FIX_FORWARD_VALIDATION_REQUIRED'
    FrozenDateTime.current=RealDateTime(2026,10,27,23,5,tzinfo=timezone.utc)
    try:
        with pytest.raises(ValueError,match='SOURCE_BOOK_CHANGED'):
            q.adopt_failed_rescue_candidate_stopped(
                NEXT,q.ROOT/'data/deploy',{n:q.inspect(n) for n in q.OWNED})
        assert not (q.ROOT/'data/deploy'/('quiescence-'+NEXT+'.json')).exists()
    finally:
        FrozenDateTime.current=NOW


def test_next_day_adopts_failed_candidate_from_scanner_full_incident(
        incident,monkeypatch):
    db,states,_=failed_candidate_stopped(incident,monkeypatch,from_stopped=True)
    try:
        row=q.adopt_failed_rescue_candidate_stopped(
            NEXT,q.ROOT/'data/deploy',{n:q.inspect(n) for n in q.OWNED})
        assert row['adopted_from_candidate']==SHA
        assert row['adopted_book']['source_sha']==SHA
        assert q.run(NEXT,'drain',allow_adopt_failed_rescue=True)['status']=='DRAINED'
        assert all(states[name]['State']['Status']=='exited' for name in q.OWNED)
    finally:
        FrozenDateTime.current=NOW


def test_next_day_exact_stopped_candidate_adopts_then_only_healthy_closed_passes(incident):
    db,states,calls=failed_candidate_stopped(incident)
    try:
        preview=q.adopt_failed_rescue_candidate_stopped(
            NEXT,q.ROOT/'data/deploy',{n:q.inspect(n) for n in q.OWNED})
        assert preview['adopted_from_candidate']==SHA
        assert preview['adopted_close_proof']['business_day_art']=='2026-10-27'
        receipt=q.run(NEXT,'drain',allow_adopt_failed_rescue=True)
        assert receipt['status']=='DRAINED'
        assert not any(call[1]=='stop' and call[-1]==NEXT for call in calls)
        installed=q.run(NEXT,'install',allow_adopt_failed_rescue=True)
        assert installed['installation_started'] is True
        with sqlite3.connect(db) as c:
            c.execute("UPDATE observer_state SET process_state='WAITING_MARKET',"
                      "session_state='CLOSED',heartbeat_at=?",
                      (FrozenDateTime.now(timezone.utc).isoformat(),))
        assert q.admit_poststart_flat(q.ADOPTED_RESCUE_MODE,NEXT)=='CLOSED'
        prior=q.ROOT/'data/deploy'/('quiescence-'+SHA+'.json')
        prior.write_text(prior.read_text()+' ')
        with pytest.raises(ValueError,match='POSTSTART_PRIOR_CHANGED'):
            q.admit_poststart_flat(q.ADOPTED_RESCUE_MODE,NEXT)
    finally:
        FrozenDateTime.current=NOW


def test_next_deploy_failure_keeps_prior_candidate_timers_paused(incident):
    _,states,calls=failed_candidate_stopped(incident)
    try:
        assert scheduler.require_adopted_rescue_hold(NEXT)=='ADOPTED_RESCUE_NO_DRAIN_HELD'
        q.run(NEXT,'drain',allow_adopt_failed_rescue=True)
        recovered=q.run(NEXT,'recover')
        assert recovered['status']=='PRIOR_FAILED_CANDIDATE_HELD_STOPPED'
        assert recovered['runtime_health']=='PRIOR_FAILED_CANDIDATE_UNVALIDATED'
        assert scheduler.require_adopted_rescue_hold(NEXT)==(
            'ADOPTED_RESCUE_PRIOR_STOPPED_HELD')
        assert all(states[name]['State']['Status']=='exited' for name in q.OWNED)
        assert not any(call[1]=='start' for call in calls)
    finally:
        FrozenDateTime.current=NOW


def test_next_install_failure_holds_exact_prior_stop_and_rejects_new_ids(incident):
    _,states,_=failed_candidate_stopped(incident)
    try:
        q.run(NEXT,'drain',allow_adopt_failed_rescue=True)
        q.run(NEXT,'install',allow_adopt_failed_rescue=True)
        assert q.run(NEXT,'recover')['status']=='FIX_FORWARD_VALIDATION_REQUIRED'
        assert scheduler.require_adopted_rescue_hold(NEXT)==(
            'ADOPTED_RESCUE_PRIOR_STOPPED_HELD')
        states[q.OWNED[0]]['Id']='f'*64
        with pytest.raises(ValueError,match='ADOPTED_RUNTIME_IDENTITY_CHANGED'):
            scheduler.require_adopted_rescue_hold(NEXT)
    finally:
        FrozenDateTime.current=NOW


@pytest.mark.parametrize('change',['prior_hash','frozen_manifest','new_id','dirty_exit','source_a92',
                                 'preopen','november','critical','current_state'])
def test_adoption_rejects_tamper_or_unverified_current_market(incident,change):
    db,states,calls=failed_candidate_stopped(incident)
    prior=q.ROOT/'data/deploy'/('quiescence-'+SHA+'.json')
    if change=='prior_hash':
        row=json.loads(prior.read_text())
        row['candidate_runtime_after_failure']['flat_book']['source_sha']='wrong'
        prior.write_text(json.dumps(row))
    elif change=='frozen_manifest':
        row=json.loads(prior.read_text())
        row['candidate_image_intent']['frozen_manifest_b64']='dGFtcGVyZWQ='
        prior.write_text(json.dumps(row))
    elif change=='new_id': states[q.OWNED[0]]['Id']='f'*64
    elif change=='dirty_exit': states[q.OWNED[0]]['State']['ExitCode']=137
    elif change=='source_a92':
        write_marker(q.ROOT)
    elif change=='preopen': FrozenDateTime.current=RealDateTime(2026,10,28,14,tzinfo=timezone.utc)
    elif change=='november': FrozenDateTime.current=RealDateTime(2026,11,2,23,5,tzinfo=timezone.utc)
    elif change=='critical': states['porota_critical_approval_rc6']['State']['Health']['Status']='unhealthy'
    elif change=='current_state':
        state=q.ROOT/'data/deploy'/'CURRENT_STATE_V2.json'
        state.write_text(json.dumps({'candidate_sha':SHA,'image_id':'sha256:'+'f'*64,
                                     'recorded_at':NOW.isoformat(),
                                     'validation_status':'DEPLOYED_VALIDATION_PENDING'}))
    try:
        with pytest.raises((ValueError,KeyError,TypeError)):
            q.run(NEXT,'drain',allow_adopt_failed_rescue=True)
        assert not (q.ROOT/'data/deploy'/('quiescence-'+NEXT+'.json')).exists()
    finally:
        FrozenDateTime.current=NOW


def test_next_deploy_classifier_uses_exact_prior_stop_readonly(incident,monkeypatch,capsys):
    db,_,calls=failed_candidate_stopped(incident)
    try:
        workflow=(Path(__file__).resolve().parents[1]/
                  '.github/workflows/porota-deploy-v2-promote.yml').read_text()
        block=workflow.split('- name: Classify current flat PAPER session before host writes',1)[1]
        code=block.split("<<'PY'\n",1)[1].split('\n          PY',1)[0]
        code='\n'.join(line.removeprefix('          ') for line in code.splitlines())
        backup=ModuleType('scripts.rc6_679_backup_suspension')
        backup.inventory=lambda:[]
        monkeypatch.setitem(sys.modules,backup.__name__,backup)
        monkeypatch.setitem(sys.modules,'scripts.rc6_deploy_quiescence',q)
        monkeypatch.setattr(sys,'argv',['-',NEXT,''])
        real_connect=sqlite3.connect
        def connect(target,*args,**kwargs):
            if str(target).startswith('file:///opt/porota-trading/data/paper_v17/'):
                target=db.as_uri()+'?mode=ro'
            return real_connect(target,*args,**kwargs)
        monkeypatch.setattr(sqlite3,'connect',connect)
        capsys.readouterr()
        before=len(calls)
        exec(compile(code,'deploy-v2-adopted-classifier','exec'),{})
        assert capsys.readouterr().out==q.ADOPTED_RESCUE_MODE+'\n'
        assert not any(call[1] in {'stop','start'} for call in calls[before:])
    finally:
        FrozenDateTime.current=NOW


@pytest.mark.parametrize('process,session', [('DEGRADED','BOOT_CALENDAR'),
    ('RUNNING','MARKET_OPEN'),('WAITING_MARKET','CHECKING')])
def test_rescue_poststart_never_promotes_degraded_or_open(incident, process, session):
    db, _, _ = incident
    q.run(SHA, 'drain', allow_degraded_closed_rescue=True)
    q.run(SHA, 'install', allow_degraded_closed_rescue=True)
    with sqlite3.connect(db) as c:
        c.execute('UPDATE observer_state SET process_state=?,session_state=?,heartbeat_at=?',
                  (process,session,FrozenDateTime.now(timezone.utc).isoformat()))
    with pytest.raises(ValueError, match='POSTSTART'):
        q.admit_poststart_flat(q.RESCUE_MODE, SHA)
