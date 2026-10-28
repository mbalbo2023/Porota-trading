"""Forma agregada observada el 28/08/2026; importes/IDs sólo ficticios.

No es una copia ni una reconstrucción de las filas del servidor.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from be_paper_engine import PaperBroker, PaperStore
from scripts import v17_ledger_preflight as probe


@pytest.fixture
def legacy_shape(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    path = source / 'synthetic_legacy.db'
    with closing(sqlite3.connect(path)) as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
          CREATE TABLE paper_positions(
            paper_id TEXT PRIMARY KEY, source TEXT NOT NULL,
            strategy_version TEXT NOT NULL, symbol TEXT NOT NULL,
            asset_class TEXT NOT NULL, settlement TEXT NOT NULL,
            status TEXT NOT NULL, quantity TEXT NOT NULL,
            entry_price TEXT NOT NULL, entry_cost TEXT NOT NULL,
            stop_price TEXT NOT NULL, target_price TEXT NOT NULL,
            opened_at TEXT NOT NULL, closed_at TEXT, exit_price TEXT,
            exit_cost TEXT, gross_pnl TEXT, net_pnl TEXT, close_reason TEXT,
            features_json TEXT NOT NULL,
            max_favorable TEXT NOT NULL DEFAULT '0',
            max_adverse TEXT NOT NULL DEFAULT '0');
          CREATE TABLE paper_fills(
            id INTEGER PRIMARY KEY AUTOINCREMENT, paper_id TEXT NOT NULL,
            source TEXT NOT NULL, side TEXT NOT NULL, filled_at TEXT NOT NULL,
            quantity TEXT NOT NULL, price TEXT NOT NULL, costs TEXT NOT NULL,
            slippage TEXT NOT NULL);
        ''')
        for i in range(10):
            state = ('UNRECONCILED_FIXTURE' if i == 3 else
                     'CLOSED' if i < 7 else 'OPEN')
            opened = f'2026-08-28T14:{i:02d}:00+00:00'
            closed = '2026-08-28T15:00:00+00:00' if state == 'CLOSED' else None
            paper_id = f'FIXTURE-ONLY-{i}'
            c.execute('''INSERT INTO paper_positions
                (paper_id,source,strategy_version,symbol,asset_class,settlement,
                 status,quantity,entry_price,entry_cost,stop_price,target_price,
                 opened_at,closed_at,exit_price,exit_cost,gross_pnl,net_pnl,
                 close_reason,features_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (paper_id, 'PRODUCTION_PAPER', 'SYNTHETIC_ONLY', f'FIXTURE{i}',
                 'ACCIONES', 'A-24HS', state, '10', '100', '1', '98', '110',
                 opened, closed, '110' if closed else None,
                 '1' if closed else None, '100' if closed else None,
                 '98' if closed else None, 'TEST_ONLY' if closed else None, '{}'))
            c.execute('INSERT INTO paper_fills VALUES(NULL,?,?,?,?,?,?,?,?)',
                      (paper_id, 'PRODUCTION_PAPER', 'BUY_SIMULATED',
                       opened, '10', '100', '1', '0'))
            if closed:
                c.execute('INSERT INTO paper_fills VALUES(NULL,?,?,?,?,?,?,?,?)',
                          (paper_id, 'PRODUCTION_PAPER', 'SELL_SIMULATED',
                           closed, '10', '110', '1', '0'))
        c.commit()
    return path


def test_observed_shape_is_review_not_permission_to_promote(legacy_shape, tmp_path):
    before = legacy_shape.read_bytes()
    scratch = tmp_path / 'private_copy'
    scratch.mkdir()
    report = probe.collect_copy(legacy_shape, scratch)
    assert report['status'] == 'OBSERVED_REVIEW_REQUIRED'
    assert report['counts'] == {
        'paper_positions': 10, 'paper_fills': 16,
        'paper_spot_sales': None, 'paper_sale_receivables': None,
        'positions_checked': 10, 'arithmetically_consistent': 9,
        'positions_inconsistent': 1, 'missing_closed_sale_receipts': 6,
        'position_states': {'CLOSED': 6, 'UNKNOWN': 1, 'OPEN': 3}}
    assert report['issues'] == {'SPOT_LEDGER_INCONSISTENT': 1}
    assert report['examples'] == [{'row_number': 4, 'issue': 'SPOT_LEDGER_INCONSISTENT'}]
    assert report['legacy_projection'] == {
        'currency': 'ARS', 'market': 'BYMA', 'currency_source': 'LEGACY_ASSUMED_ARS'}
    assert not report['database_modified'] and not report['migration_performed']
    assert not report['promotion_allowed']
    assert report['temporary_copy_removed'] and list(scratch.iterdir()) == []
    assert legacy_shape.read_bytes() == before
    assert 'UNRECONCILED_FIXTURE' not in json.dumps(report)
    assert 'FIXTURE-ONLY' not in json.dumps(report)


def test_synthetic_migration_does_not_repair_unknown_or_certify_currency(legacy_shape):
    # Sólo sobre la fixture local. No autoriza ejecutar PaperStore en producción.
    store = PaperStore(str(legacy_shape))
    with store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM paper_positions').fetchone()[0] == 10
        assert c.execute('SELECT COUNT(*) FROM paper_fills').fetchone()[0] == 16
        assert c.execute("SELECT status FROM paper_positions WHERE paper_id='FIXTURE-ONLY-3'").fetchone()[0] == 'UNRECONCILED_FIXTURE'
        assert {r[0] for r in c.execute('SELECT currency_source FROM paper_positions')} == {'LEGACY_ASSUMED_ARS'}
        receipts = c.execute('SELECT basis FROM paper_sale_receivables').fetchall()
        assert len(receipts) == 6
        assert {r[0] for r in receipts} == {'PENDING_CONFIRMATION'}
    # Recibos modelados no convierten la posición desconocida en saldo libre.
    broker = PaperBroker(store, initial_cash='100000')
    with pytest.raises(ValueError, match='Cronología'):
        broker._cash('2026-09-01T15:00:00+00:00')
    opened, invalid = store.exit_positions()
    assert len(opened) == 3 and len(invalid) == 1
    assert invalid[0][0]['status'] == 'UNRECONCILED_FIXTURE'


def test_nine_consistent_rows_does_not_mean_verified_legacy_history(legacy_shape):
    # Quitar la inconsistencia EN LA FIXTURE no elimina los otros pendientes.
    with closing(sqlite3.connect(legacy_shape)) as c:
        c.execute("UPDATE paper_positions SET status='OPEN' WHERE paper_id='FIXTURE-ONLY-3'")
        c.commit()
    report = probe.collect(legacy_shape)
    assert report['counts']['arithmetically_consistent'] == 10
    assert report['issues'] == {}
    assert report['status'] == 'OBSERVED_REVIEW_REQUIRED'
    assert report['counts']['missing_closed_sale_receipts'] == 6
    assert report['legacy_projection']['currency_source'] == 'LEGACY_ASSUMED_ARS'
    assert not report['promotion_allowed']
