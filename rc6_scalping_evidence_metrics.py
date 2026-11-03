"""Bounded, read-only Scalping RCA and synthetic replay evidence.

This module intentionally has no broker, provider, scheduler or order imports.
An event count in the PAPER database is not an authenticated PPI volume unit,
a historical RCA total, a complete OHLCV bar or out-of-sample strategy edge.
The operator may run synthetic regression tests 24x7; these summaries confer
no permission to enter the market outside the verified PAPER session.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, time, timezone
import hashlib
import json
from zoneinfo import ZoneInfo

from br_backtest_gate import purged_temporal_split
from bs_instrument_contracts import aware_datetime


ART = ZoneInfo("America/Argentina/Buenos_Aires")
PPI_INTRADAY_PUBLIC_DOCUMENTATION = (
    "https://itatppi.github.io/ppi-official-api-docs/api/documentacionRest/")
# The documented response names date/price/volume, but does NOT define whether
# the last field means quantity, nominal value, money, an interval, cumulative
# daily activity, or a vendor-revisable observation. The request also lacks a
# venue/currency selector. These facts alone cannot certify a volume contract.
PPI_INTRADAY_SCHEMA_FIELDS = ("date", "price", "volume")
MAX_CANDIDATES = 10000
MAX_IDENTITIES = 10000
MAX_SYNTHETIC_LABELS = 10000


def session_rca_snapshot(store, *, as_of):
    """Read native PAPER SQLite counters; never fabricate provider receipts.

    Candidate rows are evaluations, not HTTP requests. Source-state rows are
    only the *latest* value per full identity, not a reconstructed time series.
    All clocks are normalized to exact UTC instants before the bounded read.
    """
    at = aware_datetime(as_of)
    local_day = at.astimezone(ART).date()
    start = datetime.combine(local_day, time.min, ART).astimezone(timezone.utc).isoformat(timespec="microseconds")
    cutoff = at.astimezone(timezone.utc).isoformat(timespec="microseconds")
    with store.connect() as connection:
        # Never change SQLite state or weaken a read to claim a clean PAPER/0
        # source: an inconsistent database is a hard diagnostic failure.
        observer = connection.execute(
            "SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if observer is None or tuple(observer) != ("PRODUCTION_PAPER", 0):
            raise ValueError("PAPER_ZERO_REQUIRED_FOR_SCALPING_RCA")
        candidates = connection.execute("""SELECT action,reason FROM scalping_candidates
            WHERE evaluated_at>=? AND evaluated_at<=?
            ORDER BY evaluated_at DESC,id DESC LIMIT ?""",
            (start, cutoff, MAX_CANDIDATES + 1)).fetchall()
        states = connection.execute("""SELECT state FROM ppi_intraday_contract_state
            WHERE checked_at>=? AND checked_at<=?
            ORDER BY checked_at DESC LIMIT ?""",
            (start, cutoff, MAX_IDENTITIES + 1)).fetchall()
    if len(states) > MAX_IDENTITIES:
        raise ValueError("SCALPING_RCA_IDENTITY_CAPACITY_EXCEEDED")
    truncated = len(candidates) > MAX_CANDIDATES
    rows = candidates[:MAX_CANDIDATES]
    actions = Counter(row["action"] for row in rows)
    reasons = Counter(row["reason"] for row in rows if row["action"] == "HOLD")
    latest_state = Counter(row["state"] for row in states)
    return {
        "schema": "rc6.scalping-evidence-metrics.v1",
        "source": "LOCAL_PRODUCTION_PAPER_SQLITE; NO_PROVIDER_IO",
        "as_of_utc": cutoff,
        "market_day_art": local_day.isoformat(),
        "candidate_measure": "CANDIDATE_EVALUATIONS_NOT_PPI_CALLS",
        "candidate_evaluations_counted": len(rows),
        "candidate_evaluations_complete": not truncated,
        "candidate_actions": dict(sorted(actions.items())),
        "candidate_hold_reasons": dict(sorted(reasons.items())),
        "identity_state_measure": "LATEST_STATE_PER_EXACT_IDENTITY_NOT_HISTORICAL_EVENTS",
        "identity_states_counted": len(states),
        "latest_identity_states": dict(sorted(latest_state.items())),
        "ppi_volume_semantics": "NO_VERIFICADO_FROM_LOCAL_COUNTERS",
        "ppi_public_documentation": PPI_INTRADAY_PUBLIC_DOCUMENTATION,
        "ppi_documented_response_fields": list(PPI_INTRADAY_SCHEMA_FIELDS),
        "ppi_volume_unit": "NO_VERIFICADO",
        "ppi_volume_accumulation": "NO_VERIFICADO",
        "ppi_volume_reset_rule": "NO_VERIFICADO",
        "provider_payload_pairs": "NO_VERIFICADO",
        "historical_2582_hold_comparability": "NOT_COMPARABLE_WITH_A198_AGGREGATE",
        "oos_edge": "OOS_NO_VERIFICADO",
        "promotion_allowed": False,
        "real_orders_sent": 0,
    }


def synthetic_purged_replay(samples, *, training_end, test_start, test_end,
                            as_of, embargo_seconds):
    """Reproducible temporal partition, explicitly without OOS certification.

    Reuses the native purge/embargo contract and accepts only isolated
    synthetic labels. Never reads a production ledger, market feed or account.
    No statistical result is interpreted as a frozen, externally tested edge.
    """
    rows = list(samples)
    if len(rows) > MAX_SYNTHETIC_LABELS:
        raise ValueError("SCALPING_SYNTHETIC_REPLAY_CAPACITY_EXCEEDED")
    if any(not isinstance(row, dict) or row.get("label_source") != "SYNTHETIC_TEST_ONLY"
           for row in rows):
        raise ValueError("SCALPING_SYNTHETIC_LABEL_SOURCE_REQUIRED")
    # Stable identity order makes the digest and purged set reproducible even
    # when a replay input arrives in a different enumeration order.
    ordered = sorted(rows, key=lambda item: str(item.get("paper_id") or ""))
    partition = purged_temporal_split(ordered, training_end=training_end,
        test_start=test_start, test_end=test_end, as_of=as_of,
        embargo_seconds=embargo_seconds)
    encoded = json.dumps(ordered, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    return {
        "schema": "rc6.scalping-synthetic-purged-replay.v1",
        "input_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        "label_source": "SYNTHETIC_TEST_ONLY",
        "train_ids": [row["paper_id"] for row in partition["train"]],
        "test_ids": [row["paper_id"] for row in partition["test"]],
        "purged_ids": sorted(partition["purged"]),
        "train_count": len(partition["train"]),
        "test_count": len(partition["test"]),
        "purged_count": len(partition["purged"]),
        "requires_frozen_model": True,
        "frozen_model_attested": False,
        "strategy_validation": "SYNTHETIC_REPLAY_ONLY; OOS_NO_VERIFICADO",
        "promotion_allowed": False,
        "real_orders_sent": 0,
    }
