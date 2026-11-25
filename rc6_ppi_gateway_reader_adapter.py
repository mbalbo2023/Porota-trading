"""A678 G2: strictly READ_ONLY adapter for the *inert* shared PPI event feed.

This is deliberately NOT a drop-in active replacement for ProductMarketReader.
The G1 transport exposes only normalized STREAM observations over a local
read-only Unix socket.  It does not expose native authenticated REST Current,
Book, Intraday, catalog, history, configuration or PIT raw Book receipts.

The native reader methods therefore fail CLOSED.  In particular, returning a
reconstructed BOOK dictionary to ``ScannerPrimaryRecorder.capture_raw_book``
would misrepresent a normalized quote as independent native PPI evidence.
Future G2 implementations must add separately reviewed authenticated native
read RPC contracts before any production consumer cutover.

No PPI SDK, requests library, credentials, filesystem socket creation, SQLite
connection or order-writing interface is imported or instantiated here.
"""

from __future__ import annotations

from contextlib import contextmanager
import copy
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable

from bd_ppi_readonly_guard import MARKET_CONFIGURATION_FIELDS
from rc6_ppi_shared_feed import (
    SCHEMA, ReadOnlyUnixFeedClient, ReadOnlyUnixFeedServer,
    SharedFeedUnavailable,
)


_SHA = re.compile(r"[0-9a-f]{64}\Z")
_WORD = re.compile(r"[A-Z0-9][A-Z0-9./_-]{0,63}\Z")
_PRIORITIES = frozenset((
    "DISCOVERY", "WARM", "HOT", "STRATEGY_HOT", "SCALPING_HOT",
    "OPENED_CRITICAL", "EXIT_CRITICAL", "WATCH_CRITICAL"
))
_CONSUMERS = frozenset(("scanner", "exit_reader", "scalping", "watch"))


class GatewayEvidenceRejected(RuntimeError):
    """A source/data-method precondition is missing; no alternate PPI GET."""


_REATTACH_ONLY_CODES = frozenset({
    "PPI_GATEWAY_OWNER_CHANGED", "PPI_GATEWAY_UNAVAILABLE",
    "PPI_GATEWAY_OWNER_UNVERIFIED", "PPI_GATEWAY_PEER_IDENTITY_UNAVAILABLE",
    "PPI_GATEWAY_PEER_IDENTITY_MISMATCH", "PPI_GATEWAY_PROTOCOL_INVALID",
    "PPI_NATIVE_ACQUIRE_ENVELOPE_INVALID", "PPI_AUX_RPC_ENVELOPE_INVALID",
    "PPI_GATEWAY_OWNER_NOT_PINNED", "PPI_NATIVE_HTTP_401", "PPI_NATIVE_HTTP_403",
    "PPI_AUX_HTTP_401", "PPI_AUX_HTTP_403", "PPI_GATEWAY_SESSION_RECOVERY_PENDING",
    "PPI_EXIT_ROUND_OWNER_MISMATCH",
})


def gateway_reader_needs_reattach(error: BaseException) -> bool:
    """Only a lost owner/authorization causes a local reconnect, never login.

    A 429 or an unknown in-flight GET must not trigger immediate provider
    retry. Reattachment only constructs a new AF_UNIX adapter after bounded
    cooldown; the child still owns no SDK/credentials.
    """
    return (isinstance(error, (SharedFeedUnavailable, GatewayEvidenceRejected))
            and str(error).split(":", 1)[0] in _REATTACH_ONLY_CODES)


def gateway_socket_path(database) -> Path:
    """Same private PAPER workspace location for all sibling processes."""
    from cg_paper_workspace import artifact_root
    return artifact_root(database) / "ppi-gateway" / "market.sock"


def local_gateway_owner_custody(status: dict) -> dict:
    """Sanitized local evidence, EXPLICITLY NOT global/session authority.

    Only `ReadOnlyUnixFeedClient.owner_status()` is allowed to supply live
    status in production, after SO_PEERCRED kernel PID comparison. This pure
    validator never substitutes for provider-side login inventory or physical
    secret isolation across rootless observer children and legacy scripts.
    """
    if (not isinstance(status, dict) or status.get("state") != "OPEN"
            or type(status.get("owner_pid")) is not int
            or status["owner_pid"] <= 0
            or not _SHA.fullmatch(str(status.get("session_generation")))
            or status.get("login_invocations_by_owner") != 1
            or status.get("guard_login_wire_attempts") != 1):
        raise SharedFeedUnavailable("PPI_GATEWAY_LOCAL_CUSTODY_UNVERIFIED")
    roles = status.get("registered_clients")
    if not isinstance(roles, dict) or set(roles) - {
            "scanner", "exit_reader", "scalping"}:
        raise SharedFeedUnavailable("PPI_GATEWAY_ROLE_CENSUS_UNVERIFIED")
    for role, data in roles.items():
        if (not isinstance(data, dict) or set(data) != {"pid", "proc_starttime"}
                or ReadOnlyUnixFeedServer.process_starttime(data.get("pid"))
                != data.get("proc_starttime")):
            raise SharedFeedUnavailable("PPI_GATEWAY_ROLE_CENSUS_UNVERIFIED")
    child_pids = {data["pid"] for data in roles.values()}
    distinct_three_children = (
        set(roles) == {"scanner", "exit_reader", "scalping"}
        and len(child_pids) == 3 and status["owner_pid"] not in child_pids)
    router = status.get("native_router")
    exit_wait_ms = router.get("last_exit_queue_wait_ms") if isinstance(router, dict) else None
    return {"schema": "rc6.ppi.local-owner-custody.v1",
            "state": "LOCAL_OWNER_OBSERVED",
            "owner_pid": status["owner_pid"],
            "session_generation": status["session_generation"],
            "login_invocations_by_owner": 1,
            "guard_login_wire_attempts": 1,
            "guard_http_allowed": status.get("guard_http_allowed"),
            "guard_http_blocked": status.get("guard_http_blocked"),
            "registered_roles": tuple(sorted(roles)),
            "role_processes": {role: dict(data) for role, data in roles.items()},
            "three_roles_registered_locally": distinct_three_children,
            "global_single_session_custody": "NO_VERIFICADO",
            "other_ppi_login_processes": "NO_VERIFICADO",
            "credential_isolation": "NO_VERIFICADO",
            "provider_signed_session": False,
            "provider_quota": "UNKNOWN", "paper_ready": False,
            "provider_rate_limit_response_observed": status.get("provider_rate_limit_response_observed"),
            "exit_last_queue_wait_ms": exit_wait_ms if type(exit_wait_ms) is int and exit_wait_ms >= 0 else None,
            "real_order_routes_exposed": False}


def gateway_owner_custody(database) -> dict:
    """Host/child read-only live probe; no SDK, secrets, or role registration."""
    client = ReadOnlyUnixFeedClient(
        socket_path=gateway_socket_path(database), timeout_seconds=2)
    return local_gateway_owner_custody(client.owner_status())


def connect_paper_gateway_reader(database, *, consumer: str):
    """Connect to the existing peer-PID owner, never read PPI credentials."""
    client = ReadOnlyUnixFeedClient(
        socket_path=gateway_socket_path(database), timeout_seconds=5)
    pid = os.getpid()
    starttime = ReadOnlyUnixFeedServer.process_starttime(pid)
    if starttime is None:
        raise SharedFeedUnavailable("PPI_GATEWAY_CLIENT_PID_STARTTIME_UNVERIFIED")
    # The parent may still be registering a newly spawned child. Bounded
    # wait, zero SDK/login/GET; unregistered or changed PID always HOLD.
    deadline = time.monotonic() + 5
    while True:
        status = client.owner_status()  # kernel SO_PEERCRED pins server PID
        registered = (status.get("registered_clients") or {}).get(consumer)
        if registered == {"pid": pid, "proc_starttime": starttime}:
            break
        if time.monotonic() >= deadline:
            raise SharedFeedUnavailable("PPI_GATEWAY_CLIENT_ROLE_NOT_REGISTERED")
        time.sleep(.2)
    if status.get("guard_login_wire_attempts") != 1:
        raise SharedFeedUnavailable("PPI_GATEWAY_SINGLE_LOGIN_WIRE_UNVERIFIED")
    reader = SharedPPIReaderAdapter(
        client=client, consumer=consumer,
        catalog_source_digest=status.get("catalog_source_digest"))
    reader.login_once()  # second identity query must preserve PID/generation
    return reader


def _sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _five_key(value: Any) -> tuple[str, str, str, str, str]:
    if (not isinstance(value, (tuple, list)) or len(value) != 5
        or any(not isinstance(item, str) or not _WORD.fullmatch(item) for item in value)):
        raise ValueError("PPI_GATEWAY_FIVE_KEY_INVALID")
    return tuple(value)


def _utc_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not 1 <= len(value) <= 60:
        raise GatewayEvidenceRejected(f"PPI_GATEWAY_{field}_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GatewayEvidenceRejected(f"PPI_GATEWAY_{field}_INVALID") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GatewayEvidenceRejected(f"PPI_GATEWAY_{field}_NAIVE")
    return parsed.astimezone(timezone.utc)


class SharedPPIReaderAdapter:
    """Observe G1 STREAM evidence; do not forge PPI SDK-native REST reads.

    ``client`` is an injected ``ReadOnlyUnixFeedClient`` or deterministic fake
    with the same read-only ``pull`` method.  Constructor performs no IO.
    A successful poll remains ``OBSERVE_ONLY`` because the G1 event itself
    records ``ppi_catalog_authority=NO_VERIFICADO`` / ``paper_ready=False``.
    """

    def __init__(self, *, client: Any, consumer: str,
                 catalog_source_digest: str,
                 now_utc: Callable[[], datetime] | None = None,
                 max_age_seconds: float = 5, batch_size: int = 32,
                 retain_events: int = 64):
        if consumer not in _CONSUMERS:
            raise ValueError("PPI_GATEWAY_CONSUMER_INVALID")
        if not _SHA.fullmatch(str(catalog_source_digest)):
            raise ValueError("PPI_GATEWAY_CATALOG_DIGEST_INVALID")
        if not callable(getattr(client, "pull", None)):
            raise TypeError("PPI_GATEWAY_READONLY_PULL_REQUIRED")
        if (isinstance(max_age_seconds, bool) or
            not isinstance(max_age_seconds, (int, float)) or
            not 0 < max_age_seconds <= 120):
            raise ValueError("PPI_GATEWAY_MAX_AGE_INVALID")
        if (isinstance(batch_size, bool) or not isinstance(batch_size, int)
            or not 1 <= batch_size <= 128):
            raise ValueError("PPI_GATEWAY_BATCH_INVALID")
        if (isinstance(retain_events, bool) or not isinstance(retain_events, int)
            or not 1 <= retain_events <= 512):
            raise ValueError("PPI_GATEWAY_RETENTION_INVALID")
        self._client = client
        self._consumer = consumer
        self._source_digest = catalog_source_digest
        self._now = now_utc or (lambda: datetime.now(timezone.utc))
        self._max_age = max_age_seconds
        self._batch = batch_size
        self._retain = retain_events
        self._cursor = 0
        self._events: list[dict] = []
        self._state = "NO_VERIFICADO"
        self._last_error = None
        self._closed = False
        self._gateway_pinned = False
        self._owner_identity = None
        self._lock = threading.RLock()
        self._scope = threading.local()

    @property
    def metrics(self) -> dict:
        with self._lock:
            guard_allowed = guard_blocked = 0
            native_backoff = None
            authenticated = self._gateway_pinned
            if self._gateway_pinned:
                status = self._client.owner_status()
                if ((status.get("owner_pid"), status.get("session_generation"))
                        != self._owner_identity):
                    raise SharedFeedUnavailable("PPI_GATEWAY_OWNER_CHANGED")
                authenticated = (status.get("state") == "OPEN"
                                 and status.get("session_recovery_pending") is not True)
                guard_allowed = status.get("guard_http_allowed")
                guard_blocked = status.get("guard_http_blocked")
                native_backoff = status.get("native_backoff_remaining_seconds")
                if (type(guard_allowed) is not int or guard_allowed < 0
                        or type(guard_blocked) is not int or guard_blocked < 0):
                    raise SharedFeedUnavailable("PPI_GATEWAY_GUARD_METRICS_UNVERIFIED")
            return {
                "schema": SCHEMA,
                "consumer": self._consumer,
                "state": self._state,
                "cursor": self._cursor,
                "retained": len(self._events),
                "paper_authority": "NO_VERIFICADO",
                "authenticated": authenticated,
                "authentication_witness": "LOCAL_OWNER_PID_GENERATION_ONLY",
                "budget_enabled": self._gateway_pinned,
                "provider_requests": "UNKNOWN" if self._gateway_pinned else 0,
                "consumer_direct_http": 0,
                "http_allowed": guard_allowed,
                "http_blocked": guard_blocked,
                "native_backoff_remaining_seconds": native_backoff,
            }

    @property
    def budget_enabled(self) -> bool:
        return self._gateway_pinned

    @property
    def last_read_error_code(self):
        return self._last_error

    def gateway_owner_custody(self) -> dict:
        if not isinstance(self._client, ReadOnlyUnixFeedClient):
            raise GatewayEvidenceRejected("PPI_GATEWAY_PEERCRED_NOT_VERIFIED")
        return local_gateway_owner_custody(self._client.owner_status())

    @contextmanager
    def read_scope(self, *, priority: str | None = None, identity=None):
        if priority is not None and priority not in _PRIORITIES:
            raise ValueError("PPI_GATEWAY_PRIORITY_INVALID")
        if identity is None and priority in {"EXIT_CRITICAL", "OPENED_CRITICAL", "WATCH_CRITICAL"}:
            raise ValueError("PPI_GATEWAY_EXACT_IDENTITY_REQUIRED")
        key = _five_key(identity) if identity is not None else None
        prev = getattr(self._scope, "request", None)
        self._scope.request = (key, priority)
        try:
            yield
        finally:
            self._scope.request = prev

    def _native_unavailable(self, method: str, request=None):
        if self._closed:
            self._last_error = "PPI_GATEWAY_ADAPTER_CLOSED"
            raise GatewayEvidenceRejected(self._last_error)
        if request is not None:
            scope = getattr(self._scope, "request", None)
            if scope is None or scope[0] is None:
                self._last_error = "PPI_GATEWAY_EXACT_IDENTITY_REQUIRED"
                raise GatewayEvidenceRejected(self._last_error)
            key = scope[0]
            if tuple(request) != (key[0], key[1], key[4]):
                self._last_error = "PPI_GATEWAY_IDENTITY_MISMATCH"
                raise GatewayEvidenceRejected(self._last_error)
        self._last_error = "PPI_GATEWAY_METHOD_NOT_SUPPORTED"
        raise GatewayEvidenceRejected(self._last_error + ":" + method)

    def login_once(self) -> None:
        """Pin the one authenticated owner; never import SDK or log in here."""
        if self._closed:
            raise GatewayEvidenceRejected("PPI_GATEWAY_ADAPTER_CLOSED")
        if self._gateway_pinned:
            return
        witness = getattr(self._client, "owner_status", None)
        if not callable(witness):
            self._last_error = "PPI_GATEWAY_AUTH_ATTESTATION_MISSING"
            raise GatewayEvidenceRejected(self._last_error)
        try:
            result = witness()
        except SharedFeedUnavailable as exc:
            self._last_error = str(exc)
            raise
        if isinstance(result, dict) and result.get("session_recovery_pending") is True:
            self._last_error = "PPI_GATEWAY_SESSION_RECOVERY_PENDING"
            raise GatewayEvidenceRejected(self._last_error)
        if (not isinstance(result, dict) or result.get("state") != "OPEN"
                or result.get("catalog_source_digest") != self._source_digest
                or type(result.get("owner_pid")) is not int
                or result["owner_pid"] <= 0
                or not _SHA.fullmatch(str(result.get("session_generation")))
                or result.get("login_invocations_by_owner") != 1
                or result.get("guard_login_wire_attempts") != 1):
            self._last_error = "PPI_GATEWAY_AUTH_ATTESTATION_MISSING"
            raise GatewayEvidenceRejected(self._last_error)
        self._owner_identity = (result["owner_pid"], result["session_generation"])
        self._gateway_pinned = True
        self._state = "OWNER_PINNED_OBSERVE_ONLY"

    def _native(self, method: str, ticker: str, instrument_type: str,
                settlement: str, *, include_guard=False):
        if not self._gateway_pinned:
            return self._native_unavailable(method.upper(), (ticker, instrument_type, settlement))
        if self._closed:
            self._last_error = "PPI_GATEWAY_ADAPTER_CLOSED"
            raise GatewayEvidenceRejected(self._last_error)
        scope = getattr(self._scope, "request", None)
        if (scope is None or scope[0] is None
                or scope[1] is None):
            self._last_error = "PPI_GATEWAY_EXACT_IDENTITY_REQUIRED"
            raise GatewayEvidenceRejected(self._last_error)
        key, priority = scope
        if (ticker, instrument_type, settlement) != (key[0], key[1], key[4]):
            self._last_error = "PPI_GATEWAY_IDENTITY_MISMATCH"
            raise GatewayEvidenceRejected(self._last_error)
        try:
            response = self._client.acquire_native(
                self._consumer, method=method, five_key=key, priority=priority)
        except SharedFeedUnavailable as exc:
            self._state = "HOLD"
            self._last_error = str(exc)
            raise
        if (not isinstance(response, dict) or response.get("state") != "OK"
                or not isinstance(response.get("receipt"), dict)
                or "raw_payload" not in response["receipt"]):
            self._last_error = (response.get("state") if isinstance(response, dict)
                                else "PPI_NATIVE_ACQUIRE_RESPONSE_INVALID")
            self._state = "HOLD"
            raise GatewayEvidenceRejected(self._last_error)
        self._last_error = None
        if include_guard:
            if not isinstance(response.get("guard_wire_receipt"), dict):
                self._last_error = "PPI_GATEWAY_GUARD_WITNESS_MISSING"
                raise GatewayEvidenceRejected(self._last_error)
            return copy.deepcopy(response)
        return copy.deepcopy(response["receipt"]["raw_payload"])

    def current(self, ticker: str, instrument_type: str, settlement: str):
        return self._native("current", ticker, instrument_type, settlement)

    def book(self, ticker: str, instrument_type: str, settlement: str):
        return self._native("book", ticker, instrument_type, settlement)

    def book_with_native_receipt(self, ticker: str, instrument_type: str, settlement: str):
        """Same guarded GET, preserving the REST Book1 receipt for FI exits."""
        response = self._native("book", ticker, instrument_type, settlement, include_guard=True)
        return response["receipt"]

    def intraday(self, ticker: str, instrument_type: str, settlement: str):
        return self._native("intraday", ticker, instrument_type, settlement)

    def capture_guarded_native(self, endpoint: str, five_key, *, priority="HOT"):
        """P0 original RAW SDK and observed adapter-send witness, never forged."""
        if endpoint not in {"current", "book", "intraday"}:
            raise GatewayEvidenceRejected("PPI_GATEWAY_NATIVE_ENDPOINT_DENIED")
        key = _five_key(five_key)
        with self.read_scope(priority=priority, identity=key):
            response = self._native(endpoint, key[0], key[1], key[4],
                                    include_guard=True)
        data = {"native_payload": copy.deepcopy(response["receipt"]["raw_payload"]),
                "guard_wire_receipt": copy.deepcopy(response["guard_wire_receipt"])}
        proof = response.get("settlement_proof")
        if isinstance(proof, dict):
            data["settlement_proof"] = copy.deepcopy(proof)
        return data

    def _auxiliary(self, method: str, args: dict):
        if not self._gateway_pinned:
            return self._native_unavailable(method.upper())
        if self._closed:
            self._last_error = "PPI_GATEWAY_ADAPTER_CLOSED"
            raise GatewayEvidenceRejected(self._last_error)
        try:
            response = self._client.acquire_auxiliary(
                self._consumer, method=method, args=args)
        except SharedFeedUnavailable as exc:
            self._state = "HOLD"
            self._last_error = str(exc)
            raise
        if not isinstance(response, dict) or response.get("state") != "OK":
            self._state = "HOLD"
            self._last_error = (response.get("state") if isinstance(response, dict)
                                else "PPI_AUX_ACQUIRE_RESPONSE_INVALID")
            raise GatewayEvidenceRejected(self._last_error)
        self._last_error = None
        return copy.deepcopy(response["result"])

    def history(self, ticker: str, instrument_type: str, settlement: str, date_from, date_to):
        return self._auxiliary("history", {"ticker": ticker,
            "instrument_type": instrument_type, "settlement": settlement,
            "date_from": date_from.isoformat() if isinstance(date_from, date) else date_from,
            "date_to": date_to.isoformat() if isinstance(date_to, date) else date_to})

    def search_instruments(self, ticker: str, instrument_type: str,
                           name: str | None = None, market: str = "BYMA"):
        if (str(ticker).strip().upper() == "CAUCION"
                and str(instrument_type).strip().upper() == "CAUCIONES"
                and str(name or ticker).strip().upper() == "CAUCION"):
            # Existing ProductionMarketReader expands this legacy alias into
            # 2 x N SDK GETs *inside one call*. One long RPC would monopolize
            # the owner wire executor and prevent queued EXIT from overtaking.
            # Split into exact one-search-per-RPC calls, so EXIT can run
            # between them; this still uses the same owner SDK and no fallback.
            from bd_ppi_readonly_guard import caucion_discovery_days
            from rc6_cauciones_contract import search_evidence_key

            records, seen = [], set()
            for days in caucion_discovery_days():
                for currency in ("PESOS", "DOLAR"):
                    result = self._auxiliary("search_instruments", {
                        "ticker": f"{currency}{days}",
                        "instrument_type": "CAUCIONES", "name": str(days),
                        "market": market})
                    if not isinstance(result, list):
                        raise GatewayEvidenceRejected("PPI_AUX_CAUCION_SEARCH_SHAPE_INVALID")
                    for row in result:
                        if not isinstance(row, dict):
                            raise GatewayEvidenceRejected("PPI_AUX_CAUCION_SEARCH_ROW_INVALID")
                        key = search_evidence_key(row)
                        if key not in seen:
                            seen.add(key)
                            records.append(row)
            return records
        return self._auxiliary("search_instruments", {
            "ticker": ticker, "instrument_type": instrument_type,
            "name": name or ticker, "market": market})

    def market_configuration(self):
        # Each field is a separate router demand so ready EXIT work can
        # overtake between the SDK's configuration GETs.
        return {field: self._auxiliary("market_configuration", {"field": field})
                for field in MARKET_CONFIGURATION_FIELDS}

    def estimate_bonds(self, parameters):
        return self._native_unavailable("ESTIMATE_BONDS")

    def observe_exit_round(self, *, elapsed_seconds, deadline_seconds, failures=0):
        if self._consumer != "exit_reader" or not self._gateway_pinned:
            raise GatewayEvidenceRejected("PPI_EXIT_ROUND_OWNER_NOT_PINNED")
        observe = getattr(self._client, "observe_exit_round", None)
        if not callable(observe):
            raise GatewayEvidenceRejected("PPI_EXIT_ROUND_BUDGET_RPC_UNAVAILABLE")
        result = observe(self._consumer, elapsed_seconds=elapsed_seconds,
                         deadline_seconds=deadline_seconds, failures=failures)
        if (not isinstance(result, dict)
                or result.get("status") not in {
                    "COMPLETE", "DEGRADED", "BASELINE_NOT_MEASURED"}):
            raise GatewayEvidenceRejected("PPI_EXIT_ROUND_VERDICT_UNVERIFIED")
        return copy.deepcopy(result)

    def discard_exit_round_scope(self):
        if self._consumer != "exit_reader" or not self._gateway_pinned:
            raise GatewayEvidenceRejected("PPI_EXIT_ROUND_OWNER_NOT_PINNED")
        discard = getattr(self._client, "discard_exit_round_scope", None)
        if not callable(discard):
            raise GatewayEvidenceRejected("PPI_EXIT_ROUND_BUDGET_RPC_UNAVAILABLE")
        discard(self._consumer)

    def _validate_event(self, event: Any, *, after_cursor: int, producer_cursor: int,
                        now: datetime) -> int:
        if not isinstance(event, dict) or event.get("schema") != SCHEMA:
            raise GatewayEvidenceRejected("PPI_GATEWAY_EVENT_SCHEMA_INVALID")
        if (isinstance(event.get("sequence"), bool) or
            not isinstance(event.get("sequence"), int) or
            not after_cursor < event["sequence"] <= producer_cursor):
            raise GatewayEvidenceRejected("PPI_GATEWAY_EVENT_SEQUENCE_INVALID")
        body = {key: value for key, value in event.items() if key != "event_sha256"}
        if event.get("event_sha256") != _sha256(body):
            raise GatewayEvidenceRejected("PPI_GATEWAY_EVENT_HASH_MISMATCH")
        try:
            _five_key(event.get("ppi_five_key"))
        except ValueError as exc:
            raise GatewayEvidenceRejected("PPI_GATEWAY_FIVE_KEY_INVALID") from exc
        if event.get("ppi_catalog_source_digest") != self._source_digest:
            raise GatewayEvidenceRejected("PPI_GATEWAY_CATALOG_DIGEST_MISMATCH")
        if event.get("type_provenance") not in (
                "NATIVE_EVENT", "UNIQUE_ACTIVE_SUBSCRIPTION_NOT_NATIVE"):
            raise GatewayEvidenceRejected("PPI_GATEWAY_TYPE_PROVENANCE_INVALID")
        fingerprint = event.get("session_fingerprint")
        if fingerprint is not None and not _SHA.fullmatch(str(fingerprint)):
            raise GatewayEvidenceRejected("PPI_GATEWAY_SESSION_FINGERPRINT_INVALID")
        if (event.get("ppi_catalog_authority") != "NO_VERIFICADO" or
            event.get("paper_ready") is not False or
            event.get("order_route") != "NOT_PRESENT"):
            raise GatewayEvidenceRejected("PPI_GATEWAY_PAPER_AUTHORITY_FORGED")
        if event.get("kind") not in ("BOOK", "TRADE") or not isinstance(event.get("market_payload"), dict):
            raise GatewayEvidenceRejected("PPI_GATEWAY_EVENT_KIND_INVALID")
        born = _utc_time(event.get("native_event_time_utc"), "NATIVE_TIME")
        received = _utc_time(event.get("receive_time_utc"), "RECEIVED_TIME")
        if received < born:
            raise GatewayEvidenceRejected("PPI_GATEWAY_RECEIVE_CLOCK_INVERTED")
        if now < received or now < born:
            raise GatewayEvidenceRejected("PPI_GATEWAY_EVENT_FUTURE")
        if (now - received).total_seconds() > self._max_age:
            raise GatewayEvidenceRejected("PPI_GATEWAY_EVENT_STALE")
        if (isinstance(event.get("receive_monotonic_ns"), bool) or
            not isinstance(event.get("receive_monotonic_ns"), int) or
            event["receive_monotonic_ns"] <= 0):
            raise GatewayEvidenceRejected("PPI_GATEWAY_MONOTONIC_INVALID")
        if not _SHA.fullmatch(str(event.get("source_normalized_sha256"))):
            raise GatewayEvidenceRejected("PPI_GATEWAY_SOURCE_DIGEST_INVALID")
        return event["sequence"]

    def poll_observations(self) -> tuple[dict, ...]:
        """Pull normalized observations, never turn them into PAPER readiness.

        An empty filtered batch can advance to ``producer_cursor`` only when
        its cardinality is below ``batch_size``: the producer then scanned all
        retained events.  A full batch must resume from the last returned
        sequence rather than skip unreturned relevant records.
        """
        with self._lock:
            if self._closed:
                self._last_error = "PPI_GATEWAY_ADAPTER_CLOSED"
                raise GatewayEvidenceRejected(self._last_error)
            try:
                packet = self._client.pull(
                    self._consumer, after_sequence=self._cursor, limit=self._batch)
            except SharedFeedUnavailable as exc:
                self._state = "UNAVAILABLE"
                self._last_error = str(exc)
                raise
            if not isinstance(packet, dict) or packet.get("schema") != SCHEMA:
                self._state = "HOLD"
                self._last_error = "PPI_GATEWAY_PACKET_INVALID"
                raise GatewayEvidenceRejected(self._last_error)
            if packet.get("state") == "GAP":
                self._state = "GAP_HOLD"
                self._last_error = "PPI_GATEWAY_GAP_HOLD"
                raise SharedFeedUnavailable(self._last_error)
            if packet.get("state") != "OK":
                self._state = "HOLD"
                self._last_error = "PPI_GATEWAY_PACKET_STATE_INVALID"
                raise GatewayEvidenceRejected(self._last_error)
            cursor = packet.get("producer_cursor")
            items = packet.get("events")
            if (isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < self._cursor
                or not isinstance(items, list) or len(items) > self._batch):
                self._state = "HOLD"
                self._last_error = "PPI_GATEWAY_PACKET_CURSOR_INVALID"
                raise GatewayEvidenceRejected(self._last_error)
            now = self._now()
            if (not isinstance(now, datetime) or now.tzinfo is None or
                now.utcoffset() is None):
                self._state = "HOLD"
                self._last_error = "PPI_GATEWAY_LOCAL_CLOCK_UNVERIFIED"
                raise GatewayEvidenceRejected(self._last_error)
            now = now.astimezone(timezone.utc)
            observed = []
            last = self._cursor
            try:
                for event in items:
                    last = self._validate_event(event, after_cursor=last,
                                                producer_cursor=cursor, now=now)
                    observed.append(copy.deepcopy(event))
            except GatewayEvidenceRejected as exc:
                self._state = "HOLD"
                self._last_error = str(exc)
                raise
            self._cursor = cursor if len(items) < self._batch else last
            self._events = (self._events + observed)[-self._retain:]
            self._state = "OBSERVE_ONLY"
            self._last_error = None
            return tuple(copy.deepcopy(observed))

    def observations(self) -> tuple[dict, ...]:
        with self._lock:
            if self._closed:
                self._last_error = "PPI_GATEWAY_ADAPTER_CLOSED"
                raise GatewayEvidenceRejected(self._last_error)
            now = self._now()
            if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
                self._state = "HOLD"
                self._last_error = "PPI_GATEWAY_LOCAL_CLOCK_UNVERIFIED"
                raise GatewayEvidenceRejected(self._last_error)
            now = now.astimezone(timezone.utc)
            for event in self._events:
                received = _utc_time(event["receive_time_utc"], "RECEIVED_TIME")
                if now < received or (now - received).total_seconds() > self._max_age:
                    self._state = "HOLD"
                    self._last_error = "PPI_GATEWAY_EVENT_STALE"
                    raise GatewayEvidenceRejected(self._last_error)
            return tuple(copy.deepcopy(self._events))

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._state = "CLOSED"
            self._events.clear()
