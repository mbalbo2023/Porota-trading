"""Native, private archive capacity measurement; source pin is mandatory.

No provider/live execution. Normal four-cut measurements are descriptive. A
ten-hour result requires all 1201 full native ticks, original-byte recovery,
restarts and both fixed byte/file policies; it is never inferred from a pair.
Run against a complete immutable git archive, not an overlay or a PYTHONPATH mix.
"""
import argparse
import gc
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


def signature(path):
    value = path.lstat()
    return [value.st_dev, value.st_ino, value.st_uid, value.st_gid, value.st_mode,
        value.st_nlink, value.st_size, value.st_atime_ns, value.st_mtime_ns, value.st_ctime_ns,
        hashlib.sha256(raw(path)).hexdigest()]


def paths_at(root):
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME)
    try:
        before = root.lstat()
        names = os.listdir(descriptor)
        if len(names) > 32768 or before != root.lstat() or before != os.fstat(descriptor):
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
    parser.add_argument("--root", required=True)
    parser.add_argument("--catalog-count", type=int, default=1200)
    parser.add_argument("--ticks", type=int, default=4)
    args = parser.parse_args()
    source_root = Path(args.source_root).resolve(strict=True)
    root = Path(args.root).absolute()
    if root.exists() or args.ticks not in (4, 1201) or args.catalog_count not in (5, 1200, 12000):
        raise ValueError("NATIVE_PROFILE_EMPTY_ROOT_AND_AUTHORIZED_SIZE_REQUIRED")
    root.mkdir(mode=0o700, parents=True)
    sys.path.insert(0, str(source_root))
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
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
    provider_calls = []
    def no_network(*_, **__):
        provider_calls.append("NETWORK_ATTEMPT")
        raise AssertionError("NATIVE_PROFILE_NETWORK_FORBIDDEN")
    socket.socket.connect = no_network; socket.create_connection = no_network; socket.getaddrinfo = no_network
    from datetime import timedelta
    from scripts.rc6_issue465_stress import fixture_database, PRE, AT
    from rc6_shadow_runtime.worker import ShadowRuntime
    from rc6_shadow_runtime.retention import EvidenceRetention
    from rc6_shadow_runtime.archive_namespace import inspect_archive
    from rc6_shadow_runtime.persistence import read_committed_projection

    code_before = {str(path.relative_to(source_root)): hashlib.sha256(raw(path)).hexdigest()
                   for path in source_root.rglob("*") if path.is_file()}
    store = fixture_database(database, catalog_count=args.catalog_count, observations_per_identity=5)
    del store; gc.collect()
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close(); del connection; gc.collect()
    before = {str(path): signature(path) for path in paths_at(database.parent) if path.is_file()}
    result = {"schema": "rc6.native-archive-v3-profile.v1", "source_sha": args.source_sha, "source_tree": args.source_tree,
        "scope": "OFFLINE_SYNTHETIC_CANONICAL_FACTORY_NO_OPEN_AUTHORITY", "catalog_count": args.catalog_count,
        "input_observation_rows": args.catalog_count * 5, "ticks_requested": args.ticks, "tick_seconds": 30,
        "fixture_environment": {key: os.environ[key] for key in ("DATA_DIR", "PAPER_V17_DB_PATH", "POROTA_DYNAMIC_CAPACITY_MODE")},
        "source_database": str(database), "complete": False, "acceptance_complete": False,
        "source_database_unchanged": False, "code_source_unchanged": False, "provider_requests": 0,
        "peak_archive": {"logical_bytes_including_directories": 0, "allocated_bytes_including_directories": 0, "files": 0,
                         "entries_excluding_root": 0, "pending_temporaries_or_intents": 0},
        "peak_live": {"logical_bytes_including_directories": 0, "allocated_bytes_including_directories": 0, "files": 0,
                      "entries_excluding_root": 0, "pending_temporaries_or_intents": 0},
        "cuts": [], "restarts": [], "archive_restore_count": 0}
    destination = root / "result.json"
    begin, cpu = time.monotonic(), time.process_time()
    worker = ShadowRuntime.from_environment(database)
    archive = worker.files.archive_root
    result.update(evidence_root=str(worker.root), archive_root=str(archive), archive_format=worker.files.archive_format,
                  live_maximum_bytes=worker.files.maximum_bytes, live_maximum_files=worker.files.maximum_files,
                  archive_maximum_bytes=worker.files.archive_maximum_bytes)

    def peaks():
        for name, path in (("archive", archive), ("live", worker.root)):
            if path.exists():
                current = residence(path)
                for metric in result["peak_" + name]:
                    result["peak_" + name][metric] = max(result["peak_" + name][metric], current[metric])
    original_fsync = os.fsync
    def measured_fsync(descriptor):
        # Observe simultaneous temporary/control/original residence before
        # the real fsync and again after it; never replace its durability.
        peaks(); original_fsync(descriptor); peaks()
    os.fsync = measured_fsync
    try:
        clocks = ([PRE, AT, AT+timedelta(seconds=30), AT+timedelta(seconds=60)] if args.ticks == 4
                  else [AT+timedelta(seconds=30*index) for index in range(args.ticks)])
        for index, as_of in enumerate(clocks):
            if args.ticks > 4 and index in (360, 840):
                worker = ShadowRuntime.from_environment(database); result["restarts"].append(index)
            cycle_start = time.monotonic()
            start, process = cycle_start, time.process_time()
            report = worker.tick(as_of)
            node = {"tick_index": index, "as_of": as_of.isoformat(), "generation_id": report["generation_id"],
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
            if args.ticks > 4 and node["full_cycle_wall_seconds"] > 30:
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
        first = archiver.restore_generation(result["cuts"][0]["generation_id"])
        if ({name: hashlib.sha256(value).hexdigest() for name, value in first["members"].items()}
                != result["cuts"][0]["original_member_sha256"]):
            raise AssertionError("NATIVE_PROFILE_FULL_WHEEL_FIRST_ORIGINAL_CUT_UNAVAILABLE_OR_CHANGED")
        if final["manifest"]["configuration_fingerprint"] != worker.configuration_fingerprint(clocks[-1]):
            raise AssertionError("NATIVE_PROFILE_CURRENT_AND_CANONICAL_FACTORY_CONFIGURATION_MISMATCH")
        result.update(complete=True, final_sequence=final["pointer"]["sequence"], final_as_of=final["manifest"]["as_of"],
            final_configuration_fingerprint=worker.configuration_fingerprint(clocks[-1]), archive_admission=inspect_archive(archive),
            archive_final=residence(archive), live_final=residence(worker.root))
    except BaseException as error:
        result["error"] = {"class": type(error).__name__, "reason": str(error), "traceback": traceback.format_exc()}
        raise
    finally:
        os.fsync = original_fsync
        peaks()
        result.update(elapsed_wall_seconds=time.monotonic()-begin, elapsed_cpu_seconds=time.process_time()-cpu,
            peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
            source_database_unchanged=before == {str(path): signature(path) for path in paths_at(database.parent) if path.is_file()},
            code_source_unchanged=code_before == {str(path.relative_to(source_root)): hashlib.sha256(raw(path)).hexdigest()
                for path in source_root.rglob("*") if path.is_file()}, provider_requests=len(provider_calls))
        destination.write_text(json.dumps(result, sort_keys=True, indent=2)+"\n")
        print(json.dumps({"path": str(destination), "complete": result["complete"], "acceptance_complete": False,
                         "wall_seconds": result["elapsed_wall_seconds"], "peak_rss_bytes": result["peak_rss_bytes"]}), flush=True)


if __name__ == "__main__":
    main()
