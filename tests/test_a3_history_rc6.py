from datetime import datetime, timedelta
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
        CREATE TABLE candidate_universe(ticker TEXT,instrument_type TEXT,market TEXT,settlement TEXT,status TEXT,currency TEXT);
        INSERT INTO candidate_universe VALUES('DLR/OCT26','FUTUROS','ROFEX','T+1','AVAILABLE','ARS');
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
        st=c.execute("SELECT status,last_complete_day FROM a3_history_ingest_state_rc6_v2").fetchone()
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
        state=c.execute("SELECT status,last_error FROM a3_history_ingest_state_rc6_v2").fetchone()
        assert tuple(state)==('ALIGNMENT_UNVERIFIED','CEM_SYMBOL_FAMILY_NOT_EXACT')


def test_incremental_uses_day_after_last_complete(tmp_path):
    s=make_store(tmp_path);client=Client()
    a3.run(s,client=client,history_store=s,now=NOW,mode='BOOTSTRAP')
    client.calls.clear()
    a3.run(s,client=client,history_store=s,now=NOW,mode='DAILY_INCREMENTAL')
    assert client.calls[0]['date_from']=='2026-09-05'
    assert client.calls[0]['date_to']=='2026-09-05'


def test_a3_bootstrap_and_stale_cursor_never_request_more_than_365_dates(tmp_path):
    s=make_store(tmp_path)
    oldest=(NOW.date()-timedelta(days=364)).isoformat()
    client=Client(rows=False)
    a3.run(s,client=client,history_store=s,now=NOW,mode='BOOTSTRAP')
    assert client.calls[0]['date_from']==oldest
    a3.init_state(s)
    with s.connect() as c:
        c.execute('''UPDATE a3_history_ingest_state_rc6_v2 SET last_complete_day=?''',
                  ((NOW.date()-timedelta(days=1000)).isoformat(),))
    client.calls.clear()
    a3.run(s,client=client,history_store=s,now=NOW,mode='DAILY_INCREMENTAL')
    assert client.calls[0]['date_from']==oldest


def test_a3_provider_rows_outside_requested_dates_are_not_persisted(tmp_path):
    s=make_store(tmp_path)
    class OutOfRange(Client):
        def closing_prices(self,**kwargs):
            self.calls.append(kwargs)
            old=(NOW.date()-timedelta(days=365)).isoformat()+'T21:00:00Z'
            return {'data':[{'symbol':'DLR/OCT26','dateTime':old,
                'open':100,'high':110,'low':95,'close':105,'volume':10,'settlement':104}]}
    client=OutOfRange()
    result=a3.run(s,client=client,history_store=s,now=NOW,mode='BOOTSTRAP')
    assert result['versions_appended']==0
    with s.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM history_versions_v2').fetchone()[0]==0


def test_completed_a3_cursor_avoids_catalog_request_but_reconcile_checks_corrections(tmp_path):
    s=make_store(tmp_path)
    a3.init_state(s)
    with s.connect() as c:
        c.execute('''INSERT INTO a3_history_ingest_state_rc6_v2
          (source,symbol,instrument_type,market,currency,settlement,last_complete_day,status)
          VALUES('A3_CEM_CLOSING','DLR/OCT26','FUTUROS','ROFEX','ARS','T+1',?,'COMPLETE')''',
          (NOW.date().isoformat(),))
    class Counted(Client):
        def __init__(self):
            super().__init__();self.catalog_calls=0
        def symbols(self):
            self.catalog_calls+=1
            return super().symbols()
    client=Counted()
    for mode in ('DAILY_INCREMENTAL','WEEKEND_DEEP'):
        result=a3.run(s,client=client,history_store=s,now=NOW,mode=mode)
        assert result['reason']=='NO_NEW_A3_SESSION_GAP'
        assert result['provider_requests']==0
    assert client.catalog_calls==0 and client.calls==[]
    a3.run(s,client=client,history_store=s,now=NOW,mode='RECONCILE')
    assert client.catalog_calls==1 and len(client.calls)==1


def test_no_exact_symbol_family_match_writes_no_history(tmp_path):
    s=make_store(tmp_path)
    class Mismatch(Client):
        def symbols(self):return {'data':[{'symbol':'OTHER','cfiCode':'FXXXXX'}]}
    r=a3.run(s,client=Mismatch(),history_store=s,now=NOW,mode='BOOTSTRAP')
    assert r['unmatched']==1 and r['state']=='BLOCKED_NO_ALIGNED_TARGETS'
    assert r['provider_requests']==1
    with s.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM history_versions_v2").fetchone()[0]==0


def test_a3_job_cannot_report_green_when_all_local_targets_are_unaligned(monkeypatch,capsys):
    import ex_a3_history_job_rc6 as job
    monkeypatch.setattr(job.runner,'run',lambda *args,**kwargs: {
        'ran':True,'state':'BLOCKED_NO_ALIGNED_TARGETS','failed':0,
        'selected':40,'unmatched':40,'provider_requests':1})
    assert job.main(['DAILY_INCREMENTAL'])==2
    assert json.loads(capsys.readouterr().out)['state']=='BLOCKED_NO_ALIGNED_TARGETS'


def test_no_exact_targets_is_explicit_and_never_calls_a3(tmp_path):
    s=make_store(tmp_path)
    with s.connect() as c:c.execute("UPDATE candidate_universe SET status='UNVERIFIED'")
    client=Client()
    result=a3.run(s,client=client,history_store=s,now=NOW,mode='RECONCILE')
    assert result=={'ran':False,'reason':'NO_EXACT_A3_TARGETS',
                    'execution_allowed':False,'mode':'RECONCILE','selected':0,'provider_requests':0}
    assert client.calls==[]


def test_byma_options_do_not_issue_a3_catalog_request(tmp_path):
    s=make_store(tmp_path)
    with s.connect() as c:
        c.execute("UPDATE candidate_universe SET ticker='YPF1234C',instrument_type='OPCIONES',market='BYMA'")
    class NoCatalog(Client):
        def symbols(self):raise AssertionError('BYMA option cannot be inferred as an A3 contract')
    result=a3.run(s,client=NoCatalog(),history_store=s,now=NOW,mode='RECONCILE')
    assert result['reason']=='NO_EXACT_A3_TARGETS'
    assert result['provider_requests']==0


def test_legacy_sink_rejected_before_a3_catalog_request(tmp_path):
    s=make_store(tmp_path)
    history=Store(tmp_path/'legacy.sqlite')
    with history.connect() as c:c.execute('CREATE TABLE history_versions_v2(id INTEGER)')
    class NoCatalog(Client):
        def symbols(self):raise AssertionError('A3 request before sink validation')
    import pytest
    with pytest.raises(ValueError,match='HISTORY_COPY_MIGRATION_REQUIRED'):
        a3.run(s,client=NoCatalog(),history_store=history,now=NOW,mode='RECONCILE')


def test_open_market_or_real_orders_fails_closed(tmp_path):
    s=make_store(tmp_path)
    with s.connect() as c:c.execute("UPDATE observer_state SET session_state='MARKET_OPEN'")
    r=a3.run(s,client=Client(),history_store=s,now=NOW)
    assert r['ran'] is False and r['reason']=='RUNTIME_NOT_CLOSED_OR_SAFE'


def test_client_contract_has_no_order_methods():
    from cz_a3_cem_public_history_hf6 import A3CEMPublicReadOnlyClient
    forbidden={'send_order','new_order','replace_order','cancel_order'}
    assert not (forbidden & set(dir(A3CEMPublicReadOnlyClient)))
