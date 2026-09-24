#!/usr/bin/env python3
"""Analyze RC6 trade-master JSON without touching runtime or broker."""
from __future__ import annotations
import argparse, json, math, statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

def num(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except (TypeError,ValueError):
        return None

def med(xs):
    xs=[x for x in xs if x is not None]
    return statistics.median(xs) if xs else None

def corr(xs,ys):
    pairs=[(x,y) for x,y in zip(xs,ys) if x is not None and y is not None]
    if len(pairs)<3: return None
    xv=[x for x,_ in pairs]; yv=[y for _,y in pairs]
    mx=sum(xv)/len(xv); my=sum(yv)/len(yv)
    top=sum((x-mx)*(y-my) for x,y in pairs)
    den=math.sqrt(sum((x-mx)**2 for x in xv)*sum((y-my)**2 for y in yv))
    return top/den if den else None

def enrich(t):
    x=dict(t)
    x["_net"]=num(t.get("net_pnl")); x["_gross"]=num(t.get("gross_pnl"))
    x["_score"]=num(t.get("decision_score")); x["_momentum"]=num(t.get("momentum"))
    x["_spread"]=num(t.get("spread"))
    mm=t.get("mfe_mae") if isinstance(t.get("mfe_mae"),dict) else {}
    x["_mfe"]=num(mm.get("mfe_exec_return")); x["_mae"]=num(mm.get("mae_exec_return"))
    x["_cost"]=(num(t.get("entry_cost")) or 0)+(num(t.get("exit_cost")) or 0)
    q=num(t.get("quantity")); e=num(t.get("entry_price")); m=num(t.get("contract_cash_multiplier")) or 1
    x["_notional"]=q*e*m if q is not None and e is not None else None
    x["_cost_rate"]=x["_cost"]/x["_notional"] if x["_notional"] else None
    post=t.get("post_exit_recovery") if isinstance(t.get("post_exit_recovery"),dict) else {}
    x["_post30"]=num(post.get("max_return_30m")); x["_post60"]=num(post.get("max_return_60m")); x["_post120"]=num(post.get("max_return_120m"))
    try:
        from datetime import datetime, timezone, timedelta
        dt=datetime.fromisoformat(str(t.get("opened_at")).replace("Z","+00:00")).astimezone(timezone(timedelta(hours=-3)))
        x["_hour"]=dt.hour
    except Exception:
        x["_hour"]=None
    return x

def metric(g):
    n=len(g); wins=sum((t["_net"] or 0)>0 for t in g); losses=sum((t["_net"] or 0)<0 for t in g)
    win_sum=sum(t["_net"] for t in g if t["_net"] is not None and t["_net"]>0)
    loss_sum=sum(t["_net"] for t in g if t["_net"] is not None and t["_net"]<0)
    return {
      "n":n,"wins":wins,"losses":losses,"win_rate":wins/n if n else None,
      "net_pnl":sum(t["_net"] or 0 for t in g),
      "gross_pnl":sum(t["_gross"] or 0 for t in g),
      "costs":sum(t["_cost"] for t in g),
      "avg_net_pnl":sum(t["_net"] or 0 for t in g)/n if n else None,
      "profit_factor":win_sum/(-loss_sum) if loss_sum<0 else None,
      "median_score":med([t["_score"] for t in g]),
      "median_spread":med([t["_spread"] for t in g]),
      "median_mfe":med([t["_mfe"] for t in g]),
      "median_mae":med([t["_mae"] for t in g]),
      "median_cost_rate":med([t["_cost_rate"] for t in g]),
      "median_duration_minutes":med([num(t.get("duration_minutes")) for t in g]),
    }

def grouped(trades,keyfn):
    d=defaultdict(list)
    for t in trades: d[str(keyfn(t))].append(t)
    return {k:metric(v) for k,v in sorted(d.items())}

def bands(trades,field,bounds):
    out={}
    for label,lo,hi in bounds:
        g=[t for t in trades if t[field] is not None and t[field]>=lo and t[field]<hi]
        out[label]=metric(g)
    return out

def build(master):
    trades=[enrich(t) for t in master.get("trades",[]) if isinstance(t,dict)]
    currencies=sorted({str(t.get("currency") or "UNKNOWN") for t in trades})
    by_currency={c:metric([t for t in trades if str(t.get("currency") or "UNKNOWN")==c]) for c in currencies}
    ars=[t for t in trades if t.get("currency")=="ARS"]
    losers=[t for t in ars if t["_net"] is not None and t["_net"]<0]
    recent_evidence=[t for t in ars if t.get("evidence_hash_valid") is True]
    gross_positive=[t for t in ars if t["_gross"] is not None and t["_gross"]>0]
    gross_positive_net_negative=[t for t in gross_positive if t["_net"] is not None and t["_net"]<0]
    no_target=sum(t["_mfe"] is not None and t["_mfe"]<0.05 for t in ars)
    econ_false=[t for t in ars if isinstance(t.get("economics"),dict) and t["economics"].get("passed") is False]
    shadow_hold=[t for t in ars if t.get("decision_shadow")=="HOLD"]
    stopped=[t for t in ars if t.get("close_reason")=="STOP_PAPER"]
    stopped_post=[t for t in stopped if t.get("_post120") is not None]
    summary={
      "closed_total":len(trades),
      "by_currency":by_currency,
      "ars": {
        "samples":len(ars),
        "gross_positive_trades":len(gross_positive),
        "gross_positive_but_net_negative":len(gross_positive_net_negative),
        "gross_positive_but_net_negative_net_pnl":sum(t["_net"] or 0 for t in gross_positive_net_negative),
        "target_5pct_reached_by_mfe":sum(t["_mfe"] is not None and t["_mfe"]>=0.05 for t in ars),
        "target_5pct_not_reached_by_mfe":no_target,
        "mfe_above_total_cost_rate":sum(t["_mfe"] is not None and t["_cost_rate"] is not None and t["_mfe"]>t["_cost_rate"] for t in ars),
        "losers_mfe_above_total_cost_rate":sum(t["_mfe"] is not None and t["_cost_rate"] is not None and t["_mfe"]>t["_cost_rate"] for t in losers),
        "mae_breached_2pct":sum(t["_mae"] is not None and t["_mae"]<=-0.02 for t in ars),
        "score_net_pnl_correlation":corr([t["_score"] for t in ars],[t["_net"] for t in ars]),
        "momentum_net_pnl_correlation":corr([t["_momentum"] for t in ars],[t["_net"] for t in ars]),
        "mfe_net_pnl_correlation":corr([t["_mfe"] for t in ars],[t["_net"] for t in ars]),
        "mae_net_pnl_correlation":corr([t["_mae"] for t in ars],[t["_net"] for t in ars]),
        "immutable_evidence_trades":len(recent_evidence),
        "economic_gate_would_fail_count":len(econ_false),
        "economic_gate_would_fail_wins":sum((t["_net"] or 0)>0 for t in econ_false),
        "economic_gate_would_fail_net_pnl":sum(t["_net"] or 0 for t in econ_false),
        "historical_shadow_hold_count":len(shadow_hold),
        "historical_shadow_hold_wins":sum((t["_net"] or 0)>0 for t in shadow_hold),
        "historical_shadow_hold_net_pnl":sum(t["_net"] or 0 for t in shadow_hold),
        "stop_recovery": {
          "stop_trades":len(stopped),
          "post_120m_measured":len(stopped_post),
          "recovered_to_entry_30m":sum(t.get("_post30") is not None and t["_post30"]>=0 for t in stopped),
          "recovered_to_entry_60m":sum(t.get("_post60") is not None and t["_post60"]>=0 for t in stopped),
          "recovered_to_entry_120m":sum(t.get("_post120") is not None and t["_post120"]>=0 for t in stopped),
          "recovered_net_breakeven_120m":sum(t.get("_post120") is not None and t.get("_cost_rate") is not None and t["_post120"]>=t["_cost_rate"] for t in stopped),
          "reached_plus_1pct_120m":sum(t.get("_post120") is not None and t["_post120"]>=0.01 for t in stopped),
          "reached_plus_2pct_120m":sum(t.get("_post120") is not None and t["_post120"]>=0.02 for t in stopped),
          "median_max_return_120m":med([t.get("_post120") for t in stopped_post]),
        },
      },
      "cohorts": {
        "close_reason": grouped(ars,lambda t:t.get("close_reason")),
        "symbol": grouped(ars,lambda t:t.get("symbol")),
        "asset_class": grouped(ars,lambda t:t.get("asset_class")),
        "hour_band": grouped(ars,lambda t: "11-open" if t["_hour"] is not None and t["_hour"]<12 else ("12-15" if t["_hour"] is not None and t["_hour"]<15 else "15-close")),
        "score_band": bands(ars,"_score",[("0.62-0.63",0.62,0.63),("0.63-0.65",0.63,0.65),("0.65-0.67",0.65,0.67),("0.67+",0.67,99)]),
        "spread_band": bands(ars,"_spread",[("<0.10%",0,0.001),("0.10-0.25%",0.001,0.0025),("0.25-0.50%",0.0025,0.005),("0.50-1.00%",0.005,0.01),("1.00%+",0.01,99)]),
        "mfe_band": bands(ars,"_mfe",[("<0%",-99,0),("0-0.5%",0,0.005),("0.5-1%",0.005,0.01),("1-2%",0.01,0.02),("2-5%",0.02,0.05),("5%+",0.05,99)]),
      },
      "threshold_counterfactual": {},
      "coverage": {
        "immutable_decision_evidence":sum(t.get("evidence_hash_valid") is True for t in trades),
        "historical_candle_shadow":sum(isinstance(t.get("historical_candle_shadow"),dict) and bool(t.get("historical_candle_shadow")) for t in trades),
        "iol_snapshot":sum(isinstance(t.get("iol"),dict) and bool(t.get("iol")) for t in trades),
        "mfe_mae":sum((t.get("mfe_mae") or {}).get("status")=="MEDIDO" for t in trades),
      },
      "limitations":[
        "Threshold counterfactual only filters trades that actually opened; it does not model missed opportunities.",
        "Historical SHADOW/IOL conclusions apply only where contemporaneous evidence was persisted.",
        "MFE/MAE uses executable BID observations inside each trade lifetime; it does not claim prices outside that window.",
        "No PnL is combined across currencies."
      ],
    }
    for th in (0.62,0.63,0.64,0.65,0.66,0.67,0.68,0.69,0.70,0.72,0.74):
        g=[t for t in ars if t["_score"] is not None and t["_score"]>=th]
        summary["threshold_counterfactual"][str(th)]=metric(g)
    return summary

def markdown(r):
    a=r["ars"]; ar=r["by_currency"].get("ARS",{})
    lines=["# RC6 Alpha Tuning — Cohort Analysis","",
      "## Verified sample",
      f"- Closed trades: **{r['closed_total']}**.",
      f"- ARS: **{ar.get('n')}** trades, {ar.get('wins')} wins, {ar.get('losses')} losses, net **{ar.get('net_pnl'):.4f} ARS**.",
      f"- Gross ARS PnL before costs: **{ar.get('gross_pnl'):.4f} ARS**; costs: **{ar.get('costs'):.4f} ARS**.",
      f"- Gross-positive ARS trades: **{a['gross_positive_trades']}**; gross-positive but net-negative: **{a['gross_positive_but_net_negative']}**.",
      f"- 5% factual target reached by executable MFE: **{a['target_5pct_reached_by_mfe']}/{a['samples']}**.",
      f"- Economic gate would-fail cohort: **{a['economic_gate_would_fail_count']}** trades, wins **{a['economic_gate_would_fail_wins']}**, net **{a['economic_gate_would_fail_net_pnl']:.4f} ARS**.",
      "",
      "## Signal discrimination",
      f"- Correlation score vs net PnL: **{a['score_net_pnl_correlation']:.4f}**.",
      f"- Correlation momentum vs net PnL: **{a['momentum_net_pnl_correlation']:.4f}**.",
      f"- Correlation MFE vs net PnL: **{a['mfe_net_pnl_correlation']:.4f}**.",
      f"- Correlation MAE vs net PnL: **{a['mae_net_pnl_correlation']:.4f}**.",
      "",
      "## Interpretation constraints",
    ]
    lines += [f"- {x}" for x in r["limitations"]]
    return "\n".join(lines)+"\n"

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("master"); ap.add_argument("--json-out",default="rc6_trade_cohort_analysis.json"); ap.add_argument("--md-out",default="rc6_trade_cohort_analysis.md")
    a=ap.parse_args(); master=json.loads(Path(a.master).read_text(encoding="utf-8")); result=build(master)
    Path(a.json_out).write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    Path(a.md_out).write_text(markdown(result),encoding="utf-8")
    print(json.dumps({"status":"OK","closed_total":result["closed_total"],"ars":result["by_currency"].get("ARS"),"coverage":result["coverage"]},sort_keys=True))
if __name__=="__main__": main()
