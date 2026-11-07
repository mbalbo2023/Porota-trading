"""A689 OFFHOST: one PPI client/login, one REST wire, N zero-GET consumers.

All SDK/account/HTTP/socket objects are injected fakes. Passing these tests
does NOT attest a real provider entitlement, Watch usage, quota or runtime.
"""

import json
import socket
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_rc6_a680_ppi_guard_wire_receipt import FakeBudget, SDKMarket, FIVE, requests
from bd_ppi_readonly_guard import ProductionMarketReader
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

    def no_socket(*args, **kwargs):
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

    def make_owner(capacity=None, stream_authority=None, catalog_authority=None):
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
            socket_path=Path("/nonexistent/a689-no-socket"),
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
        assert status["provider_quota"] == status["watch_usage"] == "NO_VERIFICADO"
        assert status["paper_ready"] is False
        assert status["real_order_routes_exposed"] is False
    finally:
        owner.close()


def test_no_external_capacity_or_stream_entitlement_means_no_provider_io(harness):
    owner = harness.make_owner(capacity=FakeExternalCapacity())
    with pytest.raises(SessionOwnerBlocked, match="PPI_ACCOUNT_WATCH_CAPACITY_UNVERIFIED"):
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
        with pytest.raises(SessionOwnerBlocked, match="PPI_ACCOUNT_WATCH_CAPACITY_UNVERIFIED"):
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
