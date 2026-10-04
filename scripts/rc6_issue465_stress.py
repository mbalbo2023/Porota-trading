#!/usr/bin/env python3
"""Offline, synthetic resource/concurrency evidence for Issue 465.

Creates only new databases under an empty task directory. No runtime, network,
credentials, historical backfill or provider client. Latency measurements are
descriptive; conservative bounds and factual exit completion are the gates.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import resource
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OPEN = datetime(2026, 10, 5, 13, 30, tzinfo=timezone.utc)
PRE = OPEN - timedelta(minutes=10)
AT = OPEN + timedelta(minutes=5)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def fixture_database(path, *, catalog_count, observations_per_identity=5):
    """Materialize exact native schemas using bulk synthetic rows, once."""
    from be_paper_engine import PaperStore
    from bf_production_paper_observer import _support_schema
    from cf_intraday_scalping import init_schema
    if Path(path).exists() or not 1 <= catalog_count <= 20000:
        raise ValueError("SYNTHETIC_NEW_SOURCE_REQUIRED")
    if not 1 <= observations_per_identity <= 10:
        raise ValueError("SYNTHETIC_OBSERVATION_BOUNDS")
    store = PaperStore(str(path))
    _support_schema(store)
    init_schema(store)
    with store.connect() as c:
        c.executemany("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            (f"S{i:05d}", "ACCIONES", "BYMA", "ARS", "A-24HS", "SYNTHETIC",
             "fixture", PRE.isoformat(), "issue465", "AVAILABLE", "READY_PAPER_SPOT", "{}")
            for i in range(catalog_count)))
        c.executemany("INSERT INTO ppi_intraday_points VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            (f"S{i:05d}", "ACCIONES", "BYMA", "ARS", "A-24HS",
             (AT-timedelta(seconds=30*j)).isoformat(), str(100+j/100), "10",
             (AT-timedelta(seconds=30*j)).isoformat(), (AT-timedelta(seconds=30*j)).isoformat(),
             "PPI_MARKETDATA_INTRADAY")
            for i in range(catalog_count) for j in range(observations_per_identity)))
        c.executemany("INSERT INTO ppi_intraday_contract_state VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            (f"S{i:05d}", "ACCIONES", "BYMA", "ARS", "A-24HS",
             "CONFIRMED_INTERVAL_VOLUME", 2, 5, 0, 1, AT.isoformat(), AT.isoformat(), "synthetic")
            for i in range(catalog_count)))
        # Five open positions keep critical priority represented in the planner.
        # This source is observation-only; executable factual exits use another DB.
        c.executemany("""INSERT INTO paper_positions(paper_id,source,strategy_version,symbol,
            asset_class,settlement,status,quantity,entry_price,entry_cost,stop_price,target_price,
            opened_at,features_json,currency,market) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            (f"synthetic-open-{i}", "SYNTHETIC", "issue465", f"S{i:05d}", "ACCIONES", "A-24HS",
             "OPEN", "1", "100", ".1", "98", "105", PRE.isoformat(), "{}", "ARS", "BYMA")
            for i in range(min(5, catalog_count))))
    # No delayed fixture checkpoint can be attributed to the read-only consumer.
    import gc
    gc.collect()
    with sqlite3.connect(path) as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return store


def _shadow_child(database, output, started, release, queue, slow_disk, maximum_bytes):
    from rc6_shadow_runtime import persistence
    from rc6_shadow_runtime.worker import ShadowRuntime
    from rc6_dynamic_universe.runtime import read_runtime
    begin, cpu = time.monotonic(), time.process_time()
    phases = []
    handlers = {}
    for module_name, function_name in (
            ("families", "family_reports"), ("lab", "evaluate_runtime_lab"),
            ("entry_signals", "evaluate_runtime_entry_signals"), ("funnel", "evaluate_runtime_funnel")):
        module = importlib.import_module("rc6_shadow_runtime."+module_name)
        original = getattr(module, function_name)
        def measured(*args, _name=module_name, _original=original, **kwargs):
            wall, process = time.monotonic(), time.process_time()
            handlers.setdefault(_name, {"calls": 0, "elapsed_seconds": 0., "cpu_seconds": 0.})["calls"] += 1
            try:
                return _original(*args, **kwargs)
            finally:
                handlers[_name]["elapsed_seconds"] += time.monotonic()-wall
                handlers[_name]["cpu_seconds"] += time.process_time()-process
        setattr(module, function_name, measured)
    try:
        os.nice(10)  # Same priority as the canonical isolated SHADOW child.
    except OSError:
        pass
    actual_fsync = persistence.os.fsync
    blocked = False
    def slow_fsync(fd):
        nonlocal blocked
        if not blocked:
            blocked = True
            started.set()
            if not release.wait(15):
                raise TimeoutError("SYNTHETIC_SLOW_DISK_DEADLINE")
        return actual_fsync(fd)
    if slow_disk:
        persistence.os.fsync = slow_fsync
    else:
        started.set()
    try:
        data = read_runtime(database, as_of=AT, row_limit=20000, query_budget_seconds=2)
        phases.append("BOUNDED_READ")
        worker = ShadowRuntime(database, evidence_root=output, source_roots=[],
                               maximum_bytes=maximum_bytes)
        report = worker.tick(PRE)
        phases.append("PREOPEN_COMMITTED")
        report = worker.tick(AT)
        phases.append("OPEN_CYCLE_COMPLETED")
        assert report["provider_requests"] == report["real_orders_sent"] == 0
        assert report["real_routes"] == "NOT_CALLED"
        assert report["source_database_effect"] == "READ_ONLY"
        assert len(data["observations"]) <= 40000
        assert len(data["catalog"]) <= 20000
        from rc6_shadow_runtime.persistence import EvidenceFiles
        with EvidenceFiles(output) as files:
            checkpoint = files.read("checkpoint.json.gz")
        # Labs/funnel have explicit bounded state and remain prospective.
        assert checkpoint and "lab" in checkpoint and "entry_signals" in checkpoint and "funnel" in checkpoint
        result = {"status": report["status"], "cycle_completion": True,
                  "full_pipeline_exercised": True,
                  "family_reports": len(report["family_routing"]["families"]),
                  "observations_returned": len(data["observations"]),
                  "observation_read_truncated": data["observation_read_truncated"],
                  "source_database_effect": "READ_ONLY"}
    except Exception as exc:
        # Resource pressure is an explicit loss of SHADOW evidence, never success.
        result = {"status": "SHADOW_FAIL_CLOSED", "cycle_completion": False,
                  "error_class": type(exc).__name__, "reason": str(exc)[:180]}
        if hasattr(exc, "metrics"):
            result["retention_pressure"] = {key: value for key, value in exc.metrics.items()
                if key in {"status", "reason", "files", "bytes", "projected_files", "projected_bytes",
                           "maximum_files", "maximum_bytes", "soft_ratio", "free_bytes"}}
    finally:
        persistence.os.fsync = actual_fsync
    result.update(phases=phases, cycle_handled=True,
                  full_pipeline_exercised=set(handlers) == {"families", "lab", "entry_signals", "funnel"},
                  handler_resources=handlers,
                  elapsed_seconds=time.monotonic()-begin,
                  cpu_seconds=time.process_time()-cpu,
                  peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                  evidence_bytes=sum(p.stat().st_size for p in Path(output).rglob("*") if p.is_file()),
                  evidence_files=sum(p.is_file() for p in Path(output).rglob("*")),
                  real_orders_sent=0, real_routes="NOT_CALLED", provider_requests=0)
    started.set()  # A pre-fsync quota rejection still releases the parent probe.
    queue.put(result)


def factual_exit_probe(path):
    """Five actual PAPER ledger closes; no synthetic callback claims."""
    from be_paper_engine import D, PaperBroker, PaperStore, Quote
    from bm_exit_supervisor import PositionExitSupervisor
    from bq_exit_policy import PaperSessionPolicy
    store = PaperStore(str(path))
    clock = [PRE.isoformat()]
    broker = PaperBroker(store, clock_fn=lambda: clock[0], max_positions=5)
    quotes = {}
    old_policy = os.environ.get("PAPER_SECTOR_CONCENTRATION_POLICY")
    os.environ["PAPER_SECTOR_CONCENTRATION_POLICY"] = "OBSERVATION_ONLY"
    try:
        for index in range(5):
            q = Quote(f"EXIT{index}", "ACCIONES", "A-24HS", D(100), D("99.9"),
                      D("100.1"), D(1000), D(1000), PRE.isoformat(), currency="ARS", market="BYMA",
                      metadata_source="TEST_FIXTURE", book_at=PRE.isoformat(), trade_at=PRE.isoformat(),
                      last_kind="TRADE")
            store.add_quote(q)
            ok, reason, _ = broker._open(q, D(".8"), {})
            if not ok:
                raise AssertionError(f"SYNTHETIC_PAPER_OPEN_FAILED:{reason}")
            quotes[q.symbol] = q
        begin = time.monotonic()
        at = AT.isoformat()
        clock[0] = at
        supervisor = PositionExitSupervisor(broker, clock_fn=lambda: at, session_policy=PaperSessionPolicy())
        # Stale current plus fresh executable book still closes all five stops.
        books = {}
        for p in store.open_positions():
            q = quotes[p["symbol"]]
            key = tuple(p[k] for k in ("symbol", "asset_class", "settlement", "currency", "market"))
            books[key] = replace(q, last=D(90), bid=D("89.9"), ask=D("90.1"),
                                 book_at=at, observed_at=at, trade_at=PRE.isoformat())
        verdicts = supervisor.tick(books)
        assert len(verdicts) == 5 and all(v.state == "CLOSED" for v in verdicts), verdicts
        assert not store.open_positions()
        assert supervisor.tick(books) == []
        with store.connect() as c:
            fills = c.execute("SELECT count(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0]
            state = c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        assert fills == 5 and tuple(state) == ("PRODUCTION_PAPER", 0)
        return {"closed": 5, "sell_fills": fills, "elapsed_seconds": time.monotonic()-begin,
                "stale_current_fresh_book": True, "real_orders_sent": 0}
    finally:
        if old_policy is None:
            os.environ.pop("PAPER_SECTOR_CONCENTRATION_POLICY", None)
        else:
            os.environ["PAPER_SECTOR_CONCENTRATION_POLICY"] = old_policy


def run_stress(root, *, catalog_count=12000, observations_per_identity=5,
               slow_disk=False, maximum_bytes=128*1024**2):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError("EMPTY_SYNTHETIC_WORKSPACE_REQUIRED")
    database = root / "source.db"
    fixture_database(database, catalog_count=catalog_count,
                     observations_per_identity=observations_per_identity)
    before = sha256(database)
    ctx = mp.get_context("spawn")
    started, release, queue = ctx.Event(), ctx.Event(), ctx.Queue()
    child = ctx.Process(target=_shadow_child, args=(str(database), str(root/"shadow"),
                        started, release, queue, slow_disk, maximum_bytes))
    child.start()
    try:
        if not started.wait(20):
            raise AssertionError("SHADOW_START_DEADLINE")
        # The SHADOW child has separate source/IO ownership; it cannot call exits.
        exits = factual_exit_probe(root/"exit-paper.db")
        release.set()
        child.join(90)
        if child.is_alive():
            raise AssertionError("SHADOW_CONSERVATIVE_CYCLE_DEADLINE")
        if child.exitcode != 0:
            raise AssertionError(f"SHADOW_CHILD_EXIT:{child.exitcode}")
        shadow = queue.get(timeout=5)
    finally:
        release.set()
        if child.is_alive():
            child.terminate()
            child.join(5)
        queue.close()
        queue.join_thread()
    unchanged = before == sha256(database)
    assert unchanged, "SHADOW_MODIFIED_SOURCE_DATABASE"
    assert shadow["evidence_bytes"] <= maximum_bytes
    assert shadow["peak_rss_bytes"] < 2*1024**3
    return {"schema": "rc6.issue465.synthetic-stress.v1", "synthetic": True,
            "catalog_baseline": 1200, "catalog_count": catalog_count,
            "catalog_multiplier": catalog_count/1200,
            "observation_baseline_per_identity": 1,
            "observation_multiplier": observations_per_identity,
            "observations_materialized": catalog_count*observations_per_identity,
            "slow_disk_injected": slow_disk, "shadow": shadow,
            "factual_exits": exits, "source_database_unchanged": unchanged,
            "source_sha256": before, "real_orders_sent": 0,
            "real_routes": "NOT_CALLED", "runtime_touched": False,
            "provider_requests": 0, "cpu_latency_claim": "DESCRIPTIVE_OFFLINE_ONLY"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--catalog-count", type=int, default=12000)
    parser.add_argument("--observations-per-identity", type=int, default=5)
    parser.add_argument("--slow-disk", action="store_true")
    args = parser.parse_args(argv)
    result = run_stress(args.root, catalog_count=args.catalog_count,
                        observations_per_identity=args.observations_per_identity, slow_disk=args.slow_disk)
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True)+"\n")
    print("ISSUE465_STRESS_EVIDENCE="+str(Path(args.out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
