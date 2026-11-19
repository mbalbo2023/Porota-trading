"""A689 OFFHOST: one PPI client/login, one REST wire, N zero-GET consumers.

All SDK/account/HTTP/socket objects are injected fakes. Passing these tests
does NOT attest a real provider entitlement, Watch usage, quota or runtime.
"""

import errno
import json
import os
import socket
import sys
import tempfile
import threading
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_rc6_a680_ppi_guard_wire_receipt import FakeBudget, SDKMarket, FIVE, requests
from bd_ppi_readonly_guard import ProductionMarketReader
from rc6_ppi_shared_feed import ReadOnlyUnixFeedClient, ReadOnlyUnixFeedServer
from rc6_ppi_shared_session_owner import PPISharedSessionOwner, SessionOwnerBlocked


CATALOG = [{"ticker": FIVE[0], "type": FIVE[1], "market": FIVE[2],
            "currency": FIVE[3]}]


class FakeExternalCapacity:
    def __init__(self, allowed=None):
        self.allowed = set(allowed or ())
        self.calls = []

    def permits(self, action, *, five_key=None):
        self.calls.append((action, tuple(five_key) if five_key is not None else None))
        return action in self.allowed


class FakeStreamAuthority:
    def __init__(self, allowed=False):
        self.allowed = allowed
        self.calls = []

    def permits(self, action, *, five_key=None):
        self.calls.append(action)
        return self.allowed is True


class FakeRealtime:
    def __init__(self):
        self.connections = 0
        self.subscription_calls = []
        self.start_calls = 0

    def connect_to_market_data(self, on_connect, on_disconnect, on_market_data):
        self.connections += 1
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_data = on_market_data

    def subscribe_to_element(self, instrument):
        self.subscription_calls.append(instrument)

    def start_connections(self):
        self.start_calls += 1
        self._on_connect()  # synthetic only, no real websocket

    def connect_to_account(self, *args):
        raise AssertionError("PPI_ACCOUNT_STREAM_MUST_NOT_BE_REACHABLE")

    def subscribe_to_account_data(self, *args):
        raise AssertionError("PPI_ACCOUNT_SUBSCRIBE_MUST_NOT_BE_REACHABLE")


@pytest.fixture
def harness(monkeypatch):
    # Synthetic SDK/HTTP/socket fixture only; production login continues
    # through the newer guarded SDK path, not the obsolete Watch auth gate.
    counts = {"clients": 0, "logins": 0, "gets": 0, "connect_attempts": 0}
    native_book = {
        "date": (datetime.now(timezone.utc) - timedelta(milliseconds=100))
        .isoformat().replace("+00:00", "Z"),
        "bids": [{"position": 1, "price": 100, "quantity": 3}],
        "offers": [{"position": 1, "price": 101, "quantity": 2}],
    }
    budget = FakeBudget()
    session = requests.Session()

    class FakePPI:
        def __init__(self, *, sandbox):
            assert sandbox is False
            counts["clients"] += 1
            self.marketdata = SDKMarket(session)
            self.realtime = FakeRealtime()
            self.account = types.SimpleNamespace(login_api=self._login)

        def _login(self, key, secret):
            assert key == "FAKE_PUBLIC" and secret == "FAKE_PRIVATE"
            counts["logins"] += 1

    pkg = types.ModuleType("ppi_client")
    pkg.__path__ = []
    ppi_module = types.ModuleType("ppi_client.ppi")
    ppi_module.PPI = FakePPI
    monkeypatch.setitem(sys.modules, "ppi_client", pkg)
    monkeypatch.setitem(sys.modules, "ppi_client.ppi", ppi_module)

    native_connect = socket.socket.connect

    def no_socket(sock, *args, **kwargs):
        if sock.family == socket.AF_UNIX:
            return native_connect(sock, *args, **kwargs)
        counts["connect_attempts"] += 1
        raise AssertionError("A689_PROVIDER_NETWORK_FORBIDDEN")

    def fake_send(_adapter, request, **kwargs):
        assert request.method == "GET"
        assert budget.in_wire_scope is True
        counts["gets"] += 1
        reply = requests.Response()
        reply.status_code = 200
        reply.url = request.url
        reply._content = json.dumps(native_book, sort_keys=True).encode("utf-8")
        reply.headers["Content-Type"] = "application/json"
        reply.request = request
        return reply

    monkeypatch.setattr(socket.socket, "connect", no_socket)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)

    def factory():
        return ProductionMarketReader(
            "FAKE_PUBLIC", "FAKE_PRIVATE", budget=budget,
            consumer="SCANNER", priority="EXIT_CRITICAL")

    def make_owner(capacity=None, stream_authority=None, catalog_authority=None,
                   socket_path=None):
        return PPISharedSessionOwner(
            reader_factory=factory,
            instrument_factory=lambda ticker, family, settlement:
                (ticker, family, settlement),
            stream_authority=stream_authority or FakeStreamAuthority(),
            external_capacity_authority=capacity or FakeExternalCapacity({
                "OWNER_AUTHENTICATE", "REST_NATIVE_ACQUIRE", "STREAM_SUBSCRIPTION", }),
            catalog_authority=catalog_authority or FakeExternalCapacity({
                "PPI_FIVE_KEY_CATALOG_ATTESTED", }),
            ppi_catalog_rows=CATALOG, catalog_five_keys=[FIVE],
            catalog_source_digest="f" * 64,
            consumer_five_keys={"scanner": [FIVE], "exit_reader": [FIVE]},
            socket_path=socket_path or Path("/nonexistent/a689-no-socket"),
        )

    try:
        yield types.SimpleNamespace(make_owner=make_owner, counts=counts,
                                    native_book=native_book, budget=budget)
    finally:
        session.close()


def test_one_actual_reader_login_and_one_guarded_get_fans_out_to_n_consumers(harness):
    owner = harness.make_owner()
    try:
        assert owner.open_once() is owner
        assert owner.open_once() is owner  # idempotent; no second login
        assert harness.counts["clients"] == harness.counts["logins"] == 1
        # The injected SDK realtime facet has account APIs; public owner and
        # its narrow transport must not expose them.
        assert not hasattr(owner, "realtime") and not hasattr(owner, "client")
        narrow = owner._stream._transport
        assert not hasattr(narrow, "connect_to_account")
        assert not hasattr(narrow, "subscribe_to_account_data")
        response = owner.capture_native("book", FIVE, priority="EXIT_CRITICAL")
        assert response["state"] == "OBSERVE_ONLY"
        assert response["native_receipt"]["paper_ready"] is False
        assert harness.counts["gets"] == 1
        assert [row[0] for row in harness.budget.calls] == [
            "acquire", "start", "finish"]
        for consumer in ("scanner", "exit_reader"):
            reply = owner.dispatch_read_only({"op": "native_read", "consumer": consumer,
                "method": "book", "ppi_five_key": list(FIVE)})
            assert reply["state"] == "OK"
            assert reply["receipt"]["raw_payload"] == harness.native_book
            assert reply["receipt"]["paper_ready"] is False
            assert reply["provider_calls_by_rpc"] == 0
        assert harness.counts == {"clients": 1, "logins": 1, "gets": 1,
                                  "connect_attempts": 0}
        status = owner.status()
        assert status["login_invocations_by_owner"] == 1
        assert status["provider_quota"] == "UNKNOWN"
        assert status["watch_usage"] == "NO_VERIFICADO"
        assert status["paper_ready"] is False
        assert status["real_order_routes_exposed"] is False
    finally:
        owner.close()


def test_exit_native_demand_runs_only_in_existing_owner_reader(harness):
    """A child must request native Book from the owner, never create an SDK."""
    owner = harness.make_owner()
    try:
        owner.open_once()
        response = owner.dispatch_native_acquire(
            consumer="exit_reader", method="book", five_key=FIVE,
            priority="EXIT_CRITICAL")
        assert response["state"] == "OK"
        assert response["receipt"]["raw_payload"] == harness.native_book
        assert response["receipt"]["paper_ready"] is False
        assert response["guard_wire_receipt"]["schema"] == "RC6_PPI_NATIVE_WIRE_RECEIPT_V1"
        assert response["guard_wire_receipt"]["adapter_send_invoked"] is True
        assert response["guard_wire_receipt"]["adapter_response_completed"] is True
        assert response["guard_wire_receipt"]["guard_budget_receipt_sha256"] == (
            response["receipt"]["guard_budget_receipt_sha256"])
        assert harness.counts["clients"] == harness.counts["logins"] == 1
        assert harness.counts["gets"] == 1
        cached = owner.dispatch_read_only({
            "op": "native_read", "consumer": "scanner",
            "method": "book", "ppi_five_key": list(FIVE)})
        assert cached["state"] == "OK"
        assert harness.counts["gets"] == 1
    finally:
        owner.close()


def test_exit_round_control_uses_same_authenticated_owner_budget_not_fake_baseline(harness):
    owner = harness.make_owner()
    try:
        owner.open_once()
        invoked = []

        def report(**kwargs):
            invoked.append(("OBSERVE", kwargs))
            return {"status": "DEGRADED", "lower_suspended": True}

        def discard():
            invoked.append(("DISCARD", None))

        owner._reader.observe_exit_round = report
        owner._reader.discard_exit_round_scope = discard
        response = owner.dispatch_read_only({"op": "exit_round_observe",
            "consumer": "exit_reader", "elapsed_seconds": 2.2,
            "deadline_seconds": 5.0, "failures": 1})
        reset = owner.dispatch_read_only({
            "op": "exit_round_discard", "consumer": "exit_reader"})
        assert response["state"] == "OK"
        assert response["observation"]["status"] == "DEGRADED"
        assert response["provider_calls_by_rpc"] == 0
        assert reset["state"] == "RESET"
        assert invoked == [("OBSERVE", {"elapsed_seconds": 2.2,
            "deadline_seconds": 5.0, "failures": 1}), ("DISCARD", None)]
        assert harness.counts["clients"] == harness.counts["logins"] == 1
        assert harness.counts["gets"] == 0
    finally:
        owner.close()


def test_owner_status_is_responsive_while_one_guarded_get_waits(harness):
    """The parent has a 400ms PID registration status timeout; a 30s GET
    cannot hold the status lock or fake the owner as missing."""
    owner = harness.make_owner()
    owner.open_once()
    entered = threading.Event()
    release = threading.Event()
    status_done = threading.Event()
    replies = []
    native_errors = []

    def slow_guarded_get(*args, **kwargs):
        entered.set()
        if not release.wait(3):
            raise RuntimeError("PPI_FAKE_WIRE_TEST_TIMEOUT")
        return {"state": "OBSERVE_ONLY"}

    owner._rest.capture = slow_guarded_get

    def capture():
        try:
            owner.capture_native("book", FIVE, priority="EXIT_CRITICAL")
        except Exception as exc:
            native_errors.append(exc)

    def status_probe():
        replies.append(owner.status())
        status_done.set()

    wire = threading.Thread(target=capture)
    status = threading.Thread(target=status_probe)
    try:
        wire.start()
        assert entered.wait(1)
        status.start()
        assert status_done.wait(.6), "PPI_GATEWAY_STATUS_BLOCKED_BY_GET"
        assert replies[0]["state"] == "OPEN"
        assert replies[0]["login_invocations_by_owner"] == 1
    finally:
        release.set()
        wire.join(timeout=4)
        status.join(timeout=4)
        owner.close()
    assert not native_errors


def test_native_demand_rejects_role_spoofing_and_unbudgeted_endpoints(harness):
    owner = harness.make_owner()
    try:
        owner.open_once()
        with pytest.raises(SessionOwnerBlocked, match="PPI_NATIVE_DEMAND_ROLE_DENIED"):
            owner.dispatch_native_acquire(
                consumer="scanner", method="book", five_key=FIVE,
                priority="EXIT_CRITICAL")
        with pytest.raises(SessionOwnerBlocked, match="PPI_NATIVE_DEMAND_METHOD_DENIED"):
            owner.dispatch_native_acquire(
                consumer="scanner", method="history", five_key=FIVE,
                priority="DISCOVERY")
        assert harness.counts["gets"] == 0
    finally:
        owner.close()


def test_owner_rejects_unsplit_caucion_alias_before_sdk_multi_get(harness):
    capacity = FakeExternalCapacity({
        "OWNER_AUTHENTICATE", "REST_AUXILIARY_ACQUIRE"})
    owner = harness.make_owner(capacity=capacity)
    try:
        owner.open_once()
        called = []
        owner._reader.search_instruments = lambda *a, **kw: called.append(a) or []
        with pytest.raises(SessionOwnerBlocked,
                           match="PPI_AUX_CAUCION_BULK_DENIED"):
            owner.dispatch_auxiliary_acquire(
                consumer="scanner", method="search_instruments",
                args={"ticker": "CAUCION", "instrument_type": "CAUCIONES",
                      "name": "CAUCION", "market": "BYMA"})
        assert not called
    finally:
        owner.close()


def test_native_acquire_round_trip_over_private_unix_uses_one_owner_login(harness):
    """Native demand crosses AF_UNIX; neither EXIT nor scanner logs in."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "ppi-gateway.sock"
        owner = harness.make_owner(socket_path=path)
        worker = None
        try:
            owner.open_once()
            # OFFHOST test fixture acts as both process supervisor and fake
            # client; real runtime binds to its separate parent PID.
            owner._rpc._supervisor_pid = os.getpid()
            owner._rpc._supervisor_starttime = (
                ReadOnlyUnixFeedServer.process_starttime(os.getpid()))
            try:
                owner.start_gateway()
            except OSError as exc:
                if exc.errno in {errno.EPERM, errno.EAFNOSUPPORT, errno.ENOSYS}:
                    pytest.skip("SANDBOX_AF_UNIX_UNAVAILABLE: dispatch covered offhost")
                raise
            worker = threading.Thread(target=owner.serve_gateway_forever, daemon=True)
            worker.start()
            client = ReadOnlyUnixFeedClient(socket_path=path, timeout_seconds=2)
            identity = client.owner_status()
            birth = ReadOnlyUnixFeedServer.process_starttime(os.getpid())
            for role in ("exit_reader", "scanner"):
                registered = client.register_client(
                    role, pid=os.getpid(), proc_starttime=birth)
                assert registered["state"] == "REGISTERED"
            assert identity["state"] == "OPEN"
            assert identity["owner_pid"] == owner.status()["owner_pid"]
            assert identity["session_generation"] == owner.status()["session_generation"]
            assert identity["login_invocations_by_owner"] == 1
            response = client.acquire_native(
                "exit_reader", method="book", five_key=FIVE,
                priority="EXIT_CRITICAL")
            assert response["state"] == "OK"
            assert response["receipt"]["raw_payload"] == harness.native_book
            assert response["owner_pid"] == identity["owner_pid"]
            assert response["session_generation"] == identity["session_generation"]
            assert response["provider_calls_by_rpc"] == 1
            cached = client.read_native("scanner", method="book", five_key=FIVE)
            assert cached["state"] == "OK"
            assert cached["provider_calls_by_rpc"] == 0
            denied = client.acquire_native(
                "scanner", method="book", five_key=FIVE,
                priority="EXIT_CRITICAL")
            assert denied["state"] == "PPI_NATIVE_DEMAND_ROLE_DENIED"
            assert denied["provider_calls_by_rpc"] == "NO_VERIFICADO"
            assert harness.counts == {"clients": 1, "logins": 1, "gets": 1,
                                      "connect_attempts": 0}
        finally:
            if worker is not None:
                owner.shutdown_gateway()
                worker.join(timeout=2)
            owner.close_gateway()
            owner.close()


def test_owner_rpc_native_acquire_with_real_budgeted_reader_no_socket(harness):
    owner = harness.make_owner()
    try:
        owner.open_once()
        status = owner.dispatch_read_only({"op": "owner_status"})
        assert status["state"] == "OPEN"
        assert status["session_generation"] == owner.status()["session_generation"]
        response = owner.dispatch_read_only({
            "op": "native_acquire", "consumer": "exit_reader", "method": "book",
            "ppi_five_key": list(FIVE), "priority": "EXIT_CRITICAL"})
        assert response["state"] == "OK"
        assert response["provider_calls_by_rpc"] == 1
        assert response["receipt"]["raw_payload"] == harness.native_book
        denied = owner.dispatch_read_only({
            "op": "native_acquire", "consumer": "scanner", "method": "book",
            "ppi_five_key": list(FIVE), "priority": "EXIT_CRITICAL"})
        assert denied["state"] == "PPI_NATIVE_DEMAND_ROLE_DENIED"
        assert denied["provider_calls_by_rpc"] == "NO_VERIFICADO"
        assert harness.counts == {"clients": 1, "logins": 1, "gets": 1,
                                  "connect_attempts": 0}
    finally:
        owner.close()


def test_single_owner_serves_bounded_scanner_catalog_history_configuration(harness):
    owner = harness.make_owner(capacity=FakeExternalCapacity({
        "OWNER_AUTHENTICATE", "REST_NATIVE_ACQUIRE", "REST_AUXILIARY_ACQUIRE"}))
    try:
        owner.open_once()
        calls = []

        def search(ticker, family, name=None, market="BYMA"):
            calls.append(("search", ticker, family, name, market))
            return [{"ticker": ticker, "type": family, "market": market,
                     "currency": "ARS", "settlement": FIVE[4]}]

        def history(ticker, family, settlement, first, last):
            calls.append(("history", ticker, family, settlement, first, last))
            return [{"date": "2026-11-18", "close": 100}]

        def configuration():
            calls.append(("configuration",))
            return {"instrument_types": ["ACCIONES"]}

        owner._reader.search_instruments = search
        owner._reader.history = history
        owner._reader.market_configuration = configuration
        search_result = owner.dispatch_auxiliary_acquire(consumer="scanner",
            method="search_instruments", args={"ticker": FIVE[0],
            "instrument_type": FIVE[1], "name": FIVE[0], "market": FIVE[2]})
        rpc_configuration = owner.dispatch_read_only({"op": "aux_acquire",
            "consumer": "scanner", "method": "market_configuration", "args": {}})
        assert rpc_configuration["state"] == "OK"
        history_result = owner.dispatch_auxiliary_acquire(consumer="scanner",
            method="history", args={"ticker": FIVE[0], "instrument_type": FIVE[1],
            "settlement": FIVE[4], "date_from": "2026-11-18",
            "date_to": "2026-11-19"})
        configuration_result = owner.dispatch_auxiliary_acquire(consumer="scanner",
            method="market_configuration", args={})
        assert all(value["state"] == "OK" for value in
                   (search_result, history_result, configuration_result))
        assert history_result["result"][0]["close"] == 100
        assert search_result["result"][0]["ticker"] == FIVE[0]
        assert configuration_result["result"]["instrument_types"] == ["ACCIONES"]
        assert len(calls) == 4
        assert harness.counts["logins"] == harness.counts["clients"] == 1
        assert harness.counts["gets"] == 0  # these fixture methods are fakes
        with pytest.raises(SessionOwnerBlocked, match="AUX_ROLE_DENIED"):
            owner.dispatch_auxiliary_acquire(consumer="exit_reader",
                method="history", args={"ticker": "GGAL"})
        with pytest.raises(SessionOwnerBlocked, match="AUX_METHOD_DENIED"):
            owner.dispatch_auxiliary_acquire(consumer="scanner",
                method="account_balance", args={})
        assert len(calls) == 4
    finally:
        owner.close()


def test_aux_observed_429_retry_after_blocks_next_get_without_claiming_quota(harness):
    owner = harness.make_owner(capacity=FakeExternalCapacity({
        "OWNER_AUTHENTICATE", "REST_AUXILIARY_ACQUIRE"}))
    try:
        owner.open_once()
        attempts = []

        class RateLimit(Exception):
            response = types.SimpleNamespace(
                status_code=429, headers={"Retry-After": "120"})

        def provider():
            attempts.append("GET")
            raise RateLimit("body_is_untrusted_and_must_not_leave_owner")

        owner._reader.market_configuration = provider
        with pytest.raises(SessionOwnerBlocked, match="PPI_AUX_HTTP_429"):
            owner.dispatch_auxiliary_acquire(
                consumer="scanner", method="market_configuration", args={})
        with pytest.raises(SessionOwnerBlocked, match="PPI_AUX_PROVIDER_BACKOFF_ACTIVE"):
            owner.dispatch_auxiliary_acquire(
                consumer="scanner", method="market_configuration", args={})
        assert attempts == ["GET"]
        assert owner.status()["provider_quota"] == "UNKNOWN"
        assert owner.status()["aux_retry_after_seconds_observed"] == 120
    finally:
        owner.close()


def test_no_external_capacity_or_stream_entitlement_means_no_provider_io(harness):
    owner = harness.make_owner(capacity=FakeExternalCapacity())
    with pytest.raises(SessionOwnerBlocked, match="PPI_PAPER_CAPACITY_OR_ENTITLEMENT_UNVERIFIED"):
        owner.open_once()
    assert harness.counts == {"clients": 0, "logins": 0, "gets": 0,
                              "connect_attempts": 0}


def test_external_stream_gate_absence_blocks_connect_without_rest_fallback(harness):
    cap = FakeExternalCapacity({"OWNER_AUTHENTICATE", "REST_NATIVE_ACQUIRE",
                                "STREAM_SUBSCRIPTION"})
    owner = harness.make_owner(capacity=cap, stream_authority=FakeStreamAuthority(True))
    try:
        owner.open_once()
        assert owner.plan_focus(FIVE, priority="HOT") == "ADMITTED"
        cap.allowed.remove("STREAM_SUBSCRIPTION")
        with pytest.raises(SessionOwnerBlocked, match="PPI_STREAM_EXTERNAL_AUTHORITY_NOT_VERIFIED"):
            owner.run_market_stream()
        with pytest.raises(SessionOwnerBlocked, match="PPI_PAPER_CAPACITY_OR_ENTITLEMENT_UNVERIFIED"):
            owner.plan_focus(FIVE, priority="HOT")
        assert harness.counts["logins"] == 1
        assert harness.counts["gets"] == 0
        assert owner.status()["stream_invoked"] is False
    finally:
        owner.close()


def test_second_owner_cannot_install_nested_process_global_guard(harness):
    one = harness.make_owner()
    two = harness.make_owner()
    try:
        one.open_once()
        with pytest.raises(SessionOwnerBlocked, match="PPI_PROCESS_ALREADY_HAS_MARKET_OWNER"):
            two.open_once()
        assert harness.counts["clients"] == harness.counts["logins"] == 1
    finally:
        one.close()
    # A previously denied owner may start only once the guard is restored.
    try:
        two.open_once()
        assert harness.counts["clients"] == harness.counts["logins"] == 2
    finally:
        two.close()


def test_hot_rebind_while_stream_is_started_is_rejected(harness):
    owner = harness.make_owner()
    try:
        owner.open_once()
        assert owner.plan_focus(FIVE, priority="HOT") == "ADMITTED"
        # Model the socket-lifecycle bit without opening an SDK socket.
        owner._stream_invoked = True
        with pytest.raises(SessionOwnerBlocked, match="PPI_STREAM_FOCUS_RECONNECT_NOT_AUTHORIZED"):
            owner.plan_focus(FIVE, priority="EXIT_CRITICAL")
        with pytest.raises(SessionOwnerBlocked, match="PPI_STREAM_SAFE_STOP_NOT_VERIFIED"):
            owner.close()
    finally:
        owner._stream_invoked = False  # OFFHOST synthetic teardown only
        owner.close()


def test_missing_capacity_adapter_rejected_before_reader_factory(harness):
    with pytest.raises(SessionOwnerBlocked, match="PPI_EXTERNAL_CAPACITY_AUTHORITY_MISSING"):
        PPISharedSessionOwner(
            reader_factory=lambda: (_ for _ in ()).throw(AssertionError("NO_READER")),
            instrument_factory=lambda *args: args,
            stream_authority=FakeStreamAuthority(),
            external_capacity_authority=None,
            catalog_authority=FakeExternalCapacity({
                "PPI_FIVE_KEY_CATALOG_ATTESTED"}),
            ppi_catalog_rows=CATALOG,
            catalog_five_keys=[FIVE], catalog_source_digest="f" * 64,
            consumer_five_keys={"scanner": [FIVE]},
            socket_path=Path("/nonexistent/no-socket"),
        )
    assert harness.counts["clients"] == harness.counts["logins"] == 0


def test_catalog_attestation_missing_denies_before_authentication(harness):
    owner = harness.make_owner(catalog_authority=FakeExternalCapacity())
    with pytest.raises(SessionOwnerBlocked,
                       match="PPI_CATALOG_INDEPENDENT_AUTHORITY_UNVERIFIED"):
        owner.open_once()
    assert harness.counts == {"clients": 0, "logins": 0, "gets": 0,
                              "connect_attempts": 0}


def test_no_authenticated_sdk_means_no_raw_realtime_facet(harness):
    reader = ProductionMarketReader(
        "FAKE_PUBLIC", "FAKE_PRIVATE", budget=harness.budget,
        consumer="SCANNER")
    try:
        with pytest.raises(RuntimeError, match="no esta autenticado"):
            reader.market_only_sdk_transport(instrument_factory=lambda *args: args)
        assert harness.counts["clients"] == harness.counts["logins"] == 0
    finally:
        reader.close()


def test_stream_capacity_false_raising_or_nonbool_never_connects(harness):
    class HostileCapacity(FakeExternalCapacity):
        def __init__(self, response):
            super().__init__({"OWNER_AUTHENTICATE", "STREAM_SUBSCRIPTION"})
            self.response = response

        def permits(self, action, *, five_key=None):
            if action == "STREAM_CONNECT":
                if isinstance(self.response, BaseException):
                    raise self.response
                return self.response
            return super().permits(action, five_key=five_key)

    for response in (False, "true", ValueError("NO_QUOTA")):
        owner = harness.make_owner(capacity=HostileCapacity(response),
                                   stream_authority=FakeStreamAuthority(True))
        try:
            owner.open_once()
            owner.plan_focus(FIVE, priority="EXIT_CRITICAL")
            with pytest.raises(SessionOwnerBlocked,
                               match="PPI_STREAM_EXTERNAL_AUTHORITY_NOT_VERIFIED"):
                owner.run_market_stream()
            assert owner.status()["stream_invoked"] is False
            assert harness.counts["gets"] == harness.counts["connect_attempts"] == 0
        finally:
            owner.close()


def test_revoked_per_instrument_quota_or_catalog_before_connect_denies_socket(harness):
    for revoked in ("quota", "catalog"):
        cap = FakeExternalCapacity({"OWNER_AUTHENTICATE", "STREAM_CONNECT",
                                    "STREAM_SUBSCRIPTION"})
        catalog = FakeExternalCapacity({"PPI_FIVE_KEY_CATALOG_ATTESTED"})
        owner = harness.make_owner(capacity=cap, catalog_authority=catalog,
                                   stream_authority=FakeStreamAuthority(True))
        try:
            owner.open_once()
            assert owner.plan_focus(FIVE, priority="EXIT_CRITICAL") == "ADMITTED"
            if revoked == "quota":
                cap.allowed.remove("STREAM_SUBSCRIPTION")
            else:
                catalog.allowed.remove("PPI_FIVE_KEY_CATALOG_ATTESTED")
            with pytest.raises(SessionOwnerBlocked,
                               match="PPI_STREAM_(EXTERNAL_AUTHORITY_NOT_VERIFIED|SUBSCRIPTION_CAPACITY_REVOKED)"):
                owner.run_market_stream()
            assert owner.status()["stream_invoked"] is False
            assert harness.counts["gets"] == harness.counts["connect_attempts"] == 0
        finally:
            owner.close()


def test_delayed_sdk_connect_callback_revalidates_per_symbol_capacity(harness):
    """TOCTOU: revoke AFTER connect() binding but BEFORE on_connect callback."""
    cap = FakeExternalCapacity({"OWNER_AUTHENTICATE", "STREAM_CONNECT",
                                "STREAM_SUBSCRIPTION"})
    catalog = FakeExternalCapacity({"PPI_FIVE_KEY_CATALOG_ATTESTED"})
    owner = harness.make_owner(capacity=cap, catalog_authority=catalog,
                               stream_authority=FakeStreamAuthority(True))
    try:
        owner.open_once()
        assert owner.plan_focus(FIVE, priority="EXIT_CRITICAL") == "ADMITTED"
        raw = owner._reader._ProductionMarketReader__client.realtime
        # Fake SDK start only binds callbacks; no socket or live PPI is started.
        raw.start_connections = lambda: None
        owner.run_market_stream()
        assert raw.connections == 1
        cap.allowed.remove("STREAM_SUBSCRIPTION")  # during fake handshake
        raw._on_connect()  # SDK's deferred callback
        assert raw.subscription_calls == []
        assert owner._stream.status()["wire_subscriptions_count"] == 0
        assert owner._stream.status()["subscription_failures"] == 1
        assert owner._stream.status()["state"] == "DEGRADED"
        assert harness.counts["clients"] == harness.counts["logins"] == 1
        assert harness.counts["gets"] == harness.counts["connect_attempts"] == 0
    finally:
        owner._stream_invoked = False  # synthetic no-socket teardown ONLY
        owner._reader._ProductionMarketReader__stream_invoked = False
        owner.close()


def test_revocation_after_wire_subscription_denies_event_fanout(harness):
    """An earlier wire subscription is NOT perpetual permission to publish."""
    cap = FakeExternalCapacity({"OWNER_AUTHENTICATE", "STREAM_CONNECT",
                                "STREAM_SUBSCRIPTION"})
    owner = harness.make_owner(capacity=cap,
                               stream_authority=FakeStreamAuthority(True))
    try:
        owner.open_once()
        assert owner.plan_focus(FIVE, priority="EXIT_CRITICAL") == "ADMITTED"
        realtime = owner._reader._ProductionMarketReader__client.realtime
        owner.run_market_stream()  # in-memory fake, no socket
        assert len(realtime.subscription_calls) == 1
        cap.allowed.remove("STREAM_SUBSCRIPTION")
        realtime._on_data(json.dumps({
            "Ticker": FIVE[0], "Type": FIVE[1], "Settlement": FIVE[4],
            "Date": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "Trade": True, "Price": 100, "VolumeAmount": 1,
        }))
        assert owner._stream.status()["rejected"] == 1
        cached = owner.dispatch_read_only({"op": "pull", "consumer": "scanner",
                                           "after_sequence": 0, "limit": 10})
        assert cached["state"] == "OK" and cached["events"] == []
        assert harness.counts["gets"] == harness.counts["connect_attempts"] == 0
    finally:
        owner._stream_invoked = False  # fake-only no actual WS can remain open
        owner._reader._ProductionMarketReader__stream_invoked = False
        owner.close()
