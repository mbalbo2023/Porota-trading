"""Independent Issue 468 harness. All mutations are ephemeral synthetic SQLite.
No repository tests imported; no network, runtime, broker or production DB access.
Usage: python audit/adversarial_harness.py SOURCE OUTPUT
"""
import os, sys, json, sqlite3, tempfile, dataclasses, math, socket, threading, time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace, ModuleType

os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
def denied(*a,**k): raise RuntimeError('AUDIT_NETWORK_DISABLED')
socket.create_connection=denied
socket.socket.connect=denied
import cu_history_store_v2_hf6 as hs
import cp_history_ingest_policy_hf6 as hp
import historical_candle_shadow_rc6 as sh
import bl_candle_engine as ce
import cf_intraday_scalping as sc
from be_paper_engine import PaperStore, PaperBroker, Quote
import rc6_annual_instrument_analysis as an
import as_greeks_engine as gr
from bt_caucion_paper import CaucionOffer

ROOT=tempfile.TemporaryDirectory(prefix='issue468-')
RESULTS=[]
NOW=datetime(2026,10,2,16,0,tzinfo=timezone.utc)
def iso(t): return t.isoformat(timespec='microseconds')
class Store:
    def __init__(self,name): self.path=str(Path(ROOT.name)/(name+'.db'))
    @contextmanager
    def connect(self):
        c=sqlite3.connect(self.path,timeout=.3); c.row_factory=sqlite3.Row
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback(); raise
        finally: c.close()
def rows(s,sql,args=()):
    with s.connect() as c: return [dict(r) for r in c.execute(sql,args).fetchall()]
def run(name,fn):
    try: RESULTS.append({'id':name,'execution':'COMPLETED','result':fn()})
    except BaseException as e: RESULTS.append({'id':name,'execution':'HARNESS_ERROR','exception':repr(e)})
def outcome(fn):
    try: return {'accepted':True,'value':fn()}
    except BaseException as e: return {'accepted':False,'exception':type(e).__name__,'reason':str(e)}
def candle(**kw):
    return hs.Candle(**(dict(symbol='AUDIT',instrument_type='ACCIONES',market='BYMA',settlement='A-24HS',date='2026-09-30',open=100,high=102,low=98,close=100,volume=1000,source='PPI_API',observed_at='2026-10-01T16:00:00+00:00')|kw))
def canonical(s): return rows(s,'SELECT * FROM history_canonical_v2')

cases={
 'future_date':{'date':'2099-12-31'},'weekend':{'date':'2026-09-27'},
 'holiday_calendar_absent':{'date':'2026-07-09'},'invalid_date':{'date':'not-a-date'},
 'zero':{'close':0},'negative':{'close':-1},'nan':{'close':float('nan')},
 'infinite_close_only':{'open':None,'high':None,'low':None,'close':float('inf')},
 'partial_ohlc_close_outside':{'open':None,'high':102,'low':98,'close':500},
 'high_below_low':{'high':97},'open_outside':{'open':500},'close_outside':{'close':500},
 'jump_x10':{'open':1000,'high':1020,'low':980,'close':1000},
 'jump_div10':{'open':10,'high':10.2,'low':9.8,'close':10},
 'negative_volume':{'volume':-1},'zero_volume':{'volume':0},'nan_volume':{'volume':float('nan')},
 'infinite_volume':{'volume':float('inf')},'wrong_settlement':{'settlement':'NOT_A_TERM'},
 'unknown_family':{'instrument_type':'NONSENSE'},'alias':{'instrument_type':'ON'},
 'incomplete_identity':{'market':''},'unknown_market':{'market':'UNKNOWN'},
 'naive_observed':{'observed_at':'2026-10-01T16:00:00'},
}
for name,kw in cases.items():
    def case(name=name,kw=kw):
        s=Store('hist_'+name); result=outcome(lambda:hs.append_candle(s,candle(**kw)))
        if result['accepted']: result['canonical']=canonical(s)
        return result
    run('H_VALID_'+name,case)

def precedence():
    s=Store('precedence'); out=[]
    for source,adjusted,price,obs in [('PPI_API',False,100,'2026-10-01T16:00:00Z'),('YAHOO',True,50,'2026-10-01T17:00:00Z'),('PPI_API',False,102,'2026-10-02T17:00:00Z')]:
        hs.append_candle(s,candle(source=source,adjusted=adjusted,open=price,high=price,low=price,close=price,observed_at=obs)); out.append(canonical(s)[0])
    return out
run('H_SOURCE_ADJUSTED_PRECEDENCE',precedence)
def timestamps():
    s=Store('timestamps')
    a=hs.append_candle(s,candle()); b=hs.append_candle(s,candle(observed_at='2026-10-02T19:00:00+00:00'))
    return {'first':a,'repeat':b,'canonical':canonical(s),'versions':rows(s,'SELECT id,observed_at FROM history_versions_v2')}
run('H_REINGEST_REFRESH',timestamps)
def timeorder():
    s=Store('timezone'); hs.append_candle(s,candle(observed_at='2026-10-01T16:00:00+00:00'))
    hs.append_candle(s,candle(close=101,observed_at='2026-10-01T14:00:00-03:00'))
    return {'later_absolute_should_be':101,'canonical':canonical(s)}
run('H_TIMEZONE_LEXICAL',timeorder)
def currencies():
    s=Store('currencies')
    hs.append_candle(s,candle(metadata={'currency':'ARS'}))
    hs.append_candle(s,candle(open=1,high=1,low=1,close=1,metadata={'currency':'USD'},observed_at='2026-10-02T16:00:00Z'))
    return {'canonical':canonical(s),'versions':rows(s,'SELECT id,metadata_json FROM history_versions_v2')}
run('H_CURRENCY_COLLISION',currencies)
def settlement_control():
    s=Store('settlement'); hs.append_candle(s,candle());hs.append_candle(s,candle(settlement='INMEDIATA'))
    return {'canonical_rows':len(canonical(s))}
run('H_SETTLEMENT_ISOLATION_CONTROL',settlement_control)
def duplicates():
    s=Store('duplicates'); a=hs.append_candle(s,candle());b=hs.append_candle(s,candle());c=hs.append_candle(s,candle(close=101));d=hs.append_candle(s,candle(observed_at='2026-10-03T16:00:00Z'))
    return {'calls':[a,b,c,d],'versions':rows(s,'SELECT * FROM history_versions_v2'),'canonical':canonical(s)}
run('H_DUPLICATE_CONFLICT_ABA',duplicates)
def interrupted():
    s=Store('interrupted');hs.init_schema(s)
    with s.connect() as c: c.execute("CREATE TRIGGER break_canonical BEFORE INSERT ON history_canonical_v2 BEGIN SELECT RAISE(ABORT,'injected crash'); END")
    r=outcome(lambda:hs.append_candle(s,candle()))
    return {'outcome':r,'versions_after_abort':rows(s,'SELECT * FROM history_versions_v2'),'canonical':canonical(s)}
run('S_ATOMIC_CANONICAL_ABORT_CONTROL',interrupted)
def partialbatch():
    s=Store('partialbatch');r=outcome(lambda:hs.append_many(s,[candle(),candle(date='2026-10-01',close=-1)]))
    return {'outcome':r,'persisted':canonical(s)}
run('S_PARTIAL_BATCH_BOUNDARY',partialbatch)
def locked():
    s=Store('locked');hs.init_schema(s)
    c=sqlite3.connect(s.path);c.execute('BEGIN EXCLUSIVE');start=time.monotonic()
    r=outcome(lambda:hs.append_candle(s,candle()));elapsed=time.monotonic()-start;c.rollback();c.close()
    return {'outcome':r,'elapsed_seconds':elapsed,'retry':hs.append_candle(s,candle()),'canonical_count':len(canonical(s)),'timeout_harness_seconds':.3}
run('S_DB_LOCK_RECOVERY',locked)
def concurrent():
    s=Store('concurrent');hs.init_schema(s); barrier=threading.Barrier(2)
    class Cur:
        def __init__(self,c):self.c=c
        def fetchone(self):
            value=self.c.fetchone();barrier.wait(timeout=5);return value
    class Conn:
        def __init__(self,c):self.c=c
        def execute(self,sql,*a):
            r=self.c.execute(sql,*a)
            return Cur(r) if sql.strip().startswith('SELECT id FROM history_versions_v2') else r
        def executescript(self,*a):return self.c.executescript(*a)
    class Racing:
        @contextmanager
        def connect(self):
            c=sqlite3.connect(s.path,timeout=5);c.row_factory=sqlite3.Row
            try:yield Conn(c);c.commit()
            except BaseException:c.rollback();raise
            finally:c.close()
    with ThreadPoolExecutor(max_workers=2) as pool: calls=list(pool.map(lambda _:outcome(lambda:hs.append_candle(Racing(),candle())),range(2)))
    return {'calls':calls,'versions':rows(s,'SELECT id,payload_hash FROM history_versions_v2'),'canonical':canonical(s)}
run('S_CONCURRENT_IDENTICAL_APPEND',concurrent)

def provider_row(**kw):return dict(date='2026-09-30T17:00:00-03:00',openingPrice=100,max=102,min=98,price=100,volume=1000)|kw
policy_cases={
 'duplicate_exact':[provider_row(),provider_row()],
 'duplicate_conflict':[provider_row(),provider_row(price=101)],
 'same_instant_offset':[provider_row(),provider_row(date='2026-09-30T20:00:00+00:00')],
 'same_day_different_hour':[provider_row(),provider_row(date='2026-09-30T16:00:00-03:00')],
 'future':[provider_row(date='2099-01-01')],
 'weekend':[provider_row(date='2026-09-27')],
 'holiday_calendar_absent':[provider_row(date='2026-07-09')],
 'ten_missing_sessions':[provider_row(date='2026-09-01'),provider_row(date='2026-09-16')],
 'reverse_order':[provider_row(date='2026-09-30'),provider_row(date='2026-09-29')],
 'partial':[provider_row(),provider_row(date='2026-09-29',price=-1)],
 'ten_bars':[provider_row(date=f'2026-09-{i+1:02}T17:00:00-03:00') for i in range(10)],
 'empty':[], 'wrong_shape':{}, 'naive':[provider_row(date='2026-09-30T17:00:00')],
}
for key,val in [('zero',0),('negative',-1),('nan','NaN'),('infinity','Infinity')]:policy_cases[key]=[provider_row(price=val)]
for key,kw in [('high_below_low',{'max':97}),('open_outside',{'openingPrice':500}),('close_outside',{'price':500}),('negative_volume',{'volume':-1}),('zero_volume',{'volume':0}),('missing_volume',{'volume':None}),('x10',{'price':1000,'openingPrice':1000,'max':1020,'min':980})]:policy_cases[key]=[provider_row(**kw)]
for name,payload in policy_cases.items():
    def case(payload=payload):
        v=hp.validate_provider_history(payload,as_of=NOW)
        return dataclasses.asdict(v)|{'context':v.context_state,'quality':v.storage_quality}
    run('PPI_VALID_'+name,case)

Q=SimpleNamespace(symbol='AUDIT',asset_class='ACCIONES',market='BYMA',currency='ARS',settlement='A-24HS')
def shadow_future():
    s=Store('shadowfuture')
    with s.connect() as c:
        c.execute('CREATE TABLE production_history(symbol,instrument_type,settlement,downloaded_at,payload_json)')
        p=[provider_row(date=f'2026-09-{i+1:02}T17:00:00-03:00') for i in range(20)]
        c.execute('INSERT INTO production_history VALUES(?,?,?,?,?)',(Q.symbol,Q.asset_class,Q.settlement,iso(NOW+timedelta(days=2)),json.dumps(p)))
    return {'decision_at':iso(NOW),'downloaded_at':iso(NOW+timedelta(days=2)),'feature':sh._history_features(s,Q,NOW)}
run('L_SHADOW_FUTURE_DOWNLOAD',shadow_future)
def shadow_dupes():
    s=Store('shadowdupes')
    with s.connect() as c:
        c.execute('CREATE TABLE production_history(symbol,instrument_type,settlement,downloaded_at,payload_json)')
        c.execute('INSERT INTO production_history VALUES(?,?,?,?,?)',(Q.symbol,Q.asset_class,Q.settlement,iso(NOW),json.dumps([provider_row()]*20)))
    return sh._history_features(s,Q,NOW)
run('H_SHADOW_DUPLICATE_DAYS',shadow_dupes)
def series(**kw):return ce.Series(**(dict(symbol='AUDIT',asset_class='ACCIONES',market='BYMA',currency='ARS',settlement='A-24HS',resolution='5m',source='AUDIT_PROVIDER',adjustment='RAW',adjustment_basis='EXPLICIT_TEST',price_kind='PROVIDER_OHLC',volume_kind='QUANTITY',cash_multiplier='1')|kw))
def bar(price=100,**kw):return ce.Bar(**(dict(start='2026-10-02T15:00:00+00:00',open=D(price),high=D(price)+1,low=D(price)-1,close=D(price),volume=D(100),quality='COMPLETE')|kw))
def shadow_revisions():
    s=Store('revisions');ce.init_schema(s);a=ce.CandleArchive(s);ser=series()
    for i in range(15):a.put(ser,bar(100+i),known_at=iso(NOW-timedelta(minutes=45-i)))
    return {'archive_bars':len(a.read(ser,as_of=iso(NOW))),'shadow':sh._candle_features(s,Q,NOW)}
run('C_SHADOW_REVISION_MULTIPLICATION',shadow_revisions)
def invalid_revision():
    s=Store('invalidrevision');ce.init_schema(s);a=ce.CandleArchive(s);ser=series()
    a.put(ser,bar(),known_at='2026-10-02T15:06:00Z');a.put(ser,bar(99,quality='CONFLICT'),known_at='2026-10-02T15:07:00Z')
    return {'archive_bars':a.read(ser,as_of=iso(NOW)),'shadow':sh._candle_features(s,Q,NOW)}
run('C_SHADOW_SUPERSEDED_VALID_VERSION',invalid_revision)
for name,ser,br,known in [('future_bar',series(),bar(),'2026-10-02T15:04:00Z'),('sample_as_complete',series(price_kind='TRADE_SAMPLES',volume_kind='UNKNOWN'),bar(),'2026-10-02T15:06:00Z'),('unknown_volume_unit',series(volume_kind='UNKNOWN'),bar(),'2026-10-02T15:06:00Z'),('partial_price_nan',series(),bar(close=D('NaN')),'2026-10-02T15:06:00Z')]:
    def case(name=name,ser=ser,br=br,known=known):
        s=Store('c_'+name);ce.init_schema(s);return outcome(lambda:ce.CandleArchive(s).put(ser,br,known_at=known))
    run('C_CONTROL_'+name,case)
def candle_version_control():
    s=Store('cversion');ce.init_schema(s);a=ce.CandleArchive(s);ser=series();a.put(ser,bar(),known_at='2026-10-02T15:06:00Z');a.put(ser,bar(110),known_at='2026-10-02T15:10:00Z')
    return {'before':a.read(ser,as_of='2026-10-02T15:08:00Z'),'after':a.read(ser,as_of='2026-10-02T15:11:00Z'),'backdate':outcome(lambda:a.put(ser,bar(99),known_at='2026-10-02T15:09:00Z')),'same_timestamp':outcome(lambda:a.put(ser,bar(99),known_at='2026-10-02T15:10:00Z'))}
run('C_ASOF_AND_CORRECTION_CONTROLS',candle_version_control)

REC=dict(ticker='AUDIT',instrument_type='ACCIONES',market='BYMA',currency='ARS',settlement='A-24HS',capability='READY_PAPER_SPOT')
def scalp_store(name):
    s=Store(name);sc.init_schema(s)
    with s.connect() as c:c.execute('CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,symbol,asset_class,settlement,currency,market,bid,ask,book_at,observed_at,last,bid_size,ask_size,trade_at,last_kind,source)')
    return s
def fill_scalp(s,events,*,received=None,book_at=None,volumes=None,bid='115',ask='115.01',checked=None):
    rec=sc._identity(REC);received=received or iso(NOW);checked=checked or iso(NOW)
    with s.connect() as c:
        c.executemany('INSERT INTO ppi_intraday_points VALUES(?,?,?,?,?,?,?,?,?,?,?)',[(*rec,iso(t),str(100+i),str((volumes or [1]*len(events))[i]),received,received,'PPI_MARKETDATA_INTRADAY') for i,t in enumerate(events)])
        c.execute('INSERT INTO ppi_intraday_contract_state VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(*rec,'CONFIRMED_INTERVAL_VOLUME',2,5,0,1,iso(events[-1]),checked,'synthetic confirmed fixture'))
        c.execute('INSERT INTO market_snapshots VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('AUDIT','ACCIONES','A-24HS','ARS','BYMA',bid,ask,book_at or iso(NOW),iso(NOW),'115','1000','1000',iso(NOW),'TRADE','PRODUCTION_PAPER'))
def scalp_case(name,events,**kw):
    s=scalp_store('sc_'+name);fill_scalp(s,events,**kw); action=sc.evaluate_candidate(s,REC,at=iso(NOW))
    return {'action':action,'first_event':iso(events[0]),'last_event':iso(events[-1]),'elapsed_minutes':(events[-1]-events[0]).total_seconds()/60,'candidate':rows(s,'SELECT * FROM scalping_candidates')}
events=[NOW-timedelta(minutes=15-i) for i in range(15)]
scenarios={
 'regular':(events,{}),
 'same_minute_15_points':([NOW-timedelta(seconds=50-i*2) for i in range(15)],{}),
 'irregular_43_minutes':([NOW-timedelta(minutes=43-i*3) for i in range(15)],{}),
 'stale_tail_20_minutes':([NOW-timedelta(minutes=34-i) for i in range(15)],{'checked':iso(NOW-timedelta(minutes=20))}),
 'future_received':(events,{'received':iso(NOW+timedelta(hours=1))}),
 'open_minute':([NOW-timedelta(minutes=14-i,seconds=1) for i in range(15)],{}),
 'zero_volume':(events,{'volumes':[0]*15}),
 'crossed_book':(events,{'bid':'115','ask':'114'}),
 'stale_book':(events,{'book_at':iso(NOW-timedelta(minutes=3))}),
 'future_book':(events,{'book_at':iso(NOW+timedelta(minutes=1))}),
}
for name,(ev,kw) in scenarios.items():run('SC_'+name,lambda name=name,ev=ev,kw=kw:scalp_case(name,ev,**kw))
def normalization_sc():
    data=[dict(date=iso(NOW-timedelta(seconds=50-i*2)),price=100+i,volume=1) for i in range(15)]
    return {'distinct_minutes':len({p['date'][:16] for p in data}),'normalization':outcome(lambda:sc.normalize_payload(data,received_at=iso(NOW)))}
run('SC_NORMALIZE_DUPLICATE_MINUTES',normalization_sc)
def vol_contract(kind):
    s=scalp_store('vol_'+kind)
    vols=list(range(10,17)) if kind=='monotone_interval' else [10,20,30,40,50,5,6]
    pts=[(iso(NOW-timedelta(minutes=20-i)),D(100+i),D(v)) for i,v in enumerate(vols)]
    first=sc.persist_payload(s,REC,pts[:-1],received_at=iso(NOW-timedelta(minutes=1)))
    second=sc.persist_payload(s,REC,pts,received_at=iso(NOW))
    return {'true_fixture_semantics':kind,'first':first,'second':second}
run('SC_VOLUME_FALSE_NEGATIVE',lambda:vol_contract('monotone_interval'))
run('SC_VOLUME_FALSE_POSITIVE',lambda:vol_contract('cumulative_with_reset'))
def mutable_revision():
    s=scalp_store('mutable');event=NOW-timedelta(seconds=60)
    sc.persist_payload(s,REC,[(iso(event),D(100),D(1))],received_at=iso(NOW-timedelta(seconds=30)))
    sc.persist_payload(s,REC,[(iso(event),D(110),D(2))],received_at=iso(NOW))
    return rows(s,'SELECT * FROM ppi_intraday_points')
run('L_SC_MUTABLE_NO_VERSION',mutable_revision)

def main_ablation():
    output=[]
    for pattern in ('up','flat','down'):
      for variant in 'ABCDEFGH':
        s=PaperStore(str(Path(ROOT.name)/f'broker_{pattern}_{variant}.db'))
        for i in range(20):
            at=NOW-timedelta(minutes=(19-i)*4);p=D(100)+(D(i)/5 if pattern=='up' else -D(i)/5 if pattern=='down' else 0)
            q=Quote('AUDIT','ACCIONES','A-24HS',p,p,p+D('.01'),D(10000),D(10000),iso(at),currency='ARS',market='BYMA',book_at=iso(at),trade_at=iso(at),last_kind='TRADE')
            s.add_quote(q)
        # Production-history payloads differ; market-event samples are held fixed.
        if variant!='B':
          with s.connect() as c:
            c.execute('DROP TABLE IF EXISTS production_history');c.execute('CREATE TABLE production_history(symbol,instrument_type,settlement,downloaded_at,payload_json)')
            hist=[provider_row(date=f'2026-09-{i+1:02}T17:00:00-03:00',price=100+i,openingPrice=100+i,max=101+i,min=99+i) for i in range(25)]
            if variant=='C':hist=[dict(r,date=r['date'].replace('2026-09','2025-09')) for r in hist]
            if variant=='D':hist=hist[::2]
            if variant=='E':hist=[dict(r,source='YAHOO',adjusted=True) for r in hist]
            c.execute('INSERT INTO production_history VALUES(?,?,?,?,?)',('AUDIT','ACCIONES','A-24HS',iso(NOW),json.dumps(hist)))
        ce.init_schema(s)
        if variant!='G':
          a=ce.CandleArchive(s)
          for i in range(15):a.put(series(),bar(100+i,start=iso(NOW-timedelta(minutes=80-5*i)),volume=None if variant=='H' else D(100)),known_at=iso(NOW-timedelta(minutes=75-5*i)))
        broker=PaperBroker(s,daily_loss_pct=None,signal_window_minutes=15 if variant=='F' else 90)
        decision=broker.decide(q)
        output.append({'pattern':pattern,'variant':variant,'action':decision[0],'score':str(decision[1]),'reason':decision[2],'features':decision[3]})
    return output
run('ABLATION_MAIN_A_H',main_ablation)

def math_indicators():
    values=list(range(100,131));r=[dict(high=v+2,low=v-2,close=v) for v in values]
    return {'SMA20':an._sma(values,20),'expected_SMA20':120.5,'EMA14':an._ema(values,14),'expected_EMA14':123.5,'RSI14_monotone':an._rsi(values),'RSI14_flat':an._rsi([100]*31),'ATR14':an._atr(r),'expected_ATR14':4,'return20':an._period_return(values,20),'expected_return20':130/110-1,'drawdown_fixture':min(p/max([100,120,90,110][:i+1])-1 for i,p in enumerate([100,120,90,110]))}
run('M_INDICATOR_IDENTITIES',math_indicators)
def greeks():
    S,K,T,r,v=100.,105.,60/365,.3,.4;p=gr.precio_teorico(S,K,T,r,v,True);h=.001
    out=gr.calcular(p,S,K,60,True,r).to_dict()
    out['independent_finite_difference']={'delta':(gr.precio_teorico(S+h,K,T,r,v)-gr.precio_teorico(S-h,K,T,r,v))/(2*h),'gamma':(gr.precio_teorico(S+h,K,T,r,v)-2*p+gr.precio_teorico(S-h,K,T,r,v))/(h*h),'vega_per_percentage_point':(gr.precio_teorico(S,K,T,r,v+h)-gr.precio_teorico(S,K,T,r,v-h))/(2*h)/100,'theta_day':(gr.precio_teorico(S,K,T-h,r,v)-gr.precio_teorico(S,K,T+h,r,v))/(2*h)/365}
    out['put_call_parity_error']=p-gr.precio_teorico(S,K,T,r,v,False)-(S-K*math.exp(-r*T));return out
run('M_GREEKS_FINITE_DIFFERENCES',greeks)
def european_put():
    p=gr.precio_teorico(80,100,180/365,.3,.1,False)
    return {'model_price':p,'intrinsic':20,'iv_direct':gr.volatilidad_implicita(p,80,100,180/365,.3,False),'wrapper':gr.calcular(p,80,100,180,False,.3).to_dict()}
run('M_EUROPEAN_PUT_WRAPPER',european_put)
def caucion():
    out=[]
    for basis in (360,365):
        offer=CaucionOffer('AUDIT','ARS',D('.30'),'2026-10-02','2026-10-05T17:00:00-03:00','2026-10-02T13:00:00-03:00',D(1000000),D(1),D(1),basis,'MATURITY','EXPLICIT_SYNTHETIC',D(0),D(100000))
        out.append({'basis':basis,'days':offer.interest_days,'result':offer.economics(D(100000)),'reference':str((D(100000)*D('.3')*3/basis).quantize(D('.01')))})
    return out
run('M_CAUCION_DAY_COUNT',caucion)

# Isolate provider I/O with a fake requests dependency; actual adapters remain unmodified.
fake=ModuleType('requests');fake.Session=object;fake.RequestException=TimeoutError;sys.modules['requests']=fake
import al_historical_ingest as legacy
import ba_data912_history as d912
legacy.HIST_DB_PATH=str(Path(ROOT.name)/'legacy.db')
def units():
    parsed=legacy._normalizar_fila_iol(dict(fecha='2026-09-30',ultimoPrecio=100,apertura=100,maximo=100,minimo=100,montoOperado=100000,volumen=1000))
    return {'input_price':100,'input_cash_turnover':100000,'input_quantity':1000,'normalized_volume':parsed[-1],'multiply_price_volume':parsed[-1]*100,'overstatement_factor':parsed[-1]*100/100000}
run('U_IOL_CASH_AS_VOLUME',units)
def provider_faults():
    out=[];sleeps=[];original=d912.time.sleep;d912.time.sleep=lambda n:sleeps.append(n)
    class FakeClient:
        def __init__(self,kind):self.kind=kind;self.calls=0
        def get(self,*a,**kw):
            self.calls+=1
            if self.kind=='timeout':raise TimeoutError('injected provider timeout')
            return SimpleNamespace(status_code=429 if self.kind=='429' else 200,json=lambda:[] if self.kind=='empty' else [dict(date='2026-09-30',c=100),dict(date='2026-09-29',c=-1)])
    try:
      for kind in ('timeout','429','empty','partial'):
        client=FakeClient(kind);sleeps.clear();r=outcome(lambda:d912._get_json(client,'/synthetic'))
        out.append({'kind':kind,'calls':client.calls,'simulated_sleep_seconds':list(sleeps),'result':r})
    finally:d912.time.sleep=original
    return out
run('S_PROVIDER_FAULTS',provider_faults)
def d912_bad():return {'future':d912._normalize([dict(date='2099-01-01',o=100,h=102,l=98,c=100,v=1)],'2026-01-01'),'nan':d912._normalize([dict(date='2026-09-30',o=100,h=102,l=98,c='NaN',v=1)],'2026-01-01'),'invalid_ohlc':d912._normalize([dict(date='2026-09-30',o=100,h=90,l=98,c=100,v=1)],'2026-01-01')}
run('H_DATA912_VALIDATION',d912_bad)

def legacy_identity_collision():
    legacy.HIST_DB_PATH=str(Path(ROOT.name)/'legacy_collision.db')
    legacy.init_db()
    legacy.guardar_velas('AUDIT','ACCIONES',[('2026-09-30',100,100,100,100,1000)],'IOL',True)
    legacy.guardar_velas('AUDIT','BONOS',[('2026-09-30',1,1,1,1,1000)],'DATA912',False)
    with legacy._conn() as c: row=c.execute('SELECT symbol,asset_class,date,close,source,adjusted FROM market_historical_ohlcv').fetchall()
    return {'rows_after_two_family_writes':row}
run('H_LEGACY_ASSET_CLASS_COLLISION',legacy_identity_collision)
def legacy_liquidity():
    legacy.HIST_DB_PATH=str(Path(ROOT.name)/'legacy_liquidity.db')
    legacy.init_db()
    value=legacy._normalizar_fila_iol(dict(fecha='2026-09-30',ultimoPrecio=100,apertura=100,maximo=100,minimo=100,montoOperado=100000,volumen=1000))
    legacy.guardar_velas('AUDIT','ACCIONES',[value],'IOL',True)
    return {'cash_turnover_true_synthetic':100000,'archived_liquidity_result':d912.archived_liquidity('AUDIT','ACCIONES'),'legacy_record':value}
run('U_IOL_ARCHIVED_LIQUIDITY',legacy_liquidity)
def dense_main_signal():
    out=[]
    for minutes in (90,1):
        s=PaperStore(str(Path(ROOT.name)/f'main_dense_{minutes}.db'))
        for i in range(20):
            when=NOW-timedelta(seconds=(19-i)*(minutes*60/19))
            p=D(100)+D(i)/5
            quote=Quote('AUDIT','ACCIONES','A-24HS',p,p,p+D('.01'),D(1000),D(1000),iso(when),currency='ARS',market='BYMA',book_at=iso(when),trade_at=iso(when),last_kind='TRADE')
            s.add_quote(quote)
        d=PaperBroker(s,daily_loss_pct=None).decide(quote)
        out.append({'time_span_minutes':minutes,'action':d[0],'score':str(d[1]),'samples':d[3].get('samples')})
    return out
run('M_MAIN_SAMPLE_CADENCE',dense_main_signal)
def annual_fixed_income_label():
    item=('BONOS','AUDIT','BYMA','A-24HS')
    bars=[dict(date=f'2026-09-{i+1:02}',open=100,high=101,low=95,close=100-i/4,volume=1,adjusted=0,source='PPI') for i in range(20)]
    report=an._render_report(item,bars,2026)
    return {'performance_label': 'Performance 2026' in report,'coupon_exclusion_near_performance': 'excluye cupones' in report,'no_buy_sell_instruction':'No se emite compra/venta' in report,'preview':report[:600]}
run('M_FIXED_INCOME_ANNUAL_LABEL',annual_fixed_income_label)
def worker_idle_heartbeat():
    s=Store('worker_idle');ce.init_schema(s)
    with s.connect() as c: c.execute('CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY)')
    m=ce.SampleMaterializer(s)
    first=m.tick(NOW);second=m.tick(NOW+timedelta(minutes=1))
    return {'first':first,'second':second,'worker':rows(s,'SELECT * FROM candle_worker_state')}
run('S_HEARTBEAT_NO_PROGRESS',worker_idle_heartbeat)
def crash_inside_append():
    import multiprocessing
    s=Store('killduring');hs.init_schema(s)
    def child(path):
        import os, sqlite3
        class ChildStore:
            @contextmanager
            def connect(self):
                c=sqlite3.connect(path);c.row_factory=sqlite3.Row
                try: yield c;c.commit()
                finally:c.close()
        hs._prefer=lambda *_:os._exit(79) # After version INSERT, before canonical INSERT/commit.
        hs.append_candle(ChildStore(),candle())
    p=multiprocessing.Process(target=child,args=(s.path,));p.start();p.join(timeout=5)
    if p.is_alive():p.terminate();p.join()
    result={'child_exitcode':p.exitcode,'versions_after_restart':rows(s,'SELECT * FROM history_versions_v2'),'canonical_after_restart':canonical(s)}
    result['retry']=hs.append_candle(s,candle());result['versions_after_retry']=len(rows(s,'SELECT * FROM history_versions_v2'))
    return result
run('S_CRASH_AFTER_VERSION_INSERT',crash_inside_append)

out={'source_path':str(Path(sys.argv[1]).resolve()),'network_disabled':True,'runtime_mutations':0,'real_orders_sent_by_audit':0,'tests':RESULTS}
Path(sys.argv[2]).write_text(json.dumps(out,indent=2,default=str,ensure_ascii=False))
print(json.dumps({'tests':len(RESULTS),'harness_errors':[r for r in RESULTS if r['execution']=='HARNESS_ERROR'],'output':sys.argv[2]},default=str))
