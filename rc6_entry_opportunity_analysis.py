#!/usr/bin/env python3
"""Relate point-in-time entry features to cost-aware executable opportunity labels.

Consumes artifacts produced by the same read-only audit run. It does not query
SQLite, network or broker. Labels are positive only when the rigorous
counterfactual found a full-depth executable exit before the factual exit
intent whose modeled NET return reached the requested floor.
"""
from __future__ import annotations
import argparse,json,math
from collections import defaultdict
from decimal import Decimal,InvalidOperation
from pathlib import Path

D=Decimal
NET_KEYS=("0.0025","0.005","0.0075","0.01")

def dec(v):
    try:
        x=D(str(v)); return x if x.is_finite() else None
    except (InvalidOperation,TypeError,ValueError): return None

def auc(rows,feature,label):
    pairs=[(dec(r.get(feature)),int(r.get(label,0))) for r in rows]
    pairs=[(x,y) for x,y in pairs if x is not None]
    pos=[x for x,y in pairs if y]; neg=[x for x,y in pairs if not y]
    if not pos or not neg:return None
    s=D(0)
    for p in pos:
        for n in neg:s += D(1) if p>n else D("0.5") if p==n else D(0)
    return str(s/D(len(pos)*len(neg)))

def metric(rows,label):
    n=len(rows); y=sum(int(r.get(label,0)) for r in rows)
    pnl=sum((dec(r.get("net_pnl")) or D(0) for r in rows),D(0))
    return {"n":n,"opportunities":y,"opportunity_rate_pct":str(D(y)*100/D(n) if n else D(0)),
            "factual_net_total":str(pnl)}

def profile(rows,label,name,pred):
    covered=[r for r in rows if pred(r) is not None]
    kept=[r for r in covered if pred(r) is True]
    blocked=[r for r in covered if pred(r) is False]
    days=sorted({r["day"] for r in covered}); cut=max(1,len(days)//2); first=set(days[:cut]); second=set(days[cut:])
    positives=sum(int(r.get(label,0)) for r in covered)
    kept_pos=sum(int(r.get(label,0)) for r in kept)
    return {"name":name,"coverage":len(covered),"kept":metric(kept,label),"blocked":metric(blocked,label),
            "opportunity_recall_pct":str(D(kept_pos)*100/D(positives) if positives else D(0)),
            "first_half":metric([r for r in kept if r["day"] in first],label),
            "second_half":metric([r for r in kept if r["day"] in second],label)}

def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def build(features_path,context_path,exit_path):
    f=load(features_path); c=load(context_path); e=load(exit_path)
    cx={str(r["paper_id"]):r for r in c.get("rows",[])}
    labels={}
    for key in NET_KEYS:
        labels[key]={str(r["paper_id"]) for r in e.get("net_targets",{}).get(key,{}).get("changed",[])}
    rows=[]
    for r in f.get("rows",[]):
        pid=str(r["paper_id"]); candle=r.get("candle") or {}; ctx=cx.get(pid,{})
        book=ctx.get("book") if isinstance(ctx.get("book"),dict) else {}
        breadth=ctx.get("breadth") if isinstance(ctx.get("breadth"),dict) else {}
        ema9=dec(candle.get("ema9"));ema21=dec(candle.get("ema21"))
        row={"paper_id":pid,"day":r.get("day"),"currency":r.get("currency"),"symbol":r.get("symbol"),
             "net_pnl":r.get("net_pnl"),
             "momentum_3v15":candle.get("momentum_3v15"),
             "ema_gap":str(ema9/ema21-D(1)) if ema9 is not None and ema21 not in (None,D(0)) else None,
             "rsi14":candle.get("rsi14"),
             "rsi_delta":str(dec(candle.get("rsi14"))-dec(candle.get("rsi14_prev")))
                 if dec(candle.get("rsi14")) is not None and dec(candle.get("rsi14_prev")) is not None else None,
             "avg_range":candle.get("avg_range"),
             "book_imbalance":book.get("book_imbalance"),
             "breadth_score":breadth.get("breadth_score"),
             "asset_day_return":ctx.get("asset_day_return")}
        for key in NET_KEYS: row["opp_"+key]=1 if pid in labels[key] else 0
        rows.append(row)

    def v(r,k):return dec(r.get(k))
    predicates=[
      ("ASSET_NONPOS",lambda r:None if v(r,"asset_day_return") is None else v(r,"asset_day_return")<=0),
      ("BREADTH_NONPOS",lambda r:None if v(r,"breadth_score") is None else v(r,"breadth_score")<=0),
      ("MOMENTUM_NONPOS",lambda r:None if v(r,"momentum_3v15") is None else v(r,"momentum_3v15")<=0),
      ("EMA_GAP_NONPOS",lambda r:None if v(r,"ema_gap") is None else v(r,"ema_gap")<=0),
      ("RSI_LE_60",lambda r:None if v(r,"rsi14") is None else v(r,"rsi14")<=D(60)),
      ("ASSET_AND_MOM_NONPOS",lambda r:None if v(r,"asset_day_return") is None or v(r,"momentum_3v15") is None else v(r,"asset_day_return")<=0 and v(r,"momentum_3v15")<=0),
      ("ASSET_AND_BREADTH_NONPOS",lambda r:None if v(r,"asset_day_return") is None or v(r,"breadth_score") is None else v(r,"asset_day_return")<=0 and v(r,"breadth_score")<=0),
      ("ASSET_BREADTH_MOM_NONPOS",lambda r:None if any(v(r,k) is None for k in ("asset_day_return","breadth_score","momentum_3v15")) else v(r,"asset_day_return")<=0 and v(r,"breadth_score")<=0 and v(r,"momentum_3v15")<=0),
    ]
    feats=("momentum_3v15","ema_gap","rsi14","rsi_delta","avg_range","book_imbalance","breadth_score","asset_day_return")
    result={"schema":"POROTA_RC6_ENTRY_OPPORTUNITY_ANALYSIS_V1","read_only":True,
      "label_definition":"full-depth cost-aware NET target reached before factual exit-intent due_at",
      "coverage":{"rows":len(rows),"features_source":f.get("schema"),"context_source":c.get("schema"),"exit_source":e.get("schema")},
      "targets":{},"rows":rows,
      "limitations":["Factual opened trades only; this does not label historical HOLD decisions.",
                     "Profiles are diagnostics, not production recommendations.",
                     "Feature direction is evaluated out-of-time by reporting first and second half separately."]}
    for key in NET_KEYS:
        lab="opp_"+key
        allm=metric(rows,lab)
        result["targets"][key]={"all":allm,
          "auc_higher_is_opportunity":{feat:auc(rows,feat,lab) for feat in feats},
          "profiles":[profile(rows,lab,n,p) for n,p in predicates]}
    return result

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--features",default="rc6_point_in_time_entry_features.json")
    ap.add_argument("--context",default="rc6_entry_context_audit.json")
    ap.add_argument("--exits",default="rc6_exit_target_counterfactual.json")
    ap.add_argument("--out",default="rc6_entry_opportunity_analysis.json")
    a=ap.parse_args();r=build(a.features,a.context,a.exits)
    Path(a.out).write_text(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"status":"OK","coverage":r["coverage"],"targets":{k:v["all"] for k,v in r["targets"].items()}},sort_keys=True))

if __name__=="__main__":main()
