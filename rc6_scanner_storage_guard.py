"""Keep a PAPER scanner fail-closed on confirmed local storage exhaustion.

There are no database, network, Docker, or systemd operations in this guard.
The scanner is allowed to retry its normal startup only after a bounded,
interruptible quiet period; a full disk never becomes a successful heartbeat.
"""

from __future__ import annotations

import errno
import sqlite3
import time
from typing import Callable


STORAGE_QUIET_SECONDS = 900
STOP_POLL_SECONDS = 5


def storage_exhaustion_kind(error: BaseException) -> str | None:
    """Classify only machine-readable FULL/ENOSPC, never an error message."""
    if isinstance(error, sqlite3.Error):
        code = getattr(error, "sqlite_errorcode", None)
        if type(code) is int and code & 0xFF == sqlite3.SQLITE_FULL:
            return "SQLITE_FULL"
    if isinstance(error, OSError) and error.errno == errno.ENOSPC:
        return "ENOSPC"
    return None


def scanner_with_storage_quarantine(
    run: Callable[[], int],
    *,
    stopped: Callable[[], bool],
    emit: Callable[[dict], None],
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    quiet_seconds: int = STORAGE_QUIET_SECONDS,
) -> int:
    """Prevent a Docker restart storm while disk/SQLite remains exhausted.

    The normal scanner establishes its own signal handler. It owns settlement,
    PAPER admission, and persistent state; this emergency wrapper attempts no
    accounting action and cannot silently convert a failed write into success.
    An unrelated exception propagates unchanged for incident diagnosis.
    """
    if type(quiet_seconds) is not int or quiet_seconds < 300:
        raise ValueError("SCANNER_STORAGE_QUIET_PERIOD_UNSAFE")
    while not stopped():
        try:
            return run()
        except (sqlite3.Error, OSError) as exc:
            kind = storage_exhaustion_kind(exc)
            if kind is None:
                raise
            # Only allowlisted fields may be logged. Raw SQLite exception text
            # can contain SQL, provider symbols, or accidental private data.
            try:
                emit({"event": "SCANNER_STORAGE_QUARANTINE",
                      "state": "DEGRADED_NO_DB_WRITE",
                      "cause": kind,
                      "retry_after_seconds": quiet_seconds,
                      "real_orders_sent": 0})
            except OSError:
                # stderr/journald may also be full; log failure must not
                # re-enable immediate restarts or trigger alternate writes.
                pass
            deadline = monotonic() + quiet_seconds
            while not stopped():
                remaining = deadline - monotonic()
                if remaining <= 0:
                    break
                sleep(min(STOP_POLL_SECONDS, remaining))
    return 0
