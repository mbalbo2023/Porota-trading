"""Last-trade evidence from the authenticated, read-only IOL market stream."""
from collections import OrderedDict
from datetime import datetime, timezone
import json
from secrets import token_hex
from threading import Lock
import time

from bs_instrument_contracts import aware_datetime, decimal_value


URL = "wss://stream-marketdata.invertironline.com"
TRADE_EVIDENCE = "_rc6_iol_trade"
TRADE_STATUS = "_rc6_iol_trade_status"
TRADE_HOLDS = frozenset({"IOL_TRADE_STREAM_NOT_READY", "IOL_TRADE_SUBSCRIPTION_PENDING",
                         "IOL_TRADE_SAMPLE_ABSENT", "IOL_TRADE_QUOTE_MISMATCH"})
MAX_TRADES = 50
HEARTBEAT_SECONDS = 15


class StreamError(RuntimeError):
    def __init__(self, code):
        super().__init__("IOL_TRADE_STREAM_ERROR")
        self.code = code


def _send(socket, action, **fields):
    socket.send(json.dumps({"action": action, **fields}, separators=(",", ":")))


def _receive(socket, timeout):
    message = json.loads(socket.recv(timeout=timeout))
    if not isinstance(message, dict):
        raise StreamError(None)
    if message.get("type") == "Error":
        raise StreamError(message.get("code"))
    return message


def _ack(socket, label, on_trade):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = _receive(socket, max(0, deadline - time.monotonic()))
        if (result.get("type"), result.get("code"), result.get("msg")) == ("Success", 200, label):
            return
        on_trade(result)
    raise TimeoutError("IOL_TRADE_STREAM_ACK_TIMEOUT")


def last_trade(message, received):
    """Parse a documentary T event without accepting a quote, Book or HTTP clock."""
    if (message.get("type"), message.get("code")) != ("T", 200):
        return None
    payload = message.get("msg")
    if not isinstance(payload, dict):
        return None
    key = tuple(payload.get(k) for k in ("S", "M", "T"))
    if any(not isinstance(value, str) for value in key):
        return None
    try:
        at = aware_datetime(payload.get("D")).astimezone(timezone.utc)
        price = decimal_value(payload.get("P"), "IOL trade price", positive=True)
        shares = decimal_value(payload.get("X"), "IOL trade volume", positive=True)
    except (ValueError, TypeError, ArithmeticError):
        return None
    if at > received:
        return None
    evidence = {"trade_at": at.isoformat(), "received_at": received.isoformat(),
                "price": str(price), "shares": str(shares)}
    # IOL documents TS as the day's cumulative nominal quantity. X is the
    # REST matching field and must not be used as interval volume.
    if "TS" in payload:
        try:
            evidence["day_nominals"] = str(decimal_value(payload["TS"], "IOL TS", nonnegative=True))
        except (ValueError, TypeError, ArithmeticError):
            pass
    return key, evidence


def matched_trade(evidence, quote, received):
    """Same venue trade evidenced by the price and IOL's documented X/shares field."""
    if not isinstance(evidence, dict) or not isinstance(quote, dict):
        return None
    try:
        if (decimal_value(quote["prices"]["current"], "IOL current", positive=True) !=
                decimal_value(evidence["price"], "IOL trade price", positive=True) or
                decimal_value(quote["volume"]["shares"], "IOL quote volume", positive=True) !=
                decimal_value(evidence["shares"], "IOL trade volume", positive=True)):
            return None
        trade_at = aware_datetime(evidence["trade_at"])
        if trade_at <= aware_datetime(evidence["received_at"]) <= received:
            return evidence["trade_at"]
    except (KeyError, ValueError, TypeError, ArithmeticError):
        return None
    return None


class IOLTradeStream:
    """One connection for recently requested scanner keys, sharing its REST bearer."""

    def __init__(self, bearer, *, connect=None, clock=None):
        self._bearer = bearer
        self._connect = connect
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = Lock()
        self._wanted = OrderedDict()
        self._trades = {}
        self._subscribed = set()
        self._state = "WAITING_FOR_SCANNER"
        self._stream_token = None

    def want(self, key):
        self.want_many((key,))

    def want_many(self, keys):
        with self._lock:
            for key in keys:
                self._wanted[key] = None
                self._wanted.move_to_end(key)
                if len(self._wanted) > MAX_TRADES:
                    retired, _ = self._wanted.popitem(last=False)
                    self._trades.pop(retired, None)

    def evidence(self, key):
        return self.observation(key)[0]

    def observation(self, key):
        with self._lock:
            if self._state != "SUBSCRIBED" or self._stream_token != self._bearer():
                return None, "IOL_TRADE_STREAM_NOT_READY"
            if key not in self._subscribed:
                return None, "IOL_TRADE_SUBSCRIPTION_PENDING"
            sample = self._trades.get(key)
            return (sample, "") if sample is not None else (None, "IOL_TRADE_SAMPLE_ABSENT")

    def _keys(self):
        with self._lock:
            return set(self._wanted)

    def _set_state(self, state, token=None, subscribed=()):
        with self._lock:
            self._state, self._stream_token = state, token
            self._subscribed = set(subscribed)
            if token is None:
                self._trades.clear()

    @property
    def state(self):
        with self._lock:
            return self._state

    def run(self, stop):
        from websockets.exceptions import WebSocketException
        from websockets.sync.client import connect

        connect = self._connect or connect
        rejected_token = None
        backoff = 2
        while not stop.is_set():
            token = self._bearer()
            if not token or token == rejected_token or not self._keys():
                stop.wait(1)
                continue
            self._set_state("CONNECTING")
            try:
                with connect(URL, proxy=None, open_timeout=5, close_timeout=1,
                             ping_interval=None, max_size=64 * 1024) as socket:
                    self._session(socket, token, stop)
                backoff = 2
            except StreamError as exc:
                if exc.code in (401, 403):
                    rejected_token = token
                backoff = 60 if exc.code == 429 else min(60, 2 * backoff)
            except (OSError, ValueError, TimeoutError, WebSocketException):
                backoff = min(60, 2 * backoff)
            finally:
                self._set_state("WAITING_FOR_RECONNECT")
            stop.wait(backoff)

    def _session(self, socket, token, stop):
        active = set()
        stream_session = token_hex(32)

        def record(message):
            sample = last_trade(message, self._clock())
            if sample is None or sample[0] not in active:
                return
            key, evidence = sample
            evidence["stream_session"] = stream_session
            with self._lock:
                previous = self._trades.get(key)
                if key in self._wanted and (previous is None or evidence["trade_at"] >= previous["trade_at"]):
                    self._trades[key] = evidence

        _send(socket, "auth_token", token=token)
        _ack(socket, "Connected", record)
        ping_at = time.monotonic() + HEARTBEAT_SECONDS
        sync_at = time.monotonic()
        while not stop.is_set() and token == self._bearer():
            tick = time.monotonic()
            if tick >= ping_at:
                _send(socket, "ping")
                _ack(socket, "Pong", record)
                ping_at = time.monotonic() + HEARTBEAT_SECONDS
            if tick >= sync_at:
                wanted = self._keys()
                for action, ack, keys in (("unsubscribe", "Unsubscribed", active - wanted),
                                          ("subscribe", "Subscribed", wanted - active)):
                    if keys:
                        _send(socket, action, trades=["|".join(key) for key in sorted(keys)],
                              quotes=[], order_book=[])
                        _ack(socket, ack, record)
                active = wanted
                self._set_state("SUBSCRIBED", token, active)
                sync_at = time.monotonic() + 2
            try:
                record(_receive(socket, 1))
            except TimeoutError:
                continue
        _send(socket, "disconnect")
