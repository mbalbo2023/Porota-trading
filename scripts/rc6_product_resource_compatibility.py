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
import ast
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import stat
import sys
import threading
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

MIB = 1024**2
GIB = 1024**3
PLAN = ROOT / "ops/policy/rc6-product-resource-compatibility-v1.json"
PLAN_V2 = ROOT / "ops/policy/rc6-product-resource-compatibility-v2.json"
SCOPE = "BOUNDED_ORIGINAL_PAPER_CONSUMERS_NOT_G0_G8_OR_DROPLET_QUALIFICATION"
COMPUTE_SCOPE = "PURE_CANDIDATE_RESOURCE_OBSERVATION_NOT_REPRESENTATIVE_WORKLOAD"
FILTER_BLOCKER = "RESOURCE_CURRENT_NATIVE_NAMESPACE_FILTER_INSTALLATION_REQUIRED"
PROTECTED_SUITES = (
    "tests/test_dynamic_risk_and_caucion_sweep_hf6.py::test_concurrent_risk_uses_soft_stop_budget_not_small_position_count",
    "tests/test_dynamic_risk_and_caucion_sweep_hf6.py::test_realized_loss_consumes_capacity_before_new_candidate",
    "tests/test_rc6_final_shared_risk.py::test_concurrent_spot_future_admission_cannot_spend_same_soft_stop_capacity_twice",
    "tests/test_exit_supervision_v17.py::test_max_hold_sin_datos_persiste_y_se_ejecuta_despues_de_reiniciar",
    "tests/test_exit_supervision_v17.py::test_stop_sin_profundidad_no_se_olvida_cuando_recupera_precio",
    "tests/test_exit_supervision_v17.py::test_eod_no_aplica_a_cauciones_ni_futuros_y_frena_entradas_spot",
    "tests/test_exit_ledger_isolation_v17.py::test_lector_continua_con_posiciones_validas_y_cuenta_inconsistencias",
)


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
    version = plan.get("schema")
    require(version in ("porota.rc6.product-resource-compatibility-plan.v1",
                        "porota.rc6.product-resource-compatibility-plan.v2"),
            "RESOURCE_UNKNOWN_CONTRACT_VERSION")
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
    if version.endswith(".v2"):
        required.update(schema=version, filesystem="ext4", tmpfs_charged_to_cgroup_memory=False,
            phase="PROPOSED_PHYSICAL_DISK_PROFILE_REQUIRES_AUTHENTICATED_OWNER_ACCEPTANCE",
            owner_contract_acceptance_required=True, physical_block_device_readback_required=True,
            physical_backing_readback_required=True, representative_full_workload=False,
            protected_risk_exit_eod_original_consumers_required=True,
            telemetry_required=True, telemetry_sampling_interval_milliseconds=10,
            maximum_telemetry_samples=20000, compute_probe_scope=COMPUTE_SCOPE,
            compute_probe_maximum_control_bytes=65536,
            compute_probe_fin_required=True, compute_probe_fixtures_allowed=False,
            compute_probe_database_allowed=False, compute_probe_network_allowed=False,
            compute_probe_financial_ticks_allowed=False, compute_probe_qualification=False,
            original_material_workloads={"G4_catalog_count": 12000, "G4_observations": 60000,
                "G4_factual_paper_exits": 5, "G5_cuts": 1202,
                "G5_storage_maximum_bytes": 512 * MIB, "G5_depth": 32,
                "G5_retention_hours": 9, "G5_temporary_retention_hours": 1},
            original_material_admission_and_completion_required=True,
            artifact_transfer_bytes_inferred=False)
    require(all(type(plan.get(key)) is type(value) and plan[key] == value
                for key, value in required.items()), "RESOURCE_SCOPE_OR_FIXED_LIMIT_CHANGED")
    suites = plan.get("suites")
    require(type(suites) is list and 0 < len(suites) <= 16
            and len(suites) == len(set(suites))
            and all(re.fullmatch(r"tests/test_[A-Za-z0-9_]+\.py::test_[A-Za-z0-9_]+", suite)
                    for suite in suites), "RESOURCE_BOUNDED_ORIGINAL_SUITE_REQUIRED")
    if version.endswith(".v2"):
        legacy, _ = exact_plan(PLAN)
        require(suites == legacy["suites"] + list(PROTECTED_SUITES),
                "RESOURCE_COMPLETE_ORIGINAL_PROTECTED_CONSUMERS_REQUIRED")
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
                     "source": unescape_mount(fields[separator + 2]),
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


def validate_cgroup(observation, plan, *, private_cgroup_required=True, current_pid=None):
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
    current_pid = os.getpid() if current_pid is None else current_pid
    require(type(current_pid) is int and current_pid > 0
            and type(observation["processes"]) is list
            and all(type(pid) is int and pid > 0 for pid in observation["processes"])
            and len(set(observation["processes"])) == len(observation["processes"])
            and current_pid in observation["processes"], "RESOURCE_CURRENT_CGROUP_MEMBERSHIP_REQUIRED")
    require(type(private_cgroup_required) is bool, "RESOURCE_CGROUP_ROLE_REQUIRED")
    if private_cgroup_required:
        require(observation["membership"] == "0::/" and observation["mount_root"] == "/"
                and observation["path"] == observation["mountpoint"]
                and "ro" in observation["mount_options"] and "rw" not in observation["mount_options"],
                "RESOURCE_READONLY_CURRENT_CGROUP_NAMESPACE_ROOT_REQUIRED")
    else:
        # A compute-only control runs no fixtures, DB or worker and grants no
        # resource admission. It can observe a systemd leaf while all visible
        # cgroup controls remain read-only; full consumers retain private root.
        require("ro" in observation["mount_options"] and "rw" not in observation["mount_options"]
                and observation["membership"].startswith("0::/")
                and Path(observation["path"]).is_relative_to(observation["mountpoint"]),
                "RESOURCE_COMPUTE_READONLY_CURRENT_CGROUP_REQUIRED")
    require(all(type(observation[key]) is int and observation[key] > 0
                for key in ("cgroup_namespace_inode", "mount_namespace_inode")),
            "RESOURCE_NAMESPACE_IDENTITY_REQUIRED")
    privileges = validate_privileges(observation["privilege_status"], observation["uid"])
    return {"quota": quota, "period": period, "cpu_limit": quota / period,
            "affinity": affinity, "memory_limit_bytes": memory,
            "memory_current_bytes": current, "memory_peak_bytes": peak,
            "swap_limit_bytes": swap, "pids_limit": pids,
            "readonly_current_cgroup_namespace_root": private_cgroup_required, "privileges": privileges,
            "interpreter_supervisor_children_cache_and_tmpfs_in_same_memory_budget": True}


def select_cgroup_directory_mount(rows, actual_mount_id):
    """Resolve the visible canonical directory, not a count of file mounts."""
    require(type(actual_mount_id) is int and actual_mount_id > 0,
        "RESOURCE_ACTUAL_CGROUP_DIRECTORY_MOUNT_REQUIRED")
    mounts = [row for row in rows if row['id'] == actual_mount_id]
    require(len(mounts) == 1 and mounts[0]['filesystem'] == 'cgroup2'
        and mounts[0]['mountpoint'] == '/sys/fs/cgroup',
        'RESOURCE_ACTUAL_CGROUP_DIRECTORY_MOUNT_REQUIRED')
    require(all('ro' in row['options'] and 'rw' not in row['options']
        for row in rows if row['filesystem'] == 'cgroup2'),
        'RESOURCE_WRITABLE_CGROUP_MOUNT_FORBIDDEN')
    return mounts[0]


def current_cgroup_directory_mount(rows):
    from scripts.rc6_capacity_calibration import descriptor_mount_id
    root = Path('/sys/fs/cgroup')
    require(not any(path.is_symlink() for path in (root, *root.parents)),
        'RESOURCE_CGROUP_ALIAS_FORBIDDEN')
    fd = os.open(root, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        held = os.fstat(fd)
        mount = select_cgroup_directory_mount(rows, descriptor_mount_id(fd))
        named = root.stat(follow_symlinks=False)
        require((held.st_dev, held.st_ino, held.st_mode) == (named.st_dev, named.st_ino, named.st_mode),
            'RESOURCE_CGROUP_DIRECTORY_REBOUND')
        return mount
    finally:
        os.close(fd)


def observe_cgroup():
    from scripts.rc6_capacity_calibration import descriptor_mount_id
    rows = mount_rows(Path("/proc/self/mountinfo").read_text())
    mounted = current_cgroup_directory_mount(rows)
    membership = Path("/proc/self/cgroup").read_text().strip()
    require(membership.startswith("0::/") and "\n" not in membership,
            "RESOURCE_UNIFIED_CURRENT_CGROUP_REQUIRED")
    visible = Path(membership[3:])
    mounted_root = Path(mounted["root"])
    require(visible.is_relative_to(mounted_root), "RESOURCE_CGROUP_NAMESPACE_MAPPING_UNKNOWN")
    location = Path(mounted["mountpoint"]) / visible.relative_to(mounted_root)
    require(not any(path.is_symlink() for path in (location, *location.parents)),
            "RESOURCE_CGROUP_ALIAS_FORBIDDEN")
    files = ("cpu.max", "memory.max", "memory.swap.max", "pids.max",
             "memory.current", "memory.peak", "memory.events", "cpu.stat", "cgroup.procs")
    directory = os.open(location, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        require(descriptor_mount_id(directory) == mounted['id'], 'RESOURCE_CGROUP_LEAF_MOUNT_REBOUND')
        values = {}
        for name in files:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
            try:
                require(stat.S_ISREG(os.fstat(fd).st_mode) and descriptor_mount_id(fd) == mounted['id'],
                    'RESOURCE_CGROUP_CONTROL_MOUNT_ALIAS')
                chunks, total = [], 0
                while True:
                    chunk = os.read(fd, 65537 - total)
                    if not chunk:
                        break
                    total += len(chunk)
                    require(total <= 65536, 'RESOURCE_CGROUP_CONTROL_READ_BOUND')
                    chunks.append(chunk)
                data = b''.join(chunks)
                values[name] = data.decode('utf-8', errors='strict').strip()
            finally:
                os.close(fd)
    finally:
        os.close(directory)
    protected = ("cpu.max", "memory.max", "memory.swap.max", "pids.max", "cgroup.procs")
    values.update({"path": str(location), "membership": membership, "mount_id": mounted["id"],
                   "mount_root": mounted["root"], "mountpoint": mounted["mountpoint"],
                   "mount_options": mounted["options"],
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


def parse_pressure(raw):
    result = {}
    for line in raw.splitlines():
        fields = line.split()
        require(len(fields) == 5 and fields[0] in ("some", "full") and fields[0] not in result,
                "RESOURCE_PRESSURE_READBACK_MALFORMED")
        pairs = [field.split("=", 1) for field in fields[1:]]
        require(all(len(pair) == 2 for pair in pairs)
                and [pair[0] for pair in pairs] == ["avg10", "avg60", "avg300", "total"],
                "RESOURCE_PRESSURE_READBACK_MALFORMED")
        try:
            row = {key: float(value) for key, value in pairs[:3]}
        except ValueError as error:
            raise ValueError("RESOURCE_PRESSURE_READBACK_MALFORMED") from error
        require(all(math.isfinite(value) and 0 <= value <= 100 for value in row.values()),
                "RESOURCE_PRESSURE_READBACK_MALFORMED")
        row["total"] = finite_integer(pairs[3][1], "RESOURCE_PRESSURE_READBACK_MALFORMED")
        result[fields[0]] = row
    require("some" in result, "RESOURCE_PRESSURE_READBACK_MISSING")
    return result


def proc_memory(raw):
    result = {}
    for line in raw.splitlines():
        key, _, value = line.partition(":")
        if key in ("VmRSS", "VmHWM", "VmSwap"):
            fields = value.split()
            require(len(fields) == 2 and fields[1] == "kB" and key not in result,
                    "RESOURCE_PROCESS_MEMORY_READBACK_MALFORMED")
            result[key] = finite_integer(fields[0], "RESOURCE_PROCESS_MEMORY_READBACK_MALFORMED") * 1024
    require(set(result) == {"VmRSS", "VmHWM", "VmSwap"}, "RESOURCE_PROCESS_MEMORY_READBACK_INCOMPLETE")
    return result


def observe_resource_metrics(cgroup_path):
    location = Path(cgroup_path)
    require(location.is_absolute() and not any(row.is_symlink() for row in (location, *location.parents)),
            "RESOURCE_TELEMETRY_LITERAL_CURRENT_CGROUP_REQUIRED")
    started = time.monotonic_ns()
    def read(name):
        return (location / name).read_text().strip()
    counters = {key: finite_integer(read(key), "RESOURCE_TELEMETRY_COUNTER_INVALID")
                for key in ("memory.current", "memory.peak", "memory.swap.current")}
    observed = {"started_monotonic_ns": started,
        "cgroup_path": str(location), **counters,
        "memory.events": parse_counter(read("memory.events")),
        "memory.stat": parse_counter(read("memory.stat")), "cpu.stat": parse_counter(read("cpu.stat")),
        "memory.pressure": parse_pressure(read("memory.pressure")),
        "cpu.pressure": parse_pressure(read("cpu.pressure")),
        "io.pressure": parse_pressure(read("io.pressure")),
        "self_memory": proc_memory(Path("/proc/self/status").read_text()),
        "self_ru_maxrss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024}
    observed["finished_monotonic_ns"] = time.monotonic_ns()
    return observed


class ResourceSampler:
    """Bounded read-only samples; cgroup memory.peak is the kernel high-water."""
    def __init__(self, cgroup_path, *, interval_milliseconds=10, maximum_samples=20000):
        require(type(interval_milliseconds) is int and interval_milliseconds == 10
                and type(maximum_samples) is int and maximum_samples == 20000,
                "RESOURCE_FIXED_TELEMETRY_BOUND_REQUIRED")
        self.path, self.samples, self.errors = cgroup_path, [], []
        self.stop = threading.Event()
        self.interval = interval_milliseconds / 1000
        self.maximum_samples = maximum_samples
        self.thread = None

    def sample(self):
        if len(self.samples) >= self.maximum_samples:
            self.errors.append({"reason": "RESOURCE_TELEMETRY_SAMPLE_BOUND_REACHED", "errno": None})
            return
        try:
            self.samples.append(observe_resource_metrics(self.path))
        except (OSError, ValueError) as error:
            self.errors.append({"reason": str(error) if isinstance(error, ValueError)
                else "RESOURCE_TELEMETRY_READBACK_OS_ERROR", "errno": getattr(error, "errno", None)})

    def __enter__(self):
        self.sample()
        def observe():
            while not self.stop.wait(self.interval):
                self.sample()
                if self.errors:
                    return
        self.thread = threading.Thread(target=observe, name="rc6-resource-readback", daemon=False)
        self.thread.start()
        return self

    def __exit__(self, *unused):
        self.stop.set()
        self.thread.join(timeout=1)
        require(not self.thread.is_alive(), "RESOURCE_TELEMETRY_THREAD_FIN_REQUIRED")
        self.sample()


def summarize_metrics(samples, *, kernel_peak_rss_bytes=None):
    require(type(samples) is list and 2 <= len(samples) <= 20000,
            "RESOURCE_ACTUAL_BEFORE_AFTER_METRICS_REQUIRED")
    rates = []
    for row in samples:
        require(type(row) is dict and type(row.get("cgroup_path")) is str
                and Path(row["cgroup_path"]).is_absolute()
                and all(type(row.get(key)) is int and row[key] >= 0 for key in (
                    "started_monotonic_ns", "finished_monotonic_ns", "memory.current", "memory.peak", "memory.swap.current"))
                and row["finished_monotonic_ns"] >= row["started_monotonic_ns"]
                and row["memory.current"] <= row["memory.peak"]
                and type(row.get("memory.events")) is dict
                and type(row.get("memory.stat")) is dict
                and {"anon", "file"} <= set(row["memory.stat"])
                and all(type(key) is str and type(value) is int and value >= 0
                        for key, value in row["memory.stat"].items())
                and type(row.get("cpu.stat")) is dict and type(row["cpu.stat"].get("usage_usec")) is int
                and row["cpu.stat"]["usage_usec"] >= 0
                and type(row.get("self_memory")) is dict
                and all(type(row["self_memory"].get(key)) is int and row["self_memory"][key] >= 0
                        for key in ("VmRSS", "VmHWM", "VmSwap"))
                and row["self_memory"]["VmRSS"] <= row["self_memory"]["VmHWM"],
                "RESOURCE_TYPED_ACTUAL_TELEMETRY_REQUIRED")
        for key in ("memory.pressure", "cpu.pressure", "io.pressure"):
            pressure = row.get(key)
            require(type(pressure) is dict and "some" in pressure
                    and set(pressure) <= {"some", "full"}, "RESOURCE_PRESSURE_READBACK_MALFORMED")
            for value in pressure.values():
                require(type(value) is dict and set(value) == {"avg10", "avg60", "avg300", "total"}
                        and all(type(value[name]) in (int, float) and math.isfinite(value[name])
                                and 0 <= value[name] <= 100 for name in ("avg10", "avg60", "avg300"))
                        and type(value["total"]) is int and value["total"] >= 0,
                        "RESOURCE_PRESSURE_READBACK_MALFORMED")
    for before, after in zip(samples, samples[1:]):
        elapsed = after["started_monotonic_ns"] - before["started_monotonic_ns"]
        require(elapsed > 0 and after["cgroup_path"] == before["cgroup_path"],
                "RESOURCE_TELEMETRY_CLOCK_OR_CGROUP_REBOUND")
        usage = after["cpu.stat"]["usage_usec"] - before["cpu.stat"]["usage_usec"]
        require(usage >= 0, "RESOURCE_CPU_STAT_COUNTER_RECYCLED")
        require(after["memory.peak"] >= before["memory.peak"], "RESOURCE_MEMORY_PEAK_COUNTER_RECYCLED")
        for key in ("memory.pressure", "cpu.pressure", "io.pressure"):
            require(set(before[key]) == set(after[key])
                    and all(after[key][mode]["total"] >= before[key][mode]["total"] for mode in before[key]),
                    "RESOURCE_PRESSURE_COUNTER_RECYCLED")
        rates.append(usage * 100000 / elapsed)
    baseline, final = samples[0], samples[-1]
    keys = ("oom", "oom_kill", "oom_group_kill", "max")
    require(all(type(row["memory.events"].get(key)) is int and row["memory.events"][key] >= 0
                and baseline["memory.events"].get(key) == row["memory.events"][key]
                for row in samples for key in keys), "RESOURCE_OOM_OR_HARD_LIMIT_HIT")
    require(all(row["memory.swap.current"] == 0 and row["self_memory"]["VmSwap"] == 0
                for row in samples), "RESOURCE_SWAP_OBSERVED")
    return {"sample_count": len(samples), "sampling_interval_milliseconds": 10,
        "memory_current_sampled_peak_bytes": max(row["memory.current"] for row in samples),
        "memory_kernel_peak_bytes": max(row["memory.peak"] for row in samples),
        "self_rss_sampled_peak_bytes": max(row["self_memory"]["VmRSS"] for row in samples),
        "self_hwm_readback_bytes": max(row["self_memory"]["VmHWM"] for row in samples),
        "owned_worker_kernel_peak_rss_bytes": kernel_peak_rss_bytes,
        "swap_current_sampled_peak_bytes": 0, "cpu_sampled_peak_percent": max(rates),
        "cpu_usage_delta_microseconds": final["cpu.stat"]["usage_usec"] - baseline["cpu.stat"]["usage_usec"],
        "memory_events_before": baseline["memory.events"], "memory_events_after": final["memory.events"],
        "pressure_before": {key: baseline[key] for key in ("memory.pressure", "cpu.pressure", "io.pressure")},
        "pressure_after": {key: final[key] for key in ("memory.pressure", "cpu.pressure", "io.pressure")},
        "cpu_peak_is_sampled_interval_rate_not_continuous_peak": True,
        "self_rss_does_not_include_other_processes": True, "cgroup_memory_includes_cache_and_all_members": True}


def original_compute_controls():
    """Extract literal arguments from the original pure regression functions.

    No test module is imported and no fixture, DB or financial tick is run.
    The five original capacity calls and two emergency guards remain exact.
    """
    path = ROOT / "tests/test_dynamic_risk_and_caucion_sweep_hf6.py"
    raw = path.read_bytes()
    wanted = (
        "test_concurrent_risk_uses_soft_stop_budget_not_small_position_count",
        "test_realized_gains_cannot_be_netted_into_capacity",
        "test_realized_loss_consumes_capacity_before_new_candidate",
        "test_emergency_position_cap_is_separate_runaway_guard",
    )
    functions = {node.name: node for node in ast.parse(raw).body if isinstance(node, ast.FunctionDef)
                 and node.name in wanted}
    require(set(functions) == set(wanted), "RESOURCE_ORIGINAL_PURE_REGRESSIONS_REQUIRED")
    controls = []
    for name in wanted:
        for call in ast.walk(functions[name]):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) \
                    and call.func.id in ("capacity", "emergency_position_guard"):
                require(all(isinstance(value, ast.Constant) and type(value.value) in (str, int)
                            for value in call.args)
                        and all(keyword.arg is not None and isinstance(keyword.value, ast.Constant)
                                and type(keyword.value.value) in (str, int) for keyword in call.keywords),
                        "RESOURCE_PURE_REGRESSION_LITERAL_ARGUMENTS_REQUIRED")
                controls.append({"regression": name, "function": call.func.id,
                    "args": [value.value for value in call.args],
                    "kwargs": {keyword.arg: keyword.value.value for keyword in call.keywords}})
    require([row["function"] for row in controls] == ["capacity"] * 5 + ["emergency_position_guard"] * 2,
            "RESOURCE_EXACT_ORIGINAL_PURE_CONTROL_COUNTS_REQUIRED")
    return controls, digest(raw)


def run_original_compute_controls():
    from dataclasses import asdict
    from decimal import Decimal
    import de_concurrent_risk_capacity_hf6 as candidate
    require(Path(candidate.__file__).absolute() == ROOT / "de_concurrent_risk_capacity_hf6.py",
            "RESOURCE_REAL_CANDIDATE_PURE_IMPORT_REBOUND")
    controls, regression_sha256 = original_compute_controls()
    expected = [(True, "5000"), (False, "5000"), (True, "11000"),
                (True, "6000"), (False, "2000"), "", "EMERGENCY_POSITION_CAP"]
    outputs = []
    for request, assertion in zip(controls, expected):
        value = getattr(candidate, request["function"])(*request["args"], **request["kwargs"])
        if request["function"] == "capacity":
            require(value.admitted is assertion[0] and value.remaining_before_candidate == Decimal(assertion[1]),
                    "RESOURCE_REAL_CANDIDATE_PURE_REGRESSION_RED")
            value = {key: str(item) if isinstance(item, Decimal) else item for key, item in asdict(value).items()}
        else:
            require(value == assertion, "RESOURCE_REAL_CANDIDATE_PURE_REGRESSION_RED")
        outputs.append(value)
    return {"original_regression_sha256": regression_sha256,
        "candidate_module_sha256": digest(Path(candidate.__file__).read_bytes()),
        "inputs": controls, "inputs_sha256": digest(canonical(controls)),
        "actual_outputs": outputs, "actual_outputs_sha256": digest(canonical(outputs)),
        "actual_capacity_calls": 5, "actual_emergency_guard_calls": 2,
        "repetitions": 1, "fixtures_created": False, "financial_ticks": 0,
        "db_calls": 0, "network_calls": 0, "real_orders_sent": 0}


def compute_probe_result(*, source_sha, source_tree, unit, plan_path=PLAN_V2):
    """Called only by the certified bounded ROOT broker actor, before authentic FIN."""
    require(re.fullmatch(r"rc6-native-[0-9a-f]{32}\.service", unit or "") is not None,
            "RESOURCE_LITERAL_OWN_COMPUTE_UNIT_REQUIRED")
    plan, plan_sha256 = exact_plan(plan_path)
    require(plan["schema"].endswith(".v2"), "RESOURCE_COMPUTE_VERSIONED_PLAN_REQUIRED")
    exact_source(source_sha, source_tree)
    source_mount = containing_mount(ROOT, mount_rows(Path("/proc/self/mountinfo").read_text()))
    require("ro" in source_mount["options"] and "rw" not in source_mount["options"],
            "RESOURCE_COMPUTE_SOURCE_READONLY_REQUIRED")
    # The broker's authenticated NONROOT drop already installs this exact
    # filter with its original Source binding. Resource policy has a separate
    # hash; never reinstall or relabel the existing filter under another hash.
    binding = {"source_sha": source_sha, "source_tree": source_tree}
    proof = install_current_native_namespace_filter(source_binding=binding)
    require_current_native_namespace_filter(source_binding=binding)
    envelope = observe_cgroup()
    envelope["cpuset.cpus.effective"] = (Path(envelope["path"]) / "cpuset.cpus.effective").read_text().strip()
    limits = validate_cgroup(envelope, plan, private_cgroup_required=False)
    require(envelope["membership"] == "0::/system.slice/" + unit,
            "RESOURCE_ACTUAL_OWN_COMPUTE_UNIT_MEMBERSHIP_REQUIRED")
    require(limits["cpu_limit"] == 1 and limits["memory_limit_bytes"] == GIB,
            "RESOURCE_COMPUTE_EXACT_ONE_CPU_ONE_GIB_REQUIRED")
    require(envelope["cpuset.cpus.effective"] == str(envelope["affinity"][0]),
            "RESOURCE_COMPUTE_KERNEL_SINGLE_CPUSET_REQUIRED")
    with ResourceSampler(envelope["path"]) as observed:
        controls = run_original_compute_controls()
    require(not observed.errors, "RESOURCE_COMPUTE_TELEMETRY_READBACK_REQUIRED")
    summary = summarize_metrics(observed.samples)
    require(summary["memory_kernel_peak_bytes"] <= GIB, "RESOURCE_MEMORY_ENVELOPE_ALREADY_EXCEEDED")
    recheck = observe_cgroup()
    recheck["cpuset.cpus.effective"] = (Path(recheck["path"]) / "cpuset.cpus.effective").read_text().strip()
    validate_cgroup(recheck, plan, private_cgroup_required=False)
    require(all(envelope[key] == recheck[key] for key in ("path", "membership", "mount_id", "cpu.max",
        "memory.max", "memory.swap.max", "pids.max", "affinity", "uid", "privilege_status", "cpuset.cpus.effective")),
        "RESOURCE_COMPUTE_LIVE_CGROUP_OR_PRIVILEGE_REBOUND")
    result = {"schema": "porota.rc6.compute-resource-observation.v1", "scope": COMPUTE_SCOPE,
        "status": "PURE_CANDIDATE_CONTROLS_OBSERVED_PENDING_EXTERNAL_FIN", "unit": unit,
        "source_sha": source_sha, "source_tree": source_tree, "plan_sha256": plan_sha256,
        "identity": proof["identity"], "native_namespace_filter": proof,
        "kernel_envelope_before": envelope, "kernel_envelope_after": recheck,
        "resource_summary": summary, "resource_samples": observed.samples, "compute_controls": controls,
        "source_mount": source_mount, "source_readonly": True, "data_or_fixture_writes": False,
        "persistent_disk_compatibility": "NO_VERIFICADO", "disk_equivalence_claimed": False,
        "product_resource_compatibility": "NO_VERIFICADO", "whole_droplet_compatibility": "NO_VERIFICADO",
        "representative_full_workload": False, "original_G4_G5_started": False,
        "artifact_transfer_bytes": None, "artifact_transfer_bytes_inferred": False,
        "FIN": "PENDING_ORIGINAL_EXTERNAL_NATIVE_MANAGER", "G0_G8_qualification": False,
        "controller_memory_not_in_compute_envelope": True,
        "full_profile_blocker": "AUTHENTIC_G0_PHYSICAL_BOUNDED_STORAGE_AND_ORIGINAL_WORKLOAD_REQUIRED",
        "recurring_infrastructure_cost_usd": 0, "real_orders_sent": 0}
    require(len(canonical(result)) <= plan["compute_probe_maximum_control_bytes"],
            "RESOURCE_COMPUTE_FIXED_CONTROL_BUDGET_EXCEEDED")
    return result


def observe_physical_backing(mount, mounts, device_number, *, ancestors=()):
    """Read actual block identity and loop backing; unknown is never a disk PASS."""
    result = {"status": "UNKNOWN", "filesystem_device_number": device_number,
              "mount_source": mount["source"], "disk_equivalence_claimed": False}
    try:
        require(len(ancestors) < 8 and device_number not in ancestors,
                "RESOURCE_PHYSICAL_BLOCK_BACKING_CYCLE_OR_DEPTH_UNKNOWN")
        source = Path(mount["source"])
        require(source.is_absolute() and ".." not in source.parts
                and not any(path.is_symlink() for path in (source, *source.parents)),
                "RESOURCE_PHYSICAL_LITERAL_BLOCK_SOURCE_REQUIRED")
        details = source.lstat()
        require(stat.S_ISBLK(details.st_mode) and details.st_rdev == device_number,
                "RESOURCE_PHYSICAL_BLOCK_DEVICE_IDENTITY_REQUIRED")
        result.update(block_rdev=details.st_rdev, block_device_inode=details.st_ino)
        sysfs = Path("/sys/dev/block") / (str(os.major(device_number)) + ":" + str(os.minor(device_number)))
        require(sysfs.is_symlink(), "RESOURCE_PHYSICAL_KERNEL_BLOCK_MAPPING_REQUIRED")
        kernel_device = sysfs.resolve(strict=True)
        backing = kernel_device / "loop/backing_file"
        if backing.exists():
            raw_path = backing.read_text().strip()
            path = Path(raw_path)
            require(path.is_absolute() and ".." not in path.parts
                    and not any(row.is_symlink() for row in (path, *path.parents)),
                    "RESOURCE_PHYSICAL_LITERAL_LOOP_BACKING_REQUIRED")
            before = path.lstat()
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size > 0
                    and before.st_blocks * 512 >= before.st_size,
                    "RESOURCE_PHYSICAL_FULLY_ALLOCATED_LOOP_BACKING_REQUIRED")
            filesystem = containing_mount(path, mounts)
            require(filesystem["filesystem"] in ("ext4", "xfs") and filesystem["id"] != mount["id"],
                    "RESOURCE_PHYSICAL_LOOP_BACKING_NOT_PERSISTENT")
            parent = observe_physical_backing(filesystem, mounts, before.st_dev,
                ancestors=(*ancestors, device_number))
            require(parent["status"] == "BLOCK_AND_BACKING_READBACK_OBSERVED",
                    "RESOURCE_PHYSICAL_LOOP_BACKING_ANCESTRY_UNVERIFIED")
            result.update(backend="FULLY_ALLOCATED_LOOP_ON_PHYSICAL_FILESYSTEM",
                backing_path=str(path), backing_bytes=before.st_size, backing_allocated_bytes=before.st_blocks * 512,
                backing_device_number=before.st_dev, backing_inode=before.st_ino,
                backing_filesystem=filesystem["filesystem"], backing_mount_id=filesystem["id"],
                backing_ancestry=parent)
            require(before == path.lstat(), "RESOURCE_PHYSICAL_LOOP_BACKING_CHANGED_DURING_READBACK")
        else:
            require(not kernel_device.is_relative_to("/sys/devices/virtual"),
                    "RESOURCE_PHYSICAL_VIRTUAL_BLOCK_BACKING_UNKNOWN")
            result.update(backend="KERNEL_PHYSICAL_BLOCK_DEVICE", backing_bytes=None)
        result["status"] = "BLOCK_AND_BACKING_READBACK_OBSERVED"
    except (ValueError, OSError) as error:
        result.update(reason=str(error) if isinstance(error, ValueError) else "RESOURCE_PHYSICAL_BACKING_OS_ERROR",
                      errno=getattr(error, "errno", None))
    return result


def validate_physical_backing(observation, plan):
    backing = observation.get("physical_backing")
    require(type(backing) is dict and backing.get("status") == "BLOCK_AND_BACKING_READBACK_OBSERVED",
            "RESOURCE_PHYSICAL_BLOCK_AND_BACKING_READBACK_REQUIRED")
    require(type(backing.get("block_rdev")) is int and backing["block_rdev"] == observation["device_number"]
            == backing.get("filesystem_device_number") and type(backing.get("block_device_inode")) is int
            and backing["block_device_inode"] > 0, "RESOURCE_PHYSICAL_BLOCK_DEVICE_REBOUND")
    def verify_ancestry(node, seen=()):
        require(type(node) is dict and len(seen) < 8
                and node.get("status") == "BLOCK_AND_BACKING_READBACK_OBSERVED"
                and type(node.get("block_rdev")) is int and node["block_rdev"] not in seen
                and node.get("filesystem_device_number") == node["block_rdev"]
                and type(node.get("block_device_inode")) is int and node["block_device_inode"] > 0,
                "RESOURCE_PHYSICAL_LOOP_BACKING_ANCESTRY_UNVERIFIED")
        if node.get("backend") == "FULLY_ALLOCATED_LOOP_ON_PHYSICAL_FILESYSTEM":
            parent = node.get("backing_ancestry")
            require(type(parent) is dict and parent.get("block_rdev") == node.get("backing_device_number")
                    and node.get("backing_filesystem") in ("ext4", "xfs")
                    and type(node.get("backing_bytes")) is int and node["backing_bytes"] > 0
                    and type(node.get("backing_allocated_bytes")) is int
                    and node["backing_allocated_bytes"] >= node["backing_bytes"]
                    and type(node.get("backing_inode")) is int and node["backing_inode"] > 0,
                    "RESOURCE_PHYSICAL_LOOP_BACKING_ANCESTRY_UNVERIFIED")
            verify_ancestry(parent, (*seen, node["block_rdev"]))
        else:
            require(node.get("backend") == "KERNEL_PHYSICAL_BLOCK_DEVICE",
                    "RESOURCE_PHYSICAL_UNVERIFIED_BLOCK_BACKEND")
    if backing.get("backend") == "FULLY_ALLOCATED_LOOP_ON_PHYSICAL_FILESYSTEM":
        require(backing.get("backing_filesystem") in ("ext4", "xfs")
                and type(backing.get("backing_bytes")) is int
                and 0 < observation["total_bytes"] <= backing["backing_bytes"] <= plan["storage_maximum_bytes"]
                and type(backing.get("backing_allocated_bytes")) is int
                and backing["backing_allocated_bytes"] >= backing["backing_bytes"]
                and type(backing.get("backing_device_number")) is int
                and backing["backing_device_number"] != observation["device_number"]
                and type(backing.get("backing_inode")) is int and backing["backing_inode"] > 0,
                "RESOURCE_PHYSICAL_LOOP_BYTES_OR_FILESYSTEM_REQUIRED")
        ancestry = backing.get("backing_ancestry")
        require(type(ancestry) is dict and ancestry.get("status") == "BLOCK_AND_BACKING_READBACK_OBSERVED"
                and type(ancestry.get("block_rdev")) is int
                and ancestry["block_rdev"] == backing["backing_device_number"]
                and ancestry.get("backend") in ("KERNEL_PHYSICAL_BLOCK_DEVICE", "FULLY_ALLOCATED_LOOP_ON_PHYSICAL_FILESYSTEM"),
                "RESOURCE_PHYSICAL_LOOP_BACKING_ANCESTRY_UNVERIFIED")
        verify_ancestry(ancestry, (backing["block_rdev"],))
    else:
        require(backing.get("backend") == "KERNEL_PHYSICAL_BLOCK_DEVICE",
                "RESOURCE_PHYSICAL_UNVERIFIED_BLOCK_BACKEND")
    return {"native_block_and_backing_observed": True, "backend": backing["backend"],
            "persistent_disk_latency_or_durability_qualified": False}


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
    physical = validate_physical_backing(observation, plan) if plan["schema"].endswith(".v2") else None
    return {"aggregate_storage_limit_bytes": observation["total_bytes"],
            "aggregate_inode_limit": observation["total_inodes"],
            "available_bytes": observation["available_bytes"], "free_inodes": observation["free_inodes"],
            "filesystem": observation["filesystem"], "mount_id": observation["id"],
            "source_mount_id": observation["source_mount_id"],
            "topology": topology, "physical_backing_readback": physical,
            "aggregate_storage_security_qualified": False,
            "persistent_disk_latency_or_durability_qualified": False}


def observe_filesystem(work_root, source_root):
    root = Path(work_root).absolute()
    require(root.is_dir() and not any(path.is_symlink() for path in (root, *root.parents)),
            "RESOURCE_LITERAL_EXISTING_WORK_ROOT_REQUIRED")
    mounts = mount_rows(Path("/proc/self/mountinfo").read_text())
    mount = containing_mount(root, mounts)
    source = containing_mount(source_root, mounts)
    usage = os.statvfs(root)
    observed = {**mount, "work_root": str(root), "owner_uid": root.lstat().st_uid,
            "device_number": root.lstat().st_dev,
            "total_bytes": usage.f_blocks * usage.f_frsize,
            "available_bytes": usage.f_bavail * usage.f_frsize,
            "total_inodes": usage.f_files, "free_inodes": usage.f_favail,
            "source_mount_id": source["id"], "source_options": source["options"],
            "all_mounts": mounts,
            "nested_writable_mounts": [row["id"] for row in mounts
                if row["id"] != mount["id"] and Path(row["mountpoint"]).is_relative_to(root)
                and "rw" in row["options"]]}
    if mount["filesystem"] == "ext4":
        observed["physical_backing"] = observe_physical_backing(mount, mounts, observed["device_number"])
    return observed


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
    if plan["schema"].endswith(".v2"):
        result["original_material_profile_blockers"] = [
            "RESOURCE_SCOPED_256_MIB_PROFILE_CANNOT_CERTIFY_ORIGINAL_512_MIB_HORIZON_CONTRACT",
            "RESOURCE_COMPLETE_ORIGINAL_G4_G5_AND_EXACT_ARTIFACT_NOT_EXECUTED_BY_READINESS",
        ]
        result["owner_contract_acceptance_required"] = True
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


def verify_compute_result(result, *, source_sha, source_tree, plan_sha256, unit, birth):
    require(re.fullmatch(r"rc6-native-[0-9a-f]{32}\.service", unit or "") is not None,
            "RESOURCE_LITERAL_OWN_COMPUTE_UNIT_REQUIRED")
    require(type(result) is dict and result.get("schema") == "porota.rc6.compute-resource-observation.v1"
            and {"native_namespace_filter", "kernel_envelope_before", "kernel_envelope_after",
                 "resource_samples", "resource_summary", "compute_controls"} <= set(result)
            and result.get("scope") == COMPUTE_SCOPE and result.get("unit") == unit
            and result.get("source_sha") == source_sha and result.get("source_tree") == source_tree
            and result.get("plan_sha256") == plan_sha256
            and result.get("identity") == birth["identity"], "RESOURCE_AUTHENTIC_COMPUTE_RESULT_BINDING_REQUIRED")
    require(result.get("status") == "PURE_CANDIDATE_CONTROLS_OBSERVED_PENDING_EXTERNAL_FIN"
            and result.get("representative_full_workload") is False
            and result.get("G0_G8_qualification") is False and result.get("disk_equivalence_claimed") is False
            and result.get("original_G4_G5_started") is False
            and result.get("data_or_fixture_writes") is False
            and result.get("source_readonly") is True
            and all(result.get(key) == "NO_VERIFICADO" for key in (
                "product_resource_compatibility", "whole_droplet_compatibility", "persistent_disk_compatibility"))
            and result.get("artifact_transfer_bytes") is None
            and result.get("artifact_transfer_bytes_inferred") is False
            and result.get("FIN") == "PENDING_ORIGINAL_EXTERNAL_NATIVE_MANAGER"
            and type(result.get("real_orders_sent")) is int and result["real_orders_sent"] == 0,
            "RESOURCE_COMPUTE_CANNOT_QUALIFY_OR_RELABEL_MATERIAL_WORKLOAD")
    from scripts import rc6_native_namespace_filter as native
    proof = native.validate_evidence(result["native_namespace_filter"], source_binding={
        "source_sha": source_sha, "source_tree": source_tree})
    require(proof["identity"] == birth["identity"], "RESOURCE_COMPUTE_NATIVE_FILTER_PID_REBOUND")
    plan, current_plan_sha256 = exact_plan(PLAN_V2)
    require(plan_sha256 == current_plan_sha256 and len(canonical(result)) <= plan["compute_probe_maximum_control_bytes"],
            "RESOURCE_COMPUTE_EXACT_VERSIONED_CONTROL_REQUIRED")
    before, after = result["kernel_envelope_before"], result["kernel_envelope_after"]
    for envelope in (before, after):
        limits = validate_cgroup(envelope, plan, private_cgroup_required=False, current_pid=proof["identity"]["pid"])
        require(envelope["membership"] == "0::/system.slice/" + unit
                and envelope["mount_namespace_inode"] == proof["identity"]["mount_namespace_inode"]
                and envelope["cgroup_namespace_inode"] == proof["identity"]["cgroup_namespace_inode"],
                "RESOURCE_ACTUAL_OWN_COMPUTE_UNIT_MEMBERSHIP_REQUIRED")
        require(limits["cpu_limit"] == 1 and limits["memory_limit_bytes"] == GIB,
                "RESOURCE_COMPUTE_EXACT_ONE_CPU_ONE_GIB_REQUIRED")
        require(envelope.get("cpuset.cpus.effective") == str(envelope["affinity"][0]),
                "RESOURCE_COMPUTE_KERNEL_SINGLE_CPUSET_REQUIRED")
    stable = ("path", "membership", "mount_id", "mount_root", "mountpoint", "mount_options", "cpu.max",
              "memory.max", "memory.swap.max", "pids.max", "affinity", "uid", "euid", "privilege_status",
              "cgroup_namespace_inode", "mount_namespace_inode", "controls_immutable_to_nonroot", "cpuset.cpus.effective")
    require(all(before[key] == after[key] for key in stable), "RESOURCE_COMPUTE_LIVE_CGROUP_OR_PRIVILEGE_REBOUND")
    source_mount = result.get("source_mount")
    require(type(source_mount) is dict and "ro" in source_mount.get("options", [])
            and "rw" not in source_mount["options"], "RESOURCE_COMPUTE_SOURCE_READONLY_REQUIRED")
    summary = summarize_metrics(result["resource_samples"])
    require(result["resource_summary"] == summary and summary["memory_kernel_peak_bytes"] <= GIB
            and all(row["cgroup_path"] == before["path"] for row in result["resource_samples"]),
            "RESOURCE_ACTUAL_COMPUTE_TELEMETRY_REQUIRED")
    controls, regression_sha256 = original_compute_controls()
    observed = result["compute_controls"]
    require(type(observed) is dict and type(observed.get("actual_outputs")) is list
            and len(observed["actual_outputs"]) == 7
            and observed.get("inputs") == controls and observed.get("original_regression_sha256") == regression_sha256
            and observed.get("inputs_sha256") == digest(canonical(controls))
            and observed.get("actual_outputs_sha256") == digest(canonical(observed["actual_outputs"]))
            and observed.get("candidate_module_sha256") == digest((ROOT / "de_concurrent_risk_capacity_hf6.py").read_bytes())
            and type(observed.get("actual_capacity_calls")) is int and observed["actual_capacity_calls"] == 5
            and type(observed.get("actual_emergency_guard_calls")) is int and observed["actual_emergency_guard_calls"] == 2
            and type(observed.get("repetitions")) is int and observed["repetitions"] == 1
            and observed.get("fixtures_created") is False
            and all(type(observed.get(key)) is int and observed[key] == 0
                    for key in ("db_calls", "network_calls", "financial_ticks", "real_orders_sent")),
            "RESOURCE_ACTUAL_ORIGINAL_PURE_CONTROLS_REQUIRED")
    expected = [(True, "5000"), (False, "5000"), (True, "11000"), (True, "6000"), (False, "2000")]
    from decimal import Decimal
    require(all(type(value) is dict and value.get("admitted") is expected[index][0]
                and type(value.get("remaining_before_candidate")) is str
                and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value["remaining_before_candidate"])
                and Decimal(value["remaining_before_candidate"]) == Decimal(expected[index][1])
                for index, value in enumerate(observed["actual_outputs"][:5]))
            and observed["actual_outputs"][5:] == ["", "EMERGENCY_POSITION_CAP"],
            "RESOURCE_REAL_CANDIDATE_PURE_REGRESSION_RED")


def validate_custody_compute_observation(witness, *, binding):
    """Consume actual opaque custody, original captured actor RAW and real FIN."""
    from scripts import rc6_privileged_custody as privileged
    receipt = privileged.validate_witness(witness, binding=binding)
    require(receipt.get("resource_probe") is True,
            "RESOURCE_COMPUTE_CERTIFIED_ROOT_GUARDIAN_SCOPE_REQUIRED")
    native = receipt["native_root_proof"]
    cases = native.get("cases")
    require(type(cases) is list and all(type(row) is dict for row in cases) and [row.get("case") for row in cases] == [
        "root-success", "failure-before-drop", "root-hang", "nonroot-hang"],
        "RESOURCE_COMPUTE_FOUR_ORIGINAL_CUSTODY_CASES_REQUIRED")
    success = cases[0]
    require(success["kernel"]["returncode"] == 0 and success["kernel"]["actual_child_reaped"] is True
            and success["kernel"]["owned_children_exhaustion_verified"] is True
            and success["kernel"]["process_group_absent_after_reap"] is True,
            "RESOURCE_COMPUTE_ORIGINAL_ACTOR_FIN_REQUIRED")
    for name in ("guardian_actual_FIN", "controller_actual_FIN"):
        require(all(receipt[name].get(key) is True for key in (
            "pidfd_exit_observed", "PID1_actual_broker_reap_observed", "owned_cgroup_empty_or_removed",
            "external_PID1_birth_pidfd_ACK_verified")), "RESOURCE_COMPUTE_EXTERNAL_ROOT_GUARDIAN_FIN_REQUIRED")
    root = Path(receipt["capture_root"])
    require(root.is_absolute() and ".." not in root.parts and not root.is_relative_to(ROOT)
            and not any(row.is_symlink() for row in (root, *root.parents)),
            "RESOURCE_COMPUTE_AUTHENTIC_CAPTURE_ROOT_REQUIRED")
    manifest_raw = privileged._read(root / "manifest.json", 4 * MIB)
    require(digest(manifest_raw) == receipt["capture_manifest_sha256"],
            "RESOURCE_COMPUTE_ORIGINAL_CAPTURE_MANIFEST_REQUIRED")
    manifest = document(manifest_raw)
    require(manifest.get("schema") == "porota.rc6.generated-fixture-required-capture.v1"
            and manifest.get("binding") == binding and manifest.get("actual_owned_fin_closed") is True
            and manifest.get("phase_green") is True and type(manifest.get("files")) is list,
            "RESOURCE_COMPUTE_ORIGINAL_CAPTURE_BINDING_AND_FIN_REQUIRED")
    rows = [row for row in manifest["files"] if row["relative_source"] == "root-success.native.log"]
    require(len(rows) == 1 and rows[0]["relative_source"] == success["raw"]["path"]
            and rows[0]["bytes"] == success["raw"]["bytes"]
            and rows[0]["sha256"] == success["raw"]["sha256"]
            and type(rows[0]["capture_file"]) is str and re.fullmatch(r"[0-9]{4}\.raw", rows[0]["capture_file"]),
            "RESOURCE_COMPUTE_ORIGINAL_ACTOR_CAPTURE_MEMBER_REQUIRED")
    raw = privileged._read(root / rows[0]["capture_file"], 2 * MIB)
    require(len(raw) == rows[0]["bytes"] and digest(raw) == rows[0]["sha256"]
            and document(raw) == success["actor"], "RESOURCE_COMPUTE_ORIGINAL_ACTOR_RAW_REBOUND")
    actor = success["actor"]
    result = actor.get("compute_resource_observation")
    require(type(result) is dict and receipt.get("compute_resource_observation") == result,
            "RESOURCE_COMPUTE_TOP_COPY_CANNOT_REPLACE_ORIGINAL_ACTOR_RAW")
    identity = actor["drop"]["native_filter"]["identity"]
    require(identity["pid"] == success["kernel"]["pid"] == actor["after_drop"]["pid"]
            and int(actor["after_drop"]["start_ticks"]) == identity["start_ticks"]
            and actor["after_drop"]["boot_id"] == identity["boot_id"],
            "RESOURCE_COMPUTE_ACTUAL_NONROOT_ACTOR_IDENTITY_REQUIRED")
    unit = receipt["controller_actual_FIN"]["root_actor"]["cgroup"].rsplit("/", 1)[1]
    _, resource_plan_sha256 = exact_plan(PLAN_V2)
    verify_compute_result(result, source_sha=binding["candidate_sha"], source_tree=binding["candidate_tree"],
        plan_sha256=resource_plan_sha256, unit=unit, birth={"identity": identity})
    require(type(success["kernel"].get("peak_rss_bytes")) is int
            and 0 < success["kernel"]["peak_rss_bytes"] <= GIB,
            "RESOURCE_COMPUTE_ORIGINAL_KERNEL_PEAK_RSS_REQUIRED")
    return {"schema": "porota.rc6.compute-resource-controller.v1", "scope": COMPUTE_SCOPE,
        "status": "PASS_PURE_COMPUTE_OBSERVATION_ONLY", "source_sha": binding["candidate_sha"],
        "source_tree": binding["candidate_tree"], "resource_plan_sha256": resource_plan_sha256,
        "compute_resource_observation": result, "original_actor_raw_sha256": digest(raw),
        "capture_manifest_sha256": receipt["capture_manifest_sha256"],
        "actual_owned_actor_kernel_peak_rss_bytes": success["kernel"]["peak_rss_bytes"],
        "original_four_custody_cases_verified": True, "external_ROOT_guardian_and_FIN_verified": True,
        "product_resource_compatibility": "NO_VERIFICADO", "whole_droplet_compatibility": "NO_VERIFICADO",
        "persistent_disk_compatibility": "NO_VERIFICADO", "representative_full_workload": False,
        "G0_G8_qualification": False, "real_orders_sent": 0, "recurring_infrastructure_cost_usd": 0}


def prove_compute_probe(*, output, source_sha, source_tree, owner_session, plan_sha256, owner_check):
    """Exact Actions controller hook; only the certified guardian may launch."""
    require(callable(owner_check) and os.getuid() == os.geteuid() > 0
            and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_REPOSITORY") == "mbalbo2023/Porota-trading"
            and os.environ.get("GITHUB_ACTOR") == "mbalbo2023"
            and os.environ.get("GITHUB_RUN_ATTEMPT") == "1",
            "RESOURCE_COMPUTE_AUTHENTICATED_ACTIONS_OWNER_REQUIRED")
    exact_source(source_sha, source_tree)
    from scripts import rc6_development_checks as development
    from scripts import rc6_capacity_calibration as calibration
    from scripts import rc6_privileged_custody as privileged
    output = Path(output)
    require(output.is_absolute() and ".." not in output.parts and not output.is_relative_to(ROOT)
            and not any(row.is_symlink() for row in (output, *output.parents))
            and not output.exists(), "RESOURCE_COMPUTE_FRESH_EXTERNAL_OUTPUT_REQUIRED")
    binding = {"candidate_sha": source_sha, "candidate_tree": source_tree,
        "producer": "NATIVE_COMPUTE_RESOURCE_OBSERVATION", "attempt_id": os.environ["GITHUB_RUN_ID"] + "-1",
        "owner_id": owner_session, "runner_class": "github-hosted/ubuntu-24.04", "workload_fingerprint": plan_sha256}
    code_hashes = {name: digest((ROOT / name).read_bytes()) for name in calibration.CODE_MEMBERS}
    owner_check()
    output.mkdir(mode=0o700)
    witness = privileged.prove_custody(source=ROOT, source_sha=source_sha, source_tree=source_tree,
        code_hashes=code_hashes, parent=development.short_control_parent(ROOT), binding=binding,
        progress=None, capture_parent=output / "raw", resource_probe=True)
    owner_check()
    result = validate_custody_compute_observation(witness, binding=binding)
    exclusive_json(output / "compute-resource.json", result)
    return result


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
    measured = None
    if plan["schema"].endswith(".v2"):
        measured = ResourceSampler(envelope["cgroup_observation"]["path"])
        measured.__enter__()
    try:
        kernel, fin = lifecycle.execute_owned(namespace, command, cwd=namespace.path, environ=environment,
            log_relative="native.log", timeout_seconds=plan["timeout_seconds"], fin_label="product-resource")
    finally:
        if measured is not None:
            measured.__exit__()
    # No post-Source/RAW reads or cleanup until the original kernel FIN exists.
    required = ["native.log", "producer-owned-fin-product-resource.json", "supervisor-filter.json"]
    telemetry, telemetry_error = None, None
    if measured is not None:
        telemetry = {"schema": "porota.rc6.product-resource-telemetry.v1",
            "source_sha": args.source_sha, "source_tree": args.source_tree, "plan_sha256": plan_sha256,
            "samples": measured.samples, "readback_errors": measured.errors, "summary": None}
        try:
            require(not measured.errors, "RESOURCE_NATIVE_TELEMETRY_READBACK_FAILED")
            telemetry["summary"] = summarize_metrics(measured.samples, kernel_peak_rss_bytes=kernel["peak_rss_bytes"])
            require(telemetry["summary"]["memory_kernel_peak_bytes"] <= plan["memory_maximum_bytes"],
                    "RESOURCE_MEMORY_ENVELOPE_ALREADY_EXCEEDED")
        except (ValueError, KeyError, TypeError) as error:
            telemetry_error = str(error) if isinstance(error, ValueError) else type(error).__name__
        telemetry["validation_error"] = telemetry_error
        require(len(canonical(telemetry)) <= 80 * MIB, "RESOURCE_TELEMETRY_FIXED_PHYSICAL_CONTROL_BOUND_EXCEEDED")
        exclusive_json(namespace.path / "resource-telemetry.json", telemetry)
        required.append("resource-telemetry.json")
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
        facts[key] for key in ("failures", "errors", "skipped")) and worker_validation_error is None \
        and telemetry_error is None
    return {"schema": "porota.rc6.product-resource-profile.v1", "scope": SCOPE,
        "status": "PASS_BOUNDED_SOURCE_PROFILE_ONLY" if passed else "RED_BOUNDED_SOURCE_PROFILE",
        "source_sha": args.source_sha, "source_tree": args.source_tree, "plan_sha256": plan_sha256,
        "kernel": kernel, "native_fin": True, "cleanup": cleanup, "junit": facts,
        "junit_validation_error": junit_validation_error,
        "worker_validation_error": worker_validation_error,
        "resource_telemetry": telemetry, "resource_telemetry_error": telemetry_error,
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
        "original_material_workloads_completed": False, "representative_full_workload": False,
        "artifact_transfer_bytes": None, "artifact_transfer_bytes_inferred": False,
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
    if plan["schema"].endswith(".v2"):
        require(fields.get("PRODUCT_RESOURCE_PHYSICAL_STORAGE_CONTRACT_AUTHORIZATION") == "APPROVED"
                and fields.get("PRODUCT_RESOURCE_CONTRACT_VERSION") == plan["schema"],
                "RESOURCE_V2_EXACT_OWNER_STORAGE_CONTRACT_ACCEPTANCE_REQUIRED")
    manifest = document(fields.get("PREREQUISITES_MANIFEST_JSON", "null"))
    require(type(manifest) is dict, "RESOURCE_NATIVE_G0_MANIFEST_REQUIRED")
    result = owned_execution(args, plan, plan_sha256, envelope, args.output, owner_check, manifest)
    save_control(args.output, result, "profile.json")
    print(canonical({key: result[key] for key in ("status", "scope", "real_orders_sent")}).decode(), end="")
    return int(result["status"] != "PASS_BOUNDED_SOURCE_PROFILE_ONLY")


if __name__ == "__main__":
    raise SystemExit(main())
