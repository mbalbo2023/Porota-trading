"""A3 schema preflight stays bounded after the history file outgrows scratch."""
import hashlib
import os
import sqlite3

import pytest

from cv_history_store_adapter_hf6 import HistoricalStore
from cu_history_store_v2_hf6 import init_schema
import rc6_audit_evidence.sqlite_scratch as scratch
import rc6_audit_evidence.sqlite_snapshot as snapshot


def _large_for_fixture(monkeypatch):
    # A real host source is 749,617,152 B. Keep the regression small while
    # forcing the same >512 MiB admission branch.
    monkeypatch.setattr(scratch, 'DEFAULT_BYTES', 1)
    monkeypatch.setattr(snapshot, 'readonly_copy',
                        lambda *args, **kwargs: pytest.fail('full copy on large history'))


def test_large_history_validates_schema_without_copy_or_source_write(tmp_path, monkeypatch):
    _large_for_fixture(monkeypatch)
    path = tmp_path / 'history.db'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE marker(value INTEGER)')
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    seen = []

    def validate(c):
        seen.append(c.execute('PRAGMA query_only').fetchone()[0])
        assert c.execute('SELECT name FROM sqlite_master WHERE name=?', ('marker',)).fetchone()[0] == 'marker'

    store = HistoricalStore(str(path))
    store.prepare_history_schema(validate)
    assert seen == [1]
    assert store._prepared_identity == store._path_identity()[1]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ['history.db']


def test_large_history_rejects_legacy_schema_without_writing(tmp_path, monkeypatch):
    _large_for_fixture(monkeypatch)
    path = tmp_path / 'history.db'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE history_versions_v2(id INTEGER)')
    before = path.read_bytes()
    store = HistoricalStore(str(path))
    with pytest.raises(ValueError, match='HISTORY_COPY_MIGRATION_REQUIRED'):
        init_schema(store)
    assert store._prepared_identity is None
    assert path.read_bytes() == before


def test_large_history_rejects_source_inode_swap_before_write(tmp_path, monkeypatch):
    _large_for_fixture(monkeypatch)
    path = tmp_path / 'history.db'
    replacement = tmp_path / 'replacement.db'
    for file in (path, replacement):
        with sqlite3.connect(file) as c:
            c.execute('CREATE TABLE marker(value INTEGER)')
    store = HistoricalStore(str(path))

    def swap(c):
        assert c.execute('PRAGMA query_only').fetchone()[0] == 1
        os.replace(replacement, path)

    with pytest.raises(ValueError, match='HISTORY_INITIALIZATION_SOURCE_CHANGED'):
        store.prepare_history_schema(swap)
    assert store._prepared_identity is None
