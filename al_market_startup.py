"""Política de arranque automático guiada por el calendario de BYMA."""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import ak_byma_calendar as calendario

AUTO_START_ENABLED = os.getenv(
    "STARTUP_AUTO_BYMA", "false").strip().lower() == "true"
AUTO_START_MINUTES_BEFORE = int(os.getenv(
    "STARTUP_AUTO_MINUTES_BEFORE", "15"))
AUTO_START_MODE = os.getenv(
    "STARTUP_AUTO_MODE", "SIMULACION").strip().upper()
SERVER_TIMEZONE = os.getenv(
    "SERVER_TIMEZONE", "America/Argentina/Buenos_Aires")
MARKET_OPEN_HOUR = int(os.getenv("MARKET_OPEN_HOUR", "11"))
MARKET_CLOSE_HOUR = int(os.getenv("MARKET_CLOSE_HOUR", "17"))
ENVIRONMENT = os.getenv("ENVIRONMENT", "SANDBOX").strip().upper()


def evaluar_ventana(ahora=None):
    """Devuelve (listo, motivo) para inicializar el motor de trading."""

    if ahora is None:
        ahora = datetime.now(ZoneInfo(SERVER_TIMEZONE))
    elif getattr(ahora, "tzinfo", None) is not None:
        ahora = ahora.astimezone(ZoneInfo(SERVER_TIMEZONE))

    motivo_calendario = calendario.motivo_no_operativo(ahora.date())
    if motivo_calendario:
        return False, motivo_calendario

    minuto_actual = ahora.hour * 60 + ahora.minute
    minuto_inicio = MARKET_OPEN_HOUR * 60 - AUTO_START_MINUTES_BEFORE
    minuto_cierre = MARKET_CLOSE_HOUR * 60

    if minuto_actual < minuto_inicio:
        hora = f"{minuto_inicio // 60:02d}:{minuto_inicio % 60:02d}"
        return False, f"Esperando inicio automático a las {hora}"
    if minuto_actual >= minuto_cierre:
        return False, "Rueda finalizada"
    return True, "Ventana automática habilitada por calendario BYMA"


def modo_automatico():
    """Selecciona el modo sin permitir REAL accidentalmente en Sandbox."""

    if AUTO_START_MODE == "REAL" and ENVIRONMENT == "PRODUCTION":
        return "REAL"
    return "SIMULACION"
