"""Proposed G5A bounded acquisition and G5B complete original qualification.

The legacy all1202 hold is unchanged. Owner acceptance creates no native proof:
G5A also requires an in-process live project-quota handle, native custody and
authenticated G0--G4 artifacts. G5A never produces a GREEN gate. Serialized
receipts, environment variables and guessed filesystem bounds are not handles.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import ctypes
import errno
import fcntl
import os
from pathlib import Path
import re
import stat
import struct
from types import MappingProxyType
import json
import hashlib
import select
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from rc6_shadow_runtime import archive_components as components
from rc6_shadow_runtime.archive_physical_model import STATE_CLASSES, horizon_schedule


REVISION = "G5A_BOUNDED_EVIDENCE_V2"
SCOPE = "EVIDENCE_ACQUISITION_ONLY"
SCHEMA = "rc6.g5-bounded-evidence-contract.v2"
CAP_BYTES = 512 * 1024**2
CAP_INODES = 32768
_ISSUER = object()
_HANDLES = {}
_AUTHORITIES = {}
_CUSTODY = {}
_QUOTA_PROOFS = {}
_TRUSTED_API = {}
_PREPARATIONS = {}
_ORACLES = {}
_ORACLE_READERS = {}
_CLOSED_ORACLE_READERS = {}
_CLOSED_QUOTA_PREPARATIONS = {}
_QUOTA_DESCRIPTOR_PROOFS = {}
_WORKLOAD_QUOTAS = {}
FSGETXATTR = 0x801C581F
PROJINHERIT = 0x200
Q_GETFMT, Q_GETQUOTA, PRJQUOTA = 0x800004, 0x800007, 2
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")

# Bounds are obligations BEFORE the specific write. None, a sample, expected
# compression and future GC do not count. All are simultaneous physical bytes.
RESERVATION_CLASSES = frozenset({
    "new_whole_packs", "recipes", "receipts", "controls",
    "publication_temporaries", "gc_temporaries", "recovery_temporaries",
    "directory_and_filesystem_metadata", "other_owned_allocations",
})
CONTRACT = MappingProxyType({
    "schema": SCHEMA, "revision": REVISION, "scope": SCOPE,
    "original_cuts": 1202, "archive_limit_bytes": CAP_BYTES,
    "archive_limit_inodes": CAP_INODES, "maximum_dependency_depth": 32,
    "contracted_retention_seconds": 32400, "recovery_margin_seconds": 3600,
    "acquisition_may_stop_red": True, "acquisition_grants_g5_green": False,
    "acquisition_grants_native_v4_writer": False,
    "candidate_native_writer": "CANDIDATE_V2_NATIVE_COMPONENT_V4",
    "native_entrypoint": "ComponentArchive.build",
    "native_runtime_archive_format": "COMPONENT_V3",
    "proposed_runtime_archive_format": "COMPONENT_V4",
    "acquisition_native_writer_scope": "OWN_PRIVATE_EVIDENCE_ONLY_NOT_ARTIFACT_PROMOTION",
    "g5b_requires_same_effective_future_artifact_writer": True,
    "acquisition_initial_admission": "G0_G4_AND_ACCEPTED_NATIVE_CAPABILITY_BOOTSTRAP_THEN_LIVE_COMPOUND_BOUND_NO_PRESTATED_HORIZON_GREEN",
    "raw_transport_charge": "ORIGINAL20GIB_PHYSICAL_WITH_RESERVE4GIB_INODES10PERCENT",
    "raw_capture_lifecycle": "BOTH_NATIVE_FIN_AND_COMPLETE_PINNED_STREAM_THEN_LOOP_GONE_OWN_BACKING_RETIRE_ACTUAL_COPY_PREFLIGHT_NO_FUTURE_CREDIT",
    "native_reader_review_scope": "EXACT_FOUR_SOURCE_CAS_REVIEW_MODULES_POST_BOTH_ROOT_FIN_ORIGINAL_FILESYSTEM_NO_SOURCE_COPY_NO_G1_CLAIM",
    "host_stage_capacity": "ACTUAL_AGGREGATE_ENFORCEMENT_OR_COMPLETE_SOURCE_BURST_PROOF_REQUIRED_POLLING_IS_NOT_ADMISSION",
    "host_stage_hard_bytes": 20 * 1024**3,
    "own_upload_read_identity": "RECORD_ACTUAL_READ_ATIME_ONLY_OTHER_TEN_FIELDS_AND_SHA_UNCHANGED_ORIGINAL_SOURCE_ALL11_STRICT",
    "quota_authority": "LIVE_NATIVE_PROJECT_QUOTA_READBACK_AND_SEALED_NONROOT",
    "reservation": "CURRENT_ALLOCATED_PLUS_ALL_SIMULTANEOUS_COSTS",
    "future_gc_credit_bytes": 0, "gc_unit": "WHOLE_IMMUTABLE_PACK",
    "original_data_substitution_allowed": False, "storage_reduction_extrapolated": False,
    "qualification": "ALL1202_ORIGINAL_BYTES_AND_NATIVE_ACK_GC_RESTORE_RECOVERY",
    "release_binding": "SAME_WRITER_CODEC_RESTORER_SOURCE_BYTES_IN_IMMUTABLE_ARTIFACT",
    "per_write_envelope": "CLOSED_SEQUENTIAL_EXT4_WRITES_CURRENT_ALLOCATION_NO_FUTURE_COMPRESSION_OR_GC",
    "state_ledger": "ACTUAL_BEFORE_AFTER_OPERATIONS_AND_EXPLICIT_NOT_ENTERED_STATES",
    "owner_decision_lifecycle": "IMMUTABLE_DECISION_LIVE_OWNER_REVOCATION_V1_SAME_NATIVE_OPERATION_ONLY",
    "real_orders_sent": 0, "real_routes": "NOT_CALLED",
})
CONTRACT_SHA256 = components.sha(components.canonical(dict(CONTRACT)))


def require(value, signature):
    if not value:
        raise ValueError(signature)


def quota_setup_request(binding, *, archive_path):
    """Exact request for TrustedQuotaSetup; neither sets quota nor launches."""
    require(type(binding) is dict and _SHA.fullmatch(str(binding.get("candidate_sha", "")))
            and _SHA.fullmatch(str(binding.get("candidate_tree", "")))
            and all(isinstance(binding.get(key), str) and binding[key] for key in
                    ("owner_id", "attempt_id", "producer", "runner_class", "workload_fingerprint"))
            and binding["runner_class"] == "github-hosted/ubuntu-24.04",
            "G5A_EXACT_SOURCE_OWNER_ATTEMPT_BINDING_REQUIRED")
    from scripts.rc6_material_carrier import workload_fingerprint
    require(binding["producer"] == "Horizon-private-original-cas"
            and binding["workload_fingerprint"] == workload_fingerprint("Horizon-private-original-cas"),
            "G5A_EXACT_ORIGINAL_TWO_CATALOG_WORKLOAD_REQUIRED")
    path = Path(archive_path).absolute()
    require(not any(item.is_symlink() for item in (path, *path.parents)),
            "G5A_QUOTA_PATH_ALIAS_FORBIDDEN")
    return {"schema": "rc6.g5a-trusted-quota-setup-request.v2", "revision": REVISION,
        "contract_sha256": CONTRACT_SHA256, "binding": dict(binding), "archive_path": str(path),
        "hard_bytes": CAP_BYTES, "hard_inodes": CAP_INODES,
        "required_phases": ["TrustedQuotaSetup", "Seal", "NONROOT", "Custody_FIN"],
        "required_native_proof": ["Q_GETFMT", "Q_GETQUOTA", "FSGETXATTR_PROJINHERIT",
            "SAME_FILESYSTEM_1MIB_EDQUOT", "SEALED_QUOTA_AND_PROJECT_MUTATION_DENIED"],
        "supervisor_fin_required": True, "g5_qualification_allowed": False,
        "native_v4_write_enabled": False, "real_orders_sent": 0}


class TrustedAPIController:
    """A live ROOT trusted-setup observation, never a sealed custody witness."""
    __slots__ = ("pid", "role", "request_wire", "proof_wire", "birth_wire", "broker_wire")

    def __new__(cls, *args, **kwargs):
        raise ValueError("G5A_TRUSTED_API_NATIVE_OBSERVER_REQUIRED")

    def __setattr__(self, name, value):
        raise AttributeError("G5A_TRUSTED_API_CONTEXT_IMMUTABLE")


def _observe_trusted_api(request, proof, role):
    """Observe pre-Seal setup explicitly; never disable verify_live_controller."""
    from scripts import rc6_privileged_custody as privileged
    require(role in {"worker", "broker"} and os.getresuid() == os.getresgid() == (0, 0, 0)
            and len(os.listdir("/proc/self/task")) == 1,
            "G5A_SOURCE_FIXED_SINGLE_ROOT_TRUSTED_API_SETUP_REQUIRED")
    require(type(request) is dict and type(proof) is dict
            and request.get("g5a_contract_sha256") == CONTRACT_SHA256
            and request.get("require_quota_ancestor_seal") is True
            and request.get("source_sha") == request.get("binding", {}).get("candidate_sha")
            and request.get("source_tree") == request.get("binding", {}).get("candidate_tree"),
            "G5A_TRUSTED_API_EXACT_SOURCE_REQUEST_REQUIRED")
    source = Path(request.get("source_root", "")).absolute()
    member = "scripts/rc6_g5_contract.py"
    require(source / member == Path(__file__).absolute()
            and request.get("code_hashes", {}).get(member) == components.sha(Path(__file__).read_bytes()),
            "G5A_TRUSTED_API_EXACT_EXECUTED_SOURCE_BYTES_REQUIRED")
    mapping, boundary = proof.get("guardian_native_mapping"), proof.get("pid_boundary")
    require(type(mapping) is dict and type(boundary) is dict
            and mapping.get("binding") == request["binding"]
            and mapping.get("source_sha") == request["source_sha"]
            and mapping.get("source_tree") == request["source_tree"],
            "G5A_TRUSTED_API_ORIGINAL_NATIVE_CONTROLLER_REQUIRED")
    guardian = privileged._external_controller(proof, request, mapping)
    private = privileged._private_identity(request, mapping)
    broker, current = privileged.kernel_process(1), privileged.kernel_process(os.getpid())
    require(broker == boundary.get("local") == private["local"]
            and mapping.get("host_private_PID1") == boundary.get("host")
            and broker["uids"] == broker["gids"] == [0] * 4
            and broker["namespace_pids"] == [1]
            and broker["pid_namespace_inode"] != guardian["pid_namespace_inode"]
            and broker["cgroup"] == guardian["cgroup"]
            and current["uids"] == current["gids"] == [0] * 4
            and current["pid_namespace_inode"] == broker["pid_namespace_inode"]
            and current["boot_id"] == broker["boot_id"] and current["cgroup"] == broker["cgroup"],
            "G5A_TRUSTED_API_PRIVATE_ROOT_CONTROLLER_KERNEL_REBOUND")
    if role == "broker":
        require(current == broker and os.getpid() == 1, "G5A_TRUSTED_API_ACTUAL_PRIVATE_PID1_REQUIRED")
    else:
        require(current["pid"] > 1 and current["parent_pid"] == 1
                and current["namespace_pids"] == [current["pid"]]
                and os.stat("/proc/self/ns/mnt").st_ino != os.stat("/proc/1/ns/mnt").st_ino
                and proof.get("calibration_request_sha256") == request.get("original_request_sha256"),
                "G5A_TRUSTED_API_FIXED_EXECUTED_WORKER_BIRTH_REQUIRED")
    return current, broker


def observe_trusted_api_controller(request, proof, *, role="worker"):
    current, broker = _observe_trusted_api(request, proof, role)
    result = object.__new__(TrustedAPIController)
    fields = {"pid": os.getpid(), "role": role, "request_wire": components.canonical(request),
        "proof_wire": components.canonical(proof), "birth_wire": components.canonical(current),
        "broker_wire": components.canonical(broker)}
    for name, value in fields.items(): object.__setattr__(result, name, value)
    _TRUSTED_API[id(result)] = (result, fields)
    return result


def _trusted_api(context, *, role):
    registered = _TRUSTED_API.get(id(context))
    require(type(context) is TrustedAPIController and registered is not None
            and registered[0] is context and registered[1]["pid"] == os.getpid()
            and all(getattr(context, name) == value for name, value in registered[1].items())
            and context.role == role, "G5A_AUTHENTIC_CURRENT_TRUSTED_API_CONTROLLER_REQUIRED")
    request, proof = components.loads(context.request_wire), components.loads(context.proof_wire)
    current, broker = _observe_trusted_api(request, proof, role)
    require(components.canonical(current) == context.birth_wire
            and components.canonical(broker) == context.broker_wire,
            "G5A_TRUSTED_API_BIRTH_CHANGED")
    return request, proof


def _oracle_path(path):
    path = Path(path).absolute()
    require(not any(node.is_symlink() for node in (path, *path.parents)), "G5A_ROOT_ORACLE_ALIAS_FORBIDDEN")
    return path


def _oracle_fifo(path, descriptor):
    info, path_info = os.fstat(descriptor), _oracle_path(path).lstat()
    require(_identity(info) == _identity(path_info) and stat.S_ISFIFO(info.st_mode)
            and info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_nlink == 1 and fcntl.fcntl(descriptor, fcntl.F_GETFD) & fcntl.FD_CLOEXEC,
            "G5A_ROOT_ORACLE_PINNED_ROOT_FIFO_REQUIRED")
    return {"identity": list(_identity(info)), "mount_id": _mount_id(descriptor)}


def _oracle_wait(descriptor, event, deadline):
    remaining = deadline - time.monotonic()
    require(remaining > 0, "G5A_ROOT_ORACLE_TOTAL_DEADLINE_EXCEEDED")
    poller = select.poll(); poller.register(descriptor, event)
    for _, actual in poller.poll(max(1, int(min(remaining, 5) * 1000))):
        require(not actual & (select.POLLERR | select.POLLNVAL), "G5A_ROOT_ORACLE_TRANSPORT_CLOSED")
        if actual & event: return
    require(time.monotonic() < deadline, "G5A_ROOT_ORACLE_TOTAL_DEADLINE_EXCEEDED")


def _oracle_write(descriptor, raw, deadline):
    require(type(raw) is bytes and 0 < len(raw) <= 6 * 1024**2, "G5A_ROOT_ORACLE_FRAME_BOUND")
    data, offset = struct.pack("!I", len(raw)) + raw, 0
    while offset < len(data):
        _oracle_wait(descriptor, select.POLLOUT, deadline)
        try: offset += os.write(descriptor, data[offset:offset + 65536])
        except BlockingIOError: pass


def _oracle_read(descriptor, deadline):
    def exact(size):
        output = bytearray()
        while len(output) < size:
            _oracle_wait(descriptor, select.POLLIN, deadline)
            try: chunk = os.read(descriptor, min(65536, size - len(output)))
            except BlockingIOError: continue
            require(bool(chunk), "G5A_ROOT_ORACLE_UNEXPECTED_EOF")
            output.extend(chunk)
        return bytes(output)
    size = struct.unpack("!I", exact(4))[0]
    require(0 < size <= 6 * 1024**2, "G5A_ROOT_ORACLE_FRAME_BOUND")
    return components.loads(exact(size))


def _oracle_api_path(path):
    if path == "": return path  # fixed repository metadata, no other origin
    require(type(path) is str and len(path) <= 512 and path.startswith("/")
            and not any(ord(char) < 32 for char in path) and "#" not in path and "\\" not in path
            and ".." not in path and not path.startswith("//"), "G5A_ROOT_ORACLE_READONLY_API_PATH_INVALID")
    # Only the existing ownership and Actions provenance readers can be used.
    parsed = urllib.parse.urlsplit(path)
    query = urllib.parse.parse_qs(parsed.query, strict_parsing=True) if parsed.query else {}
    require(not parsed.scheme and not parsed.netloc and set(query) <= {"since", "per_page", "page", "ref", "head_sha"}
            and all(len(values) == 1 for values in query.values())
            and (not query or "per_page" not in query or query.get("per_page") == ["100"])
            and ("page" not in query or re.fullmatch(r"[1-9][0-9]{0,2}", query["page"][0]))
            and ("since" not in query or re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z", query["since"][0]))
            and ("head_sha" not in query or _SHA.fullmatch(query["head_sha"][0]))
            and ("ref" not in query or query["ref"] == ["ops/rc6-night-supervisor-20261005"]),
            "G5A_ROOT_ORACLE_READONLY_API_QUERY_INVALID")
    require(re.fullmatch(r"/(?:issues/(?:471|473)(?:/comments)?"
        r"|issues/comments/[1-9][0-9]*|actions/artifacts/[1-9][0-9]*"
        r"|git/commits/[0-9a-f]{40}|git/ref/heads/[A-Za-z0-9_./-]{1,200}|pulls/[1-9][0-9]*"
        r"|contents/docs/automation/rc6-night-20261005/state\.json"
        r"|actions/workflows/(?:porota-predeploy-v2|rc6-unified-candidate-tests)\.yml/runs"
        r"|actions/runs/[1-9][0-9]*(?:/attempts/[1-9][0-9]*(?:/jobs)?)?)", parsed.path)
            and (not query or parsed.path.endswith(("/comments", "/jobs", "/runs", "/state.json")))
            and ("since" not in query or parsed.path.endswith("/comments")),
        "G5A_ROOT_ORACLE_SOURCE_CLOSED_GET_ONLY")
    return path


class RootOwnerOracle:
    """Existing private ROOT PID1 serves bounded read-only API to its fixed child.

    This object never receives a token in a serializable request. Its GitHub
    client belongs exclusively to the ROOT broker. Both FIFO origins are pinned
    before the worker opens them, and are destroyed before either actor seals.
    """
    @classmethod
    def start(cls, directory, *, trusted_controller):
        request, _ = _trusted_api(trusted_controller, role="broker")
        directory = _oracle_path(directory)
        require(directory.is_relative_to(Path(request["namespace_receipt"]["path"]))
                and not os.path.lexists(directory), "G5A_ROOT_ORACLE_FRESH_OWN_ROOT_DIRECTORY_REQUIRED")
        directory.mkdir(mode=0o700)
        result = cls(); result.controller = trusted_controller; result.path = directory
        result.nonce = secrets.token_hex(32); result.fds = []; result.worker = None
        result.sequence, result.closed, result.done = 0, False, False
        try:
            for name in ("worker-request", "root-response"):
                path = directory / name; os.mkfifo(path, mode=0o600)
                result.fds.append(os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC))
            broker = components.loads(trusted_controller.birth_wire)
            result.metadata = {"schema": "rc6.g5a-root-owner-oracle.v2", "source_sha": request["source_sha"],
                "source_tree": request["source_tree"], "contract_sha256": CONTRACT_SHA256,
                "binding": request["binding"], "nonce": result.nonce, "directory": str(directory),
                "broker_birth": broker, "broker_mount_namespace_inode": os.stat("/proc/self/ns/mnt").st_ino,
                "request_fifo": _oracle_fifo(directory / "worker-request", result.fds[0]),
                "response_fifo": _oracle_fifo(directory / "root-response", result.fds[1]),
                "maximum_requests": 256, "read_only": True, "g5_qualification_allowed": False}
            _ORACLES[id(result)] = (result, os.getpid(), trusted_controller, tuple(result.fds),
                                    components.canonical(result.metadata))
            return result
        except BaseException:
            result.close(); raise

    def _validate(self):
        require(not self.closed and _ORACLES.get(id(self)) == (self, os.getpid(), self.controller,
            tuple(self.fds), components.canonical(self.metadata)), "G5A_ROOT_ORACLE_NATIVE_CURRENT_REGISTRY_REQUIRED")
        _trusted_api(self.controller, role="broker")
        require(_oracle_fifo(self.path / "worker-request", self.fds[0]) == self.metadata["request_fifo"]
                and _oracle_fifo(self.path / "root-response", self.fds[1]) == self.metadata["response_fifo"],
                "G5A_ROOT_ORACLE_FIFO_REBOUND")

    def bind_worker(self, worker_birth):
        from scripts import rc6_privileged_custody as privileged
        self._validate()
        broker = components.loads(self.controller.birth_wire)
        require(self.worker is None and type(worker_birth) is dict and type(worker_birth.get("pid")) is int
                and worker_birth["pid"] > 1 and worker_birth.get("parent_pid") == 1
                and worker_birth.get("uids") == worker_birth.get("gids") == [0] * 4
                and all(worker_birth.get(key) == broker[key] for key in ("cgroup", "boot_id", "pid_namespace_inode"))
                and privileged.kernel_process(worker_birth["pid"]) == worker_birth
                and os.stat(f"/proc/{worker_birth['pid']}/ns/mnt").st_ino != os.stat("/proc/self/ns/mnt").st_ino,
                "G5A_ROOT_ORACLE_ACTUAL_FIXED_WORKER_BIRTH_REQUIRED")
        self.worker = components.canonical(worker_birth)
        return {"worker_birth": worker_birth, "oracle_metadata_sha256": components.sha(components.canonical(self.metadata))}

    def serve_one(self, github, *, deadline):
        from scripts import rc6_privileged_custody as privileged
        self._validate()
        require(self.worker is not None and not self.done and self.sequence < 256,
                "G5A_ROOT_ORACLE_WORKER_NOT_BOUND_OR_REQUEST_LIMIT")
        worker = components.loads(self.worker)
        require(privileged.kernel_process(worker["pid"]) == worker, "G5A_ROOT_ORACLE_WORKER_BIRTH_REBOUND")
        query = _oracle_read(self.fds[0], deadline)
        require(type(query) is dict and query.get("schema") == "rc6.g5a-root-oracle-query.v2"
                and query.get("nonce") == self.nonce and query.get("sequence") == self.sequence
                and query.get("worker_birth") == worker and _HEX.fullmatch(str(query.get("challenge", "")))
                and query.get("oracle_metadata_sha256") == components.sha(components.canonical(self.metadata)),
                "G5A_ROOT_ORACLE_CHALLENGE_SOURCE_BIRTH_OR_SEQUENCE_REBOUND")
        base = {"schema": "rc6.g5a-root-oracle-response.v2", "sequence": self.sequence,
                "nonce": self.nonce, "challenge": query["challenge"], "broker_birth": self.metadata["broker_birth"],
                "oracle_metadata_sha256": query["oracle_metadata_sha256"]}
        operation = query.get("operation")
        if operation == "GET":
            value = github.request(_oracle_api_path(query.get("path")))
            raw = components.canonical(value)
            require(len(raw) <= 4 * 1024**2, "G5A_ROOT_ORACLE_API_METADATA_BOUND")
            _oracle_write(self.fds[1], components.canonical({**base, "kind": "JSON", "value": value}), deadline)
        elif operation == "DOWNLOAD":
            from scripts.rc6_architectural_gates import MAX_ARCHIVE_BYTES
            identifier, expected_size, digest = query.get("artifact_id"), query.get("expected_size"), query.get("expected_digest")
            require(type(identifier) is int and identifier > 0 and type(expected_size) is int
                    and 0 < expected_size <= MAX_ARCHIVE_BYTES and type(digest) is str
                    and re.fullmatch(r"sha256:[0-9a-f]{64}", digest), "G5A_ROOT_ORACLE_ARTIFACT_REQUEST_BOUND")
            metadata = github.request("/actions/artifacts/" + str(identifier))
            require(metadata.get("id") == identifier and metadata.get("size_in_bytes") == expected_size
                    and metadata.get("digest") == digest and metadata.get("expired") is False,
                    "G5A_ROOT_ORACLE_ARTIFACT_CURRENT_API_REBOUND")
            _oracle_write(self.fds[1], components.canonical({**base, "kind": "ARTIFACT_BEGIN", "artifact_id": identifier,
                "expected_size": expected_size, "expected_digest": digest}), deadline)
            total, hasher, chunk_index = 0, hashlib.sha256(), 0
            with github.open_artifact(identifier, deadline=deadline) as response:
                while True:
                    require(time.monotonic() < deadline, "G5A_ROOT_ORACLE_TOTAL_DEADLINE_EXCEEDED")
                    chunk = response.read(65536)
                    if not chunk: break
                    total += len(chunk); hasher.update(chunk)
                    require(total <= expected_size, "G5A_ROOT_ORACLE_ARTIFACT_BYTES_EXCEEDED")
                    _oracle_write(self.fds[1], components.canonical({**base, "kind": "ARTIFACT_CHUNK", "chunk_index": chunk_index,
                        "bytes_base64": base64.b64encode(chunk).decode("ascii")}), deadline)
                    chunk_index += 1
            require(total == expected_size and "sha256:" + hasher.hexdigest() == digest,
                    "G5A_ROOT_ORACLE_ARTIFACT_ACTUAL_BYTES_REBOUND")
            _oracle_write(self.fds[1], components.canonical({**base, "kind": "ARTIFACT_END", "bytes": total,
                "sha256": hasher.hexdigest(), "chunks": chunk_index}), deadline)
        elif operation == "CLOSE":
            _oracle_write(self.fds[1], components.canonical({**base, "kind": "CLOSED"}), deadline)
            self.close_query = components.canonical(query)
            self.done = True
        else:
            raise ValueError("G5A_ROOT_ORACLE_READONLY_OPERATION_REQUIRED")
        require(privileged.kernel_process(worker["pid"]) == worker, "G5A_ROOT_ORACLE_WORKER_BIRTH_CHANGED_DURING_GET")
        self.sequence += 1
        return self.done

    def finish_close(self, *, deadline):
        """Observe the worker's paired receipt and actual FIFO-FD closure.

        A deadline bounds failure; elapsed time never proves descriptor closure.
        Both pinned endpoints remain present until this handshake completes.
        """
        from scripts import rc6_privileged_custody as privileged
        self._validate()
        require(self.done and self.worker is not None and hasattr(self, "close_query"),
                "G5A_ROOT_ORACLE_CLOSE_QUERY_REQUIRED")
        query, worker = components.loads(self.close_query), components.loads(self.worker)
        receipt = _oracle_read(self.fds[0], deadline)
        expected = {"schema": "rc6.g5a-root-oracle-close-receipt.v2", "worker_birth": worker,
            **{key: query[key] for key in ("nonce", "sequence", "challenge", "oracle_metadata_sha256")}}
        require(receipt == expected, "G5A_ROOT_ORACLE_WORKER_CLOSE_RECEIPT_REBOUND")
        origins = {(tuple(self.metadata[key]["identity"][:2])) for key in ("request_fifo", "response_fifo")}
        for _ in range(4096):
            require(time.monotonic() < deadline, "G5A_ROOT_ORACLE_ACTUAL_DESCRIPTOR_CLOSE_DEADLINE")
            require(privileged.kernel_process(worker["pid"]) == worker, "G5A_ROOT_ORACLE_WORKER_BIRTH_REBOUND_AT_CLOSE")
            open_origins = set()
            for path in Path(f"/proc/{worker['pid']}/fd").iterdir():
                try: info = path.stat()
                except FileNotFoundError: continue
                if (info.st_dev, info.st_ino) in origins: open_origins.add((info.st_dev, info.st_ino))
            if not open_origins:
                self.worker_close_verified = True
                return {"status": "ACTUAL_KERNEL_FIFO_DESCRIPTORS_CLOSED", "worker_birth": worker,
                        "paired_close_receipt_sha256": components.sha(components.canonical(receipt))}
        raise ValueError("G5A_ROOT_ORACLE_WORKER_FIFO_DESCRIPTOR_STILL_OPEN")

    def close(self):
        if self.closed: return
        registered = _ORACLES.pop(id(self), None)
        for descriptor in self.fds:
            try: os.close(descriptor)
            except OSError: pass
        self.fds = []; self.closed = True
        if registered is not None:
            for name in ("worker-request", "root-response"):
                path = self.path / name
                info = path.lstat()
                expected = components.loads(registered[4])["request_fifo" if name == "worker-request" else "response_fifo"]
                require(list(_identity(info)) == expected["identity"], "G5A_ROOT_ORACLE_CLEANUP_REBOUND")
                path.unlink()
            self.path.rmdir()


class RootOwnerOracleReader:
    """Kernel-authenticated ROOT worker input; no bearer, URL or JSON factory."""
    @classmethod
    def open(cls, metadata, *, trusted_controller):
        request, _ = _trusted_api(trusted_controller, role="worker")
        require(type(metadata) is dict and metadata.get("schema") == "rc6.g5a-root-owner-oracle.v2"
                and metadata.get("source_sha") == request["source_sha"] and metadata.get("source_tree") == request["source_tree"]
                and metadata.get("binding") == request["binding"] and metadata.get("contract_sha256") == CONTRACT_SHA256
                and components.canonical(metadata.get("broker_birth")) == trusted_controller.broker_wire
                and metadata.get("broker_mount_namespace_inode") == os.stat("/proc/1/ns/mnt").st_ino
                and _HEX.fullmatch(str(metadata.get("nonce", ""))),
                "G5A_ROOT_ORACLE_NATIVE_SOURCE_AND_PARENT_REQUIRED")
        directory = _oracle_path(metadata.get("directory", "")); info = directory.lstat()
        require(directory.is_relative_to(Path(request["namespace_receipt"]["path"])) and stat.S_ISDIR(info.st_mode)
                and info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o700,
                "G5A_ROOT_ORACLE_EXACT_ROOT_DIRECTORY_REQUIRED")
        result = cls(); result.controller = trusted_controller; result.metadata = dict(metadata)
        result.path, result.sequence, result.fds, result.closed = directory, 0, [], False
        try:
            for name, flags in (("worker-request", os.O_WRONLY), ("root-response", os.O_RDONLY)):
                descriptor = os.open(directory / name, flags | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
                result.fds.append(descriptor)
                require(_oracle_fifo(directory / name, descriptor) == metadata["request_fifo" if name == "worker-request" else "response_fifo"],
                        "G5A_ROOT_ORACLE_FIFO_KERNEL_ORIGIN_REBOUND")
            _ORACLE_READERS[id(result)] = (result, os.getpid(), trusted_controller, tuple(result.fds), components.canonical(metadata))
            return result
        except BaseException:
            for descriptor in result.fds: os.close(descriptor)
            raise

    def _validate(self):
        require(not self.closed and _ORACLE_READERS.get(id(self)) == (self, os.getpid(), self.controller,
            tuple(self.fds), components.canonical(self.metadata)), "G5A_ROOT_ORACLE_CURRENT_READER_REQUIRED")
        _trusted_api(self.controller, role="worker")
        for index, name in enumerate(("worker-request", "root-response")):
            require(_oracle_fifo(self.path / name, self.fds[index]) == self.metadata["request_fifo" if index == 0 else "response_fifo"],
                    "G5A_ROOT_ORACLE_READER_PINNED_ORIGIN_REBOUND")

    def _query(self, operation, arguments, deadline):
        self._validate()
        require(self.sequence < 256, "G5A_ROOT_ORACLE_REQUEST_COUNT_EXCEEDED")
        challenge = secrets.token_hex(32)
        query = {"schema": "rc6.g5a-root-oracle-query.v2", "nonce": self.metadata["nonce"], "sequence": self.sequence,
            "oracle_metadata_sha256": components.sha(components.canonical(self.metadata)),
            "worker_birth": components.loads(self.controller.birth_wire), "challenge": challenge,
            "operation": operation, **arguments}
        _oracle_write(self.fds[0], components.canonical(query), deadline)
        return query

    def _response(self, query, deadline):
        self._validate(); response = _oracle_read(self.fds[1], deadline)
        require(type(response) is dict and response.get("schema") == "rc6.g5a-root-oracle-response.v2"
                and all(response.get(key) == query[key] for key in ("sequence", "nonce", "challenge", "oracle_metadata_sha256"))
                and response.get("broker_birth") == self.metadata["broker_birth"],
                "G5A_ROOT_ORACLE_PAIRED_ACTUAL_RESPONSE_REBOUND")
        return response

    def request(self, path):
        deadline = time.monotonic() + 60
        query = self._query("GET", {"path": _oracle_api_path(path)}, deadline)
        response = self._response(query, deadline)
        require(response.get("kind") == "JSON" and len(components.canonical(response.get("value"))) <= 4 * 1024**2,
                "G5A_ROOT_ORACLE_ACTUAL_METADATA_REQUIRED")
        self.sequence += 1
        return response["value"]

    def download(self, artifact_id, output, *, expected_size, expected_digest, deadline):
        from scripts.rc6_architectural_gates import MAX_ARCHIVE_BYTES
        require(type(artifact_id) is int and artifact_id > 0 and type(expected_size) is int
                and 0 < expected_size <= MAX_ARCHIVE_BYTES and type(expected_digest) is str
                and re.fullmatch(r"sha256:[0-9a-f]{64}", expected_digest), "G5A_ROOT_ORACLE_ARTIFACT_REQUEST_BOUND")
        output = _oracle_path(output)
        require(not os.path.lexists(output) and output.parent.is_dir() and output.parent.stat().st_uid == os.geteuid()
                and stat.S_IMODE(output.parent.stat().st_mode) == 0o700, "G5A_ROOT_ORACLE_FRESH_OWN_ARTIFACT_DESTINATION_REQUIRED")
        filesystem = os.statvfs(output.parent)
        unit = filesystem.f_frsize or filesystem.f_bsize
        # Exact flat one-file sequential input capture, separate from the
        #512-MiB archive project. Keep the original overall4-GiB reserve and
        #10% free-inode reserve BEFORE any prerequisite ZIP write.
        physical_upper = 6 * (((expected_size + unit - 1) // unit + 37) * unit) + 128 * 1024**2
        require(unit == 4096 and filesystem.f_bavail * unit >= physical_upper + 4 * 1024**3
                and filesystem.f_files > 0 and filesystem.f_favail - 1 >= filesystem.f_files / 10,
                "G5A_PREREQUISITE_STAGING_ORIGINAL_RESERVE_OR_INODES_RED")
        query = self._query("DOWNLOAD", {"artifact_id": artifact_id, "expected_size": expected_size,
                                         "expected_digest": expected_digest}, deadline)
        descriptor, total, hasher, chunks = None, 0, hashlib.sha256(), 0
        try:
            begin = self._response(query, deadline)
            require(begin.get("kind") == "ARTIFACT_BEGIN" and begin.get("artifact_id") == artifact_id
                    and begin.get("expected_size") == expected_size and begin.get("expected_digest") == expected_digest,
                    "G5A_ROOT_ORACLE_ARTIFACT_BEGIN_REBOUND")
            descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            while True:
                response = self._response(query, deadline)
                if response.get("kind") == "ARTIFACT_END":
                    require(response.get("chunks") == chunks and response.get("bytes") == total == expected_size
                            and response.get("sha256") == hasher.hexdigest()
                            and "sha256:" + hasher.hexdigest() == expected_digest,
                            "G5A_ROOT_ORACLE_ARTIFACT_ACTUAL_FINAL_DIGEST_REBOUND")
                    break
                require(response.get("kind") == "ARTIFACT_CHUNK" and response.get("chunk_index") == chunks,
                        "G5A_ROOT_ORACLE_ARTIFACT_CHUNK_SEQUENCE_REBOUND")
                try: raw = base64.b64decode(response.get("bytes_base64", ""), validate=True)
                except (TypeError, ValueError) as error: raise ValueError("G5A_ROOT_ORACLE_ARTIFACT_CHUNK_ENCODING") from error
                require(0 < len(raw) <= 65536 and total + len(raw) <= expected_size,
                        "G5A_ROOT_ORACLE_ARTIFACT_CHUNK_BYTES_BOUND")
                offset = 0
                while offset < len(raw): offset += os.write(descriptor, raw[offset:])
                total += len(raw); hasher.update(raw); chunks += 1
            os.fsync(descriptor); self.sequence += 1
            return {"status": "GREEN", "bytes": total, "digest": expected_digest,
                "authorization_forwarded_to_redirect": False, "total_deadline_enforced": True,
                "transport": "SOURCE_FIXED_ROOT_FIFO_NO_BEARER_IN_WORKER"}
        except BaseException:
            if descriptor is not None: output.unlink(missing_ok=True)
            raise
        finally:
            if descriptor is not None: os.close(descriptor)

    def close(self, *, deadline=None):
        if self.closed: return
        deadline = time.monotonic() + 10 if deadline is None else deadline
        query = self._query("CLOSE", {}, deadline); response = self._response(query, deadline)
        require(response.get("kind") == "CLOSED", "G5A_ROOT_ORACLE_TRUSTED_CLOSE_ACK_REQUIRED")
        # Keep the broker origins alive while the worker acknowledges the
        #actual CLOSED response. The broker verifies the last fd's real closure
        #before unlinking either endpoint or installing its custody seal.
        os.close(self.fds[1])
        receipt = {"schema": "rc6.g5a-root-oracle-close-receipt.v2", "worker_birth": query["worker_birth"],
            **{key: query[key] for key in ("nonce", "sequence", "challenge", "oracle_metadata_sha256")}}
        _oracle_write(self.fds[0], components.canonical(receipt), deadline)
        os.close(self.fds[0])
        registered = _ORACLE_READERS.pop(id(self)); self.sequence += 1
        self.fds = []; self.closed = True
        _CLOSED_ORACLE_READERS[id(self)] = (self, os.getpid(), registered[2], registered[4], self.sequence)


class TrustedG5APreparation:
    """Same-process pre-Seal authentic documents; carries no bearer or native pass."""
    __slots__ = ("pid", "approval", "binding_wire", "manifest_wire", "verified_wire", "source_root", "reader", "accepted_utc")

    def __new__(cls, *args, **kwargs): raise ValueError("G5A_NATIVE_TRUSTED_PREPARATION_FACTORY_REQUIRED")
    def __setattr__(self, name, value): raise AttributeError("G5A_TRUSTED_PREPARATION_IMMUTABLE")


def prepare_g5a_trusted(*, trusted_controller, reader, approval_url, owner_session,
                        prerequisites_manifest, evidence_root, source_root):
    """Obtain owner acceptance and G0--G4 RAW before any privilege transition.

    Only the native FIFO client can supply provenance. Caller then closes the
    reader, awaits the parent's actual ancestor seal, sets/reads quota ROOT and
    drops privileges. The typed preparation is consumed in this same PID.
    """
    from scripts.rc6_contract_revisions import authenticate_revision, validate_revision_approval
    from scripts import rc6_architectural_gates as gates
    request, _ = _trusted_api(trusted_controller, role="worker")
    require(type(reader) is RootOwnerOracleReader and reader.controller is trusted_controller,
            "G5A_NATIVE_ROOT_ORACLE_READER_REQUIRED_BEFORE_ACCEPTANCE")
    reader._validate()
    binding = request["binding"]
    require(Path(source_root).absolute() == Path(request["source_root"]), "G5A_PREPARATION_EXECUTED_SOURCE_REBOUND")
    approval = authenticate_revision(reader, approval_url, owner_session=owner_session, revision=REVISION, scope=SCOPE,
        source_sha=request["source_sha"], source_tree=request["source_tree"], contract_sha256=CONTRACT_SHA256)
    require(approval.owner_session == binding["owner_id"], "G5A_PREPARATION_EXCLUSIVE_OWNER_REBOUND")
    verified = gates.verify_manifest(prerequisites_manifest, source_sha=request["source_sha"], source_tree=request["source_tree"],
        target_gate="G5", evidence_root=evidence_root, get=reader.request, download=reader.download)
    capability_reference = prerequisites_manifest.get("g5a_capability_reference")
    bootstrap_reference = prerequisites_manifest.get("g5a_bootstrap_reference")
    require(type(capability_reference) is dict and type(bootstrap_reference) is dict
        and all(type(reference.get("run_id")) is int and reference["run_id"] > 0
                for reference in (capability_reference, bootstrap_reference)),
        "G5A_ACTUAL_CAPABILITY_ACCEPTANCE_AND_ORIGINAL_BOOTSTRAP_REFERENCES_REQUIRED_BEFORE_QUOTA_SETUP")
    runs = [reader.request("/actions/runs/" + str(reference["run_id"]))
            for reference in (capability_reference, bootstrap_reference)]
    branch = runs[0].get("head_branch")
    require(type(branch) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,199}", branch)
        and branch != "main" and ".." not in branch and all(part not in ("", ".", "..") for part in branch.split("/"))
        and all(run.get("head_branch") == branch and run.get("head_sha") == request["source_sha"] for run in runs),
        "G5A_CAPABILITY_BOOTSTRAP_ACTUAL_SAME_SOURCE_BRANCH_REQUIRED")
    reference = reader.request("/git/ref/heads/" + branch)
    commit = reader.request("/git/commits/" + request["source_sha"])
    require(reference.get("object", {}).get("type") == "commit"
        and reference.get("object", {}).get("sha") == request["source_sha"]
        and commit.get("sha") == request["source_sha"] and commit.get("tree", {}).get("sha") == request["source_tree"],
        "G5A_CAPABILITY_BOOTSTRAP_CURRENT_GITHUB_SOURCE_AND_TREE_REBOUND")
    pair_root = Path(evidence_root) / "g5a-native-capacity-pair"
    pair = gates.verify_capacity_bootstrap_artifact(bootstrap_reference, capability_reference=capability_reference,
        source_sha=request["source_sha"], source_tree=request["source_tree"],
        read_contract_sha256=gates.productive_contract(Path(request["source_repo"])),
        evidence_root=pair_root, get=reader.request, download=reader.download,
        authenticated_branch=branch, source_repository=Path(request["source_repo"]))
    verified["g5a_native_capacity_pair"] = {"authenticated_branch": branch,
        "capability_reference": capability_reference, "bootstrap_reference": bootstrap_reference,
        "capability_receipt_sha256": components.sha(pair["verified_capability_prerequisite"]["receipt_raw"]),
        "bootstrap_receipt_sha256": components.sha(pair["receipt_raw"]), "qualification_claimed": False}
    # The captures are input evidence, not the future release artifact. Exact
    # inode/content custody is checked again after the ROOT/NONROOT transition.
    captures = {}
    archive_paths = {details["archive"] for details in verified["evidence_files"].values()}
    archive_paths.update(str(pair_root / phase / "capability.zip") for phase in ("capability", "bootstrap"))
    input_directories = set()
    for archive in sorted(archive_paths):
        path = _oracle_path(archive); info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == info.st_gid == 0
                and stat.S_IMODE(info.st_mode) == 0o600,
                "G5A_TRUSTED_INPUT_ARTIFACT_ROOT_CUSTODY_REQUIRED")
        # The worker must reread these exact already-authenticated inputs after
        #Seal without a privileged descriptor. Transfer only its own known
        #captures and record the final immutable inode identity.
        os.chmod(path, 0o400, follow_symlinks=False)
        os.chown(path, request["owner_uid"], request["owner_gid"], follow_symlinks=False)
        info = path.lstat()
        captures[str(path)] = {"identity": list(_identity(info)), "bytes": info.st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        directory = path.parent
        while directory.is_relative_to(Path(evidence_root)):
            input_directories.add(directory)
            if directory == Path(evidence_root): break
            directory = directory.parent
    verified["root_capture_inventory"] = captures
    for directory in sorted(input_directories, key=lambda value: len(value.parts), reverse=True):
        require(stat.S_ISDIR(directory.lstat().st_mode) and not directory.is_symlink()
            and stat.S_IMODE(directory.lstat().st_mode) == 0o700, "G5A_TRUSTED_INPUT_DIRECTORY_CUSTODY_REQUIRED")
        os.chown(directory, request["owner_uid"], request["owner_gid"], follow_symlinks=False)
    accepted = datetime.now(timezone.utc)
    validate_revision_approval(approval, revision=REVISION, scope=SCOPE, source_sha=request["source_sha"],
                              source_tree=request["source_tree"], contract_sha256=CONTRACT_SHA256, now=accepted)
    result = object.__new__(TrustedG5APreparation)
    fields = {"pid": os.getpid(), "approval": approval, "binding_wire": components.canonical(binding),
        "manifest_wire": components.canonical(prerequisites_manifest), "verified_wire": components.canonical(verified),
        "source_root": str(Path(source_root).absolute()), "reader": reader, "accepted_utc": accepted.isoformat()}
    for name, value in fields.items(): object.__setattr__(result, name, value)
    _PREPARATIONS[id(result)] = (result, fields)
    return result


def validate_trusted_g5a_preparation(preparation, *, binding, prerequisites_manifest, continuation=None):
    from scripts.rc6_contract_revisions import validate_revision_approval
    registered = _PREPARATIONS.get(id(preparation))
    require(type(preparation) is TrustedG5APreparation and registered is not None
            and registered[0] is preparation and registered[1]["pid"] == os.getpid()
            and all(getattr(preparation, name) == value for name, value in registered[1].items())
            and preparation.binding_wire == components.canonical(binding)
            and preparation.manifest_wire == components.canonical(prerequisites_manifest),
            "G5A_CURRENT_TYPED_TRUSTED_PREPARATION_REQUIRED")
    validate_revision_approval(preparation.approval, revision=REVISION, scope=SCOPE,
        source_sha=binding["candidate_sha"], source_tree=binding["candidate_tree"], contract_sha256=CONTRACT_SHA256,
        now=None if continuation is None else datetime.fromisoformat(preparation.accepted_utc))
    if continuation is not None:
        from scripts.rc6_owned_g5a_continuation import validate_continuation
        validate_continuation(continuation, binding=binding)
    reader = preparation.reader
    require(reader.closed and _CLOSED_ORACLE_READERS.get(id(reader)) == (
        reader, os.getpid(), reader.controller, components.canonical(reader.metadata), reader.sequence),
        "G5A_ROOT_ORACLE_CLOSED_BEFORE_NONROOT_REQUIRED")
    verified = components.loads(preparation.verified_wire)
    for path, record in verified["root_capture_inventory"].items():
        info = _oracle_path(path).lstat()
        require(list(_identity(info)) == record["identity"] and info.st_size == record["bytes"]
                and components.sha(Path(path).read_bytes()) == record["sha256"],
                "G5A_TRUSTED_PREREQUISITE_INPUT_BYTES_OR_CUSTODY_REBOUND")
    return verified


def _mount_id(descriptor):
    fields = dict(line.split(":", 1) for line in
                  Path("/proc/self/fdinfo/" + str(descriptor)).read_text().splitlines() if ":" in line)
    result = int(fields.get("mnt_id", "0").strip())
    require(result > 0, "G5A_NATIVE_QUOTA_MOUNT_ID_REQUIRED")
    return result


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode, info.st_nlink)


def _project_id(descriptor):
    raw = bytearray(28)
    fcntl.ioctl(descriptor, FSGETXATTR, raw, True)
    flags, _, _, project, _, _ = struct.unpack("=IIIII8s", raw)
    require(flags & PROJINHERIT and project > 0, "G5A_NATIVE_PROJINHERIT_REQUIRED")
    return project


class _Dqblk(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ("bhard", "bsoft", "space", "ihard", "isoft", "inodes", "btime", "itime")] + [
                    ("valid", ctypes.c_uint32)]


def _quota_readback(device, project_id, *, hard_bytes=CAP_BYTES, hard_inodes=CAP_INODES):
    """READ_ONLY native quotactl, saving errno before any other operation."""
    library = ctypes.CDLL(None, use_errno=True)
    library.quotactl.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
    records = []
    format_id, quota = ctypes.c_uint32(), _Dqblk()
    for command, payload, name in ((Q_GETFMT, format_id, "Q_GETFMT"), (Q_GETQUOTA, quota, "Q_GETQUOTA")):
        ctypes.set_errno(0)
        returned = library.quotactl((command << 8) | PRJQUOTA, os.fsencode(device), project_id,
                                   ctypes.byref(payload))
        actual_errno = ctypes.get_errno()
        record = {"operation": name, "return": returned, "errno": actual_errno,
                  "errno_name": errno.errorcode.get(actual_errno, "SUCCESS"), "project_id": project_id}
        records.append(record)
        if returned != 0:
            error = ValueError("G5A_NATIVE_" + name + "_FAILED")
            error.details = record
            raise error
    # A non-observed kernel module is not used as a compatibility predicate.
    require(format_id.value > 0 and quota.bhard * 1024 == quota.bsoft * 1024 == hard_bytes
            and quota.ihard == quota.isoft == hard_inodes,
            "G5A_NATIVE_HARD_QUOTA_NOT_ORIGINAL_512MIB_32768")
    return {"format_id": format_id.value, "hard_bytes": quota.bhard * 1024,
            "hard_inodes": quota.ihard, "used_bytes": quota.space, "used_inodes": quota.inodes,
            "native_operations": records}


def decode_native_ext4_geometry(raw):
    """Decode actual owned-device bytes for the narrow supported envelope.

    Linux ext4 extent trees have at most five index levels. A sequential new
    file needs at most one leaf record per allocated data block; charging five
    full index blocks per data block is a deliberately conservative upper
    bound. An insertion into a supported htree allocates at most one leaf plus
    its bounded index path; charging six such blocks and six extent blocks for
    each is conservative. Existing directory/extent blocks are already in the
    measured allocation. No compression, future GC, deleted-name reuse or
    expected data similarity appears in this argument.

    Inline data, bigalloc, large_dir, encryption and casefold require different
    proofs and are rejected here; rejection is a predicate on actual geometry,
    never an inference from an unobserved kernel module.
    """
    require(type(raw) is bytes and len(raw) == 1024, "G5A_ACTUAL_EXT4_SUPERBLOCK_REQUIRED")
    number = lambda offset: struct.unpack_from("<I", raw, offset)[0]
    compat, incompat, readonly = number(0x5C), number(0x60), number(0x64)
    require(struct.unpack_from("<H", raw, 0x38)[0] == 0xEF53 and number(0x18) == 2
            and number(0x1C) == 2 and incompat & 0x40 and readonly & 0x100 and readonly & 0x2000
            and not compat & 0x1 and not incompat & (0x4000 | 0x8000 | 0x10000 | 0x20000)
            and not readonly & 0x200, "G5A_CURRENT_EXT4_GEOMETRY_NEEDS_DISTINCT_BOUND")
    return {"schema": "rc6.g5a-native-ext4-geometry.v2", "raw_base64": base64.b64encode(raw).decode(),
        "raw_sha256": components.sha(raw), "filesystem_magic": 0xEF53, "block_bytes": 4096,
        "compat_features": compat, "incompat_features": incompat, "readonly_features": readonly,
        "maximum_extent_index_depth": 5, "maximum_directory_index_path_blocks": 5,
        "maximum_extent_data_blocks": 32768,
        "physical_bound_scope": "SUPPORTED_EXT4_SEQUENTIAL_NEW_FILE_OPERATIONS_NOT_HORIZON_CLOSURE"}


def _native_ext4_geometry(device):
    descriptor = os.open(device, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        require(stat.S_ISBLK(os.fstat(descriptor).st_mode), "G5A_OWN_QUOTA_DEVICE_NOT_BLOCK")
        raw = os.pread(descriptor, 1024, 1024)
        return decode_native_ext4_geometry(raw)
    finally:
        os.close(descriptor)


class NativeProjectQuota:
    """An actual opened, read-back quota. JSON cannot reconstruct this object.

    TrustedQuotaSetup captures quota and closes its directory descriptors before
   Seal. After the original0..2 descriptor check, NONROOT reopens only its two
   own directories. No device/image/ROOT/API descriptor crosses the transition.
    """
    def __init__(self, authority, *, path, descriptor, project_id, readback, binding,
                 probe_path=None, probe_descriptor=None, probe_project_id=None, probe_readback=None, geometry=None):
        require(authority is _ISSUER, "G5A_SERIALIZED_OR_SYNTHETIC_QUOTA_FORBIDDEN")
        self.path, self.descriptor, self.project_id = path, descriptor, project_id
        self.readback_wire, self.binding_wire = components.canonical(readback), components.canonical(binding)
        self.identity, self.mount_id = _identity(os.fstat(descriptor)), _mount_id(descriptor)
        self.mount_namespace_inode = os.stat("/proc/self/ns/mnt").st_ino
        self.boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        self.pid = os.getpid()
        self.probe_path, self.probe_descriptor, self.probe_project_id = probe_path, probe_descriptor, probe_project_id
        self.probe_identity = _identity(os.fstat(probe_descriptor))
        self.probe_readback_wire = components.canonical(probe_readback)
        self.geometry_wire = components.canonical(geometry)
        _HANDLES[id(self)] = (self, self.pid, self.descriptor, self.path, self.identity, self.mount_id,
                             self.project_id, self.readback_wire, self.binding_wire,
                             self.mount_namespace_inode, self.boot_id, self.probe_path, self.probe_descriptor,
                             self.probe_project_id, self.probe_identity, self.probe_readback_wire, self.geometry_wire)

    def close(self):
        registered = _HANDLES.pop(id(self), None)
        if registered is not None:
            os.close(registered[2])
            os.close(registered[12])
            _QUOTA_PROOFS.pop(id(self), None)
        _CLOSED_QUOTA_PREPARATIONS.pop(id(self), None)
        _QUOTA_DESCRIPTOR_PROOFS.pop(id(self), None)

    def close_before_seal(self):
        require(os.getresuid() == (0, 0, 0), "G5A_QUOTA_PREPARATION_CLOSE_REQUIRES_TRUSTED_ROOT")
        self.snapshot(binding=components.loads(self.binding_wire))
        registered = _HANDLES.pop(id(self))
        _CLOSED_QUOTA_PREPARATIONS[id(self)] = registered
        os.close(registered[2]); os.close(registered[12])
        self.descriptor = self.probe_descriptor = None

    def reopen_after_seal(self, *, binding, filter_binding):
        from scripts import rc6_private_pid_boundary as pid_boundary
        require_sealed_nonroot(source_sha=binding["candidate_sha"], source_tree=binding["candidate_tree"],
                              filter_binding=filter_binding)
        registered = _CLOSED_QUOTA_PREPARATIONS.get(id(self))
        require(registered is not None and registered[0] is self and registered[1] == os.getpid()
                and registered[8] == components.canonical(binding) and self.descriptor is self.probe_descriptor is None
                and registered[3:12] == (self.path, self.identity, self.mount_id, self.project_id, self.readback_wire,
                    self.binding_wire, self.mount_namespace_inode, self.boot_id, self.probe_path)
                and registered[13:] == (self.probe_project_id, self.probe_identity, self.probe_readback_wire, self.geometry_wire),
                "G5A_SAME_PID_NATIVE_QUOTA_PREPARATION_REQUIRED")
        baseline = pid_boundary.validate_descriptors(pid_boundary.descriptors())
        archive_descriptor = probe_descriptor = None
        try:
            archive_descriptor = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            probe_descriptor = os.open(self.probe_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            require(_identity(os.fstat(archive_descriptor)) == self.identity
                    and _identity(os.fstat(probe_descriptor)) == self.probe_identity
                    and self.identity[2:4] == self.probe_identity[2:4] == (os.getuid(), os.getgid())
                    and _mount_id(archive_descriptor) == _mount_id(probe_descriptor) == self.mount_id
                    and _project_id(archive_descriptor) == self.project_id
                    and _project_id(probe_descriptor) == self.probe_project_id,
                    "G5A_NONROOT_QUOTA_DIRECTORY_REOPEN_REBOUND")
            self.descriptor, self.probe_descriptor = archive_descriptor, probe_descriptor
            renewed = list(registered); renewed[2], renewed[12] = archive_descriptor, probe_descriptor
            _HANDLES[id(self)] = tuple(renewed)
            _QUOTA_DESCRIPTOR_PROOFS[id(self)] = (self, os.getpid(), components.canonical(binding),
                components.canonical(filter_binding), components.canonical(baseline))
            self.snapshot(binding=binding)
            require_g5a_descriptor_composition(self, binding=binding, filter_binding=filter_binding)
            del _CLOSED_QUOTA_PREPARATIONS[id(self)]
            return self
        except BaseException:
            _HANDLES.pop(id(self), None); _QUOTA_DESCRIPTOR_PROOFS.pop(id(self), None)
            for descriptor in (archive_descriptor, probe_descriptor):
                if descriptor is not None: os.close(descriptor)
            self.descriptor = self.probe_descriptor = None
            raise

    def snapshot(self, *, binding):
        registered = _HANDLES.get(id(self))
        require(registered is not None and registered == (self, os.getpid(), self.descriptor, self.path,
                self.identity, self.mount_id, self.project_id, self.readback_wire, self.binding_wire,
                self.mount_namespace_inode, self.boot_id, self.probe_path, self.probe_descriptor,
                self.probe_project_id, self.probe_identity, self.probe_readback_wire, self.geometry_wire)
                and self.binding_wire == components.canonical(binding),
                "G5A_ACTUAL_IN_PROCESS_QUOTA_HANDLE_REQUIRED")
        require(not any(item.is_symlink() for item in (self.path, *self.path.parents))
                and _identity(os.fstat(self.descriptor)) == _identity(self.path.lstat()) == self.identity
                and _mount_id(self.descriptor) == self.mount_id
                and os.stat("/proc/self/ns/mnt").st_ino == self.mount_namespace_inode
                and Path("/proc/sys/kernel/random/boot_id").read_text().strip() == self.boot_id
                and _project_id(self.descriptor) == self.project_id,
                "G5A_LIVE_QUOTA_PATH_PROJECT_OR_MOUNT_REBOUND")
        observed = os.fstatvfs(self.descriptor)
        require(observed.f_frsize > 0 and observed.f_blocks * observed.f_frsize == CAP_BYTES
                and observed.f_files == CAP_INODES and 0 <= observed.f_bavail <= observed.f_blocks
                and 0 <= observed.f_ffree <= observed.f_files,
                "G5A_LIVE_PROJECT_QUOTA_PROJECTION_REBOUND")
        allocated, count, names = os.fstat(self.descriptor).st_blocks * 512, 0, []
        from rc6_shadow_runtime.archive_namespace import is_archive_member, is_archive_temporary
        for name in os.listdir(self.descriptor):
            require(is_archive_member(name) or is_archive_temporary(name), "G5A_NATIVE_ALLOCATION_FOREIGN_MEMBER")
            info = os.stat(name, dir_fd=self.descriptor, follow_symlinks=False)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_dev == self.identity[0]
                    and info.st_uid == self.identity[2] and info.st_gid == self.identity[3]
                    and stat.S_IMODE(info.st_mode) == 0o600, "G5A_NATIVE_ALLOCATION_ALIAS_OR_CUSTODY_CHANGED")
            count += 1; allocated += info.st_blocks * 512
            require(count <= CAP_INODES, "G5A_NATIVE_INODE_BUDGET_RED")
            names.append((name, info.st_ino, info.st_size, info.st_blocks))
        charged = CAP_BYTES - observed.f_bavail * observed.f_frsize
        require(allocated <= charged <= CAP_BYTES and count + 1 <= CAP_INODES - observed.f_ffree,
                "G5A_ACTUAL_BLOCKS_OR_INODES_EXCEED_CURRENT_QUOTA_PROJECTION")
        return {"hard_bytes": CAP_BYTES, "hard_inodes": CAP_INODES,
                "used_bytes": charged,
                "used_inodes": CAP_INODES - observed.f_ffree,
                "allocation_unit": observed.f_frsize, "project_id": self.project_id,
                "quota_format_id": components.loads(self.readback_wire)["format_id"],
                "mount_id": self.mount_id, "directory_device": self.identity[0],
                "directory_inode": self.identity[1], "native_initial_readback_sha256": components.sha(self.readback_wire),
                "physical_allocated_bytes_including_directory": allocated, "actual_member_count": count,
                "actual_allocation_inventory_sha256": components.sha(components.canonical(sorted(names))),
                "measurement_method": "LIVE_SEALED_PROJINHERIT_STATVFS_NOT_A_SAMPLED_PEAK"}

    def require_enforcement_proof(self, *, binding, filter_binding):
        self.snapshot(binding=binding)
        record = _QUOTA_PROOFS.get(id(self))
        require(record is not None and record[0] is self and record[1] == os.getpid()
                and record[2] == components.canonical(binding) and record[3] == components.canonical(filter_binding),
                "G5A_ACTUAL_SAME_FILESYSTEM_EDQUOT_AND_MUTATION_PROOF_REQUIRED")
        return components.loads(record[4])


def open_native_project_quota(archive_path, *, device, binding, probe_path):
    """TrustedQuotaSetup READ_ONLY confirmation of an already configured project."""
    quota_setup_request(binding, archive_path=archive_path)
    require(os.getresuid() == (0, 0, 0), "G5A_QUOTA_READBACK_REQUIRES_TRUSTED_ROOT_SETUP")
    path = Path(archive_path).absolute()
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    probe_descriptor = None
    try:
        info = os.fstat(descriptor)
        device_info = Path(device).lstat()
        require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700
                and stat.S_ISBLK(device_info.st_mode) and not Path(device).is_symlink()
                and info.st_dev == device_info.st_rdev,
                "G5A_QUOTA_FILESYSTEM_DEVICE_CUSTODY_REBOUND")
        project = _project_id(descriptor)
        readback = _quota_readback(device, project)
        geometry = _native_ext4_geometry(device)
        probe = Path(probe_path).absolute()
        require(not any(item.is_symlink() for item in (probe, *probe.parents)) and probe != path,
                "G5A_OWN_NATIVE_QUOTA_PROBE_ALIAS_FORBIDDEN")
        probe_descriptor = os.open(probe, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        probe_info = os.fstat(probe_descriptor)
        require(probe_info.st_dev == info.st_dev and probe_info.st_uid == info.st_uid
                and probe_info.st_gid == info.st_gid and stat.S_IMODE(probe_info.st_mode) == 0o700
                and _mount_id(probe_descriptor) == _mount_id(descriptor) and not os.listdir(probe_descriptor),
                "G5A_QUOTA_PROBE_SAME_OWNED_FILESYSTEM_REQUIRED")
        probe_project = _project_id(probe_descriptor)
        require(probe_project != project, "G5A_NATIVE_PROBE_MUST_USE_SEPARATE_1MIB_PROJECT")
        probe_readback = _quota_readback(device, probe_project, hard_bytes=1024**2)
        result = NativeProjectQuota(_ISSUER, path=path, descriptor=descriptor, project_id=project,
            readback=readback, binding=binding, probe_path=probe, probe_descriptor=probe_descriptor,
            probe_project_id=probe_project, probe_readback=probe_readback, geometry=geometry)
        result.snapshot(binding=binding)
        return result
    except BaseException:
        _HANDLES.pop(id(locals().get("result")), None)
        os.close(descriptor)
        if probe_descriptor is not None:
            os.close(probe_descriptor)
        raise


def require_g5a_descriptor_composition(quota, *, binding, filter_binding):
    """Only two newly opened NONROOT-owned quota directories extend0..2.

    This explicit composition is not a waiver for inherited privileged FDs.
   The base0..2 evidence was observed afterSeal and before either directory was
   opened. Device, raw image, external proc, ROOT log or API handles are rejected.
    """
    from scripts import rc6_private_pid_boundary as pid_boundary
    require(type(quota) is NativeProjectQuota, "G5A_NATIVE_QUOTA_FD_COMPOSITION_REQUIRED")
    proof = _QUOTA_DESCRIPTOR_PROOFS.get(id(quota))
    require(proof is not None and proof[:4] == (quota, os.getpid(), components.canonical(binding),
        components.canonical(filter_binding)), "G5A_BASE_STDIO_CHECK_BEFORE_OWN_QUOTA_REOPEN_REQUIRED")
    quota.snapshot(binding=binding)
    rows = pid_boundary.descriptors()
    base = pid_boundary.validate_descriptors([row for row in rows if row["descriptor"] in (0, 1, 2)])
    require(components.canonical(base) == proof[4] and {row["descriptor"] for row in rows}
            == {0, 1, 2, quota.descriptor, quota.probe_descriptor} and len(rows) == 5,
            "G5A_UNEXPECTED_INHERITED_OR_ACTIVE_PRIVILEGED_DESCRIPTOR")
    owned = []
    for descriptor, path, identity, project in (
        (quota.descriptor, quota.path, quota.identity, quota.project_id),
        (quota.probe_descriptor, quota.probe_path, quota.probe_identity, quota.probe_project_id)):
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        require(flags & os.O_ACCMODE == os.O_RDONLY and not flags & os.O_PATH
                and flags & os.O_DIRECTORY and fcntl.fcntl(descriptor, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
                and _identity(os.fstat(descriptor)) == _identity(path.lstat()) == identity
                and identity[2:4] == (os.getuid(), os.getgid()) and stat.S_IMODE(identity[4]) == 0o700
                and _mount_id(descriptor) == quota.mount_id and _project_id(descriptor) == project,
                "G5A_EXTRA_FD_NOT_EXACT_READONLY_NONROOT_OWN_QUOTA_DIRECTORY")
        owned.append({"descriptor": descriptor, "path": str(path), "identity": list(identity),
                      "mount_id": quota.mount_id, "project_id": project, "open_flags": flags})
    return {"schema": "rc6.g5a-native-own-quota-fd-composition.v2", "binding": dict(binding),
        "filter_binding": dict(filter_binding), "baseline_after_seal_before_own_open": base,
        "current_descriptors": rows, "owned_readonly_directories": owned,
        "privileged_bridge_retained": False, "qualification_claimed": False}


class NativeWorkloadQuota:
    """Actual original20-GiB/100000-inode scope, with fresh total physical census.

    Project quotas do not aggregate subprojects. The census explicitly includes
   the512-MiB archive and the1-MiB probe; their bytes never become uncharged RAW
   credit. project/control share the original project ID observed ROOT.
    """
    def __init__(self, issuer, *, roots, probe_path, identities, mount_id, project_id, readback, binding, geometry):
        require(issuer is _ISSUER, "G5A_NATIVE_ORIGINAL_WORKLOAD_QUOTA_FACTORY_REQUIRED")
        self.roots, self.probe_path, self.identities = tuple(roots), probe_path, tuple(identities)
        self.mount_id, self.project_id, self.pid = mount_id, project_id, os.getpid()
        self.readback_wire, self.binding_wire = components.canonical(readback), components.canonical(binding)
        self.geometry_wire = components.canonical(geometry)
        _WORKLOAD_QUOTAS[id(self)] = (self, self.pid, self.roots, self.probe_path, self.identities,
            self.mount_id, self.project_id, self.readback_wire, self.binding_wire, self.geometry_wire)

    def snapshot(self, *, binding):
        require(_WORKLOAD_QUOTAS.get(id(self)) == (self, os.getpid(), self.roots, self.probe_path, self.identities,
            self.mount_id, self.project_id, self.readback_wire, self.binding_wire, self.geometry_wire)
            and self.binding_wire == components.canonical(binding), "G5A_CURRENT_NATIVE_ORIGINAL_WORKLOAD_QUOTA_REQUIRED")
        require(os.getuid() > 0, "G5A_ORIGINAL_WORKLOAD_CENSUS_NONROOT_REQUIRED")
        total, entries, seen = 0, 0, set()
        for root, expected in zip((*self.roots, self.probe_path), self.identities):
            require(_identity(_oracle_path(root).lstat()) == expected and expected[2:4] == (os.getuid(), os.getgid()),
                    "G5A_ORIGINAL_WORKLOAD_ROOT_CUSTODY_REBOUND")
            queue = [root]
            while queue:
                path = queue.pop(); details = path.lstat(); ident = (details.st_dev, details.st_ino)
                require(not path.is_symlink() and details.st_dev == expected[0] and details.st_uid == os.getuid()
                        and (stat.S_ISDIR(details.st_mode) or stat.S_ISREG(details.st_mode) and details.st_nlink == 1)
                        and ident not in seen, "G5A_ORIGINAL_WORKLOAD_ALIAS_FOREIGN_OR_DUPLICATE_INODE")
                seen.add(ident); entries += 1; total += details.st_blocks * 512
                require(entries <= 100000 and total <= 20 * 1024**3, "G5A_ORIGINAL20GIB_100000_PHYSICAL_BUDGET_RED")
                if stat.S_ISDIR(details.st_mode):
                    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NOATIME)
                    try:
                        require(_identity(os.fstat(descriptor)) == _identity(details), "G5A_ORIGINAL_WORKLOAD_CENSUS_RACE")
                        queue.extend(path / name for name in os.listdir(descriptor))
                    finally: os.close(descriptor)
        filesystem = os.statvfs(self.roots[1])
        require(filesystem.f_frsize == 4096 and filesystem.f_blocks * filesystem.f_frsize == 20 * 1024**3
                and filesystem.f_files == 100000, "G5A_ORIGINAL_WORKLOAD_LIVE_QUOTA_PROJECTION_REBOUND")
        return {"schema": "rc6.g5a-original-workload-current-physical.v2", "physical_allocated_bytes": total,
            "physical_entries": entries, "hard_bytes": 20 * 1024**3, "hard_inodes": 100000,
            "free_bytes": filesystem.f_bavail * filesystem.f_frsize, "free_inodes": filesystem.f_favail,
            "total_inodes": filesystem.f_files, "allocation_unit": filesystem.f_frsize,
            "project_id": self.project_id, "mount_id": self.mount_id,
            "native_initial_readback_sha256": components.sha(self.readback_wire),
            "archive_and_probe_subprojects_included_physically": True, "future_gc_credit_bytes": 0}

    def before_raw_publication(self, *, path, additional_bytes, additional_inodes, binding):
        require(type(additional_bytes) is int and additional_bytes >= 0 and type(additional_inodes) is int
                and additional_inodes >= 0 and Path(path).absolute().is_relative_to(self.roots[1]),
                "G5A_RAW_CAPTURE_SOURCE_OWN_CONTROL_AND_KNOWN_BYTES_REQUIRED")
        geometry = components.loads(self.geometry_wire); unit = geometry["block_bytes"]
        physical_upper = 6 * (((additional_bytes + unit - 1) // unit + 37 * additional_inodes) * unit) + 128 * 1024**2
        return self.reserve_physical(path=path, physical_upper=physical_upper, additional_inodes=additional_inodes,
            binding=binding, known_encoded_bytes=additional_bytes)

    def reserve_physical(self, *, path, physical_upper, additional_inodes, binding, known_encoded_bytes):
        require(type(physical_upper) is int and physical_upper >= 0 and type(additional_inodes) is int
            and additional_inodes >= 0 and type(known_encoded_bytes) is int and known_encoded_bytes >= 0
            and any(Path(path).absolute().is_relative_to(root) for root in self.roots),
            "G5A_PHYSICAL_PUBLICATION_ORIGINAL_OWNED_ROOT_AND_KNOWN_BOUND_REQUIRED")
        current = self.snapshot(binding=binding)
        require(current["physical_allocated_bytes"] + physical_upper <= current["hard_bytes"]
                and current["physical_entries"] + additional_inodes <= current["hard_inodes"]
                and current["free_bytes"] >= physical_upper + 4 * 1024**3
                and current["free_inodes"] - additional_inodes >= current["total_inodes"] / 10,
                "G5A_RAW_CAPTURE_ORIGINAL20GIB_RESERVE4GIB_INODES10PERCENT_RED")
        require(components.loads(self.geometry_wire)["block_bytes"] == current["allocation_unit"],
                "G5A_RAW_CAPTURE_NATIVE_FILESYSTEM_GEOMETRY_REBOUND")
        return {"schema": "rc6.g5a-original-raw-physical-reservation.v2", "current_allocation": current,
            "known_encoded_bytes": known_encoded_bytes, "additional_inodes": additional_inodes,
            "simultaneous_physical_upper_bytes": physical_upper, "future_gc_credit_bytes": 0,
            "qualification_claimed": False}


def observe_root_workload_quota(*, root, control_root, probe_path, device, binding):
    """ROOT read-back of the original workload, no retained ROOT descriptors."""
    require(os.getresuid() == (0, 0, 0), "G5A_ORIGINAL_WORKLOAD_QUOTA_TRUSTED_ROOT_REQUIRED")
    roots, probe = (_oracle_path(root), _oracle_path(control_root)), _oracle_path(probe_path)
    require(len({*roots, probe}) == 3 and all(not left.is_relative_to(right) for left in (*roots, probe)
            for right in (*roots, probe) if left != right), "G5A_ORIGINAL_WORKLOAD_PHYSICAL_ROOTS_MUST_NOT_OVERLAP")
    descriptors = []
    try:
        for path in (*roots, probe):
            descriptors.append(os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC))
        identities = [_identity(os.fstat(descriptor)) for descriptor in descriptors]
        require(all(item[0] == identities[0][0] and item[2:4] == identities[0][2:4]
                    and item[2] > 0 and stat.S_IMODE(item[4]) == 0o700 for item in identities)
                and all(_mount_id(descriptor) == _mount_id(descriptors[0]) for descriptor in descriptors),
                "G5A_ORIGINAL_WORKLOAD_NATIVE_DEVICE_OWNER_MOUNT_REQUIRED")
        project_id = _project_id(descriptors[0])
        require(project_id == _project_id(descriptors[1]) and _project_id(descriptors[2]) != project_id,
                "G5A_PROJECT_CONTROL_NATIVE_SHARED_ORIGINAL_PROJECT_REQUIRED")
        readback = _quota_readback(device, project_id, hard_bytes=20 * 1024**3, hard_inodes=100000)
        geometry = _native_ext4_geometry(device)
        return NativeWorkloadQuota(_ISSUER, roots=roots, probe_path=probe, identities=identities,
            mount_id=_mount_id(descriptors[0]), project_id=project_id, readback=readback, binding=binding, geometry=geometry)
    finally:
        for descriptor in descriptors: os.close(descriptor)


def prove_native_quota_enforcement(quota, *, binding, filter_binding):
    """A bounded, actual NONROOT 1-MiB EDQUOT and sealed mutation attempt.

    It uses only its fresh own probe inode. Failed native bytes remain for
    authenticated FIN/custody; there is no retry or substitution of errno.
    """
    require(type(quota) is NativeProjectQuota, "G5A_LIVE_PHYSICAL_QUOTA_HANDLE_REQUIRED")
    quota.snapshot(binding=binding)
    seal = require_sealed_nonroot(source_sha=binding["candidate_sha"], source_tree=binding["candidate_tree"],
                                 filter_binding=filter_binding)
    require(_identity(os.fstat(quota.probe_descriptor)) == _identity(quota.probe_path.lstat()) == quota.probe_identity
            and _project_id(quota.probe_descriptor) == quota.probe_project_id
            and _mount_id(quota.probe_descriptor) == quota.mount_id,
            "G5A_CURRENT_NATIVE_PROBE_PROJECT_OR_MOUNT_REBOUND")
    before = os.fstatvfs(quota.probe_descriptor)
    require(before.f_blocks * before.f_frsize == 1024**2 and before.f_files == CAP_INODES,
            "G5A_CURRENT_NATIVE_PROBE_1MIB_HARD_CAP_REQUIRED")
    name = "g5a-native-edquot-" + os.urandom(16).hex() + ".bin"
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=quota.probe_descriptor)
    written, denial = 0, None
    try:
        while written <= 1024**2:
            try:
                count = os.write(descriptor, bytes(65536))
            except OSError as error:
                denial = {"operation": "write", "errno": error.errno, "errno_name": errno.errorcode.get(error.errno),
                          "bytes_written_before_denial": written}
                require(error.errno == errno.EDQUOT, "G5A_NATIVE_QUOTA_FAILED_WITHOUT_EDQUOT")
                break
            require(count > 0, "G5A_NATIVE_QUOTA_ZERO_LENGTH_WRITE")
            written += count
        require(denial is not None, "G5A_NATIVE_1MIB_QUOTA_NOT_ENFORCED")
        os.fsync(descriptor)
        info = os.fstat(descriptor)
        require(info.st_size == written and info.st_blocks * 512 <= 1024**2,
                "G5A_NATIVE_QUOTA_PROBE_ALLOCATION_EXCEEDED")
        xattr = bytearray(28)
        fcntl.ioctl(descriptor, FSGETXATTR, xattr, True)
        try:
            fcntl.ioctl(descriptor, 0x401C5820, xattr)
        except OSError as error:
            project_mutation = {"operation": "FSSETXATTR", "errno": error.errno}
            require(error.errno == errno.EPERM, "G5A_NATIVE_PROJECT_MUTATION_NOT_DENIED")
        else:
            raise ValueError("G5A_NATIVE_PROJECT_MUTATION_ALLOWED")
    finally:
        os.close(descriptor)
    library = ctypes.CDLL(None, use_errno=True)
    value = _Dqblk()
    ctypes.set_errno(0)
    returned = library.quotactl((0x800008 << 8) | PRJQUOTA, b"/dev/g5a-quota-mutation-denied", quota.project_id,
                               ctypes.byref(value))
    actual_errno = ctypes.get_errno()
    require(returned == -1 and actual_errno == errno.EPERM, "G5A_NATIVE_QUOTA_MUTATION_NOT_DENIED")
    record = {"schema": "rc6.g5a-native-quota-enforcement.v2", "binding": dict(binding),
        "quota_initial_readback_sha256": components.sha(quota.readback_wire),
        "probe_initial_readback_sha256": components.sha(quota.probe_readback_wire), "probe_name": name,
        "probe_project_id": quota.probe_project_id, "probe_allocated_bytes": info.st_blocks * 512,
        "directory_device": quota.identity[0], "directory_inode": quota.identity[1],
        "probe_device": quota.probe_identity[0], "probe_inode": quota.probe_identity[1], "mount_id": quota.mount_id,
        "native_edquot": denial, "project_mutation": project_mutation,
        "quota_mutation": {"return": returned, "errno": actual_errno},
        "seal_module_sha256": seal["module_sha256"], "native_v4_write_enabled": False,
        "g5_qualification_allowed": False, "real_orders_sent": 0}
    _QUOTA_PROOFS[id(quota)] = (quota, os.getpid(), components.canonical(binding), components.canonical(filter_binding),
                               components.canonical(record))
    return record


class NativeCustodyContext:
    """Actor-side live guardian proof; issuer CustodyWitness cannot cross exec."""
    def __init__(self, issuer, *, request, proof):
        require(issuer is _ISSUER, "G5A_NATIVE_CUSTODY_FACTORY_REQUIRED")
        self.request_wire, self.proof_wire = components.canonical(request), components.canonical(proof)
        self.pid = os.getpid()
        _CUSTODY[id(self)] = (self, self.pid, self.request_wire, self.proof_wire)

    def validate(self, *, binding):
        from scripts import rc6_privileged_custody as privileged
        require(_CUSTODY.get(id(self)) == (self, os.getpid(), self.request_wire, self.proof_wire),
                "G5A_ACTUAL_CURRENT_LIVE_CUSTODY_REQUIRED")
        request, proof = components.loads(self.request_wire), components.loads(self.proof_wire)
        require(request["binding"] == binding and request["source_sha"] == binding["candidate_sha"]
                and request["source_tree"] == binding["candidate_tree"],
                "G5A_CUSTODY_SOURCE_OWNER_ATTEMPT_REBOUND")
        return privileged.verify_live_controller(proof, request)


def observe_live_g5a_custody(request, proof):
    """Trusted actor native observation before Seal, not an issuer token copy."""
    require(os.getresuid() == (0, 0, 0), "G5A_NATIVE_CUSTODY_ROOT_OBSERVATION_REQUIRED")
    require(type(request) is dict and request.get("g5a_contract_sha256") == CONTRACT_SHA256,
            "G5A_NATIVE_CUSTODY_CONTRACT_REQUEST_REQUIRED")
    require(request.get("require_quota_ancestor_seal") is True,
            "G5A_ACTUAL_SEALED_PRIVATE_ROOT_ANCESTOR_REQUIRED")
    result = NativeCustodyContext(_ISSUER, request=request, proof=proof)
    result.validate(binding=request["binding"])
    return result


def require_sealed_nonroot(*, source_sha, source_tree, filter_binding):
    from scripts import rc6_native_namespace_filter as native_filter
    fields = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    require(os.getresuid() == (os.getuid(),) * 3 and os.getuid() > 0
            and os.getresgid() == (os.getgid(),) * 3 and os.getgid() > 0 and os.getgroups() == []
            and all(int(fields[name].strip(), 16) == 0 for name in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"))
            and fields["NoNewPrivs"].strip() == "1" and fields["Seccomp"].strip() == "2",
            "G5A_ACTUAL_NONROOT_SEAL_REQUIRED")
    require(type(filter_binding) is dict and set(filter_binding) == {"source_sha", "source_tree", "plan_sha256"}
            and filter_binding["source_sha"] == source_sha and filter_binding["source_tree"] == source_tree
            and _HEX.fullmatch(str(filter_binding["plan_sha256"])),
            "G5A_EXACT_SOURCE_AND_FILTER_PLAN_BINDING_REQUIRED")
    return native_filter.assert_current_filter(source_binding=filter_binding)


def reserve_state(snapshot, *, state, physical_costs, additional_inodes, future_gc_credit_bytes=0):
    """Arithmetic guard only. A safe reservation is never G5 admission/proof."""
    require(state in STATE_CLASSES and type(physical_costs) is dict and set(physical_costs) == RESERVATION_CLASSES
            and all(type(value) is int and value >= 0 for value in physical_costs.values())
            and type(additional_inodes) is int and additional_inodes >= 0
            and type(future_gc_credit_bytes) is int and future_gc_credit_bytes == 0,
            "G5A_ALL_SIMULTANEOUS_PHYSICAL_COSTS_REQUIRED_NO_FUTURE_GC")
    require(type(snapshot) is dict and snapshot.get("hard_bytes") == CAP_BYTES
            and snapshot.get("hard_inodes") == CAP_INODES
            and all(type(snapshot.get(key)) is int and snapshot[key] >= 0 for key in ("used_bytes", "used_inodes")),
            "G5A_CURRENT_ACTUAL_ALLOCATION_REQUIRED")
    projected_bytes = snapshot["used_bytes"] + sum(physical_costs.values())
    projected_inodes = snapshot["used_inodes"] + additional_inodes
    require(projected_bytes <= CAP_BYTES and projected_inodes <= CAP_INODES,
            "G5A_CURRENT_CONCURRENT_PHYSICAL_RESERVATION_RED")
    return {"schema": "rc6.g5a-state-reservation.v2", "state": state,
            "current_allocation": dict(snapshot), "physical_costs": dict(physical_costs),
            "additional_inodes": additional_inodes, "projected_bytes": projected_bytes,
            "projected_inodes": projected_inodes, "future_gc_credit_bytes": 0,
            "g5_qualification_allowed": False}


@dataclass(frozen=True, init=False)
class G5AAuthority:
    approval: object
    quota: NativeProjectQuota
    binding_wire: bytes
    prerequisites_wire: bytes
    custody: object
    filter_binding_wire: bytes
    publication_envelope_wire: bytes
    source_sha: str
    source_tree: str
    continuation: object
    accepted_utc: str
    workload_quota: NativeWorkloadQuota

    def __init__(self, issuer, *, approval, quota, binding, prerequisites, custody, source_sha, source_tree,
                 filter_binding, publication_envelope, continuation=None, accepted_utc=None, workload_quota=None):
        require(issuer is _ISSUER, "G5A_SERIALIZED_OR_CALLER_ASSERTED_ADMISSION_FORBIDDEN")
        for name, value in {"approval": approval, "quota": quota, "binding_wire": components.canonical(binding),
                "prerequisites_wire": components.canonical(prerequisites), "custody": custody,
                "filter_binding_wire": components.canonical(filter_binding),
                "publication_envelope_wire": components.canonical(publication_envelope),
                "source_sha": source_sha, "source_tree": source_tree, "continuation": continuation,
                "accepted_utc": accepted_utc, "workload_quota": workload_quota}.items():
            object.__setattr__(self, name, value)
        _AUTHORITIES[id(self)] = (self, os.getpid(), approval, quota, self.binding_wire,
                                  self.prerequisites_wire, custody, source_sha, source_tree, self.filter_binding_wire,
                                  self.publication_envelope_wire, continuation, accepted_utc, workload_quota)

    def reserve(self, *, state, physical_costs, additional_inodes, expected_binding):
        validate_g5a_authority(self, expected_binding=expected_binding)
        return reserve_state(self.quota.snapshot(binding=expected_binding), state=state,
                             physical_costs=physical_costs, additional_inodes=additional_inodes)

    def before_publication(self, *, archive_path, additional_bytes, additional_inodes, state, expected_binding):
        """Native inventory is checked again; the hard kernel cap stays independent."""
        validate_g5a_authority(self, expected_binding=expected_binding)
        require(Path(archive_path).absolute() == self.quota.path
                and type(additional_bytes) is int and additional_bytes >= 0
                and type(additional_inodes) is int and additional_inodes >= 0,
                "G5A_PUBLICATION_PATH_OR_CONCURRENT_INPUT_REBOUND")
        snapshot = self.quota.snapshot(binding=expected_binding)
        envelope = components.loads(self.publication_envelope_wire)
        validate_publication_envelope(envelope, snapshot=snapshot, source_sha=self.source_sha, source_tree=self.source_tree)
        require(additional_inodes <= envelope["maximum_new_inodes"], "G5A_NEW_INODE_ENVELOPE_EXCEEDED")
        unit = snapshot["allocation_unit"]
        # additional_bytes includes the existing writer's simultaneous receipt,
        # checkpoint and control staging reserve. Never subtract a planned GC.
        costs = dict(envelope["fixed_simultaneous_physical_costs"])
        data = ((additional_bytes + unit - 1) // unit) * unit + additional_inodes * unit
        costs["new_whole_packs"] += data
        costs["directory_and_filesystem_metadata"] += data * envelope.get("per_data_block_extent_index_blocks", 0)
        costs["directory_and_filesystem_metadata"] += additional_inodes * envelope["per_new_inode_metadata_bytes"]
        # Quotas are independent projects. Keep the original aggregate20-GiB
        # physical/reserve limit even when this write is charged to archive512.
        require(isinstance(self.workload_quota, NativeWorkloadQuota), "G5A_ACTUAL_ORIGINAL_WORKLOAD_QUOTA_REQUIRED")
        self.workload_quota.reserve_physical(path=self.quota.path, physical_upper=sum(costs.values()),
            additional_inodes=additional_inodes, binding=expected_binding, known_encoded_bytes=additional_bytes)
        return self.reserve(state=state, physical_costs=costs, additional_inodes=additional_inodes,
                            expected_binding=expected_binding)

    def capture_native_control(self, *, expected_binding):
        """Serializable RAW only, never an admission token or a qualified G5."""
        validate_g5a_authority(self, expected_binding=expected_binding)
        filter_binding = components.loads(self.filter_binding_wire)
        seal = require_sealed_nonroot(source_sha=self.source_sha, source_tree=self.source_tree,
                                      filter_binding=filter_binding)
        return {"schema": "rc6.g5a-native-physical-control.v2", "source_sha": self.source_sha,
            "source_tree": self.source_tree, "contract_sha256": CONTRACT_SHA256,
            "binding": dict(expected_binding), "filter_binding": filter_binding,
            "quota_initial_readback": components.loads(self.quota.readback_wire),
            "probe_initial_readback": components.loads(self.quota.probe_readback_wire),
            "native_ext4_geometry": components.loads(self.quota.geometry_wire),
            "source_publication_envelope": components.loads(self.publication_envelope_wire),
            "native_descriptor_composition": require_g5a_descriptor_composition(self.quota,
                binding=expected_binding, filter_binding=filter_binding),
            "owner_decision_lifecycle": {"mode": CONTRACT["owner_decision_lifecycle"],
                "accepted_utc": self.accepted_utc, "same_native_operation_only": True,
                "live_continuation_required": self.continuation is not None},
            "original_workload_native_readback": components.loads(self.workload_quota.readback_wire),
            "original_workload_current_physical": self.workload_quota.snapshot(binding=expected_binding),
            "enforcement_proof": self.quota.require_enforcement_proof(binding=expected_binding, filter_binding=filter_binding),
            "current_native_project": self.quota.snapshot(binding=expected_binding),
            "sealed_filter": seal, "g5_qualification_allowed": False, "native_v4_write_enabled": False,
            "real_orders_sent": 0}


def validate_publication_envelope(record, *, snapshot, source_sha, source_tree):
    """An authenticated mathematical/native envelope is separate from a sample."""
    require(type(record) is dict and record.get("schema") == "rc6.g5a-per-write-physical-envelope.v2"
            and record.get("source_sha") == source_sha and record.get("source_tree") == source_tree
            and record.get("contract_sha256") == CONTRACT_SHA256
            and record.get("origin") in {"SOURCE_PROVED_PER_WRITE_BOUND_NOT_SAMPLED_FORECAST", "CLOSED_SOURCE_OPERATIONS_ACTUAL_EXT4_GEOMETRY"}
            and record.get("allocation_unit") == snapshot["allocation_unit"]
            and record.get("quota_format_id") == snapshot["quota_format_id"]
            and type(record.get("maximum_new_inodes")) is int and 0 < record["maximum_new_inodes"] <= CAP_INODES
            and type(record.get("per_new_inode_metadata_bytes")) is int and record["per_new_inode_metadata_bytes"] >= 0
            and type(record.get("fixed_simultaneous_physical_costs")) is dict
            and set(record["fixed_simultaneous_physical_costs"]) == RESERVATION_CLASSES
            and all(type(value) is int and value >= 0 for value in record["fixed_simultaneous_physical_costs"].values())
            and (type(record.get("proof_members")) is list and bool(record["proof_members"])
                 or record.get("origin") == "CLOSED_SOURCE_OPERATIONS_ACTUAL_EXT4_GEOMETRY"),
            "G5A_PROVEN_CURRENT_FS_PUBLICATION_ENVELOPE_REQUIRED")
    if record["origin"] == "CLOSED_SOURCE_OPERATIONS_ACTUAL_EXT4_GEOMETRY":
        require(record == source_publication_envelope(record.get("writer_codec_restorer_binding"),
            record.get("native_ext4_geometry"), source_sha=source_sha, source_tree=source_tree,
            quota_format_id=snapshot["quota_format_id"]), "G5A_CLOSED_SOURCE_OPERATION_ENVELOPE_REBOUND")
    return record


def source_publication_envelope(writer_binding, geometry, *, source_sha, source_tree, quota_format_id):
    """A per-write mathematical bound, independent of all1202 future outcomes.

    The two Source-bound file writers create one flat new regular inode at a
    time, write a known byte string sequentially, fsync, close and rename. No
    sparse seek, fallocate, clone, reflink, writable mmap, external restoration
    file, range deletion or asynchronous writer is in that operation inventory.
    Existing bytes are counted by the live native snapshot; replacement control
    bytes remain simultaneous with the old file. A fixed full 32768-block extent
    additionally covers the last unused sequential allocator reservation while
    the one active inode is open. It is released by close before the next file.

    The bound may conservatively refuse a write that would fit. Refusal is RED,
    never permission to relax512MiB or infer a complete Horizon from samples.
    Qualification still requires all actual state/operation RAW and original
    restoration tests on this exact filesystem/program.
    """
    require(type(writer_binding) is dict and writer_binding.get("schema") == "rc6.archive-writer-codec-restorer-binding.v1"
            and type(geometry) is dict and type(quota_format_id) is int and quota_format_id > 0,
            "G5A_CLOSED_SOURCE_WRITER_AND_NATIVE_GEOMETRY_REQUIRED")
    try:
        raw = base64.b64decode(geometry.get("raw_base64", ""), validate=True)
    except (TypeError, ValueError) as error:
        raise ValueError("G5A_ACTUAL_EXT4_SUPERBLOCK_REQUIRED") from error
    require(decode_native_ext4_geometry(raw) == geometry, "G5A_NATIVE_EXT4_GEOMETRY_REBOUND")
    from rc6_shadow_runtime.archive_format_contract import SOURCE_MEMBERS
    require(set(writer_binding.get("source_files_sha256", {})) == set(SOURCE_MEMBERS)
            and all(type(value) is str and _HEX.fullmatch(value) for value in writer_binding["source_files_sha256"].values()),
            "G5A_COMPLETE_CLOSED_SOURCE_PUBLICATION_BYTES_REQUIRED")
    closed = writer_binding.get("closed_file_write_inventory")
    require(type(closed) is list and len(closed) == 2
            and {item.get("qualified_name") for item in closed} == {
                "ComponentArchive._publish", "EvidenceRetention._durable_control"}
            and all(item.get("one_active_new_regular_inode") is True and item.get("sequential_known_byte_write") is True
                    and type(item.get("source_sha256")) is str and _HEX.fullmatch(item["source_sha256"]) for item in closed),
            "G5A_ACTUAL_CLOSED_SEQUENTIAL_WRITER_INVENTORY_REQUIRED")
    unit, depth = geometry["block_bytes"], geometry["maximum_extent_index_depth"]
    fixed = {name: 0 for name in RESERVATION_CLASSES}
    fixed["publication_temporaries"] = geometry["maximum_extent_data_blocks"] * unit
    fixed["directory_and_filesystem_metadata"] = depth * unit
    return {"schema": "rc6.g5a-per-write-physical-envelope.v2", "source_sha": source_sha,
        "source_tree": source_tree, "contract_sha256": CONTRACT_SHA256,
        "origin": "CLOSED_SOURCE_OPERATIONS_ACTUAL_EXT4_GEOMETRY", "allocation_unit": unit,
        "quota_format_id": quota_format_id, "maximum_new_inodes": 8,
        "per_data_block_extent_index_blocks": depth,
        "per_new_inode_metadata_bytes": (1 + geometry["maximum_directory_index_path_blocks"]) * (1 + depth) * unit,
        "fixed_simultaneous_physical_costs": fixed, "future_gc_credit_bytes": 0,
        "writer_codec_restorer_binding": writer_binding, "native_ext4_geometry": geometry,
        "closed_operation_inventory": {"active_regular_writers": 1, "new_file_write": "SEQUENTIAL_KNOWN_BYTES_FSYNC_CLOSE_RENAME",
            "archive": "FLAT_PROJINHERIT_OWNED_FILES", "recovery": "SAME_IMMUTABLE_BYTES_OR_IN_MEMORY_EXACT_RESTORE",
            "GC": "CONTROL_REPLACE_AND_WHOLE_FILE_UNLINK", "future_GC_or_compression_credit": False},
        "g5_qualification_allowed": False, "native_v4_write_enabled": False, "real_orders_sent": 0}


class NativePhysicalLedger:
    """Same-process actual operation ledger, never a simulated state trace.

    States absent from a successful cut are explicitly NOT_ENTERED. Their
    zero-growth check uses the actual committed allocation; it does not claim
    that a crash, deletion, or recovery was performed. Those adversarial cases
    require their separately executed native recovery RAW at G5B.
    """
    def __init__(self, authority, *, expected_binding):
        validate_g5a_authority(authority, expected_binding=expected_binding)
        self.authority, self.binding = authority, dict(expected_binding)
        self.cuts, self.pending, self.events = [], None, []
        initial = authority.quota.snapshot(binding=expected_binding)
        self.project = {name: value for name, value in initial.items() if name not in LEDGER_ALLOCATION_FIELDS}
        self.operations = []

    def begin(self, cut_index, original_member_sha256):
        require(self.pending is None and type(cut_index) is int and cut_index == len(self.cuts)
                and cut_index < 1202 and set(original_member_sha256) == components.MEMBERS,
                "G5A_PHYSICAL_LEDGER_EXACT_NEXT_ORIGINAL_CUT_REQUIRED")
        self.pending = {"cut_index": cut_index, "original_member_sha256": dict(original_member_sha256),
                        "states": {}, "operation_events": []}
        self.record("admission", operation="CUT_BEGIN", entered=True)

    def record(self, state, *, operation, entered=True, reservation=None):
        require(self.pending is not None and state in STATE_CLASSES and type(operation) is str,
                "G5A_NATIVE_LEDGER_STATE_OUTSIDE_ORIGINAL_CUT")
        validate_g5a_authority(self.authority, expected_binding=self.binding)
        observed = self.authority.quota.snapshot(binding=self.binding)
        if reservation is None:
            reservation = reserve_state(observed, state=state,
                physical_costs={name: 0 for name in RESERVATION_CLASSES}, additional_inodes=0)
        require(reservation.get("state") == state, "G5A_NATIVE_LEDGER_RESERVATION_STATE_REBOUND")
        self.pending["states"][state] = reservation
        event = {"sequence": len(self.pending["operation_events"]), "state": state, "operation": operation,
            "status": "ACTUAL_ENTERED_STATE" if entered else "NOT_ENTERED_IN_THIS_SUCCESSFUL_CUT",
            "current_allocation": observed, "reservation": reservation,
            "reservation_sha256": components.sha(components.canonical(reservation))}
        self.pending["operation_events"].append(event)
        return event

    def before(self, *, state, path, additional_bytes, additional_inodes):
        reservation = self.authority.before_publication(archive_path=self.authority.quota.path,
            additional_bytes=additional_bytes, additional_inodes=additional_inodes,
            state=state, expected_binding=self.binding)
        self.record(state, operation="BEFORE_SOURCE_PUBLICATION:" + Path(path).name,
                    entered=True, reservation=reservation)
        return reservation

    def after(self, *, state, path):
        self.record(state, operation="AFTER_SOURCE_PUBLICATION:" + Path(path).name, entered=True)

    def finish_cut(self):
        require(self.pending is not None, "G5A_NATIVE_LEDGER_NO_ORIGINAL_CUT")
        self.record("committed", operation="NATIVE_ACK_RESTORE_AND_SOURCE_CUSTODY_COMPLETE", entered=True)
        missing = STATE_CLASSES - set(self.pending["states"])
        self.pending["not_entered_states"] = sorted(missing)
        for state in sorted(missing):
            self.record(state, operation="NO_SOURCE_OPERATION_ENTERED_FOR_STATE", entered=False)
        result = self.pending
        self.cuts.append(compact_ledger_cut(result, project=self.project, operations=self.operations)); self.pending = None
        return result

    def capture(self):
        pending = None if self.pending is None else compact_ledger_cut(self.pending, project=self.project,
                                                                       operations=self.operations)
        return {"schema": "rc6.g5a-all1202-physical-ledger.v2", "source_sha": self.binding["candidate_sha"],
            "source_tree": self.binding["candidate_tree"], "contract_sha256": CONTRACT_SHA256,
            "cuts": self.cuts, "actual_completed_cuts": len(self.cuts),
            "codec": "rc6.g5a-lossless-state-columns.v2", "project_invariants": self.project,
            "allocation_fields": list(LEDGER_ALLOCATION_FIELDS), "cost_fields": sorted(RESERVATION_CLASSES),
            "state_fields": sorted(STATE_CLASSES), "operations": self.operations,
            "pending_original_cut": pending,
            "all1202_completed": len(self.cuts) == 1202, "qualification_claimed": False,
            "native_v4_write_enabled": False, "real_orders_sent": 0}


LEDGER_ALLOCATION_FIELDS = ("used_bytes", "used_inodes", "physical_allocated_bytes_including_directory",
                            "actual_member_count", "actual_allocation_inventory_sha256")


def compact_ledger_cut(cut, *, project, operations):
    """Lossless compact capture keeps full1202 ledger out of the worker heap."""
    states, costs = sorted(STATE_CLASSES), sorted(RESERVATION_CLASSES)
    encoded, last = [], {}
    for event in cut["operation_events"]:
        current, reservation = event["current_allocation"], event["reservation"]
        require({key: value for key, value in current.items() if key not in LEDGER_ALLOCATION_FIELDS} == project
                and reservation["current_allocation"] == current,
                "G5A_NATIVE_STATE_PROJECT_REBOUND_DURING_OPERATION")
        operation = event["operation"]
        if operation not in operations: operations.append(operation)
        index = states.index(event["state"])
        encoded.append([index, operations.index(operation), int(event["status"] == "ACTUAL_ENTERED_STATE"),
            [current[key] for key in LEDGER_ALLOCATION_FIELDS], [reservation["physical_costs"][key] for key in costs],
            reservation["additional_inodes"], reservation["projected_bytes"], reservation["projected_inodes"]])
        last[index] = len(encoded) - 1
    return {"cut_index": cut["cut_index"], "original_member_sha256": cut["original_member_sha256"],
        "events": encoded, "states": [[index, last[index]] for index in sorted(last)],
        "not_entered_states": [states.index(state) for state in cut.get("not_entered_states", [])]}


def expand_ledger_capture(record):
    """Decode captured data only; caller authenticates Actions RAW separately."""
    require(type(record) is dict and record.get("codec") == "rc6.g5a-lossless-state-columns.v2"
            and record.get("allocation_fields") == list(LEDGER_ALLOCATION_FIELDS)
            and record.get("cost_fields") == sorted(RESERVATION_CLASSES)
            and record.get("state_fields") == sorted(STATE_CLASSES)
            and type(record.get("project_invariants")) is dict and type(record.get("operations")) is list
            and all(type(operation) is str and 0 < len(operation) <= 256 for operation in record["operations"])
            and type(record.get("cuts")) is list and len(record["cuts"]) <= 1202,
            "G5B_LOSSLESS_PHYSICAL_LEDGER_CODEC_REQUIRED")
    cuts, state_names, cost_names = [], record["state_fields"], record["cost_fields"]
    for cut in record["cuts"]:
        require(type(cut) is dict and type(cut.get("events")) is list and len(cut["events"]) <= 8192,
                "G5B_NATIVE_OPERATION_LEDGER_BOUND_REQUIRED")
        events = []
        for sequence, columns in enumerate(cut["events"]):
            require(type(columns) is list and len(columns) == 8
                    and all(type(columns[index]) is int for index in (0, 1, 2, 5, 6, 7))
                    and 0 <= columns[0] < len(state_names) and 0 <= columns[1] < len(record["operations"])
                    and columns[2] in (0, 1) and type(columns[3]) is list and len(columns[3]) == len(LEDGER_ALLOCATION_FIELDS)
                    and type(columns[4]) is list and len(columns[4]) == len(cost_names),
                    "G5B_NATIVE_OPERATION_LEDGER_COLUMNS_CHANGED")
            allocation = {**record["project_invariants"], **dict(zip(LEDGER_ALLOCATION_FIELDS, columns[3]))}
            reservation = reserve_state(allocation, state=state_names[columns[0]],
                physical_costs=dict(zip(cost_names, columns[4])), additional_inodes=columns[5])
            require((reservation["projected_bytes"], reservation["projected_inodes"]) == (columns[6], columns[7]),
                    "G5B_CAPTURED_PHYSICAL_PROJECTED_BUDGET_REBOUND")
            events.append({"sequence": sequence, "state": state_names[columns[0]], "operation": record["operations"][columns[1]],
                "status": "ACTUAL_ENTERED_STATE" if columns[2] else "NOT_ENTERED_IN_THIS_SUCCESSFUL_CUT",
                "current_allocation": allocation, "reservation": reservation,
                "reservation_sha256": components.sha(components.canonical(reservation))})
        require(type(cut.get("states")) is list and len(cut["states"]) == len(STATE_CLASSES)
                and all(type(pair) is list and len(pair) == 2 and all(type(value) is int for value in pair)
                        and 0 <= pair[0] < len(state_names) and 0 <= pair[1] < len(events) for pair in cut["states"])
                and {pair[0] for pair in cut["states"]} == set(range(len(STATE_CLASSES))),
                "G5B_ALL_STATES_MUST_REFERENCE_ACTUAL_CAPTURED_EVENTS")
        require(type(cut.get("not_entered_states")) is list
                and all(type(index) is int and 0 <= index < len(state_names) for index in cut["not_entered_states"])
                and len(cut["not_entered_states"]) == len(set(cut["not_entered_states"])),
                "G5B_NOT_ENTERED_STATE_DECLARATION_CHANGED")
        trace = {"cut_index": cut["cut_index"], "original_member_sha256": cut["original_member_sha256"],
            "operation_events": events,
            "states": {state_names[index]: events[reference]["reservation"] for index, reference in cut["states"]},
            "not_entered_states": [state_names[index] for index in cut["not_entered_states"]]}
        require(all(reservation["state"] == state for state, reservation in trace["states"].items()),
                "G5B_CAPTURED_LEDGER_STATE_REFERENCE_REBOUND")
        absent = set(trace["not_entered_states"])
        require(all((event["state"] in absent) == (event["status"] == "NOT_ENTERED_IN_THIS_SUCCESSFUL_CUT")
                    and (event["state"] not in absent or event["operation"] == "NO_SOURCE_OPERATION_ENTERED_FOR_STATE")
                    for event in events), "G5B_NOT_ENTERED_STATE_CANNOT_CLAIM_ACTUAL_SOURCE_OPERATION")
        require(all(reference == max(index for index, event in enumerate(events) if event["state"] == state_names[state])
                    for state, reference in cut["states"]), "G5B_CAPTURED_STATE_MUST_REFERENCE_LAST_ACTUAL_ALLOCATION")
        cuts.append(trace)
    return {**record, "cuts": cuts}


def authenticate_g5a(*, approval, quota, custody, binding, filter_binding, prerequisites_manifest,
                    evidence_root, publication_envelope_reference=None, source_root=None, get=None, download=None,
                    trusted_preparation=None, continuation=None, workload_quota=None):
    """Only this factory can replace the legacy launch hold, after owner acceptance."""
    from scripts.rc6_contract_revisions import validate_revision_approval
    from scripts import rc6_architectural_gates as gates
    quota_setup_request(binding, archive_path=quota.path if type(quota) is NativeProjectQuota else Path(evidence_root).parent)
    source_sha, source_tree = binding["candidate_sha"], binding["candidate_tree"]
    validate_revision_approval(approval, revision=REVISION, scope=SCOPE, source_sha=source_sha,
                              source_tree=source_tree, contract_sha256=CONTRACT_SHA256)
    require(type(quota) is NativeProjectQuota, "G5A_LIVE_PHYSICAL_QUOTA_HANDLE_REQUIRED")
    quota.snapshot(binding=binding)
    require(type(custody) is NativeCustodyContext, "G5A_ACTOR_SIDE_NATIVE_CUSTODY_CONTEXT_REQUIRED")
    custody.validate(binding=binding)
    require_sealed_nonroot(source_sha=source_sha, source_tree=source_tree, filter_binding=filter_binding)
    quota.require_enforcement_proof(binding=binding, filter_binding=filter_binding)
    require(type(workload_quota) is NativeWorkloadQuota, "G5A_NATIVE_ORIGINAL20GIB_RAW_CAPTURE_SCOPE_REQUIRED")
    workload_quota.snapshot(binding=binding)
    require(get is None and download is None, "G5A_NETWORK_OR_BEARER_READER_AFTER_SEAL_FORBIDDEN")
    verified = validate_trusted_g5a_preparation(trusted_preparation, binding=binding,
                                               prerequisites_manifest=prerequisites_manifest, continuation=continuation)
    require(trusted_preparation.approval is approval and source_root is not None
            and trusted_preparation.source_root == str(Path(source_root).absolute()),
            "G5A_NATIVE_TRUSTED_PREPARATION_APPROVAL_OR_SOURCE_REBOUND")
    require_g5a_descriptor_composition(quota, binding=binding, filter_binding=filter_binding)
    reference = publication_envelope_reference
    if reference is None:
        from rc6_shadow_runtime.archive_format_contract import SOURCE_MEMBERS, writer_codec_restorer_binding
        from scripts.rc6_cas_original_comparison import _read, MAX_INDEX_BYTES
        source_root = Path(source_root) if source_root is not None else Path(__file__).absolute().parents[1]
        require(Path(__file__).absolute() == source_root / "scripts/rc6_g5_contract.py",
                "G5A_CLOSED_OPERATION_PROOF_MUST_USE_EXACT_EXECUTED_SOURCE")
        writer_binding = writer_codec_restorer_binding({name: _read(source_root / name, maximum=MAX_INDEX_BYTES)
                                                      for name in SOURCE_MEMBERS})
        envelope = source_publication_envelope(writer_binding, components.loads(quota.geometry_wire),
            source_sha=source_sha, source_tree=source_tree, quota_format_id=quota.snapshot(binding=binding)["quota_format_id"])
    else:
        require(type(reference) is dict and set(reference) == {"gate", "path", "sha256"}
            and reference["gate"] in {"G0", "G1.311", "G1.312"} and _HEX.fullmatch(str(reference["sha256"])),
            "G5A_HASH_BOUND_EXECUTED_PHYSICAL_ENVELOPE_REQUIRED")
        archive = verified["evidence_files"][reference["gate"]]["archive"]
        raw = gates.capture_member(archive, reference["path"])
        require(components.sha(raw) == reference["sha256"], "G5A_PHYSICAL_ENVELOPE_ARTIFACT_REBOUND")
        envelope = components.loads(raw)
    validate_publication_envelope(envelope, snapshot=quota.snapshot(binding=binding), source_sha=source_sha, source_tree=source_tree)
    for proof in envelope.get("proof_members", []):
        require(type(proof) is dict and set(proof) == {"path", "sha256"} and _HEX.fullmatch(str(proof["sha256"]))
                and components.sha(gates.capture_member(archive, proof["path"])) == proof["sha256"],
                "G5A_PHYSICAL_ENVELOPE_ACTUAL_PROOF_BYTES_REQUIRED")
    result = G5AAuthority(_ISSUER, approval=approval, quota=quota, binding=binding,
        prerequisites={"manifest_sha256": components.sha(components.canonical(prerequisites_manifest)),
                       "artifact_origins": verified["artifact_origins"], "verified_inputs": verified}, custody=custody,
        source_sha=source_sha, source_tree=source_tree, filter_binding=filter_binding, publication_envelope=envelope,
        continuation=continuation, accepted_utc=trusted_preparation.accepted_utc, workload_quota=workload_quota)
    validate_g5a_authority(result, expected_binding=binding)
    return result


def validate_g5a_authority(authority, *, expected_binding):
    from scripts.rc6_contract_revisions import validate_revision_approval
    registered = _AUTHORITIES.get(id(authority))
    require(type(authority) is G5AAuthority and registered is not None
            and registered == (authority, os.getpid(), authority.approval, authority.quota,
                authority.binding_wire, authority.prerequisites_wire, authority.custody,
                authority.source_sha, authority.source_tree, authority.filter_binding_wire, authority.publication_envelope_wire,
                authority.continuation, authority.accepted_utc, authority.workload_quota)
            and authority.binding_wire == components.canonical(expected_binding),
            "G5A_AUTHENTIC_CURRENT_SCOPED_ADMISSION_REQUIRED")
    validate_revision_approval(authority.approval, revision=REVISION, scope=SCOPE,
        source_sha=authority.source_sha, source_tree=authority.source_tree, contract_sha256=CONTRACT_SHA256,
        now=None if authority.continuation is None else datetime.fromisoformat(authority.accepted_utc))
    if authority.continuation is not None:
        from scripts.rc6_owned_g5a_continuation import validate_continuation
        validate_continuation(authority.continuation, binding=expected_binding)
    authority.custody.validate(binding=expected_binding)
    require_sealed_nonroot(source_sha=authority.source_sha, source_tree=authority.source_tree,
                          filter_binding=components.loads(authority.filter_binding_wire))
    authority.quota.snapshot(binding=expected_binding)
    authority.quota.require_enforcement_proof(binding=expected_binding,
                                            filter_binding=components.loads(authority.filter_binding_wire))
    return authority


def validate_prepared_producer_root(root, *, g5a, expected_binding):
    """Accept only the empty, same-process quota hierarchy before fixtures exist."""
    validate_g5a_authority(g5a, expected_binding=expected_binding)
    from rc6_shadow_runtime.persistence import shadow_archive_root
    root = Path(root).absolute()
    expected_archive = shadow_archive_root(root / "data/paper_v17/observer_v17.db", {})
    require(g5a.quota.path == expected_archive and expected_archive.is_relative_to(root)
            and root.is_dir(), "G5A_EXACT_PREPARED_ORIGINAL_PRODUCER_ROOT_REQUIRED")
    required = set()
    node = expected_archive
    while node != root:
        required.add(node); node = node.parent
    required.add(root)
    queue, seen = [root], set()
    while queue:
        path = queue.pop()
        info = path.lstat()
        require(path in required and stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o700 and not path.is_symlink(),
                "G5A_PREPARED_NAMESPACE_FOREIGN_CONTENT_OR_CUSTODY_CHANGED")
        seen.add(path)
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME)
        try:
            require(_identity(os.fstat(descriptor)) == _identity(info), "G5A_PREPARED_NAMESPACE_REBOUND")
            queue.extend(path / name for name in os.listdir(descriptor))
        finally:
            os.close(descriptor)
    require(seen == required, "G5A_PREPARED_QUOTA_HIERARCHY_INCOMPLETE")
    return {"status": "PREPARED_EMPTY_ORIGINAL_NAMESPACE_NOT_QUALIFICATION", "real_orders_sent": 0}


def validate_complete_original_stream(result, *, quota_trace, writer_binding):
    """Validate captured G5B data AFTER authenticated Actions capture.

    This function alone is not provenance, an artifact approval, or a gate. It
    rejects incomplete metadata and cross-binds the per-cut exact native reads,
    hard-quota state ledger and source-bound writer/codec/restorer identities.
    The gate verifier must authenticate the actual RAW and execution first.
    """
    from scripts.rc6_cas_original_comparison import CONTRACT as ORIGINAL
    require(type(result) is dict and result.get("original_contract") == components.loads(components.canonical(dict(ORIGINAL)))
            and result.get("actual_original_cuts") == 1202 and type(result.get("cuts")) is list
            and len(result["cuts"]) == 1202 and result.get("native_candidate_ack_count") == 1202
            and result.get("first_operational_cut_restored", {}).get("V4") is True,
            "G5B_ALL1202_ORIGINAL_RESTORES_AND_NATIVE_ACKS_REQUIRED")
    require(result.get("classification") == "ORIGINAL_BYTE_DEVELOPMENT_COMPARISON_NOT_G5"
            and result.get("native_v4_write_enabled") is False and result.get("real_orders_sent") == 0,
            "G5B_ACQUISITION_CANNOT_SELF_CERTIFY_OR_ENABLE_NATIVE_WRITER")
    require(type(writer_binding) is dict and writer_binding.get("schema") == "rc6.archive-writer-codec-restorer-binding.v1"
            and result.get("writer_codec_restorer_binding") == writer_binding,
            "G5B_EXACT_WRITER_CODEC_RESTORER_SOURCE_BINDING_REQUIRED")
    require(type(quota_trace) is list and len(quota_trace) == 1202,
            "G5B_ALL1202_LIVE_PHYSICAL_QUOTA_LEDGER_REQUIRED")
    clocks = horizon_schedule(datetime.fromisoformat(ORIGINAL["preopen"]).date())
    for number, (cut, trace) in enumerate(zip(result["cuts"], quota_trace)):
        require(type(cut) is dict and cut.get("cut_index") == number and cut.get("as_of") == clocks[number].isoformat()
                and set(cut.get("original_member_sha256", {})) == components.MEMBERS
                and all(type(value) is str and _HEX.fullmatch(value) for value in cut["original_member_sha256"].values())
                and set(cut.get("member_bytes", {})) == components.MEMBERS
                and all(type(value) is int and 0 < value <= components.MAX_MEMBER_BYTES for value in cut["member_bytes"].values()),
                "G5B_ORIGINAL_SCHEDULE_OR_FIVE_MEMBER_PAYLOAD_BINDING_CHANGED")
        branch = cut.get("branches", {}).get("V4", {})
        require(branch.get("status") == "EXACT_ORIGINAL_BYTES_RESTORED"
                and branch.get("recipe_schema") in {components.RECIPE_SCHEMA, components.SLICE_RECIPE_SCHEMA}
                and type(branch.get("maximum_dependency_depth")) is int
                and 0 <= branch["maximum_dependency_depth"] <= 32,
                "G5B_V4_ORIGINAL_RESTORE_OR_DEPTH_RED")
        require(type(trace) is dict and trace.get("cut_index") == number
                and trace.get("original_member_sha256") == cut["original_member_sha256"]
                and type(trace.get("states")) is dict and set(trace["states"]) == STATE_CLASSES,
                "G5B_ALL_ORIGINAL_SIMULTANEOUS_STATES_REQUIRED")
        for state, reservation in trace["states"].items():
            require(type(reservation) is dict and all(key in reservation for key in (
                "current_allocation", "physical_costs", "additional_inodes", "future_gc_credit_bytes")),
                "G5B_COMPLETE_PHYSICAL_RESERVATION_REQUIRED")
            expected = reserve_state(reservation["current_allocation"], state=state,
                physical_costs=reservation["physical_costs"], additional_inodes=reservation["additional_inodes"],
                future_gc_credit_bytes=reservation.get("future_gc_credit_bytes"))
            require(reservation == expected, "G5B_PHYSICAL_RESERVATION_LEDGER_REBOUND")
    return {"status": "VALIDATED_CAPTURED_DATA_ONLY_NOT_GATE", "original_cuts": 1202,
            "storage_reduction_percent": None, "native_v4_write_enabled": False,
            "runtime_validated": False, "real_orders_sent": 0}
