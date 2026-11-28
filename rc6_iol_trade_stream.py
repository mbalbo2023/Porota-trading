"""Last-trade evidence from the authenticated, read-only IOL market stream."""
from collections import OrderedDict
from datetime import datetime, timezone
from decimal import Decimal
import json
from math import isfinite
from secrets import token_hex
from threading import Lock, Thread
import time

from bs_instrument_contracts import aware_datetime, decimal_value


URL = "wss://stream-marketdata.invertironline.com"
TRADE_EVIDENCE = "_rc6_iol_trade"
TRADE_STATUS = "_rc6_iol_trade_status"
TRADE_HOLDS = frozenset({"IOL_TRADE_STREAM_NOT_READY", "IOL_TRADE_SUBSCRIPTION_PENDING",
                         "IOL_TRADE_SAMPLE_ABSENT", "IOL_TRADE_QUOTE_MISMATCH"})
MAX_TRADES = 50
MAX_CONNECTIONS = 12
HEARTBEAT_SECONDS = 15
RATE_LIMIT_SECONDS = 60


class StreamError(RuntimeError):
    def __init__(self, code, *, retry_after=0):
        super().__init__("IOL_TRADE_STREAM_ERROR")
        self.code = code if type(code) is int else None
        self.retry_after = retry_after
        self.keys = ()


def _send(socket, action, **fields):
    socket.send(json.dumps({"action": action, **fields}, separators=(",", ":")))


def _receive(socket, timeout):
    message = json.loads(socket.recv(timeout=timeout), parse_float=Decimal)
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
                "price": str(price), "shares": str(shares), "transport_key": list(key)}
    # IOL documents TS as the day's cumulative nominal quantity. X is the
    # REST matching field and must not be used as interval volume.
    if "TS" in payload:
        try:
            evidence["day_nominals"] = str(decimal_value(payload["TS"], "IOL TS", nonnegative=True))
        except (ValueError, TypeError, ArithmeticError):
            pass
    return key, evidence


def _best_quote(message, received):
    """IOL BBO for ranking; no quote returned here can authorize a fill."""
    if (message.get("type"), message.get("code")) != ("Q", 200):
        return None
    payload = message.get("msg")
    if not isinstance(payload, dict) or not isinstance(payload.get("V"), list):
        return None
    key = tuple(payload.get(field) for field in ("S", "M", "T"))
    if any(not isinstance(value, str) for value in key):
        return None
    try:
        quote_at = aware_datetime(payload.get("D")).astimezone(timezone.utc)
        if quote_at > received or len(payload["V"]) != 2:
            return None
        sides = {}
        for item in payload["V"]:
            if item["T"] not in {"BID", "OFFER"} or type(item["N"]) is not int or item["N"] != 1:
                return None
            decimal_value(item["X"], "IOL BBO quantity", positive=True)
            sides[item["T"]] = decimal_value(item["P"], "IOL BBO price", positive=True)
        spread = float((sides["OFFER"] / sides["BID"] - 1) * 10_000)
    except (ValueError, TypeError, KeyError, ArithmeticError):
        return None
    if not isfinite(spread) or spread < 0:
        return None
    return key, {"quote_at": quote_at.isoformat(), "quote_received_at": received.isoformat(),
                 "spread_bps": spread}


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

    def __init__(self, bearer, *, connect=None, clock=None, quotes=False):
        self._bearer = bearer
        self._connect = connect
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = Lock()
        self._wanted = OrderedDict()
        self._wanted_revision = 0
        self._trades = {}
        self._quotes = {}
        self._quotes_enabled = quotes
        self._subscribed = {}
        self._state = "WAITING_FOR_SCANNER"
        self._stream_token = None

    def want(self, key):
        self.want_many((key,))

    def want_many(self, keys):
        with self._lock:
            self._wanted_revision += 1
            for key in keys:
                self._wanted.setdefault(key, self._wanted_revision)
                self._wanted.move_to_end(key)
                if len(self._wanted) > MAX_TRADES:
                    retired, _ = self._wanted.popitem(last=False)
                    self._trades.pop(retired, None)
                    self._quotes.pop(retired, None)

    def retire(self, keys):
        with self._lock:
            for key in keys:
                self._wanted.pop(key, None)
                self._trades.pop(key, None)
                self._quotes.pop(key, None)

    def _replace_wanted(self, keys):
        wanted = OrderedDict.fromkeys(keys)
        if len(wanted) > MAX_TRADES:
            raise ValueError("IOL_STREAM_SUBSCRIPTION_LIMIT")
        with self._lock:
            self._wanted_revision += 1
            self._wanted = OrderedDict((key, self._wanted.get(key, self._wanted_revision)) for key in wanted)
            self._trades = {key: row for key, row in self._trades.items() if key in wanted}
            self._quotes = {key: row for key, row in self._quotes.items() if key in wanted}

    def evidence(self, key):
        return self.observation(key)[0]

    def observation(self, key):
        token = self._bearer()
        with self._lock:
            if self._state != "SUBSCRIBED" or self._stream_token != token:
                return None, "IOL_TRADE_STREAM_NOT_READY"
            if key not in self._wanted or self._subscribed.get(key) != self._wanted[key]:
                return None, "IOL_TRADE_SUBSCRIPTION_PENDING"
            sample = self._trades.get(key)
            if sample is None:
                return None, "IOL_TRADE_SAMPLE_ABSENT"
            return {**sample, "transport_key": list(key)}, ""

    def _snapshot(self, token, *, include_trades=False):
        with self._lock:
            if self._state != "SUBSCRIBED" or self._stream_token != token:
                return set(), {}
            keys = {key for key, revision in self._subscribed.items() if self._wanted.get(key) == revision}
            if not include_trades:
                return keys, {}
            return keys, {
                key: {**self._trades[key], "transport_key": list(key), **self._quotes.get(key, {})}
                for key in keys if key in self._trades
            }

    def _keys(self):
        with self._lock:
            return set(self._wanted)

    def _has_wanted(self):
        with self._lock:
            return bool(self._wanted)

    def _set_state(self, state, token=None, subscribed=()):
        with self._lock:
            self._state, self._stream_token = state, token
            self._subscribed = (dict(subscribed) if isinstance(subscribed, dict) else
                                {key: self._wanted[key] for key in subscribed if key in self._wanted})
            if token is None:
                self._trades.clear()
                self._quotes.clear()

    @property
    def state(self):
        with self._lock:
            return self._state

    def run(self, stop, *, pool=None, slot=None):
        from websockets.exceptions import InvalidStatus, WebSocketException
        from websockets.sync.client import connect

        connect = self._connect or connect
        rejected_token = None
        backoff = 2
        while not stop.is_set():
            token = self._bearer()
            if not token or token == rejected_token or not self._has_wanted():
                stop.wait(1)
                continue
            if pool is not None and not pool._start_connection(slot, token):
                stop.wait(min(0.25, pool.open_spacing_seconds))
                continue
            self._set_state("CONNECTING")
            failure = None
            try:
                with connect(URL, proxy=None, open_timeout=5, close_timeout=1,
                             ping_interval=None, max_size=64 * 1024) as socket:
                    if pool is not None:
                        pool._socket_opened(slot)
                    self._session(socket, token, stop, pool=pool, slot=slot)
                backoff = 2
            except StreamError as exc:
                failure = exc
            except InvalidStatus as exc:
                seconds = (int(value) for value in exc.response.headers.get_all("Retry-After")
                           if value.isascii() and value.isdigit() and len(value) <= 9)
                failure = StreamError(exc.response.status_code, retry_after=max(seconds, default=0))
            except (OSError, ValueError, TimeoutError, WebSocketException):
                failure = StreamError(None)
            finally:
                self._set_state("WAITING_FOR_RECONNECT")
                if pool is not None:
                    pool._connection_closed(slot, token, failure)
            if failure is not None:
                if pool is None and failure.code in (401, 403):
                    rejected_token = token
                backoff = (max(RATE_LIMIT_SECONDS, failure.retry_after)
                           if failure.code == 429 else min(RATE_LIMIT_SECONDS, 2 * backoff))
            stop.wait(backoff)

    def _session(self, socket, token, stop, *, pool=None, slot=None):
        active = {}
        stream_session = token_hex(32)

        def record(message):
            received = self._clock()
            if message.get("type") == "Q" and self._quotes_enabled:
                sample, field = _best_quote(message, received), "quote_at"
            else:
                sample, field = last_trade(message, received), "trade_at"
            if sample is None or sample[0] not in active:
                return
            key, evidence = sample
            if field == "trade_at":
                evidence["stream_session"] = stream_session
            with self._lock:
                cache = self._trades if field == "trade_at" else self._quotes
                previous = cache.get(key)
                if (self._wanted.get(key) == active[key]
                        and (previous is None or evidence[field] >= previous[field])):
                    cache[key] = evidence

        if token != self._bearer() or (pool is not None and not pool._session_allowed(token, authenticate=True)):
            return

        _send(socket, "auth_token", token=token)
        _ack(socket, "Connected", record)
        if pool is not None:
            pool._socket_authenticated(slot, token)
        ping_at = time.monotonic() + HEARTBEAT_SECONDS
        sync_at = time.monotonic()
        while (not stop.is_set() and token == self._bearer() and self._has_wanted()
               and (pool is None or pool._session_allowed(token))):
            tick = time.monotonic()
            if tick >= ping_at:
                _send(socket, "ping")
                _ack(socket, "Pong", record)
                ping_at = time.monotonic() + HEARTBEAT_SECONDS
            if tick >= sync_at and (pool is None or pool._subscriptions_allowed()):
                with self._lock:
                    wanted = dict(self._wanted)
                # A retired transport key can quickly reappear for a different
                # catalogue identity. Its prior ACK and samples expire at retire.
                removed = {key for key, revision in active.items() if wanted.get(key) != revision}
                added = wanted.keys() - (active.keys() - removed)
                for action, ack, keys in (("unsubscribe", "Unsubscribed", removed),
                                          ("subscribe", "Subscribed", added)):
                    if keys:
                        wires = ["|".join(key) for key in sorted(keys)]
                        _send(socket, action, trades=wires, quotes=wires if self._quotes_enabled else [], order_book=[])
                        try:
                            _ack(socket, ack, record)
                        except StreamError as exc:
                            if action == "subscribe":
                                exc.keys = tuple(keys)
                            raise
                        if action == "subscribe":
                            active.update((key, wanted[key]) for key in keys)
                        else:
                            for key in keys:
                                active.pop(key)
                        self._set_state("SUBSCRIBED" if active else "SUBSCRIBING", token, active)
                sync_at = time.monotonic() + 2
            try:
                record(_receive(socket, 1))
            except TimeoutError:
                continue
        _send(socket, "disconnect")


class IOLTradeStreamPool:
    """Keep catalogue subscriptions stable and reserve one socket for rotation."""

    def __init__(self, bearer, *, clock=None, active=None, connect=None,
                 max_connections=MAX_CONNECTIONS, open_spacing_seconds=1.0):
        if (type(max_connections) is not int or not 2 <= max_connections <= MAX_CONNECTIONS
                or not isfinite(open_spacing_seconds) or open_spacing_seconds <= 0):
            raise ValueError("IOL_STREAM_POOL_INVALID_LIMITS")
        self._bearer = bearer
        self._active = active or (lambda: True)
        self.open_spacing_seconds = open_spacing_seconds
        self._lock = Lock()
        self._reserved = max_connections - 1
        self._rotating_slot = self._reserved
        self._streams = tuple(IOLTradeStream(bearer, connect=connect, clock=clock, quotes=slot != self._reserved)
                              for slot in range(max_connections))
        self._catalog = set()
        self._assigned = {}
        self._rotation = OrderedDict()
        self._rotation_causes = {}
        self._rejected_keys = {}
        self._claims = set()
        self._open = set()
        self._authenticated = {}
        self._issues = {}
        self._suspended = set()
        self._token = None
        self._blocked = ""
        self._retry_at = 0.0
        self._next_open = 0.0

    @staticmethod
    def _wire_keys(keys):
        result = set()
        for key in keys:
            if (not isinstance(key, (tuple, list)) or len(key) != 3
                    or any(not isinstance(value, str) or not value or "|" in value for value in key)):
                raise ValueError("IOL_STREAM_POOL_INVALID_TRANSPORT_KEY")
            result.add(tuple(key))
        return result

    def sync_catalog(self, keys):
        """Replace eligible transport keys without moving existing assignments."""
        catalog = self._wire_keys(keys)
        with self._lock:
            self._refresh_locked(self._bearer())
            if catalog != self._catalog:
                self._catalog = catalog
                self._reconcile_locked()

    def _rotate_locked(self, keys):
        for key in keys:
            if key in self._catalog and key not in self._assigned and key not in self._rejected_keys:
                self._rotation[key] = None
                self._rotation.move_to_end(key)
                if len(self._rotation) > MAX_TRADES:
                    self._rotation.popitem(last=False)

    def _reconcile_locked(self, displaced=()):
        slots = {
            slot: set() for slot in range(self._reserved)
            if slot != self._rotating_slot and slot not in self._suspended
            and (self._blocked != "IOL_WS_FORBIDDEN" or self._authenticated.get(slot) == self._token)
        }
        eligible = self._catalog.difference(self._rejected_keys)
        self._assigned = {key: slot for key, slot in self._assigned.items() if key in eligible and slot in slots}
        for key, slot in self._assigned.items():
            slots[slot].add(key)
        vacancies = (slot for slot, assigned in slots.items() for _ in range(MAX_TRADES - len(assigned)))
        for key, slot in zip(sorted(eligible.difference(self._assigned)), vacancies):
            self._assigned[key] = slot
            slots[slot].add(key)
        self._rotation = OrderedDict.fromkeys(key for key in self._rotation
                                             if key in eligible and key not in self._assigned)
        self._rotation_causes = {key: reason for key, reason in self._rotation_causes.items()
                                 if key in self._catalog and key not in self._assigned}
        self._rejected_keys = {key: reason for key, reason in self._rejected_keys.items() if key in self._catalog}
        self._rotate_locked(displaced)
        slots[self._rotating_slot] = self._rotation
        for slot, stream in enumerate(self._streams):
            stream._replace_wanted(slots.get(slot, ()))

    def want(self, key):
        self.want_many((key,))

    def want_many(self, keys):
        # Preserve request order for the existing rotating path.
        ordered = list(keys)
        self._wire_keys(ordered)
        with self._lock:
            self._refresh_locked(self._bearer())
            self._rotate_locked(tuple(key) for key in ordered)
            self._streams[self._rotating_slot]._replace_wanted(self._rotation)

    def retire(self, keys):
        retired = self._wire_keys(keys)
        with self._lock:
            self._catalog.difference_update(retired)
            self._reconcile_locked()

    def _refresh_locked(self, token):
        changed = bool(token and token != self._token)
        if changed:
            self._token = token
            self._blocked = ""
            self._rotating_slot = self._reserved
            self._issues.clear()
            self._rejected_keys.clear()
            self._rotation_causes.clear()
        if self._retry_at and time.monotonic() >= self._retry_at:
            self._retry_at = 0.0
            for slot in self._suspended:
                self._issues.pop(slot, None)
            self._suspended.clear()
            changed = True
        if changed:
            self._reconcile_locked()

    def _start_connection(self, slot, token):
        with self._lock:
            if token != self._bearer():
                return False
            self._refresh_locked(token)
            now = time.monotonic()
            if (not self._active() or self._blocked or slot in self._claims or now < max(self._next_open, self._retry_at)
                    or self._claims.difference(self._authenticated) or not self._streams[slot]._has_wanted()):
                return False
            self._next_open = now + self.open_spacing_seconds
            self._claims.add(slot)
            return True

    def _socket_opened(self, slot):
        with self._lock:
            self._open.add(slot)

    def _socket_authenticated(self, slot, token):
        with self._lock:
            self._authenticated[slot] = token
            self._issues.pop(slot, None)

    def _session_allowed(self, token, *, authenticate=False):
        with self._lock:
            if not self._active() or self._token != token:
                return False
            if authenticate:
                return not self._blocked and time.monotonic() >= self._retry_at
            return self._blocked != "IOL_WS_AUTH_REJECTED"

    def _subscriptions_allowed(self):
        with self._lock:
            return time.monotonic() >= self._retry_at

    def _connection_closed(self, slot, token, failure):
        with self._lock:
            self._claims.discard(slot)
            self._open.discard(slot)
            self._authenticated.pop(slot, None)
            if token != self._token:
                return
            if failure is None:
                if self._blocked == "IOL_WS_FORBIDDEN":
                    self._reconcile_locked()
                return
            code = failure.code
            reason = {401: "IOL_WS_AUTH_REJECTED", 403: "IOL_WS_FORBIDDEN",
                      429: "IOL_WS_RATE_LIMITED", 400: "IOL_WS_SUBSCRIPTION_REJECTED",
                      404: "IOL_WS_SUBSCRIPTION_REJECTED"}.get(code, "IOL_WS_CONNECTION_FAILED")
            self._issues[slot] = reason
            if code == 429:
                self._retry_at = max(self._retry_at, time.monotonic() + max(RATE_LIMIT_SECONDS, failure.retry_after))
                self._suspended.add(slot)
            elif code in (401, 403):
                self._blocked = reason
            if code in (400, 403, 404):
                self._rejected_keys.update(dict.fromkeys(failure.keys, reason))
            if code == 403:
                available = [index for index, bearer in self._authenticated.items() if bearer == token]
                if self._rotating_slot not in available:
                    self._rotating_slot = max(available, default=self._reserved)
            displaced = sorted(key for key, index in self._assigned.items() if index == slot)
            previous_assignments = set(self._assigned)
            self._reconcile_locked(displaced)
            self._rotation_causes.update(dict.fromkeys(previous_assignments.difference(self._assigned), reason))

    def observation(self, key):
        with self._lock:
            token = self._bearer()
            self._refresh_locked(token)
            if not token or not self._active() or self._blocked == "IOL_WS_AUTH_REJECTED":
                return None, "IOL_TRADE_STREAM_NOT_READY"
            if key not in self._catalog or key in self._rejected_keys:
                return None, "IOL_TRADE_SUBSCRIPTION_PENDING"
            slot = self._assigned.get(key, self._rotating_slot)
            return self._streams[slot].observation(key)

    def evidence(self, key):
        return self.observation(key)[0]

    def _read_streams_locked(self, token, active, *, include_trades=False):
        if not token or not active or self._blocked == "IOL_WS_AUTH_REJECTED":
            return set(), set(), {}
        continuous, rotating, trades = set(), set(), {}
        for slot, stream in enumerate(self._streams):
            acknowledged, samples = stream._snapshot(token, include_trades=include_trades)
            if slot == self._rotating_slot:
                accepted = acknowledged.intersection(self._rotation)
                rotating.update(accepted)
            else:
                accepted = {key for key in acknowledged if self._assigned.get(key) == slot}
                continuous.update(accepted)
            trades.update((key, samples[key]) for key in accepted if key in samples)
        return continuous, rotating, trades

    def recent_trades(self):
        with self._lock:
            token = self._bearer()
            self._refresh_locked(token)
            return tuple(sorted(self._read_streams_locked(token, self._active(), include_trades=True)[2].items()))

    def coverage_receipt(self, key):
        """Return a transport qualification and cause, independent of trade freshness."""
        with self._lock:
            token = self._bearer()
            self._refresh_locked(token)
            continuous, rotating, _ = self._read_streams_locked(token, self._active())
            if key not in self._catalog:
                return {"mode": "OUT_OF_CATALOG", "status": "HOLD", "reason": ""}
            if key in self._rejected_keys:
                return {"mode": "ROTATING", "status": "HOLD", "reason": self._rejected_keys[key]}
            if key in self._assigned:
                return {"mode": "CONTINUOUS", "status": "ACKNOWLEDGED" if key in continuous else "PENDING",
                        "reason": self._blocked or self._issues.get(self._assigned[key], "")}
            return {"mode": "ROTATING", "status": "ACKNOWLEDGED" if key in rotating else "PENDING",
                    "reason": self._rotation_causes.get(key) or self._blocked or "IOL_WS_ROTATION_CAPACITY"}

    def coverage_metrics(self, *, include_keys=True):
        with self._lock:
            token = self._bearer()
            self._refresh_locked(token)
            active = self._active()
            continuous, rotating, _ = self._read_streams_locked(token, active)
            overflow = len(self._catalog) - len(self._assigned)
            reason = self._blocked or ("IOL_WS_RATE_LIMITED" if self._retry_at else "")
            if not reason and self._rejected_keys:
                reason = self._rejected_keys[min(self._rejected_keys)]
            if not reason:
                reason = next((self._issues[slot] for slot in sorted(self._issues)
                               if self._streams[slot]._has_wanted()), "")
            if not reason and overflow:
                reason = "IOL_WS_ROTATION_CAPACITY"
            if not active:
                state = "WAITING_FOR_SESSION"
            elif not self._catalog:
                state = "WAITING_FOR_SCANNER"
            elif not token:
                state = "WAITING_FOR_BEARER"
            elif reason:
                state = "DEGRADED"
            elif len(continuous) == len(self._catalog):
                state = "SUBSCRIBED"
            else:
                state = "SUBSCRIBING"
            counts = {"pool_state": state, "degradation_reason": reason, "open_connections": len(self._open),
                      "catalog_transport_keys": len(self._catalog), "continuous_desired": len(self._assigned),
                      "continuous_subscribed": len(continuous), "rotating_desired": overflow,
                      "rotating_subscribed": len(rotating)}
            if include_keys:
                counts["continuous_ack_keys"] = list(map(list, sorted(continuous)))
            return counts

    @property
    def state(self):
        counts = self.coverage_metrics(include_keys=False)
        if counts["continuous_subscribed"] or counts["rotating_subscribed"]:
            return "SUBSCRIBED"
        return counts["pool_state"]

    def run(self, stop):
        workers = tuple(Thread(target=stream.run, args=(stop,), kwargs={"pool": self, "slot": slot},
                               name=f"iol-trade-stream-{slot + 1}", daemon=True)
                        for slot, stream in enumerate(self._streams))
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
