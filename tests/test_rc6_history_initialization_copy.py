"""The canonical writer inspects a copy before any native WAL negotiation."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

import pytest

import cu_history_store_v2_hf6 as history
from cv_history_store_adapter_hf6 import HistoricalStore
import ea_history_close_series_hf2 as close_history
from tests.test_rc6_history_snapshot_copy import inventory


def legacy_source(tmp_path, kind, mode):
    builder = tmp_path/"builder.sqlite"
    connection = sqlite3.connect(builder)
    if mode == "WAL_WITHOUT_SHM":
        connection.execute("PRAGMA journal_mode=WAL")
    tables = ("history_versions_v2", "history_canonical_v2") if kind == "FULL" else (
        "history_close_versions_v1", "history_close_canonical_v1")
    for table in tables:
        connection.execute("CREATE TABLE "+table+"(symbol TEXT)")
        connection.execute("INSERT INTO "+table+" VALUES('retained-legacy-row')")
    connection.execute("CREATE TABLE unrelated_evidence(value TEXT)")
    connection.execute("INSERT INTO unrelated_evidence VALUES('retained-evidence')")
    connection.commit()
    folder = tmp_path/"source"
    folder.mkdir()
    source = folder/"legacy.sqlite"
    source.write_bytes(builder.read_bytes())
    if mode == "WAL_WITHOUT_SHM":
        Path(str(source)+"-wal").write_bytes(Path(str(builder)+"-wal").read_bytes())
    connection.close()
    return folder, source


@pytest.mark.parametrize("kind", ["FULL", "CLOSE_ONLY"])
@pytest.mark.parametrize("mode", ["DELETE", "WAL_WITHOUT_SHM"])
@pytest.mark.parametrize("initializer", ["FULL", "CLOSE_ONLY"])
def test_NEW_canonical_init_rejects_legacy_before_source_sqlite_wal_or_metadata_mutation(tmp_path, monkeypatch, record_property, kind, mode, initializer):
    folder, source = legacy_source(tmp_path, kind, mode)
    before = inventory(folder)
    actual_connect = sqlite3.connect
    def guarded(path, *args, **kwargs):
        assert str(source) not in str(path), "LEGACY_REJECTION_MUST_PRECEDE_SOURCE_SQLITE"
        return actual_connect(path, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", guarded)
    initialize = history.init_schema if initializer == "FULL" else close_history.init_schema
    with pytest.raises(ValueError, match="COPY_MIGRATION_REQUIRED"):
        initialize(HistoricalStore(str(source)))
    assert inventory(folder) == before
    assert not Path(str(source)+"-shm").exists()
    record_property("legacy_init_copy", json.dumps({"source_kind": kind, "source_mode": mode,
        "initializer": initializer, "before": before, "after": inventory(folder),
        "source_sqlite_opens": 0, "wal_negotiations": 0, "source_unchanged": True}))


def test_NEW_new_native_writer_validates_initializes_commits_then_negotiates_wal(tmp_path):
    source = tmp_path/"native.sqlite"
    store = HistoricalStore(str(source))
    history.init_schema(store)
    with store.connect() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert {row[1] for row in connection.execute("PRAGMA table_info(history_versions_v2)")} >= {
            "currency", "price_basis", "adjustment_basis", "volume_kind", "version_known_at"}
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    value = history.Candle("NATIVE", "ACCIONES", "BYMA", "A-24HS", "2026-09-30", 100, 102, 98,
        100, 10, "PPI_API", False, "2026-10-01T16:00:00Z", {"volume_unit": "SHARES"},
        currency="ARS", volume_kind="QUANTITY", provider_at="2026-09-30T20:00:00Z")
    assert history.append_candle(store, value)["version_appended"]
    with store.connect() as connection:
        assert connection.execute("SELECT currency,volume_kind FROM history_versions_v2").fetchone()[:] == ("ARS", "QUANTITY")


def test_NEW_native_initializer_preflight_is_once_per_store_not_each_append_or_query(tmp_path, monkeypatch):
    source = tmp_path/"native.sqlite"
    history.init_schema(HistoricalStore(str(source)))
    store = HistoricalStore(str(source))
    import rc6_audit_evidence.sqlite_snapshot as snapshots
    actual_copy = snapshots.readonly_copy
    captured = []
    @contextmanager
    def counted(path, **kwargs):
        captured.append(str(path))
        with actual_copy(path, **kwargs) as copied:
            yield copied
    monkeypatch.setattr(snapshots, "readonly_copy", counted)
    for hour in ("16", "17"):
        value = history.Candle("NATIVE", "ACCIONES", "BYMA", "A-24HS", "2026-09-30", 100, 102, 98,
            100, 10, "PPI_API", False, f"2026-10-01T{hour}:00:00Z", {"volume_unit": "SHARES"},
            currency="ARS", volume_kind="QUANTITY", provider_at="2026-09-30T20:00:00Z")
        history.append_candle(store, value)
        with store.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM history_versions_v2").fetchone()[0] == 1
    assert captured == [str(source)]


def test_NEW_initialized_writer_cannot_open_a_replaced_inode_without_copy_preflight(tmp_path):
    source = tmp_path/"native.sqlite"
    store = HistoricalStore(str(source))
    history.init_schema(store)
    source.rename(tmp_path/"preserved.sqlite")
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE history_versions_v2(symbol TEXT)")
    before = inventory(tmp_path)
    with pytest.raises(ValueError, match="SOURCE_CHANGED"):
        store.connect()
    with pytest.raises(ValueError, match="COPY_MIGRATION_REQUIRED"):
        history.init_schema(store)
    assert inventory(tmp_path) == before
