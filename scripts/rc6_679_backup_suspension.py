#!/usr/bin/env python3
"""Exact backup retirement after the operator suspension and Deploy V2.

Plan and apply are separate invocations. No active SQLite, logs, images or
PPI Watch paths can enter the allowlist. Apply aborts on any path/inode drift.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys


ROOT = Path('/opt/porota-trading')
GENERAL = Path('/opt/porota-backups')
ARTIFACT = ROOT / 'data/paper_v17/artifacts/observer_v17.db/backups'
LEGACY = ROOT / 'data/backups'
LIMIT = 64
MAX_BYTES = 10 * 1024**3
PATTERNS = (
    (GENERAL, re.compile(r'porota-general-\d{8}T\d{6}Z\.tar\.gz')),
    (ARTIFACT, re.compile(r'(?:paper/(?:observer_\d{4}-\d{2}-\d{2}\.db\.gz|\.restore_test_\d+\.db(?:-wal|-shm)?)|history/market_history_\d{4}-\d{2}-\d{2}\.db\.gz)')),
    (LEGACY, re.compile(r'(?:wave4_closure/observer_v17_pre_wave4_cleanup_\d{8}T\d{6}Z\.db(?:\.gz|-wal|-shm)|preopen-retire/\d{8}T\d{6}Z/porota-preopen\.(?:service|timer)(?:\.(?:cat|journal|status)\.txt)?)')),
)


def run(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True,
                          timeout=15).stdout.strip()


def systemctl_state(command):
    result = subprocess.run(('systemctl', command, 'porota-host-general-backup-rc6.timer'),
                            text=True, capture_output=True, timeout=15)
    if result.returncode not in (0, 1, 3):
        raise RuntimeError('BACKUP_TIMER_STATE_UNAVAILABLE')
    return result.stdout.strip()


def guard(candidate, apply=False):
    state = json.loads((ROOT / 'data/deploy/CURRENT_STATE_V2.json').read_text())
    if (state.get('mode') != 'PRODUCTION_PAPER' or
            type(state.get('real_orders_sent')) is not int or
            state['real_orders_sent'] != 0 or state.get('real_order_routes') != 'NOT_CALLED'):
        raise RuntimeError('PAPER_GUARD_FAILED')
    if apply:
        if (state.get('candidate_sha') != candidate or
                state.get('validation_status') != 'DEPLOYED_VALIDATION_PENDING'):
            raise RuntimeError('CANDIDATE_SHA_CHANGED')
        policy = json.loads((ROOT / 'ops/policy/rc6-disk-housekeeping-v1.json').read_text())
        if policy.get('backups', {}).get('suspended') is not True:
            raise RuntimeError('BACKUP_POLICY_NOT_SUSPENDED')
        if (systemctl_state('is-enabled') != 'masked' or
                systemctl_state('is-active') != 'inactive'):
            raise RuntimeError('BACKUP_TIMER_NOT_MASKED')
        if run('docker', 'exec', 'porota_production_observer', 'python3', '-c',
               'import bi_operational_services as b; assert b.backups_suspended()'):
            raise RuntimeError('OBSERVER_BACKUP_GUARD_UNEXPECTED_OUTPUT')
        if any(run('docker', 'inspect', '-f', '{{.Image}}', name) != state.get('image_id')
               for name in ('porota_production_observer', 'porota_production_dashboard',
                            'porota_critical_approval_rc6')):
            raise RuntimeError('RUNTIME_IMAGE_CHANGED')
        sys.path.insert(0, str(ROOT))
        if not __import__('rc6_host_general_backup_job').backups_suspended():
            raise RuntimeError('HOST_BACKUP_GUARD_FAILED')
    return state


def inventory():
    files = []
    for root, pattern in PATTERNS:
        if root.is_symlink():
            raise RuntimeError('BACKUP_ROOT_SYMLINK')
        if not root.exists():
            continue
        for directory, dirs, names in os.walk(root, followlinks=False):
            if any((Path(directory) / name).is_symlink() for name in dirs):
                raise RuntimeError('BACKUP_DIRECTORY_SYMLINK')
            dirs.sort()
            for name in sorted(names):
                path = Path(directory) / name
                rel = path.relative_to(root).as_posix()
                st = path.lstat()
                if not pattern.fullmatch(rel) or not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                    raise RuntimeError('UNEXPECTED_BACKUP_PATH_OR_TYPE:' + rel)
                files.append({'path': str(path), 'device': st.st_dev, 'inode': st.st_ino,
                              'bytes': st.st_size, 'mtime_ns': st.st_mtime_ns})
                if len(files) > LIMIT or sum(row['bytes'] for row in files) > MAX_BYTES:
                    raise RuntimeError('BACKUP_PLAN_BOUND_EXCEEDED')
    return sorted(files, key=lambda item: item['path'])


def open_files(paths):
    """Fail closed if proc FDs cannot be fully surveyed or any candidate is open."""
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        fd = process / 'fd'
        try:
            handles = list(fd.iterdir())
        except FileNotFoundError:
            continue
        except PermissionError as exc:
            raise RuntimeError('PROC_FD_UNREADABLE') from exc
        for handle in handles:
            try:
                target = os.readlink(handle)
            except FileNotFoundError:
                continue
            except PermissionError as exc:
                raise RuntimeError('PROC_FD_UNREADABLE') from exc
            if target.removesuffix(' (deleted)') in paths:
                raise RuntimeError('BACKUP_FILE_OPEN')


def digest(rows):
    raw = json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate-sha', required=True)
    parser.add_argument('--require-empty', action='store_true',
                        help='Abort if the allowlist has changed from the approved empty inventory')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--plan-out')
    group.add_argument('--apply-plan')
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}', args.candidate_sha):
        raise RuntimeError('CANDIDATE_SHA_INVALID')
    applying = args.apply_plan is not None
    if args.require_empty and not applying:
        raise RuntimeError('BACKUP_EMPTY_GATE_REQUIRES_APPLY')
    before = guard(args.candidate_sha, apply=applying)
    observed = inventory()
    if args.require_empty and observed:
        raise RuntimeError('BACKUP_RETIRE_NEW_FILES_NEED_REVIEW')
    open_files({row['path'] for row in observed})
    if applying:
        target = Path(args.apply_plan)
        if target.is_symlink() or not target.is_file() or target.stat().st_size > 16384:
            raise RuntimeError('PLAN_UNTRUSTED')
        plan = json.loads(target.read_text())
        if (plan.get('schema') != 'RC6_679_BACKUP_RETIRE_V1' or
                plan.get('candidate_sha') != args.candidate_sha or
                plan.get('files') != observed or plan.get('digest') != digest(observed)):
            raise RuntimeError('BACKUP_PLAN_DRIFT')
        for row in observed:
            path = Path(row['path'])
            st = path.lstat()
            if (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_nlink) != (
                    row['device'], row['inode'], row['bytes'], row['mtime_ns'], 1):
                raise RuntimeError('BACKUP_FILE_CHANGED:' + path.name)
            path.unlink()
        if inventory():
            raise RuntimeError('BACKUP_RETIRE_INCOMPLETE')
        after = guard(args.candidate_sha, apply=True)
        if after.get('image_id') != before.get('image_id'):
            raise RuntimeError('RUNTIME_IMAGE_CHANGED')
        receipt = {'schema': 'RC6_679_BACKUP_RETIRE_V1', 'status': 'DONE',
                   'candidate_sha': args.candidate_sha, 'image_id': after.get('image_id'),
                   'count': len(observed),
                   'logical_bytes': sum(row['bytes'] for row in observed),
                   'manifest_sha256': digest(observed),
                   'free_bytes_after': shutil.disk_usage(ROOT).free}
        receipt_path = ROOT / 'data/deploy' / ('backup-retire-' + args.candidate_sha + '.json')
        fd = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(receipt, output, sort_keys=True)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        print('RC6_679_BACKUP_RETIRE=' + json.dumps(receipt, sort_keys=True))
    else:
        payload = {'schema': 'RC6_679_BACKUP_RETIRE_V1',
                   'candidate_sha': args.candidate_sha,
                   'files': observed, 'digest': digest(observed)}
        target = Path(args.plan_out)
        if target.exists() or target.is_symlink():
            raise RuntimeError('PLAN_ALREADY_EXISTS')
        target.write_text(json.dumps(payload, sort_keys=True) + '\n')
        target.chmod(0o600)
        print('RC6_679_BACKUP_PLAN=' + json.dumps(payload, sort_keys=True))


if __name__ == '__main__':
    main()
