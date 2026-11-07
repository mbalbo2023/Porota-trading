"""A680: synthetic-only official PPI REST current/book/intraday schema cases."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import pytest
from rc6_ppi_shared_rest_producer import (MarketOnlyRESTProducer, RESTProducerBlocked,
    WIRE_WITNESS_SCHEMA, _canonical_bytes)

FIVE = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-48HS")
OTHER = ("AL30", "BONOS", "BYMA", "ARS", "INMEDIATA")
T0 = datetime(2026, 11, 4, 14, 20, tzinfo=timezone.utc)


def native(method):
    date = T0.isoformat().replace("+00:00", "Z")
    if method == "book":
        return {"date": date, "bids": [{"position": 0, "price": 100, "quantity": 2}],
                "offers": [{"position": 0, "price": 101, "quantity": 1}]}
    if method == "current":
        return {"date": date, "price": 100, "volume": 12,
                "openingPrice": 99, "max": 102, "min": 98}
    if method == "intraday":
        return [{"date": (T0 - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                 "price": 99, "volume": 20},
                {"date": date, "price": 100, "volume": 12}]
    raise AssertionError("test fixture method")


class FakeGuardedReader:
    def __init__(self):
        self.calls = []
        self.mutate = lambda result: result
        self.duplicate_budget_acquires = 0

    def capture_guarded_native(self, endpoint, five_key, *, priority):
        self.calls.append((endpoint, tuple(five_key), priority))
        raw = native(endpoint)
        receipt = {"schema": WIRE_WITNESS_SCHEMA, "endpoint": endpoint,
            "consumer": "ACQUISITION_OWNER", "priority": priority,
            "http_method": "GET", "identity_source": "CALLER_ONLY_UNVERIFIED",
            "request_identity_3key": [five_key[0], five_key[1], five_key[4]],
            "claimed_five_key": list(five_key),
            "adapter_send_invoked": True, "adapter_response_completed": True,
            "http_status_code": 200, "raw_body_sha256": "1" * 64,
            "parsed_body_sha256": hashlib.sha256(_canonical_bytes(raw)).hexdigest(),
            "received_at_utc": (T0 + timedelta(milliseconds=20)).isoformat(),
            "receive_monotonic_ns": 42,
            "budget_lease_sha256": "2" * 64, "wire_attempt_id": "synthetic-wire-1",
            "guard_budget_receipt_sha256": "3" * 64}
        return self.mutate({"native_payload": raw, "guard_wire_receipt": receipt})

    def acquire(self, *args, **kwargs):
        self.duplicate_budget_acquires += 1
        raise AssertionError("Double budget lease forbidden")


class FakeNativeSink:
    def __init__(self):
        self.calls = []
    def publish_native_rest_receipt(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        return {"schema": "synthetic.receipt", "paper_ready": False}


def setup():
    reader, sink = FakeGuardedReader(), FakeNativeSink()
    producer = MarketOnlyRESTProducer(guarded_source=reader, receipt_sink=sink,
        catalog_five_keys=[FIVE, OTHER], catalog_source_digest="f" * 64,
        session_fingerprint="b" * 64)
    return reader, sink, producer


@pytest.mark.parametrize("method,priority", [
    ("current", "HOT"), ("book", "EXIT_CRITICAL"),
    ("intraday", "SCALPING_HOT")])
def test_preserves_official_rest_native_format_without_false_paper_claim(method, priority):
    reader, sink, producer = setup()
    result = producer.capture(method, FIVE, priority=priority)
    assert result["state"] == "OBSERVE_ONLY"
    assert result["ppi_catalog_authority"] == "NO_VERIFICADO"
    assert result["guard_budget_receipt_authority"] == "NO_VERIFICADO"
    assert result["extra_provider_calls_outside_guard"] == 0
    assert reader.calls == [(method, FIVE, priority)]
    assert reader.duplicate_budget_acquires == 0
    assert len(sink.calls) == 1
    assert sink.calls[0]["raw_payload"] == native(method)
    assert sink.calls[0]["capture_type"] == "REST_" + method.upper()
    assert sink.calls[0]["guard_budget_receipt_sha256"] == "3" * 64


@pytest.mark.parametrize("method,reason", [
    ("search_instruments", "NO_BUDGET_AUTHORITY"),
    ("history", "NO_BUDGET_AUTHORITY"),
    ("market_configuration", "NO_BUDGET_AUTHORITY"),
    ("place_order", "DENIED_READ_ONLY"), ("cancel_order", "DENIED_READ_ONLY")])
def test_denies_unbudgeted_or_private_route_before_provider(method, reason):
    reader, sink, producer = setup()
    with pytest.raises(RESTProducerBlocked, match=reason):
        producer.capture(method, FIVE, priority="EXIT_CRITICAL")
    assert not reader.calls and not sink.calls


def test_identity_priority_and_missing_guard_hook_fail_before_provider():
    reader, sink, producer = setup()
    with pytest.raises(RESTProducerBlocked, match="FIVE_KEY_NOT_PROVEN"):
        producer.capture("book", ("GGAL","ACCIONES","OTRO","ARS","A-48HS"),
                         priority="EXIT_CRITICAL")
    with pytest.raises(RESTProducerBlocked, match="FIVE_KEY_INVALID"):
        producer.capture("book", ("GGAL","ACCIONES","A-48HS"),
                         priority="EXIT_CRITICAL")
    with pytest.raises(RESTProducerBlocked, match="PRIORITY_INVALID"):
        producer.capture("book", FIVE, priority="UNLIMITED")
    reader.capture_guarded_native = None
    with pytest.raises(RESTProducerBlocked, match="GUARDED_NATIVE_SOURCE_UNAVAILABLE"):
        producer.capture("book", FIVE, priority="EXIT_CRITICAL")
    assert not reader.calls and not sink.calls


@pytest.mark.parametrize("field,bad", [
    ("endpoint", "current"), ("priority", "DISCOVERY"),
    ("http_method", "POST"), ("identity_source", "PPI_SIGNED_CATALOG"),
    ("claimed_five_key", list(OTHER)),
    ("request_identity_3key", ["GGAL","ACCIONES","INMEDIATA"]),
    ("http_status_code", 403), ("adapter_send_invoked", False),
    ("adapter_response_completed", False),
    ("parsed_body_sha256", "0" * 64)])
def test_guard_wire_context_mismatch_denies_publication(field, bad):
    reader, sink, producer = setup()
    def tamper(result):
        result["guard_wire_receipt"][field] = bad
        return result
    reader.mutate = tamper
    with pytest.raises(RESTProducerBlocked, match="CONTEXT_MISMATCH"):
        producer.capture("book", FIVE, priority="EXIT_CRITICAL")
    assert not sink.calls


@pytest.mark.parametrize("field,bad", [
    ("raw_body_sha256", "bad"), ("budget_lease_sha256", "bad"),
    ("guard_budget_receipt_sha256", "bad"), ("wire_attempt_id", "")])
def test_guard_wire_receipt_malformed_denies_publication(field, bad):
    reader, sink, producer = setup()
    def tamper(result):
        result["guard_wire_receipt"][field] = bad
        return result
    reader.mutate = tamper
    with pytest.raises(RESTProducerBlocked, match="BUDGET_RECEIPT_UNVERIFIED"):
        producer.capture("book", FIVE, priority="EXIT_CRITICAL")
    assert not sink.calls


@pytest.mark.parametrize("change,reason", [
    ("naive_book_date", "NATIVE_TIME_NAIVE"),
    ("naive_intraday_date", "NATIVE_TIME_NAIVE"),
    ("future_book_date", "NATIVE_TIME_AFTER_RECEIVE"),
    ("naive_guard_received", "RECEIVED_TIME_NAIVE"),
    ("bad_monotonic", "MONOTONIC_INVALID"),
    ("sensitive_payload", "NATIVE_PAYLOAD_INVALID_OR_SENSITIVE"),
    ("oversize", "NATIVE_PAYLOAD_UNBOUNDED")])
def test_source_clock_sensitive_data_or_size_failclosed(change, reason):
    reader, sink, producer = setup()
    def tamper(result):
        raw = result["native_payload"]
        receipt = result["guard_wire_receipt"]
        if change == "naive_book_date": raw["date"] = "2026-11-04T14:20:00"
        elif change == "naive_intraday_date":
            result["native_payload"] = native("intraday")
            result["native_payload"][0]["date"] = "2026-11-04T14:19:00"
        elif change == "future_book_date":
            raw["date"] = (T0 + timedelta(minutes=1)).isoformat()
        elif change == "naive_guard_received": receipt["received_at_utc"] = "2026-11-04T14:20:00"
        elif change == "bad_monotonic": receipt["receive_monotonic_ns"] = -1
        elif change == "sensitive_payload": raw["access_token"] = "NOT_REAL"
        elif change == "oversize": raw["longText"] = "A" * 65536
        if change == "future_book_date":
            receipt["parsed_body_sha256"] = hashlib.sha256(_canonical_bytes(raw)).hexdigest()
        return result
    reader.mutate = tamper
    with pytest.raises(RESTProducerBlocked, match=reason):
        producer.capture("intraday" if change == "naive_intraday_date" else "book",
                         FIVE, priority="EXIT_CRITICAL")
    assert not sink.calls


def test_no_witness_does_not_publish_native_book():
    reader, sink, producer = setup()
    reader.mutate = lambda result: {"native_payload": result["native_payload"],
                                    "guard_wire_receipt": None}
    with pytest.raises(RESTProducerBlocked, match="GUARD_WITNESS_MISSING"):
        producer.capture("book", FIVE, priority="EXIT_CRITICAL")
    assert not sink.calls


def test_constructor_rejects_duplicate_catalog_and_unproven_session():
    with pytest.raises(RESTProducerBlocked, match="CATALOG_DUPLICATE_IDENTITY"):
        MarketOnlyRESTProducer(guarded_source=object(), receipt_sink=FakeNativeSink(),
            catalog_five_keys=[FIVE,FIVE],catalog_source_digest="f" * 64,
            session_fingerprint="b" * 64)
    with pytest.raises(RESTProducerBlocked, match="SESSION_FINGERPRINT_UNVERIFIED"):
        MarketOnlyRESTProducer(guarded_source=object(), receipt_sink=FakeNativeSink(),
            catalog_five_keys=[FIVE], catalog_source_digest="f" * 64,
            session_fingerprint="bad")


@pytest.mark.parametrize("method,consumer,priority", [
    ("current", "scanner", "HOT"),
    ("book", "exit_reader", "EXIT_CRITICAL"),
    ("intraday", "scalping", "SCALPING_HOT")])
def test_native_rpc_store_receives_original_rest_shape_without_paper_admission(
        method, consumer, priority):
    """Cross-PR #982 + A680 is OFFHOST only; neither code path performs GET."""
    from rc6_ppi_shared_feed import PPISharedMarketFeed, PPIReadOnlyNativeReceiptStore

    feed = PPISharedMarketFeed(ppi_catalog_rows=[
        {"ticker": FIVE[0], "type": FIVE[1], "market": FIVE[2],
         "currency": FIVE[3]}], catalog_source_digest="f" * 64)
    feed.register_consumer(consumer, watched_five_keys=[FIVE])
    sink = PPIReadOnlyNativeReceiptStore(feed, max_receipts=8,
        clock=lambda: T0 + timedelta(milliseconds=100),
        monotonic_ns=lambda: 100_000_042)
    source = FakeGuardedReader()
    producer = MarketOnlyRESTProducer(guarded_source=source, receipt_sink=sink,
        catalog_five_keys=[FIVE], catalog_source_digest="f" * 64,
        session_fingerprint="b" * 64)
    result = producer.capture(method, FIVE, priority=priority)
    assert result["state"] == "OBSERVE_ONLY"
    assert result["native_receipt"]["raw_payload"] == native(method)
    assert result["native_receipt"]["paper_ready"] is False
    observed = sink.read_native(consumer=consumer, method=method, five_key=FIVE)
    assert observed["state"] == "OK"
    assert observed["receipt"]["raw_payload"] == native(method)
    assert observed["receipt"]["guard_budget_receipt_authority"] == "NO_VERIFICADO"
