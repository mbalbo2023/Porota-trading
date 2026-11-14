"""A651: fail-closed PR status check for the exact Predeploy V2 artifact.

GitHub considers a skipped job successful for required-status purposes.  This
pure, OFFHOST guard is run by a separate non-skippable product-PR job, after the
existing exact artifact gate.  All evidence inputs are from the *same* Actions
run, not an artifact with a matching name found in another run.  This check is
not owner GO, deployment permission, or evidence of runtime PAPER safety.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Mapping


REPO = "mbalbo2023/Porota-trading"
PRODUCT = "deploy/rc6-pr69-isolated-20260915"
FIELDS = frozenset({
    "RC6_RELEASE_REPO",
    "RC6_RELEASE_BASE_REF",
    "RC6_RELEASE_PR_DRAFT",
    "RC6_RELEASE_HEAD_SHA",
    "RC6_RELEASE_CHECKOUT_SHA",
    "RC6_RELEASE_GATE_RESULT",
    "RC6_RELEASE_ARTIFACT_SHA",
    "RC6_RELEASE_ARTIFACT_NAME",
    "RC6_RELEASE_ARTIFACT_ID",
    "RC6_RELEASE_ARTIFACT_DIGEST",
})


class RequiredReleaseEvidenceRed(ValueError):
    """Do not expose the potentially untrusted value that caused rejection."""


def _red() -> None:
    raise RequiredReleaseEvidenceRed("RC6_REQUIRED_RELEASE_EVIDENCE_RED")


def verify(data: Mapping[str, str]) -> dict:
    """Verify only CI artifact provenance; never establish operational GO."""
    if not isinstance(data, Mapping) or set(data) != FIELDS or any(
            not isinstance(value, str) for value in data.values()):
        _red()
    head = data["RC6_RELEASE_HEAD_SHA"]
    if (data["RC6_RELEASE_REPO"] != REPO or
            data["RC6_RELEASE_BASE_REF"] != PRODUCT or
            data["RC6_RELEASE_PR_DRAFT"] != "false" or
            data["RC6_RELEASE_GATE_RESULT"] != "success" or
            re.fullmatch(r"[0-9a-f]{40}", head) is None or
            data["RC6_RELEASE_CHECKOUT_SHA"] != head or
            data["RC6_RELEASE_ARTIFACT_SHA"] != head or
            data["RC6_RELEASE_ARTIFACT_NAME"] != f"porota-predeploy-v2-{head}"):
        _red()
    artifact_id = data["RC6_RELEASE_ARTIFACT_ID"]
    if re.fullmatch(r"[1-9][0-9]{0,16}", artifact_id) is None:
        _red()
    digest = data["RC6_RELEASE_ARTIFACT_DIGEST"]
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        _red()
    return {
        "schema_version": 1,
        "status": "REQUIRED_EXACT_ARTIFACT_CI_EVIDENCE_PRESENT",
        "candidate_sha": head,
        "artifact_id": int(artifact_id),
        "artifact_digest": digest,
        "artifact_contents_verified_by_this_guard": False,
        "owner_go_verified": False,
        "runtime_verified": False,
        "automatic_go": False,
        "auto_merge": False,
        "auto_deploy": False,
        "auto_trade": False,
    }


def main() -> int:
    try:
        evidence = verify({key: os.environ.get(key, "") for key in FIELDS})
    except RequiredReleaseEvidenceRed:
        print("RC6_REQUIRED_RELEASE_EVIDENCE=RED|automatic_go=NO", file=sys.stderr)
        return 2
    print("RC6_REQUIRED_RELEASE_EVIDENCE=GREEN|candidate=" +
          evidence["candidate_sha"] + "|artifact_id=" +
          str(evidence["artifact_id"]) + "|automatic_go=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
