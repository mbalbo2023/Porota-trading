"""Aggregate fail-closed freshness gate for RC6 CAUCIONES PAPER.

The agent consumes already canonical/validated snapshots. It never fetches PPI,
never scrapes and never routes orders. GREEN means every expected caucion
identity is contract-ready and carries fresh validated dynamic evidence, with a
fresh worker heartbeat and open calendar/cutoff state.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from bs_instrument_contracts import aware_datetime
from rc6_caucion_offer_adapter import CaucionOfferAdapterError, offer_from_canonical_snapshot

GATE_NAME = "CAUCION_FRESH_DATA_AGENT_GREEN"
DEFAULT_EXPECTED_TICKERS = (
    "DOLAR1", "PESOS1", "DOLAR2", "PESOS2", "DOLAR7",
    "PESOS7", "DOLAR30", "PESOS30", "DOLAR120", "PESOS120",
)
MAX_HEARTBEAT_AGE_SECONDS = 300


def _result(green: bool, reasons, tickers, *, evidence_payload=None) -> dict:
    payload = evidence_payload or {}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode()
    ).hexdigest()[:24]
    return {
        "name": GATE_NAME,
        "family": "CAUCIONES",
        "green": bool(green),
        "contract_status": "READY_PAPER_CANDIDATE" if green else "BLOCKED",
        "real_order_capability": False,
        "evidence_id": f"caucion-fresh-{digest}",
        "tickers": sorted(tickers),
        "reasons": sorted(set(reasons)),
    }


def evaluate_caucion_fresh_data_agent(
    canonical_snapshots: Sequence[Mapping[str, Any]],
    *,
    now,
    heartbeat_at,
    calendar_state: str,
    cutoff_state: str,
    contract_status_by_ticker: Mapping[str, str],
    expected_tickers=DEFAULT_EXPECTED_TICKERS,
) -> dict:
    current = aware_datetime(now, "freshness evaluation time")
    expected = tuple(str(t).strip().upper() for t in expected_tickers)
    expected_set = set(expected)
    reasons = []
    seen = {}

    try:
        heartbeat = aware_datetime(heartbeat_at, "freshness heartbeat")
        heartbeat_age = (current.astimezone(timezone.utc) - heartbeat.astimezone(timezone.utc)).total_seconds()
        if heartbeat_age < -5 or heartbeat_age > MAX_HEARTBEAT_AGE_SECONDS:
            reasons.append("HEARTBEAT_STALE_OR_FUTURE")
    except (ValueError, TypeError):
        reasons.append("HEARTBEAT_INVALID")

    if str(calendar_state or "").upper() != "OPEN":
        reasons.append("CALENDAR_NOT_OPEN")
    if str(cutoff_state or "").upper() != "OPEN":
        reasons.append("CAUCION_CUTOFF_NOT_OPEN")

    for raw in canonical_snapshots or ():
        try:
            offer = offer_from_canonical_snapshot(raw, now=current)
            ticker = offer.instrument_id.upper()
            if ticker in seen:
                reasons.append(f"DUPLICATE:{ticker}")
            seen[ticker] = raw
            if ticker not in expected_set:
                reasons.append(f"UNEXPECTED_TICKER:{ticker}")
            if str(contract_status_by_ticker.get(ticker) or "") != "READY_PAPER_CANDIDATE":
                reasons.append(f"CONTRACT_NOT_READY:{ticker}")
        except (CaucionOfferAdapterError, ValueError, TypeError) as exc:
            ticker = str(raw.get("ticker") if isinstance(raw, Mapping) else "UNKNOWN").upper()
            reasons.append(f"INVALID_DYNAMIC:{ticker}:{type(exc).__name__}")

    missing = sorted(expected_set - set(seen))
    extra = sorted(set(seen) - expected_set)
    reasons.extend(f"MISSING_TICKER:{ticker}" for ticker in missing)
    reasons.extend(f"UNEXPECTED_TICKER:{ticker}" for ticker in extra)

    for ticker in expected:
        if str(contract_status_by_ticker.get(ticker) or "") != "READY_PAPER_CANDIDATE":
            reasons.append(f"CONTRACT_NOT_READY:{ticker}")

    payload = {
        "evaluated_at": current.isoformat(),
        "heartbeat_at": str(heartbeat_at),
        "calendar_state": str(calendar_state),
        "cutoff_state": str(cutoff_state),
        "expected": sorted(expected_set),
        "seen": sorted(seen),
        "contract_status": {t: contract_status_by_ticker.get(t) for t in sorted(expected_set)},
        "snapshot_evidence_ids": sorted(
            str(v.get("evidence_id")) for v in seen.values() if isinstance(v, Mapping)
        ),
    }
    return _result(not reasons and set(seen) == expected_set, reasons, seen, evidence_payload=payload)
