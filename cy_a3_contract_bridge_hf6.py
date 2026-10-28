"""Normalize A3/Primary instrument metadata into Contract Evidence v2.

Pure transformation/storage bridge: no authentication and no order capability.
"""
from __future__ import annotations

RC4_MODULE_ROLE = 'DEFERRED_INTEGRATION'
RC4_MODULE_ROLE_REASON = 'Bridge A3 Primary contractual conservado; A3 Primary privado no está autenticado y no se fuerza wiring.'

from typing import Any

from cp_contract_evidence_v2_hf6 import record_snapshot

CFI_FAMILY = {
    "FXXXSX": "FUTUROS",
    "OCAFXS": "OPCIONES",
    "OPAFXS": "OPCIONES",
    "OCASPS": "OPCIONES",
    "OPASPS": "OPCIONES",
    "DBXXFR": "ON",
}

# Fields are copied only when A3 actually supplies them.
CONTRACT_FIELDS = (
    "cficode",
    "maturityDate",
    "currency",
    "settlType",
    "contractMultiplier",
    "minPriceIncrement",
    "minTradeVol",
    "maxTradeVol",
    "tickSize",
    "roundLot",
    "priceConvertionFactor",
    "instrumentPricePrecision",
    "instrumentSizePrecision",
    "securityType",
    "securityDescription",
    "orderTypes",
    "timesInForce",
    "tickPriceRanges",
    "lowLimitPrice",
    "highLimitPrice",
)


def _instrument_id(detail: dict[str, Any]) -> tuple[str, str]:
    iid = detail.get("instrumentId") or {}
    market = str(iid.get("marketId") or (detail.get("segment") or {}).get("marketId") or "").upper()
    symbol = str(iid.get("symbol") or detail.get("symbol") or detail.get("securityDescription") or "").strip()
    return market, symbol


def normalize_contract(detail: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(detail, dict) or not detail:
        raise ValueError("A3_CONTRACT_EMPTY")
    market, symbol = _instrument_id(detail)
    if not market or not symbol:
        raise ValueError("A3_CONTRACT_IDENTITY_MISSING")
    cfi = str(detail.get("cficode") or "").upper()
    family = CFI_FAMILY.get(cfi, "UNKNOWN")
    evidence = {field: detail[field] for field in CONTRACT_FIELDS
                if detail.get(field) not in (None, "", [], {})}
    segment = detail.get("segment")
    if isinstance(segment, dict) and segment:
        evidence["segment"] = {
            k: segment[k] for k in ("marketId", "marketSegmentId")
            if segment.get(k) not in (None, "")
        }
    evidence["market"] = market
    evidence["symbol"] = symbol
    evidence["family"] = family
    return {
        "family": family,
        "ticker": symbol,
        "market": market,
        "evidence": evidence,
    }


def record_a3_contract(store, detail: dict[str, Any], *,
                       source_ref: str = "A3_PRIMARY:/rest/instruments/detail",
                       observed_at: str | None = None) -> dict[str, Any]:
    normalized = normalize_contract(detail)
    if normalized["family"] == "UNKNOWN":
        return {
            **normalized,
            "status": "UNSUPPORTED_CFI_HOLD",
            "recorded": False,
        }
    result = record_snapshot(
        store,
        family=normalized["family"],
        ticker=normalized["ticker"],
        market=normalized["market"],
        source_class="A3_PRIMARY_API",
        source_ref=source_ref,
        evidence=normalized["evidence"],
        observed_at=observed_at,
    )
    return {
        **normalized,
        **result,
        "recorded": True,
    }


def contract_field_presence(detail: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_contract(detail)
    ev = normalized["evidence"]
    futures_required = (
        "maturityDate",
        "contractMultiplier",
        "minPriceIncrement",
        "minTradeVol",
        "tickSize",
        "roundLot",
    )
    option_required = futures_required + ("currency",)
    required = futures_required if normalized["family"] == "FUTUROS" else option_required
    missing = [field for field in required if ev.get(field) in (None, "", [], {})]
    return {
        "family": normalized["family"],
        "ticker": normalized["ticker"],
        "required": list(required),
        "missing": missing,
        "complete_for_contract_candidate": not missing,
    }
