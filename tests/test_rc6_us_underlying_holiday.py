from datetime import date, datetime
from zoneinfo import ZoneInfo

import bu_instrument_catalog as catalog
import rc6_preopen


TZ = ZoneInfo("America/Argentina/Buenos_Aires")
LABOR_DAY = datetime(2026, 9, 7, 10, 15, tzinfo=TZ)
NEXT_DAY = datetime(2026, 9, 8, 10, 15, tzinfo=TZ)
REASON = "UNDERLYING_MARKET_CLOSED: US_LABOR_DAY"


def test_labor_day_blocks_focus_and_nonfocus_cedears():
    for symbol in ("AAPL", "AAPLD", "AAPLC", "MSFT", "KO", "MELI"):
        assert catalog.rc6_underlying_opening_block(symbol, "CEDEARS", LABOR_DAY) == REASON


def test_labor_day_supports_singular_family_alias():
    assert catalog.rc6_underlying_opening_block("MSFT", "CEDEAR", LABOR_DAY) == REASON


def test_labor_day_does_not_block_argentine_equities():
    for symbol in ("GGAL", "YPFD", "PAMP"):
        assert catalog.rc6_underlying_opening_block(symbol, "ACCIONES", LABOR_DAY) == ""


def test_holiday_rule_expires_after_labor_day():
    assert catalog.rc6_underlying_opening_block("MSFT", "CEDEARS", NEXT_DAY) == ""


def test_preopen_policy_proves_nonfocus_cedear_hold():
    result = rc6_preopen.foreign_market_policy(date(2026, 9, 7))
    assert result["state"] == "GREEN"
    assert result["event"] == "US_LABOR_DAY"
    assert result["blocked_nonfocus_cedear_probes"] == {"MSFT": REASON, "KO": REASON}
    assert result["argentina_equity_block"] is None
    assert result["policy"] == "OBSERVE_AND_RECORD_QUOTES; HOLD_NEW_CEDEAR_OPENINGS_WHILE_US_MARKET_CLOSED"
