#!/usr/bin/env python3
"""Bind the exact Git checkout, image and deploy bundle by source bytes.

Stdlib only. No provider, Docker build, deployment or runtime calls are made.
The source authority is the complete commit tree, never an artifact-authored
list. All decisions and hashes are deterministic and reproducible offline.
"""
from __future__ import annotations

import argparse
import fnmatch
import gzip
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any

SOURCE_MANIFEST_NAME = "POROTA_SOURCE_PROVENANCE.json"
BUNDLE_MANIFEST_NAME = "POROTA_DEPLOY_BUNDLE_MANIFEST.json"
SCHEMA = "porota.source-byte-provenance.v2"
NON_DEPLOY_ROOTS = {"tests", "docs", ".github", ".agents"}
MAX_SOURCE_FILES = 50_000
MAX_METADATA_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_UNPACKED_BYTES = 8 * 1024**3


class ProvenanceError(ValueError):
    """A fail-closed provenance failure, with a stable error signature."""


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_gzip_envelope(path: Path) -> int:
    """Consume to actual gzip EOF, verifying CRC/size/trailer with a bound.

    tarfile stops at tar end markers and can otherwise accept a missing/corrupt
    gzip footer or trailing garbage. Hashing compressed bytes alone cannot make
    such an envelope usable by the eventual artifact consumer.
    """
    total = 0
    try:
        with gzip.open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                total += len(block)
                if total > MAX_ARCHIVE_UNPACKED_BYTES:
                    raise ProvenanceError("ARCHIVE_UNPACKED_SIZE_LIMIT")
    except (OSError, EOFError, zlib.error) as exc:
        raise ProvenanceError("INVALID_GZIP_ENVELOPE") from exc
    return total


def _pairs(pairs: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ProvenanceError("DUPLICATE_JSON_KEY:" + key)
        result[key] = value
    return result


def _invalid_json_constant(value: str) -> None:
    # Python otherwise accepts NaN/Infinity, which are not JSON literals.
    raise ProvenanceError("INVALID_JSON_CONSTANT:" + value)


def decode_json(data: bytes, signature: str = "INVALID_MANIFEST") -> Any:
    if len(data) > MAX_METADATA_BYTES:
        raise ProvenanceError("METADATA_SIZE_LIMIT")
    try:
        return json.loads(data, object_pairs_hook=_pairs, parse_constant=_invalid_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvenanceError(signature) from exc


def safe_path(path: str) -> str:
    parsed = PurePosixPath(path)
    if (not path or parsed.is_absolute() or ".." in parsed.parts or "\\" in path
            or parsed.as_posix() != path or any(ord(ch) < 32 for ch in path)):
        raise ProvenanceError("UNSAFE_PATH:" + repr(path))
    return path


def is_bundle_path(path: str) -> bool:
    """Ship the automatically enumerated deploy source, regardless of suffix.

    Exclusions are repository roles (tests/docs/CI), not a partial runtime file
    list. This includes new assets/configuration/executables without edits here.
    """
    return PurePosixPath(path).parts[0] not in NON_DEPLOY_ROOTS


def _git(repo_root: Path, *args: str, data: bytes | None = None) -> bytes:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo_root), *args], input=data, stderr=subprocess.PIPE
        )
    except subprocess.CalledProcessError as exc:
        raise ProvenanceError("GIT_SOURCE_AUTHORITY_UNAVAILABLE") from exc


def _glob_expression(pattern: str) -> re.Pattern:
    """Docker-style path glob: * stays within a directory; ** crosses it."""
    parts: list[str] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if pattern[i:i + 2] == "**":
                i += 2
                if i < len(pattern) and pattern[i] == "/":
                    parts.append("(?:.*/)?")
                    i += 1
                else:
                    parts.append(".*")
                continue
            parts.append("[^/]*")
        elif char == "?":
            parts.append("[^/]")
        elif char == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                raise ProvenanceError("UNSUPPORTED_DOCKERIGNORE_PATTERN:" + pattern)
            translated = fnmatch.translate(pattern[i:end + 1])
            parts.append(translated[4:-3])
            i = end
        elif char == "\\":
            raise ProvenanceError("UNSUPPORTED_DOCKERIGNORE_PATTERN:" + pattern)
        else:
            parts.append(re.escape(char))
        i += 1
    return re.compile("^" + "".join(parts) + "$")


def dockerignore_exclusion(path: str, text: str) -> str | None:
    """Return the last matching versioned exclusion, with parent matching.

    These are representation exceptions, not permission to omit runtime code.
    Unsupported patterns fail closed instead of silently skipping content.
    """
    excluded: str | None = None
    ancestors = ["/".join(PurePosixPath(path).parts[:i])
                 for i in range(1, len(PurePosixPath(path).parts) + 1)]
    for raw in text.splitlines():
        rule = raw.strip()
        if not rule or rule.startswith("#"):
            continue
        negate = rule.startswith("!")
        pattern = rule[1:] if negate else rule
        pattern = pattern.strip("/")
        if not pattern or pattern == ".":
            continue
        match = _glob_expression(pattern)
        if any(match.fullmatch(part) for part in ancestors):
            excluded = None if negate else rule
    return excluded


def create_source_manifest(
    repo_root: Path,
    candidate_sha: str | None = None,
    candidate_tree_sha: str | None = None,
) -> dict:
    repo_root = repo_root.resolve()
    head = _git(repo_root, "rev-parse", "HEAD").decode().strip()
    tree = _git(repo_root, "rev-parse", "HEAD^{tree}").decode().strip()
    if candidate_sha is not None and candidate_sha != head:
        raise ProvenanceError("CANDIDATE_SHA_MISMATCH")
    if candidate_tree_sha is not None and candidate_tree_sha != tree:
        raise ProvenanceError("CANDIDATE_TREE_MISMATCH")
    entries = _git(repo_root, "ls-tree", "-rz", "--full-tree", head).split(b"\0")
    source: list[tuple[str, str, str]] = []
    for entry in entries:
        if not entry:
            continue
        meta, name = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        path = safe_path(name.decode("utf-8"))
        if path in {SOURCE_MANIFEST_NAME, BUNDLE_MANIFEST_NAME}:
            raise ProvenanceError("RESERVED_GENERATED_METADATA_TRACKED:" + path)
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ProvenanceError("NON_REGULAR_GIT_SOURCE:" + path)
        source.append((path, mode, oid))
    source.sort()
    if not source or len(source) > MAX_SOURCE_FILES:
        raise ProvenanceError("SOURCE_FILE_COUNT_LIMIT")

    object_format = _git(repo_root, "rev-parse", "--show-object-format").decode().strip()
    if object_format not in {"sha1", "sha256"}:
        raise ProvenanceError("UNSUPPORTED_GIT_OBJECT_FORMAT")
    dockerignore_oid = next((oid for path, _, oid in source if path == ".dockerignore"), None)
    dockerignore = _git(repo_root, "cat-file", "blob", dockerignore_oid).decode() if dockerignore_oid else ""
    try:
        from scripts.porota_validate_deploy_artifact import is_runtime_relevant
    except ModuleNotFoundError:
        from porota_validate_deploy_artifact import is_runtime_relevant
    rows = []
    for path, mode, oid in source:
        src = repo_root / path
        for parent in src.parents:
            if parent == repo_root:
                break
            if parent.is_symlink():
                raise ProvenanceError("CHECKOUT_SOURCE_PARENT_SYMLINK:" + path)
        try:
            info = src.lstat()
        except OSError as exc:
            raise ProvenanceError("CHECKOUT_SOURCE_UNREADABLE:" + path) from exc
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ProvenanceError("NON_REGULAR_CHECKOUT_SOURCE:" + path)
        # Hash the SHA256 and Git blob identity from the same opened bytes.
        # No filters, symlink reads or hash/read TOCTOU gap can authorize bytes.
        raw_sha256 = hashlib.sha256()
        blob_digest = hashlib.new(object_format)
        blob_digest.update(b"blob " + str(info.st_size).encode() + b"\0")
        count = 0
        descriptor = os.open(src, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise ProvenanceError("CHECKOUT_SOURCE_REPLACED:" + path)
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                raw_sha256.update(block)
                blob_digest.update(block)
                count += len(block)
        if count != info.st_size or blob_digest.hexdigest() != oid:
            raise ProvenanceError("CHECKOUT_BYTE_MISMATCH:" + path)
        executable = mode == "100755"
        if bool(info.st_mode & 0o111) != executable:
            raise ProvenanceError("CHECKOUT_MODE_MISMATCH:" + path)
        excluded = dockerignore_exclusion(path, dockerignore)
        runtime = is_runtime_relevant(path)
        if (runtime or is_bundle_path(path)) and excluded:
            raise ProvenanceError("RUNTIME_SOURCE_EXCLUDED_BY_DOCKERIGNORE:" + path)
        rows.append({
            "path": path, "sha256": raw_sha256.hexdigest(), "bytes": info.st_size,
            "git_blob_oid": oid, "git_mode": mode,
            "runtime_relevant": runtime, "bundle_required": is_bundle_path(path),
            "image_required": excluded is None,
            "image_exclusion": {"authority": ".dockerignore", "rule": excluded} if excluded else None,
        })
    manifest = {
        "schema": SCHEMA, "schema_version": 2, "status": "GREEN",
        "candidate_sha": head, "candidate_tree_sha": tree,
        "git_object_format": object_format,
        "enumeration": "git ls-tree -rz --full-tree exact HEAD; every tracked regular blob",
        "file_count": len(rows), "files": rows,
        "generated_metadata": [SOURCE_MANIFEST_NAME, BUNDLE_MANIFEST_NAME],
        "bytecode_policy": "NO_APP_BYTECODE_ALLOWED; image sets PYTHONDONTWRITEBYTECODE=1",
        "real_orders_sent": 0, "real_routes": "NOT_CALLED",
    }
    manifest["manifest_sha256"] = hashlib.sha256(canonical_bytes(manifest)).hexdigest()
    return manifest


def verify_build_context(repo_root: Path, source: dict, manifest_path: Path) -> dict:
    """Reject untracked context bytes before consuming the one build."""
    tracked = {row["path"] for row in source["files"]}
    dockerignore_path = repo_root / ".dockerignore"
    dockerignore = dockerignore_path.read_text() if dockerignore_path.is_file() else ""
    generated = repo_root / SOURCE_MANIFEST_NAME
    if (not generated.is_file() or generated.is_symlink()
            or generated.read_bytes() != manifest_path.read_bytes()
            or generated.stat().st_nlink != 1 or generated.stat().st_mode & 0o111):
        raise ProvenanceError("BUILD_CONTEXT_SOURCE_MANIFEST_MISMATCH")
    for root, directories, files in os.walk(repo_root, followlinks=False):
        current = Path(root)
        for dirname in list(directories):
            path = current / dirname
            rel = path.relative_to(repo_root).as_posix()
            if rel == ".git" or dockerignore_exclusion(rel, dockerignore):
                directories.remove(dirname)
            elif path.is_symlink():
                raise ProvenanceError("BUILD_CONTEXT_SYMLINK:" + rel)
        for filename in files:
            path = current / filename
            rel = path.relative_to(repo_root).as_posix()
            if rel == ".git" or rel in tracked or rel == SOURCE_MANIFEST_NAME:
                continue
            if dockerignore_exclusion(rel, dockerignore) is None:
                raise ProvenanceError("BUILD_CONTEXT_UNTRACKED_PATH:" + rel)
    return {"status": "GREEN", "unexpected_context_files": 0}


def verify_source_manifest(repo_root: Path, manifest_path: Path,
                           candidate_sha: str | None = None,
                           candidate_tree_sha: str | None = None) -> dict:
    data = manifest_path.read_bytes()
    manifest = decode_json(data)
    if not isinstance(manifest, dict):
        raise ProvenanceError("INVALID_SOURCE_MANIFEST")
    expected = create_source_manifest(repo_root, candidate_sha, candidate_tree_sha)
    if data != canonical_bytes(expected):
        raise ProvenanceError("SOURCE_MANIFEST_GIT_MISMATCH")
    return manifest


def validate_image_labels(labels: Any, source: dict) -> None:
    if not isinstance(labels, dict):
        raise ProvenanceError("IMAGE_LABELS_MISSING")
    expected = {
        "porota.commit": source["candidate_sha"],
        "porota.tree": source["candidate_tree_sha"],
        "porota.source-manifest-sha256": hashlib.sha256(canonical_bytes(source)).hexdigest(),
        "porota.predeploy": "v2",
    }
    for key, value in expected.items():
        if labels.get(key) != value:
            raise ProvenanceError("IMAGE_LABEL_MISMATCH:" + key)


def validate_image_files(artifact_root: Path, source: dict, source_bytes: bytes) -> dict:
    if not artifact_root.is_dir() or artifact_root.is_symlink():
        raise ProvenanceError("NON_REGULAR_ARTIFACT_ROOT")
    expected = {row["path"]: row for row in source["files"]}
    present: dict[str, Path] = {}
    for path in artifact_root.rglob("*"):
        rel = safe_path(path.relative_to(artifact_root).as_posix())
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or (not stat.S_ISREG(info.st_mode)
                                      and not stat.S_ISDIR(info.st_mode)):
            raise ProvenanceError("NON_REGULAR_ARTIFACT_PATH:" + rel)
        if stat.S_ISREG(info.st_mode):
            if info.st_nlink != 1:
                raise ProvenanceError("ARTIFACT_HARDLINK:" + rel)
            present[rel] = path
            if len(present) > MAX_SOURCE_FILES + 1:
                raise ProvenanceError("ARTIFACT_FILE_COUNT_LIMIT")
    embedded = present.pop(SOURCE_MANIFEST_NAME, None)
    if embedded is None or embedded.read_bytes() != source_bytes:
        raise ProvenanceError("IMAGE_SOURCE_MANIFEST_MISMATCH")
    if embedded.stat().st_mode & 0o111:
        raise ProvenanceError("IMAGE_SOURCE_MANIFEST_EXECUTABLE")
    missing = sorted(p for p, row in expected.items() if row["image_required"] and p not in present)
    if missing:
        raise ProvenanceError("IMAGE_MISSING_SOURCE:" + ",".join(missing))
    extra = sorted(set(present) - set(expected))
    if extra:
        raise ProvenanceError("IMAGE_UNEXPECTED_PATH:" + ",".join(extra))
    checked = 0
    for rel, path in present.items():
        row = expected[rel]
        if not row["image_required"]:
            raise ProvenanceError("IMAGE_EXCLUDED_SOURCE_PRESENT:" + rel)
        if path.stat().st_size != row["bytes"] or sha256_file(path) != row["sha256"]:
            raise ProvenanceError("IMAGE_SOURCE_BYTE_MISMATCH:" + rel)
        if bool(path.stat().st_mode & 0o111) != (row["git_mode"] == "100755"):
            raise ProvenanceError("IMAGE_SOURCE_MODE_MISMATCH:" + rel)
        checked += 1
    return {"status": "GREEN", "source_files_verified": checked,
            "source_files_excluded_by_versioned_dockerignore": len(expected) - checked,
            "source_manifest_sha256": hashlib.sha256(source_bytes).hexdigest()}


def validate_image(repo_root: Path, artifact_root: Path, manifest_path: Path,
                   inspect_path: Path, candidate_sha: str | None = None,
                   candidate_tree_sha: str | None = None) -> dict:
    source = verify_source_manifest(repo_root, manifest_path, candidate_sha, candidate_tree_sha)
    inspection = decode_json(inspect_path.read_bytes(), "INVALID_IMAGE_INSPECTION")
    if not isinstance(inspection, list) or len(inspection) != 1:
        raise ProvenanceError("EXACTLY_ONE_IMAGE_REQUIRED")
    validate_image_labels(inspection[0].get("Config", {}).get("Labels"), source)
    result = validate_image_files(artifact_root, source, manifest_path.read_bytes())
    result.update(candidate_sha=source["candidate_sha"], candidate_tree_sha=source["candidate_tree_sha"],
                  image_id=inspection[0]["Id"], real_orders_sent=0, real_routes="NOT_CALLED")
    return result


def bundle_manifest(source: dict) -> dict:
    rows = [{"path": r["path"], "sha256": r["sha256"], "bytes": r["bytes"],
             "git_mode": r["git_mode"]} for r in source["files"] if r["bundle_required"]]
    return {"schema_version": 2, "status": "GREEN", "files": rows, "file_count": len(rows),
            "candidate_sha": source["candidate_sha"], "candidate_tree_sha": source["candidate_tree_sha"],
            "source_manifest_sha256": hashlib.sha256(canonical_bytes(source)).hexdigest()}


def _archive_members(archive: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    result: dict[str, tarfile.TarInfo] = {}
    for member in archive:
        name = safe_path(member.name)
        if name in result:
            raise ProvenanceError("DUPLICATE_ARCHIVE_PATH:" + name)
        if not member.isfile():
            raise ProvenanceError("NON_REGULAR_ARCHIVE_PATH:" + name)
        result[name] = member
        if len(result) > MAX_SOURCE_FILES + 2:
            raise ProvenanceError("ARCHIVE_FILE_COUNT_LIMIT")
    return result


def _member_bytes(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    if member.size > MAX_METADATA_BYTES:
        raise ProvenanceError("METADATA_SIZE_LIMIT")
    handle = archive.extractfile(member)
    if handle is None:
        raise ProvenanceError("ARCHIVE_FILE_UNREADABLE")
    return handle.read()


def validate_bundle(repo_root: Path, bundle_path: Path, bundle_manifest_path: Path,
                    source_manifest_path: Path, candidate_sha: str | None = None,
                    candidate_tree_sha: str | None = None) -> dict:
    source = verify_source_manifest(repo_root, source_manifest_path, candidate_sha, candidate_tree_sha)
    expected = bundle_manifest(source)
    external = decode_json(bundle_manifest_path.read_bytes())
    if not isinstance(external, dict):
        raise ProvenanceError("INVALID_BUNDLE_MANIFEST")
    digest = sha256_file(bundle_path)
    if external != {**expected, "bundle_sha256": digest}:
        raise ProvenanceError("BUNDLE_MANIFEST_GIT_MISMATCH")
    rows = {row["path"]: row for row in expected["files"]}
    validate_gzip_envelope(bundle_path)
    with tarfile.open(bundle_path, "r:gz") as archive:
        members = _archive_members(archive)
        metadata = {SOURCE_MANIFEST_NAME, BUNDLE_MANIFEST_NAME}
        missing = sorted((set(rows) | metadata) - set(members))
        extra = sorted(set(members) - set(rows) - metadata)
        if missing:
            raise ProvenanceError("BUNDLE_MISSING_SOURCE:" + ",".join(missing))
        if extra:
            raise ProvenanceError("BUNDLE_UNEXPECTED_PATH:" + ",".join(extra))
        if _member_bytes(archive, members[SOURCE_MANIFEST_NAME]) != source_manifest_path.read_bytes():
            raise ProvenanceError("BUNDLE_SOURCE_MANIFEST_MISMATCH")
        if _member_bytes(archive, members[BUNDLE_MANIFEST_NAME]) != canonical_bytes(expected):
            raise ProvenanceError("BUNDLE_EMBEDDED_MANIFEST_MISMATCH")
        if any(members[name].mode & 0o111 for name in metadata):
            raise ProvenanceError("BUNDLE_GENERATED_METADATA_EXECUTABLE")
        for name, row in rows.items():
            member = members[name]
            handle = archive.extractfile(member)
            digest_bytes = hashlib.file_digest(handle, "sha256").hexdigest()
            if member.size != row["bytes"] or digest_bytes != row["sha256"]:
                raise ProvenanceError("BUNDLE_SOURCE_BYTE_MISMATCH:" + name)
            if bool(member.mode & 0o111) != (row["git_mode"] == "100755"):
                raise ProvenanceError("BUNDLE_SOURCE_MODE_MISMATCH:" + name)
    return {"status": "GREEN", "bundle_sha256": digest, "bundle_files_verified": len(rows),
            "candidate_sha": source["candidate_sha"], "candidate_tree_sha": source["candidate_tree_sha"],
            "source_manifest_sha256": expected["source_manifest_sha256"]}


class _ImageLayerReader:
    """Hash the stored bytes while a gzip decoder consumes this one blob."""

    def __init__(self, handle):
        self.handle = handle
        self.digest = hashlib.sha256()
        self.bytes = 0

    def read(self, size=-1):
        block = self.handle.read(size)
        self.digest.update(block)
        self.bytes += len(block)
        return block


def _image_content_address(name: str, actual_sha256: str) -> None:
    # Legacy docker-save names need not be content addresses. OCI blob names
    # are independently bound to their actual STORED bytes, before decoding.
    if name.startswith("blobs/"):
        if name != "blobs/sha256/" + actual_sha256:
            raise ProvenanceError("IMAGE_CONTENT_ADDRESS_MISMATCH:" + name)


def validate_image_archive(image_path: Path, source: dict, expected_image_id: str) -> dict:
    """Rehash exported config and every layer against that exact image ID.

    Image filesystem byte validation happens before export. Raw config hashing
    and its ordered rootfs DiffIDs bind the saved layer bytes to that verified
    image, without a rebuild or trusting labels alone. Docker save also permits
    an OCI layout; directory headers are valid, links/duplicate paths are not.
    """
    unpacked_bytes = validate_gzip_envelope(image_path)
    with tarfile.open(image_path, "r:gz") as archive:
        members: dict[str, tarfile.TarInfo] = {}
        for member in archive:
            name = safe_path(member.name.rstrip("/"))
            if name in members or (not member.isfile() and not member.isdir()):
                raise ProvenanceError("UNSAFE_IMAGE_ARCHIVE_PATH:" + name)
            members[name] = member
            if len(members) > MAX_SOURCE_FILES:
                raise ProvenanceError("IMAGE_ARCHIVE_FILE_COUNT_LIMIT")
        if "manifest.json" not in members:
            raise ProvenanceError("IMAGE_SAVE_MANIFEST_MISSING")
        manifest = decode_json(_member_bytes(archive, members["manifest.json"]))
        if not isinstance(manifest, list) or len(manifest) != 1 or not isinstance(manifest[0], dict):
            raise ProvenanceError("EXACTLY_ONE_SAVED_IMAGE_REQUIRED")
        if not isinstance(manifest[0].get("Config"), str):
            raise ProvenanceError("INVALID_IMAGE_CONFIG")
        config_path = safe_path(manifest[0]["Config"])
        if config_path not in members or not members[config_path].isfile():
            raise ProvenanceError("IMAGE_CONFIG_MEMBER_INVALID:" + config_path)
        config_bytes = _member_bytes(archive, members[config_path])
        image_id = "sha256:" + hashlib.sha256(config_bytes).hexdigest()
        if image_id != expected_image_id:
            raise ProvenanceError("EXPORTED_IMAGE_ID_MISMATCH")
        _image_content_address(config_path, image_id[7:])
        config = decode_json(config_bytes, "INVALID_IMAGE_CONFIG")
        if not isinstance(config, dict) or not isinstance(config.get("rootfs"), dict):
            raise ProvenanceError("INVALID_IMAGE_CONFIG")
        validate_image_labels(config.get("config", {}).get("Labels"), source)
        layers = manifest[0].get("Layers", [])
        diffs = config.get("rootfs", {}).get("diff_ids", [])
        if (not isinstance(layers, list) or not isinstance(diffs, list) or not layers
                or len(layers) != len(diffs) or any(not isinstance(name, str) for name in layers)
                or any(not isinstance(diff, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", diff) for diff in diffs)):
            raise ProvenanceError("IMAGE_LAYER_IDENTITY_MISMATCH")
        if len(layers) > MAX_SOURCE_FILES:
            raise ProvenanceError("IMAGE_LAYER_REFERENCE_COUNT_LIMIT")
        for name in layers:
            safe_path(name)
            if name not in members or not members[name].isfile():
                raise ProvenanceError("IMAGE_LAYER_MEMBER_INVALID:" + name)
        # Docker's optional DiffID-keyed descriptors can be absent, a Go nil
        # map, or a partial map. Every supplied descriptor must still describe
        # each actual stored representation referenced by that DiffID.
        layer_sources = manifest[0].get("LayerSources")
        if layer_sources is not None:
            if not isinstance(layer_sources, dict) or set(layer_sources) - set(diffs):
                raise ProvenanceError("IMAGE_LAYER_SOURCES_INVALID")
            for descriptor in layer_sources.values():
                if (not isinstance(descriptor, dict)
                        or not isinstance(descriptor.get("digest"), str)
                        or not re.fullmatch(r"sha256:[0-9a-f]{64}", descriptor["digest"])
                        or type(descriptor.get("size")) is not int or descriptor["size"] < 0
                        or not isinstance(descriptor.get("mediaType"), str)):
                    raise ProvenanceError("IMAGE_LAYER_SOURCES_INVALID")
        # Identical empty/copy layers may occur multiple times in rootfs but
        # are stored once in a content-addressed save. Deduplicate physical
        # reads only; never collapse the ordered expected DiffIDs into a dict.
        actual_diffs: dict[str, str] = {}
        decoded_sizes: dict[str, int] = {}
        stored_digests: dict[str, str] = {}
        compressed_layers: dict[str, bool] = {}
        unique_decoded_bytes = 0
        for layer in sorted(set(layers), key=lambda name: members[name].offset):
            with archive.extractfile(members[layer]) as handle:
                compressed = handle.peek(2)[:2] == b"\x1f\x8b"
                stored = _ImageLayerReader(handle)
                content = gzip.GzipFile(fileobj=stored) if compressed else stored
                decoded = hashlib.sha256()
                size = 0
                try:
                    for block in iter(lambda: content.read(1024 * 1024), b""):
                        size += len(block)
                        unique_decoded_bytes += len(block)
                        if unique_decoded_bytes > MAX_ARCHIVE_UNPACKED_BYTES:
                            raise ProvenanceError("IMAGE_LAYER_UNPACKED_SIZE_LIMIT")
                        decoded.update(block)
                except (OSError, EOFError, zlib.error) as exc:
                    raise ProvenanceError("INVALID_IMAGE_LAYER_GZIP:" + layer) from exc
                finally:
                    if compressed:
                        content.close()
                if stored.bytes != members[layer].size:
                    raise ProvenanceError("IMAGE_LAYER_STORED_SIZE_MISMATCH:" + layer)
                _image_content_address(layer, stored.digest.hexdigest())
                actual_diffs[layer] = "sha256:" + decoded.hexdigest()
                decoded_sizes[layer] = size
                stored_digests[layer] = "sha256:" + stored.digest.hexdigest()
                compressed_layers[layer] = compressed
        logical_decoded_bytes = 0
        for layer, expected_diff in zip(layers, diffs):
            if actual_diffs[layer] != expected_diff:
                raise ProvenanceError("EXPORTED_IMAGE_LAYER_MISMATCH:" + layer)
            if layer_sources is not None and expected_diff in layer_sources:
                descriptor = layer_sources[expected_diff]
                media_types = ({"application/vnd.oci.image.layer.v1.tar+gzip",
                                "application/vnd.oci.image.layer.nondistributable.v1.tar+gzip",
                                "application/vnd.docker.image.rootfs.diff.tar.gzip",
                                "application/vnd.docker.image.rootfs.foreign.diff.tar.gzip"}
                               if compressed_layers[layer] else
                               {"application/vnd.oci.image.layer.v1.tar",
                                "application/vnd.oci.image.layer.nondistributable.v1.tar",
                                "application/vnd.docker.image.rootfs.diff.tar"})
                if (descriptor["digest"] != stored_digests[layer]
                        or descriptor["size"] != members[layer].size
                        or descriptor["mediaType"] not in media_types):
                    raise ProvenanceError("IMAGE_LAYER_SOURCES_MISMATCH:" + layer)
            logical_decoded_bytes += decoded_sizes[layer]
            if logical_decoded_bytes > MAX_ARCHIVE_UNPACKED_BYTES:
                raise ProvenanceError("IMAGE_LAYER_UNPACKED_SIZE_LIMIT")
    return {"status": "GREEN", "image_id": image_id,
            "image_tar_sha256": sha256_file(image_path),
            "image_config_raw_sha256": image_id.removeprefix("sha256:"),
            "image_tar_unpacked_bytes": unpacked_bytes,
            "image_layers_verified": len(layers),
            "image_unique_layer_blobs_verified": len(actual_diffs),
            "image_layer_source_descriptors_verified": len(layer_sources or {}),
            "image_layer_unpacked_bytes": logical_decoded_bytes,
            "candidate_sha": source["candidate_sha"], "candidate_tree_sha": source["candidate_tree_sha"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["create", "checkout", "image", "bundle", "image-tar"])
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--candidate-sha")
    parser.add_argument("--tree-sha")
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--image-inspect", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--bundle-manifest", type=Path)
    parser.add_argument("--image-tar", type=Path)
    parser.add_argument("--image-id")
    parser.add_argument("--verify-context", action="store_true")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    try:
        common = (args.repo_root, args.source_manifest, args.candidate_sha, args.tree_sha)
        if args.operation == "create":
            result = create_source_manifest(args.repo_root, args.candidate_sha, args.tree_sha)
            args.source_manifest.write_bytes(canonical_bytes(result))
        elif args.operation == "checkout":
            result = verify_source_manifest(*common)
            if args.verify_context:
                result = {"status": "GREEN", "checkout_source_manifest_sha256": sha256_file(args.source_manifest),
                          "build_context": verify_build_context(args.repo_root, result, args.source_manifest)}
        elif args.operation == "image":
            if args.artifact_root is None or args.image_inspect is None:
                parser.error("image requires --artifact-root and --image-inspect")
            result = validate_image(args.repo_root, args.artifact_root, args.source_manifest,
                                    args.image_inspect, args.candidate_sha, args.tree_sha)
        elif args.operation == "bundle":
            if args.bundle is None or args.bundle_manifest is None:
                parser.error("bundle requires --bundle and --bundle-manifest")
            result = validate_bundle(args.repo_root, args.bundle, args.bundle_manifest,
                                     args.source_manifest, args.candidate_sha, args.tree_sha)
        else:
            if args.image_tar is None or args.image_id is None:
                parser.error("image-tar requires --image-tar and --image-id")
            source = verify_source_manifest(*common)
            result = validate_image_archive(args.image_tar, source, args.image_id)
        if args.json_out:
            args.json_out.write_bytes(canonical_bytes(result))
        print("POROTA_BYTE_PROVENANCE=" + args.operation.upper() + "_GREEN")
        return 0
    except (ProvenanceError, OSError, KeyError, TypeError, AttributeError, IndexError,
            tarfile.TarError, EOFError) as exc:
        result = {"status": "RED", "operation": args.operation, "failure_signature": str(exc),
                  "real_orders_sent": 0, "real_routes": "NOT_CALLED"}
        if args.json_out:
            args.json_out.write_bytes(canonical_bytes(result))
        print("POROTA_BYTE_PROVENANCE=RED|" + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
