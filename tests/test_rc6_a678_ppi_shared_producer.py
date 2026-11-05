"""OFFHOST regressions for the market-only PPI SDK producer (A678 G2)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from rc6_ppi_shared_feed import EventRejected, PPISharedMarketFeed
from rc6_ppi_shared_producer import (
    MarketOnlySDKTransport, PPISharedSDKProducer, ProducerBlocked,
)


FIVE_A = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-48HS")
FIVE_B = ("AL30", "BONOS", "BYMA", "ARS", "INMEDIATA")
FIVE_C = ("AAPL", "CEDEARS", "BYMA", "ARS", "A-48HS")


class FakeFeed:
    def __init__(self, limit=2):
        self.limit = limit
        self._planned = {}
        self.events = []

    def plan_subscription(self, key, *, priority):
        order = {"EXIT_CRITICAL": 0, "WATCH_CRITICAL": 1, "OPENED_CRITICAL": 1,
                 "HOT": 2, "WARM": 3, "DISCOVERY": 4}
        key = tuple(key)
        if key in self._planned:
            if order[priority] < order[self._planned[key]]:
                self._planned[key] = priority
            return "ADMITTED"
        if len(self._planned) >= self.limit:
            worst = max(self._planned, key=lambda k: (order[self._planned[k]], k))
            if order[priority] >= order[self._planned[worst]]:
                return "CAPACITY_HOLD"
            del self._planned[worst]
        self._planned[key] = priority
        return "ADMITTED"

    def active_subscriptions(self):
        order = {"EXIT_CRITICAL": 0, "WATCH_CRITICAL": 1, "OPENED_CRITICAL": 1,
                 "HOT": 2, "WARM": 3, "DISCOVERY": 4}
        return [list(k) for k in sorted(self._planned, key=lambda k:
                                        (order[self._planned[k]], k))]

    def publish_native_market_event(self, raw, **kwargs):
        assert "orders" not in raw
        self.events.append((raw, kwargs))
        return {"event_sha256": "f" * 64, "paper_ready": False}


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.on_connect = None
        self.on_disconnect = None
        self.on_market_data = None
        self.account_calls = 0
        self.order_calls = 0

    def connect_market_data(self, on_connect, on_disconnect, on_market_data):
        self.calls.append("bind_market")
        self.on_connect, self.on_disconnect = on_connect, on_disconnect
        self.on_market_data = on_market_data

    def start_market_data(self):
        self.calls.append("start_market")
        self.on_connect()

    def subscribe_market_data(self, ticker, instrument_type, settlement):
        self.calls.append((ticker, instrument_type, settlement))

    def create_order(self, *args, **kwargs):
        self.order_calls += 1
        raise AssertionError("never access real-order routes")

    def connect_to_account(self, *args, **kwargs):
        self.account_calls += 1
        raise AssertionError("never access account routes")


class FakeAuthority:
    def __init__(self, authorized=True):
        self.authorized = authorized
        self.calls = []

    def permits(self, action, *, five_key=None):
        self.calls.append((action, five_key))
        return self.authorized


def _driver(authorized=True):
    feed, transport, authority = FakeFeed(), FakeTransport(), FakeAuthority(authorized)
    producer = PPISharedSDKProducer(feed=feed, transport=transport,
        stream_authority=authority, max_message_bytes=2048,
        received_clock=lambda: datetime(2026, 11, 5, 13, 0, 2, tzinfo=timezone.utc),
        monotonic_ns=lambda: 123456789)
    return producer, feed, transport, authority


def test_producer_construction_is_inert_and_does_not_create_a_client():
    driver, feed, transport, authority = _driver()
    assert transport.calls == []
    assert authority.calls == []
    assert feed.events == []
    assert driver.status()["state"] == "NOT_STARTED"


def test_unsigned_stream_authority_fails_before_connect_and_subscription():
    driver, _, transport, authority = _driver(authorized=False)
    assert driver.plan(FIVE_A, priority="EXIT_CRITICAL") == "ADMITTED"
    with pytest.raises(ProducerBlocked, match="PPI_STREAM_AUTHORITY_NOT_VERIFIED"):
        driver.run_market_only()
    assert transport.calls == []
    assert authority.calls == [("CONNECT_MARKET_DATA", None)]


def test_subscriptions_prioritize_exit_and_do_not_access_account_or_order_routes():
    driver, _, transport, _ = _driver()
    assert driver.plan(FIVE_A, priority="DISCOVERY") == "ADMITTED"
    assert driver.plan(FIVE_B, priority="EXIT_CRITICAL") == "ADMITTED"
    assert driver.plan(FIVE_C, priority="WARM") == "ADMITTED"
    driver.run_market_only()
    assert transport.calls == ["bind_market", "start_market",
                               ("AL30", "BONOS", "INMEDIATA"),
                               ("AAPL", "CEDEARS", "A-48HS")]
    assert transport.account_calls == transport.order_calls == 0
    assert driver.status()["state"] == "CONNECTED"


def test_native_book_is_forwarded_with_local_session_generation_not_paper_ready():
    driver, feed, transport, _ = _driver()
    driver.plan(FIVE_A, priority="EXIT_CRITICAL")
    driver.run_market_only()
    transport.on_market_data(json.dumps({"Ticker": "GGAL", "Type": "ACCIONES",
        "Settlement": "A-48HS", "Date": "2026-11-05T13:00:00Z",
        "Trade": False, "Bids": [{"Price": 100, "Quantity": 10}],
        "Offers": [{"Price": 101, "Quantity": 20}]}))
    assert len(feed.events) == 1
    raw, kwargs = feed.events[0]
    assert raw["Trade"] is False
    assert kwargs["session_fingerprint"] and len(kwargs["session_fingerprint"]) == 64
    assert kwargs["received_monotonic_ns"] == 123456789
    assert driver.status()["published"] == 1


def test_reconnect_rotates_session_and_rechecks_authority():
    driver, feed, transport, authority = _driver()
    driver.plan(FIVE_A, priority="EXIT_CRITICAL")
    driver.run_market_only()
    native = {"Ticker": "GGAL", "Type": "ACCIONES", "Settlement": "A-48HS",
              "Date": "2026-11-05T13:00:00Z", "Trade": False,
              "Bids": [], "Offers": []}
    transport.on_market_data(native)
    first = feed.events[-1][1]["session_fingerprint"]
    transport.on_disconnect()
    assert driver.status()["state"] == "DISCONNECTED"
    transport.on_connect()
    transport.on_market_data({**native, "Date": "2026-11-05T13:00:01Z"})
    second = feed.events[-1][1]["session_fingerprint"]
    assert first != second
    assert authority.calls.count(("CONNECT_MARKET_DATA", None)) == 3


def test_revocation_and_bad_payloads_never_publish_or_trigger_rest_fallback():
    driver, feed, transport, authority = _driver()
    driver.plan(FIVE_A, priority="EXIT_CRITICAL")
    driver.run_market_only()
    authority.authorized = False
    transport.on_market_data({"Trade": False, "Ticker": "GGAL"})
    assert feed.events == []
    assert driver.status()["state"] == "BLOCKED"
    authority.authorized = True
    transport.on_connect()
    transport.on_market_data("X" * 10000)
    transport.on_market_data({"Trade": True, "Ticker": "GGAL", "orders": [123]})
    assert feed.events == []
    assert driver.status()["rejected"] == 3
    assert not hasattr(driver, "get_orders")
    assert not hasattr(driver, "create_order")


def test_new_subscription_during_connection_is_fail_closed_until_reconnect():
    driver, _, transport, _ = _driver()
    driver.plan(FIVE_A, priority="EXIT_CRITICAL")
    driver.run_market_only()
    with pytest.raises(ProducerBlocked, match="PPI_STREAM_FOCUS_RECONNECT_REQUIRED"):
        driver.plan(FIVE_B, priority="HOT")
    assert transport.calls.count(("AL30", "BONOS", "INMEDIATA")) == 0


def test_degraded_lower_subscription_does_not_suppress_exit_book():
    driver, feed, transport, authority = _driver()
    original_subscribe = transport.subscribe_market_data
    def selective_subscribe(t, ty, settlement):
        if t == "GGAL":
            raise RuntimeError("unavailable instrument")
        original_subscribe(t, ty, settlement)
    transport.subscribe_market_data = selective_subscribe
    driver.plan(FIVE_A, priority="DISCOVERY")
    driver.plan(FIVE_B, priority="EXIT_CRITICAL")
    driver.run_market_only()
    assert driver.status()["state"] == "DEGRADED"
    assert driver.status()["wire_subscriptions_count"] == 1
    transport.on_market_data({"Ticker": "AL30", "Type": "BONOS", "Settlement": "INMEDIATA",
        "Date": "2026-11-05T13:00:00Z", "Trade": False, "Bids": [], "Offers": []})
    assert len(feed.events) == 1
    transport.on_market_data({"Ticker": "GGAL", "Type": "ACCIONES", "Settlement": "A-48HS",
        "Date": "2026-11-05T13:00:00Z", "Trade": False, "Bids": [], "Offers": []})
    assert len(feed.events) == 1
    assert driver.status()["rejected"] == 1


def test_transport_invokes_only_market_realtime_surface_with_injected_instrument():
    class SpyRealtime:
        def __init__(self):
            self.calls = []
        def connect_to_market_data(self, *args):
            self.calls.append("connect_market")
        def start_connections(self):
            self.calls.append("run_market")
        def subscribe_to_element(self, instrument):
            self.calls.append(("subscribe", instrument))
        def connect_to_account(self, *args):
            raise AssertionError("must never call account")
    realtime = SpyRealtime()
    transport = MarketOnlySDKTransport(realtime=realtime,
        instrument_factory=lambda t, ty, settlement: (t, ty, settlement))
    transport.connect_market_data(lambda: None, lambda: None, lambda raw: None)
    transport.subscribe_market_data("GGAL", "ACCIONES", "A-48HS")
    transport.start_market_data()
    assert realtime.calls == ["connect_market",
                              ("subscribe", ("GGAL", "ACCIONES", "A-48HS")),
                              "run_market"]
    assert not hasattr(transport, "orders")
    assert not hasattr(transport, "create_order")


def test_audit_report_is_bounded_and_contains_no_sdk_credential_or_raw_data():
    driver, _, transport, _ = _driver()
    driver.plan(FIVE_A, priority="EXIT_CRITICAL")
    driver.run_market_only()
    for _ in range(100):
        transport.on_market_data("not json SECRET ABCD")
    report = driver.status()
    assert report["rejected"] == 100
    assert "SECRET" not in repr(report)
    assert "ABCD" not in repr(report)
    assert len(repr(report)) < 600


def test_internal_feed_exception_fails_closed_without_leaking_event_or_crashing_sdk():
    driver, feed, transport, _ = _driver()
    driver.plan(FIVE_A, priority="EXIT_CRITICAL")
    driver.run_market_only()
    def broken_feed(*args, **kwargs):
        raise RuntimeError("unexpected internal recorder failure SECRET")
    feed.publish_native_market_event = broken_feed
    transport.on_market_data({"Ticker": "GGAL", "Type": "ACCIONES", "Settlement": "A-48HS",
        "Date": "2026-11-05T13:00:00Z", "Trade": False, "Bids": [], "Offers": []})
    assert driver.status()["rejected"] == 1
    assert "SECRET" not in repr(driver.status())


def test_g1_integration_book1_book2_session_and_missing_native_type_fail_closed():
    """A mock provider does NOT certify native/PPI signature or PAPER readiness."""
    from datetime import timedelta
    t0 = datetime(2026, 11, 5, 13, 0, 0, tzinfo=timezone.utc)
    moment = {"now": t0 + timedelta(milliseconds=50), "tick": 1000}
    bus = PPISharedMarketFeed(
        ppi_catalog_rows=[{"ticker": "GGAL", "type": "ACCIONES", "market": "BYMA", "currency": "ARS"}],
        catalog_source_digest="a" * 64, max_events=8, max_subscriptions=3,
        max_age_seconds=5)
    bus.register_consumer("exit_reader", watched_five_keys=[FIVE_A])
    transport, auth = FakeTransport(), FakeAuthority(True)
    producer = PPISharedSDKProducer(feed=bus, transport=transport, stream_authority=auth,
        received_clock=lambda: moment["now"], monotonic_ns=lambda: moment["tick"])
    assert producer.plan(FIVE_A, priority="EXIT_CRITICAL") == "ADMITTED"
    producer.run_market_only()
    event1 = {"Ticker": "GGAL", "Settlement": "A-48HS", "Trade": False,
              "Date": t0.isoformat(), "Bids": [{"Price": 100, "Quantity": 12}],
              "Offers": [{"Price": 101, "Quantity": 4}]}
    transport.on_market_data(event1)
    book1 = bus.pull("exit_reader", after_sequence=0)["events"][0]
    assert book1["type_provenance"] == "UNIQUE_ACTIVE_SUBSCRIPTION_NOT_NATIVE"
    assert book1["session_fingerprint"] and book1["ppi_catalog_authority"] == "NO_VERIFICADO"
    assert book1["paper_ready"] is False
    moment["now"] = t0 + timedelta(milliseconds=500)
    moment["tick"] = 1500
    transport.on_market_data({**event1, "Date": (t0 + timedelta(milliseconds=400)).isoformat(),
                              "Bids": [{"Price": 100.2, "Quantity": 12}]})
    book2 = bus.get_post_commit_book(FIVE_A, book1_receipt_sha256=book1["event_sha256"],
        commit_at=t0 + timedelta(milliseconds=100), commit_monotonic_ns=1200)
    assert book2["kind"] == "BOOK"
    assert book2["session_fingerprint"] == book1["session_fingerprint"]
    assert book2["paper_ready"] is False
    moment["now"] = t0 + timedelta(milliseconds=800)
    moment["tick"] = 1900
    transport.on_market_data({**event1, "Date": "2026-11-05T13:00:00.700"})
    assert producer.status()["rejected"] == 1
    assert bus.stats()["published"] == 2


def test_g1_integration_does_not_claim_book2_across_reconnect():
    from datetime import timedelta
    t0 = datetime(2026, 11, 5, 13, 0, tzinfo=timezone.utc)
    moment = {"now": t0, "tick": 1000}
    bus = PPISharedMarketFeed(ppi_catalog_rows=[
        {"ticker": "GGAL", "type": "ACCIONES", "market": "BYMA", "currency": "ARS"}],
        catalog_source_digest="b" * 64, max_events=8, max_subscriptions=1,
        max_age_seconds=5)
    bus.register_consumer("scanner", watched_five_keys=[FIVE_A])
    transport = FakeTransport()
    producer = PPISharedSDKProducer(feed=bus, transport=transport,
        stream_authority=FakeAuthority(True),
        received_clock=lambda: moment["now"], monotonic_ns=lambda: moment["tick"])
    producer.plan(FIVE_A, priority="EXIT_CRITICAL")
    producer.run_market_only()
    raw = {"Ticker": "GGAL", "Type": "ACCIONES", "Settlement": "A-48HS", "Trade": False,
           "Date": t0.isoformat(), "Bids": [], "Offers": []}
    transport.on_market_data(raw)
    first = bus.pull("scanner", after_sequence=0)["events"][0]
    transport.on_disconnect()
    moment["now"] = t0 + timedelta(milliseconds=400)
    moment["tick"] = 1800
    transport.on_connect()
    transport.on_market_data({**raw, "Date": (t0 + timedelta(milliseconds=300)).isoformat()})
    with pytest.raises(EventRejected, match="BOOK2_SESSION_CHANGED"):
        bus.get_post_commit_book(FIVE_A, book1_receipt_sha256=first["event_sha256"],
            commit_at=t0 + timedelta(milliseconds=100), commit_monotonic_ns=1500)
