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


class _ColdConnection(sqlite3.Connection):
    def __exit__(self, *error):
        try:
            return super().__exit__(*error)
        finally:
            self.close()


class _ColdStore(HistoricalStore):
    """Keep cold initialization bounded without changing other history users."""
    def __init__(self, path, *, deadline):
        super().__init__(path)
        self.deadline = deadline

    def connect(self):
        self._path_identity()
        if time.monotonic()>self.deadline:
            raise ValueError('LOCAL_EOD_COLD_CONNECTION_BUDGET')
        connection = sqlite3.connect(self.path, timeout=.2, factory=_ColdConnection)
        try:
            connection.row_factory = sqlite3.Row
            connection.set_progress_handler(lambda: int(time.monotonic() > self.deadline), 1000)
            connection.execute('PRAGMA busy_timeout=200')
            connection.execute('PRAGMA synchronous=NORMAL')
            connection.execute('PRAGMA cache_size=-2048')
            return connection
        except BaseException:
            connection.close()
            raise


def _runtime_block(read, at):
    safety = read.execute('SELECT mode,real_orders_sent,session_state,heartbeat_at FROM observer_state WHERE id=1').fetchone()
    if (not safety or safety['mode'] != 'PRODUCTION_PAPER' or
            type(safety['real_orders_sent']) is not int or safety['real_orders_sent'] != 0):
        raise ValueError('LOCAL_EOD_PAPER_SAFETY_REQUIRED')
    heartbeat = datetime.fromisoformat(stamp(safety['heartbeat_at']))
    if safety['session_state'] not in {'CLOSED','MARKET_CLOSED'} or not 0 <= (at-heartbeat).total_seconds() <= 180:
        return 'RUNTIME_CLOSED_UNVERIFIED'
    if read.execute("SELECT 1 FROM paper_positions WHERE status='OPEN' LIMIT 1").fetchone():
        # The cold archive reads sampled candle history, not the PAPER book.
        # An EOD carry with a persisted market-closed exit intent must not
        # prevent historical archiving for the entire night. An unmatched or
        # different OPEN position still blocks. Legacy/missing intent schema
        # also fails closed; this gate is repeated after cold preparation.
        try:
            unsafe = read.execute("""SELECT 1 FROM paper_positions p
                WHERE p.status='OPEN' AND NOT EXISTS (
                  SELECT 1 FROM paper_exit_intents x WHERE x.paper_id=p.paper_id
                    AND x.state='EXIT_PENDING_MARKET_CLOSED' AND x.cause='EOD_PAPER')
                LIMIT 1""").fetchone()
        except sqlite3.Error:
            return 'OPEN_POSITIONS'
        if unsafe:
            return 'OPEN_POSITIONS'
    return None


def _committed_ready_cursor(target, source_id):
    """Read a committed READY checkpoint without opening the COLD writer.

    Missing/in-progress/legacy COLD schemas use the existing locked path.
    In particular BACKLOG is never eligible for an idle fast path.
    """
    if not target.is_file():
        return None
    deadline = time.monotonic() + 1
    try:
        with closing(sqlite3.connect(target.as_uri() + '?mode=ro',
                                     uri=True, timeout=.1)) as cold:
            cold.row_factory = sqlite3.Row
            cold.execute('PRAGMA query_only=ON')
            cold.execute('PRAGMA cache_size=-256')
            cold.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            checkpoint = cold.execute('''SELECT cursor,state FROM local_eod_cursor_v1
                WHERE source_id=?''', (source_id,)).fetchone()
            if (not checkpoint or checkpoint['state'] != 'LOCAL_EOD_READY' or
                    type(checkpoint['cursor']) is not int or checkpoint['cursor'] < 0):
                return None
            cursor = checkpoint['cursor']
            # A READY pointer without its last durable receipt is not enough
            # evidence to bypass the normal archive writer.
            if cursor and not cold.execute('''SELECT 1 FROM local_eod_receipts_v1
                    WHERE source_id=? AND version_id=?''', (source_id, cursor)).fetchone():
                return None
            return cursor if time.monotonic() <= deadline else None
    except sqlite3.Error:
        # A lock, missing table or unreadable checkpoint cannot authorize
        # skipping a writer; the original guarded path remains authoritative.
        return None


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
        blocked = _runtime_block(read, at)
        if blocked:
            return base | {'state':'BLOCKED','reason':blocked}
        # AUTOINCREMENT ids are the immutable HOT revision high-water mark.
        # This bounded indexed lookup does not scan bars or copy the HOT DB.
        first_high_water = read.execute('SELECT COALESCE(MAX(id),0) FROM candle_versions').fetchone()[0]
        if time.monotonic()>deadline:
            raise ValueError('LOCAL_EOD_SOURCE_READ_BUDGET')
        # Admission is a separate short transaction. Cold schema/lock work
        # must hold neither the HOT snapshot nor its two-second SQL budget.
        read.rollback()
        ready_cursor = _committed_ready_cursor(target, source_id)
        if ready_cursor is not None:
            # The COLD checkpoint is only an optimization hint. A second HOT
            # transaction closes the race with revisions or safety changes
            # during that read; any mismatch must use the original writer.
            deadline = time.monotonic() + 2
            read.execute('BEGIN')
            blocked = _runtime_block(read, at)
            if blocked:
                return base | {'state':'BLOCKED','reason':blocked}
            final_high_water = read.execute('SELECT COALESCE(MAX(id),0) FROM candle_versions').fetchone()[0]
            if time.monotonic() > deadline:
                raise ValueError('LOCAL_EOD_SOURCE_READ_BUDGET')
            read.rollback()
            if final_high_water < ready_cursor:
                return base | {'state':'BLOCKED','reason':'LOCAL_EOD_HOT_CURSOR_REGRESSION'}
            if first_high_water == final_high_water == ready_cursor:
                return base | {'state':'LOCAL_EOD_READY','imported':0,'rejected':0,
                    'cursor':ready_cursor,'daily_groups':0,'real_orders_sent':0,
                    'scope':'OBSERVED_BASKET_ONLY','idle_fast_path':True}
        target.parent.mkdir(parents=True, exist_ok=True)
        available = shutil.disk_usage(target.parent).free if free_bytes is None else free_bytes
        if available < RESERVE:
            return base | {"state": "DEFERRED_DISK", "reason": "HOST_RESERVE_REQUIRED"}
        if sum(Path(str(target)+suffix).stat().st_size for suffix in ('','-wal','-shm')
               if Path(str(target)+suffix).exists()) > 2*1024**3-HEADROOM:
            return base | {"state": "BLOCKED", "reason": "COLD_ARCHIVE_PHYSICAL_BUDGET"}
        with target.with_suffix(target.suffix+'.local-eod.lock').open('a+') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            prepare_deadline=time.monotonic()+15
            store = _ColdStore(str(target),deadline=prepare_deadline)
            # Established candle schema only. Never initialize/migrate legacy
            # History v2, assign currency or modify its canonical records.
            init_schema(store)
            if time.monotonic()>prepare_deadline:
                raise ValueError('LOCAL_EOD_COLD_PREPARE_BUDGET')
            with store.connect() as write:
                write.executescript(DDL)
                write.execute('PRAGMA busy_timeout=200')
                write.execute('PRAGMA cache_size=-2048')
                page_size=write.execute('PRAGMA page_size').fetchone()[0]
                write.execute('PRAGMA max_page_count='+str(2*1024**3//page_size))
                write.execute('BEGIN IMMEDIATE')
                prior = write.execute('SELECT cursor FROM local_eod_cursor_v1 WHERE source_id=?',(source_id,)).fetchone()
                cursor = prior[0] if prior else 0
                if time.monotonic()>prepare_deadline:
                    raise ValueError('LOCAL_EOD_COLD_PREPARE_BUDGET')
                deadline=time.monotonic()+2
                read.execute('BEGIN')
                # Recheck after preparation: the earlier CLOSED/PAPER snapshot
                # is admission evidence, never authority for a changed runtime.
                blocked = _runtime_block(read,at)
                if blocked:
                    return base | {'state':'BLOCKED','reason':blocked}
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
                store.deadline=write_deadline
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
    # BACKLOG means a committed bounded batch with durable cursor. The existing
    # timer drains the next batch; blocked/deferred/corrupt remain failures.
    return 0 if result['state'] in {'NOT_DUE','LOCAL_EOD_READY','BACKLOG'} else 2


if __name__=='__main__':
    raise SystemExit(main())
