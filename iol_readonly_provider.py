"""Narrow read-only adapter for the existing IOL structured API client.

No authentication, network, persistence, cost estimation, account, portfolio, or
operation lifecycle call is made by this wrapper. It exposes one instrument or
panel query per invocation and reports source results without granting readiness.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re
from typing import Any, Callable


SOURCE_CLASS = "IOL_STRUCTURED_API"
_ALLOWED_TOKEN = re.compile(r"^[A-Za-z0-9._-]+$")


class IOLReadOnlyProviderError(ValueError):
    """Invalid request or unavailable allowlisted method."""


@dataclass(frozen=True)
class IOLObservation:
    source_class: str
    operation: str
    observed_at: str
    status: str
    request: dict[str, Any]
    payload: Any

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class IOLReadOnlyProvider:
    """Expose only panel, quote, and one-symbol history queries.

    The injected client must be an existing IOL API client or a test double.
    This adapter intentionally does not provide a generic method dispatcher.
    """

    __slots__ = ("_client", "_clock")

    def __init__(self, client: Any, *, clock: Callable[[], datetime] | None = None):
        if client is None:
            raise IOLReadOnlyProviderError("An IOL client is required")
        self._client = client
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _token(value: object, name: str) -> str:
        token = str(value or "").strip()
        if not token or token in {".", ".."} or not _ALLOWED_TOKEN.fullmatch(token):
            raise IOLReadOnlyProviderError(f"{name} must be one path token")
        return token

    def _timestamp(self) -> str:
        observed = self._clock()
        if not isinstance(observed, datetime) or observed.tzinfo is None or observed.utcoffset() is None:
            raise IOLReadOnlyProviderError("clock must return a timezone-aware datetime")
        return observed.astimezone(timezone.utc).isoformat(timespec="microseconds")

    @staticmethod
    def _observation(
        operation: str,
        request: dict[str, Any],
        payload: Any,
        observed_at: str,
    ) -> IOLObservation:
        status = "SOURCE_RETURNED_DATA" if payload not in (None, [], {}) else "EMPTY_OR_UNAVAILABLE"
        return IOLObservation(
            source_class=SOURCE_CLASS,
            operation=operation,
            observed_at=observed_at,
            status=status,
            request=request,
            payload=payload,
        )

    def get_panel(
        self,
        instrument: str = "acciones",
        panel: str = "lideres",
        country: str = "argentina",
    ) -> IOLObservation:
        request = {
            "instrument": self._token(instrument, "instrument"),
            "panel": self._token(panel, "panel"),
            "country": self._token(country, "country"),
        }
        method = getattr(self._client, "get_panel", None)
        if not callable(method):
            raise IOLReadOnlyProviderError("allowlisted IOL panel method is unavailable")
        observed_at = self._timestamp()
        payload = method(
            request["instrument"],
            request["panel"],
            request["country"],
        )
        return self._observation("panel", request, payload, observed_at)

    def get_quote(self, symbol: str, market: str = "bcba") -> IOLObservation:
        request = {
            "symbol": self._token(symbol, "symbol"),
            "market": self._token(market, "market"),
        }
        method = getattr(self._client, "get_cotizacion", None)
        if not callable(method):
            raise IOLReadOnlyProviderError("allowlisted IOL quote method is unavailable")
        observed_at = self._timestamp()
        payload = method(request["symbol"], request["market"])
        return self._observation("quote", request, payload, observed_at)

    def get_history(
        self,
        symbol: str,
        market: str = "bcba",
        *,
        days: int = 365,
        adjusted: bool = True,
    ) -> IOLObservation:
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 3660:
            raise IOLReadOnlyProviderError("days must be an integer between 1 and 3660")
        if not isinstance(adjusted, bool):
            raise IOLReadOnlyProviderError("adjusted must be a boolean")
        request = {
            "symbol": self._token(symbol, "symbol"),
            "market": self._token(market, "market"),
            "days": days,
            "adjusted": adjusted,
        }
        method = getattr(self._client, "get_serie_historica", None)
        if not callable(method):
            raise IOLReadOnlyProviderError("allowlisted IOL history method is unavailable")
        observed_at = self._timestamp()
        payload = method(
            request["symbol"],
            request["market"],
            dias=request["days"],
            ajustada=request["adjusted"],
        )
        return self._observation("history", request, payload, observed_at)
