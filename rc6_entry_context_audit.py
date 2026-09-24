#!/usr/bin/env python3
"""Point-in-time PPI microstructure and breadth audit for closed PAPER entries."""
from __future__ import annotations
import argparse,json,sqlite3
from datetime import datetime,timedelta
from decimal import Decimal,InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

TZ=ZoneInfo("America/Argentina/Buenos_Aires");D=Decimal
def dec(v):
    try:x=D(str(v));return x if x.is_finite() else None
    except (InvalidOperation,TypeError,ValueError):return None
def dt(v):
    x=datetime.fromisoformat(str(v).replace("Z","+00:00"))
    if x.tzinfo is None:raise ValueError("NAIVE_TIMESTAMP")
    return x.astimezone(TZ)
def metric(rows):
    by={}
    for cur in sorted({r["currency"] for r in rows}):
        g=[r for r in rows if r["currency"]==cur]
        by[cur]={"n":len(g),"wins":sum(r["outcome"]=="WIN" for r in g),"losses":sum(r["outcome"]=="LOSS" for r in g),
                 "net_total":str(sum((dec(r["net_pnl"]) or D(0) for r in g),D(0)))}
    return {"n":len(rows),"wins":sum(r["outcome"]=="WIN" for r in rows),"losses":sum(r["outcome"]=="LOSS" for r in rows),"by_currency":by}
def assess(rows,name,fn):
    cov=[r for r in rows if fn(r) is not None];keep=[r for r in cov if fn(r)];block=[r for r in cov if not fn(r)]
    days=sorted({r["day"] for r in cov});cut=max(1,len(days)//2);a=set(days[:cut]);b=set(days[cut:])
    return {"name":name,"coverage":len(cov),"kept":metric(keep),"blocked":metric(block),
            "winners_blocked":sum(r["outcome"]=="WIN" for r in block),"losers_blocked":sum(r["outcome"]=="LOSS" for r in block),
            "first_half":metric([r for r in keep if r["day"] in a]),"second_half":metric([r for r in keep if r["day"] in b])}
def latest_book(c,p):
    r=c.execute("""SELECT observed_at,book_at,bid,ask,bid_size,ask_size,last,last_kind,source
      FROM market_snapshots WHERE symbol=? AND asset_class=? AND settlement=? AND currency=? AND market=?
        AND julianday(observed_at)<=julianday(?) ORDER BY julianday(observed_at) DESC,id DESC LIMIT 1""",
      (p["symbol"],p["asset_class"],p["settlement"],p["currency"],p["market"],p["opened_at"])).fetchone()
    if not r:return {"state":"UNAVAILABLE"}
    x=dict(r);bid=dec(x.get("bid"));ask=dec(x.get("ask"));bs=dec(x.get("bid_size"));a_s=dec(x.get("ask_size"))
    age=(dt(p["opened_at"])-dt(x["observed_at"])).total_seconds()
    denom=(bs or D(0))+(a_s or D(0))
    return {"state":"READY","observed_at":x["observed_at"],"age_seconds":age,
      "bid":str(bid) if bid is not None else None,"ask":str(ask) if ask is not None else None,
      "bid_size":str(bs) if bs is not None else None,"ask_size":str(a_s) if a_s is not None else None,
      "spread":str(ask/bid-D(1)) if bid and ask and bid>0 else None,
      "book_imbalance":str((bs-a_s)/denom) if bs is not None and a_s is not None and denom>0 else None}
def breadth(c,p):
    opened=dt(p["opened_at"]);start=datetime.combine(opened.date(),datetime.min.time(),TZ)
    q="""WITH ranked AS (
      SELECT symbol,last,
       ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY julianday(COALESCE(trade_at,observed_at)),id) fr,
       ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY julianday(COALESCE(trade_at,observed_at)) DESC,id DESC) lr
      FROM market_snapshots
      WHERE market=? AND currency=? AND last_kind='TRADE' AND CAST(last AS REAL)>0
        AND julianday(COALESCE(trade_at,observed_at))>=julianday(?)
        AND julianday(COALESCE(trade_at,observed_at))<=julianday(?)
    ) SELECT symbol,MAX(CASE WHEN fr=1 THEN last END) first_price,
      MAX(CASE WHEN lr=1 THEN last END) last_price FROM ranked GROUP BY symbol"""
    rr=list(c.execute(q,(p["market"],p["currency"],start.isoformat(),p["opened_at"])))
    rising=falling=unchanged=0
    for r in rr:
        a=dec(r["first_price"]);b=dec(r["last_price"])
        if not a or not b:continue
        if b>a:rising+=1
        elif b<a:falling+=1
        else:unchanged+=1
    n=rising+falling+unchanged
    return {"state":"READY" if n>=4 else "INSUFFICIENT","symbols":n,"rising":rising,"falling":falling,"unchanged":unchanged,
            "breadth_score":str(D(rising-falling)/D(n)) if n else None}
def asset_day_return(c,p):
    opened=dt(p["opened_at"]);start=datetime.combine(opened.date(),datetime.min.time(),TZ)
    rr=list(c.execute("""SELECT last FROM market_snapshots WHERE symbol=? AND asset_class=? AND settlement=? AND currency=? AND market=?
      AND last_kind='TRADE' AND CAST(last AS REAL)>0 AND julianday(COALESCE(trade_at,observed_at))>=julianday(?)
      AND julianday(COALESCE(trade_at,observed_at))<=julianday(?) ORDER BY julianday(COALESCE(trade_at,observed_at)),id""",
      (p["symbol"],p["asset_class"],p["settlement"],p["currency"],p["market"],start.isoformat(),p["opened_at"])))
    if len(rr)<2:return None
    a=dec(rr[0]["last"]);b=dec(rr[-1]["last"]);return b/a-D(1) if a and b else None
def build(db):
    c=sqlite3.connect(f"file:{Path(db)}?mode=ro",uri=True,timeout=20);c.row_factory=sqlite3.Row;c.execute("PRAGMA query_only=ON")
    try:
        mode,orders=c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if mode!="PRODUCTION_PAPER" or int(orders or 0)!=0:raise RuntimeError("SAFETY_STATE_INVALID")
        ps=[dict(r) for r in c.execute("SELECT * FROM paper_positions WHERE status='CLOSED' ORDER BY julianday(opened_at),paper_id")]
        out=[]
        for p in ps:
            net=dec(p.get("net_pnl"));book=latest_book(c,p);br=breadth(c,p);adr=asset_day_return(c,p)
            out.append({"paper_id":p["paper_id"],"symbol":p["symbol"],"currency":p["currency"],"day":dt(p["opened_at"]).date().isoformat(),
              "opened_at":p["opened_at"],"net_pnl":p.get("net_pnl"),"outcome":"WIN" if net and net>0 else "LOSS",
              "book":book,"breadth":br,"asset_day_return":str(adr) if adr is not None else None})
        def x(r,k):
            try:return dec(r["book"].get(k))
            except:return None
        def bs(r):
            try:return dec(r["breadth"].get("breadth_score"))
            except:return None
        def ar(r):return dec(r.get("asset_day_return"))
        filters=[
          ("BOOK_IMBALANCE_POS",lambda r:None if x(r,"book_imbalance") is None else x(r,"book_imbalance")>0),
          ("BOOK_IMBALANCE_NONNEG",lambda r:None if x(r,"book_imbalance") is None else x(r,"book_imbalance")>=0),
          ("BREADTH_POS",lambda r:None if bs(r) is None else bs(r)>0),
          ("BREADTH_NONNEG",lambda r:None if bs(r) is None else bs(r)>=0),
          ("ASSET_DAY_RETURN_POS",lambda r:None if ar(r) is None else ar(r)>0),
          ("BREADTH_POS_AND_ASSET_POS",lambda r:None if bs(r) is None or ar(r) is None else bs(r)>0 and ar(r)>0),
        ]
        return {"schema":"POROTA_RC6_ENTRY_CONTEXT_AUDIT_V1","read_only":True,"network_calls_performed":False,"broker_calls_performed":False,
          "safety":{"mode":mode,"real_orders_sent":int(orders or 0)},
          "coverage":{"closed_total":len(out),"fresh_book_120s":sum(r["book"].get("state")=="READY" and r["book"].get("age_seconds",999)>-1 and r["book"].get("age_seconds",999)<=120 for r in out),
                      "book_imbalance":sum(x(r,"book_imbalance") is not None for r in out),"breadth":sum(bs(r) is not None for r in out),
                      "asset_day_return":sum(ar(r) is not None for r in out)},
          "assessments":[assess(out,n,f) for n,f in filters],"rows":out,
          "limitations":["Latest book must be at or before opened_at; no future quote is used.",
                         "Breadth uses only trade snapshots at or before opened_at in the same market/currency/day.",
                         "This evaluates factual opened trades only and does not label historical HOLD decisions."]}
    finally:c.close()
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--db",default="/app/data/paper_v17/observer_v17.db");a=ap.parse_args()
    print(json.dumps(build(a.db),ensure_ascii=False,indent=2,sort_keys=True,default=str))
if __name__=="__main__":main()
