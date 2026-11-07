"""Owned contexts close; borrowed PAPER transactions retain caller custody."""
import sqlite3

import pytest

from be_paper_engine import PaperStore


def test_owned_context_commits_and_closes(tmp_path):
    store = PaperStore(str(tmp_path / 'paper.db'))
    connection = store.connect()
    with connection as entered:
        assert entered is connection
        connection.execute('CREATE TABLE ownership_probe(value INTEGER)')
        connection.execute('INSERT INTO ownership_probe VALUES(7)')
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connection.execute('SELECT 1')
    with store.connect() as reopened:
        assert reopened.execute('SELECT value FROM ownership_probe').fetchone()[0] == 7


def test_owned_context_rolls_back_and_closes(tmp_path):
    store = PaperStore(str(tmp_path / 'paper.db'))
    with store.connect() as setup:
        setup.execute('CREATE TABLE ownership_probe(value INTEGER)')
    connection = store.connect()
    with pytest.raises(ValueError, match='body failure'):
        with connection:
            connection.execute('INSERT INTO ownership_probe VALUES(7)')
            raise ValueError('body failure')
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connection.execute('SELECT 1')
    with store.connect() as reopened:
        assert reopened.execute('SELECT COUNT(*) FROM ownership_probe').fetchone()[0] == 0


def test_owned_commit_failure_closes_and_preserves_failure(tmp_path):
    store = PaperStore(str(tmp_path / 'paper.db'))
    connection = store.connect()
    with pytest.raises(sqlite3.IntegrityError):
        with connection:
            connection.execute('CREATE TABLE ownership_parent(id INTEGER PRIMARY KEY)')
            connection.execute('CREATE TABLE ownership_child(id INTEGER REFERENCES ownership_parent(id) DEFERRABLE INITIALLY DEFERRED)')
            connection.execute('INSERT INTO ownership_child VALUES(7)')
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connection.execute('SELECT 1')
    with store.connect() as reopened:
        assert reopened.execute('SELECT COUNT(*) FROM ownership_child').fetchone()[0] == 0


def test_borrowed_future_projection_keeps_transaction_open(tmp_path):
    from rc6_paper_family_lifecycle import future_positions
    store = PaperStore(str(tmp_path / 'paper.db'))
    connection = store.connect()
    try:
        connection.execute('BEGIN IMMEDIATE')
        assert future_positions(store, connection=connection) == []
        assert connection.in_transaction
        assert connection.execute('SELECT 1').fetchone()[0] == 1
        connection.rollback()
    finally:
        connection.close()
