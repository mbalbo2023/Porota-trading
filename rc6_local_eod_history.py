"""Bounded local postclose archive, independent of external bootstrap.

Copies existing 1m/5m candle revisions to the existing cold HIST_DB_PATH.
Daily sampled ranges remain a separate quality-qualified projection in that
same database. They never enter provider OHLC or History v2 canonical tables.
No broker, provider, HOT writer or PPI Watch is reachable from this module.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import fcntl
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time

import ak_byma_calendar as calendar
from bl_candle_engine import Bar, CandleArchive, Series, canonical, fingerprint, init_schema, stamp
from co_market_sessions_hf6 import TZ, session_for
from cv_history_store_adapter_hf6 import HistoricalStore

HEADROOM = 64 * 1024**2
RESERVE = 2 * 1024**3 + HEADROOM
MAX_ROWS = 500
DDL = """
CREATE TABLE IF NOT EXISTS local_eod_cursor_v1(
 source_id TEXT PRIMARY KEY,cursor INTEGER NOT NULL,as_of TEXT NOT NULL,state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS local_eod_receipts_v1(
 source_id TEXT NOT NULL,version_id INTEGER NOT NULL,payload_hash TEXT NOT NULL,
 published_at TEXT NOT NULL,source_known_at TEXT NOT NULL,identity_json TEXT NOT NULL,
 body_json TEXT NOT NULL,PRIMARY KEY(source_id,version_id));
CREATE TABLE IF NOT EXISTS local_eod_provenance_v1(
 source_id TEXT NOT NULL,version_id INTEGER NOT NULL,event_at_first TEXT,event_at_last TEXT,
 received_at_first TEXT,received_at_last TEXT,source_known_at TEXT NOT NULL,published_at TEXT NOT NULL,
 PRIMARY KEY(source_id,version_id));
CREATE TABLE IF NOT EXISTS history_local_daily_v1(
 series_id TEXT NOT NULL,session TEXT NOT NULL,known_at TEXT NOT NULL,
 identity_json TEXT NOT NULL,body_json TEXT NOT NULL,body_hash TEXT NOT NULL,
 PRIMARY KEY(series_id,session,known_at));
CREATE INDEX IF NOT EXISTS idx_local_daily_session ON history_local_daily_v1(series_id,session,known_at);
CREATE INDEX IF NOT EXISTS idx_local_daily_recent ON history_local_daily_v1(session DESC,known_at DESC);
"""


def consolidate(source_path, history_path, *, as_of, batch_size=MAX_ROWS, free_bytes=None, before_commit=None):
    at = datetime.fromisoformat(stamp(as_of))
    local = at.astimezone(TZ)
    base = {"schema": "rc6.local-eod.v1", "as_of": stamp(at), "external_bootstrap": "INDEPENDENT",
            "provider_requests": 0, "real_routes": "NOT_CALLED", "ppi_watch_accessed": False,
            "history_v2_writes": False, "coverage_state": "PARTIAL", "price_kind": "TRADE_SAMPLES"}
    if type(batch_size) is not int or not 1 <= batch_size <= MAX_ROWS:
        raise ValueError("LOCAL_EOD_BATCH_LIMIT")
    if local.year not in calendar.ANIOS_AUDITADOS:
        return base | {"state": "BLOCKED", "reason": "CALENDAR_UNVERIFIED"}
    if not calendar.es_dia_habil_operativo(local.date()) or local.hour*60+local.minute < 17*60+15:
        return base | {"state": "NOT_DUE", "reason": "POSTCLOSE_WINDOW_REQUIRED"}
    source = Path(source_path).absolute()
    target = Path(history_path).absolute()
    if source.resolve(strict=True) != source or target.resolve() != target or source == target:
        raise ValueError("LOCAL_EOD_SOURCE_TARGET_ALIAS")
    if source.stat().st_nlink != 1 or any(p.is_symlink() for p in source.parents):
        raise ValueError("LOCAL_EOD_SOURCE_ALIAS")
    # No full SQLite copy (the HOT source can exceed 512 MiB). All reads are
    # keyset bounded with an independent two-second query deadline.
    source_id = fingerprint({"path": str(source), "device": source.stat().st_dev,
                             "inode": source.stat().st_ino})
    with closing(sqlite3.connect(source.as_uri()+"?mode=ro", uri=True, timeout=.1)) as read:
        read.row_factory = sqlite3.Row
        read.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 65536)
        read.execute("PRAGMA query_only=ON")
        deadline = time.monotonic()+2
        read.set_progress_handler(lambda: int(time.monotonic()>deadline), 1000)
        read.execute("BEGIN")
        safety = read.execute("SELECT mode,real_orders_sent,session_state,heartbeat_at FROM observer_state WHERE id=1").fetchone()
        if (not safety or safety["mode"] != "PRODUCTION_PAPER" or
                type(safety["real_orders_sent"]) is not int or safety["real_orders_sent"] != 0):
            raise ValueError("LOCAL_EOD_PAPER_SAFETY_REQUIRED")
        heartbeat = datetime.fromisoformat(stamp(safety["heartbeat_at"]))
        if safety["session_state"] not in {"CLOSED", "MARKET_CLOSED"} or not 0 <= (at-heartbeat).total_seconds() <= 180:
            return base | {"state": "BLOCKED", "reason": "RUNTIME_CLOSED_UNVERIFIED"}
        if read.execute("SELECT 1 FROM paper_positions WHERE status='OPEN' LIMIT 1").fetchone():
            return base | {"state": "BLOCKED", "reason": "OPEN_POSITIONS"}
        target.parent.mkdir(parents=True, exist_ok=True)
        available = shutil.disk_usage(target.parent).free if free_bytes is None else free_bytes
        if available < RESERVE:
            return base | {"state": "DEFERRED_DISK", "reason": "HOST_RESERVE_REQUIRED"}
        if sum(Path(str(target)+suffix).stat().st_size for suffix in ('','-wal','-shm')
               if Path(str(target)+suffix).exists()) > 2*1024**3-HEADROOM:
            return base | {"state": "BLOCKED", "reason": "COLD_ARCHIVE_PHYSICAL_BUDGET"}
        store = HistoricalStore(str(target))
        with target.with_suffix(target.suffix+'.local-eod.lock').open('a+') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Established candle schema only. Never initialize/migrate legacy
            # History v2, assign currency or modify its canonical records.
            init_schema(store)
            with store.connect() as write:
                write.executescript(DDL)
                write.execute('PRAGMA busy_timeout=200')
                write.execute('PRAGMA cache_size=-2048')
                page_size=write.execute('PRAGMA page_size').fetchone()[0]
                write.execute('PRAGMA max_page_count='+str(2*1024**3//page_size))
                write.execute('BEGIN IMMEDIATE')
                prior = write.execute('SELECT cursor FROM local_eod_cursor_v1 WHERE source_id=?',(source_id,)).fetchone()
                cursor = prior[0] if prior else 0
                selected = read.execute('''SELECT v.*,s.identity_json FROM candle_versions v
                    JOIN candle_series s USING(series_id) WHERE v.id>? ORDER BY v.id LIMIT ?''',
                    (cursor,batch_size))
                rows=[]; payload_bytes=0
                for row in selected:
                    payload_bytes+=len(row['body_json'].encode())+len(row['identity_json'].encode())
                    if payload_bytes>4*1024**2:
                        raise ValueError('LOCAL_EOD_BATCH_BYTE_BUDGET')
                    rows.append(row)
                clocks={}
                for row in rows:
                    clocks[row['id']]=tuple(read.execute('''SELECT MIN(event_at),MAX(event_at),MIN(received_at),MAX(received_at)
                        FROM candle_samples WHERE series_id=? AND event_at>=? AND event_at<?
                        AND received_at<=?''',(row['series_id'],row['bar_start'],row['bar_end'],stamp(at))).fetchone())
                more=bool(read.execute('SELECT 1 FROM candle_versions WHERE id>? LIMIT 1',
                                      (rows[-1]['id'] if rows else cursor,)).fetchone())
                if time.monotonic()>deadline:
                    raise ValueError('LOCAL_EOD_SOURCE_READ_BUDGET')
                # End the HOT/WAL snapshot before parsing, hashing or publishing
                # cold revisions. Cold work cannot spend the source SQL budget.
                read.rollback()
                write_deadline=time.monotonic()+15
                write.set_progress_handler(lambda:int(time.monotonic()>write_deadline),1000)
                def write_guard():
                    if time.monotonic()>write_deadline:
                        raise ValueError('LOCAL_EOD_COLD_WRITE_BUDGET')
                final = {(r['series_id'],r['bar_start']):r['id'] for r in rows}
                dirty = set()
                archive = CandleArchive(store)
                imported = rejected = 0
                for row in rows:
                    write_guard()
                    if datetime.fromisoformat(stamp(row['known_at'])) > at:
                        raise ValueError('LOCAL_EOD_FUTURE_VERSION')
                    identity = json.loads(row['identity_json'])
                    series = Series(**identity)
                    body = json.loads(row['body_json'])
                    if (series.key != row['series_id'] or fingerprint(body) != row['body_hash'] or
                            body.get('start') != row['bar_start'] or body.get('end') != row['bar_end']):
                        raise ValueError('LOCAL_EOD_SOURCE_DIGEST_MISMATCH')
                    session = session_for(series.market,series.asset_class)
                    start = datetime.fromisoformat(stamp(row['bar_start'])).astimezone(TZ)
                    end = datetime.fromisoformat(stamp(row['bar_end'])).astimezone(TZ)
                    valid = (session is not None and series.market=='BYMA' and
                        series.price_kind=='TRADE_SAMPLES' and series.resolution in {'1m','5m'} and
                        series.adjustment=='RAW' and series.adjustment_basis!='UNKNOWN' and
                        start.year in calendar.ANIOS_AUDITADOS and calendar.es_dia_habil_operativo(start.date()) and
                        start.time().replace(tzinfo=None)>=session.regular_open and
                        end.time().replace(tzinfo=None)<=session.regular_close and
                        start.date()==end.date() and end<=at.astimezone(TZ) and
                        body.get('quality') in {'SAMPLED','CONFLICT'} and not body.get('synthetic'))
                    if valid and final[(row['series_id'],row['bar_start'])]==row['id']:
                        write.execute('INSERT INTO local_eod_provenance_v1 VALUES(?,?,?,?,?,?,?,?)',
                            (source_id,row['id'],*clocks[row['id']],row['known_at'],stamp(at)))
                        bar = Bar(start=body['start'],open=Decimal(body['open']),high=Decimal(body['high']),
                            low=Decimal(body['low']),close=Decimal(body['close']),samples=body['samples'],
                            quality=body['quality'])
                        archive.put(series,bar,known_at=at,connection=write)
                        if series.resolution=='1m':
                            dirty.add((series.key,start.date().isoformat()))
                        imported+=1
                    elif not valid:
                        rejected+=1
                    write.execute('INSERT INTO local_eod_receipts_v1 VALUES(?,?,?,?,?,?,?)',
                        (source_id,row['id'],row['body_hash'],stamp(at),row['known_at'],
                         row['identity_json'],row['body_json']))
                    cursor=row['id']
                # At most 500 dirty groups, each at most the 390 sampled minutes
                # in the audited quote session. Late revisions append at NOW.
                for series_id,day in sorted(dirty):
                    write_guard()
                    identity=json.loads(write.execute('SELECT identity_json FROM candle_series WHERE series_id=?',(series_id,)).fetchone()[0])
                    day_start=datetime.fromisoformat(day).replace(tzinfo=TZ)
                    versions=write.execute('''SELECT * FROM candle_versions WHERE series_id=? AND bar_start>=?
                        AND bar_start<? ORDER BY bar_start,known_at,id LIMIT 5001''',
                        (series_id,stamp(day_start),stamp(day_start+timedelta(days=1)))).fetchall()
                    if len(versions)>5000:
                        raise ValueError('LOCAL_EOD_DAILY_REVISION_BUDGET')
                    latest={v['bar_start']:json.loads(v['body_json']) for v in versions}
                    bars=[latest[k] for k in sorted(latest)]
                    if not bars:
                        continue
                    body={'session':day,'quality':'CONFLICT' if any(b['quality']=='CONFLICT' for b in bars) else 'SAMPLED',
                        'price_kind':'TRADE_SAMPLES','coverage_state':'PARTIAL','expected_minutes':390,
                        'captured_minutes':len(bars),'missing_minutes':390-len(bars),
                        'samples':sum(b['samples'] for b in bars),'open':bars[0]['open'],'close':bars[-1]['close'],
                        'high':str(max(Decimal(b['high']) for b in bars)),
                        'low':str(min(Decimal(b['low']) for b in bars)),
                        'volume':None,'trades':None,'vwap':None,
                        'first_event_window':bars[0]['start'],'last_event_window':bars[-1]['end'],
                        'underlying_us_coverage':'NOT_MEASURED','provenance':'local candle_versions; REST samples only'}
                    previous=write.execute('SELECT body_hash FROM history_local_daily_v1 WHERE series_id=? AND session=? ORDER BY known_at DESC LIMIT 1',(series_id,day)).fetchone()
                    hashed=fingerprint(body)
                    if previous is None or previous[0]!=hashed:
                        write.execute('INSERT INTO history_local_daily_v1 VALUES(?,?,?,?,?,?)',
                            (series_id,day,stamp(at),canonical(identity),canonical(body),hashed))
                state='BACKLOG' if more else 'LOCAL_EOD_READY'
                write.execute('INSERT OR REPLACE INTO local_eod_cursor_v1 VALUES(?,?,?,?)',
                    (source_id,cursor,stamp(at),state))
                if before_commit:
                    before_commit()
                write_guard()
                return base | {'state':state,'imported':imported,'rejected':rejected,'cursor':cursor,
                    'daily_groups':len(dirty),'real_orders_sent':0,'scope':'OBSERVED_BASKET_ONLY'}


def main():
    from cg_paper_workspace import database_path
    result=consolidate(database_path(),os.getenv('HIST_DB_PATH','/app/data/market_history.db'),
                       as_of=datetime.now(timezone.utc))
    print(json.dumps(result,sort_keys=True))
    return 0 if result['state'] in {'NOT_DUE','LOCAL_EOD_READY'} else 2


if __name__=='__main__':
    raise SystemExit(main())
