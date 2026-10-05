"""Offline discovery adapters for existing, independently dated market evidence.

The adapters own no transport, credentials, browser, database or order route.
Capture time proves availability; only an explicit aware provider timestamp
proves quote age. Observations never grant signal or execution authority.
"""
from collections.abc import Mapping
from datetime import datetime, timezone

from .common import digest, identity, number, stamp


SOURCE_ALIASES = {
    "PPI": "PPI_API", "PPI_API": "PPI_API",
    "PPI_WEB": "PPI_WEB", "PPI_AUTHENTICATED_WEB": "PPI_WEB",
    "IOL": "IOL", "IOL_MCP": "IOL", "IOL_RUNTIME": "IOL",
    "BYMA": "BYMA_PUBLIC_SCRAPER", "BYMA_PUBLIC": "BYMA_PUBLIC_SCRAPER",
    "BYMA_PUBLIC_SCRAPER": "BYMA_PUBLIC_SCRAPER",
}
FAMILIES = {
    "ACCION": "ACCIONES", "ACCIONES": "ACCIONES",
    "CEDEAR": "CEDEARS", "CEDEARS": "CEDEARS",
    "ETF": "ETFS", "ETFS": "ETFS", "BONO": "BONOS", "BONOS": "BONOS",
    "TIT. PUBLICOS": "BONOS", "LETRA": "LETRAS", "LETRAS": "LETRAS",
    "ON": "OBLIGACIONES", "OBL.NEG.": "OBLIGACIONES",
    "OBL.PYME": "OBLIGACIONES", "OBLIGACIONES": "OBLIGACIONES",
    "OBLIGACIONES_NEGOCIABLES": "OBLIGACIONES",
    "OPCION": "OPCIONES", "OPCIONES": "OPCIONES",
    "FUTURO": "FUTUROS", "FUTUROS": "FUTUROS",
    "CAUCION": "CAUCIONES", "CAUCIONES": "CAUCIONES", "FCI": "FCI",
    "FCI_LOCAL": "FCI", "FCI LOCAL": "FCI",
}
MARKETS = {"BCBA": "BYMA", "BYMA": "BYMA", "ROFEX": "A3", "A3": "A3"}
TERMS = {
    "T1": "A-24HS", "T+1": "A-24HS", "24HS": "A-24HS", "A-24HS": "A-24HS",
    "T0": "INMEDIATA", "T+0": "INMEDIATA", "CI": "INMEDIATA", "INMEDIATA": "INMEDIATA",
}
VOLUME_UNITS = frozenset({"SHARES", "UNITS", "CONTRACTS", "NOMINALS", "ARS", "USD", "USD_MEP", "USD_CCL"})
PRICE_UNITS = frozenset({"PER_SHARE", "PER_UNIT", "PER_CONTRACT", "PER_NOMINAL", "PER_100_NOMINALS", "QUOTE_UNIT"})
PROVIDER_TIMES = ("provider_observed_at", "provider_timestamp", "source_at", "timestamp", "date", "tradeDate")
RECEIVED_TIMES = ("received_at", "captured_at", "capture_observed_at", "observed_at", "collected_at", "refreshed_at", "generated_at")
MAX_SOURCE_AUDIT_REPORTS = 64
MAX_SOURCE_AUDIT_OBSERVATIONS = 100000
DOM_ROUTE_FAMILIES = {
    "/Cotizaciones/Acciones": "ACCIONES", "/Cotizaciones/Cedears": "CEDEARS",
    "/Cotizaciones/ETFs": "ETFS", "/Cotizaciones/Bonos": "BONOS",
    "/Cotizaciones/Letras": "LETRAS", "/Cotizaciones/Ons": "OBLIGACIONES",
    "/Cotizaciones/Opciones": "OPCIONES", "/Cotizaciones/Futuros": "FUTUROS",
    "/Cotizaciones/Cauciones": "CAUCIONES", "/Cotizaciones/FCIs": "FCI",
}

# This is an audit of code and dated read-only evidence, not a provider promise.
SOURCE_MANIFEST = {
    "PPI_API": {
        "paths": ["bd_ppi_readonly_guard.py", "cf_intraday_scalping.py", "bf_production_paper_observer.py"],
        "coverage": "Explicit requested identity; availability differs by endpoint/instrument",
        "fields": {"current": ["price", "date"], "book": ["bid", "ask", "quantities", "date"],
                   "intraday": ["price", "volume", "date"]},
        "timestamp": "Endpoint provider date; receive time separate",
        "transversal": False, "discovery_utility": "Adaptive per-identity reads; search is identity evidence",
        "latency": "NO_VERIFICADO", "cost": "Requests per endpoint; factual capacity NO_VERIFICADO",
        "freshness": "NO_VERIFICADO_DURING_SESSION", "capacity_limit": "NO_VERIFICADO",
    },
    "PPI_WEB": {
        "paths": ["rc6_trusted_browser_contract_collector.py", "rc6_contract_dom_collector.py",
                  "rc6_contract_dom_importer.py", "rc6_dom_coverage_reconciler.py"],
        "coverage": "13 allowed DOM family pages; default caucion/auctions/futures/bonds/options; visible tables only",
        "fields": ["explicit table headers/cells", "contract metadata", "commissions", "option underlyings", "bond technical fields"],
        "timestamp": "generated_at is capture time; quote timestamp only when explicitly present",
        "transversal": "PARTIAL_VISIBLE_TABLES", "discovery_utility": "Contract/identity context; completeness requires independent expected universe",
        "configured_cadence_seconds": {"due_check": 300, "operability": 900, "derivative_series": 900,
                                       "caucion_auctions": 300, "static": 86400, "full_browser": 604800},
        "limits": "DOM max 8 tables x 100 rows x 40 columns; no pagination; importer aggregate ticker=*",
        "latency": "NO_VERIFICADO", "cost": "Existing browser outside strategy hot path; NO_VERIFICADO",
        "freshness": "NO_VERIFICADO", "legacy": "cn_ppi_authenticated_family_scraper_hf6.py is manual legacy, not a new login path",
    },
    "IOL": {
        "paths": ["iol_shadow_collector_rc6.py", "iol_shadow_observation_rc6.py",
                  "scripts/rc6_iol_shadow_collect.py", "rc6_iol_family_reference.py"],
        "coverage": "AVAILABLE/auditable catalog ticker rotation; family-specific references are separate",
        "fields": ["last", "bid", "ask", "bid_size", "ask_size", "unit_price", "lot_price",
                   "variation_pct", "cash_volume", "provider_observed_at", "asset_type", "currency", "units_per_lot"],
        "timestamp": "provider_observed_at separate from captured_at/refreshed_at; saved LIVE_FRESH labels do not age themselves",
        "transversal": False, "reference_transversal": ["options chain per underlying", "FCI inventory", "caucion rates"],
        "discovery_utility": "Existing rotating quote cache and family metadata; no all-equity fresh snapshot proven",
        "configured_cadence_seconds": 60, "configured_quote_batch": 12,
        "local_governor_calls_per_minute": 40, "governor_is_provider_capacity_evidence": False,
        "metadata_ttl_seconds": 86400, "quote_cache_ttl_seconds": 300, "provider_freshness_seconds": 120,
        "latency": "NO_VERIFICADO", "cost": "At most 24 quote/metadata calls plus nominal 15 family reads before retries; local governor serializes",
        "freshness": "NO_VERIFICADO_DURING_SESSION",
        "runtime_evidence": {"run_id": 37131712187, "job_id": 111228035345,
                             "cache_refreshed_at": "2026-10-02T19:58:57.187272+00:00",
                             "sections_observed": ["caucion:ARS", "caucion:USD", "fci"],
                             "sections_unavailable": ["fixed_income", "options:DOME", "options:ECOG", "options:ECOGC", "options:ECOGD"]},
    },
    "BYMA_PUBLIC_SCRAPER": {
        "paths": ["rc6_source_consolidation.py", "scripts/rc6_public_source_capture.py",
                  "scripts/rc6_byma_morning_pipeline.py", "scripts/rc6_byma_morning_watch.py"],
        "coverage": ["ACCIONES leading-equity panel", "CEDEARS", "BONOS", "OBLIGACIONES", "CAUCIONES", "OPCIONES"],
        "unproven_panels": ["ETFS", "LETRAS", "FUTUROS", "FCI"],
        "fields": ["symbol", "currency", "bid", "ask", "last", "variation_pct", "volume", "cash_volume",
                   "vwap", "timestamp", "provider_time_only", "bid_size", "ask_size", "settlement_code", "maturity"],
        "timestamp": "Per-row aware timestamp only; provider_time_only/capture time cannot prove quote freshness",
        "transversal": "PAGINATED_PUBLIC_PANELS", "discovery_utility": "OBSERVE_ONLY snapshot when exact identity and quote time are demonstrated",
        "configured_cadence_seconds": {"iol_host_service": 60, "observer_open": 900},
        "morning_authority_watch": "08:30 ART weekdays; reuses the same structured snapshot",
        "configured_cadence_is_confirmed_latency": False,
        "limits": "excludeZeroPxAndQty=True, T1=True, T0=False; at most 100 pages/panel; atomic capture is not a simultaneous exchange snapshot",
        "latency": "NO_VERIFICADO", "cost": "Six paginated existing public panels plus reference pages; requests/latency not measured",
        "freshness": "NO_VERIFICADO_DURING_SESSION", "decision_effect": "OBSERVE_ONLY",
        "runtime_evidence": {"run_id": 37125515526, "job_id": 111209919420,
                             "observed_at": "2026-10-03T14:04:59+00:00", "record_count": 1,
                             "status": "SCRAPED_PUBLIC_DATA", "market_phase": "CLOSED_SATURDAY",
                             "interpretation": "One demonstrated row; no full-market/freshness/capacity claim"},
    },
}


def _first(mapping, keys):
    return next((mapping[k] for k in keys if mapping.get(k) not in (None, "")), None)


def _time(value):
    try:
        return stamp(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _iso(value):
    return value.isoformat() if value is not None else None


def _canonical_identity(row, family=None):
    raw_family = str(_first(row, ("instrument_type", "family", "asset_type", "asset_class")) or family or "").strip().upper()
    raw_market = str(row.get("market") or "").strip().upper()
    raw_term = str(_first(row, ("settlement", "term", "settlement_code")) or "").strip().upper()
    record = {"ticker": _first(row, ("ticker", "symbol")),
              "instrument_type": FAMILIES.get(raw_family, raw_family),
              "market": MARKETS.get(raw_market, raw_market), "currency": row.get("currency"),
              "settlement": TERMS.get(raw_term, raw_term)}
    for keys, aliases, expected in (
        (("ticker", "symbol"), {}, str(record["ticker"] or "").strip().upper()),
        (("instrument_type", "family", "asset_type", "asset_class"), FAMILIES, record["instrument_type"]),
        (("settlement", "term", "settlement_code"), TERMS, record["settlement"]),
    ):
        explicit = [str(row[k]).strip().upper() for k in keys if row.get(k) not in (None, "")]
        if any(aliases.get(value, value) != expected for value in explicit):
            raise ValueError("EXACT_IDENTITY_REQUIRED")
    # Unknown numeric settlement/currency codes are not silently translated.
    if record["instrument_type"] not in set(FAMILIES.values()) or record["market"] not in set(MARKETS.values()):
        raise ValueError("EXACT_IDENTITY_REQUIRED")
    if record["settlement"] not in set(TERMS.values()) or str(record["currency"] or "").strip().upper() not in {"ARS", "USD", "USD_MEP", "USD_CCL"}:
        raise ValueError("EXACT_IDENTITY_REQUIRED")
    return list(identity(record))


def _entries(snapshot, source):
    """Yield raw evidence and its capture envelope, never infer identity defaults."""
    if isinstance(snapshot, list):
        return [(row, {}, None) for row in snapshot if isinstance(row, Mapping)]
    if not isinstance(snapshot, Mapping):
        return []
    if source == "BYMA_PUBLIC_SCRAPER" and isinstance(snapshot.get("sources"), list):
        entries = []
        for block in snapshot["sources"]:
            if isinstance(block, Mapping) and str(block.get("source") or "").upper() == "BYMA":
                envelope = {**snapshot, **block}
                entries.extend((row, envelope, None) for row in _list(block.get("records")) if isinstance(row, Mapping))
        return entries
    if source == "PPI_WEB" and isinstance(snapshot.get("routes"), list):
        entries = []
        aliases = {"ticker": "ticker", "symbol": "symbol", "especie": "ticker", "símbolo": "ticker",
                   "family": "family", "instrument_type": "instrument_type", "familia": "family",
                   "mercado": "market", "market": "market", "moneda": "currency", "currency": "currency",
                   "plazo": "settlement", "settlement": "settlement", "timestamp": "timestamp",
                   "provider_observed_at": "provider_observed_at", "fecha": "timestamp", "último": "last",
                   "ultimo": "last", "last": "last", "bid": "bid", "ask": "ask", "volume": "volume",
                   "volume_unit": "volume_unit", "price_unit": "price_unit"}
        for route in snapshot["routes"]:
            if not isinstance(route, Mapping) or not route.get("reached"):
                continue
            family = DOM_ROUTE_FAMILIES.get(route.get("requested"))
            for table in _list(route.get("tables")):
                if not isinstance(table, Mapping) or not table.get("materializable"):
                    continue
                headers = [aliases.get(str(h).strip().lower()) for h in _list(table.get("headers"))]
                if len([h for h in headers if h]) != len(set(h for h in headers if h)):
                    continue
                for cells in _list(table.get("rows")):
                    if isinstance(cells, list):
                        row = {key: cells[i] for i, key in enumerate(headers) if key and i < len(cells)}
                        entries.append((row, snapshot, family))
        return entries
    rows = snapshot.get("symbols") if source == "IOL" else snapshot.get("records", snapshot.get("observations", []))
    if not isinstance(rows, list):
        rows = []
    return [(row, snapshot, None) for row in rows if isinstance(row, Mapping)]


SOURCE_REASON_CODES = frozenset({
    "SOURCE_UNAVAILABLE", "SOURCE_UNAVAILABLE_OR_INVALID", "SOURCE_ERROR_REDACTED",
    "SOURCE_AUTHENTICATION_FAILED", "SOURCE_RATE_LIMITED", "SOURCE_TIMEOUT",
    "SOURCE_TRANSPORT_FAILURE", "SOURCE_RESPONSE_INVALID", "SOURCE_CIRCUIT_OPEN",
    "IOL_EMPTY_UNEXPECTED", "IOL_SHADOW_CIRCUIT_OPEN", "EMPTY_UNEXPECTED",
    "CACHE_FRESH", "CACHE_STALE", "NO_VERIFICADO", "INVALID_SOURCE_CONTAINER",
    "EXACT_IDENTITY_AMBIGUOUS", "PROVIDER_TIMESTAMP_MISSING_OR_AMBIGUOUS",
    "PROVIDER_TIMESTAMP_IN_FUTURE", "RECEIVED_TIMESTAMP_MISSING_OR_AMBIGUOUS",
    "NOT_AVAILABLE_AT_CUTOFF", "PROVIDER_TIMESTAMP_AFTER_RECEIPT", "STALE_QUOTES",
    "NUMERIC_FIELDS_OR_UNITS_UNVERIFIED", "USEFUL_SHADOW_OBSERVATION",
    "LEGACY_ERROR_IDENTITY_UNVERIFIED", "PPI_INTRADAY_UNAVAILABLE",
    "PPI_INSTRUMENT_NOT_FOUND", "PPI_SESSION_INVALID", "PPI_BUDGET_BACKPRESSURE",
    "PPI_EMPTY_OR_NON_JSON_AFTER_RETRY", "PPI_HTTP_401", "PPI_HTTP_403", "PPI_HTTP_408",
    "PPI_HTTP_429", "PPI_HTTP_500", "PPI_HTTP_502", "PPI_HTTP_503", "PPI_HTTP_504",
    "STATIC_EVIDENCE_AVAILABILITY_EXPIRED", "SOURCE_RECEIPT_CLOCK_MISMATCH",
    "DUPLICATE_SOURCE_EVIDENCE_CONFLICT", "SOURCE_FIELD_DISCREPANCY_REVIEW_REQUIRED",
    "PROSPECTIVE_RECEIPTS_ONLY; excluded rows retained as source evidence",
    "PLANNER_NATIVE_INPUT_SET; excluded source receipts retained as evidence",
})


def source_reason_code(value):
    """A closed vocabulary at durable boundaries; arbitrary text is discarded.

    Exception classes are classified independently of their message. No part of
    a response body, URL, account identifier or credential is retained.
    """
    if value is None:
        return None
    if isinstance(value, str) and value in SOURCE_REASON_CODES:
        return value
    if isinstance(value, BaseException):
        if str(value) in SOURCE_REASON_CODES:
            return str(value)
        status = getattr(value, "status_code", None)
        if type(status) is int and status in {401, 403}:
            return "SOURCE_AUTHENTICATION_FAILED"
        if status == 429:
            return "SOURCE_RATE_LIMITED"
    name = type(value).__name__ if isinstance(value, BaseException) else str(value).split(":", 1)[0]
    if name in SOURCE_REASON_CODES:
        return name
    return {"TimeoutError": "SOURCE_TIMEOUT", "Timeout": "SOURCE_TIMEOUT",
        "ConnectTimeout": "SOURCE_TIMEOUT", "ReadTimeout": "SOURCE_TIMEOUT",
        "ConnectionError": "SOURCE_TRANSPORT_FAILURE", "OSError": "SOURCE_TRANSPORT_FAILURE",
        "PermissionError": "SOURCE_AUTHENTICATION_FAILED", "CircuitOpenError": "SOURCE_CIRCUIT_OPEN",
        "JSONDecodeError": "SOURCE_RESPONSE_INVALID", "ValueError": "SOURCE_RESPONSE_INVALID",
        "RuntimeError": "SOURCE_UNAVAILABLE"}.get(name, "SOURCE_ERROR_REDACTED")


def sanitize_source_errors(value):
    """Sanitize newly received and resumed caches before any durable write."""
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if key in {"reason", "native_reason", "error", "last_error"}:
                result[key] = source_reason_code(item) if item is not None else None
            elif key == "errors":
                errors = []
                for error in (item if isinstance(item, list) else [item]):
                    if isinstance(error, Mapping) and set(error) == {"kind", "code"} and error.get("kind") in {"errors", "error", "reason", "shape"}:
                        codes = error["code"] if isinstance(error["code"], list) else [error["code"]]
                        errors.append({"kind": error["kind"], "code": [code if isinstance(code, str) and code in {
                            "sources", "records", "symbols", "observations", "routes"} else source_reason_code(code) for code in codes]})
                    else:
                        errors.append(source_reason_code(error))
                result[key] = errors
            else:
                result[key] = sanitize_source_errors(item)
        return result
    if isinstance(value, list):
        return [sanitize_source_errors(item) for item in value]
    return value


def _source_errors(snapshot):
    """Retain bounded structural errors; never persist provider response bodies."""
    if not isinstance(snapshot, Mapping):
        return []
    errors = []
    for key in ("errors", "error", "reason"):
        values = snapshot.get(key)
        for value in values if isinstance(values, list) else ([values] if values else []):
            # Errors are labels only, not raw provider messages or payloads.
            errors.append({"kind": key, "code": [source_reason_code(value)]})
    for key in ("sources", "records", "symbols", "observations", "routes"):
        if key in snapshot and not isinstance(snapshot[key], list):
            errors.append({"kind": "shape", "code": ["INVALID_SOURCE_CONTAINER", key]})
    for item in _list(snapshot.get("sources")):
        if isinstance(item, Mapping) and str(item.get("source") or "").upper() == "BYMA":
            errors.extend(_source_errors({k: item[k] for k in ("errors", "error", "reason", "records") if k in item}))
    return errors


def _list(value):
    return value if isinstance(value, list) else []


def _numeric(value, *, positive=False):
    try:
        result = number(value)
        return result if not positive or result > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _fields(row, quote):
    values = {**row, **quote}
    fields, units = {}, {}
    price_unit = str(values.get("price_unit") or values.get("quote_unit") or "").upper()
    price = _numeric(_first(values, ("price", "last", "last_price", "tradePrice")), positive=True)
    if price is not None and price_unit in PRICE_UNITS:
        fields["price"], units["price"] = price, price_unit
    volume_unit = str(values.get("volume_unit") or "").upper()
    volume = _numeric(values.get("volume"))
    if volume is not None and volume_unit in VOLUME_UNITS:
        fields["volume"], units["volume"] = volume, volume_unit
    semantics = str(values.get("volume_semantics") or "").upper()
    cumulative = _numeric(values.get("cumulative_volume"))
    if semantics == "CUMULATIVE_SESSION" and volume_unit in VOLUME_UNITS:
        cumulative = cumulative if cumulative is not None else volume
        if cumulative is not None:
            fields["cumulative_volume"], units["cumulative_volume"] = cumulative, volume_unit
    spread = _numeric(values.get("spread_bps"))
    bid, ask = _numeric(values.get("bid"), positive=True), _numeric(values.get("ask"), positive=True)
    if spread is not None:
        fields["spread_bps"] = spread
    elif bid is not None and ask is not None and ask >= bid:
        fields["spread_bps"] = (ask - bid) / bid * 10000
    quantity_unit = str(values.get("depth_unit") or values.get("quantity_unit") or "").upper()
    depth = _numeric(values.get("depth"))
    if quantity_unit in VOLUME_UNITS:
        if depth is None:
            bid_size, ask_size = _numeric(values.get("bid_size")), _numeric(values.get("ask_size"))
            if bid_size is not None and ask_size is not None:
                depth = min(bid_size, ask_size)
        if depth is not None:
            fields["depth"], units["depth"] = depth, quantity_unit
    return fields, units


def source_observations(snapshot, *, source, as_of, max_age_seconds=120):
    """Return independent SHADOW rows and explicit partial/unavailable evidence.

    Numeric price/volume/depth fields require explicit units. Spread is a
    within-row dimensionless ratio. Missing, stale, future, ambiguous and failed
    rows are retained as rejected telemetry; the caller's catalogue is untouched.
    """
    canonical = SOURCE_ALIASES.get(str(source).strip().upper())
    if canonical is None:
        raise ValueError("SOURCE_NOT_AUDITED")
    cutoff = stamp(as_of)
    max_age = number(max_age_seconds)
    observations = []
    for row, envelope, family in _entries(snapshot, canonical):
        quote = row.get("quote") if isinstance(row.get("quote"), Mapping) else {}
        source_at = _time(_first(quote, PROVIDER_TIMES) or _first(row, PROVIDER_TIMES))
        received = _time(_first(row, RECEIVED_TIMES) or _first(envelope, RECEIVED_TIMES))
        fields, units = _fields(row, quote)
        try:
            exact = _canonical_identity(row, family)
            reason = None
        except ValueError:
            exact, reason = None, "EXACT_IDENTITY_AMBIGUOUS"
        age = (cutoff - source_at).total_seconds() if source_at else None
        state = str(row.get("state") or row.get("source_state") or "").upper()
        if reason is None:
            if state and state not in {"READY", "LIVE_FRESH", "FRESH", "OBSERVE_ONLY"}:
                reason = "SOURCE_UNAVAILABLE"
            elif source_at is None:
                reason = "PROVIDER_TIMESTAMP_MISSING_OR_AMBIGUOUS"
            elif age < 0:
                reason = "PROVIDER_TIMESTAMP_IN_FUTURE"
            elif received is None:
                reason = "RECEIVED_TIMESTAMP_MISSING_OR_AMBIGUOUS"
            elif received > cutoff:
                reason = "NOT_AVAILABLE_AT_CUTOFF"
            elif source_at > received:
                reason = "PROVIDER_TIMESTAMP_AFTER_RECEIPT"
            elif age > max_age:
                reason = "STALE_QUOTES"
            elif not fields:
                reason = "NUMERIC_FIELDS_OR_UNITS_UNVERIFIED"
        observations.append({"identity": exact, "source": canonical, "source_at": _iso(source_at),
                             "received_at": _iso(received), "useful": reason is None,
                             "fields": fields, "units": units, "reason": reason or "USEFUL_SHADOW_OBSERVATION",
                             "native_reason": source_reason_code(row.get("reason")), "age_seconds": age,
                             "source_path": row.get("source_path") or envelope.get("source_path"),
                             "provenance": row.get("provenance", envelope.get("provenance")),
                             "conflicts": row.get("conflicts", []),
                             "decision_effect": "OBSERVE_ONLY", "live_decision_authority": False})
    errors = _source_errors(snapshot)
    useful = sum(row["useful"] for row in observations)
    state = str(snapshot.get("status") or snapshot.get("state") or "") if isinstance(snapshot, Mapping) else ""
    return {"source": canonical, "as_of": cutoff.isoformat(), "observations": observations, "errors": errors,
            "counts": {"seen": len(observations), "useful": useful, "rejected": len(observations) - useful},
            "provider_state": state or "NO_VERIFICADO", "provider_available": None,
            "status": "PARTIAL_SOURCE_ERRORS" if errors else "SHADOW_EVIDENCE" if observations else "SOURCE_UNAVAILABLE",
            "decision_effect": "OBSERVE_ONLY", "live_decision_authority": False,
            "real_orders_sent": 0, "real_order_routes": "NOT_CALLED"}


def native_source_reports(observations, *, as_of):
    """Reference normalized caller evidence without reinterpreting its clocks/units.

    These rows have already reached the planner. They are telemetry only: a
    report must not cause a second ingestion or confer provider capability.
    Missing source/path/unit provenance remains explicitly unverified.
    """
    cutoff = stamp(as_of).isoformat()
    groups = {}
    for index, row in enumerate(observations):
        if index >= MAX_SOURCE_AUDIT_OBSERVATIONS:
            raise ValueError("SOURCE_AUDIT_OBSERVATION_CAPACITY_REACHED")
        if not isinstance(row, Mapping):
            raise ValueError("NATIVE_SOURCE_OBSERVATION_SHAPE_REQUIRED")
        source = str(row.get("source") or "NO_VERIFICADO")
        groups.setdefault(source, []).append(sanitize_source_errors(dict(row)))
        if len(groups) > MAX_SOURCE_AUDIT_REPORTS:
            raise ValueError("SOURCE_AUDIT_REPORT_CAPACITY_REACHED")
    reports = []
    for source, rows in sorted(groups.items()):
        useful = sum(row.get("useful") is True for row in rows)
        reports.append({"source": source, "as_of": cutoff, "observations": rows,
            "errors": [], "native_input": True,
            "counts": {"seen": len(rows), "useful": useful, "rejected": len(rows) - useful},
            "provider_state": "NORMALIZED_CALLER_EVIDENCE; capability NO_VERIFICADO",
            "provider_available": None, "status": "SHADOW_EVIDENCE",
            "decision_effect": "OBSERVE_ONLY", "live_decision_authority": False,
            "real_orders_sent": 0, "real_order_routes": "NOT_CALLED"})
    return reports


def audit_sources(snapshots=None, *, as_of=None, max_age_seconds=120, reports=None):
    """Document factual existing sources, optionally audit supplied offline caches."""
    if snapshots is not None and reports is not None:
        raise ValueError("SOURCE_AUDIT_SINGLE_REPORT_AUTHORITY_REQUIRED")
    if reports is not None:
        if as_of is None:
            raise ValueError("SNAPSHOT_AUDIT_CUTOFF_REQUIRED")
        cutoff = stamp(as_of).isoformat()
        if not isinstance(reports, list):
            raise ValueError("SOURCE_REPORT_LIST_REQUIRED")
        if len(reports) > MAX_SOURCE_AUDIT_REPORTS:
            raise ValueError("SOURCE_AUDIT_REPORT_CAPACITY_REACHED")
        if sum(len(report.get("observations", [])) for report in reports
               if isinstance(report, Mapping) and isinstance(report.get("observations"), list)) > MAX_SOURCE_AUDIT_OBSERVATIONS:
            raise ValueError("SOURCE_AUDIT_OBSERVATION_CAPACITY_REACHED")
        linked = {}
        seen_digests, report_keys = set(), set()
        observation_count = 0
        for index, report in enumerate(reports):
            if (not isinstance(report, Mapping) or not report.get("source") or
                    not isinstance(report.get("observations"), list)):
                raise ValueError("SOURCE_REPORT_SHAPE_REQUIRED")
            rows = report["observations"]
            for key, expected in {"decision_effect": "OBSERVE_ONLY", "live_decision_authority": False,
                    "real_orders_sent": 0, "real_order_routes": "NOT_CALLED"}.items():
                if key in report and (report[key] != expected or type(report[key]) is not type(expected)):
                    raise ValueError("SOURCE_REPORT_SAFETY_MISMATCH")
            observation_count += len(rows)
            if observation_count > MAX_SOURCE_AUDIT_OBSERVATIONS:
                raise ValueError("SOURCE_AUDIT_OBSERVATION_CAPACITY_REACHED")
            if any(not isinstance(row, Mapping) for row in rows):
                raise ValueError("SOURCE_OBSERVATION_SHAPE_REQUIRED")
            if any(row.get("live_decision_authority", False) is not False or
                   row.get("entry_authority", False) is not False for row in rows):
                raise ValueError("SOURCE_OBSERVATION_SAFETY_MISMATCH")
            report_hash = digest(report)
            report_key = (str(report["source"]), cutoff, report.get("native_input") is True)
            if report_hash in seen_digests:
                raise ValueError("SOURCE_REPORT_DUPLICATE")
            if report_key in report_keys:
                raise ValueError("SOURCE_REPORT_IDENTITY_CONFLICT")
            seen_digests.add(report_hash)
            report_keys.add(report_key)
            row_digests = [digest(row) for row in rows]
            if len(row_digests) != len(set(row_digests)):
                raise ValueError("SOURCE_OBSERVATION_DUPLICATE")
            useful = sum(row.get("useful") is True for row in rows)
            counts = report.get("counts")
            if (not isinstance(counts, dict) or any(type(value) is not int for value in counts.values())
                    or counts != {"seen": len(rows), "useful": useful, "rejected": len(rows) - useful}):
                raise ValueError("SOURCE_REPORT_COUNTS_MISMATCH")
            if stamp(report["as_of"]).isoformat() != cutoff:
                raise ValueError("SOURCE_REPORT_CUTOFF_MISMATCH")
            # A JSON pointer and its digest bind the existing report, retaining
            # all rows/errors/clocks/conflicts in one authoritative model.
            linked[str(index)] = {"source": report["source"], "report_pointer": f"/source_reports/{index}",
                "report_digest": digest(report), "status": report["status"],
                "counts": dict(report["counts"]), "provider_available": report.get("provider_available"),
                "source_paths": sorted({str(row["source_path"]) for row in rows if row.get("source_path")}),
                "path_provenance_missing": sum(not row.get("source_path") for row in rows),
                "runtime_ingestion": report.get("runtime_ingestion"),
                "evidence_authority": "SOURCE_REPORT_REFERENCE; no independent normalization"}
        return {"schema": "RC6_SOURCE_AUDIT_LINKED_V2", "as_of": cutoff,
            "sources": SOURCE_MANIFEST, "snapshots": linked,
            "source_reports_pointer": "/source_reports", "source_reports_digest": digest(reports),
            "source_report_count": len(reports),
            "status": "OBSERVED_SOURCE_REPORTS" if reports else "NO_SOURCE_REPORTS",
            "sufficiently_fresh_transversal_radar": "NO_VERIFICADO",
            "decision_effect": "OBSERVE_ONLY", "live_decision_authority": False,
            "real_orders_sent": 0, "real_order_routes": "NOT_CALLED"}
    reports = {}
    if snapshots:
        if as_of is None:
            raise ValueError("SNAPSHOT_AUDIT_CUTOFF_REQUIRED")
        reports = {name: source_observations(value, source=name, as_of=as_of,
                                           max_age_seconds=max_age_seconds)
                   for name, value in snapshots.items()}
    return {"schema": "WS_PERF_03_SOURCE_AUDIT_V1", "sources": SOURCE_MANIFEST,
            "snapshots": reports, "sufficiently_fresh_transversal_radar": "NO_VERIFICADO",
            "decision_effect": "OBSERVE_ONLY", "live_decision_authority": False,
            "real_orders_sent": 0, "real_order_routes": "NOT_CALLED"}
