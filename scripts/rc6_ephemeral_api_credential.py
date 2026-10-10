"""Ephemeral, sealed input for an already admitted ROOT controller.

This module grants no ROOT, contract, gate or unit-launch authority. The
NONROOT Actions issuer keeps its own sealed memfd alive through the original
external FIN. Existing PID1 copies it with LoadCredential; an authenticated
ROOT actor may consume that read-only copy once, before its privilege seal.
Neither the issuer receipt nor requests contain credential data or its hash.
"""
from __future__ import annotations

import ctypes
import fcntl
import json
import os
from pathlib import Path
import re
import select
import stat
import sys

ROOT = Path(__file__).absolute().parents[1]
MEMBER = "scripts/rc6_ephemeral_api_credential.py"
NAME = "rc6-readonly-api"
MAXIMUM_BYTES = 65536
WORKER_ROLE = "PRIVATE_WORKER_READONLY_CREDENTIAL"
BROKER_ORACLE_ROLE = "SOURCE_FIXED_ROOT_BROKER_OWNER_ORACLE"
# Linux UAPI values remain valid when Python was compiled with older headers.
# Actual F_ADD_SEALS/F_GET_SEALS and adversarial writes must still succeed/deny;
# an absent Python symbol alone never establishes a missing kernel capability.
F_ADD_SEALS = getattr(fcntl, "F_ADD_SEALS", 1033)
F_GET_SEALS = getattr(fcntl, "F_GET_SEALS", 1034)
F_SEAL_SEAL = getattr(fcntl, "F_SEAL_SEAL", 0x1)
F_SEAL_SHRINK = getattr(fcntl, "F_SEAL_SHRINK", 0x2)
F_SEAL_GROW = getattr(fcntl, "F_SEAL_GROW", 0x4)
F_SEAL_WRITE = getattr(fcntl, "F_SEAL_WRITE", 0x8)
F_SEAL_FUTURE_WRITE = getattr(fcntl, "F_SEAL_FUTURE_WRITE", 0x10)
SEALS = F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL
_AUTHORITY = object()
_ISSUERS = {}
_READS = set()


class CredentialError(ValueError):
    def __init__(self, signature, *, error_number=None):
        super().__init__(signature)
        self.errno = error_number


def require(condition, signature):
    if not condition:
        raise CredentialError(signature)


def _kernel_identity():
    fields = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
    return {"pid": os.getpid(), "start_ticks": int(fields[19]),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "uid": os.getuid(), "gid": os.getgid()}


def _dumpable():
    libc = ctypes.CDLL(None, use_errno=True)
    value = libc.prctl(3, 0, 0, 0, 0)
    require(value >= 0, "CREDENTIAL_ISSUER_DUMPABLE_READBACK_REQUIRED")
    return value


def _exact_source(sha, tree):
    from scripts import rc6_product_resource_compatibility as resource_profile
    resource_profile.exact_source(sha, tree)


def _exact_binding(sha, tree, binding):
    require(type(sha) is str and re.fullmatch(r"[0-9a-f]{40}", sha)
            and type(tree) is str and re.fullmatch(r"[0-9a-f]{40}", tree)
            and type(binding) is dict
            and binding.get("candidate_sha") == sha and binding.get("candidate_tree") == tree,
            "CREDENTIAL_EXACT_SOURCE_BINDING_REQUIRED")
    require(all(type(key) is str and type(value) is str and 0 < len(value) <= 512
                for key, value in binding.items()), "CREDENTIAL_LITERAL_BINDING_REQUIRED")
    require(not any(any(word in key.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"))
                    for key in binding), "CREDENTIAL_SECRET_BINDING_FORBIDDEN")
    # Copy through canonical JSON; callers cannot mutate a registered binding.
    return json.loads(json.dumps(binding, sort_keys=True, allow_nan=False))


def _validate_issuer_authority(sha, tree, binding, owner_check):
    require(os.getuid() == os.geteuid() > 0 and os.getgid() == os.getegid() > 0
            and len(os.listdir("/proc/self/task")) == 1,
            "CREDENTIAL_SINGLE_THREAD_NONROOT_ISSUER_REQUIRED")
    require(_dumpable() == 0, "CREDENTIAL_ISSUER_DUMPABLE_ZERO_REQUIRED")
    require(callable(owner_check), "CREDENTIAL_AUTHENTICATED_OWNER_CHECK_REQUIRED")
    _exact_source(sha, tree)
    _exact_binding(sha, tree, binding)
    owner_check()
    return _kernel_identity()


def _fd_identity(details):
    # PID1 reads can change atime. The sealed file's identity/size/mode cannot.
    return (details.st_dev, details.st_ino, details.st_mode, details.st_nlink,
            details.st_uid, details.st_gid, details.st_size, details.st_mtime_ns, details.st_ctime_ns)


def _create_sealed_memfd(token):
    require(type(token) in (bytes, bytearray) and 0 < len(token) <= MAXIMUM_BYTES,
            "CREDENTIAL_BOUNDED_IN_MEMORY_BYTES_REQUIRED")
    require(sys.platform == "linux" and hasattr(os, "memfd_create") and hasattr(os, "MFD_ALLOW_SEALING"),
            "CREDENTIAL_REAL_MEMFD_AND_SEALS_REQUIRED")
    descriptor = None
    try:
        descriptor = os.memfd_create(NAME, flags=os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(token):
            written = os.write(descriptor, memoryview(token)[offset:])
            require(written > 0, "CREDENTIAL_COMPLETE_MEMFD_WRITE_REQUIRED")
            offset += written
        os.lseek(descriptor, 0, os.SEEK_SET)
        fcntl.fcntl(descriptor, F_ADD_SEALS, SEALS)
        require(fcntl.fcntl(descriptor, F_GET_SEALS) == SEALS,
                "CREDENTIAL_ALL_MEMFD_SEALS_REQUIRED")
        return descriptor
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise CredentialError("CREDENTIAL_MEMFD_CREATION_OR_SEAL_OS_ERROR", error_number=error.errno) from None
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        raise


class CredentialContext:
    """Opaque issuer object; its public metadata has no credential or digest."""
    def __init__(self, authority, *, descriptor, identity, source_sha, source_tree, binding):
        require(authority is _AUTHORITY, "CREDENTIAL_SYNTHETIC_CONTEXT_FORBIDDEN")
        self._authority = authority
        metadata = {**identity, "fd": descriptor, "name": NAME, "bytes": os.fstat(descriptor).st_size}
        _ISSUERS[id(self)] = {"object": self, "metadata": metadata,
            "source_sha": source_sha, "source_tree": source_tree, "binding": dict(binding),
            "fd_identity": _fd_identity(os.fstat(descriptor)), "closed": False}

    @property
    def metadata(self):
        return dict(_registered(self)["metadata"])

    def close(self):
        close(self)


def _registered(context):
    state = _ISSUERS.get(id(context))
    require(type(context) is CredentialContext and state is not None and state["object"] is context
            and context._authority is _AUTHORITY, "CREDENTIAL_ACTUAL_IN_PROCESS_CONTEXT_REQUIRED")
    require(not state["closed"], "CREDENTIAL_CONTEXT_ALREADY_CLOSED")
    current = _kernel_identity()
    require(all(current[key] == state["metadata"][key] for key in ("pid", "start_ticks", "boot_id", "uid", "gid")),
            "CREDENTIAL_ISSUER_PROCESS_BIRTH_REBOUND")
    return state


def _validate_descriptor(state):
    metadata = state["metadata"]
    descriptor = metadata["fd"]
    try:
        details = os.fstat(descriptor)
        seals = fcntl.fcntl(descriptor, F_GET_SEALS)
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
        target = os.readlink("/proc/" + str(metadata["pid"]) + "/fd/" + str(descriptor))
    except OSError as error:
        raise CredentialError("CREDENTIAL_ORIGINAL_ISSUER_DESCRIPTOR_OS_ERROR", error_number=error.errno) from None
    require(stat.S_ISREG(details.st_mode) and stat.S_IMODE(details.st_mode) == 0o600
            and details.st_nlink == 0 and details.st_uid == metadata["uid"] and details.st_gid == metadata["gid"]
            and details.st_size == metadata["bytes"] and 0 < details.st_size <= MAXIMUM_BYTES
            and _fd_identity(details) == state["fd_identity"], "CREDENTIAL_ISSUER_DESCRIPTOR_IDENTITY_REBOUND")
    require(seals == SEALS and flags & fcntl.FD_CLOEXEC and target == "/memfd:" + NAME + " (deleted)",
            "CREDENTIAL_LIVE_ORIGINAL_SEALED_CLOEXEC_MEMFD_REQUIRED")
    return descriptor


def create(token, *, source_sha, source_tree, binding, owner_check):
    """Receive bytes directly in memory; never read/write a token environment."""
    binding = _exact_binding(source_sha, source_tree, binding)
    identity = _validate_issuer_authority(source_sha, source_tree, binding, owner_check)
    descriptor = _create_sealed_memfd(token)
    original = _fd_identity(os.fstat(descriptor))
    context = None
    try:
        context = CredentialContext(_AUTHORITY, descriptor=descriptor, identity=identity,
            source_sha=source_sha, source_tree=source_tree, binding=binding)
        validate(context, source_sha=source_sha, source_tree=source_tree, binding=binding, owner_check=owner_check)
        return context
    except BaseException:
        _close_original(descriptor, original)
        if context is not None:
            _ISSUERS[id(context)]["closed"] = True
        raise


def validate(context, *, source_sha, source_tree, binding, owner_check):
    state = _registered(context)
    require(state["source_sha"] == source_sha and state["source_tree"] == source_tree
            and state["binding"] == _exact_binding(source_sha, source_tree, binding),
            "CREDENTIAL_REGISTERED_SOURCE_OR_BINDING_REBOUND")
    _validate_issuer_authority(source_sha, source_tree, binding, owner_check)
    _validate_descriptor(state)
    return dict(state["metadata"])


def load_credential_property(context, *, source_sha, source_tree, binding, owner_check):
    """Supply only to an existing, separately authenticated original unit."""
    metadata = validate(context, source_sha=source_sha, source_tree=source_tree,
        binding=binding, owner_check=owner_check)
    return "LoadCredential=" + NAME + ":/proc/" + str(metadata["pid"]) + "/fd/" + str(metadata["fd"])


def close(context):
    """Caller reaches original FIN first; cleanup touches only its original fd."""
    state = _registered(context)
    _close_original(state["metadata"]["fd"], state["fd_identity"])
    state["closed"] = True
    # Keep the registration tombstone: a serialized, reused or closed object
    # cannot regain access, and an unrelated recycled fd is never closed.


def _close_original(descriptor, original):
    try:
        details = os.fstat(descriptor)
    except OSError as error:
        raise CredentialError("CREDENTIAL_ORIGINAL_ISSUER_DESCRIPTOR_OS_ERROR", error_number=error.errno) from None
    # Even a rejected mode/CLOEXEC mutation must release our own memfd. Device
    # and inode establish ownership; closing a recycled foreign fd is refused.
    require((details.st_dev, details.st_ino) == original[:2], "CREDENTIAL_CLEANUP_FOREIGN_DESCRIPTOR_FORBIDDEN")
    os.close(descriptor)


def _readonly_mount(path):
    from scripts import rc6_product_resource_compatibility as resource_profile
    mount = resource_profile.containing_mount(path,
        resource_profile.mount_rows(Path("/proc/self/mountinfo").read_text()))
    require("ro" in mount["options"] and "rw" not in mount["options"],
            "CREDENTIAL_ACTUAL_READONLY_SYSTEMD_MOUNT_REQUIRED")


def _root_controller(*, unit, source_sha, source_tree, binding, controller_proof, controller_request):
    require(os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 0,
            "CREDENTIAL_AUTHENTICATED_ROOT_ACTOR_REQUIRED")
    require(len(os.listdir("/proc/self/task")) == 1, "CREDENTIAL_ROOT_READ_BEFORE_THREADS_REQUIRED")
    binding = _exact_binding(source_sha, source_tree, binding)
    require(re.fullmatch(r"rc6-native-[0-9a-f]{32}\.service", unit or "") is not None,
            "CREDENTIAL_LITERAL_OWN_NATIVE_UNIT_REQUIRED")
    require(type(controller_request) is dict and controller_request.get("source_sha") == source_sha
            and controller_request.get("source_tree") == source_tree and controller_request.get("binding") == binding,
            "CREDENTIAL_ROOT_REQUEST_SOURCE_BINDING_REQUIRED")
    from scripts import rc6_privileged_custody as privileged
    broker = privileged.verify_live_controller(controller_proof, controller_request)
    require(broker["cgroup"].rsplit("/", 1)[-1] == unit,
            "CREDENTIAL_ACTUAL_CONTROLLER_UNIT_REBOUND")
    # The authenticated request is already bound to the admitted Source. A
    # ROOT Git command on the NONROOT checkout would add an unnecessary actor.
    from hashlib import sha256
    require(controller_request.get("code_hashes", {}).get(MEMBER) == sha256(Path(__file__).read_bytes()).hexdigest()
            and Path(__file__).absolute() == ROOT / MEMBER,
            "CREDENTIAL_AUTHENTICATED_SOURCE_MODULE_REQUIRED")
    return _kernel_identity()


def _credential_path(unit):
    return Path("/run/credentials") / unit / NAME


def _pidfd_live(descriptor, expected_pid):
    require(os.readlink("/proc/self/fd/" + str(descriptor)) == "anon_inode:[pidfd]",
            "CREDENTIAL_ORACLE_REAL_WORKER_PIDFD_REQUIRED")
    rows = Path("/proc/self/fdinfo/" + str(descriptor)).read_text().splitlines()
    pids = [row.split(":", 1)[1].strip() for row in rows if row.startswith("Pid:")]
    require(pids == [str(expected_pid)], "CREDENTIAL_ORACLE_WORKER_PIDFD_TARGET_REBOUND")
    readable, _, _ = select.select([descriptor], [], [], 0)
    require(not readable, "CREDENTIAL_ORACLE_WORKER_EXITED_BEFORE_READ")


def _root_broker_oracle(*, unit, source_sha, source_tree, binding, controller_proof,
                        controller_request, worker_birth, worker_pidfd):
    """ROOT PID1 reads only after its separately executed worker actually exists."""
    require(os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 0
            and os.getpid() == 1 and len(os.listdir("/proc/self/task")) == 1,
            "CREDENTIAL_SOURCE_FIXED_ROOT_BROKER_PID1_REQUIRED")
    binding = _exact_binding(source_sha, source_tree, binding)
    require(re.fullmatch(r"rc6-native-[0-9a-f]{32}\.service", unit or "") is not None
            and type(controller_request) is dict and controller_request.get("source_sha") == source_sha
            and controller_request.get("source_tree") == source_tree and controller_request.get("binding") == binding,
            "CREDENTIAL_ORACLE_EXACT_UNIT_SOURCE_REQUEST_REQUIRED")
    from scripts import rc6_privileged_custody as privileged
    require(type(controller_proof) is dict and type(controller_proof.get("guardian_native_mapping")) is dict
            and type(controller_proof.get("pid_boundary")) is dict,
            "CREDENTIAL_ORACLE_ORIGINAL_NATIVE_CONTROLLER_PROOF_REQUIRED")
    mapping = controller_proof["guardian_native_mapping"]
    guardian = privileged._external_controller(controller_proof, controller_request, mapping)
    private = privileged._private_identity(controller_request, mapping)
    broker = privileged.kernel_process(os.getpid())
    require(broker == controller_proof["pid_boundary"].get("local") == private["local"]
            and broker["pid"] == 1 and broker["uids"] == broker["gids"] == [0] * 4
            and broker["namespace_pids"] == [1] and broker["cgroup"] == "0::/system.slice/" + unit
            and broker["pid_namespace_inode"] != guardian["pid_namespace_inode"],
            "CREDENTIAL_ORACLE_ACTUAL_PRIVATE_BROKER_REBOUND")
    from hashlib import sha256
    require(controller_request.get("code_hashes", {}).get(MEMBER) == sha256(Path(__file__).read_bytes()).hexdigest()
            and Path(__file__).absolute() == ROOT / MEMBER,
            "CREDENTIAL_AUTHENTICATED_SOURCE_MODULE_REQUIRED")
    require(type(worker_birth) is dict and type(worker_birth.get("pid")) is int
            and worker_birth["pid"] > 1 and worker_birth.get("parent_pid") == 1
            and worker_birth.get("uids") == worker_birth.get("gids") == [0] * 4
            and worker_birth.get("namespace_pids") == [worker_birth["pid"]]
            and worker_birth.get("cgroup") == broker["cgroup"]
            and worker_birth.get("boot_id") == broker["boot_id"]
            and worker_birth.get("pid_namespace_inode") == broker["pid_namespace_inode"],
            "CREDENTIAL_ORACLE_ACTUAL_PRESEAL_ROOT_WORKER_BIRTH_REQUIRED")
    worker_pid = worker_birth["pid"]
    require(privileged.kernel_process(worker_pid) == worker_birth,
            "CREDENTIAL_ORACLE_ROOT_WORKER_KERNEL_BIRTH_REBOUND")
    require(os.stat("/proc/" + str(worker_pid) + "/ns/mnt").st_ino
            != os.stat("/proc/self/ns/mnt").st_ino,
            "CREDENTIAL_ORACLE_WORKER_EXEC_PRIVATE_MOUNT_REQUIRED")
    descriptor = None
    try:
        if worker_pidfd is None:
            descriptor = os.pidfd_open(worker_pid, 0)
        else:
            require(type(worker_pidfd) is int and worker_pidfd >= 0,
                    "CREDENTIAL_ORACLE_REAL_WORKER_PIDFD_REQUIRED")
            descriptor = fcntl.fcntl(worker_pidfd, fcntl.F_DUPFD_CLOEXEC, 3)
        require(fcntl.fcntl(descriptor, fcntl.F_GETFD) & fcntl.FD_CLOEXEC,
                "CREDENTIAL_ORACLE_WORKER_PIDFD_CLOEXEC_REQUIRED")
        _pidfd_live(descriptor, worker_pid)
        require(privileged.kernel_process(worker_pid) == worker_birth,
                "CREDENTIAL_ORACLE_ROOT_WORKER_KERNEL_BIRTH_REBOUND")
        return _kernel_identity(), descriptor, worker_pid
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise CredentialError("CREDENTIAL_ORACLE_WORKER_NATIVE_READBACK_OS_ERROR", error_number=error.errno) from None
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        raise


def _copy_owner_is_root(details):
    return details.st_uid == details.st_gid == 0


def _copy_is_readonly(details, expected_bytes):
    return stat.S_ISREG(details.st_mode) and stat.S_IMODE(details.st_mode) == 0o400 \
        and details.st_nlink == 1 and _copy_owner_is_root(details) and details.st_size == expected_bytes


def read_once(*, unit, expected_bytes, source_sha, source_tree, binding, controller_proof, controller_request,
              role=WORKER_ROLE, worker_birth=None, worker_pidfd=None):
    """Return a transient bytearray; caller closes its client and wipes before seal."""
    require(role in (WORKER_ROLE, BROKER_ORACLE_ROLE), "CREDENTIAL_SOURCE_FIXED_READER_ROLE_REQUIRED")
    observer, worker_pid = None, None
    if role == BROKER_ORACLE_ROLE:
        identity, observer, worker_pid = _root_broker_oracle(unit=unit, source_sha=source_sha,
            source_tree=source_tree, binding=binding, controller_proof=controller_proof,
            controller_request=controller_request, worker_birth=worker_birth, worker_pidfd=worker_pidfd)
    else:
        require(worker_birth is None and worker_pidfd is None, "CREDENTIAL_WORKER_ROLE_CANNOT_ASSUME_BROKER_ORACLE")
        identity = _root_controller(unit=unit, source_sha=source_sha, source_tree=source_tree,
            binding=binding, controller_proof=controller_proof, controller_request=controller_request)
    try:
        return _read_once_copy(identity, unit=unit, expected_bytes=expected_bytes, observer=observer, worker_pid=worker_pid)
    finally:
        if observer is not None:
            os.close(observer)


def _read_once_copy(identity, *, unit, expected_bytes, observer, worker_pid):
    require(type(expected_bytes) is int and 0 < expected_bytes <= MAXIMUM_BYTES,
            "CREDENTIAL_EXACT_BOUNDED_COPY_BYTES_REQUIRED")
    key = (identity["pid"], identity["start_ticks"], identity["boot_id"], unit)
    require(key not in _READS, "CREDENTIAL_ROOT_SINGLE_USE_REQUIRED")
    _READS.add(key)
    path = _credential_path(unit)
    require(not any(row.is_symlink() for row in (path, *path.parents)), "CREDENTIAL_READONLY_COPY_ALIAS_FORBIDDEN")
    _readonly_mount(path)
    descriptor = None
    token = bytearray()
    try:
        if observer is not None:
            _pidfd_live(observer, worker_pid)
        require(_copy_is_readonly(path.lstat(), expected_bytes), "CREDENTIAL_ROOT_READONLY_COPY_IDENTITY_REQUIRED")
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(descriptor)
        require(_copy_is_readonly(before, expected_bytes), "CREDENTIAL_ROOT_READONLY_COPY_IDENTITY_REQUIRED")
        while len(token) <= expected_bytes:
            raw = os.read(descriptor, min(4096, expected_bytes + 1 - len(token)))
            if not raw:
                break
            token.extend(raw)
        require(len(token) == expected_bytes and _fd_identity(before) == _fd_identity(os.fstat(descriptor))
                == _fd_identity(path.lstat()), "CREDENTIAL_ROOT_COPY_CHANGED_OR_INCOMPLETE")
        if observer is not None:
            _pidfd_live(observer, worker_pid)
        return token
    except OSError as error:
        wipe(token)
        raise CredentialError("CREDENTIAL_ROOT_READONLY_COPY_OS_ERROR", error_number=error.errno) from None
    except BaseException:
        wipe(token)
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def wipe(value):
    """Erase only the caller's mutable buffer; no claim about prior Python copies."""
    require(type(value) is bytearray, "CREDENTIAL_MUTABLE_TRANSIENT_BUFFER_REQUIRED")
    for index in range(len(value)):
        value[index] = 0
