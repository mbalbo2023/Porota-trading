"""A702: 06-Nov BYMA negotiated T+1 without settlement, SOURCE_ONLY.

These are deterministic off-host contracts.  They never call a provider,
open a live database, authorize PAPER entry or schedule any collector.
"""

from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import ak_byma_calendar as calendar
import co_market_sessions_hf6 as sessions
from historical_candle_shadow_rc6 import history_session_quality
from scripts import rc6_source_cycle_dispatch as dispatch


ART = ZoneInfo("America/Argentina/Buenos_Aires")


@pytest.mark.parametrize("family,settlement", [
    ("ACCIONES", "A-24HS"),
    ("CEDEARS", "A-24HS"),
    ("BONOS", "A-24HS"),
    ("LETRAS", "T1"),
    ("OBLIGACIONES", "24HS"),
])
def test_official_nov6_negotiation_is_source_only(family, settlement):
    day = date(2026, 11, 6)
    instant = datetime(2026, 11, 6, 11, 20, tzinfo=ART)
    assert calendar.es_dia_habil_operativo(day) is False  # PAPER/settlement HOLD
    assert calendar.estado_negociacion_fuente_byma(
        day, market="BYMA", family=family, settlement=settlement
    ) == "LIMITED_T1_SOURCE_ONLY"
    assert sessions.byma_source_only_limited_phase(
        "BYMA", family, settlement, instant
    ) == "SOURCE_ONLY_OPEN"
    assert dispatch.source_t1_diagnostic_status(
        instant, market="BYMA", family=family, settlement=settlement
    ) == (True, "LIMITED_T1_SOURCE_ONLY")
    # The pre-existing generic source cycle contains mixed families and may
    # not begin any provider work without its own exact-identity/budget wiring.
    assert dispatch.phase_status(instant, "open") == (False, "NON_TRADING_DAY")
    assert sessions.byma_paper_instrument_phase(
        "BYMA", family, settlement, instant
    ) == "CLOSED"  # no PAPER opening and no settlement inference


@pytest.mark.parametrize("family,settlement", [
    ("CAUCIONES", "A-24HS"), ("FCI", "A-24HS"),
    ("OPCIONES", "CI"), ("ACCIONES", "CI"),
    ("ETFS", "A-24HS"), ("ACCIONES", "UNKNOWN"),
    ("", "A-24HS"),
])
def test_nov6_other_or_unverified_contracts_remain_closed(family, settlement):
    instant = datetime(2026, 11, 6, 11, 20, tzinfo=ART)
    assert sessions.byma_source_only_limited_phase(
        "BYMA", family, settlement, instant
    ) == "CLOSED"
    assert dispatch.source_t1_diagnostic_status(
        instant, market="BYMA", family=family, settlement=settlement
    ) == (False, "LIMITED_T1_NOT_ELIGIBLE")


@pytest.mark.parametrize("day", [
    date(2026, 11, 9), date(2026, 11, 10),
    date(2026, 11, 7), date(2026, 11, 8), date(2027, 11, 6),
])
def test_later_holidays_weekends_and_unaudited_year_fail_closed(day):
    instant = datetime(day.year, day.month, day.day, 11, 20, tzinfo=ART)
    assert calendar.estado_negociacion_fuente_byma(
        day, market="BYMA", family="ACCIONES", settlement="A-24HS"
    ) == "CLOSED"
    assert sessions.byma_source_only_limited_phase(
        "BYMA", "ACCIONES", "A-24HS", instant
    ) == "CLOSED"


def test_limited_nov6_requires_byma_and_intraday_window():
    for hour, minute in ((10, 29), (18, 0), (20, 0)):
        instant = datetime(2026, 11, 6, hour, minute, tzinfo=ART)
        assert sessions.byma_source_only_limited_phase(
            "BYMA", "ACCIONES", "A-24HS", instant
        ) == "CLOSED"
    assert calendar.estado_negociacion_fuente_byma(
        date(2026, 11, 6), market="A3", family="ACCIONES", settlement="A-24HS"
    ) == "CLOSED"
    assert sessions.byma_source_only_limited_phase(
        "BYMA", "ACCIONES", "A-24HS", datetime(2026, 11, 6, 11, 0)
    ) == "CLOSED"  # naive time cannot acquire observation authority


def test_history_requires_nov6_t1_without_inventing_c1_session():
    at = datetime(2026, 11, 11, 18, 20, tzinfo=ART)
    t1 = SimpleNamespace(market="BYMA", asset_class="ACCIONES", settlement="A-24HS")
    ci = SimpleNamespace(market="BYMA", asset_class="ACCIONES", settlement="CI")
    required_t1 = history_session_quality([], t1, at)["missing_sessions"]
    required_ci = history_session_quality([], ci, at)["missing_sessions"]
    assert "2026-11-06" in required_t1
    assert "2026-11-06" not in required_ci


def test_ordinary_regular_paper_calendar_is_unchanged():
    day = date(2026, 11, 5)
    assert calendar.es_dia_habil_operativo(day) is True
    assert calendar.estado_negociacion_fuente_byma(
        day, market="BYMA", family="ACCIONES", settlement="A-24HS"
    ) == "REGULAR"
    assert sessions.byma_paper_instrument_phase(
        "BYMA", "ACCIONES", "A-24HS",
        datetime(2026, 11, 5, 17, 30, tzinfo=ART)
    ) == "OPEN"
