"""Offline adversarial Git -> artifact proofs for Issue #465, front E."""
from __future__ import annotations

import copy
import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

from scripts.porota_artifact_provenance import (
    BUNDLE_MANIFEST_NAME, SOURCE_MANIFEST_NAME, ProvenanceError, bundle_manifest,
    canonical_bytes, create_source_manifest, dockerignore_exclusion, sha256_file,
    validate_bundle, validate_image, validate_image_archive, validate_image_labels,
    verify_source_manifest,
    verify_build_context,
)
from scripts.porota_build_deploy_bundle_v2 import build_bundle, select_bundle_paths
from scripts.porota_validate_deploy_artifact import validate


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def write(root: Path, rel: str, data: bytes) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@pytest.fixture
def candidate(tmp_path):
    repo = tmp_path / "candidate"
    repo.mkdir()
    for rel, content in {
        "Dockerfile": b"FROM scratch\nCOPY . /app\n",
        ".dockerignore": b"docs/\n*.pdf\n__pycache__/\n*.py[cod]\n",
        "app.py": b"import worker\nVALUE = 7\n",
        "worker.py": b"VALUE = 10\n",
        "ops/policy/paper.json": b'{"mode":"PAPER","real_orders_sent":0}\n',
        "assets/new.runtime.asset": b"automatic enumeration: unknown suffix\n",
        "scripts/run": b"#!/bin/sh\nexit 0\n",
        "tests/test_fixture.py": b"def test_case(): pass\n",
        ".github/workflows/build.yml": b"name: test\n",
        "docs/audit.md": b"Documentation excluded by versioned Docker policy\n",
        "README.md": b"Source includes documentation bytes\n",
    }.items():
        write(repo, rel, content)
    (repo / "scripts/run").chmod(0o755)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Offline Provenance Test")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "offline fixture source")
    manifest = create_source_manifest(repo)
    source = tmp_path / "source.json"
    source.write_bytes(canonical_bytes(manifest))
    image = tmp_path / "image-app"
    image.mkdir()
    for row in manifest["files"]:
        if row["image_required"]:
            target = image / row["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repo / row["path"], target)
    write(image, SOURCE_MANIFEST_NAME, source.read_bytes())
    labels = {
        "porota.commit": manifest["candidate_sha"],
        "porota.tree": manifest["candidate_tree_sha"],
        "porota.predeploy": "v2",
        "porota.source-manifest-sha256": sha256_file(source),
    }
    inspect = tmp_path / "inspect.json"
    inspect.write_text(json.dumps([{"Id": "sha256:" + "a" * 64, "Config": {"Labels": labels}}]))
    bundle = tmp_path / "bundle.tgz"
    bundle_meta = tmp_path / "bundle-manifest.json"
    build_bundle(repo, bundle, bundle_meta, source)
    return {"repo": repo, "source": source, "manifest": manifest, "image": image,
            "inspect": inspect, "labels": labels, "bundle": bundle, "bundle_meta": bundle_meta}


def check_image(c):
    return validate_image(c["repo"], c["image"], c["source"], c["inspect"])


def check_bundle(c):
    return validate_bundle(c["repo"], c["bundle"], c["bundle_meta"], c["source"])


def mutate_bundle(c, *, changed=None, missing=None, extra=None, member_kind=None, executable=None):
    """Repack valid gzip and honestly update outer digest to attack inner proof."""
    with tarfile.open(c["bundle"], "r:gz") as archive:
        rows = [(copy.copy(item), archive.extractfile(item).read()) for item in archive]
    with tarfile.open(c["bundle"], "w:gz") as archive:
        for info, payload in rows:
            if missing == info.name:
                continue
            if changed and changed[0] == info.name:
                payload = changed[1]
                info.size = len(payload)
            if member_kind and member_kind[0] == info.name:
                info.type = member_kind[1]
                info.linkname = "app.py"
                info.size = 0
            if executable == info.name:
                info.mode |= 0o111
            archive.addfile(info, io.BytesIO(payload) if info.isfile() else None)
        if extra:
            info = tarfile.TarInfo(extra[0])
            info.size = len(extra[1])
            archive.addfile(info, io.BytesIO(extra[1]))
    meta = json.loads(c["bundle_meta"].read_bytes())
    meta["bundle_sha256"] = sha256_file(c["bundle"])
    c["bundle_meta"].write_bytes(canonical_bytes(meta))


def test_clean_exact_checkout_image_bundle_are_green(candidate):
    c = candidate
    source = verify_source_manifest(c["repo"], c["source"])
    assert source["candidate_sha"] == git(c["repo"], "rev-parse", "HEAD")
    assert source["candidate_tree_sha"] == git(c["repo"], "rev-parse", "HEAD^{tree}")
    assert source["file_count"] == len(git(c["repo"], "ls-files").splitlines())
    assert check_image(c)["status"] == check_bundle(c)["status"] == "GREEN"
    assert check_image(c)["source_files_excluded_by_versioned_dockerignore"] == 1
    assert source["real_orders_sent"] == 0


def test_source_complete_enumeration_includes_new_asset_without_allowlist(candidate):
    c = candidate
    source = {row["path"]: row for row in c["manifest"]["files"]}
    assert source["assets/new.runtime.asset"]["bundle_required"] is True
    assert source["scripts/run"]["bundle_required"] is True
    assert source["docs/audit.md"]["image_exclusion"] == {"authority": ".dockerignore", "rule": "docs/"}
    assert select_bundle_paths(list(source)) == sorted(p for p, row in source.items() if row["bundle_required"])


def test_source_manifest_and_bundle_reproducible(candidate, tmp_path):
    c = candidate
    assert canonical_bytes(create_source_manifest(c["repo"])) == c["source"].read_bytes()
    second = tmp_path / "second.tgz"
    build_bundle(c["repo"], second, source_manifest_path=c["source"])
    assert second.read_bytes() == c["bundle"].read_bytes()


@pytest.mark.parametrize("mutation", ["byte", "mode", "staged", "missing", "symlink", "hardlink"])
def test_checkout_mutations_cannot_be_manifest_authority(candidate, tmp_path, mutation):
    c = candidate
    source = c["repo"] / "worker.py"
    if mutation in {"byte", "staged"}:
        source.write_bytes(b"VALUE = 11\n")
        if mutation == "staged":
            git(c["repo"], "add", "worker.py")
    elif mutation == "mode":
        source.chmod(0o755)
    elif mutation == "missing":
        source.unlink()
    elif mutation == "symlink":
        same = tmp_path / "same-byte-source"
        shutil.copy2(source, same)
        source.unlink()
        source.symlink_to(same)
    else:
        os.link(source, tmp_path / "source-hardlink")
    with pytest.raises(ProvenanceError):
        create_source_manifest(c["repo"])


@pytest.mark.parametrize("field", ["candidate_sha", "candidate_tree_sha", "manifest_sha256", "file_count", "files"])
def test_modified_manifest_rejected_even_if_valid_json(candidate, field):
    c = candidate
    altered = copy.deepcopy(c["manifest"])
    altered[field] = [] if field == "files" else "tampered"
    c["source"].write_bytes(canonical_bytes(altered))
    with pytest.raises(ProvenanceError, match="SOURCE_MANIFEST_GIT_MISMATCH"):
        check_image(c)


def test_manifest_cannot_self_certify_changed_hash_by_recomputing_own_digest(candidate):
    c = candidate
    altered = copy.deepcopy(c["manifest"])
    row = next(row for row in altered["files"] if row["path"] == "app.py")
    row["sha256"] = "f" * 64
    altered.pop("manifest_sha256")
    altered["manifest_sha256"] = hashlib.sha256(canonical_bytes(altered)).hexdigest()
    c["source"].write_bytes(canonical_bytes(altered))
    with pytest.raises(ProvenanceError, match="SOURCE_MANIFEST_GIT_MISMATCH"):
        check_image(c)


def test_duplicate_json_keys_rejected(candidate):
    candidate["source"].write_bytes(b'{"status":"GREEN","status":"RED"}')
    with pytest.raises(ProvenanceError, match="DUPLICATE_JSON_KEY"):
        check_image(candidate)


@pytest.mark.parametrize("argument", ["candidate_sha", "candidate_tree_sha"])
def test_explicit_candidate_authority_cannot_disagree_with_checkout(candidate, argument):
    with pytest.raises(ProvenanceError, match="CANDIDATE_(SHA|TREE)_MISMATCH"):
        create_source_manifest(candidate["repo"], **{argument: "f" * 40})


@pytest.mark.parametrize("field", ["porota.commit", "porota.tree", "porota.source-manifest-sha256", "porota.predeploy"])
def test_wrong_or_missing_image_labels_rejected(candidate, field):
    c = candidate
    changed = copy.deepcopy(c["labels"])
    changed[field] = "wrong"
    with pytest.raises(ProvenanceError, match="IMAGE_LABEL_MISMATCH"):
        validate_image_labels(changed, c["manifest"])
    changed.pop(field)
    with pytest.raises(ProvenanceError, match="IMAGE_LABEL_MISMATCH"):
        validate_image_labels(changed, c["manifest"])


@pytest.mark.parametrize("rel", ["app.py", "ops/policy/paper.json", "assets/new.runtime.asset", ".github/workflows/build.yml"])
def test_one_byte_tamper_in_runtime_or_tracked_context_rejected(candidate, rel):
    c = candidate
    path = c["image"] / rel
    data = bytearray(path.read_bytes())
    data[-2] ^= 1
    path.write_bytes(data)
    with pytest.raises(ProvenanceError, match="IMAGE_SOURCE_BYTE_MISMATCH"):
        check_image(c)


@pytest.mark.parametrize("rel", ["app.py", "assets/new.runtime.asset", "tests/test_fixture.py", SOURCE_MANIFEST_NAME])
def test_missing_source_or_embedded_manifest_rejected(candidate, rel):
    c = candidate
    (c["image"] / rel).unlink()
    with pytest.raises(ProvenanceError, match="IMAGE_(MISSING_SOURCE|SOURCE_MANIFEST_MISMATCH)"):
        check_image(c)


@pytest.mark.parametrize("rel", ["unexpected.py", "tests/not-tracked.py", ".github/hidden.sh", "__pycache__/app.cpython-311.pyc", "bin/unlisted"])
def test_extra_executable_or_hidden_source_is_rejected(candidate, rel):
    c = candidate
    write(c["image"], rel, b"exec('untrusted')\n")
    (c["image"] / rel).chmod(0o755)
    with pytest.raises(ProvenanceError, match="IMAGE_UNEXPECTED_PATH"):
        check_image(c)


def test_non_executable_known_file_cannot_be_made_executable(candidate):
    (candidate["image"] / "README.md").chmod(0o755)
    with pytest.raises(ProvenanceError, match="IMAGE_SOURCE_MODE_MISMATCH"):
        check_image(candidate)


def test_embedded_source_metadata_cannot_be_executable(candidate):
    (candidate["image"] / SOURCE_MANIFEST_NAME).chmod(0o755)
    with pytest.raises(ProvenanceError, match="IMAGE_SOURCE_MANIFEST_EXECUTABLE"):
        check_image(candidate)


@pytest.mark.parametrize("rel", ["nottracked.py", "tests/new.py", ".github/new.sh", "bin/new", "new.json"])
def test_untracked_context_rejected_before_the_only_build(candidate, rel):
    c = candidate
    write(c["repo"], SOURCE_MANIFEST_NAME, c["source"].read_bytes())
    write(c["repo"], rel, b"untracked context bytes")
    with pytest.raises(ProvenanceError, match="BUILD_CONTEXT_UNTRACKED_PATH"):
        verify_build_context(c["repo"], c["manifest"], c["source"])


def test_context_uses_only_versioned_exclusions_and_exact_generated_metadata(candidate):
    c = candidate
    write(c["repo"], SOURCE_MANIFEST_NAME, c["source"].read_bytes())
    write(c["repo"], "docs/private-note.md", b"excluded by role")
    write(c["repo"], "__pycache__/app.cpython-311.pyc", b"excluded bytecode")
    assert verify_build_context(c["repo"], c["manifest"], c["source"])["status"] == "GREEN"
    write(c["repo"], SOURCE_MANIFEST_NAME, b"{}")
    with pytest.raises(ProvenanceError, match="BUILD_CONTEXT_SOURCE_MANIFEST_MISMATCH"):
        verify_build_context(c["repo"], c["manifest"], c["source"])


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_artifact_links_to_identical_bytes_rejected(candidate, tmp_path, kind):
    c = candidate
    path = c["image"] / "app.py"
    if kind == "symlink":
        path.unlink()
        path.symlink_to(c["repo"] / "app.py")
    else:
        os.link(path, tmp_path / "artifact-hardlink")
    with pytest.raises(ProvenanceError, match="NON_REGULAR_ARTIFACT_PATH|ARTIFACT_HARDLINK"):
        check_image(c)


def test_artifact_root_symlink_rejected(candidate, tmp_path):
    link = tmp_path / "image-link"
    link.symlink_to(candidate["image"], target_is_directory=True)
    with pytest.raises(ProvenanceError, match="NON_REGULAR_ARTIFACT_ROOT"):
        validate_image(candidate["repo"], link, candidate["source"], candidate["inspect"])


def test_image_embedded_manifest_tamper_detected(candidate):
    write(candidate["image"], SOURCE_MANIFEST_NAME, b"{}\n")
    with pytest.raises(ProvenanceError, match="IMAGE_SOURCE_MANIFEST_MISMATCH"):
        check_image(candidate)


@pytest.mark.parametrize("rel", ["app.py", SOURCE_MANIFEST_NAME, BUNDLE_MANIFEST_NAME])
def test_bundle_byte_tamper_with_honest_outer_rehash_rejected(candidate, rel):
    c = candidate
    mutate_bundle(c, changed=(rel, b"bad bytes\n"))
    with pytest.raises(ProvenanceError, match="BUNDLE_.*MISMATCH"):
        check_bundle(c)


@pytest.mark.parametrize("rel", ["worker.py", SOURCE_MANIFEST_NAME, BUNDLE_MANIFEST_NAME])
def test_bundle_missing_source_rejected(candidate, rel):
    mutate_bundle(candidate, missing=rel)
    with pytest.raises(ProvenanceError, match="BUNDLE_MISSING_SOURCE"):
        check_bundle(candidate)


@pytest.mark.parametrize("rel", ["bad.sh", "hidden/new.source", "../escape", "/absolute", "app.py"])
def test_bundle_extra_duplicate_or_unsafe_path_rejected(candidate, rel):
    mutate_bundle(candidate, extra=(rel, b"malicious\n"))
    with pytest.raises(ProvenanceError, match="BUNDLE_UNEXPECTED_PATH|UNSAFE_PATH|DUPLICATE_ARCHIVE_PATH"):
        check_bundle(candidate)


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_bundle_links_rejected(candidate, kind):
    mutate_bundle(candidate, member_kind=("app.py", kind))
    with pytest.raises(ProvenanceError, match="NON_REGULAR_ARCHIVE_PATH"):
        check_bundle(candidate)


def test_bundle_external_manifest_cannot_remove_file_and_bless_self(candidate):
    c = candidate
    altered = json.loads(c["bundle_meta"].read_bytes())
    altered["files"] = altered["files"][1:]
    altered["file_count"] -= 1
    c["bundle_meta"].write_bytes(canonical_bytes(altered))
    with pytest.raises(ProvenanceError, match="BUNDLE_MANIFEST_GIT_MISMATCH"):
        check_bundle(c)


@pytest.mark.parametrize("rel", ["README.md", SOURCE_MANIFEST_NAME, BUNDLE_MANIFEST_NAME])
def test_bundle_unexpected_executable_mode_rejected(candidate, rel):
    mutate_bundle(candidate, executable=rel)
    with pytest.raises(ProvenanceError, match="BUNDLE_(SOURCE_MODE_MISMATCH|GENERATED_METADATA_EXECUTABLE)"):
        check_bundle(candidate)


@pytest.mark.parametrize("mutation", ["missing_footer", "corrupt_footer", "trailing_garbage"])
def test_bundle_gzip_envelope_corruption_rejected_even_with_updated_outer_digest(candidate, mutation):
    c = candidate
    data = c["bundle"].read_bytes()
    if mutation == "missing_footer":
        data = data[:-8]
    elif mutation == "corrupt_footer":
        data = data[:-8] + b"\0" * 8
    else:
        data += b"garbage"
    c["bundle"].write_bytes(data)
    meta = json.loads(c["bundle_meta"].read_bytes())
    meta["bundle_sha256"] = sha256_file(c["bundle"])
    c["bundle_meta"].write_bytes(canonical_bytes(meta))
    with pytest.raises((ProvenanceError, tarfile.TarError, EOFError, OSError)):
        check_bundle(c)


@pytest.mark.parametrize("rel", ["app.py", "ops/policy/paper.json", "assets/new.runtime.asset"])
def test_runtime_source_cannot_be_declared_a_dockerignore_exception(candidate, rel):
    c = candidate
    with (c["repo"] / ".dockerignore").open("a") as handle:
        handle.write(rel + "\n")
    git(c["repo"], "add", ".dockerignore")
    git(c["repo"], "commit", "-qm", "exclude required runtime")
    with pytest.raises(ProvenanceError, match="RUNTIME_SOURCE_EXCLUDED_BY_DOCKERIGNORE"):
        create_source_manifest(c["repo"])


@pytest.mark.parametrize(("path", "rules", "excluded"), [
    ("docs/audit.md", "docs/", "docs/"),
    ("docs/nested/audit.md", "docs/", "docs/"),
    (".env.example", ".env.*\n!.env.example", None),
    ("root/nested/data.pyc", "**/*.py[cod]", "**/*.py[cod]"),
    ("data.pyc", "**/*.py[cod]", "**/*.py[cod]"),
    ("app.py", "docs/\n*.py[cod]", None),
    ("root/keep.json", "root/*\n!root/keep.json", None),
])
def test_versioned_context_exclusions_and_negation(path, rules, excluded):
    assert dockerignore_exclusion(path, rules) == excluded


def saved_image(c, path: Path, *, mutate_labels=False, mutate_layer=False,
                compressed_layer=False, duplicate=False):
    layer_io = io.BytesIO()
    with tarfile.open(fileobj=layer_io, mode="w") as layer:
        info = tarfile.TarInfo("app/app.py")
        data = (c["repo"] / "app.py").read_bytes()
        info.size = len(data)
        layer.addfile(info, io.BytesIO(data))
    uncompressed = layer_io.getvalue()
    labels = {**c["labels"]}
    if mutate_labels:
        labels["porota.tree"] = "wrong"
    config = canonical_bytes({"config": {"Labels": labels}, "rootfs": {
        "type": "layers", "diff_ids": ["sha256:" + hashlib.sha256(uncompressed).hexdigest()]}})
    image_id = "sha256:" + hashlib.sha256(config).hexdigest()
    payload = bytearray(uncompressed)
    if mutate_layer:
        payload[-1] ^= 1
    payload = gzip.compress(payload, mtime=0) if compressed_layer else bytes(payload)
    saved_manifest = [{"Config": "config.json", "Layers": ["layer.tar"], "RepoTags": ["fixture:exact"]}]
    with tarfile.open(path, "w:gz") as outer:
        for name, data in [("layer.tar", payload), ("config.json", config),
                           ("manifest.json", canonical_bytes(saved_manifest))]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            outer.addfile(info, io.BytesIO(data))
            if duplicate and name == "layer.tar":
                outer.addfile(info, io.BytesIO(data))
    return image_id


@pytest.mark.parametrize("compressed", [False, True])
def test_saved_image_raw_config_and_layer_diffids_rehash_without_rebuild(candidate, tmp_path, compressed):
    path = tmp_path / "image.tar.gz"
    image_id = saved_image(candidate, path, compressed_layer=compressed)
    result = validate_image_archive(path, candidate["manifest"], image_id)
    assert result["status"] == "GREEN"
    assert result["image_layers_verified"] == 1
    assert result["image_tar_sha256"] == sha256_file(path)


@pytest.mark.parametrize("mutation", ["wrong_id", "wrong_tree_label", "corrupt_layer", "duplicate_layer", "truncated"])
def test_saved_image_mutations_rejected(candidate, tmp_path, mutation):
    path = tmp_path / "image.tar.gz"
    image_id = saved_image(candidate, path, mutate_labels=mutation == "wrong_tree_label",
                           mutate_layer=mutation == "corrupt_layer", duplicate=mutation == "duplicate_layer")
    if mutation == "wrong_id":
        image_id = "sha256:" + "b" * 64
    if mutation == "truncated":
        path.write_bytes(path.read_bytes()[:24])
    with pytest.raises((ProvenanceError, tarfile.TarError, EOFError)):
        validate_image_archive(path, candidate["manifest"], image_id)


def test_saved_image_missing_gzip_footer_rejected(candidate, tmp_path):
    path = tmp_path / "image.tar.gz"
    image_id = saved_image(candidate, path)
    path.write_bytes(path.read_bytes()[:-8])
    with pytest.raises((ProvenanceError, tarfile.TarError, EOFError, OSError)):
        validate_image_archive(path, candidate["manifest"], image_id)


def referenced_image(c, path, *, compressed=False, content_addressed=True,
                     references=(0, 1, 1, 2), layer_count=3, payload_size=1,
                     config_mutation=None, manifest_mutation=None, member_mutation=None,
                     layer_sources=False):
    """Real save format: unique stored blobs, ordered possibly repeated refs."""
    raw_layers = []
    for index in range(layer_count):
        if index == 1:
            raw_layers.append(b"\0" * 1024)  # Docker's native empty layer.
            continue
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as inner:
            payload = bytes([65 + index]) * payload_size
            info = tarfile.TarInfo("app/layer-" + str(index) + ".txt")
            info.size = len(payload)
            inner.addfile(info, io.BytesIO(payload))
        raw_layers.append(buffer.getvalue())
    stored = [gzip.compress(layer, mtime=0) if compressed else layer for layer in raw_layers]
    paths = [("blobs/sha256/" + hashlib.sha256(layer).hexdigest()) if content_addressed
             else "legacy-" + str(index) + "/layer.tar" for index, layer in enumerate(stored)]
    config = {"config": {"Labels": c["labels"]}, "rootfs": {"type": "layers", "diff_ids": [
        "sha256:" + hashlib.sha256(raw_layers[index]).hexdigest() for index in references]}}
    if config_mutation:
        config_mutation(config)
    config_bytes = canonical_bytes(config)
    image_id = "sha256:" + hashlib.sha256(config_bytes).hexdigest()
    config_path = "blobs/sha256/" + image_id[7:] if content_addressed else "config.json"
    manifest = [{"Config": config_path, "Layers": [paths[index] for index in references],
                 "RepoTags": ["fixture:exact"]}]
    if layer_sources:
        manifest[0]["LayerSources"] = {
            "sha256:" + hashlib.sha256(raw_layers[index]).hexdigest(): {
                "digest": "sha256:" + hashlib.sha256(stored[index]).hexdigest(),
                "size": len(stored[index]),
                "mediaType": "application/vnd.oci.image.layer.v1.tar" + ("+gzip" if compressed else ""),
            } for index in references
        }
    if manifest_mutation:
        manifest_mutation(manifest)
    # Physical order differs from image application order, and every blob is
    # stored once. Metadata references cannot replace exact config authority.
    members = [(name, data, tarfile.REGTYPE) for name, data in reversed(list(zip(paths, stored)))]
    members += [(manifest[0]["Config"], config_bytes, tarfile.REGTYPE),
                ("manifest.json", canonical_bytes(manifest), tarfile.REGTYPE)]
    if member_mutation:
        members = member_mutation(members, paths)
    with tarfile.open(path, "w:gz") as archive:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.size = len(data) if kind == tarfile.REGTYPE else 0
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                info.linkname = paths[0]
            archive.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
    return image_id, sum(len(raw_layers[index]) for index in references)


@pytest.mark.parametrize("compressed", [False, True])
@pytest.mark.parametrize("content_addressed", [False, True])
def test_saved_image_ordered_repeated_blob_references_are_verified(candidate, tmp_path, compressed, content_addressed):
    path = tmp_path / "repeated-image.tar.gz"
    image_id, logical_bytes = referenced_image(candidate, path, compressed=compressed,
                                               content_addressed=content_addressed)
    result = validate_image_archive(path, candidate["manifest"], image_id)
    assert result["status"] == "GREEN" and result["image_layers_verified"] == 4
    assert result["image_unique_layer_blobs_verified"] == 3
    assert result["image_layer_unpacked_bytes"] == logical_bytes
    assert result["image_config_raw_sha256"] == image_id[7:]


@pytest.mark.parametrize("compressed", [False, True])
def test_native_save_shape_23_references_11_blobs_and_13_empty_layers(candidate, tmp_path, compressed):
    path = tmp_path / "native-shape.tar.gz"
    references = (0, *range(2, 10), *([1] * 13), 10)
    image_id, logical_bytes = referenced_image(candidate, path, references=references,
                                               layer_count=11, compressed=compressed, layer_sources=True)
    result = validate_image_archive(path, candidate["manifest"], image_id)
    assert result["image_layers_verified"] == 23
    assert result["image_unique_layer_blobs_verified"] == 11
    assert result["image_layer_unpacked_bytes"] == logical_bytes


@pytest.mark.parametrize("mutation", [
    "earlier_repeated_diff", "last_repeated_diff", "wrong_order", "wrong_repeat_blob",
    "short_diff_list", "short_layer_list", "duplicate_member", "missing_blob", "directory_blob",
    "symlink_blob", "hardlink_blob", "absolute_reference", "traversal_reference",
])
def test_repeated_references_never_hide_order_missing_or_physical_member_attacks(candidate, tmp_path, mutation):
    path = tmp_path / "invalid-reference.tar.gz"
    def config_change(config):
        diffs = config["rootfs"]["diff_ids"]
        if mutation in {"earlier_repeated_diff", "last_repeated_diff"}:
            diffs[1 if mutation == "earlier_repeated_diff" else 2] = "sha256:" + "0" * 64
        elif mutation == "short_diff_list":
            diffs.pop()
    def manifest_change(manifest):
        layers = manifest[0]["Layers"]
        if mutation == "wrong_order":
            layers[0], layers[-1] = layers[-1], layers[0]
        elif mutation == "wrong_repeat_blob":
            layers[2] = layers[0]
        elif mutation == "short_layer_list":
            layers.pop()
        elif mutation in {"absolute_reference", "traversal_reference"}:
            layers[1] = "/outside/layer.tar" if mutation == "absolute_reference" else "../outside/layer.tar"
    def members_change(members, paths):
        row = next(row for row in members if row[0] == paths[1])
        if mutation == "duplicate_member":
            return [*members, row]
        if mutation == "missing_blob":
            return [item for item in members if item is not row]
        kinds = {"directory_blob": tarfile.DIRTYPE, "symlink_blob": tarfile.SYMTYPE, "hardlink_blob": tarfile.LNKTYPE}
        return [(name, data, kinds[mutation] if name == paths[1] and mutation in kinds else kind)
                for name, data, kind in members]
    image_id, _ = referenced_image(candidate, path, compressed=True, config_mutation=config_change,
                                   manifest_mutation=manifest_change, member_mutation=members_change)
    with pytest.raises(ProvenanceError):
        validate_image_archive(path, candidate["manifest"], image_id)


@pytest.mark.parametrize("mutation", ["missing_footer", "corrupt_crc", "trailing_garbage"])
def test_nested_layer_gzip_reaches_crc_and_actual_eof(candidate, tmp_path, mutation):
    path = tmp_path / "nested-gzip.tar.gz"
    def change(members, paths):
        result = []
        for name, data, kind in members:
            if name == paths[1]:
                if mutation == "missing_footer":
                    data = data[:-8]
                elif mutation == "corrupt_crc":
                    data = data[:-8] + bytes([data[-8] ^ 1]) + data[-7:]
                else:
                    data += b"not-gzip"
            result.append((name, data, kind))
        return result
    image_id, _ = referenced_image(candidate, path, compressed=True, content_addressed=False,
                                   member_mutation=change)
    with pytest.raises(ProvenanceError, match="INVALID_IMAGE_LAYER_GZIP"):
        validate_image_archive(path, candidate["manifest"], image_id)


@pytest.mark.parametrize("mutation", ["recompressed_same_diffid", "wrong_blob_name", "wrong_config_name"])
def test_content_addressed_save_bytes_cannot_be_rebound_by_only_the_diffid(candidate, tmp_path, mutation):
    path = tmp_path / "blob-name-drift.tar.gz"
    def manifest_change(manifest):
        if mutation == "wrong_config_name":
            manifest[0]["Config"] = "blobs/sha256/" + "0" * 64
    def members_change(members, paths):
        result = []
        for name, data, kind in members:
            if name == paths[0]:
                if mutation == "recompressed_same_diffid":
                    data = gzip.compress(gzip.decompress(data), mtime=1)
                elif mutation == "wrong_blob_name":
                    name = "blobs/sha256/" + "0" * 64
            elif name == "manifest.json" and mutation == "wrong_blob_name":
                manifest = json.loads(data)
                manifest[0]["Layers"][0] = "blobs/sha256/" + "0" * 64
                data = canonical_bytes(manifest)
            result.append((name, data, kind))
        return result
    image_id, _ = referenced_image(candidate, path, compressed=True, references=(0, 2),
                                   manifest_mutation=manifest_change, member_mutation=members_change)
    with pytest.raises(ProvenanceError, match="IMAGE_CONTENT_ADDRESS_MISMATCH"):
        validate_image_archive(path, candidate["manifest"], image_id)


@pytest.mark.parametrize("mutation", ["layers_object", "diffids_object", "nonstring_layer", "nonstring_diffid"])
def test_image_reference_metadata_types_are_strict(candidate, tmp_path, mutation):
    path = tmp_path / "metadata-types.tar.gz"
    def config_change(config):
        if mutation == "diffids_object":
            config["rootfs"]["diff_ids"] = {value: True for value in config["rootfs"]["diff_ids"]}
        elif mutation == "nonstring_diffid":
            config["rootfs"]["diff_ids"][0] = 1
    def manifest_change(manifest):
        if mutation == "layers_object":
            manifest[0]["Layers"] = {value: True for value in manifest[0]["Layers"]}
        elif mutation == "nonstring_layer":
            manifest[0]["Layers"][0] = 1
    image_id, _ = referenced_image(candidate, path, references=(0, 2), config_mutation=config_change,
                                   manifest_mutation=manifest_change)
    with pytest.raises(ProvenanceError, match="IMAGE_LAYER_IDENTITY_MISMATCH"):
        validate_image_archive(path, candidate["manifest"], image_id)


@pytest.mark.parametrize("mutation", ["single_decoded_blob", "aggregate_decoded_blobs", "logical_repeated_bytes"])
def test_compressed_layers_and_repeated_references_have_decoded_size_bounds(candidate, tmp_path, monkeypatch, mutation):
    from scripts import porota_artifact_provenance as provenance
    path = tmp_path / "bounded-nested.tar.gz"
    if mutation == "single_decoded_blob":
        references, payload_size = (0,), 1024 * 1024
    elif mutation == "aggregate_decoded_blobs":
        references, payload_size = (0, 2), 40 * 1024
    else:
        references, payload_size = (0,) * 10, 1
    image_id, _ = referenced_image(candidate, path, references=references, payload_size=payload_size,
                                   compressed=True)
    assert len(gzip.decompress(path.read_bytes())) < 64 * 1024
    monkeypatch.setattr(provenance, "MAX_ARCHIVE_UNPACKED_BYTES", 64 * 1024)
    with pytest.raises(ProvenanceError, match="IMAGE_LAYER_UNPACKED_SIZE_LIMIT"):
        validate_image_archive(path, candidate["manifest"], image_id)


def test_layer_reference_count_cannot_evade_physical_member_cardinality(candidate, tmp_path, monkeypatch):
    from scripts import porota_artifact_provenance as provenance
    path = tmp_path / "many-references.tar.gz"
    image_id, _ = referenced_image(candidate, path, references=(0,) * 6)
    monkeypatch.setattr(provenance, "MAX_SOURCE_FILES", 5)  # exactly five physical members
    with pytest.raises(ProvenanceError, match="IMAGE_LAYER_REFERENCE_COUNT_LIMIT"):
        validate_image_archive(path, candidate["manifest"], image_id)


@pytest.mark.parametrize("representation", ["raw", "gzip", "null", "empty", "partial"])
def test_optional_layer_sources_supported_forms_keep_exact_reference_validation(candidate, tmp_path, representation):
    path = tmp_path / "optional-sources.tar.gz"
    def change(manifest):
        sources = manifest[0]["LayerSources"]
        if representation == "null":
            manifest[0]["LayerSources"] = None  # Go's nil map representation.
        elif representation == "empty":
            sources.clear()
        elif representation == "partial":
            sources.pop(next(iter(sources)))
    image_id, _ = referenced_image(candidate, path, compressed=representation == "gzip",
                                   layer_sources=True, manifest_mutation=change)
    result = validate_image_archive(path, candidate["manifest"], image_id)
    assert result["status"] == "GREEN" and result["image_layers_verified"] == 4
    assert result["image_unique_layer_blobs_verified"] == 3


@pytest.mark.parametrize("mutation", [
    "wrong_existing_digest", "absent_blob_digest", "wrong_size", "bool_size", "float_size",
    "unsupported_media_type", "wrong_compression_type", "array_container", "string_container",
    "string_descriptor", "nonstring_digest", "extra_descriptor",
])
def test_layer_sources_descriptors_cannot_disagree_with_verified_stored_blobs(candidate, tmp_path, mutation):
    path = tmp_path / "wrong-sources.tar.gz"
    control_id, _ = referenced_image(candidate, tmp_path / "control.tar.gz", layer_sources=True)
    def change(manifest):
        sources = manifest[0]["LayerSources"]
        keys = list(sources)
        descriptor = sources[keys[0]]
        if mutation == "wrong_existing_digest":
            descriptor["digest"] = sources[keys[1]]["digest"]
        elif mutation == "absent_blob_digest":
            descriptor["digest"] = "sha256:" + "0" * 64
        elif mutation == "wrong_size":
            descriptor["size"] += 1
        elif mutation == "bool_size":
            descriptor["size"] = True
        elif mutation == "float_size":
            descriptor["size"] = float(descriptor["size"])
        elif mutation == "unsupported_media_type":
            descriptor["mediaType"] = "application/octet-stream"
        elif mutation == "wrong_compression_type":
            descriptor["mediaType"] += "+gzip"
        elif mutation == "array_container":
            manifest[0]["LayerSources"] = []
        elif mutation == "string_container":
            manifest[0]["LayerSources"] = "not-a-map"
        elif mutation == "string_descriptor":
            sources[keys[0]] = "not-a-descriptor"
        elif mutation == "nonstring_digest":
            descriptor["digest"] = [descriptor["digest"]]
        else:
            sources["sha256:" + "0" * 64] = dict(descriptor)
    image_id, _ = referenced_image(candidate, path, layer_sources=True, manifest_mutation=change)
    assert image_id == control_id  # Raw config/ImageID and layer bytes are unchanged.
    with pytest.raises(ProvenanceError, match="IMAGE_LAYER_SOURCES_(INVALID|MISMATCH)"):
        validate_image_archive(path, candidate["manifest"], image_id)


def test_repeated_diffid_descriptor_is_checked_against_every_distinct_stored_representation(candidate, tmp_path):
    path = tmp_path / "reencoded-reference.tar.gz"
    def change(members, paths):
        stored = next(data for name, data, _ in members if name == paths[0])
        reencoded = gzip.compress(gzip.decompress(stored), mtime=1)
        new_path = "blobs/sha256/" + hashlib.sha256(reencoded).hexdigest()
        result = []
        for name, data, kind in members:
            if name == "manifest.json":
                manifest = json.loads(data)
                manifest[0]["Layers"][1] = new_path
                data = canonical_bytes(manifest)
            result.append((name, data, kind))
        return [*result, (new_path, reencoded, tarfile.REGTYPE)]
    image_id, _ = referenced_image(candidate, path, compressed=True, references=(0, 0), layer_count=1,
                                   layer_sources=True, member_mutation=change)
    with pytest.raises(ProvenanceError, match="IMAGE_LAYER_SOURCES_MISMATCH"):
        validate_image_archive(path, candidate["manifest"], image_id)


@pytest.mark.parametrize("location", ["manifest", "raw_config"])
@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonstandard_json_constants_are_rejected_in_saved_image_metadata(candidate, tmp_path, location, constant):
    path = tmp_path / "invalid-json.tar.gz"
    value = float(constant)
    def manifest_change(manifest):
        if location == "manifest":
            sources = manifest[0]["LayerSources"]
            sources[next(iter(sources))]["size"] = value
    def config_change(config):
        if location == "raw_config":
            config["nonstandard_json"] = value
    image_id, _ = referenced_image(candidate, path, layer_sources=True,
                                   manifest_mutation=manifest_change, config_mutation=config_change)
    with pytest.raises(ProvenanceError, match="INVALID_JSON_CONSTANT:" + constant):
        validate_image_archive(path, candidate["manifest"], image_id)


def test_legacy_presence_validator_rejects_same_paths_with_wrong_bytes(candidate):
    c = candidate
    write(c["image"], "worker.py", b"VALUE = 11\n")
    result = validate(c["repo"], c["image"], ["app.py", "worker.py"])
    assert result["status"] == "FAILED"
    assert result["source_byte_mismatches"] == ["worker.py"]


def test_complete_offline_cli_chain_and_fail_closed_exit_code(candidate, tmp_path):
    c = candidate
    script = Path("scripts/porota_artifact_provenance.py").resolve()
    for operation, args in [
        ("checkout", []),
        ("image", ["--artifact-root", str(c["image"]), "--image-inspect", str(c["inspect"])]),
        ("bundle", ["--bundle", str(c["bundle"]), "--bundle-manifest", str(c["bundle_meta"])]),
    ]:
        proc = subprocess.run([sys.executable, str(script), operation, "--repo-root", str(c["repo"]),
                               "--source-manifest", str(c["source"]), *args], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert operation.upper() + "_GREEN" in proc.stdout
    write(c["image"], "worker.py", b"VALUE = 99\n")
    evidence = tmp_path / "red.json"
    proc = subprocess.run([sys.executable, str(script), "image", "--repo-root", str(c["repo"]),
                           "--source-manifest", str(c["source"]), "--artifact-root", str(c["image"]),
                           "--image-inspect", str(c["inspect"]), "--json-out", str(evidence)],
                          capture_output=True, text=True)
    assert proc.returncode == 1
    assert json.loads(evidence.read_bytes())["failure_signature"].startswith("IMAGE_SOURCE_BYTE_MISMATCH")
    assert json.loads(evidence.read_bytes())["real_orders_sent"] == 0


def test_worktree_git_file_supported(candidate, tmp_path):
    c = candidate
    worktree = tmp_path / "linked-worktree"
    git(c["repo"], "worktree", "add", "--detach", str(worktree), "HEAD")
    assert (worktree / ".git").is_file()
    assert create_source_manifest(worktree) == c["manifest"]


def test_predeploy_executes_strict_provenance_in_final_single_build(candidate):
    workflow = yaml.safe_load((Path(__file__).resolve().parents[1] /
                               ".github/workflows/porota-predeploy-v2.yml").read_text())
    steps = workflow["jobs"]["artifact-gate"]["steps"]
    by_name = {step["name"]: step for step in steps}
    names = list(by_name)
    freeze = "Freeze checkout byte provenance before build"
    build = "Build candidate exactly once"
    assert names.index(freeze) < names.index(build)
    assert "create" in by_name[freeze]["run"]
    assert "POROTA_SOURCE_PROVENANCE.json" in by_name[freeze]["run"]
    assert "porota.tree=" in by_name[build]["run"]
    assert "porota.source-manifest-sha256=" in by_name[build]["run"]
    assert " checkout " in by_name[build]["run"]
    assert "--source-manifest" in by_name["Artifact completeness and import closure"]["run"]
    export = by_name["Export frozen candidate artifacts"]
    assert " image-tar " in export["run"]
    assert " bundle " in export["run"]
    assert "if" not in export
    assert "porota-source-provenance.json" in by_name["Upload predeploy evidence"]["with"]["path"]
    audit_gate = "Issue 465 programable reauditing evidence gate"
    assert names.index(audit_gate) > names.index("Governed automatic test discovery and execution")
    assert "rc6_issue465_audit_gate.py verify" in by_name[audit_gate]["run"]
    assert '--junit "${POROTA_PREDEPLOY_TMP}/porota-governed-tests.xml"' in by_name[audit_gate]["run"]
    assert "porota-issue465-audit-gate.json" in by_name["Upload predeploy evidence"]["with"]["path"]
    assert "ISSUE465_REAUDIT.json" in by_name["Upload predeploy evidence"]["with"]["path"]
    assert sum(step.get("run", "").count("docker build") for step in steps) == 1
    assert "ssh " not in "\n".join(step.get("run", "") for step in steps)
