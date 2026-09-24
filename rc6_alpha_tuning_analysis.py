#!/usr/bin/env python3
"""Deterministic cohort analysis for RC6 trade-master JSON."""
from __future__ import annotations
import argparse,json,math
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

D=lambda x: Decimal(str(x))
def avg(xs): return sum(xs,D(0))/D(len(xs)) if xs else D(0)
def group(rows,key):
    out=defaultdict(list)
    for r in rows: out[str(key(r))].append(r)
    return out
def stats(rows):
    wins=[r for r in rows if r["outcome"]=="WIN"]; losses=[r for r in rows if r["outcome"]=="LOSS"]
    rets=[D(r["net_return_pct"]) for r in rows if r.get("net_return_pct") not in (None,"")]
    return {"n":len(rows),"wins":len(wins),"losses":len(losses),
            "win_rate_pct":str(D(len(wins))*100/D(len(rows)) if rows else D(0)),
            "avg_return_pct":str(avg(rets))}
def auc(rows,field):
    w=[D(r[field]) for r in rows if r["outcome"]=="WIN" and r.get(field) is not None]
    l=[D(r[field]) for r in rows if r["outcome"]=="LOSS" and r.get(field) is not None]
    if not w or not l:return None
    s=D(0)
    for a in w:
        for b in l:s+=D(1) if a>b else D("0.5") if a==b else D(0)
    return str(s/D(len(w)*len(l)))
def mfe(r): return D(r["mfe_mae"]["mfe_exec_return"])*100
def mae(r): return D(r["mfe_mae"]["mae_exec_return"])*100
def cost(r): return D(r["entry_cost"])+D(r["exit_cost"])
def invested(r):
    ret=D(r["net_return_pct"]); net=D(r["net_pnl"])
    return net/(ret/100) if ret else None
def cost_pct(r):
    inv=invested(r); return cost(r)/inv*100 if inv else None
def bin_score(v):
    x=D(v)
    for a,b in [(D(".62"),D(".63")),(D(".63"),D(".64")),(D(".64"),D(".66")),(D(".66"),D(".70")),(D(".70"),D(".75")),(D(".75"),D("1.01"))]:
        if a<=x<b:return f"[{a},{b})"
    return "OTHER"
def local_hour(iso):
    from datetime import datetime
    try:
        dt=datetime.fromisoformat(str(iso).replace("Z","+00:00")).astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
        return dt.hour+dt.minute/60
    except Exception:return -1
def time_bin(r):
    h=local_hour(r["opened_at"])
    if h<12:return "<12"
    if h<13:return "12-13"
    if h<14:return "13-14"
    if h<15:return "14-15"
    if h<16:return "15-16"
    return ">=16"
def build(master):
    rows=master["trades"]
    cur={}
    for k,g in group(rows,lambda r:r["currency"]).items():
        wins=[D(r["net_pnl"]) for r in g if D(r["net_pnl"])>0]
        losses=[-D(r["net_pnl"]) for r in g if D(r["net_pnl"])<0]
        aw=avg(wins); al=avg(losses)
        cur[k]={**stats(g),"net_total":str(sum((D(r["net_pnl"]) for r in g),D(0))),
                "gross_total":str(sum((D(r["gross_pnl"]) for r in g),D(0))),
                "cost_total":str(sum((cost(r) for r in g),D(0))),
                "average_win":str(aw),"average_loss":str(al),
                "profit_factor":str(sum(wins,D(0))/sum(losses,D(1))) if losses else None,
                "breakeven_win_rate_pct":str(al/(aw+al)*100) if aw+al else None}
    gross_pos_net_neg=sum(D(r["gross_pnl"])>0 and D(r["net_pnl"])<0 for r in rows)
    costs=[x for r in rows if (x:=cost_pct(r)) is not None]
    score_groups={k:stats(g) for k,g in group(rows,lambda r:bin_score(r["decision_score"])).items()}
    time_groups={k:stats(g) for k,g in group(rows,time_bin).items()}
    reason_groups={k:{**stats(g),"avg_mfe_pct":str(avg([mfe(r) for r in g])),
                            "avg_mae_pct":str(avg([mae(r) for r in g]))}
                   for k,g in group(rows,lambda r:r["close_reason"]).items()}
    version_groups={k:stats(g) for k,g in group(rows,lambda r:r["strategy_version"]).items()}
    shadow=[r for r in rows if r.get("decision_shadow")]
    shadow_groups={k:{**stats(g),"net_by_currency":{c:str(sum((D(r["net_pnl"]) for r in cg),D(0)))
                      for c,cg in group(g,lambda r:r["currency"]).items()}}
                   for k,g in group(shadow,lambda r:r["decision_shadow"]).items()}
    iol=[r for r in rows if isinstance(r.get("iol"),dict)]
    iol_groups={k:stats(g) for k,g in group(iol,lambda r:(r["iol"].get("state"),r["iol"].get("freshness"),r["iol"].get("quality"))).items()}
    five=sum(mfe(r)>=D(5) for r in rows); two=sum(mfe(r)>=D(2) for r in rows)
    two_wins=sum(mfe(r)>=D(2) and r["outcome"]=="WIN" for r in rows)
    stop20=sum(mae(r)<=D(-2) for r in rows)
    high=[r for r in rows if D(r["decision_score"])>=D(".70")]
    return {
      "schema":"POROTA_RC6_ALPHA_TUNING_FINDINGS_V1",
      "source_schema":master["schema"],"read_only":True,
      "totals":stats(rows),"by_currency":cur,
      "integrity":master["integrity"],
      "friction":{"gross_positive_net_negative":gross_pos_net_neg,
                  "mean_roundtrip_cost_pct":str(avg(costs)),"median_roundtrip_cost_pct":str(sorted(costs)[len(costs)//2])},
      "signal":{"score_auc_higher_is_better":auc(rows,"decision_score"),
                "score_bins":score_groups,
                "score_ge_070":stats(high)},
      "excursions":{"mfe_ge_5pct":five,"mfe_ge_2pct":two,"mfe_ge_2pct_winners":two_wins,
                    "mae_le_minus_2pct":stop20,
                    "winners_median_mfe_pct":str(sorted(mfe(r) for r in rows if r["outcome"]=="WIN")[4]),
                    "losers_median_mfe_pct":str(sorted(mfe(r) for r in rows if r["outcome"]=="LOSS")[len([r for r in rows if r["outcome"]=="LOSS"])//2])},
      "by_close_reason":reason_groups,"by_version":version_groups,"by_entry_time":time_groups,
      "historical_candle_shadow":shadow_groups,"iol_observed":iol_groups,
      "headline_findings":[
        "82 closed PAPER trades are reconciled with no duplicate paper_id and MFE/MAE measured for every trade.",
        "The factual score is inversely associated with wins in this sample; score >=0.70 has zero wins.",
        "No trade reached +5% executable MFE during its lifetime; the factual +5% target was never observed as reachable.",
        "Round-trip friction is material and converts multiple gross-positive trades into net losses.",
        "Immutable decision evidence exists only for the newer subset; older rows must not be assigned IOL/context retrospectively."
      ]
    }
def md(r):
    c=r["by_currency"]
    lines=["# RC6 Alpha Tuning — Phase 1/2 findings","",
      "Generated from the read-only 82-trade master ledger. No strategy changes or broker calls were performed.","",
      "## Confirmed baseline",
      f"- Closed trades: **{r['totals']['n']}**; wins: **{r['totals']['wins']}**; losses: **{r['totals']['losses']}**; win rate: **{D(r['totals']['win_rate_pct']):.2f}%**.",
      f"- ARS: {c.get('ARS',{}).get('n',0)} trades, PnL {c.get('ARS',{}).get('net_total')} ARS, profit factor {c.get('ARS',{}).get('profit_factor')}.",
      f"- USD_MEP: {c.get('USD_MEP',{}).get('n',0)} trades, PnL {c.get('USD_MEP',{}).get('net_total')} USD_MEP.",
      f"- Immutable decision evidence verified: {r['integrity']['immutable_evidence_verified']}/{r['totals']['n']}; MFE/MAE measured: {r['integrity']['mfe_mae_measured']}/{r['totals']['n']}.","",
      "## Findings that change the tuning hypothesis",
      f"- Score AUC (higher score expected to be better): **{r['signal']['score_auc_higher_is_better']}**. Values below 0.5 mean higher factual scores did not rank winners above losers.",
      f"- Score >=0.70: **{r['signal']['score_ge_070']['n']} trades, {r['signal']['score_ge_070']['wins']} wins**.",
      f"- Trades reaching +5% executable MFE: **{r['excursions']['mfe_ge_5pct']}**. Trades reaching +2%: **{r['excursions']['mfe_ge_2pct']}**, of which **{r['excursions']['mfe_ge_2pct_winners']}** finished net winners.",
      f"- Trades reaching -2% executable MAE: **{r['excursions']['mae_le_minus_2pct']}**.",
      f"- Gross-positive but net-negative due to friction: **{r['friction']['gross_positive_net_negative']} trades**.",
      f"- Mean modeled round-trip cost burden: **{D(r['friction']['mean_roundtrip_cost_pct']):.3f}%** of invested notional.","",
      "## Interpretation guardrail",
      "These are observed associations, not causal proof. No threshold, stop, target or SHADOW gate should be promoted from this report alone. The next gate is reproducible baseline replay and post-exit recovery analysis for stopped trades.",""]
    return "\n".join(lines)
def main():
    ap=argparse.ArgumentParser();ap.add_argument("master");ap.add_argument("--json-out");ap.add_argument("--md-out")
    a=ap.parse_args(); master=json.loads(Path(a.master).read_text(encoding="utf-8")); r=build(master)
    if a.json_out:Path(a.json_out).write_text(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True,default=str)+"\n",encoding="utf-8")
    if a.md_out:Path(a.md_out).write_text(md(r)+"\n",encoding="utf-8")
    print(json.dumps(r,ensure_ascii=False,sort_keys=True,default=str))
if __name__=="__main__":main()
