"""RC4-HF2 candidate: conserva evidencia histórica de cierre cuando PPI trae OHLC parcial.

Este módulo NO repara ni sintetiza velas. Mantiene una serie paralela de cierre
para filas históricas PPI donde fecha y close son válidos, pero open/high/low
o volumen no alcanzan el contrato FULL_OHLC. La serie paralela nunca concede
READY_PAPER ni se usa como precio de ejecución.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
SOURCE = "PPI_PRODUCTION_HISTORY_CLOSE_ONLY"
QUALITY = "CLOSE_ONLY_PROVIDER_PARTIAL"

DDL = """
CREATE TABLE IF NOT EXISTS history_close_versions_v1(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  instrument_type TEXT NOT NULL,
  market TEXT NOT NULL,
  settlement TEXT NOT NULL,
  date TEXT NOT NULL,
  close REAL NOT NULL,
  source TEXT NOT NULL,
  quality TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  raw_row_hash TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_history_close_version_dedupe_v1
  ON history_close_versions_v1(
    symbol,instrument_type,market,settlement,date,source,quality,raw_row_hash
  );
CREATE INDEX IF NOT EXISTS idx_history_close_versions_identity_v1
  ON history_close_versions_v1(
    symbol,instrument_type,market,settlement,date,observed_at DESC,id DESC
  );
CREATE TABLE IF NOT EXISTS history_close_canonical_v1(
  symbol TEXT NOT NULL,
  instrument_type TEXT NOT NULL,
  market TEXT NOT NULL,
  settlement TEXT NOT NULL,
  date TEXT NOT NULL,
  close REAL NOT NULL,
  source TEXT NOT NULL,
  quality TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  version_id INTEGER NOT NULL,
  PRIMARY KEY(symbol,instrument_type,market,settlement,date),
  FOREIGN KEY(version_id) REFERENCES history_close_versions_v1(id)
);
CREATE INDEX IF NOT EXISTS idx_history_close_canonical_lookup_v1
  ON history_close_canonical_v1(instrument_type,market,settlement,symbol,date);
"""


@dataclass(frozen=True)
class CloseEvidence:
    symbol: str
    instrument_type: str
    market: str
    settlement: str
    date: str
    close: float
    observed_at: str
    rejection_reason: str
    row_index: int
    raw_row: dict


def _canonical(value) -> str:
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str)


def _row_hash(row: Mapping) -> str:
    return sha256(_canonical(dict(row)).encode("utf-8")).hexdigest()


def _parse_dt(value) -> datetime:
    if value in (None, ""):
        raise ValueError("DATE_MISSING")
    try:
        result=datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("DATE_INVALID") from exc
    if result.tzinfo is None:
        result=result.replace(tzinfo=TZ)
    return result


def _positive_close(value) -> float:
    if value in (None, ""):
        raise ValueError("CLOSE_MISSING")
    try:
        result=Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("CLOSE_INVALID") from exc
    if not result.is_finite():
        raise ValueError("CLOSE_NONFINITE")
    if result <= 0:
        raise ValueError("CLOSE_NONPOSITIVE")
    return float(result)


def init_schema(store) -> None:
    with store.connect() as c:
        c.executescript(DDL)


def extract_rejected_close_evidence(payload, rejected_rows, *, symbol: str,
                                    instrument_type: str, market: str,
                                    settlement: str, observed_at: str,
                                    as_of: datetime, date_from=None,
                                    date_to=None) -> tuple[CloseEvidence, ...]:
    """Extrae sólo close+fecha de filas ya rechazadas por FULL_OHLC.

    No rescata filas con errores de fecha o close. Open/high/low y volumen se
    ignoran deliberadamente: no se reinterpretan y nunca se sintetizan.
    """
    if not isinstance(payload,list):
        return ()
    reject_by_index={int(item.index):str(item.reason) for item in rejected_rows}
    if not reject_by_index:
        return ()
    at=as_of if as_of.tzinfo else as_of.replace(tzinfo=TZ)
    seen=set()
    out=[]
    for index in sorted(reject_by_index):
        if index < 0 or index >= len(payload):
            continue
        row=payload[index]
        if not isinstance(row,Mapping):
            continue
        try:
            source_at=_parse_dt(row.get("date"))
            if source_at > at:
                continue
            source_day=source_at.astimezone(TZ).date()
            if date_from is not None and source_day < date_from:
                continue
            if date_to is not None and source_day > date_to:
                continue
            key=source_day.isoformat()
            if key in seen:
                continue
            close=_positive_close(row.get("price"))
        except (ValueError, TypeError, OverflowError):
            continue
        seen.add(key)
        out.append(CloseEvidence(
            str(symbol or "").upper(),str(instrument_type or "").upper(),
            str(market or "").upper(),str(settlement or "").upper(),
            key,close,str(observed_at),reject_by_index[index],int(index),dict(row)
        ))
    return tuple(out)


def append_close_evidence(store, item: CloseEvidence) -> dict:
    for value in (item.symbol,item.instrument_type,item.market,item.settlement,item.date):
        if not str(value or "").strip():
            raise ValueError("HISTORY_CLOSE_IDENTITY_INCOMPLETE")
    if item.market == "UNKNOWN" or item.settlement == "UNKNOWN":
        raise ValueError("HISTORY_CLOSE_IDENTITY_UNVERIFIED")
    if item.close <= 0:
        raise ValueError("HISTORY_CLOSE_NONPOSITIVE")
    init_schema(store)
    digest=_row_hash(item.raw_row)
    metadata=_canonical({
        "rejection_reason_full_ohlc":item.rejection_reason,
        "row_index":item.row_index,
        "ready_paper_implication":"NONE",
        "execution_price_implication":"NONE",
        "open_high_low":"NOT_STORED",
        "volume":"NOT_INTERPRETED",
    })
    with store.connect() as c:
        existing=c.execute(
            """SELECT id,close,observed_at FROM history_close_versions_v1
               WHERE symbol=? AND instrument_type=? AND market=? AND settlement=?
                 AND date=? AND source=? AND quality=? AND raw_row_hash=?
               ORDER BY id DESC LIMIT 1""",
            (item.symbol,item.instrument_type,item.market,item.settlement,item.date,
             SOURCE,QUALITY,digest),
        ).fetchone()
        appended=existing is None
        if existing is None:
            cur=c.execute(
                """INSERT INTO history_close_versions_v1(
                  symbol,instrument_type,market,settlement,date,close,source,quality,
                  observed_at,raw_row_hash,metadata_json)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (item.symbol,item.instrument_type,item.market,item.settlement,item.date,
                 item.close,SOURCE,QUALITY,item.observed_at,digest,metadata),
            )
            version_id=int(cur.lastrowid)
            version_close=float(item.close)
            version_observed_at=str(item.observed_at)
        else:
            version_id=int(existing[0])
            version_close=float(existing[1])
            version_observed_at=str(existing[2])
        current=c.execute(
            """SELECT observed_at,version_id FROM history_close_canonical_v1
               WHERE symbol=? AND instrument_type=? AND market=? AND settlement=? AND date=?""",
            (item.symbol,item.instrument_type,item.market,item.settlement,item.date),
        ).fetchone()
        # If the exact raw row was already preserved, canonical evidence must
        # keep the immutable version's original observed_at. A later retry of
        # identical provider bytes is not a new market observation.
        chosen=current is None or version_observed_at >= str(current[0])
        if chosen:
            c.execute(
                """INSERT INTO history_close_canonical_v1(
                  symbol,instrument_type,market,settlement,date,close,source,quality,
                  observed_at,version_id)
                  VALUES(?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(symbol,instrument_type,market,settlement,date) DO UPDATE SET
                    close=excluded.close,source=excluded.source,quality=excluded.quality,
                    observed_at=excluded.observed_at,version_id=excluded.version_id""",
                (item.symbol,item.instrument_type,item.market,item.settlement,item.date,
                 version_close,SOURCE,QUALITY,version_observed_at,version_id),
            )
    return {"version_id":version_id,"version_appended":appended,
            "canonical_updated":chosen,"quality":QUALITY,"source":SOURCE}


def append_many(store, rows: Iterable[CloseEvidence]) -> dict:
    versions=canonical=0
    for row in rows:
        result=append_close_evidence(store,row)
        versions+=int(bool(result["version_appended"]))
        canonical+=int(bool(result["canonical_updated"]))
    return {"versions_appended":versions,"canonical_updates":canonical}


def coverage(connection, *, symbol: str, instrument_type: str,
             market: str, settlement: str) -> dict:
    row=connection.execute(
        """SELECT COUNT(*) n,MIN(date),MAX(date)
           FROM history_close_canonical_v1
           WHERE symbol=? AND instrument_type=? AND market=? AND settlement=?""",
        (str(symbol).upper(),str(instrument_type).upper(),str(market).upper(),
         str(settlement).upper()),
    ).fetchone()
    return {"rows":int(row[0] or 0),"first_date":row[1],"last_date":row[2]}
