"""RC5: métricas históricas calificadas por profundidad y freshness.

Sólo lectura. La profundidad por cantidad de barras no equivale a historia
utilizable si el último dato está stale. El calendario BYMA auditado se usa
únicamente dentro de años soportados; nunca se inventan feriados de años no
auditados. Ninguna métrica de este módulo habilita READY_PAPER ni ejecución.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import ak_byma_calendar as byma_calendar
from am_us_equity_calendar_rc6 import us_equity_business_day
from dd_history_metrics_hf6 import history_db_path, target_universe


def _depth(rows: int) -> str:
    if rows <= 0:
        return "NONE"
    if rows < 30:
        return "LT30"
    if rows < 90:
        return "LT90"
    if rows < 180:
        return "LT180"
    return "GE180"


def _business_gap_2026(last_day: date, latest: date, family: str = "ACCIONES") -> int:
    if last_day >= latest:
        return 0
    if last_day.year not in byma_calendar.ANIOS_AUDITADOS or latest.year not in byma_calendar.ANIOS_AUDITADOS:
        raise ValueError("HISTORY_FRESHNESS_CALENDAR_NOT_AUDITED")
    d=last_day+timedelta(days=1)
    count=0
    guard=0
    while d <= latest:
        guard += 1
        if guard > 400:
            raise RuntimeError("HISTORY_FRESHNESS_GUARD_EXCEEDED")
        byma_open = byma_calendar.es_dia_habil_operativo(d)
        underlying_open = str(family or "").upper() not in {"CEDEAR", "CEDEARS"} or us_equity_business_day(d)
        if byma_open and underlying_open:
            count += 1
        d += timedelta(days=1)
    return count


def _freshness(last_day: date | None, latest: date, family: str = "ACCIONES") -> tuple[str, str]:
    if last_day is None:
        return "NO_HISTORY", "NA"
    if last_day > latest:
        return "FUTURE_ANOMALY", "NEGATIVE"
    if last_day.year not in byma_calendar.ANIOS_AUDITADOS:
        return "STALE_UNVERIFIED_CALENDAR", "UNVERIFIED_PRE2026"
    gap=_business_gap_2026(last_day,latest,family)
    if gap <= 2:
        return "FRESH", str(gap)
    if gap <= 10:
        return "STALE", str(gap)
    return "SEVERELY_STALE", str(gap)


def _expected_completed_session() -> date:
    """Use an independent market clock, never the newest stored candle."""
    now=datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))
    day=now.date() if now.time()>=time(18,0) else now.date()-timedelta(days=1)
    for _ in range(10):
        if day.year not in byma_calendar.ANIOS_AUDITADOS:
            return day  # caller marks the unaudited calendar NO_VERIFICADO
        if byma_calendar.es_dia_habil_operativo(day):
            return day
        day-=timedelta(days=1)
    raise ValueError("HISTORY_EXPECTED_SESSION_UNVERIFIED")


def freshness_qualified_metrics(observer_connection, path: Path | None = None, families=None, *, expected_session_date: date | None = None) -> dict:
    """Read-only freshness metrics for an explicitly bounded dashboard scope.

    expected_session_date makes the pre-open audit compare each instrument with
    the last session that should already be present, rather than silently using
    the newest date found in the store as its own benchmark.
    """
    targets=target_universe(observer_connection, families=families)
    partial_keys={
        (item["symbol"],item["family"],item["market"],item["settlement"])
        for item in targets
    }
    db=(path or history_db_path()).resolve()
    base={
        "available":False,
        "target_total":len(partial_keys),
        "fresh_ge30":0,
        "fresh_ge90":0,
        "fresh_ge180":0,
        "fresh_total":0,
        "stale_ge90":[],
        "stale_ge90_count":0,
        "depth_counts":{},
        "freshness_counts":{},
        "readiness_implication":"NONE",
        "execution_price_implication":"NONE",
    }
    if not db.exists() or not db.is_file():
        return dict(base,reason="HISTORY_DB_UNAVAILABLE")
    tables={r[0] for r in observer_connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "financial_instrument_catalog" not in tables:
        return dict(base,reason="TARGET_CURRENCY_UNVERIFIED")
    catalog_columns={r[1] for r in observer_connection.execute(
        "PRAGMA table_info(financial_instrument_catalog)")}
    if not {"ticker","instrument_type","market","currency","settlement"} <= catalog_columns:
        return dict(base,reason="TARGET_CURRENCY_UNVERIFIED")
    currencies={}
    for symbol,family,market,currency,settlement in observer_connection.execute(
            "SELECT ticker,instrument_type,market,currency,settlement FROM financial_instrument_catalog"):
        key=tuple(str(x or "").upper() for x in (symbol,family,market,settlement))
        if key in partial_keys:
            currencies.setdefault(key,set()).add(str(currency or "").upper())
    unresolved=sum(1 for key in partial_keys if len(currencies.get(key,set()))!=1
                   or next(iter(currencies[key])) in {"","UNKNOWN"})
    if unresolved:
        return dict(base,reason="TARGET_CURRENCY_AMBIGUOUS_OR_MISSING",
                    unresolved_target_total=unresolved)
    c=sqlite3.connect("file:"+str(db)+"?mode=ro",uri=True,timeout=5)
    c.execute("PRAGMA query_only=ON")
    try:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "history_canonical_v2" not in tables:
            return dict(base,reason="HISTORY_V2_UNAVAILABLE")
        columns={r[1] for r in c.execute("PRAGMA table_info(history_canonical_v2)")}
        if not {"symbol","instrument_type","market","currency","settlement",
                "date","price_basis","adjustment_basis","open","high","low","close"} <= columns:
            return dict(base,reason="V2_EXACT_SCHEMA_NOT_PRESENT")
        # Only this explicitly named RAW series and complete OHLC rows can
        # contribute to a FULL_OHLC depth/freshness card.
        basis=("RAW","RAW_NO_ADJUSTMENT")
        eligible_sql="""price_basis=? AND adjustment_basis=? AND
            open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL AND
            close IS NOT NULL"""
        latest_raw=c.execute(
            "SELECT MAX(date) FROM history_canonical_v2 WHERE "+eligible_sql,basis
        ).fetchone()[0]
        if not latest_raw:
            return dict(base,reason="NO_FULL_OHLC_RAW_SERIES")
        latest=date.fromisoformat(str(latest_raw)[:10])
        expected = expected_session_date or _expected_completed_session()
        if latest.year not in byma_calendar.ANIOS_AUDITADOS or expected.year not in byma_calendar.ANIOS_AUDITADOS:
            return dict(base,reason="FRESHNESS_CALENDAR_YEAR_NOT_AUDITED",store_latest_date=latest.isoformat(),expected_session_date=expected.isoformat())

        groups={}
        for r in c.execute(
            """SELECT symbol,instrument_type,market,currency,settlement,COUNT(*),MIN(date),MAX(date)
               FROM history_canonical_v2
               WHERE """+eligible_sql+"""
               GROUP BY symbol,instrument_type,market,currency,settlement""",basis
        ):
            groups[tuple(str(x or "").upper() for x in r[:5])]=(int(r[5] or 0),r[6],r[7])

        depths=Counter(); freshness=Counter(); stale_ge90=[]; instrument_rows=[]
        fresh_ge30=fresh_ge90=fresh_ge180=fresh_total=0
        for partial in sorted(partial_keys):
            symbol,family,market,settlement=partial
            currency=next(iter(currencies[partial]))
            key=(symbol,family,market,currency,settlement)
            rows,first,last=groups.get(key,(0,None,None))
            dep=_depth(rows)
            last_day=date.fromisoformat(str(last)[:10]) if last else None
            fr,gap=_freshness(last_day,expected,family)
            instrument_rows.append({
                "symbol":symbol,"family":family,"market":market,"currency":currency,
                "settlement":settlement,"price_basis":basis[0],"adjustment_basis":basis[1],
                "rows": rows, "last_date": last,
                "expected_session_date": expected.isoformat(), "freshness": fr, "business_gap": gap,
            })
            depths[dep]+=1
            freshness[fr]+=1
            if fr == "FRESH":
                fresh_total += 1
                if rows >= 30:
                    fresh_ge30 += 1
                if rows >= 90:
                    fresh_ge90 += 1
                if rows >= 180:
                    fresh_ge180 += 1
            if rows >= 90 and fr != "FRESH":
                stale_ge90.append({
                    "symbol":symbol,"family":family,"market":market,"currency":currency,
                    "settlement":settlement,"price_basis":basis[0],"adjustment_basis":basis[1],
                    "rows":rows,"first_date":first,"last_date":last,
                    "freshness":fr,"business_gap":gap,
                })

        return dict(
            base,
            available=True,
            reason="OK",
            store_latest_date=latest.isoformat(),
            expected_session_date=expected.isoformat(),
            fresh_total=fresh_total,
            fresh_ge30=fresh_ge30,
            fresh_ge90=fresh_ge90,
            fresh_ge180=fresh_ge180,
            stale_ge90=stale_ge90,
            stale_ge90_count=len(stale_ge90),
            depth_counts=dict(sorted(depths.items())),
            freshness_counts=dict(sorted(freshness.items())),
            instrument_rows=sorted(instrument_rows, key=lambda row: (str(row["freshness"]) == "FRESH", str(row["symbol"]))),
            identity_key="symbol/instrument_type/market/currency/settlement",
            series_basis="RAW/RAW_NO_ADJUSTMENT",
            minimum_quality="FULL_OHLC",
            calendar_source="BYMA_AUDITED_PLUS_US_UNDERLYING_FOR_CEDEARS",
        )
    finally:
        c.close()


def assert_freshness_metric_contract() -> None:
    assert _depth(0)=="NONE"
    assert _depth(29)=="LT30"
    assert _depth(30)=="LT90"
    assert _depth(90)=="LT180"
    assert _depth(180)=="GE180"
    assert _freshness(None,date(2026,9,4))[0]=="NO_HISTORY"
    assert _freshness(date(2025,12,9),date(2026,9,4))[0]=="STALE_UNVERIFIED_CALENDAR"
    assert _business_gap_2026(date(2026,9,4),date(2026,9,8),"CEDEARS") == 1
