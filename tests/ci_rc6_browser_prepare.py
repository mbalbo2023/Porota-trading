"""Exact157 native preparation, completed before the browser consumer RPC.

This private small dataset is not the canonical 12000/60000 benchmark. Every
original PRE/quote/OPEN tick and writer/GC boundary is preserved.
"""
import argparse
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timezone
import gc
import hashlib
import json
from math import isfinite
import os
from pathlib import Path
import socket
import sqlite3
import sys
from time import monotonic, perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_STARTED = monotonic()
sys.path.insert(0, str(ROOT))

from tests.rc6_browser_ipc import (
    GateFailure, MAX_FRAME, PROTOCOL, environment_receipt, frame, imported_source,
    output_guard, protected_bytes, require, source_inventory, source_sqlite_guard, source_start,
)
from tests.rc6_browser_prepared import (
    PREPARATION_SECONDS, PREPARED_SCHEMA, PREPARED_VARIANTS, inventory_digest,
)


def raw_archive_controls(index, source_index):
    raw = index.parent / "source.commit.raw"
    archive = index.parent / "source.tar"
    require(not any(member.is_symlink() for path in (raw, archive) for member in (path, *path.parents))
            and raw.is_file() and archive.is_file(), "PREPARATION_RAW_GIT_CONTROLS_REQUIRED")
    commit = protected_bytes(raw)
    require(hashlib.sha256(commit).hexdigest() == source_index["raw_git_commit_sha256_declared"]
            and hashlib.sha1(b"commit " + str(len(commit)).encode() + b"\0" + commit).hexdigest()
                == source_index["source_sha"]
            and commit.splitlines()[0] == ("tree " + source_index["candidate_tree_sha"]).encode(),
            "PREPARATION_RAW_GIT_COMMIT_MISMATCH")
    declared = json.loads(protected_bytes(index))
    require(hashlib.sha256(protected_bytes(archive)).hexdigest() == declared["tar_sha256"],
            "PREPARATION_RAW_ARCHIVE_MISMATCH")


def require_finalized_sqlite_connections():
    # Inspect existing connection handles without a cursor, SQL, reconnect or
    # extra GC. The original two writer finalization calls remain unchanged.
    open_handles = 0
    for instance in gc.get_objects():
        if isinstance(instance, sqlite3.Connection):
            try:
                instance.in_transaction
            except sqlite3.ProgrammingError:
                continue  # The existing handle is already closed.
            open_handles += 1
    require(open_handles == 0, "NATIVE_PREPARATION_WRITER_NOT_FINALIZED", {"open_handles": open_handles})


def run(output, *, executable, index, variant, python_version=None, absolute_deadline=None):
    begin, now = perf_counter(), monotonic()
    deadline = now + PREPARATION_SECONDS if absolute_deadline is None else absolute_deadline
    require(isfinite(deadline) and now < deadline <= now + PREPARATION_SECONDS,
            "NATIVE_PREPARATION_DEADLINE")
    output_guard(output)
    environment = environment_receipt(ROOT, product=True, executable=executable, python_version=python_version)
    before, source_index = source_start(ROOT, index)
    require(source_index and source_index["pin_complete"], "WHOLE_COMPLETE_RAW_SOURCE_INDEX_REQUIRED")
    raw_archive_controls(index, source_index)
    require(variant in PREPARED_VARIANTS, "PREPARED_VARIANT_REJECTED")
    output.mkdir(parents=True, exist_ok=False)
    progress_path = output / "progress.jsonl"
    progress, network, calls = [], [], []
    def record(stage, **values):
        require(monotonic() <= deadline, "NATIVE_PREPARATION_DEADLINE")
        event = {"stage": stage, "elapsed_seconds": perf_counter() - begin, **values}
        raw = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        require(len(progress) < 64 and len(raw.encode()) < 4096, "NATIVE_PREPARATION_PROGRESS_BUDGET")
        with progress_path.open("a") as stream:
            stream.write(raw)
            stream.flush()
        progress.append(event)
    def no_network(*_args, **_kwargs):
        network.append("BLOCKED")
        raise GateFailure("PROVIDER_OR_NETWORK_CALLED")
    data = output / "data"
    database = data / "native-paper.db"
    native_root = data / "artifacts/native-paper.db/dynamic-shadow"
    bindings = {key: "" for key in os.environ
                if key.startswith("POROTA_CAPACITY_") and key.endswith("_PATH")}
    bindings.update({"DATA_DIR": str(data), "PAPER_V17_DB_PATH": str(database),
        "HIST_DB_PATH": str(data / "absent-history.db"),
        "POROTA_DYNAMIC_SHADOW_ROOT": str(native_root), "POROTA_SHADOW_RUNTIME_ROOT": str(native_root),
        "POROTA_DYNAMIC_SHADOW_ARCHIVE_ROOT": str(data / "artifacts/native-paper.db/dynamic-shadow-archive"),
        "POROTA_IOL_SHADOW_ROOT": str(data / "market"), "POROTA_IOL_SHADOW_CACHE_PATH": "",
        "POROTA_BUILD_SHA": source_index["source_sha"], "POROTA_CANDIDATE_TREE_SHA": source_index["candidate_tree_sha"],
        "POROTA_DYNAMIC_CAPACITY_MODE": "OFF",
        "POROTA_CAPACITY_POLICY_PATH": str(ROOT / "ops/policy/rc6-dynamic-capacity-v1.json"),
        "POROTA_CAPACITY_SHADOW_PATH": str(native_root / "CURRENT.json")})
    with ExitStack() as guards:
        guards.enter_context(patch.dict(os.environ, bindings))
        for owner, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"),
                            (socket.socket, "sendto"), (socket, "create_connection"), (socket, "getaddrinfo")):
            guards.enter_context(patch.object(owner, name, no_network))
        # Metadata, complete Git pin and the output policy were checked before
        # this first import or construction of any financial fixture.
        from tests.rc6_dashboard_native_fixture import native_fixture
        record("FIXTURE_BEGIN")
        options = {"multifamily": True, "count": 35} if variant == "MULTIFAMILY" else {}
        fixture = native_fixture(data, progress=record, **options)
        record("FIXTURE_END")
        fixture.broker.supervise_futures(fixture.as_of.isoformat())
        gc.collect()  # Original normal consumer's final writer boundary.
        require_finalized_sqlite_connections()
        record("WRITERS_FINALIZED")
        require(fixture.database == database and fixture.root == native_root,
                "NATIVE_PREPARATION_PRIVATE_DATASET_MISMATCH")
        from tests.ci_rc6_projection_large_reader import custody_inventory
        from rc6_audit_evidence.sqlite_snapshot import readonly_copy
        from rc6_shadow_runtime import persistence
        from rc6_trader_dashboard.projected_generation import VERIFICATION_LEVEL
        guards.enter_context(patch.object(sqlite3, "connect", source_sqlite_guard(database, sqlite3.connect, calls)))
        custody_before = custody_inventory(database, native_root)
        record("READONLY_PROOF_BEGIN")
        with readonly_copy(database, validate=False, deadline=min(deadline, monotonic() + 2)) as copied:
            catalog = [list(row) for row in copied.execute("SELECT ticker,instrument_type,market,currency,settlement "
                "FROM financial_instrument_catalog ORDER BY ticker,instrument_type,market,currency,settlement")]
        families = {}
        for row in catalog:
            families[row[1]] = families.get(row[1], 0) + 1
        cut = persistence.read_committed_projection(native_root, limit=10, deadline=min(deadline, monotonic() + 1))
        report = fixture.cut["report"]
        pointer = json.loads(protected_bytes(native_root / "CURRENT.json"))
        manifest = json.loads(protected_bytes(native_root / ("gen-" + pointer["generation_id"]) / "manifest.json"))
        require(report["phase"] == "OPEN" and report["mode"] == "SHADOW"
                and report["provider_requests"] == report["real_orders_sent"] == 0
                and report["real_routes"] == "NOT_CALLED" and report["ppi_watch"] == "UNTOUCHED",
                "NATIVE_PREPARATION_OPEN_AND_SAFETY_REQUIRED")
        require(cut["pointer"] == pointer and cut["manifest"] == manifest
                and fixture.cut["pointer"] == pointer and fixture.cut["manifest"] == manifest
                and fixture.cut["export_contract"]["verification_level"] == "FULL_LOGICAL_SEMANTICS"
                and cut["export_contract"]["verification_level"] == VERIFICATION_LEVEL
                and set(manifest["files"]) == {"report", "checkpoint", "status", "projection"},
                "NATIVE_PREPARATION_COHERENT_FOUR_ROLES_REQUIRED")
        require(len(catalog) == PREPARED_VARIANTS[variant], "NATIVE_PREPARATION_CATALOG_INCOMPLETE")
        if variant == "MULTIFAMILY":
            from bs_instrument_contracts import FAMILIES
            require(set(families) == FAMILIES and families["FUTUROS"] == 3
                    and all(count == 35 for family, count in families.items() if family != "FUTUROS"),
                    "NATIVE_PREPARATION_MULTIFAMILY_INCOMPLETE")
            require({row[0] for row in catalog if row[1] == "FUTUROS"} == {"DLR/OCT26", "DLR/NOV26", "DLR/DIC26"},
                    "NATIVE_PREPARATION_UNPROVEN_DLR_SERIES")
        custody_after = custody_inventory(database, native_root)
        require_finalized_sqlite_connections()
        require(custody_before == custody_after and calls.count("SOURCE_BLOCKED") == 0,
                "NATIVE_PREPARATION_READONLY_PROOF_CHANGED_CUSTODY")
        record("READONLY_PROOF_END")
    after = source_inventory(ROOT)
    closure, unexpected = imported_source(ROOT, before)
    require(before == after and not unexpected and closure
            and all(row["matches_archived_blob"] for row in closure), "NATIVE_PREPARATION_SOURCE_PROOF_FAILED")
    require(not network, "PROVIDER_OR_NETWORK_CALLED")
    ticks = [{key: event[key] for key in ("as_of", "generation_id", "sequence", "phase", "mode")}
             for event in progress if event["stage"] == "TICK_END"]
    require(len(ticks) == 10 and [tick["sequence"] for tick in ticks] == list(range(1, 11))
            and ticks[-1]["generation_id"] == pointer["generation_id"], "NATIVE_PREPARATION_ORIGINAL_TICKS_MISSING")
    return {"schema": PREPARED_SCHEMA, "scope": "COMPLETED_SMALL_NATIVE_PREPARATION_NOT_BIG_ACCEPTANCE",
        "status": "DATA_READY", "variant": variant, "big_acceptance_claim": False,
        "database": str(database), "root": str(native_root), "source_cut": fixture.as_of.isoformat(),
        "pointer": pointer, "manifest": manifest, "native_phase": report["phase"], "native_ticks": ticks,
        "verification_level": fixture.cut["export_contract"]["verification_level"],
        "consumer_verification_level": cut["export_contract"]["verification_level"],
        "custody_verification_scope": cut["export_contract"]["custody"],
        "product_environment": environment, "producer_pid": os.getpid(),
        "producer_recorded_at": datetime.now(timezone.utc).isoformat(),
        "source_sha": source_index["source_sha"], "candidate_tree_sha": source_index["candidate_tree_sha"],
        "source_pin_complete": True, "tracked_source_files": len(before),
        "source_before_digest": inventory_digest(before), "source_after_digest": inventory_digest(after),
        "source_proof_pass": True, "imported_product_modules": closure, "unexpected_product_imports": unexpected,
        "catalog_full_identities": catalog, "catalog_families": families, "custody_inventory": custody_after,
        "provider_attempts": 0, "real_orders_sent": report["real_orders_sent"], "real_routes": report["real_routes"],
        "ppi_watch": report["ppi_watch"], "preparation_elapsed_seconds_before_emission": perf_counter() - begin,
        "all_sqlite_writers_closed_before_custody": True,
        "preparation_cap_seconds": PREPARATION_SECONDS, "progress_records": len(progress),
        "progress_sha256": hashlib.sha256(protected_bytes(progress_path)).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-python", type=Path, required=True)
    parser.add_argument("--expected-python-version", choices=("3.11", "3.12"))
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--variant", choices=tuple(PREPARED_VARIANTS), required=True)
    parser.add_argument("--absolute-deadline", type=float)
    args = parser.parse_args()
    if args.absolute_deadline is None:
        args.absolute_deadline = SCRIPT_STARTED + PREPARATION_SECONDS
    try:
        output_guard(args.output)
    except GateFailure as error:
        parser.error(str(error))
    channel = sys.stdout.buffer
    try:
        with redirect_stdout(sys.stderr):
            result = run(args.output, executable=args.expected_python, index=args.index, variant=args.variant,
                python_version=args.expected_python_version, absolute_deadline=args.absolute_deadline)
        raw = frame({"protocol": PROTOCOL, "id": 0, "ok": True, "result": result})
        require(args.absolute_deadline is None or monotonic() <= args.absolute_deadline, "NATIVE_PREPARATION_DEADLINE")
        (args.output / "native-fixture.json").write_bytes(raw)
        channel.write(raw)
        channel.flush()
        require(args.absolute_deadline is None or monotonic() <= args.absolute_deadline, "NATIVE_PREPARATION_DEADLINE")
    except BaseException as error:
        failure = {"gate": str(error) if isinstance(error, GateFailure) else "NATIVE_PREPARATION_REJECTED",
                   "error_class": type(error).__name__, "details": error.details if isinstance(error, GateFailure) else {}}
        raw = frame({"protocol": PROTOCOL, "id": 0, "ok": False, "error": failure})
        if args.output.exists():
            (args.output / "preparation-failure.json").write_bytes(raw)
        channel.write(raw)
        channel.flush()
        raise SystemExit(1)
