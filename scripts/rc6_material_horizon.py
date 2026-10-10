"""Source-indexed RC6 horizon carrier; original ac9/cd39 stay byte-exact.

The native producer is a distinct indexed __main__ script with actual stdlib
finalization before final Source captures. The supervisor uses the reviewed
native manager, original21600/TERM2/cleanup5 and original native30s/1202 calls.
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import threading
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import subprocess
import sys
import time
import traceback

STATS = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size",
         "st_blocks", "st_atime_ns", "st_mtime_ns", "st_ctime_ns")
MEMBERS = {"report.json.gz", "checkpoint.json.gz", "status.json", "manifest.json", "projection.sqlite"}
PROFILE = "scripts/rc6_material_horizon_producer.py"
DRIVER = "scripts/rc6_material_horizon.py"
HEX40, HEX64 = re.compile(r"[0-9a-f]{40}\Z"), re.compile(r"[0-9a-f]{64}\Z")


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def parse(wire):
    return json.loads(wire, object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("NONFINITE_JSON")))


def attributes(info):
    return {name: getattr(info, name) for name in STATS}


def capture(path, *, keep_bytes=False):
    """Single-link regular bytes plus all11 physical stats; NOATIME reads."""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("REGULAR_SINGLELINK_FILE_REQUIRED:"+str(path))
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK)
    sha, blob, chunks, size = hashlib.sha256(), hashlib.sha1(
        b"blob "+str(before.st_size).encode()+b"\0"), [], 0
    try:
        if attributes(os.fstat(descriptor)) != attributes(before):
            raise ValueError("FILE_CHANGED_BEFORE_CAPTURE")
        while block := os.read(descriptor, 1024*1024):
            sha.update(block); blob.update(block); size += len(block)
            if keep_bytes:
                chunks.append(block)
        if (size != before.st_size or attributes(os.fstat(descriptor)) != attributes(before)
                or attributes(path.lstat()) != attributes(before)):
            raise ValueError("FILE_CHANGED_DURING_CAPTURE")
    finally:
        os.close(descriptor)
    return {**attributes(before), "sha256": sha.hexdigest(), "git_blob": blob.hexdigest()}, b"".join(chunks)


def directory_names(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK)
    try:
        before = attributes(os.fstat(descriptor))
        names = os.listdir(descriptor)
        if len(names) > 32768 or before != attributes(os.fstat(descriptor)) or before != attributes(path.lstat()):
            raise ValueError("DIRECTORY_CHANGED_OR_UNBOUNDED")
        return names
    finally:
        os.close(descriptor)


def canonical(path, *, existing):
    path = Path(path).absolute()
    if path.resolve(strict=existing) != path or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("CANONICAL_UNALIASED_PATH_REQUIRED:"+str(path))
    return path


def git(repository, *arguments, payload=None):
    return subprocess.check_output(["git", "--no-replace-objects", "-C", str(repository), *arguments],
        input=payload, env={**os.environ, "GIT_NO_REPLACE_OBJECTS":"1", "GIT_NO_LAZY_FETCH":"1", "GIT_OPTIONAL_LOCKS":"0"})


def raw_objects(repository, identifiers, kind):
    identifiers = list(dict.fromkeys(identifiers))
    if not identifiers or any(not HEX40.fullmatch(value) for value in identifiers):
        raise ValueError("RAW_GIT_IDENTIFIERS_INVALID")
    wire = git(repository, "cat-file", "--batch", payload=("\n".join(identifiers)+"\n").encode())
    offset, result = 0, {}
    for identifier in identifiers:
        end = wire.find(b"\n", offset)
        row = wire[offset:end].split()
        if end < offset or len(row) != 3 or row[:2] != [identifier.encode(), kind.encode()]:
            raise ValueError("RAW_GIT_OBJECT_HEADER_INVALID")
        size = int(row[2]); offset = end+1
        body = wire[offset:offset+size]; offset += size
        if size < 0 or len(body) != size or wire[offset:offset+1] != b"\n":
            raise ValueError("RAW_GIT_OBJECT_SIZE_INVALID")
        offset += 1
        if hashlib.sha1(kind.encode()+b" "+str(size).encode()+b"\0"+body).hexdigest() != identifier:
            raise ValueError("RAW_GIT_OBJECT_HASH_INVALID")
        result[identifier] = body
    if offset != len(wire):
        raise ValueError("RAW_GIT_EXTRA_BYTES")
    return result


def inventory(source):
    files, directories, queue = {}, set(), [source]
    while queue:
        parent = queue.pop()
        for name in directory_names(parent):
            path = parent/name; info = path.lstat(); relative = path.relative_to(source).as_posix()
            if stat.S_ISDIR(info.st_mode):
                if stat.S_IMODE(info.st_mode) != 0o755:
                    raise ValueError("SOURCE_DIRECTORY_MODE_INVALID")
                directories.add(relative); queue.append(path)
            else:
                snapshot, _ = capture(path)
                mode = stat.S_IMODE(info.st_mode)
                if mode not in (0o644, 0o755):
                    raise ValueError("SOURCE_FILE_MODE_INVALID")
                files[relative] = {"sha256":snapshot["sha256"], "mode":"100"+format(mode,"03o"), "blob_id":snapshot["git_blob"]}
    implicit = {p.as_posix() for name in files for p in PurePosixPath(name).parents if p.as_posix() != "."}
    if directories != implicit:
        raise ValueError("SOURCE_DIRECTORY_NAMESPACE_NOT_EXACT")
    return files


def verify_source(repository, source, sha, tree, pin):
    if (not HEX40.fullmatch(sha) or not HEX40.fullmatch(tree)
            or pin.get("schema") != "rc6.complete-archive-source-pin.v1"
            or pin.get("source_sha") != sha or pin.get("source_tree") != tree
            or type(pin.get("overlay_count")) is not int or pin["overlay_count"] != 0
            or any(not isinstance(pin.get(key), dict) for key in ("files","modes","blob_ids"))):
        raise ValueError("COMPLETE_SOURCE_PIN_REQUIRED")
    if git(repository,"for-each-ref","--format=%(refname)","refs/replace").strip():
        raise ValueError("GIT_REPLACE_REFERENCES_FORBIDDEN")
    commit = raw_objects(repository, [sha], "commit")[sha]
    if not commit.startswith(b"tree "+tree.encode()+b"\n") or hashlib.sha256(commit).hexdigest() != pin.get("raw_git_commit_sha256"):
        raise ValueError("RAW_GIT_COMMIT_TREE_MISMATCH")
    expected, trees = {}, [tree]
    for row in git(repository,"ls-tree","-r","-t","-z","--full-tree",sha).split(b"\0"):
        if not row:
            continue
        metadata, encoded = row.split(b"\t",1)
        mode, kind, identifier = metadata.decode().split(); name = encoded.decode(); path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or path.as_posix() != name or not HEX40.fullmatch(identifier):
            raise ValueError("RAW_GIT_PATH_INVALID")
        if kind == "tree" and mode == "040000":
            trees.append(identifier)
        elif kind == "blob" and mode in ("100644","100755") and name not in expected:
            expected[name] = {"mode":mode,"blob_id":identifier}
        else:
            raise ValueError("RAW_GIT_MEMBER_INVALID")
    raw_objects(repository, trees, "tree")
    actual = inventory(source)
    if (set(actual) != set(expected) or set(actual) != set(pin["files"])
            or set(actual) != set(pin["modes"]) or set(actual) != set(pin["blob_ids"])
            or any(row != {"sha256":pin["files"][name],"mode":pin["modes"][name],"blob_id":pin["blob_ids"][name]}
                   or row["mode"] != expected[name]["mode"] or row["blob_id"] != expected[name]["blob_id"] for name,row in actual.items())):
        raise ValueError("WHOLE_SOURCE_HASH_MODE_BLOB_OR_NAMESPACE_MISMATCH")
    return actual, {"source_sha":sha,"source_tree":tree,"source_files":len(actual),"tree_objects_rehashed":len(set(trees)),
                    "raw_commit_sha256":hashlib.sha256(commit).hexdigest(),"scope":"RAW_GIT_ALL_TREES_BLOBS_AND_COMPLETE_PHYSICAL_NAMESPACE"}


def normalized(value):
    return re.sub(r"[-_.]+","-",value).lower()


def lock_rows(wire):
    result, current = {}, None
    for line in wire.decode().splitlines():
        text = line.strip().removesuffix("\\").strip()
        if not text or text.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)",text)
        if match:
            name = normalized(match[1])
            if name in result:
                raise ValueError("DUPLICATE_HASH_LOCK_NAME")
            result[name] = {"version":match[2],"hashes":set()}; current = name
        elif current is not None and re.fullmatch(r"--hash=sha256:[0-9a-f]{64}",text):
            result[current]["hashes"].add(text.removeprefix("--hash=sha256:"))
        else:
            raise ValueError("UNSUPPORTED_HASH_LOCK_RECORD")
    return result


PREFLIGHT = r'''
import importlib.metadata, json, platform, sys, sysconfig
rows = [{"name":d.metadata["Name"],"version":d.version} for d in importlib.metadata.distributions()]
print(json.dumps({"python":platform.python_version(),"executable":sys.executable,"prefix":sys.prefix,"base_prefix":sys.base_prefix,
 "os":platform.system(),"architecture":platform.machine(),"libc":platform.libc_ver(),"distributions":rows,
 "stdlib":sysconfig.get_path("stdlib"),"venv_sites":[sysconfig.get_path(k) for k in ("purelib","platlib")]}))
'''


def preflight(interpreter, version, source, environment):
    wire = subprocess.check_output([str(interpreter),"-I","-B","-c",PREFLIGHT], env=environment, timeout=30)
    actual = parse(wire)
    policy = parse(capture(source/"ops/policy/rc6-supply-chain-v1.json",keep_bytes=True)[1])
    expected, installed, locks = {}, {}, {}
    for key, name in (("packages","requirements.lock.txt"),("build_tools","requirements.build.lock.txt")):
        snapshot, body = capture(source/name,keep_bytes=True); rows = lock_rows(body)
        package_rows = policy[key]
        canonical_rows = {normalized(row["name"]):row for row in package_rows}
        if len(canonical_rows) != len(package_rows) or set(rows) != set(canonical_rows):
            raise ValueError("POLICY_AND_HASH_LOCK_NAMES_MISMATCH")
        for distribution, row in rows.items():
            allowed = {item["sha256"] for item in canonical_rows[distribution]["distributions"]}
            if (distribution in expected or row["version"] != canonical_rows[distribution]["version"]
                    or not row["hashes"] or row["hashes"] != allowed):
                raise ValueError("POLICY_AND_HASH_LOCK_VERSIONS_OR_HASHES_MISMATCH")
            expected[distribution] = row["version"]
        locks[name] = {"sha256":snapshot["sha256"],"pins":len(rows)}
    for row in actual["distributions"]:
        name = normalized(row["name"])
        if name in installed:
            raise ValueError("DUPLICATE_INSTALLED_DISTRIBUTION")
        installed[name] = row["version"]
    if (actual["python"] != version or version not in ("3.11.16","3.12.14")
            or actual["os"] != "Linux" or actual["architecture"] != "x86_64"
            or actual["libc"][0] != "glibc" or tuple(map(int,actual["libc"][1].split("."))) < (2,36)
            or actual["prefix"] == actual["base_prefix"] or len(installed) != len(expected) or len(expected) != 157
            or installed != expected or policy.get("schema") != "rc6.hashed-distribution-lock.v1"):
        raise ValueError("EXACT_FROZEN157_PLATFORM_ENVIRONMENT_REQUIRED")
    return {**actual,"expected_total":157,"installed_total":len(actual["distributions"]),"installed_unique_total":len(installed),
            "locks":locks,"policy_sha256":capture(source/"ops/policy/rc6-supply-chain-v1.json")[0]["sha256"],
            "scope":"INSTALLED_METADATA_VERSIONS_AND_HASH_LOCKS_NOT_DISTRIBUTION_BYTES_OR_IMAGE"}


def database_snapshot(database):
    result = {}
    for suffix in ("","-wal","-shm","-journal"):
        path = Path(str(database)+suffix)
        try:
            snapshot, _ = capture(path)
        except FileNotFoundError:
            if not suffix:
                raise
            result[suffix] = None
        else:
            result[suffix] = {key:value for key,value in snapshot.items() if key != "git_blob"}
    return result


def positive(value):
    return type(value) is int and value > 0


def inspect_native(native, source, pin, qualification, database, errors):
    def require(condition, name):
        if not condition:
            errors.append(name)
    require(isinstance(native,dict) and native.get("schema") == "rc6.native-archive-v3-profile.v2","NATIVE_SCHEMA")
    require(native.get("source_sha") == pin["source_sha"] and native.get("source_tree") == pin["source_tree"],"NATIVE_SOURCE_PIN")
    require(type(native.get("overlay_count")) is int and native["overlay_count"] == 0,"NATIVE_OVERLAY")
    for name in ("execution_complete","native_horizon_contract_verified","horizon_complete","complete","acceptance_complete",
                 "code_source_unchanged","source_database_unchanged","import_graph_verified","source_index_and_tar_unchanged"):
        require(native.get(name) is True,"NATIVE_FLAG:"+name)
    require(not any(name in native for name in ("error","finalization_error")),"NATIVE_ERRORS")
    require(native.get("ticks_requested") == 1201 and native.get("native_ticks_executed") == 1202,"ACTUAL1202_NATIVE_CALLS")
    cuts = native.get("cuts",[])
    require(len(cuts) == 1202 and len({cut["generation_id"] for cut in cuts}) == 1202,"1202_DISTINCT_CUTS")
    if len(cuts) != 1202:
        return
    clocks = [datetime.fromisoformat(cut["as_of"]) for cut in cuts]
    require(all(clock.utcoffset() is not None for clock in clocks) and clocks[0] < clocks[1],"NATIVE_AWARE_PRE_AND_POSTPRE_CLOCKS")
    require(all((b-a).total_seconds() == 30 for a,b in zip(clocks[1:],clocks[2:]))
        and (clocks[-1]-clocks[1]).total_seconds() == 36000 and native.get("measured_horizon_seconds") == 36000,"TENH_ASOF_NOT_WALLCLOCK")
    require(native.get("restarts") == [361,841] and all(cuts[i]["checkpoint_reused"] is True for i in (361,841)),"REAL_FACTORY_RESTART_CHECKPOINT_REUSE")
    require(all(type(cut["full_cycle_wall_seconds"]) in (int,float) and math.isfinite(cut["full_cycle_wall_seconds"])
        and 0 <= cut["full_cycle_wall_seconds"] <= 30 for cut in cuts[1:]),"EXISTING30S_POSTPRE_FULL_CYCLE")
    for index,cut in enumerate(cuts):
        require(cut["tick_index"] == index and cut["preopen_seed"] is (index == 0),"CUT_INDEX_AND_SEED")
        require(isinstance(cut["phase"],str) and bool(cut["phase"]),"ACTUAL_NATIVE_PHASE_NOT_FORCED_OPEN")
        require(set(cut["member_bytes"]) == set(cut["original_member_sha256"]) == MEMBERS,"EXACT_FIVE_BYTE_MEMBERS")
        require(all(type(size) is int and size >= 0 for size in cut["member_bytes"].values())
            and all(HEX64.fullmatch(value) for value in cut["original_member_sha256"].values()),"MEMBER_BYTES_AND_HASHES")
        require(cut["original_manifest_sha256"] == cut["original_member_sha256"]["manifest.json"]
            and cut["original_manifest_clock"] == cut["as_of"] and cut["original_generation_custody_unchanged"] is True,"ORIGINAL_MANIFEST_AND_CUSTODY")
        receipt = cut["native_receipt"]
        require(receipt["generation_id"] == cut["generation_id"] and receipt["manifest_sha256"] == cut["original_manifest_sha256"]
            and receipt["source_as_of"] == cut["as_of"],"NATIVE_RECEIPT_BINDING")
        require(cut["catalog_ready_count"] == 1200 and cut["provider_requests"] == cut["real_orders_sent"] == 0
            and cut["real_routes"] == "NOT_CALLED","CUT_CARDINALITY_AND_NO_AUTHORITY")
    reads = native.get("native_input_reads",[])
    require(len(reads) == 1202,"ACTUAL_NATIVE_SOURCE_READS")
    for cut,clock,read in zip(cuts,clocks,reads,strict=True):
        require(read["as_of"] == cut["as_of"],"SOURCE_READ_CLOCK_MATCHES_ACTUAL_NATIVE_CUT")
        after_stale = (clock-clocks[1]).total_seconds() > 120
        require(read["fixed_source_contract_must_be_stale"] is after_stale and (not after_stale or read["intraday_confirmed_observations"] == 0)
            and read["entry_authorized_observations"] == 0 and read["source_database_effect"] == "READ_ONLY","FIXED_SOURCE_STALE_AND_NO_DATA_REWRITE")
    require(native.get("archive_restore_count") == 1202 and native.get("first_restored_tick_index") == 1
        and native.get("first_original_public_restore_completed") is True,"EVERY_RESTORE_AND_FINAL_FIRST_OPERATIONAL_RESTORE")
    require(native.get("live_maximum_files") == 512 and native.get("live_maximum_bytes") == 128*1024**2
        and native.get("archive_maximum_files") == 32768 and native.get("archive_maximum_bytes") == 512*1024**2,"ORIGINAL_NATIVE_NAMESPACE_AND_QUOTAS")
    for name,byte_limit,file_limit in (("live",128*1024**2,512),("archive",512*1024**2,32768)):
        peak = native["peak_"+name]
        require(max(peak["logical_bytes_including_directories"],peak["allocated_bytes_including_directories"]) <= byte_limit
            and peak["entries_excluding_root"] <= file_limit,"TEMPORARY_PEAK:"+name)
    reserve = native["filesystem_reserve_observation"]
    require(reserve["reserve_bytes"] == 2*1024**3 and reserve["minimum_free_inode_percent"] == 10
        and reserve["minimum_observed_free_bytes"] >= 2*1024**3 and reserve["minimum_observed_free_inode_percent"] >= 10,"ORIGINAL_FILESYSTEM_RESERVE")
    require(native["peak_scratch_native_policy_occupied_bytes"] <= 512*1024**2 and native["peak_rss_bytes"] <= 2*1024**3
        and positive(native["native_scratch_measure_calls"]) and positive(native["real_fsync_calls"]),"NATIVE_SCRATCH_RSS_AND_REAL_DURABILITY")
    observations = native["native_retention_observations"]
    require(observations["additional_gc_rotation_or_pin_requests"] == 0
        and positive(observations["native_ack_compactions"]) and positive(observations["native_rotations"]),"ACTUAL_MAINTENANCE_NO_ADDITIONAL_CALLS")
    require(all(type(row[key]) is int and row[key] >= 0 for row in observations["counters"].values() for key in ("entered","completed","failed"))
        and all(row["entered"] == row["completed"] and row["failed"] == 0 for row in observations["counters"].values()),"MAINTENANCE_COUNTER_CLOSURE")
    head = native["verified_final_archive_head"]
    require(head["contracted_horizon_seconds"] == 32400 and head["recovery_margin_seconds"] == 3600
        and head["source_as_of_max"] == cuts[-1]["as_of"],"VERIFIED_NATIVE_HEAD_WHEEL_PLUS_RECOVERY")
    require(native.get("final_as_of") == cuts[-1]["as_of"]
        and type(native.get("final_sequence")) is int and native["final_sequence"] == cuts[-1]["sequence"],
        "FINAL_CURRENT_CLOCK_AND_SEQUENCE_MATCH_LAST_NATIVE_CUT")
    guard = native["private_execution_guard"]
    require(guard["network_attempt_count"] == guard["sqlite_rejected_count"] == native["provider_requests"] == 0
        and guard["primary_sqlite_sealed_after_fixture"] is True,"PRIVATE_SQLITE_AND_PYTHON_TRANSPORT_BARRIER")
    require(native.get("source_database") == str(database) and native["source_database_changes"] == [],"SOURCE_DB_BINDING")
    require(native["source_database_before"] == native["source_database_after"],"NATIVE_ALL11_DB_BEFORE_AFTER")
    physical = database_snapshot(database)
    require(physical == native["source_database_before"],"DRIVER_POSTREAP11_STATS_AND_SHA_MATCH_NATIVE_BASELINE")
    for row in physical.values():
        if row is not None:
            require(set(row) == set(STATS)|{"sha256"},"EXACT11_STATS_SHA_NOT_COUNTING_HASH_AS_STAT")
    for key in ("import_graph_before_fixtures","import_graph_after"):
        graph = native[key]
        require(graph["unexpected"] == [] and Path(graph["stdlib"]).resolve() == Path(qualification["stdlib"]).resolve(),"NATIVE_CAPTURED_IMPORT_GRAPH")
        sites = {Path(value).resolve() for value in qualification["venv_sites"]}
        require({Path(value).resolve() for value in graph["actual_venv_sites"]} == sites,"ACTUAL_VENV_IMPORT_ROOTS")
        for origins in graph["modules"].values():
            for row in origins:
                path = Path(row["path"])
                if row["origin"] == "PINNED_COMPLETE_SOURCE":
                    require(path.is_relative_to(source),"PINNED_PROJECT_IMPORT_PATH")
                    name = path.relative_to(source).as_posix()
                    if row.get("sha256") is not None:
                        require(row["sha256"] == pin["files"].get(name) and row["blob_id"] == pin["blob_ids"].get(name)
                            and row["mode"] == pin["modes"].get(name),"PINNED_PROJECT_IMPORT_BYTES")
                elif row["origin"] == "ACTUAL_VENV":
                    require(any(path.is_relative_to(site) for site in sites),"VENV_IMPORT_PATH")
                elif row["origin"] == "STDLIB":
                    require(path.is_relative_to(Path(qualification["stdlib"]).resolve()) and "site-packages" not in path.parts,"STDLIB_IMPORT_PATH")
                else:
                    require(False,"UNRECOGNIZED_IMPORT_ORIGIN")


def write_json(path, value):
    path.write_text(json.dumps(value,sort_keys=True,indent=2)+"\n")


def reap(child):
    pid, status, usage = os.wait4(child.pid,os.WNOHANG)
    if not pid:
        return None
    child.returncode = os.waitstatus_to_exitcode(status)
    return {"pid":pid,"wait_status":status,"returncode":child.returncode,"user_cpu_seconds":usage.ru_utime,
            "system_cpu_seconds":usage.ru_stime,"peak_rss_bytes":usage.ru_maxrss*1024,
            "input_blocks":usage.ru_inblock,"output_blocks":usage.ru_oublock,"reaped":True}


def terminate(child, grace):
    try:
        os.killpg(child.pid,signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic()+grace
    while time.monotonic() < deadline:
        result = reap(child)
        if result is not None:
            return result
        time.sleep(.05)
    try:
        os.killpg(child.pid,signal.SIGKILL)
    except ProcessLookupError:
        pass
    deadline = time.monotonic()+grace
    while time.monotonic() < deadline:
        result = reap(child)
        if result is not None:
            return result
        time.sleep(.05)
    return {"pid":child.pid,"reaped":False,"signals_sent":"SIGTERM_THEN_SIGKILL","reason":"KERNEL_EXIT_NOT_OBSERVED_WITHIN_MANAGEMENT_GRACE"}


def require(value, signature):
    if not value:
        raise ValueError(signature)


def pre_capture_kernel_state():
    """Read the supervisor's own state; ECHILD is required before Source IO."""
    require(sys.platform == "linux", "LINUX_OWN_KERNEL_CUSTODY_REQUIRED")
    libc = ctypes.CDLL(None, use_errno=True)
    before = ctypes.c_int()
    require(libc.prctl(37, ctypes.byref(before), 0, 0, 0) == 0
            and before.value in (0, 1), "PRE_SOURCE_SUBREAPER_STATE_REQUIRED")
    try:
        found, child_status, child_usage = os.wait4(-1, os.WNOHANG)
    except ChildProcessError as error:
        require(error.errno == errno.ECHILD, "PRE_SOURCE_KERNEL_ECHILD_REQUIRED")
    else:
        raise ValueError("PRE_SOURCE_OWN_CHILD_PRESENT:" + str(found))
    after = ctypes.c_int()
    require(libc.prctl(37, ctypes.byref(after), 0, 0, 0) == 0
            and after.value == before.value, "PRE_SOURCE_SUBREAPER_READBACK_CHANGED")
    return {"kernel_echild_verified": True, "subreaper_state_before": before.value,
            "subreaper_state_after": after.value,
            "scope": "THIS_SUPERVISOR_ONLY; NO_GLOBAL_PROC_SCAN_OR_STATE_MUTATION"}


def managed_custody_closed(kernel):
    """Physical FIN permits documentary reads; it does not assert Native GREEN."""
    return (type(kernel) is dict and type(kernel.get("pid")) is int and kernel["pid"] > 0
            and kernel.get("actual_child_reaped") is True
            and kernel.get("wait4_reaped_pid") == kernel["pid"]
            and kernel.get("kernel_pre_popen_echild_verified") is True
            and kernel.get("owned_children_exhaustion_verified") is True
            and kernel.get("process_group_absent_after_reap") is True
            and kernel.get("subreaper_activation_readback_verified") is True
            and kernel.get("subreaper_restore_attempted") is True
            and kernel.get("subreaper_restoration_readback_verified") is True
            and kernel.get("remaining_owned_children") == [])


def managed_phase_green(kernel):
    """Keep failure, late reap, observed residuals and signal cleanup RED."""
    return (managed_custody_closed(kernel) and kernel.get("returncode") == 0
            and kernel.get("timed_out") is False
            and kernel.get("late_observed_main_reap_irreversible_red") is False
            and kernel.get("kernel_wait4_zero_observed_irreversible_red") is False
            and kernel.get("residual_descendants_observed") == []
            and kernel.get("owned_group_signal_observations") == []
            and kernel.get("supervisor_errors") == []
            and type(kernel.get("wall_seconds")) in (int, float)
            and math.isfinite(kernel["wall_seconds"]) and 0 <= kernel["wall_seconds"] <= 21600
            and all(type(row.get("exit_code")) is int and row["exit_code"] == 0
                    for row in kernel.get("adopted_descendants_reaped", [])))


def owned_kernel_peak_rss(kernel):
    candidates = [kernel.get("peak_rss_bytes")]
    candidates.extend(row.get("kernel_lifetime_peak_rss_bytes")
                      for row in kernel.get("adopted_descendants_reaped", []))
    require(candidates and all(type(value) is int and value >= 0 for value in candidates),
            "MAIN_AND_ADOPTED_WAIT4_RSS_REQUIRED")
    return max(candidates)


ORIGINAL_DRIVER = "docs/audits/rc6-convergence-persistence-evidence/run_full_horizon_1201_v2.py.source"
ORIGINAL_DRIVER_SHA256 = "ac9ba0f457ee4592646d91c5654f037efbfa2bc4f758469e16b7cf51d87e7aec"
ORIGINAL_PRODUCER = "docs/audits/rc6-convergence-persistence-evidence/native_archive_v3_profile_probe_v2.py"
ORIGINAL_PRODUCER_SHA256 = "cd39d440b36e5f35fbc23ad210fdf7370b728b761ce36020c9c88de3fa75f8da"
NATIVE_MANAGER_SHA256 = "55325b3108e175a42b87ebe544fd307fa45ffd29b6f7ab471443803ad9ba53b8"
MAX_FILE = 512*1024**2
MAX_MEMBERS = 32768
FIELDS = STATS
CODE10 = tuple(field for field in FIELDS if field != "st_atime_ns")



def native_finalizer_closed(report):
    stages = ['nonnegative_stdlib_finalizers', 'join_owned_children', 'stop_owned_forkserver',
              'remaining_stdlib_finalizers', 'stop_owned_resource_tracker', 'verify_kernel_echild']
    return (type(report) is dict and report.get('status') == 'GREEN'
        and type(report.get('management_bound_seconds')) is int and report['management_bound_seconds'] == 5
        and report.get('finalization_thread_finished') is True
        and report.get('kernel_echild_before_phase_return') is True
        and report.get('signal_guard_installed_and_witnessed') is True
        and report.get('forced_termination_attempted') is False
        and report.get('forced_termination') is False and report.get('signal_vetoed') is False
        and report.get('termination_signal_attempts') == [] and report.get('errors') == []
        and report.get('unexpected_kernel_children') == []
        and type(report.get('wall_seconds')) in (int, float)
        and 0 <= report['wall_seconds'] <= 5
        and [row.get('stage') for row in report.get('protocol_steps', [])] == stages
        and all(row.get('completed') is True for row in report['protocol_steps']))

def fields(info):
    return {name: getattr(info, name) for name in FIELDS}


def custody_canonical(path):
    require(path.is_absolute() and path.resolve(strict=True) == path,
            "CANONICAL_EXISTING_OWN_PATH_REQUIRED:" + str(path))
    require(not any(parent.is_symlink() for parent in (path, *path.parents)),
            "NO_ALIASED_ANCESTOR_REQUIRED:" + str(path))
    return path


def custody_capture(path, keep=False):
    custody_canonical(path)
    initial = path.lstat()
    require(stat.S_ISREG(initial.st_mode) and initial.st_nlink == 1
            and initial.st_uid == os.geteuid() and 0 <= initial.st_size <= MAX_FILE,
            "BOUNDED_OWN_REGULAR_SINGLE_LINK_REQUIRED:" + str(path))
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME
                         | os.O_NONBLOCK | os.O_CLOEXEC)
    chunks = []
    digest = hashlib.sha256()
    blob = hashlib.sha1(b"blob " + str(initial.st_size).encode() + b"\0")
    size = 0
    try:
        require(fields(initial) == fields(os.fstat(descriptor)), "CAPTURE_INITIAL_IDENTITY_CHANGED")
        while block := os.read(descriptor, 1024 * 1024):
            size += len(block)
            require(size <= initial.st_size, "CAPTURE_FILE_GREW")
            digest.update(block); blob.update(block)
            if keep:
                chunks.append(block)
        require(size == initial.st_size and fields(initial) == fields(os.fstat(descriptor))
                and fields(initial) == fields(path.lstat()), "CAPTURE_ALL11_CHANGED")
    finally:
        os.close(descriptor)
    return {"stat_fields": fields(initial), "sha256": digest.hexdigest(),
            "blob_id": blob.hexdigest()}, b"".join(chunks)


def whole_code_snapshot(source):
    """Full exact namespace including root/dirs and all11+hashes for files."""
    custody_canonical(source)
    queue = [source]
    result = {}
    while queue:
        parent = queue.pop()
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
                and stat.S_IMODE(info.st_mode) == (0o700 if parent == source else 0o755),
                "OWN_PINNED_SOURCE_DIRECTORY_CUSTODY_REQUIRED")
        fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                     | os.O_NOATIME | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            require(fields(info) == fields(os.fstat(fd)), "SOURCE_DIRECTORY_OPEN_IDENTITY_CHANGED")
            names = sorted(os.listdir(fd))
            require(len(names) <= MAX_MEMBERS and fields(info) == fields(os.fstat(fd))
                    and fields(info) == fields(parent.lstat()), "SOURCE_DIRECTORY_ALL11_CHANGED")
        finally:
            os.close(fd)
        relative = "." if parent == source else parent.relative_to(source).as_posix()
        result[relative] = {"kind": "directory", "stat_fields": fields(info), "entries": names}
        for name in names:
            path = parent / name
            item = path.lstat()
            if stat.S_ISDIR(item.st_mode):
                queue.append(path)
            else:
                record, _ = custody_capture(path)
                require(stat.S_IMODE(item.st_mode) in (0o644, 0o755), "SOURCE_MODE_NOT_PINNABLE")
                result[path.relative_to(source).as_posix()] = {"kind": "file", **record}
            require(len(result) + len(queue) <= MAX_MEMBERS, "SOURCE_NAMESPACE_BOUND")
    return result


def pinned_code(snapshot, pin):
    files = {name: row for name, row in snapshot.items() if row["kind"] == "file"}
    require(set(files) == set(pin["files"]), "EXACT_PINNED_WHOLE_SOURCE_MEMBER_SET_REQUIRED")
    for name, row in files.items():
        mode = "100" + format(stat.S_IMODE(row["stat_fields"]["st_mode"]), "03o")
        require(row["sha256"] == pin["files"][name] and row["blob_id"] == pin["blob_ids"][name]
                and mode == pin["modes"][name], "SOURCE_PIN_BYTES_MODE_BLOB_MISMATCH:" + name)
    return len(files)



def bounded_native_control(path):
    path = custody_canonical(path)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
            and before.st_uid == os.geteuid() and 0 <= before.st_size <= 262144,
            "HORIZON_NATIVE_CONTROL_SIZE_TYPE_OR_OWNER_LIMIT")
    return capture(path, keep_bytes=True)


def write_control_new(control_root, name, value):
    path = control_root/name
    wire = (json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n").encode()
    fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        offset = 0
        while offset < len(wire):
            size = os.write(fd,wire[offset:])
            require(size > 0,"HORIZON_CONTROL_SHORT_WRITE")
            offset += size
        os.fsync(fd)
    finally:
        os.close(fd)
    fd = os.open(control_root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return custody_capture(path)[0]


def source_controls(index):
    return {name:custody_capture(index.parent/name)[0] for name in
        ('source.tar','source.index.json','source.commit.raw','source.export-durability.json')}


def prepare_code(source, repository, sha, tree, index, control, raw, data):
    initial = pre_capture_kernel_state()
    require(not raw.exists() and not data.exists(),'NO_RAW_DATA_BEFORE_CODE_PREPARATION')
    controls_before = source_controls(index)
    pin = parse(capture(index,keep_bytes=True)[1])
    before = whole_code_snapshot(source)
    pinned_code(before,pin)
    # Ordinary real CODE-only reads before epoch baseline; no filesystem reset.
    for name,row in before.items():
        path = source if name == '.' else source/name
        flags = os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC
        if row['kind'] == 'directory':
            fd = os.open(path,flags|os.O_DIRECTORY)
            try:
                require(sorted(os.listdir(fd)) == row['entries'],'CODE_PREPARATION_NAMESPACE_CHANGED')
            finally:
                os.close(fd)
        else:
            fd = os.open(path,flags|os.O_NONBLOCK)
            digest,size = hashlib.sha256(),0
            try:
                while block := os.read(fd,1024*1024):
                    digest.update(block);size += len(block)
            finally:
                os.close(fd)
            require(size == row['stat_fields']['st_size'] and digest.hexdigest() == row['sha256'],
                    'CODE_PREPARATION_BYTES_CHANGED')
    after = whole_code_snapshot(source)
    require(set(before)==set(after),'CODE_PREPARATION_NAMESPACE_CHANGED')
    changes = []
    for name in before:
        a,b = before[name],after[name]
        require({k:v for k,v in a.items() if k!='stat_fields'}=={k:v for k,v in b.items() if k!='stat_fields'}
            and all(a['stat_fields'][f]==b['stat_fields'][f] for f in CODE10),
            'CODE_PREPARATION_10_FIELDS_BYTES_OR_MODES_CHANGED')
        require(b['stat_fields']['st_atime_ns']>=max(b['stat_fields']['st_mtime_ns'],b['stat_fields']['st_ctime_ns']),
                'CODE_PREBASELINE_ORDINARY_READ_NOT_ESTABLISHED')
        if a['stat_fields']['st_atime_ns'] != b['stat_fields']['st_atime_ns']:
            changes.append({'member':name,'before_atime_ns':a['stat_fields']['st_atime_ns'],
                            'after_atime_ns':b['stat_fields']['st_atime_ns']})
    verify_source(repository,source,sha,tree,pin)
    require(source_controls(index)==controls_before,'SOURCE_CONTROLS_CHANGED_DURING_CODE_PREPARATION')
    final = pre_capture_kernel_state()
    require(initial['subreaper_state_after']==final['subreaper_state_after'],'CODE_PREPARATION_KERNEL_CHANGED')
    write_control_new(control,'code-preparation-before-all11.json',before)
    write_control_new(control,'code-preparation-after-all11.json',after)
    receipt = {'schema':'rc6.material-horizon-code-preparation.v1','source_sha':sha,'source_tree':tree,
        'code10_bytes_modes_and_namespace_unchanged':True,'actual_code_atime_changes':changes,
        'source_controls_all11_unchanged':True,'ordinary_code_reads':True,'filesystem_stat_restoration':False,
        'data_priming':False,'own_kernel_before':initial,'own_kernel_after':final,'gate_qualification':False}
    write_control_new(control,'code-preparation.json',receipt)
    return pin


def comparison_command_args(path,expected_hash,*,source_root,data_root,source_sha,source_tree):
    """Forward one original bounded control; it never confers G5 authority."""
    if path is None:
        require(expected_hash is None,'PRIVATE_CAS_MANIFEST_AND_HASH_PAIR_REQUIRED')
        return []
    require(type(expected_hash) is str and HEX64.fullmatch(expected_hash),
        'PRIVATE_CAS_MANIFEST_AND_HASH_PAIR_REQUIRED')
    path=canonical(Path(path),existing=True)
    require(not path.is_relative_to(source_root) and path.parent==data_root.parent
        and path.name=='private-cas-admission.json','PRIVATE_CAS_MANIFEST_OWNED_SIBLING_REQUIRED')
    require(path.lstat().st_uid==os.geteuid() and path.lstat().st_size<=256*1024,
        'PRIVATE_CAS_MANIFEST_OWNED_BYTES_BOUND')
    facts,wire=capture(path,keep_bytes=True)
    require(facts['st_size']<=256*1024 and facts['sha256']==expected_hash,
        'PRIVATE_CAS_MANIFEST_BYTES_OR_HASH_CHANGED')
    manifest=parse(wire)
    require(manifest.get('schema')=='rc6.original-cas-comparison-admission.v2'
        and manifest.get('source_sha')==source_sha and manifest.get('source_tree')==source_tree
        and manifest.get('producer_namespace_root')==str(data_root)
        and manifest.get('qualification_scope')=='PRIVATE_DEVELOPMENT_ONLY_NOT_G5',
        'PRIVATE_CAS_MANIFEST_SOURCE_OR_DATA_ROOT_REBOUND')
    return ['--cas-comparison-manifest',str(path)]


def execute(*,source_root,source_repo,source_sha,source_tree,source_index,raw_root,data_root,python311,control_root,
            cas_comparison_manifest=None,cas_comparison_manifest_sha256=None):
    initial_kernel = pre_capture_kernel_state()  # No Source or payload IO before actual own ECHILD.
    source = canonical(source_root,existing=True)
    repository = canonical(source_repo,existing=True)
    index = canonical(source_index,existing=True)
    raw,data,control = (canonical(path,existing=False) for path in (raw_root,data_root,control_root))
    require(all(not path.exists() for path in (raw,data,control)),'NEW_HORIZON_RAW_DATA_CONTROL_REQUIRED')
    for a,b in ((raw,data),(raw,source),(raw,repository),(data,source),(data,repository),
                (control,source),(control,repository),(control,raw),(control,data)):
        require(a!=b and not a.is_relative_to(b) and not b.is_relative_to(a),'SEPARATE_HORIZON_NAMESPACES_REQUIRED')
    require(index.name=='source.index.json' and not index.is_relative_to(data), 'INDEPENDENT_WHOLE_SOURCE_INDEX_REQUIRED')
    comparison_args=comparison_command_args(cas_comparison_manifest,cas_comparison_manifest_sha256,
        source_root=source,data_root=data,source_sha=source_sha,source_tree=source_tree)
    interpreter = Path(python311).absolute()
    require(interpreter.is_file() and os.access(interpreter,os.X_OK),'EXPLICIT_PRODUCT311_INTERPRETER_REQUIRED')
    os.umask(0o022)
    control.mkdir(mode=0o700)
    for name in ('tmp','logs','hypothesis'):
        (control/name).mkdir(mode=0o700)
    environment = {key:value for key,value in os.environ.items()
        if not key.startswith(('POROTA_','PAPER_','PPI_','IOL_','BYMA_','PYTHON')) and key not in {'DATA_DIR','HIST_DB_PATH'}}
    environment.update(PYTHONDONTWRITEBYTECODE='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
        TMPDIR=str(control/'tmp'),RUNNER_TEMP=str(control/'tmp'),LOG_DIR=str(control/'logs'),
        HYPOTHESIS_STORAGE_DIRECTORY=str(control/'hypothesis'))
    result = {'schema':'rc6.material-horizon-carrier.v1','classification':'IN_PROGRESS_NOT_ACCEPTED',
        'source_sha':source_sha,'source_tree':source_tree,'native_horizon_validated':False,'errors':[],
        'scope':'SYNTHETIC1202_NATIVE_CUTS_NOT_CONTINUOUS_OPEN_OR_ARTIFACT_RUNTIME',
        'supervisor_environment_scope':'STDLIB_CONTROL_ONLY_NOT_QUALIFIED_AS_PRODUCT157',
        'management_watchdog_seconds':21600,'original_native_full_cycle_seconds':30,
        'owned_cleanup_seconds':5,'native_stdlib_finalizer_seconds':5,'native_ticks_expected':1202,
        'real_orders_sent':0,'artifact_validated':False,'runtime_validated':False,'deployed':False,
        'initial_own_kernel':initial_kernel,'read_payload_on_unknown':False}
    kernel = None
    source_before = controls_before = None
    prepared = launched = False
    def progress(phase,pid,entered,deadline,log_fd):
        write_json(raw/('driver-started.json' if phase=='started' else 'driver-progress.json'),
            {'native_pid':pid,'process_reaped':False,'management_elapsed_seconds':time.monotonic()-entered,
             'management_seconds_remaining':max(0,deadline-time.monotonic()),
             'native_stdout_bytes':os.fstat(log_fd).st_size,'classification':'IN_PROGRESS_NOT_ACCEPTED'})
    try:
        pin = parse(capture(index,keep_bytes=True)[1])
        before,authority = verify_source(repository,source,source_sha,source_tree,pin)
        require(capture(source/ORIGINAL_DRIVER)[0]['sha256']==ORIGINAL_DRIVER_SHA256
                and capture(source/ORIGINAL_PRODUCER)[0]['sha256']==ORIGINAL_PRODUCER_SHA256,
                'PRESERVED_ORIGINAL_HORIZON_MEMBERS_CHANGED')
        require(capture(source/'scripts/rc6_controlled_native_child_manager.py')[0]['sha256']==NATIVE_MANAGER_SHA256,
                'REVIEWED_HORIZON_NATIVE_MANAGER_BYTES_CHANGED')
        require(capture(Path(__file__).absolute())[0]['sha256']==before[DRIVER]['sha256']
                and Path(__file__).absolute()==source/DRIVER,'SOURCE_INDEXED_CARRIER_MEMBER_REQUIRED')
        sys.path.insert(0,str(source))
        from scripts import rc6_controlled_native_child_manager as manager
        from scripts import rc6_controlled_governed_runner as lifecycle
        for module,name in ((manager,'scripts/rc6_controlled_native_child_manager.py'),
                            (lifecycle,'scripts/rc6_controlled_governed_runner.py')):
            require(Path(module.__file__).absolute()==source/name,'HORIZON_CONTROL_MODULE_SOURCE_ORIGIN_MISMATCH')
        capability = lifecycle.restrict_inet_creation()  # Irreversible and inherited; never an INET exception.
        result['offline_ipv6_creation_capability'] = capability
        require(capability['status']=='INSTALLED_AND_KERNEL_WITNESSED','HORIZON_NATIVE_INET_CAPABILITY_NOT_WITNESSED')
        qualification = preflight(interpreter,'3.11.16',source,environment)
        result['environment_qualification_before_fixtures'] = qualification
        require(not data.exists() and not raw.exists(),'HORIZON_DATA_APPEARED_BEFORE_PRODUCT157_QUALIFICATION')
        pin = prepare_code(source,repository,source_sha,source_tree,index,control,raw,data)
        prepared = True
        source_before,controls_before = whole_code_snapshot(source),source_controls(index)
        write_control_new(control,'source-before-all11.json',source_before)
        write_control_new(control,'source-controls-before-all11.json',controls_before)
        raw.mkdir(mode=0o700)
        owned_fin = control/'producer-owned-fin.json'
        command = [str(interpreter),'-I','-B','-u',str(source/PROFILE),
            '--source-repo',str(repository),'--source-root',str(source),
            '--source-sha',source_sha,'--source-tree',source_tree,'--source-index',str(index),
            '--root',str(data),'--catalog-count','1200','--ticks','1201','--owned-fin',str(owned_fin)]
        command.extend(comparison_args)
        result.update(command=command,source_index_sha256=capture(index)[0]['sha256'],
            source_tar_sha256=capture(index.parent/'source.tar')[0]['sha256'],
            source_authority_before=authority,source_file_count=len(before),
            native_owned_fin_control=str(owned_fin),raw_root=str(raw),data_root=str(data))
        write_control_new(control,'launch.json',result)
        launched = True
        kernel = manager.managed_native_child(command,source,raw/'native1201.log',environment,21600,
            terminate_grace=2,progress_poll=5,progress=progress)
        result['kernel_owned_process_custody'] = kernel
        require(manager.managed_custody_closed(kernel),'HORIZON_OWNED_KERNEL_UNKNOWN_NO_PAYLOAD_POSTREAD')
        actual_after = manager.pre_capture_kernel_state()
        require(actual_after['subreaper_state_after']==initial_kernel['subreaper_state_after'],
                'HORIZON_OWN_SUBREAPER_NOT_RESTORED')
        result['actual_own_kernel_after'] = actual_after
        result['physical_custody_closed'] = True
        # First native control only. Native result, Source, index/TAR and log remain unread.
        fin_capture,fin_wire = bounded_native_control(owned_fin)
        fin = parse(fin_wire)
        require(fin.get('schema')=='rc6.native-horizon-owned-fin.v1'
                and fin.get('pid')==kernel['pid'] and fin.get('parent_pid')==os.getpid()
                and fin.get('entry_module')=='__main__' and fin.get('entry_member')==PROFILE
                and fin.get('source_sha')==source_sha and fin.get('source_tree')==source_tree
                and fin.get('source_root')==str(source)
                and fin.get('source_index_sha256')==result['source_index_sha256'],
                'HORIZON_NATIVE_OWNED_FIN_SOURCE_PID_BINDING_MISMATCH')
        require(native_finalizer_closed(fin.get('child_infrastructure_finalization'))
                and fin.get('native_finalizer_closed') is True
                and fin.get('operation_audit_witness_verified') is True
                and fin.get('operation_audit',{}).get('inet_socket_attempts')==[]
                and fin.get('offline_ipv6_creation_capability',{}).get('status')=='INSTALLED_AND_KERNEL_WITNESSED'
                and type(fin.get('infrastructure_before')) is dict
                and set(fin['infrastructure_before']) == {'resource_tracker_pid', 'resource_tracker_fd',
                    'forkserver_pid', 'forkserver_alive_fd'}
                and all(value is None for value in fin['infrastructure_before'].values()),
                'HORIZON_NATIVE_FINALIZER5_UNKNOWN_OR_RED_NO_PAYLOAD_POSTREAD')
        result.update(native_owned_fin=fin,native_owned_fin_capture=fin_capture,
                      native_finalizer5_green_before_payload_reads=True)
        if not manager.managed_phase_green(kernel) or kernel.get("process_group_absent_at_main_reap") is not True:
            result['errors'].append('NATIVE_EXIT_DEADLINE_RESIDUAL_OR_CLEANUP_NOT_GREEN')
        peak_rss = owned_kernel_peak_rss(kernel)
        result['kernel_owned_lifetime_peak_rss_bytes_max'] = peak_rss
        require(peak_rss<2*1024**3,'HORIZON_ORIGINAL_STRICT_KERNEL_RSS_2GIB')
        native_capture,native_wire = capture(data/'result.json',keep_bytes=True)
        native = parse(native_wire)
        require(native.get('native_child_infrastructure')==fin,'HORIZON_NATIVE_LIFECYCLE_CONTROL_RESULT_MISMATCH')
        inspect_native(native,source,pin,qualification,data/'data/paper_v17/observer_v17.db',result['errors'])
        result.update(native_result_sha256=native_capture['sha256'],native_result_bytes=len(native_wire),
                      original_native_acceptance_checks_executed=True)
        after,authority_after = verify_source(repository,source,source_sha,source_tree,pin)
        source_after,controls_after = whole_code_snapshot(source),source_controls(index)
        write_control_new(control,'source-after-all11.json',source_after)
        write_control_new(control,'source-controls-after-all11.json',controls_after)
        require(before==after and source_before==source_after,'HORIZON_CODE_OR_NAMESPACE_ALL11_CHANGED')
        require(controls_before==controls_after,'HORIZON_SOURCE_CONTROLS_ALL11_CHANGED')
        result.update(source_authority_after=authority_after,source_whole_all11_unchanged=True,
                      source_controls_all11_unchanged=True)
        result['native_horizon_validated'] = (manager.managed_phase_green(kernel)
            and kernel.get('process_group_absent_at_main_reap') is True and not result['errors'])
        result['classification'] = 'LOCAL_SYNTHETIC_FULL1201_NATIVE_HORIZON_GREEN' if result['native_horizon_validated'] else 'FULL1201_NOT_ACCEPTED'
    except BaseException as error:
        result['errors'].append({'class':type(error).__name__,'reason':str(error)})
        result['native_horizon_validated'] = False
        result['classification'] = 'FULL1201_NOT_ACCEPTED'
    if comparison_args:
        result.update(native_horizon_validated=False,private_comparison_active=True,
            classification='PRIVATE_ORIGINAL_CAS_COMPARISON_NOT_G5',G5_qualification=False,
            comparison_manifest_sha256=cas_comparison_manifest_sha256)
    result.update(code_preparation_completed=prepared,native_launched=launched)
    receipt = write_control_new(control,'terminal.json',result)
    result['terminal_receipt_sha256'] = receipt['sha256']
    return (0 if result['native_horizon_validated'] else 1),result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source-root','source-repo','source-sha','source-tree','source-index',
                 'raw-root','data-root','python311','control-root'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--cas-comparison-manifest')
    parser.add_argument('--cas-comparison-manifest-sha256')
    args = parser.parse_args()
    values = vars(args)
    for name in ('source_root','source_repo','source_index','raw_root','data_root','python311','control_root'):
        values[name] = Path(values[name])
    code,report = execute(**values)
    print(json.dumps({'classification':report['classification'],'source_sha':report['source_sha'],
                      'native_horizon_validated':report['native_horizon_validated']},sort_keys=True),flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
