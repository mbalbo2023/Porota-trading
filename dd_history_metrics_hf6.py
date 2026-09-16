"""Read-only HF6 v2 history metrics for dashboard/introspection.

The denominator is never hardcoded. Target identities come from Porota's
AVAILABLE candidate universe independently of can_simulate. Source capability
is reported separately so HOLD families can accumulate history without being
presented as PAPER-ready.

RC4-HF2 candidate keeps FULL_OHLC and CLOSE_ONLY_PROVIDER_PARTIAL metrics
separate. Close-only evidence never inflates the canonical FULL_OHLC counters.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import os
import sqlite3


OPERATIONAL_HISTORY_FAMILIES = {"ACCIONES", "CEDEARS"}
# Existing PPI and batch history paths are proven only for the current
# operational scope. Legacy rows can be displayed as audit evidence but are
# never considered an enabled collection source.
PPI_PROVEN_HISTORY_FAMILIES = OPERATIONAL_HISTORY_FAMILIES


def _family_scope(families) -> set[str] | None:
    if families is None:
        return None
    return {str(family or "").upper() for family in families}


def target_universe(connection, families=None) -> list[dict]:
    """Read AVAILABLE identities, optionally restricted to an explicit family scope."""
    tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "candidate_universe" not in tables:
        return []
    scope=_family_scope(families)
    result=[]
    for r in connection.execute(
        """SELECT ticker,instrument_type,market,settlement,status
           FROM candidate_universe WHERE status='AVAILABLE'
           ORDER BY instrument_type,ticker,market,settlement"""
    ):
        symbol,family,market,settlement,status=r
        family=str(family or "").upper()
        if scope is not None and family not in scope:
            continue
        result.append({
            "symbol":str(symbol or "").upper(),
            "family":family,
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
    if family in OPERATIONAL_HISTORY_FAMILIES:
        out.append("DATA912_BATCH")
    return tuple(out) if out else ("PROBE_REQUIRED",)


def observer_history_metrics(connection, families=None) -> dict:
    targets=target_universe(connection, families=families)
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


def _close_series_metrics(connection, tables, families=None) -> dict:
    required={"history_close_versions_v1","history_close_canonical_v1"}
    if not required.issubset(tables):
        return {"available":False,"canonical_rows":0,"identities":0,"by_family":{},
                "quality":"CLOSE_ONLY_PROVIDER_PARTIAL"}
    scope=_family_scope(families)
    where="" if scope is None else " WHERE UPPER(instrument_type) IN ("+",".join("?" for _ in scope)+")"
    params=tuple(sorted(scope or ()))
    rows=int(connection.execute("SELECT COUNT(*) FROM history_close_canonical_v1"+where,params).fetchone()[0] or 0)
    identities=int(connection.execute(
        """SELECT COUNT(*) FROM (
           SELECT symbol,instrument_type,market,settlement
           FROM history_close_canonical_v1"""+where+"""
           GROUP BY symbol,instrument_type,market,settlement)""",params
    ).fetchone()[0] or 0)
    by={}
    for r in connection.execute(
        """SELECT instrument_type,COUNT(DISTINCT symbol),COUNT(*),MIN(date),MAX(date)
           FROM history_close_canonical_v1"""+where+""" GROUP BY instrument_type ORDER BY instrument_type""",params
    ):
        by[str(r[0])]={"symbols":int(r[1] or 0),"rows":int(r[2] or 0),
                       "first_date":r[3],"last_date":r[4]}
    return {"available":True,"canonical_rows":rows,"identities":identities,
            "by_family":by,"quality":"CLOSE_ONLY_PROVIDER_PARTIAL",
            "allowed_uses":["close_returns","close_momentum","close_trend"],
            "forbidden_uses":["atr","high_low_range","candlestick_patterns","volume","vwap","execution_price","ready_paper"]}


def v2_store_metrics(path: Path | None = None, families=None) -> dict:
    db=(path or history_db_path()).resolve()
    if not db.exists() or not db.is_file():
        return {"available":False,"path":str(db),"canonical_rows":0,"identities":0,
                "by_family":{},"close_series":{"available":False,"canonical_rows":0,
                "identities":0,"by_family":{},"quality":"CLOSE_ONLY_PROVIDER_PARTIAL"}}
    c=sqlite3.connect("file:"+str(db)+"?mode=ro",uri=True,timeout=5)
    try:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required={"history_versions_v2","history_canonical_v2"}
        close=_close_series_metrics(c,tables,families=families)
        if not required.issubset(tables):
            return {"available":False,"path":str(db),"canonical_rows":0,"identities":0,
                    "by_family":{},"reason":"V2_SCHEMA_NOT_PRESENT","close_series":close}
        scope=_family_scope(families)
        where="" if scope is None else " WHERE UPPER(instrument_type) IN ("+",".join("?" for _ in scope)+")"
        params=tuple(sorted(scope or ()))
        row=c.execute("SELECT COUNT(*) FROM history_canonical_v2"+where,params).fetchone()
        identities=c.execute(
            """SELECT COUNT(*) FROM (
               SELECT symbol,instrument_type,market,settlement
               FROM history_canonical_v2"""+where+"""
               GROUP BY symbol,instrument_type,market,settlement)""",params
        ).fetchone()
        by={}
        for r in c.execute(
            """SELECT instrument_type,COUNT(DISTINCT symbol),COUNT(*),MIN(date),MAX(date)
               FROM history_canonical_v2"""+where+""" GROUP BY instrument_type ORDER BY instrument_type""",params
        ):
            by[str(r[0])]={"symbols":int(r[1] or 0),"rows":int(r[2] or 0),
                           "first_date":r[3],"last_date":r[4]}
        return {"available":True,"path":str(db),"canonical_rows":int(row[0] or 0),
                "full_ohlc_canonical_rows":int(row[0] or 0),
                "identities":int(identities[0] or 0),"by_family":by,
                "full_ohlc_quality":"FULL_OHLC","close_series":close}
    finally:
        c.close()


def legacy_family_metrics(connection, families=None) -> dict:
    tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "production_history" not in tables:return {}
    cols={r[1] for r in connection.execute("PRAGMA table_info(production_history)")}
    if not {"symbol","instrument_type"}.issubset(cols):return {}
    out={}
    scope=_family_scope(families)
    where="" if scope is None else " WHERE UPPER(instrument_type) IN ("+",".join("?" for _ in scope)+")"
    params=tuple(sorted(scope or ()))
    for r in connection.execute("SELECT instrument_type,COUNT(DISTINCT symbol),COALESCE(SUM(row_count),0),MIN(date_from),MAX(date_to) FROM production_history"+where+" GROUP BY instrument_type",params):
        out[str(r[0] or "UNKNOWN").upper()]={"symbols":int(r[1] or 0),"rows":int(r[2] or 0),"first_date":r[3],"last_date":r[4],"identity":"LEGACY_SYMBOL_TYPE"}
    return out


def effective_store_metrics(observer_connection,path=None,families=None):
    v2=v2_store_metrics(path,families=families)
    if v2.get("available"):return dict(v2,layer="V2")
    legacy=legacy_family_metrics(observer_connection,families=families)
    return {"available":False,"path":v2.get("path"),"canonical_rows":sum(x["rows"] for x in legacy.values()),"identities":sum(x["symbols"] for x in legacy.values()),"by_family":legacy,"reason":v2.get("reason") or "V2_UNAVAILABLE","layer":"LEGACY_FALLBACK","close_series":v2.get("close_series",{})}


def assert_history_metric_invariants() -> None:
    if "PROBE_REQUIRED" not in source_capabilities("ON"):
        raise AssertionError("Unproven history family must not be marked supported")
    if source_capabilities("FUTUROS") != ("A3_CEM",):
        raise AssertionError("Futures history capability must remain explicit")