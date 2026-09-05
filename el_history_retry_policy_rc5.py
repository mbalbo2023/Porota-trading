"""RC5 Smart Ingest V4: política pura de reintentos históricos.

No hace red ni SQLite. Decide *cuándo* una identidad puede volver a ser elegida
para PPI_HISTORY a partir de evidencia persistida. Mantiene el scheduler global
de 2 h, pero evita usar ese cadence como reintento por identidad cuando el
payload histórico no puede cambiar durante la misma fecha operativa.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import ak_byma_calendar as byma_calendar

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
AUTO_STATES = {"EMPTY_OR_INVALID", "PARTIAL", "VALID_PAYLOAD", "UNKNOWN"}
SEVERE_FRESHNESS = {"SEVERELY_STALE", "STALE_UNVERIFIED_CALENDAR", "FUTURE_ANOMALY"}


@dataclass(frozen=True)
class RetryDecision:
    due: bool
    priority: int
    reason: str
    next_date: str | None
    quarantine: bool = False


def _as_ar_date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        dt=value
    else:
        dt=datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt=dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ).date()


def _next_audited_business_day(day: date) -> date | None:
    """No inventa calendarios fuera de años auditados."""
    current=day+timedelta(days=1)
    for _ in range(14):
        if current.year not in byma_calendar.ANIOS_AUDITADOS:
            return None
        if byma_calendar.es_dia_habil_operativo(current):
            return current
        current+=timedelta(days=1)
    raise RuntimeError("HISTORY_RETRY_CALENDAR_GUARD_EXCEEDED")


def retry_decision(*, last_attempt_at=None, state="UNKNOWN", freshness="UNKNOWN",
                   depth="UNKNOWN", secondary_state=None, now=None, force=False) -> RetryDecision:
    now=now or datetime.now(TZ)
    if now.tzinfo is None:
        now=now.replace(tzinfo=TZ)
    today=now.astimezone(TZ).date()
    state=str(state or "UNKNOWN").upper()
    freshness=str(freshness or "UNKNOWN").upper()
    depth=str(depth or "UNKNOWN").upper()
    secondary=str(secondary_state or "UNKNOWN").upper()
    last_day=_as_ar_date(last_attempt_at)

    if last_day is None:
        return RetryDecision(True,100,"NEVER_ATTEMPTED",None,False)

    if freshness in SEVERE_FRESHNESS and not force:
        return RetryDecision(False,0,"SEVERE_STALE_REQUIRES_REVIEW",None,True)

    if force:
        return RetryDecision(True,95,"EXPLICIT_FORCE",None,False)

    next_day=_next_audited_business_day(last_day)
    if next_day is None:
        return RetryDecision(False,0,"NEXT_BUSINESS_DAY_UNPROVEN",None,True)
    next_iso=next_day.isoformat()

    if today < next_day:
        return RetryDecision(False,0,"WAIT_NEW_BUSINESS_DATE",next_iso,False)

    if state not in AUTO_STATES:
        return RetryDecision(False,0,"UNRECOGNIZED_ATTEMPT_STATE",next_iso,True)

    if freshness == "STALE":
        return RetryDecision(True,80,"TARGETED_STALE_REFRESH",next_iso,False)

    if state == "EMPTY_OR_INVALID" and secondary == "EMPTY_OR_INVALID":
        return RetryDecision(True,20,"BOTH_SOURCES_EMPTY_BUSINESS_DAY_BACKOFF",next_iso,False)

    if state == "EMPTY_OR_INVALID":
        return RetryDecision(True,30,"PPI_EMPTY_BUSINESS_DAY_BACKOFF",next_iso,False)

    if state == "PARTIAL":
        priority=45 if depth in {"NONE","LT30","LT90"} else 35
        return RetryDecision(True,priority,"PARTIAL_AFTER_NEW_BUSINESS_DATE",next_iso,False)

    if state == "VALID_PAYLOAD":
        priority=25 if depth in {"NONE","LT30","LT90"} else 10
        return RetryDecision(True,priority,"VALID_AFTER_NEW_BUSINESS_DATE",next_iso,False)

    return RetryDecision(True,15,"UNKNOWN_STATE_AFTER_NEW_BUSINESS_DATE",next_iso,False)


def assert_retry_policy_contract() -> None:
    monday=datetime(2026,9,7,12,0,tzinfo=TZ)
    saturday=datetime(2026,9,5,12,0,tzinfo=TZ)
    assert retry_decision(last_attempt_at=None,now=saturday).reason == "NEVER_ATTEMPTED"
    same=retry_decision(last_attempt_at="2026-09-04T15:00:00-03:00",state="PARTIAL",freshness="FRESH",depth="LT90",now=saturday)
    assert not same.due and same.next_date == "2026-09-07"
    due=retry_decision(last_attempt_at="2026-09-04T15:00:00-03:00",state="PARTIAL",freshness="FRESH",depth="LT90",now=monday)
    assert due.due and due.reason == "PARTIAL_AFTER_NEW_BUSINESS_DATE"
    stale=retry_decision(last_attempt_at="2026-09-04T15:00:00-03:00",state="PARTIAL",freshness="STALE",depth="LT180",now=monday)
    assert stale.due and stale.priority == 80
    severe=retry_decision(last_attempt_at="2026-09-04T15:00:00-03:00",state="PARTIAL",freshness="SEVERELY_STALE",depth="LT90",now=monday)
    assert severe.quarantine and not severe.due
