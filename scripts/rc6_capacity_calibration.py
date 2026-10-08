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
import hashlib
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

ROOT = Path(__file__).absolute().parents[1]
SCHEMA = "porota.rc6.capacity-calibration.v1"
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
                "requirements.lock.txt", "requirements.build.lock.txt", "ops/policy/rc6-supply-chain-v1.json",
                OBJECTS_MEMBER, BUILDER_MEMBER)
ORIGIN = "https://github.com/mbalbo2023/Porota-trading.git"
VERSIONS = {"311": "3.11.16", "312": "3.12.14"}
FSGETXATTR, FSSETXATTR = 0x801C581F, 0x401C5820
SETFLAGS = (0x40086602, 0x40046602)
PROJINHERIT = 0x200
Q_GETQUOTA, Q_SETQUOTA, PRJQUOTA = 0x800007, 0x800008, 2
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
                  "directory_and_file_rounding_bytes": (4 * MAX_RAW_FILES + 64) * fragment_bytes}
    return {"schema": "porota.rc6.capacity-diagnostic-control-bound.v1", "components": components,
            "total_bytes": sum(components.values()), "premature_cleanup_credit_bytes": 0,
            "future_capture_copies_included": 2, "max_raw_files": MAX_RAW_FILES}


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
            add("ioctl", comparisons=((1, 4, request, 0),))
        for flag in NAMESPACE_CLONE_FLAGS:
            add("clone", comparisons=((0, 7, flag, flag),))
        add("clone3", errno.ENOSYS)
        for option in (4, 8, 24, 28, 47):
            add("prctl", comparisons=((0, 4, option, 0),))
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


def set_quota(device, project_id, hard_bytes):
    need(type(hard_bytes) is int and hard_bytes > 0 and hard_bytes % 1024 == 0,
         "CALIBRATION_NATIVE_QUOTA_UNITS_REQUIRED")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.quotactl.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
    value = Dqblk()
    value.bhard, value.bsoft = hard_bytes // 1024, hard_bytes // 1024
    value.ihard, value.isoft, value.valid = MAX_ENTRIES, MAX_ENTRIES, 1 | 4
    need(libc.quotactl((Q_SETQUOTA << 8) | PRJQUOTA, device.encode(), project_id, ctypes.byref(value)) == 0,
         "CALIBRATION_NATIVE_PROJECT_QUOTA_SET_FAILED")
    actual = Dqblk()
    need(libc.quotactl((Q_GETQUOTA << 8) | PRJQUOTA, device.encode(), project_id, ctypes.byref(actual)) == 0
         and actual.bhard * 1024 == hard_bytes and actual.ihard == MAX_ENTRIES,
         "CALIBRATION_NATIVE_PROJECT_QUOTA_READBACK_FAILED")
    return {"project_id": project_id, "hard_bytes": actual.bhard * 1024, "hard_inodes": actual.ihard,
            "kernel_readback": True}


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
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=300)
    need(len(result.stdout) <= MIB, "CALIBRATION_OWN_SETUP_LOG_LIMIT")
    try:
        os.waitid(os.P_ALL, 0, os.WNOHANG | os.WNOWAIT | os.WEXITED)
    except ChildProcessError as error:
        need(error.errno == errno.ECHILD, "CALIBRATION_SETUP_ECHILD_REQUIRED")
    else:
        raise ValueError("CALIBRATION_SETUP_CHILD_UNKNOWN")
    return {"argv": argv, "returncode": result.returncode, "raw_sha256": digest(result.stdout),
            "raw_base64": base64.b64encode(result.stdout).decode(), "actual_setup_waited_echild": True}


def run_setup(request, argv):
    record = setup_command(argv)
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


def open_image_on_issuer_mount(request):
    """Only this authenticated proc root magic link may cross namespaces.

    Every ordinary component below that root uses a pinned NOFOLLOW dirfd.
    The loop's writable backing reference must belong to the issuer's original
    mount, rather than the cloned mount that will become read-only.
    """
    root = validate_root_request(request)
    issuer_before = issuer_kernel_identity(request)
    claim = request["namespace_receipt"]
    held, image_fd, marker_fd = [], None, None
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
        need(validate_root_request(request) == root and issuer_kernel_identity(request) == issuer_before
             and identity(os.fstat(image_fd)) == request["image_identity"],
             "CALIBRATION_ISSUER_CHANGED_DURING_OPEN")
        origin = {"schema": "porota.rc6.original-issuer-backing.v1", "issuer": issuer_before,
                  "original_mount_id": image_mount, "original_directory_identity": claim["identity"],
                  "image_identity": request["image_identity"], "namespace_nonce": claim["namespace_nonce"],
                  "marker_sha256": claim["marker_sha256"], "marker_identity": identity(before),
                  "ordinary_components_nofollow": True, "issuer_revalidated_before_after": True}
        result, image_fd = image_fd, None
        return result, origin
    finally:
        if image_fd is not None:
            os.close(image_fd)
        if marker_fd is not None:
            os.close(marker_fd)
        for descriptor in reversed(held):
            os.close(descriptor)


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
    backing_fd, backing_origin = open_image_on_issuer_mount(request)
    request["backing_origin"] = backing_origin
    request["setup_stage"] = "configure_own_autoclear_loop"
    device, loop, hold = configure_loop(image, expected_identity=request["image_identity"],
                                        owned_image_fd=backing_fd, backing_origin=backing_origin)
    request["loop"] = loop
    try:
        request["setup_commands"] = []
        request["setup_stage"] = "mkfs_own_ext4"
        run_setup(request, ["mkfs.ext4", "-q", "-F", "-m", "0", "-O", "project,quota",
                            "-E", "lazy_itable_init=0,lazy_journal_init=0", device])
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
        run_setup(request, ["mount", "-t", "ext4", "-o", "prjquota,nodev,nosuid", device, str(mountpoint)])
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
        inner = filesystem(project)
        require_capacity(inner, limits["project_hard_limit_bytes"], control_bytes=MIB)
        request.update(project=str(project), probe=str(probe), loop=loop, quotas=quotas,
                       inner_before=inner)
        mounts = []
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            fields = line.split()
            mounts.append({"id": int(fields[0]), "mountpoint": fields[4], "readonly": "ro" in fields[5].split(",")})
        need(all(row["readonly"] or row["mountpoint"] == str(mountpoint) for row in mounts)
             and sum(row["mountpoint"] == str(mountpoint) and not row["readonly"] for row in mounts) == 1,
             "CALIBRATION_OUTSIDE_MOUNTS_NOT_ALL_READONLY")
        request["outside_mounts_readonly_verified"] = True
        if request["mode"] == "bootstrap":
            binaries = {}
            for name in ("git", "cc"):
                executable = shutil.which(name)
                need(executable, "CALIBRATION_ORIGINAL_NATIVE_TOOLCHAIN_MISSING")
                path = Path(executable).resolve(strict=True)
                binaries[name] = {"path": str(path), "sha256": digest(read(path)),
                                  "identity": identity(path.lstat())}
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
    token = os.environ.get("RC6_CALIBRATION_READONLY_GIT_TOKEN")
    git_env = {}
    if token:
        askpass = project / "askpass.py"
        publish_bytes(askpass, b'#!/usr/bin/env python3\nimport os,sys\nprint("x-access-token" if "username" in sys.argv[1].lower() else os.environ["RC6_CALIBRATION_READONLY_GIT_TOKEN"])\n', 0o700)
        git_env = {"GIT_ASKPASS": str(askpass), "RC6_CALIBRATION_READONLY_GIT_TOKEN": token}
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


def nonroot_worker(request):
    project, source = Path(request["project"]), Path(request["source_root"])
    need(os.getuid() == os.geteuid() == request["owner_uid"] > 0, "CALIBRATION_BACKEND_NONROOT_REQUIRED")
    need(digest(read(source / DRIVER_MEMBER)) == DRIVER_SHA256, "CALIBRATION_ORIGINAL_MANAGER_CHANGED")
    manager = runpy.run_path(str(source / DRIVER_MEMBER))
    manager["pre_capture_kernel_state"]()
    for name in ("home", "tmp", "controls"):
        (project / name).mkdir(mode=0o700)
    os.chdir(project)
    os.umask(0o022)
    records, samples = [], [inventory(project)]
    def run(argv, label, seconds, extra=None):
        need(re.fullmatch("[A-Za-z0-9_-]{1,80}", label), "CALIBRATION_COMMAND_LABEL_REQUIRED")
        # The hard quota is rechecked by its immutable readback and enforced
        # live by the kernel; capacity is measured immediately before producer.
        require_capacity(filesystem(project), request["limits"]["project_hard_limit_bytes"] - samples[-1]["allocated_bytes"],
                         control_bytes=MIB)
        log = project / "controls" / (label + ".log")
        kernel = manager["managed_native_child"](argv, project, log, {**clean_env(project), **(extra or {})}, seconds,
            terminate_grace=2, progress_poll=5, progress=lambda *_: samples.append(inventory(project)))
        need(manager["managed_custody_closed"](kernel), "CALIBRATION_NATIVE_FIN_UNKNOWN")
        manager["pre_capture_kernel_state"]()
        publish(project / "controls" / (label + ".kernel.json"), kernel)
        samples.append(inventory(project))
        records.append({"label": label, "argv": argv, "kernel": kernel, "log": str(log)})
        need(manager["managed_phase_green"](kernel)
             and 0 <= kernel.get("termination_reap_restore_cleanup_seconds", math.inf) <= 5,
             "CALIBRATION_NATIVE_COMMAND_RED")
        return records[-1]
    probe = run([sys.executable, "-I", "-B", str(source / "scripts/rc6_capacity_calibration.py"), "--probe-child",
                 "--project", str(project), "--probe", request["probe"], "--outside", request["namespace_receipt"]["path"],
                 "--supervisor-pid", str(os.getpid()), "--issuer-pid", str(request["issuer"]["pid"])],
                "capability", 60)
    positive = decode(read(probe["log"]))
    need(positive.get("actual_positive") is True, "CALIBRATION_REAL_CAPABILITY_NOT_PROVED")
    build = bootstrap(request, run) if request["mode"] == "bootstrap" else None
    final_kernel = manager["pre_capture_kernel_state"]()
    raw_files, raw_total = [], 0
    for path in sorted((project / "controls").iterdir()):
        need(len(raw_files) < MAX_RAW_FILES, "CALIBRATION_REQUIRED_RAW_FILE_COUNT")
        raw = read(path, MAX_RAW)
        raw_total += len(raw)
        need(raw_total <= MAX_RAW, "CALIBRATION_REQUIRED_RAW_LIMIT")
        raw_files.append({"path": path.name, "bytes": len(raw), "sha256": digest(raw),
                          "raw_base64": base64.b64encode(raw).decode()})
    return {"status": "GREEN", "actual_capability_proved": True, "capability": positive,
            "privilege": request["privilege"], "loop": request["loop"], "quotas": request["quotas"],
            "inner_before": request["inner_before"], "inner_after": filesystem(project),
            "setup_commands": request["setup_commands"], "source_sha": request["source_sha"],
            "backing_origin": request["backing_origin"], "readonly_operation": request["readonly_operation"],
            "setup_stage": request["setup_stage"],
            "private_mount_preflight": request["private_mount_preflight"],
            "source_tree": request["source_tree"], "final_own_kernel": final_kernel,
            "commands": records, "samples": samples,
            "measured_high_water": max(samples, key=lambda x: x["allocated_bytes"]),
            "absolute_enforced_project_bound_bytes": request["limits"]["project_hard_limit_bytes"],
            "temporal_peak_exact_claimed": False, "bootstrap": build,
            "outside_mounts_readonly_verified": request["outside_mounts_readonly_verified"],
            "raw_files": raw_files, "raw_total_bytes": raw_total, "qualification_claimed": False}


def root_worker(request_path):
    request_raw = read(request_path, 4 * MIB)
    request = decode(request_raw)
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


def main(args):
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
    request = {"schema": "porota.rc6.capacity-root-request.v1", "mode": args.mode, "limits": limits,
               "owner_uid": os.geteuid(), "owner_gid": os.getegid(), "source_root": str(source),
               "source_sha": args.source_sha, "source_tree": args.source_tree, "binding": binding,
               "namespace_receipt": owned.namespace_receipt(namespace), "image": str(image),
               "image_identity": identity(image.lstat()), "mountpoint": str(mountpoint),
               "mount_namespace_inode": os.stat("/proc/self/ns/mnt").st_ino, "code_hashes": code_hashes,
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
              "G0_G8_qualification_claimed": False, "payload_upload_safe": False}
    try:
        need(shutil.which("sudo") and shutil.which("unshare") and shutil.which("mount") and shutil.which("mkfs.ext4"),
             "CALIBRATION_NATIVE_OWN_QUOTA_TOOLS_MISSING")
        # Parent control descriptors cannot be opened through /proc/PID/fd by a
        # same-UID build backend. The child still receives only its bounded log.
        libc = ctypes.CDLL(None, use_errno=True)
        need(libc.prctl(4, 0, 0, 0, 0) == 0, "CALIBRATION_PARENT_DUMPABLE_GUARD_REQUIRED")
        env = dict(os.environ)
        # Only trusted supervisor report writes reach the bounded outer log.
        # Workloads receive their own quota-backed logs and close_fds=True.
        # RLIMIT_FSIZE would silently change Git's ordinary >128MiB pack files.
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        command = ["sudo", "-n", "--preserve-env=RUNNER_TOOL_CACHE,RC6_CALIBRATION_READONLY_GIT_TOKEN", "--",
                   "unshare", "--mount", "--propagation", "private", "--",
                   sys.executable, "-I", "-B", str(source / "scripts/rc6_capacity_calibration.py"),
                   "--root-worker", str(request_path)]
        kernel, fin = owned.execute_owned(namespace, command, cwd=source, environ=env,
                                           timeout_seconds=300 if args.mode == "capability" else 10800,
                                           fin_label="quota-diagnosis")
        report["kernel"] = kernel
        # Documentary reads begin only after original physical FIN, including RED.
        worker = decode(read(namespace.path / "producer-native.log", MAX_WORKER_WIRE))
        need(worker.get("schema") == "porota.rc6.capacity-worker.v1"
             and worker.get("request_sha256") == request_sha, "CALIBRATION_NATIVE_WORKER_REPORT_MISSING")
        inner = worker["report"]
        report["worker"] = {k: v for k, v in inner.items() if k != "raw_files"}
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
        if inner.get("loop") is not None:
            report["loop_finalization"] = loop_gone(inner["loop"])
        else:
            raise ValueError("CALIBRATION_OWN_LOOP_FINALIZATION_UNKNOWN")
        capture = owned.capture_required_evidence(namespace, fin, output / "sealed-controls",
                                                 ["root-request.json", "producer-native.log"])
        report["capture_manifest_sha256"] = capture.manifest_sha256
        report["cleanup"] = owned.cleanup_namespace(namespace, fin, capture)
        report["outer_after_cleanup"] = filesystem(parent)
        report["payload_upload_safe"] = True
        report["actual_capability_proved"] = inner.get("actual_capability_proved") is True
        if report["actual_capability_proved"]:
            report["capability_checks"] = {"project_hard_limit_enforced": True, "same_uid_escape_blocked": True,
                "project_reassignment_blocked": True, "quota_mutation_blocked": True,
                "privilege_drop_seccomp_enforced": True}
        report["status"] = "GREEN" if inner.get("status") == "GREEN" and owned._phase_green(fin) else "BLOQUEADO"
    except BaseException as error:
        report["reason"] = str(error).split(":", 1)[0]
        report["error_class"] = type(error).__name__
        report["preserved_owned_namespace"] = str(namespace.path)
        report["status"] = "BLOQUEADO"
    need(len(wire(report)) <= MAX_RECEIPT, "CALIBRATION_FINAL_RECEIPT_LIMIT")
    publish(output / "calibration.json", report)
    print(wire({"status": report["status"], "qualification_claimed": False,
                "calibration_path": str(output / "calibration.json"), "real_orders_sent": 0}).decode().strip(), flush=True)
    return 0 if report["status"] == "GREEN" else 1


def cli():
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
    return main(a)


if __name__ == "__main__":
    raise SystemExit(cli())
