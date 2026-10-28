from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from am_us_equity_calendar_rc6 import cedear_opening_gate

AR = ZoneInfo("America/Argentina/Buenos_Aires")


def at(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=AR)


def test_cedear_entries_require_regular_us_session_but_actions_do_not():
    assert cedear_opening_gate("CEDEARS", at(2026, 1, 5, 13)) == (True, "")
    allowed, reason = cedear_opening_gate("CEDEARS", at(2026, 1, 19, 13))
    assert allowed is False and reason == "CEDEAR_UNDERLYING_US_HOLIDAY"
    allowed, reason = cedear_opening_gate("CEDEARS", at(2026, 1, 5, 11))
    assert allowed is False and reason == "CEDEAR_UNDERLYING_US_PREOPEN"
    allowed, reason = cedear_opening_gate("CEDEARS", at(2026, 1, 5, 18))
    assert allowed is False and reason == "CEDEAR_UNDERLYING_US_CLOSED"
    # 15:30 ART del 27/11 equivale a 13:30 ET: rueda US acortada ya cerrada.
    allowed, reason = cedear_opening_gate("CEDEARS", at(2026, 11, 27, 15, 30))
    assert allowed is False and reason == "CEDEAR_UNDERLYING_US_CLOSED"
    assert cedear_opening_gate("ACCIONES", at(2026, 1, 19, 13)) == (True, "")


def test_observer_rejects_stale_data_before_calling_the_engine():
    source = Path("bf_production_paper_observer.py").read_text(encoding="utf-8")
    assert source.index("data_error = q.time_error(") < source.index("broker.on_quote(")
    assert "cedear_opening_gate(asset_class, datetime.now(TZ))" in source
