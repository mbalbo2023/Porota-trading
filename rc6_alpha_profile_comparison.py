#!/usr/bin/env python3
"""RC6 candidate-profile comparison using only already-audited artifacts.

This module is intentionally offline and non-binding. It combines factual closed
trades with point-in-time entry diagnostics and precedence-aware exit
counterfactuals. It does not create labels for historical HOLD decisions and it
does not model portfolio capital reuse after blocked/earlier-exited trades.
"""
from __future__ import annotations
import argparse,json,math
from collections import defaultdict
from decimal import Decimal,InvalidOperation
from pathlib import Path

D=Decimal
def dec(v):
    try:
        x=D(str(v)); return x if x.is_finite() else None
    except (InvalidOperation,TypeError,ValueError): return None
def load(path): return json.loads(Path(path).read_text(encoding="utf-8"))
def metric(rows,pnl_key="profile_net"):
    xs=[dec(r.get(pnl_key)) for r in rows if dec(r.get(pnl_key)) is not None]
    wins=sum(x>0 for x in xs); losses=sum(x<0 for x in xs)
    wp=sum((x for x in xs if x>0),D(0)); lp=-sum((x for x in xs if x<0),D(0))
    return {"n":len(xs),"wins":wins,"losses":losses,
      "win_rate_pct":str(D(wins)*100/D(len(xs)) if xs else D(0)),
      "net_total":str(sum(xs,D(0))),
      "avg_net":str(sum(xs,D(0))/D(len(xs)) if xs else D(0)),
      "profit_factor":str(wp/lp) if lp else None}
def by_currency(rows,pnl_key="profile_net"):
    return {cur:metric([r for r in rows if r["currency"]==cur],pnl_key)
            for cur in sorted({r["currency"] for r in rows})}
def extract_entry(pt,ctx):
    p={r["paper_id"]:r for r in pt.get("rows",[])}
    c={r["paper_id"]:r for r in ctx.get("rows",[])}
    out={}
    for pid in set(p)|set(c):
        pr=p.get(pid,{}) ; cr=c.get(pid,{})
        candle=pr.get("candle") if isinstance(pr.get("candle"),dict) else {}
        book=cr.get("book") if isinstance(cr.get("book"),dict) else {}
        breadth=cr.get("breadth") if isinstance(cr.get("breadth"),dict) else {}
        out[pid]={
          "candle_momentum":dec(candle.get("momentum_3v15")),
          "ema9":dec(candle.get("ema9")),"ema21":dec(candle.get("ema21")),
          "rsi14":dec(candle.get("rsi14")),
          "book_imbalance":dec(book.get("book_imbalance")),
          "breadth_score":dec(breadth.get("breadth_score")),
          "asset_day_return":dec(cr.get("asset_day_return")),
        }
    return out
def counterfactual_map(exit_cf,kind,key):
    root=exit_cf[kind][key]
    changed={r["paper_id"]:dec(r.get("candidate_net_pnl")) for r in root.get("changed",[])}
    unknown={r.get("paper_id") for r in root.get("unmodeled_rows",[]) if r.get("paper_id")}
    return changed,unknown
def day_of(t):
    return str(t.get("opened_at") or "")[:10]
def time_minute(t):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    raw=str(t.get("opened_at") or "").replace("Z","+00:00")
    x=datetime.fromisoformat(raw)
    if x.tzinfo is None:return None
    x=x.astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
    return x.hour*60+x.minute
def profile_pred(name,t,e):
    score=dec(t.get("decision_score")); minute=time_minute(t)
    if name=="FACTUAL_ALL": return True
    if name=="ACTIONS_ONLY":
        asset=str(t.get("asset_class") or "").upper()
        return None if not asset else asset=="ACCIONES"
    if name=="SCORE_CAP_067":
        return None if score is None else score<D("0.67")
    if name=="NO_FIRST_90M":
        return None if minute is None else minute>=12*60
    if name=="ASSET_DAY_NONPOS":
        x=e.get("asset_day_return"); return None if x is None else x<=0
    if name=="CANDLE_MOM_NONPOS":
        x=e.get("candle_momentum"); return None if x is None else x<=0
    if name=="ANTI_CHASE_CORE":
        a=e.get("asset_day_return"); m=e.get("candle_momentum")
        if a is None or m is None or score is None: return None
        return a<=0 and m<=0 and score<D("0.67")
    return None
def evaluate(trades,entry,name,split_days,exit_changed=None,exit_unknown=None):
    exit_changed=exit_changed or {}; exit_unknown=exit_unknown or set()
    rows=[]; blocked=[]; insufficient=[]
    for t in trades:
        pid=t["paper_id"]; e=entry.get(pid,{})
        keep=profile_pred(name,t,e)
        if keep is None:
            insufficient.append(pid); continue
        if not keep:
            blocked.append(t); continue
        if pid in exit_unknown:
            insufficient.append(pid); continue
        factual=dec(t.get("net_pnl"))
        pnl=exit_changed.get(pid,factual)
        if pnl is None:
            insufficient.append(pid); continue
        rows.append({"paper_id":pid,"currency":str(t.get("currency") or "UNKNOWN"),
                     "day":day_of(t),"profile_net":str(pnl),"factual_net":str(factual) if factual is not None else None})
    first,second=split_days
    first_rows=[r for r in rows if r["day"] in first]; second_rows=[r for r in rows if r["day"] in second]
    # ARS is the only currency with enough observations to use for a candidate gate.
    ars_all=[r for r in rows if r["currency"]=="ARS"]
    ars_first=[r for r in first_rows if r["currency"]=="ARS"]
    ars_second=[r for r in second_rows if r["currency"]=="ARS"]
    m_all=metric(ars_all); m_first=metric(ars_first); m_second=metric(ars_second)
    def pf(m):
        x=dec(m.get("profit_factor")); return x if x is not None else D(0)
    gate={
      "min_total_ars_n":m_all["n"]>=20,
      "min_validation_ars_n":m_second["n"]>=8,
      "positive_train_net":dec(m_first["net_total"]) is not None and dec(m_first["net_total"])>0,
      "positive_validation_net":dec(m_second["net_total"]) is not None and dec(m_second["net_total"])>0,
      "profit_factor_train_gt_1":pf(m_first)>1,
      "profit_factor_validation_gt_1":pf(m_second)>1,
    }
    gate["passes_candidate_gate"]=all(gate.values())
    return {"entry_profile":name,"kept_total":len(rows),"blocked_total":len(blocked),
      "insufficient_total":len(insufficient),"by_currency":by_currency(rows),
      "train_days":sorted(first),"validation_days":sorted(second),
      "train_by_currency":by_currency(first_rows),"validation_by_currency":by_currency(second_rows),
      "ars_candidate_gate":gate}
def build(master,pt,ctx,exit_cf):
    trades=[t for t in master.get("trades",[]) if isinstance(t,dict)]
    entry=extract_entry(pt,ctx)
    days=sorted({day_of(t) for t in trades if day_of(t)})
    cut=max(1,len(days)//2)
    split_days=(set(days[:cut]),set(days[cut:]))
    profiles={}
    entry_names=("FACTUAL_ALL","ACTIONS_ONLY","SCORE_CAP_067","NO_FIRST_90M",
                 "ASSET_DAY_NONPOS","CANDLE_MOM_NONPOS","ANTI_CHASE_CORE")
    for name in entry_names:
        profiles[name+"__FACTUAL_EXIT"]=evaluate(trades,entry,name,split_days)
    for key in ("0.0025","0.005","0.0075","0.01"):
        changed,unknown=counterfactual_map(exit_cf,"net_targets",key)
        profiles["FACTUAL_ALL__NET_TARGET_"+key]=evaluate(trades,entry,"FACTUAL_ALL",split_days,changed,unknown)
        profiles["ANTI_CHASE_CORE__NET_TARGET_"+key]=evaluate(trades,entry,"ANTI_CHASE_CORE",split_days,changed,unknown)
    passed=[k for k,v in profiles.items() if v["ars_candidate_gate"]["passes_candidate_gate"]]
    return {"schema":"POROTA_RC6_ALPHA_PROFILE_COMPARISON_V1","mode":"READ_ONLY_SHADOW_ANALYSIS",
      "temporal_split":{"train_days":days[:cut],"validation_days":days[cut:]},
      "profiles":profiles,"candidate_gate_passed":passed,
      "conclusion":"CANDIDATE_EXISTS" if passed else "NO_PROFILE_PASSES_PREDEFINED_ROBUSTNESS_GATE",
      "limitations":[
        "Entry profiles only filter factual opened trades; historical HOLDs are not assigned synthetic outcomes.",
        "Earlier hypothetical exits and blocked entries can change future capital/risk availability; portfolio feedback is not modeled.",
        "The temporal split is a fixed chronological half split, not a parameter-selection loop.",
        "No monetary PnL is combined across currencies."
      ]}
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--master",default="rc6_trade_master.json")
    ap.add_argument("--entry-features",default="rc6_point_in_time_entry_features.json")
    ap.add_argument("--entry-context",default="rc6_entry_context_audit.json")
    ap.add_argument("--exit-cf",default="rc6_exit_target_counterfactual.json")
    ap.add_argument("--out",default="rc6_alpha_profile_comparison.json")
    a=ap.parse_args()
    r=build(load(a.master),load(a.entry_features),load(a.entry_context),load(a.exit_cf))
    Path(a.out).write_text(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"status":"OK","conclusion":r["conclusion"],"passed":r["candidate_gate_passed"]},sort_keys=True))
if __name__=="__main__":main()
