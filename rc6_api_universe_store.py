"""SQLite persistence model for API-confirmed PPI identities.

Source/control only until explicitly wired. The store never calls PPI, never sends
orders and never promotes an identity to PAPER. Only rows already validated as
``api_discovered=True`` may be persisted.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_schema(conn: sqlite3.Connection) -> None:
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


def _currency_text(value) -> str:
    if isinstance(value, dict):
        for key in ("description","symbol","name","code"):
            if value.get(key):
                return str(value[key])[:120]
        return ""
    return str(value or "")[:120]


def upsert_discovered(conn: sqlite3.Connection, identity: dict, *, source: str = "PPI_PRODUCTION_API") -> None:
    if not identity.get("api_discovered"):
        raise ValueError("API_UNIVERSE_REQUIRES_DISCOVERED_IDENTITY")
    family=str(identity.get("family") or "").strip().upper()
    ticker=str(identity.get("ticker") or "").strip().upper()
    market=str(identity.get("market") or "").strip().upper()
    if not family or not ticker or not market:
        raise ValueError("API_UNIVERSE_IDENTITY_INCOMPLETE")
    if identity.get("context_only"):
        raise ValueError("CONTEXT_ONLY_CANNOT_BE_API_DISCOVERED")
    if identity.get("paper_candidate"):
        raise ValueError("AUTO_PAPER_PROMOTION_FORBIDDEN")
    ts=now_iso()
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
      api_declared=1,
      api_discovered=1,
      last_seen_at=excluded.last_seen_at
    """,(
      family,ticker,market,str(identity.get("description") or "")[:240],
      _currency_text(identity.get("currency")),str(source)[:80],ts,ts
    ))


def rows(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory=sqlite3.Row
    return [dict(r) for r in conn.execute("SELECT * FROM api_universe_v1 ORDER BY family,ticker,market")]


def assert_no_execution_capability() -> None:
    assert not any(name in globals() for name in ("send_order","place_order","cancel_order"))
