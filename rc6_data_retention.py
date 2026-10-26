"""Shared hard bound for daily market history, including the current ART date.

This pure module does not delete data. Physical deletion requires a separately
verified pin inventory and a transaction plan for each store.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ART = ZoneInfo('America/Argentina/Buenos_Aires')
MAX_DAILY_DATES = 365


def daily_start(now: datetime) -> date:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('RETENTION_CLOCK_UNVERIFIED')
    return now.astimezone(ART).date() - timedelta(days=MAX_DAILY_DATES - 1)


def bounded_daily_start(requested: date, now: datetime) -> date:
    if type(requested) is not date:
        raise ValueError('RETENTION_REQUEST_DATE_INVALID')
    return max(requested, daily_start(now))
