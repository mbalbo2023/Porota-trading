"""Pure replay of Source-authenticated captured PRIVATE_PID_BOUNDARY_V2 custody.

The caller authenticates the exact Actions artifact, Source and outer owned FIN
before calling this decoder. ``read_original`` returns captured bytes, never a
live path. Successful linkage is documentary evidence only: no live witness,
owner acceptance, privileged launch, G0 GREEN or bootstrap authority is minted.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
from pathlib import PurePosixPath
import re
import signal
import stat

from scripts import rc6_controlled_native_child_manager as manager
from scripts import rc6_heavy_test_preflight as capacity
from scripts import rc6_native_namespace_filter as native_filter
from scripts import rc6_private_pid_boundary as boundary
from scripts import rc6_privileged_custody as custody
from scripts import rc6_root_actor_seal as root_seal

MEMBER = "scripts/rc6_private_pid_capture_replay.py"
SCHEMA = "porota.rc6.private-pid-captured-custody-replay.v2"
MARKER = ".porota-generated-fixture-owner.json"
CASES = ("root-success", "failure-before-drop", "root-hang", "nonroot-hang")
FIN_NAMES = ("producer-owned-fin-root-guardian-proof.json", "producer-owned-fin-root-worker-proof.json")
REQUIRED = frozenset((MARKER, *FIN_NAMES, "custody-request.json", "probe-request.json",
    "guardian-native.log", "custody-native.log", "guardian-unit-original.json", "broker-unit-original.json",
    "guardian-birth.json", "guardian-birth.observed.json", "broker-birth.json", "broker-birth.observed.json",
    "guardian-bridge.json", "broker-bridge.json", "guardian-controller.json", "private-broker-native.log",
    "private-mount-setup.native.json", "private-broker-birth.json", "private-broker.observed.json",
    "private-parent-seal.json", *[name + ".native.log" for name in CASES], "private-pid-v2.native.log",
    "boundary-before.challenge.json", "boundary-before.guardian.json",
    "boundary-after.challenge.json", "boundary-after.guardian.json"))
HASH_MEMBERS = frozenset((MEMBER, custody.MEMBER, custody.MANAGER_MEMBER, custody.ROOT_SEAL_MEMBER,
    boundary.MEMBER, "scripts/rc6_native_namespace_filter.py"))
REQUEST_KEYS = frozenset(("schema", "source_root", "source_sha", "source_tree", "code_hashes", "owner_uid",
    "owner_gid", "binding", "namespace_receipt", "issuer", "host_PID1", "host_boundary", "runtime_seconds"))
BOUND = custody.CONTROL_BOUND
MANIFEST_BOUND = 4 * 1024**2
TOTAL_BOUND = 128 * 1024**2


def require(condition, error):
    if not condition:
        raise ValueError(error)


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _hex(value, size=64):
    return type(value) is str and re.fullmatch("[0-9a-f]{" + str(size) + "}", value) is not None


def _relative(name):
    require(type(name) is str and 0 < len(name) <= 4096 and "\x00" not in name
            and str(PurePosixPath(name)) == name and not name.startswith("/")
            and all(part not in ("", ".", "..") for part in name.split("/")),
            "PIDV2_CAPTURE_RELATIVE_SOURCE_INVALID")
    return name


def _absolute(name):
    require(type(name) is str and 0 < len(name) <= 4096 and "\x00" not in name
            and PurePosixPath(name).is_absolute() and str(PurePosixPath(name)) == name
            and ".." not in PurePosixPath(name).parts and name != "/",
            "PIDV2_CAPTURE_ABSOLUTE_IDENTITY_INVALID")
    return PurePosixPath(name)


def _decode(raw, name):
    require(type(raw) is bytes and 0 < len(raw) <= (MANIFEST_BOUND if name == "manifest.json" else BOUND),
            "PIDV2_CAPTURE_JSON_BYTES_BOUND:" + name)
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, "PIDV2_CAPTURE_DUPLICATE_JSON_KEY:" + name)
            value[key] = item
        return value
    def constant(_):
        raise ValueError("PIDV2_CAPTURE_NONFINITE_JSON:" + name)
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("PIDV2_CAPTURE_JSON_INVALID:" + name) from error
    require(type(value) is dict, "PIDV2_CAPTURE_JSON_OBJECT_REQUIRED:" + name)
    return value


def _process(value, *, full=False):
    # A pure projection, never a procfs read or an authority factory.
    custody.public_process_identity(value)
    require(not full or type(value.get("pid_namespace_inode")) is int and value["pid_namespace_inode"] > 0,
            "PIDV2_CAPTURE_PROCESS_NAMESPACE_MISSING")
    require(int(value["start_ticks"]) > 0, "PIDV2_CAPTURE_PROCESS_BIRTH_INVALID")
    return value


def _same_public(left, right):
    return custody.public_process_identity(left) == custody.public_process_identity(right)


def _kernel(kernel, *, supervisor_pid, supervisor_uid, limit, green):
    require(type(kernel) is dict and manager.managed_custody_closed(kernel)
            and type(kernel.get("pid")) is int and type(kernel.get("wait4_reaped_pid")) is int
            and type(kernel.get("returncode")) is int and type(kernel.get("timed_out")) is bool
            and kernel.get("supervisor_pid") == supervisor_pid
            and type(kernel.get("supervisor_pid")) is int
            and type(kernel.get("supervisor_native_tid")) is int and kernel["supervisor_native_tid"] > 0
            and type(kernel.get("launcher_management_deadline_seconds")) in (int, float)
            and kernel["launcher_management_deadline_seconds"] == limit
            and kernel.get("sigint_failure_grace_seconds") == 0
            and kernel.get("owned_cleanup_management_bound_seconds") == 5
            and type(kernel.get("wall_seconds")) in (int, float)
            and math.isfinite(kernel["wall_seconds"]) and 0 <= kernel["wall_seconds"] <= limit + 5
            and type(kernel.get("command")) is list and kernel["command"]
            and all(type(word) is str and "\x00" not in word for word in kernel["command"]),
            "PIDV2_CAPTURE_ORIGINAL_WAIT4_FIN_INVALID")
    identity = kernel.get("supervisor_kernel_identity_before_spawn")
    require(type(identity) is dict and identity.get("Pid") == str(kernel["supervisor_native_tid"])
            and identity.get("Tgid") == str(supervisor_pid)
            and type(identity.get("Uid")) is str and identity["Uid"].split() == [str(supervisor_uid)] * 4,
            "PIDV2_CAPTURE_SUPERVISOR_IDENTITY_REBOUND")
    require(not green or manager.managed_phase_green(kernel), "PIDV2_CAPTURE_NATIVE_PHASE_RED")
    return kernel


def _fin(value, *, binding, nonce, issuer, limit, green):
    require(value.get("schema") == "porota.rc6.generated-fixture-owned-fin.v1"
            and value.get("binding") == binding and value.get("namespace_nonce") == nonce
            and value.get("manager_sha256") == custody.MANAGER_SHA256
            and value.get("actual_owned_fin_closed") is True
            and value.get("global_or_other_producer_FIN_claimed") is False
            and type(value.get("real_orders_sent")) is int and value["real_orders_sent"] == 0,
            "PIDV2_CAPTURE_OWNED_FIN_CONTROL_REBOUND")
    kernel = _kernel(value["kernel"], supervisor_pid=issuer["pid"], supervisor_uid=issuer["uid"],
        limit=limit, green=green)
    require(value.get("phase_green") is manager.managed_phase_green(kernel),
            "PIDV2_CAPTURE_OWNED_FIN_PHASE_FORGED")
    return kernel


def _unit_controller(controller, snapshot, *, request, guard):
    nonce = request["namespace_receipt"]["namespace_nonce"]
    name = "rc6-native-" + nonce + ("-guard" if guard else "") + ".service"
    runtime = 6 if guard else 45
    require(type(controller) is dict and controller.get("schema") == "porota.rc6.pid1-root-controller-live.v1"
            and controller.get("unit") == name and controller.get("host_boundary") == request["host_boundary"]
            and controller.get("PID1") == request["host_PID1"]
            and controller.get("external_PID1_identity_pending_live_ACK") is True
            and controller.get("systemd_guard_exists_before_guardian_exec") is True
            and controller.get("ROOT_actor_boundary_certified") is False
            and controller.get("root_controller_claimed_by_metadata_only") is False,
            "PIDV2_CAPTURE_CONTROLLER_REBOUND")
    actor = _process(controller["actor"], full=True)
    require(actor["parent_pid"] == 1 and actor["uids"] == [0] * 4 and actor["gids"] == [0] * 4
            and actor["namespace_pids"] == [actor["pid"]]
            and actor["pid_namespace_inode"] == request["issuer"]["pid_namespace_inode"]
            and actor["boot_id"] == request["issuer"]["boot_id"]
            and actor["cgroup"] == "0::/system.slice/" + name
            and type(controller.get("mount_namespace_inode")) is int
            and controller["mount_namespace_inode"] != request["host_boundary"]["mount_namespace_inode"],
            "PIDV2_CAPTURE_CONTROLLER_BIRTH_REBOUND")
    forbidden = sum(1 << number for number in (16, 17, 19, 22, 25, 30, 38, 39, 40))
    require(actor["capabilities"]["CapEff"] & (1 << 5)
            and all(actor["capabilities"][key] & forbidden == 0 for key in ("CapPrm", "CapEff", "CapBnd")),
            "PIDV2_CAPTURE_CONTROLLER_CAPABILITIES_FORBIDDEN")
    require(snapshot.get("schema") == "porota.rc6.root-owned-transient-unit-diagnostic.v1"
            and snapshot.get("unit") == name and snapshot.get("binding") == request["binding"]
            and snapshot.get("source_sha") == request["source_sha"]
            and snapshot.get("source_tree") == request["source_tree"]
            and snapshot.get("scope") == "DIAGNOSTIC_ONLY"
            and snapshot.get("ROOT_custody_qualified") is False and snapshot.get("ROOT_FIN_claimed") is False,
            "PIDV2_CAPTURE_ORIGINAL_UNIT_REBOUND")
    original = snapshot["original_unit"]
    require(type(original) is dict and original.get("path") == "/run/systemd/transient/" + name
            and type(original.get("raw_base64")) is str, "PIDV2_CAPTURE_ORIGINAL_UNIT_BYTES_MISSING")
    try:
        raw = base64.b64decode(original["raw_base64"], validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("PIDV2_CAPTURE_ORIGINAL_UNIT_ENCODING_INVALID") from error
    require(0 < len(raw) <= custody.SYSTEMD_QUERY_BOUND and _hash(raw) == original.get("sha256"),
            "PIDV2_CAPTURE_ORIGINAL_UNIT_HASH_REBOUND")
    identity = original.get("identity")
    require(type(identity) is list and len(identity) == 5 and all(type(x) is int for x in identity)
            and all(x > 0 for x in identity[:2]) and identity[2:4] == [0, 0] and stat.S_ISREG(identity[4]),
            "PIDV2_CAPTURE_ORIGINAL_ROOT_UNIT_IDENTITY_INVALID")
    unit = controller["transient_unit"]
    parsed = custody.parse_unit_properties(raw)
    require(unit == {**original, **parsed}, "PIDV2_CAPTURE_UNIT_METADATA_DIFFERS_FROM_ORIGINAL")
    properties = parsed["properties"]
    custody.require_guard_log_profile(name, properties)
    require(custody._seconds(properties.get("RuntimeMaxSec")) == runtime
            and custody._seconds(properties.get("TimeoutStopSec")) == 2
            and properties.get("KillMode") == "control-group"
            and properties.get("SendSIGKILL") in ("yes", "true", "1")
            and properties.get("FinalKillSignal") in ("SIGKILL", "KILL", "9")
            and all(properties.get(key) in ("yes", "true", "1") for key in (
                "PrivateMounts", "PrivateDevices", "ProtectControlGroups", "NoNewPrivileges",
                "ProtectKernelTunables", "ProtectKernelModules", "ProtectKernelLogs"))
            and properties.get("ProtectSystem") == "strict" and properties.get("ProtectHome") == "read-only"
            and properties.get("Restart") == "no"
            and parsed["path_lists"].get("ReadWritePaths") == [request["namespace_receipt"]["path"]]
            and set(properties.get("RestrictAddressFamilies", "").split()) == {"AF_INET", "AF_INET6"}
            and custody.allowed_namespace_bitmap(properties.get("RestrictNamespaces")) == 0x20020000
            and properties.get("SystemCallErrorNumber") in ("EPERM", "1")
            and custody.exact_system_call_filter(properties.get("SystemCallFilter"))
            and "/proc/1" in parsed["path_lists"].get("InaccessiblePaths", [])
            and not any("/proc/1/root" in value for value in properties.values()),
            "PIDV2_CAPTURE_UNIT_SECURITY_BOUNDARY_CHANGED")
    return actor


def _service_ack(birth, acknowledgement, actual_fin, *, request, request_raw, controller):
    require(birth.get("schema") == "porota.rc6.root-custody-controller-birth.v1"
            and birth.get("binding") == request["binding"] and birth.get("request_sha256") == _hash(request_raw)
            and birth.get("controller") == controller,
            "PIDV2_CAPTURE_SERVICE_BIRTH_REQUEST_REBOUND")
    actor = controller["actor"]
    require(acknowledgement.get("schema") == "porota.rc6.root-controller-native-pidfd-observed.v1"
            and acknowledgement.get("binding") == request["binding"]
            and acknowledgement.get("birth_sha256") == _hash(custody.wire(birth))
            and acknowledgement.get("issuer") == request["issuer"]
            and acknowledgement.get("controller_pid") == actor["pid"]
            and acknowledgement.get("actual_controller") == custody.public_process_identity(actor)
            and acknowledgement.get("actual_PID1") == request["host_PID1"]
            and acknowledgement.get("host_boundary") == request["host_boundary"]
            and acknowledgement.get("ROOT_custody_qualified_by_JSON") is False,
            "PIDV2_CAPTURE_NATIVE_PIDFD_ACK_REBOUND")
    require(type(actual_fin) is dict and all(actual_fin.get(key) is True for key in (
        "pidfd_exit_observed", "PID1_actual_broker_reap_observed", "owned_cgroup_empty_or_removed",
        "external_PID1_birth_pidfd_ACK_verified"))
        and actual_fin.get("root_actor") == actor
        and actual_fin.get("external_PID1_live_kernel_identity") == request["host_PID1"]
        and actual_fin.get("ROOT_custody_qualified_by_JSON") is False,
        "PIDV2_CAPTURE_EXTERNAL_ROOT_FIN_REBOUND")


def _raw_link(row, name, raws, values):
    require(type(row) is dict and row.get("raw") == {"path": name, "sha256": _hash(raws[name]), "bytes": len(raws[name])},
            "PIDV2_CAPTURE_NATIVE_RAW_REFERENCE_REBOUND:" + name)
    require(row.get("observation", row.get("actor")) == values[name],
            "PIDV2_CAPTURE_NATIVE_RAW_OBSERVATION_REBOUND:" + name)


def _scope_state(state):
    require(type(state) is dict and state.get("kernel_echild_verified") is True
            and type(state.get("subreaper_state_before")) is int and state["subreaper_state_before"] in (0, 1)
            and state.get("subreaper_state_after") == state["subreaper_state_before"]
            and state.get("scope") == "THIS_SUPERVISOR_ONLY; NO_GLOBAL_PROC_SCAN_OR_STATE_MUTATION",
            "PIDV2_CAPTURE_PRIVATE_MANAGER_ECHILD_MISSING")


def _mount_diagnostic(diagnostic, record, *, raw, request):
    require(record == {"relative_source": "private-mount-setup.native.json", "sha256": _hash(raw),
        "bytes": len(raw), "scope": "DIAGNOSTIC_ONLY", "ROOT_FIN_claimed": False, "ROOT_custody_qualified": False}
        and diagnostic.get("schema") == "porota.rc6.private-mount-setup-diagnostic.v1"
        and diagnostic.get("status") == "SETUP_READBACK_ONLY_NO_ROOT_QUALIFICATION"
        and diagnostic.get("binding") == request["binding"] and diagnostic.get("mode") == "probe"
        and diagnostic.get("scope") == "DIAGNOSTIC_ONLY" and diagnostic.get("ROOT_FIN_claimed") is False
        and diagnostic.get("ROOT_custody_qualified") is False and diagnostic.get("foreign_writable_rows") == [],
        "PIDV2_CAPTURE_ORIGINAL_MOUNT_DIAGNOSTIC_REBOUND")
    decoded = {}
    for name in ("mountinfo_before", "mountinfo_before_readonly", "mountinfo_after_recursive_readonly", "mountinfo_after"):
        encoded = diagnostic[name]
        require(type(encoded) is dict and set(encoded) == {"raw_base64", "sha256", "bytes"}
                and type(encoded.get("raw_base64")) is str and type(encoded.get("bytes")) is int,
                "PIDV2_CAPTURE_ORIGINAL_MOUNTINFO_INVALID")
        try:
            original = base64.b64decode(encoded["raw_base64"], validate=True)
        except (ValueError, TypeError) as error:
            raise ValueError("PIDV2_CAPTURE_ORIGINAL_MOUNTINFO_ENCODING_INVALID") from error
        require(0 < len(original) == encoded["bytes"] <= 65536 and _hash(original) == encoded.get("sha256"),
                "PIDV2_CAPTURE_ORIGINAL_MOUNTINFO_HASH_REBOUND")
        decoded[name] = custody._mount_rows(original.decode("utf-8", errors="strict"))
    require(all("ro" in row["options"] and "rw" not in row["options"]
                for row in decoded["mountinfo_after_recursive_readonly"]),
            "PIDV2_CAPTURE_RECURSIVE_READONLY_READBACK_MISSING")
    claim = request["namespace_receipt"]
    control = diagnostic["private_control_after"]
    require(type(control) is dict and set(control) == {"identity", "private_mount_id", "target"}
            and control == diagnostic.get("private_control_before") and control.get("identity") == claim["identity"]
            and control.get("target") == claim["path"] and type(control.get("private_mount_id")) is int
            and control["private_mount_id"] > 0, "PIDV2_CAPTURE_PRIVATE_CONTROL_MOUNT_REBOUND")
    after = decoded["mountinfo_after"]
    own = [row for row in after if row["target"] == claim["path"]]
    require(len(own) == 1 and own[0]["id"] == control["private_mount_id"]
            and own[0]["device"] == claim["identity"][0] and "rw" in own[0]["options"]
            and all("ro" in row["options"] and "rw" not in row["options"]
                    for row in after if row["target"] != claim["path"]),
            "PIDV2_CAPTURE_FOREIGN_WRITABLE_MOUNT")
    operations = diagnostic.get("operations")
    require(type(operations) is list and len(operations) == 6, "PIDV2_CAPTURE_PRIVATE_SETUP_OPERATIONS_MISSING")
    for row, (operation, target) in zip(operations[:4], (("old_proc_detach", "/proc"), ("proc_mount", "/proc"),
            ("old_devpts_detach", "/dev/pts"), ("devpts_mount", "/dev/pts"))):
        require(row == {"operation": operation, "target": target, "returncode": 0, "errno": 0},
                "PIDV2_CAPTURE_PRIVATE_SETUP_OPERATION_REBOUND")
    for row, readonly in zip(operations[4:], (True, False)):
        require(row == {"operation": "mount_setattr", "syscall_number": 442,
            "descriptor_scope": "AT_FDCWD" if readonly else "FRESH_PRIVATE_CONTROL", "path": "/" if readonly else "",
            "flags": 0x8100 if readonly else 0x1000, "attr_set": 1 if readonly else 0,
            "attr_clr": 0 if readonly else 1, "propagation": 0, "userns_fd": 0, "attribute_bytes": 32,
            "returncode": 0, "errno": 0}, "PIDV2_CAPTURE_RECURSIVE_READONLY_OPERATION_REBOUND")
    return diagnostic["mountinfo_after"]["sha256"], control["private_mount_id"], after


def _worker_command(command, *, source, request_path, case=None):
    suffix = ["-I", "-B", "-c", custody.ROOT_ACTOR_RUNPY_LAUNCHER, str(source / custody.MEMBER)]
    suffix += ["--boundary-worker"] if case is None else ["--actor-case", case]
    suffix += ["--request", str(request_path)]
    require(len(command) == len(suffix) + 1 and command[1:] == suffix
            and _absolute(command[0]), "PIDV2_CAPTURE_ORIGINAL_ACTOR_COMMAND_CHANGED")


def _private_pid_mapping(proof, values, *, request, request_raw, guardian):
    external = proof["external_guardian"]
    mapping = values["private-broker.observed.json"]
    born = values["private-broker-birth.json"]
    require(external.get("private_PID1_actual_mapping") == mapping
            and external.get("controller") == guardian
            and external.get("private_PID1_native_pidfd_FIN") is True
            and external.get("actual_root_FIN_closed") is True
            and external.get("root_phase_green") is True
            and external.get("original_manager_sha256") == custody.MANAGER_SHA256
            and mapping.get("schema") == "porota.rc6.root-private-pid-guardian-observed.v1"
            and mapping.get("binding") == request["binding"] and born.get("binding") == request["binding"]
            and mapping.get("request_sha256") == born.get("request_sha256") == _hash(request_raw)
            and mapping.get("source_sha") == request["source_sha"] and mapping.get("source_tree") == request["source_tree"]
            and mapping.get("guardian_actual") == guardian["actor"]
            and mapping.get("host_boundary") == born.get("host_boundary") == request["host_boundary"]
            and mapping.get("external_PID1_ACK") == values["broker-birth.observed.json"]
            and mapping.get("private_mount_setup") == born.get("private_mount_setup"),
            "PIDV2_CAPTURE_PRIVATE_PID1_MAPPING_REBOUND")
    host = _process(mapping["host_private_PID1"], full=True)
    issuer = _process(mapping["issuer_actual"])
    require(host == born.get("host_private_PID1") and host["uids"] == [0] * 4
            and host["gids"] == [0] * 4 and host["namespace_pids"] == [host["pid"], 1]
            and host["pid_namespace_inode"] != request["issuer"]["pid_namespace_inode"]
            and host["cgroup"] == guardian["actor"]["cgroup"]
            and host["boot_id"] == issuer["boot_id"] == request["issuer"]["boot_id"]
            and all(issuer.get(key) == request["issuer"][key] for key in ("pid", "start_ticks", "boot_id"))
            and issuer["uids"] == [request["owner_uid"]] * 4 and issuer["gids"] == [request["owner_gid"]] * 4
            and issuer["namespace_pids"] == [issuer["pid"]], "PIDV2_CAPTURE_PRIVATE_PID1_BIRTH_REBOUND")
    kernel = _kernel(external["private_broker_kernel"], supervisor_pid=guardian["actor"]["pid"],
        supervisor_uid=0, limit=35, green=True)
    require(host["parent_pid"] == kernel["pid"], "PIDV2_CAPTURE_PRIVATE_PID1_PARENT_WAIT4_REBOUND")
    command = kernel["command"]
    prefix = ["unshare", "--mount", "--propagation", "private", "--pid", "--fork", "--kill-child=SIGKILL",
              "--", "/bin/sh", "-c", custody.BRIDGE_SHELL, "rc6-opaque-own-fd"]
    suffix = ["-I", "-B", str(PurePosixPath(request["source_root"]) / custody.MEMBER),
              "--private-init", "probe", "--request", str(PurePosixPath(request["namespace_receipt"]["path"]) / "probe-request.json"),
              "--unit", guardian["unit"]]
    require(len(command) == len(prefix) + 1 + len(suffix) and command[:len(prefix)] == prefix
            and _absolute(command[len(prefix)]) and command[len(prefix) + 1:] == suffix,
            "PIDV2_CAPTURE_PRIVATE_MANAGER_COMMAND_CHANGED")
    bridge = values["broker-bridge.json"]
    claim = request["namespace_receipt"]
    expected_bridge = {"schema": "porota.rc6.original-own-directory-fd-native.v1", "descriptor": 3,
        "identity": claim["identity"], "original_mount_id": claim["mount_id"], "marker_sha256": claim["marker_sha256"],
        "namespace_nonce": claim["namespace_nonce"], "foreign_host_proc_exposed": False}
    require(bridge == values["guardian-bridge.json"] == mapping.get("original_directory_bridge")
            == born.get("original_directory_bridge") == expected_bridge,
            "PIDV2_CAPTURE_OPAQUE_BRIDGE_REBOUND")
    mounts = mapping["private_mount_setup"]
    require(type(mounts) is dict and all(mounts.get(key) is True for key in (
        "old_host_proc_detached", "single_readonly_private_proc", "private_devpts_newinstance_created",
        "foreign_mounts_readonly_required")) and mounts.get("quota_ancestor_sealed") is False,
        "PIDV2_CAPTURE_PRIVATE_MOUNT_SETUP_INCOMPLETE")
    private = proof["controller"]["private_PID1"]
    local = _process(private["local"], full=True)
    require(private.get("schema") == "porota.rc6.root-private-pid-native.v1"
            and private.get("host") == host and private.get("issuer") == issuer
            and local["pid"] == 1 and local["uids"] == [0] * 4 and local["gids"] == [0] * 4
            and local["namespace_pids"] == [1] and local["start_ticks"] == host["start_ticks"]
            and local["boot_id"] == host["boot_id"] and local["pid_namespace_inode"] == host["pid_namespace_inode"]
            and local["cgroup"] == host["cgroup"] and private.get("private_pid_namespace_before_actor_work") is True
            and private.get("foreign_signal_native_denial") == {"signal": 0, "errno": 3,
                "host_pid": issuer["pid"], "host_process_actually_live": True},
            "PIDV2_CAPTURE_LOCAL_PRIVATE_PID1_REBOUND")
    return mapping, bridge


def _validate_capture(receipt, *, binding, code_hashes, read_original):
    capacity.validate_binding(binding)
    require(callable(read_original) and type(receipt) is dict and type(code_hashes) is dict
            and HASH_MEMBERS <= set(code_hashes) and all(_relative(name) and _hex(value) for name, value in code_hashes.items())
            and code_hashes[custody.MANAGER_MEMBER] == custody.MANAGER_SHA256,
            "PIDV2_CAPTURE_EXACT_SOURCE_CODE_HASHES_REQUIRED")
    require(receipt.get("schema") == custody.SCHEMA and receipt.get("status") == "NATIVE_CUSTODY_PROVED"
            and receipt.get("binding") == binding and receipt.get("source_sha") == binding["candidate_sha"]
            and receipt.get("source_tree") == binding["candidate_tree"] and receipt.get("code_hashes") == code_hashes
            and receipt.get("proof_only_no_G0_qualification") is True and receipt.get("contract_revision_required") is True
            and receipt.get("original_PROC_EACCES_equivalence_approved") is False
            and type(receipt.get("real_orders_sent")) is int and receipt["real_orders_sent"] == 0
            and "resource_probe" not in receipt, "PIDV2_CAPTURE_RECEIPT_FORGED_OR_REBOUND")
    capture_path = _absolute(receipt["capture_root"])
    require(capture_path.name == "revision-custody-raw", "PIDV2_CAPTURE_OUTER_NAMESPACE_PREFIX_CHANGED")
    manifest_raw = read_original("manifest.json")
    manifest = _decode(manifest_raw, "manifest.json")
    require(_hash(manifest_raw) == receipt.get("capture_manifest_sha256")
            and manifest.get("schema") == "porota.rc6.generated-fixture-required-capture.v1"
            and manifest.get("binding") == binding and _hex(manifest.get("namespace_nonce"), 32)
            and manifest.get("actual_owned_fin_closed") is True and manifest.get("owned_fin_kind") == "NATIVE_WAIT4_OWNED_FIN"
            and manifest.get("phase_green") is True and type(manifest.get("real_orders_sent")) is int
            and manifest["real_orders_sent"] == 0, "PIDV2_CAPTURE_INNER_MANIFEST_REBOUND")
    rows = manifest.get("files")
    require(type(rows) is list and len(rows) == len(REQUIRED)
            and all(type(row) is dict and set(row) == {"relative_source", "capture_file", "bytes", "sha256", "source_identity"}
                for row in rows)
            and [row["relative_source"] for row in rows] == sorted(REQUIRED), "PIDV2_CAPTURE_REQUIRED_ORIGINAL_FILES_MISSING")
    raws, values, identities = {}, {}, set()
    for index, row in enumerate(rows):
        name = _relative(row["relative_source"])
        require(row.get("capture_file") == str(index).zfill(4) + ".raw" and type(row.get("bytes")) is int
                and 0 <= row["bytes"] <= BOUND and _hex(row.get("sha256")), "PIDV2_CAPTURE_FILE_RECORD_INVALID")
        raw = read_original(name)
        require(type(raw) is bytes and len(raw) == row["bytes"] and _hash(raw) == row["sha256"],
                "PIDV2_CAPTURE_ORIGINAL_BYTES_MISSING_OR_CHANGED:" + name)
        identity = row["source_identity"]
        require(type(identity) is list and len(identity) == 9 and all(type(x) is int for x in identity)
                and identity[0] > 0 and identity[1] > 0 and identity[2] > 0 and identity[3] > 0
                and identity[4:7] == [0o600, 1, len(raw)] and all(x >= 0 for x in identity[7:])
                and tuple(identity[:2]) not in identities, "PIDV2_CAPTURE_SOURCE_INODE_REBOUND")
        identities.add(tuple(identity[:2]))
        raws[name] = raw
        if name != "guardian-native.log":
            values[name] = _decode(raw, name)
    require(type(manifest.get("total_bytes")) is int and manifest["total_bytes"] == sum(map(len, raws.values()))
            and manifest["total_bytes"] <= TOTAL_BOUND, "PIDV2_CAPTURE_TOTAL_BYTES_REBOUND")
    request, probe = values["custody-request.json"], values["probe-request.json"]
    require(set(request) == REQUEST_KEYS and request.get("schema") == "porota.rc6.root-custody-request.v1" and request.get("binding") == binding
            and request.get("source_sha") == binding["candidate_sha"] and request.get("source_tree") == binding["candidate_tree"]
            and request.get("code_hashes") == code_hashes and request.get("runtime_seconds") == 6
            and type(request.get("owner_uid")) is int and request["owner_uid"] > 0
            and type(request.get("owner_gid")) is int and request["owner_gid"] > 0
            and probe == {**request, "runtime_seconds": 45, "boundary_revision": boundary.REVISION,
                "boundary_host_proc_device": probe.get("boundary_host_proc_device")}
            and type(probe.get("boundary_host_proc_device")) is int and probe["boundary_host_proc_device"] > 0
            and "resource_probe" not in request and "boundary_revision" not in request
            and raws["custody-request.json"] == custody.wire(request) and raws["probe-request.json"] == custody.wire(probe),
            "PIDV2_CAPTURE_REQUEST_SOURCE_CASE_BINDING_REBOUND")
    source = _absolute(request["source_root"])
    claim = request["namespace_receipt"]
    original_path = _absolute(claim["path"])
    nonce = claim.get("namespace_nonce")
    require(_hex(nonce, 32) and nonce == manifest["namespace_nonce"]
            and original_path.name == "r6-" + base64.urlsafe_b64encode(bytes.fromhex(nonce)).decode().rstrip("=")
            and not capture_path.is_relative_to(original_path) and not original_path.is_relative_to(capture_path)
            and claim.get("schema") == "porota.rc6.generated-fixture-consumer-binding.v1"
            and claim.get("binding") == binding and claim.get("cleanup_authority_granted") is False
            and type(claim.get("identity")) is list and len(claim["identity"]) == 5
            and all(type(x) is int and x > 0 for x in claim["identity"])
            and claim["identity"][2:] == [request["owner_uid"], request["owner_gid"], 0o700]
            and type(claim.get("mount_id")) is int and claim["mount_id"] > 0,
            "PIDV2_CAPTURE_ORIGINAL_NAMESPACE_REBOUND")
    require(all(row["source_identity"][0] == claim["identity"][0]
                and row["source_identity"][2:4] == [request["owner_uid"], request["owner_gid"]] for row in rows),
            "PIDV2_CAPTURE_SOURCE_IDENTITY_FOREIGN")
    marker = values[MARKER]
    require(marker.get("schema") == "porota.rc6.generated-fixture-owner.v1" and marker.get("binding") == binding
            and marker.get("namespace_nonce") == nonce and marker.get("path") == str(original_path)
            and marker.get("identity") == claim["identity"] and marker.get("mount_id") == claim["mount_id"]
            and marker.get("runtime_paths_authorized") is False and marker.get("real_orders_sent") == 0
            and _hash(raws[MARKER]) == claim.get("marker_sha256"), "PIDV2_CAPTURE_OWNER_MARKER_REBOUND")
    issuer = request["issuer"]
    require(type(issuer) is dict and set(issuer) == set(custody.ISSUER_FIELDS)
            and type(issuer.get("pid")) is int and issuer["pid"] > 0
            and type(issuer.get("pid_namespace_inode")) is int and issuer["pid_namespace_inode"] > 0
            and type(issuer.get("start_ticks")) is str and issuer["start_ticks"].isdigit() and int(issuer["start_ticks"]) > 0,
            "PIDV2_CAPTURE_ORIGINAL_ISSUER_BIRTH_INVALID")
    pid1 = _process(request["host_PID1"])
    require(pid1["pid"] == 1 and pid1["parent_pid"] == 0 and pid1["uids"] == [0] * 4
            and pid1["gids"] == [0] * 4 and pid1["boot_id"] == issuer["boot_id"],
            "PIDV2_CAPTURE_EXTERNAL_PID1_REBOUND")
    custody.require_host_boundary(request["host_boundary"])
    service_kernels = []
    controllers = []
    for guard, prefix, fin_name, receipt_name in (
            (True, "guardian", FIN_NAMES[0], "guardian_actual_FIN"),
            (False, "broker", FIN_NAMES[1], "controller_actual_FIN")):
        selected = request if guard else probe
        birth = values[prefix + "-birth.json"]
        controller = birth["controller"]
        _unit_controller(controller, values[prefix + "-unit-original.json"], request=selected, guard=guard)
        _service_ack(birth, values[prefix + "-birth.observed.json"], receipt[receipt_name],
            request=selected, request_raw=raws["custody-request.json" if guard else "probe-request.json"], controller=controller)
        kernel = _fin(values[fin_name], binding=binding, nonce=nonce,
            issuer={**issuer, "uid": request["owner_uid"]}, limit=20 if guard else custody.PROBE_LIMIT, green=not guard)
        command = kernel["command"]
        name = controller["unit"]
        suffix = ["--", "/bin/sh", "-c", custody.BRIDGE_SHELL, "rc6-opaque-own-fd"]
        require(command[:4] == ["sudo", "-n", "--", "systemd-run"] and "--unit=" + name in command
                and "--working-directory=" + str(source) in command and "--wait" in command
                and command[-15:-10] == suffix and _absolute(command[-10])
                and command[-9:] == ["-I", "-B", str(source / custody.MEMBER), "--guardian",
                    "guard-hang" if guard else "probe", "--request",
                    str(original_path / ("custody-request.json" if guard else "probe-request.json")), "--unit", name],
                "PIDV2_CAPTURE_OWNED_SERVICE_COMMAND_REBOUND")
        # The systemd guard is a real failed timeout test; its outer supervisor
        # must have reaped it within the original 20s bound without timing out.
        require(not guard or kernel["returncode"] != 0 and kernel["timed_out"] is False,
                "PIDV2_CAPTURE_GUARD_TIMEOUT_NOT_NATIVE")
        service_kernels.append(kernel)
        controllers.append(controller)
    require(manifest.get("kernel_sha256") == capacity.digest(service_kernels[1]),
            "PIDV2_CAPTURE_FINAL_MANAGER_KERNEL_HASH_REBOUND")
    proof = values["custody-native.log"]
    require(receipt.get("native_root_proof") == proof and proof.get("schema") == custody.SCHEMA
            and proof.get("binding") == binding and proof.get("request_sha256") == _hash(raws["probe-request.json"])
            and proof.get("manager_sha256") == custody.MANAGER_SHA256
            and all(proof.get(key) is True for key in ("ROOT_parent_owns_actual_worker_wait4", "native_probe_proved",
                "actor_mount_operations_attempted", "actor_quota_syscall_probes_attempted", "namespace_setup_mounts_executed",
                "root_actor_seal_probe_only", "contract_revision_required", "proof_only_no_G0_qualification"))
            and all(proof.get(key) is False for key in ("backing_allocated", "actor_mount_operations_succeeded",
                "actor_quota_mutations_succeeded", "quota_backing_mounts_attempted", "original_PROC_EACCES_equivalence_approved"))
            and type(proof.get("real_orders_sent")) is int and proof["real_orders_sent"] == 0,
            "PIDV2_CAPTURE_ROOT_PROOF_DIFFERS_FROM_ORIGINAL")
    private_raw = raws["private-broker-native.log"]
    private_original = values["private-broker-native.log"]
    original_evidence = private_original["private_pid_boundary_v2"]
    evidence = proof["private_pid_boundary_v2"]
    expected_private = {key: value for key, value in proof.items() if key != "external_guardian"}
    expected_private["private_pid_boundary_v2"] = {key: evidence[key] for key in (
        "observation", "kernel", "native_inherited_proc_control", "raw")}
    require(private_original == expected_private
            and proof["external_guardian"].get("private_broker_raw_sha256") == _hash(private_raw),
            "PIDV2_CAPTURE_PRIVATE_BROKER_ORIGINAL_BYTES_REBOUND")
    guardian = controllers[1]
    require(proof["controller"].get("external") == guardian and proof["controller"].get("unit") == guardian["unit"]
            and proof["controller"].get("ROOT_actor_boundary_certified") is True,
            "PIDV2_CAPTURE_PRIVATE_CONTROLLER_REBOUND")
    guardian_control = values["guardian-controller.json"]
    require(guardian_control == {**values["broker-birth.json"], "source_sha": request["source_sha"],
        "source_tree": request["source_tree"], "code_hashes": code_hashes, "issuer": issuer},
        "PIDV2_CAPTURE_GUARDIAN_SOURCE_CONTROL_REBOUND")
    mapping, bridge = _private_pid_mapping(proof, values, request=probe, request_raw=raws["probe-request.json"], guardian=guardian)
    mountinfo_sha256, private_control_mount_id, captured_mount_rows = _mount_diagnostic(values["private-mount-setup.native.json"],
        mapping["private_mount_setup"].get("mount_diagnostic"), raw=raws["private-mount-setup.native.json"], request=probe)
    for state in (proof["before"], proof["after"]):
        _scope_state(state)
    require(proof["before"]["subreaper_state_after"] == proof["after"]["subreaper_state_before"],
            "PIDV2_CAPTURE_PRIVATE_SUBREAPER_NOT_RESTORED")
    seal = values["private-parent-seal.json"]
    source_binding = {"source_sha": request["source_sha"], "source_tree": request["source_tree"]}
    root_seal.validate_evidence(seal, owner_uid=request["owner_uid"], owner_gid=request["owner_gid"], source_binding=source_binding)
    local = proof["controller"]["private_PID1"]["local"]
    require(seal == proof["controller"].get("root_actor_seal") and seal.get("scope") == "ROOT_POST_SETUP_ONLY"
            and seal.get("module_sha256") == code_hashes[custody.ROOT_SEAL_MEMBER]
            and seal["identity"]["pid"] == 1 and str(seal["identity"]["start_ticks"]) == local["start_ticks"]
            and seal["identity"]["pid_namespace_inode"] == local["pid_namespace_inode"]
            and seal["identity"].get("boot_id") == local["boot_id"]
            and seal["boundary"].get("host_pid_namespace_inode") == issuer["pid_namespace_inode"]
            and seal["boundary"].get("host_mount_namespace_inode") == request["host_boundary"]["mount_namespace_inode"]
            and seal["boundary"].get("host_device_number") == request["host_boundary"]["device_number"]
            and seal["boundary"].get("host_devpts_device_number") == request["host_boundary"]["devpts_device_number"]
            and seal["boundary"].get("control_root") == str(original_path)
            and seal["boundary"].get("control_identity") == claim["identity"]
            and seal["boundary"].get("mountinfo_sha256") == mountinfo_sha256,
            "PIDV2_CAPTURE_PARENT_ROOT_SEAL_REBOUND")
    cases = proof.get("cases")
    require(type(cases) is list and [row.get("case") for row in cases] == list(CASES),
            "PIDV2_CAPTURE_ORIGINAL_FOUR_ROOT_CASES_MISSING")
    for row, case in zip(cases, CASES):
        _raw_link(row, case + ".native.log", raws, values)
        actor = row["actor"]
        kernel = _kernel(row["kernel"], supervisor_pid=1, supervisor_uid=0,
            limit=1 if "hang" in case else 10, green=case == "root-success")
        _worker_command(kernel["command"], source=source, request_path=original_path / "custody-request.json", case=case)
        birth = _process(actor["root_before_drop"], full=True)
        parent = _process(actor["ROOT_parent"])
        require(actor.get("schema") == "porota.rc6.root-custody-probe-actor.v1" and actor.get("case") == case
                and actor.get("root_protected_before_first_probe_instruction") is True
                and actor.get("contract_revision_required") is True
                and actor.get("original_PROC_EACCES_equivalence_approved") is False
                and birth["pid"] == kernel["pid"] and birth["parent_pid"] == 1 and birth["uids"] == [0] * 4
                and birth["gids"] == [0] * 4 and birth["cgroup"] == local["cgroup"]
                and birth["capabilities"] == seal["capabilities"]
                and birth["pid_namespace_inode"] == local["pid_namespace_inode"] and birth["namespace_pids"] == [birth["pid"]]
                and birth["boot_id"] == local["boot_id"]
                and parent["pid"] == 1 and parent["uids"] == [0] * 4 and parent["gids"] == [0] * 4
                and parent["start_ticks"] == local["start_ticks"] and parent["cgroup"] == birth["cgroup"]
                and parent["capabilities"] == seal["capabilities"]
                and actor.get("parent_root_actor_seal_sha256") == _hash(raws["private-parent-seal.json"]),
                "PIDV2_CAPTURE_ROOT_CASE_BIRTH_REBOUND:" + case)
        child = actor["root_actor_seal"]
        root_seal.validate_inheritance_evidence(child, seal, owner_uid=request["owner_uid"],
            owner_gid=request["owner_gid"], source_binding=source_binding)
        require(child["identity"]["pid"] == birth["pid"] and str(child["identity"]["start_ticks"]) == birth["start_ticks"],
                "PIDV2_CAPTURE_ROOT_CASE_SEAL_BIRTH_REBOUND:" + case)
        denials = actor["native_denials"]
        require(set(denials) == {"foreign_kernel_pid_limit", "setns", "AF_UNIX", "foreign_cgroup_writer"}
                and all(row.get("denied") is True for row in denials.values())
                and all(denials[name].get("errno") in (1, 13, 30) for name in ("foreign_kernel_pid_limit", "foreign_cgroup_writer"))
                and denials["foreign_kernel_pid_limit"].get("write_attempted") is False
                and denials["setns"].get("return") == -1 and denials["setns"].get("errno") == 1
                and denials["AF_UNIX"].get("errno") in (1, 13, 97), "PIDV2_CAPTURE_ROOT_NATIVE_DENIALS_INCOMPLETE")
        case_boundary = actor["pid_boundary"]
        require(case_boundary.get("host") == mapping["host_private_PID1"] and case_boundary.get("issuer") == mapping["issuer_actual"]
                and case_boundary.get("local") == birth and case_boundary.get("private_pid_namespace_before_actor_work") is True
                and case_boundary.get("foreign_signal_native_denial") == proof["controller"]["private_PID1"]["foreign_signal_native_denial"],
                "PIDV2_CAPTURE_ROOT_CASE_PID_BOUNDARY_REBOUND")
        if "hang" in case:
            signals = kernel["owned_group_signal_observations"]
            require(kernel["timed_out"] is True and kernel["returncode"] == -signal.SIGKILL
                    and all(any(row.get("signal") == number and row.get("outcome") == "SENT"
                        and row.get("pgid") == kernel["pid"] for row in signals)
                        for number in (signal.SIGTERM, signal.SIGKILL)), "PIDV2_CAPTURE_ROOT_TERM2_KILL_FIN_MISSING")
        elif case == "failure-before-drop":
            require(kernel["returncode"] == 73 and kernel["timed_out"] is False and "after_drop" not in actor,
                    "PIDV2_CAPTURE_PRE_DROP_FAILURE_NOT_NATIVE")
        if case == "nonroot-hang":
            after = _process(actor["after_drop"], full=True)
            drop = actor["drop"]
            native_filter.validate_evidence(drop["native_filter"], source_binding=source_binding)
            require(after["pid"] == birth["pid"] and after["start_ticks"] == birth["start_ticks"]
                    and after["uids"] == [request["owner_uid"]] * 4 and after["gids"] == [request["owner_gid"]] * 4
                    and not any(after["capabilities"].values()) and after["cgroup"] == birth["cgroup"]
                    and after["boot_id"] == birth["boot_id"]
                    and after["pid_namespace_inode"] == birth["pid_namespace_inode"]
                    and drop.get("uid") == request["owner_uid"] and drop.get("gid") == request["owner_gid"]
                    and drop.get("capabilities_zero") is True and drop.get("no_new_privileges") is True
                    and drop.get("seccomp_mode") == 2 and drop["native_filter"]["identity"]["pid"] == after["pid"]
                    and str(drop["native_filter"]["identity"]["start_ticks"]) == after["start_ticks"]
                    and drop.get("mount_namespace_inode") == seal["identity"]["mount_namespace_inode"]
                    and drop["native_filter"]["identity"]["mount_namespace_inode"] == seal["identity"]["mount_namespace_inode"]
                    and drop["native_filter"].get("filters_before") == child["filters_after"]
                    and drop["native_filter"]["module_sha256"] == code_hashes["scripts/rc6_native_namespace_filter.py"],
                    "PIDV2_CAPTURE_NONROOT_CASE_DROP_REBOUND")
    _raw_link(evidence, "private-pid-v2.native.log", raws, values)
    observation = values["private-pid-v2.native.log"]
    require(evidence.get("status") == "NATIVE_OBSERVATION_PROVED" and evidence.get("request") == probe
            and evidence.get("contract_sha256") == boundary.contract_sha256()
            and evidence.get("bridge") == bridge, "PIDV2_CAPTURE_V2_EVIDENCE_REBOUND")
    actor = observation["actor"]
    kernel = _kernel(evidence["kernel"], supervisor_pid=1, supervisor_uid=0, limit=20, green=True)
    require(kernel["pid"] == actor["pid"] and actor["parent_pid"] == 1
            and actor["pid_namespace_inode"] == local["pid_namespace_inode"]
            and actor["mount_namespace_inode"] == seal["identity"]["mount_namespace_inode"]
            and observation["mount_view"].get("raw_sha256") == mountinfo_sha256
            and observation["mount_view"].get("proc_device") == seal["boundary"]["proc_mount"]["device"],
            "PIDV2_CAPTURE_BOUNDARY_ACTOR_WAIT4_BIRTH_REBOUND")
    view_rows = observation["mount_view"]["rows"]
    require(type(view_rows) is list and len(view_rows) == len(captured_mount_rows)
            and {(row.get("id"), row.get("target"), row.get("type"), tuple(row.get("options", []))) for row in view_rows}
                == {(row["id"], row["target"], row["type"], tuple(row["options"])) for row in captured_mount_rows}
            and len([row for row in view_rows if row.get("target") == str(original_path)
                and row.get("id") == private_control_mount_id]) == 1,
            "PIDV2_CAPTURE_OBSERVER_MOUNT_ROWS_DIFFERS_FROM_ORIGINAL")
    _worker_command(kernel["command"], source=source, request_path=original_path / "probe-request.json")
    guardians = evidence["guardian_rows"]
    for stage, row in zip(("before", "after"), guardians):
        challenge_name = "boundary-" + stage + ".challenge.json"
        challenge = values[challenge_name]
        require(challenge.get("schema") == "porota.rc6.private-pid-challenge.v2" and challenge.get("stage") == stage
                and challenge.get("binding") == binding and challenge.get("request_sha256") == _hash(raws["probe-request.json"])
                and challenge.get("actor") == actor and challenge.get("nonce") == row.get("nonce")
                and row.get("challenge_sha256") == _hash(raws[challenge_name])
                and row == values["boundary-" + stage + ".guardian.json"]
                and row.get("guardian_actual") == guardian["actor"] and row.get("issuer_actual") == mapping["issuer_actual"]
                and row["host_actor_actual"].get("parent_pid") == mapping["host_private_PID1"]["pid"],
                "PIDV2_CAPTURE_ACTUAL_CHALLENGE_GUARDIAN_BYTES_REBOUND")
    native_pre = evidence["native_pre_controls"]
    require(type(native_pre) is list and len(native_pre) == 2
            and native_pre[1] == original_evidence.get("native_inherited_proc_control"),
            "PIDV2_CAPTURE_NATIVE_PRE_NAMESPACE_CONTROLS_REBOUND")
    for row in native_pre:
        require(_hex(row.get("mountinfo_sha256")) and row.get("proc_device") == probe["boundary_host_proc_device"],
                "PIDV2_CAPTURE_NATIVE_UNSAFE_VIEW_CONTROL_MISSING")
    wrong = native_pre[0]["kernel_identity"]
    inherited = native_pre[1]["kernel_identity"]
    require(all(wrong.get(key) == guardian["actor"][key] for key in custody.PUBLIC_PROCESS_FIELDS)
            and wrong.get("pid_namespace_inode") == issuer["pid_namespace_inode"]
            and inherited.get("pid") == 1 and inherited.get("namespace_pids") == mapping["host_private_PID1"]["namespace_pids"]
            and inherited.get("start_ticks") == local["start_ticks"]
            and inherited.get("pid_namespace_inode") == local["pid_namespace_inode"],
            "PIDV2_CAPTURE_NATIVE_UNSAFE_VIEW_BIRTH_REBOUND")
    arguments = {"request": probe, "guardian_rows": guardians, "native_pre_controls": native_pre, "bridge": bridge}
    boundary.validate_observation(observation, **arguments)
    require(evidence.get("negative_guards") == boundary.adversarial_rejections(observation, **arguments),
            "PIDV2_CAPTURE_NEGATIVE_GUARDS_CHANGED_OR_MISSING")
    cleanup = receipt["cleanup"]
    require(type(cleanup) is dict and cleanup.get("schema") == "porota.rc6.generated-fixture-scoped-cleanup.v1"
            and cleanup.get("binding") == binding and cleanup.get("namespace_nonce") == nonce
            and all(cleanup.get(key) is True for key in ("namespace_removed", "actual_owned_fin_closed", "phase_green",
                "directory_inode_removed", "original_namespace_removed", "original_path_is_nonexistent"))
            and cleanup.get("owned_fin_kind") == "NATIVE_WAIT4_OWNED_FIN"
            and cleanup.get("capture_manifest_sha256") == receipt["capture_manifest_sha256"]
            and cleanup.get("capture_path") == str(capture_path)
            and all(cleanup.get(key) is False for key in ("path_replaced_by_authenticated_numbering_control",
                "link_targets_followed", "global_cleanup_claimed", "runtime_paths_authorized"))
            and all(type(cleanup.get(key)) is int and cleanup[key] == 0 for key in (
                "file_modes_changed_for_retirement", "foreign_paths_removed", "real_orders_sent"))
            and type(cleanup.get("retained_entries_before")) is int and cleanup["retained_entries_before"] >= len(rows) + 1
            and _hex(cleanup.get("post_fin_directory_permission_transitions_sha256")),
            "PIDV2_CAPTURE_OWN_NONCE_CLEANUP_REBOUND")
    return {"schema": SCHEMA, "status": "CAPTURED_PRIVATE_PID_V2_CLOSURE_VERIFIED",
        "scope": "DOCUMENTARY_CLOSURE_NOT_LIVE_AUTHORITY", "binding": binding,
        "source_sha": request["source_sha"], "source_tree": request["source_tree"],
        "capture_manifest_sha256": receipt["capture_manifest_sha256"],
        "native_custody_receipt_sha256": _hash(custody.wire(receipt)),
        "boundary_observation_sha256": _hash(custody.wire(evidence)),
        "custody_request_sha256": _hash(raws["custody-request.json"]),
        "probe_request_sha256": _hash(raws["probe-request.json"]),
        "contract_sha256": boundary.contract_sha256(),
        "original_manager_sha256": custody.MANAGER_SHA256, "root_cases": list(CASES),
        "native_raw_sha256": _hash(raws["private-pid-v2.native.log"]),
        "negative_guards": evidence["negative_guards"], "in_process_witness_minted": False,
        "privileged_launch_authorized": False, "owner_contract_acceptance_granted": False,
        "G0_GREEN": False, "qualification_claimed": False, "real_orders_sent": 0}


def validate_capture(receipt, *, binding, code_hashes, read_original):
    """Verify captured linkage only; all IO is the caller's authenticated bytes.

    ``read_original('manifest.json')`` returns the inner original manifest.
    Other calls use its fixed ``relative_source`` names. This function does not
    open any path, inspect /proc, execute a producer or create opaque authority.
    """
    try:
        return _validate_capture(receipt, binding=binding, code_hashes=code_hashes, read_original=read_original)
    except (KeyError, TypeError, IndexError, AttributeError, OSError, OverflowError, UnicodeError) as error:
        raise ValueError("PIDV2_CAPTURE_REQUIRED_SCHEMA_MISSING_OR_INVALID:" + str(error)) from error
