"""Bounded, read-only RC6 SQLite lock/process attribution for Actions custody."""
from __future__ import annotations

import collections
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time

DB = Path('/opt/porota-trading/data/paper_v17/observer_v17.db')
CONTAINERS = ('porota_production_observer', 'porota_production_dashboard',
              'porota_critical_approval_rc6')


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def command(args, timeout=12):
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout, check=False)
        return p.returncode, p.stdout.decode('utf-8', 'replace')
    except subprocess.TimeoutExpired:
        return 124, ''


def process(pid):
    try:
        raw = (Path('/proc') / str(pid) / 'stat').read_text()
        # Field 22 starttime detects PID reuse; process name can contain spaces.
        tail = raw[raw.rfind(')') + 2:].split()
        return {'pid': pid, 'ppid': int(tail[1]), 'state': tail[0],
                'start_ticks': int(tail[19])}
    except (OSError, IndexError, ValueError):
        return None


def ancestor(pid, roots):
    seen = set()
    chain = []
    for _ in range(24):
        if pid in roots:
            return roots[pid], chain
        if pid in seen or pid <= 1:
            break
        seen.add(pid)
        p = process(pid)
        if not p:
            break
        chain.append(p)
        pid = p['ppid']
    return 'OTHER_OR_UNPROVEN', chain


def inodes():
    found = {}
    for suffix, label in (('', 'DB'), ('-wal', 'WAL'), ('-shm', 'SHM')):
        try:
            s = os.stat(str(DB) + suffix)
            found[(os.major(s.st_dev), os.minor(s.st_dev), s.st_ino)] = label
        except OSError:
            pass
    return found


def locks(wanted, roots):
    rows = []
    for line in Path('/proc/locks').read_text().splitlines():
        fields = line.split()
        if len(fields) < 7 or fields[1] == '->':
            continue
        try:
            a, b, n = fields[5].split(':')
            label = wanted.get((int(a, 16), int(b, 16), int(n)))
            if not label:
                continue
            pid = int(fields[4])
        except (ValueError, TypeError):
            continue
        owner, chain = ancestor(pid, roots)
        rows.append({'file': label, 'kind': fields[3], 'owner': owner,
                     'pid': pid, 'start_ticks': chain[0]['start_ticks'] if chain else None})
    return rows


def fd_counts(wanted, roots):
    counts = collections.Counter()
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        owner, _ = ancestor(pid, roots)
        if owner == 'OTHER_OR_UNPROVEN':
            continue
        try:
            for fd in (entry / 'fd').iterdir():
                try:
                    s = fd.stat()
                    label = wanted.get((os.major(s.st_dev), os.minor(s.st_dev), s.st_ino))
                    if label:
                        counts[owner + '|' + label] += 1
                except OSError:
                    pass
        except OSError:
            pass
    return dict(counts)


def main():
    start = utc()
    fs = os.statvfs('/')
    report = {'schema': 'porota.rc6.sre.sqlite-owner-attribution.v1',
              'started_utc': start, 'host_mutation': False,
              'free_bytes': fs.f_bavail * fs.f_frsize,
              'free_inodes': fs.f_ffree}
    roots = {}
    containers = {}
    for name in CONTAINERS:
        code, out = command(['docker', 'inspect', name], 20)
        if code:
            containers[name] = {'inspect_exit': code}
            continue
        x = json.loads(out)[0]
        state = x['State']
        pid = state['Pid']
        roots[pid] = name
        containers[name] = {'id': x['Id'], 'image': x['Image'],
                            'pid': pid, 'start_ticks': (process(pid) or {}).get('start_ticks'),
                            'running': state['Running'], 'health': (state.get('Health') or {}).get('Status'),
                            'restart_count': x['RestartCount'], 'init': x['HostConfig'].get('Init')}
    report['containers'] = containers
    c = sqlite3.connect('file:' + str(DB) + '?mode=ro', uri=True, timeout=3)
    try:
        c.execute('PRAGMA query_only=ON')
        report['paper'] = list(c.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone())
    finally:
        c.close()
    wanted = inodes()
    report['db_inodes'] = {label: {'device': f'{a:x}:{b:x}', 'inode': n}
                           for (a, b, n), label in wanted.items()}
    report['fd_counts'] = fd_counts(wanted, roots)
    samples = []
    for _ in range(60):
        samples.append({'utc': utc(), 'locks': locks(wanted, roots)})
        time.sleep(1)
    report['samples'] = samples
    report['counts'] = dict(collections.Counter(
        x['file'] + '|' + x['kind'] + '|' + x['owner']
        for s in samples for x in s['locks']))
    try:
        p = subprocess.run(['docker', 'logs', '--timestamps', '--since', '1h',
                            '--tail', '4000', 'porota_production_observer'],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=25, check=False)
        lines = p.stdout.decode('utf-8', 'replace').splitlines()
        relevant = [l for l in lines if 'database is locked' in l.lower()]
        report['log'] = {'exit': p.returncode, 'lines': len(lines),
                         'lock_lines': len(relevant),
                         'first_lock_timestamp': relevant[0][:35] if relevant else None,
                         'last_lock_timestamp': relevant[-1][:35] if relevant else None,
                         'raw_sha256': hashlib.sha256(p.stdout).hexdigest()}
    except subprocess.TimeoutExpired:
        report['log'] = {'exit': 124}
    report['completed_utc'] = utc()
    print(json.dumps(report, sort_keys=True, separators=(',', ':')))


if __name__ == '__main__':
    main()
