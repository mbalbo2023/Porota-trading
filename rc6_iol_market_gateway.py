"""Read-only IOL market RPCs over the existing RC6 local peer transport."""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import partial
from hashlib import sha256
from math import ceil, inf
import os
from pathlib import Path
import re
from secrets import token_hex
from threading import Lock
import time

from bs_instrument_contracts import aware_datetime
from iol_rest_readonly_rc6 import IOLRestError, IOLRestReadOnly, SOURCE, SYMBOL
from iol_shadow_collector_rc6 import (
    COM19031_1800_FAMILIES, CircuitOpenError, RateGovernor, is_late_observation_window,
)
from fe_fixed_income_nominal_contract_rc6 import FAMILIES as FIXED_INCOME_FAMILIES
from rc6_fixed_income_iol_evidence import create_iol_book, iol_quote_book_fields
from rc6_iol_market_data import IOLMarketReader, MARKETS, SPOT, TERMS, iol_trade_stream_key, read_iol_market_payload
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
RECENT_TRADES_FIELDS = {"op", "consumer", "limit", "session_generation"}
REFERENCE_FIELDS = {"op", "consumer", "method", "arguments", "session_generation", "allow_renew"}
REFERENCE_METHODS = {
    "collector": frozenset({"get_asset_info", "get_asset_quote", "get_asset_depth", "get_options_chain",
                            "get_caucion_terms", "get_caucion_rate"}),
    "health": frozenset({"authenticate", "verify_rest"}),
}
REFERENCE_OPTION_TTL = 120.0
REFERENCE_OPTION_LIMIT = 1024
TRADE_COVERAGE_STATES = frozenset({"TRADE_OBSERVED", "IOL_TRADE_SAMPLE_ABSENT", "SUBSCRIPTION_PENDING",
                                   "NOT_READY", "SCOPE_DENIED"})
SALE_BOOK2_FIELDS = {"op", "consumer", "identity", "session_generation", "book1_receipt_sha256",
                     "cutoff_at", "cutoff_monotonic_ns"}
SALE_BOOK_RETENTION = 256
PREPARE_TRADES_LIMIT = MAX_TRADES // 2  # A full batch must fit the 4 KiB IPC request.
RECENT_TRADE_SECONDS = 120
TRADE_POOL_COUNTS = ("catalog_transport_keys", "continuous_desired", "continuous_subscribed",
                     "rotating_desired", "rotating_subscribed", "open_connections")
TRADE_POOL_STATES = frozenset({"WAITING_FOR_SESSION", "WAITING_FOR_SCANNER", "WAITING_FOR_BEARER",
                               "SUBSCRIBING", "SUBSCRIBED", "DEGRADED"})
TRADE_POOL_REASONS = frozenset({"", "IOL_WS_ROTATION_CAPACITY", "IOL_WS_RATE_LIMITED", "IOL_WS_FORBIDDEN",
                                "IOL_WS_AUTH_REJECTED", "IOL_WS_CONNECTION_FAILED",
                                "IOL_WS_SUBSCRIPTION_REJECTED"})
RECENT_TRADE_ROW = {"identity", "state", "trade_at", "received_at"}
BBO_RANKING_FIELDS = {"quote_at", "spread_bps"}
_REASON = re.compile(r"IOL_[A-Z0-9_]{1,100}\Z")
PROFILE_ENV = "POROTA_RC6_MARKET_PROVIDER"


def iol_primary_enabled():
    provider = os.getenv(PROFILE_ENV, "PPI")
    if provider not in {"PPI", "IOL_REST"}:
        raise ValueError("RC6_MARKET_PROVIDER_INVALID")
    return provider == "IOL_REST"


def iol_gateway_socket_path(database):
    # The observer's existing private tmpfs is also shared by its scanner, EXIT and exec jobs.
    # Its socket, lifetime lease and OAuth state must stay off the dashboard's data volume.
    workspace = sha256(os.fsencode(Path(database).resolve())).hexdigest()[:24]
    return Path("/tmp") / f"porota-iol-{workspace}" / "market.sock"


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


def _reference_arguments(role, method, arguments):
    if (type(role) is not str or role not in REFERENCE_METHODS or type(method) is not str
            or method not in REFERENCE_METHODS[role] or not isinstance(arguments, dict)
            or any(type(value) is not str for value in arguments.values())):
        raise IOLRestError("IOL_GATEWAY_REFERENCE_METHOD_DENIED")
    fields = set(arguments)
    if role == "health" and not fields:
        return {}
    repo_fields = {"currency", "caucion_type"}
    if (method == "get_caucion_terms" and fields == repo_fields
            or method == "get_caucion_rate" and fields == repo_fields | {"term_days"}):
        if (arguments["currency"] in ("ARS", "USD") and arguments["caucion_type"] == "colocadora"
                and arguments.get("term_days", "1") in ("1", "2", "3")):
            return dict(arguments)
    if (method == "get_asset_info" and fields == {"symbol", "market"}
            or method in ("get_asset_quote", "get_asset_depth") and fields == {"symbol", "market", "term"}
            or method == "get_options_chain" and fields in ({"symbol"}, {"symbol", "market"})):
        symbol, market = arguments["symbol"].upper(), arguments.get("market", "BCBA").upper()
        canonical = {"symbol": symbol, "market": market}
        if SYMBOL.fullmatch(symbol) and market in MARKETS.values():
            if method not in ("get_asset_quote", "get_asset_depth"):
                return canonical
            term = arguments["term"].upper()
            if term in TERMS.values():
                return {**canonical, "term": term}
    raise IOLRestError("IOL_GATEWAY_REFERENCE_ARGUMENTS_DENIED")


@dataclass(frozen=True)
class _CatalogScope:
    allowed: dict
    reference_allowed: frozenset
    reference_symbols: dict
    stream_keys: dict
    stream_identities: dict
    planned_exit_calls: int
    revision: str
    state: str = "CURRENT"


class IOLMarketGateway(ReadOnlyUnixFeedServer):
    """Keep the REST session and budget in the owner, one endpoint per demand."""

    control_schema = CONTROL_SCHEMA

    def __init__(self, rest, *, allowed, socket_path, supervisor_pid, clock=None, trade_stream=None,
                 opened=(), scheduled_references=False, reference_allowed=None):
        if (not isinstance(rest, IOLRestReadOnly) or not isinstance(rest.governor, RateGovernor)
                or rest.governor.max_wait_seconds is None
                or rest.governor.max_wait_seconds >= NATIVE_RPC_WAIT_SECONDS):
            raise ValueError("IOL_GATEWAY_BOUNDED_WIRE_BUDGET_REQUIRED")
        rest.governor.policy.validate()
        self._rest = rest
        self._scheduled_references = scheduled_references
        self._reference_options = {}
        self._reference_repo_terms = {}
        self._scope = None
        self._scope_lock = Lock()
        self._last_scanner_attempts = {}
        self._last_scanner_books = {}
        self.trade_stream = trade_stream
        self.refresh_scope(allowed, opened=opened, reference_allowed=reference_allowed)
        self._wire = deque(maxlen=rest.governor.policy.max_calls_per_minute * 2)
        self._wire_lock = Lock()
        self._wire_consumer = None
        self._fi_assets = {}
        self._fi_exit_books = deque(maxlen=SALE_BOOK_RETENTION)
        self._fi_exit_books_lock = Lock()
        rest.wire_observer = self._record_wire
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._generation = token_hex(32)
        super().__init__(None, socket_path=socket_path, supervisor_pid=supervisor_pid,
                         native_acquirer=self._capture, owner_status=self._status)

    @property
    def allowed(self):
        return self._scope.allowed

    def refresh_scope(self, allowed, *, opened=(), reference_allowed=None):
        if set(allowed) != set(ROLE_PRIORITIES):
            raise ValueError("IOL_GATEWAY_ROLE_SCOPE_REQUIRED")
        scoped = {role: frozenset(_identity(key) for key in keys) for role, keys in allowed.items()}
        opened = frozenset(_identity(key) for key in opened)
        scoped["exit_reader"] |= opened
        references = scoped["scanner"] if reference_allowed is None else frozenset(map(_identity, reference_allowed))
        planned_exit_calls = len(ROLE_ENDPOINTS["exit_reader"]) * max(1, len(opened))
        previous = self._scope
        if (previous is not None and previous.allowed == scoped
                and previous.reference_allowed == references
                and previous.planned_exit_calls == planned_exit_calls):
            with self._scope_lock:
                if previous.state != "CURRENT":
                    self._scope = replace(previous, state="CURRENT")
                self._sync_trade_catalog()
            return
        by_transport = defaultdict(list)
        for key in scoped["scanner"]:
            try:
                by_transport[iol_trade_stream_key(key)].append(key)
            except IOLRestError:
                continue
        reference_symbols = defaultdict(list)
        for key in references:
            reference_symbols[(key[0], MARKETS[key[2]])].append(key)
        stream_identities = {transport: keys[0] for transport, keys in by_transport.items() if len(keys) == 1}
        stream_keys = {identity: transport for transport, identity in stream_identities.items()}
        revision = _sha({"allowed": {role: sorted(keys) for role, keys in scoped.items()},
                         "references": sorted(references),
                         "planned_exit_calls": planned_exit_calls})
        with self._scope_lock:
            self._scope = _CatalogScope(scoped, references, dict(reference_symbols), stream_keys, stream_identities,
                                        planned_exit_calls, revision)
            if previous is not None and self.trade_stream is not None:
                retired = {key for key, identity in previous.stream_identities.items()
                           if stream_identities.get(key) != identity}
                if retired:
                    self.trade_stream.retire(retired)
                for identity in previous.stream_keys.keys() - stream_keys.keys():
                    self._last_scanner_attempts.pop(identity, None)
                    self._last_scanner_books.pop(identity, None)
            self._sync_trade_catalog()

    def _sync_trade_catalog(self):
        sync = getattr(self.trade_stream, "sync_catalog", None)
        if callable(sync):
            sync(self._scope.stream_identities)

    def note_scope_refresh_failed(self):
        self._scope = replace(self._scope, state="LAST_VERIFIED")

    def _wire_reserves(self, scope):
        capacity = self._rest.governor.policy.max_calls_per_minute
        return min(capacity - 1, scope.planned_exit_calls), int(capacity > 1)

    def _status(self):
        scope = self._scope
        exit_reserve, auth_reserve = self._wire_reserves(scope)
        return {"state": "OPEN", "provider": "IOL_REST", "wire_budget_attached": True,
                "owner_pid": os.getpid(), "session_generation": self._generation,
                "authenticated": self._rest.session_current,
                "trade_stream_configured": self.trade_stream is not None,
                "trade_stream_state": self.trade_stream.state if self.trade_stream else "NOT_CONFIGURED",
                "wire_attempts": self._rest.governor.calls_total,
                "rate_limited_attempts": self._rest.governor.calls_429,
                "catalog_scope_state": scope.state, "catalog_scope_revision": scope.revision,
                "wire_budget": self._rest.governor.snapshot(reserved_exit=exit_reserve,
                                                           reserved_auth=auth_reserve)}

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

    def _trade_pool_metrics(self):
        metrics_reader = getattr(self.trade_stream, "coverage_metrics", None)
        if not callable(metrics_reader):
            return {}
        metrics = metrics_reader(include_keys=False)
        result = {key: metrics[key] for key in TRADE_POOL_COUNTS
                  if type(metrics.get(key)) is int and metrics[key] >= 0}
        for field, allowed in (("pool_state", TRADE_POOL_STATES), ("degradation_reason", TRADE_POOL_REASONS)):
            value = metrics.get(field)
            result[field] = value if type(value) is str and value in allowed else "NO_VERIFICADO"
        return result

    def _reference_permitted(self, peer_pid):
        # The reference job runs inside the observer namespace through the verified scheduler.
        # Reference jobs have no access to supervisor registration or operational market RPCs.
        return (self._scheduled_references and self.process_starttime(peer_pid) is not None
                and self._supervisor_starttime is not None
                and self.process_starttime(self._supervisor_pid) == self._supervisor_starttime)

    def _read_reference(self, method, arguments, *, late):
        if method in REFERENCE_METHODS["health"]:
            self._rest.authenticate()
            if method == "authenticate":
                return {"result": "AUTHENTICATED"}
            if ("GD30", "BONOS", "BYMA", "ARS", "A-24HS") not in self._scope.reference_allowed:
                raise IOLRestError("IOL_GATEWAY_IDENTITY_SCOPE_DENIED")
            asset = self._rest.call("get_asset_info", {"symbol": "GD30", "market": "BCBA"})
            if (asset.get("type"), asset.get("currency"), asset.get("term")) != ("BONOS", "ARS", "T1"):
                raise IOLRestError("IOL_REST_REFERENCE_IDENTITY_UNVERIFIED")
            return {"result": "REFERENCE_GD30_VERIFIED"}
        if method.startswith("get_caucion_"):
            if late:
                raise IOLRestError("IOL_GATEWAY_LATE_REFERENCE_SCOPE_DENIED")
            currency = arguments["currency"]
            if method == "get_caucion_terms":
                terms = self._rest._repo_terms(currency)
                self._reference_repo_terms[currency] = (terms, time.monotonic() + REFERENCE_OPTION_TTL)
                return {"term_days": sorted(terms)}
            terms, expires = self._reference_repo_terms.get(currency, ({}, 0))
            days = int(arguments["term_days"])
            if expires <= time.monotonic() or days not in terms:
                raise IOLRestError("IOL_GATEWAY_REPO_TERM_UNVERIFIED")
            return {"rate": self._rest._repo_quote(currency, days, terms[days])}
        symbol, market = arguments["symbol"], arguments["market"]
        keys = self._scope.reference_symbols.get((symbol, market), ())
        if late:
            keys = tuple(key for key in keys if key[1] in COM19031_1800_FAMILIES
                         and key[2] == "BYMA" and key[4] == "A-24HS")
            if not keys or method == "get_options_chain" or arguments.get("term", "T1") != "T1":
                raise IOLRestError("IOL_GATEWAY_LATE_REFERENCE_SCOPE_DENIED")
        if method in ("get_asset_quote", "get_asset_depth"):
            matches = [key for key in keys if TERMS[key[4]] == arguments["term"]]
            if len(matches) != 1:
                raise IOLRestError("IOL_GATEWAY_IDENTITY_SCOPE_DENIED")
            term, currency = arguments["term"], matches[0][3]
            if method == "get_asset_depth":
                return self._rest._quote_depth(symbol, market, term, currency)
            quote = self._rest._quote(symbol, market, term, include_depth=False)
            if quote.get("currency") != currency:
                raise IOLRestError("IOL_REST_QUOTE_IDENTITY_UNVERIFIED")
            return quote
        if method == "get_asset_info":
            if not keys and self._reference_options.get((symbol, market), 0) <= time.monotonic():
                raise IOLRestError("IOL_GATEWAY_IDENTITY_SCOPE_DENIED")
            return self._rest.call(method, arguments)
        if not any(key[1] == "ACCIONES" and key[2] == "BYMA" for key in keys):
            raise IOLRestError("IOL_GATEWAY_IDENTITY_SCOPE_DENIED")
        chain = self._rest.call(method, arguments)
        expires = time.monotonic() + REFERENCE_OPTION_TTL
        for row in chain["options"][:REFERENCE_OPTION_LIMIT]:
            self._reference_options[(row["symbol"], market)] = expires
        while len(self._reference_options) > REFERENCE_OPTION_LIMIT:
            self._reference_options.pop(next(iter(self._reference_options)))
        return chain

    def _capture(self, *, endpoint, identity, priority, consumer, peer_pid, arguments=None, allow_renew=True):
        reference = arguments is not None
        if reference:
            if not self._reference_permitted(peer_pid):
                return self._response("IOL_GATEWAY_UNREGISTERED_CLIENT")
        else:
            if not self._role_permitted(consumer, peer_pid):
                return self._response("IOL_GATEWAY_UNREGISTERED_CLIENT")
            if identity not in self.allowed[consumer]:
                return self._response("IOL_GATEWAY_IDENTITY_SCOPE_DENIED")
        governor = self._rest.governor
        before = governor.calls_total
        self._wire_consumer = consumer
        exit_reserve, auth_reserve = self._wire_reserves(self._scope)
        if priority == "EXIT_CRITICAL":
            exit_reserve = 0
        self._rest.wire_reserve = exit_reserve + auth_reserve
        self._rest.oauth_wire_reserve = exit_reserve
        original_renew = self._rest.allow_renew
        captured_book_ns = None
        try:
            if reference:
                late = is_late_observation_window(self._clock())
                self._rest.allow_renew = original_renew and allow_renew and not late
                payload = self._read_reference(endpoint, arguments, late=late)
                body = {"consumer": consumer, "method": endpoint, "arguments": arguments,
                        "received_at": aware_datetime(self._clock()).isoformat(), "payload": payload}
                if len(_canonical_bytes(body)) > 120_000:
                    raise IOLRestError("IOL_GATEWAY_REFERENCE_PAYLOAD_OVERSIZE")
                proof = {"provider": SOURCE, "session_generation": self._generation, **body}
                return self._response("OK", wire_calls=governor.calls_total-before,
                                      receipt_sha256=_sha(proof), **body)
            fixed_income = identity[1] in FIXED_INCOME_FAMILIES
            if fixed_income and endpoint == "asset":
                self._fi_assets.pop(consumer, None)
            trade_key = None
            if endpoint == "quote" and self.trade_stream is not None:
                with self._scope_lock:
                    trade_key = self._scope.stream_keys.get(identity)
                    if trade_key is not None:
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
                    evidence, hold = None, "IOL_TRADE_STREAM_NOT_READY"
                    if trade_key is not None and self._scope.stream_keys.get(identity) == trade_key:
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
            if endpoint == "depth":
                captured_book_ns = received_ns
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
            if (consumer == "scanner" and endpoint in ("quote", "depth")
                    and governor.calls_total > before):
                with self._scope_lock:
                    if identity in self._scope.stream_keys:
                        self._last_scanner_attempts[identity] = time.monotonic_ns()
                        if captured_book_ns is not None:
                            self._last_scanner_books[identity] = captured_book_ns
            self._rest.allow_renew = original_renew
            self._rest.wire_reserve = self._rest.oauth_wire_reserve = 0
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

    def _validate_trade_request(self, request, peer_pid, fields):
        if set(request) != fields or request["consumer"] != "scanner":
            raise IOLRestError("IOL_GATEWAY_ROLE_SCOPE_DENIED")
        if peer_pid is None or not self._role_permitted("scanner", peer_pid):
            raise IOLRestError("IOL_GATEWAY_UNREGISTERED_CLIENT")
        if request["session_generation"] != self._generation:
            raise IOLRestError("IOL_GATEWAY_OWNER_CHANGED")

    def _trade_request(self, request, peer_pid):
        self._validate_trade_request(request, peer_pid, SUBSCRIPTION_FIELDS)
        items = request["identities"]
        if not isinstance(items, list) or not 0 < len(items) <= PREPARE_TRADES_LIMIT:
            raise IOLRestError("IOL_GATEWAY_SUBSCRIPTION_LIMIT")
        try:
            return tuple(map(_identity, items))
        except ValueError as exc:
            raise IOLRestError("IOL_GATEWAY_EXACT_IDENTITY_REQUIRED") from exc

    def _prepare_trades(self, identities):
        with self._scope_lock:
            stream_keys = self._scope.stream_keys
            keys = tuple(dict.fromkeys(stream_keys[identity] for identity in identities if identity in stream_keys))
            if not keys:
                return self._response("IOL_GATEWAY_IDENTITY_SCOPE_DENIED")
            if self.trade_stream is None:
                return self._response("IOL_TRADE_STREAM_NOT_READY")
            self.trade_stream.want_many(keys)
        return self._response("OK", scheduled_subscriptions=len(keys))

    def _trade_coverage(self, identities):
        states = {"IOL_TRADE_SUBSCRIPTION_PENDING": "SUBSCRIPTION_PENDING",
                  "IOL_TRADE_SAMPLE_ABSENT": "IOL_TRADE_SAMPLE_ABSENT"}
        observations = []
        scope = self._scope
        for identity in identities:
            key = scope.stream_keys.get(identity)
            row = {"identity": list(identity)}
            if identity not in scope.allowed["scanner"]:
                state = "SCOPE_DENIED"
            elif self.trade_stream is None or key is None:
                state = "NOT_READY"
            else:
                sample, hold = self.trade_stream.observation(key)
                if isinstance(sample, dict) and sample.get("transport_key") == list(key):
                    try:
                        row["trade_at"] = aware_datetime(sample.get("trade_at")).isoformat()
                    except (TypeError, ValueError, OverflowError):
                        pass
                state = "TRADE_OBSERVED" if "trade_at" in row else states.get(hold, "NOT_READY")
            observations.append({**row, "state": state})
        proof = {"provider": SOURCE, "session_generation": self._generation, "observations": observations}
        return self._response("OK", observations=observations, receipt_sha256=_sha(proof))

    def _recent_trades(self, request, peer_pid):
        self._validate_trade_request(request, peer_pid, RECENT_TRADES_FIELDS)
        limit = request["limit"]
        if type(limit) is not int or not 0 < limit <= PREPARE_TRADES_LIMIT:
            raise IOLRestError("IOL_GATEWAY_SUBSCRIPTION_LIMIT")
        recent = getattr(self.trade_stream, "recent_trades", None)
        observations = {}
        with self._scope_lock:
            scope = self._scope
            samples = recent() if callable(recent) else ()
            checked_at = aware_datetime(self._clock())
            for key, sample in samples:
                identity = scope.stream_identities.get(key)
                if identity is None or not isinstance(sample, dict) or sample.get("transport_key") != list(key):
                    continue
                try:
                    trade_at = aware_datetime(sample.get("trade_at")).astimezone(timezone.utc)
                    received_at = aware_datetime(sample.get("received_at")).astimezone(timezone.utc)
                except (ValueError, TypeError, OverflowError):
                    continue
                if (not trade_at <= received_at <= checked_at
                        or (checked_at - trade_at).total_seconds() > RECENT_TRADE_SECONDS):
                    continue
                if identity not in observations or trade_at > observations[identity][0]:
                    observations[identity] = (trade_at, received_at, self._bbo_ranking(sample, checked_at))
            ranking = {
                identity: (self._last_scanner_books.get(identity, 0),
                           self._last_scanner_attempts.get(identity, 0),
                           bbo.get("spread_bps", inf),
                           -trade_at.timestamp(), identity)
                for identity, (trade_at, _, bbo) in observations.items()
            }
        rows = [{"identity": list(identity), "state": "TRADE_OBSERVED",
                 "trade_at": observations[identity][0].isoformat(),
                 "received_at": observations[identity][1].isoformat(), **observations[identity][2]}
                for identity in sorted(observations, key=ranking.get)[:limit]]
        proof = {"provider": SOURCE, "session_generation": self._generation, "limit": limit,
                 "observations": rows}
        return self._response("OK", limit=limit, observations=rows, receipt_sha256=_sha(proof))

    @staticmethod
    def _bbo_ranking(sample, checked_at):
        spread = sample.get("spread_bps")
        if type(spread) not in (int, float) or not 0 <= spread < 20_000:
            return {}
        try:
            quoted = aware_datetime(sample.get("quote_at")).astimezone(timezone.utc)
            received = aware_datetime(sample.get("quote_received_at")).astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return {}
        if quoted <= received <= checked_at and (checked_at - quoted).total_seconds() <= RECENT_TRADE_SECONDS:
            return {"quote_at": quoted.isoformat(), "spread_bps": spread}
        return {}

    def _scheduled_reference(self, request, peer_pid):
        if set(request) != REFERENCE_FIELDS or type(request["allow_renew"]) is not bool:
            return self._response("IOL_GATEWAY_REQUEST_DENIED")
        if not self._reference_permitted(peer_pid):
            return self._response("IOL_GATEWAY_UNREGISTERED_CLIENT")
        if request["session_generation"] != self._generation:
            return self._response("IOL_GATEWAY_OWNER_CHANGED")
        consumer, method = request["consumer"], request["method"]
        try:
            arguments = _reference_arguments(consumer, method, request["arguments"])
        except IOLRestError as exc:
            return self._response(str(exc))
        return self._demand(endpoint=method, identity=None, priority="DISCOVERY", consumer=consumer,
                            peer_pid=peer_pid, arguments=arguments, allow_renew=request["allow_renew"])

    def _demand(self, **request):
        try:
            return self._native_router.demand(**request)
        except RuntimeError:
            return self._response("IOL_GATEWAY_SCHEDULER_HOLD", wire_calls="NO_VERIFICADO")
        except TimeoutError:
            return self._response("IOL_GATEWAY_READ_DEADLINE", wire_calls="NO_VERIFICADO")

    def dispatch(self, request, *, peer_pid=None):
        if not isinstance(request, dict):
            return self._response("IOL_GATEWAY_REQUEST_DENIED")
        operation = request.get("op")
        if operation in ("iol_prepare_trades", "iol_trade_coverage"):
            try:
                identities = self._trade_request(request, peer_pid)
                return (self._prepare_trades if operation == "iol_prepare_trades" else self._trade_coverage)(
                    identities)
            except IOLRestError as exc:
                return self._response(str(exc))
        if operation == "iol_recent_trades":
            try:
                return self._recent_trades(request, peer_pid)
            except IOLRestError as exc:
                return self._response(str(exc))
        if operation == "iol_scheduled_reference":
            return self._scheduled_reference(request, peer_pid)
        if operation == "iol_native_sale_book2":
            return self._first_sale_book2(request, peer_pid)
        if operation in ("register_client", "owner_status"):
            if operation == "register_client" and request.get("consumer") not in ("scanner", "exit_reader"):
                return self._response("IOL_GATEWAY_ROLE_SCOPE_DENIED")
            result = super().dispatch(request, peer_pid=peer_pid)
            if operation == "owner_status" and result.get("provider") == "IOL_REST":
                result["wire_usage"] = self._wire_usage()
                result["trade_coverage"] = self._trade_pool_metrics()
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
        return self._demand(endpoint=endpoint, identity=identity, priority=priority,
                            consumer=role, peer_pid=peer_pid)


class IOLGatewayClient(ReadOnlyUnixFeedClient):
    control_schema = CONTROL_SCHEMA

    def _owner_status_matches_provider(self, response):
        return (response.get("provider") == "IOL_REST" and
                response.get("wire_budget_attached") is True)

    def _scanner_trade_query(self, operation, **fields):
        if self._pinned_owner is None:
            raise IOLRestError("IOL_GATEWAY_OWNER_NOT_PINNED")
        owner_pid, generation = self._pinned_owner
        response = self._request({"op": operation, "consumer": "scanner",
            **fields, "session_generation": generation},
            require_owner_identity=True)
        if (response.get("schema") != DATA_SCHEMA or response.get("provider") != "IOL_REST"
                or type(response.get("wire_calls")) is not int or response["wire_calls"] != 0
                or response.get("paper_ready") is not False
                or response.get("order_route") != "NOT_PRESENT"):
            raise IOLRestError("IOL_GATEWAY_RECEIPT_UNVERIFIED")
        if (response.get("owner_pid"), response.get("session_generation")) != (owner_pid, generation):
            raise SharedFeedUnavailable("IOL_GATEWAY_OWNER_CHANGED")
        if response.get("state") != "OK":
            reason = response.get("state")
            raise IOLRestError(reason if isinstance(reason, str) and _REASON.fullmatch(reason)
                               else "IOL_GATEWAY_RECEIPT_UNVERIFIED")
        return response

    def _request_trade_batch(self, operation, identities):
        if not 0 < len(identities) <= PREPARE_TRADES_LIMIT:
            raise IOLRestError("IOL_GATEWAY_SUBSCRIPTION_LIMIT")
        return self._scanner_trade_query(operation, identities=[list(_identity(key)) for key in identities])

    def prepare_trades(self, identities):
        response = self._request_trade_batch("iol_prepare_trades", identities)
        count = response.get("scheduled_subscriptions")
        if type(count) is not int or not 0 < count <= len(identities):
            raise IOLRestError("IOL_GATEWAY_RECEIPT_UNVERIFIED")
        return count

    def trade_coverage(self, identities):
        identities = tuple(_identity(key) for key in identities)
        response = self._request_trade_batch("iol_trade_coverage", identities)
        rows = response.get("observations")
        try:
            if not isinstance(rows, list) or len(rows) != len(identities):
                raise ValueError
            for row, identity in zip(rows, identities):
                if (not isinstance(row, dict) or row.get("identity") != list(identity)
                        or row.get("state") not in TRADE_COVERAGE_STATES):
                    raise ValueError
                if row["state"] == "TRADE_OBSERVED":
                    if set(row) != {"identity", "state", "trade_at"}:
                        raise ValueError
                    aware_datetime(row["trade_at"])
                elif set(row) != {"identity", "state"}:
                    raise ValueError
            proof = {"provider": SOURCE, "session_generation": self._pinned_owner[1], "observations": rows}
            if response.get("receipt_sha256") != _sha(proof):
                raise ValueError
        except (TypeError, ValueError, OverflowError) as exc:
            raise IOLRestError("IOL_GATEWAY_RECEIPT_UNVERIFIED") from exc
        return rows

    def recent_trades(self, limit=PREPARE_TRADES_LIMIT):
        if type(limit) is not int or not 0 < limit <= PREPARE_TRADES_LIMIT:
            raise IOLRestError("IOL_GATEWAY_SUBSCRIPTION_LIMIT")
        response = self._scanner_trade_query("iol_recent_trades", limit=limit)
        rows = response.get("observations")
        try:
            if (not isinstance(rows, list) or len(rows) > limit or type(response.get("limit")) is not int
                    or response["limit"] != limit):
                raise ValueError
            seen = set()
            for row in rows:
                if (not isinstance(row, dict) or set(row) not in (RECENT_TRADE_ROW,
                                                                  RECENT_TRADE_ROW | BBO_RANKING_FIELDS)
                        or row["state"] != "TRADE_OBSERVED"):
                    raise ValueError
                identity = _identity(row["identity"])
                if identity in seen or aware_datetime(row["trade_at"]) > aware_datetime(row["received_at"]):
                    raise ValueError
                if BBO_RANKING_FIELDS <= row.keys():
                    if type(row["spread_bps"]) not in (int, float) or not 0 <= row["spread_bps"] < 20_000:
                        raise ValueError
                    aware_datetime(row["quote_at"])
                seen.add(identity)
            proof = {"provider": SOURCE, "session_generation": self._pinned_owner[1],
                     "limit": limit, "observations": rows}
            if response.get("receipt_sha256") != _sha(proof):
                raise ValueError
        except (TypeError, ValueError, OverflowError) as exc:
            raise IOLRestError("IOL_GATEWAY_RECEIPT_UNVERIFIED") from exc
        return rows

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

    def trade_coverage(self, identities):
        if self.consumer != "scanner":
            raise IOLRestError("IOL_GATEWAY_ROLE_SCOPE_DENIED")
        return self.client.trade_coverage(identities)

    def recent_trades(self, limit=PREPARE_TRADES_LIMIT):
        if self.consumer != "scanner":
            raise IOLRestError("IOL_GATEWAY_ROLE_SCOPE_DENIED")
        return self.client.recent_trades(limit)

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


class IOLGatewayReferenceClient(IOLGatewayClient):
    """Bounded read-only client for jobs executed inside the observer."""

    source = SOURCE
    uses_wire_budget = True

    def __init__(self, database, *, consumer, allow_renew=True):
        if consumer not in REFERENCE_METHODS or type(allow_renew) is not bool:
            raise ValueError("IOL_GATEWAY_REFERENCE_METHOD_DENIED")
        super().__init__(socket_path=iol_gateway_socket_path(database), timeout_seconds=5)
        self.consumer, self.allow_renew = consumer, allow_renew
        self.calls_total = self.calls_429 = 0

    def call(self, method, arguments):
        if self.consumer == "collector" and method == "get_caucion_rates":
            terms = self._read_reference("get_caucion_terms", arguments)["term_days"]
            rates = []
            for days in terms:
                rate = self._read_reference("get_caucion_rate", {**arguments, "term_days": str(days)})["rate"]
                if rate is not None:
                    rates.append(rate)
            return {"rates": rates}
        payload = self._read_reference(method, arguments)
        if self.consumer == "collector" and method == "get_asset_quote":
            try:
                payload = {**payload, **self._read_reference("get_asset_depth", arguments)}
            except IOLRestError as exc:
                if exc.status_code in (401, 403, 429):
                    raise
        return payload

    def _read_reference(self, method, arguments):
        arguments = _reference_arguments(self.consumer, method, arguments)
        try:
            if self._pinned_owner is None:
                self.owner_status()
            owner_pid, generation = self._pinned_owner
            response = self._request({"op": "iol_scheduled_reference", "consumer": self.consumer,
                "method": method, "arguments": arguments, "session_generation": generation,
                "allow_renew": self.allow_renew}, require_owner_identity=True)
        except SharedFeedUnavailable as exc:
            self.calls_total = self.calls_429 = "NO_VERIFICADO"
            raise IOLRestError("IOL_GATEWAY_IPC_UNAVAILABLE") from exc
        if ((response.get("owner_pid"), response.get("session_generation")) != (owner_pid, generation)
                or response.get("schema") != DATA_SCHEMA or response.get("provider") != SOURCE
                or response.get("paper_ready") is not False or response.get("order_route") != "NOT_PRESENT"):
            self.calls_total = self.calls_429 = "NO_VERIFICADO"
            raise IOLRestError("IOL_GATEWAY_RECEIPT_UNVERIFIED")
        count, status = response.get("wire_calls"), response.get("http_status")
        if type(count) is not int or count < 0:
            self.calls_total = self.calls_429 = "NO_VERIFICADO"
        elif type(self.calls_total) is int:
            self.calls_total += count
            self.calls_429 += status == 429
        if response.get("state") != "OK":
            reason = response.get("state")
            raise IOLRestError(reason if isinstance(reason, str) and _REASON.fullmatch(reason)
                               else "IOL_GATEWAY_RECEIPT_UNVERIFIED",
                               status_code=status if type(status) is int else None,
                               retry_after=response.get("retry_after_seconds"))
        try:
            body = {key: response[key] for key in ("consumer", "method", "arguments", "received_at", "payload")}
            aware_datetime(body["received_at"])
            if ((body["consumer"], body["method"], body["arguments"]) != (self.consumer, method, arguments)
                    or not isinstance(body["payload"], dict) or type(count) is not int or count < 0
                    or response.get("receipt_sha256") != _sha({"provider": SOURCE,
                       "session_generation": generation, **body})):
                raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise IOLRestError("IOL_GATEWAY_RECEIPT_UNVERIFIED") from exc
        return body["payload"]
