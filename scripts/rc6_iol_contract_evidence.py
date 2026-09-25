#!/usr/bin/env python3
"""Bounded IOL contract-evidence collector for RC6.

Read-only MCP tools only.  It persists provider-backed contract evidence into
Contract Evidence v2 and never changes trading readiness by itself.
"""
from __future__ import annotations
import json, os, re, sqlite3, time
from datetime import datetime, timezone
from pathlib import Path

import cp_contract_evidence_v2_hf6 as ce
import rc6_iol_contract_evidence as norm
from iol_mcp_readonly_adapter_rc6 import OAuthStoreReadOnlyMCP, READ_ONLY_MARKET_TOOLS

DB=os.getenv("POROTA_OBSERVER_DB","/opt/porota-trading/data/paper_v17/observer_v17.db")
STATE=Path(os.getenv("POROTA_IOL_CONTRACT_STATE","/opt/porota-trading/data/market/iol_contract_rotation.json"))
BATCH=max(1,min(int(os.getenv("POROTA_IOL_CONTRACT_BATCH","6")),12))
MIN_INTERVAL=max(300,int(os.getenv("POROTA_IOL_CONTRACT_INTERVAL_SECONDS","900")))
FAMILIES={"BONOS","LETRAS","ON","OBLIGACIONES","OPCIONES","FCI","CAUCIONES","FUTUROS","ETF","ETFS"}

class Store:
    def __init__(self,path): self.path=path
    def connect(self):
        c=sqlite3.connect(self.path,timeout=20);c.row_factory=sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON");return c

def _load_state():
    try:
        x=json.loads(STATE.read_text(encoding="utf-8"))
        return x if isinstance(x,dict) else {}
    except (OSError,ValueError,json.JSONDecodeError): return {}

def _save_state(x):
    STATE.parent.mkdir(parents=True,exist_ok=True)
    p=STATE.with_suffix(".tmp")
    p.write_text(json.dumps(x,sort_keys=True),encoding="utf-8");os.chmod(p,0o644);p.replace(STATE)

def _due(state):
    raw=state.get("updated_at")
    if not raw:return True
    try:
        d=datetime.fromisoformat(str(raw).replace("Z","+00:00"))
        if d.tzinfo is None:d=d.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc)-d).total_seconds()>=MIN_INTERVAL
    except ValueError:return True

def _catalog():
    c=sqlite3.connect(f"file:{DB}?mode=ro",uri=True,timeout=15);c.row_factory=sqlite3.Row;c.execute("PRAGMA query_only=ON")
    rows=[dict(r) for r in c.execute("""SELECT ticker,instrument_type,market,currency,settlement,
      metadata_json,last_seen_at FROM financial_instrument_catalog
      WHERE status='AVAILABLE' ORDER BY instrument_type,ticker,currency,settlement""")]
    c.close()
    result=[]
    seen=set()
    for r in rows:
        fam=str(r["instrument_type"] or "").upper()
        if fam not in FAMILIES: continue
        key=(str(r["ticker"]).upper(),fam,str(r["market"]).upper(),str(r["currency"]).upper())
        if key in seen: continue
        seen.add(key)
        try:r["metadata"]=json.loads(r.pop("metadata_json") or "{}")
        except Exception:r["metadata"]={}
        result.append(r)
    return result

def _call(client,name,args):
    if name not in READ_ONLY_MARKET_TOOLS: raise RuntimeError("IOL_TOOL_NOT_READ_ONLY:"+name)
    value=client.call(name,args)
    time.sleep(1.0)
    return value if isinstance(value,dict) else {}

def _ppi_xhr_integer_proof(store,ticker):
    return norm.ppi_integer_quantity_proven(ce.current_records(store,ticker=ticker))

def _record(store,row,evidence,tools):
    if not evidence:return None
    settlement=evidence.get("settlement") or row.get("settlement") or "UNKNOWN"
    return ce.record_snapshot(
      store,family=row["instrument_type"],ticker=row["ticker"],
      market=evidence.get("market") or row.get("market") or "UNKNOWN",
      settlement=settlement,source_class="IOL_MCP",
      source_ref="IOL_MCP_READ_ONLY:"+",".join(tools),
      evidence=evidence,observed_at=datetime.now(timezone.utc).isoformat())

def _fixed(client,store,row):
    symbol=row["ticker"];ccy=str(row.get("currency") or "ARS").upper()
    iol_ccy="USD" if ccy.startswith("USD") else "ARS"
    info=_call(client,"get_asset_info",{"symbol":symbol,"market":"BCBA"})
    analytics=_call(client,"get_fixed_income_analytics",{"ticker":symbol})
    sim=_call(client,"simulate_fixed_income_by_nominals",{"ticker":symbol,"nominals":1,"currency":iol_ccy})
    ev=norm.fixed_income_evidence(info,analytics,sim,
        integer_quantity_proven=_ppi_xhr_integer_proof(store,symbol))
    return _record(store,row,ev,("get_asset_info","get_fixed_income_analytics","simulate_fixed_income_by_nominals"))

def _option(client,store,row,chains):
    symbol=row["ticker"]
    info=_call(client,"get_asset_info",{"symbol":symbol,"market":"BCBA"})
    underlying=norm.option_underlying_hint(info)
    chain={}
    if underlying:
        if underlying not in chains:
            chains[underlying]=_call(client,"get_options_chain",{"symbol":underlying})
        options=chains[underlying].get("options") if isinstance(chains[underlying],dict) else []
        chain=next((x for x in options or [] if isinstance(x,dict) and str(x.get("symbol") or "").upper()==symbol.upper()),{})
    ev=norm.option_evidence(info,chain,underlying=underlying or "")
    return _record(store,row,ev,("get_asset_info","get_options_chain"))

def _fci(client,store,row,funds):
    symbol=row["ticker"]
    info=_call(client,"get_asset_info",{"symbol":symbol,"market":"BCBA"})
    fund=next((x for x in funds if isinstance(x,dict) and str(x.get("asset") or "").upper()==symbol.upper()),{})
    term=str(info.get("term") or "T0").lower()
    quote=_call(client,"get_asset_quote",{"symbol":symbol,"market":"BCBA","term":term})
    return _record(store,row,norm.fci_evidence(info,fund,quote),("get_fci_funds","get_asset_info","get_asset_quote"))

def _cauciones(client,store):
    payload=_call(client,"get_caucion_rates",{"currency":"ARS","caucion_type":"colocadora"})
    rows=payload.get("rates") or payload.get("items") or []
    saved=0
    for item in rows:
        ev=norm.caucion_evidence(item,currency="ARS")
        if not ev:continue
        days=ev.get("term_days")
        ce.record_snapshot(store,family="CAUCIONES",ticker=f"PESOS{days}",market="BYMA",
          settlement="INMEDIATA",source_class="IOL_MCP",source_ref="IOL_MCP_READ_ONLY:get_caucion_rates",
          evidence=ev,observed_at=datetime.now(timezone.utc).isoformat())
        saved+=1
    return saved

def main():
    state=_load_state()
    if not _due(state):
        print("IOL_CONTRACT_EVIDENCE=NOT_DUE");return 0
    store=Store(DB);ce.init_schema(store)
    rows=_catalog()
    if not rows:
        print("IOL_CONTRACT_EVIDENCE=NO_CATALOG");return 0
    cursor=int(state.get("cursor") or 0)%len(rows)
    selected=[rows[(cursor+i)%len(rows)] for i in range(min(BATCH,len(rows)))]
    client=OAuthStoreReadOnlyMCP();chains={};funds=[];ok=errors=0
    try:
        fund_payload=_call(client,"get_fci_funds",{})
        funds=fund_payload.get("funds") or []
    except Exception: errors+=1
    try: ok+=_cauciones(client,store)
    except Exception: errors+=1
    for row in selected:
        fam=str(row["instrument_type"]).upper()
        try:
            if fam in {"BONOS","LETRAS","ON","OBLIGACIONES"}: _fixed(client,store,row)
            elif fam=="OPCIONES": _option(client,store,row,chains)
            elif fam=="FCI": _fci(client,store,row,funds)
            else:
                # IOL currently exposes no dedicated futures contract endpoint;
                # ETF identity/quote is captured by the ordinary IOL collector.
                continue
            ok+=1
        except Exception:
            errors+=1
    next_cursor=(cursor+len(selected))%len(rows)
    _save_state({"cursor":next_cursor,"catalog_size":len(rows),"updated_at":datetime.now(timezone.utc).isoformat(),
                 "processed":len(selected),"saved":ok,"errors":errors})
    print(f"IOL_CONTRACT_EVIDENCE=COMPLETE processed={len(selected)} saved={ok} errors={errors} cursor={next_cursor}")
    print("REAL_ORDERS_SENT=0")
    return 0

if __name__=="__main__": raise SystemExit(main())
