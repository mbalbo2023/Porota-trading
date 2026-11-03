"""A244: no real history is purged without independently proven pins/PIT."""

import ast
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from be_paper_engine import PaperStore
import dj_caucion_live_ppi_rc6 as caucion
from scripts import rc6_history_offhost_preflight as preflight


def _history_copy(tmp_path):
    path = tmp_path / 'history.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE history_versions_v2(
                id INTEGER PRIMARY KEY, symbol TEXT, instrument_type TEXT,
                market TEXT, settlement TEXT, date TEXT, source TEXT,
                metadata_json TEXT);
            INSERT INTO history_versions_v2 VALUES
              (1,'GGAL','ACCIONES','BYMA','A-24HS','2025-02-01','PPI','{}'),
              (2,'GGAL','ACCIONES','BYMA','A-24HS','2025-02-02','PPI','{}');
            CREATE TABLE history_canonical_v2(version_id INTEGER);
            CREATE TABLE history_canonical_v2_quarantine_rc6(version_id INTEGER);
            CREATE TABLE paper_implicit_pin(history_version_id INTEGER, detail_json TEXT);
            INSERT INTO paper_implicit_pin VALUES(1,'{"receipt":1}'),(999,'{}');
            CREATE TABLE paper_explicit_pin(
              version_id INTEGER REFERENCES history_versions_v2(id));
            INSERT INTO paper_explicit_pin VALUES(2);
            CREATE TABLE "quoted""archive"(version_id INTEGER);
            INSERT INTO "quoted""archive" VALUES(1);
            CREATE TABLE hidden_json_pin(payload_json TEXT);
            INSERT INTO hidden_json_pin VALUES('{"version_id":2}');
        ''')
    digest = sha256(path.read_bytes()).hexdigest()
    return path, {'source_bytes': path.stat().st_size, 'source_sha256': digest}


def test_offhost_graph_surveys_every_table_and_cannot_certify_json_or_cross_store(tmp_path):
    path, capture = _history_copy(tmp_path)
    before = sha256(path.read_bytes()).hexdigest()
    report = preflight.census(path, capture, runner_root=tmp_path)
    graph = report['local_reference_graph']
    assert graph['tables_scanned'] == 7
    columns = {(row['table'], row['column']): row
               for row in graph['possible_version_reference_columns']}
    assert columns[('paper_implicit_pin', 'history_version_id')]['nonnull_rows'] == 2
    assert columns[('paper_implicit_pin', 'history_version_id')]['matching_legacy_version_ids'] == 1
    assert columns[('paper_explicit_pin', 'version_id')]['matching_legacy_version_ids'] == 1
    assert columns[('quoted"archive', 'version_id')]['matching_legacy_version_ids'] == 1
    assert {'from_table': 'paper_explicit_pin', 'from_column': 'version_id',
            'to_table': 'history_versions_v2', 'to_column': 'id'} in graph['declared_foreign_keys']
    assert {'table': 'hidden_json_pin', 'column': 'payload_json'} in graph[
        'json_columns_with_unverified_embedded_links']
    assert graph['local_schema_survey_completed']
    assert graph['json_payload_linkage_verified'] is False
    assert graph['cross_store_and_artifact_pins_verified'] is False
    assert graph['version_ids_safe_to_purge'] == 0
    assert graph['host_purge_allowed'] is False
    assert report['retention_allowed'] is False
    assert report['source_mutated'] is False
    assert sha256(path.read_bytes()).hexdigest() == before


def test_offhost_graph_fails_closed_on_schema_budget(tmp_path, monkeypatch):
    path, capture = _history_copy(tmp_path)
    monkeypatch.setattr(preflight, 'MAX_SCHEMA_TABLES', 3)
    with pytest.raises(ValueError, match='OFFHOST_SCHEMA_TABLE_BUDGET_EXCEEDED'):
        preflight.census(path, capture, runner_root=tmp_path)


def test_legacy_monolith_retention_has_no_delete_or_vacuum_execution():
    """An accidental legacy scheduler invocation fails before DB access."""
    source = Path(__file__).resolve().parents[1].joinpath('j_main.py').read_text()
    functions = [n for n in ast.parse(source).body
                 if isinstance(n, ast.FunctionDef)
                 and n.name == 'run_monthly_data_retention_job']
    assert len(functions) == 1
    body = ast.get_source_segment(source, functions[0])
    assert 'DELETE FROM' not in body and 'conn2.execute' not in body
    compiled = compile(ast.fix_missing_locations(ast.Module(
        body=functions, type_ignores=[])), 'legacy_retention_guard', 'exec')
    namespace = {}
    exec(compiled, namespace)
    with pytest.raises(RuntimeError, match='RC6_RETENTION_PHYSICAL_PURGE_BLOCKED_PIN_PIT_UNVERIFIED'):
        namespace['run_monthly_data_retention_job']()


def test_caucion_quote_preserves_old_receipts_past_previous_2000_cap(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / 'paper.sqlite'))
    caucion.init_schema(store)
    old = [(f'PESOS7', 'BYMA', 'ARS', 'INMEDIATA', 7, None, None, None,
            'NO_PLACED_BID', caucion.SOURCE, f'2025-01-{1+(i%28):02d}T10:00:00-03:00')
           for i in range(2050)]
    with store.connect() as db:
        db.executemany('''INSERT INTO paper_caucion_live_book_history(
            ticker,market,currency,settlement,term_days,quoted_at,tna_fraction,
            available_principal,state,source,observed_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)''', old)
        db.executescript('''CREATE TRIGGER preserve_historical_receipts
            BEFORE DELETE ON paper_caucion_live_book_history
            BEGIN SELECT RAISE(ABORT, 'receipt still pinned'); END;''')
    now = datetime(2026, 10, 30, 16, 55, tzinfo=ZoneInfo('America/Argentina/Buenos_Aires'))
    monkeypatch.setattr(caucion, 'collection_window', lambda _: True)
    monkeypatch.setattr(caucion, '_catalog', lambda _: [{
        'ticker': 'PESOS3', 'market': 'BYMA', 'currency': 'ARS',
        'settlement': 'INMEDIATA', 'term_days': 3}])

    class Reader:
        def book(self, *_):
            return {'date': now.isoformat(),
                    'bids': [{'price': '21.0', 'quantity': '100000'}], 'offers': []}

    result = caucion.refresh(Reader(), store, now=now, clock_fn=lambda: now)
    assert result['ready'] == 0 and result['real_routes'] == []
    assert result['history_rows'] == 2051
    assert result['history_review_threshold_exceeded'] is True
    assert result['history_retention'] == 'HOLD_PIN_AND_PIT_UNVERIFIED'
    with store.connect() as db:
        assert tuple(db.execute('SELECT MIN(id), MAX(id), COUNT(*) '
                                'FROM paper_caucion_live_book_history').fetchone()) == (1, 2051, 2051)
        assert db.execute('SELECT ticker FROM paper_caucion_live_book_history '
                          'WHERE id=1').fetchone()[0] == 'PESOS7'
        assert db.execute('SELECT state FROM paper_caucion_live_book '
                          'WHERE ticker=?', ('PESOS3',)).fetchone()[0] == 'BOOK_QUANTITY_UNIT_UNVERIFIED'
