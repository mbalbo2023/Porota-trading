import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
SOURCE=HERE/'rc6_family_source_readonly.py'
if not SOURCE.is_file(): SOURCE=HERE.parent/'scripts'/'rc6_family_source_readonly.py'
spec=importlib.util.spec_from_file_location('readonly_audit',SOURCE)
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)

class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/'test.db'
        self.c=sqlite3.connect(self.db)
        self.c.executescript('''
        CREATE TABLE observer_state(id INTEGER,mode TEXT,real_orders_sent INTEGER,ppi_auth TEXT,process_state TEXT,session_state TEXT,heartbeat_at TEXT,last_market_data_at TEXT);
        INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0,'AUTHENTICATED','WAITING_MARKET','MARKET_CLOSED','2026-10-29T22:00:00Z','2026-10-29T21:00:00Z');
        CREATE TABLE financial_instrument_catalog(ticker,instrument_type,market,currency,settlement,settlement_source,status,capability,last_seen_at,metadata_json);
        CREATE TABLE paper_caucion_live_book(ticker,market,currency,settlement,term_days,quoted_at,state,source,observed_at,tna_fraction,available_principal);
        INSERT INTO paper_caucion_live_book VALUES('PESOS3','BYMA','ARS','INMEDIATA',3,'2026-10-29T20:00:00Z','STALE_BOOK','PPI_MARKETDATA_BOOK_READONLY','2026-10-29T20:00:00Z','PRIVATE_PRICE','PRIVATE_AMOUNT');
        ''')
        meta=json.dumps({'paper_caucion_contract_v1':{'fee_authority':'PPI_FEE_V1','paper_fill_policy':'LIVE_PPI_BID_PARTICIPATION_CAP'},'secret':'PRIVATE_SECRET'})
        self.c.execute("INSERT INTO financial_instrument_catalog VALUES('PESOS3','CAUCIONES','BYMA','ARS','INMEDIATA','PPI','AVAILABLE','READY_PAPER_CAUCION_PLACING','2026-10-29T22:00:00Z',?)",(meta,))
        self.c.execute("INSERT INTO financial_instrument_catalog VALUES('YMCJO','ON','BYMA','ARS','A-24HS','PPI','AVAILABLE','READY_PAPER','2026-10-29T22:00:00Z',?)",(json.dumps({'units_per_lot':100,'secret':'PRIVATE_SECRET'}),))
        self.c.commit()
    def tearDown(self):
        self.c.close();self.tmp.cleanup()
    def test_no_database_mutation(self):
        before=self.db.read_bytes();audit.audit(self.db);self.assertEqual(before,self.db.read_bytes())
    def test_metadata_only(self):
        result=audit.audit(self.db)
        self.assertNotIn('PRIVATE_',json.dumps(result))
        self.assertEqual(result['ymcjo_identity'][0]['unit_fields'],{'units_per_lot':100})
        self.assertTrue(result['caucion_contract_flags'][0]['fee_authority_present'])
        self.assertNotIn('PPI_FEE_V1',json.dumps(result))
    def test_missing_optional_tables(self):
        result=audit.audit(self.db)
        self.assertEqual(result['candidate_identity_v2'],'MISSING_TABLE')
    def test_reject_real_orders(self):
        self.c.execute('UPDATE observer_state SET real_orders_sent=1');self.c.commit()
        with self.assertRaises(RuntimeError):audit.audit(self.db)
    def test_reject_wrong_mode(self):
        self.c.execute("UPDATE observer_state SET mode='REAL'");self.c.commit()
        with self.assertRaises(RuntimeError):audit.audit(self.db)
    def test_missing_db_not_created(self):
        missing=Path(self.tmp.name)/'missing.db'
        with self.assertRaises(sqlite3.Error):audit.audit(missing)
        self.assertFalse(missing.exists())
    def test_redaction(self):
        self.assertEqual(audit.code('token_secret_abcdef'),'REDACTED_NON_CODE')

if __name__=='__main__':unittest.main()
