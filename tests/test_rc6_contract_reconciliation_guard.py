"""Offline unit and real-reconciler regressions. SQLite fixtures are temporary.

The observer function is extracted from its actual AST; application startup,
broker clients, timers and production paths are never imported or executed.
"""
from __future__ import annotations
import ast
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import bu_instrument_catalog as catalog
import rc6_contract_reconciliation_guard as guard

NOW='2026-10-03T01:36:06+00:00'


def fixture(family='OPCIONES'):
    primary=dict(ticker='TESTOPT' if family=='OPCIONES' else 'TESTON',instrument_type=family,
        market='BYMA',currency='ARS',settlement='INMEDIATA',settlement_source='PPI_FIELD',
        description='Synthetic offline fixture',last_seen_at=NOW,run_id='offline',status='AVAILABLE',
        capability='CONTRACT_EVIDENCE_REVIEW_REQUIRED',raw={'_discovery_source':'PPI_PRIMARY',
            '_provider_instrument_type':'ON' if family=='OBLIGACIONES' else family,
            'isin':'ARTEST000001','cajaValoresCode':'123','nominalInPrice':100,
            '_contract_bridge':{'status':'BLOCKED','gaps':['CHANGE_REVIEW_REQUIRED']}})
    contract=dict(family=family,market='BYMA',currency='ARS',settlement='INMEDIATA',
        cash_multiplier='100' if family=='OPCIONES' else '0.01',quantity_step='1',minimum_quantity='1',
        metadata_source='CONTRACT_EVIDENCE_V2_BOUND')
    if family=='OPCIONES':
        contract.update(underlying='TEST',strike='100',option_right='CALL',expires_at='2026-12-18T20:00:00+00:00')
    proof=dict(source_class='PPI_STRUCTURED_API',source_ref='PPI:offline-fixture',
               evidence_hash='1'*64,observed_at=NOW,effective_at=None)
    contract['field_provenance']={k:dict(proof) for k in contract if k not in {'family','metadata_source'}}
    complement={k:primary[k] for k in guard.IDENTITY_FIELDS}
    complement.update(source='CONTRACT_EVIDENCE_V2',observed_at=NOW,financial_contract_v17=contract,
        identity_evidence=dict(market_explicit=True,currency_explicit=True,settlement_explicit=True),
        contract_bridge=dict(status='NORMALIZED',gaps=[],field_provenance=contract['field_provenance'],observed_at=NOW))
    return primary,complement


def alias_fixture():
    primary,comp=fixture('OBLIGACIONES')
    primary['capability']='NEEDS_NOMINAL_UNITS'
    primary['raw'].pop('_contract_bridge')
    old=copy.deepcopy(primary)
    old.update(instrument_type='ON',status='STALE',last_seen_at='2026-09-01T00:00:00+00:00')
    old['raw']['_discovery_source']='LEGACY_CATALOG'
    return primary,old,comp


class GuardTests(unittest.TestCase):
    def test_exact_normalized_current_contract_recovers_review(self):
        p,c=fixture()
        self.assertTrue(guard.reconciliation_allowed(p,c,checked_at=NOW))

    def test_real_provider_negatives_and_conflicts_stay_blocked(self):
        for code in guard.HARD_PRIMARY_BLOCKS:
            with self.subTest(code=code):
                p,c=fixture();p['capability']=code
                self.assertFalse(guard.reconciliation_allowed(p,c,checked_at=NOW))

    def test_material_change_remains_pending(self):
        p,c=fixture();c['contract_bridge'].update(status='BLOCKED',gaps=['CHANGE_REVIEW_REQUIRED'])
        self.assertFalse(guard.reconciliation_allowed(p,c,checked_at=NOW))

    def test_generic_iol_or_false_normalized_proof_cannot_clear_review(self):
        for change in ('source','missing_contract','empty_proof','hash','gaps','identity_proof'):
            with self.subTest(change=change):
                p,c=fixture()
                if change=='source':c['source']='IOL_COMPLEMENTARY'
                elif change=='missing_contract':c['financial_contract_v17']=None
                elif change=='empty_proof':c['financial_contract_v17']['field_provenance']={}
                elif change=='hash':next(iter(c['financial_contract_v17']['field_provenance'].values()))['evidence_hash']='not-a-hash'
                elif change=='gaps':c['contract_bridge']['gaps']=['MISSING:strike']
                else:c['identity_evidence']['settlement_explicit']=False
                self.assertFalse(guard.reconciliation_allowed(p,c,checked_at=NOW))

    def test_identity_mismatch_never_clears_review(self):
        for key in guard.IDENTITY_FIELDS:
            p,c=fixture();c[key]='WRONG'
            self.assertFalse(guard.reconciliation_allowed(p,c,checked_at=NOW))

    def test_old_missing_naive_and_future_ppi_identity_rejected(self):
        for stamp in ('2026-08-01T00:00:00+00:00',None,'2026-10-03T01:36:06','2030-01-01T00:00:00+00:00'):
            p,c=fixture();p['last_seen_at']=stamp
            self.assertFalse(guard.reconciliation_allowed(p,c,checked_at=NOW))

    def test_negative_flags_and_unverified_primary_rejected(self):
        for key in ('operable','isActive','isTradable','_discovery_source'):
            p,c=fixture();p['raw'][key]='IOL_COMPLEMENTARY' if key=='_discovery_source' else False
            self.assertFalse(guard.reconciliation_allowed(p,c,checked_at=NOW))

    def test_persisted_conflict_not_erased(self):
        p,c=fixture();p['raw']['_contract_conflicts']={'fields':['strike']}
        self.assertFalse(guard.reconciliation_allowed(p,c,checked_at=NOW))

    def test_invalid_numeric_contract_not_trusted(self):
        for value in ('NaN','-1','0','broken'):
            p,c=fixture();c['financial_contract_v17']['cash_multiplier']=value
            self.assertFalse(guard.reconciliation_allowed(p,c,checked_at=NOW))

    def test_input_not_mutated(self):
        p,c=fixture();before=copy.deepcopy((p,c))
        guard.reconciliation_allowed(p,c,checked_at=NOW)
        self.assertEqual(before,(p,c))

    def test_nonreview_missing_contract_keeps_existing_reconciliation_path(self):
        p,c=fixture();p['capability']='NEEDS_OPTION_CONTRACT'
        self.assertTrue(guard.reconciliation_allowed(p,{},checked_at=NOW))

    def test_strong_stale_alias_resolves_independent_of_order(self):
        p,o,c=alias_fixture()
        self.assertEqual(guard.select_verified_primary([p,o],c,checked_at=NOW),[p])
        self.assertEqual(guard.select_verified_primary([o,p],c,checked_at=NOW),[p])

    def test_missing_or_conflicting_strong_identity_remains_ambiguous(self):
        for field,value in [('isin',None),('isin','AROTHER00001'),('cajaValoresCode','456'),('nominalInPrice',1000)]:
            p,o,c=alias_fixture();o['raw'][field]=value
            self.assertEqual(len(guard.select_verified_primary([p,o],c,checked_at=NOW)),2)

    def test_multiple_live_aliases_not_selected(self):
        p,o,c=alias_fixture();o['status']='AVAILABLE'
        self.assertEqual(len(guard.select_verified_primary([p,o],c,checked_at=NOW)),2)

    def test_alias_different_currency_market_term_ticker_not_selected(self):
        for key in ('currency','market','settlement','ticker'):
            p,o,c=alias_fixture();o[key]='OTHER'
            self.assertEqual(len(guard.select_verified_primary([p,o],c,checked_at=NOW)),2)

    def test_more_than_two_aliases_not_selected(self):
        p,o,c=alias_fixture()
        self.assertEqual(len(guard.select_verified_primary([p,o,copy.deepcopy(o)],c,checked_at=NOW)),3)

    def test_alias_proof_does_not_modify_or_remove_history(self):
        p,o,c=alias_fixture();before=copy.deepcopy([p,o,c])
        guard.select_verified_primary([p,o],c,checked_at=NOW)
        self.assertEqual([p,o,c],before)


class Store:
    def __init__(self,path):self.path=str(path);self.events=[]
    def connect(self):
        con=sqlite3.connect(self.path,timeout=.2);con.row_factory=sqlite3.Row
        return con
    def event(self,*args):self.events.append(args)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'local.db')
        catalog.init_schema(self.store)
        with self.store.connect() as con:
            con.executescript('''
            CREATE TABLE instrument_catalog(instrument_type,ticker,description,market,settlement,downloaded_at,raw_json);
            CREATE TABLE candidate_universe(ticker,instrument_type,settlement,market,can_simulate,status,detail,last_checked_at);
            CREATE TABLE paper_events(id INTEGER PRIMARY KEY,event_time,component,event,symbol,detail);
            CREATE TABLE complementary_contract_retry(ticker,instrument_type,market,currency,settlement,source,observed_at,state,reason,last_attempt_at,attempts,
                PRIMARY KEY(ticker,instrument_type,market,currency,settlement,source));
            ''')
        tree=ast.parse((ROOT/'bf_production_paper_observer.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_reconcile_complementary_catalog')
        ns=dict(Path=Path,os=os,json=json,financial_catalog=catalog,
            complementary_discovery=lambda root:[],now_iso=lambda:NOW,COMPLEMENTARY_CONTRACT_TTL_SECONDS=86400)
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual-reconciler','exec'),ns)
        self.reconcile=ns[fn.name]
        self.env=patch.dict(os.environ,{'POROTA_MARKET_DATA_ROOT':self.tmp.name});self.env.start()

    def tearDown(self):self.env.stop();self.tmp.cleanup()

    def persist(self,row):
        with self.store.connect() as con:catalog.persist(con,row)

    def run_complements(self,values):
        with patch('rc6_contract_bridge.complements_from_store',return_value=values):
            return self.reconcile(self.store)

    def test_stale_review_recovers_in_real_reconciler(self):
        p,c=fixture();self.persist(p)
        self.assertEqual(self.run_complements([c]),1)
        with self.store.connect() as con:
            result=con.execute('SELECT status,can_simulate FROM candidate_identity_v2').fetchone()
        self.assertEqual(tuple(result),('AVAILABLE',1))

    def test_real_material_review_stays_closed_in_real_reconciler(self):
        p,c=fixture();c['financial_contract_v17']=None;c['contract_bridge'].update(status='BLOCKED',gaps=['CHANGE_REVIEW_REQUIRED'])
        self.persist(p);self.assertEqual(self.run_complements([c]),0)
        with self.store.connect() as con:
            self.assertEqual(con.execute('SELECT can_simulate FROM candidate_identity_v2').fetchone()[0],0)

    def test_real_negative_ppi_capability_cannot_be_overridden(self):
        p,c=fixture();p['capability']='TYPE_NOT_ENUMERATED';self.persist(p)
        self.assertEqual(self.run_complements([c]),0)
        with self.store.connect() as con:
            self.assertEqual(con.execute('SELECT capability FROM financial_instrument_catalog').fetchone()[0],'TYPE_NOT_ENUMERATED')

    def test_proven_legacy_alias_does_not_poison_current_identity(self):
        p,o,c=alias_fixture();self.persist(p);self.persist(o)
        self.assertEqual(self.run_complements([c]),1)
        with self.store.connect() as con:
            rows=con.execute('SELECT instrument_type,can_simulate FROM candidate_identity_v2 ORDER BY instrument_type').fetchall()
            self.assertEqual([tuple(r) for r in rows],[('OBLIGACIONES',1),('ON',0)])
            self.assertEqual(con.execute('SELECT count(*) FROM financial_instrument_catalog').fetchone()[0],2)
            old=con.execute("SELECT metadata_json FROM financial_instrument_catalog WHERE instrument_type='ON'").fetchone()[0]
            self.assertEqual(json.loads(old),o['raw'])

    def test_real_alias_conflict_remains_ambiguous(self):
        p,o,c=alias_fixture();o['raw']['isin']='AROTHER00001';self.persist(p);self.persist(o)
        self.assertEqual(self.run_complements([c]),0)
        with self.store.connect() as con:
            self.assertEqual(con.execute('SELECT sum(can_simulate) FROM candidate_identity_v2').fetchone()[0],0)

    def test_repeat_reconciliation_is_idempotent_for_readiness(self):
        p,c=fixture();self.persist(p)
        self.assertEqual(self.run_complements([c]),1)
        self.assertEqual(self.run_complements([c]),0)


if __name__=='__main__':unittest.main()
