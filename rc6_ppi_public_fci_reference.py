"""Quarantined PPI public FCI catalog discovery, with no broker-side effects.

This accepts *already captured*, explicitly bounded public listing rows.  It
does not navigate PPI, touch a trusted browser/profile, call authenticated
routes, join an internal PPI detail ID to a CAFCI class, or enter PAPER.

PPI's public catalog lists display names/categories and links to an
authenticated detail page; the latter is **not** evidence of an account's
eligibility, order cutoff, exact class terms, NAV, fees or settlement receipt.
The legacy authenticated ``/Cotizaciones/FCIs`` collectors are independent
and deliberately left untouched.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit


SCHEMA = "rc6-ppi-public-fci-reference-v1"
SOURCE = "PPI_PUBLIC_FCI_CATALOG_REFERENCE_ONLY"
SOURCE_URL = "https://www.portfoliopersonal.com/Cotizaciones/SupermercadoFCI"
PAPER_ONLY = True
RUNTIME_ENTRY_AUTHORITY = False
SOURCE_AUTHENTICATED = False
REAL_ROUTES_USED = ()

_LINK = re.compile(r"/Cotizaciones/FCI/([0-9]{1,10})\Z")
_VISIBLE = re.compile(r"[^\x00-\x1f\x7f]{2,180}\Z")
_ROW_FIELDS = frozenset({"name", "category", "detail_url"})


@dataclass(frozen=True)
class PublicFundListing:
    name: str
    category: str
    ppi_detail_id: str


@dataclass(frozen=True)
class PublicFCIReference:
    received_at: str
    rows: tuple[PublicFundListing, ...]
    schema: str = SCHEMA
    source: str = SOURCE
    source_url: str = SOURCE_URL
    status: str = "REFERENCE_ONLY"
    provider_time: None = None
    exact_cafci_class_binding: bool = False
    source_authenticated: bool = False
    runtime_entry_authority: bool = False
    paper_only: bool = True
    real_routes_used: tuple = ()

    def public_receipt(self) -> dict:
        """Allowlisted serializable public metadata; no provider raw payload."""
        return asdict(self)


def _receipt_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("PPI_PUBLIC_FCI_CAPTURE_TIME_INVALID") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("PPI_PUBLIC_FCI_CAPTURE_TIME_INVALID")
    return parsed.astimezone(timezone.utc)


def _detail_id(value: str) -> str:
    if not isinstance(value, str) or len(value) > 160:
        raise ValueError("PPI_PUBLIC_FCI_DETAIL_ROUTE_UNVERIFIED")
    url = urlsplit(value)
    if (url.scheme != "https" or url.netloc != "trading.portfoliopersonal.com"
            or url.query or url.fragment or url.username or url.password):
        raise ValueError("PPI_PUBLIC_FCI_DETAIL_ROUTE_UNVERIFIED")
    match = _LINK.fullmatch(url.path)
    if match is None:
        raise ValueError("PPI_PUBLIC_FCI_DETAIL_ROUTE_UNVERIFIED")
    return match.group(1)


def inspect_public_fci_listing(rows: Sequence[Mapping], *, received_at: str,
                               source_url: str = SOURCE_URL) -> PublicFCIReference:
    """Validate a bounded public display list; never construct trading facts.

    `received_at` records when the caller saw a public page, *not* a NAV or
    broker quote timestamp.  Missing pages, partial pagination and duplicate
    display identities must never be silently promoted to an exact catalog.
    """
    if source_url != SOURCE_URL:
        raise ValueError("PPI_PUBLIC_FCI_SOURCE_URL_UNVERIFIED")
    received = _receipt_time(received_at)
    if (not isinstance(rows, (tuple, list)) or not 1 <= len(rows) <= 100
            or any(not isinstance(row, Mapping) or set(row) != _ROW_FIELDS
                   for row in rows)):
        raise ValueError("PPI_PUBLIC_FCI_LISTING_SHAPE_INVALID")
    normalized = []
    seen_names = set()
    seen_ids = set()
    for row in rows:
        name, category = row["name"], row["category"]
        if (not isinstance(name, str) or not _VISIBLE.fullmatch(name)
                or not isinstance(category, str) or not _VISIBLE.fullmatch(category)
                or name != name.strip() or category != category.strip()):
            raise ValueError("PPI_PUBLIC_FCI_DISPLAY_FIELDS_INVALID")
        ppi_id = _detail_id(row["detail_url"])
        # Repetition across a paginated public list is not an exact-class join.
        if name.casefold() in seen_names or ppi_id in seen_ids:
            raise ValueError("PPI_PUBLIC_FCI_DUPLICATE_DISPLAY_IDENTITY")
        seen_names.add(name.casefold())
        seen_ids.add(ppi_id)
        normalized.append(PublicFundListing(name, category, ppi_id))
    return PublicFCIReference(received_at=received.isoformat(), rows=tuple(normalized))
