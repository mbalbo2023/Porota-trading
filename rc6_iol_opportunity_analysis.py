#!/usr/bin/env python3
"""IOL point-in-time diagnostics against executable net-opportunity labels.

Consumes the decision label artifact only. No network, broker or SQLite access.
IOL remains SHADOW and has no factual authority.
"""
from __future__ import annotations
import argparse,json
from collections import Counter
from decimal import Decimal,InvalidOperation
from pathlib import Path

D=Decimal
TARGETS=("0.0025","0.005")

def dec(v):
    try:
        x=D(str(v)); return x if x.is_finite() else None
    except (InvalidOperation,TypeError,ValueError): return None

def ratio(a,b):
    a=dec(a);b=dec(b)
    return (a/b-D(1)) if a is not None and b not in (None,D(0)) else None

def auc(rows,feature,label,invert=False):
    pairs=[]
    for r in rows:
        x=r.get(feature)
        x=dec(x)
        y=r.get(label)
        if x is None or y not in (0,1):continue
        if invert:x=-x
        pairs.append((x,int(y)))
    pos=[x for x,y in pairs if y];neg=[x for x,y in pairs if not y]
    if not pos or not neg:return None
    s=D(0)
    for p in pos:
        for n in neg:s+=D(1) if p>n else D("0.5") if p==n else D(0)
    return str(s/D(len(pos)*len(neg)))

def metric(rows,label):
    rr=[r for r in rows if r.get(label) in (0,1)]
    pos=sum(int(r[label]) for r in rr)
    return {"n":len(rr),"positive":pos,
            "rate_pct":str(D(pos)*100/D(len(rr)) if rr else D(0))}

def enrich(r):
    x=dict(r)
    x["iol_last_vs_ppi_last"]=str(v) if (v:=ratio(r.get("iol_last"),r.get("ppi_last"))) is not None else None
    x["iol_bid_vs_ppi_bid"]=str(v) if (v:=ratio(r.get("iol_bid"),r.get("ppi_bid"))) is not None else None
    x["iol_ask_vs_ppi_ask"]=str(v) if (v:=ratio(r.get("iol_ask"),r.get("ppi_ask"))) is not None else None
    return x

def build(payload):
    rows=[enrich(r) for r in payload.get("rows",[]) if isinstance(r,dict)]
    ars=[r for r in rows if r.get("currency")=="ARS"]
    ready=[r for r in ars if r.get("iol_state")=="READY"]
    fresh=[r for r in ready if r.get("iol_freshness")=="FRESH"]
    good=[r for r in fresh if r.get("iol_quality")=="GOOD"]
    coverage={
      "labeled_rows":len(rows),"ars_rows":len(ars),
      "state_counts":dict(Counter(str(r.get("iol_state") or "MISSING") for r in ars)),
      "quality_counts":dict(Counter(str(r.get("iol_quality") or "MISSING") for r in ars)),
      "freshness_counts":dict(Counter(str(r.get("iol_freshness") or "MISSING") for r in ars)),
      "ready":len(ready),"fresh_ready":len(fresh),"good_fresh_ready":len(good),
      "same_currency":sum(r.get("iol_currency") in (None,"",r.get("currency")) for r in ars),
    }
    numeric=(
      ("iol_spread_pct",True),
      ("iol_variation_pct",False),
      ("iol_cash_volume",False),
      ("iol_age_seconds",True),
      ("iol_last_vs_ppi_last",False),
      ("iol_bid_vs_ppi_bid",False),
      ("iol_ask_vs_ppi_ask",False),
    )
    targets={}
    for t in TARGETS:
        label="opp_"+t
        targets[t]={
          "all_ars":metric(ars,label),
          "ready":metric(ready,label),
          "fresh_ready":metric(fresh,label),
          "good_fresh_ready":metric(good,label),
          "auc_good_fresh":{name:auc(good,name,label,invert) for name,invert in numeric},
          "by_day":{day:metric([r for r in good if r.get("day")==day],label)
                    for day in sorted({r.get("day") for r in good if r.get("day")})},
        }
    return {"schema":"POROTA_RC6_IOL_OPPORTUNITY_ANALYSIS_V1",
      "mode":"SHADOW_READ_ONLY","decision_effect":"NO_FACTUAL_BINDING",
      "coverage":coverage,"targets":targets,
      "limitations":[
        "IOL values are used only when persisted contemporaneously in immutable decision evidence.",
        "AUC is diagnostic association, not causal evidence and not a production threshold.",
        "Cash volume is not normalized cross-sectionally by instrument size in this first diagnostic.",
        "No missing IOL value is inferred."
      ]}

def main():
    ap=argparse.ArgumentParser();ap.add_argument("labels");ap.add_argument("--out",default="rc6_iol_opportunity_analysis.json")
    a=ap.parse_args();p=json.loads(Path(a.labels).read_text(encoding="utf-8"));r=build(p)
    Path(a.out).write_text(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"status":"OK","coverage":r["coverage"],"targets":r["targets"]},sort_keys=True))
if __name__=="__main__":main()
