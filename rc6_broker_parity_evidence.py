"""PAPER-only broker-parity evidence integration for RC6.

This module is deliberately offline: it consumes sanitized structured captures
and exact PPI catalog rows, then writes only to a caller-supplied evidence store.
It cannot authenticate, validate/place orders, accept DDJJ, or mutate runtime.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable

import cp_contract_evidence_v2_hf6 as evidence_v2
import cq_family_contract_rules_hf6 as rules
from rc6_multisource_discovery import canonical_family, canonical_market, canonical_settlement


SCHEMA = "rc6-broker-parity-evidence-v1"
REAL_ROUTES_USED = ()
DOM_SOURCES = frozenset({"PPI_AUTHENTICATED_DOM", "IOL_AUTHENTICATED_DOM"})
EXECUTOR_READY = frozenset({
    "ACCIONES", "CEDEARS", "ETF", "ETFS", "BONOS", "LETRAS",
    "ON", "OBLIGACIONES", "OPCIONES", "CAUCIONES",
    "FCI", "FCI_LOCAL", "FUTUROS",
})


@dataclass(frozen=True)
class FullIdentity:
    family: str
    ticker: str
    market: str
    currency: str
    settlement: str

    @classmethod
    def from_mapping(cls, row):
        family = canonical_family(row.get("instrument_type") or row.get("family"))
        return cls(family, str(row.get("ticker") or "").upper(),
                   canonical_market(row.get("market")),
                   str(row.get("currency") or "").upper(),
                   canonical_settlement(row.get("settlement"), family))

    def valid(self):
        return all(value not in {"", "*", "UNKNOWN", "NO_VERIFICADO"}
                   for value in self.__dict__.values())


def _primary_ppi(row):
    raw = row.get("raw")
    if not isinstance(raw, dict):
        try:
            raw = json.loads(row.get("metadata_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = {}
    source = str(raw.get("_discovery_source") or "").upper()
    settlement_source = str(row.get("settlement_source") or "").upper()
    return source in {"PPI_PRIMARY", "LEGACY_CATALOG"} or settlement_source in {
        "PPI_FIELD", "REQUEST_CANDIDATE"}


def _map_iol_contract(row):
    contract = row.get("financial_contract_v17")
    contract = contract if isinstance(contract, dict) else {}
    family = canonical_family(row.get("instrument_type"))
    mapped = {
        "market": canonical_market(row.get("market")),
        "currency": str(row.get("currency") or "").upper(),
        "settlement": canonical_settlement(row.get("settlement"), family),
        "quantity_min": contract.get("minimum_quantity"),
        "quantity_step": contract.get("quantity_step"),
        "cash_multiplier": contract.get("cash_multiplier"),
    }
    if family in {"BONOS", "LETRAS", "ON", "OBLIGACIONES"}:
        detail = contract.get("fixed_income_evidence") or row.get("fixed_income_analytics") or {}
        mapped.update({
            "price_quote_unit": detail.get("quote_basis_nominal"),
            "maturity_date": detail.get("maturity_date"),
        })
    elif family == "OPCIONES":
        detail = row.get("option_chain_evidence") or {}
        multiplier = contract.get("cash_multiplier")
        mapped.update({
            "underlying": contract.get("underlying") or detail.get("underlying"),
            "put_call": contract.get("option_right") or {
                "C": "CALL", "V": "PUT", "CALL": "CALL", "PUT": "PUT",
            }.get(str(detail.get("option_type") or "").upper()),
            "strike": contract.get("strike") or detail.get("strike_price"),
            "expiry_at": contract.get("expires_at") or detail.get("expires_at"),
            "lot_size": multiplier or detail.get("contract_lot"),
            "contract_multiplier": multiplier or detail.get("contract_lot"),
            # cash_multiplier needs both lot and premium basis; a partial chain
            # must not invent it merely because the exchange publishes a lot.
            "cash_multiplier": multiplier,
            "premium_basis": contract.get("premium_basis"),
        })
    result = {key: value for key, value in mapped.items()
              if value not in (None, "", [], {})}
    return result or None


def _first(row, *names):
    for name in names:
        if isinstance(row, dict) and row.get(name) not in (None, "", [], {}):
            return row[name]
    return None


def _ppi_index(ppi_catalog):
    return {FullIdentity.from_mapping(row): row for row in ppi_catalog
            if FullIdentity.from_mapping(row).valid() and _primary_ppi(row)}


def _fci_records(payload, ppi):
    accepted, blocked = [], []
    source_state = str((payload.get("section_states") or {}).get("fci")
                       or payload.get("cache_state") or "UNKNOWN")
    capture_timestamp = (payload.get("section_observed_at") or {}).get("fci") or payload.get("refreshed_at")
    for row in payload.get("fci", []):
        if not isinstance(row, dict):
            continue
        candidate = {
            "ticker": _first(row, "asset", "ticker", "symbol"),
            "instrument_type": "FCI", "market": _first(row, "market") or "FCI",
            "currency": _first(row, "currency", "currency_code"),
            "settlement": _first(row, "settlement", "term") or "INMEDIATA",
        }
        identity = FullIdentity.from_mapping(candidate)
        if identity not in ppi:
            blocked.append({"identity": identity.__dict__, "blocker": "BLOCKED_IDENTITY",
                            "source_section": "fci"})
            continue
        evidence = {
            "market": identity.market, "currency": identity.currency,
            "settlement": identity.settlement,
            "fund_description": _first(row, "description", "name"),
            "iol_operable_observed": _first(row, "operable"),
            "provider_timestamp": None, "capture_timestamp": capture_timestamp,
            "freshness_basis": "CAPTURE_TIMESTAMP_STATIC_ONLY",
        }
        accepted.append({**identity.__dict__, "source_class": "IOL_STRUCTURED_API",
                         "source_ref": f"iol-mcp-cache:{source_state}:get_fci_funds:{identity.ticker}",
                         "observed_at": capture_timestamp,
                         "evidence": {k: v for k, v in evidence.items() if v is not None}})
    return accepted, blocked


def _rate_fraction(row):
    explicit = _first(row, "annual_rate_fraction", "rate_fraction")
    if explicit is not None:
        return explicit, None
    percent = _first(row, "tna", "annual_rate", "rate")
    try:
        value = Decimal(str(percent))
    except (InvalidOperation, TypeError, ValueError):
        return None, None
    return str(value / 100), "provider percent / 100"


def _caucion_maturity(value):
    text = str(value or "").strip()
    if len(text) == 10:
        return text + "T11:00:00-03:00"
    return value


def _ppi_caucion_identity(ppi, currency, term_days, side):
    matches = []
    for identity, row in ppi.items():
        if identity.family != "CAUCIONES" or identity.currency != currency:
            continue
        raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
        observed_term = _first(row, "term_days") or _first(raw, "term_days", "plazo_dias")
        observed_side = str(_first(row, "side") or _first(raw, "side", "caucion_type") or "").upper()
        if str(observed_term) == str(term_days) and observed_side == side:
            matches.append(identity)
    return matches[0] if len(matches) == 1 else None


def _caucion_records(payload, ppi):
    accepted, blocked = [], []
    for currency, rows in (payload.get("cauciones") or {}).items():
        source_state = str((payload.get("section_states") or {}).get("caucion:"+str(currency))
                           or payload.get("cache_state") or "UNKNOWN")
        capture_timestamp = ((payload.get("section_observed_at") or {}).get("caucion:"+str(currency))
                             or payload.get("refreshed_at"))
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            term_days = _first(row, "term_days", "term", "days", "plazo")
            side = str(_first(row, "side", "caucion_type") or "COLOCADORA").upper()
            identity = _ppi_caucion_identity(ppi, str(currency).upper(), term_days, side)
            if identity is None:
                blocked.append({"identity": {"family": "CAUCIONES", "ticker": "UNBOUND",
                                "market": "BYMA", "currency": str(currency).upper(),
                                "settlement": "UNBOUND"}, "blocker": "BLOCKED_IDENTITY",
                                "source_section": "cauciones", "term_days": term_days})
                continue
            provider_at = _first(row, "provider_timestamp", "quoted_at", "timestamp")
            live_at = provider_at or capture_timestamp
            rate, derivation = _rate_fraction(row)
            minimum = _first(row, "minimum_principal", "minimum_amount", "min_amount")
            evidence = {
                "market": identity.market, "currency": identity.currency,
                "settlement": identity.settlement, "side": side,
                "term_days": term_days,
                "minimum_principal": minimum,
                "maturity_at": _caucion_maturity(_first(row, "maturity_at", "due_date", "maturity")),
                "start_date": str(capture_timestamp or "")[:10],
                "day_count_basis": 365,
                "fee_payment": "UPFRONT",
                "paper_fill_policy": "CONSERVATIVE_NOTIONAL_CAP",
                "paper_notional_cap": minimum,
                "paper_principal_step": "0.01",
                "provider_timestamp": live_at,
                "capture_timestamp": capture_timestamp,
                "freshness_basis": "PROVIDER_TIMESTAMP" if provider_at else "LIVE_RESPONSE_CAPTURE",
            }
            # A successful read-only rate response is a live observation when
            # the provider omits a separate quote timestamp.
            if live_at:
                evidence.update({
                    "annual_rate_fraction": rate,
                    "quoted_at": live_at,
                    "operable": _first(row, "operable") is not False,
                    "market_session_state": _first(row, "market_session_state", "session_state") or "OPEN",
                })
                if derivation:
                    evidence["derivation_rule"] = derivation
            accepted.append({**identity.__dict__, "source_class": "IOL_STRUCTURED_API",
                             "source_ref": f"iol-mcp-cache:{source_state}:get_caucion_rates:{currency}:{term_days}",
                             "observed_at": provider_at or capture_timestamp,
                             "evidence": {k: v for k, v in evidence.items() if v is not None}})
    return accepted, blocked


def iol_structured_records(payload: dict, ppi_catalog: Iterable[dict]):
    """Bind an IOL cache only to an already-known exact PPI identity."""
    if not isinstance(payload, dict) or payload.get("schema") != "rc6-iol-family-reference-v1":
        raise ValueError("IOL_CACHE_SCHEMA_INVALID")
    ppi = _ppi_index(ppi_catalog)
    accepted, blocked = [], []
    capture_timestamp = payload.get("refreshed_at")
    cache_state = str(payload.get("cache_state") or "UNKNOWN")
    for row in payload.get("records", []):
        if not isinstance(row, dict):
            continue
        identity = FullIdentity.from_mapping(row)
        if identity not in ppi:
            blocked.append({"identity": identity.__dict__, "blocker": "BLOCKED_IDENTITY"})
            continue
        mapped = _map_iol_contract(row)
        if not mapped:
            blocked.append({"identity": identity.__dict__, "blocker": "BLOCKED_DATA"})
            continue
        # The collector timestamp is capture time.  Static terms may be stored
        # with that provenance, but it must not certify a live quote/session.
        row_observed = row.get("observed_at") or capture_timestamp
        mapped.update({
            "provider_timestamp": None,
            "capture_timestamp": row_observed,
            "freshness_basis": "CAPTURE_TIMESTAMP_STATIC_ONLY",
        })
        accepted.append({
            **identity.__dict__, "source_class": "IOL_STRUCTURED_API",
            "source_ref": f"iol-mcp-cache:{cache_state}:{payload.get('schema')}:{identity.ticker}",
            "observed_at": row_observed, "evidence": mapped,
        })
    fci_accepted, fci_blocked = _fci_records(payload, ppi)
    caucion_accepted, caucion_blocked = _caucion_records(payload, ppi)
    return {"accepted": accepted + fci_accepted + caucion_accepted,
            "blocked": blocked + fci_blocked + caucion_blocked}


def ingest_iol_structured_cache(store, payload, ppi_catalog):
    result = iol_structured_records(payload, ppi_catalog)
    written = []
    for record in result["accepted"]:
        written.append(evidence_v2.record_snapshot(
            store, family=record["family"], ticker=record["ticker"],
            market=record["market"], currency=record["currency"],
            settlement=record["settlement"], source_class=record["source_class"],
            source_ref=record["source_ref"], observed_at=record["observed_at"],
            evidence=record["evidence"],
        ))
    return {"schema": SCHEMA, "written": written, "blocked": result["blocked"],
            "real_routes_used": list(REAL_ROUTES_USED)}


def scraping_fallback_allowed(field, observations, required_fields):
    """DOM is eligible only for an indispensable unresolved PAPER field."""
    if field not in set(required_fields):
        return False
    non_dom = [item for item in observations
               if item.get("field") == field
               and item.get("value") not in (None, "", [], {})
               and item.get("source_class") not in DOM_SOURCES]
    return not non_dom


def classify_instrument(family, records, *, profile="OPEN", now=None):
    family = canonical_family(family)
    result = rules.evaluate_family(family, records, profile=profile, now=now)
    status = result["status"]
    missing_contract = set(result.get("missing_contract", []))
    event_fields = (set(rules.EVENT_CONDITIONAL_FIELDS.get(family, ())) |
                    set(rules.EVENT_DYNAMIC_FIELDS.get(family, ())))
    blocker_axes = []
    if status in {"MISSING_CONTRACT", "CONFLICT"} or result.get("missing_contract"):
        blocker_axes.append("BLOCKED_DATA")
    if (status in {"MISSING_DYNAMIC", "STALE_DYNAMIC"}
            or result.get("missing_dynamic") or result.get("stale_dynamic")):
        blocker_axes.append("BLOCKED_DYNAMIC_DATA")
    if family not in EXECUTOR_READY:
        blocker_axes.append("BLOCKED_EXECUTOR")
    if (str(profile).upper() in {"EVENT", "FULL"} and missing_contract
            and missing_contract <= event_fields):
        blocker_axes.append("BLOCKED_EVENT_LIFECYCLE")

    if (str(profile).upper() in {"EVENT", "FULL"} and missing_contract
            and missing_contract <= event_fields):
        blocker = "BLOCKED_EVENT_LIFECYCLE"
    elif status == "MISSING_CONTRACT":
        blocker = "BLOCKED_DATA"
    elif status in {"MISSING_DYNAMIC", "STALE_DYNAMIC"}:
        blocker = "BLOCKED_DYNAMIC_DATA"
    elif status == "CONFLICT":
        blocker = "BLOCKED_DATA"
    elif family not in EXECUTOR_READY:
        blocker = "BLOCKED_EXECUTOR"
    elif profile in {"EVENT", "FULL"} and result.get("event_missing"):
        blocker = "BLOCKED_EVENT_LIFECYCLE"
    elif status == "READY_PAPER_CANDIDATE":
        blocker = "READY_PAPER_CANDIDATE"
    else:
        blocker = "BLOCKED_DATA"
    if blocker == "READY_PAPER_CANDIDATE":
        blocker_axes.append(blocker)
    return {**result, "blocker_class": blocker,
            "blocker_axes": list(dict.fromkeys(blocker_axes or [blocker])),
            "real_money_authorized": False, "real_routes_used": []}
