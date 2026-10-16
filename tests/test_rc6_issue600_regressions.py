"""Causal offline guards for Issue #600; no provider or production access."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import sqlite3
from types import SimpleNamespace

import pytest

from bl_candle_engine import Bar, CandleArchive, Series, init_schema
from cv_history_store_adapter_hf6 import HistoricalStore
from rc6_local_eod_history import consolidate, RESERVE
from rc6_shadow_runtime.persistence import EvidenceFiles, read_committed_generation
from rc6_dynamic_universe.common import digest
from rc6_trader_dashboard.generation import read_shadow
from scripts import rc6_history_cutoff_repair_once as repair
import historical_candle_shadow_rc6 as features

NOW = datetime.fromisoformat('2026-10-16T20:20:00+00:00')


@pytest.fixture
def source(tmp_path):
    store=HistoricalStore(str(tmp_path/'hot.db'))
    init_schema(store)
    with store.connect() as c:
        c.executescript('''CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER,session_state TEXT,heartbeat_at TEXT);
        CREATE TABLE paper_positions(status TEXT);''')
        c.execute('INSERT INTO observer_state VALUES(1,?,?,?,?)',('PRODUCTION_PAPER',0,'MARKET_CLOSED',NOW.isoformat()))
    return store


def sample(store,currency='ARS',settlement='CI',price='100',known=None):
    s=Series('GGAL','ACCIONES','BYMA',currency,settlement,'1m','PRODUCTION_PAPER_SNAPSHOTS',
             'RAW','OBSERVED_NOMINAL_PRICE','TRADE_SAMPLES','UNKNOWN')
    b=Bar('2026-10-16T14:00:00+00:00',*[Decimal(price)]*4,samples=1,quality='SAMPLED')
    CandleArchive(store).put(s,b,known_at=known or '2026-10-16T14:01:00+00:00')


def test_local_eod_independent_idempotent_and_monetary(source,tmp_path):
    sample(source); sample(source,'USD'); sample(source,settlement='24HS')
    cold=tmp_path/'history.db'
    before=source.path
    result=consolidate(before,cold,as_of=NOW,free_bytes=RESERVE)
    assert result['state']=='LOCAL_EOD_READY' and result['imported']==3
    assert result['provider_requests']==0 and not result['history_v2_writes']
    assert consolidate(before,cold,as_of=NOW,free_bytes=RESERVE)['imported']==0
    with sqlite3.connect(cold) as c:
        rows=c.execute('SELECT identity_json,body_json FROM history_local_daily_v1').fetchall()
        assert len(rows)==3
        for _,raw in rows:
            body=json.loads(raw)
            assert body['quality']=='SAMPLED' and body['coverage_state']=='PARTIAL'
            assert body['captured_minutes']==1 and body['missing_minutes']==389
            assert body['volume'] is body['trades'] is body['vwap'] is None
        assert c.execute("SELECT count(*) FROM sqlite_master WHERE name='history_canonical_v2'").fetchone()[0]==0
    with source.connect() as c:
        assert c.execute("SELECT count(*) FROM sqlite_master WHERE name='local_eod_cursor_v1'").fetchone()[0]==0


def test_crash_does_not_publish_cursor_or_daily(source,tmp_path):
    sample(source)
    def crash(): raise RuntimeError('fixture crash')
    cold=tmp_path/'history.db'
    with pytest.raises(RuntimeError):
        consolidate(source.path,cold,as_of=NOW,free_bytes=RESERVE,before_commit=crash)
    with sqlite3.connect(cold) as c:
        assert c.execute('SELECT count(*) FROM local_eod_cursor_v1').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM history_local_daily_v1').fetchone()[0]==0
    assert consolidate(source.path,cold,as_of=NOW,free_bytes=RESERVE)['imported']==1


def test_local_hot_snapshot_ends_before_cold_publication(source,tmp_path,monkeypatch):
    import rc6_local_eod_history as local
    sample(source); sample(source,'USD')
    connections=[]; original_connect=sqlite3.connect; original_put=CandleArchive.put
    def connect(database,*args,**kwargs):
        connection=original_connect(database,*args,**kwargs)
        if 'mode=ro' in str(database): connections.append(connection)
        return connection
    def put(*args,**kwargs):
        assert len(connections)==1 and not connections[0].in_transaction
        return original_put(*args,**kwargs)
    monkeypatch.setattr(local.sqlite3,'connect',connect)
    monkeypatch.setattr(CandleArchive,'put',put)
    assert consolidate(source.path,tmp_path/'history.db',as_of=NOW,free_bytes=RESERVE)['imported']==2


def test_cold_preparation_neither_holds_hot_snapshot_nor_spends_its_sql_budget(source,tmp_path,monkeypatch):
    import rc6_local_eod_history as local
    sample(source)
    connections=[];shift=[0];original_connect=sqlite3.connect
    original_init=local.init_schema;original_clock=local.time.monotonic
    def connect(database,*args,**kwargs):
        connection=original_connect(database,*args,**kwargs)
        if 'mode=ro' in str(database): connections.append(connection)
        return connection
    def prepare(*args,**kwargs):
        assert len(connections)==1 and not connections[0].in_transaction
        original_init(*args,**kwargs)
        shift[0]=3  # Cold work exceeds HOT2s, but remains within its own15s.
    monkeypatch.setattr(local.sqlite3,'connect',connect)
    monkeypatch.setattr(local,'init_schema',prepare)
    monkeypatch.setattr(local.time,'monotonic',lambda:original_clock()+shift[0])
    result=consolidate(source.path,tmp_path/'history.db',as_of=NOW,free_bytes=RESERVE)
    assert result['state']=='LOCAL_EOD_READY' and result['imported']==1


@pytest.mark.parametrize('change',['open','position','unsafe'])
def test_runtime_admission_is_rechecked_after_cold_preparation(source,tmp_path,monkeypatch,change):
    import rc6_local_eod_history as local
    sample(source);original=local.init_schema
    def changed(*args,**kwargs):
        original(*args,**kwargs)
        with source.connect() as c:
            if change=='open': c.execute("UPDATE observer_state SET session_state='MARKET_OPEN'")
            elif change=='position': c.execute("INSERT INTO paper_positions VALUES('OPEN')")
            else: c.execute('UPDATE observer_state SET real_orders_sent=1')
    monkeypatch.setattr(local,'init_schema',changed)
    cold=tmp_path/'history.db'
    if change=='unsafe':
        with pytest.raises(ValueError,match='PAPER_SAFETY_REQUIRED'):
            consolidate(source.path,cold,as_of=NOW,free_bytes=RESERVE)
    else:
        result=consolidate(source.path,cold,as_of=NOW,free_bytes=RESERVE)
        assert result['state']=='BLOCKED'
    with sqlite3.connect(cold) as c:
        assert c.execute('SELECT COUNT(*) FROM local_eod_cursor_v1').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM history_local_daily_v1').fetchone()[0]==0


def test_cold_preparation_has_its_own_finite_budget(source,tmp_path,monkeypatch):
    import rc6_local_eod_history as local
    sample(source);original_init=local.init_schema;original_clock=local.time.monotonic
    def expired(*args,**kwargs):
        original_init(*args,**kwargs)
        monkeypatch.setattr(local.time,'monotonic',lambda:original_clock()+16)
    monkeypatch.setattr(local,'init_schema',expired)
    cold=tmp_path/'history.db'
    with pytest.raises(ValueError,match='COLD_PREPARE_BUDGET'):
        consolidate(source.path,cold,as_of=NOW,free_bytes=RESERVE)
    with sqlite3.connect(cold) as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='local_eod_cursor_v1'").fetchone()


def test_local_oversize_source_payload_is_rejected_before_publication(source,tmp_path):
    sample(source)
    with source.connect() as c:
        c.execute('UPDATE candle_versions SET body_json=?',('x'*65537,))
    cold=tmp_path/'history.db'
    with pytest.raises(sqlite3.DataError):
        consolidate(source.path,cold,as_of=NOW,free_bytes=RESERVE)
    with sqlite3.connect(cold) as c:
        assert c.execute('SELECT COUNT(*) FROM local_eod_cursor_v1').fetchone()[0]==0


def test_local_cold_deadline_rolls_back_complete_batch(source,tmp_path,monkeypatch):
    import rc6_local_eod_history as local
    sample(source); cold=tmp_path/'history.db'
    original=local.time.monotonic
    with monkeypatch.context() as patch:
        def expired(): patch.setattr(local.time,'monotonic',lambda:original()+20)
        with pytest.raises(ValueError,match='COLD_WRITE_BUDGET'):
            consolidate(source.path,cold,as_of=NOW,free_bytes=RESERVE,before_commit=expired)
    with sqlite3.connect(cold) as c:
        assert c.execute('SELECT COUNT(*) FROM local_eod_cursor_v1').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM history_local_daily_v1').fetchone()[0]==0


def test_late_revision_appends_at_real_publication_time(source,tmp_path):
    cold=tmp_path/'history.db'; sample(source)
    consolidate(source.path,cold,as_of=NOW,free_bytes=RESERVE)
    sample(source,price='101',known='2026-10-16T20:21:00+00:00')
    later=NOW+timedelta(minutes=2)
    consolidate(source.path,cold,as_of=later,free_bytes=RESERVE)
    with sqlite3.connect(cold) as c:
        rows=c.execute('SELECT known_at,body_json FROM history_local_daily_v1 ORDER BY known_at').fetchall()
    assert len(rows)==2 and datetime.fromisoformat(rows[1][0])==later
    assert json.loads(rows[0][1])['close']=='100' and json.loads(rows[1][1])['close']=='101'


def test_local_disk_and_market_admission_do_not_write(source,tmp_path):
    cold=tmp_path/'history.db'
    assert consolidate(source.path,cold,as_of=NOW,free_bytes=RESERVE-1)['state']=='DEFERRED_DISK'
    assert not cold.exists()
    assert consolidate(source.path,cold,as_of=NOW-timedelta(hours=1))['state']=='NOT_DUE'
    assert not cold.exists()
    with source.connect() as c: c.execute("UPDATE observer_state SET session_state='MARKET_OPEN'")
    assert consolidate(source.path,cold,as_of=NOW,free_bytes=RESERVE)['state']=='BLOCKED'
    assert not cold.exists()


def test_local_no_samples_is_not_a_zero_candle_and_corruption_is_atomic(source,tmp_path):
    cold=tmp_path/'history.db'
    assert consolidate(source.path,cold,as_of=NOW,free_bytes=RESERVE)['imported']==0
    with sqlite3.connect(cold) as c:
        assert c.execute('SELECT count(*) FROM history_local_daily_v1').fetchone()[0]==0
    sample(source)
    with source.connect() as c:
        c.execute("UPDATE candle_versions SET body_hash='corrupt'")
    with pytest.raises(ValueError,match='DIGEST_MISMATCH'):
        consolidate(source.path,cold,as_of=NOW,free_bytes=RESERVE)
    with sqlite3.connect(cold) as c:
        assert c.execute('SELECT cursor FROM local_eod_cursor_v1').fetchone()[0]==0


def test_local_rejects_future_revision_and_excess_cold_file(source,tmp_path):
    sample(source,known=NOW+timedelta(minutes=1))
    with pytest.raises(ValueError,match='FUTURE_VERSION'):
        consolidate(source.path,tmp_path/'history.db',as_of=NOW,free_bytes=RESERVE)
    huge=tmp_path/'large-history.db'
    with huge.open('wb') as f: f.truncate(2*1024**3+1)
    assert consolidate(source.path,huge,as_of=NOW,free_bytes=RESERVE)['reason']=='COLD_ARCHIVE_PHYSICAL_BUDGET'


def test_repair_never_unions_currency_or_price_basis(tmp_path):
    store=HistoricalStore(str(tmp_path/'history.db'))
    with store.connect() as c:
        c.execute('CREATE TABLE history_canonical_v2(symbol TEXT,instrument_type TEXT,market TEXT,settlement TEXT,currency TEXT,price_basis TEXT,adjustment_basis TEXT,date TEXT)')
        rows=[('ARS','RAW','RAW_NO_ADJUSTMENT','2026-09-17'),('ARS','RAW','RAW_NO_ADJUSTMENT','2026-09-21'),
              ('USD','RAW','RAW_NO_ADJUSTMENT','2026-09-18'),('ARS','SPLIT','SPLIT_SOURCE','2026-09-18')]
        c.executemany('INSERT INTO history_canonical_v2 VALUES(?,?,?,?,?,?,?,?)',
            [('GGAL','ACCIONES','BYMA','CI',*r) for r in rows])
    key=('GGAL','ACCIONES','BYMA','CI','ARS','RAW','RAW_NO_ADJUSTMENT')
    result=repair._coverage(store,key)
    assert not result['complete'] and result['missing']==1
    with pytest.raises(ValueError,match='FULL_SERIES'):
        repair._latest(store,key[:4])
    assert repair._live_identity_unambiguous([key],key)
    usd=(*key[:4],'USD',*key[5:])
    assert not repair._live_identity_unambiguous([key,usd],key)


def test_history_stale_gap_holiday_future_and_unknown_calendar():
    import ak_byma_calendar as cal
    q=SimpleNamespace(market='BYMA',asset_class='CEDEARS')
    at=datetime.fromisoformat('2026-10-16T15:00:00+00:00')
    day=at.date()-timedelta(days=1); days=[]
    while len(days)<20:
        if cal.es_dia_habil_operativo(day): days.append(day.isoformat())
        day-=timedelta(days=1)
    assert features.history_session_quality(days,q,at)['state']=='READY'
    assert '2026-10-12' not in days
    assert features.history_session_quality(days[1:],q,at)['state']=='HISTORY_STALE'
    assert features.history_session_quality(days[:5]+days[6:],q,at)['state']=='HISTORY_GAPS'
    assert features.history_session_quality(days+['2026-10-16'],q,at)['state']=='HISTORY_FUTURE_SESSION'
    assert features.history_session_quality(days,q,at.replace(year=2027))['state']=='HISTORY_CALENDAR_UNVERIFIED'


def committed(root):
    report={'as_of':NOW.isoformat(),'real_orders_sent':0,'real_routes':'NOT_CALLED',
            'operational_funnel':{'as_of':NOW.isoformat(),'by_currency_channel':[{'currency':'ARS','channel':'NATIVE_FACTUAL','stages':{'PAPER_OPENED':2}}]}}
    status={'as_of':NOW.isoformat(),'real_orders_sent':0,'real_routes':'NOT_CALLED','status':'SHADOW_OBSERVING',
            'configuration_fingerprint':'fixture','report_digest':digest(report)}
    with EvidenceFiles(root) as files:
        files.write('status.json',status)
        files.publish_generation(report,status)
    return report,status


def test_real_generation_writer_reader_and_failure_revocation(tmp_path):
    report,status=committed(tmp_path/'shadow')
    cut=read_committed_generation(tmp_path/'shadow')
    assert cut['report']['operational_funnel']==report['operational_funnel']
    assert read_shadow(tmp_path/'shadow')['state']=='COMMITTED_COHERENT_SHADOW'
    with EvidenceFiles(tmp_path/'shadow') as files:
        files.write('status.json',status|{'status':'FAIL_CLOSED'})
    assert read_shadow(tmp_path/'shadow')['state']=='NO_VERIFICADO'


def test_generation_tamper_and_missing_pointer_never_invent_data(tmp_path):
    root=tmp_path/'shadow'
    assert read_shadow(root)['report']=={} and not root.exists()
    committed(root)
    pointer=json.loads((root/'CURRENT.json').read_text())
    pointer['payload']['file']='../../other'
    pointer['digest']=digest(pointer['payload'])
    (root/'CURRENT.json').write_text(json.dumps(pointer))
    assert read_shadow(root)['state']=='NO_VERIFICADO'


def test_sql_deadline_is_not_spent_allocating_thousands_of_catalog_rows(tmp_path,monkeypatch):
    import time
    from rc6_dynamic_universe import runtime
    path=tmp_path/'runtime.db'
    with sqlite3.connect(path) as c:
        c.executescript('''CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER);
        INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0);
        CREATE TABLE financial_instrument_catalog(ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,status TEXT,capability TEXT);
        CREATE TABLE paper_positions(paper_id INTEGER PRIMARY KEY,symbol TEXT,asset_class TEXT,market TEXT,currency TEXT,settlement TEXT,status TEXT);''')
        c.executemany('INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?)',
            [(str(i),'ACCIONES','BYMA','ARS','CI','AVAILABLE','READY_PAPER_SPOT') for i in range(3000)])
    original=sqlite3.connect
    allocations=[]
    def slow_row(cursor,row):
        time.sleep(.001)
        allocations.append(1)
        return sqlite3.Row(cursor,row)
    class Connection(sqlite3.Connection):
        def __setattr__(self,name,value):
            super().__setattr__(name,slow_row if name=='row_factory' and value is sqlite3.Row else value)
    monkeypatch.setattr(runtime.sqlite3,'connect',lambda *a,**k: original(*a,**k,factory=Connection))
    result=runtime.read_runtime(path,as_of=NOW,query_budget_seconds=.2)
    assert len(result['full_catalog'])==3000
    assert len(allocations)<20
    assert result['safety']['real_orders_sent']==0
