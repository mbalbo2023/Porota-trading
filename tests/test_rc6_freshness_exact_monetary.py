"""Regression: depth/freshness must not merge money or count close-only bars."""
from datetime import date, timedelta
import sqlite3

import ek_history_freshness_metrics_rc5 as metrics
from ek_history_freshness_metrics_rc5 import freshness_qualified_metrics


def _observer():
    c=sqlite3.connect(":memory:")
    c.execute("CREATE TABLE candidate_universe(ticker TEXT,instrument_type TEXT,market TEXT,settlement TEXT,status TEXT)")
    c.execute("CREATE TABLE financial_instrument_catalog(ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT)")
    c.execute("INSERT INTO candidate_universe VALUES('X','ACCIONES','BYMA','INMEDIATA','AVAILABLE')")
    c.execute("INSERT INTO financial_instrument_catalog VALUES('X','ACCIONES','BYMA','ARS','INMEDIATA')")
    return c


def _history(path):
    c=sqlite3.connect(path)
    c.execute("""CREATE TABLE history_canonical_v2(
        symbol TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,
        date TEXT,price_basis TEXT,adjustment_basis TEXT,
        open REAL,high REAL,low REAL,close REAL)""")
    return c


def test_ohlc_depth_uses_one_currency_one_basis_and_complete_candles(tmp_path,monkeypatch):
    db=tmp_path/"history.db"
    with _history(db) as c:
        day=date(2026,10,21)
        for n in range(45):
            while day.weekday()>=5: day-=timedelta(days=1)
            currency,price_basis,complete=(
                ("ARS","RAW",n<29) if n<35 else
                ("USD","RAW",True) if n<40 else ("ARS","SPLIT",True)
            )
            c.execute("INSERT INTO history_canonical_v2 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("X","ACCIONES","BYMA",currency,"INMEDIATA",day.isoformat(),
                 price_basis,"RAW_NO_ADJUSTMENT",1 if complete else None,
                 2 if complete else None,1 if complete else None,1.5))
            day-=timedelta(days=1)
    observer=_observer()
    result=freshness_qualified_metrics(observer,db,expected_session_date=date(2026,10,21))
    assert result["available"] and result["fresh_total"]==1
    assert result["fresh_ge30"]==0 and result["fresh_ge90"]==0
    assert result["instrument_rows"][0]["rows"]==29
    assert result["instrument_rows"][0]["currency"]=="ARS"
    assert result["series_basis"]=="RAW/RAW_NO_ADJUSTMENT"
    # A stale store must not nominate its own newest date as the market clock.
    monkeypatch.setattr(metrics,"_expected_completed_session",lambda:date(2026,11,20))
    stale=freshness_qualified_metrics(observer,db)
    assert stale["fresh_total"]==0
    assert stale["expected_session_date"]=="2026-11-20"
    observer.execute("INSERT INTO financial_instrument_catalog VALUES('X','ACCIONES','BYMA','USD','INMEDIATA')")
    ambiguous=freshness_qualified_metrics(observer,db,expected_session_date=date(2026,10,21))
    assert not ambiguous["available"]
    assert ambiguous["reason"]=="TARGET_CURRENCY_AMBIGUOUS_OR_MISSING"


def test_legacy_or_close_only_cannot_mark_fresh_ohlc(tmp_path):
    observer=_observer()
    db=tmp_path/"legacy.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE history_canonical_v2(symbol TEXT,date TEXT)")
        c.execute("INSERT INTO history_canonical_v2 VALUES('X','2026-10-21')")
    assert freshness_qualified_metrics(observer,db)["reason"]=="V2_EXACT_SCHEMA_NOT_PRESENT"
    db2=tmp_path/"close.db"
    with _history(db2) as c:
        c.execute("INSERT INTO history_canonical_v2 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("X","ACCIONES","BYMA","ARS","INMEDIATA","2026-10-21",
                   "RAW","RAW_NO_ADJUSTMENT",None,None,None,1.5))
    result=freshness_qualified_metrics(observer,db2)
    assert result["reason"]=="NO_FULL_OHLC_RAW_SERIES"
    assert result["fresh_ge30"]==0
