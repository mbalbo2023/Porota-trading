#!/usr/bin/env python3
"""One-instrument authenticated IOL API proof, read-only.

PPI remains primary. This probe only proves whether the existing IOL OAuth/MCP
market-data path returns structured data for one instrument and compares it
when a PPI primary snapshot is available. It never calls execution tools.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from iol_mcp_readonly_adapter_rc6 import OAuthStoreReadOnlyMCP

SYMBOL = os.getenv("POROTA_IOL_PROBE_SYMBOL", "YPFD").upper()
MARKET = os.getenv("POROTA_IOL_PROBE_MARKET", "BCBA")
TERM = os.getenv("POROTA_IOL_PROBE_TERM", "t1")
OUTPUT = Path(os.getenv(
    "POROTA_IOL_PROBE_OUTPUT",
    "/opt/porota-trading/data/market/rc6_iol_api_probe_latest.json",
))
PRIMARY_CANDIDATES = (
    os.getenv("POROTA_PRIMARY_LAST_CACHE_PATH", "").strip(),
    "/opt/porota-trading/data/market/primary_last.json",
    "/app/data/market/primary_last.json",
)


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _quote_last(payload: Any) -> float | None:
    if not isinstance(payload, dict):
        return None
    for key in ("last", "unit_price", "price", "last_price"):
        value = _number(payload.get(key))
        if value is not None:
            return value
    trade = payload.get("trade")
    if isinstance(trade, dict):
        for key in ("lot_price", "price", "last"):
            value = _number(trade.get(key))
            if value is not None:
                return value
    return None


def _safe_quote(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    result: dict[str, Any] = {}
    for key in ("last", "unit_price", "price", "last_price", "bid", "ask",
                "variation", "variation_pct", "cash_volume",
                "provider_observed_at", "timestamp"):
        if key in payload and isinstance(payload[key], (str, int, float, bool)):
            result[key] = payload[key]
    trade = payload.get("trade")
    if isinstance(trade, dict):
        for key in ("lot_price", "price", "last"):
            if key in trade and isinstance(trade[key], (str, int, float, bool)):
                result["trade_" + key] = trade[key]
    return result


def _safe_metadata(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    return {
        key: payload.get(key)
        for key in ("type", "asset_type", "currency", "units_per_lot")
        if isinstance(payload.get(key), (str, int, float, bool))
    }


def _ppi_last() -> tuple[float | None, dict[str, Any]]:
    for raw_path in PRIMARY_CANDIDATES:
        if not raw_path:
            continue
        try:
            payload = json.loads(Path(raw_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        values = payload.get("quotes_by_symbol") or payload.get("last_by_symbol") if isinstance(payload, dict) else {}
        if not isinstance(values, dict):
            continue
        raw = values.get(SYMBOL) or values.get(SYMBOL.upper())
        if isinstance(raw, dict):
            value = _number(raw.get("last") or raw.get("unit_price") or raw.get("price"))
            observed = raw.get("provider_observed_at") or raw.get("observed_at")
        else:
            value, observed = _number(raw), None
        if value is not None:
            return value, {"path": raw_path, "observed_at": observed}
    return None, {"status": "PPI_PRIMARY_SNAPSHOT_UNAVAILABLE"}


def _comparison(ppi_last: float | None, iol_last: float | None) -> dict[str, Any]:
    if ppi_last is None or iol_last is None:
        return {"status": "COMPARISON_INSUFFICIENT_EVIDENCE"}
    difference_pct = 0.0 if ppi_last == iol_last else abs(iol_last - ppi_last) / abs(ppi_last) * 100
    return {
        "status": "MATCH" if difference_pct <= 2.0 else "DIVERGENCE",
        "ppi_last": ppi_last,
        "iol_last": iol_last,
        "difference_pct": round(difference_pct, 6),
        "tolerance_pct": 2.0,
    }


def main() -> int:
    result: dict[str, Any] = {
        "schema": "rc6-iol-api-one-instrument-proof-v1",
        "instrument": {"symbol": SYMBOL, "market": MARKET, "term": TERM},
        "source_order": "PPI_PRIMARY_IOL_COMPLEMENTARY",
        "decision_effect": "OBSERVE_ONLY",
        "real_money_authorized": False,
    }
    client = OAuthStoreReadOnlyMCP()
    try:
        metadata = client.call("get_asset_info", {"symbol": SYMBOL, "market": MARKET})
        quote = client.call("get_asset_quote", {"symbol": SYMBOL, "market": MARKET, "term": TERM})
        iol_last = _quote_last(quote)
        result.update({
            "api_status": "API_STRUCTURED_DATA" if iol_last is not None else "API_REACHABLE_NO_QUOTE",
            "iol_quote": _safe_quote(quote),
            "iol_metadata": _safe_metadata(metadata),
        })
    except Exception as exc:
        iol_last = None
        result.update({
            "api_status": "API_UNAVAILABLE",
            "error_class": type(exc).__name__,
            "error_code": str(exc)[:160],
        })
    ppi_last, ppi_evidence = _ppi_last()
    result["ppi_primary"] = {"last": ppi_last, **ppi_evidence}
    result["comparison"] = _comparison(ppi_last, iol_last)
    result["api_proof"] = result["api_status"] == "API_STRUCTURED_DATA"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(OUTPUT.suffix + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.chmod(temporary, 0o644)
    temporary.replace(OUTPUT)
    print("IOL_API_ONE_INSTRUMENT_PROOF=" + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["api_proof"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
