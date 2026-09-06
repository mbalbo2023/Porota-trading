"""RC6 native Contract Evidence cadence/window policy.

No HTTP, credentials, browser or trading actions live here. The single cadence
source remains co_contract_ingestion_policy_hf6. Browser work is never allowed
on non-operational BYMA days and never enters the trading hot path.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import ak_byma_calendar as byma_calendar
from co_contract_ingestion_policy_hf6 import CADENCES, ttl_seconds
from co_market_sessions_hf6 import BYMA_PAPER_SPOT_CLOSE, BYMA_PAPER_SPOT_OPEN

JOB_TO_CADENCE = {
    "CONTRACT_EVIDENCE_DYNAMIC": "OPERABILITY",
    "CONTRACT_EVIDENCE_CAUCIONES": "CAUCION_LIVE_CONTRACT",
    "CONTRACT_EVIDENCE_AUCTIONS": "AUCTION_STATUS",
    "CONTRACT_EVIDENCE_DERIVATIVES": "DERIVATIVE_SERIES",
    "CONTRACT_EVIDENCE_STATIC": "STATIC_CONTRACT",
    "CONTRACT_EVIDENCE_FULL_BROWSER": "FULL_BROWSER_AUDIT",
}

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
DYNAMIC_START = BYMA_PAPER_SPOT_OPEN
DYNAMIC_END = BYMA_PAPER_SPOT_CLOSE


def cadence_seconds(job_key: str) -> int:
    return ttl_seconds(JOB_TO_CADENCE[str(job_key)])


def _local(ref: datetime) -> datetime:
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return ref.astimezone(AR_TZ)


def window_allows(job_key: str, now=None) -> bool:
    ref = now or datetime.now(timezone.utc)
    local = _local(ref)
    if not byma_calendar.es_dia_habil_operativo(local.date()):
        return False
    cadence = CADENCES[JOB_TO_CADENCE[str(job_key)]]
    clock = local.time().replace(tzinfo=None)
    if cadence.during_market:
        return DYNAMIC_START <= clock < DYNAMIC_END
    return clock < DYNAMIC_START or clock >= DYNAMIC_END


def due(last_run_at, job_key: str, now=None) -> bool:
    ref = now or datetime.now(timezone.utc)
    if not window_allows(job_key, ref):
        return False
    if not last_run_at:
        return True
    try:
        stamp = datetime.fromisoformat(str(last_run_at).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return (ref.astimezone(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds() >= cadence_seconds(job_key)
    except Exception:
        return True
