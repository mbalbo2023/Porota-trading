"""Minimal ROOT project-quota operations, separate from privilege sealing.

Callers must already authenticate their ephemeral request/mount/device and
external guardian. These helpers grant no launch or cleanup authority. Each
successful native operation is read back; kernel metadata is not capability.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat

SCHEMA = "porota.rc6.trusted-quota-setup.v2"
PHASES = ("TrustedQuotaSetup", "Seal", "NONROOT", "CustodyFIN")


def require(value, reason):
    if not value:
        raise ValueError(reason)


def validate_project_spec(*, hard_bytes, hard_inodes, operation_scope):
    require(type(hard_bytes) is int and hard_bytes > 0 and hard_bytes % 1024 == 0
            and type(hard_inodes) is int and hard_inodes > 0, "TRUSTED_QUOTA_EXACT_UNITS_REQUIRED")
    from scripts import rc6_capacity_calibration as calibration
    allowed = {
        "capacity-probe": {(512 * calibration.MIB, calibration.MAX_ENTRIES), (calibration.MIB, calibration.MAX_ENTRIES)},
        "capacity-calibration": {(20 * calibration.GIB, calibration.MAX_ENTRIES), (calibration.MIB, calibration.MAX_ENTRIES)},
        "G5A_EVIDENCE_ACQUISITION_ONLY": {(20 * calibration.GIB, calibration.MAX_ENTRIES),
            (512 * calibration.MIB, 32768), (calibration.MIB, 32768)},
        "G5A_HOST_STAGE_AGGREGATE_20GIB_V1": {(20 * calibration.GIB, calibration.MAX_ENTRIES),
            (calibration.MIB, 32768)},
    }
    require(operation_scope in allowed and (hard_bytes, hard_inodes) in allowed[operation_scope],
            "TRUSTED_QUOTA_SCOPE_OR_LIMIT_REBOUND")
    return {"hard_bytes": hard_bytes, "hard_inodes": hard_inodes, "operation_scope": operation_scope}


def setup_project(device, path, project_id, *, hard_bytes, hard_inodes, operation_scope):
    """Actual FSSETXATTR/Q_SETQUOTA/GETQUOTA, never a documentary capability."""
    from scripts import rc6_capacity_calibration as calibration
    spec = validate_project_spec(hard_bytes=hard_bytes, hard_inodes=hard_inodes, operation_scope=operation_scope)
    require(os.getuid() == os.geteuid() == 0 and type(project_id) is int and project_id > 0
            and Path(path).is_absolute() and not any(p.is_symlink() for p in (Path(path), *Path(path).parents))
            and stat.S_ISDIR(Path(path).lstat().st_mode), "TRUSTED_QUOTA_ROOT_OWN_DIRECTORY_REQUIRED")
    calibration.set_project(path, project_id)
    attributes = calibration.project_attributes(path)
    require(attributes["project_id"] == project_id and attributes["inheritance"] is True,
            "TRUSTED_QUOTA_PROJECT_ASSIGNMENT_READBACK_FAILED")
    payload = calibration.Dqblk()
    payload.bhard = payload.bsoft = hard_bytes // 1024
    payload.ihard = payload.isoft = hard_inodes
    payload.valid = 1 | 4
    set_record = calibration.quota_operation(device, project_id, calibration.Q_SETQUOTA, payload)
    actual = calibration.Dqblk()
    read_record = calibration.quota_operation(device, project_id, calibration.Q_GETQUOTA, actual)
    require(actual.bhard * 1024 == actual.bsoft * 1024 == hard_bytes
            and actual.ihard == actual.isoft == hard_inodes
            and 0 <= actual.space < hard_bytes and 0 <= actual.inodes < hard_inodes,
            "TRUSTED_QUOTA_KERNEL_READBACK_FAILED")
    return {"schema": SCHEMA, **spec, "project_id": project_id,
            "project_attributes": attributes, "used_bytes": actual.space, "used_inodes": actual.inodes,
            "kernel_readback": True, "native_operation": read_record, "set_native_operation": set_record,
            "qualification_claimed": False}


def setup_receipt(request):
    """Retain actual operation controls; do not substitute this for EDQUOT."""
    from scripts import rc6_capacity_calibration as calibration
    require(request.get("setup_stage") == "trusted_quota_setup_complete"
            and request.get("quota_format", {}).get("format_id") == calibration.FORMAT_VFS_V1
            and request.get("quota_format", {}).get("return") == 0
            and request.get("mount_operation", {}).get("return") == 0
            and request.get("outside_mounts_readonly_verified") is True
            and type(request.get("quotas")) is list and len(request["quotas"]) == 2,
            "TRUSTED_QUOTA_NATIVE_SETUP_REQUIRED")
    for quota in request["quotas"]:
        require(quota.get("kernel_readback") is True
                and quota.get("native_operation", {}).get("return") == 0
                and quota.get("set_native_operation", {}).get("return") == 0,
                "TRUSTED_QUOTA_NATIVE_READBACK_REQUIRED")
    return {"schema": SCHEMA, "phase": PHASES[0], "source_sha": request["source_sha"],
        "source_tree": request["source_tree"], "binding": request["binding"],
        "quota_format": request["quota_format"], "quotas": request["quotas"],
        "mount_operation": request["mount_operation"], "ext4_superblock": request["ext4_superblock"],
        "project_attributes": request["project_attributes"],
        "actual_financial_backend_called": False, "actual_NONROOT_workload_started": False,
        "module_loading_requested": False, "qualification_claimed": False}


def validate_phase_sequence(phases, *, complete):
    require(type(phases) is list and all(type(row) is dict for row in phases)
            and [row.get("phase") for row in phases] == list(PHASES if complete else PHASES[:2]),
            "TRUSTED_QUOTA_PHASE_ORDER_REQUIRED")
    require(all(row.get("qualification_claimed") is False for row in phases),
            "TRUSTED_QUOTA_PHASE_QUALIFICATION_FORBIDDEN")
    return phases
