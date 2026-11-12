"""R281 trusted-main OFFHOST proof for a single existing immutable D/E pair.

The script reopens actual GitHub REST records and ZIP bytes through the
reviewed R252/R251 verifier.  It is NOT a deployment script, host preflight,
owner GO, permission to trade, or a replacement for the canonical Deploy V2.
Do not add workflow_dispatch inputs for a release selector: they would allow
an unreviewed artifact to be substituted for the fixed D/E evidence.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, Mapping


REPO = "mbalbo2023/Porota-trading"
PRODUCT = "deploy/rc6-pr69-isolated-20260915"
PR_NUMBER = 1087
CANDIDATE_SHA = "4fe14abddc4693948c8081840f4ffa60e0f6c8a0"
CANDIDATE_TREE = "9871d69ff3570dce3ff3203941f7bc615d1ce7d8"
PUBLISHER_MAIN_SHA = "7718e1ed16174da9e13bc4bd39158c2aba63a156"
D_RUN_ID = 43301355473
D_RUN_ATTEMPT = 1
D_ARTIFACT_ID = 13982961716
D_ARTIFACT_DIGEST = "sha256:65fc878e62e116a4e93b508b1bd36e015f53a21b6fcba351303c5aac9abb4a0d"
E_RUN_ID = 43304509783
E_RUN_ATTEMPT = 1
E_ARTIFACT_ID = 13984065411
E_ARTIFACT_DIGEST = "sha256:0b077b5ce678df601ecc5b1bd4b3e0630115c60947cb8943deff087f46f6c1f4"
HEX40 = re.compile(r"[0-9a-f]{40}\Z")

# R252 performs the authoritative D/E same-run, PR, checkout-tree, digest,
# manifest and ZIP-byte checks.  This controller pins the exact known receipts
# and intentionally cannot select the latest or a similarly named artifact.
BRIDGE_EVIDENCE = {
    "RC6_BRIDGE_REPOSITORY": REPO,
    "RC6_BRIDGE_MODE": "PR_CANDIDATE",
    "RC6_BRIDGE_CANDIDATE_SHA": CANDIDATE_SHA,
    "RC6_BRIDGE_CANDIDATE_TREE_SHA": CANDIDATE_TREE,
    "RC6_BRIDGE_PUBLISHER_MAIN_SHA": PUBLISHER_MAIN_SHA,
    "RC6_BRIDGE_E_RUN_ID": str(E_RUN_ID),
    "RC6_BRIDGE_E_RUN_ATTEMPT": str(E_RUN_ATTEMPT),
    "RC6_BRIDGE_E_ARTIFACT_ID": str(E_ARTIFACT_ID),
    "RC6_BRIDGE_PRODUCT_SHA": "",
    "RC6_BRIDGE_PRODUCT_REF": "",
}


class R281Hold(ValueError):
    """A failed release/host boundary is an intentional HOLD79."""


def _hold() -> None:
    raise R281Hold("RC6_R281_HOLD79")


def verify_offhost(
    event: Mapping[str, str], *, checkout_sha: str,
    github: Any, trusted_bridge: Callable[..., Mapping[str, Any]],
) -> dict[str, Any]:
    """Verify a fixed R251 D/E pair with R252; grant no host or deploy GO.

    External boundaries (REST and real byte verifier) are injected in tests
    only; the CLI uses the reviewed modules and a read-only GitHub token.
    """
    if (not isinstance(event, Mapping) or
            event.get("GITHUB_REPOSITORY") != REPO or
            event.get("GITHUB_EVENT_NAME") != "workflow_dispatch" or
            event.get("GITHUB_REF") != "refs/heads/main" or
            type(checkout_sha) is not str or
            HEX40.fullmatch(checkout_sha) is None or
            event.get("GITHUB_SHA") != checkout_sha):
        _hold()

    getter = getattr(github, "get_json", None)
    if not callable(getter):
        _hold()

    def rest(path: str) -> Mapping[str, Any]:
        try:
            record = getter(path)
        except (ValueError, OSError, RuntimeError, TimeoutError, TypeError):
            _hold()
        if not isinstance(record, Mapping):
            _hold()
        return record

    # GitHub's current PR and protected PRODUCT ref select the R252 mode.
    # Never take a SHA/mode selector from the workflow_dispatch operator.
    pr_path = f"/repos/{REPO}/pulls/{PR_NUMBER}"
    pr = rest(pr_path)
    head = pr.get("head")
    if (type(pr.get("number")) is not int or pr["number"] != PR_NUMBER or
            pr.get("draft") is not False or
            not isinstance(head, Mapping) or
            head.get("sha") != CANDIDATE_SHA):
        _hold()
    bridge_evidence = dict(BRIDGE_EVIDENCE)
    product_path = None
    product = None
    product_sha = ""
    if pr.get("state") == "open" and pr.get("merged") is False:
        mode = "PR_CANDIDATE"
    elif pr.get("state") == "closed" and pr.get("merged") is True:
        mode = "PRODUCT_POSTMERGE"
        product_path = f"/repos/{REPO}/git/ref/heads/{PRODUCT}"
        product = rest(product_path)
        obj = product.get("object")
        if (product.get("ref") != "refs/heads/" + PRODUCT or
                not isinstance(obj, Mapping) or
                obj.get("type") != "commit" or
                type(obj.get("sha")) is not str or
                HEX40.fullmatch(obj["sha"]) is None or
                pr.get("merge_commit_sha") != obj["sha"]):
            _hold()
        product_sha = obj["sha"]
        bridge_evidence.update({
            "RC6_BRIDGE_MODE": mode,
            "RC6_BRIDGE_PRODUCT_SHA": product_sha,
            "RC6_BRIDGE_PRODUCT_REF": "refs/heads/" + PRODUCT,
        })
    else:
        _hold()

    if not callable(trusted_bridge):
        _hold()
    try:
        verified = trusted_bridge(bridge_evidence, github=github)
    except (ValueError, OSError, RuntimeError, TimeoutError, TypeError, KeyError):
        _hold()

    # Every field is checked, including the explicit NO/False states. A real
    # release receipt must NEVER be interpreted as owner GO or runtime proof.
    expected = {
        "status": "TRUSTED_RELEASE_BYTES_BOUND",
        "mode": mode,
        "candidate_sha": CANDIDATE_SHA,
        "candidate_tree_sha": CANDIDATE_TREE,
        "product_sha": product_sha,
        "release_run_id": D_RUN_ID,
        "release_run_attempt": D_RUN_ATTEMPT,
        "release_artifact_id": D_ARTIFACT_ID,
        "release_artifact_digest": D_ARTIFACT_DIGEST,
        "trusted_main_audit_run_id": E_RUN_ID,
        "trusted_main_audit_run_attempt": E_RUN_ATTEMPT,
        "artifact_contents_verified_by_this_guard": True,
        "owner_go_verified": False,
        "runtime_verified": False,
        "automatic_go": False,
    }
    if (not isinstance(verified, Mapping) or
            set(verified) != set(expected) or
            any(type(verified[key]) is not type(value) or verified[key] != value
                for key, value in expected.items())):
        _hold()

    # R252 reopens E and audits its ZIP. Pin E's metadata digest as a further
    # immutable identity check, including run and original publisher SHA.
    e_artifact = rest(f"/repos/{REPO}/actions/artifacts/{E_ARTIFACT_ID}")
    if (not isinstance(e_artifact, Mapping) or
            type(e_artifact.get("id")) is not int or
            e_artifact["id"] != E_ARTIFACT_ID or
            e_artifact.get("digest") != E_ARTIFACT_DIGEST or
            e_artifact.get("expired") is not False or
            not isinstance(e_artifact.get("workflow_run"), Mapping) or
            e_artifact["workflow_run"].get("id") != E_RUN_ID or
            e_artifact["workflow_run"].get("head_sha") != PUBLISHER_MAIN_SHA):
        _hold()
    # Bind the pre-audit mode and product source against later HEAD races.
    if rest(pr_path) != pr:
        _hold()
    if product_path is not None and rest(product_path) != product:
        _hold()

    return {
        "status": "OFFHOST_D_E_BYTES_REOPENED",
        "mode": mode,
        "candidate_sha": CANDIDATE_SHA,
        "candidate_tree_sha": CANDIDATE_TREE,
        "product_sha": product_sha,
        "release_run_id": D_RUN_ID,
        "release_artifact_id": D_ARTIFACT_ID,
        "release_artifact_digest": D_ARTIFACT_DIGEST,
        "trusted_main_audit_run_id": E_RUN_ID,
        "e_artifact_id": E_ARTIFACT_ID,
        "e_artifact_digest": E_ARTIFACT_DIGEST,
        "artifact_contents_verified_by_this_guard": True,
        "owner_go_verified": False,
        "host_control_verified": False,
        "runtime_verified": False,
        "deploy_authorized": False,
        "automatic_go": False,
    }


def _load_trusted_guard():
    """Import only the reviewed R252 sibling from the main checkout."""
    source = Path(__file__).resolve().parent / "rc6_ci_release_required_guard.py"
    if not source.is_file():
        _hold()
    try:
        spec = importlib.util.spec_from_file_location("rc6_r281_r252_guard", source)
        if spec is None or spec.loader is None:
            _hold()
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except (OSError, ImportError, SyntaxError, ValueError):
        _hold()


def _trusted_checkout_sha() -> str:
    root = Path(__file__).resolve().parents[1]
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (OSError, TimeoutError, subprocess.TimeoutExpired):
        _hold()
    checkout = completed.stdout.strip()
    if completed.returncode or HEX40.fullmatch(checkout) is None:
        _hold()
    return checkout


def main(argv: list[str] | None = None) -> int:
    if argv != ["--verify-fixed-d-e"]:
        print("RC6_R281_OFFHOST=HOLD79|deploy_authorized=NO", file=sys.stderr)
        return 79
    try:
        token = os.environ.get("GITHUB_TOKEN", "")
        output = os.environ.get("GITHUB_OUTPUT", "")
        if not token or not output:
            _hold()
        guard = _load_trusted_guard()
        client_type = guard._load_trusted_module(
            "rc6_r163_trusted_postrun_byte_audit.py").GitHubReadOnlyREST
        client = client_type(token)
        receipt = verify_offhost(
            os.environ, checkout_sha=_trusted_checkout_sha(), github=client,
            trusted_bridge=guard.verify_trusted_release,
        )
        # Only offhost_release_verified is surfaced to this single run. HOST
        # stays a separate non-skippable HOLD79 job and has no privileged step.
        with open(output, "a", encoding="utf-8") as stream:
            stream.write("offhost_release_verified=true\n")
    except (R281Hold, ValueError, OSError, RuntimeError, TimeoutError,
            ImportError, TypeError, KeyError, AttributeError):
        print("RC6_R281_OFFHOST=HOLD79|host_control_verified=NO_VERIFICADO|deploy_authorized=NO",
              file=sys.stderr)
        return 79
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
