"""Local readiness/alias regressions; not a physical custody attestation."""
from contextlib import closing
import gc
import os
import sqlite3

import pytest

import cg_paper_workspace as workspace
from be_paper_engine import PaperStore


def test_ready_child_rejects_changed_capital(tmp_path, monkeypatch):
    path = tmp_path / 'paper.db'
    workspace.runtime_store(path, environ={'PAPER_INITIAL_CAPITAL_ARS': '222'})
    monkeypatch.setenv('POROTA_RUNTIME_SCHEMA_READY', '1')
    monkeypatch.setenv('PAPER_INITIAL_CAPITAL_ARS', '333')
    with pytest.raises(ValueError):
        PaperStore(str(path))


def test_ready_child_cannot_create_missing_database(tmp_path, monkeypatch):
    path = tmp_path / 'missing' / 'paper.db'
    monkeypatch.setenv('POROTA_RUNTIME_SCHEMA_READY', '1')
    with pytest.raises((ValueError, RuntimeError)):
        PaperStore(str(path))
    assert not path.parent.exists()


@pytest.mark.parametrize('table', ['observer_state', 'paper_causal_decision_receipts'])
def test_ready_child_rejects_missing_schema_without_repair(tmp_path, monkeypatch, table):
    path = tmp_path / 'paper.db'
    workspace.runtime_store(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute('DROP TABLE ' + table)
    monkeypatch.setenv('POROTA_RUNTIME_SCHEMA_READY', '1')
    with pytest.raises(ValueError):
        PaperStore(str(path))
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone() is None


def test_parent_symlink_rejected_before_foreign_creation(tmp_path):
    own = tmp_path / 'own'
    foreign = tmp_path / 'foreign'
    own.mkdir()
    foreign.mkdir()
    (own / 'redirect').symlink_to(foreign, target_is_directory=True)
    with pytest.raises(ValueError):
        workspace.runtime_store(own / 'redirect' / 'paper.db')
    assert list(foreign.iterdir()) == []


@pytest.mark.parametrize('kind', ['symlink', 'hardlink'])
def test_foreign_lock_alias_rejected_before_database_creation(tmp_path, kind):
    target = tmp_path / 'paper.db'
    foreign = tmp_path / 'foreign.txt'
    foreign.write_bytes(b'FOREIGN')
    lock = tmp_path / 'paper.db.initialization.lock'
    if kind == 'symlink':
        lock.symlink_to(foreign)
    else:
        os.link(foreign, lock)
    with pytest.raises(ValueError):
        workspace.runtime_store(target)
    assert not target.exists()
    assert foreign.read_bytes() == b'FOREIGN'


@pytest.mark.parametrize('suffix', ['-wal', '-shm'])
def test_orphan_sidecar_is_not_adopted_by_fresh_start(tmp_path, suffix):
    path = tmp_path / 'paper.db'
    sidecar = tmp_path / ('paper.db' + suffix)
    sidecar.write_bytes(b'FOREIGN SIDECAR')
    with pytest.raises(ValueError):
        workspace.runtime_store(path)
    assert not path.exists()
    assert sidecar.read_bytes() == b'FOREIGN SIDECAR'


def test_valid_ready_child_preserves_schema_and_identity(tmp_path, monkeypatch):
    path = tmp_path / 'paper.db'
    workspace.runtime_store(path)
    with closing(sqlite3.connect(path)) as connection:
        before = connection.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall()
    identity = workspace.read_identity(path)
    monkeypatch.setenv('POROTA_RUNTIME_SCHEMA_READY', '1')
    child = PaperStore(str(path))
    assert child.path == str(path)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall() == before
    assert workspace.read_identity(path) == identity


def test_ready_child_rejects_missing_column_without_repair(tmp_path, monkeypatch):
    path = tmp_path / 'paper.db'
    workspace.runtime_store(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute('ALTER TABLE observer_state DROP COLUMN real_orders_sent')
    monkeypatch.setenv('POROTA_RUNTIME_SCHEMA_READY', '1')
    with pytest.raises(ValueError, match='CHILD_SCHEMA_REQUIRED'):
        PaperStore(str(path))
    with closing(sqlite3.connect(path)) as connection:
        assert 'real_orders_sent' not in {row[1] for row in connection.execute('PRAGMA table_info(observer_state)')}


def test_ready_child_rejects_non_wal_without_changing_journal(tmp_path, monkeypatch):
    path = tmp_path / 'paper.db'
    workspace.runtime_store(path)
    # Current parent connections can still await GC; the fixture owns all of them.
    gc.collect()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute('PRAGMA journal_mode=DELETE').fetchone()[0] == 'delete'
    monkeypatch.setenv('POROTA_RUNTIME_SCHEMA_READY', '1')
    with pytest.raises(ValueError, match='PAPER_SQLITE_WAL_REQUIRED'):
        PaperStore(str(path))
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
