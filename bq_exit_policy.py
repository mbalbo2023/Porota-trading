"""Ventana conservadora del simulador de contado, no calendario universal.

RC5 toma la ventana regular del PAPER spot desde ``co_market_sessions_hf6``.
No simula subastas, sesiones extendidas, after-market ni horarios no verificados.
La salida EOD conserva buffers propios de riesgo antes del cierre regular.
"""
from dataclasses import dataclass
import json
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
SESSION_SOURCE = f"BYMA COM{BYMA_HOURS_COMMUNICATION}; {BYMA_HOURS_SOURCE}; RC6 PAPER regular"
OPTION_EXPIRY_TRADING_CUTOFF = time(15, 30)
PAPER_SESSION_FAMILIES = frozenset(set(SPOT_FAMILIES) | {"OPCIONES"})


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

    @staticmethod
    def _get(instrument, key, default=None):
        if isinstance(instrument, dict):
            return instrument.get(key, default)
        return getattr(instrument, key, default)

    def _family(self, instrument):
        try:
            return family_name(self._get(instrument, "asset_class"))
        except (ValueError, AttributeError):
            return None

    def _option_expiry(self, instrument):
        if self._family(instrument) != "OPCIONES":
            return None
        contract = self._get(instrument, "contract")
        value = getattr(contract, "expires_at", None) if contract is not None else None
        if value is None and isinstance(instrument, dict):
            try:
                features = json.loads(instrument.get("features_json") or "{}")
                value = (features.get("financial_contract") or {}).get("expires_at")
            except (TypeError, ValueError, json.JSONDecodeError):
                value = None
        try:
            return aware_datetime(value, "vencimiento opción").astimezone(TZ) if value else None
        except (ValueError, TypeError):
            return None

    def supports(self, instrument):
        family = self._family(instrument)
        market = str(self._get(instrument, "market") or "").upper()
        settlement = str(self._get(instrument, "settlement") or "").upper()
        if market != "BYMA" or family not in PAPER_SESSION_FAMILIES:
            return False
        if family == "OPCIONES":
            # BYMA Clearing: premium settlement is T+0 from 2026-04-24.
            return settlement in {"INMEDIATA", "CI", "T+0", "T0"}
        return settlement in {"INMEDIATA", "A-24HS", "CI", "24HS", "T+0", "T+1"}

    def bounds(self, at, instrument=None):
        local = aware_datetime(at).astimezone(TZ)
        end_time = self.close_time
        expiry = self._option_expiry(instrument) if instrument is not None else None
        if expiry is not None and expiry.date() == local.date():
            # BYMA currently permits options only until 15:30 on expiration
            # day. Keep the standard exit buffer before that hard cutoff.
            end_time = min(end_time, OPTION_EXPIRY_TRADING_CUTOFF)
        return (datetime.combine(local.date(), self.open_time, TZ),
                datetime.combine(local.date(), end_time, TZ))

    def execution_error(self, instrument, at):
        if not self.supports(instrument):
            return "SESSION_NOT_SUPPORTED"
        local = aware_datetime(at).astimezone(TZ)
        if not calendar.es_dia_habil_operativo(local.date()):
            return "MARKET_CLOSED_OR_CALENDAR_UNKNOWN"
        expiry = self._option_expiry(instrument)
        if self._family(instrument) == "OPCIONES":
            if expiry is None:
                return "OPTION_EXPIRY_UNAVAILABLE"
            if local >= expiry:
                return "OPTION_EXPIRED"
        start, end = self.bounds(at, instrument)
        return "" if start <= local < end else "OUTSIDE_PAPER_EXECUTION_WINDOW"

    def admission_error(self, instrument, at):
        error = self.execution_error(instrument, at)
        if error:
            return error
        _, end = self.bounds(at, instrument)
        if self.close_at_eod and aware_datetime(at).astimezone(TZ) >= end - timedelta(minutes=self.no_entry_minutes):
            return "EOD_NO_NEW_ENTRIES"
        return ""

    def exit_due(self, position, at):
        if not self.close_at_eod or not self.supports(position):
            return False
        opened = aware_datetime(position["opened_at"]).astimezone(TZ)
        local = aware_datetime(at).astimezone(TZ)
        _, end = self.bounds(at, position)
        return opened.date() < local.date() or local >= end - timedelta(minutes=self.exit_minutes)
