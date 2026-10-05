"""Post-start health through the real V2 worker and external read-only reader."""
from copy import deepcopy
from datetime import timedelta
import gc
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from cg_paper_workspace import artifact_root
from rc6_shadow_runtime.persistence import read_committed_generation
from rc6_shadow_runtime.worker import ShadowRuntime
from scripts.rc6_shadow_health_gate import (
    HEALTH_SCHEMA, ShadowHealthRejected, main, read_health, validate_health_cut,
)
from tests.test_rc6_shadow_runtime_wiring import PRE, make_store

SOURCE, TREE = "a" * 40, "b" * 40
NOW = PRE + timedelta(seconds=5)


@pytest.fixture
def real_cut(tmp_path, monkeypatch):
    for name in ("POROTA_DYNAMIC_SHADOW_ROOT", "POROTA_SHADOW_RUNTIME_ROOT",
                 "POROTA_DYNAMIC_SHADOW_ARCHIVE_ROOT", "POROTA_IOL_SHADOW_CACHE_PATH",
                 "POROTA_IOL_SHADOW_ROOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HIST_DB_PATH", str(tmp_path / "absent-history.db"))
    monkeypatch.setenv("POROTA_DYNAMIC_CAPACITY_MODE", "SHADOW")
    monkeypatch.setenv("POROTA_CAPACITY_POLICY_PATH", str(Path("ops/policy/rc6-dynamic-capacity-v1.json").resolve()))
    for name in ("REPORT", "RECOMMENDATION", "APPROVAL"):
        monkeypatch.setenv("POROTA_CAPACITY_" + name + "_PATH", "")
    store, _ = make_store(tmp_path, count=1)
    root = artifact_root(store.path)
    monkeypatch.setenv("POROTA_CAPACITY_SHADOW_PATH", str(root / "dynamic-shadow/CURRENT.json"))
    worker = ShadowRuntime.from_environment(store.path)
    gc.collect()
    with sqlite3.connect(store.path) as database:
        database.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    worker.tick(PRE)
    health = {"schema": HEALTH_SCHEMA, "recorded_at": NOW.isoformat(), "source_sha": SOURCE,
        "candidate_tree_sha": TREE, "children": {"dynamic_shadow": {"state": "RUNNING",
        "pid": os.getpid(), "restarts": 0, "spawn_failures": 0,
        "started_at": (PRE - timedelta(seconds=120)).isoformat(), "last_exit_at": None,
        "last_error": None}}}
    path = root / "runtime-health.json"
    path.write_text(json.dumps(health))
    return store.path, worker, health, read_committed_generation(worker.root), path


def evaluate(health, generation, worker, **kwargs):
    return validate_health_cut(health, generation, source_sha=SOURCE, tree_sha=TREE,
        now=NOW, configuration_fingerprint=worker.configuration_fingerprint(NOW), **kwargs)


def test_real_producer_health_is_green_without_provider_or_source_writes(real_cut):
    database, worker, health, cut, health_path = real_cut
    paths = [Path(database)] + [path for path in worker.root.rglob("*") if path.is_file()] + [health_path]
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    result = read_health(database, source_sha=SOURCE, tree_sha=TREE, now=NOW)
    assert result["status"] == "GREEN" and result["generation_id"] == cut["manifest"]["generation_id"]
    assert result["readiness"] == "PREOPEN_NON_OPERATIONAL"
    assert result["provider_capacity_open"] == "NO_VERIFICADO"
    assert result["real_orders_sent"] == 0 and result["real_routes"] == "NOT_CALLED"
    assert cut["report"]["provider_requests"] == 0
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}


@pytest.mark.parametrize("state", ["STARTUP_WAIT", "SPAWN_FAILED", "CRASH_BACKOFF", "STOPPED"])
def test_fresh_scanner_and_generation_cannot_hide_shadow_child_failure(real_cut, state):
    _, worker, health, cut, _ = real_cut
    health["children"]["dynamic_shadow"]["state"] = state
    with pytest.raises(ShadowHealthRejected, match="CHILD_NOT_HEALTHY"):
        evaluate(health, cut, worker)


def test_actual_dead_process_rejects_fresh_scanner_and_fresh_generation(real_cut):
    _, worker, health, cut, _ = real_cut
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=10)
    health["children"]["dynamic_shadow"]["pid"] = process.pid
    with pytest.raises(ShadowHealthRejected, match="CHILD_NOT_HEALTHY"):
        evaluate(health, cut, worker)


@pytest.mark.parametrize("drift", ["source", "tree", "clock_stale", "clock_future", "old_generation",
    "future_generation", "prior_child_generation", "wrong_config", "legacy_schema", "boolean_pid",
    "boolean_restarts", "negative_restarts", "bad_spawn_failures", "restart_not_stable",
    "report_routes", "status_orders", "boolean_orders", "phase"])
def test_adversarial_health_or_generation_drift_rejects(real_cut, drift):
    _, worker, health, original, _ = real_cut
    cut = deepcopy(original)
    child = health["children"]["dynamic_shadow"]
    if drift == "source": health["source_sha"] = "c"*40
    elif drift == "tree": health["candidate_tree_sha"] = "c"*40
    elif drift == "clock_stale": health["recorded_at"] = (NOW - timedelta(seconds=21)).isoformat()
    elif drift == "clock_future": health["recorded_at"] = (NOW + timedelta(seconds=1)).isoformat()
    elif drift == "old_generation": cut["report"]["as_of"] = (NOW - timedelta(seconds=91)).isoformat()
    elif drift == "future_generation": cut["report"]["as_of"] = (NOW + timedelta(seconds=1)).isoformat()
    elif drift == "prior_child_generation": child["started_at"] = (PRE + timedelta(seconds=1)).isoformat()
    elif drift == "wrong_config": cut["manifest"]["configuration_fingerprint"] = "other"
    elif drift == "legacy_schema": cut["manifest"]["schema"] = "rc6.shadow-evidence-generation.v1"
    elif drift == "boolean_pid": child["pid"] = True
    elif drift == "boolean_restarts": child["restarts"] = False
    elif drift == "negative_restarts": child["restarts"] = -1
    elif drift == "bad_spawn_failures": child["spawn_failures"] = True
    elif drift == "restart_not_stable":
        child.update(restarts=4, started_at=(PRE - timedelta(seconds=5)).isoformat())
    elif drift == "report_routes": cut["report"]["real_routes"] = "CALLED"
    elif drift == "status_orders": cut["status"]["real_orders_sent"] = 1
    elif drift == "boolean_orders": cut["report"]["real_orders_sent"] = False
    elif drift == "phase": cut["report"]["phase"] = "UNKNOWN"
    with pytest.raises(ShadowHealthRejected):
        evaluate(health, cut, worker)


def test_observable_restart_can_recover_after_two_cadences_and_current_progress(real_cut):
    database, worker, health, cut, path = real_cut
    child = health["children"]["dynamic_shadow"]
    child.update(restarts=3, spawn_failures=1, started_at=(NOW - timedelta(seconds=65)).isoformat())
    path.write_text(json.dumps(health))
    result = read_health(database, source_sha=SOURCE, tree_sha=TREE, now=NOW)
    assert result["child_restarts"] == 3 and result["child_spawn_failures"] == 1
    assert result["status"] == "GREEN"


@pytest.mark.parametrize("failure", ["missing", "symlink", "oversize", "malformed_json", "missing_generation"])
def test_real_reader_rejects_untrusted_or_missing_health_evidence(real_cut, failure, tmp_path):
    database, worker, _, _, path = real_cut
    if failure == "missing": path.unlink()
    elif failure == "symlink":
        raw = path.read_bytes(); path.unlink()
        other = tmp_path / "other-health.json"; other.write_bytes(raw)
        path.symlink_to(other)
    elif failure == "oversize": path.write_bytes(b" " * (64*1024+1))
    elif failure == "malformed_json": path.write_bytes(b'{"schema":"first","schema":"second"}')
    elif failure == "missing_generation": (worker.root / "CURRENT.json").unlink()
    with pytest.raises((ValueError, OSError)):
        read_health(database, source_sha=SOURCE, tree_sha=TREE, now=NOW)


def test_cli_bounds_red_without_disclosing_private_error(monkeypatch, capsys):
    def private_failure(*args, **kwargs):
        raise ValueError("private operator secret value")
    monkeypatch.setattr("scripts.rc6_shadow_health_gate.read_health", private_failure)
    assert main(["--database", "synthetic.db", "--source-sha", SOURCE, "--tree-sha", TREE]) == 1
    output = capsys.readouterr().out
    assert "RC6_SHADOW_RUNTIME_HEALTH=RED" in output and "private" not in output
