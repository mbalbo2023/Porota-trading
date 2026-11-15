#!/usr/bin/env python3
"""OFFHOST ZIP byte verification for a future, independently trusted RC6 gate.

This script is deliberately not wired into Actions and must NOT be executed as
release authority from an unreviewed PR checkout. A future fixed-SHA trusted
bootstrap must fetch the raw GitHub REST artifact ZIP and authenticated REST
artifact/run receipts, then supply independently expected SHA/tree/run/attempt,
artifact ID and digest. This module makes no HTTP requests or host mutations.
Its optional workflow-path override is restricted to the canonical Predeploy V2
path and the isolated R163 offhost proof workflow; pull_request is required.
The canonical artifact name is ``porota-predeploy-v2-{candidate_sha}``;
the R163 proof name is exactly
``rc6-r163-offhost-{run_id}-{run_attempt}-{candidate_sha}``.

The *internal* ZIP member ``porota-artifact-byte-manifest-v1.json`` has exactly:
  schema_version=1, candidate_sha, candidate_tree_sha, run_id, run_attempt,
  artifact_name, file_count, total_size_bytes, files (sorted by path).
Each files[] row has exactly path, type="file", size_bytes, sha256. The manifest
does not list itself: its bytes are bound by the authenticated digest of the
*whole raw ZIP*. It has no artifact_id: upload-artifact v4 assigns that ID only
after upload. The external REST artifact receipt binds artifact_id, run_id,
head_sha, name, ZIP size/digest; the REST run receipt binds the successful
Predeploy run. Future INFRA native CID/volume evidence may be included as a
hashed file, but this verifier does NOT yet validate its inner native schema.

Even a successful byte match is NOT owner approval, release GO, physical PAPER
safety, or trusted-bootstrap certification. No R144/HOLD79 is lifted here.
The same workflow run cannot attest its own status=completed: an intrarun
smoke is REVIEW_ONLY/NO_VERIFICADO. Final run evidence requires a subsequent
trusted-default-branch, fixed-SHA workflow_run bootstrap after completion;
TRIGGER_POSTRUN_TRUSTED_DEFAULT_WORKFLOW is not implemented in this module.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any, Mapping
import zipfile


REPOSITORY = "mbalbo2023/Porota-trading"
PREDEPLOY_WORKFLOW = ".github/workflows/porota-predeploy-v2.yml"
OFFHOST_PROOF_WORKFLOW = ".github/workflows/rc6-r163-offhost-guard79-proof.yml"
ALLOWED_WORKFLOWS = frozenset({PREDEPLOY_WORKFLOW, OFFHOST_PROOF_WORKFLOW})
MANIFEST_NAME = "porota-artifact-byte-manifest-v1.json"
MANIFEST_KEYS = frozenset({
    "schema_version", "candidate_sha", "candidate_tree_sha", "run_id",
    "run_attempt", "artifact_name", "file_count", "total_size_bytes", "files",
})
FILE_KEYS = frozenset({"path", "type", "size_bytes", "sha256"})
MAX_ARCHIVE_BYTES = 2 * 1024**3
MAX_UNCOMPRESSED_BYTES = 2 * 1024**3
MAX_ZIP_EXPANSION_RATIO = 200
MAX_MEMBERS = 4096
MAX_MANIFEST_BYTES = 512 * 1024
CHUNK_BYTES = 1024 * 1024
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
SAFE_SEGMENT = re.compile(r"[a-zA-Z0-9_.+@-]+\Z")


class ArtifactByteEvidenceRed(ValueError):
    """Fixed reason code: never incorporate untrusted path/data in errors."""


def _red(reason: str) -> None:
    raise ArtifactByteEvidenceRed(reason)


def _integer(value: Any, *, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _digest(value: Any, *, require_prefix: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    if value.startswith("sha256:"):
        value = value[7:]
    elif require_prefix:
        return None
    return value if HEX64.fullmatch(value) else None


def _safe_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > 1024:
        return False
    return all(segment not in {".", ".."} and SAFE_SEGMENT.fullmatch(segment)
               for segment in value.split("/"))


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            _red("JSON_DUPLICATE_KEY")
        out[key] = value
    return out


def _parse_json(data: bytes) -> Any:
    def finite_float(token: str) -> float:
        parsed = float(token)
        if not math.isfinite(parsed):
            _red("JSON_NONFINITE")
        return parsed

    try:
        return json.loads(data, object_pairs_hook=_no_duplicate_keys,
                          parse_constant=lambda _: _red("JSON_NONFINITE"),
                          parse_float=finite_float)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        if isinstance(exc, ArtifactByteEvidenceRed):
            raise
        _red("JSON_INVALID")


def _check_receipts(
    artifact: Mapping[str, Any], run: Mapping[str, Any], *,
    sha: str, run_id: int, attempt: int, artifact_id: int, digest: str,
    workflow_path: str,
) -> str:
    if not isinstance(artifact, Mapping) or not isinstance(run, Mapping):
        _red("REST_RECEIPT_INVALID")
    if not _integer(artifact.get("id"), minimum=1) or artifact["id"] != artifact_id:
        _red("ARTIFACT_ID_MISMATCH")
    if workflow_path == PREDEPLOY_WORKFLOW:
        name = f"porota-predeploy-v2-{sha}"
    elif workflow_path == OFFHOST_PROOF_WORKFLOW:
        name = f"rc6-r163-offhost-{run_id}-{attempt}-{sha}"
    else:
        _red("EXPECTED_WORKFLOW_INVALID")
    if artifact.get("name") != name:
        _red("ARTIFACT_NAME_MISMATCH")
    if artifact.get("expired") is not False:
        _red("ARTIFACT_EXPIRED")
    if _digest(artifact.get("digest"), require_prefix=True) != digest:
        _red("ARTIFACT_DIGEST_MISMATCH")
    workflow = artifact.get("workflow_run")
    if (not isinstance(workflow, Mapping) or
            not _integer(workflow.get("id"), minimum=1) or
            workflow["id"] != run_id):
        _red("ARTIFACT_RUN_MISMATCH")
    if workflow.get("head_sha") != sha:
        _red("ARTIFACT_SHA_MISMATCH")
    if not _integer(run.get("id"), minimum=1) or run["id"] != run_id:
        _red("RUN_ID_MISMATCH")
    if not _integer(run.get("run_attempt"), minimum=1) or run["run_attempt"] != attempt:
        _red("RUN_ATTEMPT_MISMATCH")
    if run.get("head_sha") != sha:
        _red("RUN_SHA_MISMATCH")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        _red("RUN_NOT_SUCCESS")
    if run.get("event") != "pull_request" or run.get("path") != workflow_path:
        _red("RUN_WORKFLOW_MISMATCH")
    repository = run.get("repository")
    if not isinstance(repository, Mapping) or repository.get("full_name") != REPOSITORY:
        _red("RUN_REPO_MISMATCH")
    return name


def _snapshot_archive(source: Path, snapshot: Any, *, limit: int) -> tuple[str, int]:
    """Copy once into an anonymous fd; compare the digest of those same bytes."""
    try:
        if not stat.S_ISREG(source.lstat().st_mode):
            _red("ARCHIVE_NOT_REGULAR")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(source, flags)
        with os.fdopen(fd, "rb") as archive:
            if not stat.S_ISREG(os.fstat(archive.fileno()).st_mode):
                _red("ARCHIVE_NOT_REGULAR")
            digest = hashlib.sha256()
            size = 0
            while chunk := archive.read(CHUNK_BYTES):
                size += len(chunk)
                if size > limit:
                    _red("ARCHIVE_SIZE_LIMIT")
                digest.update(chunk)
                snapshot.write(chunk)
    except (OSError, OverflowError) as exc:
        if isinstance(exc, ArtifactByteEvidenceRed):
            raise
        _red("ARCHIVE_IO_RED")
    if size == 0:
        _red("ARCHIVE_EMPTY")
    snapshot.flush()
    snapshot.seek(0)
    return digest.hexdigest(), size


def _verify_zip(snapshot: Any, *, sha: str, tree: str, run_id: int,
                attempt: int, name: str) -> tuple[int, int]:
    try:
        with zipfile.ZipFile(snapshot, "r") as archive:
            infos = archive.infolist()
            if not 2 <= len(infos) <= MAX_MEMBERS:
                _red("ZIP_MEMBER_COUNT_INVALID")
            members: dict[str, zipfile.ZipInfo] = {}
            sum_uncompressed = 0
            sum_compressed = 0
            for info in infos:
                if not _safe_path(info.filename):
                    _red("ZIP_MEMBER_PATH_UNSAFE")
                if info.filename in members:
                    _red("ZIP_MEMBER_DUPLICATE")
                kind = stat.S_IFMT(info.external_attr >> 16)
                if (info.is_dir() or kind not in (0, stat.S_IFREG) or
                        info.flag_bits & 1 or info.compress_type not in
                        (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    _red("ZIP_MEMBER_TYPE_UNSAFE")
                if not _integer(info.file_size) or not _integer(info.compress_size):
                    _red("ZIP_MEMBER_SIZE_INVALID")
                if (info.file_size > MAX_ZIP_EXPANSION_RATIO * info.compress_size or
                        (info.file_size and info.compress_size == 0)):
                    _red("ZIP_EXPANSION_RATIO_LIMIT")
                sum_uncompressed += info.file_size
                sum_compressed += info.compress_size
                if sum_uncompressed > MAX_UNCOMPRESSED_BYTES + MAX_MANIFEST_BYTES:
                    _red("ZIP_UNCOMPRESSED_LIMIT")
                members[info.filename] = info
            if sum_uncompressed > MAX_ZIP_EXPANSION_RATIO * sum_compressed:
                _red("ZIP_EXPANSION_RATIO_LIMIT")
            # Distinct central-directory names can alias in a later consumer:
            # case-insensitive filesystems and a regular file used as a parent.
            folded: set[str] = set()
            for member in members:
                name_folded = member.casefold()
                if name_folded in folded:
                    _red("ZIP_MEMBER_COLLISION")
                folded.add(name_folded)
            for member in members:
                parts = member.split("/")
                if any("/".join(parts[:end]).casefold() in folded
                       for end in range(1, len(parts))):
                    _red("ZIP_MEMBER_COLLISION")
            manifest_info = members.get(MANIFEST_NAME)
            if manifest_info is None or manifest_info.file_size > MAX_MANIFEST_BYTES:
                _red("MANIFEST_MISSING_OR_OVERSIZED")
            with archive.open(manifest_info, "r") as stream:
                data = stream.read(MAX_MANIFEST_BYTES + 1)
            if len(data) > MAX_MANIFEST_BYTES:
                _red("MANIFEST_MISSING_OR_OVERSIZED")
            manifest = _parse_json(data)
            if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS:
                _red("MANIFEST_SCHEMA_INVALID")
            if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
                _red("MANIFEST_SCHEMA_INVALID")
            if manifest["candidate_sha"] != sha:
                _red("MANIFEST_SHA_MISMATCH")
            if manifest["candidate_tree_sha"] != tree:
                _red("MANIFEST_TREE_MISMATCH")
            if not _integer(manifest["run_id"], minimum=1) or manifest["run_id"] != run_id:
                _red("MANIFEST_RUN_MISMATCH")
            if (not _integer(manifest["run_attempt"], minimum=1) or
                    manifest["run_attempt"] != attempt):
                _red("MANIFEST_ATTEMPT_MISMATCH")
            if manifest["artifact_name"] != name:
                _red("MANIFEST_ARTIFACT_NAME_MISMATCH")
            rows = manifest["files"]
            if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_MEMBERS - 1:
                _red("MANIFEST_FILES_INVALID")
            if (not _integer(manifest["file_count"], minimum=1) or
                    manifest["file_count"] != len(rows)):
                _red("MANIFEST_COUNT_MISMATCH")
            if not _integer(manifest["total_size_bytes"], minimum=0):
                _red("MANIFEST_SIZE_MISMATCH")
            expected_paths: set[str] = set()
            expected_total = 0
            last_path = ""
            for item in rows:
                if not isinstance(item, dict) or set(item) != FILE_KEYS:
                    _red("MANIFEST_FILE_SCHEMA_INVALID")
                path = item["path"]
                if (not _safe_path(path) or path == MANIFEST_NAME or
                        path <= last_path or path in expected_paths):
                    _red("MANIFEST_FILE_PATH_INVALID")
                last_path = path
                expected_paths.add(path)
                if item["type"] != "file":
                    _red("MANIFEST_FILE_TYPE_INVALID")
                if (not _integer(item["size_bytes"]) or
                        not isinstance(item["sha256"], str) or
                        not HEX64.fullmatch(item["sha256"])):
                    _red("MANIFEST_FILE_DIGEST_INVALID")
                expected_total += item["size_bytes"]
                if expected_total > MAX_UNCOMPRESSED_BYTES:
                    _red("ZIP_UNCOMPRESSED_LIMIT")
            if manifest["total_size_bytes"] != expected_total:
                _red("MANIFEST_SIZE_MISMATCH")
            if set(members) != expected_paths | {MANIFEST_NAME}:
                _red("ZIP_MEMBERS_UNDECLARED")
            for item in rows:
                info = members[item["path"]]
                if info.file_size != item["size_bytes"]:
                    _red("FILE_SIZE_MISMATCH")
                digest = hashlib.sha256()
                read_size = 0
                with archive.open(info, "r") as stream:
                    while chunk := stream.read(CHUNK_BYTES):
                        read_size += len(chunk)
                        if read_size > item["size_bytes"]:
                            _red("FILE_SIZE_MISMATCH")
                        digest.update(chunk)
                if read_size != item["size_bytes"]:
                    _red("FILE_SIZE_MISMATCH")
                if digest.hexdigest() != item["sha256"]:
                    _red("FILE_DIGEST_MISMATCH")
            return len(rows), expected_total
    except (zipfile.BadZipFile, EOFError, RuntimeError, OSError, ValueError) as exc:
        if isinstance(exc, ArtifactByteEvidenceRed):
            raise
        _red("ZIP_INVALID")


def verify_artifact_bytes(
    archive: Path, *, artifact_receipt: Mapping[str, Any],
    run_receipt: Mapping[str, Any], expected_candidate_sha: str,
    expected_tree_sha: str, expected_run_id: int, expected_run_attempt: int,
    expected_artifact_id: int, expected_digest: str,
    expected_workflow_path: str = PREDEPLOY_WORKFLOW,
    max_archive_bytes: int = MAX_ARCHIVE_BYTES,
) -> dict[str, Any]:
    """Verify exact REST-bound ZIP bytes without declaring deployment approval."""
    if not isinstance(expected_candidate_sha, str) or not HEX40.fullmatch(expected_candidate_sha):
        _red("EXPECTED_SHA_INVALID")
    if not isinstance(expected_tree_sha, str) or not HEX40.fullmatch(expected_tree_sha):
        _red("EXPECTED_TREE_INVALID")
    if not _integer(expected_run_id, minimum=1):
        _red("EXPECTED_RUN_INVALID")
    if not _integer(expected_run_attempt, minimum=1):
        _red("EXPECTED_ATTEMPT_INVALID")
    if not _integer(expected_artifact_id, minimum=1):
        _red("EXPECTED_ARTIFACT_INVALID")
    if not isinstance(expected_workflow_path, str) or expected_workflow_path not in ALLOWED_WORKFLOWS:
        _red("EXPECTED_WORKFLOW_INVALID")
    if not _integer(max_archive_bytes, minimum=1) or max_archive_bytes > MAX_ARCHIVE_BYTES:
        _red("ARCHIVE_BUDGET_INVALID")
    digest = _digest(expected_digest)
    if digest is None:
        _red("EXPECTED_DIGEST_INVALID")
    name = _check_receipts(
        artifact_receipt, run_receipt, sha=expected_candidate_sha,
        run_id=expected_run_id, attempt=expected_run_attempt,
        artifact_id=expected_artifact_id, digest=digest,
        workflow_path=expected_workflow_path,
    )
    try:
        with tempfile.TemporaryFile(mode="w+b") as snapshot:
            actual_digest, size = _snapshot_archive(Path(archive), snapshot,
                                                     limit=max_archive_bytes)
            if actual_digest != digest:
                _red("ZIP_DIGEST_MISMATCH")
            if (not _integer(artifact_receipt.get("size_in_bytes"), minimum=1) or
                    artifact_receipt["size_in_bytes"] != size):
                _red("ARTIFACT_SIZE_MISMATCH")
            files, total = _verify_zip(
                snapshot, sha=expected_candidate_sha, tree=expected_tree_sha,
                run_id=expected_run_id, attempt=expected_run_attempt, name=name,
            )
            snapshot.seek(0)
            last = hashlib.sha256()
            while chunk := snapshot.read(CHUNK_BYTES):
                last.update(chunk)
            if last.hexdigest() != digest:
                _red("SNAPSHOT_CHANGED")
    except OSError:
        _red("SNAPSHOT_IO_RED")
    return {
        "schema_version": 1,
        "status": "BYTES_MATCH_EXPECTED",
        "candidate_sha": expected_candidate_sha,
        "candidate_tree_sha": expected_tree_sha,
        "run_id": expected_run_id,
        "run_attempt": expected_run_attempt,
        "artifact_id": expected_artifact_id,
        "workflow_path": expected_workflow_path,
        "artifact_digest": "sha256:" + digest,
        "file_count": files,
        "total_size_bytes": total,
        "artifact_contents_verified_by_this_guard": True,
        "trusted_bootstrap_verified": False,
        "owner_go_verified": False,
        "automatic_go": False,
        "runtime_verified": False,
    }


def _json_receipt(path: Path) -> Mapping[str, Any]:
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or not 0 < opened.st_size <= MAX_MANIFEST_BYTES:
                _red("REST_RECEIPT_FILE_UNSAFE")
            data = stream.read(MAX_MANIFEST_BYTES + 1)
            after = os.fstat(stream.fileno())
            if (len(data) != opened.st_size or
                    (opened.st_dev, opened.st_ino, opened.st_size,
                     opened.st_mtime_ns, opened.st_ctime_ns) !=
                    (after.st_dev, after.st_ino, after.st_size,
                     after.st_mtime_ns, after.st_ctime_ns)):
                _red("REST_RECEIPT_FILE_UNSAFE")
            named = path.lstat()
            if (not stat.S_ISREG(named.st_mode) or
                    (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)):
                _red("REST_RECEIPT_FILE_UNSAFE")
    except OSError:
        _red("REST_RECEIPT_FILE_UNSAFE")
    receipt = _parse_json(data)
    if not isinstance(receipt, dict):
        _red("REST_RECEIPT_INVALID")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--artifact-receipt", type=Path, required=True)
    parser.add_argument("--run-receipt", type=Path, required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--candidate-tree-sha", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument("--artifact-id", type=int, required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--expected-workflow-path", default=PREDEPLOY_WORKFLOW)
    args = parser.parse_args(argv)
    try:
        result = verify_artifact_bytes(
            args.archive, artifact_receipt=_json_receipt(args.artifact_receipt),
            run_receipt=_json_receipt(args.run_receipt),
            expected_candidate_sha=args.candidate_sha,
            expected_tree_sha=args.candidate_tree_sha,
            expected_run_id=args.run_id, expected_run_attempt=args.run_attempt,
            expected_artifact_id=args.artifact_id, expected_digest=args.digest,
            expected_workflow_path=args.expected_workflow_path,
        )
    except ArtifactByteEvidenceRed as exc:
        print(f"RC6_ARTIFACT_BYTE_GATE=RED|reason={exc}|automatic_go=NO", file=sys.stderr)
        return 79
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
