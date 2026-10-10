"""Prove current native isolation and read limits before bounded PAPER consumers.

This controller neither provisions nor enlarges a cgroup/filesystem. Readiness
is fail-closed on a stock runner. Execution additionally requires authentic G0,
fresh owner authority and an actual Product157 environment. A tmpfs ceiling
is a resource observation, not a proof of disk durability, aggregate storage
security or the memory of a complete Droplet including its OS and PPI Watch.
The consumer path remains disabled until the current native namespace filter
has authentic installation and denial evidence; Seccomp=2 alone never admits it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import stat
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

MIB = 1024**2
GIB = 1024**3
PLAN = ROOT / "ops/policy/rc6-product-resource-compatibility-v1.json"
SCOPE = "BOUNDED_ORIGINAL_PAPER_CONSUMERS_NOT_G0_G8_OR_DROPLET_QUALIFICATION"
FILTER_BLOCKER = "RESOURCE_CURRENT_NATIVE_NAMESPACE_FILTER_INSTALLATION_REQUIRED"


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def document(raw):
    from scripts.rc6_material_pr_admission import document as strict_document
    return strict_document(raw)


def exact_plan(path=PLAN):
    raw = Path(path).read_bytes()
    plan = document(raw)
    # Scope and historical hard limits are fixed. This new profile adds a
    # measurement; it does not replace or relabel the original BIG/Horizon.
    required = {
        "schema": "porota.rc6.product-resource-compatibility-plan.v1",
        "scope": SCOPE, "cpu_maximum": 1, "affinity_cpu_count": 1,
        "memory_maximum_bytes": GIB, "swap_maximum_bytes": 0,
        "pids_maximum": 64, "storage_maximum_bytes": 256 * MIB,
        "inode_maximum": 65536, "minimum_free_inode_fraction": 0.1,
        "filesystem": "tmpfs", "tmpfs_charged_to_cgroup_memory": True,
        "persistent_disk_latency_or_durability_qualified": False,
        "whole_droplet_memory_qualified": False,
        "host_os_and_ppi_watch_memory_not_in_process_envelope": True,
        "source_readonly_mount_required": True,
        "all_foreign_mounts_readonly_required": True,
        "current_cgroup_readonly_namespace_root_required": True,
        "privilege_status_readback_required": True,
        "effective_seccomp_rules_qualified": False,
        "aggregate_storage_security_qualified": False,
        "execution_enabled": True,
        "native_namespace_filter_schema": "porota.rc6.native-namespace-filter.v1",
        "native_namespace_filter_plan_sha256": "9daf64ba999008527b7220b4274e0e7439e27711608ec47b544b8e6e7e42dc2e",
        "native_current_load_counter_and_denials_required": True,
        "native_worker_inheritance_before_own_installation_required": True,
        "supervisor_dumpable_zero_required": True,
        "worker_exec_dumpable_actual_value_required": True,
        "product157_before_fixtures_required": True,
        "native_g0_artifact_maximum_bytes": 64 * MIB,
        "native_g0_only_required": True, "timeout_seconds": 180,
        "historical_G4_G5_workloads_changed": False,
        "material_gate_launched_by_readiness": False,
        "G0_G8_qualification": False, "recurring_infrastructure_cost_usd": 0,
        "persistent_infrastructure_changes": False,
        "mode": "PRODUCTION_PAPER / SIMULATION", "real_orders_sent": 0,
        "ppi_watch": "UNTOUCHED", "DEPLOY_OWNER": "NOT_ACQUIRED",
    }
    require(all(type(plan.get(key)) is type(value) and plan[key] == value
                for key, value in required.items()), "RESOURCE_SCOPE_OR_FIXED_LIMIT_CHANGED")
    suites = plan.get("suites")
    require(type(suites) is list and 0 < len(suites) <= 16
            and len(suites) == len(set(suites))
            and all(re.fullmatch(r"tests/test_[A-Za-z0-9_]+\.py::test_[A-Za-z0-9_]+", suite)
                    for suite in suites), "RESOURCE_BOUNDED_ORIGINAL_SUITE_REQUIRED")
    return plan, digest(raw)


def unescape_mount(value):
    return re.sub(r"\\([0-7]{3})", lambda item: chr(int(item[1], 8)), value)


def mount_rows(raw):
    rows = []
    for line in raw.splitlines():
        fields = line.split()
        require("-" in fields, "RESOURCE_MOUNTINFO_MALFORMED")
        separator = fields.index("-")
        require(separator >= 6 and len(fields) == separator + 4,
                "RESOURCE_MOUNTINFO_MALFORMED")
        rows.append({"id": int(fields[0]), "device": fields[2],
                     "root": unescape_mount(fields[3]),
                     "mountpoint": unescape_mount(fields[4]),
                     "options": sorted(fields[5].split(",")),
                     "propagation": sorted(fields[6:separator]),
                     "filesystem": fields[separator + 1],
                     "super_options": sorted(fields[separator + 3].split(","))})
    return rows


def containing_mount(path, rows):
    path = Path(path).absolute()
    matches = [row for row in rows if path.is_relative_to(Path(row["mountpoint"]))]
    require(bool(matches), "RESOURCE_ACTUAL_MOUNT_MISSING")
    longest = max(len(row["mountpoint"]) for row in matches)
    matches = [row for row in matches if len(row["mountpoint"]) == longest]
    require(len(matches) == 1, "RESOURCE_OVERMOUNT_OR_AMBIGUOUS_MOUNT")
    return matches[0]


def finite_integer(value, reason):
    require(type(value) is str and re.fullmatch(r"[0-9]+", value) is not None, reason)
    return int(value)


def parse_counter(raw):
    result = {}
    for line in raw.splitlines():
        pair = line.split()
        require(len(pair) == 2 and pair[0] not in result, "RESOURCE_COUNTER_MALFORMED")
        result[pair[0]] = finite_integer(pair[1], "RESOURCE_COUNTER_MALFORMED")
    return result


def privilege_status(raw):
    wanted = {"Uid", "Gid", "Groups", "CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb",
              "NoNewPrivs", "Seccomp"}
    result = {}
    for line in raw.splitlines():
        key, separator, value = line.partition(":")
        if key in wanted:
            require(separator and key not in result, "RESOURCE_PRIVILEGE_STATUS_AMBIGUOUS")
            result[key] = value.strip()
    require(set(result) == wanted, "RESOURCE_PRIVILEGE_STATUS_INCOMPLETE")
    return result


def validate_privileges(status, uid):
    for key in ("Uid", "Gid"):
        ids = status[key].split()
        require(len(ids) == 4 and all(re.fullmatch(r"[0-9]+", value) for value in ids),
                "RESOURCE_PRIVILEGE_ID_STATUS_INVALID")
        values = list(map(int, ids))
        require(len(set(values)) == 1 and values[0] > 0
                and (key != "Uid" or values[0] == uid), "RESOURCE_SAVED_OR_FILESYSTEM_ROOT_ESCAPE")
    require(status["Groups"] == "", "RESOURCE_SUPPLEMENTARY_GROUP_ESCAPE")
    require(all(re.fullmatch(r"[0-9a-fA-F]+", status[key]) and int(status[key], 16) == 0
                for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")),
            "RESOURCE_CAPABILITY_ESCAPE")
    require(status["NoNewPrivs"] == "1", "RESOURCE_NO_NEW_PRIVILEGES_REQUIRED")
    require(status["Seccomp"] == "2", "RESOURCE_SECCOMP_FILTER_MODE_REQUIRED")
    # Linux's status counter cannot identify the installed filter rules. G0
    # still needs its original native proof of denied namespace/mount syscalls.
    return {"uids_and_gids_nonroot": True, "five_capability_sets_zero": True,
            "no_new_privileges": True, "seccomp_mode": 2,
            "effective_seccomp_rules": "NO_VERIFICADO"}


def validate_mount_topology(mounts, *, work_mount_id, work_root):
    require(type(mounts) is list and 0 < len(mounts) <= 4096,
            "RESOURCE_COMPLETE_MOUNT_TOPOLOGY_REQUIRED")
    identifiers = [row["id"] for row in mounts]
    require(all(type(identifier) is int and identifier > 0 for identifier in identifiers)
            and len(identifiers) == len(set(identifiers)), "RESOURCE_MOUNT_IDENTITY_AMBIGUOUS")
    require(work_mount_id in identifiers, "RESOURCE_WORK_MOUNT_MISSING_FROM_TOPOLOGY")
    require(sum(row["mountpoint"] == "/" for row in mounts) == 1,
            "RESOURCE_UNAMBIGUOUS_ROOT_MOUNT_REQUIRED")
    for row in mounts:
        options = row["options"]
        require(type(options) is list and sum(value in options for value in ("ro", "rw")) == 1,
                "RESOURCE_MOUNT_WRITE_MODE_UNKNOWN")
        require(not any(token.startswith(("shared:", "master:", "propagate_from:"))
                        for token in row["propagation"]), "RESOURCE_SHARED_MOUNT_PROPAGATION_ESCAPE")
        if row["id"] != work_mount_id:
            require("ro" in options, "RESOURCE_FOREIGN_WRITABLE_MOUNT_QUOTA_ESCAPE")
        else:
            require(row["mountpoint"] == work_root and "rw" in options,
                    "RESOURCE_WORK_MOUNT_TOPOLOGY_REBOUND")
    return {"complete_mount_count": len(mounts), "foreign_mounts_all_readonly": True,
            "private_mount_propagation": True, "mount_topology_sha256": digest(canonical(mounts)),
            "aggregate_storage_security_qualified": False,
            "effective_seccomp_rules": "NO_VERIFICADO"}


def validate_cgroup(observation, plan):
    quota_fields = observation["cpu.max"].split()
    require(len(quota_fields) == 2, "RESOURCE_CPU_MAX_MALFORMED")
    quota = finite_integer(quota_fields[0], "RESOURCE_CPU_LIMIT_NOT_ENFORCED")
    period = finite_integer(quota_fields[1], "RESOURCE_CPU_LIMIT_NOT_ENFORCED")
    require(quota > 0 and period > 0 and quota <= period * plan["cpu_maximum"],
            "RESOURCE_CPU_ABOVE_ONE_CORE")
    affinity = observation["affinity"]
    require(type(affinity) is list and len(affinity) == plan["affinity_cpu_count"]
            and all(type(cpu) is int and cpu >= 0 for cpu in affinity),
            "RESOURCE_SINGLE_CPU_AFFINITY_REQUIRED")
    memory = finite_integer(observation["memory.max"], "RESOURCE_MEMORY_LIMIT_NOT_ENFORCED")
    swap = finite_integer(observation["memory.swap.max"], "RESOURCE_SWAP_LIMIT_NOT_ENFORCED")
    pids = finite_integer(observation["pids.max"], "RESOURCE_PIDS_LIMIT_NOT_ENFORCED")
    require(0 < memory <= plan["memory_maximum_bytes"], "RESOURCE_MEMORY_ABOVE_ONE_GIB")
    require(swap == plan["swap_maximum_bytes"], "RESOURCE_SWAP_MUST_BE_ZERO")
    require(0 < pids <= plan["pids_maximum"], "RESOURCE_PIDS_LIMIT_TOO_LARGE")
    require(observation["controls_immutable_to_nonroot"] is True,
            "RESOURCE_NONROOT_CAN_ENLARGE_LIMITS")
    current = finite_integer(observation["memory.current"], "RESOURCE_MEMORY_CURRENT_INVALID")
    peak = finite_integer(observation["memory.peak"], "RESOURCE_MEMORY_PEAK_INVALID")
    require(0 <= current <= peak <= memory, "RESOURCE_MEMORY_ENVELOPE_ALREADY_EXCEEDED")
    require(type(observation.get("uid")) is int and observation["uid"] > 0
            and observation["uid"] == observation["euid"], "RESOURCE_NONROOT_REQUIRED")
    require(type(observation["processes"]) is list
            and os.getpid() in observation["processes"], "RESOURCE_CURRENT_CGROUP_MEMBERSHIP_REQUIRED")
    require(observation["membership"] == "0::/" and observation["mount_root"] == "/"
            and observation["path"] == observation["mountpoint"]
            and "ro" in observation["mount_options"] and "rw" not in observation["mount_options"],
            "RESOURCE_READONLY_CURRENT_CGROUP_NAMESPACE_ROOT_REQUIRED")
    require(all(type(observation[key]) is int and observation[key] > 0
                for key in ("cgroup_namespace_inode", "mount_namespace_inode")),
            "RESOURCE_NAMESPACE_IDENTITY_REQUIRED")
    privileges = validate_privileges(observation["privilege_status"], observation["uid"])
    return {"quota": quota, "period": period, "cpu_limit": quota / period,
            "affinity": affinity, "memory_limit_bytes": memory,
            "memory_current_bytes": current, "memory_peak_bytes": peak,
            "swap_limit_bytes": swap, "pids_limit": pids,
            "readonly_current_cgroup_namespace_root": True, "privileges": privileges,
            "interpreter_supervisor_children_cache_and_tmpfs_in_same_memory_budget": True}


def observe_cgroup():
    rows = mount_rows(Path("/proc/self/mountinfo").read_text())
    mounts = [row for row in rows if row["filesystem"] == "cgroup2"]
    require(len(mounts) == 1, "RESOURCE_UNAMBIGUOUS_CGROUP_V2_REQUIRED")
    membership = Path("/proc/self/cgroup").read_text().strip()
    require(membership.startswith("0::/") and "\n" not in membership,
            "RESOURCE_UNIFIED_CURRENT_CGROUP_REQUIRED")
    visible = Path(membership[3:])
    mounted_root = Path(mounts[0]["root"])
    require(visible.is_relative_to(mounted_root), "RESOURCE_CGROUP_NAMESPACE_MAPPING_UNKNOWN")
    location = Path(mounts[0]["mountpoint"]) / visible.relative_to(mounted_root)
    require(not any(path.is_symlink() for path in (location, *location.parents)),
            "RESOURCE_CGROUP_ALIAS_FORBIDDEN")
    files = ("cpu.max", "memory.max", "memory.swap.max", "pids.max",
             "memory.current", "memory.peak", "memory.events", "cpu.stat", "cgroup.procs")
    values = {name: (location / name).read_text().strip() for name in files}
    protected = ("cpu.max", "memory.max", "memory.swap.max", "pids.max", "cgroup.procs")
    values.update({"path": str(location), "membership": membership, "mount_id": mounts[0]["id"],
                   "mount_root": mounts[0]["root"], "mountpoint": mounts[0]["mountpoint"],
                   "mount_options": mounts[0]["options"],
                   "cgroup_namespace_inode": os.stat("/proc/self/ns/cgroup").st_ino,
                   "mount_namespace_inode": os.stat("/proc/self/ns/mnt").st_ino,
                   "privilege_status": privilege_status(Path("/proc/self/status").read_text()),
                   "affinity": sorted(os.sched_getaffinity(0)), "uid": os.getuid(), "euid": os.geteuid(),
                   "controls_immutable_to_nonroot": not any(os.access(location / name, os.W_OK)
                                                            for name in protected),
                   "processes": [finite_integer(value, "RESOURCE_CGROUP_PID_INVALID")
                                 for value in values["cgroup.procs"].splitlines()],
                   "events": parse_counter(values["memory.events"]),
                   "cpu_statistics": parse_counter(values["cpu.stat"]),
                   "process_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024})
    return values


def validate_filesystem(observation, plan):
    require(observation["mountpoint"] == observation["work_root"]
            and observation["root"] == "/", "RESOURCE_EXCLUSIVE_WHOLE_MOUNT_REQUIRED")
    require(observation["filesystem"] == plan["filesystem"],
            "RESOURCE_FILESYSTEM_PROFILE_REQUIRES_SEPARATE_DISK_CONTRACT")
    require("rw" in observation["options"] and "nosuid" in observation["options"]
            and "nodev" in observation["options"], "RESOURCE_BOUNDED_MOUNT_SECURITY_REQUIRED")
    require(type(observation["total_bytes"]) is int
            and 0 < observation["total_bytes"] <= plan["storage_maximum_bytes"],
            "RESOURCE_AGGREGATE_FILESYSTEM_CAPACITY_NOT_ENFORCED")
    require(type(observation["total_inodes"]) is int
            and 0 < observation["total_inodes"] <= plan["inode_maximum"],
            "RESOURCE_AGGREGATE_INODE_LIMIT_NOT_ENFORCED")
    require(type(observation["free_inodes"]) is int
            and observation["total_inodes"] >= observation["free_inodes"]
            >= observation["total_inodes"] * plan["minimum_free_inode_fraction"],
            "RESOURCE_FREE_INODE_RESERVE_REQUIRED")
    require(type(observation["available_bytes"]) is int
            and 0 < observation["available_bytes"] <= observation["total_bytes"],
            "RESOURCE_FILESYSTEM_AVAILABLE_BYTES_INVALID")
    require(observation["source_mount_id"] != observation["id"]
            and "ro" in observation["source_options"], "RESOURCE_SOURCE_READONLY_SEPARATE_MOUNT_REQUIRED")
    require(observation["nested_writable_mounts"] == [], "RESOURCE_NESTED_WRITABLE_QUOTA_ESCAPE")
    require(type(observation["owner_uid"]) is int and observation["owner_uid"] == os.geteuid(),
            "RESOURCE_OWNED_WORK_ROOT_REQUIRED")
    topology = validate_mount_topology(observation["all_mounts"],
        work_mount_id=observation["id"], work_root=observation["work_root"])
    source_rows = [row for row in observation["all_mounts"] if row["id"] == observation["source_mount_id"]]
    require(len(source_rows) == 1 and source_rows[0]["options"] == observation["source_options"],
            "RESOURCE_SOURCE_MOUNT_TOPOLOGY_REBOUND")
    return {"aggregate_storage_limit_bytes": observation["total_bytes"],
            "aggregate_inode_limit": observation["total_inodes"],
            "available_bytes": observation["available_bytes"], "free_inodes": observation["free_inodes"],
            "filesystem": observation["filesystem"], "mount_id": observation["id"],
            "source_mount_id": observation["source_mount_id"],
            "topology": topology, "aggregate_storage_security_qualified": False,
            "persistent_disk_latency_or_durability_qualified": False}


def observe_filesystem(work_root, source_root):
    root = Path(work_root).absolute()
    require(root.is_dir() and not any(path.is_symlink() for path in (root, *root.parents)),
            "RESOURCE_LITERAL_EXISTING_WORK_ROOT_REQUIRED")
    mounts = mount_rows(Path("/proc/self/mountinfo").read_text())
    mount = containing_mount(root, mounts)
    source = containing_mount(source_root, mounts)
    usage = os.statvfs(root)
    return {**mount, "work_root": str(root), "owner_uid": root.lstat().st_uid,
            "device_number": root.lstat().st_dev,
            "total_bytes": usage.f_blocks * usage.f_frsize,
            "available_bytes": usage.f_bavail * usage.f_frsize,
            "total_inodes": usage.f_files, "free_inodes": usage.f_favail,
            "source_mount_id": source["id"], "source_options": source["options"],
            "all_mounts": mounts,
            "nested_writable_mounts": [row["id"] for row in mounts
                if row["id"] != mount["id"] and Path(row["mountpoint"]).is_relative_to(root)
                and "rw" in row["options"]]}


def readiness(work_root, source_root, plan, *, source_binding=None):
    # Preserve all independent observations, even when the first limit is RED.
    result = {"checks": {}, "failures": [], "workload_started": False,
              "status": "BLOQUEADO", "scope": SCOPE,
              "product_resource_compatibility": "NO_VERIFICADO",
              "whole_droplet_compatibility": "NO_VERIFICADO",
              "persistent_disk_compatibility": "NO_VERIFICADO",
              "effective_seccomp_rules": "NO_VERIFICADO",
              "aggregate_storage_security_qualified": False,
              "execution_blockers": [{"reason": FILTER_BLOCKER,
                  "scope": "CURRENT_SUPERVISOR_AND_WORKER_NOT_HISTORICAL_G0_NAMESPACE"}],
              "G0_G8_qualification": False, "real_orders_sent": 0}
    for name, reader, validator in (
        ("cgroup", observe_cgroup, validate_cgroup),
        ("filesystem", lambda: observe_filesystem(work_root, source_root), validate_filesystem),
    ):
        try:
            observed = reader()
            result[name + "_observation"] = observed
            result[name + "_validated"] = validator(observed, plan)
            result["checks"][name] = True
        except (ValueError, OSError) as error:
            reason = str(error) if isinstance(error, ValueError) else "RESOURCE_OBSERVATION_OS_ERROR"
            result["checks"][name] = False
            result["failures"].append({"check": name, "reason": reason,
                                       "errno": getattr(error, "errno", None)})
    if not result["failures"]:
        result["status"] = "ENVELOPE_OBSERVED_EXECUTION_BLOCKED_NATIVE_FILTER_PROOF_MISSING"
    try:
        proof = require_current_native_namespace_filter(source_binding=source_binding)
        result["native_namespace_filter"] = proof
        result["effective_seccomp_rules"] = "CURRENT_PROCESS_NATIVE_INSTALLATION_AND_DENIALS_OBSERVED"
        result["execution_blockers"] = []
        result["checks"]["native_filter"] = True
        if not result["failures"]:
            result["status"] = "BOUNDED_ENVELOPE_AND_CURRENT_NATIVE_FILTER_OBSERVED"
            result["aggregate_storage_security_qualified"] = True
    except (ValueError, OSError) as error:
        result["checks"]["native_filter"] = False
        result["native_filter_error"] = str(error) if isinstance(error, ValueError) else "RESOURCE_FILTER_LIBRARY_OS_ERROR"
    return result


def require_current_native_namespace_filter(*, source_binding=None):
    from scripts import rc6_native_namespace_filter as native
    return native.assert_current_filter(source_binding=source_binding)


def install_current_native_namespace_filter(*, source_binding, inherited=None):
    from scripts import rc6_native_namespace_filter as native
    return native.install_filter(source_binding=source_binding, inherited=inherited)


def filter_binding(args):
    return {"source_sha": args.source_sha, "source_tree": args.source_tree,
            "plan_sha256": digest(Path(args.plan).read_bytes())}


def exclusive_json(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())


def read_supervisor_filter(path, expected_sha256, work_root):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts and path.is_relative_to(work_root)
            and not any(row.is_symlink() for row in (path, *path.parents))
            and re.fullmatch(r"[0-9a-f]{64}", expected_sha256 or ""),
            "RESOURCE_OWNED_SUPERVISOR_FILTER_CONTROL_REQUIRED")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        details = os.fstat(descriptor)
        require(stat.S_ISREG(details.st_mode) and details.st_uid == os.geteuid()
                and stat.S_IMODE(details.st_mode) == 0o600 and 0 < details.st_size <= 65536,
                "RESOURCE_SUPERVISOR_FILTER_CONTROL_METADATA_REQUIRED")
        raw = os.read(descriptor, 65537)
    finally:
        os.close(descriptor)
    require(len(raw) == details.st_size and digest(raw) == expected_sha256,
            "RESOURCE_SUPERVISOR_FILTER_CONTROL_BYTES_REBOUND")
    return document(raw)


def require_same_envelope(before, after):
    require(not before["failures"], "RESOURCE_INITIAL_ENVELOPE_NOT_ADMITTED")
    require(not after["failures"], "RESOURCE_LIVE_RECHECK_FAILED")
    for key in ("path", "mount_id", "membership", "cpu.max", "memory.max", "memory.swap.max",
                "pids.max", "affinity", "controls_immutable_to_nonroot", "mount_root", "mountpoint",
                "mount_options", "cgroup_namespace_inode", "mount_namespace_inode", "privilege_status"):
        require(before["cgroup_observation"][key] == after["cgroup_observation"][key],
                "RESOURCE_CGROUP_MOVED_OR_LIMIT_CHANGED")
    for key in ("id", "device_number", "mountpoint", "root", "filesystem", "total_bytes",
                "total_inodes", "options", "source_mount_id", "source_options", "all_mounts"):
        require(before["filesystem_observation"][key] == after["filesystem_observation"][key],
                "RESOURCE_FILESYSTEM_REBOUND_OR_LIMIT_CHANGED")
    old_events, new_events = (node["cgroup_observation"]["events"] for node in (before, after))
    require(all(old_events.get(key) == new_events.get(key) for key in ("oom", "oom_kill", "oom_group_kill", "max")),
            "RESOURCE_OOM_OR_HARD_LIMIT_HIT")


def exact_source(sha, tree):
    from scripts import rc6_controlled_governed_runner as governed
    require(re.fullmatch(r"[0-9a-f]{40}", sha or "") and re.fullmatch(r"[0-9a-f]{40}", tree or ""),
            "RESOURCE_EXACT_SOURCE_SHA_TREE_REQUIRED")
    require(governed.git(ROOT, "rev-parse", "HEAD").decode().strip() == sha
            and governed.git(ROOT, "rev-parse", "HEAD^{tree}").decode().strip() == tree,
            "RESOURCE_FROZEN_SOURCE_REBOUND")
    require(not governed.git(ROOT, "status", "--porcelain").strip(),
            "RESOURCE_CLEAN_EXACT_SOURCE_REQUIRED")


def worker(args, plan):
    """Read limits and installed157 in the actual child before any fixture."""
    exact_source(args.source_sha, args.source_tree)
    observed = readiness(args.work_root, ROOT, plan)
    require(not observed["failures"], "RESOURCE_WORKER_LIVE_ENVELOPE_NOT_ADMITTED")
    binding = filter_binding(args)
    parent = read_supervisor_filter(args.supervisor_filter, args.supervisor_filter_sha256, args.work_root)
    proof = install_current_native_namespace_filter(source_binding=binding, inherited=parent)
    require_current_native_namespace_filter(source_binding=binding)
    observed = readiness(args.work_root, ROOT, plan, source_binding=binding)
    require(not observed["failures"] and not observed["execution_blockers"],
            "RESOURCE_WORKER_CURRENT_NATIVE_ENVELOPE_REQUIRED")
    require(not any(token in key.upper() for key in os.environ
                    for token in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "ACCESS_KEY")),
            "RESOURCE_WORKER_CREDENTIAL_ENV_FORBIDDEN")
    paths = [Path(value) for value in (args.worker_controls, args.basetemp, args.junit)]
    require(all(path.is_absolute() and ".." not in path.parts and path.is_relative_to(args.work_root)
                and not any(row.is_symlink() for row in (path, *path.parents)) for path in paths)
            and len(set(paths)) == 3, "RESOURCE_WORKER_WRITES_OUTSIDE_AGGREGATE_MOUNT")
    from scripts.rc6_native_import_provenance import environment_qualification
    installed = environment_qualification(ROOT)
    receipt = Path(args.worker_controls)
    exclusive_json(receipt, {"schema": "porota.rc6.product-resource-worker.v1",
        "source_sha": args.source_sha, "source_tree": args.source_tree,
        "pid": os.getpid(), "parent_pid": os.getppid(), "plan_sha256": digest(Path(args.plan).read_bytes()),
        "before_fixtures": True, "kernel_envelope": observed, "product157": installed,
        "native_namespace_filter": proof, "parent_filter_sha256": args.supervisor_filter_sha256,
        "credential_environment_present": False, "real_orders_sent": 0})
    import pytest
    return pytest.main(["--noconftest", "-c", "/dev/null", "--rootdir=" + str(ROOT),
        "-p", "no:cacheprovider", "-o", "junit_family=legacy",
        "--basetemp=" + args.basetemp, "--junitxml=" + args.junit, "-q",
        *[str(ROOT / suite.partition("::")[0]) + "::" + suite.partition("::")[2]
          for suite in plan["suites"]]])


def verify_native_profile(worker_receipt, kernel, facts, *, source_sha, source_tree, plan_sha256, plan, envelope):
    require(type(worker_receipt) is dict
            and worker_receipt.get("schema") == "porota.rc6.product-resource-worker.v1"
            and worker_receipt.get("source_sha") == source_sha
            and worker_receipt.get("source_tree") == source_tree
            and worker_receipt.get("plan_sha256") == plan_sha256
            and type(worker_receipt.get("pid")) is int and worker_receipt["pid"] == kernel["pid"]
            and type(worker_receipt.get("parent_pid")) is int and worker_receipt["parent_pid"] == os.getpid()
            and worker_receipt.get("before_fixtures") is True
            and type(worker_receipt.get("real_orders_sent")) is int and worker_receipt["real_orders_sent"] == 0,
            "RESOURCE_ACTUAL_NATIVE_WORKER_BINDING_REQUIRED")
    from scripts import rc6_native_namespace_filter as native
    binding = {"source_sha": source_sha, "source_tree": source_tree, "plan_sha256": plan_sha256}
    parent = native.validate_evidence(envelope.get("native_namespace_filter"), source_binding=binding)
    child = native.validate_evidence(worker_receipt.get("native_namespace_filter"), source_binding=binding)
    inheritance = native.validate_inheritance_evidence(child, parent, source_binding=binding)
    require(child["identity"]["pid"] == kernel["pid"] and parent["identity"]["pid"] == os.getpid()
            and parent["dumpable"] == 0
            and child["filters_before"] == parent["filters_after"]
            and type(inheritance) is dict and inheritance.get("fork_exec_inheritance_observed") is True
            and inheritance.get("parent_receipt_sha256") == digest(canonical(parent))
            and worker_receipt.get("parent_filter_sha256") == digest(canonical(parent))
            and worker_receipt.get("credential_environment_present") is False,
            "RESOURCE_ACTUAL_WORKER_FILTER_LOAD_AND_INHERITANCE_REQUIRED")
    installed = worker_receipt.get("product157", {})
    require(all(type(installed.get(key)) is int and installed[key] == 157
                for key in ("installed_total", "installed_unique_total", "expected_total")),
            "RESOURCE_NATIVE_ACTUAL157_REQUIRED")
    require_same_envelope(envelope, worker_receipt["kernel_envelope"])
    expected = sorted((suite.partition("::")[0][:-3].replace("/", "."), suite.partition("::")[2])
                      for suite in plan["suites"])
    require(type(facts) is dict and facts.get("cases") == len(expected)
            and sorted(tuple(pair) for pair in facts.get("identities", [])) == expected,
            "RESOURCE_COMPLETE_ORIGINAL_PROFILE_IDENTITIES_REQUIRED")


def owned_execution(args, plan, plan_sha256, envelope, output, owner_check, g0_manifest):
    binding = filter_binding(args)
    supervisor_filter = require_current_native_namespace_filter(source_binding=binding)
    from scripts import rc6_architectural_gates as gates
    from scripts import rc6_authenticated_fixture_lifecycle as lifecycle
    from scripts import rc6_controlled_governed_runner as governed
    from scripts.rc6_actions_custody import Github
    from scripts.rc6_development_checks import junit_facts
    from scripts.rc6_native_import_provenance import environment_qualification
    client = Github(os.environ.get("GH_TOKEN"))

    def bounded_get(path):
        row = client.request(path)
        if path.startswith("/actions/artifacts/"):
            require(type(row.get("size_in_bytes")) is int
                    and row["size_in_bytes"] <= plan["native_g0_artifact_maximum_bytes"],
                    "RESOURCE_G0_ARTIFACT_EXCEEDS_ADMITTED_BUDGET")
        return row

    # Authenticate original native bytes/DAG, not just a JSON checks object.
    # G0 only: the bounded consumer profile cannot authorize G2 or later.
    require(type(g0_manifest.get("artifacts")) is list and len(g0_manifest["artifacts"]) == 1,
            "RESOURCE_EXACT_SINGLE_NATIVE_G0_REQUIRED")
    g0 = gates.verify_manifest(g0_manifest, source_sha=args.source_sha, source_tree=args.source_tree,
        target_gate="G1.311", evidence_root=output / "native-g0", get=bounded_get)
    require([row["gate"] for row in g0["authenticated_receipts"]] == ["G0"],
            "RESOURCE_NATIVE_G0_ONLY_REQUIRED")
    owner_check()
    installed = environment_qualification(ROOT)
    live = readiness(args.work_root, ROOT, plan, source_binding=binding)
    require(not live["failures"] and not live["execution_blockers"], "RESOURCE_PRELAUNCH_LIVE_ENVELOPE_REQUIRED")
    before = governed.source_pin(ROOT, args.source_sha, args.source_tree)
    namespace = lifecycle.create_namespace(Path(args.work_root), {
        "candidate_sha": args.source_sha, "candidate_tree": args.source_tree,
        "producer": "PRODUCT_RESOURCE_COMPATIBILITY_BOUNDED_SOURCE_PROFILE",
        "attempt_id": os.environ["GITHUB_RUN_ID"] + ":1", "owner_id": args.owner_session,
        "runner_class": "DIAGNOSTIC", "workload_fingerprint": plan_sha256})
    temporary = namespace.path / "t"
    temporary.mkdir(mode=0o700)
    parent_control = namespace.path / "supervisor-filter.json"
    exclusive_json(parent_control, supervisor_filter)
    parent_control_sha256 = digest(canonical(supervisor_filter))
    environment = {key: value for key, value in os.environ.items()
        if not any(token in key.upper() for token in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "ACCESS_KEY"))
        and key not in ("GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY")}
    environment.update({"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(temporary), "TMP": str(temporary), "TEMP": str(temporary),
        "DATA_DIR": str(temporary / "data"), "LOG_DIR": str(temporary / "logs"),
        "DB_PATH": str(temporary / "trading_system.db"), "HIST_DB_PATH": str(temporary / "history.db"),
        "TESTING_LOG_PATH": str(temporary / "testing.jsonl"),
        "DASHBOARD_SESSION_STORE": str(temporary / "dashboard_sessions.json")})
    command = [sys.executable, "-I", "-B", str(Path(__file__).absolute()), "--worker",
        "--plan", str(args.plan), "--source-sha", args.source_sha, "--source-tree", args.source_tree,
        "--work-root", str(args.work_root), "--worker-controls", str(namespace.path / "worker.json"),
        "--supervisor-filter", str(parent_control), "--supervisor-filter-sha256", parent_control_sha256,
        "--basetemp", str(namespace.path / "p"), "--junit", str(namespace.path / "junit.xml")]
    kernel, fin = lifecycle.execute_owned(namespace, command, cwd=namespace.path, environ=environment,
        log_relative="native.log", timeout_seconds=plan["timeout_seconds"], fin_label="product-resource")
    # No post-Source/RAW reads or cleanup until the original kernel FIN exists.
    required = ["native.log", "producer-owned-fin-product-resource.json", "supervisor-filter.json"]
    for name in ("worker.json", "junit.xml"):
        if (namespace.path / name).is_file():
            required.append(name)
    captured = lifecycle.capture_required_evidence(namespace, fin, output / "native-profile", required)
    rows = {row["relative_source"]: row for row in captured.files}
    facts, junit_validation_error = None, None
    if "junit.xml" in rows:
        try:
            facts = junit_facts((captured.path / rows["junit.xml"]["capture_file"]).read_bytes())
        except (ValueError, ET.ParseError) as error:
            junit_validation_error = type(error).__name__
    cleanup = lifecycle.cleanup_namespace(namespace, fin, captured)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            stream.write("safe_profile_upload=true\n")
    owner_check()
    after = governed.source_pin(ROOT, args.source_sha, args.source_tree)
    atime = governed.compare_source(before, after)
    recheck = readiness(args.work_root, ROOT, plan, source_binding=binding)
    require_same_envelope(envelope, recheck)
    worker_validation_error = None
    try:
        require("worker.json" in rows, "RESOURCE_NATIVE_WORKER_CONTROL_MISSING")
        worker_receipt = document((captured.path / rows["worker.json"]["capture_file"]).read_bytes())
        verify_native_profile(worker_receipt, kernel, facts, source_sha=args.source_sha, source_tree=args.source_tree,
            plan_sha256=plan_sha256, plan=plan, envelope=envelope)
    except (ValueError, KeyError, TypeError) as error:
        literal = str(error)
        worker_validation_error = literal if re.fullmatch(r"[A-Z][A-Z0-9_]+", literal) else type(error).__name__
    passed = kernel["returncode"] == 0 and facts is not None and not any(
        facts[key] for key in ("failures", "errors", "skipped")) and worker_validation_error is None
    return {"schema": "porota.rc6.product-resource-profile.v1", "scope": SCOPE,
        "status": "PASS_BOUNDED_SOURCE_PROFILE_ONLY" if passed else "RED_BOUNDED_SOURCE_PROFILE",
        "source_sha": args.source_sha, "source_tree": args.source_tree, "plan_sha256": plan_sha256,
        "kernel": kernel, "native_fin": True, "cleanup": cleanup, "junit": facts,
        "junit_validation_error": junit_validation_error,
        "worker_validation_error": worker_validation_error,
        "product157_before_fixtures": installed, "native_G0_artifact_origins": g0["artifact_origins"],
        "source_before_sha256": digest(canonical(before)), "source_after_sha256": digest(canonical(after)),
        "source_atime_observations": atime, "source_unchanged": True,
        "source_export_or_tar_or_venv_duplicated": False,
        "envelope_before": envelope, "envelope_after": recheck,
        "product_resource_compatibility": "NO_VERIFICADO", "whole_droplet_compatibility": "NO_VERIFICADO",
        "persistent_disk_compatibility": "NO_VERIFICADO", "G0_G8_qualification": False,
        "effective_seccomp_rules": "SUPERVISOR_AND_WORKER_OWN_NATIVE_INSTALLATION_AND_INHERITANCE_OBSERVED" if passed else "NO_VERIFICADO",
        "aggregate_storage_security_qualified": passed,
        "supervisor_native_namespace_filter": supervisor_filter,
        "tested_candidate_build_once_artifact": False, "deployed": False,
        "recurring_infrastructure_cost_usd": 0, "real_orders_sent": 0}


def save_control(output, value, name="readiness.json"):
    require(name in ("readiness.json", "profile.json"), "RESOURCE_LITERAL_CONTROL_NAME_REQUIRED")
    path = output / name
    require(not path.is_symlink(), "RESOURCE_CONTROL_ALIAS_FORBIDDEN")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, default=PLAN)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--owner-session")
    parser.add_argument("--launch-receipt-url")
    parser.add_argument("--readiness", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--worker-controls")
    parser.add_argument("--supervisor-filter")
    parser.add_argument("--supervisor-filter-sha256")
    parser.add_argument("--basetemp")
    parser.add_argument("--junit")
    args = parser.parse_args(argv)
    require(sum((args.readiness, args.execute, args.worker)) == 1, "RESOURCE_ONE_EXACT_MODE_REQUIRED")
    plan, plan_sha256 = exact_plan(args.plan)
    exact_source(args.source_sha, args.source_tree)
    if args.worker:
        return worker(args, plan)
    require(args.output is not None and args.output.is_absolute()
            and ".." not in args.output.parts
            and not args.output.is_relative_to(ROOT)
            and not any(path.is_symlink() for path in (args.output, *args.output.parents)),
            "RESOURCE_OUTPUT_OUTSIDE_SOURCE_REQUIRED")
    args.output.mkdir(mode=0o700)
    binding = filter_binding(args)
    envelope = readiness(args.work_root, ROOT, plan, source_binding=binding)
    # An initial Seccomp=0 is expected when the launcher drops capabilities
    # then execs this supervisor. Only this cheap self-installation occurs
    # before final admission; failed CPU/RAM/filesystem limits still prohibit
    # G0 downloads, Product157 imports and every fixture/consumer.
    try:
        install_current_native_namespace_filter(source_binding=binding)
        envelope = readiness(args.work_root, ROOT, plan, source_binding=binding)
    except (ValueError, OSError) as error:
        envelope["native_filter_error"] = str(error) if isinstance(error, ValueError) else "RESOURCE_FILTER_LIBRARY_OS_ERROR"
    envelope.update(schema="porota.rc6.product-resource-readiness.v1",
        source_sha=args.source_sha, source_tree=args.source_tree, plan_sha256=plan_sha256,
        run_id=os.environ.get("GITHUB_RUN_ID"), run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT"),
        recurring_infrastructure_cost_usd=0, persistent_infrastructure_changes=False,
        source_export_or_tar_or_venv_created=False)
    save_control(args.output, envelope)
    if args.readiness:
        print(canonical({key: envelope[key] for key in ("status", "failures", "execution_blockers", "workload_started", "real_orders_sent")}).decode(), end="")
        return int(bool(envelope["failures"] or envelope["execution_blockers"]))
    require(not envelope["failures"], "RESOURCE_ENVELOPE_BLOCKED_NO_WORKLOAD_LAUNCHED")
    require(not envelope["execution_blockers"], FILTER_BLOCKER)
    require_current_native_namespace_filter(source_binding=binding)
    require(args.output.is_relative_to(args.work_root), "RESOURCE_ALL_EXECUTION_WRITES_WITHIN_AGGREGATE_MOUNT_REQUIRED")
    require(os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("GITHUB_REPOSITORY") == "mbalbo2023/Porota-trading"
            and os.environ.get("GITHUB_ACTOR") == "mbalbo2023" and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and os.environ.get("GITHUB_RUN_ATTEMPT") == "1", "RESOURCE_CANONICAL_EXPLICIT_FIRST_DISPATCH_REQUIRED")
    from scripts.rc6_actions_custody import Github, verify_owner
    client = Github(os.environ.get("GH_TOKEN"))

    def owner_check():
        return verify_owner(client, args.launch_receipt_url, sha=args.source_sha, tree=args.source_tree,
            owner=args.owner_session, plan_sha256=plan_sha256, authorization_field="RC6_DEVELOPMENT_CHECKS_AUTHORIZATION")

    owner_check()
    identifier = args.launch_receipt_url.rsplit("-", 1)[1]
    authority = client.request("/issues/comments/" + identifier)
    fields = {}
    for line in authority["body"].splitlines():
        pair = re.fullmatch(r"([A-Z][A-Z0-9_]*)=(.*)", line)
        if pair:
            require(pair[1] not in fields, "RESOURCE_DUPLICATE_AUTHORIZATION_FIELD")
            fields[pair[1]] = pair[2]
    require(fields.get("PRODUCT_RESOURCE_PROFILE_AUTHORIZATION") == "APPROVED"
            and fields.get("RESOURCE_PLAN_SHA256") == plan_sha256,
            "RESOURCE_EXACT_PROFILE_AUTHORIZATION_REQUIRED")
    manifest = document(fields.get("PREREQUISITES_MANIFEST_JSON", "null"))
    require(type(manifest) is dict, "RESOURCE_NATIVE_G0_MANIFEST_REQUIRED")
    result = owned_execution(args, plan, plan_sha256, envelope, args.output, owner_check, manifest)
    save_control(args.output, result, "profile.json")
    print(canonical({key: result[key] for key in ("status", "scope", "real_orders_sent")}).decode(), end="")
    return int(result["status"] != "PASS_BOUNDED_SOURCE_PROFILE_ONLY")


if __name__ == "__main__":
    raise SystemExit(main())
