"""PRIVATE_PID_BOUNDARY_V2 observations; serialized evidence is never authority.

The external ROOT guardian pins live kernel identities before and after the
sealed NONROOT observer.  A missing host PID is only one observation in that
proof.  Legacy EACCES assertions remain the default until separately accepted.
This module never mounts a filesystem, allocates backing, qualifies G0 or
authorizes a financial workload.
"""
from __future__ import annotations

import copy
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import stat
import time

REVISION = "PRIVATE_PID_BOUNDARY_V2"
SCHEMA = "porota.rc6.private-pid-boundary-observation.v2"
GUARD_SCHEMA = "porota.rc6.private-pid-external-guardian.v2"
MEMBER = "scripts/rc6_private_pid_boundary.py"
BOUND = 256 * 1024
MAGICLINKS = ("root", "cwd", "fd", "map_files", "ns")
NEGATIVES = ("arbitrary_absent_pid", "recycled_birth", "pid_collision", "wrong_namespace",
             "host_procfs", "magiclink", "wrong_bridge", "inherited_privileged_fd",
             "forged_receipt", "lost_guardian")
NATIVE_NEGATIVES = ("wrong_namespace", "host_procfs", "magiclink", "wrong_bridge",
                    "inherited_privileged_fd", "lost_guardian")
NATIVE_CONTROL_ERRORS = {
    "wrong_namespace": "PIDV2_WRONG_NAMESPACE", "host_procfs": "PIDV2_HOST_PROCFS_VIEW",
    "magiclink": "PIDV2_INHERITED_PRIVILEGED_FD", "wrong_bridge": "PIDV2_WRONG_OPAQUE_BRIDGE",
    "inherited_privileged_fd": "PIDV2_INHERITED_PRIVILEGED_FD",
    "lost_guardian": "PIDV2_GUARDIAN_LOST_OR_PROCESS_EXITED",
}
_AUTHORITY = object()
_WITNESSES = {}


def require(value, signature):
    if not value:
        raise ValueError(signature)


def wire(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def contract():
    """One proposed revision, including the exact replaced assertion."""
    return {"schema": "porota.rc6.private-pid-boundary-contract.v2", "revision": REVISION,
        "legacy_default": "PROC_HOST_ISSUER_ROOT_EACCES_V1",
        "replaced_assertion": "issuer_root_escape_probe:/proc/HOST_PID/root:EACCES",
        "preserved_assertion": "quota_probe:/proc/LOCAL_SUPERVISOR/fd/1:EACCES",
        "required_properties": ["LIVE_EXTERNAL_PIDFD_BIRTH_BOOT_CGROUP_NAMESPACE",
            "ONE_PRIVATE_READONLY_PROCFS", "LOCAL_HOST_NUMERIC_PID_COLLISION_VETO",
            "NO_HOST_PROCFS_OR_MAGICLINK_BRIDGE", "AUTHENTIC_OWN_OPAQUE_DIRFD_CLOSED_BEFORE_NONROOT",
            "ZERO_CAPS_NNP_SECCOMP_NONROOT", "EXISTING_FOREIGN_TARGET_NATIVE_DENIALS",
            "BEFORE_AFTER_NONCE_ACK", "ORIGINAL_ROOT_WAIT4_TERM2_FIN5_ECHILD"],
        "negative_cases": list(NEGATIVES), "native_negative_controls": list(NATIVE_NEGATIVES),
        "phases": ["TrustedQuotaSetup", "Seal", "NONROOT", "CustodyFIN"],
        "capability": {"backing_bytes": 5 * 1024**3, "project_bytes": 512 * 1024**2},
        "bootstrap": {"backing_bytes": 26 * 1024**3, "project_bytes": 20 * 1024**3},
        "residual_reserve_bytes": 4 * 1024**3, "free_inodes_percent_minimum": 10,
        "capability_before_bootstrap": True, "owner_acceptance_required": True,
        "ENOENT_alone_accepted": False, "observation_grants_G0": False,
        "real_orders_sent": 0, "real_routes": "NOT_CALLED", "recurring_additional_cost_usd": 0}


def contract_sha256():
    return digest(wire(contract()))


def _read(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        chunks, total = [], 0
        while True:
            chunk = os.read(descriptor, min(65536, BOUND + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            require(total <= BOUND, "PIDV2_KERNEL_METADATA_BOUND")
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="strict")
    finally:
        os.close(descriptor)


def local_identity():
    from scripts import rc6_privileged_custody as custody
    status = dict(row.split(":", 1) for row in _read("/proc/self/status").splitlines() if ":" in row)
    # Before procfs replacement its Pid is the host PID. Always authenticate
    # SELF, never /proc/1 merely because getpid() is already private PID1.
    result = custody.kernel_process(int(status["Pid"]))
    result.update(pid=os.getpid(), mount_namespace_inode=os.stat("/proc/self/ns/mnt").st_ino,
                  no_new_privileges=int(status["NoNewPrivs"]), seccomp_mode=int(status["Seccomp"]),
                  seccomp_filters=int(status.get("Seccomp_filters", "0")),
                  supplementary_groups=[int(x) for x in status["Groups"].split()])
    return result


def _unescape(path):
    require(not re.search(r"\\(?![0-7]{3})", path), "PIDV2_MOUNT_ESCAPE_UNKNOWN")
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), path)


def mount_view():
    raw = _read("/proc/self/mountinfo")
    rows = []
    for line in raw.splitlines():
        fields = line.split()
        require(len(fields) >= 10 and fields.count("-") == 1 and fields[0].isdigit()
                and fields[1].isdigit() and re.fullmatch(r"[0-9]+:[0-9]+", fields[2]),
                "PIDV2_MOUNTINFO_INVALID")
        split = fields.index("-")
        require(split >= 6 and len(fields) == split + 4, "PIDV2_MOUNTINFO_INVALID")
        rows.append({"id": int(fields[0]), "parent_id": int(fields[1]), "device": fields[2],
            "root": _unescape(fields[3]), "target": _unescape(fields[4]),
            "options": fields[5].split(","), "propagation": fields[6:split],
            "type": fields[split + 1], "source": _unescape(fields[split + 2])})
    require(0 < len(rows) <= 1024 and len({row["id"] for row in rows}) == len(rows),
            "PIDV2_MOUNT_COUNT_OR_IDENTITY_INVALID")
    return {"rows": rows, "raw_sha256": digest(raw.encode()),
            "proc_device": os.stat("/proc", follow_symlinks=False).st_dev,
            "proc_self": os.readlink("/proc/self")}


def descriptors():
    rows = []
    for name in os.listdir("/proc/self/fd"):
        require(name.isdecimal(), "PIDV2_DESCRIPTOR_UNKNOWN")
        number = int(name)
        try:
            details = os.fstat(number)
        except OSError as error:
            require(error.errno == errno.EBADF, "PIDV2_DESCRIPTOR_UNKNOWN")
            continue  # closed listdir descriptor, never an accepted open FD
        rows.append({"descriptor": number, "device": details.st_dev, "inode": details.st_ino,
                     "mode": details.st_mode, "rdevice": details.st_rdev,
                     "flags": fcntl.fcntl(number, fcntl.F_GETFL)})
    return sorted(rows, key=lambda row: row["descriptor"])


def validate_descriptors(rows):
    require(type(rows) is list and {row.get("descriptor") for row in rows} == {0, 1, 2}
            and len(rows) == 3, "PIDV2_INHERITED_PRIVILEGED_FD")
    for row in rows:
        require(all(type(row.get(key)) is int for key in (
            "descriptor", "device", "inode", "mode", "rdevice", "flags")), "PIDV2_DESCRIPTOR_UNKNOWN")
        require(stat.S_ISREG(row["mode"]) or stat.S_ISFIFO(row["mode"])
                or stat.S_ISCHR(row["mode"]) and row["rdevice"] == os.makedev(1, 3),
                "PIDV2_STDIO_FOREIGN_HANDLE")
        require(row["descriptor"] != 0 or stat.S_ISCHR(row["mode"])
                and row["rdevice"] == os.makedev(1, 3), "PIDV2_STDIN_MUST_BE_NULL")
    return rows


def validate_bridge_descriptor(descriptor, claim):
    from scripts import rc6_capacity_calibration as calibration
    require(type(descriptor) is int and descriptor >= 0 and type(claim) is dict,
            "PIDV2_BRIDGE_BINDING_REQUIRED")
    details = os.fstat(descriptor)
    require(fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_PATH == os.O_PATH
            and stat.S_ISDIR(details.st_mode)
            and [details.st_dev, details.st_ino, details.st_uid, details.st_gid,
                 stat.S_IMODE(details.st_mode)] == claim.get("identity")
            and calibration.descriptor_mount_id(descriptor) == claim.get("mount_id"),
            "PIDV2_WRONG_OPAQUE_BRIDGE")
    marker = os.open(".porota-generated-fixture-owner.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                     dir_fd=descriptor)
    try:
        before = os.fstat(marker)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= BOUND,
                "PIDV2_BRIDGE_MARKER_INVALID")
        raw = os.read(marker, BOUND + 1)
        require(len(raw) == before.st_size and digest(raw) == claim.get("marker_sha256")
                and {key: value for key, value in calibration.identity(before).items() if key != "st_atime_ns"}
                == {key: value for key, value in calibration.identity(os.fstat(marker)).items() if key != "st_atime_ns"},
                "PIDV2_BRIDGE_MARKER_REBOUND")
        value = calibration.decode(raw)
        require(value.get("binding") == claim.get("binding") and value.get("identity") == claim.get("identity")
                and value.get("namespace_nonce") == claim.get("namespace_nonce")
                and value.get("mount_id") == claim.get("mount_id") and value.get("runtime_paths_authorized") is False,
                "PIDV2_BRIDGE_MARKER_REBOUND")
    finally:
        os.close(marker)
    return {"identity": claim["identity"], "mount_id": claim["mount_id"],
            "marker_sha256": claim["marker_sha256"], "namespace_nonce": claim["namespace_nonce"],
            "O_PATH": True, "ordinary_components_NOFOLLOW": True}


def validate_private_view(actor, mounts, host):
    require(type(actor) is dict and type(host) is dict
            and type(actor.get("pid")) is int and actor["pid"] > 0
            and type(actor.get("pid_namespace_inode")) is int
            and actor["pid_namespace_inode"] > 0
            and type(actor.get("mount_namespace_inode")) is int and actor["mount_namespace_inode"] > 0
            and actor["pid_namespace_inode"] != host.get("pid_namespace_inode")
            and actor.get("mount_namespace_inode") != host.get("mount_namespace_inode"),
            "PIDV2_WRONG_NAMESPACE")
    require(actor.get("namespace_pids") == [actor["pid"]]
            and mounts.get("proc_self") == str(actor["pid"])
            and type(mounts.get("proc_device")) is int and mounts["proc_device"] != host.get("proc_device"),
            "PIDV2_HOST_PROCFS_VIEW")
    rows = mounts.get("rows")
    require(type(rows) is list and 0 < len(rows) <= 1024, "PIDV2_MOUNTINFO_INVALID")
    procs = [row for row in rows if row.get("type") == "proc"]
    require(len(procs) == 1 and procs[0].get("root") == "/" and procs[0].get("target") == "/proc"
            and "ro" in procs[0].get("options", []) and "rw" not in procs[0].get("options", []),
            "PIDV2_HOST_PROCFS_MOUNT_OR_ALIAS")
    for row in rows:
        require(not any(token.startswith(("shared:", "master:", "propagate_from:"))
                        for token in row.get("propagation", [])), "PIDV2_SHARED_PROPAGATION")
        require(not any(re.search(r"/proc/(?:[0-9]+|self|thread-self)/(?:root|cwd|fd|map_files|ns)(?:/|$)",
                                  row.get(key, "")) for key in ("target", "source", "root")),
                "PIDV2_MAGICLINK_MOUNT")
    return True


def assert_pidfd_live(descriptor):
    require(type(descriptor) is int and descriptor >= 0, "PIDV2_GUARDIAN_PIDFD_REQUIRED")
    poller = select.poll()
    poller.register(descriptor, select.POLLIN | select.POLLHUP | select.POLLERR)
    require(not poller.poll(0), "PIDV2_GUARDIAN_LOST_OR_PROCESS_EXITED")


def _expect_rejection(name, function, *args):
    try:
        function(*args)
    except ValueError as error:
        return {"case": name, "status": "REJECTED", "error": str(error),
                "scope": "ACTUAL_KERNEL_CONTROL", "qualification_claimed": False}
    raise ValueError("PIDV2_NEGATIVE_ACCEPTED:" + name)


def pre_namespace_controls(request, *, inherited_host_proc=False):
    """Read actual unsafe views before replacing procfs; no mount mutations."""
    host = {"pid_namespace_inode": request["issuer"]["pid_namespace_inode"],
            "mount_namespace_inode": request["host_boundary"]["mount_namespace_inode"],
            "proc_device": request["boundary_host_proc_device"]}
    actor, view = local_identity(), mount_view()
    name = "host_procfs" if inherited_host_proc else "wrong_namespace"
    row = _expect_rejection(name, validate_private_view, actor, view, host)
    require(row["error"] == ("PIDV2_HOST_PROCFS_VIEW" if inherited_host_proc else "PIDV2_WRONG_NAMESPACE"),
            "PIDV2_NATIVE_NEGATIVE_WRONG_DISCRIMINANT")
    row.update(kernel_identity=actor, mountinfo_sha256=view["raw_sha256"], proc_device=view["proc_device"])
    return row


class GuardianLedger:
    """Lives only in the trusted external guardian; challenge files are untrusted."""
    def __init__(self, request, namespace, guardian):
        from scripts import rc6_privileged_custody as custody
        self.request, self.namespace, self.guardian = request, Path(namespace), guardian
        self.subject = custody.public_kernel_process(request["issuer"]["pid"])
        require(self.subject["start_ticks"] == request["issuer"]["start_ticks"]
                and self.subject["boot_id"] == request["issuer"]["boot_id"], "PIDV2_RECYCLED_HOST_SUBJECT")
        self.subject_fd = os.pidfd_open(self.subject["pid"], 0)
        self.actor_fd = None
        self.rows = []
        require(custody.public_kernel_process(self.subject["pid"]) == self.subject, "PIDV2_SUBJECT_PIDFD_RACE")
        self.native_controls = [pre_namespace_controls(request)]

    def observe(self, mapping):
        from scripts import rc6_privileged_custody as custody
        from scripts import rc6_capacity_calibration as calibration
        assert_pidfd_live(self.subject_fd)
        subject = custody.public_kernel_process(self.subject["pid"])
        require(subject == self.subject, "PIDV2_RECYCLED_HOST_SUBJECT")
        stage = "before" if not self.rows else "after"
        if len(self.rows) == 2 or mapping is None:
            return
        path = self.namespace / ("boundary-" + stage + ".challenge.json")
        if not path.exists():
            return
        raw = calibration.read(path, BOUND)
        challenge = calibration.decode(raw)
        require(challenge.get("schema") == "porota.rc6.private-pid-challenge.v2"
                and challenge.get("stage") == stage and challenge.get("binding") == self.request["binding"]
                and challenge.get("request_sha256") == digest(wire(self.request))
                and re.fullmatch("[0-9a-f]{64}", challenge.get("nonce", "")), "PIDV2_CHALLENGE_FORGED")
        actor = challenge["actor"]
        parent = mapping["host_private_PID1"]["pid"]
        children = [int(x) for x in _read(f"/proc/{parent}/task/{parent}/children").split()]
        # NONROOT dumpable=0 forbids following its namespace links even for a
        # guardian without CAP_SYS_PTRACE. Public NSpid, direct parent, birth,
        # cgroup and the pinned private PID1 provide the external mapping.
        actuals = [custody.public_kernel_process(pid) for pid in children]
        candidates = [value for value in actuals if value["namespace_pids"] == [value["pid"], actor["pid"]]
                      and value["start_ticks"] == actor["start_ticks"]]
        require(len(candidates) == 1, "PIDV2_ACTOR_HOST_BIRTH_MAPPING_REBOUND")
        actual = candidates[0]
        actual["pid_namespace_inode"] = mapping["host_private_PID1"]["pid_namespace_inode"]
        require(actual["uids"] == actor["uids"] == [self.request["owner_uid"]] * 4
                and actual["gids"] == actor["gids"] == [self.request["owner_gid"]] * 4
                and actual["cgroup"] == actor["cgroup"] == self.guardian["cgroup"]
                and actual["pid_namespace_inode"] == actor["pid_namespace_inode"]
                and custody.kernel_process(self.guardian["pid"]) == self.guardian,
                "PIDV2_GUARDIAN_OR_ACTOR_KERNEL_REBOUND")
        if self.actor_fd is None:
            self.actor_fd = os.pidfd_open(actual["pid"], 0)
        assert_pidfd_live(self.actor_fd)
        require(custody.public_kernel_process(actual["pid"]) == custody.public_process_identity(actual),
                "PIDV2_ACTOR_PIDFD_RACE")
        row = {"schema": GUARD_SCHEMA, "stage": stage, "challenge_sha256": digest(raw),
            "nonce": challenge["nonce"], "binding": self.request["binding"],
            "request_sha256": digest(wire(self.request)), "issuer_actual": subject,
            "guardian_actual": self.guardian, "host_actor_actual": actual,
            "subject_pidfd_alive": True, "actor_pidfd_alive": True,
            "host_proc_device": self.request["boundary_host_proc_device"],
            "monotonic_ns": time.monotonic_ns()}
        custody._write_control(self.namespace / ("boundary-" + stage + ".guardian.json"), row,
                               self.request["owner_uid"], self.request["owner_gid"])
        self.rows.append(row)  # Trusted RAM, never reconstructed from worker files.

    def close(self):
        if self.actor_fd is not None:
            os.close(self.actor_fd)
            self.actor_fd = None
        if self.subject_fd is not None:
            os.close(self.subject_fd)
            self.subject_fd = None


def _challenge(request, stage, actor):
    from scripts import rc6_capacity_calibration as calibration
    root = Path(request["namespace_receipt"]["path"])
    value = {"schema": "porota.rc6.private-pid-challenge.v2", "stage": stage,
        "nonce": os.urandom(32).hex(), "binding": request["binding"],
        "request_sha256": digest(wire(request)), "actor": actor}
    calibration.publish(root / ("boundary-" + stage + ".challenge.json"), value)
    acknowledgement = root / ("boundary-" + stage + ".guardian.json")
    deadline = time.monotonic() + 7
    while not acknowledgement.exists():
        require(time.monotonic() < deadline, "PIDV2_LIVE_GUARDIAN_ACK_ABSENT")
        time.sleep(0.025)
    result = calibration.decode(calibration.read(acknowledgement, BOUND))
    require(result.get("challenge_sha256") == digest(wire(value)) and result.get("nonce") == value["nonce"],
            "PIDV2_GUARDIAN_ACK_FORGED")
    return result


def _open_denial(label, path, flags, accepted):
    try:
        descriptor = os.open(path, flags | os.O_CLOEXEC)
    except OSError as error:
        require(error.errno in accepted, "PIDV2_NATIVE_DENIAL_UNKNOWN:" + label)
        return {"operation": "open", "path": str(path), "flags": flags,
                "errno": error.errno, "target_existed": Path(path).exists(), "write_performed": False}
    os.close(descriptor)
    raise ValueError("PIDV2_NATIVE_ESCAPE:" + label)


def observe_nonroot(request):
    """Real adversarial observations in the sealed process, before any backend."""
    from scripts import rc6_capacity_calibration as calibration
    actor = local_identity()
    uid, gid = request["owner_uid"], request["owner_gid"]
    require(actor["uids"] == [uid] * 4 and actor["gids"] == [gid] * 4 and uid > 0
            and actor["supplementary_groups"] == [] and not any(actor["capabilities"].values())
            and actor["no_new_privileges"] == 1 and actor["seccomp_mode"] == 2
            and actor["seccomp_filters"] > 0, "PIDV2_NONROOT_SEAL_REQUIRED")
    host = {"pid_namespace_inode": request["issuer"]["pid_namespace_inode"],
            "mount_namespace_inode": request["host_boundary"]["mount_namespace_inode"],
            "proc_device": request["boundary_host_proc_device"]}
    view, fds = mount_view(), descriptors()
    validate_private_view(actor, view, host)
    validate_descriptors(fds)
    before = _challenge(request, "before", actor)
    foreign = request["issuer"]["pid"]
    visible = Path(f"/proc/{foreign}").exists()
    require(not visible, "PIDV2_HOST_LOCAL_PID_COLLISION")
    hidden = {"host_pid": foreign, "local_pid_visible": visible, "magiclinks": {}}
    for name in MAGICLINKS:
        hidden["magiclinks"][name] = _open_denial(name, f"/proc/{foreign}/{name}", os.O_RDONLY, (errno.ENOENT,))
    try:
        os.kill(foreign, 0)
    except OSError as error:
        require(error.errno == errno.ESRCH, "PIDV2_HOST_SIGNAL_NOT_NAMESPACE_DENIED")
        hidden["signal_zero_errno"] = error.errno
    else:
        raise ValueError("PIDV2_HOST_SIGNAL_ADDRESSABLE")
    # Existing visible ROOT PID1 and existing kernel/cgroup targets are tested
    # independently of all hidden-PID ENOENT observations. No data is written.
    denials = {}
    for label, target in (("foreign_kernel", "/proc/sys/kernel/pid_max"),
                          ("foreign_cgroup", "/sys/fs/cgroup/cgroup.procs")):
        require(Path(target).is_file(), "PIDV2_EXISTING_NATIVE_TARGET_REQUIRED:" + label)
        denials[label] = _open_denial(label, target, os.O_WRONLY, (errno.EROFS, errno.EACCES, errno.EPERM))
    for name, target in (("root", "/proc/1/root"), ("cwd", "/proc/1/cwd"), ("fd", "/proc/1/fd/1"),
                         ("ns", "/proc/1/ns/pid")):
        denials["local_root_" + name] = _open_denial(name, target, os.O_RDONLY, (errno.EACCES, errno.EPERM))
    try:
        os.kill(1, 0)
    except OSError as error:
        require(error.errno == errno.EPERM, "PIDV2_VISIBLE_ROOT_SIGNAL_NOT_DENIED")
        denials["local_root_signal"] = {"errno": error.errno, "signal": 0, "write_performed": False}
    else:
        raise ValueError("PIDV2_VISIBLE_ROOT_SIGNAL_ALLOWED")
    controls = []
    for case, path in (("magiclink", "/proc/self/root"), ("wrong_bridge", "/"),
                       ("inherited_privileged_fd", "/")):
        fd = os.open(path, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            controls.append(_expect_rejection(case, validate_bridge_descriptor, fd, request["namespace_receipt"])
                            if case == "wrong_bridge" else
                            _expect_rejection(case, validate_descriptors, descriptors()))
        finally:
            os.close(fd)
    reader, writer = os.pipe2(os.O_CLOEXEC)
    child = os.fork()
    if child == 0:
        os.close(writer)
        os.read(reader, 1)
        os._exit(0)
    os.close(reader)
    fd = os.pidfd_open(child, 0)
    try:
        os.write(writer, b"x")
        os.close(writer)
        writer = None
        waited, status = os.waitpid(child, 0)
        require(waited == child and status == 0, "PIDV2_NEGATIVE_CHILD_FIN_REQUIRED")
        controls.append(_expect_rejection("lost_guardian", assert_pidfd_live, fd))
        controls[-1].update(actual_waitpid=True, actual_exited_pidfd=True,
                            tested_primitive="FAIL_CLOSED_ON_LOST_PINNED_GUARDIAN")
    finally:
        os.close(fd)
        if writer is not None:
            os.close(writer)
    after = _challenge(request, "after", actor)
    result = {"schema": SCHEMA, "revision": REVISION, "contract_sha256": contract_sha256(),
        "scope": "SMALL_NATIVE_OBSERVATION_ONLY", "binding": request["binding"],
        "request_sha256": digest(wire(request)), "actor": actor, "host_boundary": host,
        "mount_view": view, "descriptors": fds, "external_before": before, "external_after": after,
        "hidden_subject": hidden, "existing_denials": denials, "native_controls": controls,
        "qualification_claimed": False, "G0_GREEN": False, "real_orders_sent": 0}
    return result


def quota_actor_observation(context):
    """Recheck the new quota worker's actual view, using its ROOT live mapping.

    This runs only after authenticated launch, trusted setup, ancestor sealing
    and irreversible NONROOT drop. The external guardian continues checking
    the issuer and retains kill/reap custody; this projection grants no launch.
    """
    request, proof = context["request"], context["proof"]
    require(request.get("boundary_revision") == REVISION
            and request.get("require_quota_ancestor_seal") is True
            and proof.get("calibration_request_sha256") == request.get("original_request_sha256"),
            "PIDV2_QUOTA_LIVE_CONTEXT_REQUIRED")
    mapping = proof["guardian_native_mapping"]
    subject = mapping["issuer_actual"]
    require(subject["pid"] == request["issuer"]["pid"] and subject["start_ticks"] == request["issuer"]["start_ticks"]
            and subject["boot_id"] == request["issuer"]["boot_id"]
            and subject["namespace_pids"] == [subject["pid"]]
            and mapping.get("binding") == request["binding"]
            and mapping.get("source_sha") == request["source_sha"]
            and mapping.get("source_tree") == request["source_tree"], "PIDV2_QUOTA_SUBJECT_REBOUND")
    actor, view = local_identity(), mount_view()
    host = {"pid_namespace_inode": request["issuer"]["pid_namespace_inode"],
            "mount_namespace_inode": request["mount_namespace_inode"],
            "proc_device": request["boundary_host_proc_device"]}
    validate_private_view(actor, view, host)
    validate_descriptors(descriptors())
    require(actor["uids"] == [request["owner_uid"]] * 4 and actor["gids"] == [request["owner_gid"]] * 4
            and not any(actor["capabilities"].values()) and actor["supplementary_groups"] == []
            and actor["no_new_privileges"] == 1 and actor["seccomp_mode"] == 2,
            "PIDV2_NONROOT_SEAL_REQUIRED")
    foreign = subject["pid"]
    require(not Path(f"/proc/{foreign}").exists(), "PIDV2_HOST_LOCAL_PID_COLLISION")
    denials = {name: _open_denial(name, f"/proc/{foreign}/{name}", os.O_RDONLY, (errno.ENOENT,))
               for name in MAGICLINKS}
    try:
        os.kill(foreign, 0)
    except OSError as error:
        require(error.errno == errno.ESRCH, "PIDV2_HOST_SIGNAL_NOT_NAMESPACE_DENIED")
        signal_errno = error.errno
    else:
        raise ValueError("PIDV2_HOST_SIGNAL_ADDRESSABLE")
    existing = _open_denial("local_root_fd", "/proc/1/fd/1", os.O_WRONLY, (errno.EACCES, errno.EPERM))
    return {"schema": "porota.rc6.quota-private-pid-observation.v2", "revision": REVISION,
            "contract_sha256": contract_sha256(), "actor": actor, "mount_view": view,
            "external_issuer": subject, "hidden_magiclinks": denials, "host_signal_errno": signal_errno,
            "descriptors": descriptors(),
            "existing_ROOT_descriptor_denial": existing, "ENOENT_alone_accepted": False,
            "launch_authority_granted": False, "qualification_claimed": False}


def validate_observation(value, *, request, guardian_rows, native_pre_controls, bridge):
    """Captured verification only. Caller must independently prove native FIN."""
    require(type(value) is dict and value.get("schema") == SCHEMA and value.get("revision") == REVISION
            and value.get("contract_sha256") == contract_sha256()
            and value.get("scope") == "SMALL_NATIVE_OBSERVATION_ONLY"
            and value.get("binding") == request["binding"] and value.get("request_sha256") == digest(wire(request))
            and value.get("qualification_claimed") is False and value.get("G0_GREEN") is False
            and type(value.get("real_orders_sent")) is int and value["real_orders_sent"] == 0,
            "PIDV2_RECEIPT_FORGED_OR_REBOUND")
    actor, host = value["actor"], value["host_boundary"]
    require(host == {"pid_namespace_inode": request["issuer"]["pid_namespace_inode"],
                    "mount_namespace_inode": request["host_boundary"]["mount_namespace_inode"],
                    "proc_device": request["boundary_host_proc_device"]}, "PIDV2_HOST_BOUNDARY_REBOUND")
    validate_private_view(actor, value["mount_view"], host)
    validate_descriptors(value["descriptors"])
    caps = actor.get("capabilities")
    require(type(caps) is dict and set(caps) == {"CapEff", "CapPrm", "CapBnd", "CapInh", "CapAmb"}
            and all(type(number) is int and number == 0 for number in caps.values())
            and actor.get("uids") == [request["owner_uid"]] * 4 and actor.get("gids") == [request["owner_gid"]] * 4
            and actor.get("supplementary_groups") == []
            and actor.get("no_new_privileges") == 1 and actor.get("seccomp_mode") == 2
            and type(actor.get("seccomp_filters")) is int and actor["seccomp_filters"] > 0,
            "PIDV2_NONROOT_SEAL_REQUIRED")
    require(type(guardian_rows) is list and len(guardian_rows) == 2, "PIDV2_LIVE_GUARDIAN_ACK_ABSENT")
    for stage, row in zip(("before", "after"), guardian_rows):
        require(type(row) is dict and row.get("schema") == GUARD_SCHEMA and row.get("stage") == stage
                and row == value.get("external_" + stage) and row.get("binding") == request["binding"]
                and row.get("request_sha256") == digest(wire(request))
                and row.get("subject_pidfd_alive") is True and row.get("actor_pidfd_alive") is True
                and type(row.get("monotonic_ns")) is int and row["monotonic_ns"] > 0
                and re.fullmatch("[0-9a-f]{64}", row.get("nonce", ""))
                and re.fullmatch("[0-9a-f]{64}", row.get("challenge_sha256", "")),
                "PIDV2_GUARDIAN_ACK_FORGED_OR_LOST")
        subject = row["issuer_actual"]
        require(subject.get("pid") == request["issuer"]["pid"]
                and subject.get("start_ticks") == request["issuer"]["start_ticks"]
                and subject.get("boot_id") == request["issuer"]["boot_id"]
                and subject.get("namespace_pids") == [subject["pid"]]
                and subject.get("uids") == [request["owner_uid"]] * 4
                and subject.get("gids") == [request["owner_gid"]] * 4,
                "PIDV2_ABSENT_ARBITRARY_OR_RECYCLED_SUBJECT")
        actual, guardian = row["host_actor_actual"], row["guardian_actual"]
        require(actual.get("namespace_pids") == [actual["pid"], actor["pid"]]
                and actual.get("start_ticks") == actor["start_ticks"]
                and actual.get("pid_namespace_inode") == actor["pid_namespace_inode"]
                and actual.get("uids") == actor["uids"] and actual.get("gids") == actor["gids"]
                and actual.get("cgroup") == actor["cgroup"] == guardian.get("cgroup")
                and guardian.get("pid_namespace_inode") == host["pid_namespace_inode"]
                and guardian.get("uids") == [0] * 4 and guardian.get("parent_pid") == 1
                and row.get("host_proc_device") == host["proc_device"], "PIDV2_ACTOR_MAPPING_REBOUND")
    require(guardian_rows[0]["issuer_actual"] == guardian_rows[1]["issuer_actual"]
            and guardian_rows[0]["guardian_actual"] == guardian_rows[1]["guardian_actual"]
            and guardian_rows[0]["host_actor_actual"] == guardian_rows[1]["host_actor_actual"]
            and guardian_rows[0]["nonce"] != guardian_rows[1]["nonce"]
            and guardian_rows[0]["monotonic_ns"] < guardian_rows[1]["monotonic_ns"], "PIDV2_BIRTH_OR_ACK_REPLAY")
    hidden = value["hidden_subject"]
    require(hidden.get("host_pid") == request["issuer"]["pid"] and hidden.get("local_pid_visible") is False
            and hidden.get("signal_zero_errno") == errno.ESRCH, "PIDV2_HOST_LOCAL_PID_COLLISION")
    require(set(hidden.get("magiclinks", {})) == set(MAGICLINKS)
            and all(row.get("errno") == errno.ENOENT for row in hidden["magiclinks"].values()),
            "PIDV2_HOST_MAGICLINK_ADDRESSABLE")
    denials = value["existing_denials"]
    require(set(denials) == {"foreign_kernel", "foreign_cgroup", "local_root_root", "local_root_cwd",
                            "local_root_fd", "local_root_ns", "local_root_signal"}
            and all(row.get("errno") in (errno.EACCES, errno.EPERM, errno.EROFS)
                    and row.get("write_performed") is False for row in denials.values())
            and all(denials[name].get("target_existed") is True for name in ("foreign_kernel", "foreign_cgroup")),
            "PIDV2_EXISTING_TARGET_NATIVE_DENIAL_REQUIRED")
    expected = request["namespace_receipt"]
    require(type(bridge) is dict and bridge.get("identity") == expected["identity"]
            and bridge.get("original_mount_id") == expected["mount_id"]
            and bridge.get("marker_sha256") == expected["marker_sha256"]
            and bridge.get("foreign_host_proc_exposed") is False, "PIDV2_WRONG_OPAQUE_BRIDGE")
    controls = native_pre_controls + value["native_controls"]
    require({row.get("case") for row in controls} == set(NATIVE_NEGATIVES)
            and len(controls) == len(NATIVE_NEGATIVES)
            and all(row.get("status") == "REJECTED" and row.get("scope") == "ACTUAL_KERNEL_CONTROL"
                    and row.get("qualification_claimed") is False
                    and row.get("error") == NATIVE_CONTROL_ERRORS.get(row.get("case"))
                    for row in controls), "PIDV2_NATIVE_NEGATIVE_MATRIX_INCOMPLETE")
    lost = next(row for row in controls if row["case"] == "lost_guardian")
    require(lost.get("actual_waitpid") is True and lost.get("actual_exited_pidfd") is True,
            "PIDV2_LOST_GUARDIAN_NATIVE_CONTROL_FIN_REQUIRED")
    return value


def adversarial_rejections(value, *, request, guardian_rows, native_pre_controls, bridge):
    """Attack captured authority linkage without inventing native syscall results."""
    results = []
    for case in NEGATIVES:
        altered, spec, rows, opaque = copy.deepcopy(value), copy.deepcopy(request), copy.deepcopy(guardian_rows), copy.deepcopy(bridge)
        if case in ("arbitrary_absent_pid", "recycled_birth"):
            for index, stage in enumerate(("before", "after")):
                subject = rows[index]["issuer_actual"]
                if case == "arbitrary_absent_pid":
                    subject["pid"] += 100000000
                    subject["namespace_pids"] = [subject["pid"]]
                else:
                    subject["start_ticks"] = str(int(subject["start_ticks"]) + 1)
                altered["external_" + stage] = copy.deepcopy(rows[index])
        elif case == "pid_collision":
            altered["hidden_subject"]["local_pid_visible"] = True
        elif case == "wrong_namespace":
            altered["actor"]["pid_namespace_inode"] = spec["issuer"]["pid_namespace_inode"]
        elif case == "host_procfs":
            altered["mount_view"]["proc_device"] = spec["boundary_host_proc_device"]
        elif case == "magiclink":
            altered["mount_view"]["rows"][0]["target"] = "/proc/1/root"
        elif case == "wrong_bridge":
            opaque["identity"][1] += 1
        elif case == "inherited_privileged_fd":
            altered["descriptors"].append({**altered["descriptors"][0], "descriptor": 3})
        elif case == "forged_receipt":
            altered["external_after"]["nonce"] = "0" * 64
        elif case == "lost_guardian":
            rows.pop()
        try:
            validate_observation(altered, request=spec, guardian_rows=rows,
                                 native_pre_controls=native_pre_controls, bridge=opaque)
        except ValueError as error:
            results.append({"case": case, "status": "REJECTED", "error": str(error),
                            "scope": "CAPTURED_EVIDENCE_ATTACK", "native_result_simulated": False})
        else:
            raise ValueError("PIDV2_NEGATIVE_ACCEPTED:" + case)
    return results


class BoundaryWitness:
    def __init__(self, authority, receipt, custody, binding):
        require(authority is _AUTHORITY, "PIDV2_SYNTHETIC_WITNESS_FORBIDDEN")
        self.receipt, self.custody, self.binding = receipt, custody, dict(binding)
        self.pid, self.start = os.getpid(), local_identity()["start_ticks"]
        _WITNESSES[id(self)] = (self, self.pid, self.start, digest(wire(receipt)), dict(binding), custody)


def boundary_witness(custody_witness, *, binding):
    from scripts import rc6_privileged_custody as custody
    accepted = custody.validate_witness(custody_witness, binding=binding)
    evidence = accepted.get("native_root_proof", {}).get("private_pid_boundary_v2")
    require(type(evidence) is dict and evidence.get("status") == "NATIVE_OBSERVATION_PROVED"
            and evidence.get("contract_sha256") == contract_sha256()
            and accepted.get("controller_actual_FIN", {}).get("pidfd_exit_observed") is True
            and accepted.get("cleanup", {}).get("namespace_removed") is True,
            "PIDV2_NATIVE_OBSERVATION_AND_PHYSICAL_FIN_REQUIRED")
    validate_observation(evidence["observation"], request=evidence["request"], guardian_rows=evidence["guardian_rows"],
        native_pre_controls=evidence["native_pre_controls"], bridge=evidence["bridge"])
    expected = adversarial_rejections(evidence["observation"], request=evidence["request"],
        guardian_rows=evidence["guardian_rows"], native_pre_controls=evidence["native_pre_controls"], bridge=evidence["bridge"])
    require(expected == evidence.get("negative_guards"), "PIDV2_NEGATIVE_GUARDS_REBOUND")
    return BoundaryWitness(_AUTHORITY, evidence, custody_witness, binding)


def validate_witness(witness, *, binding, custody_witness):
    from scripts import rc6_privileged_custody as custody
    custody.validate_witness(custody_witness, binding=binding)
    registered = _WITNESSES.get(id(witness))
    require(type(witness) is BoundaryWitness and registered is not None and registered[0] is witness
            and registered[1:3] == (os.getpid(), local_identity()["start_ticks"])
            and registered[3] == digest(wire(witness.receipt)) and registered[4] == witness.binding == binding
            and registered[5] is witness.custody is custody_witness, "PIDV2_ACTUAL_IN_PROCESS_WITNESS_REQUIRED")
    return witness.receipt
