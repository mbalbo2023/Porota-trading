"""Consolidated read-only evidence for PPI, IOL and public official sources.

PPI remains authoritative. IOL and public-source captures are complementary
and can never change a decision or authorize an order.
"""
from __future__ import annotations

import hashlib
import html
import json
from html.parser import HTMLParser
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
    "BYMA": os.getenv("POROTA_BYMA_PUBLIC_DATA_URL", "https://open.bymadata.com.ar/"),
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


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables=[]; self._table=None; self._row=None; self._cell=None
    def handle_starttag(self, tag, attrs):
        tag=tag.lower()
        if tag=="table" and self._table is None:
            self._table={"rows":[]}; self._row=None; self._cell=None
        elif self._table is not None and tag=="tr":
            self._row=[]; self._table["rows"].append(self._row)
        elif self._table is not None and tag in {"th","td"} and self._row is not None:
            self._cell={"tag":tag,"text":""}; self._row.append(self._cell)
    def handle_data(self, data):
        if self._cell is not None:
            self._cell["text"] += data
    def handle_endtag(self, tag):
        tag=tag.lower()
        if tag in {"th","td"}: self._cell=None
        elif tag=="tr": self._row=None
        elif tag=="table" and self._table is not None:
            self.tables.append(self._table); self._table=None; self._row=None; self._cell=None

def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()

def _parse_html_tables(text: str) -> list[dict[str, Any]]:
    parser = _TableParser()
    parser.feed(text)
    records=[]
    for table in parser.tables:
        rows=[[ _clean_text(cell["text"]) for cell in row if _clean_text(cell["text"]) ] for row in table["rows"]]
        rows=[row for row in rows if row]
        if len(rows)<2: continue
        header=rows[0]
        if len(header)<2: continue
        normalized_header=[_clean_text(x).lower() for x in header]
        for values in rows[1:]:
            if len(values)<2: continue
            item={normalized_header[i]: values[i] for i in range(min(len(normalized_header),len(values)))}
            if any(item.values()):
                records.append(item)
    return records[:500]

def _normalize_public_records(source: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aliases={
        "symbol": ("especie","símbolo","simbolo","ticker","symbol","code"),
        "currency": ("moneda","currency"),
        "bid": ("p. cpra.","p compra","compra","bid","bidprice","bid_price","buyprice"),
        "ask": ("p. vta.","p venta","venta","ask","offerprice","offer_price","sellprice"),
        "last": ("último","ultimo","last","lastprice","last_price","tradeprice","trade_price","price"),
        "variation_pct": ("var.","variación","variacion","variation","variationpercent","variation_pct","changepercent"),
        "volume": ("volumen","volume","tradevolume","trade_volume","quantity"),
        "cash_volume": ("vol. monto","vol monto","cash volume","cashvolume","cash_volume","tradedamount"),
        "vwap": ("vwap",),
        "timestamp": ("hora","timestamp","fecha","date","tradedate","trade_date","marketdatadate","market_data_date"),
        "maturity": ("vto.","vto","vencimiento","maturity"),
        "adjustment": ("ajuste","adjustment"),
        "open_interest": ("interés abierto","interes abierto","open interest"),
    }
    out=[]
    for raw in records:
        normalized={"source":source}
        lowered={_clean_text(k).lower():v for k,v in raw.items()}
        for field,names in aliases.items():
            for name in names:
                if name in lowered and lowered[name] not in ("", "-", "—"):
                    normalized[field]=lowered[name]; break
        if normalized.get("symbol"):
            out.append(normalized)
    return out

def _json_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)][:500]
    if not isinstance(payload, dict):
        return []
    for key in ("items", "data", "records", "results", "content"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)][:500]
        if isinstance(value, dict):
            nested = _json_records(value)
            if nested:
                return nested
    return []

def parse_public_payload(source: str, url: str, body: bytes, http_status: int = 200) -> dict[str, Any]:
    digest = hashlib.sha256(body).hexdigest()
    text = body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raw_records = _parse_html_tables(text)
        records = _normalize_public_records(source, raw_records)
        title = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
        return {
            "source": source, "url": url,
            "status": "SCRAPED_HTML_DATA" if http_status < 400 and records else "REFERENCE_ONLY",
            "http_status": http_status, "record_count": len(records),
            "structured": bool(records), "records": records,
            "page_title": html.unescape(title.group(1)).strip() if title else "",
            "digest": digest, "observed_at": _now(), "scrape_method": "html_table",
        }
    raw_records = _json_records(payload)
    records = _normalize_public_records(source, raw_records)
    return {
        "source": source, "url": url,
        "status": "REACHABLE_STRUCTURED_DATA" if http_status < 400 and records else "REFERENCE_ONLY",
        "http_status": http_status, "record_count": len(records),
        "structured": bool(records), "records": records, "digest": digest,
        "observed_at": _now(), "scrape_method": "json",
    }

def _byma_post(url: str, endpoint: str) -> dict[str, Any]:
    target = url.rstrip("/") + "/vanoms-be-core/rest/api/bymadata/free/" + endpoint
    body = json.dumps({
        "excludeZeroPxAndQty": True, "T1": True, "T0": False,
        "Content-Type": "application/json, text/plain",
    }).encode("utf-8")
    request = Request(target, data=body, method="POST", headers={
        "User-Agent": "Porota-RC6-read-only/1.0",
        "Accept": "application/json", "Content-Type": "application/json",
    })
    with urlopen(request, timeout=float(os.getenv("POROTA_PUBLIC_SOURCE_TIMEOUT", "15"))) as response:
        payload = response.read(MAX_BYTES + 1)
        status = int(getattr(response, "status", 200))
    if len(payload) > MAX_BYTES:
        raise ValueError("PUBLIC_SOURCE_RESPONSE_TOO_LARGE")
    result = parse_public_payload("BYMA", target, payload, status)
    result["scrape_method"] = "bymadata_public_post"
    result["endpoint"] = endpoint
    return result


def _collect_byma_public(base_url: str) -> dict[str, Any]:
    merged: list[dict[str, Any]] = []
    endpoint_results = []
    errors = []
    for endpoint in ("leading-equity", "cedears"):
        try:
            result = _byma_post(base_url, endpoint)
            endpoint_results.append(result)
            merged.extend(result.get("records", []))
        except Exception as exc:
            errors.append(f"{endpoint}:{type(exc).__name__}:{str(exc)[:120]}")
    # Preserve one row per symbol/settlement/currency; CEDEARs can overlap only
    # when the public panel returns duplicated pagination fragments.
    unique = {}
    for row in merged:
        key = tuple(str(row.get(k) or "") for k in ("symbol", "currency", "maturity"))
        unique[key] = row
    records = list(unique.values())
    return {
        "source": "BYMA", "url": base_url,
        "status": "SCRAPED_PUBLIC_DATA" if records else "REFERENCE_ONLY",
        "http_status": 200 if endpoint_results else None,
        "record_count": len(records), "structured": bool(records),
        "records": records, "scrape_method": "bymadata_public_post",
        "endpoints": [{"endpoint": item.get("endpoint"), "status": item.get("status"),
                       "record_count": item.get("record_count", 0),
                       "http_status": item.get("http_status")} for item in endpoint_results],
        "errors": errors, "observed_at": _now(),
    }


def collect_public_sources(urls: dict[str, str] | None = None) -> dict[str, Any]:
    results = []
    selected = urls or SOURCE_URLS
    for source, url in selected.items():
        if source == "BYMA" and "open.bymadata.com.ar" in url:
            try:
                results.append(_collect_byma_public(url))
            except Exception as exc:
                results.append({"source": source, "url": url, "status": "UNAVAILABLE",
                                "http_status": None, "record_count": 0,
                                "error": f"{type(exc).__name__}:{str(exc)[:160]}",
                                "observed_at": _now()})
            continue
        try:
            request = Request(url, headers={
                "User-Agent": "Porota-RC6-read-only/1.0",
                "Accept": "application/json,text/html;q=0.9",
            })
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
