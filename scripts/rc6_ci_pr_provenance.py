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
# A229: one candidate-scoped *diagnostic* pilot, never a deployment trigger.
PILOT_PR_NUMBER = 776
PILOT_HEAD_REF = "candidate/rc6-a224-consolidated-after-775-20261031"


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


def select_parallel_pilot(event: object, *, expected_sha: str,
                          operator_opt_in: str = "") -> dict:
    """Permit only PR #776 exact branch or a deliberate repository variable.

    The already-authoritative sequential suite stays mandatory. This receipt
    never verifies a tree, authorizes an owner GO, or promotes a runtime.
    """
    base = verify(event, expected_sha=expected_sha)
    if type(operator_opt_in) is not str or operator_opt_in not in ("", "0", "1"):
        _red()
    try:
        request = event["pull_request"]
        head_ref = request["head"]["ref"]
        head_repo = request["head"]["repo"]["full_name"]
        base_repo = request["base"]["repo"]["full_name"]
    except (KeyError, TypeError, AttributeError):
        _red()
    if (not isinstance(head_ref, str) or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", head_ref) is None or
            base_repo != REPO or head_repo != REPO):
        _red()
    selected = (base["pr_number"] == PILOT_PR_NUMBER and
                head_ref == PILOT_HEAD_REF)
    if not selected and operator_opt_in != "1":
        _red()
    return {"schema_version": 1, "status": "PARALLEL_DIAGNOSTIC_ONLY",
            "selection": "EXACT_PR776_BRANCH" if selected else "REPOSITORY_OPT_IN",
            "candidate_sha": base["candidate_sha"],
            "base_sha": base["base_sha"],
            "pr_number": base["pr_number"],
            "owner_go_verified": False, "automatic_go": False,
            "runtime_authority": False, "tree_verified": False}
