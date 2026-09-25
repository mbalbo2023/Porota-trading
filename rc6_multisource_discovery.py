"""RC6 dynamic multi-source discovery plan.

Universe authority is not a checked-in ticker list. PPI is queried first with
programmatically generated prefixes; IOL and BYMA caches can add identities or
fill metadata. Discovery does not itself authorize PAPER execution.
"""
from __future__ import annotations
import json, os
from datetime import date
from pathlib import Path
from typing import Any

from bs_instrument_contracts import family_name

PREFIXES=tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
PPI_QUERY_TYPES={
    "ACCIONES":"ACCIONES",
    "CEDEARS":"CEDEARS",
    "ETFS":"ETF",
    "BONOS":"BONOS",
    "LETRAS":"LETRAS",
    "OBLIGACIONES":"ON",
    "OPCIONES":"OPCIONES",
    "FUTUROS":"FUTUROS",
    "CAUCIONES":"CAUCIONES",
    "FCI":"FCI",
}
PPI_MARKETS={
    "ACCIONES":("BYMA",),"CEDEARS":("BYMA",),"ETFS":("BYMA",),
    "BONOS":("BYMA",),"LETRAS":("BYMA",),"OBLIGACIONES":("BYMA",),
    "OPCIONES":("BYMA",),"FUTUROS":("ROFEX","A3"),
    "CAUCIONES":("BYMA",),"FCI":("BYMA",),
}
DEFAULT_SETTLEMENT={
    "ACCIONES":"A-24HS","CEDEARS":"A-24HS","ETFS":"A-24HS",
    "BONOS":"A-24HS","LETRAS":"A-24HS","OBLIGACIONES":"A-24HS",
    "OPCIONES":"INMEDIATA","FUTUROS":"INMEDIATA",
    "CAUCIONES":"INMEDIATA","FCI":"INMEDIATA",
}
MARKET_ALIAS={"BCBA":"BYMA","BYMA":"BYMA","ROFEX":"A3","A3":"A3"}
TERM_ALIAS={"T1":"A-24HS","24HS":"A-24HS","A-24HS":"A-24HS",
            "T0":"INMEDIATA","CI":"INMEDIATA","INMEDIATA":"INMEDIATA"}

def canonical_family(value: Any) -> str:
    try:return family_name(value)
    except ValueError:
        key=str(value or "").strip().upper()
        aliases={"TIT. PUBLICOS":"BONOS","OBL.NEG.":"OBLIGACIONES",
                 "OBL.PYME":"OBLIGACIONES","FONDO COMÚN DE INVERSIÓN":"FCI",
                 "FDOS.INV":"FCI","FONDOS COTIZ.":"ETFS","LETRA / NOTA":"LETRAS"}
        return aliases.get(key,key)

def canonical_market(value: Any) -> str:
    raw=str(value or "").strip().upper()
    return MARKET_ALIAS.get(raw,raw)

def canonical_settlement(value: Any, family: str="") -> str:
    raw=str(value or "").strip().upper()
    if raw:return TERM_ALIAS.get(raw,raw)
    return DEFAULT_SETTLEMENT.get(canonical_family(family),"")

def ppi_query_plan(*, day: date|None=None, prefixes_per_family: int|None=None):
    """Small rotating daily plan; full alphabet coverage accumulates automatically.

    Prefixes are generated code, never a ticker allowlist. A record returned by
    PPI is authoritative evidence of existence; the query prefix is not.
    """
    day=day or date.today()
    width=prefixes_per_family or int(os.getenv("PPI_DISCOVERY_PREFIXES_PER_FAMILY","4"))
    width=max(1,min(width,len(PREFIXES)))
    start=day.toordinal()%len(PREFIXES)
    out=[]
    families=tuple(PPI_QUERY_TYPES)
    for offset,family in enumerate(families):
        base=(start+offset*3)%len(PREFIXES)
        chosen=[PREFIXES[(base+i)%len(PREFIXES)] for i in range(width)]
        for market in PPI_MARKETS[family]:
            for prefix in chosen:
                out.append((prefix,PPI_QUERY_TYPES[family],DEFAULT_SETTLEMENT[family],market,True,family))
    return out

def _rows(path: Path, source: str):
    try:payload=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,ValueError,json.JSONDecodeError):return []
    if source=="IOL":
        values=payload.get("symbols") if isinstance(payload,dict) else []
        out=[]
        for row in values if isinstance(values,list) else []:
            if not isinstance(row,dict):continue
            family=canonical_family(row.get("asset_type") or row.get("family"))
            symbol=str(row.get("symbol") or "").strip().upper()
            if not family or not symbol:continue
            out.append({
                "ticker":symbol,"instrument_type":family,
                "market":canonical_market(row.get("market") or "BYMA"),
                "currency":row.get("currency"),
                "settlement":canonical_settlement(row.get("term"),family),
                "description":str(row.get("description") or ""),
                "units_per_lot":row.get("units_per_lot"),
                "source":"IOL_COMPLEMENTARY","raw":row,
            })
        return out
    if source=="BYMA":
        out=[]
        for block in payload.get("sources",[]) if isinstance(payload,dict) else []:
            if not isinstance(block,dict) or str(block.get("source") or "").upper()!="BYMA":continue
            observed=block.get("observed_at") or payload.get("collected_at")
            for row in block.get("records",[]) if isinstance(block.get("records"),list) else []:
                if not isinstance(row,dict):continue
                family=canonical_family(row.get("family"))
                symbol=str(row.get("symbol") or row.get("ticker") or "").strip().upper()
                if not family or not symbol:continue
                out.append({
                    "ticker":symbol,"instrument_type":family,"market":"BYMA",
                    "currency":row.get("currency"),
                    "settlement":canonical_settlement(row.get("term") or row.get("settlement"),family),
                    "description":str(row.get("description") or ""),
                    "source":"BYMA_PUBLIC_COMPLEMENTARY","observed_at":observed,"raw":row,
                })
        return out
    return []

def _family_reference_rows(path: Path):
    payload=_load(path)
    out=[]
    for raw in payload.get("records",[]) if isinstance(payload,dict) else []:
        if not isinstance(raw,dict): continue
        family=canonical_family(raw.get("instrument_type") or raw.get("family"))
        ticker=str(raw.get("ticker") or raw.get("symbol") or "").strip().upper()
        if not family or not ticker: continue
        row=dict(raw)
        row["ticker"]=ticker
        row["instrument_type"]=family
        row["market"]=canonical_market(raw.get("market") or "BYMA")
        row["settlement"]=canonical_settlement(raw.get("settlement") or raw.get("term"),family)
        row["source"]="IOL_COMPLEMENTARY"
        out.append(row)
    # FCI inventory is discovery evidence, not a complete trading contract.
    for raw in payload.get("fci",[]) if isinstance(payload,dict) else []:
        if not isinstance(raw,dict) or not raw.get("operable"): continue
        ticker=str(raw.get("asset") or "").strip().upper()
        if not ticker: continue
        out.append({
            "ticker":ticker,"instrument_type":"FCI",
            "market":canonical_market(raw.get("market") or "BYMA"),
            "currency":raw.get("currency"),
            "settlement":canonical_settlement("T0","FCI"),
            "description":str(raw.get("description") or ""),
            "source":"IOL_COMPLEMENTARY",
            "fci_type":raw.get("fciType"),
            "raw":dict(raw),
        })
    return out

def _load(path: Path):
    try:
        value=json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError,json.JSONDecodeError):
        return {}

def complementary_discovery(root: Path|str="/app/data/market"):
    root=Path(root)
    values=(
        _rows(root/"iol_shadow_latest.json","IOL")
        + _family_reference_rows(root/"iol_family_reference_latest.json")
        + _rows(root/"rc6_public_sources_latest.json","BYMA")
    )
    unique={}
    priority={"BYMA_PUBLIC_COMPLEMENTARY":1,"IOL_COMPLEMENTARY":2}
    for row in values:
        key=(row["ticker"],row["instrument_type"],row["market"],row.get("settlement") or "")
        current=unique.get(key)
        if current is None or priority.get(str(row.get("source")),0) > priority.get(str(current.get("source")),0):
            unique[key]=row
    return list(unique.values())
