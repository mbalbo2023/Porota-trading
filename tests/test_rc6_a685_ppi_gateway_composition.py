"""A685 OFFHOST composition: one guarded REST wire, two RPC readers, no PAPER.

This is an integration contract for DRAFT #976/#982/#984/#985, not a live
broker, provider-quota, catalog-signature or deployment certification.  The
existing A680 test supplies only synthetic SDK/HTTP and budget primitives.
"""

from datetime import datetime, timedelta, timezone
import json
import socket
from types import SimpleNamespace

import pytest

from test_rc6_a680_ppi_guard_wire_receipt import (
    FakeBudget, SDKMarket, FIVE, requests,
)
from bd_ppi_readonly_guard import ProductionMarketReader, ReadOnlyPolicyViolation
from rc6_ppi_shared_feed import PPIReadOnlyNativeReceiptStore, PPISharedMarketFeed
from rc6_ppi_shared_rest_producer import MarketOnlyRESTProducer, RESTProducerBlocked


@pytest.fixture
def composed(monkeypatch):
    """All potential HTTP calls terminate in the fake adapter, never the wire."""
    native_book = {
        "date": (datetime.now(timezone.utc) - timedelta(milliseconds=100))
        .isoformat().replace("+00:00", "Z"),
        "bids": [{"position": 1, "price": 100, "quantity": 3}],
        "offers": [{"position": 1, "price": 101, "quantity": 2}],
    }
    budget = FakeBudget()
    status = {"code": 200}
    sends = []

    def no_socket(*args, **kwargs):
        raise AssertionError("A685_PROVIDER_NETWORK_FORBIDDEN")

    monkeypatch.setattr(socket.socket, "connect", no_socket)

    def fake_send(_adapter, request, **kwargs):
        assert budget.in_wire_scope is True
        sends.append({"method": request.method, "url": request.url})
        response = requests.Response()
        response.status_code = status["code"]
        response.url = request.url
        response._content = json.dumps(native_book, sort_keys=True).encode("utf-8")
        response.headers["Content-Type"] = "application/json"
        response.request = request
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)
    reader = ProductionMarketReader("OFFHOST_FAKE_KEY", "OFFHOST_FAKE_SECRET",
                                    budget=budget, consumer="SCANNER")
    session = requests.Session()
    reader._ProductionMarketReader__client = SimpleNamespace(
        marketdata=SDKMarket(session))
    reader._ProductionMarketReader__authenticated = True

    feed = PPISharedMarketFeed(ppi_catalog_rows=[{
        "ticker": FIVE[0], "type": FIVE[1], "market": FIVE[2],
        "currency": FIVE[3],
    }], catalog_source_digest="f" * 64)
    for consumer in ("scanner", "exit_reader"):
        feed.register_consumer(consumer, watched_five_keys=[FIVE])
    sink = PPIReadOnlyNativeReceiptStore(feed, max_receipts=8,
                                         max_age_seconds=120)
    producer = MarketOnlyRESTProducer(
        guarded_source=reader, receipt_sink=sink,
        catalog_five_keys=[FIVE], catalog_source_digest="f" * 64,
        session_fingerprint="b" * 64)
    try:
        yield SimpleNamespace(native=native_book, budget=budget, status=status,
                              sends=sends, sink=sink, producer=producer,
                              reader=reader, session=session)
    finally:
        session.close()
        reader.close()


def test_one_guarded_sdk_rest_read_fans_out_to_two_native_rpc_consumers(composed):
    result = composed.producer.capture("book", FIVE, priority="EXIT_CRITICAL")
    assert result["state"] == "OBSERVE_ONLY"
    assert result["extra_provider_calls_outside_guard"] == 0
    assert result["real_orders_sent_by_producer"] == 0
    assert result["native_receipt"]["paper_ready"] is False
    assert result["native_receipt"]["raw_payload"] == composed.native
    assert result["native_receipt"]["guard_budget_receipt_authority"] == "NO_VERIFICADO"
    assert result["native_receipt"]["ppi_catalog_authority"] == "NO_VERIFICADO"

    for consumer in ("scanner", "exit_reader"):
        reply = composed.sink.dispatch({
            "op": "native_read", "consumer": consumer, "method": "book",
            "ppi_five_key": list(FIVE),
        })
        assert reply["state"] == "OK"
        assert reply["provider_calls_by_rpc"] == 0
        assert reply["receipt"]["raw_payload"] == composed.native
        assert reply["receipt"]["receipt_sha256"] == result["native_receipt"]["receipt_sha256"]
        assert reply["receipt"]["paper_ready"] is False

    assert len(composed.sends) == 1
    assert composed.sends[0]["method"] == "GET"
    assert [row[0] for row in composed.budget.calls] == [
        "acquire", "start", "finish",
    ]
    assert composed.reader.metrics["login_calls"] == 0
    assert composed.sink.stats()["retained_raw_receipts"] == 1


def test_budget_denial_never_calls_http_or_publishes_native_receipt(composed, monkeypatch):
    def deny(endpoint, *, consumer, priority):
        composed.budget.calls.append(("acquire", endpoint, consumer, priority))
        return {"allowed": False, "reason": "A685_SYNTHETIC_BUDGET_DENIED"}

    monkeypatch.setattr(composed.budget, "acquire", deny)
    with pytest.raises(Exception, match="A685_SYNTHETIC_BUDGET_DENIED"):
        composed.producer.capture("book", FIVE, priority="EXIT_CRITICAL")
    assert composed.sends == []
    assert composed.sink.stats()["retained_raw_receipts"] == 0
    assert composed.sink.read_native(
        consumer="scanner", method="book", five_key=FIVE,
    )["state"] == "NO_AUTHENTIC_NATIVE_RECEIPT"


def test_http_429_is_accounted_once_but_never_published(composed):
    composed.status["code"] = 429
    with pytest.raises((ReadOnlyPolicyViolation, RESTProducerBlocked)):
        composed.producer.capture("book", FIVE, priority="EXIT_CRITICAL")
    assert len(composed.sends) == 1
    assert [row[0] for row in composed.budget.calls] == [
        "acquire", "start", "finish",
    ]
    assert composed.sink.stats()["retained_raw_receipts"] == 0


def test_sdk_fabrication_without_http_never_produces_native_receipt(composed):
    composed.reader._ProductionMarketReader__client = SimpleNamespace(
        marketdata=SDKMarket(composed.session, fabricates=True))
    with pytest.raises(ReadOnlyPolicyViolation,
                       match="PPI_NATIVE_WIRE_RECEIPT_COUNT_UNVERIFIED"):
        composed.producer.capture("book", FIVE, priority="EXIT_CRITICAL")
    assert composed.sends == []
    assert composed.budget.calls == []
    assert composed.sink.stats()["retained_raw_receipts"] == 0
