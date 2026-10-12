"""The native observer's dated repair flag is a source-preserving diagnostic."""
from pathlib import Path
import sqlite3

import pytest

import bf_production_paper_observer as observer
from tests.test_rc6_history_snapshot_copy import inventory


def historical_marker(tmp_path, *, wal, complete):
    writer_path = tmp_path / "writer.sqlite"
    writer = sqlite3.connect(writer_path)
    if wal:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    writer.execute("CREATE TABLE rc6_history_cutoff_repair_runs(cutoff TEXT PRIMARY KEY,state TEXT)")
    writer.execute("INSERT INTO rc6_history_cutoff_repair_runs VALUES(?,?)",
        ("2026-09-21", "COMPLETE" if complete else "INCOMPLETE"))
    writer.commit()
    folder = tmp_path / "captured-source"
    folder.mkdir()
    source = folder / "history.sqlite"
    source.write_bytes(writer_path.read_bytes())
    if wal:
        Path(str(source) + "-wal").write_bytes(Path(str(writer_path) + "-wal").read_bytes())
    writer.close()
    return folder, source


@pytest.mark.parametrize("wal", [False, True])
@pytest.mark.parametrize("complete", [False, True])
def test_native_observer_repair_diagnostic_reads_dated_flag_without_changing_source(
        tmp_path, monkeypatch, wal, complete):
    folder, source = historical_marker(tmp_path, wal=wal, complete=complete)
    monkeypatch.setenv("HIST_DB_PATH", str(source))
    before = inventory(folder)
    assert observer._history_cutoff_repair_complete() is complete
    assert inventory(folder) == before
    assert not Path(str(source) + "-shm").exists()
    assert not Path(str(source) + "-journal").exists()


def test_native_observer_missing_history_diagnostic_creates_no_database_or_directory(tmp_path, monkeypatch):
    source = tmp_path / "absent-directory" / "history.sqlite"
    monkeypatch.setenv("HIST_DB_PATH", str(source))
    assert observer._history_cutoff_repair_complete() is False
    assert not source.parent.exists()


def test_native_observer_invalid_history_diagnostic_preserves_evidence_and_stays_closed(tmp_path, monkeypatch):
    source = tmp_path / "history.sqlite"
    source.write_bytes(b"not-a-sqlite-database")
    monkeypatch.setenv("HIST_DB_PATH", str(source))
    before = inventory(tmp_path)
    assert observer._history_cutoff_repair_complete() is False
    assert inventory(tmp_path) == before
