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
from types import SimpleNamespace

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
    assert contract["contract_revision_required"] and contract["proof_only_no_G0_qualification"]
    assert contract["original_PROC_EACCES_equivalence_approved"] is False
    assert contract["root_actor_seal_probe_only"] and contract["root_actor_seal_quota_ancestor_allowed"] is False


@pytest.mark.parametrize("value", [None, True, 12, "", "A" * 40, "g" * 40, "0" * 39, "0" * 41])
def test_missing_exact_source_never_enables_a_contract(value):
    with pytest.raises(ValueError, match="ROOT_CUSTODY_EXACT_SOURCE_REQUIRED"):
        custody.native_contract(value, "2" * 40, hashes())


@pytest.mark.parametrize("member", [custody.MEMBER, custody.MANAGER_MEMBER,
    custody.ROOT_SEAL_MEMBER, "scripts/rc6_capacity_calibration.py", "scripts/rc6_native_namespace_filter.py"])
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


def test_only_owned_guard_hang_adds_debug_without_changing_any_restriction(tmp_path):
    standard = custody.service_properties(tmp_path, 6, probe_only=True)
    assert not any(value.startswith("Log") for value in standard)
    guard = custody.service_command(custody.ROOT, tmp_path / "custody-request.json", nonce="a" * 32,
        control_root=tmp_path, runtime_seconds=6, broker_mode="guard-hang", guard=True)
    properties = [value.removeprefix("--property=") for value in guard if value.startswith("--property=")]
    assert properties == [*standard, "LogLevelMax=debug"]
    for mode in ("probe", "quota"):
        normal = custody.service_command(custody.ROOT, tmp_path / "probe-request.json", nonce="a" * 32,
            control_root=tmp_path, runtime_seconds=6, broker_mode=mode)
        expected = custody.service_properties(tmp_path, 6, probe_only=mode == "probe")
        assert [value.removeprefix("--property=") for value in normal if value.startswith("--property=")] == expected
        with pytest.raises(ValueError, match="GUARD_DIAGNOSTIC_ROLE_REQUIRED"):
            custody.service_command(custody.ROOT, tmp_path / "probe-request.json", nonce="a" * 32,
                control_root=tmp_path, runtime_seconds=6, broker_mode=mode, guard=True)
    with pytest.raises(ValueError, match="GUARD_DIAGNOSTIC_ROLE_REQUIRED"):
        custody.service_command(custody.ROOT, tmp_path / "custody-request.json", nonce="a" * 32,
            control_root=tmp_path, runtime_seconds=6, broker_mode="guard-hang")


@pytest.mark.parametrize("value", [None, "info", "", "0", True])
def test_guard_live_readback_requires_its_debug_profile(value):
    with pytest.raises(ValueError, match="GUARD_ACTUAL_DEBUG_LEVEL_REQUIRED"):
        custody.require_guard_log_profile(custody.unit_name("a" * 32, guard=True), {"LogLevelMax": value})
    custody.require_guard_log_profile(custody.unit_name("a" * 32, guard=True), {"LogLevelMax": "debug"})
    custody.require_guard_log_profile(custody.unit_name("a" * 32, guard=True), {"LogLevelMax": "7"})
    custody.require_guard_log_profile(custody.unit_name("a" * 32), {})
    with pytest.raises(ValueError, match="NON_GUARD_LOG_PROFILE_CHANGED"):
        custody.require_guard_log_profile(custody.unit_name("a" * 32), {"LogLevelMax": "debug"})


def systemd_raw_fixture():
    return {"manager": b"Version=255.4-1ubuntu8.11\nLogLevel=info\nLogTarget=journal-or-kmsg\nDefaultStandardOutput=journal\nDefaultStandardError=inherit\n",
        "version": b"systemd 255 (255.4-1ubuntu8.11)\n+PAM +AUDIT\n", "package": b"systemd\t255.4-1ubuntu8.11\n",
        "binaries": custody.wire({"schema": "porota.rc6.installed-systemd-binaries.v1",
            "ROOT_custody_qualified": False, "ROOT_FIN_claimed": False,
            "files": [{"path": path, "bytes": 64, "sha256": "a" * 64,
                "identity": [1, 2, 0, 0, 0o100755, 1, 64, 10, 11],
                "running_PID1_or_executor_identity_verified": False} for path in custody.SYSTEMD_BINARY_PATHS]})}


def test_systemd_fixed_queries_and_captured_metadata_do_not_qualify_root():
    command = custody.systemd_query_command("manager")
    assert command == ["/usr/bin/systemctl", "show", "--no-pager",
        "--property=Version,LogLevel,LogTarget,DefaultStandardOutput,DefaultStandardError"]
    assert custody.systemd_query_command("version") == ["/usr/bin/systemd-run", "--version"]
    package = custody.systemd_query_command("package")
    assert package[-1] == "systemd" and "--showformat=${Package}\t${Version}\n" in package
    for unknown in (None, "stop", "set-property", "foreign.service", "binaries"):
        with pytest.raises(ValueError, match="FIXED_SYSTEMD_QUERY_REQUIRED"):
            custody.systemd_query_command(unknown)
    result = custody.systemd_diagnostic_origin(systemd_raw_fixture())
    assert result["status"] == "SCOPED_NONROOT_SYSTEMD_OBSERVATION_ONLY"
    assert result["namespace_failure_cause"] == "UNKNOWN" and result["executor_logging_journal_target_observed"] is True
    assert result["running_PID1_binary_identity_verified"] is False
    assert result["ROOT_custody_qualified"] is result["ROOT_FIN_claimed"] is False
    with pytest.raises(ValueError, match="ACTUAL_IN_PROCESS_WITNESS_REQUIRED"):
        custody.validate_witness(result, binding=binding())


@pytest.mark.parametrize("kind,raw", [("manager", b""), ("manager", b"Version=255\n"),
    ("manager", b"Version=255\nVersion=255\n"), ("manager", b"x" * 65537),
    ("version", b"systemd invented\n"), ("version", b"systemd 255 (foreign-version)\n"),
    ("package", b"foreign\t255.4\n"), ("binaries", b"{}\n")])
def test_empty_malformed_or_rebound_systemd_provenance_stays_unknown(kind, raw):
    original = systemd_raw_fixture()
    original[kind] = raw
    result = custody.systemd_diagnostic_origin(original)
    assert result["status"] == "UNKNOWN" and result["namespace_failure_cause"] == "UNKNOWN"
    assert result["ROOT_custody_qualified"] is result["ROOT_FIN_claimed"] is False


def test_systemd_installed_binary_hash_is_readonly_and_not_a_running_pid1_proof():
    result = custody._installed_systemd_binary("/usr/bin/systemctl")
    assert result["bytes"] > 0 and result["sha256"] == hashlib.sha256(Path("/usr/bin/systemctl").read_bytes()).hexdigest()
    assert result["identity"][2] == 0 and result["running_PID1_or_executor_identity_verified"] is False
    with pytest.raises(ValueError, match="FIXED_SYSTEMD_BINARY_REQUIRED"):
        custody._installed_systemd_binary("/proc/1/exe")


def test_aliased_control_root_or_foreign_request_is_not_a_root_command(tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="ROOT_CUSTODY_PRIVATE_CONTROL_ROOT_REQUIRED"):
        custody.service_properties(alias, 45)
    with pytest.raises(ValueError, match="ROOT_CUSTODY_FIXED_OWN_REQUEST_PATH_REQUIRED"):
        custody.service_command(custody.ROOT, tmp_path.parent / "custody-request.json",
            nonce="a" * 32, control_root=tmp_path, runtime_seconds=45, broker_mode="probe")


@pytest.mark.parametrize("target", ["/", "/run", "/run/systemd", "/run/systemd/mount-rootfs",
    "/proc/1/root", "/proc/1/cwd", "/proc/1/fd/3", "/proc/self/root", "/proc/thread-self/ns/mnt", "/x/../proc/1"])
def test_access_mount_magiclink_or_root_is_rejected_before_any_root_launch(target):
    with pytest.raises(ValueError, match="ACCESS_MAGICLINK_OR_ROOT_FORBIDDEN"):
        custody.guard_access_targets(["InaccessiblePaths=" + target])


def test_access_mount_alias_to_root_is_rejected_and_real_pid1_directory_is_required(tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to("/", target_is_directory=True)
    with pytest.raises(ValueError, match="ACCESS_MAGICLINK_OR_ROOT_FORBIDDEN"):
        custody.guard_access_targets(["InaccessiblePaths=" + str(alias)])
    properties = custody.service_properties(tmp_path, 6, probe_only=True)
    mask = next(value for value in properties if value.startswith("InaccessiblePaths="))
    assert "/proc/1" in mask.split() and "/proc/1/root" not in mask
    assert "PrivateDevices=yes" in properties and "ProtectHome=read-only" in properties
    quota = custody.service_properties(tmp_path, 45)
    assert "PrivateDevices=yes" not in quota and "ProtectHome=read-only" not in quota


def test_host_boundary_is_self_metadata_and_cannot_be_enabled_by_flags():
    result = custody.host_boundary_snapshot()
    assert result == {"mount_namespace_inode": os.stat("/proc/self/ns/mnt").st_ino,
        "device_number": os.stat("/dev").st_dev, "devpts_device_number": os.stat("/dev/pts").st_dev}
    for value in ({}, {**result, "device_number": True}, {**result, "devpts_device_number": 0},
                  {**result, "root_proved": True}):
        with pytest.raises(ValueError, match="AUTHENTIC_HOST_BOUNDARY_REQUIRED"):
            custody.require_host_boundary(value)


def test_nonroot_bootstrap_never_loads_libc_or_attempts_mounts(monkeypatch):
    host = custody.kernel_process(os.getpid())
    monkeypatch.setattr(custody.ctypes, "CDLL", lambda *args, **kwargs: pytest.fail("Cannot mount from NONROOT"))
    request = {"host_boundary": custody.host_boundary_snapshot(), "issuer": custody.issuer_snapshot(host)}
    with pytest.raises(ValueError, match="PRIVATE_BOOTSTRAP_KERNEL_BOUNDARY_REQUIRED"):
        custody._prepare_private_mounts(request, mode="probe", host_identity=host)


@pytest.mark.parametrize("scenario", ["probe", "quota", "proc_detach", "proc_mount", "pts_detach",
    "pts_mount", "foreign_ro", "host_pts", "host_namespace", "shared_namespace", "stacked_proc"])
def test_private_bootstrap_fixed_mount_sequence_and_fail_closed_errnos_are_decoder_only(tmp_path, monkeypatch, scenario):
    # Pure syscall/readback decoder: every mount/umount is a recording stub.
    # No ROOT, namespace, mount, device mutation or native certification runs.
    actual = custody.kernel_process(os.getpid())
    own_mount = os.stat("/proc/self/ns/mnt").st_ino
    native_proc_read = custody._proc_text
    native_mountinfo = native_proc_read("/proc/self/mountinfo")
    native_devices = (os.stat("/dev").st_dev, os.stat("/dev/pts").st_dev)
    host = {"mount_namespace_inode": own_mount if scenario == "host_namespace" else own_mount + 1,
        "device_number": 100, "devpts_device_number": 200}
    request = {"issuer": custody.issuer_snapshot(actual), "host_boundary": host,
        "namespace_receipt": {"path": str(tmp_path)}}
    private = {**actual, "uids": [0] * 4, "namespace_pids": [actual["pid"], 1],
        "pid_namespace_inode": actual["pid_namespace_inode"] + 1}
    rows = ["1 0 8:1 / / ro - ext4 ext4 rw", "2 1 0:2 / /proc ro,nosuid,nodev,noexec - proc proc rw",
        "3 1 0:3 / /dev ro - tmpfs tmpfs rw", "4 3 0:4 / /dev/pts ro,nosuid,noexec - devpts devpts rw",
        "5 1 0:5 / " + str(tmp_path) + " rw - tmpfs tmpfs rw", "6 1 0:6 / /foreign rw,nosuid,nodev - tmpfs tmpfs rw"]
    if scenario == "stacked_proc": rows.append("7 2 0:7 / /proc/1 ro - tmpfs tmpfs rw")
    if scenario == "shared_namespace": rows[0] = rows[0].replace(" ro -", " ro shared:1 -")
    calls = []
    class FakeLibc:
        def umount2(self, target, flags):
            calls.append(("umount", target, flags))
            if scenario == ("proc_detach" if target == b"/proc" else "pts_detach"):
                ctypes.set_errno(errno.EPERM)
                return -1
            return 0
        def mount(self, source, target, fstype, flags, data):
            calls.append(("mount", source, target, fstype, flags.value, data))
            failure = "proc_mount" if target == b"/proc" else "pts_mount" if target == b"/dev/pts" else "foreign_ro"
            if scenario == failure:
                ctypes.set_errno(errno.EACCES)
                return -1
            if target == b"/foreign": rows[5] = rows[5].replace(" rw,nosuid,nodev -", " ro,nosuid,nodev -")
            return 0
    saved_stat = os.stat
    def metadata(path, *args, **kwargs):
        if str(path) == "/proc/self/ns/mnt": return SimpleNamespace(st_ino=own_mount)
        if str(path) == "/dev": return SimpleNamespace(st_dev=101)
        if str(path) == "/dev/pts": return SimpleNamespace(st_dev=200 if scenario == "host_pts" else 201)
        return saved_stat(path, *args, **kwargs)
    monkeypatch.setattr(custody.os, "getuid", lambda: 0)
    monkeypatch.setattr(custody.os, "geteuid", lambda: 0)
    monkeypatch.setattr(custody.os, "getpid", lambda: 1)
    monkeypatch.setattr(custody.os, "stat", metadata)
    monkeypatch.setattr(custody.ctypes, "CDLL", lambda *args, **kwargs: FakeLibc())
    monkeypatch.setattr(custody, "_proc_text", lambda path: "\n".join(rows))
    if scenario in ("probe", "quota"):
        result = custody._prepare_private_mounts(request, mode=scenario, host_identity=private)
        assert result["single_readonly_private_proc"] is True and result["quota_ancestor_sealed"] is False
        assert calls[:2] == [("umount", b"/proc", 2), ("mount", b"proc", b"/proc", b"proc", 15, None)]
        if scenario == "probe":
            assert calls[2:4] == [("umount", b"/dev/pts", 2), ("mount", b"devpts", b"/dev/pts", b"devpts", 11,
                b"newinstance,ptmxmode=0666,mode=0620,gid=5")]
            assert calls[4][2] == b"/foreign" and (calls[4][4] & (4096 | 32 | 1)) == (4096 | 32 | 1)
            assert not any(call[0] == "mount" and call[2] == os.fsencode(tmp_path) for call in calls)
        else:
            assert len(calls) == 2 and result["private_devpts_newinstance_created"] is False
    else:
        with pytest.raises(ValueError, match="ROOT_CUSTODY_PRIVATE|ROOT_CUSTODY_SINGLE_PRIVATE_PROC"):
            custody._prepare_private_mounts(request, mode="probe", host_identity=private)
        if scenario in ("host_namespace", "shared_namespace"): assert not calls
    assert native_proc_read("/proc/self/mountinfo") == native_mountinfo
    assert (saved_stat("/dev").st_dev, saved_stat("/dev/pts").st_dev) == native_devices


@pytest.mark.parametrize("raw", ["", "not mountinfo", "1 0 0:1 / / rw shared:1 - tmpfs tmpfs rw",
    "1 0 0:1 / /x\\garbage ro - tmpfs tmpfs ro", "1 0 0:1 / /x/../y ro - tmpfs tmpfs ro"])
def test_unknown_or_shared_mounts_cannot_reach_root_seal(raw):
    with pytest.raises(ValueError, match="ROOT_CUSTODY_PRIVATE_MOUNT"):
        custody._mount_rows(raw)


def test_single_proc_mount_rows_and_host_seal_arguments_keep_original_owner_binding(tmp_path):
    rows = custody._mount_rows("1 0 0:1 / / ro - ext4 ext4 ro\n2 1 0:2 / /proc ro,nosuid,nodev,noexec - proc proc rw")
    assert rows[1] == {"target": "/proc", "options": ["ro", "nosuid", "nodev", "noexec"], "type": "proc"}
    request = {"owner_uid": os.getuid(), "owner_gid": os.getgid(), "source_sha": "1" * 40,
        "source_tree": "2" * 40, "issuer": custody.issuer_snapshot(custody.kernel_process(os.getpid())),
        "host_boundary": custody.host_boundary_snapshot(), "namespace_receipt": {"path": str(tmp_path)}}
    arguments = custody._seal_arguments(request)
    assert arguments["host_devpts_device_number"] == os.stat("/dev/pts").st_dev
    assert arguments["control_root"] == str(tmp_path) and arguments["source_binding"] == {
        "source_sha": "1" * 40, "source_tree": "2" * 40}


@pytest.mark.parametrize("namespaces", [None, "mnt pid", "mnt pid user", "pid", "mnt pid unknown"])
@pytest.mark.parametrize("final_signal", ["SIGKILL", "KILL", "9", "TERM", "SIGTERM", "15", "HUP", "SIGHUP", "1"])
def test_masked_pid1_decoder_never_follows_foreign_proc_or_qualifies_root(tmp_path, monkeypatch, namespaces, final_signal):
    # Pure readback decoder fixture: no ROOT process, unit, mount or seal.
    actor = custody.kernel_process(os.getpid())
    actor.update(parent_pid=1, uids=[0] * 4, gids=[0] * 4)
    actor["capabilities"] = {key: 1 << 5 for key in actor["capabilities"]}
    name = custody.unit_name("a" * 32)
    actor["cgroup"] = "0::/system.slice/" + name
    pid1 = {**custody.public_process_identity(actor), "pid": 1, "parent_pid": 0, "namespace_pids": [1]}
    host = {key: number + 1 for key, number in custody.host_boundary_snapshot().items()}
    request = {"host_PID1": pid1, "host_boundary": host, "issuer": custody.issuer_snapshot(actor)}
    properties = dict(value.split("=", 1) for value in custody.service_properties(tmp_path, 45, probe_only=True))
    # Authenticated Source1f5678fe's original unit (2538 B, d5a5d710...43a58)
    # persists FinalKillSignal=KILL for the unchanged SIGKILL request.
    properties["FinalKillSignal"] = final_signal
    if namespaces is not None: properties["RestrictNamespaces"] = namespaces
    monkeypatch.setattr(custody, "kernel_process", lambda pid: actor)
    monkeypatch.setattr(custody, "public_kernel_process", lambda *args, **kwargs: pytest.fail("Host PID1 is masked"))
    serialized = [key + "=" + (" ".join('"' + word + '"' for word in value.split()) if key in custody.UNIT_PATH_KEYS else value)
        for key, value in properties.items()]
    parsed = custody.parse_unit_properties(("[Service]\n" + "\n".join(serialized)).encode())
    monkeypatch.setattr(custody, "_unit_file", lambda unit: parsed)
    if namespaces not in (None, "mnt pid") or final_signal not in ("SIGKILL", "KILL", "9"):
        with pytest.raises(ValueError, match="UNIT_PROPERTIES_REQUIRED|UNIT_NAMESPACE_BITMAP_UNKNOWN"):
            custody.controller_snapshot(name, 45, request=request, mode="probe")
        return
    result = custody.controller_snapshot(name, 45, request=request, mode="probe")
    assert result["external_PID1_identity_pending_live_ACK"] is True
    assert result["ROOT_actor_boundary_certified"] is False
    with pytest.raises(ValueError, match="ACTUAL_IN_PROCESS_WITNESS_REQUIRED"):
        custody.validate_witness(result, binding=binding())
    request["host_PID1"] = {**pid1, "boot_id": "00000000-0000-0000-0000-000000000000"}
    with pytest.raises(ValueError, match="EXTERNAL_PID1_IDENTITY_REBOUND"):
        custody.controller_snapshot(name, 45, request=request, mode="probe")


def test_probe_result_distinguishes_bootstrap_mounts_from_actor_mounts_decoder_only(tmp_path, monkeypatch, capsysbinary):
    from scripts import rc6_root_actor_seal as root_seal
    # Pure orchestration fixture: all ROOT identities, native setup and seal
    # calls are stubs. The resulting JSON cannot qualify custody or admission.
    request = {"owner_uid": os.getuid(), "owner_gid": os.getgid(), "binding": binding(),
        "source_sha": "1" * 40, "source_tree": "2" * 40,
        "issuer": custody.issuer_snapshot(custody.kernel_process(os.getpid())),
        "host_boundary": custody.host_boundary_snapshot(), "namespace_receipt": {"path": str(tmp_path)}}
    monkeypatch.setattr(custody.os, "getuid", lambda: 0)
    monkeypatch.setattr(custody.os, "geteuid", lambda: 0)
    monkeypatch.setattr(custody.os, "getpid", lambda: 1)
    monkeypatch.setattr(custody, "_request", lambda *args, **kwargs: (request, b"fixture", tmp_path))
    monkeypatch.setattr(custody, "_read", lambda *args, **kwargs: b'{"controller":{}}')
    monkeypatch.setattr(custody, "_private_identity", lambda *args: {"host": {"cgroup": "owned"}})
    monkeypatch.setattr(custody, "_external_controller", lambda *args: {"cgroup": "owned"})
    monkeypatch.setattr(custody, "_manager", lambda *args: {"pre_capture_kernel_state": lambda: {}})
    monkeypatch.setattr(root_seal, "install_root_actor_seal", lambda **kwargs: {"scope": "NONROOT_TEST_ONLY"})
    monkeypatch.setattr(custody, "_write_control", lambda *args: None)
    monkeypatch.setattr(custody, "_broker_probe", lambda *args: [])
    assert custody.broker_main("probe", tmp_path / "request.json", custody.unit_name("a" * 32)) == 0
    result = json.loads(capsysbinary.readouterr().out)
    assert result["namespace_setup_mounts_executed"] is True
    assert result["actor_mount_operations_attempted"] is result["actor_quota_syscall_probes_attempted"] is True
    assert result["actor_mount_operations_succeeded"] is result["actor_quota_mutations_succeeded"] is result["quota_backing_mounts_attempted"] is False
    assert "actor_mount_operations_executed" not in result and "quota_mutations_executed" not in result
    assert "quota_mutations_attempted" not in result
    assert "mount_operations_attempted" not in result
    assert result["proof_only_no_G0_qualification"] is result["contract_revision_required"] is True
    assert result["original_PROC_EACCES_equivalence_approved"] is False
    with pytest.raises(ValueError, match="ACTUAL_IN_PROCESS_WITNESS_REQUIRED"):
        custody.validate_witness(result, binding=binding())


@pytest.mark.parametrize("mutation", [None, "parent", "boundary", "namespace", "boot"])
def test_external_pid1_ack_requires_live_double_identity_and_pidfds(tmp_path, monkeypatch, mutation):
    # ONLY a decoder fixture with two readonly real pidfds (SELF and PID1).
    # Public ROOT fields below are substituted; this cannot mint a witness.
    observer = custody.kernel_process(os.getpid())
    parent = {**custody.public_kernel_process(1), "uids": [0] * 4, "gids": [0] * 4}
    actor = {**observer, "parent_pid": 1, "uids": [0] * 4, "gids": [0] * 4}
    nonce = "a" * 32
    name = custody.unit_name(nonce)
    actor["cgroup"] = "0::/system.slice/" + name
    host = custody.host_boundary_snapshot()
    namespace = tmp_path / ("r6-" + base64.urlsafe_b64encode(bytes.fromhex(nonce)).decode().rstrip("="))
    namespace.mkdir(mode=0o700)
    path = namespace / "broker-birth.json"
    birth = {"binding": binding(), "controller": {"unit": name, "actor": actor, "PID1": parent,
        "host_boundary": host, "mount_namespace_inode": host["mount_namespace_inode"] + 1}}
    if mutation == "parent": birth["controller"]["PID1"] = {**parent, "start_ticks": "0"}
    elif mutation == "boundary": birth["controller"]["host_boundary"] = {**host, "device_number": host["device_number"] + 1}
    elif mutation == "namespace": birth["controller"]["actor"] = {**actor, "pid_namespace_inode": actor["pid_namespace_inode"] + 1}
    elif mutation == "boot": birth["controller"]["PID1"] = {**parent, "boot_id": "00000000-0000-0000-0000-000000000000"}
    calibration.publish(path, birth)
    monkeypatch.setattr(custody, "public_kernel_process", lambda pid, **kwargs: parent if pid == 1 else custody.public_process_identity(actor))
    monkeypatch.setattr(custody, "kernel_process", lambda pid, **kwargs: observer)
    state = {}
    callback = custody._observer(path, binding(), state, None, expected_pid1=parent, expected_host=host)
    if mutation:
        with pytest.raises(ValueError, match="LIVE_EXTERNAL_PID1_OR_BOUNDARY_REBOUND"):
            callback("poll", os.getpid(), 0, 1, -1)
        assert not state and not path.with_suffix(".observed.json").exists()
    else:
        try:
            callback("poll", os.getpid(), 0, 1, -1)
            ack = json.loads(path.with_suffix(".observed.json").read_bytes())
            assert ack["actual_PID1"] == parent and ack["actual_controller"] == custody.public_process_identity(actor)
            assert ack["ROOT_custody_qualified_by_JSON"] is False
            assert state["external_PID1_observed_native"] is True
            with pytest.raises(ValueError, match="ACTUAL_IN_PROCESS_WITNESS_REQUIRED"):
                custody.validate_witness(ack, binding=binding())
        finally:
            for key in ("pidfd", "parent_pidfd"):
                if state.get(key) is not None: os.close(state[key])


@pytest.mark.parametrize("value", [None, "infinity", "1.5s", "1minute", "-1s", "1s 1h", "1s 1sec", "1s  1ms", "1m"])
def test_unknown_unit_duration_is_not_native_custody(value):
    with pytest.raises(ValueError, match="ROOT_CUSTODY_UNIT_DURATION_UNKNOWN"):
        custody._seconds(value)


@pytest.mark.parametrize("value,seconds", [("6s", 6), ("45s", 45), ("2s", 2), ("4min 50s", 290),
    ("4min50s", 290), ("2h 59min 50s", 10790), ("1min", 60), ("1s 1ms", 1.001), ("1000000us", 1)])
def test_systemd255_formatted_timespan_preserves_the_exact_deadline(value, seconds):
    assert custody._seconds(value) == seconds


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
    (namespace.path / "private-broker-native.log").write_bytes(b"UNREAD_PRIVATE_RAW")
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
    (namespace.path / "private-broker-native.log").write_bytes(b"UNREAD_PRIVATE_RAW")
    command = ["/bin/sh", "-c", custody.BRIDGE_SHELL, "rc6-own-diagnostic",
        sys.executable, "-I", "-B", "-c", "import sys;sys.stderr.write('ORIGINAL_STDERR');sys.exit(73)"]
    kernel, fin = custody.execute_service(namespace, command, cwd=custody.ROOT, environ={"PATH": "/usr/bin:/bin"},
        progress=None, timeout_seconds=5, log_relative="guardian-native.log", fin_label="root-guardian-proof")
    assert kernel["returncode"] == 73 and kernel["actual_child_reaped"]
    original_fin = calibration.read(namespace.path / fin.control_name)
    actual_execute = owned.execute_owned
    def cheap_unknown_query(ns, requested, **kwargs):
        if "--journal-unit" in requested:
            return actual_execute(ns, [sys.executable, "-I", "-B", "-c", "import sys;sys.exit(1)"], **kwargs)
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
    assert result["journal"]["origin"]["status"] == "UNKNOWN"
    assert (output / "journal-query-original.log").read_bytes() == b""
    assert (output / "journal-fin-original.json").exists()


@pytest.mark.parametrize("query_exit", [0, 4])
@pytest.mark.parametrize("private_defect", [None, "symlink", "hardlink", "oversize", "writable"])
def test_inactive_unit_failure_captures_original_bytes_without_root_credit(tmp_path, monkeypatch, query_exit, private_defect):
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
    snapshot = unit_snapshot_fixture(custody.unit_name(namespace.nonce, guard=True))
    snapshot_raw = custody.wire(snapshot)
    calibration.publish(namespace.path / "guardian-unit-original.json", snapshot)
    optional_bytes = {name: ("ORIGINAL_PRIVATE_TRACEBACK:" + name).encode()
        for name in custody.OPTIONAL_FAILURE_RAW_NAMES}
    for name, raw in optional_bytes.items():
        path = namespace.path / name
        path.write_bytes(raw)
        path.chmod(0o600)
    private_path = namespace.path / "private-broker-native.log"
    if private_defect == "symlink":
        private_path.unlink()
        private_path.symlink_to(namespace.path / "root-success.native.log")
    elif private_defect == "hardlink": os.link(private_path, namespace.path / "private-alias.log")
    elif private_defect == "oversize":
        with private_path.open("r+b") as stream: stream.truncate(custody.CONTROL_BOUND + 1)
    elif private_defect == "writable": private_path.chmod(0o622)
    (namespace.path / "foreign-not-enumerated.log").write_bytes(b"EXCLUDED")
    result = custody._preserve_failure({"namespace": namespace, "kernel": kernel, "fin": fin,
        "unit": custody.unit_name(namespace.nonce, guard=True), "log": "guardian-native.log"}, tmp_path / "diagnostic",
        ValueError("ROOT_CUSTODY_ACTUAL_CONTROLLER_PIDFD_REQUIRED"))
    assert result["root_producer_RAW_read"] is True
    capture = Path(result["diagnostic_capture"])
    manifest = json.loads((capture / "manifest.json").read_bytes())
    row = next(row for row in manifest["files"] if row["relative_source"] == "guardian-native.log")
    assert (capture / row["capture_file"]).read_bytes() == b"ORIGINAL_STDERR"
    snapshot_row = next(row for row in manifest["files"] if row["relative_source"] == "guardian-unit-original.json")
    assert (capture / snapshot_row["capture_file"]).read_bytes() == snapshot_raw
    assert snapshot_row["sha256"] == hashlib.sha256(snapshot_raw).hexdigest()
    assert set(result["optional_producer_RAW"]) == set(custody.OPTIONAL_FAILURE_RAW_NAMES)
    assert not any(row["relative_source"] == "foreign-not-enumerated.log" for row in manifest["files"])
    for name, raw in optional_bytes.items():
        metadata = result["optional_producer_RAW"][name]
        rows = [row for row in manifest["files"] if row["relative_source"] == name]
        assert metadata["ROOT_FIN_claimed"] is metadata["ROOT_custody_qualified"] is False
        if name == "private-broker-native.log" and private_defect:
            assert metadata["status"] == "UNKNOWN" and not rows
        else:
            assert metadata["status"] == "CAPTURED_PRIVATE_RAW_DIAGNOSTIC_ONLY"
            assert len(rows) == 1 and (capture / rows[0]["capture_file"]).read_bytes() == raw
            assert metadata["sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["unit_snapshot"]["status"] == "CAPTURED_ROOT_UNIT_METADATA_DIAGNOSTIC_ONLY"
    assert result["unit_snapshot"]["ROOT_FIN_claimed"] is result["unit_snapshot"]["ROOT_custody_qualified"] is False
    assert result["ROOT_FIN"] == "UNKNOWN" and result["ROOT_custody_qualified"] is False
    assert result["reservation_recovery_credited_bytes"] == 0 and namespace.path.is_dir()
    assert result["unit_query_kernel"]["returncode"] == query_exit
    assert result["cgroup_revalidated_after_capture"] is True
    if query_exit == 4:
        assert manifest["phase_green"] is False


@pytest.mark.parametrize("exit_code", [0, 73])
def test_private_raw_is_pinned_after_actual_nonroot_fin_before_secondary_checks(tmp_path, exit_code):
    assert os.getuid() == os.geteuid() > 0
    manager = custody._manager(custody.ROOT)
    manager["pre_capture_kernel_state"]()
    path = tmp_path / "private-broker-native.log"
    kernel = manager["managed_native_child"]([sys.executable, "-I", "-B", "-c",
        "import sys;sys.stderr.write('ORIGINAL_PRIVATE_TRACEBACK');sys.exit(" + str(exit_code) + ")"],
        custody.ROOT, path, {"PATH": "/usr/bin:/bin"}, 5, terminate_grace=2, progress_poll=5)
    assert manager["managed_custody_closed"](kernel) and kernel["returncode"] == exit_code
    assert kernel["actual_child_reaped"] and kernel["owned_children_exhaustion_verified"]
    raw = custody._closed_private_broker_raw(manager, kernel, path,
        owner_uid=os.getuid(), owner_gid=os.getgid())
    assert raw == b"ORIGINAL_PRIVATE_TRACEBACK" and path.read_bytes() == raw
    with pytest.raises(ValueError, match="ROOT_CUSTODY_NATIVE_PRIVATE_PIDFD_REQUIRED"):
        custody.require(False, "ROOT_CUSTODY_NATIVE_PRIVATE_PIDFD_REQUIRED")
    with pytest.raises(json.JSONDecodeError): calibration.decode(raw)
    assert path.read_bytes() == raw  # Either secondary failure retains causal bytes.
    with pytest.raises(ValueError, match="ACTUAL_IN_PROCESS_WITNESS_REQUIRED"):
        custody.validate_witness({"kernel": kernel, "raw_sha256": custody.sha256(raw)}, binding=binding())


@pytest.mark.parametrize("failure", ["fin", "echild"])
def test_private_raw_never_opens_or_transfers_before_original_fin_and_echild(tmp_path, monkeypatch, failure):
    seen = []
    def closed(kernel):
        seen.append("fin")
        return failure != "fin"
    def before():
        seen.append("echild")
        raise ValueError("ACTUAL_OWN_ECHILD_UNKNOWN")
    def forbidden(*args, **kwargs): pytest.fail("No producer IO/ownership before closed FIN plus ECHILD")
    monkeypatch.setattr(custody.os, "open", forbidden)
    monkeypatch.setattr(custody.os, "fchown", forbidden)
    with pytest.raises(ValueError):
        custody._closed_private_broker_raw({"managed_custody_closed": closed, "pre_capture_kernel_state": before},
            {}, tmp_path / "private-broker-native.log", owner_uid=os.getuid(), owner_gid=os.getgid())
    assert seen == (["fin"] if failure == "fin" else ["fin", "echild"])


@pytest.mark.parametrize("defect", ["symlink", "hardlink", "fifo", "oversize", "writable", "name"])
def test_closed_private_raw_rejects_aliases_and_bounds_before_transfer(tmp_path, monkeypatch, defect):
    path = tmp_path / ("foreign.log" if defect == "name" else "private-broker-native.log")
    if defect == "fifo": os.mkfifo(path, 0o600)
    else:
        path.write_bytes(b"OWN_NONROOT_TRACEBACK")
        path.chmod(0o600)
    if defect == "symlink":
        path.rename(tmp_path / "target.log")
        path.symlink_to(tmp_path / "target.log")
    elif defect == "hardlink": os.link(path, tmp_path / "alias.log")
    elif defect == "oversize":
        with path.open("r+b") as stream: stream.truncate(custody.CONTROL_BOUND + 1)
    elif defect == "writable": path.chmod(0o622)
    monkeypatch.setattr(custody.os, "fchown", lambda *args: pytest.fail("Untrusted producer cannot be transferred"))
    manager = {"managed_custody_closed": lambda kernel: True, "pre_capture_kernel_state": lambda: {}}
    with pytest.raises((ValueError, OSError)):
        custody._closed_private_broker_raw(manager, {}, path, owner_uid=os.getuid(), owner_gid=os.getgid())


def test_closed_private_raw_rejects_path_rebound_and_closes_pinned_descriptor(tmp_path, monkeypatch):
    path = tmp_path / "private-broker-native.log"
    path.write_bytes(b"ORIGINAL_OWN_NONROOT")
    path.chmod(0o600)
    actual_read, descriptors = os.read, []
    def rebound(descriptor, maximum):
        part = actual_read(descriptor, maximum)
        if not descriptors:
            descriptors.append(descriptor)
            path.rename(tmp_path / "former.log")
            path.write_bytes(b"OTHER_OWN_NONROOT")
            path.chmod(0o600)
        return part
    monkeypatch.setattr(custody.os, "read", rebound)
    manager = {"managed_custody_closed": lambda kernel: True, "pre_capture_kernel_state": lambda: {}}
    with pytest.raises(ValueError, match="ROOT_CUSTODY_PRIVATE_RAW_CHANGED"):
        custody._closed_private_broker_raw(manager, {}, path, owner_uid=os.getuid(), owner_gid=os.getgid())
    with pytest.raises(OSError) as error: os.fstat(descriptors[0])
    assert error.value.errno == errno.EBADF


def unit_snapshot_fixture(unit):
    raw = b'[Service]\nInaccessiblePaths="/proc/1"\n'
    return {"schema": "porota.rc6.root-owned-transient-unit-diagnostic.v1", "unit": unit, "binding": binding(),
        "source_sha": binding()["candidate_sha"], "source_tree": binding()["candidate_tree"],
        "original_unit": {"path": "/run/systemd/transient/" + unit, "sha256": hashlib.sha256(raw).hexdigest(),
            "raw_base64": base64.b64encode(raw).decode(), "identity": [1, 2, 0, 0, 0o100644]},
        "scope": "DIAGNOSTIC_ONLY", "ROOT_FIN_claimed": False, "ROOT_custody_qualified": False}


@pytest.mark.parametrize("mutation", [None, "unit", "binding", "uid", "mode", "digest", "raw", "root_claim"])
def test_prebirth_unit_snapshot_preserves_original_digest_but_never_authorizes_root(mutation):
    unit = custody.unit_name("a" * 32, guard=True)
    value = unit_snapshot_fixture(unit)
    if mutation == "unit": value["unit"] = "foreign.service"
    elif mutation == "binding": value["binding"] = {}
    elif mutation == "uid": value["original_unit"]["identity"][2] = os.getuid()
    elif mutation == "mode": value["original_unit"]["identity"][4] = 0o40700
    elif mutation == "digest": value["original_unit"]["sha256"] = "0" * 64
    elif mutation == "raw": value["original_unit"]["raw_base64"] = "invalid base64"
    elif mutation == "root_claim": value["ROOT_custody_qualified"] = True
    result = custody.unit_snapshot_origin(custody.wire(value), unit=unit, binding=binding())
    assert result["status"] == ("UNKNOWN" if mutation else "CAPTURED_ROOT_UNIT_METADATA_DIAGNOSTIC_ONLY")
    assert result["ROOT_FIN_claimed"] is result["ROOT_custody_qualified"] is False
    if mutation is None: assert result["original_unit_sha256"] == value["original_unit"]["sha256"]
    with pytest.raises(ValueError, match="ACTUAL_IN_PROCESS_WITNESS_REQUIRED"):
        custody.validate_witness(result, binding=binding())


def test_unit_snapshot_is_published_before_controller_validation_decoder_only(tmp_path, monkeypatch):
    request = {"runtime_seconds": 6}
    seen = []
    monkeypatch.setattr(custody.os, "getuid", lambda: 0)
    monkeypatch.setattr(custody.os, "geteuid", lambda: 0)
    monkeypatch.setattr(custody, "_request", lambda *args: (request, b"fixture", tmp_path))
    monkeypatch.setattr(custody, "_publish_unit_snapshot", lambda *args: seen.append("snapshot"))
    def reject(*args, **kwargs):
        assert seen == ["snapshot"]
        raise ValueError("ORIGINAL_VALIDATION_RED")
    monkeypatch.setattr(custody, "controller_snapshot", reject)
    with pytest.raises(ValueError, match="ORIGINAL_VALIDATION_RED"):
        custody.guardian_main("guard-hang", tmp_path / "request.json", custody.unit_name("a" * 32, guard=True))


def test_systemd255_quoted_paths_append_reset_and_sections_decode_exactly():
    raw = b'[Unit]\nDescription=owned\n[Service]\nInaccessiblePaths="-/run/dbus" "/proc/1"\nInaccessiblePaths="-/dev/shm"\nReadWritePaths="/discard"\nReadWritePaths=\nReadWritePaths="/owned\\x20space"\nExecStart=\nExecStart="/usr/bin/python3" "-I"\n'
    parsed = custody.parse_unit_properties(raw)
    assert parsed["path_lists"] == {"InaccessiblePaths": ["-/run/dbus", "/proc/1", "-/dev/shm"], "ReadWritePaths": ["/owned space"]}
    assert "/proc/1" in parsed["path_lists"]["InaccessiblePaths"]
    assert parsed["properties"]["ExecStart"] == '"/usr/bin/python3" "-I"'


@pytest.mark.parametrize("value,expected", [(r'"/a\\b"', "/a\\b"), (r'"/a\040b"', "/a b"),
    (r'"/a\sb"', "/a b"), (r'"/a\u00E9"', "/aé"), ('"/a%%b"', "/a%b")])
def test_systemd_path_cunescape_and_literal_specifier(value, expected):
    assert custody._unit_path_words(value) == [expected]


@pytest.mark.parametrize("raw", [b'InaccessiblePaths="/proc/1"', b'[Unit]\nInaccessiblePaths="/proc/1"',
    b'[Unknown]\nKey=value', b'[Service]\nInaccessiblePaths="/proc/1', b'[Service]\nInaccessiblePaths="/a\\q"',
    b'[Service]\nInaccessiblePaths="/a\\xZZ"', b'[Service]\nInaccessiblePaths="/a\\000"',
    b'[Service]\nInaccessiblePaths="/a%n"', b'[Service]\nInaccessiblePaths=""',
    b'[Service]\nNoNewPrivileges=yes\nNoNewPrivileges=no', b'[Unit]\nKey=a\n[Service]\nKey=a',
    b'[Service]\nKey=a\\', b'[Service]\nReadWriteDirectories=/a', b'[Service]\nKey=\x00'])
def test_unknown_or_ambiguous_unit_serialization_fails_closed(raw):
    with pytest.raises((ValueError, UnicodeError), match="ROOT_CUSTODY_UNIT"):
        custody.parse_unit_properties(raw)


@pytest.mark.parametrize("key", sorted(custody.UNIT_SERVICE_ONLY_KEYS - custody.UNIT_OPAQUE_ADDITIVE_KEYS))
def test_guard_property_in_wrong_section_never_qualifies_as_service_setting(key):
    with pytest.raises(ValueError, match="UNIT_SERVICE_SECTION_REQUIRED"):
        custody.parse_unit_properties(("[Unit]\n" + key + "=yes\n[Service]\nDescription=fixture").encode())


@pytest.mark.parametrize("value,expected", [("mnt pid", 0x20020000), ("pid mnt", 0x20020000),
    ("~cgroup net user ipc uts", 0x20020000), ("mnt pid user", 0x30020000), ("mnt", 0x20000)])
def test_namespace_serialization_compares_the_exact_allowed_bitmap(value, expected):
    assert custody.allowed_namespace_bitmap(value) == expected
    assert (custody.allowed_namespace_bitmap(value) == 0x20020000) is (value in ("mnt pid", "pid mnt", "~cgroup net user ipc uts"))


@pytest.mark.parametrize("value", [None, True, "", "yes", "no", "0x20020000", "mnt mnt pid", "mnt pid foreign", "~mnt ~pid"])
def test_unknown_namespace_serialization_never_passes_readback(value):
    with pytest.raises(ValueError, match="UNIT_NAMESPACE_BITMAP_UNKNOWN"):
        custody.allowed_namespace_bitmap(value)


@pytest.mark.parametrize("mutation", [None, "whitelist", "omit", "extra", "group", "errno", "double", "duplicate"])
def test_readback_requires_the_complete_original_systemd_denylist(mutation):
    value = custody.SYSTEMD_DENIED_FILTER
    if mutation == "whitelist": value = value[1:]
    elif mutation == "omit": value = "~setns"
    elif mutation == "extra": value += " unshare"
    elif mutation == "group": value = "~@privileged setns"
    elif mutation == "errno": value = value.replace("ptrace", "ptrace:1")
    elif mutation == "double": value = "~" + value
    elif mutation == "duplicate": value += " setns"
    assert custody.exact_system_call_filter(value) is (mutation is None)


@pytest.mark.parametrize("mutation", [None, "content", "mode", "inode"])
def test_real_nonroot_unit_read_ignores_atime_but_rejects_content_metadata_rebinding(tmp_path, mutation):
    path = tmp_path / "unit.service"
    path.write_bytes(b'[Service]\nInaccessiblePaths="/proc/1"\n')
    initial = path.stat()
    os.utime(path, ns=(1, initial.st_mtime_ns))
    before = path.stat()
    raw = path.read_bytes()
    after_read = path.stat()
    assert raw and custody.unit_source_identity(before) == custody.unit_source_identity(after_read)
    assert before.st_atime_ns <= before.st_mtime_ns
    if mutation == "content": path.write_bytes(b'CHANGED')
    elif mutation == "mode": path.chmod(0o400)
    elif mutation == "inode":
        replacement = tmp_path / "replacement"
        replacement.write_bytes(raw)
        replacement.replace(path)
    assert (custody.unit_source_identity(before) == custody.unit_source_identity(path.stat())) is (mutation is None)


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


def test_guard_startup_status_is_retained_without_changing_security_properties(tmp_path):
    command = custody.service_command(custody.ROOT, tmp_path / "custody-request.json", nonce="a" * 32,
        control_root=tmp_path, runtime_seconds=6, broker_mode="guard-hang", guard=True)
    assert "--quiet" not in command and "--collect" in command
    assert "--property=NoNewPrivileges=yes" in command and "--property=ProtectSystem=strict" in command
    assert any("CAP_SYS_PTRACE" in value for value in command if value.startswith("--property=CapabilityBoundingSet="))
    assert any("/proc/1" in value.split() for value in command if value.startswith("--property=InaccessiblePaths="))
    assert not any("/proc/1/root" in value for value in command)
    with pytest.raises(ValueError, match="ACCESS_MAGICLINK_OR_ROOT_FORBIDDEN"):
        custody.guard_access_targets(["InaccessiblePaths=-/proc/1/root"])


def test_journal_command_cannot_cross_boot_or_unit_boundaries():
    unit, boot = custody.unit_name("a" * 32, guard=True), "b" * 32
    command = custody.journal_command(unit, boot)
    assert command[0] == "/usr/bin/journalctl" and "--lines=30" in command and "--no-pager" in command
    assert command[command.index("_BOOT_ID=" + boot):] == ["_BOOT_ID=" + boot, "_UID=0", "UNIT=" + unit,
        "+", "_BOOT_ID=" + boot, "_SYSTEMD_UNIT=" + unit]
    assert not any(value in command for value in ("--follow", "--vacuum-time", "--rotate", "sudo"))
    with pytest.raises(ValueError, match="ROOT_CUSTODY_PRIVATE_UNIT_NAME_REQUIRED"):
        custody.journal_command("foreign.service", boot)
    with pytest.raises(ValueError, match="ROOT_CUSTODY_JOURNAL_ACTUAL_BOOT_REQUIRED"):
        custody.journal_command(unit, "current")


def pid1_exit_record(unit, boot, invocation="c" * 32):
    return {"_BOOT_ID": boot, "_PID": "1", "_UID": "0", "UNIT": unit,
        "_SYSTEMD_UNIT": "init.scope", "_SYSTEMD_CGROUP": "/init.scope",
        "CODE_FILE": "src/core/unit.c", "CODE_FUNC": "unit_log_process_exit", "CODE_LINE": "6066",
        "MESSAGE_ID": custody.UNIT_PROCESS_EXIT_MESSAGE_ID, "EXIT_CODE": "exited", "EXIT_STATUS": "226",
        "INVOCATION_ID": invocation, "MESSAGE": "Main process exited, status=226/NAMESPACE"}


def invocation_record(boot, invocation="c" * 32):
    # Decoder fixture for a generic systemd log without UNIT/cgroup metadata.
    return {"_BOOT_ID": boot, "_PID": "42", "_UID": "0", "INVOCATION_ID": invocation,
        "CODE_FILE": "src/core/namespace.c", "CODE_FUNC": "setup_namespace", "CODE_LINE": "2590",
        "_EXE": "/usr/lib/systemd/systemd-executor", "ERRNO": "2", "MESSAGE": "Failed remount /owned: No such file"}


def test_invocation_query_is_fixed_scoped_and_keeps_causal_fields():
    unit, boot, invocation = custody.unit_name("a" * 32, guard=True), "b" * 32, "c" * 32
    command = custody.journal_command(unit, boot, invocation)
    assert command[-3:] == ["_BOOT_ID=" + boot, "_UID=0", "INVOCATION_ID=" + invocation]
    assert "--lines=30" in command and "+" not in command
    assert not any(arg.startswith(("UNIT=", "_SYSTEMD_UNIT=")) for arg in command)
    fields = next(arg for arg in command if arg.startswith("--output-fields=")).split("=", 1)[1].split(",")
    assert {"INVOCATION_ID", "MESSAGE_ID", "EXIT_CODE", "EXIT_STATUS", "PROCESS_PID", "_EXE", "ERRNO", "CODE_FUNC"} <= set(fields)
    for value in ("", "0" * 32, "C" * 32, True, 42, "c" * 31, "c" * 33, "current"):
        with pytest.raises(ValueError, match="JOURNAL_INVOCATION_ID_REQUIRED"):
            custody.journal_command(unit, boot, value)


def test_pid1_anchor_is_only_diagnostic_and_preserves_exact_primary_digests():
    unit, boot = custody.unit_name("a" * 32, guard=True), "b" * 32
    raw = custody.wire(pid1_exit_record(unit, boot))
    result = custody.journal_invocation_anchor(raw, unit=unit, boot_id=boot, owner_uid=os.geteuid(),
        kernel={"timed_out": False, "returncode": 0})
    assert result["status"] == "PID1_INVOCATION_DIAGNOSTIC_ANCHOR_ONLY"
    assert result["primary_raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["anchor_record_sha256"] == hashlib.sha256(raw.rstrip(b"\n")).hexdigest()
    assert result["invocation_id"] == "c" * 32 and result["exit_status"] == 226
    assert result["ROOT_FIN_claimed"] is result["ROOT_custody_qualified"] is False
    with pytest.raises(ValueError, match="ACTUAL_IN_PROCESS_WITNESS_REQUIRED"):
        custody.validate_witness(result, binding=binding())


@pytest.mark.parametrize("field,value", [("_BOOT_ID", "d" * 32), ("_UID", "1001"), ("_PID", "2"),
    ("UNIT", "foreign.service"), ("CODE_FILE", "foreign.c"), ("CODE_FUNC", "unit_log_failure"),
    ("CODE_LINE", True), ("CODE_LINE", "0"), ("MESSAGE_ID", "d" * 32), ("EXIT_CODE", "killed"),
    ("EXIT_STATUS", "1"), ("EXIT_STATUS", 226), ("INVOCATION_ID", "0" * 32),
    ("INVOCATION_ID", ["c" * 32]), ("INVOCATION_ID", None), ("INVOCATION_ID", "C" * 32)])
def test_bad_anchor_cannot_select_secondary_journal(field, value):
    unit, boot = custody.unit_name("a" * 32, guard=True), "b" * 32
    record = pid1_exit_record(unit, boot)
    record[field] = value
    result = custody.journal_invocation_anchor(custody.wire(record), unit=unit, boot_id=boot,
        owner_uid=os.geteuid(), kernel={"timed_out": False, "returncode": 0})
    assert result["status"] == "UNKNOWN" and "invocation_id" not in result
    assert result["ROOT_FIN_claimed"] is result["ROOT_custody_qualified"] is False


def test_anchor_rejects_conflicting_or_duplicate_pid1_exits():
    unit, boot = custody.unit_name("a" * 32, guard=True), "b" * 32
    record = pid1_exit_record(unit, boot)
    for other in (record, {**record, "INVOCATION_ID": "d" * 32},
                  {**record, "CODE_FUNC": "unit_log_failure", "INVOCATION_ID": "d" * 32}):
        result = custody.journal_invocation_anchor(custody.wire(record) + custody.wire(other),
            unit=unit, boot_id=boot, owner_uid=os.geteuid(), kernel={"timed_out": False, "returncode": 0})
        assert result["status"] == "UNKNOWN"


def test_generic_invocation_without_unit_or_cgroup_keeps_errno_but_never_qualifies_root():
    unit, boot = custody.unit_name("a" * 32, guard=True), "b" * 32
    record = invocation_record(boot)
    raw = custody.wire(record) + custody.wire(pid1_exit_record(unit, boot))
    result = custody.journal_invocation_origin(raw, unit=unit, boot_id=boot, invocation_id="c" * 32,
        kernel={"timed_out": False, "returncode": 0})
    assert result["status"] == "SCOPED_INVOCATION_DIAGNOSTIC_ONLY" and result["record_count"] == 2
    assert result["ROOT_FIN_claimed"] is result["ROOT_custody_qualified"] is False


@pytest.mark.parametrize("mutation", [None, "exe", "source", "unit", "cgroup", "invocation", "cached_invocation"])
def test_anchored_executor_cached_init_scope_is_diagnostic_only(mutation):
    # Source2afd's own invocation was journaled with cached init.scope metadata.
    # It can supply diagnosis, never ROOT identity, custody, FIN or admission.
    unit, boot = custody.unit_name("a" * 32, guard=True), "b" * 32
    record = {**invocation_record(boot), "_SYSTEMD_UNIT": "init.scope", "_SYSTEMD_CGROUP": "/init.scope"}
    if mutation == "exe": record["_EXE"] = "/usr/bin/foreign"
    elif mutation == "source": record["CODE_FILE"] = "src/core/foreign.c"
    elif mutation == "unit": record["UNIT"] = "foreign.service"
    elif mutation == "cgroup": record["_SYSTEMD_CGROUP"] = "/foreign"
    elif mutation == "invocation": record["INVOCATION_ID"] = "d" * 32
    elif mutation == "cached_invocation": record["_SYSTEMD_INVOCATION_ID"] = "d" * 32
    raw = custody.wire(record)
    result = custody.journal_invocation_origin(raw, unit=unit, boot_id=boot, invocation_id="c" * 32,
        kernel={"timed_out": False, "returncode": 0})
    assert result["status"] == ("UNKNOWN" if mutation else "SCOPED_INVOCATION_DIAGNOSTIC_ONLY")
    assert result["ROOT_FIN_claimed"] is result["ROOT_custody_qualified"] is False
    with pytest.raises(ValueError, match="ACTUAL_IN_PROCESS_WITNESS_REQUIRED"):
        custody.validate_witness(result, binding=binding())


@pytest.mark.parametrize("field,value", [("_BOOT_ID", "d" * 32), ("_UID", "1001"),
    ("INVOCATION_ID", "d" * 32), ("UNIT", "foreign.service"), ("_SYSTEMD_UNIT", "foreign.service"),
    ("_SYSTEMD_CGROUP", "/foreign"), ("_PID", True), ("_PID", "0"),
    ("CODE_FILE", "src/../foreign.c"), ("CODE_FUNC", "not function"), ("CODE_LINE", "bad"),
    ("_EXE", ["/usr/lib/systemd/systemd-executor"]), ("_EXE", "relative"), ("MESSAGE", ["duplicate"]),
    ("ERRNO", "bad"), ("ERRNO", "0"), ("ERRNO", "4096"), ("_SYSTEMD_INVOCATION_ID", "d" * 32)])
def test_invocation_rejects_foreign_or_malformed_metadata(field, value):
    unit, boot = custody.unit_name("a" * 32, guard=True), "b" * 32
    record = invocation_record(boot)
    record[field] = value
    result = custody.journal_invocation_origin(custody.wire(record), unit=unit, boot_id=boot,
        invocation_id="c" * 32, kernel={"timed_out": False, "returncode": 0})
    assert result["status"] == "UNKNOWN" and result["ROOT_custody_qualified"] is False


@pytest.mark.parametrize("kind", ["empty", "red", "timeout", "rows", "bytes", "duplicate_json"])
def test_invocation_unknown_bounds_and_red_query_do_not_invent_cause(kind):
    unit, boot = custody.unit_name("a" * 32, guard=True), "b" * 32
    raw, kernel = custody.wire(invocation_record(boot)), {"timed_out": False, "returncode": 0}
    if kind == "empty": raw = b""
    elif kind == "red": kernel["returncode"] = 1
    elif kind == "timeout": kernel["timed_out"] = True
    elif kind == "rows": raw *= 31
    elif kind == "bytes": raw = custody.wire({**invocation_record(boot), "MESSAGE": "x" * custody.CONTROL_BOUND})
    else: raw = raw.replace(b'"_UID":"0"', b'"_UID":"0","_UID":"0"')
    result = custody.journal_invocation_origin(raw, unit=unit, boot_id=boot, invocation_id="c" * 32, kernel=kernel)
    assert result["status"] == "UNKNOWN" and result["ROOT_FIN_claimed"] is False


@pytest.mark.parametrize("scenario", ["valid", "unknown_anchor", "wrong_client_exit", "secondary_red",
                                     "secondary_failure", "primary_changed", "anchor_changed"])
def test_failure_invocation_capture_has_original_nonroot_fin_and_retains_primary_error(tmp_path, monkeypatch, scenario):
    from scripts import rc6_authenticated_fixture_lifecycle as owned
    namespace = owned.create_namespace(tmp_path, binding())
    unit = custody.unit_name(namespace.nonce, guard=True)
    boot = custody._proc_text("/proc/sys/kernel/random/boot_id").strip().replace("-", "")
    client_exit = 73 if scenario == "wrong_client_exit" else 226
    kernel, fin = owned.execute_owned(namespace, [sys.executable, "-I", "-B", "-c",
        "import sys;sys.stderr.write('ORIGINAL_STDERR');sys.exit(" + str(client_exit) + ")"],
        cwd=custody.ROOT, environ={"PATH": "/usr/bin:/bin"}, progress=None, timeout_seconds=5,
        log_relative="guardian-native.log", fin_label="root-guardian-proof")
    primary_record = pid1_exit_record(unit, boot)
    if scenario == "unknown_anchor": primary_record.pop("INVOCATION_ID")
    primary_raw, secondary_raw = custody.wire(primary_record), custody.wire(invocation_record(boot))
    actual_execute, actual_preserve = owned.execute_owned, custody._preserve_journal
    secondary_requests = []
    def decoder_query(ns, requested, **kwargs):
        assert kwargs["timeout_seconds"] == 5 and "sudo" not in requested
        if "--journal-invocation" in requested:
            secondary_requests.append(requested)
            assert requested[-2:] == ["--journal-invocation", "c" * 32]
            if scenario == "secondary_failure": raise ValueError("OWN_SECONDARY_FIXTURE_FAILURE")
            raw, exit_status = secondary_raw, 1 if scenario == "secondary_red" else 0
        elif "--journal-unit" in requested:
            raw, exit_status = primary_raw, 0
        else:
            assert requested[:2] == ["systemctl", "show"] and requested[-1] == unit
            raw, exit_status = b"DECODER_OWN_INACTIVE_UNIT\n", 0
        return actual_execute(ns, [sys.executable, "-I", "-B", "-c",
            "import sys;sys.stdout.buffer.write(" + repr(raw) + ");sys.exit(" + str(exit_status) + ")"], **kwargs)
    def digest_change(ns, requested_unit, output, requested_boot, invocation_id=None):
        result = actual_preserve(ns, requested_unit, output, requested_boot, invocation_id)
        if invocation_id is None and scenario == "primary_changed":
            (ns.path / "diagnostic-journal-query.log").write_bytes(primary_raw + b"\n")
        if invocation_id is None and scenario == "anchor_changed":
            result["invocation_anchor"]["invocation_id"] = "d" * 32
        return result
    monkeypatch.setattr(owned, "execute_owned", decoder_query)
    monkeypatch.setattr(custody, "_preserve_journal", digest_change)
    # Only the pure unit/cgroup decoder is substituted. No ROOT/systemd unit
    # runs: all bytes and actual FIN come from own NONROOT fixture processes.
    monkeypatch.setattr(custody, "_inactive_unit_readback", lambda *args: {
        "unit": unit, "scope": "DECODER_FIXTURE_ONLY", "cgroup_state": "DECODER_FIXTURE_EMPTY"})
    monkeypatch.setattr(custody, "_cgroup_writer_absence", lambda *args: "DECODER_FIXTURE_EMPTY")
    monkeypatch.setattr(owned, "cleanup_namespace", lambda *args: pytest.fail("Diagnostic cannot cleanup"))
    output = tmp_path / "diagnostic"
    result = custody._preserve_failure({"namespace": namespace, "kernel": kernel, "fin": fin,
        "unit": unit, "boot_id": boot, "log": "guardian-native.log"}, output,
        ValueError("ROOT_CUSTODY_ACTUAL_CONTROLLER_PIDFD_REQUIRED"))
    assert result["error_signature"] == "ROOT_CUSTODY_ACTUAL_CONTROLLER_PIDFD_REQUIRED"
    assert result["ROOT_FIN"] == "UNKNOWN" and result["ROOT_custody_qualified"] is False
    assert result["cleanup_authorized"] is False and result["reservation_recovery_credited_bytes"] == 0
    assert namespace.path.is_dir() and (output / "journal-query-original.log").read_bytes() == primary_raw
    assert result["journal"]["raw_sha256"] == hashlib.sha256(primary_raw).hexdigest()
    capture = Path(result["diagnostic_capture"])
    manifest = json.loads((capture / "manifest.json").read_bytes())
    files = {row["relative_source"]: row for row in manifest["files"]}
    selected = scenario in ("valid", "secondary_red", "secondary_failure")
    assert bool(secondary_requests) is selected
    if scenario in ("valid", "secondary_red"):
        secondary = result["journal_invocation"]
        assert (output / "journal-invocation-query-original.log").read_bytes() == secondary_raw
        assert (output / "journal-invocation-fin-original.json").exists()
        assert secondary["raw_sha256"] == hashlib.sha256(secondary_raw).hexdigest()
        assert secondary["kernel"]["actual_child_reaped"] and secondary["kernel"]["owned_children_exhaustion_verified"]
        assert secondary["kernel"]["sigint_failure_grace_seconds"] == 0
        row = files["diagnostic-journal-invocation-query.log"]
        assert (capture / row["capture_file"]).read_bytes() == secondary_raw
        assert row["sha256"] == secondary["raw_sha256"]
        assert "producer-owned-fin-diagnostic-journal-invocation-query.json" in files
        expected = "UNKNOWN" if scenario == "secondary_red" else "SCOPED_INVOCATION_DIAGNOSTIC_ONLY"
        assert secondary["origin"]["status"] == expected
    else:
        assert result["journal_invocation"]["status"] == "UNKNOWN"
        assert "diagnostic-journal-invocation-query.log" not in files
    assert "producer-owned-fin-diagnostic-journal-query.json" in files


def test_journal_origin_accepts_exact_boot_pid1_unit_and_own_unit_only():
    unit, boot = custody.unit_name("a" * 32, guard=True), "b" * 32
    records = [{"_BOOT_ID": boot, "_PID": "1", "_UID": "0", "UNIT": unit, "MESSAGE": "EXIT_NAMESPACE"},
        {"_BOOT_ID": boot, "_PID": "42", "_UID": "0", "_SYSTEMD_UNIT": unit,
         "_SYSTEMD_CGROUP": "/system.slice/" + unit, "MESSAGE": "Failed mount"}]
    raw = b"".join(custody.wire(record) for record in records)
    result = custody.journal_origin(raw, unit=unit, boot_id=boot, owner_uid=os.geteuid(),
        kernel={"timed_out": False, "returncode": 0})
    assert result["status"] == "SCOPED_JOURNAL_OBSERVATION_ONLY"
    assert result["origins"] == ["PID1_UNIT", "OWN_UNIT"] and result["record_count"] == 2
    assert result["ROOT_FIN_claimed"] is False and result["ROOT_custody_qualified"] is False


def test_pre_cgroup_root_executor_journal_is_diagnostic_only():
    unit, boot = custody.unit_name("a" * 32, guard=True), "b" * 32
    record = {"_BOOT_ID": boot, "_PID": "42", "_UID": "0", "UNIT": unit,
        "_SYSTEMD_UNIT": "init.scope", "_SYSTEMD_CGROUP": "/init.scope", "SYSLOG_IDENTIFIER": "(sh)",
        "CODE_FILE": "src/core/exec-invoke.c", "CODE_FUNC": "exec_invoke", "CODE_LINE": "4670",
        "ERRNO": "13", "MESSAGE": "Failed to set up mount namespacing: /owned/test: Permission denied"}
    result = custody.journal_origin(custody.wire(record), unit=unit, boot_id=boot, owner_uid=os.geteuid(),
        kernel={"timed_out": False, "returncode": 0})
    assert result["status"] == "SCOPED_JOURNAL_OBSERVATION_ONLY"
    assert result["origins"] == ["ROOT_UNIT_FIELD_DIAGNOSTIC_ONLY"]
    assert result["ROOT_FIN_claimed"] is False and result["ROOT_custody_qualified"] is False
    with pytest.raises(ValueError, match="ROOT_CUSTODY_ACTUAL_IN_PROCESS_WITNESS_REQUIRED"):
        custody.validate_witness(result, binding=binding())


@pytest.mark.parametrize("field,value", [("_BOOT_ID", "c" * 32), ("_BOOT_ID", True),
    ("_UID", "1001"), ("UNIT", "foreign.service"), ("_PID", "0"), ("_PID", 42),
    ("CODE_FILE", "foreign.c"), ("CODE_FUNC", "not a function"), ("CODE_LINE", "unknown")])
def test_pre_cgroup_executor_unknown_origin_never_becomes_a_root_diagnostic(field, value):
    unit, boot = custody.unit_name("a" * 32, guard=True), "b" * 32
    record = {"_BOOT_ID": boot, "_PID": "42", "_UID": "0", "UNIT": unit, "_SYSTEMD_UNIT": "init.scope",
        "CODE_FILE": "src/core/exec-invoke.c", "CODE_FUNC": "exec_invoke", "CODE_LINE": "4670", field: value}
    result = custody.journal_origin(custody.wire(record), unit=unit, boot_id=boot, owner_uid=os.geteuid(),
        kernel={"timed_out": False, "returncode": 0})
    assert result["status"] == "UNKNOWN" and result["ROOT_FIN_claimed"] is False
    assert result["ROOT_custody_qualified"] is False


@pytest.mark.parametrize("field,value", [("_BOOT_ID", "c" * 32), ("_UID", "999999"),
    ("UNIT", "foreign.service"), ("_PID", "42")])
def test_foreign_pid1_journal_record_is_unknown(field, value):
    unit, boot = custody.unit_name("a" * 32, guard=True), "b" * 32
    record = {"_BOOT_ID": boot, "_PID": "1", "_UID": "0", "UNIT": unit, field: value}
    result = custody.journal_origin(custody.wire(record), unit=unit, boot_id=boot, owner_uid=os.geteuid(),
        kernel={"timed_out": False, "returncode": 0})
    assert result["status"] == "UNKNOWN" and result["ROOT_custody_qualified"] is False


@pytest.mark.parametrize("raw,kernel,reason", [(b"", {"timed_out": False, "returncode": 0}, "JOURNAL_EMPTY"),
    (b"permission denied", {"timed_out": False, "returncode": 1}, "JOURNAL_QUERY_RED"),
    (b"", {"timed_out": True, "returncode": 0}, "JOURNAL_QUERY_RED")])
def test_empty_red_or_timed_out_journal_never_invents_rca(raw, kernel, reason):
    result = custody.journal_origin(raw, unit=custody.unit_name("a" * 32), boot_id="b" * 32,
        owner_uid=os.geteuid(), kernel=kernel)
    assert result["status"] == "UNKNOWN" and result["reason"] == reason
    assert result["ROOT_FIN_claimed"] is False


def test_nonroot_journal_worker_has_actual_boot_binding_and_hard_log_size_cap(tmp_path):
    from scripts import rc6_authenticated_fixture_lifecycle as owned
    namespace = owned.create_namespace(tmp_path, binding())
    unit = custody.unit_name(namespace.nonce)
    boot = custody._proc_text("/proc/sys/kernel/random/boot_id").strip().replace("-", "")
    # Replace only the read-only journal executable in this NONROOT child.
    # The shipped worker still establishes the real rlimit before exec.
    program = ("import sys,resource;sys.path.insert(0," + repr(str(custody.ROOT)) + ");"
        "from scripts import rc6_privileged_custody as c;"
        "c.journal_command=lambda *args:[sys.executable,'-I','-B','-c',"
        + repr("import resource,json;print(json.dumps({'limit':resource.getrlimit(resource.RLIMIT_FSIZE)}))") + "];"
        "c.journal_query_worker(" + repr(unit) + "," + repr(boot) + ")")
    kernel, fin = owned.execute_owned(namespace, [sys.executable, "-I", "-B", "-c", program], cwd=custody.ROOT,
        environ={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, progress=None, timeout_seconds=5,
        log_relative="journal-rlimit.json", fin_label="own-nonroot-journal-rlimit")
    assert owned._phase_green(fin) and kernel["actual_child_reaped"]
    assert json.loads(calibration.read(namespace.path / "journal-rlimit.json"))["limit"] == [custody.CONTROL_BOUND] * 2


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
