"""Strict RC6 read-only historical adapter for InvertirOnline (IOL).

This is deliberately isolated from the active observer and from the legacy
``ak_iol_client.py`` module.  It has exactly two network capabilities:

1. POST to an OAuth/token endpoint only, for authentication/refresh.
2. GET to explicitly allow-listed market-data/history routes.

It has no order, estimate, portfolio, account, transfer or mutation route and
never writes Porota databases.  Historical observations returned by this
module are evidence candidates only; canonical history promotion remains
DENIED until settlement identity and RAW/ADJUSTED semantics are proven.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import os
from typing import Any, Iterable
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
DEFAULT_BASE_URL = "https://api.invertironline.com"
TOKEN_PATHS = frozenset({"/api/v2/token", "/token"})
ALLOWED_MARKETS = frozenset({"bcba", "nyse", "nasdaq", "amex"})
PRICE_BASES = frozenset({"RAW", "ADJUSTED"})


class IOLReadOnlyPolicyViolation(RuntimeError):
    """Raised before the network when a route/method is outside the contract."""


@dataclass(frozen=True)
class IOLHistoryIdentity:
    symbol: str
    instrument_type: str
    market: str
    settlement: str | None
    source_term: str | None = None
    settlement_alignment_verified: bool = False

    @property
    def canonical_identity_verified(self) -> bool:
        return bool(
            self.symbol.strip()
            and self.instrument_type.strip()
            and self.market.strip()
            and self.settlement
            and self.settlement_alignment_verified
        )


@dataclass(frozen=True)
class IOLHistoryBar:
    source: str
    symbol: str
    instrument_type: str
    market: str
    settlement: str | None
    date: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    price_basis: str
    source_term: str | None
    observed_at: str
    quality_class: str
    canonical_write_allowed: bool
    canonical_block_reason: str | None


def _decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("INVALID_NUMERIC_HISTORY_FIELD") from exc
    if not result.is_finite():
        raise ValueError("NONFINITE_HISTORY_FIELD")
    return result


def _valid_ohlcv(open_: Decimal, high: Decimal, low: Decimal,
                 close: Decimal, volume: Decimal) -> None:
    if min(open_, high, low, close) <= 0:
        raise ValueError("NONPOSITIVE_OHLC")
    if volume < 0:
        raise ValueError("NEGATIVE_VOLUME")
    if high < max(open_, low, close) or low > min(open_, high, close):
        raise ValueError("INCONSISTENT_OHLC")


def _parse_day(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("INVALID_HISTORY_DATE") from exc


def current_byma_session_is_final(now: datetime | None = None) -> bool:
    """Conservative same-day rule used only for BCBA historical evidence.

    IOL can return a row labelled as today's daily candle while the Argentine
    wheel is still open.  Therefore a same-day bar is never considered final
    until a post-close safety margin has elapsed.  This does NOT determine if
    BYMA traded that day; the scheduler/calendar still controls due-ness.
    """
    ref = now or datetime.now(TZ)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=TZ)
    ref = ref.astimezone(TZ)
    return ref.time() >= time(17, 10)


def canonical_eligibility(identity: IOLHistoryIdentity, price_basis: str,
                          *, current_day_final: bool, bar_day: date,
                          today: date) -> tuple[bool, str | None]:
    basis = str(price_basis or "").upper()
    if basis not in PRICE_BASES:
        return False, "PRICE_BASIS_UNVERIFIED"
    if not identity.canonical_identity_verified:
        return False, "ALIGNMENT_UNVERIFIED"
    if bar_day == today and identity.market.lower() == "bcba" and not current_day_final:
        return False, "INCOMPLETE_CURRENT_SESSION"
    # RC6 multi-source policy still requires selector hardening before IOL may
    # promote itself.  Keep this explicit even after identity proof succeeds.
    return False, "IOL_CANONICAL_WRITE_NOT_AUTHORIZED"


class IOLHistoryReadOnlyClient:
    def __init__(self, *, username: str | None = None, password: str | None = None,
                 base_url: str | None = None, timeout: float = 12.0,
                 session: requests.Session | None = None) -> None:
        self.username = username if username is not None else os.getenv("IOL_USERNAME", "")
        self.password = password if password is not None else os.getenv("IOL_PASSWORD", "")
        self.base_url = (base_url or os.getenv("IOL_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = float(timeout)
        self.session = session or requests.Session()
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: datetime | None = None

    @staticmethod
    def _assert_allowed(method: str, path: str) -> None:
        method = method.upper().strip()
        if method == "POST" and path in TOKEN_PATHS:
            return
        if method != "GET":
            raise IOLReadOnlyPolicyViolation(f"MUTATING_METHOD_BLOCKED:{method}:{path}")
        # Strict GET allow-list: only title quote/history metadata routes. No
        # account, portfolio, operation, transfer or order resources.
        lower = path.lower()
        if not lower.startswith("/api/v2/"):
            raise IOLReadOnlyPolicyViolation(f"GET_ROUTE_NOT_ALLOWLISTED:{path}")
        parts = [p for p in lower.split("/") if p]
        if len(parts) < 5 or parts[0:2] != ["api", "v2"]:
            raise IOLReadOnlyPolicyViolation(f"GET_ROUTE_NOT_ALLOWLISTED:{path}")
        market = parts[2]
        if market not in ALLOWED_MARKETS or parts[3] != "titulos":
            raise IOLReadOnlyPolicyViolation(f"GET_ROUTE_NOT_ALLOWLISTED:{path}")
        # Allowed shape: /api/v2/{market}/Titulos/{symbol}/Cotizacion[/seriehistorica/...]
        if "cotizacion" not in parts[5:]:
            raise IOLReadOnlyPolicyViolation(f"GET_ROUTE_NOT_ALLOWLISTED:{path}")
        if any(term in lower for term in ("/operar", "/operaciones", "/portafolio", "/estadocuenta", "/transfer")):
            raise IOLReadOnlyPolicyViolation(f"GET_ROUTE_NOT_ALLOWLISTED:{path}")

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        self._assert_allowed(method, path)
        return self.session.request(
            method.upper(), self.base_url + path, timeout=self.timeout, **kwargs
        )

    def authenticate(self) -> bool:
        if not self.username or not self.password:
            return False
        payload = {
            "username": self.username,
            "password": self.password,
            "grant_type": "password",
        }
        response = None
        for path in ("/api/v2/token", "/token"):
            response = self._request(
                "POST", path, data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if response.status_code != 404:
                break
        if response is None or response.status_code != 200:
            return False
        data = response.json()
        token = data.get("access_token")
        if not token:
            return False
        self._access_token = str(token)
        self._refresh_token = data.get("refresh_token") or None
        try:
            seconds = max(60, int(data.get("expires_in") or 900))
        except (TypeError, ValueError):
            seconds = 900
        self._expires_at = datetime.now(TZ) + timedelta(seconds=seconds)
        return True

    def _bearer(self) -> dict[str, str] | None:
        if not self._access_token and not self.authenticate():
            return None
        return {"Authorization": f"Bearer {self._access_token}"}

    @staticmethod
    def _history_path(symbol: str, market: str, from_day: date, to_day: date,
                      price_basis: str) -> str:
        market_l = str(market).lower()
        if market_l not in ALLOWED_MARKETS:
            raise ValueError("IOL_MARKET_NOT_ALLOWED")
        basis = str(price_basis).upper()
        if basis not in PRICE_BASES:
            raise ValueError("PRICE_BASIS_UNVERIFIED")
        adjusted = "true" if basis == "ADJUSTED" else "false"
        return (
            f"/api/v2/{market_l}/Titulos/{quote(symbol.strip(), safe='')}/Cotizacion/"
            f"seriehistorica/{from_day.isoformat()}/{to_day.isoformat()}/{adjusted}"
        )

    def get_history_evidence(self, identity: IOLHistoryIdentity, *,
                             from_date: str | date, to_date: str | date,
                             price_basis: str = "RAW",
                             now: datetime | None = None) -> list[IOLHistoryBar]:
        from_day = _parse_day(from_date)
        to_day = _parse_day(to_date)
        if to_day < from_day:
            raise ValueError("INVALID_HISTORY_RANGE")
        headers = self._bearer()
        if not headers:
            raise RuntimeError("IOL_AUTH_UNAVAILABLE")
        path = self._history_path(identity.symbol, identity.market, from_day, to_day, price_basis)
        response = self._request("GET", path, headers=headers)
        if response.status_code != 200:
            raise RuntimeError(f"IOL_HISTORY_HTTP_{response.status_code}")
        payload = response.json()
        rows: Iterable[dict]
        if isinstance(payload, dict):
            rows = payload.get("precios") or payload.get("bars") or []
        elif isinstance(payload, list):
            rows = payload
        else:
            rows = []

        ref = now or datetime.now(TZ)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=TZ)
        ref = ref.astimezone(TZ)
        same_day_final = current_byma_session_is_final(ref)
        out: list[IOLHistoryBar] = []
        for row in rows:
            bar_day = _parse_day(row.get("fecha") or row.get("date") or row.get("time"))
            open_ = _decimal(row.get("apertura", row.get("open")))
            high = _decimal(row.get("maximo", row.get("high")))
            low = _decimal(row.get("minimo", row.get("low")))
            close = _decimal(row.get("ultimoPrecio", row.get("close")))
            volume = _decimal(row.get("volumen", row.get("volume", 0)))
            _valid_ohlcv(open_, high, low, close, volume)
            allowed, reason = canonical_eligibility(
                identity, price_basis, current_day_final=same_day_final,
                bar_day=bar_day, today=ref.date(),
            )
            out.append(IOLHistoryBar(
                source="IOL",
                symbol=identity.symbol,
                instrument_type=identity.instrument_type,
                market=identity.market.upper(),
                settlement=identity.settlement,
                date=bar_day.isoformat(),
                open=open_, high=high, low=low, close=close, volume=volume,
                price_basis=str(price_basis).upper(),
                source_term=identity.source_term,
                observed_at=ref.isoformat(),
                quality_class="FULL_OHLCV",
                canonical_write_allowed=allowed,
                canonical_block_reason=reason,
            ))
        return out


def assert_iol_readonly_invariants() -> None:
    client = IOLHistoryReadOnlyClient(username="x", password="y")
    client._assert_allowed("POST", "/token")
    client._assert_allowed("POST", "/api/v2/token")
    client._assert_allowed("GET", "/api/v2/bcba/Titulos/GGAL/Cotizacion/seriehistorica/2026-09-01/2026-09-04/false")
    for method, path in (
        ("POST", "/api/v2/operar/estimar"),
        ("POST", "/api/v2/operaciones"),
        ("DELETE", "/api/v2/operaciones/1"),
        ("GET", "/api/v2/portafolio/argentina"),
        ("GET", "/api/v2/estadocuenta"),
    ):
        try:
            client._assert_allowed(method, path)
        except IOLReadOnlyPolicyViolation:
            pass
        else:
            raise AssertionError(f"unsafe IOL route allowed: {method} {path}")
