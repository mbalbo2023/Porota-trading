"""Consolidated read-only evidence for PPI, IOL and public official sources.

PPI remains authoritative. IOL and public-source captures are complementary
and can never change a decision or authorize an order.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen

SCHEMA = "rc6-consolidated-source-evidence-v1"
MAX_BYTES = 2_000_000
# These are references only until a documented technical endpoint with
# credentials returns structured records. A3/MAE is separate from MATBA-ROFEX.
SOURCE_URLS = {
    "BYMA": os.getenv("POROTA_BYMA_PUBLIC_DATA_URL", "https://apiportal.byma.com.ar/"),
    "CNV": os.getenv("POROTA_CNV_PUBLIC_DATA_URL", "https://www.cnv.gov.ar/SitioWeb/HechosRelevantes"),
    "MATBA_ROFEX": os.getenv("POROTA_MATBA_ROFEX_PUBLIC_DATA_URL", "https://matbarofex.com.ar/Indices-mtr/documentacion"),
    "A3_MAE": os.getenv("POROTA_A3_MAE_PUBLIC_DATA_URL", "https://marketdata.mae.com.ar/swagger/api-documentacion.html"),
}

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return tuple(str(row.get(k) or "").strip().upper() for k in ("family", "symbol", "market", "term"))

def _num(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None

def _age_status(value: Any, now: datetime | None = None, max_age: int = 120) -> str:
    if not value:
        return "UNKNOWN"
    try:
        observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            return "UNKNOWN"
        age = (now or datetime.now(timezone.utc)) - observed.astimezone(timezone.utc)
        return "FRESH" if 0 <= age.total_seconds() <= max_age else "STALE"
    except (TypeError, ValueError):
        return "UNKNOWN"

def consolidate(ppi_rows: list[dict[str, Any]], iol_rows: list[dict[str, Any]],
                official_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Merge by identity; never fill a primary field with secondary data."""
    secondary = {_key(row): row for row in iol_rows if isinstance(row, dict)}
    official = {_key(row): row for row in (official_rows or []) if isinstance(row, dict)}
    rows = []
    for ppi in ppi_rows:
        if not isinstance(ppi, dict):
            continue
        key = _key(ppi)
        iol = secondary.get(key, {})
        ext = official.get(key, {})
        primary = dict(ppi)
        complement = {
            "last": _num(iol.get("last")),
            "bid": _num(iol.get("bid")),
            "ask": _num(iol.get("ask")),
            "bid_size": _num(iol.get("bid_size")),
            "ask_size": _num(iol.get("ask_size")),
            "variation_pct": _num(iol.get("variation_pct")),
            "cash_volume": _num(iol.get("cash_volume")),
            "provider_observed_at": iol.get("provider_observed_at"),
            "asset_type": iol.get("asset_type"),
            "currency": iol.get("currency"),
            "units_per_lot": iol.get("units_per_lot"),
        }
        compared = {}
        for field in ("last", "bid", "ask", "bid_size", "ask_size", "variation_pct", "cash_volume"):
            pv, sv = _num(primary.get(field)), complement.get(field)
            compared[field] = {
                "primary": pv, "secondary": sv,
                "state": "MATCH" if pv is not None and sv is not None and (
                    pv == sv or (pv and abs(pv - sv) / abs(pv) * 100 <= 2.0)
                ) else "DIVERGENCE" if pv is not None and sv is not None else "NOT_COMPARABLE",
            }
        rows.append({
            "identity": {"family": key[0], "symbol": key[1], "market": key[2], "term": key[3]},
            "ppi_primary": primary,
            "iol_complement": complement,
            "official_complement": ext,
            "comparison": compared,
            "freshness": {
                "PPI": _age_status(primary.get("provider_observed_at") or primary.get("observed_at")),
                "IOL": _age_status(complement.get("provider_observed_at")),
            },
            "decision_effect": "OBSERVE_ONLY",
            "live_decision_authority": False,
            "real_money_authorized": False,
        })
    return {
        "schema": SCHEMA,
        "source_order": "PPI_PRIMARY_IOL_COMPLEMENTARY_OFFICIAL_REFERENCE",
        "collected_at": _now(),
        "rows": rows,
        "counts": {
            "ppi": len(ppi_rows),
            "iol": len(iol_rows),
            "consolidated": len(rows),
            "official": len(official),
        },
        "decision_effect": "OBSERVE_ONLY",
        "real_money_authorized": False,
    }

def parse_public_payload(source: str, url: str, body: bytes, http_status: int = 200) -> dict[str, Any]:
    digest = hashlib.sha256(body).hexdigest()
    text = body.decode("utf-8", errors="replace")
    records: list[dict[str, Any]] = []
    structured = False
    try:
        payload = json.loads(text)
        structured = True
        values = payload if isinstance(payload, list) else payload.get("items", []) if isinstance(payload, dict) else []
        if isinstance(values, list):
            records = [item for item in values if isinstance(item, dict)][:500]
    except json.JSONDecodeError:
        title = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
        return {
            "source": source, "url": url, "status": "REFERENCE_ONLY" if http_status < 400 else "UNAVAILABLE_HTTP",
            "http_status": http_status, "record_count": 0, "structured": False,
            "page_title": html.unescape(title.group(1)).strip() if title else "",
            "digest": digest, "observed_at": _now(),
        }
    return {
        "source": source, "url": url,
        "status": "REACHABLE_STRUCTURED" if http_status < 400 and records else "REFERENCE_ONLY",
        "http_status": http_status, "record_count": len(records), "structured": structured,
        "records": records, "digest": digest, "observed_at": _now(),
    }

def collect_public_sources(urls: dict[str, str] | None = None) -> dict[str, Any]:
    results = []
    for source, url in (urls or SOURCE_URLS).items():
        try:
            request = Request(url, headers={"User-Agent": "Porota-RC6-read-only/1.0", "Accept": "application/json,text/html;q=0.9"})
            with urlopen(request, timeout=float(os.getenv("POROTA_PUBLIC_SOURCE_TIMEOUT", "15"))) as response:
                body = response.read(MAX_BYTES + 1)
                status = int(getattr(response, "status", 200))
            if len(body) > MAX_BYTES:
                raise ValueError("PUBLIC_SOURCE_RESPONSE_TOO_LARGE")
            results.append(parse_public_payload(source, url, body, status))
        except Exception as exc:
            results.append({
                "source": source, "url": url, "status": "UNAVAILABLE", "http_status": None,
                "record_count": 0, "error": f"{type(exc).__name__}:{str(exc)[:160]}",
                "observed_at": _now(),
            })
    return {"schema": SCHEMA, "sources": results, "collected_at": _now(),
            "decision_effect": "OBSERVE_ONLY", "real_money_authorized": False}
