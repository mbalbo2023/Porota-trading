"""A145: exact #751 running-but-unvalidated fix-forward, not trading GO."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import rc6_deploy_quiescence as q


SHA = "b" * 40
REAL_A151_HELD_OPEN_PROOF = q.a151_held_open_proof
REAL_ADOPTED_CLOSE_PROOF = q.adopted_rescue_closed_proof


@pytest.fixture
def incident(tmp_path, monkeypatch):
    root = tmp_path / "porota"
    folder = root / "data/deploy"
    folder.mkdir(parents=True)
    db = root / "data/paper_v17/observer_v17.db"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER,process_state TEXT,session_state TEXT,heartbeat_at TEXT)")
        c.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0,'STOPPED','CLOSED',?)",
                  (datetime.now(timezone.utc).isoformat(),))
        for name in ("paper_positions", "paper_cauciones", "paper_future_positions"):
            c.execute(f"CREATE TABLE {name}(status TEXT)")
    monkeypatch.setattr(q, "ROOT", root)
    monkeypatch.setattr(q, "adopted_rescue_closed_proof", lambda: {"business_day_art": "2026-10-27"})
    monkeypatch.setattr(q, "critical_admission", lambda: {"authority": "ISSUES_ONLY", "orders": 0})
    states = {}
    for idx, name in enumerate(q.OWNED):
        states[name] = {
            "Id": ("a" if idx == 0 else "b") * 64,
            "Image": q.A143_PENDING_IMAGE_ID,
            "State": {"Status": "running", "StartedAt": "2026-10-27T21:00:00Z",
                      "FinishedAt": "2026-10-26T20:00:00Z", "ExitCode": 0,
                      "OOMKilled": False},
        }
    monkeypatch.setattr(q, "inspect", lambda name: json.loads(json.dumps(states[name])))
    # Exact JSON member of the #751 frozen Predeploy artifact 12780414186.
    # Docker load materialized a separate runtime image ID on the Droplet.
    frozen = {"build_once": True,
              "candidate_sha": q.A143_PENDING_SOURCE_SHA,
              "candidate_tree_sha": q.A143_PENDING_TREE_SHA,
              "image_config_sha256": "e4767d51eb86a27a705f1f5bd0a56aceb07df6b1d46c7d772024b48c9cd01cd2",
              "image_id": q.A143_PENDING_FROZEN_IMAGE_ID,
              "image_size_bytes": 810458086,
              "image_tar_sha256": "879a253d1a714216290edc842ba1065c3cbd139561c5f172e048502ba59a250f",
              "paper_mode_required": "PRODUCTION_PAPER",
              "real_order_capability_required": "BLOCKED",
              "real_orders_sent_required": 0,
              "schema_version": 1}
    marker = {"schema_version": 2, "candidate_sha": q.A143_PENDING_SOURCE_SHA,
              "deploy_sha": q.A143_PENDING_DEPLOY_SHA,
              "image_id": q.A143_PENDING_IMAGE_ID,
              "validation_status": "DEPLOYED_VALIDATION_PENDING",
              "deployment_strategy": "FIX_FORWARD_ONLY", "build_once": True,
              "mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
              "real_order_routes": "NOT_CALLED", "ppi_watch_untouched": True,
              "recorded_at": datetime.now(timezone.utc).isoformat()}
    (folder / "CURRENT_STATE_V2.json").write_text(json.dumps(marker))
    (folder / "porota-frozen-candidate.json").write_text(json.dumps(frozen,sort_keys=True,indent=2)+'\n')
    frozen_hash = q.hashlib.sha256((folder / "porota-frozen-candidate.json").read_bytes()).hexdigest()
    assert frozen_hash == q.A143_PENDING_FROZEN_SHA256
    prior = {"candidate_sha": q.A143_PENDING_SOURCE_SHA,
             "status": "FIX_FORWARD_VALIDATION_REQUIRED", "installation_started": True,
             "degraded_closed_rescue": True, "real_orders_sent": 0,
             "ppi_watch_accessed": False,
             "candidate_image_intent": {"image_id": q.A143_PENDING_IMAGE_ID,
                                        "candidate_tree_sha": frozen["candidate_tree_sha"],
                                        "manifest_sha256": frozen_hash},
             "candidate_runtime_after_failure": {
                 "status": "running",
                 "containers": {name: {"id": states[name]["Id"],
                                        "image": states[name]["Image"],
                                        "lifecycle": q.lifecycle(states[name])}
                                for name in q.OWNED},
                 "flat_book": {"source_sha": q.A143_PENDING_SOURCE_SHA,
                               "real_orders_sent": 0}}}
    (folder / ("quiescence-" + q.A143_PENDING_SOURCE_SHA + ".json")).write_text(json.dumps(prior))
    def fake_command(args):
        assert args[:2] == ["docker", "stop"]
        name = args[-1]
        state = states[name]["State"]
        state["Status"] = "exited"
        state["FinishedAt"] = "2026-10-27T23:00:00Z"
        return ""
    monkeypatch.setattr(q, "command", fake_command)
    return root, folder, db, states


def test_a145_exact_running_pending_admits_only_recovery(incident):
    _, folder, _, states = incident
    proof = q.a143_pending_running_proof()
    assert proof["source_sha"] == q.A143_PENDING_SOURCE_SHA
    assert proof["image_id"] == q.A143_PENDING_IMAGE_ID
    assert proof["frozen_sha256"] == q.A143_PENDING_FROZEN_SHA256
    assert json.loads((folder/"porota-frozen-candidate.json").read_text())["image_id"] == \
        q.A143_PENDING_FROZEN_IMAGE_ID
    assert q.A143_PENDING_FROZEN_IMAGE_ID != q.A143_PENDING_IMAGE_ID
    assert proof["flat_book"]["real_orders_sent"] == 0
    assert all(v["State"]["Status"] == "running" for v in states.values())


@pytest.mark.parametrize('mutation', ['frozen_runtime_id', 'frozen_other_id',
                                      'frozen_digest', 'prior_intent',
                                      'runtime_docker_image'])
def test_a143_two_exact_image_ids_and_manifest_hash_fail_closed(
        incident, mutation):
    _,folder,_,states=incident
    frozen_path=folder/'porota-frozen-candidate.json'
    prior_path=folder/('quiescence-'+q.A143_PENDING_SOURCE_SHA+'.json')
    if mutation in {'frozen_runtime_id','frozen_other_id'}:
        frozen=json.loads(frozen_path.read_text())
        frozen['image_id']=(q.A143_PENDING_IMAGE_ID if mutation=='frozen_runtime_id'
                            else 'sha256:'+'f'*64)
        frozen_path.write_text(json.dumps(frozen,sort_keys=True,indent=2)+'\n')
        # Even a forged prior manifest digest cannot override the artifact.
        prior=json.loads(prior_path.read_text())
        prior['candidate_image_intent']['manifest_sha256']=q.hashlib.sha256(
            frozen_path.read_bytes()).hexdigest()
        prior_path.write_text(json.dumps(prior))
    elif mutation=='frozen_digest':
        frozen_path.write_bytes(frozen_path.read_bytes()+b' ')
    elif mutation=='prior_intent':
        prior=json.loads(prior_path.read_text())
        prior['candidate_image_intent']['image_id']=q.A143_PENDING_FROZEN_IMAGE_ID
        prior_path.write_text(json.dumps(prior))
    else:
        states[q.OWNED[0]]['Image']=q.A143_PENDING_FROZEN_IMAGE_ID
    with pytest.raises(ValueError,match='QUIESCENCE_A143_'):
        q.a143_pending_running_proof()
    assert all(v['State']['Status']=='running' for v in states.values())


def test_a143_stopped_continuation_rejects_rewritten_frozen_even_if_proof_rehashed(
        incident):
    _,folder,_,states=incident
    receipt=q.run(SHA,'drain',allow_a143_pending_running=True)
    frozen_path=folder/'porota-frozen-candidate.json'
    frozen=json.loads(frozen_path.read_text())
    frozen['image_id']=q.A143_PENDING_IMAGE_ID
    frozen_path.write_text(json.dumps(frozen,sort_keys=True,indent=2)+'\n')
    receipt['a143_previous_proof']['frozen_sha256']=q.hashlib.sha256(
        frozen_path.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='QUIESCENCE_A143_STOP_SOURCE_CHANGED'):
        q.a143_stopped_continuation(receipt)
    assert all(v['State']['Status']=='exited' for v in states.values())


def test_a145_observed_degraded_market_closed_is_exact_preflight_and_poststop(incident):
    """A145 read-only #40614093115 saw this exact pair, not BOOT_CALENDAR."""
    _,_,db,states=incident
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET process_state='DEGRADED',session_state='MARKET_CLOSED'")
    proof=q.a143_pending_running_proof()
    assert proof['flat_book']['process_state']=='DEGRADED'
    assert proof['flat_book']['session_state']=='MARKET_CLOSED'
    receipt=q.run(SHA,'drain',allow_a143_pending_running=True)
    assert receipt['status']=='DRAINED'
    assert all(states[name]['State']['Status']=='exited' for name in q.OWNED)
    # The DEGRADED parent may ACK SIGTERM with STOPPED, while remaining CLOSED.
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET process_state='STOPPED',session_state='MARKET_CLOSED'")
    assert q.run(SHA,'install',allow_a143_pending_running=True)['installation_started'] is True


@pytest.mark.parametrize('process,session', [
    ('DEGRADED','MARKET_OPEN'),('DEGRADED','CHECKING'),
    ('WAITING_MARKET','MARKET_CLOSED'),('STOPPED','MARKET_CLOSED'),
    ('RUNNING','MARKET_OPEN'),('RUNNING','CLOSED'),
])
def test_a145_unknown_state_pair_rejects_even_if_paper_flat(incident,process,session):
    _,_,db,states=incident
    with sqlite3.connect(db) as c:
        c.execute('UPDATE observer_state SET process_state=?,session_state=?',(process,session))
    with pytest.raises(ValueError,match='CLOSED_FLAT_BOOK_RED'):
        q.run(SHA,'drain',allow_a143_pending_running=True)
    assert all(states[name]['State']['Status']=='running' for name in q.OWNED)


@pytest.mark.parametrize("target,key,wrong", [
    ("marker", "candidate_sha", "e" * 40),
    ("marker", "deploy_sha", "e" * 40),
    ("marker", "image_id", "sha256:" + "e" * 64),
    ("marker", "validation_status", "VALIDATED_RUNTIME"),
    ("marker", "real_orders_sent", 1),
    ("marker", "real_order_routes", "CALLED"),
    ("frozen", "candidate_sha", "e" * 40),
    ("frozen", "image_id", "sha256:" + "e" * 64),
    ("frozen", "candidate_tree_sha", "e" * 40),
    ("prior", "status", "DRAINED"),
    ("prior", "installation_started", False),
    ("intent", "image_id", "sha256:" + "e" * 64),
    ("runtime", "status", "exited"),
])
def test_a145_marker_image_source_or_receipt_mismatch_never_stops(
        incident, target, key, wrong):
    _, folder, _, states = incident
    paths = {"marker": folder / "CURRENT_STATE_V2.json",
             "frozen": folder / "porota-frozen-candidate.json",
             "prior": folder / ("quiescence-" + q.A143_PENDING_SOURCE_SHA + ".json")}
    path = paths["prior"] if target in {"intent", "runtime"} else paths[target]
    value = json.loads(path.read_text())
    container = (value["candidate_image_intent"] if target == "intent" else
                 value["candidate_runtime_after_failure"] if target == "runtime" else value)
    container[key] = wrong
    path.write_text(json.dumps(value))
    with pytest.raises((ValueError, KeyError)):
        q.run(SHA, "drain", allow_a143_pending_running=True)
    assert all(v["State"]["Status"] == "running" for v in states.values())


@pytest.mark.parametrize("changed", ["docker_id", "docker_image", "session_open", "paper_order", "stale_heartbeat", "spot", "caucion", "future"])
def test_a145_mismatched_runtime_or_paper_ledger_fails_before_stop(incident, changed):
    _, _, db, states = incident
    if changed == "docker_id":
        states[q.OWNED[0]]["Id"] = "f" * 64
    elif changed == "docker_image":
        states[q.OWNED[0]]["Image"] = "sha256:" + "f" * 64
    else:
        with sqlite3.connect(db) as c:
            if changed == "session_open":
                c.execute("UPDATE observer_state SET process_state='RUNNING',session_state='MARKET_OPEN'")
            elif changed == "paper_order":
                c.execute("UPDATE observer_state SET real_orders_sent=1")
            elif changed == "stale_heartbeat":
                c.execute("UPDATE observer_state SET heartbeat_at='2026-10-20T00:00:00+00:00'")
            else:
                table = {"spot":"paper_positions", "caucion":"paper_cauciones",
                         "future":"paper_future_positions"}[changed]
                c.execute(f"INSERT INTO {table} VALUES(?)",
                          ("ACTIVE" if changed == "future" else "OPEN",))
    with pytest.raises((ValueError, KeyError)):
        q.run(SHA, "drain", allow_a143_pending_running=True)
    assert all(v["State"]["Status"] == "running" for v in states.values())


def test_a145_drain_install_exact_proof_and_no_second_stop(incident):
    _, folder, _, states = incident
    receipt = q.run(SHA, "drain", allow_a143_pending_running=True)
    assert receipt["status"] == "DRAINED"
    assert set(receipt["stop_evidence"]) == set(q.OWNED)
    assert all(v["State"]["Status"] == "exited" for v in states.values())
    assert q.run(SHA, "drain", allow_a143_pending_running=True)["status"] == "DRAINED"
    assert q.run(SHA, "install", allow_a143_pending_running=True)["status"] == "HOST_INSTALLATION_STARTED"
    assert json.loads((folder / ("quiescence-" + SHA + ".json")).read_text())["installation_started"] is True


def new_a143_normalized_failure(
        incident,monkeypatch,tmp_path):
    from scripts import rc6_deploy_scheduler_suspend as scheduler
    root,folder,db,states=incident
    q.run(SHA,'drain',allow_a143_pending_running=True)
    q.run(SHA,'install',allow_a143_pending_running=True)
    frozen_new={'candidate_sha':SHA,'candidate_tree_sha':'c'*40,
                'image_id':'sha256:'+'d'*64,'image_tar_sha256':'e'*64,
                'paper_mode_required':'PRODUCTION_PAPER','real_orders_sent_required':0}
    raw=json.dumps(frozen_new,sort_keys=True).encode()
    new_path=tmp_path/'new-frozen.json';new_path.write_bytes(raw)
    loaded='sha256:'+'f'*64
    monkeypatch.setattr(q,'candidate_operator_module',lambda **_:SimpleNamespace(
        exact_a151_operator_preopen_pause=lambda:None,
        operator_preopen_timer_disabled=lambda:True))
    monkeypatch.setattr(q,'command',lambda args: json.dumps([{'Id':loaded}])
                        if args[:3]==['docker','image','inspect'] else '')
    receipt=q.run(SHA,'image-intent',candidate_image_id=loaded,
                  frozen_manifest=new_path)
    intent=receipt['candidate_image_intent']
    assert receipt['status']=='HOST_INSTALLATION_STARTED'
    assert intent['manifest_sha256']==q.hashlib.sha256(raw).hexdigest()
    assert intent['image_id']==loaded!=frozen_new['image_id']
    # A failure before any new container stays an old stopped hold; the
    # durable intent alone never claims that a new runtime was installed.
    before=q.run(SHA,'recover')
    assert before['status']=='FIX_FORWARD_VALIDATION_REQUIRED'
    assert before['prior_a143_stopped_after_install'] is True
    assert before.get('candidate_runtime_after_failure') is None
    monkeypatch.setattr(scheduler,'a143_staged_module',lambda:q)
    monkeypatch.setattr(scheduler,'exact_a151_operator_preopen_pause',lambda:None)
    monkeypatch.setattr(scheduler,'operator_preopen_timer_disabled',lambda:True)
    assert scheduler.require_a143_fix_forward_hold(SHA)== \
        'A143_PRIOR_UNVALIDATED_STOPPED_HELD'

    # A subsequent canonical attempt can start only the exact intended new
    # pair; this fixture models its poststart failure and pending marker.
    for idx,name in enumerate(q.OWNED):
        states[name]['Id']=('1' if idx==0 else '2')*64
        states[name]['Image']=loaded
        states[name]['State']['Status']='running'
        states[name]['State']['StartedAt']=datetime.now(timezone.utc).isoformat()
    marker=json.loads((folder/'CURRENT_STATE_V2.json').read_text())
    marker.update(candidate_sha=SHA,image_id=loaded,
                  recorded_at=datetime.now(timezone.utc).isoformat())
    (folder/'CURRENT_STATE_V2.json').write_text(json.dumps(marker))
    (folder/'porota-frozen-candidate.json').write_bytes(raw)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE observer_state SET process_state='DEGRADED',session_state='BOOT_CALENDAR'")
    monkeypatch.setattr(q,'critical_admission',lambda:
                        {'capability':'issues_only','real_orders_sent':0})
    # Reestablish the pre-failure receipt state as it would be before the
    # recover callback in the same attempt; the first branch above tested
    # old-stopped behavior independently.
    path=folder/('quiescence-'+SHA+'.json')
    old=json.loads(path.read_text())
    old['status']='HOST_INSTALLATION_STARTED';old['installation_started']=True
    old.pop('prior_a143_stopped_after_install',None)
    path.write_text(json.dumps(old))
    after=q.run(SHA,'recover')
    assert after['status']=='FIX_FORWARD_VALIDATION_REQUIRED'
    assert after['candidate_runtime_after_failure']['status']=='running'
    assert scheduler.require_a143_fix_forward_hold(SHA)== \
        'A145_NEW_CANDIDATE_UNVALIDATED_HELD'
    return root,folder,states,loaded


def test_a143_new_image_intent_precedes_runtime_and_binds_normalized_id(
        incident,monkeypatch,tmp_path):
    new_a143_normalized_failure(incident,monkeypatch,tmp_path)


@pytest.mark.parametrize('mutation', ['missing_intent','invalid_base64',
                                      'forged_manifest_hash',
                                      'frozen_changed','marker_image',
                                      'docker_image','docker_restart',
                                      'runtime_proof_missing','critical_drift'])
def test_a143_new_failure_hold_rejects_unbound_provenance(
        incident,monkeypatch,tmp_path,mutation):
    from scripts import rc6_deploy_scheduler_suspend as scheduler
    _,folder,states,_=new_a143_normalized_failure(incident,monkeypatch,tmp_path)
    receipt_path=folder/('quiescence-'+SHA+'.json')
    marker_path=folder/'CURRENT_STATE_V2.json'
    frozen_path=folder/'porota-frozen-candidate.json'
    receipt=json.loads(receipt_path.read_text())
    if mutation=='missing_intent':
        receipt.pop('candidate_image_intent')
    elif mutation=='invalid_base64':
        receipt['candidate_image_intent']['frozen_manifest_b64']='!not-base64'
    elif mutation=='forged_manifest_hash':
        receipt['candidate_image_intent']['manifest_sha256']='0'*64
    elif mutation=='frozen_changed':
        frozen=json.loads(frozen_path.read_text())
        frozen['image_id']='sha256:'+'0'*64
        frozen_path.write_text(json.dumps(frozen,sort_keys=True))
    elif mutation=='marker_image':
        marker=json.loads(marker_path.read_text())
        marker['image_id']='sha256:'+'0'*64
        marker_path.write_text(json.dumps(marker))
    elif mutation=='docker_image':
        states[q.OWNED[0]]['Image']='sha256:'+'0'*64
    elif mutation=='docker_restart':
        states[q.OWNED[0]]['State']['StartedAt']=datetime.now(timezone.utc).isoformat()
    elif mutation=='critical_drift':
        monkeypatch.setattr(q,'critical_admission',lambda:
                            {'capability':'issues_only','real_orders_sent':0,
                             'image':'sha256:'+'0'*64})
    else:
        receipt.pop('candidate_runtime_after_failure')
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError,match='SCHEDULER_A143_NEW_|SCHEDULER_A143_NEW_PAPER_BOOK_RED'):
        scheduler.require_a143_fix_forward_hold(SHA)


def test_a143_failure_after_new_pair_without_prior_intent_never_claims_runtime(
        incident,monkeypatch):
    _,folder,_,states=incident
    q.run(SHA,'drain',allow_a143_pending_running=True)
    q.run(SHA,'install',allow_a143_pending_running=True)
    for idx,name in enumerate(q.OWNED):
        states[name]['Id']=('1' if idx==0 else '2')*64
        states[name]['Image']='sha256:'+'f'*64
        states[name]['State']['Status']='running'
    receipt=q.run(SHA,'recover')
    assert receipt['status']=='RECOVERY_UNVERIFIED'
    assert receipt.get('candidate_runtime_after_failure') is None


def test_a145_poststop_receipt_mutation_fails_before_install(incident):
    _, folder, _, states = incident
    q.run(SHA, "drain", allow_a143_pending_running=True)
    marker = folder / "CURRENT_STATE_V2.json"
    value = json.loads(marker.read_text())
    value["image_id"] = "sha256:" + "f" * 64
    marker.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="SOURCE_CHANGED"):
        q.run(SHA, "install", allow_a143_pending_running=True)
    assert all(v["State"]["Status"] == "exited" for v in states.values())


def test_a145_classifier_and_drain_flag_wiring_is_exact():
    from pathlib import Path
    workflow = (Path(__file__).parents[1] / ".github/workflows/porota-deploy-v2-promote.yml").read_text()
    assert "CLOSED_A143_PENDING_FIX_FORWARD" in workflow
    assert workflow.count("--adopt-a143-running-pending") == 4
    assert "candidate.a143_pending_running_proof(" in workflow
    assert "originals,allow_a151_held_open=True" in workflow
    assert "print('A151_HELD_OPEN' if proof.get('operator_hold')" in workflow
    assert "RC6_A143_FAILED_VALIDATION_RECOVERY=ADMITTED|health=RED|PAPER=0" in workflow


class A151OpenClock(datetime):
    @classmethod
    def now(cls, tz=None):
        value=datetime(2026,10,28,14,4,tzinfo=timezone.utc)
        return value if tz is None else value.astimezone(tz)


def a151_open_incident(incident, monkeypatch):
    root,folder,db,states=incident
    (root/'ak_byma_calendar.py').write_bytes(
        (Path(__file__).parents[1]/'ak_byma_calendar.py').read_bytes())
    monkeypatch.setattr(q,'datetime',A151OpenClock)
    marker_path=folder/'CURRENT_STATE_V2.json'
    marker=json.loads(marker_path.read_text())
    marker['recorded_at']='2026-10-28T13:00:00+00:00'
    marker_path.write_text(json.dumps(marker))
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET process_state='DEGRADED',session_state='BOOT_CALENDAR',heartbeat_at='2026-10-28T14:03:55+00:00'")
    hold={'admission':q.A151_HELD_OPEN_MODE,'business_day_art':'2026-10-28',
          'operator_receipt_sha256':'e'*64,
          'operator_operation_id':'A151-40686116017-1',
          'paper_execution_authorized':False}
    monkeypatch.setattr(q,'a151_held_open_proof',lambda **_:dict(hold))
    monkeypatch.setattr(q,'candidate_operator_module',lambda **_:SimpleNamespace(
        exact_a151_operator_preopen_pause=lambda:('e'*64,'A151-40686116017-1'),
        operator_preopen_timer_disabled=lambda:True))
    return root,folder,db,states


def test_a151_held_open_exact_a143_flat_drain_and_stopped_boot_calendar(
        incident,monkeypatch):
    _,_,db,states=a151_open_incident(incident,monkeypatch)
    proof=q.a143_pending_running_proof(allow_a151_held_open=True)
    assert proof['operator_hold']['admission']==q.A151_HELD_OPEN_MODE
    assert proof['close'] is None
    original_command=q.command
    def stop_with_scanner_ack(args):
        result=original_command(args)
        if args[-1]=='porota_production_observer':
            with sqlite3.connect(db) as c:
                c.execute("UPDATE observer_state SET process_state='STOPPED'")
        return result
    monkeypatch.setattr(q,'command',stop_with_scanner_ack)
    receipt=q.run(SHA,'drain',allow_a143_pending_running=True,
                  allow_a151_held_open=True)
    assert receipt['status']=='DRAINED'
    assert receipt['a143_previous_proof']['operator_hold']==proof['operator_hold']
    assert all(states[name]['State']['Status']=='exited' for name in q.OWNED)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET session_state='CLOSED'")
    with pytest.raises(ValueError,match='BOOK_OR_CRITICAL_CHANGED'):
        q.a143_stopped_continuation(receipt)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET session_state='BOOT_CALENDAR'")
    with pytest.raises(ValueError,match='RECEIPT_MODE_CHANGED'):
        q.run(SHA,'install',allow_a143_pending_running=True)
    assert q.run(SHA,'install',allow_a143_pending_running=True,
                 allow_a151_held_open=True)['installation_started'] is True


@pytest.mark.parametrize('mutation', ['wrong_session','paper_order','spot','caucion',
                                     'future','stale_heartbeat','marker','docker_id',
                                     'missing_hold','preopen_rearmed'])
def test_a151_held_open_adversarial_fails_before_docker_stop(
        incident,monkeypatch,mutation):
    _,folder,db,states=a151_open_incident(incident,monkeypatch)
    if mutation=='docker_id':
        states[q.OWNED[0]]['Id']='f'*64
    elif mutation=='marker':
        path=folder/'CURRENT_STATE_V2.json'; value=json.loads(path.read_text())
        value['real_order_routes']='CALLED';path.write_text(json.dumps(value))
    elif mutation=='missing_hold':
        monkeypatch.setattr(q,'a151_held_open_proof',lambda **_:(_ for _ in ()).throw(
            ValueError('A151_RECEIPT_MISSING')))
    elif mutation=='preopen_rearmed':
        monkeypatch.setattr(q,'a151_held_open_proof',lambda **_:
            REAL_A151_HELD_OPEN_PROOF(
                operator_a151_proof=lambda:('e'*64,'A151-40686116017-1'),
                operator_timer_disabled=lambda:False))
    else:
        with sqlite3.connect(db) as c:
            if mutation=='wrong_session':
                c.execute("UPDATE observer_state SET session_state='MARKET_OPEN'")
            elif mutation=='paper_order':
                c.execute('UPDATE observer_state SET real_orders_sent=1')
            elif mutation=='stale_heartbeat':
                c.execute("UPDATE observer_state SET heartbeat_at='2026-10-27T14:00:00+00:00'")
            else:
                name={'spot':'paper_positions','caucion':'paper_cauciones',
                      'future':'paper_future_positions'}[mutation]
                c.execute(f'INSERT INTO {name} VALUES(?)',
                          ('ACTIVE' if mutation=='future' else 'OPEN',))
    with pytest.raises((ValueError,KeyError)):
        q.run(SHA,'drain',allow_a143_pending_running=True,
              allow_a151_held_open=True)
    assert all(states[name]['State']['Status']=='running' for name in q.OWNED)


def test_a151_1700_crossing_allows_only_existing_held_cleanup_not_new_drain(
        incident,monkeypatch):
    _,_,db,states=a151_open_incident(incident,monkeypatch)
    class AfterOpen(datetime):
        @classmethod
        def now(cls,tz=None):
            value=datetime(2026,10,28,20,5,tzinfo=timezone.utc)
            return value if tz is None else value.astimezone(tz)
    monkeypatch.setattr(q,'datetime',AfterOpen)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET heartbeat_at='2026-10-28T20:04:55+00:00'")
    monkeypatch.setattr(q,'a151_held_open_proof',REAL_A151_HELD_OPEN_PROOF)
    hold={'admission':q.A151_HELD_OPEN_MODE,'business_day_art':'2026-10-28',
          'operator_receipt_sha256':'e'*64,
          'operator_operation_id':'A151-40686116017-1',
          'paper_execution_authorized':False}
    with pytest.raises(ValueError,match='PHASE_CHANGED'):
        q.run(SHA,'drain',allow_a143_pending_running=True,
              allow_a151_held_open=True)
    assert all(v['State']['Status']=='running' for v in states.values())
    proof=q.a143_pending_running_proof(allow_a151_held_open=True,
                                      held_continuation=hold)
    assert proof['operator_hold']['operator_receipt_sha256']=='e'*64
    with pytest.raises(ValueError,match='CONTINUATION_RECEIPT_CHANGED'):
        q.a143_pending_running_proof(allow_a151_held_open=True,
            held_continuation=dict(hold,operator_receipt_sha256='f'*64))


def test_a151_mode_does_not_block_independent_29_postclose_or_admit_29_open(
        incident,monkeypatch):
    root,_,db,_=incident
    (root/'ak_byma_calendar.py').write_bytes(
        (Path(__file__).parents[1]/'ak_byma_calendar.py').read_bytes())
    monkeypatch.setattr(q,'adopted_rescue_closed_proof',REAL_ADOPTED_CLOSE_PROOF)
    class Day29Closed(datetime):
        @classmethod
        def now(cls,tz=None):
            value=datetime(2026,10,29,23,30,tzinfo=timezone.utc)
            return value if tz is None else value.astimezone(tz)
    monkeypatch.setattr(q,'datetime',Day29Closed)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET heartbeat_at='2026-10-29T23:29:55+00:00'")
    proof=q.a143_pending_running_proof(allow_a151_held_open=True)
    assert proof['close']['business_day_art']=='2026-10-29'
    assert proof['operator_hold'] is None
    class Day29Open(datetime):
        @classmethod
        def now(cls,tz=None):
            value=datetime(2026,10,29,14,0,tzinfo=timezone.utc)
            return value if tz is None else value.astimezone(tz)
    monkeypatch.setattr(q,'datetime',Day29Open)
    with pytest.raises(ValueError,match='CLOSE_WINDOW_RED'):
        q.a143_pending_running_proof(allow_a151_held_open=True)


@pytest.mark.parametrize('utc_clock,admitted', [
    ((2026,10,28,2,59),True),  # Oct27 23:59 ART: exact postclose.
    ((2026,10,28,3,0),False),  # Oct28 00:00 ART: a different day.
    ((2026,10,28,23,0),True),  # Oct28 20:00 ART: next independently postclosed day.
])
def test_a145_com18782_postclose_does_not_reuse_prior_art_day(
        tmp_path,monkeypatch,utc_clock,admitted):
    from datetime import datetime as ActualDateTime
    source = ("from datetime import date\nCALENDARIO_ACTUALIZADO_EL=date(2026,10,14)\n"
              "ANIOS_AUDITADOS={2026}\n"
              "def es_dia_habil_operativo(d): return True\n")
    calendar=tmp_path/'ak_byma_calendar.py'
    calendar.write_text(source)
    monkeypatch.setattr(q,'ROOT',tmp_path)
    monkeypatch.setattr(q,'RESCUE_CALENDAR_SHA256',q.hashlib.sha256(calendar.read_bytes()).hexdigest())
    class Clock(ActualDateTime):
        @classmethod
        def now(cls,tz=None):
            return ActualDateTime(*utc_clock,tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(q,'datetime',Clock)
    if admitted:
        assert q.adopted_rescue_closed_proof()['calendar_sha256']==q.RESCUE_CALENDAR_SHA256
    else:
        with pytest.raises(ValueError,match='CLOSE_WINDOW_RED'):
            q.adopted_rescue_closed_proof()
