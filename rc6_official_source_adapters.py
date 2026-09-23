"""Read-only official-source adapters for RC6 contract evidence.

Reachable landing pages are recorded as REFERENCE_ONLY, never as contract
approval. Structured provider fields are normalized without inference.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

SCHEMA = "rc6-official-source-evidence-v1"
MAX_BYTES = 2_000_000
TIMEOUT_SECONDS = float(os.getenv("POROTA_OFFICIAL_SOURCE_TIMEOUT", "15"))
OFFICIAL_SOURCE_URLS = {
    "BYMA": os.getenv("POROTA_BYMA_OFFICIAL_URL", "https://www.byma.com.ar/productos/productos-de-datos/market-data/apis"),
    "CNV": os.getenv("POROTA_CNV_OFFICIAL_URL", "https://www.cnv.gov.ar/sitioweb/empresas?seccion=buscador"),
    "MATBA_ROFEX": os.getenv("POROTA_MATBA_ROFEX_OFFICIAL_URL", "https://cem.matbarofex.com.ar/"),
}
FIELD_ALIASES = {
    "ticker": ("ticker", "symbol", "simbolo", "codigo", "code"),
    "isin": ("isin",),
    "maturity": ("maturity", "maturity_date", "vencimiento", "fecha_vencimiento"),
    "currency": ("currency", "moneda", "currency_code"),
    "coupon": ("coupon", "coupon_rate", "cupon", "tasa_cupon"),
    "multiplier": ("contractMultiplier", "contract_multiplier", "multiplier", "tamctra"),
    "initial_margin": ("initialMargin", "initial_margin", "margen_inicial"),
}

@dataclass(frozen=True)
class FetchResult:
    source: str
    url: str
    observed_at: str
    status: str
    http_status: int | None
    content_type: str
    digest: str
    evidence: dict[str, Any]
    error: str = ""

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _safe_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("OFFICIAL_SOURCE_URL_INVALID")
    return parsed.geturl()

def _fetch(url: str, opener: Callable[..., Any] = urlopen) -> tuple[int, str, bytes]:
    safe = _safe_url(url)
    request = Request(safe, headers={"User-Agent": "Porota-RC6-read-only-evidence/1.0", "Accept": "application/json,text/html;q=0.9"})
    response = opener(request, timeout=TIMEOUT_SECONDS)
    try:
        status = int(getattr(response, "status", 200))
        content_type = str(response.headers.get("Content-Type", ""))[:160]
        body = response.read(MAX_BYTES + 1)
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()
    if len(body) > MAX_BYTES:
        raise ValueError("OFFICIAL_SOURCE_RESPONSE_TOO_LARGE")
    return status, content_type, body

def _flatten_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        keys = {str(k).lower() for k in value}
        if keys.intersection({"ticker", "symbol", "isin", "codigo", "code", "vencimiento", "maturity"}):
            found.append(value)
        for nested in value.values():
            found.extend(_flatten_dicts(nested))
    elif isinstance(value, list):
        for nested in value:
            found.extend(_flatten_dicts(nested))
    return found[:500]

def _pick(record: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    lowered = {str(k).lower(): v for k, v in record.items()}
    for alias in aliases:
        if alias.lower() in lowered and lowered[alias.lower()] not in (None, ""):
            return lowered[alias.lower()]
    return None

def normalize_records(source: str, payload: Any) -> list[dict[str, Any]]:
    result = []
    for raw in _flatten_dicts(payload):
        normalized = {"source": source}
        for field, aliases in FIELD_ALIASES.items():
            value = _pick(raw, aliases)
            if value not in (None, ""):
                normalized[field] = value
        if len(normalized) > 1:
            result.append(normalized)
    return result

def parse_payload(source: str, url: str, status: int, content_type: str, body: bytes, *, observed_at: str | None = None) -> FetchResult:
    observed_at = observed_at or _now()
    digest = hashlib.sha256(body).hexdigest()
    text = body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
        structured = True
    except json.JSONDecodeError:
        payload = None
        structured = False
    records = normalize_records(source, payload) if structured else []
    title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    evidence = {
        "schema": SCHEMA, "source": source, "url": url, "http_status": status,
        "content_type": content_type, "structured": structured, "records": records,
        "record_count": len(records), "official_links": sorted(set(re.findall(r"https?://[^\\s\"'<>]+", text)))[:100],
        "page_title": html.unescape(title_match.group(1)).strip() if title_match else "",
        "retrieved_at": observed_at,
    }
    state = "UNAVAILABLE_HTTP" if status >= 400 else "REACHABLE_STRUCTURED" if structured and records else "REFERENCE_ONLY"
    return FetchResult(source, url, observed_at, state, status, content_type, digest, evidence)

def init_schema(store: Any) -> None:
    with store.connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS official_source_evidence(
          source TEXT NOT NULL, url TEXT NOT NULL, observed_at TEXT NOT NULL,
          status TEXT NOT NULL, http_status INTEGER, content_type TEXT NOT NULL,
          digest TEXT NOT NULL, evidence_json TEXT NOT NULL, error TEXT NOT NULL,
          PRIMARY KEY(source,url,observed_at))""")

def collect(store: Any, *, opener: Callable[..., Any] = urlopen, urls: dict[str, str] | None = None) -> dict[str, Any]:
    init_schema(store)
    results = []
    for source, raw_url in (urls or OFFICIAL_SOURCE_URLS).items():
        url, observed_at = str(raw_url or "").strip(), _now()
        try:
            status, content_type, body = _fetch(url, opener)
            result = parse_payload(source, url, status, content_type, body, observed_at=observed_at)
        except Exception as exc:
            result = FetchResult(source, url, observed_at, "UNAVAILABLE", None, "", "", {}, f"{type(exc).__name__}:{str(exc)[:240]}")
        with store.connect() as c:
            c.execute("""INSERT INTO official_source_evidence
              (source,url,observed_at,status,http_status,content_type,digest,evidence_json,error)
              VALUES(?,?,?,?,?,?,?,?,?)""", (result.source,result.url,result.observed_at,result.status,
              result.http_status,result.content_type,result.digest,json.dumps(result.evidence,ensure_ascii=False,sort_keys=True),result.error))
        results.append({"source": result.source, "status": result.status, "http_status": result.http_status,
                        "record_count": result.evidence.get("record_count", 0), "error": result.error,
                        "url": result.url, "observed_at": result.observed_at})
    return {"schema": SCHEMA, "sources": results, "collected_at": _now()}

def assert_read_only_invariants() -> None:
    assert all(urlparse(url).scheme in {"http", "https"} for url in OFFICIAL_SOURCE_URLS.values())
