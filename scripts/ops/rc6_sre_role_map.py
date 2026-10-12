"""Read-only process role map and current PAPER/critical safety snapshot."""
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import subprocess

DB = '/opt/porota-trading/data/paper_v17/observer_v17.db'
NAMES = ('porota_production_observer', 'porota_production_dashboard',
         'porota_critical_approval_rc6')
ROLES = {'--exit-reader': 'EXIT_READER',
         '--notification-worker': 'NOTIFICATIONS',
         '--candle-worker': 'CANDLES',
         '--intraday-scalping-worker': 'SCALPING',
         '--caucion-cash-sweep-worker': 'CAUCION'}

def proc(pid):
    try:
        root = Path('/proc') / str(pid)
        raw = (root / 'stat').read_text()
        f = raw[raw.rfind(')') + 2:].split()
        return {'pid': pid, 'ppid': int(f[1]), 'start_ticks': int(f[19]),
                'cmd': (root / 'cmdline').read_bytes().split(b'\0')}
    except (OSError, IndexError, ValueError):
        return None

def owned(pid, root):
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
        pid = p['ppid']
    return False

def role(cmd):
    arguments = {a.decode('utf-8', 'replace') for a in cmd}
    for arg, label in ROLES.items():
        if arg in arguments:
            return label
    if any(a.endswith(b'bf_production_paper_observer.py') for a in cmd):
        return 'SCANNER'
    return 'PARENT_OR_UNKNOWN'

def main():
    report = {'schema': 'porota.rc6.sre.role-map.v1',
              'utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'host_mutation': False, 'containers': {}}
    for name in NAMES:
        x = json.loads(subprocess.run(['docker', 'inspect', name],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=20, check=True).stdout)[0]
        s = x['State']
        report['containers'][name] = {'id': x['Id'], 'pid': s['Pid'],
              'health': (s.get('Health') or {}).get('Status'),
              'running': s['Running'], 'restart_count': x['RestartCount'],
              'init': x['HostConfig'].get('Init'), 'oom_killed': s['OOMKilled']}
    root = report['containers'][NAMES[0]]['pid']
    wanted = {}
    for suffix, label in (('', 'DB'), ('-wal', 'WAL'), ('-shm', 'SHM')):
        try:
            st = os.stat(DB + suffix)
            wanted[(st.st_dev, st.st_ino)] = label
        except OSError:
            pass
    rows = []
    for e in Path('/proc').iterdir():
        if not e.name.isdigit() or not owned(int(e.name), root):
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
        rows.append({'pid': p['pid'], 'ppid': p['ppid'],
                     'start_ticks': p['start_ticks'], 'role': role(p['cmd']),
                     'sqlite_fds': counts})
    report['roles'] = sorted(rows, key=lambda x: x['pid'])
    c = sqlite3.connect('file:' + DB + '?mode=ro', uri=True, timeout=3)
    try:
        c.execute('PRAGMA query_only=ON')
        report['paper'] = list(c.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone())
    finally:
        c.close()
    fs = os.statvfs('/')
    report['free_bytes'] = fs.f_bavail * fs.f_frsize
    report['free_inodes'] = fs.f_ffree
    print(json.dumps(report, sort_keys=True, separators=(',', ':')))

if __name__ == '__main__':
    main()
