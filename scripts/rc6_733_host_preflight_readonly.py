#!/usr/bin/env python3
"""Bounded Deploy V2 permission and mutex metadata probe; never acquires lock."""
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys


def main():
    if len(sys.argv) != 3 or not re.fullmatch(r'[0-9a-f]{40}', sys.argv[2]):
        raise ValueError('PREFLIGHT_IDENTITY_INVALID')
    user = pwd.getpwnam(sys.argv[1])
    if os.geteuid() != 0 or user.pw_uid == 0:
        raise ValueError('PREFLIGHT_PRIVILEGE_INVALID')
    directory = Path('/run/lock')
    root = os.lstat(directory)
    safe_parent = (stat.S_ISDIR(root.st_mode) and root.st_uid == 0 and
                   (not root.st_mode & stat.S_IWOTH or root.st_mode & stat.S_ISVTX))
    target = directory / 'porota-rc6-deploy-v2.lock'
    try:
        lock = os.lstat(target)
    except FileNotFoundError:
        lock_status = 'ABSENT_ROOT_CAN_CREATE'
    else:
        if not (stat.S_ISREG(lock.st_mode) and lock.st_uid == 0 and
                stat.S_IMODE(lock.st_mode) == 0o600 and lock.st_nlink == 1):
            lock_status = 'UNSAFE'
        else:
            lock_status = 'ROOT_0600_SINGLE_LINK'
    inventory = subprocess.run(
        ['docker', 'ps', '--format', '{{.Names}}', '--filter',
         'name=porota-deploy-contract-'], capture_output=True, text=True,
        timeout=7, check=False)
    writers = (sum(bool(re.fullmatch(r'porota-deploy-contract-[0-9a-f]{40}', n))
                   for n in inventory.stdout.splitlines())
               if inventory.returncode == 0 else None)
    fs = os.statvfs('/')
    available = fs.f_bavail * fs.f_frsize
    inode_free_percent = (100.0 * fs.f_ffree / fs.f_files) if fs.f_files else 0.0
    # Exact #733 frozen bytes plus the product policy's 2 GiB headroom.
    required = 277389663 + 2 * 806479940 + 2 * 1632401 + 2147483648
    report = {
        'schema': 'RC6_733_HOST_PREFLIGHT_READONLY_V1',
        'candidate_sha': sys.argv[2],
        'sudo_python_c_euid': os.geteuid(),
        'remote_user_unprivileged': True,
        'lock_parent_safe': bool(safe_parent),
        'lock_status': lock_status,
        'contract_writers_running': writers,
        'root_available_bytes': available,
        'inode_free_percent': round(inode_free_percent, 6),
        'frozen_pretransfer_required_bytes': required,
        'disk_formula_pass_at_sample': available >= required,
        'inode_gate_pass_at_sample': inode_free_percent >= 10,
        'side_effects': 'NONE_READ_ONLY',
    }
    report['status'] = ('GREEN' if safe_parent and lock_status != 'UNSAFE'
                        and writers == 0 and available >= required
                        and inode_free_percent >= 10 else 'RED')
    print('RC6_733_HOST_PREFLIGHT=' + json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'GREEN' else 2


if __name__ == '__main__':
    raise SystemExit(main())
