"""One bounded NOGATE component trace of an exact archive under Chromium.

Timing wrappers return native values unchanged. GC state, byte verification,
deadline, selector and source remain untouched. This cannot certify acceptance.
"""
import argparse
from contextlib import contextmanager, ExitStack
import gc
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import sys
from threading import get_ident, local
from time import monotonic, perf_counter, process_time, thread_time
from urllib.parse import unquote, urlsplit
from functools import wraps
from unittest.mock import patch


class DiagnosticWindowEnded(Exception):
    pass


def require_then_window_end(require, value, gate, details=None, *, end=None):
    """A real native/browser failure takes precedence over diagnostic stopping."""
    require(value, gate, details)
    if end is not None and monotonic() >= end:
        raise DiagnosticWindowEnded()


class DiagnosticTrace:
    """Aggregate observations only; native bytes, arguments and results pass through.

    Codec context is thread-local because report and checkpoint verification
    can run together. No literal, template, packet or expanded JSON is retained
    by this observer. Nested elapsed/CPU times must not be added.
    """
    STORAGE_SCHEMAS = {"rc6.lossless-json-storage.v1", "rc6.lossless-json-storage.v2"}
    HEX = re.compile(r"[0-9a-f]{64}\Z")

    def __init__(self, digest_roles, state):
        self.digest_roles = dict(digest_roles)
        self.role_digests = {role: digest for digest, role in digest_roles.items()}
        self.state = state
        self.context = local()
        self.aggregates = {}
        self.gc_aggregates = {}
        self.pending_gc = {}

    def codec_metadata(self, value, keywords):
        if not isinstance(value, dict):
            return {}
        digest = value.get("logical_sha256")
        digest = digest if isinstance(digest, str) and self.HEX.fullmatch(digest) else None
        schema = value.get("schema")
        logical_bytes, retain = value.get("logical_bytes"), keywords.get("retain", False)
        metadata = {"role": self.digest_roles.get(digest), "logical_sha256": digest,
                    "storage_schema": schema if isinstance(schema, str) and schema in self.STORAGE_SCHEMAS else "UNSUPPORTED",
                    "logical_bytes_declared": logical_bytes if type(logical_bytes) is int and logical_bytes >= 0 else None,
                    "retain": retain if type(retain) is bool else None}
        if schema == "rc6.lossless-json-storage.v2":
            def count(name):
                member = value.get(name)
                declared = member.get("count") if isinstance(member, dict) else None
                return declared if type(declared) is int and declared >= 0 else None
            metadata["codec_counts_declared"] = {
                "packets": len(value["packets"]) if isinstance(value.get("packets"), list) else None,
                **{name.removesuffix("_count"): value[name] if type(value.get(name)) is int and value[name] >= 0 else None
                   for name in ("templates_count", "literals_count")},
                **{name: count(name) for name in ("directory", "instances", "bindings", "references")}}
        return metadata

    def record(self, label, began, cpu, thread_cpu, error=None, *, metadata=None, returned_bytes=None):
        metadata = metadata if metadata is not None else getattr(self.context, "codec", {})
        thread_id, render = get_ident(), self.state["render"]
        key = (label, render, thread_id, metadata.get("role"), metadata.get("logical_sha256"))
        if key not in self.aggregates:
            self.aggregates[key] = {"label": label, "render": render, "thread_id": thread_id,
                "scope": "PREFLIGHT_OR_OUTSIDE_RENDER" if render is None else "RENDER",
                "metadata_last_declared": metadata, "calls": 0, "completed_calls": 0,
                "errors_by_class": {}, "elapsed_seconds_total": 0., "elapsed_seconds_max": 0.,
                "process_cpu_seconds_total_overlap_not_additive": 0., "thread_cpu_seconds_total": 0.,
                "returned_bytes_total": 0}
        aggregate = self.aggregates[key]
        elapsed = perf_counter() - began
        aggregate["calls"] += 1
        aggregate["metadata_last_declared"] = metadata
        aggregate["elapsed_seconds_total"] += elapsed
        aggregate["elapsed_seconds_max"] = max(aggregate["elapsed_seconds_max"], elapsed)
        aggregate["process_cpu_seconds_total_overlap_not_additive"] += process_time() - cpu
        aggregate["thread_cpu_seconds_total"] += thread_time() - thread_cpu
        if error is None:
            aggregate["completed_calls"] += 1
        else:
            aggregate["errors_by_class"][error] = aggregate["errors_by_class"].get(error, 0) + 1
        if returned_bytes is not None:
            aggregate["returned_bytes_total"] += returned_bytes

    def timed(self, label, function, *, codec=False, member_roles=None):
        @wraps(function)
        def wrapped(*positional, **keywords):
            metadata = None
            if member_roles is not None:
                role = member_roles.get(Path(positional[1]).name)
                if role is None:
                    return function(*positional, **keywords)
                metadata = {"role": role, "logical_sha256": self.role_digests.get(role)}
            previous = getattr(self.context, "codec", {})
            if codec:
                metadata = self.codec_metadata(positional[0] if positional else None, keywords)
                self.context.codec = metadata
            began, cpu, thread_cpu = perf_counter(), process_time(), thread_time()
            error, returned_bytes = None, None
            try:
                result = function(*positional, **keywords)
                if member_roles is not None and isinstance(result, bytes):
                    returned_bytes = len(result)
                return result
            except BaseException as failure:
                error = type(failure).__name__
                raise
            finally:
                try:
                    self.record(label, began, cpu, thread_cpu, error, metadata=metadata,
                                returned_bytes=returned_bytes)
                finally:
                    if codec:
                        self.context.codec = previous
        return wrapped

    def collect(self, phase, info):
        thread_id = get_ident()
        key = thread_id, info["generation"]
        if phase == "start":
            self.pending_gc[key] = perf_counter(), thread_time(), self.state["render"]
        elif phase == "stop" and key in self.pending_gc:
            began, cpu, render = self.pending_gc.pop(key)
            bucket = (thread_id, info["generation"], render)
            if bucket not in self.gc_aggregates:
                self.gc_aggregates[bucket] = {"thread_id": thread_id, "generation": info["generation"],
                    "render": render, "collections": 0, "elapsed_seconds_total": 0.,
                    "elapsed_seconds_max": 0., "thread_cpu_seconds_total": 0., "collected": 0, "uncollectable": 0}
            aggregate = self.gc_aggregates[bucket]
            elapsed = perf_counter() - began
            aggregate["collections"] += 1
            aggregate["elapsed_seconds_total"] += elapsed
            aggregate["elapsed_seconds_max"] = max(aggregate["elapsed_seconds_max"], elapsed)
            aggregate["thread_cpu_seconds_total"] += thread_time() - cpu
            aggregate["collected"] += info["collected"]
            aggregate["uncollectable"] += info["uncollectable"]

    def records(self):
        return sorted(self.aggregates.values(), key=lambda row: (
            -1 if row["render"] is None else row["render"], row["label"], row["thread_id"],
            row["metadata_last_declared"].get("role") or ""))

    def gc_records(self):
        return sorted(self.gc_aggregates.values(), key=lambda row: (
            -1 if row["render"] is None else row["render"], row["generation"], row["thread_id"]))


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



@contextmanager
def observe_product(product):
    """Instrument only the locked native child; driver never imports the product."""
    from rc6_shadow_runtime import packed_storage, persistence, serialization
    import rc6_audit_evidence.sqlite_snapshot as snapshot
    digest_roles = {record["payload_digest"]: role for role, record in product.manifest["files"].items()}
    renders = []
    state = {"render": None, "renders": renders, "end": product.diagnostic_deadline or monotonic()+60}
    trace = DiagnosticTrace(digest_roles, state)
    original_copy = snapshot.readonly_copy

    @contextmanager
    def traced_copy(*positional, **keywords):
        context = original_copy(*positional, **keywords)
        began, cpu, thread_cpu = perf_counter(), process_time(), thread_time()
        try:
            connection = context.__enter__()
        except BaseException as failure:
            trace.record("source_capture_enter", began, cpu, thread_cpu, type(failure).__name__)
            raise
        trace.record("source_capture_enter", began, cpu, thread_cpu)
        try:
            yield connection
        except BaseException:
            exception = sys.exc_info()
            close = trace.timed("source_capture_exit", context.__exit__)
            if not close(*exception):
                raise
        else:
            trace.timed("source_capture_exit", context.__exit__)(None, None, None)

    original_build = product.build_page

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

    original_require = product.require

    def bounded_require(value, gate, details=None):
        return require_then_window_end(original_require, value, gate, details, end=state["end"])

    replacements = [(snapshot, "readonly_copy", traced_copy),
        (serialization, "_storage_bytes", trace.timed("storage_v1", serialization._storage_bytes, codec=True)),
        (packed_storage, "unpack_wire", trace.timed("fresh_unpack_wire", packed_storage.unpack_wire, codec=True))]
    for name in ("_slots", "_expanded", "_array", "_inflate"):
        replacements.append((packed_storage, name, trace.timed("packed" + name, getattr(packed_storage, name))))
    member_roles = {name: role for role, name in persistence.GENERATION_ROLES.items()}
    replacements.extend([
        (persistence.EvidenceFiles, "_bytes", trace.timed("member_bytes", persistence.EvidenceFiles._bytes,
                                                        member_roles=member_roles)),
        (persistence.EvidenceFiles, "_wire_generation", trace.timed("wire_generation", persistence.EvidenceFiles._wire_generation)),
        (persistence, "read_committed_projection", trace.timed("canonical_query", persistence.read_committed_projection)),
        (product, "build_page", traced_build), (product, "require", bounded_require),
        (product, "readonly_copy", traced_copy)])
    with ExitStack() as instrumentation:
        for owner, name, replacement in replacements:
            instrumentation.enter_context(patch.object(owner, name, replacement))
        gc.callbacks.append(trace.collect)
        try:
            yield trace
        finally:
            gc.callbacks.remove(trace.collect)


def run(args):
    sys.dont_write_bytecode = True
    index = json.loads(args.index.read_text())
    root = Path(index.get("extracted_root", Path(__file__).resolve().parents[1])).resolve()
    database, native_root = args.database.resolve(), args.root.resolve()
    runner = Path(__file__).resolve()
    output = args.output.resolve()
    previous_path = sys.path[:]
    sys.path.insert(0, str(root))
    try:
        from tests.rc6_browser_ipc import (GateFailure, ProductDiagnosticWindowEnded, imported_source,
            output_guard, read_source_index, source_inventory)
        try:
            output_guard(args.output, database, native_root, source_root=root, gate="NEW_DIAGNOSTIC_OUTPUT_OUTSIDE_SOURCE_REQUIRED")
        except GateFailure as error:
            raise ValueError(str(error)) from error
        index = read_source_index(args.index, root)
        assert index.get("overlays") == []
        before = source_inventory(root)
        assert {key:value["sha256"] for key,value in before.items()} == index["source_file_hashes"]
        from tests import ci_rc6_projection_large_browser as browser
        clients, network = [], []
        end = monotonic()+60
        original_require = browser.require
        def bounded_require(value, gate, details=None):
            return require_then_window_end(original_require, value, gate, details, end=end)
        def no_network(*_args, **_kwargs):
            network.append("BLOCKED")
            raise GateFailure("PROVIDER_OR_NETWORK_CALLED")
        result, error_class, details = "NO_FAILURE_OBSERVED_WITHIN_DIAGNOSTIC_WINDOW", None, {}
        with ExitStack() as guards:
            guards.enter_context(patch.object(browser, "require", bounded_require))
            guards.enter_context(patch.object(socket.socket, "connect", no_network))
            guards.enter_context(patch.object(socket, "create_connection", no_network))
            try:
                browser.run(database, native_root, output/"browser",
                    product_python=getattr(args, "product_python", sys.executable), index=args.index, diagnostic=True,
                    product_python_version=getattr(args, "product_python_version", None),
                    client_sink=clients, diagnostic_deadline=end, require_complete_index=True)
            except (DiagnosticWindowEnded, ProductDiagnosticWindowEnded):
                pass
            except BaseException as error:
                result, error_class = "NATIVE_OR_BROWSER_FAILURE_RETAINED", type(error).__name__
                details = {"gate": str(error) if isinstance(error, GateFailure) else "DIAGNOSTIC_CALL_FAILED",
                    "native_details": error.details if isinstance(error, GateFailure) else {}}
        client = clients[0] if clients else None
        native = client.finish_receipt if client and client.finish_receipt else {}
        after = source_inventory(root)
        closure, unexpected = imported_source(root, before)
        proof = before == after and native.get("source_proof_pass") is True and not network and not unexpected
        proof = proof and all(row["matches_archived_blob"] for row in closure)
        receipt = {"schema": "rc6.native-browser-components-diagnostic.v2", "status": "DIAGNOSTIC_ONLY_"+result,
            "acceptance_complete": False, "native_gate_acceptance_claim": False, "source_sha": index["source_sha"],
            "candidate_tree_sha": index["candidate_tree_sha"], "archive_sha256": index["archive_sha256"], "overlays": [],
            "runtime_instrumentation": "TIMING_WRAPPERS_AND_GC_CALLBACK_IN_LOCKED_PRODUCT_CHILD_ONLY",
            "runner_sha256": hashlib.sha256(runner.read_bytes()).hexdigest(), "error_class": error_class, "details": details,
            "renders": native.get("renders", []), "stage_aggregates": native.get("stage_aggregates", []),
            "gc_aggregates": native.get("gc_aggregates", []), "source_proof_pass": proof,
            "tracked_source_files": len(before), "tracked_source_hashes_unchanged": before == after,
            "tracked_source_hashes_and_modes_unchanged": before == after,
            "custody_inventory_before": native.get("custody_inventory_before"), "custody_inventory_after": native.get("custody_inventory_after"),
            "native_custody_unchanged": native.get("native_custody_unchanged", False),
            "imported_product_modules": native.get("imported_product_modules", []), "driver_imported_source": closure,
            "unexpected_product_imports": native.get("unexpected_product_imports", [])+unexpected,
            "network_attempts": native.get("network_attempts", 0)+len(network), "source_sqlite_attempts": native.get("source_sqlite_attempts", 0),
            "product_environment": client.product_environment if client else None,
            "driver_environment": client.driver_environment if client else None,
            "scope_limits": ["60s diagnostic window, no acceptance even if no failure was observed.",
                "Native product is locked child157; Playwright driver158 cannot import financial/dashboard modules.",
                "Native stages exclude stdio; acceptance wall includes IPC, snapshot/render and HTML/JSON.",
                "Nested/overlapping elapsed and CPU totals cannot be added; declared codec counts are not validation.",
                "Member read counts are observations, not independent cache-absence or full logical proof.",
                "cgroup includes every process in the container; instrumentation adds overhead."]}
        output.mkdir(parents=True, exist_ok=True)
        with (output/"diagnostic.json").open("x") as stream:
            stream.write(json.dumps(receipt, sort_keys=True, indent=2)+"\n")
        print(json.dumps({"status": receipt["status"], "acceptance_complete": False,
                         "renders": len(receipt["renders"]), "source_proof_pass": proof}))
        return 0 if proof else 1
    finally:
        sys.path[:] = previous_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-python", type=Path, required=True)
    parser.add_argument("--product-python-version", choices=("3.11", "3.12"), default="3.11")
    parser.add_argument("--index", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    raise SystemExit(run(parser.parse_args()))
