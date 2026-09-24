#!/usr/bin/env python3
"""Coverage audit for a future decision-level opportunity dataset.

Read-only and label-free. This script does NOT decide whether a historical
decision was good or bad and does NOT synthesize fills. It only measures how
many persisted decisions have enough contemporaneous identity/quote evidence
and a future observed executable path to support a later, separately reviewed
opportunity-label study.
"""
from __future__ import annotations
import argparse,bisect,hashlib,json,sqlite3
from collections import Counter,defaultdict
from datetime import datetime,timedelta
from decimal import Decimal,InvalidOperation
from pathlib import Path

def dec(v):
    try:
        x=Decimal(str(v)); return x if x.is_finite() else None
    except (InvalidOperation,TypeError,ValueError): return None

def safe(v):
    try:
        x=json.loads(v or "{}"); return x if isinstance(x,dict) else {}
    except Exception:return {}

def canon(x): return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def sha(x): return hashlib.sha256(canon(x).encode()).hexdigest()

def aware(v):
    try:
        x=datetime.fromisoformat(str(v).replace("Z","+00:00"))
        return x if x.tzinfo is not None else None
    except Exception:return None

IDENTITY=("symbol","asset_class","settlement","currency","market")
BOOK=("bid","ask","bid_size","ask_size","observed_at","book_at")

def build_snapshot_index(c):
    idx=defaultdict(list)
    for r in c.execute("""SELECT observed_at,symbol,asset_class,settlement,currency,market,bid,bid_size
      FROM market_snapshots ORDER BY symbol,asset_class,settlement,currency,market,julianday(observed_at),id"""):
        at=aware(r["observed_at"])
        if at is None: continue
        key=tuple(str(r[k]) for k in IDENTITY)
        bid=dec(r["bid"]); size=dec(r["bid_size"])
        idx[key].append((at,bid is not None and bid>0,size is not None and size>0))
    return {key:([x[0] for x in series],series) for key,series in idx.items()}

def future_path_count(idx,q,at,minutes=120):
    key=tuple(str(q[k]) for k in IDENTITY)
    pair=idx.get(key)
    if not pair:return (0,0,0)
    times,series=pair
    lo=bisect.bisect_right(times,at)
    hi=bisect.bisect_right(times,at+timedelta(minutes=minutes))
    window=series[lo:hi]
    return (len(window),sum(x[1] for x in window),sum(x[1] and x[2] for x in window))

def build(db):
    c=sqlite3.connect(f"file:{Path(db)}?mode=ro",uri=True,timeout=20);c.row_factory=sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    try:
        mode,orders=c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if mode!="PRODUCTION_PAPER" or int(orders or 0)!=0: raise RuntimeError("SAFETY_STATE_INVALID")
        decisions=[dict(r) for r in c.execute("SELECT * FROM paper_decisions ORDER BY julianday(decided_at),id")]
        ev={}
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='decision_evidence_snapshots'").fetchone():
            for r in c.execute("SELECT * FROM decision_evidence_snapshots"):
                d=dict(r); p=safe(d.get("payload_json"))
                ev[str(d["decision_key"])]={"payload":p,"hash_valid":bool(p) and sha(p)==str(d.get("payload_sha256") or "")}
        reasons=Counter(); actions=Counter(); ready_actions=Counter(); stages=Counter(); by_day=defaultdict(lambda:Counter())
        state_examples=defaultdict(list)
        snapshot_index=build_snapshot_index(c)
        for d in decisions:
            action=str(d.get("action") or "UNKNOWN").upper();actions[action]+=1
            day=str(d.get("decided_at") or "")[:10]
            e=ev.get(str(d.get("decision_key")) or "")
            state="NO_EVIDENCE"; q={}; path_n=bid_n=depth_n=0
            if not e:
                reasons["NO_EVIDENCE_SNAPSHOT"]+=1
            elif not e["hash_valid"]:
                state="INVALID_EVIDENCE_HASH";reasons[state]+=1
            else:
                p=e["payload"];q=p.get("quote_used") if isinstance(p.get("quote_used"),dict) else {}
                missing_id=[k for k in IDENTITY if q.get(k) in (None,"","UNKNOWN")]
                if missing_id:
                    state="IDENTITY_INCOMPLETE";reasons[state]+=1
                else:
                    at=aware(q.get("observed_at") or d.get("decided_at"))
                    if at is None:
                        state="TIMESTAMP_INVALID";reasons[state]+=1
                    else:
                        missing_book=[k for k in BOOK if q.get(k) in (None,"")]
                        bid,ask,bs,a_s=map(dec,(q.get("bid"),q.get("ask"),q.get("bid_size"),q.get("ask_size")))
                        if missing_book or bid is None or ask is None or bs is None or a_s is None or bid<=0 or ask<bid:
                            state="BOOK_INCOMPLETE";reasons[state]+=1
                        else:
                            state="POINT_IN_TIME_QUOTE_READY"; stages[state]+=1
                            path_n,bid_n,depth_n=future_path_count(snapshot_index,q,at)
                            if bid_n<=0:
                                state="NO_FUTURE_EXECUTABLE_BID";reasons[state]+=1
                            elif depth_n<=0:
                                state="NO_FUTURE_BID_DEPTH";reasons[state]+=1
                            else:
                                state="LABEL_PATH_READY";stages[state]+=1;ready_actions[action]+=1
            by_day[day][state]+=1
            if len(state_examples[state])<20:
                state_examples[state].append({"decision_key":d.get("decision_key"),"decided_at":d.get("decided_at"),
                  "symbol":d.get("symbol"),"action":action,"score":d.get("score"),
                  "future_snapshots_120m":path_n,"future_bid_120m":bid_n,"future_bid_depth_120m":depth_n})
        ready_count=sum(v.get("LABEL_PATH_READY",0) for v in by_day.values())
        return {"schema":"POROTA_RC6_DECISION_OPPORTUNITY_COVERAGE_V1","read_only":True,
          "network_calls_performed":False,"broker_calls_performed":False,
          "labels_created":False,"synthetic_fills_created":False,
          "safety":{"mode":mode,"real_orders_sent":int(orders or 0)},
          "coverage":{"decisions_total":len(decisions),"evidence_snapshots":len(ev),
            "label_path_ready":ready_count,"label_path_ready_pct":(ready_count*100/len(decisions) if decisions else 0),
            "actions_all":dict(actions),"actions_ready":dict(ready_actions),
            "stages":dict(stages),"failure_reasons":dict(reasons)},
          "by_day":{k:dict(v) for k,v in sorted(by_day.items())},
          "state_examples":{k:v for k,v in sorted(state_examples.items())},
          "limitations":[
            "This audit creates no outcome/opportunity labels and no hypothetical fills.",
            "Future path is checked only for evidence coverage; future data is never treated as an entry feature.",
            "Exact costs, session precedence, liquidity consumption and portfolio contention must be specified before any later label can be valid.",
            "Missing evidence fails closed and is not inferred."
          ]}
    finally:c.close()

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--db",default="/app/data/paper_v17/observer_v17.db");ap.add_argument("--out")
    a=ap.parse_args();r=build(a.db);raw=json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)
    if a.out:Path(a.out).write_text(raw+"\n",encoding="utf-8")
    print(raw)
if __name__=="__main__":main()
