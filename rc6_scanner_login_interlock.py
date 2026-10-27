"""Durable scanner login spacing independent of the PAPER SQLite database.

The observer's /app/data artifact directory is bind mounted across scanner and
container restarts. A failed file reservation denies the login; it never falls
back to an in-memory timer or a provider probe.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

from cg_paper_workspace import artifact_root

_MARKER = "scanner-login-interlock-v1.json"
_LOCK = "scanner-login-interlock-v1.lock"
_SCHEMA = "RC6_SCANNER_LOGIN_INTERLOCK_V1"
_NS = 1_000_000_000


def _boot_id():
    value = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value):
        raise ValueError("PPI_LOGIN_BOOT_ID_UNAVAILABLE")
    return value


def _regular_private(fd):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
            info.st_uid != os.geteuid() or info.st_mode & 0o077):
        raise ValueError("PPI_LOGIN_INTERLOCK_FILE_UNTRUSTED")


class ScannerLoginInterlock:
    """One durable reservation before each scanner POST login.

    An absent marker or a different host boot starts a full quarantine window.
    Corrupted evidence or unavailable storage denies login. Wall time is never
    used to decide expiry.
    """

    def __init__(self, database, cooldown_seconds, *, monotonic_ns=None, boot_id=None):
        self.root = artifact_root(database)
        self.cooldown_ns = max(300, int(cooldown_seconds)) * _NS
        self.monotonic_ns = monotonic_ns or time.monotonic_ns
        self.boot_id = boot_id or _boot_id

    def _read(self, directory):
        try:
            fd = os.open(_MARKER, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                         dir_fd=directory)
        except FileNotFoundError:
            return None
        try:
            _regular_private(fd)
            raw = os.read(fd, 513)
            if len(raw) > 512:
                raise ValueError("PPI_LOGIN_INTERLOCK_OVERSIZE")
            item = json.loads(raw)
            if (type(item) is not dict or set(item) !=
                    {"schema", "kind", "boot_id", "monotonic_ns", "cooldown_ns"} or
                    item["schema"] != _SCHEMA or
                    item["kind"] not in ("QUARANTINE", "ATTEMPT") or
                    not isinstance(item["boot_id"], str) or
                    not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",
                                     item["boot_id"]) or
                    type(item["monotonic_ns"]) is not int or item["monotonic_ns"] < 0 or
                    type(item["cooldown_ns"]) is not int or
                    item["cooldown_ns"] < 300 * _NS):
                raise ValueError("PPI_LOGIN_INTERLOCK_INVALID")
            return item
        finally:
            os.close(fd)

    def _write(self, directory, kind, boot, tick):
        payload = json.dumps({"schema": _SCHEMA, "kind": kind, "boot_id": boot,
                              "monotonic_ns": tick, "cooldown_ns": self.cooldown_ns},
                             sort_keys=True, separators=(",", ":")).encode()
        if len(payload) > 512:
            raise ValueError("PPI_LOGIN_INTERLOCK_OVERSIZE")
        temporary = f".{_MARKER}.{uuid.uuid4().hex}.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
        try:
            _regular_private(fd)
            with os.fdopen(fd, "wb") as handle:
                fd = -1
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, _MARKER, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            if fd != -1:
                os.close(fd)
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass

    def reserve(self):
        """Return ALLOWED only after a durable claim; all errors deny network."""
        try:
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY |
                                os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                info = os.fstat(directory)
                if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o002:
                    raise ValueError("PPI_LOGIN_INTERLOCK_DIRECTORY_UNTRUSTED")
                lock = os.open(_LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW |
                               os.O_CLOEXEC, 0o600, dir_fd=directory)
                try:
                    _regular_private(lock)
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    if os.stat(_LOCK, dir_fd=directory, follow_symlinks=False).st_ino != os.fstat(lock).st_ino:
                        raise ValueError("PPI_LOGIN_INTERLOCK_LOCK_REPLACED")
                    boot, tick = self.boot_id(), self.monotonic_ns()
                    if type(tick) is not int or tick < 0:
                        raise ValueError("PPI_LOGIN_INTERLOCK_CLOCK_INVALID")
                    previous = self._read(directory)
                    if previous is None:
                        self._write(directory, "QUARANTINE", boot, tick)
                        return "COOLDOWN"
                    if previous["boot_id"] != boot:
                        # The previous boot's clock cannot be compared. Waiting
                        # a full window from this observation is conservative
                        # even when the reboot happened right after a POST.
                        self._write(directory, "QUARANTINE", boot, tick)
                        return "COOLDOWN"
                    if tick < previous["monotonic_ns"]:
                        return "UNAVAILABLE"
                    if tick - previous["monotonic_ns"] < max(
                            self.cooldown_ns, previous["cooldown_ns"]):
                        return "COOLDOWN"
                    self._write(directory, "ATTEMPT", boot, tick)
                    return "ALLOWED"
                finally:
                    os.close(lock)
            finally:
                os.close(directory)
        except (OSError, ValueError, TypeError, UnicodeError):
            return "UNAVAILABLE"
