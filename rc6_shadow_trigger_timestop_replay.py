#!/usr/bin/env python3
"""Sequential SHADOW replay: TREND50_LOW ranking + entry trigger + short time-stop.

Research-only / read-only. Candidate selection uses only point-in-time frozen
features. Future BID observations are used only after hypothetical entry for
target/stop/time-stop exits. No factual decisions, fills, broker routes or
SQLite rows are modified.
"""
from __future__ import annotations
import argparse,bisect,hashlib,json,sqlite3
from collections import Counter,defaultdict
from datetime import datetime,timedelta
from decimal import Decimal,InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

D=Decimal
TZ=ZoneInfo("America/Argentina/Buenos_Aires")
SLIPPAGE=D("0.0002")
STOP=D("0.02")
TARGETS=(D("0.0025"),D("0.005"))
TIME_STOPS=(30,60,120)
CAPACITY=5
IDENTITY=("symbol","asset_class","settlement","currency","market")
TRIGGERS=("NONE","CANDLE_MOM_POS","SCORE_MID_045_055","CANDLE_MOM_POS_AND_SCORE_MID")

def dec(v):
    try:
        x=D(str(v)); return x if x.is_finite() else None
    except (InvalidOperation,TypeError,ValueError): return None

def safe(v):
    try:
        x=json.loads(v or "{}"); return x if isinstance(x,dict) else {}
    except Exception:return {}

def canon(x): return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def hsh(x): return hashlib.sha256(canon(x).encode()).hexdigest()

def aware(v):
    try:
        x=datetime.fromisoformat(str(v).replace("Z","+00:00"))
        return x if x.tzinfo else None
    except Exception:return None

def rates(asset):
    import au_fee_schedule
    return D(str(au_fee_schedule.costo_por_tramo(asset))),D(str(au_fee_schedule.costo_por_tramo_bonificado(asset)))

def net_return(entry,exit_fill,full,low):
    entry_cost=entry*full
    exit_cost=max(D(0),exit_fill*full-min(entry,exit_fill)*(full-low))
    return (exit_fill-entry-entry_cost-exit_cost)/entry

def session_cutoff(q,at,time_stop):
    import bq_exit_policy
    p=bq_exit_policy.PaperSessionPolicy()
    inst={k:q.get(k) for k in ("market","asset_class","settlement")}
    if p.admission_error(inst,at): return None
    _,end=p.bounds(at)
    return min(at+timedelta(minutes=time_stop),end-timedelta(minutes=p.exit_minutes))

def snapshot_index(c):
    idx=defaultdict(list)
    for r in c.execute("""SELECT id,observed_at,symbol,asset_class,settlement,currency,market,bid,bid_size
      FROM market_snapshots ORDER BY symbol,asset_class,settlement,currency,market,julianday(observed_at),id"""):
        at=aware(r["observed_at"])
        if at is None: continue
        key=tuple(str(r[k]) for k in IDENTITY)
        idx[key].append((at,dec(r["bid"]),dec(r["bid_size"])))
    return {k:([x[0] for x in v],v) for k,v in idx.items()}

def path(idx,q,start,end):
    pair=idx.get(tuple(str(q[k]) for k in IDENTITY))
    if not pair:return []
    times,series=pair
    lo=bisect.bisect_right(times,start); hi=bisect.bisect_right(times,end)
    return [x for x in series[lo:hi] if x[1] is not None and x[1]>0 and x[2] is not None and x[2]>0]

def trigger_ok(cand,name):
    cm=cand.get("candle_momentum")
    sc=cand.get("score")
    if name=="NONE": return True
    if name=="CANDLE_MOM_POS": return cm is not None and cm>0
    if name=="SCORE_MID_045_055": return sc is not None and D("0.45")<=sc<=D("0.55")
    if name=="CANDLE_MOM_POS_AND_SCORE_MID":
        return cm is not None and cm>0 and sc is not None and D("0.45")<=sc<=D("0.55")
    return False

def outcome(idx,cand,target,time_stop):
    q=cand["quote"]; at=cand["at"]; cut=session_cutoff(q,at,time_stop)
    if cut is None or cut<=at:return None
    obs=path(idx,q,at,cut)
    if not obs:return None
    entry=cand["ask"]*(D(1)+SLIPPAGE)
    stop=entry*(D(1)-STOP)
    full,low=rates(q["asset_class"])
    last=None
    for stamp,bid,size in obs:
        exit_fill=bid*(D(1)-SLIPPAGE)
        nr=net_return(entry,exit_fill,full,low)
        last=(stamp,nr,bid)
        if nr>=target:
            return {"exit_at":stamp,"net_return":nr,"reason":"TARGET_NET","bid":bid}
        if bid<=stop:
            return {"exit_at":stamp,"net_return":nr,"reason":"STOP_2PCT","bid":bid}
    if last:
        return {"exit_at":last[0],"net_return":last[1],"reason":"TIME_STOP","bid":last[2]}
    return None

def metrics(trades):
    xs=[t["net_return"] for t in trades]
    wins=sum(x>0 for x in xs); losses=sum(x<0 for x in xs); flats=sum(x==0 for x in xs)
    wp=sum((x for x in xs if x>0),D(0)); lp=-sum((x for x in xs if x<0),D(0))
    eq=D(0); peak=D(0); maxdd=D(0)
    for x in xs:
        eq+=x; peak=max(peak,eq); maxdd=max(maxdd,peak-eq)
    return {"n":len(xs),"wins":wins,"losses":losses,"flats":flats,
      "win_rate_pct":str(D(wins)*100/D(len(xs)) if xs else D(0)),
      "sum_unit_return":str(sum(xs,D(0))),
      "avg_unit_return":str(sum(xs,D(0))/D(len(xs)) if xs else D(0)),
      "profit_factor":str(wp/lp) if lp else None,
      "max_drawdown_unit_return":str(maxdd),
      "reasons":dict(Counter(t["reason"] for t in trades))}

def build(db):
    c=sqlite3.connect(f"file:{Path(db)}?mode=ro",uri=True,timeout=30); c.row_factory=sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    try:
        mode,orders=c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if mode!="PRODUCTION_PAPER" or int(orders or 0)!=0: raise RuntimeError("SAFETY_STATE_INVALID")
        decisions={str(r["decision_key"]):dict(r) for r in c.execute("SELECT * FROM paper_decisions")}
        ev=[]
        for r in c.execute("SELECT * FROM decision_evidence_snapshots ORDER BY julianday(captured_at),decision_key"):
            x=dict(r); p=safe(x.get("payload_json"))
            if p and hsh(p)==str(x.get("payload_sha256") or ""): ev.append((x,p))
        idx=snapshot_index(c); groups=defaultdict(list); excluded=Counter()
        for er,p in ev:
            d=decisions.get(str(er["decision_key"]))
            if not d: continue
            f=safe(d.get("features_json"))
            shadow=f.get("historical_candle_shadow") if isinstance(f.get("historical_candle_shadow"),dict) else {}
            hist=shadow.get("history") if isinstance(shadow.get("history"),dict) else {}
            candle=shadow.get("candles_5m") if isinstance(shadow.get("candles_5m"),dict) else {}
            trend50=dec(hist.get("trend_50"))
            candle_mom=dec(candle.get("momentum_3v15"))
            score=dec(d.get("score"))
            q=p.get("quote_used") if isinstance(p.get("quote_used"),dict) else {}
            if trend50 is None: excluded["TREND50_MISSING"]+=1; continue
            if q.get("currency")!="ARS": excluded["NON_ARS"]+=1; continue
            if any(q.get(k) in (None,"","UNKNOWN") for k in IDENTITY): excluded["IDENTITY"]+=1; continue
            ask=dec(q.get("ask")); ask_size=dec(q.get("ask_size")); at=aware(q.get("observed_at") or d.get("decided_at"))
            if ask is None or ask<=0 or ask_size is None or ask_size<=0 or at is None:
                excluded["ENTRY_BOOK"]+=1; continue
            if session_cutoff(q,at,120) is None: excluded["SESSION"]+=1; continue
            minute=at.replace(second=0,microsecond=0)
            groups[minute].append({"decision_key":d["decision_key"],"symbol":d["symbol"],
              "factual_action":str(d.get("action") or "").upper(),"at":at,
              "day":at.astimezone(TZ).date().isoformat(),"trend50":trend50,
              "candle_momentum":candle_mom,"score":score,"ask":ask,"quote":q,
              "spread":dec(f.get("spread"))})
        days=sorted({x["day"] for g in groups.values() for x in g})
        train=set(days[:-1]); validation=set(days[-1:])
        results={}
        for trigger in TRIGGERS:
          for target in TARGETS:
            for ts in TIME_STOPS:
                open_trades=[]; done=[]
                for minute,cands in sorted(groups.items()):
                    still=[]
                    for t in open_trades:
                        if t["exit_at"]<=minute: done.append(t)
                        else: still.append(t)
                    open_trades=still
                    if len(open_trades)>=CAPACITY: continue
                    occupied={t["symbol"] for t in open_trades}
                    eligible=[x for x in cands if x["symbol"] not in occupied and trigger_ok(x,trigger)]
                    if not eligible: continue
                    chosen=min(eligible,key=lambda x:(x["trend50"],x["spread"] if x["spread"] is not None else D(99),x["symbol"],x["decision_key"]))
                    o=outcome(idx,chosen,target,ts)
                    if o is None: continue
                    open_trades.append({**chosen,**o,"trigger":trigger,"time_stop_minutes":ts,"target_net":target})
                done.extend(open_trades)
                tr=[x for x in done if x["day"] in train]; va=[x for x in done if x["day"] in validation]
                mt=metrics(tr); mv=metrics(va); ma=metrics(done)
                def pf(m):
                    x=dec(m.get("profit_factor")); return x if x is not None else D(0)
                gate={"min_train_n":mt["n"]>=20,"min_validation_n":mv["n"]>=10,
                      "train_avg_positive":dec(mt["avg_unit_return"])>0 if mt["n"] else False,
                      "validation_avg_positive":dec(mv["avg_unit_return"])>0 if mv["n"] else False,
                      "train_pf_gt_1":pf(mt)>1,"validation_pf_gt_1":pf(mv)>1}
                gate["passes_expectancy_gate"]=all(gate.values())
                key=f"{trigger}__TARGET_{target}__TSTOP_{ts}__CAP_{CAPACITY}"
                results[key]={"trigger":trigger,"target_net":str(target),"time_stop_minutes":ts,
                  "capacity":CAPACITY,"all":ma,"train":mt,"validation":mv,"gate":gate}
        passed=[k for k,v in results.items() if v["gate"]["passes_expectancy_gate"]]
        return {"schema":"POROTA_RC6_TRIGGER_TIMESTOP_REPLAY_V1","read_only":True,
          "network_calls_performed":False,"broker_calls_performed":False,"factual_writes":False,
          "safety":{"mode":mode,"real_orders_sent":int(orders or 0)},
          "selection":"lowest history_trend50 among candidates passing the predeclared point-in-time trigger in each minute",
          "triggers":list(TRIGGERS),"targets":[str(x) for x in TARGETS],"time_stops":list(TIME_STOPS),
          "capacity":CAPACITY,"days":days,"train_days":sorted(train),"validation_days":sorted(validation),
          "excluded":dict(excluded),"passed":passed,"results":results,
          "limitations":[
            "Unit-return replay, not capital-weighted portfolio PnL.",
            "One new candidate at most per minute and no duplicate open symbol.",
            "Capacity fixed at five; sector/capital contention is not modeled.",
            "Future BID observations are used only after entry for exits.",
            "This small predefined grid is a research gate; no factual parameter is changed."
          ]}
    finally:c.close()

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--db",default="/app/data/paper_v17/observer_v17.db"); ap.add_argument("--out")
    a=ap.parse_args(); r=build(a.db); raw=json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)
    if a.out: Path(a.out).write_text(raw+"\n",encoding="utf-8")
    print(raw)
if __name__=="__main__":main()
