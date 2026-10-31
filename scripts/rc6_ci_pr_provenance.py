"""A222 offline exact PR provenance guard for a future Predeploy step.

Validates only the candidate's GitHub event identity.  It does not determine
merge authorization, SHA/tree equivalence, owner GO or runtime readiness.
No event body, comments, user identity or credentials are copied to output.
"""

from __future__ import annotations

import re


BASE = "deploy/rc6-pr69-isolated-20260915"
REPO = "mbalbo2023/Porota-trading"
ACTIONS = frozenset({"opened", "synchronize", "reopened", "ready_for_review"})


class PRProvenanceRed(ValueError):
    pass


def _red() -> None:
    raise PRProvenanceRed("RC6_PR_PROVENANCE_RED")


def verify(event: object, *, expected_sha: str, repo: str = REPO,
           base: str = BASE) -> dict:
    if (not isinstance(event, dict) or not isinstance(expected_sha, str) or
            re.fullmatch(r"[0-9a-f]{40}", expected_sha) is None):
        _red()
    if not isinstance(repo, str) or not isinstance(base, str):
        _red()
    try:
        request = event["pull_request"]
        repository = event["repository"]
        branch = request["base"]
        head = request["head"]
        pr_number = request["number"]
        action = event["action"]
        observed_repo = repository["full_name"]
        observed_head_repo = head["repo"]["full_name"]
        observed_sha = head["sha"]
        observed_base_sha = branch["sha"]
        observed_base = branch["ref"]
        draft = request["draft"]
        merged = request["merged"]
        state = request["state"]
    except (KeyError, TypeError, AttributeError):
        _red()
    if (observed_repo != repo or observed_head_repo != repo or
            observed_base != base or observed_sha != expected_sha or
            not isinstance(observed_base_sha, str) or
            re.fullmatch(r"[0-9a-f]{40}", observed_base_sha) is None or
            type(pr_number) is not int or pr_number <= 0 or
            not isinstance(action, str) or action not in ACTIONS or
            draft is not False or merged is not False or
            state != "open"):
        _red()
    return {"schema_version": 1, "status": "PR_SOURCE_IDENTITY_VERIFIED",
            "candidate_sha": expected_sha, "base_sha": observed_base_sha,
            "base_branch": base, "pr_number": pr_number,
            "owner_go_verified": False, "tree_verified": False,
            "artifact_verified": False, "runtime_verified": False,
            "automatic_go": False}
