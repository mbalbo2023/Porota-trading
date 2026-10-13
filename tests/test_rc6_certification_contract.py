"""Bounded economic counterexamples, no ROOT actors or calibration workloads."""
import ctypes
import errno
import os
import struct

import pytest

from scripts import rc6_capacity_calibration as calibration
from scripts import rc6_certification_contract as contract


def proposal_storage(**changes):
    return contract.evaluate_proposed_storage(**{
        "free_bytes": 40 * contract.GIB, "total_inodes": 1000, "free_inodes": 100,
        "allocation_unit_bytes": 4096, "incremental_metadata_bound_bytes": 1024**2, **changes})


def test_direct_quota_proposal_does_not_discard_the_inner_loop_reserve_or_rewrite_limits():
    historic = calibration.limits_for("bootstrap")
    actual = proposal_storage()
    # Replacing 26 GiB with a 20 GiB quota and just the exterior 4 GiB would
    # silently remove require_physical_and_project_capacity's interior reserve.
    assert actual["independent_reserve_bytes"] == 8 * contract.GIB
    assert actual["maximum_arithmetic_saving_bytes"] == 2 * contract.GIB - 2 * 1024**2
    assert actual["reserves_merged"] is False
    assert calibration.limits_for("bootstrap") == historic
    proposal = contract.zero_cost_contract()
    assert proposal["proposed_storage"]["project_hard_limit_bytes"] == historic["project_hard_limit_bytes"]
    assert proposal["proposed_storage"]["project_hard_inodes"] == calibration.MAX_ENTRIES == 100000
    assert proposal["proposed_storage"]["separate_EDQUOT_probe_hard_limit_bytes"] == calibration.MIB
    assert proposal["proposed_storage"]["separate_EDQUOT_probe_hard_inodes"] == calibration.MAX_ENTRIES
    assert proposal["privileged_signal_custody"] == dict(calibration.PRIVILEGED_SIGNAL_CUSTODY)
    assert proposal["recurring_additional_cost_usd"] == 0


def test_physical_measurement_above_nominal_14_gb_is_eligible_for_arithmetic_only():
    actual = proposal_storage(free_bytes=91_698_458_624)
    assert actual["status"] == "ARITHMETIC_FEASIBLE_PENDING_PROOF"
    assert actual["nominal_storage_bytes"] == 14 * 1000**3
    assert actual["nominal_storage_guarantees_necessary_floor"] is False
    assert actual["nominal_storage_is_launch_ceiling"] is actual["platform_impossibility_claimed"] is False
    assert actual["launch_authorized"] is actual["equivalence_proved"] is actual["G0_GREEN_claimed"] is False
    assert actual["metadata_bound_authenticated"] is actual["physical_reservation_equivalence_proved"] is False
    assert "ROOT_SIGNAL_AND_REAP_CUSTODY_FROM_FIRST_INSTRUCTION" in actual["remaining_native_proof_obligations"]
    assert "ALL_WRITERS_CONFINED_OR_AUTHENTICALLY_BOUNDED" in actual["remaining_native_proof_obligations"]


def test_unknown_metadata_blocks_even_with_an_abundant_disk():
    actual = proposal_storage(free_bytes=100 * contract.GIB, incremental_metadata_bound_bytes=None)
    assert actual["storage_blockers"] == ["CERTIFICATION_INCREMENTAL_METADATA_BOUND_UNKNOWN"]
    assert actual["required_minimum_bytes"] is actual["maximum_arithmetic_saving_bytes"] is None
    assert actual["status"] == "BLOCKED_NECESSARY_STORAGE"


def test_exact_physical_and_inode_boundaries_are_independent():
    minimum = proposal_storage()["required_minimum_bytes"]
    for delta, inodes in ((-1, 100), (0, 99), (0, 100)):
        actual = proposal_storage(free_bytes=minimum + delta, free_inodes=inodes)
        assert ("CERTIFICATION_MEASURED_PHYSICAL_BYTES_INSUFFICIENT" in actual["storage_blockers"]) == (delta < 0)
        assert ("CERTIFICATION_INODE_RESERVE_INSUFFICIENT" in actual["storage_blockers"]) == (inodes < 100)
        assert actual["launch_authorized"] is False


@pytest.mark.parametrize("field,bad", [("free_bytes", True), ("free_inodes", -1),
    ("total_inodes", 0), ("incremental_metadata_bound_bytes", 0.5),
    ("incremental_metadata_bound_bytes", 2**63 - 1), ("free_inodes", 1001)])
def test_untrusted_arithmetic_cannot_use_bool_float_negative_overflow_or_impossible_counts(field, bad):
    with pytest.raises(ValueError, match="CERTIFICATION_"):
        proposal_storage(**{field: bad})


def test_unknown_or_changed_historic_limits_cannot_be_normalized_into_a_new_contract(monkeypatch):
    monkeypatch.setattr(calibration, "limits_for", lambda _mode: {
        "backing_image_bytes": 25 * contract.GIB, "project_hard_limit_bytes": 20 * contract.GIB,
        "residual_reserve_bytes": 4 * contract.GIB})
    with pytest.raises(ValueError, match="HISTORIC_CALIBRATION_LIMITS_CHANGED"):
        contract.zero_cost_contract()


def test_readonly_observer_performs_no_quota_setting_mount_or_actor_launch(tmp_path, monkeypatch):
    calls = []
    def get_project(_fd, request, buffer, mutate):
        calls.append(request)
        assert request == calibration.FSGETXATTR and mutate is True
        buffer[:] = struct.pack("=5I8x", 0, 0, 0, 0, 0)
    monkeypatch.setattr(contract.fcntl, "ioctl", get_project)
    monkeypatch.setattr(contract, "_quota_readback", lambda _fd, identifier: {
        "status": "NO_VERIFICADO", "project_id": identifier, "reason": "CONTROLLED_UNKNOWN",
        "mutations_attempted": False, "enforcement_proved": False})
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Read-only quota observer must not start any actor")
    monkeypatch.setattr(calibration.subprocess, "run", forbidden)
    monkeypatch.setattr(calibration.subprocess, "Popen", forbidden)
    before = {key: getattr(tmp_path.stat(), key) for key in ("st_dev", "st_ino", "st_nlink", "st_mtime_ns", "st_ctime_ns")}
    actual = contract.readonly_quota_custody_observation(tmp_path)
    after = {key: getattr(tmp_path.stat(), key) for key in before}
    assert before == after and list(tmp_path.iterdir()) == []
    assert calls == [calibration.FSGETXATTR]
    assert actual["status"] == "NO_VERIFICADO" and actual["observer_errors"] == []
    assert actual["quota"]["reason"] == "CONTROLLED_UNKNOWN"
    assert actual["mount"]["directory_inode"] == tmp_path.stat().st_ino
    assert actual["project"]["statfs_may_be_quota_projected"] is False
    assert actual["observer_process"]["uids"][1] == os.geteuid()
    assert actual["observer_process"]["root_actor_signal_or_FIN_test"] == "NOT_CALLED"
    assert actual["native_quota_enforcement_proved"] is actual["privileged_signal_custody_proved"] is False
    assert actual["launch_authorized"] is actual["FIN_proved"] is False


@pytest.mark.parametrize("identifier,flags", [(1, 0), (0, calibration.PROJINHERIT), (1, calibration.PROJINHERIT)])
def test_projected_statfs_view_cannot_be_used_as_unprojected_physical_capacity(tmp_path, monkeypatch, identifier, flags):
    def get_project(_fd, request, buffer, _mutate):
        assert request == calibration.FSGETXATTR
        buffer[:] = struct.pack("=5I8x", flags, 0, 0, identifier, 0)
    monkeypatch.setattr(contract.fcntl, "ioctl", get_project)
    monkeypatch.setattr(contract, "_quota_readback", lambda *_: {"status": "READBACK_METADATA_ONLY"})
    actual = contract.readonly_quota_custody_observation(tmp_path)
    assert actual["project"]["statfs_may_be_quota_projected"] is True
    assert actual["native_quota_enforcement_proved"] is actual["launch_authorized"] is False


def test_denied_project_metadata_is_unknown_instead_of_inventing_no_quota(tmp_path, monkeypatch):
    def denied(*_args):
        raise PermissionError(errno.EPERM, "Controlled FSGETXATTR denial")
    monkeypatch.setattr(contract.fcntl, "ioctl", denied)
    actual = contract.readonly_quota_custody_observation(tmp_path)
    assert actual["project"] is actual["quota"] is None
    assert actual["observer_errors"][0]["errno"] == errno.EPERM
    assert actual["status"] == "NO_VERIFICADO"
    assert actual["platform_impossibility_claimed"] is actual["launch_authorized"] is False


def test_quota_readback_issues_only_get_commands_and_cannot_claim_enforcement(monkeypatch):
    class ReadonlyQuota:
        def __init__(self):
            self.calls = []
        def __call__(self, descriptor, command, identifier, payload):
            self.calls.append((descriptor, command, identifier))
            if command == (calibration.Q_GETFMT << 8) | calibration.PRJQUOTA:
                ctypes.cast(payload, ctypes.POINTER(ctypes.c_uint32)).contents.value = calibration.FORMAT_VFS_V1
            elif command == (calibration.Q_GETQUOTA << 8) | calibration.PRJQUOTA:
                value = ctypes.cast(payload, ctypes.POINTER(calibration.Dqblk)).contents
                value.bhard = value.bsoft = 20 * contract.GIB // 1024
                value.ihard = value.isoft = 100000
            else:
                raise AssertionError("A mutating quota command is forbidden")
            return 0
    function = ReadonlyQuota()
    class Libc:
        quotactl_fd = function
    monkeypatch.setattr(contract.ctypes, "CDLL", lambda *_args, **_kwargs: Libc())
    actual = contract._quota_readback(77, 42)
    assert [call[1] >> 8 for call in function.calls] == [calibration.Q_GETFMT, calibration.Q_GETQUOTA]
    assert actual["hard_bytes"] == 20 * contract.GIB and actual["hard_inodes"] == 100000
    assert actual["status"] == "READBACK_METADATA_ONLY"
    assert actual["mutations_attempted"] is actual["enforcement_proved"] is False


def test_readback_absent_libc_symbol_is_explicit_unknown(monkeypatch):
    monkeypatch.setattr(contract.ctypes, "CDLL", lambda *_args, **_kwargs: object())
    actual = contract._quota_readback(77, 42)
    assert actual["status"] == "NO_VERIFICADO"
    assert actual["reason"] == "QUOTACTL_FD_READ_OBSERVER_UNAVAILABLE"
    assert actual["mutations_attempted"] is actual["enforcement_proved"] is False
