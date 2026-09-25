import datetime as dt

import bf_production_paper_observer as observer


def test_audited_byma_holiday_closes_business_day():
    assert observer._business_day(dt.date(2026, 8, 17)) is False


def test_audited_byma_weekday_is_preserved():
    assert observer._business_day(dt.date(2026, 9, 9)) is True


def test_unknown_calendar_year_fails_closed():
    assert observer._business_day(dt.date(2027, 9, 9)) is False
