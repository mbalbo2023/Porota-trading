"""Isolated persisted Source migration, child ordering and SQLite lock guards."""
from contextlib import closing
from datetime import timedelta, timezone
import fcntl
from pathlib import Path
import sqlite3
import threading

import pytest

import bv_paper_runtime as runtime
import cf_intraday_scalping as intraday
from cg_paper_workspace import capital_profile
from be_paper_engine import PaperStore
from rc6_dynamic_universe.runtime import read_runtime
from rc6_shadow_runtime.source_reads import source_tick
from scripts.rc6_issue465_stress import AT, fixture_database
from tests.test_rc6_runtime_row_decode import _source_inventory
from tests.test_rc6_temporal_query_plan import QUERY


@pytest.fixture
def persisted_copy(tmp_path, monkeypatch):
    # main() sets READY directly; register the absent/empty prior value so the
    # fixture always restores the process environment after a parent attempt.
    monkeypatch.setenv("POROTA_RUNTIME_SCHEMA_READY", "")
    monkeypatch.setenv("PAPER_SCALPING_MODE", "OFF")
    original = tmp_path / "original.db"
    original_store = fixture_database(original, catalog_count=64, observations_per_identity=5)
    with closing(original_store.connect()) as connection, connection:
        connection.execute("DROP INDEX " + intraday.INTRADAY_TEMPORAL_INDEX)
        connection.execute("UPDATE paper_workspace SET initial_capital_json=? WHERE id=1", (capital_profile(),))
        clock = (AT - timedelta(seconds=15)).isoformat()
        shifted = (AT - timedelta(seconds=15)).astimezone(timezone(timedelta(hours=-3))).isoformat()
        connection.executemany("INSERT INTO ppi_intraday_points VALUES(?,?,?,?,?,?,?,?,?,?,?)", [
            ("TIE_UTC", "ACCIONES", "BYMA", "ARS", "A-24HS", clock,
             "123", "10", clock, clock, "PPI_MARKETDATA_INTRADAY"),
            ("TIE_OFFSET", "ACCIONES", "BYMA", "ARS", "A-24HS", shifted,
             "124", "11", clock, clock, "PPI_MARKETDATA_INTRADAY"),
            ("FUTURE_VERIFICATION", "ACCIONES", "BYMA", "ARS", "A-24HS", clock,
             "125", "12", clock, (AT + timedelta(seconds=1)).isoformat(), "PPI_MARKETDATA_INTRADAY"),
        ])
    copied = tmp_path / "copied.db"
    with closing(sqlite3.connect(original)) as source, closing(sqlite3.connect(copied)) as target:
        source.backup(target)
    monkeypatch.setenv(runtime.DB_ENV, str(copied))
    # This handle owns no writer; every test supplies its own closed connection.
    class Store:
        path = str(copied)
        def connect(self):
            connection = sqlite3.connect(copied, timeout=.35)
            connection.row_factory = sqlite3.Row
            return connection
    yield Store(), original


def durable_rows(store):
    with closing(store.connect()) as connection:
        names = ("paper_workspace", "observer_state", "paper_positions", "paper_fills",
                 "paper_decisions", "financial_instrument_catalog", "ppi_intraday_points")
        return {name: [tuple(row) for row in connection.execute("SELECT rowid,* FROM " + name + " ORDER BY rowid")]
                for name in names}


def temporal_query(connection):
    args = (AT.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
            AT.isoformat(), AT.isoformat(), AT.isoformat(), 1000)
    return [tuple(row) for row in connection.execute(QUERY, args)], [row[3] for row in
        connection.execute("EXPLAIN QUERY PLAN " + QUERY, args)]


def test_parent_migrates_persisted_copy_before_first_shadow_without_scalping_worker(persisted_copy, monkeypatch):
    store, original = persisted_copy
    original_bytes = original.read_bytes()
    before = durable_rows(store)
    with closing(store.connect()) as connection:
        legacy, plan = temporal_query(connection)
    assert any("TEMP B-TREE" in row for row in plan)
    native_children = runtime.ChildProcesses
    seen = []
    def before_children(commands):
        assert "intraday_scalping" not in commands
        assert __import__("os").environ["POROTA_RUNTIME_SCHEMA_READY"] == "1"
        with closing(store.connect()) as connection:
            intraday.require_intraday_temporal_index(connection)
            actual, indexed_plan = temporal_query(connection)
        assert actual == legacy
        assert any("SEARCH" in row and intraday.INTRADAY_TEMPORAL_INDEX in row for row in indexed_plan)
        assert not any("TEMP B-TREE" in row or "SCAN ppi_intraday_points" in row for row in indexed_plan)
        identity = _source_inventory(Path(store.path))
        with source_tick(store.path):
            report = read_runtime(store.path, as_of=AT, row_limit=1000)
        assert _source_inventory(Path(store.path)) == identity
        observations = report["observations"]
        assert [row["identity"][0] for row in observations] == [row[1] for row in legacy]
        assert [row["source_at"] for row in observations] == [row[2] for row in legacy]
        assert report["safety"] == {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0, "real_routes": "NOT_CALLED"}
        ties = [row[1] for row in legacy if row[1].startswith("TIE_")]
        assert ties == ["TIE_UTC", "TIE_OFFSET"]
        assert all(row[1] != "FUTURE_VERIFICATION" for row in legacy)
        seen.append(commands)
        return native_children(commands)
    monkeypatch.setattr(runtime, "ChildProcesses", before_children)
    def clock(parent_store, children, stop):
        assert children.commands["dynamic_shadow"][-1] == "--dynamic-shadow-worker"
        with open(store.path + ".runtime.lock", "a") as contender:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setattr(runtime, "run_clock", clock)
    assert runtime.main([]) == 0 and len(seen) == 1
    assert durable_rows(store) == before and original.read_bytes() == original_bytes
    # The parent FIN released its runtime lock and every migration writer.
    with open(store.path + ".runtime.lock", "a") as contender:
        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with closing(store.connect()) as writer, writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO paper_events(event_at,source,event_type,detail) VALUES(?,?,?,?)",
                       (AT.isoformat(), "PRODUCTION_PAPER", "ISOLATED_POST_MIGRATION_WRITER", "no financial effect"))


def test_second_parent_cannot_open_sqlite_or_migrate_a_running_supervisor(persisted_copy, monkeypatch):
    store, _ = persisted_copy
    before = _source_inventory(Path(store.path))
    def forbidden():
        pytest.fail("Second parent must fail before runtime_store or any DDL")
    monkeypatch.setattr(runtime, "runtime_store", forbidden)
    with open(store.path + ".runtime.lock", "a") as active:
        fcntl.flock(active, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            runtime.main([])
    assert _source_inventory(Path(store.path)) == before


def test_shadow_missing_index_fails_closed_without_migrating_persisted_source(persisted_copy):
    store, _ = persisted_copy
    before = _source_inventory(Path(store.path))
    with pytest.raises(RuntimeError, match="SOURCE_TEMPORAL_INDEX_REQUIRED"):
        with source_tick(store.path):
            read_runtime(store.path, as_of=AT, row_limit=1000)
    assert _source_inventory(Path(store.path)) == before


@pytest.mark.parametrize("expression", ("julianday(event_at) ASC", "event_at DESC",
                                         "julianday(first_received_at) DESC"))
def test_wrong_named_index_cannot_be_accepted_or_repaired_by_readers(persisted_copy, expression):
    store, _ = persisted_copy
    with closing(store.connect()) as writer, writer:
        writer.execute("CREATE INDEX " + intraday.INTRADAY_TEMPORAL_INDEX + " ON ppi_intraday_points(" + expression + ")")
    before = _source_inventory(Path(store.path))
    with pytest.raises(RuntimeError, match="SOURCE_TEMPORAL_INDEX_REQUIRED"):
        with source_tick(store.path):
            read_runtime(store.path, as_of=AT, row_limit=1000)
    assert _source_inventory(Path(store.path)) == before


def test_genuine_writer_contention_aborts_migration_and_releases_its_connection(persisted_copy):
    store, _ = persisted_copy
    before = durable_rows(store)
    with closing(store.connect()) as writer:
        writer.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            intraday.prepare_shadow_source_index(store)
        assert writer.in_transaction
        writer.rollback()
    assert durable_rows(store) == before
    with closing(store.connect()) as connection:
        with pytest.raises(RuntimeError, match="SOURCE_TEMPORAL_INDEX_REQUIRED"):
            intraday.require_intraday_temporal_index(connection)
        connection.execute("BEGIN IMMEDIATE")
        connection.rollback()


def test_inherited_scalping_child_verifies_schema_without_ddl_or_parent_dependency(persisted_copy, monkeypatch):
    store, _ = persisted_copy
    intraday.init_schema(store)
    statements = []
    native_connect = store.connect
    def traced():
        connection = native_connect()
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(store, "connect", traced)
    monkeypatch.setenv("POROTA_RUNTIME_SCHEMA_READY", "1")
    monkeypatch.setattr(intraday, "init_schema", lambda *_: pytest.fail("Inherited child must never migrate"))
    stop = threading.Event()
    stop.set()
    intraday.run_worker(store, stop, clock_fn=lambda: AT.isoformat())
    assert statements
    assert not any(row.lstrip().upper().startswith(("CREATE", "ALTER", "DROP")) for row in statements)


def test_constructor_closes_every_schema_connection_before_returning(tmp_path, monkeypatch):
    monkeypatch.setenv("POROTA_RUNTIME_SCHEMA_READY", "")
    connections = []
    native = PaperStore.connect
    def observed(store):
        connection = native(store)
        connections.append(connection)
        return connection
    monkeypatch.setattr(PaperStore, "connect", observed)
    store = PaperStore(str(tmp_path / "constructor.db"))
    assert len(connections) >= 10
    assert not hasattr(store, "_schema_connections")
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
    # A later normal caller owns its connection and is not silently closed.
    with closing(store.connect()) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.rollback()


def test_failed_schema_scope_closes_and_rolls_back_only_its_owned_connections(persisted_copy):
    copied, _ = persisted_copy
    store = PaperStore(copied.path)
    before = durable_rows(copied)
    with pytest.raises(ValueError, match="CONTROLLED_SCHEMA_ABORT"):
        with store.schema_preparation():
            connection = store.connect()
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE ppi_intraday_points SET price='999' WHERE rowid=1")
            with pytest.raises(RuntimeError, match="PAPER_SCHEMA_CONNECTION_SCOPE_REENTRY"):
                with store.schema_preparation():
                    pytest.fail("Schema scope must be exclusive")
            raise ValueError("CONTROLLED_SCHEMA_ABORT")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    assert not hasattr(store, "_schema_connections")
    assert durable_rows(copied) == before
