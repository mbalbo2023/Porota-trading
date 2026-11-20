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

from scripts.rc6_ci_shard_quorum import manifest


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
