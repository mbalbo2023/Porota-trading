"""Do not spend SHADOW's transform budget on contracts without observations."""
from datetime import timedelta
import sqlite3

import pytest

from rc6_dynamic_universe import runtime
from tests import test_rc6_shadow_sqlite_stage_diagnostics as fixtures

AT = fixtures.AT


@pytest.fixture
def database():
    case = fixtures.ReadStages(); case.setUp()
    try:
        with sqlite3.connect(case.db) as c:
            c.executescript('''
                CREATE TABLE ppi_intraday_points(symbol TEXT,asset_class TEXT,market TEXT,currency TEXT,settlement TEXT,event_at TEXT,first_received_at TEXT,last_verified_at TEXT,price REAL,volume REAL,source TEXT);
                CREATE TABLE ppi_intraday_contract_state(symbol TEXT,asset_class TEXT,market TEXT,currency TEXT,settlement TEXT,state TEXT,last_source_at TEXT,checked_at TEXT);
            ''')
            c.executemany('INSERT INTO ppi_intraday_contract_state VALUES(?,?,?,?,?,?,?,?)',
                [(f'S{i:05d}', 'ACCIONES', 'BYMA', 'ARS', 'A-24HS',
                  'CONFIRMED_INTERVAL_VOLUME', AT.isoformat(), AT.isoformat()) for i in range(2424)])
        yield case.db
    finally:
        case.doCleanups()


def point(db, symbol='S00000'):
    with sqlite3.connect(db) as c:
        c.execute('INSERT INTO ppi_intraday_points VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (symbol, 'ACCIONES', 'BYMA', 'ARS', 'A-24HS', AT.isoformat(), AT.isoformat(), AT.isoformat(), 100, 10, 'TEST'))


def test_empty_intraday_does_not_exhaust_budget_parsing_2424_unconsumed_contracts(database, monkeypatch):
    real_stamp = runtime.stamp
    conversions = [0]
    def costed_stamp(value):
        conversions[0] += 1
        return real_stamp(value)
    monkeypatch.setattr(runtime, 'stamp', costed_stamp)
    monkeypatch.setattr(runtime, 'monotonic', lambda: conversions[0] * .01)
    diagnostics = {}
    result = runtime.read_runtime(database, as_of=AT, query_budget_seconds=2, diagnostics=diagnostics)
    assert conversions[0] == 1  # Only as_of; contracts cannot affect absent points.
    assert result['observations'] == []
    assert len(result['full_catalog']) == 3000
    assert diagnostics['transform_elapsed_ms'] < 2000


def test_only_observed_identity_has_contract_timestamps_converted(database, monkeypatch):
    point(database)
    real_stamp = runtime.stamp
    calls = []
    def tracked_stamp(value):
        calls.append(value)
        return real_stamp(value)
    monkeypatch.setattr(runtime, 'stamp', tracked_stamp)
    result = runtime.read_runtime(database, as_of=AT, query_budget_seconds=2)
    assert len(calls) == 3
    assert result['observations'][0]['intraday_confirmed'] is True
    assert result['observations'][0]['entry_authority'] is False


@pytest.mark.parametrize('source_delta,checked_delta', [(-121, 0), (1, 1), (0, -1), (0, 121)])
def test_requested_contract_retains_freshness_and_order_fail_closed(database, source_delta, checked_delta):
    point(database)
    with sqlite3.connect(database) as c:
        c.execute('UPDATE ppi_intraday_contract_state SET last_source_at=?,checked_at=? WHERE symbol=?',
            ((AT + timedelta(seconds=source_delta)).isoformat(),
             (AT + timedelta(seconds=checked_delta)).isoformat(), 'S00000'))
    result = runtime.read_runtime(database, as_of=AT, query_budget_seconds=2)
    assert result['observations'][0]['intraday_confirmed'] is False
    assert result['observations'][0]['volume_semantics'] == 'NO_VERIFICADO'


def test_consumed_contract_work_still_cannot_overrun_transform_budget(database, monkeypatch):
    point(database)
    real_stamp = runtime.stamp
    conversions = [0]
    def costed_stamp(value):
        conversions[0] += 1
        return real_stamp(value)
    monkeypatch.setattr(runtime, 'stamp', costed_stamp)
    monkeypatch.setattr(runtime, 'monotonic', lambda: conversions[0] * 1.1)
    with pytest.raises(ValueError, match='RUNTIME_TRANSFORM_BUDGET_EXCEEDED'):
        runtime.read_runtime(database, as_of=AT, query_budget_seconds=2)
