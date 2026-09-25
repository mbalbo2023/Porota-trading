#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ=ZoneInfo("America/Argentina/Buenos_Aires")
DAYS=("2026-09-24","2026-09-25")

def parse_dt(v):
    if not v: return None
    try:
        x=datetime.fromisoformat(str(v).replace("Z","+00:00"))
        if x.tzinfo is None: x=x.replace(tzinfo=timezone.utc)
        return x
    except Exception:
        return None

def local_day(v):
    x=parse_dt(v)
    return x.astimezone(TZ).date().isoformat() if x else None

def table_exists(c,t):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone())

def columns(c,t):
    if not table_exists(c,t): return set()
    return {str(r[1]) for r in c.execute(f'PRAGMA table_info("{t}")')}

def rows(c,sql,p=()):
    return [dict(r) for r in c.execute(sql,p)]

def recent_rows(c,t,time_col,cols_needed=None):
    cs=columns(c,t)
    if not cs or time_col not in cs: return []
    use=[x for x in (cols_needed or sorted(cs)) if x in cs]
    if not use: return []
    quoted=",".join(f'"{x}"' for x in use)
    sql=f'SELECT {quoted} FROM "{t}" WHERE julianday("{time_col}")>=julianday(?) ORDER BY julianday("{time_col}")'
    try: return rows(c,sql,("2026-09-24T03:00:00+00:00",))
    except sqlite3.Error: return []

def day_counter(data,time_col,key_col):
    out={d:Counter() for d in DAYS}
    for r in data:
        d=local_day(r.get(time_col))
        if d in out:
            out[d][str(r.get(key_col) or "UNKNOWN")]+=1
    return {d:dict(v) for d,v in out.items()}

def summarize_db(db):
    c=sqlite3.connect(f"file:{db}?mode=ro",uri=True,timeout=20)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    try:
        tables=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        result={"tables":tables}

        if table_exists(c,"observer_state"):
            state=rows(c,"SELECT * FROM observer_state WHERE id=1")
            result["observer_state"]=state[0] if state else None

        # positions
        pc=columns(c,"paper_positions")
        if pc:
            wanted=[x for x in ("paper_id","symbol","asset_class","currency","status","opened_at","closed_at","close_reason","net_pnl") if x in pc]
            ps=rows(c,f'SELECT {",".join(wanted)} FROM paper_positions') if wanted else []
            result["positions"]={}
            for day in DAYS:
                opened=[r for r in ps if local_day(r.get("opened_at"))==day]
                closed=[r for r in ps if local_day(r.get("closed_at"))==day]
                result["positions"][day]={
                    "opened":len(opened),"closed":len(closed),
                    "opened_symbols":dict(Counter(str(r.get("symbol") or "UNKNOWN") for r in opened)),
                    "closed_reasons":dict(Counter(str(r.get("close_reason") or "UNKNOWN") for r in closed)),
                    "net_by_currency":{cur:str(sum(float(r.get("net_pnl") or 0) for r in closed if str(r.get("currency") or "UNKNOWN")==cur))
                                       for cur in sorted({str(r.get("currency") or "UNKNOWN") for r in closed})}
                }
            result["open_now"]=sum(str(r.get("status") or "").upper()=="OPEN" for r in ps)

        # decisions
        dc=columns(c,"paper_decisions")
        if dc and "decided_at" in dc:
            wanted=[x for x in ("decision_key","decided_at","symbol","action","score","reason","features_json") if x in dc]
            ds=recent_rows(c,"paper_decisions","decided_at",wanted)
            result["decisions"]={}
            for day in DAYS:
                rr=[r for r in ds if local_day(r.get("decided_at"))==day]
                result["decisions"][day]={
                    "total":len(rr),
                    "actions":dict(Counter(str(r.get("action") or "UNKNOWN") for r in rr)),
                    "reasons_top":Counter(str(r.get("reason") or "UNKNOWN") for r in rr).most_common(20),
                    "symbols":len({str(r.get("symbol") or "") for r in rr if r.get("symbol")}),
                    "buy_symbols":dict(Counter(str(r.get("symbol") or "UNKNOWN") for r in rr if str(r.get("action") or "").upper()=="BUY")),
                    "score_present":sum(r.get("score") not in (None,"") for r in rr),
                }

        # gates
        gc=columns(c,"trade_gate_evaluations")
        if gc and "evaluated_at" in gc:
            wanted=[x for x in ("evaluated_at","decision_key","symbol","final_result","reason","paper_id","detail_json") if x in gc]
            gs=recent_rows(c,"trade_gate_evaluations","evaluated_at",wanted)
            result["gates"]={}
            for day in DAYS:
                rr=[r for r in gs if local_day(r.get("evaluated_at"))==day]
                result["gates"][day]={
                    "total":len(rr),
                    "final_results":dict(Counter(str(r.get("final_result") or "UNKNOWN") for r in rr)),
                    "reasons_top":Counter(str(r.get("reason") or "UNKNOWN") for r in rr).most_common(20),
                    "blocked_symbols":dict(Counter(str(r.get("symbol") or "UNKNOWN") for r in rr if str(r.get("final_result") or "").upper()=="BLOCKED")),
                    "paper_ids":sum(bool(r.get("paper_id")) for r in rr),
                }

        # fills
        fc=columns(c,"paper_fills")
        if fc and "filled_at" in fc:
            wanted=[x for x in ("paper_id","side","filled_at","quantity","price") if x in fc]
            fs=recent_rows(c,"paper_fills","filled_at",wanted)
            result["fills"]={d:dict(Counter(str(r.get("side") or "UNKNOWN") for r in fs if local_day(r.get("filled_at"))==d)) for d in DAYS}

        # events - support possible type column names
        ec=columns(c,"paper_events")
        if ec:
            tcol=next((x for x in ("event_at","created_at","recorded_at","occurred_at","at") if x in ec),None)
            kcol=next((x for x in ("event_type","type","name","kind") if x in ec),None)
            if tcol and kcol:
                ev=recent_rows(c,"paper_events",tcol,[tcol,kcol])
                result["events"]={d:dict(Counter(str(r.get(kcol) or "UNKNOWN") for r in ev if local_day(r.get(tcol))==d)) for d in DAYS}

        # market snapshots
        mc=columns(c,"market_snapshots")
        if mc:
            tcol=next((x for x in ("observed_at","book_at","captured_at") if x in mc),None)
            wanted=[x for x in ("source","observed_at","book_at","symbol","asset_class","market","currency","bid","ask","last") if x in mc]
            if tcol:
                ms=recent_rows(c,"market_snapshots",tcol,wanted)
                result["market_snapshots"]={}
                for day in DAYS:
                    rr=[r for r in ms if local_day(r.get(tcol))==day]
                    by_source=Counter(str(r.get("source") or "UNKNOWN") for r in rr)
                    latest={}
                    for r in rr:
                        src=str(r.get("source") or "UNKNOWN")
                        ts=r.get(tcol)
                        if ts and (src not in latest or str(ts)>str(latest[src])): latest[src]=ts
                    result["market_snapshots"][day]={
                        "total":len(rr),"unique_symbols":len({r.get("symbol") for r in rr if r.get("symbol")}),
                        "by_source":dict(by_source),"latest_by_source":latest,
                        "by_market":dict(Counter(str(r.get("market") or "UNKNOWN") for r in rr)),
                        "by_asset_class":dict(Counter(str(r.get("asset_class") or "UNKNOWN") for r in rr)),
                    }

        # source sync table if present
        if table_exists(c,"source_sync"):
            sc=columns(c,"source_sync")
            wanted=[x for x in ("source","status","count","detail","last_attempt_at","last_success_at") if x in sc]
            try: result["source_sync"]=rows(c,f'SELECT {",".join(wanted)} FROM source_sync ORDER BY source') if wanted else []
            except sqlite3.Error: result["source_sync_error"]="QUERY_FAILED"

        # health-ish tables, only metadata + compact recent rows
        result["health_tables"]=[t for t in tables if "health" in t.lower() or "readiness" in t.lower()]
        for t in result["health_tables"][:8]:
            cs=columns(c,t)
            timecol=next((x for x in ("updated_at","observed_at","checked_at","recorded_at","created_at") if x in cs),None)
            wanted=[x for x in ("name","key","source","state","status","detail","updated_at","observed_at","checked_at") if x in cs]
            if wanted:
                try:
                    q=f'SELECT {",".join(wanted)} FROM "{t}"'
                    if timecol: q+=f' ORDER BY julianday("{timecol}") DESC LIMIT 30'
                    else: q+=" LIMIT 30"
                    result.setdefault("health_samples",{})[t]=rows(c,q)
                except sqlite3.Error:
                    pass

        return result
    finally:
        c.close()

def read_json(path):
    p=Path(path)
    if not p.exists(): return {"exists":False,"path":str(p)}
    out={"exists":True,"path":str(p),"mtime_utc":datetime.fromtimestamp(p.stat().st_mtime,timezone.utc).isoformat()}
    try:
        x=json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        out["parse_error"]=type(e).__name__; return out
    out["schema"]=x.get("schema") or x.get("schema_version")
    out["refreshed_at"]=x.get("refreshed_at")
    out["collected_at"]=x.get("collected_at")
    if p.name=="iol_shadow_latest.json":
        syms=x.get("symbols") if isinstance(x.get("symbols"),list) else []
        out["progress"]=x.get("progress")
        out["primary_comparison_contract"]=x.get("primary_comparison_contract")
        out["symbol_count"]=len(syms)
        out["states"]=dict(Counter(str(r.get("state") or "UNKNOWN") for r in syms if isinstance(r,dict)))
        out["quality"]=dict(Counter(str(r.get("quality") or "UNKNOWN") for r in syms if isinstance(r,dict)))
        out["captured_latest"]=max((str(r.get("captured_at")) for r in syms if isinstance(r,dict) and r.get("captured_at")),default=None)
    elif p.name=="rc6_consolidated_ppi_iol_latest.json":
        rs=x.get("rows") if isinstance(x.get("rows"),list) else []
        out["counts"]=x.get("counts")
        out["source_order"]=x.get("source_order")
        out["decision_effect"]=x.get("decision_effect")
        f=Counter()
        fresh=Counter()
        for r in rs:
            if not isinstance(r,dict): continue
            for _,meta in (r.get("effective_fields") or {}).items():
                if isinstance(meta,dict): f[str(meta.get("source") or "NONE")]+=1
            for src,val in (r.get("freshness") or {}).items(): fresh[f"{src}:{val}"]+=1
        out["effective_field_sources"]=dict(f)
        out["freshness_counts"]=dict(fresh)
    elif p.name=="rc6_public_sources_latest.json":
        ss=x.get("sources") if isinstance(x.get("sources"),list) else []
        out["decision_effect"]=x.get("decision_effect")
        out["sources"]=[{"source":s.get("source"),"status":s.get("status"),"record_count":s.get("record_count"),
                         "observed_at":s.get("observed_at"),"http_status":s.get("http_status")} for s in ss if isinstance(s,dict)]
    elif p.name=="iol_shadow_rotation.json":
        out["rotation"]={k:x.get(k) for k in ("universe_size","cycle_id","next_index","priority_count","background_count","updated_at")}
        out["seen_count"]=len(x.get("seen") or [])
    elif p.name=="rc6_instrument_evidence_latest.json":
        out["generated_at"]=x.get("generated_at")
        out["source_order"]=x.get("source_order")
        out["universe"]=x.get("universe")
        out["counts"]=x.get("counts")
        out["families"]=x.get("families")
        out["decision_effect"]=x.get("decision_effect")
        out["paper_shadow_only"]=x.get("paper_shadow_only")
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--db",default="/opt/porota-trading/data/paper_v17/observer_v17.db")
    ap.add_argument("--market-root",default="/opt/porota-trading/data/market")
    a=ap.parse_args()
    root=Path(a.market_root)
    report={
      "schema":"POROTA_RC6_NO_TRADES_SOURCE_CASCADE_AUDIT_V1",
      "generated_at":datetime.now(timezone.utc).isoformat(),
      "read_only":True,
      "days":list(DAYS),
      "db":summarize_db(a.db),
      "caches":{
        name:read_json(root/name) for name in (
          "iol_shadow_latest.json",
          "iol_shadow_rotation.json",
          "rc6_consolidated_ppi_iol_latest.json",
          "rc6_public_sources_latest.json",
          "rc6_instrument_evidence_latest.json",
          "primary_last.json",
        )
      }
    }
    print(json.dumps(report,ensure_ascii=False,sort_keys=True,default=str))
if __name__=="__main__":
    main()
