#!/usr/bin/env python3
"""Offline, synthetic resource/concurrency evidence for Issue 465.

Creates only new databases under an empty task directory. No runtime, network,
credentials, historical backfill or provider client. Latency measurements are
descriptive; conservative bounds and factual exit completion are the gates.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import json
import gc
import multiprocessing as mp
import os
from pathlib import Path
import resource
from queue import Empty
import sqlite3
import stat
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OPEN = datetime(2026, 10, 5, 13, 30, tzinfo=timezone.utc)
PRE = OPEN - timedelta(minutes=10)
AT = OPEN + timedelta(minutes=5)


class StressResourceLimit(AssertionError):
    def __init__(self, evidence):
        self.evidence = evidence
        super().__init__(json.dumps({"resource_gates": evidence["resource_gates"],
            "shadow_reason": evidence["shadow"].get("reason")}, sort_keys=True))


class StressImportProofLimit(AssertionError):
    def __init__(self, evidence):
        self.evidence = evidence
        super().__init__("NATIVE_IMPORT_PROVENANCE_NOT_VERIFIED")


def _reaped_child_lifetime_rss(child):
    """An actual reaped-children maximum, not isolated worker-only wait4."""
    result = {"status":"UNVERIFIED_UNREAPED", "worker_pid":child.pid,
        "worker_reaped_before_observation":False, "peak_rss_bytes":None,
        "scope":"PARENT_RUSAGE_CHILDREN_MAXIMUM_OF_ALL_REAPED_CHILDREN_INCLUDING_GIT_PREFLIGHT",
        "isolated_worker_wait4":False}
    if child.is_alive() or type(child.exitcode) is not int:
        return result
    result["worker_reaped_before_observation"] = True
    result["worker_exitcode"] = child.exitcode
    try:
        peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        if type(peak) not in (int,float) or peak < 0 or not float(peak).is_integer():
            raise ValueError("NATIVE_IMPORT_REAPED_CHILDREN_RSS_INVALID")
        result.update(status="VERIFIED_REAPED_CHILDREN_MAXIMUM",peak_rss_bytes=int(peak)*1024)
    except Exception as error:
        result.update(status="UNVERIFIED_RUSAGE",error_class=type(error).__name__,reason=str(error))
    return result


def source_file_custody(path):
    """Capture owned source bytes and metadata without changing access time."""
    path = Path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK)
    h = hashlib.sha256()
    fields = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size",
              "st_blocks", "st_atime_ns", "st_mtime_ns", "st_ctime_ns")
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("STRESS_SOURCE_CUSTODY_INVALID")
        size = 0
        while block := os.read(descriptor, 1024 * 1024):
            h.update(block)
            size += len(block)
        after, location = os.fstat(descriptor), path.lstat()
        if size != before.st_size or any(getattr(before, name) != getattr(after, name)
                or getattr(before, name) != getattr(location, name) for name in fields):
            raise ValueError("STRESS_SOURCE_CHANGED_DURING_CAPTURE")
        return {**{name: getattr(before, name) for name in fields}, "sha256": h.hexdigest()}
    finally:
        os.close(descriptor)


def sha256(path):
    return source_file_custody(path)["sha256"]


def source_custody_snapshot(database):
    result = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = Path(str(database) + suffix)
        try:
            path.lstat()
        except FileNotFoundError:
            if not suffix:
                raise
            continue
        result[suffix] = source_file_custody(path)
    return result


def io_snapshot():
    """Actual process counters; logical IO is separate from physical IO."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    values = {"input_blocks": usage.ru_inblock, "output_blocks": usage.ru_oublock}
    path = Path("/proc/self/io")
    if path.is_file():
        for line in path.read_text().splitlines():
            key, value = line.split(":", 1)
            if key in {"rchar", "wchar", "syscr", "syscw", "read_bytes", "write_bytes", "cancelled_write_bytes"}:
                values[key] = int(value.strip())
    return values


def io_delta(before, after):
    return {key: after[key] - value for key, value in before.items() if key in after}


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
    with closing(store.connect()) as c, c:
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
    with closing(sqlite3.connect(path)) as c, c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return store


def _shadow_child(database, output, started, release, queue, slow_disk, maximum_bytes,
                  canonical_runtime=False, diagnostic_stacks=None, source_binding=None):
    import_observer = None
    try:
        if source_binding is not None:
            try:
                from scripts.rc6_native_import_provenance import NativeImportObserver, environment_qualification
                if source_binding["prepared_by_pid"] != os.getppid() or source_binding["source_root"] != str(ROOT):
                    raise ValueError("NATIVE_IMPORT_CHILD_PARENT_OR_SOURCE_ROOT_MISMATCH")
                child_qualification = environment_qualification(ROOT)
                import_observer = NativeImportObserver(source_binding, role="SPAWNED_SHADOW_WORKER")
                initial = import_observer.initial_receipt()
                initial.update(environment_before_product_imports=child_qualification,
                               primary_fixture_created_by_parent=True)
                queue.put({"_probe_event":"IMPORT_PROVENANCE", "phase":"BEFORE_PRODUCT_IMPORTS", "import_provenance":initial})
            except Exception as error:
                if import_observer is not None:
                    import_observer.deactivate()
                started.set()
                queue.put({"_probe_event":"FINAL", "status":"SHADOW_FAIL_CLOSED", "cycle_completion":False,
                    "cycle_handled":True, "reason":"NATIVE_IMPORT_PROVENANCE_STARTUP_UNVERIFIED", "handler_resources":{},
                    "phases":[], "elapsed_seconds":0., "cpu_seconds":0., "peak_rss_bytes":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                    "fsync":{"fsync_entered":False,"fsync_completed":False,"scope":"ACTUAL_STAGED_GENERATION_MEMBER_FSYNC"},
                    "import_provenance":{"status":"UNVERIFIED_STARTUP","native_pid":os.getpid(),"parent_pid":os.getppid(),
                        "source_sha":source_binding.get("source_sha"),"source_tree":source_binding.get("source_tree"),
                        "source_index_sha256":source_binding.get("source_index_sha256"),"transient_closure_verified":False,
                        "error_class":type(error).__name__,"reason":str(error)}})
                return
        return _shadow_child_work(database,output,started,release,queue,slow_disk,maximum_bytes,
            canonical_runtime,diagnostic_stacks,import_observer,
            child_qualification if source_binding is not None else None)
    finally:
        if import_observer is not None:
            import_observer.deactivate()


def _shadow_child_work(database,output,started,release,queue,slow_disk,maximum_bytes,
                       canonical_runtime,diagnostic_stacks,import_observer,child_qualification):
    from rc6_shadow_runtime import persistence
    from rc6_shadow_runtime.worker import ShadowRuntime
    import rc6_shadow_runtime.worker as worker_module
    import rc6_shadow_runtime.stages as stages_module
    from rc6_dynamic_universe.runtime import read_runtime
    fixture_environment = None
    if canonical_runtime:
        # This spawned process owns only its newly created empty DATA tree.
        # Source roots, archive and evidence are then native factory defaults.
        for key in tuple(os.environ):
            if key.startswith("POROTA_CAPACITY_") and key.endswith("_PATH"):
                os.environ.pop(key)
        for key in ("HIST_DB_PATH", "POROTA_DYNAMIC_SHADOW_ROOT", "POROTA_SHADOW_RUNTIME_ROOT",
                    "POROTA_DYNAMIC_SHADOW_ARCHIVE_ROOT", "POROTA_DYNAMIC_SHADOW_ARCHIVE_MAX_BYTES",
                    "POROTA_IOL_SHADOW_ROOT", "POROTA_IOL_SHADOW_CACHE_PATH"):
            os.environ.pop(key, None)
        fixture_environment = {"DATA_DIR": str(Path(database).parent.parent),
            "PAPER_V17_DB_PATH": str(database), "POROTA_DYNAMIC_CAPACITY_MODE": "OFF"}
        from cg_paper_workspace import artifact_root
        from scripts.rc6_sqlite_scratch_guard import runtime_settings
        scratch = artifact_root(database) / "sqlite-read-scratch"
        scratch.mkdir(mode=0o700, parents=True, exist_ok=False)
        fixture_environment.update(runtime_settings(scratch))
        os.environ.update(fixture_environment)
    begin, cpu, initial_io = time.monotonic(), time.process_time(), io_snapshot()
    stack_stream = None
    gc_callback = None
    gc_diagnostic = {"events": 0, "source_capture_events": 0, "write_errors": 0}
    if diagnostic_stacks is not None:
        import faulthandler
        descriptor = os.open(diagnostic_stacks, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_APPEND, 0o600)
        stack_stream = os.fdopen(descriptor, "w")
        def gc_callback(phase, info):
            # Observe real collection without changing thresholds or policy.
            # Only numeric capture progress is recorded, never source rows.
            at = time.monotonic()
            event = {"diagnostic_event": "GC", "phase": phase,
                     "generation": info.get("generation"), "at_monotonic": at,
                     "child_cpu_seconds": time.process_time()-cpu,
                     "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                     "collected": info.get("collected"), "uncollectable": info.get("uncollectable")}
            frame = sys._getframe(1)
            try:
                capture = None
                first_capture = None
                for _ in range(32):
                    if frame is None:
                        break
                    if frame.f_globals.get("__name__") == "rc6_audit_evidence.sqlite_snapshot":
                        progress = frame.f_locals
                        candidate = {"function": frame.f_code.co_name,
                            "line": frame.f_lineno, "consumed_bytes": progress.get("consumed"),
                            "copy_pass": progress.get("destination") is not None}
                        deadline = progress.get("deadline")
                        if type(deadline) in (int, float):
                            candidate["remaining_seconds"] = deadline-at
                        if first_capture is None:
                            first_capture = candidate
                        if frame.f_code.co_name == "_read":
                            capture = candidate
                            break
                    frame = frame.f_back
                if first_capture is not None:
                    event["source_capture"] = capture if capture is not None else first_capture
                    event["source_capture_first_frame"] = first_capture
                    gc_diagnostic["source_capture_events"] += 1
                raw = (json.dumps(event, sort_keys=True, allow_nan=False)+"\n").encode()
                if os.write(descriptor, raw) != len(raw):
                    gc_diagnostic["write_errors"] += 1
                gc_diagnostic["events"] += 1
            except Exception:
                gc_diagnostic["write_errors"] += 1
            finally:
                del frame
        gc.callbacks.append(gc_callback)
        # A native stack witness survives termination at the unchanged deadline.
        # This optional instrument always marks its result diagnostic-only.
        faulthandler.dump_traceback_later(10, repeat=True, file=stack_stream)
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
            queue.put({"_probe_event": "ENTER", "handler_name": _name,
                "entered_at_monotonic": wall, "child_cpu_seconds": process-cpu})
            try:
                return _original(*args, **kwargs)
            except Exception as error:
                if _name == "funnel":
                    frame = error.__traceback__
                    while frame is not None:
                        state = frame.tb_frame.f_locals.get("checkpoint")
                        if frame.tb_frame.f_code.co_name == "evaluate_runtime_funnel" and isinstance(state, dict):
                            from rc6_performance.common import canonical
                            handlers[_name]["guarded_state"] = {
                                "expanded_bytes": len(canonical(state).encode()),
                                "sections": {key: {"entries": len(value) if isinstance(value, (dict, list)) else None,
                                    "bytes": len(canonical(value).encode())} for key, value in state.items()}}
                            break
                        frame = frame.tb_next
                raise
            finally:
                handlers[_name]["elapsed_seconds"] += time.monotonic()-wall
                handlers[_name]["cpu_seconds"] += time.process_time()-process
        setattr(module, function_name, measured)
    for owner, method, name in (
            (worker_module, "run_shadow", "native_orchestrator"),
            (stages_module, "enrich_pipeline", "native_stage_enrichment"),
            (persistence.EvidenceFiles, "read_writer_generation", "checkpoint_restore"),
            (persistence.EvidenceFiles, "commit_generation", "publication"),
            (persistence.PreparedStorage, "__init__", "storage_prepare"),
            (persistence.PreparedStorage, "metrics", "storage_metrics"),
            (persistence.PreparedStorage, "encode", "storage_encode")):
        original = getattr(owner, method)
        def operation(*args, _name=name, _original=original, **kwargs):
            wall, process = time.monotonic(), time.process_time()
            metrics = handlers.setdefault(_name, {"calls": 0, "elapsed_seconds": 0., "cpu_seconds": 0.})
            metrics["calls"] += 1
            queue.put({"_probe_event": "ENTER", "handler_name": _name,
                "entered_at_monotonic": wall, "child_cpu_seconds": process-cpu})
            try:
                value = _original(*args, **kwargs)
                if _name == "checkpoint_restore" and kwargs.get("checkpoint") and value:
                    state = value["checkpoint"]
                    assert all(key in state for key in ("lab", "entry_signals", "funnel"))
                    metrics.setdefault("restored_sequences", []).append(value["pointer"]["sequence"])
                return value
            finally:
                metrics["elapsed_seconds"] += time.monotonic()-wall
                metrics["cpu_seconds"] += time.process_time()-process
                metrics["peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
                queue.put({"_probe_event": "PROGRESS", "handler_resources": handlers.copy(),
                    "phases": phases.copy(), "elapsed_seconds": time.monotonic()-begin,
                    "cpu_seconds": time.process_time()-cpu, "peak_rss_bytes": metrics["peak_rss_bytes"]})
        setattr(owner, method, operation)
    from rc6_shadow_runtime.projection import PreparedProjection
    for method, name in (("__init__", "projection_prepare"), ("build", "projection_header")):
        original = getattr(PreparedProjection, method)
        def operation(*args, _name=name, _original=original, **kwargs):
            wall, process = time.monotonic(), time.process_time()
            metrics = handlers.setdefault(_name, {"calls": 0, "elapsed_seconds": 0., "cpu_seconds": 0.})
            metrics["calls"] += 1
            queue.put({"_probe_event": "ENTER", "handler_name": _name,
                "entered_at_monotonic": wall, "child_cpu_seconds": process-cpu})
            try: return _original(*args, **kwargs)
            finally:
                metrics["elapsed_seconds"] += time.monotonic()-wall
                metrics["cpu_seconds"] += time.process_time()-process
                queue.put({"_probe_event": "PROGRESS", "handler_resources": handlers.copy(),
                    "phases": phases.copy(), "elapsed_seconds": time.monotonic()-begin,
                    "cpu_seconds": time.process_time()-cpu,
                    "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024})
        setattr(PreparedProjection, method, operation)
    try:
        os.nice(10)  # Same priority as the canonical isolated SHADOW child.
    except OSError:
        pass
    actual_fsync = persistence.os.fsync
    blocked = False
    fsync = {"fsync_entered": False, "fsync_completed": False, "blocked_seconds": 0.,
             "scope": "ACTUAL_STAGED_GENERATION_MEMBER_FSYNC"}
    def slow_fsync(fd):
        nonlocal blocked
        descriptor = Path(os.readlink("/proc/self/fd/" + str(fd)))
        eligible = descriptor.name in persistence.GENERATION_ROLES.values() and descriptor.parent.name.startswith(".generation-")
        if not blocked and eligible:
            blocked = True
            fsync.update(fsync_entered=True, entered_at_monotonic=time.monotonic(), member=descriptor.name)
            queue.put({"_probe_event": "FSYNC", "fsync": fsync.copy()})
            started.set()
            waited_at = time.monotonic()
            if not release.wait(15):
                raise TimeoutError("SYNTHETIC_SLOW_DISK_DEADLINE")
            fsync["blocked_seconds"] = time.monotonic() - waited_at
            value = actual_fsync(fd)
            fsync.update(fsync_completed=True, completed_at_monotonic=time.monotonic())
            queue.put({"_probe_event": "FSYNC", "fsync": fsync.copy()})
            return value
        return actual_fsync(fd)
    if slow_disk:
        persistence.os.fsync = slow_fsync
    else:
        started.set()
    try:
        data = read_runtime(database, as_of=AT, row_limit=20000, query_budget_seconds=2)
        phases.append("BOUNDED_READ")
        if canonical_runtime:
            worker = ShadowRuntime.from_environment(database, maximum_bytes=maximum_bytes)
            if worker.root.absolute() != Path(output).absolute():
                raise ValueError("SYNTHETIC_CANONICAL_ROOT_MISMATCH")
        else:
            worker = ShadowRuntime.from_environment(database, evidence_root=output, source_roots=[],
                                   maximum_bytes=maximum_bytes)
        report = worker.tick(PRE)
        phases.append("PREOPEN_COMMITTED")
        del report
        gc.collect()
        report = worker.tick(AT)
        phases.append("OPEN_CYCLE_COMPLETED")
        assert report["provider_requests"] == report["real_orders_sent"] == 0
        assert report["real_routes"] == "NOT_CALLED"
        assert report["source_database_effect"] == "READ_ONLY"
        assert len(data["observations"]) <= 40000
        assert len(data["catalog"]) <= 20000
        committed = persistence.read_committed_projection(output, deadline=time.monotonic()+1)
        assert committed["report"]["as_of"] == report["as_of"]
        assert committed["pointer"]["generation_id"] == report["generation_id"]
        assert 1 in handlers["checkpoint_restore"].get("restored_sequences", [])
        assert committed["dataset_pages"]["opportunities"]["total"] == 2*len(report["catalog_ready"])
        result = {"status": report["status"], "cycle_completion": True,
                  "full_pipeline_exercised": True,
                  "family_reports": len(report["family_routing"] if isinstance(report["family_routing"], list)
                                        else report["family_routing"]["families"]),
                  "observations_returned": len(data["observations"]),
                  "observation_read_truncated": data["observation_read_truncated"],
                  "source_database_effect": "READ_ONLY", "committed_sequence": committed["pointer"]["sequence"],
                  "generation_schema": committed["manifest"]["schema"],
                  "configuration_fingerprint": committed["manifest"]["configuration_fingerprint"],
                  "canonical_factory": canonical_runtime, "fixture_environment": fixture_environment,
                  "database": str(database), "evidence_root": str(worker.root),
                  "archive_root": str(worker.files.archive_root) if worker.files.archive_root is not None else None,
                  "source_roots": list(map(str, worker.source_roots)),
                  "verification_level": committed["export_contract"]["verification_level"],
                  "capacity_policy": report["capacity_policy"],
                  "catalog_ready_count": len(report["catalog_ready"])}
    except Exception as exc:
        # Resource pressure is an explicit loss of SHADOW evidence, never success.
        result = {"status": "SHADOW_FAIL_CLOSED", "cycle_completion": False,
                  "error_class": type(exc).__name__, "reason": persistence.failure_reason(exc)}
        frames = []; cursor = exc.__traceback__
        while cursor is not None:
            frames.append({"module": cursor.tb_frame.f_globals.get("__name__"),
                           "function": cursor.tb_frame.f_code.co_name, "line": cursor.tb_lineno})
            cursor = cursor.tb_next
        result["error_context"] = {"frames": frames[-12:], "sqlite_errorname": getattr(exc, "sqlite_errorname", None),
                                   "sqlite_errorcode": getattr(exc, "sqlite_errorcode", None)}
        frame = exc.__traceback__
        while frame is not None:
            local = frame.tb_frame.f_locals
            if frame.tb_frame.f_code.co_name == "pack" and isinstance(local.get("wire"), bytes):
                result["guarded_generation_role"] = {"role": local.get("role"),
                    "raw_bytes": len(local["wire"]), "payload_limit": worker.files.payload_limit,
                    "sections": {key: {"entries": len(value) if isinstance(value, (dict, list)) else None,
                        "bytes": len(persistence._encode(value))}
                        for key, value in local["values"][local["role"]].items()}}
                break
            frame = frame.tb_next
        if hasattr(exc, "metrics"):
            result["retention_pressure"] = {key: value for key, value in exc.metrics.items()
                if key in {"status", "reason", "files", "bytes", "projected_files", "projected_bytes",
                           "maximum_files", "maximum_bytes", "soft_ratio", "free_bytes"}}
    finally:
        persistence.os.fsync = actual_fsync
        if stack_stream is not None:
            try:
                gc.callbacks.remove(gc_callback)
            finally:
                faulthandler.cancel_dump_traceback_later()
                stack_stream.close()
    if diagnostic_stacks is not None:
        result["diagnostic_gc_receipt"] = dict(gc_diagnostic,
            policy_changed=False, scope="OBSERVED_REAL_CHILD_COLLECTION_ONLY")
    if import_observer is not None:
        try:
            result["import_provenance"] = import_observer.finish()
            result["import_provenance"]["environment_before_product_imports"] = child_qualification
        except Exception as error:
            result["import_provenance"] = {"status":"UNVERIFIED_FINAL_BOUNDARY", "native_pid":os.getpid(),
                "parent_pid":os.getppid(), "transient_closure_verified":False,
                "error_class":type(error).__name__, "reason":str(error)}
    result.update(phases=phases, cycle_handled=True,
                  full_pipeline_exercised={"families", "lab", "entry_signals", "funnel"} <= set(handlers),
                  handler_resources=handlers,
                  elapsed_seconds=time.monotonic()-begin,
                  cpu_seconds=time.process_time()-cpu,
                  peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                  evidence_bytes=sum(p.stat().st_size for p in Path(output).rglob("*") if p.is_file()),
                  evidence_files=sum(p.is_file() for p in Path(output).rglob("*")),
                  io=io_delta(initial_io, io_snapshot()), fsync=fsync,
                  real_orders_sent=0, real_routes="NOT_CALLED", provider_requests=0)
    started.set()  # Completion fallback releases the probe; fsync flags remain factual.
    result["_probe_event"] = "FINAL"
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
        begin, cpu, initial_io = time.monotonic(), time.process_time(), io_snapshot()
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
                "cpu_seconds": time.process_time()-cpu, "io": io_delta(initial_io, io_snapshot()),
                "started_at_monotonic": begin, "finished_at_monotonic": time.monotonic(),
                "stale_current_fresh_book": True, "real_orders_sent": 0}
    finally:
        if old_policy is None:
            os.environ.pop("PAPER_SECTOR_CONCENTRATION_POLICY", None)
        else:
            os.environ["PAPER_SECTOR_CONCENTRATION_POLICY"] = old_policy


def run_stress(root, *, catalog_count=12000, observations_per_identity=5,
               slow_disk=False, maximum_bytes=128*1024**2, allow_fail_closed=False,
               canonical_runtime=False, diagnostic_stacks=None, source_provenance=None):
    binding, parent_observer, parent_before = None, None, None
    if source_provenance is not None:
        if not canonical_runtime or not isinstance(source_provenance,dict):
            raise ValueError("NATIVE_IMPORT_PROVENANCE_REQUIRES_CANONICAL_RUNTIME_AND_FULL_BINDING")
        from scripts.rc6_native_import_provenance import prepare_binding, NativeImportObserver
        binding = prepare_binding(**source_provenance)
    try:
        if binding is not None:
            parent_observer = NativeImportObserver(binding,role="NATIVE_LAUNCHER_AND_FACTUAL_EXITS")
            parent_before = parent_observer.initial_receipt()
        return _run_stress(root,catalog_count=catalog_count,observations_per_identity=observations_per_identity,
            slow_disk=slow_disk,maximum_bytes=maximum_bytes,allow_fail_closed=allow_fail_closed,
            canonical_runtime=canonical_runtime,diagnostic_stacks=diagnostic_stacks,
            binding=binding,parent_observer=parent_observer,parent_before=parent_before)
    finally:
        if parent_observer is not None:
            parent_observer.deactivate()


def _run_stress(root, *, catalog_count, observations_per_identity, slow_disk, maximum_bytes,
                allow_fail_closed, canonical_runtime, diagnostic_stacks, binding, parent_observer, parent_before):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError("EMPTY_SYNTHETIC_WORKSPACE_REQUIRED")
    if type(canonical_runtime) is not bool:
        raise ValueError("CANONICAL_RUNTIME_FLAG_INVALID")
    if diagnostic_stacks is not None:
        diagnostic_stacks = Path(diagnostic_stacks).absolute()
        if (diagnostic_stacks.exists() or diagnostic_stacks.is_symlink()
                or diagnostic_stacks.parent.resolve(strict=True) != diagnostic_stacks.parent):
            raise ValueError("NEW_PRIVATE_DIAGNOSTIC_STACK_FILE_REQUIRED")
        diagnostic_stacks = str(diagnostic_stacks)
    database = root / "data" / "paper_v17" / "observer_v17.db" if canonical_runtime else root / "source.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    if canonical_runtime:
        from cg_paper_workspace import artifact_root
        output = artifact_root(database) / "dynamic-shadow"
    else:
        output = root / "shadow"
    fixture_database(database, catalog_count=catalog_count,
                     observations_per_identity=observations_per_identity)
    source_before = source_custody_snapshot(database)
    before = source_before[""]["sha256"]
    ctx = mp.get_context("spawn")
    started, release, queue = ctx.Event(), ctx.Event(), ctx.Queue()
    child_args = (str(database), str(output), started, release, queue, slow_disk, maximum_bytes,
                  canonical_runtime, diagnostic_stacks)
    if binding is not None:
        child_args += (binding,)
    child = ctx.Process(target=_shadow_child,args=child_args)
    cycle_begin = time.monotonic(); deadline = cycle_begin+90
    child.start()
    shadow = {"status": "SHADOW_FAIL_CLOSED", "cycle_completion": False, "cycle_handled": False,
        "reason": "SHADOW_CONSERVATIVE_CYCLE_DEADLINE", "handler_resources": {}, "phases": [],
        "fsync": {"fsync_entered": False, "fsync_completed": False, "scope": "ACTUAL_STAGED_GENERATION_MEMBER_FSYNC"},
        "elapsed_seconds": 90., "cpu_seconds": 0., "peak_rss_bytes": 0}
    completed = False
    progress_receipts = []
    child_import_before = None

    def receive(message):
        nonlocal completed, shadow, child_import_before
        event = message.pop("_probe_event", None)
        progress_receipts.append({"event": event,
            "received_at_monotonic": time.monotonic(),
            "handler_name": message.get("handler_name"),
            "entered_at_monotonic": message.get("entered_at_monotonic"),
            "child_cpu_seconds": message.get("child_cpu_seconds"),
            "child_elapsed_seconds": message.get("elapsed_seconds"),
            "phases": list(message.get("phases", [])),
            "handler_names": sorted(message.get("handler_resources", {}))})
        if event == "IMPORT_PROVENANCE":
            child_import_before = message.get("import_provenance")
        elif event == "FINAL":
            shadow = message
            completed = True
        elif event in {"PROGRESS", "FSYNC"}:
            shadow.update(message)

    try:
        # Drain native progress before fsync as well as afterwards. Waiting only
        # on the fsync event loses the actual completed stages when the original
        # ninety-second deadline expires first, and can stall a queue feeder.
        while not started.is_set() and not completed and time.monotonic() < deadline:
            try:
                message = queue.get(timeout=min(.05, max(.001, deadline-time.monotonic())))
            except Empty:
                continue
            receive(message)
        # The SHADOW child has separate source/IO ownership; it cannot call exits.
        exits = factual_exit_probe(root/"exit-paper.db")
        release.set()
        # Consume before joining: the multiprocessing feeder cannot finish a
        # large resource receipt while the parent is waiting for its exit.
        while not completed and time.monotonic() < deadline:
            try: message = queue.get(timeout=max(.01, deadline-time.monotonic()))
            except Empty: break
            receive(message)
        if not completed:
            # Read only already available messages, without extending the cycle
            # deadline or claiming that a missing fsync was entered.
            while not completed:
                try:
                    message = queue.get_nowait()
                except Empty:
                    break
                receive(message)
            shadow.update(status="SHADOW_FAIL_CLOSED", cycle_completion=False,
                cycle_handled=False, reason="SHADOW_CONSERVATIVE_CYCLE_DEADLINE")
            proc = Path("/proc") / str(child.pid) / "status"
            if proc.is_file():
                for line in proc.read_text().splitlines():
                    if line.startswith("VmHWM:"):
                        shadow["peak_rss_bytes"] = max(shadow["peak_rss_bytes"], int(line.split()[1])*1024)
        child.join(max(0., deadline-time.monotonic()))
        if child.is_alive():
            shadow.update(child_cleanup_completed=False, child_cleanup_reason="SHADOW_CHILD_CLEANUP_DEADLINE")
        else:
            shadow["child_cleanup_completed"] = True
        if not child.is_alive() and child.exitcode != 0:
            raise AssertionError(f"SHADOW_CHILD_EXIT:{child.exitcode}")
    finally:
        release.set()
        if child.is_alive():
            child.terminate()
            child.join(5)
        queue.close()
        queue.join_thread()
    lifetime_rss = _reaped_child_lifetime_rss(child) if binding is not None else None
    shadow.setdefault("evidence_bytes", sum(p.stat().st_size for p in output.rglob("*") if p.is_file()))
    shadow.setdefault("evidence_files", sum(p.is_file() for p in output.rglob("*")))
    shadow.setdefault("full_pipeline_exercised", {"families", "lab", "entry_signals", "funnel"} <= set(shadow["handler_resources"]))
    shadow["probe_event_receipts"] = progress_receipts
    shadow["cycle_started_at_monotonic"] = cycle_begin
    source_after = source_custody_snapshot(database)
    unchanged = source_before == source_after
    fsync = shadow["fsync"]
    slow_proven = bool(slow_disk and fsync["fsync_entered"] and fsync["fsync_completed"]
        and fsync["entered_at_monotonic"] <= exits["started_at_monotonic"]
        <= exits["finished_at_monotonic"] <= fsync["completed_at_monotonic"])
    result = {"schema": "rc6.issue465.synthetic-stress.v2", "synthetic": True,
            "catalog_baseline": 1200, "catalog_count": catalog_count,
            "catalog_multiplier": catalog_count/1200,
            "observation_baseline_per_identity": 1,
            "observation_multiplier": observations_per_identity,
            "observations_materialized": catalog_count*observations_per_identity,
            "slow_disk_requested": slow_disk, "slow_disk_injected": fsync["fsync_entered"],
            "slow_fsync_exit_isolation_proven": slow_proven, "shadow": shadow,
            "factual_exits": exits, "source_database_unchanged": unchanged,
            "source_sha256": before, "real_orders_sent": 0,
            "source_custody_before": source_before, "source_custody_after": source_after,
            "source_custody_scope": "MAIN_WAL_SHM_JOURNAL_BYTES_AND_ALL_CUSTODY_STATS_NOATIME",
            "canonical_runtime_requested": canonical_runtime, "database": str(database), "evidence_root": str(output),
            "diagnostic_only": diagnostic_stacks is not None,
            "diagnostic_stack_file": diagnostic_stacks,
            "real_routes": "NOT_CALLED", "runtime_touched": False,
            "provider_requests": 0, "cpu_latency_claim": "DESCRIPTIVE_OFFLINE_ONLY"}
    result["completion_required"] = not allow_fail_closed
    result["cycle_deadline_seconds"] = 90
    result["resource_gates"] = {"source_database_unchanged": unchanged,
        "evidence_within_quota": shadow["evidence_bytes"] <= maximum_bytes,
        "rss_within_two_gib": shadow["peak_rss_bytes"] < 2 * 1024**3,
        "child_cleanup_completed": shadow["child_cleanup_completed"],
        "complete_committed_cycle": allow_fail_closed or shadow["cycle_completion"],
        "actual_slow_fsync_exit_isolation": not slow_disk or allow_fail_closed or slow_proven,
        "maximum_rss_bytes": 2 * 1024**3, "actual_rss_bytes": shadow["peak_rss_bytes"],
        "maximum_evidence_bytes": maximum_bytes, "actual_evidence_bytes": shadow["evidence_bytes"]}
    if lifetime_rss is not None:
        shadow["lifetime_resource_observation"] = lifetime_rss
        verified_rss = lifetime_rss["status"] == "VERIFIED_REAPED_CHILDREN_MAXIMUM"
        actual_rss = max(shadow["peak_rss_bytes"],lifetime_rss["peak_rss_bytes"]) if verified_rss else None
        result["resource_gates"].update(actual_rss_bytes=actual_rss,
            reported_worker_rss_bytes=shadow["peak_rss_bytes"],
            rss_within_two_gib=verified_rss and actual_rss < 2 * 1024**3,
            rss_scope=lifetime_rss["scope"])
    if binding is None:
        result["import_provenance"] = {"requested":False,"status":"NOT_AUDITED","transient_closure_verified":False}
    else:
        try:
            parent_final = parent_observer.finish()
        except Exception as error:
            parent_final = {"status":"UNVERIFIED_FINAL_BOUNDARY","transient_closure_verified":False,
                            "error_class":type(error).__name__,"reason":str(error)}
        child_final = shadow.get("import_provenance")
        sources = (binding["source_sha"],binding["source_tree"],binding["source_index_sha256"])
        child_bound = (isinstance(child_import_before,dict) and isinstance(child_final,dict)
            and all(proof.get("native_pid")==child.pid and proof.get("parent_pid")==os.getpid()
                and tuple(proof.get(key) for key in ("source_sha","source_tree","source_index_sha256"))==sources
                for proof in (child_import_before,child_final)))
        verified = bool(child_bound and completed and shadow.get("child_cleanup_completed") is True
            and child.exitcode==0 and parent_final.get("transient_closure_verified") is True
            and child_final.get("transient_closure_verified") is True)
        result["import_provenance"] = {"requested":True,"status":"VERIFIED_OBSERVED_WINDOWS" if verified else "UNVERIFIED",
            "source_sha":binding["source_sha"],"source_tree":binding["source_tree"],"source_index_sha256":binding["source_index_sha256"],
            "environment_before_fixtures":binding["parent_environment_before_fixtures"],
            "sys_path_before_fixtures":binding["sys_path_before_fixtures"],"launcher_pid":os.getpid(),"launcher_parent_pid":os.getppid(),
            "worker_pid":child.pid,"worker_exitcode":child.exitcode,"worker_cleanup_completed":shadow.get("child_cleanup_completed") is True,
            "worker_pid_source_binding_verified":child_bound,"parent_boundary_before":parent_before,"parent_final":parent_final,
            "worker_boundary_before":child_import_before,"worker_final":child_final,"transient_closure_verified":verified,
            "scope":"OBSERVED_PARENT_AND_WORKER_WINDOWS; BOOTSTRAP_AND_POSTBOUNDARY_IPC_TEARDOWN_NOT_FULL_LIFETIME_CLOSURE"}
    result["business_resource_complete"] = all(result["resource_gates"][key] for key in
               ("source_database_unchanged", "evidence_within_quota", "rss_within_two_gib", "child_cleanup_completed",
                "complete_committed_cycle", "actual_slow_fsync_exit_isolation"))
    result["import_proof_complete"] = binding is not None and result["import_provenance"]["transient_closure_verified"] is True
    if not result["business_resource_complete"]:
        raise StressResourceLimit(result)
    if binding is not None and not result["import_proof_complete"]:
        raise StressImportProofLimit(result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--catalog-count", type=int, default=12000)
    parser.add_argument("--observations-per-identity", type=int, default=5)
    parser.add_argument("--slow-disk", action="store_true")
    parser.add_argument("--canonical-runtime", action="store_true")
    parser.add_argument("--diagnostic-stacks", help="New private raw stack/GC file; result is diagnostic-only")
    for name in ("source-root","source-repo","source-sha","source-tree","source-index"):
        parser.add_argument("--"+name,help="Optional import audit: all five source-binding arguments are required together")
    args = parser.parse_args(argv)
    source_values = {name:getattr(args,name) for name in ("source_root","source_repo","source_sha","source_tree","source_index")}
    if any(value is not None for value in source_values.values()) and not all(value is not None for value in source_values.values()):
        parser.error("Import provenance requires all five full source-binding arguments")
    source_provenance = source_values if all(value is not None for value in source_values.values()) else None
    code = 0
    try:
        result = run_stress(args.root, catalog_count=args.catalog_count,
                            observations_per_identity=args.observations_per_identity, slow_disk=args.slow_disk,
                            canonical_runtime=args.canonical_runtime, diagnostic_stacks=args.diagnostic_stacks,
                            **({"source_provenance":source_provenance} if source_provenance is not None else {}))
    except (StressResourceLimit,StressImportProofLimit) as error:
        result, code = error.evidence, 1
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True)+"\n")
    print("ISSUE465_STRESS_EVIDENCE="+str(Path(args.out)))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
