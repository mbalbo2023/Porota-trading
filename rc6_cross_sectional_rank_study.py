#!/usr/bin/env python3
"""Cross-sectional ranking study for RC6 decision-level unit opportunities.

Offline only. Consumes rc6_decision_unit_opportunity.json and evaluates
predefined within-minute rankings. No runtime access, no broker, no SQLite.
"""
from __future__ import annotations
import argparse,json
from collections import defaultdict
from decimal import Decimal,InvalidOperation
from pathlib import Path

D=Decimal
TARGETS=("0.0025","0.005")
PROFILES={
  "TREND50_LOW":(("history_trend50","LOW"),),
  "TREND50_LOW_SPREAD_LOW":(("history_trend50","LOW"),("spread","LOW")),
  "TREND50_LOW_BOOK_LOW":(("history_trend50","LOW"),("book_imbalance","LOW")),
  "TREND50_LOW_SCORE_LOW":(("history_trend50","LOW"),("score","LOW")),
}

def dec(v):
    try:
        x=D(str(v));return x if x.is_finite() else None
    except (InvalidOperation,TypeError,ValueError):return None

def minute_key(v):
    return str(v or "")[:16]

def rate(rows,label):
    rr=[r for r in rows if r.get("labels",{}).get(label) in (0,1)]
    pos=sum(int(r["labels"][label]) for r in rr)
    return {"n":len(rr),"positive":pos,"rate_pct":str(D(pos)*100/D(len(rr)) if rr else D(0))}

def rank_group(rows,profile,topk):
    usable=[]
    for r in rows:
        vals=[]
        ok=True
        for feat,direction in PROFILES[profile]:
            x=dec((r.get("features") or {}).get(feat))
            if x is None:ok=False;break
            vals.append((feat,direction,x))
        if ok: usable.append((r,vals))
    if not usable:return []
    scores={}
    n=len(usable)
    for i,(r,vals) in enumerate(usable):
        parts=[]
        for feat,direction,x in vals:
            allx=sorted(vs[j][2] for _,vs in usable for j in range(len(vs)) if vs[j][0]==feat)
            # percentile rank; lower numeric is better for LOW.
            less=sum(v<x for v in allx); equal=sum(v==x for v in allx)
            pct=(D(less)+D(equal-1)/D(2))/D(max(1,len(allx)-1)) if len(allx)>1 else D(0)
            parts.append(pct if direction=="LOW" else D(1)-pct)
        scores[r["decision_key"]]=sum(parts,D(0))/D(len(parts))
    ranked=sorted((r for r,_ in usable),key=lambda r:(scores[r["decision_key"]],r["decision_key"]))
    return ranked[:topk]

def build(payload):
    rows=[r for r in payload.get("rows",[]) if isinstance(r,dict) and r.get("currency")=="ARS"]
    for r in rows:r["_minute"]=minute_key(r.get("decided_at"))
    groups=defaultdict(list)
    for r in rows:groups[r["_minute"]].append(r)
    days=sorted({r.get("day") for r in rows if r.get("day")})
    train=set(days[:-1] if len(days)>=2 else days)
    validation=set(days[-1:] if len(days)>=2 else [])
    result={"schema":"POROTA_RC6_CROSS_SECTIONAL_RANK_STUDY_V1","currency":"ARS",
      "temporal_split":{"train_days":sorted(train),"validation_days":sorted(validation)},
      "profiles":{},"limitations":[
        "This is a unit-opportunity ranking study, not a portfolio backtest.",
        "Selections can repeat the same symbol across adjacent minutes.",
        "Only point-in-time features from the source artifact are used for ranking.",
        "Profiles are predefined and evaluated on train/validation days without retuning on validation."
      ]}
    for t in TARGETS:
        label="hit_net_"+t+"_before_stop"
        base_train=rate([r for r in rows if r.get("day") in train],label)
        base_val=rate([r for r in rows if r.get("day") in validation],label)
        result["profiles"][t]={"BASE":{"train":base_train,"validation":base_val}}
        for pname in PROFILES:
            for topk in (1,2):
                selected=[]
                for g in groups.values():selected.extend(rank_group(g,pname,topk))
                tr=[r for r in selected if r.get("day") in train]
                va=[r for r in selected if r.get("day") in validation]
                mt=rate(tr,label);mv=rate(va,label)
                bt=dec(base_train["rate_pct"]);bv=dec(base_val["rate_pct"])
                rt=dec(mt["rate_pct"]);rv=dec(mv["rate_pct"])
                gate={
                  "min_train_n":mt["n"]>=100,
                  "min_validation_n":mv["n"]>=100,
                  "train_lift_ge_1_5x":bool(bt and rt is not None and rt>=bt*D("1.5")),
                  "validation_lift_ge_2x":bool(bv and rv is not None and rv>=bv*D("2")),
                  "validation_positive_count_ge_10":mv["positive"]>=10,
                }
                gate["passes_rank_gate"]=all(gate.values())
                result["profiles"][t][f"{pname}_TOP{topk}"]={"train":mt,"validation":mv,"gate":gate}
    passed=[]
    for t,ps in result["profiles"].items():
        for name,x in ps.items():
            if name!="BASE" and x["gate"]["passes_rank_gate"]:passed.append({"target":t,"profile":name})
    result["passed"]=passed
    return result

def main():
    ap=argparse.ArgumentParser();ap.add_argument("source");ap.add_argument("--out",default="rc6_cross_sectional_rank_study.json")
    a=ap.parse_args();p=json.loads(Path(a.source).read_text(encoding="utf-8"));r=build(p)
    Path(a.out).write_text(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"status":"OK","passed":r["passed"],"profiles":r["profiles"]},sort_keys=True))
if __name__=="__main__":main()
