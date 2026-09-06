"""Disponibilidad de ventas PAPER: fail-closed sin inventar un cutoff intradía.

CI puede acreditarse en el mismo instante modelado. Para T+1 se calcula sólo
la fecha hábil esperada como evidencia diagnóstica. Sin una acreditación
reconciliada/autoritativa no se inventa una hora ni una frontera automática:
el producido permanece bloqueado hasta confirmación explícita.
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
    """Frontera diagnóstica conservadora, no autorización para liberar caja.

    Puede expresar el comienzo del día posterior a la fecha hábil esperada,
    pero no constituye evidencia del broker y por sí sola jamás acredita un
    producido T+1. Si ni siquiera la fecha es demostrable, retorna ``None``.
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
    """Aceptar sólo disponibilidad defendible por la procedencia declarada.

    ``PENDING_CONFIRMATION`` nunca se auto-acredita por el mero paso del
    tiempo. Si ni siquiera la fecha hábil es demostrable, también permanece
    bloqueado hasta conciliación. Una marca ``PAPER_CONSERVATIVE_CALENDAR``
    sólo es válida para plazos cuyo timestamp modelado sí esté definido (CI).
    """
    traded=aware_datetime(traded_at)
    available=aware_datetime(available_at) if available_at is not None else None
    if basis=='PENDING_CONFIRMATION':
        if available is not None: raise ValueError('Recibo pendiente con acreditación no confirmada')
        # Sin evidencia del broker no existe una hora de disponibilidad defendible.
        # La fecha hábil esperada sirve para diagnóstico, jamás para liberar caja.
        return None
    if basis=='PAPER_CONSERVATIVE_CALENDAR':
        modeled=modeled_sale_settlement(settlement,traded)
        if modeled is None or available is None or available!=aware_datetime(modeled):
            raise ValueError('Liquidación incompatible con el modelo PAPER declarado')
        return available
    raise ValueError('Fuente de liquidación no soportada; requiere conciliación')
