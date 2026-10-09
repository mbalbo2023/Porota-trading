"""The temporal index preserves every row and the legacy equal-clock order."""
from contextlib import closing
from datetime import timedelta, timezone

import pytest

from scripts.rc6_issue465_stress import AT, fixture_database
from rc6_dynamic_universe.runtime import read_runtime
from rc6_shadow_runtime.source_reads import source_tick


QUERY = """SELECT rowid,symbol,event_at,first_received_at,last_verified_at,price
    FROM ppi_intraday_points
    WHERE julianday(event_at)>=julianday(?) AND julianday(event_at)<=julianday(?)
      AND julianday(first_received_at)<=julianday(?)
      AND julianday(last_verified_at)<=julianday(?)
    ORDER BY julianday(event_at) DESC LIMIT ?"""


def test_native_index_avoids_temporal_scan_sort_without_changing_ties_or_cutoff(tmp_path):
    path = tmp_path / "source.db"
    store = fixture_database(path, catalog_count=128, observations_per_identity=5)
    args = (AT.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
            AT.isoformat(), AT.isoformat(), AT.isoformat(), 401)
    with closing(store.connect()) as connection, connection:
        connection.execute("DROP INDEX idx_intraday_event_julian_desc")
        before = [tuple(row) for row in connection.execute(QUERY, args)]
        old_plan = [row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + QUERY, args)]
        # Equal instants with different offsets retain insertion order, and a
        # future verification clock must never be made eligible by the index.
        clock = (AT - timedelta(seconds=15)).isoformat()
        connection.executemany("INSERT INTO ppi_intraday_points VALUES(?,?,?,?,?,?,?,?,?,?,?)", [
            ("OFFSET", "ACCIONES", "BYMA", "ARS", "A-24HS", clock, "123", "10",
             clock, clock, "PPI_MARKETDATA_INTRADAY"),
            ("OFFSET_B", "ACCIONES", "BYMA", "ARS", "A-24HS",
             (AT - timedelta(seconds=15)).astimezone(timezone(timedelta(hours=-3))).isoformat(),
             "123", "10", clock, clock, "PPI_MARKETDATA_INTRADAY"),
            ("FUTURE", "ACCIONES", "BYMA", "ARS", "A-24HS", clock, "124", "10",
             clock, (AT + timedelta(seconds=1)).isoformat(), "PPI_MARKETDATA_INTRADAY"),
        ])
        exact = [tuple(row) for row in connection.execute(QUERY, args)]
        connection.execute("CREATE INDEX idx_intraday_event_julian_desc "
                           "ON ppi_intraday_points(julianday(event_at) DESC)")
        indexed = [tuple(row) for row in connection.execute(QUERY, args)]
        new_plan = [row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + QUERY, args)]
    assert before and exact == indexed
    assert all(row[1] != "FUTURE" for row in indexed)
    assert any("TEMP B-TREE" in row for row in old_plan)
    assert any("SEARCH" in row and "idx_intraday_event_julian_desc" in row for row in new_plan)
    assert not any("TEMP B-TREE" in row for row in new_plan)


def test_stress_preliminary_reader_and_worker_use_the_productive_contract(tmp_path):
    path = tmp_path / "source.db"
    fixture_database(path, catalog_count=128, observations_per_identity=5)
    with source_tick(path):
        actual = read_runtime(path, as_of=AT, row_limit=1000)
        with pytest.raises(ValueError, match="SOURCE_QUERY_CONTRACT_MISMATCH"):
            read_runtime(path, as_of=AT, row_limit=1000, query_budget_seconds=2)
    assert len(actual["catalog"]) == 128
    assert len(actual["observations"]) == 640
    assert actual["safety"] == {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
                                "real_routes": "NOT_CALLED"}
