#!/usr/bin/env python3
"""Point-in-time entry feature audit for RC6 CLOSED PAPER trades.

All features are reconstructed only from data known at or before opened_at.
SQLite is query-only. No network, broker, parameter mutation or synthetic bars.
"""
from __future__ import annotations
import argparse,json,sqlite3,math
from collections import defaultdict
from datetime import datetime
from decimal import Decimal,InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

TZ=ZoneInfo("America/Argentina/Buenos_Aires")
D=Decimal

def dec(v):
    try:
        x=D(str(v)); return x if x.is_finite() else None
    except (InvalidOperation,TypeError,ValueError): return None

def dt(v):
    x=datetime.fromisoformat(str(v).replace("Z","+00:00"))
    if x.tzinfo is None: raise ValueError("NAIVE_TIMESTAMP")
    return x.astimezone(TZ)

def js(v):
    try:
        x=json.loads(v or "{}"); return x if isinstance(x,dict) else {}
    except Exception:return {}

def valid_ohlc(raw):
    if not isinstance(raw,dict):return None
    vals={}
    for k,names in {"open":("open","openingPrice"),"high":("high","max"),"low":("low","min"),"close":("close","price")}.items():
        v=next((dec(raw.get(n)) for n in names if dec(raw.get(n)) is not None),None)
        if v is None or v<=0:return None
        vals[k]=v
    if vals["low"]>vals["high"] or not vals["low"]<=vals["open"]<=vals["high"] or not vals["low"]<=vals["close"]<=vals["high"]:return None
    return vals

def ema(vals,span):
    if not vals:return None
    a=D(2)/D(span+1); x=vals[0]
    for v in vals[1:]:x=a*v+(D(1)-a)*x
    return x

def rsi(vals,period=14):
    if len(vals)<2:return (None,None)
    gains=[];losses=[]
    for a,b in zip(vals,vals[1:]):
        z=b-a;gains.append(max(D(0),z));losses.append(max(D(0),-z))
    alpha=D(1)/D(period)
    ag=gains[0];al=losses[0];series=[]
    def value(g,l):
        if l==0:return D(100) if g>0 else D(50)
        rs=g/l;return D(100)-D(100)/(D(1)+rs)
    series.append(value(ag,al))
    for g,l in zip(gains[1:],losses[1:]):
        ag=alpha*g+(D(1)-alpha)*ag;al=alpha*l+(D(1)-alpha)*al
        series.append(value(ag,al))
    return (series[-1],series[-2] if len(series)>1 else None)

def candles(c,p):
    q="""SELECT v.body_json,v.known_at,v.bar_start,v.bar_end
      FROM candle_versions v JOIN candle_series s ON s.series_id=v.series_id
      WHERE json_extract(s.identity_json,'$.symbol')=?
        AND json_extract(s.identity_json,'$.asset_class')=?
        AND json_extract(s.identity_json,'$.market')=?
        AND json_extract(s.identity_json,'$.currency')=?
        AND json_extract(s.identity_json,'$.settlement')=?
        AND json_extract(s.identity_json,'$.resolution')='5m'
        AND julianday(v.bar_end)<=julianday(?) AND julianday(v.known_at)<=julianday(?)
      ORDER BY julianday(v.bar_start) DESC LIMIT 80"""
    rows=list(c.execute(q,(p["symbol"],p["asset_class"],p["market"],p["currency"],p["settlement"],p["opened_at"],p["opened_at"])))
    bars=[]
    for r in reversed(rows):
        b=js(r["body_json"])
        if b.get("quality") not in {"COMPLETE","SAMPLED"} or b.get("synthetic"):continue
        o=valid_ohlc(b)
        if not o:continue
        vol=dec(b.get("volume"))
        bars.append({**o,"volume":vol,"known_at":r["known_at"],"bar_end":r["bar_end"]})
    closes=[b["close"] for b in bars]
    mom=None
    if len(closes)>=15:
        s=sum(closes[-3:],D(0))/D(3);l=sum(closes[-15:],D(0))/D(15);mom=s/l-D(1) if l else None
    rnow,rprev=rsi(closes)
    return {"state":"READY" if len(closes)>=21 else "INSUFFICIENT","n":len(closes),
      "momentum_3v15":mom,"ema9":ema(closes[-60:],9) if len(closes)>=9 else None,
      "ema21":ema(closes[-60:],21) if len(closes)>=21 else None,
      "rsi14":rnow,"rsi14_prev":rprev,
      "avg_range":sum((b["high"]/b["low"]-D(1) for b in bars[-20:]),D(0))/D(min(20,len(bars))) if bars else None,
      "volume_sum20":sum((b["volume"] or D(0) for b in bars[-20:]),D(0)) if bars else None}

def daily(c,p):
    row=c.execute("""SELECT payload_json,downloaded_at FROM production_history
      WHERE symbol=? AND instrument_type=? AND settlement=?
        AND julianday(downloaded_at)<=julianday(?)
      ORDER BY julianday(downloaded_at) DESC LIMIT 1""",
      (p["symbol"],p["asset_class"],p["settlement"],p["opened_at"])).fetchone()
    if not row:return {"state":"UNAVAILABLE","n":0}
    raw=js(row["payload_json"])
    payload=raw if isinstance(raw,list) else None
    if payload is None:
        try:
            x=json.loads(row["payload_json"] or "[]");payload=x if isinstance(x,list) else []
        except Exception:payload=[]
    opened=dt(p["opened_at"]).date()
    vals=[]
    for x in payload:
        if not isinstance(x,dict):continue
        ds=str(x.get("date") or "")[:10]
        try:
            if datetime.fromisoformat(ds).date()>opened:continue
        except Exception:continue
        o=valid_ohlc(x)
        if o:vals.append((ds,o))
    vals.sort(key=lambda z:z[0]);cl=[x[1]["close"] for x in vals]
    def trend(n):
        return cl[-1]/cl[-n]-D(1) if len(cl)>=n and cl[-n]>0 else None
    return {"state":"READY" if len(cl)>=20 else "INSUFFICIENT","n":len(cl),
      "downloaded_at":row["downloaded_at"],"trend20":trend(20),"trend50":trend(50)}

def metrics(rows):
    n=len(rows);w=[r for r in rows if r["outcome"]=="WIN"];l=[r for r in rows if r["outcome"]=="LOSS"]
    by={}
    for cur in sorted({r["currency"] for r in rows}):
        g=[r for r in rows if r["currency"]==cur]
        by[cur]={"n":len(g),"wins":sum(x["outcome"]=="WIN" for x in g),"losses":sum(x["outcome"]=="LOSS" for x in g),
          "net_total":str(sum((dec(x["net_pnl"]) or D(0) for x in g),D(0)))}
    return {"n":n,"wins":len(w),"losses":len(l),"win_rate_pct":str(D(len(w))*100/D(n) if n else D(0)),"by_currency":by}

def assess(rows,name,pred):
    covered=[r for r in rows if pred(r) is not None]
    kept=[r for r in covered if pred(r) is True];blocked=[r for r in covered if pred(r) is False]
    days=sorted({r["day"] for r in covered});cut=max(1,len(days)//2);first=set(days[:cut]);second=set(days[cut:])
    return {"name":name,"coverage":len(covered),"kept":metrics(kept),"blocked":metrics(blocked),
      "winners_blocked":sum(r["outcome"]=="WIN" for r in blocked),
      "losers_blocked":sum(r["outcome"]=="LOSS" for r in blocked),
      "first_half":metrics([r for r in kept if r["day"] in first]),
      "second_half":metrics([r for r in kept if r["day"] in second]),
      "days_first":sorted(first),"days_second":sorted(second)}

def build(db):
    c=sqlite3.connect(f"file:{Path(db)}?mode=ro",uri=True,timeout=20);c.row_factory=sqlite3.Row;c.execute("PRAGMA query_only=ON")
    try:
        mode,orders=c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if mode!="PRODUCTION_PAPER" or int(orders or 0)!=0:raise RuntimeError("SAFETY_STATE_INVALID")
        ps=[dict(r) for r in c.execute("SELECT * FROM paper_positions WHERE status='CLOSED' ORDER BY julianday(opened_at),paper_id")]
        rows=[]
        for p in ps:
            net=dec(p.get("net_pnl"));out="WIN" if net and net>0 else "LOSS" if net and net<0 else "FLAT"
            cf=candles(c,p);df=daily(c,p)
            row={"paper_id":p["paper_id"],"symbol":p["symbol"],"asset_class":p["asset_class"],"currency":p["currency"],
              "opened_at":p["opened_at"],"day":dt(p["opened_at"]).date().isoformat(),"net_pnl":p.get("net_pnl"),"outcome":out,
              "candle":{k:(str(v) if isinstance(v,D) else v) for k,v in cf.items()},
              "daily":{k:(str(v) if isinstance(v,D) else v) for k,v in df.items()}}
            rows.append(row)
        def v(r,k):return dec(r["candle"].get(k))
        def dv(r,k):return dec(r["daily"].get(k))
        filters=[
          ("CANDLE_MOMENTUM_3V15_POS",lambda r: None if v(r,"momentum_3v15") is None else v(r,"momentum_3v15")>0),
          ("EMA9_GT_EMA21_5M",lambda r: None if v(r,"ema9") is None or v(r,"ema21") is None else v(r,"ema9")>v(r,"ema21")),
          ("RSI14_35_65_5M",lambda r: None if v(r,"rsi14") is None else D(35)<=v(r,"rsi14")<=D(65)),
          ("RSI14_RISING_LT70_5M",lambda r: None if v(r,"rsi14") is None or v(r,"rsi14_prev") is None else v(r,"rsi14")>v(r,"rsi14_prev") and v(r,"rsi14")<D(70)),
          ("DAILY_TREND20_POS",lambda r: None if dv(r,"trend20") is None else dv(r,"trend20")>0),
          ("DAILY_TREND50_POS",lambda r: None if dv(r,"trend50") is None else dv(r,"trend50")>0),
          ("MOM_POS_AND_EMA_UP",lambda r: None if v(r,"momentum_3v15") is None or v(r,"ema9") is None or v(r,"ema21") is None else v(r,"momentum_3v15")>0 and v(r,"ema9")>v(r,"ema21")),
          ("EMA_UP_AND_RSI_RISING",lambda r: None if v(r,"ema9") is None or v(r,"ema21") is None or v(r,"rsi14") is None or v(r,"rsi14_prev") is None else v(r,"ema9")>v(r,"ema21") and v(r,"rsi14")>v(r,"rsi14_prev") and v(r,"rsi14")<D(70)),
          ("DAILY20_AND_CANDLE_MOM_POS",lambda r: None if dv(r,"trend20") is None or v(r,"momentum_3v15") is None else dv(r,"trend20")>0 and v(r,"momentum_3v15")>0),
        ]
        assessments=[assess(rows,n,p) for n,p in filters]
        return {"schema":"POROTA_RC6_POINT_IN_TIME_ENTRY_FEATURE_AUDIT_V1","read_only":True,
          "network_calls_performed":False,"broker_calls_performed":False,"safety":{"mode":mode,"real_orders_sent":int(orders or 0)},
          "coverage":{"closed_total":len(rows),"candle21":sum(int(r["candle"]["n"])>=21 for r in rows),
                      "daily20":sum(int(r["daily"]["n"])>=20 for r in rows),"daily50":sum(int(r["daily"]["n"])>=50 for r in rows)},
          "lookahead_protection":["candle.bar_end<=opened_at","candle.known_at<=opened_at","production_history.downloaded_at<=opened_at","daily_bar_date<=opened_local_date"],
          "assessments":assessments,"rows":rows}
    finally:c.close()

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--db",default="/app/data/paper_v17/observer_v17.db");ap.add_argument("--out")
    a=ap.parse_args();r=build(a.db);raw=json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True,default=str)
    if a.out:Path(a.out).write_text(raw+"\n",encoding="utf-8")
    print(raw)
if __name__=="__main__":main()
