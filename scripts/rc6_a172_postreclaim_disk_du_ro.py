#!/usr/bin/env python3
"""Bounded post-A172 disk allocation census; no file contents or mutation."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import urllib.request


LOCK = Path('/run/lock/porota-rc6-deploy-v2.lock')
BARRIER = Path('/run/lock/porota-rc6-deploy-v2-attempt.json')
PATHS = {
    'docker_data': Path('/var/lib/docker'),
    'trading_data_total': Path('/opt/porota-trading/data'),
    'paper_data': Path('/opt/porota-trading/data/paper_v17'),
    'market_data': Path('/opt/porota-trading/data/market'),
    'deploy_receipts': Path('/opt/porota-trading/data/deploy'),
    'general_backups': Path('/opt/porota-backups'),
    'journal': Path('/var/log/journal'),
    'apt_cache': Path('/var/cache/apt'),
    'tmp': Path('/tmp'),
    'forensics': Path('/var/lib/porota-rc6-forensics'),
}
DOCKER_TYPE = {'Images', 'Containers', 'Local Volumes', 'Build Cache'}


class Unverified(RuntimeError):
    pass


def metadata():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    values = []
    for key in ('id', 'region'):
        with opener.open('http://169.254.169.254/metadata/v1/' + key, timeout=3) as response:
            if response.status != 200:
                raise Unverified('DROPLET_METADATA_HTTP')
            data = response.read(65)
            if not data or len(data) > 64:
                raise Unverified('DROPLET_METADATA_INVALID')
            values.append(data.decode('ascii').strip())
    if tuple(values) != ('594077619', 'nyc1'):
        raise Unverified('DROPLET_IDENTITY_MISMATCH')
    return {'id': values[0], 'region': values[1]}


def usage():
    disk = shutil.disk_usage('/')
    vfs = os.statvfs('/')
    if vfs.f_files <= 0:
        raise Unverified('INODE_TOTAL_INVALID')
    return {'total_bytes': disk.total, 'used_bytes': disk.used,
            'free_bytes': disk.free, 'inodes_total': vfs.f_files,
            'inodes_free': vfs.f_ffree,
            'inodes_free_percent': round(100*vfs.f_ffree/vfs.f_files, 6)}


def safe_path(path):
    root_dev = os.lstat('/').st_dev
    cursor = Path('/')
    for part in path.parts[1:]:
        cursor = cursor / part
        try:
            row = cursor.lstat()
        except FileNotFoundError:
            return False
        if not stat.S_ISDIR(row.st_mode) or row.st_dev != root_dev:
            raise Unverified('ALLOWLIST_PATH_SYMLINK_MOUNT_OR_NONDIR')
    return True


def du_allocated(path, *, timeout=14):
    if not safe_path(path):
        return None
    result = subprocess.run(['du', '-x', '-s', '-B1', '--', str(path)],
                            text=True, capture_output=True, timeout=timeout)
    if result.returncode or len(result.stdout) > 256:
        raise Unverified('DU_UNAVAILABLE')
    match = re.fullmatch(r'([0-9]+)\s+' + re.escape(str(path)), result.stdout.strip())
    if match is None:
        raise Unverified('DU_OUTPUT_INVALID')
    return int(match.group(1))


def docker_disk_summary():
    result = subprocess.run(['docker', 'system', 'df', '--format', '{{json .}}'],
                            text=True, capture_output=True, timeout=15)
    if result.returncode or len(result.stdout) > 65536:
        raise Unverified('DOCKER_DF_UNAVAILABLE')
    rows = result.stdout.splitlines()
    if not rows or len(rows) > 8:
        raise Unverified('DOCKER_DF_SHAPE')
    clean = {}
    for line in rows:
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise Unverified('DOCKER_DF_JSON') from exc
        kind = row.get('Type')
        if kind not in DOCKER_TYPE or kind in clean:
            raise Unverified('DOCKER_DF_TYPE')
        fields = {key: str(row.get(key, '')) for key in ('TotalCount', 'Active', 'Size', 'Reclaimable')}
        if (not re.fullmatch(r'[0-9]+', fields['TotalCount']) or
                not re.fullmatch(r'[0-9]+', fields['Active']) or
                not all(re.fullmatch(r'[0-9.]+\s*[A-Za-z]{0,4}(?:\s*\([0-9.]+%\))?',
                                     fields[key]) for key in ('Size', 'Reclaimable'))):
            raise Unverified('DOCKER_DF_FIELD_INVALID')
        clean[kind] = fields
    return clean


def audit():
    host = metadata()
    fd = os.open(LOCK, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or
                stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1):
            raise Unverified('HOST_MUTEX_UNSAFE')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Unverified('HOST_MUTEX_BUSY') from exc
        if BARRIER.exists() or BARRIER.is_symlink():
            raise Unverified('DEPLOY_BARRIER_PRESENT')
        before = usage()
        docker = docker_disk_summary()
        allocated = {key: du_allocated(path) for key, path in PATHS.items()}
        after = usage()
        if BARRIER.exists() or BARRIER.is_symlink():
            raise Unverified('DEPLOY_BARRIER_CHANGED')
        return {'schema': 'RC6_A172_POSTRECLAIM_DISK_DU_RO_V1', 'status': 'GREEN',
                'read_only': True, 'droplet': host, 'barrier': 'ABSENT',
                'df_before': before, 'df_after': after,
                'allocated_bytes_by_allowlisted_area': allocated,
                'docker_system_df_summary': docker,
                'paths_overlap': {'trading_data_total_contains':
                    ['paper_data', 'market_data', 'deploy_receipts']},
                'host_mutations': 0}
    finally:
        os.close(fd)


def main():
    try:
        report = audit()
    except (Unverified, OSError, ValueError, TypeError, UnicodeError,
            subprocess.TimeoutExpired) as exc:
        report = {'schema': 'RC6_A172_POSTRECLAIM_DISK_DU_RO_V1',
                  'status': 'UNVERIFIED', 'read_only': True,
                  'reason': str(exc) if isinstance(exc, Unverified) else type(exc).__name__}
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'GREEN' else 2


if __name__ == '__main__':
    raise SystemExit(main())
