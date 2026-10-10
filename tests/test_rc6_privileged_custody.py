"""Economic guards and an own NONROOT opaque-FD transport, never ROOT proof."""
from __future__ import annotations

import copy
import base64
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import select
import sys

import pytest

from scripts import rc6_capacity_calibration as calibration
from scripts import rc6_privileged_custody as custody


def hashes():
    return {name: hashlib.sha256((custody.ROOT / name).read_bytes()).hexdigest()
            for name in calibration.CODE_MEMBERS}


def binding():
    return {"candidate_sha": "1" * 40, "candidate_tree": "2" * 40,
            "producer": "UNIT_NONROOT_FD_TRANSPORT_ONLY", "attempt_id": "UNIT_ONLY",
            "owner_id": "UNIT_ONLY", "workload_fingerprint": "4" * 64,
            "runner_class": "github-hosted/ubuntu-24.04"}


def test_contract_does_not_enable_backing_quota_or_qualification():
    contract = custody.native_contract("1" * 40, "2" * 40, hashes())
    assert contract["manager_sha256"] == calibration.DRIVER_SHA256 == custody.MANAGER_SHA256
    assert contract["root_actor_boundary"] == "PRIVATE_PID_NAMESPACE_BEFORE_ROOT_OR_NONROOT_PROBE_ACTOR"
    assert contract["root_foreign_process_signals_forbidden"] is True
    assert contract["namespace_mounts_for_custody_proof_allowed"] is True
    assert contract["quota_backing_mounts_allowed"] is False
    assert contract["backing_allocation_allowed"] is False and contract["quota_mutation_allowed"] is False
    assert contract["TERM_seconds"] == 2 and contract["failed_phase_FIN_seconds"] == 5
    assert contract["control_bound_bytes"] == 2 * 1024**2
    assert contract["native_probe_timeout_seconds"] == 60
    assert contract["recurring_additional_cost_usd"] == 0 and contract["persistent_infrastructure_allowed"] is False


@pytest.mark.parametrize("value", [None, True, 12, "", "A" * 40, "g" * 40, "0" * 39, "0" * 41])
def test_missing_exact_source_never_enables_a_contract(value):
    with pytest.raises(ValueError, match="ROOT_CUSTODY_EXACT_SOURCE_REQUIRED"):
        custody.native_contract(value, "2" * 40, hashes())


@pytest.mark.parametrize("member", [custody.MEMBER, custody.MANAGER_MEMBER,
    "scripts/rc6_capacity_calibration.py", "scripts/rc6_native_namespace_filter.py"])
def test_missing_or_rebound_frozen_program_blocks(member):
    code = hashes()
    code[member] = "changed"
    with pytest.raises(ValueError, match="ROOT_CUSTODY_AUTHENTIC_CODE_HASHES_REQUIRED"):
        custody.native_contract("1" * 40, "2" * 40, code)


@pytest.mark.parametrize("value", [True, 0, "", "../foreign", "x" * 32, "a" * 31, "a" * 33])
def test_unit_names_cannot_select_foreign_services(value):
    with pytest.raises(ValueError, match="ROOT_CUSTODY_EXACT_NAMESPACE_NONCE_REQUIRED"):
        custody.unit_name(value)


@pytest.mark.parametrize("value", [True, 0, -1, 10801, "45", float("nan")])
def test_transient_units_have_exact_finite_integer_bounds(tmp_path, value):
    with pytest.raises(ValueError, match="ROOT_CUSTODY_NATIVE_RUNTIME_BOUND_REQUIRED"):
        custody.service_properties(tmp_path, value)


def test_service_command_uses_frozen_bootstrap_and_no_foreign_proc_bind(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_TOOL_CACHE", "/opt/hostedtoolcache")
    command = custody.service_command(custody.ROOT, tmp_path / "custody-request.json",
        nonce="a" * 32, control_root=tmp_path, runtime_seconds=45, broker_mode="probe")
    assert command[:4] == ["sudo", "-n", "--", "systemd-run"]
    assert "--property=TimeoutStopSec=2" in command and "--property=KillMode=control-group" in command
    assert "--property=RuntimeMaxSec=45" in command and "--property=Restart=no" in command
    assert "--property=ProtectControlGroups=yes" in command and "--property=ProtectSystem=strict" in command
    assert "--property=RestrictAddressFamilies=AF_INET AF_INET6" in command
    assert any("CAP_SYS_PTRACE" in item for item in command if item.startswith("--property=CapabilityBoundingSet="))
    assert not any(item.startswith("--property=BindReadOnlyPaths=/proc") for item in command)
    assert command[command.index(custody.BRIDGE_SHELL) - 2 : command.index(custody.BRIDGE_SHELL)] == ["/bin/sh", "-c"]
    assert "--setenv=RUNNER_TOOL_CACHE=/opt/hostedtoolcache" in command
    assert "--guardian" in command and command[-1] == custody.unit_name("a" * 32)


def test_aliased_control_root_or_foreign_request_is_not_a_root_command(tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="ROOT_CUSTODY_PRIVATE_CONTROL_ROOT_REQUIRED"):
        custody.service_properties(alias, 45)
    with pytest.raises(ValueError, match="ROOT_CUSTODY_FIXED_OWN_REQUEST_PATH_REQUIRED"):
        custody.service_command(custody.ROOT, tmp_path.parent / "custody-request.json",
            nonce="a" * 32, control_root=tmp_path, runtime_seconds=45, broker_mode="probe")


@pytest.mark.parametrize("value", [None, "infinity", "1.5s", "1min", "-1s", "1s 1ms"])
def test_unknown_unit_duration_is_not_native_custody(value):
    with pytest.raises(ValueError, match="ROOT_CUSTODY_UNIT_DURATION_UNKNOWN"):
        custody._seconds(value)


@pytest.mark.parametrize("value", [{"status": "NATIVE_CUSTODY_PROVED", "proved": True},
                                    True, None, "NATIVE_CUSTODY_PROVED"])
def test_serialized_receipts_flags_and_json_cannot_admit_backing(value):
    with pytest.raises(ValueError, match="ROOT_CUSTODY_ACTUAL_IN_PROCESS_WITNESS_REQUIRED"):
        custody.validate_witness(value, binding=binding())


def test_constructed_unregistered_object_does_not_become_a_native_witness():
    forged = custody.CustodyWitness.__new__(custody.CustodyWitness)
    forged.receipt = {"status": "NATIVE_CUSTODY_PROVED"}
    with pytest.raises(ValueError, match="ROOT_CUSTODY_ACTUAL_IN_PROCESS_WITNESS_REQUIRED"):
        custody.validate_witness(forged, binding=binding())
    with pytest.raises(ValueError, match="ROOT_CUSTODY_SYNTHETIC_WITNESS_FORBIDDEN"):
        custody.CustodyWitness(object(), forged.receipt, binding())


def test_root_only_custody_decoder_never_grants_quota_or_backing():
    # Private constructor input is a decoder fixture ONLY. Even the registered
    # ROOT-only result cannot relax the original EACCES contract or allocate.
    code = hashes()
    fixture = {"status": "NATIVE_CUSTODY_PROVED", "source_sha": "1" * 40,
        "source_tree": "2" * 40, "code_hashes": code, "proof_only_no_G0_qualification": True}
    witness = custody.CustodyWitness(custody._AUTHORITY, fixture, binding())
    signal, receipt, reason = calibration.calibration_custody(witness, binding=binding(),
        source_sha="1" * 40, source_tree="2" * 40, code_hashes=code)
    assert signal["proved"] is True and signal["privileged_launch_authorized"] is False
    assert receipt["proof_only_no_G0_qualification"] is True
    assert reason == "CALIBRATION_PRIVATE_PID_ORIGINAL_PROC_ROOT_CONTRACT_REVIEW_REQUIRED"
    with pytest.raises(ValueError, match="CALIBRATION_NATIVE_CUSTODY_EXACT_SOURCE_REBOUND"):
        calibration.calibration_custody(witness, binding=binding(), source_sha="3" * 40,
            source_tree="2" * 40, code_hashes=code)
    fixture["privileged_launch_authorized"] = True
    with pytest.raises(ValueError, match="ROOT_CUSTODY_ACTUAL_IN_PROCESS_WITNESS_REQUIRED"):
        calibration.calibration_custody(witness, binding=binding(), source_sha="1" * 40,
            source_tree="2" * 40, code_hashes=code)


def test_root_metadata_cannot_substitute_actual_process_or_pidfd(tmp_path):
    actual = custody.kernel_process(os.getpid())
    pretend = copy.deepcopy(actual)
    pretend["uids"] = [0] * 4
    synthetic = tmp_path / ("r6-" + base64.urlsafe_b64encode(bytes.fromhex("a" * 32)).decode().rstrip("="))
    synthetic.mkdir(mode=0o700)
    path = synthetic / "broker-birth.json"
    calibration.publish(path, {"binding": binding(), "controller": {"actor": pretend}})
    state = {}
    with pytest.raises(ValueError, match="ROOT_CUSTODY_SUPERVISORY_BIRTH_NOT_ACTUAL_ROOT"):
        custody._observer(path, binding(), state, None)("poll", os.getpid(), 0, 1, -1)
    assert not state
    with pytest.raises(ValueError, match="ROOT_CUSTODY_ACTUAL_CONTROLLER_PIDFD_REQUIRED"):
        custody._closed_service({"actual_FIN": True, "pidfd_exit_observed": True})


def test_native_unexited_pidfd_is_not_fin_and_is_closed():
    descriptor = os.pidfd_open(os.getpid(), 0)
    state = {"pidfd": descriptor}
    with pytest.raises(ValueError, match="ROOT_CUSTODY_CONTROLLER_PIDFD_NOT_EXITED"):
        custody._closed_service(state)
    assert state["pidfd"] is None
    with pytest.raises(OSError) as error:
        os.fstat(descriptor)
    assert error.value.errno == errno.EBADF


def test_public_identity_remains_native_when_own_nonroot_child_is_not_dumpable():
    """Real EACCES regression; no ROOT or capability/dumpable change to pytest."""
    assert os.getuid() == os.geteuid() > 0
    assert custody.kernel_process(os.getpid())["capabilities"]["CapEff"] == 0
    ready_r, ready_w = os.pipe()
    release_r, release_w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(ready_r)
        os.close(release_w)
        libc = ctypes.CDLL(None, use_errno=True)
        valid = libc.prctl(4, 0, 0, 0, 0) == 0 and libc.prctl(3, 0, 0, 0, 0) == 0
        os.write(ready_w, b"0" if valid else b"F")
        os.close(ready_w)
        complete = bool(select.select([release_r], [], [], 5)[0]) and os.read(release_r, 1) == b"F"
        os.close(release_r)
        os._exit(0 if valid and complete else 72)
    os.close(ready_w)
    os.close(release_r)
    try:
        assert select.select([ready_r], [], [], 3)[0] and os.read(ready_r, 1) == b"0"
        public = custody.public_kernel_process(pid)
        assert public["pid"] == pid and public["parent_pid"] == os.getpid()
        assert public["uids"] == [os.getuid()] * 4 and public["gids"] == [os.getgid()] * 4
        assert "pid_namespace_inode" not in public
        descriptor = os.pidfd_open(pid, 0)
        try:
            assert custody.public_kernel_process(pid) == public
            with pytest.raises(OSError) as error:
                custody.kernel_process(pid)
            assert error.value.errno == errno.EACCES
        finally:
            os.close(descriptor)
    finally:
        os.close(ready_r)
        os.write(release_w, b"F")
        os.close(release_w)
        reaped, status = os.waitpid(pid, 0)
        assert reaped == pid and os.waitstatus_to_exitcode(status) == 0
        with pytest.raises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)


@pytest.mark.parametrize("field,value", [("pid", True), ("parent_pid", -1),
    ("start_ticks", "invented"), ("uids", [0]), ("gids", [True] * 4),
    ("capabilities", {"CapEff": 0}), ("namespace_pids", []), ("pid_namespace_inode", True)])
def test_public_identity_projection_rejects_incomplete_or_forged_types(field, value):
    actual = custody.kernel_process(os.getpid())
    actual[field] = value
    with pytest.raises(ValueError, match="ROOT_CUSTODY_PUBLIC_IDENTITY_INVALID"):
        custody.public_process_identity(actual)


def test_public_projection_cannot_forge_native_identity_or_claim_namespace_verification():
    actual = custody.kernel_process(os.getpid())
    forged = copy.deepcopy(actual)
    forged["uids"] = [0] * 4
    assert custody.public_process_identity(forged) != custody.public_kernel_process(os.getpid())
    forged = copy.deepcopy(actual)
    forged["pid_namespace_inode"] += 1
    assert custody.public_process_identity(forged) == custody.public_kernel_process(os.getpid())
    # This projection intentionally proves no namespace. The separate native
    # guardian-self comparison must reject the altered request snapshot.
    with pytest.raises(ValueError, match="ROOT_CUSTODY_ISSUER_SELF_HOST_NAMESPACE_REBOUND"):
        custody.issuer_host_namespace({"issuer": custody.issuer_snapshot(forged)}, actual)
    actual["invented_root_authority"] = True
    with pytest.raises(ValueError, match="ROOT_CUSTODY_PUBLIC_IDENTITY_FIELDS_REQUIRED"):
        custody.public_process_identity(actual)


def test_issuer_namespace_requires_self_snapshot_and_native_host_mapping():
    actual = custody.kernel_process(os.getpid())
    snapshot = custody.issuer_snapshot(actual)
    assert custody.issuer_host_namespace({"issuer": snapshot}, actual) == actual["pid_namespace_inode"]
    with pytest.raises(ValueError, match="ROOT_CUSTODY_ISSUER_SELF_NAMESPACE_REQUIRED"):
        custody.issuer_snapshot(custody.public_process_identity(actual))
    private = copy.deepcopy(actual)
    private["namespace_pids"] = [actual["pid"], 1]
    with pytest.raises(ValueError, match="ROOT_CUSTODY_ISSUER_SELF_HOST_NAMESPACE_REBOUND"):
        custody.issuer_host_namespace({"issuer": snapshot}, private)
    for invalid in (True, 0, snapshot["pid_namespace_inode"] + 1):
        altered = {**snapshot, "pid_namespace_inode": invalid}
        with pytest.raises(ValueError, match="ROOT_CUSTODY_ISSUER_SELF_HOST_NAMESPACE_REBOUND"):
            custody.issuer_host_namespace({"issuer": altered}, actual)


def test_failure_without_authentic_client_fin_never_reads_raw_or_cleans(tmp_path, monkeypatch):
    from scripts import rc6_authenticated_fixture_lifecycle as owned
    namespace = owned.create_namespace(tmp_path, binding())
    def forbidden(*args, **kwargs):
        pytest.fail("No log read, service query or cleanup without authentic client FIN")
    monkeypatch.setattr(custody, "_read", forbidden)
    monkeypatch.setattr(custody, "execute_service", forbidden)
    monkeypatch.setattr(owned, "cleanup_namespace", forbidden)
    result = custody._preserve_failure({"namespace": namespace}, tmp_path / "diagnostic",
        ValueError("ROOT_CUSTODY_ACTUAL_CONTROLLER_PIDFD_REQUIRED"))
    assert result["ROOT_FIN"] == result["writer_absence"] == "UNKNOWN"
    assert result["root_producer_RAW_read"] is False and result["cleanup_authorized"] is False
    assert result["reservation_recovery_credited_bytes"] == 0 and namespace.path.is_dir()
    assert not (tmp_path / "diagnostic").exists()


@pytest.mark.parametrize("field,value", [("Id", "foreign.service"), ("ActiveState", "active"),
    ("MainPID", "42"), ("ControlPID", "43"), ("ControlGroup", "/foreign"), ("SubState", "running")])
def test_foreign_or_active_unit_readback_never_allows_failure_raw(field, value):
    unit = custody.unit_name("a" * 32, guard=True)
    fields = {"Id": unit, "LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead",
        "MainPID": "0", "ControlPID": "0", "ControlGroup": ""}
    fields[field] = value
    raw = "".join(key + "=" + val + "\n" for key, val in fields.items()).encode()
    with pytest.raises(ValueError, match="ROOT_CUSTODY_DIAGNOSTIC_UNIT_STILL_ACTIVE_OR_UNKNOWN"):
        custody._inactive_unit_readback(unit, raw, 0)


def test_unknown_writer_failure_preserves_real_client_fin_and_keeps_namespace(tmp_path, monkeypatch):
    from scripts import rc6_authenticated_fixture_lifecycle as owned
    namespace = owned.create_namespace(tmp_path, binding())
    command = ["/bin/sh", "-c", custody.BRIDGE_SHELL, "rc6-own-diagnostic",
        sys.executable, "-I", "-B", "-c", "import sys;sys.stderr.write('ORIGINAL_STDERR');sys.exit(73)"]
    kernel, fin = custody.execute_service(namespace, command, cwd=custody.ROOT, environ={"PATH": "/usr/bin:/bin"},
        progress=None, timeout_seconds=5, log_relative="guardian-native.log", fin_label="root-guardian-proof")
    assert kernel["returncode"] == 73 and kernel["actual_child_reaped"]
    original_fin = calibration.read(namespace.path / fin.control_name)
    actual_execute = owned.execute_owned
    def cheap_unknown_query(ns, requested, **kwargs):
        assert requested[-1] == custody.unit_name(namespace.nonce, guard=True)
        assert requested[:2] == ["systemctl", "show"] and "sudo" not in requested
        return actual_execute(ns, [sys.executable, "-I", "-B", "-c", "print('UNIT_UNKNOWN')"], **kwargs)
    def no_capture(*args, **kwargs):
        pytest.fail("ROOT log must not be opened while exact unit/cgroup is UNKNOWN")
    monkeypatch.setattr(owned, "execute_owned", cheap_unknown_query)
    monkeypatch.setattr(owned, "capture_required_evidence", no_capture)
    monkeypatch.setattr(owned, "cleanup_namespace", no_capture)
    output = tmp_path / "diagnostic"
    result = custody._preserve_failure({"namespace": namespace, "kernel": kernel, "fin": fin,
        "unit": custody.unit_name(namespace.nonce, guard=True), "log": "guardian-native.log"}, output,
        ValueError("ROOT_CUSTODY_ACTUAL_CONTROLLER_PIDFD_REQUIRED"))
    assert (output / "client-fin-original.json").read_bytes() == original_fin
    assert result["original_client_FIN_authenticated"] is True and result["client_kernel"] == kernel
    assert result["ROOT_FIN"] == result["writer_absence"] == "UNKNOWN"
    assert result["root_producer_RAW_read"] is False and result["ROOT_custody_qualified"] is False
    assert result["cleanup_authorized"] is False and namespace.path.is_dir()
    assert (output / "failure.json").exists() and not (output / "closed-client-raw").exists()


@pytest.mark.parametrize("query_exit", [0, 4])
def test_inactive_unit_failure_captures_original_bytes_without_root_credit(tmp_path, monkeypatch, query_exit):
    from scripts import rc6_authenticated_fixture_lifecycle as owned
    namespace = owned.create_namespace(tmp_path, binding())
    command = ["/bin/sh", "-c", custody.BRIDGE_SHELL, "rc6-own-diagnostic",
        sys.executable, "-I", "-B", "-c", "import sys;sys.stderr.write('ORIGINAL_STDERR');sys.exit(73)"]
    kernel, fin = custody.execute_service(namespace, command, cwd=custody.ROOT, environ={"PATH": "/usr/bin:/bin"},
        progress=None, timeout_seconds=5, log_relative="guardian-native.log", fin_label="root-guardian-proof")
    assert kernel["returncode"] == 73 and kernel["actual_child_reaped"]
    actual_execute = owned.execute_owned
    def cheap_inactive_query(ns, requested, **kwargs):
        return actual_execute(ns, [sys.executable, "-I", "-B", "-c",
            "import sys;print('UNIT_READBACK_FIXTURE');sys.exit(" + str(query_exit) + ")"], **kwargs)
    # Decoder fixture only, not a claimed ROOT/native cgroup proof. The actual
    # original manager still supplies NONROOT FIN and byte-exact RAW capture.
    monkeypatch.setattr(owned, "execute_owned", cheap_inactive_query)
    monkeypatch.setattr(custody, "_inactive_unit_readback", lambda *args: {
        "unit": custody.unit_name(namespace.nonce, guard=True), "scope": "DECODER_FIXTURE_ONLY",
        "cgroup_state": "DECODER_FIXTURE_EMPTY", "ROOT_FIN_claimed": False})
    monkeypatch.setattr(custody, "_cgroup_writer_absence", lambda *args: "DECODER_FIXTURE_EMPTY")
    monkeypatch.setattr(owned, "cleanup_namespace", lambda *args: pytest.fail("Diagnostic cannot cleanup"))
    result = custody._preserve_failure({"namespace": namespace, "kernel": kernel, "fin": fin,
        "unit": custody.unit_name(namespace.nonce, guard=True), "log": "guardian-native.log"}, tmp_path / "diagnostic",
        ValueError("ROOT_CUSTODY_ACTUAL_CONTROLLER_PIDFD_REQUIRED"))
    assert result["root_producer_RAW_read"] is True
    capture = Path(result["diagnostic_capture"])
    manifest = json.loads((capture / "manifest.json").read_bytes())
    row = next(row for row in manifest["files"] if row["relative_source"] == "guardian-native.log")
    assert (capture / row["capture_file"]).read_bytes() == b"ORIGINAL_STDERR"
    assert result["ROOT_FIN"] == "UNKNOWN" and result["ROOT_custody_qualified"] is False
    assert result["reservation_recovery_credited_bytes"] == 0 and namespace.path.is_dir()
    assert result["unit_query_kernel"]["returncode"] == query_exit
    assert result["cgroup_revalidated_after_capture"] is True
    if query_exit == 4:
        assert manifest["phase_green"] is False


def test_exit4_absent_unit_readback_needs_exact_fields_and_never_claims_fresh_systemd(monkeypatch):
    unit = custody.unit_name("a" * 32, guard=True)
    fields = {"Id": unit, "LoadState": "not-found", "ActiveState": "inactive", "SubState": "dead",
        "MainPID": "0", "ControlPID": "0", "ControlGroup": ""}
    calls = []
    monkeypatch.setattr(custody, "_cgroup_writer_absence", lambda name: calls.append(name) or "DECODER_FIXTURE_ABSENT")
    raw = "".join(key + "=" + value + "\n" for key, value in fields.items()).encode()
    result = custody._inactive_unit_readback(unit, raw, 4)
    assert calls == [unit] and result["ROOT_FIN_claimed"] is False
    assert result["systemd_query_samples"] == 1 and result["fresh_systemd_recheck_after_capture"] is False
    for field, value in (("LoadState", "loaded"), ("ActiveState", "failed"), ("SubState", "failed"),
                         ("ControlGroup", "/system.slice/" + unit)):
        altered = {**fields, field: value}
        raw = "".join(key + "=" + val + "\n" for key, val in altered.items()).encode()
        with pytest.raises(ValueError, match="ROOT_CUSTODY_DIAGNOSTIC_EXIT4_NOT_EXACT_ABSENCE"):
            custody._inactive_unit_readback(unit, raw, 4)
    assert calls == [unit]


def test_typed_failure_never_becomes_a_root_witness():
    error = custody.CustodyFailure(ValueError("ROOT_CUSTODY_ACTUAL_CONTROLLER_PIDFD_REQUIRED"), {"status": "RED"})
    assert str(error) == "ROOT_CUSTODY_ACTUAL_CONTROLLER_PIDFD_REQUIRED" and error.diagnostics["status"] == "RED"
    with pytest.raises(ValueError, match="ROOT_CUSTODY_ACTUAL_IN_PROCESS_WITNESS_REQUIRED"):
        custody.validate_witness(error, binding=binding())


def test_original_manager_and_opaque_fd_transport_are_real_nonroot_only(tmp_path):
    """Exercise Unix FD inheritance without sudo/systemd/mount/ROOT/fixtures."""
    from scripts import rc6_authenticated_fixture_lifecycle as owned
    assert os.getuid() == os.geteuid() > 0
    namespace = owned.create_namespace(tmp_path, binding())
    request = {"namespace_receipt": owned.namespace_receipt(namespace), "binding": binding(),
               "owner_uid": os.geteuid(), "owner_gid": os.getegid()}
    request_path = namespace.path / "own-request.json"
    calibration.publish(request_path, request)
    program = ("import sys,os,json,pathlib;sys.path.insert(0," + repr(str(custody.ROOT)) + ");"
        "from scripts import rc6_privileged_custody as c;"
        "r=json.loads(pathlib.Path(" + repr(str(request_path)) + ").read_bytes());"
        "p=c.original_directory_bridge(r);"
        "print(json.dumps({'bridge':p,'uid':os.geteuid(),'ROOT_executed':False},sort_keys=True))")
    command = ["/bin/sh", "-c", custody.BRIDGE_SHELL, "rc6-own-nonroot-transport",
               sys.executable, "-I", "-B", "-c", program]
    before_stdin = os.fstat(0)
    kernel, fin = custody.execute_service(namespace, command, cwd=custody.ROOT,
        environ={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"},
        progress=None, timeout_seconds=10, log_relative="own-transport.json", fin_label="own-nonroot-transport")
    assert owned._phase_green(fin), kernel
    assert os.fstat(0) == before_stdin
    result = json.loads(calibration.read(namespace.path / "own-transport.json"))
    assert result["uid"] == os.geteuid() > 0 and result["ROOT_executed"] is False
    assert result["bridge"]["original_mount_id"] == request["namespace_receipt"]["mount_id"]
    assert result["bridge"]["identity"] == request["namespace_receipt"]["identity"]
    assert result["bridge"]["foreign_host_proc_exposed"] is False
    assert kernel["actual_child_reaped"] and kernel["owned_children_exhaustion_verified"]
    assert kernel["owned_cleanup_management_bound_seconds"] == 5
    capture = owned.capture_required_evidence(namespace, fin, tmp_path / "sealed-own-nonroot",
        ["own-request.json", "own-transport.json"])
    cleanup = owned.cleanup_namespace(namespace, fin, capture)
    assert cleanup["namespace_removed"] is True
