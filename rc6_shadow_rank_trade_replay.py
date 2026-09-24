#!/usr/bin/env python3
"""Sequential unit-return replay of the TREND50_LOW shadow rank.

Read-only study. Selection uses only point-in-time evidence. Future BID data is
used only after a hypothetical entry to determine target/stop/time/EOD outcome.
No factual decision, fill, broker route or SQLite row is modified.
"""
from __future__ import annotations
import argparse,bisect,hashlib,json,sqlite3
from collections import defaultdict
from datetime import datetime,timedelta
from decimal import Decimal,InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

D=Decimal
TZ=ZoneInfo("America/Argentina/Buenos_Aires")
SLIPPAGE=D("0.0002")
STOP=D("0.02")
TARGETS=(D("0.0025"),D("0.005"))
CAPACITIES=(1,5)
IDENTITY=("symbol","asset_class","settlement","currency","market")

def dec(v):
    try:
        x=D(str(v));return x if x.is_finite() else None
    except (InvalidOperation,TypeError,ValueError):return None
def safe(v):
    try:
        x=json.loads(v or "{}");return x if isinstance(x,dict) else {}
    except Exception:return {}
def canon(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def hsh(x):return hashlib.sha256(canon(x).encode()).hexdigest()
def aware(v):
    try:
        x=datetime.fromisoformat(str(v).replace("Z","+00:00"));return x if x.tzinfo else None
    except Exception:return None
def rates(asset):
    import au_fee_schedule
    return D(str(au_fee_schedule.costo_por_tramo(asset))),D(str(au_fee_schedule.costo_por_tramo_bonificado(asset)))
def net_return(entry,exit_fill,full,low):
    entry_cost=entry*full
    exit_cost=max(D(0),exit_fill*full-min(entry,exit_fill)*(full-low))
    return (exit_fill-entry-entry_cost-exit_cost)/entry
def session_cutoff(q,at):
    import bq_exit_policy
    p=bq_exit_policy.PaperSessionPolicy()
    inst={k:q.get(k) for k in ("market","asset_class","settlement")}
    if p.admission_error(inst,at):return None
    _,end=p.bounds(at)
    return min(at+timedelta(minutes=120),end-timedelta(minutes=p.exit_minutes))
def snapshot_index(c):
    idx=defaultdict(list)
    for r in c.execute("""SELECT id,observed_at,symbol,asset_class,settlement,currency,market,bid,bid_size
      FROM market_snapshots ORDER BY symbol,asset_class,settlement,currency,market,julianday(observed_at),id"""):
        at=aware(r["observed_at"])
        if at is None:continue
        key=tuple(str(r[k]) for k in IDENTITY)
        idx[key].append((at,dec(r["bid"]),dec(r["bid_size"])))
    return {k:([x[0] for x in v],v) for k,v in idx.items()}
def path(idx,q,start,end):
    pair=idx.get(tuple(str(q[k]) for k in IDENTITY))
    if not pair:return []
    times,series=pair
    lo=bisect.bisect_right(times,start);hi=bisect.bisect_right(times,end)
    return [x for x in series[lo:hi] if x[1] is not None and x[1]>0 and x[2] is not None and x[2]>0]
def outcome(idx,cand,target):
    q=cand["quote"];at=cand["at"];cut=session_cutoff(q,at)
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
        return {"exit_at":last[0],"net_return":last[1],"reason":"TIME_OR_EOD","bid":last[2]}
    return None
def metrics(trades):
    xs=[t["net_return"] for t in trades]
    wins=sum(x>0 for x in xs);losses=sum(x<0 for x in xs)
    wp=sum((x for x in xs if x>0),D(0));lp=-sum((x for x in xs if x<0),D(0))
    equity=D(0);peak=D(0);maxdd=D(0)
    for x in xs:
        equity+=x;peak=max(peak,equity);maxdd=max(maxdd,peak-equity)
    return {"n":len(xs),"wins":wins,"losses":losses,
      "win_rate_pct":str(D(wins)*100/D(len(xs)) if xs else D(0)),
      "sum_unit_return":str(sum(xs,D(0))),
      "avg_unit_return":str(sum(xs,D(0))/D(len(xs)) if xs else D(0)),
      "profit_factor":str(wp/lp) if lp else None,
      "max_drawdown_unit_return":str(maxdd),
      "reasons":dict(__import__("collections").Counter(t["reason"] for t in trades))}
def build(db):
    c=sqlite3.connect(f"file:{Path(db)}?mode=ro",uri=True,timeout=30);c.row_factory=sqlite3.Row;c.execute("PRAGMA query_only=ON")
    try:
        mode,orders=c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if mode!="PRODUCTION_PAPER" or int(orders or 0)!=0:raise RuntimeError("SAFETY_STATE_INVALID")
        decisions={str(r["decision_key"]):dict(r) for r in c.execute("SELECT * FROM paper_decisions")}
        ev=[]
        for r in c.execute("SELECT * FROM decision_evidence_snapshots ORDER BY julianday(captured_at),decision_key"):
            x=dict(r);p=safe(x.get("payload_json"))
            if p and hsh(p)==str(x.get("payload_sha256") or ""):ev.append((x,p))
        idx=snapshot_index(c);groups=defaultdict(list);excluded=defaultdict(int)
        for er,p in ev:
            d=decisions.get(str(er["decision_key"]))
            if not d:continue
            f=safe(d.get("features_json"));shadow=f.get("historical_candle_shadow") if isinstance(f.get("historical_candle_shadow"),dict) else {}
            hist=shadow.get("history") if isinstance(shadow.get("history"),dict) else {}
            trend50=dec(hist.get("trend_50"))
            q=p.get("quote_used") if isinstance(p.get("quote_used"),dict) else {}
            if trend50 is None:excluded["TREND50_MISSING"]+=1;continue
            if q.get("currency")!="ARS":excluded["NON_ARS"]+=1;continue
            if any(q.get(k) in (None,"","UNKNOWN") for k in IDENTITY):excluded["IDENTITY"]+=1;continue
            ask=dec(q.get("ask"));ask_size=dec(q.get("ask_size"));at=aware(q.get("observed_at") or d.get("decided_at"))
            if ask is None or ask<=0 or ask_size is None or ask_size<=0 or at is None:excluded["ENTRY_BOOK"]+=1;continue
            if session_cutoff(q,at) is None:excluded["SESSION"]+=1;continue
            minute=at.replace(second=0,microsecond=0)
            groups[minute].append({"decision_key":d["decision_key"],"symbol":d["symbol"],"action":str(d.get("action") or "").upper(),
              "at":at,"day":at.astimezone(TZ).date().isoformat(),"trend50":trend50,"ask":ask,"quote":q,
              "spread":dec(f.get("spread"))})
        days=sorted({x["day"] for g in groups.values() for x in g});train=set(days[:-1]);validation=set(days[-1:])
        results={}
        for target in TARGETS:
            for cap in CAPACITIES:
                open_trades=[];done=[];last_exit_by_symbol={}
                for minute,cands in sorted(groups.items()):
                    still=[]
                    for t in open_trades:
                        if t["exit_at"]<=minute:done.append(t);last_exit_by_symbol[t["symbol"]]=t["exit_at"]
                        else:still.append(t)
                    open_trades=still
                    if len(open_trades)>=cap:continue
                    ranked=sorted(cands,key=lambda x:(x["trend50"],x["spread"] if x["spread"] is not None else D(99),x["symbol"],x["decision_key"]))
                    occupied={t["symbol"] for t in open_trades}
                    chosen=None
                    for cand in ranked:
                        if cand["symbol"] not in occupied:
                            chosen=cand;break
                    if chosen is None:continue
                    o=outcome(idx,chosen,target)
                    if o is None:continue
                    open_trades.append({**chosen,**o})
                done.extend(open_trades)
                tr=[x for x in done if x["day"] in train];va=[x for x in done if x["day"] in validation]
                mt=metrics(tr);mv=metrics(va);mall=metrics(done)
                def pf(m):
                    x=dec(m.get("profit_factor"));return x if x is not None else D(0)
                gate={"min_train_n":mt["n"]>=20,"min_validation_n":mv["n"]>=10,
                      "train_avg_positive":dec(mt["avg_unit_return"])>0 if mt["n"] else False,
                      "validation_avg_positive":dec(mv["avg_unit_return"])>0 if mv["n"] else False,
                      "train_pf_gt_1":pf(mt)>1,"validation_pf_gt_1":pf(mv)>1}
                gate["passes_expectancy_gate"]=all(gate.values())
                results[f"TARGET_{target}_CAP_{cap}"]={"target_net":str(target),"capacity":cap,
                  "all":mall,"train":mt,"validation":mv,"gate":gate,
                  "trades":[{k:(str(v) if isinstance(v,D) else v.isoformat() if isinstance(v,datetime) else v)
                             for k,v in t.items() if k not in ("quote",)} for t in done]}
        return {"schema":"POROTA_RC6_TREND50_LOW_SEQUENTIAL_REPLAY_V1","read_only":True,
          "network_calls_performed":False,"broker_calls_performed":False,"factual_writes":False,
          "safety":{"mode":mode,"real_orders_sent":int(orders or 0)},
          "selection":"lowest history_trend50 within each minute; spread only deterministic tie-break",
          "days":days,"train_days":sorted(train),"validation_days":sorted(validation),
          "excluded":dict(excluded),"results":results,
          "limitations":["Unit-return replay, not capital-weighted portfolio PnL.",
            "One new ranked candidate at most per minute; existing symbol positions are not duplicated.",
            "Capacity tests are 1 and 5; no sector or capital contention is modeled here.",
            "Selection uses only point-in-time features; future BID observations are used only for exits."]}
    finally:c.close()
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--db",default="/app/data/paper_v17/observer_v17.db");ap.add_argument("--out")
    a=ap.parse_args();r=build(a.db);raw=json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)
    if a.out:Path(a.out).write_text(raw+"\n",encoding="utf-8")
    print(raw)
if __name__=="__main__":main()
