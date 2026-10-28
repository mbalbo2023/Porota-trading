"""A145: exact #751 running-but-unvalidated fix-forward, not trading GO."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from scripts import rc6_deploy_quiescence as q


SHA = "b" * 40


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
    frozen = {"candidate_sha": q.A143_PENDING_SOURCE_SHA,
              "image_id": q.A143_PENDING_IMAGE_ID,
              "candidate_tree_sha": q.A143_PENDING_TREE_SHA, "image_tar_sha256": "d" * 64}
    marker = {"schema_version": 2, "candidate_sha": q.A143_PENDING_SOURCE_SHA,
              "deploy_sha": q.A143_PENDING_DEPLOY_SHA,
              "image_id": q.A143_PENDING_IMAGE_ID,
              "validation_status": "DEPLOYED_VALIDATION_PENDING",
              "deployment_strategy": "FIX_FORWARD_ONLY", "build_once": True,
              "mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
              "real_order_routes": "NOT_CALLED", "ppi_watch_untouched": True,
              "recorded_at": datetime.now(timezone.utc).isoformat()}
    (folder / "CURRENT_STATE_V2.json").write_text(json.dumps(marker))
    (folder / "porota-frozen-candidate.json").write_text(json.dumps(frozen))
    frozen_hash = q.hashlib.sha256((folder / "porota-frozen-candidate.json").read_bytes()).hexdigest()
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
    _, _, _, states = incident
    proof = q.a143_pending_running_proof()
    assert proof["source_sha"] == q.A143_PENDING_SOURCE_SHA
    assert proof["image_id"] == q.A143_PENDING_IMAGE_ID
    assert proof["flat_book"]["real_orders_sent"] == 0
    assert all(v["State"]["Status"] == "running" for v in states.values())


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
    assert workflow.count("--adopt-a143-running-pending") == 2
    assert "candidate.a143_pending_running_proof(originals)" in workflow
    assert "RC6_A143_FAILED_VALIDATION_RECOVERY=ADMITTED|health=RED|PAPER=0" in workflow


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
