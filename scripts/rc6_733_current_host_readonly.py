#!/usr/bin/env python3
"""One bounded, passive snapshot for #733. Never acquire a lock or mutate RC6."""
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import os
import pwd
import re
import sqlite3
import stat
import subprocess
import sys
import time


DB = Path('/opt/porota-trading/data/paper_v17/observer_v17.db')
LOCK_DIR = Path('/run/lock')
OWNED = ('porota_production_observer', 'porota_production_dashboard')


def exact_file_status(path):
    try:
        meta = os.lstat(path)
    except FileNotFoundError:
        return 'ABSENT'
    if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0 or
            stat.S_IMODE(meta.st_mode) != 0o600 or meta.st_nlink != 1):
        return 'UNSAFE'
    return 'ROOT_0600_SINGLE_LINK'


def bounded_db_snapshot():
    start = time.monotonic()
    with sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True, timeout=.2) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA busy_timeout=200')
        db.set_progress_handler(lambda: int(time.monotonic() - start > 1.5), 1000)
        state = db.execute(
            'SELECT mode,real_orders_sent,process_state,session_state,heartbeat_at '
            'FROM observer_state WHERE id=1').fetchone()
        if state is None:
            raise ValueError('OBSERVER_STATE_MISSING')
        try:
            heartbeat = datetime.fromisoformat(state['heartbeat_at'])
            age = (datetime.now(timezone.utc) - heartbeat).total_seconds()
            heartbeat_fresh = bool(heartbeat.tzinfo and 0 <= age <= 180)
        except (TypeError, ValueError):
            heartbeat_fresh = False
        limits = {
            'spot_open': "SELECT 1 FROM paper_positions WHERE status='OPEN' LIMIT 2",
            'futures_active': "SELECT 1 FROM paper_future_positions WHERE status='ACTIVE' LIMIT 2",
        }
        exposures = {key: len(db.execute(query).fetchall()) for key, query in limits.items()}
        cauciones = db.execute(
            "SELECT opened_at,maturity_at,source,currency,settled_at "
            "FROM paper_cauciones WHERE status='OPEN' LIMIT 2").fetchall()
        exposures['caucion_open'] = len(cauciones)
        exception_eligible = False
        if len(cauciones) == 1:
            p = dict(cauciones[0])
            try:
                opened = datetime.fromisoformat(p['opened_at'])
                maturity = datetime.fromisoformat(p['maturity_at'])
                local = opened.astimezone(ZoneInfo('America/Argentina/Buenos_Aires'))
                exception_eligible = bool(
                    opened.tzinfo and maturity.tzinfo and
                    opened <= datetime.now(timezone.utc) < maturity and
                    p['source'] == 'PRODUCTION_PAPER' and p['currency'] == 'ARS' and
                    p['settled_at'] is None and
                    (local.date().isoformat(), local.hour, local.minute) ==
                    ('2026-10-22', 16, 30) and
                    p['maturity_at'] == '2026-10-23T17:00:00-03:00')
            except (TypeError, ValueError):
                pass
        return {
            'mode': state['mode'], 'real_orders_sent': state['real_orders_sent'],
            'process_state': state['process_state'],
            'session_state': state['session_state'],
            'heartbeat_at': state['heartbeat_at'],
            'heartbeat_fresh_180s': heartbeat_fresh,
            'exposures_at_most_two_each': exposures,
            'historical_caucion_exception_eligible_now': exception_eligible,
        }


def docker_snapshot():
    result = subprocess.run(
        ['docker', 'inspect', '--format',
         '{{json .State}}|{{.Image}}', *OWNED],
        capture_output=True, text=True, timeout=5, check=True)
    lines = result.stdout.splitlines()
    if len(lines) != len(OWNED):
        raise ValueError('DOCKER_INSPECT_CARDINALITY')
    return {
        name: {'status': json.loads(line.split('|', 1)[0])['Status'],
               'image': line.split('|', 1)[1]}
        for name, line in zip(OWNED, lines)
    }


def main():
    if (len(sys.argv) != 5 or os.geteuid() != 0 or
            not re.fullmatch(r'[0-9a-f]{40}', sys.argv[2]) or
            not re.fullmatch(r'[0-9a-f]{40}', sys.argv[3])):
        raise ValueError('PREFLIGHT_IDENTITY_OR_PRIVILEGE_INVALID')
    remote = pwd.getpwnam(sys.argv[1])
    if remote.pw_uid == 0:
        raise ValueError('PREFLIGHT_REMOTE_USER_ROOT')
    required = int(sys.argv[4])
    if required != 4041528229:
        raise ValueError('PREFLIGHT_FROZEN_BYTES_CHANGED')
    sys.path.insert(0, '/opt/porota-trading')
    from scripts.rc6_679_backup_suspension import inventory, systemctl_state
    backups = inventory()
    backup_count = len(backups)
    backup_bytes = sum(row['bytes'] for row in backups)
    backup_timer = {'enabled': systemctl_state('is-enabled'),
                    'active': systemctl_state('is-active')}
    directory = os.lstat(LOCK_DIR)
    parent_safe = bool(stat.S_ISDIR(directory.st_mode) and directory.st_uid == 0 and
                       (not directory.st_mode & stat.S_IWOTH or directory.st_mode & stat.S_ISVTX))
    lock = exact_file_status(LOCK_DIR / 'porota-rc6-deploy-v2.lock')
    barrier = exact_file_status(LOCK_DIR / 'porota-rc6-deploy-v2-attempt.json')
    barrier_state = 'ABSENT'
    if barrier == 'ROOT_0600_SINGLE_LINK':
        fd = os.open(LOCK_DIR / 'porota-rc6-deploy-v2-attempt.json',
                     os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            if os.fstat(fd).st_size > 1024:
                raise ValueError('BARRIER_TOO_LARGE')
            barrier_state = json.loads(os.read(fd, 1025)).get('status', 'INVALID')
        finally:
            os.close(fd)
    running = subprocess.run(
        ['docker', 'ps', '--format', '{{.Names}}', '--filter',
         'name=porota-deploy-contract-'], capture_output=True, text=True,
        timeout=5, check=True)
    writers = sum(bool(re.fullmatch(r'porota-deploy-contract-[0-9a-f]{40}', n))
                  for n in running.stdout.splitlines())
    fs = os.statvfs('/')
    free = fs.f_bavail * fs.f_frsize
    inode_free = 100 * fs.f_ffree / fs.f_files if fs.f_files else 0
    db = bounded_db_snapshot()
    containers = docker_snapshot()
    mode_hint = ('CLOSED_FLAT' if
                 db['process_state'] == 'WAITING_MARKET' and
                 db['session_state'] in ('CLOSED', 'MARKET_CLOSED') and
                 all(v == 0 for v in db['exposures_at_most_two_each'].values())
                 else 'NEEDS_EXACT_DEPLOY_CLASSIFIER')
    report = {
        'schema': 'RC6_733_CURRENT_HOST_READONLY_V1',
        'sampled_at_utc': datetime.now(timezone.utc).isoformat(),
        'candidate_sha': sys.argv[2], 'candidate_tree': sys.argv[3],
        'sudo_euid': os.geteuid(), 'remote_user_unprivileged': True,
        'lock_parent_safe': parent_safe, 'lock_status': lock,
        'barrier_file_status': barrier, 'barrier_state': barrier_state,
        'contract_writers_running': writers,
        'root_available_bytes': free, 'required_pretransfer_bytes': required,
        'inode_free_percent': round(inode_free, 6),
        'disk_pass_at_sample': free >= required and inode_free >= 10,
        'backup_allowlist_count': backup_count, 'backup_allowlist_bytes': backup_bytes,
        'backup_timer': backup_timer,
        'observer': db, 'owned_containers': containers,
        'classification_hint': mode_hint,
        'host_mutations': 'NONE_REQUESTED',
    }
    report['status'] = ('PASS_SNAPSHOT' if
                        parent_safe and lock != 'UNSAFE' and
                        barrier == 'ABSENT' and barrier_state == 'ABSENT' and
                        writers == 0 and
                        report['disk_pass_at_sample'] and
                        backup_count == 0 and backup_timer == {'enabled': 'masked', 'active': 'inactive'} and
                        db['mode'] == 'PRODUCTION_PAPER' and
                        type(db['real_orders_sent']) is int and db['real_orders_sent'] == 0 and
                        db['heartbeat_fresh_180s'] and
                        mode_hint == 'CLOSED_FLAT' and
                        all(v['status'] == 'running' for v in containers.values())
                        else 'NO_GO_SNAPSHOT')
    print('RC6_733_CURRENT_HOST=' + json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'PASS_SNAPSHOT' else 2


if __name__ == '__main__':
    raise SystemExit(main())
