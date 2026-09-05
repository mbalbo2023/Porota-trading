"""RC4-HF2 candidate tests for explicit close-only historical evidence."""
import sqlite3

import ct_ppi_history_salvage_hf6 as salvage
import ea_history_close_series_hf2 as close_series


class MemoryStore:
    def __init__(self):
        self.connection=sqlite3.connect(":memory:")
        self.connection.row_factory=sqlite3.Row
    def connect(self):
        return _NonClosing(self.connection)


class _NonClosing:
    def __init__(self,connection): self.connection=connection
    def __enter__(self): return self.connection
    def __exit__(self,*_): self.connection.commit(); return False


def _ingest(payload, *, attempted_at="2026-09-05T12:00:00+00:00", observer=None, history=None):
    observer=observer or MemoryStore()
    history=history or MemoryStore()
    result=salvage.ingest_ppi_payload(
        observer,
        symbol="ABC",
        instrument_type="CEDEARS",
        market="BYMA",
        settlement="A-24HS",
        payload=payload,
        requested_from="2026-09-01",
        requested_to="2026-09-04",
        attempted_at=attempted_at,
        history_store=history,
    )
    return observer,history,result


def test_open_zero_preserves_close_only_without_inventing_ohlc():
    payload=[{
        "date":"2026-09-03T00:00:00-03:00",
        "openingPrice":0,
        "max":8.34,
        "min":8.34,
        "price":0.21,
        "volume":0,
    }]
    observer,history,result=_ingest(payload)
    assert result["full_ohlc_rows"]==0
    assert result["close_only_rows"]==1
    with history.connect() as c:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "history_canonical_v2" in tables:
            assert c.execute("SELECT COUNT(*) FROM history_canonical_v2").fetchone()[0]==0
        row=c.execute("SELECT * FROM history_close_canonical_v1").fetchone()
        assert row["date"]=="2026-09-03"
        assert row["close"]==0.21
        assert row["quality"]==close_series.QUALITY
    with observer.connect() as c:
        attempt=c.execute("SELECT metadata_json FROM history_attempt_ledger_v2").fetchone()
        assert '"close_only_rows":1' in attempt[0]


def test_full_ohlc_stays_in_full_store_and_is_not_duplicated_as_close_only():
    payload=[{
        "date":"2026-09-03T00:00:00-03:00",
        "openingPrice":10.0,
        "max":11.0,
        "min":9.5,
        "price":10.5,
        "volume":100,
    }]
    _,history,result=_ingest(payload)
    assert result["full_ohlc_rows"]==1
    assert result["close_only_rows"]==0
    with history.connect() as c:
        full=c.execute("SELECT open,high,low,close FROM history_canonical_v2").fetchone()
        assert tuple(full)==(10.0,11.0,9.5,10.5)
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "history_close_canonical_v1" not in tables


def test_ohlc_inconsistent_preserves_only_valid_close():
    payload=[{
        "date":"2026-09-04T00:00:00-03:00",
        "openingPrice":20.96,
        "max":20.96,
        "min":20.96,
        "price":20.88,
        "volume":522.8,
    }]
    _,history,result=_ingest(payload)
    assert result["valid_rows"]==0
    assert result["close_only_rows"]==1
    with history.connect() as c:
        row=c.execute("SELECT close,quality FROM history_close_canonical_v1").fetchone()
        assert tuple(row)==(20.88,"CLOSE_ONLY_PROVIDER_PARTIAL")


def test_invalid_close_is_not_salvaged_even_if_full_rejection_mentions_open():
    payload=[{
        "date":"2026-09-04T00:00:00-03:00",
        "openingPrice":0,
        "max":8.34,
        "min":8.34,
        "price":0,
        "volume":0,
    }]
    _,history,result=_ingest(payload)
    assert result["full_ohlc_rows"]==0
    assert result["close_only_rows"]==0
    with history.connect() as c:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "history_close_canonical_v1" not in tables


def test_close_only_never_enters_full_ohlc_canonical_table():
    payload=[
        {"date":"2026-09-03T00:00:00-03:00","openingPrice":10,"max":10,"min":10,"price":10,"volume":0},
        {"date":"2026-09-04T00:00:00-03:00","openingPrice":10,"max":10,"min":10,"price":10.2,"volume":0},
    ]
    _,history,result=_ingest(payload)
    assert result["full_ohlc_rows"]==1
    assert result["close_only_rows"]==1
    with history.connect() as c:
        full_dates=[r[0] for r in c.execute("SELECT date FROM history_canonical_v2 ORDER BY date")]
        close_dates=[r[0] for r in c.execute("SELECT date FROM history_close_canonical_v1 ORDER BY date")]
    assert full_dates==["2026-09-03"]
    assert close_dates==["2026-09-04"]


def test_identical_retry_dedupes_version_and_preserves_original_observed_at():
    payload=[{
        "date":"2026-09-04T00:00:00-03:00",
        "openingPrice":7.11,
        "max":0,
        "min":0,
        "price":7.11,
        "volume":191.97,
    }]
    observer=MemoryStore()
    history=MemoryStore()
    _ingest(payload,attempted_at="2026-09-05T12:00:00+00:00",observer=observer,history=history)
    _ingest(payload,attempted_at="2026-09-05T14:00:00+00:00",observer=observer,history=history)
    with history.connect() as c:
        versions=c.execute("SELECT COUNT(*) FROM history_close_versions_v1").fetchone()[0]
        canonical=c.execute("SELECT observed_at,version_id FROM history_close_canonical_v1").fetchone()
        version=c.execute("SELECT observed_at FROM history_close_versions_v1 WHERE id=?",(canonical["version_id"],)).fetchone()
    assert versions==1
    assert canonical["observed_at"]=="2026-09-05T12:00:00+00:00"
    assert canonical["observed_at"]==version["observed_at"]
