"""Calendario operativo conservador de BYMA.

Fuente oficial:
https://www.byma.com.ar/mercado/calendario-bursatil

La fuente puede cambiar por disposiciones oficiales. Por seguridad, este módulo
no intenta deducir que un día desconocido está abierto: fuera de los años
auditados devuelve cerrado (fail closed). Las jornadas con negociación pero sin
liquidación también se bloquean para PAPER y settlement; una consulta separada
puede clasificar sólo la ventana de observación de contratos T+1 explícitos.
"""

from datetime import date
from typing import Optional

FUENTE_OFICIAL = "https://www.byma.com.ar/mercado/calendario-bursatil"
CALENDARIO_AUDITADO_EL = date(2026, 9, 30)
# Preserve the earliest audited cutoff used by historical SHADOW fixtures;
# this is a subsequent correction to the same year's future dates.
CALENDARIO_ACTUALIZADO_EL = date(2026, 10, 14)
ANIOS_AUDITADOS = frozenset({2026})

# Incluye feriados, días sin negociación y jornadas especiales/limitadas que
# Porota trata conservadoramente como no operativas.
DIAS_NO_OPERATIVOS_2026 = {
    date(2026, 1, 1): "Año Nuevo",
    date(2026, 2, 16): "Carnaval",
    date(2026, 2, 17): "Carnaval",
    date(2026, 3, 23): "Día no laborable con fines turísticos",
    date(2026, 3, 24): "Día Nacional de la Memoria por la Verdad y la Justicia",
    date(2026, 4, 2): "Día del Veterano y de los Caídos en la Guerra de Malvinas",
    date(2026, 4, 3): "Viernes Santo",
    date(2026, 5, 1): "Día del Trabajador",
    date(2026, 5, 25): "Día de la Revolución de Mayo",
    date(2026, 6, 15): "Paso a la Inmortalidad del General Martín Miguel de Güemes",
    date(2026, 7, 9): "Día de la Independencia",
    date(2026, 7, 10): "Día no laborable con fines turísticos",
    date(2026, 8, 17): "Paso a la Inmortalidad del General José de San Martín",
    date(2026, 10, 12): "Día del Respeto a la Diversidad Cultural",
    date(2026, 11, 6): "Día del Bancario; jornada especial sin liquidación",
    date(2026, 11, 9): "Visita de Su Santidad el Papa León XIV; fecha excepcional informada por BYMA",
    date(2026, 11, 10): "Visita de Su Santidad el Papa León XIV; fecha excepcional informada por BYMA",
    date(2026, 11, 23): "Día de la Soberanía Nacional",
    date(2026, 12, 7): "Día no laborable con fines turísticos",
    date(2026, 12, 8): "Inmaculada Concepción de María",
    date(2026, 12, 24): "Nochebuena; jornada especial sin liquidación",
    date(2026, 12, 25): "Navidad",
    date(2026, 12, 31): "Jornada sin negociación ni liquidación",
}

DIAS_NO_OPERATIVOS = dict(DIAS_NO_OPERATIVOS_2026)

# BYMA 06-Nov-2026: jornada con NEGOCIACION y SIN LIQUIDACION, según nota (3)
# del calendario oficial. No transforma esa jornada en una sesión PAPER ni en
# una fecha de liquidación. Sólo contratos T+1 BYMA conocidos pueden utilizar
# este diagnóstico; ETF/opciones/futuros/FCI/otros mercados quedan en HOLD.
BYMA_T1_SOURCE_ONLY_LIMITED_DAYS = frozenset({date(2026, 11, 6)})
BYMA_T1_SOURCE_ONLY_FAMILIES = frozenset({
    "ACCIONES", "CEDEARS", "BONOS", "LETRAS", "OBLIGACIONES",
})
BYMA_T1_EXACT_SETTLEMENTS = frozenset({"A-24HS", "24HS", "T1"})


def motivo_no_operativo(dia: date) -> Optional[str]:
    """Devuelve el motivo de bloqueo o None si el día es operativo."""

    if dia.weekday() >= 5:
        return "Fin de semana"
    if dia.year not in ANIOS_AUDITADOS:
        return f"Calendario BYMA del año {dia.year} no auditado"
    return DIAS_NO_OPERATIVOS.get(dia)


def es_dia_habil_operativo(dia: date) -> bool:
    """Verdadero únicamente para una rueda normal incluida en el calendario."""

    return motivo_no_operativo(dia) is None


def estado_negociacion_fuente_byma(dia: date, *, market: str,
                                   family: str, settlement: str) -> str:
    """Reloj de fuente; nunca certifica PAPER, SETTLEMENT, cuota ni PIT.

    ``REGULAR`` es el día ordinario; ``LIMITED_T1_SOURCE_ONLY`` permite sólo
    diagnóstico por contrato exacto durante la rueda limitada del 06/11.
    Fechas/mercados desconocidos permanecen ``CLOSED``. Ningún consumidor debe
    usar este estado como permiso de orden o de llamada al broker.
    """
    if type(dia) is not date or str(market or "").strip().upper() != "BYMA":
        return "CLOSED"
    if dia.year not in ANIOS_AUDITADOS:
        return "CLOSED"
    if es_dia_habil_operativo(dia):
        return "REGULAR"
    if (dia in BYMA_T1_SOURCE_ONLY_LIMITED_DAYS
            and str(family or "").strip().upper() in BYMA_T1_SOURCE_ONLY_FAMILIES
            and str(settlement or "").strip().upper() in BYMA_T1_EXACT_SETTLEMENTS):
        return "LIMITED_T1_SOURCE_ONLY"
    return "CLOSED"
