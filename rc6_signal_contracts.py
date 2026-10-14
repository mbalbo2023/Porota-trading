"""Declared event signal contracts; counts never imply bars or elapsed time.

These are PAPER hypotheses, not evidence of economic edge. A source's volume
unit and accumulation convention require an explicit, dated contract.
"""
from __future__ import annotations

from datetime import timezone
from decimal import Decimal

from bs_instrument_contracts import aware_datetime

SCALPING_TEMPORAL_CONTRACT = {
    "schema": "rc6.event-signal-contract.v1", "version": "scalping-events-v1",
    "sample_kind": "DISTINCT_SOURCE_EVENTS", "minimum_samples": 15,
    "minimum_observed_span_seconds": 14 * 60, "maximum_gap_seconds": 120,
    "lookback_limit_seconds": 45 * 60, "bar_duration_seconds": None,
    "strategy_validation": "PAPER_HYPOTHESIS; ECONOMIC_EDGE_NO_VERIFICADO",
}


def instant_us(value):
    """Exact UTC integer without SQLite floating-point dates."""
    from datetime import datetime
    delta = aware_datetime(value).astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return delta.days * 86400000000 + delta.seconds * 1000000 + delta.microseconds


def register_exact_time(connection):
    connection.create_function("rc6_instant_us", 1, lambda v: instant_us(v) if v else None, deterministic=True)


def event_window_contract(points, at):
    times = [aware_datetime(p["event_at"]) for p in points]
    tail = times[-15:]
    span = (tail[-1] - tail[0]).total_seconds() if len(tail) >= 2 else 0
    gaps = [(right - left).total_seconds() for left, right in zip(tail, tail[1:])]
    reason = ("INSUFFICIENT_INTRADAY_POINTS" if len(tail) < 15 else
              "INTRADAY_EVENT_SPAN_INSUFFICIENT" if span < 14 * 60 else
              "INTRADAY_EVENT_CONTINUITY_UNVERIFIED" if any(g <= 0 or g > 120 for g in gaps) else "")
    if any(aware_datetime(p["first_received_at"]) > aware_datetime(at) for p in points):
        reason = "INTRADAY_INPUT_NOT_KNOWN_AT_CUTOFF"
    return dict(SCALPING_TEMPORAL_CONTRACT, observed_span_seconds=span,
                samples=len(points), reason_code=reason, passed=not reason)


def volume_contract(record, at):
    raw = record.get("raw") or {}
    if not raw and record.get("metadata_json"):
        import json
        try:
            decoded = json.loads(record["metadata_json"])
            raw = decoded.get("raw") or decoded
        except (ValueError, TypeError, AttributeError):
            raise ValueError("PPI_VOLUME_CONTRACT_UNVERIFIED")
    if not isinstance(raw, dict):
        raise ValueError("PPI_VOLUME_CONTRACT_UNVERIFIED")
    contract = raw.get("intraday_volume_contract") or record.get("intraday_volume_contract")
    if not isinstance(contract, dict):
        raise ValueError("PPI_VOLUME_CONTRACT_UNVERIFIED")
    try:
        if (contract.get("schema") != "rc6.provider-volume-contract.v1"
                or contract.get("provider") != "PPI" or contract.get("endpoint") != "MarketData/Intraday"
                or contract.get("family") != record["instrument_type"]
                or contract.get("verification") != "VERIFIED" or not contract.get("evidence_ref")
                or contract.get("unit") not in {"QUANTITY", "NOMINAL", "TURNOVER_MONEY"}
                or contract.get("accumulation") not in {"INTERVAL", "CUMULATIVE"}
                or contract.get("reset_rule") != "SESSION_ONLY"
                or not aware_datetime(contract["effective_at"]) <= aware_datetime(at)
                or not aware_datetime(contract["known_at"]) <= aware_datetime(at)):
            raise ValueError("PPI_VOLUME_CONTRACT_UNVERIFIED")
        if contract["unit"] == "TURNOVER_MONEY" and contract.get("currency") != record["currency"]:
            raise ValueError("PPI_VOLUME_CURRENCY_MISMATCH")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("PPI_VOLUME_CONTRACT_UNVERIFIED") from exc
    return dict(contract)


def quantity_activity(values, contract):
    """No conversion of monetary turnover or nominal amount into quantity."""
    if contract.get("unit") != "QUANTITY":
        raise ValueError("PPI_QUANTITY_VOLUME_UNAVAILABLE")
    amounts = [Decimal(str(v)) for v in values]
    if contract["accumulation"] == "INTERVAL":
        return amounts
    deltas = [right - left for left, right in zip(amounts, amounts[1:])]
    if any(v < 0 for v in deltas):
        raise ValueError("PPI_CUMULATIVE_VOLUME_RESET_UNVERIFIED")
    # The first cumulative point is an unknown interval, never invented volume.
    return [None] + deltas
