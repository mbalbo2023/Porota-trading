"""Read-only IOL market RPCs over the existing RC6 local peer transport."""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import replace
from datetime import datetime, timezone
from functools import partial
from math import ceil
import os
import re
from secrets import token_hex
from threading import Lock
import time

from bs_instrument_contracts import aware_datetime
from iol_rest_readonly_rc6 import IOLRestError, IOLRestReadOnly, SYMBOL
from iol_shadow_collector_rc6 import CircuitOpenError, RateGovernor
from fe_fixed_income_nominal_contract_rc6 import FAMILIES as FIXED_INCOME_FAMILIES
from rc6_fixed_income_iol_evidence import create_iol_book, iol_quote_book_fields
from rc6_iol_market_data import IOLMarketReader, MARKETS, SPOT, TERMS, read_iol_market_payload
from rc6_iol_trade_stream import MAX_TRADES, TRADE_EVIDENCE, TRADE_STATUS, matched_trade
from rc6_ppi_shared_feed import (
    NATIVE_RPC_WAIT_SECONDS, ReadOnlyUnixFeedClient, ReadOnlyUnixFeedServer,
    SharedFeedUnavailable, _canonical_bytes, _sha,
)


CONTROL_SCHEMA = "rc6.iol.market-gateway-control.v1"
DATA_SCHEMA = "rc6.iol.market-gateway-receipt.v1"
ROLE_PRIORITIES = {
    "scanner": frozenset({"DISCOVERY", "WARM", "HOT", "STRATEGY_HOT", "OPENED_CRITICAL"}),
    "exit_reader": frozenset({"EXIT_CRITICAL"}),
}
ROLE_ENDPOINTS = {"scanner": frozenset({"asset", "quote", "depth"}),
                  "exit_reader": frozenset({"asset", "depth"})}
REQUEST_FIELDS = {"op", "consumer", "endpoint", "identity", "priority", "session_generation"}
SUBSCRIPTION_FIELDS = {"op", "consumer", "identities", "session_generation"}
SALE_BOOK2_FIELDS = {"op", "consumer", "identity", "session_generation", "book1_receipt_sha256",
                     "cutoff_at", "cutoff_monotonic_ns"}
SALE_BOOK_RETENTION = 256
PREPARE_TRADES_LIMIT = MAX_TRADES // 2  # A full batch must fit the 4 KiB IPC request.
_REASON = re.compile(r"IOL_[A-Z0-9_]{1,100}\Z")
PROFILE_ENV = "POROTA_RC6_MARKET_PROVIDER"


def iol_primary_enabled():
    provider = os.getenv(PROFILE_ENV, "PPI")
    if provider not in {"PPI", "IOL_REST"}:
        raise ValueError("RC6_MARKET_PROVIDER_INVALID")
    return provider == "IOL_REST"


def iol_gateway_socket_path(database):
    from cg_paper_workspace import artifact_root

    return artifact_root(database) / "iol-gateway" / "market.sock"


def iol_gateway_identities(keys):
    """Use exact local keys only as a read scope, not as provider attestations."""
    scoped = set()
    for key in keys:
        try:
            scoped.add(_identity(key))
        except ValueError:
            continue
    return frozenset(scoped)


def iol_gateway_reader_needs_reattach(error):
    return isinstance(error, SharedFeedUnavailable) or (
        isinstance(error, IOLRestError) and str(error) in {
            "IOL_GATEWAY_OWNER_CHANGED", "IOL_GATEWAY_OWNER_NOT_PINNED",
            "IOL_GATEWAY_UNREGISTERED_CLIENT",
        })


def _identity(value):
    if (not isinstance(value, (tuple, list)) or len(value) != 5
            or any(type(part) is not str for part in value)):
        raise ValueError("IOL_GATEWAY_EXACT_IDENTITY_REQUIRED")
    symbol, family, market, currency, term = value
    if (not SYMBOL.fullmatch(symbol) or family not in SPOT or market not in MARKETS
            or currency not in {"ARS", "USD"} or term not in TERMS):
        raise ValueError("IOL_GATEWAY_EXACT_IDENTITY_UNSUPPORTED")
    return tuple(value)


def _stream_key(identity):
    return identity[0], MARKETS[identity[2]], TERMS[identity[4]]


class IOLMarketGateway(ReadOnlyUnixFeedServer):
    """Keep the REST session and budget in the owner, one endpoint per demand."""

    control_schema = CONTROL_SCHEMA

    def __init__(self, rest, *, allowed, socket_path, supervisor_pid, clock=None, trade_stream=None):
        if (not isinstance(rest, IOLRestReadOnly) or not isinstance(rest.governor, RateGovernor)
                or rest.governor.max_wait_seconds is None
                or rest.governor.max_wait_seconds >= NATIVE_RPC_WAIT_SECONDS):
            raise ValueError("IOL_GATEWAY_BOUNDED_WIRE_BUDGET_REQUIRED")
        rest.governor.policy.validate()
        if set(allowed) != set(ROLE_PRIORITIES):
            raise ValueError("IOL_GATEWAY_ROLE_SCOPE_REQUIRED")
        self.allowed = {role: frozenset(_identity(key) for key in keys) for role, keys in allowed.items()}
        self._rest = rest
        self._wire = deque(maxlen=rest.governor.policy.max_calls_per_minute * 2)
        self._wire_lock = Lock()
        self._wire_consumer = None
        self._fi_assets = {}
        self._fi_exit_books = deque(maxlen=SALE_BOOK_RETENTION)
        self._fi_exit_books_lock = Lock()
        rest.wire_observer = self._record_wire
        self.trade_stream = trade_stream
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._generation = token_hex(32)
        super().__init__(None, socket_path=socket_path, supervisor_pid=supervisor_pid,
                         native_acquirer=self._capture, owner_status=self._status)

    def _status(self):
        return {"state": "OPEN", "provider": "IOL_REST", "wire_budget_attached": True,
                "owner_pid": os.getpid(), "session_generation": self._generation,
                "authenticated": self._rest.session_current,
                "trade_stream_state": self.trade_stream.state if self.trade_stream else "NOT_CONFIGURED",
                "wire_attempts": self._rest.governor.calls_total,
                "rate_limited_attempts": self._rest.governor.calls_429}

    def _record_wire(self, endpoint, started, ended, status, body_bytes):
        with self._wire_lock:
            self._wire.append((started, ended - started, self._wire_consumer,
                               endpoint, status, body_bytes))

    def _wire_usage(self):
        now = time.monotonic()
        with self._wire_lock:
            recent = [row for row in self._wire if 0 <= now - row[0] < 60]
        results = Counter(f"{consumer}/{endpoint}/{status}" for
                          _, _, consumer, endpoint, status, _ in recent)
        latencies = sorted(row[1] for row in recent)
        return {
            "attempts_last_second": sum(now - row[0] < 1 for row in recent),
            "attempts_last_minute": len(recent),
            "http_results": dict(results),
            "response_body_bytes_read": sum(row[5] or 0 for row in recent),
            "latency_ms_p50": round(1000 * latencies[ceil(.5 * len(latencies)) - 1], 2) if latencies else None,
            "latency_ms_p95": round(1000 * latencies[ceil(.95 * len(latencies)) - 1], 2) if latencies else None,
        }

    def _response(self, state, *, wire_calls=0, **fields):
        return {"schema": DATA_SCHEMA, **self._status(), "state": state,
                "wire_calls": wire_calls, "paper_ready": False, "order_route": "NOT_PRESENT", **fields}

    def _capture(self, *, endpoint, identity, priority, consumer, peer_pid):
        if not self._role_permitted(consumer, peer_pid):
            return self._response("IOL_GATEWAY_UNREGISTERED_CLIENT")
        governor = self._rest.governor
        before = governor.calls_total
        self._wire_consumer = consumer
        try:
            fixed_income = identity[1] in FIXED_INCOME_FAMILIES
            if fixed_income and endpoint == "asset":
                self._fi_assets.pop(consumer, None)
            trade_key = _stream_key(identity)
            if endpoint == "quote" and self.trade_stream is not None:
                self.trade_stream.want(trade_key)
            payload = read_iol_market_payload(self._rest, endpoint, identity)
            received = aware_datetime(self._clock()).astimezone(timezone.utc)
            received_ns = time.monotonic_ns()
            if not isinstance(payload, dict):
                raise ValueError("IOL_GATEWAY_MARKET_PAYLOAD_INVALID")
            if endpoint == "quote":
                payload = dict(payload)
                payload.pop(TRADE_EVIDENCE, None)
                payload.pop(TRADE_STATUS, None)
                if self.trade_stream is not None:
                    evidence, hold = self.trade_stream.observation(trade_key)
                    if matched_trade(evidence, payload, received):
                        payload[TRADE_EVIDENCE] = {**evidence, "identity": list(identity),
                                                   "gateway_generation": self._generation,
                                                   "quote_received_at": received.isoformat()}
                    else:
                        payload[TRADE_STATUS] = hold or "IOL_TRADE_QUOTE_MISMATCH"
            body = {"endpoint": endpoint, "identity": list(identity),
                    "received_at": received.isoformat(), "payload": payload}
            if len(_canonical_bytes(body)) > 120_000:
                raise ValueError("IOL_GATEWAY_MARKET_PAYLOAD_INVALID")
            proof = {"provider": "IOL_REST", "session_generation": self._generation, **body}
            fields = {}
            if fixed_income and endpoint == "asset":
                self._fi_assets[consumer] = (identity, payload, received.isoformat())
            if fixed_income and endpoint == "depth":
                asset_entry = self._fi_assets.pop(consumer, None)
                if asset_entry is None or asset_entry[0] != identity:
                    raise IOLRestError("IOL_FI_BOOK_ASSET_CAPTURE_MISSING")
                try:
                    receipt = create_iol_book(
                        identity, asset=asset_entry[1], asset_at=asset_entry[2], depth=payload,
                        depth_at=received.isoformat(), depth_monotonic_ns=received_ns,
                        generation=self._generation, consumer=consumer, depth_sha256=_sha(payload))
                except (KeyError, ValueError, TypeError, IOLRestError) as exc:
                    raise IOLRestError("IOL_FI_BOOK_RECEIPT_UNVERIFIED") from exc
                fields["native_book"] = receipt
                if len(_canonical_bytes({**body, **fields})) > 126_000:
                    raise IOLRestError("IOL_FI_BOOK_RECEIPT_OVERSIZE")
                if consumer == "exit_reader":
                    with self._fi_exit_books_lock:
                        self._fi_exit_books.append(receipt)
            return self._response("OK", wire_calls=governor.calls_total-before,
                                  receipt_sha256=_sha(proof), **body, **fields)
        except CircuitOpenError:
            return self._response("IOL_GATEWAY_RATE_HOLD", wire_calls=governor.calls_total-before)
        except IOLRestError as exc:
            state = str(exc)
            status = exc.status_code if type(exc.status_code) is int else None
            retry = exc.retry_after if type(exc.retry_after) is int and exc.retry_after >= 0 else None
            return self._response(state if _REASON.fullmatch(state) else "IOL_GATEWAY_READ_FAILED",
                                  wire_calls=governor.calls_total-before, http_status=status,
                                  retry_after_seconds=retry)
        except (ValueError, TypeError, OSError):
            return self._response("IOL_GATEWAY_MARKET_PAYLOAD_INVALID",
                                  wire_calls=governor.calls_total-before)
        finally:
            self._wire_consumer = None

    def _first_sale_book2(self, request, peer_pid):
        """Read the first later retained EXIT book from the pinned owner."""
        from rc6_fixed_income_sale_authority import SALE_FAMILIES

        if set(request) != SALE_BOOK2_FIELDS or request["consumer"] != "exit_reader":
            return self._response("IOL_GATEWAY_ROLE_SCOPE_DENIED")
        if peer_pid is None or not self._role_permitted("exit_reader", peer_pid):
            return self._response("IOL_GATEWAY_UNREGISTERED_CLIENT")
        if request["session_generation"] != self._generation:
            return self._response("IOL_FI_BOOK2_SESSION_CHANGED")
        try:
            identity = _identity(request["identity"])
            cutoff_at = aware_datetime(request["cutoff_at"])
            cutoff_ns, book1_sha = request["cutoff_monotonic_ns"], request["book1_receipt_sha256"]
            if (identity[1] not in SALE_FAMILIES or identity not in self.allowed["exit_reader"]
                    or type(cutoff_ns) is not int or not 0 < cutoff_ns <= time.monotonic_ns()
                    or cutoff_at > aware_datetime(self._clock())
                    or not isinstance(book1_sha, str) or not re.fullmatch(r"[a-f0-9]{64}", book1_sha)):
                raise ValueError("IOL_FI_BOOK2_SCOPE_UNVERIFIED")
        except (TypeError, ValueError, OverflowError):
            return self._response("IOL_FI_BOOK2_SCOPE_UNVERIFIED")
        found_book1 = False
        with self._fi_exit_books_lock:
            for book in self._fi_exit_books:
                if book["identity"] != list(identity):
                    continue
                if not found_book1:
                    found_book1 = book["receipt_sha256"] == book1_sha
                    continue
                if (book["receive_monotonic_ns"] > cutoff_ns
                        and aware_datetime(book["received_at_utc"]) > cutoff_at
                        and aware_datetime(book["depth"]["last_updated"]) > cutoff_at):
                    return self._response("OK", receipt=book)
        return self._response("IOL_FI_BOOK2_NOT_YET_CAPTURED" if found_book1
                              else "IOL_FI_BOOK1_NOT_RETAINED")

    def _prepare_trades(self, request, peer_pid):
        if set(request) != SUBSCRIPTION_FIELDS or request["consumer"] != "scanner":
            return self._response("IOL_GATEWAY_ROLE_SCOPE_DENIED")
        if peer_pid is None or not self._role_permitted("scanner", peer_pid):
            return self._response("IOL_GATEWAY_UNREGISTERED_CLIENT")
        if request["session_generation"] != self._generation:
            return self._response("IOL_GATEWAY_OWNER_CHANGED")
        items = request["identities"]
        if not isinstance(items, list) or not 0 < len(items) <= PREPARE_TRADES_LIMIT:
            return self._response("IOL_GATEWAY_SUBSCRIPTION_LIMIT")
        try:
            keys = tuple(dict.fromkeys(map(_identity, items)))
        except ValueError:
            return self._response("IOL_GATEWAY_EXACT_IDENTITY_REQUIRED")
        if not self.allowed["scanner"].issuperset(keys):
            return self._response("IOL_GATEWAY_IDENTITY_SCOPE_DENIED")
        if self.trade_stream is None:
            return self._response("IOL_TRADE_STREAM_NOT_READY")
        stream_keys = tuple(dict.fromkeys(map(_stream_key, keys)))
        self.trade_stream.want_many(stream_keys)
        return self._response("OK", scheduled_subscriptions=len(stream_keys))

    def dispatch(self, request, *, peer_pid=None):
        if not isinstance(request, dict):
            return self._response("IOL_GATEWAY_REQUEST_DENIED")
        operation = request.get("op")
        if operation == "iol_prepare_trades":
            return self._prepare_trades(request, peer_pid)
        if operation == "iol_native_sale_book2":
            return self._first_sale_book2(request, peer_pid)
        if operation in ("register_client", "owner_status"):
            if operation == "register_client" and request.get("consumer") not in ("scanner", "exit_reader"):
                return self._response("IOL_GATEWAY_ROLE_SCOPE_DENIED")
            result = super().dispatch(request, peer_pid=peer_pid)
            if operation == "owner_status" and result.get("provider") == "IOL_REST":
                result["wire_usage"] = self._wire_usage()
            return result
        if operation != "iol_market_read" or set(request) != REQUEST_FIELDS:
            return self._response("IOL_GATEWAY_REQUEST_DENIED")
        role, endpoint, priority = (request[k] for k in ("consumer", "endpoint", "priority"))
        if (type(role) is not str or role not in ROLE_PRIORITIES
                or type(endpoint) is not str or type(priority) is not str
                or endpoint not in ROLE_ENDPOINTS[role] or priority not in ROLE_PRIORITIES[role]):
            return self._response("IOL_GATEWAY_ROLE_SCOPE_DENIED")
        if peer_pid is None or not self._role_permitted(role, peer_pid):
            return self._response("IOL_GATEWAY_UNREGISTERED_CLIENT")
        if request["session_generation"] != self._generation:
            return self._response("IOL_GATEWAY_OWNER_CHANGED")
        try:
            identity = _identity(request["identity"])
        except ValueError:
            return self._response("IOL_GATEWAY_EXACT_IDENTITY_REQUIRED")
        if identity not in self.allowed[role]:
            return self._response("IOL_GATEWAY_IDENTITY_SCOPE_DENIED")
        try:
            return self._native_router.demand(endpoint=endpoint, identity=identity, priority=priority,
                                              consumer=role, peer_pid=peer_pid)
        except RuntimeError:
            return self._response("IOL_GATEWAY_SCHEDULER_HOLD", wire_calls="NO_VERIFICADO")
        except TimeoutError:
            return self._response("IOL_GATEWAY_READ_DEADLINE", wire_calls="NO_VERIFICADO")


class IOLGatewayClient(ReadOnlyUnixFeedClient):
    control_schema = CONTROL_SCHEMA

    def _owner_status_matches_provider(self, response):
        return (response.get("provider") == "IOL_REST" and
                response.get("wire_budget_attached") is True)

    def prepare_trades(self, identities):
        if self._pinned_owner is None:
            raise IOLRestError("IOL_GATEWAY_OWNER_NOT_PINNED")
        if not 0 < len(identities) <= PREPARE_TRADES_LIMIT:
            raise IOLRestError("IOL_GATEWAY_SUBSCRIPTION_LIMIT")
        owner_pid, generation = self._pinned_owner
        response = self._request({"op": "iol_prepare_trades", "consumer": "scanner",
            "identities": [list(_identity(key)) for key in identities], "session_generation": generation},
            require_owner_identity=True)
        if (response.get("schema") != DATA_SCHEMA or response.get("provider") != "IOL_REST"
                or type(response.get("wire_calls")) is not int or response["wire_calls"] != 0
                or response.get("paper_ready") is not False
                or response.get("order_route") != "NOT_PRESENT"):
            raise IOLRestError("IOL_GATEWAY_RECEIPT_UNVERIFIED")
        if (response.get("owner_pid"), response.get("session_generation")) != (owner_pid, generation):
            raise SharedFeedUnavailable("IOL_GATEWAY_OWNER_CHANGED")
        count = response.get("scheduled_subscriptions")
        if response.get("state") != "OK":
            reason = response.get("state")
            raise IOLRestError(reason if isinstance(reason, str) and _REASON.fullmatch(reason)
                               else "IOL_GATEWAY_RECEIPT_UNVERIFIED")
        if type(count) is not int or not 0 < count <= len(identities):
            raise IOLRestError("IOL_GATEWAY_RECEIPT_UNVERIFIED")
        return count

    def market_read(self, endpoint, identity, *, consumer, priority, native_books=None):
        if self._pinned_owner is None:
            raise IOLRestError("IOL_GATEWAY_OWNER_NOT_PINNED")
        identity = _identity(identity)
        owner_pid, generation = self._pinned_owner
        response = self._request({"op": "iol_market_read", "consumer": consumer,
            "endpoint": endpoint, "identity": list(identity), "priority": priority,
            "session_generation": generation}, require_owner_identity=True)
        if (response.get("schema") != DATA_SCHEMA or response.get("provider") != "IOL_REST"
                or response.get("paper_ready") is not False or response.get("order_route") != "NOT_PRESENT"):
            raise IOLRestError("IOL_GATEWAY_RECEIPT_UNVERIFIED")
        if (response.get("owner_pid"), response.get("session_generation")) != (owner_pid, generation):
            raise SharedFeedUnavailable("IOL_GATEWAY_OWNER_CHANGED")
        state = response.get("state")
        if state != "OK":
            if (not isinstance(state, str) or not _REASON.fullmatch(state)
                    or "payload" in response or "receipt_sha256" in response):
                raise IOLRestError("IOL_GATEWAY_RECEIPT_UNVERIFIED")
            raise IOLRestError(state, status_code=response.get("http_status"),
                               retry_after=response.get("retry_after_seconds"))
        proof = {key: response.get(key) for key in
                 ("provider", "session_generation", "endpoint", "identity", "received_at", "payload")}
        if (proof["endpoint"] != endpoint or proof["identity"] != list(identity)
                or not isinstance(proof["payload"], dict)
                or type(response.get("wire_calls")) is not int or response["wire_calls"] < 1
                or response.get("receipt_sha256") != _sha(proof)):
            raise IOLRestError("IOL_GATEWAY_RECEIPT_UNVERIFIED")
        if native_books is not None and endpoint == "depth":
            book = response.get("native_book")
            if (not isinstance(book, dict) or book.get("session_fingerprint") != generation
                    or book.get("raw_depth_sha256") != _sha(proof["payload"])):
                raise IOLRestError("IOL_FI_BOOK_RECEIPT_UNVERIFIED")
            native_books.append(book)
        return proof["payload"], aware_datetime(proof["received_at"])

    def first_sale_book2(self, book1, *, cutoff_at, cutoff_ns):
        if self._pinned_owner is None:
            raise IOLRestError("IOL_GATEWAY_OWNER_NOT_PINNED")
        owner_pid, generation = self._pinned_owner
        if generation != book1["session_fingerprint"]:
            raise ValueError("IOL_FI_BOOK2_SESSION_CHANGED")
        response = self._request({"op": "iol_native_sale_book2", "consumer": "exit_reader",
            "identity": book1["identity"], "session_generation": generation,
            "book1_receipt_sha256": book1["receipt_sha256"], "cutoff_at": cutoff_at.isoformat(),
            "cutoff_monotonic_ns": cutoff_ns}, require_owner_identity=True)
        if ((response.get("owner_pid"), response.get("session_generation")) != (owner_pid, generation)):
            raise SharedFeedUnavailable("IOL_GATEWAY_OWNER_CHANGED")
        if (response.get("schema") != DATA_SCHEMA or response.get("provider") != "IOL_REST"
                or response.get("paper_ready") is not False or response.get("order_route") != "NOT_PRESENT"
                or type(response.get("wire_calls")) is not int or response["wire_calls"] != 0):
            raise ValueError("IOL_FI_BOOK2_GATEWAY_RECEIPT_UNVERIFIED")
        return {"state": response.get("state"), "receipt": response.get("receipt"),
                "provider_calls_by_rpc": response["wire_calls"]}


class IOLGatewayMarketReader:
    """Consumer adapter with zero provider credentials or HTTP client."""

    def __init__(self, client, *, consumer, trade_clock=None, clock=None):
        if consumer not in ROLE_PRIORITIES:
            raise ValueError("IOL_GATEWAY_ROLE_SCOPE_REQUIRED")
        self.client, self.consumer = client, consumer
        self.trade_clock, self.clock = trade_clock, clock

    def prepare_trades(self, identities):
        if self.consumer != "scanner":
            raise IOLRestError("IOL_GATEWAY_ROLE_SCOPE_DENIED")
        return self.client.prepare_trades(identities)

    def observe(self, identity, *, contract=None, priority="DISCOVERY", exit_only=False):
        if exit_only != (self.consumer == "exit_reader"):
            raise IOLRestError("IOL_GATEWAY_ROLE_SCOPE_DENIED")
        capture = partial(self.client.market_read, consumer=self.consumer, priority=priority)
        native_books = [] if identity[1] in FIXED_INCOME_FAMILIES else None
        if native_books is not None:
            capture = partial(capture, native_books=native_books)
        observed = IOLMarketReader(None, capture=capture, trade_clock=self.trade_clock,
                                   clock=self.clock, gateway_evidence=True).observe(
                                       identity, contract=contract, exit_only=exit_only)
        if native_books is None:
            return observed
        if len(native_books) != 1:
            raise IOLRestError("IOL_FI_BOOK_RECEIPT_UNVERIFIED")
        quote = replace(observed.quote, iol_native_rest_book=native_books[0])
        iol_quote_book_fields(quote, identity, consumer=self.consumer)
        return replace(observed, quote=quote)

    @property
    def metrics(self):
        return self.client.owner_status()

    def close(self):
        # Every RPC opens its own bounded socket. There is no provider session
        # on this side of the transport.
        self.client = None


def connect_iol_gateway_reader(database, *, consumer, trade_clock=None):
    """Attach only after the runtime parent registers this PID and birth tick."""
    if consumer not in ROLE_PRIORITIES:
        raise ValueError("IOL_GATEWAY_ROLE_SCOPE_REQUIRED")
    client = IOLGatewayClient(socket_path=iol_gateway_socket_path(database),
                              timeout_seconds=5)
    pid = os.getpid()
    tick = ReadOnlyUnixFeedServer.process_starttime(pid)
    if tick is None:
        raise SharedFeedUnavailable("IOL_GATEWAY_CLIENT_PID_STARTTIME_UNVERIFIED")
    deadline = time.monotonic() + 5
    while True:
        status = client.owner_status()
        registered = (status.get("registered_clients") or {}).get(consumer)
        if registered == {"pid": pid, "proc_starttime": tick}:
            return IOLGatewayMarketReader(client, consumer=consumer, trade_clock=trade_clock)
        if time.monotonic() >= deadline:
            raise SharedFeedUnavailable("IOL_GATEWAY_CLIENT_ROLE_NOT_REGISTERED")
        time.sleep(.2)
