"""A111: the observed 26 October A92 degraded/flat runtime is not healthy."""
import base64
from datetime import date as RealDate, datetime as RealDateTime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from types import ModuleType

import pytest

from scripts import rc6_deploy_quiescence as q
from scripts import rc6_deploy_scheduler_suspend as scheduler


SHA = 'a' * 40
NOW = RealDateTime(2026, 10, 26, 23, 5, tzinfo=timezone.utc)


class FrozenDateTime(RealDateTime):
    current = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


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
        c.execute('''CREATE TABLE observer_state(id,mode,real_orders_sent,process_state,
                     session_state,heartbeat_at,source_sha)''')
        c.execute('INSERT INTO observer_state VALUES(1,?,?,?,?,?,?)',
                  ('PRODUCTION_PAPER', 0, 'DEGRADED', 'BOOT_CALENDAR',
                   (NOW-timedelta(seconds=5)).isoformat(), q.RESCUE_SOURCE_SHA))
        for table in ('paper_positions', 'paper_cauciones', 'paper_future_positions'):
            c.execute('CREATE TABLE ' + table + '(status)')
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
                  "heartbeat_at=?,source_sha=?", (FrozenDateTime.now(timezone.utc).isoformat(), SHA))
    assert q.admit_poststart_flat(q.RESCUE_MODE, SHA) == 'CLOSED'


@pytest.mark.parametrize('change', ['early', 'next_day', 'calendar', 'source', 'image',
    'heartbeat', 'open_spot', 'open_caucion', 'active_future', 'real_orders', 'critical'])
def test_uncertain_incident_cannot_stop_either_container(incident, change):
    db, states, calls = incident
    if change == 'early': FrozenDateTime.current = RealDateTime(2026,10,26,22,59,tzinfo=timezone.utc)
    elif change == 'next_day': FrozenDateTime.current = RealDateTime(2026,10,27,3,0,tzinfo=timezone.utc)
    elif change == 'calendar': (q.ROOT/'ak_byma_calendar.py').write_text('tampered')
    elif change == 'image': states[q.OWNED[1]]['Image'] = 'sha256:'+'f'*64
    elif change == 'critical': states['porota_critical_approval_rc6']['State']['Health']['Status'] = 'unhealthy'
    else:
        with sqlite3.connect(db) as c:
            if change == 'source': c.execute("UPDATE observer_state SET source_sha='wrong'")
            elif change == 'heartbeat': c.execute("UPDATE observer_state SET heartbeat_at='2020-01-01T00:00:00+00:00'")
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
        'RESCUE_NO_DRAIN_ORIGINAL_DEGRADED_HELD')
    assert not any(call[1] in {'stop', 'start'} for call in calls)
    FrozenDateTime.current = RealDateTime(2026,10,27,3,0,tzinfo=timezone.utc)
    try:
        with pytest.raises(ValueError,match='RESCUE_WINDOW_RED'):
            scheduler.require_rescue_degraded_hold(SHA)
    finally:
        FrozenDateTime.current = NOW


def test_cleanup_no_drain_persists_explicit_hold_without_timer_start(incident,monkeypatch):
    names=['porota-preopen-rc6.timer','porota-runtime-evidence-rc6.timer']
    raw=json.dumps({'units':{'systemd/'+n:{'role':'ACTIVE_TIMER','active':True}
                             for n in names}}).encode()
    policy=base64.b64encode(raw).decode()
    target=q.ROOT/'data/deploy'
    target.mkdir(parents=True)
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
        'RESCUE_NO_DRAIN_ORIGINAL_DEGRADED_HELD')
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
    assert 'RC6_PAPER_SESSION=RESCUE_ADMITTED|runtime=DEGRADED|health=RED' in workflow


def test_candidate_image_intent_and_exact_exited_replacement_hold(incident):
    db, states, _ = incident
    q.run(SHA, 'drain', allow_degraded_closed_rescue=True)
    q.run(SHA, 'install', allow_degraded_closed_rescue=True)
    frozen = q.ROOT/'frozen.json'
    frozen.write_text(json.dumps({'candidate_sha':SHA,'image_tar_sha256':'5'*64}))
    intent = q.run(SHA,'image-intent',candidate_image_id='sha256:'+'4'*64,
                   frozen_manifest=str(frozen))['candidate_image_intent']
    assert intent['image_tar_sha256'] == '5'*64
    for index,name in enumerate(q.OWNED, 7):
        states[name]['Id'] = str(index)*64
        states[name]['Image'] = intent['image_id']
        states[name]['State'].update(Status='exited',ExitCode=0,OOMKilled=False,
                                      FinishedAt=NOW.isoformat())
    with sqlite3.connect(db) as c:
        c.execute('UPDATE observer_state SET source_sha=?',(SHA,))
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
    frozen.write_text(json.dumps({'candidate_sha':SHA,'image_tar_sha256':'5'*64}))
    q.run(SHA,'image-intent',candidate_image_id='sha256:'+'4'*64,
          frozen_manifest=str(frozen))
    receipt=q.run(SHA,'recover')
    assert receipt['status']=='FIX_FORWARD_VALIDATION_REQUIRED'
    assert receipt.get('candidate_runtime_after_failure') is None
    assert scheduler.require_rescue_degraded_hold(SHA)=='RESCUE_DEGRADED_HELD'


@pytest.mark.parametrize('change',['missing_intent','mixed_image','unclean_exit'])
def test_failed_replacement_never_rearms_without_provenance(incident,change):
    _,states,_=incident
    q.run(SHA,'drain',allow_degraded_closed_rescue=True)
    q.run(SHA,'install',allow_degraded_closed_rescue=True)
    if change != 'missing_intent':
        frozen=q.ROOT/'frozen.json'
        frozen.write_text(json.dumps({'candidate_sha':SHA,'image_tar_sha256':'5'*64}))
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
    assert 'RESCUE_ADMITTED|runtime=DEGRADED|health=RED' in block
    assert 'RC6_RESCUE_DISK_PREFLIGHT=RED|no cleanup or host transfer' in workflow


@pytest.mark.parametrize('source_ok', [True, False])
def test_pretransfer_classifier_replays_observed_host_without_writes(
        incident, monkeypatch, capsys, source_ok):
    db, states, _ = incident
    if not source_ok:
        with sqlite3.connect(db) as c:
            c.execute("UPDATE observer_state SET source_sha='wrong'")
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
        with pytest.raises(SystemExit,match='RC6_RESCUE_PAPER_SOURCE_RED'):
            exec(compile(code,'deploy-v2-rescue-classifier','exec'),{})
        assert critical == []


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
