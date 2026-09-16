"""RC5: métricas históricas calificadas por profundidad y freshness.

Sólo lectura. La profundidad por cantidad de barras no equivale a historia
utilizable si el último dato está stale. El calendario BYMA auditado se usa
únicamente dentro de años soportados; nunca se inventan feriados de años no
auditados. Ninguna métrica de este módulo habilita READY_PAPER ni ejecución.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from pathlib import Path
import sqlite3

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


def freshness_qualified_metrics(observer_connection, path: Path | None = None, families=None) -> dict:
    """Read-only freshness metrics for an explicitly bounded dashboard scope."""
    targets=target_universe(observer_connection, families=families)
    db=(path or history_db_path()).resolve()
    base={
        "available":False,
        "target_total":len(targets),
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
    c=sqlite3.connect("file:"+str(db)+"?mode=ro",uri=True,timeout=5)
    c.execute("PRAGMA query_only=ON")
    try:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "history_canonical_v2" not in tables:
            return dict(base,reason="HISTORY_V2_UNAVAILABLE")
        latest_raw=c.execute("SELECT MAX(date) FROM history_canonical_v2").fetchone()[0]
        if not latest_raw:
            return dict(base,reason="HISTORY_STORE_EMPTY")
        latest=date.fromisoformat(str(latest_raw)[:10])
        if latest.year not in byma_calendar.ANIOS_AUDITADOS:
            return dict(base,reason="LATEST_CALENDAR_YEAR_NOT_AUDITED",store_latest_date=latest.isoformat())

        groups={}
        for r in c.execute(
            """SELECT symbol,instrument_type,market,settlement,COUNT(*),MIN(date),MAX(date)
               FROM history_canonical_v2
               GROUP BY symbol,instrument_type,market,settlement"""
        ):
            groups[tuple(str(x or "").upper() for x in r[:4])]=(int(r[4] or 0),r[5],r[6])

        depths=Counter(); freshness=Counter(); stale_ge90=[]
        fresh_ge30=fresh_ge90=fresh_ge180=fresh_total=0
        for item in targets:
            key=(item["symbol"],item["family"],item["market"],item["settlement"])
            rows,first,last=groups.get(key,(0,None,None))
            dep=_depth(rows)
            last_day=date.fromisoformat(str(last)[:10]) if last else None
            fr,gap=_freshness(last_day,latest,item["family"])
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
                    "symbol":item["symbol"],"family":item["family"],
                    "market":item["market"],"settlement":item["settlement"],
                    "rows":rows,"first_date":first,"last_date":last,
                    "freshness":fr,"business_gap":gap,
                })

        return dict(
            base,
            available=True,
            reason="OK",
            store_latest_date=latest.isoformat(),
            fresh_total=fresh_total,
            fresh_ge30=fresh_ge30,
            fresh_ge90=fresh_ge90,
            fresh_ge180=fresh_ge180,
            stale_ge90=stale_ge90,
            stale_ge90_count=len(stale_ge90),
            depth_counts=dict(sorted(depths.items())),
            freshness_counts=dict(sorted(freshness.items())),
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
