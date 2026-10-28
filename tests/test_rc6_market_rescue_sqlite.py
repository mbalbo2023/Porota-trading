"""Protect the read-only SHADOW paths against unbounded live table scans."""
from datetime import datetime, timezone
import sqlite3

from be_paper_engine import PaperStore
from bf_production_paper_observer import _support_schema
from cf_intraday_scalping import init_schema as init_intraday_schema
from rc6_dynamic_universe.runtime import read_runtime
from rc6_shadow_runtime.worker import ShadowRuntime


AT = datetime(2026, 10, 14, 15, 30, tzinfo=timezone.utc)


def _plan(connection, sql, *params):
    return " | ".join(row[3] for row in connection.execute(
        "EXPLAIN QUERY PLAN " + sql, params))


def test_shadow_live_query_shapes_are_indexed_and_read_only(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    _support_schema(store)
    init_intraday_schema(store)
    # Repeat initialization as the parent does after a container replacement.
    _support_schema(store)
    init_intraday_schema(store)
    with store.connect() as connection:
        catalog = _plan(connection, """SELECT ticker FROM financial_instrument_catalog
            WHERE status='AVAILABLE' AND capability LIKE 'READY_PAPER%'
            ORDER BY instrument_type,market,currency,ticker,settlement LIMIT ?""", 20001)
        full = _plan(connection, """SELECT ticker FROM financial_instrument_catalog
            ORDER BY instrument_type,market,currency,ticker,settlement LIMIT ?""", 20001)
        intraday = _plan(connection, """SELECT event_at FROM ppi_intraday_points
            WHERE julianday(event_at)>=julianday(?) AND julianday(event_at)<=julianday(?)
              AND julianday(first_received_at)<=julianday(?)
              AND julianday(last_verified_at)<=julianday(?)
            ORDER BY julianday(event_at) DESC LIMIT ?""", AT.isoformat(),
            AT.isoformat(), AT.isoformat(), AT.isoformat(), 20001)
        snapshots = _plan(connection, """SELECT observed_at FROM market_snapshots NOT INDEXED
            WHERE id >= (SELECT MIN(id) FROM market_snapshots
                WHERE julianday(observed_at)<=julianday(?)
                  AND julianday(observed_at)>=julianday(?))
              AND julianday(observed_at)<=julianday(?)
              AND julianday(observed_at)>=julianday(?)
            ORDER BY id DESC LIMIT ?""", AT.isoformat(), AT.isoformat(),
            AT.isoformat(), AT.isoformat(), 20001)
        events = _plan(connection, """SELECT event_at FROM paper_events
            WHERE event_type IN ('INTRADAY_SCALPING_UNSUPPORTED','INTRADAY_SCALPING_ERROR')
              AND julianday(event_at)>=julianday(?) AND julianday(event_at)<=julianday(?)
            ORDER BY id DESC LIMIT 200""", AT.isoformat(), AT.isoformat())
        assert "idx_shadow_catalog_order" in catalog
        assert "idx_shadow_catalog_order" in full
        assert "TEMP B-TREE" not in catalog + full
        assert "idx_shadow_intraday_event_jd" in intraday
        assert "USING INTEGER PRIMARY KEY" in snapshots
        assert "idx_shadow_snapshot_observed_jd" in snapshots
        assert "TEMP B-TREE" not in snapshots
        assert "idx_shadow_event_type_at_jd" in events
    with sqlite3.connect(store.path) as observer:
        before = observer.execute("PRAGMA data_version").fetchone()[0]
        data = read_runtime(store.path, as_of=AT, query_budget_seconds=1.5)
        shadow = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
        shadow._metadata(AT, AT.isoformat())
        after = observer.execute("PRAGMA data_version").fetchone()[0]
    assert data["source_database_effect"] == "READ_ONLY"
    assert data["safety"] == {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
                              "real_routes": "NOT_CALLED"}
    assert after == before


def test_bounded_snapshot_rowid_scan_preserves_filtered_newest_first_semantics():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY, observed_at TEXT)")
    connection.execute("CREATE INDEX idx_shadow_snapshot_observed_jd "
                       "ON market_snapshots(julianday(observed_at),id)")
    connection.executemany("INSERT INTO market_snapshots VALUES(?,?)", [
        (1, "2026-10-14T15:30:00+00:00"),
        (2, "2026-10-13T15:30:00+00:00"),
        (3, "2026-10-14T15:10:00+00:00"),
        (4, "2026-10-15T15:30:00+00:00"),
        (5, "2026-10-14T15:20:00+00:00"),
    ])
    predicate = """WHERE julianday(observed_at)<=julianday(?)
        AND julianday(observed_at)>=julianday(?) ORDER BY id DESC LIMIT ?"""
    parameters = ("2026-10-14T15:40:00+00:00",
                  "2026-10-14T00:00:00+00:00", 2)
    before = connection.execute("SELECT id FROM market_snapshots " + predicate,
                                parameters).fetchall()
    bounded = """WHERE id >= (SELECT MIN(id) FROM market_snapshots
        WHERE julianday(observed_at)<=julianday(?)
          AND julianday(observed_at)>=julianday(?))
        AND julianday(observed_at)<=julianday(?)
        AND julianday(observed_at)>=julianday(?)
        ORDER BY id DESC LIMIT ?"""
    args = parameters[:2] + parameters
    after = connection.execute("SELECT id FROM market_snapshots NOT INDEXED " + bounded,
                               args).fetchall()
    assert after == before == [(5,), (3,)]
    plan = _plan(connection, "SELECT id FROM market_snapshots NOT INDEXED " + bounded,
                 *args)
    assert "USING INTEGER PRIMARY KEY" in plan
    assert "idx_shadow_snapshot_observed_jd" in plan
    assert "TEMP B-TREE" not in plan
    missing = ("2026-10-12T16:00:00+00:00", "2026-10-12T00:00:00+00:00", 2)
    assert connection.execute("SELECT id FROM market_snapshots NOT INDEXED " + bounded,
                              missing[:2] + missing).fetchall() == []
