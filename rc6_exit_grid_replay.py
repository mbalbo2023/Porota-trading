#!/usr/bin/env python3
"""RC6 read-only first-passage exit grid.

Replays each CLOSED PAPER trade independently against contemporaneous executable
BID snapshots from entry until the earlier of 360 minutes or 17:00 ART.
It does not model portfolio contention, alternative entries, or missed trades.
Only rows with persisted economic fee rates are eligible; missing rates are not
inferred.
"""
from __future__ import annotations
import argparse,json,sqlite3
from collections import defaultdict
from datetime import datetime,timedelta,time
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

TZ=ZoneInfo("America/Argentina/Buenos_Aires")
D=Decimal
SLIPPAGE=D("0.0002")
STOPS=(D("0.01"),D("0.015"),D("0.02"),D("0.025"),D("0.03"))
TARGETS=(D("0.01"),D("0.015"),D("0.02"),D("0.025"),D("0.03"),D("0.05"))

def dec(v):
    try:
        x=D(str(v)); return x if x.is_finite() else None
    except (InvalidOperation,TypeError,ValueError): return None

def js(v):
    try:
        x=json.loads(v or "{}"); return x if isinstance(x,dict) else {}
    except Exception:return {}

def dt(v):
    x=datetime.fromisoformat(str(v).replace("Z","+00:00"))
    if x.tzinfo is None: raise ValueError("NAIVE_TIMESTAMP")
    return x.astimezone(TZ)

def q4(x): return x.quantize(D("0.0001"),rounding=ROUND_HALF_UP)
def q2(x): return x.quantize(D("0.01"),rounding=ROUND_HALF_UP)

def exit_cost(exit_fill,entry_price,qty,factor,full,low):
    amount=exit_fill*qty*factor
    base=q2(amount*full)
    rebate=q2(min(entry_price*qty*factor,amount)*(full-low))
    return max(D(0),base-rebate)

def path(c,p):
    opened=dt(p["opened_at"])
    session_end=datetime.combine(opened.date(),time(17,0),TZ)
    horizon=min(opened+timedelta(minutes=360),session_end)
    q="""SELECT observed_at,bid,bid_size,source,id FROM market_snapshots
         WHERE symbol=? AND asset_class=? AND settlement=? AND currency=? AND market=?
           AND julianday(observed_at)>=julianday(?) AND julianday(observed_at)<=julianday(?)
           AND CAST(bid AS REAL)>0
         ORDER BY julianday(observed_at),id"""
    out=[]
    for r in c.execute(q,(p["symbol"],p["asset_class"],p["settlement"],p["currency"],p["market"],
                          p["opened_at"],horizon.astimezone(opened.tzinfo).isoformat())):
        bid=dec(r["bid"])
        if bid is None or bid<=0: continue
        at=dt(r["observed_at"])
        if at<opened or at>horizon: continue
        out.append({"at":at,"bid":bid,"bid_size":dec(r["bid_size"]),"source":r["source"],"id":r["id"]})
    return out,horizon

def replay_one(p,obs,stop_pct,target_pct):
    entry=dec(p["entry_price"]); qty=dec(p["quantity"])
    feat=js(p["features_json"]); econ=feat.get("economics") if isinstance(feat.get("economics"),dict) else {}
    factor=dec(feat.get("contract_cash_multiplier","1"))
    full=dec(econ.get("full_leg_rate")); low=dec(econ.get("rebated_leg_rate"))
    entry_cost=dec(p["entry_cost"])
    if None in (entry,qty,factor,full,low,entry_cost) or not obs:
        return {"status":"NOT_REPLAYABLE","reason":"ECONOMIC_RATE_OR_PATH_MISSING"}
    stop=entry*(D(1)-stop_pct); target=entry*(D(1)+target_pct)
    hit=None; reason="HORIZON"
    for o in obs:
        if o["bid"]<=stop:
            hit=o;reason="STOP";break
        if o["bid"]>=target:
            hit=o;reason="TARGET";break
    if hit is None: hit=obs[-1]
    exit_fill=q4(hit["bid"]*(D(1)-SLIPPAGE))
    ec=exit_cost(exit_fill,entry,qty,factor,full,low)
    gross=(exit_fill-entry)*qty*factor
    net=gross-entry_cost-ec
    invested=entry*qty*factor
    ret=(net/invested*100) if invested else None
    opened=dt(p["opened_at"])
    return {"status":"REPLAYED","reason":reason,"exit_at":hit["at"].isoformat(),
            "exit_bid":str(hit["bid"]),"exit_fill":str(exit_fill),
            "entry_cost":str(entry_cost),"exit_cost":str(ec),
            "gross_pnl":str(gross),"net_pnl":str(net),
            "net_return_pct":str(ret) if ret is not None else None,
            "duration_minutes":(hit["at"]-opened).total_seconds()/60.0}

def metric(rows):
    rr=[x for x in rows if x.get("status")=="REPLAYED" and dec(x.get("net_pnl")) is not None]
    wins=[x for x in rr if dec(x["net_pnl"])>0]; losses=[x for x in rr if dec(x["net_pnl"])<0]
    w=sum((dec(x["net_pnl"]) for x in wins),D(0)); l=-sum((dec(x["net_pnl"]) for x in losses),D(0))
    return {"n":len(rr),"wins":len(wins),"losses":len(losses),
            "win_rate_pct":str(D(len(wins))*100/D(len(rr)) if rr else D(0)),
            "net_total":str(sum((dec(x["net_pnl"]) for x in rr),D(0))),
            "avg_net":str(sum((dec(x["net_pnl"]) for x in rr),D(0))/D(len(rr)) if rr else D(0)),
            "profit_factor":str(w/l) if l else None,
            "target_exits":sum(x["reason"]=="TARGET" for x in rr),
            "stop_exits":sum(x["reason"]=="STOP" for x in rr),
            "horizon_exits":sum(x["reason"]=="HORIZON" for x in rr)}

def build(db):
    c=sqlite3.connect(f"file:{Path(db)}?mode=ro",uri=True,timeout=20);c.row_factory=sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    try:
        mode,orders=c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if mode!="PRODUCTION_PAPER" or int(orders or 0)!=0:
            raise RuntimeError("SAFETY_STATE_INVALID")
        positions=[dict(r) for r in c.execute("SELECT * FROM paper_positions WHERE status='CLOSED' ORDER BY julianday(opened_at),paper_id")]
        paths={}; eligible=[]; legacy=[]; path_missing=[]
        for p in positions:
            feat=js(p.get("features_json")); econ=feat.get("economics") if isinstance(feat.get("economics"),dict) else {}
            if dec(econ.get("full_leg_rate")) is None or dec(econ.get("rebated_leg_rate")) is None:
                legacy.append(p["paper_id"]); continue
            obs,horizon=path(c,p); paths[p["paper_id"]]=(obs,horizon)
            if not obs:path_missing.append(p["paper_id"]);continue
            eligible.append(p)
        grid={}
        details={}
        for s in STOPS:
            for t in TARGETS:
                key=f"S{str(s)}_T{str(t)}"; rs=[]
                for p in eligible:
                    obs,_=paths[p["paper_id"]]
                    row=replay_one(p,obs,s,t);row.update(paper_id=p["paper_id"],currency=p["currency"],
                        strategy_version=p["strategy_version"],stop_pct=str(s),target_pct=str(t))
                    rs.append(row)
                grid[key]={"stop_pct":str(s),"target_pct":str(t),"all":metric(rs),
                           "ARS":metric([x for x in rs if x["currency"]=="ARS"]),
                           "USD_MEP":metric([x for x in rs if x["currency"]=="USD_MEP"])}
                details[key]=rs
        actual_current=[p for p in eligible if p["strategy_version"]!="paper-momentum-v17.0-rc3-hf1"]
        return {"schema":"POROTA_RC6_EXIT_GRID_REPLAY_V1","read_only":True,
          "network_calls_performed":False,"broker_calls_performed":False,
          "safety":{"mode":mode,"real_orders_sent":int(orders or 0)},
          "method":{"slippage_fraction":str(SLIPPAGE),"session_close_art":"17:00",
                    "max_hold_minutes":360,"trigger_price":"EXECUTABLE_BID",
                    "fee_source":"PERSISTED_ECONOMICS_FULL_AND_REBATED_LEG_RATES",
                    "liquidity_model":"INDEPENDENT_FULL_POSITION_FIRST_PASSAGE; NO_PORTFOLIO_CONTENTION",
                    "same_day_intraday_rebate":True},
          "coverage":{"closed_total":len(positions),"eligible":len(eligible),
                      "legacy_fee_model_excluded":len(legacy),"legacy_ids":legacy,
                      "path_missing":len(path_missing),"path_missing_ids":path_missing,
                      "current_model_nonlegacy":len(actual_current)},
          "grid":grid,"details":details}
    finally:c.close()

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--db",default="/app/data/paper_v17/observer_v17.db");ap.add_argument("--out")
    a=ap.parse_args();r=build(a.db);raw=json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True,default=str)
    if a.out:Path(a.out).write_text(raw+"\n",encoding="utf-8")
    print(raw)
if __name__=="__main__":main()
