"""Read-only physical disk growth classifier with bounded low-priority scans."""
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import subprocess

DB = Path('/opt/porota-trading/data/paper_v17/observer_v17.db')

def stat(path):
    try:
        s = Path(path).stat()
        return {'physical_bytes': s.st_blocks * 512, 'logical_bytes': s.st_size,
                'inode': s.st_ino, 'device': s.st_dev,
                'mtime_utc': dt.datetime.fromtimestamp(s.st_mtime, dt.timezone.utc).isoformat()}
    except OSError as exc:
        return {'error': type(exc).__name__}

def du(path):
    try:
        p = subprocess.run(['du', '-x', '-s', '-B1', path], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=20, check=False)
        if p.returncode == 0:
            return int(p.stdout.split()[0])
        return {'exit': p.returncode, 'stderr_class': 'ERROR'}
    except subprocess.TimeoutExpired:
        return {'exit': 124}

def category(root, glob):
    rows = []
    try:
        paths = list(Path(root).glob(glob))[:150]
    except OSError:
        paths = []
    for p in paths:
        try:
            if p.is_file() and not p.is_symlink():
                s = p.stat()
                rows.append((s.st_blocks * 512, s.st_mtime))
        except OSError:
            pass
    return {'files': len(rows), 'physical_bytes': sum(x[0] for x in rows),
            'oldest_mtime_utc': dt.datetime.fromtimestamp(min(x[1] for x in rows), dt.timezone.utc).isoformat() if rows else None,
            'newest_mtime_utc': dt.datetime.fromtimestamp(max(x[1] for x in rows), dt.timezone.utc).isoformat() if rows else None}

def main():
    fs = os.statvfs('/')
    report = {'schema': 'porota.rc6.sre.disk-growth.v1',
              'utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'host_mutation': False,
              'root_free_bytes': fs.f_bavail * fs.f_frsize,
              'root_free_inodes': fs.f_ffree,
              'db': {label: stat(str(DB) + suffix) for suffix,label in
                     (('', 'DB'), ('-wal', 'WAL'), ('-shm', 'SHM'))},
              'du': {label: du(path) for label,path in (
                   ('log','/var/log'), ('journal','/var/log/journal'),
                   ('docker','/var/lib/docker'), ('data','/opt/porota-trading/data'),
                   ('general_backups','/opt/porota-backups'),
                   ('builds','/opt/porota-builds'), ('staging','/opt/porota-staging'))},
              'journal_archived': category('/var/log/journal', '**/*@*.journal'),
              'journal_active': category('/var/log/journal', '**/system.journal'),
              'docker_logs': category('/var/lib/docker/containers', '*/*-json.log')}
    c = sqlite3.connect('file:' + str(DB) + '?mode=ro', uri=True, timeout=3)
    try:
        c.execute('PRAGMA query_only=ON')
        report['paper'] = list(c.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone())
    finally:
        c.close()
    print(json.dumps(report, sort_keys=True, separators=(',', ':')))

if __name__ == '__main__':
    main()
