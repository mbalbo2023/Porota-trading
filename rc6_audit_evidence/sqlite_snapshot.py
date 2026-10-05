"""Bounded SQLite reads from a verified private filesystem copy.

SQLite never opens the source: even ``mode=ro`` can create source WAL SHM.
The input must be quiescent while main and WAL bytes are captured. Concurrent
changes, rollback journals, aliases and an unbounded source fail closed.
"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import time
from contextlib import contextmanager


class SnapshotError(ValueError):
    """Sanitized reason, with no private source path or SQLite details."""


def _check(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise SnapshotError("TIME_BUDGET_EXHAUSTED")


def _metadata(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _inventory(path):
    result = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        member = Path(str(path) + suffix)
        try:
            info = member.lstat()
        except FileNotFoundError:
            if not suffix:
                raise SnapshotError("SOURCE_UNAVAILABLE") from None
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SnapshotError("REGULAR_UNALIASED_FILE_REQUIRED")
        result[suffix] = _metadata(info)
    if result.get("-journal", (0, 0, 0, 0, 0))[4]:
        raise SnapshotError("SOURCE_SNAPSHOT_BUSY")
    return result


def _read(member, expected, *, deadline, destination=None):
    # Preserve source atime too. If the caller cannot obtain O_NOATIME, a
    # source-owner capture is required; silently degrading would change it.
    if not hasattr(os,'O_NOFOLLOW') or not hasattr(os,'O_NOATIME'):
        raise SnapshotError('SOURCE_OWNER_CAPTURE_REQUIRED')
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME
    digest = hashlib.sha256()
    try:
        descriptor = os.open(member, flags)
        with os.fdopen(descriptor, "rb") as stream:
            if _metadata(os.fstat(stream.fileno())) != expected:
                raise SnapshotError("SOURCE_SNAPSHOT_BUSY")
            consumed = 0
            while True:
                _check(deadline)
                # Read at most the declared remaining bytes plus one sentinel.
                # A growing source never causes an oversized scratch write.
                chunk = stream.read(min(1024 * 1024, expected[4] - consumed + 1))
                if not chunk:
                    if consumed != expected[4]:
                        raise SnapshotError('SOURCE_SNAPSHOT_BUSY')
                    break
                if consumed + len(chunk) > expected[4]:
                    raise SnapshotError('SOURCE_SNAPSHOT_BUSY')
                consumed += len(chunk)
                digest.update(chunk)
                if destination is not None:
                    destination.write(chunk)
            if _metadata(os.fstat(stream.fileno())) != expected:
                raise SnapshotError("SOURCE_SNAPSHOT_BUSY")
    except OSError:
        raise SnapshotError("SOURCE_UNAVAILABLE") from None
    return digest.hexdigest()


@contextmanager
def readonly_copy(path, *, deadline=None, max_source_bytes=512 * 1024 * 1024,
                  validate=True):
    """Yield a Row connection on a coherent copy; clean it on every exit.

    ``validate=False`` omits full quick_check for bounded dashboard reads.
    Main/WAL identity, byte digests and causal stability are always checked.
    An unchanged committed image can be copied while a writer holds an unused
    DELETE-mode lock; a nonempty rollback journal cannot be copied safely.
    """
    if type(max_source_bytes) is not int or max_source_bytes <= 0:
        raise SnapshotError("BOUNDED_SOURCE_BUDGET_REQUIRED")
    if deadline is None:
        deadline = time.monotonic() + 10.0
    if isinstance(deadline,bool) or not isinstance(deadline,(int,float)) or not math.isfinite(deadline):
        raise SnapshotError('BOUNDED_TIME_BUDGET_REQUIRED')
    path = Path(path).absolute()
    if path.resolve() != path or any(parent.is_symlink() for parent in path.parents):
        raise SnapshotError("REGULAR_UNALIASED_FILE_REQUIRED")
    _check(deadline)
    try:
        before = _inventory(path)
        if sum(info[4] for info in before.values()) > max_source_bytes:
            raise SnapshotError("SOURCE_BYTE_BUDGET_EXHAUSTED")
        with tempfile.TemporaryDirectory(prefix="rc6-sqlite-copy-") as scratch:
            copy = Path(scratch) / "snapshot.sqlite"
            digests = {}
            for suffix, info in before.items():
                _check(deadline)
                # SHM is process-local indexing/locks. SQLite reconstructs it
                # in scratch from the transported main+WAL, never in source.
                if suffix in {"", "-wal"}:
                    target = Path(str(copy) + suffix)
                    with target.open("xb") as output:
                        os.chmod(target, 0o600)
                        digests[suffix] = _read(Path(str(path) + suffix), info,
                                                deadline=deadline, destination=output)
                else:
                    digests[suffix] = _read(Path(str(path) + suffix), info, deadline=deadline)
            if _inventory(path) != before:
                raise SnapshotError("SOURCE_SNAPSHOT_BUSY")
            for suffix, info in before.items():
                if _read(Path(str(path) + suffix), info, deadline=deadline) != digests[suffix]:
                    raise SnapshotError("SOURCE_SNAPSHOT_BUSY")
            if _inventory(path) != before:
                raise SnapshotError("SOURCE_SNAPSHOT_BUSY")
            _check(deadline)
            connection = sqlite3.connect(copy.as_uri() + "?mode=ro", uri=True, timeout=.025)
            try:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA query_only=ON")
                connection.execute("PRAGMA busy_timeout=25")
                connection.execute("PRAGMA trusted_schema=OFF")
                connection.set_progress_handler(
                    lambda: int(deadline is not None and time.monotonic() >= deadline), 100)
                if validate and connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise SnapshotError("SOURCE_SNAPSHOT_INVALID")
                _check(deadline)
                yield connection
                _check(deadline)
            finally:
                connection.close()
    except sqlite3.Error:
        _check(deadline)
        raise SnapshotError("SOURCE_READ_FAILED") from None
    except OSError:
        raise SnapshotError("SOURCE_UNAVAILABLE") from None
