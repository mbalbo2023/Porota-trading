"""A3 CEM public read-only client for HF6 v2.

Purpose: official A3 historical/reference ingestion outside the live trading path.
No authentication, order-routing or broker fallback logic exists here.

Official public OpenAPI currently exposes GET-only resources including:
closing-prices, tick-prices, symbols, products, totals, spot-prices,
option-prices/underlying and download endpoints.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import requests

CEM_ORDER_ROUTING_ALLOWED = False
CEM_LIVE_TRADING_GATE_ALLOWED = False
DEFAULT_CEM_BASE_URL = "https://apicem.matbarofex.com.ar"
CLOSING_MAX_RANGE_DAYS = 370
TICK_MAX_RANGE_DAYS = 1
MAX_PAGE_SIZE = 500


class CEMReadOnlyError(RuntimeError):
    pass


@dataclass(frozen=True)
class CEMConfig:
    base_url: str = DEFAULT_CEM_BASE_URL
    timeout_seconds: float = 20.0

    def validate(self) -> None:
        if not self.base_url.startswith("https://"):
            raise CEMReadOnlyError("CEM_BASE_URL_MUST_BE_HTTPS")


def _parse_date(value: str, label: str) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise CEMReadOnlyError(f"CEM_{label}_INVALID") from exc


def _bounded_history_params(*, symbol: str, product: str, segment: str,
                            date_from: str, date_to: str, page: int,
                            page_size: int, max_days: int) -> dict[str, Any]:
    """Prevent accidental market-wide historical scans from the runtime.

    CEM is background/reference only. Every historical request must identify at
    least one market scope and an explicit bounded date interval.
    """
    if not any(str(x or "").strip() for x in (symbol, product, segment)):
        raise CEMReadOnlyError("CEM_HISTORY_SCOPE_REQUIRED")
    if not date_from or not date_to:
        raise CEMReadOnlyError("CEM_HISTORY_DATE_RANGE_REQUIRED")
    start = _parse_date(date_from, "DATE_FROM")
    end = _parse_date(date_to, "DATE_TO")
    if end < start:
        raise CEMReadOnlyError("CEM_HISTORY_DATE_RANGE_REVERSED")
    if (end - start).total_seconds() > max_days * 86400:
        raise CEMReadOnlyError("CEM_HISTORY_DATE_RANGE_TOO_WIDE")
    page = int(page)
    page_size = int(page_size)
    if page < 1 or page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise CEMReadOnlyError("CEM_HISTORY_PAGINATION_INVALID")
    params: dict[str, Any] = {
        "from": date_from,
        "to": date_to,
        "page": page,
        "pageSize": page_size,
    }
    if symbol:
        params["symbol"] = symbol
    if product:
        params["product"] = product
    if segment:
        params["segment"] = segment
    return params


class A3CEMPublicReadOnlyClient:
    """GET-only official A3 CEM client for history/reference evidence."""

    def __init__(self, config: Optional[CEMConfig] = None,
                 session: Optional[requests.Session] = None):
        if CEM_ORDER_ROUTING_ALLOWED or CEM_LIVE_TRADING_GATE_ALLOWED:
            raise CEMReadOnlyError("CEM_READ_ONLY_INVARIANT_BROKEN")
        self.config = config or CEMConfig()
        self.config.validate()
        self.session = session or requests.Session()

    def _url(self, path: str) -> str:
        return f"{self.config.base_url.rstrip('/')}/{path.lstrip('/')}"

    def _get(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        r = self.session.get(
            self._url(path),
            params=params,
            timeout=self.config.timeout_seconds,
            headers={"Accept": "application/json", "User-Agent": "Porota-HF6-CEM/1.0"},
        )
        if r.status_code >= 400:
            raise CEMReadOnlyError(f"CEM_HTTP_{r.status_code}:{path}")
        try:
            return r.json()
        except Exception as exc:
            raise CEMReadOnlyError(f"CEM_NON_JSON:{path}") from exc

    def products(self) -> dict[str, Any]:
        return self._get("api/v1/products")

    def symbols(self) -> dict[str, Any]:
        return self._get("api/v1/symbols")

    def closing_prices(self, *, symbol: str = "", product: str = "",
                       segment: str = "", date_from: str = "", date_to: str = "",
                       page: int = 1, page_size: int = 500) -> dict[str, Any]:
        params = _bounded_history_params(
            symbol=symbol, product=product, segment=segment,
            date_from=date_from, date_to=date_to, page=page,
            page_size=page_size, max_days=CLOSING_MAX_RANGE_DAYS,
        )
        return self._get("api/v1/closing-prices", params)

    def tick_prices(self, *, symbol: str = "", product: str = "",
                    segment: str = "", date_from: str = "", date_to: str = "",
                    page: int = 1, page_size: int = 500) -> dict[str, Any]:
        params = _bounded_history_params(
            symbol=symbol, product=product, segment=segment,
            date_from=date_from, date_to=date_to, page=page,
            page_size=page_size, max_days=TICK_MAX_RANGE_DAYS,
        )
        return self._get("api/v1/tick-prices", params)

    def totals(self, **params: Any) -> dict[str, Any]:
        return self._get("api/v1/totals", params or None)

    def option_underlying_prices(self, **params: Any) -> dict[str, Any]:
        return self._get("api/v1/option-prices/underlying", params or None)


def assert_cem_invariants() -> None:
    if CEM_ORDER_ROUTING_ALLOWED is not False:
        raise AssertionError("CEM_ORDER_ROUTING_ALLOWED must remain false")
    if CEM_LIVE_TRADING_GATE_ALLOWED is not False:
        raise AssertionError("CEM_LIVE_TRADING_GATE_ALLOWED must remain false")
    forbidden = {"send_order", "new_order", "replace_order", "cancel_order"}
    if forbidden & set(dir(A3CEMPublicReadOnlyClient)):
        raise AssertionError("CEM client exposed forbidden order methods")
