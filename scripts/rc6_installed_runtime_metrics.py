"""Bounded passive metrics from the exact installed RC6 IOL release."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import time

from scripts.porota_rootless_host import DB_RELATIVE, database_snapshot, decode, require


SCHEMA = "porota.rc6.installed-runtime-metrics.v1"
CONTAINER_PROBE = r'''
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import sys
import time

from bl_candle_engine import fingerprint
from cg_paper_workspace import artifact_root
from co_market_sessions_hf6 import TZ
from rc6_scheduler_snapshot import instant
from rc6_shadow_runtime.persistence import EvidenceFiles
from rc6_shadow_runtime.timing import (PUBLICATION_COORDINATES, PUBLICATION_HISTORY_FILE,
    PUBLICATION_HISTORY_LIMIT, PUBLICATION_HISTORY_SCHEMA)
from scripts.porota_rootless_host import IOL_CONTROLLER_CHILDREN, read_controller_health, observe_iol_owner

HOT = Path('/app/data/paper_v17/observer_v17.db')
COLD = Path('/app/data/market_history.db')
CG = Path('/sys/fs/cgroup')
START = time.monotonic()
STATES = {
    'RUNNING', 'STARTUP_WAIT', 'STARTING', 'READY', 'READY_PREOPEN', 'WAITING_MARKET', 'WAITING_WINDOW',
    'DEGRADED', 'STOPPED', 'RESTART_WAIT', 'EXITED', 'FAILED', 'BACKOFF', 'COOLDOWN', 'IDLE',
    'MARKET_CLOSED', 'MARKET_OPEN', 'CLOSED', 'OPEN', 'PREOPEN', 'CHECKING', 'STOPPING',
    'SHADOW_OBSERVING', 'SHADOW_IDLE_MARKET_CLOSED', 'FAIL_CLOSED', 'LOCAL_EOD_READY', 'BACKLOG',
    'PREOPEN_SNAPSHOT_REQUIRED_DURING_SESSION', 'COMPLETE', 'COMMITTED', 'NOT_ATTEMPTED', 'OK', 'ERROR',
}
SOURCES = {'IOL_REST', 'IOL_REST_TRADE_SAMPLE', 'PPI_GUARDED_CURRENT', 'PPI_MARKETDATA_CURRENT',
           'PRODUCTION_PAPER_SNAPSHOTS', 'PRODUCTION_PAPER', 'IOL_MCP', 'A3_CEM_CLOSING',
           'PPI_PRODUCTION_HISTORY_REPORTED', 'BYMA_INTERVALORES', 'DATA912'}

def timestamp(value):
    parsed = instant(value)
    return parsed.astimezone(timezone.utc).isoformat() if parsed else None

def age(value):
    parsed = instant(value)
    return round((datetime.now(timezone.utc) - parsed).total_seconds(), 3) if parsed else None

def state(value):
    return value if isinstance(value, str) and value in STATES else None

def integer(value):
    return int(value) if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 24 else None

def count(value):
    return value if type(value) is int and value >= 0 else None

def read_small(path):
    try:
        with path.open(encoding='ascii') as source:
            return source.read(8192).strip()
    except (OSError, UnicodeError):
        return ''

def counters(name, allowed):
    result = {}
    for line in read_small(CG / name).splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] in allowed and integer(fields[1]) is not None:
            result[fields[0]] = integer(fields[1])
    return result

def cgroup():
    cpu = read_small(CG / 'cpu.max').split()
    fields = ('memory.current', 'memory.max', 'pids.current', 'pids.max')
    usage = {name.replace('.', '_'): integer(read_small(CG / name)) for name in fields}
    return {**usage, 'state': 'OBSERVED' if len(cpu) == 2 else 'NO_VERIFICADO',
            'reason': None if len(cpu) == 2 else 'CGROUP_V2_CPU_LIMIT_UNAVAILABLE',
            'cpu_quota_us': integer(cpu[0]) if len(cpu) == 2 else None,
            'cpu_period_us': integer(cpu[1]) if len(cpu) == 2 else None,
            'cpu_unlimited': cpu[0] == 'max' if len(cpu) == 2 else None,
            'cpu_stat': counters('cpu.stat', {'usage_usec', 'user_usec', 'system_usec', 'nr_periods', 'nr_throttled', 'throttled_usec'}),
            'memory_stat': counters('memory.stat', {'anon', 'file', 'kernel', 'inactive_file', 'active_file'}),
            'memory_events': counters('memory.events', {'low', 'high', 'max', 'oom', 'oom_kill', 'oom_group_kill'})}

def cgroup_delta(first, last, seconds):
    cpu_delta = {}
    mem_events = {}
    for name, deltas in (('cpu_stat', cpu_delta), ('memory_events', mem_events)):
        for key, before in first[name].items():
            after = last[name].get(key)
            if after is not None and after >= before:
                deltas[key] = after - before
    period_count = cpu_delta.get('nr_periods')
    throttled = cpu_delta.get('nr_throttled')
    before, after = first['memory_current'], last['memory_current']
    file_cache = last['memory_stat'].get('inactive_file')
    return {'state': 'OBSERVED' if 'usage_usec' in cpu_delta and after is not None else 'NO_VERIFICADO',
            'duration_s': round(seconds, 6), 'cpu_delta': cpu_delta, 'memory_event_delta': mem_events,
            'cpu_cores_mean': round(cpu_delta['usage_usec'] / (seconds * 1e6), 4)
                              if seconds > 0 and 'usage_usec' in cpu_delta else None,
            'cpu_throttled_period_fraction': round(throttled / period_count, 5)
                if period_count and throttled is not None and throttled <= period_count else None,
            'memory_current_delta_bytes': after - before if before is not None and after is not None else None,
            'memory_less_inactive_file_bytes': max(0, after - file_cache)
                if after is not None and file_cache is not None else None}

def wal_size(database):
    try:
        if not stat.S_ISREG(database.lstat().st_mode):
            return None
    except OSError:
        return None
    try:
        info = database.with_name(database.name + '-wal').lstat()
        return info.st_size if stat.S_ISREG(info.st_mode) else None
    except FileNotFoundError:
        return 0
    except OSError:
        return None

def wal_window(before, after):
    return {'start_bytes': before, 'end_bytes': after,
            'delta_bytes': after - before if before is not None and after is not None else None}

def observation_lag(observations, clock_column):
    latencies = []
    for row in observations:
        event, received = instant(row[clock_column]), instant(row[1])
        if event and received and event <= received:
            latencies.append(round((received - event).total_seconds(), 3))
    if not latencies:
        return {'samples': 0, 'reason': 'NO_VALID_IOL_EVENT_CLOCKS_IN_SAMPLE'}
    latencies.sort()
    middle = len(latencies) // 2
    median = latencies[middle] if len(latencies) % 2 else (latencies[middle-1] + latencies[middle]) / 2
    return {'samples': len(latencies), 'min_s': latencies[0], 'median_s': round(median, 3), 'max_s': latencies[-1]}

def worker_pulse(value, checked_at):
    return {'state': state(value), 'heartbeat_at': timestamp(checked_at), 'heartbeat_age_s': age(checked_at)}

def controller_metrics(sha, tree):
    path = artifact_root(HOT) / 'runtime-health.json'
    report = read_controller_health(path, provider='IOL_REST')
    if not report or report.get('source_sha') != sha or report.get('candidate_tree_sha') != tree or report.get('market_provider') != 'IOL_REST':
        return {'state': 'NO_VERIFICADO', 'reason': 'CONTROLLER_RECEIPT_MISSING_OR_RELEASE_MISMATCH'}
    children = report['children']
    workers = {}
    for role in IOL_CONTROLLER_CHILDREN:
        row = children.get(role, {})
        workers[role] = {'state': state(row.get('state')), 'pid_alive': row.get('alive') is True,
                         'started_at': timestamp(row.get('started_at')), 'pid': count(row.get('pid'))}
    stamp = age(report.get('recorded_at'))
    return {'state': 'OBSERVED', 'roles_match': report.get('roles_match') is True,
            'controller_age_s': stamp, 'fresh_within_120s': stamp is not None and 0 <= stamp <= 120,
            'recorded_at': timestamp(report.get('recorded_at')), 'workers': workers}

def publication_window(files, worker, status, sha, tree):
    unavailable = {'state': 'NO_VERIFICADO', 'reason': 'SHADOW_GENERATION_EVIDENCE_UNAVAILABLE'}
    try:
        history = files.read(PUBLICATION_HISTORY_FILE)
        pointer = files.read('CURRENT.json')
    except (OSError, ValueError, AttributeError):
        return unavailable
    started, now = instant(worker.get('started_at')), datetime.now(timezone.utc)
    if (not isinstance(history, dict) or not isinstance(pointer, dict) or not isinstance(status, dict)
            or started is None or worker.get('pid_alive') is not True or not worker.get('pid')
            or history.get('schema') != PUBLICATION_HISTORY_SCHEMA or history.get('worker_pid') != worker['pid']
            or history.get('source_sha') != sha or history.get('source_tree') != tree):
        return unavailable
    samples = history.get('observations')
    if not isinstance(samples, list) or not 1 <= len(samples) <= PUBLICATION_HISTORY_LIMIT:
        return unavailable
    latest = samples[-1]
    if (not isinstance(latest, dict) or any(pointer.get(key) != latest.get(key) for key in PUBLICATION_COORDINATES)
            or type(latest.get('sequence')) is not int or status.get('as_of') != latest.get('source_as_of')
            or status.get('status') != 'SHADOW_OBSERVING' or type(status.get('real_orders_sent')) is not int
            or status['real_orders_sent'] != 0 or status.get('real_routes') != 'NOT_CALLED'):
        return unavailable
    day = now.astimezone(TZ).date()
    durations = []
    previous_sequence = 0
    for row in samples:
        if not isinstance(row, dict):
            return unavailable
        observed, published = instant(row.get('source_as_of')), instant(row.get('publish_completed_at'))
        sequence, seconds = row.get('sequence'), row.get('total_seconds')
        if (observed is None or published is None or not started <= observed <= published <= now
                or type(sequence) is not int or sequence <= previous_sequence
                or type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0):
            return unavailable
        if observed.astimezone(TZ).date() == day and row.get('phase') == 'OPEN':
            durations.append(seconds)
        previous_sequence = sequence
    durations.sort()
    p95 = None
    if len(durations) > 1:
        rank = .95 * (len(durations) - 1)
        index = int(rank)
        p95 = round(durations[index] + (rank - index) * (durations[index + 1] - durations[index]), 6)
    return {'state': 'OBSERVED', 'worker_started_at': worker['started_at'], 'worker_pid': worker['pid'],
            'last_generation': latest, 'session_date_ar': day.isoformat(), 'open_cycle_samples': len(durations),
            'open_cycle_p95_seconds': p95,
            'open_cycle_p95_state': 'OBSERVED' if p95 is not None else 'NO_VERIFICADO'}

def shadow_metrics(worker, sha, tree):
    files = EvidenceFiles(artifact_root(HOT) / 'dynamic-shadow', payload_limit=131072)
    result = {}
    status = None
    for name in ('idle', 'status'):
        try:
            row = files.read(name + '.json')
        except (OSError, ValueError, AttributeError):
            result[name] = {'state': 'NO_VERIFICADO', 'reason': 'SHADOW_RECEIPT_READ_OR_DIGEST_UNVERIFIED'}
            continue
        if row is None:
            result[name] = {'state': 'NO_VERIFICADO', 'reason': 'SHADOW_RECEIPT_ABSENT'}
            continue
        if isinstance(row, dict) and row.get('real_routes') == 'NOT_CALLED' and type(row.get('real_orders_sent')) is int and row['real_orders_sent'] == 0:
            clock = row.get('heartbeat_at') if name == 'idle' else row.get('as_of')
            observed, started = instant(clock), instant(worker.get('started_at'))
            current = bool(worker.get('pid_alive') and observed and started
                           and started <= observed <= datetime.now(timezone.utc))
            receipt = {'state': state(row.get('state') or row.get('status')) if current else 'NO_VERIFICADO',
                       'at': timestamp(clock), 'age_s': age(clock), 'after_worker_start': current}
            if not current:
                receipt['reason'] = 'SHADOW_RECEIPT_NOT_FROM_CURRENT_WORKER'
            timing = row.get('generation_timing')
            if current and name == 'status':
                status = row
            if current and isinstance(timing, dict) and name == 'status':
                receipt['compute_seconds'] = {key: timing[key] for key in ('poll_seconds', 'tick_seconds', 'tick_cpu_seconds')
                                              if type(timing.get(key)) in (int, float) and math.isfinite(timing[key]) and timing[key] >= 0}
            result[name] = receipt
        else:
            result[name] = {'state': 'NO_VERIFICADO', 'reason': 'SHADOW_RECEIPT_SAFETY_CONTRACT_UNVERIFIED'}
    result['publication_window'] = publication_window(files, worker, status, sha, tree)
    result['cold_queue'] = cold_queue(files, result['publication_window'].get('last_generation', {}))
    return result

def cold_queue(files, generation):
    from rc6_shadow_runtime.cold_audit import Directory

    try:
        with Directory(files) as directory:
            queue = directory.read('queue.json')
    except (OSError, ValueError):
        queue = None
    if not isinstance(queue, dict) or queue.get('state') not in {'PENDING', 'COMPLETE', 'FAILED'}:
        return {'state': 'NO_VERIFICADO'}
    matches = generation.get('cold_snapshot_id') is not None and generation['cold_snapshot_id'] == queue.get('snapshot_id')
    return {'state': 'OBSERVED', 'queue_state': queue['state'], 'matches_current_generation': matches}

def quote_provenance(value):
    if value in SOURCES:
        return value
    if isinstance(value, str) and value.startswith('PPI_CATALOG:'):
        return 'PPI_CATALOG_PROVENANCE'
    return 'OTHER' if value else 'UNATTRIBUTED'

def rows(db, sql, params=()):
    until = min(START + 6, time.monotonic() + .33)
    try:
        if time.monotonic() < until:
            db.set_progress_handler(lambda: int(time.monotonic() >= until), 1000)
            return db.execute(sql, params).fetchall()
    except sqlite3.Error:
        pass
    finally:
        db.set_progress_handler(None, 0)
    return None

def one(db, sql, params=()):
    observed = rows(db, sql, params)
    return observed[0] if observed else None

def open_ro(path):
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.2)
    connection.execute('PRAGMA query_only=ON')
    connection.execute('BEGIN')
    return connection

def sqlite_metrics(db):
    started = time.perf_counter()
    read_ok = one(db, 'SELECT 1') == (1,)
    elapsed = round((time.perf_counter() - started) * 1000, 3)
    info = {'query_only': one(db, 'PRAGMA query_only') == (1,),
            'select_1_ok': read_ok, 'select_1_ms': elapsed if read_ok else None}
    for name in ('schema_version', 'page_count', 'page_size', 'freelist_count', 'journal_mode'):
        row = one(db, 'PRAGMA ' + name)
        value = row[0] if row else None
        if type(value) is int or isinstance(value, str) and value in {'wal', 'delete', 'truncate', 'persist', 'off', 'memory'}:
            info[name] = value
    return info

def hot_metrics():
    try:
        with closing(open_ro(HOT)) as db:
            result = {'state': 'OBSERVED', 'sqlite': sqlite_metrics(db), 'worker_pulses': {}}
            started = time.perf_counter()
            scanner = one(db, 'SELECT process_state,heartbeat_at,session_state,last_market_data_at,ppi_auth,mode,real_orders_sent FROM observer_state WHERE id=1')
            result['sqlite']['observer_state_read_ms'] = round((time.perf_counter() - started) * 1000, 3) if scanner else None
            pulses = result['worker_pulses']
            if scanner:
                pulses['scanner'] = worker_pulse(*scanner[:2])
                result.update({'session': state(scanner[2]), 'last_market_data_at': timestamp(scanner[3]),
                               'ppi_auth': state(scanner[4]), 'paper_mode': scanner[5] == 'PRODUCTION_PAPER',
                               'real_orders_sent': scanner[6] if type(scanner[6]) is int else None})
            for role, table in (('supervisor', 'paper_supervisor_state'), ('exit_reader', 'paper_exit_reader_state'),
                                ('notifications', 'paper_notification_worker'), ('candles', 'candle_worker_state'),
                                ('intraday_scalping_prior', 'intraday_scalping_worker_state'),
                                ('caucion_cash_sweep_prior', 'paper_caucion_cash_sweep_state')):
                row = one(db, 'SELECT state,heartbeat_at FROM ' + table + ' WHERE id=1')
                pulses[role] = worker_pulse(*row) if row else None
            clocks = rows(db, 'SELECT metadata_source,observed_at,trade_at,book_at FROM market_snapshots ORDER BY id DESC LIMIT 128')
            if clocks is not None:
                counts = Counter(quote_provenance(row[0]) for row in clocks)
                newest = max((stamp for row in clocks if (stamp := timestamp(row[1]))), default=None)
                iol = [row for row in clocks if row[0] == 'IOL_REST']
                result['snapshot_tail'] = {'sample_limit': 128, 'rows_by_source': dict(counts), 'latest_observed_at': newest,
                                           'iol_trade_event_to_observation': observation_lag(iol, 2),
                                           'iol_book_event_to_observation': observation_lag(iol, 3)}
            refresh = one(db, 'SELECT state,checked_at FROM candidate_refresh_state WHERE id=1')
            result['catalog_refresh'] = {'state': state(refresh[0]), 'checked_at': timestamp(refresh[1])} if refresh else None
            candles = one(db, 'SELECT id,bar_end,known_at FROM candle_versions ORDER BY id DESC LIMIT 1')
            worker = one(db, 'SELECT cursor FROM candle_worker_state WHERE id=1')
            counts = one(db, 'SELECT COUNT(*) FROM candle_dirty')
            result['candles'] = {'latest_revision_id': count(candles[0]) if candles else None,
                                 'last_revision_bar_end': timestamp(candles[1]) if candles else None,
                                 'last_revision_known_at': timestamp(candles[2]) if candles else None,
                                 'worker_cursor': count(worker[0]) if worker else None,
                                 'dirty_bars': count(counts[0]) if counts else None}
            return result
    except (OSError, sqlite3.Error):
        return {'state': 'NO_VERIFICADO', 'reason': 'HOT_DATABASE_READ_UNAVAILABLE'}

def cold_metrics():
    try:
        info = HOT.stat()
        source_id = fingerprint({'path': str(HOT), 'device': info.st_dev, 'inode': info.st_ino})
        with closing(open_ro(COLD)) as db:
            result = {'state': 'OBSERVED', 'sqlite': sqlite_metrics(db)}
            started = time.perf_counter()
            cursor = one(db, 'SELECT cursor,as_of,state FROM local_eod_cursor_v1 WHERE source_id=?', (source_id,))
            result['sqlite']['archive_cursor_read_ms'] = round((time.perf_counter() - started) * 1000, 3) if cursor else None
            if cursor and count(cursor[0]) is not None:
                receipt = one(db, 'SELECT published_at FROM local_eod_receipts_v1 WHERE source_id=? AND version_id=?', (source_id, cursor[0]))
                result['local_eod'] = {'cursor': cursor[0], 'as_of': timestamp(cursor[1]), 'state': state(cursor[2]),
                                       'cursor_has_receipt': bool(receipt), 'receipt_published_at': timestamp(receipt[0]) if receipt else None}
            latest = one(db, 'SELECT source,version_known_at FROM history_versions_v2 ORDER BY id DESC LIMIT 1')
            if latest:
                result['latest_history_version'] = {'source': latest[0] if latest[0] in SOURCES else 'OTHER',
                                                    'known_at': timestamp(latest[1])}
            return result
    except (OSError, sqlite3.Error):
        return {'state': 'NO_VERIFICADO', 'reason': 'COLD_DATABASE_READ_UNAVAILABLE'}

def main():
    role, = sys.argv[1:]
    assert role in ('observer', 'dashboard') and os.getenv('POROTA_RC6_MARKET_PROVIDER') == 'IOL_REST'
    sha, tree = os.getenv('POROTA_BUILD_SHA'), os.getenv('POROTA_CANDIDATE_TREE_SHA')
    started_at, sample_start = datetime.now(timezone.utc), time.monotonic()
    first = cgroup()
    result = {'source_sha': sha, 'source_tree': tree, 'role': role}
    if role == 'observer':
        wal_before = {'hot': wal_size(HOT), 'cold': wal_size(COLD)}
        started = time.perf_counter()
        owner = observe_iol_owner(HOT)
        result['gateway_control'] = {'state': 'OBSERVED' if owner else 'NO_VERIFICADO',
                                     'reason': None if owner else 'GATEWAY_CONTROL_RECEIPT_UNAVAILABLE',
                                     'elapsed_ms': round((time.perf_counter() - started) * 1000, 3),
                                     'authenticated': owner.get('authenticated') is True if owner else None,
                                     'wire_attempts': count(owner.get('wire_attempts')) if owner else None, 'provider_probed': False}
        controller = controller_metrics(sha, tree)
        worker = controller.get('workers', {}).get('dynamic_shadow', {}) if controller.get('fresh_within_120s') else {}
        result.update({'controller': controller, 'dynamic_shadow': shadow_metrics(worker, sha, tree),
                       'hot': hot_metrics(), 'cold': cold_metrics()})
    time.sleep(max(0.0, sample_start + 1 - time.monotonic()))
    last = cgroup()
    result['cgroup'] = last
    result['sample_window'] = cgroup_delta(first, last, time.monotonic() - sample_start)
    result['sample_window'].update(started_at=started_at.isoformat(), finished_at=datetime.now(timezone.utc).isoformat())
    if role == 'observer':
        result['sample_window']['wal'] = {name: wal_window(wal_before[name], wal_size(path))
                                         for name, path in (('hot', HOT), ('cold', COLD))}
    print(json.dumps(result, sort_keys=True, allow_nan=False))

if __name__ == '__main__':
    main()
'''


def _host_capacity(manager):
    filesystems = {}
    for label, path in (("data", manager.root / "data"), ("docker", manager.identity.store)):
        try:
            fs = os.statvfs(path)
            filesystems[label] = {"free_bytes": fs.f_bavail * fs.f_frsize, "total_bytes": fs.f_blocks * fs.f_frsize,
                                  "free_inodes": fs.f_favail, "total_inodes": fs.f_files}
        except OSError:
            filesystems[label] = None
    memory = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            key, _, values = line.partition(":")
            if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
                amount, unit = values.split()
                if amount.isdecimal() and unit == "kB":
                    memory[key] = int(amount) * 1024
    except (OSError, UnicodeError, ValueError):
        pass
    try:
        load = [round(value, 3) for value in os.getloadavg()]
    except OSError:
        load = None
    return {"filesystems": filesystems, "memory_bytes": memory, "load_average_1_5_15m": load,
            "visible_logical_cpus": os.cpu_count()}


def _container_metrics(manager, current, role):
    started = time.perf_counter()
    try:
        raw = manager.docker.call("container", "exec", current["roles"][role], "timeout", "--kill-after=1s",
                                  "9s", "python", "-B", "-c", CONTAINER_PROBE, role, timeout=11)
        result = decode(raw)
        require(isinstance(result, dict) and result.get("role") == role and
                all(result.get(key) == current[key] for key in ("source_sha", "source_tree")),
                "METRICS_CONTAINER_RELEASE_MISMATCH")
    except (RuntimeError, OSError, ValueError, TypeError):
        return {"state": "NO_VERIFICADO", "reason": "INSTALLED_CONTAINER_METRICS_UNAVAILABLE"}
    return {"state": "OBSERVED", "container_id": current["roles"][role],
            "container_exec_ms": round((time.perf_counter() - started) * 1000, 3), **result}


def observe_runtime_metrics(manager, current):
    """Use the caller's verified rootless manager and shared release lock."""
    from rc6_backup_policy import backups_suspended

    require(current.get("state") == "VALIDADO_RUNTIME" and current.get("market_provider") == "IOL_REST",
            "INSTALLED_IOL_RELEASE_REQUIRED")
    before = database_snapshot(manager.root / DB_RELATIVE)
    require(before == current.get("database"), "METRICS_DATABASE_IDENTITY_CHANGED")
    with ThreadPoolExecutor(max_workers=2) as executor:
        probes = {role: executor.submit(_container_metrics, manager, current, role) for role in ("observer", "dashboard")}
        capacity = _host_capacity(manager)
        containers = {role: future.result() for role, future in probes.items()}
    after = database_snapshot(manager.root / DB_RELATIVE)
    require(after == before, "METRICS_DATABASE_IDENTITY_CHANGED")
    observer = containers["observer"]
    hot = observer.get("hot", {})
    require(hot.get("real_orders_sent") in (None, 0), "METRICS_REAL_ORDER_OBSERVED")
    return {"schema": SCHEMA, "state": "OBSERVED" if observer["state"] == "OBSERVED" else "NO_VERIFICADO",
            "reason": observer.get("reason"),
            **{key: current[key] for key in ("source_sha", "source_tree", "image_id", "attempt")},
            "observed_at": datetime.now(timezone.utc).isoformat(), "provider_traffic_generated": 0,
            "read_only": True, "real_orders_sent": after["real_orders_sent"], "host_capacity": capacity,
            "recovery": {"production_backup_policy": "SUSPENDED" if backups_suspended() else "ENABLED",
                         "production_restore_state": "NO_VERIFICADO", "rpo_rto_state": "NO_VERIFICADO"},
            "container_probe_sha256": hashlib.sha256(CONTAINER_PROBE.encode()).hexdigest(), "containers": containers}
