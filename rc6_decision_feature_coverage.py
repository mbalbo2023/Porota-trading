#!/usr/bin/env python3
"""Aggregate point-in-time feature coverage for RC6 PAPER decisions.

Read-only. Measures what was actually frozen for BUY/HOLD decisions by day.
No labels, fills, model fitting, network or broker calls.
"""
from __future__ import annotations
import argparse,hashlib,json,sqlite3
from collections import Counter,defaultdict
from pathlib import Path

def safe(v):
    try:
        x=json.loads(v or "{}"); return x if isinstance(x,dict) else {}
    except Exception:return {}

def canon(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def sha(x):return hashlib.sha256(canon(x).encode()).hexdigest()

FIELDS=(
 "BASE_SIGNAL","CANDIDATE","HARD_SAFETY","HIST_SHADOW","HIST_HISTORY",
 "HIST_TREND20","HIST_TREND50","HIST_AVG_RANGE","CANDLES_5M",
 "CANDLE_MOMENTUM","CANDLE_AVG_RANGE","IOL","MACRO","GDELT",
 "ECONOMICS_GATE_DETAIL","QUOTE_COMPLETE","EVIDENCE_HASH_VALID"
)

def present(v): return v not in (None,"","UNKNOWN")

def build(db):
    c=sqlite3.connect(f"file:{Path(db)}?mode=ro",uri=True,timeout=20);c.row_factory=sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    try:
        mode,orders=c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if mode!="PRODUCTION_PAPER" or int(orders or 0)!=0:raise RuntimeError("SAFETY_STATE_INVALID")
        evidence={}
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='decision_evidence_snapshots'").fetchone():
            for r in c.execute("SELECT decision_key,payload_sha256,payload_json FROM decision_evidence_snapshots"):
                p=safe(r["payload_json"])
                evidence[str(r["decision_key"])]={"payload":p,"valid":bool(p) and sha(p)==str(r["payload_sha256"] or "")}
        gates={}
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trade_gate_evaluations'").fetchone():
            for r in c.execute("SELECT decision_key,detail_json FROM trade_gate_evaluations ORDER BY julianday(evaluated_at),id"):
                gates[str(r["decision_key"])]=safe(r["detail_json"])
        counts=defaultdict(Counter); totals=defaultdict(Counter)
        rows=[dict(r) for r in c.execute("SELECT decision_key,decided_at,action,features_json FROM paper_decisions ORDER BY julianday(decided_at),decision_key")]
        for d in rows:
            day=str(d.get("decided_at") or "")[:10];action=str(d.get("action") or "UNKNOWN").upper()
            keys=((day,"ALL"),(day,action),("ALL","ALL"),("ALL",action))
            f=safe(d.get("features_json")); ev=evidence.get(str(d.get("decision_key")),{})
            p=ev.get("payload",{}) if isinstance(ev,dict) else {}
            inp=p.get("inputs_used") if isinstance(p.get("inputs_used"),dict) else {}
            q=p.get("quote_used") if isinstance(p.get("quote_used"),dict) else {}
            hs=f.get("historical_candle_shadow") if isinstance(f.get("historical_candle_shadow"),dict) else {}
            hist=hs.get("history") if isinstance(hs.get("history"),dict) else {}
            candles=hs.get("candles_5m") if isinstance(hs.get("candles_5m"),dict) else {}
            gd=gates.get(str(d.get("decision_key")),{})
            flags={
              "BASE_SIGNAL":all(present(f.get(k)) for k in ("momentum","spread","samples")),
              "CANDIDATE":isinstance(f.get("candidate"),dict) and bool(f.get("candidate")),
              "HARD_SAFETY":isinstance(f.get("hard_safety"),dict) and bool(f.get("hard_safety")),
              "HIST_SHADOW":bool(hs),
              "HIST_HISTORY":bool(hist),
              "HIST_TREND20":present(hist.get("trend_20")),
              "HIST_TREND50":present(hist.get("trend_50")),
              "HIST_AVG_RANGE":present(hist.get("avg_range")),
              "CANDLES_5M":bool(candles),
              "CANDLE_MOMENTUM":present(candles.get("momentum_3v15")),
              "CANDLE_AVG_RANGE":present(candles.get("avg_range_5m")),
              "IOL":isinstance(inp.get("iol"),dict) and bool(inp.get("iol")),
              "MACRO":isinstance(f.get("macro_risk_shadow"),dict) and bool(f.get("macro_risk_shadow")),
              "GDELT":isinstance(f.get("gdelt_risk_shadow"),dict) and bool(f.get("gdelt_risk_shadow")),
              "ECONOMICS_GATE_DETAIL":isinstance(gd.get("economics"),dict) and bool(gd.get("economics")),
              "QUOTE_COMPLETE":all(present(q.get(k)) for k in ("symbol","asset_class","settlement","currency","market","bid","ask","bid_size","ask_size","observed_at")),
              "EVIDENCE_HASH_VALID":bool(ev.get("valid")),
            }
            for key in keys:
                totals[key]["TOTAL"]+=1
                for name,val in flags.items():
                    if val:counts[key][name]+=1
        def pack(key):
            n=totals[key]["TOTAL"]
            return {"total":n,"counts":{f:counts[key][f] for f in FIELDS},
              "pct":{f:round(counts[key][f]*100/n,4) if n else 0 for f in FIELDS}}
        days=sorted({k[0] for k in totals if k[0]!="ALL"})
        by_day={}
        for day in days:
            acts=sorted({k[1] for k in totals if k[0]==day and k[1]!="ALL"})
            by_day[day]={"ALL":pack((day,"ALL"))}
            for a in acts:by_day[day][a]=pack((day,a))
        return {"schema":"POROTA_RC6_DECISION_FEATURE_COVERAGE_V1","read_only":True,
          "network_calls_performed":False,"broker_calls_performed":False,
          "safety":{"mode":mode,"real_orders_sent":int(orders or 0)},
          "decisions_total":len(rows),"evidence_snapshots":len(evidence),
          "overall":{"ALL":pack(("ALL","ALL")),"BUY":pack(("ALL","BUY")),"HOLD":pack(("ALL","HOLD"))},
          "by_day":by_day,
          "interpretation":"Coverage only; missing fields are not inferred and no feature quality claim is made."}
    finally:c.close()

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--db",default="/app/data/paper_v17/observer_v17.db");ap.add_argument("--out")
    a=ap.parse_args();r=build(a.db);raw=json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)
    if a.out:Path(a.out).write_text(raw+"\n",encoding="utf-8")
    print(raw)
if __name__=="__main__":main()
