"""Barrera HTTP fail-closed para observacion de mercado PPI Produccion.

Este modulo no conoce ordenes ni cuentas. Solo permite autenticacion y datos
de mercado contra el host productivo exacto. Cualquier ruta no enumerada se
rechaza antes de que requests entregue el paquete al adaptador de red.
"""

from __future__ import annotations

import os
import hashlib
import json
import re
import secrets
import threading
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from json import JSONDecodeError
from typing import Callable, Optional
from urllib.parse import parse_qsl, urlsplit

import requests


PRODUCTION_HOST = "clientapi.portfoliopersonal.com"

_POST_PATHS = {
    "/api/1.0/account/loginapi",
    "/api/1.0/account/refreshtoken",
}

_GET_PATHS = {
    "/api/1.0/configuration/instrumenttypes",
    "/api/1.0/configuration/markets",
    "/api/1.0/configuration/settlements",
    "/api/1.0/configuration/quantitytypes",
    "/api/1.0/configuration/operationterms",
    "/api/1.0/configuration/operationtypes",
    "/api/1.0/configuration/operations",
    "/api/1.0/configuration/holidays",
    "/api/1.0/configuration/islocalholiday",
    "/api/1.0/marketdata/searchinstrument",
    "/api/1.0/marketdata/search",
    "/api/1.0/marketdata/current",
    "/api/1.0/marketdata/book",
    "/api/1.0/marketdata/intraday",
    "/api/1.0/marketdata/bonds/estimate",
}

# A legal read-only URL is not necessarily a budget-authorized provider call.
# The durable budget V1 has exactly these three GET endpoints. In particular,
# SearchInstrument, historical Search, Bonds/Estimate and Configuration
# CANNOT borrow a Current/Book lease: their PPI quota/global-rate contract and
# external Watch usage are not evidenced. Block them *before wire* until a
# reviewed authority/schema covers each additional read explicitly.
_BUDGETED_MARKET_GET_PATHS = frozenset({
    "/api/1.0/marketdata/current",
    "/api/1.0/marketdata/book",
    "/api/1.0/marketdata/intraday",
})

_NATIVE_WIRE_RECEIPT_SCHEMA = "RC6_PPI_NATIVE_WIRE_RECEIPT_V1"
_NATIVE_WIRE_BODY_MAX_BYTES = 1_048_576
_NATIVE_WIRE_RECEIPT_MAX_ATTEMPTS = 8
_NATIVE_WIRE_MAX_JSON_NODES = 20_000
_NATIVE_WIRE_MAX_JSON_DEPTH = 32
_NATIVE_IDENTITY_PART = re.compile(r"[A-Z0-9][A-Z0-9./_-]{0,63}\Z")
_NATIVE_RECEIPT_KEYS = frozenset({
    "schema", "endpoint", "http_method", "consumer", "priority",
    "request_identity_3key", "claimed_five_key", "identity_source",
    "adapter_send_invoked", "adapter_response_completed", "http_status_code",
    "raw_body_sha256", "parsed_body_sha256", "received_at_utc",
    "receive_monotonic_ns", "budget_lease_sha256", "wire_attempt_id",
    "guard_budget_receipt_sha256",
})


def _native_receipt_json(value) -> bytes:
    """Canonical local digest; never represents a broker signature."""
    seen = 0
    todo = [(value, 0)]
    while todo:
        node, depth = todo.pop()
        seen += 1
        if seen > _NATIVE_WIRE_MAX_JSON_NODES or depth > _NATIVE_WIRE_MAX_JSON_DEPTH:
            raise ValueError("PPI_NATIVE_WIRE_JSON_COMPLEXITY_UNBOUNDED")
        if isinstance(node, dict):
            todo.extend((item, depth + 1) for item in node.values())
        elif isinstance(node, (tuple, list)):
            todo.extend((item, depth + 1) for item in node)
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > _NATIVE_WIRE_BODY_MAX_BYTES:
        raise ValueError("PPI_NATIVE_WIRE_JSON_BYTES_UNBOUNDED")
    return encoded


def _native_wire_request_identity(url: str):
    """Extract only the three URL-encoded PPI fields, never persist the URL."""
    fields = {}
    try:
        for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True):
            key = key.casefold()
            if key in {"ticker", "type", "settlement"}:
                if key in fields or not isinstance(value, str):
                    return None
                normalized = value.strip().upper()
                if not _NATIVE_IDENTITY_PART.fullmatch(normalized):
                    return None
                fields[key] = normalized
    except (TypeError, ValueError):
        return None
    if set(fields) != {"ticker", "type", "settlement"}:
        return None
    return [fields["ticker"], fields["type"], fields["settlement"]]


def _unbudgeted_get_reason(method: str, path: str) -> str | None:
    if method != "GET" or path not in _GET_PATHS or path in _BUDGETED_MARKET_GET_PATHS:
        return None
    if path.startswith("/api/1.0/marketdata/"):
        return "PPI_MARKETDATA_ENDPOINT_BUDGET_UNVERIFIED"
    return "PPI_CONFIGURATION_ENDPOINT_BUDGET_UNVERIFIED"

# PPI confirmó oficialmente que cauciones se buscan por cantidad de días y
# ticker MONEDA+días. Estos plazos fueron probados contra producción en modo
# GET/read-only el 2026-09-07. La lista es configurable para ampliar cobertura
# sin volver al filtro inválido "CAUCION" ni cambiar lógica de órdenes.
DEFAULT_CAUCION_DISCOVERY_DAYS = (1, 2, 7, 30, 120)


def caucion_discovery_days(raw: str | None = None) -> tuple[int, ...]:
    raw = os.getenv("PPI_CAUCION_DISCOVERY_DAYS", "") if raw is None else raw
    if not str(raw).strip():
        return DEFAULT_CAUCION_DISCOVERY_DAYS
    days = []
    for part in str(raw).split(","):
        value = int(part.strip())
        if not 1 <= value <= 365:
            raise ValueError("PPI_CAUCION_DISCOVERY_DAYS_OUT_OF_RANGE")
        if value not in days:
            days.append(value)
    if not days or len(days) > 120:
        raise ValueError("PPI_CAUCION_DISCOVERY_DAYS_INVALID_COUNT")
    return tuple(days)


class ReadOnlyPolicyViolation(RuntimeError):
    """La solicitud fue bloqueada localmente; no salio a la red."""


class ProductionAuthResponseError(RuntimeError):
    """Login no JSON con metadatos HTTP enumerados, sin cuerpo ni headers crudos."""


def _login_response_metadata(response, body) -> str:
    """Only fixed labels and the byte count may leave the login wire boundary."""
    try:
        status = response.status_code
        status = status if type(status) is int and 100 <= status <= 599 else "UNKNOWN"
        header = response.headers.get("Content-Type")
        media = header.split(";", 1)[0].strip().lower() if isinstance(header, str) else ""
        media_class = {
            "application/json": "APPLICATION_JSON",
            "application/problem+json": "APPLICATION_PROBLEM_JSON",
            "text/html": "TEXT_HTML",
            "text/plain": "TEXT_PLAIN",
        }.get(media, "OTHER" if header else "ABSENT")
        length = len(body) if isinstance(body, (bytes, bytearray)) else "UNKNOWN"
        return f"HTTP={status}; Content-Type={media_class}; Content-Length={length}"
    except Exception:
        # A diagnostic must never interrupt or loosen the existing login.
        return "HTTP=UNKNOWN; Content-Type=UNKNOWN; Content-Length=UNKNOWN"


def session_invalid(error: BaseException) -> bool:
    """Reconoce autenticación expirada sin depender de una clase privada del SDK."""
    current = error
    for _ in range(6):
        response = getattr(current, "response", None)
        if getattr(response, "status_code", None) in {401, 403}:
            return True
        text = str(current).lower()
        if any(token in text for token in (
            "unauthorized", "not authorized", "no autorizado", "token expired",
            "invalid token", "authentication required", "sesión expirada",
        )):
            return True
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
        if current is None:
            break
    return False


def transient_payload_error(error: BaseException) -> bool:
    """Limita el retry a cuerpos vacíos/incompletos y fallos transitorios HTTP."""
    current = error
    for _ in range(6):
        if isinstance(current, JSONDecodeError):
            return True
        response = getattr(current, "response", None)
        if getattr(response, "status_code", None) in {408, 429, 500, 502, 503, 504}:
            return True
        text = str(current).lower()
        if any(token in text for token in (
            "expecting value: line 1 column 1", "empty response", "empty body",
            "temporarily unavailable", "connection reset", "remote disconnected",
        )):
            return True
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
        if current is None:
            break
    return False


def instrument_not_found(error: BaseException) -> bool:
    """True only for PPI's explicit per-instrument negative capability response."""
    current = error
    for _ in range(6):
        if str(current).strip().casefold() == "instrument not found":
            return True
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
        if current is None:
            break
    return False


def classify_read_error(error: BaseException) -> str:
    """Etiqueta estable para observabilidad sin almacenar cuerpos sensibles."""
    if type(error).__name__ == "BudgetBackpressure":
        return "PPI_BUDGET_BACKPRESSURE"
    if session_invalid(error):
        return "PPI_SESSION_INVALID"
    if instrument_not_found(error):
        return "PPI_INSTRUMENT_NOT_FOUND"
    current = error
    for _ in range(6):
        response = getattr(current, "response", None)
        status = getattr(response, "status_code", None)
        if status:
            return f"PPI_HTTP_{status}"
        if isinstance(current, JSONDecodeError):
            return "PPI_EMPTY_OR_NON_JSON_AFTER_RETRY"
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
        if current is None:
            break
    return "PPI_" + type(error).__name__.upper()


def retry_read(call, *, retries: int = 1, pause=time.sleep, delay: float = 0.35):
    """Reintento acotado para GET idempotente; nunca reintenta autenticación."""
    if retries not in {0, 1, 2}:
        raise ValueError("PPI_READ_RETRIES_OUT_OF_RANGE")
    for attempt in range(retries + 1):
        try:
            return call()
        except Exception as exc:
            if session_invalid(exc) or not transient_payload_error(exc) or attempt >= retries:
                raise
            pause(delay)


def _normal_path(value: str) -> str:
    path = urlsplit(value).path.rstrip("/")
    return path.lower() or "/"


@dataclass
class ReadOnlyTransportGuard:
    """Intercepta Session.request y HTTPAdapter.send con lista blanca cerrada."""

    audit: Optional[Callable[[str, str, str], None]] = None
    connect_timeout: int = 10
    read_timeout: int = 30
    calls_allowed: int = 0
    calls_blocked: int = 0
    login_calls: int = 0
    login_cooldown_seconds: int = 900
    clock: Callable[[], float] = time.monotonic
    _installed: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _original_request: object = None
    _original_send: object = None
    _last_login_at: Optional[float] = None
    budget: object = None
    consumer: str = "UNSCOPED"
    priority: str = "DISCOVERY"
    _scope: threading.local = field(default_factory=threading.local)

    @contextmanager
    def read_scope(self, *, priority=None, identity=None):
        previous = getattr(self._scope, "priority", None)
        previous_identity = getattr(self._scope, "identity", None)
        if identity is not None and (len(identity) != 5 or any(not isinstance(x, str) or not x for x in identity)):
            raise ValueError("PPI_BUDGET_EXACT_IDENTITY_REQUIRED")
        self._scope.priority = priority or self.priority
        self._scope.identity = identity
        try:
            yield
        finally:
            self._scope.priority = previous
            self._scope.identity = previous_identity

    @contextmanager
    def native_wire_receipt_scope(self):
        """Opt-in thread-local witness of *adapter responses*, not SEND quota.

        This hook never changes the default transport, performs no market GET
        itself and is intentionally unavailable to nested or overlapping
        captures on the same thread. Each HTTPAdapter.send retains exactly
        the budget acquire/start/wire_scope/finish sequence it had before.
        """
        if getattr(self._scope, "native_wire_receipts", None) is not None:
            raise ReadOnlyPolicyViolation("PPI_NATIVE_WIRE_RECEIPT_NESTED")
        attempts = []
        self._scope.native_wire_receipts = attempts
        try:
            yield attempts
        finally:
            self._scope.native_wire_receipts = None

    def _record(self, method: str, path: str, result: str) -> None:
        if self.audit:
            self.audit(method, path, result)

    def _read_error(self, code):
        # The SDK can refresh/retry inside one read after HTTP401. Preserve
        # its originating auth/rate-limit diagnosis when that retry meets the
        # already-open circuit. A new _read resets this thread-local value.
        if getattr(self._scope, "read_error_code", None) not in {"PPI_HTTP_429", "PPI_HTTP_401", "PPI_HTTP_403"}:
            self._scope.read_error_code = code

    def check(self, method: str, url: str, *, count_login: bool = False) -> tuple[str, str]:
        parsed = urlsplit(str(url))
        method = str(method or "").upper()
        path = _normal_path(url)

        if parsed.scheme.lower() != "https":
            raise ReadOnlyPolicyViolation("Solo se permite HTTPS.")
        if (parsed.hostname or "").lower() != PRODUCTION_HOST:
            raise ReadOnlyPolicyViolation("Host fuera de la lista blanca productiva.")

        allowed = ((method == "POST" and path in _POST_PATHS) or
                   (method == "GET" and path in _GET_PATHS))
        if not allowed:
            raise ReadOnlyPolicyViolation(
                f"Ruta bloqueada por politica de solo lectura: {method} {path}"
            )

        with self._lock:
            if count_login and path == "/api/1.0/account/loginapi":
                now = self.clock()
                if (self._last_login_at is not None and
                        now - self._last_login_at < self.login_cooldown_seconds):
                    raise ReadOnlyPolicyViolation(
                        "Login adicional bloqueado durante el cooldown."
                    )
                self.login_calls += 1
                self._last_login_at = now
        return method, path

    def install(self) -> "ReadOnlyTransportGuard":
        if self._installed:
            return self

        guard = self
        self._original_request = requests.sessions.Session.request
        self._original_send = requests.adapters.HTTPAdapter.send
        original_request = self._original_request
        original_send = self._original_send

        def guarded_request(session, method, url, **kwargs):
            try:
                checked_method, checked_path = guard.check(method, url, count_login=True)
                if kwargs.get("stream"):
                    raise ReadOnlyPolicyViolation("PPI_STREAMING_READ_NOT_OWNED")
            except ReadOnlyPolicyViolation:
                with guard._lock:
                    guard.calls_blocked += 1
                guard._record(str(method).upper(), _normal_path(url), "BLOCKED")
                raise
            reason = _unbudgeted_get_reason(checked_method, checked_path)
            if reason:
                from rc6_ppi_global_budget import BudgetBackpressure
                guard._read_error(reason)
                with guard._lock:
                    guard.calls_blocked += 1
                guard._record(checked_method, checked_path, "BACKPRESSURE")
                raise BudgetBackpressure(reason)
            kwargs["allow_redirects"] = False
            kwargs["timeout"] = (guard.connect_timeout, guard.read_timeout)
            guard._record(checked_method, checked_path, "ALLOWED")
            return original_request(session, method, url, **kwargs)

        def guarded_send(adapter, request, **kwargs):
            try:
                checked_method, checked_path = guard.check(request.method, request.url)
                if kwargs.get("stream"):
                    raise ReadOnlyPolicyViolation("PPI_STREAMING_READ_NOT_OWNED")
            except ReadOnlyPolicyViolation:
                with guard._lock:
                    guard.calls_blocked += 1
                guard._record(
                    str(request.method).upper(), _normal_path(request.url), "BLOCKED"
                )
                raise
            reason = _unbudgeted_get_reason(checked_method, checked_path)
            if reason:
                from rc6_ppi_global_budget import BudgetBackpressure
                guard._read_error(reason)
                with guard._lock:
                    guard.calls_blocked += 1
                guard._record(checked_method, checked_path, "BACKPRESSURE")
                raise BudgetBackpressure(reason)
            endpoint = checked_path.rsplit("/", 1)[-1] if checked_path.startswith("/api/1.0/marketdata/") else None
            budget = guard.budget if endpoint in {"current", "book", "intraday"} else None
            lease = None
            observed = getattr(guard._scope, "native_wire_receipts", None)
            attempt_id = secrets.token_hex(16) if observed is not None else None
            if checked_method != "POST" or checked_path != "/api/1.0/account/loginapi":
                # Never attribute a later SDK JSON parse failure to an older
                # login response from the same thread.
                guard._scope.login_http_metadata = None
            if endpoint in {"current", "book", "intraday"}:
                from rc6_ppi_global_budget import BudgetBackpressure
                if budget is None:
                    guard._read_error("PPI_SHARED_BUDGET_UNAVAILABLE")
                    guard._record(checked_method, checked_path, "BACKPRESSURE")
                    raise BudgetBackpressure("PPI_SHARED_BUDGET_UNAVAILABLE")
                try:
                    admitted = budget.acquire(endpoint, consumer=guard.consumer,
                        priority=getattr(guard._scope, "priority", None) or guard.priority)
                except BudgetBackpressure as error:
                    guard._read_error(str(error))
                    guard._record(checked_method, checked_path, "BACKPRESSURE")
                    raise
                if not admitted["allowed"]:
                    guard._read_error(admitted["reason"])
                    guard._record(checked_method, checked_path, "BACKPRESSURE")
                    raise BudgetBackpressure(admitted["reason"])
                lease = admitted["lease"]
                if not isinstance(lease, str) or not lease:
                    guard._read_error("PPI_BUDGET_LEASE_UNVERIFIED")
                    guard._record(checked_method, checked_path, "BACKPRESSURE")
                    raise BudgetBackpressure("PPI_BUDGET_LEASE_UNVERIFIED")
            kwargs["timeout"] = (guard.connect_timeout, guard.read_timeout)
            ownership = budget.wire_scope(lease) if budget is not None and hasattr(budget, "wire_scope") else nullcontext()
            # HTTPAdapter.send returns headers. Session.send would consume
            # the body AFTER return and outside the durable wire lease. The
            # native non-streaming read includes body completion and errors.
            with ownership:
                response = None
                try:
                    if budget is not None:
                        budget.start(lease)
                    with guard._lock:
                        guard.calls_allowed += 1
                    response = original_send(adapter, request, **kwargs)
                    if response.status_code >= 400:
                        guard._read_error(f"PPI_HTTP_{response.status_code}")
                    body = response.content
                    # Capture the body-completion clock BEFORE accounting
                    # finalization. A slow finish() must not shift a PIT
                    # receive timestamp across a subsequent COMMIT.
                    body_received_at_utc = datetime.now(timezone.utc).isoformat() if observed is not None else None
                    body_received_monotonic_ns = time.monotonic_ns() if observed is not None else None
                    if checked_method == "POST" and checked_path == "/api/1.0/account/loginapi":
                        guard._scope.login_http_metadata = _login_response_metadata(response, body)
                except Exception as error:
                    status = response.status_code if response is not None else None
                    code = (f"PPI_HTTP_{status}" if status and status >= 400 else
                        str(error) if type(error).__name__ == "BudgetBackpressure" else classify_read_error(error))
                    guard._read_error(code)
                    try:
                        if response is not None:
                            response.close()
                    finally:
                        if budget is not None:
                            budget.finish(lease, status_code=status, error_code=code)
                    raise
                if budget is not None:
                    budget.finish(lease, status_code=response.status_code)
                # A652's http_allowed counts an adapter *attempt*, not an
                # observed HTTP response. This optional witness is recorded
                # only after the actual adapter returned, the full body was
                # consumed and the EXISTING single budget lease finished.
                # No URL/query/headers/body or secret lease is persisted.
                if observed is not None:
                    if len(observed) >= _NATIVE_WIRE_RECEIPT_MAX_ATTEMPTS:
                        observed[:] = [{"state": "OVERFLOW"}]
                    elif endpoint in {"current", "book", "intraday"} and checked_method == "GET":
                        body_sha = None
                        parsed_sha = None
                        if isinstance(body, (bytes, bytearray)) and len(body) <= _NATIVE_WIRE_BODY_MAX_BYTES:
                            body_sha = hashlib.sha256(body).hexdigest()
                            try:
                                parsed_sha = hashlib.sha256(_native_receipt_json(
                                    json.loads(body))).hexdigest()
                            except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
                                pass
                        receipt = {
                            "schema": _NATIVE_WIRE_RECEIPT_SCHEMA,
                            "endpoint": endpoint,
                            "http_method": "GET",
                            "consumer": guard.consumer,
                            "priority": getattr(guard._scope, "priority", None) or guard.priority,
                            "request_identity_3key": _native_wire_request_identity(request.url),
                            "claimed_five_key": list(getattr(guard._scope, "identity", ()) or ()),
                            "identity_source": "CALLER_ONLY_UNVERIFIED",
                            "adapter_send_invoked": True,
                            "adapter_response_completed": True,
                            "http_status_code": response.status_code if type(response.status_code) is int else None,
                            "raw_body_sha256": body_sha,
                            "parsed_body_sha256": parsed_sha,
                            "received_at_utc": body_received_at_utc,
                            "receive_monotonic_ns": body_received_monotonic_ns,
                            "budget_lease_sha256": hashlib.sha256(lease.encode("utf-8")).hexdigest() if isinstance(lease, str) else None,
                            "wire_attempt_id": attempt_id,
                        }
                        receipt["guard_budget_receipt_sha256"] = hashlib.sha256(
                            _native_receipt_json(receipt)).hexdigest()
                        observed.append(receipt)
                return response

        requests.sessions.Session.request = guarded_request
        requests.adapters.HTTPAdapter.send = guarded_send
        self._installed = True
        return self

    def restore(self) -> None:
        if not self._installed:
            return
        requests.sessions.Session.request = self._original_request
        requests.adapters.HTTPAdapter.send = self._original_send
        self._installed = False


class ProductionMarketReader:
    """Fachada sin atributo de ordenes, cuentas, transferencias ni cancelacion."""

    def __init__(self, api_key: str, api_secret: str,
                 audit: Optional[Callable[[str, str, str], None]] = None, *,
                 budget=None, consumer="UNSCOPED", priority="DISCOVERY"):
        if not api_key or not api_secret:
            raise ValueError("Faltan credenciales productivas.")
        self.__api_key = api_key
        self.__api_secret = api_secret
        if budget is None:
            from rc6_ppi_global_budget import budget_from_environment
            budget = budget_from_environment()
        self.__guard = ReadOnlyTransportGuard(audit=audit, budget=budget,
            consumer=consumer, priority=priority).install()
        self.__client = None
        self.__authenticated = False

    @property
    def metrics(self) -> dict:
        return {
            "http_allowed": self.__guard.calls_allowed,
            "http_blocked": self.__guard.calls_blocked,
            "login_calls": self.__guard.login_calls,
            "authenticated": self.__authenticated,
        }

    @property
    def budget_enabled(self):
        return self.__guard.budget is not None

    def observe_exit_round(self, *, elapsed_seconds, deadline_seconds, failures=0):
        """Publish factual EXIT progress through the shared budget authority."""
        observe = getattr(self.__guard.budget, "observe_exit_round", None)
        if observe is None:
            return {"status": "BASELINE_NOT_MEASURED", "lower_suspended": None}
        return observe(elapsed_seconds=elapsed_seconds, deadline_seconds=deadline_seconds, failures=failures)

    def discard_exit_round_scope(self):
        """Reset temporal admission evidence without reading source or sidecar."""
        discard = getattr(self.__guard.budget, "discard_exit_round_scope", None)
        if callable(discard):
            discard()

    @property
    def last_read_error_code(self):
        """Sanitized current-read diagnosis; no response body or global LKG."""
        return getattr(self.__guard._scope, "read_error_code", None)

    def login_once(self) -> None:
        if self.__authenticated:
            return
        from ppi_client.ppi import PPI

        client = PPI(sandbox=False)
        self.__guard._scope.login_http_metadata = None
        non_json_metadata = None
        try:
            client.account.login_api(self.__api_key, self.__api_secret)
        except JSONDecodeError:
            non_json_metadata = self.__guard._scope.login_http_metadata
            if not non_json_metadata:
                raise
        finally:
            self.__guard._scope.login_http_metadata = None
        if non_json_metadata:
            # Raise outside the SDK exception handler: the new error must not
            # retain a context object whose JSONDecodeError.doc is the body.
            raise ProductionAuthResponseError(
                "PPI_PRODUCTION_AUTH_NON_JSON_RESPONSE: " + non_json_metadata
            )
        self.__client = client
        self.__authenticated = True

    def _market(self):
        if not self.__authenticated or self.__client is None:
            raise RuntimeError("El lector no esta autenticado.")
        return self.__client.marketdata

    def current(self, ticker: str, instrument_type: str, settlement: str):
        return self._read("current", ticker, instrument_type, settlement)

    def book(self, ticker: str, instrument_type: str, settlement: str):
        self.__guard._scope.read_error_code = None
        identity = getattr(self.__guard._scope, "identity", None)
        priority = getattr(self.__guard._scope, "priority", None) or self.__guard.priority
        shared = getattr(self.__guard.budget, "coalesced_book", None)
        if callable(shared) and identity is not None and priority in {"EXIT_CRITICAL", "OPENED_CRITICAL"}:
            if (identity[0], identity[1], identity[4]) != (ticker, instrument_type, settlement):
                raise ValueError("PPI_BUDGET_REQUEST_IDENTITY_MISMATCH")
            return shared(identity, lambda: self._read("book", ticker, instrument_type, settlement),
                consumer=self.__guard.consumer, priority=priority)
        return self._read("book", ticker, instrument_type, settlement)

    def read_scope(self, *, priority=None, identity=None):
        return self.__guard.read_scope(priority=priority, identity=identity)

    def capture_guarded_native(self, endpoint: str, five_key, *, priority="HOT"):
        """Return one SDK-native market result bound to ONE observed GET body.

        This is an optional source-witness hook for a future *single* market
        acquisition owner. It never creates a new budget lease on its own:
        `_read` still goes through guarded HTTPAdapter.send once per actual
        attempt. An SDK-fabricated/cached result or ambiguous retries cannot
        impersonate a native HTTP Book/Current/Intraday response.

        It is NOT PPI catalog identity authority, broker-signed evidence,
        realtime session provenance, an order authorization or PAPER_READY.
        """
        if endpoint not in {"current", "book", "intraday"} or type(endpoint) is not str:
            raise ReadOnlyPolicyViolation("PPI_NATIVE_WIRE_RECEIPT_ENDPOINT_DENIED")
        if (not isinstance(five_key, (tuple, list)) or len(five_key) != 5
            or any(not isinstance(item, str) or
                   not _NATIVE_IDENTITY_PART.fullmatch(item.strip().upper())
                   for item in five_key)):
            raise ReadOnlyPolicyViolation("PPI_NATIVE_WIRE_RECEIPT_IDENTITY_INVALID")
        identity = tuple(item.strip().upper() for item in five_key)
        if not isinstance(priority, str) or not priority or len(priority) > 32:
            raise ReadOnlyPolicyViolation("PPI_NATIVE_WIRE_RECEIPT_PRIORITY_INVALID")

        with self.__guard.read_scope(priority=priority, identity=identity):
            with self.__guard.native_wire_receipt_scope() as attempts:
                result = self._read(endpoint, identity[0], identity[1], identity[4])
        if len(attempts) != 1:
            raise ReadOnlyPolicyViolation("PPI_NATIVE_WIRE_RECEIPT_COUNT_UNVERIFIED")
        receipt = attempts[0]
        if (set(receipt) != _NATIVE_RECEIPT_KEYS
            or receipt.get("schema") != _NATIVE_WIRE_RECEIPT_SCHEMA
            or receipt.get("endpoint") != endpoint
            or receipt.get("http_method") != "GET"
            or receipt.get("consumer") != self.__guard.consumer
            or receipt.get("priority") != priority
            or receipt.get("identity_source") != "CALLER_ONLY_UNVERIFIED"
            or receipt.get("http_status_code") != 200
            or receipt.get("adapter_send_invoked") is not True
            or receipt.get("adapter_response_completed") is not True
            or receipt.get("claimed_five_key") != list(identity)
            or receipt.get("request_identity_3key") != [identity[0], identity[1], identity[4]]
            or not isinstance(receipt.get("raw_body_sha256"), str)
            or not isinstance(receipt.get("budget_lease_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", receipt["raw_body_sha256"])
            or not re.fullmatch(r"[0-9a-f]{64}", receipt["budget_lease_sha256"])
            or not isinstance(receipt.get("wire_attempt_id"), str)
            or not re.fullmatch(r"[0-9a-f]{32}", receipt["wire_attempt_id"])
            or not isinstance(receipt.get("receive_monotonic_ns"), int)
            or receipt["receive_monotonic_ns"] <= 0):
            raise ReadOnlyPolicyViolation("PPI_NATIVE_WIRE_RECEIPT_SOURCE_UNVERIFIED")
        try:
            stamp = datetime.fromisoformat(receipt["received_at_utc"].replace("Z", "+00:00"))
            expected_digest = hashlib.sha256(_native_receipt_json({
                key: value for key, value in receipt.items()
                if key != "guard_budget_receipt_sha256"})).hexdigest()
        except (AttributeError, ValueError, TypeError, OverflowError) as error:
            raise ReadOnlyPolicyViolation("PPI_NATIVE_WIRE_RECEIPT_SOURCE_UNVERIFIED") from error
        if (stamp.tzinfo is None or stamp.utcoffset() is None
            or stamp.utcoffset().total_seconds() != 0
            or receipt.get("guard_budget_receipt_sha256") != expected_digest):
            raise ReadOnlyPolicyViolation("PPI_NATIVE_WIRE_RECEIPT_INTEGRITY_INVALID")
        try:
            sdk_digest = hashlib.sha256(_native_receipt_json(result)).hexdigest()
        except (ValueError, TypeError, OverflowError, RecursionError) as error:
            raise ReadOnlyPolicyViolation("PPI_NATIVE_WIRE_RECEIPT_SDK_PAYLOAD_INVALID") from error
        if receipt.get("parsed_body_sha256") != sdk_digest:
            raise ReadOnlyPolicyViolation("PPI_NATIVE_WIRE_RECEIPT_PAYLOAD_MISMATCH")
        return {"native_payload": result, "guard_wire_receipt": receipt}

    def _read(self, endpoint, *args):
        self.__guard._scope.read_error_code = None
        try:
            return getattr(self._market(), endpoint)(*args)
        except Exception as error:
            if self.last_read_error_code is None:
                self.__guard._scope.read_error_code = classify_read_error(error)
            budget = getattr(getattr(self, "_ProductionMarketReader__guard", None), "budget", None)
            # HTTP status errors are counted once at the actual send. Native
            # SDK 200/session and parse errors become sanitized diagnostics.
            if budget is not None and (session_invalid(error) or isinstance(error, JSONDecodeError)):
                budget.report_error(endpoint, classify_read_error(error))
            raise

    def estimate_bonds(self, parameters):
        """Read-only PPI bond valuation; caller must supply every model field."""
        from ppi_client.models.estimate_bonds import EstimateBonds

        if not isinstance(parameters, EstimateBonds):
            raise TypeError("PPI_ESTIMATE_PARAMETERS_REQUIRED")
        return self._market().estimate_bonds(parameters)

    def market_configuration(self):
        """Enums públicos documentados; misma sesión y ninguna cuenta/orden."""
        self._market()  # exige la misma sesión; no intenta otro login
        configuration = self.__client.configuration
        values = {"instrument_types": configuration.get_instrument_types(),
                "markets": configuration.get_markets(),
                "settlements": configuration.get_settlements(),
                "quantity_types": configuration.get_quantity_types(),
                "operation_terms": configuration.get_operation_terms(),
                "operation_types": configuration.get_operation_types(),
                "operations": configuration.get_operations()}
        if any(not isinstance(items,list) or any(not isinstance(item,str) or not item.strip() for item in items)
               for items in values.values()):
            raise ValueError('PPI_CONFIGURATION_INVALID_SHAPE')
        return values

    def _search_cauciones_official(self, market: str = "BYMA"):
        """Expande el alias legado CAUCION al contrato oficial PPI.

        PPI confirmó: Name=cantidad de días y ticker MONEDA+días. Se consultan
        únicamente plazos configurados/probados y se devuelve una lista directa
        compatible con el catálogo existente. No consulta cuentas ni Order/Budget.
        """
        records = []
        seen = set()
        for days in caucion_discovery_days():
            name = str(days)
            for currency in ("PESOS", "DOLAR"):
                ticker = f"{currency}{days}"
                payload = self._market().search_instrument(
                    ticker, name, str(market or "BYMA"), "CAUCIONES"
                )
                if not isinstance(payload, list):
                    raise ValueError("PPI_CAUCION_SEARCH_INVALID_SHAPE")
                for raw in payload:
                    if not isinstance(raw, dict):
                        raise ValueError("PPI_CAUCION_SEARCH_INVALID_ROW")
                    key = (
                        str(raw.get("ticker") or "").upper(),
                        str(raw.get("type") or "").upper(),
                        str(raw.get("market") or "").upper(),
                        str(raw.get("currency") or "").upper(),
                    )
                    if key not in seen:
                        seen.add(key)
                        records.append(raw)
        return records

    def search_instruments(self, ticker: str, instrument_type: str,
                           name: str | None = None, market: str = "BYMA"):
        """Busca un candidato concreto sin consultar saldos ni permisos.

        El SDK productivo exige ``Ticker`` y ``Name``. Para CAUCIONES se conserva
        el alias histórico ``CAUCION`` sólo como señal interna y se transforma
        localmente al contrato oficial ``PESOS{días}``/``DOLAR{días}``,
        ``Name={días}`` antes de emitir GETs.
        """
        ticker = str(ticker or "").strip()
        name = str(name or ticker).strip()
        instrument_type = str(instrument_type or "").strip().upper()
        if instrument_type == "CAUCIONES" and ticker.upper() == "CAUCION" and name.upper() == "CAUCION":
            return self._search_cauciones_official(market)
        if not ticker or not name:
            raise ValueError("PPI requiere Ticker y Name no vacíos para buscar instrumentos.")
        return self._market().search_instrument(
            ticker, name, str(market or "BYMA"), instrument_type
        )

    def history(self, ticker: str, instrument_type: str, settlement: str,
                date_from, date_to):
        return self._market().search(
            ticker, instrument_type, settlement, date_from, date_to
        )

    def intraday(self, ticker: str, instrument_type: str, settlement: str):
        return self._read("intraday", ticker, instrument_type, settlement)

    def close(self) -> None:
        self.__guard.restore()
