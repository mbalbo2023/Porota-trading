"""Zero recurring cost certification proposal and read-only quota observations.

No function in this module creates a filesystem, changes a project ID, sets a
quota, starts an actor, grants admission, or treats metadata as enforcement.
The existing calibration contract and its privileged-custody hold stay intact.
"""
from __future__ import annotations

import ctypes
import errno
import fcntl
import os
from pathlib import Path
import struct
import time

GIB = 1024**3
# GitHub's documented nominal SSD capacity uses GB. Preserve that distinction
# in receipts instead of silently treating it as a 14 GiB physical ceiling.
GITHUB_NOMINAL_STORAGE_BYTES = 14 * 1000**3
PROOF_OBLIGATIONS = (
    "AUTHENTICATED_CONTRACT_REVIEW_AND_EXACT_CANDIDATE",
    "NATIVE_AGGREGATE_BYTE_AND_INODE_QUOTA_ENFORCEMENT",
    "ALL_WRITERS_CONFINED_OR_AUTHENTICALLY_BOUNDED",
    "NO_FOREIGN_PROJECT_ID_OR_WRITABLE_MOUNT_ESCAPE",
    "ROOT_SIGNAL_AND_REAP_CUSTODY_FROM_FIRST_INSTRUCTION",
    "NONROOT_UID_GID_CAPABILITIES_FDS_AND_SECCOMP",
    "PHYSICAL_RESERVATION_EQUIVALENCE_AND_LIVE_RECHECK",
    "EXACT_SOURCE_FULL_GIT_AND_WORKLOAD_IDENTITY",
    "ACTUAL_OWN_FIN_BEFORE_RAW_CAPTURE_OR_CLEANUP",
    "HASH_VERIFIED_RAW_CUSTODY_AND_CRASH_RECOVERY",
)


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _integer(value, *, zero=True):
    require(type(value) is int and (0 if zero else 1) <= value <= 2**63 - 1,
            "CERTIFICATION_EXACT_INTEGER_REQUIRED")
    return value


def zero_cost_contract():
    """Describe the proposed revision; this is not a substitute admission path.

    The loop has TWO reserves today: 4 GiB inside the image in
    require_physical_and_project_capacity and 4 GiB outside in require_capacity.
    The conservative proposal retains both even on one filesystem. Combining
    them into one reserve is a separate revision and is not credited here.
    """
    from scripts import rc6_capacity_calibration as calibration
    historic = calibration.limits_for("bootstrap")
    require(historic == {"backing_image_bytes": 26 * GIB,
                        "project_hard_limit_bytes": 20 * GIB,
                        "residual_reserve_bytes": 4 * GIB},
            "CERTIFICATION_HISTORIC_CALIBRATION_LIMITS_CHANGED")
    require(calibration.MAX_ENTRIES == 100000, "CERTIFICATION_HISTORIC_INODE_LIMIT_CHANGED")
    return {"schema": "porota.rc6.zero-cost-contract-proposal.v1", "status": "PROPUESTO",
        "runner_profile": "github-hosted/ubuntu-24.04",
        "recurring_additional_cost_usd": 0, "persistent_infrastructure_created": False,
        "historical_bootstrap_limits": historic,
        "proposed_storage": {"mechanism": "DIRECT_NATIVE_PROJECT_QUOTA_EXISTING_EPHEMERAL_FILESYSTEM",
            "new_backing_image_bytes": 0, "project_hard_limit_bytes": historic["project_hard_limit_bytes"],
            "project_hard_inodes": calibration.MAX_ENTRIES,
            "separate_EDQUOT_probe_hard_limit_bytes": calibration.MIB,
            "separate_EDQUOT_probe_hard_inodes": calibration.MAX_ENTRIES,
            "independent_reserves": {"workload_bytes": 4 * GIB, "custody_bytes": 4 * GIB},
            "reserves_merged": False, "physical_reservation_proof": "NO_VERIFICADO",
            "incremental_filesystem_metadata_bound": "REQUIRED_NOT_ASSUMED_ZERO"},
        "unmodified_scope": {"G5_cycles": 1202, "G5_evidence_bytes": 512 * 1024**2,
            "G5_max_depth": 32, "ROOT_NONROOT_security_required": True,
            "exact_Source_fullGit_original19_required": True,
            "original_retention_and_byte_exact_payload_required": True,
            "FIN_and_recovery_required": True},
        "contract_review_required": True, "proof_obligations": list(PROOF_OBLIGATIONS),
        "privileged_signal_custody": dict(calibration.PRIVILEGED_SIGNAL_CUSTODY),
        "equivalence_proved": False, "G0_GREEN_claimed": False, "launch_authorized": False,
        "heavy_authorized": False, "deploy_authorized": False,
        "real_orders_sent": 0, "real_routes": "NOT_CALLED", "ppi_watch": "UNTOUCHED"}


def evaluate_proposed_storage(*, free_bytes, total_inodes, free_inodes,
                              allocation_unit_bytes, incremental_metadata_bound_bytes=None,
                              nominal_storage_bytes=GITHUB_NOMINAL_STORAGE_BYTES):
    """Conservative direct-quota arithmetic, always pending review/native proof.

    A numeric metadata bound is only a diagnostic input; it is not authenticated
    code evidence. Unknown metadata blocks even this arithmetic conclusion.
    Free bytes must come from the unprojected physical filesystem, not statfs
    of a PROJINHERIT directory whose view is clipped by the project quota.
    """
    from scripts.rc6_capacity_calibration import outer_control_peak_bound
    proposal = zero_cost_contract()
    for value in (free_bytes, free_inodes):
        _integer(value)
    _integer(total_inodes, zero=False)
    _integer(nominal_storage_bytes, zero=False)
    require(free_inodes <= total_inodes, "CERTIFICATION_INODE_MEASUREMENT_INVALID")
    controls = outer_control_peak_bound(allocation_unit_bytes)
    storage = proposal["proposed_storage"]
    quota = storage["project_hard_limit_bytes"]
    probe = storage["separate_EDQUOT_probe_hard_limit_bytes"]
    reserves = sum(storage["independent_reserves"].values())
    original_floor = 26 * GIB + 4 * GIB + controls["total_bytes"]
    blockers, required = [], None
    if incremental_metadata_bound_bytes is None:
        blockers.append("CERTIFICATION_INCREMENTAL_METADATA_BOUND_UNKNOWN")
    else:
        _integer(incremental_metadata_bound_bytes)
        required = quota + probe + reserves + controls["total_bytes"] + incremental_metadata_bound_bytes
        require(required <= 2**63 - 1, "CERTIFICATION_COMPOUND_BOUND_OVERFLOW")
        if free_bytes < required:
            blockers.append("CERTIFICATION_MEASURED_PHYSICAL_BYTES_INSUFFICIENT")
    if free_inodes * 10 < total_inodes:
        blockers.append("CERTIFICATION_INODE_RESERVE_INSUFFICIENT")
    return {"schema": "porota.rc6.proposed-direct-quota-storage-arithmetic.v1",
        "status": "BLOCKED_NECESSARY_STORAGE" if blockers else "ARITHMETIC_FEASIBLE_PENDING_PROOF",
        "storage_blockers": blockers, "required_minimum_bytes": required,
        "historic_loop_minimum_bytes": original_floor,
        "maximum_arithmetic_saving_bytes": None if required is None else original_floor - required,
        "project_hard_limit_bytes": quota, "separate_EDQUOT_probe_hard_limit_bytes": probe,
        "independent_reserve_bytes": reserves,
        "outer_control_bound": controls,
        "incremental_metadata_bound_bytes": incremental_metadata_bound_bytes,
        "measured_free_bytes": free_bytes, "nominal_storage_bytes": nominal_storage_bytes,
        "nominal_storage_is_launch_ceiling": False,
        "nominal_storage_guarantees_necessary_floor": None if required is None else nominal_storage_bytes >= required,
        "platform_impossibility_claimed": False, "reserves_merged": False,
        "contract_review_required": True, "remaining_native_proof_obligations": list(PROOF_OBLIGATIONS),
        "metadata_bound_authenticated": False, "physical_reservation_equivalence_proved": False,
        "equivalence_proved": False, "G0_GREEN_claimed": False, "launch_authorized": False}


def _bounded_text(path, maximum=1024**2):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        chunks, size = [], 0
        while True:
            raw = os.read(descriptor, min(65536, maximum + 1 - size))
            if not raw:
                break
            chunks.append(raw)
            size += len(raw)
            require(size <= maximum, "CERTIFICATION_METADATA_READ_BOUND")
        return b"".join(chunks).decode("utf-8", errors="strict")
    finally:
        os.close(descriptor)


def _mount_metadata(descriptor, details):
    from scripts import porota_predeploy_cleanup as custody
    mount_id = custody.mount_id(descriptor)
    rows = []
    for line in _bounded_text("/proc/self/mountinfo").splitlines():
        before, separator, after = line.partition(" - ")
        fields, fs_fields = before.split(), after.split()
        require(separator and len(fields) >= 6 and len(fs_fields) >= 3,
                "CERTIFICATION_MOUNT_METADATA_INVALID")
        if fields[0] == str(mount_id):
            rows.append((fields, fs_fields))
    require(len(rows) == 1, "CERTIFICATION_MOUNT_METADATA_AMBIGUOUS")
    fields, fs_fields = rows[0]
    require(fields[2] == f"{os.major(details.st_dev)}:{os.minor(details.st_dev)}",
            "CERTIFICATION_MOUNT_DEVICE_REBOUND")
    return {"mount_id": mount_id, "filesystem_device": details.st_dev, "directory_inode": details.st_ino,
            "filesystem_type": fs_fields[0], "mount_options": fields[5].split(","),
            "superblock_options": fs_fields[2].split(",")}


def _quota_readback(descriptor, project_id):
    """Invoke only Q_GETFMT/Q_GETQUOTA against the already opened filesystem.

    quotactl_fd avoids resolving a guessed /dev device or a path alias. An old
    libc, denied read or unsupported filesystem remains an explicit unknown.
    A successful readback still does not prove enforcement or writer custody.
    """
    from scripts import rc6_capacity_calibration as calibration
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "quotactl_fd", None)
    if function is None:
        return {"status": "NO_VERIFICADO", "reason": "QUOTACTL_FD_READ_OBSERVER_UNAVAILABLE",
                "mutations_attempted": False, "enforcement_proved": False}
    function.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    function.restype = ctypes.c_int
    operations = []
    def read_operation(command, identifier, value, name):
        ctypes.set_errno(0)
        returned = function(descriptor, (command << 8) | calibration.PRJQUOTA,
                            identifier, ctypes.byref(value))
        saved_errno = ctypes.get_errno()
        operations.append({"operation": name, "return": returned, "errno": saved_errno,
                           "errno_name": errno.errorcode.get(saved_errno, "SUCCESS" if saved_errno == 0 else "UNKNOWN")})
        return returned == 0
    quota_format = ctypes.c_uint32()
    if not read_operation(calibration.Q_GETFMT, 0, quota_format, "quotactl_fd/Q_GETFMT/PRJQUOTA"):
        return {"status": "NO_VERIFICADO", "reason": "EXISTING_PROJECT_QUOTA_FORMAT_NOT_READABLE",
                "operations": operations, "mutations_attempted": False, "enforcement_proved": False}
    quota = calibration.Dqblk()
    if not read_operation(calibration.Q_GETQUOTA, project_id, quota, "quotactl_fd/Q_GETQUOTA/PRJQUOTA"):
        return {"status": "NO_VERIFICADO", "reason": "EXISTING_PROJECT_QUOTA_NOT_READABLE",
                "format_id": quota_format.value, "operations": operations,
                "mutations_attempted": False, "enforcement_proved": False}
    return {"status": "READBACK_METADATA_ONLY", "project_id": project_id,
            "format_id": quota_format.value, "hard_bytes": quota.bhard * 1024,
            "soft_bytes": quota.bsoft * 1024, "used_bytes": quota.space,
            "hard_inodes": quota.ihard, "soft_inodes": quota.isoft, "used_inodes": quota.inodes,
            "operations": operations, "mutations_attempted": False, "enforcement_proved": False}


def readonly_quota_custody_observation(path):
    """Observe this execution's native metadata without elevating privileges."""
    from scripts import porota_predeploy_cleanup as custody
    from scripts import rc6_capacity_calibration as calibration
    selected = Path(path).absolute()
    errors, mount, project, quota, process = [], None, None, None, None
    def failed(component, error):
        errors.append({"component": component, "status": "NO_VERIFICADO",
                       "error_type": type(error).__name__, "errno": getattr(error, "errno", None),
                       "reason": str(error)[:2048]})
    try:
        with custody.directory(selected, readable=True) as descriptor:
            details = os.fstat(descriptor)
            mount = _mount_metadata(descriptor, details)
            buffer = bytearray(28)
            fcntl.ioctl(descriptor, calibration.FSGETXATTR, buffer, True)
            xflags, _, _, project_id, _ = struct.unpack("=5I8x", buffer)
            project = {"project_id": project_id, "project_inherit": bool(xflags & calibration.PROJINHERIT),
                       "xflags": xflags, "operation": "ioctl/FSGETXATTR",
                       "statfs_may_be_quota_projected": project_id != 0 or bool(xflags & calibration.PROJINHERIT)}
            quota = _quota_readback(descriptor, project_id)
            require(os.fstat(descriptor).st_dev == details.st_dev
                    and os.fstat(descriptor).st_ino == details.st_ino
                    and custody.mount_id(descriptor) == mount["mount_id"],
                    "CERTIFICATION_OBSERVED_INODE_OR_MOUNT_REBOUND")
    except Exception as error:
        failed("existing_filesystem_project_quota", error)
    try:
        status = dict(row.split(":", 1) for row in _bounded_text("/proc/self/status", 65536).splitlines() if ":" in row)
        process = {"pid": os.getpid(), "uids": [int(value) for value in status["Uid"].split()],
                   "gids": [int(value) for value in status["Gid"].split()],
                   "capabilities": {key: int(status[key].strip(), 16) for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")},
                   "no_new_privileges": int(status["NoNewPrivs"].strip()),
                   "seccomp_mode": int(status["Seccomp"].strip()),
                   "mount_namespace_inode": os.stat("/proc/self/ns/mnt").st_ino,
                   "user_namespace_inode": os.stat("/proc/self/ns/user").st_ino,
                   "observed_monotonic_ns": time.monotonic_ns(),
                   "root_actor_signal_or_FIN_test": "NOT_CALLED"}
    except Exception as error:
        failed("observer_process_custody_metadata", error)
    known = quota is not None and quota.get("status") == "READBACK_METADATA_ONLY"
    return {"schema": "porota.rc6.readonly-existing-quota-custody.v1",
        "status": "READ_ONLY_METADATA_RECORDED" if known and not errors else "NO_VERIFICADO",
        "mount": mount, "project": project, "quota": quota, "observer_process": process,
        "observer_errors": errors, "privileged_signal_custody": dict(calibration.PRIVILEGED_SIGNAL_CUSTODY),
        "project_assignment_attempted": False, "quota_mutations_attempted": False,
        "actor_launch_attempted": False, "mount_attempted": False,
        "native_quota_enforcement_proved": False, "privileged_signal_custody_proved": False,
        "FIN_proved": False, "platform_impossibility_claimed": False,
        "G0_GREEN_claimed": False, "launch_authorized": False}
