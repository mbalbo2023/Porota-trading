"""R340: authenticated OFFHOST release gate for the existing R321 ROOT workflow.

This module uses R252/R163 to re-open the *current*, unique D/E release bytes.
R281's hard-pinned historical D/E IDs are intentionally not used. A verified
release is not an owner GO and confers NO ROOT, ENV_GENESIS or deploy authority.

R305 PIN, R321 INSTALL and R337 ENV seal remain separate owner/host gates.
No shell, SSH, sudo, secrets, Docker or runtime mutation is exposed here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, Mapping


REPO = "mbalbo2023/Porota-trading"
OWNER = "mbalbo2023"
PR_NUMBER = 1087
PRODUCT = "deploy/rc6-pr69-isolated-20260915"
_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ROOT = f"/repos/{REPO}"


class ControlHold(RuntimeError):
    """Fail closed without printing GitHub REST payloads or credentials."""

    exit_code = 79

    def __init__(self, reason: str):
        safe = reason if re.fullmatch(r"[A-Z][A-Z0-9_]{0,70}", reason) else "UNVERIFIED"
        self.reason = safe
        super().__init__("RC6_R340_HOLD79:" + safe)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ControlHold(reason)


def verify_authenticated_release(
    event: Mapping[str, str], *, checkout_sha: str, github: Any,
    trusted_release: Callable[..., Mapping[str, Any]],
) -> dict[str, Any]:
    """Bind actual trusted-main dispatch, live PR and R252 byte-checked D/E.

    `github` and `trusted_release` are injectable only for focal tests. The CLI
    constructs the authenticated R163 GitHub REST client and imports R252 from
    its exact trusted-main checkout; it never takes a release selector or a GO
    payload from workflow_dispatch inputs or its environment.
    """
    _require(isinstance(event, Mapping) and type(checkout_sha) is str and
             _SHA40.fullmatch(checkout_sha) is not None and
             event.get("GITHUB_EVENT_NAME") == "workflow_dispatch" and
             event.get("GITHUB_REPOSITORY") == REPO and
             event.get("GITHUB_REF") == "refs/heads/main" and
             event.get("GITHUB_ACTOR") == OWNER and
             event.get("GITHUB_TRIGGERING_ACTOR") == OWNER and
             event.get("GITHUB_SHA") == checkout_sha,
             "TRUSTED_MAIN_OWNER_DISPATCH_REQUIRED")
    getter = getattr(github, "get_json", None)
    _require(callable(getter) and callable(trusted_release),
             "AUTHENTICATED_GITHUB_REST_REQUIRED")

    def rest(path: str) -> Mapping[str, Any]:
        try:
            record = getter(path)
        except (ValueError, OSError, RuntimeError, TimeoutError, TypeError):
            raise ControlHold("GITHUB_REST_UNAVAILABLE") from None
        _require(isinstance(record, Mapping), "GITHUB_REST_INVALID")
        return record

    main_ref_path = _ROOT + "/git/ref/heads/main"
    main_commit_path = _ROOT + "/git/commits/" + checkout_sha
    pr_path = _ROOT + f"/pulls/{PR_NUMBER}"
    main_ref = rest(main_ref_path)
    _require(main_ref.get("ref") == "refs/heads/main" and
             isinstance(main_ref.get("object"), Mapping) and
             main_ref["object"].get("type") == "commit" and
             main_ref["object"].get("sha") == checkout_sha,
             "LIVE_MAIN_CHECKOUT_DRIFT")
    main_commit = rest(main_commit_path)
    _require(main_commit.get("sha") == checkout_sha and
             isinstance(main_commit.get("tree"), Mapping) and
             type(main_commit["tree"].get("sha")) is str and
             _SHA40.fullmatch(main_commit["tree"]["sha"]) is not None,
             "MAIN_TREE_NOT_AUTHENTICATED")

    pr = rest(pr_path)
    _require(type(pr.get("number")) is int and pr["number"] == PR_NUMBER and
             pr.get("draft") is False and pr.get("state") == "open" and
             pr.get("merged") is False and
             isinstance(pr.get("head"), Mapping) and
             type(pr["head"].get("sha")) is str and
             _SHA40.fullmatch(pr["head"]["sha"]) is not None and
             isinstance(pr.get("base"), Mapping) and
             pr["base"].get("ref") == PRODUCT,
             "SINGLE_CANDIDATE_PR_NOT_READY")
    candidate_sha = pr["head"]["sha"]
    candidate_commit_path = _ROOT + "/git/commits/" + candidate_sha
    candidate_commit = rest(candidate_commit_path)
    _require(candidate_commit.get("sha") == candidate_sha and
             isinstance(candidate_commit.get("tree"), Mapping) and
             type(candidate_commit["tree"].get("sha")) is str and
             _SHA40.fullmatch(candidate_commit["tree"]["sha"]) is not None,
             "CANDIDATE_TREE_NOT_AUTHENTICATED")
    candidate_tree = candidate_commit["tree"]["sha"]

    # R252 uniquely discovers E for the exact current main publisher and PR
    # HEAD, authenticates GitHub run/attempt and downloads/verifies D and E ZIP
    # bytes. Empty E selectors mean uniqueness rather than "take latest".
    selectors = {
        "RC6_BRIDGE_REPOSITORY": REPO,
        "RC6_BRIDGE_MODE": "PR_CANDIDATE",
        "RC6_BRIDGE_CANDIDATE_SHA": candidate_sha,
        "RC6_BRIDGE_CANDIDATE_TREE_SHA": candidate_tree,
        "RC6_BRIDGE_PUBLISHER_MAIN_SHA": checkout_sha,
        "RC6_BRIDGE_E_RUN_ID": "",
        "RC6_BRIDGE_E_RUN_ATTEMPT": "",
        "RC6_BRIDGE_E_ARTIFACT_ID": "",
        "RC6_BRIDGE_PRODUCT_SHA": "",
        "RC6_BRIDGE_PRODUCT_REF": "",
    }
    try:
        proof = trusted_release(selectors, github=github)
    except (ValueError, OSError, RuntimeError, TimeoutError, TypeError, KeyError):
        raise ControlHold("CURRENT_UNIQUE_D_E_BYTES_NOT_VERIFIED") from None
    expected = {
        "status": "TRUSTED_RELEASE_BYTES_BOUND",
        "mode": "PR_CANDIDATE",
        "candidate_sha": candidate_sha,
        "candidate_tree_sha": candidate_tree,
        "product_sha": "",
        "artifact_contents_verified_by_this_guard": True,
        "owner_go_verified": False,
        "runtime_verified": False,
        "automatic_go": False,
    }
    _require(isinstance(proof, Mapping) and
             set(proof) == set(expected) | {
                 "release_run_id", "release_run_attempt", "release_artifact_id",
                 "release_artifact_digest", "trusted_main_audit_run_id",
                 "trusted_main_audit_run_attempt",
             } and
             all(type(proof[key]) is type(value) and proof[key] == value
                 for key, value in expected.items()) and
             all(type(proof[key]) is int and proof[key] > 0 for key in (
                 "release_run_id", "release_run_attempt", "release_artifact_id",
                 "trusted_main_audit_run_id", "trusted_main_audit_run_attempt")) and
             type(proof["release_artifact_digest"]) is str and
             _DIGEST.fullmatch(proof["release_artifact_digest"]) is not None,
             "RELEASE_PROOF_FIELDS_NOT_EXACT")

    # Reopen every source after R252's artifact download (TOCTOU guard).
    # GitHub can alter mergeability, timestamps and other REST presentation
    # fields mid-download; only the authenticated Git/source identities are
    # immutable requirements, not entire REST response dictionaries.
    def ref_identity(record: Mapping[str, Any]) -> tuple[Any, ...]:
        obj = record.get("object")
        _require(isinstance(obj, Mapping), "GITHUB_HEAD_CHANGED_DURING_AUTH")
        return (record.get("ref"), obj.get("type"), obj.get("sha"))

    def commit_identity(record: Mapping[str, Any]) -> tuple[Any, ...]:
        tree = record.get("tree")
        _require(isinstance(tree, Mapping), "GITHUB_HEAD_CHANGED_DURING_AUTH")
        return (record.get("sha"), tree.get("sha"))

    def pr_identity(record: Mapping[str, Any]) -> tuple[Any, ...]:
        head, base = record.get("head"), record.get("base")
        _require(isinstance(head, Mapping) and isinstance(base, Mapping),
                 "GITHUB_HEAD_CHANGED_DURING_AUTH")

        def repo_identity(part: Mapping[str, Any]) -> Any:
            repo = part.get("repo")
            _require(repo is None or isinstance(repo, Mapping),
                     "GITHUB_HEAD_CHANGED_DURING_AUTH")
            return repo.get("full_name") if isinstance(repo, Mapping) else None

        return (
            record.get("number"), record.get("state"), record.get("draft"),
            record.get("merged"), head.get("sha"), head.get("ref"),
            repo_identity(head), base.get("sha"), base.get("ref"),
            repo_identity(base),
        )

    _require(ref_identity(rest(main_ref_path)) == ref_identity(main_ref) and
             commit_identity(rest(main_commit_path)) == commit_identity(main_commit) and
             pr_identity(rest(pr_path)) == pr_identity(pr) and
             commit_identity(rest(candidate_commit_path)) ==
             commit_identity(candidate_commit),
             "GITHUB_HEAD_CHANGED_DURING_AUTH")

    return {
        "status": "OFFHOST_D_E_BYTES_AUTHENTICATED",
        "publisher_main_sha": checkout_sha,
        "publisher_main_tree_sha": main_commit["tree"]["sha"],
        "candidate_sha": candidate_sha,
        "candidate_tree_sha": candidate_tree,
        "release_run_id": proof["release_run_id"],
        "release_artifact_id": proof["release_artifact_id"],
        "release_artifact_digest": proof["release_artifact_digest"],
        "trusted_main_audit_run_id": proof["trusted_main_audit_run_id"],
        "artifact_contents_verified_by_this_guard": True,
        "owner_go_verified": False,
        "host_control_verified": False,
        "root_env_genesis_verified": False,
        "deploy_authorized": False,
        "real_orders_allowed": False,
        "runtime_verified": False,
    }


def _checkout_head() -> str:
    root = Path(__file__).resolve().parents[1]
    try:
        proc = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=False, timeout=10)
    except (OSError, TimeoutError, subprocess.TimeoutExpired):
        raise ControlHold("CHECKOUT_SHA_UNAVAILABLE") from None
    head = proc.stdout.strip()
    _require(proc.returncode == 0 and _SHA40.fullmatch(head) is not None,
             "CHECKOUT_SHA_UNAVAILABLE")
    return head


def main(argv: list[str] | None = None) -> int:
    options = sys.argv[1:] if argv is None else argv
    try:
        _require(options == ["--verify-offhost"], "FIXED_OFFHOST_VERB_REQUIRED")
        token = os.environ.get("GITHUB_TOKEN")
        _require(type(token) is str and len(token) > 0, "ACTIONS_READ_TOKEN_REQUIRED")
        from scripts import rc6_ci_release_required_guard as r252
        from scripts import rc6_r163_trusted_postrun_byte_audit as r163

        github = r163.GitHubReadOnlyREST(token)
        receipt = verify_authenticated_release(
            os.environ, checkout_sha=_checkout_head(), github=github,
            trusted_release=r252.verify_trusted_release,
        )
    except (ControlHold, ValueError, OSError, RuntimeError, TimeoutError,
            ImportError, AttributeError, TypeError, KeyError) as exc:
        reason = exc.reason if isinstance(exc, ControlHold) else "TRUSTED_DEPENDENCY_FAILURE"
        print("RC6_R340_OFFHOST=HOLD79|reason=" + reason +
              "|host_control_verified=NO_VERIFICADO|deploy_authorized=NO")
        return 79
    # Even release success NEVER unlocks ROOT; the existing R321 job holds.
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
