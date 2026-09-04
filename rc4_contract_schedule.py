"""RC4 due-policy for Contract Evidence jobs.

Single source of cadence truth is co_contract_ingestion_policy_hf6. This module
only decides due/not-due; it performs no HTTP, login or broker operation.
"""
from __future__ import annotations
from datetime import datetime, timezone
from co_contract_ingestion_policy_hf6 import ttl_seconds

JOB_TO_CADENCE = {
    "CONTRACT_EVIDENCE_DYNAMIC": "OPERABILITY",
    "CONTRACT_EVIDENCE_CAUCIONES": "CAUCION_LIVE_CONTRACT",
    "CONTRACT_EVIDENCE_AUCTIONS": "AUCTION_STATUS",
    "CONTRACT_EVIDENCE_DERIVATIVES": "DERIVATIVE_SERIES",
    "CONTRACT_EVIDENCE_STATIC": "STATIC_CONTRACT",
    "CONTRACT_EVIDENCE_FULL_BROWSER": "FULL_BROWSER_AUDIT",
}

def cadence_seconds(job_key: str) -> int:
    return ttl_seconds(JOB_TO_CADENCE[str(job_key)])

def due(last_run_at, job_key, now=None):
    seconds = cadence_seconds(job_key)
    ref = now or datetime.now(timezone.utc)
    if not last_run_at:
        return True
    try:
        dt = datetime.fromisoformat(str(last_run_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (ref - dt.astimezone(timezone.utc)).total_seconds() >= seconds
    except Exception:
        return True
