#!/usr/bin/env python3
"""Read-only replay audit of RC6 factual technical decisions.

Uses only features persisted at decision time. It never reconstructs missing
features, never calls broker/network, and never writes SQLite.
"""
from __future__ import annotations
import argparse,json,sqlite3
from decimal import Decimal,InvalidOperation
from pathlib import Path

D=Decimal
def dec(v):
    try:
        x=D(str(v))
        return x if x.is_finite() else None
    except (InvalidOperation,TypeError,ValueError):
        return None

def safe_json(v):
    try:
        x=json.loads(v or "{}")
        return x if isinstance(x,dict) else {}
    except Exception:
        return {}

def recompute(features):
    mom=dec(features.get("momentum")); spr=dec(features.get("spread")); th=dec(features.get("paper_threshold"))
    if mom is None or spr is None or th is None:
        return {"status":"NOT_REPLAYABLE","reason":"FROZEN_TECHNICAL_FEATURES_MISSING"}
    raw=D("0.5")+mom*D(40)-spr*D(10)
    score=max(D(0),min(D(1),raw))
    action="HOLD" if spr>D("0.02") or score<th else "BUY"
    return {"status":"REPLAYED","score":str(score),"threshold":str(th),"spread":str(spr),"momentum":str(mom),"action":action}

def build(db_path):
    p=Path(db_path)
    c=sqlite3.connect(f"file:{p}?mode=ro",uri=True,timeout=20)
    c.row_factory=sqlite3.Row; c.execute("PRAGMA query_only=ON")
    try:
        mode,orders=c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if mode!="PRODUCTION_PAPER" or int(orders or 0)!=0:
            raise RuntimeError(f"SAFETY_STATE_INVALID:{mode}:{orders}")
        rows=[dict(r) for r in c.execute("""SELECT decision_key,decided_at,symbol,action,score,reason,features_json
          FROM paper_decisions ORDER BY julianday(decided_at),id""")]
        out=[]; replayed=0; exact_action=0; exact_score=0; mismatches=[]
        thresholds=[D("0.62"),D("0.63"),D("0.64"),D("0.65"),D("0.66"),D("0.67"),D("0.68"),D("0.69"),D("0.70"),D("0.72"),D("0.74")]
        threshold_counts={str(x):{"replayable":0,"would_buy":0,"would_hold":0} for x in thresholds}
        for r in rows:
            f=safe_json(r.get("features_json")); rep=recompute(f)
            item={"decision_key":r["decision_key"],"decided_at":r["decided_at"],"symbol":r["symbol"],
                  "stored_action":r["action"],"stored_score":r["score"],"stored_reason":r["reason"],**rep}
            if rep["status"]=="REPLAYED":
                replayed+=1
                stored_score=dec(r["score"]); calc=dec(rep["score"])
                score_ok=stored_score is not None and calc is not None and abs(stored_score-calc)<=D("0.000000000001")
                action_ok=str(r["action"]).upper()==rep["action"]
                item["score_match"]=score_ok; item["action_match"]=action_ok
                exact_score+=int(score_ok); exact_action+=int(action_ok)
                if not score_ok or not action_ok:
                    mismatches.append(item)
                spr=dec(rep["spread"]); sc=dec(rep["score"])
                for th in thresholds:
                    q=threshold_counts[str(th)]; q["replayable"]+=1
                    act="HOLD" if spr>D("0.02") or sc<th else "BUY"
                    q["would_buy" if act=="BUY" else "would_hold"]+=1
            out.append(item)
        return {
          "schema":"POROTA_RC6_BASELINE_REPLAY_AUDIT_V1","read_only":True,
          "network_calls_performed":False,"broker_calls_performed":False,
          "safety":{"mode":mode,"real_orders_sent":int(orders or 0)},
          "decisions_total":len(rows),"replayable":replayed,"not_replayable":len(rows)-replayed,
          "stored_action_matches":exact_action,"stored_score_matches":exact_score,
          "action_match_rate":exact_action/replayed if replayed else None,
          "score_match_rate":exact_score/replayed if replayed else None,
          "mismatches":mismatches[:200],
          "threshold_candidate_counts":threshold_counts,
          "rows":out[:5000],
          "limitations":[
            "Only decisions with persisted momentum, spread and paper_threshold are replayable.",
            "Threshold counts describe candidate density only; HOLD rows have no realized PnL and are not backfilled with future outcomes.",
            "No missing historical feature is inferred or synthesized."
          ]
        }
    finally:c.close()

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--db",default="/app/data/paper_v17/observer_v17.db");ap.add_argument("--out")
    a=ap.parse_args();r=build(a.db);raw=json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)
    if a.out:Path(a.out).write_text(raw+"\n",encoding="utf-8")
    print(raw)
if __name__=="__main__":main()
