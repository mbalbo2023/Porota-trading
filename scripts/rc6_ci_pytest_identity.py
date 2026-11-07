"""Pytest hook: attest exact collected node IDs in JUnit without publishing IDs.

Opt-in with RC6_CI_IDENTITY_PROOF=1, RC6_CI_SOURCE_SHA,
RC6_CI_SOURCE_TREE_SHA and RC6_CI_MANIFEST_PATH.  The private manifest carries
nodeids for governed partitioning; public receipts must contain only their
collection digest/count and per-test SHA-256 JUnit properties.

This module has no pytest dependency at import time and does not alter tests
unless the explicit proof environment is enabled.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time

from scripts.rc6_ci_shard_quorum import manifest, _sha


PROPERTY = "rc6_nodeid_sha256"
# Shared producer/reader limits. A pytest node may emit multiple unittest
# subTest call reports but never earns additional testcase credit.
MAX_PROGRESS_CALL_REPORTS = 64
MAX_PROGRESS_BYTES = 8 * 1024 * 1024
_progress = None


class _ProgressWriter:
    """Explicit diagnostic-only, fsynced hash events; no report text or paths."""

    def __init__(self, path, source_sha, source_tree_sha, segment_sha256):
        self.fd = None
        self.binding = {"source_sha": _sha(source_sha),
                        "source_tree_sha": _sha(source_tree_sha),
                        "segment_sha256": _sha(segment_sha256, 64)}
        self.path = Path(path)
        self.fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT |
                          os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        self.identity = os.fstat(self.fd)
        self.sequence = 0
        # Freeze the actual clock function before tests begin. Patching a
        # dependency's ``time.monotonic`` mutates Python's shared time module.
        self._clock = time.monotonic
        self.started = self._clock()
        self._max_call_reports = MAX_PROGRESS_CALL_REPORTS
        self._max_bytes = MAX_PROGRESS_BYTES
        self._bytes_written = 0
        self._active_node = None
        self._calls_for_active = 0

    def append(self, nodeid, phase, outcome):
        if (phase not in {"start", "setup", "call", "teardown", "finish"} or
                outcome not in {"none", "passed", "failed", "skipped"}):
            raise RuntimeError("RC6_PYTEST_PROGRESS_INVALID")
        if phase == "start":
            if self._active_node is not None:
                raise RuntimeError("RC6_PYTEST_PROGRESS_OVERLAPPING_NODE")
            self._active_node = nodeid
            self._calls_for_active = 0
        elif phase == "call":
            if self._active_node != nodeid or self._calls_for_active >= self._max_call_reports:
                raise RuntimeError("RC6_PYTEST_PROGRESS_SUBTEST_LIMIT")
            self._calls_for_active += 1
        elif phase == "finish":
            if self._active_node != nodeid:
                raise RuntimeError("RC6_PYTEST_PROGRESS_FOREIGN_FINISH")
            self._active_node = None
        value = {**self.binding, "sequence": self.sequence,
                 "nodeid_sha256": nodeid_digest(nodeid), "phase": phase,
                 "outcome": outcome,
                 "elapsed_ms": round((self._clock() - self.started) * 1000)}
        current = os.stat(self.path, follow_symlinks=False)
        owned = os.fstat(self.fd)
        if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or owned.st_nlink != 1 or
                (current.st_dev, current.st_ino) != (self.identity.st_dev, self.identity.st_ino)):
            raise RuntimeError("RC6_PYTEST_PROGRESS_TARGET_CHANGED")
        raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
        if self._bytes_written + len(raw) > self._max_bytes:
            raise RuntimeError("RC6_PYTEST_PROGRESS_SIZE_LIMIT")
        if os.write(self.fd, raw) != len(raw):
            raise RuntimeError("RC6_PYTEST_PROGRESS_SHORT_WRITE")
        os.fsync(self.fd)
        self._bytes_written += len(raw)
        self.sequence += 1

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __del__(self):
        self.close()


def pytest_sessionstart(session):
    global _progress
    if _progress is not None:
        _progress.close()
    _progress = None
    path = os.environ.get("RC6_CI_PROGRESS_PATH")
    if _enabled() and path and os.environ.get("RC6_CI_PROGRESS_PID") == str(os.getpid()):
        _progress = _ProgressWriter(path, os.environ.get("RC6_CI_SOURCE_SHA", ""),
            os.environ.get("RC6_CI_SOURCE_TREE_SHA", ""),
            os.environ.get("RC6_CI_SEGMENT_SHA256", ""))


def pytest_sessionfinish(session, exitstatus):
    global _progress
    if _progress is not None:
        _progress.close()
        _progress = None


def pytest_runtest_logstart(nodeid, location):
    if _progress is not None:
        _progress.append(nodeid, "start", "none")


def pytest_runtest_logreport(report):
    if _progress is not None:
        _progress.append(report.nodeid, report.when, report.outcome)


def pytest_runtest_logfinish(nodeid, location):
    if _progress is not None:
        _progress.append(nodeid, "finish", "none")


def _enabled() -> bool:
    return os.environ.get("RC6_CI_IDENTITY_PROOF") == "1"


def nodeid_digest(nodeid: str) -> str:
    return hashlib.sha256(nodeid.encode("utf-8")).hexdigest()


def pytest_collection_modifyitems(session, config, items):
    if not _enabled():
        return
    for item in items:
        digest = nodeid_digest(item.nodeid)
        present = [value for name, value in item.user_properties if name == PROPERTY]
        if present:
            if present != [digest]:
                raise RuntimeError("RC6_PYTEST_NODEID_PROPERTY_CONFLICT")
            continue
        item.user_properties.append((PROPERTY, digest))


def pytest_collection_finish(session):
    if not _enabled():
        return
    path_text = os.environ.get("RC6_CI_MANIFEST_PATH")
    if not path_text:
        raise RuntimeError("RC6_PYTEST_MANIFEST_PATH_REQUIRED")
    candidate = os.environ.get("RC6_CI_SOURCE_SHA", "")
    tree = os.environ.get("RC6_CI_SOURCE_TREE_SHA", "")
    proof = manifest(candidate, tree, [item.nodeid for item in session.items])
    if _progress is not None and proof["collection_sha256"] != _progress.binding["segment_sha256"]:
        raise RuntimeError("RC6_PYTEST_PROGRESS_COLLECTION_MISMATCH")
    path = Path(path_text)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError("RC6_PYTEST_MANIFEST_TARGET_UNSAFE")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=path.parent, prefix=".rc6-pytest-",
                                         delete=False) as handle:
            temp_path = Path(handle.name)
            os.chmod(temp_path, 0o600)
            json.dump(proof, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
