#!/usr/bin/env python3
"""Read-only, exact host receipt after A170 failed before runtime restart."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import urllib.request

ROOT = Path('/opt/porota-trading')
sys.path.insert(0, str(ROOT))
OLD = 'b778c8063e96574ddf859907abb99fae6f4e3484'
CURRENT = '33ea6d365c47d415fc5480a23b1338c208c2a29f'
OLD_DIGEST = 'ac2d6ec4bb3fe2a2ce809d89034cb564c1a3ce790de21838b654abc787471f81'
Q_DIGEST = '5346f3b56681056bf91e2324e7e6a2c440d067749272090820057390bc8f3412'
OWNED = ('porota_production_observer', 'porota_production_dashboard')


def receipt(name):
    path = ROOT / 'data/deploy' / name
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size < 65536:
        raise ValueError('RECEIPT_PATH_OR_BOUND_RED')
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def command(*argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=15, check=True).stdout


def unit_state(name, mode):
    return subprocess.run(['systemctl', 'is-' + mode, name], capture_output=True,
                          text=True, timeout=5).stdout.strip()


def run():
    if os.geteuid() != 0:
        raise ValueError('ROOT_REQUIRED')
    lock = Path('/run/lock/porota-rc6-deploy-v2.lock')
    info = lock.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError('MUTEX_IDENTITY_RED')
    with lock.open('rb') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        barrier = Path('/run/lock/porota-rc6-deploy-v2-attempt.json')
        if barrier.exists() or barrier.is_symlink():
            raise ValueError('ATTEMPT_BARRIER_PRESENT')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        droplet = {}
        for field in ('id', 'region'):
            with opener.open('http://169.254.169.254/metadata/v1/' + field, timeout=2) as response:
                droplet[field] = response.read(64).decode().strip()
        if droplet != {'id': '594077619', 'region': 'nyc1'}:
            raise ValueError('DROPLET_IDENTITY_RED')
        source = ROOT / 'scripts/rc6_deploy_quiescence.py'
        if source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != Q_DIGEST:
            raise ValueError('INSTALLED_SOURCE_RED')
        old, old_digest = receipt('quiescence-' + OLD + '.json')
        new, new_digest = receipt('quiescence-' + CURRENT + '.json')
        if (old_digest != OLD_DIGEST or old.get('status') != 'PRIOR_A143_UNVALIDATED_HELD_STOPPED' or
                old.get('installation_started') is not False or
                new.get('candidate_sha') != CURRENT or
                new.get('status') != 'FIX_FORWARD_VALIDATION_REQUIRED' or
                new.get('installation_started') is not True or
                new.get('a143_pending_adopted') is not True or
                new.get('a169_origin_sha256') != OLD_DIGEST or
                new.get('real_orders_sent') != 0 or new.get('ppi_watch_accessed') is not False or
                new.get('inflight') is not None or
                set(old.get('containers', {})) != set(OWNED) or
                new.get('containers') != old.get('containers') or
                new.get('stop_evidence') != old.get('stop_evidence')):
            raise ValueError('EXACT_RECEIPT_CHAIN_RED')
        actual = json.loads(command('docker', 'inspect', *OWNED))
        if len(actual) != 2:
            raise ValueError('OWNED_RUNTIME_COUNT_RED')
        for entry in actual:
            name = entry['Name'].lstrip('/')
            if (name not in OWNED or entry['Id'] != old['containers'][name]['id'] or
                    entry['Image'] != old['containers'][name]['image'] or
                    entry['State']['Status'] != 'exited' or
                    entry['State']['ExitCode'] != 0):
                raise ValueError('OLD_DOCKER_IDENTITY_OR_STOP_RED')
        from scripts import rc6_deploy_quiescence as q
        from scripts import rc6_deploy_scheduler_suspend as scheduler
        book = q.a143_stopped_continuation(old)
        if (book.get('real_orders_sent') != 0 or
                book.get('source_sha') != q.A143_PENDING_SOURCE_SHA or
                (book.get('process_state'), book.get('session_state')) not in
                {('STOPPED', 'BOOT_CALENDAR'), ('DEGRADED', 'BOOT_CALENDAR')}):
            raise ValueError('PAPER_BOOK_RED')
        timer, _ = receipt('scheduler-suspend-' + CURRENT + '.json')
        names = timer.get('timers')
        if (timer.get('status') != 'SUSPENDED' or timer.get('candidate_sha') != CURRENT or
                type(names) is not list or len(names) != 16 or len(set(names)) != 16):
            raise ValueError('TIMER_RECEIPT_RED')
        guardian = timer.get('preserved_iol_timer')
        for name in names:
            active, enabled = unit_state(name, 'active'), unit_state(name, 'enabled')
            expected = ('active', 'enabled') if name == guardian else ('inactive', 'disabled') if name == scheduler.OPERATOR_PREOPEN_TIMER else ('inactive', 'enabled')
            if (active, enabled) != expected:
                raise ValueError('TIMER_STATE_RED')
        # Only inspect PPI Watch unit states; do not start, stop, or read its data.
        ppi = [row.split()[0] for row in command('systemctl', 'list-unit-files', '--no-legend').splitlines()
               if row.split() and 'ppi' in row.split()[0].lower() and 'watch' in row.split()[0].lower()]
        ppi_states = {name: [unit_state(name, 'active'), unit_state(name, 'enabled')]
                      for name in ppi}
        usage = shutil.disk_usage('/')
        statvfs = os.statvfs('/')
        return {'schema': 'rc6.a171.failed-install-readonly.v1', 'status': 'PROVEN',
                'droplet': droplet, 'candidate': CURRENT, 'prior': OLD,
                'prior_receipt_sha256': old_digest, 'failed_receipt_sha256': new_digest,
                'failed_inflight': 'MISSING', 'failed_status': new['status'],
                'installation_started': True, 'runtime': 'BOTH_EXACT_OLD_EXITED_0',
                'book': [book['process_state'], book['session_state']],
                'real_orders_sent': 0, 'timers_held': len(names),
                'iol_guardian': [unit_state(guardian, 'active'), unit_state(guardian, 'enabled')],
                'ppi_watch_state_only': ppi_states, 'barrier': 'ABSENT',
                'disk_free_bytes': usage.free,
                'inode_free_percent': round(100 * statvfs.f_ffree / statvfs.f_files, 6),
                'host_mutations': 0, 'operational_go': False}


if __name__ == '__main__':
    try:
        row = run()
        print('RC6_A171_FAILED_INSTALL_RO=' + json.dumps(row, sort_keys=True, separators=(',', ':')))
    except Exception as exc:
        code = str(exc).split(':', 1)[0]
        print('RC6_A171_FAILED_INSTALL_RO=RED|' + (code if re.fullmatch('[A-Z][A-Z0-9_]{2,99}', code) else type(exc).__name__.upper()))
        raise SystemExit(2)
