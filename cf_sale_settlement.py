"""Disponibilidad de ventas PAPER: fail-closed sin inventar un cutoff intradía.

CI puede acreditarse en el mismo instante modelado. Para T+1 se calcula la
fecha hábil esperada. Si no existe una hora contractual confirmada, la venta
permanece bloqueada durante toda esa fecha y sólo se libera a partir de las
00:00 del día calendario siguiente. Ese límite es una espera conservadora,
no una hora de liquidación atribuida al broker.
"""
from datetime import date, datetime, time, timedelta
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

def conservative_unconfirmed_availability(settlement, traded_at):
    """Límite seguro para un T+1 sin cutoff confirmado.

    Nunca pretende conocer la hora real de acreditación. Si la fecha hábil
    esperada es defendible, espera hasta que esa fecha haya transcurrido por
    completo y devuelve 00:00 del día calendario siguiente. Si la fecha no se
    puede demostrar, retorna None y la caja sigue bloqueada.
    """
    expected=modeled_sale_settlement_date(settlement,traded_at)
    if expected is None:
        return None
    boundary=date.fromisoformat(expected)+timedelta(days=1)
    return datetime.combine(boundary,time.min,tzinfo=TZ)

def modeled_sale_settlement(settlement, traded_at):
    """Timestamp de disponibilidad sólo cuando el modelo puede defenderlo.

    CI retorna el instante de venta. T+1 retorna ``None`` deliberadamente:
    la DB no persiste una hora de broker no observada.
    """
    at=aware_datetime(traded_at).astimezone(TZ)
    if not isinstance(settlement,str): raise ValueError('Plazo de liquidación no textual')
    key=settlement.upper().strip()
    if key in {'INMEDIATA','CI','T+0'}: return at.isoformat()
    if key in {'A-24HS','24HS','T+1'}:
        modeled_sale_settlement_date(settlement,traded_at)
        return None
    return None

def validated_sale_settlement(settlement, traded_at, available_at, basis):
    """Aceptar sólo disponibilidad reconciliada o una espera conservadora.

    ``PENDING_CONFIRMATION`` continúa bloqueando durante toda la fecha hábil
    esperada. Una vez terminada esa fecha, se puede liberar el PAPER sin
    inventar un cutoff intradía: la frontera derivada es el comienzo del día
    calendario siguiente. Si ni siquiera la fecha hábil es demostrable, queda
    bloqueado indefinidamente hasta conciliación.
    """
    traded=aware_datetime(traded_at)
    available=aware_datetime(available_at) if available_at is not None else None
    if basis=='PENDING_CONFIRMATION':
        if available is not None: raise ValueError('Recibo pendiente con acreditación no confirmada')
        return conservative_unconfirmed_availability(settlement,traded)
    if basis=='PAPER_CONSERVATIVE_CALENDAR':
        modeled=modeled_sale_settlement(settlement,traded)
        if modeled is None or available is None or available!=aware_datetime(modeled):
            raise ValueError('Liquidación incompatible con el modelo PAPER declarado')
        return available
    raise ValueError('Fuente de liquidación no soportada; requiere conciliación')
