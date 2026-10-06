"""RC4-HF2 candidate: conserva evidencia histórica de cierre cuando PPI trae OHLC parcial.

Este módulo NO repara ni sintetiza velas. Mantiene una serie paralela de cierre
para filas históricas PPI donde fecha y close son válidos, pero open/high/low
o volumen no alcanzan el contrato FULL_OHLC. La serie paralela nunca concede
READY_PAPER ni se usa como precio de ejecución.
"""
from __future__ import annotations

from dataclasses import dataclass,replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo
from cu_history_store_v2_hf6 import Candle,instant,utc,resolve_legacy_currency,initialize_history_schema
from bs_instrument_contracts import cash_currency

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
SOURCE = "PPI_PRODUCTION_HISTORY_CLOSE_ONLY"
QUALITY = "CLOSE_ONLY_PROVIDER_PARTIAL"

DDL = """
CREATE TABLE IF NOT EXISTS history_close_versions_v1(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  instrument_type TEXT NOT NULL,
  market TEXT NOT NULL,
  currency TEXT NOT NULL,
  settlement TEXT NOT NULL,
  date TEXT NOT NULL,
  close REAL NOT NULL,
  source TEXT NOT NULL,
  quality TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  raw_row_hash TEXT NOT NULL,
  previous_version_id INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_history_close_version_dedupe_v1
  ON history_close_versions_v1(
    symbol,instrument_type,market,currency,settlement,date,source,quality,previous_version_id,raw_row_hash
  );
CREATE INDEX IF NOT EXISTS idx_history_close_versions_identity_v1
  ON history_close_versions_v1(
    symbol,instrument_type,market,currency,settlement,date,observed_at DESC,id DESC
  );
CREATE TABLE IF NOT EXISTS history_close_canonical_v1(
  symbol TEXT NOT NULL,
  instrument_type TEXT NOT NULL,
  market TEXT NOT NULL,
  currency TEXT NOT NULL,
  settlement TEXT NOT NULL,
  date TEXT NOT NULL,
  close REAL NOT NULL,
  source TEXT NOT NULL,
  quality TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  version_id INTEGER NOT NULL,
  PRIMARY KEY(symbol,instrument_type,market,currency,settlement,date),
  FOREIGN KEY(version_id) REFERENCES history_close_versions_v1(id)
);
CREATE INDEX IF NOT EXISTS idx_history_close_canonical_lookup_v1
  ON history_close_canonical_v1(instrument_type,market,currency,settlement,symbol,date);
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
    currency: str = ""


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
    def apply(c):
        for table in ('history_close_versions_v1','history_close_canonical_v1'):
            columns={r[1] for r in c.execute('PRAGMA table_info('+table+')')}
            if columns and ('currency' not in columns or (table=='history_close_versions_v1' and 'previous_version_id' not in columns)):
                raise ValueError('HISTORY_CLOSE_COPY_MIGRATION_REQUIRED')
        for statement in DDL.split(';'):
            if statement.strip(): c.execute(statement)
    initialize_history_schema(store,apply)


def extract_rejected_close_evidence(payload, rejected_rows, *, symbol: str,
                                    instrument_type: str, market: str,
                                    settlement: str, observed_at: str,
                                    as_of: datetime, date_from=None,
                                    date_to=None,currency=None) -> tuple[CloseEvidence, ...]:
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
        reason=reject_by_index[index]
        # Close salvage is restricted to incomplete OHLC/volume. Identity,
        # availability and conflicting daily revisions cannot be repaired by
        # selecting an arbitrary price from the rejected payload.
        if not reason.startswith(('OPEN_','HIGH_','LOW_','OHLC_','VOLUME_',
                                  'HISTORY_OPEN_','HISTORY_HIGH_','HISTORY_LOW_',
                                  'HISTORY_OHLC_','HISTORY_VOLUME_')):
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
        item=CloseEvidence(
            str(symbol or "").upper(),str(instrument_type or "").upper(),
            str(market or "").upper(),str(settlement or "").upper(),
            key,close,utc(observed_at),reject_by_index[index],int(index),dict(row),cash_currency(currency)
        )
        try:
            Candle(item.symbol,item.instrument_type,item.market,item.settlement,item.date,
                None,None,None,item.close,None,SOURCE,currency=item.currency,
                observed_at=item.observed_at).validate()
        except ValueError:
            continue
        out.append(item)
    return tuple(out)


def append_close_evidence(store, item: CloseEvidence, *, connection=None) -> dict:
    for value in (item.symbol,item.instrument_type,item.market,item.currency,item.settlement,item.date):
        if not str(value or "").strip():
            raise ValueError("HISTORY_CLOSE_IDENTITY_INCOMPLETE")
    if item.market == "UNKNOWN" or item.settlement == "UNKNOWN":
        raise ValueError("HISTORY_CLOSE_IDENTITY_UNVERIFIED")
    normalized=Candle(item.symbol,item.instrument_type,item.market,item.settlement,item.date,
           None,None,None,item.close,None,SOURCE,observed_at=item.observed_at,
           currency=item.currency,price_basis='RAW').normalized()
    normalized.validate()
    item=replace(item,symbol=normalized.symbol,instrument_type=normalized.instrument_type,
                 market=normalized.market,currency=normalized.currency,settlement=normalized.settlement,
                 date=normalized.date,close=normalized.close,observed_at=normalized.observed_at)
    if connection is None:
        init_schema(store)
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            try:
                result=append_close_evidence(store,item,connection=c)
                c.commit()
                return result
            except BaseException:
                c.rollback();raise
    digest=_row_hash(item.raw_row)
    metadata=_canonical({
        "rejection_reason_full_ohlc":item.rejection_reason,
        "row_index":item.row_index,
        "ready_paper_implication":"NONE",
        "execution_price_implication":"NONE",
        "open_high_low":"NOT_STORED",
        "volume":"NOT_INTERPRETED",
        "currency":item.currency,
        "copy_migration_currency":item.raw_row.get('copy_migration_currency'),
    })
    c=connection
    if c is not None:
        existing=c.execute(
            """SELECT id,close,observed_at FROM history_close_versions_v1
               WHERE symbol=? AND instrument_type=? AND market=? AND currency=? AND settlement=?
                 AND date=? AND source=? AND quality=?
               ORDER BY observed_at DESC,id DESC LIMIT 1""",
            (item.symbol,item.instrument_type,item.market,item.currency,item.settlement,item.date,
             SOURCE,QUALITY),
        ).fetchone()
        appended=existing is None or float(existing['close'])!=float(item.close)
        if existing and appended:
            if item.observed_at<existing['observed_at']:raise ValueError('HISTORY_CLOSE_REVISION_BACKDATED')
            if item.observed_at==existing['observed_at']:raise ValueError('HISTORY_CLOSE_REVISION_TIME_CONFLICT')
        if appended:
            cur=c.execute(
                """INSERT INTO history_close_versions_v1(
                  symbol,instrument_type,market,currency,settlement,date,close,source,quality,
                  observed_at,raw_row_hash,previous_version_id,metadata_json)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (item.symbol,item.instrument_type,item.market,item.currency,item.settlement,item.date,
                 item.close,SOURCE,QUALITY,item.observed_at,digest,int(existing['id']) if existing else 0,metadata),
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
               WHERE symbol=? AND instrument_type=? AND market=? AND currency=? AND settlement=? AND date=?""",
            (item.symbol,item.instrument_type,item.market,item.currency,item.settlement,item.date),
        ).fetchone()
        # If the exact raw row was already preserved, canonical evidence must
        # keep the immutable version's original observed_at. A later retry of
        # identical provider bytes is not a new market observation.
        chosen=current is None or (appended and version_observed_at > str(current[0]))
        if chosen:
            c.execute(
                """INSERT INTO history_close_canonical_v1(
                  symbol,instrument_type,market,currency,settlement,date,close,source,quality,
                  observed_at,version_id)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(symbol,instrument_type,market,currency,settlement,date) DO UPDATE SET
                    close=excluded.close,source=excluded.source,quality=excluded.quality,
                    observed_at=excluded.observed_at,version_id=excluded.version_id""",
                (item.symbol,item.instrument_type,item.market,item.currency,item.settlement,item.date,
                 version_close,SOURCE,QUALITY,version_observed_at,version_id),
            )
    return {"version_id":version_id,"version_appended":appended,
            "canonical_updated":chosen,"quality":QUALITY,"source":SOURCE}


def append_many(store, rows: Iterable[CloseEvidence]) -> dict:
    rows=list(rows)
    if not rows: return {"versions_appended":0,"canonical_updates":0}
    if len(rows)>10000: raise ValueError('HISTORY_BATCH_ROW_BUDGET_EXHAUSTED')
    init_schema(store)
    versions=canonical=0
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        try:
            for row in rows:
                result=append_close_evidence(store,row,connection=c)
                versions+=int(bool(result["version_appended"]))
                canonical+=int(bool(result["canonical_updated"]))
            c.commit()
        except BaseException:
            c.rollback();raise
    return {"versions_appended":versions,"canonical_updates":canonical}


def migrate_legacy_tables(connection, *, currency_map, max_rows=1000000, check=lambda:None,
                          mapping_digest=None):
    """Migrate close-only tables in an already isolated destination copy."""
    columns={r[1] for r in connection.execute('PRAGMA table_info(history_close_versions_v1)')}
    if not columns or {'currency','previous_version_id'}<=columns:
        return {'migrated_rows':0,'quarantined_rows':0}
    records=[dict(r) for r in connection.execute('SELECT * FROM history_close_versions_v1 LIMIT ?', (max_rows+1,))]
    if len(records)>max_rows:raise ValueError('HISTORY_MIGRATION_ROW_BUDGET_EXHAUSTED')
    check()
    connection.execute('ALTER TABLE history_close_canonical_v1 RENAME TO history_close_canonical_v1_legacy')
    connection.execute('ALTER TABLE history_close_versions_v1 RENAME TO history_close_versions_v1_legacy')
    for index in ('idx_history_close_version_dedupe_v1','idx_history_close_versions_identity_v1','idx_history_close_canonical_lookup_v1'):
        connection.execute('DROP INDEX IF EXISTS '+index)
    for statement in DDL.split(';'):
        if statement.strip():connection.execute(statement)
    connection.execute('CREATE TABLE history_close_migration_quarantine_v2(old_version_id INTEGER PRIMARY KEY,reason TEXT NOT NULL)')
    connection.execute('CREATE TABLE history_close_migration_map_v2(old_version_id INTEGER PRIMARY KEY,new_version_id INTEGER NOT NULL)')
    migrated=quarantined=0
    def ordering(row):
        try:return (utc(row['observed_at']),row['id'])
        except ValueError:return ('',row['id'])
    for row in sorted(records,key=ordering):
        check()
        try:
            meta=json.loads(row['metadata_json'] or '{}')
            if not isinstance(meta,dict):raise ValueError('HISTORY_METADATA_INVALID')
            key=tuple(str(row[f] or '').strip().upper() for f in ('symbol','instrument_type','market','settlement'))
            mapped=currency_map.get(key)
            explicit=row.get('currency') or meta.get('currency')
            if row.get('currency') and meta.get('currency') and cash_currency(row['currency'])!=cash_currency(meta['currency']):
                raise ValueError('HISTORY_CURRENCY_CONFLICT')
            currency=resolve_legacy_currency(explicit,mapped)
            item=CloseEvidence(row['symbol'],row['instrument_type'],row['market'],row['settlement'],
                row['date'],row['close'],utc(row['observed_at']),meta.get('rejection_reason_full_ohlc','LEGACY_PARTIAL'),
                int(meta.get('row_index',0)),{'date':row['date'],'price':row['close'],'legacy_raw_row_hash':row['raw_row_hash'],
                    'copy_migration_currency':{'origin':'LEGACY_METADATA' if explicit else 'EXPLICIT_REVIEWED_MAPPING',
                        'mapping_sha256':mapping_digest,'legacy_version_id':row['id']}},currency)
            result=append_close_evidence(None,item,connection=connection)
            connection.execute('INSERT INTO history_close_migration_map_v2 VALUES(?,?)',(row['id'],result['version_id']))
            migrated+=1
        except (ValueError,TypeError,KeyError) as exc:
            reason=str(exc) if str(exc).startswith('HISTORY_') else 'HISTORY_CLOSE_CURRENCY_UNVERIFIED'
            connection.execute('INSERT INTO history_close_migration_quarantine_v2 VALUES(?,?)',(row['id'],reason))
            quarantined+=1
    return {'migrated_rows':migrated,'quarantined_rows':quarantined}


def coverage(connection, *, symbol: str, instrument_type: str,
             market: str,currency: str, settlement: str) -> dict:
    row=connection.execute(
        """SELECT COUNT(*) n,MIN(date),MAX(date)
           FROM history_close_canonical_v1
           WHERE symbol=? AND instrument_type=? AND market=? AND currency=? AND settlement=?""",
        (str(symbol).upper(),str(instrument_type).upper(),str(market).upper(),cash_currency(currency),
         str(settlement).upper()),
    ).fetchone()
    return {"rows":int(row[0] or 0),"first_date":row[1],"last_date":row[2]}
