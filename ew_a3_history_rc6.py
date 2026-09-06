"""RC6 A3 historical background ingestion.

Read-only against A3 CEM; writes only normalized historical evidence + its own
checkpoint ledger. It has no order methods and never participates in the live
PPI decision path.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import time
from zoneinfo import ZoneInfo

from cv_history_store_adapter_hf6 import default_history_store
from cu_history_store_v2_hf6 import append_many, init_schema as init_history_schema
from cz_a3_cem_public_history_hf6 import A3CEMPublicReadOnlyClient, assert_cem_invariants
from db_a3_cem_normalizer_hf6 import normalize_symbols, closing_to_candle, assert_cem_normalizer_invariants
from cy_market_source_arbitration_hf6 import assert_source_invariants

TZ=ZoneInfo('America/Argentina/Buenos_Aires')
FAMILIES={'FUTUROS','OPCIONES'}
STATE_DDL='''
CREATE TABLE IF NOT EXISTS a3_history_ingest_state_rc6(
  source TEXT NOT NULL,symbol TEXT NOT NULL,instrument_type TEXT NOT NULL,
  market TEXT NOT NULL,settlement TEXT NOT NULL,
  last_complete_day TEXT,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
  empty_observations INTEGER NOT NULL DEFAULT 0,last_attempt_at TEXT,
  last_success_at TEXT,last_error TEXT NOT NULL DEFAULT '',rows_last INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(source,symbol,instrument_type,market,settlement));
'''


def init_state(store):
    with store.connect() as c: c.executescript(STATE_DDL)


def _tables(c):
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def load_targets(store):
    """Only exact verified Porota identities; no market/settlement inference."""
    with store.connect() as c:
        if 'candidate_universe' not in _tables(c): return []
        cols={r[1] for r in c.execute('PRAGMA table_info(candidate_universe)')}
        needed={'ticker','instrument_type','market','settlement','status'}
        if not needed.issubset(cols): return []
        select='ticker,instrument_type,market,settlement'
        rows=c.execute(f'''SELECT {select} FROM candidate_universe
          WHERE status='AVAILABLE' ORDER BY instrument_type,ticker,market,settlement''').fetchall()
    out=[]
    for row in rows:
        symbol,family,market,settlement=(str(x or '').upper().strip() for x in row)
        if family not in FAMILIES: continue
        if any(x in {'','UNKNOWN'} for x in (symbol,market,settlement)): continue
        out.append((symbol,family,market,settlement))
    return out


def _payload_rows(payload):
    if isinstance(payload,dict) and isinstance(payload.get('data'),list): return payload['data']
    if isinstance(payload,list): return payload
    return []


def _state(store,target):
    init_state(store)
    with store.connect() as c:
        row=c.execute('''SELECT * FROM a3_history_ingest_state_rc6 WHERE
          source='A3_CEM_CLOSING' AND symbol=? AND instrument_type=? AND market=? AND settlement=?''',target).fetchone()
        return dict(row) if row else None


def _record(store,target,*,status,day,rows,error='',empty=False,success=False,at=None):
    now=(at or datetime.now(TZ)).isoformat(timespec='seconds')
    prev=_state(store,target) or {}
    empty_count=int(prev.get('empty_observations') or 0)+(1 if empty else 0)
    if not empty: empty_count=0
    with store.connect() as c:
        c.execute('''INSERT INTO a3_history_ingest_state_rc6(
          source,symbol,instrument_type,market,settlement,last_complete_day,status,attempts,
          empty_observations,last_attempt_at,last_success_at,last_error,rows_last)
          VALUES('A3_CEM_CLOSING',?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(source,symbol,instrument_type,market,settlement) DO UPDATE SET
            last_complete_day=excluded.last_complete_day,status=excluded.status,
            attempts=a3_history_ingest_state_rc6.attempts+1,
            empty_observations=excluded.empty_observations,last_attempt_at=excluded.last_attempt_at,
            last_success_at=CASE WHEN excluded.last_success_at IS NOT NULL THEN excluded.last_success_at ELSE a3_history_ingest_state_rc6.last_success_at END,
            last_error=excluded.last_error,rows_last=excluded.rows_last''',
          (*target,day,status,int(prev.get('attempts') or 0)+1,empty_count,now,now if success else None,str(error)[:500],int(rows)))
    return empty_count


def _range_for(mode,current,state):
    today=current.date()
    last=None
    try: last=date.fromisoformat(str((state or {}).get('last_complete_day') or ''))
    except Exception: last=None
    if mode=='BOOTSTRAP':
        start=max(today-timedelta(days=365), (last+timedelta(days=1)) if last else today-timedelta(days=365))
    elif mode=='WEEKEND_DEEP':
        start=max(today-timedelta(days=180),(last+timedelta(days=1)) if last else today-timedelta(days=180))
    elif mode=='RECONCILE':
        start=today-timedelta(days=10)
    else:  # DAILY_INCREMENTAL
        start=(last+timedelta(days=1)) if last else today-timedelta(days=7)
    return start,today


def run(store,*,client=None,history_store=None,now=None,mode='DAILY_INCREMENTAL',batch_limit=40,throttle_seconds=0.0):
    assert_cem_invariants();assert_cem_normalizer_invariants();assert_source_invariants()
    mode=str(mode).upper()
    if mode not in {'BOOTSTRAP','DAILY_INCREMENTAL','RECONCILE','WEEKEND_DEEP'}:
        raise ValueError('A3_HISTORY_MODE_INVALID')
    current=now or datetime.now(TZ)
    if current.tzinfo is None: current=current.replace(tzinfo=TZ)
    with store.connect() as c:
        runtime=c.execute('SELECT session_state,real_orders_sent FROM observer_state WHERE id=1').fetchone()
        if not runtime or str(runtime[0])!='MARKET_CLOSED' or int(runtime[1] or 0)!=0:
            return {'ran':False,'reason':'RUNTIME_NOT_CLOSED_OR_SAFE','execution_allowed':False,'mode':mode}
    targets=load_targets(store)[:max(1,int(batch_limit))]
    cem=client or A3CEMPublicReadOnlyClient()
    catalog=normalize_symbols(cem.symbols())
    by_identity={(x.symbol,x.family):x for x in catalog if x.family in FAMILIES}
    hstore=history_store or default_history_store();init_history_schema(hstore);init_state(store)
    stats={'selected':len(targets),'matched':0,'unmatched':0,'failed':0,'empty':0,'complete':0,
           'versions_appended':0,'canonical_updates':0,'protected_by_precedence':0}
    per_target=[]
    for target in targets:
        symbol,family,market,settlement=target
        meta=by_identity.get((symbol,family))
        if meta is None:
            stats['unmatched']+=1
            _record(store,target,status='ALIGNMENT_UNVERIFIED',day=None,rows=0,error='CEM_SYMBOL_FAMILY_NOT_EXACT')
            per_target.append({'target':target,'status':'ALIGNMENT_UNVERIFIED'});continue
        state=_state(store,target);start,end=_range_for(mode,current,state)
        if start>end:
            per_target.append({'target':target,'status':'NOT_DUE'});continue
        try:
            payload=cem.closing_prices(symbol=symbol,date_from=start.isoformat(),date_to=end.isoformat(),page=1,page_size=500)
            raw=_payload_rows(payload);candles=[]
            for row in raw:
                if str(row.get('symbol') or '').upper().strip()!=symbol: continue
                try:
                    candle=closing_to_candle(row,instrument_type=family,market=market,settlement_identity=settlement)
                    if candle.symbol!=symbol or candle.instrument_type!=family: continue
                    candles.append(candle)
                except (TypeError,ValueError): continue
            if not candles:
                empties=_record(store,target,status='EMPTY_OBSERVED',day=(state or {}).get('last_complete_day'),rows=0,empty=True)
                status='EMPTY_CONFIRMED' if empties>=2 else 'EMPTY_OBSERVED'
                if status=='EMPTY_CONFIRMED':
                    _record(store,target,status=status,day=(state or {}).get('last_complete_day'),rows=0,empty=True)
                stats['empty']+=1;per_target.append({'target':target,'status':status,'range':[start.isoformat(),end.isoformat()]})
            else:
                result=append_many(hstore,candles)
                last=max(c.date for c in candles)
                _record(store,target,status='COMPLETE',day=last,rows=len(candles),success=True)
                stats['matched']+=1;stats['complete']+=1
                for key in ('versions_appended','canonical_updates','protected_by_precedence'):
                    stats[key]+=int(result.get(key) or 0)
                per_target.append({'target':target,'status':'COMPLETE','rows':len(candles),'last':last,'range':[start.isoformat(),end.isoformat()]})
        except Exception as exc:
            stats['failed']+=1
            _record(store,target,status='FAILED',day=(state or {}).get('last_complete_day'),rows=0,error=f'{type(exc).__name__}:{exc}')
            per_target.append({'target':target,'status':'FAILED','error':type(exc).__name__})
        if throttle_seconds: time.sleep(max(0,float(throttle_seconds)))
    return {'ran':True,'source':'A3_CEM_CLOSING','execution_allowed':False,'hot_path':False,
            'mode':mode,**stats,'targets':per_target}
