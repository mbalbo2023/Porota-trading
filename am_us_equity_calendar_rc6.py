"""Calendario de admisión para subyacentes estadounidenses de CEDEARs.

El horario y los cierres se aplican solamente a aperturas PAPER de CEDEARs.
La observación de libro BYMA y la supervisión de salidas continúan aunque no
haya rueda del subyacente. Fuera del año auditado el resultado es fail-closed.
"""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")
AUDITED_YEAR = 2026

# Cierres completos regulares NYSE/Nasdaq 2026. Se versionan explícitamente
# para no depender de red ni de una inferencia de feriados durante la rueda.
US_EQUITY_FULL_CLOSURES_2026 = frozenset({
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
})

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)


def us_equity_phase(now: datetime | None = None) -> str:
    """Devuelve OPEN sólo dentro de sesión regular US del año auditado."""
    local = (now or datetime.now(NY_TZ)).astimezone(NY_TZ)
    if local.year != AUDITED_YEAR:
        return "UNAUDITED_YEAR"
    if local.weekday() >= 5:
        return "WEEKEND"
    if local.date().isoformat() in US_EQUITY_FULL_CLOSURES_2026:
        return "HOLIDAY"
    clock = local.time().replace(tzinfo=None)
    if clock < REGULAR_OPEN:
        return "PREOPEN"
    if clock >= REGULAR_CLOSE:
        return "CLOSED"
    return "OPEN"


def cedear_opening_gate(asset_class: str, now: datetime | None = None) -> tuple[bool, str]:
    """No bloquea acciones argentinas; CEDEARs requieren subyacente US abierto."""
    family = str(asset_class or "").upper()
    if family not in {"CEDEAR", "CEDEARS"}:
        return True, ""
    phase = us_equity_phase(now)
    if phase == "OPEN":
        return True, ""
    return False, f"CEDEAR_UNDERLYING_US_{phase}"


def calendar_status(now: datetime | None = None) -> dict:
    phase = us_equity_phase(now)
    return {
        "market": "US_EQUITY",
        "audited_year": AUDITED_YEAR,
        "phase": phase,
        "open_for_cedear_entries": phase == "OPEN",
        "policy": "FAIL_CLOSED_OUTSIDE_REGULAR_US_SESSION",
    }
