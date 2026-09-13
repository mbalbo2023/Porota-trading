#!/usr/bin/env python3
"""Fail-closed HTTP GET transport for authenticated PPI Web reads.

Credentials/cookies are never persisted or logged by this module. A caller may
supply an ephemeral Cookie header in memory. Only HTTPS GET requests to the
explicit PPI host allowlist are accepted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ALLOWED_HOSTS = {
    "www.portfoliopersonal.com",
    "trading.portfoliopersonal.com",
}


@dataclass(frozen=True)
class ReadonlyResponse:
    url: str
    status: int
    content_type: str
    body: bytes


def validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ValueError("PPI Web URL must use HTTPS")
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"host not allowed: {host}")
    if parsed.username or parsed.password:
        raise ValueError("credentials in URL are forbidden")


def sanitize_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    clean: dict[str, str] = {
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.1",
        "User-Agent": "PorotaTrading-RC6-PPIWebReadonly/1.0",
    }
    for key, value in (headers or {}).items():
        k = str(key).strip()
        if k.lower() in {"authorization", "proxy-authorization"}:
            raise ValueError(f"forbidden header: {k}")
        if "\n" in str(value) or "\r" in str(value):
            raise ValueError("header injection rejected")
        clean[k] = str(value)
    return clean


def get(url: str, headers: Mapping[str, str] | None = None, timeout: float = 20.0) -> ReadonlyResponse:
    validate_url(url)
    request = Request(url=url, headers=sanitize_headers(headers), method="GET")
    with urlopen(request, timeout=timeout) as response:  # nosec B310: URL allowlist enforced above
        body = response.read()
        return ReadonlyResponse(
            url=response.geturl(),
            status=int(getattr(response, "status", 200)),
            content_type=str(response.headers.get("Content-Type", "")),
            body=body,
        )


def redacted_headers_for_log(headers: Mapping[str, str] | None) -> dict[str, str]:
    """Safe logging representation: secrets and session material are redacted."""
    result: dict[str, str] = {}
    for key, value in (headers or {}).items():
        if key.lower() in {"cookie", "set-cookie", "authorization", "x-csrf-token", "x-xsrf-token"}:
            result[key] = "<REDACTED>"
        else:
            result[key] = str(value)
    return result
