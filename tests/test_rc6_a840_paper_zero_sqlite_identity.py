"""A840: PAPER/0 SQLite preflight must not trust a swapped pathname."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from scripts import rc6_source_cycle_dispatch as dispatch


def _db(path: Path, *, sent: int = 0) -> None:
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute("CREATE TABLE observer_state(id INT, mode TEXT, real_orders_sent INT)")
            connection.execute("INSERT INTO observer_state VALUES (1,'PRODUCTION_PAPER',?)", (sent,))


def test_paper_zero_local_zero_without_independent_identity_stays_blocked(tmp_path):
    db = tmp_path / "owned.db"
    _db(db)
    with pytest.raises(dispatch.SourceCycleBlocked, match="^PAPER_DATABASE_IDENTITY_UNVERIFIED$"):
        dispatch._paper_zero(db)


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
        with pytest.raises(dispatch.SourceCycleBlocked, match="^PAPER_DATABASE_IDENTITY_UNVERIFIED$"):
            dispatch._paper_zero(db)
        with writer:
            writer.execute("UPDATE observer_state SET real_orders_sent=1 WHERE id=1")
        with pytest.raises(dispatch.SourceCycleBlocked, match="PAPER_ZERO_REQUIRED"):
            dispatch._paper_zero(db)


def test_private_file_mode_is_not_independent_database_identity(tmp_path):
    db = tmp_path / "owned.db"
    _db(db)
    db.chmod(0o600)
    with pytest.raises(dispatch.SourceCycleBlocked, match="^PAPER_DATABASE_IDENTITY_UNVERIFIED$"):
        dispatch._paper_zero(db)


@pytest.mark.parametrize("mode,sent", [("PRODUCTION_PAPER", 1), ("LIVE", 0),
                                      ("PRODUCTION_PAPER", -1), (None, 0)])
def test_negative_paper_evidence_remains_blocking(tmp_path, mode, sent):
    db = tmp_path / "owned.db"
    _db(db, sent=sent)
    with closing(sqlite3.connect(db)) as connection:
        with connection:
            connection.execute("UPDATE observer_state SET mode=?", (mode,))
    with pytest.raises(dispatch.SourceCycleBlocked, match="^PAPER_ZERO_REQUIRED$"):
        dispatch._paper_zero(db)


@pytest.mark.parametrize("hour", [13, 20])  # Regular and late windows, UTC.
def test_identity_hold_leaves_source_cycle_without_effects(tmp_path, hour):
    db = tmp_path / "owned.db"
    _db(db)
    market = tmp_path / "market"
    market.mkdir()
    state = market / "rc6_source_cycle_dispatch_state.json"
    state.write_text(json.dumps({"schema": dispatch.STATE_SCHEMA,
                                "session_date": None,
                                "last_public_success_at": None,
                                "last_evidence_success_at": None}))
    before = state.read_bytes()
    db_before = db.read_bytes()
    calls = []

    def runner(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1, stdout="")

    with pytest.raises(dispatch.SourceCycleBlocked, match="^PAPER_DATABASE_IDENTITY_UNVERIFIED$"):
        dispatch.run_cycle(now=datetime(2026, 11, 5, hour, 30, tzinfo=timezone.utc),
                           db=db, market_root=market, lock=tmp_path / "source.lock",
                           runner=runner)
    assert calls == []
    assert state.read_bytes() == before
    assert db.read_bytes() == db_before
    assert sorted(path.name for path in market.iterdir()) == [state.name]
    assert not (tmp_path / "source.lock").exists()


def test_cli_identity_hold_reports_blocked_exit_two_without_positive_counter(tmp_path, monkeypatch, capsys):
    db = tmp_path / "owned.db"
    _db(db)
    market = tmp_path / "market"
    market.mkdir()
    original_cycle = dispatch.run_cycle
    calls = []

    def runner(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1, stdout="")

    # Adapt only clock/paths/process boundary; execute the real safety gate.
    def local_cycle(**kwargs):
        return original_cycle(now=datetime(2026, 11, 5, 13, 30, tzinfo=timezone.utc),
                              db=db, market_root=market,
                              lock=tmp_path / "source.lock", runner=runner)

    monkeypatch.setattr(dispatch, "run_cycle", local_cycle)
    assert dispatch.main([]) == 2
    payload = json.loads(capsys.readouterr().out.split("=", 1)[1])
    assert payload == {"status": "BLOCKED", "reason": "PAPER_DATABASE_IDENTITY_UNVERIFIED",
                       "stages": {}}
    assert calls == []
    assert not list(market.iterdir())
    assert not (tmp_path / "source.lock").exists()
