"""Replay native budget reads from an entire independently pinned Git archive.

Run outside that archive. Only explicitly new synthetic fixtures are writable;
every archived blob/mode and every imported project module is checked. A RED
custody result requires successful original count/digest controls, not a failed
harness, unavailable capacity, or an inferred provider behavior.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import gc
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import unquote


def raw(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME)
    try:
        chunks = []
        while block := os.read(descriptor, 1024*1024):
            chunks.append(block)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def git(repo, *args):
    env = dict(os.environ, GIT_NO_LAZY_FETCH="1")
    return subprocess.check_output(["git", "--no-replace-objects", "-C", str(repo), *args], env=env).decode().strip()


def verify_archive(source, repo, revision):
    if len(revision) != 40 or git(repo, "rev-parse", "--verify", revision+"^{commit}") != revision:
        raise ValueError("EXACT_GIT_COMMIT_REQUIRED")
    rows = git(repo, "ls-tree", "-r", "--full-tree", revision).splitlines()
    inventory = {}
    for row in rows:
        metadata, name = row.split("\t", 1)
        mode, kind, blob = metadata.split()
        path = source / name
        info = path.lstat()
        if (kind != "blob" or mode not in {"100644", "100755"} or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != int(mode[-3:], 8)
                or path.resolve() != path):
            raise ValueError("ARCHIVE_BLOB_MODE_ALIAS_MISMATCH:"+name)
        data = raw(path)
        observed_blob = hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()
        if observed_blob != blob:
            raise ValueError("ARCHIVE_BLOB_BYTES_MISMATCH:"+name)
        inventory[name] = {"mode": mode, "blob": blob, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    actual = {str(path.relative_to(source)) for path in source.rglob("*") if path.is_file() or path.is_symlink()}
    if actual != set(inventory):
        raise ValueError("ARCHIVE_FILE_CLOSURE_MISMATCH")
    return inventory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--source-repo", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--phase", required=True, choices=("PRIOR", "CURRENT"))
    parser.add_argument("--fixture-root", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    source, fixture = args.source_root.absolute(), args.fixture_root.absolute()
    if source.resolve() != source or fixture.exists() or fixture.is_relative_to(source):
        raise ValueError("NEW_PRIVATE_FIXTURE_OUTSIDE_SOURCE_REQUIRED")
    if args.receipt.absolute().is_relative_to(source):
        raise ValueError("RECEIPT_OUTSIDE_SOURCE_REQUIRED")
    before = verify_archive(source, args.source_repo, args.source_sha)
    fixture.mkdir(mode=0o700, parents=True)
    scratch = fixture / "scratch"
    scratch.mkdir(mode=0o700)
    os.environ["TMPDIR"] = str(scratch)
    tempfile.tempdir = str(scratch)
    for key in list(os.environ):
        if key.startswith("POROTA_SQLITE_SCRATCH_"):
            del os.environ[key]
    os.environ["POROTA_DYNAMIC_CAPACITY_MODE"] = "OFF"
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source))
    sqlite_opens, network, results = [], [], []
    stage = ["NATIVE_FIXTURE_SETUP"]
    def audit(event, values):
        if event != "sqlite3.connect":
            return
        value = str(values[0])
        if value == ":memory:":
            sqlite_opens.append({"stage": stage[0], "scope": "IN_MEMORY"})
            return
        path = Path(unquote(value.split("?", 1)[0].removeprefix("file:"))).absolute()
        if not path.is_relative_to(fixture):
            raise AssertionError("SQLITE_OUTSIDE_PRIVATE_FIXTURE_FORBIDDEN")
        sqlite_opens.append({"stage": stage[0], "path": str(path.relative_to(fixture))})
    sys.addaudithook(audit)
    def forbidden(*args, **kwargs):
        network.append(stage[0])
        raise AssertionError("NETWORK_FORBIDDEN")
    socket.socket.connect = forbidden
    socket.socket.connect_ex = forbidden
    socket.create_connection = forbidden
    socket.getaddrinfo = forbidden
    error = None
    environment = None
    try:
        import pytest
        from scripts.porota_dependency_repro_audit import installed_distribution_audit
        policy = json.loads(raw(source/"ops/policy/rc6-supply-chain-v1.json"))
        environment = installed_distribution_audit(policy)
        if environment["status"] != "GREEN" or environment["installed_total"] != 157:
            raise AssertionError("FROZEN_157_EXACT_VERSION_METADATA_REQUIRED")
        from rc6_dynamic_universe.common import digest
        from rc6_ppi_global_budget import supervisable_position_count, _supervisable_identity_digests
        from scripts.rc6_issue465_stress import source_custody_snapshot
        from tests.test_issue465_budget_adversarial import native_opened_store
        from tests.test_rc6_convergence_budget_liveness import seed_offline_pending_futures
        from tests.test_rc6_ppi_capacity_benchmark import Clock
        data_root = fixture / "ledger"
        data_root.mkdir(mode=0o700)
        clock = Clock("2026-10-05T14:00:00+00:00")
        with pytest.MonkeyPatch.context() as patch:
            store, broker = native_opened_store(data_root, clock, patch, count=5)
            seed_offline_pending_futures(store, patch)
            gc.collect()
            with closing(store.connect()) as anchor:
                expected_rows = list(anchor.execute("SELECT symbol,asset_class,market,currency,settlement FROM paper_positions WHERE status='OPEN'"))
                expected_rows.extend(anchor.execute("SELECT symbol,'FUTUROS',market,currency,settlement FROM paper_future_positions WHERE status='ACTIVE'"))
                expected = {digest(list(row)) for row in expected_rows}
                if len(expected_rows) != 10 or len(expected) != 10:
                    raise AssertionError("NATIVE_MIXED_LEDGER_POSITIVE_CONTROL_FAILED")
                for name, call in (("count", supervisable_position_count), ("digests", _supervisable_identity_digests)):
                    stage[0] = "PREPARE_PRIVATE_FIXTURE_ATIME"
                    # This explicit fixture action precedes the custody boundary.
                    # The original/archive code and unrelated files are untouched.
                    for suffix in ("", "-wal", "-shm"):
                        path = Path(str(store.path)+suffix)
                        info = path.lstat()
                        os.utime(path, ns=(0, info.st_mtime_ns))
                    original = source_custody_snapshot(store.path)
                    if original.get("-shm", {}).get("st_size", 0) <= 0:
                        raise AssertionError("ACTUAL_WAL_SHM_REQUIRED")
                    stage[0] = "NATIVE_BUDGET_"+name.upper()
                    entered = time.monotonic()
                    value = call(store.path)
                    elapsed = time.monotonic()-entered
                    observed = source_custody_snapshot(store.path)
                    valid = value == 10 if name == "count" else value == expected
                    changes = {suffix: {field: {"before": original.get(suffix, {}).get(field), "after": after_value}
                        for field, after_value in member.items() if original.get(suffix, {}).get(field) != after_value}
                        for suffix, member in observed.items()}
                    changes = {suffix: values for suffix, values in changes.items() if values}
                    results.append({"caller": name, "positive_math_control": valid,
                        "observed_count": value if name == "count" else len(value) if value is not None else None,
                        "elapsed_seconds": elapsed, "source_custody_preserved": original == observed,
                        "before": original, "after": observed, "changes": changes})
                del broker
        after = verify_archive(source, args.source_repo, args.source_sha)
        if before != after:
            raise AssertionError("ARCHIVED_SOURCE_CHANGED")
        project_roots = {name.split("/", 1)[0].removesuffix(".py") for name in before}
        imports, alien = [], []
        for name, module in sorted(sys.modules.items()):
            filename = getattr(module, "__file__", None)
            if filename is None or name.split(".", 1)[0] not in project_roots:
                continue
            path = Path(filename).absolute()
            if not path.is_relative_to(source):
                alien.append({"module": name, "path": str(path)})
            else:
                relative = str(path.relative_to(source))
                imports.append({"module": name, "path": relative, "sha256": before[relative]["sha256"]})
        if alien:
            raise AssertionError("ALIEN_PROJECT_IMPORTS:"+json.dumps(alien, sort_keys=True))
        if not all(result["positive_math_control"] for result in results) or len(results) != 2:
            raise AssertionError("NATIVE_MATH_CONTROL_FAILED_NOT_A_CUSTODY_RED")
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
        imports = []
        after = verify_archive(source, args.source_repo, args.source_sha)
    classification = ("INVALIDATED_HARNESS" if error or network else
        ("OLD" if args.phase == "PRIOR" else "CURRENT")+"_NATIVE_PRIMARY_CUSTODY_RED_WITH_PRESERVED_MATH_CONTROLS" if any(not r["source_custody_preserved"] for r in results)
        else "NATIVE_PRIMARY_CUSTODY_CONTROL_GREEN")
    receipt = {"schema": "rc6.budget-primary-custody-native-replay.v1", "classification": classification,
        "scope": "OFFLINE_SYNTHETIC_NATIVE_PAPER_LEDGER_PRIVATE_FIXTURES_NO_PROVIDER_NO_RUNTIME",
        "phase": args.phase, "environment_metadata_closure": environment, "python_version": sys.version,
        "source_sha": args.source_sha, "source_tree": git(args.source_repo, "rev-parse", args.source_sha+"^{tree}"),
        "source_root": str(source), "source_files": len(before), "source_bytes": sum(row["bytes"] for row in before.values()),
        "source_inventory_sha256": canonical_digest(before), "all_git_blob_bytes_modes_verified": True,
        "source_unchanged": before == after, "imports": imports, "alien_imports": [],
        "provider_attempts": network, "sqlite_opens": sqlite_opens, "harness_error": error,
        "results": results, "real_orders_sent": 0, "real_routes": "NOT_CALLED",
        "limits": ["No PPI Watch assets inspected or modified.", "No provider capacity, commercial terms or external latency demonstrated.",
            "Two reader invocations and their math controls are not new requirements or pytest totals.",
            "SQL/NoAtime custody is tested on a synthetic native PAPER ledger; deployment/final artifact/FIP remain separate."]}
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, sort_keys=True, indent=2)+"\n")
    print(json.dumps({"classification": classification, "receipt": str(args.receipt),
        "sha256": hashlib.sha256(args.receipt.read_bytes()).hexdigest(), "source_files": len(before),
        "source_unchanged": before == after, "provider_attempts": len(network), "harness_error": error}, sort_keys=True))
    return 1 if error or network else 0


if __name__ == "__main__":
    raise SystemExit(main())
