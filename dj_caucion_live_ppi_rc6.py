
"""Bounded dynamic PPI caucion book collector for PAPER EOD.

Uses an already-authenticated read-only ProductionMarketReader supplied by
the observer. Dynamic rate/depth are deliberately NOT written to Contract
Evidence v2: normal market movement must never become contract-change review.
"""
from __future__ import annotations
import json
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from bd_ppi_readonly_guard import retry_read
from bs_instrument_contracts import aware_datetime
from rc6_cauciones_contract import parse_ticker, placed_side_book_levels, quote_from_book_level

TZ=ZoneInfo("America/Argentina/Buenos_Aires")
MAX_ROWS=2000
SOURCE="PPI_MARKETDATA_BOOK_READONLY"
POLICY="LIVE_PPI_BID_PARTICIPATION_CAP"

def init_schema(store):
    with store.connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS paper_caucion_live_book(
          ticker TEXT NOT NULL, market TEXT NOT NULL, currency TEXT NOT NULL,
          settlement TEXT NOT NULL, term_days INTEGER NOT NULL,
          quoted_at TEXT, tna_fraction TEXT, available_principal TEXT,
          state TEXT NOT NULL, source TEXT NOT NULL, observed_at TEXT NOT NULL,
          PRIMARY KEY(ticker,market,currency,settlement));
        CREATE TABLE IF NOT EXISTS paper_caucion_live_book_history(
          id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL,
          market TEXT NOT NULL, currency TEXT NOT NULL, settlement TEXT NOT NULL,
          term_days INTEGER NOT NULL, quoted_at TEXT, tna_fraction TEXT,
          available_principal TEXT, state TEXT NOT NULL, source TEXT NOT NULL,
          observed_at TEXT NOT NULL);
        """)

def collection_window(at):
    from di_caucion_cash_sweep_runtime_hf6 import paper_schedule
    local=aware_datetime(at).astimezone(TZ)
    schedule=paper_schedule(local)
    if not schedule:
        return False
    warmup=schedule["sweep_start_at"]-timedelta(minutes=5)
    return warmup <= local <= schedule["order_cutoff_at"]

def _catalog(store):
    with store.connect() as c:
        rows=c.execute("""SELECT ticker,market,currency,settlement,status
          FROM financial_instrument_catalog
          WHERE instrument_type='CAUCIONES' AND status='AVAILABLE'
            AND currency='ARS' AND market='BYMA'
          ORDER BY ticker""").fetchall()
    out=[]
    for row in rows:
        item=dict(row)
        try: ident=parse_ticker(item["ticker"])
        except ValueError: continue
        if ident.currency_prefix!="PESOS" or ident.term_days not in {1,2,7,30,120}: continue
        item["term_days"]=ident.term_days
        out.append(item)
    return out

def _book_row(reader,item,observed_at,now):
    state="NO_PLACED_BID"; quoted_at=None; rate=None; principal=None
    try:
        book=retry_read(lambda: reader.book(item["ticker"],"CAUCIONES",item["settlement"]),retries=1)
        quoted_at=book.get("date") if isinstance(book,dict) else None
        levels=list(placed_side_book_levels(book if isinstance(book,dict) else {}))
        if levels:
            quote=quote_from_book_level(item["ticker"],levels[0])
            at=aware_datetime(quoted_at,"book caución")
            age=(aware_datetime(now).astimezone(at.tzinfo)-at).total_seconds()
            if age < 0: state="FUTURE_BOOK"
            elif age > 120: state="STALE_BOOK"
            elif quote.tna_percent <= 0 or quote.quantity <= 0: state="EMPTY_PLACED_BID"
            else:
                state="BOOK_READY"
                rate=str((quote.tna_percent/Decimal("100")).normalize())
                principal=str(quote.quantity.normalize())
    except Exception as exc:
        state="READ_ERROR:"+type(exc).__name__
    return {**item,"quoted_at":quoted_at,"tna_fraction":rate,
            "available_principal":principal,"state":state,
            "source":SOURCE,"observed_at":observed_at}

def refresh(reader,store,*,now):
    if reader is None or not hasattr(reader,"book"):
        raise ValueError("CAUCION_LIVE_READER_REQUIRED")
    if not collection_window(now):
        return {"state":"OUTSIDE_COLLECTION_WINDOW","records":0,"ready":0,
                "real_routes":[]}
    init_schema(store)
    observed_at=aware_datetime(now).isoformat()
    rows=[_book_row(reader,item,observed_at,now) for item in _catalog(store)]
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        for row in rows:
            values=(row["ticker"],row["market"],row["currency"],row["settlement"],
                    row["term_days"],row["quoted_at"],row["tna_fraction"],
                    row["available_principal"],row["state"],row["source"],row["observed_at"])
            c.execute("""INSERT INTO paper_caucion_live_book VALUES(?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(ticker,market,currency,settlement) DO UPDATE SET
              term_days=excluded.term_days,quoted_at=excluded.quoted_at,
              tna_fraction=excluded.tna_fraction,
              available_principal=excluded.available_principal,state=excluded.state,
              source=excluded.source,observed_at=excluded.observed_at""",values)
            c.execute("""INSERT INTO paper_caucion_live_book_history
              (ticker,market,currency,settlement,term_days,quoted_at,tna_fraction,
               available_principal,state,source,observed_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?)""",values)
        c.execute("""DELETE FROM paper_caucion_live_book_history
          WHERE id NOT IN (SELECT id FROM paper_caucion_live_book_history
          ORDER BY id DESC LIMIT ?)""",(MAX_ROWS,))
    return {"state":"OK","records":len(rows),
            "ready":sum(r["state"]=="BOOK_READY" for r in rows),
            "states":{r["ticker"]:r["state"] for r in rows},
            "real_routes":[]}
