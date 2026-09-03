"""HF6-v2: almacenamiento histórico canónico con identidad financiera completa.

Motivación
----------
El esquema legado ``market_historical_ohlcv`` usa PRIMARY KEY(symbol,date).
Eso puede colisionar al ampliar Porota a múltiples familias, mercados o
settlements. HF6-v2 mantiene el esquema legado intacto y crea un almacén
paralelo, migrable y reversible.

Invariantes
-----------
- identidad = symbol + instrument_type + market + settlement + date;
- cada observación entra primero a history_versions_v2 (append-only);
- history_canonical_v2 conserva una sola vela elegida por identidad/fecha;
- una fuente de menor autoridad nunca pisa una de mayor autoridad;
- adjusted=True prevalece sobre adjusted=False cuando la identidad es la misma;
- ningún dato histórico implica READY_PAPER ni sirve como precio de ejecución.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Iterable


SOURCE_RANK = {
    "PPI_PRODUCTION_HISTORY": 10,
    "PPI_API": 10,
    "BYMA_EOD": 20,
    "BYMA": 20,
    "A3_CEM_CLOSING": 20,
    "IOL": 30,
    "DATA912": 50,
    "DATA912_POROTA_BATCH": 50,
    "YAHOO": 90,
}
DEFAULT_SOURCE_RANK = 100

DDL = """
CREATE TABLE IF NOT EXISTS history_versions_v2(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  instrument_type TEXT NOT NULL,
  market TEXT NOT NULL,
  settlement TEXT NOT NULL,
  date TEXT NOT NULL,
  open REAL,
  high REAL,
  low REAL,
  close REAL NOT NULL,
  volume REAL,
  source TEXT NOT NULL,
  adjusted INTEGER NOT NULL DEFAULT 0,
  observed_at TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_history_versions_v2_identity
  ON history_versions_v2(symbol,instrument_type,market,settlement,date,id DESC);
CREATE INDEX IF NOT EXISTS idx_history_versions_v2_source
  ON history_versions_v2(source,observed_at DESC);

CREATE TABLE IF NOT EXISTS history_canonical_v2(
  symbol TEXT NOT NULL,
  instrument_type TEXT NOT NULL,
  market TEXT NOT NULL,
  settlement TEXT NOT NULL,
  date TEXT NOT NULL,
  open REAL,
  high REAL,
  low REAL,
  close REAL NOT NULL,
  volume REAL,
  source TEXT NOT NULL,
  adjusted INTEGER NOT NULL DEFAULT 0,
  source_rank INTEGER NOT NULL,
  observed_at TEXT NOT NULL,
  version_id INTEGER NOT NULL,
  PRIMARY KEY(symbol,instrument_type,market,settlement,date),
  FOREIGN KEY(version_id) REFERENCES history_versions_v2(id)
);
CREATE INDEX IF NOT EXISTS idx_history_canonical_v2_lookup
  ON history_canonical_v2(instrument_type,market,settlement,symbol,date);
"""


@dataclass(frozen=True)
class Candle:
    symbol: str
    instrument_type: str
    market: str
    settlement: str
    date: str
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: float | None
    source: str
    adjusted: bool = False
    observed_at: str | None = None
    metadata: dict | None = None

    def normalized(self) -> "Candle":
        return Candle(
            str(self.symbol or "").strip().upper(),
            str(self.instrument_type or "").strip().upper(),
            str(self.market or "").strip().upper(),
            str(self.settlement or "").strip().upper(),
            str(self.date or "")[:10],
            None if self.open is None else float(self.open),
            None if self.high is None else float(self.high),
            None if self.low is None else float(self.low),
            float(self.close),
            None if self.volume is None else float(self.volume),
            str(self.source or "").strip().upper(),
            bool(self.adjusted),
            self.observed_at or datetime.now(timezone.utc).isoformat(),
            dict(self.metadata or {}),
        )

    def validate(self) -> None:
        value = self.normalized()
        for field in (value.symbol, value.instrument_type, value.market,
                      value.settlement, value.date, value.source):
            if not field:
                raise ValueError("HISTORY_IDENTITY_INCOMPLETE")
        if value.market == "UNKNOWN" or value.settlement == "UNKNOWN":
            raise ValueError("HISTORY_IDENTITY_UNVERIFIED")
        if value.close <= 0:
            raise ValueError("HISTORY_CLOSE_NONPOSITIVE")
        if value.open is not None and value.open <= 0:
            raise ValueError("HISTORY_OPEN_NONPOSITIVE")
        if value.high is not None and value.high <= 0:
            raise ValueError("HISTORY_HIGH_NONPOSITIVE")
        if value.low is not None and value.low <= 0:
            raise ValueError("HISTORY_LOW_NONPOSITIVE")
        if all(x is not None for x in (value.open, value.high, value.low)):
            if not value.low <= min(value.open, value.close) <= max(value.open, value.close) <= value.high:
                raise ValueError("HISTORY_OHLC_INCONSISTENT")
        if value.volume is not None and value.volume < 0:
            raise ValueError("HISTORY_VOLUME_NEGATIVE")


def init_schema(store) -> None:
    with store.connect() as c:
        c.executescript(DDL)


def _canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def _hash(candle: Candle) -> str:
    value = candle.normalized()
    payload = {
        "symbol": value.symbol,
        "instrument_type": value.instrument_type,
        "market": value.market,
        "settlement": value.settlement,
        "date": value.date,
        "open": value.open,
        "high": value.high,
        "low": value.low,
        "close": value.close,
        "volume": value.volume,
        "source": value.source,
        "adjusted": value.adjusted,
    }
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def source_rank(source: str) -> int:
    source = str(source or "").upper()
    for prefix, rank in SOURCE_RANK.items():
        if source.startswith(prefix):
            return rank
    return DEFAULT_SOURCE_RANK


def _prefer(new: Candle, current) -> bool:
    """Return True only when new evidence may replace current canonical row."""
    if current is None:
        return True
    new = new.normalized()
    current_adjusted = bool(current["adjusted"])
    if new.adjusted != current_adjusted:
        return new.adjusted and not current_adjusted
    new_rank = source_rank(new.source)
    current_rank = int(current["source_rank"])
    if new_rank != current_rank:
        return new_rank < current_rank
    # Same authority: later observation may correct the same provider/date.
    return str(new.observed_at) >= str(current["observed_at"])


def append_candle(store, candle: Candle) -> dict:
    """Append version and update canonical only when source precedence allows."""
    value = candle.normalized()
    value.validate()
    init_schema(store)
    payload_hash = _hash(value)
    metadata_json = _canonical_json(value.metadata or {})
    rank = source_rank(value.source)
    with store.connect() as c:
        cur = c.execute(
            """INSERT INTO history_versions_v2(
              symbol,instrument_type,market,settlement,date,open,high,low,close,
              volume,source,adjusted,observed_at,payload_hash,metadata_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (value.symbol,value.instrument_type,value.market,value.settlement,value.date,
             value.open,value.high,value.low,value.close,value.volume,value.source,
             int(value.adjusted),value.observed_at,payload_hash,metadata_json),
        )
        version_id = int(cur.lastrowid)
        current = c.execute(
            """SELECT * FROM history_canonical_v2
               WHERE symbol=? AND instrument_type=? AND market=? AND settlement=? AND date=?""",
            (value.symbol,value.instrument_type,value.market,value.settlement,value.date),
        ).fetchone()
        chosen = _prefer(value, current)
        if chosen:
            c.execute(
                """INSERT INTO history_canonical_v2(
                  symbol,instrument_type,market,settlement,date,open,high,low,close,
                  volume,source,adjusted,source_rank,observed_at,version_id)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(symbol,instrument_type,market,settlement,date) DO UPDATE SET
                    open=excluded.open,high=excluded.high,low=excluded.low,
                    close=excluded.close,volume=excluded.volume,source=excluded.source,
                    adjusted=excluded.adjusted,source_rank=excluded.source_rank,
                    observed_at=excluded.observed_at,version_id=excluded.version_id""",
                (value.symbol,value.instrument_type,value.market,value.settlement,value.date,
                 value.open,value.high,value.low,value.close,value.volume,value.source,
                 int(value.adjusted),rank,value.observed_at,version_id),
            )
    return {
        "version_id": version_id,
        "canonical_updated": chosen,
        "source_rank": rank,
        "payload_hash": payload_hash,
    }


def append_many(store, candles: Iterable[Candle]) -> dict:
    versions = canonical_updates = 0
    protected = 0
    for candle in candles:
        result = append_candle(store, candle)
        versions += 1
        if result["canonical_updated"]:
            canonical_updates += 1
        else:
            protected += 1
    return {
        "versions_appended": versions,
        "canonical_updates": canonical_updates,
        "protected_by_precedence": protected,
    }


def coverage(connection, *, symbol: str, instrument_type: str,
             market: str, settlement: str) -> dict:
    row = connection.execute(
        """SELECT COUNT(*) n,MIN(date) first_date,MAX(date) last_date,
                  COUNT(DISTINCT source) sources
           FROM history_canonical_v2
           WHERE symbol=? AND instrument_type=? AND market=? AND settlement=?""",
        (str(symbol).upper(),str(instrument_type).upper(),str(market).upper(),
         str(settlement).upper()),
    ).fetchone()
    return {
        "rows": int(row[0] or 0),
        "first_date": row[1],
        "last_date": row[2],
        "sources": int(row[3] or 0),
    }
