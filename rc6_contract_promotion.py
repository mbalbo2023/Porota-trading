"""RC6 contract-to-PAPER promotion gate.

This is the only bridge from multi-source contract evidence to can_simulate.
It never authorizes real money. Generic spot promotion is limited to families
whose existing PaperBroker can price cash/notional correctly. Specialized
families remain explicit until their dedicated simulator is wired.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

import cp_contract_evidence_v2_hf6 as ce
import rc6_multisource_contracts as multi
from bs_instrument_contracts import family_name

FIXED_INCOME={"BONOS","LETRAS","OBLIGACIONES"}
EXISTING_SPOT={"ACCIONES","CEDEARS","ETFS"}

def _present(value):
    return value not in (None,"","UNKNOWN","None")

def ppi_catalog_evidence(record):
    """Canonical identity only; raw nominalInPrice is retained but not interpreted."""
    evidence={}
    for key in ("market","currency","settlement"):
        value=record.get(key)
        if _present(value): evidence[key]=value
    raw=record.get("raw") if isinstance(record.get("raw"),dict) else {}
    if raw.get("nominalInPrice") not in (None,""):
        evidence["ppi_nominalInPrice_raw"]=raw["nominalInPrice"]
    fam=family_name(record.get("instrument_type"))
    if fam in EXISTING_SPOT and str(record.get("market") or "").upper()=="BYMA":
        # Existing audited BYMA spot-unit convention, not a new inference.
        evidence["quantity_step"]=1
    return {"source_class":"PPI_STRUCTURED_API","source_ref":"PPI_SEARCH_INSTRUMENT",
            "observed_at":record.get("last_seen_at"),"evidence":evidence}

def promotion_plan(record, source_records):
    records=[ppi_catalog_evidence(record),*(source_records or [])]
    state=multi.readiness(record.get("instrument_type"),records)
    fam=family_name(record.get("instrument_type"))
    spec=None
    if fam in FIXED_INCOME and state.get("paper_simulatable"):
        spec=multi.fixed_income_instrument_contract(
            record.get("ticker"),fam,records)
    return {"family":fam,"readiness":state,"instrument_contract":spec,
            "promote_spot":bool(spec) or (
                fam in EXISTING_SPOT and str(record.get("capability") or "")=="READY_PAPER_SPOT"
            ),"real_money_authorized":False}

def _table(store):
    with store.connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS multisource_contract_readiness(
          ticker TEXT NOT NULL,instrument_type TEXT NOT NULL,market TEXT NOT NULL,
          currency TEXT NOT NULL,settlement TEXT NOT NULL,status TEXT NOT NULL,
          paper_simulatable INTEGER NOT NULL,contract_complete INTEGER NOT NULL,
          simulator_implemented INTEGER NOT NULL,missing_json TEXT NOT NULL,
          conflicts_json TEXT NOT NULL,provenance_json TEXT NOT NULL,checked_at TEXT NOT NULL,
          PRIMARY KEY(ticker,instrument_type,market,currency,settlement))""")

def promote(store):
    """Persist readiness and promote only proven generic spot contracts."""
    _table(store);ce.init_schema(store)
    with store.connect() as c:
        rows=[dict(r) for r in c.execute("""SELECT * FROM financial_instrument_catalog
          WHERE status='AVAILABLE' ORDER BY instrument_type,ticker,market,currency,settlement""")]
    promoted=ready=pending=conflicts=0
    now=datetime.now(timezone.utc).isoformat()
    for row in rows:
        try: row["raw"]=json.loads(row.get("metadata_json") or "{}")
        except Exception: row["raw"]={}
        source_records=ce.current_records(store,ticker=row["ticker"])
        plan=promotion_plan(row,source_records)
        state=plan["readiness"]
        if state["status"]=="BLOCKED_CONFLICT": conflicts+=1
        elif state.get("paper_simulatable"): ready+=1
        else: pending+=1
        with store.connect() as c:
            c.execute("""INSERT OR REPLACE INTO multisource_contract_readiness
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
              row["ticker"],row["instrument_type"],row["market"],row["currency"],row["settlement"],
              state["status"],int(bool(state.get("paper_simulatable"))),
              int(bool(state.get("contract_complete"))),
              int(bool(state.get("simulator_implemented"))),
              json.dumps(state.get("missing") or [],sort_keys=True),
              json.dumps(state.get("conflicts") or {},sort_keys=True,default=str),
              json.dumps(state.get("provenance") or {},sort_keys=True,default=str),now))
        spec=plan.get("instrument_contract")
        if not spec:
            continue
        fields=state["fields"]
        resolved_settlement=str(fields.get("settlement") or "").upper()
        if not _present(resolved_settlement):
            continue
        raw=dict(row["raw"]);raw["financial_contract_v17"]=spec
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("""INSERT OR REPLACE INTO financial_instrument_catalog
              (ticker,instrument_type,market,currency,settlement,settlement_source,
               description,last_seen_at,run_id,status,capability,metadata_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",(
              row["ticker"],row["instrument_type"],str(fields["market"]).upper(),
              str(fields["currency"]).upper(),resolved_settlement,
              "MULTISOURCE_CONTRACT_V1",row["description"],row["last_seen_at"],
              row["run_id"],"AVAILABLE","READY_PAPER_SPOT",
              json.dumps(raw,ensure_ascii=False,sort_keys=True,default=str)))
            if str(row["settlement"]).upper()!=resolved_settlement:
                c.execute("""UPDATE financial_instrument_catalog SET status='STALE'
                  WHERE ticker=? AND instrument_type=? AND market=? AND currency=? AND settlement=?""",
                  (row["ticker"],row["instrument_type"],row["market"],row["currency"],row["settlement"]))
            c.execute("""INSERT OR REPLACE INTO candidate_universe
              (ticker,instrument_type,settlement,market,can_simulate,status,detail,last_checked_at)
              VALUES(?,?,?,?,1,'AVAILABLE','READY_PAPER_SPOT_MULTISOURCE',?)""",
              (row["ticker"],row["instrument_type"],resolved_settlement,
               str(fields["market"]).upper(),now))
        promoted+=1
    return {"checked":len(rows),"ready":ready,"pending":pending,"conflicts":conflicts,
            "promoted_spot":promoted,"real_money_authorized":False}
