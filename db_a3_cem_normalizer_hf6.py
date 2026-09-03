"""Normalize official A3 CEM reference/history data for HF6 v2.

Important terminology guard: CEM ClosingPriceDto.settlement is a settlement/
adjustment PRICE, not Porota's settlement-term identity. It is stored only as
metadata and can never populate History Store `settlement` automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cu_history_store_v2_hf6 import Candle

CEM_HISTORY_SOURCE = "A3_CEM_CLOSING"
CEM_EXECUTION_ALLOWED = False


@dataclass(frozen=True)
class CEMSymbol:
    symbol: str
    family: str
    cfi_code: str
    maturity_date: str | None
    strike_price: float | None
    underlying: str | None
    product: str | None
    security_type: str | None
    segment: str | None
    currency: str | None
    maturity_month_year: str | None
    description: str | None
    legs: tuple[str, ...]


def family_from_cfi(cfi_code: str) -> str:
    code = str(cfi_code or "").strip().upper()
    if code.startswith("F"):
        return "FUTUROS"
    if code.startswith("O"):
        return "OPCIONES"
    return "UNKNOWN"


def normalize_symbol(row: dict[str, Any]) -> CEMSymbol:
    if not isinstance(row, dict):
        raise ValueError("CEM_SYMBOL_NOT_OBJECT")
    symbol = str(row.get("symbol") or "").strip().upper()
    if not symbol:
        raise ValueError("CEM_SYMBOL_MISSING")
    cfi = str(row.get("cfiCode") or "").strip().upper()
    strike = row.get("strikePrice")
    return CEMSymbol(
        symbol=symbol,
        family=family_from_cfi(cfi),
        cfi_code=cfi,
        maturity_date=str(row.get("maturityDate")) if row.get("maturityDate") else None,
        strike_price=float(strike) if strike is not None else None,
        underlying=str(row.get("underlying")) if row.get("underlying") else None,
        product=str(row.get("product")) if row.get("product") else None,
        security_type=str(row.get("securityType")) if row.get("securityType") else None,
        segment=str(row.get("segment")) if row.get("segment") else None,
        currency=str(row.get("currency")) if row.get("currency") else None,
        maturity_month_year=str(row.get("maturityMonthYear")) if row.get("maturityMonthYear") else None,
        description=str(row.get("description")) if row.get("description") else None,
        legs=tuple(str(x) for x in (row.get("legs") or [])),
    )


def normalize_symbols(payload: dict[str, Any]) -> list[CEMSymbol]:
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("CEM_SYMBOLS_SHAPE_INVALID")
    result=[]
    for row in rows:
        try:
            result.append(normalize_symbol(row))
        except (TypeError, ValueError):
            continue
    return result


def closing_to_candle(row: dict[str, Any], *, instrument_type: str,
                      market: str, settlement_identity: str) -> Candle:
    """Convert CEM official EOD OHLC to History Store v2.

    `market` and `settlement_identity` must come from verified Porota contract
    identity. They are deliberately NOT inferred from CEM's numeric
    `settlement` field, which is a settlement/adjustment price.
    """
    if CEM_EXECUTION_ALLOWED:
        raise RuntimeError("CEM_EXECUTION_INVARIANT_BROKEN")
    market = str(market or "").strip().upper()
    settlement_identity = str(settlement_identity or "").strip().upper()
    if not market or market == "UNKNOWN":
        raise ValueError("CEM_HISTORY_MARKET_IDENTITY_REQUIRED")
    if not settlement_identity or settlement_identity == "UNKNOWN":
        raise ValueError("CEM_HISTORY_SETTLEMENT_IDENTITY_REQUIRED")
    if not isinstance(row, dict):
        raise ValueError("CEM_CLOSING_NOT_OBJECT")
    symbol = str(row.get("symbol") or "").strip().upper()
    date_time = str(row.get("dateTime") or "")
    if not symbol or not date_time:
        raise ValueError("CEM_CLOSING_IDENTITY_INCOMPLETE")
    try:
        day = datetime.fromisoformat(date_time.replace("Z", "+00:00")).date().isoformat()
    except ValueError as exc:
        raise ValueError("CEM_CLOSING_DATE_INVALID") from exc
    close = row.get("close")
    if close in (None, ""):
        raise ValueError("CEM_CLOSING_CLOSE_MISSING")
    metadata = {
        "cem_settlement_price": row.get("settlement"),
        "open_interest": row.get("openInterest"),
        "open_interest_change": row.get("openInterestChange"),
        "units_open_interest": row.get("unitsOpenInterest"),
        "units_open_interest_change": row.get("unitsOpenInterestChange"),
        "trade_count": row.get("tradeCount"),
        "change": row.get("change"),
        "change_percent": row.get("changePercent"),
        "implied_rate": row.get("impliedRate"),
        "previous_close": row.get("previousClose"),
        "units_volume": row.get("unitsVolume"),
        "option_type": row.get("optionType"),
        "strike_price": row.get("strikePrice"),
        "underlying": row.get("underlying"),
        "product": row.get("product"),
        "source_schema": "CEM2 ClosingPriceDto",
    }
    return Candle(
        symbol=symbol,
        instrument_type=str(instrument_type or "").upper(),
        market=market,
        settlement=settlement_identity,
        date=day,
        open=None if row.get("open") is None else float(row.get("open")),
        high=None if row.get("high") is None else float(row.get("high")),
        low=None if row.get("low") is None else float(row.get("low")),
        close=float(close),
        volume=None if row.get("volume") is None else float(row.get("volume")),
        source=CEM_HISTORY_SOURCE,
        adjusted=False,
        observed_at=None,
        metadata=metadata,
    )


def normalize_tick(row: dict[str, Any]) -> dict[str, Any]:
    """Validate a CEM TickByTickDto without creating a trading price source."""
    if not isinstance(row, dict):
        raise ValueError("CEM_TICK_NOT_OBJECT")
    symbol=str(row.get("symbol") or "").strip().upper()
    at=str(row.get("dateTime") or "")
    price=row.get("price")
    if not symbol or not at or price in (None, ""):
        raise ValueError("CEM_TICK_INCOMPLETE")
    datetime.fromisoformat(at.replace("Z", "+00:00"))
    value=float(price)
    if value <= 0:
        raise ValueError("CEM_TICK_PRICE_NONPOSITIVE")
    volume=None if row.get("volume") is None else float(row.get("volume"))
    if volume is not None and volume < 0:
        raise ValueError("CEM_TICK_VOLUME_NEGATIVE")
    return {"symbol":symbol,"dateTime":at,"price":value,"volume":volume,
            "source":"A3_CEM_TICK","execution_allowed":False}


def assert_cem_normalizer_invariants() -> None:
    if CEM_EXECUTION_ALLOWED is not False:
        raise AssertionError("CEM execution must remain disabled")
