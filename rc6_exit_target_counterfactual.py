#!/usr/bin/env python3
"""Isolated lower-target counterfactual for closed RC6 PAPER trades.

Read-only, path-ordered and precedence-aware. A candidate take-profit is
counted only when an executable BID reached the candidate threshold before
the factual exit intent became due and there was enough observed BID depth
for the full remaining quantity at the configured participation rate.

This is NOT a portfolio replay: capital/risk interactions after an earlier
hypothetical exit are intentionally not simulated.
"""
from __future__ import annotations
import argparse,json,sqlite3
from datetime import datetime
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
from pathlib import Path

D=Decimal
TARGETS=(D("0.005"),D("0.0075"),D("0.01"),D("0.0125"),D("0.015"),D("0.02"),D("0.025"),D("0.03"),D("0.035"),D("0.05"))
NET_TARGETS=(D("0.0025"),D("0.005"),D("0.0075"),D("0.01"))
SLIPPAGE=D("0.0002")
PARTICIPATION=D("0.10")
CENT=D("0.01")
P4=D("0.0001")

def dec(v):
    try:
        x=D(str(v)); return x if x.is_finite() else None
    except (InvalidOperation,TypeError,ValueError): return None

def js(v):
    try:
        x=json.loads(v or "{}"); return x if isinstance(x,dict) else {}
    except Exception:return {}

def dt(v):
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00"))
    except Exception:return None

def fee_rates(asset_class):
    import au_fee_schedule
    full=D(str(au_fee_schedule.costo_por_tramo(asset_class)))
    low=D(str(au_fee_schedule.costo_por_tramo_bonificado(asset_class)))
    return full,low

def modeled_exit_cost(p,exit_price,rebate):
    qty=dec(p["quantity"]); factor=dec(js(p.get("features_json")).get("contract_cash_multiplier","1")) or D(1)
    entry=dec(p["entry_price"]); full,low=fee_rates(p["asset_class"])
    sell=(exit_price*qty*factor)
    cost=(sell*full).quantize(CENT,rounding=ROUND_HALF_UP)
    if rebate:
        buy=entry*qty*factor
        credit=(min(buy,sell)*(full-low)).quantize(CENT,rounding=ROUND_HALF_UP)
        cost=max(D(0),cost-credit)
    return cost

def infer_rebate(p):
    actual_price=dec(p.get("exit_price")); actual_cost=dec(p.get("exit_cost"))
    if actual_price is None or actual_cost is None:return None
    full=modeled_exit_cost(p,actual_price,False); reb=modeled_exit_cost(p,actual_price,True)
    return abs(actual_cost-reb) <= abs(actual_cost-full)

def cutoff_for(c,p):
    closed=dt(p.get("closed_at"))
    if closed is None:return None
    if not any(r[1]=="paper_exit_intents" for r in c.execute("SELECT type,name FROM sqlite_master")):
        return closed
    row=c.execute("SELECT due_at FROM paper_exit_intents WHERE paper_id=?",(p["paper_id"],)).fetchone()
    due=dt(row[0]) if row and row[0] else None
    return min(closed,due) if due else closed

def path(c,p,cutoff):
    sc={r[1] for r in c.execute("PRAGMA table_info(market_snapshots)")}
    need={"symbol","asset_class","settlement","currency","market","observed_at","bid","bid_size","source"}
    if not need.issubset(sc):return []
    return [dict(r) for r in c.execute("""SELECT id,source,observed_at,bid,bid_size
      FROM market_snapshots
      WHERE symbol=? AND asset_class=? AND settlement=? AND currency=? AND market=?
        AND julianday(observed_at)>=julianday(?) AND julianday(observed_at)<julianday(?)
        AND CAST(bid AS REAL)>0
      ORDER BY julianday(observed_at),id""",
      (p["symbol"],p["asset_class"],p["settlement"],p["currency"],p["market"],p["opened_at"],cutoff.isoformat()))]

def candidate(p,points,target_pct,rebate):
    entry=dec(p["entry_price"]); qty=dec(p["quantity"])
    factor=dec(js(p.get("features_json")).get("contract_cash_multiplier","1")) or D(1)
    entry_cost=dec(p["entry_cost"])
    threshold=entry*(D(1)+target_pct)
    insufficient_depth=0
    for r in points:
        bid=dec(r.get("bid")); size=dec(r.get("bid_size"))
        if bid is None or size is None or bid<threshold:continue
        if size*PARTICIPATION < qty:
            insufficient_depth+=1; continue
        exit_price=(bid*(D(1)-SLIPPAGE)).quantize(P4)
        exit_cost=modeled_exit_cost(p,exit_price,rebate)
        gross=(exit_price-entry)*qty*factor
        net=gross-entry_cost-exit_cost
        return {"status":"HIT_FULL_DEPTH","at":r["observed_at"],"bid":str(bid),
                "exit_price":str(exit_price),"exit_cost":str(exit_cost),
                "gross_pnl":str(gross),"net_pnl":str(net),
                "insufficient_depth_hits_before_fill":insufficient_depth}
    return {"status":"NOT_HIT" if not insufficient_depth else "HIT_ONLY_INSUFFICIENT_DEPTH",
            "insufficient_depth_hits_before_fill":insufficient_depth}

def candidate_net_floor(p,points,net_target_pct,rebate):
    entry=dec(p["entry_price"]); qty=dec(p["quantity"])
    factor=dec(js(p.get("features_json")).get("contract_cash_multiplier","1")) or D(1)
    entry_cost=dec(p["entry_cost"])
    invested=entry*qty*factor
    insufficient_depth=0
    for r in points:
        bid=dec(r.get("bid")); size=dec(r.get("bid_size"))
        if bid is None or size is None: continue
        exit_price=(bid*(D(1)-SLIPPAGE)).quantize(P4)
        exit_cost=modeled_exit_cost(p,exit_price,rebate)
        gross=(exit_price-entry)*qty*factor
        net=gross-entry_cost-exit_cost
        net_return=(net/invested) if invested else None
        if net_return is None or net_return<net_target_pct: continue
        if size*PARTICIPATION < qty:
            insufficient_depth+=1; continue
        return {"status":"HIT_FULL_DEPTH","at":r["observed_at"],"bid":str(bid),
                "exit_price":str(exit_price),"exit_cost":str(exit_cost),
                "gross_pnl":str(gross),"net_pnl":str(net),
                "net_return":str(net_return),
                "insufficient_depth_hits_before_fill":insufficient_depth}
    return {"status":"NOT_HIT" if not insufficient_depth else "HIT_ONLY_INSUFFICIENT_DEPTH",
            "insufficient_depth_hits_before_fill":insufficient_depth}

def build(db_path):
    pth=Path(db_path)
    c=sqlite3.connect(f"file:{pth}?mode=ro",uri=True,timeout=20);c.row_factory=sqlite3.Row;c.execute("PRAGMA query_only=ON")
    try:
        mode,orders=c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if mode!="PRODUCTION_PAPER" or int(orders or 0)!=0:raise RuntimeError("SAFETY_STATE_INVALID")
        positions=[dict(r) for r in c.execute("SELECT * FROM paper_positions WHERE status='CLOSED' ORDER BY julianday(closed_at),paper_id")]
        baseline={}
        for p in positions:
            cur=str(p.get("currency") or "UNKNOWN");baseline.setdefault(cur,D(0));baseline[cur]+=dec(p.get("net_pnl")) or D(0)
        results={}
        for target in TARGETS:
            key=str(target); bycur={k:v for k,v in baseline.items()}
            changed=[]; no_model=[]; hits=0; improved=0; worsened=0
            for p in positions:
                cutoff=cutoff_for(c,p); rebate=infer_rebate(p)
                if cutoff is None or rebate is None:
                    no_model.append({"paper_id":p["paper_id"],"reason":"CUTOFF_OR_FEE_REGIME_UNRESOLVED"});continue
                pts=path(c,p,cutoff); cand=candidate(p,pts,target,rebate)
                if cand["status"]=="HIT_FULL_DEPTH":
                    hits+=1
                    factual=dec(p["net_pnl"]) or D(0); hypothetical=dec(cand["net_pnl"]) or D(0)
                    delta=hypothetical-factual; cur=str(p.get("currency") or "UNKNOWN")
                    bycur[cur]+=delta
                    improved+=int(delta>0);worsened+=int(delta<0)
                    changed.append({"paper_id":p["paper_id"],"symbol":p["symbol"],"currency":cur,
                       "factual_reason":p.get("close_reason"),"factual_net_pnl":str(factual),
                       "candidate_net_pnl":str(hypothetical),"delta_net_pnl":str(delta),
                       "rebate_regime_inferred":rebate,**cand})
                elif cand["status"]=="HIT_ONLY_INSUFFICIENT_DEPTH":
                    no_model.append({"paper_id":p["paper_id"],"reason":"TARGET_HIT_BUT_FULL_DEPTH_UNAVAILABLE",
                                     "target":key,"hits":cand["insufficient_depth_hits_before_fill"]})
            results[key]={"target_pct":key,"changed_trades":hits,"improved_trades":improved,"worsened_trades":worsened,
                          "candidate_net_by_currency":{k:str(v) for k,v in sorted(bycur.items())},
                          "delta_by_currency":{k:str(bycur[k]-baseline[k]) for k in sorted(baseline)},
                          "unmodeled":len(no_model),"changed":changed,"unmodeled_rows":no_model[:200]}
        net_results={}
        for target in NET_TARGETS:
            key=str(target); bycur={k:v for k,v in baseline.items()}
            changed=[]; no_model=[]; hits=0; improved=0; worsened=0
            for p in positions:
                cutoff=cutoff_for(c,p); rebate=infer_rebate(p)
                if cutoff is None or rebate is None:
                    no_model.append({"paper_id":p["paper_id"],"reason":"CUTOFF_OR_FEE_REGIME_UNRESOLVED"});continue
                pts=path(c,p,cutoff); cand=candidate_net_floor(p,pts,target,rebate)
                if cand["status"]=="HIT_FULL_DEPTH":
                    hits+=1
                    factual=dec(p["net_pnl"]) or D(0); hypothetical=dec(cand["net_pnl"]) or D(0)
                    delta=hypothetical-factual; cur=str(p.get("currency") or "UNKNOWN")
                    bycur[cur]+=delta
                    improved+=int(delta>0);worsened+=int(delta<0)
                    changed.append({"paper_id":p["paper_id"],"symbol":p["symbol"],"currency":cur,
                       "factual_reason":p.get("close_reason"),"factual_net_pnl":str(factual),
                       "candidate_net_pnl":str(hypothetical),"delta_net_pnl":str(delta),
                       "rebate_regime_inferred":rebate,**cand})
                elif cand["status"]=="HIT_ONLY_INSUFFICIENT_DEPTH":
                    no_model.append({"paper_id":p["paper_id"],"reason":"NET_TARGET_HIT_BUT_FULL_DEPTH_UNAVAILABLE",
                                     "net_target":key,"hits":cand["insufficient_depth_hits_before_fill"]})
            net_results[key]={"net_target_pct":key,"changed_trades":hits,"improved_trades":improved,"worsened_trades":worsened,
                          "candidate_net_by_currency":{k:str(v) for k,v in sorted(bycur.items())},
                          "delta_by_currency":{k:str(bycur[k]-baseline[k]) for k in sorted(baseline)},
                          "unmodeled":len(no_model),"changed":changed,"unmodeled_rows":no_model[:200]}
        return {"schema":"POROTA_RC6_EXIT_TARGET_COUNTERFACTUAL_V2","read_only":True,
          "network_calls_performed":False,"broker_calls_performed":False,
          "safety":{"mode":mode,"real_orders_sent":int(orders or 0)},
          "baseline_net_by_currency":{k:str(v) for k,v in sorted(baseline.items())},
          "targets":results,"net_targets":net_results,
          "assumptions":{"slippage_fraction":str(SLIPPAGE),"participation":str(PARTICIPATION),
            "trigger":"gross targets: first observed BID >= candidate target; net targets: first observed full-depth BID whose modeled net return reaches the requested floor; all before factual exit-intent due_at",
            "fill":"full-depth only; observed BID less slippage",
            "fees":"same fee/rebate regime inferred from each factual exit, canonical au_fee_schedule",
            "portfolio_feedback":"NOT_MODELED"},
          "limitations":["Earlier hypothetical exits can alter later capital/risk availability; this isolated trade replay does not model that.",
                         "Insufficient-depth target touches are not treated as full exits.",
                         "No target event after a persisted factual exit-intent due_at can override that factual cause."]
        }
    finally:c.close()

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--db",default="/app/data/paper_v17/observer_v17.db");ap.add_argument("--out")
    a=ap.parse_args();r=build(a.db);raw=json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)
    if a.out:Path(a.out).write_text(raw+"\n",encoding="utf-8")
    print(raw)
if __name__=="__main__":main()
