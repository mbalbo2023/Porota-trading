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
_progress = None
_unexpected_xpass = False


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
        self.started = time.monotonic()

    def append(self, nodeid, phase, outcome):
        if (phase not in {"start", "setup", "call", "teardown", "finish"} or
                outcome not in {"none", "passed", "failed", "skipped"}):
            raise RuntimeError("RC6_PYTEST_PROGRESS_INVALID")
        value = {**self.binding, "sequence": self.sequence,
                 "nodeid_sha256": nodeid_digest(nodeid), "phase": phase,
                 "outcome": outcome,
                 "elapsed_ms": round((time.monotonic() - self.started) * 1000)}
        current = os.stat(self.path, follow_symlinks=False)
        owned = os.fstat(self.fd)
        if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or owned.st_nlink != 1 or
                (current.st_dev, current.st_ino) != (self.identity.st_dev, self.identity.st_ino)):
            raise RuntimeError("RC6_PYTEST_PROGRESS_TARGET_CHANGED")
        raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
        if os.write(self.fd, raw) != len(raw):
            raise RuntimeError("RC6_PYTEST_PROGRESS_SHORT_WRITE")
        os.fsync(self.fd)
        self.sequence += 1

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __del__(self):
        self.close()


def pytest_sessionstart(session):
    global _progress, _unexpected_xpass
    if _progress is not None:
        _progress.close()
    _progress = None
    _unexpected_xpass = False
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
    # The marker's strict=False overrides pytest's global xfail_strict=true.
    # If any other reporter consumed the original XPASS before our hook, the
    # session still cannot return zero and turn an XPASS into a GREEN receipt.
    if _enabled() and _unexpected_xpass and session.exitstatus == 0:
        session.exitstatus = 1


def pytest_runtest_logstart(nodeid, location):
    if _progress is not None:
        _progress.append(nodeid, "start", "none")


def pytest_runtest_logreport(report):
    global _unexpected_xpass
    if (_enabled() and getattr(report, "when", None) == "call" and
            getattr(report, "passed", False) and hasattr(report, "wasxfail")):
        # xfail(strict=False) may produce PASSED+wasxfail with pytest rc=0.
        # Reclassify the *real* pytest TestReport before JUnit/terminal sinks;
        # never emit the original exception, node ID or fixture content.
        report.outcome = "failed"
        report.longrepr = "RC6_CI_UNEXPECTED_XPASS"
        del report.wasxfail
        _unexpected_xpass = True
    if _progress is not None:
        _progress.append(report.nodeid, report.when, report.outcome)


def pytest_runtest_logfinish(nodeid, location):
    if _progress is not None:
        _progress.append(nodeid, "finish", "none")


def _enabled() -> bool:
    return os.environ.get("RC6_CI_IDENTITY_PROOF") == "1"


def pytest_configure(config):
    """Guard TestReport before other reporters can mistake XPASS for PASS.

    Import pytest only inside its own hook, retaining this module's existing
    no-pytest import contract for receipt reducers without test dependencies.
    """
    if not _enabled():
        return
    import pytest
    # --runxfail can arrive from PYTEST_ADDOPTS and suppress both JUnit xfail
    # and TestReport.wasxfail. Refuse that process before collecting anything.
    if getattr(config.option, "runxfail", False):
        raise pytest.UsageError("RC6_CI_RUNXFAIL_FORBIDDEN")
    if config.pluginmanager.hasplugin("rc6-ci-xpass-guard"):
        return

    class XPassGuard:
        @pytest.hookimpl(hookwrapper=True, tryfirst=True)
        def pytest_runtest_makereport(self, item, call):
            global _unexpected_xpass
            result = yield
            report = result.get_result()
            if (report.when == "call" and report.passed and
                    hasattr(report, "wasxfail")):
                report.outcome = "failed"
                report.longrepr = "RC6_CI_UNEXPECTED_XPASS"
                del report.wasxfail
                _unexpected_xpass = True

    config.pluginmanager.register(XPassGuard(), name="rc6-ci-xpass-guard")


def nodeid_digest(nodeid: str) -> str:
    return hashlib.sha256(nodeid.encode("utf-8")).hexdigest()


def pytest_collection_modifyitems(session, config, items):
    if not _enabled():
        return
    for item in items:
        # The governed FULL suite requires every test to execute normally.
        # xfail(strict=False) overrides global strictness, and --runxfail
        # suppresses evidence entirely. The latter is denied at configure;
        # static xfail markings (including strict=False) are denied here.
        if any(item.iter_markers(name="xfail")):
            import pytest
            raise pytest.UsageError("RC6_CI_XFAIL_MARKER_FORBIDDEN")
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
