"""A3/Primary REST read-only client for HF6 v2.

This module deliberately implements ONLY authentication and read endpoints.
There are no order-create, replace or cancel methods.

It is intended for an isolated sidecar/environment. Do not install its future
WebSocket dependencies into the main Porota/PPI Python environment.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import os
from typing import Any, Optional

import requests

A3_ORDER_ROUTING_ALLOWED = False
DEFAULT_REMARKETS_BASE_URL = "https://api.remarkets.primary.com.ar"
DEFAULT_PRODUCTION_BASE_URL = "https://api.primary.com.ar"


class A3ReadOnlyError(RuntimeError):
    pass


@dataclass(frozen=True)
class A3Config:
    username: str
    password: str
    account: str = ""
    environment: str = "REMARKETS"
    base_url: str = ""
    timeout_seconds: float = 12.0

    @classmethod
    def from_env(cls) -> "A3Config":
        env = str(os.getenv("A3_PRIMARY_ENVIRONMENT", "REMARKETS")).upper().strip()
        base = str(os.getenv("A3_PRIMARY_BASE_URL", "")).strip()
        if not base:
            base = DEFAULT_PRODUCTION_BASE_URL if env == "PRODUCTION" else DEFAULT_REMARKETS_BASE_URL
        return cls(
            username=str(os.getenv("A3_PRIMARY_USER", "")).strip(),
            password=str(os.getenv("A3_PRIMARY_PASSWORD", "")),
            account=str(os.getenv("A3_PRIMARY_ACCOUNT", "")).strip(),
            environment=env,
            base_url=base.rstrip("/"),
            timeout_seconds=float(os.getenv("A3_PRIMARY_TIMEOUT_SECONDS", "12")),
        )

    def validate(self) -> None:
        if not self.username or not self.password:
            raise A3ReadOnlyError("A3_CREDENTIALS_MISSING")
        if self.environment not in {"REMARKETS", "PRODUCTION"}:
            raise A3ReadOnlyError("A3_ENVIRONMENT_INVALID")
        if not self.base_url.startswith("https://"):
            raise A3ReadOnlyError("A3_BASE_URL_MUST_BE_HTTPS")


class A3PrimaryReadOnlyClient:
    """Strict read client. Token lives only in process memory."""

    def __init__(self, config: A3Config, session: Optional[requests.Session] = None):
        if A3_ORDER_ROUTING_ALLOWED:
            raise A3ReadOnlyError("A3_ORDER_ROUTING_INVARIANT_BROKEN")
        config.validate()
        self.config = config
        self.session = session or requests.Session()
        self._token: Optional[str] = None

    def _url(self, path: str) -> str:
        return f"{self.config.base_url}/{path.lstrip('/')}"

    def authenticate(self) -> None:
        response = self.session.post(
            self._url("auth/getToken"),
            headers={
                "X-Username": self.config.username,
                "X-Password": self.config.password,
            },
            timeout=self.config.timeout_seconds,
        )
        if response.status_code >= 400:
            raise A3ReadOnlyError(f"A3_AUTH_HTTP_{response.status_code}")
        token = response.headers.get("X-Auth-Token") or response.headers.get("x-auth-token")
        if not token:
            raise A3ReadOnlyError("A3_AUTH_TOKEN_MISSING")
        # Never log/return/persist the token.
        self._token = token

    def _headers(self) -> dict[str, str]:
        if not self._token:
            self.authenticate()
        return {"X-Auth-Token": str(self._token)}

    def _get(self, path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        # Central gate: all data calls in this client are GET-only.
        response = self.session.get(
            self._url(path),
            headers=self._headers(),
            params=params,
            timeout=self.config.timeout_seconds,
        )
        if response.status_code >= 400:
            raise A3ReadOnlyError(f"A3_READ_HTTP_{response.status_code}:{path}")
        try:
            payload = response.json()
        except Exception as exc:
            raise A3ReadOnlyError(f"A3_READ_NON_JSON:{path}") from exc
        if not isinstance(payload, dict):
            raise A3ReadOnlyError(f"A3_READ_INVALID_SHAPE:{path}")
        return payload

    def get_segments(self) -> dict[str, Any]:
        return self._get("rest/segment/all")

    def get_all_instruments(self) -> dict[str, Any]:
        return self._get("rest/instruments/all")

    def get_detailed_instruments(self) -> dict[str, Any]:
        return self._get("rest/instruments/details")

    def get_instrument_details(self, symbol: str, market_id: str = "ROFX") -> dict[str, Any]:
        symbol = str(symbol or "").strip()
        if not symbol:
            raise A3ReadOnlyError("A3_SYMBOL_MISSING")
        return self._get(
            "rest/instruments/detail",
            params={"marketId": str(market_id), "symbol": symbol},
        )

    def get_trade_history(self, symbol: str, trading_date: date | str,
                          market_id: str = "ROFX") -> dict[str, Any]:
        symbol = str(symbol or "").strip()
        if not symbol:
            raise A3ReadOnlyError("A3_SYMBOL_MISSING")
        day = trading_date.isoformat() if isinstance(trading_date, date) else str(trading_date)
        if len(day) != 10:
            raise A3ReadOnlyError("A3_TRADE_DATE_INVALID")
        return self._get(
            "rest/data/getTrades",
            params={"marketId": str(market_id), "symbol": symbol, "date": day},
        )

    def readiness(self) -> dict[str, Any]:
        """Read-only capability smoke. Does not expose payloads or credentials."""
        result = {
            "order_routing_allowed": A3_ORDER_ROUTING_ALLOWED,
            "authenticated": False,
            "segments_readable": False,
            "instruments_readable": False,
        }
        self.authenticate()
        result["authenticated"] = True
        self.get_segments()
        result["segments_readable"] = True
        self.get_all_instruments()
        result["instruments_readable"] = True
        return result


def assert_read_only_contract() -> None:
    if A3_ORDER_ROUTING_ALLOWED is not False:
        raise AssertionError("A3_ORDER_ROUTING_ALLOWED must stay false")
    forbidden = ("send_order", "new_order", "replace_order", "cancel_order")
    members = set(dir(A3PrimaryReadOnlyClient))
    leaked = sorted(set(forbidden) & members)
    if leaked:
        raise AssertionError(f"Forbidden order methods exposed: {leaked}")
