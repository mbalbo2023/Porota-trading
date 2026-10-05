"""Published ZIP and evidence-only replay; no Docker daemon or remote API calls."""
from copy import copy, deepcopy
import hashlib
import io
import json
import gzip
from pathlib import Path
from types import SimpleNamespace
import tarfile
import time
import urllib.request

import pytest
import yaml

from scripts.porota_artifact_http import (
    ArtifactHTTPRejected, MAX_METADATA_BYTES, RemoveAuthorizationRedirect, api_get, download_artifact,
)
from scripts.porota_artifact_provenance import canonical_bytes, sha256_file, validate_image_archive
from scripts.porota_predeploy_binding import artifact_name, validate_binding, verify_frozen_payload
from scripts.porota_published_artifact_evidence import (
    MAX_EVIDENCE_BYTES, MAX_SECONDARY_ARTIFACT_BYTES, current_primary_binding, main,
    verify_loaded_image_and_imports, verify_saved_app_rootfs, write_evidence,
)
from tests.test_rc6_convergence_sre_binding import NOW, approved_origin, candidate, frozen_payload, saved_source_image


@pytest.fixture
def github_context(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "mbalbo2023/Porota-trading")
    monkeypatch.setenv("GH_TOKEN", "unit-test")


class Response(io.BytesIO):
    pass


class MemoryOpener:
    def __init__(self, data): self.data, self.requests = data, []
    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return Response(self.data)


def test_native_urllib_redirect_does_not_forward_authorization():
    request = urllib.request.Request("https://api.github.com/artifact", headers={"Authorization": "Bearer unit-test"})
    request.add_unredirected_header("Authorization", "Bearer another-unit-test")
    handler = RemoveAuthorizationRedirect()
    redirect = handler.redirect_request(request, None, 302, "Found", {}, "https://signed-download.example/object?signature=fixture")
    assert "authorization" not in {key.lower() for key, _ in redirect.header_items()}
    assert request.get_header("Authorization") is not None
    with pytest.raises(ArtifactHTTPRejected, match="TRANSPORT_REJECTED"):
        handler.redirect_request(request, None, 302, "Found", {}, "http://signed-download.example/object")


@pytest.mark.parametrize("drift", ["none", "changed_bytes", "oversize", "truncated", "deadline"])
def test_bounded_stream_download_and_digest_execute_the_real_io_algorithm(tmp_path, github_context, drift):
    data = b"verified immutable outer ZIP bytes" * 2000
    expected = "sha256:" + hashlib.sha256(data).hexdigest()
    returned = data if drift not in {"changed_bytes", "oversize", "truncated"} else {
        "changed_bytes": b"x" + data[1:], "oversize": data + b"x", "truncated": data[:-1]}[drift]
    opener = MemoryOpener(returned); output = tmp_path / "primary.zip"
    kwargs = dict(expected_size=len(data), expected_digest=expected,
                  deadline=99 if drift == "deadline" else 200, opener=opener, clock=lambda: 100)
    if drift == "none":
        result = download_artifact(303, output, **kwargs)
        assert output.read_bytes() == data and result["status"] == "GREEN"
    else:
        with pytest.raises(ArtifactHTTPRejected): download_artifact(303, output, **kwargs)
        assert not output.exists()
    assert all(0 < timeout <= 30 for _, timeout in opener.requests)


def test_existing_download_file_is_never_deleted_or_overwritten(tmp_path, github_context):
    data = b"new"; output = tmp_path / "primary.zip"; output.write_bytes(b"preserved")
    with pytest.raises(FileExistsError):
        download_artifact(303, output, expected_size=len(data),
            expected_digest="sha256:" + hashlib.sha256(data).hexdigest(), deadline=200,
            opener=MemoryOpener(data), clock=lambda: 100)
    assert output.read_bytes() == b"preserved"


def test_metadata_response_is_bounded_and_uses_fixed_repository_url(github_context):
    opener = MemoryOpener(b'{"id":303}')
    assert api_get("/actions/artifacts/303", opener=opener, deadline=200, clock=lambda: 100) == {"id": 303}
    assert opener.requests[0][0].full_url == "https://api.github.com/repos/mbalbo2023/Porota-trading/actions/artifacts/303"
    with pytest.raises(ArtifactHTTPRejected, match="SIZE_LIMIT"):
        api_get("/actions/artifacts/303", opener=MemoryOpener(b" "*(MAX_METADATA_BYTES+1)), deadline=200, clock=lambda: 100)


def test_current_upload_binding_is_evidence_only_until_completed_success(monkeypatch):
    approval, workflow, run, attempt, artifact, inventory = approved_origin()
    for execution in (run, attempt): execution.update(status="in_progress", conclusion=None)
    env = {"GITHUB_REPOSITORY": approval["repository"], "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_WORKFLOW_REF": approval["repository"] + "/" + approval["workflow_path"] + "@refs/pull/472/merge",
        "GITHUB_RUN_ID": "202", "GITHUB_RUN_ATTEMPT": "3"}
    for name, value in env.items(): monkeypatch.setenv(name, value)
    values = {"/actions/workflows/porota-predeploy-v2.yml": workflow, "/actions/runs/202": run,
        "/actions/runs/202/attempts/3": attempt, "/actions/artifacts/303": artifact,
        "/actions/runs/202/artifacts?per_page=100": {"total_count": 1, "artifacts": inventory}}
    binding = current_primary_binding(approval["candidate_sha"], 303, approval["artifact_digest"][7:],
        deadline=200, get=lambda path, **kwargs: deepcopy(values[path]))
    assert binding["execution_state"] == "IN_PROGRESS_EVIDENCE_ONLY"
    with pytest.raises(ValueError): validate_binding(approval, workflow, run, attempt, artifact, inventory, now=NOW)


def test_real_saved_layer_replay_matches_git_bytes_and_all_modes(frozen_payload):
    c, output, _, _ = frozen_payload
    image = output / "porota-predeploy-image.tar.gz"
    result = verify_saved_app_rootfs(image, c["manifest"])
    assert result["status"] == "GREEN"
    assert result["source_files_and_generated_metadata_verified"] == sum(row["image_required"] for row in c["manifest"]["files"]) + 1
    assert any(row["mode"] == 0o755 for row in result["files"])


@pytest.mark.parametrize("mutation", ["same_bytes_writable", "changed_bytes", "deleted_file", "parent_alias", "extra_source"])
def test_fully_rehashed_saved_image_rootfs_drift_rejects(frozen_payload, mutation):
    c, output, _, _ = frozen_payload
    image = output / "porota-predeploy-image.tar.gz"
    if mutation == "same_bytes_writable": (c["image"] / "worker.py").chmod(0o666)
    elif mutation == "changed_bytes": (c["image"] / "worker.py").write_bytes(b"changed\n")
    elif mutation == "deleted_file": (c["image"] / "worker.py").unlink()
    elif mutation == "parent_alias":
        policy = c["image"] / "ops/policy/paper.json"; policy.unlink(); policy.symlink_to("../not-paper.json")
    elif mutation == "extra_source": (c["image"] / "untracked.py").write_bytes(b"not Git source\n")
    identity = saved_source_image(c, image)
    # Raw config/layers and their hashes remain internally consistent. The
    # independent Git->rootfs proof must still detect changed bytes/modes/aliases.
    assert validate_image_archive(image, c["manifest"], identity)["status"] == "GREEN"
    with pytest.raises(ValueError, match="ROOTFS"):
        verify_saved_app_rootfs(image, c["manifest"])


def test_opaque_whiteouts_and_repeated_compressed_layers_replay_native_order(frozen_payload):
    c, output, _, _ = frozen_payload
    image = output / "porota-predeploy-image.tar.gz"
    with tarfile.open(image, "r:gz") as archive:
        base = archive.extractfile("layer.tar").read()
    def layer(entries):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for name, value in entries:
                info = tarfile.TarInfo(name); info.size = len(value); info.mode = 0o644
                archive.addfile(info, io.BytesIO(value))
        return stream.getvalue()
    layers = [base, layer([("app/stale.py", b"stale"), ("app/assets/stale.txt", b"stale")]),
        layer([("app/.wh.stale.py", b""), ("app/assets/.wh..wh..opq", b""),
               ("app/assets/new.runtime.asset", (c["image"] / "assets/new.runtime.asset").read_bytes())]),
        layer([])]
    references = (0, 1, 2, 3, 3)
    config = canonical_bytes({"config": {"Labels": c["labels"]}, "rootfs": {"type": "layers", "diff_ids": [
        "sha256:" + hashlib.sha256(layers[index]).hexdigest() for index in references]}})
    identity = "sha256:" + hashlib.sha256(config).hexdigest()
    manifest = [{"Config": "config.json", "Layers": [f"layer-{index}.tar.gz" for index in references], "RepoTags": ["fixture:exact"]}]
    members = [(f"layer-{index}.tar.gz", gzip.compress(value, mtime=0)) for index, value in enumerate(layers)]
    members += [("config.json", config), ("manifest.json", canonical_bytes(manifest))]
    with tarfile.open(image, "w:gz") as archive:
        for name, value in members:
            info = tarfile.TarInfo(name); info.size = len(value); archive.addfile(info, io.BytesIO(value))
    assert validate_image_archive(image, c["manifest"], identity)["status"] == "GREEN"
    replay = verify_saved_app_rootfs(image, c["manifest"])
    assert replay["status"] == "GREEN" and replay["ordered_layer_references"] == 5 and replay["unique_layer_blobs"] == 4


def retag_fixture(output, candidate_sha):
    path = output / "porota-predeploy-image.tar.gz"
    with tarfile.open(path, "r:gz") as archive:
        members = [(copy(info), archive.extractfile(info).read()) for info in archive]
    with tarfile.open(path, "w:gz") as archive:
        for info, data in members:
            if info.name == "manifest.json":
                value = json.loads(data); value[0]["RepoTags"] = ["porota-predeploy-v2:" + candidate_sha]
                data = canonical_bytes(value); info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    frozen = json.loads((output / "porota-frozen-candidate.json").read_text())
    frozen["image_tar_sha256"] = sha256_file(path)
    (output / "porota-frozen-candidate.json").write_bytes(canonical_bytes(frozen))


@pytest.mark.parametrize("wrong_id", [False, True])
def test_downloaded_image_load_and_offline_smoke_require_exact_actual_id(frozen_payload, wrong_id):
    c, output, binding, frozen = frozen_payload
    retag_fixture(output, binding["candidate_sha"])
    calls = []
    def docker(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["docker", "image", "inspect"]:
            stdout = "sha256:" + "f"*64 if wrong_id else frozen["image_id"]
        elif argv[:2] == ["docker", "run"]: stdout = "POROTA_PUBLISHED_EXACT_IMAGE_IMPORT_AND_CLOSURE=GREEN"
        else: stdout = "Loaded image"
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")
    kwargs = dict(candidate_sha=binding["candidate_sha"], image_ref="porota-predeploy-v2:"+binding["candidate_sha"],
                  deadline=time.monotonic()+60, run=docker)
    if wrong_id:
        with pytest.raises(ValueError, match="LOADED_IMAGE_ID_MISMATCH"):
            verify_loaded_image_and_imports(output, **kwargs)
        assert len(calls) == 2
    else:
        result = verify_loaded_image_and_imports(output, **kwargs)
        assert result["status"] == "GREEN" and result["network"] == "NONE"
        assert calls[-1][calls[-1].index("--network") + 1] == "none"
        assert "-v" not in calls[-1] and "--mount" not in calls[-1]
    assert not any(argv[1] == "build" for argv in calls)


def test_evidence_only_is_small_and_contains_no_image_or_bundle(frozen_payload, tmp_path):
    c, output, binding, frozen = frozen_payload
    receipt = verify_frozen_payload(c["repo"], output, binding, c["manifest"]["candidate_tree_sha"])
    rootfs = verify_saved_app_rootfs(output / "porota-predeploy-image.tar.gz", c["manifest"])
    evidence = tmp_path / "secondary"
    result = write_evidence(evidence, binding=binding, payload={"extracted_root": output, "receipt": receipt},
        rootfs=rootfs, download={"status": "GREEN"}, loaded_image_id=frozen["image_id"])
    assert result["promotable"] is False and result["evidence_payload_bytes"] < MAX_EVIDENCE_BYTES < MAX_SECONDARY_ARTIFACT_BYTES
    assert all(path.suffix == ".json" for path in evidence.iterdir())
    index = json.loads((evidence / "evidence-inventory.json").read_text())
    assert all(row["sha256"] == sha256_file(evidence/name) for name,row in index["files"].items())
    report = json.loads((evidence / "published-primary-verification.json").read_text())
    assert report["primary"]["artifact_id"] == binding["artifact_id"] and report["image_rebuilt"] is False


def test_replay_errors_do_not_disclose_signed_urls_headers_or_tokens(monkeypatch, capsys, tmp_path):
    def fail(*args, **kwargs): raise ValueError("https://signed.example?private-token=unit-test-secret")
    monkeypatch.setattr("scripts.porota_published_artifact_evidence.current_primary_binding", fail)
    assert main(["--candidate-sha", "a"*40, "--tree-sha", "b"*40, "--artifact-id", "303",
        "--upload-digest", "d"*64, "--image-ref", "porota-predeploy-v2:"+"a"*40,
        "--evidence-root", str(tmp_path / "evidence")]) == 1
    output = capsys.readouterr().out
    assert "RED" in output and "signed.example" not in output and "unit-test-secret" not in output


def test_workflow_secondary_cannot_replace_primary_or_trigger_another_build():
    workflow = yaml.safe_load(Path(".github/workflows/porota-predeploy-v2.yml").read_text())
    steps = workflow["jobs"]["artifact-gate"]["steps"]
    primary = next(i for i, step in enumerate(steps) if step.get("id") == "frozen_primary")
    replay = next(i for i, step in enumerate(steps) if "Replay exact published primary" in step["name"])
    secondary = next(i for i, step in enumerate(steps) if step.get("id") == "primary_evidence_only")
    assert primary < replay < secondary
    assert "porota-predeploy-image.tar.gz" in steps[primary]["with"]["path"]
    assert "evidence-only" in steps[secondary]["with"]["name"] and steps[secondary]["with"]["path"].endswith("*.json")
    assert "docker build" not in steps[replay]["run"]
    assert artifact_name(approved_origin()[0]).startswith("porota-predeploy-v2-" + "a"*40)
