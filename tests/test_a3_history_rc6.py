from datetime import datetime
from pathlib import Path
import json
import sqlite3
from zoneinfo import ZoneInfo

import ew_a3_history_rc6 as a3

TZ=ZoneInfo('America/Argentina/Buenos_Aires')
NOW=datetime(2026,9,5,20,0,tzinfo=TZ)


class Store:
    def __init__(self,path): self.path=str(path)
    def connect(self):
        c=sqlite3.connect(self.path);c.row_factory=sqlite3.Row;return c


class Client:
    def __init__(self,rows=True): self.rows=rows;self.calls=[]
    def symbols(self):
        return {'data':[{'symbol':'DLR/OCT26','cfiCode':'FXXXXX','maturityDate':'2026-10-31','currency':'USD'}]}
    def closing_prices(self,**kwargs):
        self.calls.append(kwargs)
        if not self.rows:return {'data':[]}
        return {'data':[{'symbol':'DLR/OCT26','dateTime':'2026-09-04T21:00:00Z',
                         'open':100,'high':110,'low':95,'close':105,'volume':10,'settlement':104}]}


class CompactDLRClient(Client):
    def symbols(self):
        return {'data':[{'symbol':'DLR102026','cfiCode':'FXXXXX','maturityDate':'2026-10-31','currency':'USD'}]}
    def closing_prices(self,**kwargs):
        self.calls.append(kwargs)
        if not self.rows:return {'data':[]}
        return {'data':[{'symbol':'DLR102026','dateTime':'2026-09-04T21:00:00Z',
                         'open':100,'high':110,'low':95,'close':105,'volume':10,'settlement':104}]}


def make_store(tmp_path):
    s=Store(tmp_path/'db.sqlite')
    with s.connect() as c:
        c.executescript('''
        CREATE TABLE observer_state(id INTEGER PRIMARY KEY,session_state TEXT,real_orders_sent INTEGER);
        INSERT INTO observer_state VALUES(1,'MARKET_CLOSED',0);
        CREATE TABLE candidate_universe(ticker TEXT,instrument_type TEXT,market TEXT,settlement TEXT,status TEXT);
        INSERT INTO candidate_universe VALUES('DLR/OCT26','FUTUROS','ROFEX','T+1','AVAILABLE');
        ''')
    return s


def test_bootstrap_is_background_readonly_a3_and_writes_provenanced_history(tmp_path):
    s=make_store(tmp_path);client=Client()
    r=a3.run(s,client=client,history_store=s,now=NOW,mode='BOOTSTRAP',throttle_seconds=0)
    assert r['ran'] is True and r['execution_allowed'] is False and r['hot_path'] is False
    assert r['complete']==1 and r['unmatched']==0
    assert len(client.calls)==1
    with s.connect() as c:
        row=c.execute("SELECT symbol,instrument_type,market,settlement,source,date FROM history_canonical_v2").fetchone()
        assert tuple(row)==('DLR/OCT26','FUTUROS','ROFEX','T+1','A3_CEM_CLOSING','2026-09-04')
        st=c.execute("SELECT status,last_complete_day FROM a3_history_ingest_state_rc6").fetchone()
        assert tuple(st)==('COMPLETE','2026-09-04')


def test_compact_a3_dlr_maps_deterministically_but_persists_porota_identity(tmp_path):
    s=make_store(tmp_path);client=CompactDLRClient()
    r=a3.run(s,client=client,history_store=s,now=NOW,mode='BOOTSTRAP',throttle_seconds=0)
    assert r['complete']==1 and r['unmatched']==0 and r['deterministic_mapped']==1
    assert client.calls[0]['symbol']=='DLR102026'
    with s.connect() as c:
        row=c.execute("SELECT symbol,metadata_json FROM history_versions_v2").fetchone()
        assert row['symbol']=='DLR/OCT26'
        metadata=json.loads(row['metadata_json'])
        assert metadata['a3_source_symbol']=='DLR102026'
        assert metadata['porota_canonical_symbol']=='DLR/OCT26'
        assert metadata['identity_alignment']=='EXACT_DETERMINISTIC'


def test_dlr_spread_or_variant_stays_fail_closed(tmp_path):
    s=make_store(tmp_path)
    with s.connect() as c:
        c.execute("UPDATE candidate_universe SET ticker='DLR/OCT26-NOV26'")
    client=CompactDLRClient()
    r=a3.run(s,client=client,history_store=s,now=NOW,mode='BOOTSTRAP',throttle_seconds=0)
    assert r['unmatched']==1 and r['complete']==0
    assert client.calls==[]
    with s.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM history_versions_v2").fetchone()[0]==0
        state=c.execute("SELECT status,last_error FROM a3_history_ingest_state_rc6").fetchone()
        assert tuple(state)==('ALIGNMENT_UNVERIFIED','CEM_SYMBOL_FAMILY_NOT_EXACT')


def test_incremental_uses_day_after_last_complete(tmp_path):
    s=make_store(tmp_path);client=Client()
    a3.run(s,client=client,history_store=s,now=NOW,mode='BOOTSTRAP')
    client.calls.clear()
    a3.run(s,client=client,history_store=s,now=NOW,mode='DAILY_INCREMENTAL')
    assert client.calls[0]['date_from']=='2026-09-05'
    assert client.calls[0]['date_to']=='2026-09-05'


def test_no_exact_symbol_family_match_writes_no_history(tmp_path):
    s=make_store(tmp_path)
    class Mismatch(Client):
        def symbols(self):return {'data':[{'symbol':'OTHER','cfiCode':'FXXXXX'}]}
    r=a3.run(s,client=Mismatch(),history_store=s,now=NOW,mode='BOOTSTRAP')
    assert r['unmatched']==1
    with s.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM history_versions_v2").fetchone()[0]==0


def test_open_market_or_real_orders_fails_closed(tmp_path):
    s=make_store(tmp_path)
    with s.connect() as c:c.execute("UPDATE observer_state SET session_state='MARKET_OPEN'")
    r=a3.run(s,client=Client(),history_store=s,now=NOW)
    assert r['ran'] is False and r['reason']=='RUNTIME_NOT_CLOSED_OR_SAFE'


def test_client_contract_has_no_order_methods():
    from cz_a3_cem_public_history_hf6 import A3CEMPublicReadOnlyClient
    forbidden={'send_order','new_order','replace_order','cancel_order'}
    assert not (forbidden & set(dir(A3CEMPublicReadOnlyClient)))
