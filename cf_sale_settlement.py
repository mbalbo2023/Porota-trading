"""Disponibilidad de ventas PAPER: fail-closed sin inventar un cutoff intradía.

CI puede acreditarse en el mismo instante modelado. Para T+1 se calcula la
fecha hábil esperada sólo como información de UX; hasta verificar un cutoff
o una acreditación autoritativa, ``available_at`` permanece sin confirmar.
"""
from datetime import timedelta
from zoneinfo import ZoneInfo
from bs_instrument_contracts import aware_datetime

TZ = ZoneInfo('America/Argentina/Buenos_Aires')

def modeled_sale_settlement_date(settlement, traded_at):
    """Fecha hábil esperada, sin hora contractual inventada."""
    at=aware_datetime(traded_at).astimezone(TZ)
    if not isinstance(settlement,str): raise ValueError('Plazo de liquidación no textual')
    key=settlement.upper().strip()
    if key in {'INMEDIATA','CI','T+0'}: return at.date().isoformat()
    if key not in {'A-24HS','24HS','T+1'}: return None
    import ak_byma_calendar as calendar
    candidate=at.date()
    for _ in range(370):
        candidate += timedelta(days=1)
        if candidate.year not in calendar.ANIOS_AUDITADOS: return None
        if calendar.es_dia_habil_operativo(candidate): return candidate.isoformat()
    return None

def modeled_sale_settlement(settlement, traded_at):
    """Timestamp de disponibilidad sólo cuando el modelo puede defenderlo.

    CI retorna el instante de venta. T+1 retorna ``None`` deliberadamente:
    conocemos la fecha hábil esperada pero no un cutoff intradía oficial.
    """
    at=aware_datetime(traded_at).astimezone(TZ)
    if not isinstance(settlement,str): raise ValueError('Plazo de liquidación no textual')
    key=settlement.upper().strip()
    if key in {'INMEDIATA','CI','T+0'}: return at.isoformat()
    if key in {'A-24HS','24HS','T+1'}:
        # Validate that the next date is derivable, but do not turn a date into
        # a fabricated timestamp.
        modeled_sale_settlement_date(settlement,traded_at)
        return None
    return None

def validated_sale_settlement(settlement, traded_at, available_at, basis):
    """Accept only reconciled/defensible availability; pending stays blocked."""
    traded=aware_datetime(traded_at)
    available=aware_datetime(available_at) if available_at is not None else None
    if basis=='PENDING_CONFIRMATION':
        if available is not None: raise ValueError('Recibo pendiente con acreditación no confirmada')
        return None
    if basis=='PAPER_CONSERVATIVE_CALENDAR':
        modeled=modeled_sale_settlement(settlement,traded)
        if modeled is None or available is None or available!=aware_datetime(modeled):
            raise ValueError('Liquidación incompatible con el modelo PAPER declarado')
        return available
    raise ValueError('Fuente de liquidación no soportada; requiere conciliación')
