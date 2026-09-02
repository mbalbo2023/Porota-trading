"""Sesiones de mercado versionadas por familia/mercado para Contract Evidence v2.

WIP: no está conectado todavía al runtime HF6 aceptado.

Objetivo: retirar la suposición de una única apertura/cierre global. Cada
familia sólo puede evaluarse cuando su sesión fue verificada contra fuente
oficial vigente. Un horario no confirmado devuelve UNKNOWN y el consumidor
debe actuar fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


@dataclass(frozen=True)
class MarketSession:
    market: str
    family: str
    regular_open: time
    regular_close: time
    preopen_start: time | None
    source: str
    source_date: date
    notes: str = ""


# A3 Mercados / Matba-Rofex: página oficial de Horarios de Negociación,
# verificada 2026-09-02. No se extrapolan estos horarios a BYMA.
A3_SOURCE = "https://matbarofex.com.ar/horario_de_negociacion"
A3_SOURCE_DATE = date(2026, 9, 2)

A3_SESSIONS = {
    "FUTUROS_OPCIONES_MONEDAS": MarketSession(
        "A3", "FUTUROS_OPCIONES_MONEDAS", time(10, 0), time(15, 0), time(9, 30),
        A3_SOURCE, A3_SOURCE_DATE, "Dólar/Yuan"),
    "FUTUROS_OPCIONES_AGRO": MarketSession(
        "A3", "FUTUROS_OPCIONES_AGRO", time(10, 30), time(17, 0), time(10, 0),
        A3_SOURCE, A3_SOURCE_DATE, "Agro general; mini/Chicago tienen excepciones"),
    "FUTUROS_OPCIONES_OTROS": MarketSession(
        "A3", "FUTUROS_OPCIONES_OTROS", time(10, 30), time(17, 0), time(10, 0),
        A3_SOURCE, A3_SOURCE_DATE,
        "RFX20, acciones individuales, BTC, Oro, WTI, Títulos Públicos, CER, CAUC"),
}

# BYMA acaba de publicar Comunicado 19016 el 2026-09-01. Hasta normalizar su
# tabla completa no se inventan horarios por familia. La página de Opciones sí
# confirma reglas especiales del vencimiento: negociación hasta 15:30 y
# ejercicio/no ejercicio hasta 15:59.
BYMA_HOURS_SOURCE = "https://www.byma.com.ar/comunicados/horarios-de-negociacion-liquidacion-y-recepcion-de-informacion"
BYMA_HOURS_SOURCE_DATE = date(2026, 9, 1)
BYMA_OPTIONS_SOURCE = "https://www.byma.com.ar/productos/productos-financieros/opciones"


def session_for(market: str, family: str) -> MarketSession | None:
    market = str(market or "").upper()
    family = str(family or "").upper()
    if market in {"A3", "MATBA_ROFEX", "ROFEX"}:
        aliases = {
            "DLR": "FUTUROS_OPCIONES_MONEDAS",
            "MONEDAS": "FUTUROS_OPCIONES_MONEDAS",
            "AGRO": "FUTUROS_OPCIONES_AGRO",
            "RFX20": "FUTUROS_OPCIONES_OTROS",
            "ACCIONES": "FUTUROS_OPCIONES_OTROS",
            "BTC": "FUTUROS_OPCIONES_OTROS",
            "ORO": "FUTUROS_OPCIONES_OTROS",
            "WTI": "FUTUROS_OPCIONES_OTROS",
            "TITULOS_PUBLICOS": "FUTUROS_OPCIONES_OTROS",
            "CER": "FUTUROS_OPCIONES_OTROS",
            "CAUC": "FUTUROS_OPCIONES_OTROS",
        }
        key = aliases.get(family, family)
        return A3_SESSIONS.get(key)
    return None


def phase_for(session: MarketSession | None, now: datetime | None = None) -> str:
    if session is None:
        return "UNKNOWN"
    now = (now or datetime.now(TZ)).astimezone(TZ)
    clock = now.time().replace(tzinfo=None)
    if session.preopen_start and session.preopen_start <= clock < session.regular_open:
        return "PREOPEN"
    if session.regular_open <= clock < session.regular_close:
        return "OPEN"
    return "CLOSED"


def byma_schedule_status() -> dict:
    """Expone explícitamente el pendiente de normalización, no inventa datos."""
    return {
        "state": "SOURCE_FOUND_NORMALIZATION_PENDING",
        "source": BYMA_HOURS_SOURCE,
        "source_date": BYMA_HOURS_SOURCE_DATE.isoformat(),
        "options_expiry_trading_until": "15:30",
        "options_expiry_instruction_until": "15:59",
        "options_source": BYMA_OPTIONS_SOURCE,
    }
