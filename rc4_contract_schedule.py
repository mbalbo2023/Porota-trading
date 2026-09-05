"""RC4/RC5 due-policy for Contract Evidence jobs.

Single source of cadence truth is co_contract_ingestion_policy_hf6.
This module adds the operational time-window policy only; it performs no HTTP,
login, broker operation or database mutation.

RC5 alinea el inicio dinámico con la ventana regular PAPER spot verificada de
BYMA (10:30). El navegador autenticado continúa fuera del hot path y nunca se
inicia durante fines de semana.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

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
    """Return whether this job may wake the authenticated browser now.

    Dynamic jobs: weekdays 10:30 <= local time < 17:00 Argentina.
    Static/full-browser jobs: outside that dynamic window on weekdays only.
    Their own 1-day / 7-day TTL still applies. Weekends never start the
    authenticated browser.
    """
    ref = now or datetime.now(timezone.utc)
    local = _local(ref)
    cadence_name = JOB_TO_CADENCE[str(job_key)]
    cadence = CADENCES[cadence_name]
    clock = local.time().replace(tzinfo=None)
    weekday = local.weekday() < 5

    if cadence.during_market:
        return weekday and DYNAMIC_START <= clock < DYNAMIC_END

    # Static evidence/audit is intentionally outside the hot market window,
    # but never on weekends. Friday post-close is the preferred weekly slot.
    # TTL remains the primary repetition guard.
    return weekday and (clock < DYNAMIC_START or clock >= DYNAMIC_END)


def due(last_run_at, job_key, now=None):
    ref = now or datetime.now(timezone.utc)
    if not window_allows(job_key, ref):
        return False

    seconds = cadence_seconds(job_key)
    if not last_run_at:
        return True
    try:
        dt = datetime.fromisoformat(str(last_run_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (ref - dt.astimezone(timezone.utc)).total_seconds() >= seconds
    except Exception:
        return True
