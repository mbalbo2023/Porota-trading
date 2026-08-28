"""Disponibilidad de ventas PAPER: mismo contrato para cierres y parciales.

No confirma una acreditación de PPI ni reescribe fechas históricas.
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from bs_instrument_contracts import aware_datetime

TZ = ZoneInfo('America/Argentina/Buenos_Aires')


def modeled_sale_settlement(settlement, traded_at):
    """CI inmediato; T+1 al fin del siguiente día auditado, sólo en PAPER."""
    at = aware_datetime(traded_at).astimezone(TZ)
    if not isinstance(settlement,str):
        raise ValueError('Plazo de liquidación no textual')
    key = settlement.upper().strip()
    if key in {'INMEDIATA','CI','T+0'}:
        return at.isoformat()
    if key not in {'A-24HS','24HS','T+1'}:
        return None
    import ak_byma_calendar as calendar
    candidate = at.date()
    for _ in range(370):
        candidate += timedelta(days=1)
        if candidate.year not in calendar.ANIOS_AUDITADOS:
            return None
        if calendar.es_dia_habil_operativo(candidate):
            return datetime.combine(candidate,time.max,TZ).isoformat()
    return None


def validated_sale_settlement(settlement, traded_at, available_at, basis):
    """Fuente/fecha conciliadas o error; pendiente sin acreditación = None.

    Una fuente distinta necesita un protocolo explícito de conciliación. Una
    fecha del modelo no verificable con el calendario disponible no se supone
    válida, ni se reemplaza por una fecha nueva.
    """
    traded = aware_datetime(traded_at)
    available = aware_datetime(available_at) if available_at is not None else None
    if basis=='PENDING_CONFIRMATION':
        if available is not None:
            raise ValueError('Recibo pendiente con acreditación no confirmada')
    elif basis=='PAPER_CONSERVATIVE_CALENDAR':
        modeled = modeled_sale_settlement(settlement,traded)
        if modeled is None or available is None or available!=aware_datetime(modeled):
            raise ValueError('Liquidación incompatible con el modelo PAPER declarado')
    else:
        raise ValueError('Fuente de liquidación no soportada; requiere conciliación')
    return available
