"""Bounded host inventory for #679. Run through an isolated diagnostic Action.

No writes, broker calls, service controls, Docker exec, or WAL checkpoints.
Unknown historical counters are reported as unavailable, never inferred.
"""
import base64
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time


ROOT = Path('/opt/porota-trading')
CONTAINERS = ('porota_production_observer', 'porota_production_dashboard',
              'porota_critical_approval_rc6')


def command(*args, timeout=3):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           check=False)
        return p.stdout.strip()[:65536] if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def read_number(path):
    try:
        return int(Path(path).read_text().strip())
    except (OSError, ValueError):
        return None


def process_sample(pid):
    base = Path('/proc') / str(pid)
    try:
        stat = (base / 'stat').read_text().rsplit(')', 1)[1].split()
        io = dict(line.split(': ', 1) for line in (base / 'io').read_text().splitlines()
                  if ': ' in line)
        status = dict(line.split(':', 1) for line in (base / 'status').read_text().splitlines()
                      if ':' in line)
        # Inspect an allowlist of module names only. Never publish raw argv,
        # which may contain credentials or private runtime data.
        args = (base / 'cmdline').read_bytes().lower()[:4096]
        roles = ((b'--dynamic-shadow-worker', 'dynamic_shadow'),
                 (b'--candle-worker', 'candle_worker'),
                 (b'--exit-reader', 'exit_reader'),
                 (b'--notification-worker', 'notifications'),
                 (b'--performance-worker', 'performance'),
                 (b'--intraday-scalping-worker', 'intraday_scalping'),
                 (b'--caucion-cash-sweep-worker', 'caucion_cash_sweep'),
                 (b'bf_production_paper_observer.py', 'scanner'),
                 (b'bv_paper_runtime.py', 'exit_clock'))
        role = next((label for marker, label in roles if marker in args), 'UNCLASSIFIED')
        return {'pid': pid, 'cpu_ticks': int(stat[11]) + int(stat[12]),
                'read_bytes': int(io['read_bytes']), 'write_bytes': int(io['write_bytes']),
                'rss_kib': int(status['VmRSS'].split()[0]),
                'major_faults': int(stat[9]), 'name': status.get('Name', '').strip()[:48],
                'parent_pid': int(status['PPid'].strip()), 'allowlisted_role': role}
    except (OSError, ValueError, KeyError, IndexError):
        return None


def descendants(pid, limit=48):
    found, queue = set(), [pid]
    while queue and len(found) < limit:
        current = queue.pop(0)
        if current in found or current <= 0:
            continue
        found.add(current)
        try:
            raw = (Path('/proc') / str(current) / 'task' / str(current) / 'children').read_text()
            queue.extend(int(value) for value in raw.split()[:limit])
        except (OSError, ValueError):
            pass
    return sorted(found)


def sqlite_status(path):
    p = Path(path)
    result = {'path_role': p.name, 'files': {}}
    for suffix in ('', '-wal', '-shm'):
        file = Path(str(p) + suffix)
        try:
            result['files'][suffix or 'db'] = {'bytes': file.stat().st_size,
                                                  'mtime_ns': file.stat().st_mtime_ns}
        except OSError:
            result['files'][suffix or 'db'] = None
    if not p.is_file():
        return result
    try:
        with sqlite3.connect(p.as_uri() + '?mode=ro', uri=True, timeout=.2) as c:
            c.execute('PRAGMA query_only=ON')
            c.execute('PRAGMA busy_timeout=200')
            end = time.monotonic() + 2
            c.set_progress_handler(lambda: int(time.monotonic() > end), 1000)
            result['pages'] = {name: c.execute('PRAGMA ' + name).fetchone()[0]
                               for name in ('page_count', 'page_size', 'freelist_count')}
            if p.name == 'market_history.db':
                names = {row[0] for row in c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' LIMIT 1000")}
                result['history_schema'] = {}
                for table in ('history_canonical_v2', 'production_history',
                              'history_local_daily_v1', 'local_eod_cursor_v1'):
                    if table in names:
                        result['history_schema'][table] = {
                            'columns': [row[1] for row in c.execute('PRAGMA table_info("' + table + '")')],
                            'max_rowid': c.execute('SELECT COALESCE(MAX(rowid),0) FROM "' + table + '"').fetchone()[0]}
                    else:
                        result['history_schema'][table] = {'present': False}
            if p.name == 'observer_v17.db':
                names = {row[0] for row in c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' LIMIT 1000")}
                allowed = ('financial_instrument_catalog', 'market_snapshots',
                           'ppi_intraday_points', 'ppi_intraday_contract_state',
                           'paper_positions', 'paper_events',
                           'decision_evidence_snapshots', 'paper_fills')
                tails = {}
                for name in allowed:
                    if name in names:
                        # Indexed rowid tail, no row payload or table scan.
                        tails[name] = c.execute(
                            f'SELECT COALESCE(MAX(rowid),0) FROM "{name}"').fetchone()[0]
                result['allowlisted_rowid_tails'] = tails
                # Measure the exact two bounded read variants; do not export
                # payloads, alter the database or allow a long SQLite scan.
                specs = (('financial_instrument_catalog', 20000),
                         ('paper_positions', 4096), ('paper_future_positions', 4096),
                         ('paper_family_lifecycle', 4096), ('paper_daily_risk', 32),
                         ('paper_equity_by_currency', 500),
                         ('ppi_intraday_contract_state', 20000),
                         ('ppi_intraday_points', 5000), ('market_snapshots', 5000),
                         ('paper_events', 3000), ('scalping_candidates', 2000),
                         ('decision_evidence_snapshots', 3000), ('paper_fills', 3000),
                         ('paper_family_lifecycle_events', 3000))
                for deep, budget in ((False, .75), (True, 2.0)):
                    started = time.monotonic()
                    deadline = started + budget
                    c.set_progress_handler(lambda: int(time.monotonic() > deadline), 200)
                    digest = hashlib.sha256()
                    measured = {'tables': {}, 'budget_ms': int(budget * 1000)}
                    try:
                        for name, limit in specs:
                            if time.monotonic() > deadline:
                                break
                            if name not in names:
                                measured['tables'][name] = {'rows': 0, 'absent': True}
                                continue
                            if name == 'financial_instrument_catalog' and not deep:
                                row = c.execute('''SELECT COUNT(*),COALESCE(MAX(rowid),0),
                                  MAX(last_seen_at) FROM financial_instrument_catalog''').fetchone()
                                digest.update(repr(tuple(row)).encode())
                                measured['tables'][name] = {'scalar': True}
                                continue
                            order = 'julianday(event_at)' if name == 'ppi_intraday_points' else 'rowid'
                            if name in ('paper_daily_risk', 'paper_equity_by_currency',
                                        'ppi_intraday_points', 'market_snapshots', 'paper_events',
                                        'scalping_candidates', 'decision_evidence_snapshots',
                                        'paper_fills', 'paper_family_lifecycle_events'):
                                row = c.execute(f'SELECT COALESCE(MAX(rowid),0) FROM "{name}"').fetchone()
                                digest.update(repr(tuple(row)).encode())
                            projection = ('rowid,captured_at,payload_sha256' if name ==
                                          'decision_evidence_snapshots' else '*')
                            count = size = 0
                            for row in c.execute(f'SELECT {projection} FROM "{name}" ORDER BY {order} DESC LIMIT ?',
                                                 (limit + 1,)):
                                if count >= limit:
                                    break
                                raw = repr(tuple(row)).encode('utf-8', 'surrogatepass')
                                if len(raw) > 65536 or time.monotonic() > deadline:
                                    raise ValueError('PROBE_BUDGET')
                                digest.update(raw)
                                count += 1
                                size += len(raw)
                            measured['tables'][name] = {'rows': count, 'represented_bytes': size}
                    except (sqlite3.Error, ValueError) as exc:
                        measured['error_class'] = type(exc).__name__
                    measured['elapsed_ms'] = round((time.monotonic() - started) * 1000, 3)
                    measured['status'] = ('WITHIN_BUDGET' if len(measured['tables']) == len(specs)
                                          and measured['elapsed_ms'] <= budget * 1000 and
                                          'error_class' not in measured else 'OVER_BUDGET')
                    result['closed_probe_cost_' + ('deep' if deep else 'fast')] = measured
    except (OSError, sqlite3.Error) as exc:
        result['query_error'] = type(exc).__name__
    return result


def bounded_directory(path, max_files=5000):
    root = Path(path)
    counts, sizes = defaultdict(int), defaultdict(int)
    seen = 0
    if not root.is_dir() or root.is_symlink():
        return {'present': False}
    for current, folders, files in os.walk(root, followlinks=False):
        relative = Path(current).relative_to(root)
        if len(relative.parts) >= 2:
            folders[:] = []
        folders[:] = sorted(d for d in folders if not (Path(current) / d).is_symlink())
        for name in sorted(files):
            seen += 1
            if seen > max_files:
                return {'present': True, 'truncated': True,
                        'file_count_seen': seen, 'bytes_by_role': dict(sizes)}
            file = Path(current) / name
            try:
                if file.is_symlink() or not file.is_file():
                    continue
                role = relative.parts[0] if relative.parts else name.split('-', 1)[0]
                counts[role] += 1
                sizes[role] += file.stat().st_size
            except OSError:
                pass
    return {'present': True, 'truncated': False, 'file_count_seen': seen,
            'files_by_role': dict(counts), 'bytes_by_role': dict(sizes)}




def hash_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def backup_manifest():
    """Exact bounded candidates, with inode identity. Never traverse symlinks."""
    roots = (Path('/opt/porota-backups'),
             ROOT / 'data/paper_v17/artifacts/observer_v17.db/backups',
             ROOT / 'data/backups')
    result = {}
    for root in roots:
        entries = []
        if root.is_dir() and not root.is_symlink():
            for current, folders, names in os.walk(root, followlinks=False):
                folders[:] = sorted(d for d in folders if not (Path(current)/d).is_symlink())
                for name in sorted(names):
                    path = Path(current) / name
                    if len(entries) >= 128:
                        raise RuntimeError('BACKUP_MANIFEST_BOUNDS_EXCEEDED')
                    st = path.lstat()
                    entries.append({'path': str(path), 'bytes': st.st_size,
                                    'inode': st.st_ino, 'device': st.st_dev,
                                    'mtime_ns': st.st_mtime_ns, 'links': st.st_nlink,
                                    'regular': path.is_file() and not path.is_symlink(),
                                    'sha256': (hash_file(path)
                                               if name.startswith('market_history_') and name.endswith('.db.gz')
                                               else None)})
        result[str(root)] = entries
    return result


def last_eod_outcomes():
    raw = command('journalctl', '--no-pager', '-o', 'cat',
                  '-u', 'porota-local-eod-history-rc6.service',
                  '--since', '2026-10-20 00:00:00 UTC', '-n', '20', timeout=5)
    if raw is None:
        return {'status': 'NO_VERIFICADO'}
    outcomes = []
    for line in raw.splitlines():
        try:
            value = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and value.get('schema') == 'rc6.local-eod.v1':
            outcomes.append({key: value.get(key) for key in ('as_of', 'state', 'reason')})
    return {'status': 'BOUNDED_LAST_20_LINES', 'outcomes': outcomes[-10:]}


def last_backup_admission():
    raw = command('journalctl', '--no-pager', '-o', 'cat',
                  '-u', 'porota-host-general-backup-rc6.service',
                  '--since', '2026-10-21 00:00:00 UTC', '-n', '20', timeout=5)
    if raw is None:
        return {'status': 'NO_VERIFICADO'}
    for line in reversed(raw.splitlines()):
        try:
            value = json.loads(line)
            admission = value.get('backup_capacity')
        except (ValueError, TypeError, AttributeError):
            continue
        if isinstance(admission, dict):
            allowed = ('status', 'source_upper_bound_bytes', 'staging_required_bytes',
                       'destination_required_bytes', 'staging_free_bytes',
                       'destination_free_bytes', 'same_device')
            return {'status': 'OBSERVED_JOURNAL', 'admission':
                    {key: admission.get(key) for key in allowed}}
    return {'status': 'NO_ADMISSION_LINE_IN_LAST_20'}



def backup_unit_inventory():
    """Find even legacy RC4 backup timers without touching service state."""
    raw = command('systemctl', 'list-unit-files', '--no-legend', '--no-pager', timeout=5)
    if raw is None:
        return {'status': 'NO_VERIFICADO'}
    names = sorted(set(line.split()[0] for line in raw.splitlines() if line.split()
                       and line.split()[0].startswith('porota-')
                       and 'backup' in line.split()[0].lower()
                       and line.split()[0].endswith(('.timer', '.service'))))
    if len(names) > 32:
        raise RuntimeError('BACKUP_UNIT_INVENTORY_BOUNDS')
    units = {}
    for name in names:
        state = command('systemctl', 'show', name, '-p',
                        'LoadState,ActiveState,UnitFileState,FragmentPath', timeout=2)
        units[name] = (dict(line.split('=', 1) for line in state.splitlines()
                            if '=' in line) if state is not None else {'status':'UNAVAILABLE'})
    return {'status': 'READ_ONLY', 'units': units}



def growth_roots():
    """Bounded metadata-only inventory; totals are partial when truncated."""
    roots = (ROOT / 'data/deploy', ROOT / 'data/paper_v17/artifacts',
             ROOT / 'data/market_history', Path('/var/log/journal'),
             Path('/var/lib/docker/containers'), ROOT / 'data/market',
             ROOT / 'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow')
    output = {}
    for root in roots:
        seen = total = 0
        truncated = False
        if root.is_symlink() or not root.is_dir():
            output[str(root)] = {'status': 'ABSENT_OR_SYMLINK'}
            continue
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not (Path(current) / d).is_symlink())
            for name in sorted(files):
                seen += 1
                if seen > 512:
                    truncated = True
                    break
                path = Path(current) / name
                try:
                    if not path.is_symlink() and path.is_file():
                        total += path.stat().st_size
                except OSError:
                    pass
            if truncated:
                break
        output[str(root)] = {'file_count_seen': seen, 'logical_bytes_observed': total,
                             'truncated': truncated}
    return output



def shadow_largest_files():
    root = ROOT / 'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow'
    if not root.is_dir() or root.is_symlink():
        return {'status':'ABSENT_OR_SYMLINK'}
    files = []
    seen = 0
    for current, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not (Path(current) / d).is_symlink())
        for name in names:
            seen += 1
            if seen > 512:
                return {'status':'TRUNCATED', 'file_count_seen': seen}
            path = Path(current) / name
            if path.is_symlink() or not path.is_file():
                continue
            st = path.stat()
            files.append({'role': path.relative_to(root).as_posix(),
                          'bytes': st.st_size, 'mtime_ns': st.st_mtime_ns})
    return {'status':'READ_ONLY', 'file_count_seen':seen,
            'largest':sorted(files, key=lambda x:x['bytes'], reverse=True)[:16]}



def cold_audit_queue_metadata():
    root = ROOT / 'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow/cold-audit'
    path = root / 'queue.json'
    try:
        if root.is_symlink() or path.is_symlink() or path.stat().st_size > 8192:
            return {'status':'UNTRUSTED'}
        value = json.loads(path.read_text())
        payload = value.get('payload', {})
        if type(payload) is not dict:
            return {'status':'UNTRUSTED'}
        return {'status':'READ_ONLY', 'state':payload.get('state'),
                'snapshot_id':payload.get('snapshot_id'),
                'manifest_sha256':payload.get('manifest_sha256')}
    except (OSError, ValueError, TypeError):
        return {'status':'NO_VERIFICADO'}



def postclose_readiness_metadata():
    path = ROOT / 'data/paper_v17/observer_v17.db'
    try:
        with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=.2) as c:
            c.execute('PRAGMA query_only=ON')
            c.execute('PRAGMA busy_timeout=200')
            end = time.monotonic()+2
            c.set_progress_handler(lambda: int(time.monotonic()>end), 1000)
            state = c.execute('SELECT mode,real_orders_sent,process_state,session_state,heartbeat_at '
                              'FROM observer_state WHERE id=1').fetchone()
            if state is None or state[0]!='PRODUCTION_PAPER' or state[1]!=0:
                return {'status':'PAPER_GUARD_RED'}
            counts = {name:c.execute('SELECT COUNT(*) FROM '+name+' WHERE status=?',
                      (status,)).fetchone()[0] for name,status in
                      (('paper_positions','OPEN'),('paper_cauciones','OPEN'),
                       ('paper_future_positions','ACTIVE'))}
            return {'status':'READ_ONLY', 'process_state':state[2],
                    'session_state':state[3], 'heartbeat_at':state[4],
                    'open_counts':counts}
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return {'status':'NO_VERIFICADO'}


def scheduler_export_exec_evidence():
    """Allowlisted metadata for systemd 203/EXEC; no journal text or argv leaves the host."""
    path = ROOT / 'scripts/porota_export_scheduler_state_rc6.sh'
    result = {'expected_path_role': 'scripts/porota_export_scheduler_state_rc6.sh'}
    try:
        st = path.lstat()
        result.update(present=True, regular=path.is_file() and not path.is_symlink(),
                      mode=oct(st.st_mode & 0o777), uid=st.st_uid, gid=st.st_gid,
                      bytes=st.st_size,
                      mount_noexec=bool(os.statvfs(path).f_flag & getattr(os, 'ST_NOEXEC', 8)))
        with path.open('rb') as stream:
            result['shebang_expected'] = stream.readline(128).strip() == b'#!/usr/bin/env bash'
    except FileNotFoundError:
        result['present'] = False
    except OSError:
        result.setdefault('present', 'NO_VERIFICADO')
        result['shebang_expected'] = 'NO_VERIFICADO'
    raw = command('systemctl', 'show', 'porota-scheduler-export-rc6.service',
                  '-p', 'Result', '-p', 'ExecMainStatus', '-p', 'User',
                  '-p', 'FragmentPath', '-p', 'ExecStart', timeout=3)
    props = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line) if raw else {}
    result['unit'] = {
        'result': props.get('Result'), 'exit_status': props.get('ExecMainStatus'),
        'expected_user': props.get('User') == 'porotaadmin',
        'expected_exec_path': str(path) in props.get('ExecStart', ''),
        'fragment_in_systemd': props.get('FragmentPath') ==
                                  '/etc/systemd/system/porota-scheduler-export-rc6.service'}
    raw = command('journalctl', '--no-pager', '-o', 'cat', '-u',
                  'porota-scheduler-export-rc6.service',
                  '--since', '2026-10-21 19:00:00 UTC', '-n', '20', timeout=4)
    result['journal_codes'] = ({
        'failed_step_exec': 'Failed at step EXEC' in raw,
        'file_missing': 'No such file or directory' in raw,
        'permission_denied': 'Permission denied' in raw,
        'exec_format': 'Exec format error' in raw,
    } if raw is not None else {'status': 'NO_VERIFICADO'})
    return result


def main(policy_b64):
    start = time.monotonic()
    policy = json.loads(base64.b64decode(policy_b64, validate=True))
    timers = sorted(Path(path).name for path, spec in policy['units'].items()
                    if spec['role'] == 'ACTIVE_TIMER' and spec['active'] is True)
    if len(timers) != 19:
        raise RuntimeError('TIMER_POLICY_COUNT_MISMATCH')
    current = json.loads((ROOT / 'data/deploy/CURRENT_STATE_V2.json').read_text())
    if current['deploy_sha'] != 'f0c393ca2b5c1560bf57cf776f1e9fcfcbca582f':
        raise RuntimeError('DEPLOY_SHA_CHANGED')
    if current['mode'] != 'PRODUCTION_PAPER' or type(current['real_orders_sent']) is not int \
            or current['real_orders_sent'] != 0 or current['real_order_routes'] != 'NOT_CALLED':
        raise RuntimeError('PAPER_SAFETY_GATE_FAILED')
    containers = {}
    pids = set()
    for name in CONTAINERS:
        raw = command('docker', 'inspect', name, timeout=5)
        if raw is None:
            raise RuntimeError('CONTAINER_INSPECT_UNAVAILABLE')
        row = json.loads(raw)[0]
        if row['Image'] != current['image_id'] or not row['State']['Running']:
            raise RuntimeError('CONTAINER_IDENTITY_CHANGED')
        pid = int(row['State']['Pid'])
        containers[name] = {'init_pid': pid, 'image_id': row['Image'],
                            'restart_count': row['RestartCount'],
                            'oom': row['State']['OOMKilled']}
        pids.update(descendants(pid))
    first = {pid: process_sample(pid) for pid in sorted(pids)}
    time.sleep(5)
    second = {pid: process_sample(pid) for pid in sorted(pids)}
    processes = []
    for pid in sorted(pids):
        a, b = first[pid], second[pid]
        if a is None or b is None:
            continue
        processes.append({**b, 'cpu_ticks_delta_5s': b['cpu_ticks']-a['cpu_ticks'],
                          'read_bytes_delta_5s': b['read_bytes']-a['read_bytes'],
                          'write_bytes_delta_5s': b['write_bytes']-a['write_bytes'],
                          'major_faults_delta_5s': b['major_faults']-a['major_faults']})
    fields = ('ActiveState', 'SubState', 'UnitFileState', 'LastTriggerUSec',
              'NextElapseUSecRealtime', 'Result', 'ExecMainStatus')
    service_fields = ('ActiveState', 'SubState', 'Result', 'ExecMainStatus',
                      'ExecMainStartTimestamp', 'ExecMainExitTimestamp', 'NRestarts')
    units, services = {}, {}
    for timer in timers:
        raw = command('systemctl', 'show', timer, '-p', ','.join(fields), timeout=2)
        units[timer] = (dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
                        if raw is not None else {'status': 'UNAVAILABLE'})
        service = timer.removesuffix('.timer') + '.service'
        raw = command('systemctl', 'show', service, '-p', ','.join(service_fields), timeout=2)
        services[service] = (dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
                             if raw is not None else {'status': 'UNAVAILABLE'})
    db = ROOT / 'data/paper_v17/observer_v17.db'
    shadow = ROOT / 'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow'
    disk = os.statvfs('/')
    result = {'schema': 'RC6_679_READ_ONLY_RESOURCE_BASELINE_V1',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'source_sha': current['deploy_sha'], 'candidate_sha': current['candidate_sha'],
              'paper': {'mode': current['mode'], 'real_orders_sent': 0,
                        'real_routes': current['real_order_routes']},
              'containers': containers, 'process_delta_window_seconds': 5,
              'processes': processes, 'tracked_process_count': len(pids),
              'timers': units, 'services': services,
              'local_eod_journal': last_eod_outcomes(),
              'backup_admission_journal': last_backup_admission(),
              'backup_manifest': backup_manifest(),
              'backup_units': backup_unit_inventory(),
              'growth_roots': growth_roots(),
              'shadow_largest': shadow_largest_files(),
              'cold_audit_queue': cold_audit_queue_metadata(),
              'postclose_readiness': postclose_readiness_metadata(),
              'scheduler_export_exec_evidence': scheduler_export_exec_evidence(),
              'observer_sqlite': sqlite_status(db),
              'history_sqlite': sqlite_status(ROOT / 'data/market_history.db'),
              'shadow_files': bounded_directory(shadow),
              'disk_free_bytes': disk.f_bavail * disk.f_frsize,
              'disk_free_inodes': disk.f_ffree,
              'elapsed_seconds': round(time.monotonic()-start, 3),
              'historical_per_pid_14_20_oct': 'NO_VERIFICADO',
              'actual_duplicate_execution': 'NO_VERIFICADO',
              'ppi_watch': 'NOT_ACCESSED'}
    print('RC6_679_RESOURCE_BASELINE=' + json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('POLICY_ARGUMENT_REQUIRED')
    main(sys.argv[1])
