"""Economic Source/IO guards; no ROOT, unit, quota or G5 acceptance is simulated."""
from __future__ import annotations

import ast
import copy
import hashlib
import inspect
import os
from pathlib import Path
import time

import pytest

from scripts import rc6_g5_contract as g5
from scripts import rc6_privileged_custody as custody


def metadata():
    return {"pid": 41, "start_ticks": 200, "boot_id": "12345678-1234-1234-1234-123456789abc",
        "uid": 1001, "gid": 1001, "fd": 12, "name": "rc6-readonly-api", "bytes": 33}


def test_original_device_runtime_properties_remain_exact_and_do_not_gain_g5a(tmp_path):
    original = custody.service_properties(tmp_path, 10800)
    assert "RuntimeMaxSec=10800" in original
    assert "TimeoutStopSec=2" in original
    assert not any(value.startswith(("CPUQuota=", "MemoryMax=", "LoadCredential=")) for value in original)
    with pytest.raises(ValueError, match="NATIVE_RUNTIME_BOUND_REQUIRED"):
        custody.service_properties(tmp_path, 10801)


def test_g5a_has_distinct_21600_bound_fixed_1cpu_1gib_swap0_and_original_devices(tmp_path):
    properties = custody.service_properties(tmp_path, 21600, g5a=True, resource_cpu=3)
    for value in ("RuntimeMaxSec=21600", "TimeoutStopSec=2", "CPUQuota=100%", "AllowedCPUs=3",
                  "CPUAffinity=3", "MemoryMax=1073741824", "MemorySwapMax=0", "TasksMax=64"):
        assert value in properties
    assert not any(value.startswith(("PrivateDevices=", "ProtectHome=", "LoadCredential=")) for value in properties)
    assert custody.service_properties(tmp_path, 21600, g5a=True, resource_cpu=3) == properties


@pytest.mark.parametrize("kwargs", [
    {"g5a": True}, {"g5a": True, "resource_cpu": True}, {"g5a": True, "resource_cpu": -1},
    {"g5a": True, "resource_cpu": 8192}, {"g5a": True, "resource_cpu": 1, "probe_only": True},
    {"g5a": 1, "resource_cpu": 1}, {"g5a": "yes", "resource_cpu": 1},
])
def test_g5a_limit_roles_are_not_unreviewed_native_toggles(tmp_path, kwargs):
    with pytest.raises(ValueError):
        custody.service_properties(tmp_path, 21600, **kwargs)


@pytest.mark.parametrize("runtime", [0, -1, 21601, True, "21600"])
def test_g5a_never_adds_wall_budget(tmp_path, runtime):
    with pytest.raises(ValueError, match="NATIVE_RUNTIME_BOUND_REQUIRED"):
        custody.service_properties(tmp_path, runtime, g5a=True, resource_cpu=0)


def test_public_credential_metadata_has_no_token_or_digest_and_fixed_property():
    value = metadata()
    assert custody._g5a_credential_value(value) == "rc6-readonly-api:/proc/41/fd/12"
    assert set(value) == {"pid", "start_ticks", "boot_id", "uid", "gid", "fd", "name", "bytes"}


@pytest.mark.parametrize("field,value", [
    ("token", "PUBLIC_UNIT_CANARY"), ("sha256", "a" * 64), ("path", "/proc/42/fd/3"),
    ("fd", 2), ("fd", True), ("fd", "12"), ("pid", 0), ("pid", True),
    ("uid", 0), ("gid", 0), ("name", "other"), ("name", "../rc6-readonly-api"),
    ("bytes", 65537), ("bytes", 0), ("bytes", True), ("start_ticks", "-2"),
    ("boot_id", "unbound"),
])
def test_credential_public_metadata_cannot_smuggle_authority_or_alias(field, value):
    current = metadata(); current[field] = value
    with pytest.raises(ValueError, match="OPAQUE_CREDENTIAL_METADATA_REQUIRED"):
        custody._g5a_credential_value(current)


def test_load_credential_is_single_service_property_and_duplicate_alias_rejected():
    raw = b"[Service]\nLoadCredential=rc6-readonly-api:/proc/41/fd/12\n"
    assert custody.parse_unit_properties(raw)["properties"]["LoadCredential"] == custody._g5a_credential_value(metadata())
    with pytest.raises(ValueError, match="UNIT_SINGULAR_CONFLICT"):
        custody.parse_unit_properties(raw + b"LoadCredential=rc6-readonly-api:/proc/42/fd/4\n")
    with pytest.raises(ValueError, match="UNIT_SERVICE_SECTION_REQUIRED"):
        custody.parse_unit_properties(b"[Unit]\nLoadCredential=rc6-readonly-api:/proc/41/fd/12\n")


def test_original_service_rejects_credential_argument_before_launch(tmp_path):
    with pytest.raises(ValueError, match="CREDENTIAL_ONLY_IN_G5A_ROLE"):
        custody.service_command(custody.ROOT, tmp_path / "custody-request.json", nonce="a" * 32,
            control_root=tmp_path, runtime_seconds=20, broker_mode="quota", credential_context=object())


def test_native_manager_remains_byte_exact_and_sequential_guardian_used():
    assert hashlib.sha256((custody.ROOT / custody.MANAGER_MEMBER).read_bytes()).hexdigest() == custody.MANAGER_SHA256
    source = inspect.getsource(custody._g5a_broker)
    assert 'manager["managed_native_child"]' in source
    assert 'manager["managed_custody_closed"]' in source
    assert "terminate_grace=2, progress_poll=5" in source
    assert "systemd-run" not in source and "subprocess.Popen" not in source
    assert 'request["runtime_seconds"] - 20' in source
    assert 'credential.BROKER_ORACLE_ROLE' in source
    assert 'worker_birth=child' in source


def test_source_orders_fork_exec_broker_credential_ack_close_wipe_then_parent_seal():
    tree = ast.parse(inspect.getsource(custody._g5a_broker))
    callback = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "prepare_and_seal")
    calls = {ast.unparse(node.func): node.lineno for node in ast.walk(callback) if isinstance(node, ast.Call)}
    assert calls["oracle.bind_worker"] < calls["credential.read_once"] < calls["oracle.serve_one"]
    assert calls["oracle.serve_one"] < calls["oracle.finish_close"] < calls["client.close"]
    assert calls["client.close"] < calls["credential.wipe"] < calls["oracle.close"]
    assert calls["oracle.close"] < calls["_prepare_quota_ancestor_mounts"] < calls["root_seal.install_quota_custody_seal"]
    assert calls["os.fchmod"] < calls["root_seal.install_quota_custody_seal"]
    # The Source callback is passed to the original manager, never called by
    # a fabricated PID or a dictionary claiming a native child's birth.
    source = inspect.getsource(custody._g5a_broker)
    assert "progress=prepare_and_seal" in source
    assert source.count("prepare_and_seal(") == 1


def test_root_worker_exec_is_fixed_same_interpreter_and_exported_source():
    source = inspect.getsource(custody._g5a_broker)
    assert 'request["python311"], "-I", "-B", str(ROOT / MEMBER)' in source
    assert '"--g5a-root-worker", "--request", request["calibration_request"]' in source
    assert "runner.validate_source_inputs(root_request)" in source
    assert 'runner.validate_request_shape(root_request)' in source
    shape = inspect.getsource(custody._g5a_service_shape)
    assert 'request.get("python311") == sys.executable' in shape
    assert "set(request) == G5A_SERVICE_FIELDS" in shape


def test_g5a_source_pin_uses_separate_raw_git_repository_and_complete_index():
    source = inspect.getsource(custody._request)
    assert 'repository = Path(value["source_repo"]) if g5a_role else ROOT' in source
    assert 'producer.verify_source(repository, source, value["source_sha"], value["source_tree"], calibration.decode(index))' in source
    assert 'calibration.source_pin(ROOT, value["source_sha"], value["source_tree"])' in source
    assert "members = g5a_runner.CODE_MEMBERS" in source
    assert "os.environ.pop(key, None)" in source


def test_binary_raw_bound_and_hash_reference_do_not_claim_complete_or_green():
    assert custody.G5A_RAW_BOUND == 20 * 1024**3
    assert custody.G5A_RAW_CHUNK == 512 * 1024
    source = inspect.getsource(custody._g5a_closed_raw_reference)
    assert "_read(" not in source and "json.loads" not in source and "decode(" not in source
    assert "base64" not in source and "b''.join" not in source and "b\"\".join" not in source
    assert "os.read(descriptor, G5A_RAW_CHUNK)" in source
    assert '"stream_complete_verified": False' in source and '"outer_source_parser_required": True' in source
    assert '"G5_GREEN": False' in source and '"qualification_claimed": False' in source
    assert source.index('manager["managed_custody_closed"]') < source.index("os.open(")
    assert "read_identity(before) == read_identity(os.fstat(descriptor))" in source
    relay = inspect.getsource(custody._g5a_broker)
    assert '"required_raw_complete": False' in relay


def test_nonroot_cannot_fabricate_root_controller_or_credential_proof():
    assert os.getuid() > 0
    with pytest.raises(ValueError, match="SINGLE_ROOT_TRUSTED_API_SETUP_REQUIRED"):
        g5.observe_trusted_api_controller({}, {}, role="broker")


@pytest.mark.parametrize("path", [
    "https://api.github.com/repos/foreign/project", "//foreign", "/issues/474", "/../issues/471",
    "/issues/471?per_page=101", "/actions/artifacts/1/zip", "/actions/runs/1?head_sha=" + "a" * 40,
])
def test_readonly_root_api_default_get_does_not_expand_scope(path):
    with pytest.raises(ValueError):
        g5._oracle_api_path(path)


def test_readonly_client_close_really_wipes_mutable_buffer_and_cannot_reopen():
    token = bytearray(b"PUBLIC_UNIT_CANARY")
    client = custody._G5AReadonlyGithub(token, deadline=time.monotonic() + 20)
    client.close()
    assert token == bytearray(len(token)) and client.token is None and client.opener is None
    with pytest.raises(ValueError, match="CLIENT_CLOSED"):
        client.request("/issues/471")


def test_root_client_has_no_mutation_method_env_token_or_upload_path():
    parameters = inspect.signature(custody._G5AReadonlyGithub.request).parameters
    assert set(parameters) == {"self", "path"}
    source = inspect.getsource(custody._G5AReadonlyGithub)
    assert 'method="GET"' in source
    assert 'RemoveAuthorizationRedirect' in source
    assert "os.environ" not in source and "uploads.github.com" not in source
    assert 'request.remove_header("Authorization")' in source
    with pytest.raises(ValueError, match="ARTIFACT_FIXED_BOUND_REQUIRED"):
        custody._G5AReadonlyGithub(bytearray(b"PUBLIC_UNIT_CANARY"), deadline=time.monotonic() + 5).open_artifact(
            True, deadline=time.monotonic())


def test_new_native_resource_readback_does_not_use_unit_flags_as_kernel_proof():
    source = inspect.getsource(custody._g5a_kernel_resource_limits)
    for name in ("cpu.max", "memory.max", "memory.swap.max", "pids.max", "cpuset.cpus.effective"):
        assert name in source
    assert 'int(cpu[0]) == int(cpu[1]) > 0' in source
    assert '"qualification_claimed": False' in source


def test_source_roles_never_auto_green_the_new_contract():
    assert custody.G5A_SCOPE == "G5A_EVIDENCE_ACQUISITION_ONLY"
    assert "token" not in custody.G5A_SERVICE_FIELDS and "credential_digest" not in custody.G5A_SERVICE_FIELDS
    for operation in (custody.guardian_main, custody.private_init, custody.broker_main):
        assert '(mode == "g5a") == (request.get("operation_scope") == G5A_SCOPE)' in inspect.getsource(operation)
    source = inspect.getsource(custody.controller_snapshot)
    assert '"ROOT_CUSTODY_ORIGINAL_ROLE_CREDENTIAL_FORBIDDEN"' in source


def _owned_io(tmp_path):
    """Actual NONROOT fixture bytes, never a ROOT or FIN acceptance receipt."""
    from scripts import rc6_capacity_calibration as calibration
    tmp_path.chmod(0o700)
    path = tmp_path / "root-worker-native.log"
    raw = b"RC6G5ARAW2\0" + b"OWN_UNIT_BYTES_" * 100001
    path.write_bytes(raw); path.chmod(0o600)
    args = {"namespace": tmp_path, "expected_identity": calibration.identity(path.stat()),
        "expected_bytes": len(raw), "expected_sha256": hashlib.sha256(raw).hexdigest(),
        "owner_uid": os.getuid(), "owner_gid": os.getgid(), "maximum": custody.G5A_RAW_BOUND}
    return path, raw, args


def test_nonroot_owned_io_hash_before_self_transfer_preserves_exact_bytes_all8_immutable_fields(tmp_path):
    path, raw, args = _owned_io(tmp_path)
    receipt = custody._g5a_transfer_pinned_io(path, **args)
    assert receipt["bytes"] == len(raw) and receipt["sha256"] == hashlib.sha256(raw).hexdigest()
    for field in ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_blocks", "st_atime_ns", "st_mtime_ns"):
        assert receipt["identity_before"][field] == receipt["identity_after"][field]
    assert receipt["stream_complete_verified"] is False and receipt["G5_GREEN"] is False
    assert receipt["qualification_claimed"] is False
    assert "actual_root_worker_FIN_verified" not in receipt
    assert "actual_private_PID1_FIN_verified" not in receipt


@pytest.mark.parametrize("attack", ["sha", "bytes", "inode", "atime", "blocks", "parent_mode", "symlink", "hardlink", "path", "mode", "foreign_uid"])
def test_owned_source_io_rejects_rebinding_before_any_transfer(tmp_path, monkeypatch, attack):
    path, raw, args = _owned_io(tmp_path)
    if attack == "sha": args["expected_sha256"] = "0" * 64
    elif attack == "bytes": args["expected_bytes"] += 1
    elif attack == "inode": args["expected_identity"]["st_ino"] += 1
    elif attack == "atime": args["expected_identity"]["st_atime_ns"] += 1
    elif attack == "blocks": args["expected_identity"]["st_blocks"] += 1
    elif attack == "parent_mode": tmp_path.chmod(0o755)
    elif attack == "symlink": path.rename(tmp_path / "target"); path.symlink_to(tmp_path / "target")
    elif attack == "hardlink": os.link(path, tmp_path / "alias")
    elif attack == "path": path = tmp_path / "foreign.log"
    elif attack == "mode": path.chmod(0o644)
    else: args["owner_uid"] += 1
    called = []
    monkeypatch.setattr(custody.os, "fchown", lambda *items: called.append(items))
    with pytest.raises((ValueError, OSError)):
        custody._g5a_transfer_pinned_io(path, **args)
    assert called == []


def test_actual_nonroot_cannot_consume_fake_fin_flags_to_transfer_root_raw():
    assert os.getuid() > 0
    with pytest.raises(ValueError, match="ACTUAL_ROOT_TRANSFER_ROLE_REQUIRED"):
        custody._g5a_transfer_closed_worker_raw(request={}, result={"actual_root_FIN": True},
            manager={}, private_broker_kernel={"closed": True}, mapping={}, controller={}, private_pidfd=3)


def _untrusted_relay_decoder_document():
    """Attack input for a pure decoder ONLY; every test below requires RED.

    It is not a native ROOT observation, authenticated receipt or authority.
    No test launches a unit or accepts this dictionary as native custody.
    """
    from scripts import rc6_controlled_native_child_manager as manager
    sha, tree, boot = "1" * 40, "2" * 40, "12345678-1234-1234-1234-123456789abc"
    actor = {"pid": 40, "cgroup": "0::/system.slice/rc6-native-" + "a" * 32 + ".service"}
    controller = {"actor": actor, "unit": "rc6-native-" + "a" * 32 + ".service"}
    private = {"pid": 1, "namespace_pids": [1], "uids": [0] * 4, "gids": [0] * 4,
        "cgroup": actor["cgroup"], "boot_id": boot, "start_ticks": "20", "pid_namespace_inode": 99}
    host = {**private, "pid": 41, "namespace_pids": [41, 1]}
    mapping = {"host_private_PID1": host, "guardian_actual": actor, "binding": {}, "source_sha": sha, "source_tree": tree}
    request = {"binding": {}, "source_sha": sha, "source_tree": tree,
        "calibration_request_sha256": "3" * 64, "issuer": {"boot_id": boot, "pid_namespace_inode": 98}}
    kernel = {"pid": 2, "wait4_reaped_pid": 2, "actual_child_reaped": True,
        "kernel_pre_popen_echild_verified": True, "owned_children_exhaustion_verified": True,
        "process_group_absent_after_reap": True, "subreaper_activation_readback_verified": True,
        "subreaper_restore_attempted": True, "subreaper_restoration_readback_verified": True,
        "remaining_owned_children": [], "supervisor_pid": 1, "supervisor_native_tid": 1,
        "supervisor_kernel_identity_before_spawn": {"Pid": "1", "Tgid": "1", "Uid": "0\t0\t0\t0"}}
    child = {**private, "pid": 2, "parent_pid": 1, "namespace_pids": [2]}
    report = {"schema": "porota.rc6.g5a-custody-worker.v1", "operation_scope": custody.G5A_SCOPE,
        "binding": {}, "source_sha": sha, "source_tree": tree, "request_sha256": "3" * 64,
        "G5_GREEN": False, "qualification_claimed": False, "required_raw_complete": False,
        "privileged_controller": {"manager_sha256": custody.MANAGER_SHA256,
            "root_native_FIN_closed": True, "worker_kernel": kernel,
            "controller": {"external": controller, "unit": controller["unit"], "private_PID1": {"host": host, "local": private}},
            "oracle_closed_before_seal": True,
            "oracle_close": {"status": "ACTUAL_KERNEL_FIFO_DESCRIPTORS_CLOSED", "worker_birth": child,
                             "paired_close_receipt_sha256": "4" * 64}}}
    return report, {"request": request, "mapping": mapping, "controller": controller,
                    "manager": {"managed_custody_closed": manager.managed_custody_closed}}


@pytest.mark.parametrize("attack", ["schema", "sha", "tree", "binding", "request", "green", "full"])
def test_source_relay_decoder_rejects_schema_hash_and_qualification_forgery(attack):
    report, args = _untrusted_relay_decoder_document()
    field, value = {"schema": ("schema", "porota.rc6.capacity-custody-worker.v1"), "sha": ("source_sha", "0" * 40),
        "tree": ("source_tree", "0" * 40), "binding": ("binding", {"owner": "foreign"}),
        "request": ("request_sha256", "0" * 64), "green": ("G5_GREEN", True), "full": ("required_raw_complete", True)}[attack]
    report[field] = value
    with pytest.raises(ValueError, match="EXACT_SOURCE_RELAY_REQUIRED"):
        custody._g5a_validate_transfer_relay(report, **args)


@pytest.mark.parametrize("field", ["actual_child_reaped", "kernel_pre_popen_echild_verified", "owned_children_exhaustion_verified",
    "process_group_absent_after_reap", "subreaper_activation_readback_verified", "subreaper_restore_attempted",
    "subreaper_restoration_readback_verified", "wait4_reaped_pid", "remaining_owned_children"])
def test_source_relay_decoder_rejects_incomplete_original_fin_document(field):
    report, args = _untrusted_relay_decoder_document()
    report["privileged_controller"]["worker_kernel"][field] = [2] if field == "remaining_owned_children" else False
    with pytest.raises(ValueError, match="ORIGINAL_ROOT_WORKER_FIN_REQUIRED"):
        custody._g5a_validate_transfer_relay(report, **args)


@pytest.mark.parametrize("attack", ["missing", "flag", "status", "digest", "extra"])
def test_source_relay_decoder_rejects_unpaired_oracle_close_without_root_simulation(attack):
    report, args = _untrusted_relay_decoder_document(); value = report["privileged_controller"]
    if attack == "missing": value.pop("oracle_close")
    elif attack == "flag": value["oracle_closed_before_seal"] = False
    elif attack == "status": value["oracle_close"]["status"] = "WAITED_5_SECONDS"
    elif attack == "digest": value["oracle_close"]["paired_close_receipt_sha256"] = "unbound"
    else: value["oracle_close"]["assumed"] = True
    with pytest.raises(ValueError, match="SOURCE_PAIRED_ORACLE_CLOSE_REQUIRED"):
        custody._g5a_validate_transfer_relay(report, **args)


@pytest.mark.parametrize("field,value", [("pid", 3), ("parent_pid", 40), ("cgroup", "0::/foreign"),
    ("pid_namespace_inode", 98), ("boot_id", "unbound"), ("uids", [1001] * 4), ("namespace_pids", [40, 2])])
def test_source_relay_decoder_rejects_worker_pid_namespace_cgroup_rebinding(field, value):
    report, args = _untrusted_relay_decoder_document()
    report["privileged_controller"]["oracle_close"]["worker_birth"][field] = value
    with pytest.raises(ValueError, match="ORIGINAL_WORKER_PID_CGROUP_REBOUND"):
        custody._g5a_validate_transfer_relay(report, **args)


def test_source_guardian_transfers_only_after_native_original_fin_pidfd_and_echild_then_all11_hash():
    outer = inspect.getsource(custody.guardian_main)
    assert outer.index('bool(poller.poll(0))') < outer.index('_g5a_transfer_closed_worker_raw')
    assert outer.index('_g5a_transfer_closed_worker_raw') < outer.index('os.close(mapping_state["pidfd"])')
    assert 'result["external_guardian"]["g5a_raw_transfer"]' in outer
    source = inspect.getsource(custody._g5a_transfer_closed_worker_raw)
    assert source.index('manager["managed_custody_closed"]') < source.index('manager["pre_capture_kernel_state"]')
    assert source.index('manager["pre_capture_kernel_state"]') < source.index('_g5a_transfer_pinned_io')
    assert '"anon_inode:[pidfd]"' in source and 'not Path("/proc/"' in source
    assert 'actual_root_worker_FIN_verified=True, actual_private_PID1_FIN_verified=True' in source
    io = inspect.getsource(custody._g5a_transfer_pinned_io)
    assert io.index('digest.hexdigest() == expected_sha256') < io.index('os.fchown(')
    assert 'os.O_NOATIME' in io and 'G5A_RAW_CHUNK' in io and 'calibration.identity(before)' in io
    assert 'json.loads' not in io and 'base64' not in io and 'b"".join' not in io
    assert set(custody.G5A_STAT_FIELDS) == set(custody.read_identity(Path(custody.__file__).stat())) | {"st_atime_ns"}


def test_oracle_native_ack_receipt_is_retained_in_source_relay_before_parent_seal():
    source = inspect.getsource(custody._g5a_broker)
    assert 'seal_state["oracle_close"] = oracle.finish_close(deadline=api_deadline)' in source
    assert '"oracle_close": seal_state.get("oracle_close")' in source
    assert source.index('oracle.finish_close(') < source.index('root_seal.install_quota_custody_seal(')


def test_fixed_root_controls_are_source_bound_before_same_owned_descriptor_transfer():
    source = inspect.getsource(custody._g5a_transfer_closed_worker_raw)
    assert '("live-root-controller.json", "quota-ancestor-seal.json", "g5a-owner-oracle.json")' in source
    assert 'document.get("guardian_native_mapping") == mapping' in source
    assert 'document.get("pid_boundary") == result["privileged_controller"]["controller"]["private_PID1"]' in source
    assert 'document.get("contract_sha256") == request["g5a_contract_sha256"]' in source
    assert 'document.get("broker_birth") == result["privileged_controller"]["controller"]["private_PID1"]["local"]' in source
    assert 'document.get("directory") == str(namespace / "g5a-owner-oracle")' in source
    assert '"G5A_GUARDIAN_FIXED_CONTROL_ROOT_ORIGIN_REQUIRED"' in source
    assert 'maximum=CONTROL_BOUND' in source
