"""Replay Source-authenticated G5A custody after FIN; never issue authority.

The small controls belong to the authenticated main Actions ZIP. The original
20 GiB binary stream is joined by its exact transfer/END/manifest hashes and
the separately authenticated all1202 frame family, never loaded here. The
four original adversarial ROOT cases retain their own G0 producer binding.
They cannot be relabelled as work performed by the Horizon writer.
"""
from __future__ import annotations

import base64
import errno
import hashlib
import math
from pathlib import Path, PurePosixPath
import re
import stat

from scripts import rc6_capacity_calibration as calibration
from scripts import rc6_controlled_native_child_manager as manager
from scripts import rc6_g5_contract as contract
from scripts import rc6_g5a_native_runner as runner
from scripts import rc6_g5a_raw_capture as raw_capture
from scripts import rc6_native_namespace_filter as native
from scripts import rc6_private_pid_boundary as boundary
from scripts import rc6_private_pid_capture_replay as pid_replay
from scripts import rc6_privileged_custody as custody
from scripts import rc6_root_actor_seal as root_seal

SCHEMA = "rc6.g5a-custody-captured.v2"
REPLAY_SCHEMA = "rc6.g5a-custody-captured-replay.v2"
SCOPE = "DOCUMENTARY_G5A_CUSTODY_REPLAY_ONLY"
MAX_CONTROL_BYTES = 16 * 1024**2
CONTROL_NAMES = frozenset({
    "root-request.json", "custody-request.json", "producer-owned-fin-g5a-original1202.json",
    "producer-native.log", "private-broker-native.log", "broker-birth.json",
    "broker-birth.observed.json", "broker-unit-original.json", "broker-bridge.json",
    "guardian-controller.json", "live-root-controller.json", "private-broker-birth.json",
    "private-broker.observed.json", "quota-ancestor-seal.json", "g5a-owner-oracle.json",
    "loop-birth.json", "g5a-prerequisites-manifest.json",
})
NATIVE_RESULT_KEYS = frozenset({"kernel", "request", "custody_request", "service_state", "actual_root_service_FIN"})


def require(value, error):
    if not value:
        raise ValueError(error)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _references(references, *, names, read_member):
    """Only authenticated bounded ZIP bytes, no live input path or JSON data."""
    require(type(references) is dict and set(references) == set(names),
            "G5A_CUSTODY_COMPLETE_ORIGINAL_CONTROL_REFERENCES_REQUIRED")
    values, paths = {}, set()
    for name in sorted(names):
        row = references[name]
        require(type(row) is dict and set(row) == {"path", "bytes", "sha256"}
                and type(row["path"]) is str and pid_replay._relative(row["path"])
                and row["path"] not in paths and type(row["bytes"]) is int
                and 0 < row["bytes"] <= MAX_CONTROL_BYTES and pid_replay._hex(row["sha256"]),
                "G5A_CUSTODY_CANONICAL_BOUNDED_RAW_REFERENCE_REQUIRED:" + name)
        paths.add(row["path"])
        raw = read_member(row["path"])
        require(type(raw) is bytes and len(raw) == row["bytes"] and sha(raw) == row["sha256"],
                "G5A_CUSTODY_ACTUAL_ORIGINAL_BYTES_CHANGED:" + name)
        values[name] = raw
    return values


def _kernel(value, *, supervisor, uid, maximum):
    limit = value.get("launcher_management_deadline_seconds") if type(value) is dict else None
    require(type(limit) in (float, int) and math.isfinite(limit) and 0 < limit <= maximum,
            "G5A_CUSTODY_ORIGINAL_MANAGER_DEADLINE_REBOUND")
    return pid_replay._kernel(value, supervisor_pid=supervisor, supervisor_uid=uid, limit=limit, green=True)


def _unit(controller, snapshot, *, request):
    """Read original transient-unit bytes, not a copied property checklist."""
    name = "rc6-native-" + request["namespace_receipt"]["namespace_nonce"] + ".service"
    require(controller.get("schema") == "porota.rc6.pid1-root-controller-live.v1"
            and controller.get("unit") == name and controller.get("PID1") == request["host_PID1"]
            and controller.get("host_boundary") == request["host_boundary"]
            and controller.get("ROOT_actor_boundary_certified") is False
            and controller.get("root_controller_claimed_by_metadata_only") is False,
            "G5A_CUSTODY_ACTUAL_EXTERNAL_CONTROLLER_REBOUND")
    actor = pid_replay._process(controller["actor"], full=True)
    require(actor["parent_pid"] == 1 and actor["uids"] == actor["gids"] == [0] * 4
            and actor["namespace_pids"] == [actor["pid"]]
            and actor["pid_namespace_inode"] == request["issuer"]["pid_namespace_inode"]
            and actor["boot_id"] == request["issuer"]["boot_id"]
            and actor["cgroup"] == "0::/system.slice/" + name
            and controller.get("mount_namespace_inode") != request["host_boundary"]["mount_namespace_inode"],
            "G5A_CUSTODY_GUARDIAN_KERNEL_BIRTH_REBOUND")
    forbidden = sum(1 << number for number in (16, 17, 19, 22, 25, 30, 38, 39, 40))
    require(actor["capabilities"]["CapEff"] & (1 << 5)
            and all(actor["capabilities"][key] & forbidden == 0 for key in ("CapPrm", "CapEff", "CapBnd")),
            "G5A_CUSTODY_GUARDIAN_FORBIDDEN_CAPABILITY")
    require(snapshot.get("schema") == "porota.rc6.root-owned-transient-unit-diagnostic.v1"
            and snapshot.get("unit") == name and snapshot.get("binding") == request["binding"]
            and snapshot.get("source_sha") == request["source_sha"]
            and snapshot.get("source_tree") == request["source_tree"],
            "G5A_CUSTODY_ORIGINAL_TRANSIENT_UNIT_SOURCE_REBOUND")
    original = snapshot["original_unit"]
    require(type(original) is dict and original.get("path") == "/run/systemd/transient/" + name,
            "G5A_CUSTODY_ORIGINAL_TRANSIENT_UNIT_MISSING")
    raw = base64.b64decode(original["raw_base64"], validate=True)
    require(sha(raw) == original.get("sha256"), "G5A_CUSTODY_ORIGINAL_TRANSIENT_UNIT_HASH_REBOUND")
    identity = original.get("identity")
    require(type(identity) is list and len(identity) == 5 and all(type(value) is int for value in identity)
            and identity[0] > 0 and identity[1] > 0 and identity[2:4] == [0, 0]
            and stat.S_ISREG(identity[4]), "G5A_CUSTODY_ROOT_UNIT_INODE_REBOUND")
    parsed = custody.parse_unit_properties(raw)
    require(controller.get("transient_unit") == {**original, **parsed},
            "G5A_CUSTODY_TRANSIENT_METADATA_DIFFERS_FROM_ORIGINAL_BYTES")
    properties = parsed["properties"]
    custody.require_guard_log_profile(name, properties)
    expected = custody.service_properties(request["namespace_receipt"]["path"], request["runtime_seconds"],
                                         resource_cpu=request["resource_cpu"], g5a=True)
    for item in expected:
        key, text = item.split("=", 1)
        if key in custody.UNIT_PATH_KEYS:
            require(parsed["path_lists"].get(key) == custody._unit_path_words(text),
                    "G5A_CUSTODY_TRANSIENT_PATH_SECURITY_CHANGED:" + key)
        elif key in ("RuntimeMaxSec", "TimeoutStopSec"):
            require(custody._seconds(properties.get(key)) == custody._seconds(text),
                    "G5A_CUSTODY_TRANSIENT_BOUND_CHANGED:" + key)
        else:
            require(properties.get(key) == text, "G5A_CUSTODY_TRANSIENT_SECURITY_CHANGED:" + key)
    require(properties.get("LoadCredential") == custody._g5a_credential_value(request["credential_metadata"]),
            "G5A_CUSTODY_SOURCE_FIXED_EPHEMERAL_CREDENTIAL_REBOUND")
    return actor


def _replay_private_capture(value, *, request, native_pair, read_member, source_hashes):
    require(type(value) is dict and set(value) == {"scope", "receipt", "files", "capacity_receipt"}
            and value.get("scope") == "G0_NATIVE_BOUNDARY_PREREQUISITE",
            "G5A_CUSTODY_COMPLETE_G0_PRIVATE_PID_CAPTURE_REQUIRED")
    receipt = value["receipt"]
    capability = value["capacity_receipt"]
    require(type(receipt) is dict and receipt.get("source_sha") == request["source_sha"]
            and receipt.get("source_tree") == request["source_tree"]
            and type(native_pair) is dict and type(capability) is dict
            and native_pair.get("capability_receipt_sha256") == sha(calibration.wire(capability))
            and pid_replay._hex(native_pair.get("bootstrap_receipt_sha256"))
            and capability.get("native_custody_receipt") == receipt
            and capability.get("source_sha") == request["source_sha"]
            and capability.get("source_tree") == request["source_tree"],
            "G5A_CUSTODY_SOURCE_LINKED_G0_PRIVATE_PID_CAPTURE_REQUIRED")
    original_binding = receipt.get("binding")
    require(type(original_binding) is dict
            and original_binding.get("candidate_sha") == request["source_sha"]
            and original_binding.get("candidate_tree") == request["source_tree"]
            and original_binding.get("producer") in {"capacity-probe", "capacity-calibration", "G0-native-custody"},
            "G5A_CUSTODY_G0_ORIGINAL_BINDING_CANNOT_ALIAS_HORIZON")
    hashes = receipt.get("code_hashes")
    require(type(hashes) is dict and pid_replay.HASH_MEMBERS <= set(hashes)
            and all(name in source_hashes and source_hashes[name] == digest for name, digest in hashes.items()),
            "G5A_CUSTODY_G0_REPLAY_EXACT_SOURCE_BYTES_REQUIRED")
    originals = _references(value["files"], names={"manifest.json", *pid_replay.REQUIRED}, read_member=read_member)
    return pid_replay.validate_capture(receipt, binding=original_binding, code_hashes=hashes,
                                       read_original=lambda name: originals[name])


def _verify(record, *, row, quota_control, read_member, source_hashes):
    require(type(record) is dict and set(record) == {"schema", "source_sha", "source_tree", "binding",
            "native_result", "original_raw_capture", "controls", "private_pid_capture"}
            and record.get("schema") == SCHEMA and record.get("source_sha") == row.get("source_sha")
            and record.get("source_tree") == row.get("source_tree")
            and record.get("binding") == quota_control.get("binding"),
            "G5A_CUSTODY_EXACT_CAPTURED_SOURCE_AND_BINDING_REQUIRED")
    raws = _references(record["controls"], names=CONTROL_NAMES, read_member=read_member)
    values = {name: calibration.decode(raw) for name, raw in raws.items()}
    result = record["native_result"]
    require(type(result) is dict and set(result) == NATIVE_RESULT_KEYS,
            "G5A_CUSTODY_ORIGINAL_NATIVE_RESULT_CONTROLS_REQUIRED")
    request, service_request = values["root-request.json"], values["custody-request.json"]
    runner.validate_request_shape(request)
    require(result["request"] == request and result["custody_request"] == service_request
            and request["binding"] == record["binding"] and request["source_sha"] == row["source_sha"]
            and request["source_tree"] == row["source_tree"]
            and request["code_hashes"] == {name: source_hashes[name] for name in runner.CODE_MEMBERS}
            and raws["root-request.json"] == calibration.wire(request)
            and raws["custody-request.json"] == custody.wire(service_request),
            "G5A_CUSTODY_SOURCE_REQUEST_OR_NATIVE_RESULT_REBOUND")
    require(all(service_request.get(key) == request[key] for key in ("binding", "source_sha", "source_tree",
            "source_root", "code_hashes", "owner_uid", "owner_gid", "namespace_receipt", "issuer",
            "host_PID1", "host_boundary", "boundary_revision", "require_quota_ancestor_seal"))
            and service_request.get("operation_scope") == runner.OPERATION_SCOPE
            and service_request.get("calibration_request_sha256") == sha(raws["root-request.json"])
            and service_request.get("calibration_request") == str(PurePosixPath(request["namespace_receipt"]["path"]) / "root-request.json")
            and service_request.get("runtime_seconds") == runner.HORIZON_SECONDS - 10,
            "G5A_CUSTODY_SERVICE_ROOT_REQUEST_BYTES_REBOUND")
    prerequisites = values["g5a-prerequisites-manifest.json"]
    require(sha(raws["g5a-prerequisites-manifest.json"]) == request["g5a_prerequisites_manifest_sha256"],
            "G5A_CUSTODY_ACTUAL_ROOT_PREREQUISITES_BYTES_REBOUND")
    relay, private_relay = values["producer-native.log"], values["private-broker-native.log"]
    require(relay.get("schema") == "porota.rc6.g5a-custody-worker.v1"
            and relay.get("operation_scope") == runner.OPERATION_SCOPE and relay.get("binding") == request["binding"]
            and relay.get("source_sha") == row["source_sha"] and relay.get("source_tree") == row["source_tree"]
            and relay.get("request_sha256") == sha(raws["root-request.json"])
            and relay.get("G5_GREEN") is False and relay.get("qualification_claimed") is False,
            "G5A_CUSTODY_ACTUAL_ROOT_RELAY_SOURCE_REBOUND")
    external, root = relay["external_guardian"], relay["privileged_controller"]
    controller, mapping = external["controller"], values["private-broker.observed.json"]
    require(private_relay == {key: value for key, value in relay.items() if key != "external_guardian"}
            and external.get("private_broker_raw_sha256") == sha(raws["private-broker-native.log"])
            and external.get("controller") == values["guardian-controller.json"]
            and external.get("private_PID1_actual_mapping") == mapping
            and external.get("original_manager_sha256") == root.get("manager_sha256") == custody.MANAGER_SHA256,
            "G5A_CUSTODY_ORIGINAL_PRIVATE_BROKER_AND_GUARDIAN_RELAY_REBOUND")
    guardian = _unit(controller, values["broker-unit-original.json"], request=service_request)
    pid_replay._service_ack(values["broker-birth.json"], values["broker-birth.observed.json"],
        result["actual_root_service_FIN"], request=service_request,
        request_raw=raws["custody-request.json"], controller=controller)
    state = result["service_state"]
    require(type(state) is dict and state.get("pidfd") is None and state.get("birth") == values["broker-birth.json"]
            and state.get("external_PID1") == request["host_PID1"]
            and state.get("external_PID1_observed_native") is True,
            "G5A_CUSTODY_ORIGINAL_EXTERNAL_PIDFD_STATE_REBOUND")
    outer = pid_replay._fin(values["producer-owned-fin-g5a-original1202.json"], binding=request["binding"],
        nonce=request["namespace_receipt"]["namespace_nonce"], issuer=request["issuer"],
        limit=result["kernel"].get("launcher_management_deadline_seconds"), green=True)
    require(outer == result["kernel"] and 0 < outer["launcher_management_deadline_seconds"] <= runner.HORIZON_SECONDS,
            "G5A_CUSTODY_ORIGINAL_OUTER_WAIT4_FIN_REBOUND")
    worker = _kernel(root["worker_kernel"], supervisor=1, uid=0, maximum=runner.HORIZON_SECONDS)
    guardian_kernel = _kernel(external["private_broker_kernel"], supervisor=guardian["pid"], uid=0,
                              maximum=runner.HORIZON_SECONDS)
    private, host, issuer = root["controller"]["private_PID1"], mapping["host_private_PID1"], mapping["issuer_actual"]
    local = private["local"]
    source = PurePosixPath(request["source_root"])
    own = PurePosixPath(request["namespace_receipt"]["path"])
    worker_command = ["unshare", "--mount", "--propagation", "private", "--", "/bin/sh", "-c",
        custody.BRIDGE_SHELL, "rc6-opaque-own-fd", request["python311"], "-I", "-B", str(source / custody.MEMBER),
        "--g5a-root-worker", "--request", str(own / "root-request.json"),
        "--privileged-custody-proof", str(own / "live-root-controller.json")]
    private_prefix = ["unshare", "--mount", "--propagation", "private", "--pid", "--fork",
        "--kill-child=SIGKILL", "--", "/bin/sh", "-c", custody.BRIDGE_SHELL, "rc6-opaque-own-fd"]
    private_suffix = ["-I", "-B", str(source / custody.MEMBER), "--private-init", "g5a", "--request",
        str(own / "custody-request.json"), "--unit", controller["unit"]]
    command = guardian_kernel["command"]
    require(worker["command"] == worker_command
        and len(command) == len(private_prefix) + 1 + len(private_suffix)
        and command[:len(private_prefix)] == private_prefix
        and pid_replay._absolute(command[len(private_prefix)])
        and command[len(private_prefix) + 1:] == private_suffix,
        "G5A_CUSTODY_SOURCE_FIXED_ROOT_WORKER_COMMAND_REBOUND")
    outer_command = outer["command"]
    outer_prefix = ["sudo", "-n", "--", "systemd-run", "--quiet", "--wait", "--pipe", "--collect",
        "--service-type=exec", "--unit=" + controller["unit"], "--working-directory=" + str(source)]
    outer_suffix = ["--", "/bin/sh", "-c", custody.BRIDGE_SHELL, "rc6-opaque-own-fd", outer_command[-10],
        "-I", "-B", str(source / custody.MEMBER), "--guardian", "g5a", "--request",
        str(own / "custody-request.json"), "--unit", controller["unit"]]
    require(outer_command[:len(outer_prefix)] == outer_prefix
        and pid_replay._absolute(outer_suffix[5]) and outer_command[-len(outer_suffix):] == outer_suffix,
        "G5A_CUSTODY_SOURCE_FIXED_OUTER_COMMAND_REBOUND")
    for value in (host, local, issuer):
        pid_replay._process(value, full=True)
    require(mapping.get("source_sha") == request["source_sha"] and mapping.get("source_tree") == request["source_tree"]
            and mapping.get("binding") == request["binding"]
            and mapping.get("request_sha256") == sha(raws["custody-request.json"])
            and mapping.get("guardian_actual") == guardian
            and host.get("parent_pid") == guardian_kernel["pid"]
            and host.get("namespace_pids") == [host["pid"], 1] and host.get("uids") == host.get("gids") == [0] * 4
            and local.get("pid") == 1 and local.get("namespace_pids") == [1]
            and local.get("uids") == local.get("gids") == [0] * 4
            and local.get("start_ticks") == host.get("start_ticks")
            and local.get("pid_namespace_inode") == host.get("pid_namespace_inode") != guardian["pid_namespace_inode"]
            and local.get("cgroup") == host.get("cgroup") == guardian["cgroup"]
            and all(issuer.get(key) == request["issuer"][key] for key in ("pid", "start_ticks", "boot_id"))
            and issuer.get("uids") == [request["owner_uid"]] * 4
            and issuer.get("gids") == [request["owner_gid"]] * 4,
            "G5A_CUSTODY_ACTUAL_PRIVATE_PID_KERNEL_MAPPING_REBOUND")
    born, live = values["private-broker-birth.json"], values["live-root-controller.json"]
    bridge = {"schema": "porota.rc6.original-own-directory-fd-native.v1", "descriptor": 3,
        "identity": request["namespace_receipt"]["identity"], "original_mount_id": request["namespace_receipt"]["mount_id"],
        "marker_sha256": request["namespace_receipt"]["marker_sha256"],
        "namespace_nonce": request["namespace_receipt"]["namespace_nonce"], "foreign_host_proc_exposed": False}
    require(born.get("host_private_PID1") == host
        and born.get("request_sha256") == mapping["request_sha256"]
        and born.get("binding") == request["binding"]
        and mapping.get("original_directory_bridge") == born.get("original_directory_bridge")
        == values["broker-bridge.json"] == bridge
        and mapping.get("external_PID1_ACK") == values["broker-birth.observed.json"]
        and mapping.get("private_mount_setup") == born.get("private_mount_setup")
        and live.get("controller") == controller and live.get("guardian_native_mapping") == mapping
        and live.get("pid_boundary") == private
        and live.get("calibration_request_sha256") == sha(raws["root-request.json"])
        and live.get("request_sha256") == sha(raws["custody-request.json"])
        and live.get("source_sha") == request["source_sha"] and live.get("source_tree") == request["source_tree"]
        and live.get("binding") == request["binding"] and live.get("code_hashes") == request["code_hashes"],
        "G5A_CUSTODY_ORIGINAL_BIRTH_BRIDGE_AND_LIVE_SOURCE_CONTROLLER_REBOUND")
    ancestor = values["quota-ancestor-seal.json"]
    root_seal.validate_evidence(ancestor, owner_uid=request["owner_uid"], owner_gid=request["owner_gid"],
        source_binding={"source_sha": request["source_sha"], "source_tree": request["source_tree"]})
    require(root.get("quota_ancestor_seal") == root["controller"].get("root_actor_seal") == ancestor
            and ancestor.get("scope") == "ROOT_QUOTA_CUSTODY_ONLY" and ancestor["identity"]["pid"] == 1
            and ancestor["identity"]["pid_namespace_inode"] == local["pid_namespace_inode"]
            and str(ancestor["identity"]["start_ticks"]) == local["start_ticks"]
            and root.get("oracle_closed_before_seal") is True
            and root.get("oracle_close", {}).get("status") == "ACTUAL_KERNEL_FIFO_DESCRIPTORS_CLOSED",
            "G5A_CUSTODY_SOURCE_FIXED_ORACLE_CLOSE_AND_ROOT_SEAL_REQUIRED")
    child = root["oracle_close"]["worker_birth"]
    require(child.get("pid") == worker["pid"] and child.get("parent_pid") == 1
        and child.get("uids") == child.get("gids") == [0] * 4
        and child.get("pid_namespace_inode") == local["pid_namespace_inode"]
        and child.get("cgroup") == local["cgroup"]
        and pid_replay._hex(root["oracle_close"].get("paired_close_receipt_sha256")),
        "G5A_CUSTODY_ROOT_API_AFTER_WORKER_BIRTH_AND_BEFORE_SEAL_REQUIRED")
    captured = record["original_raw_capture"]
    require(type(captured) is dict and captured.get("schema") == "rc6.g5a-post-fin-lossless-capture.v2"
            and captured.get("source_sha") == request["source_sha"] and captured.get("source_tree") == request["source_tree"]
            and captured.get("binding") == request["binding"] and captured.get("actual_original_cuts") == 1202
            and captured.get("stream_sha256") == relay["root_worker"]["sha256"]
            and captured.get("stream_bytes") == relay["root_worker"]["bytes"]
            and type(captured.get("stream_bytes")) is int and 0 < captured["stream_bytes"] <= runner.MAX_STREAM_BYTES
            and captured.get("G5_GREEN") is False and captured.get("qualification_claimed") is False,
            "G5A_CUSTODY_ORIGINAL_BOUNDED_SOURCE_STREAM_JOIN_REQUIRED")
    retirement = captured.get("original_retirement", {})
    scan = retirement.get("complete_original_stream_scan", {})
    require(retirement.get("schema") == "rc6.g5a-original-own-backing-retirement.v2"
        and retirement.get("original_request_sha256") == sha(raws["root-request.json"])
        and retirement.get("relay_sha256") == sha(raws["producer-native.log"])
        and scan.get("schema") == "rc6.g5a-post-fin-whole-original-stream-scan.v2"
        and scan.get("stream_bytes") == captured["stream_bytes"]
        and scan.get("stream_sha256") == captured["stream_sha256"]
        and scan.get("worker_report_sha256") == sha(calibration.wire(captured["worker_report"]))
        and all(value.get("source_sha") == request["source_sha"]
                and value.get("source_tree") == request["source_tree"]
                and value.get("binding") == request["binding"] for value in (retirement, scan)),
        "G5A_CUSTODY_SOURCE_WHOLE_STREAM_END_AND_WORKER_REPORT_JOIN_REQUIRED")
    files = captured.get("files")
    require(type(files) is list and all(type(item) is dict and type(item.get("relative_source")) is str for item in files)
        and len({item["relative_source"] for item in files}) == len(files),
        "G5A_CUSTODY_SOURCE_STREAM_FILE_INVENTORY_REQUIRED")
    raw_capture.validate_original_control(captured["worker_report"],
        {item["relative_source"]: item for item in files}, request=request)
    oracle = values["g5a-owner-oracle.json"]
    require(oracle.get("schema") == "rc6.g5a-root-owner-oracle.v2"
        and oracle.get("source_sha") == request["source_sha"] and oracle.get("source_tree") == request["source_tree"]
        and oracle.get("binding") == request["binding"] and oracle.get("broker_birth") == local
        and oracle.get("contract_sha256") == request["g5a_contract_sha256"]
        and oracle.get("directory") == str(own / "g5a-owner-oracle") and pid_replay._hex(oracle.get("nonce")),
        "G5A_CUSTODY_ACTUAL_SOURCE_FIXED_OWNER_ORACLE_REBOUND")
    transfer = external.get("g5a_raw_transfer", {})
    before, after = transfer.get("identity_before"), transfer.get("identity_after")
    require(type(before) is dict and type(after) is dict
        and set(before) == set(after) == set(raw_capture.IDENTITY_FIELDS)
        and all(type(number) is int and number >= 0 for identity in (before, after) for number in identity.values())
        and all(before[key] == after[key] for key in raw_capture.STABLE_TRANSFER)
        and (before["st_uid"], before["st_gid"]) == (0, 0)
        and (after["st_uid"], after["st_gid"]) == (request["owner_uid"], request["owner_gid"])
        and stat.S_ISREG(after["st_mode"]) and stat.S_IMODE(after["st_mode"]) == 0o600
        and after["st_nlink"] == 1 and after["st_size"] == captured["stream_bytes"]
        and transfer.get("path") == "root-worker-native.log"
        and transfer.get("bytes") == captured["stream_bytes"] and transfer.get("sha256") == captured["stream_sha256"]
        and all(transfer.get(key) is True for key in ("actual_root_worker_FIN_verified",
            "actual_private_PID1_FIN_verified", "actual_original_private_broker_FIN_verified")),
        "G5A_CUSTODY_EXACT_ROOT_TO_OWN_NONROOT_STREAM_TRANSFER_REQUIRED")
    pid_replay._scope_state(transfer.get("guardian_kernel_echild"))
    controls = transfer.get("fixed_controls")
    require(type(controls) is list and len(controls) == 3
        and {item.get("path") for item in controls} == {"live-root-controller.json", "quota-ancestor-seal.json", "g5a-owner-oracle.json"},
        "G5A_CUSTODY_ACTUAL_ROOT_FIXED_CONTROL_TRANSFER_REQUIRED")
    for item in controls:
        before, after = item.get("identity_before"), item.get("identity_after")
        require(type(before) is dict and type(after) is dict
            and set(before) == set(after) == set(raw_capture.IDENTITY_FIELDS)
            and all(type(number) is int and number >= 0 for identity in (before, after) for number in identity.values())
            and all(before[key] == after[key] for key in raw_capture.STABLE_TRANSFER)
            and (before["st_uid"], before["st_gid"]) == (0, 0)
            and (after["st_uid"], after["st_gid"]) == (request["owner_uid"], request["owner_gid"])
            and stat.S_ISREG(after["st_mode"]) and stat.S_IMODE(after["st_mode"]) == 0o600
            and after["st_nlink"] == 1 and after["st_size"] == len(raws[item["path"]])
            and item.get("bytes") == len(raws[item["path"]]) and item.get("sha256") == sha(raws[item["path"]]),
            "G5A_CUSTODY_ROOT_FIXED_CONTROL_ALL11_TRANSFER_REBOUND:" + item["path"])
    observation = captured["worker_report"]["report"]["private_pid_boundary"]
    actor = observation["actor"]
    boundary.validate_private_view(actor, observation["mount_view"], {
        "pid_namespace_inode": request["issuer"]["pid_namespace_inode"],
        "mount_namespace_inode": request["mount_namespace_inode"], "proc_device": request["boundary_host_proc_device"]})
    boundary.validate_descriptors(observation["descriptors"])
    native.validate_evidence(quota_control["sealed_filter"], source_binding=request["filter_binding"])
    identity = quota_control["sealed_filter"]["identity"]
    loop = values["loop-birth.json"]
    require(loop.get("actor_pid") == worker["pid"]
        and loop.get("actor_start_ticks") == str(identity["start_ticks"])
        and loop.get("actor_mount_namespace_inode") == identity["mount_namespace_inode"]
        and loop.get("boot_id") == identity["boot_id"] == request["issuer"]["boot_id"],
        "G5A_CUSTODY_ACTUAL_ROOT_QUOTA_SETUP_AND_SAME_NONROOT_PID_REBOUND")
    require(observation.get("schema") == "porota.rc6.quota-private-pid-observation.v2"
            and observation.get("revision") == boundary.REVISION
            and observation.get("contract_sha256") == boundary.contract_sha256()
            and observation.get("launch_authority_granted") is False and observation.get("qualification_claimed") is False
            and actor.get("pid") == worker["pid"] == identity["pid"] and actor.get("parent_pid") == 1
            and actor.get("pid_namespace_inode") == host["pid_namespace_inode"]
            and actor.get("cgroup") == guardian["cgroup"] and actor.get("start_ticks") == str(identity["start_ticks"])
            and actor.get("uids") == [request["owner_uid"]] * 4 and actor.get("gids") == [request["owner_gid"]] * 4
            and actor.get("supplementary_groups") == [] and actor.get("no_new_privileges") == 1
            and actor.get("seccomp_mode") == 2 and not any(actor["capabilities"].values())
            and observation.get("external_issuer") == issuer and observation.get("ENOENT_alone_accepted") is False
            and observation.get("host_signal_errno") == errno.ESRCH
            and type(observation.get("host_signal_errno")) is int
            and observation.get("existing_ROOT_descriptor_denial", {}).get("errno") in (errno.EACCES, errno.EPERM)
            and type(observation["existing_ROOT_descriptor_denial"].get("errno")) is int
            and observation["existing_ROOT_descriptor_denial"].get("write_performed") is False
            and set(observation.get("hidden_magiclinks", {})) == set(boundary.MAGICLINKS)
            and all(type(item.get("errno")) is int and item.get("errno") == errno.ENOENT and item.get("write_performed") is False
                    for item in observation["hidden_magiclinks"].values()),
            "G5A_CUSTODY_COMPLETE_CURRENT_NONROOT_PID_VIEW_REQUIRED_NOT_ENOENT_ALONE")
    private_capture = _replay_private_capture(record["private_pid_capture"], request=request,
        native_pair=captured["worker_report"]["report"].get("g5a_native_capacity_pair"),
        read_member=read_member, source_hashes=source_hashes)
    return {"schema": REPLAY_SCHEMA, "scope": SCOPE, "source_sha": request["source_sha"],
        "source_tree": request["source_tree"], "binding": request["binding"],
        "actual_root_and_outer_FIN_replayed": True, "full_private_PID_adversarial_capture_replayed": True,
        "original_G0_private_PID_capture": private_capture, "outer_kernel_sha256": sha(calibration.wire(outer)),
        "current_worker_kernel_sha256": sha(calibration.wire(worker)),
        "current_guardian_kernel_sha256": sha(calibration.wire(guardian_kernel)),
        "captured_stream_sha256": captured["stream_sha256"],
        "new_launch_authorization_granted": False, "owner_acceptance_granted": False,
        "G5_GREEN": False, "qualification_claimed": False, "global_cleanup_claimed": False, "real_orders_sent": 0}


def verify_capture(record, *, row, quota_control, archive_context, source_root):
    """Entry point for gates.verify_g5a_captured_custody; fail closed by default."""
    from scripts import rc6_architectural_gates as gates
    from scripts import rc6_g5b_evidence as evidence
    state = evidence.registered_archive(archive_context)
    require(state["row"] == row, "G5A_CUSTODY_CURRENT_AUTHENTICATED_ACTIONS_ROW_REQUIRED")
    source_root = Path(source_root).absolute()
    try:
        hashes = {name: sha((source_root / name).read_bytes()) for name in runner.CODE_MEMBERS}
        return _verify(record, row=row, quota_control=quota_control, source_hashes=hashes,
            read_member=lambda member: gates.capture_member(archive_context.archive, member))
    except (KeyError, TypeError, IndexError, AttributeError, OSError, OverflowError, UnicodeError) as error:
        raise ValueError("G5A_CUSTODY_ORIGINAL_RAW_SCHEMA_MISSING_OR_INVALID:" + str(error)) from error
