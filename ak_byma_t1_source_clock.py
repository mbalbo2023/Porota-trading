"""BYMA exact T+1 *source-observation* clock, separate from PAPER calendar.

The canonical BYMA non-operational calendar is deliberately byte-frozen by
historical A92/A151 rescue provenance checks. This module imports that calendar
instead of editing its original bytes or reproducing its holiday list.

The 06-Nov-2026 negotiated-but-no-settlement exception is SOURCE_ONLY for
audited exact T+1 contracts. This pure clock never authorizes orders, PAPER,
settlement, provider GET, quota, or a published EOD/source receipt.
"""

from datetime import date

import ak_byma_calendar as calendar


# BYMA calendar official note (3), 06-Nov-2026: negotiation without settlement.
BYMA_T1_SOURCE_ONLY_LIMITED_DAYS = frozenset({date(2026, 11, 6)})
BYMA_T1_SOURCE_ONLY_FAMILIES = frozenset({
    "ACCIONES", "CEDEARS", "BONOS", "LETRAS", "OBLIGACIONES",
})
BYMA_T1_EXACT_SETTLEMENTS = frozenset({"A-24HS", "24HS", "T1", "T+1"})


def estado_negociacion_fuente_byma(dia: date, *, market: str,
                                   family: str, settlement: str) -> str:
    """Return REGULAR, LIMITED_T1_SOURCE_ONLY or CLOSED for a source date.

    The exception is observational only: ``calendar.es_dia_habil_operativo``
    remains False on November 6 and cannot be bypassed for PAPER/settlement.
    Unverified days/markets/contracts stay CLOSED.
    """
    if type(dia) is not date or str(market or "").strip().upper() != "BYMA":
        return "CLOSED"
    if dia.year not in calendar.ANIOS_AUDITADOS:
        return "CLOSED"
    if calendar.es_dia_habil_operativo(dia):
        return "REGULAR"
    if (dia in BYMA_T1_SOURCE_ONLY_LIMITED_DAYS
            and str(family or "").strip().upper() in BYMA_T1_SOURCE_ONLY_FAMILIES
            and str(settlement or "").strip().upper() in BYMA_T1_EXACT_SETTLEMENTS):
        return "LIMITED_T1_SOURCE_ONLY"
    return "CLOSED"
