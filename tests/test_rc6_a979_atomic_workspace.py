"""Fresh bootstrap publication and dirfd namespace operations, never VFS custody."""
import os
from pathlib import Path
import sqlite3

import pytest

import cg_paper_workspace as workspace
from be_paper_engine import PaperStore


def test_failed_fresh_schema_never_publishes_partial_database(tmp_path, monkeypatch):
    target = tmp_path / 'paper.db'
    original = PaperStore.init_db

    def fail_after_schema(store):
        original(store)
        raise RuntimeError('injected fresh bootstrap failure')

    monkeypatch.setattr(PaperStore, 'init_db', fail_after_schema)
    with pytest.raises(RuntimeError, match='injected fresh'):
        workspace.runtime_store(target)
    assert not target.exists()
    assert not Path(str(target) + '-wal').exists()
    monkeypatch.setattr(PaperStore, 'init_db', original)
    restarted = workspace.runtime_store(target)
    assert workspace.read_identity(restarted.path)['initial_capital_json'] == workspace.capital_profile()


def test_fresh_name_not_visible_until_capital_and_schema_complete(tmp_path, monkeypatch):
    target = tmp_path / 'paper.db'
    original = workspace.identity_from_connection
    observed = []

    def inspect(connection):
        observed.append(target.exists())
        return original(connection)

    monkeypatch.setattr(workspace, 'identity_from_connection', inspect)
    store = workspace.runtime_store(target)
    assert observed and not any(observed)
    assert Path(store.path) == target
    assert workspace.read_identity(target)['initial_capital_json'] == workspace.capital_profile()


def test_parent_replacement_before_lock_never_creates_foreign_lock(tmp_path, monkeypatch):
    parent = tmp_path / 'own'
    parent.mkdir()
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    target = parent / 'paper.db'
    original = os.open
    replaced = False

    def replace_before_lock(path, flags, *args, **kwargs):
        nonlocal replaced
        if str(path).endswith('.initialization.lock') and not replaced:
            replaced = True
            parent.rename(tmp_path / 'moved')
            parent.symlink_to(foreign, target_is_directory=True)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, 'open', replace_before_lock)
    with pytest.raises((ValueError, OSError)):
        workspace.runtime_store(target)
    assert replaced
    assert list(foreign.iterdir()) == []


def test_competing_final_name_is_not_overwritten(tmp_path, monkeypatch):
    target = tmp_path / 'paper.db'
    original = workspace.identity_from_connection

    def competing_name(connection):
        value = original(connection)
        if not target.exists():
            target.write_bytes(b'FOREIGN COMPETING FILE')
        return value

    monkeypatch.setattr(workspace, 'identity_from_connection', competing_name)
    with pytest.raises((ValueError, OSError)):
        workspace.runtime_store(target)
    assert target.read_bytes() == b'FOREIGN COMPETING FILE'
