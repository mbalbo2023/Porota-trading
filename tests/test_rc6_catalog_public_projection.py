"""The public metadata projection must avoid reading the large catalog payload."""
import json
import sqlite3

from be_paper_engine import PaperStore
from bu_instrument_catalog import init_schema

FIELDS = 'ticker,instrument_type,market,currency,settlement,settlement_source,last_seen_at,status,capability'
QUERY = ('SELECT COUNT(*),json_group_array(json_array(' + FIELDS + ')) FROM '
         '(SELECT ' + FIELDS + ' FROM financial_instrument_catalog '
         'ORDER BY instrument_type,market,currency,ticker,settlement LIMIT ?)')


def test_public_metadata_projection_is_covered_without_payload_lookups(tmp_path):
    store = PaperStore(str(tmp_path/'paper.db'))
    init_schema(store)
    with store.connect() as c:
        for currency in ('ARS','USD'):
            c.execute('INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                      ('SAME','ACCIONES','BYMA',currency,'A-24HS','PPI','public',
                       '2026-10-17T00:00:00Z','fixture','AVAILABLE','READY_PAPER',
                       'PRIVATE_PAYLOAD_'*50000))
        plan = ' | '.join(r[3] for r in c.execute('EXPLAIN QUERY PLAN '+QUERY,(20001,)))
        assert 'COVERING INDEX idx_catalog_public_metadata_v1' in plan
        assert 'TEMP B-TREE' not in plan
        count, payload = c.execute(QUERY,(20001,)).fetchone()
        assert count == 2
        assert {row[3] for row in json.loads(payload)} == {'ARS','USD'}
        assert 'PRIVATE_' not in payload


def test_public_projection_index_is_idempotent_and_tracks_clocks_and_identity(tmp_path):
    store = PaperStore(str(tmp_path/'paper.db'))
    init_schema(store); init_schema(store)
    with store.connect() as c:
        indices = c.execute("PRAGMA index_list('financial_instrument_catalog')").fetchall()
        assert sum(row[1] == 'idx_catalog_public_metadata_v1' for row in indices) == 1
        columns = [row[2] for row in c.execute("PRAGMA index_info('idx_catalog_public_metadata_v1')")]
        assert set(columns) == set(FIELDS.split(','))
        c.execute('INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                  ('SAME','ACCIONES','BYMA','ARS','CI','PPI','public',
                   '2026-10-16T00:00:00Z','fixture','AVAILABLE','READY_PAPER','PRIVATE'))
        c.execute("UPDATE financial_instrument_catalog SET last_seen_at='2026-10-17T00:00:00Z'")
        count, payload = c.execute(QUERY,(20001,)).fetchone()
        assert count == 1 and json.loads(payload)[0][6] == '2026-10-17T00:00:00Z'
