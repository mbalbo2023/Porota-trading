#!/usr/bin/env python3
"""Native maximum-member witness and permanent governed-test launcher.

This exercises the canonical factory's publisher and the public V3 archiver,
not business ticks, the full wheel, a Docker image, providers or production.
It requires a complete immutable Git export and the exact approved157 closure.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import gc
import gzip
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import resource
import signal
import socket
import sqlite3
import stat
import subprocess
import uuid
import sys
import tempfile
import time
import traceback
from urllib.parse import unquote, urlsplit
import zlib

MIB = 1024**2
FIELDS = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size",
          "st_blocks", "st_atime_ns", "st_mtime_ns", "st_ctime_ns")
MIN_MEMBER, MAX_MEMBER = 63*MIB, 64*MIB
MAX_LIVE, MAX_ARCHIVE, MAX_SCRATCH, MAX_RSS = 128*MIB, 512*MIB, 512*MIB, 2*1024**3
MAX_WALL = 300
PRE = datetime(2026, 10, 5, 13, 20, tzinfo=timezone.utc)


def require(condition, reason):
    if not condition:
        raise AssertionError(reason)


def product_module_names(index):
    """Derive product import identities from the complete committed tree."""
    names = set()
    for path in index["files"]:
        if not path.endswith(".py"):
            continue
        pieces = path[:-3].split("/")
        if pieces[-1] == "__init__":
            pieces.pop()
        names.update(".".join(pieces[:count]) for count in range(1, len(pieces)+1))
    return names


def native_deadline(signum, frame):
    raise TimeoutError("NATIVE_MEMORY_WITNESS_300SECOND_DEADLINE")


def safe_path(path, *, absent=False):
    path = Path(path).absolute()
    require(".." not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)), "PROBE_PATH_ALIAS")
    if absent:
        require(not path.exists(), "PROBE_PATH_ALREADY_EXISTS")
    return path


def identity(info):
    return {field: getattr(info, field) for field in FIELDS}


def capture(path, *, raw=False, maximum=128*MIB):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_uid == os.geteuid() and before.st_size <= maximum,
                "PROBE_FILE_CUSTODY_OR_BOUND")
        sha256, blob = hashlib.sha256(), hashlib.sha1()
        blob.update(b"blob " + str(before.st_size).encode() + b"\0")
        parts, size = [], 0
        while part := os.read(fd, 1024*1024):
            sha256.update(part); blob.update(part); size += len(part)
            if raw:
                parts.append(part)
        require(size == before.st_size and identity(before) == identity(os.fstat(fd)) == identity(Path(path).lstat()),
                "PROBE_FILE_CHANGED_DURING_CAPTURE")
        result = {**identity(before), "sha256": sha256.hexdigest(), "blob_id": blob.hexdigest()}
        return (b"".join(parts), result) if raw else result
    finally:
        os.close(fd)


def paths_at(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME)
    try:
        before = identity(os.fstat(fd))
        names = os.listdir(fd)
        require(len(names) <= 32768 and before == identity(os.fstat(fd)) == identity(Path(directory).lstat()),
                "PROBE_DIRECTORY_CHANGED_OR_UNBOUNDED")
        return [Path(directory)/name for name in sorted(names)]
    finally:
        os.close(fd)


def tree(path, *, hashes=False):
    if not path.exists():
        return None
    result, pending = {}, [Path(path)]
    while pending:
        member = pending.pop()
        info = member.lstat()
        require(info.st_uid == os.geteuid(), "PROBE_TREE_OWNER_MISMATCH")
        rel = "." if member == path else str(member.relative_to(path))
        require(len(result) < 32768, "PROBE_TREE_UNBOUNDED")
        if stat.S_ISDIR(info.st_mode):
            result[rel] = identity(info)
            pending.extend(paths_at(member))
        else:
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "PROBE_TREE_ALIAS")
            result[rel] = capture(member) if hashes else identity(info)
    return result


def residence(path):
    inventory = tree(path) or {}
    return {"logical_bytes": sum(row["st_size"] for row in inventory.values()),
            "allocated_bytes": sum(row["st_blocks"]*512 for row in inventory.values()),
            "entries_excluding_root": max(0, len(inventory)-1),
            "files": sum(stat.S_ISREG(row["st_mode"]) for row in inventory.values()),
            "temporary_entries": sum(name.endswith(".tmp") or name in ("BUILD.json", "GC.json")
                                     for name in inventory)}


def original_database(path):
    return {suffix: capture(Path(str(path)+suffix)) if Path(str(path)+suffix).exists() else None
            for suffix in ("", "-wal", "-shm", "-journal")}


def source_inventory(source, index):
    inventory = tree(source, hashes=True)
    observed = {name: row for name, row in inventory.items() if stat.S_ISREG(row["st_mode"])}
    require(set(observed) == set(index["files"]) == set(index["modes"]) == set(index["blob_ids"]),
            "COMPLETE_SOURCE_MEMBER_SET_MISMATCH")
    for name, row in observed.items():
        mode = "100" + format(stat.S_IMODE(row["st_mode"]), "03o")
        require(row["sha256"] == index["files"][name] and mode == index["modes"][name]
                and row["blob_id"] == index["blob_ids"][name], "COMPLETE_SOURCE_BYTES_MODE_BLOB_MISMATCH")
    # Normal Python imports may read code. Source identity is bytes/mode/blob;
    # full unchanged stat+byte custody is separately required for native data.
    return {name: {"sha256": row["sha256"], "mode": index["modes"][name], "blob_id": row["blob_id"]}
            for name, row in observed.items()}


def git_authority(repo, sha, expected_tree, index):
    def git(*args):
        return subprocess.check_output(["git", "--no-replace-objects", "-c", "protocol.allow=never", "-C", str(repo), *args],
            env={**os.environ, "GIT_NO_LAZY_FETCH": "1"}, stderr=subprocess.DEVNULL, timeout=10)
    require(not git("for-each-ref", "--format=%(refname)", "refs/replace").strip(), "GIT_REPLACE_AUTHORITY_FORBIDDEN")
    raw = git("cat-file", "commit", sha)
    oid = hashlib.sha1(b"commit " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    actual_tree = git("rev-parse", "--verify", sha+"^{tree}").decode().strip()
    require(oid == sha and actual_tree == expected_tree and raw.splitlines()[0] == b"tree "+actual_tree.encode(),
            "RAW_GIT_COMMIT_OR_TREE_MISMATCH")
    tracked = {}
    for row in git("ls-tree", "-r", "--full-tree", "-z", sha).split(b"\0"):
        if not row:
            continue
        header, name = row.split(b"\t", 1)
        mode, kind, blob = header.decode("ascii").split()
        require(kind == "blob" and mode in ("100644", "100755"), "GIT_SOURCE_TRACKED_MODE_OR_TYPE_INVALID")
        path = name.decode("utf-8")
        require(path not in tracked, "GIT_SOURCE_DUPLICATE_PATH")
        tracked[path] = {"mode": mode, "blob_id": blob}
    require(set(tracked) == set(index["files"]) == set(index["modes"]) == set(index["blob_ids"])
            and all(row["mode"] == index["modes"][name] and row["blob_id"] == index["blob_ids"][name]
                    for name, row in tracked.items()), "FROZEN_INDEX_NOT_BOUND_TO_RAW_GIT_TREE")
    return {"source_sha": oid, "source_tree": actual_tree, "raw_commit_sha256": hashlib.sha256(raw).hexdigest(),
            "tracked_source_objects": len(tracked),
            "git_tree_member_binding_sha256": hashlib.sha256(json.dumps(tracked, sort_keys=True,
                        separators=(",", ":")).encode()).hexdigest()}


def parser():
    p = argparse.ArgumentParser()
    p.add_argument("--source-root", type=Path, required=True)
    p.add_argument("--git-repo", type=Path, required=True)
    p.add_argument("--source-index", type=Path, required=True)
    p.add_argument("--source-sha", required=True)
    p.add_argument("--source-tree", required=True)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--receipt", type=Path, required=True)
    p.add_argument("--execution-id", required=True)
    return p


def main():
    process_begin = time.monotonic()
    signal.signal(signal.SIGALRM, native_deadline)
    signal.alarm(MAX_WALL)
    args = parser().parse_args()
    source, root, receipt_path = safe_path(args.source_root), safe_path(args.root, absent=True), safe_path(args.receipt, absent=True)
    require(not root.is_relative_to(source) and not source.is_relative_to(root) and not receipt_path.is_relative_to(source)
            and not receipt_path.is_relative_to(root), "PROBE_OUTPUT_MUST_BE_EXTERNAL")
    require(os.geteuid() == os.getuid(), "NATIVE_FIXTURE_REAL_EFFECTIVE_OWNER_MISMATCH")
    require(sys.platform == "linux" and sys.version_info[:2] in ((3, 11), (3, 12)),
            "APPROVED_LINUX_PYTHON_311_OR_312_REQUIRED")
    require(len(args.execution_id) == 32 and all(c in "0123456789abcdef" for c in args.execution_id),
            "FRESH_NATIVE_EXECUTION_ID_REQUIRED")
    index_raw, _ = capture(safe_path(args.source_index), raw=True, maximum=8*MIB)
    index = json.loads(index_raw)
    require(index.get("schema") == "rc6.complete-archive-source-pin.v1" and index.get("source_sha") == args.source_sha
            and index.get("source_tree") == args.source_tree and type(index.get("overlay_count")) is int
            and index["overlay_count"] == 0, "COMPLETE_FROZEN_SOURCE_INDEX_REQUIRED")
    source_before = source_inventory(source, index)
    git_before = git_authority(safe_path(args.git_repo), args.source_sha, args.source_tree, index)
    network = []
    def no_network(*a, **kw):
        network.append("BLOCKED")
        raise AssertionError("NATIVE_MEMORY_NETWORK_FORBIDDEN")
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = socket.getaddrinfo = no_network
    socket.socket.send = socket.socket.sendall = socket.socket.sendto = no_network
    if hasattr(socket.socket, "sendmsg"):
        socket.socket.sendmsg = no_network
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source))
    from scripts.porota_dependency_repro_audit import installed_distribution_audit
    policy = json.loads(capture(source/"ops/policy/rc6-supply-chain-v1.json", raw=True)[0])
    closure = installed_distribution_audit(policy)
    require(closure["status"] == "GREEN" and closure["expected_total"] == closure["installed_total"] == 157,
            "EXACT_APPROVED_157_INSTALLED_CLOSURE_REQUIRED")
    from be_paper_engine import PaperStore
    from cg_paper_workspace import artifact_root
    from scripts.rc6_sqlite_scratch_guard import runtime_settings
    from rc6_audit_evidence.sqlite_scratch import inspect_scratch
    from rc6_shadow_runtime.worker import ShadowRuntime
    from rc6_shadow_runtime.retention import EvidenceRetention, RetentionPressure
    from rc6_shadow_runtime import projection, archive_components as components

    for key in tuple(os.environ):
        if key.startswith(("POROTA_", "PAPER_")) or key in ("DATA_DIR", "HIST_DB_PATH"):
            os.environ.pop(key, None)
    result = {"schema": "rc6.native-maximum-archive-member-memory-witness.v2",
              "execution_id": args.execution_id, "native_pid": os.getpid(),
              "guards_in_shared_native_execution": 2, "native_executions": 1,
              "scope": "CANONICAL_FACTORY_PUBLICATION_AND_PUBLIC_V3_ARCHIVE_MEMORY_ONLY",
              "source_sha": args.source_sha, "source_tree": args.source_tree, "overlay_count": 0,
              "source_index_sha256": hashlib.sha256(index_raw).hexdigest(), "git_authority": git_before,
              "probe_source_sha256": capture(Path(__file__))["sha256"],
              "source_files": len(source_before), "installed_closure": closure, "uid": os.geteuid(),
              "runtime_validated": False, "artifact_validated": False, "horizon_validated": False,
              "full_business_ticks_executed": 0, "complete": False, "calibration": [], "cuts": [],
              "real_fsync_calls": 0,
              "fixture_note": "Planner rows and repeated identities are explicit private typed publisher inputs; no entity uniqueness, finance, market, cadence or horizon claim.",
              "calibration_scope": "AT_MOST_THREE_INDEPENDENT_CANONICAL_PRIVATE_DATASETS; each obeys native128/512; aggregate residence is separately recorded.",
              "limits": {"live_bytes": MAX_LIVE, "live_entries": 512, "archive_bytes": MAX_ARCHIVE,
                         "archive_entries": 32768, "scratch_bytes": MAX_SCRATCH, "rss_bytes": MAX_RSS,
                         "wall_seconds": MAX_WALL}, "peaks": {}, "data_custody": {}, "network_attempts": 0}
    root.mkdir(mode=0o700)
    require(root.lstat().st_uid == os.geteuid() and stat.S_IMODE(root.lstat().st_mode) == 0o700,
            "PRIVATE_FIXTURE_ROOT_CUSTODY_REQUIRED")
    actual_tempdir = tempfile.tempdir
    begin = process_begin
    environments, databases = [], []
    immutable_sources, source_sqlite_attempts = [], []
    actual_sqlite_connect = sqlite3.connect
    def private_sqlite(value, *a, **kw):
        decoded = os.fsdecode(value)
        decoded = unquote(urlsplit(decoded).path) if decoded.startswith("file:") else decoded
        path = Path(decoded).absolute() if decoded != ":memory:" else None
        if path is not None and any(path == original or (path.exists() and path.samefile(original))
                                    for original in immutable_sources):
            source_sqlite_attempts.append(str(path))
            raise AssertionError("IMMUTABLE_SOURCE_SQLITE_OPEN_FORBIDDEN")
        return actual_sqlite_connect(value, *a, **kw)
    sqlite3.connect = private_sqlite
    namespaces = []
    tempfile_requests, physical_destroy_observations = [], []
    current_scratch = None
    audit_observing = False
    actual_fsync = os.fsync
    require(getattr(actual_fsync, "__module__", None) == "posix", "ACTUAL_NATIVE_FSYNC_REQUIRED")
    def peak():
        space = os.statvfs(root)
        free_bytes = space.f_bavail*space.f_frsize
        require(free_bytes >= 2*1024**3 and space.f_files > 0 and space.f_favail*100 >= space.f_files*10,
                "NATIVE_FILESYSTEM_SHARED_RESERVE_OR_INODES_EXCEEDED")
        result["minimum_free_bytes_observed"] = min(result.get("minimum_free_bytes_observed", free_bytes), free_bytes)
        for name, path, byte_limit, entry_limit in namespaces:
            if path.exists():
                require(path.lstat().st_dev == root.lstat().st_dev, "NATIVE_DERIVED_ROOT_FILESYSTEM_DRIFT")
            current = residence(path)
            previous = result["peaks"].setdefault(name, {k: 0 for k in current})
            for key, value in current.items():
                previous[key] = max(previous[key], value)
            require(max(current["logical_bytes"], current["allocated_bytes"]) <= byte_limit
                    and current["entries_excluding_root"] <= entry_limit, "NATIVE_PHYSICAL_OR_LOGICAL_PEAK_EXCEEDED")
        result["peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
        overall = residence(root)
        old_overall = result.setdefault("overall_private_fixture_peak", {k: 0 for k in overall})
        for key, value in overall.items():
            old_overall[key] = max(old_overall[key], value)
        require(result["peak_rss_bytes"] <= MAX_RSS, "NATIVE_REAL_RSS_2GIB_EXCEEDED")
    def measured_fsync(fd):
        peak(); actual_fsync(fd); result["real_fsync_calls"] += 1; peak()
    def audit_target(value, directory_fd):
        path = Path(os.fsdecode(value))
        if path.is_absolute():
            return safe_path(path)
        if directory_fd is None or directory_fd < 0:
            return safe_path(Path.cwd()/path)
        # os.unlink(name, dir_fd=fd) audits the relative name and actual FD;
        # resolving only against cwd would miss native retention removals.
        descriptor = os.fstat(directory_fd)
        require(stat.S_ISDIR(descriptor.st_mode), "AUDIT_NATIVE_DIRECTORY_FD_REQUIRED")
        directory = safe_path(os.readlink("/proc/self/fd/"+str(directory_fd)))
        require(identity(descriptor) == identity(directory.lstat()), "AUDIT_NATIVE_DIRECTORY_FD_CHANGED")
        return safe_path(directory/path)
    def observed_audit(event, arguments):
        nonlocal audit_observing
        if audit_observing:
            return
        if event in ("tempfile.mkstemp", "tempfile.mkdtemp"):
            requested = Path(os.fsdecode(arguments[0])).absolute()
            request = {"event": event, "path": str(requested),
                       "canonical_scratch": str(current_scratch) if current_scratch is not None else None,
                       "quota_bytes": MAX_SCRATCH}
            tempfile_requests.append(request)
            require(current_scratch is not None and requested.is_relative_to(current_scratch),
                    "TEMPFILE_REQUEST_OUTSIDE_CANONICAL_NATIVE_SCRATCH")
        elif event in ("os.remove", "os.rmdir", "os.rename") and arguments and isinstance(arguments[0], (str, bytes)):
            audit_observing = True
            try:
                if event == "os.rename":
                    targets = [(arguments[0], arguments[2] if len(arguments) > 2 else None),
                               (arguments[1], arguments[3] if len(arguments) > 3 else None)]
                else:
                    targets = [(arguments[0], arguments[1] if len(arguments) > 1 else None)]
                inside = [(audit_target(value, directory_fd), directory_fd) for value, directory_fd in targets]
                inside = [(path, directory_fd) for path, directory_fd in inside
                          if path.is_relative_to(root) and path.exists()]
                if inside:
                    peak()
                    for path, directory_fd in inside:
                        physical_destroy_observations.append({"event": event, "path": str(path),
                            "directory_fd": directory_fd,
                            "residence_before_operation": residence(path) if path.is_dir() else identity(path.lstat())})
            finally:
                audit_observing = False
    sys.addaudithook(observed_audit)
    os.fsync = measured_fsync

    def create_factory(label):
        nonlocal current_scratch
        data = root/label/"data"
        database = data/"paper_v17"/"observer_v17.db"
        require(not any(Path(str(database)+s).exists() for s in ("", "-wal", "-shm", "-journal")), "PRIOR_SOURCE_DATA_PRESENT")
        database.parent.mkdir(mode=0o700, parents=True)
        os.environ.update(DATA_DIR=str(data), PAPER_V17_DB_PATH=str(database), POROTA_DYNAMIC_CAPACITY_MODE="OFF")
        store = PaperStore(str(database))
        del store; gc.collect()
        # Native source initialization is finished before the immutable baseline.
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        gc.collect()
        baseline = original_database(database)
        immutable_sources.extend(Path(str(database)+s) for s in ("", "-wal", "-shm", "-journal")
                                 if Path(str(database)+s).exists())
        scratch = artifact_root(database)/"sqlite-read-scratch"
        scratch.mkdir(mode=0o700, parents=True)
        os.environ.update(runtime_settings(str(scratch)))
        # Generic Python temporary requests share the actual native private
        # scratch path and its one512MiB quota. No auxiliary budget or host
        # TMPDIR is introduced. The typed inspector must accept the namespace.
        current_scratch = scratch
        tempfile.tempdir = str(scratch)
        worker = ShadowRuntime.from_environment(database, source_roots=[])
        require((worker.files.maximum_bytes, worker.files.maximum_files, worker.files.archive_maximum_bytes,
                 worker.files.archive_format) == (MAX_LIVE, 512, MAX_ARCHIVE, "COMPONENT_V3"), "NATIVE_FACTORY_POLICY_DRIFT")
        require(worker.root == artifact_root(database)/"dynamic-shadow"
                and worker.files.archive_root == artifact_root(database)/"dynamic-shadow-archive", "NATIVE_CANONICAL_ROOT_DRIFT")
        environments.append({"label": label, "source_database": str(database), "scratch": str(scratch),
                             "admission": inspect_scratch(scratch), "factory_configuration": worker.configuration})
        databases.append((database, baseline))
        namespaces.extend([(label+".live", worker.root, MAX_LIVE, 512),
                           (label+".archive", worker.files.archive_root, MAX_ARCHIVE, 32768),
                           (label+".scratch", scratch, MAX_SCRATCH, 32768)])
        return worker

    ticker = "MEMORY_FIXTURE_" + "".join(hashlib.sha256(str(number).encode()).hexdigest() for number in range(7))
    def inputs(count, number):
        at = (PRE+timedelta(seconds=30*number)).isoformat()
        common = {"as_of": at, "mode": "SHADOW", "real_orders_sent": 0, "real_routes": "NOT_CALLED",
                  "provider_requests": 0, "factual_execution": "NOT_CALLED"}
        row = {"identity": [ticker, "ACCIONES", "BYMA", "ARS", "A-24HS"], "state": "DISCOVERY", "rank": 1,
               "strategy_id": "SYNTHETIC_ARCHIVE_MEMORY_ONLY", "rejection_reason": "NO_MARKET_AUTHORITY",
               "last_useful_observation_at": None, "source": "PRIVATE_TYPED_PUBLICATION_FIXTURE"}
        report = {**common, "source_reports": [], "source_audit": {},
                  "engines": {"SCALPING": {"telemetry": [row]*count}}}
        return report, dict(common), {**common, "status": "SHADOW_OBSERVING"}, {"as_of": at, "source_identity": "PRIVATE_TYPED_MEMORY_FIXTURE"}

    def publish(worker, count, number):
        report, checkpoint, status, watermark = inputs(count, number)
        with worker.files as files:
            cut = files.commit_generation(report, checkpoint, status, source_watermark=watermark,
                                          configuration_fingerprint=worker.configuration_fingerprint(watermark["as_of"]))
        return cut

    durable = []
    actual_projection = projection.PreparedProjection
    class ObservedProjection(actual_projection):
        def build(self, *a, **kw):
            try:
                return super().build(*a, **kw)
            finally:
                durable.append(self.connection.execute("PRAGMA page_count").fetchone()[0]*4096)

    try:
        projection.PreparedProjection = ObservedProjection
        desired, count, samples, chosen = MIN_MEMBER+MIB//4, 512, [], None
        for attempt in range(1, 4):
            worker = create_factory("calibration-"+str(attempt))
            durable.clear(); error = None; cut = None
            started = time.monotonic()
            try:
                cut = publish(worker, count, 1)
            except ValueError as caught:
                if str(caught) != "SHADOW_PROJECTION_DURABLE_LIMIT" or not durable:
                    raise
                error = "SHADOW_PROJECTION_DURABLE_LIMIT"
            measured = durable[-1]
            node = {"attempt": attempt, "actual_input_rows": count, "native_projection_bytes": measured,
                    "wall_seconds": time.monotonic()-started, "status": "GREEN" if cut else "RED",
                    "reason": error, "sealed_generation": cut["pointer"] if cut else None}
            result["calibration"].append(node); samples.append((count, measured)); peak()
            print(json.dumps({"phase": "CALIBRATION", "attempt": attempt, "native_rows": count,
                              "projection_bytes": measured, "status": node["status"]}), flush=True)
            if cut and MIN_MEMBER <= measured <= MAX_MEMBER:
                chosen = count; break
            if len(samples) == 1:
                count = max(1, min(199999, round(count*(desired-65536)/max(1, measured-65536))))
            else:
                (n1, b1), (n2, b2) = samples[-2:]
                require(n1 != n2 and b1 != b2, "NATIVE_COUNT_CALIBRATION_DID_NOT_PROGRESS")
                count = max(1, min(199999, round(n2+(desired-b2)*(n2-n1)/(b2-b1))))
        projection.PreparedProjection = actual_projection
        require(chosen is not None, "NATIVE_MEMBER_BAND_NOT_REACHED_IN_THREE_ATTEMPTS")
        result["chosen_native_input_rows"] = chosen
        worker = create_factory("main")
        archive, live = worker.files.archive_root, worker.root
        archiver = EvidenceRetention(live, archive_root=archive, archive_format=worker.files.archive_format,
                                    maximum_bytes=worker.files.maximum_bytes, maximum_files=worker.files.maximum_files,
                                    archive_maximum_bytes=worker.files.archive_maximum_bytes) if live.exists() else None
        last = None
        for number in range(1, 35):
            start = time.monotonic()
            cut = publish(worker, chosen if number == 1 else 256, number)
            generation = live/("gen-"+cut["pointer"]["generation_id"])
            original = tree(generation, hashes=True)
            projection_bytes = original["projection.sqlite"]["st_size"]
            require(MIN_MEMBER <= projection_bytes <= MAX_MEMBER if number == 1 else projection_bytes <= MIB,
                    "NATIVE_ACTUAL_MEMBER_BAND_OR_SMALL_CUT_FAILED")
            node = {"sequence": cut["pointer"]["sequence"], "generation_id": cut["pointer"]["generation_id"],
                    "as_of": cut["manifest"]["as_of"], "projection_bytes": projection_bytes,
                    "original_members": {k: v for k, v in original.items() if k != "."}, "archived": number != 32}
            if archiver is None:
                archiver = EvidenceRetention(live, archive_root=archive, archive_format=worker.files.archive_format,
                                            maximum_bytes=worker.files.maximum_bytes, maximum_files=worker.files.maximum_files,
                                            archive_maximum_bytes=worker.files.archive_maximum_bytes)
            if number != 32:
                last = archiver.archive_generation(generation)
                require(original == tree(generation, hashes=True), "NATIVE_ARCHIVE_ORIGINAL_CUSTODY_CHANGED")
                node["receipt"] = last
                if number == 1:
                    first_before = {str(p): tree(p, hashes=True) for p in
                                    (live, live.with_name(live.name+".authority"), archive)}
                    first_encoder_attempts = []
                    saved_first_encoders = gzip.compress, zlib.compress, zlib.compressobj
                    def first_no_encoder(*a, **kw):
                        first_encoder_attempts.append("CALLED")
                        raise AssertionError("NATIVE_LARGE_ANCESTOR_RESTORE_ENCODER_FORBIDDEN")
                    gzip.compress = zlib.compress = zlib.compressobj = first_no_encoder
                    try:
                        large_result = archiver.restore_generation(last["generation_id"])
                    finally:
                        gzip.compress, zlib.compress, zlib.compressobj = saved_first_encoders
                    require(set(large_result["members"]) == set(node["original_members"])
                            and all(hashlib.sha256(value).hexdigest() == node["original_members"][name]["sha256"]
                                    and len(value) == node["original_members"][name]["st_size"]
                                    for name, value in large_result["members"].items())
                            and large_result["manifest"] == cut["manifest"] and not first_encoder_attempts,
                            "NATIVE_LARGE_ANCESTOR_FIVE_ORIGINAL_BYTES_FAILED")
                    require(first_before == {str(p): tree(p, hashes=True) for p in
                            (live, live.with_name(live.name+".authority"), archive)},
                            "NATIVE_LARGE_ANCESTOR_READONLY_CUSTODY_FAILED")
                    result["large_ancestor_original_five_member_restore"] = {
                        "exact_member_sizes_and_sha256": True, "exact_manifest": True, "encoder_attempts": 0,
                        "protected_roots_all_stats_and_hashes_unchanged": True,
                        "verification_level": large_result["verification_level"]}
                    del large_result
            node["wall_seconds"] = time.monotonic()-start
            node["live_residence"] = residence(live)
            node["archive_residence"] = residence(archive)
            node["scratch_residence"] = residence(Path(os.environ["POROTA_SQLITE_SCRATCH_ROOT"]))
            result["cuts"].append(node); peak(); del cut; gc.collect()
            if number in (1, 8, 16, 24, 32, 34):
                print(json.dumps({"phase": "NATIVE_PUBLICATION_AND_ARCHIVE", "sequence": number,
                                  "projection_bytes": projection_bytes, "archived": node["archived"],
                                  "elapsed_wall_seconds": time.monotonic()-begin}), flush=True)
        recipe_wire = capture(archive/(last["generation_id"]+".recipe.gz"), raw=True)[0]
        recipe = json.loads(gzip.decompress(recipe_wire))
        require(recipe["members"]["projection.sqlite"]["dependency_depth"] == 32, "NATIVE_ACTUAL_DEPTH_32_NOT_REACHED")
        protected = [live, live.with_name(live.name+".authority"), archive]
        before = {str(p): tree(p, hashes=True) for p in protected}
        target = live/("gen-"+last["generation_id"])
        target_originals = {p.name: capture(p, raw=True)[0] for p in paths_at(target)}
        decoded, instances, encoder_attempts = [], [], []
        actual_component, actual_decode = components.ComponentArchive, components.decode_page_pack
        class ObservedArchive(actual_component):
            def __init__(self, *a):
                super().__init__(*a); instances.append(self)
        def decode(*a, **kw):
            obj = instances[-1]
            require(not obj.projections and sum(len(raw) for raw, _ in obj.packs.values()) <= components.MAX_PACK_BYTES,
                    "NATIVE_RESTORE_PERSISTENT_IMAGE_OR_PACK_CACHE")
            frame, nesting = sys._getframe(), 0
            while frame:
                nesting += frame.f_code is actual_component._projection.__code__; frame = frame.f_back
            require(nesting == 1, "NATIVE_RESTORE_RECURSIVE_IMAGE_STACK")
            value = actual_decode(*a, **kw)
            decoded.append({"previous_depth": kw["previous_depth"], "actual_reconstructed_bytes": len(value),
                            "actual_reconstructed_sha256": hashlib.sha256(value).hexdigest()})
            peak()
            return value
        def no_encoder(*a, **kw):
            encoder_attempts.append("CALLED")
            raise AssertionError("NATIVE_PUBLIC_RESTORE_ENCODER_FORBIDDEN")
        components.ComponentArchive, components.decode_page_pack = ObservedArchive, decode
        actual_encoders = gzip.compress, zlib.compress, zlib.compressobj
        gzip.compress = zlib.compress = zlib.compressobj = no_encoder
        try:
            restored = archiver.restore_generation(last["generation_id"])
        finally:
            gzip.compress, zlib.compress, zlib.compressobj = actual_encoders
            components.ComponentArchive, components.decode_page_pack = actual_component, actual_decode
        require([row["previous_depth"] for row in decoded] == [None, *range(32)] and len(decoded) == 33
                and MIN_MEMBER <= decoded[0]["actual_reconstructed_bytes"] <= MAX_MEMBER,
                "NATIVE_33_DECODES_OR_MAXIMUM_ANCESTOR_MISSING")
        require([row["actual_reconstructed_sha256"] for row in decoded] ==
                [row["original_members"]["projection.sqlite"]["sha256"] for row in result["cuts"] if row["archived"]],
                "NATIVE_DECODED_CHAIN_DIFFERS_FROM_ORIGINAL_PUBLISHED_PROJECTIONS")
        require(restored["members"] == target_originals and len(restored["members"]) == 5
                and before == {str(p): tree(p, hashes=True) for p in protected} and not encoder_attempts,
                "NATIVE_PUBLIC_RESTORE_ORIGINAL_BYTES_CUSTODY_OR_ENCODER_FAILED")
        result["restore"] = {"dependency_depth": 32, "decoded": decoded, "encoder_attempts": len(encoder_attempts),
                             "exact_five_original_members": True, "protected_roots_all_stats_and_hashes_unchanged": True,
                             "verification_level": restored["verification_level"],
                             "target_projection_bytes": len(restored["members"]["projection.sqlite"])}
        # A genuine native admission forecast rejects three measured large
        # images; it does not physically publish them or expand the policy.
        control = create_factory("live-denial-control")
        control_cut = publish(control, 1, 1)
        control_before = tree(control.root, hashes=True)
        forecast = 3*result["cuts"][0]["projection_bytes"]
        with control.files as files:
            # The public writer reader verifies its current authority before
            # the public admission API receives the same owned writer lock.
            files.read_writer_generation(checkpoint=False)
            admission = EvidenceRetention(control.root, archive_root=files.archive_root,
                archive_format=files.archive_format, maximum_bytes=files.maximum_bytes,
                maximum_files=files.maximum_files, archive_maximum_bytes=files.archive_maximum_bytes,
                auto_archive=files.archive_root is not None)
            try:
                admission.prepare(additional_bytes=forecast, additional_files=21,
                                  pinned=[control_cut["pointer"]["generation_id"]],
                                  writer_fd=files.lock.fileno())
            except RetentionPressure as error:
                require(error.reason == "RETENTION_HARD_BYTES_CAPACITY_REACHED", "NATIVE_LIVE_DENIAL_WRONG_REASON")
                result["live_three_large_denial"] = {"status": "EXPECTED_RED", "kind": "NATIVE_ADMISSION_FORECAST_ONLY",
                                                     "reason": error.reason, "forecast_projection_bytes": forecast,
                                                     "admission_api": "EvidenceRetention.prepare",
                                                     "metrics": error.metrics}
            else:
                raise AssertionError("NATIVE_LIVE_THREE_LARGE_FORECAST_WAS_ADMITTED")
        require(control_before == tree(control.root, hashes=True), "NATIVE_LIVE_DENIAL_CHANGED_ORIGINALS")
        result["live_three_large_denial"]["root_custody_before"] = control_before
        result["live_three_large_denial"]["root_custody_after"] = tree(control.root, hashes=True)
        result["complete"] = True
    except BaseException as error:
        result["unexpected_error"] = {"class": type(error).__name__, "reason": str(error),
                                      "traceback": traceback.format_exc(limit=12)}
    finally:
        projection.PreparedProjection = actual_projection
        os.fsync = actual_fsync
        sqlite3.connect = actual_sqlite_connect
        tempfile.tempdir = actual_tempdir
        result["elapsed_wall_seconds"] = time.monotonic()-begin
        result["peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
        result["fixture_environments"] = environments
        result["tempfile_requests"] = tempfile_requests
        result["physical_pre_destroy_observations"] = physical_destroy_observations
        result["temporary_storage_contract"] = "ALL_PYTHON_TEMPORARIES_SHARE_THE_CURRENT_CANONICAL_NATIVE512MIB_SCRATCH; NO_AUXILIARY_QUOTA"
        result["restored_projection_storage"] = "RAM_BYTES_AND_IN_MEMORY_SQLITE; ACCOUNTED_BY_REAL_CHILD_RSS"
        result["network_attempts"] = len(network)
        result["source_sqlite_attempts_after_custody_baseline"] = source_sqlite_attempts
        try:
            for database, baseline in databases:
                after = original_database(database)
                result["data_custody"][str(database)] = {"before": baseline, "after": after, "unchanged": baseline == after}
            result["final_scratch_namespace_admissions"] = {
                row["label"]: inspect_scratch(Path(row["scratch"])) for row in environments}
            result["complete_source_bytes_modes_blobs_unchanged"] = source_before == source_inventory(source, index)
            result["raw_git_authority_unchanged"] = git_before == git_authority(args.git_repo, args.source_sha, args.source_tree, index)
            imported, unexpected = [], []
            for name, module in sorted(sys.modules.items()):
                file = getattr(module, "__file__", None)
                if not file:
                    continue
                path = Path(file).absolute()
                if path.is_relative_to(source):
                    rel = str(path.relative_to(source)); actual = capture(path)
                    imported.append({"module": name, "path": rel, "sha256": actual["sha256"],
                                     "matches_frozen_source": actual["sha256"] == index["files"].get(rel)})
                elif name in product_module_names(index) or name.startswith("rc6_"):
                    unexpected.append({"module": name, "path": str(path)})
            result["product_imports"], result["unexpected_product_imports"] = imported, unexpected
            result["elapsed_wall_seconds"] = time.monotonic()-begin
            result["complete"] = bool(result["complete"] and not network and not source_sqlite_attempts and not unexpected
                and result["complete_source_bytes_modes_blobs_unchanged"] and result["raw_git_authority_unchanged"]
                and all(row["unchanged"] for row in result["data_custody"].values())
                and all(row["matches_frozen_source"] for row in imported)
                and result["real_fsync_calls"] > 0
                and result["peak_rss_bytes"] <= MAX_RSS and result["elapsed_wall_seconds"] <= MAX_WALL)
        except BaseException as error:
            result["complete"] = False
            result["final_custody_error"] = {"class": type(error).__name__, "reason": str(error)}
        wire = json.dumps(result, indent=2, sort_keys=True, allow_nan=False).encode()+b"\n"
        fd = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(wire); stream.flush(); os.fsync(stream.fileno())
        print(json.dumps({k: result[k] for k in ("source_sha", "complete", "elapsed_wall_seconds", "peak_rss_bytes")}), flush=True)
        require(time.monotonic()-process_begin <= MAX_WALL, "NATIVE_FINAL_OUTPUT_EXCEEDED_300SECOND_DEADLINE")
        signal.alarm(0)
    return 0 if result["complete"] else 1


def _git(repo, *arguments):
    return subprocess.check_output(
        ["git", "--no-replace-objects", "-c", "protocol.allow=never", "-C", str(repo), *arguments],
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1"}, stderr=subprocess.DEVNULL, timeout=20,
    )


def _literal_head(repo):
    require(not _git(repo, "for-each-ref", "--format=%(refname)", "refs/replace").strip(),
            "GIT_REPLACE_AUTHORITY_FORBIDDEN")
    sha = _git(repo, "rev-parse", "--verify", "HEAD^{commit}").decode("ascii").strip()
    raw = _git(repo, "cat-file", "commit", sha)
    require(hashlib.sha1(b"commit " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == sha,
            "RAW_LITERAL_HEAD_MISMATCH")
    tree_oid = _git(repo, "rev-parse", "--verify", sha + "^{tree}").decode("ascii").strip()
    require(raw.splitlines()[0] == b"tree " + tree_oid.encode("ascii"), "RAW_LITERAL_TREE_MISMATCH")
    return sha, tree_oid, hashlib.sha256(raw).hexdigest()


def _tracked_objects(repo, sha):
    objects = {}
    for record in _git(repo, "ls-tree", "-r", "--full-tree", "-z", sha).split(b"\0"):
        if not record:
            continue
        header, name = record.split(b"\t", 1)
        mode, kind, blob = header.decode("ascii").split()
        relative = name.decode("utf-8")
        path = Path(relative)
        require(kind == "blob" and mode in ("100644", "100755") and not path.is_absolute()
                and path.parts and ".git" not in path.parts and ".." not in path.parts
                and str(path) == relative and relative not in objects, "WHOLE_GIT_TRACKED_MEMBER_INVALID")
        objects[relative] = {"mode": mode, "blob_id": blob}
    require(bool(objects) and len(objects) < 32768, "WHOLE_GIT_TRACKED_MEMBER_BOUND")
    return objects


def _checkout_inventory(repo, objects):
    inventory = {}
    for name, item in objects.items():
        actual = capture(safe_path(repo / name))
        mode = "100" + format(stat.S_IMODE(actual["st_mode"]), "03o")
        require(mode == item["mode"] and actual["blob_id"] == item["blob_id"],
                "GOVERNED_CHECKOUT_TRACKED_BYTES_OR_MODE_DRIFT")
        inventory[name] = {"mode": mode, "blob_id": actual["blob_id"], "sha256": actual["sha256"]}
    return inventory


def _publish_json(path, value):
    raw = json.dumps(value, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(raw).hexdigest()


def validate_completed_child_resources(resources):
    """Require the actual kernel envelope, including output and process exit.

    The330-second outer limit is exclusively for termination/diagnosis. A
    child whose own JSON says300 or less cannot pass a kernel envelope over300.
    This predicate is applied to counters obtained directly from wait4.
    """
    require(type(resources.get("returncode")) is int and resources["returncode"] == 0
            and resources.get("outer_timed_out") is False, "NATIVE_KERNEL_EXIT_NOT_SUCCESSFUL")
    elapsed, rss = resources.get("elapsed_wall_seconds"), resources.get("real_peak_rss_bytes")
    require(type(elapsed) in (int, float) and math.isfinite(elapsed) and 0 < elapsed <= MAX_WALL,
            "NATIVE_KERNEL_FULL_ENVELOPE_EXCEEDS_300SECONDS")
    require(type(rss) is int and 0 < rss <= MAX_RSS, "NATIVE_KERNEL_REAL_RSS_LIMIT_FAILED")


def capture_complete_checkout(repo, output):
    """Snapshot every committed blob, preserving exact modes and zero overlays.

    No git-archive export attributes, hand-written file allowlist, source symlink
    or untracked Python module can alter the fresh source used by the child.
    """
    repo, output = safe_path(repo), safe_path(output, absent=True)
    require(not output.is_relative_to(repo) and not repo.is_relative_to(output),
            "FROZEN_WHOLE_SOURCE_MUST_BE_EXTERNAL")
    sha, tree_oid, raw_commit_sha256 = _literal_head(repo)
    objects = _tracked_objects(repo, sha)
    checkout_before = _checkout_inventory(repo, objects)
    relative_probe = "scripts/rc6_archive_maximum_member_probe.py"
    require(relative_probe in checkout_before and capture(Path(__file__))["sha256"] ==
            checkout_before[relative_probe]["sha256"], "CURRENT_PROBE_NOT_IN_LITERAL_COMMITTED_HEAD")
    output.mkdir(mode=0o700)
    source = output / "source"
    source.mkdir(mode=0o700)
    batch = subprocess.Popen(["git", "--no-replace-objects", "-c", "protocol.allow=never", "-C", str(repo), "cat-file", "--batch"],
                             env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, start_new_session=True)
    total = 0
    try:
        for relative, item in objects.items():
            batch.stdin.write(item["blob_id"].encode("ascii") + b"\n")
            batch.stdin.flush()
            header = batch.stdout.readline(256).decode("ascii").strip().split()
            require(len(header) == 3 and header[:2] == [item["blob_id"], "blob"],
                    "RAW_GIT_BATCH_BLOB_HEADER_INVALID")
            size = int(header[2])
            require(0 <= size <= 128*MIB, "COMPLETE_CHECKOUT_SOURCE_MEMBER_BOUND")
            space = os.statvfs(output)
            require(space.f_bavail*space.f_frsize >= size+2*1024**3 and space.f_files > 0
                    and space.f_favail*100 >= space.f_files*10,
                    "COMPLETE_CHECKOUT_COPY_SHARED_FILESYSTEM_RESERVE_REQUIRED")
            destination = source / relative
            destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                remaining = size
                while remaining:
                    chunk = batch.stdout.read(min(MIB, remaining))
                    require(bool(chunk), "RAW_GIT_BATCH_BLOB_TRUNCATED")
                    stream.write(chunk)
                    remaining -= len(chunk)
                os.fchmod(stream.fileno(), 0o755 if item["mode"] == "100755" else 0o644)
            require(batch.stdout.read(1) == b"\n", "RAW_GIT_BATCH_MEMBER_DELIMITER_INVALID")
            total += size
        batch.stdin.close()
        require(batch.wait(timeout=20) == 0, "RAW_GIT_BATCH_FAILED")
    finally:
        if batch.poll() is None:
            batch.kill()
            batch.wait(timeout=3)
        for stream in (batch.stdin, batch.stdout):
            if stream is not None and not stream.closed:
                stream.close()
    index = {
        "schema": "rc6.complete-archive-source-pin.v1", "scope": "ALL_LITERAL_HEAD_GIT_BLOBS_AND_MODES",
        "source_sha": sha, "source_tree": tree_oid, "raw_git_commit_sha256": raw_commit_sha256,
        "files": {name: row["sha256"] for name, row in checkout_before.items()},
        "modes": {name: row["mode"] for name, row in checkout_before.items()},
        "blob_ids": {name: row["blob_id"] for name, row in checkout_before.items()}, "overlay_count": 0,
        "original_git_blob_bytes": total,
    }
    _publish_json(output / "source.index.json", index)
    require(checkout_before == _checkout_inventory(repo, objects)
            and _literal_head(repo) == (sha, tree_oid, raw_commit_sha256),
            "GOVERNED_CHECKOUT_CHANGED_DURING_WHOLE_CAPTURE")
    require(source_inventory(source, index) == checkout_before, "WHOLE_CAPTURE_SOURCE_DIFFERS_FROM_CHECKOUT")
    return {"source_root": source, "source_index": output / "source.index.json", "index": index,
            "checkout_before": checkout_before, "objects": objects, "head": (sha, tree_oid, raw_commit_sha256)}


def _execute_governed_witness(repo_root, output_root):
    """Launch one fresh native process for the two automatic governed guards.

    The function always assembles a new whole source and starts a child. It has
    no receipt-only mode and cannot consume a previous passing witness.
    """
    require(sys.platform == "linux" and sys.version_info[:2] in ((3, 11), (3, 12))
            and os.getuid() == os.geteuid() and all(hasattr(os, key) for key in
                ("wait4", "waitstatus_to_exitcode", "killpg")), "NATIVE_GUARD_EXECUTION_PLATFORM_REQUIRED")
    require(len(list(importlib.metadata.distributions())) == 157, "APPROVED157_INTERPRETER_CLOSURE_REQUIRED")
    repo, output = safe_path(repo_root), safe_path(output_root, absent=True)
    require(not output.is_relative_to(repo) and not repo.is_relative_to(output), "NATIVE_GUARD_OUTPUT_MUST_BE_EXTERNAL")
    output.mkdir(mode=0o700)
    pin = capture_complete_checkout(repo, output / "frozen")
    raw = output / "raw"
    raw.mkdir(mode=0o700)
    native_root = output / "native-data"
    require(not native_root.exists(), "PRIOR_NATIVE_DATA_NOT_ALLOWED")
    execution_id = uuid.uuid4().hex
    source_sha, source_tree, _ = pin["head"]
    command = [sys.executable, "-I", "-B", str(pin["source_root"] / "scripts/rc6_archive_maximum_member_probe.py"),
               "--source-root", str(pin["source_root"]), "--git-repo", str(repo),
               "--source-index", str(pin["source_index"]), "--source-sha", source_sha, "--source-tree", source_tree,
               "--root", str(native_root), "--receipt", str(raw / "native-receipt.json"),
               "--execution-id", execution_id]
    before = {"schema": "rc6.maximum-member-governed-native-launch.v1", "execution_id": execution_id,
              "source_sha": source_sha, "source_tree": source_tree, "source_files": len(pin["index"]["files"]),
              "source_index_sha256": capture(pin["source_index"])["sha256"],
              "probe_source_sha256": pin["index"]["files"]["scripts/rc6_archive_maximum_member_probe.py"],
              "command": command, "cwd": str(pin["source_root"]), "uid": os.geteuid(),
              "native_executions": 1, "guards_in_shared_native_execution": 2, "outer_timeout_seconds": 330,
              "prior_native_data_present": False, "native_child_started": False,
              "frozen_source_physical_residence": residence(pin["source_root"]),
              "frozen_source_original_git_blob_bytes": pin["index"]["original_git_blob_bytes"],
              "started_at": datetime.now(timezone.utc).isoformat()}
    _publish_json(raw / "launch-before.json", before)
    process = None
    started = time.monotonic()
    timed_out = False
    try:
        with (raw / "native.log").open("xb") as log:
            process = subprocess.Popen(command, cwd=pin["source_root"],
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, stdout=log,
                stderr=subprocess.STDOUT, start_new_session=True)
            term_at = kill_at = None
            while True:
                waited, status, usage = os.wait4(process.pid, os.WNOHANG)
                if waited:
                    require(waited == process.pid, "WRONG_NATIVE_CHILD_REAPED")
                    exit_code = os.waitstatus_to_exitcode(status)
                    process.returncode = exit_code
                    break
                now = time.monotonic()
                if now >= started + 330 and term_at is None:
                    timed_out = True
                    os.killpg(process.pid, signal.SIGTERM)
                    term_at = now
                elif term_at is not None and now >= term_at + 3 and kill_at is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    kill_at = now
                elif kill_at is not None and now >= kill_at + 3:
                    raise TimeoutError("PRIVATE_NATIVE_CHILD_NOT_REAPED_AFTER_SIGKILL")
                time.sleep(0.1)
        resources = {"schema": "rc6.maximum-member-governed-linux-wait4.v1", "execution_id": execution_id,
                     "pid": process.pid, "returncode": exit_code, "outer_timed_out": timed_out,
                     "elapsed_wall_seconds": time.monotonic()-started,
                     "elapsed_wall_source": "PARENT_MONOTONIC_LAUNCH_THROUGH_EXACT_PID_KERNEL_REAP; INCLUDES_OUTPUT_EXIT_AND_POLL_LATENCY",
                     "cpu_user_seconds": usage.ru_utime, "cpu_system_seconds": usage.ru_stime,
                     "cpu_total_seconds": usage.ru_utime+usage.ru_stime,
                     "real_peak_rss_bytes": usage.ru_maxrss*1024, "ru_maxrss_unit": "LINUX_KIBIBYTES",
                     "minor_page_faults": usage.ru_minflt, "major_page_faults": usage.ru_majflt,
                     "input_blocks": usage.ru_inblock, "output_blocks": usage.ru_oublock,
                     "scope": "EXACT_CHILD_PID_AND_KERNEL_ACCOUNTED_REAPED_DESCENDANTS; HIGH_WATER_RSS_NOT_SUM_OF_PROCESSES"}
        _publish_json(raw / "child-resource.json", resources)
        require(not timed_out and exit_code == 0, "NATIVE_WITNESS_FAILED; raw=" + str(raw))
        validate_completed_child_resources(resources)
        report_wire, _ = capture(raw / "native-receipt.json", raw=True, maximum=2*MIB)
        report = json.loads(report_wire)
        require(report.get("schema") == "rc6.native-maximum-archive-member-memory-witness.v2"
                and report.get("complete") is True and report.get("execution_id") == execution_id
                and report.get("native_pid") == process.pid and report.get("source_sha") == source_sha
                and report.get("source_tree") == source_tree and report.get("uid") == os.geteuid()
                and report.get("probe_source_sha256") == before["probe_source_sha256"]
                and report.get("source_index_sha256") == before["source_index_sha256"]
                and type(report.get("native_executions")) is int and report["native_executions"] == 1
                and type(report.get("guards_in_shared_native_execution")) is int
                and report["guards_in_shared_native_execution"] == 2,
                "FRESH_ACTUAL_NATIVE_CHILD_RECEIPT_BINDING_FAILED")
        require(report["peak_rss_bytes"] == resources["real_peak_rss_bytes"] <= MAX_RSS
                and report["elapsed_wall_seconds"] <= MAX_WALL and resources["elapsed_wall_seconds"] <= MAX_WALL,
                "NATIVE_CHILD_ACTUAL_RESOURCE_LIMIT_FAILED")
        require(_literal_head(repo) == pin["head"]
                and _checkout_inventory(repo, pin["objects"]) == pin["checkout_before"],
                "GOVERNED_CHECKOUT_CHANGED_DURING_NATIVE_CHILD")
        final = {**before, "native_child_started": True, "native_pid": process.pid, "returncode": exit_code,
                 "outer_timed_out": timed_out, "whole_checkout_bytes_modes_blobs_unchanged": True,
                 "native_receipt_sha256": hashlib.sha256(report_wire).hexdigest(),
                 "resource_receipt_sha256": capture(raw / "child-resource.json")["sha256"],
                 "log_sha256": capture(raw / "native.log", maximum=64*1024)["sha256"],
                 "external_output_physical_residence_after_execution": residence(output),
                 "physical_accounting_scope": "FROZEN_COMPLETE_SOURCE_PLUS_RAW_REPORTS_PLUS_ALL_PRIVATE_NATIVE_DATASETS; NO_AUXILIARY_RUNTIME_BUDGET"}
        _publish_json(raw / "launch-final.json", final)
        return {"report": report, "resources": resources, "launch": final, "raw_root": raw,
                "output_root": output, "execution_id": execution_id}
    except BaseException as error:
        if process is not None and process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
                waited, status, _ = os.wait4(process.pid, 0)
                process.returncode = os.waitstatus_to_exitcode(status)
            except (ProcessLookupError, ChildProcessError):
                pass
        _publish_json(raw / "launch-error.json", {**before,
            "native_child_started": process is not None, "native_pid": process.pid if process is not None else None,
            "elapsed_wall_seconds": time.monotonic()-started, "exception_class": type(error).__name__,
            "reason": str(error), "raw_root": str(raw)})
        raise


def execute_governed_witness(repo_root, output_root):
    """Run the fresh shared native witness while preserving the caller umask."""
    previous_umask = os.umask(0o022)
    try:
        return _execute_governed_witness(repo_root, output_root)
    finally:
        os.umask(previous_umask)


if __name__ == "__main__":
    raise SystemExit(main())
