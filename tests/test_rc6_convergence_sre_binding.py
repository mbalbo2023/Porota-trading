"""Approved origin, external ZIP replacement and pre-tag identity regressions."""
from copy import deepcopy
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import stat
import sys
import tarfile
import zipfile

import pytest

from scripts.porota_predeploy_binding import (
    BASE_BRANCH, BindingRejected, DENIED_ARTIFACTS, DENIED_HEADS, DENIED_RUNS,
    REQUIRED_FILES, REPOSITORY, TRAILERS, WORKFLOW_PATH,
    CONVERGENCE_HANDOFF, CONVERGENCE_VERIFIER,
    artifact_name, parse_merge_approval, safe_extract, validate_binding, verify_frozen_payload,
    verify_final_input_provenance, verify_governed_exclusions,
)
from scripts.porota_artifact_provenance import canonical_bytes, create_source_manifest, sha256_file
from scripts.porota_build_deploy_bundle_v2 import build_bundle
from scripts import rc6_archive_v3_image_smoke as codec_smoke
from scripts import rc6_convergence_provenance as convergence_verifier
from tests.test_rc6_archive_v3_image_smoke import synthetic_receipt
from scripts.porota_host_manifest_v2 import build_manifest
from scripts.porota_host_policy_v2 import validate_policy
from tests.test_issue465_provenance import candidate, git, write

NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)


def approved_origin():
    approval = {"candidate_sha": "a" * 40, "repository": REPOSITORY, "workflow_path": WORKFLOW_PATH,
                "event": "pull_request", "workflow_id": 101, "run_id": 202, "run_attempt": 3,
                "artifact_id": 303, "artifact_digest": "sha256:" + "d" * 64}
    workflow = {"id": 101, "path": WORKFLOW_PATH, "state": "active"}
    run = {"id": 202, "workflow_id": 101, "path": WORKFLOW_PATH, "head_sha": "a" * 40,
           "event": "pull_request", "run_attempt": 3, "status": "completed", "conclusion": "success",
           "run_started_at": "2026-10-04T23:00:00Z", "updated_at": "2026-10-05T00:00:00Z",
           "repository": {"id": 9, "full_name": REPOSITORY}, "head_repository": {"id": 9, "full_name": REPOSITORY},
           "pull_requests": [{"head": {"sha": "a" * 40, "repo": {"id": 9}},
                              "base": {"ref": BASE_BRANCH, "repo": {"id": 9}}}]}
    artifact = {"id": 303, "name": artifact_name(approval), "expired": False,
                "digest": approval["artifact_digest"], "size_in_bytes": 1234,
                "created_at": "2026-10-04T23:30:00Z",
                "expires_at": "2026-10-12T00:00:00Z", "workflow_run": {
                    "id": 202, "head_sha": "a" * 40, "repository_id": 9, "head_repository_id": 9}}
    return approval, workflow, run, deepcopy(run), artifact, [deepcopy(artifact)]


def check(values):
    return validate_binding(*values, now=NOW)


def test_approved_unique_run_attempt_artifact_origin_is_green():
    result = check(approved_origin())
    assert result["run_id"] == 202 and result["run_attempt"] == 3 and result["artifact_id"] == 303


@pytest.mark.parametrize("mutation", ["head", "workflow_id", "workflow_path", "event", "repository",
    "fork_head_repo", "rerun_attempt", "failed", "in_progress", "wrong_pr_head", "wrong_pr_base",
    "attempt_head", "homonym_workflow", "artifact_other_run", "artifact_other_head", "expired",
    "expiry_clock", "artifact_digest", "artifact_id", "duplicate_artifact", "unrelated_artifact",
    "prior_attempt_artifact", "missing_repository_id", "boolean_attempt"])
def test_approved_origin_rejects_adversarial_inputs(mutation):
    values = list(approved_origin()); approval, workflow, run, attempt, artifact, inventory = values
    if mutation == "head": run["head_sha"] = "f" * 40
    elif mutation == "workflow_id": run["workflow_id"] = 404
    elif mutation == "workflow_path": run["path"] = ".github/workflows/homonym.yml"
    elif mutation == "event": run["event"] = "workflow_dispatch"
    elif mutation == "repository": run["repository"]["full_name"] = "foreign/repo"
    elif mutation == "fork_head_repo": run["head_repository"]["full_name"] = "fork/Porota-trading"
    elif mutation == "rerun_attempt": run["run_attempt"] = 4
    elif mutation == "failed": run["conclusion"] = "failure"
    elif mutation == "in_progress": run["status"] = "in_progress"
    elif mutation == "wrong_pr_head": run["pull_requests"][0]["head"]["sha"] = "f" * 40
    elif mutation == "wrong_pr_base": run["pull_requests"][0]["base"]["ref"] = "main"
    elif mutation == "attempt_head": attempt["head_sha"] = "f" * 40
    elif mutation == "homonym_workflow": workflow.update(id=999, name="Porota Predeploy V2")
    elif mutation == "artifact_other_run": artifact["workflow_run"]["id"] = 999
    elif mutation == "artifact_other_head": artifact["workflow_run"]["head_sha"] = "f" * 40
    elif mutation == "expired": artifact["expired"] = True
    elif mutation == "expiry_clock": artifact["expires_at"] = NOW.isoformat()
    elif mutation == "artifact_digest": artifact["digest"] = "sha256:" + "e" * 64
    elif mutation == "artifact_id": artifact["id"] = 999
    elif mutation == "duplicate_artifact": inventory.append(deepcopy(artifact))
    elif mutation == "unrelated_artifact": inventory.clear()
    elif mutation == "prior_attempt_artifact": artifact["created_at"] = "2026-10-04T22:59:59Z"
    elif mutation == "missing_repository_id":
        run["repository"].pop("id")
    elif mutation == "boolean_attempt": run["run_attempt"] = True
    with pytest.raises(BindingRejected):
        check(values)


def merge_message(approval):
    return "Merge reviewed final candidate\n\n" + "\n".join(f"{name}: {approval[field]}" for name, field in TRAILERS.items())


def test_merge_approval_is_concrete_and_rejects_duplicates_and_old_inputs():
    approval = approved_origin()[0]
    assert parse_merge_approval(merge_message(approval), approval["candidate_sha"]) == approval
    for message in ("ordinary merge", merge_message(approval) + "\nPorota-Predeploy-Run-ID: 999"):
        with pytest.raises(BindingRejected):
            parse_merge_approval(message, approval["candidate_sha"])
    approval["run_id"] = 37234866451
    with pytest.raises(BindingRejected, match="PRECONVERGENCE_ARTIFACT_DENIED"):
        parse_merge_approval(merge_message(approval), approval["candidate_sha"])


@pytest.mark.parametrize("field,denied", [("run_id", value) for value in sorted(DENIED_RUNS)] +
    [("artifact_id", value) for value in sorted(DENIED_ARTIFACTS)] +
    [("candidate_sha", value) for value in sorted(DENIED_HEADS)])
def test_every_superseded_input_is_denied_from_exact_handoff_identity(field, denied):
    approval = approved_origin()[0]; approval[field] = denied
    with pytest.raises(BindingRejected, match="PRECONVERGENCE"):
        parse_merge_approval(merge_message(approval), approval["candidate_sha"])


def saved_source_image(c, path):
    layer = io.BytesIO()
    with tarfile.open(fileobj=layer, mode="w") as archive:
        archive.add(c["image"], arcname="app")
    raw = layer.getvalue()
    config = canonical_bytes({"config": {"Labels": c["labels"]}, "rootfs": {
        "type": "layers", "diff_ids": ["sha256:" + hashlib.sha256(raw).hexdigest()]}})
    identity = "sha256:" + hashlib.sha256(config).hexdigest()
    manifest = [{"Config": "config.json", "Layers": ["layer.tar"],
                 "RepoTags": ["porota-predeploy-v2:" + c["manifest"]["candidate_sha"]]}]
    with tarfile.open(path, "w:gz") as archive:
        for name, data in (("layer.tar", raw), ("config.json", config), ("manifest.json", canonical_bytes(manifest))):
            info = tarfile.TarInfo(name); info.size = len(data); archive.addfile(info, io.BytesIO(data))
    return identity


@pytest.fixture
def frozen_payload(candidate, tmp_path):
    c = candidate
    policy = {"units": {}}
    write(c["repo"], "ops/policy/host-control-plane-reconciliation-v2.json", canonical_bytes(policy))
    # This native Git/Docker-save-format fixture checks metadata only. It
    # never runs Docker or represents execution of the image's codec CLI.
    source_root = Path(__file__).resolve().parents[1]
    for name in (*codec_smoke.SOURCE_PATHS, "rc6_shadow_runtime/__init__.py"):
        write(c["repo"], name, (source_root / name).read_bytes())
        (c["repo"] / name).chmod(0o644)
    git(c["repo"], "add", "."); git(c["repo"], "commit", "-qm", "fixture host policy")
    c["manifest"] = create_source_manifest(c["repo"])
    c["source"].write_bytes(canonical_bytes(c["manifest"]))
    for row in c["manifest"]["files"]:
        if row["image_required"]:
            target = c["image"] / row["path"]; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(c["repo"] / row["path"], target)
    write(c["image"], "POROTA_SOURCE_PROVENANCE.json", c["source"].read_bytes())
    c["labels"].update({"porota.commit": c["manifest"]["candidate_sha"],
        "porota.tree": c["manifest"]["candidate_tree_sha"],
        "porota.source-manifest-sha256": sha256_file(c["source"])})
    build_bundle(c["repo"], c["bundle"], c["bundle_meta"], c["source"])
    output = tmp_path / "frozen"; output.mkdir()
    image = output / "porota-predeploy-image.tar.gz"
    image_id = saved_source_image(c, image)
    binding = approved_origin()[0]; binding["candidate_sha"] = c["manifest"]["candidate_sha"]
    origin = {name: binding[name] for name in (
        "repository", "workflow_path", "workflow_id", "run_id", "run_attempt", "event", "candidate_sha")}
    frozen = {"execution_origin": origin, "candidate_sha": binding["candidate_sha"],
        "candidate_tree_sha": c["manifest"]["candidate_tree_sha"], "paper_mode_required": "PRODUCTION_PAPER",
        "real_orders_sent_required": 0, "real_order_capability_required": "BLOCKED", "build_once": True,
        "image_size_bytes": 10240, "image_id": image_id, "image_tar_sha256": sha256_file(image),
        "source_manifest_sha256": sha256_file(c["source"])}
    codec_raw = canonical_bytes(synthetic_receipt(c["manifest"], frozen))
    codec_path = output / "porota-runtime-codec-smoke.json"
    codec_path.write_bytes(codec_raw); codec_path.chmod(0o644)
    source_rows = {row["path"]: row for row in c["manifest"]["files"]}
    frozen["runtime_codec_smoke"] = {"schema": codec_smoke.SCHEMA,
        "receipt_sha256": hashlib.sha256(codec_raw).hexdigest(), "image_id": image_id,
        "script_sha256": source_rows[codec_smoke.SOURCE_PATHS[0]]["sha256"],
        "fixtures_sha256": {name: source_rows[name]["sha256"] for name in codec_smoke.SOURCE_PATHS[1:3]}}
    (output / "porota-frozen-candidate.json").write_bytes(canonical_bytes(frozen))
    for path, name in ((c["source"], "porota-source-provenance.json"),
                       (c["bundle"], "porota-deploy-bundle-v2.tgz"),
                       (c["bundle_meta"], "porota-deploy-bundle-v2-manifest.json")):
        shutil.copy2(path, output / name)
    manifest = build_manifest(c["repo"], policy, binding["candidate_sha"])
    # Exercise the native CLI's actual pretty JSON format, rather than a
    # canonical-format surrogate that would hide serialization incompatibility.
    for name, payload in (("porota-host-manifest-v2.json", manifest),
                          ("porota-host-policy-v2.json", validate_policy(manifest, policy))):
        (output / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    # Generic source-format controls contain no convergence handoff and make
    # no governed/FIP execution claim. All primary receipt names are present.
    generic = {"fixture_scope": "EXPLICIT_SYNTHETIC_METADATA_ONLY_NO_GOVERNED_OR_FINAL_CLOSURE"}
    for name in ("porota-final-input-provenance.json", "porota-governed-tests.json"):
        path = output / name; path.write_bytes(canonical_bytes(generic)); path.chmod(0o644)
    junit = output / "porota-governed-tests.xml"
    junit.write_bytes(b'<fixture scope="EXPLICIT_SYNTHETIC_METADATA_ONLY_NO_GOVERNED_EVIDENCE"/>\n')
    junit.chmod(0o644)
    return c, output, binding, frozen


def test_full_frozen_payload_source_bundle_image_and_native_host_receipts_are_verified(frozen_payload):
    c, output, binding, _ = frozen_payload
    result = verify_frozen_payload(c["repo"], output, binding, c["manifest"]["candidate_tree_sha"])
    assert result["status"] == "GREEN" and set(result["files"]) == set(REQUIRED_FILES)
    assert result["image"]["image_id"] == json.loads((output / "porota-frozen-candidate.json").read_text())["image_id"]
    assert result["runtime_codec_smoke"]["runtime_approval"] is False
    assert result["final_input_provenance"]["status"] == "NOT_APPLICABLE"


@pytest.fixture
def self_asserted_final_receipts(frozen_payload):
    """Attack fixture: closed-looking metadata with no original input closure."""
    c, output, binding, frozen = frozen_payload
    root = Path(__file__).resolve().parents[1]
    write(c["repo"], CONVERGENCE_HANDOFF, b'{"fixture":"ATTACK_METADATA_NOT_ORIGINAL_INPUTS"}\n')
    write(c["repo"], CONVERGENCE_VERIFIER, (root / CONVERGENCE_VERIFIER).read_bytes())
    (c["repo"] / CONVERGENCE_VERIFIER).chmod(0o644)
    git(c["repo"], "add", "."); git(c["repo"], "commit", "-qm", "private self-asserted FIP attack fixture")
    source = create_source_manifest(c["repo"])
    (output / "porota-source-provenance.json").write_bytes(canonical_bytes(source))
    frozen.update(candidate_sha=source["candidate_sha"], candidate_tree_sha=source["candidate_tree_sha"],
                  source_manifest_sha256=sha256_file(output / "porota-source-provenance.json"))
    junit = b'<testsuites><testsuite name="pytest" tests="1" failures="0" errors="0" skipped="0"><testcase classname="tests.test_fixture" name="test_case"/></testsuite></testsuites>\n'
    (output / "porota-governed-tests.xml").write_bytes(junit)
    fip = {"schema": "rc6.final-input-provenance.v1", "candidate_sha": source["candidate_sha"],
        "candidate_tree": source["candidate_tree_sha"], "software_status": "EXECUTED_NATIVE_GREEN",
        "final_candidate_eligible": True, "material_programming_gates_closed": True,
        "pending_material_programming_gates": [], "test_execution": {
            "junit_sha256": hashlib.sha256(junit).hexdigest(), "executed_unique_cases": 1},
        "fixture_scope": "ATTACK_SELF_ASSERTED_STATUS_WITHOUT_ORIGINAL_GIT_INPUT_PROVENANCE"}
    gov = {"schema_version": 1, "status": "GREEN", "source_unchanged": True,
        "scope": "repository-root automatic pytest discovery", "candidate_sha": source["candidate_sha"],
        "candidate_tree": source["candidate_tree_sha"], "junit_sha256": hashlib.sha256(junit).hexdigest(),
        "junit_bytes": len(junit), "discovered": 1, "executed": 1, "exclusions": [],
        **{name: 0 for name in ("pytest_exit_code", "failures", "errors", "skipped", "xfail")}}
    for name, report in (("porota-final-input-provenance.json", fip), ("porota-governed-tests.json", gov)):
        (output / name).write_bytes(canonical_bytes(report))
    frozen["final_input_provenance_sha256"] = sha256_file(output / "porota-final-input-provenance.json")
    return c, output, frozen, source, fip, gov


def test_self_asserted_green_final_fip_must_recompute_actual_original_git_closure(self_asserted_final_receipts):
    c, output, frozen, source, _fip, _gov = self_asserted_final_receipts
    with pytest.raises(ValueError, match="GIT_SOURCE_UNAVAILABLE|ORIGINAL_INPUT"):
        verify_final_input_provenance(c["repo"], output, frozen, source)


@pytest.fixture
def original_governed_exclusion_receipts(self_asserted_final_receipts):
    """Native original Git policy; other final-closure metadata remains attack-only."""
    c, output, frozen, _source, fip, gov = self_asserted_final_receipts
    root = Path(__file__).resolve().parents[1]
    original_handoff = subprocess.check_output(
        ["git", "--no-replace-objects", "-C", str(root), "show", "HEAD:" + CONVERGENCE_HANDOFF])
    manifest = json.loads(original_handoff)
    anchor = next(row["head_sha"] for row in manifest["sources"] if row["pr"] == 466)
    original_matrix = subprocess.check_output(
        ["git", "--no-replace-objects", "-C", str(root), "show",
         anchor + ":" + convergence_verifier.PRIOR_AUDIT_MATRIX])
    # Private Git uses the existing read-only object pool, never fabricating an
    # original commit or fetching from a network. Candidate writes stay private.
    objects = subprocess.check_output(
        ["git", "--no-replace-objects", "-C", str(root), "rev-parse", "--git-path", "objects"],
        text=True).strip()
    objects = (root / objects).resolve()
    write(c["repo"], ".git/objects/info/alternates", (str(objects) + "\n").encode())
    write(c["repo"], CONVERGENCE_HANDOFF, original_handoff)
    # A conflicting evolved candidate matrix cannot change the original policy.
    write(c["repo"], convergence_verifier.PRIOR_AUDIT_MATRIX,
          b'{"governed_exclusions":[],"fixture":"ATTACK_EVOLVED_CANDIDATE_POLICY"}\n')
    git(c["repo"], "add", "."); git(c["repo"], "commit", "-qm", "private original exclusion authority fixture")
    source = create_source_manifest(c["repo"])
    (output / "porota-source-provenance.json").write_bytes(canonical_bytes(source))
    frozen.update(candidate_sha=source["candidate_sha"], candidate_tree_sha=source["candidate_tree_sha"],
                  source_manifest_sha256=sha256_file(output / "porota-source-provenance.json"))
    fip.update(candidate_sha=source["candidate_sha"], candidate_tree=source["candidate_tree_sha"])
    gov.update(candidate_sha=source["candidate_sha"], candidate_tree=source["candidate_tree_sha"],
               exclusions=json.loads(original_matrix)["governed_exclusions"])
    for name, report in (("porota-final-input-provenance.json", fip), ("porota-governed-tests.json", gov)):
        (output / name).write_bytes(canonical_bytes(report))
    frozen["final_input_provenance_sha256"] = sha256_file(output / "porota-final-input-provenance.json")
    return c, output, frozen, source, fip, gov, original_matrix


def test_governed_exclusion_control_uses_exact_original_git_blob_and_ignores_evolved_candidate_policy(original_governed_exclusion_receipts):
    c, output, frozen, source, _fip, gov, original_matrix = original_governed_exclusion_receipts
    captured = {name: (output / name).read_bytes() for name in (
        "porota-final-input-provenance.json", "porota-governed-tests.xml", "porota-governed-tests.json")}
    verified = verify_governed_exclusions(c["repo"], frozen["candidate_sha"], source,
                                          convergence_verifier, gov["exclusions"])
    assert verified["source_pr"] == 466
    assert verified["source_blob"] == hashlib.sha1(
        b"blob " + str(len(original_matrix)).encode() + b"\0" + original_matrix).hexdigest()
    assert verified["raw_matrix_sha256"] == hashlib.sha256(original_matrix).hexdigest()
    assert verified["governed_exclusions"] == json.loads(original_matrix)["governed_exclusions"]
    assert verified["verification_scope"] == "EXACT_POLICY_FROM_IMMUTABLE_ORIGINAL_GIT_BLOB_NOT_GOV_SELF_ASSERTION"
    # This control closes policy comparison only. Its self-asserted FIP is still
    # rejected by native recomputation because the other original inputs are absent.
    with pytest.raises(ValueError, match="GIT_SOURCE_UNAVAILABLE|ORIGINAL_INPUT"):
        verify_final_input_provenance(c["repo"], output, frozen, source)
    assert captured == {name: (output / name).read_bytes() for name in captured}


@pytest.mark.parametrize("mutation", ["empty", "extra", "different_path", "duplicate"])
def test_governed_exclusion_drift_rejects_when_raw_fip_and_junit_stay_unchanged(original_governed_exclusion_receipts, mutation):
    c, output, frozen, source, _fip, gov, _matrix = original_governed_exclusion_receipts
    fip_bytes = (output / "porota-final-input-provenance.json").read_bytes()
    junit_bytes = (output / "porota-governed-tests.xml").read_bytes()
    if mutation == "empty": gov["exclusions"] = []
    elif mutation == "extra": gov["exclusions"].append("test_fixture_extra.py|EXPLICIT_ATTACK|tests/test_fixture_extra.py")
    elif mutation == "different_path":
        gov["exclusions"] = [value.replace("tests/", "tests/foreign/") for value in gov["exclusions"]]
    else: gov["exclusions"] += gov["exclusions"][:1]
    (output / "porota-governed-tests.json").write_bytes(canonical_bytes(gov))
    with pytest.raises(BindingRejected, match="^FINAL_GOVERNED_EXCLUSIONS_MISMATCH$"):
        verify_final_input_provenance(c["repo"], output, frozen, source)
    assert (output / "porota-final-input-provenance.json").read_bytes() == fip_bytes
    assert (output / "porota-governed-tests.xml").read_bytes() == junit_bytes
    assert frozen["final_input_provenance_sha256"] == hashlib.sha256(fip_bytes).hexdigest()


@pytest.mark.parametrize("mutation", ["wrong_source_digest", "rebound_handoff", "replacement_ref"])
def test_governed_exclusion_authority_cannot_be_rebound_to_another_original_source(original_governed_exclusion_receipts, mutation):
    c, _output, frozen, source, _fip, gov, _matrix = original_governed_exclusion_receipts
    if mutation == "wrong_source_digest":
        row = next(row for row in source["files"] if row["path"] == CONVERGENCE_HANDOFF)
        row["sha256"] = "f" * 64
    elif mutation == "rebound_handoff":
        handoff = json.loads((c["repo"] / CONVERGENCE_HANDOFF).read_bytes())
        next(row for row in handoff["sources"] if row["pr"] == 466)["head_sha"] = frozen["candidate_sha"]
        write(c["repo"], CONVERGENCE_HANDOFF, canonical_bytes(handoff))
        git(c["repo"], "add", "."); git(c["repo"], "commit", "-qm", "private coordinated authority rebound attack")
        source = create_source_manifest(c["repo"])
        frozen["candidate_sha"] = source["candidate_sha"]
    else:
        handoff = json.loads((c["repo"] / CONVERGENCE_HANDOFF).read_bytes())
        anchor = next(row["head_sha"] for row in handoff["sources"] if row["pr"] == 466)
        git(c["repo"], "replace", anchor, frozen["candidate_sha"])
    with pytest.raises(BindingRejected, match="ORIGINAL_INPUT_EXCLUSION_AUTHORITY_INVALID|GIT_REPLACE_REFS_FORBIDDEN"):
        verify_governed_exclusions(c["repo"], frozen["candidate_sha"], source,
                                   convergence_verifier, gov["exclusions"])


@pytest.mark.parametrize("mutation,signature", [("raw_hash", "DIGEST_MISMATCH"),
    ("candidate", "CANDIDATE_MISMATCH"), ("tree", "CANDIDATE_MISMATCH"),
    ("inventory", "CANDIDATE_MISMATCH"), ("eligible_false", "MATERIAL_GATES_OPEN"),
    ("eligible_integer", "MATERIAL_GATES_OPEN"), ("material_false", "MATERIAL_GATES_OPEN"),
    ("pending", "MATERIAL_GATES_OPEN"), ("pending_dict", "MATERIAL_GATES_OPEN"),
    ("junit_hash", "JUNIT_BINDING_INVALID"), ("gov_boolean_zero", "GOVERNED_RECEIPT_BINDING_INVALID"),
    ("gov_foreign_head", "GOVERNED_RECEIPT_BINDING_INVALID"),
    ("gov_count", "GOVERNED_RECEIPT_BINDING_INVALID"), ("duplicate_junit", "JUNIT_DUPLICATE_CASE"),
    ("failed_junit", "JUNIT_NONPASS"), ("junit_alias", None), ("fip_alias", None)])
def test_final_release_barrier_rejects_pending_wrong_head_and_forged_raw_governed_receipts(self_asserted_final_receipts, mutation, signature):
    c, output, frozen, source, fip, gov = self_asserted_final_receipts
    if mutation == "candidate": fip["candidate_sha"] = "f" * 40
    elif mutation == "tree": fip["candidate_tree"] = "f" * 40
    elif mutation == "inventory": fip["software_status"] = "INVENTORY_NOT_EXECUTED"
    elif mutation == "eligible_false": fip["final_candidate_eligible"] = False
    elif mutation == "eligible_integer": fip["final_candidate_eligible"] = 1
    elif mutation == "material_false": fip["material_programming_gates_closed"] = False
    elif mutation == "pending": fip["pending_material_programming_gates"] = [{"id": "U14", "disposition": "MATERIAL_PENDING"}]
    elif mutation == "pending_dict": fip["pending_material_programming_gates"] = {}
    elif mutation == "junit_hash": fip["test_execution"]["junit_sha256"] = "f" * 64
    elif mutation == "gov_boolean_zero": gov["failures"] = False
    elif mutation == "gov_foreign_head": gov["candidate_sha"] = "f" * 40
    elif mutation == "gov_count": gov["executed"] = 2
    elif mutation in {"duplicate_junit", "failed_junit"}:
        path = output / "porota-governed-tests.xml"
        raw = path.read_bytes()
        if mutation == "duplicate_junit":
            case = b'<testcase classname="tests.test_fixture" name="test_case"/>'
            raw = raw.replace(b'tests="1"', b'tests="2"').replace(case, case + case)
            gov["executed"] = gov["discovered"] = fip["test_execution"]["executed_unique_cases"] = 2
        else: raw = raw.replace(b'name="test_case"/>', b'name="test_case"><failure>actual failure</failure></testcase>')
        path.write_bytes(raw)
        gov["junit_sha256"] = fip["test_execution"]["junit_sha256"] = hashlib.sha256(raw).hexdigest()
        gov["junit_bytes"] = len(raw)
    (output / "porota-final-input-provenance.json").write_bytes(canonical_bytes(fip))
    (output / "porota-governed-tests.json").write_bytes(canonical_bytes(gov))
    frozen["final_input_provenance_sha256"] = sha256_file(output / "porota-final-input-provenance.json")
    if mutation == "raw_hash": (output / "porota-final-input-provenance.json").write_bytes(b'attack')
    if mutation in {"junit_alias", "fip_alias"}:
        name = "porota-governed-tests.xml" if mutation == "junit_alias" else "porota-final-input-provenance.json"
        path = output / name; target = output / ("original-" + name); path.rename(target); path.symlink_to(target)
    with pytest.raises((ValueError, OSError), match=signature):
        verify_final_input_provenance(c["repo"], output, frozen, source)


def test_git_replace_objects_are_rejected_before_any_artifact_claim(frozen_payload):
    c, output, binding, _frozen = frozen_payload
    original = git(c["repo"], "rev-parse", "HEAD")
    write(c["repo"], "worker.py", b"VALUE = 'replacement source'\n")
    git(c["repo"], "add", "."); git(c["repo"], "commit", "-qm", "private replacement")
    replacement = git(c["repo"], "rev-parse", "HEAD")
    git(c["repo"], "reset", "--hard", original); git(c["repo"], "replace", original, replacement)
    with pytest.raises(BindingRejected, match="GIT_REPLACE_REFS_FORBIDDEN"):
        verify_frozen_payload(c["repo"], output, binding, c["manifest"]["candidate_tree_sha"])


@pytest.mark.parametrize("mutation,signature", [("missing", None), ("alias", None),
    ("digest", "DIGEST_MISMATCH"), ("script", "SOURCE_MISMATCH"),
    ("fixture", "SOURCE_MISMATCH"), ("loaded_id", "BINDING_INVALID"),
    ("missing_case", "CASE_CLOSURE"), ("boolean_counter", "SAFETY"),
    ("unknown_schema", "NOT_GREEN"), ("root_uid", "CUSTODY"),
    ("fake_import", "IMPORT_BINDING"), ("self_approve", "SCOPE_ESCALATION"),
    ("legacy_wire", "CODEC_BYTES"), ("legacy_member", "LEGACY_MEMBER_BINDING"),
    ("inspector_source", "NAMESPACE_SCOPE"), ("signed_zero_claim", "FLOAT_CONTROL")])
def test_frozen_tiny_receipt_is_bound_to_raw_bytes_source_fixtures_and_image_id(frozen_payload, mutation, signature):
    c, output, binding, frozen = frozen_payload
    path = output / "porota-runtime-codec-smoke.json"
    report = json.loads(path.read_bytes())
    if mutation == "missing": path.unlink()
    elif mutation == "alias":
        original = output / "receipt-original.json"; path.rename(original); path.symlink_to(original)
    elif mutation == "digest": path.write_bytes(path.read_bytes() + b" ")
    elif mutation == "script": frozen["runtime_codec_smoke"]["script_sha256"] = "f" * 64
    elif mutation == "fixture": frozen["runtime_codec_smoke"]["fixtures_sha256"][codec_smoke.CODEC_FIXTURE] = "f" * 64
    elif mutation == "loaded_id": frozen["runtime_codec_smoke"]["image_id"] = "sha256:" + "f" * 64
    else:
        if mutation == "missing_case": del report["cases"][codec_smoke.CASE_IDS[-1]]
        elif mutation == "boolean_counter": report["provider_requests"] = False
        elif mutation == "unknown_schema": report["schema"] = "unsupported"
        elif mutation == "root_uid": report["execution_uid"] = 0
        elif mutation == "fake_import": report["imported_source_modules"] = {"rc6_shadow_runtime": {
            "path": codec_smoke.SOURCE_PATHS[0], "sha256": frozen["runtime_codec_smoke"]["script_sha256"]}}
        elif mutation == "legacy_wire": report["cases"][codec_smoke.CASE_IDS[0]]["wire_sha256"] = "f" * 64
        elif mutation == "legacy_member": report["cases"][codec_smoke.CASE_IDS[6]]["member_sha256"]["projection.sqlite"] = "f" * 64
        elif mutation == "inspector_source": report["cases"][codec_smoke.CASE_IDS[10]]["source_sha256"] = "f" * 64
        elif mutation == "signed_zero_claim": report["cases"][codec_smoke.CASE_IDS[1]]["signed_zero_and_finite_float_types_exact"] = 1
        else: report["nine_hour_archive_capacity"] = "APPROVED"
        path.write_bytes(canonical_bytes(report))
        frozen["runtime_codec_smoke"]["receipt_sha256"] = sha256_file(path)
    (output / "porota-frozen-candidate.json").write_bytes(canonical_bytes(frozen))
    with pytest.raises((ValueError, OSError), match=signature):
        verify_frozen_payload(c["repo"], output, binding, c["manifest"]["candidate_tree_sha"])


@pytest.mark.parametrize("field,value", [("workflow_id", 999), ("run_id", 999), ("run_attempt", 4),
    ("repository", "foreign/repo"), ("workflow_path", ".github/workflows/homonym.yml"),
    ("event", "workflow_dispatch"), ("candidate_sha", "f"*40)])
def test_payload_cannot_replace_external_origin_with_self_asserted_json(frozen_payload, field, value):
    c, output, binding, frozen = frozen_payload
    frozen["execution_origin"][field] = value
    (output / "porota-frozen-candidate.json").write_bytes(canonical_bytes(frozen))
    with pytest.raises(BindingRejected, match="APPROVAL_MISMATCH"):
        verify_frozen_payload(c["repo"], output, binding, c["manifest"]["candidate_tree_sha"])


@pytest.mark.parametrize("field,value", [("image_size_bytes", True), ("real_orders_sent_required", False),
    ("build_once", 1), ("paper_mode_required", "REAL")])
def test_payload_safety_and_capacity_metadata_types_fail_closed(frozen_payload, field, value):
    c, output, binding, frozen = frozen_payload
    frozen[field] = value
    (output / "porota-frozen-candidate.json").write_bytes(canonical_bytes(frozen))
    with pytest.raises(BindingRejected, match="APPROVAL_MISMATCH"):
        verify_frozen_payload(c["repo"], output, binding, c["manifest"]["candidate_tree_sha"])


def zip_fixture(path, *, extra=None, omit=()):
    with zipfile.ZipFile(path, "w") as archive:
        for name in REQUIRED_FILES:
            if name in omit:
                continue
            archive.writestr("evidence/" + name, b"offline fixture")
        if extra:
            with pytest.warns(UserWarning, match="Duplicate name") if extra in archive.namelist() else nullcontext():
                archive.writestr(extra, b"attack")
    return {"artifact_size_bytes": path.stat().st_size,
            "artifact_digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}


def test_zip_external_replacement_is_rejected_before_extraction(tmp_path):
    path = tmp_path / "artifact.zip"; binding = zip_fixture(path)
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("replacement.txt", b"valid ZIP with changed bytes")
    with pytest.raises(BindingRejected, match="APPROVED_ARTIFACT_BYTES_MISMATCH"):
        safe_extract(path, tmp_path / "out", binding)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("extra", ["../escape", "evidence/../escape", "/absolute",
    "other/porota-frozen-candidate.json", "evidence/porota-frozen-candidate.json"])
def test_zip_paths_duplicates_and_ambiguous_frozen_payload_are_rejected(tmp_path, extra):
    path = tmp_path / "artifact.zip"; binding = zip_fixture(path, extra=extra)
    with pytest.raises(BindingRejected):
        safe_extract(path, tmp_path / "out", binding)


def test_safe_zip_has_one_required_file_per_basename(tmp_path):
    path = tmp_path / "artifact.zip"; binding = zip_fixture(path)
    safe_extract(path, tmp_path / "out", binding)
    assert {item.name for item in (tmp_path / "out").iterdir()} == set(REQUIRED_FILES)


@pytest.mark.parametrize("mutation", ["none", "missing", "duplicate"])
def test_published_primary_requires_one_original_final_input_provenance_receipt(tmp_path, mutation):
    path = tmp_path / "artifact.zip"
    name = "porota-final-input-provenance.json"
    binding = zip_fixture(path, omit=(name,), extra=None if mutation == "missing" else "evidence/" + name)
    if mutation == "duplicate":
        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr("other/" + name, b"homonymous receipt")
        binding = {"artifact_size_bytes": path.stat().st_size,
                   "artifact_digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}
    if mutation == "none":
        safe_extract(path, tmp_path / "out", binding, extra_required=(name,))
        assert (tmp_path / "out" / name).read_bytes() == b"attack"
    else:
        with pytest.raises(BindingRejected):
            safe_extract(path, tmp_path / "out", binding, extra_required=(name,))
        assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("same_id", [True, False])
def test_actual_promotion_id_guards_run_before_any_tag_even_with_valid_tar(tmp_path, same_id):
    text = Path(".github/workflows/porota-deploy-v2-promote.yml").read_text()
    start = text.index('          LOADED_IMAGE_ID="$(sudo -n docker image inspect')
    end = text.index('          sudo -n python3 - "$STAGE"', start)
    code = "\n".join(line[10:] for line in text[start:end].splitlines())
    tag_lines = [line.strip() for line in text.splitlines() if line.strip().startswith('sudo -n docker tag "$FROZEN_IMAGE"')]
    bin_root = tmp_path / "bin"; bin_root.mkdir(); marker = tmp_path / "tags"
    (bin_root / "sudo").write_text('#!/bin/sh\nshift\nexec "$@"\n')
    (bin_root / "docker").write_text('''#!/bin/sh
if [ "$1" = image ]; then
  if [ "$4" = '{{.Id}}' ]; then printf '%s\\n' "$FIXTURE_LOADED_ID"; else printf '{}\\n'; fi
elif [ "$1" = tag ]; then printf 'tag\\n' >> "$FIXTURE_TAG_MARKER"; fi
''')
    for command in bin_root.iterdir(): command.chmod(0o755)
    image = tmp_path / "porota-predeploy-image.tar.gz"; image.write_bytes(b"same hashed tar")
    expected = "sha256:" + "a" * 64
    (tmp_path / "porota-frozen-candidate.json").write_text(json.dumps({
        "image_tar_sha256": hashlib.sha256(image.read_bytes()).hexdigest(), "image_config_sha256": "diagnostic"}))
    env = {**os.environ, "PATH": str(bin_root) + ":/usr/bin:/bin", "REMOTE_DIR": str(tmp_path),
           "EXPECTED_IMAGE_ID": expected, "FROZEN_IMAGE": "frozen", "CANDIDATE_IMAGE": "candidate",
           "STABLE_IMAGE": "stable", "FIXTURE_LOADED_ID": expected if same_id else "sha256:" + "b" * 64,
           "FIXTURE_TAG_MARKER": str(marker)}
    result = subprocess.run(["bash", "-c", "set -Eeuo pipefail\n" + code + "\n" + "\n".join(tag_lines)],
                            env=env, capture_output=True, text=True)
    assert (result.returncode == 0) is same_id
    assert marker.exists() is same_id
    if same_id: assert marker.read_text().splitlines() == ["tag", "tag"]


@pytest.mark.parametrize("mutation", ["none", "bad_source_mode", "symlink_target", "hardlink_target", "parent_alias"])
def test_actual_install_checks_all_inputs_and_aliases_before_first_atomic_copy(tmp_path, mutation):
    workflow = Path(".github/workflows/porota-deploy-v2-promote.yml").read_text()
    start = workflow.index('          sudo -n python3 - "$STAGE" "$REPO"')
    block = workflow[start:].split("<<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    code = "\n".join(line[10:] for line in block.splitlines())
    stage, target = tmp_path / "stage", tmp_path / "target"
    stage.mkdir(); target.mkdir()
    paths = {"a.py": b"NEW_FIRST\n", "package/b.py": b"NEW_LAST\n"}
    for root in (stage, target):
        for relative, value in paths.items():
            write(root, relative, value if root == stage else b"OLD\n")
    write(stage, "POROTA_SOURCE_PROVENANCE.json", b'{"status":"GREEN"}\n')
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(canonical_bytes({"status": "GREEN", "files": [
        {"path": relative, "sha256": sha256_file(stage / relative), "git_mode": "100644"}
        for relative in paths]}))
    foreign = tmp_path / "foreign.py"; foreign.write_bytes(b"FOREIGN_UNTOUCHED\n")
    if mutation == "bad_source_mode": (stage / "package/b.py").chmod(0o666)
    elif mutation in {"symlink_target", "hardlink_target"}:
        (target / "package/b.py").unlink()
        if mutation == "symlink_target": (target / "package/b.py").symlink_to(foreign)
        else: os.link(foreign, target / "package/b.py")
    elif mutation == "parent_alias":
        alias = tmp_path / "foreign-package"; alias.mkdir()
        shutil.rmtree(target / "package")
        (target / "package").symlink_to(alias, target_is_directory=True)
    result = subprocess.run([sys.executable, "-", str(stage), str(target), str(manifest)],
                            input=code, text=True, capture_output=True)
    assert (result.returncode == 0) is (mutation == "none")
    assert foreign.read_bytes() == b"FOREIGN_UNTOUCHED\n"
    if mutation == "none":
        assert all((target / name).read_bytes() == value for name, value in paths.items())
        assert stat.S_IMODE((target / "POROTA_SOURCE_PROVENANCE.json").stat().st_mode) == 0o644
        assert not list(target.rglob(".porota-install-*"))
    else:
        assert (target / "a.py").read_bytes() == b"OLD\n"
        assert not (target / "POROTA_SOURCE_PROVENANCE.json").exists()
