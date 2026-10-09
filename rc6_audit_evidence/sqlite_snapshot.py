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
import tempfile  # Compatibility for existing offline capture instrumentation.
import time
from contextlib import contextmanager
from contextvars import ContextVar
import threading

from .sqlite_scratch import SnapshotError, inspect_scratch, private_scratch, snapshot_peak_bytes


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


def _read(member, expected, *, deadline, destination=None, scratch_guard=None):
    # Preserve source atime too. If the caller cannot obtain O_NOATIME, a
    # source-owner capture is required; silently degrading would change it.
    if any(not hasattr(os, flag) for flag in ('O_NOFOLLOW', 'O_NOATIME', 'O_NONBLOCK')):
        raise SnapshotError('SOURCE_OWNER_CAPTURE_REQUIRED')
    # Inventory and open are separate operations. A substituted FIFO must
    # reach the metadata check without waiting for a writer at os.open.
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK
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
                    if scratch_guard is not None:
                        scratch_guard.check()
                    destination.write(chunk)
            if _metadata(os.fstat(stream.fileno())) != expected:
                raise SnapshotError("SOURCE_SNAPSHOT_BUSY")
    except OSError:
        raise SnapshotError("SOURCE_UNAVAILABLE") from None
    return digest.hexdigest()


_ACTIVE_CAPTURE = ContextVar("rc6_verified_source_capture", default=None)


def _bounded_deadline(deadline):
    if deadline is None:
        deadline = time.monotonic() + 10.0
    if (isinstance(deadline, bool) or not isinstance(deadline, (int, float))
            or not math.isfinite(deadline)):
        raise SnapshotError("BOUNDED_TIME_BUDGET_REQUIRED")
    _check(deadline)
    return deadline


def _bounded_source(path, max_source_bytes):
    if type(max_source_bytes) is not int or max_source_bytes <= 0:
        raise SnapshotError("BOUNDED_SOURCE_BUDGET_REQUIRED")
    path = Path(path).absolute()
    if path.resolve() != path or any(parent.is_symlink() for parent in path.parents):
        raise SnapshotError("REGULAR_UNALIASED_FILE_REQUIRED")
    return path


@contextmanager
def _captured_image(path, *, deadline, max_source_bytes, scratch_root,
                    max_scratch_bytes, reserve_bytes, min_free_inode_percent,
                    owner_lease=None):
    """Capture one byte-verified image; the deadline bounds capture, not yield."""
    try:
        before = _inventory(path)
        if sum(info[4] for info in before.values()) > max_source_bytes:
            raise SnapshotError("SOURCE_BYTE_BUDGET_EXHAUSTED")
        with private_scratch(path, main_bytes=before[''][4],
                wal_bytes=before.get('-wal', (0, 0, 0, 0, 0))[4],
                source_shm_bytes=before.get('-shm', (0, 0, 0, 0, 0))[4],
                scratch_root=scratch_root, max_scratch_bytes=max_scratch_bytes,
                reserve_bytes=reserve_bytes, min_free_inode_percent=min_free_inode_percent,
                owner_lease=owner_lease) as (scratch, guard):
            copy = Path(scratch) / "snapshot.sqlite"
            digests = {}
            for suffix, info in before.items():
                _check(deadline)
                # SHM remains process-local indexing/locks. SQLite reconstructs
                # it only in private scratch, never in the Source namespace.
                if suffix in {"", "-wal"}:
                    target = Path(str(copy) + suffix)
                    with target.open("xb") as output:
                        os.chmod(target, 0o600)
                        digests[suffix] = _read(Path(str(path) + suffix), info,
                            deadline=deadline, destination=output, scratch_guard=guard)
                else:
                    digests[suffix] = _read(Path(str(path) + suffix), info, deadline=deadline)
            _verify_source(path, before, digests, deadline=deadline)
            _check(deadline)
            if guard is not None:
                guard.check()
            yield copy, before, digests, guard
    except OSError:
        raise SnapshotError("SOURCE_UNAVAILABLE") from None


def _verify_source(path, before, digests, *, deadline):
    if _inventory(path) != before:
        raise SnapshotError("SOURCE_SNAPSHOT_BUSY")
    for suffix, info in before.items():
        if _read(Path(str(path) + suffix), info, deadline=deadline) != digests[suffix]:
            raise SnapshotError("SOURCE_SNAPSHOT_BUSY")
    if _inventory(path) != before:
        raise SnapshotError("SOURCE_SNAPSHOT_BUSY")
    _check(deadline)


@contextmanager
def _readonly_connection(copy, *, deadline, validate, guard):
    """Isolated SQL transaction/progress state on the same immutable image."""
    _check(deadline)
    connection = None
    try:
        connection = _open_readonly_connection(copy, deadline=deadline, guard=guard)
        if validate and connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise SnapshotError("SOURCE_SNAPSHOT_INVALID")
        _check(deadline)
        yield connection
        _check(deadline)
        if guard is not None:
            guard.check()
    except sqlite3.Error:
        _check(deadline)
        raise SnapshotError("SOURCE_READ_FAILED") from None
    finally:
        if connection is not None:
            connection.close()


def _open_readonly_connection(copy, *, deadline, guard):
    connection = sqlite3.connect(copy.as_uri() + "?mode=ro", uri=True, timeout=.025)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=25")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA temp_store=MEMORY")
        if guard is not None:
            guard.check()
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 100)
        return connection
    except BaseException:
        connection.close()
        raise


class CapturedSource:
    """Thread-bound custody of one verified private image, never a Source DB."""
    def __init__(self, source, copy, before, digests, guard):
        self.source, self._copy = source, copy
        self._before, self._digests, self._guard = before, digests, guard
        self._thread = threading.get_ident()
        self._pid = os.getpid()
        self._live = True
        self._anchor = None
        self._image_inventory = None
        self._image_digests = None
        self.receipt = {"schema": "rc6.single-source-capture.v1", "primary_captures": 1,
            "source_bytes": sum(info[4] for info in before.values()),
            "source_path_sha256": hashlib.sha256(os.fsencode(source)).hexdigest(),
            "source_inventory_sha256": hashlib.sha256(repr(sorted(before.items())).encode()).hexdigest(),
            "source_sha256": dict(digests), "query_consumers": [], "additional_captures": [],
            "capture_seconds": None, "verification_seconds": None,
            "cleanup_seconds": None,
            "scratch_admission": dict(guard.admission) if guard is not None else None,
            "private_image_guard": "rc6.immutable-private-image.v1",
            "private_image_sha256": None, "private_image_unchanged": False,
            "source_unchanged": False, "cleanup_complete": False}

    def assert_owner(self):
        if not self._live or threading.get_ident() != self._thread or os.getpid() != self._pid:
            raise SnapshotError("SOURCE_SNAPSHOT_SCOPE_CUSTODY_REQUIRED")

    def seal(self, *, deadline):
        """Prepare local WAL indexing once, then seal main/WAL before queries.

        An anchor keeps SQLite's private WAL/SHM lifecycle stable across RO
        consumers. Main/WAL are immutable financial bytes; regenerated SHM is
        explicitly mutable derived indexing, excluded from the byte seal.
        """
        self.assert_owner()
        try:
            self._anchor = _open_readonly_connection(self._copy, deadline=deadline, guard=self._guard)
            self._anchor.execute("SELECT name FROM sqlite_master LIMIT 0").fetchall()
            self._anchor.set_progress_handler(None, 0)
        except sqlite3.Error:
            _check(deadline)
            raise SnapshotError("SOURCE_READ_FAILED") from None
        self._image_inventory, self._image_digests = {}, {}
        for suffix in ("", "-wal"):
            member = Path(str(self._copy)+suffix)
            if not member.exists():
                continue
            info = member.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid():
                raise SnapshotError("PRIVATE_IMAGE_CUSTODY_REQUIRED")
            # SQLite may create an empty derived WAL when a quiescent WAL-mode
            # Source has no WAL. It cannot introduce nonempty financial bytes.
            if suffix not in self._digests and (suffix != "-wal" or info.st_size):
                raise SnapshotError("PRIVATE_IMAGE_CHANGED")
            os.chmod(member, 0o400)
            self._image_inventory[suffix] = _metadata(member.lstat())
            self._image_digests[suffix] = self._digests.get(suffix, hashlib.sha256(b"").hexdigest())
        self.receipt["private_image_sha256"] = dict(self._image_digests)
        _check(deadline)
        if self._guard is not None:
            self._guard.check()

    def verify_image(self, *, deadline):
        self.assert_owner()
        def inventory():
            result = {}
            for suffix in ("", "-wal"):
                member = Path(str(self._copy)+suffix)
                try:
                    info = member.lstat()
                except FileNotFoundError:
                    continue
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o400):
                    raise SnapshotError("PRIVATE_IMAGE_CHANGED")
                result[suffix] = _metadata(info)
            return result
        if inventory() != self._image_inventory:
            raise SnapshotError("PRIVATE_IMAGE_CHANGED")
        for suffix, info in self._image_inventory.items():
            if _read(Path(str(self._copy)+suffix), info, deadline=deadline) != self._image_digests[suffix]:
                raise SnapshotError("PRIVATE_IMAGE_CHANGED")
        if inventory() != self._image_inventory:
            raise SnapshotError("PRIVATE_IMAGE_CHANGED")
        _check(deadline)
        self.receipt["private_image_unchanged"] = True

    def close_anchor(self):
        if self._anchor is not None:
            self._anchor.close()
            self._anchor = None

    @contextmanager
    def connection(self, *, deadline, validate, consumer):
        self.assert_owner()
        if consumer is not None and (type(consumer) is not str or not consumer or len(consumer) > 64):
            raise SnapshotError("SOURCE_QUERY_CONSUMER_INVALID")
        if len(self.receipt["query_consumers"]) >= 64:
            raise SnapshotError("SOURCE_QUERY_CONSUMER_BUDGET_EXHAUSTED")
        started = time.monotonic()
        entry = {"consumer": consumer or "direct_readonly_copy", "query_seconds": None,
                 "shared_primary_capture": True}
        self.receipt["query_consumers"].append(entry)
        try:
            with _readonly_connection(self._copy, deadline=deadline, validate=validate,
                                      guard=self._guard) as connection:
                yield connection
        finally:
            entry["query_seconds"] = time.monotonic() - started


@contextmanager
def captured_source(path, *, capture_budget_seconds, verification_budget_seconds,
                    max_source_bytes=512 * 1024 * 1024, scratch_root=None,
                    max_scratch_bytes=None, reserve_bytes=None, min_free_inode_percent=None):
    """A coherent capture shared by all same-Source reads in this tick/context.

    Consumers get separate read-only SQLite connections without recopy/hash.
    The Source is inventory/digest-verified after all consumers and before this
    context returns. Publication belongs after successful context completion.
    Additional distinct history Sources keep their own verified capture.
    """
    for value in (capture_budget_seconds, verification_budget_seconds):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value <= 0):
            raise SnapshotError("BOUNDED_TIME_BUDGET_REQUIRED")
    if _ACTIVE_CAPTURE.get() is not None:
        raise SnapshotError("SOURCE_SNAPSHOT_SCOPE_REENTRY_FORBIDDEN")
    path = _bounded_source(path, max_source_bytes)
    started = time.monotonic()
    deadline = started + capture_budget_seconds
    capture = None
    with _captured_image(path, deadline=deadline, max_source_bytes=max_source_bytes,
            scratch_root=scratch_root, max_scratch_bytes=max_scratch_bytes,
            reserve_bytes=reserve_bytes, min_free_inode_percent=min_free_inode_percent) as image:
        capture = CapturedSource(path, *image)
        try:
            capture.seal(deadline=deadline)
            capture.receipt["capture_seconds"] = time.monotonic() - started
            token = _ACTIVE_CAPTURE.set(capture)
            try:
                yield capture
                capture.assert_owner()
                verify_started = time.monotonic()
                verify_deadline = verify_started + verification_budget_seconds
                capture.verify_image(deadline=verify_deadline)
                _verify_source(path, capture._before, capture._digests, deadline=verify_deadline)
                capture.receipt["verification_seconds"] = time.monotonic() - verify_started
                capture.receipt["source_unchanged"] = True
            finally:
                _ACTIVE_CAPTURE.reset(token)
        finally:
            capture._live = False
            capture.close_anchor()
        cleanup_started = time.monotonic()
    capture.receipt["cleanup_seconds"] = time.monotonic() - cleanup_started
    capture.receipt["cleanup_complete"] = True


@contextmanager
def readonly_copy(path, *, deadline=None, max_source_bytes=512 * 1024 * 1024,
                  validate=True, scratch_root=None, max_scratch_bytes=None,
                  reserve_bytes=None, min_free_inode_percent=None, consumer=None):
    """Yield a Row connection on verified private bytes; clean it on every exit.

    Standalone calls retain the existing absolute capture/query/cleanup bound.
    Inside an explicit captured_source scope, the same primary image is reused:
    this deadline bounds the query and its connection, while the enclosing
    capture and final Source verification each have their separate deadline.
    """
    deadline = _bounded_deadline(deadline)
    path = _bounded_source(path, max_source_bytes)
    capture = _ACTIVE_CAPTURE.get()
    if capture is not None:
        capture.assert_owner()
        if path == capture.source:
            if capture.receipt["source_bytes"] > max_source_bytes:
                raise SnapshotError("SOURCE_BYTE_BUDGET_EXHAUSTED")
            if any(value is not None for value in (scratch_root, max_scratch_bytes,
                    reserve_bytes, min_free_inode_percent)):
                raise SnapshotError("SOURCE_SNAPSHOT_SCOPE_CONFIGURATION_MISMATCH")
            with capture.connection(deadline=deadline, validate=validate, consumer=consumer) as connection:
                yield connection
            _check(deadline)
            return
        # History is a different Source, not another primary capture. Keep a
        # bounded receipt of every such additional capture and its reason.
        if len(capture.receipt["additional_captures"]) >= 8:
            raise SnapshotError("SOURCE_ADDITIONAL_CAPTURE_BUDGET_EXHAUSTED")
        additional = {"reason": "DISTINCT_SOURCE_IDENTITY", "consumer": consumer or "preopen_history",
            "source_path_sha256": hashlib.sha256(os.fsencode(path)).hexdigest(),
            "source_bytes": None, "source_sha256": None, "capture_seconds": None,
            "source_unchanged": False, "cleanup_complete": False}
        capture.receipt["additional_captures"].append(additional)
    else:
        additional = None
    started = time.monotonic()
    with _captured_image(path, deadline=deadline, max_source_bytes=max_source_bytes,
            scratch_root=scratch_root, max_scratch_bytes=max_scratch_bytes,
            reserve_bytes=reserve_bytes, min_free_inode_percent=min_free_inode_percent,
            owner_lease=capture._guard if capture is not None else None) as image:
        copy, before, digests, guard = image
        if additional is not None:
            additional.update(source_bytes=sum(info[4] for info in before.values()),
                source_sha256=dict(digests), capture_seconds=time.monotonic()-started,
                scratch_admission=dict(guard.admission) if guard is not None else None)
        with _readonly_connection(copy, deadline=deadline, validate=validate, guard=guard) as connection:
            yield connection
        # A standalone or distinct-history consumer also cannot certify rows
        # after the authoritative Source changed during its SQL phase.
        _verify_source(path, before, digests, deadline=deadline)
        if additional is not None:
            additional["source_unchanged"] = True
    # Existing standalone consumers include physical cleanup in their budget.
    _check(deadline)
    if additional is not None:
        additional["cleanup_complete"] = True
