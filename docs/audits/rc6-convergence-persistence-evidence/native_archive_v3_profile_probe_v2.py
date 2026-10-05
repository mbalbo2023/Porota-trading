"""Native, private archive capacity measurement; source pin is mandatory.

No provider/live execution. Normal four-cut measurements are descriptive. A
ten-hour result requires all 1201 full native ticks, original-byte recovery,
restarts and both fixed byte/file policies; it is never inferred from a pair.
V2 requires the complete source index, canonical disk scratch and unchanged
source bytes AND metadata. The original V1 runner/107a RED receipt remain intact.
Run against a complete immutable git archive, not an overlay or a PYTHONPATH mix.
"""
import argparse
import gc
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import resource
import socket
import sqlite3
import stat
import sys
import time
import traceback


def raw(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME)
    try:
        chunks = []
        while data := os.read(descriptor, 65536):
            chunks.append(data)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


FIELDS = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size",
          "st_blocks", "st_atime_ns", "st_mtime_ns", "st_ctime_ns")


def signature(path):
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise ValueError("NATIVE_PROFILE_SOURCE_ALIAS_INVALID")
    hashed = hashlib.sha256(raw(path)).hexdigest()
    after = path.lstat()
    if tuple(getattr(value, key) for key in FIELDS) != tuple(getattr(after, key) for key in FIELDS):
        raise ValueError("NATIVE_PROFILE_SOURCE_CHANGED_DURING_INVENTORY")
    return {**{key: getattr(value, key) for key in FIELDS}, "sha256": hashed}


def database_inventory(database):
    # This public helper is ROOT-owned and closes the same source-custody
    # contract used by the large concurrency runner. Its revision must be in
    # the complete source pin; no fallback to the old main-file-only hash.
    from scripts.rc6_issue465_stress import source_custody_snapshot
    snapshot = source_custody_snapshot(database)
    return {suffix: snapshot.get(suffix) for suffix in ("", "-wal", "-shm", "-journal")}


def inventory_changes(before, after, phase):
    changes = []
    for suffix in sorted(set(before) | set(after)):
        a, b = before.get(suffix), after.get(suffix)
        if a is None or b is None:
            if a != b:
                changes.append({"phase": phase, "member_suffix": suffix, "field": "presence",
                    "before": a is not None, "after": b is not None})
        else:
            for key in sorted(set(a) | set(b)):
                if a.get(key) != b.get(key):
                    changes.append({"phase": phase, "member_suffix": suffix, "field": key,
                        "before": a.get(key), "after": b.get(key)})
    return changes


def source_files(root):
    result = {}
    queue = [root]
    while queue:
        directory = queue.pop()
        for path in paths_at(directory):
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                queue.append(path)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                result[str(path.relative_to(root))] = hashlib.sha256(raw(path)).hexdigest()
            else:
                raise ValueError("NATIVE_PROFILE_CODE_ALIAS_INVALID")
    return result


def paths_at(root):
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME)
    try:
        before = root.lstat()
        names = os.listdir(descriptor)
        identity = tuple(getattr(before, key) for key in FIELDS)
        if (len(names) > 32768
                or identity != tuple(getattr(root.lstat(), key) for key in FIELDS)
                or identity != tuple(getattr(os.fstat(descriptor), key) for key in FIELDS)):
            raise ValueError("NATIVE_PROFILE_DIRECTORY_CHANGED_OR_UNBOUNDED")
        return [root / name for name in names]
    finally:
        os.close(descriptor)


def custody_at(root):
    info = root.lstat()
    return {"directory": [info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode,
        info.st_nlink, info.st_size, info.st_blocks, info.st_atime_ns, info.st_mtime_ns, info.st_ctime_ns],
        "members": {path.name: signature(path) for path in paths_at(root)}}


def residence(root):
    logical = allocated = files = temporaries = entries = 0
    queue = [root]
    while queue:
        path = queue.pop(); info = path.lstat()
        allocated += info.st_blocks * 512
        logical += info.st_size
        entries += int(path != root)
        if stat.S_ISDIR(info.st_mode):
            queue.extend(paths_at(path))
        else:
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("NATIVE_PROFILE_ALIAS_OR_NAMESPACE_INVALID")
            files += 1
            temporaries += int(path.name.endswith(".tmp") or path.name in {"BUILD.json", "GC.json"})
    return {"logical_bytes_including_directories": logical, "allocated_bytes_including_directories": allocated,
            "files": files, "entries_excluding_root": entries, "pending_temporaries_or_intents": temporaries}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--source-index", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--catalog-count", type=int, default=1200)
    parser.add_argument("--ticks", type=int, default=4)
    args = parser.parse_args()
    source_root = Path(args.source_root).absolute()
    if source_root.resolve(strict=True) != source_root or any(parent.is_symlink() for parent in source_root.parents):
        raise ValueError("NATIVE_PROFILE_CODE_ALIAS_INVALID")
    pin = json.loads(raw(Path(args.source_index)))
    if (not isinstance(pin, dict) or pin.get("schema") != "rc6.complete-archive-source-pin.v1"
            or pin.get("source_sha") != args.source_sha or pin.get("source_tree") != args.source_tree
            or type(pin.get("overlay_count")) is not int or pin["overlay_count"] != 0
            or not isinstance(pin.get("files"), dict)):
        raise ValueError("NATIVE_PROFILE_COMPLETE_SOURCE_PIN_REQUIRED")
    code_before = source_files(source_root)
    if code_before != pin["files"]:
        raise ValueError("NATIVE_PROFILE_COMPLETE_SOURCE_HASH_MISMATCH")
    root = Path(args.root).absolute()
    if (root.exists() or root == source_root or root in source_root.parents or source_root in root.parents
            or root.parent.resolve() != root.parent or args.ticks not in (4, 1201)
            or args.catalog_count not in (5, 1200, 12000)):
        raise ValueError("NATIVE_PROFILE_EMPTY_ROOT_AND_AUTHORIZED_SIZE_REQUIRED")
    root.mkdir(mode=0o700, parents=True)
    sys.path.insert(0, str(source_root))
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    for name in tuple(os.environ):
        if name.startswith("POROTA_CAPACITY_") and name.endswith("_PATH"):
            os.environ.pop(name)
    for name in ("HIST_DB_PATH", "POROTA_DYNAMIC_SHADOW_ROOT", "POROTA_SHADOW_RUNTIME_ROOT",
                 "POROTA_DYNAMIC_SHADOW_ARCHIVE_ROOT", "POROTA_DYNAMIC_SHADOW_ARCHIVE_MAX_BYTES",
                 "POROTA_IOL_SHADOW_ROOT", "POROTA_IOL_SHADOW_CACHE_PATH"):
        os.environ.pop(name, None)
    data = root / "data"; database = data / "paper_v17" / "observer_v17.db"
    database.parent.mkdir(mode=0o700, parents=True)
    os.environ.update(DATA_DIR=str(data), PAPER_V17_DB_PATH=str(database), POROTA_DYNAMIC_CAPACITY_MODE="OFF")
    from cg_paper_workspace import artifact_root
    from scripts.rc6_sqlite_scratch_guard import runtime_settings
    from rc6_audit_evidence import sqlite_scratch
    scratch = artifact_root(database) / "sqlite-read-scratch"
    scratch.mkdir(mode=0o700, parents=True)
    environment = {"DATA_DIR": str(data), "PAPER_V17_DB_PATH": str(database), "POROTA_DYNAMIC_CAPACITY_MODE": "OFF",
                   **runtime_settings(str(scratch))}
    os.environ.update(environment)
    # The existing native disk guard rejects tmpfs, wrong ownership, aliases
    # or weakened budgets. No fall back to unconfigured TemporaryDirectory.
    scratch_admission = sqlite_scratch.inspect_scratch(scratch)
    provider_calls = []
    def no_network(*_, **__):
        provider_calls.append("NETWORK_ATTEMPT")
        raise AssertionError("NATIVE_PROFILE_NETWORK_FORBIDDEN")
    socket.socket.connect = no_network; socket.socket.connect_ex = no_network
    socket.create_connection = no_network; socket.getaddrinfo = no_network
    from datetime import timedelta
    from scripts.rc6_issue465_stress import fixture_database, PRE, AT
    from rc6_shadow_runtime.worker import ShadowRuntime
    import rc6_shadow_runtime.worker as worker_module
    from rc6_shadow_runtime.retention import EvidenceRetention
    from rc6_shadow_runtime.archive_namespace import inspect_archive
    from rc6_shadow_runtime.persistence import read_committed_projection

    store = fixture_database(database, catalog_count=args.catalog_count, observations_per_identity=5)
    del store; gc.collect()
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    del connection; gc.collect()
    before = database_inventory(database)
    result = {"schema": "rc6.native-archive-v3-profile.v2", "source_sha": args.source_sha, "source_tree": args.source_tree,
        "scope": "OFFLINE_SYNTHETIC_CANONICAL_FACTORY_NO_OPEN_AUTHORITY", "catalog_count": args.catalog_count,
        "input_observation_rows": args.catalog_count * 5, "ticks_requested": args.ticks, "tick_seconds": 30,
        "native_ticks_planned_including_preopen": 4 if args.ticks == 4 else args.ticks + 1,
        "preopen_seed_ticks": 1, "scope_is_descriptive_four_cuts": args.ticks == 4,
        "fixture_environment": environment, "scratch_admission": scratch_admission,
        "source_index_sha256": hashlib.sha256(raw(Path(args.source_index))).hexdigest(),
        "source_file_count": len(code_before), "overlay_count": 0,
        "source_database_before": before, "source_database_changes": [],
        "peak_scratch_native_policy_occupied_bytes": 0, "native_scratch_measure_calls": 0,
        "source_database": str(database), "complete": False, "acceptance_complete": False,
        "source_database_unchanged": False, "code_source_unchanged": False, "provider_requests": 0,
        "peak_archive": {"logical_bytes_including_directories": 0, "allocated_bytes_including_directories": 0, "files": 0,
                         "entries_excluding_root": 0, "pending_temporaries_or_intents": 0},
        "peak_live": {"logical_bytes_including_directories": 0, "allocated_bytes_including_directories": 0, "files": 0,
                      "entries_excluding_root": 0, "pending_temporaries_or_intents": 0},
        "cuts": [], "restarts": [], "archive_restore_count": 0, "native_input_reads": []}
    destination = root / "result.json"
    begin, cpu = time.monotonic(), time.process_time()
    worker = ShadowRuntime.from_environment(database)
    archive = worker.files.archive_root
    result.update(evidence_root=str(worker.root), archive_root=str(archive), archive_format=worker.files.archive_format,
                  live_maximum_bytes=worker.files.maximum_bytes, live_maximum_files=worker.files.maximum_files,
                  archive_maximum_bytes=worker.files.archive_maximum_bytes)

    def source_unchanged(phase):
        after = database_inventory(database)
        changes = inventory_changes(before, after, phase)
        result["source_database_changes"].extend(changes)
        if changes:
            raise AssertionError("NATIVE_PROFILE_SOURCE_DATABASE_BYTES_OR_METADATA_CHANGED")

    def peaks():
        for name, path in (("archive", archive), ("live", worker.root)):
            if path.exists():
                current = residence(path)
                for metric in result["peak_" + name]:
                    result["peak_" + name][metric] = max(result["peak_" + name][metric], current[metric])
    original_fsync = os.fsync
    original_measure = sqlite_scratch._Lease.measure
    original_runtime_read = worker_module.read_runtime
    def measured_runtime_read(*arguments, **keywords):
        value = original_runtime_read(*arguments, **keywords)
        result["native_input_reads"].append({"as_of": keywords["as_of"].isoformat(),
            "row_limit": keywords["row_limit"], "query_budget_seconds": keywords["query_budget_seconds"],
            "catalog_returned": len(value["catalog"]), "observations_returned": len(value["observations"]),
            "observation_read_truncated": value["observation_read_truncated"]})
        return value
    worker_module.read_runtime = measured_runtime_read
    def measured_scratch(lease):
        occupied = original_measure(lease)
        result["native_scratch_measure_calls"] += 1
        result["peak_scratch_native_policy_occupied_bytes"] = max(
            result["peak_scratch_native_policy_occupied_bytes"], occupied)
        return occupied
    sqlite_scratch._Lease.measure = measured_scratch
    def measured_fsync(descriptor):
        # Observe simultaneous temporary/control/original residence before
        # the real fsync and again after it; never replace its durability.
        peaks(); original_fsync(descriptor); peaks()
    os.fsync = measured_fsync
    try:
        clocks = ([PRE, AT, AT+timedelta(seconds=30), AT+timedelta(seconds=60)] if args.ticks == 4
                  else [PRE, *[AT+timedelta(seconds=30*index) for index in range(args.ticks)]])
        for index, as_of in enumerate(clocks):
            if args.ticks > 4 and index in (361, 841):
                worker = ShadowRuntime.from_environment(database); result["restarts"].append(index)
            cycle_start = time.monotonic()
            start, process = cycle_start, time.process_time()
            report = worker.tick(as_of)
            source_unchanged("pipeline_tick_" + str(index))
            node = {"tick_index": index, "preopen_seed": index == 0, "as_of": as_of.isoformat(), "generation_id": report["generation_id"],
                "sequence": report["sequence"], "phase": report["phase"], "checkpoint_reused": report["checkpoint_reused"],
                "pipeline_wall_seconds": time.monotonic()-start, "pipeline_cpu_seconds": time.process_time()-process,
                "configuration_fingerprint": report["configuration_fingerprint"], "retention_status": report["evidence_retention"]["status"],
                "catalog_ready_count": len(report["catalog_ready"]), "provider_requests": report["provider_requests"],
                "real_orders_sent": report["real_orders_sent"], "real_routes": report["real_routes"]}
            generation = worker.root / ("gen-" + report["generation_id"])
            current = json.loads(raw(worker.root / "CURRENT.json"))
            if (current["generation_id"], current["sequence"]) != (node["generation_id"], node["sequence"]):
                raise AssertionError("NATIVE_PROFILE_RETURNED_WITHOUT_ACTUAL_CURRENT_COMMIT")
            originals = {path.name: raw(path) for path in paths_at(generation)}
            original_custody = custody_at(generation)
            del report; gc.collect()
            archiver = EvidenceRetention(worker.root, archive_root=archive, archive_format=worker.files.archive_format,
                maximum_bytes=worker.files.maximum_bytes, maximum_files=worker.files.maximum_files,
                archive_maximum_bytes=worker.files.archive_maximum_bytes)
            start, process = time.monotonic(), time.process_time()
            receipt = archiver.archive_generation(generation)
            node.update(archive_wall_seconds=time.monotonic()-start, archive_cpu_seconds=time.process_time()-process,
                recipe_bytes=(archive / (receipt["generation_id"] + ".recipe.gz")).stat().st_size)
            restored = archiver.restore_generation(receipt["generation_id"])
            if restored["members"] != originals:
                raise AssertionError("NATIVE_PROFILE_ORIGINAL_MEMBER_RESTORE_MISMATCH")
            if original_custody != custody_at(generation):
                raise AssertionError("NATIVE_PROFILE_ORIGINAL_SOURCE_CUSTODY_CHANGED")
            source_unchanged("archive_restore_tick_" + str(index))
            result["archive_restore_count"] += 1
            node.update(member_bytes={name: len(value) for name, value in originals.items()},
                        original_member_sha256={name: hashlib.sha256(value).hexdigest() for name, value in originals.items()},
                        full_cycle_wall_seconds=time.monotonic()-cycle_start, archive_residence=residence(archive),
                        live_residence=residence(worker.root), archive_verification_level=restored["verification_level"])
            result["cuts"].append(node); peaks()
            if any(node[key] != value for key, value in (("catalog_ready_count", args.catalog_count), ("provider_requests", 0),
                ("real_orders_sent", 0), ("real_routes", "NOT_CALLED"))):
                raise AssertionError("NATIVE_PROFILE_CARDINALITY_OR_SAFETY_MISMATCH")
            for name, byte_limit, file_limit in (("archive", worker.files.archive_maximum_bytes, 32768),
                                               ("live", worker.files.maximum_bytes, worker.files.maximum_files)):
                peak = result["peak_" + name]
                if max(peak["allocated_bytes_including_directories"], peak["logical_bytes_including_directories"]) > byte_limit:
                    raise AssertionError("NATIVE_PROFILE_" + name.upper() + "_BYTE_QUOTA_EXCEEDED")
                if peak["entries_excluding_root"] > file_limit:
                    raise AssertionError("NATIVE_PROFILE_" + name.upper() + "_FILE_QUOTA_EXCEEDED")
            if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024 > 2*1024**3:
                raise AssertionError("NATIVE_PROFILE_RSS_EXCEEDED")
            if args.ticks > 4 and index > 0 and node["full_cycle_wall_seconds"] > 30:
                raise AssertionError("NATIVE_PROFILE_WHEEL_PIPELINE_ARCHIVE_RESTORE_CANNOT_SUSTAIN_30SECOND_CADENCE")
            if index in result["restarts"] and not node["checkpoint_reused"]:
                raise AssertionError("NATIVE_PROFILE_NATIVE_RESTART_DID_NOT_RECOVER_COMMITTED_CHECKPOINT")
            destination.write_text(json.dumps(result, sort_keys=True, indent=2)+"\n")
            if index < 4 or index % 100 == 0:
                print(json.dumps({"tick": index, "sequence": node["sequence"], "phase": node["phase"],
                    "pipeline_seconds": node["pipeline_wall_seconds"], "archive_seconds": node["archive_wall_seconds"],
                    "archive_allocated_bytes": node["archive_residence"]["allocated_bytes_including_directories"]}), flush=True)
            del originals, restored; gc.collect()
        final = read_committed_projection(worker.root, limit=1)
        # The measured OPEN horizon is ten hours inclusive. Its first OPEN is
        # exactly at the GC cutoff; the preceding PRE seed is separately charged
        # and may correctly expire. Every measured cut/receipt remains distinct.
        retained_index = 0 if args.ticks == 4 else 1
        first = archiver.restore_generation(result["cuts"][retained_index]["generation_id"])
        if ({name: hashlib.sha256(value).hexdigest() for name, value in first["members"].items()}
                != result["cuts"][retained_index]["original_member_sha256"]):
            raise AssertionError("NATIVE_PROFILE_FULL_WHEEL_FIRST_ORIGINAL_CUT_UNAVAILABLE_OR_CHANGED")
        if final["manifest"]["configuration_fingerprint"] != worker.configuration_fingerprint(clocks[-1]):
            raise AssertionError("NATIVE_PROFILE_CURRENT_AND_CANONICAL_FACTORY_CONFIGURATION_MISMATCH")
        source_unchanged("final_committed_projection_and_first_original_restore")
        if (worker.files.maximum_files != 512 or worker.files.maximum_bytes != 128*1024**2
                or worker.files.archive_maximum_bytes != 512*1024**2
                or result["peak_scratch_native_policy_occupied_bytes"] > sqlite_scratch.MAX_BYTES):
            raise AssertionError("NATIVE_PROFILE_FIXED_CANONICAL_BUDGET_MISMATCH")
        horizon_seconds = (clocks[-1] - clocks[retained_index]).total_seconds()
        if args.ticks == 1201 and horizon_seconds != 10*60*60:
            raise AssertionError("NATIVE_PROFILE_FULL_WHEEL_ACTUAL_CLOCK_HORIZON_MISMATCH")
        result.update(complete=True, final_sequence=final["pointer"]["sequence"], final_as_of=final["manifest"]["as_of"],
            final_configuration_fingerprint=worker.configuration_fingerprint(clocks[-1]), archive_admission=inspect_archive(archive),
            archive_final=residence(archive), live_final=residence(worker.root),
            measured_horizon_seconds=horizon_seconds, first_restored_tick_index=retained_index)
    except BaseException as error:
        result["error"] = {"class": type(error).__name__, "reason": str(error), "traceback": traceback.format_exc()}
        raise
    finally:
        os.fsync = original_fsync
        sqlite_scratch._Lease.measure = original_measure
        worker_module.read_runtime = original_runtime_read
        peaks()
        after = database_inventory(database)
        result.update(elapsed_wall_seconds=time.monotonic()-begin, elapsed_cpu_seconds=time.process_time()-cpu,
            peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
            source_database_after=after,
            source_database_unchanged=before == after,
            code_source_unchanged=code_before == source_files(source_root), provider_requests=len(provider_calls),
            scratch_final=sqlite_scratch.inspect_scratch(scratch))
        result["source_database_changes"].extend(inventory_changes(before, result["source_database_after"], "final"))
        if not result["source_database_unchanged"] or not result["code_source_unchanged"] or provider_calls:
            result["complete"] = False
            result.setdefault("error", {"class": "AssertionError", "reason": "NATIVE_PROFILE_FINAL_SOURCE_OR_SAFETY_CHANGED"})
        result["native_ticks_executed"] = len(result["cuts"])
        result["acceptance_complete"] = bool(args.ticks == 1201 and result["complete"]
            and result["source_database_unchanged"] and result["code_source_unchanged"] and not provider_calls)
        destination.write_text(json.dumps(result, sort_keys=True, indent=2)+"\n")
        print(json.dumps({"path": str(destination), "complete": result["complete"], "acceptance_complete": result["acceptance_complete"],
                         "wall_seconds": result["elapsed_wall_seconds"], "peak_rss_bytes": result["peak_rss_bytes"]}), flush=True)
    if not result["complete"]:
        raise AssertionError("NATIVE_PROFILE_FINAL_SOURCE_OR_SAFETY_CHANGED")


if __name__ == "__main__":
    main()
