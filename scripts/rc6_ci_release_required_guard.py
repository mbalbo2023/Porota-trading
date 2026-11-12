"""RC6 trusted-main release evidence guard (legacy A651 + R252 bridge).

GitHub considers a skipped job successful for required-status purposes.  This
pure, OFFHOST guard is run by a separate non-skippable product-PR job, after the
existing exact artifact gate.  All evidence inputs are from the *same* Actions
run, not an artifact with a matching name found in another run.  This check is
not owner GO, deployment permission, or evidence of runtime PAPER safety.

R252 independently reopens the completed R251 D/E chain over GitHub REST and
delegates ZIP byte audits to the existing R251 verifier. It cannot authorize
host promotion or real orders. Legacy same-run A651 stays non-authoritative.
"""

from __future__ import annotations

import os
import json
import importlib.util
import re
import stat
import sys
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Any, Mapping


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

BRIDGE_FIELDS = frozenset({
    "RC6_BRIDGE_REPOSITORY", "RC6_BRIDGE_MODE",
    "RC6_BRIDGE_CANDIDATE_SHA", "RC6_BRIDGE_CANDIDATE_TREE_SHA",
    "RC6_BRIDGE_PUBLISHER_MAIN_SHA", "RC6_BRIDGE_E_RUN_ID",
    "RC6_BRIDGE_E_RUN_ATTEMPT", "RC6_BRIDGE_E_ARTIFACT_ID",
    "RC6_BRIDGE_PRODUCT_SHA", "RC6_BRIDGE_PRODUCT_REF",
})
BRIDGE_MODES = frozenset({"PR_CANDIDATE", "PRODUCT_POSTMERGE"})
PR_NUMBER = 1087
CANDIDATE_BRANCH = "integration/rc6-a1115-r87-unified-paper-candidate-20261110"
E_PATH = ".github/workflows/rc6-r251-trusted-release-postcompletion.yml"
D_PATH = ".github/workflows/rc6-r251-trusted-release-build.yml"
E_NAME = "RC6 R251 Trusted Release Postcompletion Byte Audit (READ ONLY)"
D_NAME = "RC6 R251 Trusted Release Build (OFFHOST ONLY)"
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"sha256:[0-9a-f]{64}\Z")
RFC3339 = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z\Z")


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


def _positive_decimal(raw: Any) -> int:
    if type(raw) is not str or re.fullmatch(r"[1-9][0-9]{0,16}", raw) is None:
        _red()
    return int(raw)


def _timestamp(raw: Any) -> datetime:
    if type(raw) is not str or RFC3339.fullmatch(raw) is None:
        _red()
    try:
        return datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError:
        _red()


def _run_matches(run: Any, *, run_id: int, attempt: int, workflow: str,
                 name: str, event: str, main_sha: str) -> None:
    if not isinstance(run, Mapping):
        _red()
    paths = {workflow, workflow + "@main", workflow + "@refs/heads/main",
             workflow + "@" + main_sha}
    if (run.get("id") != run_id or type(run.get("id")) is not int or
            run.get("run_attempt") != attempt or
            type(run.get("run_attempt")) is not int or
            run.get("status") != "completed" or
            run.get("conclusion") != "success" or
            run.get("event") != event or type(run.get("path")) is not str or
            run["path"] not in paths or
            run.get("name") != name or run.get("head_sha") != main_sha or
            run.get("head_branch") != "main" or
            not isinstance(run.get("repository"), Mapping) or
            run["repository"].get("full_name") != REPO or
            not isinstance(run.get("head_repository"), Mapping) or
            run["head_repository"].get("full_name") != REPO):
        _red()
    if _timestamp(run.get("run_started_at")) > _timestamp(run.get("updated_at")):
        _red()


def _artifact_matches(artifact: Any, *, artifact_id: int, run_id: int,
                      name: str, digest: str, main_sha: str,
                      run: Mapping[str, Any]) -> None:
    if not isinstance(artifact, Mapping) or not isinstance(artifact.get("workflow_run"), Mapping):
        _red()
    created = _timestamp(artifact.get("created_at"))
    if (artifact.get("id") != artifact_id or type(artifact.get("id")) is not int or
            artifact.get("name") != name or artifact.get("expired") is not False or
            artifact.get("digest") != digest or type(digest) is not str or
            HEX64.fullmatch(digest) is None or
            type(artifact.get("size_in_bytes")) is not int or
            artifact["size_in_bytes"] <= 0 or
            artifact["workflow_run"].get("id") != run_id or
            artifact["workflow_run"].get("head_sha") != main_sha or
            not _timestamp(run["run_started_at"]) <= created <= _timestamp(run["updated_at"])):
        _red()


def _unique_artifact_list(payload: Any, expected: Mapping[str, Any]) -> None:
    # Never take latest-by-created_at or an ambiguous rerun artifact.
    if (not isinstance(payload, Mapping) or
            type(payload.get("total_count")) is not int or
            not 0 < payload["total_count"] <= 100 or
            type(payload.get("artifacts")) is not list or
            len(payload["artifacts"]) != payload["total_count"]):
        _red()
    matches = [row for row in payload["artifacts"]
               if isinstance(row, Mapping) and row.get("name") == expected["name"]]
    if len(matches) != 1 or matches[0] != expected:
        _red()


def _candidate_pr(pr: Any, *, mode: str, sha: str, product_sha: str) -> None:
    if not isinstance(pr, Mapping):
        _red()
    head, base = pr.get("head"), pr.get("base")
    if (pr.get("number") != PR_NUMBER or type(pr.get("draft")) is not bool or
            pr["draft"] is not False or not isinstance(head, Mapping) or
            not isinstance(base, Mapping) or not isinstance(head.get("repo"), Mapping) or
            not isinstance(base.get("repo"), Mapping) or
            head.get("sha") != sha or head.get("ref") != CANDIDATE_BRANCH or
            head["repo"].get("full_name") != REPO or
            base.get("ref") != PRODUCT or base["repo"].get("full_name") != REPO):
        _red()
    if mode == "PR_CANDIDATE":
        if pr.get("state") != "open" or pr.get("merged") is True:
            _red()
    elif (pr.get("state") != "closed" or pr.get("merged") is not True or
          pr.get("merge_commit_sha") != product_sha):
        _red()


def _github_get(github: Any, path: str) -> Mapping[str, Any]:
    getter = getattr(github, "get_json", None) or getattr(github, "get", None)
    if not callable(getter):
        _red()
    try:
        response = getter(path)
    except (ValueError, OSError, RuntimeError, TimeoutError):
        _red()
    if not isinstance(response, Mapping):
        _red()
    return response


def _reopen_stable(github: Any, records: Mapping[str, Mapping[str, Any]]) -> None:
    for path, before in records.items():
        if _github_get(github, path) != before:
            _red()


def _load_trusted_module(filename: str) -> Any:
    """Load only reviewed sibling scripts under the -I trusted checkout.

    `python -I scripts/guard.py` intentionally does not place its checkout on
    sys.path; never add an untrusted candidate working directory to imports.
    """
    if filename not in {"rc6_r163_artifact_byte_verifier.py",
                        "rc6_r163_trusted_postrun_byte_audit.py"}:
        _red()
    source = Path(__file__).resolve().parent / filename
    try:
        meta = source.lstat()
        if (not stat.S_ISREG(meta.st_mode) or meta.st_size <= 0 or
                meta.st_size > 1024 * 1024):
            _red()
        spec = importlib.util.spec_from_file_location(
            "rc6_trusted_main_" + source.stem, source)
        if spec is None or spec.loader is None:
            _red()
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except (OSError, ImportError, SyntaxError, ValueError):
        _red()


def verify_trusted_release(
    data: Mapping[str, str], *, github: Any, byte_verifier: Any = None,
) -> dict[str, Any]:
    """Authenticate completed R251 E/D receipts and the exact candidate/merge.

    Tests may replace the separately tested R251 byte-verifier boundary.
    The CLI never accepts an override, and missing reviewed R251 code is RED.
    """
    if (not isinstance(data, Mapping) or set(data) != BRIDGE_FIELDS or
            any(type(value) is not str for value in data.values()) or
            data["RC6_BRIDGE_REPOSITORY"] != REPO or
            data["RC6_BRIDGE_MODE"] not in BRIDGE_MODES):
        _red()
    mode = data["RC6_BRIDGE_MODE"]
    candidate = data["RC6_BRIDGE_CANDIDATE_SHA"]
    tree = data["RC6_BRIDGE_CANDIDATE_TREE_SHA"]
    publisher = data["RC6_BRIDGE_PUBLISHER_MAIN_SHA"]
    product_sha = data["RC6_BRIDGE_PRODUCT_SHA"]
    product_ref = data["RC6_BRIDGE_PRODUCT_REF"]
    if any(HEX40.fullmatch(value) is None for value in (candidate, tree, publisher)):
        _red()
    if mode == "PR_CANDIDATE":
        if product_sha != "" or product_ref != "":
            _red()
    elif HEX40.fullmatch(product_sha) is None or product_ref != "refs/heads/" + PRODUCT:
        _red()
    e_id = _positive_decimal(data["RC6_BRIDGE_E_RUN_ID"])
    e_attempt = _positive_decimal(data["RC6_BRIDGE_E_RUN_ATTEMPT"])
    e_artifact_id = _positive_decimal(data["RC6_BRIDGE_E_ARTIFACT_ID"])
    if byte_verifier is None:
        byte_verifier = _load_trusted_module("rc6_r163_artifact_byte_verifier.py")
    for name in ("verify_postcompletion_receipt_zip", "verify_artifact_bytes"):
        if not callable(getattr(byte_verifier, name, None)):
            _red()
    if (not callable(getattr(byte_verifier, "download_release_zip", None)) and
            not callable(getattr(github, "download_artifact", None))):
        _red()

    prefix = "/repos/" + REPO
    e_run_path = f"{prefix}/actions/runs/{e_id}"
    e_attempt_path = f"{e_run_path}/attempts/{e_attempt}"
    e_art_path = f"{prefix}/actions/artifacts/{e_artifact_id}"
    e_list_path = f"{e_run_path}/artifacts?per_page=100"
    pr_path = f"{prefix}/pulls/{PR_NUMBER}"
    commit_path = f"{prefix}/git/commits/{candidate}"
    first: dict[str, Mapping[str, Any]] = {}
    for path in (e_run_path, e_attempt_path, e_art_path, e_list_path,
                 pr_path, commit_path):
        first[path] = _github_get(github, path)
    e_run, e_art = first[e_run_path], first[e_art_path]
    _run_matches(e_run, run_id=e_id, attempt=e_attempt, workflow=E_PATH,
                 name=E_NAME, event="workflow_run", main_sha=publisher)
    _run_matches(first[e_attempt_path], run_id=e_id, attempt=e_attempt,
                 workflow=E_PATH, name=E_NAME, event="workflow_run",
                 main_sha=publisher)
    if (first[commit_path].get("sha") != candidate or
            not isinstance(first[commit_path].get("tree"), Mapping) or
            first[commit_path]["tree"].get("sha") != tree):
        _red()
    _candidate_pr(first[pr_path], mode=mode, sha=candidate,
                  product_sha=product_sha)
    if product_sha:
        product_path = f"{prefix}/commits/{product_sha}"
        first[product_path] = _github_get(github, product_path)
        commit = first[product_path]
        parents = commit.get("parents")
        if (commit.get("sha") != product_sha or
                not isinstance(parents, list) or len(parents) != 2 or
                any(not isinstance(p, Mapping) or
                    type(p.get("sha")) is not str or
                    HEX40.fullmatch(p["sha"]) is None for p in parents) or
                parents[1]["sha"] != candidate or
                not isinstance(commit.get("commit"), Mapping) or
                not isinstance(commit["commit"].get("tree"), Mapping) or
                commit["commit"]["tree"].get("sha") != tree):
            _red()

    # R251 alone downloads securely and parses/validates either ZIP; this
    # bridge only checks trusted REST identities, attempt windows and races.
    with tempfile.TemporaryDirectory(prefix="rc6-r252-release-") as directory:
        temp = Path(directory)

        def downloaded(artifact_id: int) -> Path:
            fake = getattr(github, "download_artifact", None)
            if callable(fake):
                return Path(fake(artifact_id))  # controlled unit-test boundary
            dest = temp / f"artifact-{artifact_id}.zip"
            byte_verifier.download_release_zip(github, artifact_id, dest)
            return dest

        try:
            e_receipt = byte_verifier.verify_postcompletion_receipt_zip(
                downloaded(e_artifact_id), expected_digest=e_art.get("digest"),
                expected_size_bytes=e_art.get("size_in_bytes"))
        except (ValueError, OSError, RuntimeError, TimeoutError):
            _red()
        if not isinstance(e_receipt, Mapping):
            _red()
        d_id = e_receipt.get("publisher_run_id")
        d_attempt = e_receipt.get("publisher_run_attempt")
        d_artifact_id = e_receipt.get("release_artifact_id")
        if any(type(x) is not int or x <= 0 for x in
               (d_id, d_attempt, d_artifact_id)):
            _red()
        d_run_path = f"{prefix}/actions/runs/{d_id}"
        d_attempt_path = f"{d_run_path}/attempts/{d_attempt}"
        d_art_path = f"{prefix}/actions/artifacts/{d_artifact_id}"
        d_list_path = f"{d_run_path}/artifacts?per_page=100"
        for path in (d_run_path, d_attempt_path, d_art_path, d_list_path):
            first[path] = _github_get(github, path)
        d_run, d_art = first[d_run_path], first[d_art_path]
        _run_matches(d_run, run_id=d_id, attempt=d_attempt, workflow=D_PATH,
                     name=D_NAME, event="workflow_dispatch", main_sha=publisher)
        _run_matches(first[d_attempt_path], run_id=d_id, attempt=d_attempt,
                     workflow=D_PATH, name=D_NAME, event="workflow_dispatch",
                     main_sha=publisher)
        d_name = f"rc6-r251-release-{d_id}-{d_attempt}-{candidate}"
        e_name = f"rc6-r251-postcompletion-{d_id}-{d_attempt}-{candidate}"
        d_digest = d_art.get("digest")
        _artifact_matches(e_art, artifact_id=e_artifact_id, run_id=e_id,
                          name=e_name, digest=e_art.get("digest"),
                          main_sha=publisher, run=first[e_attempt_path])
        _unique_artifact_list(first[e_list_path], e_art)
        _artifact_matches(d_art, artifact_id=d_artifact_id, run_id=d_id,
                          name=d_name, digest=d_digest, main_sha=publisher,
                          run=first[d_attempt_path])
        _unique_artifact_list(first[d_list_path], d_art)
        if (e_receipt.get("schema_version") != 1 or
                e_receipt.get("status") != "RELEASE_BYTES_VERIFIED_POSTCOMPLETION" or
                e_receipt.get("repository") != REPO or
                e_receipt.get("candidate_sha") != candidate or
                e_receipt.get("candidate_tree_sha") != tree or
                e_receipt.get("publisher_main_sha") != publisher or
                e_receipt.get("publisher_workflow_path") != D_PATH or
                e_receipt.get("release_artifact_id") != d_artifact_id or
                e_receipt.get("release_artifact_name") != d_name or
                e_receipt.get("release_artifact_digest") != d_digest or
                e_receipt.get("release_artifact_size_bytes") != d_art.get("size_in_bytes") or
                e_receipt.get("release_manifest_verified") is not True or
                e_receipt.get("r144_release_go") is not False or
                e_receipt.get("runtime_verified") is not False or
                e_receipt.get("real_orders_physical_verified") is not False):
            _red()
        try:
            d_proof = byte_verifier.verify_artifact_bytes(
                downloaded(d_artifact_id), artifact_receipt=d_art,
                run_receipt=d_run, expected_candidate_sha=candidate,
                expected_tree_sha=tree, expected_run_id=d_id,
                expected_run_attempt=d_attempt, expected_artifact_id=d_artifact_id,
                expected_digest=d_digest, expected_workflow_path=D_PATH,
                expected_publisher_sha=publisher)
        except (ValueError, OSError, RuntimeError, TimeoutError):
            _red()
    if (not isinstance(d_proof, Mapping) or
            d_proof.get("status") != "BYTES_MATCH_EXPECTED" or
            d_proof.get("artifact_contents_verified_by_this_guard") is not True or
            d_proof.get("candidate_sha") != candidate or
            d_proof.get("candidate_tree_sha") != tree or
            d_proof.get("publisher_main_sha") != publisher or
            d_proof.get("artifact_id") != d_artifact_id or
            d_proof.get("artifact_digest") != d_digest or
            d_proof.get("run_id") != d_id or
            d_proof.get("run_attempt") != d_attempt or
            d_proof.get("automatic_go") is not False or
            d_proof.get("owner_go_verified") is not False or
            d_proof.get("runtime_verified") is not False):
        _red()
    _reopen_stable(github, first)
    return {
        "status": "TRUSTED_RELEASE_BYTES_BOUND",
        "mode": mode, "candidate_sha": candidate, "candidate_tree_sha": tree,
        "product_sha": product_sha, "release_run_id": d_id,
        "release_run_attempt": d_attempt, "release_artifact_id": d_artifact_id,
        "release_artifact_digest": d_digest, "trusted_main_audit_run_id": e_id,
        "trusted_main_audit_run_attempt": e_attempt,
        "artifact_contents_verified_by_this_guard": True,
        "owner_go_verified": False, "runtime_verified": False,
        "automatic_go": False,
    }


def _main_trusted_bridge(mode: str) -> int:
    try:
        if mode not in BRIDGE_MODES:
            _red()
        values = {key: os.environ.get(key, "") for key in BRIDGE_FIELDS}
        if (values["RC6_BRIDGE_MODE"] != mode or
                os.environ.get("GITHUB_REPOSITORY") != REPO):
            _red()
        event = os.environ.get("GITHUB_EVENT_NAME")
        ref = os.environ.get("GITHUB_REF")
        sha = os.environ.get("GITHUB_SHA")
        if mode == "PR_CANDIDATE":
            if (event != "pull_request" or
                    ref not in (f"refs/pull/{PR_NUMBER}/merge",
                                f"refs/pull/{PR_NUMBER}/head")):
                _red()
        elif (event != "push" or ref != "refs/heads/" + PRODUCT or
              sha != values["RC6_BRIDGE_PRODUCT_SHA"]):
            _red()
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            _red()
        # Use the established R206 read-only REST transport; R251 owns
        # storage-redirect allowlisting and both bounded ZIP parsers.
        client_type = _load_trusted_module(
            "rc6_r163_trusted_postrun_byte_audit.py").GitHubReadOnlyREST
        client = client_type(token)
        result = verify_trusted_release(values, github=client)
    except (RequiredReleaseEvidenceRed, ValueError, OSError, RuntimeError,
            TimeoutError, ImportError):
        print("RC6_R252_TRUSTED_RELEASE=HOLD79|automatic_go=NO", file=sys.stderr)
        return 79
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv:
        if len(argv) == 2 and argv[0] == "--trusted-release-mode":
            return _main_trusted_bridge(argv[1])
        print("RC6_R252_TRUSTED_RELEASE=HOLD79|automatic_go=NO", file=sys.stderr)
        return 79
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
    raise SystemExit(main(sys.argv[1:]))
