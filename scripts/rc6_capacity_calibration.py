#!/usr/bin/env python3
"""Own hard-quota capacity diagnosis. This never qualifies an RC6 candidate.

The root role only constructs a private mount namespace. It drops every UID,
GID and capability before loading the original NONROOT native supervisor or
running build backends. An unknown FIN preserves the image and its controls.
No host mount, quota, existing inode, runtime or productive checkout is changed.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import errno
import fcntl
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import re
import resource
import runpy
import shutil
import stat
import struct
import subprocess
import sys
import time
from types import MappingProxyType

ROOT = Path(__file__).absolute().parents[1]
SCHEMA = "porota.rc6.capacity-calibration.v1"
PRIVILEGED_SIGNAL_CUSTODY = MappingProxyType({"status": "NO_VERIFICADO", "proved": False,
                                           "privileged_launch_authorized": False})
GIB, MIB = 1024**3, 1024**2
RESERVE, CONTROLS = 4 * GIB, 128 * MIB
MAX_RAW, MAX_RAW_FILES, MAX_ENTRIES = 24 * MIB, 256, 100000
MAX_WORKER_WIRE, MAX_RECEIPT = 40 * MIB, 8 * MIB
DRIVER_MEMBER = "scripts/rc6_controlled_native_child_manager.py"
DRIVER_SHA256 = "55325b3108e175a42b87ebe544fd307fa45ffd29b6f7ab471443803ad9ba53b8"
PREFIX = "docs/audits/convergence/evidence/controlled-successor-20261006/checkpoint7-read-diagnostics/"
OBJECTS_MEMBER = PREFIX + "full-gov-prepared-only/original-required-19-objects.json"
OBJECTS_SHA256 = "492a3c0de260501776ee235ea5472da373d6c5881a4dfafca05518297fc16579"
BUILDER_MEMBER = PREFIX + "full-gov-builder-prepared-only/build_governed_sources.py.source"
BUILDER_SHA256 = "03d08c3f362b12da70defa8f1e2388373d41e20a4ffc08e46f48fb9691667738"
CODE_MEMBERS = ("scripts/rc6_capacity_calibration.py", DRIVER_MEMBER, "scripts/rc6_material_environment.py",
                "scripts/rc6_owned_gate_lease.py",
                "requirements.lock.txt", "requirements.build.lock.txt", "ops/policy/rc6-supply-chain-v1.json",
                OBJECTS_MEMBER, BUILDER_MEMBER)
ORIGIN = "https://github.com/mbalbo2023/Porota-trading.git"
VERSIONS = {"311": "3.11.16", "312": "3.12.14"}
FSGETXATTR, FSSETXATTR = 0x801C581F, 0x401C5820
SETFLAGS = (0x40086602, 0x40046602)
PROJINHERIT = 0x200
Q_GETQUOTA, Q_SETQUOTA, PRJQUOTA = 0x800007, 0x800008, 2
Q_GETFMT, FORMAT_VFS_V1 = 0x800004, 4
# These mutate namespace/privilege/quota state. Native fork/exec, wait4,
# subreaper readback and the original supervisor's TERM2/finalize5 remain legal.
DENIED_SYSCALLS = (
    "mount", "umount2", "unshare", "setns", "chroot", "pivot_root",
    "move_mount", "open_tree", "fsopen", "fsconfig", "fsmount", "fspick", "mount_setattr",
    "quotactl", "quotactl_fd", "setuid", "setreuid", "setresuid", "setfsuid",
    "setgid", "setregid", "setresgid", "setfsgid", "setgroups", "capset",
    "ptrace", "process_vm_writev", "process_vm_readv", "bpf", "kexec_load", "kexec_file_load",
    "init_module", "finit_module", "delete_module", "reboot", "swapon", "swapoff",
    "io_uring_setup", "io_uring_enter", "io_uring_register",
)
NAMESPACE_CLONE_FLAGS = (0x00020000, 0x02000000, 0x04000000, 0x08000000,
                         0x10000000, 0x20000000, 0x40000000, 0x00000080)


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


class CalibrationOperationError(ValueError):
    def __init__(self, reason, details):
        super().__init__(reason)
        self.details = details


def wire(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def decode(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            need(key not in result, "CALIBRATION_DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("CALIBRATION_NONFINITE_JSON")))


def read(path, maximum=CONTROLS):
    path = Path(path).absolute()
    need(not any(p.is_symlink() for p in (path, *path.parents)), "CALIBRATION_NOFOLLOW_PATH_REQUIRED")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NOATIME)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= maximum,
             "CALIBRATION_PRIVATE_REGULAR_CONTROL_REQUIRED")
        chunks, count = [], 0
        while True:
            chunk = os.read(fd, min(MIB, maximum + 1 - count))
            if not chunk:
                break
            count += len(chunk)
            need(count <= maximum, "CALIBRATION_CONTROL_TOO_LARGE")
            chunks.append(chunk)
        need(identity(before) == identity(os.fstat(fd)) == identity(path.lstat()),
             "CALIBRATION_CONTROL_CHANGED")
        return b"".join(chunks)
    finally:
        os.close(fd)


def publish(path, value):
    raw = wire(value)
    need(len(raw) <= CONTROLS, "CALIBRATION_WIRE_LIMIT")
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return digest(raw)


def identity(value):
    return {key: getattr(value, key) for key in (
        "st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size", "st_blocks",
        "st_atime_ns", "st_mtime_ns", "st_ctime_ns")}


def limits_for(mode):
    need(mode in ("capability", "bootstrap"), "CALIBRATION_MODE_REQUIRED")
    return {"backing_image_bytes": (5 if mode == "capability" else 26) * GIB,
            "project_hard_limit_bytes": 512 * MIB if mode == "capability" else 20 * GIB,
            "residual_reserve_bytes": RESERVE}


def validate_limits(mode, value):
    need(type(value) is dict and value == limits_for(mode)
         and all(type(v) is int for v in value.values()), "CALIBRATION_HARD_LIMIT_REBOUND")
    return value


def filesystem(path):
    path = Path(path).absolute()
    need(not any(p.is_symlink() for p in (path, *path.parents)), "CALIBRATION_FILESYSTEM_NOFOLLOW_REQUIRED")
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        details, fs = os.fstat(fd), os.fstatvfs(fd)
        rows = Path("/proc/self/fdinfo/" + str(fd)).read_text().splitlines()
        mounts = [int(x.split(":", 1)[1]) for x in rows if x.startswith("mnt_id:")]
        need(len(mounts) == 1 and fs.f_frsize > 0 and fs.f_files > 0, "CALIBRATION_FILESYSTEM_MEASUREMENT_REQUIRED")
        return {"path": str(path), "device": details.st_dev, "inode": details.st_ino,
                "mount_id": mounts[0], "fragment_bytes": fs.f_frsize,
                "total_bytes": fs.f_blocks * fs.f_frsize,
                "available_bytes": fs.f_bavail * fs.f_frsize,
                "free_inodes": fs.f_favail, "total_inodes": fs.f_files,
                "measured_monotonic_ns": time.monotonic_ns()}
    finally:
        os.close(fd)


def require_capacity(observation, bound, *, control_bytes=0):
    fields = ("available_bytes", "free_inodes", "total_inodes", "fragment_bytes")
    need(type(observation) is dict and all(type(observation.get(k)) is int for k in fields)
         and observation["total_inodes"] > 0 and observation["fragment_bytes"] > 0,
         "CALIBRATION_CAPACITY_NOT_MEASURED")
    need(type(bound) is int and bound > 0 and type(control_bytes) is int and control_bytes >= 0,
         "CALIBRATION_KNOWN_HARD_BOUND_REQUIRED")
    need(observation["available_bytes"] >= bound + RESERVE + control_bytes,
         "CALIBRATION_CAPACITY_RESERVE_BLOCKED")
    need(observation["free_inodes"] * 10 >= observation["total_inodes"],
         "CALIBRATION_INODE_RESERVE_BLOCKED")


def outer_control_peak_bound(fragment_bytes):
    """Physical simultaneous copies, never a credit from future cleanup.

    Both authenticated captures have their original 128MiB API ceiling. The
    already downloaded predecessor artifact is in the live baseline; there is
    no new artifact download between this measurement and own FIN.
    """
    need(type(fragment_bytes) is int and 0 < fragment_bytes <= 65536,
         "CALIBRATION_CONTROL_ALLOCATION_UNIT_UNKNOWN")
    components = {"root_request_marker_fin_bytes": 6 * MIB,
                  "original_worker_log_bytes": MAX_WORKER_WIRE,
                  "decoded_original_raw_bytes": MAX_RAW,
                  "calibration_receipt_bytes": MAX_RECEIPT,
                  "internal_authenticated_capture_bytes": CONTROLS,
                  "carrier_authenticated_capture_bytes": CONTROLS,
                  "two_capture_manifests_bytes": 8 * MIB,
                  "carrier_terminal_controls_bytes": 4 * MIB,
                  "issuer_owned_lease_controls_bytes": 16 * MIB,
                  "directory_and_file_rounding_bytes": (4 * MAX_RAW_FILES + 64) * fragment_bytes}
    return {"schema": "porota.rc6.capacity-diagnostic-control-bound.v1", "components": components,
            "total_bytes": sum(components.values()), "premature_cleanup_credit_bytes": 0,
            "future_capture_copies_included": 2, "max_raw_files": MAX_RAW_FILES}


def evaluate_kernel_prerequisites(config, *, quota_module_live, filesystems, tools, seccomp_names_known):
    """Read-only metadata admission, never a native quota capability claim."""
    missing, unknown = [], []
    for name in ("CONFIG_QUOTA", "CONFIG_QUOTACTL", "CONFIG_EXT4_FS", "CONFIG_QFMT_V2"):
        value = config.get(name)
        if value == "n":
            missing.append(name)
        elif value not in ("y", "m"):
            unknown.append(name)
    if config.get("CONFIG_QFMT_V2") == "m" and not quota_module_live:
        missing.append("QFMT_VFS_V1_MODULE_NOT_ALREADY_LIVE")
    if "ext4" not in filesystems:
        missing.append("EXT4_NOT_REGISTERED")
    for name in ("sudo", "unshare", "mkfs.ext4"):
        if name not in tools:
            missing.append("TOOL_MISSING:" + name)
    if not seccomp_names_known:
        missing.append("LIBSECCOMP_REQUIRED_NAMES_UNAVAILABLE")
    return {"status": "NO_VERIFICADO" if unknown else "BLOQUEADO" if missing else "PREREQUISITES_PRESENT",
            "missing": missing, "unknown": unknown, "actual_capability_proved": False,
            "module_loading_attempted": False, "qualification_claimed": False,
            "kernel_unsupported_claimed": False,
            "format_readiness": "READINESS_REQUIRED_NO_MODULE_LOADING" if
                "QFMT_VFS_V1_MODULE_NOT_ALREADY_LIVE" in missing else
                "NO_VERIFICADO" if unknown else "PRESENT_METADATA_ONLY"}


def readonly_system_code(path):
    """Hash a resolved system executable; this grants no cleanup authority.

    System code may have legitimate hardlink aliases and a different owner.
    Ordinary O_RDONLY can change its atime; bytes and the other ten fields
    must remain stable. The financial Source contract is not weakened.
    """
    path = Path(path).resolve(strict=True)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= 128 * MIB,
             "CALIBRATION_SYSTEM_CODE_REGULAR_BOUND_REQUIRED")
        hasher, count = hashlib.sha256(), 0
        while True:
            chunk = os.read(fd, MIB)
            if not chunk:
                break
            count += len(chunk)
            need(count <= 128 * MIB, "CALIBRATION_SYSTEM_CODE_READ_LIMIT")
            hasher.update(chunk)
        after, named = os.fstat(fd), path.stat()
        stable = lambda value: {k: v for k, v in identity(value).items() if k != "st_atime_ns"}
        need(stable(before) == stable(after) == stable(named) and count == before.st_size,
             "CALIBRATION_SYSTEM_CODE_CHANGED")
        return {"path": str(path), "sha256": hasher.hexdigest(), "bytes": count,
                "identity_before": identity(before), "identity_after": identity(after),
                "source_financial_contract_modified": False, "cleanup_authority_granted": False}
    finally:
        os.close(fd)


def kernel_prerequisites(mode="capability"):
    release = platform.release()
    need(re.fullmatch(r"[A-Za-z0-9._+-]{1,120}", release), "CALIBRATION_KERNEL_RELEASE_REQUIRED")
    config, config_record = {}, None
    for path, compressed in ((Path("/boot/config-" + release), False), (Path("/proc/config.gz"), True)):
        try:
            with path.open("rb") as stream:
                raw = stream.read(4 * MIB + 1)
        except (FileNotFoundError, PermissionError):
            continue
        need(len(raw) <= 4 * MIB, "CALIBRATION_KERNEL_CONFIG_LIMIT")
        decoded = gzip.GzipFile(fileobj=io.BytesIO(raw)).read(4 * MIB + 1) if compressed else raw
        need(len(decoded) <= 4 * MIB, "CALIBRATION_KERNEL_CONFIG_LIMIT")
        for line in decoded.decode().splitlines():
            if line.startswith("CONFIG_") and "=" in line:
                key, value = line.split("=", 1)
                need(key not in config, "CALIBRATION_KERNEL_CONFIG_AMBIGUOUS")
                config[key] = value
            elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
                key = line[2:-11]
                need(key not in config, "CALIBRATION_KERNEL_CONFIG_AMBIGUOUS")
                config[key] = "n"
        selected = "\n".join(key + "=" + config.get(key, "UNKNOWN") for key in
            ("CONFIG_QUOTA", "CONFIG_QUOTACTL", "CONFIG_EXT4_FS", "CONFIG_QFMT_V2", "CONFIG_XFS_FS", "CONFIG_XFS_QUOTA")) + "\n"
        config_record = {"path": str(path), "raw_sha256": digest(raw), "decoded_sha256": digest(decoded),
                         "selected_raw": selected, "selected_sha256": digest(selected.encode())}
        break
    filesystems_raw = Path("/proc/filesystems").read_bytes()
    filesystems = [line.split()[-1] for line in filesystems_raw.decode().splitlines() if line.split()]
    module = Path("/sys/module/quota_v2/initstate")
    module_live = module.exists() and module.read_text().strip() == "live"
    module_directory = Path("/usr/lib/modules") / release
    available = []
    for suffix in (".ko", ".ko.zst", ".ko.xz", ".ko.gz"):
        path = module_directory / ("kernel/fs/quota/quota_v2" + suffix)
        if path.is_file():
            available.append({"path": str(path), "identity": identity(path.stat()), "loaded_by_this_diagnostic": False})
    tools = {}
    for name in ("sudo", "unshare", "mkfs.ext4", "mkfs.xfs", "git", "cc"):
        found = shutil.which(name)
        if found:
            path = Path(found).resolve(strict=True)
            value = path.stat()
            if stat.S_ISREG(value.st_mode) and os.access(path, os.X_OK):
                tools[name] = {"path": str(path), "identity": identity(value), "code": readonly_system_code(path),
                               "version": "NO_VERIFICADO_UNTIL_OWNED_SETUP_QUERY"}
    resolved, seccomp_error = {}, None
    try:
        lib = ctypes.CDLL("libseccomp.so.2", use_errno=True)
        lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
        for name in (*DENIED_SYSCALLS, "ioctl", "prctl", "clone", "clone3"):
            resolved[name] = lib.seccomp_syscall_resolve_name(name.encode())
    except OSError as error:
        seccomp_error = error.errno
    result = evaluate_kernel_prerequisites(config, quota_module_live=module_live, filesystems=filesystems,
        tools=tools, seccomp_names_known=bool(resolved) and all(number >= 0 for number in resolved.values()))
    if mode == "bootstrap":
        for name in ("git", "cc"):
            if name not in tools:
                result["missing"].append("TOOL_MISSING:" + name)
        if result["missing"] and not result["unknown"]:
            result["status"] = "BLOQUEADO"
    result.update({"schema": "porota.rc6.readonly-kernel-quota-prerequisites.v1", "kernel_release": release,
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(), "config": config_record,
        "config_selected": {k: config.get(k, "UNKNOWN") for k in ("CONFIG_QUOTA", "CONFIG_QUOTACTL", "CONFIG_EXT4_FS", "CONFIG_QFMT_V2")},
        "filesystems": filesystems, "filesystems_raw_sha256": digest(filesystems_raw),
        "quota_v2_already_live": module_live, "quota_v2_module_files_metadata": available,
        "tools": tools, "seccomp_syscalls": resolved, "seccomp_load_errno": seccomp_error,
        "xfs_readonly_alternative": {"CONFIG_XFS_FS": config.get("CONFIG_XFS_FS", "UNKNOWN"),
            "CONFIG_XFS_QUOTA": config.get("CONFIG_XFS_QUOTA", "UNKNOWN"), "registered": "xfs" in filesystems,
            "mkfs_xfs_present": "mkfs.xfs" in tools, "mounted_or_selected": False}})
    return result


def decode_ext4_superblock(raw):
    need(type(raw) is bytes and len(raw) == 1024, "CALIBRATION_EXT4_SUPERBLOCK_LENGTH_REQUIRED")
    u32 = lambda offset: struct.unpack_from("<I", raw, offset)[0]
    need(struct.unpack_from("<H", raw, 0x38)[0] == 0xEF53, "CALIBRATION_EXT4_MAGIC_REQUIRED")
    flags, inodes = u32(0x64), u32(0)
    need(flags & 0x0100 and flags & 0x2000 and u32(0x18) == 2,
         "CALIBRATION_EXT4_PROJECT_QUOTA_FORMAT_REQUIRED")
    usr, grp, project = u32(0x240), u32(0x244), u32(0x26C)
    need(usr == 3 and grp == 4 and 11 <= project <= inodes,
         "CALIBRATION_EXT4_QUOTA_INODES_REQUIRED")
    return {"raw_sha256": digest(raw), "raw_base64": base64.b64encode(raw).decode(),
            "quota_feature": True, "project_feature": True, "block_bytes": 4096,
            "usr_quota_inum": usr, "grp_quota_inum": grp, "prj_quota_inum": project}


def own_image_superblock(request):
    fd = os.open(request["image"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        expected = request["image_identity"]
        need(all(getattr(before, key) == expected[key] for key in ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size"))
             and before.st_blocks * 512 >= request["limits"]["backing_image_bytes"],
             "CALIBRATION_OWN_BACKING_RESERVATION_RELEASED")
        raw = os.pread(fd, 1024, 1024)
        need(identity(before) == identity(os.fstat(fd)) == identity(Path(request["image"]).lstat()),
             "CALIBRATION_OWN_SUPERBLOCK_CHANGED")
        result = decode_ext4_superblock(raw)
        result["image_identity_after_nodiscard_mkfs"] = identity(before)
        return result
    finally:
        os.close(fd)


def clean_env(project):
    project = Path(project)
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(project / "home"),
            "TMPDIR": str(project / "tmp"), "TEMP": str(project / "tmp"), "TMP": str(project / "tmp"),
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "PIP_NO_CACHE_DIR": "1"}


def source_pin(root, sha, tree):
    need(re.fullmatch("[0-9a-f]{40}", sha or "") and re.fullmatch("[0-9a-f]{40}", tree or ""),
         "CALIBRATION_EXACT_SOURCE_REQUIRED")
    for args, expected in ((["rev-parse", "HEAD"], sha.encode()), (["rev-parse", "HEAD^{tree}"], tree.encode()),
                           (["status", "--porcelain", "--untracked-files=all"], b"")):
        row = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                             env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
        need(row.stdout.strip() == expected, "CALIBRATION_SOURCE_CHANGED")


def verify_admission(admission, binding, mode):
    gate = "capacity-probe" if mode == "capability" else "capacity-calibration"
    need(type(admission) is dict and admission.get("schema") == "porota.rc6.capacity-diagnostic-admission.v1"
         and admission.get("status") == "ADMITTED_DIAGNOSTIC_NOT_STARTED"
         and admission.get("gate") == gate and binding.get("producer") == gate
         and admission.get("source_sha") == binding.get("candidate_sha")
         and admission.get("source_tree") == binding.get("candidate_tree")
         and admission.get("owner_session") == binding.get("owner_id")
         and admission.get("qualification_claimed") is False and admission.get("material_gates") == []
         and admission.get("real_orders_sent") == 0 and type(admission.get("real_orders_sent")) is int
         and admission.get("mode") == "PRODUCTION_PAPER / SIMULATION"
         and admission.get("real_routes") == "NOT_CALLED" and admission.get("ppi_watch") == "UNTOUCHED"
         and admission.get("DEPLOY_OWNER") == "NOT_ACQUIRED", "CALIBRATION_ADMISSION_REBOUND")
    scope = admission.get("scope")
    need(type(scope) is dict and scope == {"schema": "porota.rc6.capacity-diagnostic-scope.v1", "mode": gate,
         **limits_for(mode), "financial_tick_allowed": False, "qualification_claimed": False}
         and all(type(scope[k]) is int for k in limits_for(mode)), "CALIBRATION_AUTHORIZED_QUOTA_REBOUND")
    need(binding.get("runner_class") == "github-hosted/ubuntu-24.04"
         and os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
         and os.environ.get("GITHUB_REPOSITORY") == "mbalbo2023/Porota-trading"
         and admission.get("actions_origin", {}).get("run", {}).get("id") == int(os.environ.get("GITHUB_RUN_ID", "0")),
         "CALIBRATION_ACTUAL_ACTIONS_REQUIRED")
    return admission


def validate_capability(receipt, origin, *, sha, tree, code_hashes, prerequisite):
    need(type(receipt) is dict and receipt.get("schema") == SCHEMA
         and receipt.get("mode") == "capability" and receipt.get("status") == "GREEN"
         and receipt.get("actual_capability_proved") is True and receipt.get("qualification_claimed") is False
         and receipt.get("source_sha") == sha and receipt.get("source_tree") == tree
         and receipt.get("code_hashes") == code_hashes and receipt.get("real_orders_sent") == 0,
         "CALIBRATION_AUTHENTICATED_CAPABILITY_REQUIRED")
    validate_limits("capability", receipt.get("limits"))
    need(type(origin) is dict and type(prerequisite) is dict
         and origin.get("schema") == "porota.rc6.capacity-capability-artifact-origin.v1"
         and origin.get("downloaded_exact_bytes_verified") is True
         and origin.get("original_outer_native_fin_verified") is True
         and origin.get("actual_outer_cleanup_verified") is True
         and origin.get("source_sha") == sha and origin.get("source_tree") == tree
         and re.fullmatch("[0-9a-f]{64}", receipt.get("read_contract_sha256", ""))
         and origin.get("read_contract_sha256") == receipt["read_contract_sha256"]
         and origin.get("producer_receipt_sha256") == digest(wire(receipt))
         and origin.get("source_code_hashes_verified") == code_hashes
         and origin.get("qualification_claimed") is False and type(origin.get("run_attempt")) is int
         and origin.get("run_attempt") == 1
         and origin.get("artifact_id") == prerequisite.get("artifact_id")
         and origin.get("run_id") == prerequisite.get("run_id")
         and origin.get("artifact_digest") == prerequisite.get("artifact_digest")
         and type(origin.get("artifact_id")) is int and origin["artifact_id"] > 0
         and type(origin.get("run_id")) is int and origin["run_id"] > 0
         and re.fullmatch("sha256:[0-9a-f]{64}", origin.get("artifact_digest", "")),
         "CALIBRATION_CAPABILITY_ARTIFACT_ORIGIN_REQUIRED")
    fin = receipt.get("kernel", {})
    need(fin.get("actual_child_reaped") is True and fin.get("kernel_pre_popen_echild_verified") is True
         and fin.get("owned_children_exhaustion_verified") is True
         and fin.get("process_group_absent_after_reap") is True and fin.get("remaining_owned_children") == []
         and receipt.get("cleanup", {}).get("namespace_removed") is True,
         "CALIBRATION_CAPABILITY_PHYSICAL_FIN_REQUIRED")


def seccomp_plan():
    return {"default": "ALLOW", "denied_syscalls": list(DENIED_SYSCALLS),
            "denied_ioctl_requests": [FSSETXATTR, *SETFLAGS],
            "denied_clone_namespace_flags": list(NAMESPACE_CLONE_FLAGS),
            "clone3_errno": errno.ENOSYS, "denied_prctl": [4, 8, 24, 28, 47],
            "no_new_privileges": True}


def install_seccomp():
    # libseccomp expands the native architecture's actual syscall numbers.
    lib = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    lib.seccomp_init.argtypes, lib.seccomp_init.restype = [ctypes.c_uint32], ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                                         ctypes.c_uint, ctypes.c_void_p]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    class Comparison(ctypes.Structure):
        _fields_ = [("arg", ctypes.c_uint), ("op", ctypes.c_uint), ("a", ctypes.c_uint64), ("b", ctypes.c_uint64)]
    context = lib.seccomp_init(0x7FFF0000)
    need(context, "CALIBRATION_NATIVE_SECCOMP_REQUIRED")
    try:
        def add(name, denied_errno=errno.EPERM, comparisons=()):
            number = lib.seccomp_syscall_resolve_name(name.encode())
            need(number >= 0, "CALIBRATION_SECCOMP_SYSCALL_UNKNOWN:" + name)
            values = (Comparison * len(comparisons))(*(Comparison(*x) for x in comparisons))
            need(lib.seccomp_rule_add_array(context, 0x00050000 | denied_errno, number, len(values), values) == 0,
                 "CALIBRATION_SECCOMP_RULE_FAILED")
        for name in DENIED_SYSCALLS:
            add(name)
        for request in (FSSETXATTR, *SETFLAGS):
            add("ioctl", comparisons=((1, 7, 0xFFFFFFFF, request),))
        for flag in NAMESPACE_CLONE_FLAGS:
            add("clone", comparisons=((0, 7, flag, flag),))
        add("clone3", errno.ENOSYS)
        for option in (4, 8, 24, 28, 47):
            add("prctl", comparisons=((0, 7, 0xFFFFFFFF, option),))
        need(lib.seccomp_load(context) == 0, "CALIBRATION_SECCOMP_LOAD_FAILED")
    finally:
        lib.seccomp_release(context)


def drop_privileges(uid, gid):
    need(type(uid) is int and uid > 0 and type(gid) is int and gid > 0
         and os.getuid() == os.geteuid() == 0 and len(os.listdir("/proc/self/task")) == 1,
         "CALIBRATION_ROOT_SETUP_SINGLE_THREAD_REQUIRED")
    libc = ctypes.CDLL(None, use_errno=True)
    for capability in range(64):
        if libc.prctl(24, capability, 0, 0, 0) != 0:
            need(ctypes.get_errno() == errno.EINVAL, "CALIBRATION_CAPABILITY_BOUNDING_DROP_FAILED")
    os.setgroups([])
    os.setresgid(gid, gid, gid)
    os.setresuid(uid, uid, uid)
    need(libc.prctl(4, 0, 0, 0, 0) == 0 and libc.prctl(38, 1, 0, 0, 0) == 0,
         "CALIBRATION_DUMPABLE_NNP_REQUIRED")
    install_seccomp()
    values = dict(x.split(":", 1) for x in Path("/proc/self/status").read_text().splitlines() if ":" in x)
    need(os.getresuid() == (uid, uid, uid) and os.getresgid() == (gid, gid, gid) and os.getgroups() == []
         and all(int(values[k].strip(), 16) == 0 for k in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"))
         and values["NoNewPrivs"].strip() == "1" and values["Seccomp"].strip() == "2",
         "CALIBRATION_REAL_PRIVILEGE_DROP_NOT_PROVED")
    return {"uid": uid, "gid": gid, "capabilities_zero": True, "no_new_privileges": True,
            "seccomp_mode": 2, "mount_namespace_inode": os.stat("/proc/self/ns/mnt").st_ino}


class LoopInfo(ctypes.Structure):
    _fields_ = [(x, ctypes.c_uint64) for x in ("device", "inode", "rdevice", "offset", "sizelimit")] + [
        (x, ctypes.c_uint32) for x in ("number", "encrypt_type", "encrypt_key_size", "flags")] + [
        ("file_name", ctypes.c_char * 64), ("crypt_name", ctypes.c_char * 64),
        ("encrypt_key", ctypes.c_char * 32), ("init", ctypes.c_uint64 * 2)]


class LoopConfig(ctypes.Structure):
    _fields_ = [("fd", ctypes.c_uint32), ("block_size", ctypes.c_uint32),
                ("info", LoopInfo), ("reserved", ctypes.c_uint64 * 8)]


def configure_loop(image, *, expected_identity=None, owned_image_fd=None, backing_origin=None):
    control, image_fd, loop_fd = None, owned_image_fd, None
    try:
        control = os.open("/dev/loop-control", os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        if image_fd is None:
            image_fd = os.open(image, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        if expected_identity is not None:
            need(identity(os.fstat(image_fd)) == expected_identity and os.fstat(image_fd).st_nlink == 1,
                 "CALIBRATION_HELD_IMAGE_IDENTITY_REBOUND")
        number = fcntl.ioctl(control, 0x4C82)
        need(type(number) is int and number >= 0, "CALIBRATION_FRESH_LOOP_REQUIRED")
        path = Path("/dev/loop" + str(number))
        loop_fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        need(stat.S_ISBLK(os.fstat(loop_fd).st_mode), "CALIBRATION_LOOP_BLOCK_DEVICE_REQUIRED")
        config = LoopConfig()
        config.fd, config.block_size, config.info.flags = image_fd, 4096, 4
        fcntl.ioctl(loop_fd, 0x4C0A, bytes(config))  # Atomic LOOP_CONFIGURE; EBUSY is RED.
        status = bytearray(ctypes.sizeof(LoopInfo))
        fcntl.ioctl(loop_fd, 0x4C05, status, True)
        actual, expected = LoopInfo.from_buffer_copy(status), os.fstat(image_fd)
        need(actual.device == expected.st_dev and actual.inode == expected.st_ino and actual.flags & 4,
             "CALIBRATION_LOOP_BACKING_IDENTITY_CHANGED")
        result = str(path), {"number": number, "backing_device": actual.device,
                            "backing_inode": actual.inode, "autoclear": True,
                            "backing_origin": backing_origin}, loop_fd
        loop_fd = None  # Ownership transfers until the own mount holds AUTOCLEAR.
        return result
    finally:
        if loop_fd is not None:
            os.close(loop_fd)
        if control is not None:
            os.close(control)
        if image_fd is not None:
            os.close(image_fd)


def set_project(path, project_id):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        current = bytearray(28)
        fcntl.ioctl(fd, FSGETXATTR, current, True)
        xflags, extsize, nextents, _, cow = struct.unpack("=5I8x", current)
        fcntl.ioctl(fd, FSSETXATTR, struct.pack("=5I8x", xflags | PROJINHERIT, extsize, nextents, project_id, cow))
    finally:
        os.close(fd)


class Dqblk(ctypes.Structure):
    _fields_ = [(x, ctypes.c_uint64) for x in ("bhard", "bsoft", "space", "ihard", "isoft", "inodes", "btime", "itime")] + [
        ("valid", ctypes.c_uint32)]


def native_operation(name, function, args, *, metadata):
    ctypes.set_errno(0)
    returned = function(*args)
    saved_errno = ctypes.get_errno()
    record = {"operation": name, "return": returned, "errno": saved_errno,
              "errno_name": errno.errorcode.get(saved_errno, "SUCCESS" if saved_errno == 0 else "UNKNOWN"),
              "actual_mount_namespace_inode": os.stat("/proc/self/ns/mnt").st_ino,
              "kernel_release": platform.release(), **metadata}
    if returned != 0:
        raise CalibrationOperationError("CALIBRATION_OWN_NATIVE_OPERATION_FAILED", record)
    return record


def mount_own_ext4(device, mountpoint, backing_origin):
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p]
    return native_operation("mount", libc.mount,
        (device.encode(), str(mountpoint).encode(), b"ext4", 2 | 4, b"prjquota"),
        metadata={"device": device, "target": str(mountpoint), "filesystem_type": "ext4",
                  "flags": 2 | 4, "options": "prjquota", "backing_origin": backing_origin,
                  "target_identity_before": identity(Path(mountpoint).lstat())})


def quota_operation(device, project_id, command, payload):
    libc = ctypes.CDLL(None, use_errno=True)
    libc.quotactl.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
    name = {Q_GETFMT: "Q_GETFMT", Q_SETQUOTA: "Q_SETQUOTA", Q_GETQUOTA: "Q_GETQUOTA"}[command]
    return native_operation("quotactl/" + name, libc.quotactl,
        ((command << 8) | PRJQUOTA, device.encode(), project_id, ctypes.byref(payload)),
        metadata={"command": (command << 8) | PRJQUOTA, "device": device, "project_id": project_id,
                  "quota_type": PRJQUOTA})


def quota_format(device):
    value = ctypes.c_uint32()
    record = quota_operation(device, 0, Q_GETFMT, value)
    need(value.value == FORMAT_VFS_V1, "CALIBRATION_NATIVE_PROJECT_QUOTA_FORMAT_REBOUND")
    return {**record, "format_id": value.value}


def get_quota(device, project_id, hard_bytes):
    actual = Dqblk()
    record = quota_operation(device, project_id, Q_GETQUOTA, actual)
    need(actual.bhard * 1024 == actual.bsoft * 1024 == hard_bytes
         and actual.ihard == actual.isoft == MAX_ENTRIES, "CALIBRATION_NATIVE_PROJECT_QUOTA_READBACK_FAILED")
    return {"project_id": project_id, "hard_bytes": actual.bhard * 1024, "hard_inodes": actual.ihard,
            "used_bytes": actual.space, "used_inodes": actual.inodes, "kernel_readback": True,
            "native_operation": record}


def set_quota(device, project_id, hard_bytes):
    need(type(hard_bytes) is int and hard_bytes > 0 and hard_bytes % 1024 == 0,
         "CALIBRATION_NATIVE_QUOTA_UNITS_REQUIRED")
    value = Dqblk()
    value.bhard, value.bsoft = hard_bytes // 1024, hard_bytes // 1024
    value.ihard, value.isoft, value.valid = MAX_ENTRIES, MAX_ENTRIES, 1 | 4
    record = quota_operation(device, project_id, Q_SETQUOTA, value)
    return {**get_quota(device, project_id, hard_bytes), "set_native_operation": record}


def require_physical_and_project_capacity(physical, quota, limit):
    need(type(quota) is dict and quota.get("kernel_readback") is True
         and all(type(quota.get(k)) is int for k in ("hard_bytes", "used_bytes", "hard_inodes", "used_inodes"))
         and quota["hard_bytes"] == limit and quota["hard_inodes"] == MAX_ENTRIES
         and 0 <= quota["used_bytes"] < limit and 0 <= quota["used_inodes"] < MAX_ENTRIES,
         "CALIBRATION_PROJECT_CAPACITY_BINDING_REQUIRED")
    # Physical reserve is outside the project limit. A statfs on a
    # PROJINHERIT directory would incorrectly hide that reserve behind B.
    require_capacity(physical, limit - quota["used_bytes"], control_bytes=MIB)


def live_project_quota(project, expected):
    observed = filesystem(project)
    need(observed["total_bytes"] == expected["hard_bytes"]
         and observed["total_inodes"] == expected["hard_inodes"] == MAX_ENTRIES,
         "CALIBRATION_LIVE_PROJECT_LIMIT_REBOUND")
    return {**expected, "used_bytes": expected["hard_bytes"] - observed["available_bytes"],
            "used_inodes": MAX_ENTRIES - observed["free_inodes"],
            "measurement_method": "KERNEL_PROJINHERIT_STATFS_PROJECTION",
            "live_projection": observed, "root_initial_readback_unchanged": True}


def mount_all_readonly(*, backing_origin=None, issuer_namespace_inode=None):
    # MOUNT_ATTR_RDONLY modifies namespace-local mounts, never a shared
    # superblock's SB_RDONLY. A generic mount -o remount,ro is forbidden here.
    class MountAttr(ctypes.Structure):
        _fields_ = [(x, ctypes.c_uint64) for x in ("set", "clear", "propagation", "userns_fd")]
    libc = ctypes.CDLL(None, use_errno=True)
    value = MountAttr(1, 0, 0, 0)
    need(platform.machine() == "x86_64", "CALIBRATION_NATIVE_MOUNT_SYSCALL_ARCH_REQUIRED")
    mountinfo = Path("/proc/self/mountinfo").read_text()
    metadata = {"operation": "mount_setattr", "syscall_number": 442, "dirfd": -100, "path": "/",
                "flags": 0x8000, "attributes": {"set": 1, "clear": 0, "propagation": 0, "userns_fd": 0},
                "attribute_size": ctypes.sizeof(value), "kernel_release": platform.release(),
                "actual_mount_namespace_inode": os.stat("/proc/self/ns/mnt").st_ino,
                "issuer_mount_namespace_inode": issuer_namespace_inode,
                "mountinfo_before_sha256": digest(mountinfo.encode()), "backing_origin": backing_origin}
    ctypes.set_errno(0)
    returned = libc.syscall(442, -100, b"/", 0x8000, ctypes.byref(value), ctypes.sizeof(value))
    actual_errno = ctypes.get_errno()  # Save immediately, before any metadata IO.
    metadata.update({"return": returned, "errno": actual_errno, "errno_name": errno.errorcode.get(actual_errno, "UNKNOWN")})
    if returned != 0:
        raise CalibrationOperationError("CALIBRATION_RECURSIVE_PRIVATE_READONLY_REQUIRED", metadata)
    return metadata


def validate_private_mountinfo(text):
    """Decode kernel mount metadata only; parsing never proves native isolation."""
    need(type(text) is str and text.strip(), "CALIBRATION_PRIVATE_MOUNTINFO_REQUIRED")
    ids = []
    for line in text.splitlines():
        fields = line.split()
        need(len(fields) >= 10 and fields.count("-") == 1 and fields[0].isdigit()
             and fields[1].isdigit(), "CALIBRATION_MOUNTINFO_MALFORMED")
        separator = fields.index("-")
        need(separator >= 6 and len(fields) == separator + 4, "CALIBRATION_MOUNTINFO_MALFORMED")
        need(not any(token.startswith(("shared:", "master:", "propagate_from:"))
                     for token in fields[6:separator]), "CALIBRATION_SHARED_PROPAGATION_VETO")
        ids.append(int(fields[0]))
    need(len(ids) == len(set(ids)) and all(identifier > 0 for identifier in ids),
         "CALIBRATION_MOUNTINFO_IDENTITY_AMBIGUOUS")
    return {"mount_count": len(ids), "shared_master_propagate_from_absent": True,
            "mountinfo_sha256": digest(text.encode())}


def private_mount_preflight(parent_namespace_inode):
    need(type(parent_namespace_inode) is int and parent_namespace_inode > 0,
         "CALIBRATION_PARENT_NAMESPACE_IDENTITY_REQUIRED")
    actual = os.stat("/proc/self/ns/mnt").st_ino
    need(actual != parent_namespace_inode, "CALIBRATION_NEW_ROOT_MOUNT_NAMESPACE_REQUIRED")
    result = validate_private_mountinfo(Path("/proc/self/mountinfo").read_text())
    result.update({"actual_mount_namespace_inode": actual, "issuer_mount_namespace_inode": parent_namespace_inode,
                   "kernel_namespace_differs": True, "before_any_mutation": True})
    return result


def setup_command(argv):
    timed_out = False
    try:
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=300)
        raw, returncode = result.stdout, result.returncode
    except subprocess.TimeoutExpired as error:
        # subprocess.run kills and waits for its own direct child. The actual
        # census below still refuses live or zombie descendants. The returned
        # prefix is preserved without claiming a complete timeout log.
        raw, returncode, timed_out = error.output or b"", None, True
    need(type(raw) is bytes, "CALIBRATION_SETUP_RAW_BYTES_REQUIRED")
    record = {"argv": argv, "returncode": returncode, "timed_out": timed_out,
              "raw_sha256": digest(raw), "raw_bytes": len(raw),
              "raw_base64": base64.b64encode(raw).decode() if len(raw) <= MIB else None,
              "raw_complete": not timed_out and len(raw) <= MIB,
              "actual_setup_waited_echild": False}
    try:
        os.waitid(os.P_ALL, 0, os.WNOHANG | os.WNOWAIT | os.WEXITED)
    except ChildProcessError as error:
        record["actual_setup_waited_echild"] = error.errno == errno.ECHILD
    else:
        record["actual_setup_waited_echild"] = False
    if not record["actual_setup_waited_echild"]:
        raise CalibrationOperationError("CALIBRATION_SETUP_CHILD_UNKNOWN", record)
    if not record["raw_complete"]:
        raise CalibrationOperationError("CALIBRATION_SETUP_TIMEOUT_RAW_PARTIAL" if timed_out else
                                        "CALIBRATION_OWN_SETUP_LOG_LIMIT", record)
    return record


def run_setup(request, argv):
    try:
        record = setup_command(argv)
    except CalibrationOperationError as error:
        if error.details.get("actual_setup_waited_echild") is True:
            request.setdefault("setup_commands", []).append(error.details)
        else:
            # Completed-command inventory cannot absorb an unknown child.
            request["setup_failure_control"] = error.details
        raise
    request.setdefault("setup_commands", []).append(record)
    if record["returncode"] != 0:
        raise CalibrationOperationError("CALIBRATION_OWN_SETUP_COMMAND_FAILED", record)
    return record


def validate_root_request(request):
    """Borrow custody from the actual issuer; this is no deletion authority."""
    need(type(request) is dict and request.get("schema") == "porota.rc6.capacity-root-request.v1",
         "CALIBRATION_AUTHENTIC_ROOT_REQUEST_REQUIRED")
    validate_limits(request.get("mode"), request.get("limits"))
    claim = request.get("namespace_receipt")
    need(type(claim) is dict and claim.get("schema") == "porota.rc6.generated-fixture-consumer-binding.v1"
         and claim.get("cleanup_authority_granted") is False and claim.get("binding") == request.get("binding")
         and type(request.get("owner_uid")) is int and request["owner_uid"] > 0
         and type(request.get("owner_gid")) is int and request["owner_gid"] > 0,
         "CALIBRATION_ROOT_NONROOT_ISSUER_REQUIRED")
    root = Path(claim.get("path", "")).absolute()
    need(str(root) == claim.get("path") and not any(p.is_symlink() for p in (root, *root.parents)),
         "CALIBRATION_ROOT_NAMESPACE_NOFOLLOW_REQUIRED")
    details = root.lstat()
    need(stat.S_ISDIR(details.st_mode) and details.st_uid == request["owner_uid"]
         and details.st_gid == request["owner_gid"] and stat.S_IMODE(details.st_mode) == 0o700
         and claim.get("identity") == [details.st_dev, details.st_ino, details.st_uid, details.st_gid, 0o700],
         "CALIBRATION_ROOT_NAMESPACE_REBOUND")
    marker_path = root / ".porota-generated-fixture-owner.json"
    marker_raw = read(marker_path, MIB)
    marker = decode(marker_raw)
    need(marker_path.lstat().st_uid == request["owner_uid"] and stat.S_IMODE(marker_path.lstat().st_mode) == 0o600
         and digest(marker_raw) == claim.get("marker_sha256")
         and marker.get("schema") == "porota.rc6.generated-fixture-owner.v1"
         and marker.get("runtime_paths_authorized") is False and marker.get("real_orders_sent") == 0
         and type(marker.get("real_orders_sent")) is int
         and marker.get("binding") == request["binding"] and marker.get("identity") == claim["identity"]
         and marker.get("path") == str(root) and marker.get("namespace_nonce") == claim.get("namespace_nonce")
         and marker.get("mount_id") == claim.get("mount_id")
         and re.fullmatch("[0-9a-f]{32}", claim.get("namespace_nonce", "")),
         "CALIBRATION_ROOT_ORIGINAL_MARKER_CHANGED")
    generated_name = "r6-" + base64.urlsafe_b64encode(bytes.fromhex(claim["namespace_nonce"])).decode().rstrip("=")
    need(root.name == generated_name, "CALIBRATION_ROOT_ORIGINAL_GENERATED_NAME_REQUIRED")
    binding = request["binding"]
    expected = {"candidate_sha", "candidate_tree", "producer", "attempt_id", "owner_id", "workload_fingerprint", "runner_class"}
    need(type(binding) is dict and set(binding) == expected
         and binding["candidate_sha"] == request["source_sha"] and binding["candidate_tree"] == request["source_tree"]
         and binding["producer"] == ("capacity-probe" if request["mode"] == "capability" else "capacity-calibration")
         and binding["runner_class"] == "github-hosted/ubuntu-24.04", "CALIBRATION_ROOT_BINDING_REBOUND")
    need(Path(request["image"]) == root / "image.ext4" and Path(request["mountpoint"]) == root / "mount",
         "CALIBRATION_ROOT_FOREIGN_RESOURCE_VETO")
    issuer_kernel_identity(request)
    return root


def issuer_kernel_identity(request):
    """Authenticate the real ancestor before borrowing its original mount."""
    issuer = request.get("issuer")
    need(type(issuer) is dict and type(issuer.get("pid")) is int and issuer["pid"] > 0
         and issuer.get("boot_id") == Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
         "CALIBRATION_ACTUAL_ISSUER_REQUIRED")
    pid, found = os.getppid(), False
    for _ in range(8):
        if pid == issuer["pid"]:
            found = True
            break
        if pid <= 1:
            break
        rows = Path("/proc/" + str(pid) + "/stat").read_text().rsplit(")", 1)[1].split()
        pid = int(rows[1])
    need(found, "CALIBRATION_ISSUER_NOT_ACTUAL_ANCESTOR")
    status = dict(line.split(":", 1) for line in Path("/proc/" + str(pid) + "/status").read_text().splitlines() if ":" in line)
    birth = Path("/proc/" + str(pid) + "/stat").read_text().rsplit(")", 1)[1].split()[19]
    need([int(x) for x in status["Uid"].split()] == [request["owner_uid"]] * 4
         and [int(x) for x in status["Gid"].split()] == [request["owner_gid"]] * 4
         and birth == issuer.get("start_ticks")
         and os.stat("/proc/" + str(pid) + "/ns/mnt").st_ino == request.get("mount_namespace_inode"),
         "CALIBRATION_ISSUER_KERNEL_IDENTITY_CHANGED")
    return {"pid": pid, "start_ticks": birth, "boot_id": issuer["boot_id"],
            "uids": [int(x) for x in status["Uid"].split()],
            "gids": [int(x) for x in status["Gid"].split()],
            "mount_namespace_inode": request["mount_namespace_inode"]}


def descriptor_mount_id(fd):
    rows = Path("/proc/self/fdinfo/" + str(fd)).read_text().splitlines()
    values = [int(row.split(":", 1)[1]) for row in rows if row.startswith("mnt_id:")]
    need(len(values) == 1 and values[0] > 0, "CALIBRATION_HELD_MOUNT_ID_REQUIRED")
    return values[0]


def open_image_on_issuer_mount(request, *, include_birth=False):
    """Only this authenticated proc root magic link may cross namespaces.

    Every ordinary component below that root uses a pinned NOFOLLOW dirfd.
    The loop's writable backing reference must belong to the issuer's original
    mount, rather than the cloned mount that will become read-only.
    """
    root = validate_root_request(request)
    issuer_before = issuer_kernel_identity(request)
    claim = request["namespace_receipt"]
    held, image_fd, marker_fd, birth_fd = [], None, None, None
    try:
        directory = os.open("/proc/" + str(issuer_before["pid"]) + "/root",
                            os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
        held.append(directory)
        for component in root.parts[1:]:
            directory = os.open(component, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                dir_fd=directory)
            held.append(directory)
        details = os.fstat(directory)
        need([details.st_dev, details.st_ino, details.st_uid, details.st_gid, stat.S_IMODE(details.st_mode)]
             == claim["identity"] and descriptor_mount_id(directory) == claim["mount_id"],
             "CALIBRATION_ISSUER_ORIGINAL_DIRECTORY_REBOUND")
        marker_name = ".porota-generated-fixture-owner.json"
        marker_fd = os.open(marker_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NOATIME,
                            dir_fd=directory)
        before = os.fstat(marker_fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= MIB
             and before.st_uid == request["owner_uid"] and before.st_gid == request["owner_gid"]
             and stat.S_IMODE(before.st_mode) == 0o600, "CALIBRATION_ISSUER_ORIGINAL_MARKER_CUSTODY_REQUIRED")
        marker_raw = b""
        while len(marker_raw) <= MIB:
            chunk = os.read(marker_fd, min(65536, MIB + 1 - len(marker_raw)))
            if not chunk:
                break
            marker_raw += chunk
        marker = decode(marker_raw)
        need(len(marker_raw) <= MIB and digest(marker_raw) == claim["marker_sha256"]
             and marker.get("namespace_nonce") == claim["namespace_nonce"]
             and marker.get("binding") == claim["binding"] and marker.get("mount_id") == claim["mount_id"]
             and identity(before) == identity(os.fstat(marker_fd))
             == identity(os.stat(marker_name, dir_fd=directory, follow_symlinks=False))
             == identity((root / marker_name).lstat()), "CALIBRATION_ISSUER_ORIGINAL_MARKER_CHANGED")
        image_fd = os.open("image.ext4", os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NOATIME,
                           dir_fd=directory)
        image_before = os.fstat(image_fd)
        image_mount = descriptor_mount_id(image_fd)
        need(stat.S_ISREG(image_before.st_mode) and image_before.st_nlink == 1
             and image_before.st_uid == request["owner_uid"] and image_before.st_gid == request["owner_gid"]
             and stat.S_IMODE(image_before.st_mode) == 0o600
             and image_before.st_size == request["limits"]["backing_image_bytes"]
             and identity(image_before) == request["image_identity"]
             == identity(os.stat("image.ext4", dir_fd=directory, follow_symlinks=False))
             == identity(Path(request["image"]).lstat()) and image_mount == claim["mount_id"],
             "CALIBRATION_ISSUER_ORIGINAL_IMAGE_REBOUND")
        if include_birth:
            need(Path(request["loop_birth_path"]) == root / "loop-birth.json",
                 "CALIBRATION_LOOP_BIRTH_FOREIGN_PATH_VETO")
            birth_fd = os.open("loop-birth.json", os.O_RDWR | os.O_NOFOLLOW | os.O_NOATIME | os.O_CLOEXEC,
                               dir_fd=directory)
            need(identity(os.fstat(birth_fd)) == request["loop_birth_identity"]
                 == identity(os.stat("loop-birth.json", dir_fd=directory, follow_symlinks=False))
                 and os.fstat(birth_fd).st_nlink == 1 and stat.S_IMODE(os.fstat(birth_fd).st_mode) == 0o600
                 and descriptor_mount_id(birth_fd) == claim["mount_id"]
                 and digest(os.pread(birth_fd, MIB + 1, 0)) == request["loop_birth_pending_sha256"],
                 "CALIBRATION_LOOP_BIRTH_ORIGINAL_CONTROL_REBOUND")
        need(validate_root_request(request) == root and issuer_kernel_identity(request) == issuer_before
             and identity(os.fstat(image_fd)) == request["image_identity"],
             "CALIBRATION_ISSUER_CHANGED_DURING_OPEN")
        origin = {"schema": "porota.rc6.original-issuer-backing.v1", "issuer": issuer_before,
                  "original_mount_id": image_mount, "original_directory_identity": claim["identity"],
                  "image_identity": request["image_identity"], "namespace_nonce": claim["namespace_nonce"],
                  "marker_sha256": claim["marker_sha256"], "marker_identity": identity(before),
                  "ordinary_components_nofollow": True, "issuer_revalidated_before_after": True}
        result, image_fd = image_fd, None
        if include_birth:
            birth, birth_fd = birth_fd, None
            return result, origin, birth
        return result, origin
    finally:
        if image_fd is not None:
            os.close(image_fd)
        if marker_fd is not None:
            os.close(marker_fd)
        if birth_fd is not None:
            os.close(birth_fd)
        for descriptor in reversed(held):
            os.close(descriptor)


def publish_loop_birth(fd, request, loop):
    need(identity(os.fstat(fd)) == request["loop_birth_identity"]
         and digest(os.pread(fd, MIB + 1, 0)) == request["loop_birth_pending_sha256"],
         "CALIBRATION_PENDING_LOOP_BIRTH_CHANGED")
    value = {"schema": "porota.rc6.authenticated-loop-birth.v1", "binding": request["binding"],
             "namespace_nonce": request["namespace_receipt"]["namespace_nonce"],
             "source_sha": request["source_sha"], "source_tree": request["source_tree"],
             "request_sha256": request["original_request_sha256"], "image_identity": request["image_identity"],
             "loop": loop, "actor_pid": os.getpid(), "actor_start_ticks": Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19],
             "actor_mount_namespace_inode": os.stat("/proc/self/ns/mnt").st_ino,
             "issuer_mount_namespace_inode": request["mount_namespace_inode"],
             "boot_id": request["issuer"]["boot_id"], "before_readonly_and_privilege_drop": True,
             "original_mount_id": descriptor_mount_id(fd), "qualification_claimed": False}
    raw = wire(value)
    need(len(raw) <= MIB, "CALIBRATION_LOOP_BIRTH_LIMIT")
    os.ftruncate(fd, len(raw))
    cursor = 0
    while cursor < len(raw):
        written = os.pwrite(fd, raw[cursor:], cursor)
        need(written > 0, "CALIBRATION_LOOP_BIRTH_SHORT_WRITE")
        cursor += written
    os.fsync(fd)
    need(os.pread(fd, len(raw) + 1, 0) == raw, "CALIBRATION_LOOP_BIRTH_READBACK_CHANGED")
    return {"path": request["loop_birth_path"], "sha256": digest(raw), "identity": identity(os.fstat(fd))}


def validate_loop_birth(value, request, request_sha256):
    claim, expected = request["namespace_receipt"], request["image_identity"]
    need(type(value) is dict and value.get("schema") == "porota.rc6.authenticated-loop-birth.v1"
         and value.get("binding") == request["binding"] and value.get("namespace_nonce") == claim["namespace_nonce"]
         and value.get("source_sha") == request["source_sha"] and value.get("source_tree") == request["source_tree"]
         and value.get("request_sha256") == request_sha256 and value.get("image_identity") == expected
         and type(value.get("image_identity")) is dict
         and all(type(v) is int for v in value["image_identity"].values())
         and type(value.get("original_mount_id")) is int
         and value.get("original_mount_id") == claim["mount_id"]
         and type(value.get("issuer_mount_namespace_inode")) is int
         and value.get("issuer_mount_namespace_inode") == request["mount_namespace_inode"]
         and type(value.get("actor_mount_namespace_inode")) is int
         and value["actor_mount_namespace_inode"] > 0
         and value["actor_mount_namespace_inode"] != request["mount_namespace_inode"]
         and type(value.get("actor_pid")) is int and value["actor_pid"] > 0
         and type(value.get("actor_start_ticks")) is str and value["actor_start_ticks"].isdigit()
         and int(value["actor_start_ticks"]) > 0
         and value.get("boot_id") == request["issuer"]["boot_id"]
         and value.get("before_readonly_and_privilege_drop") is True
         and value.get("qualification_claimed") is False, "CALIBRATION_LOOP_BIRTH_BINDING_REBOUND")
    loop = value.get("loop")
    need(type(loop) is dict and type(loop.get("number")) is int and loop["number"] >= 0
         and type(loop.get("backing_device")) is int and type(loop.get("backing_inode")) is int
         and loop.get("backing_device") == expected["st_dev"] and loop.get("backing_inode") == expected["st_ino"]
         and loop.get("autoclear") is True and type(loop.get("backing_origin")) is dict
         and loop["backing_origin"].get("image_identity") == expected
         and loop["backing_origin"].get("original_mount_id") == claim["mount_id"]
         and loop["backing_origin"].get("namespace_nonce") == claim["namespace_nonce"]
         and loop["backing_origin"].get("marker_sha256") == claim["marker_sha256"],
         "CALIBRATION_LOOP_BIRTH_NATIVE_READBACK_REBOUND")
    return loop


def read_loop_birth(request, request_sha256):
    path = Path(request["loop_birth_path"])
    before = path.lstat()
    expected = request["loop_birth_identity"]
    need(all(getattr(before, k) == expected[k] for k in ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink")),
         "CALIBRATION_LOOP_BIRTH_INODE_CHANGED")
    raw = read(path, MIB)
    return validate_loop_birth(decode(raw), request, request_sha256), digest(raw)


def root_setup(request):
    request["setup_stage"] = "authenticate_root_request"
    validate_root_request(request)
    need(os.getuid() == os.geteuid() == 0 and request["owner_uid"] > 0
         and request["mount_namespace_inode"] != os.stat("/proc/self/ns/mnt").st_ino,
         "CALIBRATION_NEW_ROOT_MOUNT_NAMESPACE_REQUIRED")
    request["private_mount_preflight"] = private_mount_preflight(request["mount_namespace_inode"])
    limits = validate_limits(request["mode"], request["limits"])
    image, mountpoint = Path(request["image"]), Path(request["mountpoint"])
    need(identity(image.lstat()) == request["image_identity"] and image.parent == mountpoint.parent
         and image.lstat().st_uid == request["owner_uid"] and image.lstat().st_nlink == 1
         and image.lstat().st_size == limits["backing_image_bytes"]
         and not image.is_symlink() and mountpoint.lstat().st_uid == request["owner_uid"]
         and stat.S_IMODE(mountpoint.lstat().st_mode) == 0o700 and not list(mountpoint.iterdir()),
         "CALIBRATION_ROOT_OWN_IMAGE_CUSTODY_REQUIRED")
    source = Path(request["source_root"]).absolute()
    need(not source.is_relative_to(image.parent) and not image.parent.is_relative_to(source),
         "CALIBRATION_IMAGE_SOURCE_OVERLAP")
    need(source == ROOT and type(request.get("code_hashes")) is dict
         and set(request["code_hashes"]) == set(CODE_MEMBERS), "CALIBRATION_ROOT_EXACT_SOURCE_PROGRAM_REQUIRED")
    for member, expected in request["code_hashes"].items():
        need(type(expected) is str and re.fullmatch("[0-9a-f]{64}", expected)
             and digest(read(source / member)) == expected, "CALIBRATION_ROOT_SOURCE_CHANGED")
        row = subprocess.run(["git", "-c", "safe.directory=" + str(source), "-C", str(source),
                              "show", request["source_sha"] + ":" + member],
                             env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}, capture_output=True, check=True, timeout=60)
        need(digest(row.stdout) == expected, "CALIBRATION_ROOT_PUBLISHED_PROGRAM_REBOUND")
    request["setup_stage"] = "open_original_issuer_backing"
    backing_fd, backing_origin, birth_fd = open_image_on_issuer_mount(request, include_birth=True)
    request["backing_origin"] = backing_origin
    request["setup_stage"] = "configure_own_autoclear_loop"
    try:
        device, loop, hold = configure_loop(image, expected_identity=request["image_identity"],
                                            owned_image_fd=backing_fd, backing_origin=backing_origin)
    except BaseException:
        os.close(birth_fd)
        raise
    request["loop"] = loop
    try:
        try:
            request["loop_birth_control"] = publish_loop_birth(birth_fd, request, loop)
        finally:
            os.close(birth_fd)
        request["setup_commands"] = []
        run_setup(request, ["mkfs.ext4", "-V"])
        request["setup_stage"] = "mkfs_own_ext4"
        run_setup(request, ["mkfs.ext4", "-q", "-F", "-b", "4096", "-m", "0", "-O", "project,quota",
                            "-E", "lazy_itable_init=0,lazy_journal_init=0,nodiscard", device])
        request["ext4_superblock"] = own_image_superblock(request)
        request["setup_stage"] = "mount_setattr_private_readonly"
        request["readonly_operation"] = mount_all_readonly(backing_origin=backing_origin,
                                    issuer_namespace_inode=request["mount_namespace_inode"])
        actual_image = image.lstat()
        need(actual_image.st_dev == request["image_identity"]["st_dev"]
             and actual_image.st_ino == request["image_identity"]["st_ino"]
             and actual_image.st_nlink == 1 and actual_image.st_uid == request["owner_uid"]
             and stat.S_IMODE(actual_image.st_mode) == 0o600
             and actual_image.st_size == limits["backing_image_bytes"], "CALIBRATION_IMAGE_ALIAS_OR_REBOUND")
        request["setup_stage"] = "mount_own_ext4"
        request["mount_operation"] = mount_own_ext4(device, mountpoint, backing_origin)
        request["quota_format"] = quota_format(device)
        os.chmod(mountpoint, 0o555)
        project, probe = mountpoint / "project", mountpoint / "edquot-probe"
        project.mkdir(mode=0o700)
        probe.mkdir(mode=0o700)
        project_id = 1 + int(request["namespace_receipt"]["namespace_nonce"][:7], 16)
        for path, number in ((project, project_id), (probe, project_id + 1)):
            set_project(path, number)
            os.chown(path, request["owner_uid"], request["owner_gid"])
        quotas = [set_quota(device, project_id, limits["project_hard_limit_bytes"]),
                  set_quota(device, project_id + 1, MIB)]
        inner = filesystem(mountpoint)
        require_physical_and_project_capacity(inner, quotas[0], limits["project_hard_limit_bytes"])
        request.update(project=str(project), probe=str(probe), loop=loop, quotas=quotas,
                       inner_before=inner, device=device)
        mounts = []
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            fields = line.split()
            mounts.append({"id": int(fields[0]), "mountpoint": fields[4], "readonly": "ro" in fields[5].split(","),
                           "options": fields[5].split(",")})
        need(all(row["readonly"] or row["mountpoint"] == str(mountpoint) for row in mounts)
             and sum(row["mountpoint"] == str(mountpoint) and not row["readonly"] for row in mounts) == 1,
             "CALIBRATION_OUTSIDE_MOUNTS_NOT_ALL_READONLY")
        need(all({"nodev", "nosuid"} <= set(row["options"]) for row in mounts if row["mountpoint"] == str(mountpoint)),
             "CALIBRATION_OWN_MOUNT_NODEV_NOSUID_NOT_PROVED")
        request["outside_mounts_readonly_verified"] = True
        if request["mode"] == "bootstrap":
            binaries = {}
            for name in ("git", "cc"):
                executable = shutil.which(name)
                need(executable, "CALIBRATION_ORIGINAL_NATIVE_TOOLCHAIN_MISSING")
                path = Path(executable).resolve(strict=True)
                code = readonly_system_code(path)
                binaries[name] = {"path": str(path), "sha256": code["sha256"],
                                  "identity": code["identity_after"], "code": code}
            request["toolchain_binaries"] = binaries
    finally:
        os.close(hold)
    # No writable raw device/image/root descriptor reaches NONROOT workloads.
    for name in os.listdir("/proc/self/fd"):
        if name.isdigit() and int(name) > 2:
            try:
                os.close(int(name))
            except OSError as error:
                need(error.errno == errno.EBADF, "CALIBRATION_PRIVILEGED_FD_CLOSE_FAILED")
    request["setup_stage"] = "drop_privileges"
    request["privilege"] = drop_privileges(request["owner_uid"], request["owner_gid"])
    request["setup_stage"] = "nonroot_ready"
    return request


def issuer_root_escape_probe(issuer_pid, outside):
    need(type(issuer_pid) is int and issuer_pid > 0 and issuer_pid != os.getpid()
         and Path(outside).is_absolute(), "CALIBRATION_ORIGINAL_ISSUER_PROBE_BINDING_REQUIRED")
    # The only attempted write is a fresh file in our own authenticated image
    # namespace. No issuer/foreign payload is read or a foreign path pruned.
    target = "/proc/" + str(issuer_pid) + "/root" + str(Path(outside)) + "/issuer-escape-must-not-exist"
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except OSError as error:
        need(error.errno == errno.EACCES, "CALIBRATION_ORIGINAL_ISSUER_ROOT_NOT_PROTECTED")
        return {"errno": error.errno, "denied": True, "issuer_pid": issuer_pid,
                "operation": "open_own_namespace_through_original_issuer_root"}
    else:
        os.close(descriptor)
        raise ValueError("CALIBRATION_ORIGINAL_ISSUER_ROOT_ESCAPE")


def quota_probe(project, probe, outside, supervisor_pid, issuer_pid):
    need(os.getuid() == os.geteuid() > 0, "CALIBRATION_NONROOT_PROBE_REQUIRED")
    observed = {}
    fd = os.open(project, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        before = bytearray(28)
        fcntl.ioctl(fd, FSGETXATTR, before, True)
        xflags, extsize, nextents, projid, cow = struct.unpack("=5I8x", before)
        need(projid > 0 and xflags & PROJINHERIT, "CALIBRATION_PROJECT_INHERITANCE_REQUIRED")
        for label, request, payload in (
            ("project_id_change", FSSETXATTR, struct.pack("=5I8x", xflags, extsize, nextents, 0, cow)),
            ("inheritance_clear", FSSETXATTR, struct.pack("=5I8x", xflags & ~PROJINHERIT, extsize, nextents, projid, cow)),
            ("setflags", SETFLAGS[0], struct.pack("=Q", 0))):
            try:
                fcntl.ioctl(fd, request, payload)
            except OSError as error:
                need(error.errno == errno.EPERM, "CALIBRATION_PROJECT_ESCAPE_NOT_SECCOMP_DENIED")
                observed[label] = {"errno": error.errno, "denied": True}
            else:
                raise ValueError("CALIBRATION_PROJECT_QUOTA_ESCAPE")
        after = bytearray(28)
        fcntl.ioctl(fd, FSGETXATTR, after, True)
        need(before == after, "CALIBRATION_PROJECT_ATTRIBUTES_CHANGED")
        libc = ctypes.CDLL(None, use_errno=True)
        for label, number, argument, payload in (
            ("high32_project_id_change", 16, FSSETXATTR | (1 << 32),
             struct.pack("=5I8x", xflags, extsize, nextents, 0, cow)),
            ("high32_setflags", 16, SETFLAGS[0] | (1 << 32), struct.pack("=Q", 0))):
            buffer = ctypes.create_string_buffer(payload)
            ctypes.set_errno(0)
            returned = libc.syscall(number, fd, ctypes.c_ulong(argument), ctypes.byref(buffer))
            saved_errno = ctypes.get_errno()
            need(returned == -1 and saved_errno == errno.EPERM, "CALIBRATION_LOW32_IOCTL_ESCAPE_NOT_DENIED")
            observed[label] = {"return": returned, "errno": saved_errno, "denied": True}
        ctypes.set_errno(0)
        returned = libc.syscall(157, ctypes.c_ulong(4 | (1 << 32)), 1, 0, 0, 0)
        saved_errno = ctypes.get_errno()
        need(returned == -1 and saved_errno == errno.EPERM, "CALIBRATION_LOW32_PRCTL_ESCAPE_NOT_DENIED")
        observed["high32_prctl"] = {"return": returned, "errno": saved_errno, "denied": True}
        final = bytearray(28)
        fcntl.ioctl(fd, FSGETXATTR, final, True)
        need(before == final, "CALIBRATION_HIGH32_PROJECT_ATTRIBUTES_CHANGED")
    finally:
        os.close(fd)
    filename = Path(probe) / "hard-quota.bin"
    fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        try:
            os.posix_fallocate(fd, 0, MIB + 4096)
        except OSError as error:
            need(error.errno == errno.EDQUOT, "CALIBRATION_NATIVE_EDQUOT_REQUIRED")
            observed["edquot"] = {"errno": error.errno, "allocated_bytes": os.fstat(fd).st_blocks * 512,
                                  "probe_limit_bytes": MIB, "actual_positive": True}
        else:
            raise ValueError("CALIBRATION_QUOTA_NOT_ENFORCED")
    finally:
        os.close(fd)
    try:
        fd = os.open(Path(outside) / "escape-must-not-exist", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError as error:
        need(error.errno == errno.EROFS, "CALIBRATION_OUTSIDE_WRITE_NOT_READONLY")
        observed["outside_write"] = {"errno": error.errno, "readonly": True}
    else:
        os.close(fd)
        raise ValueError("CALIBRATION_OUTSIDE_WRITE_ESCAPE")
    need(type(supervisor_pid) is int and supervisor_pid > 0 and supervisor_pid != os.getpid(),
         "CALIBRATION_OWN_SUPERVISOR_PID_REQUIRED")
    try:
        descriptor = os.open("/proc/" + str(supervisor_pid) + "/fd/1", os.O_WRONLY | os.O_CLOEXEC)
    except OSError as error:
        need(error.errno == errno.EACCES, "CALIBRATION_OUTSIDE_CONTROL_FD_NOT_PROTECTED")
        observed["supervisor_fd_escape"] = {"errno": error.errno, "denied": True}
    else:
        os.close(descriptor)
        raise ValueError("CALIBRATION_OUTSIDE_CONTROL_FD_ESCAPE")
    observed["original_issuer_root_escape"] = issuer_root_escape_probe(issuer_pid, outside)
    quota = Dqblk()
    libc = ctypes.CDLL(None, use_errno=True)
    need(libc.quotactl((Q_SETQUOTA << 8) | PRJQUOTA, b"/dev/quota-probe-denied", projid, ctypes.byref(quota)) == -1
         and ctypes.get_errno() == errno.EPERM, "CALIBRATION_QUOTA_MUTATION_NOT_DENIED")
    observed["quota_mutation"] = {"errno": errno.EPERM, "denied": True}
    return {"actual_positive": True, "same_uid": os.geteuid(), "checks": observed,
            "project_attributes_unchanged": True, "source_financial_code_called": False}


def inventory(project, *, readonly_roots=()):
    project = Path(project)
    device = project.lstat().st_dev
    allocated, entries, seen, symlinks = 0, 0, set(), []
    for parent, directories, files in os.walk(project, followlinks=False):
        for path in (Path(parent), *[Path(parent) / x for x in files],
                     *[Path(parent) / x for x in directories if (Path(parent) / x).is_symlink()]):
            value = path.lstat()
            need(value.st_dev == device, "CALIBRATION_PROJECT_FOREIGN_MOUNT")
            if stat.S_ISLNK(value.st_mode):
                # Lexical classification only: never stat/read its target.
                target = Path(os.path.normpath(str(path.parent / os.readlink(path))))
                internal = target.is_relative_to(project)
                readonly = any(target.is_relative_to(Path(root).absolute()) for root in readonly_roots)
                need(internal or readonly, "CALIBRATION_SYMBOLIC_REFERENCE_NOT_OWN_OR_PINNED_READONLY")
                symlinks.append({"path": str(path.relative_to(project)), "target": os.readlink(path),
                                 "target_kind": "OWN_PROJECT" if internal else "PINNED_READONLY_CODE",
                                 "target_followed": False})
            key = (value.st_dev, value.st_ino)
            entries += 1
            if key not in seen:
                seen.add(key)
                allocated += value.st_blocks * 512
    need(entries <= MAX_ENTRIES, "CALIBRATION_RETAINED_ENTRIES_EXCEEDED")
    return {"allocated_bytes": allocated, "retained_entries": entries,
            "monotonic_ns": time.monotonic_ns(), "measurement_kind": "OBSERVED_ALLOCATED_HIGH_WATER",
            "symlinks": symlinks, "symbolic_targets_followed": False}


def verify_python(path, epoch):
    path = Path(path).absolute()
    cache = Path(os.environ.get("RUNNER_TOOL_CACHE", "/opt/hostedtoolcache")).absolute()
    expected = cache / "Python" / VERSIONS[epoch] / ("x64/bin/python3.11" if epoch == "311" else "x64/bin/python3.12")
    need(path == expected and path.is_file() and not any(p.is_symlink() for p in path.parents),
         "CALIBRATION_PREINSTALLED_PINNED_PYTHON_REQUIRED")
    need(not path.is_symlink() and stat.S_ISREG(path.lstat().st_mode) and os.access(path, os.X_OK),
         "CALIBRATION_PINNED_REGULAR_ELF_REQUIRED")
    resolved = path.resolve(strict=True)
    need(resolved.is_relative_to(expected.parents[1]) and resolved.is_file(), "CALIBRATION_PYTHON_ALIAS_ESCAPE")
    return str(path)


def validate_product_closure(packages, policy):
    normalize = lambda name: re.sub(r"[-_.]+", "-", name.lower())
    need(type(packages) is list and all(type(row) is list and len(row) == 2
         and all(type(x) is str and x for x in row) for row in packages),
         "CALIBRATION_PRODUCT157_METADATA_REQUIRED")
    expected = {normalize(row["name"]): row["version"] for row in policy["packages"] + policy["build_tools"]}
    observed = {normalize(name): version for name, version in packages}
    need(len(expected) == len(observed) == len(packages) == 157 and expected == observed,
         "CALIBRATION_PRODUCT157_NAMES_VERSIONS_REBOUND")
    need(policy["allowed_sdists"] == ["msgpack", "ppi-client", "signalrcoreppi", "ta"],
         "CALIBRATION_ORIGINAL_FOUR_SDISTS_REQUIRED")
    return True


def bootstrap(request, run):
    project, source = Path(request["project"]), Path(request["source_root"])
    interpreters = {epoch: verify_python(request["pythons"][epoch], epoch) for epoch in VERSIONS}
    # Check both exact interpreters BEFORE any SDK producer. No setup-python,
    # download, modified pins or more powerful runner is admitted here.
    for epoch, python in interpreters.items():
        row = run([python, "-I", "-B", "-c", "import platform;print(platform.python_version())"],
                  "interpreter" + epoch, 60)
        need(read(row["log"]).strip() == VERSIONS[epoch].encode(), "CALIBRATION_PYTHON_VERSION_REBOUND")
    objects_wire = read(source / OBJECTS_MEMBER)
    need(digest(objects_wire) == OBJECTS_SHA256, "CALIBRATION_ORIGINAL19_CHANGED")
    objects = decode(objects_wire)["objects"]
    need(len(objects) == len({x["sha"] for x in objects}) == 19, "CALIBRATION_EXACT19_REQUIRED")
    results = {"PRODUCT157": {}, "fullGit": [], "original_objects": objects,
               "original_builder_sha256": BUILDER_SHA256,
               "authority": "AUTHENTIC_WIP_DIAGNOSTIC_ONLY_NOT_PR476_BUILDER_ADMISSION",
               "additional_seed_transport_measured_separately": True}
    results["toolchain_binaries"] = request["toolchain_binaries"]
    for name, flags in (("git", ["--version", "--build-options"]), ("cc", ["--version"])):
        row = run([request["toolchain_binaries"][name]["path"], *flags], "toolchain-" + name, 60)
        results["toolchain_binaries"][name]["native_version_raw_sha256"] = digest(read(row["log"]))
    need(digest(read(source / BUILDER_MEMBER)) == BUILDER_SHA256, "CALIBRATION_ORIGINAL_BUILDER_CHANGED")
    for epoch, base in interpreters.items():
        private = project / ("product" + epoch)
        private.mkdir(mode=0o700)
        temporary = private / "bootstrap-temp"
        temporary.mkdir(mode=0o700)
        venv = private / "venv"
        environment = {"TMPDIR": str(temporary), "TEMP": str(temporary), "TMP": str(temporary)}
        run([base, "-I", "-B", str(source / "scripts/rc6_material_environment.py"), "--root", str(venv),
             "--owner-uid", str(os.geteuid()), "--python-version", VERSIONS[epoch],
             "--receipt", str(private / "creation.json")], "venv" + epoch, 300, environment)
        python = str(venv / "bin/python")
        for n, flags in enumerate((
            ["--require-hashes", "--only-binary=:all:", "-r", str(source / "requirements.build.lock.txt")],
            ["--require-hashes", "--only-binary=:all:", "--no-binary=msgpack,ppi-client,signalrcoreppi,ta",
             "--no-build-isolation", "-r", str(source / "requirements.lock.txt")])):
            run([python, "-I", "-B", "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir", *flags],
                "locked" + epoch + "-" + str(n), 1800, environment)
        run([python, "-I", "-B", "-m", "pip", "check"], "pipcheck" + epoch, 300)
        check = "import importlib.metadata as m,json;print(json.dumps(sorted((d.metadata['Name'],d.version) for d in m.distributions())))"
        row = run([python, "-I", "-B", "-c", check], "closure" + epoch, 60)
        packages = decode(read(row["log"]))
        validate_product_closure(packages, decode(read(source / "ops/policy/rc6-supply-chain-v1.json")))
        results["PRODUCT157"][epoch] = {"packages": packages, "python": VERSIONS[epoch],
                                       "creation_receipt_sha256": digest(read(private / "creation.json")),
                                       "filesystem": inventory(private)}
    # This extra seed is declared, bounded and measured. The immutable builder
    # requires PR476 authority; WIP uses only its literal transport operations.
    seed = project / "fullGit-seed"
    run(["git", "clone", "--no-local", "--no-hardlinks", "--no-checkout", str(source), str(seed)], "seed-clone", 600)
    run(["git", "-C", str(seed), "config", "remote.origin.url", ORIGIN], "seed-origin", 60)
    shallow = run(["git", "-C", str(seed), "rev-parse", "--is-shallow-repository"], "seed-shallow", 60)
    # Canonical repository is PUBLIC. No issuer/control credential, askpass,
    # credential helper, private fallback or persisted authentication is used.
    git_env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_ASKPASS": "/bin/false", "SSH_ASKPASS": "/bin/false"}
    if read(shallow["log"]).strip() == b"true":
        run(["git", "-C", str(seed), "-c", "credential.helper=", "fetch", "--unshallow", "--no-tags",
             "--no-write-fetch-head", "--no-auto-maintenance", ORIGIN, request["source_sha"]], "seed-full-history", 1800, git_env)
    for n, obj in enumerate(objects):
        need(re.fullmatch("[0-9a-f]{40}", obj["sha"]), "CALIBRATION_ORIGINAL_SHA_INVALID")
        run(["git", "-C", str(seed), "-c", "credential.helper=", "fetch", "--no-tags", "--no-write-fetch-head",
             "--no-auto-maintenance", ORIGIN, obj["sha"] + ":" + obj["local_ref"]], "seed-object" + str(n), 300, git_env)
        row = run(["git", "-C", str(seed), "cat-file", "commit", obj["sha"]], "seed-object-check" + str(n), 60)
        raw = read(row["log"])
        need(digest(raw) == obj["raw_commit_sha256"]
             and hashlib.sha1(b"commit " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == obj["sha"]
             and raw.splitlines()[0] == b"tree " + obj["github_tree"].encode(), "CALIBRATION_LITERAL19_IDENTITY_CHANGED")
    results["seed_transport"] = inventory(seed)
    for epoch in VERSIONS:
        clone = project / ("fullSource" + epoch)
        run(["git", "clone", "--no-local", "--no-hardlinks", "--no-checkout", str(seed), str(clone)], "clone" + epoch, 600)
        run(["git", "-C", str(clone), "config", "remote.origin.url", ORIGIN], "origin" + epoch, 60)
        run(["git", "-C", str(clone), "checkout", "--detach", request["source_sha"]], "checkout" + epoch, 300)
        for n, obj in enumerate(objects):
            run(["git", "-C", str(clone), "fetch", "--no-tags", "--no-write-fetch-head", "--no-auto-maintenance",
                 str(seed), obj["sha"] + ":" + obj["local_ref"]], "original" + epoch + "-" + str(n), 300)
        run(["git", "-C", str(clone), "fsck", "--full", "--strict"], "fsck" + epoch, 600)
        row = run(["git", "-C", str(clone), "rev-parse", "HEAD", "HEAD^{tree}", "--is-shallow-repository"], "identity" + epoch, 60)
        need(read(row["log"]).splitlines() == [request["source_sha"].encode(), request["source_tree"].encode(), b"false"],
             "CALIBRATION_FULLGIT_SOURCE_REBOUND")
        need(not (clone / ".git/objects/info/alternates").exists(), "CALIBRATION_GIT_ALTERNATES_FORBIDDEN")
        results["fullGit"].append({"epoch": epoch, "source_sha": request["source_sha"], "source_tree": request["source_tree"],
                                   "exact_original19": True, "filesystem": inventory(clone)})
    return results


def publish_bytes(path, raw, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def finalize_nonroot_report(request, report, manager):
    """Preserve failure RAW while the private mount still exists, after FIN."""
    report["required_raw_complete"] = False
    if report.get("producer_state") not in ("NONE_STARTED", "FIN_CLOSED"):
        report["actual_own_fin_closed"] = False
        report["raw_capture_reason"] = "CALIBRATION_PRODUCER_FIN_UNKNOWN"
        return report
    try:
        report["final_own_kernel"] = manager["pre_capture_kernel_state"]()
        report["actual_own_fin_closed"] = True
        project = Path(request["project"])
        raw_files, total = [], 0
        for path in sorted((project / "controls").iterdir()):
            need(len(raw_files) < MAX_RAW_FILES, "CALIBRATION_REQUIRED_RAW_FILE_COUNT")
            raw = read(path, MAX_RAW)
            total += len(raw)
            need(total <= MAX_RAW, "CALIBRATION_REQUIRED_RAW_LIMIT")
            raw_files.append({"path": path.name, "bytes": len(raw), "sha256": digest(raw),
                              "raw_base64": base64.b64encode(raw).decode()})
        expected = {name for row in report["commands"] for name in (Path(row["log"]).name, row["label"] + ".kernel.json")}
        need(expected <= {row["path"] for row in raw_files}, "CALIBRATION_FIRST_FAILURE_REQUIRED_RAW_MISSING")
        report.update(raw_files=raw_files, raw_total_bytes=total, required_raw_complete=True)
    except BaseException as error:
        report["raw_capture_reason"] = str(error).split(":", 1)[0]
    return report


def nonroot_worker(request):
    project, source = Path(request["project"]), Path(request["source_root"])
    need(os.getuid() == os.geteuid() == request["owner_uid"] > 0, "CALIBRATION_BACKEND_NONROOT_REQUIRED")
    need(digest(read(source / DRIVER_MEMBER)) == DRIVER_SHA256, "CALIBRATION_ORIGINAL_MANAGER_CHANGED")
    manager = runpy.run_path(str(source / DRIVER_MEMBER))
    manager["pre_capture_kernel_state"]()
    report = {"status": "BLOQUEADO", "actual_capability_proved": False, "commands": [], "samples": [],
              "producer_state": "NONE_STARTED", "actual_own_fin_closed": True, "required_raw_complete": False,
              "qualification_claimed": False}
    request["partial_nonroot_report"] = report
    request["nonroot_manager"] = manager
    for name in ("home", "tmp", "controls"):
        (project / name).mkdir(mode=0o700)
    os.chdir(project)
    os.umask(0o022)
    records, samples = report["commands"], report["samples"]
    samples.append(inventory(project))
    def run(argv, label, seconds, extra=None):
        need(re.fullmatch("[A-Za-z0-9_-]{1,80}", label), "CALIBRATION_COMMAND_LABEL_REQUIRED")
        # The hard quota is rechecked by its immutable readback and enforced
        # live by the kernel; capacity is measured immediately before producer.
        image_value = Path(request["image"]).lstat()
        need(image_value.st_blocks * 512 >= request["limits"]["backing_image_bytes"]
             and image_value.st_dev == request["image_identity"]["st_dev"]
             and image_value.st_ino == request["image_identity"]["st_ino"]
             and image_value.st_nlink == 1, "CALIBRATION_LIVE_BACKING_RESERVATION_RELEASED")
        outer = filesystem(Path(request["image"]).parent)
        require_capacity(outer, request["outer_control_peak_bound"]["total_bytes"])
        physical, quota = filesystem(Path(request["mountpoint"])), live_project_quota(project, request["quotas"][0])
        require_physical_and_project_capacity(physical, quota, request["limits"]["project_hard_limit_bytes"])
        log = project / "controls" / (label + ".log")
        report["producer_state"] = "LAUNCHING"
        report["actual_own_fin_closed"] = False
        kernel = manager["managed_native_child"](argv, project, log, {**clean_env(project), **(extra or {})}, seconds,
            terminate_grace=2, progress_poll=5, progress=lambda *_: samples.append(inventory(project)))
        records.append({"label": label, "argv": argv, "kernel": kernel, "log": str(log),
                        "capacity_before": {"outer": outer, "physical": physical, "project_quota": quota}})
        need(manager["managed_custody_closed"](kernel), "CALIBRATION_NATIVE_FIN_UNKNOWN")
        manager["pre_capture_kernel_state"]()
        report["producer_state"] = "FIN_CLOSED"
        report["actual_own_fin_closed"] = True
        publish(project / "controls" / (label + ".kernel.json"), kernel)
        samples.append(inventory(project))
        need(manager["managed_phase_green"](kernel)
             and 0 <= kernel.get("termination_reap_restore_cleanup_seconds", math.inf) <= 5,
             "CALIBRATION_NATIVE_COMMAND_RED")
        need(type(kernel.get("peak_rss_bytes")) is int and 0 <= kernel["peak_rss_bytes"] < 2 * GIB,
             "CALIBRATION_NATIVE_MEMORY_BOUND_EXCEEDED")
        return records[-1]
    probe = run([sys.executable, "-I", "-B", str(source / "scripts/rc6_capacity_calibration.py"), "--probe-child",
                 "--project", str(project), "--probe", request["probe"], "--outside", request["namespace_receipt"]["path"],
                 "--supervisor-pid", str(os.getpid()), "--issuer-pid", str(request["issuer"]["pid"])],
                "capability", 60)
    positive = decode(read(probe["log"]))
    need(positive.get("actual_positive") is True, "CALIBRATION_REAL_CAPABILITY_NOT_PROVED")
    report.update(actual_capability_proved=True, capability=positive)
    build = bootstrap(request, run) if request["mode"] == "bootstrap" else None
    report.update({"status": "GREEN", "actual_capability_proved": True, "capability": positive,
            "privilege": request["privilege"], "loop": request["loop"], "quotas": request["quotas"],
            "inner_before": request["inner_before"], "inner_after": filesystem(Path(request["mountpoint"])),
            "project_quota_after": live_project_quota(project, request["quotas"][0]),
            "setup_commands": request["setup_commands"], "source_sha": request["source_sha"],
            "backing_origin": request["backing_origin"], "readonly_operation": request["readonly_operation"],
            "mount_operation": request["mount_operation"], "quota_format": request["quota_format"],
            "ext4_superblock": request["ext4_superblock"], "loop_birth_control": request["loop_birth_control"],
            "setup_stage": request["setup_stage"],
            "private_mount_preflight": request["private_mount_preflight"],
            "source_tree": request["source_tree"],
            "commands": records, "samples": samples,
            "measured_high_water": max(samples, key=lambda x: x["allocated_bytes"]),
            "absolute_enforced_project_bound_bytes": request["limits"]["project_hard_limit_bytes"],
            "temporal_peak_exact_claimed": False, "bootstrap": build,
            "outside_mounts_readonly_verified": request["outside_mounts_readonly_verified"],
            "qualification_claimed": False})
    finalize_nonroot_report(request, report, manager)
    need(report["required_raw_complete"] and report["actual_own_fin_closed"], "CALIBRATION_REQUIRED_RAW_NOT_SEALED")
    return report


def root_worker(request_path):
    request_raw = read(request_path, 4 * MIB)
    request = decode(request_raw)
    request["original_request_sha256"] = digest(request_raw)
    try:
        prepared = root_setup(request)
        report = nonroot_worker(prepared)
    except BaseException as error:
        report = {"status": "BLOQUEADO", "actual_capability_proved": False,
                  "reason": "CALIBRATION_NATIVE_OSERROR" if isinstance(error, OSError) else str(error).split(":", 1)[0],
                  "class": type(error).__name__, "qualification_claimed": False, "loop": request.get("loop"),
                  "setup_stage": request.get("setup_stage"), "setup_commands": request.get("setup_commands", []),
                  "private_mount_preflight": request.get("private_mount_preflight"),
                  "backing_origin": request.get("backing_origin"),
                  "readonly_operation": request.get("readonly_operation"),
                  "operation_failure": getattr(error, "details", None),
                  "oserror_errno": error.errno if isinstance(error, OSError) else None}
        for name in ("loop_birth_control", "mount_operation", "quota_format", "ext4_superblock", "quotas", "inner_before", "privilege"):
            report[name] = request.get(name)
        partial = request.get("partial_nonroot_report")
        if partial is not None:
            partial = finalize_nonroot_report(request, partial, request["nonroot_manager"])
            report.update({k: v for k, v in partial.items() if k not in ("status", "reason", "qualification_claimed")})
            report["status"] = "BLOQUEADO"
        else:
            try:
                os.waitid(os.P_ALL, 0, os.WNOHANG | os.WNOWAIT | os.WEXITED)
            except ChildProcessError as census_error:
                setup_closed = census_error.errno == errno.ECHILD
            else:
                setup_closed = False
            raw_complete = not request.get("setup_failure_control") and all(
                row.get("raw_complete") is True for row in request.get("setup_commands", []))
            report.update(actual_own_fin_closed=setup_closed, required_raw_complete=setup_closed and raw_complete,
                          raw_files=[], raw_total_bytes=0, raw_scope="SETUP_CONTROLS_ONLY_NO_NONROOT_PRODUCER_LAUNCHED",
                          actual_setup_kernel_echild_verified=setup_closed)
    need(len(wire({k: v for k, v in report.items() if k != "raw_files"})) <= 4 * MIB,
         "CALIBRATION_WORKER_CONTROL_METADATA_LIMIT")
    raw = wire({"schema": "porota.rc6.capacity-worker.v1", "request_sha256": digest(request_raw),
                "report": report})
    need(len(raw) <= MAX_WORKER_WIRE, "CALIBRATION_WORKER_REPORT_LIMIT")
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()
    return 0 if report["status"] == "GREEN" else 1


def loop_gone(loop):
    need(type(loop) is dict and type(loop.get("number")) is int and loop["number"] >= 0
         and loop.get("autoclear") is True, "CALIBRATION_LOOP_CONTROL_MISSING")
    path = Path("/sys/block/loop" + str(loop["number"]) + "/loop/backing_file")
    # An already reused or still bound device is never detached by inference.
    need(not path.exists(), "CALIBRATION_LOOP_CONSUMER_OR_REUSE_UNKNOWN")
    return {"original_autoclear_loop_absent": True, "global_loop_cleanup_attempted": False}


def attach_issuer_evidence(report, progress):
    if hasattr(progress, "receipt"):
        report["issuer_lease"] = progress.receipt
        try:
            report["issuer_evidence_refs"] = progress.evidence_refs
        except (ValueError, OSError, AttributeError) as error:
            # The monitor refuses documentary reads while own native FIN is
            # unknown. Preserve that refusal; never use reporting to bypass it.
            report["issuer_evidence_state"] = "NO_VERIFICADO_NOT_READ"
            report["issuer_evidence_reason"] = str(error).split(":", 1)[0]
            report["status"] = "BLOQUEADO"
            report["payload_upload_safe"] = False


def main(args, *, progress=None):
    need(os.getuid() == os.geteuid() > 0 and sys.platform == "linux" and platform.machine() == "x86_64",
         "CALIBRATION_ACTIONS_NONROOT_LINUX_REQUIRED")
    sys.path.insert(0, str(ROOT))
    from scripts import rc6_authenticated_fixture_lifecycle as owned
    binding = decode(read(args.binding_json))
    claim = decode(read(args.namespace_receipt))
    parent = owned.validate_consumer_receipt(claim, candidate_sha=args.source_sha, candidate_tree=args.source_tree)
    need(claim["binding"] == binding, "CALIBRATION_NAMESPACE_BINDING_REBOUND")
    admission_path = os.environ.get("RC6_CALIBRATION_ADMISSION_JSON")
    need(admission_path, "CALIBRATION_FRESH_ADMISSION_REQUIRED")
    admission = verify_admission(decode(read(admission_path)), binding, args.mode)
    source = args.source_root.absolute()
    source_pin(source, args.source_sha, args.source_tree)
    output = args.output.absolute()
    need(output.is_relative_to(parent) and output != parent and not output.exists(), "CALIBRATION_OWN_FRESH_OUTPUT_REQUIRED")
    output.mkdir(mode=0o700)
    code_hashes = {m: digest(read(source / m)) for m in CODE_MEMBERS}
    if progress is None:
        from scripts.rc6_owned_gate_lease import issuer_factory
        progress = issuer_factory(args, control_root=output / "owned-lease-controls")
    try:
        prerequisites = kernel_prerequisites(args.mode)
    except (ValueError, OSError, EOFError, UnicodeError) as error:
        prerequisites = {"schema": "porota.rc6.readonly-kernel-quota-prerequisites.v1", "status": "NO_VERIFICADO",
            "actual_capability_proved": False, "qualification_claimed": False, "kernel_unsupported_claimed": False,
            "module_loading_attempted": False, "unknown": ["READONLY_METADATA_NOT_VERIFIABLE"], "missing": [],
            "error_class": type(error).__name__, "errno": getattr(error, "errno", None),
            "reason": str(error).split(":", 1)[0]}
    publish(output / "kernel-prerequisites.json", prerequisites)
    # The NONROOT issuer's ability to signal a pre-drop ROOT actor has not
    # been proved. Metadata readiness cannot confer that authority. There is
    # no flag, environment or admission bool that enables this launch.
    signal_custody = dict(PRIVILEGED_SIGNAL_CUSTODY)
    if (prerequisites["status"] != "PREREQUISITES_PRESENT" or signal_custody["proved"] is not True
            or signal_custody["privileged_launch_authorized"] is not True):
        source_pin(source, args.source_sha, args.source_tree)
        early = {"schema": SCHEMA, "mode": args.mode, "source_sha": args.source_sha, "source_tree": args.source_tree,
                 "binding": binding, "namespace_receipt": claim, "code_hashes": code_hashes,
                 "read_contract_sha256": admission["read_contract_sha256"], "limits": limits_for(args.mode),
                 "status": "BLOQUEADO", "actual_capability_proved": False, "qualification_claimed": False,
                 "reason": "CALIBRATION_PRIVILEGED_SIGNAL_CUSTODY_UNVERIFIED",
                 "privileged_signal_custody": signal_custody,
                 "G0_G8_qualification_claimed": False, "generation": "NOT_STARTED", "inner_namespace_created": False,
                 "inner_generation_operations": {"create_namespace": "NOT_CALLED", "image_allocation": "NOT_CALLED",
                                                 "root_worker_launch": "NOT_CALLED"},
                 "kernel_prerequisites": prerequisites, "kernel_prerequisites_ref": {
                     "path": "kernel-prerequisites.json", "sha256": digest(wire(prerequisites))},
                 "payload_upload_safe": True, "source_unchanged": True, "required_raw": [],
                 "cleanup": {"status": "NOT_STARTED", "namespace_removed": False, "actual_inner_FIN_claimed": False},
                 "real_orders_sent": 0, "real_routes": "NOT_CALLED", "ppi_watch": "UNTOUCHED", "DEPLOY_OWNER": "NOT_ACQUIRED"}
        attach_issuer_evidence(early, progress)
        publish(output / "calibration.json", early)
        print(wire({"status": "BLOQUEADO", "generation": "NOT_STARTED", "qualification_claimed": False}).decode().strip(), flush=True)
        return 1
    if args.mode == "bootstrap":
        need(args.capability_receipt and args.capability_artifact_origin, "CALIBRATION_CAPABILITY_PREREQUISITE_MISSING")
        validate_capability(decode(read(args.capability_receipt)), decode(read(args.capability_artifact_origin)),
            sha=args.source_sha, tree=args.source_tree, code_hashes=code_hashes,
            prerequisite=admission["capability_prerequisite"])
        for epoch, value in (("311", args.python311 or os.environ.get("RC6_CALIBRATION_PYTHON311", "")),
                             ("312", args.python312 or os.environ.get("RC6_CALIBRATION_PYTHON312", ""))):
            verify_python(value, epoch)
    limits = limits_for(args.mode)
    before = filesystem(parent)
    source_fs = filesystem(source)
    need(before["device"] == source_fs["device"] and before["mount_id"] == source_fs["mount_id"],
         "CALIBRATION_OUTER_SOURCE_FILESYSTEM_MISMATCH")
    control_bound = outer_control_peak_bound(before["fragment_bytes"])
    require_capacity(before, limits["backing_image_bytes"], control_bytes=control_bound["total_bytes"])
    namespace = owned.create_namespace(parent, binding)
    image, mountpoint = namespace.path / "image.ext4", namespace.path / "mount"
    mountpoint.mkdir(mode=0o700)
    image_fd = os.open(image, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(image_fd, "wb") as stream:
        os.posix_fallocate(stream.fileno(), 0, limits["backing_image_bytes"])
        os.fsync(stream.fileno())
    after_image = filesystem(parent)
    require_capacity(after_image, control_bound["total_bytes"])
    birth_path = namespace.path / "loop-birth.json"
    pending_birth = {"schema": "porota.rc6.pending-loop-birth.v1", "binding": binding,
                     "namespace_nonce": namespace.nonce, "image_identity": identity(image.lstat()),
                     "loop": None, "qualification_claimed": False}
    pending_birth_sha = publish(birth_path, pending_birth)
    request = {"schema": "porota.rc6.capacity-root-request.v1", "mode": args.mode, "limits": limits,
               "owner_uid": os.geteuid(), "owner_gid": os.getegid(), "source_root": str(source),
               "source_sha": args.source_sha, "source_tree": args.source_tree, "binding": binding,
               "namespace_receipt": owned.namespace_receipt(namespace), "image": str(image),
               "image_identity": identity(image.lstat()), "mountpoint": str(mountpoint),
               "mount_namespace_inode": os.stat("/proc/self/ns/mnt").st_ino, "code_hashes": code_hashes,
               "loop_birth_path": str(birth_path), "loop_birth_identity": identity(birth_path.lstat()),
               "loop_birth_pending_sha256": pending_birth_sha, "outer_control_peak_bound": control_bound,
               "issuer": {"pid": os.getpid(), "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                          "start_ticks": Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19]},
               "pythons": {"311": args.python311 or os.environ.get("RC6_CALIBRATION_PYTHON311", ""),
                           "312": args.python312 or os.environ.get("RC6_CALIBRATION_PYTHON312", "")}}
    request_path = namespace.path / "root-request.json"
    need(len(wire(request)) <= 4 * MIB, "CALIBRATION_ROOT_REQUEST_METADATA_LIMIT")
    request_sha = publish(request_path, request)
    report = {"schema": SCHEMA, "mode": args.mode, "source_sha": args.source_sha, "source_tree": args.source_tree,
              "binding": binding, "namespace_receipt": claim, "code_hashes": code_hashes, "limits": limits,
              "read_contract_sha256": admission["read_contract_sha256"], "qualification_claimed": False,
              "real_orders_sent": 0, "real_routes": "NOT_CALLED", "ppi_watch": "UNTOUCHED",
              "DEPLOY_OWNER": "NOT_ACQUIRED", "actual_capability_proved": False, "status": "BLOQUEADO",
              "outer_before": before, "outer_after_image": after_image, "root_request_sha256": request_sha,
              "outer_control_peak_bound": control_bound,
              "kernel_prerequisites": prerequisites, "generation": "STARTED", "inner_namespace_created": True,
              "G0_G8_qualification_claimed": False, "payload_upload_safe": False}
    try:
        need(shutil.which("sudo") and shutil.which("unshare") and shutil.which("mount") and shutil.which("mkfs.ext4"),
             "CALIBRATION_NATIVE_OWN_QUOTA_TOOLS_MISSING")
        # Parent control descriptors cannot be opened through /proc/PID/fd by a
        # same-UID build backend. The child still receives only its bounded log.
        libc = ctypes.CDLL(None, use_errno=True)
        need(libc.prctl(4, 0, 0, 0, 0) == 0, "CALIBRATION_PARENT_DUMPABLE_GUARD_REQUIRED")
        env = {key: os.environ[key] for key in ("PATH", "RUNNER_TOOL_CACHE", "LANG", "LC_ALL") if key in os.environ}
        # Only trusted supervisor report writes reach the bounded outer log.
        # Workloads receive their own quota-backed logs and close_fds=True.
        # RLIMIT_FSIZE would silently change Git's ordinary >128MiB pack files.
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        command = ["sudo", "-n", "--preserve-env=RUNNER_TOOL_CACHE", "--",
                   "unshare", "--mount", "--propagation", "private", "--",
                   sys.executable, "-I", "-B", str(source / "scripts/rc6_capacity_calibration.py"),
                   "--root-worker", str(request_path)]
        if hasattr(progress, "before_launch"):
            progress.before_launch("quota-diagnosis")
        kernel, fin = owned.execute_owned(namespace, command, cwd=source, environ=env, progress=progress,
                                           timeout_seconds=300 if args.mode == "capability" else 10800,
                                           fin_label="quota-diagnosis")
        report["kernel"] = kernel
        issuer_red = False
        if hasattr(progress, "after_fin"):
            try:
                progress.after_fin(kernel)
            except BaseException as error:
                issuer_red = True
                report["issuer_after_fin_reason"] = str(error).split(":", 1)[0]
            attach_issuer_evidence(report, progress)
        born_loop, born_sha = read_loop_birth(request, request_sha)
        report["loop_birth_sha256"] = born_sha
        # Documentary reads begin only after original physical FIN, including RED.
        try:
            worker = decode(read(namespace.path / "producer-native.log", MAX_WORKER_WIRE))
            need(worker.get("schema") == "porota.rc6.capacity-worker.v1"
                 and worker.get("request_sha256") == request_sha, "CALIBRATION_NATIVE_WORKER_REPORT_MISSING")
            inner = worker["report"]
        except (ValueError, OSError) as error:
            # A STOP without terminal JSON proves loop absence only. It does
            # not prove preservation of quota-backed first-failure RAW.
            report["loop_finalization"] = loop_gone(born_loop)
            raise ValueError("CALIBRATION_PARTIAL_WORKER_RAW_UNKNOWN") from error
        report["worker"] = {k: v for k, v in inner.items() if k != "raw_files"}
        need(inner.get("actual_own_fin_closed") is True and inner.get("required_raw_complete") is True,
             "CALIBRATION_PARTIAL_REQUIRED_RAW_OR_FIN_UNKNOWN")
        raw_refs = []
        for row in inner.get("raw_files", []):
            need(re.fullmatch("[A-Za-z0-9_.-]{1,100}", row.get("path", "")), "CALIBRATION_RAW_NAME_INVALID")
            raw = base64.b64decode(row["raw_base64"], validate=True)
            need(len(raw) == row["bytes"] and digest(raw) == row["sha256"], "CALIBRATION_RAW_HASH_CHANGED")
            publish_bytes(output / row["path"], raw)
            raw_refs.append({k: v for k, v in row.items() if k != "raw_base64"})
        report["required_raw"] = raw_refs
        source_pin(source, args.source_sha, args.source_tree)
        report["source_unchanged"] = True
        need(inner.get("loop") == born_loop, "CALIBRATION_TERMINAL_LOOP_BIRTH_REBOUND")
        report["loop_finalization"] = loop_gone(born_loop)
        capture = owned.capture_required_evidence(namespace, fin, output / "sealed-controls",
                                                 ["root-request.json", "producer-native.log", "loop-birth.json"])
        report["capture_manifest_sha256"] = capture.manifest_sha256
        report["cleanup"] = owned.cleanup_namespace(namespace, fin, capture)
        report["outer_after_cleanup"] = filesystem(parent)
        report["payload_upload_safe"] = True
        report["actual_capability_proved"] = inner.get("actual_capability_proved") is True
        if report["actual_capability_proved"]:
            report["capability_checks"] = {"project_hard_limit_enforced": True, "same_uid_escape_blocked": True,
                "project_reassignment_blocked": True, "quota_mutation_blocked": True,
                "privilege_drop_seccomp_enforced": True}
        report["status"] = "GREEN" if inner.get("status") == "GREEN" and owned._phase_green(fin) and not issuer_red else "BLOQUEADO"
    except BaseException as error:
        report["reason"] = str(error).split(":", 1)[0]
        report["error_class"] = type(error).__name__
        report["preserved_owned_namespace"] = str(namespace.path)
        report["status"] = "BLOQUEADO"
    attach_issuer_evidence(report, progress)
    need(len(wire(report)) <= MAX_RECEIPT, "CALIBRATION_FINAL_RECEIPT_LIMIT")
    publish(output / "calibration.json", report)
    print(wire({"status": report["status"], "qualification_claimed": False,
                "calibration_path": str(output / "calibration.json"), "real_orders_sent": 0}).decode().strip(), flush=True)
    return 0 if report["status"] == "GREEN" else 1


def cli(*, progress=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root-worker", type=Path)
    p.add_argument("--probe-child", action="store_true")
    p.add_argument("--project", type=Path)
    p.add_argument("--probe", type=Path)
    p.add_argument("--outside", type=Path)
    p.add_argument("--supervisor-pid", type=int)
    p.add_argument("--issuer-pid", type=int)
    p.add_argument("--mode", choices=("capability", "bootstrap"))
    p.add_argument("--source-root", type=Path)
    p.add_argument("--source-sha")
    p.add_argument("--source-tree")
    p.add_argument("--namespace-receipt", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--binding-json", type=Path)
    p.add_argument("--capability-receipt", type=Path)
    p.add_argument("--capability-artifact-origin", type=Path)
    p.add_argument("--python311")
    p.add_argument("--python312")
    a = p.parse_args()
    if a.root_worker:
        return root_worker(a.root_worker)
    if a.probe_child:
        need(a.project and a.probe and a.outside, "CALIBRATION_NATIVE_PROBE_PATHS_REQUIRED")
        print(wire(quota_probe(a.project, a.probe, a.outside, a.supervisor_pid, a.issuer_pid)).decode().strip(), flush=True)
        return 0
    need(a.mode and a.source_root and a.source_sha and a.source_tree and a.namespace_receipt and a.output and a.binding_json,
         "CALIBRATION_EXPLICIT_CLI_BINDING_REQUIRED")
    return main(a, progress=progress)


if __name__ == "__main__":
    raise SystemExit(cli())
