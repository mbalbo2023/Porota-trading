"""A145: UTC persistence must not multiply after-hours PPI history/catalog jobs."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from be_paper_engine import PaperStore
import bf_production_paper_observer as observer
from bf_production_paper_observer import _source_sync_due, _support_schema


ART = ZoneInfo("America/Argentina/Buenos_Aires")
NIGHT_IN_ARGENTINA = datetime(2026, 10, 27, 22, 30, tzinfo=ART)


@pytest.mark.parametrize("recorded,expected_due", [
    ("2026-10-28T00:45:00+00:00", False),  # UTC tomorrow = today 21:45 ART
    ("2026-10-27T23:45:00Z", False),       # Same local date, 20:45 ART
    ("2026-10-27T02:45:00+00:00", True),   # UTC today = yesterday ART
    ("2026-10-27", False),                 # Legacy trading-day receipt
    ("2026-10-27T22:00:00", False),        # Legacy naive assumed ART
    ("2026-10-28T03:45:00Z", False),       # Future/skew must fail closed
    ("unparseable", False),                # Do not start a provider storm
])
def test_source_sync_due_uses_local_trading_date(tmp_path, recorded, expected_due):
    store = PaperStore(str(tmp_path / "paper.db"))
    _support_schema(store)
    with store.connect() as connection:
        connection.execute("""INSERT INTO source_sync
            (source,status,last_attempt_at,last_success_at,items,detail)
            VALUES('PPI_PRODUCTION_CATALOG','VERDE',?,?,1,'test')""",
            (recorded, recorded))
    assert _source_sync_due(store, "PPI_PRODUCTION_CATALOG", NIGHT_IN_ARGENTINA) is expected_due


def test_source_sync_due_missing_attempt_is_due(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    _support_schema(store)
    assert _source_sync_due(store, "PPI_PRODUCTION_HISTORY", NIGHT_IN_ARGENTINA) is True


def test_source_sync_advances_at_local_midnight_not_utc_midnight(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    _support_schema(store)
    with store.connect() as connection:
        connection.execute("""INSERT INTO source_sync
            (source,status,last_attempt_at,last_success_at,items,detail)
            VALUES('PPI_PRODUCTION_CATALOG','VERDE',?,?,1,'test')""",
            ("2026-10-28T02:55:00Z", "2026-10-28T02:55:00Z"))
    assert not _source_sync_due(store, "PPI_PRODUCTION_CATALOG",
                                datetime(2026, 10, 27, 23, 59, tzinfo=ART))
    assert _source_sync_due(store, "PPI_PRODUCTION_CATALOG",
                            datetime(2026, 10, 28, 0, 1, tzinfo=ART))


@pytest.mark.parametrize("local_day", [
    datetime(2026, 10, 31, 12, 0, tzinfo=ART),  # Saturday
    datetime(2026, 11, 1, 12, 0, tzinfo=ART),  # Sunday
    datetime(2026, 11, 6, 12, 0, tzinfo=ART),  # Audited BYMA holiday
    datetime(2027, 1, 4, 12, 0, tzinfo=ART),  # Unaudited year: fail closed
])
def test_scheduled_daily_sync_skips_nontrading_dates_without_provider_or_db(
        monkeypatch, local_day):
    def unexpected(*_args, **_kwargs):
        pytest.fail("Closed-day sync attempted a provider or source_sync read")

    monkeypatch.setattr(observer, "_source_sync_due", unexpected)
    monkeypatch.setattr(observer, "_download_catalog", unexpected)
    monkeypatch.setattr(observer, "_download_histories", unexpected)

    assert observer._daily_sync_needed(object(), now=local_day) is False
    assert observer._daily_sync(object(), object(), now=local_day) is None

