"""Explicit custody/close boundaries for offline fixture SQLite writers.

The native writer implementation is unchanged. Tracking keeps committed
connections alive until a declared fixture boundary, then closes them before
Source capture/inventory; GC timing is never the quiescence authority.
"""
from contextlib import contextmanager
import sqlite3
import threading
from unittest.mock import patch


class FixtureWriterLifecycle:
    def __init__(self, connect):
        self._connect = connect
        self._writers = []
        self._thread = threading.get_ident()
        self.closed_writers = 0
        self.quiescent_boundaries = 0

    def connect(self, database, *args, **kwargs):
        if threading.get_ident() != self._thread:
            raise ValueError("FIXTURE_WRITER_THREAD_CUSTODY_REQUIRED")
        connection = self._connect(database, *args, **kwargs)
        value = str(database)
        # Native snapshot/projection readers are explicitly mode=ro or memory.
        # They own their independent close lifecycle and are not fixture writes.
        if value != ":memory:" and not (kwargs.get("uri") and "mode=ro" in value):
            self._writers.append(connection)
        return connection

    def quiesce(self, *, require_committed=True):
        if threading.get_ident() != self._thread:
            raise ValueError("FIXTURE_WRITER_THREAD_CUSTODY_REQUIRED")
        writers, self._writers = self._writers, []
        uncommitted = False
        for connection in writers:
            try:
                uncommitted |= connection.in_transaction
            except sqlite3.ProgrammingError:
                # Writers using closing() are already explicitly quiescent.
                continue
            finally:
                connection.close()
            self.closed_writers += 1
        self.quiescent_boundaries += 1
        if require_committed and uncommitted:
            raise ValueError("FIXTURE_UNCOMMITTED_WRITER")


@contextmanager
def fixture_sqlite_writers():
    lifecycle = FixtureWriterLifecycle(sqlite3.connect)
    with patch.object(sqlite3, "connect", lifecycle.connect):
        try:
            yield lifecycle
        except BaseException:
            lifecycle.quiesce(require_committed=False)
            raise
        else:
            lifecycle.quiesce()


def fixture_write(function, *args, **kwargs):
    """One native fixture producer call, closed before its caller continues."""
    with fixture_sqlite_writers():
        return function(*args, **kwargs)
