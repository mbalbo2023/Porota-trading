"""HF6 v2: append-only evidence ledger for historical ingestion attempts.

This module is deliberately separate from the legacy
production_history_attempts table. The legacy table is a latest-state cache
because its primary key is (symbol, instrument_type, settlement) and the
observer writes it with INSERT OR REPLACE. HF6-v2 preserves every attempt so
retry behavior, recovery and source quality remain auditable.

Creating/writing this ledger does not enable trading and must not be used as a
READY_PAPER signal by itself.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any


DDL = """
CREATE TABLE IF NOT EXISTS history_attempt_ledger_v2(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  instrument_type TEXT NOT NULL,
  settlement TEXT NOT NULL,
  source TEXT NOT NULL,
  requested_from TEXT,
  requested_to TEXT,
  attempted_at TEXT NOT NULL,
  completed_at TEXT,
  provider_rows INTEGER NOT NULL DEFAULT 0,
  valid_rows INTEGER NOT NULL DEFAULT 0,
  rejected_rows INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL,
  error_class TEXT,
  detail TEXT NOT NULL DEFAULT '',
  raw_body_hash TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_history_attempt_v2_identity
  ON history_attempt_ledger_v2(symbol,instrument_type,settlement,attempted_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS idx_history_attempt_v2_state
  ON history_attempt_ledger_v2(state,attempted_at DESC,id DESC);
"""


@dataclass(frozen=True)
class HistoryAttempt:
    symbol: str
    instrument_type: str
    settlement: str
    source: str
    attempted_at: str
    state: str
    requested_from: str | None = None
    requested_to: str | None = None
    completed_at: str | None = None
    provider_rows: int = 0
    valid_rows: int = 0
    rejected_rows: int = 0
    error_class: str | None = None
    detail: str = ""
    raw_body_hash: str | None = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self):
        for name in ("symbol","instrument_type","settlement","source","attempted_at","state"):
            value=str(getattr(self,name) or "").strip()
            if not value:
                raise ValueError(f"{name} requerido")
        for name in ("provider_rows","valid_rows","rejected_rows"):
            value=int(getattr(self,name))
            if value < 0:
                raise ValueError(f"{name} no puede ser negativo")
        if self.valid_rows + self.rejected_rows > self.provider_rows:
            raise ValueError("valid_rows + rejected_rows excede provider_rows")


def init_schema(store) -> None:
    with store.connect() as c:
        c.executescript(DDL)


def _canonical(value: Any) -> str:
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str)


def body_hash(payload: Any) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


def append_attempt(store, attempt: HistoryAttempt) -> int:
    """Append one immutable attempt record and return its generated id."""
    metadata=_canonical(attempt.metadata or {})
    with store.connect() as c:
        cur=c.execute("""INSERT INTO history_attempt_ledger_v2(
          symbol,instrument_type,settlement,source,requested_from,requested_to,
          attempted_at,completed_at,provider_rows,valid_rows,rejected_rows,state,
          error_class,detail,raw_body_hash,metadata_json)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (attempt.symbol,attempt.instrument_type,attempt.settlement,attempt.source,
           attempt.requested_from,attempt.requested_to,attempt.attempted_at,
           attempt.completed_at,int(attempt.provider_rows),int(attempt.valid_rows),
           int(attempt.rejected_rows),attempt.state,attempt.error_class,
           str(attempt.detail)[:2000],attempt.raw_body_hash,metadata))
        return int(cur.lastrowid)


def latest_attempt(connection, symbol: str, instrument_type: str, settlement: str):
    return connection.execute("""SELECT * FROM history_attempt_ledger_v2
      WHERE symbol=? AND instrument_type=? AND settlement=?
      ORDER BY attempted_at DESC,id DESC LIMIT 1""",
      (symbol,instrument_type,settlement)).fetchone()


def attempt_count(connection, symbol: str, instrument_type: str, settlement: str) -> int:
    return int(connection.execute("""SELECT COUNT(*) FROM history_attempt_ledger_v2
      WHERE symbol=? AND instrument_type=? AND settlement=?""",
      (symbol,instrument_type,settlement)).fetchone()[0])


def migrate_legacy_latest(store, *, source="PPI_HISTORY", migrated_at=None) -> int:
    """Seed v2 with the latest legacy state once; does not fabricate old retries.

    The legacy table cannot reconstruct overwritten attempts. This migration
    therefore records only the currently visible latest state and marks that
    provenance explicitly in metadata.
    """
    migrated_at=migrated_at or datetime.now(timezone.utc).isoformat()
    init_schema(store)
    inserted=0
    with store.connect() as c:
        exists=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='production_history_attempts'").fetchone()
        if not exists:
            return 0
        rows=c.execute("""SELECT symbol,instrument_type,settlement,attempted_at,
          state,valid_rows,detail FROM production_history_attempts""").fetchall()
        for row in rows:
            duplicate=c.execute("""SELECT 1 FROM history_attempt_ledger_v2
              WHERE symbol=? AND instrument_type=? AND settlement=?
                AND attempted_at=? AND source=? LIMIT 1""",
              (row[0],row[1],row[2],row[3],source)).fetchone()
            if duplicate:
                continue
            c.execute("""INSERT INTO history_attempt_ledger_v2(
              symbol,instrument_type,settlement,source,attempted_at,provider_rows,
              valid_rows,rejected_rows,state,detail,metadata_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (row[0],row[1],row[2],source,row[3],int(row[5] or 0),int(row[5] or 0),0,
               row[4],str(row[6] or "")[:2000],_canonical({
                 "migration":"legacy_latest_only",
                 "migrated_at":migrated_at,
                 "warning":"legacy INSERT OR REPLACE overwrote earlier attempts"
               })))
            inserted+=1
    return inserted
