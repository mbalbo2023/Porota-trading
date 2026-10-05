"""Exact monetary identity, separate price bases and immutable PIT revisions.

History is research evidence and never grants readiness or execution authority.
Legacy four-field databases require an explicit migration on a verified copy.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date as calendar_date, datetime, timezone
from hashlib import sha256
from itertools import islice
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from bs_instrument_contracts import cash_currency

SOURCE_RANK = {"PPI_PRODUCTION_HISTORY":10,"PPI_API":10,"BYMA_EOD":20,"BYMA":20,
               "A3_CEM_CLOSING":20,"IOL":30,"DATA912":50,"DATA912_POROTA_BATCH":50,"YAHOO":90}
DEFAULT_SOURCE_RANK = 100
MAX_BATCH_ROWS = 10000
IDENTITY = ("symbol","instrument_type","market","currency","settlement")
SERIES = (*IDENTITY,"date","price_basis","adjustment_basis")
ART = ZoneInfo("America/Argentina/Buenos_Aires")
DDL = """
CREATE TABLE IF NOT EXISTS history_versions_v2(
 id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT NOT NULL,instrument_type TEXT NOT NULL,
 market TEXT NOT NULL,currency TEXT NOT NULL,settlement TEXT NOT NULL,date TEXT NOT NULL,
 price_basis TEXT NOT NULL,adjustment_basis TEXT NOT NULL,open REAL,high REAL,low REAL,
 close REAL NOT NULL,volume REAL,volume_kind TEXT NOT NULL,source TEXT NOT NULL,
 adjusted INTEGER NOT NULL,event_at TEXT NOT NULL,provider_at TEXT,observed_at TEXT NOT NULL,
 version_known_at TEXT NOT NULL,payload_hash TEXT NOT NULL,previous_version_id INTEGER NOT NULL,
 metadata_json TEXT NOT NULL,
 UNIQUE(symbol,instrument_type,market,currency,settlement,date,price_basis,
        adjustment_basis,source,previous_version_id,payload_hash));
CREATE INDEX IF NOT EXISTS idx_history_versions_v2_identity ON history_versions_v2(
 symbol,instrument_type,market,currency,settlement,date,price_basis,adjustment_basis,id DESC);
CREATE INDEX IF NOT EXISTS idx_history_versions_v2_source ON history_versions_v2(source,version_known_at);
CREATE INDEX IF NOT EXISTS idx_history_versions_v2_exact_revision ON history_versions_v2(
 symbol,instrument_type,market,currency,settlement,date,price_basis,adjustment_basis,
 source,version_known_at DESC,id DESC);
CREATE TABLE IF NOT EXISTS history_canonical_v2(
 symbol TEXT NOT NULL,instrument_type TEXT NOT NULL,market TEXT NOT NULL,currency TEXT NOT NULL,
 settlement TEXT NOT NULL,date TEXT NOT NULL,price_basis TEXT NOT NULL,adjustment_basis TEXT NOT NULL,
 open REAL,high REAL,low REAL,close REAL NOT NULL,volume REAL,volume_kind TEXT NOT NULL,
 source TEXT NOT NULL,adjusted INTEGER NOT NULL,source_rank INTEGER NOT NULL,event_at TEXT NOT NULL,
 provider_at TEXT,observed_at TEXT NOT NULL,version_known_at TEXT NOT NULL,last_checked_at TEXT NOT NULL,
 version_id INTEGER NOT NULL,metadata_json TEXT NOT NULL,
 PRIMARY KEY(symbol,instrument_type,market,currency,settlement,date,price_basis,adjustment_basis),
 FOREIGN KEY(version_id) REFERENCES history_versions_v2(id));
CREATE INDEX IF NOT EXISTS idx_history_canonical_v2_lookup ON history_canonical_v2(
 instrument_type,market,currency,settlement,symbol,price_basis,date);
CREATE TABLE IF NOT EXISTS history_checks_v2(
 version_id INTEGER PRIMARY KEY,last_checked_at TEXT NOT NULL,
 FOREIGN KEY(version_id) REFERENCES history_versions_v2(id));
CREATE TABLE IF NOT EXISTS history_batch_attempts_v2(
 attempt_key TEXT PRIMARY KEY,first_committed_at TEXT NOT NULL,last_checked_at TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state='COMMITTED'),committed_rows INTEGER NOT NULL,
 versions_appended INTEGER NOT NULL,canonical_updates INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS history_batch_rows_v2(
 attempt_key TEXT NOT NULL,ordinal INTEGER NOT NULL,version_id INTEGER NOT NULL,
 payload_hash TEXT NOT NULL,PRIMARY KEY(attempt_key,ordinal),
 FOREIGN KEY(attempt_key) REFERENCES history_batch_attempts_v2(attempt_key),
 FOREIGN KEY(version_id) REFERENCES history_versions_v2(id));
CREATE TABLE IF NOT EXISTS history_source_conflicts_v2(
 current_version_id INTEGER NOT NULL,contender_version_id INTEGER NOT NULL,
 reason TEXT NOT NULL,known_at TEXT NOT NULL,
 PRIMARY KEY(current_version_id,contender_version_id,reason),
 FOREIGN KEY(current_version_id) REFERENCES history_versions_v2(id),
 FOREIGN KEY(contender_version_id) REFERENCES history_versions_v2(id));
"""


def instant(value) -> datetime:
    try:
        parsed = value if isinstance(value,datetime) else datetime.fromisoformat(str(value).replace("Z","+00:00"))
    except (TypeError,ValueError,OverflowError):
        raise ValueError("HISTORY_CLOCK_INVALID") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("HISTORY_CLOCK_AWARE_REQUIRED")
    return parsed.astimezone(timezone.utc)


def utc(value) -> str:
    return instant(value).isoformat(timespec="microseconds")


def _canonical_json(value) -> str:
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False,default=str)


def validate_daily_values(*, date, open, high, low, close, volume, known_at, market):
    """Shared numeric/session gate, including legacy evidence without identity.

    Passing this gate establishes values only. Currency and exact identity are
    still mandatory at the v2 sink and absent legacy identity stays advisory.
    """
    try:day=calendar_date.fromisoformat(date)
    except (TypeError,ValueError):raise ValueError('HISTORY_DATE_INVALID') from None
    if day.isoformat()!=date:raise ValueError('HISTORY_DATE_INVALID')
    known=instant(known_at)
    if known>datetime.now(timezone.utc):raise ValueError('HISTORY_KNOWN_AT_IN_FUTURE')
    if day>known.astimezone(ART).date():raise ValueError('HISTORY_DATE_IN_FUTURE')
    if day.weekday()>=5:raise ValueError('HISTORY_SESSION_WEEKEND')
    if market=='BYMA':
        from ak_byma_calendar import ANIOS_AUDITADOS,motivo_no_operativo
        if day.year in ANIOS_AUDITADOS and motivo_no_operativo(day):
            raise ValueError('HISTORY_SESSION_NOT_OPERATIONAL')
    values={}
    for name,raw in (('open',open),('high',high),('low',low),('close',close),('volume',volume)):
        if isinstance(raw,bool):raise ValueError('HISTORY_NUMBER_BOOLEAN')
        if raw is None:
            if name=='close':raise ValueError('HISTORY_CLOSE_REQUIRED')
            values[name]=None
            continue
        try:number=float(raw)
        except (TypeError,ValueError,OverflowError):raise ValueError('HISTORY_'+name.upper()+'_INVALID') from None
        if not math.isfinite(number):raise ValueError('HISTORY_'+name.upper()+'_NONFINITE')
        if number<0 if name=='volume' else number<=0:
            raise ValueError('HISTORY_'+name.upper()+'_NONPOSITIVE')
        values[name]=number
    ohlc=(values['open'],values['high'],values['low'])
    if any(x is None for x in ohlc) and not all(x is None for x in ohlc):raise ValueError('HISTORY_OHLC_PARTIAL')
    if all(x is not None for x in ohlc) and not values['low']<=min(values['open'],values['close'])<=max(values['open'],values['close'])<=values['high']:
        raise ValueError('HISTORY_OHLC_INCONSISTENT')
    return values


@dataclass(frozen=True)
class Candle:
    # Preserve the original positional API. Missing monetary identity resolves
    # only from explicit metadata, never ticker, family or an ARS default.
    symbol:str
    instrument_type:str
    market:str
    settlement:str
    date:str
    open:float | None
    high:float | None
    low:float | None
    close:float
    volume:float | None
    source:str
    adjusted:bool=False
    observed_at:str | None=None
    metadata:dict | None=None
    currency:str=""
    price_basis:str=""
    adjustment_basis:str=""
    volume_kind:str="UNKNOWN"
    provider_at:str | None=None

    def normalized(self):
        if any(isinstance(getattr(self,name),bool) for name in ('open','high','low','close','volume')):
            raise ValueError('HISTORY_NUMBER_BOOLEAN')
        metadata=dict(self.metadata or {})
        monetary=self.currency or metadata.get("currency")
        if not monetary: raise ValueError("HISTORY_CURRENCY_REQUIRED")
        try: monetary=cash_currency(monetary)
        except ValueError: raise ValueError("HISTORY_CURRENCY_UNVERIFIED") from None
        if metadata.get("currency") and cash_currency(metadata["currency"])!=monetary:
            raise ValueError("HISTORY_CURRENCY_CONFLICT")
        basis=str(self.price_basis or metadata.get("price_basis") or
                  ("UNKNOWN_ADJUSTED" if self.adjusted else "RAW")).strip().upper()
        adjustment=str(self.adjustment_basis or metadata.get("adjustment_basis") or
                       ("RAW_NO_ADJUSTMENT" if basis=="RAW" else "UNKNOWN")).strip()
        metadata["currency"]=monetary
        return replace(self,symbol=str(self.symbol or "").strip().upper(),
            instrument_type=str(self.instrument_type or "").strip().upper(),
            market=str(self.market or "").strip().upper(),settlement=str(self.settlement or "").strip().upper(),
            date=str(self.date or "").strip(),source=str(self.source or "").strip().upper(),
            open=None if self.open is None else float(self.open),high=None if self.high is None else float(self.high),
            low=None if self.low is None else float(self.low),close=float(self.close),
            volume=None if self.volume is None else float(self.volume),currency=monetary,
            adjusted=bool(self.adjusted),
            price_basis=basis,adjustment_basis=adjustment,volume_kind=str(self.volume_kind or "UNKNOWN").upper(),
            observed_at=utc(self.observed_at or datetime.now(timezone.utc)),
            provider_at=utc(self.provider_at) if self.provider_at else None,metadata=metadata)

    def validate(self):
        value=self.normalized()
        if any(getattr(value,f) in {"","UNKNOWN"} for f in (*IDENTITY,"source")):
            raise ValueError("HISTORY_IDENTITY_UNVERIFIED")
        from cp_history_ingest_policy_hf6 import OPERATIONAL_HISTORY_FAMILIES,LEGACY_HISTORY_FAMILIES
        if value.instrument_type not in OPERATIONAL_HISTORY_FAMILIES | LEGACY_HISTORY_FAMILIES:
            raise ValueError("HISTORY_FAMILY_UNVERIFIED")
        known=instant(value.observed_at)
        validate_daily_values(date=value.date,open=value.open,high=value.high,low=value.low,
                              close=value.close,volume=value.volume,known_at=known,market=value.market)
        if value.price_basis not in {"RAW","SPLIT","DIVIDEND","TOTAL_RETURN","UNKNOWN_ADJUSTED"}:
            raise ValueError("HISTORY_PRICE_BASIS_UNVERIFIED")
        if value.price_basis=="RAW" and value.adjusted: raise ValueError("HISTORY_ADJUSTMENT_CONFLICT")
        if value.price_basis not in {"RAW","UNKNOWN_ADJUSTED"} and value.adjustment_basis in {"","UNKNOWN"}:
            raise ValueError("HISTORY_ADJUSTMENT_PROVENANCE_REQUIRED")
        if value.volume_kind not in {"UNKNOWN","QUANTITY","NOMINAL","CONTRACTS","MONEY"}:
            raise ValueError("HISTORY_VOLUME_UNIT_INVALID")
        if value.provider_at and instant(value.provider_at)>known: raise ValueError("HISTORY_PROVIDER_AFTER_KNOWN")
        _canonical_json(value.metadata or {})


def _init_connection(connection):
    for name in ("history_versions_v2","history_canonical_v2"):
        cols={r[1] for r in connection.execute("PRAGMA table_info("+name+")")}
        required={"currency","price_basis","adjustment_basis","version_known_at","volume_kind"}
        if name=='history_versions_v2':required.add('previous_version_id')
        if cols and not required<=cols:
            raise ValueError("HISTORY_COPY_MIGRATION_REQUIRED")
    for statement in DDL.split(";"):
        if statement.strip(): connection.execute(statement)


def init_schema(store):
    with store.connect() as connection: _init_connection(connection)


def source_rank(source):
    text=str(source or "").upper()
    for name,rank in SOURCE_RANK.items():
        if text==name or text.startswith(name+"_"): return rank
    return DEFAULT_SOURCE_RANK


def _key(value): return tuple(getattr(value,f) for f in SERIES)


def _hash(candle):
    value=candle.normalized()
    body={f:getattr(value,f) for f in (*SERIES,"open","high","low","close","volume","volume_kind","source","adjusted","provider_at")}
    body.update({f:(value.metadata or {}).get(f) for f in ("adjustment_factor","corporate_actions")})
    return sha256(_canonical_json(body).encode()).hexdigest()


def _prefer(new,current):
    if current is None: return True
    value=new.normalized()
    if (value.price_basis,value.adjustment_basis)!=(current["price_basis"],current["adjustment_basis"]): return False
    rank=source_rank(value.source)
    if rank!=int(current["source_rank"]): return rank<int(current["source_rank"])
    left,right=instant(value.observed_at),instant(current["version_known_at"])
    return left>right or (left==right and value.source<current["source"])


_WHERE=" AND ".join(f+"=?" for f in SERIES)


def _append_connection(connection,value):
    digest=_hash(value)
    previous=connection.execute("SELECT * FROM history_versions_v2 WHERE "+_WHERE+
        " AND source=? ORDER BY version_known_at DESC,id DESC LIMIT 1",(*_key(value),value.source)).fetchone()
    duplicate=previous is not None and previous["payload_hash"]==digest
    if previous is not None and not duplicate:
        if value.observed_at<previous["version_known_at"]: raise ValueError("HISTORY_REVISION_BACKDATED")
        if value.observed_at==previous["version_known_at"]: raise ValueError("HISTORY_REVISION_TIME_CONFLICT")
    appended=not duplicate
    metadata=dict(value.metadata or {})
    metadata["session_calendar_status"]="AUDITED_BYMA_2026" if value.market=="BYMA" and value.date.startswith("2026-") else "HOLIDAYS_NO_VERIFICADO"
    metadata["history_quality"]="FULL_OHLC" if value.open is not None else "CLOSE_ONLY"
    if appended:
        fields=(*SERIES,"open","high","low","close","volume","volume_kind","source","adjusted","event_at","provider_at","observed_at","version_known_at","payload_hash","previous_version_id","metadata_json")
        args=(*_key(value),value.open,value.high,value.low,value.close,value.volume,value.volume_kind,value.source,
              int(value.adjusted),value.date,value.provider_at,value.observed_at,value.observed_at,digest,
              int(previous["id"]) if previous else 0,_canonical_json(metadata))
        cursor=connection.execute("INSERT INTO history_versions_v2("+",".join(fields)+") VALUES("+",".join("?" for _ in fields)+")",args)
        version_id=int(cursor.lastrowid)
        version=connection.execute("SELECT * FROM history_versions_v2 WHERE id=?",(version_id,)).fetchone()
    else: version=previous;version_id=int(previous["id"])
    connection.execute("INSERT INTO history_checks_v2 VALUES(?,?) ON CONFLICT(version_id) DO UPDATE SET last_checked_at=MAX(last_checked_at,excluded.last_checked_at)",(version_id,value.observed_at))
    current=connection.execute("SELECT * FROM history_canonical_v2 WHERE "+_WHERE,_key(value)).fetchone()
    conflicts=0
    if appended:
        other_series=connection.execute('''SELECT version_id,source,close,price_basis,adjustment_basis,
            version_known_at FROM history_canonical_v2 WHERE symbol=? AND instrument_type=?
            AND market=? AND currency=? AND settlement=? AND date=?''',_key(value)[:6]).fetchall()
        for other in other_series:
            if (other['price_basis'],other['adjustment_basis'])!=(value.price_basis,value.adjustment_basis):
                reason='INCOMPARABLE_PRICE_BASIS'
            elif other['source']!=value.source and float(other['close'])!=value.close:
                reason='SOURCE_PRICE_DISCREPANCY'
            else:continue
            cursor=connection.execute('INSERT OR IGNORE INTO history_source_conflicts_v2 VALUES(?,?,?,?)',
                (other['version_id'],version_id,reason,max(value.observed_at,other['version_known_at'])))
            conflicts+=int(cursor.rowcount>0)
    # Retry clocks never become evidence availability. ABA corrections acquire
    # a new immutable revision after the intervening version, without races.
    chosen=_prefer(replace(value,observed_at=version["version_known_at"]),current) if appended else current is None
    if chosen:
        fields=(*SERIES,"open","high","low","close","volume","volume_kind","source","adjusted","source_rank","event_at","provider_at","observed_at","version_known_at","last_checked_at","version_id","metadata_json")
        record={f:version[f] for f in fields if f in version.keys()}
        record.update(source_rank=source_rank(value.source),last_checked_at=value.observed_at,version_id=version_id)
        connection.execute("INSERT INTO history_canonical_v2("+",".join(fields)+") VALUES("+",".join("?" for _ in fields)+") ON CONFLICT("+",".join(SERIES)+") DO UPDATE SET "+",".join(f+"=excluded."+f for f in fields if f not in SERIES),tuple(record[f] for f in fields))
    elif int(current["version_id"])==version_id:
        connection.execute("UPDATE history_canonical_v2 SET last_checked_at=MAX(last_checked_at,?) WHERE "+_WHERE,(value.observed_at,*_key(value)))
    return {"version_id":version_id,"version_appended":appended,"canonical_updated":chosen,
            "source_rank":source_rank(value.source),"payload_hash":digest,"conflicts_recorded":conflicts}


def append_many(store,candles:Iterable[Candle],*,attempt_key=None):
    values=list(islice(iter(candles),MAX_BATCH_ROWS+1))
    if len(values)>MAX_BATCH_ROWS: raise ValueError("HISTORY_BATCH_ROW_BUDGET_EXHAUSTED")
    values=[v.normalized() for v in values]
    for value in values: value.validate()
    if not values: return {"versions_appended":0,"canonical_updates":0,"protected_by_precedence":0,"committed_rows":0,"batch_semantics":"ATOMIC_BOUNDED"}
    init_schema(store)
    key=attempt_key or sha256(_canonical_json([(v.observed_at,_hash(v)) for v in values]).encode()).hexdigest()
    results=[]
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            prior=connection.execute('SELECT ordinal,version_id,payload_hash FROM history_batch_rows_v2 WHERE attempt_key=? ORDER BY ordinal',
                                     (key,)).fetchall() if attempt_key is not None else []
            if prior:
                if [row['payload_hash'] for row in prior]!=[_hash(value) for value in values]:
                    raise ValueError('HISTORY_ATTEMPT_KEY_PAYLOAD_CONFLICT')
                checked=max(value.observed_at for value in values)
                for row,value in zip(prior,values):
                    connection.execute('UPDATE history_checks_v2 SET last_checked_at=MAX(last_checked_at,?) WHERE version_id=?',
                                       (value.observed_at,row['version_id']))
                    connection.execute('UPDATE history_canonical_v2 SET last_checked_at=MAX(last_checked_at,?) WHERE version_id=?',
                                       (value.observed_at,row['version_id']))
                    results.append({'version_id':row['version_id'],'version_appended':False,'canonical_updated':False,
                                    'source_rank':source_rank(value.source),'payload_hash':row['payload_hash'],'conflicts_recorded':0})
                connection.execute('UPDATE history_batch_attempts_v2 SET last_checked_at=MAX(last_checked_at,?) WHERE attempt_key=?',
                                   (checked,key))
                connection.commit()
                return {'versions_appended':0,'canonical_updates':0,'protected_by_precedence':0,
                        'committed_rows':len(values),'attempt_key':key,'batch_semantics':'ATOMIC_BOUNDED',
                        'replayed_committed_attempt':True,'row_results':results}
            for value in values: results.append(_append_connection(connection,value))
            versions=sum(int(r["version_appended"]) for r in results)
            canonical=sum(int(r["canonical_updated"]) for r in results)
            checked=max(v.observed_at for v in values)
            connection.execute("INSERT INTO history_batch_attempts_v2 VALUES(?,?,?,'COMMITTED',?,?,?) ON CONFLICT(attempt_key) DO UPDATE SET last_checked_at=MAX(last_checked_at,excluded.last_checked_at)",(key,checked,checked,len(values),versions,canonical))
            connection.executemany('INSERT INTO history_batch_rows_v2 VALUES(?,?,?,?) ON CONFLICT(attempt_key,ordinal) DO UPDATE SET version_id=excluded.version_id,payload_hash=excluded.payload_hash',
                [(key,index,result['version_id'],result['payload_hash']) for index,result in enumerate(results)])
            connection.commit()
        except BaseException:
            connection.rollback();raise
    return {"versions_appended":versions,"canonical_updates":canonical,"protected_by_precedence":len(values)-canonical,
            "committed_rows":len(values),"attempt_key":key,"batch_semantics":"ATOMIC_BOUNDED","row_results":results}


def append_candle(store,candle): return append_many(store,[candle])["row_results"][0]


def read_as_of(connection,*,symbol,instrument_type,market,currency,settlement,as_of,
               price_basis="RAW",adjustment_basis="RAW_NO_ADJUSTMENT"):
    """Latest known revision per source/day, then authority within same basis."""
    cut=utc(as_of)
    identity=tuple(str(x or "").strip().upper() for x in (symbol,instrument_type,market,currency,settlement))
    if any(x in {"","UNKNOWN"} for x in identity): raise ValueError("HISTORY_IDENTITY_UNVERIFIED")
    rows=connection.execute("""SELECT v.* FROM history_versions_v2 v WHERE symbol=? AND instrument_type=?
        AND market=? AND currency=? AND settlement=? AND price_basis=? AND adjustment_basis=?
        AND version_known_at<=? AND date<=? ORDER BY date,source,version_known_at,id LIMIT 200001""",
        (*identity,price_basis,adjustment_basis,cut,instant(cut).astimezone(ART).date().isoformat())).fetchall()
    if len(rows)>200000: raise ValueError('HISTORY_READER_ROW_BUDGET_EXHAUSTED')
    by_source={}
    for row in rows:
        if instant(row["version_known_at"])<=instant(cut): by_source[(row["date"],row["source"])]=row
    by_day={}
    for row in by_source.values():
        # ISO timestamps have fixed UTC width; no float epoch loses microseconds.
        old=by_day.get(row["date"])
        rank=source_rank(row["source"])
        better_time = old is not None and row["version_known_at"] > old[1]["version_known_at"]
        tie = old is not None and row["version_known_at"] == old[1]["version_known_at"]
        better_tie = tie and (row["source"], -row["id"]) < (old[1]["source"], -old[1]["id"])
        if old is None or rank<old[0] or (rank==old[0] and (better_time or better_tie)):
            by_day[row["date"]]=(rank,row)
    return [dict(by_day[day][1]) for day in sorted(by_day)]


def coverage(connection,*,symbol,instrument_type,market,currency,settlement,as_of=None,
             price_basis="RAW",consumer="HISTORICAL_RESEARCH"):
    cut=as_of or datetime.now(timezone.utc)
    rows=read_as_of(connection,symbol=symbol,instrument_type=instrument_type,market=market,
                    currency=currency,settlement=settlement,as_of=cut,price_basis=price_basis)
    return {"rows":len(rows),"first_date":rows[0]["date"] if rows else None,"last_date":rows[-1]["date"] if rows else None,
            "sources":len({r["source"] for r in rows}),"identity":[symbol,instrument_type,market,currency,settlement],
            "as_of":utc(cut),"price_basis":price_basis,"consumer":consumer,"readiness_implication":"NONE",
            "session_coverage":sorted({r["date"] for r in rows})}


def coverage_inventory(connection, identities, *, as_of, consumer,
                       session=None, minimum_rows=30):
    """Current exact-key cohort coverage, separately from consumer readiness.

    Cohort identity and cut are retained so a historical denominator cannot
    silently be reused for another session or a different monetary universe.
    """
    cut=utc(as_of)
    keys=[]
    for raw in identities:
        if len(raw)!=5: raise ValueError('HISTORY_COVERAGE_FULL_IDENTITY_REQUIRED')
        key=tuple(str(x or '').strip().upper() for x in raw)
        if any(x in {'','UNKNOWN'} for x in key): raise ValueError('HISTORY_COVERAGE_FULL_IDENTITY_REQUIRED')
        key=(*key[:3],cash_currency(key[3]),key[4])
        keys.append(key)
    cohort=sorted(set(keys))
    if not consumer or type(minimum_rows) is not int or minimum_rows<1:
        raise ValueError('HISTORY_COVERAGE_CONSUMER_REQUIRED')
    if session is not None:
        day=calendar_date.fromisoformat(session)
        if day>instant(cut).astimezone(ART).date(): raise ValueError('HISTORY_COVERAGE_SESSION_IN_FUTURE')
    rows=[]
    for key in cohort:
        entry=coverage(connection,symbol=key[0],instrument_type=key[1],market=key[2],
            currency=key[3],settlement=key[4],as_of=cut,consumer=consumer)
        entry.update(covered=entry['rows']>0,sufficient=entry['rows']>=minimum_rows,
                     requested_session=session,session_covered=session in entry['session_coverage'] if session else None)
        rows.append(entry)
    groups={}
    for entry in rows:
        currency=entry['identity'][3]
        group=groups.setdefault(currency,{'denominator':0,'covered':0,'sufficient':0,'session_covered':0})
        group['denominator']+=1
        for name in ('covered','sufficient','session_covered'):group[name]+=int(bool(entry[name]))
    return {'schema':'rc6.history-exact-key-coverage.v1','as_of':cut,'consumer':consumer,
            'session':session,'price_basis':'RAW','minimum_rows':minimum_rows,
            'cohort_sha256':sha256(_canonical_json(cohort).encode()).hexdigest(),
            'denominator':len(cohort),'duplicate_identities':len(keys)-len(cohort),
            'covered':sum(int(r['covered']) for r in rows),'missing':sum(int(not r['covered']) for r in rows),
            'sufficient':sum(int(r['sufficient']) for r in rows),'by_currency':groups,
            'rows':rows,'readiness_implication':'NONE','runtime_verified':False}


def resolve_legacy_currency(explicit, mapped):
    """Use an explicit row quote or one proven mapping, never a currency guess."""
    try:
        monetary=cash_currency(explicit) if explicit else None
        choices={cash_currency(value) for value in mapped} if isinstance(mapped,(list,tuple,set)) else ({cash_currency(mapped)} if mapped else set())
    except ValueError:raise ValueError('HISTORY_CURRENCY_UNVERIFIED') from None
    if monetary:
        if choices and monetary not in choices:raise ValueError('HISTORY_CURRENCY_MAPPING_CONFLICT')
        return monetary
    if not choices:raise ValueError('HISTORY_CURRENCY_UNVERIFIED')
    if len(choices)!=1:raise ValueError('HISTORY_CURRENCY_AMBIGUOUS')
    return next(iter(choices))


def migrate_copy(source,destination,*,currency_map=None,seconds=60,max_source_bytes=512*1024*1024,
                 max_rows=1000000):
    """Preserve source and legacy tables; migrate only explicit currency.

    Mapping keys are exact (symbol,family,market,settlement), with a single
    externally evidenced currency. Missing/conflicting/ambiguous rows remain
    in quarantine with their original immutable source records preserved.
    """
    from rc6_audit_evidence.sqlite_snapshot import readonly_copy
    source,destination=Path(source).absolute(),Path(destination).absolute()
    destination_members=[Path(str(destination)+suffix) for suffix in ('','-wal','-shm','-journal')]
    if source==destination or any(path.exists() or path.is_symlink() for path in destination_members):
        raise ValueError("HISTORY_NEW_COPY_DESTINATION_REQUIRED")
    frozen_map={}
    for raw_key,mapped in (currency_map or {}).items():
        if not isinstance(raw_key,tuple) or len(raw_key)!=4:
            raise ValueError('HISTORY_MIGRATION_MAPPING_KEY_INVALID')
        key=tuple(str(value or '').strip().upper() for value in raw_key)
        if any(value in {'','UNKNOWN'} for value in key):raise ValueError('HISTORY_MIGRATION_MAPPING_KEY_INVALID')
        choices=mapped if isinstance(mapped,(list,tuple,set)) else [mapped]
        try:frozen_map[key]=tuple(sorted({cash_currency(value) for value in choices}))
        except ValueError:raise ValueError('HISTORY_CURRENCY_UNVERIFIED') from None
    currency_map=frozen_map
    mapping_digest=sha256(_canonical_json(sorted((list(key),value) for key,value in currency_map.items())).encode()).hexdigest()
    if not 0<seconds<=300 or type(max_rows) is not int or not 0<max_rows<=5000000:
        raise ValueError('HISTORY_MIGRATION_BOUNDED_BUDGET_REQUIRED')
    deadline=time.monotonic()+seconds
    def check(*_):
        if time.monotonic()>=deadline:raise ValueError('HISTORY_MIGRATION_TIME_BUDGET_EXHAUSTED')
    try:
        with readonly_copy(source,deadline=deadline,max_source_bytes=max_source_bytes) as original:
            output=sqlite3.connect(destination)
            try: original.backup(output,pages=256,progress=check)
            finally: output.close()
        class Store:
            @contextmanager
            def connect(self):
                connection=sqlite3.connect(destination);connection.row_factory=sqlite3.Row
                try: yield connection;connection.commit()
                except BaseException: connection.rollback();raise
                finally: connection.close()
        store=Store()
        with store.connect() as c:
            c.set_progress_handler(lambda:int(time.monotonic()>=deadline),100)
            columns={r[1] for r in c.execute("PRAGMA table_info(history_versions_v2)")}
            close_columns={r[1] for r in c.execute('PRAGMA table_info(history_close_versions_v1)')}
            close_legacy=bool(close_columns) and not {'currency','previous_version_id'}<=close_columns
            if not columns and not close_legacy:raise ValueError('HISTORY_MIGRATION_SOURCE_SCHEMA_REQUIRED')
            if 'currency' in columns and not close_legacy:raise ValueError('HISTORY_MIGRATION_ALREADY_CURRENT')
            full_legacy=bool(columns) and 'currency' not in columns
            versions=[dict(r) for r in c.execute("SELECT * FROM history_versions_v2 LIMIT ?",(max_rows+1,))] if full_legacy else []
            if len(versions)>max_rows:raise ValueError('HISTORY_MIGRATION_ROW_BUDGET_EXHAUSTED')
            if full_legacy:
                c.execute("ALTER TABLE history_canonical_v2 RENAME TO history_canonical_v2_legacy")
                c.execute("ALTER TABLE history_versions_v2 RENAME TO history_versions_v2_legacy")
                for index in ("idx_history_versions_v2_identity","idx_history_versions_v2_source","idx_history_canonical_v2_lookup"): c.execute("DROP INDEX IF EXISTS "+index)
            _init_connection(c)
            c.execute("CREATE TABLE history_migration_quarantine_v2(old_version_id INTEGER PRIMARY KEY,reason TEXT NOT NULL,payload_hash TEXT NOT NULL)")
            c.execute("CREATE TABLE history_migration_map_v2(old_version_id INTEGER PRIMARY KEY,new_version_id INTEGER NOT NULL)")
        migrated=quarantined=0
        def ordering(row):
            try: return (utc(row["observed_at"]),row["id"])
            except ValueError: return ("",row["id"])
        for row in sorted(versions,key=ordering):
            check()
            try:
                metadata=json.loads(row["metadata_json"] or "{}")
                if not isinstance(metadata,dict):raise ValueError('HISTORY_METADATA_INVALID')
                oldkey=tuple(str(row[f] or '').strip().upper() for f in ("symbol","instrument_type","market","settlement"))
                mapped=currency_map.get(oldkey)
                explicit=metadata.get("currency")
                monetary=resolve_legacy_currency(explicit,mapped)
                metadata['copy_migration_currency']={'origin':'LEGACY_METADATA' if explicit else 'EXPLICIT_REVIEWED_MAPPING',
                    'mapping_sha256':mapping_digest,'legacy_version_id':row['id']}
                value=Candle(**{f:row[f] for f in ("symbol","instrument_type","market","settlement","date","open","high","low","close","volume","source","adjusted","observed_at")},metadata=metadata,currency=monetary)
                result=append_candle(store,value)
                with store.connect() as c: c.execute("INSERT INTO history_migration_map_v2 VALUES(?,?)",(row["id"],result["version_id"]))
                migrated+=1
            except (ValueError,TypeError,KeyError) as exc:
                reason=str(exc) if str(exc).startswith("HISTORY_") else "HISTORY_MIGRATION_ROW_INVALID"
                # Non-finite legacy bytes remain in the preserved legacy table;
                # hash the representation without trying to store unsafe JSON.
                raw_hash=sha256(json.dumps(row,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
                with store.connect() as c:c.execute("INSERT INTO history_migration_quarantine_v2 VALUES(?,?,?)",(row["id"],reason,raw_hash))
                quarantined+=1
        with store.connect() as c:
            c.set_progress_handler(lambda:int(time.monotonic()>=deadline),100)
            from ea_history_close_series_hf2 import migrate_legacy_tables
            close_migration=migrate_legacy_tables(c,currency_map=currency_map,max_rows=max_rows-len(versions),check=check,
                                                  mapping_digest=mapping_digest)
            if c.execute("PRAGMA quick_check").fetchone()[0]!="ok": raise ValueError("HISTORY_MIGRATION_INTEGRITY_FAILED")
        return {"state":"COPY_MIGRATED","source_rows":len(versions),"migrated_rows":migrated,"quarantined_rows":quarantined,
                "close_only":close_migration,"currency_mapping_sha256":mapping_digest,
                "source_mutation":False,"full_ingestion_repeated":False}
    except BaseException:
        for path in destination_members:
            if path.exists():path.unlink()
        raise
