"""RC6 centralized Telegram delivery policy.

Safety property: priority <= 0 (CRITICAL/P0) is unconditionally deliverable and
can never be suppressed by calendar logic. Routine PAPER/market/history events
are suppressed on non-business days and are terminally marked so they do not
replay on Monday.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

TZ=ZoneInfo('America/Argentina/Buenos_Aires')

CRITICAL_PRIORITY_MAX=0
ROUTINE_KINDS={
    'CLOSING_SUMMARY','PAPER_FILLED_BUY','PAPER_FILLED_SELL',
    'PAPER_CAUCION_PLACED','PAPER_CAUCION_MATURED','PAPER_EXIT_STATE',
    'MARKET_CLOSED','NO_TRADE','HISTORY_INGEST','HISTORY_PROGRESS',
    'INGEST_PROGRESS','PAPER_PROGRESS','ROUTINE_HEALTH','SUCCESS','INFO',
}


@dataclass(frozen=True)
class DeliveryDecision:
    allow: bool
    terminal_state: str
    reason: str
    critical_unsuppressible: bool


def _business_day(moment: datetime) -> bool:
    try:
        import ak_byma_calendar as cal
        return bool(cal.es_dia_habil_operativo(moment.astimezone(TZ).date()))
    except Exception:
        # Calendar failure may suppress routine traffic but MUST NOT suppress P0.
        return False


def decide(*, kind: str, priority: int, at: datetime, business_day: bool | None=None) -> DeliveryDecision:
    kind=str(kind or '').upper().strip()
    priority=int(priority)
    if priority <= CRITICAL_PRIORITY_MAX:
        return DeliveryDecision(True,'DELIVER','CRITICAL_UNSUPPRESSIBLE',True)
    if business_day is None:
        business_day=_business_day(at)
    if not business_day:
        return DeliveryDecision(False,'SUPPRESSED_WEEKEND','NON_BUSINESS_DAY_ROUTINE',False)
    return DeliveryDecision(True,'DELIVER','BUSINESS_DAY',False)


def decide_row(row: dict, at: datetime) -> DeliveryDecision:
    return decide(kind=row.get('kind',''),priority=int(row.get('priority',30)),at=at)
