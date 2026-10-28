"""Disponibilidad de ventas PAPER: fail-closed sin inventar un cutoff intradía.

CI puede acreditarse en el mismo instante modelado. Para T+1 se calcula sólo
la fecha hábil esperada como evidencia diagnóstica.

RC6 hotfix 2026-09-07: T+1 puede liberarse únicamente DESPUÉS de haber
transcurrido por completo la fecha hábil esperada de liquidación. No se inventa
una hora de broker: se usa 00:00 del día calendario siguiente como frontera
conservadora. La política está apagada por defecto y PRODUCTION_PAPER debe
habilitarla explícitamente con PAPER_T1_FULL_DATE_RELEASE=true. Nunca autoriza
órdenes reales ni acredita saldos PPI reales.
"""
import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from bs_instrument_contracts import aware_datetime

TZ = ZoneInfo('America/Argentina/Buenos_Aires')


def t1_full_date_release_enabled():
    """Política RC6 PAPER: false por defecto; configuración ambigua falla cerrada."""
    value = str(os.getenv('PAPER_T1_FULL_DATE_RELEASE', 'false')).strip().lower()
    if value in {'0', 'false', 'no', 'off', ''}:
        return False
    if value in {'1', 'true', 'yes', 'si', 'sí', 'on'}:
        return True
    return False


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
    """Frontera PAPER conservadora, no una hora atribuida al broker.

    Expresa el comienzo del día posterior a la fecha hábil esperada. El caller
    todavía debe comparar esta frontera con su ``as_of`` antes de liberar caja.
    """
    expected=modeled_sale_settlement_date(settlement,traded_at)
    if expected is None:
        return None
    boundary=date.fromisoformat(expected)+timedelta(days=1)
    return datetime.combine(boundary,time.min,tzinfo=TZ)


def modeled_sale_settlement(settlement, traded_at):
    """Timestamp persistible cuando el modelo tiene una frontera contractual.

    CI retorna el instante de venta. T+1 retorna ``None`` deliberadamente:
    la DB no persiste una hora de broker no observada. El hotfix calcula la
    frontera PAPER efectiva al leer, sin reescribir la procedencia histórica.
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
    """Disponibilidad efectiva defendible por procedencia y política PAPER.

    ``PENDING_CONFIRMATION`` jamás acepta un ``available_at`` inventado. Con la
    política apagada conserva la semántica histórica y permanece bloqueado. Con
    la política RC6 explícitamente activa, sólo para T+1 reconocido y con fecha
    hábil demostrable devuelve la frontera conservadora posterior al día completo
    de settlement. El caller todavía debe compararla contra su ``as_of``.
    """
    traded=aware_datetime(traded_at)
    available=aware_datetime(available_at) if available_at is not None else None
    if basis=='PENDING_CONFIRMATION':
        if available is not None: raise ValueError('Recibo pendiente con acreditación no confirmada')
        if t1_full_date_release_enabled():
            key=str(settlement or '').upper().strip()
            if key in {'A-24HS','24HS','T+1'}:
                return conservative_unconfirmed_availability(settlement,traded)
        return None
    if basis=='PAPER_CONSERVATIVE_CALENDAR':
        modeled=modeled_sale_settlement(settlement,traded)
        if modeled is None or available is None or available!=aware_datetime(modeled):
            raise ValueError('Liquidación incompatible con el modelo PAPER declarado')
        return available
    raise ValueError('Fuente de liquidación no soportada; requiere conciliación')