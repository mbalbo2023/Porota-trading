"""Barrera HTTP fail-closed para observacion de mercado PPI Produccion.

Este modulo no conoce ordenes ni cuentas. Solo permite autenticacion y datos
de mercado contra el host productivo exacto. Cualquier ruta no enumerada se
rechaza antes de que requests entregue el paquete al adaptador de red.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from json import JSONDecodeError
from typing import Callable, Optional
from urllib.parse import urlsplit

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


def classify_read_error(error: BaseException) -> str:
    """Etiqueta estable para observabilidad sin almacenar cuerpos sensibles."""
    if session_invalid(error):
        return "PPI_SESSION_INVALID"
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

    def _record(self, method: str, path: str, result: str) -> None:
        if self.audit:
            self.audit(method, path, result)

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
            except ReadOnlyPolicyViolation:
                with guard._lock:
                    guard.calls_blocked += 1
                guard._record(str(method).upper(), _normal_path(url), "BLOCKED")
                raise
            kwargs["allow_redirects"] = False
            kwargs["timeout"] = (guard.connect_timeout, guard.read_timeout)
            guard._record(checked_method, checked_path, "ALLOWED")
            return original_request(session, method, url, **kwargs)

        def guarded_send(adapter, request, **kwargs):
            try:
                checked_method, checked_path = guard.check(request.method, request.url)
            except ReadOnlyPolicyViolation:
                with guard._lock:
                    guard.calls_blocked += 1
                guard._record(
                    str(request.method).upper(), _normal_path(request.url), "BLOCKED"
                )
                raise
            with guard._lock:
                guard.calls_allowed += 1
            kwargs["timeout"] = (guard.connect_timeout, guard.read_timeout)
            return original_send(adapter, request, **kwargs)

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
                 audit: Optional[Callable[[str, str, str], None]] = None):
        if not api_key or not api_secret:
            raise ValueError("Faltan credenciales productivas.")
        self.__api_key = api_key
        self.__api_secret = api_secret
        self.__guard = ReadOnlyTransportGuard(audit=audit).install()
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

    def login_once(self) -> None:
        if self.__authenticated:
            return
        from ppi_client.ppi import PPI

        client = PPI(sandbox=False)
        client.account.login_api(self.__api_key, self.__api_secret)
        self.__client = client
        self.__authenticated = True

    def _market(self):
        if not self.__authenticated or self.__client is None:
            raise RuntimeError("El lector no esta autenticado.")
        return self.__client.marketdata

    def current(self, ticker: str, instrument_type: str, settlement: str):
        return self._market().current(ticker, instrument_type, settlement)

    def book(self, ticker: str, instrument_type: str, settlement: str):
        return self._market().book(ticker, instrument_type, settlement)

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
        return self._market().intraday(ticker, instrument_type, settlement)

    def close(self) -> None:
        self.__guard.restore()