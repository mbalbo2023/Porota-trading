"""A630 adversarial no-network tests: exact full-key and two true leases."""

from contextlib import contextmanager, nullcontext, redirect_stdout
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import base64
import fcntl
import json
import io
import os
from pathlib import Path
import sqlite3
import subprocess
import stat
import sys
import tempfile
import types
import unittest
import zipfile
from unittest.mock import patch
from scripts import rc6_a630_host_ppi_current_book as engine

from scripts.rc6_a630_host_ppi_current_book import (
    SLOTS, PER_GET_CLOCK_FIELDS, TwoGetAtomicBudget, _existing_shared_budget, _exit_round_fresh,
    _strict_adapter_send,
    _http_body_hashes,
    _utc, capture_two,
    decode_target, host_preflight, target_for_slot, unverified_transport_receipt,
    verify_receipt, ROOTLESS_CANDIDATE_SHA, ROOTLESS_CANDIDATE_TREE, ROOTLESS_IMAGE_ID,
    _attest_rootless_release, _attest_rootless_observer, _rootless_state,
    _rootless_context, rootless_host_capture, verified_rootless_wrapper,
)

NOW = datetime(2026, 11, 4, 14, 20, 1, tzinfo=timezone.utc)
PROVIDER = "2026-11-04T14:20:00+00:00"
TARGET = SLOTS["1120"]


class TestA670PromoteArtifactAttestation(unittest.TestCase):
    """Synthetic Deploy V2 artifact shape; never a caller-supplied flag."""

    def setUp(self):
        self.run = {"id": 888, "run_attempt": 2, "status": "completed",
                    "conclusion": "success", "event": "workflow_dispatch",
                    "path": ".github/workflows/porota-deploy-v2-promote.yml",
                    "head_branch": "deploy/rc6-pr69-isolated-20260915",
                    "head_sha": "a" * 40,
                    "head_repository": {"full_name": "mbalbo2023/Porota-trading"}}
        self.runtime = {
            "schema": "porota.rootless.deploy.v1", "state": "VALIDADO_RUNTIME",
            "phase": "COMPLETE", "source_sha": "d" * 40,
            "source_tree": "e" * 40, "image_id": "sha256:" + "f" * 64,
            "release_manifest_sha256": "c" * 64,
            "operating_mode": "PRODUCTION_PAPER", "execution_mode": "SIMULATION",
            "real_orders_sent": 0, "ppi_watch_touched": False,
            "roles": {"observer": "1" * 64, "dashboard": "2" * 64},
            "health": {"observer": "running", "dashboard": "healthy",
                       "orders_guard": "BLOCKED"},
        }
        self.download = {
            "schema": "porota.release.download.v1", "deployment_sha": "a" * 40,
            "source_sha": "d" * 40, "source_tree": "e" * 40,
            "image_id": "sha256:" + "f" * 64,
            "run_id": 777, "run_attempt": 1, "artifact_id": 444,
            "artifact_digest": "sha256:" + "3" * 64,
        }
        self.artifact = {"id": 500, "name": "porota-runtime-888-2",
                         "expired": False, "size_in_bytes": 0, "digest": "",
                         "workflow_run": {"id": 888, "head_sha": "a" * 40}}

    def archived(self, *, runtime=None, download=None, add_member=None,
                 runtime_raw=None):
        with io.BytesIO() as stream:
            with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("runtime.json", runtime_raw if runtime_raw is not None
                                 else json.dumps(runtime if runtime is not None else self.runtime))
                archive.writestr("download.json", json.dumps(
                    download if download is not None else self.download))
                if add_member is not None:
                    archive.writestr(add_member, "unexpected")
            return stream.getvalue()

    def evidence(self, payload):
        meta = {**self.artifact, "size_in_bytes": len(payload),
                "digest": "sha256:" + sha256(payload).hexdigest()}
        return engine.verified_promote_v2_runtime_artifact(self.run, meta, payload)

    def test_synthetic_promote_artifact_digest_binds_new_release_without_old_p0_pin(self):
        result = self.evidence(self.archived())
        self.assertEqual(result, {"source_sha": "d" * 40,
                 "source_tree": "e" * 40, "image_id": "sha256:" + "f" * 64,
                 "release_manifest_sha256": "c" * 64,
                 "observer_cid": "1" * 64, "dashboard_cid": "2" * 64,
                 "promote_run_id": 888, "promote_run_attempt": 2,
                 "runtime_artifact_digest": "sha256:" + sha256(self.archived()).hexdigest()})

    def test_rejects_tampered_provider_metadata_and_paper_receipts(self):
        payload = self.archived()
        bad_runs = ({"status": "in_progress"}, {"conclusion": "failure"},
                    {"path": ".github/workflows/deploy.yml"},
                    {"head_branch": "main"}, {"event": "schedule"},
                    {"head_repository": {"full_name": "other/repo"}},
                    {"head_repository": "not-a-dict"},
                    {"head_repository": ["other/repo"]})
        for bad in bad_runs:
            with self.subTest(run=bad), self.assertRaises(ValueError):
                engine.verified_promote_v2_runtime_artifact({**self.run, **bad},
                    {**self.artifact, "size_in_bytes": len(payload),
                     "digest": "sha256:" + sha256(payload).hexdigest()}, payload)
        for change in ({"real_orders_sent": 1}, {"state": "NO_VERIFICADO"},
                       {"ppi_watch_touched": True}, {"source_sha": "b" * 40},
                       {"execution_mode": "LIVE"},
                       {"roles": {"observer": "1" * 64}},
                       {"health": {"observer": "running", "dashboard": "healthy",
                                   "orders_guard": "OPEN"}}):
            with self.subTest(runtime=change), self.assertRaises(ValueError):
                self.evidence(self.archived(runtime={**self.runtime, **change}))
        for bad in (self.archived(add_member="private.log"),
                    self.archived(runtime_raw='{"state":"VALIDADO_RUNTIME","state":"HOLD"}')):
            with self.subTest(extra=True), self.assertRaises(ValueError):
                self.evidence(bad)
        with self.assertRaisesRegex(ValueError, "PROMOTE_ARTIFACT_CUSTODY_HOLD"):
            engine.verified_promote_v2_runtime_artifact(
                self.run, {**self.artifact,
                    "size_in_bytes": len(payload),
                    "digest": "sha256:" + sha256(payload).hexdigest(),
                    "workflow_run": "not-a-dict"}, payload)

    def test_latest_canonical_promote_is_resolved_by_github_not_user_env_or_p0_head(self):
        payload = self.archived()
        artifact = {**self.artifact, "size_in_bytes": len(payload),
                    "digest": "sha256:" + sha256(payload).hexdigest()}
        urls = []
        def api(path):
            urls.append(path)
            if "/workflows/porota-deploy-v2-promote.yml/runs?" in path:
                return json.dumps({"workflow_runs": [{**self.run, "id": 800},
                                                      self.run]}).encode()
            if path.endswith("/actions/runs/888/artifacts?per_page=100"):
                return json.dumps({"artifacts": [artifact]}).encode()
            if path.endswith("/actions/artifacts/500/zip"):
                return payload
            raise AssertionError("UNEXPECTED_GITHUB_ENDPOINT:" + path)
        expected = self.evidence(payload)
        self.assertEqual(engine.fetch_latest_verified_promote_v2_claim(api), expected)
        self.assertEqual(len(urls), 3)
        self.assertTrue(all(path.startswith("repos/mbalbo2023/Porota-trading/actions/")
                            for path in urls))
        self.assertFalse(any("ppi" in path.lower() for path in urls))
        # No fallback to an older successful run if the most recent artifact
        # is absent; that must not silently bless a stale release.
        def missing_new_artifact(path):
            if "/workflows/porota-deploy-v2-promote.yml/runs?" in path:
                return json.dumps({"workflow_runs": [self.run, {**self.run,
                    "id": 800}]}).encode()
            if "/actions/runs/888/artifacts?" in path:
                return b'{"artifacts":[]}'
            raise AssertionError("FALLBACK_TO_OLDER_RUN_FORBIDDEN")
        with self.assertRaises(ValueError):
            engine.fetch_latest_verified_promote_v2_claim(missing_new_artifact)

    def test_oversize_promote_zip_metadata_stops_before_any_bytes_download(self):
        oversized = {**self.artifact, "size_in_bytes": 65537,
                     "digest": "sha256:" + "0" * 64}
        endpoints = []
        def api(path):
            endpoints.append(path)
            if "/workflows/porota-deploy-v2-promote.yml/runs?" in path:
                return json.dumps({"workflow_runs": [self.run]}).encode()
            if "/actions/runs/888/artifacts?" in path:
                return json.dumps({"artifacts": [oversized]}).encode()
            raise AssertionError("ZIP_UNEXPECTEDLY_DOWNLOADED")
        with self.assertRaises(ValueError):
            engine.fetch_latest_verified_promote_v2_claim(api)
        self.assertEqual(len(endpoints), 2)


class Shared:
    def __init__(self, *, deny_at=None, duplicate=False, lease_none=False):
        self.calls = []
        self.deny_at = deny_at
        self.duplicate = duplicate
        self.lease_none = lease_none
        self.started = []
        self.finished = []

    def acquire(self, endpoint, *, consumer, priority):
        self.calls.append((endpoint, consumer, priority))
        if self.deny_at == len(self.calls):
            return {"allowed": False}
        return {"allowed": True,
                "lease": None if self.lease_none else
                "L1" if self.duplicate or len(self.calls) == 1 else "L2"}

    def start(self, lease):
        self.started.append(lease)

    def wire_scope(self, lease):
        return nullcontext()

    def finish(self, lease, **outcome):
        self.finished.append((lease, outcome))


class Reader:
    def __init__(self, key, secret, *, budget, consumer, priority, audit,
                 clock=PROVIDER, book_clock=None, extra_get=False, bad_shape=False,
                 close_error=False):
        self.budget = budget
        self.consumer = consumer
        self.priority = priority
        self.audit = audit
        self.clock = clock
        self.book_clock = book_clock or clock
        self.extra_get = extra_get
        self.bad_shape = bad_shape
        self.close_error = close_error
        self.actual = []

    def login_once(self):
        self.audit("POST", "/api/1.0/account/loginapi", "ALLOWED")

    def _request(self, endpoint):
        grant = self.budget.acquire(endpoint, consumer=self.consumer,
                                    priority=self.priority)
        if not grant.get("allowed"):
            raise ValueError("ATOMIC_LEASE_DENIED")
        lease = grant["lease"]
        self.budget.start(lease)
        with self.budget.wire_scope(lease):
            self.audit("GET", "/api/1.0/marketdata/" + endpoint, "ALLOWED")
            self.actual.append(endpoint)
        self.budget.finish(lease, outcome="SUCCESS")
        if self.extra_get:
            self.audit("GET", "/api/1.0/marketdata/" + endpoint, "ALLOWED")

    def current(self, ticker, ppi_type, settlement):
        assert (ticker, ppi_type, settlement) == ("AL30", "BONOS", "A-24HS")
        self._request("current")
        return {"price": 749.0, "date": self.clock}

    def book(self, ticker, ppi_type, settlement):
        self._request("book")
        if self.bad_shape:
            return {"date": self.clock, "bid": []}
        return {"date": self.book_clock, "bids": [{"price": 748, "quantity": 7}],
                "offers": [{"price": 750, "quantity": 5}]}

    def close(self):
        if self.close_error:
            raise RuntimeError("synthetic close failed")
        return None


@contextmanager
def fake_hashes():
    # Simulates the real HTTP body hook; never opens a socket.
    yield [["/api/1.0/marketdata/current", sha256(b"current"), 7],
           ["/api/1.0/marketdata/book", sha256(b"book"), 4]]


@contextmanager
def fake_adapter():
    yield ["/api/1.0/marketdata/current", "/api/1.0/marketdata/book"]


def invoke(shared, *, observations=None, **reader_options):
    constructed = []

    def factory(*args, **kwargs):
        obj = Reader(*args, **kwargs, **reader_options)
        constructed.append(obj)
        return obj

    observed = iter(observations).__next__ if observations is not None else lambda: NOW
    result = capture_two(TARGET, ("fake-key", "fake-secret"), shared, factory,
                         clock=observed, http_limiter=fake_hashes,
                         adapter_guard=fake_adapter)
    return result, constructed


class TestA630(unittest.TestCase):
    def test_typed_provider_429_is_sanitized_without_leaking_exception_text(self):
        class ThrottledReader(Reader):
            def current(self, *args):
                self.last_read_error_code = "PPI_HTTP_429"
                raise RuntimeError("private upstream response body with credentials")
        result = capture_two(TARGET, ("fake-key", "fake-secret"), Shared(),
                             lambda *a, **kw: ThrottledReader(*a, **kw),
                             clock=lambda: NOW, http_limiter=fake_hashes,
                             adapter_guard=fake_adapter)
        self.assertEqual(result["state"], "NO_VERIFICADO")
        self.assertEqual(result["reason"], "PPI_HTTP_429")
        self.assertEqual(result["current_gets"], 0)
        self.assertTrue(verify_receipt(result, TARGET))
        self.assertNotIn("credentials", json.dumps(result))

    def test_typed_login_401_403_remain_sanitized(self):
        for code in ("PPI_HTTP_401", "PPI_HTTP_403"):
            class AuthRejected(Reader):
                def login_once(self):
                    self.last_read_error_code = code
                    raise RuntimeError("private login response with account details")
            result = capture_two(TARGET, ("fake-key", "fake-secret"), Shared(),
                                 lambda *a, **kw: AuthRejected(*a, **kw),
                                 http_limiter=fake_hashes, adapter_guard=fake_adapter)
            self.assertEqual(result["reason"], code)
            self.assertTrue(verify_receipt(result, TARGET))
            self.assertNotIn("private login", json.dumps(result))

    def test_actual_product_auth_metadata_shape_is_bounded_without_reader_error_code(self):
        class ProductionAuthResponseError(RuntimeError):
            pass
        for status in (401, 403, 429):
            class AuthRejected(Reader):
                def login_once(self):
                    raise ProductionAuthResponseError(
                        f"PPI_PRODUCTION_AUTH_PROVIDER_ERROR: HTTP={status}; "
                        "Content-Type=APPLICATION_JSON; Content-Length=128")
            receipt = capture_two(TARGET, ("key", "secret"), Shared(),
                                  lambda *a, **kw: AuthRejected(*a, **kw),
                                  http_limiter=fake_hashes, adapter_guard=fake_adapter)
            self.assertEqual(receipt["reason"], f"PPI_HTTP_{status}")
            self.assertTrue(verify_receipt(receipt, TARGET))
            self.assertNotIn("Length=128", json.dumps(receipt))
        self.assertIsNone(engine._sanitized_native_auth_reason(
            ProductionAuthResponseError("PPI_PRODUCTION_AUTH_PROVIDER_ERROR: HTTP=401; secret=SHOULD_NOT_LEAK")))

    def test_pilot_time_gate_fails_closed_on_other_day_and_near_close(self):
        from datetime import datetime
        with patch.dict(os.environ, {"RC6_A630_LOCAL_SLOT": "1630",
                                     "RC6_A630_LOCAL_ART_DATE": "2026-11-18"}):
            self.assertTrue(engine._local_pilot_session_now(
                now=datetime(2026, 11, 18, 19, 44, tzinfo=timezone.utc)))
            self.assertFalse(engine._local_pilot_session_now(
                now=datetime(2026, 11, 18, 19, 46, tzinfo=timezone.utc)))
            self.assertFalse(engine._local_pilot_session_now(
                now=datetime(2026, 11, 19, 19, 40, tzinfo=timezone.utc)))
            self.assertFalse(engine._local_pilot_session_now(
                now=datetime(2026, 11, 18, 19, 20, tzinfo=timezone.utc)))

    def test_postclose_pilot_is_opt_in_same_day_and_never_an_open_session(self):
        day = "2026-11-18"
        with patch.dict(os.environ, {"RC6_A630_LOCAL_SLOT": "1630",
                                     "RC6_A630_LOCAL_ART_DATE": day}, clear=False):
            later = datetime(2026, 11, 18, 23, 0, tzinfo=timezone.utc)
            self.assertFalse(engine._local_pilot_session_now(now=later))
            with patch.dict(os.environ, {"RC6_A630_POSTCLOSE": "ONCE"}):
                self.assertTrue(engine._local_pilot_session_now(now=later))
                self.assertFalse(engine._local_pilot_session_now(
                    now=datetime(2026, 11, 18, 19, 55, tzinfo=timezone.utc)))
                self.assertFalse(engine._local_pilot_session_now(
                    now=datetime(2026, 11, 19, 2, 0, tzinfo=timezone.utc)))
                self.assertFalse(engine._local_pilot_session_now(
                    now=datetime(2026, 11, 19, 3, 1, tzinfo=timezone.utc)))
                self.assertFalse(engine._local_pilot_session_now(
                    now=datetime(2026, 11, 19, 23, 0, tzinfo=timezone.utc)))

    def test_postclose_stale_provider_clocks_are_evidence_not_readiness(self):
        previous = (NOW - timedelta(days=1)).isoformat()
        stale, objects = invoke(Shared(), clock=previous)
        self.assertEqual(stale["state"], "NO_VERIFICADO")
        self.assertEqual(stale["reason"], "PPI_NATIVE_CLOCK_STALE_OR_FUTURE")
        receipt = capture_two(TARGET, ("fake-key", "fake-secret"), Shared(),
            lambda *args, **kwargs: Reader(*args, **kwargs, clock=previous),
            clock=lambda: NOW, http_limiter=fake_hashes, adapter_guard=fake_adapter,
            allow_stale=True)
        self.assertTrue(verify_receipt(receipt, TARGET))
        self.assertEqual(receipt["state"], "EVIDENCE_ONLY_NO_GO")
        self.assertEqual(receipt["reason"], "PPI_NATIVE_STALE_POSTCLOSE_DIAGNOSTIC_NO_GO")
        self.assertEqual((receipt["current_gets"], receipt["book_gets"]), (1, 1))
        self.assertIsNotNone(receipt["current_received_at_utc"])
        self.assertFalse(receipt["source_pit_verified"])
        self.assertFalse(receipt["execution_allowed"])
        self.assertEqual(receipt["orders_sent"], 0)

    def test_postclose_future_clock_and_missing_shapes_still_fail_closed(self):
        for bad, shape, reason in (
            ((NOW + timedelta(seconds=1)).isoformat(), False,
             "PPI_NATIVE_CLOCK_STALE_OR_FUTURE"),
            ((NOW - timedelta(days=1)).isoformat(), True,
             "PROVIDER_RESPONSE_SHAPE_INVALID"),
        ):
            with self.subTest(reason=reason):
                receipt = capture_two(TARGET, ("fake-key", "fake-secret"), Shared(),
                    lambda *args, **kwargs: Reader(*args, **kwargs,
                        clock=bad, bad_shape=shape),
                    clock=lambda: NOW, http_limiter=fake_hashes,
                    adapter_guard=fake_adapter, allow_stale=True)
                self.assertEqual(receipt["state"], "NO_VERIFICADO")
                self.assertEqual(receipt["reason"], reason)
                self.assertNotIn("current_received_at_utc", receipt)

    def test_postclose_receipt_reason_cannot_forge_fresh_or_legacy_pit(self):
        old = (NOW - timedelta(days=1)).isoformat()
        receipt = capture_two(TARGET, ("key", "secret"), Shared(),
            lambda *args, **kwargs: Reader(*args, **kwargs, clock=old),
            clock=lambda: NOW, http_limiter=fake_hashes,
            adapter_guard=fake_adapter, allow_stale=True)
        self.assertTrue(verify_receipt(receipt, TARGET))
        forged = dict(receipt, reason="PPI_GET_RECEIPT_SDK_ONLY_NO_CAUSAL_PIT")
        self.assertFalse(verify_receipt(forged, TARGET))
        for key in PER_GET_CLOCK_FIELDS:
            forged.pop(key)
        self.assertFalse(verify_receipt(forged, TARGET))
        fresh, _ = invoke(Shared())
        forged = dict(fresh, reason="PPI_NATIVE_STALE_POSTCLOSE_DIAGNOSTIC_NO_GO")
        self.assertFalse(verify_receipt(forged, TARGET))

    def test_local_pilot_only_accepts_a_real_operator_pause_and_closed_entry_gate(self):
        operator = types.ModuleType("scripts.rc6_operator_paper_rearm")
        operator.PAUSE_STATUS = "PAPER_PAUSED_BY_OPERATOR"
        operator.trusted_operator_pause = lambda **kwargs: None
        admission = types.ModuleType("rc6_paper_admission_gate")
        admission.admission_status = lambda **kwargs: (
            False, "OPERATOR_NO_GO_PENDING_EXPLICIT_REARM")
        with patch.dict(sys.modules, {
                "scripts.rc6_operator_paper_rearm": operator,
                "rc6_paper_admission_gate": admission}):
            self.assertFalse(engine._local_pilot_admission_paused())
            operator.trusted_operator_pause = lambda **kwargs: {
                "status": operator.PAUSE_STATUS, "receipt_sha256": "a" * 64}
            self.assertTrue(engine._local_pilot_admission_paused())
            admission.admission_status = lambda **kwargs: (True, "")
            self.assertFalse(engine._local_pilot_admission_paused())
            admission.admission_status = lambda **kwargs: (
                False, "PAPER_PREOPEN_RECEIPT_UNSAFE")
            self.assertFalse(engine._local_pilot_admission_paused())

    def test_local_pilot_cannot_admit_a_get_without_paused_entry_authority(self):
        module = types.ModuleType("rc6_ppi_global_budget")
        module.supervisable_position_count = lambda *args: 0
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(sys.modules, {"rc6_ppi_global_budget": module}), \
             patch.object(engine, "_local_pilot_session_now", return_value=True), \
             patch.object(engine, "_local_pilot_admission_paused", return_value=False):
            fd = os.open(Path(directory) / "bootstrap.lock", os.O_CREAT | os.O_RDWR, 0o600)
            pilot = engine.LocalPilotBudget(Shared(), Path(directory) / "paper.db",
                                            Path(directory) / "global.sqlite", fd)
            self.assertEqual(pilot.acquire("current", consumer="UNSCOPED",
                                            priority="DISCOVERY"),
                             {"allowed": False,
                              "reason": "PPI_LOCAL_PILOT_PAPER_ADMISSION_HOLD"})
            self.assertRaises(ValueError, pilot.start, "L1")
            with self.assertRaises(ValueError):
                with pilot.wire_scope("L1"):
                    self.fail("wire must remain closed without verified PAUSE")
            pilot.close()

    def test_local_pilot_checks_positions_again_at_start_and_wire(self):
        observed = {"count": 0}
        module = types.ModuleType("rc6_ppi_global_budget")
        module.supervisable_position_count = lambda *a: observed["count"]
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(sys.modules, {"rc6_ppi_global_budget": module}), \
             patch.object(engine, "_local_pilot_session_now", return_value=True), \
             patch.object(engine, "_local_pilot_admission_paused", return_value=True):
            fd = os.open(Path(directory) / "bootstrap.lock", os.O_CREAT | os.O_RDWR, 0o600)
            pilot = engine.LocalPilotBudget(Shared(), Path(directory) / "paper.db",
                                            Path(directory) / "global.sqlite", fd)
            self.assertTrue(pilot.acquire("current", consumer="UNSCOPED",
                                          priority="DISCOVERY")["allowed"])
            observed["count"] = 1
            self.assertRaises(ValueError, pilot.start, "L1")
            with self.assertRaises(ValueError):
                with pilot.wire_scope("L1"):
                    self.fail("wire must remain closed when PAPER positions are present")
            self.assertFalse(pilot.acquire("book", consumer="UNSCOPED",
                                           priority="DISCOVERY")["allowed"])
            pilot.close()

    def test_fixed_slots_two_families_and_never_alias(self):
        self.assertEqual(len(SLOTS), 4)
        self.assertEqual({x[1] for x in SLOTS.values()}, {"BONOS", "ACCIONES"})
        self.assertRaises(ValueError, target_for_slot, "0000")
        self.assertRaises(ValueError, decode_target,
            base64.b64encode(json.dumps(["AL30", "LETRAS", "BYMA", "ARS", "A-24HS"]).encode()).decode())

    def test_oversize_or_duplicate_key_fails_before_reader(self):
        for value in ("%", "a" * 1200, "eA==", "YQ=="):
            with self.assertRaises(ValueError):
                decode_target(value)
        self.assertRaises(ValueError, _utc, "2026-11-04T14:20:00")

    def test_missing_budget_no_get_no_reader(self):
        called = []
        result = capture_two(TARGET, ("key", "secret"), None,
                             lambda *args, **kw: called.append(True))
        self.assertEqual(called, [])
        self.assertEqual(result["reason"], "SHARED_BUDGET_OR_EXIT_RESERVE_NOT_ATTESTED")
        self.assertEqual(result["current_gets"], 0)
        self.assertEqual(result["book_gets"], 0)

    def test_specific_budget_failure_classification_keeps_zero_get(self):
        for code in ("PPI_BUDGET_SIDECAR_ABSENT", "PPI_RETENTION_NOT_READY",
                     "PPI_EXIT_LOWER_SUSPENDED", "PPI_EXIT_ROUND_NOT_ATTESTED"):
            called = []
            receipt = capture_two(TARGET, ("key", "secret"), None,
                                  lambda *a, **kw: called.append(True),
                                  budget_reason=code)
            self.assertEqual(receipt["reason"], code)
            self.assertEqual(receipt["current_gets"], 0)
            self.assertEqual(called, [])

    def test_real_sidecar_snapshot_required_and_never_created(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / 'observer.db'
            budget_dir = root / 'budget-artifact'
            sidecar = budget_dir / 'ppi-budget' / 'global.sqlite'
            workspace = types.ModuleType('cg_paper_workspace')
            workspace.database_path = lambda: database
            workspace.artifact_root = lambda path: budget_dir
            module = types.ModuleType('rc6_ppi_global_budget')
            class RuntimePPIBudget:
                def __init__(self, path):
                    self.path = path
            module.RuntimePPIBudget = RuntimePPIBudget
            module.budget_from_environment = lambda database: RuntimePPIBudget(sidecar)
            snapshot = {'status':'OBSERVED','receipt_retention':{'status':'READY'},
                        'exit_service':{'lower_suspended':False,'round':{
                            'status':'COMPLETE','required_scope_verified_current':True,
                            'required_scope_basis':'CURRENT_VERIFIED_PAPER_LEDGER',
                            'recorded_at':datetime.now(timezone.utc).timestamp()}}}
            module.runtime_budget_snapshot = lambda database: snapshot
            with patch.dict(sys.modules, {'cg_paper_workspace': workspace,
                                          'rc6_ppi_global_budget': module}):
                budget, reason = _existing_shared_budget()
                self.assertIsNone(budget)
                self.assertEqual(reason, 'PPI_BUDGET_SIDECAR_ABSENT')
                self.assertFalse(sidecar.exists())  # no writer creates it
                sidecar.parent.mkdir(parents=True)
                sidecar.touch()
                budget, reason = _existing_shared_budget()
                self.assertIsInstance(budget, RuntimePPIBudget)
                self.assertIsNone(reason)
                snapshot['receipt_retention']['status'] = 'NO_VERIFICADO'
                budget, reason = _existing_shared_budget()
                self.assertIsNone(budget)
                self.assertEqual(reason, 'PPI_RETENTION_NOT_READY')
                snapshot['receipt_retention']['status'] = 'READY'
                snapshot['exit_service']['round']['required_scope_verified_current'] = False
                budget, reason = _existing_shared_budget()
                self.assertIsNone(budget)
                self.assertEqual(reason, 'PPI_EXIT_ROUND_NOT_ATTESTED')

    def test_explicit_local_once_budget_uses_original_global_sidecar_and_zero_positions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "observer.db"
            sidecar = root / "artifacts/ppi-budget/global.sqlite"
            workspace = types.ModuleType("cg_paper_workspace")
            workspace.database_path = lambda: database
            workspace.artifact_root = lambda path: root / "artifacts"
            module = types.ModuleType("rc6_ppi_global_budget")
            seen = []
            module.RuntimePPIBudget = type("RuntimePPIBudget", (), {})
            module.budget_from_environment = lambda path: self.fail("unapproved runtime budget")
            module.runtime_budget_snapshot = lambda path: self.fail("no sidecar to snapshot")
            module.supervisable_position_count = lambda path, *a, **k: 0
            module.SCHEMA = "RC6_GLOBAL_PPI_BUDGET_V1"
            class GlobalPPIBudget:
                def __init__(self, path, policy, **kwargs):
                    seen.append((path, policy, kwargs))
            module.GlobalPPIBudget = GlobalPPIBudget
            with patch.dict(sys.modules, {"cg_paper_workspace": workspace,
                                          "rc6_ppi_global_budget": module}), \
                 patch.dict(os.environ, {"RC6_A630_LOCAL_PILOT": "ONCE"}), \
                 patch.object(engine, "_local_pilot_session_now", return_value=True), \
                 patch.object(engine, "_local_pilot_admission_paused", return_value=True):
                budget, reason = _existing_shared_budget()
            self.assertIsInstance(budget, engine.LocalPilotBudget)
            self.assertIsInstance(budget.inner, GlobalPPIBudget)
            self.assertIsNone(reason)
            self.assertEqual(len(seen), 1)
            path, policy, kwargs = seen[0]
            self.assertEqual(path, root / "artifacts/ppi-a670-diagnostic/budget.sqlite")
            self.assertIn(database, kwargs["protected"])
            self.assertEqual(policy["authority"], "LOCAL_DIAGNOSTIC_ONLY_NOT_PROVIDER_QUOTA")
            self.assertEqual(policy["endpoint_limits"],
                             {"current": 1, "book": 1, "intraday": 1})
            self.assertEqual(policy["global_limit"], 2)
            self.assertEqual(policy["window_seconds"], 3600)
            self.assertEqual(policy["max_parallel_requests"], 1)
            self.assertEqual(policy["breaker_seconds"], 3600)
            self.assertLessEqual((datetime.fromisoformat(policy["expires_at"]) -
                                  datetime.now(timezone.utc)).total_seconds(), 120)
            self.assertFalse(sidecar.exists())  # runtime budget never bootstrapped
            self.assertTrue(Path(str(sidecar) + ".bootstrap.lock").is_file())
            second_fd = os.open(str(sidecar) + ".bootstrap.lock", os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(second_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(second_fd)
            budget.close()
            with open(str(sidecar) + ".bootstrap.lock", "rb") as reopened:
                fcntl.flock(reopened.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(reopened.fileno(), fcntl.LOCK_UN)
            self.assertFalse(sidecar.exists())

    def test_manual_once_never_mints_a_second_authority_after_private_ledger_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db, sidecar = root / "paper.db", root / "artifacts/ppi-budget/global.sqlite"
            workspace = types.ModuleType("cg_paper_workspace")
            workspace.database_path = lambda: db
            workspace.artifact_root = lambda path: root / "artifacts"
            module = types.ModuleType("rc6_ppi_global_budget")
            module.RuntimePPIBudget = type("RuntimePPIBudget", (), {})
            module.budget_from_environment = lambda path: None
            module.runtime_budget_snapshot = lambda path: None
            module.SCHEMA = "RC6_GLOBAL_PPI_BUDGET_V1"
            module.supervisable_position_count = lambda *a, **kw: 0
            creations = []
            class FakeGlobal:
                def __init__(self, path, policy, **kwargs):
                    creations.append(path)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("synthetic first diagnostic, not a runtime budget")
            module.GlobalPPIBudget = FakeGlobal
            with patch.dict(sys.modules, {"cg_paper_workspace": workspace,
                                          "rc6_ppi_global_budget": module}), \
                 patch.dict(os.environ, {"RC6_A630_LOCAL_PILOT": "ONCE"}), \
                 patch.object(engine, "_local_pilot_session_now", return_value=True), \
                 patch.object(engine, "_local_pilot_admission_paused", return_value=True):
                first, first_reason = _existing_shared_budget()
                self.assertIsNone(first_reason)
                self.assertIsInstance(first, engine.LocalPilotBudget)
                first.close()
                second, second_reason = _existing_shared_budget()
                self.assertIsNone(second)
                self.assertEqual(second_reason, "PPI_LOCAL_PILOT_ALREADY_USED")
            self.assertEqual(len(creations), 1)
            self.assertFalse(sidecar.exists())

    def test_local_pilot_rejects_any_positions_or_unverified_paper_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "observer.db"
            workspace = types.ModuleType("cg_paper_workspace")
            workspace.database_path = lambda: database
            workspace.artifact_root = lambda path: root / "artifacts"
            module = types.ModuleType("rc6_ppi_global_budget")
            module.RuntimePPIBudget = type("RuntimePPIBudget", (), {})
            module.SCHEMA = "RC6_GLOBAL_PPI_BUDGET_V1"
            constructed = []
            module.GlobalPPIBudget = lambda *a, **k: constructed.append(True)
            module.runtime_budget_snapshot = lambda p: None
            module.budget_from_environment = lambda p: None
            with patch.dict(sys.modules, {"cg_paper_workspace": workspace,
                                          "rc6_ppi_global_budget": module}), \
                 patch.dict(os.environ, {"RC6_A630_LOCAL_PILOT": "ONCE"}), \
                 patch.object(engine, "_local_pilot_session_now", return_value=True), \
                 patch.object(engine, "_local_pilot_admission_paused", return_value=True):
                for count in (1, None):
                    module.supervisable_position_count = lambda *a, **k: count
                    budget, reason = _existing_shared_budget()
                    self.assertIsNone(budget)
                    self.assertEqual(reason, "PPI_LOCAL_PILOT_PAPER_LEDGER_HOLD")
            self.assertFalse(constructed)

    def test_exit_round_must_be_recent_tz_aware_and_not_future(self):
        cycle={'status':'COMPLETE','required_scope_verified_current':True,
               'recorded_at':(NOW-timedelta(seconds=299)).timestamp()}
        self.assertTrue(_exit_round_fresh(cycle,now=NOW))
        cycle['recorded_at']=(NOW-timedelta(seconds=301)).timestamp()
        self.assertFalse(_exit_round_fresh(cycle,now=NOW))
        cycle['recorded_at']=(NOW+timedelta(seconds=1)).timestamp()
        self.assertFalse(_exit_round_fresh(cycle,now=NOW))
        cycle['recorded_at']='2026-11-04T14:20:00'
        self.assertFalse(_exit_round_fresh(cycle,now=NOW))
        cycle['recorded_at']=float('inf')
        self.assertFalse(_exit_round_fresh(cycle,now=NOW))
        cycle['recorded_at']=True
        self.assertFalse(_exit_round_fresh(cycle,now=NOW))
        cycle.pop('recorded_at')
        self.assertFalse(_exit_round_fresh(cycle,now=NOW))

    def test_missing_credentials_no_get(self):
        shared = Shared()
        result = capture_two(TARGET, None, shared, lambda *a, **k: None)
        self.assertEqual(shared.calls, [])
        self.assertEqual(result["reason"], "PPI_KEYPAIR_UNAVAILABLE")

    def test_both_leases_and_two_http_digests_evidence_only(self):
        shared = Shared()
        result, readers = invoke(shared)
        self.assertEqual(readers[0].actual, ["current", "book"])
        self.assertEqual(len(shared.calls), 2)
        self.assertEqual([x[0] for x in shared.calls], ["current", "book"])
        self.assertEqual(set(shared.started), {"L1", "L2"})
        self.assertEqual(len(shared.finished), 2)
        self.assertEqual(result["state"], "EVIDENCE_ONLY_NO_GO")
        self.assertTrue(verify_receipt(result, TARGET))
        self.assertFalse(result["source_pit_verified"])
        self.assertFalse(result["independent_provider_signature_verified"])
        self.assertEqual(result["orders_sent"], 0)

    def test_three_utc_clocks_observed_individually_and_v1_archive_accepted(self):
        observations = [NOW, NOW + timedelta(milliseconds=100),
                        NOW + timedelta(milliseconds=300)]
        result, readers = invoke(Shared(), observations=observations)
        self.assertEqual(readers[0].actual, ["current", "book"])
        self.assertEqual(result["current_received_at_utc"], observations[0].isoformat())
        self.assertEqual(result["book_received_at_utc"], observations[1].isoformat())
        self.assertEqual(result["pair_completed_at_utc"], observations[2].isoformat())
        self.assertEqual(result["first_known_at_utc"], result["pair_completed_at_utc"])
        self.assertTrue(verify_receipt(result, TARGET))
        self.assertEqual(result["schema"], "rc6.a630.ppi.current-book.sample.v1")
        # Previously exported success artifacts have the same v1 SCHEMA but
        # lacked per-GET evidence. They remain readable, never causal PIT.
        archived = {key: val for key, val in result.items()
                    if key not in PER_GET_CLOCK_FIELDS}
        self.assertTrue(verify_receipt(archived, TARGET))
        self.assertFalse(archived["source_pit_verified"])

    def test_clock_callbacks_are_after_each_get_and_pair_close(self):
        trace = []

        class TracedReader(Reader):
            def current(self, *args):
                result = super().current(*args)
                trace.append("current-returned")
                return result

            def book(self, *args):
                result = super().book(*args)
                trace.append("book-returned")
                return result

            def close(self):
                trace.append("reader-closed")
                return super().close()

        def traced_clock():
            trace.append("timestamp")
            return NOW

        result = capture_two(TARGET, ("fake-key", "fake-secret"), Shared(),
                             TracedReader, clock=traced_clock,
                             http_limiter=fake_hashes, adapter_guard=fake_adapter)
        self.assertEqual(trace, ["current-returned", "timestamp", "book-returned",
                                 "timestamp", "reader-closed", "timestamp"])
        self.assertTrue(verify_receipt(result, TARGET))

    def test_current_native_future_relative_to_get1_is_not_hidden_by_book2(self):
        observations = [NOW, NOW + timedelta(seconds=3),
                        NOW + timedelta(seconds=4)]
        result, readers = invoke(Shared(), observations=observations,
                                 clock="2026-11-04T14:20:02+00:00")
        # Legacy pair-only clock would accept provider 14:20:02 by 14:20:05.
        # It was actually *in the future* when Current returned at 14:20:01.
        self.assertEqual(readers[0].actual, ["current", "book"])
        self.assertEqual(result["reason"], "PPI_NATIVE_CLOCK_STALE_OR_FUTURE")
        self.assertEqual(result["state"], "NO_VERIFICADO")
        self.assertTrue(verify_receipt(result, TARGET))
        self.assertTrue(PER_GET_CLOCK_FIELDS.isdisjoint(result))
        self.assertIsNone(result["current_http_body_sha256"])

    def test_book_native_future_relative_to_get2_fails_closed(self):
        observations = [NOW, NOW + timedelta(seconds=1),
                        NOW + timedelta(seconds=3)]
        result, _ = invoke(Shared(), observations=observations,
                           book_clock="2026-11-04T14:20:03+00:00")
        self.assertEqual(result["reason"], "PPI_NATIVE_CLOCK_STALE_OR_FUTURE")
        self.assertEqual(result["state"], "NO_VERIFICADO")
        self.assertTrue(PER_GET_CLOCK_FIELDS.isdisjoint(result))

    def test_long_book_latency_must_not_relax_legacy_pair_freshness(self):
        observations = [NOW, NOW + timedelta(seconds=125),
                        NOW + timedelta(seconds=126)]
        result, _ = invoke(Shared(), observations=observations,
                           book_clock="2026-11-04T14:22:05+00:00")
        # Current was only 1 s old at GET1 but 127 s old by pair completion.
        # It remains ineligible under the pre-existing age budget.
        self.assertEqual(result["reason"], "PPI_NATIVE_CLOCK_STALE_OR_FUTURE")
        self.assertEqual(result["state"], "NO_VERIFICADO")
        self.assertTrue(PER_GET_CLOCK_FIELDS.isdisjoint(result))

    def test_backwards_naive_or_missing_observation_fails_closed(self):
        cases = [
            [NOW, NOW - timedelta(milliseconds=1), NOW],
            [NOW, NOW + timedelta(milliseconds=1), NOW - timedelta(milliseconds=1)],
            [NOW.replace(tzinfo=None), NOW, NOW],
            [NOW, NOW.replace(tzinfo=None), NOW],
            [NOW, NOW, None],
        ]
        for observations in cases:
            with self.subTest(observations=observations):
                result, _ = invoke(Shared(), observations=observations)
                self.assertEqual(result["reason"], "PPI_NATIVE_CLOCK_INVALID")
                self.assertEqual(result["state"], "NO_VERIFICADO")
                self.assertTrue(PER_GET_CLOCK_FIELDS.isdisjoint(result))
                self.assertIsNone(result["current_http_body_sha256"])
                self.assertTrue(verify_receipt(result, TARGET))

    def test_partial_get_or_close_failure_cannot_emit_observation_or_digest(self):
        cases = (dict(shared=Shared(deny_at=2), reader={}),
                 dict(shared=Shared(), reader={"close_error": True}))
        for case in cases:
            result, _ = invoke(case["shared"], **case["reader"])
            self.assertEqual(result["state"], "NO_VERIFICADO")
            self.assertTrue(PER_GET_CLOCK_FIELDS.isdisjoint(result))
            self.assertIsNone(result["first_known_at_utc"])
            self.assertIsNone(result["current_http_body_sha256"])
            self.assertTrue(verify_receipt(result, TARGET))

    def test_receipt_rejects_partial_forged_or_reordered_clocks(self):
        valid, _ = invoke(Shared(), observations=[
            NOW, NOW + timedelta(milliseconds=100), NOW + timedelta(milliseconds=300)])
        self.assertTrue(verify_receipt(valid, TARGET))
        edits = (
            {"current_received_at_utc": "2026-11-04T14:20:02+00:00"},
            {"book_received_at_utc": "2026-11-04T14:20:00+00:00"},
            {"pair_completed_at_utc": "2026-11-04T14:20:04+00:00"},
            {"current_received_at_utc": "2026-11-04T14:20:01"},
            {"book_received_at_utc": "2026-11-04T11:20:01.100-03:00"},
        )
        for edit in edits:
            with self.subTest(edit=edit):
                self.assertFalse(verify_receipt({**valid, **edit}, TARGET))
        missing_one = {key: val for key, val in valid.items()
                       if key != "current_received_at_utc"}
        self.assertFalse(verify_receipt(missing_one, TARGET))
        invalid = {**valid, "state": "NO_VERIFICADO",
                   "reason": "PPI_NATIVE_CLOCK_INVALID"}
        self.assertFalse(verify_receipt(invalid, TARGET))

    def test_first_lease_denied_no_get(self):
        result, readers = invoke(Shared(deny_at=1))
        self.assertEqual(readers[0].actual, [])
        self.assertEqual((result["current_gets"], result["book_gets"]), (0, 0))
        self.assertEqual(result["state"], "NO_VERIFICADO")

    def test_second_lease_denied_does_not_retry(self):
        shared = Shared(deny_at=2)
        result, readers = invoke(shared)
        self.assertEqual(readers[0].actual, ["current"])
        self.assertEqual(len(shared.calls), 2)
        self.assertEqual(result["state"], "NO_VERIFICADO")
        self.assertIsNone(result["current_http_body_sha256"])

    def test_allowed_true_without_lease_fails_closed(self):
        result, readers = invoke(Shared(lease_none=True))
        self.assertEqual(readers[0].actual, [])
        self.assertEqual(result["state"], "NO_VERIFICADO")

    def test_duplicate_lease_rejected_before_second_get(self):
        result, readers = invoke(Shared(duplicate=True))
        self.assertEqual(readers[0].actual, ["current"])
        self.assertEqual(result["book_gets"], 0)
        self.assertEqual(result["state"], "NO_VERIFICADO")

    def test_endpoint_replay_and_wrong_priority_cannot_acquire(self):
        shared = Shared()
        budget = TwoGetAtomicBudget(shared)
        denied = budget.acquire("book", consumer="UNSCOPED", priority="DISCOVERY")
        self.assertFalse(denied["allowed"])
        self.assertEqual(shared.calls, [])
        denied = budget.acquire("current", consumer="SCALPING", priority="DISCOVERY")
        self.assertFalse(denied["allowed"])
        self.assertEqual(shared.calls, [])
        admitted = budget.acquire("current", consumer="UNSCOPED", priority="DISCOVERY")
        self.assertTrue(admitted["allowed"])
        self.assertFalse(budget.acquire("current", consumer="UNSCOPED", priority="DISCOVERY")["allowed"])

    def test_current_stale_and_future_fails_no_digest(self):
        for clock in ("2026-11-04T14:10:00+00:00", "2026-11-04T14:22:00+00:00"):
            result, _ = invoke(Shared(), clock=clock)
            self.assertEqual(result["state"], "NO_VERIFICADO")
            self.assertIsNone(result["book_http_body_sha256"])

    def test_book_bad_shape_no_ready(self):
        result, _ = invoke(Shared(), bad_shape=True)
        self.assertEqual(result["reason"], "PROVIDER_RESPONSE_SHAPE_INVALID")
        self.assertEqual(result["state"], "NO_VERIFICADO")

    def test_extra_provider_get_is_detected_even_with_two_leases(self):
        result, _ = invoke(Shared(), extra_get=True)
        self.assertEqual(result["reason"], "PROVIDER_GET_SCOPE_OR_COUNT_INVALID")
        self.assertEqual(result["state"], "NO_VERIFICADO")

    def test_http_digest_exact_provider_host_port_query_and_book2(self):
        class Response:
            def __init__(self, url):
                self.url = url

            def iter_content(self, chunk_size=1, decode_unicode=False):
                return iter([b"{", b'"ok":true', b"}"])

        requests = types.ModuleType("requests")
        models = types.ModuleType("requests.models")
        models.Response = Response
        requests.models = models
        current = "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Current"
        valid_query = "?Ticker=AL30&Type=BONOS&Settlement=A-24HS"
        invalid_urls = [
            current.replace("clientapi.portfoliopersonal.com", "evil.example") + valid_query,
            current.replace(".com/", ".com:8443/") + valid_query,
            current.replace("https://", "http://") + valid_query,
            current.replace("https://", "https://u:p@") + valid_query,
            current + "?Ticker=AL30&Type=ACCIONES&Settlement=A-24HS",
            current + "?Ticker=AL30&Ticker=AL30&Type=BONOS&Settlement=A-24HS",
            current + "?Ticker=AL30&Type=BONOS&Settlement=A-48HS",
        ]
        with patch.dict(sys.modules, {"requests": requests, "requests.models": models}):
            with _http_body_hashes(TARGET) as seen:
                self.assertEqual(b"".join(Response(current + valid_query).iter_content()),
                                 b'{"ok":true}')
                self.assertEqual(seen[0][1].hexdigest(), sha256(b'{"ok":true}').hexdigest())
                self.assertEqual(seen[0][2], len(b'{"ok":true}'))
                for url in invalid_urls:
                    with self.assertRaises(ValueError, msg=url):
                        list(Response(url).iter_content())
                # A second Book body, even same HTTP route, is forbidden.
                book = current.replace("Current", "Book") + valid_query
                list(Response(book).iter_content())
                with self.assertRaises(ValueError):
                    list(Response(book).iter_content())

    def test_http_adapter_blocks_search_estimate_order_redirect_and_book2_before_wire(self):
        wire_calls = []
        class HTTPAdapter:
            def send(self, request, *args, **kwargs):
                wire_calls.append((request.method, request.url))
                return object()  # no socket, only count would-be sends
        adapters = types.ModuleType('requests.adapters')
        adapters.HTTPAdapter = HTTPAdapter
        requests = types.ModuleType('requests')
        requests.adapters = adapters
        class Request:
            def __init__(self, method, url):
                self.method, self.url = method, url
        domain = 'https://clientapi.portfoliopersonal.com/api/1.0/'
        query = '?Ticker=AL30&Type=BONOS&Settlement=A-24HS'
        budget = TwoGetAtomicBudget(Shared())
        with patch.dict(sys.modules, {'requests':requests, 'requests.adapters':adapters}):
            with _strict_adapter_send(TARGET, budget) as sends:
                adapter = HTTPAdapter()
                # Configuration/Search/Estimate/Orders are not budgeted here.
                bad_routes = ['MarketData/Search', 'MarketData/Estimate',
                              'Orders/New', 'MarketData/Book']
                for route in bad_routes:
                    with self.assertRaises(ValueError, msg=route):
                        adapter.send(Request('GET', domain+route+query), verify=True)
                for url in (domain+'MarketData/Current'+query.replace('BONOS','ACCIONES'),
                            domain.replace('.com/', '.com:8443/')+'MarketData/Current'+query,
                            'https://evil.example/api/1.0/MarketData/Current'+query):
                    with self.assertRaises(ValueError):
                        adapter.send(Request('GET', url), verify=True)
                self.assertEqual(wire_calls, [])
                # Auth POST is allowed once but unsafe TLS/cancel order is not.
                with self.assertRaises(ValueError):
                    adapter.send(Request('POST', domain+'Account/LoginApi'), verify=False)
                adapter.send(Request('POST', domain+'Account/LoginApi'), verify=True)
                with self.assertRaises(ValueError):
                    adapter.send(Request('POST', domain+'Account/LoginApi'), verify=True)
                self.assertEqual(len(wire_calls), 1)
                # A correctly scoped GET must still have native lease started.
                with self.assertRaises(ValueError):
                    adapter.send(Request('GET',domain+'MarketData/Current'+query), verify=True)
                grant=budget.acquire('current',consumer='UNSCOPED',priority='DISCOVERY')
                # The product guard enters wire_scope BEFORE budget.start.
                ownership=budget.wire_scope(grant['lease'])
                with ownership:
                    with self.assertRaises(ValueError):
                        adapter.send(Request('GET',domain+'MarketData/Current'+query), verify=True)
                    budget.start(grant['lease'])
                    adapter.send(Request('GET',domain+'MarketData/Current'+query), verify=True)
                with self.assertRaises(ValueError):
                    adapter.send(Request('GET',domain+'MarketData/Current'+query), verify=True)
                with self.assertRaises(ValueError):
                    adapter.send(Request('GET',domain+'MarketData/Book'+query), verify=True)
                budget.finish(grant['lease'])
                grant=budget.acquire('book',consumer='UNSCOPED',priority='DISCOVERY')
                with budget.wire_scope(grant['lease']):
                    budget.start(grant['lease'])
                    adapter.send(Request('GET',domain+'MarketData/Book'+query), verify=True)
                with self.assertRaises(ValueError):
                    adapter.send(Request('GET',domain+'MarketData/Book'+query), verify=True)
                self.assertEqual(sends, ['/api/1.0/marketdata/current',
                                         '/api/1.0/marketdata/book'])
        self.assertEqual(len(wire_calls), 3)  # one auth POST, exactly 2 GET

    def test_real_guard_order_native_budget_acquires_before_strict_adapter_send(self):
        """Product transport guard must WRAP our strict pre-wire seam, not vice versa."""
        network = []
        class HTTPAdapter:
            def send(self, request, *args, **kwargs):
                network.append((request.method, request.url))
                return object()  # fake socket boundary, no provider GET
        base_send = HTTPAdapter.send
        adapters = types.ModuleType('requests.adapters')
        adapters.HTTPAdapter = HTTPAdapter
        requests = types.ModuleType('requests')
        requests.adapters = adapters
        class Request:
            def __init__(self, method, url):
                self.method, self.url = method, url
        domain = 'https://clientapi.portfoliopersonal.com/api/1.0/'
        query = '?Ticker=AL30&Type=BONOS&Settlement=A-24HS'
        class ProductGuardReader:
            def __init__(self, key, secret, *, budget, consumer, priority, audit):
                self.budget, self.consumer = budget, consumer
                self.priority, self.audit = priority, audit
                self.adapter=HTTPAdapter()
                self.previous=HTTPAdapter.send
                def guarded_send(adapter, request, *args, **kwargs):
                    endpoint = request.url.split('/MarketData/')[-1].split('?')[0].lower()
                    lease=None
                    if request.method=='GET' and endpoint in {'current','book'}:
                        result=budget.acquire(endpoint,consumer=consumer,priority=priority)
                        if not result.get('allowed'):
                            raise ValueError('ATOMIC_LEASE_DENIED')
                        lease=result['lease']
                        ownership=budget.wire_scope(lease)
                    try:
                        if lease:
                            with ownership:  # product guard native sequence
                                budget.start(lease)
                                response=self.previous(adapter,request,*args,**kwargs)
                        else:
                            response=self.previous(adapter,request,*args,**kwargs)
                        self.audit(request.method, '/api/1.0/'+request.url.split('/api/1.0/')[-1].split('?')[0].lower(), 'ALLOWED')
                        return response
                    finally:
                        if lease:
                            budget.finish(lease)
                HTTPAdapter.send=guarded_send
            def login_once(self):
                self.adapter.send(Request('POST',domain+'Account/LoginApi'),verify=True)
            def current(self, *args):
                self.adapter.send(Request('GET',domain+'MarketData/Current'+query),verify=True)
                return {'date':PROVIDER,'price':748.5}
            def book(self, *args):
                self.adapter.send(Request('GET',domain+'MarketData/Book'+query),verify=True)
                return {'date':PROVIDER,'bids':[],'offers':[]}
            def close(self):
                HTTPAdapter.send=self.previous
        shared=Shared()
        with patch.dict(sys.modules, {'requests':requests,'requests.adapters':adapters}):
            result=capture_two(TARGET,('k','s'),shared,ProductGuardReader,
                clock=lambda:NOW,http_limiter=fake_hashes)
        self.assertEqual(result['state'],'EVIDENCE_ONLY_NO_GO')
        self.assertEqual(result['adapter_current_sends'],1)
        self.assertEqual(result['adapter_book_sends'],1)
        self.assertEqual([endpoint for endpoint, *_ in shared.calls],['current','book'])
        self.assertEqual(len(network),3)  # auth POST + exactly Current,Book
        self.assertIs(HTTPAdapter.send,base_send)  # nesting restored completely

    def test_forged_evidence_fails_sanitizer(self):
        result, _ = invoke(Shared())
        for edit in ({"source_pit_verified": True}, {"orders_sent": 1},
                     {"current_http_body_sha256": "x" * 64},
                     {"book_gets": 0}, {"source": "IOL"},
                     {"lease_starts": 1}, {"execution_allowed": True}):
            forged = {**result, **edit}
            self.assertFalse(verify_receipt(forged, TARGET), edit)

    def _catalog_db(self, target, *, source="REQUEST_CANDIDATE",
                    extra=None, extra_source=None):
        temp = tempfile.TemporaryDirectory()
        path = Path(temp.name) / "observer_v17.db"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE paper_workspace (id INTEGER, namespace TEXT, "
                         "origin TEXT, imported_legacy INTEGER, dataset_id TEXT)")
            conn.execute("INSERT INTO paper_workspace VALUES (1,?,?,0,?)",
                         ("POROTA_PAPER_V17_FRESH", "FRESH_EMPTY", str(__import__('uuid').uuid4())))
            conn.execute("CREATE TABLE financial_instrument_catalog (ticker TEXT, "
                         "instrument_type TEXT, market TEXT, currency TEXT, "
                         "settlement TEXT, settlement_source TEXT)")
            conn.execute("INSERT INTO financial_instrument_catalog VALUES (?,?,?,?,?,?)",
                         (*target, source))
            if extra:
                conn.execute("INSERT INTO financial_instrument_catalog VALUES (?,?,?,?,?,?)",
                             (*extra, extra_source or source))
        return temp, path

    def test_host_paper_5key_primary_exact_only(self):
        temp, path = self._catalog_db(TARGET)
        try:
            result = host_preflight("1120", path, enforce_mount=False)
            self.assertEqual(result["state"], "IDENTITY_ONLY_NO_GET")
            self.assertEqual(result["target"], list(TARGET))
            self.assertEqual(result["ppi_gets"], 0)
        finally:
            temp.cleanup()

    def test_host_3key_alias_duplicate_other_currency_denied(self):
        extra = ("AL30", "BONOS", "BYMA", "USD", "A-24HS")
        temp, path = self._catalog_db(TARGET, extra=extra)
        try:
            result = host_preflight("1120", path, enforce_mount=False)
            self.assertEqual(result["reason"], "PPI_PRIMARY_FIVE_KEY_NOT_UNIQUE")
            self.assertEqual(result["target"], None)
        finally:
            temp.cleanup()

    def test_host_complementary_sibling_does_not_veto_unique_ppi(self):
        extra = ("AL30", "BONOS", "BYMA", "USD", "A-24HS")
        temp, path = self._catalog_db(TARGET, extra=extra,
                                      extra_source="IOL_COMPLEMENTARY")
        try:
            result = host_preflight("1120", path, enforce_mount=False)
            self.assertEqual(result["state"], "IDENTITY_ONLY_NO_GET")
            self.assertEqual(result["target"], list(TARGET))
            self.assertEqual(result["ppi_gets"], 0)
        finally:
            temp.cleanup()

    def test_host_ppi_native_field_also_authorizes_unique_selection(self):
        temp, path = self._catalog_db(TARGET, source="PPI_FIELD")
        try:
            result = host_preflight("1120", path, enforce_mount=False)
            self.assertEqual(result["state"], "IDENTITY_ONLY_NO_GET")
        finally:
            temp.cleanup()

    def test_host_non_ppi_source_cannot_become_ppi_primary(self):
        temp, path = self._catalog_db(TARGET, source="IOL_COMPLEMENTARY")
        try:
            result = host_preflight("1120", path, enforce_mount=False)
            self.assertEqual(result["reason"], "PPI_PRIMARY_SOURCE_NOT_ATTESTED")
        finally:
            temp.cleanup()

    def test_host_default_requires_ro_os_mount_before_sqlite(self):
        temp, path = self._catalog_db(TARGET)
        try:
            result = host_preflight("1120", path)
            self.assertEqual(result["reason"], "HOST_RO_MOUNT_REQUIRED")
            self.assertFalse(result["db_opened"])
        finally:
            temp.cleanup()

    def test_receipt_is_not_authority_even_if_native_dates_fresh(self):
        result, _ = invoke(Shared())
        self.assertFalse(result["paper_sim_verified"])
        self.assertEqual(result["source"], "PPI_SDK_HTTPS_ALLOWLIST_NOT_PROVIDER_SIGNED")
        self.assertFalse(result["raw_provider_payload_persisted"])

    def test_partial_current_then_ssh_timeout_never_reports_zero_quota(self):
        # The runner cannot know whether Current crossed the wire before
        # stderr/stdio/SSH was lost. This guard is independent of provider logs.
        for reason in ("HOST_PREFLIGHT_OR_RUNTIME_HOLD", "PPI_PROVIDER_OR_AUTH_FAILED"):
            receipt = unverified_transport_receipt(reason)
            self.assertEqual(receipt["state"], "NO_VERIFICADO")
            self.assertEqual(receipt["transport_count_authority"], "NO_VERIFICADO")
            self.assertIsNone(receipt["current_gets"])
            self.assertIsNone(receipt["book_gets"])
            self.assertEqual(receipt["orders_sent"], 0)


class TestA630RootlessEvidence(unittest.TestCase):
    """Synthetic attestation only; no SSH, Docker daemon, provider, or host write."""

    def setUp(self):
        self.cid = "a" * 64
        self.attempt = "20261117-a670"
        self.release = {
            "schema": "porota.release.v1", "source_sha": ROOTLESS_CANDIDATE_SHA,
            "source_tree": ROOTLESS_CANDIDATE_TREE, "image_id": ROOTLESS_IMAGE_ID,
            "operating_mode": "PRODUCTION_PAPER", "execution_mode": "SIMULATION",
            "real_order_capability": "BLOCKED", "real_orders_sent_required": 0,
        }
        self.release_raw = (json.dumps(self.release, sort_keys=True) + "\n").encode()
        self.current = {
            "schema": "porota.rootless.deploy.v1", "state": "VALIDADO_RUNTIME",
            "phase": "COMPLETE", "daemon_id": "synthetic-daemon",
            "source_sha": ROOTLESS_CANDIDATE_SHA, "source_tree": ROOTLESS_CANDIDATE_TREE,
            "image_id": ROOTLESS_IMAGE_ID, "operating_mode": "PRODUCTION_PAPER",
            "execution_mode": "SIMULATION", "real_orders_sent": 0,
            "release_manifest_sha256": sha256(self.release_raw).hexdigest(),
            "attempt": self.attempt,
            "roles": {"observer": self.cid, "dashboard": "b" * 64},
        }
        self.home = Path("/home/porotaadmin")
        self.info = {
            "SecurityOptions": ["name=rootless", "name=seccomp,profile=builtin"],
            "CgroupVersion": "2", "CgroupDriver": "systemd",
            "DockerRootDir": str(self.home / ".local/share/porota-rc6/docker"),
            "ID": "synthetic-daemon",
        }
        self.observer = {
            "Id": self.cid, "Name": f"/porota-rc6-observer-{self.attempt}",
            "Image": ROOTLESS_IMAGE_ID,
            "State": {"Running": True, "OOMKilled": False},
            "Config": {
                "Image": ROOTLESS_IMAGE_ID, "User": "0:0", "Entrypoint": None,
                "WorkingDir": "/app", "Cmd": ["python", "bv_paper_runtime.py"],
                "Labels": {"io.porota.project": "rc6", "io.porota.role": "observer",
                           "io.porota.attempt": self.attempt},
                "Env": ["POROTA_BUILD_SHA=" + ROOTLESS_CANDIDATE_SHA,
                        "POROTA_CANDIDATE_TREE_SHA=" + ROOTLESS_CANDIDATE_TREE,
                        "PAPER_V17_DB_PATH=/app/data/paper_v17/observer_v17.db",
                        "DASHBOARD_OPERATION_MODE=PRODUCTION_PAPER",
                        "EXECUTION_MODE=SIMULATION", "REAL_ORDER_CAPABILITY=BLOCKED"],
            },
            "HostConfig": {"ReadonlyRootfs": True, "Privileged": False,
                           "CapDrop": ["ALL"], "CapAdd": [],
                           "NetworkMode": "porota-rc6_default", "PidMode": "",
                           "Memory": 1024**3, "NanoCpus": 1_000_000_000,
                           "PidsLimit": 256, "PortBindings": {},
                           "SecurityOpt": ["no-new-privileges:true"],
                           "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=128m",
                                     "/home/botuser/.cache": "rw,noexec,nosuid,size=64m",
                                     "/home/botuser/.config": "rw,noexec,nosuid,size=16m"}},
            "Mounts": [
                {"Type": "bind", "Source": "/opt/porota-trading/data",
                 "Destination": "/app/data", "RW": True},
                {"Type": "bind", "Source": "/opt/porota-trading/.secrets/ppi_production.json",
                 "Destination": "/run/secrets/ppi_production.json", "RW": False},
                {"Type": "bind", "Source": str(self.home / ".config/porota-rc6/operator"),
                 "Destination": "/app/operator", "RW": False},
            ],
        }
        self.dashboard = {
            "Id": "b" * 64,
            "Name": f"/porota-rc6-dashboard-{self.attempt}",
            "Image": ROOTLESS_IMAGE_ID,
            "State": {"Running": True, "OOMKilled": False,
                      "Health": {"Status": "healthy"}},
            "Config": {
                "Image": ROOTLESS_IMAGE_ID, "User": "0:0", "Entrypoint": None,
                "WorkingDir": "/app", "Cmd": ["python", "o_dashboard.py"],
                "Labels": {"io.porota.project": "rc6", "io.porota.role": "dashboard",
                           "io.porota.attempt": self.attempt},
                "Env": list(self.observer["Config"]["Env"]),
            },
            "HostConfig": {**self.observer["HostConfig"],
                "PortBindings": {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8000"}]}},
            "Mounts": [m for m in self.observer["Mounts"]
                       if m["Destination"] != "/run/secrets/ppi_production.json"],
        }

    def test_release_attests_candidate_tree_image_and_paper_zero(self):
        self.assertEqual(_attest_rootless_release(self.current, self.release,
                         self.release_raw), self.cid)
        for updates in ({"source_sha": "f" * 40}, {"source_tree": "f" * 40},
                        {"image_id": "sha256:" + "f" * 64},
                        {"real_orders_sent": 1}, {"state": "NO_VERIFICADO"},
                        {"phase": "POSTFLIGHT"},
                        {"release_manifest_sha256": "a" * 64},
                        {"roles": {"observer": "alias"}}):
            with self.subTest(updates=updates):
                with self.assertRaises(ValueError):
                    _attest_rootless_release({**self.current, **updates},
                                             self.release, self.release_raw)

    def test_future_promote_artifact_coordinates_replace_legacy_pin_only_when_entire_release_matches(self):
        pins = {"source_sha": "d" * 40, "source_tree": "e" * 40,
                "image_id": "sha256:" + "f" * 64,
                "observer_cid": self.cid, "dashboard_cid": "b" * 64}
        new_release = {**self.release, **{k: pins[k] for k in (
            "source_sha", "source_tree", "image_id")}}
        new_raw = (json.dumps(new_release, sort_keys=True) + "\n").encode()
        new_current = {**self.current, **{k: pins[k] for k in (
            "source_sha", "source_tree", "image_id")},
                       "release_manifest_sha256": sha256(new_raw).hexdigest()}
        new_observer = json.loads(json.dumps(self.observer))
        new_observer["Image"] = pins["image_id"]
        new_observer["Config"]["Image"] = pins["image_id"]
        new_observer["Config"]["Env"] = [
            "POROTA_BUILD_SHA=" + pins["source_sha"],
            "POROTA_CANDIDATE_TREE_SHA=" + pins["source_tree"],
            *self.observer["Config"]["Env"][2:]]
        self.assertEqual(_attest_rootless_release(new_current, new_release,
                         new_raw, expected=pins), self.cid)
        self.assertTrue(_attest_rootless_observer(self.info, new_observer,
                        new_current, uid=1000, home=self.home, expected=pins))
        with self.assertRaises(ValueError):
            _attest_rootless_release({**new_current, "roles": {
                "observer": "9" * 64, "dashboard": "b" * 64}},
                new_release, new_raw, expected=pins)
        with self.assertRaises(ValueError):
            _attest_rootless_release(new_current, {**new_release,
                "image_id": ROOTLESS_IMAGE_ID}, new_raw, expected=pins)
        with self.assertRaises(ValueError):
            _attest_rootless_observer(self.info, self.observer,
                new_current, uid=1000, home=self.home, expected=pins)

    def test_release_requires_real_blocked_capability_and_exact_manifest(self):
        for change in ({"real_order_capability": "ALLOWED"},
                       {"real_orders_sent_required": 1},
                       {"source_sha": "f" * 40}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    _attest_rootless_release(self.current, {**self.release, **change},
                                             self.release_raw)

    def test_rootless_observer_matches_private_daemon_and_immutable_cid(self):
        self.assertTrue(_attest_rootless_observer(self.info, self.observer,
                        self.current, uid=1000, home=self.home))
        for info_update in ({"SecurityOptions": ["name=seccomp"]},
                            {"DockerRootDir": "/var/lib/docker"},
                            {"CgroupVersion": "1"}):
            with self.subTest(info_update=info_update):
                with self.assertRaises(ValueError):
                    _attest_rootless_observer({**self.info, **info_update}, self.observer,
                                              self.current, uid=1000, home=self.home)

    def test_dashboard_is_live_inspected_at_exact_pinned_cid_health_image_and_paper(self):
        self.assertTrue(engine._attest_rootless_dashboard(
            self.dashboard, self.current, uid=1000, home=self.home))
        for change in ({"Id": "9" * 64}, {"Image": "sha256:" + "f" * 64},
                       {"State": {"Running": False}},
                       {"State": {"Running": True,
                                  "Health": {"Status": "healthy"}}},
                       {"State": {"Running": True, "Health": {"Status": "unhealthy"}}},
                       {"Config": {**self.dashboard["Config"], "Cmd": ["python", "evil.py"]}},
                       {"Mounts": [*self.dashboard["Mounts"],
                           {"Type": "bind", "Source": "/opt/porota-trading/.secrets/ppi_production.json",
                            "Destination": "/run/secrets/ppi_production.json", "RW": False}]},
                       {"Mounts": [*self.dashboard["Mounts"],
                           {"Type": "tmpfs", "Destination": "/run/secrets"}]},
                       {"HostConfig": {**self.dashboard["HostConfig"], "Tmpfs": {
                           "/tmp": "rw,exec,size=128m"}}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                engine._attest_rootless_dashboard(
                    {**self.dashboard, **change}, self.current, uid=1000, home=self.home)
        with_extra_tmpfs = {**self.dashboard, "Mounts": [*self.dashboard["Mounts"],
            {"Type": "tmpfs", "Destination": "/tmp", "RW": True}]}
        self.assertTrue(engine._attest_rootless_dashboard(
            with_extra_tmpfs, self.current, uid=1000, home=self.home))

    def test_rootless_observer_rejects_foreign_label_command_and_secret_rw(self):
        cases = [
            {"Name": "/porota_production_observer"},
            {"Image": "sha256:" + "f" * 64},
            {"State": {"Running": False}},
            {"Config": {**self.observer["Config"], "Cmd": ["python", "evil.py"]}},
            {"Config": {**self.observer["Config"], "Env": [
                "POROTA_BUILD_SHA=" + ROOTLESS_CANDIDATE_SHA,
                "POROTA_BUILD_SHA=" + ROOTLESS_CANDIDATE_SHA]}},
            {"Mounts": [{**row, "RW": True} if row["Destination"] ==
                "/run/secrets/ppi_production.json" else row
                for row in self.observer["Mounts"]]},
            {"Mounts": [*self.observer["Mounts"],
                        {"Type": "tmpfs", "Destination": "/run/secrets", "RW": True}]},
            {"HostConfig": {**self.observer["HostConfig"], "NetworkMode": "host"}},
            {"HostConfig": {**self.observer["HostConfig"], "PidMode": "host"}},
            {"HostConfig": {**self.observer["HostConfig"], "CapAdd": ["SYS_ADMIN"]}},
            {"HostConfig": {**self.observer["HostConfig"], "Memory": 0}},
            {"HostConfig": {**self.observer["HostConfig"], "PidsLimit": 0}},
            {"HostConfig": {**self.observer["HostConfig"], "NanoCpus": 3_000_000_000}},
            {"HostConfig": {**self.observer["HostConfig"], "Tmpfs": {"/tmp": "rw,exec,size=128m"}}},
        ]
        for change in cases:
            with self.subTest(change=list(change)):
                with self.assertRaises(ValueError):
                    _attest_rootless_observer(self.info, {**self.observer, **change},
                                              self.current, uid=1000, home=self.home)

    def test_one_rootless_exec_reuses_exact_script_and_returns_only_signed_budget_evidence(self):
        valid, _ = invoke(Shared())
        context = {"cid": self.cid, "state_token": "d" * 64,
                   "docker": ("/usr/bin/docker", "--host",
                              "unix:///run/user/1000/porota-rc6-docker.sock"),
                   "environment": {"LC_ALL": "C"}}
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0,
                                               stdout=json.dumps(valid).encode(), stderr=b"")

        target_b64 = base64.b64encode(json.dumps(list(TARGET)).encode()).decode()
        with patch("scripts.rc6_a630_host_ppi_current_book._rootless_context",
                   side_effect=[context, context]):
            result = rootless_host_capture(target_b64, ROOTLESS_CANDIDATE_SHA,
                ROOTLESS_CANDIDATE_TREE, ROOTLESS_IMAGE_ID, b"# bounded audited source\n",
                run=fake_run)
        self.assertEqual(result["state"], "EVIDENCE_ONLY_NO_GO")
        self.assertTrue(result["runtime_pin_verified"])
        self.assertFalse(result["source_pit_verified"])
        self.assertEqual(result["receipt"]["current_gets"], 1)
        self.assertEqual(result["receipt"]["book_gets"], 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][:3], context["docker"])
        self.assertEqual(calls[0][0][-5:], (self.cid, "python", "-B", "-", "container-capture"))
        self.assertEqual(calls[0][1]["input"], b"# bounded audited source\n")
        self.assertNotIn("sudo", " ".join(calls[0][0]))

    def test_local_pilot_is_only_forwarded_to_explicit_rootless_capture(self):
        valid, _ = invoke(Shared())
        context = {"cid": self.cid, "state_token": "d" * 64,
                   "docker": ("/usr/bin/docker", "--host",
                              "unix:///run/user/1000/porota-rc6-docker.sock"),
                   "environment": {"LC_ALL": "C"}}
        commands = []
        def run(command, **kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0,
                                               stdout=json.dumps(valid).encode(), stderr=b"")
        target = base64.b64encode(json.dumps(list(TARGET)).encode()).decode()
        with patch("scripts.rc6_a630_host_ppi_current_book._rootless_context",
                   side_effect=[context, context]):
            rootless_host_capture(target, ROOTLESS_CANDIDATE_SHA, ROOTLESS_CANDIDATE_TREE,
                                  ROOTLESS_IMAGE_ID, b"program", local_pilot=True,
                                  pilot_slot="1120", pilot_date="2026-11-18", run=run)
        self.assertEqual(len(commands), 1)
        self.assertIn("RC6_A630_LOCAL_PILOT=ONCE", commands[0])
        self.assertIn("RC6_A630_LOCAL_SLOT=1120", commands[0])
        self.assertIn("RC6_A630_LOCAL_ART_DATE=2026-11-18", commands[0])

    def test_rootless_mismatch_or_exec_timeout_fails_closed_without_false_get_zero(self):
        target_b64 = base64.b64encode(json.dumps(list(TARGET)).encode()).decode()
        no_pin = rootless_host_capture(target_b64, "f" * 40,
            ROOTLESS_CANDIDATE_TREE, ROOTLESS_IMAGE_ID, b"program")
        self.assertEqual(no_pin["reason"], "ROOTLESS_PIN_MISMATCH")
        self.assertIsNone(no_pin["receipt"]["current_gets"])
        context = {"cid": self.cid, "state_token": "d" * 64,
                   "docker": ("/usr/bin/docker", "--host",
                              "unix:///run/user/1000/porota-rc6-docker.sock"),
                   "environment": {"LC_ALL": "C"}}
        with patch("scripts.rc6_a630_host_ppi_current_book._rootless_context",
                   return_value=context):
            def timeout(command, **kwargs):
                raise subprocess.TimeoutExpired(command, 55)
            uncertain = rootless_host_capture(target_b64, ROOTLESS_CANDIDATE_SHA,
                ROOTLESS_CANDIDATE_TREE, ROOTLESS_IMAGE_ID, b"program", run=timeout)
        self.assertEqual(uncertain["state"], "NO_VERIFICADO")
        self.assertEqual(uncertain["reason"], "ROOTLESS_DOCKER_TRANSPORT_HOLD")
        self.assertIsNone(uncertain["receipt"]["current_gets"])
        self.assertEqual(uncertain["receipt"]["transport_count_authority"], "NO_VERIFICADO")

    def test_rootless_state_change_after_provider_blocks_attestation(self):
        valid, _ = invoke(Shared())
        before = {"cid": self.cid, "state_token": "d" * 64,
                  "docker": ("/usr/bin/docker", "--host", "unix:///run/user/1000/porota-rc6-docker.sock"),
                  "environment": {"LC_ALL": "C"}}
        after = {**before, "state_token": "e" * 64}
        target_b64 = base64.b64encode(json.dumps(list(TARGET)).encode()).decode()
        with patch("scripts.rc6_a630_host_ppi_current_book._rootless_context",
                   side_effect=[before, after]):
            result = rootless_host_capture(target_b64, ROOTLESS_CANDIDATE_SHA,
                ROOTLESS_CANDIDATE_TREE, ROOTLESS_IMAGE_ID, b"program",
                run=lambda cmd, **kwargs: subprocess.CompletedProcess(
                    cmd, 0, stdout=json.dumps(valid).encode(), stderr=b""))
        self.assertEqual(result["reason"], "ROOTLESS_STATE_DRIFT_HOLD")
        self.assertFalse(result["runtime_pin_verified"])
        self.assertIsNone(result["receipt"]["book_gets"])

    def test_rootless_private_fd_walk_rejects_symlink_and_release_drift(self):
        with tempfile.TemporaryDirectory(prefix="a630-fd-private-") as temporary:
            home = Path(temporary) / "account"
            parent = home / ".local/state/porota-rc6"
            pinned = parent / "releases" / ROOTLESS_CANDIDATE_SHA
            pinned.mkdir(parents=True, mode=0o700)
            for directory in (home, home / ".local", home / ".local/state",
                              parent, parent / "releases", pinned):
                directory.chmod(0o700)
            current_path = parent / "current.json"
            release_path = pinned / "release.json"
            current_path.write_text(json.dumps(self.current))
            release_path.write_bytes(self.release_raw)
            current_path.chmod(0o600)
            release_path.chmod(0o600)
            self.assertEqual(_rootless_state(home, os.getuid())[1], self.cid)
            release_path.rename(pinned / "release.original")
            release_path.symlink_to(pinned / "release.original")
            with self.assertRaises(ValueError):
                _rootless_state(home, os.getuid())
            release_path.unlink()
            release_path.write_bytes(b"{}")
            with self.assertRaises(ValueError):
                _rootless_state(home, os.getuid())
            release_path.write_bytes(self.release_raw)
            (home / ".local").chmod(0o777)
            with self.assertRaises(ValueError):
                _rootless_state(home, os.getuid())

    def test_private_fd_walk_selects_only_artifact_pinned_new_source_and_not_old_p0(self):
        pins = {"source_sha": "d" * 40, "source_tree": "e" * 40,
                "image_id": "sha256:" + "f" * 64,
                "observer_cid": self.cid, "dashboard_cid": "b" * 64}
        release = {**self.release, **{k: pins[k] for k in (
            "source_sha", "source_tree", "image_id")}}
        raw = (json.dumps(release, sort_keys=True) + "\n").encode()
        current = {**self.current, **{k: pins[k] for k in (
            "source_sha", "source_tree", "image_id")},
                   "release_manifest_sha256": sha256(raw).hexdigest()}
        with tempfile.TemporaryDirectory(prefix="a670-release-fd-") as folder:
            home = Path(folder) / "account"
            state = home / ".local/state/porota-rc6"
            released = state / "releases" / pins["source_sha"]
            released.mkdir(parents=True, mode=0o700)
            for directory in (home, home / ".local", home / ".local/state",
                              state, state / "releases", released):
                directory.chmod(0o700)
            (state / "current.json").write_text(json.dumps(current))
            (released / "release.json").write_bytes(raw)
            (state / "current.json").chmod(0o600)
            (released / "release.json").chmod(0o600)
            self.assertEqual(_rootless_state(home, os.getuid(), expected=pins)[1], self.cid)
            with self.assertRaises(ValueError):
                _rootless_state(home, os.getuid())  # previous P0 pin cannot claim new one
            with self.assertRaises(ValueError):
                _rootless_state(home, os.getuid(), expected={**pins,
                    "dashboard_cid": "9" * 64})

    def test_rootless_context_socket_and_daemon_identity_fail_closed_offhost(self):
        with tempfile.TemporaryDirectory(prefix="a630-socket-") as temporary:
            home = Path(temporary) / "home"
            root = home / ".local/state/porota-rc6"
            pinned = root / "releases" / ROOTLESS_CANDIDATE_SHA
            pinned.mkdir(parents=True, mode=0o700)
            for directory in (home, home / ".local", home / ".local/state",
                              root, root / "releases", pinned):
                directory.chmod(0o700)
            (root / "current.json").write_text(json.dumps(self.current))
            (pinned / "release.json").write_bytes(self.release_raw)
            (root / "current.json").chmod(0o600)
            (pinned / "release.json").chmod(0o600)
            runtime = Path(temporary) / "runtime"
            runtime.mkdir(mode=0o700)
            runtime.chmod(0o700)
            socket_path = runtime / "porota-rc6-docker.sock"
            query_calls = []

            def fake_run(command, **kwargs):
                query_calls.append(command)
                if command[3] == "info":
                    info = {**self.info, "DockerRootDir": str(home / ".local/share/porota-rc6/docker")}
                    return subprocess.CompletedProcess(command, 0, stdout=json.dumps(info))
                item = (self.dashboard if command[-1] == "b" * 64 else self.observer)
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps([{
                    **item,
                    "Mounts": [({**m, "Source": str(home / ".config/porota-rc6/operator")}
                                if m["Destination"] == "/app/operator" else m)
                               for m in item["Mounts"]],
                }]))

            with patch("scripts.rc6_a630_host_ppi_current_book.ROOTLESS_UID", os.getuid()), \
                 patch("scripts.rc6_a630_host_ppi_current_book.pwd.getpwuid",
                       return_value=types.SimpleNamespace(pw_dir=str(home), pw_name="synthetic")):
                with self.assertRaises(ValueError):
                    _rootless_context(run=fake_run, runtime_override=runtime)
                self.assertEqual(query_calls, [])
                original_lstat = Path.lstat

                def synthetic_socket(path):
                    if path == socket_path:
                        return types.SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600,
                                                     st_uid=os.getuid())
                    return original_lstat(path)

                with patch.object(Path, "lstat", synthetic_socket):
                    context = _rootless_context(run=fake_run, runtime_override=runtime)
                    self.assertEqual(context["cid"], self.cid)
                    self.assertEqual(len(query_calls), 3)
                    self.assertEqual(query_calls[-1][-1], "b" * 64)
                    self.assertEqual(query_calls[0][:3],
                                     ("/usr/bin/docker", "--host", f"unix://{socket_path}"))
                    pins = {"source_sha": ROOTLESS_CANDIDATE_SHA,
                            "source_tree": ROOTLESS_CANDIDATE_TREE,
                            "image_id": ROOTLESS_IMAGE_ID,
                            "observer_cid": self.cid,
                            "dashboard_cid": "b" * 64}
                    self.assertEqual(_rootless_context(run=fake_run,
                        runtime_override=runtime, expected=pins)["cid"], self.cid)
                    with self.assertRaises(ValueError):
                        _rootless_context(run=fake_run, runtime_override=runtime,
                            expected={**pins, "observer_cid": "9" * 64})
                socket_path.symlink_to(root / "current.json")
                with self.assertRaises(ValueError):
                    _rootless_context(run=fake_run, runtime_override=runtime)
                self.assertEqual(len(query_calls), 6)

    def test_sanitizer_preserves_only_validated_typed_budget_reason(self):
        token = base64.b64encode(json.dumps(list(TARGET)).encode()).decode()
        inner = capture_two(TARGET, None, Shared(), lambda *a, **k: None)
        self.assertTrue(verify_receipt(inner, TARGET))
        wrapper = {"schema": "rc6.a630.rootless.capture.v1",
                   "state": "NO_VERIFICADO", "reason": "PPI_KEYPAIR_UNAVAILABLE",
                   "runtime_pin_verified": True, "host_file_writes": 0,
                   "provider_payload_uploaded": False,
                   "paper_admission_certified": False, "source_pit_verified": False,
                   "receipt": inner}
        selected = verified_rootless_wrapper(json.dumps(wrapper).encode(), TARGET)
        self.assertIsNotNone(selected)
        self.assertEqual(selected["reason"], "PPI_KEYPAIR_UNAVAILABLE")
        self.assertEqual(selected["receipt"]["current_gets"], 0)
        for forged in ({**wrapper, "secret": "PRIVATE_TOKEN"},
                       {**wrapper, "reason": "PPI_KEYPAIR_UNAVAILABLE\nPRIVATE"},
                       {**wrapper, "receipt": {**inner, "source_pit_verified": True}},
                       {**wrapper, "receipt": {**inner, "reason": "ATOMIC_LEASE_DENIED"}}):
            with self.subTest(forged=list(forged)):
                self.assertIsNone(verified_rootless_wrapper(
                    json.dumps(forged).encode(), TARGET))

    def test_sanitizer_accepts_bounded_unknown_counts_on_transport_failure(self):
        from scripts.rc6_a630_host_ppi_current_book import unverified_transport_receipt
        inner = unverified_transport_receipt("ROOTLESS_SOCKET_HOLD")
        wrapper = {"schema": "rc6.a630.rootless.capture.v1",
                   "state": "NO_VERIFICADO", "reason": "ROOTLESS_SOCKET_HOLD",
                   "runtime_pin_verified": False, "host_file_writes": 0,
                   "provider_payload_uploaded": False,
                   "paper_admission_certified": False, "source_pit_verified": False,
                   "receipt": inner}
        self.assertEqual(verified_rootless_wrapper(json.dumps(wrapper).encode(), TARGET), wrapper)
        self.assertIsNone(wrapper["receipt"]["current_gets"])

    def test_rootless_cli_argument_order_preserves_target_and_candidate_identity(self):
        target_b64 = base64.b64encode(json.dumps(list(TARGET)).encode()).decode()
        outcome = {"schema": "rc6.a630.rootless.capture.v1", "state": "NO_VERIFICADO",
                   "reason": "ROOTLESS_SOCKET_HOLD"}
        with patch.object(engine, "rootless_host_capture", return_value=outcome) as capture, \
             patch.object(sys, "argv", ["-c", "rootless-capture", target_b64,
                                       ROOTLESS_CANDIDATE_SHA, ROOTLESS_CANDIDATE_TREE,
                                       ROOTLESS_IMAGE_ID]), \
             patch.dict(engine.__dict__, {"__RC6_SOURCE_BYTES__": b"source"}):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(engine.main(), 2)
            self.assertEqual(json.loads(out.getvalue()), outcome)
        capture.assert_called_once_with(target_b64, ROOTLESS_CANDIDATE_SHA,
                                        ROOTLESS_CANDIDATE_TREE, ROOTLESS_IMAGE_ID, b"source",
                                        local_pilot=False, pilot_slot="", pilot_date="",
                                        postclose=False)

    def test_observer_enqueue_rootless_exec_is_single_sqlite_child_without_ppi_client(self):
        manifest = "c" * 64
        roles = {"observer": self.cid, "dashboard": "b" * 64}
        context = {"cid": self.cid, "state_token": "d" * 64,
                   "release_manifest_sha256": manifest,
                   "roles": roles,
                   "docker": ("/usr/bin/docker", "--host",
                              "unix:///run/user/1000/porota-rc6-docker.sock"),
                   "environment": {"LC_ALL": "C"}}
        expected = {"schema": "rc6.p0.observer-command.child.v1", "state": "EVIDENCE",
                    "reason": "OBSERVER_COMMAND_FINISHED", "art_date": "2026-11-18",
                    "family": "BONOS", "command_id": 123, "command_status": "EVIDENCE",
                    "provider_status": "FRESH_OBSERVED", "paper_ready": False,
                    "order_authority": False, "db_rows_inserted": 1}
        calls = []
        def run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0,
                stdout=json.dumps(expected).encode(), stderr=b"")
        with patch.object(engine, "_rootless_context", side_effect=[context, context]):
            wrapper = engine.rootless_host_enqueue(
                "2026-11-18", "BONOS", ROOTLESS_CANDIDATE_SHA,
                ROOTLESS_CANDIDATE_TREE, ROOTLESS_IMAGE_ID, manifest,
                b"# source in memory\n", run=run,
                expected_roles=roles, promote_artifact_digest="sha256:" + "1" * 64)
        self.assertEqual(wrapper["state"], "EVIDENCE")
        self.assertTrue(wrapper["runtime_pin_verified"])
        self.assertEqual(wrapper["receipt"], expected)
        self.assertEqual(len(calls), 1)
        command = calls[0][0]
        self.assertEqual(command[:3], context["docker"])
        self.assertEqual(command[-5:], (self.cid, "python", "-B", "-", "container-enqueue"))
        self.assertIn("POROTA_RUNTIME_SCHEMA_READY=1", command)
        self.assertNotIn("container-capture", command)
        self.assertNotIn("RC6_A630_LOCAL_PILOT=ONCE", command)
        self.assertNotIn("sudo", " ".join(command))
        self.assertEqual(calls[0][1]["input"], b"# source in memory\n")

    def test_observer_enqueue_rejects_unpinned_manifest_or_drift_without_retry(self):
        self.assertEqual(engine.rootless_host_enqueue(
            "2026-11-18", "BONOS", ROOTLESS_CANDIDATE_SHA,
            ROOTLESS_CANDIDATE_TREE, ROOTLESS_IMAGE_ID, "UNPINNED", b"source")
            ["reason"], "ROOTLESS_PIN_MISMATCH")
        valid = {"schema": "rc6.p0.observer-command.child.v1", "state": "QUEUED",
                 "reason": "OBSERVER_COMMAND_POLL_TIMEOUT", "art_date": "2026-11-18",
                 "family": "BONOS", "command_id": 41, "command_status": "RUNNING",
                 "provider_status": None, "paper_ready": False,
                 "order_authority": False, "db_rows_inserted": 1}
        before = {"cid": self.cid, "state_token": "d" * 64,
                  "release_manifest_sha256": "c" * 64,
                  "roles": {"observer": self.cid, "dashboard": "b" * 64},
                  "docker": ("/usr/bin/docker", "--host", "unix:///run/user/1000/porota-rc6-docker.sock"),
                  "environment": {"LC_ALL": "C"}}
        after = {**before, "state_token": "e" * 64}
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 2,
                stdout=json.dumps(valid).encode(), stderr=b"")
        with patch.object(engine, "_rootless_context", side_effect=[before, after]):
            wrapper = engine.rootless_host_enqueue("2026-11-18", "BONOS",
                ROOTLESS_CANDIDATE_SHA, ROOTLESS_CANDIDATE_TREE, ROOTLESS_IMAGE_ID,
                "c" * 64, b"source", run=run,
                expected_roles=before["roles"],
                promote_artifact_digest="sha256:" + "1" * 64)
        self.assertEqual(len(calls), 1)
        self.assertEqual(wrapper["state"], "NO_VERIFICADO")
        self.assertEqual(wrapper["reason"], "ROOTLESS_STATE_DRIFT_HOLD")
        self.assertIsNone(wrapper["receipt"]["command_id"])

    def test_open_rootless_enqueues_one_scanner_child_without_new_ppi_client(self):
        manifest = "c" * 64
        roles = {"observer": self.cid, "dashboard": "b" * 64}
        context = {"cid": self.cid, "state_token": "d" * 64,
                   "release_manifest_sha256": manifest,
                   "roles": roles,
                   "docker": ("/usr/bin/docker", "--host",
                              "unix:///run/user/1000/porota-rc6-docker.sock"),
                   "environment": {"LC_ALL": "C"}}
        child = {"schema": "rc6.p0.open-ten-family.child.v1", "state": "QUEUED",
                 "reason": "OBSERVER_COMMAND_POLL_TIMEOUT", "art_date": "2026-11-19",
                 "family": "ALL", "command_id": 27, "command_status": "RUNNING",
                 "provider_status": None, "paper_ready": False, "order_authority": False,
                 "db_rows_inserted": 1, "summary": None}
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 2,
                stdout=json.dumps(child).encode(), stderr=b"")
        with patch.object(engine, "_rootless_context", side_effect=[context, context]):
            wrapper = engine.rootless_host_enqueue("2026-11-19", "ALL",
                "d" * 40, "e" * 40,
                "sha256:" + "f" * 64, manifest, b"# audited source in memory\n",
                run=run, open_batch=True, expected_roles=roles,
                promote_artifact_digest="sha256:" + "1" * 64)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-5:], (self.cid, "python", "-B", "-", "container-enqueue-open"))
        self.assertNotIn("container-capture", calls[0])
        self.assertNotIn("PPI_PRODUCTION_SECRET_FILE", " ".join(calls[0]))
        self.assertEqual(wrapper["state"], "NO_VERIFICADO")
        self.assertTrue(wrapper["runtime_pin_verified"])
        self.assertEqual(wrapper["receipt"], child)

    def test_open_release_from_artifact_rejects_missing_cid_or_digest_before_docker(self):
        calls = []
        def forbidden(*args, **kwargs):
            calls.append(args)
            raise AssertionError("DOCKER_MUST_NOT_START")
        args = ("2026-11-19", "ALL", "d" * 40, "e" * 40,
                "sha256:" + "f" * 64, "c" * 64, b"# program\n")
        for roles, digest in ((None, "sha256:" + "a" * 64),
                              ({"observer": self.cid}, "sha256:" + "a" * 64),
                              ({"observer": self.cid, "dashboard": "b" * 64}, "UNVERIFIED")):
            with self.subTest(roles=roles, digest=digest):
                result = engine.rootless_host_enqueue(*args, open_batch=True,
                    expected_roles=roles, promote_artifact_digest=digest, run=forbidden)
                self.assertEqual((result["state"], result["reason"]),
                                 ("NO_VERIFICADO", "ROOTLESS_PIN_MISMATCH"))
        self.assertEqual(calls, [])

    def test_open_rootless_verifier_does_not_accept_legacy_postclose_receipt(self):
        old = {"schema": "rc6.p0.observer-command.child.v1", "state": "EVIDENCE",
               "reason": "OBSERVER_COMMAND_FINISHED", "art_date": "2026-11-19",
               "family": "BONOS", "command_id": 27, "command_status": "EVIDENCE",
               "provider_status": "FRESH_OBSERVED", "paper_ready": False,
               "order_authority": False, "db_rows_inserted": 1}
        wrapper = {"schema": "rc6.p0.observer-command.rootless.v1", "state": "EVIDENCE",
                   "reason": "OBSERVER_COMMAND_FINISHED", "runtime_pin_verified": True,
                   "ppi_client_or_login_by_action": False,
                   "real_orders_sent_by_action": 0, "receipt": old}
        self.assertIsNone(engine.verified_command_wrapper(json.dumps(wrapper).encode(),
                           "2026-11-19", "ALL", open_batch=True))

    def test_open_wrapper_rejects_mismatched_family_even_with_valid_child(self):
        child = {"schema": "rc6.p0.open-ten-family.child.v1", "state": "QUEUED",
                 "reason": "OBSERVER_COMMAND_POLL_TIMEOUT", "art_date": "2026-11-19",
                 "family": "ALL", "command_id": 3, "command_status": "RUNNING",
                 "provider_status": None, "paper_ready": False,
                 "order_authority": False, "db_rows_inserted": 1, "summary": None}
        wrapper = {"schema": "rc6.p0.observer-command.rootless.v1",
                   "state": "NO_VERIFICADO", "reason": "OBSERVER_COMMAND_POLL_TIMEOUT",
                   "runtime_pin_verified": True, "ppi_client_or_login_by_action": False,
                   "real_orders_sent_by_action": 0, "receipt": child}
        data = json.dumps(wrapper).encode()
        self.assertIsNotNone(engine.verified_command_wrapper(data, "2026-11-19",
                             "ALL", open_batch=True))
        self.assertIsNone(engine.verified_command_wrapper(data, "2026-11-19",
                          "BONOS", open_batch=True))


class TestA630ObserverCommandSQLite(unittest.TestCase):
    """Real local SQLite; no host, SDK, network, release mutation or provider."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rc6-p0-command-")
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "observer_v17.db"
        self.now = datetime(2026, 11, 18, 20, 15, tzinfo=timezone.utc)
        with sqlite3.connect(self.db) as c:
            c.executescript("""
            CREATE TABLE paper_workspace(id INTEGER PRIMARY KEY, namespace TEXT,
                origin TEXT, imported_legacy INTEGER, dataset_id TEXT);
            INSERT INTO paper_workspace VALUES (1,'POROTA_PAPER_V17_FRESH',
                'FRESH_EMPTY',0,'123e4567-e89b-12d3-a456-426614174000');
            CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,
                real_orders_sent INTEGER,ppi_auth TEXT,heartbeat_at TEXT);
            INSERT INTO observer_state VALUES (1,'PRODUCTION_PAPER',0,'OK',
                '2026-11-18T20:14:50+00:00');
            CREATE TABLE observer_commands(
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
                command TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT,
                finished_at TEXT, result TEXT NOT NULL DEFAULT '');
            CREATE TABLE paper_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT, event_at TEXT NOT NULL,
                source TEXT NOT NULL, event_type TEXT NOT NULL, paper_id TEXT,
                detail TEXT NOT NULL);
            CREATE INDEX idx_shadow_event_type_at_jd
                ON paper_events(event_type,julianday(event_at));
            """)
        self.environment = {"POROTA_BUILD_SHA": ROOTLESS_CANDIDATE_SHA,
            "POROTA_CANDIDATE_TREE_SHA": ROOTLESS_CANDIDATE_TREE,
            "DASHBOARD_OPERATION_MODE": "PRODUCTION_PAPER",
            "EXECUTION_MODE": "SIMULATION", "REAL_ORDER_CAPABILITY": "BLOCKED",
            "PAPER_V17_DB_PATH": str(self.db),
            "POROTA_RUNTIME_SCHEMA_READY": "1",
            "RC6_P0_EXPECTED_PRODUCT_SHA": ROOTLESS_CANDIDATE_SHA,
            "RC6_P0_EXPECTED_PRODUCT_TREE": ROOTLESS_CANDIDATE_TREE,
            "RC6_P0_EXPECTED_IMAGE_ID": ROOTLESS_IMAGE_ID}
        self.version = types.SimpleNamespace(MODE="PRODUCTION_PAPER",
            EXECUTION="SIMULATED", REAL_ORDER_CAPABILITY="BLOCKED")

    def enqueue(self, **kwargs):
        arguments = {"db": self.db, "now": self.now, "poll_seconds": 0}
        arguments.update(kwargs)
        with patch.object(engine, "_global_ppi_single_session_custody_verified",
                          return_value=True), \
             patch.dict(os.environ, self.environment), \
             patch.dict(sys.modules, {"_version": self.version}):
            return engine.container_enqueue("2026-11-18", "BONOS", **arguments)

    def open_enqueue(self, **kwargs):
        when = datetime(2026, 11, 19, 13, 45, tzinfo=timezone.utc)
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE observer_state SET heartbeat_at=? WHERE id=1",
                       ((when - timedelta(seconds=10)).isoformat(),))
        arguments = {"db": self.db, "now": when, "poll_seconds": 0}
        arguments.update(kwargs)
        with patch.object(engine, "_global_ppi_single_session_custody_verified",
                          return_value=True), \
             patch.dict(os.environ, self.environment), \
             patch.dict(sys.modules, {"_version": self.version}):
            return engine.container_enqueue_open_batch("2026-11-19", "ALL", **arguments)

    def test_global_ppi_single_session_custody_blocks_both_db_queue_entrypoints(self):
        # No physical global login-actor receipt yet. Existing scanner auth,
        # PAPER0, old rootless manifest and any supplied environment flag are
        # insufficient. Unit tests for downstream SQL use explicit mocking;
        # production CLI has no externally supplied bypass.
        with patch.dict(os.environ, {**self.environment,
                                      "RC6_PPI_SINGLE_SESSION_GLOBAL_ATTESTED": "1"}), \
             patch.dict(sys.modules, {"_version": self.version}):
            open_result = engine.container_enqueue_open_batch("2026-11-19", "ALL",
                db=self.db, now=datetime(2026, 11, 19, 13, 45, tzinfo=timezone.utc),
                poll_seconds=0)
            postclose_result = engine.container_enqueue("2026-11-18", "BONOS",
                db=self.db, now=self.now, poll_seconds=0)
        for result in (open_result, postclose_result):
            self.assertEqual((result["state"], result["reason"]),
                             ("HOLD", "PPI_SINGLE_SESSION_GLOBAL_UNVERIFIED"))
            self.assertEqual(result["db_rows_inserted"], 0)
            self.assertFalse(result["paper_ready"])
            self.assertFalse(result["order_authority"])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM observer_commands").fetchone()[0], 0)

    def test_future_release_child_paper_build_identity_is_runtime_pinned_not_old_p0(self):
        future = dict(self.environment,
            POROTA_BUILD_SHA="d" * 40,
            POROTA_CANDIDATE_TREE_SHA="e" * 40,
            RC6_P0_EXPECTED_PRODUCT_SHA="d" * 40,
            RC6_P0_EXPECTED_PRODUCT_TREE="e" * 40,
            RC6_P0_EXPECTED_IMAGE_ID="sha256:" + "f" * 64)
        with patch.dict(os.environ, future), \
             patch.dict(sys.modules, {"_version": self.version}):
            engine._command_runtime_gate(self.db)
        # A release pin *must* match actual image runtime environment; a
        # forged SHA cannot be accepted solely because it came from a flag.
        with patch.dict(os.environ, {**future, "POROTA_BUILD_SHA": "a" * 40}), \
             patch.dict(sys.modules, {"_version": self.version}):
            with self.assertRaisesRegex(ValueError, "OBSERVER_COMMAND_PAPER_MODE_HOLD"):
                engine._command_runtime_gate(self.db)

    def open_summary(self):
        families = ("ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS",
                    "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI")
        return {"schema": "RC6_PPI_OPEN_TEN_FAMILY_V1", "day": "2026-11-19",
                "paper_ready": False, "order_authority": False,
                "readiness_reason": "FULL_READINESS_NOT_EVIDENCED",
                "unattempted_reason": "BOUNDED",
                "catalog_scope": "PPI_CLAIMED_ONLY", "outside_ppi_scope": 12,
                "statuses": {family: ("FRESH_OBSERVED" if family == "ACCIONES"
                                        else "CATALOG_IDENTITY_UNAVAILABLE")
                             for family in families},
                "unverified": {family: 2 if family == "ACCIONES" else 0
                               for family in families},
                "coverage": {family: {"enumerated_exact": 1 if family == "ACCIONES" else 0,
                                      "attempted": 1 if family == "ACCIONES" else 0,
                                      "native_pair_qa_pass": 1 if family == "ACCIONES" else 0,
                                      "ready": 0, "hold": 3 if family == "ACCIONES" else 0,
                                      "unattempted": 2 if family == "ACCIONES" else 0}
                             for family in families}}

    def insert_open_wire(self, connection, *, family="ACCIONES", endpoint="current",
                         command_id=None, identity5=None, symbol="GGAL",
                         settlement="A-24HS",
                         wire_time="2026-11-19T13:45:00+00:00",
                         market_time="2026-11-19T13:44:59+00:00"):
        if command_id is None:
            command_id = connection.execute(
                "SELECT id FROM observer_commands ORDER BY id DESC LIMIT 1").fetchone()[0]
        if identity5 is None:
            identity5 = f"{symbol}:{family}:BYMA:ARS:{settlement}"
        detail = (f"command_id={command_id};{symbol}:{family}:{settlement};"
                  f"identity5={identity5};endpoint={endpoint};http_status=200;"
                  f"wire_received_at_utc={wire_time};"
                  f"market_timestamp_utc={market_time};"
                  f"wire_sha256={'a' if endpoint == 'current' else 'b'}"
                  + "0" * 63 + ";paper_ready=false")
        connection.execute("INSERT INTO paper_events(event_at,source,event_type,paper_id,detail) "
                           "VALUES (?,?,?,?,?)", (
                               "2026-11-19T13:45:01+00:00", "PRODUCTION_PAPER",
                               "PPI_DIRECT_REST_NATIVE_WIRE_WITNESS", None, detail))

    def test_open_batch_enqueues_one_row_and_never_retries_prior_hold(self):
        first = self.open_enqueue()
        self.assertEqual(first["state"], "QUEUED")
        self.assertEqual(first["db_rows_inserted"], 1)
        self.assertEqual(first["family"], "ALL")
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT command FROM observer_commands").fetchall(),
                             [("PPI_DIRECT_REST_OPEN_ONCE:2026-11-19:ALL",)])
            db.execute("UPDATE observer_commands SET status='HOLD'")
        second = self.open_enqueue()
        self.assertEqual(second["state"], "HOLD")
        self.assertEqual(second["reason"], "PPI_OPEN_ALREADY_REQUESTED_NO_RETRY")
        self.assertEqual(second["db_rows_inserted"], 0)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM observer_commands").fetchone()[0], 1)

    def test_open_batch_attests_ten_family_summary_without_provider_payload(self):
        summary = self.open_summary()
        def scanner_finishes(_delay):
            with sqlite3.connect(self.db) as db:
                self.insert_open_wire(db, endpoint="current")
                self.insert_open_wire(db, endpoint="book")
                db.execute("UPDATE observer_commands SET status='EVIDENCE',result=?",
                           (json.dumps(summary, separators=(",", ":")),))
        result = self.open_enqueue(poll_seconds=2, poll_interval=0.001,
                                   sleep=scanner_finishes)
        self.assertEqual(result["state"], "EVIDENCE")
        self.assertEqual(result["provider_status"], "OPEN_TEN_FAMILY_QA_EVIDENCE")
        self.assertEqual(result["summary"]["coverage"]["ACCIONES"]["native_pair_qa_pass"], 1)
        self.assertEqual(result["summary"]["coverage"]["ACCIONES"]["ready"], 0)
        self.assertEqual(result["summary"]["coverage"]["ACCIONES"]["hold"], 3)
        self.assertEqual(result["summary"]["unverified"]["ACCIONES"], 2)
        self.assertFalse(result["paper_ready"])
        self.assertEqual(len(result["summary"]["statuses"]), 10)

    def test_open_batch_rejects_scanner_evidence_without_independent_native_wire(self):
        summary = self.open_summary()
        def scanner_finishes(_delay):
            with sqlite3.connect(self.db) as db:
                db.execute("UPDATE observer_commands SET status='EVIDENCE',result=?",
                           (json.dumps(summary, separators=(",", ":")),))
        result = self.open_enqueue(poll_seconds=2, poll_interval=0.001,
                                   sleep=scanner_finishes)
        self.assertEqual(result["state"], "HOLD")
        self.assertEqual(result["reason"], "OBSERVER_COMMAND_OPEN_WIRE_UNVERIFIED")
        self.assertEqual(result["command_status"], "EVIDENCE")
        self.assertIsNone(result["summary"])
        self.assertFalse(result["paper_ready"])
        self.assertEqual(result["db_rows_inserted"], 1)
        self.assertTrue(engine.verify_open_receipt(result, "2026-11-19"))

    def test_open_batch_request_candidate_wire_is_not_ppi_field_or_ready(self):
        summary = self.open_summary()
        # RCA #1192: PPI SearchInstrument does not provide authoritative
        # settlement. A REQUEST_CANDIDATE may be probed, never promoted.
        summary["coverage"]["ACCIONES"]["enumerated_exact"] = 0
        summary["unverified"]["ACCIONES"] = 3
        def scanner_finishes(_delay):
            with sqlite3.connect(self.db) as db:
                self.insert_open_wire(db, endpoint="current")
                self.insert_open_wire(db, endpoint="book")
                db.execute("UPDATE observer_commands SET status='EVIDENCE',result=?",
                           (json.dumps(summary, separators=(",", ":")),))
        result = self.open_enqueue(poll_seconds=2, poll_interval=0.001,
                                   sleep=scanner_finishes)
        self.assertEqual(result["state"], "EVIDENCE")
        self.assertEqual(result["summary"]["coverage"]["ACCIONES"]["enumerated_exact"], 0)
        self.assertEqual(result["summary"]["unverified"]["ACCIONES"], 3)
        self.assertEqual(result["summary"]["coverage"]["ACCIONES"]["native_pair_qa_pass"], 1)
        self.assertEqual(result["summary"]["coverage"]["ACCIONES"]["ready"], 0)

    def test_open_batch_preserves_verified_current_only_on_provider_hold(self):
        summary = self.open_summary()
        summary["statuses"]["ACCIONES"] = "PROVIDER_OR_CONTRACT_HOLD"
        summary["coverage"]["ACCIONES"]["native_pair_qa_pass"] = 0
        def scanner_holds(_delay):
            with sqlite3.connect(self.db) as db:
                self.insert_open_wire(db, endpoint="current")
                db.execute("UPDATE observer_commands SET status='HOLD',result=?",
                           (json.dumps(summary, separators=(",", ":")),))
        result = self.open_enqueue(poll_seconds=2, poll_interval=0.001,
                                   sleep=scanner_holds)
        self.assertEqual(result["state"], "HOLD")
        self.assertEqual(result["reason"], "OBSERVER_COMMAND_SCANNER_HOLD")
        self.assertEqual(result["summary"]["coverage"]["ACCIONES"]["attempted"], 1)
        self.assertEqual(result["summary"]["coverage"]["ACCIONES"]["native_pair_qa_pass"], 0)
        self.assertEqual(result["summary"]["coverage"]["ACCIONES"]["ready"], 0)

    def test_open_batch_preexisting_event_cannot_attest_new_queue(self):
        with sqlite3.connect(self.db) as db:
            self.insert_open_wire(db, command_id=1, endpoint="current")
            self.insert_open_wire(db, command_id=1, endpoint="book")
        summary = self.open_summary()
        def scanner_finishes(_delay):
            with sqlite3.connect(self.db) as db:
                db.execute("UPDATE observer_commands SET status='EVIDENCE',result=?",
                           (json.dumps(summary, separators=(",", ":")),))
        result = self.open_enqueue(poll_seconds=2, poll_interval=0.001,
                                   sleep=scanner_finishes)
        self.assertEqual((result["state"], result["reason"]),
                         ("HOLD", "OBSERVER_COMMAND_OPEN_WIRE_UNVERIFIED"))

    def test_open_batch_max_ten_pair_fixture_and_twenty_one_witness_hold(self):
        # Synthetic SQLite fixture verifies the *parser limit*, not provider
        # or full-instrument readiness. No HTTP, broker or PPI actor is used.
        families = self.open_summary()["statuses"]
        for over_limit in (False, True):
            with self.subTest(over_limit=over_limit):
                with sqlite3.connect(self.db) as db:
                    db.execute("DELETE FROM observer_commands")
                    db.execute("DELETE FROM paper_events")
                summary = self.open_summary()
                for family in families:
                    summary["statuses"][family] = "FRESH_OBSERVED"
                    summary["unverified"][family] = 0
                    summary["coverage"][family] = {
                        "enumerated_exact": 1, "attempted": 1,
                        "native_pair_qa_pass": 1, "ready": 0, "hold": 1,
                        "unattempted": 0}
                self.assertLessEqual(len(json.dumps(
                    summary, separators=(",", ":")).encode()), 2000)
                def scanner_finishes(_delay):
                    with sqlite3.connect(self.db) as db:
                        for family in families:
                            for endpoint in ("current", "book"):
                                self.insert_open_wire(db, family=family,
                                    symbol=family[:3] + "1", endpoint=endpoint)
                        if over_limit:
                            self.insert_open_wire(db, family="ACCIONES",
                                symbol="ACC1", endpoint="current")
                        db.execute("UPDATE observer_commands SET status='EVIDENCE',result=?",
                                   (json.dumps(summary, separators=(",", ":")),))
                result = self.open_enqueue(poll_seconds=2, poll_interval=0.001,
                                           sleep=scanner_finishes)
                if over_limit:
                    self.assertEqual((result["state"], result["reason"]),
                                     ("HOLD", "OBSERVER_COMMAND_OPEN_WIRE_UNVERIFIED"))
                    self.assertIsNone(result["summary"])
                else:
                    self.assertEqual(result["state"], "EVIDENCE")
                    self.assertEqual(len(result["summary"]["coverage"]), 10)
                    self.assertTrue(all(
                        result["summary"]["coverage"][f]["native_pair_qa_pass"] == 1
                        for f in families))
                    self.assertTrue(all(
                        result["summary"]["coverage"][f]["ready"] == 0
                        for f in families))

    def test_open_batch_rejects_fake_or_mismatched_wire_custody(self):
        cases = (
            ("missing_book", {"endpoints": ("current",)}),
            ("old_command", {"command_id": 0}),
            ("mismatched_fivekey", {"book_identity5": "GGAL:ACCIONES:BYMA:USD:A-24HS"}),
            ("unknown_wire_clock", {"wire_time": "UNVERIFIED"}),
            ("future_provider_clock", {"market_time": "2026-11-19T14:10:00+00:00"}),
            ("duplicate_current", {"endpoints": ("current", "current", "book")}),
            ("book_before_current", {"endpoints": ("book", "current")}),
        )
        for name, options in cases:
            with self.subTest(name=name):
                with sqlite3.connect(self.db) as db:
                    db.execute("DELETE FROM observer_commands")
                    db.execute("DELETE FROM paper_events")
                summary = self.open_summary()
                def scanner_finishes(_delay):
                    with sqlite3.connect(self.db) as db:
                        for endpoint in options.get("endpoints", ("current", "book")):
                            overrides = {k: v for k, v in options.items()
                                         if k not in {"endpoints", "book_identity5"}}
                            if endpoint == "book" and "book_identity5" in options:
                                overrides["identity5"] = options["book_identity5"]
                            self.insert_open_wire(db, endpoint=endpoint,
                                **overrides)
                        db.execute("UPDATE observer_commands SET status='EVIDENCE',result=?",
                                   (json.dumps(summary, separators=(",", ":")),))
                result = self.open_enqueue(poll_seconds=2, poll_interval=0.001,
                                           sleep=scanner_finishes)
                self.assertEqual((result["state"], result["reason"]),
                                 ("HOLD", "OBSERVER_COMMAND_OPEN_WIRE_UNVERIFIED"))
                self.assertIsNone(result["summary"])

    def test_open_batch_rejects_same_path_different_db_inode(self):
        summary = self.open_summary()
        replacement = self.db.with_suffix(".replacement.db")
        def malicious_path_swap(_delay):
            with sqlite3.connect(self.db) as src, sqlite3.connect(replacement) as dst:
                src.backup(dst)
            with sqlite3.connect(replacement) as attacker:
                rowid = attacker.execute("SELECT id FROM observer_commands").fetchone()[0]
                self.insert_open_wire(attacker, endpoint="current", command_id=rowid)
                self.insert_open_wire(attacker, endpoint="book", command_id=rowid)
                attacker.execute("UPDATE observer_commands SET status='EVIDENCE',result=?",
                                 (json.dumps(summary, separators=(",", ":")),))
            os.replace(replacement, self.db)
        result = self.open_enqueue(poll_seconds=2, poll_interval=0.001,
                                   sleep=malicious_path_swap)
        self.assertEqual((result["state"], result["reason"]),
                         ("HOLD", "OBSERVER_COMMAND_DB_CUSTODY_DRIFT"))
        self.assertIsNone(result["summary"])
        self.assertEqual(result["db_rows_inserted"], 1)
        self.assertFalse(result["paper_ready"])

    def test_open_batch_rejects_malformed_or_overclaiming_summary(self):
        summary = self.open_summary()
        summary["coverage"]["ACCIONES"]["ready"] = 1  # No FULL readiness grant.
        summary["api_key"] = "PRIVATE_DO_NOT_UPLOAD"
        def scanner_finishes(_delay):
            with sqlite3.connect(self.db) as db:
                db.execute("UPDATE observer_commands SET status='EVIDENCE',result=?",
                           (json.dumps(summary),))
        result = self.open_enqueue(poll_seconds=2, poll_interval=0.001,
                                   sleep=scanner_finishes)
        self.assertEqual((result["state"], result["reason"]),
                         ("HOLD", "OBSERVER_COMMAND_OPEN_RESULT_INVALID"))
        self.assertEqual(result["command_status"], "EVIDENCE")
        self.assertNotIn("PRIVATE_DO_NOT_UPLOAD", json.dumps(result))

    def test_open_product_issuer_schema_v1_requires_honest_scope_and_bounded_reason(self):
        native = self.open_summary()
        self.assertEqual(engine._open_summary(
            json.dumps(native, separators=(",", ":")), "2026-11-19"), native)
        self.assertEqual(set(native), {
            "schema", "day", "paper_ready", "order_authority",
            "readiness_reason", "unattempted_reason", "catalog_scope",
            "outside_ppi_scope", "statuses", "coverage", "unverified"})
        legacy = dict(native)
        legacy.pop("outside_ppi_scope")
        with self.assertRaisesRegex(ValueError, "OBSERVER_COMMAND_OPEN_RESULT_INVALID"):
            engine._open_summary(json.dumps(legacy), "2026-11-19")
        legacy = dict(native, catalog_scope="ALL_CATALOG_READY")
        with self.assertRaisesRegex(ValueError, "OBSERVER_COMMAND_OPEN_RESULT_INVALID"):
            engine._open_summary(json.dumps(legacy), "2026-11-19")
        legacy = dict(native, unattempted_reason="BOUNDED_SAMPLE_OR_STOP")
        with self.assertRaisesRegex(ValueError, "OBSERVER_COMMAND_OPEN_RESULT_INVALID"):
            engine._open_summary(json.dumps(legacy), "2026-11-19")

    def test_open_batch_outside_market_or_without_scanner_auth_inserts_nothing(self):
        before = self.open_enqueue(now=datetime(2026, 11, 19, 13, 29,
                                                tzinfo=timezone.utc))
        self.assertEqual(before["state"], "HOLD")
        self.assertEqual(before["reason"], "OBSERVER_COMMAND_SESSION_HOLD")
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE observer_state SET ppi_auth='COOLDOWN'")
        refused = self.open_enqueue()
        self.assertEqual(refused["reason"], "OBSERVER_COMMAND_SCANNER_AUTH_HOLD")
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM observer_commands").fetchone()[0], 0)

    def test_one_queued_row_and_idempotent_second_dispatch(self):
        first = self.enqueue()
        self.assertEqual(first["state"], "QUEUED")
        self.assertEqual(first["command_status"], "QUEUED")
        self.assertEqual(first["db_rows_inserted"], 1)
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT command,status FROM observer_commands").fetchall(),
                [("PPI_DIRECT_REST_POSTCLOSE_ONCE:2026-11-18:BONOS", "QUEUED")])
        second = self.enqueue()
        self.assertEqual(second["state"], "HOLD")
        self.assertEqual(second["reason"], "OBSERVER_COMMAND_ALREADY_REQUESTED")
        self.assertEqual(second["db_rows_inserted"], 0)
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM observer_commands").fetchone()[0], 1)

    def test_poll_reads_scanner_receipt_without_sdk_or_ddl(self):
        def observer_completes(_delay):
            with sqlite3.connect(self.db) as c:
                rowid = c.execute("SELECT id FROM observer_commands").fetchone()[0]
                result = json.dumps({"command_id": rowid, "family": "BONOS",
                    "provider_status": "FRESH_OBSERVED", "reason": None,
                    "paper_ready": False, "order_authority": False})
                c.execute("UPDATE observer_commands SET status='EVIDENCE',result=? WHERE id=?",
                          (result, rowid))
        with patch.object(engine, "_global_ppi_single_session_custody_verified",
                          return_value=True), \
             patch.dict(os.environ, self.environment), \
             patch.dict(sys.modules, {"_version": self.version}):
            receipt = engine.container_enqueue("2026-11-18", "BONOS",
                db=self.db, now=self.now, poll_seconds=2,
                poll_interval=0.001, sleep=observer_completes)
        self.assertEqual(receipt["state"], "EVIDENCE")
        self.assertEqual(receipt["provider_status"], "FRESH_OBSERVED")
        self.assertFalse(receipt["paper_ready"])
        self.assertFalse(receipt["order_authority"])
        with sqlite3.connect(self.db) as c:
            self.assertEqual([row[1] for row in c.execute("PRAGMA table_info(observer_commands)")],
                ["id", "created_at", "command", "status", "started_at", "finished_at", "result"])

    def test_schema_auth_mode_and_stale_day_fail_without_any_row(self):
        cases = (
            ({"POROTA_RUNTIME_SCHEMA_READY": ""}, "OBSERVER_COMMAND_SCHEMA_NOT_READY"),
            ({"REAL_ORDER_CAPABILITY": "ENABLED"}, "OBSERVER_COMMAND_PAPER_MODE_HOLD"),
            ({"POROTA_BUILD_SHA": "f" * 40}, "OBSERVER_COMMAND_PAPER_MODE_HOLD"),
        )
        for changes, reason in cases:
            with self.subTest(changes=changes):
                with patch.dict(self.environment, changes):
                    receipt = self.enqueue()
                self.assertEqual((receipt["state"], receipt["reason"]), ("HOLD", reason))
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE observer_state SET ppi_auth='COOLDOWN' WHERE id=1")
        self.assertEqual(self.enqueue()["reason"], "OBSERVER_COMMAND_SCANNER_AUTH_HOLD")
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE observer_state SET ppi_auth='OK' WHERE id=1")
            c.execute("ALTER TABLE observer_commands RENAME TO old_commands")
        self.assertEqual(self.enqueue()["reason"], "OBSERVER_COMMAND_SCHEMA_INVALID")
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM old_commands").fetchone()[0], 0)
        self.assertEqual(self.enqueue(now=self.now + timedelta(days=1))["state"], "HOLD")


class TestA630RootlessTail(unittest.TestCase):
    """Preserve historical rootless CLI and transport regressions."""

    setUp = TestA630RootlessEvidence.setUp

    def test_open_queue_cli_routes_only_to_existing_scanner_command(self):
        outcome = {"schema": engine.COMMAND_ROOTLESS_SCHEMA, "state": "NO_VERIFICADO",
                   "reason": "ROOTLESS_SOCKET_HOLD"}
        args = ["-c", "rootless-enqueue-open", "2026-11-19", "ALL",
                ROOTLESS_CANDIDATE_SHA, ROOTLESS_CANDIDATE_TREE,
                ROOTLESS_IMAGE_ID, "c" * 64, "1" * 64, "2" * 64,
                "sha256:" + "3" * 64]
        with patch.object(engine, "rootless_host_enqueue", return_value=outcome) as issue, \
             patch.object(engine, "rootless_host_capture") as capture, \
             patch.dict(engine.__dict__, {"__RC6_SOURCE_BYTES__": b"source"}), \
             patch.object(sys, "argv", args), redirect_stdout(io.StringIO()):
            self.assertEqual(engine.main(), 2)
        issue.assert_called_once_with("2026-11-19", "ALL",
            ROOTLESS_CANDIDATE_SHA, ROOTLESS_CANDIDATE_TREE, ROOTLESS_IMAGE_ID,
            "c" * 64, b"source", open_batch=True,
            expected_roles={"observer": "1" * 64, "dashboard": "2" * 64},
            promote_artifact_digest="sha256:" + "3" * 64)
        capture.assert_not_called()

    def test_queue_cli_routes_only_to_rootless_enqueue_not_capture(self):
        outcome = {"schema": engine.COMMAND_ROOTLESS_SCHEMA, "state": "NO_VERIFICADO",
                   "reason": "ROOTLESS_SOCKET_HOLD"}
        args = ["-c", "rootless-enqueue", "2026-11-18", "BONOS",
                ROOTLESS_CANDIDATE_SHA, ROOTLESS_CANDIDATE_TREE,
                ROOTLESS_IMAGE_ID, "c" * 64, "1" * 64, "2" * 64,
                "sha256:" + "3" * 64]
        with patch.object(engine, "rootless_host_enqueue", return_value=outcome) as issue, \
             patch.object(engine, "rootless_host_capture") as capture, \
             patch.dict(engine.__dict__, {"__RC6_SOURCE_BYTES__": b"source"}), \
             patch.object(sys, "argv", args), redirect_stdout(io.StringIO()):
            self.assertEqual(engine.main(), 2)
        issue.assert_called_once_with("2026-11-18", "BONOS",
            ROOTLESS_CANDIDATE_SHA, ROOTLESS_CANDIDATE_TREE, ROOTLESS_IMAGE_ID,
            "c" * 64, b"source",
            expected_roles={"observer": "1" * 64, "dashboard": "2" * 64},
            promote_artifact_digest="sha256:" + "3" * 64)
        capture.assert_not_called()

    def test_unattested_legacy_cli_argv_never_invokes_docker(self):
        outcome = {"schema": engine.COMMAND_ROOTLESS_SCHEMA,
                   "state": "NO_VERIFICADO", "reason": "ROOTLESS_PIN_MISMATCH"}
        args = ["-c", "rootless-enqueue-open", "2026-11-19", "ALL",
                ROOTLESS_CANDIDATE_SHA, ROOTLESS_CANDIDATE_TREE,
                ROOTLESS_IMAGE_ID, "c" * 64]
        with patch.object(engine, "rootless_host_enqueue", return_value=outcome) as issue, \
             patch.object(sys, "argv", args):
            with self.assertRaisesRegex(SystemExit, "A630_EXPLICIT_MODE_REQUIRED"):
                engine.main()
        issue.assert_not_called()

    def test_postclose_cli_only_with_exact_manual_pilot_suffix(self):
        target_b64 = base64.b64encode(json.dumps(list(TARGET)).encode()).decode()
        common = ["-c", "rootless-capture", target_b64, ROOTLESS_CANDIDATE_SHA,
                  ROOTLESS_CANDIDATE_TREE, ROOTLESS_IMAGE_ID, "ONCE", "1120",
                  "2026-11-18"]
        outcome = {"schema": "rc6.a630.rootless.capture.v1", "state": "NO_VERIFICADO",
                   "reason": "PPI_LOCAL_PILOT_ALREADY_USED"}
        with patch.object(engine, "rootless_host_capture", return_value=outcome) as capture, \
             patch.dict(engine.__dict__, {"__RC6_SOURCE_BYTES__": b"source"}):
            with patch.object(sys, "argv", common + ["POSTCLOSE"]), redirect_stdout(io.StringIO()):
                self.assertEqual(engine.main(), 2)
            capture.assert_called_once_with(target_b64, ROOTLESS_CANDIDATE_SHA,
                ROOTLESS_CANDIDATE_TREE, ROOTLESS_IMAGE_ID, b"source", local_pilot=True,
                pilot_slot="1120", pilot_date="2026-11-18", postclose=True)
            capture.reset_mock()
            with patch.object(sys, "argv", common + ["ANYTHING"]):
                self.assertRaises(SystemExit, engine.main)
            capture.assert_not_called()

    def test_in_memory_ssh_bootstrap_never_opens_docker_on_wrong_pin(self):
        # The same Python -c bootstrap as A670; no remote host/SSH/secret/GET.
        bootstrap = ('import sys; src=sys.stdin.buffer.read(1000001); '
            'assert 0<len(src)<=1000000; '
            'scope={"__name__":"__main__","__RC6_SOURCE_BYTES__":src}; '
            'exec(compile(src,"A630_STDIN","exec"),scope)')
        source = Path(engine.__file__).read_bytes()
        target_b64 = base64.b64encode(json.dumps(list(TARGET)).encode()).decode()
        completed = subprocess.run([sys.executable, "-B", "-c", bootstrap,
            "rootless-capture", target_b64, "f" * 40, ROOTLESS_CANDIDATE_TREE,
            ROOTLESS_IMAGE_ID], input=source, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=5)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stderr, b"")
        doc = json.loads(completed.stdout)
        self.assertEqual(doc["reason"], "ROOTLESS_PIN_MISMATCH")
        self.assertFalse(doc["runtime_pin_verified"])
        self.assertIsNone(doc["receipt"]["current_gets"])

    def test_rootless_typed_provider_failure_retains_exact_signed_budget_reason(self):
        inner = capture_two(TARGET, None, Shared(), lambda *a, **k: None)
        target_b64 = base64.b64encode(json.dumps(list(TARGET)).encode()).decode()
        context = {"cid": self.cid, "state_token": "d" * 64,
                   "docker": ("/usr/bin/docker", "--host",
                              "unix:///run/user/1000/porota-rc6-docker.sock"),
                   "environment": {"LC_ALL": "C"}}
        with patch("scripts.rc6_a630_host_ppi_current_book._rootless_context",
                   side_effect=[context, context]):
            result = rootless_host_capture(target_b64, ROOTLESS_CANDIDATE_SHA,
                ROOTLESS_CANDIDATE_TREE, ROOTLESS_IMAGE_ID, b"source",
                run=lambda cmd, **kwargs: subprocess.CompletedProcess(
                    cmd, 2, stdout=json.dumps(inner).encode(), stderr=b""))
        self.assertEqual(result["state"], "NO_VERIFICADO")
        self.assertEqual(result["reason"], "PPI_KEYPAIR_UNAVAILABLE")
        self.assertTrue(result["runtime_pin_verified"])
        self.assertEqual(result["receipt"]["current_gets"], 0)
        self.assertEqual(verified_rootless_wrapper(json.dumps(result).encode(), TARGET), result)


if __name__ == "__main__":
    unittest.main()
