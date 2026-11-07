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


@pytest.mark.parametrize('operation', ['daily_risk', 'capacity'])
def test_borrowed_risk_preserves_caller_transaction(tmp_path, operation):
    from test_rc6_final_shared_risk import make_broker, START
    from dh_paper_dynamic_risk_gate_hf6 import portfolio_capacity
    broker = make_broker(tmp_path, [START])
    connection = broker.store.connect()
    try:
        connection.execute('BEGIN IMMEDIATE')
        if operation == 'daily_risk':
            broker.daily_risk.evaluate(START, connection=connection)
        else:
            portfolio_capacity(broker, 'ARS', START, connection=connection)
        assert connection.in_transaction
        assert connection.execute('SELECT COUNT(*) FROM paper_daily_risk').fetchone()[0] > 0
        connection.rollback()
        assert connection.execute('SELECT COUNT(*) FROM paper_daily_risk').fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize('operation', ['observer_schema', 'operations_schema', 'catalog_schema',
                                      'daily_risk', 'policy_context', 'future_positions', 'capacity'])
def test_a978_owned_callers_close_every_created_connection(tmp_path, monkeypatch, operation):
    from test_rc6_final_shared_risk import make_broker, START
    broker = make_broker(tmp_path, [START])
    store = broker.store
    original = store.connect
    captured = []

    def capture():
        connection = original()
        captured.append(connection)
        return connection

    monkeypatch.setattr(store, 'connect', capture)
    if operation == 'observer_schema':
        from bf_production_paper_observer import _support_schema
        _support_schema(store)
    elif operation == 'operations_schema':
        from bi_operational_services import init_schema
        init_schema(store)
    elif operation == 'catalog_schema':
        from bu_instrument_catalog import init_schema
        init_schema(store)
    elif operation == 'daily_risk':
        broker.daily_risk.evaluate(START)
    elif operation == 'policy_context':
        from es_policy_context_rc6 import collect
        collect(store, at=START)
    elif operation == 'future_positions':
        from rc6_paper_family_lifecycle import future_positions
        future_positions(store)
    else:
        from dh_paper_dynamic_risk_gate_hf6 import portfolio_capacity
        portfolio_capacity(broker, 'ARS', START)
    assert captured
    for connection in captured:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            connection.execute('SELECT 1')
