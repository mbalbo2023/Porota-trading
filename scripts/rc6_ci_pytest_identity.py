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
import re
import sys
from pathlib import Path
import tempfile

from scripts.rc6_ci_shard_quorum import manifest, ShardQuorumRed, _NODEID_REASON_CODES


PROPERTY = "rc6_nodeid_sha256"
_LAST_ACTIVE_MODULE = None


def _enabled() -> bool:
    return os.environ.get("RC6_CI_IDENTITY_PROOF") == "1"


def nodeid_digest(nodeid: str) -> str:
    return hashlib.sha256(nodeid.encode("utf-8")).hexdigest()


def pytest_runtest_logstart(nodeid, location):
    """Private, opt-in per-module heartbeat; no parameter or nodeid bytes."""
    global _LAST_ACTIVE_MODULE
    if (not _enabled() or os.environ.get("RC6_CI_MODULE_TRACE") != "1"
            or not isinstance(location, tuple) or not location):
        return
    module = location[0]
    if (type(module) is not str
            or not re.fullmatch(r"tests/[A-Za-z0-9_./-]{1,220}\.py", module)
            or ".." in Path(module).parts or module == _LAST_ACTIVE_MODULE):
        return
    _LAST_ACTIVE_MODULE = module
    # The runner captures this stdout privately and publishes only a verified
    # source module path if pytest was killed or returned nonzero.
    sys.__stdout__.write("\nRC6_CI_ACTIVE_MODULE:" + module + "\n")
    sys.__stdout__.flush()



def pytest_internalerror(excrepr, excinfo):
    """Persist a PRIVATE sanitized pytest INTERNAL_ERROR cause, fail-closed.

    Never copy raw traceback, credentials, parameters or node IDs. The hook
    itself must not create another Python exception during emergency handling.
    """
    target = os.environ.get("RC6_CI_INTERNAL_ERROR_PATH", "")
    if not (_enabled() and target):
        return
    staged = None
    try:
        name = getattr(getattr(excinfo, "type", None), "__name__", "")
        if (type(name) is not str or
                not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", name)):
            name = "UNVERIFIED"
        receipt = {"schema": "rc6.pytest.internalerror.v1",
                   "exception_type": name}
        # Reuse an existing fixed reason identifier, not arbitrary exception
        # text or private nodeid values. Do not relax any length/UTF8 rule.
        error = getattr(excinfo, "value", None)
        if isinstance(error, ShardQuorumRed):
            marker = "RC6_SHARD_QUORUM_RED:"
            msg = str(error)
            if msg.startswith(marker):
                code = msg[len(marker):]
                if code in _NODEID_REASON_CODES:
                    receipt["quorum_reason"] = code
        root = Path.cwd().resolve(strict=True)
        tb = getattr(getattr(excinfo, "value", None), "__traceback__", None)
        for _ in range(128):
            if tb is None:
                break
            filename = Path(tb.tb_frame.f_code.co_filename)
            if filename.is_absolute():
                try:
                    relative = filename.resolve(strict=True).relative_to(root).as_posix()
                    parts = Path(relative).parts
                    if (re.fullmatch(r"[A-Za-z0-9_./-]{1,220}\.py", relative)
                            and ".." not in parts and (root / relative).is_file()
                            and not any((root / Path(*parts[:i])).is_symlink()
                                        for i in range(1, len(parts) + 1))
                            and 1 <= tb.tb_lineno <= 10000000):
                        receipt["source_file"] = relative
                        receipt["source_line"] = tb.tb_lineno
                except (OSError, ValueError, RuntimeError):
                    pass
            tb = tb.tb_next
        path = Path(target)
        if path.is_symlink() or path.parent.is_symlink() or not path.parent.is_dir():
            return
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=".rc6-internalerror-", delete=False) as output:
            staged = Path(output.name)
            os.chmod(staged, 0o600)
            json.dump(receipt, output, sort_keys=True, separators=(",", ":"))
            output.write("\n")
        os.replace(staged, path)
        staged = None
    except Exception:
        pass
    finally:
        if staged is not None:
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                pass

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
