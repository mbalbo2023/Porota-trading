#!/usr/bin/env python3
"""Point-in-time market breadth audit at each CLOSED PAPER entry.

Uses only PRODUCTION_PAPER market snapshots observed up to the original feature
timestamp on the same Buenos Aires market day. It mirrors the existing
FIRST_LAST_TRADE_SAMPLE_BREADTH_NOT_AN_INDEX method and current 70% bearish
threshold. Read-only; no network and no strategy mutation.
"""
from __future__ import annotations
import argparse,json,sqlite3
from datetime import datetime,time
from decimal import Decimal,InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

TZ=ZoneInfo("America/Argentina/Buenos_Aires");D=Decimal

def dec(v):
    try:
        x=D(str(v));return x if x.is_finite() else None
    except (InvalidOperation,TypeError,ValueError):return None

def local_start(as_of):
    x=datetime.fromisoformat(str(as_of).replace("Z","+00:00"))
    if x.tzinfo is None:raise ValueError("NAIVE_TIMESTAMP")
    x=x.astimezone(TZ)
    return datetime.combine(x.date(),time.min,TZ).isoformat()

def breadth(c,as_of,currency):
    start=local_start(as_of)
    sql="""WITH ranked AS (
      SELECT symbol,asset_class,currency,last,
        ROW_NUMBER() OVER (PARTITION BY symbol,asset_class,currency ORDER BY julianday(COALESCE(trade_at,observed_at)),id) first_rank,
        ROW_NUMBER() OVER (PARTITION BY symbol,asset_class,currency ORDER BY julianday(COALESCE(trade_at,observed_at)) DESC,id DESC) last_rank
      FROM market_snapshots
      WHERE last_kind='TRADE' AND CAST(last AS REAL)>0
        AND UPPER(asset_class) IN ('ACCIONES','CEDEARS')
        AND currency=?
        AND julianday(COALESCE(trade_at,observed_at))>=julianday(?)
        AND julianday(COALESCE(trade_at,observed_at))<=julianday(?)
    )
    SELECT symbol,asset_class,currency,
      MAX(CASE WHEN first_rank=1 THEN last END) first_price,
      MAX(CASE WHEN last_rank=1 THEN last END) last_price
    FROM ranked GROUP BY symbol,asset_class,currency"""
    rr=[dict(r) for r in c.execute(sql,(currency,start,as_of))]
    rising=falling=unchanged=0
    for r in rr:
        a=dec(r.get("first_price"));b=dec(r.get("last_price"))
        if a is None or b is None or a<=0:continue
        if b>a:rising+=1
        elif b<a:falling+=1
        else:unchanged+=1
    n=rising+falling+unchanged
    frac=D(falling)/D(n) if n else None
    return {"state":"INSUFFICIENT_SAMPLE" if n<4 else ("BEARISH_BREADTH" if frac>=D("0.70") else "MIXED_OR_POSITIVE"),
      "symbols":n,"rising":rising,"falling":falling,"unchanged":unchanged,
      "falling_fraction":str(frac) if frac is not None else None,
      "rising_fraction":str(D(rising)/D(n)) if n else None,
      "method":"FIRST_LAST_TRADE_SAMPLE_BREADTH_NOT_AN_INDEX","threshold":"0.70"}

def metrics(rows):
    nets=[dec(r["net_pnl"]) or D(0) for r in rows];w=sum(x>0 for x in nets);l=sum(x<0 for x in nets)
    return {"n":len(rows),"wins":w,"losses":l,"win_rate_pct":str(D(w)*100/D(len(rows)) if rows else D(0)),
            "net_total":str(sum(nets,D(0)))}

def auc(rows):
    vals=[(dec(r["breadth"].get("rising_fraction")),1 if (dec(r["net_pnl"]) or D(0))>0 else 0) for r in rows]
    pos=[float(v) for v,y in vals if v is not None and y];neg=[float(v) for v,y in vals if v is not None and not y]
    if not pos or not neg:return None
    s=0
    for p in pos:
        for n in neg:s+=1 if p>n else .5 if p==n else 0
    return s/(len(pos)*len(neg))

def build(db):
    c=sqlite3.connect(f"file:{Path(db)}?mode=ro",uri=True,timeout=20);c.row_factory=sqlite3.Row;c.execute("PRAGMA query_only=ON")
    try:
        mode,orders=c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if mode!="PRODUCTION_PAPER" or int(orders or 0)!=0:raise RuntimeError("SAFETY_STATE_INVALID")
        ps=[dict(r) for r in c.execute("""SELECT p.paper_id,p.symbol,p.asset_class,p.currency,p.opened_at,p.net_pnl,l.feature_timestamp
          FROM paper_positions p LEFT JOIN paper_learning_samples l USING(paper_id)
          WHERE p.status='CLOSED' ORDER BY julianday(p.opened_at),p.paper_id""")]
        out=[]
        for p in ps:
            as_of=p.get("feature_timestamp") or p["opened_at"]
            out.append({**p,"as_of":as_of,"breadth":breadth(c,as_of,p["currency"])})
        covered=[r for r in out if r["breadth"]["state"]!="INSUFFICIENT_SAMPLE"]
        bearish=[r for r in covered if r["breadth"]["state"]=="BEARISH_BREADTH"]
        other=[r for r in covered if r["breadth"]["state"]=="MIXED_OR_POSITIVE"]
        bycur={}
        for cur in sorted({r["currency"] for r in covered}):
            rr=[r for r in covered if r["currency"]==cur]
            bycur[cur]={"all":metrics(rr),"bearish":metrics([x for x in rr if x["breadth"]["state"]=="BEARISH_BREADTH"]),
                        "mixed_or_positive":metrics([x for x in rr if x["breadth"]["state"]=="MIXED_OR_POSITIVE"]),
                        "auc_rising_fraction":auc(rr)}
        return {"schema":"POROTA_RC6_ENTRY_BREADTH_AUDIT_V1","read_only":True,"network_calls_performed":False,
          "broker_calls_performed":False,"safety":{"mode":mode,"real_orders_sent":int(orders or 0)},
          "coverage":{"closed_total":len(ps),"covered":len(covered),"insufficient":len(ps)-len(covered)},
          "policy_reference":{"bearish_falling_fraction":"0.70","binding":False,"current_policy":"ALERT_ONLY"},
          "all":{"bearish":metrics(bearish),"mixed_or_positive":metrics(other)},"by_currency":bycur,"rows":out,
          "limitations":["Breadth is not an index; it is first-vs-last observed trade breadth up to entry time.",
                         "This evaluates factual opened trades only and cannot assign realized outcomes to blocked/HOLD candidates."]}
    finally:c.close()

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--db",default="/app/data/paper_v17/observer_v17.db");ap.add_argument("--out")
    a=ap.parse_args();r=build(a.db);raw=json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)
    if a.out:Path(a.out).write_text(raw+"\n",encoding="utf-8")
    print(raw)
if __name__=="__main__":main()
