#!/usr/bin/env python3
"""Receive exactly six frozen files into an attempt-owned staging directory."""
from __future__ import annotations

import os
import re
import stat
import sys
import tarfile
from pathlib import Path

NAMES = frozenset({
    'porota-frozen-candidate.json', 'porota-predeploy-image.tar.gz',
    'porota-deploy-bundle-v2.tgz', 'porota-deploy-bundle-v2-manifest.json',
    'porota-host-manifest-v2.json', 'porota-host-policy-v2.json',
})
ROOT = Path('/tmp')


def attempt_dir(sha: str, run: str, attempt: str) -> Path:
    if not re.fullmatch('[0-9a-f]{40}', sha) or not re.fullmatch('[1-9][0-9]*', run) or not re.fullmatch('[1-9][0-9]*', attempt):
        raise ValueError('STAGING_IDENTITY_INVALID')
    return ROOT / f'porota-deploy-v2-{sha}-{run}-{attempt}'


def receive(directory: Path, stream) -> None:
    if directory.parent != ROOT or directory.is_symlink() or directory.exists():
        raise ValueError('STAGING_PATH_UNSAFE_OR_ALREADY_PRESENT')
    os.mkdir(directory, 0o700)
    parent = os.lstat(directory)
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_nlink < 2 or
            parent.st_uid != os.geteuid()):
        raise ValueError('STAGING_DIRECTORY_UNSAFE')
    seen = set()
    dirfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(dirfd)
        if (opened.st_dev, opened.st_ino) != (parent.st_dev, parent.st_ino):
            raise ValueError('STAGING_DIRECTORY_REPLACED')
        with tarfile.open(fileobj=stream, mode='r|') as archive:
            for member in archive:
                if member.name not in NAMES or member.name in seen or not member.isfile() or member.size < 1:
                    raise ValueError('STAGING_MEMBER_UNSAFE')
                seen.add(member.name)
                fd = os.open(member.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=dirfd)
                with os.fdopen(fd, 'wb') as out:
                    source = archive.extractfile(member)
                    if source is None:
                        raise ValueError('STAGING_SOURCE_MISSING')
                    remaining = member.size
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError('STAGING_SOURCE_TRUNCATED')
                        out.write(chunk)
                        remaining -= len(chunk)
                    out.flush()
                    os.fsync(out.fileno())
        os.fsync(dirfd)
        final = os.lstat(directory)
        if (final.st_dev, final.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError('STAGING_DIRECTORY_REPLACED')
    finally:
        os.close(dirfd)
    if seen != NAMES:
        raise ValueError('STAGING_MEMBERS_INCOMPLETE')
    print('RC6_ATTEMPT_STAGE=GREEN|files=6', flush=True)


if __name__ == '__main__':
    receive(attempt_dir(*sys.argv[1:4]), sys.stdin.buffer)
