"""Evidencia consolidada PPI -> IOL -> fuentes públicas para RC6.

Los valores conservan su procedencia. IOL y BYMA sólo rellenan ausencias de la
fuente anterior y la salida queda limitada a PAPER/SHADOW, sin rutas reales.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
from html.parser import HTMLParser
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen

from rc6_dynamic_universe.common import digest, stamp
from rc6_shadow_runtime.source_authority import (VERSION as AUTHORITY_VERSION, native_time, receipt_time, resolve_field, source_rank)

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

_FAMILY_ALIASES = {
    "ACCION": "ACCIONES", "ACCIONES": "ACCIONES", "CEDEAR": "CEDEARS", "CEDEARS": "CEDEARS",
    "ETF": "ETFS", "ETFS": "ETFS", "BONO": "BONOS", "BONOS": "BONOS",
    "LETRA": "LETRAS", "LETRAS": "LETRAS", "ON": "OBLIGACIONES",
    "OBLIGACION": "OBLIGACIONES", "OBLIGACIONES": "OBLIGACIONES",
    "OPCION": "OPCIONES", "OPCIONES": "OPCIONES", "FUTURO": "FUTUROS",
    "FUTUROS": "FUTUROS", "CAUCION": "CAUCIONES", "CAUCIONES": "CAUCIONES",
    "FCI": "FCI",
}
_MARKET_ALIASES = {"BCBA": "BYMA", "BYMA": "BYMA", "ROFEX": "A3", "A3": "A3"}
_TERM_ALIASES = {"T1": "A-24HS", "24HS": "A-24HS", "A-24HS": "A-24HS",
                 "T0": "INMEDIATA", "CI": "INMEDIATA", "INMEDIATA": "INMEDIATA"}

def _canonical_family(value: Any) -> str:
    raw = str(value or "").strip().upper().replace("_", "").replace("-", "").replace(" ", "")
    return _FAMILY_ALIASES.get(raw, str(value or "").strip().upper())

def _canonical_market(value: Any) -> str:
    raw = str(value or "").strip().upper()
    return _MARKET_ALIASES.get(raw, raw)

def _canonical_term(value: Any) -> str:
    raw = str(value or "").strip().upper()
    return _TERM_ALIASES.get(raw, raw)

def _key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        _canonical_family(row.get("family") or row.get("instrument_type") or row.get("asset_type") or row.get("asset_class")),
        str(row.get("symbol") or row.get("ticker") or "").strip().upper(),
        _canonical_market(row.get("market")),
        str(row.get("currency") or "").strip().upper(),
        _canonical_term(row.get("term") or row.get("settlement") or row.get("settlement_code")),
    )

def _num(value: Any) -> float | None:
    try:
        result = float(value) if value not in (None, "") and not isinstance(value, bool) else None
        return result if result is not None and math.isfinite(result) else None
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

CASCADE_FIELDS = ("last", "bid", "ask", "bid_size", "ask_size", "variation_pct",
                  "cash_volume", "volume", "vwap")


def _cascade_fields(primary: dict[str, Any], iol: dict[str, Any], official: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Completa por prioridad sin ocultar la fuente de cada valor."""
    result = {}
    for field in CASCADE_FIELDS:
        candidates = (("PPI", _num(primary.get(field))), ("IOL", _num(iol.get(field))),
                      ("BYMA", _num(official.get(field))))
        source, value = next(((name, value) for name, value in candidates if value is not None), (None, None))
        result[field] = {"value": value, "source": source,
                         "ppi": candidates[0][1], "iol": candidates[1][1], "byma": candidates[2][1]}
    return result


def _identity_valid(row):
    key = _key(row)
    if not all(key) or key[0] not in set(_FAMILY_ALIASES.values()) or key[2] not in set(_MARKET_ALIASES.values()) or key[3] not in {"ARS", "USD", "USD_MEP", "USD_CCL"} or key[4] not in set(_TERM_ALIASES.values()):
        return False
    for names, normalize, expected in (
        (("family", "instrument_type", "asset_type", "asset_class"), _canonical_family, key[0]),
        (("symbol", "ticker"), lambda value: str(value).strip().upper(), key[1]),
        (("term", "settlement", "settlement_code"), _canonical_term, key[4]),
    ):
        if any(normalize(row[name]) != expected for name in names if row.get(name) not in (None, "")):
            return False
    return True


def _safe_evidence(value):
    """Retain invalid numeric evidence without emitting invalid JSON numbers."""
    if isinstance(value, float) and not math.isfinite(value):
        return {"raw_value": repr(value), "status": "NO_VERIFICADO_NON_FINITE_NUMBER"}
    if isinstance(value, dict):
        return {key: _safe_evidence(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_evidence(item) for item in value]
    return value


def _fold_source(records):
    """No source row wins an ambiguous duplicate by list/dict insertion order."""
    unique = {digest(row): dict(row) for row in records}
    rows = [unique[key] for key in sorted(unique)]
    merged, conflicts = {}, []
    for field in set().union(*(set(row) for row in rows)) if rows else ():
        values = {digest(row[field]): row[field] for row in rows if row.get(field) not in (None, "")}
        if len(values) == 1:
            merged[field] = next(iter(values.values()))
        elif len(values) > 1:
            conflicts.append({"field": field, "reason": "DUPLICATE_SOURCE_EVIDENCE_CONFLICT",
                              "values": list(values.values())[:16]})
    return merged, conflicts, rows[:16]


def consolidate(ppi_rows: list[dict[str, Any]], iol_rows: list[dict[str, Any]],
                official_rows: list[dict[str, Any]] | None = None, *, as_of=None) -> dict[str, Any]:
    """Five-part identity joins; unbound legacy comparisons stay references.

    Diagnostic effective_fields preserve the previous comparison interface.
    validated_effective_fields additionally require native clocks, availability,
    exact identity and conflict-free evidence. Reference rows assign no canonical
    identity and cannot become discovery/selection/entry authority.
    """
    collected = stamp(as_of).isoformat() if as_of is not None else _now()
    groups, references, identity_reviews = {}, {}, []
    for source, records in (("PPI", ppi_rows), ("IOL", iol_rows), ("BYMA", official_rows or [])):
        for raw in records:
            if not isinstance(raw, dict) or not _key(raw)[1]:
                continue
            row = _safe_evidence(raw)
            key = _key(row)
            if _identity_valid(row):
                groups.setdefault(key, {}).setdefault(source, []).append(row)
            else:
                base = (key[0], key[1], key[2], key[4])
                references.setdefault(base, {}).setdefault(source, []).append(row)
                if len(identity_reviews) < 1000:
                    identity_reviews.append({"source": source, "source_path": row.get("source_path") or source,
                        "identity": list(key), "reason": "EXACT_IDENTITY_REQUIRED_OR_CONFLICTING",
                        "canonical_identity": None, "identity_assigned": False})
    # Legacy missing-currency comparisons may show compatible reference values,
    # without borrowing the complementary currency or inventing a PPI identity.
    for base, sources in references.items():
        for key, bound in groups.items():
            if (key[0], key[1], key[2], key[4]) == base:
                for source, records in bound.items():
                    sources.setdefault(source, []).extend(records)
    rows = []
    work = [(key, values, True) for key, values in sorted(groups.items())]
    work += [((base[0], base[1], base[2], "", base[3]), values, False) for base, values in sorted(references.items())]
    # References first preserve callers which historically used one scalar
    # comparison row. Their absent canonical_identity makes authority explicit.
    work.sort(key=lambda value: (value[0][0:3], value[0][4], value[2], value[0][3]))
    for key, grouped, exact in work:
        folded, duplicates, candidates = {}, {}, {}
        for source in ("PPI", "IOL", "BYMA"):
            folded[source], duplicates[source], candidates[source] = _fold_source(grouped.get(source, []))
        primary, secondary, public = folded["PPI"], folded["IOL"], folded["BYMA"]
        primary_authoritative = bool(exact and grouped.get("PPI") and
            all(source_rank(row.get("source") or "PPI") == 0 for row in grouped["PPI"]))
        source_reviews = [{"source": row.get("source"), "comparison_source": "PPI",
            "reason": "PRIMARY_SOURCE_AUTHORITY_NOT_PROVEN"} for row in grouped.get("PPI", [])
            if source_rank(row.get("source") or "PPI") != 0][:16]
        complement = {field: _num(secondary.get(field)) for field in CASCADE_FIELDS}
        complement.update({field: secondary.get(field) for field in ("provider_observed_at", "asset_type", "currency", "units_per_lot")})
        compared = {}
        for field in CASCADE_FIELDS:
            pv, sv = _num(primary.get(field)), _num(secondary.get(field))
            compared[field] = {"primary": pv, "secondary": sv,
                "state": "MATCH" if pv is not None and sv is not None and (pv == sv or (pv and abs(pv-sv)/abs(pv)*100 <= 2.0)) else "DIVERGENCE" if pv is not None and sv is not None else "NOT_COMPARABLE"}
        validated, provenance = {}, {}
        for field in CASCADE_FIELDS:
            evidence = []
            for source, records in grouped.items():
                for candidate in records:
                    if candidate.get(field) is None:
                        continue
                    quote_time = native_time(candidate)
                    origin = candidate.get("source") or source
                    if source == "PPI" and field in {"bid", "ask", "bid_size", "ask_size"}:
                        quote_time = candidate.get("book_at") or candidate.get("provider_book_at")
                    unit = candidate.get(field+"_unit")
                    if field in {"last", "bid", "ask", "vwap"}:
                        unit = unit or candidate.get("price_unit")
                    evidence.append({"value": _num(candidate.get(field)), "source": origin,
                        "source_path": candidate.get("source_path") or origin,
                        "source_at": quote_time, "received_at": receipt_time(candidate),
                        "unit": unit,
                        "valid": str(candidate.get("state") or "READY").upper() in {"READY", "LIVE_FRESH", "FRESH", "OBSERVE_ONLY"},
                        "native_reason": candidate.get("reason")})
            resolved = resolve_field(evidence, as_of=collected, tolerance_fraction=.02)
            provenance[field] = resolved
            validated[field] = {"value": resolved["value"] if exact and not resolved["review_required"] else None,
                                "source": resolved.get("source") if exact and not resolved["review_required"] else None}
        review = (bool(source_reviews) or any(duplicates.values()) or any(value["review_required"] for value in provenance.values())
                  or any(value["state"] == "DIVERGENCE" for value in compared.values()))
        rows.append({
            "identity": {"family": key[0], "symbol": key[1], "market": key[2], "term": key[4]},
            "canonical_identity": [key[1], key[0], key[2], key[3], key[4]] if exact else None,
            "currency": key[3] or None, "identity_binding": "EXACT_FIVE_PART_IDENTITY" if exact else "REFERENCE_ONLY_EXACT_IDENTITY_REQUIRED",
            "identity_primary_source": "PPI" if primary_authoritative else "COMPLEMENT_REFERENCE_ONLY",
            "ppi_primary": primary, "iol_complement": complement, "official_complement": public,
            "effective_fields": _cascade_fields(primary, complement, public),
            "validated_effective_fields": validated, "field_provenance": provenance, "comparison": compared,
            "duplicate_conflicts": duplicates, "source_candidates": candidates,
            "source_authority_reviews": source_reviews,
            "review_status": "CONFLICT_REVIEW_REQUIRED" if review else "NO_CURRENT_CONFLICT",
            "freshness": {source: _age_status(native_time(value)) for source, value in folded.items()},
            "decision_effect": "OBSERVE_ONLY", "shadow_promotion": not review,
            "selection_eligible": bool(primary_authoritative and not review and validated["last"]["value"] is not None
                                       and validated["last"]["value"] > 0),
            "live_decision_authority": False, "entry_authority": False, "real_money_authorized": False,
        })
    return {"schema": SCHEMA, "source_authority_policy": AUTHORITY_VERSION,
        "source_order": "PPI_PRIMARY_IOL_COMPLEMENTARY_BYMA_PUBLIC_COMPLEMENTARY", "collected_at": collected,
        "rows": rows, "identity_reviews": identity_reviews,
        "counts": {"ppi": len(ppi_rows), "iol": len(iol_rows), "consolidated": len(rows), "official": len(official_rows or [])},
        "provider_available": {"PPI": None, "IOL": None, "BYMA": None},
        "decision_effect": "OBSERVE_ONLY", "entry_authority": False, "real_money_authorized": False}


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
    return records

def _normalize_public_records(source: str, records: list[dict[str, Any]], *, family: str = "") -> list[dict[str, Any]]:
    aliases={
        "symbol": ("especie","símbolo","simbolo","ticker","symbol","code"),
        "currency": ("moneda","currency","denominationccy"),
        "bid": ("p. cpra.","p compra","compra","bid","bidprice","bid_price","buyprice"),
        "ask": ("p. vta.","p venta","venta","ask","offerprice","offer_price","sellprice"),
        "last": ("último","ultimo","last","lastprice","last_price","tradeprice","trade_price","price","trade"),
        "variation_pct": ("var.","variación","variacion","variation","variationpercent","variation_pct","changepercent"),
        "volume": ("volumen","volume","tradevolume","trade_volume","quantity"),
        "cash_volume": ("vol. monto","vol monto","cash volume","cashvolume","cash_volume","tradedamount","volumeamount"),
        "vwap": ("vwap",),
        "timestamp": ("hora","timestamp","fecha","date","tradedate","trade_date","marketdatadate","market_data_date"),
        "maturity": ("vto.","vto","vencimiento","maturity","maturitydate"),
        "settlement_code": ("settlementtype","settlement_code"),
        "provider_time_only": ("tradehour","provider_time_only"),
        "bid_size": ("quantitybid","bid_size"),
        "ask_size": ("quantityoffer","ask_size"),
        "adjustment": ("ajuste","adjustment"),
        "open_interest": ("interés abierto","interes abierto","open interest"),
    }
    out=[]
    for raw in records:
        normalized={"source":source, "family": _canonical_family(family), "market": "BYMA"}
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
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("items", "data", "records", "results", "content"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
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
        raw_records = _parse_html_tables(text) if http_status < 400 else []
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
    provider_error = isinstance(payload, dict) and (payload.get("error") or str(payload.get("status", "")).upper() in {"ERROR", "FAILED"})
    raw_records = [] if http_status >= 400 or provider_error else _json_records(payload)
    records = _normalize_public_records(source, raw_records)
    return {
        "source": source, "url": url,
        "status": "REACHABLE_STRUCTURED_DATA" if http_status < 400 and records else "REFERENCE_ONLY",
        "http_status": http_status, "record_count": len(records),
        "structured": bool(records), "records": records, "digest": digest,
        "observed_at": _now(), "scrape_method": "json",
        "pagination": payload.get("content", {}) if isinstance(payload, dict) else {},
        "source_record_count": len(raw_records),
    }

def _byma_post(url: str, endpoint: str, family: str) -> dict[str, Any]:
    target = url.rstrip("/") + "/vanoms-be-core/rest/api/bymadata/free/" + endpoint
    records, pages = [], []
    expected_total = None
    for page in range(1, 101):
        body = json.dumps({
            "excludeZeroPxAndQty": True, "T1": True, "T0": False,
            "Content-Type": "application/json, text/plain", "page_number": page,
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
        meta = result.get("pagination") or {}
        if not isinstance(meta, dict):
            raise ValueError("BYMA_PAGINATION_INVALID")
        page_count = int(meta.get("page_count") or 1)
        if page_count > 100 or int(meta.get("page_number") or page) != page:
            raise ValueError("BYMA_PAGINATION_NOT_ADVANCING")
        if meta.get("total_elements_count") is not None:
            total = int(meta["total_elements_count"])
            if expected_total is not None and expected_total != total:
                raise ValueError("BYMA_PAGINATION_CHANGED_DURING_CAPTURE")
            expected_total = total
        if result["status"] == "REFERENCE_ONLY" and (page_count > 1 or page > 1):
            raise ValueError("BYMA_PAGE_NOT_STRUCTURED")
        records.extend(dict(row, family=_canonical_family(family)) for row in result["records"])
        pages.append({"page_number":page,"digest":result["digest"],"source_record_count":result.get("source_record_count",0),"record_count":len(result["records"]),"observed_at":result["observed_at"]})
        if page >= page_count:
            break
    if expected_total is not None and sum(p["source_record_count"] for p in pages) != expected_total:
        raise ValueError("BYMA_SOURCE_COUNT_MISMATCH")
    return {"source":"BYMA","url":target,"records":records,"record_count":len(records),
        "structured":bool(records),"status":"SCRAPED_PUBLIC_DATA" if records else "REFERENCE_ONLY",
        "http_status":status,"scrape_method":"bymadata_public_post","endpoint":endpoint,
        "family":_canonical_family(family),"pages":pages,"source_record_count":sum(p["source_record_count"] for p in pages),
        "expected_total":expected_total,"observed_at":_now()}


def _collect_byma_public(base_url: str) -> dict[str, Any]:
    merged: list[dict[str, Any]] = []
    endpoint_results = []
    errors = []
    endpoint_families = (
        ("leading-equity", "ACCIONES"),
        ("cedears", "CEDEARS"),
        ("public-bonds", "BONOS"),
        ("negociable-obligations", "OBLIGACIONES"),
        ("cauciones", "CAUCIONES"),
        ("options", "OPCIONES"),
    )
    for endpoint, family in endpoint_families:
        try:
            result = _byma_post(base_url, endpoint, family)
            endpoint_results.append(result)
            merged.extend(result.get("records", []))
        except Exception as exc:
            errors.append(f"{endpoint}:{type(exc).__name__}:{str(exc)[:120]}")
    # Preserve one row per symbol/settlement/currency; CEDEARs can overlap only
    # when the public panel returns duplicated pagination fragments.
    unique = {}
    for row in merged:
        key = (_canonical_family(row.get("family")), str(row.get("symbol") or "").upper(),
               str(row.get("currency") or "").upper(), str(row.get("settlement_code") or ""), str(row.get("maturity") or ""))
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
                       "http_status": item.get("http_status"),"source_record_count":item.get("source_record_count"),
                       "expected_total":item.get("expected_total"),"pages":item.get("pages",[])} for item in endpoint_results],
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
