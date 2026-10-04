"""Actual offline load/locks/isolated-process regressions, not microbenchmarks."""
from datetime import timedelta
import multiprocessing as mp
from pathlib import Path
import json
import os
import shutil
import sqlite3

import pytest

from scripts.rc6_issue465_stress import (AT, fixture_database, run_stress, sha256)
from rc6_dynamic_universe.runtime import read_runtime
from rc6_ppi_global_budget import BudgetBackpressure, GlobalPPIBudget, SCHEMA


def record(result, name):
    root = os.getenv("ISSUE465_EVIDENCE_DIR")
    if not root and os.getenv("RUNNER_TEMP"):
        root = str(Path(os.environ["RUNNER_TEMP"])/"porota-predeploy-evidence"/"issue465-stress")
    if root:
        Path(root).mkdir(parents=True, exist_ok=True)
        (Path(root)/(name+".json")).write_text(json.dumps(result, indent=2, sort_keys=True)+"\n")
        # Committed synthetic causal witnesses and frozen focal metadata are
        # uploaded with the new Actions artifact; no private dataset is copied.
        audits = Path(__file__).resolve().parents[1]/"docs"/"audits"
        for evidence_name in ("ISSUE465_REAUDIT_RED.json", "ISSUE465_REAUDIT_FOCAL.json"):
            source = audits/evidence_name
            if source.is_file() and not source.is_symlink():
                assert source.stat().st_size <= 10*1024**2
                shutil.copyfile(source, Path(root)/evidence_name)


def test_10x_catalog_5x_observations_exercises_all_shadow_labs_and_five_factual_exits(tmp_path):
    result = run_stress(tmp_path / "load")
    record(result, "catalog10x-observations5x")
    assert result["catalog_multiplier"] == 10 and result["observation_multiplier"] == 5
    assert result["observations_materialized"] == 60000
    assert result["shadow"]["full_pipeline_exercised"], json.dumps(result["shadow"])
    assert result["shadow"]["cycle_handled"]
    if result["shadow"]["cycle_completion"]:
        assert result["shadow"]["observation_read_truncated"]
    else:
        # Exhausting an explicit SHADOW bound is degradation, never evidence
        # of a successful large cycle; the factual engine remains available.
        assert result["shadow"]["status"] == "SHADOW_FAIL_CLOSED"
        assert result["shadow"]["reason"] in {
            "FUNNEL_CHECKPOINT_CAPACITY_EXCEEDED", "SHADOW_PAYLOAD_LIMIT", "SHADOW_EVIDENCE_CAPACITY_REACHED"}
    assert result["factual_exits"]["closed"] == 5
    assert result["source_database_unchanged"] and result["real_orders_sent"] == 0


@pytest.mark.parametrize("quota", [128*1024**2, 1024])
def test_slow_disk_or_shadow_quota_failure_cannot_block_actual_exit_supervisor(tmp_path, quota):
    result = run_stress(tmp_path / "isolation", catalog_count=100,
                        slow_disk=True, maximum_bytes=quota)
    record(result, "slow-disk-quota-"+str(quota))
    assert result["factual_exits"]["closed"] == result["factual_exits"]["sell_fills"] == 5
    assert result["source_database_unchanged"]
    if quota == 1024:
        assert not result["shadow"]["cycle_completion"]
        assert result["shadow"]["status"] == "SHADOW_FAIL_CLOSED"
        assert result["shadow"]["reason"] in {
            "SHADOW_EVIDENCE_CAPACITY_REACHED", "RETENTION_HARD_BYTES_CAPACITY_REACHED"}
    else:
        assert result["shadow"]["cycle_completion"], result["shadow"]


def test_wal_writer_lock_does_not_turn_shadow_into_writer_or_unbounded_query(tmp_path):
    path = tmp_path / "source.db"
    fixture_database(path, catalog_count=101)
    before = sha256(path)
    writer = sqlite3.connect(path)
    writer.execute("BEGIN IMMEDIATE")
    try:
        result = read_runtime(path, as_of=AT, row_limit=101, query_budget_seconds=2)
        assert len(result["catalog"]) == 101 and len(result["observations"]) <= 202
        assert result["observation_read_truncated"]
        with pytest.raises(ValueError, match="CATALOG_READ_TRUNCATED"):
            read_runtime(path, as_of=AT, row_limit=100, query_budget_seconds=2)
    finally:
        writer.rollback()
        writer.close()
    assert sha256(path) == before


def _burst_process(path, config, priority, count, out, endpoint="book"):
    budget = GlobalPPIBudget(path, config, clock=lambda: AT)
    admitted, denied = 0, 0
    reasons = {}
    for _ in range(count):
        try:
            result = budget.acquire(endpoint, consumer="EXIT_READER" if priority == "EXIT_CRITICAL" else "SCANNER",
                                    priority=priority)
        except BudgetBackpressure as error:
            # Acquisition uncertainty is a bounded native denial, not a wire
            # admission. Start/finish failures still fail this stress explicitly.
            assert str(error) == "PPI_BUDGET_STATE_UNAVAILABLE"
            result = {"allowed": False, "reason": str(error)}
        if result["allowed"]:
            budget.start(result["lease"])
            budget.finish(result["lease"])
            admitted += 1
        else:
            denied += 1
            reason = result["reason"]
            assert reason in {"PPI_BUDGET_STATE_UNAVAILABLE", "PPI_SERIAL_BACKPRESSURE",
                              "PPI_HIGH_PRIORITY_RESERVE_BACKPRESSURE", "PPI_BUDGET_EXHAUSTED"}
            reasons[reason] = reasons.get(reason, 0)+1
    out.put((priority, admitted, denied, reasons))


@pytest.mark.parametrize("endpoint", ["book", "intraday"])
def test_multiprocess_burst_cannot_spend_exit_floor(tmp_path, endpoint):
    path = str(tmp_path/"budget.sqlite")
    config = {"schema": SCHEMA, "version": "synthetic-stress", "recommendation_digest": "a"*64,
        "configuration_fingerprint": "b"*64, "window_seconds": 30,
        "endpoint_limits": {"current": 5, "book": 5, "intraday": 5}, "global_limit": 15,
        "max_parallel_requests": 1, "safety_reserve": "NOT_PROVIDER_CAPACITY_CLAIM",
        "priority_reserves": {"EXIT_CRITICAL": {"book": 5}},
        "expires_at": (AT+timedelta(hours=1)).isoformat(), "critical_book_seconds": 5,
        "lease_seconds": 60, "breaker_seconds": 60, "session_breaker_seconds": 900,
        "server_error_threshold": 2, "maximum_bytes": 8*1024**2}
    budget = GlobalPPIBudget(path, config, clock=lambda: AT)
    ctx = mp.get_context("spawn")
    out = ctx.Queue()
    priorities = (("OPENED_CRITICAL", "SCALPING_HOT", "DISCOVERY") if endpoint == "book"
                  else ("SCALPING_HOT", "STRATEGY_HOT", "DISCOVERY"))
    children = [ctx.Process(target=_burst_process, args=(path, config, p, 30, out, endpoint))
                for p in priorities]
    for child in children:
        child.start()
    for child in children:
        child.join(20)
        if child.is_alive():
            child.terminate(); child.join(5)
        assert child.exitcode == 0
    results = [out.get(timeout=5) for _ in children]
    lower_admitted = sum(admitted for _, admitted, _, _ in results)
    if endpoint == "book":
        assert lower_admitted == 0, results
    else:
        assert 1 <= lower_admitted <= 5, results
    assert all(admitted+denied == 30 and sum(reasons.values()) == denied
               for _, admitted, denied, reasons in results)
    exit_child = ctx.Process(target=_burst_process, args=(path, config, "EXIT_CRITICAL", 5, out))
    exit_child.start(); exit_child.join(20)
    if exit_child.is_alive():
        exit_child.terminate(); exit_child.join(5)
    assert exit_child.exitcode == 0
    assert out.get(timeout=5) == ("EXIT_CRITICAL", 5, 0, {})
    metrics = budget.metrics()
    assert metrics["global"]["used"] == 5+lower_admitted
    record({"synthetic": True, "endpoint": endpoint, "burst": results, "metrics": metrics,
            "sqlite_wait_policy_ms": 50, "real_orders_sent": 0}, "multiprocess-budget-"+endpoint)
    out.close(); out.join_thread()


def _locked_burst_process(path, config, ready, release, out):
    # Initialize the actual native reader before the parent's deliberate writer
    # lock, then exercise the same child burst path used by the stress.
    GlobalPPIBudget(path, config, clock=lambda: AT)
    ready.set()
    assert release.wait(10)
    _burst_process(path, config, "DISCOVERY", 3, out)


def test_native_budget_writer_lock_is_explicit_child_denial_without_wire_or_exit_loss(tmp_path):
    path = str(tmp_path/"budget.sqlite")
    config = {"schema": SCHEMA, "recommendation_digest": "a"*64,
        "configuration_fingerprint": "b"*64, "window_seconds": 30,
        "endpoint_limits": {"current": 5, "book": 5, "intraday": 5}, "global_limit": 15,
        "max_parallel_requests": 1, "priority_reserves": {"EXIT_CRITICAL": {"book": 5}},
        "expires_at": (AT+timedelta(hours=1)).isoformat(), "critical_book_seconds": 5,
        "lease_seconds": 60, "breaker_seconds": 60, "session_breaker_seconds": 900,
        "server_error_threshold": 2, "maximum_bytes": 8*1024**2}
    budget = GlobalPPIBudget(path, config, clock=lambda: AT)
    ctx = mp.get_context("spawn")
    ready, release, out = ctx.Event(), ctx.Event(), ctx.Queue()
    child = ctx.Process(target=_locked_burst_process, args=(path, config, ready, release, out))
    writer = sqlite3.connect(path)
    try:
        child.start()
        assert ready.wait(10)
        writer.execute("BEGIN IMMEDIATE")
        release.set()
        child.join(5)
        assert child.exitcode == 0
        assert out.get(timeout=2) == ("DISCOVERY", 0, 3,
                                      {"PPI_BUDGET_STATE_UNAVAILABLE": 3})
    finally:
        writer.rollback(); writer.close()
        if child.is_alive():
            child.terminate(); child.join(5)
        out.close(); out.join_thread()
    assert budget.metrics()["global"]["used"] == 0
    for _ in range(5):
        request = budget.acquire("book", priority="EXIT_CRITICAL", consumer="EXIT_READER")
        assert request["allowed"]
        budget.start(request["lease"]); budget.finish(request["lease"])
    assert budget.metrics()["global"]["used"] == 5
    record({"synthetic": True, "locked_child_denied": 3, "factual_exit_admitted": 5,
            "denial_reason": "PPI_BUDGET_STATE_UNAVAILABLE", "real_orders_sent": 0},
           "native-budget-writer-lock")
