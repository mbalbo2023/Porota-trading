"""AUD-468-06..13/17/18/20/21, restored history controls and caller replay."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from decimal import Decimal as D
import json
import multiprocessing
import os
from pathlib import Path
import socket
import sqlite3
from types import SimpleNamespace

import pytest
import requests

import al_historical_ingest as legacy
import ba_data912_history as data912
import bl_candle_engine as candles
import cr_data912_reconcile_hf6 as reconcile
import ct_ppi_history_salvage_hf6 as salvage
import cu_history_store_v2_hf6 as history
import cw_data912_history_v2_sink as sink
import historical_candle_shadow_rc6 as shadow
from db_a3_cem_normalizer_hf6 import closing_to_candle

NOW=datetime(2026,10,2,16,tzinfo=timezone.utc)
Q=SimpleNamespace(symbol='AUDIT',asset_class='ACCIONES',market='BYMA',currency='ARS',settlement='A-24HS')


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*_,**__):raise AssertionError('NETWORK_FORBIDDEN_IN_HISTORY_REGRESSION')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    monkeypatch.setattr(socket,'create_connection',forbidden)


class Store:
    def __init__(self,path):self.path=str(path)
    @contextmanager
    def connect(self):
        c=sqlite3.connect(self.path,timeout=5);c.row_factory=sqlite3.Row
        try:yield c;c.commit()
        except BaseException:c.rollback();raise
        finally:c.close()


def candle(**changes):
    defaults=dict(symbol='AUDIT',instrument_type='ACCIONES',market='BYMA',settlement='A-24HS',
        date='2026-09-30',open=100,high=102,low=98,close=100,volume=1000,
        source='PPI_API',currency='ARS',price_basis='RAW',volume_kind='QUANTITY',
        observed_at='2026-10-01T16:00:00Z')
    defaults.update(changes)
    return history.Candle(**defaults)


def table(store,name):
    with store.connect() as c:return [dict(r) for r in c.execute('SELECT * FROM '+name)]


def exact_read(store,cut=NOW,**changes):
    args=dict(symbol=Q.symbol,instrument_type=Q.asset_class,market=Q.market,
              currency=Q.currency,settlement=Q.settlement,as_of=cut)
    args.update(changes)
    with store.connect() as c:return history.read_as_of(c,**args)


def test_AUD06_adjusted_secondary_never_overwrites_raw_and_action_bases_are_separate(tmp_path):
    s=Store(tmp_path/'history.sqlite')
    history.append_candle(s,candle())
    history.append_candle(s,candle(source='YAHOO',adjusted=True,price_basis='',
        open=50,high=50,low=50,close=50,observed_at='2026-10-01T17:00:00Z'))
    history.append_candle(s,candle(close=101,observed_at='2026-10-02T15:00:00Z'))
    assert exact_read(s)[0]['close']==101
    rows=table(s,'history_canonical_v2')
    assert {r['price_basis'] for r in rows}=={'RAW','UNKNOWN_ADJUSTED'}
    for factor in ('2','3'):
        history.append_candle(s,candle(source='YAHOO',adjusted=True,price_basis='SPLIT',
            adjustment_basis='action:'+factor,metadata={'adjustment_factor':factor},
            open=50,high=50,low=50,close=50))
    assert len(table(s,'history_canonical_v2'))==4


def test_AUD07_currency_identity_is_exact_and_missing_currency_is_rejected(tmp_path):
    s=Store(tmp_path/'history.sqlite')
    history.append_candle(s,candle())
    history.append_candle(s,candle(currency='USD_MEP',open=1,high=1,low=1,close=1))
    assert exact_read(s)[0]['close']==100
    assert exact_read(s,currency='USD_MEP')[0]['close']==1
    with pytest.raises(ValueError,match='CURRENCY_REQUIRED'):history.append_candle(s,candle(currency=''))
    with pytest.raises(ValueError,match='CURRENCY_CONFLICT'):history.append_candle(s,candle(metadata={'currency':'USD'}))


def test_AUD08_retry_preserves_version_known_at_and_only_updates_check_clock(tmp_path):
    s=Store(tmp_path/'history.sqlite')
    first=history.append_candle(s,candle())
    retry=history.append_candle(s,candle(observed_at='2026-10-02T15:00:00Z'))
    assert first['version_id']==retry['version_id'] and not retry['version_appended']
    row=table(s,'history_canonical_v2')[0]
    version=table(s,'history_versions_v2')[0]
    assert row['observed_at']==row['version_known_at']==version['version_known_at']==history.utc('2026-10-01T16:00:00Z')
    assert row['last_checked_at']==history.utc('2026-10-02T15:00:00Z')
    assert row['event_at']=='2026-09-30'


def test_AUD09_offsets_order_absolute_microsecond_instants_and_replay_stays_causal(tmp_path):
    s=Store(tmp_path/'history.sqlite')
    history.append_candle(s,candle())
    history.append_candle(s,candle(close=101,observed_at='2026-10-01T14:00:00.000001-03:00'))
    assert exact_read(s,cut='2026-10-01T17:00:00.000000Z')[0]['close']==100
    assert exact_read(s,cut='2026-10-01T17:00:00.000001Z')[0]['close']==101
    assert table(s,'history_canonical_v2')[0]['version_known_at']=='2026-10-01T17:00:00.000001+00:00'


@pytest.mark.parametrize('changes,reason',[
    ({'date':'2099-01-01'},'DATE_IN_FUTURE'),({'date':'not-a-date'},'DATE_INVALID'),
    ({'date':'2026-09-27'},'SESSION_WEEKEND'),({'date':'2026-07-09'},'SESSION_NOT_OPERATIONAL'),
    ({'close':float('inf'),'open':None,'high':None,'low':None},'CLOSE_NONFINITE'),
    ({'close':float('nan')},'CLOSE_NONFINITE'),({'volume':float('inf')},'VOLUME_NONFINITE'),
    ({'volume':-1},'VOLUME_NONPOSITIVE'),({'high':97},'OHLC_INCONSISTENT'),
    ({'open':None,'close':500},'OHLC_PARTIAL'),
    ({'observed_at':'2026-10-01T16:00:00'},'CLOCK_AWARE_REQUIRED'),
    ({'observed_at':'2099-01-01T16:00:00Z'},'KNOWN_AT_IN_FUTURE'),
    ({'provider_at':'2026-10-02T16:00:00Z'},'PROVIDER_AFTER_KNOWN'),
    ({'adjusted':True},'ADJUSTMENT_CONFLICT'),
    ({'adjusted':True,'price_basis':'SPLIT'},'ADJUSTMENT_PROVENANCE_REQUIRED'),
])
def test_AUD10_universal_sink_validation_prevents_any_row_write(tmp_path,changes,reason):
    s=Store(tmp_path/'history.sqlite');history.init_schema(s)
    with pytest.raises(ValueError,match=reason):history.append_candle(s,candle(**changes))
    assert table(s,'history_versions_v2')==table(s,'history_canonical_v2')==[]


def test_AUD11_shadow_rejects_future_known_revision_and_legacy_ambiguous_identity(tmp_path):
    s=Store(tmp_path/'history.sqlite')
    days=[datetime(2026,9,1,tzinfo=timezone.utc)+timedelta(days=i) for i in range(30)]
    for day in days:
        if day.weekday()>=5:continue
        history.append_candle(s,candle(date=day.date().isoformat(),observed_at='2026-10-04T16:00:00Z'))
    assert shadow._history_features(s,Q,NOW)['observations']==0
    assert shadow._history_features(s,Q,datetime(2026,10,4,16,tzinfo=timezone.utc))['state']=='READY'
    old=Store(tmp_path/'legacy.sqlite')
    with old.connect() as c:
        c.execute('CREATE TABLE production_history(symbol,instrument_type,settlement,downloaded_at,payload_json)')
        c.execute('INSERT INTO production_history VALUES(?,?,?,?,?)',('AUDIT','ACCIONES','A-24HS',history.utc(NOW+timedelta(days=2)),'[]'))
    assert shadow._history_features(old,Q,NOW)['state']=='HISTORY_IDENTITY_UNVERIFIED'


def series(**changes):
    args=dict(symbol='AUDIT',asset_class='ACCIONES',market='BYMA',currency='ARS',settlement='A-24HS',
        resolution='5m',source='PPI_API',adjustment='RAW',adjustment_basis='EXPLICIT_TEST',
        price_kind='PROVIDER_OHLC',volume_kind='QUANTITY',cash_multiplier='1')
    args.update(changes);return candles.Series(**args)


def bar(price=100,**changes):
    args=dict(start='2026-10-02T15:00:00Z',open=D(price),high=D(price)+1,low=D(price)-1,
              close=D(price),volume=D(100),quality='COMPLETE')
    args.update(changes);return candles.Bar(**args)


def test_AUD12_fifteen_revisions_count_one_and_conflict_revokes_without_resurrection(tmp_path):
    s=Store(tmp_path/'candles.sqlite');candles.init_schema(s);archive=candles.CandleArchive(s)
    ser=series()
    for i in range(15):archive.put(ser,bar(100+i),known_at=history.utc(NOW-timedelta(minutes=45-i)))
    assert shadow._candle_features(s,Q,NOW)['observations']==1
    archive.put(ser,bar(111,quality='CONFLICT'),known_at='2026-10-02T15:59:00Z')
    assert shadow._candle_features(s,Q,NOW)['observations']==0
    assert shadow._candle_features(s,Q,NOW-timedelta(minutes=2))['observations']==1


def test_AUD12_sources_bases_currencies_are_never_concatenated_to_ready(tmp_path):
    s=Store(tmp_path/'candles.sqlite');candles.init_schema(s);archive=candles.CandleArchive(s)
    for variant in (series(),series(source='YAHOO'),series(currency='USD'),
                    series(adjustment='SPLIT',adjustment_basis='ACTION')):
        for i in range(8):archive.put(variant,bar(100+i,start=history.utc(NOW-timedelta(minutes=50-i*5))),known_at=history.utc(NOW-timedelta(minutes=45-i*5)))
    result=shadow._candle_features(s,Q,NOW)
    assert result['observations']==8 and result['state']=='CANDLE_INSUFFICIENT'
    assert result['series']['source']=='PPI_API'


def test_H_LEGACY_ASSET_CLASS_COLLISION_atomic_guard_preserves_original(tmp_path,monkeypatch):
    monkeypatch.setattr(legacy,'HIST_DB_PATH',str(tmp_path/'legacy.sqlite'));legacy.init_db()
    row=('2026-09-30',100,100,100,100,1000)
    legacy.guardar_velas('AUDIT','ACCIONES',[row],'IOL',False)
    with pytest.raises(ValueError,match='FAMILY_COLLISION'):
        legacy.guardar_velas('AUDIT','BONOS',[row],'DATA912',False)
    with legacy._conn() as c:
        assert c.execute('SELECT asset_class,close FROM market_historical_ohlcv').fetchone()==('ACCIONES',100)


def test_U_IOL_ARCHIVED_LIQUIDITY_keeps_money_and_quantity_separate(tmp_path,monkeypatch):
    monkeypatch.setattr(legacy,'HIST_DB_PATH',str(tmp_path/'legacy.sqlite'));legacy.init_db()
    row=legacy._normalizar_fila_iol(dict(fecha='2026-09-30',ultimoPrecio=100,apertura=100,maximo=100,
        minimo=100,montoOperado=100000,volumen=1000,currency='ARS',volume_kind='QUANTITY'))
    assert row[5]==1000 and row.cash_turnover==100000
    legacy.guardar_velas('AUDIT','ACCIONES',[row],'IOL',True)
    assert data912.archived_liquidity('AUDIT','ACCIONES',currency='ARS')==100000
    assert data912.archived_liquidity('AUDIT','ACCIONES') is None
    assert data912.archived_liquidity('AUDIT','ACCIONES',currency='USD') is None
    money_only=legacy._normalizar_fila_iol(dict(fecha='2026-09-30',ultimoPrecio=100,montoOperado=100000))
    assert money_only[5] is None and money_only.cash_turnover==100000


def test_AUD17_concurrent_identical_writers_have_one_version_and_atomic_idempotence(tmp_path):
    s=Store(tmp_path/'history.sqlite');history.init_schema(s)
    with ThreadPoolExecutor(max_workers=8) as workers:
        results=list(workers.map(lambda _:history.append_candle(s,candle()),range(24)))
    assert sum(int(x['version_appended']) for x in results)==1
    assert len(table(s,'history_versions_v2'))==len(table(s,'history_canonical_v2'))==1
    with s.connect() as c:
        row=c.execute('SELECT * FROM history_versions_v2').fetchone()
        fields=[r[1] for r in c.execute('PRAGMA table_info(history_versions_v2)') if r[1]!='id']
        with pytest.raises(sqlite3.IntegrityError):
            c.execute('INSERT INTO history_versions_v2('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',tuple(row[f] for f in fields))


def test_AUD17_ABA_has_three_causal_versions_and_identical_retry_does_not_add_fourth(tmp_path):
    s=Store(tmp_path/'history.sqlite')
    for close,known in ((100,'2026-10-01T16:00:00Z'),(101,'2026-10-01T17:00:00Z'),(100,'2026-10-01T18:00:00Z'),(100,'2026-10-01T19:00:00Z')):
        history.append_candle(s,candle(close=close,observed_at=known))
    assert len(table(s,'history_versions_v2'))==3
    assert exact_read(s,cut='2026-10-01T17:30:00Z')[0]['close']==101
    assert exact_read(s,cut='2026-10-01T18:30:00Z')[0]['close']==100


def test_NEW_history_revision_lookup_work_does_not_grow_with_unrelated_provider_identities(tmp_path):
    s=Store(tmp_path/'history.sqlite');history.append_candle(s,candle())
    def query_work():
        operations=0
        def counter():
            nonlocal operations
            operations+=1
            return 0
        with s.connect() as connection:
            connection.set_progress_handler(counter,1)
            row=connection.execute('SELECT * FROM history_versions_v2 WHERE '+history._WHERE+
                ' AND source=? ORDER BY version_known_at DESC,id DESC LIMIT 1',
                ('MISSING','ACCIONES','BYMA','ARS','A-24HS','2026-09-30','RAW','RAW_NO_ADJUSTMENT','PPI_API')).fetchone()
            connection.set_progress_handler(None,0)
            assert row is None
        return operations
    small=query_work()
    history.append_many(s,[candle(symbol='UNRELATED'+str(index)) for index in range(2000)])
    large=query_work()
    assert large<=small+128
    assert len(table(s,'history_versions_v2'))==2001


def test_AUD18_batch_validation_and_mid_transaction_abort_leave_no_partial_commit(tmp_path):
    s=Store(tmp_path/'history.sqlite');history.init_schema(s)
    with pytest.raises(ValueError):history.append_many(s,[candle(),candle(close=-1,date='2026-10-01')])
    assert table(s,'history_versions_v2')==[]
    with s.connect() as c:
        c.execute("CREATE TRIGGER fail_second BEFORE INSERT ON history_canonical_v2 WHEN NEW.date='2026-10-01' BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.DatabaseError):history.append_many(s,[candle(),candle(date='2026-10-01')])
    assert table(s,'history_versions_v2')==table(s,'history_canonical_v2')==table(s,'history_batch_attempts_v2')==[]


def test_history_DB_LOCK_RECOVERY_retries_after_lock_without_partial_versions(tmp_path):
    s=Store(tmp_path/'history.sqlite');history.init_schema(s)
    class ShortStore(Store):
        @contextmanager
        def connect(self):
            c=sqlite3.connect(self.path,timeout=.01);c.row_factory=sqlite3.Row
            try:yield c;c.commit()
            except BaseException:c.rollback();raise
            finally:c.close()
    with sqlite3.connect(s.path) as locker:
        locker.execute('BEGIN EXCLUSIVE')
        with pytest.raises(sqlite3.OperationalError,match='locked'):
            history.append_candle(ShortStore(s.path),candle())
        locker.rollback()
    assert history.append_candle(s,candle())['version_appended']
    assert len(table(s,'history_versions_v2'))==1


def _crash_child(path):
    history._prefer=lambda *_:os._exit(79)
    history.append_candle(Store(path),candle())


def test_S_CRASH_AFTER_VERSION_INSERT_restart_rolls_back_and_exact_retry_recovers(tmp_path):
    s=Store(tmp_path/'history.sqlite');history.init_schema(s)
    child=multiprocessing.get_context('fork').Process(target=_crash_child,args=(s.path,))
    child.start();child.join(timeout=5)
    if child.is_alive():child.terminate();child.join();pytest.fail('child deadline exceeded')
    assert child.exitcode==79
    assert table(s,'history_versions_v2')==table(s,'history_canonical_v2')==[]
    assert history.append_candle(s,candle())['version_appended']
    assert len(table(s,'history_versions_v2'))==1


def test_S_HEARTBEAT_NO_PROGRESS_retains_zero_progress_as_distinct_from_ready(tmp_path):
    s=Store(tmp_path/'candles.sqlite');candles.init_schema(s)
    with s.connect() as c:c.execute('CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY)')
    worker=candles.SampleMaterializer(s)
    first=worker.tick(NOW);second=worker.tick(NOW+timedelta(minutes=1))
    for result in (first,second):
        assert result['accepted']==result['versions']==result['cursor']==0
    assert table(s,'candle_worker_state')[0]['heartbeat_at']!=history.utc(NOW)


def ppi_row(**changes):
    row=dict(date='2026-09-30T17:00:00-03:00',openingPrice=100,max=102,min=98,price=100,volume=1000)
    row.update(changes);return row


def test_AUD18_cross_store_failure_has_durable_stage_and_resumes_without_duplicate_attempt(tmp_path,monkeypatch):
    observer=Store(tmp_path/'observer.sqlite');archive=Store(tmp_path/'history.sqlite')
    args=dict(symbol='AUDIT',instrument_type='ACCIONES',market='BYMA',currency='ARS',settlement='A-24HS',
              payload=[ppi_row(),ppi_row(date='2026-09-29T17:00:00-03:00',openingPrice=0)],
              attempted_at='2026-10-01T16:00:00Z',history_store=archive)
    original=salvage._append_attempt_once
    monkeypatch.setattr(salvage,'_append_attempt_once',lambda *_:(_ for _ in ()).throw(OSError('injected observer failure')))
    with pytest.raises(OSError):salvage.ingest_ppi_payload(observer,**args)
    assert table(archive,'history_ingest_sagas_v2')[0]['state']=='CLOSE_COMMITTED'
    assert len(table(archive,'history_versions_v2'))==len(table(archive,'history_close_versions_v1'))==1
    monkeypatch.setattr(salvage,'_append_attempt_once',original)
    result=salvage.ingest_ppi_payload(observer,**args)
    retry=salvage.ingest_ppi_payload(observer,**args)
    assert result['attempt_id']==retry['attempt_id']
    assert len(table(observer,'history_attempt_ledger_v2'))==len(table(observer,'history_row_rejections_v2'))==1
    assert table(archive,'history_ingest_sagas_v2')[0]['state']=='OBSERVER_COMMITTED'
    assert len(table(archive,'history_versions_v2'))==len(table(archive,'history_close_versions_v1'))==1


def test_AUD18_close_only_currency_isolation_ABA_and_atomic_batch(tmp_path):
    import ea_history_close_series_hf2 as closes
    s=Store(tmp_path/'history.sqlite')
    first=closes.CloseEvidence('AUDIT','ACCIONES','BYMA','A-24HS','2026-09-30',100,
         history.utc('2026-10-01T16:00:00Z'),'OPEN_MISSING',0,{'price':100},'ARS')
    for amount,known in ((100,'16'),(101,'17'),(100,'18')):
        closes.append_close_evidence(s,replace(first,close=amount,raw_row={'price':amount},
                                     observed_at=history.utc('2026-10-01T'+known+':00:00Z')))
    closes.append_close_evidence(s,replace(first,currency='USD',close=1,raw_row={'price':1}))
    assert len(table(s,'history_close_versions_v1'))==4
    assert {row['currency']:row['close'] for row in table(s,'history_close_canonical_v1')}=={'ARS':100,'USD':1}
    before=table(s,'history_close_versions_v1')
    with pytest.raises(ValueError):
        closes.append_many(s,[replace(first,date='2026-10-01'),replace(first,date='2026-10-01',close=-1)])
    assert table(s,'history_close_versions_v1')==before


def test_AUD18_committed_saga_retry_after_intervening_revision_does_not_resurrect_old_data(tmp_path):
    observer=Store(tmp_path/'observer.sqlite');archive=Store(tmp_path/'history.sqlite')
    args=dict(symbol='AUDIT',instrument_type='ACCIONES',market='BYMA',currency='ARS',settlement='A-24HS',
              payload=[ppi_row()],attempted_at='2026-10-01T16:00:00Z',history_store=archive)
    first=salvage.ingest_ppi_payload(observer,**args)
    history.append_candle(archive,candle(source='PPI_PRODUCTION_HISTORY',close=101,
                                        provider_at=ppi_row()['date'],observed_at='2026-10-01T17:00:00Z'))
    retried=salvage.ingest_ppi_payload(observer,**{**args,'attempted_at':'2026-10-01T18:00:00Z'})
    assert first['attempt_id']==retried['attempt_id'] and retried['versions_appended']==0
    assert exact_read(archive)[0]['close']==101
    saga=table(archive,'history_ingest_sagas_v2')[0]
    assert saga['first_attempted_at']==history.utc(args['attempted_at'])
    assert saga['last_attempted_at']==history.utc('2026-10-01T18:00:00Z') and saga['attempt_count']==2
    assert len(table(archive,'history_versions_v2'))==2


def test_AUD10_conflicting_daily_PPI_duplicates_are_all_quarantined_with_exact_indices(tmp_path):
    observer=Store(tmp_path/'observer.sqlite');archive=Store(tmp_path/'history.sqlite')
    result=salvage.ingest_ppi_payload(observer,symbol='AUDIT',instrument_type='ACCIONES',market='BYMA',
        currency='ARS',settlement='A-24HS',payload=[ppi_row(),ppi_row(price=101),ppi_row(price=99)],
        attempted_at='2026-10-01T16:00:00Z',history_store=archive)
    assert result['provider_rows']==result['rejected_rows']==3 and result['valid_rows']==result['close_only_rows']==0
    assert [row['row_index'] for row in table(observer,'history_row_rejections_v2')]==[0,1,2]
    assert table(archive,'history_versions_v2')==[]


def test_AUD10_nonlist_PPI_payload_is_a_typed_failure_before_creating_a_store(tmp_path):
    archive=Store(tmp_path/'history.sqlite')
    with pytest.raises(ValueError,match='HISTORY_PAYLOAD_NOT_LIST'):
        salvage.ingest_ppi_payload(Store(tmp_path/'observer.sqlite'),symbol='AUDIT',instrument_type='ACCIONES',
            market='BYMA',currency='ARS',settlement='A-24HS',payload={},history_store=archive)
    assert not Path(archive.path).exists()


def test_AUD11_shadow_reads_explicit_dedicated_path_without_calling_its_write_connection(tmp_path):
    archive=Store(tmp_path/'history.sqlite')
    for index in range(30):
        day=datetime(2026,9,1,tzinfo=timezone.utc)+timedelta(days=index)
        if day.weekday()<5:history.append_candle(archive,candle(date=day.date().isoformat()))
    class ReadTarget:
        path=archive.path
        def connect(self):raise AssertionError('SHADOW_MUST_NOT_OPEN_SOURCE_SQLITE')
    result=shadow.collect(object(),Q,NOW,history_store=ReadTarget())
    assert result['history']['state']=='READY' and result['history']['observations']>=20
    assert result['mode']=='SHADOW' and result['decision_effect']=='OBSERVE_ONLY'


def test_AUD10_legacy_writer_shares_numeric_validation_and_rolls_back_entire_batch(tmp_path,monkeypatch):
    monkeypatch.setattr(legacy,'HIST_DB_PATH',str(tmp_path/'legacy.sqlite'));legacy.init_db()
    valid=('2026-09-30',100,102,98,100,1000)
    invalid=('2026-10-01',100,102,98,float('inf'),1000)
    with pytest.raises(ValueError,match='HISTORY_CLOSE_NONFINITE'):
        legacy.guardar_velas('AUDIT','ACCIONES',[valid,invalid],'IOL',False)
    with legacy._conn() as c:assert c.execute('SELECT COUNT(*) FROM market_historical_ohlcv').fetchone()[0]==0


@pytest.mark.parametrize('changes,reason',[
    ({'date':'2099-01-01'},'DATE_IN_FUTURE'),({'date':'bad'},'DATE_INVALID'),
    ({'c':'NaN'},'C_NONFINITE'),({'h':90},'OHLC_INCONSISTENT'),
    ({'o':None},'O_MISSING'),({'v':float('inf')},'V_NONFINITE'),
])
def test_AUD20_Data912_reports_exact_rejections_before_shared_sink(changes,reason):
    row=dict(date='2026-09-30',o=100,h=102,l=98,c=100,v=1000);row.update(changes)
    accepted,rejected=data912._normalize([row],'2026-01-01',as_of=NOW,return_rejections=True)
    assert not accepted and rejected==[{'index':0,'reason':reason}]


def test_AUD20_partial_batch_is_not_reported_complete_and_four_key_target_never_fetches(tmp_path,monkeypatch):
    s=Store(tmp_path/'history.sqlite');calls=[]
    monkeypatch.setattr(data912,'_session',lambda:object())
    monkeypatch.setattr(sink.time,'sleep',lambda *_:None)
    def response(client,path):
        calls.append(path)
        return [dict(date='2026-09-30',o=100,h=102,l=98,c=100,v=1000),
                dict(date='2099-01-01',o=100,h=102,l=98,c=100,v=1000)]
    monkeypatch.setattr(data912,'_get_json',response)
    result=sink.refresh_identities([('AUDIT','ACCIONES','BYMA','ARS','A-24HS')],history_store=s)
    assert not result['ok'] and result['results'][0]['state']=='HISTORICAL_V2_PARTIAL'
    assert result['results'][0]['rejections'][0]['reason']=='DATE_IN_FUTURE'
    assert len(table(s,'history_versions_v2'))==1
    rejected=sink.refresh_identities([('AUDIT','ACCIONES','BYMA','A-24HS')],history_store=s)
    assert rejected['invalid_targets']==1 and not rejected['ok'] and len(calls)==1


def test_AUD07_Data912_cohort_currency_collision_is_rejected_before_batch_limit_or_fetch(tmp_path,monkeypatch):
    monkeypatch.setattr(data912,'_session',lambda:object())
    def forbidden(*_):raise AssertionError('AMBIGUOUS_REQUEST_MUST_NOT_FETCH')
    monkeypatch.setattr(data912,'_get_json',forbidden)
    s=Store(tmp_path/'history.sqlite')
    result=sink.refresh_identities([('AUDIT','ACCIONES','BYMA','ARS','A-24HS'),
        ('AUDIT','ACCIONES','BYMA','USD','A-24HS')],batch_limit=1,history_store=s)
    assert not result['ok'] and result['selected']==0 and result['invalid_targets']==2
    assert result['ambiguous_provider_requests']==1
    assert {row['reason'] for row in result['identity_rejections']}=={'HISTORY_PROVIDER_REQUEST_IDENTITY_AMBIGUOUS'}
    assert table(s,'history_versions_v2')==[]


@pytest.mark.parametrize('failure,reason',[('429','DATA912_RATE_LIMIT'),('timeout','DATA912_TIMEOUT')])
def test_AUD20_provider_retry_failure_has_safe_taxonomy_and_no_false_completion(monkeypatch,failure,reason):
    calls=[];delays=[]
    monkeypatch.setattr(data912.time,'sleep',delays.append)
    class Client:
        def get(self,*args,**options):
            calls.append(1)
            if failure=='timeout':raise requests.Timeout('Authorization: secret-fixture-marker')
            return SimpleNamespace(status_code=429)
    with pytest.raises(RuntimeError,match=reason) as error:data912._get_json(Client(),'/synthetic')
    assert 'secret-fixture-marker' not in str(error.value)
    assert len(calls)==data912.DATA912_RETRIES


def test_AUD21_coverage_cut_cohort_currency_sessions_and_consumer_do_not_imply_ready(tmp_path):
    s=Store(tmp_path/'history.sqlite');history.append_candle(s,candle())
    history.append_candle(s,candle(currency='USD_MEP',observed_at='2026-10-04T16:00:00Z'))
    keys=[('AUDIT','ACCIONES','BYMA','ARS','A-24HS'),('AUDIT','ACCIONES','BYMA','USD_MEP','A-24HS')]
    with s.connect() as c:
        result=history.coverage_inventory(c,keys,as_of=NOW,consumer='ANNUAL_RESEARCH',session='2026-09-30')
        later=history.coverage_inventory(c,keys,as_of=NOW+timedelta(days=2),consumer='ANNUAL_RESEARCH',session='2026-09-30')
    assert result['denominator']==2 and result['covered']==1 and result['missing']==1
    assert result['by_currency']['ARS']['session_covered']==1
    assert result['by_currency']['USD_MEP']['covered']==0
    assert later['covered']==2 and later['cohort_sha256']==result['cohort_sha256']
    assert result['readiness_implication']=='NONE' and not result['runtime_verified']


def test_AUD07_copy_migration_retains_source_and_quarantines_unknown_ambiguous_currency(tmp_path):
    source=tmp_path/'legacy.sqlite';target=tmp_path/'migrated.sqlite'
    with sqlite3.connect(source) as c:
        c.executescript('''CREATE TABLE history_versions_v2(
          id INTEGER PRIMARY KEY,symbol,instrument_type,market,settlement,date,open,high,low,close,
          volume,source,adjusted,observed_at,payload_hash,metadata_json);
          CREATE TABLE history_canonical_v2(symbol,instrument_type,market,settlement,date,close);
          CREATE TABLE unrelated_evidence(value);INSERT INTO unrelated_evidence VALUES('preserve');''')
        for i,meta in enumerate(({'currency':'ARS'},{},{'currency':'USD'}),1):
            c.execute('INSERT INTO history_versions_v2 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (i,'AUDIT'+str(i),'ACCIONES','BYMA','A-24HS','2026-09-30',100,102,98,100,1000,'PPI_API',0,
                 '2026-10-01T16:00:00Z','oldhash'+str(i),json.dumps(meta)))
    before=source.read_bytes()
    result=history.migrate_copy(source,target,currency_map={('AUDIT2','ACCIONES','BYMA','A-24HS'):['ARS','USD']})
    assert result['migrated_rows']==2 and result['quarantined_rows']==1 and source.read_bytes()==before
    with sqlite3.connect(target) as c:
        assert c.execute('SELECT COUNT(*) FROM history_versions_v2_legacy').fetchone()[0]==3
        assert c.execute('SELECT value FROM unrelated_evidence').fetchone()[0]=='preserve'
        assert c.execute('SELECT reason FROM history_migration_quarantine_v2').fetchone()[0]=='HISTORY_CURRENCY_AMBIGUOUS'
    with pytest.raises(ValueError,match='COPY_MIGRATION_REQUIRED'):history.init_schema(Store(source))


def test_AUD07_copy_migration_includes_close_only_legacy_without_guessing_currency(tmp_path):
    source=tmp_path/'legacy.sqlite';target=tmp_path/'migrated.sqlite'
    with sqlite3.connect(source) as c:
        c.executescript('''CREATE TABLE history_versions_v2(
          id INTEGER PRIMARY KEY,symbol,instrument_type,market,settlement,date,open,high,low,close,
          volume,source,adjusted,observed_at,payload_hash,metadata_json);
          CREATE TABLE history_canonical_v2(symbol,instrument_type,market,settlement,date,close);
          CREATE TABLE history_close_versions_v1(id INTEGER PRIMARY KEY,symbol,instrument_type,
            market,settlement,date,close,source,quality,observed_at,raw_row_hash,metadata_json);
          CREATE TABLE history_close_canonical_v1(symbol,instrument_type,market,settlement,date,close);
          INSERT INTO history_close_versions_v1 VALUES(1,'AUDIT','ACCIONES','BYMA','A-24HS',
            '2026-09-30',100,'PPI_PRODUCTION_HISTORY_CLOSE_ONLY','CLOSE_ONLY_PROVIDER_PARTIAL',
            '2026-10-01T16:00:00Z','hash','{}');''')
    before=source.read_bytes()
    result=history.migrate_copy(source,target,currency_map={('AUDIT','ACCIONES','BYMA','A-24HS'):'ARS'})
    assert result['close_only']=={'migrated_rows':1,'quarantined_rows':0}
    assert source.read_bytes()==before
    with sqlite3.connect(target) as c:
        assert c.execute('SELECT currency,close FROM history_close_canonical_v1').fetchone()==('ARS',100)
        assert c.execute('SELECT COUNT(*) FROM history_close_versions_v1_legacy').fetchone()[0]==1


def test_AUD07_explicit_legacy_currency_survives_a_multi_currency_catalog_cohort():
    assert history.resolve_legacy_currency('ARS',['ARS','USD_MEP'])=='ARS'
    with pytest.raises(ValueError,match='CURRENCY_AMBIGUOUS'):
        history.resolve_legacy_currency(None,['ARS','USD_MEP'])
    with pytest.raises(ValueError,match='CURRENCY_MAPPING_CONFLICT'):
        history.resolve_legacy_currency('USD',['ARS','USD_MEP'])


def test_AUD07_A3_quote_currency_is_not_underlying_currency_and_old_targets_fail_closed(tmp_path):
    row=dict(symbol='DLR/OCT26',dateTime='2026-09-30T21:00:00Z',currency='USD',
             open=1500,high=1501,low=1499,close=1500,volume=1)
    with pytest.raises(ValueError,match='QUOTE_CURRENCY'):closing_to_candle(row,instrument_type='FUTUROS',market='A3',settlement_identity='INMEDIATA')
    result=closing_to_candle(row,instrument_type='FUTUROS',market='A3',currency='ARS',settlement_identity='INMEDIATA')
    assert result.currency=='ARS' and result.metadata['cem_reference_currency']=='USD'
    with sqlite3.connect(tmp_path/'old.sqlite') as c:
        c.execute('CREATE TABLE candidate_universe(ticker,instrument_type,market,settlement,status)')
        c.execute("INSERT INTO candidate_universe VALUES('AUDIT','ACCIONES','BYMA','A-24HS','AVAILABLE')")
        assert reconcile.load_targets(c)==[]
