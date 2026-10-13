"""Pure/mocked Source orchestration; no loop, quota, mount or ROOT test runs."""
from __future__ import annotations

import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from scripts import rc6_capacity_calibration as calibration
from scripts import rc6_g5_contract as g5
from scripts import rc6_g5a_native_runner as runner
from scripts import rc6_privileged_custody as custody
from scripts import rc6_trusted_quota_setup as trusted


@pytest.mark.parametrize("root_request", ({}, {"schema": "porota.rc6.capacity-root-request.v1", "mode": "bootstrap"},
    {"schema": "porota.rc6.g5a-root-request.v1", "preparation": True}))
@pytest.mark.parametrize("preparation", (None, True, {}, {"accepted": True}))
def test_serialized_preparation_cannot_reach_any_privileged_setup(root_request, preparation, monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail("NO_PRIVILEGED_OPERATIONS_WITHOUT_TYPED_PREPARATION")
    for name in ("configure_loop", "mount_all_readonly", "mount_own_ext4", "run_setup", "drop_privileges"):
        monkeypatch.setattr(calibration, name, forbidden)
    monkeypatch.setattr(trusted, "setup_project", forbidden)
    with pytest.raises(ValueError, match="CURRENT_TYPED_PREPARATION_REQUIRED_BEFORE_PRIVILEGED_OPERATIONS"):
        calibration.g5a_trusted_quota_setup(root_request, preparation=preparation)
    with pytest.raises(ValueError, match="CURRENT_TYPED_PREPARATION_REQUIRED_BEFORE_PRIVILEGED_OPERATIONS"):
        calibration.g5a_seal_quota_setup(root_request, preparation=preparation)


@pytest.mark.parametrize("scope", ("capacity-probe", "capacity-calibration", "G5A_EVIDENCE_ACQUISITION_ONLY"))
def test_original_twenty_gib_quota_is_only_admitted_in_its_exact_scopes(scope):
    if scope == "capacity-probe":
        with pytest.raises(ValueError, match="SCOPE_OR_LIMIT_REBOUND"):
            trusted.validate_project_spec(hard_bytes=20 * calibration.GIB, hard_inodes=100000, operation_scope=scope)
    else:
        assert trusted.validate_project_spec(hard_bytes=20 * calibration.GIB, hard_inodes=100000,
            operation_scope=scope)["hard_bytes"] == 20 * calibration.GIB


@pytest.mark.parametrize("hard_bytes,hard_inodes", ((20 * calibration.GIB + 1024, 100000),
    (20 * calibration.GIB, 100001), (26 * calibration.GIB, 100000),
    (512 * calibration.MIB, 100000), (calibration.MIB, 100000), (True, 32768)))
def test_g5a_never_admits_larger_or_relabelled_project_limits(hard_bytes, hard_inodes):
    with pytest.raises(ValueError, match="TRUSTED_QUOTA_"):
        trusted.validate_project_spec(hard_bytes=hard_bytes, hard_inodes=hard_inodes,
            operation_scope="G5A_EVIDENCE_ACQUISITION_ONLY")


def test_type_only_preparation_has_no_registered_native_or_owner_authority(monkeypatch):
    prep = object.__new__(g5.TrustedG5APreparation)
    object.__setattr__(prep, "manifest_wire", b"{}")
    root_request = dict.fromkeys(runner.REQUEST_FIELDS)
    root_request.update(binding={"fixture": "DECLARED_FORMAT_ONLY"})
    original_sha = calibration.digest(calibration.wire(root_request))
    root_request.update(original_request_sha256=original_sha,
        privileged_custody_live={"calibration_request_sha256": original_sha})
    monkeypatch.setattr(runner, "validate_request_shape", lambda row: {"fixture_only": True})
    monkeypatch.setattr(runner, "read_input", lambda *args, **kwargs: pytest.fail("Unregistered object cannot read Source inputs"))
    monkeypatch.setattr(calibration, "configure_loop", lambda *args, **kwargs: pytest.fail("No native setup"))
    with pytest.raises(ValueError, match="G5A_CURRENT_TYPED_TRUSTED_PREPARATION_REQUIRED"):
        calibration.g5a_trusted_quota_setup(root_request, preparation=prep)


def mocked_setup(tmp_path, monkeypatch):
    """A declared orchestration double; none of these records are native proof."""
    mount = tmp_path / "owned" / "mount"; mount.mkdir(parents=True)
    image = mount.parent / "image.ext4"; image.write_bytes(b"UNIT_FORMAT_ONLY_NO_BACKING_ALLOCATION")
    producer = mount / "project"
    archive = producer / "data/paper_v17/artifacts/observer_v17.db/dynamic-shadow-archive"
    paths = {"mount": mount, "producer": producer, "control": mount / "control", "archive": archive,
        "probe": mount / "edquot-probe"}
    image_fields = {"st_dev": 22, "st_ino": 33, "st_uid": 1001, "st_gid": 1001,
        "st_mode": stat.S_IFREG | 0o600, "st_nlink": 1, "st_size": 26 * calibration.GIB,
        "st_blocks": 52 * calibration.GIB // 512, "st_atime_ns": 1, "st_mtime_ns": 1, "st_ctime_ns": 1}
    mount_fields = {**image_fields, "st_mode": stat.S_IFDIR | 0o700}
    request = {"schema": "porota.rc6.g5a-root-request.v1", "operation_scope": "G5A_EVIDENCE_ACQUISITION_ONLY",
        "source_sha": "a" * 40, "source_tree": "b" * 40, "binding": {"fixture": "DECLARED_UNIT_DOUBLE_ONLY"},
        "owner_uid": 1001, "owner_gid": 1001, "image": str(image), "image_identity": image_fields,
        "mountpoint": str(mount), "mount_namespace_inode": 88, "privileged_custody_live": {"fixture_only": True},
        "namespace_receipt": {"namespace_nonce": "1" * 32, "path": str(mount.parent)},
        "filter_binding": {"source_sha": "a" * 40, "source_tree": "b" * 40, "plan_sha256": "c" * 64},
        "limits": {"backing_image_bytes": 26 * calibration.GIB, "project_hard_limit_bytes": 20 * calibration.GIB,
            "residual_reserve_bytes": 4 * calibration.GIB}}
    calls = []; preparation = object()
    def approved_check(actual, *, preparation=None):
        assert actual is request and preparation is not None
        calls.append("TYPED_VALIDATION_DOUBLE")
        return paths
    monkeypatch.setattr(calibration, "g5a_validate_setup_request", approved_check)
    monkeypatch.setattr(calibration, "g5a_validate_root_namespace", lambda actual: calls.append("LIVE_ROOT_PARENT_DOUBLE"))
    monkeypatch.setattr(custody, "verify_live_controller", lambda *args: calls.append("LIVE_ROOT_SEAL_RECHECK_DOUBLE"))
    monkeypatch.setattr(os, "getresuid", lambda: (0, 0, 0)); monkeypatch.setattr(os, "getresgid", lambda: (0, 0, 0))
    real_listdir = os.listdir
    monkeypatch.setattr(os, "listdir", lambda path: [str(os.getpid())] if str(path) == "/proc/self/task"
        else ["0", "1", "2", "3", "9"] if str(path) == "/proc/self/fd" else real_listdir(path))
    real_stat = os.stat
    monkeypatch.setattr(os, "stat", lambda path, *args, **kwargs: SimpleNamespace(st_ino=99)
        if str(path) == "/proc/self/ns/mnt" else real_stat(path, *args, **kwargs))
    real_lstat = Path.lstat
    monkeypatch.setattr(Path, "lstat", lambda path, *args, **kwargs: SimpleNamespace(**image_fields)
        if path == image else SimpleNamespace(**mount_fields) if path == mount else real_lstat(path, *args, **kwargs))
    real_read_text = Path.read_text
    mountinfo = "1 0 0:1 / / ro - ext4 /dev/root ro\n2 1 0:2 / " + str(mount) + " rw,nodev,nosuid - ext4 /dev/loop0 rw\n"
    monkeypatch.setattr(Path, "read_text", lambda path, *args, **kwargs: mountinfo if str(path) == "/proc/self/mountinfo"
        else real_read_text(path, *args, **kwargs))
    monkeypatch.setattr(os, "chmod", lambda *args, **kwargs: calls.append("CHMOD_DOUBLE"))
    monkeypatch.setattr(os, "chown", lambda *args, **kwargs: calls.append("CHOWN_DOUBLE"))
    monkeypatch.setattr(os, "close", lambda fd: calls.append(("CLOSE_DOUBLE", fd)))
    monkeypatch.setattr(calibration, "private_mount_preflight", lambda *args: {"fixture_only": True})
    monkeypatch.setattr(calibration, "g5a_open_image_on_issuer_mount", lambda actual: (10001, {"fixture_only": True}, 10002))
    monkeypatch.setattr(calibration, "configure_loop", lambda *args, **kwargs: ("/dev/loop0", {"fixture_only": True}, 10003))
    monkeypatch.setattr(calibration, "publish_loop_birth", lambda *args: {"fixture_only": True})
    def run_setup(actual, argv): calls.append(("SETUP_DOUBLE", argv))
    monkeypatch.setattr(calibration, "run_setup", run_setup)
    monkeypatch.setattr(calibration, "own_image_superblock", lambda actual: {"fixture_only": True})
    monkeypatch.setattr(calibration, "mount_all_readonly", lambda **kwargs: calls.append("READONLY_DOUBLE") or {"return": 0})
    monkeypatch.setattr(calibration, "mount_own_ext4", lambda *args: calls.append("MOUNT_DOUBLE") or {"return": 0})
    monkeypatch.setattr(calibration, "quota_format", lambda *args: {"return": 0, "format_id": 4})
    quotas = {}
    def project_setup(device, path, number, *, hard_bytes, hard_inodes, operation_scope):
        spec = trusted.validate_project_spec(hard_bytes=hard_bytes, hard_inodes=hard_inodes, operation_scope=operation_scope)
        result = {**spec, "project_id": number, "kernel_readback": True, "used_bytes": 0, "used_inodes": 0,
            "project_attributes": {"project_id": number, "inheritance": True}, "fixture_only": True}
        quotas[path] = result; calls.append(("QUOTA_DOUBLE", str(path), number, hard_bytes, hard_inodes))
        return result
    monkeypatch.setattr(trusted, "setup_project", project_setup)
    def filesystem(path):
        if path == mount:
            return {"available_bytes": 25 * calibration.GIB, "free_inodes": 1500000,
                "total_inodes": 1600000, "fragment_bytes": 4096, "total_bytes": 26 * calibration.GIB}
        return {"available_bytes": quotas[path]["hard_bytes"], "free_inodes": quotas[path]["hard_inodes"],
            "total_bytes": quotas[path]["hard_bytes"], "total_inodes": quotas[path]["hard_inodes"], "fragment_bytes": 4096}
    monkeypatch.setattr(calibration, "filesystem", filesystem)
    def drop(uid, gid, *, source_binding):
        assert source_binding == request["filter_binding"]
        calls.append("DROP_DOUBLE")
        return {"uid": uid, "gid": gid, "fixture_only": True}
    monkeypatch.setattr(calibration, "drop_privileges", drop)
    return request, preparation, paths, calls


def test_g5a_setup_uses_original_backing_shared_workload_and_distinct_bounded_archive(tmp_path, monkeypatch):
    request, preparation, paths, calls = mocked_setup(tmp_path, monkeypatch)
    result = calibration.g5a_trusted_quota_setup(request, preparation=preparation)
    assert result is request and "mode" not in result
    assert calls[:2] == ["TYPED_VALIDATION_DOUBLE", "LIVE_ROOT_PARENT_DOUBLE"]
    operations = [row for row in calls if isinstance(row, tuple) and row[0] == "QUOTA_DOUBLE"]
    assert len(operations) == 4
    assert operations[0][2] == operations[1][2]
    assert [(row[3], row[4]) for row in operations] == [(20 * calibration.GIB, 100000),
        (20 * calibration.GIB, 100000), (512 * calibration.MIB, 32768), (calibration.MIB, 32768)]
    assert len({row[2] for row in operations}) == 3
    mkfs = [row[1] for row in calls if isinstance(row, tuple) and row[0] == "SETUP_DOUBLE"]
    assert "lazy_itable_init=0,lazy_journal_init=0,nodiscard" in mkfs[1]
    assert result["archive"] == str(paths["archive"])
    assert result["phases"][0]["native_EDQUOT_proved_by_setup"] is False
    assert result["phases"][0]["G5_GREEN"] is False and result["phases"][0]["qualification_claimed"] is False
    assert ("CLOSE_DOUBLE", 10003) in calls
    assert "DROP_DOUBLE" not in calls


def test_g5a_seal_rechecks_typed_input_and_live_parent_closes_all_fds_then_drops_exact_source(tmp_path, monkeypatch):
    request, preparation, _, calls = mocked_setup(tmp_path, monkeypatch)
    calibration.g5a_trusted_quota_setup(request, preparation=preparation)
    before = len(calls)
    result = calibration.g5a_seal_quota_setup(request, preparation=preparation)
    seal_calls = calls[before:]
    assert seal_calls == ["TYPED_VALIDATION_DOUBLE", "LIVE_ROOT_SEAL_RECHECK_DOUBLE",
        ("CLOSE_DOUBLE", 3), ("CLOSE_DOUBLE", 9), "DROP_DOUBLE"]
    assert result["setup_stage"] == "g5a_nonroot_ready"
    assert [row["phase"] for row in result["phases"]] == ["TrustedQuotaSetup", "Seal"]
    assert result["phases"][1]["filter_binding"] == request["filter_binding"]
    assert result["phases"][1]["qualification_claimed"] is False


@pytest.mark.parametrize("fault", ("statvfs_bytes", "statvfs_inodes", "physical_reserve", "readback", "seal_stage", "native_fin"))
def test_g5a_setup_or_seal_cannot_continue_when_required_kernel_projection_is_red(tmp_path, monkeypatch, fault):
    request, preparation, _, calls = mocked_setup(tmp_path, monkeypatch)
    if fault in ("statvfs_bytes", "statvfs_inodes", "physical_reserve"):
        original = calibration.filesystem
        def changed(path):
            result = original(path)
            if fault == "statvfs_bytes": result["total_bytes"] += 4096
            elif fault == "statvfs_inodes": result["total_inodes"] += 1
            elif "available_bytes" in result: result["available_bytes"] = 0
            return result
        monkeypatch.setattr(calibration, "filesystem", changed)
    elif fault == "readback":
        monkeypatch.setattr(trusted, "setup_project", lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("TRUSTED_QUOTA_KERNEL_READBACK_FAILED")))
    if fault in ("seal_stage", "native_fin"):
        calibration.g5a_trusted_quota_setup(request, preparation=preparation)
        if fault == "seal_stage": request["setup_stage"] = "not_setup"
        else: monkeypatch.setattr(custody, "verify_live_controller", lambda *args: (_ for _ in ()).throw(
            ValueError("ROOT_CUSTODY_EXTERNAL_PIDFD_ALREADY_EXITED")))
        with pytest.raises(ValueError, match="G5A_SETUP_|ROOT_CUSTODY_"):
            calibration.g5a_seal_quota_setup(request, preparation=preparation)
    else:
        with pytest.raises(ValueError, match="G5A_SETUP_|TRUSTED_QUOTA_|CALIBRATION_"):
            calibration.g5a_trusted_quota_setup(request, preparation=preparation)
    assert "DROP_DOUBLE" not in calls
