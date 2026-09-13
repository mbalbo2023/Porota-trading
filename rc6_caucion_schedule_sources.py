"""Versioned official schedule evidence for RC6 CAUCIONES PAPER.

This module contains no network access and no order capability. It freezes the
currently verified public operating window for CAUCIONES and converts it into
``CaucionScheduleEvidence`` for a BYMA business date.

Evidence frozen 2026-09-13:
- BYMA Comunicado 19016 (2026-09-01), current negotiation-hours publication.
- PPI Support "Horarios de Mercado" (updated 2025-07-28): Contado Inmediato,
  including Cauciones, Monday-Friday 10:30-17:00.

Both ARS and USD_MEP must receive their own evidence object. Any calendar day
that the local BYMA calendar cannot certify as open is rejected fail-closed.
"""
from __future__ import annotations

from datetime import date

import ak_byma_calendar as byma_calendar
from rc6_caucion_schedule_gate import CaucionScheduleEvidence, CaucionScheduleError

BYMA_SOURCE = "BYMA_COM19016_2026-09-01"
PPI_SOURCE = "PPI_SUPPORT_HORARIOS_MERCADO_UPDATED_2025-07-28"
EVIDENCE_VERSION = "RC6_CAUCION_SCHEDULE_2026-09-13_V1"
SUPPORTED_CURRENCIES = ("ARS", "USD_MEP")
OPEN_TIME = "10:30:00-03:00"
CLOSE_TIME = "17:00:00-03:00"


def build_schedule_evidence(business_date, currency: str) -> CaucionScheduleEvidence:
    try:
        day = business_date if isinstance(business_date, date) else date.fromisoformat(str(business_date))
    except ValueError as exc:
        raise CaucionScheduleError("invalid schedule business date") from exc
    currency = str(currency or "").strip().upper()
    if currency not in SUPPORTED_CURRENCIES:
        raise CaucionScheduleError("unsupported caucion schedule currency")
    if not byma_calendar.es_dia_habil_operativo(day):
        raise CaucionScheduleError("BYMA_CALENDAR_CLOSED_OR_UNAVAILABLE")
    stamp = day.isoformat()
    return CaucionScheduleEvidence(
        business_date=stamp,
        currency=currency,
        operation="COLOCAR-CAUCION",
        opens_at=f"{stamp}T{OPEN_TIME}",
        market_closes_at=f"{stamp}T{CLOSE_TIME}",
        order_cutoff_at=f"{stamp}T{CLOSE_TIME}",
        byma_source=BYMA_SOURCE,
        broker_source=PPI_SOURCE,
        broker_cutoff_verified=True,
        evidence_version=f"{EVIDENCE_VERSION}:{currency}:{stamp}",
    )


def build_all_schedule_evidence(business_date) -> dict[str, CaucionScheduleEvidence]:
    return {currency: build_schedule_evidence(business_date, currency)
            for currency in SUPPORTED_CURRENCIES}
