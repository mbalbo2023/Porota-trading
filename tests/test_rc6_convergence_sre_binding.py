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
    artifact_name, parse_merge_approval, safe_extract, validate_binding, verify_frozen_payload,
)
from scripts.porota_artifact_provenance import canonical_bytes, create_source_manifest, sha256_file
from scripts.porota_build_deploy_bundle_v2 import build_bundle
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
    return c, output, binding, frozen


def test_full_frozen_payload_source_bundle_image_and_native_host_receipts_are_verified(frozen_payload):
    c, output, binding, _ = frozen_payload
    result = verify_frozen_payload(c["repo"], output, binding, c["manifest"]["candidate_tree_sha"])
    assert result["status"] == "GREEN" and set(result["files"]) == set(REQUIRED_FILES)
    assert result["image"]["image_id"] == json.loads((output / "porota-frozen-candidate.json").read_text())["image_id"]


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


def zip_fixture(path, *, extra=None):
    with zipfile.ZipFile(path, "w") as archive:
        for name in REQUIRED_FILES:
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
    binding = zip_fixture(path, extra=None if mutation == "missing" else "evidence/" + name)
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
