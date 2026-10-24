#!/usr/bin/env python3
"""Run one Deploy V2 host operation under a protected, inherited flock.

The workflow sends this exact source via a base64 Python -c argument before
installing a candidate.  It is a short-lived part of that operation, not a
background daemon.  A surviving child retains the open file description if
the SSH transport or its parent exits.
"""
from __future__ import annotations

import fcntl
import os
import pwd
import re
import stat
import subprocess
import sys
from pathlib import Path


LOCK_DIR = Path('/run/lock')
LOCK_PATH = LOCK_DIR / 'porota-rc6-deploy-v2.lock'


def open_lock(directory_path: Path = LOCK_DIR, trusted_uid: int = 0) -> int:
    lock_path = directory_path / LOCK_PATH.name
    directory = os.lstat(directory_path)
    if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != trusted_uid or
            directory.st_mode & stat.S_IWOTH and not directory.st_mode & stat.S_ISVTX):
        raise RuntimeError('MUTEX_PARENT_UNSAFE')
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        before = os.fstat(fd)
        on_path = os.lstat(lock_path)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != trusted_uid or
                stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1 or
                (before.st_dev, before.st_ino) != (on_path.st_dev, on_path.st_ino)):
            raise RuntimeError('MUTEX_ALIAS_UNSAFE')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('MUTEX_BUSY') from exc
        after = os.lstat(lock_path)
        if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
            raise RuntimeError('MUTEX_REPLACED')
        return fd
    except BaseException:
        os.close(fd)
        raise


def run(argv: list[str]) -> int:
    if os.geteuid() != 0 or len(argv) < 3 or argv[1] not in ('user', 'root'):
        raise RuntimeError('MUTEX_INVOCATION_INVALID')
    user = pwd.getpwnam(argv[0])
    if user.pw_uid == 0:
        raise RuntimeError('MUTEX_REMOTE_USER_MUST_BE_UNPRIVILEGED')
    allow_contract_lifecycle = len(argv) > 2 and argv[2] == '--allow-contract-lifecycle'
    command = argv[3:] if allow_contract_lifecycle else argv[2:]
    if not command:
        raise RuntimeError('MUTEX_COMMAND_MISSING')
    fd = open_lock()
    try:
        # A Docker child can outlive a lost SSH/sudo CLI even after its parent
        # releases flock. Never start another mutator beside that exact writer.
        if not allow_contract_lifecycle:
            inventory = subprocess.run(
                ['docker', 'ps', '--format', '{{.Names}}', '--filter',
                 'name=porota-deploy-contract-'], capture_output=True,
                text=True, timeout=10, check=False)
            if inventory.returncode:
                raise RuntimeError('CONTRACT_WRITER_INVENTORY_FAILED')
            if any(re.fullmatch(r'porota-deploy-contract-[0-9a-f]{40}', name)
                   for name in inventory.stdout.splitlines()):
                raise RuntimeError('CONTRACT_WRITER_ORPHAN_RUNNING')
        env = os.environ.copy()
        env['RC6_DEPLOY_MUTEX_FD'] = str(fd)
        if argv[1] == 'user':
            env.update(HOME=user.pw_dir, USER=user.pw_name, LOGNAME=user.pw_name)
        def drop_privileges() -> None:
            if argv[1] == 'user':
                os.initgroups(user.pw_name, user.pw_gid)
                os.setgid(user.pw_gid)
                os.setuid(user.pw_uid)
        print('RC6_DEPLOY_HOST_MUTEX=HELD', flush=True)
        return subprocess.run(command, check=False, env=env, pass_fds=(fd,),
                              preexec_fn=drop_privileges).returncode
    finally:
        os.close(fd)


def main() -> int:
    try:
        return run(sys.argv[1:])
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print('RC6_DEPLOY_HOST_MUTEX=RED|' + str(exc), file=sys.stderr)
        return 75


if __name__ == '__main__':
    raise SystemExit(main())
