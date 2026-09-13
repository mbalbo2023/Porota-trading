"""Evidence-driven CAUCIONES session/cutoff gate for PAPER.

No opening/closing time is invented here. Runtime must supply a versioned BYMA
market-window source and an explicit broker/PPI cutoff source for the exact
currency/operation. If broker cutoff evidence is missing, the gate is HOLD.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import ak_byma_calendar as byma_calendar
from bs_instrument_contracts import aware_datetime, cash_currency


class CaucionScheduleError(ValueError):
    pass


@dataclass(frozen=True)
class CaucionScheduleEvidence:
    business_date: str
    currency: str
    operation: str
    opens_at: str
    market_closes_at: str
    order_cutoff_at: str
    byma_source: str
    broker_source: str
    broker_cutoff_verified: bool
    evidence_version: str

    def __post_init__(self):
        try:
            day = date.fromisoformat(str(self.business_date))
        except ValueError as exc:
            raise CaucionScheduleError("invalid business_date") from exc
        object.__setattr__(self, "currency", cash_currency(self.currency))
        if str(self.operation or "").upper() != "COLOCAR-CAUCION":
            raise CaucionScheduleError("schedule evidence is not for COLOCAR-CAUCION")
        object.__setattr__(self, "operation", "COLOCAR-CAUCION")
        opened = aware_datetime(self.opens_at, "caucion opens_at")
        closed = aware_datetime(self.market_closes_at, "caucion market_closes_at")
        cutoff = aware_datetime(self.order_cutoff_at, "caucion order_cutoff_at")
        if not opened < cutoff <= closed:
            raise CaucionScheduleError("caucion schedule ordering invalid")
        local_dates = {opened.date(), closed.date(), cutoff.date()}
        if local_dates != {day}:
            raise CaucionScheduleError("caucion schedule timestamps must match business_date")
        for name in ("byma_source", "evidence_version"):
            if not str(getattr(self, name) or "").strip():
                raise CaucionScheduleError(f"{name} missing")
        if self.broker_cutoff_verified and not str(self.broker_source or "").strip():
            raise CaucionScheduleError("verified broker cutoff lacks source")


def evaluate_schedule(evidence: CaucionScheduleEvidence | None, *, now) -> dict:
    at = aware_datetime(now, "caucion schedule now")
    base = {
        "family":"CAUCIONES",
        "operation":"COLOCAR-CAUCION",
        "calendar_state":"CLOSED",
        "cutoff_state":"CLOSED",
        "state":"HOLD",
        "reason":"",
    }
    if not isinstance(evidence, CaucionScheduleEvidence):
        return base | {"reason":"CAUCION_SCHEDULE_EVIDENCE_MISSING"}
    if not evidence.broker_cutoff_verified:
        return base | {"currency":evidence.currency,
                       "reason":"PPI_BROKER_CUTOFF_UNVERIFIED",
                       "evidence_version":evidence.evidence_version}
    if at.date().isoformat() != evidence.business_date:
        return base | {"currency":evidence.currency,
                       "reason":"SCHEDULE_BUSINESS_DATE_MISMATCH",
                       "evidence_version":evidence.evidence_version}
    if not byma_calendar.es_dia_habil_operativo(at.date()):
        return base | {"currency":evidence.currency,
                       "reason":"BYMA_CALENDAR_CLOSED_OR_UNAVAILABLE",
                       "evidence_version":evidence.evidence_version}
    opened = aware_datetime(evidence.opens_at)
    closed = aware_datetime(evidence.market_closes_at)
    cutoff = aware_datetime(evidence.order_cutoff_at)
    if not opened <= at < closed:
        return base | {"currency":evidence.currency,
                       "reason":"OUTSIDE_CAUCION_MARKET_WINDOW",
                       "evidence_version":evidence.evidence_version}
    if not opened <= at < cutoff:
        return base | {"currency":evidence.currency,
                       "calendar_state":"OPEN",
                       "reason":"PPI_CAUCION_ORDER_CUTOFF_REACHED",
                       "evidence_version":evidence.evidence_version}
    return base | {
        "currency":evidence.currency,
        "calendar_state":"OPEN",
        "cutoff_state":"OPEN",
        "state":"OPEN",
        "reason":"VERIFIED_CAUCION_WINDOW_AND_PPI_CUTOFF_OPEN",
        "evidence_version":evidence.evidence_version,
        "byma_source":evidence.byma_source,
        "broker_source":evidence.broker_source,
        "opens_at":evidence.opens_at,
        "market_closes_at":evidence.market_closes_at,
        "order_cutoff_at":evidence.order_cutoff_at,
    }
