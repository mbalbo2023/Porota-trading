"""A145: UTC persistence must not multiply after-hours PPI history/catalog jobs."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from be_paper_engine import PaperStore
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

