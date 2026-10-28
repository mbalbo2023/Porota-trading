"""Política de arranque e hibernación guiada por el calendario de BYMA."""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import ak_byma_calendar as calendario

AUTO_START_ENABLED = os.getenv(
    "STARTUP_AUTO_BYMA", "false").strip().lower() == "true"
AUTO_START_MINUTES_BEFORE = int(os.getenv(
    "STARTUP_AUTO_MINUTES_BEFORE", "15"))
AUTO_STOP_MINUTES_AFTER = int(os.getenv(
    "STARTUP_AUTO_STOP_MINUTES_AFTER", "10"))
AUTO_START_MODE = os.getenv(
    "STARTUP_AUTO_MODE", "SIMULACION").strip().upper()
SERVER_TIMEZONE = os.getenv(
    "SERVER_TIMEZONE", "America/Argentina/Buenos_Aires")
MARKET_OPEN_HOUR = int(os.getenv("MARKET_OPEN_HOUR", "11"))
MARKET_CLOSE_HOUR = int(os.getenv("MARKET_CLOSE_HOUR", "17"))
ENVIRONMENT = os.getenv("ENVIRONMENT", "SANDBOX").strip().upper()


def _hora_local(ahora=None):
    if ahora is None:
        return datetime.now(ZoneInfo(SERVER_TIMEZONE))
    if getattr(ahora, "tzinfo", None) is not None:
        return ahora.astimezone(ZoneInfo(SERVER_TIMEZONE))
    return ahora


def evaluar_ventana(ahora=None):
    """Devuelve (listo, motivo) para inicializar el motor de trading."""

    ahora = _hora_local(ahora)
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


def motor_debe_estar_activo(ahora=None):
    """Indica si el proceso pesado debe existir, incluyendo el cierre diario.

    La ventana se extiende unos minutos después de la rueda para que j_main
    cierre posiciones, escriba el resumen de fin de día y recién entonces se
    apague. El dashboard no depende de esta función y permanece disponible.
    """

    ahora = _hora_local(ahora)
    motivo_calendario = calendario.motivo_no_operativo(ahora.date())
    if motivo_calendario:
        return False, motivo_calendario

    minuto_actual = ahora.hour * 60 + ahora.minute
    minuto_inicio = MARKET_OPEN_HOUR * 60 - AUTO_START_MINUTES_BEFORE
    minuto_hibernacion = MARKET_CLOSE_HOUR * 60 + AUTO_STOP_MINUTES_AFTER

    if minuto_actual < minuto_inicio:
        hora = f"{minuto_inicio // 60:02d}:{minuto_inicio % 60:02d}"
        return False, f"Motor hibernado hasta las {hora}"
    if minuto_actual >= minuto_hibernacion:
        return False, "Rueda finalizada; motor hibernado"
    return True, "Motor habilitado por calendario BYMA"


def modo_automatico():
    """Selecciona el modo sin permitir REAL accidentalmente en Sandbox."""

    if AUTO_START_MODE == "REAL" and ENVIRONMENT == "PRODUCTION":
        return "REAL"
    return "SIMULACION"
