"""A782: frozen-image byte provenance cannot be inferred from SHA labels."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.porota_validate_deploy_artifact import (
    SourceImageProvenanceError,
    exact_git_image_bytes,
    git_tracked_runtime_files,
    is_runtime_relevant,
    validate,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True,
    ).strip()


def _write(root: Path, name: str, payload: bytes) -> Path:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


def _fixture(tmp_path: Path) -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    image = tmp_path / "app"
    repo.mkdir()
    image.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.name", "CI proof")
    _git(repo, "config", "user.email", "ci-proof@example.invalid")
    tracked = {
        "worker.py": b"COUNT = 7\n",
        "ops/policy/rc6-dynamic-capacity-v1.json": b'{"max_active": 1}\n',
        "ops/policy/porota-policy.yaml": b"paper_only: true\n",
        "ops/policy/rc6-byma-reviewed-authority-v1.json": b'{"source": "BYMA"}\n',
        "docs/declared-contract.json": b'{"version": 2}\n',
    }
    for name, content in tracked.items():
        _write(repo, name, content)
        _write(image, name, content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "frozen source")
    return repo, image, _git(repo, "rev-parse", "HEAD")


def _guard(repo: Path, image: Path, sha: str) -> dict:
    return exact_git_image_bytes(repo, image, sha)


def test_exact_git_blob_bytes_green_including_all_policy_formats(tmp_path):
    repo, image, sha = _fixture(tmp_path)
    result = _guard(repo, image, sha)
    assert result["status"] == "GREEN"
    assert result["verified_tracked_image_files"] == 5
    assert result["source"] == "COMMITTED_GIT_BLOB_NOT_WORKTREE"
    assert is_runtime_relevant("ops/policy/rc6-dynamic-capacity-v1.json")
    assert is_runtime_relevant("ops/policy/porota-policy.yaml")
    assert is_runtime_relevant("ops/policy/rc6-byma-reviewed-authority-v1.json")


@pytest.mark.parametrize(
    "name,changed",
    [
        ("worker.py", b"COUNT = 999\n"),
        ("ops/policy/rc6-dynamic-capacity-v1.json", b'{"max_active": 999}\n'),
        ("ops/policy/rc6-byma-reviewed-authority-v1.json", b'{"source": "unknown"}\n'),
        ("ops/policy/porota-policy.yaml", b"paper_only: false\n"),
        ("docs/declared-contract.json", b'{"version": 3}\n'),
    ],
)
def test_adversarial_tracked_source_drift_fails_although_completeness_green(
    tmp_path, name, changed,
):
    repo, image, sha = _fixture(tmp_path)
    _write(image, name, changed)
    # Existing closure alone is precisely the old false GREEN; the new guard
    # compares image bytes with the committed blob rather than image self-hash.
    assert validate(repo, image, git_tracked_runtime_files(repo))["status"] == "GREEN"
    result = _guard(repo, image, sha)
    assert result["status"] == "FAILED"
    assert {row["path"] for row in result["image_tracked_source_mismatches"]} == {name}
    assert result["image_tracked_source_mismatches"][0]["reason"] == "SOURCE_IMAGE_BYTES_DRIFT"


def test_policy_config_absence_fails_original_completeness(tmp_path):
    repo, image, sha = _fixture(tmp_path)
    (image / "ops/policy/porota-policy.yaml").unlink()
    result = validate(repo, image, git_tracked_runtime_files(repo))
    assert result["status"] == "FAILED"
    assert "ops/policy/porota-policy.yaml" in result["missing_runtime_files"]


@pytest.mark.parametrize("stage", [False, True])
def test_dirty_checkout_rejected_not_smuggled_as_good_candidate(tmp_path, stage):
    repo, image, sha = _fixture(tmp_path)
    _write(repo, "worker.py", b"COUNT = 99\n")
    if stage:
        _git(repo, "add", "worker.py")
    result = _guard(repo, image, sha)
    assert result["status"] == "FAILED"
    assert result["dirty_tracked_files"] == ["worker.py"]


@pytest.mark.parametrize("name", ["stray.py", "ops/policy/unknown.yaml"])
def test_untracked_runtime_source_in_checkout_rejected(tmp_path, name):
    repo, image, sha = _fixture(tmp_path)
    _write(repo, name, b"unreviewed: true\n")
    result = _guard(repo, image, sha)
    assert result["status"] == "FAILED"
    assert result["untracked_runtime_files"] == [name]


def test_untracked_python_in_extracted_image_is_not_ignored(tmp_path):
    repo, image, sha = _fixture(tmp_path)
    _write(image, "surprise_worker.py", b"EXECUTE = True\n")
    result = _guard(repo, image, sha)
    assert result["status"] == "FAILED"
    assert result["untracked_image_runtime_files"] == ["surprise_worker.py"]


def test_untracked_compiled_native_binary_keeps_separate_abi_guard(tmp_path):
    repo, image, _ = _fixture(tmp_path)
    native_c = b"PyMODINIT_FUNC PyInit__rc6_json_bound(void) { return 0; }\n"
    _write(repo, "rc6_shadow_runtime/_rc6_json_bound.c", native_c)
    _write(image, "rc6_shadow_runtime/_rc6_json_bound.c", native_c)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "native input")
    sha = _git(repo, "rev-parse", "HEAD")
    _write(image, "rc6_shadow_runtime/_rc6_json_bound.so", b"mock compiled ABI payload")
    assert _guard(repo, image, sha)["status"] == "GREEN"
    assert validate(repo, image, git_tracked_runtime_files(repo))["native_extensions"][0]["module"] == "_rc6_json_bound"


def test_image_symlink_alias_rejected(tmp_path):
    repo, image, sha = _fixture(tmp_path)
    source = image / "worker.py"
    source.unlink()
    source.symlink_to(repo / "worker.py")
    result = _guard(repo, image, sha)
    assert result["status"] == "FAILED"
    assert {row["reason"] for row in result["image_tracked_source_mismatches"]} == {"SOURCE_IMAGE_ALIAS_UNSAFE"}


def test_image_hardlink_alias_rejected(tmp_path):
    repo, image, sha = _fixture(tmp_path)
    source = image / "worker.py"
    source.unlink()
    os.link(repo / "worker.py", source)
    result = _guard(repo, image, sha)
    assert result["status"] == "FAILED"
    assert {row["reason"] for row in result["image_tracked_source_mismatches"]} == {"SOURCE_IMAGE_ALIAS_UNSAFE"}


def test_image_parent_symlink_alias_rejected(tmp_path):
    repo, image, sha = _fixture(tmp_path)
    target = image / "ops/policy"
    copy = tmp_path / "aliased"
    shutil.copytree(target, copy)
    shutil.rmtree(target)
    target.symlink_to(copy, target_is_directory=True)
    result = _guard(repo, image, sha)
    assert result["status"] == "FAILED"
    assert result["image_symlink_paths"] == ["ops/policy"]


def test_head_must_match_exact_candidate_sha(tmp_path):
    repo, image, _ = _fixture(tmp_path)
    with pytest.raises(SourceImageProvenanceError, match="GIT_HEAD_CANDIDATE_SHA_MISMATCH"):
        _guard(repo, image, "f" * 40)


def test_cli_gate_fails_on_runtime_policy_tamper(tmp_path):
    repo, image, sha = _fixture(tmp_path)
    _write(image, "ops/policy/rc6-dynamic-capacity-v1.json", b'{"max_active": 20}\n')
    output = tmp_path / "manifest.json"
    validator = Path(__file__).resolve().parents[1] / "scripts/porota_validate_deploy_artifact.py"
    run = subprocess.run(
        [sys.executable, str(validator), "--repo-root", str(repo),
         "--artifact-root", str(image), "--candidate-sha", sha,
         "--manifest-out", str(output)],
        text=True, capture_output=True, check=False,
    )
    assert run.returncode == 1
    assert json.loads(output.read_text(encoding="utf-8"))["source_image_provenance"]["status"] == "FAILED"
    assert "POROTA_ARTIFACT_INTEGRITY=GREEN" not in run.stdout


def test_predeploy_requires_exact_commit_blob_provenance_before_artifact():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/porota-predeploy-v2.yml").read_text(encoding="utf-8")
    assert '--candidate-sha "$CANDIDATE_SHA"' in workflow
    assert "x['source_image_provenance']['status']=='GREEN'" in workflow
    assert "POROTA_IMAGE_GIT_BLOB_PROVENANCE=GREEN" in workflow
    assert workflow.index("POROTA_IMAGE_GIT_BLOB_PROVENANCE=GREEN") < workflow.index(
        "Frozen candidate fail-closed contract",
    )
