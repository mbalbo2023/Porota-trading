"""Positive ownership of idle stdlib tracker infrastructure, never phase FIN.

No tracker is stopped, no workload child is joined/reaped, and no global
finalizer is invoked here. Kernel child census is mandatory. Its absence is
BLOCKED, including execution environments without Linux's task/children view.
"""
from __future__ import annotations

from dataclasses import dataclass
import array
import errno
import fcntl
import hashlib
import inspect
import os
from pathlib import Path
import re
import stat
import sys
import termios
import threading
import time
import types

from scripts import porota_predeploy_cleanup as custody
from scripts import rc6_heavy_test_preflight as capacity

_AUTHORITY = object()
_ISSUED = {}


def _read_proc(pid, member, maximum=65536):
    custody.require(type(pid) is int and pid > 0 and type(member) is str and ".." not in member.split("/"),
                    "INFRA_PROCESS_MEMBER_INVALID")
    anchor = Path("/proc") / str(pid)
    with custody.directory(anchor) as parent:
        descriptor = os.open(member, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        try:
            raw = os.read(descriptor, maximum + 1)
            custody.require(len(raw) <= maximum, "INFRA_PROCESS_MEMBER_TOO_LARGE")
            return raw
        finally:
            os.close(descriptor)


def own_kernel_children():
    """Complete census of THIS process's own tasks, no global /proc scan."""
    root = Path("/proc") / str(os.getpid()) / "task"
    before = sorted(os.listdir(root))
    children = set()
    for task in before:
        custody.require(task.isdecimal(), "INFRA_OWN_TASK_INVALID")
        try:
            raw = _read_proc(os.getpid(), "task/" + task + "/children")
        except FileNotFoundError:
            # Missing children on an extant task is an unavailable capability,
            # not an empty census. A task race is also conservatively blocked.
            raise ValueError("INFRA_OWN_KERNEL_CHILD_CENSUS_UNAVAILABLE_OR_RACED")
        custody.require(all(piece.isdecimal() for piece in raw.split()), "INFRA_OWN_CHILD_CENSUS_INVALID")
        children.update(int(piece) for piece in raw.split())
    custody.require(before == sorted(os.listdir(root)), "INFRA_OWN_TASK_CENSUS_CHANGED")
    return sorted(children)


def process_identity(pid):
    status = _read_proc(pid, "status").decode()
    fields = {line.partition(":")[0]: line.partition(":")[2].strip() for line in status.splitlines()}
    raw_stat = _read_proc(pid, "stat").decode()
    stat_fields = raw_stat.rsplit(")", 1)[1].split()
    custody.require(fields.get("Pid") == str(pid) and fields.get("PPid") == str(os.getpid())
                    and fields.get("Uid", "").split() == [str(os.getuid())] * 4
                    and fields.get("Gid", "").split() == [str(os.getgid())] * 4
                    and fields.get("Threads") == "1", "INFRA_NOT_ONE_OWN_KERNEL_CHILD")
    return {"pid": pid, "parent_pid": int(fields["PPid"]), "uid": os.getuid(), "gid": os.getgid(),
            "starttime_ticks": int(stat_fields[19]), "thread_count": 1,
            "state": stat_fields[0]}


def _fd_info(pid, descriptor):
    path = Path("/proc") / str(pid) / "fd" / str(descriptor)
    info = os.stat(path)
    target = os.readlink(path)
    return {"fd": descriptor, "device": info.st_dev, "inode": info.st_ino,
            "mode": info.st_mode, "uid": info.st_uid, "target": target}


def namespace_process_references(pid, namespace, measured):
    """Own/positively owned process metadata only, no mapped payload reads."""
    roots = {(namespace.identity[0], namespace.identity[1])}
    roots.update(tuple(row["identity"][:2]) for row in measured["rows"].values())
    for member in ("cwd", "root", "exe"):
        path = Path("/proc") / str(pid) / member
        value, target = os.stat(path), os.readlink(path)
        custody.require((value.st_dev, value.st_ino) not in roots
                        and target != str(namespace.path) and not target.startswith(str(namespace.path) + "/"),
                        "INFRA_PROCESS_CWD_ROOT_OR_EXEC_REFERENCES_FIXTURE_NAMESPACE")
    for line in _read_proc(pid, "maps", maximum=8 * 1024**2).decode().splitlines():
        row = line.split(None, 5)
        custody.require(len(row) >= 5, "INFRA_PROCESS_MAPS_METADATA_INVALID")
        major, minor = row[3].split(":")
        inode = int(row[4])
        device = os.makedev(int(major, 16), int(minor, 16))
        target = row[5] if len(row) == 6 else ""
        custody.require((device, inode) not in roots and target != str(namespace.path)
                        and not target.startswith(str(namespace.path) + "/"),
                        "INFRA_PROCESS_MEMORY_MAP_REFERENCES_FIXTURE_NAMESPACE")
    for name in os.listdir(Path("/proc") / str(pid) / "fd"):
        custody.require(name.isdecimal(), "INFRA_PROCESS_DESCRIPTOR_METADATA_INVALID")
        try:
            row = _fd_info(pid, int(name))
        except FileNotFoundError:
            continue  # Our short-lived metadata descriptors may have closed.
        custody.require((row["device"], row["inode"]) not in roots
                        and row["target"] != str(namespace.path)
                        and not row["target"].startswith(str(namespace.path) + "/"),
                        "INFRA_PROCESS_FD_REFERENCES_FIXTURE_NAMESPACE")


def _code_hash(function):
    # Marshal's reference/intern flags may change after a function executes.
    # Fingerprint semantic immutable code fields, not interpreter cache state.
    def constant(value):
        if type(value) is types.CodeType:
            return {"type": "code", "value": code(value)}
        if value is None or type(value) in (bool, int, str):
            return {"type": type(value).__name__, "value": value}
        if type(value) is bytes:
            return {"type": "bytes", "value": value.hex()}
        if type(value) is float:
            return {"type": "float", "value": value.hex()}
        if type(value) in (tuple, frozenset):
            rows = [constant(member) for member in value]
            if type(value) is frozenset:
                rows.sort(key=capacity.canonical)
            return {"type": type(value).__name__, "value": rows}
        raise ValueError("INFRA_UNSUPPORTED_STDLIB_CODE_CONSTANT")

    def code(value):
        return {"bytecode": value.co_code.hex(), "names": value.co_names, "varnames": value.co_varnames,
                "freevars": value.co_freevars, "cellvars": value.co_cellvars, "flags": value.co_flags,
                "argcount": value.co_argcount, "posonlyargcount": value.co_posonlyargcount,
                "kwonlyargcount": value.co_kwonlyargcount, "constants": [constant(member) for member in value.co_consts],
                "exceptiontable": value.co_exceptiontable.hex(), "linetable": value.co_linetable.hex()}
    return capacity.digest(code(function.__code__))


@dataclass(frozen=True)
class _ScopedTrackerLease:
    authority: object
    namespace_nonce: str
    monitor: object
    observation: dict


class SessionResourceTrackerLease:
    """Observe the original tracker spawn and original protocol writes.

    Forwarders preserve the original args/results/exceptions. Observation errors
    never substitute another producer implementation or manufacture ownership.
    They are sticky blockers for subsequent fixture cleanup admission.
    """
    def __init__(self, binding):
        from multiprocessing import resource_tracker, util, spawn
        capacity.validate_binding(binding)
        self.binding = dict(binding)
        self.pid, self.tid = os.getpid(), threading.get_native_id()
        self.resource_tracker, self.util, self.spawn = resource_tracker, util, spawn
        self.tracker = resource_tracker._resource_tracker
        self.ensure_original = self.tracker.ensure_running
        self.send_original = self.tracker._send
        self.spawn_original = util.spawnv_passfds
        self.main_original = resource_tracker.main
        self.codes = {"ensure_running": _code_hash(self.ensure_original.__func__),
                      "send": _code_hash(self.send_original.__func__),
                      "spawnv_passfds": _code_hash(self.spawn_original),
                      "main": _code_hash(self.main_original)}
        self.stdlib_files = {}
        for module in (resource_tracker, util, spawn):
            path = Path(inspect.getsourcefile(module)).absolute()
            before = path.stat()
            raw = path.read_bytes()  # Ordinary CODE read; never reset atime.
            after = path.stat()
            custody.require(custody.identity(before) == custody.identity(after), "INFRA_STDLIB_CODE_CHANGED")
            self.stdlib_files[module.__name__] = {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw), "identity": custody.identity(after)}
        self.initial = {"pid": self.tracker._pid, "fd": self.tracker._fd}
        self.errors = []
        self.birth = None
        self.registrations = {}
        self.protocol_events = 0
        self.spawn_context = 0
        self.positive_observations = 0
        self.installed = False
        if self.initial != {"pid": None, "fd": None}:
            self.errors.append("INFRA_EXISTING_OR_INHERITED_TRACKER_HAS_NO_BIRTH_AUTHORITY")

    def _context(self):
        custody.require(os.getpid() == self.pid and threading.get_native_id() == self.tid,
                        "INFRA_OWNER_CONTEXT_CHANGED")

    def install(self):
        self._context()
        custody.require(not self.installed and getattr(self.ensure_original, "__func__", None)
                        is self.resource_tracker.ResourceTracker.ensure_running
                        and self.spawn_original is self.util.spawnv_passfds,
                        "INFRA_ORIGINAL_INSTANCE_AND_SPAWN_REQUIRED")

        def observe_spawn(path, args, passfds):
            expected = self.spawn_context > 0 and os.getpid() == self.pid and threading.get_native_id() == self.tid
            inherited = None
            if expected:
                try:
                    inherited = [_fd_info(self.pid, int(fd)) for fd in passfds]
                except (OSError, ValueError) as error:
                    self.errors.append(str(error))
            result = self.spawn_original(path, args, passfds)
            if expected:
                try:
                    self._observe_birth(result, path, args, passfds, inherited)
                except (OSError, ValueError, KeyError, TypeError) as error:
                    self.errors.append(str(error))
            return result

        def observe_ensure(*args, **kwargs):
            self.spawn_context += 1
            try:
                return self.ensure_original(*args, **kwargs)
            finally:
                self.spawn_context -= 1

        def observe_send(cmd, name, rtype):
            result = self.send_original(cmd, name, rtype)
            if os.getpid() == self.pid:
                try:
                    self._context()
                    custody.require(cmd in ("REGISTER", "UNREGISTER", "PROBE")
                                    and rtype in ("semaphore", "shared_memory", "noop")
                                    and type(name) is str and len(name) <= 512
                                    and ":" not in name and "\n" not in name,
                                    "INFRA_UNKNOWN_PROTOCOL_RESOURCE")
                    key = (name, rtype)
                    if cmd == "REGISTER":
                        custody.require(key not in self.registrations, "INFRA_DUPLICATE_RESOURCE_REGISTRATION")
                        self.registrations[key] = {"owner_pid": self.pid, "resource_type": rtype,
                            "resource_name_sha256": hashlib.sha256(name.encode()).hexdigest()}
                    elif cmd == "UNREGISTER":
                        custody.require(key in self.registrations, "INFRA_UNOBSERVED_RESOURCE_UNREGISTER")
                        del self.registrations[key]
                    self.protocol_events += 1
                except (OSError, ValueError, KeyError, TypeError) as error:
                    self.errors.append(str(error))
            return result

        self.spawn_wrapper, self.ensure_wrapper, self.send_wrapper = observe_spawn, observe_ensure, observe_send
        self.util.spawnv_passfds = observe_spawn
        self.tracker.ensure_running = observe_ensure
        self.tracker._send = observe_send
        # Preserve the public instance alias too; all calls still forward the
        # exact original method. No class/stop/global-finalizer is replaced.
        if self.resource_tracker.ensure_running == self.ensure_original:
            self.resource_tracker.ensure_running = observe_ensure
        self.installed = True

    def _observe_birth(self, pid, executable, args, passfds, inherited):
        custody.require(self.birth is None and inherited is not None and type(pid) is int and pid > 0,
                        "INFRA_TRACKER_RELAUNCH_OR_UNOBSERVED_BIRTH")
        arguments = [os.fsdecode(value) for value in args]
        custody.require(len(arguments) >= 3 and arguments[-2] == "-c", "INFRA_TRACKER_ORIGINAL_COMMAND_REQUIRED")
        match = re.fullmatch(r"from multiprocessing\.resource_tracker import main;main\(([0-9]+)\)", arguments[-1])
        custody.require(match is not None and int(match[1]) in list(passfds)
                        and Path(os.fsdecode(executable)).resolve() == Path(sys.executable).resolve(),
                        "INFRA_TRACKER_ORIGINAL_INTERPRETER_COMMAND_REQUIRED")
        pipe = next(row for row in inherited if row["fd"] == int(match[1]))
        custody.require(stat.S_ISFIFO(pipe["mode"]) and set(int(value) for value in passfds)
                        <= {int(match[1]), sys.stderr.fileno()}, "INFRA_TRACKER_UNDECLARED_INHERITED_DESCRIPTOR")
        self.birth = {"pid": pid, "read_pipe_fd": int(match[1]), "pipe": pipe,
            "interpreter": str(Path(sys.executable).resolve()), "arguments": arguments,
            "spawn_passfds": sorted(int(value) for value in passfds), "inherited_descriptors": inherited,
            "own_pid": self.pid, "own_native_tid": self.tid, "code_hashes": self.codes,
            "stdlib_code": self.stdlib_files, "observed_actual_original_spawn_return_pid": True}
        # The original function returns after fork_exec; bootstrap may still
        # be transitioning. Identity and exact exec are revalidated on use.
        self.birth["kernel_birth_identity"] = process_identity(pid)

    def verify_for_fixture(self, namespace, measured):
        from scripts import rc6_authenticated_fixture_lifecycle as lifecycle
        self._context()
        lifecycle.authenticate(namespace)
        custody.require(namespace.binding == self.binding, "INFRA_LEASE_CANDIDATE_OR_OWNER_BINDING_CHANGED")
        custody.require(self.installed and not self.errors and self.birth is not None
                        and self.resource_tracker._resource_tracker is self.tracker
                        and self.tracker.ensure_running is self.ensure_wrapper and self.tracker._send is self.send_wrapper
                        and self.util.spawnv_passfds is self.spawn_wrapper
                        and self.tracker._pid == self.birth["pid"] and type(self.tracker._fd) is int,
                        "INFRA_POSITIVE_TRACKER_BIRTH_AND_CURRENT_IDENTITY_REQUIRED")
        custody.require(self.codes == {"ensure_running": _code_hash(self.resource_tracker.ResourceTracker.ensure_running),
            "send": _code_hash(self.resource_tracker.ResourceTracker._send),
            "spawnv_passfds": _code_hash(self.spawn_original), "main": _code_hash(self.resource_tracker.main)},
            "INFRA_ORIGINAL_STDLIB_FUNCTION_CHANGED")
        custody.require(not self.registrations, "INFRA_SCOPED_RESOURCE_REGISTRATIONS_NOT_CLOSED")
        for member in self.stdlib_files.values():
            path = Path(member["path"])
            before = path.stat()
            raw = path.read_bytes()
            custody.require(custody.identity(before) == member["identity"]
                            == custody.identity(path.stat()) and len(raw) == member["bytes"]
                            and hashlib.sha256(raw).hexdigest() == member["sha256"],
                            "INFRA_STDLIB_BIRTH_FILE_BYTES_OR_IDENTITY_CHANGED")
        roots = {(namespace.identity[0], namespace.identity[1])}
        roots.update(tuple(row["identity"][:2]) for row in measured["rows"].values())
        for row in self.birth["inherited_descriptors"]:
            custody.require((row["device"], row["inode"]) not in roots
                            and row["target"] != str(namespace.path)
                            and not row["target"].startswith(str(namespace.path) + "/"),
                            "INFRA_TRACKER_HAS_FIXTURE_NAMESPACE_HANDLE")
        namespace_process_references(self.pid, namespace, measured)
        namespace_process_references(self.birth["pid"], namespace, measured)
        children = own_kernel_children()
        custody.require(children == [self.birth["pid"]], "INFRA_UNKNOWN_OR_WORKLOAD_KERNEL_CHILD_PRESENT")
        # WNOWAIT never reaps. None means no exited/ready child, not ECHILD.
        pending = os.waitid(os.P_ALL, 0, os.WNOHANG | os.WNOWAIT | os.WEXITED)
        custody.require(pending is None, "INFRA_EXITED_OR_ZOMBIE_CHILD_NOT_JOINED")
        pipe = _fd_info(self.pid, self.tracker._fd)
        custody.require((pipe["device"], pipe["inode"]) == (self.birth["pipe"]["device"], self.birth["pipe"]["inode"])
                        and stat.S_ISFIFO(pipe["mode"]), "INFRA_TRACKER_PARENT_PIPE_CHANGED")
        pid = self.birth["pid"]
        entered = time.monotonic()
        # One actual original PROBE, then read-only kernel acknowledgement of
        # pipe drain + blocking read on the SAME pipe. No sleep or retry tests.
        custody.require(self.tracker._check_alive(), "INFRA_TRACKER_ORIGINAL_LIVE_PROBE_FAILED")
        while True:
            identity = process_identity(pid)
            stable = {k: value for k, value in identity.items() if k != "state"}
            born = {k: value for k, value in self.birth["kernel_birth_identity"].items() if k != "state"}
            custody.require(stable == born, "INFRA_TRACKER_PID_START_OR_OWNER_CHANGED")
            unread = array.array("i", [0])
            fcntl.ioctl(self.tracker._fd, termios.FIONREAD, unread, True)
            syscall = _read_proc(pid, "syscall").decode().split()
            if (unread[0] == 0 and identity["state"] == "S" and len(syscall) >= 2 and syscall[0] == "0"
                    and int(syscall[1], 0) == self.birth["read_pipe_fd"]):
                break
            custody.require(time.monotonic() - entered <= 0.5, "INFRA_TRACKER_PIPE_DRAIN_AND_IDLE_READ_NOT_PROVEN")
        command = _read_proc(pid, "cmdline").split(b"\0")
        actual = [os.fsdecode(value) for value in command if value]
        custody.require(actual == self.birth["arguments"]
                        and Path(os.readlink(Path("/proc") / str(pid) / "exe")).resolve()
                        == Path(self.birth["interpreter"]), "INFRA_TRACKER_EXEC_COMMAND_CHANGED")
        child_fds = sorted(os.listdir(Path("/proc") / str(pid) / "fd"))
        observations = []
        for name in child_fds:
            custody.require(name.isdecimal(), "INFRA_TRACKER_DESCRIPTOR_UNKNOWN")
            row = _fd_info(pid, int(name))
            custody.require((row["device"], row["inode"]) not in roots
                            and row["target"] != str(namespace.path)
                            and not row["target"].startswith(str(namespace.path) + "/"),
                            "INFRA_TRACKER_HAS_FIXTURE_NAMESPACE_HANDLE")
            custody.require(int(name) in set(self.birth["spawn_passfds"]) | {0, 1, 2},
                            "INFRA_TRACKER_UNDECLARED_LIVE_DESCRIPTOR")
            observations.append({k: value for k, value in row.items() if k != "target"})
        custody.require(child_fds == sorted(os.listdir(Path("/proc") / str(pid) / "fd"))
                        and own_kernel_children() == children, "INFRA_KERNEL_OR_DESCRIPTOR_CENSUS_CHANGED")
        self.positive_observations += 1
        observation = {"schema": "porota.rc6.scoped-tracker-consumer-lease.v1", "binding": self.binding,
            "namespace_nonce": namespace.nonce, "kernel_birth": self.birth,
            "kernel_own_children": children, "kernel_census_verified": True,
            "tracker_protocol_pipe_drained": True, "tracker_blocked_in_original_pipe_read": True,
            "scoped_resource_registrations_outstanding": 0, "tracker_namespace_handles": 0,
            "tracker_descriptor_observations": observations, "whole_phase_kernel_ECHILD_claimed": False,
            "workload_child_or_source_consumer_admitted": False, "real_orders_sent": 0}
        lease = _ScopedTrackerLease(_AUTHORITY, namespace.nonce, self, observation)
        _ISSUED.setdefault(namespace.nonce, []).append(lease)
        return lease

    def summary(self):
        return {"schema": "porota.rc6.session-infrastructure-lease-summary.v1", "binding": self.binding,
            "positive_fixture_lease_observations": self.positive_observations, "protocol_events_observed": self.protocol_events,
            "outstanding_scoped_resource_registrations": len(self.registrations), "blockers": self.errors,
            "positively_observed_tracker_birth": self.birth, "existing_or_inherited_tracker": self.initial,
            "original_phase_or_big_ECHILD_guard_changed": False, "global_finalizer_or_stop_called": False}


def require_scoped_lease(namespace, lease, measured):
    custody.require(type(lease) is _ScopedTrackerLease and lease.authority is _AUTHORITY
                    and any(lease is original for original in _ISSUED.get(namespace.nonce, []))
                    and lease.namespace_nonce == namespace.nonce,
                    "INFRA_ACTUAL_POSITIVE_OPAQUE_LEASE_REQUIRED")
    return lease.monitor.verify_for_fixture(namespace, measured)
