"""Bounded host inventory for #679. Run through an isolated diagnostic Action.

No writes, broker calls, service controls, Docker exec, or WAL checkpoints.
Unknown historical counters are reported as unavailable, never inferred.
"""
import base64
from collections import defaultdict
from datetime import datetime, timezone
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


def main(policy_b64):
    start = time.monotonic()
    policy = json.loads(base64.b64decode(policy_b64, validate=True))
    timers = sorted(Path(path).name for path, spec in policy['units'].items()
                    if spec['role'] == 'ACTIVE_TIMER' and spec['active'] is True)
    if len(timers) != 20:
        raise RuntimeError('TIMER_POLICY_COUNT_MISMATCH')
    current = json.loads((ROOT / 'data/deploy/CURRENT_STATE_V2.json').read_text())
    if current['deploy_sha'] != 'bc661130968f2b85b8b2146f76923ddd26eb7837':
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
