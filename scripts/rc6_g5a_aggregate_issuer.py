"""Native 20 GiB host-stage quota emitter; no G5A admission authority.

The trusted setup and the NONROOT stage process are deliberately separate.
The original manager must own their birth, FIN and cleanup. In particular a
JSON copy of either result cannot unlock rc6_g5a_host_capacity.BARRIER.
No module loading, host remount, fallback counter or directory census occurs.
"""
from __future__ import annotations

import ctypes
import errno
import fcntl
import os
from pathlib import Path
import platform
import stat
import struct

from scripts import rc6_capacity_calibration as calibration
from scripts import rc6_trusted_quota_setup as trusted

SCHEMA = "porota.rc6.g5a-host-stage-aggregate-native.v1"
LIMIT = 20 * 1024**3
INODES = 100000
PROBE_LIMIT = 1024**2
STAGES = frozenset(("preparation", "reader-collection", "reader-execution"))
WRITE_BITS = (1, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14)


def require(value, reason):
    if not value:
        raise ValueError(reason)


def _plain_directory(path):
    path = Path(path).absolute()
    require(not any(part.is_symlink() for part in (path, *path.parents)),
            "G5A_AGGREGATE_PATH_ALIAS_FORBIDDEN")
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_nlink >= 2,
            "G5A_AGGREGATE_OWN_DIRECTORY_REQUIRED")
    return path, info


def _mount(root):
    fd = os.open(root, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        held, mount_id = os.fstat(fd), calibration.descriptor_mount_id(fd)
        require(held.st_dev == root.lstat().st_dev and mount_id > 0,
                "G5A_AGGREGATE_HELD_MOUNT_REBOUND")
        rows = []
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            left, separator, right = line.partition(" - ")
            require(separator, "G5A_AGGREGATE_MOUNTINFO_INVALID")
            fields, tail = left.split(), right.split()
            if int(fields[0]) == mount_id:
                rows.append((fields, tail))
        require(len(rows) == 1 and len(rows[0][0]) >= 6 and len(rows[0][1]) >= 2,
                "G5A_AGGREGATE_EXACT_MOUNT_REQUIRED")
        fields, tail = rows[0]
        major, minor = (int(item) for item in fields[2].split(":"))
        require(tail[0] == "ext4" and "rw" in fields[5].split(",")
                and os.makedev(major, minor) == held.st_dev,
                "G5A_AGGREGATE_ORIGINAL_EXT4_PROJECT_MOUNT_REQUIRED")
        return {"mount_id": mount_id, "device": held.st_dev,
                "major": major, "minor": minor, "filesystem": tail[0]}
    finally:
        os.close(fd)


def _quota(device, project_id):
    record = calibration.Dqblk()
    operation = calibration.quota_operation(str(device), project_id, calibration.Q_GETQUOTA, record)
    return {"hard_bytes": record.bhard * 1024, "soft_bytes": record.bsoft * 1024,
            "hard_inodes": record.ihard, "soft_inodes": record.isoft,
            "used_bytes": record.space, "used_inodes": record.inodes,
            "native_operation": operation}


def trusted_setup(*, root, probe, source, device, project_id, probe_id, owner_uid, owner_gid):
    """Root-only setup on the original ext4, before any installer or reader.

    The caller supplies a fresh own pair of sibling directories in a private
    mount namespace and an authenticated device. Failure retains both paths
    for original-manager FIN/capture; this function never removes them.
    """
    require(os.getresuid() == (0, 0, 0) and len(os.listdir("/proc/self/task")) == 1,
            "G5A_AGGREGATE_TRUSTED_SINGLE_ROOT_REQUIRED")
    require(type(project_id) is int and type(probe_id) is int and project_id > 0
            and probe_id > 0 and project_id != probe_id
            and type(owner_uid) is int and type(owner_gid) is int
            and owner_uid > 0 and owner_gid > 0,
            "G5A_AGGREGATE_DISTINCT_OWN_PROJECTS_REQUIRED")
    root, root_info = _plain_directory(root)
    probe, probe_info = _plain_directory(probe)
    source, source_info = _plain_directory(source)
    home, home_info = _plain_directory(Path.home())
    require(root.parent == probe.parent and root != probe and root.is_relative_to(home)
            and root_info.st_dev == probe_info.st_dev == source_info.st_dev == home_info.st_dev
            and (root_info.st_uid, probe_info.st_uid) == (owner_uid, owner_uid)
            and not any(root.iterdir()) and not any(probe.iterdir())
            and len(os.fsencode(root / "e")) <= 50,
            "G5A_AGGREGATE_ORIGINAL_FILESYSTEM_FRESH_SHORT_ROOT_REQUIRED")
    mount = _mount(root)
    block = Path(device)
    require(block.is_absolute() and not block.is_symlink()
            and stat.S_ISBLK(block.lstat().st_mode)
            and block.stat().st_rdev == mount["device"],
            "G5A_AGGREGATE_EXACT_BLOCK_DEVICE_REQUIRED")
    original = calibration.filesystem(root)
    require(original["available_bytes"] >= LIMIT + 4 * 1024**3
            and original["free_inodes"] * 10 >= original["total_inodes"],
            "G5A_AGGREGATE_ORIGINAL_RESERVE_AND_INODES_REQUIRED")
    fmt = calibration.quota_format(str(block))
    require(fmt["format_id"] == calibration.FORMAT_VFS_V1,
            "G5A_AGGREGATE_KERNEL_PROJECT_QUOTA_FORMAT_REQUIRED")
    aggregate = trusted.setup_project(str(block), root, project_id,
        hard_bytes=LIMIT, hard_inodes=INODES,
        operation_scope="G5A_HOST_STAGE_AGGREGATE_20GIB_V1")
    control = trusted.setup_project(str(block), probe, probe_id,
        hard_bytes=PROBE_LIMIT, hard_inodes=32768,
        operation_scope="G5A_HOST_STAGE_AGGREGATE_20GIB_V1")
    require(aggregate["kernel_readback"] is True and control["kernel_readback"] is True
            and _quota(block, project_id)["hard_bytes"] == LIMIT
            and _quota(block, probe_id)["hard_bytes"] == PROBE_LIMIT,
            "G5A_AGGREGATE_KERNEL_READBACK_REQUIRED")
    for name in ("home", "tmp", "cache", "builds", "exports", "readers", "source"):
        child = root / name
        child.mkdir(mode=0o700)
        os.chown(child, owner_uid, owner_gid)
        require(calibration.project_attributes(child)["project_id"] == project_id,
                "G5A_AGGREGATE_CHILD_PROJECT_INHERITANCE_REQUIRED")
    return {"schema": SCHEMA, "phase": "TRUSTED_SETUP_ONLY", "root": str(root),
            "probe": str(probe), "source": str(source), "device": str(block),
            "mount": mount, "root_identity": [root_info.st_dev, root_info.st_ino],
            "owner_uid": owner_uid, "owner_gid": owner_gid, "project_id": project_id,
            "probe_id": probe_id, "quota_format": fmt, "aggregate": aggregate,
            "probe_quota": control, "launch_authorized": False,
            "G5_GREEN": False, "qualification_claimed": False}


def nonroot_unlinked_edquot_probe(probe, *, expected_id, hard_bytes=PROBE_LIMIT):
    """Actual EDQUOT on an open unlinked inode, including the exact 20 GiB project.

    The 20 GiB call is a material disk operation and must only be made after
    admission by the original manager. The 1 MiB control uses another project.
    A caught ENOSPC does not count as EDQUOT or as aggregate enforcement.
    """
    require(type(hard_bytes) is int and hard_bytes in (PROBE_LIMIT, LIMIT),
            "G5A_AGGREGATE_EXACT_PROBE_LIMIT_REQUIRED")
    require(os.getuid() == os.geteuid() > 0 and type(expected_id) is int and expected_id > 0,
            "G5A_AGGREGATE_NONROOT_PROBE_REQUIRED")
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    require(status["NoNewPrivs"].strip() == "1" and status["Seccomp"].strip() == "2"
            and all(int(status[key].strip(), 16) == 0 for key in
                    ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")),
            "G5A_AGGREGATE_ORIGINAL_NONROOT_SEAL_REQUIRED")
    probe, info = _plain_directory(probe)
    require(info.st_uid == os.getuid()
            and calibration.project_attributes(probe)["project_id"] == expected_id,
            "G5A_AGGREGATE_PROBE_PROJECT_REBOUND")
    if hard_bytes == LIMIT:
        calibration.require_capacity(calibration.filesystem(probe), LIMIT)
    path = probe / "open-unlinked-edquot"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.unlink(path)
        require(os.fstat(fd).st_nlink == 0, "G5A_AGGREGATE_UNLINKED_FD_REQUIRED")
        try:
            os.posix_fallocate(fd, 0, hard_bytes + 4096)
        except OSError as error:
            require(error.errno == errno.EDQUOT,
                    "G5A_AGGREGATE_ACTUAL_UNLINKED_EDQUOT_REQUIRED")
            result = {"errno": error.errno, "name": errno.errorcode[error.errno],
                "open_unlinked": True, "allocated_bytes": os.fstat(fd).st_blocks * 512,
                "same_project_id": expected_id, "hard_bytes": hard_bytes}
        else:
            raise ValueError("G5A_AGGREGATE_UNLINKED_QUOTA_NOT_ENFORCED")
    finally:
        os.close(fd)
    return result


def trusted_stage_kernel_readback(setup, *, stage):
    """Root readback after the original manager's stage FIN, not an admission.

    A captured JSON copy cannot attest the preceding seal, original manager,
    actual workload or its FIN. The live ROOT parent must bind those facts.
    """
    require(os.getresuid() == (0, 0, 0) and len(os.listdir("/proc/self/task")) == 1,
            "G5A_AGGREGATE_TRUSTED_SINGLE_ROOT_REQUIRED")
    require(type(setup) is dict and setup.get("schema") == SCHEMA
            and setup.get("phase") == "TRUSTED_SETUP_ONLY" and stage in STAGES,
            "G5A_AGGREGATE_ORIGINAL_STAGE_SETUP_REQUIRED")
    root, info = _plain_directory(setup["root"])
    require([info.st_dev, info.st_ino] == setup["root_identity"]
            and _mount(root) == setup["mount"]
            and calibration.project_attributes(root)["project_id"] == setup["project_id"]
            and calibration.project_attributes(root)["inheritance"] is True,
            "G5A_AGGREGATE_STAGE_ROOT_REBOUND")
    quota = _quota(setup["device"], setup["project_id"])
    require(quota["hard_bytes"] == quota["soft_bytes"] == LIMIT
            and quota["hard_inodes"] == quota["soft_inodes"] == INODES
            and 0 <= quota["used_bytes"] <= LIMIT
            and 0 <= quota["used_inodes"] <= INODES,
            "G5A_AGGREGATE_STAGE_KERNEL_QUOTA_REBOUND")
    physical = calibration.filesystem(root)
    require(physical["available_bytes"] >= 4 * 1024**3
            and physical["free_inodes"] * 10 >= physical["total_inodes"],
            "G5A_AGGREGATE_STAGE_PHYSICAL_RESERVE_REBOUND")
    return {"schema": SCHEMA, "phase": "POST_ORIGINAL_STAGE_FIN_READBACK_ONLY",
            "stage": stage, "root_identity": setup["root_identity"],
            "mount": setup["mount"], "kernel_quota": quota, "physical": physical,
            "original_manager_fin_verified": False, "launch_authorized": False,
            "G5_GREEN": False, "qualification_claimed": False}


class _PathBeneath(ctypes.Structure):
    _fields_ = (("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32))


def install_stage_write_boundary(root, *, outside_control):
    """Landlock all inherited subprocess writes to the one quota project.

    The original native NONROOT/cap0/NNP/seccomp seal must already exist.
    All extra FDs are closed; stdio must be pipes or character devices so an
    inherited regular FD cannot write around the path policy.
    """
    require(platform.machine() in ("x86_64", "aarch64") and os.getuid() == os.geteuid() > 0,
            "G5A_AGGREGATE_NATIVE_NONROOT_ARCH_REQUIRED")
    root, info = _plain_directory(root)
    outside, other = _plain_directory(outside_control)
    require(root.parent == outside.parent and other.st_dev == info.st_dev
            and other.st_uid == os.getuid()
            and stat.S_IMODE(other.st_mode) & 0o300 == 0o300
            and calibration.project_attributes(root)["inheritance"] is True,
            "G5A_AGGREGATE_BOUNDARY_OWN_PROJECT_REQUIRED")
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    require(status["NoNewPrivs"].strip() == "1" and status["Seccomp"].strip() == "2"
            and all(int(status[key].strip(), 16) == 0 for key in
                    ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")),
            "G5A_AGGREGATE_ORIGINAL_NONROOT_SEAL_REQUIRED")
    require(all(not stat.S_ISREG(os.fstat(fd).st_mode) for fd in (0, 1, 2)),
            "G5A_AGGREGATE_INHERITED_REGULAR_STDIO_FORBIDDEN")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    abi = libc.syscall(444, ctypes.c_void_p(), 0, 1)
    require(abi >= 3, "G5A_AGGREGATE_LANDLOCK_TRUNCATE_AND_REFER_REQUIRED")
    rights = sum(1 << bit for bit in WRITE_BITS)
    root_fd = os.open(root, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    ruleset_fd = -1
    try:
        held = os.fstat(root_fd)
        require((held.st_dev, held.st_ino) == (info.st_dev, info.st_ino),
                "G5A_AGGREGATE_BOUNDARY_DIRFD_REBOUND")
        for value in os.listdir("/proc/self/fd"):
            if value.isdecimal() and int(value) > 2 and int(value) != root_fd:
                try:
                    os.close(int(value))
                except OSError as error:
                    require(error.errno == errno.EBADF, "G5A_AGGREGATE_FD_CLOSE_FAILED")
        mask = ctypes.c_uint64(rights)
        ruleset_fd = libc.syscall(444, ctypes.byref(mask), ctypes.sizeof(mask), 0)
        require(ruleset_fd >= 0, "G5A_AGGREGATE_LANDLOCK_RULESET_FAILED")
        rule = _PathBeneath(rights, root_fd)
        require(libc.syscall(445, ruleset_fd, 1, ctypes.byref(rule), 0) == 0
                and libc.syscall(446, ruleset_fd, 0) == 0,
                "G5A_AGGREGATE_LANDLOCK_SEAL_FAILED")
    finally:
        if ruleset_fd >= 0:
            os.close(ruleset_fd)
        os.close(root_fd)
    target = outside / "outside-write-must-not-exist"
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError as error:
        require(error.errno == errno.EACCES and not target.exists(),
                "G5A_AGGREGATE_ACTUAL_OUTSIDE_WRITE_DENIAL_REQUIRED")
    else:
        os.close(fd)
        raise ValueError("G5A_AGGREGATE_WRITE_BOUNDARY_ESCAPE")
    return {"landlock_abi": abi, "handled_write_rights": rights,
            "root_device": info.st_dev, "root_inode": info.st_ino,
            "outside_errno": errno.EACCES, "all_extra_fds_closed": True,
            "inherited_by_fork_exec": True, "launch_authorized": False}


def stage_environment(root, existing):
    """Place installer, compiler, temporary, Git and reader output in quota."""
    root, _ = _plain_directory(root)
    require(type(existing) is dict and all(type(k) is str and type(v) is str for k, v in existing.items())
            and not any(k in existing for k in ("GH_TOKEN", "GITHUB_TOKEN", "RC6_READONLY_GIT_TOKEN")),
            "G5A_AGGREGATE_NO_CHILD_BEARER_REQUIRED")
    paths = {"HOME": root / "home", "TMPDIR": root / "tmp", "TMP": root / "tmp", "TEMP": root / "tmp",
             "XDG_CACHE_HOME": root / "cache", "PIP_CACHE_DIR": root / "cache",
             "PYTHONPYCACHEPREFIX": root / "cache"}
    require(all(path.is_dir() and not path.is_symlink() and path.stat().st_dev == root.stat().st_dev
                for path in paths.values()), "G5A_AGGREGATE_ALL_CHILD_WRITE_ROOTS_REQUIRED")
    result = {key: value for key, value in existing.items() if key not in paths}
    result.update({key: str(path) for key, path in paths.items()})
    result.update(PYTHONDONTWRITEBYTECODE="1", GIT_OPTIONAL_LOCKS="0")
    return result


def incomplete_evidence(*, setup, unlinked_control, boundary, stages):
    """Retain native facts without turning serialized evidence into a gate."""
    require(type(stages) is list and all(stage in STAGES for stage in stages),
            "G5A_AGGREGATE_ORIGINAL_STAGES_REQUIRED")
    return {"schema": SCHEMA, "setup": setup, "unlinked_control": unlinked_control,
            "boundary": boundary, "observed_stages": stages,
            "all_stages_observed": set(stages) == STAGES,
            "native_original_fin": False, "material_gate_authorized": False,
            "launch_authorized": False, "G5_GREEN": False,
            "qualification_claimed": False, "real_orders_sent": 0,
            "reason": "G5A_HOST_STAGE_AGGREGATE_ENFORCEMENT_NOT_VERIFIED"}
