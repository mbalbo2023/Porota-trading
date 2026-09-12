"""Read-only metrics for RC6 scalping intraday revision telemetry.

The dashboard must not reinterpret the contract policy. This module only
summarizes telemetry already emitted by ``cf_intraday_scalping`` and performs
SELECT queries against ``paper_events``. Malformed evidence is reported as
invalid instead of being converted to zero-age observations.
"""
from __future__ import annotations

import json
from math import ceil


EVENT_TYPE = "SCALPING_INTRADAY_REVISION_TELEMETRY"


def _percentile_nearest_rank(values, percentile):
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, ceil((percentile / 100.0) * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def summarize_revision_rows(rows, *, threshold_seconds=120):
    """Summarize persisted telemetry without deriving a new trading verdict."""
    valid = []
    invalid = 0
    by_action = {}
    by_symbol = {}
    latest_received_at = None
    for row in rows or ():
        try:
            detail = row["detail"] if isinstance(row, dict) else row[0]
            payload = json.loads(detail)
            action = str(payload["action"])
            symbol = str(payload["symbol"])
            age = float(payload["age_seconds"])
            mutable = int(payload["mutable_seconds"])
            received = str(payload["received_at"])
            if age < 0 or mutable <= 0 or not symbol or not action or not received:
                raise ValueError("invalid telemetry")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OverflowError):
            invalid += 1
            continue
        valid.append(age)
        by_action[action] = by_action.get(action, 0) + 1
        by_symbol[symbol] = by_symbol.get(symbol, 0) + 1
        if latest_received_at is None or received > latest_received_at:
            latest_received_at = received

    observed_mutable = by_action.get("REFRESH_MUTABLE", 0)
    observed_closed = by_action.get("REJECT_CLOSED_REVISION", 0)
    state = "NO_EVIDENCE" if not valid else "EVIDENCE_AVAILABLE"
    return {
        "state": state,
        "total_valid": len(valid),
        "invalid_events": invalid,
        "refresh_mutable": observed_mutable,
        "reject_closed_revision": observed_closed,
        "other_actions": len(valid) - observed_mutable - observed_closed,
        "threshold_seconds": int(threshold_seconds),
        "age_p50_seconds": _percentile_nearest_rank(valid, 50),
        "age_p95_seconds": _percentile_nearest_rank(valid, 95),
        "age_max_seconds": max(valid) if valid else None,
        "latest_received_at": latest_received_at,
        "by_action": dict(sorted(by_action.items())),
        "by_symbol": dict(sorted(by_symbol.items(), key=lambda item: (-item[1], item[0]))),
        "interpretation": (
            "OBSERVABILITY_ONLY: no threshold or trading gate is changed by these metrics"
        ),
    }


def read_revision_summary(connection, *, since_event_at=None, threshold_seconds=120):
    """Read only the dedicated event type from an already-open connection."""
    params = [EVENT_TYPE]
    where = "event_type=?"
    if since_event_at is not None:
        where += " AND event_at>=?"
        params.append(str(since_event_at))
    rows = connection.execute(
        "SELECT detail FROM paper_events WHERE " + where + " ORDER BY event_at,id",
        tuple(params),
    ).fetchall()
    return summarize_revision_rows(rows, threshold_seconds=threshold_seconds)
