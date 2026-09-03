"""Read-only HF6/RC4 history metrics for dashboard and introspection.

The denominator is never hardcoded. Target identities come from Porota's
AVAILABLE candidate universe independently of ``can_simulate``. Source
capability is reported separately so HOLD families can accumulate history
without being presented as PAPER-ready.

RC4 deliberately keeps two different coverage semantics visible:

* History Store v2 uses the full financial identity
  ``symbol + instrument_type + market + settlement``.
* The proven legacy PPI table is keyed by
  ``symbol + instrument_type + settlement`` and therefore is reported as
  LEGACY coverage, never relabelled as v2 canonical coverage.

A legacy ``market_history.db`` file without the v2 schema is NOT an available
History Store v2. This distinction prevents the dashboard from suppressing
real legacy coverage and displaying false ``0/x`` values.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import os
import sqlite3


DATA912_FAMILIES = {"ACCIONES", "CEDEARS", "BONOS"}
CEM_FAMILIES = {"FUTUROS", "OPCIONES"}
# Existing PPI history path is already proven for these families. Other
# families stay PROBE_REQUIRED until a real endpoint/payload is validated.
PPI_PROVEN_HISTORY_FAMILIES = {"ACCIONES", "CEDEARS", "BONOS"}


def _tables(connection) -> set[str]:
    return {r[0] for r in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def target_universe(connection) -> list[dict]:
    tables = _tables(connection)
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


def _legacy_history_by_family(connection, tables: set[str]) -> dict[str, dict]:
    """Return truthful legacy PPI coverage without pretending v2 identity.

    ``production_history`` has no market column, so these counts are explicitly
    labelled ``LEGACY_SYMBOL_TYPE_SETTLEMENT``. They are useful as a migration
    fallback and observability signal, not as evidence that v2 is populated.
    """
    if "production_history" not in tables:
        return {}
    result={}
    for family, identities, rows, first_date, last_date, last_download in connection.execute(
        """SELECT UPPER(COALESCE(instrument_type,'')),
                  COUNT(*), COALESCE(SUM(row_count),0), MIN(date_from), MAX(date_to),
                  MAX(downloaded_at)
           FROM production_history
           WHERE row_count > 0
           GROUP BY UPPER(COALESCE(instrument_type,''))
           ORDER BY 1"""
    ):
        if not family:
            continue
        result[str(family)]={
            "identities":int(identities or 0),
            "rows":int(rows or 0),
            "first_date":first_date,
            "last_date":last_date,
            "last_download":last_download,
            "identity_semantics":"LEGACY_SYMBOL_TYPE_SETTLEMENT",
            "source":"PPI_PRODUCTION_HISTORY",
        }
    return result


def observer_history_metrics(connection) -> dict:
    targets=target_universe(connection)
    by_family=Counter(x["family"] for x in targets)
    capabilities={family:source_capabilities(family) for family in sorted(by_family)}
    tables=_tables(connection)
    attempts=Counter()
    valid_rows=Counter()
    if "production_history_attempts" in tables:
        for r in connection.execute(
                "SELECT instrument_type,state,valid_rows FROM production_history_attempts"):
            attempts[str(r[1] or "UNKNOWN")]+=1
            valid_rows[str(r[0] or "UNKNOWN").upper()]+=int(r[2] or 0)
    legacy_by_family=_legacy_history_by_family(connection,tables)
    return {
        "target_total":len(targets),
        "target_by_family":dict(sorted(by_family.items())),
        "source_capabilities":capabilities,
        "legacy_attempt_states":dict(sorted(attempts.items())),
        "legacy_valid_rows_by_family":dict(sorted(valid_rows.items())),
        "legacy_history_by_family":legacy_by_family,
        "legacy_identities_total":sum(v["identities"] for v in legacy_by_family.values()),
        "legacy_rows_total":sum(v["rows"] for v in legacy_by_family.values()),
        "legacy_identity_semantics":"LEGACY_SYMBOL_TYPE_SETTLEMENT",
    }


def history_db_path() -> Path:
    return Path(os.getenv("HIST_DB_PATH","data/market_history.db")).resolve()


def v2_store_metrics(path: Path | None = None) -> dict:
    db=(path or history_db_path()).resolve()
    if not db.exists() or not db.is_file():
        return {"available":False,"path":str(db),"canonical_rows":0,
                "identities":0,"by_family":{},"reason":"V2_DB_NOT_PRESENT"}
    c=sqlite3.connect("file:"+str(db)+"?mode=ro",uri=True,timeout=5)
    try:
        tables=_tables(c)
        required={"history_versions_v2","history_canonical_v2"}
        missing=sorted(required-tables)
        if missing:
            return {"available":False,"path":str(db),"canonical_rows":0,
                    "identities":0,"by_family":{},"reason":"V2_SCHEMA_NOT_PRESENT",
                    "missing_tables":missing}
        row=c.execute("SELECT COUNT(*) FROM history_canonical_v2").fetchone()
        identities=c.execute(
            """SELECT COUNT(*) FROM (
               SELECT symbol,instrument_type,market,settlement
               FROM history_canonical_v2
               GROUP BY symbol,instrument_type,market,settlement)"""
        ).fetchone()
        by={}
        for r in c.execute(
            """SELECT instrument_type,
                      COUNT(*) AS canonical_rows,
                      MIN(date),MAX(date)
               FROM history_canonical_v2
               GROUP BY instrument_type ORDER BY instrument_type"""
        ):
            family=str(r[0] or "").upper()
            # Count complete financial identities, not merely DISTINCT symbol.
            count=c.execute(
                """SELECT COUNT(*) FROM (
                     SELECT symbol,instrument_type,market,settlement
                     FROM history_canonical_v2 WHERE instrument_type=?
                     GROUP BY symbol,instrument_type,market,settlement)""",
                (r[0],)).fetchone()[0]
            by[family]={
                "identities":int(count or 0),
                # ``symbols`` retained for compatibility with the HF6 dashboard.
                "symbols":int(count or 0),
                "rows":int(r[1] or 0),
                "first_date":r[2],"last_date":r[3],
                "identity_semantics":"V2_FULL_FINANCIAL_IDENTITY",
            }
        return {"available":True,"path":str(db),
                "canonical_rows":int(row[0] or 0),
                "identities":int(identities[0] or 0),"by_family":by,
                "reason":"V2_SCHEMA_PRESENT",
                "identity_semantics":"V2_FULL_FINANCIAL_IDENTITY"}
    finally:
        c.close()


def effective_family_coverage(observer_metrics: dict, store_v2: dict) -> dict:
    """Select the truthful display layer without conflating legacy with v2.

    v2 wins only when its required schema is actually present. Otherwise the
    dashboard may display legacy PPI coverage as a clearly labelled fallback.
    """
    observer_metrics=dict(observer_metrics or {})
    store_v2=dict(store_v2 or {})
    if store_v2.get("available"):
        return {
            "source":"HISTORY_STORE_V2",
            "identity_semantics":"V2_FULL_FINANCIAL_IDENTITY",
            "by_family":dict(store_v2.get("by_family") or {}),
            "identities_total":int(store_v2.get("identities") or 0),
            "rows_total":int(store_v2.get("canonical_rows") or 0),
            "v2_reason":str(store_v2.get("reason") or "V2_SCHEMA_PRESENT"),
        }
    return {
        "source":"PPI_LEGACY_FALLBACK",
        "identity_semantics":str(observer_metrics.get("legacy_identity_semantics") or
                                 "LEGACY_SYMBOL_TYPE_SETTLEMENT"),
        "by_family":dict(observer_metrics.get("legacy_history_by_family") or {}),
        "identities_total":int(observer_metrics.get("legacy_identities_total") or 0),
        "rows_total":int(observer_metrics.get("legacy_rows_total") or 0),
        "v2_reason":str(store_v2.get("reason") or "V2_UNAVAILABLE"),
    }


def assert_history_metric_invariants() -> None:
    if "PROBE_REQUIRED" not in source_capabilities("ON"):
        raise AssertionError("Unproven history family must not be marked supported")
    if source_capabilities("FUTUROS") != ("A3_CEM",):
        raise AssertionError("Futures history capability must remain explicit")
