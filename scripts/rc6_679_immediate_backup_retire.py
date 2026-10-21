#!/usr/bin/env python3
"""One-shot guarded retirement of only the audited RC6 backup inventory.

The old observer continues PAPER operation.  Its two internal producers must
have a proven future due time; the host timer is stopped and disabled before any unlink.
This is an operator-authorized interim action pending the certified candidate.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


ROOT = Path('/opt/porota-trading')
DB = ROOT / 'data/paper_v17/observer_v17.db'
STATE = ROOT / 'data/deploy/CURRENT_STATE_V2.json'
RECEIPT = ROOT / 'data/deploy/backup-retire-immediate-20261021.json'
TIMER = 'porota-host-general-backup-rc6.timer'
SERVICE = 'porota-host-general-backup-rc6.service'
PRODUCT_SHA = 'bc661130968f2b85b8b2146f76923ddd26eb7837'
RUNTIME_SHA = 'f525ed707d540ad3b4b75eecf95c503bfd4b230c'
RUNTIME_IMAGE = 'sha256:4aa66170a873c1459cb558ffaa59dc79381579dd4234d4c57adf17540b73d042'
ROOTS = (
    (Path('/opt/porota-backups'), re.compile(r'porota-general-\d{8}T\d{6}Z\.tar\.gz')),
    (ROOT / 'data/paper_v17/artifacts/observer_v17.db/backups',
     re.compile(r'(?:paper/(?:observer_\d{4}-\d{2}-\d{2}\.db\.gz|\.restore_test_\d+\.db(?:-wal|-shm)?)|history/market_history_\d{4}-\d{2}-\d{2}\.db\.gz)')),
    (ROOT / 'data/backups',
     re.compile(r'(?:wave4_closure/observer_v17_pre_wave4_cleanup_\d{8}T\d{6}Z\.db(?:\.gz|-wal|-shm)|preopen-retire/\d{8}T\d{6}Z/porota-preopen\.(?:service|timer)(?:\.(?:cat|journal|status)\.txt)?)')),
)


def command(*args, accepted=(0,)):
    p = subprocess.run(args, text=True, capture_output=True, timeout=15)
    if p.returncode not in accepted:
        raise RuntimeError('COMMAND_FAILED:' + args[0] + ':' + args[1])
    return p.stdout.strip()


def unit_state(name):
    return command('systemctl', 'is-enabled', name, accepted=(0, 1, 3)), command(
        'systemctl', 'is-active', name, accepted=(0, 1, 3))


def general_service_quiescent():
    # The capacity guard exits 75 and leaves this oneshot unit in failed state.
    # It is safe to retire only when systemd proves no active writer remains.
    detail = command('systemctl', 'show', SERVICE, '-p', 'ActiveState', '-p', 'SubState',
                     '-p', 'MainPID', '-p', 'ControlPID', '-p', 'ExecMainStatus')
    fields = dict(line.split('=', 1) for line in detail.splitlines())
    if (fields.get('MainPID') != '0' or fields.get('ControlPID') != '0' or
            (fields.get('ActiveState'), fields.get('SubState'), fields.get('ExecMainStatus'))
            not in {('inactive', 'dead', '0'), ('failed', 'failed', '75')}):
        raise RuntimeError('GENERAL_BACKUP_SERVICE_WRITER_OR_UNKNOWN_STATE')
    return fields


def ppi_units():
    raw = command('systemctl', 'list-unit-files', '--no-legend')
    names = sorted(row.split()[0] for row in raw.splitlines() if row.split()
                   and 'ppi' in row.split()[0].lower() and 'watch' in row.split()[0].lower())
    return [(name, *unit_state(name)) for name in names]


def paper_guard():
    if STATE.is_symlink() or STATE.stat().st_size > 65536:
        raise RuntimeError('CURRENT_STATE_UNSAFE')
    s = json.loads(STATE.read_text())
    if (s.get('candidate_sha') != RUNTIME_SHA or s.get('deploy_sha') != PRODUCT_SHA
            or s.get('image_id') != RUNTIME_IMAGE or s.get('validation_status') != 'VALIDATED_RUNTIME'
            or s.get('mode') != 'PRODUCTION_PAPER' or type(s.get('real_orders_sent')) is not int
            or s['real_orders_sent'] != 0 or s.get('real_order_routes') != 'NOT_CALLED'):
        raise RuntimeError('RUNTIME_PROVENANCE_OR_PAPER_GUARD_RED')
    for name in ('porota_production_observer', 'porota_production_dashboard',
                 'porota_critical_approval_rc6'):
        image, status = command('docker', 'inspect', '-f', '{{.Image}}|{{.State.Status}}', name).split('|')
        if (image, status) != (RUNTIME_IMAGE, 'running'):
            raise RuntimeError('CONTAINER_IDENTITY_OR_STATUS_RED:' + name)
    with sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True, timeout=.2) as c:
        c.execute('PRAGMA query_only=ON')
        c.execute('PRAGMA busy_timeout=200')
        limit = time.monotonic() + 2
        c.set_progress_handler(lambda: int(time.monotonic() > limit), 1000)
        mode, orders, process, session, stamp = c.execute(
            'SELECT mode,real_orders_sent,process_state,session_state,heartbeat_at '
            'FROM observer_state WHERE id=1').fetchone()
        heartbeat = datetime.fromisoformat(stamp)
        if (mode != 'PRODUCTION_PAPER' or type(orders) is not int or orders != 0
                or process != 'RUNNING' or session != 'MARKET_OPEN' or heartbeat.tzinfo is None
                or not 0 <= (datetime.now(timezone.utc) - heartbeat).total_seconds() <= 180):
            raise RuntimeError('OBSERVER_PAPER_HEARTBEAT_RED')
        books = [c.execute('SELECT count(*) FROM ' + table + ' WHERE status=?', (status,)).fetchone()[0]
                 for table, status in (('paper_positions', 'OPEN'), ('paper_cauciones', 'OPEN'),
                                       ('paper_future_positions', 'ACTIVE'))]
        if books != [0, 1, 0]:
            raise RuntimeError('PAPER_BOOK_DRIFT:' + ','.join(map(str, books)))
        now = datetime.now(ZoneInfo('America/Argentina/Buenos_Aires'))
        jobs = {}
        for key in ('DAILY_BACKUP', 'HISTORY_BACKUP'):
            row = c.execute('SELECT last_run_at,state FROM operational_jobs WHERE job_key=?', (key,)).fetchone()
            if not row or not row[0]:
                raise RuntimeError('INTERNAL_BACKUP_DUE_UNVERIFIED:' + key)
            last = datetime.fromisoformat(row[0])
            if last.tzinfo is None or not 0 < (last + __import__('datetime').timedelta(hours=20) - now).total_seconds() <= 20 * 3600:
                raise RuntimeError('INTERNAL_BACKUP_IMMINENT_OR_CLOCK_RED:' + key)
            # Allow at least three hours for the newly certified, governed deploy.
            if (last + __import__('datetime').timedelta(hours=20) - now).total_seconds() < 3 * 3600:
                raise RuntimeError('INTERNAL_BACKUP_WITHIN_THREE_HOURS:' + key)
            jobs[key] = {'last_run_at': row[0], 'state': row[1]}
    return {'book': books, 'jobs': jobs}


def inventory():
    rows = []
    for root, pattern in ROOTS:
        if root.is_symlink() or not root.is_dir() or root.stat().st_dev != ROOT.stat().st_dev:
            raise RuntimeError('BACKUP_ROOT_UNSAFE')
        for directory, dirs, names in os.walk(root, followlinks=False):
            if any((Path(directory) / name).is_symlink() for name in dirs):
                raise RuntimeError('BACKUP_DIRECTORY_ALIAS')
            dirs.sort()
            for name in sorted(names):
                path = Path(directory) / name
                rel = path.relative_to(root).as_posix()
                st = path.lstat()
                if (not pattern.fullmatch(rel) or not stat.S_ISREG(st.st_mode) or
                        st.st_nlink != 1 or st.st_dev != root.stat().st_dev):
                    raise RuntimeError('UNEXPECTED_BACKUP_ENTRY:' + rel)
                rows.append((str(path), st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns))
    rows.sort()
    if len(rows) != 31 or not 4_000_000_000 <= sum(row[3] for row in rows) <= 5_000_000_000:
        raise RuntimeError('BACKUP_INVENTORY_DRIFT')
    return rows


def no_open_files(paths):
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            fds = list((proc / 'fd').iterdir())
        except FileNotFoundError:
            continue
        except PermissionError as exc:
            raise RuntimeError('PROC_FD_UNREADABLE') from exc
        for fd in fds:
            try:
                target = os.readlink(fd)
            except FileNotFoundError:
                continue
            except PermissionError as exc:
                raise RuntimeError('PROC_FD_UNREADABLE') from exc
            if target.removesuffix(' (deleted)') in paths:
                raise RuntimeError('BACKUP_FILE_OPEN')


def main():
    if RECEIPT.exists() or RECEIPT.is_symlink():
        raise RuntimeError('ONE_SHOT_ALREADY_ATTEMPTED')
    import fcntl
    lockpath = ROOT / 'data/deploy/.backup-retire-immediate.lock'
    with lockpath.open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ppi = ppi_units()
        before = paper_guard()
        service_before = general_service_quiescent()
        rows = inventory()
        no_open_files({row[0] for row in rows})
        free_before = shutil.disk_usage(ROOT).free
        timer_before = unit_state(TIMER)
        command('systemctl', 'mask', '--now', TIMER)
        if unit_state(TIMER) != ('masked', 'inactive'):
            raise RuntimeError('HOST_BACKUP_TIMER_NOT_MASKED')
        if (paper_guard()['jobs'] != before['jobs'] or inventory() != rows or
                general_service_quiescent() != service_before):
            raise RuntimeError('PRE_UNLINK_STATE_CHANGED')
        no_open_files({row[0] for row in rows})
        digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        intent = RECEIPT.with_suffix('.intent.json')
        fd = os.open(intent, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump({'schema':'RC6_679_IMMEDIATE_RETIRE_V1','status':'INTENT',
                       'count':31,'manifest_sha256':digest,'timer_before':timer_before}, stream)
            stream.flush(); os.fsync(stream.fileno())
        for name, dev, inode, size, mtime in rows:
            path = Path(name)
            st = path.lstat()
            if (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_nlink) != (
                    dev, inode, size, mtime, 1):
                raise RuntimeError('BACKUP_FILE_DRIFT:' + path.name)
            path.unlink()
        if any(any(names) for root, _ in ROOTS for _, _, names in os.walk(root)):
            raise RuntimeError('BACKUP_RETIRE_INCOMPLETE')
        after = paper_guard()
        if after['jobs'] != before['jobs'] or ppi_units() != ppi:
            raise RuntimeError('POST_UNLINK_RUNTIME_OR_PPI_CHANGED')
        receipt = {'schema':'RC6_679_IMMEDIATE_RETIRE_V1','status':'DONE',
                   'count':31,'logical_bytes':sum(r[3] for r in rows),
                   'manifest_sha256':digest,'free_before':free_before,
                   'free_after':shutil.disk_usage(ROOT).free,
                   'timer_after':unit_state(TIMER), 'backup_jobs':after['jobs'],
                   'paper_book':after['book'], 'ppi_watch_unchanged':True,
                   'general_service_before':service_before,
                   'internal_producers_suspended_after_candidate_deploy':False}
        fd = os.open(RECEIPT, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(receipt, stream, sort_keys=True)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        print('RC6_679_IMMEDIATE_BACKUP_RETIRE=' + json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    main()
