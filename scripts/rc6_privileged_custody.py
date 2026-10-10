"""Ephemeral PID1 custody for the fixed RC6 ROOT quota setup.

The existing native manager remains byte exact. PID1 owns the ROOT broker from
fork/exec; the ROOT broker uses that manager to signal and reap its ROOT/NONROOT
children. Metadata alone never produces an accepted in-process witness.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import runpy
import select
import signal
import stat
import sys
import time

ROOT = Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCHEMA = "porota.rc6.privileged-custody-native.v1"
CONTRACT_SCHEMA = "porota.rc6.privileged-custody-contract.v1"
MEMBER = "scripts/rc6_privileged_custody.py"
MANAGER_MEMBER = "scripts/rc6_controlled_native_child_manager.py"
MANAGER_SHA256 = "55325b3108e175a42b87ebe544fd307fa45ffd29b6f7ab471443803ad9ba53b8"
CONTROL_BOUND = 2 * 1024**2
SYSTEMD_QUERY_BOUND = 65536
SYSTEMD_BINARY_BOUND = 16 * 1024**2
SYSTEMD_CONFIG_FIELDS = ("Version", "LogLevel", "LogTarget", "DefaultStandardOutput", "DefaultStandardError")
SYSTEMD_BINARY_PATHS = ("/usr/bin/systemctl", "/usr/bin/systemd-run", "/usr/lib/systemd/systemd",
                        "/usr/lib/systemd/systemd-executor")
PROBE_LIMIT = 60
BRIDGE_DESCRIPTOR = 3
BRIDGE_SHELL = 'exec 3<&0; exec 0</dev/null; exec "$@"'
_AUTHORITY = object()
_WITNESSES = {}


def require(value, signature):
    if not value:
        raise ValueError(signature)


def wire(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _read(path, maximum=CONTROL_BOUND):
    from scripts import rc6_capacity_calibration as calibration
    return calibration.read(path, maximum)


def _proc_text(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        raw = os.read(descriptor, 65537)
        require(len(raw) <= 65536, "ROOT_CUSTODY_PROC_METADATA_BOUND")
        return raw.decode("utf-8", errors="strict")
    finally:
        os.close(descriptor)


PUBLIC_PROCESS_FIELDS = frozenset(("pid", "parent_pid", "start_ticks", "uids", "gids", "capabilities",
                                  "cgroup", "boot_id", "namespace_pids"))
ISSUER_FIELDS = ("pid", "start_ticks", "boot_id", "pid_namespace_inode")


def public_process_identity(value):
    """Strict projection; a namespace claim never substitutes a kernel read."""
    require(type(value) is dict and set(value) in (PUBLIC_PROCESS_FIELDS,
            PUBLIC_PROCESS_FIELDS | {"pid_namespace_inode"}), "ROOT_CUSTODY_PUBLIC_IDENTITY_FIELDS_REQUIRED")
    require(type(value["pid"]) is int and value["pid"] > 0
            and type(value["parent_pid"]) is int and value["parent_pid"] >= 0
            and type(value["start_ticks"]) is str and value["start_ticks"].isdigit()
            and type(value["boot_id"]) is str and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value["boot_id"])
            and type(value["cgroup"]) is str and bool(value["cgroup"])
            and all(type(value[key]) is list and len(value[key]) == 4
                    and all(type(x) is int and x >= 0 for x in value[key]) for key in ("uids", "gids"))
            and type(value["capabilities"]) is dict
            and set(value["capabilities"]) == {"CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"}
            and all(type(x) is int and x >= 0 for x in value["capabilities"].values())
            and type(value["namespace_pids"]) is list and bool(value["namespace_pids"])
            and all(type(x) is int and x > 0 for x in value["namespace_pids"])
            and ("pid_namespace_inode" not in value or type(value["pid_namespace_inode"]) is int
                 and value["pid_namespace_inode"] > 0), "ROOT_CUSTODY_PUBLIC_IDENTITY_INVALID")
    return {key: value[key] for key in PUBLIC_PROCESS_FIELDS}


def public_kernel_process(pid, *, proc_root=Path("/proc")):
    """Public proc metadata only: following foreign ns links needs ptrace READ."""
    require(type(pid) is int and pid > 0, "ROOT_CUSTODY_EXACT_PID_REQUIRED")
    base = str(Path(proc_root) / str(pid))
    values = dict(row.split(":", 1) for row in _proc_text(base + "/status").splitlines() if ":" in row)
    fields = _proc_text(base + "/stat").rsplit(")", 1)[1].split()
    return public_process_identity({"pid": pid, "parent_pid": int(fields[1]), "start_ticks": fields[19],
            "uids": [int(x) for x in values["Uid"].split()],
            "gids": [int(x) for x in values["Gid"].split()],
            "capabilities": {key: int(values[key].strip(), 16) for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")},
            "cgroup": _proc_text(base + "/cgroup").strip(),
            "boot_id": _proc_text("/proc/sys/kernel/random/boot_id").strip(),
            "namespace_pids": [int(x) for x in values.get("NSpid", "").split()]})


def kernel_process(pid, *, proc_root=Path("/proc")):
    """Full identity for self or a protected ROOT child in our private boundary."""
    value = public_kernel_process(pid, proc_root=proc_root)
    value["pid_namespace_inode"] = os.stat(Path(proc_root) / str(pid) / "ns/pid").st_ino
    public_process_identity(value)
    return value


def issuer_snapshot(identity):
    public_process_identity(identity)
    require("pid_namespace_inode" in identity, "ROOT_CUSTODY_ISSUER_SELF_NAMESPACE_REQUIRED")
    return {key: identity[key] for key in ISSUER_FIELDS}


def issuer_host_namespace(request, guardian):
    """Authenticate the issuer-self snapshot against the guardian's own inode."""
    issuer = request.get("issuer")
    public_process_identity(guardian)
    require(type(issuer) is dict and set(issuer) == set(ISSUER_FIELDS)
            and type(issuer["pid"]) is int and issuer["pid"] > 0
            and type(issuer["pid_namespace_inode"]) is int and issuer["pid_namespace_inode"] > 0
            and issuer["pid_namespace_inode"] == guardian.get("pid_namespace_inode")
            and guardian["namespace_pids"] == [guardian["pid"]],
            "ROOT_CUSTODY_ISSUER_SELF_HOST_NAMESPACE_REBOUND")
    return issuer["pid_namespace_inode"]


def unit_name(nonce, *, guard=False):
    require(type(nonce) is str and re.fullmatch("[0-9a-f]{32}", nonce), "ROOT_CUSTODY_EXACT_NAMESPACE_NONCE_REQUIRED")
    return "rc6-native-" + nonce + ("-guard" if guard else "") + ".service"


def native_contract(source_sha, source_tree, code_hashes):
    require(all(type(x) is str and re.fullmatch("[0-9a-f]{40}", x) for x in (source_sha, source_tree)),
            "ROOT_CUSTODY_EXACT_SOURCE_REQUIRED")
    required = (MEMBER, MANAGER_MEMBER, "scripts/rc6_capacity_calibration.py", "scripts/rc6_native_namespace_filter.py")
    require(type(code_hashes) is dict and code_hashes.get(MANAGER_MEMBER) == MANAGER_SHA256
            and all(type(code_hashes.get(member)) is str and re.fullmatch("[0-9a-f]{64}", code_hashes[member])
                    for member in required),
            "ROOT_CUSTODY_AUTHENTIC_CODE_HASHES_REQUIRED")
    return {"schema": CONTRACT_SCHEMA, "source_sha": source_sha, "source_tree": source_tree,
            "adapter_sha256": code_hashes[MEMBER], "manager_sha256": MANAGER_SHA256,
            "mechanism": "EXISTING_PID1_TRANSIENT_UNIT_AND_UNMODIFIED_ROOT_NATIVE_MANAGER",
            "root_actor_boundary": "PRIVATE_PID_NAMESPACE_BEFORE_ROOT_OR_NONROOT_PROBE_ACTOR",
            "issuer_authentication": "GUARDIAN_LIVE_PID_START_BOOT_AND_OPAQUE_OWN_DIRECTORY_FD",
            "root_foreign_process_signals_forbidden": True,
            "native_probe_timeout_seconds": PROBE_LIMIT, "control_bound_bytes": CONTROL_BOUND,
            "TERM_seconds": 2, "failed_phase_FIN_seconds": 5,
            "backing_allocation_allowed": False, "quota_mutation_allowed": False,
            "namespace_mounts_for_custody_proof_allowed": True, "quota_backing_mounts_allowed": False,
            "actor_mount_operation_allowed": False, "PID1_ephemeral_namespace_setup_allowed": True,
            "financial_tick_allowed": False,
            "persistent_infrastructure_allowed": False, "recurring_additional_cost_usd": 0}


def service_properties(control_root, runtime_seconds):
    root = Path(control_root).absolute()
    require(root != Path("/") and not any(x.is_symlink() for x in (root, *root.parents)),
            "ROOT_CUSTODY_PRIVATE_CONTROL_ROOT_REQUIRED")
    require(type(runtime_seconds) is int and 1 <= runtime_seconds <= 10800,
            "ROOT_CUSTODY_NATIVE_RUNTIME_BOUND_REQUIRED")
    return ["RuntimeMaxSec=" + str(runtime_seconds), "TimeoutStopSec=2", "KillMode=control-group",
            "SendSIGKILL=yes", "FinalKillSignal=SIGKILL", "Restart=no", "PrivateMounts=yes",
            "ProtectSystem=strict", "ReadWritePaths=" + str(root), "ProtectControlGroups=yes",
            "ProtectKernelTunables=yes", "ProtectKernelModules=yes", "ProtectKernelLogs=yes",
            "CapabilityBoundingSet=~CAP_SYS_PTRACE CAP_SYS_BOOT CAP_SYS_MODULE CAP_SYS_RAWIO CAP_SYS_TIME CAP_AUDIT_CONTROL CAP_BPF CAP_PERFMON CAP_CHECKPOINT_RESTORE",
            "NoNewPrivileges=yes", "RestrictAddressFamilies=AF_INET AF_INET6",
            # Only the frozen guardian may create the private PID namespace.
            # Actors run inside it; the exact NONROOT plan denies new namespaces.
            "RestrictNamespaces=~cgroup net user ipc uts",
            "InaccessiblePaths=-/run/systemd/private -/run/dbus -/run/docker.sock -/var/run/docker.sock -/proc/1/root -/dev/shm",
            "SystemCallFilter=~setns ptrace process_vm_readv process_vm_writev bpf open_by_handle_at name_to_handle_at kexec_load kexec_file_load init_module finit_module delete_module reboot swapon swapoff",
            "SystemCallErrorNumber=EPERM"]


def service_command(source, request_path, *, nonce, control_root, runtime_seconds,
                    broker_mode, guard=False):
    require(broker_mode in ("probe", "quota", "guard-hang"), "ROOT_CUSTODY_FIXED_BROKER_MODE_REQUIRED")
    require(type(guard) is bool and guard == (broker_mode == "guard-hang"),
            "ROOT_CUSTODY_GUARD_DIAGNOSTIC_ROLE_REQUIRED")
    source = Path(source).absolute()
    require(source == ROOT, "ROOT_CUSTODY_FIXED_CHECKOUT_REQUIRED")
    request_path = Path(request_path).absolute()
    require(request_path.parent == Path(control_root).absolute()
            and request_path.name in ("custody-request.json", "probe-request.json")
            and not request_path.is_symlink(), "ROOT_CUSTODY_FIXED_OWN_REQUEST_PATH_REQUIRED")
    name = unit_name(nonce, guard=guard)
    command = ["sudo", "-n", "--", "systemd-run", "--wait", "--pipe", "--collect", "--service-type=exec",
               "--unit=" + name, "--working-directory=" + str(source)]
    # The guardian failure log is diagnostic, not a JSON producer. Preserve
    # systemd-run startup/status messages there instead of discarding them.
    if broker_mode != "guard-hang":
        command.insert(4, "--quiet")
    command.extend("--property=" + value for value in service_properties(control_root, runtime_seconds))
    if guard:
        # This changes only the owned startup diagnostic's log verbosity.
        # Broker/quota properties and every isolation restriction stay fixed.
        command.append("--property=LogLevelMax=debug")
    for key in ("PATH", "RUNNER_TOOL_CACHE", "LANG", "LC_ALL"):
        if key in os.environ:
            value = os.environ[key]
            require(type(value) is str and "\x00" not in value and "\n" not in value,
                    "ROOT_CUSTODY_SANITIZED_ENVIRONMENT_REQUIRED")
            command.append("--setenv=" + key + "=" + value)
    command.extend(["--", "/bin/sh", "-c", BRIDGE_SHELL, "rc6-opaque-own-fd",
                    sys.executable, "-I", "-B", str(source / MEMBER), "--guardian", broker_mode,
                    "--request", str(Path(request_path).absolute()), "--unit", name])
    return command


def _unit_file(name):
    require(type(name) is str and re.fullmatch(r"rc6-native-[0-9a-f]{32}(?:-guard)?\.service", name),
            "ROOT_CUSTODY_PRIVATE_UNIT_NAME_REQUIRED")
    path = Path("/run/systemd/transient") / name
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1,
            "ROOT_CUSTODY_ROOT_OWNED_TRANSIENT_UNIT_REQUIRED")
    raw = _read(path)
    values = {}
    for line in raw.decode().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return {"path": str(path), "sha256": sha256(raw), "raw_base64": base64.b64encode(raw).decode(), "properties": values,
            "identity": [before.st_dev, before.st_ino, before.st_uid, before.st_gid, before.st_mode]}


def _seconds(value):
    require(type(value) is str, "ROOT_CUSTODY_UNIT_DURATION_UNKNOWN")
    match = re.fullmatch(r"([0-9]+)(us|ms|s|sec)?", value)
    require(match is not None, "ROOT_CUSTODY_UNIT_DURATION_UNKNOWN")
    number, unit = match.groups()
    return int(number) / {"us": 1000000, "ms": 1000, "s": 1, "sec": 1, None: 1}[unit]


def require_guard_log_profile(name, properties):
    require(type(name) is str and re.fullmatch(r"rc6-native-[0-9a-f]{32}(?:-guard)?\.service", name),
            "ROOT_CUSTODY_PRIVATE_UNIT_NAME_REQUIRED")
    if name.endswith("-guard.service"):
        require(properties.get("LogLevelMax") in ("debug", "7"),
                "ROOT_CUSTODY_GUARD_ACTUAL_DEBUG_LEVEL_REQUIRED")
    else:
        require("LogLevelMax" not in properties, "ROOT_CUSTODY_NON_GUARD_LOG_PROFILE_CHANGED")


def controller_snapshot(name, expected_runtime):
    actor = kernel_process(os.getpid())
    controller = public_kernel_process(1)
    require(actor["parent_pid"] == 1 and actor["uids"] == [0] * 4 and controller["uids"] == [0] * 4,
            "ROOT_CUSTODY_ACTUAL_PID1_ROOT_PARENT_REQUIRED")
    expected_cgroup = "0::/system.slice/" + name
    require(actor["cgroup"] == expected_cgroup, "ROOT_CUSTODY_ACTUAL_PRIVATE_CGROUP_REQUIRED")
    unit = _unit_file(name)
    properties = unit["properties"]
    require_guard_log_profile(name, properties)
    require(_seconds(properties.get("RuntimeMaxSec")) == expected_runtime
            and _seconds(properties.get("TimeoutStopSec")) == 2
            and properties.get("KillMode") == "control-group"
            and properties.get("SendSIGKILL") in ("yes", "true", "1")
            and properties.get("FinalKillSignal") in ("SIGKILL", "9")
            and properties.get("PrivateMounts") in ("yes", "true", "1")
            and properties.get("ProtectSystem") == "strict"
            and properties.get("ProtectControlGroups") in ("yes", "true", "1")
            and properties.get("NoNewPrivileges") in ("yes", "true", "1")
            and set(properties.get("RestrictAddressFamilies", "").split()) == {"AF_INET", "AF_INET6"}
            and set(properties.get("RestrictNamespaces", "").split()) == {"~cgroup", "net", "user", "ipc", "uts"}
            and properties.get("SystemCallErrorNumber") in ("EPERM", "1")
            and "setns" in properties.get("SystemCallFilter", "").lstrip("~").split(),
            "ROOT_CUSTODY_ACTUAL_UNIT_PROPERTIES_REQUIRED")
    require(actor["capabilities"]["CapEff"] & (1 << 5), "ROOT_CUSTODY_PARENT_CAP_KILL_REQUIRED")
    forbidden = sum(1 << number for number in (16, 17, 19, 22, 25, 30, 38, 39, 40))
    require(all(actor["capabilities"][key] & forbidden == 0 for key in ("CapPrm", "CapEff", "CapBnd")),
            "ROOT_CUSTODY_FOREIGN_PROCESS_AND_KERNEL_CAPABILITIES_FORBIDDEN")
    return {"schema": "porota.rc6.pid1-root-controller-live.v1", "unit": name,
            "actor": actor, "PID1": controller, "transient_unit": unit,
            "mount_namespace_inode": os.stat("/proc/self/ns/mnt").st_ino,
            "systemd_guard_exists_before_guardian_exec": True,
            "ROOT_actor_boundary_certified": False,
            "root_controller_claimed_by_metadata_only": False}


def _manager(source):
    raw = _read(Path(source) / MANAGER_MEMBER)
    require(sha256(raw) == MANAGER_SHA256, "ROOT_CUSTODY_ORIGINAL_MANAGER_CHANGED")
    return runpy.run_path(str(Path(source) / MANAGER_MEMBER))


def _write_control(path, value, owner_uid, owner_gid):
    from scripts import rc6_capacity_calibration as calibration
    calibration.publish(path, value)
    os.chown(path, owner_uid, owner_gid, follow_symlinks=False)


def _request(path, *, private=False):
    from scripts import rc6_capacity_calibration as calibration
    raw = _read(path)
    value = calibration.decode(raw)
    require(value.get("schema") == "porota.rc6.root-custody-request.v1"
            and value.get("source_root") == str(ROOT)
            and type(value.get("owner_uid")) is int and value["owner_uid"] > 0
            and type(value.get("owner_gid")) is int and value["owner_gid"] > 0
            and value.get("code_hashes", {}).get(MANAGER_MEMBER) == MANAGER_SHA256
            and value["code_hashes"].get(MEMBER) == sha256(_read(ROOT / MEMBER)),
            "ROOT_CUSTODY_AUTHENTIC_FIXED_REQUEST_REQUIRED")
    native_contract(value["source_sha"], value["source_tree"], value["code_hashes"])
    for member, expected in value["code_hashes"].items():
        require(member in calibration.CODE_MEMBERS and sha256(_read(ROOT / member)) == expected,
                "ROOT_CUSTODY_EXACT_SOURCE_PROGRAM_REBOUND")
    require(set(value["code_hashes"]) == set(calibration.CODE_MEMBERS),
            "ROOT_CUSTODY_SOURCE_PROGRAM_INCOMPLETE")
    # Root may read the runner-owned checkout, but must not trust Git's global
    # safe.directory or mutate its configuration to do so.
    previous = {key: os.environ.get(key) for key in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0")}
    try:
        os.environ.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="safe.directory", GIT_CONFIG_VALUE_0=str(ROOT))
        calibration.source_pin(ROOT, value["source_sha"], value["source_tree"])
    finally:
        for key, old in previous.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
    claim = value["namespace_receipt"]
    binding_fields = {"candidate_sha", "candidate_tree", "producer", "attempt_id", "owner_id", "workload_fingerprint", "runner_class"}
    require(type(value.get("binding")) is dict and set(value["binding"]) == binding_fields
            and value["binding"]["candidate_sha"] == value["source_sha"]
            and value["binding"]["candidate_tree"] == value["source_tree"]
            and value["binding"]["runner_class"] == "github-hosted/ubuntu-24.04",
            "ROOT_CUSTODY_EXACT_ACTIONS_BINDING_REQUIRED")
    require(type(claim) is dict and claim.get("schema") == "porota.rc6.generated-fixture-consumer-binding.v1"
            and claim.get("cleanup_authority_granted") is False and claim.get("binding") == value["binding"],
            "ROOT_CUSTODY_AUTHENTIC_CONSUMER_CLAIM_REQUIRED")
    path_root = Path(claim["path"]).absolute()
    require(str(path_root) == claim["path"] and not any(x.is_symlink() for x in (path_root, *path_root.parents)),
            "ROOT_CUSTODY_CONSUMER_NOFOLLOW_REQUIRED")
    details = path_root.lstat()
    require(stat.S_ISDIR(details.st_mode) and details.st_uid == value["owner_uid"]
            and details.st_gid == value["owner_gid"] and stat.S_IMODE(details.st_mode) == 0o700
            and claim["identity"] == [details.st_dev, details.st_ino, details.st_uid, details.st_gid, 0o700],
            "ROOT_CUSTODY_CONSUMER_INODE_REBOUND")
    nonce = claim.get("namespace_nonce")
    unit_name(nonce)
    require(path_root.name == "r6-" + base64.urlsafe_b64encode(bytes.fromhex(nonce)).decode().rstrip("="),
            "ROOT_CUSTODY_ORIGINAL_NAMESPACE_NAME_REQUIRED")
    marker_path = path_root / ".porota-generated-fixture-owner.json"
    marker_raw = _read(marker_path)
    marker = calibration.decode(marker_raw)
    marker_stat = marker_path.lstat()
    require(marker_stat.st_uid == value["owner_uid"] and stat.S_IMODE(marker_stat.st_mode) == 0o600
            and marker.get("schema") == "porota.rc6.generated-fixture-owner.v1"
            and sha256(marker_raw) == claim["marker_sha256"] and marker.get("binding") == value["binding"]
            and marker.get("identity") == claim["identity"] and marker.get("namespace_nonce") == nonce
            and marker.get("path") == str(path_root) and marker.get("mount_id") == claim["mount_id"]
            and type(marker.get("real_orders_sent")) is int and marker["real_orders_sent"] == 0
            and marker.get("runtime_paths_authorized") is False,
            "ROOT_CUSTODY_ORIGINAL_MARKER_REBOUND")
    require(Path(path).parent == path_root and value.get("binding") == value["namespace_receipt"]["binding"],
            "ROOT_CUSTODY_REQUEST_NAMESPACE_REBOUND")
    if not private:
        issuer_host_namespace(value, kernel_process(os.getpid()))
        issuer = public_kernel_process(value["issuer"]["pid"])
        require(issuer["uids"] == [value["owner_uid"]] * 4
                and issuer["gids"] == [value["owner_gid"]] * 4
                and issuer["start_ticks"] == value["issuer"]["start_ticks"]
                and issuer["boot_id"] == value["issuer"]["boot_id"]
                and issuer["namespace_pids"] == [issuer["pid"]], "ROOT_CUSTODY_ISSUER_KERNEL_IDENTITY_CHANGED")
    return value, raw, path_root


def original_directory_bridge(request):
    """Authenticate FD3, which transports only the issuer's own directory.

    The fixed ROOT setup opens exactly marker/image/birth with NOFOLLOW. This
    descriptor is closed before any mutable NONROOT backend or probe actor.
    No host proc tree, foreign root/cwd/fd magic links or namespace fd is bound.
    """
    from scripts import rc6_capacity_calibration as calibration
    claim = request["namespace_receipt"]
    details = os.fstat(BRIDGE_DESCRIPTOR)
    require(fcntl.fcntl(BRIDGE_DESCRIPTOR, fcntl.F_GETFL) & os.O_PATH == os.O_PATH and stat.S_ISDIR(details.st_mode)
            and [details.st_dev, details.st_ino, details.st_uid, details.st_gid, stat.S_IMODE(details.st_mode)] == claim["identity"]
            and calibration.descriptor_mount_id(BRIDGE_DESCRIPTOR) == claim["mount_id"],
            "ROOT_CUSTODY_ORIGINAL_OPAQUE_DIRECTORY_FD_NOT_TRANSPORTED")
    descriptor = os.open(".porota-generated-fixture-owner.json",
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NOATIME, dir_fd=BRIDGE_DESCRIPTOR)
    try:
        before = os.fstat(descriptor)
        raw = os.read(descriptor, 65537)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == len(raw) <= 65536
                and before.st_uid == request["owner_uid"] and before.st_gid == request["owner_gid"]
                and stat.S_IMODE(before.st_mode) == 0o600 and sha256(raw) == claim["marker_sha256"]
                and calibration.identity(before) == calibration.identity(os.fstat(descriptor)),
                "ROOT_CUSTODY_ORIGINAL_OPAQUE_MARKER_REBOUND")
    finally:
        os.close(descriptor)
    marker = calibration.decode(raw)
    require(marker.get("binding") == request["binding"] and marker.get("namespace_nonce") == claim["namespace_nonce"]
            and marker.get("identity") == claim["identity"] and marker.get("mount_id") == claim["mount_id"],
            "ROOT_CUSTODY_ORIGINAL_OPAQUE_MARKER_BINDING_REBOUND")
    return {"schema": "porota.rc6.original-own-directory-fd-native.v1", "descriptor": BRIDGE_DESCRIPTOR,
            "identity": claim["identity"], "original_mount_id": claim["mount_id"],
            "marker_sha256": sha256(raw), "namespace_nonce": claim["namespace_nonce"],
            "foreign_host_proc_exposed": False}


def _private_identity(request, mapping):
    local = kernel_process(os.getpid())
    host = mapping["host_private_PID1"]
    issuer = mapping["issuer_actual"]
    public_process_identity(issuer)
    host_inode = issuer_host_namespace(request, mapping["guardian_actual"])
    require(host["namespace_pids"] == [host["pid"], 1]
            and local["pid_namespace_inode"] == host["pid_namespace_inode"] != host_inode
            and issuer["pid"] == request["issuer"]["pid"]
            and issuer["start_ticks"] == request["issuer"]["start_ticks"]
            and issuer["boot_id"] == request["issuer"]["boot_id"]
            and issuer["uids"] == [request["owner_uid"]] * 4
            and issuer["gids"] == [request["owner_gid"]] * 4
            and issuer["namespace_pids"] == [issuer["pid"]]
            and host["cgroup"] == local["cgroup"], "ROOT_CUSTODY_ACTUAL_PRIVATE_PID_MAPPING_REQUIRED")
    mounts = [row.split() for row in _proc_text("/proc/self/mountinfo").splitlines()]
    proc = [row for row in mounts if row[4] == "/proc"]
    require(len(proc) == 1 and "ro" in proc[0][5].split(",")
            and proc[0][proc[0].index("-") + 1] == "proc", "ROOT_CUSTODY_PRIVATE_PROC_READONLY_REQUIRED")
    # Signal 0 never delivers a signal. The target is live in the original
    # host proc view, yet cannot be addressed in the actor's PID namespace.
    foreign = issuer["pid"]
    require(not Path("/proc/" + str(foreign)).exists(), "ROOT_CUSTODY_HOST_PID_COLLIDES_WITH_PRIVATE_ACTOR")
    try:
        os.kill(foreign, 0)
    except OSError as error:
        require(error.errno == errno.ESRCH, "ROOT_CUSTODY_FOREIGN_PROCESS_SIGNAL_NOT_NAMESPACE_DENIED")
        denied = {"signal": 0, "errno": error.errno, "host_pid": foreign, "host_process_actually_live": True}
    else:
        raise ValueError("ROOT_CUSTODY_FOREIGN_PROCESS_SIGNAL_ESCAPE")
    return {"schema": "porota.rc6.root-private-pid-native.v1",
            "local": local, "host": host, "issuer": issuer, "foreign_signal_native_denial": denied,
            "private_pid_namespace_before_actor_work": True}


def _actor_observation(case, request):
    import socket
    actor = kernel_process(os.getpid())
    require(actor["uids"] == [0] * 4, "ROOT_CUSTODY_PROBE_MUST_START_ROOT")
    parent = kernel_process(os.getppid())
    require(parent["uids"] == [0] * 4 and parent["cgroup"] == actor["cgroup"],
            "ROOT_CUSTODY_ROOT_PARENT_AND_CGROUP_INHERITANCE_REQUIRED")
    mapping = json.loads(_read(Path(request["namespace_receipt"]["path"]) / "private-broker.observed.json"))
    boundary = _private_identity(request, mapping)
    require(parent["pid"] == 1 and parent["pid_namespace_inode"] == actor["pid_namespace_inode"],
            "ROOT_CUSTODY_ROOT_PARENT_NOT_PRIVATE_PID1")
    denies = {}
    for name, target in (("foreign_kernel_pid_limit", Path("/proc/sys/kernel/pid_max")),):
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CLOEXEC)
        except OSError as error:
            require(error.errno in (errno.EACCES, errno.EPERM, errno.EROFS),
                    "ROOT_CUSTODY_FOREIGN_PROCESS_WRITER_DENIAL_UNKNOWN")
            denies[name] = {"errno": error.errno, "denied": True, "write_attempted": False}
        else:
            os.close(descriptor)
            raise ValueError("ROOT_CUSTODY_FOREIGN_PROCESS_WRITER_ALLOWED")
    libc = ctypes.CDLL(None, use_errno=True)
    ctypes.set_errno(0)
    returned = libc.syscall(308, -1, 0)  # invalid fd: EPERM must precede EBADF
    saved = ctypes.get_errno()
    require(returned == -1 and saved == errno.EPERM, "ROOT_CUSTODY_NATIVE_SETNS_DENIAL_REQUIRED")
    denies["setns"] = {"return": returned, "errno": saved, "denied": True}
    try:
        handle = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except OSError as error:
        require(error.errno in (errno.EAFNOSUPPORT, errno.EPERM, errno.EACCES), "ROOT_CUSTODY_FOREIGN_UNIX_SOCKET_NOT_DENIED")
        denies["AF_UNIX"] = {"errno": error.errno, "denied": True}
    else:
        handle.close()
        raise ValueError("ROOT_CUSTODY_FOREIGN_UNIX_SOCKET_ALLOWED")
    try:
        descriptor = os.open("/sys/fs/cgroup/cgroup.procs", os.O_WRONLY | os.O_CLOEXEC)
    except OSError as error:
        require(error.errno in (errno.EROFS, errno.EACCES, errno.EPERM), "ROOT_CUSTODY_CGROUP_WRITER_UNKNOWN")
        denies["foreign_cgroup_writer"] = {"errno": error.errno, "denied": True}
    else:
        os.close(descriptor)
        raise ValueError("ROOT_CUSTODY_FOREIGN_CGROUP_WRITER_ALLOWED")
    result = {"schema": "porota.rc6.root-custody-probe-actor.v1", "case": case,
              "root_before_drop": actor, "ROOT_parent": parent, "native_denials": denies,
              "pid_boundary": boundary, "root_protected_before_first_probe_instruction": True}
    if case == "nonroot-hang":
        from scripts import rc6_capacity_calibration as calibration
        result["drop"] = calibration.drop_privileges(request["owner_uid"], request["owner_gid"],
            source_binding={"source_sha": request["source_sha"], "source_tree": request["source_tree"]})
        result["after_drop"] = kernel_process(os.getpid())
    if case in ("root-hang", "nonroot-hang"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    sys.stdout.buffer.write(wire(result))
    sys.stdout.buffer.flush()
    if case == "failure-before-drop":
        os._exit(73)
    if case in ("root-hang", "nonroot-hang"):
        while True:
            signal.pause()


def _broker_probe(request, namespace, manager, controller):
    rows = []
    for case in ("root-success", "failure-before-drop", "root-hang", "nonroot-hang"):
        manager["pre_capture_kernel_state"]()
        output = namespace / (case + ".native.log")
        command = [sys.executable, "-I", "-B", str(ROOT / MEMBER), "--actor-case", case,
                   "--request", str(namespace / "custody-request.json")]
        kernel = manager["managed_native_child"](command, ROOT, output,
            {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"},
            1 if "hang" in case else 10, terminate_grace=2, progress_poll=5)
        require(manager["managed_custody_closed"](kernel), "ROOT_CUSTODY_PROBE_ACTUAL_OWN_FIN_REQUIRED")
        manager["pre_capture_kernel_state"]()
        os.chown(output, request["owner_uid"], request["owner_gid"], follow_symlinks=False)
        raw = _read(output)
        actor = json.loads(raw)
        require(actor["case"] == case and actor["root_before_drop"]["uids"] == [0] * 4
                and actor["ROOT_parent"]["pid"] == os.getpid()
                and actor["root_before_drop"]["pid"] == kernel["pid"]
                and actor["root_before_drop"]["cgroup"] == controller["private_PID1"]["local"]["cgroup"],
                "ROOT_CUSTODY_PROBE_ACTUAL_ROOT_OR_BIRTH_REBOUND")
        if "hang" in case:
            sends = kernel["owned_group_signal_observations"]
            require(kernel["timed_out"] is True and kernel["returncode"] == -signal.SIGKILL
                    and all(any(row["signal"] == number and row["outcome"] == "SENT" for row in sends)
                            for number in (signal.SIGTERM, signal.SIGKILL)),
                    "ROOT_CUSTODY_ACTUAL_ROOT_TERM2_KILL_FIN_PROOF_REQUIRED")
        elif case == "failure-before-drop":
            require(kernel["returncode"] == 73 and kernel["timed_out"] is False,
                    "ROOT_CUSTODY_PRE_DROP_FAILURE_NOT_AUTHENTIC")
        else:
            require(manager["managed_phase_green"](kernel), "ROOT_CUSTODY_ROOT_SUCCESS_NATIVE_RED")
        rows.append({"case": case, "kernel": kernel, "actor": actor,
                     "raw": {"path": output.name, "sha256": sha256(raw), "bytes": len(raw)}})
    return rows


def guardian_main(mode, request_path, name):
    require(os.getuid() == os.geteuid() == 0, "ROOT_CUSTODY_ACTUAL_ROOT_BROKER_REQUIRED")
    request, request_raw, namespace = _request(request_path)
    runtime = request["runtime_seconds"]
    controller = controller_snapshot(name, runtime)
    birth = {"schema": "porota.rc6.root-custody-controller-birth.v1", "binding": request["binding"],
             "request_sha256": sha256(request_raw), "controller": controller}
    if mode == "guard-hang":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    birth_path = namespace / ("guardian-birth.json" if mode == "guard-hang" else "broker-birth.json")
    _write_control(birth_path, birth, request["owner_uid"], request["owner_gid"])
    # Observe a native pidfd before any short-lived internal phase. Progress
    # remains the original five seconds; no thread changes subreaper custody.
    acknowledgement = birth_path.with_suffix(".observed.json")
    deadline = time.monotonic() + min(10, runtime)
    while not acknowledgement.exists():
        require(time.monotonic() < deadline, "ROOT_CUSTODY_NATIVE_OBSERVER_NOT_READY")
        time.sleep(0.05)
    acknowledged = json.loads(_read(acknowledgement))
    require(acknowledged.get("binding") == request["binding"]
            and acknowledged.get("birth_sha256") == sha256(wire(birth))
            and acknowledged.get("issuer") == request["issuer"]
            and acknowledged.get("controller_pid") == os.getpid(),
            "ROOT_CUSTODY_NATIVE_OBSERVER_ACK_REBOUND")
    bridge = original_directory_bridge(request)
    _write_control(namespace / ("guardian-bridge.json" if mode == "guard-hang" else "broker-bridge.json"),
                   bridge, request["owner_uid"], request["owner_gid"])
    if mode == "guard-hang":
        # PID1 must terminate/reap this broker even before it creates a worker.
        while True:
            signal.pause()
    _write_control(namespace / "guardian-controller.json", {**birth, "source_sha": request["source_sha"],
        "source_tree": request["source_tree"], "code_hashes": request["code_hashes"],
        "issuer": request["issuer"]}, request["owner_uid"], request["owner_gid"])
    manager = _manager(ROOT)
    manager["pre_capture_kernel_state"]()
    log = namespace / "private-broker-native.log"
    command = ["unshare", "--mount", "--propagation", "private", "--pid", "--fork",
               "--kill-child=SIGKILL", "--", "/bin/sh", "-c", BRIDGE_SHELL, "rc6-opaque-own-fd",
               sys.executable, "-I", "-B", str(ROOT / MEMBER),
               "--private-init", mode, "--request", str(request_path), "--unit", name]
    mapping_state = {}
    def observe_private(stage, pid, entered, deadline, log_fd):
        live_issuer = public_kernel_process(request["issuer"]["pid"])
        require(live_issuer["start_ticks"] == request["issuer"]["start_ticks"]
                and live_issuer["boot_id"] == request["issuer"]["boot_id"]
                and live_issuer["uids"] == [request["owner_uid"]] * 4
                and live_issuer["gids"] == [request["owner_gid"]] * 4
                and live_issuer["namespace_pids"] == [live_issuer["pid"]],
                "ROOT_CUSTODY_LIVE_ISSUER_DIED_OR_REBOUND")
        if mapping_state or not (namespace / "private-broker-birth.json").exists():
            return
        born = json.loads(_read(namespace / "private-broker-birth.json"))
        actual = kernel_process(born["host_private_PID1"]["pid"])
        issuer = live_issuer
        require(actual == born["host_private_PID1"] and actual["parent_pid"] == pid
                and actual["uids"] == [0] * 4 and actual["namespace_pids"] == [actual["pid"], 1]
                and actual["pid_namespace_inode"] != issuer_host_namespace(request, controller["actor"])
                and actual["cgroup"] == controller["actor"]["cgroup"]
                and born.get("binding") == request["binding"]
                and born.get("request_sha256") == sha256(request_raw),
                "ROOT_CUSTODY_NATIVE_PRIVATE_PID_BIRTH_REBOUND")
        descriptor = os.pidfd_open(actual["pid"], 0)
        require(kernel_process(actual["pid"]) == actual, "ROOT_CUSTODY_PRIVATE_PIDFD_BIRTH_RACE")
        mapping = {"schema": "porota.rc6.root-private-pid-guardian-observed.v1", "binding": request["binding"],
            "host_private_PID1": actual, "guardian_actual": controller["actor"], "issuer_actual": issuer,
            "source_sha": request["source_sha"], "source_tree": request["source_tree"],
            "request_sha256": sha256(request_raw), "original_directory_bridge": born["original_directory_bridge"]}
        _write_control(namespace / "private-broker.observed.json", mapping, request["owner_uid"], request["owner_gid"])
        mapping_state.update(pidfd=descriptor, mapping=mapping)
    saved = os.dup(0)
    try:
        os.dup2(BRIDGE_DESCRIPTOR, 0)
        kernel = manager["managed_native_child"](command, ROOT, log,
            {key: os.environ[key] for key in ("PATH", "RUNNER_TOOL_CACHE", "LANG", "LC_ALL") if key in os.environ},
            runtime - 10, terminate_grace=2, progress_poll=5, progress=observe_private)
    finally:
        os.dup2(saved, 0)
        os.close(saved)
    require(manager["managed_custody_closed"](kernel), "ROOT_CUSTODY_PRIVATE_BROKER_ACTUAL_FIN_REQUIRED")
    manager["pre_capture_kernel_state"]()
    require(type(mapping_state.get("pidfd")) is int, "ROOT_CUSTODY_NATIVE_PRIVATE_PIDFD_REQUIRED")
    poller = select.poll()
    poller.register(mapping_state["pidfd"], select.POLLIN | select.POLLHUP)
    try:
        require(bool(poller.poll(0)) and not Path("/proc/" + str(mapping_state["mapping"]["host_private_PID1"]["pid"])).exists(),
                "ROOT_CUSTODY_PRIVATE_PID1_ACTUAL_REAP_REQUIRED")
    finally:
        os.close(mapping_state["pidfd"])
    os.chown(log, request["owner_uid"], request["owner_gid"], follow_symlinks=False)
    raw = _read(log)
    from scripts import rc6_capacity_calibration as calibration
    result = calibration.decode(raw)
    result["external_guardian"] = {"controller": controller, "private_broker_kernel": kernel,
        "private_PID1_actual_mapping": mapping_state["mapping"], "private_PID1_native_pidfd_FIN": True,
        "private_broker_raw_sha256": sha256(raw), "original_manager_sha256": MANAGER_SHA256,
        "actual_root_FIN_closed": True, "root_phase_green": manager["managed_phase_green"](kernel)}
    sys.stdout.buffer.write(wire(result))
    sys.stdout.buffer.flush()
    return 0 if manager["managed_phase_green"](kernel) else 1


def _external_controller(proof, request, mapping):
    require(type(proof) is dict and proof.get("schema") == "porota.rc6.root-custody-controller-birth.v1"
            and proof.get("binding") == request["binding"]
            and proof.get("source_sha") == request["source_sha"] and proof.get("source_tree") == request["source_tree"]
            and proof.get("code_hashes") == request["code_hashes"] and proof.get("issuer") == request["issuer"],
            "ROOT_CUSTODY_EXACT_LIVE_CONTROLLER_PROOF_REQUIRED")
    controller = proof["controller"]
    guardian = mapping["guardian_actual"]
    require(guardian == controller["actor"] and guardian["uids"] == [0] * 4 and guardian["parent_pid"] == 1
            and guardian["cgroup"] == "0::/system.slice/" + controller["unit"]
            and _unit_file(controller["unit"]) == controller["transient_unit"],
            "ROOT_CUSTODY_LIVE_CONTROLLER_KERNEL_REBOUND")
    return guardian


def _null_stdin():
    descriptor = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
    try:
        os.dup2(descriptor, 0)
    finally:
        if descriptor != 0:
            os.close(descriptor)


def private_init(mode, request_path, name):
    """Frozen bootstrap: no fixture, package backend or actor before sealing."""
    require(os.getuid() == os.geteuid() == 0 and os.getpid() == 1,
            "ROOT_CUSTODY_PRIVATE_BOOTSTRAP_PID1_REQUIRED")
    from scripts import rc6_capacity_calibration as calibration
    raw = _read(request_path)
    request = calibration.decode(raw)
    status = dict(line.split(":", 1) for line in _proc_text("/proc/self/status").splitlines() if ":" in line)
    host = kernel_process(int(status["Pid"].strip()))
    require(host["namespace_pids"] == [host["pid"], 1], "ROOT_CUSTODY_PRIVATE_BOOTSTRAP_HOST_MAPPING_REQUIRED")
    bridge = original_directory_bridge(request)
    libc = ctypes.CDLL(None, use_errno=True)
    ctypes.set_errno(0)
    returned = libc.mount(b"proc", b"/proc", b"proc", ctypes.c_ulong(1 | 2 | 4 | 8), None)
    saved = ctypes.get_errno()
    require(returned == 0, "ROOT_CUSTODY_PRIVATE_PROC_MOUNT_FAILED:" + str(saved))
    namespace = Path(request["namespace_receipt"]["path"])
    _write_control(namespace / "private-broker-birth.json", {"binding": request["binding"],
        "request_sha256": sha256(raw), "host_private_PID1": host, "original_directory_bridge": bridge},
        request["owner_uid"], request["owner_gid"])
    deadline = time.monotonic() + 10
    while not (namespace / "private-broker.observed.json").exists():
        require(time.monotonic() < deadline, "ROOT_CUSTODY_EXTERNAL_NATIVE_MAPPING_NOT_READY")
        time.sleep(0.05)
    if mode == "probe":
        os.close(BRIDGE_DESCRIPTOR)
    return broker_main(mode, request_path, name)


def broker_main(mode, request_path, name):
    require(os.getuid() == os.geteuid() == 0 and os.getpid() == 1,
            "ROOT_CUSTODY_ACTUAL_PRIVATE_ROOT_PID1_REQUIRED")
    request, request_raw, namespace = _request(request_path, private=True)
    mapping = json.loads(_read(namespace / "private-broker.observed.json"))
    boundary = _private_identity(request, mapping)
    proof = json.loads(_read(namespace / "guardian-controller.json"))
    guardian = _external_controller(proof, request, mapping)
    require(boundary["host"]["cgroup"] == guardian["cgroup"], "ROOT_CUSTODY_PRIVATE_BROKER_CGROUP_REBOUND")
    controller = {"external": proof["controller"], "private_PID1": boundary,
                  "unit": name, "ROOT_actor_boundary_certified": True}
    manager = _manager(ROOT)
    before = manager["pre_capture_kernel_state"]()
    if mode == "probe":
        rows = _broker_probe(request, namespace, manager, controller)
        result = {"schema": SCHEMA, "request_sha256": sha256(request_raw), "binding": request["binding"],
                  "controller": controller, "cases": rows, "before": before,
                  "after": manager["pre_capture_kernel_state"](), "manager_sha256": MANAGER_SHA256,
                  "ROOT_parent_owns_actual_worker_wait4": True, "native_probe_proved": True,
                  "backing_allocated": False, "quota_mutations_attempted": False,
                  "mount_operations_attempted": False, "real_orders_sent": 0}
        sys.stdout.buffer.write(wire(result))
        sys.stdout.buffer.flush()
        return 0
    require(mode == "quota" and request.get("calibration_request") == str(namespace / "root-request.json")
            and sha256(_read(namespace / "root-request.json", 4 * 1024**2)) == request.get("calibration_request_sha256"),
            "ROOT_CUSTODY_FIXED_CALIBRATION_REQUEST_REQUIRED")
    proof_path = namespace / "live-root-controller.json"
    _write_control(proof_path, {**proof, "pid_boundary": boundary, "guardian_native_mapping": mapping,
        "calibration_request_sha256": request["calibration_request_sha256"]}, request["owner_uid"], request["owner_gid"])
    command = ["unshare", "--mount", "--propagation", "private", "--", "/bin/sh", "-c", BRIDGE_SHELL,
               "rc6-opaque-own-fd", sys.executable, "-I", "-B",
               str(ROOT / "scripts/rc6_capacity_calibration.py"), "--root-worker", request["calibration_request"],
               "--privileged-custody-proof", str(proof_path)]
    log = namespace / "root-worker-native.log"
    saved = os.dup(0)
    try:
        os.dup2(BRIDGE_DESCRIPTOR, 0)
        kernel = manager["managed_native_child"](command, ROOT, log,
            {key: os.environ[key] for key in ("PATH", "RUNNER_TOOL_CACHE", "LANG", "LC_ALL") if key in os.environ},
            request["runtime_seconds"] - 20, terminate_grace=2, progress_poll=5)
    finally:
        os.dup2(saved, 0)
        os.close(saved)
    require(manager["managed_custody_closed"](kernel), "ROOT_CUSTODY_QUOTA_WORKER_ACTUAL_FIN_REQUIRED")
    manager["pre_capture_kernel_state"]()
    os.chown(log, request["owner_uid"], request["owner_gid"], follow_symlinks=False)
    worker_raw = _read(log, 40 * 1024**2)
    from scripts import rc6_capacity_calibration as calibration
    worker = calibration.decode(worker_raw)
    require(worker.get("schema") == "porota.rc6.capacity-worker.v1", "ROOT_CUSTODY_CALIBRATION_WORKER_WIRE_REQUIRED")
    # Keep the original byte-exact worker report once. Relays carry only its
    # hash and actual FIN; repeating base64 RAW would triple physical storage.
    relay = {"schema": "porota.rc6.capacity-custody-worker.v1", "request_sha256": worker["request_sha256"],
        "root_worker": {"path": log.name, "sha256": sha256(worker_raw), "bytes": len(worker_raw)},
        "privileged_controller": {"controller": controller, "worker_kernel": kernel,
            "manager_sha256": MANAGER_SHA256, "root_native_FIN_closed": True,
            "root_native_phase_green": manager["managed_phase_green"](kernel)}}
    sys.stdout.buffer.write(wire(relay))
    sys.stdout.buffer.flush()
    return 0 if manager["managed_phase_green"](kernel) else 1


class CustodyWitness:
    def __init__(self, authority, receipt, binding):
        require(authority is _AUTHORITY, "ROOT_CUSTODY_SYNTHETIC_WITNESS_FORBIDDEN")
        self.authority, self.receipt, self.binding = authority, receipt, dict(binding)
        self.pid, self.start = os.getpid(), kernel_process(os.getpid())["start_ticks"]
        _WITNESSES[id(self)] = (self, self.pid, self.start, sha256(wire(receipt)), dict(binding))


def validate_witness(witness, *, binding):
    registered = _WITNESSES.get(id(witness))
    require(type(witness) is CustodyWitness and registered is not None and registered[0] is witness
            and witness.authority is _AUTHORITY
            and witness.pid == os.getpid() and witness.start == kernel_process(os.getpid())["start_ticks"]
            and registered[1:3] == (witness.pid, witness.start)
            and witness.binding == binding == registered[4]
            and sha256(wire(witness.receipt)) == registered[3]
            and witness.receipt.get("status") == "NATIVE_CUSTODY_PROVED",
            "ROOT_CUSTODY_ACTUAL_IN_PROCESS_WITNESS_REQUIRED")
    return witness.receipt


def verify_live_controller(proof, request):
    """Authorize the service-ancestor exception using actual live kernel state."""
    require(proof.get("calibration_request_sha256") == request.get("original_request_sha256"),
            "ROOT_CUSTODY_CALIBRATION_REQUEST_REBOUND")
    mapping = proof.get("guardian_native_mapping")
    require(type(mapping) is dict and mapping.get("binding") == request["binding"]
            and mapping.get("source_sha") == request["source_sha"] and mapping.get("source_tree") == request["source_tree"],
            "ROOT_CUSTODY_NATIVE_GUARDIAN_MAPPING_REQUIRED")
    guardian = _external_controller(proof, request, mapping)
    boundary = proof.get("pid_boundary")
    require(type(boundary) is dict, "ROOT_CUSTODY_ACTUAL_PRIVATE_BROKER_PROOF_REQUIRED")
    broker = kernel_process(1)
    require(broker == boundary["local"] and mapping["host_private_PID1"] == boundary["host"]
            and broker["uids"] == [0] * 4 and broker["pid_namespace_inode"] != guardian["pid_namespace_inode"],
            "ROOT_CUSTODY_PRIVATE_ROOT_BROKER_KERNEL_REBOUND")
    current = kernel_process(os.getpid())
    require(current["parent_pid"] == 1 and current["cgroup"] == broker["cgroup"]
            and current["pid_namespace_inode"] == broker["pid_namespace_inode"]
            and current["pid_namespace_inode"] != guardian["pid_namespace_inode"],
            "ROOT_CUSTODY_CURRENT_WORKER_NOT_ACTUAL_SERVICE_CHILD")
    _private_identity(request, mapping)
    return broker


def execute_service(namespace, command, *, cwd, environ, progress, timeout_seconds,
                    log_relative="producer-native.log", fin_label=None):
    """Transport only an authenticated own directory FD through existing stdio."""
    from scripts import rc6_authenticated_fixture_lifecycle as owned
    owned.authenticate(namespace)
    directory = os.open(namespace.path, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    saved = os.dup(0)
    try:
        os.dup2(directory, 0)
        return owned.execute_owned(namespace, command, cwd=cwd, environ=environ, progress=progress,
            timeout_seconds=timeout_seconds, log_relative=log_relative, fin_label=fin_label)
    finally:
        os.dup2(saved, 0)
        os.close(saved)
        os.close(directory)


def _observer(birth_path, binding, state, progress):
    """Read a trusted supervisory birth control only; never producer RAW."""
    def observe(stage, pid, entered, deadline, log_fd):
        if progress is not None:
            progress(stage, pid, entered, deadline, log_fd)
        if state.get("pidfd") is not None or not Path(birth_path).exists():
            return
        birth = json.loads(_read(birth_path))
        require(birth.get("binding") == binding, "ROOT_CUSTODY_BIRTH_BINDING_REBOUND")
        root_name = Path(birth_path).parent.name
        require(root_name.startswith("r6-"), "ROOT_CUSTODY_ORIGINAL_BIRTH_NAMESPACE_REQUIRED")
        nonce = base64.urlsafe_b64decode(root_name[3:] + "==").hex()
        expected_unit = unit_name(nonce, guard=Path(birth_path).name == "guardian-birth.json")
        actual = public_kernel_process(birth["controller"]["actor"]["pid"])
        require(actual == public_process_identity(birth["controller"]["actor"])
                and actual["uids"] == [0] * 4 and actual["parent_pid"] == 1,
                "ROOT_CUSTODY_SUPERVISORY_BIRTH_NOT_ACTUAL_ROOT")
        require(birth["controller"].get("unit") == expected_unit
                and actual["cgroup"] == "0::/system.slice/" + expected_unit,
                "ROOT_CUSTODY_SUPERVISORY_BIRTH_FOREIGN_UNIT")
        descriptor = os.pidfd_open(actual["pid"], 0)
        try:
            require(public_kernel_process(actual["pid"]) == actual, "ROOT_CUSTODY_PIDFD_BIRTH_RACE")
            observer = kernel_process(os.getpid())
            from scripts import rc6_capacity_calibration as calibration
            calibration.publish(Path(birth_path).with_suffix(".observed.json"), {
                "schema": "porota.rc6.root-controller-native-pidfd-observed.v1", "binding": binding,
                "birth_sha256": sha256(wire(birth)), "controller_pid": actual["pid"],
                "issuer": issuer_snapshot(observer)})
        except BaseException:
            os.close(descriptor)
            raise
        state.update(pidfd=descriptor, birth=birth)
    return observe


def _closed_service(state):
    descriptor = state.get("pidfd")
    require(type(descriptor) is int, "ROOT_CUSTODY_ACTUAL_CONTROLLER_PIDFD_REQUIRED")
    try:
        poller = select.poll()
        poller.register(descriptor, select.POLLIN | select.POLLHUP)
        require(bool(poller.poll(0)), "ROOT_CUSTODY_CONTROLLER_PIDFD_NOT_EXITED")
        actor = state["birth"]["controller"]["actor"]
        require(not Path("/proc/" + str(actor["pid"])).exists(), "ROOT_CUSTODY_PID1_ACTUAL_CONTROLLER_REAP_UNKNOWN")
        cgroup = Path("/sys/fs/cgroup") / actor["cgroup"].split("::", 1)[1].lstrip("/")
        if cgroup.exists():
            events = dict(line.split() for line in _proc_text(cgroup / "cgroup.events").splitlines())
            require(events.get("populated") == "0", "ROOT_CUSTODY_ACTUAL_SERVICE_CHILDREN_REMAIN")
        return {"pidfd_exit_observed": True, "PID1_actual_broker_reap_observed": True,
                "owned_cgroup_empty_or_removed": True, "root_actor": actor}
    finally:
        os.close(descriptor)
        state["pidfd"] = None


class CustodyFailure(ValueError):
    """RED diagnostic only; this object is never a ROOT custody witness."""
    def __init__(self, error, diagnostics):
        super().__init__(str(error))
        self.diagnostics = diagnostics


def _failure_destination(destination, namespace):
    path = Path(destination)
    require(path.is_absolute() and ".." not in path.parts and not os.path.lexists(path)
            and not path.is_relative_to(namespace.path) and not path.is_relative_to(ROOT)
            and not namespace.path.is_relative_to(path)
            and not any(p.is_symlink() for p in (path, *path.parents)),
            "ROOT_CUSTODY_FRESH_OUTSIDE_CAPTURE_REQUIRED")
    parent = path.parent.lstat()
    require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.geteuid(),
            "ROOT_CUSTODY_DIAGNOSTIC_PARENT_CUSTODY_REQUIRED")
    path.mkdir(mode=0o700)
    return path


def _diagnostic_bytes(path, raw):
    require(len(raw) <= CONTROL_BOUND, "ROOT_CUSTODY_DIAGNOSTIC_BYTES_BOUND")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(raw)
    finally:
        os.close(descriptor)


def _inactive_unit_readback(unit, raw, returncode):
    """Read-only absence of writers, never signal/reap or ROOT FIN credit."""
    require(type(unit) is str and re.fullmatch(r"rc6-native-[0-9a-f]{32}(?:-guard)?\.service", unit),
            "ROOT_CUSTODY_PRIVATE_UNIT_NAME_REQUIRED")
    expected = "/system.slice/" + unit
    fields = ("Id", "LoadState", "ActiveState", "SubState", "MainPID", "ControlPID", "ControlGroup")
    require(len(raw) <= 65536 and type(returncode) is int and returncode in (0, 4),
            "ROOT_CUSTODY_DIAGNOSTIC_UNIT_QUERY_UNKNOWN")
    rows = raw.decode("utf-8", errors="strict").splitlines()
    require(len(rows) == len(fields) and all("=" in row for row in rows),
            "ROOT_CUSTODY_DIAGNOSTIC_UNIT_FIELDS_UNKNOWN")
    values = dict(row.split("=", 1) for row in rows)
    require(set(values) == set(fields) and values["Id"] == unit
            and values["LoadState"] in ("loaded", "not-found")
            and values["ActiveState"] in ("inactive", "failed")
            and values["SubState"] in ("dead", "failed")
            and values["MainPID"] == values["ControlPID"] == "0"
            and values["ControlGroup"] in ("", expected),
            "ROOT_CUSTODY_DIAGNOSTIC_UNIT_STILL_ACTIVE_OR_UNKNOWN")
    # systemctl returns 4 for an absent/collected unit. This remains a RED
    # command: only exact not-found metadata plus actual cgroup absence/empty
    # permits diagnostic RAW capture; it provides no ROOT FIN or phase GREEN.
    require(returncode == 0 or values["LoadState"] == "not-found"
            and values["ActiveState"] == "inactive" and values["SubState"] == "dead"
            and values["ControlGroup"] == "", "ROOT_CUSTODY_DIAGNOSTIC_EXIT4_NOT_EXACT_ABSENCE")
    state = _cgroup_writer_absence(unit)
    return {"unit": unit, "properties": values, "cgroup_state": state,
            "scope": "READONLY_WRITER_ABSENCE_ONLY", "ROOT_FIN_claimed": False,
            "systemd_query_samples": 1, "fresh_systemd_recheck_after_capture": False}


def _cgroup_writer_absence(unit):
    require(type(unit) is str and re.fullmatch(r"rc6-native-[0-9a-f]{32}(?:-guard)?\.service", unit),
            "ROOT_CUSTODY_PRIVATE_UNIT_NAME_REQUIRED")
    expected = "/system.slice/" + unit
    cgroup = Path("/sys/fs/cgroup") / expected.lstrip("/")
    # A missing directory is usable only beneath a readable actual cgroup2
    # mount. Absence of /sys/fs/cgroup itself is not proof of no writers.
    mounts = [row.split() for row in _proc_text("/proc/self/mountinfo").splitlines()]
    require(any(row[4] == "/sys/fs/cgroup" and row[row.index("-") + 1] == "cgroup2" for row in mounts),
            "ROOT_CUSTODY_DIAGNOSTIC_CGROUP_MOUNT_UNKNOWN")
    try:
        before = cgroup.lstat()
    except FileNotFoundError:
        Path("/sys/fs/cgroup").lstat()
        state = "EXACT_CGROUP_ABSENT"
    else:
        require(stat.S_ISDIR(before.st_mode), "ROOT_CUSTODY_DIAGNOSTIC_CGROUP_NOT_DIRECTORY")
        events = dict(row.split() for row in _proc_text(cgroup / "cgroup.events").splitlines())
        require(events.get("populated") == "0" and not _proc_text(cgroup / "cgroup.procs").strip()
                and not _proc_text(cgroup / "cgroup.threads").strip()
                and before == cgroup.lstat(), "ROOT_CUSTODY_DIAGNOSTIC_CGROUP_WRITERS_UNKNOWN")
        state = "EXACT_CGROUP_NATIVE_EMPTY"
    return state


def journal_command(unit, boot_id):
    require(type(unit) is str and re.fullmatch(r"rc6-native-[0-9a-f]{32}(?:-guard)?\.service", unit),
            "ROOT_CUSTODY_PRIVATE_UNIT_NAME_REQUIRED")
    require(type(boot_id) is str and re.fullmatch(r"[0-9a-f]{32}", boot_id),
            "ROOT_CUSTODY_JOURNAL_ACTUAL_BOOT_REQUIRED")
    # PID1 and the pre-exec ROOT executor report UNIT=. The executor may not
    # yet have trusted _SYSTEMD_UNIT= for this service. Keep exact boot/ROOT
    # UID/unit matches without filtering its PID away. These are diagnostics.
    return ["/usr/bin/journalctl", "--no-pager", "--quiet", "--output=json", "--lines=30",
        "--output-fields=_BOOT_ID,_PID,_UID,_SYSTEMD_UNIT,_SYSTEMD_CGROUP,UNIT,SYSLOG_IDENTIFIER,MESSAGE,ERRNO,CODE_FILE,CODE_LINE,CODE_FUNC",
        "_BOOT_ID=" + boot_id, "_UID=0", "UNIT=" + unit, "+", "_BOOT_ID=" + boot_id, "_SYSTEMD_UNIT=" + unit]


def systemd_query_command(kind):
    """Fixed NONROOT read-only commands; no unit selection or mutation API."""
    commands = {
        "manager": ["/usr/bin/systemctl", "show", "--no-pager",
                    "--property=" + ",".join(SYSTEMD_CONFIG_FIELDS)],
        "version": ["/usr/bin/systemd-run", "--version"],
        "package": ["/usr/bin/dpkg-query", "--show", "--showformat=${Package}\t${Version}\n", "systemd"],
    }
    require(type(kind) is str and kind in commands, "ROOT_CUSTODY_FIXED_SYSTEMD_QUERY_REQUIRED")
    return list(commands[kind])


def _installed_systemd_binary(path):
    """Hash an installed trusted file, never claim it is PID1's pinned FD."""
    path = Path(path)
    require(str(path) in SYSTEMD_BINARY_PATHS, "ROOT_CUSTODY_FIXED_SYSTEMD_BINARY_REQUIRED")
    require(not any(item.is_symlink() for item in (path, *path.parents)),
            "ROOT_CUSTODY_SYSTEMD_BINARY_ALIAS")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and not before.st_mode & 0o022
                and 0 < before.st_size <= SYSTEMD_BINARY_BOUND, "ROOT_CUSTODY_SYSTEMD_BINARY_IDENTITY_UNKNOWN")
        hashed, size = hashlib.sha256(), 0
        while True:
            part = os.read(descriptor, 131072)
            if not part:
                break
            size += len(part)
            require(size <= SYSTEMD_BINARY_BOUND, "ROOT_CUSTODY_SYSTEMD_BINARY_BYTES_BOUND")
            hashed.update(part)
        after = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        identity = [getattr(before, name) for name in fields]
        require(size == before.st_size and identity == [getattr(after, name) for name in fields]
                and identity == [getattr(path.lstat(), name) for name in fields],
                "ROOT_CUSTODY_SYSTEMD_BINARY_CHANGED")
        return {"path": str(path), "bytes": size, "sha256": hashed.hexdigest(), "identity": identity,
                "running_PID1_or_executor_identity_verified": False}
    finally:
        os.close(descriptor)


def systemd_query_worker(kind):
    require(os.getuid() == os.geteuid() > 0 and public_kernel_process(os.getpid())["capabilities"]["CapEff"] == 0,
            "ROOT_CUSTODY_SYSTEMD_QUERY_NONROOT_ONLY")
    require(kind in ("manager", "version", "package", "binaries"), "ROOT_CUSTODY_FIXED_SYSTEMD_QUERY_REQUIRED")
    resource.setrlimit(resource.RLIMIT_FSIZE, (SYSTEMD_QUERY_BOUND, SYSTEMD_QUERY_BOUND))
    if kind != "binaries":
        command = systemd_query_command(kind)
        os.execve(command[0], command, {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
    rows = []
    for path in SYSTEMD_BINARY_PATHS:
        try:
            rows.append(_installed_systemd_binary(path))
        except (ValueError, OSError) as error:
            rows.append({"path": path, "status": "UNKNOWN", "errno": getattr(error, "errno", None),
                         "reason": str(error), "running_PID1_or_executor_identity_verified": False})
    sys.stdout.buffer.write(wire({"schema": "porota.rc6.installed-systemd-binaries.v1", "files": rows,
        "ROOT_custody_qualified": False, "ROOT_FIN_claimed": False}))
    return 0


def systemd_diagnostic_origin(raws):
    """Inspect original query bytes; observations cannot admit a ROOT actor."""
    result = {"status": "UNKNOWN", "ROOT_custody_qualified": False, "ROOT_FIN_claimed": False,
              "namespace_failure_cause": "UNKNOWN", "running_PID1_binary_identity_verified": False}
    try:
        require(type(raws) is dict and set(raws) == {"manager", "version", "package", "binaries"}
                and all(type(raw) is bytes and 0 < len(raw) <= SYSTEMD_QUERY_BOUND for raw in raws.values()),
                "ROOT_CUSTODY_SYSTEMD_ORIGINAL_QUERY_BYTES_REQUIRED")
        config = {}
        for line in raws["manager"].decode("utf-8", errors="strict").splitlines():
            key, value = line.split("=", 1)
            require(key in SYSTEMD_CONFIG_FIELDS and key not in config and value
                    and len(value) <= 512, "ROOT_CUSTODY_SYSTEMD_MANAGER_FIELDS_UNKNOWN")
            config[key] = value
        require(set(config) == set(SYSTEMD_CONFIG_FIELDS)
                and config["LogLevel"] in ("emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")
                and config["LogTarget"] in ("auto", "console", "console-prefixed", "journal", "journal-or-kmsg",
                    "kmsg", "syslog", "syslog-or-kmsg", "null"), "ROOT_CUSTODY_SYSTEMD_MANAGER_PROFILE_UNKNOWN")
        version = raws["version"].decode("utf-8", errors="strict").splitlines()[0]
        require(re.fullmatch(r"systemd [0-9]{3}(?:\.[0-9]+)?(?: \([^\r\n]{1,256}\))?", version),
                "ROOT_CUSTODY_SYSTEMD_VERSION_UNKNOWN")
        package = raws["package"].decode("utf-8", errors="strict").strip()
        require(re.fullmatch(r"systemd\t[0-9][A-Za-z0-9.+:~\-]{0,255}", package),
                "ROOT_CUSTODY_SYSTEMD_PACKAGE_UNKNOWN")
        package_version = package.split("\t", 1)[1]
        require(config["Version"] == package_version and ("(" + package_version + ")") in version,
                "ROOT_CUSTODY_SYSTEMD_LIVE_AND_INSTALLED_VERSION_REBOUND")
        from scripts import rc6_capacity_calibration as calibration
        binaries = calibration.decode(raws["binaries"])
        require(binaries.get("schema") == "porota.rc6.installed-systemd-binaries.v1"
                and binaries.get("ROOT_custody_qualified") is False and binaries.get("ROOT_FIN_claimed") is False
                and type(binaries.get("files")) is list
                and [row.get("path") for row in binaries["files"]] == list(SYSTEMD_BINARY_PATHS)
                and all(type(row.get("sha256")) is str and re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
                    and type(row.get("bytes")) is int and 0 < row["bytes"] <= SYSTEMD_BINARY_BOUND
                    and type(row.get("identity")) is list and len(row["identity"]) == 9
                    and all(type(item) is int for item in row["identity"])
                    and row["identity"][2] == 0 and stat.S_ISREG(row["identity"][4])
                    and row["identity"][4] & 0o022 == 0 and row["identity"][5] > 0
                    and row["identity"][6] == row["bytes"]
                    and row.get("running_PID1_or_executor_identity_verified") is False
                    for row in binaries["files"]), "ROOT_CUSTODY_SYSTEMD_INSTALLED_BINARY_UNKNOWN")
        return {**result, "status": "SCOPED_NONROOT_SYSTEMD_OBSERVATION_ONLY", "manager": config,
                "installed_version": version, "installed_package": package, "installed_binaries": binaries["files"],
                "executor_logging_journal_target_observed": config["LogTarget"] in ("journal", "journal-or-kmsg"),
                "default_stdio_is_guard_pipe_stdio_claimed": False}
    except (ValueError, TypeError, KeyError, UnicodeError, IndexError, AttributeError) as error:
        return {**result, "reason": str(error)}


def journal_query_worker(unit, boot_id):
    require(os.getuid() == os.geteuid() > 0 and public_kernel_process(os.getpid())["capabilities"]["CapEff"] == 0,
            "ROOT_CUSTODY_JOURNAL_NONROOT_ONLY")
    require(_proc_text("/proc/sys/kernel/random/boot_id").strip().replace("-", "") == boot_id,
            "ROOT_CUSTODY_JOURNAL_ACTUAL_BOOT_REQUIRED")
    command = journal_command(unit, boot_id)
    # Even a journal entry with a huge MESSAGE cannot grow the original
    # manager's log past this physical cap. SIGXFSZ is RED with actual FIN.
    resource.setrlimit(resource.RLIMIT_FSIZE, (CONTROL_BOUND, CONTROL_BOUND))
    os.execve(command[0], command, {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})


def journal_origin(raw, *, unit, boot_id, owner_uid, kernel):
    result = {"status": "UNKNOWN", "ROOT_custody_qualified": False, "ROOT_FIN_claimed": False,
              "unit": unit, "boot_id": boot_id, "record_count": 0}
    if kernel.get("timed_out") is not False or kernel.get("returncode") != 0:
        return {**result, "reason": "JOURNAL_QUERY_RED"}
    if not raw.strip():
        return {**result, "reason": "JOURNAL_EMPTY"}
    try:
        from scripts import rc6_capacity_calibration as calibration
        rows = raw.splitlines()
        require(len(raw) <= CONTROL_BOUND and 1 <= len(rows) <= 30, "ROOT_CUSTODY_JOURNAL_BOUND_REQUIRED")
        kinds = []
        for row in rows:
            value = calibration.decode(row)
            require(type(value) is dict and value.get("_BOOT_ID") == boot_id,
                    "ROOT_CUSTODY_JOURNAL_FOREIGN_BOOT")
            if value.get("_UID") == "0" and value.get("UNIT") == unit:
                require(type(value.get("_PID")) is str and value["_PID"].isdigit() and int(value["_PID"]) > 0,
                        "ROOT_CUSTODY_JOURNAL_ROOT_UNIT_PID_INVALID")
                if value["_PID"] == "1":
                    kinds.append("PID1_UNIT")
                else:
                    require(value.get("CODE_FILE") in ("src/core/exec-invoke.c", "src/core/namespace.c", "src/core/executor.c")
                            and type(value.get("CODE_FUNC")) is str and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value["CODE_FUNC"])
                            and type(value.get("CODE_LINE")) is str and value["CODE_LINE"].isdigit()
                            and int(value["CODE_LINE"]) > 0,
                            "ROOT_CUSTODY_JOURNAL_EXECUTOR_SOURCE_REQUIRED")
                    kinds.append("ROOT_UNIT_FIELD_DIAGNOSTIC_ONLY")
            else:
                require(value.get("_SYSTEMD_UNIT") == unit and value.get("_UID") in ("0", str(owner_uid))
                        and type(value.get("_PID")) is str and value["_PID"].isdigit() and int(value["_PID"]) > 1
                        and value.get("_SYSTEMD_CGROUP", "/system.slice/" + unit) == "/system.slice/" + unit,
                        "ROOT_CUSTODY_JOURNAL_FOREIGN_PROCESS_OR_UNIT")
                kinds.append("OWN_UNIT")
        return {**result, "status": "SCOPED_JOURNAL_OBSERVATION_ONLY", "record_count": len(rows), "origins": kinds}
    except (ValueError, TypeError, KeyError, UnicodeError) as error:
        return {**result, "reason": str(error)}


def _preserve_journal(namespace, unit, output, boot_id):
    from scripts import rc6_authenticated_fixture_lifecycle as owned
    require(_proc_text("/proc/sys/kernel/random/boot_id").strip().replace("-", "") == boot_id,
            "ROOT_CUSTODY_JOURNAL_ACTUAL_BOOT_REQUIRED")
    command = [sys.executable, "-I", "-B", str(ROOT / MEMBER), "--journal-unit", unit, "--journal-boot", boot_id]
    kernel, fin = owned.execute_owned(namespace, command, cwd=ROOT,
        environ={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, progress=None, timeout_seconds=5,
        log_relative="diagnostic-journal-query.log", fin_label="diagnostic-journal-query")
    owned.require_fin(namespace, fin)
    raw = _read(namespace.path / "diagnostic-journal-query.log", CONTROL_BOUND)
    fin_raw = _read(namespace.path / fin.control_name, 1024**2)
    _diagnostic_bytes(output / "journal-query-original.log", raw)
    _diagnostic_bytes(output / "journal-fin-original.json", fin_raw)
    return {"command": journal_command(unit, boot_id), "kernel": kernel,
        "original_FIN_sha256": sha256(fin_raw), "raw_sha256": sha256(raw), "raw_bytes": len(raw),
        "origin": journal_origin(raw, unit=unit, boot_id=boot_id, owner_uid=os.geteuid(), kernel=kernel)}


def _preserve_failure(context, destination, error):
    from scripts import rc6_authenticated_fixture_lifecycle as owned
    from scripts import rc6_capacity_calibration as calibration
    diagnostic = {"schema": "porota.rc6.root-custody-failure-diagnostic.v1", "status": "RED",
        "error_class": type(error).__name__, "error_signature": str(error),
        "scope": "ORIGINAL_NONROOT_CLIENT_ONLY", "ROOT_FIN": "UNKNOWN",
        "ROOT_custody_qualified": False, "cleanup_authorized": False, "namespace_removed": False,
        "reservation_recovery_credited_bytes": 0, "root_producer_RAW_read": False,
        "writer_absence": "UNKNOWN", "real_orders_sent": 0}
    namespace, fin = context.get("namespace"), context.get("fin")
    if namespace is None:
        return diagnostic
    diagnostic.update(namespace_retained=str(namespace.path), binding=namespace.binding)
    if fin is None or destination is None:
        return diagnostic
    output = None
    try:
        owned.require_fin(namespace, fin)
        client_fin_raw = _read(namespace.path / fin.control_name, 1024**2)
        diagnostic.update(client_kernel=context["kernel"], original_client_FIN_sha256=sha256(client_fin_raw),
            original_client_FIN_control=fin.control_name, original_client_FIN_authenticated=True)
        output = _failure_destination(destination, namespace)
        _diagnostic_bytes(output / "client-fin-original.json", client_fin_raw)
        # Query only the positively owned nonce unit; never enumerate, stop,
        # signal, reset-failed or mutate any systemd/cgroup state.
        unit = context["unit"]
        # This NONROOT query has no ROOT writer and is captured even if the
        # unit is active/unknown or the query is RED/empty. It never certifies
        # ROOT custody and cannot grant cleanup or recovery credit.
        try:
            boot_id = context.get("boot_id", _proc_text("/proc/sys/kernel/random/boot_id").strip()).replace("-", "")
            diagnostic["journal"] = _preserve_journal(namespace, unit, output, boot_id)
        except (ValueError, OSError, KeyError, TypeError) as journal_error:
            diagnostic["journal"] = {"status": "UNKNOWN", "reason": str(journal_error),
                "ROOT_custody_qualified": False, "ROOT_FIN_claimed": False}
        query_kernel, query_fin = owned.execute_owned(namespace, ["systemctl", "show", "--no-pager",
            "--property=Id,LoadState,ActiveState,SubState,MainPID,ControlPID,ControlGroup", "--", unit],
            cwd=ROOT, environ={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, progress=None,
            timeout_seconds=5, log_relative="diagnostic-unit-query.log", fin_label="diagnostic-unit-query")
        owned.require_fin(namespace, query_fin)
        query_raw = _read(namespace.path / "diagnostic-unit-query.log", 65536)
        diagnostic["unit_query_kernel"] = query_kernel
        diagnostic["unit_query_sha256"] = sha256(query_raw)
        _diagnostic_bytes(output / "unit-query-original.log", query_raw)
        try:
            require(query_kernel["timed_out"] is False and query_kernel["returncode"] in (0, 4),
                    "ROOT_CUSTODY_DIAGNOSTIC_QUERY_FIN_RED")
            before = _inactive_unit_readback(unit, query_raw, query_kernel["returncode"])
        except (ValueError, OSError, KeyError, UnicodeError) as readback_error:
            diagnostic["writer_absence_error"] = str(readback_error)
        else:
            # The original raw stderr/stdout is opened only after actual
            # owned client FIN and exact unit/cgroup absence of all writers.
            require((namespace.path / context["log"]).lstat().st_size <= CONTROL_BOUND,
                    "ROOT_CUSTODY_DIAGNOSTIC_LOG_BYTES_BOUND")
            capture = owned.capture_required_evidence(namespace, query_fin, output / "closed-client-raw",
                [context["log"], "diagnostic-unit-query.log",
                 *(["diagnostic-journal-query.log"] if "raw_sha256" in diagnostic["journal"] else [])])
            # Only cgroup state is read again. The single systemd query above
            # is retained verbatim and never advertised as a fresh second one.
            after = _cgroup_writer_absence(unit)
            require(after == before["cgroup_state"], "ROOT_CUSTODY_DIAGNOSTIC_WRITER_ABSENCE_CHANGED")
            diagnostic.update(writer_absence=before, root_producer_RAW_read=True,
                cgroup_revalidated_after_capture=True,
                diagnostic_capture=str(capture.path), diagnostic_capture_manifest_sha256=capture.manifest_sha256,
                ROOT_custody_qualified=False)
        calibration.publish(output / "failure.json", diagnostic)
        diagnostic["diagnostic_root"] = str(output)
    except (ValueError, OSError, KeyError, TypeError) as diagnostic_error:
        diagnostic["preservation_error"] = str(diagnostic_error)
        if output is not None:
            try:
                calibration.publish(output / "failure.json", diagnostic)
                diagnostic["diagnostic_root"] = str(output)
            except (ValueError, OSError):
                pass  # The primary error and UNKNOWN must never be replaced.
    return diagnostic


def prove_custody(*, source, source_sha, source_tree, code_hashes, parent, binding, progress,
                  capture_parent=None):
    context = {}
    try:
        return _prove_custody(source=source, source_sha=source_sha, source_tree=source_tree,
            code_hashes=code_hashes, parent=parent, binding=binding, progress=progress,
            capture_parent=capture_parent, failure_context=context)
    except (ValueError, OSError, KeyError, TypeError) as error:
        raise CustodyFailure(error, _preserve_failure(context, capture_parent, error)) from error


def _prove_custody(*, source, source_sha, source_tree, code_hashes, parent, binding, progress,
                  capture_parent=None, failure_context):
    from scripts import rc6_authenticated_fixture_lifecycle as owned
    from scripts import rc6_capacity_calibration as calibration
    require(os.getuid() == os.geteuid() > 0, "ROOT_CUSTODY_NONROOT_ISSUER_REQUIRED")
    libc = ctypes.CDLL(None, use_errno=True)
    require(libc.prctl(39, 0, 0, 0, 0) == 0, "ROOT_CUSTODY_ISSUER_NNP_PREVENTS_EXISTING_SUDO")
    # Self namespace access remains valid with dumpable=0. Capture it before
    # the reduction; foreign proc namespace links are never needed by ROOT.
    issuer = kernel_process(os.getpid())
    require(libc.prctl(4, 0, 0, 0, 0) == 0 and libc.prctl(3, 0, 0, 0, 0) == 0,
            "ROOT_CUSTODY_ISSUER_DUMPABLE_ZERO_REQUIRED")
    native_contract(source_sha, source_tree, code_hashes)
    namespace = owned.create_namespace(parent, binding)
    failure_context["namespace"] = namespace
    failure_context["boot_id"] = issuer["boot_id"]
    receipt = owned.namespace_receipt(namespace)
    request = {"schema": "porota.rc6.root-custody-request.v1", "source_root": str(Path(source).absolute()),
        "source_sha": source_sha, "source_tree": source_tree, "code_hashes": code_hashes,
        "owner_uid": os.getuid(), "owner_gid": os.getgid(), "binding": binding,
        "namespace_receipt": receipt, "issuer": issuer_snapshot(issuer),
        "runtime_seconds": 6}
    request_path = namespace.path / "custody-request.json"
    calibration.publish(request_path, request)
    state = {}
    guard = service_command(source, request_path, nonce=namespace.nonce, control_root=namespace.path,
                            runtime_seconds=6, broker_mode="guard-hang", guard=True)
    kernel, fin = execute_service(namespace, guard, cwd=source,
        environ={key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ},
        log_relative="guardian-native.log", timeout_seconds=20, fin_label="root-guardian-proof",
        progress=_observer(namespace.path / "guardian-birth.json", binding, state, progress))
    failure_context.update(kernel=kernel, fin=fin, log="guardian-native.log", unit=unit_name(namespace.nonce, guard=True))
    require(kernel["returncode"] != 0 and kernel["timed_out"] is False,
            "ROOT_CUSTODY_PID1_GUARD_TIMEOUT_NOT_NATIVE")
    guardian_fin = _closed_service(state)
    request["runtime_seconds"] = 45
    probe_request = namespace.path / "probe-request.json"
    calibration.publish(probe_request, request)
    # Actors use the original immutable request for identity; the broker's
    # guard is longer than the fixed four-case probe, never a payload producer.
    probe = service_command(source, probe_request, nonce=namespace.nonce, control_root=namespace.path,
                            runtime_seconds=45, broker_mode="probe")
    state = {}
    kernel, fin = execute_service(namespace, probe, cwd=source,
        environ={key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ},
        log_relative="custody-native.log", timeout_seconds=PROBE_LIMIT, fin_label="root-worker-proof",
        progress=_observer(namespace.path / "broker-birth.json", binding, state, progress))
    failure_context.update(kernel=kernel, fin=fin, log="custody-native.log", unit=unit_name(namespace.nonce))
    controller_fin = _closed_service(state)
    require(owned._phase_green(fin), "ROOT_CUSTODY_NATIVE_BROKER_PROBE_RED")
    raw = _read(namespace.path / "custody-native.log")
    result = calibration.decode(raw)
    require(result.get("schema") == SCHEMA and result.get("request_sha256") == sha256(wire(request))
            and result.get("binding") == binding and result.get("native_probe_proved") is True
            and [row["case"] for row in result.get("cases", [])]
                == ["root-success", "failure-before-drop", "root-hang", "nonroot-hang"],
            "ROOT_CUSTODY_NATIVE_ORIGINAL_PROBE_BYTES_REQUIRED")
    expected = ["custody-request.json", "probe-request.json", "guardian-native.log", "custody-native.log",
                "guardian-birth.json", "guardian-birth.observed.json", "broker-birth.json", "broker-birth.observed.json",
                "guardian-bridge.json", "broker-bridge.json", "guardian-controller.json", "private-broker-native.log",
                "private-broker-birth.json", "private-broker.observed.json",
                *[row["raw"]["path"] for row in result["cases"]]]
    destination = (namespace.path.parent / ("root-custody-" + namespace.nonce)
                   if capture_parent is None else Path(capture_parent))
    require(destination.is_absolute() and ".." not in destination.parts
            and not destination.exists() and not destination.is_relative_to(namespace.path)
            and not destination.is_relative_to(ROOT)
            and not any(p.is_symlink() for p in (destination, *destination.parents)),
            "ROOT_CUSTODY_FRESH_OUTSIDE_CAPTURE_REQUIRED")
    capture = owned.capture_required_evidence(namespace, fin, destination, expected)
    cleanup = owned.cleanup_namespace(namespace, fin, capture)
    accepted = {"schema": SCHEMA, "status": "NATIVE_CUSTODY_PROVED", "binding": binding,
        "source_sha": source_sha, "source_tree": source_tree, "code_hashes": code_hashes,
        "native_root_proof": result, "guardian_actual_FIN": guardian_fin, "controller_actual_FIN": controller_fin,
        "capture_manifest_sha256": capture.manifest_sha256, "capture_root": str(capture.path),
        "cleanup": cleanup, "proof_only_no_G0_qualification": True, "real_orders_sent": 0}
    return CustodyWitness(_AUTHORITY, accepted, binding)


def quota_service_request(request, *, namespace_receipt, source, code_hashes):
    issuer = kernel_process(os.getpid())
    return {"schema": "porota.rc6.root-custody-request.v1", "source_root": str(Path(source).absolute()),
        "source_sha": request["source_sha"], "source_tree": request["source_tree"], "code_hashes": code_hashes,
        "owner_uid": request["owner_uid"], "owner_gid": request["owner_gid"], "binding": request["binding"],
        "namespace_receipt": namespace_receipt, "issuer": issuer_snapshot(issuer),
        # The original outer ceiling includes PID1's TERM2 and final reap.
        "runtime_seconds": (300 if request["mode"] == "capability" else 10800) - 10,
        "calibration_request": str(Path(namespace_receipt["path"]) / "root-request.json"),
        "calibration_request_sha256": sha256(wire(request))}


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guardian", choices=("probe", "quota", "guard-hang"))
    parser.add_argument("--private-init", choices=("probe", "quota"))
    parser.add_argument("--broker", choices=("probe", "quota", "guard-hang"))
    parser.add_argument("--actor-case", choices=("root-success", "failure-before-drop", "root-hang", "nonroot-hang"))
    parser.add_argument("--request", type=Path)
    parser.add_argument("--unit")
    parser.add_argument("--journal-unit")
    parser.add_argument("--journal-boot")
    parser.add_argument("--systemd-query", choices=("manager", "version", "package", "binaries"))
    args = parser.parse_args()
    if args.systemd_query:
        require(not any((args.guardian, args.private_init, args.broker, args.actor_case, args.request,
                         args.unit, args.journal_unit, args.journal_boot)),
                "ROOT_CUSTODY_SYSTEMD_EXACT_READONLY_ROLE_REQUIRED")
        return systemd_query_worker(args.systemd_query)
    if args.journal_unit or args.journal_boot:
        require(args.journal_unit and args.journal_boot and not any((args.guardian, args.private_init, args.broker,
                args.actor_case, args.request, args.unit)), "ROOT_CUSTODY_JOURNAL_EXACT_READONLY_ROLE_REQUIRED")
        return journal_query_worker(args.journal_unit, args.journal_boot)
    require(args.request is not None, "ROOT_CUSTODY_FIXED_REQUEST_REQUIRED")
    if args.actor_case:
        request, _, _ = _request(args.request, private=True)
        _actor_observation(args.actor_case, request)
        return 0
    if args.private_init:
        require(not args.guardian and not args.broker and args.unit, "ROOT_CUSTODY_EXPLICIT_ROLE_REQUIRED")
        return private_init(args.private_init, args.request, args.unit)
    require(bool(args.guardian) != bool(args.broker) and args.unit, "ROOT_CUSTODY_EXPLICIT_ROLE_REQUIRED")
    return guardian_main(args.guardian, args.request, args.unit) if args.guardian else broker_main(args.broker, args.request, args.unit)


if __name__ == "__main__":
    raise SystemExit(cli())
