"""Per-tick native reads on verified private bytes; historical standalone API.

The yielded identity is always the original source. The copy is ephemeral
derived scratch with the existing canonical byte, space and inode policies.
"""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import stat

from rc6_audit_evidence.sqlite_snapshot import (SnapshotError, captured_source,
    readonly_copy, source_capture_active)
from .read_contract import DEFAULT_READ_CONTRACT, activate_read_contract


def original_source_path(database):
    path = Path(database).absolute()
    if path.resolve(strict=True) != path or any(parent.is_symlink() for parent in path.parents):
        raise SnapshotError("REGULAR_UNALIASED_FILE_REQUIRED")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SnapshotError("REGULAR_UNALIASED_FILE_REQUIRED")
    return path


@contextmanager
def source_tick(database, *, contract=DEFAULT_READ_CONTRACT):
    """One primary capture, verified/removed before the caller can publish."""
    path = original_source_path(database)
    with activate_read_contract(contract):
        with captured_source(path,
                capture_budget_seconds=contract.capture_budget_seconds,
                verification_budget_seconds=contract.verification_budget_seconds,
                max_source_bytes=contract.maximum_source_bytes) as capture:
            capture.receipt["read_contract_sha256"] = contract.fingerprint()
            yield capture


@contextmanager
def source_connection(database, *, deadline, consumer=None):
    path = original_source_path(database)
    before = path.lstat()
    identity = (before.st_dev, before.st_ino, before.st_uid, before.st_gid, before.st_mode, before.st_nlink)
    with readonly_copy(path, deadline=deadline, validate=False, consumer=consumer) as connection:
        after = path.lstat()
        if identity != (after.st_dev, after.st_ino, after.st_uid, after.st_gid, after.st_mode, after.st_nlink):
            raise SnapshotError("SOURCE_SNAPSHOT_BUSY")
        yield connection, before


@contextmanager
def tick_reader_connection(database, *, deadline, consumer):
    """Share the verified Source only inside a productive worker tick.

    Existing standalone diagnostics retain their incumbent read-only budget
    and behavior, but cannot claim a multi-consumer PIT receipt. A published
    worker cut always runs under source_tick and thus cannot use this fallback.
    """
    if source_capture_active():
        with source_connection(database, deadline=deadline, consumer=consumer) as value:
            yield value
    else:
        path = Path(database).resolve(strict=True)
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True,
                                     timeout=.005)) as connection:
            connection.row_factory = sqlite3.Row
            yield connection, path.stat()
