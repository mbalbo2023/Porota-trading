"""R281: immutable D/E reuse never becomes a host or deploy authorization."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from scripts import rc6_r281_trusted_deploy_controller as controller


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/rc6-r281-trusted-deploy-controller.yml"
MAIN = "9" * 40
PRODUCT_SHA = "1" * 40
PR_PATH = "/repos/mbalbo2023/Porota-trading/pulls/1087"
PRODUCT_PATH = ("/repos/mbalbo2023/Porota-trading/git/ref/heads/"
                "deploy/rc6-pr69-isolated-20260915")
E_PATH = "/repos/mbalbo2023/Porota-trading/actions/artifacts/13984065411"


def trusted_event() -> dict[str, str]:
    return {
        "GITHUB_REPOSITORY": "mbalbo2023/Porota-trading",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": MAIN,
    }


def validated_bridge_receipt(mode="PR_CANDIDATE") -> dict:
    return {
        "status": "TRUSTED_RELEASE_BYTES_BOUND",
        "mode": mode,
        "candidate_sha": controller.CANDIDATE_SHA,
        "candidate_tree_sha": controller.CANDIDATE_TREE,
        "product_sha": PRODUCT_SHA if mode == "PRODUCT_POSTMERGE" else "",
        "release_run_id": controller.D_RUN_ID,
        "release_run_attempt": controller.D_RUN_ATTEMPT,
        "release_artifact_id": controller.D_ARTIFACT_ID,
        "release_artifact_digest": controller.D_ARTIFACT_DIGEST,
        "trusted_main_audit_run_id": controller.E_RUN_ID,
        "trusted_main_audit_run_attempt": controller.E_RUN_ATTEMPT,
        "artifact_contents_verified_by_this_guard": True,
        "owner_go_verified": False,
        "runtime_verified": False,
        "automatic_go": False,
    }


class ReadOnlyAPI:
    def __init__(self, *, mode="PR_CANDIDATE", **overrides):
        self.record = {
            "id": controller.E_ARTIFACT_ID,
            "digest": controller.E_ARTIFACT_DIGEST,
            "expired": False,
            "workflow_run": {
                "id": controller.E_RUN_ID,
                "head_sha": controller.PUBLISHER_MAIN_SHA,
            },
        }
        self.record.update(overrides)
        self.pr = {
            "number": 1087, "draft": False,
            "state": "closed" if mode == "PRODUCT_POSTMERGE" else "open",
            "merged": mode == "PRODUCT_POSTMERGE",
            "merge_commit_sha": PRODUCT_SHA if mode == "PRODUCT_POSTMERGE" else None,
            "head": {"sha": controller.CANDIDATE_SHA},
        }
        self.product = {
            "ref": "refs/heads/deploy/rc6-pr69-isolated-20260915",
            "object": {"type": "commit", "sha": PRODUCT_SHA},
        }
        self.requested_paths = []
        self.after_reads = {}

    def get_json(self, path):
        self.requested_paths.append(path)
        if path in self.after_reads and self.requested_paths.count(path) >= 2:
            return deepcopy(self.after_reads[path])
        if path == E_PATH:
            return deepcopy(self.record)
        if path == PR_PATH:
            return deepcopy(self.pr)
        if path == PRODUCT_PATH:
            return deepcopy(self.product)
        raise ValueError("UNEXPECTED_REST_PATH")


def run_verify(*, mode="PR_CANDIDATE", event=None, receipt=None, api=None,
               bridge_error=None):
    observed = []

    def bridge(inputs, *, github):
        observed.append((dict(inputs), github))
        if bridge_error:
            raise bridge_error
        return deepcopy(validated_bridge_receipt(mode) if receipt is None else receipt)

    reader = api or ReadOnlyAPI(mode=mode)
    result = controller.verify_offhost(
        trusted_event() if event is None else event,
        checkout_sha=MAIN,
        github=reader,
        trusted_bridge=bridge,
    )
    return result, observed, reader


def test_known_d_e_receipts_are_reused_offhost_without_owner_or_host_go():
    receipt, calls, reader = run_verify()
    assert len(calls) == 1
    assert calls[0][0] == controller.BRIDGE_EVIDENCE
    assert calls[0][1] is reader
    assert calls[0][0]["RC6_BRIDGE_CANDIDATE_SHA"] == (
        "4fe14abddc4693948c8081840f4ffa60e0f6c8a0")
    assert receipt["status"] == "OFFHOST_D_E_BYTES_REOPENED"
    assert receipt["release_run_id"] == 43301355473
    assert receipt["trusted_main_audit_run_id"] == 43304509783
    assert receipt["release_artifact_digest"] == (
        "sha256:65fc878e62e116a4e93b508b1bd36e015f53a21b6fcba351303c5aac9abb4a0d")
    assert receipt["e_artifact_digest"] == (
        "sha256:0b077b5ce678df601ecc5b1bd4b3e0630115c60947cb8943deff087f46f6c1f4")
    assert receipt["artifact_contents_verified_by_this_guard"] is True
    assert receipt["host_control_verified"] is False
    assert receipt["owner_go_verified"] is False
    assert receipt["runtime_verified"] is False
    assert receipt["deploy_authorized"] is False
    assert receipt["automatic_go"] is False
    assert reader.requested_paths == [PR_PATH, E_PATH, PR_PATH]


def test_product_postmerge_reuses_exact_d_e_without_build_or_input_override():
    receipt, calls, reader = run_verify(mode="PRODUCT_POSTMERGE")
    assert calls[0][0]["RC6_BRIDGE_MODE"] == "PRODUCT_POSTMERGE"
    assert calls[0][0]["RC6_BRIDGE_PRODUCT_SHA"] == PRODUCT_SHA
    assert calls[0][0]["RC6_BRIDGE_PRODUCT_REF"] == (
        "refs/heads/deploy/rc6-pr69-isolated-20260915")
    assert calls[0][0]["RC6_BRIDGE_CANDIDATE_SHA"] == controller.CANDIDATE_SHA
    assert receipt["product_sha"] == PRODUCT_SHA
    assert receipt["status"] == "OFFHOST_D_E_BYTES_REOPENED"
    assert receipt["host_control_verified"] is False
    assert receipt["deploy_authorized"] is False
    assert reader.requested_paths == [
        PR_PATH, PRODUCT_PATH, E_PATH, PR_PATH, PRODUCT_PATH]


@pytest.mark.parametrize("mutation", [
    lambda api: api.pr.update(draft=True),
    lambda api: api.pr.update(number=1088),
    lambda api: api.pr.update(state="closed", merged=False),
    lambda api: api.pr.update(state="open", merged=True),
    lambda api: api.pr["head"].update(sha="2" * 40),
])
def test_untrusted_pr_transition_holds_before_r252(mutation):
    reader = ReadOnlyAPI()
    mutation(reader)
    with pytest.raises(controller.R281Hold):
        run_verify(api=reader)


@pytest.mark.parametrize("mutation", [
    lambda api: api.pr.update(merge_commit_sha="2" * 40),
    lambda api: api.product.update(ref="refs/heads/main"),
    lambda api: api.product["object"].update(type="tag"),
    lambda api: api.product["object"].update(sha="2" * 40),
])
def test_postmerge_product_head_is_canonical_and_matches_merge(mutation):
    reader = ReadOnlyAPI(mode="PRODUCT_POSTMERGE")
    mutation(reader)
    with pytest.raises(controller.R281Hold):
        run_verify(mode="PRODUCT_POSTMERGE", api=reader)


def test_postmerge_forged_r252_mode_or_merge_commit_rejected():
    fake = validated_bridge_receipt("PRODUCT_POSTMERGE")
    fake["product_sha"] = "3" * 40
    with pytest.raises(controller.R281Hold):
        run_verify(mode="PRODUCT_POSTMERGE", receipt=fake)


def test_pr_mutation_across_r252_audit_is_rejected():
    reader = ReadOnlyAPI()
    changed = deepcopy(reader.pr)
    changed["head"]["sha"] = "3" * 40
    reader.after_reads[PR_PATH] = changed
    with pytest.raises(controller.R281Hold):
        run_verify(api=reader)


def test_postmerge_protected_branch_head_race_is_rejected():
    reader = ReadOnlyAPI(mode="PRODUCT_POSTMERGE")
    changed = deepcopy(reader.product)
    changed["object"]["sha"] = "3" * 40
    reader.after_reads[PRODUCT_PATH] = changed
    with pytest.raises(controller.R281Hold):
        run_verify(mode="PRODUCT_POSTMERGE", api=reader)


@pytest.mark.parametrize("field,value", [
    ("GITHUB_REPOSITORY", "attacker/example"),
    ("GITHUB_EVENT_NAME", "pull_request"),
    ("GITHUB_REF", "refs/heads/deploy/rc6-pr69-isolated-20260915"),
    ("GITHUB_SHA", "8" * 40),
])
def test_event_not_exact_reviewed_main_checkout_holds_before_rest(field, value):
    event = trusted_event()
    event[field] = value
    with pytest.raises(controller.R281Hold):
        run_verify(event=event)


@pytest.mark.parametrize("field,value", [
    ("status", "SUCCESS"),
    ("mode", "PRODUCT_POSTMERGE"),
    ("candidate_sha", "8" * 40),
    ("candidate_tree_sha", "8" * 40),
    ("product_sha", "7" * 40),
    ("release_run_id", 43301355474),
    ("release_run_attempt", 2),
    ("release_artifact_id", 13982961717),
    ("release_artifact_digest", "sha256:" + "f" * 64),
    ("trusted_main_audit_run_id", 43304509784),
    ("trusted_main_audit_run_attempt", 2),
    ("artifact_contents_verified_by_this_guard", False),
    ("owner_go_verified", True),
    ("runtime_verified", True),
    ("automatic_go", True),
])
def test_no_fake_or_mismatched_r252_release_receipt_accepted(field, value):
    fake = validated_bridge_receipt()
    fake[field] = value
    with pytest.raises(controller.R281Hold):
        run_verify(receipt=fake)


@pytest.mark.parametrize("record_changes", [
    {"id": 13984065412},
    {"digest": "sha256:" + "a" * 64},
    {"expired": True},
    {"workflow_run": {"id": 43304509784,
                      "head_sha": "7718e1ed16174da9e13bc4bd39158c2aba63a156"}},
    {"workflow_run": {"id": 43304509783,
                      "head_sha": "7" * 40}},
])
def test_e_digest_or_identity_mismatch_fails_closed(record_changes):
    with pytest.raises(controller.R281Hold):
        run_verify(api=ReadOnlyAPI(**record_changes))


def test_draft_candidate_rejection_from_r252_remains_blocking():
    with pytest.raises(controller.R281Hold):
        run_verify(bridge_error=ValueError("RC6_REQUIRED_RELEASE_EVIDENCE_RED"))


def test_missing_required_field_is_hold_not_implicit_go():
    incomplete = validated_bridge_receipt()
    incomplete.pop("artifact_contents_verified_by_this_guard")
    with pytest.raises(controller.R281Hold):
        run_verify(receipt=incomplete)


def test_real_trusted_r252_r251_sources_load_in_isolated_checkout():
    guard = controller._load_trusted_guard()
    transport = guard._load_trusted_module(
        "rc6_r163_trusted_postrun_byte_audit.py")
    bytes_verifier = guard._load_trusted_module(
        "rc6_r163_artifact_byte_verifier.py")
    assert callable(guard.verify_trusted_release)
    assert callable(transport.GitHubReadOnlyREST)
    assert callable(bytes_verifier.verify_artifact_bytes)
    assert callable(bytes_verifier.verify_postcompletion_receipt_zip)


def test_main_outputs_only_offhost_receipt_never_host_or_deploy_go(
        tmp_path, monkeypatch, capsys):
    report = tmp_path / "github-output"
    bridge_receipt = validated_bridge_receipt()
    fake_api = ReadOnlyAPI()
    fake_guard = SimpleNamespace(
        _load_trusted_module=lambda name: SimpleNamespace(
            GitHubReadOnlyREST=lambda token: fake_api),
        verify_trusted_release=lambda values, github: deepcopy(bridge_receipt),
    )
    monkeypatch.setattr(controller, "_load_trusted_guard", lambda: fake_guard)
    monkeypatch.setattr(controller, "_trusted_checkout_sha", lambda: MAIN)
    for key, value in trusted_event().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GITHUB_TOKEN", "example-not-real-token")
    monkeypatch.setenv("GITHUB_OUTPUT", str(report))
    assert controller.main(["--verify-fixed-d-e"]) == 0
    assert report.read_text() == "offhost_release_verified=true\n"
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "OFFHOST_D_E_BYTES_REOPENED"
    assert receipt["host_control_verified"] is False
    assert receipt["deploy_authorized"] is False


def test_workflow_has_manual_main_only_non_skippable_host_hold79():
    source = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.load(source, Loader=yaml.BaseLoader)
    assert list(workflow["on"]) == ["workflow_dispatch"]
    assert workflow["concurrency"] == {
        "group": "rc6-unified-paper-deploy", "cancel-in-progress": "false"}
    assert workflow["permissions"] == {
        "contents": "read", "actions": "read", "pull-requests": "read"}
    assert set(workflow["jobs"]) == {
        "offhost_release_evidence", "host_physical_preflight"}
    offhost = workflow["jobs"]["offhost_release_evidence"]
    host = workflow["jobs"]["host_physical_preflight"]
    assert "refs/heads/main" in offhost["if"]
    assert host["needs"] == "offhost_release_evidence"
    assert host["if"] == "${{ always() }}"
    assert "exit 79" in str(host)
    assert "host_control_verified=NO_VERIFICADO" in str(host)
    assert "deploy_authorized=NO" in str(host)
    assert "rc6_r281_trusted_deploy_controller.py --verify-fixed-d-e" in str(offhost)
    assert all("docker" not in str(jobs).lower() and
               "ssh " not in str(jobs).lower() and
               "sudo " not in str(jobs).lower() and
               "systemctl " not in str(jobs).lower()
               for jobs in workflow["jobs"].values())


def test_command_line_without_trusted_github_context_is_hold79():
    result = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts/rc6_r281_trusted_deploy_controller.py"),
         "--verify-fixed-d-e"],
        cwd=ROOT, capture_output=True, text=True, timeout=5,
        env={"PATH": "/usr/bin:/bin", "GITHUB_REPOSITORY": "mbalbo2023/Porota-trading"},
        check=False,
    )
    assert result.returncode == 79
    assert "RC6_R281_OFFHOST=HOLD79" in result.stderr
    assert "OFFHOST_D_E_BYTES_REOPENED" not in result.stdout
