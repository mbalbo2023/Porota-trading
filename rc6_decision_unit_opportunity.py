#!/usr/bin/env python3
"""Decision-level unit-opportunity study for recent RC6 evidence.

Labels are market-opportunity observations, NOT hypothetical portfolio trades.
Entry uses the contemporaneous ASK plus factual slippage. Future exits use
observed BID with positive displayed depth and exit slippage. Costs use the
canonical RC6 fee schedule plus the modeled intraday smaller-leg rebate.
Features are strictly point-in-time; future data is used only for labels.
"""
from __future__ import annotations
import argparse,bisect,hashlib,json,math,sqlite3,statistics
from collections import Counter,defaultdict
from datetime import datetime,timedelta,time
from decimal import Decimal,InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

D=Decimal
TZ=ZoneInfo("America/Argentina/Buenos_Aires")
SLIPPAGE=D("0.0002")
STOP=D("0.02")
TARGETS=(D("0.0025"),D("0.005"),D("0.0075"),D("0.01"))
IDENTITY=("symbol","asset_class","settlement","currency","market")

def dec(v):
    try:
        x=D(str(v));return x if x.is_finite() else None
    except (InvalidOperation,TypeError,ValueError):return None
def safe(v):
    try:
        x=json.loads(v or "{}");return x if isinstance(x,dict) else {}
    except Exception:return {}
def canon(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def hsh(x):return hashlib.sha256(canon(x).encode()).hexdigest()
def aware(v):
    try:
        x=datetime.fromisoformat(str(v).replace("Z","+00:00"))
        return x if x.tzinfo is not None else None
    except Exception:return None
def local(v):
    x=aware(v);return x.astimezone(TZ) if x else None
def nstr(x):return str(x) if x is not None else None

def snapshot_index(c):
    idx=defaultdict(list)
    for r in c.execute("""SELECT id,observed_at,symbol,asset_class,settlement,currency,market,bid,bid_size
      FROM market_snapshots
      ORDER BY symbol,asset_class,settlement,currency,market,julianday(observed_at),id"""):
        at=aware(r["observed_at"]);bid=dec(r["bid"]);size=dec(r["bid_size"])
        if at is None:continue
        key=tuple(str(r[k]) for k in IDENTITY)
        idx[key].append((at,bid,size))
    return {k:([x[0] for x in v],v) for k,v in idx.items()}

def path(idx,q,at):
    key=tuple(str(q[k]) for k in IDENTITY);pair=idx.get(key)
    if not pair:return []
    loc=at.astimezone(TZ)
    eod=datetime.combine(loc.date(),time(16,50),TZ).astimezone(at.tzinfo)
    end=min(at+timedelta(minutes=120),eod)
    if end<=at:return []
    times,series=pair
    lo=bisect.bisect_right(times,at);hi=bisect.bisect_right(times,end)
    return [(t,b,s) for t,b,s in series[lo:hi] if b is not None and b>0 and s is not None and s>0]

def rates(asset):
    import au_fee_schedule
    full=D(str(au_fee_schedule.costo_por_tramo(asset)))
    low=D(str(au_fee_schedule.costo_por_tramo_bonificado(asset)))
    return full,low

def net_return(entry,exit_fill,full,low):
    # Full entry cost; intraday rebate is applied on the smaller notional leg.
    entry_cost=entry*full
    exit_cost=max(D(0),exit_fill*full-min(entry,exit_fill)*(full-low))
    return (exit_fill-entry-entry_cost-exit_cost)/entry

def auc(rows,feature,label):
    pairs=[]
    for r in rows:
        x=dec(r["features"].get(feature));y=r["labels"].get(label)
        if x is not None and y in (0,1):pairs.append((x,y))
    pos=[x for x,y in pairs if y];neg=[x for x,y in pairs if not y]
    if not pos or not neg:return None
    s=D(0)
    for p in pos:
        for n in neg:s+=D(1) if p>n else D("0.5") if p==n else D(0)
    return str(s/D(len(pos)*len(neg)))

def label_metrics(rows,label):
    rr=[r for r in rows if r["labels"].get(label) in (0,1)]
    y=sum(r["labels"][label] for r in rr)
    return {"n":len(rr),"positive":y,"positive_rate_pct":str(D(y)*100/D(len(rr)) if rr else D(0))}

def features(d,q):
    f=safe(d.get("features_json"))
    shadow=f.get("historical_candle_shadow") if isinstance(f.get("historical_candle_shadow"),dict) else {}
    hist=shadow.get("history") if isinstance(shadow.get("history"),dict) else {}
    candle=shadow.get("candles_5m") if isinstance(shadow.get("candles_5m"),dict) else {}
    bid,ask,bs,a_s=map(dec,(q.get("bid"),q.get("ask"),q.get("bid_size"),q.get("ask_size")))
    denom=(bs or D(0))+(a_s or D(0))
    loc=local(q.get("observed_at") or d.get("decided_at"))
    return {
      "score":nstr(dec(d.get("score"))),
      "momentum":nstr(dec(f.get("momentum"))),
      "spread":nstr(dec(f.get("spread"))),
      "samples":nstr(dec(f.get("samples"))),
      "book_imbalance":nstr((bs-a_s)/denom) if bs is not None and a_s is not None and denom>0 else None,
      "history_trend20":nstr(dec(hist.get("trend_20"))),
      "history_trend50":nstr(dec(hist.get("trend_50"))),
      "history_avg_range":nstr(dec(hist.get("avg_range"))),
      "history_avg_abs_return":nstr(dec(hist.get("avg_abs_return"))),
      "candle_momentum_3v15":nstr(dec(candle.get("momentum_3v15"))),
      "candle_avg_range_5m":nstr(dec(candle.get("avg_range_5m"))),
      "shadow_score_delta":nstr(dec(shadow.get("shadow_score_delta"))),
      "minute_of_day":str(loc.hour*60+loc.minute) if loc else None,
    }

def build(db):
    c=sqlite3.connect(f"file:{Path(db)}?mode=ro",uri=True,timeout=20);c.row_factory=sqlite3.Row;c.execute("PRAGMA query_only=ON")
    try:
        mode,orders=c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if mode!="PRODUCTION_PAPER" or int(orders or 0)!=0:raise RuntimeError("SAFETY_STATE_INVALID")
        decisions={str(r["decision_key"]):dict(r) for r in c.execute("SELECT * FROM paper_decisions")}
        ev=[]
        for r in c.execute("SELECT * FROM decision_evidence_snapshots ORDER BY julianday(captured_at),decision_key"):
            x=dict(r);p=safe(x.get("payload_json"))
            if not p or hsh(p)!=str(x.get("payload_sha256") or ""):continue
            ev.append((x,p))
        idx=snapshot_index(c)
        rows=[];reasons=Counter()
        for er,p in ev:
            dk=str(er["decision_key"]);d=decisions.get(dk)
            if not d:reasons["DECISION_MISSING"]+=1;continue
            q=p.get("quote_used") if isinstance(p.get("quote_used"),dict) else {}
            f=safe(d.get("features_json"))
            if not isinstance(f.get("candidate"),dict) or not isinstance(f.get("hard_safety"),dict):
                reasons["NO_FROZEN_CANDIDATE_FEATURES"]+=1;continue
            if any(q.get(k) in (None,"","UNKNOWN") for k in IDENTITY):
                reasons["IDENTITY_INCOMPLETE"]+=1;continue
            bid,ask,bs,a_s=map(dec,(q.get("bid"),q.get("ask"),q.get("bid_size"),q.get("ask_size")))
            at=aware(q.get("observed_at") or d.get("decided_at"))
            if at is None or bid is None or ask is None or bs is None or a_s is None or bid<=0 or ask<bid or a_s<=0:
                reasons["ENTRY_BOOK_INCOMPLETE"]+=1;continue
            obs=path(idx,q,at)
            if not obs:reasons["NO_FUTURE_POSITIVE_DEPTH_BID"]+=1;continue
            try:full,low=rates(q["asset_class"])
            except Exception:
                reasons["FEE_MODEL_UNAVAILABLE"]+=1;continue
            entry=ask*(D(1)+SLIPPAGE)
            stop_price=entry*(D(1)-STOP)
            max_net=None;min_net=None;stop_at=None
            target_any={str(t):None for t in TARGETS}
            target_before_stop={str(t):None for t in TARGETS}
            for stamp,bid_f,bid_size in obs:
                exit_fill=bid_f*(D(1)-SLIPPAGE)
                nr=net_return(entry,exit_fill,full,low)
                max_net=nr if max_net is None or nr>max_net else max_net
                min_net=nr if min_net is None or nr<min_net else min_net
                if stop_at is None and bid_f<=stop_price:stop_at=stamp
                for t in TARGETS:
                    k=str(t)
                    if target_any[k] is None and nr>=t:target_any[k]=stamp
            labels={}
            timings={}
            for t in TARGETS:
                k=str(t);hit=target_any[k]
                labels["hit_net_"+k]=1 if hit else 0
                labels["hit_net_"+k+"_before_stop"]=1 if hit and (stop_at is None or hit<stop_at) else 0
                timings["hit_net_"+k+"_minutes"]=str(D(str((hit-at).total_seconds()/60))) if hit else None
            loc=at.astimezone(TZ)
            rows.append({"decision_key":dk,"decided_at":d.get("decided_at"),"day":loc.date().isoformat(),
              "symbol":d.get("symbol"),"action":str(d.get("action") or "UNKNOWN").upper(),
              "currency":q.get("currency"),"asset_class":q.get("asset_class"),
              "features":features(d,q),"labels":labels,"timings":timings,
              "max_net_return_120m":nstr(max_net),"min_net_return_120m":nstr(min_net),
              "stop2_hit":stop_at is not None,
              "observations":len(obs)})
        days=sorted({r["day"] for r in rows})
        train_days=days[:-1] if len(days)>=2 else days
        validation_days=days[-1:] if len(days)>=2 else []
        train=[r for r in rows if r["day"] in train_days]
        val=[r for r in rows if r["day"] in validation_days]
        feats=("score","momentum","spread","samples","book_imbalance","history_trend20","history_trend50",
               "history_avg_range","history_avg_abs_return","candle_momentum_3v15","candle_avg_range_5m",
               "shadow_score_delta","minute_of_day")
        analysis={}
        for t in TARGETS:
            label="hit_net_"+str(t)+"_before_stop"
            analysis[str(t)]={
              "all":label_metrics(rows,label),"train":label_metrics(train,label),"validation":label_metrics(val,label),
              "auc_all":{x:auc(rows,x,label) for x in feats},
              "auc_train":{x:auc(train,x,label) for x in feats},
              "auc_validation":{x:auc(val,x,label) for x in feats},
              "by_action":{
                a:{"all":label_metrics([r for r in rows if r["action"]==a],label),
                   "train":label_metrics([r for r in train if r["action"]==a],label),
                   "validation":label_metrics([r for r in val if r["action"]==a],label)}
                for a in sorted({r["action"] for r in rows})}
            }
        feature_coverage={x:sum(dec(r["features"].get(x)) is not None for r in rows) for x in feats}
        return {"schema":"POROTA_RC6_DECISION_UNIT_OPPORTUNITY_V1","read_only":True,
          "network_calls_performed":False,"broker_calls_performed":False,
          "hypothetical_portfolio_trades":False,"unit_opportunity_labels":True,
          "safety":{"mode":mode,"real_orders_sent":int(orders or 0)},
          "method":{"entry":"observed ASK * (1+0.0002)","exit":"future observed BID with positive displayed depth * (1-0.0002)",
                    "horizon":"min(120 minutes, 16:50 America/Argentina/Buenos_Aires)",
                    "costs":"canonical RC6 au_fee_schedule; full entry leg plus intraday smaller-leg rebate",
                    "stop_control":"2% below modeled entry; target-before-stop label",
                    "meaning":"unit market opportunity only; no capital/sizing/portfolio contention claim"},
          "coverage":{"evidence_snapshots_valid":len(ev),"labeled_rows":len(rows),
                      "actions":dict(Counter(r["action"] for r in rows)),"days":days,
                      "feature_nonmissing":feature_coverage,"excluded":dict(reasons)},
          "temporal_split":{"train_days":train_days,"validation_days":validation_days},
          "analysis":analysis,"rows":rows,
          "limitations":[
            "Displayed positive depth proves only a unit-sized observable exit, not that a factual portfolio-sized order could fill.",
            "Capital, concurrent positions, sector caps and shared-liquidity contention are intentionally not modeled.",
            "Only recent decisions with immutable evidence are labeled; older decisions remain excluded.",
            "Future data appears only in labels, never in point-in-time entry features.",
            "Results across ARS and USD_MEP must not be summed monetarily."
          ]}
    finally:c.close()

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--db",default="/app/data/paper_v17/observer_v17.db");ap.add_argument("--out")
    a=ap.parse_args();r=build(a.db);raw=json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)
    if a.out:Path(a.out).write_text(raw+"\n",encoding="utf-8")
    print(raw)
if __name__=="__main__":main()
