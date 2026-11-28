from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from queue import Empty, Queue
from threading import Event, Lock, Thread
import time

import pytest

import rc6_iol_trade_stream as stream


AT = datetime(2026, 11, 26, 15, tzinfo=timezone.utc)
RECEIVED = AT + timedelta(microseconds=1)
WIRE_PRICE = "100.00000000000000000012"


def catalogue(count):
    return [(f"TEST{index:04d}", "BCBA", "T1") for index in range(count)]


def wait_for(predicate, timeout=7):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


class Socket:
    def __init__(self, market, number):
        self.market = market
        self.number = number
        self.incoming = Queue()
        self.subscriptions = set()

    def receive_success(self, label):
        self.incoming.put(json.dumps({"type": "Success", "code": 200, "msg": label}))

    def receive_error(self, code):
        self.incoming.put(json.dumps({"type": "Error", "code": code, "msg": "fixture"}))

    def publish(self, wire, before_ack=False):
        symbol, venue, term = wire.split("|")
        payload = {"S": symbol, "M": venue, "T": term, "D": AT.isoformat()}
        trade = {"type": "T", "code": 200, "msg": {**payload, "P": WIRE_PRICE, "X": 15}}
        if before_ack:
            trade["msg"]["P"] = "999"
        # The documentary price is a JSON number. Its digits exceed binary float precision.
        if not before_ack:
            self.incoming.put(json.dumps(trade).replace(json.dumps(WIRE_PRICE), WIRE_PRICE))
        else:
            self.incoming.put(json.dumps(trade))
        self.incoming.put(json.dumps({
            "type": "Q", "code": 200, "msg": {**payload, "V": [
                {"T": "BID", "P": 100, "X": 10, "N": 1}, {"T": "OFFER", "P": "100.01", "X": 20, "N": 1},
            ]},
        }))

    def send(self, data):
        request = json.loads(data)
        action = request["action"]
        trades = set(request.get("trades", ()))
        with self.market.lock:
            self.market.calls.append((self.number, request))
            if action == "auth_token":
                if code := self.market.auth_errors.get(self.number):
                    self.receive_error(code)
                else:
                    self.receive_success("Connected")
            elif action == "subscribe":
                assert request["order_book"] == []
                assert set(request["quotes"]) in (set(), trades)
                assert len(self.subscriptions | trades) <= stream.MAX_TRADES
                if self.market.bad_wire in trades:
                    self.receive_error(404)
                elif self.market.acknowledge:
                    self.accept(trades)
                else:
                    self.market.pending.append((self, trades))
                    for wire in sorted(trades):
                        self.publish(wire, before_ack=True)
            elif action == "unsubscribe":
                self.subscriptions.difference_update(trades)
                self.receive_success("Unsubscribed")
            elif action == "ping":
                self.receive_success("Pong")
            else:
                assert action == "disconnect"

    def accept(self, trades):
        self.subscriptions.update(trades)
        self.receive_success("Subscribed")
        for wire in sorted(trades):
            self.publish(wire)

    def recv(self, *, timeout):
        try:
            return self.incoming.get(timeout=timeout)
        except Empty as exc:
            raise TimeoutError from exc


class Market:
    def __init__(self, *, acknowledge=True, auth_errors=None, bad_wire=None):
        self.lock = Lock()
        self.acknowledge = acknowledge
        self.auth_errors = auth_errors or {}
        self.bad_wire = bad_wire
        self.pending = []
        self.calls = []
        self.connected = []
        self.open = set()
        self.peak_open = 0

    @contextmanager
    def connect(self, url, **options):
        assert url == stream.URL
        assert options["proxy"] is None and options["ping_interval"] is None
        with self.lock:
            number = len(self.connected)
            self.connected.append(time.monotonic())
            self.open.add(number)
            self.peak_open = max(self.peak_open, len(self.open))
        try:
            yield Socket(self, number)
        finally:
            with self.lock:
                self.open.remove(number)

    def release_acks(self):
        with self.lock:
            self.acknowledge = True
            for socket, trades in self.pending:
                socket.accept(trades)
            self.pending.clear()


@contextmanager
def running(pool):
    stop = Event()
    worker = Thread(target=pool.run, args=(stop,), daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join(6)
        assert not worker.is_alive()


def test_540_acknowledged_trades_are_simultaneous_with_stable_assignments_and_exact_bbo():
    market = Market(acknowledge=False)
    pool = stream.IOLTradeStreamPool(lambda: "demo-oauth-receipt", clock=lambda: RECEIVED,
                                    connect=market.connect, open_spacing_seconds=0.02)
    keys = catalogue(540)
    pool.sync_catalog([*keys, *reversed(keys)])

    with running(pool):
        wait_for(lambda: len(market.pending) == 11)
        assert pool.coverage_metrics()["continuous_desired"] == 540
        assert pool.coverage_metrics()["continuous_subscribed"] == 0
        assert pool.recent_trades() == ()
        assert pool.evidence(keys[0]) is None
        market.release_acks()
        wait_for(lambda: len(pool.recent_trades()) == 540)
        status = pool.coverage_metrics()
        assert status == {"pool_state": "SUBSCRIBED", "degradation_reason": "", "open_connections": 11,
                          "catalog_transport_keys": 540, "continuous_desired": 540, "continuous_subscribed": 540,
                          "continuous_ack_keys": list(map(list, keys)), "rotating_desired": 0, "rotating_subscribed": 0}
        assert all(right - left >= 0.017 for left, right in zip(market.connected, market.connected[1:]))
        assert {request["token"] for _, request in market.calls if request["action"] == "auth_token"} == {"demo-oauth-receipt"}
        assert {request["action"] for _, request in market.calls} == {"auth_token", "subscribe"}
        receipt = pool.evidence(keys[0])
        assert receipt["price"] == WIRE_PRICE and receipt["received_at"] == RECEIVED.isoformat()
        assert "spread_bps" not in receipt
        recent = dict(pool.recent_trades())[keys[0]]
        assert recent["quote_at"] == AT.isoformat() and recent["spread_bps"] == pytest.approx(1)

        calls = len(market.calls)
        pool.sync_catalog(list(reversed(keys)))
        assert pool.evidence(keys[0]) == receipt and len(market.calls) == calls

        # A transport identity lost and immediately reintroduced must earn a
        # new ACK. The other 539 subscriptions keep their existing receipts.
        other = pool.evidence(keys[-1])
        pool.retire([keys[0]])
        pool.sync_catalog(keys)
        assert pool.evidence(keys[0]) is None
        assert pool.evidence(keys[-1]) == other
        wait_for(lambda: pool.coverage_metrics()["continuous_subscribed"] == 540)
        changes = [(call["action"], call["trades"]) for _, call in market.calls[calls:]
                   if call["action"] in {"subscribe", "unsubscribe"}]
        assert changes == [("unsubscribe", ["|".join(keys[0])]), ("subscribe", ["|".join(keys[0])])]

        all_keys = catalogue(575)
        pool.sync_catalog(all_keys)
        pool.want_many(all_keys)
        wait_for(lambda: pool.coverage_metrics()["rotating_subscribed"] == 25)
        status = pool.coverage_metrics()
        assert status["continuous_desired"] == 550
        assert status["rotating_desired"] == 25 and status["degradation_reason"] == "IOL_WS_ROTATION_CAPACITY"
        assert pool.state == "SUBSCRIBED" and status["pool_state"] == "DEGRADED"
        assert market.peak_open == status["open_connections"] == 12
        assert pool.coverage_receipt(all_keys[-1]) == {"mode": "ROTATING", "status": "ACKNOWLEDGED",
                                                       "reason": "IOL_WS_ROTATION_CAPACITY"}
        assert any(call["trades"] and not call["quotes"] for _, call in market.calls if call["action"] == "subscribe")
    with pytest.raises(ValueError, match="IOL_STREAM_POOL_INVALID_LIMITS"):
        stream.IOLTradeStreamPool(lambda: None, max_connections=13)


def test_rate_limit_pauses_pool_opens_and_keeps_previously_acked_keys(monkeypatch):
    monkeypatch.setattr(stream, "RATE_LIMIT_SECONDS", 0.6)
    market = Market(auth_errors={1: 429})
    pool = stream.IOLTradeStreamPool(lambda: "fixture", clock=lambda: RECEIVED,
                                    connect=market.connect, max_connections=4, open_spacing_seconds=0.02)
    keys = catalogue(130)
    pool.sync_catalog(keys)
    with running(pool):
        wait_for(lambda: pool.coverage_metrics()["degradation_reason"] == "IOL_WS_RATE_LIMITED")
        assert len(market.connected) == 2
        assert pool.coverage_metrics()["continuous_subscribed"] == 50
        assert pool.coverage_metrics()["rotating_desired"] >= 30
        time.sleep(0.15)
        assert len(market.connected) == 2
        wait_for(lambda: pool.coverage_metrics()["pool_state"] == "SUBSCRIBED")
        assert market.connected[2] - market.connected[1] >= 0.55
        assert pool.coverage_metrics()["continuous_subscribed"] == 130


def test_forbidden_supplemental_socket_reuses_only_an_already_authorized_socket():
    market = Market(auth_errors={1: 403})
    bearer = ["same-bearer"]
    pool = stream.IOLTradeStreamPool(lambda: bearer[0], clock=lambda: RECEIVED,
                                    connect=market.connect, max_connections=4, open_spacing_seconds=0.02)
    keys = catalogue(130)
    pool.sync_catalog(keys)
    with running(pool):
        wait_for(lambda: pool.coverage_metrics()["degradation_reason"] == "IOL_WS_FORBIDDEN")
        pool.want_many(keys[-50:])
        wait_for(lambda: pool.coverage_metrics()["rotating_subscribed"] == 50)
        status = pool.coverage_metrics()
        assert status["continuous_subscribed"] == 0 and status["open_connections"] == 1
        assert len(market.connected) == 2
        assert pool.coverage_receipt(keys[-1])["mode"] == "ROTATING"
        assert pool.coverage_receipt(keys[-1])["status"] == "ACKNOWLEDGED"
        receipt = pool.evidence(keys[-1])
        assert receipt["price"] == WIRE_PRICE
        bearer[0] = "renewed-by-owner"
        assert pool.evidence(keys[-1]) is None
        wait_for(lambda: pool.coverage_metrics()["pool_state"] == "SUBSCRIBED")
        assert pool.coverage_metrics()["continuous_subscribed"] == 130
        wait_for(lambda: pool.evidence(keys[-1]) is not None)
        assert pool.evidence(keys[-1])["stream_session"] != receipt["stream_session"]


def test_rejected_subscription_batch_and_session_close_remain_holds():
    active = Event()
    keys = catalogue(54)
    market = Market(bad_wire="|".join(keys[-1]))
    pool = stream.IOLTradeStreamPool(lambda: "fixture", clock=lambda: RECEIVED, active=active.is_set,
                                    connect=market.connect, max_connections=3, open_spacing_seconds=0.02)
    pool.sync_catalog(keys)
    with running(pool):
        assert pool.state == "WAITING_FOR_SESSION" and len(market.connected) == 0
        active.set()
        wait_for(lambda: pool.coverage_metrics()["degradation_reason"] == "IOL_WS_SUBSCRIPTION_REJECTED")
        wait_for(lambda: pool.coverage_metrics()["continuous_subscribed"] == 50)
        assert pool.coverage_receipt(keys[-1]) == {"mode": "ROTATING", "status": "HOLD",
                                                  "reason": "IOL_WS_SUBSCRIPTION_REJECTED"}
        pool.want_many(keys)
        assert pool.evidence(keys[-1]) is None
        assert len(market.connected) == 2
        previous = pool.evidence(keys[1])["stream_session"]
        active.clear()
        assert pool.state == "WAITING_FOR_SESSION"
        assert pool.recent_trades() == ()
        wait_for(lambda: pool.coverage_metrics()["open_connections"] == 0)
        active.set()
        wait_for(lambda: pool.evidence(keys[1]) is not None)
        assert pool.evidence(keys[1])["stream_session"] != previous


def test_handshake_finishing_after_session_close_does_not_send_the_bearer():
    active = Event()
    active.set()
    market = Market()

    @contextmanager
    def closes_after_handshake(url, **options):
        with market.connect(url, **options) as socket:
            active.clear()
            yield socket

    pool = stream.IOLTradeStreamPool(lambda: "fixture", active=active.is_set, connect=closes_after_handshake,
                                    max_connections=2, open_spacing_seconds=0.02)
    pool.sync_catalog(catalogue(1))
    with running(pool):
        wait_for(lambda: len(market.connected) == 1 and not market.open)
        assert pool.state == "WAITING_FOR_SESSION"
        assert market.calls == []


def test_shared_forbidden_receipt_halts_authentication_on_an_inflight_socket():
    market = Market(acknowledge=False)
    release = Event()

    @contextmanager
    def delay_second_handshake(url, **options):
        with market.connect(url, **options) as socket:
            if socket.number == 1:
                assert release.wait(5)
            yield socket

    pool = stream.IOLTradeStreamPool(lambda: "fixture", connect=delay_second_handshake,
                                    max_connections=3, open_spacing_seconds=0.02)
    pool.sync_catalog(catalogue(60))
    with running(pool):
        try:
            wait_for(lambda: len(market.connected) == 2 and len(market.pending) == 1)
            with market.lock:
                market.pending.pop()[0].receive_error(403)
            wait_for(lambda: pool.coverage_metrics()["degradation_reason"] == "IOL_WS_FORBIDDEN")
        finally:
            release.set()
        wait_for(lambda: not market.open)
        assert [number for number, request in market.calls if request["action"] == "auth_token"] == [0]
