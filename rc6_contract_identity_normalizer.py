"""RC6 post-final deterministic Contract-Evidence identity binding helpers.

Pure/source-only module. It never queries PPI, writes databases, sends orders, or
promotes an instrument to PAPER. A web observation may bind only when a
family-specific normalization maps to exactly one ticker already present in the
API-authoritative universe.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable


SUPPORTED_BINDING_FAMILIES = frozenset({"FUTUROS", "CAUCIONES"})


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().upper()


def futures_key(value: object) -> str:
    """Formatting-only key proven against current PPI web/API futures identities."""
    return re.sub(r"\s+", "", _clean(value))


def caucion_candidate(value: object) -> str | None:
    """Return an API-style caucion ticker only for an explicit currency+days row.

    The candidate is not authoritative until it is checked against api_tickers.
    The leading ordinal is deliberately ignored; currency and term are explicit.
    """
    text = _clean(value)
    match = re.fullmatch(r"\d+\s+(PESOS?|D[ÓO]LARES?)\s+(\d+)D", text, flags=re.IGNORECASE)
    if not match:
        return None
    currency, days = match.groups()
    prefix = "PESOS" if currency.upper().startswith("PESO") else "DOLAR"
    return f"{prefix}{int(days)}"


def unique_api_match(*, family: str, web_identity: object, api_tickers: Iterable[object]) -> str | None:
    """Bind only if the observation deterministically maps to one API ticker.

    OPCIONES and every unproven family remain fail-closed by design.
    """
    fam = _clean(family)
    tickers = sorted({_clean(t) for t in api_tickers if _clean(t)})
    if fam == "FUTUROS":
        key = futures_key(web_identity)
        index: dict[str, list[str]] = defaultdict(list)
        for ticker in tickers:
            index[futures_key(ticker)].append(ticker)
        matches = index.get(key, [])
        return matches[0] if len(matches) == 1 else None
    if fam == "CAUCIONES":
        candidate = caucion_candidate(web_identity)
        return candidate if candidate and candidate in set(tickers) else None
    return None


def assert_no_execution_capability() -> None:
    forbidden = {"send_order", "place_order", "cancel_order", "confirm_order", "execute_order"}
    assert not (forbidden & set(globals()))
