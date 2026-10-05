"""Stdlib-only preparation transport; the driver never executes native writers.

The fixed preparation bound is infrastructure scope, separate from RPC20,
render1 and health2. A completed small fixture never satisfies the BIG gate.
"""
from datetime import datetime, timedelta
import ast
import hashlib
import json
from math import isfinite
import os
from pathlib import Path
import re
import selectors
import subprocess
from time import monotonic, perf_counter

from tests.rc6_browser_ipc import (
    GateFailure, MAX_FRAME, PROTOCOL, output_guard, parse_frame, protected_bytes,
    require, source_inventory, source_start,
)

PREPARATION_SECONDS = 90.0
PREPARED_SCHEMA = "rc6.browser-prepared-small-native-fixture.v1"
PREPARED_VARIANTS = {"NORMAL": 25, "MULTIFAMILY": 318}


def inventory_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def prepared_receipt(path, expected_sha256, *, source_root, source_index, database=None, root=None):
    """Bind the producer's pinned frame to the same source and private dataset."""
    require(type(expected_sha256) is str and re.fullmatch(r"[0-9a-f]{64}", expected_sha256),
            "PREPARED_RECEIPT_DIGEST_REQUIRED")
    require(not any(member.is_symlink() for member in (path, *path.parents))
            and not path.resolve().is_relative_to(source_root)
            and path.is_file() and path.stat().st_size < MAX_FRAME,
            "PREPARED_RECEIPT_ALIAS_OR_LOCATION_REJECTED")
    raw = protected_bytes(path)
    require(hashlib.sha256(raw).hexdigest() == expected_sha256, "PREPARED_RECEIPT_PIN_CHANGED")
    envelope = parse_frame(raw)
    require(envelope["id"] == 0 and envelope.get("ok") is True,
            "PREPARED_RECEIPT_ENVELOPE_REJECTED")
    value = envelope.get("result")
    require(type(value) is dict and value.get("schema") == PREPARED_SCHEMA
            and value.get("variant") in PREPARED_VARIANTS
            and value.get("scope") == "COMPLETED_SMALL_NATIVE_PREPARATION_NOT_BIG_ACCEPTANCE"
            and value.get("status") == "DATA_READY" and value.get("big_acceptance_claim") is False
            and value.get("all_sqlite_writers_closed_before_custody") is True,
            "PREPARED_RECEIPT_SCOPE_REJECTED")
    require(source_index and source_index["pin_complete"] is True
            and value.get("source_sha") == source_index["source_sha"]
            and value.get("candidate_tree_sha") == source_index["candidate_tree_sha"]
            and value.get("source_pin_complete") is True
            and value.get("tracked_source_files") == len(source_index["source_file_hashes"]),
            "PREPARED_RECEIPT_FOREIGN_SOURCE")
    require(value.get("source_before_digest") == value.get("source_after_digest")
            == inventory_digest(source_inventory(source_root))
            and value.get("source_proof_pass") is True
            and value.get("unexpected_product_imports") == []
            and type(value.get("imported_product_modules")) is list
            and value["imported_product_modules"]
            and all(type(row) is dict and row.get("matches_archived_blob") is True
                    and row.get("sha256") == source_index["source_file_hashes"].get(row.get("path"))
                    for row in value["imported_product_modules"]),
            "PREPARED_RECEIPT_SOURCE_PROOF_REJECTED")
    environment = value.get("product_environment", {})
    require(environment.get("role") == "PRODUCT" and environment.get("installed_count") == 157
            and environment.get("locked_count") == 157
            and not environment.get("missing") and not environment.get("extra")
            and not environment.get("wrong_versions"), "PREPARED_RECEIPT_ENVIRONMENT_REJECTED")
    require(type(value.get("provider_attempts")) is int and value["provider_attempts"] == 0
            and type(value.get("real_orders_sent")) is int and value["real_orders_sent"] == 0
            and value.get("real_routes") == "NOT_CALLED" and value.get("ppi_watch") == "UNTOUCHED",
            "PREPARED_RECEIPT_SAFETY_REJECTED")
    base = path.parent.resolve()
    db, evidence = Path(value.get("database", "")), Path(value.get("root", ""))
    require(db.is_absolute() and evidence.is_absolute()
            and db.resolve().is_relative_to(base / "data") and evidence.resolve().is_relative_to(base / "data")
            and not any(member.is_symlink() for item in (db, evidence) for member in (item, *item.parents))
            and db.is_file() and evidence.is_dir()
            and (database is None or db == database) and (root is None or evidence == root),
            "PREPARED_RECEIPT_DATASET_REJECTED")
    ticks = value.get("native_ticks")
    require(type(ticks) is list and len(ticks) == 10
            and all(type(tick) is dict and type(tick.get("sequence")) is int for tick in ticks)
            and [tick.get("sequence") for tick in ticks] == list(range(1, 11))
            and ticks[0].get("phase") == "PREOPEN" and ticks[-1].get("phase") == "OPEN"
            and all(tick.get("mode") == "SHADOW" for tick in ticks),
            "PREPARED_RECEIPT_NATIVE_TICKS_REJECTED")
    progress_path = base / "progress.jsonl"
    require(progress_path.is_file() and progress_path.stat().st_size < 256 * 1024,
            "PREPARED_RECEIPT_PROGRESS_REJECTED")
    progress_raw = protected_bytes(progress_path)
    require(hashlib.sha256(progress_raw).hexdigest() == value.get("progress_sha256"),
            "PREPARED_RECEIPT_PROGRESS_REJECTED")
    progress = [json.loads(line) for line in progress_raw.splitlines()]
    require(type(value.get("progress_records")) is int and 0 < len(progress) == value["progress_records"] <= 64
            and all(type(event) is dict for event in progress)
            and [{key: event.get(key) for key in ("as_of", "generation_id", "sequence", "phase", "mode")}
                 for event in progress if event.get("stage") == "TICK_END"] == ticks,
            "PREPARED_RECEIPT_NATIVE_TICKS_REJECTED")
    pointer, manifest = value.get("pointer", {}), value.get("manifest", {})
    cut_time, manifest_time = datetime.fromisoformat(value["source_cut"]), datetime.fromisoformat(manifest["as_of"])
    require(cut_time.utcoffset() == manifest_time.utcoffset() == timedelta(0)
            and pointer.get("sequence") == 10 and manifest.get("sequence") == 10
            and pointer.get("generation_id") == manifest.get("generation_id") == ticks[-1].get("generation_id")
            and manifest.get("as_of") == ticks[-1].get("as_of")
            and cut_time == manifest_time
            and value.get("native_phase") == "OPEN"
            and set(manifest.get("files", {})) == {"report", "checkpoint", "status", "projection"}
            and value.get("verification_level") == "FULL_LOGICAL_SEMANTICS",
            "PREPARED_RECEIPT_INCOMPLETE_OPEN_CUT")
    rows, counts = value.get("catalog_full_identities"), value.get("catalog_families")
    require(type(rows) is list and len(rows) == PREPARED_VARIANTS[value["variant"]]
            and all(type(row) is list and len(row) == 5 and all(type(part) is str and part for part in row) for row in rows)
            and len({tuple(row) for row in rows}) == len(rows) and type(counts) is dict,
            "PREPARED_RECEIPT_FULL_IDENTITY_REJECTED")
    factual = {}
    for row in rows:
        factual[row[1]] = factual.get(row[1], 0) + 1
    require(factual == counts and type(value.get("custody_inventory")) is dict
            and value["custody_inventory"], "PREPARED_RECEIPT_POPULATION_OR_CUSTODY_REJECTED")
    if value["variant"] == "NORMAL":
        require(counts == {"ACCIONES": 25}, "PREPARED_RECEIPT_VARIANT_POPULATION_REJECTED")
    else:
        # Read the pinned native enum as data. No product module executes in
        # the158 driver, and no duplicated family authority is introduced.
        native_enum = ast.parse(protected_bytes(source_root / "bs_instrument_contracts.py"))
        definitions = [node for node in native_enum.body if isinstance(node, ast.Assign)
                       and any(isinstance(name, ast.Name) and name.id == "FAMILIES" for name in node.targets)]
        require(len(definitions) == 1 and isinstance(definitions[0].value, ast.Call)
                and isinstance(definitions[0].value.func, ast.Name) and definitions[0].value.func.id == "frozenset",
                "PREPARED_NATIVE_FAMILY_ENUM_REJECTED")
        families = ast.literal_eval(definitions[0].value.args[0])
        require(counts == {family: 3 if family == "FUTUROS" else 35 for family in families}
                and {row[0] for row in rows if row[1] == "FUTUROS"} == {"DLR/OCT26", "DLR/NOV26", "DLR/DIC26"},
                "PREPARED_RECEIPT_VARIANT_POPULATION_REJECTED")
    return value


def prepared_launcher(path, expected_sha256, *, source_root, source_index, producer, receipt_sha256):
    """Only the observing parent can certify the producer's successful exit."""
    require(type(expected_sha256) is str and re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
            and not any(member.is_symlink() for member in (path, *path.parents))
            and not path.resolve().is_relative_to(source_root)
            and path.is_file() and path.stat().st_size < MAX_FRAME, "PREPARED_LAUNCHER_PIN_REQUIRED")
    raw = protected_bytes(path)
    require(hashlib.sha256(raw).hexdigest() == expected_sha256, "PREPARED_LAUNCHER_PIN_CHANGED")
    value = json.loads(raw)
    elapsed = value.get("elapsed_seconds_including_startup_emission_and_reap")
    require(value.get("schema") == "rc6.browser-native-preparation-transport.v1"
            and value.get("status") == "COMPLETE" and type(value.get("returncode")) is int
            and value["returncode"] == 0 and value.get("reaped") is True
            and value.get("cleanup_forced") is False and value.get("source_hashes_modes_blobs_unchanged") is True
            and value.get("source_sha") == source_index["source_sha"]
            and value.get("candidate_tree_sha") == source_index["candidate_tree_sha"]
            and type(value.get("pid")) is int and value["pid"] == producer["producer_pid"]
            and value.get("stdout_sha256") == receipt_sha256
            and value.get("variant") == producer["variant"]
            and value.get("preparation_cap_seconds") == PREPARATION_SECONDS
            and type(elapsed) in {int, float} and isfinite(elapsed) and 0 <= elapsed <= PREPARATION_SECONDS,
            "PREPARED_LAUNCHER_COMPLETION_REJECTED")
    return value


def prepare_native_fixture(executable, output, *, source_root, index, variant, python_version=None):
    """Run the exact157 producer and reap it before exposing its completed data."""
    require(variant in PREPARED_VARIANTS, "PREPARED_VARIANT_REJECTED")
    begin, deadline = perf_counter(), monotonic() + PREPARATION_SECONDS
    output_guard(output, source_root=source_root)
    transport = output.with_name(output.name + "-transport")
    output_guard(transport, source_root=source_root)
    before, source_index = source_start(source_root, index)
    require(source_index and source_index["pin_complete"], "WHOLE_COMPLETE_RAW_SOURCE_INDEX_REQUIRED")
    command = [str(executable), "-I", "-B", str(source_root / "tests/ci_rc6_browser_prepare.py"),
        "--expected-python", str(executable), "--index", str(index), "--output", str(output),
        "--variant", variant, "--absolute-deadline", str(deadline)]
    if python_version:
        command.extend(("--expected-python-version", python_version))
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    environment.pop("PYTHONPATH", None)
    transport.mkdir(parents=True, exist_ok=False)
    process, forced, failure, result = None, False, None, None
    captures = {"stdout": bytearray(), "stderr": bytearray()}
    try:
        process = subprocess.Popen(command, cwd=source_root, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        with (transport / "stdout.raw").open("wb") as stdout, (transport / "stderr.raw").open("wb") as stderr:
            with selectors.DefaultSelector() as selector:
                for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
                    selector.register(stream, selectors.EVENT_READ, name)
                while selector.get_map():
                    remaining = deadline - monotonic()
                    require(remaining > 0, "NATIVE_PREPARATION_DEADLINE")
                    events = selector.select(remaining)
                    require(events, "NATIVE_PREPARATION_DEADLINE")
                    for key, _ in events:
                        raw = os.read(key.fileobj.fileno(), 65536)
                        if not raw:
                            selector.unregister(key.fileobj)
                            continue
                        (stdout if key.data == "stdout" else stderr).write(raw)
                        captures[key.data].extend(raw)
                        require(len(captures[key.data]) < MAX_FRAME, "NATIVE_PREPARATION_FRAME_BUDGET")
        remaining = deadline - monotonic()
        require(remaining > 0, "NATIVE_PREPARATION_DEADLINE")
        process.wait(timeout=remaining)
        require(monotonic() <= deadline and process.returncode == 0,
                "NATIVE_PREPARATION_DID_NOT_EXIT_SUCCESSFULLY", {"returncode": process.returncode})
        envelope = parse_frame(bytes(captures["stdout"]))
        require(envelope["id"] == 0 and envelope.get("ok") is True,
                "NATIVE_PREPARATION_REJECTED", envelope.get("error", {}))
        path = output / "native-fixture.json"
        digest = hashlib.sha256(bytes(captures["stdout"])).hexdigest()
        result = prepared_receipt(path, digest, source_root=source_root, source_index=source_index)
        require(result == envelope["result"] and result.get("producer_pid") == process.pid,
                "NATIVE_PREPARATION_STDIO_RECEIPT_CHANGED")
    except subprocess.TimeoutExpired as error:
        failure = GateFailure("NATIVE_PREPARATION_DEADLINE", {"error_class": type(error).__name__})
    except BaseException as error:
        failure = error
    finally:
        if process is not None:
            if process.poll() is None:
                forced = True
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            process.stdout.close()
            process.stderr.close()
        after = source_inventory(source_root)
        elapsed = perf_counter() - begin
        launcher = {"schema": "rc6.browser-native-preparation-transport.v1", "variant": variant,
            "command": command, "pid": process.pid if process is not None else None,
            "returncode": process.returncode if process is not None else None,
            "reaped": process is not None and process.poll() is not None, "cleanup_forced": forced,
            "elapsed_seconds_including_startup_emission_and_reap": elapsed,
            "preparation_cap_seconds": PREPARATION_SECONDS, "not_a_consumer_or_big_sla_claim": True,
            "source_sha": source_index["source_sha"], "candidate_tree_sha": source_index["candidate_tree_sha"],
            "source_hashes_modes_blobs_unchanged": before == after,
            "stdout_bytes": len(captures["stdout"]), "stderr_bytes": len(captures["stderr"]),
            "stdout_sha256": hashlib.sha256(captures["stdout"]).hexdigest(),
            "stderr_sha256": hashlib.sha256(captures["stderr"]).hexdigest(),
            "status": "COMPLETE" if failure is None and not forced and before == after
                and elapsed <= PREPARATION_SECONDS else "REJECTED",
            "failure_gate": str(failure) if isinstance(failure, GateFailure) else None,
            "failure_class": type(failure).__name__ if failure is not None else None}
        (transport / "launcher.json").write_text(json.dumps(launcher, sort_keys=True, indent=2) + "\n")
        elapsed = perf_counter() - begin
        if monotonic() > deadline:
            if failure is None:
                failure = GateFailure("NATIVE_PREPARATION_DEADLINE")
            launcher.update(status="REJECTED", failure_gate="NATIVE_PREPARATION_DEADLINE",
                failure_class=type(failure).__name__, elapsed_seconds_including_startup_emission_and_reap=elapsed)
            (transport / "launcher.json").write_text(json.dumps(launcher, sort_keys=True, indent=2) + "\n")
    require(before == after, "WHOLE_SOURCE_HASHES_OR_MODES_CHANGED")
    if failure is not None:
        raise failure
    require(not forced and elapsed <= PREPARATION_SECONDS, "NATIVE_PREPARATION_DEADLINE_OR_CLEANUP_FAILED")
    launcher_path = transport / "launcher.json"
    launcher_sha = hashlib.sha256(protected_bytes(launcher_path)).hexdigest()
    require(monotonic() <= deadline, "NATIVE_PREPARATION_DEADLINE")
    return {"receipt_path": path, "receipt_sha256": digest, "receipt": result,
            "launcher_receipt": launcher, "transport_root": transport,
            "launcher_path": launcher_path, "launcher_sha256": launcher_sha,
            "database": Path(result["database"]), "root": Path(result["root"])}


def initialize_prepared_product(product, prepared):
    return product.request("initialize", mode="PREPARED_SMALL", database=str(prepared["database"]),
        root=str(prepared["root"]), prepared_receipt=str(prepared["receipt_path"]),
        prepared_receipt_sha256=prepared["receipt_sha256"], prepared_launcher=str(prepared["launcher_path"]),
        prepared_launcher_sha256=prepared["launcher_sha256"])
