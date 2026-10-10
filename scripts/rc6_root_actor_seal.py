"""Separate post-setup ROOT seal; never bootstrap, quota or ROOT admission.

The original NONROOT plan and native manager remain unchanged. ROOT callers
must already have an authenticated private PID/mount boundary and private
devices. Captured JSON cannot authorize a current process. NONROOT probes
exercise the real filter without certifying ROOT capabilities or isolation.
"""
from __future__ import annotations

import copy
import ctypes
import errno
import os
from pathlib import Path
import re
import stat
import sys
import threading

ROOT = Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import rc6_native_namespace_filter as native

SCHEMA = "porota.rc6.root-actor-seal.v1"
INHERITANCE_SCHEMA = "porota.rc6.root-actor-seal-inheritance.v1"
CONTROL_BOUND = 65536
MAX_DEV_ENTRIES = 512
MAX_DEV_DEPTH = 4
# CHOWN: transfer owned post-FIN logs. DAC_OVERRIDE: traverse runner-owned
# mode0700 controls in the frozen supervisor. KILL: own process group cleanup.
# SETUID/GID/PCAP: the fixed irreversible NONROOT drop, not privilege recovery.
# These necessities are code-derived; native ROOT compatibility is unverified.
RETAINED_CAPABILITIES = (0, 1, 5, 6, 7, 8)
RETAINED_MASK = sum(1 << bit for bit in RETAINED_CAPABILITIES)
CAP_FIELDS = ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
DENIED_SYSCALLS = (
    "mount", "umount2", "unshare", "setns", "chroot", "pivot_root",
    "move_mount", "open_tree", "fsopen", "fsconfig", "fsmount", "fspick", "mount_setattr",
    "quotactl", "quotactl_fd", "ioctl", "setuid", "setreuid", "setfsuid",
    "setgid", "setregid", "setfsgid", "capset", "ptrace", "process_vm_readv", "process_vm_writev",
    "open_by_handle_at", "name_to_handle_at", "pidfd_getfd", "socket", "socketpair",
    "bpf", "kexec_load", "kexec_file_load", "init_module", "finit_module", "delete_module",
    "reboot", "swapon", "swapoff", "io_uring_setup", "io_uring_enter", "io_uring_register",
    "mknod", "mknodat",
)
BOUNDARY_LINK_FIELDS = (
    "host_pid_namespace_inode", "host_mount_namespace_inode", "host_device_number",
    "host_devpts_device_number", "control_root", "control_identity",
    "proc_mount", "device_mount", "devpts_mount",
)
_LIVE = None


def require(value, signature):
    if not value:
        raise ValueError(signature)


def _owners(uid, gid):
    require(all(type(x) is int and 0 < x < 0xFFFFFFFF for x in (uid, gid)),
            "ROOT_SEAL_EXACT_NONROOT_OWNER_REQUIRED")


def build_plan(owner_uid, owner_gid):
    _owners(owner_uid, owner_gid)
    return {"schema": "porota.rc6.root-actor-seal-plan.v1", "default": "ALLOW",
        "denied_syscalls": list(DENIED_SYSCALLS),
        "denied_clone_namespace_flags": list(native.NAMESPACE_CLONE_FLAGS), "clone3_errno": errno.ENOSYS,
        "setresuid_only": [owner_uid] * 3, "setresgid_only": [owner_gid] * 3,
        "setgroups_only_count": 0, "prctl_dumpable_only": 0, "prctl_capbset_drop_range": [0, 63],
        "denied_prctl_options": [8, 28, 47], "retained_root_capabilities": list(RETAINED_CAPABILITIES),
        "retained_capability_minimum_requires_native_review": True,
        "no_new_privileges": True, "quota_bootstrap_allowed": False,
        "original_NONROOT_plan_changed": False, "original_manager_changed": False}


def _read(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        raw = os.read(descriptor, CONTROL_BOUND + 1)
        require(len(raw) <= CONTROL_BOUND, "ROOT_SEAL_KERNEL_READ_BOUND")
        return raw.decode("utf-8", errors="strict")
    finally:
        os.close(descriptor)


def _identity():
    value = native._identity()
    value["pid_namespace_inode"] = os.stat("/proc/self/ns/pid").st_ino
    return value


def _caps(status):
    return {key: int(status[key], 16) for key in CAP_FIELDS}


def _process(status):
    require(sys.platform == "linux" and ctypes.sizeof(ctypes.c_void_p) == 8
            and threading.get_native_id() == os.getpid() and status.get("Threads") == "1"
            and len(os.listdir("/proc/self/task")) == 1, "ROOT_SEAL_SINGLE_NATIVE_THREAD_REQUIRED")


def _descriptors():
    rows = []
    for name in os.listdir("/proc/self/fd"):
        require(name.isdecimal(), "ROOT_SEAL_DESCRIPTOR_METADATA_UNKNOWN")
        number = int(name)
        try:
            details = os.fstat(number)
        except OSError as error:
            require(error.errno == errno.EBADF, "ROOT_SEAL_DESCRIPTOR_METADATA_UNKNOWN")
            continue  # os.listdir's already-closed directory descriptor only.
        require(number <= 2, "ROOT_SEAL_INHERITED_HOST_DESCRIPTOR_FORBIDDEN")
        require(stat.S_ISREG(details.st_mode) or stat.S_ISFIFO(details.st_mode)
                or stat.S_ISCHR(details.st_mode) and details.st_rdev == os.makedev(1, 3),
                "ROOT_SEAL_STDIO_NOT_OWN_FILE_PIPE_OR_NULL")
        require(number != 0 or stat.S_ISCHR(details.st_mode) and details.st_rdev == os.makedev(1, 3),
                "ROOT_SEAL_STDIN_MUST_BE_NULL")
        rows.append({"descriptor": number, "device": details.st_dev, "inode": details.st_ino,
                     "mode": details.st_mode, "rdevice": details.st_rdev})
    require({row["descriptor"] for row in rows} == {0, 1, 2}, "ROOT_SEAL_COMPLETE_STDIO_REQUIRED")
    return sorted(rows, key=lambda row: row["descriptor"])


def _mount_path(value):
    require(not re.search(r"\\(?![0-7]{3})", value), "ROOT_SEAL_MOUNT_ESCAPE_UNKNOWN")
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), value)


def _device_inventory():
    # PrivateDevices alone does not prove a private devpts: inspect visible
    # nodes, never follow /dev/fd or /dev/core into foreign process handles.
    allowed = {(1, 3), (1, 5), (1, 7), (1, 8), (1, 9), (5, 0), (5, 2)}
    pending, entries, nodes = [(Path("/dev"), 0)], 0, []
    while pending:
        directory, depth = pending.pop()
        require(depth <= MAX_DEV_DEPTH, "ROOT_SEAL_DEVICE_DEPTH_BOUND")
        for child in directory.iterdir():
            entries += 1
            require(entries <= MAX_DEV_ENTRIES, "ROOT_SEAL_DEVICE_COUNT_BOUND")
            details = child.lstat()
            require(not stat.S_ISBLK(details.st_mode), "ROOT_SEAL_FOREIGN_BLOCK_DEVICE_VISIBLE")
            if stat.S_ISCHR(details.st_mode):
                pair = (os.major(details.st_rdev), os.minor(details.st_rdev))
                require(pair in allowed, "ROOT_SEAL_FOREIGN_CHARACTER_DEVICE_VISIBLE")
                nodes.append({"path": str(child), "major": pair[0], "minor": pair[1]})
            elif stat.S_ISDIR(details.st_mode):
                pending.append((child, depth + 1))
    return {"entries": entries, "character_nodes": nodes, "block_devices_absent": True,
            "foreign_tty_and_loop_control_nodes_absent": True}


def _boundary(*, owner_uid, owner_gid, control_root, host_pid_namespace_inode,
              host_mount_namespace_inode, host_device_number, host_devpts_device_number, initial):
    identity = _identity()
    require(all(type(x) is int and x > 0 for x in (
        host_pid_namespace_inode, host_mount_namespace_inode, host_device_number, host_devpts_device_number)),
        "ROOT_SEAL_AUTHENTICATED_HOST_BOUNDARY_REQUIRED")
    require(identity["pid_namespace_inode"] != host_pid_namespace_inode
            and identity["mount_namespace_inode"] != host_mount_namespace_inode
            and (os.getpid() == 1 if initial else os.getpid() > 1),
            "ROOT_SEAL_PRIVATE_PID_AND_MOUNT_REQUIRED")
    root = Path(control_root)
    require(root.is_absolute() and root != Path("/") and ".." not in root.parts
            and not any(path.is_symlink() for path in (root, *root.parents)),
            "ROOT_SEAL_PRIVATE_CONTROL_ROOT_REQUIRED")
    details = root.lstat()
    require(stat.S_ISDIR(details.st_mode) and details.st_uid == owner_uid
            and details.st_gid == owner_gid and stat.S_IMODE(details.st_mode) == 0o700,
            "ROOT_SEAL_PRIVATE_CONTROL_CUSTODY_REQUIRED")
    raw = _read("/proc/self/mountinfo")
    mounts = []
    for line in raw.splitlines():
        fields = line.split()
        require(fields.count("-") == 1 and len(fields) >= 10 and fields[0].isdecimal()
                and fields[1].isdecimal() and re.fullmatch(r"[0-9]+:[0-9]+", fields[2]),
                "ROOT_SEAL_MOUNT_METADATA_UNKNOWN")
        split = fields.index("-")
        require(split >= 6 and len(fields) == split + 4 and int(fields[0]) > 0,
                "ROOT_SEAL_MOUNT_METADATA_UNKNOWN")
        require(not any(item.startswith(("shared:", "master:", "propagate_from:"))
                    for item in fields[6:split]), "ROOT_SEAL_SHARED_MOUNT_PROPAGATION_FORBIDDEN")
        major, minor = map(int, fields[2].split(":"))
        mounts.append({"id": int(fields[0]), "root": _mount_path(fields[3]),
                       "device": os.makedev(major, minor),
                       "target": _mount_path(fields[4]), "options": fields[5].split(","),
                       "type": fields[split + 1], "super_options": fields[split + 3].split(",")})
    require(0 < len(mounts) <= 1024, "ROOT_SEAL_MOUNT_COUNT_BOUND")
    require(len({row["id"] for row in mounts}) == len(mounts), "ROOT_SEAL_MOUNT_IDENTITY_AMBIGUOUS")
    proc = [row for row in mounts if row["target"] == "/proc"]
    dev = [row for row in mounts if row["target"] == "/dev"]
    devpts = [row for row in mounts if row["type"] == "devpts"]
    require(len(proc) == 1 and proc[0]["type"] == "proc" and proc[0]["root"] == "/"
            and "ro" in proc[0]["options"],
            "ROOT_SEAL_PRIVATE_PROC_READONLY_REQUIRED")
    require(not any(row["type"] == "proc" and row["root"] == "/" and row["target"] != "/proc"
                    for row in mounts), "ROOT_SEAL_FOREIGN_PROC_ALIAS_FORBIDDEN")
    status = native._status()
    require(status.get("Pid") == status.get("Tgid") == str(os.getpid())
            and status.get("NSpid", "").split() == [str(os.getpid())]
            and os.readlink("/proc/self") == str(os.getpid()), "ROOT_SEAL_PRIVATE_PROC_PID_VIEW_REQUIRED")
    require(len(dev) == 1 and dev[0]["type"] == "tmpfs" and dev[0]["root"] == "/",
            "ROOT_SEAL_PRIVATE_DEVICES_REQUIRED")
    require(len(devpts) == 1 and devpts[0]["target"] == "/dev/pts" and devpts[0]["root"] == "/",
            "ROOT_SEAL_SINGLE_PRIVATE_DEVPTS_REQUIRED")
    require(all("ro" in row["options"] or row["target"] == str(root) for row in mounts),
            "ROOT_SEAL_FOREIGN_WRITABLE_MOUNT_FORBIDDEN")
    dev_identity = os.stat("/dev", follow_symlinks=False)
    pts_identity = os.stat("/dev/pts", follow_symlinks=False)
    require(stat.S_ISDIR(dev_identity.st_mode) and dev_identity.st_dev == dev[0]["device"]
            and dev_identity.st_dev != host_device_number, "ROOT_SEAL_PRIVATE_DEVICES_REQUIRED")
    # Systemd PrivateDevices bind-mounts the host devpts. Readonly is not a
    # device-I/O prohibition, and a later host PTY defeats an empty inventory.
    # The fixed caller must construct a separate instance before this seal.
    require(stat.S_ISDIR(pts_identity.st_mode) and pts_identity.st_dev == devpts[0]["device"]
            and pts_identity.st_dev != host_devpts_device_number, "ROOT_SEAL_PRIVATE_DEVPTS_REQUIRED")
    cgroups = [row for row in mounts if row["type"] in ("cgroup", "cgroup2")]
    require(cgroups and all("ro" in row["options"] for row in cgroups),
            "ROOT_SEAL_CGROUP_READONLY_REQUIRED")
    return {"host_pid_namespace_inode": host_pid_namespace_inode,
        "host_mount_namespace_inode": host_mount_namespace_inode, "host_device_number": host_device_number,
        "host_devpts_device_number": host_devpts_device_number,
        "control_identity": [details.st_dev, details.st_ino, details.st_uid, details.st_gid, 0o700],
        "proc_mount": proc[0], "device_mount": dev[0], "devpts_mount": devpts[0],
        "private_devpts_readonly": True,
        "control_root": str(root), "private_proc_readonly": True, "foreign_mounts_readonly": True,
        "cgroup_readonly": True, "mountinfo_sha256": native.digest(raw.encode()),
        "devices": _device_inventory(), "descriptors": _descriptors(),
        "host_boundary_provenance_requires_caller_live_custody": True}


def _validate_boundary_evidence(boundary, identity, owner_uid, owner_gid):
    """Check captured shape/linkage; it supplies no live host authorization."""
    require(type(boundary) is dict and all(type(boundary.get(key)) is int and boundary[key] > 0
        for key in BOUNDARY_LINK_FIELDS[:4]), "ROOT_SEAL_HOST_BOUNDARY_EVIDENCE_REQUIRED")
    require(identity["pid_namespace_inode"] != boundary["host_pid_namespace_inode"]
            and identity["mount_namespace_inode"] != boundary["host_mount_namespace_inode"],
            "ROOT_SEAL_CAPTURED_NAMESPACE_REBOUND")
    root = boundary.get("control_root")
    control = boundary.get("control_identity")
    require(type(root) is str and Path(root).is_absolute() and root != "/" and ".." not in Path(root).parts
            and type(control) is list and len(control) == 5
            and all(type(value) is int for value in control) and all(value > 0 for value in control[:2])
            and control[2:] == [owner_uid, owner_gid, 0o700], "ROOT_SEAL_CAPTURED_CONTROL_REBOUND")
    mounts = []
    for key, target, kind in (("proc_mount", "/proc", "proc"), ("device_mount", "/dev", "tmpfs"),
                              ("devpts_mount", "/dev/pts", "devpts")):
        row = boundary.get(key)
        require(type(row) is dict and type(row.get("id")) is int and row["id"] > 0
                and type(row.get("device")) is int and row["device"] > 0
                and row.get("root") == "/" and row.get("target") == target and row.get("type") == kind
                and type(row.get("options")) is list and "ro" in row["options"] and "rw" not in row["options"],
                "ROOT_SEAL_CAPTURED_PRIVATE_MOUNT_REQUIRED")
        mounts.append(row)
    require(len({row["id"] for row in mounts}) == 3
            and mounts[1]["device"] != boundary["host_device_number"]
            and mounts[2]["device"] != boundary["host_devpts_device_number"],
            "ROOT_SEAL_CAPTURED_DEVICE_OR_MOUNT_ALIAS")
    require(all(boundary.get(key) is True for key in (
        "private_proc_readonly", "private_devpts_readonly", "foreign_mounts_readonly", "cgroup_readonly",
        "host_boundary_provenance_requires_caller_live_custody"))
        and type(boundary.get("mountinfo_sha256")) is str
        and re.fullmatch(r"[0-9a-f]{64}", boundary["mountinfo_sha256"]),
        "ROOT_SEAL_CAPTURED_BOUNDARY_NOT_PROVED")


def _require_inherited_boundary(child, parent):
    require(type(child) is dict and type(parent) is dict
            and all(key in child and key in parent and child[key] == parent[key] for key in BOUNDARY_LINK_FIELDS),
            "ROOT_SEAL_INHERITED_BOUNDARY_REBOUND")


class CapHeader(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]


class CapData(ctypes.Structure):
    _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32),
               ("inheritable", ctypes.c_uint32)]


def _reduce_root_capabilities(libc, before):
    last = int(_read("/proc/sys/kernel/cap_last_cap").strip())
    require(0 <= last <= 63 and _caps(before)["CapEff"] & RETAINED_MASK == RETAINED_MASK,
            "ROOT_SEAL_REQUIRED_DROP_AND_SUPERVISOR_CAPABILITIES_MISSING")
    require(libc.prctl(47, 4, 0, 0, 0) == 0, "ROOT_SEAL_AMBIENT_CLEAR_FAILED")
    os.setgroups([])
    for bit in range(last + 1):
        if bit not in RETAINED_CAPABILITIES:
            require(libc.prctl(24, bit, 0, 0, 0) == 0, "ROOT_SEAL_BOUNDING_DROP_FAILED")
    header = CapHeader(0x20080522, 0)
    data = (CapData * 2)(*(CapData((RETAINED_MASK >> (32 * i)) & 0xFFFFFFFF,
                                  (RETAINED_MASK >> (32 * i)) & 0xFFFFFFFF, 0) for i in range(2)))
    require(libc.capset(ctypes.byref(header), data) == 0, "ROOT_SEAL_EFFECTIVE_PERMITTED_DROP_FAILED")
    after = native._status()
    caps = _caps(after)
    require(caps == {"CapInh": 0, "CapAmb": 0, "CapPrm": RETAINED_MASK,
            "CapEff": RETAINED_MASK, "CapBnd": RETAINED_MASK} and os.getgroups() == [],
            "ROOT_SEAL_CAPABILITIES_NOT_EXACT_RETAINED_SET")
    return caps


def _compile(lib, plan):
    arch = int(lib.seccomp_arch_native())
    require(arch in native.SUPPORTED_ARCHITECTURES, "ROOT_SEAL_NATIVE_ARCHITECTURE_UNREVIEWED")
    context = lib.seccomp_init(native.ALLOW)
    require(context, "ROOT_SEAL_CONTEXT_REQUIRED")
    rules, resolved = [], {}
    try:
        for attribute in (3, 4):
            require(lib.seccomp_attr_set(context, attribute, 1) == 0, "ROOT_SEAL_NNP_TSYNC_ATTRIBUTE_FAILED")

        def add(name, comparisons=(), denied_errno=errno.EPERM):
            number = int(lib.seccomp_syscall_resolve_name(name.encode()))
            require(number >= 0, "ROOT_SEAL_SYSCALL_UNKNOWN:" + name)
            resolved[name] = number
            values = (native.Comparison * len(comparisons))(*(native.Comparison(*row) for row in comparisons))
            require(lib.seccomp_rule_add_array(context, native.ERRNO | denied_errno, number,
                len(values), values) == 0, "ROOT_SEAL_RULE_FAILED:" + name)
            rules.append({"syscall": name, "number": number, "errno": denied_errno,
                          "comparisons": [list(row) for row in comparisons]})

        for name in DENIED_SYSCALLS:
            add(name)
        for flag in native.NAMESPACE_CLONE_FLAGS:
            add("clone", ((0, native.MASKED_EQ, flag, flag),))
        add("clone3", denied_errno=errno.ENOSYS)
        for name in ("setresuid", "setresgid"):
            for index, value in enumerate(plan[name + "_only"]):
                add(name, ((index, 1, value, 0),))  # SCMP_CMP_NE
        add("setgroups", ((0, 1, 0, 0),))
        for option in plan["denied_prctl_options"]:
            add("prctl", ((0, native.MASKED_EQ, 0xFFFFFFFF, option),))
        add("prctl", ((0, native.MASKED_EQ, 0xFFFFFFFF, 4), (1, 1, 0, 0)))
        add("prctl", ((0, native.MASKED_EQ, 0xFFFFFFFF, 24), (1, 6, 63, 0)))  # GT
        descriptor = os.memfd_create("rc6-root-actor-seal-bpf", os.MFD_CLOEXEC)
        try:
            require(lib.seccomp_export_bpf(context, descriptor) == 0, "ROOT_SEAL_BPF_EXPORT_FAILED")
            size = os.fstat(descriptor).st_size
            require(0 < size <= 4096 * 8 and size % 8 == 0, "ROOT_SEAL_BPF_BOUND_EXCEEDED")
            raw = os.pread(descriptor, size, 0)
            require(len(raw) == size, "ROOT_SEAL_BPF_EXPORT_TRUNCATED")
        finally:
            os.close(descriptor)
        version = lib.seccomp_version().contents
        return context, {"native_architecture": arch, "libseccomp_version": [version.major, version.minor, version.micro],
            "resolved_syscalls": resolved, "rule_count": len(rules), "rules_sha256": native.digest(native.canonical(rules)),
            "exported_bpf_sha256": native.digest(raw), "exported_bpf_bytes": len(raw),
            "kernel_bpf_bytes_readback_claimed": False, "tsync_requested": True}
    except BaseException:
        lib.seccomp_release(context)
        raise


def _probe_specs():
    # Invalid operands stay non-mutating even in a missing-filter regression.
    rows = [("mount", "mount", (0, 0, 0, 0, 0), errno.EPERM),
        ("umount2", "umount2", (0, 0x80000000), errno.EPERM),
        ("unshare", "unshare", (1 << 63,), errno.EPERM),
        ("setns", "setns", (-1, 0), errno.EPERM), ("clone3", "clone3", (0, 0), errno.ENOSYS),
        ("chroot", "chroot", (0,), errno.EPERM),
        ("pivot_root", "pivot_root", (0, 0), errno.EPERM),
        ("move_mount", "move_mount", (-1, 0, -1, 0, 0), errno.EPERM),
        ("open_tree", "open_tree", (-1, 0, 0xFFFFFFFF), errno.EPERM),
        ("fsopen", "fsopen", (0, 0), errno.EPERM),
        ("fsconfig", "fsconfig", (-1, 0xFFFFFFFF, 0, 0, 0), errno.EPERM),
        ("fsmount", "fsmount", (-1, 0xFFFFFFFF, 0xFFFFFFFF), errno.EPERM),
        ("fspick", "fspick", (-1, 0, 0xFFFFFFFF), errno.EPERM),
        ("mount_setattr", "mount_setattr", (-1, 0, 0, 0, 0), errno.EPERM),
        ("open_by_handle_at", "open_by_handle_at", (-1, 0, 0), errno.EPERM),
        ("name_to_handle_at", "name_to_handle_at", (-1, 0, 0, 0, 0), errno.EPERM),
        ("pidfd_getfd", "pidfd_getfd", (-1, -1, 0), errno.EPERM),
        ("socket", "socket", (-1, 0, 0), errno.EPERM),
        ("socketpair", "socketpair", (-1, 0, 0, 0), errno.EPERM),
        ("ptrace", "ptrace", (0xFFFFFFFF, os.getppid(), 0, 0), errno.EPERM),
        ("process_vm_readv", "process_vm_readv", (os.getppid(), 0, 1, 0, 1, 0), errno.EPERM),
        ("process_vm_writev", "process_vm_writev", (os.getppid(), 0, 1, 0, 1, 0), errno.EPERM),
        ("quotactl", "quotactl", (0, 0, 0, 0), errno.EPERM),
        ("quotactl_fd", "quotactl_fd", (-1, 0, 0, 0), errno.EPERM),
        ("ioctl", "ioctl", (-1, 0, 0), errno.EPERM),
        ("setresuid_no_change", "setresuid", (-1, -1, -1), errno.EPERM),
        ("setresgid_no_change", "setresgid", (-1, -1, -1), errno.EPERM),
        ("setgroups_invalid_pointer", "setgroups", (1, 0), errno.EPERM),
        ("capset_invalid_pointer", "capset", (0, 0), errno.EPERM),
        ("prctl_dumpable_invalid", "prctl", (4, 0xFFFFFFFF, 0, 0, 0), errno.EPERM),
        ("prctl_capbset_invalid", "prctl", (24, 64, 0, 0, 0), errno.EPERM)]
    rows.extend(("clone_namespace_" + str(flag), "clone", (flag | 0x10000, 0, 0, 0, 0), errno.EPERM)
                for flag in native.NAMESPACE_CLONE_FLAGS)
    rows.extend(("prctl_" + str(option), "prctl", (option, 0xFFFFFFFF, 0, 0, 0), errno.EPERM)
                for option in (8, 28, 47))
    return rows


def _denials():
    lib, libc, rows = native._library(), ctypes.CDLL(None, use_errno=True), []
    libc.syscall.restype = ctypes.c_long
    for label, name, arguments, expected in _probe_specs():
        number = int(lib.seccomp_syscall_resolve_name(name.encode()))
        require(number >= 0, "ROOT_SEAL_PROBE_SYSCALL_UNKNOWN")
        ctypes.set_errno(0)
        returned = int(libc.syscall(ctypes.c_long(number), *(ctypes.c_ulonglong(x) for x in arguments)))
        saved = ctypes.get_errno()
        require(returned == -1 and saved == expected, "ROOT_SEAL_NATIVE_DENIAL_MISSING:" + label)
        rows.append({"probe": label, "syscall": name, "returncode": returned, "errno": saved})
    return rows


def validate_evidence(receipt, *, owner_uid, owner_gid, source_binding=None):
    plan, binding = build_plan(owner_uid, owner_gid), native._binding(source_binding)
    require(type(receipt) is dict and receipt.get("schema") == SCHEMA
            and receipt.get("plan") == plan and receipt.get("plan_sha256") == native.digest(native.canonical(plan))
            and receipt.get("source_binding") == binding and receipt.get("scope") in ("ROOT_POST_SETUP_ONLY", "NONROOT_TEST_ONLY")
            and receipt.get("ROOT_custody_certified") is False and receipt.get("G0_G8_qualification") is False
            and receipt.get("quota_bootstrap_allowed") is False
            and type(receipt.get("seccomp_load_returncode")) is int and receipt["seccomp_load_returncode"] == 0
            and type(receipt.get("filters_before")) is int and 0 <= receipt["filters_before"] < 1024
            and receipt.get("filters_after") == receipt["filters_before"] + 1
            and receipt.get("native_architecture") in native.SUPPORTED_ARCHITECTURES
            and receipt.get("tsync_requested") is True and receipt.get("no_new_privileges") is True
            and receipt.get("dumpable") == 0 and receipt.get("kernel_bpf_bytes_readback_claimed") is False,
            "ROOT_SEAL_AUTHENTIC_NATIVE_EVIDENCE_REQUIRED")
    require(all(type(receipt.get(key)) is str and re.fullmatch("[0-9a-f]{64}", receipt[key])
        for key in ("module_sha256", "rules_sha256", "exported_bpf_sha256")), "ROOT_SEAL_PROGRAM_HASH_REQUIRED")
    require(type(receipt.get("exported_bpf_bytes")) is int and 0 < receipt["exported_bpf_bytes"] <= 4096 * 8
            and receipt["exported_bpf_bytes"] % 8 == 0, "ROOT_SEAL_BPF_BOUND_EXCEEDED")
    identity = receipt.get("identity")
    require(type(identity) is dict and all(type(identity.get(key)) is int and identity[key] > 0
        for key in ("pid", "native_tid", "start_ticks", "pid_namespace_inode", "mount_namespace_inode", "cgroup_namespace_inode"))
        and identity["pid"] == identity["native_tid"], "ROOT_SEAL_NATIVE_IDENTITY_REQUIRED")
    rows = receipt.get("denial_probes")
    require(type(rows) is list and len(rows) == len(_probe_specs()) and all(
        type(row) is dict and row.get("probe") == label and row.get("syscall") == name
        and row.get("returncode") == -1 and row.get("errno") == expected
        for row, (label, name, _, expected) in zip(rows, _probe_specs())), "ROOT_SEAL_COMPLETE_DENIALS_REQUIRED")
    caps = receipt.get("capabilities")
    if receipt["scope"] == "ROOT_POST_SETUP_ONLY":
        require(binding is not None and caps == {"CapInh": 0, "CapAmb": 0, "CapPrm": RETAINED_MASK,
                        "CapEff": RETAINED_MASK, "CapBnd": RETAINED_MASK}
                and type(receipt.get("boundary")) is dict, "ROOT_SEAL_ROOT_CAPABILITIES_OR_BOUNDARY_UNPROVED")
        _validate_boundary_evidence(receipt["boundary"], identity, owner_uid, owner_gid)
    else:
        require(type(caps) is dict and all(caps.get(key) == 0 for key in ("CapInh", "CapPrm", "CapEff", "CapAmb"))
                and receipt.get("boundary") is None, "ROOT_SEAL_NONROOT_TEST_CANNOT_CERTIFY_BOUNDARY")
    require(len(native.canonical(receipt)) <= CONTROL_BOUND, "ROOT_SEAL_RECEIPT_BOUND_EXCEEDED")
    return copy.deepcopy(receipt)


def validate_inheritance(parent, *, owner_uid, owner_gid, source_binding=None):
    parent = validate_evidence(parent, owner_uid=owner_uid, owner_gid=owner_gid, source_binding=source_binding)
    status, identity = native._status(), _identity()
    _process(status)
    require(identity["pid"] != parent["identity"]["pid"] and identity["parent_pid"] == parent["identity"]["pid"],
            "ROOT_SEAL_ACTUAL_PARENT_REQUIRED")
    ticks = int(_read("/proc/" + str(identity["parent_pid"]) + "/stat").rsplit(")", 1)[1].split()[19])
    require(ticks == parent["identity"]["start_ticks"] and all(identity[key] == parent["identity"][key]
        for key in ("boot_id", "pid_namespace_inode", "mount_namespace_inode", "cgroup_namespace_inode")),
        "ROOT_SEAL_PARENT_OR_NAMESPACE_REBOUND")
    require(status.get("NoNewPrivs") == "1" and status.get("Seccomp") == "2"
            and native._counter(status) == parent["filters_after"], "ROOT_SEAL_INHERITED_COUNTER_OR_NNP_REBOUND")
    return {"schema": INHERITANCE_SCHEMA, "identity": identity, "parent_identity": parent["identity"],
        "parent_receipt_sha256": native.digest(native.canonical(parent)), "source_binding": native._binding(source_binding),
        "filters_before_own_installation": native._counter(status), "native_denials_before_own_installation": _denials(),
        "inherited_dumpable_observed": int(ctypes.CDLL(None).prctl(3, 0, 0, 0, 0)),
        "fork_exec_inheritance_observed": True, "ROOT_custody_certified": False}


def validate_inheritance_evidence(child, parent, *, owner_uid, owner_gid, source_binding=None):
    child = validate_evidence(child, owner_uid=owner_uid, owner_gid=owner_gid, source_binding=source_binding)
    parent = validate_evidence(parent, owner_uid=owner_uid, owner_gid=owner_gid, source_binding=source_binding)
    proof = child.get("inherited")
    require(type(proof) is dict and proof.get("schema") == INHERITANCE_SCHEMA
            and proof.get("identity") == child["identity"] and proof.get("parent_identity") == parent["identity"]
            and child["identity"]["parent_pid"] == parent["identity"]["pid"]
            and proof.get("parent_receipt_sha256") == native.digest(native.canonical(parent))
            and proof.get("source_binding") == native._binding(source_binding)
            and proof.get("fork_exec_inheritance_observed") is True
            and proof.get("ROOT_custody_certified") is False
            and proof.get("filters_before_own_installation") == child["filters_before"] == parent["filters_after"]
            and proof.get("native_denials_before_own_installation") == child["denial_probes"],
            "ROOT_SEAL_COMPLETE_INHERITANCE_EVIDENCE_REQUIRED")
    require(all(child[key] == parent[key] for key in (
        "scope", "plan_sha256", "module_sha256", "rules_sha256", "exported_bpf_sha256", "exported_bpf_bytes", "native_architecture"))
        and all(child["identity"][key] == parent["identity"][key] for key in (
            "boot_id", "pid_namespace_inode", "mount_namespace_inode", "cgroup_namespace_inode")),
        "ROOT_SEAL_INHERITED_PROGRAM_REBOUND")
    if child["scope"] == "ROOT_POST_SETUP_ONLY":
        _require_inherited_boundary(child["boundary"], parent["boundary"])
    return copy.deepcopy(proof)


def _install(*, owner_uid, owner_gid, source_binding, inherited, scope, boundary_arguments=None):
    global _LIVE
    plan, binding = build_plan(owner_uid, owner_gid), native._binding(source_binding)
    require(binding is not None or scope == "NONROOT_TEST_ONLY", "ROOT_SEAL_EXACT_SOURCE_REQUIRED")
    before = native._status()
    _process(before)
    require(_LIVE is None or _LIVE["identity"]["pid"] != os.getpid(), "ROOT_SEAL_ALREADY_INSTALLED")
    if scope == "ROOT_POST_SETUP_ONLY":
        require(os.getresuid() == (0, 0, 0), "ROOT_SEAL_ACTUAL_ROOT_REQUIRED")
        boundary = _boundary(owner_uid=owner_uid, owner_gid=owner_gid, initial=inherited is None, **boundary_arguments)
    else:
        native._nonroot(before, single_thread=True)
        require(all(_caps(before)[key] == 0 for key in ("CapInh", "CapPrm", "CapEff", "CapAmb")),
                "ROOT_SEAL_NONROOT_TEST_PRIVILEGES_FORBIDDEN")
        boundary = None
    count = native._counter(before)
    inheritance = validate_inheritance(inherited, owner_uid=owner_uid, owner_gid=owner_gid,
        source_binding=binding) if inherited is not None else None
    require(inherited is None or inherited["scope"] == scope, "ROOT_SEAL_INHERITED_SCOPE_REBOUND")
    if scope == "ROOT_POST_SETUP_ONLY" and inherited is not None:
        _require_inherited_boundary(boundary, inherited["boundary"])
    lib, libc = native._library(), ctypes.CDLL(None, use_errno=True)
    context, program = _compile(lib, plan)
    try:
        require(libc.prctl(4, 0, 0, 0, 0) == 0 and libc.prctl(3, 0, 0, 0, 0) == 0,
                "ROOT_SEAL_DUMPABLE_ZERO_FAILED")
        if scope == "ROOT_POST_SETUP_ONLY" and inherited is None:
            caps = _reduce_root_capabilities(libc, before)
        else:
            caps = _caps(before)
            if scope == "ROOT_POST_SETUP_ONLY":
                require(caps == {"CapInh": 0, "CapAmb": 0, "CapPrm": RETAINED_MASK,
                    "CapEff": RETAINED_MASK, "CapBnd": RETAINED_MASK}, "ROOT_SEAL_INHERITED_CAPABILITIES_REBOUND")
        require(libc.prctl(38, 1, 0, 0, 0) == 0 and libc.prctl(39, 0, 0, 0, 0) == 1, "ROOT_SEAL_NNP_FAILED")
        result = int(lib.seccomp_load(context))
        require(result == 0, "ROOT_SEAL_NATIVE_LOAD_FAILED")
    finally:
        lib.seccomp_release(context)
    after = native._status()
    require(after.get("NoNewPrivs") == "1" and after.get("Seccomp") == "2"
            and native._counter(after) == count + 1 and _caps(after) == caps,
            "ROOT_SEAL_KERNEL_READBACK_CHANGED")
    receipt = {"schema": SCHEMA, "scope": scope, "source_binding": binding,
        "identity": _identity(), "plan": plan, "plan_sha256": native.digest(native.canonical(plan)),
        "module_sha256": native.digest(Path(__file__).read_bytes()), **program,
        "filters_before": count, "filters_after": count + 1, "seccomp_load_returncode": result,
        "no_new_privileges": True, "dumpable": int(libc.prctl(3, 0, 0, 0, 0)),
        "capabilities": caps, "boundary": boundary, "denial_probes": _denials(), "inherited": inheritance,
        "ROOT_custody_certified": False, "G0_G8_qualification": False, "quota_bootstrap_allowed": False,
        "ROOT_capability_reduction_executed": scope == "ROOT_POST_SETUP_ONLY", "ROOT_capability_minimum_proved": False,
        "real_orders_sent": 0, "recurring_additional_cost_usd": 0}
    validate_evidence(receipt, owner_uid=owner_uid, owner_gid=owner_gid, source_binding=binding)
    _LIVE = receipt
    return assert_current_seal(owner_uid=owner_uid, owner_gid=owner_gid, source_binding=binding)


def install_root_actor_seal(*, owner_uid, owner_gid, source_binding, host_pid_namespace_inode,
                            host_mount_namespace_inode, host_device_number, host_devpts_device_number,
                            control_root, inherited=None):
    """Seal an already admitted ROOT PID1, or its actual inherited ROOT actor.

    Caller authenticates host identifiers, including the issuer's own devpts
    device before systemd starts, from its LIVE custody witness; this
    function supplies no ROOT launcher or substitute for that authorization.
    Private setup is completed before this irreversible call. Never use on the
    quota bootstrap, which still needs mounts, devices and quota setters.
    """
    return _install(owner_uid=owner_uid, owner_gid=owner_gid, source_binding=source_binding,
        inherited=inherited, scope="ROOT_POST_SETUP_ONLY", boundary_arguments={
            "host_pid_namespace_inode": host_pid_namespace_inode, "host_mount_namespace_inode": host_mount_namespace_inode,
            "host_device_number": host_device_number, "host_devpts_device_number": host_devpts_device_number,
            "control_root": control_root})


def install_nonroot_test_seal(*, owner_uid, owner_gid, source_binding=None, inherited=None):
    """Real kernel filter in own NONROOT child, with no ROOT isolation credit."""
    return _install(owner_uid=owner_uid, owner_gid=owner_gid, source_binding=source_binding,
                    inherited=inherited, scope="NONROOT_TEST_ONLY")


def assert_current_seal(*, owner_uid, owner_gid, source_binding=None):
    require(_LIVE is not None, "ROOT_SEAL_CURRENT_PROCESS_INSTALLATION_MISSING")
    receipt = _LIVE
    require(receipt["identity"] == _identity() and receipt["source_binding"] == native._binding(source_binding),
            "ROOT_SEAL_CURRENT_PROCESS_OR_SOURCE_REBOUND")
    status = native._status()
    require(status.get("NoNewPrivs") == "1" and status.get("Seccomp") == "2"
            and native._counter(status) == receipt["filters_after"] and _caps(status) == receipt["capabilities"]
            and ctypes.CDLL(None).prctl(3, 0, 0, 0, 0) == 0, "ROOT_SEAL_LIVE_KERNEL_STATE_REBOUND")
    if receipt["scope"] == "ROOT_POST_SETUP_ONLY":
        require(os.getresuid() == (0, 0, 0), "ROOT_SEAL_ROOT_ROLE_ALREADY_DROPPED")
        _descriptors()
    return validate_evidence(receipt, owner_uid=owner_uid, owner_gid=owner_gid, source_binding=source_binding)
