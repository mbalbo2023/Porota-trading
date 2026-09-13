#!/usr/bin/env python3
"""Validate and persist PPI Web FULL_OHLC evidence without synthetic repair.

Source precedence is enforced by history_store_v2: PPI API outranks PPI Web,
which outranks IOL. This module never rewrites an API canonical candle with a
lower-authority PPI Web candle.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import cp_history_ingest_policy_hf6 as policy
import cq_history_attempt_ledger_hf6 as ledger
import cu_history_store_v2_hf6 as history_v2
from cv_history_store_adapter_hf6 import default_history_store

SOURCE = "PPI_WEB_HISTORY"


def _date_bound(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()


def _as_of(value) -> datetime:
    if value in (None, ""):
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ingest_web_rows(store, *, symbol: str, instrument_type: str, market: str,
                    settlement: str, rows, requested_from=None,
                    requested_to=None, attempted_at=None, history_store=None) -> dict:
    attempted_dt = _as_of(attempted_at)
    attempted_iso = attempted_dt.isoformat()
    date_from = _date_bound(requested_from)
    date_to = _date_bound(requested_to)
    market = str(market or "").strip().upper()
    settlement = str(settlement or "").strip().upper()
    if not market or market == "UNKNOWN":
        raise ValueError("HISTORY_MARKET_UNVERIFIED")
    if not settlement or settlement == "UNKNOWN":
        raise ValueError("HISTORY_SETTLEMENT_UNVERIFIED")

    result = policy.validate_provider_history(
        rows, as_of=attempted_dt, date_from=date_from, date_to=date_to,
    )
    hstore = history_store or default_history_store()
    candles = [
        history_v2.Candle(
            symbol=str(symbol).upper(),
            instrument_type=str(instrument_type).upper(),
            market=market,
            settlement=settlement,
            date=str(row["date"])[:10],
            open=float(row["openingPrice"]), high=float(row["max"]),
            low=float(row["min"]), close=float(row["price"]),
            volume=float(row["volume"]), source=SOURCE, adjusted=False,
            observed_at=attempted_iso,
            metadata={
                "ready_paper_implication": "NONE",
                "execution_price_implication": "NONE",
                "history_quality": "FULL_OHLC",
                "web_residual_completion": True,
            },
        )
        for row in result.valid_rows
    ]
    stored = history_v2.append_many(hstore, candles) if candles else {
        "versions_appended": 0, "canonical_updates": 0, "protected_by_precedence": 0,
    }

    ledger.init_schema(store)
    attempt = ledger.HistoryAttempt(
        symbol=str(symbol).upper(), instrument_type=str(instrument_type).upper(),
        settlement=settlement, source=SOURCE,
        requested_from=date_from.isoformat() if date_from else None,
        requested_to=date_to.isoformat() if date_to else None,
        attempted_at=attempted_iso,
        completed_at=datetime.now(timezone.utc).isoformat(),
        provider_rows=result.provider_rows, valid_rows=result.valid_count,
        rejected_rows=result.rejected_count, state=result.storage_quality,
        detail=(f"market={market}; context={result.context_state}; "
                f"ratio={result.valid_ratio:.6f}; no synthesis; PPI API precedence protected"),
        raw_body_hash=ledger.body_hash(rows),
        metadata={
            "market": market, "context_state": result.context_state,
            "valid_ratio": result.valid_ratio, "first_date": result.first_date,
            "last_date": result.last_date, "full_ohlc_rows": result.valid_count,
            "ready_paper_implication": "NONE", "execution_price_implication": "NONE",
            "history_store": "history_canonical_v2", "source": SOURCE,
        },
    )
    attempt_id = ledger.append_attempt(store, attempt)
    return {
        "attempt_id": attempt_id,
        "provider_rows": result.provider_rows,
        "valid_rows": result.valid_count,
        "rejected_rows": result.rejected_count,
        "valid_ratio": result.valid_ratio,
        "storage_quality": result.storage_quality,
        "first_date": result.first_date,
        "last_date": result.last_date,
        "versions_appended": stored["versions_appended"],
        "canonical_updates": stored["canonical_updates"],
        "protected_by_precedence": stored["protected_by_precedence"],
        "source": SOURCE,
    }
