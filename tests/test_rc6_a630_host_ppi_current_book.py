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
    SLOTS, PER_GET_CLOCK_FIELDS, TwoGetAtomicBudget, _existing_shared_budget, _exit_round_fresh,
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

    def test_workflow_fallback_uses_unknown_transport_count_helper(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    '.github/workflows/rc6-a630-ppi-current-book-20261104.yml')
        source = workflow.read_text(encoding='utf-8')
        self.assertIn('result=unverified_transport_receipt()', source)
        self.assertIn('doc={**unverified_transport_receipt(reason)', source)
        self.assertNotIn('"current_gets":0,"book_gets":0', source)


if __name__ == "__main__":
    unittest.main()
