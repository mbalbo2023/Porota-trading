"""One bounded NOGATE component trace of an exact archive under Chromium.

Timing wrappers return native values unchanged. GC state, byte verification,
deadline, selector and source remain untouched. This cannot certify acceptance.
"""
import argparse
from contextlib import contextmanager
import gc
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
from threading import get_ident
from time import monotonic, perf_counter, process_time, thread_time
from urllib.parse import unquote, urlsplit


class DiagnosticWindowEnded(Exception):
    pass


def protected_bytes(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOATIME | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        return stream.read()


def cgroup_cpu():
    path = Path("/sys/fs/cgroup/cpu.stat")
    try:
        return {key: int(value) for key, value in (row.split() for row in path.read_text().splitlines())}
    except (ValueError, OSError):
        return {}


def run(args):
    sys.dont_write_bytecode = True
    index = json.loads(args.index.read_text())
    root = Path(index["extracted_root"]).resolve()
    database, native_root = args.database.resolve(), args.root.resolve()
    runner = Path(__file__).resolve()
    assert index.get("overlays") == []
    source_members = {Path(str(database) + suffix) for suffix in ("", "-wal", "-shm", "-journal")}
    output = args.output.resolve()
    if (args.output.exists() or any(path.is_symlink() for path in (args.output, *args.output.parents))
            or output.is_relative_to(root) or output.is_relative_to(native_root)
            or output.is_relative_to(Path(str(native_root) + ".authority")) or output in source_members):
        raise ValueError("NEW_DIAGNOSTIC_OUTPUT_OUTSIDE_SOURCE_REQUIRED")
    hashes = lambda: {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in sorted(root.rglob("*")) if path.is_file()}
    before = hashes()
    assert before == index["source_file_hashes"]
    network, source_sqlite = [], []
    original_connect = sqlite3.connect

    def no_network(*_args, **_kwargs):
        network.append("BLOCKED")
        raise AssertionError("NETWORK_FORBIDDEN")

    def private_connect(value, *positional, **keywords):
        raw = os.fsdecode(value)
        raw = unquote(urlsplit(raw).path) if raw.startswith("file:") else raw
        path = Path(raw).resolve() if raw != ":memory:" else None
        same = path in source_members if path is not None else False
        if path is not None and path.exists():
            same = same or any(member.exists() and path.samefile(member) for member in source_members)
        if same:
            source_sqlite.append("BLOCKED")
            raise AssertionError("SOURCE_SQLITE_FORBIDDEN")
        return original_connect(value, *positional, **keywords)

    socket.socket.connect = no_network
    socket.create_connection = no_network
    sqlite3.connect = private_connect
    sys.path.insert(0, str(root))
    from tests import ci_rc6_projection_large_browser as browser
    from rc6_shadow_runtime import persistence, serialization
    import rc6_audit_evidence.sqlite_snapshot as snapshot

    custody_before = browser.custody_inventory(database, native_root)
    pointer = json.loads(protected_bytes(native_root / "CURRENT.json"))
    manifest = json.loads(protected_bytes(native_root / ("gen-" + pointer["generation_id"]) / "manifest.json"))
    digest_roles = {record["payload_digest"]: role for role, record in manifest["files"].items()}
    stages, renders, collections, pending_gc = [], [], [], {}
    state = {"render": None, "end": None}

    def record(label, began, cpu, thread_cpu, error=None, **metadata):
        stages.append({"label": label, "render": state["render"], "thread_id": get_ident(),
                       "elapsed_seconds": perf_counter() - began, "process_cpu_seconds_overlap_not_additive": process_time() - cpu,
                       "thread_cpu_seconds": thread_time() - thread_cpu, "error_class": error, **metadata})

    def timed(label, function):
        def wrapped(*positional, **keywords):
            began, cpu, thread_cpu = perf_counter(), process_time(), thread_time()
            error, metadata = None, {}
            if label == "storage" and positional and isinstance(positional[0], dict):
                value = positional[0]
                metadata = {"logical_bytes": value.get("logical_bytes"), "logical_sha256": value.get("logical_sha256"),
                            "role": digest_roles.get(value.get("logical_sha256")), "retain": keywords.get("retain")}
            try:
                return function(*positional, **keywords)
            except BaseException as failure:
                error = type(failure).__name__
                raise
            finally:
                record(label, began, cpu, thread_cpu, error, **metadata)
        return wrapped

    original_copy = snapshot.readonly_copy

    @contextmanager
    def traced_copy(*positional, **keywords):
        context = original_copy(*positional, **keywords)
        began, cpu, thread_cpu = perf_counter(), process_time(), thread_time()
        try:
            connection = context.__enter__()
        except BaseException as failure:
            record("source_capture_enter", began, cpu, thread_cpu, type(failure).__name__)
            raise
        record("source_capture_enter", began, cpu, thread_cpu)
        try:
            yield connection
        except BaseException:
            exception = sys.exc_info()
            close = timed("source_capture_exit", context.__exit__)
            if not close(*exception):
                raise
        else:
            timed("source_capture_exit", context.__exit__)(None, None, None)

    def collect(phase, info):
        key = get_ident(), info["generation"]
        if phase == "start":
            pending_gc[key] = perf_counter(), thread_time(), state["render"]
        elif phase == "stop" and key in pending_gc:
            began, cpu, render = pending_gc.pop(key)
            collections.append({"render": render, "generation": info["generation"], "elapsed_seconds": perf_counter() - began,
                                "thread_cpu_seconds": thread_time() - cpu, "collected": info["collected"],
                                "uncollectable": info["uncollectable"]})

    original_build = browser.build_page

    def traced_build(*positional, **keywords):
        render = len(renders)
        state["render"] = render
        began, cpu = perf_counter(), process_time()
        group_before, gc_before = cgroup_cpu(), gc.get_stats()
        failure = None
        try:
            return original_build(*positional, **keywords)
        except BaseException as error:
            failure = type(error).__name__
            raise
        finally:
            group_after = cgroup_cpu()
            renders.append({"index": render, "path": positional[0], "filters": positional[1], "error_class": failure,
                            "elapsed_seconds": perf_counter() - began, "process_cpu_seconds": process_time() - cpu,
                            "gc_before": gc_before, "gc_after": gc.get_stats(),
                            "cgroup_delta_all_container_processes": {key: value - group_before[key] for key, value in group_after.items() if key in group_before}})
            state["render"] = None

    original_require = browser.require

    def bounded_require(value, gate, details=None):
        original_require(value, gate, details)
        if state["end"] is not None and monotonic() >= state["end"]:
            raise DiagnosticWindowEnded()

    snapshot.readonly_copy = traced_copy
    serialization._storage_bytes = timed("storage", serialization._storage_bytes)
    persistence.EvidenceFiles._wire_generation = timed("wire_generation", persistence.EvidenceFiles._wire_generation)
    persistence.read_committed_projection = timed("canonical_query", persistence.read_committed_projection)
    browser.build_page = traced_build
    browser.require = bounded_require
    gc.callbacks.append(collect)
    result, error_class, details = "NO_FAILURE_OBSERVED_WITHIN_DIAGNOSTIC_WINDOW", None, {}
    try:
        state["end"] = monotonic() + 60
        browser.run(database, native_root, output / "browser")
    except DiagnosticWindowEnded:
        pass
    except BaseException as error:
        result, error_class = "NATIVE_OR_BROWSER_FAILURE_RETAINED", type(error).__name__
        details = {"gate": str(error) if isinstance(error, browser.GateFailure) else "DIAGNOSTIC_CALL_FAILED",
                   "native_details": error.details if isinstance(error, browser.GateFailure) else {}}
    finally:
        gc.callbacks.remove(collect)
    custody_after, after = browser.custody_inventory(database, native_root), hashes()
    closure, unexpected = [], []
    for name, module in sorted(sys.modules.items()):
        file = getattr(module, "__file__", None)
        if not file:
            continue
        path = Path(file).resolve()
        if path.is_relative_to(root):
            relative = str(path.relative_to(root))
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            closure.append({"module": name, "path": relative, "sha256": checksum,
                            "matches_archived_blob": checksum == index["source_file_hashes"].get(relative)})
        elif str(path).startswith("/workspace/porota_") and path != runner:
            unexpected.append(str(path))
    proof = before == after and custody_before == custody_after and not network and not source_sqlite and not unexpected
    proof = proof and all(record["matches_archived_blob"] for record in closure)
    receipt = {"schema": "rc6.native-browser-components-diagnostic.v1", "status": "DIAGNOSTIC_ONLY_" + result,
               "acceptance_complete": False, "native_gate_acceptance_claim": False, "source_sha": index["source_sha"],
               "candidate_tree_sha": index["candidate_tree_sha"], "archive_sha256": index["archive_sha256"], "overlays": [],
               "runtime_instrumentation": "TIMING_WRAPPERS_AND_GC_CALLBACK_ONLY; ORIGINAL_NATIVE_RETURNS_DEADLINE_AND_GC_STATE_UNCHANGED",
               "runner_sha256": hashlib.sha256(runner.read_bytes()).hexdigest(), "error_class": error_class,
               "details": details, "renders": renders, "stages": stages, "gc_collections": collections,
               "source_proof_pass": proof, "tracked_source_files": len(before), "tracked_source_hashes_unchanged": before == after,
               "custody_inventory_before": custody_before, "custody_inventory_after": custody_after,
               "native_custody_unchanged": custody_before == custody_after, "imported_product_modules": closure,
               "unexpected_product_imports": unexpected, "network_attempts": len(network), "source_sqlite_attempts": len(source_sqlite),
               "scope_limits": ["60s diagnostic window, no acceptance even if no failure was observed.",
                                "Overlapping process CPU per stage cannot be added; thread CPU excludes separateSHA workers.",
                                "cgroup counters include all processes in this container; timing wrappers add diagnostic overhead."]}
    output.mkdir(parents=True, exist_ok=True)
    with (output / "diagnostic.json").open("x") as stream:
        stream.write(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"status": receipt["status"], "acceptance_complete": False, "renders": len(renders), "source_proof_pass": proof}))
    return 0 if proof else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    raise SystemExit(run(parser.parse_args()))
