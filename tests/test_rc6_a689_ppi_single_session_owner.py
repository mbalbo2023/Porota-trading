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
    wire = types.SimpleNamespace(status=200, body=None)
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
        reply.status_code = wire.status
        reply.url = request.url
        reply._content = (wire.body if wire.body is not None else
                          json.dumps(native_book, sort_keys=True).encode("utf-8"))
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
                                    native_book=native_book, budget=budget, wire=wire)
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

        def configuration(field=None):
            calls.append(("configuration", field))
            return {"instrument_types": ["ACCIONES"]} if field is None else ["ACCIONES"]

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
        single_enum = owner.dispatch_read_only({"op": "aux_acquire",
            "consumer": "scanner", "method": "market_configuration",
            "args": {"field": "instrument_types"}})
        assert single_enum["state"] == "OK"
        assert single_enum["result"] == ["ACCIONES"]
        assert calls[-1] == ("configuration", "instrument_types")
        with pytest.raises(SessionOwnerBlocked, match="AUX_ARGUMENTS_INVALID"):
            owner.dispatch_auxiliary_acquire(consumer="scanner",
                method="market_configuration", args={"field": "account"})
        assert all(value["state"] == "OK" for value in
                   (search_result, history_result, configuration_result))
        assert history_result["result"][0]["close"] == 100
        assert search_result["result"][0]["ticker"] == FIVE[0]
        assert configuration_result["result"]["instrument_types"] == ["ACCIONES"]
        assert len(calls) == 5
        assert harness.counts["logins"] == harness.counts["clients"] == 1
        assert harness.counts["gets"] == 0  # these fixture methods are fakes
        with pytest.raises(SessionOwnerBlocked, match="AUX_ROLE_DENIED"):
            owner.dispatch_auxiliary_acquire(consumer="exit_reader",
                method="history", args={"ticker": "GGAL"})
        with pytest.raises(SessionOwnerBlocked, match="AUX_METHOD_DENIED"):
            owner.dispatch_auxiliary_acquire(consumer="scanner",
                method="account_balance", args={})
        assert len(calls) == 5
    finally:
        owner.close()


def test_single_configuration_field_uses_the_same_guarded_http_transport(harness):
    owner = harness.make_owner(capacity=FakeExternalCapacity({
        "OWNER_AUTHENTICATE", "REST_AUXILIARY_ACQUIRE"}))
    try:
        owner.open_once()
        harness.wire.body = b'["ETFS"]'
        client = owner._reader._ProductionMarketReader__client
        client.configuration = types.SimpleNamespace(
            get_instrument_types=lambda: requests.get(
                "https://clientapi.portfoliopersonal.com/api/1.0/Configuration/InstrumentTypes"
            ).json())
        result = owner.dispatch_read_only({"op": "aux_acquire", "consumer": "scanner",
            "method": "market_configuration", "args": {"field": "instrument_types"}})
        assert result["state"] == "OK"
        assert result["result"] == ["ETFS"]
        assert harness.counts == {"clients": 1, "logins": 1, "gets": 1, "connect_attempts": 0}
        assert ("acquire", "configuration/instrumenttypes", "SCANNER", "EXIT_CRITICAL") in (
            harness.budget.calls)
    finally:
        owner.close()


def test_aux_observed_429_retry_after_blocks_next_get_without_claiming_quota(harness):
    owner = harness.make_owner(capacity=FakeExternalCapacity({
        "OWNER_AUTHENTICATE", "REST_AUXILIARY_ACQUIRE", "REST_NATIVE_ACQUIRE"}))
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
        with pytest.raises(SessionOwnerBlocked, match="PPI_NATIVE_PROVIDER_BACKOFF_ACTIVE"):
            owner.capture_native("book", FIVE, priority="EXIT_CRITICAL")
        assert attempts == ["GET"]
        assert owner.status()["provider_quota"] == "UNKNOWN"
        assert owner.status()["aux_retry_after_seconds_observed"] == 120
        assert owner.status()["session_recovery_pending"] is False
        assert not owner.session_recovery_due()
    finally:
        owner.close()


@pytest.mark.parametrize(("http_status", "body", "reason"), [
    (404, "Instrument not found", "PPI_INSTRUMENT_NOT_FOUND"),
    (404, "UNTRUSTED_RESPONSE_BODY", "PPI_NATIVE_HTTP_404"),
    (503, "UNTRUSTED_RESPONSE_BODY", "PPI_NATIVE_HTTP_503"),
])
def test_owner_reports_guarded_native_http_failure_when_sdk_raises_only_the_body(
        harness, http_status, body, reason):
    owner = harness.make_owner()
    try:
        owner.open_once()
        harness.wire.status = http_status
        harness.wire.body = json.dumps(body).encode()
        sdk = owner._reader._ProductionMarketReader__client.marketdata
        original_current = sdk.current

        def sdk_current(*args):
            raise Exception(original_current(*args))

        sdk.current = sdk_current
        with pytest.raises(SessionOwnerBlocked, match=f"^{reason}$"):
            owner.capture_native("current", FIVE, priority="DISCOVERY")
        assert harness.counts["gets"] == 1
        assert owner.status()["session_recovery_pending"] is False
        assert owner.status()["native_backoff_remaining_seconds"] == 0
    finally:
        owner.close()


@pytest.mark.parametrize("http_status", [401, 403, 429])
def test_owner_uses_guarded_status_when_sdk_retry_hides_http_error(
        harness, monkeypatch, http_status):
    import rc6_ppi_shared_session_owner as owner_module

    now = [1000.0]
    monkeypatch.setattr(owner_module, "time", types.SimpleNamespace(
        monotonic=lambda: now[0]))
    owner = harness.make_owner(capacity=FakeExternalCapacity({
        "OWNER_AUTHENTICATE", "REST_NATIVE_ACQUIRE", "REST_AUXILIARY_ACQUIRE"}))
    try:
        owner.open_once()
        generation = owner.status()["session_generation"]
        harness.wire.status = http_status
        # The retry hits the guard before another wire send and does not
        # carry an HTTP response on the exception raised by the SDK call.
        owner._reader._ProductionMarketReader__client.marketdata.two_reads = True
        with pytest.raises(SessionOwnerBlocked, match=f"PPI_NATIVE_HTTP_{http_status}"):
            owner.capture_native("book", FIVE, priority="EXIT_CRITICAL")
        assert harness.counts["gets"] == 1
        assert owner.status()["native_last_http_status"] == http_status
        assert owner.status()["session_recovery_pending"] == (http_status == 401)
        if http_status == 401:
            blocked = "PPI_GATEWAY_SESSION_RECOVERY_PENDING"
            with pytest.raises(SessionOwnerBlocked, match=blocked):
                owner.dispatch_auxiliary_acquire(
                    consumer="scanner", method="market_configuration", args={})
        else:
            blocked = "PPI_NATIVE_PROVIDER_BACKOFF_ACTIVE"
        with pytest.raises(SessionOwnerBlocked, match=blocked):
            owner.capture_native("book", FIVE, priority="EXIT_CRITICAL")
        assert harness.counts["gets"] == 1
        assert owner.status()["session_generation"] == generation
        now[0] += owner_module.AUTH_COOLDOWN_SECONDS - 1
        assert not owner.session_recovery_due()
        now[0] += 1
        assert owner.session_recovery_due() == (http_status == 401)
        assert harness.counts["logins"] == 1
    finally:
        owner.close()


def test_native_retry_after_blocks_aux_until_observed_deadline(harness, monkeypatch):
    import rc6_ppi_shared_session_owner as owner_module

    now = [1000.0]
    monkeypatch.setattr(owner_module, "time", types.SimpleNamespace(
        monotonic=lambda: now[0]))
    owner = harness.make_owner(capacity=FakeExternalCapacity({
        "OWNER_AUTHENTICATE", "REST_NATIVE_ACQUIRE", "REST_AUXILIARY_ACQUIRE"}))
    try:
        owner.open_once()
        limited = requests.Response()
        limited.status_code = 429
        limited.headers["Retry-After"] = "1200"
        unauthorized = requests.Response()
        unauthorized.status_code = 401
        responses = iter((limited, unauthorized))

        def sdk_failure(*_args, **_kwargs):
            raise Exception(next(responses))

        monkeypatch.setattr(owner._rest, "capture", sdk_failure)
        monkeypatch.setattr(owner._reader, "market_configuration", sdk_failure)
        with pytest.raises(SessionOwnerBlocked, match="PPI_NATIVE_HTTP_429"):
            owner.capture_native("book", FIVE, priority="EXIT_CRITICAL")
        with pytest.raises(SessionOwnerBlocked, match="PPI_AUX_PROVIDER_BACKOFF_ACTIVE"):
            owner.dispatch_auxiliary_acquire(
                consumer="scanner", method="market_configuration", args={})
        now[0] += 1199
        with pytest.raises(SessionOwnerBlocked, match="PPI_AUX_PROVIDER_BACKOFF_ACTIVE"):
            owner.dispatch_auxiliary_acquire(
                consumer="scanner", method="market_configuration", args={})
        now[0] += 1
        with pytest.raises(SessionOwnerBlocked, match="PPI_AUX_HTTP_401"):
            owner.dispatch_auxiliary_acquire(
                consumer="scanner", method="market_configuration", args={})
        now[0] += owner_module.AUTH_COOLDOWN_SECONDS
        assert owner.session_recovery_due()
        assert harness.counts["logins"] == 1
    finally:
        owner.close()


def test_aux_propagates_only_known_off_wire_budget_causes(harness, monkeypatch):
    from rc6_ppi_global_budget import BudgetBackpressure

    owner = harness.make_owner(capacity=FakeExternalCapacity({
        "OWNER_AUTHENTICATE", "REST_AUXILIARY_ACQUIRE"}))
    try:
        owner.open_once()
        for reason, expected in (
            ("PPI_DIRECT_PROVIDER_429_BREAKER", "PPI_DIRECT_PROVIDER_429_BREAKER"),
            ("PRIVATE_UPSTREAM_DETAIL", "PPI_AUX_PROVIDER_FAILURE_UNVERIFIED"),
        ):
            def denied():
                raise BudgetBackpressure(reason)

            monkeypatch.setattr(owner._reader, "market_configuration", denied)
            with pytest.raises(SessionOwnerBlocked, match=expected):
                owner.dispatch_auxiliary_acquire(
                    consumer="scanner", method="market_configuration", args={})
        assert owner.status()["aux_last_http_status"] is None
        assert harness.counts["gets"] == 0
    finally:
        owner.close()


def test_single_owner_refresh_clears_401_and_preserves_live_generation(harness, monkeypatch):
    import rc6_ppi_shared_session_owner as owner_module

    now = [1000.0]
    monkeypatch.setattr(owner_module, "time", types.SimpleNamespace(monotonic=lambda: now[0]))
    owner = harness.make_owner()
    try:
        owner.open_once()
        generation = owner.status()["session_generation"]
        count = []
        monkeypatch.setattr(owner._reader, "refresh_market_session_once",
                            lambda: count.append(1) or (True, 200, None))
        owner._provider_hold(429, 120)
        owner._native_hold_until = owner._provider_hold(401, None)
        owner._native_last_http_status = 401
        assert owner.refresh_invalid_session() is None
        now[0] += 120
        assert owner.refresh_invalid_session() == "PPI_OWNER_REFRESH_SUCCESS"
        assert not owner.status()["session_recovery_pending"]
        assert owner._native_hold_until == 0
        assert owner.status()["session_generation"] == generation
        assert owner.refresh_invalid_session() is None
        assert len(count) == harness.counts["logins"] == 1
    finally:
        owner.close()


@pytest.mark.parametrize(("http_status", "body"), [
    (401, b""), (401, b"SYNTHETIC_PRIVATE_AUTH_BODY"),
    (403, b"Unauthorized"), (429, b"Unauthorized"),
])
def test_aux_sdk_130_text_exception_uses_the_guarded_http_status(
        harness, monkeypatch, http_status, body):
    owner = harness.make_owner(capacity=FakeExternalCapacity({
        "OWNER_AUTHENTICATE", "REST_AUXILIARY_ACQUIRE"}))
    try:
        owner.open_once()
        market = owner._reader._ProductionMarketReader__client.marketdata

        def sdk_130_search(ticker, name, market_code, family):
            response = market.session.get(
                "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/SearchInstrument",
                params={"Ticker": ticker, "Name": name, "Market": market_code, "Type": family})
            # Pinned PPIClient.get can raise just a message without attaching
            # the Response. The empty 401 uses the special Unauthorized term.
            if response.status_code == 401 and not response.content:
                raise Exception("Unauthorized")
            raise Exception(response.content.decode())

        monkeypatch.setattr(market, "search_instrument", sdk_130_search, raising=False)
        harness.wire.status, harness.wire.body = http_status, body
        with pytest.raises(SessionOwnerBlocked, match=f"^PPI_AUX_HTTP_{http_status}$"):
            owner.dispatch_auxiliary_acquire(
                consumer="scanner", method="search_instruments",
                args={"ticker": FIVE[0], "name": FIVE[0],
                      "instrument_type": FIVE[1], "market": FIVE[2]})
        assert harness.counts["gets"] == 1
        assert owner.status()["session_recovery_pending"] == (http_status == 401)
    finally:
        owner.close()


def test_aux_does_not_reuse_stale_401_or_an_unauthorized_without_wire(harness, monkeypatch):
    owner = harness.make_owner(capacity=FakeExternalCapacity({
        "OWNER_AUTHENTICATE", "REST_AUXILIARY_ACQUIRE"}))
    args = {"ticker": FIVE[0], "name": FIVE[0],
            "instrument_type": FIVE[1], "market": FIVE[2]}
    try:
        owner.open_once()
        market = owner._reader._ProductionMarketReader__client.marketdata
        send = True

        def sdk_search(*_args):
            if send:
                market.session.get(
                    "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/SearchInstrument")
            raise Exception("Unauthorized")

        monkeypatch.setattr(market, "search_instrument", sdk_search, raising=False)
        # Seed the legacy thread-local state via the guarded fake SDK without
        # sending this first failure through the owner's classifier.
        harness.wire.status = 401
        with pytest.raises(Exception, match="Unauthorized"):
            owner._reader.search_instruments(**args)
        assert owner._reader.last_read_error_code == "PPI_HTTP_401"
        harness.wire.status = 200
        for send in (False, True):
            with pytest.raises(SessionOwnerBlocked, match="PPI_AUX_PROVIDER_FAILURE_UNVERIFIED"):
                owner.dispatch_auxiliary_acquire(
                    consumer="scanner", method="search_instruments", args=args)
            assert owner.status()["session_recovery_pending"] is False
        assert harness.counts["gets"] == 2
    finally:
        owner.close()


def test_runtime_refreshes_completed_catalog_under_one_guarded_login(
        harness, tmp_path, monkeypatch):
    import bv_paper_runtime as runtime
    import rc6_scanner_login_interlock as login_lock
    from be_paper_engine import PaperStore
    from bu_instrument_catalog import init_schema
    from rc6_ppi_shared_feed import SharedFeedUnavailable

    store = PaperStore(str(tmp_path / "paper.sqlite"))
    init_schema(store)
    new_key = ("GGAL", "ACCIONES", "BYMA", "ARS", "INMEDIATA")
    owner = harness.make_owner(capacity=FakeExternalCapacity({
        "OWNER_AUTHENTICATE", "REST_NATIVE_ACQUIRE", "REST_AUXILIARY_ACQUIRE"}))
    starts, reservations = [], []
    searches = []
    pid = os.getpid()
    birth = ReadOnlyUnixFeedServer.process_starttime(pid)

    def persist(connection, key):
        ticker, family, market, currency, _ = key
        connection.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (*key, "REQUEST_CANDIDATE", "OFFHOST", "2026-11-22T13:00:00Z",
             "RUN_TEST", "STALE", "NOT_READY", json.dumps({
                 "_discovery_source": "PPI_PRIMARY", "ticker": ticker,
                 "type": family, "market": market, "currency": currency})))

    with store.connect() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS candidate_refresh_state(
            id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL, checked_at TEXT NOT NULL)""")
        connection.execute("INSERT INTO candidate_refresh_state VALUES(1,'COMPLETE','generation-1')")
        persist(connection, FIVE)

    class Interlock:
        def __init__(self, database, cooldown):
            assert database == store.path and cooldown == 900

        def reserve(self):
            reservations.append("ALLOWED")
            return "ALLOWED"

    class SocketThread:
        live = True

        def is_alive(self):
            return self.live

        def join(self, timeout):
            assert timeout == 5
            self.live = False

    # Lifetime and socket binding are covered by the existing Linux tests.
    # This fixture uses the real client codec and the real role/router logic,
    # injecting only the unavailable AF_UNIX transport in this environment.
    def start_gateway(instance):
        assert instance is owner
        rpc = instance._rpc
        rpc._supervisor_pid = pid
        rpc._supervisor_starttime = birth
        rpc._ever_listened = True
        rpc.start_native_priority_router()
        instance._reader.search_instruments = (
            lambda ticker, *_args, **_kwargs: searches.append(ticker) or [])
        starts.append((instance._reader, instance.status()["session_generation"], rpc))
        return SocketThread()

    def attach():
        client = ReadOnlyUnixFeedClient(socket_path=tmp_path / "not-bound.sock")
        status = client.owner_status()
        client.register_client("exit_reader", pid=pid, proc_starttime=birth)
        client.register_client("scanner", pid=pid, proc_starttime=birth)
        return client, status

    def acquire(client, key):
        return client.acquire_native("exit_reader", method="book", five_key=key,
                                     priority="EXIT_CRITICAL")

    def search(client):
        return client.acquire_auxiliary("scanner", method="search_instruments",
            args={"ticker": new_key[0], "name": new_key[0],
                  "market": new_key[2], "instrument_type": new_key[1]})

    def local_exchange(_client, packet, *, require_owner_identity):
        assert require_owner_identity is True
        return owner._rpc.dispatch(packet, peer_pid=pid)

    class Stop:
        tick = 0
        clients = []

        def is_set(self):
            return self.tick == 4

        def wait(self, seconds):
            assert seconds == 5
            self.tick += 1
            if self.tick == 1:
                old, status = attach()
                self.clients.append((old, status))
                assert acquire(old, FIVE)["state"] == "OK"
                with store.connect() as connection:
                    connection.execute("""UPDATE candidate_refresh_state
                        SET state='RUNNING', checked_at='generation-2'""")
                    persist(connection, new_key)
            elif self.tick == 2:
                old, status = self.clients[0]
                assert old.owner_status()["session_generation"] == status["session_generation"]
                assert acquire(old, new_key)["state"] == "PPI_NATIVE_DEMAND_IDENTITY_DENIED"
                with store.connect() as connection:
                    connection.execute("UPDATE candidate_refresh_state SET state='COMPLETE'")
            elif self.tick == 3:
                fresh, status = attach()
                old, old_status = self.clients[0]
                assert status["session_generation"] != old_status["session_generation"]
                assert status["catalog_source_digest"] != old_status["catalog_source_digest"]
                assert status["login_invocations_by_owner"] == 1
                assert starts[0][0] is starts[1][0]
                assert not starts[0][2].native_router_status()["running"]
                with pytest.raises(SharedFeedUnavailable, match="PPI_GATEWAY_OWNER_CHANGED"):
                    acquire(old, new_key)
                with pytest.raises(SharedFeedUnavailable, match="PPI_GATEWAY_OWNER_CHANGED"):
                    search(old)
                assert searches == []
                assert search(fresh)["state"] == "OK"
                assert searches == [new_key[0]]
                assert harness.counts["gets"] == 1  # stale pin never reaches the wire
                stale_rpc = starts[0][2].dispatch({
                    "op": "native_acquire", "consumer": "exit_reader",
                    "method": "book", "ppi_five_key": list(FIVE),
                    "priority": "EXIT_CRITICAL",
                    "session_generation": status["session_generation"]}, peer_pid=pid)
                assert stale_rpc["state"] == "PPI_NATIVE_ROUTER_NOT_RUNNING"
                assert harness.counts["gets"] == 1  # no direct path from retired listener
                assert acquire(fresh, new_key)["state"] == "OK"
                assert owner._catalog_authority.permits(
                    "PPI_FIVE_KEY_CATALOG_ATTESTED", five_key=new_key) is False
                with store.connect() as connection:
                    connection.execute("""UPDATE candidate_refresh_state
                        SET checked_at='generation-3'""")
            return self.is_set()

    monkeypatch.setattr(login_lock, "ScannerLoginInterlock", Interlock)
    monkeypatch.setattr(runtime, "_make_ppi_gateway_owner", lambda _store: owner)
    monkeypatch.setattr(runtime, "_start_ppi_gateway_server", start_gateway)
    monkeypatch.setattr(ReadOnlyUnixFeedClient, "_request", local_exchange)
    runtime._run_ppi_gateway_owned_loop(store, Stop())
    assert owner.status()["state"] == "CLOSED"
    assert len(starts) == 2  # same digest did not start another listener
    assert reservations == ["ALLOWED"]
    assert harness.counts == {"clients": 1, "logins": 1, "gets": 2,
                              "connect_attempts": 0}


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
