"""Ventana conservadora del simulador de contado, no calendario universal.

RC5 toma la ventana regular del PAPER spot desde ``co_market_sessions_hf6``.
No simula subastas, sesiones extendidas, after-market ni horarios no verificados.
La salida EOD conserva buffers propios de riesgo antes del cierre regular.
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import ak_byma_calendar as calendar
from bs_instrument_contracts import SPOT_FAMILIES, aware_datetime, family_name
from co_market_sessions_hf6 import (
    BYMA_HOURS_COMMUNICATION,
    BYMA_HOURS_SOURCE,
    BYMA_PAPER_SPOT_CLOSE,
    BYMA_PAPER_SPOT_OPEN,
)

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
SESSION_SOURCE = f"BYMA COM{BYMA_HOURS_COMMUNICATION}; {BYMA_HOURS_SOURCE}; RC5 PAPER spot regular"


@dataclass(frozen=True)
class PaperSessionPolicy:
    open_time: time = BYMA_PAPER_SPOT_OPEN
    close_time: time = BYMA_PAPER_SPOT_CLOSE
    no_entry_minutes: int = 30
    exit_minutes: int = 10
    close_at_eod: bool = True

    def __post_init__(self):
        if not time(10, 30) <= self.open_time < self.close_time <= time(17, 0):
            raise ValueError("Ventana paper fuera del modelo regular conservador")
        span = (datetime.combine(datetime.min.date(), self.close_time) -
                datetime.combine(datetime.min.date(), self.open_time)).total_seconds() / 60
        if not 0 <= self.exit_minutes <= self.no_entry_minutes < span:
            raise ValueError("Buffers EOD incompatibles con la sesión")

    def supports(self, instrument):
        get = instrument.get if isinstance(instrument, dict) else lambda k: getattr(instrument, k)
        try:
            return (get("market") == "BYMA" and family_name(get("asset_class")) in SPOT_FAMILIES
                    and get("settlement") in {"INMEDIATA", "A-24HS", "CI", "24HS", "T+0", "T+1"})
        except (ValueError, AttributeError):
            return False

    def bounds(self, at):
        at = aware_datetime(at).astimezone(TZ)
        return (datetime.combine(at.date(), self.open_time, TZ),
                datetime.combine(at.date(), self.close_time, TZ))

    def execution_error(self, instrument, at):
        if not self.supports(instrument):
            return "SESSION_NOT_SUPPORTED"
        local = aware_datetime(at).astimezone(TZ)
        if not calendar.es_dia_habil_operativo(local.date()):
            return "MARKET_CLOSED_OR_CALENDAR_UNKNOWN"
        start, end = self.bounds(at)
        return "" if start <= local < end else "OUTSIDE_PAPER_EXECUTION_WINDOW"

    def admission_error(self, instrument, at):
        error = self.execution_error(instrument, at)
        if error:
            return error
        _, end = self.bounds(at)
        if self.close_at_eod and aware_datetime(at) >= end - timedelta(minutes=self.no_entry_minutes):
            return "EOD_NO_NEW_ENTRIES"
        return ""

    def exit_due(self, position, at):
        if not self.close_at_eod or not self.supports(position):
            return False
        opened = aware_datetime(position["opened_at"]).astimezone(TZ)
        local = aware_datetime(at).astimezone(TZ)
        _, end = self.bounds(at)
        return opened.date() < local.date() or local >= end - timedelta(minutes=self.exit_minutes)
