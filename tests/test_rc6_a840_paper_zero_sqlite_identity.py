"""A840: PAPER/0 SQLite preflight must not trust a swapped pathname."""
from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import sqlite3

import pytest

from scripts import rc6_source_cycle_dispatch as dispatch


def _db(path: Path, *, sent: int = 0) -> None:
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute("CREATE TABLE observer_state(id INT, mode TEXT, real_orders_sent INT)")
            connection.execute("INSERT INTO observer_state VALUES (1,'PRODUCTION_PAPER',?)", (sent,))


def test_paper_zero_pinned_path_reads_actual_owned_sqlite(tmp_path):
    db = tmp_path / "owned.db"
    _db(db)
    assert dispatch._paper_zero(db) is None


def test_final_symlink_injected_at_sqlite_connect_rejected_before_foreign_result(tmp_path, monkeypatch):
    db = tmp_path / "owned.db"
    foreign = tmp_path / "another_application.db"
    saved = tmp_path / "saved_owned.db"
    _db(db)
    _db(foreign)
    original = sqlite3.connect
    injected = []

    def swap_at_connect(database, *args, **kwargs):
        if str(db) in str(database) and "mode=ro" in str(database) and not injected:
            db.rename(saved)
            db.symlink_to(foreign)
            injected.append(True)
        return original(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", swap_at_connect)
    with pytest.raises(dispatch.SourceCycleBlocked, match="PAPER_DATABASE_IDENTITY_UNVERIFIED"):
        dispatch._paper_zero(db)
    assert injected == [True]
    assert db.is_symlink()


def test_foreign_hardlink_as_paper_db_rejected(tmp_path):
    foreign = tmp_path / "another_application.db"
    _db(foreign)
    db = tmp_path / "owned.db"
    os.link(foreign, db)
    with pytest.raises(dispatch.SourceCycleBlocked, match="PAPER_DATABASE_IDENTITY_UNVERIFIED"):
        dispatch._paper_zero(db)
    assert foreign.stat().st_nlink == 2


def test_foreign_db_through_symlinked_parent_rejected(tmp_path):
    foreign_parent = tmp_path / "another_application"
    foreign_parent.mkdir()
    foreign = foreign_parent / "owned.db"
    _db(foreign)
    alias = tmp_path / "market"
    alias.symlink_to(foreign_parent, target_is_directory=True)
    with pytest.raises(dispatch.SourceCycleBlocked, match="PAPER_DATABASE_IDENTITY_UNVERIFIED"):
        dispatch._paper_zero(alias / foreign.name)


def test_name_swap_during_sql_read_discards_successful_foreign_claim(tmp_path, monkeypatch):
    db = tmp_path / "owned.db"
    foreign = tmp_path / "another_application.db"
    saved = tmp_path / "saved_owned.db"
    _db(db)
    _db(foreign)
    original = sqlite3.connect
    injected = []

    class SwappingReader:
        def __init__(self, reader):
            self.reader = reader

        def execute(self, sql, *args):
            if sql.startswith("SELECT mode,real_orders_sent") and not injected:
                db.rename(saved)
                db.symlink_to(foreign)
                injected.append(True)
            return self.reader.execute(sql, *args)

        def close(self):
            self.reader.close()

    def wrap_connect(database, *args, **kwargs):
        result = original(database, *args, **kwargs)
        return SwappingReader(result) if str(db) in str(database) and "mode=ro" in str(database) else result

    monkeypatch.setattr(sqlite3, "connect", wrap_connect)
    with pytest.raises(dispatch.SourceCycleBlocked, match="PAPER_DATABASE_IDENTITY_UNVERIFIED"):
        dispatch._paper_zero(db)
    assert injected == [True]


def test_mode_changed_during_sql_read_discards_unsafe_identity(tmp_path, monkeypatch):
    db = tmp_path / "owned.db"
    _db(db)
    original = sqlite3.connect
    modified = []

    class ModeChangingReader:
        def __init__(self, reader):
            self.reader = reader

        def execute(self, sql, *args):
            if sql.startswith("SELECT mode,real_orders_sent") and not modified:
                db.chmod(0o600)
                modified.append(True)
            return self.reader.execute(sql, *args)

        def close(self):
            self.reader.close()

    def wrap_connect(database, *args, **kwargs):
        result = original(database, *args, **kwargs)
        return ModeChangingReader(result) if str(db) in str(database) and "mode=ro" in str(database) else result

    monkeypatch.setattr(sqlite3, "connect", wrap_connect)
    with pytest.raises(dispatch.SourceCycleBlocked, match="PAPER_DATABASE_IDENTITY_UNVERIFIED"):
        dispatch._paper_zero(db)
    assert modified == [True]


def test_wal_row_is_observed_not_stale_main_database(tmp_path):
    db = tmp_path / "owned.db"
    with closing(sqlite3.connect(db)) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        with writer:
            writer.execute("CREATE TABLE observer_state(id INT, mode TEXT, real_orders_sent INT)")
            writer.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0)")
        assert (tmp_path / "owned.db-wal").exists()
        assert dispatch._paper_zero(db) is None
        with writer:
            writer.execute("UPDATE observer_state SET real_orders_sent=1 WHERE id=1")
        with pytest.raises(dispatch.SourceCycleBlocked, match="PAPER_ZERO_REQUIRED"):
            dispatch._paper_zero(db)


def test_no_wal_production_db_main_mode_0600_is_valid_if_identity_stable(tmp_path):
    db = tmp_path / "owned.db"
    _db(db)
    db.chmod(0o600)
    assert dispatch._paper_zero(db) is None
