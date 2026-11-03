"""Regression: repeated SQLITE_FULL never causes an immediate scanner exit."""

import errno
import sqlite3

import pytest

from rc6_scanner_storage_guard import (
    scanner_with_storage_quarantine, storage_exhaustion_kind,
)


def sqlite_error(code):
    error = sqlite3.OperationalError("private details must not be logged")
    error.sqlite_errorcode = code
    return error


def test_only_machine_readable_storage_exhaustion_is_quarantined():
    assert storage_exhaustion_kind(sqlite_error(sqlite3.SQLITE_FULL)) == "SQLITE_FULL"
    assert storage_exhaustion_kind(sqlite_error(sqlite3.SQLITE_FULL | 0x100)) == "SQLITE_FULL"
    assert storage_exhaustion_kind(OSError(errno.ENOSPC, "full")) == "ENOSPC"
    assert storage_exhaustion_kind(sqlite_error(sqlite3.SQLITE_BUSY)) is None
    assert storage_exhaustion_kind(sqlite_error(sqlite3.SQLITE_CORRUPT)) is None
    assert storage_exhaustion_kind(sqlite3.OperationalError("database or disk is full")) is None
    assert storage_exhaustion_kind(OSError(errno.EIO, "I/O failure")) is None


def test_repeated_full_keeps_single_process_alive_with_bounded_retries():
    clock = [0.0]
    calls = []
    delays = []
    events = []

    def run():
        calls.append(clock[0])
        if len(calls) <= 2:
            raise sqlite_error(sqlite3.SQLITE_FULL)
        return 0

    def sleep(seconds):
        assert 0 < seconds <= 5
        delays.append(seconds)
        clock[0] += seconds

    result = scanner_with_storage_quarantine(
        run, stopped=lambda: False, emit=events.append,
        monotonic=lambda: clock[0], sleep=sleep)
    assert result == 0
    assert calls == [0.0, 900.0, 1800.0]
    assert sum(delays) == 1800
    assert len(events) == 2
    assert all(event == {
        "event": "SCANNER_STORAGE_QUARANTINE", "state": "DEGRADED_NO_DB_WRITE",
        "cause": "SQLITE_FULL", "retry_after_seconds": 900,
        "real_orders_sent": 0} for event in events)


def test_shutdown_during_quarantine_does_not_restart_scanner():
    stopped = [False]
    calls = []
    sleeps = []

    def run():
        calls.append(1)
        raise OSError(errno.ENOSPC, "private path")

    def sleep(seconds):
        sleeps.append(seconds)
        stopped[0] = True

    events = []
    assert scanner_with_storage_quarantine(
        run, stopped=lambda: stopped[0], emit=events.append,
        monotonic=lambda: 0, sleep=sleep) == 0
    assert len(calls) == 1
    assert sleeps == [5]
    assert events[0]["cause"] == "ENOSPC"


def test_unrelated_errors_propagate_without_wait_or_forged_success():
    delays = []

    def run():
        raise sqlite_error(sqlite3.SQLITE_CORRUPT)

    with pytest.raises(sqlite3.OperationalError):
        scanner_with_storage_quarantine(
            run, stopped=lambda: False, emit=lambda _: None,
            sleep=delays.append)
    assert delays == []


def test_bad_quiet_window_is_rejected():
    with pytest.raises(ValueError, match="QUIET_PERIOD_UNSAFE"):
        scanner_with_storage_quarantine(
            lambda: 0, stopped=lambda: False, emit=lambda _: None,
            quiet_seconds=0)


def test_full_stderr_does_not_cause_restart_storm():
    stopped = [False]
    calls = []

    def run():
        calls.append(1)
        raise sqlite_error(sqlite3.SQLITE_FULL)

    def emit(_):
        raise OSError(errno.ENOSPC, "journald full")

    assert scanner_with_storage_quarantine(
        run, stopped=lambda: stopped[0], emit=emit,
        monotonic=lambda: 0, sleep=lambda _: stopped.__setitem__(0, True)) == 0
    assert calls == [1]
