#!/usr/bin/env python3
"""Point-in-time 5m candle diagnostics for factual closed RC6 entries.

Read-only. For each closed trade, selects the latest candle version known at
the original feature timestamp, one version per bar, with bar_end <= as_of.
No future-known candle and no duplicate version can enter the feature set.
"""
from __future__ import annotations
import argparse,json,math,sqlite3,statistics
from collections import defaultdict
from decimal import Decimal,InvalidOperation
from pathlib import Path

D=Decimal

def dec(v):
    try:
        x=D(str(v));return x if x.is_finite() else None
    except (InvalidOperation,TypeError,ValueError):return None

def js(v):
    try:
        x=json.loads(v or "{}");return x if isinstance(x,dict) else {}
    except Exception:return {}

def auc(rows,key):
    vals=[(r.get(key),1 if (dec(r.get("net_pnl")) or D(0))>0 else 0) for r in rows if r.get(key) is not None]
    pos=[float(v) for v,y in vals if y]; neg=[float(v) for v,y in vals if not y]
    if not pos or not neg:return None
    score=0.0
    for p in pos:
        for n in neg:
            score += 1 if p>n else .5 if p==n else 0
    return score/(len(pos)*len(neg))

def metrics(rows):
    net=[dec(r.get("net_pnl")) or D(0) for r in rows];wins=sum(x>0 for x in net);losses=sum(x<0 for x in net)
    wp=sum(x for x in net if x>0);lp=-sum(x for x in net if x<0)
    return {"n":len(rows),"wins":wins,"losses":losses,
      "win_rate_pct":str(D(wins)*100/D(len(rows)) if rows else D(0)),
      "net_total":str(sum(net,D(0))),"profit_factor":str(wp/lp) if lp else None}

def candle_rows(c,p,as_of):
    sql="""WITH v0 AS (
      SELECT v.bar_start,v.bar_end,v.known_at,v.body_json,
             ROW_NUMBER() OVER (
               PARTITION BY v.series_id,v.bar_start
               ORDER BY julianday(v.known_at) DESC, v.body_json DESC
             ) AS rn
      FROM candle_versions v
      JOIN candle_series s ON s.series_id=v.series_id
      WHERE json_extract(s.identity_json,'$.symbol')=?
        AND json_extract(s.identity_json,'$.asset_class')=?
        AND json_extract(s.identity_json,'$.market')=?
        AND json_extract(s.identity_json,'$.currency')=?
        AND json_extract(s.identity_json,'$.settlement')=?
        AND json_extract(s.identity_json,'$.resolution')='5m'
        AND v.bar_end<=? AND v.known_at<=?
    )
    SELECT bar_start,bar_end,known_at,body_json FROM v0 WHERE rn=1
    ORDER BY bar_start DESC LIMIT 60"""
    raw=[dict(r) for r in c.execute(sql,(p["symbol"],p["asset_class"],p["market"],p["currency"],p["settlement"],as_of,as_of))]
    out=[]
    for r in reversed(raw):
        body=js(r.get("body_json"))
        if body.get("quality") not in {"COMPLETE","SAMPLED"} or body.get("synthetic"):continue
        close=dec(body.get("close"));high=dec(body.get("high"));low=dec(body.get("low"))
        if close is None or high is None or low is None or low<=0 or not (low<=close<=high):continue
        out.append({"bar_start":r["bar_start"],"bar_end":r["bar_end"],"known_at":r["known_at"],
                    "close":close,"range":high/low-D(1)})
    return out

def mean(xs):return sum(xs,D(0))/D(len(xs)) if xs else None
def trend(closes,n):
    if len(closes)<n:return None
    a=closes[-n];b=closes[-1]
    return b/a-D(1) if a else None

def features(bars):
    closes=[x["close"] for x in bars]; ranges=[x["range"] for x in bars]
    s=mean(closes[-3:]) if len(closes)>=3 else None
    l=mean(closes[-15:]) if len(closes)>=15 else None
    mom=s/l-D(1) if s and l else None
    return {"bars":len(bars),"momentum_3v15":str(mom) if mom is not None else None,
      "trend_15":str(trend(closes,15)) if trend(closes,15) is not None else None,
      "trend_30":str(trend(closes,30)) if trend(closes,30) is not None else None,
      "trend_60":str(trend(closes,60)) if trend(closes,60) is not None else None,
      "avg_range_5m":str(mean(ranges)) if ranges else None,
      "last_bar_end":bars[-1]["bar_end"] if bars else None,
      "last_known_at":bars[-1]["known_at"] if bars else None}

def build(db):
    c=sqlite3.connect(f"file:{Path(db)}?mode=ro",uri=True,timeout=20);c.row_factory=sqlite3.Row;c.execute("PRAGMA query_only=ON")
    try:
        mode,orders=c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if mode!="PRODUCTION_PAPER" or int(orders or 0)!=0:raise RuntimeError("SAFETY_STATE_INVALID")
        ps=[dict(r) for r in c.execute("""SELECT p.*,l.feature_timestamp
          FROM paper_positions p LEFT JOIN paper_learning_samples l USING(paper_id)
          WHERE p.status='CLOSED' ORDER BY julianday(p.opened_at),p.paper_id""")]
        out=[]
        for p in ps:
            as_of=p.get("feature_timestamp") or p.get("opened_at")
            try:bars=candle_rows(c,p,as_of);f=features(bars)
            except sqlite3.OperationalError as exc:
                f={"bars":0,"error":type(exc).__name__}
            out.append({"paper_id":p["paper_id"],"symbol":p["symbol"],"asset_class":p["asset_class"],
              "currency":p["currency"],"strategy_version":p["strategy_version"],"as_of":as_of,
              "net_pnl":p.get("net_pnl"),"outcome":"WIN" if (dec(p.get("net_pnl")) or D(0))>0 else "LOSS",
              **f})
        ready=[r for r in out if r.get("momentum_3v15") is not None]
        def pass_profile(r,name):
            m=dec(r.get("momentum_3v15"));t15=dec(r.get("trend_15"));t30=dec(r.get("trend_30"))
            if name=="MOM_POS":return m is not None and m>0
            if name=="TREND15_POS":return t15 is not None and t15>0
            if name=="MOM_AND_T15":return m is not None and t15 is not None and m>0 and t15>0
            if name=="MOM_T15_T30":return m is not None and t15 is not None and t30 is not None and m>0 and t15>0 and t30>0
            return False
        profiles={}
        for name in ("MOM_POS","TREND15_POS","MOM_AND_T15","MOM_T15_T30"):
            evaluated=[r for r in ready if name!="MOM_T15_T30" or r.get("trend_30") is not None]
            passed=[r for r in evaluated if pass_profile(r,name)];blocked=[r for r in evaluated if not pass_profile(r,name)]
            profiles[name]={"evaluated":len(evaluated),"passed":metrics(passed),"blocked":metrics(blocked),
                            "blocked_winners":sum(r["outcome"]=="WIN" for r in blocked)}
        bycur={}
        for cur in sorted({r["currency"] for r in ready}):
            rr=[r for r in ready if r["currency"]==cur]
            bycur[cur]={"metrics":metrics(rr),"auc_momentum_3v15":auc(rr,"momentum_3v15"),
                        "auc_trend_15":auc(rr,"trend_15"),"auc_trend_30":auc(rr,"trend_30")}
        return {"schema":"POROTA_RC6_CANDLE_ENTRY_DIAGNOSTIC_V1","read_only":True,
          "network_calls_performed":False,"broker_calls_performed":False,
          "safety":{"mode":mode,"real_orders_sent":int(orders or 0)},
          "coverage":{"closed_total":len(ps),"momentum_ready":len(ready),
                      "trend30_ready":sum(r.get("trend_30") is not None for r in out),
                      "trend60_ready":sum(r.get("trend_60") is not None for r in out)},
          "point_in_time_rule":"one latest version per bar; bar_end<=as_of AND known_at<=as_of",
          "by_currency":bycur,"profiles":profiles,"rows":out,
          "limitations":["Profiles filter only factual opened trades; they do not assign outcomes to historical HOLD decisions.",
                         "No production_history payload is used; only versioned candles known at decision time are admitted.",
                         "Positive/negative sign profiles are diagnostics, not proposed production thresholds."]}
    finally:c.close()

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--db",default="/app/data/paper_v17/observer_v17.db");ap.add_argument("--out")
    a=ap.parse_args();r=build(a.db);raw=json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)
    if a.out:Path(a.out).write_text(raw+"\n",encoding="utf-8")
    print(raw)
if __name__=="__main__":main()
