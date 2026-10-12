"""Read-only bounded per-process SQLite descriptor trend."""
from __future__ import annotations
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time

DB = '/opt/porota-trading/data/paper_v17/observer_v17.db'

def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def proc(pid):
    try:
        raw = (Path('/proc') / str(pid) / 'stat').read_text()
        tail = raw[raw.rfind(')') + 2:].split()
        return int(tail[1]), int(tail[19]), tail[0]
    except (OSError, IndexError, ValueError):
        return None

def ancestry(pid, root):
    seen = set()
    for _ in range(24):
        if pid == root:
            return True
        if pid <= 1 or pid in seen:
            return False
        seen.add(pid)
        p = proc(pid)
        if not p:
            return False
        pid = p[0]
    return False

def scan(root, wanted):
    rows = []
    for e in Path('/proc').iterdir():
        if not e.name.isdigit() or not ancestry(int(e.name), root):
            continue
        p = proc(int(e.name))
        if not p:
            continue
        counts = {'DB': 0, 'WAL': 0, 'SHM': 0}
        try:
            fds = list((e / 'fd').iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                st = fd.stat()
                label = wanted.get((st.st_dev, st.st_ino))
                if label:
                    counts[label] += 1
            except OSError:
                pass
        rows.append({'pid': int(e.name), 'start_ticks': p[1], 'state': p[2],
                     'all_fds': len(fds), 'sqlite_fds': counts})
    return sorted(rows, key=lambda x: x['pid'])

def main():
    raw = subprocess.run(['docker', 'inspect', 'porota_production_observer'],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=True)
    x = json.loads(raw.stdout)[0]
    root = x['State']['Pid']
    wanted = {}
    for suffix, label in (('', 'DB'), ('-wal', 'WAL'), ('-shm', 'SHM')):
        try:
            st = os.stat(DB + suffix)
            wanted[(st.st_dev, st.st_ino)] = label
        except OSError:
            pass
    report = {'schema': 'porota.rc6.sre.sqlite-fd-trend.v1',
              'host_mutation': False, 'started_utc': utc(),
              'observer_id': x['Id'], 'observer_restart_count': x['RestartCount'],
              'observer_pid': root, 'observer_start_ticks': (proc(root) or [None, None])[1],
              'samples': []}
    for i in range(5):
        report['samples'].append({'utc': utc(), 'processes': scan(root, wanted)})
        if i < 4:
            time.sleep(30)
    c = sqlite3.connect('file:' + DB + '?mode=ro', uri=True, timeout=3)
    try:
        c.execute('PRAGMA query_only=ON')
        report['paper'] = list(c.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone())
    finally:
        c.close()
    fs = os.statvfs('/')
    report['free_bytes'] = fs.f_bavail * fs.f_frsize
    report['completed_utc'] = utc()
    print(json.dumps(report, sort_keys=True, separators=(',', ':')))

if __name__ == '__main__':
    main()
