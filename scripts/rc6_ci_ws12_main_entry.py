"""A1004: fail-closed WS12 admission, sourced only from protected main.

This is deliberately NOT an OCI runner, test gate, or a QA certificate.
Until an independent image, supervisor, and publisher are validated in real
GitHub Actions, the only possible process exit status is HOLD=79.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping


HOLD = 79
WORKFLOW_REF = (
    "mbalbo2023/Porota-trading/.github/workflows/"
    "rc6-ws-tests-12-current-contract.yml@refs/heads/main"
)
HEX_SHA = re.compile(r"[0-9a-f]{40}\Z")


def context_is_main_dispatch(context: Mapping[str, str]) -> bool:
    """Validate only trusted runner context; candidate values remain data."""
    expected = {
        "GITHUB_REPOSITORY": "mbalbo2023/Porota-trading",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_PROTECTED": "true",
        "GITHUB_WORKFLOW_REF": WORKFLOW_REF,
        "RC6_RUNNER_ENVIRONMENT": "github-hosted",
    }
    if any(context.get(key) != value for key, value in expected.items()):
        return False
    main_sha = context.get("GITHUB_SHA", "")
    workflow_sha = context.get("GITHUB_WORKFLOW_SHA", "")
    candidate_sha = context.get("RC6_CANDIDATE_SHA", "")
    candidate_tree = context.get("RC6_CANDIDATE_TREE_SHA", "")
    if not all(isinstance(value, str) and HEX_SHA.fullmatch(value) for value in (
        main_sha, workflow_sha, candidate_sha, candidate_tree,
    )):
        return False
    if workflow_sha != main_sha or candidate_sha == main_sha:
        return False
    for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        number = context.get(key, "")
        if not isinstance(number, str) or not re.fullmatch(r"[0-9]{1,20}", number):
            return False
        if int(number) <= 0:
            return False
    return True


def main(context: Mapping[str, str] | None = None) -> int:
    # Never print source strings or environment values in public Actions logs.
    good = context_is_main_dispatch(os.environ if context is None else context)
    status = "MAIN_CONTEXT_VERIFIED_HOLD_NO_OCI" if good else "UNTRUSTED_CONTEXT"
    print(f"RC6_WS12_CONTROL={status}|qa_certified=false|automatic_go=false")
    return HOLD


if __name__ == "__main__":
    raise SystemExit(main())
