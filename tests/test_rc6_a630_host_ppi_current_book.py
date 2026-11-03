"""A630 adversarial no-network tests: exact full-key and two true leases."""

from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import base64
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from scripts.rc6_a630_host_ppi_current_book import (
    SLOTS, TwoGetAtomicBudget, _existing_shared_budget, _exit_round_fresh,
    _strict_adapter_send,
    _http_body_hashes,
    _utc, capture_two,
    decode_target, host_preflight, target_for_slot, unverified_transport_receipt,
    verify_receipt,
)

NOW = datetime(2026, 11, 4, 14, 20, 1, tzinfo=timezone.utc)
PROVIDER = "2026-11-04T14:20:00+00:00"
TARGET = SLOTS["1120"]


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
                 clock=PROVIDER, extra_get=False, bad_shape=False):
        self.budget = budget
        self.consumer = consumer
        self.priority = priority
        self.audit = audit
        self.clock = clock
        self.extra_get = extra_get
        self.bad_shape = bad_shape
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
        return {"date": self.clock, "bids": [{"price": 748, "quantity": 7}],
                "offers": [{"price": 750, "quantity": 5}]}

    def close(self):
        return None


@contextmanager
def fake_hashes():
    # Simulates the real HTTP body hook; never opens a socket.
    yield [["/api/1.0/marketdata/current", sha256(b"current"), 7],
           ["/api/1.0/marketdata/book", sha256(b"book"), 4]]


@contextmanager
def fake_adapter():
    yield ["/api/1.0/marketdata/current", "/api/1.0/marketdata/book"]


def invoke(shared, **reader_options):
    constructed = []

    def factory(*args, **kwargs):
        obj = Reader(*args, **kwargs, **reader_options)
        constructed.append(obj)
        return obj

    result = capture_two(TARGET, ("fake-key", "fake-secret"), shared, factory,
                         clock=lambda: NOW, http_limiter=fake_hashes,
                         adapter_guard=fake_adapter)
    return result, constructed


class TestA630(unittest.TestCase):
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
                            'recorded_at':datetime.now(timezone.utc).isoformat()}}}
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

    def test_exit_round_must_be_recent_tz_aware_and_not_future(self):
        cycle={'status':'COMPLETE','required_scope_verified_current':True,
               'recorded_at':(NOW-timedelta(seconds=299)).isoformat()}
        self.assertTrue(_exit_round_fresh(cycle,now=NOW))
        cycle['recorded_at']=(NOW-timedelta(seconds=301)).isoformat()
        self.assertFalse(_exit_round_fresh(cycle,now=NOW))
        cycle['recorded_at']=(NOW+timedelta(seconds=1)).isoformat()
        self.assertFalse(_exit_round_fresh(cycle,now=NOW))
        cycle['recorded_at']='2026-11-04T14:20:00'
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
                budget.start(grant['lease'])
                adapter.send(Request('GET',domain+'MarketData/Current'+query), verify=True)
                with self.assertRaises(ValueError):
                    adapter.send(Request('GET',domain+'MarketData/Current'+query), verify=True)
                with self.assertRaises(ValueError):
                    adapter.send(Request('GET',domain+'MarketData/Book'+query), verify=True)
                budget.finish(grant['lease'])
                grant=budget.acquire('book',consumer='UNSCOPED',priority='DISCOVERY')
                budget.start(grant['lease'])
                adapter.send(Request('GET',domain+'MarketData/Book'+query), verify=True)
                with self.assertRaises(ValueError):
                    adapter.send(Request('GET',domain+'MarketData/Book'+query), verify=True)
                self.assertEqual(sends, ['/api/1.0/marketdata/current',
                                         '/api/1.0/marketdata/book'])
        self.assertEqual(len(wire_calls), 3)  # one auth POST, exactly 2 GET

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

    def test_workflow_fallback_uses_unknown_transport_count_helper(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    '.github/workflows/rc6-a630-ppi-current-book-20261104.yml')
        source = workflow.read_text(encoding='utf-8')
        self.assertIn('result=unverified_transport_receipt()', source)
        self.assertIn('doc={**unverified_transport_receipt(reason)', source)
        self.assertNotIn('"current_gets":0,"book_gets":0', source)


if __name__ == "__main__":
    unittest.main()
