#!/usr/bin/env python3
"""Durable, resumable PPI API historical ingestion runner for RC6.

This process is intentionally read-only against PPI except for authentication.
It writes only historical/evidence SQLite stores. Runtime ownership belongs to
systemd on the Droplet; GitHub Actions is only the bounded deploy/control plane.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import bf_production_paper_observer as observer
from bd_ppi_readonly_guard import ProductionMarketReader, classify_read_error, retry_read, session_invalid
from cv_history_store_adapter_hf6 import default_history_store
import ct_ppi_history_salvage_hf6 as history_salvage

RUN_ID = os.getenv("PPI_HISTORY_RUN_ID", "PPI-HIST-20260912-001")
TARGET_DAYS = int(os.getenv("PPI_HISTORY_TARGET_DAYS", "365"))
SLEEP_SECONDS = float(os.getenv("PPI_HISTORY_SLEEP_SECONDS", "0.25"))
INTEGRITY_EVERY = max(1, int(os.getenv("PPI_HISTORY_INTEGRITY_EVERY", "10")))
MIN_FREE_GIB = float(os.getenv("PPI_HISTORY_MIN_FREE_GIB", "3"))
HARD_STOP_GIB = float(os.getenv("PPI_HISTORY_HARD_STOP_GIB", "2"))
EXPECTED_IDENTITIES = int(os.getenv("PPI_HISTORY_EXPECTED_IDENTITIES", "1960"))
PIDFILE = os.getenv("PPI_HISTORY_PIDFILE", "/tmp/porota-ppi-fullfamily-history.pid")
STOP = False


def _stop(*_args):
    global STOP
    STOP = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(v) -> str:
    return str(v or "").strip().upper()


def _pick(cols, *names):
    for name in names:
        if name in cols:
            return name
    return None


def _state_schema(store) -> None:
    with store.connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS ppi_history_ingest_runtime(
          run_id TEXT PRIMARY KEY,
          status TEXT NOT NULL,
          family TEXT,
          done_identities INTEGER NOT NULL DEFAULT 0,
          pending_identities INTEGER NOT NULL DEFAULT 0,
          error_identities INTEGER NOT NULL DEFAULT 0,
          current_range TEXT NOT NULL,
          last_committed_batch INTEGER NOT NULL DEFAULT 0,
          heartbeat TEXT NOT NULL,
          rows_canonical INTEGER NOT NULL DEFAULT 0,
          disk_free_gib REAL NOT NULL DEFAULT 0,
          lock_owner TEXT NOT NULL DEFAULT 'systemd:/run/lock/porota-ppi-fullfamily-history.lock',
          safety TEXT NOT NULL DEFAULT '',
          detail TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ppi_history_ingest_tasks(
          run_id TEXT NOT NULL,
          symbol TEXT NOT NULL,
          instrument_type TEXT NOT NULL,
          market TEXT NOT NULL,
          settlement TEXT NOT NULL,
          priority INTEGER NOT NULL,
          state TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0,
          first_before TEXT,
          last_before TEXT,
          first_after TEXT,
          last_after TEXT,
          provider_rows INTEGER NOT NULL DEFAULT 0,
          valid_rows INTEGER NOT NULL DEFAULT 0,
          result TEXT NOT NULL DEFAULT '',
          last_error TEXT NOT NULL DEFAULT '',
          started_at TEXT,
          finished_at TEXT,
          updated_at TEXT NOT NULL,
          PRIMARY KEY(run_id,symbol,instrument_type,market,settlement)
        );
        CREATE INDEX IF NOT EXISTS idx_ppi_history_ingest_tasks_run_state
          ON ppi_history_ingest_tasks(run_id,state,priority,instrument_type,symbol);
        """)


def _safety(store) -> str:
    with store.connect() as c:
        row = c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
    if not row:
        raise RuntimeError("OBSERVER_STATE_MISSING")
    value = f"{row[0]}|{int(row[1] or 0)}"
    if row[0] != "PRODUCTION_PAPER" or int(row[1] or 0) != 0:
        raise RuntimeError("SAFETY_INVARIANT_BROKEN:" + value)
    return value


def _current_universe(store):
    with store.connect() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(ppi_argentina_api_shadow)")]
        sym = _pick(cols, "ticker", "symbol")
        fam = _pick(cols, "instrument_type", "family", "type")
        mkt = _pick(cols, "market")
        stl = _pick(cols, "settlement")
        cur = _pick(cols, "currency", "moneda")
        if not all([sym, fam, mkt, stl]):
            raise RuntimeError("SHADOW_IDENTITY_COLUMNS_MISSING")
        selected = [sym, fam, mkt, stl] + ([cur] if cur else [])
        sql = (
            "SELECT " + ",".join('"'+x+'"' for x in selected)
            + " FROM ppi_argentina_api_shadow "
              "WHERE market IN ('BYMA','ROFEX','A3','OTC') "
              "AND instrument_type NOT IN ('ACCIONES-USA','FCI-EXTERIOR')"
        )
        rows = c.execute(sql).fetchall()
    keys = []
    currencies = {}
    for row in rows:
        key = (_norm(row[0]), _norm(row[1]), _norm(row[2]), _norm(row[3]))
        keys.append(key)
        currencies.setdefault(key, set()).add(_norm(row[4]) if cur else "")
    if len(keys) != EXPECTED_IDENTITIES or len(set(keys)) != EXPECTED_IDENTITIES:
        raise RuntimeError(f"UNIVERSE_DRIFT:{len(keys)}:{len(set(keys))}")
    collisions = [key for key, values in currencies.items() if len(values) > 1]
    if collisions:
        raise RuntimeError(f"CURRENCY_COLLISION:{len(collisions)}")
    return sorted(set(keys), key=lambda x: (x[1], x[0], x[2], x[3]))


def _history_stats(hstore, current_set):
    stats = {}
    with hstore.connect() as c:
        qc = c.execute("PRAGMA quick_check").fetchone()[0]
        if qc != "ok":
            raise RuntimeError("HISTORY_QUICK_CHECK_FAILED:" + str(qc))
        for row in c.execute("""SELECT upper(trim(symbol)),upper(trim(instrument_type)),
                                      upper(trim(market)),upper(trim(settlement)),
                                      count(*),min(date),max(date)
                               FROM history_canonical_v2
                               GROUP BY upper(trim(symbol)),upper(trim(instrument_type)),
                                        upper(trim(market)),upper(trim(settlement))"""):
            key = (row[0], row[1], row[2], row[3])
            if key in current_set:
                stats[key] = (int(row[4] or 0), row[5], row[6])
    return stats


def _expected_latest() -> str:
    local = datetime.now(observer.TZ)
    day = local.date()
    close_minute = observer.MARKET_CLOSE_HOUR * 60 + observer.MARKET_CLOSE_MINUTE
    now_minute = local.hour * 60 + local.minute
    if observer._business_day(day) and now_minute >= close_minute:
        return day.isoformat()
    day -= timedelta(days=1)
    while not observer._business_day(day):
        day -= timedelta(days=1)
    return day.isoformat()


def _disk_free_gib(hstore) -> float:
    folder = os.path.dirname(os.path.abspath(hstore.path)) or "."
    return shutil.disk_usage(folder).free / (1024 ** 3)


def _canonical_rows(hstore) -> int:
    with hstore.connect() as c:
        row = c.execute("SELECT count(*) FROM history_canonical_v2").fetchone()
    return int(row[0] or 0)


def _duplicates(hstore) -> int:
    with hstore.connect() as c:
        row = c.execute("""SELECT count(*) FROM (
          SELECT symbol,instrument_type,market,settlement,date,count(*) n
          FROM history_canonical_v2
          GROUP BY symbol,instrument_type,market,settlement,date HAVING n>1)""").fetchone()
    return int(row[0] or 0)


def _integrity(hstore) -> None:
    with hstore.connect() as c:
        qc = c.execute("PRAGMA quick_check").fetchone()[0]
    if qc != "ok":
        raise RuntimeError("HISTORY_QUICK_CHECK_FAILED:" + str(qc))
    dup = _duplicates(hstore)
    if dup:
        raise RuntimeError(f"CANONICAL_DUPLICATES:{dup}")


def _counts(store):
    terminal = ("ALREADY_COVERED", "DONE_VALID", "DONE_PARTIAL", "DONE_EMPTY")
    with store.connect() as c:
        rows = c.execute("SELECT state,count(*) FROM ppi_history_ingest_tasks WHERE run_id=? GROUP BY state", (RUN_ID,)).fetchall()
    by = {str(r[0]): int(r[1]) for r in rows}
    done = sum(by.get(x, 0) for x in terminal)
    pending = sum(by.get(x, 0) for x in ("PENDING", "RETRYABLE", "RUNNING"))
    errors = by.get("ERROR", 0)
    return done, pending, errors, by


def _runtime(store, hstore, *, status, family="", detail="", committed=None):
    done, pending, errors, _ = _counts(store)
    now = _now()
    free = _disk_free_gib(hstore)
    rows = _canonical_rows(hstore)
    safety = _safety(store)
    start = (datetime.now(observer.TZ).date() - timedelta(days=TARGET_DAYS)).isoformat()
    end = datetime.now(observer.TZ).date().isoformat()
    with store.connect() as c:
        previous = c.execute("SELECT last_committed_batch FROM ppi_history_ingest_runtime WHERE run_id=?", (RUN_ID,)).fetchone()
        batch = int(previous[0] or 0) if previous else 0
        if committed is not None:
            batch = int(committed)
        c.execute("""INSERT INTO ppi_history_ingest_runtime(
          run_id,status,family,done_identities,pending_identities,error_identities,
          current_range,last_committed_batch,heartbeat,rows_canonical,disk_free_gib,
          lock_owner,safety,detail,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(run_id) DO UPDATE SET
            status=excluded.status,family=excluded.family,
            done_identities=excluded.done_identities,pending_identities=excluded.pending_identities,
            error_identities=excluded.error_identities,current_range=excluded.current_range,
            last_committed_batch=excluded.last_committed_batch,heartbeat=excluded.heartbeat,
            rows_canonical=excluded.rows_canonical,disk_free_gib=excluded.disk_free_gib,
            lock_owner=excluded.lock_owner,safety=excluded.safety,detail=excluded.detail,
            updated_at=excluded.updated_at""",
          (RUN_ID,status,family,done,pending,errors,f"{start}/{end}",batch,now,rows,free,
           "systemd:/run/lock/porota-ppi-fullfamily-history.lock",safety,detail[:1500],now))
    print("RUNTIME=" + json.dumps({"run_id":RUN_ID,"status":status,"family":family,
          "done":done,"pending":pending,"errors":errors,"batch":batch,
          "heartbeat":now,"rows":rows,"disk_free_gib":round(free,3),"safety":safety},
          sort_keys=True), flush=True)
    return batch


def _seed_tasks(store, hstore) -> None:
    universe = _current_universe(store)
    current_set = set(universe)
    stats = _history_stats(hstore, current_set)
    target_start = (datetime.now(observer.TZ).date() - timedelta(days=TARGET_DAYS)).isoformat()
    recent = _expected_latest()
    now = _now()
    with store.connect() as c:
        # A prior process may only leave RUNNING after releasing the host lock.
        # This runner is invoked only while owning that lock, so retry is safe.
        c.execute("""UPDATE ppi_history_ingest_tasks
                     SET state='RETRYABLE',updated_at=?,last_error=CASE WHEN last_error='' THEN 'recovered_after_lock_reacquire' ELSE last_error END
                     WHERE run_id=? AND state='RUNNING'""", (now, RUN_ID))
        for key in universe:
            before = stats.get(key)
            if before is None:
                priority, state = 0, "PENDING"
                first_before = last_before = None
            else:
                _, first_before, last_before = before
                if not last_before or last_before < recent:
                    priority, state = 1, "PENDING"
                elif not first_before or first_before > target_start:
                    priority, state = 2, "PENDING"
                else:
                    priority, state = 9, "ALREADY_COVERED"
            c.execute("""INSERT OR IGNORE INTO ppi_history_ingest_tasks(
              run_id,symbol,instrument_type,market,settlement,priority,state,
              first_before,last_before,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (RUN_ID,key[0],key[1],key[2],key[3],priority,state,first_before,last_before,now))
        count = c.execute("SELECT count(*) FROM ppi_history_ingest_tasks WHERE run_id=?", (RUN_ID,)).fetchone()[0]
    if int(count) != EXPECTED_IDENTITIES:
        raise RuntimeError(f"TASK_UNIVERSE_MISMATCH:{count}")


def _next_task(store):
    with store.connect() as c:
        row = c.execute("""SELECT symbol,instrument_type,market,settlement,priority,attempts
                           FROM ppi_history_ingest_tasks
                           WHERE run_id=? AND state IN ('PENDING','RETRYABLE')
                           ORDER BY priority,instrument_type,symbol,market,settlement LIMIT 1""",
                        (RUN_ID,)).fetchone()
    return tuple(row) if row else None


def _coverage(hstore, key):
    with hstore.connect() as c:
        row = c.execute("""SELECT count(*),min(date),max(date) FROM history_canonical_v2
                           WHERE upper(trim(symbol))=? AND upper(trim(instrument_type))=?
                             AND upper(trim(market))=? AND upper(trim(settlement))=?""", key).fetchone()
    return int(row[0] or 0), row[1], row[2]


def _mark_running(store, key):
    now = _now()
    with store.connect() as c:
        c.execute("""UPDATE ppi_history_ingest_tasks
                     SET state='RUNNING',attempts=attempts+1,started_at=COALESCE(started_at,?),updated_at=?
                     WHERE run_id=? AND symbol=? AND instrument_type=? AND market=? AND settlement=?""",
                  (now,now,RUN_ID,*key))


def _mark_done(store, key, state, result, before, after):
    now = _now()
    provider_rows = int(result.get("provider_rows",0) or 0)
    valid_rows = int(result.get("valid_rows",0) or 0)
    detail = json.dumps({k:result.get(k) for k in (
        "provider_rows","valid_rows","rejected_rows","storage_quality","context_state",
        "canonical_updates","versions_appended","first_date","last_date")}, sort_keys=True, default=str)
    with store.connect() as c:
        c.execute("""UPDATE ppi_history_ingest_tasks SET state=?,first_before=COALESCE(first_before,?),
                     last_before=COALESCE(last_before,?),first_after=?,last_after=?,provider_rows=?,valid_rows=?,
                     result=?,last_error='',finished_at=?,updated_at=?
                     WHERE run_id=? AND symbol=? AND instrument_type=? AND market=? AND settlement=?""",
                  (state,before[1],before[2],after[1],after[2],provider_rows,valid_rows,detail,now,now,RUN_ID,*key))


def _mark_error(store, key, exc, retryable=False):
    now = _now()
    state = "RETRYABLE" if retryable else "ERROR"
    detail = f"{classify_read_error(exc)}:{type(exc).__name__}:{str(exc)[:500]}"
    with store.connect() as c:
        c.execute("""UPDATE ppi_history_ingest_tasks SET state=?,last_error=?,finished_at=?,updated_at=?
                     WHERE run_id=? AND symbol=? AND instrument_type=? AND market=? AND settlement=?""",
                  (state,detail,now,now,RUN_ID,*key))
    return detail


def main() -> int:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    with open(PIDFILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

    store = observer.runtime_store()
    hstore = default_history_store()
    reader = None
    try:
        _state_schema(store)
        safety = _safety(store)
        free = _disk_free_gib(hstore)
        if free < HARD_STOP_GIB:
            _runtime(store,hstore,status="BLOCKED_DISK_HARD",detail=f"free_gib={free:.3f}")
            return 0
        if free < MIN_FREE_GIB:
            _runtime(store,hstore,status="BLOCKED_DISK_START",detail=f"free_gib={free:.3f}")
            return 0
        _seed_tasks(store,hstore)
        committed = _runtime(store,hstore,status="STARTING",detail="queue_seeded_365d")

        key_secret = observer._secret()
        reader = ProductionMarketReader(key_secret[0], key_secret[1], audit=store.audit_http)
        reader.login_once()
        _runtime(store,hstore,status="RUNNING",detail="ppi_readonly_login_ok",committed=committed)

        processed_since_integrity = 0
        while not STOP:
            task = _next_task(store)
            if task is None:
                _integrity(hstore)
                _, _, errors, by = _counts(store)
                status = "API_PASS_COMPLETE" if errors == 0 else "API_PASS_COMPLETE_WITH_ERRORS"
                _runtime(store,hstore,status=status,detail=json.dumps(by,sort_keys=True),committed=committed)
                return 0

            symbol, family, market, settlement, _priority, _attempts = task
            key = (symbol,family,market,settlement)
            _safety(store)
            free = _disk_free_gib(hstore)
            if free < HARD_STOP_GIB:
                _runtime(store,hstore,status="BLOCKED_DISK_HARD",family=family,detail=f"free_gib={free:.3f}",committed=committed)
                return 0
            if free < MIN_FREE_GIB:
                _runtime(store,hstore,status="BLOCKED_DISK_CONTINUE",family=family,detail=f"free_gib={free:.3f}",committed=committed)
                return 0

            before = _coverage(hstore,key)
            _mark_running(store,key)
            _runtime(store,hstore,status="RUNNING",family=family,
                     detail=f"request={symbol}|{family}|{market}|{settlement}",committed=committed)
            start = datetime.now(observer.TZ).date() - timedelta(days=TARGET_DAYS)
            end = datetime.now(observer.TZ).date()
            try:
                payload = retry_read(lambda: reader.history(symbol,family,settlement,start,end), retries=1)
                attempted = _now()
                result = history_salvage.ingest_ppi_payload(
                    store,symbol=symbol,instrument_type=family,market=market,settlement=settlement,
                    payload=payload,requested_from=start,requested_to=end,attempted_at=attempted,
                    history_store=hstore)
                after = _coverage(hstore,key)
                valid = int(result.get("valid_rows",0) or 0)
                provider = int(result.get("provider_rows",0) or 0)
                rejected = int(result.get("rejected_rows",0) or 0)
                if valid <= 0:
                    task_state = "DONE_EMPTY"
                elif rejected > 0 or valid < provider:
                    task_state = "DONE_PARTIAL"
                else:
                    task_state = "DONE_VALID"
                _mark_done(store,key,task_state,result,before,after)
                committed += 1
                processed_since_integrity += 1
                if processed_since_integrity >= INTEGRITY_EVERY:
                    _integrity(hstore)
                    processed_since_integrity = 0
                _runtime(store,hstore,status="RUNNING",family=family,
                         detail=f"committed={symbol}|{task_state}|valid={valid}|provider={provider}",committed=committed)
            except Exception as exc:
                if session_invalid(exc):
                    detail = _mark_error(store,key,exc,retryable=True)
                    _runtime(store,hstore,status="BLOCKED_AUTH",family=family,detail=detail,committed=committed)
                    return 0
                detail = _mark_error(store,key,exc,retryable=False)
                committed += 1
                _runtime(store,hstore,status="RUNNING",family=family,detail=f"error_committed={symbol}|{detail}",committed=committed)
            time.sleep(SLEEP_SECONDS)

        _integrity(hstore)
        _runtime(store,hstore,status="PAUSED",detail="SIGTERM_controlled_pause",committed=committed)
        return 0
    except Exception as exc:
        try:
            status = "BLOCKED_SAFETY" if "SAFETY_" in str(exc) else "BLOCKED_INTEGRITY"
            _runtime(store,hstore,status=status,detail=f"{type(exc).__name__}:{str(exc)[:1200]}")
        except Exception:
            print(f"FATAL={type(exc).__name__}:{str(exc)[:1200]}", flush=True)
        return 0
    finally:
        if reader is not None:
            try:
                reader.close()
            except Exception:
                pass
        try:
            if os.path.exists(PIDFILE) and open(PIDFILE,encoding="utf-8").read().strip() == str(os.getpid()):
                os.unlink(PIDFILE)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
