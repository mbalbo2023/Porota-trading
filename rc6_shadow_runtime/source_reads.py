"""Bounded native reads on verified private bytes; source SQLite never opens.

The yielded identity is always the original source. The copy is ephemeral
derived scratch with the existing canonical byte, space and inode policies.
"""
from contextlib import contextmanager
from pathlib import Path
import stat

from rc6_audit_evidence.sqlite_snapshot import SnapshotError, readonly_copy


def original_source_path(database):
    path = Path(database).absolute()
    if path.resolve(strict=True) != path or any(parent.is_symlink() for parent in path.parents):
        raise SnapshotError("REGULAR_UNALIASED_FILE_REQUIRED")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SnapshotError("REGULAR_UNALIASED_FILE_REQUIRED")
    return path


@contextmanager
def source_connection(database, *, deadline):
    path = original_source_path(database)
    before = path.lstat()
    identity = (before.st_dev, before.st_ino, before.st_uid, before.st_gid, before.st_mode, before.st_nlink)
    with readonly_copy(path, deadline=deadline, validate=False) as connection:
        after = path.lstat()
        if identity != (after.st_dev, after.st_ino, after.st_uid, after.st_gid, after.st_mode, after.st_nlink):
            raise SnapshotError("SOURCE_SNAPSHOT_BUSY")
        yield connection, before
