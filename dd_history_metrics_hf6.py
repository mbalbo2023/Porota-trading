"""Read-only HF6 v2 history metrics for dashboard/introspection.

The denominator is never hardcoded. Target identities come from Porota's
AVAILABLE candidate universe independently of can_simulate. Source capability
is reported separately so HOLD families can accumulate history without being
presented as PAPER-ready.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import os
import sqlite3


DATA912_FAMILIES = {"ACCIONES", "CEDEARS", "BONOS"}
CEM_FAMILIES = {"FUTUROS", "OPCIONES"}
# Existing PPI history path is already proven for these families. Other
# families stay PROBE_REQUIRED until a real endpoint/payload is validated.
PPI_PROVEN_HISTORY_FAMILIES = {"ACCIONES", "CEDEARS", "BONOS"}


def target_universe(connection) -> list[dict]:
    tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "candidate_universe" not in tables:
        return []
    result=[]
    for r in connection.execute(
        """SELECT ticker,instrument_type,market,settlement,status
           FROM candidate_universe WHERE status='AVAILABLE'
           ORDER BY instrument_type,ticker,market,settlement"""
    ):
        symbol,family,market,settlement,status=r
        result.append({
            "symbol":str(symbol or "").upper(),
            "family":str(family or "").upper(),
            "market":str(market or "").upper(),
            "settlement":str(settlement or "").upper(),
            "status":str(status or ""),
        })
    return result


def source_capabilities(family: str) -> tuple[str, ...]:
    family=str(family or "").upper()
    out=[]
    if family in PPI_PROVEN_HISTORY_FAMILIES:
        out.append("PPI_HISTORY")
    if family in DATA912_FAMILIES:
        out.append("DATA912_BATCH")
    if family in CEM_FAMILIES:
        out.append("A3_CEM")
    return tuple(out) if out else ("PROBE_REQUIRED",)


def observer_history_metrics(connection) -> dict:
    targets=target_universe(connection)
    by_family=Counter(x["family"] for x in targets)
    capabilities={family:source_capabilities(family) for family in sorted(by_family)}
    tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    attempts=Counter()
    valid_rows=Counter()
    if "production_history_attempts" in tables:
        for r in connection.execute("SELECT instrument_type,state,valid_rows FROM production_history_attempts"):
            attempts[str(r[1] or "UNKNOWN")]+=1
            valid_rows[str(r[0] or "UNKNOWN").upper()]+=int(r[2] or 0)
    return {
        "target_total":len(targets),
        "target_by_family":dict(sorted(by_family.items())),
        "source_capabilities":capabilities,
        "legacy_attempt_states":dict(sorted(attempts.items())),
        "legacy_valid_rows_by_family":dict(sorted(valid_rows.items())),
    }


def history_db_path() -> Path:
    return Path(os.getenv("HIST_DB_PATH","data/market_history.db")).resolve()


def v2_store_metrics(path: Path | None = None) -> dict:
    db=(path or history_db_path()).resolve()
    if not db.exists() or not db.is_file():
        return {"available":False,"path":str(db),"canonical_rows":0,"identities":0,"by_family":{}}
    c=sqlite3.connect("file:"+str(db)+"?mode=ro",uri=True,timeout=5)
    try:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "history_canonical_v2" not in tables:
            return {"available":True,"path":str(db),"canonical_rows":0,"identities":0,"by_family":{}}
        row=c.execute("SELECT COUNT(*) FROM history_canonical_v2").fetchone()
        identities=c.execute(
            """SELECT COUNT(*) FROM (
               SELECT symbol,instrument_type,market,settlement
               FROM history_canonical_v2
               GROUP BY symbol,instrument_type,market,settlement)"""
        ).fetchone()
        by={}
        for r in c.execute(
            """SELECT instrument_type,COUNT(DISTINCT symbol),COUNT(*),MIN(date),MAX(date)
               FROM history_canonical_v2 GROUP BY instrument_type ORDER BY instrument_type"""
        ):
            by[str(r[0])]={"symbols":int(r[1] or 0),"rows":int(r[2] or 0),
                           "first_date":r[3],"last_date":r[4]}
        return {"available":True,"path":str(db),"canonical_rows":int(row[0] or 0),
                "identities":int(identities[0] or 0),"by_family":by}
    finally:
        c.close()


def assert_history_metric_invariants() -> None:
    if "PROBE_REQUIRED" not in source_capabilities("ON"):
        raise AssertionError("Unproven history family must not be marked supported")
    if source_capabilities("FUTUROS") != ("A3_CEM",):
        raise AssertionError("Futures history capability must remain explicit")
