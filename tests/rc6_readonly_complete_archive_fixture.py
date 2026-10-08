"""One owned complete Source input for the two readonly consumer modules.

The IPC module keeps its original mutable, module-scoped complete_archive.
This pytest plugin has one session FixtureDef, not a module singleton cache.
Source bytes, Git modes, blobs and each native custody proof remain governed
by the original consumers. These additional leases observe all11 metadata;
ordinary CODE/directory atime observations are recorded without resets.
No DATA is warmed, no retained fixture is removed and no resource cap changes.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import inspect
import json
import os
from pathlib import Path, PurePosixPath
import stat
from threading import Lock

import pytest

from tests.rc6_browser_ipc import protected_bytes
from tests.test_rc6_browser_product_ipc import complete_archive as _ipc_archive

_FIELDS = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink",
           "st_size", "st_blocks", "st_atime_ns", "st_mtime_ns", "st_ctime_ns")
_CODE_INPUTS = frozenset(("requirements.lock.txt", "requirements.build.lock.txt"))
_ORIGINAL_PROVIDER = _ipc_archive.__wrapped__
_ORIGINAL_PROVIDER_CODE = _ORIGINAL_PROVIDER.__code__
_ORIGINAL_PROVIDER_SOURCE_SHA256 = "c53e68cd4e3d808bb2dfbd830f9df7540bd5a1e7f7f67999f7ac4836b2318bab"
_CONSTRUCTION_AUTHORITY = object()


@dataclass(frozen=True)
class _OriginalConstruction:
    authority: object
    registration: object
    source_triple: tuple
    base_identity: tuple
    root_identity: tuple
    source_repository: Path
    original_factory: dict
    factory: object


def _identity5(value):
    return (value.st_dev, value.st_ino, value.st_uid, value.st_gid, value.st_mode)


def _empty_owned_directory(path, *, private=False):
    from scripts import porota_predeploy_cleanup as custody
    anchor = _open_directory(Path(path).absolute())
    readable = None
    try:
        info = os.fstat(anchor)
        _require(info.st_uid == os.geteuid() and info.st_gid == os.getegid()
                 and (not private or stat.S_IMODE(info.st_mode) == 0o700),
                 "READONLY_SOURCE_FRESH_CONSTRUCTION_DIRECTORY_OWNER")
        readable = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME | os.O_CLOEXEC,
                           dir_fd=anchor)
        _require(_all11(info) == _all11(os.fstat(readable)) and not os.listdir(readable),
                 "READONLY_SOURCE_FRESH_CONSTRUCTION_DIRECTORY_NOT_EMPTY")
        return _identity5(info), custody.mount_id(anchor)
    finally:
        if readable is not None:
            os.close(readable)
        os.close(anchor)


def _require(value, reason):
    if not value:
        raise RuntimeError(reason)


def _all11(info):
    return {name: getattr(info, name) for name in _FIELDS}


def _open_directory(path):
    """Anchor every component without following any link or reading contents."""
    path = Path(path)
    _require(path.is_absolute() and ".." not in path.parts, "READONLY_SOURCE_PATH_CONTEXT")
    descriptor = os.open("/", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _stat_member(parent, relative, uid, device, kind):
    parts = PurePosixPath(relative).parts
    _require(parts and all(part not in {"", ".", ".."} for part in parts),
             "READONLY_SOURCE_MEMBER_CONTEXT")
    descriptor = os.dup(parent)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            info = os.fstat(descriptor)
            _require(info.st_uid == uid and info.st_dev == device, "READONLY_SOURCE_PARENT_OWNER")
        child = os.open(parts[-1], os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor)
        try:
            info = os.fstat(child)
            _require(info.st_uid == uid and info.st_dev == device, "READONLY_SOURCE_MEMBER_OWNER")
            _require(stat.S_ISDIR(info.st_mode) if kind == "directory" else
                     stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                     "READONLY_SOURCE_MEMBER_ALIAS_OR_TYPE")
            return _all11(info)
        finally:
            os.close(child)
    finally:
        os.close(descriptor)


class ArchiveRegistration:
    """Session-owned control observations, never borrowed mutable Source state."""
    def __init__(self):
        self.pid, self.uid = os.getpid(), os.geteuid()
        self.ipc_roots, self.readonly_leases = [], []
        self.original_constructions = []
        self.consumed_constructions = []

    def construct_original_source(self, tmp_path_factory):
        """Observe a genuine EMPTY mktemp before the unchanged original producer.

        The returned opaque single-use token authorizes a flush of only that
        newly generated Source. An arbitrary Source tuple or JSON never does.
        """
        from scripts.rc6_pytest_fixture_lifecycle import require_original_factory_forwarder
        self.context()
        factory_witness = require_original_factory_forwarder(tmp_path_factory)
        _require(_ipc_archive.__wrapped__ is _ORIGINAL_PROVIDER
                 and _ORIGINAL_PROVIDER.__code__ is _ORIGINAL_PROVIDER_CODE
                 and hashlib.sha256(inspect.getsource(_ORIGINAL_PROVIDER).encode()).hexdigest()
                 == _ORIGINAL_PROVIDER_SOURCE_SHA256, "READONLY_SOURCE_ORIGINAL_PRODUCER_CHANGED")
        repository = Path(_ORIGINAL_PROVIDER.__globals__["ROOT"]).absolute()
        created = []

        class ObservedOriginalFactory:
            def mktemp(_self, *args, **kwargs):
                path = tmp_path_factory.mktemp(*args, **kwargs)
                identity, mount = _empty_owned_directory(path)
                _require(len(created) == 0 and path != repository
                         and not path.is_relative_to(repository) and not repository.is_relative_to(path),
                         "READONLY_SOURCE_NEW_NAMESPACE_OVERLAPS_REPOSITORY")
                created.append((Path(path).absolute(), identity, mount))
                return path  # Exact original result and original arguments.

        triple = _ORIGINAL_PROVIDER(ObservedOriginalFactory())
        _require(type(triple) is tuple and len(triple) == 3 and len(created) == 1,
                 "READONLY_SOURCE_ORIGINAL_CONSTRUCTION_RESULT_REQUIRED")
        base, identity, mount = created[0]
        root, index, _tree = triple
        _require(Path(root).absolute() == base / "source" and Path(index).absolute() == base / "source.index.json",
                 "READONLY_SOURCE_ORIGINAL_NAMESPACE_RESULT_REBOUND")
        from scripts import porota_predeploy_cleanup as custody
        anchor = _open_directory(base)
        root_anchor = _open_directory(root)
        try:
            _require(_identity5(os.fstat(anchor)) == identity and custody.mount_id(anchor) == mount
                     and custody.mount_id(root_anchor) == mount,
                     "READONLY_SOURCE_ORIGINAL_CONSTRUCTION_NAMESPACE_REBOUND")
            root_identity = _identity5(os.fstat(root_anchor))
        finally:
            os.close(root_anchor)
            os.close(anchor)
        token = _OriginalConstruction(_CONSTRUCTION_AUTHORITY, self, triple, identity, root_identity,
                                      repository, factory_witness, tmp_path_factory)
        self.original_constructions.append(token)
        return triple, token

    def claim_original_construction(self, token, triple, control_root, index_record):
        from scripts.rc6_pytest_fixture_lifecycle import require_original_factory_forwarder
        self.context()
        _require(type(token) is _OriginalConstruction and token.authority is _CONSTRUCTION_AUTHORITY
                 and token.registration is self and any(token is value for value in self.original_constructions)
                 and not any(token is value for value in self.consumed_constructions)
                 and tuple(triple) == token.source_triple and not self.readonly_leases,
                 "READONLY_SOURCE_FRESH_SINGLE_USE_ORIGINAL_CONSTRUCTION_REQUIRED")
        _require(require_original_factory_forwarder(token.factory) == token.original_factory,
                 "READONLY_SOURCE_ORIGINAL_FACTORY_BINDING_CHANGED")
        _empty_owned_directory(control_root, private=True)
        root = Path(triple[0]).absolute()
        controls = Path(control_root).absolute()
        _require(controls != root.parent and controls != root and not controls.is_relative_to(root.parent)
                 and not root.parent.is_relative_to(controls)
                 and root != token.source_repository and not root.is_relative_to(token.source_repository),
                 "READONLY_SOURCE_CONSTRUCTION_CONTROL_OR_REPOSITORY_OVERLAP")
        base, anchor = _open_directory(root.parent), _open_directory(root)
        readable = None
        try:
            readable = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME | os.O_CLOEXEC,
                               dir_fd=base)
            _require(_identity5(os.fstat(base)) == token.base_identity
                     and _identity5(os.fstat(anchor)) == token.root_identity
                     and set(os.listdir(readable)) == {"source", "source.tar", "source.commit.raw", "source.index.json"},
                     "READONLY_SOURCE_ORIGINAL_CONSTRUCTION_LAYOUT_CHANGED")
        finally:
            if readable is not None:
                os.close(readable)
            os.close(anchor)
            os.close(base)
        binding = token.original_factory.get("binding")
        if binding is not None:
            _require(index_record["source_sha"] == binding["candidate_sha"]
                     and index_record["source_tree"] == binding["candidate_tree"],
                     "READONLY_SOURCE_ORIGINAL_CONSTRUCTION_CANDIDATE_REBOUND")
        self.consumed_constructions.append(token)  # A RED attempt cannot reuse it.
        return {"original_producer_source_sha256": _ORIGINAL_PROVIDER_SOURCE_SHA256,
                "original_factory_empty_before_producer": True, "single_use_original_construction": True,
                "base_identity": list(token.base_identity), "source_identity": list(token.root_identity),
                "flush_authority_scope": "THIS_ORIGINAL_NEW_GENERATED_ARCHIVE_ONLY"}

    def context(self):
        _require(self.pid == os.getpid() and self.uid == os.getuid() == os.geteuid()
                 and self.uid != 0, "READONLY_SOURCE_OWNER_CONTEXT")

    def observe_original_ipc_setup(self, fixturedef, request, triple):
        self.context()
        descriptor = _open_directory(triple[0])
        try:
            info = os.fstat(descriptor)
            _require(info.st_uid == self.uid, "READONLY_SOURCE_IPC_OWNER")
            self.ipc_roots.append({"scope": fixturedef.scope, "module": request.module.__name__,
                                   "root_all11_at_owned_setup": _all11(info)})
        finally:
            os.close(descriptor)


class ReadonlyArchive:
    def __init__(self, triple, registry, control_root, *, construction=None):
        self.triple, self.registry = triple, registry
        self.root, self.index, self.tree = triple
        self.root, self.index = Path(self.root), Path(self.index)
        self.control_root = Path(control_root)
        self.pid, self.uid = os.getpid(), os.geteuid()
        self.state, self.active_owner, self.sequence = "READY", None, 0
        self.pending_lease = None
        self.last_source_drift = None
        self.lock = Lock()
        registry.context()
        _require(self.index.parent == self.root.parent and self.index.name == "source.index.json",
                 "READONLY_SOURCE_INDEX_CONTEXT")
        row = json.loads(protected_bytes(self.index))
        _require(row["schema"] == "rc6.complete-archive-source-pin.v1" and row["overlay_count"] == 0
                 and row["source_tree"] == self.tree and set(row["files"]) == set(row["modes"])
                 == set(row["blob_ids"]), "READONLY_SOURCE_COMPLETE_INDEX")
        self.index_record = row
        self.paths = tuple(sorted(row["files"]))
        _require(self.paths and all(not PurePosixPath(name).is_absolute() and ".." not in
                 PurePosixPath(name).parts for name in self.paths), "READONLY_SOURCE_INDEX_PATHS")
        directories = {"."}
        for name in self.paths:
            directories.update(str(parent) for parent in PurePosixPath(name).parents if str(parent) != ".")
        self.directories = tuple(sorted(directories))
        control = _open_directory(self.control_root)
        try:
            info = os.fstat(control)
            _require(info.st_uid == self.uid and stat.S_IMODE(info.st_mode) == 0o700,
                     "READONLY_SOURCE_CONTROL_OWNER")
            self.control_device = info.st_dev
        finally:
            os.close(control)
        # Git's archive stdout and tar extraction may have dirty delayed extents
        # even after their writers have closed. Freeze only after flushing this
        # newly constructed, owned input. Never sync an existing lease to hide
        # drift; st_blocks stays part of the unchanged Source10 guard.
        if construction is None:
            # Explicit metadata-fixture callers may exercise the unchanged
            # Source10/11 guards. Their tuples confer no permission to sync a
            # Source and cannot claim productive construction quiescence.
            self.construction_quiescence = {"status": "BLOCKED_NO_ORIGINAL_CONSTRUCTION_AUTHORITY",
                "files_fsynced_before_initial_snapshot": 0, "source_payload_bytes_read": 0,
                "metadata_fields_removed_from_guard": [], "global_sync_performed": False,
                "post_freeze_sync_or_stat_reset": False, "native_kernel_FIN_claimed": False}
        else:
            authority = registry.claim_original_construction(construction, triple, control_root, row)
            self.construction_quiescence = self._settle_owned_construction()
            self.construction_quiescence.update(status="OWN_ORIGINAL_CONSTRUCTION_SYNCED_BEFORE_FREEZE",
                                                construction_authority=authority)
        self.initial = self._snapshot()

    def _settle_owned_construction(self):
        """Flush only creator-owned regular files/directories before first lease.

        No Source payload read, atime reset, global sync or unowned target access
        occurs. This does not assert producer/kernel FIN or fixture quota GREEN.
        """
        from scripts import porota_predeploy_cleanup as custody
        root = _open_directory(self.root)
        parent = _open_directory(self.root.parent)
        flushed, allocation_changes = 0, []
        try:
            mount = custody.mount_id(root)
            _require(custody.mount_id(parent) == mount, "READONLY_SOURCE_CONSTRUCTION_MOUNT_CHANGED")

            def flush_file(base, name):
                nonlocal flushed
                parts = PurePosixPath(name).parts
                anchor = os.dup(base)
                descriptor = None
                try:
                    for part in parts[:-1]:
                        child = os.open(part, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                        dir_fd=anchor)
                        os.close(anchor)
                        anchor = child
                        details = os.fstat(anchor)
                        _require(details.st_uid == self.uid and details.st_dev == self.control_device
                                 and custody.mount_id(anchor) == mount, "READONLY_SOURCE_CONSTRUCTION_PARENT_CUSTODY")
                    descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME |
                                         os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=anchor)
                    before = os.fstat(descriptor)
                    _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                             and before.st_uid == self.uid and before.st_dev == self.control_device
                             and custody.mount_id(descriptor) == mount,
                             "READONLY_SOURCE_CONSTRUCTION_FILE_CUSTODY")
                    os.fsync(descriptor)
                    after = os.fstat(descriptor)
                    # Own flush may finish physical allocation. It may not
                    # change identity, bytes length, permissions or timestamps.
                    _require(all(getattr(before, field) == getattr(after, field)
                                 for field in _FIELDS if field != "st_blocks")
                             and _all11(after) == _all11(os.stat(parts[-1], dir_fd=anchor, follow_symlinks=False)),
                             "READONLY_SOURCE_CHANGED_DURING_CONSTRUCTION_FLUSH")
                    flushed += 1
                    if before.st_blocks != after.st_blocks:
                        allocation_changes.append({"member": name, "before_st_blocks": before.st_blocks,
                                                   "after_st_blocks": after.st_blocks,
                                                   "scope": "OWN_CONSTRUCTION_BEFORE_SOURCE_FREEZE"})
                finally:
                    if descriptor is not None:
                        os.close(descriptor)
                    os.close(anchor)

            for name in self.paths:
                flush_file(root, name)
            for name in ("source.index.json", "source.tar", "source.commit.raw"):
                flush_file(parent, name)
            for name in sorted(self.directories, key=lambda value: value.count("/"), reverse=True):
                directory = self.root if name == "." else self.root / name
                anchor = _open_directory(directory)
                readable = None
                try:
                    before = os.fstat(anchor)
                    _require(before.st_uid == self.uid and before.st_dev == self.control_device
                             and custody.mount_id(anchor) == mount, "READONLY_SOURCE_CONSTRUCTION_DIRECTORY_CUSTODY")
                    readable = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME | os.O_CLOEXEC,
                                       dir_fd=anchor)
                    _require(_all11(os.fstat(readable)) == _all11(before), "READONLY_SOURCE_CONSTRUCTION_DIRECTORY_REBOUND")
                    os.fsync(readable)
                    _require(_all11(os.fstat(readable)) == _all11(before), "READONLY_SOURCE_CONSTRUCTION_DIRECTORY_CHANGED")
                finally:
                    if readable is not None:
                        os.close(readable)
                    os.close(anchor)
        finally:
            os.close(parent)
            os.close(root)
        return {"schema": "rc6.readonly-complete-source-construction-quiescence.v1",
                "owner_pid": self.pid, "owner_uid": self.uid, "files_fsynced_before_initial_snapshot": flushed,
                "allocation_changes_before_freeze": allocation_changes, "source_payload_bytes_read": 0,
                "metadata_fields_removed_from_guard": [], "global_sync_performed": False,
                "post_freeze_sync_or_stat_reset": False, "native_kernel_FIN_claimed": False,
                "real_orders_sent": 0}

    def _context(self, required_state="READY"):
        try:
            self.registry.context()
            _require(self.pid == os.getpid() and self.uid == os.getuid() == os.geteuid(),
                     "READONLY_SOURCE_OWNER_CONTEXT")
            _require(self.state == required_state, "READONLY_SOURCE_UNKNOWN_OR_UNCLOSED")
        except BaseException:
            self.state = "UNKNOWN_OR_UNCLOSED"
            raise

    def _snapshot(self):
        """Metadata only: no directory listing, Source bytes or link targets."""
        root = _open_directory(self.root)
        parent = None
        try:
            parent = _open_directory(self.root.parent)
            info = os.fstat(root)
            _require(info.st_uid == self.uid and info.st_dev == self.control_device,
                     "READONLY_SOURCE_ROOT_OWNER")
            parent_info = os.fstat(parent)
            _require(parent_info.st_uid == self.uid and parent_info.st_dev == info.st_dev,
                     "READONLY_SOURCE_PARENT_OWNER")
            result = {"source/.": _all11(info)}
            for name in self.directories:
                if name != ".":
                    result["source/" + name + "/"] = _stat_member(root, name, self.uid, info.st_dev, "directory")
            for name in self.paths:
                row = _stat_member(root, name, self.uid, info.st_dev, "file")
                _require(stat.S_IMODE(row["st_mode"]) == int(self.index_record["modes"][name][-3:], 8),
                         "READONLY_SOURCE_GIT_MODE_CHANGED")
                result["source/" + name] = row
            for name in ("source.index.json", "source.tar", "source.commit.raw"):
                result[name] = _stat_member(parent, name, self.uid, info.st_dev, "file")
            return result
        finally:
            if parent is not None:
                os.close(parent)
            os.close(root)

    def _compare(self, before, after):
        _require(before.keys() == after.keys(), "READONLY_SOURCE_MEMBER_SET_CHANGED")
        observations = []
        for name in before:
            changed = [field for field in _FIELDS if field != "st_atime_ns"
                       and before[name][field] != after[name][field]]
            if changed:
                self.last_source_drift = {
                    "schema": "rc6.readonly-source10-drift.v1",
                    "member_name_sha256": hashlib.sha256(name.encode()).hexdigest(),
                    "changed_fields": changed,
                    "before_all11": before[name], "after_all11": after[name],
                    "source_payload_bytes_read_for_diagnostic": 0,
                    "fields_removed_or_reset": [], "classification": "IRREVERSIBLE_SOURCE10_RED"}
                raise RuntimeError("READONLY_SOURCE10_CHANGED")
            if before[name]["st_atime_ns"] != after[name]["st_atime_ns"]:
                code = name.startswith("source/") and (stat.S_ISDIR(before[name]["st_mode"])
                       or name.endswith(".py") or name[len("source/"):] in _CODE_INPUTS)
                _require(code, "READONLY_NON_CODE_ATIME_CHANGED")
                observations.append({"member": name, "before_atime_ns": before[name]["st_atime_ns"],
                                     "after_atime_ns": after[name]["st_atime_ns"],
                                     "scope": "OBSERVED_ORDINARY_CODE_OR_SOURCE_DIRECTORY_READ"})
        return observations

    def _publish(self, row):
        control = _open_directory(self.control_root)
        try:
            info = os.fstat(control)
            _require(info.st_uid == self.uid and info.st_dev == self.control_device
                     and stat.S_IMODE(info.st_mode) == 0o700, "READONLY_SOURCE_CONTROL_OWNER")
            raw = (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
            descriptor = os.open(f"lease-{self.sequence:04d}.json", os.O_WRONLY | os.O_CREAT |
                                 os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=control)
            try:
                with os.fdopen(descriptor, "wb", closefd=False) as output:
                    output.write(raw)
                    output.flush()
                    os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            os.close(control)

    @contextmanager
    def lease(self, module, failure_count):
        """Defer all post-Source metadata until the real full teardown report."""
        self._context()
        claimed, row = False, None
        try:
            _require(self.lock.acquire(blocking=False), "READONLY_SOURCE_LEASE_ALREADY_ACTIVE")
            claimed = True
            self.sequence += 1
            self.active_owner = module
            baseline_failures = failure_count()
            row = {"schema": "rc6.readonly-complete-archive-module-lease.v2", "module": module,
                   "owner_pid": self.pid, "owner_uid": self.uid, "sequence": self.sequence,
                   "source_sha": self.index_record["source_sha"], "source_tree": self.tree,
                   "complete_source_files": len(self.paths), "complete_source_directories": len(self.directories),
                   "construction_quiescence": self.construction_quiescence,
                   "original_consumer_hash_blob_mode_and_FIN_guards_changed": False,
                   "retention_policy": "EXPLICIT_RETAINED_FOR_EPHEMERAL_RUNNER_TEARDOWN",
                   "fixture_root_removed": False, "native_gate_or_artifact_qualification_claimed": False,
                   "real_orders_sent": 0, "post_failure_source_reads": 0,
                   "source_payload_bytes_read_by_module_lease": 0}
            before = self._snapshot()
            opening_observations = self._compare(self.initial, before)
            row["before_all11_sha256"] = hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()
            yield self.triple
            self._context()
            _require(self.active_owner == module, "READONLY_SOURCE_LEASE_CONTEXT_REBOUND")
            _require(failure_count() == baseline_failures, "READONLY_MODULE_FAILED_POSTREAD_VETO")
            self.pending_lease = {"module": module, "failure_count": failure_count,
                                  "baseline_failures": baseline_failures, "row": row,
                                  "before": before, "opening_observations": opening_observations}
            self.state = "PENDING_REPORT"
        except BaseException as error:
            self.state = "UNKNOWN_OR_UNCLOSED"
            if row is not None:
                row.update(status="UNKNOWN_OR_UNCLOSED", failure_class=type(error).__name__,
                           Source10_unchanged=None, Source11_unchanged=False)
                if self.last_source_drift is not None:
                    row["source10_drift"] = self.last_source_drift
                self._publish(row)
            raise
        finally:
            if claimed:
                self.active_owner = None
                self.lock.release()

    def failed_real_report(self):
        """Sticky veto only; this path never opens or snapshots Source."""
        self.state = "UNKNOWN_OR_UNCLOSED"
        pending, self.pending_lease = self.pending_lease, None
        if pending is not None:
            row = pending["row"]
            row.update(status="UNKNOWN_OR_UNCLOSED", failure_class="REAL_PYTEST_PHASE_FAILED",
                       Source10_unchanged=None, Source11_unchanged=False,
                       actual_pytest_teardown_report_passed=False)
            self._publish(row)

    def finish_from_real_report(self, item, report, call):
        """Only the actual full teardown result permits the pending postread."""
        if report.failed or call.excinfo is not None:
            self.failed_real_report()
            return
        if report.when != "teardown" or self.pending_lease is None:
            return
        pending = self.pending_lease
        row = pending["row"]
        try:
            self._context("PENDING_REPORT")
            _require(isinstance(report, pytest.TestReport) and report.nodeid == item.nodeid
                     and report.passed is True and call.when == "teardown"
                     and item.module.__name__ == pending["module"],
                     "READONLY_SOURCE_REAL_TEARDOWN_REPORT_CONTEXT")
            _require(pending["failure_count"]() == pending["baseline_failures"],
                     "READONLY_MODULE_FAILED_POSTREAD_VETO")
            after = self._snapshot()
            observations = self._compare(pending["before"], after)
            self.initial = after
            row.update(status="SOURCE10_CLOSED", Source10_unchanged=True,
                       Source11_unchanged=not observations and not pending["opening_observations"],
                       CODE_atime_observations=pending["opening_observations"] + observations,
                       actual_pytest_teardown_report_passed=True,
                       report_nodeid_length=len(report.nodeid),
                       report_nodeid_sha256=hashlib.sha256(report.nodeid.encode()).hexdigest(),
                       after_all11_sha256=hashlib.sha256(json.dumps(after, sort_keys=True).encode()).hexdigest())
            self._publish(row)
            self.registry.readonly_leases.append({"module": pending["module"], "provider_id": id(self),
                "root_all11_at_owned_boundary": pending["before"]["source/."], "status": "SOURCE10_CLOSED"})
            self.pending_lease = None
            self.state = "READY"
        except BaseException as error:
            self.pending_lease = None
            self.state = "UNKNOWN_OR_UNCLOSED"
            row.update(status="UNKNOWN_OR_UNCLOSED", failure_class=type(error).__name__,
                       Source10_unchanged=None, Source11_unchanged=False)
            if self.last_source_drift is not None:
                row["source10_drift"] = self.last_source_drift
            self._publish(row)
            raise


@pytest.fixture(scope="session")
def rc6_archive_registration():
    return ArchiveRegistration()


@pytest.fixture(scope="session")
def rc6_readonly_complete_archive(tmp_path_factory, rc6_archive_registration):
    triple, construction = rc6_archive_registration.construct_original_source(tmp_path_factory)
    controls = tmp_path_factory.mktemp("readonly-complete-source-controls")
    return ReadonlyArchive(triple, rc6_archive_registration, controls, construction=construction)


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef, request):
    outcome = yield
    original = _ipc_archive.__wrapped__
    if outcome.excinfo is None and fixturedef.argname == "complete_archive" and fixturedef.func is original:
        registration = request.getfixturevalue("rc6_archive_registration")
        registration.observe_original_ipc_setup(fixturedef, request, outcome.get_result())


def _owned_report_providers(item):
    """Use actual item fixture values, never a module-level provider cache."""
    providers = []
    for value in (getattr(item, "funcargs", None) or {}).values():
        if isinstance(value, ReadonlyArchive) and not any(value is prior for prior in providers):
            providers.append(value)
    return providers


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    providers = _owned_report_providers(item)
    outcome = yield
    if outcome.excinfo is not None:
        for provider in providers:
            provider.failed_real_report()
        return
    report = outcome.get_result()
    for provider in providers:
        try:
            provider.finish_from_real_report(item, report, call)
        except BaseException as error:
            # Preserve a real Source/owner failure as RED, never replace it with
            # a passing report or synthesize a kernel/producer finalization.
            outcome.force_exception(error)
            return
