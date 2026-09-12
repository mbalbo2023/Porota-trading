#!/usr/bin/env python3
"""Additive bootstrap of API-confirmed PPI identities into api_universe_v1.

Runs inside the production PAPER observer container. It only uses PPI production
Configuration/SearchInstrument through ProductionMarketReader and the existing
instrument_catalog, then writes an additive metadata table. It never calls order,
account trading, budget, cancel or transfer routes and never promotes PAPER.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from bf_production_paper_observer import _secret
from bd_ppi_readonly_guard import ProductionMarketReader

DB = "/app/data/paper_v17/observer_v17.db"
PLACEHOLDERS = {"", "*", "UNKNOWN", "NONE", "N/A", "NULL"}
ALIASES = {
    "ACCIONES_USA": "ACCIONES-USA",
    "FCI_EXTERIOR": "FCI-EXTERIOR",
    "FCI_LOCAL": "FCI",
    "OBLIGACIONES": "ON",
    "ETFS": "ETF",
}
DIRECTED = (
    ("ACCIONES-USA", "AAPL", "AAPL", "NYSE"),
    ("ACCIONES-USA", "MSFT", "MSFT", "NASDAQ"),
    ("ACCIONES-USA", "NVDA", "NVDA", "NASDAQ"),
    ("ACCIONES-USA", "TSLA", "TSLA", "NYSE"),
    ("ETF", "SPY", "SPY", "NYSE"),
    ("ETF", "IWM", "IWM", "NYSE"),
    ("ETF", "QQQ", "QQQ", "NYSE"),
    ("FCI", "Adcap", "Adcap", "BYMA"),
)


def canon(value):
    raw = str(value or "").strip().upper()
    return ALIASES.get(raw, raw)


def concrete(value):
    return str(value or "").strip().upper() not in PLACEHOLDERS


def currency_text(value):
    if isinstance(value, dict):
        for key in ("description", "symbol", "name", "code"):
            if value.get(key):
                return str(value[key])[:120]
        return ""
    return str(value or "")[:120]


def schema(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS api_universe_v1(
      family TEXT NOT NULL,
      ticker TEXT NOT NULL,
      market TEXT NOT NULL,
      description TEXT NOT NULL DEFAULT '',
      currency TEXT NOT NULL DEFAULT '',
      source TEXT NOT NULL,
      api_declared INTEGER NOT NULL CHECK(api_declared IN (0,1)),
      api_discovered INTEGER NOT NULL CHECK(api_discovered IN (0,1)),
      contract_ready INTEGER NOT NULL DEFAULT 0 CHECK(contract_ready IN (0,1)),
      history_ready INTEGER NOT NULL DEFAULT 0 CHECK(history_ready IN (0,1)),
      simulator_ready INTEGER NOT NULL DEFAULT 0 CHECK(simulator_ready IN (0,1)),
      context_only INTEGER NOT NULL DEFAULT 0 CHECK(context_only IN (0,1)),
      paper_candidate INTEGER NOT NULL DEFAULT 0 CHECK(paper_candidate IN (0,1)),
      first_seen_at TEXT NOT NULL,
      last_seen_at TEXT NOT NULL,
      PRIMARY KEY(family,ticker,market),
      CHECK(NOT (context_only=1 AND api_discovered=1)),
      CHECK(paper_candidate=0)
    )
    """)


def upsert(conn, *, family, ticker, market, description="", currency="", source):
    family, ticker, market = canon(family), str(ticker or "").strip().upper(), str(market or "").strip().upper()
    if not family or not concrete(ticker) or not concrete(market):
        return False
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute("""
    INSERT INTO api_universe_v1(
      family,ticker,market,description,currency,source,
      api_declared,api_discovered,contract_ready,history_ready,simulator_ready,
      context_only,paper_candidate,first_seen_at,last_seen_at
    ) VALUES(?,?,?,?,?,?,1,1,0,0,0,0,0,?,?)
    ON CONFLICT(family,ticker,market) DO UPDATE SET
      description=CASE WHEN excluded.description<>'' THEN excluded.description ELSE api_universe_v1.description END,
      currency=CASE WHEN excluded.currency<>'' THEN excluded.currency ELSE api_universe_v1.currency END,
      source=excluded.source,
      api_declared=1,api_discovered=1,last_seen_at=excluded.last_seen_at
    """, (family,ticker,market,str(description or "")[:240],currency_text(currency),source,ts,ts))
    return True


def main():
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    qc = conn.execute("PRAGMA quick_check").fetchone()[0]
    state = conn.execute("SELECT mode,session_state,real_orders_sent FROM observer_state WHERE id=1").fetchone()
    if qc != "ok" or not state or state[0] != "PRODUCTION_PAPER" or int(state[2] or 0) != 0:
        raise SystemExit("SAFETY_PRECHECK_FAILED")

    reader = ProductionMarketReader(*_secret())
    returned = []
    try:
        reader.login_once()
        cfg = reader.market_configuration()
        api_types = {canon(x) for x in cfg["instrument_types"]}
        api_markets = {str(x).strip().upper() for x in cfg["markets"]}
        for family,ticker,name,market in DIRECTED:
            if canon(family) not in api_types or market not in api_markets:
                continue
            payload = reader.search_instruments(ticker, family, name=name, market=market)
            for raw in payload:
                if not isinstance(raw, dict):
                    continue
                rfam = canon(raw.get("type") or raw.get("instrumentType"))
                rticker = str(raw.get("ticker") or raw.get("symbol") or "").strip().upper()
                rmarket = str(raw.get("market") or "").strip().upper()
                if rfam == canon(family) and rfam in api_types and rmarket == market and rmarket in api_markets and concrete(rticker):
                    returned.append((rfam,rticker,rmarket,raw))
        if reader.metrics["http_blocked"] != 0:
            raise RuntimeError("PPI_HTTP_BLOCKED_NONZERO")
    finally:
        reader.close()

    # Build a transaction only after all read-only PPI calls succeeded.
    schema(conn)
    conn.execute("BEGIN IMMEDIATE")
    catalog_added = 0
    for row in conn.execute("SELECT instrument_type,ticker,description,market,raw_json FROM instrument_catalog"):
        family, market = canon(row["instrument_type"]), str(row["market"] or "").strip().upper()
        if family not in api_types or market not in api_markets or not concrete(row["ticker"]):
            continue
        raw = {}
        try:
            raw = json.loads(row["raw_json"] or "{}")
        except Exception:
            raw = {}
        if upsert(conn,family=family,ticker=row["ticker"],market=market,
                  description=row["description"] or raw.get("description") or "",
                  currency=raw.get("currency") or "",source="PPI_PRODUCTION_API_CATALOG"):
            catalog_added += 1

    directed_added = 0
    for family,ticker,market,raw in returned:
        if upsert(conn,family=family,ticker=ticker,market=market,
                  description=raw.get("description") or "",currency=raw.get("currency") or "",
                  source="PPI_PRODUCTION_API_DIRECTED"):
            directed_added += 1
    conn.commit()

    counts = [dict(r) for r in conn.execute("SELECT family,count(*) n FROM api_universe_v1 GROUP BY family ORDER BY family")]
    total = conn.execute("SELECT count(*) FROM api_universe_v1").fetchone()[0]
    bad = conn.execute("SELECT count(*) FROM api_universe_v1 WHERE api_discovered<>1 OR api_declared<>1 OR context_only<>0 OR paper_candidate<>0").fetchone()[0]
    qc2 = conn.execute("PRAGMA quick_check").fetchone()[0]
    state2 = conn.execute("SELECT mode,session_state,real_orders_sent FROM observer_state WHERE id=1").fetchone()
    conn.close()
    print(json.dumps({"state":"GREEN","catalog_seen":catalog_added,"directed_seen":directed_added,"total":total,"counts":counts,"invalid_rows":bad,"quick_check":qc2,"mode":state2[0],"session":state2[1],"real_orders_sent":state2[2]},sort_keys=True))
    assert qc2 == "ok" and state2[0] == "PRODUCTION_PAPER" and int(state2[2] or 0) == 0
    assert bad == 0
    print("API_UNIVERSE_BOOTSTRAP=GREEN")
    print("AUTO_PAPER_PROMOTION=FALSE")
    print("REAL_ORDER_ROUTES=NOT_CALLED")


if __name__ == "__main__":
    main()
