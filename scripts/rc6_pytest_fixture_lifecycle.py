"""Retire original fresh factory fixtures after their last real consumer FIN.

No fixture implementation, test selection, test result or global finalizer is
replaced. Unknown children, open namespace handles, live writer threads, failed
reports or pending Source leases preserve the namespace and produce a blocker.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import errno
import hashlib
import inspect
import os
from pathlib import Path
import threading
import weakref

import pytest
from _pytest import tmpdir

from scripts import porota_predeploy_cleanup as custody
from scripts import rc6_authenticated_fixture_lifecycle as lifecycle
from scripts import rc6_heavy_test_preflight as capacity

_ORIGINAL_FACTORY_WITNESSES = {}


def require_original_factory_forwarder(factory):
    """Prove this instance still forwards the actual original factory method."""
    custody.require(type(factory) is tmpdir.TempPathFactory, "FIXTURE_ORIGINAL_TEMP_FACTORY_REQUIRED")
    if getattr(factory.mktemp, "__func__", None) is tmpdir.TempPathFactory.mktemp:
        return {"original_factory_method": True, "authenticated_lifecycle_forwarder": False}
    witness = _ORIGINAL_FACTORY_WITNESSES.get(id(factory))
    custody.require(witness is not None and witness[0]() is factory and factory.mktemp is witness[2]
                    and getattr(witness[3], "__func__", None) is tmpdir.TempPathFactory.mktemp,
                    "FIXTURE_ORIGINAL_FACTORY_FORWARDER_NOT_AUTHENTICATED")
    owner = witness[1]
    owner._context()
    lifecycle.validate_consumer_receipt(owner.parent_claim,
        candidate_sha=owner.binding["candidate_sha"], candidate_tree=owner.binding["candidate_tree"])
    return {"original_factory_method": True, "authenticated_lifecycle_forwarder": True,
            "binding": dict(owner.binding)}


@dataclass
class FixtureScope:
    namespace: lifecycle.GeneratedNamespace
    baseline_threads: frozenset
    witness: object = None
    failed: bool = False
    leases: list = field(default_factory=list)
    required_raw: list = field(default_factory=list)
    status: str = "IN_PROGRESS"
    blockers: list = field(default_factory=list)
    receipt: dict = None
    fin: object = None
    capture: object = None
    owner_key: tuple = None
    producer_nodeid: str = None
    fixture_scope: str = "function"
    postfinalizer: object = None
    control_provider: object = None
    closed_lease_observations: dict = field(default_factory=dict)
    control_provider_observation: dict = None
    numbering: dict = None
    numbering_receipt: dict = None


def namespace_handles(namespace, measured):
    """Own process FD metadata only; never read payload or expose foreign paths."""
    roots = {(namespace.identity[0], namespace.identity[1])}
    roots.update(tuple(row["identity"][:2]) for row in measured["rows"].values())
    references = []
    # This proc view belongs to THIS process; it is not a global process scan.
    for name in os.listdir("/proc/self/fd"):
        if not name.isdecimal():
            continue
        fd = int(name)
        try:
            details = os.fstat(fd)
            target = os.readlink("/proc/self/fd/" + name)
        except OSError as error:
            if error.errno in (errno.ENOENT, errno.EBADF):
                continue
            raise
        if ((details.st_dev, details.st_ino) in roots or target == str(namespace.path)
                or target.startswith(str(namespace.path) + "/")):
            references.append({"fd": fd, "device": details.st_dev, "inode": details.st_ino,
                               "scope": "OPEN_OWN_NAMESPACE_HANDLE_METADATA_ONLY"})
    return references


class FixtureLifecyclePlugin:
    def __init__(self, parent_claim, control_root: Path, *, candidate_sha, candidate_tree):
        self.owner = lifecycle.validate_consumer_receipt(parent_claim,
            candidate_sha=candidate_sha, candidate_tree=candidate_tree)
        self.parent_claim, self.binding = parent_claim, dict(parent_claim["binding"])
        self.control_root = Path(control_root).absolute()
        custody.require(self.control_root.is_relative_to(self.owner) and self.control_root != self.owner
                        and not os.path.lexists(self.control_root), "FIXTURE_LIFECYCLE_FRESH_CONTROL_ROOT_REQUIRED")
        self.control_root.mkdir(mode=0o700)
        self.pid, self.tid = os.getpid(), threading.get_native_id()
        self.scopes = {}
        self.start_failures = []
        self._fixture_stack = []
        self._fixture_owners = {}
        self._factory_wrappers = []
        self._current_item = None
        self._setup_sequence = 0
        from scripts.rc6_scoped_infrastructure_lease import SessionResourceTrackerLease
        self.infrastructure_monitor = SessionResourceTrackerLease(self.binding)
        self.infrastructure_monitor.install()

    def _context(self):
        custody.require(os.getpid() == self.pid and threading.get_native_id() == self.tid,
                        "FIXTURE_LIFECYCLE_OWNER_CONTEXT_CHANGED")

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_fixture_setup(self, fixturedef, request):
        self._context()
        key = (id(fixturedef), id(request))
        self._setup_sequence += 1
        producer = request.node.nodeid + "::fixture(" + fixturedef.argname + "/" + fixturedef.scope + "/" + str(self._setup_sequence) + ")"
        record = {"key": key, "fixture_id": id(fixturedef), "scope": fixturedef.scope,
                  "producer_nodeid": producer, "scopes": [], "request_id": id(request)}
        self._fixture_owners[key] = record
        self._fixture_stack.append(record)
        try:
            outcome = yield
            if outcome.excinfo is not None:
                for scope in record["scopes"]:
                    scope.failed, scope.status, scope.blockers = True, "UNKNOWN_OR_FAILED_PRESERVED", ["REAL_FIXTURE_SETUP_FAILED"]
                return
            value = outcome.get_result()
            if fixturedef.argname == "tmp_path_factory" and fixturedef.func is inspect.unwrap(tmpdir.tmp_path_factory):
                self._instrument_original_factory(value)
            if type(value).__module__ == "tests.rc6_readonly_complete_archive_fixture" and type(value).__name__ == "ReadonlyArchive":
                for scope in record["scopes"]:
                    scope.leases.append(weakref.ref(value))
                    if scope.namespace.path == value.control_root:
                        scope.control_provider = weakref.ref(value)
        finally:
            custody.require(self._fixture_stack and self._fixture_stack[-1] is record,
                            "FIXTURE_REAL_SETUP_STACK_CHANGED")
            self._fixture_stack.pop()

    def _instrument_original_factory(self, factory):
        custody.require(type(factory) is tmpdir.TempPathFactory
                        and getattr(factory.mktemp, "__func__", None) is tmpdir.TempPathFactory.mktemp,
                        "FIXTURE_ORIGINAL_TEMP_FACTORY_REQUIRED")
        original = factory.mktemp

        def owned_mktemp(*args, **kwargs):
            # Exact original call and result. Adoption occurs while the real
            # returned root is EMPTY, before any fixture producer can write.
            path = original(*args, **kwargs)
            self._context()
            if self._fixture_stack:
                record = self._fixture_stack[-1]
                producer = record["producer_nodeid"]
            elif self._current_item is not None:
                producer = self._current_item.nodeid
                key = ("direct-item", producer)
                record = self._fixture_owners.setdefault(key, {"key": key, "fixture_id": None,
                    "scope": "function", "producer_nodeid": producer, "scopes": []})
            else:
                self.start_failures.append({"classification": "ORIGINAL_FACTORY_CALL_WITHOUT_REAL_PRODUCER_CONTEXT",
                    "path_sha256": hashlib.sha256(str(path).encode()).hexdigest()})
                return path
            namespace = lifecycle.adopt_empty_fixture_namespace(path, self.binding,
                control_parent=self.control_root, parent_claim=self.parent_claim, nodeid=producer)
            scope = FixtureScope(namespace,
                frozenset(thread.ident for thread in threading.enumerate() if thread.is_alive()),
                owner_key=record["key"], producer_nodeid=producer, fixture_scope=record["scope"])
            invocation = inspect.signature(original).bind(*args, **kwargs)
            invocation.apply_defaults()
            if invocation.arguments["numbered"]:
                prefix = Path(os.fspath(invocation.arguments["basename"])).name
                suffix = path.name[len(prefix):] if path.name.startswith(prefix) else ""
                custody.require(suffix.isascii() and suffix.isdecimal()
                                and str(int(suffix)) == suffix, "FIXTURE_ORIGINAL_NUMBERED_RESULT_REQUIRED")
                scope.numbering = {"prefix": prefix, "number": int(suffix),
                    "control_path": str(path)}
            self.scopes[namespace.nonce] = scope
            record["scopes"].append(scope)
            return path

        factory.mktemp = owned_mktemp
        self._factory_wrappers.append((factory, owned_mktemp))
        _ORIGINAL_FACTORY_WITNESSES[id(factory)] = (weakref.ref(factory), self, owned_mktemp, original)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        self._current_item = item
        try:
            yield
        finally:
            self._current_item = None

    def pytest_fixture_post_finalizer(self, fixturedef, request):
        record = self._fixture_owners.get((id(fixturedef), id(request)))
        if record is None:
            return
        for scope in record["scopes"]:
            if scope.failed or scope.status == "GREEN":
                continue
            try:
                scope.postfinalizer = lifecycle.observe_real_fixture_post_finalizer(scope.namespace,
                    fixturedef=fixturedef, request=request, producer_nodeid=scope.producer_nodeid)
            except (OSError, ValueError, KeyError, TypeError) as error:
                scope.failed, scope.status, scope.blockers = True, "UNKNOWN_OR_FAILED_PRESERVED", [str(error)]

    def register_required_raw(self, nodeid: str, relative_paths):
        """A fixture producer may explicitly retain its necessary native controls."""
        self._context()
        scopes = [scope for scope in self.scopes.values() if scope.producer_nodeid == nodeid
                  or scope.namespace.fixture_nodeid_sha256 == hashlib.sha256(nodeid.encode()).hexdigest()]
        custody.require(scopes, "FIXTURE_CAPTURE_PRODUCER_UNKNOWN")
        for scope in scopes:
            custody.require(scope.status == "IN_PROGRESS", "FIXTURE_CAPTURE_REGISTRATION_AFTER_FIN")
            for name in relative_paths:
                lifecycle._relative(name)
                if name not in scope.required_raw:
                    scope.required_raw.append(name)

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_makereport(self, item, call):
        outcome = yield
        actual_defs = set(id(value) for value in (getattr(getattr(item, "_request", None), "_fixture_defs", {}) or {}).values())
        scopes = [scope for scope in self.scopes.values() if scope.status != "GREEN"
                  and (scope.owner_key[0] in actual_defs or scope.producer_nodeid == item.nodeid)]
        # Original scope finalization can also happen in the last consumer's
        # teardown after its fixture lookup cache has been cleared.
        if call.when == "teardown":
            scopes.extend(scope for scope in self.scopes.values() if scope.status != "GREEN"
                and scope.postfinalizer is not None and call.start <= scope.postfinalizer.observed_time <= call.stop
                and not any(scope is present for present in scopes))
        if not scopes:
            return
        if outcome.excinfo is not None:
            for scope in scopes:
                scope.failed = True
                scope.status, scope.blockers = "UNKNOWN_OR_FAILED_PRESERVED", ["REAL_REPORT_HOOK_FAILED"]
            return
        report = outcome.get_result()
        if report.failed or call.excinfo is not None:
            for scope in scopes:
                scope.failed = True
                scope.status, scope.blockers = "UNKNOWN_OR_FAILED_PRESERVED", ["REAL_PYTEST_PHASE_FAILED"]
            return
        if report.when != "teardown" or not report.passed:
            return
        providers = [weakref.ref(value) for value in (getattr(item, "funcargs", None) or {}).values()
            if type(value).__module__ == "tests.rc6_readonly_complete_archive_fixture" and type(value).__name__ == "ReadonlyArchive"]
        for scope in scopes:
            if scope.failed:
                continue
            if scope.postfinalizer is not None and call.start <= scope.postfinalizer.observed_time <= call.stop:
                scope.leases.extend(reference for reference in providers if reference not in scope.leases)
                scope.witness = lifecycle.observe_real_shared_teardown(scope.namespace,
                    postfinalizer=scope.postfinalizer, item=item, report=report, call=call)
                self._attempt(scope)
            elif scope.producer_nodeid == item.nodeid:
                scope.leases.extend(providers)
                scope.witness = lifecycle.observe_real_teardown(scope.namespace, item=item, report=report, call=call)
                self._attempt(scope)
            elif scope.postfinalizer is not None:
                scope.status, scope.blockers = "UNKNOWN_OR_UNCLOSED_PRESERVED", ["ORIGINAL_SCOPE_FIN_OUTSIDE_REAL_TEARDOWN_PRESERVED"]

    def pytest_runtest_logfinish(self, nodeid, location):
        # This event follows all report hooks, including actual Source lease
        # postread. A pending lease never triggers a speculative Source read.
        for scope in self.scopes.values():
            if scope.witness is not None and scope.status != "GREEN" and not scope.failed:
                self._attempt(scope)

    def pytest_runtest_logreport(self, report):
        # ALL makereport wrappers (including Source10 postread) completed, but
        # pytest has not yet released item.funcargs. Observe real READY here;
        # logfinish can occur after the final provider weakref has expired.
        if report.when == "teardown" and report.passed:
            for scope in self.scopes.values():
                if (scope.witness is not None and scope.witness.report["nodeid"] == report.nodeid
                        and scope.status != "GREEN" and not scope.failed):
                    self._attempt(scope)

    def _attempt(self, scope):
        self._context()
        namespace = scope.namespace
        if scope.failed or scope.witness is None or scope.status == "GREEN":
            return
        try:
            for index, reference in enumerate(scope.leases):
                provider = reference()
                if provider is None:
                    custody.require(index in scope.closed_lease_observations,
                                    "FIXTURE_SOURCE_LEASE_VANISHED_WITHOUT_REAL_CLOSURE")
                    continue
                custody.require(provider.state == "READY" and provider.pending_lease is None,
                                "FIXTURE_SOURCE_LEASE_NOT_CLOSED")
                # Observe only after this scope's real last teardown witness.
                # Compact fields survive ordinary fixture-object release; a
                # missing weakref alone is never lease closure evidence.
                scope.closed_lease_observations[index] = {"sequence": provider.sequence,
                    "source_tree": provider.tree, "actual_state": "READY", "pending_lease": None}
                if scope.control_provider is not None and scope.control_provider() is provider:
                    scope.control_provider_observation = {"control_root": str(provider.control_root),
                        "final_sequence": provider.sequence, "source_tree": provider.tree,
                        "actual_state_after_last_consumer": "READY"}
            new_threads = [thread for thread in threading.enumerate()
                           if thread.is_alive() and thread.ident not in scope.baseline_threads]
            custody.require(not new_threads, "FIXTURE_LIVE_WRITER_THREAD_NOT_CLOSED")
            custody.require(scope.numbering is not None,
                            "ORIGINAL_UNNUMBERED_FACTORY_NAMESPACE_COLLISION_SEMANTICS_PRESERVED")
            # Scoped FIN first: actual own ECHILD, or a positively authenticated
            # idle session-infrastructure lease that consumes no fixture inode.
            # No child is reaped, stopped or globally finalized here. Original
            # whole-phase/BIG ECHILD is independently required at phase FIN.
            fin = lifecycle.finish_inprocess_fixture(namespace, witness=scope.witness, control_parent=self.control_root,
                                                    infrastructure_monitor=self.infrastructure_monitor)
            scope.fin = fin
            measured = lifecycle.inventory(namespace)
            references = namespace_handles(namespace, measured)
            custody.require(not references, "FIXTURE_OPEN_FILE_OR_SQLITE_WRITER_NOT_CLOSED")
            from scripts.rc6_scoped_infrastructure_lease import namespace_process_references
            namespace_process_references(os.getpid(), namespace, measured)
            capture = scope.capture
            if capture is None:
                if scope.control_provider is not None:
                    provider = scope.control_provider()
                    observed = scope.control_provider_observation
                    custody.require(observed is not None and observed["control_root"] == str(namespace.path)
                                    and (provider is None or provider.control_root == namespace.path),
                                    "FIXTURE_REAL_SOURCE_CONTROL_PROVIDER_REQUIRED")
                    required = ["lease-" + str(sequence).zfill(4) + ".json"
                                for sequence in range(1, observed["final_sequence"] + 1)]
                    custody.require(set(required) == set(measured["rows"])
                                    and not any(row["directory"] for row in measured["rows"].values()),
                                    "FIXTURE_SOURCE_CONTROL_MEMBERS_UNKNOWN")
                    scope.required_raw.extend(name for name in required if name not in scope.required_raw)
                capture = lifecycle.capture_required_evidence(namespace, fin,
                    self.control_root / (namespace.nonce + ".capture"), scope.required_raw)
                scope.capture = capture
            # Recheck after capture and immediately before the first unlink.
            custody.require(not namespace_handles(namespace, measured), "FIXTURE_HANDLE_OPENED_AFTER_CAPTURE")
            # Pinning happens inside the authenticated cleaner, AFTER it
            # rechecks all producer references. No producer FD guard is waived.
            receipt = lifecycle.cleanup_namespace(namespace, fin, capture,
                on_directory_retired=lambda: self._preserve_original_numbering(scope, fin, capture))
            receipt.update(original_tmp_path_fixture_implementation_changed=False,
                           original_test_result_changed=False, actual_teardown_report_passed=True,
                           actual_own_kernel_ECHILD=fin.final_state["kernel_echild_verified"],
                           session_infrastructure_lease_admitted=fin.infrastructure_lease is not None,
                           own_open_namespace_handles=0,
                           live_new_threads=0, namespace_inventory_sha256=capacity.digest(measured),
                           full_governed_or_global_FIN_claimed=False,
                           evidence_scope="UNIT_FIXTURE_ASSERTION_AND_EXPLICIT_REQUIRED_RAW_ONLY")
            receipt["original_factory_numbering_control"] = scope.numbering_receipt
            receipt.update(directory_inode_removed=True, original_namespace_removed=True,
                path_replaced_by_authenticated_numbering_control=True,
                original_path_is_nonexistent=False,
                capacity_after_numbering_control=capacity.measure_filesystem(namespace.path.parent))
            custody.publish(self.control_root / (namespace.nonce + ".cleanup.json"), receipt, compact=True)
            scope.status, scope.blockers, scope.receipt = "GREEN", [], receipt
        except (OSError, ValueError, KeyError, TypeError) as error:
            scope.status, scope.blockers = "UNKNOWN_OR_UNCLOSED_PRESERVED", [str(error)]
            # Never close arbitrary FDs, GC fixtures, join foreign threads,
            # weaken Source stats, retry tests or remove a blocked namespace.

    def _preserve_original_numbering(self, scope, fin, capture):
        """Keep the original factory's next number without retaining its payload.

        The original parser sees EXACTLY the original name in this new regular
        control file. This preserves even overlapping prefixes with digits.
        The previous directory and its inode are gone; the control is a new
        owned inode, and its path is explicitly not claimed to be nonexistent.
        """
        path = Path(scope.numbering["control_path"])
        owner = lifecycle.validate_consumer_receipt(self.parent_claim,
            candidate_sha=self.binding["candidate_sha"], candidate_tree=self.binding["candidate_tree"])
        custody.require(path.is_relative_to(owner) and path == scope.namespace.path,
                        "FIXTURE_NUMBERING_CONTROL_OUTSIDE_AUTHORITY")
        payload = {"schema": "porota.rc6.original-factory-numbering-control.v1", "binding": self.binding,
            "namespace_nonce": scope.namespace.nonce, "original_prefix": scope.numbering["prefix"],
            "original_number": scope.numbering["number"], "original_namespace_path": str(scope.namespace.path),
            "owned_fin_kind": lifecycle._fin_kind(fin), "kernel_sha256": capacity.digest(fin.kernel),
            "capture_manifest_sha256": capture.manifest_sha256,
            "mktemp_implementation_args_and_result_changed": False,
            "control_is_regular_file_not_directory_or_alias": True, "real_orders_sent": 0}
        if scope.numbering_receipt is None:
            with custody.directory(path.parent) as parent:
                details = os.fstat(parent)
                custody.require(details.st_dev == scope.namespace.identity[0]
                                and details.st_uid == scope.namespace.identity[2]
                                and custody.mount_id(parent) == scope.namespace.mount_id,
                                "FIXTURE_NUMBERING_CONTROL_PARENT_CHANGED")
            custody.publish(path, payload, compact=True)
            raw, details = custody.read_file(path, mode=0o600)
            custody.require(details.st_ino != scope.namespace.identity[1],
                            "FIXTURE_NUMBERING_CONTROL_REUSES_OLD_DIRECTORY_INODE")
            receipt = {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
                "identity": custody.identity(details), "control": payload,
                "retained_control_counts_toward_entries_and_evidence": True}
            custody.publish(self.control_root / (scope.namespace.nonce + ".numbering-control.json"), receipt, compact=True)
            scope.numbering_receipt = receipt
        raw, details = custody.read_file(path, mode=0o600)
        custody.require(hashlib.sha256(raw).hexdigest() == scope.numbering_receipt["sha256"]
                        and custody.identity(details) == scope.numbering_receipt["identity"]
                        and custody.decode(raw) == payload, "FIXTURE_ORIGINAL_NUMBERING_CONTROL_CHANGED")
        return scope.numbering_receipt

    def retry_after_original_phase_finalization(self):
        """Retry cleanup only, after the ORIGINAL finalizer closed real children.

        Stored witnesses are compact; no test/fixture result is rerun, and no
        global finalizer is invoked here. Existing controls are not overwritten.
        """
        for scope in self.scopes.values():
            if scope.witness is not None and scope.status != "GREEN" and not scope.failed:
                self._attempt(scope)

    def summary(self):
        rows = [{"fixture_nodeid_sha256": scope.namespace.fixture_nodeid_sha256,
                 "namespace_nonce": scope.namespace.nonce, "status": scope.status,
                 "original_fixture_scope": scope.fixture_scope,
                 "blockers": scope.blockers,
                 "namespace_removed": scope.status == "GREEN"}
                for scope in self.scopes.values()]
        return {"schema": "porota.rc6.pytest-fixture-lifecycle-summary.v1", "binding": self.binding,
                "actual_original_function_tmp_path_scopes": sum(row["original_fixture_scope"] == "function" for row in rows),
                "authenticated_original_factory_scopes": len(rows),
                "original_factory_context_failures": self.start_failures,
                "session_infrastructure": self.infrastructure_monitor.summary(),
                "scope_counts": {name: sum(row["original_fixture_scope"] == name for row in rows)
                                 for name in ("function", "class", "module", "package", "session")},
                "closed_and_removed_scopes": sum(row["status"] == "GREEN" for row in rows),
                "preserved_blocked_or_failed_scopes": sum(row["status"] != "GREEN" for row in rows),
                "reclaimed_allocated_bytes": sum(scope.receipt["allocated_bytes_before"]
                    for scope in self.scopes.values() if scope.receipt is not None),
                "reclaimed_entries": sum(scope.receipt["retained_entries_before"]
                    for scope in self.scopes.values() if scope.receipt is not None),
                "rows": rows, "fixtures_or_tests_redefined": False, "global_cleanup_claimed": False,
                "full_governed_or_runtime_validation_claimed": False, "real_orders_sent": 0}
