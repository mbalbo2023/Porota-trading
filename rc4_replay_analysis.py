#!/usr/bin/env python3
"""Offline RC4 replay diagnostics. It reports evidence; it never changes runtime parameters."""
from __future__ import annotations
import json, math, os, sqlite3, statistics
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from rc4_replay import atr, slippage_bps

DB_DEFAULT='data/paper_v17/observer_v17.db'
ATR_PERIODS=(10,15,20,25,30)
HOLD_CANDIDATES=(60,90,120,180,240,300)

def _dt(v):
    if not v:return None
    try:return datetime.fromisoformat(str(v).replace('Z','+00:00'))
    except Exception:return None

def _percentiles(values):
    xs=sorted(float(x) for x in values)
    if not xs:return None
    def q(p):
        if len(xs)==1:return xs[0]
        pos=(len(xs)-1)*p; lo=math.floor(pos); hi=math.ceil(pos)
        return xs[lo] if lo==hi else xs[lo]+(xs[hi]-xs[lo])*(pos-lo)
    return {'n':len(xs),'p10':q(.1),'p50':q(.5),'p90':q(.9),'mean':statistics.fmean(xs)}

def analyze(db_path=DB_DEFAULT, history_path=None):
    out={'mode':'OFFLINE_READ_ONLY','parameter_changes':False,'slippage':{},'durations':{},
         'max_hold_replay':{'state':'INSUFFICIENT_DATA','reason':'Requires overlapping path/marks to know counterfactual exit price.'},
         'atr':{},'fixed_vs_atr':{'state':'INSUFFICIENT_DATA'},'tick_vs_candle':{'state':'INSUFFICIENT_DATA'},
         'emergency_cap':{'state':'DERIVED_FROM_RISK_POLICY','parameter_changes':False}}
    path=Path(db_path)
    if not path.exists(): return dict(out,db_available=False)
    c=sqlite3.connect(f'file:{path.resolve()}?mode=ro',uri=True); c.row_factory=sqlite3.Row
    try:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'paper_fills' in tables:
            absolute=defaultdict(list); bps=defaultdict(list)
            for r in c.execute("SELECT side,price,slippage FROM paper_fills WHERE slippage IS NOT NULL"):
                try:
                    side=str(r['side'] or 'UNKNOWN').upper()
                    fill=Decimal(str(r['price'])); slip=abs(Decimal(str(r['slippage'])))
                    absolute[side].append(float(slip))
                    reference=(fill-slip) if side.startswith('BUY') else (fill+slip)
                    if reference>0:
                        bps[side].append(float(slip/reference*Decimal(10000)))
                except Exception:
                    pass
            out['slippage']={
                'label':'PAPER_MODELED_FROM_RECORDED_FILL_AND_REFERENCE',
                'broker_real_execution':False,
                'absolute_by_side':{k:_percentiles(v) for k,v in absolute.items()},
                'bps_by_side':{k:_percentiles(v) for k,v in bps.items()},
            }
        if 'paper_positions' in tables:
            mins=[]
            for r in c.execute("SELECT opened_at,closed_at FROM paper_positions WHERE closed_at IS NOT NULL"):
                a,b=_dt(r['opened_at']),_dt(r['closed_at'])
                if a and b and b>=a: mins.append((b-a).total_seconds()/60)
            out['durations']=_percentiles(mins) or {'n':0}
        hpath=Path(history_path or os.getenv('HIST_DB_PATH','data/market_history.db'))
        if hpath.exists():
            h=sqlite3.connect(f'file:{hpath.resolve()}?mode=ro',uri=True); h.row_factory=sqlite3.Row
            try:
                ht={r[0] for r in h.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'history_canonical_v2' in ht:
                    rows=list(h.execute("SELECT symbol,instrument_type,market,settlement,date,open,high,low,close FROM history_canonical_v2 ORDER BY symbol,instrument_type,market,settlement,date"))
                    groups=defaultdict(list)
                    for r in rows: groups[(r['symbol'],r['instrument_type'],r['market'],r['settlement'])].append(dict(r))
                    atr_stats={}
                    for period in ATR_PERIODS:
                        vals=[]
                        for candles in groups.values():
                            try:
                                value=atr(candles,period)
                                if value is not None: vals.append(float(value))
                            except Exception: pass
                        atr_stats[str(period)]={'series_with_value':len(vals),'median_abs':statistics.median(vals) if vals else None}
                    out['atr']={'state':'EVIDENCE_AVAILABLE' if any(v['series_with_value'] for v in atr_stats.values()) else 'INSUFFICIENT_DATA','periods':atr_stats}
            finally:h.close()
    finally:c.close()
    return dict(out,db_available=True)

if __name__=='__main__': print(json.dumps(analyze(),ensure_ascii=False,indent=2,sort_keys=True))
