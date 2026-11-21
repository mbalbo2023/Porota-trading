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

# The audited #1203 range covers market negotiation dates for equities and
# CEDEARs. It does not extend the operational calendar to the rest of 2025.
BYMA_HISTORY_CUTOFF_FROM = date(2025, 9, 21)
BYMA_HISTORY_CUTOFF_TO = date(2026, 9, 21)
_HISTORY_2025_CLOSED = frozenset({
    date(2025, 10, 10), date(2025, 11, 24), date(2025, 12, 8),
    date(2025, 12, 25), date(2025, 12, 31),
})
_HISTORY_TRADING_WITHOUT_SETTLEMENT = frozenset({
    date(2025, 11, 6), date(2025, 11, 21), date(2025, 12, 24),
    date(2026, 3, 23), date(2026, 7, 10),
})
# BYMA notice 18859 and Nov 6 market reporting establish both November T+1
# sessions. Notice 18897 documents Contado 24 h on Dec 24. The 2026 BYMA
# calendar and dated 24 h trades corroborate March 23 and July 10.
_HISTORY_ATTESTED_T1_WITHOUT_SAME_DAY_SETTLEMENT = frozenset({
    date(2025, 11, 6), date(2025, 11, 21), date(2025, 12, 24),
    date(2026, 3, 23), date(2026, 7, 10),
})


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


def estado_negociacion_historica_byma(dia: date, *, market: str,
                                      family: str, settlement: str) -> str:
    """Use the bounded #1203 audit without modifying live admission rules."""
    if (type(dia) is not date or not BYMA_HISTORY_CUTOFF_FROM <= dia <= BYMA_HISTORY_CUTOFF_TO
            or market != "BYMA" or family not in {"ACCIONES", "CEDEARS"}):
        return "UNVERIFIED"
    if dia in _HISTORY_TRADING_WITHOUT_SETTLEMENT:
        if (dia in _HISTORY_ATTESTED_T1_WITHOUT_SAME_DAY_SETTLEMENT
                and settlement in BYMA_T1_EXACT_SETTLEMENTS):
            return "LIMITED_T1_SOURCE_ONLY"
        return "LIMITED_SETTLEMENT_UNVERIFIED"
    if dia.year == 2025:
        return "CLOSED" if dia.weekday() >= 5 or dia in _HISTORY_2025_CLOSED else "REGULAR"
    if dia.year not in calendar.ANIOS_AUDITADOS:
        return "UNVERIFIED"
    return estado_negociacion_fuente_byma(dia, market=market, family=family,
                                          settlement=settlement)
