"""Offline regressions for census instrumentation; no broker/network imports."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("census", Path(__file__).resolve().parents[1] / "scripts" / "rc6_paper_blockers_census.py")
census = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(census)


class CensusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "fixture.db"
        self.writer = sqlite3.connect(self.path)
        self.writer.execute("PRAGMA journal_mode=WAL")
        self.writer.executescript("""
        CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER,heartbeat_at TEXT,ppi_auth TEXT,process_state TEXT,session_state TEXT);
        INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0,'2026-10-02T20:00:00+00:00','OK','WAITING_MARKET','MARKET_CLOSED');
        CREATE TABLE candidate_identity_v2(ticker TEXT,instrument_type TEXT,status TEXT,can_simulate INTEGER,paused_reason TEXT);
        CREATE TABLE financial_instrument_catalog(ticker TEXT,metadata_json TEXT);
        INSERT INTO candidate_identity_v2 VALUES('A','OPCIONES','AVAILABLE',1,'');
        INSERT INTO candidate_identity_v2 VALUES('B','FUTUROS','PAUSED_EXPLICIT',0,'PPI_NEGATIVE');
        INSERT INTO candidate_identity_v2 VALUES('C','BONOS','READY',1,'INVALID_LITERAL_READY');
        """)
        self.writer.commit()

    def tearDown(self):
        self.writer.close()
        self.tmp.cleanup()

    def test_full_canonical_count_no_literal_ready(self):
        data = census.collect(self.path)
        self.assertEqual(data['summary']['total'], 3)
        self.assertEqual(data['summary']['ready'], 1)
        self.assertTrue(data['complete'])
        self.assertEqual(data['database_writes'], 0)

    def test_all_identities_not_sample(self):
        self.writer.executemany('INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?)',
            [(str(i),'ON','PAUSED_EXPLICIT',0,'IDENTITY_AMBIGUOUS') for i in range(1200)])
        self.writer.commit()
        self.assertEqual(census.collect(self.path)['summary']['total'], 1203)

    def test_query_only_leaves_logical_database_unchanged(self):
        before = '\n'.join(self.writer.iterdump())
        census.collect(self.path)
        self.assertEqual(before, '\n'.join(self.writer.iterdump()))

    def test_sensitive_nested_json_redacted(self):
        self.writer.execute('INSERT INTO financial_instrument_catalog VALUES(?,?)',
            ('A',json.dumps({'contract':{'multiplier':100},'access_token':'not-a-real-token','nested':[{'account_number':'fake'}]})))
        self.writer.commit()
        row = census.collect(self.path)['tables']['financial_instrument_catalog'][0]
        self.assertEqual(row['metadata_json']['access_token'], 'REDACTED')
        self.assertEqual(row['metadata_json']['nested'][0]['account_number'], 'REDACTED')
        self.assertEqual(row['metadata_json']['contract']['multiplier'],100)

    def test_nonpaper_and_null_real_order_count_rejected(self):
        for mode, count in [('LIVE',0),('PRODUCTION_PAPER',1),('PRODUCTION_PAPER',None)]:
            self.writer.execute('UPDATE observer_state SET mode=?,real_orders_sent=?',(mode,count))
            self.writer.commit()
            with self.assertRaisesRegex(RuntimeError,'PAPER_INVARIANT'):
                census.collect(self.path)

    def test_row_limit_no_partial_success(self):
        with self.assertRaisesRegex(RuntimeError,'ROW_LIMIT'):
            census.collect(self.path,max_rows=1)

    def test_deadline_no_partial_success(self):
        with self.assertRaises((TimeoutError,sqlite3.OperationalError)):
            census.collect(self.path,deadline_seconds=-1)

    def test_existing_wal_required(self):
        self.writer.close()
        self.writer = sqlite3.connect(self.path)
        self.writer.execute('PRAGMA journal_mode=DELETE')
        with self.assertRaisesRegex(RuntimeError,'REQUIRES_EXISTING_WAL'):
            census.collect(self.path)

    def test_missing_database_not_created(self):
        absent = Path(self.tmp.name) / 'absent.db'
        with self.assertRaises(FileNotFoundError):
            census.collect(absent)
        self.assertFalse(absent.exists())

    def test_required_schema_missing_fails(self):
        self.writer.execute('DROP TABLE candidate_identity_v2')
        self.writer.commit()
        with self.assertRaisesRegex(RuntimeError,'REQUIRED_TABLE_MISSING'):
            census.collect(self.path)

    def test_full_database_tables_not_exported(self):
        self.writer.execute('CREATE TABLE broker_accounts(secret TEXT)')
        self.writer.execute("INSERT INTO broker_accounts VALUES('not-a-real-secret')")
        self.writer.commit()
        data=census.collect(self.path)
        self.assertNotIn('broker_accounts',data['tables'])
        self.assertNotIn('not-a-real-secret',json.dumps(data))


if __name__ == '__main__':
    unittest.main()
