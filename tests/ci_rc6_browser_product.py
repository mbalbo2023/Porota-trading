"""Native browser handlers in the exact Python 3.11 / 157-distribution process.

The driver speaks bounded JSON over stdio. No browser library, product response
cache, adjusted clock or weakened reader participates in this process.
"""
import argparse
from contextlib import ExitStack, redirect_stdout
from datetime import datetime
import gc
import hashlib
import json
import os
from math import isfinite
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
from time import monotonic, perf_counter, process_time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.rc6_browser_ipc import (
    GateFailure, MAX_FRAME, PROTOCOL, environment_receipt, frame, imported_source,
    parse_frame, protected_bytes, require, source_inventory, source_sqlite_guard, source_start,
)


class NativeProduct:
    def __init__(self, root, index, *, diagnostic=False, diagnostic_deadline=None):
        self.root, self.index, self.diagnostic = root, index, diagnostic
        self.diagnostic_deadline = diagnostic_deadline
        self.before, self.source_index = source_start(root, index)
        self.guards, self.resources, self.network, self.source_calls = ExitStack(), ExitStack(), [], []
        self.initialized, self.finished, self.database = False, False, None
        self.native_root, self.custody_before, self.fixture = None, None, None
        self.require = require
        self.trace = None
        def no_network(*_args, **_kwargs):
            self.network.append("BLOCKED")
            raise GateFailure("PROVIDER_OR_NETWORK_CALLED")
        for owner, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"),
                            (socket.socket, "sendto"), (socket, "create_connection"), (socket, "getaddrinfo")):
            self.guards.enter_context(patch.object(owner, name, no_network))

    def initialize(self, request):
        require(not self.initialized and self.database is None and not self.finished,
                "PRODUCT_IPC_INITIALIZATION_REPEATED")
        mode = request.get("mode")
        require(mode in {"LARGE", "NORMAL"}, "PRODUCT_IPC_MODE_INVALID")
        # No native import occurs before the environment and whole-source
        # handshake. NORMAL keeps the original writer supervision/GC behavior.
        if mode == "NORMAL":
            temporary = self.resources.enter_context(tempfile.TemporaryDirectory(prefix="porota-native-browser-product-"))
            from tests.rc6_dashboard_native_fixture import native_fixture
            self.fixture = native_fixture(Path(temporary))
            self.fixture.broker.supervise_futures(self.fixture.as_of.isoformat())
            gc.collect()  # Original normal-runner writer finalization, unchanged.
            self.database, self.native_root, self.cut_at = self.fixture.database, self.fixture.root, self.fixture.as_of
        else:
            require(type(request.get("database")) is str and type(request.get("root")) is str,
                    "PRODUCT_IPC_SOURCE_CONTRACT")
            paths = (Path(request["database"]), Path(request["root"]))
            require(not any(member.is_symlink() for path in paths for member in (path, *path.parents)),
                    "SOURCE_ALIAS_FORBIDDEN")
            self.database, self.native_root = (path.resolve() for path in paths)
            require(self.database.is_file() and self.native_root.is_dir(), "COMPLETED_NATIVE_FIXTURE_REQUIRED")

        self.guards.enter_context(patch.dict(os.environ, {"POROTA_DYNAMIC_SHADOW_ROOT": str(self.native_root),
            "POROTA_SHADOW_RUNTIME_ROOT": str(self.native_root)}))
        self.guards.enter_context(patch.object(sqlite3, "connect",
            source_sqlite_guard(self.database, sqlite3.connect, self.source_calls)))

        from rc6_shadow_runtime import persistence
        from rc6_audit_evidence import sqlite_snapshot
        from rc6_trader_dashboard.datasets import shadow_rows
        from rc6_trader_dashboard.navigation import CANONICAL_PATHS, LEGACY
        from rc6_trader_dashboard.projected_generation import VERIFICATION_LEVEL
        from rc6_trader_dashboard.projection import Projection, Store
        from rc6_trader_dashboard.routes import build_page
        from tests.ci_rc6_projection_large_reader import custody_inventory, require_native_large_cut
        self.persistence, self.readonly_copy, self.build_page = persistence, sqlite_snapshot.readonly_copy, build_page
        self.custody_inventory, self.verification_level = custody_inventory, VERIFICATION_LEVEL
        self.custody_before = custody_inventory(self.database, self.native_root)
        self.pointer = json.loads(protected_bytes(self.native_root / "CURRENT.json"))
        self.manifest = json.loads(protected_bytes(self.native_root / ("gen-" + self.pointer["generation_id"]) / "manifest.json"))
        self.require(set(self.manifest["files"]) == {"report", "checkpoint", "status", "projection"}, "FOUR_ROLES_REQUIRED")
        self.cut_at = datetime.fromisoformat(self.manifest["as_of"].replace("Z", "+00:00"))
        forbidden = {self.manifest["files"][role]["payload_digest"] for role in ("report", "checkpoint")}
        original_decode = persistence.decode_storage
        def bounded_decode(value, *args, **kwargs):
            if isinstance(value, dict):
                self.require(value.get("logical_sha256") not in forbidden, "ORIGINAL_REPORT_OR_CHECKPOINT_DECODED")
            return original_decode(value, *args, **kwargs)
        self.guards.enter_context(patch.object(persistence, "decode_storage", bounded_decode))
        if self.diagnostic:
            from tests.ci_rc6_projection_browser_diagnostic import observe_product
            self.trace = self.guards.enter_context(observe_product(self))

        result = {"mode": mode, "database": str(self.database), "root": str(self.native_root),
            "pointer": self.pointer, "source_cut": self.cut_at.isoformat(), "native_generation_roles": sorted(self.manifest["files"]),
            "verification_level": VERIFICATION_LEVEL, "canonical_paths": list(CANONICAL_PATHS), "legacy": LEGACY,
            "source_pin_complete": self.source_index is not None}
        if mode == "LARGE":
            with self.readonly_copy(self.database, validate=False, deadline=monotonic() + 2) as copied:
                catalog_count = copied.execute("SELECT count(*) FROM financial_instrument_catalog").fetchone()[0]
                observation_count = copied.execute("SELECT count(*) FROM ppi_intraday_points").fetchone()[0]
                last_identity = tuple(copied.execute("SELECT ticker,instrument_type,market,currency,settlement "
                    "FROM financial_instrument_catalog ORDER BY ticker DESC LIMIT 1").fetchone())
            preflight_begin = perf_counter()
            with Store(self.database, now=self.cut_at) as store:
                projection = Projection(store)
                cut = projection.shadow
                self.require(cut["state"] == "COMMITTED_COHERENT_SHADOW", "PROJECTED_CUT_UNAVAILABLE",
                    {"state": cut["state"], "reason": cut["reason"], "error_class": cut.get("error_class"),
                     "elapsed_seconds": perf_counter() - preflight_begin, "scope": "default"})
                self.require(cut["pointer"] == self.pointer and cut["verification_level"] == VERIFICATION_LEVEL,
                             "BROWSER_PREFLIGHT_CUT_OR_VERIFICATION_MISMATCH")
                require_native_large_cut(cut, catalog_count=catalog_count, observation_count=observation_count)
                planner = shadow_rows(projection, "opportunities")
                self.require(planner.state == "AVAILABLE" and planner.total == 2 * catalog_count,
                             "PLANNER_DENOMINATOR_INCOMPLETE")
            self.require(not store.errors, "SOURCE_SNAPSHOT_REJECTED")
            preflight_begin = perf_counter()
            with Store(self.database, now=self.cut_at) as store:
                scoped_projection = Projection(store, {"family": last_identity[1]})
                scoped_cut = scoped_projection.shadow
                self.require(scoped_cut["state"] == "COMMITTED_COHERENT_SHADOW", "PROJECTED_CUT_UNAVAILABLE",
                    {"state": scoped_cut["state"], "reason": scoped_cut["reason"], "error_class": scoped_cut.get("error_class"),
                     "elapsed_seconds": perf_counter() - preflight_begin, "scope": "family"})
                scoped = scoped_projection.funnel_scope
                self.require(scoped_cut["pointer"] == self.pointer, "SCOPED_PREFLIGHT_CHANGED_CUT")
                self.require(scoped["state"] == "AVAILABLE" and scoped["total_groups"] >= 2 * catalog_count,
                             "COMPLETE_COHORT_POPULATION_UNAVAILABLE")
            self.require(not store.errors, "SOURCE_SNAPSHOT_REJECTED")
            result.update({"catalog_full_identities": catalog_count, "observations": observation_count,
                "last_identity": last_identity, "planner_rows": planner.total, "funnel_groups": scoped["total_groups"],
                "custody": cut["export_contract"]["custody"]})
        self.initialized = True
        return result

    def render(self, request):
        self.require(self.initialized and not self.finished, "PRODUCT_IPC_NOT_READY")
        path, params = request.get("path"), request.get("params")
        self.require(type(path) is str and path in self.paths
            and type(params) is dict and len(params) <= 32
            and all(type(key) is str and type(value) is str and len(key) <= 128 and len(value) <= 4096
                    for key, value in params.items()), "PRODUCT_IPC_RENDER_SELECTION_INVALID")
        begin, cpu = perf_counter(), process_time()
        native_reader, observed = self.persistence.read_committed_projection, []
        def same_native_cut(*arguments, **keywords):
            document = native_reader(*arguments, **keywords)
            require(document["pointer"] == self.pointer
                and document["manifest"]["as_of"] == self.manifest["as_of"]
                and document["manifest"]["configuration_fingerprint"] == self.manifest["configuration_fingerprint"]
                and document["export_contract"]["verification_level"] == self.verification_level,
                "RENDER_CHANGED_COMMITTED_CUT")
            proofs = document["export_contract"]["verified_payloads"]
            require(set(proofs) == set(self.manifest["files"])
                and all(proofs[role]["payload_digest"] == self.manifest["files"][role]["payload_digest"]
                        for role in proofs), "RENDER_CHANGED_VERIFIED_MEMBER_DIGESTS")
            observed.append({"pointer": document["pointer"],
                "source_cut": document["manifest"]["as_of"],
                "role_digests": {role: proof["payload_digest"] for role, proof in proofs.items()}})
            return document
        with patch.object(self.persistence, "read_committed_projection", same_native_cut):
            html, headers = self.build_page(path, params, self.database, now=self.cut_at)
        self.require(observed, "RENDER_DISCARDED_VERIFIED_CUT")
        result = {"html": html, "headers": headers, "path": path, "filters": params,
            "source_cut": datetime.fromisoformat(observed[-1]["source_cut"].replace("Z", "+00:00")).isoformat(),
            "pointer": observed[-1]["pointer"], "native_role_payload_digests": observed[-1]["role_digests"],
            "native_verified_queries": len(observed),
            "native_elapsed_seconds": perf_counter() - begin, "native_process_cpu_seconds": process_time() - cpu,
            "html_bytes": len(html.encode())}
        # The response is one actual handler result, never a retained HTML/JSON
        # response from a prior request. Oversize is rejected, not truncated.
        self.require(result["html_bytes"] < MAX_FRAME, "RENDER_EXCEEDS_REQUEST_BUDGET")
        return result

    @property
    def paths(self):
        from rc6_trader_dashboard.navigation import CANONICAL_PATHS, LEGACY
        return CANONICAL_PATHS.keys() | LEGACY.keys()

    def health(self):
        self.require(self.initialized and self.source_index is not None, "PRODUCT_HEALTH_SOURCE_INDEX_REQUIRED")
        from scripts.rc6_shadow_health_gate import read_health
        begin = perf_counter()
        result = read_health(str(self.database), source_sha=self.source_index["source_sha"],
            tree_sha=self.source_index["candidate_tree_sha"], now=self.cut_at)
        self.require(perf_counter() - begin <= 2, "HEALTH_EXCEEDS_REQUEST_BUDGET")
        return result

    def finish(self):
        require(not self.finished, "PRODUCT_IPC_FINISH_REPEATED")
        self.finished = True
        self.guards.close()
        custody_after = self.custody_inventory(self.database, self.native_root) if self.custody_before is not None else None
        after = source_inventory(self.root)
        closure, unexpected = imported_source(self.root, self.before)
        proof = self.before == after and not unexpected and not self.network and "SOURCE_BLOCKED" not in self.source_calls
        proof = proof and all(row["matches_archived_blob"] for row in closure)
        proof = proof and (self.custody_before is None or self.custody_before == custody_after)
        receipt = {"source_proof_pass": proof, "tracked_source_files": len(self.before),
            "tracked_source_hashes_and_modes_unchanged": self.before == after,
            "source_inventory_before": self.before, "source_inventory_after": after,
            "imported_product_modules": closure, "unexpected_product_imports": unexpected,
            "network_attempts": len(self.network), "source_sqlite_attempts": self.source_calls.count("SOURCE_BLOCKED"),
            "custody_inventory_before": self.custody_before, "custody_inventory_after": custody_after,
            "native_custody_unchanged": self.custody_before is not None and self.custody_before == custody_after,
            "custody_verification_scope": "SELECTED_NATIVE_SOURCE" if self.custody_before is not None else "NOT_EXERCISED",
            "source_pin_complete": self.source_index is not None}
        if self.trace is not None:
            receipt.update({"stage_aggregates": self.trace.records(), "gc_aggregates": self.trace.gc_records(),
                            "renders": self.trace.state["renders"]})
        return receipt


def error_response(error):
    from tests.ci_rc6_projection_browser_diagnostic import DiagnosticWindowEnded
    return {"gate": "DIAGNOSTIC_WINDOW_ENDED" if isinstance(error, DiagnosticWindowEnded)
            else str(error) if isinstance(error, GateFailure) else "NATIVE_BROWSER_REJECTED",
            "error_class": type(error).__name__, "details": error.details if isinstance(error, GateFailure) else {}}


def serve(args, input_stream=None, output_stream=None):
    input_stream = input_stream or sys.stdin.buffer
    output_stream = output_stream or sys.stdout.buffer
    product, request_id, finished = None, 0, False
    # All native print/log output goes to stderr. The stdout protocol has one
    # bounded response frame per request and cannot be polluted by a writer.
    with redirect_stdout(sys.stderr):
        try:
            environment = environment_receipt(ROOT, product=True, executable=args.expected_python,
                python_version=args.expected_python_version)
            require(args.diagnostic_deadline is None or args.diagnostic and isfinite(args.diagnostic_deadline),
                    "DIAGNOSTIC_WINDOW_INVALID")
            product = NativeProduct(ROOT, args.index, diagnostic=args.diagnostic,
                diagnostic_deadline=args.diagnostic_deadline)
            response = {"protocol": PROTOCOL, "id": 0, "ok": True, "result": {"environment": environment}}
        except Exception as error:
            output_stream.write(frame({"protocol": PROTOCOL, "id": 0, "ok": False, "error": error_response(error)}))
            output_stream.flush()
            return 1
        output_stream.write(frame(response))
        output_stream.flush()
        try:
            while True:
                raw = input_stream.readline(MAX_FRAME)
                if not raw:
                    break
                try:
                    request = parse_frame(raw)
                    require(request["id"] == request_id + 1, "PRODUCT_IPC_REQUEST_ID")
                    request_id = request["id"]
                    operation = request.get("op")
                    handlers = {"initialize": product.initialize, "render": product.render,
                                "health": lambda _request: product.health(), "finish": lambda _request: product.finish()}
                    require(type(operation) is str and operation in handlers, "PRODUCT_IPC_OPERATION_INVALID")
                    fields = {"initialize": {"protocol", "id", "op", "mode"},
                              "render": {"protocol", "id", "op", "path", "params"},
                              "health": {"protocol", "id", "op"}, "finish": {"protocol", "id", "op"}}[operation]
                    if operation == "initialize" and request.get("mode") == "LARGE":
                        fields = fields | {"database", "root"}
                    require(set(request) == fields, "PRODUCT_IPC_REQUEST_FIELDS_INVALID")
                    result = handlers[operation](request)
                    response = {"protocol": PROTOCOL, "id": request_id, "ok": True, "result": result}
                    encoded = frame(response)
                    finished = operation == "finish"
                except Exception as error:
                    encoded = frame({"protocol": PROTOCOL, "id": request_id, "ok": False, "error": error_response(error)})
                output_stream.write(encoded)
                output_stream.flush()
                if finished:
                    return 0
        finally:
            product.guards.close()
            product.resources.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-python", required=True)
    parser.add_argument("--expected-python-version", choices=("3.11", "3.12"))
    parser.add_argument("--index", type=Path)
    parser.add_argument("--diagnostic", action="store_true")
    parser.add_argument("--diagnostic-deadline", type=float)
    raise SystemExit(serve(parser.parse_args()))
