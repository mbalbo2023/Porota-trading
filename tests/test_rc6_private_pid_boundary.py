"""Cheap captured-data attacks; no ROOT/unit/quota/native qualification."""
from __future__ import annotations

import copy
import errno
import os
import stat
from types import SimpleNamespace

import pytest

from scripts import rc6_private_pid_boundary as boundary
from scripts import rc6_trusted_quota_setup as trusted
from scripts import rc6_capacity_calibration as calibration


@pytest.fixture
def captured():
    binding = {"candidate_sha": "a" * 40, "candidate_tree": "b" * 40,
               "producer": "capacity-probe", "owner_id": "DECODER_ONLY"}
    issuer = {"pid": 4000, "start_ticks": "100", "boot_id": "11111111-1111-1111-1111-111111111111",
              "pid_namespace_inode": 10}
    request = {"issuer": issuer, "binding": binding, "owner_uid": 1001, "owner_gid": 1001,
        "host_boundary": {"mount_namespace_inode": 11}, "boundary_host_proc_device": 12,
        "namespace_receipt": {"identity": [33, 44, 1001, 1001, 0o700],
                              "mount_id": 55, "marker_sha256": "c" * 64}}
    actor = {"pid": 8, "parent_pid": 1, "start_ticks": "200", "pid_namespace_inode": 20,
             "mount_namespace_inode": 21, "namespace_pids": [8], "uids": [1001] * 4,
             "gids": [1001] * 4, "cgroup": "0::/system.slice/rc6-native-decoder.service",
             "capabilities": dict.fromkeys(("CapEff", "CapPrm", "CapBnd", "CapInh", "CapAmb"), 0),
             "supplementary_groups": [], "no_new_privileges": 1, "seccomp_mode": 2, "seccomp_filters": 3}
    guardian = {"pid": 4001, "parent_pid": 1, "pid_namespace_inode": 10,
                "uids": [0] * 4, "cgroup": actor["cgroup"]}
    host_actor = {**actor, "pid": 4002, "namespace_pids": [4002, 8]}
    subject = {**issuer, "uids": [1001] * 4, "gids": [1001] * 4, "namespace_pids": [4000]}
    rows = [{"schema": boundary.GUARD_SCHEMA, "stage": stage, "challenge_sha256": digit * 64,
             "nonce": digit * 64, "binding": binding, "request_sha256": boundary.digest(boundary.wire(request)),
             "issuer_actual": copy.deepcopy(subject), "guardian_actual": guardian,
             "host_actor_actual": host_actor, "subject_pidfd_alive": True, "actor_pidfd_alive": True,
             "host_proc_device": 12, "monotonic_ns": index + 1}
            for index, (stage, digit) in enumerate((("before", "1"), ("after", "2")))]
    fds = [{"descriptor": number, "device": 99, "inode": number + 1, "rdevice": os.makedev(1, 3),
            "mode": stat.S_IFCHR if number == 0 else stat.S_IFREG, "flags": os.O_RDONLY if number == 0 else os.O_WRONLY}
           for number in range(3)]
    view = {"proc_device": 22, "proc_self": "8", "raw_sha256": "d" * 64,
            "rows": [{"id": 1, "root": "/", "target": "/", "source": "/dev/root", "type": "ext4",
                      "options": ["ro"], "propagation": []},
                     {"id": 2, "root": "/", "target": "/proc", "source": "proc", "type": "proc",
                      "options": ["ro"], "propagation": []}]}
    denials = {name: {"errno": errno.EACCES, "write_performed": False,
                      "target_existed": name in ("foreign_kernel", "foreign_cgroup")}
               for name in ("foreign_kernel", "foreign_cgroup", "local_root_root", "local_root_cwd",
                            "local_root_fd", "local_root_ns", "local_root_signal")}
    native = [{"case": case, "status": "REJECTED", "scope": "ACTUAL_KERNEL_CONTROL",
               "qualification_claimed": False, "error": boundary.NATIVE_CONTROL_ERRORS[case],
               **({"actual_waitpid": True, "actual_exited_pidfd": True} if case == "lost_guardian" else {})}
              for case in boundary.NATIVE_NEGATIVES]
    bridge = {"identity": request["namespace_receipt"]["identity"], "original_mount_id": 55,
              "marker_sha256": "c" * 64, "foreign_host_proc_exposed": False}
    value = {"schema": boundary.SCHEMA, "revision": boundary.REVISION,
        "contract_sha256": boundary.contract_sha256(), "scope": "SMALL_NATIVE_OBSERVATION_ONLY",
        "binding": binding, "request_sha256": boundary.digest(boundary.wire(request)),
        "actor": actor, "host_boundary": {"pid_namespace_inode": 10, "mount_namespace_inode": 11, "proc_device": 12},
        "mount_view": view, "descriptors": fds, "external_before": copy.deepcopy(rows[0]),
        "external_after": copy.deepcopy(rows[1]),
        "hidden_subject": {"host_pid": 4000, "local_pid_visible": False, "signal_zero_errno": errno.ESRCH,
            "magiclinks": {name: {"errno": errno.ENOENT} for name in boundary.MAGICLINKS}},
        "existing_denials": denials, "native_controls": native[2:],
        "qualification_claimed": False, "G0_GREEN": False, "real_orders_sent": 0}
    return value, {"request": request, "guardian_rows": rows, "native_pre_controls": native[:2], "bridge": bridge}


def test_complete_decoder_closure_does_not_grant_in_process_native_authority(captured):
    value, arguments = captured
    assert boundary.validate_observation(value, **arguments) is value
    with pytest.raises(ValueError, match="ROOT_CUSTODY_ACTUAL_IN_PROCESS_WITNESS_REQUIRED"):
        boundary.boundary_witness(value, binding=arguments["request"]["binding"])
    assert value["G0_GREEN"] is value["qualification_claimed"] is False


def test_all_ten_adversarial_guards_reject_without_simulating_native_syscalls(captured):
    value, arguments = captured
    rows = boundary.adversarial_rejections(value, **arguments)
    assert [row["case"] for row in rows] == list(boundary.NEGATIVES)
    assert all(row["status"] == "REJECTED" and row["scope"] == "CAPTURED_EVIDENCE_ATTACK"
               and row["native_result_simulated"] is False for row in rows)
    subjects = [row for row in rows if row["case"] in ("arbitrary_absent_pid", "recycled_birth")]
    assert all(row["error"] == "PIDV2_ABSENT_ARBITRARY_OR_RECYCLED_SUBJECT" for row in subjects)


@pytest.mark.parametrize("defect,expected", [("no_guard", "PIDV2_LIVE_GUARDIAN_ACK_ABSENT"),
    ("no_existing_targets", "PIDV2_EXISTING_TARGET_NATIVE_DENIAL_REQUIRED"),
    ("no_negative_matrix", "PIDV2_NATIVE_NEGATIVE_MATRIX_INCOMPLETE"),
    ("collision", "PIDV2_HOST_LOCAL_PID_COLLISION"),
    ("host_proc_alias", "PIDV2_HOST_PROCFS_MOUNT_OR_ALIAS"),
    ("shared_mount", "PIDV2_SHARED_PROPAGATION"),
    ("replayed_ack", "PIDV2_BIRTH_OR_ACK_REPLAY")])
def test_enoent_cannot_replace_any_other_boundary_property(captured, defect, expected):
    value, arguments = captured
    if defect == "no_guard": arguments["guardian_rows"] = []
    elif defect == "no_existing_targets": value["existing_denials"] = {}
    elif defect == "no_negative_matrix": value["native_controls"] = []
    elif defect == "collision": value["hidden_subject"]["local_pid_visible"] = True
    elif defect == "host_proc_alias":
        value["mount_view"]["rows"].append({**value["mount_view"]["rows"][1], "id": 3, "target": "/host-proc"})
    elif defect == "shared_mount": value["mount_view"]["rows"][0]["propagation"] = ["shared:1"]
    elif defect == "replayed_ack":
        arguments["guardian_rows"][1]["nonce"] = arguments["guardian_rows"][0]["nonce"]
        value["external_after"]["nonce"] = value["external_before"]["nonce"]
    with pytest.raises(ValueError, match=expected):
        boundary.validate_observation(value, **arguments)


@pytest.mark.parametrize("payload", [None, {}, {"status": "NATIVE_OBSERVATION_PROVED"},
    {"privileged_launch_authorized": True}, {"original_PROC_EACCES_equivalence_approved": True}])
def test_serialized_or_boolean_boundary_cannot_authorize_backing(payload):
    with pytest.raises(ValueError, match="PIDV2_SYNTHETIC_WITNESS_FORBIDDEN"):
        boundary.BoundaryWitness(object(), payload, None, {})


@pytest.mark.parametrize("scope,size,inodes", [("capacity-probe", 512 * calibration.MIB, 100000),
    ("capacity-calibration", 20 * calibration.GIB, 100000),
    ("G5A_EVIDENCE_ACQUISITION_ONLY", 512 * calibration.MIB, 32768)])
def test_quota_specs_bind_original_gate_limits_without_launch_authority(scope, size, inodes):
    assert trusted.validate_project_spec(hard_bytes=size, hard_inodes=inodes, operation_scope=scope) == {
        "hard_bytes": size, "hard_inodes": inodes, "operation_scope": scope}
    with pytest.raises(ValueError, match="TRUSTED_QUOTA_SCOPE_OR_LIMIT_REBOUND"):
        trusted.validate_project_spec(hard_bytes=size + 1024, hard_inodes=inodes, operation_scope=scope)
    with pytest.raises(ValueError, match="TRUSTED_QUOTA_SCOPE_OR_LIMIT_REBOUND"):
        trusted.validate_project_spec(hard_bytes=size, hard_inodes=inodes + 1, operation_scope=scope)


@pytest.mark.parametrize("bad", [True, 0, -1, "536870912", 536870912.0])
def test_quota_setup_rejects_noninteger_or_unbounded_units(bad):
    with pytest.raises(ValueError, match="TRUSTED_QUOTA_EXACT_UNITS_REQUIRED"):
        trusted.validate_project_spec(hard_bytes=bad, hard_inodes=100000, operation_scope="capacity-probe")


def test_setup_seal_nonroot_fin_are_distinct_ordered_obligations():
    phases = [{"phase": phase, "qualification_claimed": False} for phase in trusted.PHASES]
    trusted.validate_phase_sequence(phases, complete=True)
    trusted.validate_phase_sequence(phases[:2], complete=False)
    with pytest.raises(ValueError, match="TRUSTED_QUOTA_PHASE_ORDER_REQUIRED"):
        trusted.validate_phase_sequence([phases[1], phases[0], *phases[2:]], complete=True)
    with pytest.raises(ValueError, match="TRUSTED_QUOTA_PHASE_ORDER_REQUIRED"):
        trusted.validate_phase_sequence(phases[:-1], complete=True)


def test_native_setup_receipt_cannot_be_minted_from_format_metadata_alone():
    with pytest.raises(ValueError, match="TRUSTED_QUOTA_NATIVE_SETUP_REQUIRED"):
        trusted.setup_receipt({"setup_stage": "trusted_quota_setup_complete", "quota_v2_already_live": True})


def test_contract_proposes_single_specific_replacement_and_retains_original_supervisor_eacces():
    spec = boundary.contract()
    assert spec["owner_acceptance_required"] is True and spec["ENOENT_alone_accepted"] is False
    assert spec["preserved_assertion"] == "quota_probe:/proc/LOCAL_SUPERVISOR/fd/1:EACCES"
    assert spec["replaced_assertion"] == "issuer_root_escape_probe:/proc/HOST_PID/root:EACCES"
    assert spec["observation_grants_G0"] is False and spec["capability_before_bootstrap"] is True
    assert spec["residual_reserve_bytes"] == 4 * calibration.GIB


@pytest.mark.parametrize("field,bad", [("capabilities", {}), ("capabilities", {"CapEff": 0}),
    ("pid_namespace_inode", True), ("mount_namespace_inode", 0)])
def test_captured_seal_requires_complete_typed_kernel_observations(captured, field, bad):
    value, arguments = captured
    value["actor"][field] = bad
    with pytest.raises(ValueError, match="PIDV2_(NONROOT_SEAL_REQUIRED|WRONG_NAMESPACE)"):
        boundary.validate_observation(value, **arguments)


@pytest.mark.parametrize("field", ["actual_waitpid", "actual_exited_pidfd"])
def test_lost_guardian_negative_requires_actual_closed_control_fin(captured, field):
    value, arguments = captured
    value["native_controls"][-1][field] = False
    with pytest.raises(ValueError, match="PIDV2_LOST_GUARDIAN_NATIVE_CONTROL_FIN_REQUIRED"):
        boundary.validate_observation(value, **arguments)


def test_quota_ancestor_log_exception_never_admits_another_fd_or_named_file(tmp_path, monkeypatch):
    """Descriptor decoder only; no ROOT install, filter, unit or quota."""
    from scripts import rc6_root_actor_seal as seal
    import fcntl
    path = tmp_path / "root-worker-native.log"
    path.write_bytes(b"CLOSED_DECODER_ONLY")
    path.chmod(0o600)
    held = path.lstat()
    details = {0: SimpleNamespace(st_dev=1, st_ino=2, st_mode=stat.S_IFCHR | 0o666, st_rdev=os.makedev(1, 3)),
               1: SimpleNamespace(st_dev=2, st_ino=3, st_mode=stat.S_IFIFO | 0o600, st_rdev=0),
               2: SimpleNamespace(st_dev=2, st_ino=3, st_mode=stat.S_IFIFO | 0o600, st_rdev=0), 7: held}
    fake_os = SimpleNamespace(**{**vars(os), "listdir": lambda _: [str(number) for number in details],
                                  "fstat": lambda fd: details[fd]})
    monkeypatch.setattr(seal, "os", fake_os)
    monkeypatch.setattr(calibration, "descriptor_mount_id", lambda _: 13)
    monkeypatch.setattr(fcntl, "fcntl", lambda *_: os.O_WRONLY)
    own = {"descriptor": 7, "identity": [held.st_dev, held.st_ino, held.st_uid, held.st_gid, held.st_mode],
           "mount_id": 13, "path": str(path)}
    with pytest.raises(ValueError, match="ROOT_SEAL_INHERITED_HOST_DESCRIPTOR_FORBIDDEN"):
        seal._descriptors()
    assert [row["descriptor"] for row in seal._descriptors(owned_fin_log=own)] == [0, 1, 2, 7]
    foreign = tmp_path / "foreign" / "root-worker-native.log"
    foreign.parent.mkdir()
    foreign.write_bytes(b"FOREIGN_DECODER_ONLY")
    with pytest.raises(ValueError, match="ROOT_SEAL_OWN_FIN_LOG_NAMED_REBOUND"):
        seal._descriptors(owned_fin_log={**own, "path": str(foreign)})
    with pytest.raises(ValueError, match="ROOT_SEAL_OWN_FIN_LOG_FD_REBOUND"):
        seal._descriptors(owned_fin_log={**own, "mount_id": 14})
    details[9] = held
    with pytest.raises(ValueError, match="ROOT_SEAL_INHERITED_HOST_DESCRIPTOR_FORBIDDEN"):
        seal._descriptors(owned_fin_log=own)


def test_approval_context_without_native_boundary_remains_legacy_blocked(monkeypatch):
    from scripts import rc6_privileged_custody as custody
    receipt = {"source_sha": "a" * 40, "source_tree": "b" * 40, "code_hashes": {}, "status": "DECODER_ONLY"}
    monkeypatch.setattr(custody, "validate_witness", lambda *args, **kwargs: receipt)
    result, observed, reason = calibration.calibration_custody(object(), binding={},
        source_sha="a" * 40, source_tree="b" * 40, code_hashes={}, revision_context=object())
    assert result["privileged_launch_authorized"] is False and observed is receipt
    assert reason == "CALIBRATION_PRIVATE_PID_ORIGINAL_PROC_ROOT_CONTRACT_REVIEW_REQUIRED"
def test_real_nonroot_denial_observes_regular_entry_without_following_target(tmp_path):
    assert os.geteuid()>0
    target=tmp_path/'own-denied-target';target.write_bytes(b'own bounded denial control')
    target.chmod(0)
    try:
        row=boundary._open_denial('own_denial',target,os.O_RDONLY,(errno.EACCES,))
        assert row['errno']==errno.EACCES and row['target_existed'] is True
        assert row['target_entry_observed'] is True and row['target_metadata_follows_links'] is False
        assert row['write_performed'] is False
    finally:target.chmod(0o600)


def test_real_nonroot_denied_parent_leaves_existence_unknown(tmp_path):
    assert os.geteuid()>0
    parent=tmp_path/'own-denied-parent';parent.mkdir(mode=0o700)
    target=parent/'actual-own-leaf';target.write_bytes(b'own existing leaf')
    parent.chmod(0)
    try:
        row=boundary._open_denial('own_parent',target,os.O_RDONLY,(errno.EACCES,))
        assert row['errno']==errno.EACCES and row['target_existed'] is None
        assert row['target_entry_observed'] is None and row['target_metadata_errno']==errno.EACCES
        assert row['target_metadata_follows_links'] is row['write_performed'] is False
    finally:parent.chmod(0o700)


def test_missing_entry_and_dangling_link_are_distinct_without_claiming_target(tmp_path):
    target=tmp_path/'missing';alias=tmp_path/'dangling';alias.symlink_to(target)
    missing=boundary._open_denial('missing',target,os.O_RDONLY,(errno.ENOENT,))
    linked=boundary._open_denial('dangling',alias,os.O_RDONLY,(errno.ENOENT,))
    assert missing['target_existed'] is missing['target_entry_observed'] is False
    assert linked['target_entry_observed'] is True and linked['target_existed'] is None
    assert linked['write_performed'] is False
