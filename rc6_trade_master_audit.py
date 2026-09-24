#!/usr/bin/env python3
"""RC6 closed-trade master audit.

Strictly read-only. Reads the PAPER SQLite ledger and emits one JSON document.
No network calls, broker calls, writes, parameter changes or inferred prices.
"""
from __future__ import annotations
import argparse, hashlib, json, sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

DEFAULT_DB="/app/data/paper_v17/observer_v17.db"

def safe_json(v):
    try:
        x=json.loads(v or "{}")
        return x if isinstance(x,dict) else {}
    except Exception:
        return {}

def d(v):
    try:
        x=Decimal(str(v))
        return x if x.is_finite() else None
    except (InvalidOperation,TypeError,ValueError):
        return None

def cols(c,t):
    try: return {str(r[1]) for r in c.execute(f'PRAGMA table_info("{t}")')}
    except sqlite3.Error: return set()

def exists(c,t):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone())

def rows(c,sql,p=()):
    return [dict(r) for r in c.execute(sql,p)]

def canonical_hash(payload):
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()

def pick(obj,*path):
    cur=obj
    for k in path:
        if not isinstance(cur,dict): return None
        cur=cur.get(k)
    return cur

def mfe_mae(c,p):
    needed={"symbol","asset_class","settlement","currency","market","opened_at","closed_at","entry_price"}
    if not needed.issubset(p) or any(p.get(k) in (None,"") for k in needed):
        return {"status":"NO_MEDIDO","reason":"TRADE_IDENTITY_INCOMPLETE"}
    if not exists(c,"market_snapshots"):
        return {"status":"NO_MEDIDO","reason":"MARKET_SNAPSHOTS_MISSING"}
    sc=cols(c,"market_snapshots")
    required={"symbol","asset_class","settlement","currency","market","observed_at","bid","source"}
    if not required.issubset(sc):
        return {"status":"NO_MEDIDO","reason":"SNAPSHOT_SCHEMA_INCOMPLETE","missing":sorted(required-sc)}
    entry=d(p["entry_price"])
    if not entry or entry<=0:
        return {"status":"NO_MEDIDO","reason":"ENTRY_PRICE_INVALID"}
    q="""SELECT source,observed_at,book_at,bid FROM market_snapshots
         WHERE symbol=? AND asset_class=? AND settlement=? AND currency=? AND market=?
           AND julianday(observed_at)>=julianday(?) AND julianday(observed_at)<=julianday(?)
           AND CAST(bid AS REAL)>0 ORDER BY julianday(observed_at),id"""
    obs=rows(c,q,(p["symbol"],p["asset_class"],p["settlement"],p["currency"],p["market"],p["opened_at"],p["closed_at"]))
    usable=[]
    for r in obs:
        price=d(r.get("bid"))
        if price and price>0 and r.get("source"):
            ret=(price-entry)/entry
            usable.append((ret,r))
    if not usable:
        return {"status":"NO_MEDIDO","reason":"NO_EXECUTABLE_BID_PROVENANCE","observations_used":0}
    best=max(usable,key=lambda x:x[0]); worst=min(usable,key=lambda x:x[0])
    return {
      "status":"MEDIDO","mfe_exec_return":str(best[0]),"mae_exec_return":str(worst[0]),
      "mfe_price":str(best[1]["bid"]),"mae_price":str(worst[1]["bid"]),
      "mfe_at":best[1]["observed_at"],"mae_at":worst[1]["observed_at"],
      "observations_used":len(usable),
      "source_set":sorted({str(r.get("source")) for _,r in usable}),
    }

def post_exit_recovery(c,p):
    """Executable BID path after close, bounded to 120 minutes; observation only."""
    needed={"symbol","asset_class","settlement","currency","market","closed_at","entry_price"}
    if any(p.get(k) in (None,"") for k in needed):
        return {"status":"NO_MEDIDO","reason":"TRADE_IDENTITY_INCOMPLETE"}
    entry=d(p.get("entry_price"))
    if not entry or entry<=0 or not exists(c,"market_snapshots"):
        return {"status":"NO_MEDIDO","reason":"ENTRY_OR_SNAPSHOTS_UNAVAILABLE"}
    sc=cols(c,"market_snapshots")
    required={"symbol","asset_class","settlement","currency","market","observed_at","bid","source"}
    if not required.issubset(sc):
        return {"status":"NO_MEDIDO","reason":"SNAPSHOT_SCHEMA_INCOMPLETE"}
    q="""SELECT source,observed_at,book_at,bid FROM market_snapshots
         WHERE symbol=? AND asset_class=? AND settlement=? AND currency=? AND market=?
           AND julianday(observed_at)>julianday(?)
           AND julianday(observed_at)<=julianday(?) + (120.0/1440.0)
           AND CAST(bid AS REAL)>0 ORDER BY julianday(observed_at),id"""
    obs=rows(c,q,(p["symbol"],p["asset_class"],p["settlement"],p["currency"],p["market"],p["closed_at"],p["closed_at"]))
    if not obs:
        return {"status":"NO_MEDIDO","reason":"NO_POST_EXIT_EXECUTABLE_BID","observations_used":0}
    try:
        closed=datetime.fromisoformat(str(p["closed_at"]).replace("Z","+00:00"))
    except ValueError:
        return {"status":"NO_MEDIDO","reason":"CLOSED_AT_INVALID"}
    usable=[]
    for r in obs:
        price=d(r.get("bid"))
        try: at=datetime.fromisoformat(str(r.get("observed_at")).replace("Z","+00:00"))
        except (TypeError,ValueError): continue
        if price and price>0 and r.get("source") and at.tzinfo and closed.tzinfo:
            minutes=(at-closed).total_seconds()/60.0
            if 0 < minutes <= 120.0001:
                usable.append((minutes,(price-entry)/entry,r))
    if not usable:
        return {"status":"NO_MEDIDO","reason":"NO_POST_EXIT_EXECUTABLE_BID","observations_used":0}
    result={"status":"MEDIDO","observations_used":len(usable),
            "source_set":sorted({str(r.get("source")) for _,_,r in usable})}
    for window in (30,60,120):
        xs=[x for x in usable if x[0]<=window]
        if not xs:
            result[f"max_return_{window}m"]=None
            result[f"max_bid_{window}m"]=None
            result[f"max_at_{window}m"]=None
            continue
        best=max(xs,key=lambda x:x[1])
        result[f"max_return_{window}m"]=str(best[1])
        result[f"max_bid_{window}m"]=str(best[2]["bid"])
        result[f"max_at_{window}m"]=best[2]["observed_at"]
    return result

def build(db_path):
    path=Path(db_path)
    c=sqlite3.connect(f"file:{path}?mode=ro",uri=True,timeout=20)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    try:
        mode_row=c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if not mode_row:
            raise RuntimeError("OBSERVER_STATE_MISSING")
        mode,real_orders=mode_row
        if str(mode)!="PRODUCTION_PAPER" or int(real_orders or 0)!=0:
            raise RuntimeError(f"SAFETY_STATE_INVALID:{mode}:{real_orders}")
        required=["paper_positions","paper_fills","paper_learning_samples","paper_decisions","trade_gate_evaluations"]
        missing=[t for t in required if not exists(c,t)]
        if missing: raise RuntimeError("REQUIRED_TABLES_MISSING:"+",".join(missing))
        positions=rows(c,"SELECT * FROM paper_positions WHERE status='CLOSED' ORDER BY julianday(closed_at),paper_id")
        fills_by=defaultdict(list)
        for r in rows(c,"SELECT * FROM paper_fills ORDER BY paper_id,julianday(filled_at),id"):
            fills_by[str(r.get("paper_id"))].append(r)
        learning={str(r["paper_id"]):r for r in rows(c,"SELECT * FROM paper_learning_samples")}
        gates_by_paper=defaultdict(list)
        for r in rows(c,"SELECT * FROM trade_gate_evaluations ORDER BY julianday(evaluated_at),id"):
            if r.get("paper_id"): gates_by_paper[str(r["paper_id"])].append(r)
        decisions={str(r["decision_key"]):r for r in rows(c,"SELECT * FROM paper_decisions")}
        evidence={}
        if exists(c,"decision_evidence_snapshots"):
            for r in rows(c,"SELECT * FROM decision_evidence_snapshots"):
                payload=safe_json(r.get("payload_json"))
                evidence[str(r["decision_key"])]={"row":r,"payload":payload,
                    "hash_valid":bool(payload) and canonical_hash(payload)==str(r.get("payload_sha256") or "")}
        intents={}
        if exists(c,"paper_exit_intents"):
            intents={str(r["paper_id"]):r for r in rows(c,"SELECT * FROM paper_exit_intents")}
        output=[]
        exceptions=[]
        for p in positions:
            pid=str(p["paper_id"]); feat=safe_json(p.get("features_json"))
            gs=gates_by_paper.get(pid,[])
            gate=gs[-1] if gs else {}
            dk=str(gate.get("decision_key") or "")
            decision=decisions.get(dk,{})
            ev=evidence.get(dk,{})
            ep=ev.get("payload",{}) if isinstance(ev,dict) else {}
            quote=pick(ep,"quote_used") or {}
            inputs=pick(ep,"inputs_used") or {}
            fs=fills_by.get(pid,[])
            buys=[x for x in fs if x.get("side")=="BUY_SIMULATED"]
            sells=[x for x in fs if x.get("side")=="SELL_SIMULATED"]
            lr=learning.get(pid,{})
            net=d(p.get("net_pnl"))
            outcome="WIN" if net is not None and net>0 else "LOSS" if net is not None and net<0 else "FLAT" if net==0 else "UNKNOWN"
            ex=[]
            if not buys: ex.append("BUY_FILL_MISSING")
            if not sells: ex.append("SELL_FILL_MISSING")
            if not lr: ex.append("LEARNING_SAMPLE_MISSING")
            if not gate: ex.append("GATE_EVIDENCE_MISSING")
            if dk and not decision: ex.append("DECISION_ROW_MISSING")
            if dk and not ev: ex.append("IMMUTABLE_EVIDENCE_MISSING")
            if ex: exceptions.append({"paper_id":pid,"issues":ex})
            record={
              "paper_id":pid,"strategy_version":p.get("strategy_version"),"symbol":p.get("symbol"),
              "asset_class":p.get("asset_class"),"market":p.get("market"),"settlement":p.get("settlement"),
              "currency":p.get("currency"),"currency_source":p.get("currency_source"),
              "opened_at":p.get("opened_at"),"closed_at":p.get("closed_at"),"quantity":p.get("quantity"),
              "entry_price":p.get("entry_price"),"entry_cost":p.get("entry_cost"),
              "stop_price":p.get("stop_price"),"target_price":p.get("target_price"),
              "exit_price":p.get("exit_price"),"exit_cost":p.get("exit_cost"),
              "gross_pnl":p.get("gross_pnl"),"net_pnl":p.get("net_pnl"),"outcome":outcome,
              "close_reason":p.get("close_reason"),"duration_minutes":lr.get("duration_minutes"),
              "net_return_pct":lr.get("net_return_pct"),
              "decision_key":dk or None,"decision_action":decision.get("action"),
              "decision_score":decision.get("score"),"decision_reason":decision.get("reason"),
              "gate_final_result":gate.get("final_result"),"gate_reason":gate.get("reason"),
              "sma3":feat.get("sma3"),"sma8":feat.get("sma8"),"momentum":feat.get("momentum"),
              "spread":feat.get("spread"),"samples":feat.get("samples"),
              "signal_window_minutes":feat.get("signal_window_minutes"),
              "paper_threshold":feat.get("paper_threshold"),
              "candidate":feat.get("candidate"),"economics":feat.get("economics"),
              "economics_mode":feat.get("economics_mode"),
              "historical_candle_shadow":feat.get("historical_candle_shadow"),
              "decision_shadow":feat.get("decision_shadow"),
              "macro_risk_shadow":feat.get("macro_risk_shadow"),
              "gdelt_risk_shadow":feat.get("gdelt_risk_shadow"),
              "contract_cash_multiplier":feat.get("contract_cash_multiplier"),
              "contract_quantity_step":feat.get("contract_quantity_step"),
              "quote_used":quote,
              "iol":inputs.get("iol") if isinstance(inputs,dict) else None,
              "evidence_hash_valid":ev.get("hash_valid") if ev else None,
              "fill_count":len(fs),"buy_fill_count":len(buys),"sell_fill_count":len(sells),
              "fills":fs,"exit_intent":intents.get(pid),
              "mfe_mae":mfe_mae(c,p),
              "post_exit_recovery":post_exit_recovery(c,p),
              "exceptions":ex,
            }
            output.append(record)
        by_currency=defaultdict(lambda:{"samples":0,"wins":0,"losses":0,"flats":0,"net_total":Decimal("0")})
        for r in output:
            b=by_currency[str(r.get("currency") or "UNKNOWN")]; b["samples"]+=1
            if r["outcome"]=="WIN": b["wins"]+=1
            elif r["outcome"]=="LOSS": b["losses"]+=1
            elif r["outcome"]=="FLAT": b["flats"]+=1
            n=d(r.get("net_pnl"))
            if n is not None: b["net_total"]+=n
        curr={}
        for k,v in sorted(by_currency.items()):
            curr[k]={**{x:v[x] for x in ("samples","wins","losses","flats")},
                     "win_rate_pct":str((Decimal(v["wins"])*100/Decimal(v["samples"])) if v["samples"] else Decimal("0")),
                     "net_total":str(v["net_total"])}
        integrity={
          "closed_total":len(output),
          "unique_paper_ids":len({r["paper_id"] for r in output}),
          "duplicate_paper_ids":len(output)-len({r["paper_id"] for r in output}),
          "by_currency":curr,
          "exceptions_total":len(exceptions),
          "missing_buy_fill":sum("BUY_FILL_MISSING" in x["issues"] for x in exceptions),
          "missing_sell_fill":sum("SELL_FILL_MISSING" in x["issues"] for x in exceptions),
          "missing_learning_sample":sum("LEARNING_SAMPLE_MISSING" in x["issues"] for x in exceptions),
          "missing_gate_evidence":sum("GATE_EVIDENCE_MISSING" in x["issues"] for x in exceptions),
          "immutable_evidence_verified":sum(r.get("evidence_hash_valid") is True for r in output),
          "mfe_mae_measured":sum(pick(r,"mfe_mae","status")=="MEDIDO" for r in output),
          "mfe_mae_unmeasured":sum(pick(r,"mfe_mae","status")!="MEDIDO" for r in output),
        }
        return {
          "schema":"POROTA_RC6_TRADE_MASTER_AUDIT_V1",
          "generated_at":datetime.now().astimezone().isoformat(),
          "read_only":True,"network_calls_performed":False,"broker_calls_performed":False,
          "safety":{"mode":mode,"real_orders_sent":int(real_orders or 0)},
          "database":str(path),"integrity":integrity,"exceptions":exceptions,"trades":output,
        }
    finally:
        c.close()

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--db",default=DEFAULT_DB)
    args=ap.parse_args()
    print(json.dumps(build(args.db),ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str))
if __name__=="__main__":
    main()
