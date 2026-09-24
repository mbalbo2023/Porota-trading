#!/usr/bin/env python3
"""Analyze decision-level net-opportunity labels.

Consumes only the JSON artifact from rc6_decision_net_opportunity_labels.py.
No runtime, network, broker or SQLite access.
"""
from __future__ import annotations
import argparse,json,math
from collections import defaultdict
from decimal import Decimal,InvalidOperation
from pathlib import Path

D=Decimal
TARGETS=("0.0025","0.005","0.0075","0.01")

def dec(v):
    try:
        x=D(str(v));return x if x.is_finite() else None
    except (InvalidOperation,TypeError,ValueError):return None

def auc(rows,feature,label,invert=False):
    pairs=[]
    for r in rows:
        x=dec(r.get(feature));y=r.get(label)
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
    n=len(rows);pos=sum(int(r.get(label,0)) for r in rows)
    return {"n":n,"positive":pos,"rate_pct":str(D(pos)*100/D(n) if n else D(0))}

def build(payload):
    rows=[r for r in payload.get("rows",[]) if isinstance(r,dict)]
    days=sorted({r.get("day") for r in rows if r.get("day")})
    cut=max(1,len(days)//2);train=set(days[:cut]);validation=set(days[cut:])
    out={"schema":"POROTA_RC6_DECISION_LABEL_ANALYSIS_V1","rows":len(rows),
         "temporal_split":{"train_days":days[:cut],"validation_days":days[cut:]},
         "targets":{}}
    for t in TARGETS:
        label="opp_"+t
        valid=[r for r in rows if r.get(label) in (0,1)]
        by_action={a:metric([r for r in valid if r.get("action")==a],label) for a in sorted({r.get("action") for r in valid})}
        score_bands=[]
        for name,lo,hi in (("<0.60",-99,D("0.60")),("0.60-0.62",D("0.60"),D("0.62")),("0.62-0.65",D("0.62"),D("0.65")),
                           ("0.65-0.70",D("0.65"),D("0.70")),(">=0.70",D("0.70"),D("99"))):
            g=[r for r in valid if dec(r.get("stored_score")) is not None and dec(r["stored_score"])>=lo and dec(r["stored_score"])<hi]
            score_bands.append({"band":name,**metric(g,label)})
        by_day={d:metric([r for r in valid if r.get("day")==d],label) for d in days}
        buy=[r for r in valid if r.get("action")=="BUY"];hold=[r for r in valid if r.get("action")=="HOLD"]
        buy_rate=D(metric(buy,label)["rate_pct"]) if buy else None
        hold_rate=D(metric(hold,label)["rate_pct"]) if hold else None
        out["targets"][t]={
          "all":metric(valid,label),"by_action":by_action,
          "buy_vs_hold_uplift_pct_points":str(buy_rate-hold_rate) if buy_rate is not None and hold_rate is not None else None,
          "auc":{"score":auc(valid,"stored_score",label),"momentum":auc(valid,"momentum",label),
                 "spread_lower_is_better":auc(valid,"spread",label,invert=True),"samples":auc(valid,"samples",label)},
          "score_bands":score_bands,"by_day":by_day,
          "train":metric([r for r in valid if r.get("day") in train],label),
          "validation":metric([r for r in valid if r.get("day") in validation],label)
        }
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument("labels");ap.add_argument("--out",default="rc6_decision_label_analysis.json")
    a=ap.parse_args();p=json.loads(Path(a.labels).read_text(encoding="utf-8"));r=build(p)
    Path(a.out).write_text(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"status":"OK","rows":r["rows"],"targets":r["targets"]},sort_keys=True))
if __name__=="__main__":main()
