"""Fail-closed bridge from sanitized PPI web tables to API discovery candidates.

Web rows may propose names/tickers for later SearchInstrument validation, but this
module NEVER marks an identity as api_discovered, contract_ready or PAPER-ready.
It recognizes only explicit observed headers; it does not infer financial meaning.
"""
from __future__ import annotations

import re
import unicodedata


TICKER_HEADERS = {"TICKER", "ESPECIE", "SIMBOLO", "CODIGO"}
NAME_HEADERS = {"NOMBRE", "DESCRIPCION", "INSTRUMENTO", "FONDO"}
PLACEHOLDERS = {"", "*", "UNKNOWN", "N/A", "NULL", "NONE"}


def _norm_header(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^A-Z0-9]+", "_", text.upper()).strip("_")


def _clean(value: object, limit: int = 220) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def extract_candidates(*, family: str, headers, rows, source_route: str = "") -> list[dict]:
    normalized = [_norm_header(h) for h in (headers or [])]
    ticker_indexes = [i for i,h in enumerate(normalized) if h in TICKER_HEADERS]
    name_indexes = [i for i,h in enumerate(normalized) if h in NAME_HEADERS]
    if not ticker_indexes and not name_indexes:
        return []

    out=[]
    seen=set()
    for row in rows or []:
        if not isinstance(row,(list,tuple)):
            continue
        ticker=""
        name=""
        for i in ticker_indexes:
            if i < len(row) and _clean(row[i]).upper() not in PLACEHOLDERS:
                ticker=_clean(row[i]).upper(); break
        for i in name_indexes:
            if i < len(row) and _clean(row[i]).upper() not in PLACEHOLDERS:
                name=_clean(row[i]); break
        if not ticker and not name:
            continue
        key=(str(family or "").upper(),ticker,name)
        if key in seen:
            continue
        seen.add(key)
        observed={}
        for i,h in enumerate(normalized):
            if h and i < len(row):
                value=_clean(row[i])
                if value:
                    observed[h]=value
        out.append({
            "family": str(family or "").upper(),
            "ticker_candidate": ticker or None,
            "name_candidate": name or None,
            "source_route": str(source_route or ""),
            "observed_columns": observed,
            "web_candidate_only": True,
            "api_discovered": False,
            "contract_ready": False,
            "paper_candidate": False,
        })
    return out


def assert_no_execution_capability() -> None:
    assert not any(name in globals() for name in ("send_order","place_order","cancel_order"))
