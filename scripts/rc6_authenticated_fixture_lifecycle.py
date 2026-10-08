#!/usr/bin/env python3
"""Custody for generated test namespaces, actual owned FIN and sealed evidence.

This is not host/runtime cleanup. A path or a caller-supplied FIN JSON is never
deletion authority. Only a namespace created here, this process's native child
FIN, and a verified external capture can authorize removal of its own resources.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import ctypes
import errno
import hashlib
import os
from pathlib import Path, PurePosixPath
import runpy
import stat
import sys
import threading
import time
import uuid

ROOT = Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import porota_predeploy_cleanup as custody
from scripts import rc6_heavy_test_preflight as capacity

MARKER = ".porota-generated-fixture-owner.json"
MAX_ENTRIES = 100000
MAX_CAPTURE_BYTES = 128 * 1024**2
DRIVER = ROOT / "scripts/rc6_controlled_native_child_manager.py"
DRIVER_SHA256 = "55325b3108e175a42b87ebe544fd307fa45ffd29b6f7ab471443803ad9ba53b8"
_FIN_AUTHORITY = object()
_CAPTURE_AUTHORITY = object()
_CREATED_NAMESPACES = {}
_OWNED_FINS = {}
_FIN_HISTORIES = {}
_OBSERVED_TEARDOWNS = {}
_OBSERVED_FIXTURE_POSTFINALIZERS = {}
_VERIFIED_MANAGER = None


@dataclass(frozen=True)
class GeneratedNamespace:
    path: Path
    binding: dict
    identity: tuple
    mount_id: int
    marker_sha256: str
    marker_identity: tuple
    nonce: str
    marker_path: Path | None = None
    fixture_nodeid_sha256: str | None = None


@dataclass(frozen=True)
class _OwnedFIN:
    authority: object
    namespace_nonce: str
    supervisor_pid: int
    native_tid: int
    kernel: dict
    manager: dict
    final_state: dict
    control_name: str


@dataclass(frozen=True)
class _CapturedEvidence:
    authority: object
    namespace_nonce: str
    path: Path
    identity: tuple
    mount_id: int
    manifest_sha256: str
    files: tuple


@dataclass(frozen=True)
class _InProcessFixtureFIN:
    authority: object
    namespace_nonce: str
    supervisor_pid: int
    native_tid: int
    kernel: dict
    manager: dict
    final_state: dict
    control_path: Path
    control_sha256: str
    infrastructure_lease: object = None


@dataclass(frozen=True)
class _ActualTeardownWitness:
    authority: object
    namespace_nonce: str
    supervisor_pid: int
    native_tid: int
    nodeid: str
    report: dict


@dataclass(frozen=True)
class _ActualFixturePostfinalizer:
    authority: object
    namespace_nonce: str
    supervisor_pid: int
    native_tid: int
    producer_nodeid: str
    fixture_scope: str
    observed_time: float


def _marker_path(namespace):
    return namespace.marker_path or namespace.path / MARKER


def _fresh_own_kernel(manager):
    """Do not let the original wait4 probe steal an unjoined zombie's reap.

    waitid WNOWAIT is nondestructive, including an already exited child. An
    actual ECHILD is required before the unchanged native wait4/ECHILD probe.
    This examines only this process's children, never a host process scan.
    """
    try:
        os.waitid(os.P_ALL, 0, os.WNOHANG | os.WNOWAIT | os.WEXITED)
    except ChildProcessError as error:
        custody.require(error.errno == errno.ECHILD, "FIXTURE_NONDESTRUCTIVE_KERNEL_ECHILD_REQUIRED")
    else:
        raise ValueError("PRE_SOURCE_OWN_CHILD_PRESENT:WNOWAIT_PRECHECK_NO_REAP")
    return manager["pre_capture_kernel_state"]()


def adopt_empty_fixture_namespace(path: Path, binding: dict, *, control_parent: Path,
                                 parent_claim: dict, nodeid: str) -> GeneratedNamespace:
    """Only a fresh empty tmp_path under a declared owner can be authenticated.

    Its marker is external so the original test sees exactly the original empty
    directory. The pytest plugin supplies the actual original FixtureDef result.
    """
    capacity.validate_binding(binding)
    owner = validate_consumer_receipt(parent_claim, candidate_sha=binding["candidate_sha"],
                                     candidate_tree=binding["candidate_tree"])
    path, control_parent = Path(path).absolute(), Path(control_parent).absolute()
    custody.require(path != owner and path.is_relative_to(owner) and control_parent.is_relative_to(owner)
                    and not control_parent.is_relative_to(path) and not path.is_relative_to(control_parent)
                    and type(nodeid) is str and nodeid, "FIXTURE_FRESH_PYTEST_SCOPE_REQUIRED")
    with custody.directory(path, readable=True) as fd:
        details = os.fstat(fd)
        mount = custody.mount_id(fd)
        custody.require(details.st_uid == os.geteuid() and stat.S_IMODE(details.st_mode) == 0o700
                        and mount == parent_claim["mount_id"] and details.st_dev == parent_claim["identity"][0]
                        and not os.listdir(fd), "FIXTURE_PYTEST_ROOT_NOT_FRESH_EMPTY_OWNED")
    with custody.directory(control_parent) as fd:
        control = os.fstat(fd)
        custody.require(control.st_uid == os.geteuid() and stat.S_IMODE(control.st_mode) == 0o700
                        and control.st_dev == details.st_dev and custody.mount_id(fd) == mount,
                        "FIXTURE_EXTERNAL_CONTROL_CUSTODY_INVALID")
    nonce, node_hash = uuid.uuid4().hex, hashlib.sha256(nodeid.encode()).hexdigest()
    marker_path = control_parent / (nonce + ".owner.json")
    marker = {"schema": "porota.rc6.generated-fixture-owner.v1", "binding": binding,
              "namespace_nonce": nonce, "path": str(path), "identity": list(stable_identity(details)),
              "mount_id": mount, "fixture_nodeid_sha256": node_hash,
              "authority_scope": "ORIGINAL_FRESH_PYTEST_TMP_PATH_ONLY", "runtime_paths_authorized": False,
              "real_orders_sent": 0}
    custody.publish(marker_path, marker)
    raw, marker_stat = custody.read_file(marker_path, mode=0o600)
    namespace = GeneratedNamespace(path, dict(binding), stable_identity(details), mount,
        hashlib.sha256(raw).hexdigest(), tuple(custody.identity(marker_stat)), nonce, marker_path, node_hash)
    _CREATED_NAMESPACES[nonce] = namespace
    return namespace


def stable_identity(value):
    return (value.st_dev, value.st_ino, value.st_uid, value.st_gid, stat.S_IMODE(value.st_mode))


def create_namespace(parent: Path, binding: dict) -> GeneratedNamespace:
    capacity.validate_binding(binding)
    parent = Path(parent).absolute()
    nonce_uuid = uuid.uuid4()
    nonce = nonce_uuid.hex
    # Keep all 128 random bits while permitting an authenticated short TMPDIR;
    # never escape to an arbitrary ancestor to satisfy AF_UNIX path budgets.
    name = "r6-" + base64.urlsafe_b64encode(nonce_uuid.bytes).decode("ascii").rstrip("=")
    with custody.directory(parent) as fd:
        before = os.fstat(fd)
        custody.require(before.st_uid == os.geteuid(), "FIXTURE_PARENT_FOREIGN_OWNER")
        mount = custody.mount_id(fd)
        os.mkdir(name, mode=0o700, dir_fd=fd)
        child = os.open(name, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
        try:
            details = os.fstat(child)
            custody.require(details.st_dev == before.st_dev and custody.mount_id(child) == mount
                            and details.st_uid == os.geteuid() and stat.S_IMODE(details.st_mode) == 0o700,
                            "FIXTURE_NEW_NAMESPACE_CUSTODY_INVALID")
        finally:
            os.close(child)
    path = parent / name
    marker = {"schema": "porota.rc6.generated-fixture-owner.v1", "binding": binding,
              "namespace_nonce": nonce, "path": str(path), "identity": list(stable_identity(details)),
              "mount_id": mount, "real_orders_sent": 0, "runtime_paths_authorized": False}
    custody.publish(path / MARKER, marker)
    raw, marker_stat = custody.read_file(path / MARKER, mode=0o600)
    namespace = GeneratedNamespace(path, dict(binding), stable_identity(details), mount,
                                   hashlib.sha256(raw).hexdigest(), tuple(custody.identity(marker_stat)), nonce)
    _CREATED_NAMESPACES[nonce] = namespace
    return namespace


def authenticate(namespace: GeneratedNamespace) -> None:
    custody.require(type(namespace) is GeneratedNamespace, "FIXTURE_AUTHENTICATED_NAMESPACE_REQUIRED")
    custody.require(_CREATED_NAMESPACES.get(namespace.nonce) is namespace,
                    "FIXTURE_NAMESPACE_NOT_CREATED_BY_THIS_OWNER")
    capacity.validate_binding(namespace.binding)
    with custody.directory(namespace.path) as fd:
        details = os.fstat(fd)
        custody.require(stable_identity(details) == namespace.identity and details.st_uid == os.geteuid()
                        and stat.S_IMODE(details.st_mode) == 0o700
                        and custody.mount_id(fd) == namespace.mount_id,
                        "FIXTURE_NAMESPACE_REBOUND")
    raw, marker = custody.read_file(_marker_path(namespace), mode=0o600)
    value = custody.decode(raw)
    custody.require(hashlib.sha256(raw).hexdigest() == namespace.marker_sha256
                    and tuple(custody.identity(marker)) == namespace.marker_identity
                    and value.get("binding") == namespace.binding and value.get("namespace_nonce") == namespace.nonce
                    and value.get("path") == str(namespace.path), "FIXTURE_OWNER_MARKER_CHANGED")


def namespace_receipt(namespace: GeneratedNamespace) -> dict:
    """Child consumption authority only; this JSON never authorizes deletion."""
    authenticate(namespace)
    return {"schema": "porota.rc6.generated-fixture-consumer-binding.v1", "path": str(namespace.path),
            "binding": dict(namespace.binding), "identity": list(namespace.identity),
            "mount_id": namespace.mount_id, "marker_sha256": namespace.marker_sha256,
            "namespace_nonce": namespace.nonce, "cleanup_authority_granted": False}


def validate_consumer_receipt(receipt: dict, *, candidate_sha: str, candidate_tree: str) -> Path:
    """Authenticate an already owned namespace for a child; no free-path fallback."""
    custody.require(type(receipt) is dict
                    and receipt.get("schema") == "porota.rc6.generated-fixture-consumer-binding.v1"
                    and receipt.get("cleanup_authority_granted") is False,
                    "FIXTURE_CONSUMER_BINDING_REQUIRED")
    capacity.validate_binding(receipt.get("binding"))
    custody.require(receipt["binding"]["candidate_sha"] == candidate_sha
                    and receipt["binding"]["candidate_tree"] == candidate_tree,
                    "FIXTURE_CONSUMER_CANDIDATE_MISMATCH")
    path = Path(receipt["path"])
    with custody.directory(path) as fd:
        details = os.fstat(fd)
        custody.require(list(stable_identity(details)) == receipt.get("identity")
                        and details.st_uid == os.geteuid() and stat.S_IMODE(details.st_mode) == 0o700
                        and custody.mount_id(fd) == receipt.get("mount_id"), "FIXTURE_CONSUMER_ROOT_REBOUND")
    raw, _ = custody.read_file(path / MARKER, mode=0o600)
    marker = custody.decode(raw)
    custody.require(hashlib.sha256(raw).hexdigest() == receipt.get("marker_sha256")
                    and marker.get("binding") == receipt["binding"]
                    and marker.get("namespace_nonce") == receipt.get("namespace_nonce")
                    and marker.get("path") == str(path) and marker.get("identity") == receipt["identity"]
                    and marker.get("mount_id") == receipt["mount_id"], "FIXTURE_CONSUMER_MARKER_CHANGED")
    return path


def execute_owned(namespace: GeneratedNamespace, command, *, cwd: Path, environ: dict,
                  log_relative: str = "producer-native.log", timeout_seconds: int = 5400,
                  fin_label: str | None = None):
    """The unchanged native supervisor creates the only accepted live FIN token."""
    authenticate(namespace)
    custody.require(type(timeout_seconds) is int and 1 <= timeout_seconds <= 21600,
                    "FIXTURE_NATIVE_MANAGEMENT_BOUND_REQUIRED")
    custody.require(type(command) is list and command and all(type(x) is str for x in command),
                    "FIXTURE_NATIVE_COMMAND_INVALID")
    _relative(log_relative)
    history = _FIN_HISTORIES.setdefault(namespace.nonce, {})
    if fin_label is not None:
        custody.require(type(fin_label) is str and 0 < len(fin_label) <= 80
                        and all(c.isalnum() or c in "-_" for c in fin_label), "FIXTURE_FIN_LABEL_INVALID")
        fin_name = "producer-owned-fin-" + fin_label + ".json"
    else:
        fin_name = ("producer-owned-fin.json" if not history else
                    "producer-owned-fin-" + str(len(history) + 1).zfill(4) + ".json")
    custody.require(fin_name not in history and not os.path.lexists(namespace.path / fin_name),
                    "FIXTURE_FIN_CONTROL_ALREADY_EXISTS")
    driver_raw, _ = custody.read_file(DRIVER, maximum=1024**2)
    custody.require(hashlib.sha256(driver_raw).hexdigest() == DRIVER_SHA256,
                    "FIXTURE_ORIGINAL_NATIVE_MANAGER_CHANGED")
    manager = runpy.run_path(str(DRIVER))
    initial = _fresh_own_kernel(manager)
    kernel = manager["managed_native_child"](command, Path(cwd), namespace.path / log_relative,
        dict(environ), timeout_seconds, terminate_grace=2, progress_poll=5)
    custody.require(manager["managed_custody_closed"](kernel), "FIXTURE_ACTUAL_OWNED_FIN_NOT_CLOSED")
    final = _fresh_own_kernel(manager)
    custody.require(initial["kernel_echild_verified"] is True and final["kernel_echild_verified"] is True
                    and initial["subreaper_state_after"] == final["subreaper_state_before"]
                    == final["subreaper_state_after"] and kernel.get("supervisor_pid") == os.getpid()
                    and kernel.get("supervisor_native_tid") == threading.get_native_id(),
                    "FIXTURE_ACTUAL_OWN_SUPERVISOR_NOT_RESTORED")
    authenticate(namespace)
    fin = _OwnedFIN(_FIN_AUTHORITY, namespace.nonce, os.getpid(), threading.get_native_id(), kernel, manager, final, fin_name)
    _OWNED_FINS[namespace.nonce] = fin
    custody.publish(namespace.path / fin_name, {
        "schema": "porota.rc6.generated-fixture-owned-fin.v1", "binding": namespace.binding,
        "namespace_nonce": namespace.nonce, "manager_sha256": DRIVER_SHA256, "kernel": kernel,
        "actual_owned_fin_closed": True, "phase_green": manager["managed_phase_green"](kernel),
        "global_or_other_producer_FIN_claimed": False, "real_orders_sent": 0}, compact=True)
    raw, _ = custody.read_file(namespace.path / fin_name, maximum=1024**2, mode=0o600)
    history[fin_name] = hashlib.sha256(raw).hexdigest()
    return kernel, fin


def require_fin(namespace, fin):
    authenticate(namespace)  # no FIN/control read through a rebound namespace.
    if type(fin) is _InProcessFixtureFIN:
        custody.require(fin.authority is _FIN_AUTHORITY and _OWNED_FINS.get(namespace.nonce) is fin
                        and fin.namespace_nonce == namespace.nonce and fin.supervisor_pid == os.getpid()
                        and fin.native_tid == threading.get_native_id(), "FIXTURE_ACTUAL_OWNED_FIN_REQUIRED")
        try:
            current = _fresh_own_kernel(fin.manager)
        except ValueError as error:
            if not str(error).startswith("PRE_SOURCE_OWN_CHILD_PRESENT:") or fin.infrastructure_lease is None:
                raise
            from scripts import rc6_scoped_infrastructure_lease as infrastructure
            infrastructure.require_scoped_lease(namespace, fin.infrastructure_lease, inventory(namespace))
            current = _scoped_kernel_state()
        custody.require(current["subreaper_state_before"] == current["subreaper_state_after"]
                        == fin.final_state["subreaper_state_after"], "FIXTURE_KERNEL_CHANGED_AFTER_FIN")
        raw, _ = custody.read_file(fin.control_path, maximum=1024**2, mode=0o600)
        custody.require(hashlib.sha256(raw).hexdigest() == fin.control_sha256,
                        "FIXTURE_IN_PROCESS_FIN_CONTROL_CHANGED")
        custody.require(custody.decode(raw)["kernel"] == fin.kernel, "FIXTURE_ORIGINAL_FIN_KERNEL_CHANGED")
        return
    custody.require(type(fin) is _OwnedFIN and fin.authority is _FIN_AUTHORITY
                    and _OWNED_FINS.get(namespace.nonce) is fin
                    and fin.namespace_nonce == namespace.nonce and fin.supervisor_pid == os.getpid()
                    and fin.native_tid == threading.get_native_id()
                    and fin.manager["managed_custody_closed"](fin.kernel),
                    "FIXTURE_ACTUAL_OWNED_FIN_REQUIRED")
    current = _fresh_own_kernel(fin.manager)
    custody.require(current["kernel_echild_verified"] is True
                    and current["subreaper_state_before"] == current["subreaper_state_after"]
                    == fin.final_state["subreaper_state_after"], "FIXTURE_KERNEL_CHANGED_AFTER_FIN")
    raw, _ = custody.read_file(namespace.path / fin.control_name, maximum=1024**2, mode=0o600)
    custody.require(hashlib.sha256(raw).hexdigest() == _FIN_HISTORIES[namespace.nonce][fin.control_name]
                    and custody.decode(raw)["kernel"] == fin.kernel, "FIXTURE_ORIGINAL_FIN_KERNEL_CHANGED")


def observe_real_teardown(namespace, *, item, report, call):
    """Hold an opaque compact witness, never item/fixture/log payload objects."""
    import pytest
    from _pytest.runner import CallInfo
    authenticate(namespace)
    custody.require(namespace.fixture_nodeid_sha256 is not None
                    and isinstance(item, pytest.Item) and isinstance(report, pytest.TestReport)
                    and isinstance(call, CallInfo) and report.nodeid == item.nodeid
                    and report.when == call.when == "teardown" and report.passed is True
                    and call.excinfo is None
                    and hashlib.sha256(item.nodeid.encode()).hexdigest() == namespace.fixture_nodeid_sha256,
                    "FIXTURE_ACTUAL_PYTEST_TEARDOWN_REPORT_REQUIRED")
    witness = _ActualTeardownWitness(_FIN_AUTHORITY, namespace.nonce, os.getpid(), threading.get_native_id(),
                                    report.nodeid, {"nodeid": report.nodeid, "when": report.when, "outcome": report.outcome})
    _OBSERVED_TEARDOWNS[namespace.nonce] = witness
    return witness


def observe_real_fixture_post_finalizer(namespace, *, fixturedef, request, producer_nodeid):
    """Observe the original pytest scope finalizer, before its cache clears.

    This event alone never authorizes removal: finalizers may still fail. Only
    the actual complete teardown report covering this event can close a scope.
    """
    from _pytest.fixtures import FixtureDef, SubRequest
    authenticate(namespace)
    custody.require(isinstance(fixturedef, FixtureDef) and isinstance(request, SubRequest)
                    and request._fixturedef is fixturedef and fixturedef.cached_result is not None
                    and fixturedef.cached_result[2] is None and fixturedef.scope == request.scope
                    and hashlib.sha256(producer_nodeid.encode()).hexdigest() == namespace.fixture_nodeid_sha256,
                    "FIXTURE_ORIGINAL_POST_FINALIZER_CONTEXT_REQUIRED")
    observed = _ActualFixturePostfinalizer(_FIN_AUTHORITY, namespace.nonce, os.getpid(),
        threading.get_native_id(), producer_nodeid, fixturedef.scope, time.time())
    _OBSERVED_FIXTURE_POSTFINALIZERS[namespace.nonce] = observed
    return observed


def observe_real_shared_teardown(namespace, *, postfinalizer, item, report, call):
    """Actual PASS teardown of the last consumer, covering original scope FIN."""
    import pytest
    from _pytest.runner import CallInfo
    authenticate(namespace)
    custody.require(type(postfinalizer) is _ActualFixturePostfinalizer
                    and postfinalizer.authority is _FIN_AUTHORITY
                    and _OBSERVED_FIXTURE_POSTFINALIZERS.get(namespace.nonce) is postfinalizer
                    and postfinalizer.namespace_nonce == namespace.nonce
                    and postfinalizer.supervisor_pid == os.getpid()
                    and postfinalizer.native_tid == threading.get_native_id()
                    and isinstance(item, pytest.Item) and isinstance(report, pytest.TestReport)
                    and isinstance(call, CallInfo) and report.nodeid == item.nodeid
                    and report.when == call.when == "teardown" and report.passed is True
                    and call.excinfo is None and call.start <= postfinalizer.observed_time <= call.stop,
                    "FIXTURE_REAL_LAST_CONSUMER_TEARDOWN_REQUIRED")
    witness = _ActualTeardownWitness(_FIN_AUTHORITY, namespace.nonce, os.getpid(),
        threading.get_native_id(), postfinalizer.producer_nodeid,
        {"nodeid": report.nodeid, "when": report.when, "outcome": report.outcome,
         "fixture_scope": postfinalizer.fixture_scope,
         "original_fixture_post_finalizer_observed": True,
         "last_consumer_proven_by_original_scope_teardown": True})
    _OBSERVED_TEARDOWNS[namespace.nonce] = witness
    return witness


def _scoped_kernel_state():
    libc = ctypes.CDLL(None, use_errno=True)
    before, after = ctypes.c_int(), ctypes.c_int()
    custody.require(libc.prctl(37, ctypes.byref(before), 0, 0, 0) == 0 and before.value in (0, 1)
                    and libc.prctl(37, ctypes.byref(after), 0, 0, 0) == 0 and before.value == after.value,
                    "FIXTURE_SCOPED_SUBREAPER_READBACK_CHANGED")
    return {"kernel_echild_verified": False, "subreaper_state_before": before.value,
            "subreaper_state_after": after.value,
            "scope": "THIS_IN_PROCESS_FIXTURE_WITH_POSITIVELY_OWNED_IDLE_SESSION_TRACKER_LEASE_ONLY",
            "whole_phase_or_big_ECHILD_claimed": False}


def finish_inprocess_fixture(namespace, *, witness, control_parent, infrastructure_monitor=None):
    """Fresh kernel ECHILD for THIS pytest fixture; never another PID's wait4 FIN.

    A compact opaque witness from the real hook permits a delayed retry after
    shared infrastructure closes; it does not retain potentially huge funcargs.
    No child reap, transitive/global FIN or governed suite PASS is asserted.
    """
    authenticate(namespace)
    custody.require(type(witness) is _ActualTeardownWitness and witness.authority is _FIN_AUTHORITY
                    and _OBSERVED_TEARDOWNS.get(namespace.nonce) is witness
                    and witness.namespace_nonce == namespace.nonce and witness.supervisor_pid == os.getpid()
                    and witness.native_tid == threading.get_native_id()
                    and hashlib.sha256(witness.nodeid.encode()).hexdigest() == namespace.fixture_nodeid_sha256,
                    "FIXTURE_ACTUAL_PYTEST_TEARDOWN_WITNESS_REQUIRED")
    previous = _OWNED_FINS.get(namespace.nonce)
    if type(previous) is _InProcessFixtureFIN:
        require_fin(namespace, previous)  # fresh actual ECHILD, never rewrite the original control.
        return previous
    raw, _ = custody.read_file(DRIVER, maximum=1024**2)
    custody.require(hashlib.sha256(raw).hexdigest() == DRIVER_SHA256, "FIXTURE_ORIGINAL_NATIVE_MANAGER_CHANGED")
    # Every producer still verifies original bytes. Reuse only the immutable
    # module's functions, avoiding thousands of identical module dictionaries
    # retained by compact FIN receipts during one complete governed phase.
    global _VERIFIED_MANAGER
    if _VERIFIED_MANAGER is None:
        _VERIFIED_MANAGER = runpy.run_path(str(DRIVER))
    manager = _VERIFIED_MANAGER
    infrastructure_lease = None
    try:
        state = _fresh_own_kernel(manager)  # actual own wait4/ECHILD; no stolen zombie reap.
    except ValueError as error:
        if (not str(error).startswith("PRE_SOURCE_OWN_CHILD_PRESENT:") or infrastructure_monitor is None
                or infrastructure_monitor.birth is None):
            raise
        infrastructure_lease = infrastructure_monitor.verify_for_fixture(namespace, inventory(namespace))
        state = _scoped_kernel_state()  # explicitly NOT total phase/BIG ECHILD.
    control_parent = Path(control_parent).absolute()
    custody.require(namespace.marker_path.parent == control_parent, "FIXTURE_FIN_CONTROL_SCOPE_CHANGED")
    control_path = control_parent / (namespace.nonce + ".fixture-fin.json")
    kernel = {"schema": "porota.rc6.in-process-fixture-fin.v1", "scope": "THIS_PYTEST_FIXTURE_PRODUCER_ONLY",
              "fixture_nodeid_sha256": namespace.fixture_nodeid_sha256, "supervisor_pid": os.getpid(),
              "supervisor_native_tid": threading.get_native_id(), "fresh_actual_own_kernel": state,
              "actual_teardown_report_passed": True, "producer_is_in_process": True,
              "native_child_reap_claimed": False, "external_or_global_FIN_claimed": False,
              "original_native_manager_sha256": DRIVER_SHA256, "real_orders_sent": 0}
    if infrastructure_lease is not None:
        observation = infrastructure_lease.observation
        kernel["session_infrastructure_lease"] = {name: observation[name] for name in (
            "kernel_census_verified", "kernel_own_children", "tracker_protocol_pipe_drained",
            "tracker_blocked_in_original_pipe_read", "tracker_namespace_handles",
            "scoped_resource_registrations_outstanding", "whole_phase_kernel_ECHILD_claimed")}
        kernel["session_infrastructure_lease"]["kernel_birth_sha256"] = capacity.digest(observation["kernel_birth"])
    custody.publish(control_path, {"binding": namespace.binding, "namespace_nonce": namespace.nonce,
        "kernel": kernel, "actual_report": witness.report,
        "whole_governed_or_runtime_validation_claimed": False}, compact=True)
    control_raw, _ = custody.read_file(control_path, maximum=1024**2, mode=0o600)
    fin = _InProcessFixtureFIN(_FIN_AUTHORITY, namespace.nonce, os.getpid(), threading.get_native_id(),
                              kernel, manager, state, control_path, hashlib.sha256(control_raw).hexdigest(), infrastructure_lease)
    _OWNED_FINS[namespace.nonce] = fin
    return fin


def _phase_green(fin):
    return (True if type(fin) is _InProcessFixtureFIN else fin.manager["managed_phase_green"](fin.kernel))


def _fin_kind(fin):
    if type(fin) is _InProcessFixtureFIN and fin.infrastructure_lease is not None:
        return "IN_PROCESS_REAL_TEARDOWN_WITH_POSITIVELY_OWNED_IDLE_SESSION_TRACKER_LEASE"
    return ("IN_PROCESS_REAL_TEARDOWN_AND_OWN_KERNEL_ECHILD" if type(fin) is _InProcessFixtureFIN
            else "NATIVE_WAIT4_OWNED_FIN")


def _relative(name):
    custody.require(type(name) is str and 0 < len(name) <= 4096 and "\x00" not in name
                    and str(PurePosixPath(name)) == name and not name.startswith("/")
                    and all(x not in ("", ".", "..") for x in name.split("/")),
                    "FIXTURE_RELATIVE_PATH_INVALID")
    return name


def inventory(namespace):
    """Metadata-only, NOFOLLOW inventory; do not touch symlink targets or payload."""
    authenticate(namespace)
    rows, inodes, hardlink_counts = {}, {}, {}

    def visit(fd, relative):
        before = tuple(custody.identity(os.fstat(fd)))
        names = sorted(os.listdir(fd))
        custody.require(len(rows) + len(names) + 1 <= MAX_ENTRIES, "FIXTURE_RETAINED_NAMESPACE_LIMIT")
        for name in names:
            key = name if not relative else relative + "/" + name
            value = os.stat(name, dir_fd=fd, follow_symlinks=False)
            mounted = custody.require_member_mount(fd, name, namespace.mount_id)
            custody.require(custody.identity(value) == custody.identity(mounted)
                            and value.st_dev == namespace.identity[0] and value.st_uid == namespace.identity[2],
                            "FIXTURE_MEMBER_FOREIGN_CHANGED_OR_MOUNTED")
            rows[key] = {"identity": tuple(custody.identity(value)), "directory": stat.S_ISDIR(value.st_mode),
                         "allocated_bytes": value.st_blocks * 512, "mode": value.st_mode}
            inode = (value.st_dev, value.st_ino)
            inodes[inode] = value.st_blocks * 512
            if stat.S_ISREG(value.st_mode):
                hardlink_counts[inode] = hardlink_counts.get(inode, 0) + 1
            if stat.S_ISDIR(value.st_mode):
                nested = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME | os.O_CLOEXEC,
                                 dir_fd=fd)
                try:
                    custody.require(custody.identity(os.fstat(nested)) == custody.identity(value),
                                    "FIXTURE_DIRECTORY_REBOUND")
                    visit(nested, key)
                finally:
                    os.close(nested)
            custody.require(custody.identity(os.stat(name, dir_fd=fd, follow_symlinks=False))
                            == custody.identity(value), "FIXTURE_MEMBER_CHANGED")
        custody.require(tuple(custody.identity(os.fstat(fd))) == before, "FIXTURE_DIRECTORY_CHANGED")

    with custody.directory(namespace.path, readable=True) as fd:
        root = os.fstat(fd)
        visit(fd, "")
        inodes[(root.st_dev, root.st_ino)] = root.st_blocks * 512
    for row in rows.values():
        if stat.S_ISREG(row["mode"]):
            identity = row["identity"]
            custody.require(hardlink_counts[identity[:2]] == identity[5], "FIXTURE_EXTERNAL_HARDLINK_BLOCKED")
    return {"rows": rows, "retained_entries": len(rows) + 1,
            "allocated_bytes": sum(inodes.values()), "payload_files_read": 0, "link_targets_followed": False}


def capture_required_evidence(namespace, fin, destination: Path, required_paths):
    """Seal explicitly required RAW/controls; capture existence alone is insufficient."""
    require_fin(namespace, fin)
    authenticate(namespace)
    custody.require(type(required_paths) in (list, tuple)
                    and (required_paths or type(fin) is _InProcessFixtureFIN)
                    and len(required_paths) == len(set(required_paths)), "FIXTURE_REQUIRED_CAPTURE_LIST_INVALID")
    sources = {MARKER: _marker_path(namespace)}
    if type(fin) is _InProcessFixtureFIN:
        sources["fixture-owned-fin.json"] = fin.control_path
    else:
        histories = _FIN_HISTORIES.get(namespace.nonce, {})
        custody.require(fin.control_name in histories, "FIXTURE_ACTUAL_FIN_HISTORY_MISSING")
        for name, expected in histories.items():
            raw, _ = custody.read_file(namespace.path / name, maximum=1024**2, mode=0o600)
            custody.require(hashlib.sha256(raw).hexdigest() == expected, "FIXTURE_OWNED_FIN_HISTORY_CHANGED")
            sources[name] = namespace.path / name
    for name in required_paths:
        name = _relative(name)
        if name not in sources:
            sources[name] = namespace.path / name
    names = sorted(sources)
    destination = Path(destination).absolute()
    custody.require(not destination.is_relative_to(namespace.path)
                    and not namespace.path.is_relative_to(destination)
                    and not os.path.lexists(destination), "FIXTURE_CAPTURE_DESTINATION_NOT_FRESH_OR_OVERLAPS")
    with custody.directory(destination.parent) as parent:
        details = os.fstat(parent)
        custody.require(details.st_dev == namespace.identity[0] and details.st_uid == namespace.identity[2]
                        and custody.mount_id(parent) == namespace.mount_id, "FIXTURE_CAPTURE_PARENT_CUSTODY_INVALID")
        os.mkdir(destination.name, mode=0o700, dir_fd=parent)
    files, total = [], 0
    for index, name in enumerate(names):
        source = sources[name]
        with custody.directory(source.parent) as parent:
            fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=parent)
            try:
                before = os.fstat(fd)
                custody.private_stat(before)
                custody.require(before.st_dev == namespace.identity[0] and custody.mount_id(fd) == namespace.mount_id,
                                "FIXTURE_CAPTURE_SOURCE_FOREIGN_OR_MOUNTED")
                total += before.st_size
                custody.require(total <= MAX_CAPTURE_BYTES, "FIXTURE_REQUIRED_CAPTURE_TOO_LARGE")
                target = destination / (str(index).zfill(4) + ".raw")
                output = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
                h, count = hashlib.sha256(), 0
                with os.fdopen(output, "wb") as stream:
                    while True:
                        raw = os.read(fd, 1024**2)
                        if not raw:
                            break
                        count += len(raw)
                        custody.require(count <= before.st_size, "FIXTURE_CAPTURE_SOURCE_GREW")
                        h.update(raw)
                        stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                custody.require(count == before.st_size
                                and custody.identity(before) == custody.identity(os.fstat(fd))
                                == custody.identity(os.stat(source.name, dir_fd=parent, follow_symlinks=False)),
                                "FIXTURE_CAPTURE_SOURCE_CHANGED")
                files.append({"relative_source": name, "capture_file": target.name, "bytes": count,
                              "sha256": h.hexdigest(), "source_identity": custody.identity(before)})
            finally:
                os.close(fd)
    manifest = {"schema": "porota.rc6.generated-fixture-required-capture.v1", "binding": namespace.binding,
                "namespace_nonce": namespace.nonce, "files": files, "total_bytes": total,
                "actual_owned_fin_closed": True, "owned_fin_kind": _fin_kind(fin), "phase_green": _phase_green(fin),
                "kernel_sha256": capacity.digest(fin.kernel), "real_orders_sent": 0}
    custody.publish(destination / "manifest.json", manifest, compact=True)
    raw, _ = custody.read_file(destination / "manifest.json", maximum=4 * 1024**2, mode=0o600)
    with custody.directory(destination) as fd:
        capture = _CapturedEvidence(_CAPTURE_AUTHORITY, namespace.nonce, destination,
                    stable_identity(os.fstat(fd)), custody.mount_id(fd), hashlib.sha256(raw).hexdigest(), tuple(files))
    verify_capture(namespace, capture)
    return capture


def verify_capture(namespace, capture):
    custody.require(type(capture) is _CapturedEvidence and capture.authority is _CAPTURE_AUTHORITY
                    and capture.namespace_nonce == namespace.nonce, "FIXTURE_REQUIRED_CAPTURE_MISSING")
    with custody.directory(capture.path, readable=True) as fd:
        custody.require(stable_identity(os.fstat(fd)) == capture.identity
                        and custody.mount_id(fd) == capture.mount_id == namespace.mount_id,
                        "FIXTURE_REQUIRED_CAPTURE_REBOUND")
        custody.require(set(os.listdir(fd)) == {"manifest.json", *[x["capture_file"] for x in capture.files]},
                        "FIXTURE_REQUIRED_CAPTURE_MEMBERS_CHANGED")
    raw, _ = custody.read_file(capture.path / "manifest.json", maximum=4 * 1024**2, mode=0o600)
    manifest = custody.decode(raw)
    custody.require(hashlib.sha256(raw).hexdigest() == capture.manifest_sha256
                    and manifest.get("binding") == namespace.binding and tuple(manifest["files"]) == capture.files,
                    "FIXTURE_REQUIRED_CAPTURE_MANIFEST_CHANGED")
    for row in capture.files:
        path = capture.path / row["capture_file"]
        with custody.directory(path.parent) as parent:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=parent)
            try:
                before = os.fstat(fd)
                custody.private_stat(before)
                custody.require(before.st_size == row["bytes"], "FIXTURE_REQUIRED_CAPTURE_SIZE_CHANGED")
                h = hashlib.sha256()
                for chunk in iter(lambda: os.read(fd, 1024**2), b""):
                    h.update(chunk)
                custody.require(h.hexdigest() == row["sha256"]
                                and custody.identity(before) == custody.identity(os.fstat(fd))
                                == custody.identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)),
                                "FIXTURE_REQUIRED_CAPTURE_HASH_OR_IDENTITY_CHANGED")
            finally:
                os.close(fd)


def cleanup_namespace(namespace, fin, capture, *, on_directory_retired=None):
    """Revalidate everything before first unlink; never operate on a free path."""
    require_fin(namespace, fin)
    authenticate(namespace)
    verify_capture(namespace, capture)
    for row in capture.files:
        name = row["relative_source"]
        if name == MARKER:
            source = _marker_path(namespace)
        elif type(fin) is _InProcessFixtureFIN and name == "fixture-owned-fin.json":
            source = fin.control_path
        else:
            source = namespace.path / name
        with custody.directory(source.parent) as parent:
            current = os.stat(source.name, dir_fd=parent, follow_symlinks=False)
            custody.require(custody.identity(current) == row["source_identity"],
                            "FIXTURE_REQUIRED_SOURCE_CHANGED_AFTER_CAPTURE")
    before_capacity = capacity.measure_filesystem(namespace.path)
    measured = inventory(namespace)
    custody.require(inventory(namespace) == measured, "FIXTURE_CHANGED_BEFORE_FIRST_UNLINK")
    rows = measured["rows"]
    remaining_link_identities = {}

    def remove(fd, relative):
        for name in sorted(os.listdir(fd)):
            key = name if not relative else relative + "/" + name
            custody.require(key in rows, "FIXTURE_UNKNOWN_MEMBER_BEFORE_UNLINK")
            row = rows[key]
            value = os.stat(name, dir_fd=fd, follow_symlinks=False)
            observed = custody.require_member_mount(fd, name, namespace.mount_id)
            identity = custody.identity(value)
            expected = list(row["identity"])
            inode = tuple(expected[:2])
            if stat.S_ISREG(row["mode"]) and inode in remaining_link_identities:
                expected = remaining_link_identities[inode]
            custody.require(identity == expected and identity == custody.identity(observed),
                            "FIXTURE_MEMBER_CHANGED_BEFORE_UNLINK")
            if row["directory"]:
                nested = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME | os.O_CLOEXEC,
                                 dir_fd=fd)
                try:
                    custody.require(custody.identity(os.fstat(nested)) == identity, "FIXTURE_DIRECTORY_REBOUND_BEFORE_UNLINK")
                    remove(nested, key)
                    custody.require(not os.listdir(nested)
                                    and stable_identity(os.fstat(nested)) == tuple(expected[:5]),
                                    "FIXTURE_DIRECTORY_CHANGED_BEFORE_REMOVE")
                finally:
                    os.close(nested)
                final = os.stat(name, dir_fd=fd, follow_symlinks=False)
                custody.require(stable_identity(final) == tuple(expected[:5]), "FIXTURE_DIRECTORY_NAME_REBOUND")
                os.rmdir(name, dir_fd=fd)
            else:
                pinned = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                try:
                    custody.require(custody.identity(os.fstat(pinned)) == identity
                                    and custody.mount_id(pinned) == namespace.mount_id,
                                    "FIXTURE_MEMBER_REBOUND_AT_UNLINK")
                    os.unlink(name, dir_fd=fd)  # own entry only; no target opened.
                    if stat.S_ISREG(row["mode"]):
                        after_unlink = custody.identity(os.fstat(pinned))
                        custody.require(after_unlink[:5] == identity[:5]
                                        and after_unlink[5] == identity[5] - 1
                                        and after_unlink[6:8] == identity[6:8],
                                        "FIXTURE_HARDLINK_CHANGED_DURING_OWN_UNLINK")
                        remaining_link_identities[inode] = after_unlink
                finally:
                    os.close(pinned)

    with custody.directory(namespace.path, readable=True) as fd:
        remove(fd, "")
        custody.require(not os.listdir(fd), "FIXTURE_NAMESPACE_NOT_EMPTY_AFTER_CLEANUP")
        with custody.directory(namespace.path.parent) as parent:
            custody.require(stable_identity(os.stat(namespace.path.name, dir_fd=parent, follow_symlinks=False))
                            == namespace.identity, "FIXTURE_NAMESPACE_REBOUND_BEFORE_REMOVE")
            os.rmdir(namespace.path.name, dir_fd=parent)
        # The cleaner's own descriptor pins the retired directory inode until
        # an authenticated regular numbering control can take its old name.
        # It is opened AFTER all producer FIN/handle guards, never a producer
        # handle excluded from those guards.
        replacement = on_directory_retired() if on_directory_retired is not None else None
        if replacement is not None:
            raw, value = custody.read_file(namespace.path, mode=0o600)
            custody.require(value.st_ino != namespace.identity[1]
                            and value.st_dev == namespace.identity[0] and value.st_uid == namespace.identity[2]
                            and custody.identity(value) == replacement["identity"]
                            and hashlib.sha256(raw).hexdigest() == replacement["sha256"]
                            and replacement["control"]["schema"] == "porota.rc6.original-factory-numbering-control.v1"
                            and replacement["control"]["binding"] == namespace.binding
                            and replacement["control"]["namespace_nonce"] == namespace.nonce,
                            "FIXTURE_NUMBER_CONTROL_REPLACEMENT_NOT_AUTHENTICATED")
    custody.require((replacement is not None or not os.path.lexists(namespace.path)), "FIXTURE_NAMESPACE_REMAINS")
    after_capacity = capacity.measure_filesystem(namespace.path.parent)
    return {"schema": "porota.rc6.generated-fixture-scoped-cleanup.v1", "binding": namespace.binding,
            "namespace_nonce": namespace.nonce, "namespace_removed": True,
            "actual_owned_fin_closed": True, "owned_fin_kind": _fin_kind(fin), "phase_green": _phase_green(fin),
            "directory_inode_removed": True, "original_namespace_removed": True,
            "path_replaced_by_authenticated_numbering_control": replacement is not None,
            "original_path_is_nonexistent": replacement is None,
            "capture_manifest_sha256": capture.manifest_sha256, "capture_path": str(capture.path),
            "retained_entries_before": measured["retained_entries"], "allocated_bytes_before": measured["allocated_bytes"],
            "capacity_before": before_capacity, "capacity_after": after_capacity,
            "link_targets_followed": False, "foreign_paths_removed": 0, "global_cleanup_claimed": False,
            "runtime_paths_authorized": False, "real_orders_sent": 0}
