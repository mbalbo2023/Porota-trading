from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from rc6_data_retention import bounded_daily_start, daily_start


def test_daily_window_uses_argentina_date_and_has_exactly_365_dates():
    now=datetime(2026,10,27,1,30,tzinfo=timezone.utc)
    assert now.astimezone(ZoneInfo('America/Argentina/Buenos_Aires')).date()==date(2026,10,26)
    oldest=daily_start(now)
    assert oldest==date(2025,10,27)
    assert (date(2026,10,26)-oldest).days+1==365
    assert bounded_daily_start(date(2025,1,1),now)==oldest
    assert bounded_daily_start(date(2026,10,1),now)==date(2026,10,1)


def test_daily_window_rejects_naive_clock_and_non_date():
    with pytest.raises(ValueError,match='RETENTION_CLOCK_UNVERIFIED'):
        daily_start(datetime(2026,10,26))
    with pytest.raises(ValueError,match='RETENTION_REQUEST_DATE_INVALID'):
        bounded_daily_start(datetime(2026,10,26,tzinfo=timezone.utc),
                            datetime.now(timezone.utc))
