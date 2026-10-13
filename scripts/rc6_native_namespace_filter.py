"""Install and prove the original namespace/quota filter in the current process.

This module never constructs a namespace, changes a quota or launches ROOT.
The exported BPF is the program submitted by this code, not a privileged
readback of kernel filter bytes. A successful native load, an exact counter
increment and safe denied syscalls together witness our own installation.
Serialized evidence alone cannot authorize a current worker.
"""
from __future__ import annotations

import copy
import argparse
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import stat
import subprocess
import sys
import threading

SCHEMA = "porota.rc6.native-namespace-filter.v1"
INHERITANCE_SCHEMA = "porota.rc6.native-namespace-filter-inheritance.v1"
FSSETXATTR = 0x401C5820
SETFLAGS = (0x40086602, 0x40046602)
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
PRCTL_DENIED = (4, 8, 24, 28, 47)
ALLOW, ERRNO, MASKED_EQ = 0x7FFF0000, 0x00050000, 7
# clone's flags argument is zero on these native 64-bit ABIs. Reject other
# architectures instead of assuming libseccomp translates argument positions.
SUPPORTED_ARCHITECTURES = (0xC000003E, 0xC00000B7)
_LIVE = None
ROOT = Path(__file__).absolute().parents[1]
DRIVER = ROOT / "scripts/rc6_controlled_native_child_manager.py"
DRIVER_SHA256 = "55325b3108e175a42b87ebe544fd307fa45ffd29b6f7ab471443803ad9ba53b8"


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def decode(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, "NATIVE_FILTER_DUPLICATE_JSON_FIELD")
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError("NATIVE_FILTER_NONFINITE_JSON_FORBIDDEN")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)


def build_plan():
    """A fresh copy of the unchanged historical calibration filter contract."""
    return {"default": "ALLOW", "denied_syscalls": list(DENIED_SYSCALLS),
            "denied_ioctl_requests": [FSSETXATTR, *SETFLAGS],
            "denied_clone_namespace_flags": list(NAMESPACE_CLONE_FLAGS),
            "clone3_errno": errno.ENOSYS, "denied_prctl": list(PRCTL_DENIED),
            "no_new_privileges": True}


def _binding(value):
    if value is None:
        return None
    require(type(value) is dict and set(value) in (
        {"source_sha", "source_tree"}, {"source_sha", "source_tree", "plan_sha256"}),
        "NATIVE_FILTER_EXACT_SOURCE_BINDING_REQUIRED")
    require(all(type(item) is str and re.fullmatch("[0-9a-f]{" + str(
        64 if key == "plan_sha256" else 40) + "}", item) for key, item in value.items()),
        "NATIVE_FILTER_EXACT_SOURCE_BINDING_REQUIRED")
    return dict(value)


def _status():
    raw = Path("/proc/self/status").read_bytes()
    require(len(raw) <= 65536, "NATIVE_FILTER_STATUS_BOUND_EXCEEDED")
    return {line.partition(":")[0]: line.partition(":")[2].strip()
            for line in raw.decode().splitlines() if ":" in line}


def _counter(status):
    value = status.get("Seccomp_filters", "")
    require(re.fullmatch(r"[0-9]+", value) and int(value) < 1024,
            "NATIVE_FILTER_ACTUAL_KERNEL_FILTER_COUNTER_REQUIRED")
    return int(value)


def _identity():
    tail = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
    return {"pid": os.getpid(), "parent_pid": os.getppid(),
            "native_tid": threading.get_native_id(), "start_ticks": int(tail[19]),
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "mount_namespace_inode": os.stat("/proc/self/ns/mnt").st_ino,
            "cgroup_namespace_inode": os.stat("/proc/self/ns/cgroup").st_ino}


def _nonroot(status, *, single_thread=False):
    require(sys.platform == "linux" and ctypes.sizeof(ctypes.c_void_p) == 8,
            "NATIVE_FILTER_NATIVE_LINUX_64_REQUIRED")
    require(os.getuid() == os.geteuid() > 0 and int(status.get("CapEff", "-1"), 16) == 0,
            "NATIVE_FILTER_NONROOT_ZERO_EFFECTIVE_CAPABILITIES_REQUIRED")
    require(threading.get_native_id() == os.getpid(), "NATIVE_FILTER_MAIN_THREAD_REQUIRED")
    if single_thread:
        require(status.get("Threads") == "1" and len(os.listdir("/proc/self/task")) == 1,
                "NATIVE_FILTER_SINGLE_THREAD_INSTALLATION_REQUIRED")


class Comparison(ctypes.Structure):
    _fields_ = [("arg", ctypes.c_uint), ("op", ctypes.c_uint),
                ("a", ctypes.c_uint64), ("b", ctypes.c_uint64)]


class Version(ctypes.Structure):
    _fields_ = [("major", ctypes.c_uint), ("minor", ctypes.c_uint), ("micro", ctypes.c_uint)]


def _library():
    lib = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    lib.seccomp_init.argtypes, lib.seccomp_init.restype = [ctypes.c_uint32], ctypes.c_void_p
    lib.seccomp_arch_native.argtypes, lib.seccomp_arch_native.restype = [], ctypes.c_uint32
    lib.seccomp_version.argtypes, lib.seccomp_version.restype = [], ctypes.POINTER(Version)
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
    lib.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                                         ctypes.c_uint, ctypes.c_void_p]
    lib.seccomp_rule_add_array.restype = ctypes.c_int
    lib.seccomp_attr_set.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32]
    lib.seccomp_attr_set.restype = ctypes.c_int
    lib.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.seccomp_export_bpf.restype = ctypes.c_int
    lib.seccomp_load.argtypes, lib.seccomp_load.restype = [ctypes.c_void_p], ctypes.c_int
    lib.seccomp_release.argtypes, lib.seccomp_release.restype = [ctypes.c_void_p], None
    return lib


def _compiled_context(lib):
    arch = int(lib.seccomp_arch_native())
    require(arch in SUPPORTED_ARCHITECTURES, "NATIVE_FILTER_UNREVIEWED_NATIVE_ARCHITECTURE")
    context = lib.seccomp_init(ALLOW)
    require(context, "NATIVE_FILTER_CONTEXT_REQUIRED")
    try:
        # NNP and TSYNC are explicit; unknown/unsupported attributes fail closed.
        for attribute in (3, 4):
            require(lib.seccomp_attr_set(context, attribute, 1) == 0,
                    "NATIVE_FILTER_NNP_OR_TSYNC_ATTRIBUTE_FAILED")
        resolved, rules = {}, []

        def add(name, denied_errno=errno.EPERM, comparisons=()):
            number = int(lib.seccomp_syscall_resolve_name(name.encode()))
            require(number >= 0, "NATIVE_FILTER_SYSCALL_UNKNOWN:" + name)
            resolved[name] = number
            values = (Comparison * len(comparisons))(*(Comparison(*row) for row in comparisons))
            require(lib.seccomp_rule_add_array(context, ERRNO | denied_errno, number,
                len(values), values) == 0, "NATIVE_FILTER_RULE_FAILED:" + name)
            rules.append({"syscall": name, "number": number, "errno": denied_errno,
                          "comparisons": [list(row) for row in comparisons]})

        for name in DENIED_SYSCALLS:
            add(name)
        for request in (FSSETXATTR, *SETFLAGS):
            add("ioctl", comparisons=((1, MASKED_EQ, 0xFFFFFFFF, request),))
        for flag in NAMESPACE_CLONE_FLAGS:
            add("clone", comparisons=((0, MASKED_EQ, flag, flag),))
        add("clone3", errno.ENOSYS)
        for option in PRCTL_DENIED:
            add("prctl", comparisons=((0, MASKED_EQ, 0xFFFFFFFF, option),))
        fd = os.memfd_create("rc6-native-filter-bpf", os.MFD_CLOEXEC)
        try:
            require(lib.seccomp_export_bpf(context, fd) == 0, "NATIVE_FILTER_BPF_EXPORT_FAILED")
            size = os.fstat(fd).st_size
            require(0 < size <= 4096 * 8 and size % 8 == 0, "NATIVE_FILTER_BPF_BOUND_EXCEEDED")
            raw = os.pread(fd, size, 0)
            require(len(raw) == size, "NATIVE_FILTER_BPF_EXPORT_TRUNCATED")
        finally:
            os.close(fd)
        version = lib.seccomp_version().contents
        return context, {"native_architecture": arch,
            "libseccomp_version": [version.major, version.minor, version.micro],
            "resolved_syscalls": resolved, "rules_sha256": digest(canonical(rules)),
            "rule_count": len(rules), "exported_bpf_sha256": digest(raw),
            "exported_bpf_bytes": len(raw), "kernel_bpf_bytes_readback_claimed": False,
            "tsync_requested": True}
    except BaseException:
        lib.seccomp_release(context)
        raise


def _probe_specs():
    # All operands are deliberately invalid even if a missing filter lets them
    # reach the kernel. clone lacks SIGHAND for THREAD, so it cannot fork; no
    # pre-install attempt mounts or creates a namespace.
    probes = [("mount", "mount", (0, 0, 0, 0, 0), errno.EPERM),
              ("umount2", "umount2", (0, 0x80000000), errno.EPERM),
              ("unshare", "unshare", (1 << 63,), errno.EPERM),
              ("setns", "setns", (-1, 0), errno.EPERM),
              ("clone3", "clone3", (0, 0), errno.ENOSYS),
              ("quotactl", "quotactl", (0, 0, 0, 0), errno.EPERM),
              ("quotactl_fd", "quotactl_fd", (-1, 0, 0, 0), errno.EPERM),
              ("ptrace_parent_invalid_request", "ptrace", (0xFFFFFFFF, os.getppid(), 0, 0), errno.EPERM),
              ("process_vm_readv_parent_invalid_iovec", "process_vm_readv", (os.getppid(), 0, 1, 0, 1, 0), errno.EPERM),
              ("process_vm_writev_parent_invalid_iovec", "process_vm_writev", (os.getppid(), 0, 1, 0, 1, 0), errno.EPERM)]
    probes.extend(("clone_namespace_" + str(flag), "clone", (flag | 0x10000, 0, 0, 0, 0), errno.EPERM)
                  for flag in NAMESPACE_CLONE_FLAGS)
    probes.extend(("ioctl_" + str(request), "ioctl", (-1, request, 0), errno.EPERM)
                  for request in (FSSETXATTR, *SETFLAGS))
    probes.extend(("prctl_" + str(option), "prctl", (option, 0xFFFFFFFF, 0, 0, 0), errno.EPERM)
                  for option in PRCTL_DENIED)
    return probes


def _denials():
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    lib = _library()
    observed = []
    for label, name, arguments, expected in _probe_specs():
        number = int(lib.seccomp_syscall_resolve_name(name.encode()))
        require(number >= 0, "NATIVE_FILTER_PROBE_SYSCALL_UNKNOWN")
        ctypes.set_errno(0)
        result = int(libc.syscall(ctypes.c_long(number), *(ctypes.c_ulonglong(x) for x in arguments)))
        saved_errno = ctypes.get_errno()
        require(result == -1 and saved_errno == expected, "NATIVE_FILTER_DENIAL_NOT_OBSERVED:" + label)
        observed.append({"probe": label, "syscall": name, "number": number,
                         "returncode": result, "errno": saved_errno})
    return observed


def validate_evidence(receipt, *, source_binding=None):
    """Validate captured evidence; this never enables a current process."""
    require(type(receipt) is dict and receipt.get("schema") == SCHEMA
            and receipt.get("source_binding") == _binding(source_binding)
            and receipt.get("plan") == build_plan()
            and receipt.get("plan_sha256") == digest(canonical(build_plan()))
            and type(receipt.get("seccomp_load_returncode")) is int and receipt["seccomp_load_returncode"] == 0
            and type(receipt.get("filters_before")) is int
            and type(receipt.get("filters_after")) is int
            and 0 <= receipt["filters_before"] < 1024
            and receipt["filters_after"] == receipt["filters_before"] + 1
            and type(receipt.get("native_architecture")) is int
            and receipt["native_architecture"] in SUPPORTED_ARCHITECTURES
            and receipt.get("tsync_requested") is True
            and receipt.get("no_new_privileges") is True
            and type(receipt.get("dumpable")) is int and receipt["dumpable"] in (0, 1)
            and receipt.get("kernel_bpf_bytes_readback_claimed") is False
            and receipt.get("G0_G8_qualification") is False,
            "NATIVE_FILTER_AUTHENTIC_INSTALLATION_EVIDENCE_REQUIRED")
    require(all(type(receipt.get(key)) is str and re.fullmatch("[0-9a-f]{64}", receipt[key])
                for key in ("exported_bpf_sha256", "rules_sha256", "module_sha256")),
            "NATIVE_FILTER_PROGRAM_HASH_REQUIRED")
    require(type(receipt.get("exported_bpf_bytes")) is int
            and 0 < receipt["exported_bpf_bytes"] <= 4096 * 8 and receipt["exported_bpf_bytes"] % 8 == 0,
            "NATIVE_FILTER_BPF_BOUND_EXCEEDED")
    rows = receipt.get("denial_probes")
    expected = _probe_specs()
    require(type(rows) is list and len(rows) == len(expected)
            and all(type(row) is dict and row.get("probe") == label and row.get("syscall") == name
                and type(row.get("returncode")) is int and row["returncode"] == -1
                and type(row.get("errno")) is int and row["errno"] == denied_errno
                for row, (label, name, _, denied_errno) in zip(rows, expected)),
            "NATIVE_FILTER_COMPLETE_NATIVE_DENIALS_REQUIRED")
    require(type(receipt.get("identity")) is dict and all(
        type(receipt["identity"].get(key)) is int and receipt["identity"][key] > 0
        for key in ("pid", "parent_pid", "native_tid", "start_ticks",
                    "mount_namespace_inode", "cgroup_namespace_inode")),
        "NATIVE_FILTER_KERNEL_PROCESS_IDENTITY_REQUIRED")
    require(receipt["identity"]["pid"] == receipt["identity"]["native_tid"]
            and type(receipt["identity"].get("boot_id")) is str
            and re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", receipt["identity"]["boot_id"]),
            "NATIVE_FILTER_KERNEL_PROCESS_IDENTITY_REQUIRED")
    return copy.deepcopy(receipt)


def validate_inheritance_evidence(child_receipt, parent_receipt, *, source_binding=None):
    """Link captured child observations to its supervisor's original bytes."""
    child = validate_evidence(child_receipt, source_binding=source_binding)
    parent = validate_evidence(parent_receipt, source_binding=source_binding)
    proof = child.get("inherited")
    require(type(proof) is dict and proof.get("schema") == INHERITANCE_SCHEMA
            and proof.get("identity") == child["identity"]
            and proof.get("parent_identity") == parent["identity"]
            and child["identity"]["parent_pid"] == parent["identity"]["pid"]
            and proof.get("parent_receipt_sha256") == digest(canonical(parent))
            and proof.get("source_binding") == _binding(source_binding)
            and proof.get("fork_exec_inheritance_observed") is True
            and type(proof.get("filters_before_own_installation")) is int
            and proof["filters_before_own_installation"] == child["filters_before"] == parent["filters_after"]
            and proof.get("native_denials_before_own_installation") == child["denial_probes"]
            and proof.get("G0_G8_qualification") is False,
            "NATIVE_FILTER_COMPLETE_PARENT_CHILD_INHERITANCE_EVIDENCE_REQUIRED")
    require(all(child[key] == parent[key] for key in (
        "plan_sha256", "module_sha256", "rules_sha256", "exported_bpf_sha256", "exported_bpf_bytes",
        "native_architecture")) and all(child["identity"][key] == parent["identity"][key]
        for key in ("boot_id", "mount_namespace_inode", "cgroup_namespace_inode")),
        "NATIVE_FILTER_INHERITED_PROGRAM_OR_NAMESPACE_REBOUND")
    return copy.deepcopy(proof)


def assert_current_filter(*, source_binding=None):
    require(_LIVE is not None, "NATIVE_FILTER_CURRENT_PROCESS_INSTALLATION_MISSING")
    receipt = _LIVE
    require(receipt["identity"] == _identity(), "NATIVE_FILTER_PROCESS_OR_NAMESPACE_REBOUND")
    require(receipt["source_binding"] == _binding(source_binding), "NATIVE_FILTER_SOURCE_BINDING_REBOUND")
    status = _status()
    _nonroot(status)
    libc = ctypes.CDLL(None, use_errno=True)
    require(status.get("NoNewPrivs") == "1" and status.get("Seccomp") == "2"
            and _counter(status) == receipt["filters_after"]
            and libc.prctl(3, 0, 0, 0, 0) == receipt["dumpable"],
            "NATIVE_FILTER_LIVE_KERNEL_INSTALLATION_CHANGED")
    return validate_evidence(receipt, source_binding=source_binding)


def validate_inheritance(parent_receipt, *, source_binding=None):
    parent = validate_evidence(parent_receipt, source_binding=source_binding)
    status, current = _status(), _identity()
    _nonroot(status, single_thread=True)
    identity = parent["identity"]
    require(current["pid"] != identity["pid"] and current["parent_pid"] == identity["pid"],
            "NATIVE_FILTER_ACTUAL_PARENT_INHERITANCE_REQUIRED")
    parent_ticks = int(Path("/proc/" + str(identity["pid"]) + "/stat").read_text().rsplit(")", 1)[1].split()[19])
    require(parent_ticks == identity["start_ticks"] and all(current[key] == identity[key]
        for key in ("boot_id", "mount_namespace_inode", "cgroup_namespace_inode")),
        "NATIVE_FILTER_PARENT_OR_NAMESPACE_REBOUND")
    require(status.get("NoNewPrivs") == "1" and status.get("Seccomp") == "2"
            and _counter(status) == parent["filters_after"], "NATIVE_FILTER_INHERITED_COUNTER_OR_NNP_CHANGED")
    return {"schema": INHERITANCE_SCHEMA, "identity": current,
            "parent_identity": identity, "parent_receipt_sha256": digest(canonical(parent)),
            "source_binding": _binding(source_binding), "filters_before_own_installation": _counter(status),
            "native_denials_before_own_installation": _denials(),
            "fork_exec_inheritance_observed": True, "G0_G8_qualification": False}


def install_filter(*, source_binding=None, inherited=None):
    """Install in the NONROOT caller, with optional prior fork/exec evidence."""
    global _LIVE
    binding = _binding(source_binding)
    if _LIVE is not None and _LIVE["identity"]["pid"] == os.getpid():
        require(inherited is None, "NATIVE_FILTER_ALREADY_INSTALLED_IN_CURRENT_WORKER")
        return assert_current_filter(source_binding=binding)
    before = _status()
    _nonroot(before, single_thread=True)
    count = _counter(before)
    inherited_proof = validate_inheritance(inherited, source_binding=binding) if inherited is not None else None
    libc = ctypes.CDLL(None, use_errno=True)
    if inherited is None:
        require(libc.prctl(3, 0, 0, 0, 0) == 0 or libc.prctl(4, 0, 0, 0, 0) == 0,
                "NATIVE_FILTER_SUPERVISOR_DUMPABLE_ZERO_FAILED")
        require(libc.prctl(3, 0, 0, 0, 0) == 0, "NATIVE_FILTER_SUPERVISOR_DUMPABLE_ZERO_FAILED")
    else:
        # Ordinary exec restores dumpability. The historical filter denies all
        # PR_SET_DUMPABLE, including a second set-to-zero. Preserve that rule
        # and record the child value; its supervisor remains non-dumpable.
        require(inherited["dumpable"] == 0, "NATIVE_FILTER_PARENT_CUSTODY_DUMPABLE_ZERO_REQUIRED")
    require(libc.prctl(38, 1, 0, 0, 0) == 0 and libc.prctl(39, 0, 0, 0, 0) == 1,
            "NATIVE_FILTER_NO_NEW_PRIVILEGES_FAILED")
    lib = _library()
    context, program = _compiled_context(lib)
    try:
        result = int(lib.seccomp_load(context))
        require(result == 0, "NATIVE_FILTER_NATIVE_LOAD_FAILED")
    finally:
        lib.seccomp_release(context)
    after = _status()
    require(after.get("NoNewPrivs") == "1" and after.get("Seccomp") == "2"
            and _counter(after) == count + 1, "NATIVE_FILTER_NATIVE_INSTALLATION_COUNTER_NOT_INCREMENTED")
    receipt = {"schema": SCHEMA, "source_binding": binding, "identity": _identity(),
        "plan": build_plan(), "plan_sha256": digest(canonical(build_plan())),
        "module_sha256": digest(Path(__file__).read_bytes()), **program,
        "filters_before": count, "filters_after": _counter(after), "seccomp_load_returncode": result,
        "no_new_privileges": True, "dumpable": int(libc.prctl(3, 0, 0, 0, 0)),
        "denial_probes": _denials(), "inherited": inherited_proof,
        "G0_G8_qualification": False, "real_orders_sent": 0}
    validate_evidence(receipt, source_binding=binding)
    _LIVE = receipt
    return assert_current_filter(source_binding=binding)


def _exclusive_control(path, value):
    raw = canonical(value)
    require(len(raw) <= 65536, "NATIVE_FILTER_CHEAP_CONTROL_BOUND_EXCEEDED")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return digest(raw)


def _parent_control(path, expected_hash):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts
            and not any(row.is_symlink() for row in (path, *path.parents))
            and re.fullmatch("[0-9a-f]{64}", expected_hash or ""), "NATIVE_FILTER_CHEAP_PARENT_CONTROL_REQUIRED")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        details = os.fstat(fd)
        require(stat.S_ISREG(details.st_mode) and details.st_uid == os.geteuid()
                and stat.S_IMODE(details.st_mode) == 0o600 and 0 < details.st_size <= 65536,
                "NATIVE_FILTER_CHEAP_PARENT_CONTROL_REQUIRED")
        raw = os.read(fd, 65537)
    finally:
        os.close(fd)
    require(len(raw) == details.st_size and digest(raw) == expected_hash,
            "NATIVE_FILTER_CHEAP_PARENT_CONTROL_HASH_REBOUND")
    return decode(raw)


def run_cheap_probe(*, output, source_binding=None):
    """Own NONROOT proof only; preserve RAW after the unchanged manager's FIN.

    The caller supplies a private admitted storage namespace. This function
    never obtains ROOT, creates a mount, installs packages or runs Product157.
    It does not prove quota enforcement or the complete privilege-drop profile.
    """
    binding = _binding(source_binding)
    output = Path(output)
    require(output.is_absolute() and ".." not in output.parts and not output.is_relative_to(ROOT)
            and not any(row.is_symlink() for row in (output, *output.parents)),
            "NATIVE_FILTER_CHEAP_OUTPUT_OUTSIDE_SOURCE_REQUIRED")
    supervisor = install_filter(source_binding=binding)
    output.mkdir(mode=0o700)
    parent_path = output / "supervisor-filter.json"
    parent_hash = _exclusive_control(parent_path, supervisor)
    command = [sys.executable, "-I", "-B", str(Path(__file__).absolute()), "--worker",
        "--parent-control", str(parent_path), "--parent-control-sha256", parent_hash]
    if binding is not None:
        command.extend(["--source-sha", binding["source_sha"], "--source-tree", binding["source_tree"]])
        if "plan_sha256" in binding:
            command.extend(["--plan-sha256", binding["plan_sha256"]])
    require(digest(DRIVER.read_bytes()) == DRIVER_SHA256, "NATIVE_FILTER_ORIGINAL_MANAGER_REBOUND")
    manager = runpy.run_path(str(DRIVER))
    environment = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ}
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    kernel = manager["managed_native_child"](command, output, output / "worker-native.json",
        environment, 15, terminate_grace=2, progress_poll=5)
    require(manager["managed_custody_closed"](kernel), "NATIVE_FILTER_CHEAP_ACTUAL_OWN_FIN_REQUIRED")
    # Only a real FIN admits RAW reads. No retry or partial evidence becomes
    # qualification. Preserve this original kernel history even on worker RED.
    _exclusive_control(output / "native-owned-fin.json", {
        "schema": "porota.rc6.native-filter-owned-fin.v1", "manager_sha256": DRIVER_SHA256,
        "kernel": kernel, "actual_owned_fin_closed": True, "G0_G8_qualification": False})
    require(kernel["returncode"] == 0, "NATIVE_FILTER_CHEAP_NATIVE_WORKER_RED")
    descriptor = os.open(output / "worker-native.json", os.O_RDONLY | os.O_NOFOLLOW)
    try:
        details = os.fstat(descriptor)
        require(stat.S_ISREG(details.st_mode) and details.st_uid == os.geteuid()
                and not stat.S_IMODE(details.st_mode) & 0o022 and 0 < details.st_size <= 65536,
                "NATIVE_FILTER_CHEAP_WORKER_RAW_BOUND_EXCEEDED")
        raw = os.read(descriptor, 65537)
    finally:
        os.close(descriptor)
    require(len(raw) == details.st_size, "NATIVE_FILTER_CHEAP_WORKER_RAW_BOUND_EXCEEDED")
    worker = decode(raw)
    validate_inheritance_evidence(worker, supervisor, source_binding=binding)
    require(worker["identity"]["pid"] == kernel["pid"]
            and kernel["supervisor_pid"] == os.getpid(), "NATIVE_FILTER_CHEAP_ACTUAL_CHILD_BINDING_REQUIRED")
    assert_current_filter(source_binding=binding)
    observed_status = _status()
    result = {"schema": "porota.rc6.native-filter-cheap-probe.v1", "status": "PASS_NATIVE_FILTER_ONLY",
        "scope": "OWN_NONROOT_NATIVE_FILTER_NOT_G0_OR_PRODUCT_RESOURCE_QUALIFICATION",
        "source_binding": binding, "supervisor": supervisor, "worker": worker,
        "kernel": kernel, "original_native_fin": True,
        "worker_raw_sha256": digest(raw), "supervisor_control_sha256": parent_hash,
        "observed_uid_gid_groups_capabilities": {key: observed_status[key]
            for key in ("Uid", "Gid", "Groups", "CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")},
        "complete_nonroot_privilege_drop_qualified": False,
        "aggregate_storage_security_qualified": False, "product157_executed": False,
        "product_fixture_consumers_executed": False, "ROOT_executed_by_this_probe": False,
        "G0_G8_qualification": False, "recurring_infrastructure_cost_usd": 0, "real_orders_sent": 0}
    _exclusive_control(output / "probe.json", result)
    return result


def _exact_source(binding):
    if binding is None:
        return
    def git(*arguments):
        return subprocess.run(["git", *arguments], cwd=ROOT, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=5).stdout.decode().strip()
    require(git("rev-parse", "HEAD") == binding["source_sha"]
            and git("rev-parse", "HEAD^{tree}") == binding["source_tree"]
            and not git("status", "--porcelain"), "NATIVE_FILTER_CLEAN_FROZEN_SOURCE_REQUIRED")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--source-sha")
    parser.add_argument("--source-tree")
    parser.add_argument("--plan-sha256")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--parent-control", type=Path)
    parser.add_argument("--parent-control-sha256")
    args = parser.parse_args(argv)
    require(args.probe != args.worker, "NATIVE_FILTER_ONE_CHEAP_MODE_REQUIRED")
    binding = None
    if args.source_sha is not None or args.source_tree is not None or args.plan_sha256 is not None:
        binding = {"source_sha": args.source_sha, "source_tree": args.source_tree}
        if args.plan_sha256 is not None:
            binding["plan_sha256"] = args.plan_sha256
        binding = _binding(binding)
    _exact_source(binding)
    if args.worker:
        require(args.parent_control is not None, "NATIVE_FILTER_CHEAP_PARENT_CONTROL_REQUIRED")
        parent = _parent_control(args.parent_control, args.parent_control_sha256)
        result = install_filter(source_binding=binding, inherited=parent)
    else:
        require(args.output is not None, "NATIVE_FILTER_CHEAP_OUTPUT_REQUIRED")
        result = run_cheap_probe(output=args.output, source_binding=binding)
    raw = canonical(result)
    require(len(raw) <= 65536, "NATIVE_FILTER_CHEAP_STDOUT_BOUND_EXCEEDED")
    sys.stdout.buffer.write(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
