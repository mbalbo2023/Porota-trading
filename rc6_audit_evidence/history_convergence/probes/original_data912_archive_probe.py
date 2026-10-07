"""Replay Data912 on one complete original source archive, entirely offline.

The original H_DATA912_VALIDATION function is selected unchanged from the
supplied #468 driver. Additional sink/provider cases are explicitly native
reconstructions using that archive's own APIs and schema initializers.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import socket
import sqlite3
import stat
import sys
import tarfile
import tempfile
import traceback
import zipfile


ORIGINAL_DRIVER_SHA256 = "a9d2ea84ae48bbff0ccf07bcee85a346e5fe874446edfdb4cf3ad95225845595"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def members(root):
    inventory = {}
    for path in root.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            continue
        info = path.lstat()
        assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "ARCHIVE_MEMBER_ALIAS_OR_TYPE_INVALID"
        inventory[str(path.relative_to(root))] = {
            "bytes": info.st_size, "mode": stat.S_IMODE(info.st_mode), "nlink": info.st_nlink}
    return inventory


def safe(value):
    """Describe nonfinite observations without writing nonstandard JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        return {"observed_nonfinite_float": repr(value)}
    if isinstance(value, dict):
        return {str(key): safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(item) for item in value]
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-index", type=Path, required=True)
    parser.add_argument("--source-archive", type=Path, required=True)
    original = parser.add_mutually_exclusive_group(required=True)
    original.add_argument("--original-evidence-zip", type=Path)
    original.add_argument("--original-driver-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for name in ("source_index", "source_archive", "original_evidence_zip", "original_driver_file"):
        path = getattr(args, name)
        if path is not None:
            setattr(args, name, path.resolve(strict=True))
    index_raw = args.source_index.read_bytes()
    index = json.loads(index_raw)
    root = Path(index["extracted_root"]).resolve(strict=True)
    output = args.output.resolve()
    assert not output.is_relative_to(root), "OUTPUT_MUST_BE_OUTSIDE_SOURCE_ARCHIVE"
    assert sha(args.source_archive) == index["archive_sha256"], "ARCHIVE_BYTES_CHANGED"
    expected = index["source_file_hashes"]
    archive_files = {}
    with tarfile.open(args.source_archive) as archive:
        assert archive.pax_headers.get("comment") == index["source_sha"], "ARCHIVE_COMMIT_MARKER_MISMATCH"
        for member in archive.getmembers():
            if member.isdir():
                continue
            assert member.isfile() and not Path(member.name).is_absolute() and ".." not in Path(member.name).parts
            assert member.name not in archive_files, "ARCHIVE_DUPLICATE_MEMBER"
            with archive.extractfile(member) as stream:
                archive_files[member.name] = hashlib.sha256(stream.read()).hexdigest()
    assert archive_files == expected, "ARCHIVE_EXTRACTED_BYTES_INDEX_MISMATCH"
    before_members = members(root)
    assert set(before_members) == set(expected), "ARCHIVE_MEMBER_INVENTORY_MISMATCH"
    before = {name: sha(root / name) for name in expected}
    assert before == expected, "ARCHIVE_SOURCE_MISMATCH"
    if args.original_evidence_zip is not None:
        with zipfile.ZipFile(args.original_evidence_zip) as bundle:
            driver_raw = bundle.read("audit/adversarial_harness.py")
    else:
        driver_raw = args.original_driver_file.read_bytes()
    assert hashlib.sha256(driver_raw).hexdigest() == ORIGINAL_DRIVER_SHA256
    driver_text = driver_raw.decode()
    definitions = [node for node in ast.parse(driver_text).body
                   if isinstance(node, ast.FunctionDef) and node.name == "d912_bad"]
    assert len(definitions) == 1, "ORIGINAL_DEFINITION_UNAVAILABLE"
    fragment = ast.get_source_segment(driver_text, definitions[0])

    # Remove repository/worktree search routes before importing native code.
    sys.path[:] = [str(root)] + [path for path in sys.path
                                if not path.startswith("/workspace/porota_rc6_")]
    os.chdir(root)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ.pop("POROTA_RUNTIME_SCHEMA_READY", None)
    sys.dont_write_bytecode = True
    network_attempts = []

    def deny(*_args, **_kwargs):
        network_attempts.append("NETWORK_ATTEMPT_BLOCKED")
        raise RuntimeError("OFFLINE_ORIGINAL_HISTORY_PROBE_NETWORK_FORBIDDEN")

    socket.create_connection = socket.socket.connect = socket.socket.connect_ex = deny
    socket.getaddrinfo = deny
    native_sqlite_connect = sqlite3.connect
    sqlite_fixture_root = None
    sqlite_attempts = []

    def guarded_connect(database, *arguments, **options):
        assert isinstance(database, (str, bytes, os.PathLike)), "OFFLINE_SQLITE_PATH_TYPE_FORBIDDEN"
        text = os.fsdecode(database)
        if text == ":memory:":
            sqlite_attempts.append({"scope": "IN_MEMORY", "authorized": True})
        else:
            path = Path(text).resolve()
            allowed = (bool(text) and not text.startswith("file:") and
                       sqlite_fixture_root is not None and path.is_relative_to(sqlite_fixture_root))
            sqlite_attempts.append({"scope": "EPHEMERAL_NATIVE_FIXTURE" if allowed else "FORBIDDEN_NONFIXTURE_PATH",
                "fixture_file": str(path.relative_to(sqlite_fixture_root)) if allowed else None,
                "authorized": bool(allowed)})
            assert allowed, "OFFLINE_SQLITE_OUTSIDE_AUTHORIZED_FIXTURE_FORBIDDEN"
        return native_sqlite_connect(database, *arguments, **options)

    sqlite3.connect = guarded_connect
    started = datetime.now(timezone.utc).isoformat()
    import requests
    import ba_data912_history as data912
    import cw_data912_history_v2_sink as sink
    from cv_history_store_adapter_hf6 import HistoricalStore

    results = {}

    def record(name, call):
        try:
            results[name] = {"execution": "COMPLETED", "result": safe(call())}
        except Exception as error:
            results[name] = {"execution": "HARNESS_ERROR_NOT_PRODUCT_RED",
                "error": type(error).__name__ + ":" + str(error),
                "traceback": traceback.format_exc()}

    namespace = {"d912": data912}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), "original:#468:d912_bad", "exec"), namespace)

    def original_normalizer():
        observed = namespace["d912_bad"]()
        return {"original_function_output": observed, "red_contracts": {
            "future_date_accepted": bool(observed["future"]),
            "nonfinite_close_accepted": any(not math.isfinite(row[4]) for row in observed["nan"]),
            "inconsistent_ohlc_accepted": any(row[2] < row[3] for row in observed["invalid_ohlc"]),
        }, "scope": "UNCHANGED_ORIGINAL_FUNCTION_WITH_ACTUAL_NATIVE_ADAPTER_AND_REQUESTS_DEPENDENCY"}

    record("H_DATA912_VALIDATION", original_normalizer)
    valid = dict(date="2026-09-30", o=100, h=102, l=98, c=100, v=1)
    normalizer_cases = {"valid_control": valid,
        "infinite_close_only": dict(date="2026-09-30", c=float("inf"), v=1),
        "invalid_date": {**valid, "date": "not-a-date"},
        "partial_ohlc": {**valid, "o": None},
        "infinite_volume": {**valid, "v": float("inf")}}
    for name, row in normalizer_cases.items():
        record("NATIVE_NORMALIZER:" + name,
               lambda row=row: {"input": row, "normalized": data912._normalize([row], "2026-01-01")})

    class FakeClient:
        """Provider seam only; the native transport/normalizer/sink stay real."""
        def __init__(self, kind, rows=None):
            self.kind, self.rows, self.calls = kind, rows, 0

        def get(self, *_args, **_kwargs):
            self.calls += 1
            if self.kind == "timeout":
                raise requests.Timeout("synthetic-provider-timeout")
            client = self
            class Response:
                status_code = 429 if client.kind == "429" else 200
                @staticmethod
                def json():
                    return client.rows
            return Response()

    original_sleep = data912.time.sleep
    delays = []
    data912.time.sleep = delays.append
    try:
        for kind in ("timeout", "429"):
            def provider_case(kind=kind):
                client = FakeClient(kind)
                delays.clear()
                try:
                    observed = {"accepted": True, "value": data912._get_json(client, "/synthetic")}
                except Exception as error:
                    observed = {"accepted": False, "exception": type(error).__name__, "reason": str(error)}
                return {"calls": client.calls, "configured_retries": data912.DATA912_RETRIES,
                    "simulated_sleep_seconds": list(delays), "result": observed,
                    "scope": "NATIVE_API_RECONSTRUCTION_WITH_REAL_REQUESTS_TIMEOUT_CLASS; NOT_ORIGINAL_FAKE_REQUESTS_DRIVER"}
            record("NATIVE_PROVIDER:" + kind, provider_case)

        with tempfile.TemporaryDirectory(prefix="rc6-original-history-fixtures-") as directory:
            sqlite_fixture_root = Path(directory).resolve(strict=True)
            original_session = data912._session
            try:
                payloads = {"valid_control": [valid],
                    "future": [{**valid, "date": "2099-01-01"}],
                    "infinite_close_only": [normalizer_cases["infinite_close_only"]],
                    "nan_close": [{**valid, "c": "NaN"}],
                    "inconsistent_ohlc": [{**valid, "h": 90}],
                    "partial_ohlc": [normalizer_cases["partial_ohlc"]],
                    "valid_plus_future": [valid, {**valid, "date": "2099-01-01"}],
                    "empty_control": []}
                for name, rows in payloads.items():
                    def sink_case(name=name, rows=rows):
                        client = FakeClient("rows", rows)
                        data912._session = lambda: client
                        store = HistoricalStore(str(Path(directory) / (name + ".sqlite")))
                        response = sink.refresh_identities([("AUDIT", "ACCIONES", "BYMA", "A-24HS")],
                                                           history_store=store)
                        connection = store.connect()
                        try:
                            canonical = [dict(row) for row in connection.execute("SELECT * FROM history_canonical_v2")]
                            versions = connection.execute("SELECT COUNT(*) FROM history_versions_v2").fetchone()[0]
                        finally:
                            connection.close()
                        return {"provider_payload": rows, "provider_calls": client.calls,
                            "native_result": response, "versions": versions, "canonical": canonical,
                            "schema_initializer": "NATIVE_CU_INIT_SCHEMA_CALLED_BY_NATIVE_CW_REFRESH; NO_PROBE_DDL",
                            "scope": "NATIVE_ORIGINAL_FOUR_FIELD_TARGET_API_ON_EPHEMERAL_STORE"}
                    record("NATIVE_SINK:" + name, sink_case)
            finally:
                data912._session = original_session
                sqlite_fixture_root = None
    finally:
        data912.time.sleep = original_sleep

    imported, alien, origins = {}, {}, {}
    repo_modules = {name[:-3].replace("/", ".").removesuffix(".__init__")
                    for name in expected if name.endswith(".py")}
    for name, module in tuple(sys.modules.items()):
        origin = getattr(module, "__file__", None)
        if not origin:
            continue
        path = Path(origin).resolve()
        origins[name] = str(path)
        if path.is_relative_to(root):
            relative = str(path.relative_to(root))
            imported[name] = {"path": relative, "sha256": sha(path)}
            if relative not in expected or imported[name]["sha256"] != expected[relative]:
                alien[name] = str(path)
        elif name in repo_modules:
            alien[name] = str(path)
    after = {name: sha(root / name) for name in expected}
    after_members = members(root)
    assert (not network_attempts and not alien and before == after and before_members == after_members
            and all(item["authorized"] for item in sqlite_attempts))
    receipt = {"schema": "rc6.original-history-data912-archive-replay.v1",
        "scope": "INDEPENDENT_OFFLINE_ORIGINAL_SOURCE_NATIVE_REPLAY; NOT_CURRENT_PROVIDER_OR_RUNTIME_AUTHORITY",
        "source_sha": index["source_sha"], "source_tree": index["source_tree"],
        "source_index_sha256": hashlib.sha256(index_raw).hexdigest(),
        "archive_sha256": index["archive_sha256"], "runner_sha256": sha(Path(__file__)),
        "complete_archive_member_bytes_verified": archive_files == expected,
        "original_evidence_zip_sha256": sha(args.original_evidence_zip) if args.original_evidence_zip else None,
        "original_driver_sha256": ORIGINAL_DRIVER_SHA256,
        "unchanged_original_d912_bad_definition_sha256": hashlib.sha256(fragment.encode()).hexdigest(),
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
        "requests_version": requests.__version__, "network_attempts": network_attempts,
        "sqlite_connection_attempts": sqlite_attempts,
        "sqlite_connection_scope": "ONLY_IN_MEMORY_OR_EXPLICIT_EPHEMERAL_FIXTURE_ROOT; SOURCE_AND_RUNTIME_PATHS_FORBIDDEN",
        "source_files_before": before, "source_files_after": after,
        "source_member_inventory_before": before_members, "source_member_inventory_after": after_members,
        "source_unchanged": before == after and before_members == after_members,
        "source_invariance_scope": "ALL_ARCHIVED_FILE_BYTES_NAMES_SIZES_MODES_NLINK; NOT_A_BLANKET_ATIME_OR_HOST_SQLITE_CLAIM",
        "imported_repo_modules": imported, "alien_repo_modules": alien,
        "all_imported_file_origins": origins, "results": results,
        "harness_errors": [name for name, item in results.items() if item["execution"] != "COMPLETED"],
        "runtime_verified": False, "real_orders_sent": 0, "provider_requests": 0,
        "current_full_history_coverage": "NO_VERIFICADO",
        "original_R_ids_added": 0, "original_clause_ids_added": 0}
    output.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"output": str(output), "sha256": sha(output),
        "completed": len(results) - len(receipt["harness_errors"]), "harness_errors": receipt["harness_errors"],
        "source_unchanged": receipt["source_unchanged"], "alien_repo_modules": alien}))


if __name__ == "__main__":
    main()
