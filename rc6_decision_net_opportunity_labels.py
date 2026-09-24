#!/usr/bin/env python3
"""Decision-level executable net-opportunity labels for RC6 PAPER.

Scope:
- read-only SQLite
- no broker/network calls
- no synthetic factual fills
- labels only historical counterfactual opportunity existence
- BUY and HOLD decisions can both be labeled
"""
from __future__ import annotations
import argparse,bisect,hashlib,json,sqlite3
from collections import Counter,defaultdict
from datetime import datetime,timedelta
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

D=Decimal
TZ=ZoneInfo("America/Argentina/Buenos_Aires")
SLIPPAGE=D("0.0002")
PARTICIPATION=D("0.10")
UNIT_QTY=D("1")
NET_TARGETS=(D("0.0025"),D("0.005"),D("0.0075"),D("0.01"))
CENT=D("0.01")
P4=D("0.0001")

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
def sha(x):return hashlib.sha256(canon(x).encode()).hexdigest()

def aware(v):
    try:
        x=datetime.fromisoformat(str(v).replace("Z","+00:00"))
        return x if x.tzinfo is not None else None
    except Exception:return None

def fee_rates(asset_class):
    import au_fee_schedule
    return D(str(au_fee_schedule.costo_por_tramo(asset_class))),D(str(au_fee_schedule.costo_por_tramo_bonificado(asset_class)))

def session_cutoff(q,at):
    try:
        import bq_exit_policy
        pol=bq_exit_policy.PaperSessionPolicy()
        inst={k:q.get(k) for k in ("market","asset_class","settlement")}
        if pol.admission_error(inst,at):
            return None,"SESSION_NOT_ADMISSIBLE"
        _,end=pol.bounds(at)
        cutoff=end-timedelta(minutes=pol.exit_minutes)
        return min(at+timedelta(minutes=120),cutoff),""
    except Exception as exc:
        return None,"SESSION_POLICY_UNAVAILABLE:"+type(exc).__name__

def snapshot_index(c):
    idx=defaultdict(list)
    for r in c.execute("""SELECT id,observed_at,symbol,asset_class,settlement,currency,market,bid,bid_size
      FROM market_snapshots
      ORDER BY symbol,asset_class,settlement,currency,market,julianday(observed_at),id"""):
        at=aware(r["observed_at"])
        if at is None:continue
        key=tuple(str(r[k]) for k in IDENTITY)
        idx[key].append((at,dec(r["bid"]),dec(r["bid_size"])))
    return {k:([x[0] for x in v],v) for k,v in idx.items()}

def future_points(idx,q,start,cutoff):
    key=tuple(str(q[k]) for k in IDENTITY)
    pair=idx.get(key)
    if not pair:return []
    times,series=pair
    lo=bisect.bisect_right(times,start)
    hi=bisect.bisect_right(times,cutoff)
    return series[lo:hi]

def modeled_net(entry_fill,exit_fill,asset_class):
    full,low=fee_rates(asset_class)
    entry_cost=(entry_fill*full).quantize(CENT,rounding=ROUND_HALF_UP)
    exit_cost=(exit_fill*full).quantize(CENT,rounding=ROUND_HALF_UP)
    # Same-day PPI fee rebate on the smaller leg for supported spot families.
    credit=(min(entry_fill,exit_fill)*(full-low)).quantize(CENT,rounding=ROUND_HALF_UP)
    net=exit_fill-entry_fill-entry_cost-exit_cost+credit
    return net,(net/entry_fill if entry_fill else None),entry_cost,exit_cost,credit

def build(db):
    c=sqlite3.connect(f"file:{Path(db)}?mode=ro",uri=True,timeout=30);c.row_factory=sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    try:
        mode,orders=c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if mode!="PRODUCTION_PAPER" or int(orders or 0)!=0:raise RuntimeError("SAFETY_STATE_INVALID")
        decisions=[dict(r) for r in c.execute("SELECT * FROM paper_decisions ORDER BY julianday(decided_at),id")]
        ev={}
        for r in c.execute("SELECT * FROM decision_evidence_snapshots"):
            x=dict(r);p=safe(x.get("payload_json"))
            ev[str(x["decision_key"])]={"payload":p,"hash_valid":bool(p) and sha(p)==str(x.get("payload_sha256") or "")}
        idx=snapshot_index(c)
        rows=[];unknown=Counter();by_action=Counter();by_day=defaultdict(Counter)
        target_counts={str(t):Counter() for t in NET_TARGETS}
        for d in decisions:
            action=str(d.get("action") or "UNKNOWN").upper()
            e=ev.get(str(d.get("decision_key") or ""))
            if not e or not e["hash_valid"]:
                continue
            p=e["payload"];q=p.get("quote_used") if isinstance(p.get("quote_used"),dict) else {}
            missing=[k for k in IDENTITY if q.get(k) in (None,"","UNKNOWN")]
            at=aware(q.get("observed_at") or d.get("decided_at"))
            ask=dec(q.get("ask"));ask_size=dec(q.get("ask_size"))
            if missing or at is None or ask is None or ask<=0 or ask_size is None:
                unknown["ENTRY_EVIDENCE_INCOMPLETE"]+=1;continue
            if ask_size*PARTICIPATION<UNIT_QTY:
                unknown["ENTRY_DEPTH_INSUFFICIENT"]+=1;continue
            cutoff,why=session_cutoff(q,at)
            if cutoff is None or cutoff<=at:
                unknown[why or "NO_HORIZON"]+=1;continue
            pts=future_points(idx,q,at,cutoff)
            entry_fill=(ask*(D(1)+SLIPPAGE)).quantize(P4)
            best=None
            valid_points=0
            for ts,bid,bsize in pts:
                if bid is None or bid<=0 or bsize is None or bsize*PARTICIPATION<UNIT_QTY:continue
                valid_points+=1
                exit_fill=(bid*(D(1)-SLIPPAGE)).quantize(P4)
                try:
                    net,ret,ec,xc,credit=modeled_net(entry_fill,exit_fill,q["asset_class"])
                except Exception:
                    continue
                if ret is None:continue
                if best is None or ret>best["net_return"]:
                    best={"at":ts.isoformat(),"bid":str(bid),"exit_fill":str(exit_fill),"net":str(net),
                          "net_return":ret,"entry_cost":str(ec),"exit_cost":str(xc),"rebate_credit":str(credit)}
            if best is None:
                unknown["NO_EXECUTABLE_FUTURE_PATH"]+=1;continue
            features=safe(d.get("features_json"))
            row={"decision_key":d["decision_key"],"decided_at":d["decided_at"],"as_of":at.isoformat(),
                 "day":at.astimezone(TZ).date().isoformat(),"symbol":d["symbol"],"action":action,
                 "stored_score":d.get("score"),"stored_reason":d.get("reason"),
                 "asset_class":q["asset_class"],"settlement":q["settlement"],"currency":q["currency"],"market":q["market"],
                 "entry_ask":str(ask),"entry_fill":str(entry_fill),"entry_ask_size":str(ask_size),
                 "cutoff":cutoff.isoformat(),"future_valid_points":valid_points,
                 "max_net_return":str(best["net_return"]),"max_net_at":best["at"],
                 "max_net_bid":best["bid"],"max_net_exit_fill":best["exit_fill"],
                 "entry_cost":best["entry_cost"],"exit_cost_at_max":best["exit_cost"],
                 "rebate_credit_at_max":best["rebate_credit"],
                 "momentum":features.get("momentum"),"spread":features.get("spread"),
                 "samples":features.get("samples"),"paper_threshold":features.get("paper_threshold")}
            for t in NET_TARGETS:
                hit=best["net_return"]>=t
                row["opp_"+str(t)]=1 if hit else 0
                target_counts[str(t)]["positive" if hit else "negative"]+=1
                target_counts[str(t)]["BUY_positive" if hit and action=="BUY" else "BUY_negative" if action=="BUY" else "HOLD_positive" if hit else "HOLD_negative"]+=1
            rows.append(row);by_action[action]+=1;by_day[row["day"]][action]+=1
        coverage={"decisions_total":len(decisions),"evidence_snapshots":len(ev),"labeled_rows":len(rows),
                  "labeled_pct":(len(rows)*100/len(decisions) if decisions else 0),
                  "by_action":dict(by_action),"unknown_reasons":dict(unknown)}
        return {"schema":"POROTA_RC6_DECISION_NET_OPPORTUNITY_LABELS_V1","read_only":True,
          "network_calls_performed":False,"broker_calls_performed":False,"factual_fills_created":False,
          "safety":{"mode":mode,"real_orders_sent":int(orders or 0)},
          "assumptions":{"horizon_minutes":120,"session_cutoff":"PaperSessionPolicy EOD exit buffer",
            "entry":"observed ASK plus 2bp slippage, unit quantity, requires 10% participation depth",
            "exit":"best executable observed BID minus 2bp slippage before cutoff, unit quantity, requires 10% participation depth",
            "costs":"canonical au_fee_schedule with same-day PPI rebate model",
            "labels":[str(t) for t in NET_TARGETS]},
          "coverage":coverage,"target_counts":{k:dict(v) for k,v in target_counts.items()},
          "by_day":{k:dict(v) for k,v in sorted(by_day.items())},"rows":rows,
          "limitations":[
            "Labels describe historical executable opportunity, not actual fills or recommendations.",
            "Unit-quantity labels do not model portfolio contention, capital reuse or actual position sizing.",
            "Only decisions with immutable point-in-time quote evidence and an executable future BID path are labeled.",
            "Features missing from historical evidence are not inferred."
          ]}
    finally:c.close()

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--db",default="/app/data/paper_v17/observer_v17.db");ap.add_argument("--out")
    a=ap.parse_args();r=build(a.db);raw=json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)
    if a.out:Path(a.out).write_text(raw+"\n",encoding="utf-8")
    print(raw)
if __name__=="__main__":main()
