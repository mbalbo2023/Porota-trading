"""Test candidate exclusion shared by Predeploy, bundle, and Promote V2."""
from __future__ import annotations

import subprocess


TESTING_ONLY_MARKER = "ops/policy/rc6-iol-testing-only.json"
RELEASE_BLOCKED = "TESTING_ONLY_PROMOTION_BLOCKED"


def require_release_scope(paths):
    if TESTING_ONLY_MARKER in paths:
        raise ValueError(RELEASE_BLOCKED)


def require_release_tree(repository, revision):
    marker = subprocess.check_output(
        ["git", "-C", str(repository), "ls-tree", "--name-only", revision, "--", TESTING_ONLY_MARKER],
        stderr=subprocess.DEVNULL,
        text=True,
    )
    require_release_scope(marker.splitlines())
