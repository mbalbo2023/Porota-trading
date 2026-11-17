#!/usr/bin/env python3
"""One-shot, no-sudo repair of Porota's fixed login .config parent only.

Only the group/other WRITE permission bits may change. This is a host
custody repair, NOT a preopen receipt, PAPER rearm, timer or Docker action.
GitHub Actions transmits this reviewed file to the SSH login via stdin.
"""
from __future__ import annotations

from contextlib import ExitStack
import grp
import json
import os
import pwd
import stat
import sys


UID = 1000
LOGIN = "porotaadmin"
HOME = "/home/porotaadmin"
SCHEMA = "rc6.preopen.config-parent-mode.v1"
OPERATION = "config_parent_repair"
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
REASONS = frozenset({
    "LOGIN_IDENTITY_UNVERIFIED", "LOGIN_ACCOUNT_UNVERIFIED",
    "LOGIN_PRIVATE_GROUP_UNVERIFIED", "HOST_ROOT_CUSTODY_UNTRUSTED",
    "HOST_PARENT_CUSTODY_UNTRUSTED", "HOST_PARENT_CUSTODY_UNVERIFIED",
    "CONFIG_PARENT_UNAVAILABLE", "CONFIG_PARENT_CUSTODY_UNTRUSTED",
    "CONFIG_PARENT_SPECIAL_MODE_UNVERIFIED", "CONFIG_PARENT_CHANGED_DURING_REPAIR",
    "CONFIG_PARENT_IO_UNVERIFIED", "UNEXPECTED_ARGUMENTS",
})


class RepairHold(ValueError):
    """A fixed, non-sensitive hold reason; never include an OS error message."""


def _same_inode(a, b):
    return (a.st_dev, a.st_ino, a.st_uid, a.st_gid) == (b.st_dev, b.st_ino, b.st_uid, b.st_gid)


def _verified_child(parent_fd, component, owner, group):
    """Return an FD pinned to one non-aliased directory and exact owner."""
    fd = None
    try:
        fd = os.open(component, DIR_FLAGS, dir_fd=parent_fd)
        observed = os.fstat(fd)
        named = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
        if (not stat.S_ISDIR(observed.st_mode) or not stat.S_ISDIR(named.st_mode)
                or not _same_inode(observed, named) or observed.st_mode != named.st_mode
                or observed.st_uid != owner or observed.st_gid != group or
                observed.st_mode & 0o022):
            raise RepairHold("HOST_PARENT_CUSTODY_UNTRUSTED")
        return fd
    except OSError:
        if fd is not None:
            os.close(fd)
        raise RepairHold("HOST_PARENT_CUSTODY_UNVERIFIED") from None
    except RepairHold:
        if fd is not None:
            os.close(fd)
        raise


def repair_pinned_config(home_fd, *, expected_uid, expected_gid):
    """Change ONLY mode g/o write on the pinned `.config` directory inode.

    The caller must authenticate and pin the full fixed HOST ancestor chain.
    This helper takes a directory descriptor rather than a path from a caller.
    """
    if (type(expected_uid) is not int or expected_uid <= 0 or
            type(expected_gid) is not int or expected_gid <= 0):
        raise RepairHold("LOGIN_IDENTITY_UNVERIFIED")
    try:
        fd = os.open(".config", DIR_FLAGS, dir_fd=home_fd)
    except OSError:
        raise RepairHold("CONFIG_PARENT_UNAVAILABLE") from None
    try:
        before = os.fstat(fd)
        named = os.stat(".config", dir_fd=home_fd, follow_symlinks=False)
        if (not stat.S_ISDIR(before.st_mode) or not stat.S_ISDIR(named.st_mode)
                or before.st_uid != expected_uid or before.st_gid != expected_gid or
                not _same_inode(before, named)
                or before.st_mode != named.st_mode):
            raise RepairHold("CONFIG_PARENT_CUSTODY_UNTRUSTED")
        before_mode = stat.S_IMODE(before.st_mode)
        # An unexpected set-ID/sticky mode can make fchmod clear unrelated bits.
        if before_mode & 0o7000:
            raise RepairHold("CONFIG_PARENT_SPECIAL_MODE_UNVERIFIED")
        desired_mode = before_mode & ~0o022
        mutated = before_mode != desired_mode
        if mutated:
            os.fchmod(fd, desired_mode)
        after = os.fstat(fd)
        named_after = os.stat(".config", dir_fd=home_fd, follow_symlinks=False)
        if (not _same_inode(before, after) or not _same_inode(after, named_after)
                or not stat.S_ISDIR(named_after.st_mode) or
                stat.S_IMODE(after.st_mode) != desired_mode or
                stat.S_IMODE(named_after.st_mode) != desired_mode):
            raise RepairHold("CONFIG_PARENT_CHANGED_DURING_REPAIR")
        return {"state": "REPAIRED" if mutated else "ALREADY_SAFE",
                "before_mode": oct(before_mode), "after_mode": oct(desired_mode),
                "mutations": int(mutated)}
    except OSError:
        raise RepairHold("CONFIG_PARENT_IO_UNVERIFIED") from None
    finally:
        os.close(fd)


def repair_current_host():
    """Pin `/home/porotaadmin/.config` from `/`; no caller/env path inputs."""
    if (os.getuid() != UID or os.geteuid() != UID or
            os.getgid() != UID or os.getegid() != UID):
        raise RepairHold("LOGIN_IDENTITY_UNVERIFIED")
    account = pwd.getpwuid(UID)
    if account.pw_name != LOGIN or account.pw_dir != HOME or account.pw_gid != UID:
        raise RepairHold("LOGIN_ACCOUNT_UNVERIFIED")
    try:
        primary_group = grp.getgrgid(UID)
        other_members = [name for name in primary_group.gr_mem if name != LOGIN]
        foreign_accounts = [user for user in pwd.getpwall()
                            if user.pw_gid == UID and user.pw_uid != UID]
        if (primary_group.gr_name != LOGIN or other_members or foreign_accounts):
            raise RepairHold("LOGIN_PRIVATE_GROUP_UNVERIFIED")
    except (KeyError, OSError):
        raise RepairHold("LOGIN_PRIVATE_GROUP_UNVERIFIED") from None
    with ExitStack() as opened:
        root_fd = os.open("/", DIR_FLAGS)
        opened.callback(os.close, root_fd)
        root = os.fstat(root_fd)
        if (root.st_uid != 0 or not stat.S_ISDIR(root.st_mode) or root.st_mode & 0o022):
            raise RepairHold("HOST_ROOT_CUSTODY_UNTRUSTED")
        home_parent = _verified_child(root_fd, "home", 0, 0)
        opened.callback(os.close, home_parent)
        home_fd = _verified_child(home_parent, LOGIN, UID, UID)
        opened.callback(os.close, home_fd)
        return repair_pinned_config(home_fd, expected_uid=UID, expected_gid=UID)


def main():
    try:
        if len(sys.argv) != 1:
            raise RepairHold("UNEXPECTED_ARGUMENTS")
        observed = repair_current_host()
        result = {"schema": SCHEMA, "operation": OPERATION, "uid": UID,
                  "ppi_watch_touched": False, "boca_touched": False,
                  "docker_touched": False, "systemd_touched": False,
                  "db_touched": False, "order_routes": "NOT_CALLED",
                  "private_group_verified": True,
                  "indirect_impact": "NO_VERIFICADO", **observed}
        code = 0
    except (RepairHold, OSError, ValueError, KeyError, TypeError):
        # Do not emit raw exception strings, arbitrary paths, content or secrets.
        reason = sys.exc_info()[1]
        description = str(reason)
        result = {"schema": SCHEMA, "operation": OPERATION, "state": "BLOQUEADO",
                  "reason": (description if isinstance(reason, RepairHold) and
                             description in REASONS else "HOST_IO_UNVERIFIED"),
                  "mutations": "NO_VERIFICADO"}
        code = 2
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
