"""The deployed observer command must retain the complete PAPER controller."""
from contextlib import redirect_stdout
import importlib
import io
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from scripts import porota_rootless_host as host


ROOT = Path(__file__).resolve().parents[1]
SHA, TREE = "a" * 40, "b" * 40
CHILDREN = ("scanner", "performance", "dynamic_shadow", "exit_reader", "notifications",
            "candles", "intraday_scalping", "caucion_cash_sweep")
NOW = datetime(2026, 11, 15, 18, 0, tzinfo=timezone.utc)
STARTED = NOW - timedelta(seconds=30)


def controller_snapshot():
    return {"schema": "rc6.runtime-child-health.v1", "source_sha": SHA,
            "candidate_tree_sha": TREE, "recorded_at": NOW.isoformat(), "roles_match": True,
            "children": {name: {"state": "RUNNING", "pid": 100 + index,
                                "started_at": (STARTED + timedelta(seconds=1)).isoformat(),
                                "alive": True}
                         for index, name in enumerate(CHILDREN)}}


def test_compose_observer_plans_all_workers_under_the_exit_clock(tmp_path, monkeypatch):
    """Changing Compose to the scanner alone must fail even when it stays alive."""
    from be_paper_engine import PaperStore

    command = yaml.safe_load((ROOT / "compose.rootless.yml").read_text())["services"]["observer"]["command"]
    assert command[0] == "python" and len(command) == 2
    entry = importlib.import_module(Path(command[1]).stem)
    assert callable(getattr(entry, "main", None)), "Compose must start the PAPER controller, not the scanner alone"
    store = PaperStore(str(tmp_path / "paper.db"))
    environment, _ = host.runtime_environments({}, {"DASHBOARD_ACCESS_TOKEN": "fixture-only"}, SHA, TREE)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PAPER_V17_DB_PATH", store.path)
    monkeypatch.setenv("POROTA_RUNTIME_SCHEMA_READY", "")
    monkeypatch.setattr(entry, "runtime_store", lambda: store)
    monkeypatch.setattr(signal, "signal", lambda *_: None)
    clocks = []
    monkeypatch.setattr(entry, "run_clock", lambda current, children, stop: clocks.append((current, children)))
    assert entry.main([]) == 0
    assert len(clocks) == 1 and clocks[0][0] is store
    children = clocks[0][1]
    assert children.commands == {
        "scanner": [sys.executable, str(ROOT / "bf_production_paper_observer.py")],
        "performance": [sys.executable, str(ROOT / "bv_paper_runtime.py"), "--performance-worker"],
        "dynamic_shadow": [sys.executable, str(ROOT / "bv_paper_runtime.py"), "--dynamic-shadow-worker"],
        "exit_reader": [sys.executable, str(ROOT / "bv_paper_runtime.py"), "--exit-reader"],
        "notifications": [sys.executable, str(ROOT / "bv_paper_runtime.py"), "--notification-worker"],
        "candles": [sys.executable, str(ROOT / "bv_paper_runtime.py"), "--candle-worker"],
        "intraday_scalping": [sys.executable, str(ROOT / "bv_paper_runtime.py"), "--intraday-scalping-worker"],
        "caucion_cash_sweep": [sys.executable, str(ROOT / "bv_paper_runtime.py"), "--caucion-cash-sweep-worker"],
    }
    assert not children.processes  # A plan test never claims actual child startup.


def classify(health):
    check = getattr(host, "classify_controller", None)
    assert callable(check), "Postflight needs current controller and child evidence"
    return check(health, source_sha=SHA, source_tree=TREE, now=NOW, started=STARTED)


def test_controller_accepts_only_current_live_topology():
    result = classify(controller_snapshot())
    assert result["status"] == "READY"
    assert result["entrypoint"] == "bv_paper_runtime.py"
    assert result["children"] == sorted(CHILDREN)
    assert result["source_sha"] == SHA and result["source_tree"] == TREE
    assert "real_orders_sent" not in result  # The database remains the counter authority.


@pytest.mark.parametrize("change", [
    lambda item: None,
    lambda item: dict(item, schema="old-schema"),
    lambda item: dict(item, source_sha="c" * 40),
    lambda item: dict(item, candidate_tree_sha="d" * 40),
    lambda item: dict(item, recorded_at=(STARTED - timedelta(seconds=1)).isoformat()),
    lambda item: dict(item, recorded_at=(NOW + timedelta(seconds=1)).isoformat()),
    lambda item: dict(item, recorded_at="invalid"),
    lambda item: dict(item, roles_match=False),
    lambda item: dict(item, children={}),
    lambda item: dict(item, children=dict(item["children"], scanner={"state": "STARTUP_WAIT"})),
    lambda item: dict(item, children=dict(item["children"], scanner=dict(item["children"]["scanner"], alive=False))),
    lambda item: dict(item, children=dict(item["children"], scanner=dict(item["children"]["scanner"], pid=True))),
    lambda item: dict(item, children=dict(item["children"], scanner=dict(item["children"]["scanner"], started_at=STARTED.replace(year=2025).isoformat()))),
])
def test_old_missing_or_incomplete_controller_evidence_waits_without_acceptance(change):
    result = classify(change(controller_snapshot()))
    assert result["status"] == "PENDING"
    assert result["reason"].startswith("CONTROLLER_")


def test_snapshot_checks_live_pid_and_does_not_export_private_or_counter_fields(tmp_path):
    read = getattr(host, "read_controller_health", None)
    assert callable(read), "The observer probe must observe child liveness"
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=5)
    raw = controller_snapshot()
    raw.update(real_orders_sent=0, private="must-not-leak")
    raw["children"]["scanner"].update(pid=os.getpid(), last_error="private-error")
    raw["children"]["exit_reader"]["pid"] = child.pid
    path = tmp_path / "runtime-health.json"
    path.write_text(json.dumps(raw))
    observed = read(path)
    assert observed["children"]["scanner"]["alive"] is True
    assert observed["children"]["exit_reader"]["alive"] is False
    assert "private" not in json.dumps(observed)
    assert "real_orders_sent" not in observed
    path.unlink()
    assert read(path) is None
    path.write_text("not-json")
    assert read(path) is None
    path.unlink()
    target = tmp_path / "other.json"
    target.write_text(json.dumps(raw))
    path.symlink_to(target)
    assert read(path) is None


@pytest.mark.parametrize("state", ("T", "t", "Z", "X"))
def test_suspended_or_zombie_children_are_not_reported_alive(tmp_path, monkeypatch, state):
    path = tmp_path / "runtime-health.json"
    path.write_text(json.dumps(controller_snapshot()))
    monkeypatch.setattr("builtins.open", lambda *_, **__: io.StringIO(f"101 (fixture) {state} 0 0"))
    assert host.read_controller_health(path)["children"]["scanner"]["alive"] is False


def test_real_runtime_probe_connects_controller_evidence_without_overriding_database_counter(tmp_path, monkeypatch):
    database = tmp_path / "paper.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE observer_state(id,mode,real_orders_sent,ppi_auth,process_state,session_state,last_market_data_at,heartbeat_at,detail)")
        connection.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',7,'OK','WAITING_MARKET','CLOSED',NULL,?,'')", (NOW.isoformat(),))
    path = tmp_path / "runtime-health.json"
    health = controller_snapshot()
    health["real_orders_sent"] = 0  # A fixed publisher field must not erase the observed 7.
    health["children"]["scanner"]["pid"] = os.getpid()
    path.write_text(json.dumps(health))
    connect, read = sqlite3.connect, host.read_controller_health
    def fixed_database(location, **kwargs):
        assert location == "file:/app/data/paper_v17/observer_v17.db?mode=ro"
        assert kwargs["uri"] is True
        return connect(database.as_uri() + "?mode=ro", **kwargs)
    def fixed_health(value):
        assert value == "/app/data/paper_v17/artifacts/observer_v17.db/runtime-health.json"
        return read(path)
    monkeypatch.setattr(sqlite3, "connect", fixed_database)
    monkeypatch.setattr(host, "read_controller_health", fixed_health)
    output = io.StringIO()
    with redirect_stdout(output):
        exec(compile(host.RUNTIME_PROBE, "runtime-probe", "exec"), {})
    observed = json.loads(output.getvalue())
    assert observed["controller"]["source_sha"] == SHA
    assert observed["controller"]["children"]["scanner"]["alive"] is True
    assert observed["real_orders_sent"] == 7
    with pytest.raises(host.Hold, match="PAPER_RUNTIME_SAFETY"):
        host.classify_runtime(observed, now=NOW, started=STARTED)


def postflight_fixture(tmp_path, monkeypatch, samples):
    identity = host.Identity(os.getuid(), os.getgid(), "fixture", tmp_path)
    manager = host.Host(tmp_path, identity)
    monkeypatch.setattr(manager, "check_role", lambda *_: {"State": {"Running": True, "Health": {"Status": "healthy"}}})
    states = iter(samples)
    monkeypatch.setattr(manager.docker, "json", lambda *_: next(states))
    class Clock(datetime):
        @classmethod
        def now(cls, _zone=None):
            return NOW
    monkeypatch.setattr(host, "datetime", Clock)
    document = {"source_sha": SHA, "source_tree": TREE, "roles": {"observer": "1" * 64}}
    times = iter((0, 0, 5, 10))
    waits = []
    return manager, document, times, waits


def observer_row(controller):
    return {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0, "ppi_auth": "ERROR",
            "process_state": "DEGRADED", "session_state": "BOOT_PPI_LOGIN",
            "heartbeat_at": NOW.isoformat(), "detail": "Login local bloqueado por interlock persistente.",
            "controller": controller}


def test_postflight_waits_for_controller_then_accepts_provider_amber(tmp_path, monkeypatch):
    manager, document, times, waits = postflight_fixture(tmp_path, monkeypatch,
        [observer_row(None), observer_row(controller_snapshot())])
    result = manager.postflight(document, STARTED, budget=10, sleep=waits.append, clock=lambda: next(times))
    assert waits == [5]
    assert result["source_alert"] == "AMBER_PPI_LOGIN_UNAVAILABLE"
    assert manager.receipt["controller"]["status"] == "READY"
    assert manager.receipt["controller"]["children"] == sorted(CHILDREN)


def test_postflight_never_certifies_a_lone_scanner_and_reports_controller_timeout(tmp_path, monkeypatch):
    manager, document, times, waits = postflight_fixture(tmp_path, monkeypatch,
        [observer_row(None), observer_row(None)])
    with pytest.raises(host.Hold, match="RUNTIME_STARTUP_TIMEOUT"):
        manager.postflight(document, STARTED, budget=10, sleep=waits.append, clock=lambda: next(times))
    assert waits == [5, 5]
    assert manager.receipt["controller"] == {"status": "PENDING", "reason": "CONTROLLER_HEALTH_UNAVAILABLE"}
    assert manager.receipt["state"] == "NO_VERIFICADO"
