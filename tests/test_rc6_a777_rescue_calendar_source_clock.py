"""A777: A92/A151 byte-pinned rescue coexists with evolving BYMA source T+1.

OFFHOST synthetic checks; no external fetch, Docker, orders or live ledger.
"""

import hashlib
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import ak_byma_calendar as calendar
import ak_byma_t1_source_clock as clock
import co_market_sessions_hf6 as sessions
from scripts import rc6_deploy_quiescence as rescue


ART = ZoneInfo("America/Argentina/Buenos_Aires")
HISTORICAL_A92_CALENDAR_SHA256 = (
    "32c5b024e6166b91adaff473b4884ec838f2cb2c0f9197427ab9b6f276b15c7a")


def test_exact_historical_rescue_bytes_remain_immutable():
    raw = (Path(__file__).resolve().parents[1] / "ak_byma_calendar.py").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == HISTORICAL_A92_CALENDAR_SHA256
    assert rescue.RESCUE_CALENDAR_SHA256 == HISTORICAL_A92_CALENDAR_SHA256
    assert not hasattr(calendar, "estado_negociacion_fuente_byma")
    assert calendar.CALENDARIO_ACTUALIZADO_EL == date(2026, 10, 14)


@pytest.mark.parametrize("day,expected", [
    (date(2026, 11, 6), "LIMITED_T1_SOURCE_ONLY"),
    (date(2026, 11, 9), "CLOSED"),
    (date(2026, 11, 10), "CLOSED"),
    (date(2026, 11, 11), "REGULAR"),
    (date(2027, 11, 11), "CLOSED"),
])
def test_governed_t1_source_day_without_calendar_repin(day, expected):
    assert clock.estado_negociacion_fuente_byma(
        day, market="BYMA", family="ACCIONES", settlement="A-24HS") == expected
    assert calendar.es_dia_habil_operativo(day) is (expected == "REGULAR")


@pytest.mark.parametrize("day,hour,minute,safe", [
    (6, 10, 29, True), (6, 10, 30, False),
    (6, 18, 14, False), (6, 18, 15, True),
    (7, 12, 0, True), (8, 12, 0, True),
    (9, 12, 0, True), (10, 12, 0, True),
    (11, 10, 30, False), (11, 18, 14, False), (11, 18, 15, True),
])
def test_heavy_writes_clock_separates_session_from_postclose_floor(day, hour, minute, safe):
    assert sessions.byma_heavy_writes_safe_now(
        datetime(2026, 11, day, hour, minute, tzinfo=ART)) is safe


def test_heavy_writes_unknown_or_naive_clock_fails_closed(monkeypatch):
    assert sessions.byma_heavy_writes_safe_now(datetime(2026, 11, 11, 20)) is False
    assert sessions.byma_heavy_writes_safe_now(
        datetime(2027, 11, 11, 20, tzinfo=ART)) is False
    monkeypatch.setattr(calendar, "ANIOS_AUDITADOS", frozenset())
    assert sessions.byma_heavy_writes_safe_now(
        datetime(2026, 11, 11, 20, tzinfo=ART)) is False


def test_live_calendar_tamper_still_blocks_pinned_a151(monkeypatch, tmp_path):
    class HistoricalOpenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 28, 11, 20, tzinfo=tz)

    raw = (Path(__file__).resolve().parents[1] / "ak_byma_calendar.py").read_bytes()
    (tmp_path / "ak_byma_calendar.py").write_bytes(raw + b"\n# forged calendar\n")
    monkeypatch.setattr(rescue, "ROOT", tmp_path)
    monkeypatch.setattr(rescue, "datetime", HistoricalOpenDateTime)
    with pytest.raises(ValueError, match="QUIESCENCE_A151_CALENDAR_SOURCE_RED"):
        rescue.a151_held_open_proof(
            operator_a151_proof=lambda: ("a" * 64, "A151-1-1"),
            operator_timer_disabled=lambda: True)
