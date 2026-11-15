"""Selected A702/A777/A809b HISTORY contracts; no collector or PAPER extension.

All dates and empty-history expectations are hand-derived. These tests use
real pure production functions, never provider requests or a live database.
"""

from datetime import date, datetime, timezone
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import ak_byma_calendar as calendar
import co_market_sessions_hf6 as sessions
from historical_candle_shadow_rc6 import history_session_quality


ART = ZoneInfo("America/Argentina/Buenos_Aires")


def quote(settlement="A-24HS", *, market="BYMA", family="ACCIONES"):
    return SimpleNamespace(market=market, asset_class=family, settlement=settlement)


def test_history_requires_nov6_t1_without_inventing_c1_session():
    at = datetime(2026, 11, 11, 18, 20, tzinfo=ART)
    required_t1 = history_session_quality([], quote(), at)["missing_sessions"]
    required_ci = history_session_quality([], quote("CI"), at)["missing_sessions"]
    assert "2026-11-06" in required_t1
    assert "2026-11-06" not in required_ci


@pytest.mark.parametrize("day,hour,minute,expected", [
    (6, 17, 30, "2026-11-05"),
    (6, 18, 10, "2026-11-05"),
    (6, 18, 15, "2026-11-06"),
    (9, 12, 0, "2026-11-06"),
    (10, 12, 0, "2026-11-06"),
    (11, 17, 0, "2026-11-06"),
    (11, 17, 30, "2026-11-06"),
    (11, 18, 10, "2026-11-06"),
    (11, 18, 15, "2026-11-11"),
])
def test_t1_historical_expected_session_respects_exact_close_and_eod_floor(
        day, hour, minute, expected):
    result = history_session_quality(
        [], quote(), datetime(2026, 11, day, hour, minute, tzinfo=ART))
    assert result["expected_last_session"] == expected
    assert result["state"] == "HISTORY_GAPS"  # floor is not a source receipt


@pytest.mark.parametrize("at,expected", [
    (datetime(2026, 10, 30, 17, 14, 59, 999999, tzinfo=ART), "2026-10-29"),
    (datetime(2026, 10, 30, 17, 15, tzinfo=ART), "2026-10-30"),
    (datetime(2026, 11, 2, 18, 14, 59, 999999, tzinfo=ART), "2026-10-30"),
    (datetime(2026, 11, 2, 18, 15, tzinfo=ART), "2026-11-02"),
    (datetime(2026, 11, 6, 18, 14, 59, 999999, tzinfo=ART), "2026-11-05"),
    (datetime(2026, 11, 11, 18, 14, 59, 999999, tzinfo=ART), "2026-11-06"),
    (datetime(2026, 11, 11, 21, 15, tzinfo=timezone.utc), "2026-11-11"),
])
def test_historical_floor_boundary_and_utc_equivalence(at, expected):
    assert history_session_quality([], quote(), at)["expected_last_session"] == expected


@pytest.mark.parametrize("at", [
    datetime(2026, 11, 11, 18, 20), date(2026, 11, 11), None, "2026-11-11",
])
@pytest.mark.parametrize("settlement", ["A-24HS", "CI"])
def test_historical_session_rejects_naive_clock_instead_of_host_tz_guess(at, settlement):
    assert history_session_quality([], quote(settlement), at) == {
        "state": "HISTORY_CALENDAR_UNVERIFIED"}


@pytest.mark.parametrize("at", [
    datetime(2027, 11, 11, 20, tzinfo=ART),
    datetime(2026, 1, 5, 20, tzinfo=ART),  # required window crosses unaudited year
])
def test_history_does_not_invent_unaudited_sessions(at):
    assert history_session_quality([], quote(), at) == {
        "state": "HISTORY_CALENDAR_UNVERIFIED"}


@pytest.mark.parametrize("settlement", ["CI", "INMEDIATA", "T0"])
def test_unverified_or_ci_settlement_cannot_acquire_nov6_history(settlement):
    result = history_session_quality(
        [], quote(settlement), datetime(2026, 11, 11, 18, 20, tzinfo=ART))
    assert "2026-11-06" not in result["missing_sessions"]


# Hand-counted twenty ordinary BYMA source sessions as of 11-Nov after EOD.
# 06-Nov is deliberately missing: this is complete for CI, incomplete for T+1.
TWENTY_SESSIONS_WITHOUT_NOV6 = [
    "2026-11-11", "2026-11-05", "2026-11-04", "2026-11-03", "2026-11-02",
    "2026-10-30", "2026-10-29", "2026-10-28", "2026-10-27", "2026-10-26",
    "2026-10-23", "2026-10-22", "2026-10-21", "2026-10-20", "2026-10-19",
    "2026-10-16", "2026-10-15", "2026-10-14", "2026-10-13", "2026-10-09",
]


@pytest.mark.parametrize("settlement", ["T+1", "A-24HS", "T1", "24HS"])
def test_full_twenty_session_history_cannot_hide_missing_t1_source_day(settlement):
    result = history_session_quality(
        TWENTY_SESSIONS_WITHOUT_NOV6, quote(settlement),
        datetime(2026, 11, 11, 18, 20, tzinfo=ART))
    assert result["state"] == "HISTORY_GAPS"
    assert result["missing_sessions"] == ["2026-11-06"]


@pytest.mark.parametrize("settlement", ["CI", "INMEDIATA", "T0"])
def test_complete_ci_history_does_not_require_no_settlement_day(settlement):
    result = history_session_quality(
        TWENTY_SESSIONS_WITHOUT_NOV6, quote(settlement),
        datetime(2026, 11, 11, 18, 20, tzinfo=ART))
    assert result["state"] == "READY"
    assert result["missing_sessions"] == []


@pytest.mark.parametrize("settlement", ["UNKNOWN", None, "", "BOGUS", 1])
def test_complete_history_cannot_certify_unverified_settlement(settlement):
    assert history_session_quality(
        TWENTY_SESSIONS_WITHOUT_NOV6, quote(settlement),
        datetime(2026, 11, 11, 18, 20, tzinfo=ART)) == {
            "state": "HISTORY_CALENDAR_UNVERIFIED"}


def test_complete_t_plus_one_history_can_be_ready_only_with_nov6():
    complete = TWENTY_SESSIONS_WITHOUT_NOV6[:-1] + ["2026-11-06"]
    result = history_session_quality(
        complete, quote("T+1"), datetime(2026, 11, 11, 18, 20, tzinfo=ART))
    assert result["state"] == "READY"
    assert result["missing_sessions"] == []


@pytest.mark.parametrize("family,settlement,expected", [
    ("ACCIONES", "A-24HS", "LIMITED_T1_SOURCE_ONLY"),
    ("ACCIONES", "T+1", "LIMITED_T1_SOURCE_ONLY"),
    ("CEDEARS", "A-24HS", "LIMITED_T1_SOURCE_ONLY"),
    ("BONOS", "A-24HS", "LIMITED_T1_SOURCE_ONLY"),
    ("LETRAS", "T1", "LIMITED_T1_SOURCE_ONLY"),
    ("OBLIGACIONES", "24HS", "LIMITED_T1_SOURCE_ONLY"),
    ("ETFS", "A-24HS", "CLOSED"), ("CAUCIONES", "A-24HS", "CLOSED"),
    ("OPCIONES", "A-24HS", "CLOSED"), ("FCI", "A-24HS", "CLOSED"),
    ("ACCIONES", "CI", "CLOSED"), ("ACCIONES", "UNKNOWN", "CLOSED"),
])
def test_exact_source_day_does_not_open_other_contracts(family, settlement, expected):
    spec = importlib.util.find_spec("ak_byma_t1_source_clock")
    assert spec is not None, "A777 source-only clock is not implemented"
    from ak_byma_t1_source_clock import estado_negociacion_fuente_byma
    assert estado_negociacion_fuente_byma(
        date(2026, 11, 6), market="BYMA", family=family,
        settlement=settlement) == expected


@pytest.mark.parametrize("day,expected", [
    (date(2026, 11, 6), "LIMITED_T1_SOURCE_ONLY"),
    (date(2026, 11, 9), "CLOSED"), (date(2026, 11, 10), "CLOSED"),
    (date(2026, 11, 11), "REGULAR"), (date(2027, 11, 11), "CLOSED"),
])
def test_governed_t1_source_day_without_calendar_repin(day, expected):
    assert importlib.util.find_spec("ak_byma_t1_source_clock") is not None
    from ak_byma_t1_source_clock import estado_negociacion_fuente_byma
    assert estado_negociacion_fuente_byma(
        day, market="BYMA", family="ACCIONES", settlement="A-24HS") == expected
    assert calendar.es_dia_habil_operativo(day) is (expected == "REGULAR")


def test_source_clock_rejects_wrong_market_and_non_date():
    assert importlib.util.find_spec("ak_byma_t1_source_clock") is not None
    from ak_byma_t1_source_clock import estado_negociacion_fuente_byma
    assert estado_negociacion_fuente_byma(
        date(2026, 11, 6), market="A3", family="ACCIONES", settlement="A-24HS") == "CLOSED"
    assert estado_negociacion_fuente_byma(
        datetime(2026, 11, 6, tzinfo=ART), market="BYMA", family="ACCIONES",
        settlement="A-24HS") == "CLOSED"


def test_publication_floor_is_pure_aware_time_not_a_receipt():
    floor = getattr(sessions, "byma_eod_publication_floor", None)
    assert callable(floor), "EOD source publication floor is not implemented"
    assert floor(date(2026, 10, 30)) == datetime(2026, 10, 30, 17, 15, tzinfo=ART)
    assert floor(date(2026, 11, 2)) == datetime(2026, 11, 2, 18, 15, tzinfo=ART)
    with pytest.raises(ValueError):
        floor(datetime(2026, 11, 11, 18, 20))
    with pytest.raises(ValueError):
        floor(None)


def test_paper_session_and_historical_rescue_calendar_remain_unchanged():
    # A777's historical rescue verifies these immutable bytes, independently
    # of the evolving source clock. Do not relax that gate to fix history.
    raw = (Path(__file__).resolve().parents[1] / "ak_byma_calendar.py").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "32c5b024e6166b91adaff473b4884ec838f2cb2c0f9197427ab9b6f276b15c7a")
    assert sessions.session_for("BYMA", "ACCIONES").regular_close.hour == 17
    assert sessions.byma_paper_spot_phase(datetime(2026, 11, 11, 17, 30, tzinfo=ART)) == "CLOSED"
    assert sessions.byma_paper_spot_phase(datetime(2026, 11, 6, 11, 0, tzinfo=ART)) == "CLOSED"
