#!/usr/bin/env python3
"""R163 read-only, default-main POSTRUN public artifact byte authenticator.

It never executes PR code, extracts archives, builds images, sends orders or
changes GitHub/host state. Artifact and REST bytes authenticate a *public V2
projection*, NOT the private original Docker inspect. The producer-supplied
SHA256 of withheld raw inspect is a commitment and is NOT independently replayed
here. Native host/runtime custody remains NO_VERIFICADO.

The same-run R163 proof remains untrusted REVIEW_ONLY. The upstream run must be
completed successfully, source PR #1087 remain at its exact SHA, and the raw
artifact ZIP digest must match GitHub REST. No R144/HOLD79/release gate is lifted.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import io
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import sys
import tarfile
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib


REPOSITORY = "mbalbo2023/Porota-trading"
PRODUCT_BRANCH = "deploy/rc6-pr69-isolated-20260915"
CANDIDATE_REF = "integration/rc6-a1115-r87-unified-paper-candidate-20261110"
PR_NUMBER = 1087
UPSTREAM_NAME = "RC6 R163 Offhost Guard79 Proof (PROBE ONLY)"
UPSTREAM_PATH = ".github/workflows/rc6-r163-offhost-guard79-proof.yml"
TRUSTED_PUBLISHER_NAME = "RC6 R206 Trusted Main Preupload Publisher (PROBE ONLY)"
TRUSTED_PUBLISHER_PATH = ".github/workflows/rc6-r163-postrun-trusted-main.yml"
ZIP_MANIFEST = "porota-artifact-byte-manifest-v1.json"
ZIP_PAYLOAD = frozenset({
    "source-inspect.json", "collector-inspect.json", "custody.json",
    "native.tar", "native-manifest.json",
})
TAR_FILES = frozenset({
    "native-bound.xml", "session/dashboard_auth_52.xml",
    "session/publication_io_identity.xml", "session/scalping_capacity.xml",
    "session/scheduler_monitor_backup.xml", "session/signals_risk_exit_ledger.xml",
    "session/sources_readiness_network.xml", "session/sql_shadow_wal.xml",
    "session/treasury_caucion.xml", "session/stress.json",
    "catalog/full-catalog.json", "cadence/cadence-fresh.json",
    "cadence/cadence-legacy.json",
})
# R206: the PR-owned collector already emits these 13 fixed public projections.
# Validate them independently in trusted main; hash-only TAR verification is
# insufficient because a fully resealed TAR could otherwise contain opaque
# private XML attributes, arbitrary JSON fields or a forged release signal.
PUBLIC_XML_FILES = frozenset(name for name in TAR_FILES if name.endswith(".xml"))
PUBLIC_JSON_GENERATIONS = {
    "session/stress.json": 2,
    "catalog/full-catalog.json": 2,
    "cadence/cadence-fresh.json": 12,
    "cadence/cadence-legacy.json": 12,
}
MAX_PUBLIC_CASES = 100000
MAX_PUBLIC_FILE_BYTES = 8 * 1024 * 1024
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
UTC_RFC3339 = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
CID64 = re.compile(r"[0-9a-f]{64}\Z")
PATH_SEGMENT = re.compile(r"[a-zA-Z0-9_.+@-]+\Z")
ZIP_KEYS = frozenset({"schema_version", "candidate_sha", "candidate_tree_sha",
                      "run_id", "run_attempt", "artifact_name", "file_count",
                      "total_size_bytes", "files"})
ZIP_FILE_KEYS = frozenset({"path", "type", "size_bytes", "sha256"})
NATIVE_KEYS = frozenset({
    "schema", "schema_version", "status", "candidate_sha", "candidate_tree_sha",
    "image_id", "run_id", "run_attempt", "role", "source_cid",
    "collector_cid", "volume_name", "tar_sha256", "tar_size_bytes",
    "file_count", "files", "host_or_runtime_verified",
})
CUSTODY_KEYS = frozenset({
    "schema_version", "status", "candidate_sha", "candidate_tree_sha",
    "image_id", "run_id", "run_attempt", "role", "source_cid",
    "collector_cid", "volume_name", "mount_target", "uid", "gid",
    "host_or_runtime_verified",
})
# Public artifact contract. Private Docker inspect bytes are never available here.
# SHA256 of the private raw bytes is a commitment, not replayable evidence.
PUBLIC_INSPECT_SCHEMA = "RC6_R163_PUBLIC_INSPECT_V2"
PUBLIC_INSPECT_KEYS = frozenset({
    "schema", "schema_version", "role", "status", "raw_inspect",
    "source_redaction", "record", "host_or_runtime_verified",
})
PUBLIC_RECORD_KEYS = frozenset({"Id", "Image", "Config", "HostConfig", "Mounts"})
PUBLIC_MOUNT_KEYS = frozenset({
    "Type", "Name", "Driver", "Destination", "Mode", "RW",
})
PUBLIC_HOST_KEYS = frozenset({
    "Privileged", "ReadonlyRootfs", "NetworkMode", "CapDrop", "CapAdd",
    "Devices", "Tmpfs", "PublishAllPorts", "PortBindings", "Links",
    "ExtraHosts", "SecurityOpt", "Binds", "IpcMode", "UTSMode",
    "PidMode", "UsernsMode", "CgroupnsMode", "GroupAdd", "DeviceRequests",
    "DeviceCgroupRules", "Runtime", "Sysctls", "CgroupParent",
    "VolumesFrom", "Mounts",
})
MAX_PUBLIC_INSPECT_BYTES = 262144

MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_MANIFEST_BYTES = 512 * 1024
MAX_ZIP_BYTES = 64 * 1024 * 1024
MAX_ZIP_UNCOMPRESSED = 64 * 1024 * 1024
MAX_TAR_BYTES = 32 * 1024 * 1024
MAX_TAR_PAYLOAD = 24 * 1024 * 1024
MAX_TAR_FILE = 8 * 1024 * 1024
MAX_ZIP_EXPANSION_RATIO = 200


class PostrunEvidenceRed(ValueError):
    """Bounded reason only; never include untrusted URLs, JSON or credentials."""


def _red(reason: str) -> None:
    raise PostrunEvidenceRed(reason)


def _positive(value: Any) -> bool:
    return type(value) is int and value > 0



def _utc_timestamp(value: Any, reason: str) -> datetime:
    """Exact GitHub UTC second/fraction timestamp; reject offsets/coercions."""
    if type(value) is not str or UTC_RFC3339.fullmatch(value) is None:
        _red(reason)
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _red(reason)


def _sha(value: Any) -> bool:
    return type(value) is str and HEX40.fullmatch(value) is not None


def _hex64(value: Any) -> bool:
    return type(value) is str and HEX64.fullmatch(value) is not None


def _safe_path(path: Any) -> bool:
    return (type(path) is str and 0 < len(path) <= 1024 and
            all(segment not in (".", "..") and PATH_SEGMENT.fullmatch(segment)
                for segment in path.split("/")))


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _red("JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def load_unique_json(raw: bytes) -> Any:
    """Strict JSON, including finite floats, with no duplicate object keys."""
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_JSON_BYTES:
        _red("JSON_SIZE_INVALID")

    def finite_float(token: str) -> float:
        result = float(token)
        if not math.isfinite(result):
            _red("JSON_NONFINITE")
        return result

    try:
        return json.loads(raw, object_pairs_hook=_unique_pairs,
                          parse_constant=lambda _: _red("JSON_NONFINITE"),
                          parse_float=finite_float)
    except (ValueError, UnicodeDecodeError, RecursionError, OverflowError) as exc:
        if isinstance(exc, PostrunEvidenceRed):
            raise
        _red("JSON_INVALID")


def _require_dict(value: Any, reason: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        _red(reason)
    return value


def _name(sha: str, run_id: int, attempt: int) -> str:
    return f"rc6-r163-offhost-{run_id}-{attempt}-{sha}"


def _workflow_path_exact(path: Any, expected: str, *,
                         branch: str, sha: str,
                         upstream_pull: bool = False) -> bool:
    """Accept documented GitHub REST @ref variants without prefix matching.

    GitHub's workflow-runs REST may report a workflow path followed by a
    concrete ref. Never accept an arbitrary @branch or a different PR/SHA.
    """
    variants = {expected, f"{expected}@{branch}",
                f"{expected}@refs/heads/{branch}", f"{expected}@{sha}"}
    if upstream_pull:
        variants.add(f"{expected}@refs/pull/{PR_NUMBER}/merge")
    return type(path) is str and path in variants


def _identity(event: Mapping[str, Any], run: Mapping[str, Any],
              pr: Mapping[str, Any], commit: Mapping[str, Any],
              expected_run_id: int, expected_run_attempt: int, *,
              expected_conclusion: str = "success") -> tuple[str, str, str]:
    if not _positive(expected_run_id) or not _positive(expected_run_attempt):
        _red("EXPECTED_RUN_INVALID")
    if (not isinstance(event, dict) or event.get("action") != "completed" or
            not isinstance(event.get("repository"), dict) or
            event["repository"].get("full_name") != REPOSITORY):
        _red("EVENT_IDENTITY_MISMATCH")
    event_run = event.get("workflow_run")
    if (not isinstance(event_run, dict) or
            type(event_run.get("id")) is not int or
            event_run.get("id") != expected_run_id or
            type(event_run.get("run_attempt")) is not int or
            event_run.get("run_attempt") != expected_run_attempt):
        _red("EVENT_RUN_MISMATCH")
    if (event_run.get("status") != "completed" or
            event_run.get("conclusion") != expected_conclusion or
            event_run.get("event") != "pull_request" or
            event_run.get("name") != UPSTREAM_NAME):
        _red("EVENT_UPSTREAM_NOT_SUCCESS")
    if not isinstance(run, dict) or run.get("id") != expected_run_id or not _positive(run.get("id")):
        _red("RUN_ID_MISMATCH")
    if (run.get("run_attempt") != expected_run_attempt or
            not _positive(run.get("run_attempt"))):
        _red("RUN_ATTEMPT_MISMATCH")
    if run.get("status") != "completed" or run.get("conclusion") != expected_conclusion:
        _red("RUN_NOT_SUCCESS")
    if run.get("event") != "pull_request":
        _red("RUN_EVENT_MISMATCH")
    sha = run.get("head_sha")
    if (not _sha(sha) or
            not _workflow_path_exact(
                run.get("path"), UPSTREAM_PATH, branch=CANDIDATE_REF,
                sha=sha, upstream_pull=True) or
            run.get("name") != UPSTREAM_NAME):
        _red("RUN_WORKFLOW_MISMATCH")
    if (not isinstance(run.get("repository"), dict) or
            run["repository"].get("full_name") != REPOSITORY or
            not isinstance(run.get("head_repository"), dict) or
            run["head_repository"].get("full_name") != REPOSITORY):
        _red("RUN_REPO_MISMATCH")
    if event_run.get("head_sha") != sha:
        _red("RUN_SHA_MISMATCH")
    if not _workflow_path_exact(
        event_run.get("path"), UPSTREAM_PATH, branch=CANDIDATE_REF,
        sha=sha, upstream_pull=True):
        _red("EVENT_WORKFLOW_MISMATCH")
    linked_prs = run.get("pull_requests")
    if (not isinstance(linked_prs, list) or len(linked_prs) != 1 or
            not isinstance(linked_prs[0], dict) or
            linked_prs[0].get("number") != PR_NUMBER or
            not isinstance(linked_prs[0].get("head"), dict) or
            linked_prs[0]["head"].get("sha") != sha or
            not isinstance(linked_prs[0].get("base"), dict) or
            linked_prs[0]["base"].get("ref") != PRODUCT_BRANCH):
        _red("RUN_PR_MISMATCH")
    if (not isinstance(pr, dict) or pr.get("number") != PR_NUMBER or
            pr.get("state") != "open" or type(pr.get("draft")) is not bool or
            not isinstance(pr.get("head"), dict) or
            not isinstance(pr.get("base"), dict)):
        _red("PR_IDENTITY_MISMATCH")
    head, base = pr["head"], pr["base"]
    if (head.get("ref") != CANDIDATE_REF or
            not isinstance(head.get("repo"), dict) or
            head["repo"].get("full_name") != REPOSITORY or
            run.get("head_branch") != CANDIDATE_REF):
        _red("PR_HEAD_REF_MISMATCH")
    if (base.get("ref") != PRODUCT_BRANCH or
            not isinstance(base.get("repo"), dict) or
            base["repo"].get("full_name") != REPOSITORY):
        _red("PR_BASE_MISMATCH")
    if head.get("sha") != sha:
        _red("PR_HEAD_STALE")
    if (not isinstance(commit, dict) or commit.get("sha") != sha or
            not isinstance(commit.get("tree"), dict) or
            not _sha(commit["tree"].get("sha"))):
        _red("COMMIT_TREE_INVALID")
    return sha, commit["tree"]["sha"], _name(sha, expected_run_id, expected_run_attempt)



def _attempt_window(attempt: Mapping[str, Any], *,
                    sha: str, run_id: int, attempt_number: int,
                    expected_branch: str = CANDIDATE_REF,
                    expected_path: str = UPSTREAM_PATH,
                    expected_name: str = UPSTREAM_NAME,
                    expected_event: str = "pull_request",
                    expected_conclusion: str = "success") -> tuple[datetime, datetime]:
    """Bind the *specific* REST run attempt, not the original run's created_at.

    A previous attempt may upload an artifact bearing the future attempt name.
    Only GitHub's authenticated attempt-N run_started_at establishes when
    attempt N actually began; timestamp equality is ambiguous at seconds
    precision and is intentionally rejected for artifact creation.
    """
    if (type(attempt) is not dict or
            not _positive(attempt.get("id")) or attempt["id"] != run_id or
            not _positive(attempt.get("run_attempt")) or
            attempt["run_attempt"] != attempt_number or
            attempt.get("head_sha") != sha or
            attempt.get("head_branch") != expected_branch or
            not _workflow_path_exact(
                attempt.get("path"), expected_path,
                branch=expected_branch, sha=sha,
                upstream_pull=(expected_path == UPSTREAM_PATH)) or
            attempt.get("name") != expected_name or
            attempt.get("event") != expected_event or
            type(attempt.get("repository")) is not dict or
            attempt["repository"].get("full_name") != REPOSITORY or
            type(attempt.get("head_repository")) is not dict or
            attempt["head_repository"].get("full_name") != REPOSITORY):
        _red("ATTEMPT_IDENTITY_MISMATCH")
    if attempt.get("status") != "completed" or attempt.get("conclusion") != expected_conclusion:
        _red("ATTEMPT_NOT_SUCCESS")
    start = _utc_timestamp(attempt.get("run_started_at"), "ATTEMPT_TIMESTAMP_INVALID")
    created = _utc_timestamp(attempt.get("created_at"), "ATTEMPT_TIMESTAMP_INVALID")
    finish = _utc_timestamp(attempt.get("updated_at"), "ATTEMPT_TIMESTAMP_INVALID")
    if not start <= created <= finish or not start < finish:
        _red("ATTEMPT_TIMESTAMP_INVALID")
    return start, finish


def verify_trusted_publisher_identity(*, upstream_event: Mapping[str, Any],
                                      upstream_run: Mapping[str, Any],
                                      upstream_attempt: Mapping[str, Any],
                                      pr: Mapping[str, Any],
                                      commit: Mapping[str, Any],
                                      publisher_run: Mapping[str, Any],
                                      publisher_attempt: Mapping[str, Any],
                                      expected_upstream_run_id: int,
                                      expected_upstream_attempt: int,
                                      expected_publisher_run_id: int,
                                      expected_publisher_attempt: int) -> dict[str, Any]:
    """R206 two distinct REST identities; not publication or CI attestation.

    The current PR producer intentionally exits 79 after its native tests.
    A *failed* upstream run is therefore accepted only for identity inventory:
    its test outcomes and the reason for failure are NOT authenticated here.
    Publisher P/N must already be completed/success. Never use this during its
    own upload to synthesize a completed receipt, or infer a release GO.
    """
    if (not _positive(expected_upstream_run_id) or
            not _positive(expected_upstream_attempt) or
            not _positive(expected_publisher_run_id) or
            not _positive(expected_publisher_attempt) or
            expected_upstream_run_id == expected_publisher_run_id):
        _red("PUBLISHER_UPSTREAM_ALIAS")
    sha, tree, _ = _identity(
        upstream_event, upstream_run, pr, commit,
        expected_upstream_run_id, expected_upstream_attempt,
        expected_conclusion="failure")
    _attempt_window(
        upstream_attempt, sha=sha, run_id=expected_upstream_run_id,
        attempt_number=expected_upstream_attempt,
        expected_conclusion="failure")
    if (type(publisher_run) is not dict or
            publisher_run.get("id") != expected_publisher_run_id or
            publisher_run.get("run_attempt") != expected_publisher_attempt or
            publisher_run.get("status") != "completed" or
            publisher_run.get("conclusion") != "success" or
            publisher_run.get("event") != "workflow_run" or
            not _workflow_path_exact(
                publisher_run.get("path"), TRUSTED_PUBLISHER_PATH,
                branch="main", sha=publisher_run.get("head_sha")) or
            publisher_run.get("name") != TRUSTED_PUBLISHER_NAME or
            publisher_run.get("head_branch") != "main" or
            not _sha(publisher_run.get("head_sha")) or
            type(publisher_run.get("repository")) is not dict or
            publisher_run["repository"].get("full_name") != REPOSITORY or
            type(publisher_run.get("head_repository")) is not dict or
            publisher_run["head_repository"].get("full_name") != REPOSITORY):
        _red("PUBLISHER_IDENTITY_MISMATCH")
    if (type(publisher_attempt) is not dict or
            publisher_attempt.get("run_attempt") != expected_publisher_attempt):
        _red("PUBLISHER_ATTEMPT_MISMATCH")
    _attempt_window(
        publisher_attempt, sha=publisher_run["head_sha"],
        run_id=expected_publisher_run_id,
        attempt_number=expected_publisher_attempt,
        expected_branch="main", expected_path=TRUSTED_PUBLISHER_PATH,
        expected_name=TRUSTED_PUBLISHER_NAME,
        expected_event="workflow_run")
    return {
        "status": "DUAL_IDENTITY_REST_SHAPE_ONLY",
        "candidate_sha": sha, "candidate_tree_sha": tree,
        "upstream_run_id": expected_upstream_run_id,
        "upstream_attempt": expected_upstream_attempt,
        "publisher_main_sha": publisher_run["head_sha"],
        "publisher_run_id": expected_publisher_run_id,
        "publisher_attempt": expected_publisher_attempt,
        "upstream_ci_green_verified": False,
        "publisher_to_upstream_event_link_verified": False,
        "native_payload_verified": False,
        "release_go": False, "runtime_verified": False,
    }


def assert_trusted_preupload_writer_isolation(*, publisher_uid: int,
                                              candidate_writer_uid: int,
                                              publisher_staging_root: Path,
                                              candidate_can_access_staging: bool) -> None:
    """Fail-closed host staging check; does not prove OCI/mount isolation.

    A true caller-supplied reachability signal *always* fails. For a false
    signal, a trusted executor must additionally verify its actual Docker
    mounts/user namespace. This function alone can never authorize upload.
    """
    if (type(publisher_uid) is not int or publisher_uid < 0 or
            type(candidate_writer_uid) is not int or candidate_writer_uid < 0 or
            type(candidate_can_access_staging) is not bool or
            not isinstance(publisher_staging_root, Path)):
        _red("PREUPLOAD_IDENTITY_INVALID")
    # A claimed inaccessible path is never enough if both processes have the
    # same host UID. Docker namespacing alone is not an attestation of remap.
    if publisher_uid == candidate_writer_uid:
        _red("PREUPLOAD_SAME_UID_WRITER")
    if candidate_can_access_staging:
        _red("PREUPLOAD_WRITER_REACHABLE")
    try:
        metadata = publisher_staging_root.lstat()
    except OSError:
        _red("PREUPLOAD_STAGE_UNSAFE")
    if (not stat.S_ISDIR(metadata.st_mode) or
            metadata.st_uid != publisher_uid or
            stat.S_IMODE(metadata.st_mode) != 0o700):
        _red("PREUPLOAD_STAGE_UNSAFE")


def _canonical_public_json(document: Mapping[str, Any]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def _trusted_project_private_inspect(*, raw: bytes, collector: bool,
                                     source_cid: str, collector_cid: str,
                                     candidate_sha: str, tree: str, image_id: str,
                                     volume: str, run_id: int, attempt: int) -> bytes:
    """Publish a fixed V2 projection from actual trusted-runner Docker bytes.

    Raw Config.Env, Source mounts, logs, driver metadata and any unknown
    private fields are never serialized. The commitment to raw inspect bytes
    is *not* independently replayable by the later public auditor.
    """
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_PUBLIC_INSPECT_BYTES:
        _red("PREUPLOAD_PRIVATE_INSPECT_INVALID")
    parsed = load_unique_json(raw)
    if (type(parsed) is not list or len(parsed) != 1 or
            type(parsed[0]) is not dict):
        _red("PREUPLOAD_PRIVATE_INSPECT_INVALID")
    entry = parsed[0]
    host, config, mounts = (entry.get("HostConfig"), entry.get("Config"),
                            entry.get("Mounts"))
    if (type(host) is not dict or type(config) is not dict or
            type(mounts) is not list or len(mounts) != 1 or
            type(mounts[0]) is not dict or
            type(host.get("Mounts")) not in (list, type(None))):
        _red("PREUPLOAD_PRIVATE_INSPECT_INVALID")
    private_mount = mounts[0]
    if (private_mount.get("Source") is None or
            type(private_mount.get("Source")) is not str or
            not private_mount["Source"]):
        _red("PREUPLOAD_PRIVATE_SOURCE_MISSING")
    for key in PUBLIC_MOUNT_KEYS:
        if key not in private_mount:
            _red("PREUPLOAD_PRIVATE_MOUNT_INVALID")
    safe_host = {key: value for key, value in host.items()
                 if key in PUBLIC_HOST_KEYS}
    declaration = "NOT_APPLICABLE"
    if not collector:
        original = safe_host.get("Mounts")
        if type(original) is not list or len(original) != 1 or type(original[0]) is not dict:
            _red("PREUPLOAD_PRIVATE_MOUNT_INVALID")
        if "Source" in original[0] and original[0]["Source"] != "":
            # An explicit named/bind source must never be relabeled as an
            # empty anonymous-volume declaration by this redaction.
            _red("PREUPLOAD_PRIVATE_MOUNT_INVALID")
        declaration = "EMPTY" if "Source" in original[0] else "ABSENT"
        safe_host["Mounts"] = [{key: value for key, value in original[0].items()
                               if key != "Source"}]
    document = {
        "schema": PUBLIC_INSPECT_SCHEMA,
        "schema_version": 2,
        "role": "bundle-collector" if collector else "bundle",
        "status": "OFFHOST_PROJECTED_EVIDENCE_ONLY",
        "raw_inspect": {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw), "published": False,
        },
        "source_redaction": {
            "hostconfig_declaration": declaration,
            "runtime_source_nonempty_observed": True,
        },
        "record": {
            "Id": entry.get("Id"), "Image": entry.get("Image"),
            "Config": {"User": config.get("User"), "Labels": config.get("Labels")},
            "HostConfig": safe_host,
            "Mounts": [{key: private_mount[key] for key in PUBLIC_MOUNT_KEYS}],
        },
        "host_or_runtime_verified": False,
    }
    _validate_public_inspect(
        document, cid=collector_cid if collector else source_cid,
        source_cid=source_cid, image_id=image_id, volume=volume,
        sha=candidate_sha, tree=tree, run_id=run_id,
        attempt=attempt, collector=collector)
    return _canonical_public_json(document)


def build_trusted_preupload_members(*, producer_private_inspect: bytes,
                                    collector_private_inspect: bytes,
                                    native_tar: bytes,
                                    candidate_sha: str, candidate_tree_sha: str,
                                    run_id: int, run_attempt: int,
                                    image_id: str, source_cid: str,
                                    collector_cid: str, volume_name: str,
                                    publisher_staging_root: Path,
                                    publisher_uid: int, candidate_writer_uid: int,
                                    candidate_can_access_staging: bool) -> dict[str, bytes]:
    """Produce exactly five public members in trusted memory before upload.

    A trusted workflow must gather Docker inspect/collector stdout itself and
    assert the runner/container mount boundary; this pure verifier cannot
    create OCI isolation or independently attest the candidate's test runs.
    """
    assert_trusted_preupload_writer_isolation(
        publisher_uid=publisher_uid, candidate_writer_uid=candidate_writer_uid,
        publisher_staging_root=publisher_staging_root,
        candidate_can_access_staging=candidate_can_access_staging)
    if (not _sha(candidate_sha) or not _sha(candidate_tree_sha) or
            not _positive(run_id) or not _positive(run_attempt) or
            type(image_id) is not str or
            re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None or
            any(type(value) is not str or CID64.fullmatch(value) is None
                for value in (source_cid, collector_cid, volume_name)) or
            source_cid == collector_cid):
        _red("PREUPLOAD_EXPECTED_IDENTITY_INVALID")
    files = _native_tar_files(native_tar)
    members = {
        "source-inspect.json": _trusted_project_private_inspect(
            raw=producer_private_inspect, collector=False,
            source_cid=source_cid, collector_cid=collector_cid,
            candidate_sha=candidate_sha, tree=candidate_tree_sha,
            image_id=image_id, volume=volume_name, run_id=run_id,
            attempt=run_attempt),
        "collector-inspect.json": _trusted_project_private_inspect(
            raw=collector_private_inspect, collector=True,
            source_cid=source_cid, collector_cid=collector_cid,
            candidate_sha=candidate_sha, tree=candidate_tree_sha,
            image_id=image_id, volume=volume_name, run_id=run_id,
            attempt=run_attempt),
        "native.tar": native_tar,
    }
    custody = {
        "schema_version": 1, "status": "OFFHOST_CONTRACT_ONLY",
        "candidate_sha": candidate_sha, "candidate_tree_sha": candidate_tree_sha,
        "image_id": image_id, "run_id": str(run_id),
        "run_attempt": str(run_attempt), "role": "bundle",
        "source_cid": source_cid, "collector_cid": collector_cid,
        "volume_name": volume_name, "mount_target": "/app/data",
        "uid": 1000, "gid": 1000, "host_or_runtime_verified": False,
    }
    native = {
        "schema": "RC6_R163_NATIVE_TAR_V1", "schema_version": 1,
        "status": "OFFHOST_BYTES_ONLY", "candidate_sha": candidate_sha,
        "candidate_tree_sha": candidate_tree_sha, "image_id": image_id,
        "run_id": str(run_id), "run_attempt": str(run_attempt),
        "role": "bundle", "source_cid": source_cid,
        "collector_cid": collector_cid, "volume_name": volume_name,
        "tar_sha256": hashlib.sha256(native_tar).hexdigest(),
        "tar_size_bytes": len(native_tar), "file_count": len(files),
        "files": files, "host_or_runtime_verified": False,
    }
    members["custody.json"] = _canonical_public_json(custody)
    members["native-manifest.json"] = _canonical_public_json(native)
    if len(members) != 5 or set(members) != ZIP_PAYLOAD:
        _red("PREUPLOAD_PAYLOAD_SET_INVALID")
    if _native_evidence(members, sha=candidate_sha,
                        tree=candidate_tree_sha, run_id=run_id,
                        attempt=run_attempt) != 13:
        _red("PREUPLOAD_PAYLOAD_INVALID")
    return members


def _read_owned_preupload_file(path: Path, *, owner_uid: int,
                               limit: int) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        _red("PREUPLOAD_INPUT_UNSAFE")
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != owner_uid or
                before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o600 or
                not 0 < before.st_size <= limit):
            _red("PREUPLOAD_INPUT_UNSAFE")
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(fd, min(1024 * 1024, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(fd)
        immutable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid",
                     "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        if (len(data) != before.st_size or
                any(getattr(before, key) != getattr(after, key)
                    for key in immutable)):
            _red("PREUPLOAD_INPUT_CHANGED")
        return bytes(data)
    except OSError:
        _red("PREUPLOAD_INPUT_UNSAFE")
    finally:
        os.close(fd)


def seal_trusted_preupload_directory(*, publisher_staging_root: Path,
                                     input_root: Path, output_name: str,
                                     candidate_sha: str, candidate_tree_sha: str,
                                     run_id: int, run_attempt: int,
                                     image_id: str, source_cid: str,
                                     collector_cid: str, volume_name: str,
                                     publisher_uid: int, candidate_writer_uid: int,
                                     candidate_can_access_staging: bool) -> Path:
    """Seal six public-only members into a trusted, exclusive 0700 directory.

    Does not upload, self-attest, or grant release. Candidate Docker code must
    never receive a bind mount of this staging tree; preupload caller verifies
    the Docker inspect contract independently before using this helper.
    """
    assert_trusted_preupload_writer_isolation(
        publisher_uid=publisher_uid, candidate_writer_uid=candidate_writer_uid,
        publisher_staging_root=publisher_staging_root,
        candidate_can_access_staging=candidate_can_access_staging)
    if (not isinstance(input_root, Path) or input_root.parent != publisher_staging_root or
            type(output_name) is not str or
            not re.fullmatch(r"[a-z0-9_-]{1,40}", output_name)):
        _red("PREUPLOAD_INPUT_ROOT_UNSAFE")
    try:
        input_meta = input_root.lstat()
    except OSError:
        _red("PREUPLOAD_INPUT_ROOT_UNSAFE")
    if (not stat.S_ISDIR(input_meta.st_mode) or
            input_meta.st_uid != publisher_uid or
            stat.S_IMODE(input_meta.st_mode) != 0o700 or
            sorted(p.name for p in input_root.iterdir()) !=
            ["collector-inspect.json", "native.tar", "source-inspect.json"]):
        _red("PREUPLOAD_INPUT_ROOT_UNSAFE")
    private_source = _read_owned_preupload_file(
        input_root / "source-inspect.json", owner_uid=publisher_uid,
        limit=MAX_PUBLIC_INSPECT_BYTES)
    private_collector = _read_owned_preupload_file(
        input_root / "collector-inspect.json", owner_uid=publisher_uid,
        limit=MAX_PUBLIC_INSPECT_BYTES)
    native_tar = _read_owned_preupload_file(
        input_root / "native.tar", owner_uid=publisher_uid,
        limit=MAX_TAR_BYTES)
    members = build_trusted_preupload_members(
        producer_private_inspect=private_source,
        collector_private_inspect=private_collector,
        native_tar=native_tar, candidate_sha=candidate_sha,
        candidate_tree_sha=candidate_tree_sha,
        run_id=run_id, run_attempt=run_attempt, image_id=image_id,
        source_cid=source_cid, collector_cid=collector_cid,
        volume_name=volume_name, publisher_staging_root=publisher_staging_root,
        publisher_uid=publisher_uid, candidate_writer_uid=candidate_writer_uid,
        candidate_can_access_staging=candidate_can_access_staging)
    name = _name(candidate_sha, run_id, run_attempt)
    rows = [{"path": key, "type": "file", "size_bytes": len(value),
             "sha256": hashlib.sha256(value).hexdigest()}
            for key, value in sorted(members.items())]
    manifest = {
        "schema_version": 1, "candidate_sha": candidate_sha,
        "candidate_tree_sha": candidate_tree_sha,
        "run_id": run_id, "run_attempt": run_attempt,
        "artifact_name": name, "file_count": len(rows),
        "total_size_bytes": sum(row["size_bytes"] for row in rows),
        "files": rows,
    }
    members[ZIP_MANIFEST] = _canonical_public_json(manifest)
    preview = io.BytesIO()
    with zipfile.ZipFile(preview, mode="w", compression=zipfile.ZIP_STORED) as zf:
        for relative, data in sorted(members.items()):
            zf.writestr(relative, data)
    raw = preview.getvalue()
    payload = _zip_evidence(
        raw, sha=candidate_sha, tree=candidate_tree_sha, name=name,
        run_id=run_id, attempt=run_attempt,
        digest=hashlib.sha256(raw).hexdigest(), expected_size=len(raw))
    _native_evidence(payload, sha=candidate_sha, tree=candidate_tree_sha,
                     run_id=run_id, attempt=run_attempt)
    output_dir = publisher_staging_root / output_name
    try:
        output_dir.mkdir(mode=0o700)
        for relative, data in sorted(members.items()):
            fd = os.open(output_dir / relative, os.O_WRONLY | os.O_CREAT |
                         os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            try:
                with os.fdopen(fd, "wb", closefd=False) as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(fd)
            finally:
                os.close(fd)
        if (sorted(p.name for p in output_dir.iterdir()) != sorted(members) or
                stat.S_IMODE(output_dir.stat().st_mode) != 0o700):
            _red("PREUPLOAD_OUTPUT_UNSAFE")
    except OSError:
        _red("PREUPLOAD_OUTPUT_UNSAFE")
    return output_dir


def _select_attempt_artifact(artifact_list: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    if not isinstance(artifact_list, dict) or not isinstance(artifact_list.get("artifacts"), list):
        _red("ARTIFACT_LIST_AMBIGUOUS")
    rows = artifact_list["artifacts"]
    if (type(artifact_list.get("total_count")) is not int or
            not 1 <= artifact_list["total_count"] <= 100 or
            len(rows) != artifact_list["total_count"] or
            not all(type(row) is dict and _positive(row.get("id")) for row in rows) or
            len({row["id"] for row in rows}) != len(rows)):
        _red("ARTIFACT_LIST_AMBIGUOUS")
    matches = [row for row in rows if row.get("name") == name]
    if len(matches) != 1:
        _red("ARTIFACT_LIST_AMBIGUOUS")
    return matches[0]


def _artifact_identity(artifact_list: Mapping[str, Any], artifact: Mapping[str, Any],
                       *, name: str, sha: str, run_id: int,
                       attempt_window: tuple[datetime, datetime]) -> tuple[int, str, int]:
    listed = _select_attempt_artifact(artifact_list, name)
    if not isinstance(artifact, dict) or artifact.get("id") != listed["id"]:
        _red("ARTIFACT_ID_MISMATCH")
    if listed.get("name") != name or artifact.get("name") != name:
        _red("ARTIFACT_NAME_MISMATCH")
    if artifact.get("expired") is not False or listed.get("expired") is not False:
        _red("ARTIFACT_EXPIRED")
    if (not _positive(artifact.get("size_in_bytes")) or
            artifact["size_in_bytes"] > MAX_ZIP_BYTES or
            artifact["size_in_bytes"] != listed.get("size_in_bytes")):
        _red("ARTIFACT_SIZE_MISMATCH")
    digest = artifact.get("digest")
    if (type(digest) is not str or not digest.startswith("sha256:") or
            not _hex64(digest[7:]) or listed.get("digest") != digest):
        _red("ARTIFACT_DIGEST_MISMATCH")
    workflow = artifact.get("workflow_run")
    if (not isinstance(workflow, dict) or workflow.get("id") != run_id or
            not _positive(workflow.get("id"))):
        _red("ARTIFACT_RUN_MISMATCH")
    if workflow.get("head_sha") != sha:
        _red("ARTIFACT_SHA_MISMATCH")
    for key in ("created_at", "updated_at"):
        if listed.get(key) != artifact.get(key):
            _red("ARTIFACT_TIMESTAMP_MISMATCH")
    created = _utc_timestamp(artifact.get("created_at"), "ARTIFACT_TIMESTAMP_INVALID")
    updated = _utc_timestamp(artifact.get("updated_at"), "ARTIFACT_TIMESTAMP_INVALID")
    start, finish = attempt_window
    if not start < created <= updated <= finish:
        _red("ARTIFACT_ATTEMPT_WINDOW_INVALID")
    return artifact["id"], digest[7:], artifact["size_in_bytes"]



def _strict_zip32_envelope(
    raw: bytes, archive: zipfile.ZipFile, members: list[zipfile.ZipInfo],
) -> dict[str, tuple[int, int]]:
    """Own every ZIP byte: contiguous local records, CD records and EOCD only.

    GitHub actions/upload-artifact@v4 uses streaming ZIP32 entries with a signed
    16-byte data descriptor (GPBF bit 3). CPython's seekable writer instead
    backpatches local headers. Both are supported; ZIP64, opaque framing,
    comments, extra fields, prefixes, data gaps and trailers are rejected.
    This establishes ZIP framing custody, NOT secrecy of semantic payloads or
    independent authenticity of private Docker inspect bytes.
    """
    reason = "ZIP_ENVELOPE_UNSAFE"
    if len(raw) < 22:
        _red(reason)
    eocd_at = len(raw) - 22
    if raw[eocd_at:eocd_at + 4] != b"PK\x05\x06":
        _red(reason)
    (_, disk, cd_disk, disk_count, total_count, cd_size,
     cd_at, eocd_comment_length) = struct.unpack_from("<IHHHHIIH", raw, eocd_at)
    if (disk != 0 or cd_disk != 0 or
            disk_count != len(members) or total_count != len(members) or
            disk_count == 0xffff or total_count == 0xffff or
            cd_size == 0xffffffff or cd_at == 0xffffffff or
            eocd_comment_length != 0 or archive.comment != b"" or
            cd_at < 0 or cd_size < 0 or
            cd_at + cd_size != eocd_at or archive.start_dir != cd_at):
        _red(reason)

    # The central directory must contain exactly the same six records that
    # zipfile enumerated, with no unclaimed metadata or padding.
    cursor = cd_at
    central_timestamps: dict[str, tuple[int, int, int]] = {}
    for info in members:
        if (cursor + 46 > eocd_at or
                raw[cursor:cursor + 4] != b"PK\x01\x02"):
            _red(reason)
        (_, _made, version, flags, method, mtime, mdate, crc, csize, usize,
         name_len, extra_len, comment_len, disk_start, _internal, _external,
         local_at) = struct.unpack_from("<IHHHHHHIIIHHHHHII", raw, cursor)
        try:
            expected_name = info.filename.encode("ascii")
        except UnicodeEncodeError:
            _red(reason)
        if (not expected_name or len(expected_name) != name_len or
                cursor + 46 + name_len > eocd_at or
                raw[cursor + 46:cursor + 46 + name_len] != expected_name or
                extra_len != 0 or comment_len != 0 or disk_start != 0 or
                info.extra != b"" or info.comment != b"" or
                flags & ~0x0808 or version > 20 or
                flags != info.flag_bits or method != info.compress_type or
                crc != info.CRC or csize != info.compress_size or
                usize != info.file_size or local_at != info.header_offset or
                csize == 0xffffffff or usize == 0xffffffff):
            _red(reason)
        central_timestamps[info.filename] = (version, mtime, mdate)
        cursor += 46 + name_len
    if cursor != eocd_at:
        _red(reason)

    # Sort physical offsets independently of central enumeration; there must
    # be no prefixes, inter-local gaps, descriptor gaps or pre-CD padding.
    cursor = 0
    segments: dict[str, tuple[int, int]] = {}
    for info in sorted(members, key=lambda item: item.header_offset):
        if (info.header_offset != cursor or cursor + 30 > cd_at or
                raw[cursor:cursor + 4] != b"PK\x03\x04"):
            _red(reason)
        (_, needed, flags, method, mtime, mdate, local_crc,
         local_csize, local_usize, name_len, extra_len) = struct.unpack_from(
            "<IHHHHHIIIHH", raw, cursor
        )
        name = info.filename.encode("ascii")
        if ((needed, mtime, mdate) != central_timestamps[info.filename] or
                flags != info.flag_bits or method != info.compress_type or
                flags & ~0x0808 or extra_len != 0 or
                name_len != len(name) or
                cursor + 30 + name_len > cd_at or
                raw[cursor + 30:cursor + 30 + name_len] != name):
            _red(reason)
        data_at = cursor + 30 + name_len
        data_end = data_at + info.compress_size
        if data_end > cd_at:
            _red(reason)
        segments[info.filename] = (data_at, data_end)
        if flags & 0x0008:
            # archiver/zip-stream's streaming ZIP32 signed descriptor.
            if (local_crc != 0 or local_csize != 0 or local_usize != 0 or
                    data_end + 16 > cd_at or
                    raw[data_end:data_end + 4] != b"PK\x07\x08"):
                _red(reason)
            (crc, csize, usize) = struct.unpack_from("<III", raw, data_end + 4)
            if (crc, csize, usize) != (info.CRC, info.compress_size, info.file_size):
                _red(reason)
            cursor = data_end + 16
        else:
            if ((local_crc, local_csize, local_usize) !=
                    (info.CRC, info.compress_size, info.file_size)):
                _red(reason)
            cursor = data_end
    if cursor != cd_at or len(segments) != len(members):
        _red(reason)
    return segments


def _deflate_consumes_exact_zip_bytes(
    raw: bytes, data_at: int, data_end: int, expected_size: int,
) -> None:
    """Reject secret-bearing trailing bytes ignored by ordinary zipfile read."""
    stream = zlib.decompressobj(-zlib.MAX_WBITS)
    total = 0
    cursor = data_at
    while cursor < data_end:
        chunk = raw[cursor:min(data_end, cursor + 65536)]
        cursor += len(chunk)
        part = stream.decompress(chunk, expected_size - total + 1)
        total += len(part)
        if (total > expected_size or stream.unconsumed_tail or
                stream.unused_data or (stream.eof and cursor != data_end)):
            _red("ZIP_DEFLATE_STREAM_UNSAFE")
    if not stream.eof or total != expected_size:
        _red("ZIP_DEFLATE_STREAM_UNSAFE")


def _zip_evidence(raw: bytes, *, sha: str, tree: str, name: str,
                  run_id: int, attempt: int, digest: str,
                  expected_size: int) -> dict[str, bytes]:
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_ZIP_BYTES:
        _red("ZIP_SIZE_LIMIT")
    if len(raw) != expected_size:
        _red("ARTIFACT_SIZE_MISMATCH")
    if hashlib.sha256(raw).hexdigest() != digest:
        _red("ZIP_DIGEST_MISMATCH")
    try:
        with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
            members = archive.infolist()
            if len(members) != len(ZIP_PAYLOAD) + 1:
                _red("ZIP_MEMBER_SET_INVALID")
            index: dict[str, zipfile.ZipInfo] = {}
            uncompressed = compressed = 0
            for item in members:
                if not _safe_path(item.filename):
                    _red("ZIP_PATH_UNSAFE")
                if item.filename in index:
                    _red("ZIP_DUPLICATE_MEMBER")
                kind = stat.S_IFMT(item.external_attr >> 16)
                if (item.is_dir() or kind not in (0, stat.S_IFREG) or
                        item.flag_bits & 1 or
                        item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    _red("ZIP_TYPE_UNSAFE")
                if (type(item.file_size) is not int or type(item.compress_size) is not int or
                        item.file_size < 0 or item.compress_size < 0 or
                        item.file_size > MAX_TAR_BYTES or
                        item.file_size > MAX_ZIP_EXPANSION_RATIO * item.compress_size):
                    _red("ZIP_EXPANSION_LIMIT")
                uncompressed += item.file_size
                compressed += item.compress_size
                if uncompressed > MAX_ZIP_UNCOMPRESSED:
                    _red("ZIP_UNCOMPRESSED_LIMIT")
                index[item.filename] = item
            if uncompressed > MAX_ZIP_EXPANSION_RATIO * compressed:
                _red("ZIP_EXPANSION_LIMIT")
            if set(index) != ZIP_PAYLOAD | {ZIP_MANIFEST}:
                _red("ZIP_MEMBER_SET_INVALID")
            segments = _strict_zip32_envelope(raw, archive, members)
            for item in members:
                if item.compress_type == zipfile.ZIP_DEFLATED:
                    start, end = segments[item.filename]
                    _deflate_consumes_exact_zip_bytes(raw, start, end, item.file_size)
                elif item.compress_size != item.file_size:
                    # CPython ZipExtFile truncates STORED reads at file_size,
                    # silently ignoring the rest of the declared ZIP segment.
                    _red("ZIP_STORED_STREAM_UNSAFE")
            manifest_info = index[ZIP_MANIFEST]
            if not 0 < manifest_info.file_size <= MAX_MANIFEST_BYTES:
                _red("ZIP_MANIFEST_SIZE_INVALID")
            manifest = load_unique_json(archive.read(ZIP_MANIFEST))
            if (type(manifest) is not dict or set(manifest) != ZIP_KEYS or
                    type(manifest["schema_version"]) is not int or
                    manifest["schema_version"] != 1):
                _red("ZIP_MANIFEST_SCHEMA_INVALID")
            if manifest["candidate_sha"] != sha:
                _red("MANIFEST_SHA_MISMATCH")
            if manifest["candidate_tree_sha"] != tree:
                _red("MANIFEST_TREE_MISMATCH")
            if manifest["run_id"] != run_id or type(manifest["run_id"]) is not int:
                _red("MANIFEST_RUN_MISMATCH")
            if manifest["run_attempt"] != attempt or type(manifest["run_attempt"]) is not int:
                _red("MANIFEST_ATTEMPT_MISMATCH")
            if manifest["artifact_name"] != name:
                _red("MANIFEST_NAME_MISMATCH")
            rows = manifest["files"]
            if (type(rows) is not list or len(rows) != len(ZIP_PAYLOAD) or
                    type(manifest["file_count"]) is not int or
                    manifest["file_count"] != len(rows)):
                _red("MANIFEST_FILE_SET_INVALID")
            if (type(manifest["total_size_bytes"]) is not int or
                    manifest["total_size_bytes"] < 0):
                _red("MANIFEST_TOTAL_SIZE_INVALID")
            payload: dict[str, bytes] = {}
            expected_total = 0
            if [row.get("path") for row in rows if type(row) is dict] != sorted(ZIP_PAYLOAD):
                _red("MANIFEST_FILE_SET_INVALID")
            for row in rows:
                if (type(row) is not dict or set(row) != ZIP_FILE_KEYS or
                        row["path"] not in ZIP_PAYLOAD or row["type"] != "file" or
                        type(row["size_bytes"]) is not int or row["size_bytes"] < 0 or
                        not _hex64(row["sha256"])):
                    _red("MANIFEST_FILE_INVALID")
                item = index[row["path"]]
                if item.file_size != row["size_bytes"]:
                    _red("FILE_SIZE_MISMATCH")
                data = archive.read(item)
                if len(data) != row["size_bytes"]:
                    _red("FILE_SIZE_MISMATCH")
                if hashlib.sha256(data).hexdigest() != row["sha256"]:
                    _red("FILE_DIGEST_MISMATCH")
                payload[row["path"]] = data
                expected_total += len(data)
            if manifest["total_size_bytes"] != expected_total:
                _red("MANIFEST_TOTAL_SIZE_INVALID")
            return payload
    except (zipfile.BadZipFile, EOFError, RuntimeError, OSError,
            ValueError, zlib.error, struct.error) as exc:
        if isinstance(exc, PostrunEvidenceRed):
            raise
        _red("ZIP_INVALID")


def _public_native_schema(name: str, raw: bytes) -> None:
    """Reconstruct the sole allowed projection, never parse raw private data.

    XML is a literal synthetic sequence of numbered case elements; JSON is a
    canonical fixed-key object. Nothing from a candidate-controlled raw XML
    attribute, JSON nested field, header or trailer may survive publication.
    The private SHA is explicitly unattested, never proof of source bytes.
    """
    reason = "PUBLIC_NATIVE_SCHEMA_INVALID"
    if (type(name) is not str or name not in TAR_FILES or type(raw) is not bytes or
            not 0 < len(raw) <= MAX_PUBLIC_FILE_BYTES or b"\x00" in raw):
        _red(reason)
    if name in PUBLIC_XML_FILES:
        prefix = (
            '<testsuite schema="RC6_R206_PUBLIC_JUNIT_V1" '
            'status="PRIVATE_SOURCE_UNATTESTED" path="' + name +
            '" name="R206_PUBLIC_SYNTHETIC_ONLY" tests="'
        ).encode("ascii")
        pattern = (
            rb'([1-9][0-9]{0,5})" failures="0" errors="0" skipped="0" '
            rb'private_raw_size_bytes="([1-9][0-9]{0,7})" '
            rb'private_raw_sha256_unattested="([0-9a-f]{64})" release_go="false">'
        )
        if not raw.startswith(prefix):
            _red(reason)
        matched = re.match(pattern, raw[len(prefix):])
        if matched is None:
            _red(reason)
        count, private_size, raw_hash = matched.groups()
        if (int(count) > MAX_PUBLIC_CASES or
                int(private_size) > MAX_PUBLIC_FILE_BYTES):
            _red(reason)
        header = prefix + matched.group(0)
        expected = (
            header + b"".join(
                f'<testcase name="case-{index:06d}"/>'.encode("ascii")
                for index in range(1, int(count) + 1)
            ) + b"</testsuite>\n"
        )
        if raw != expected:
            _red(reason)
    elif name in PUBLIC_JSON_GENERATIONS:
        if len(raw) > 2048:
            _red(reason)
        try:
            document = load_unique_json(raw)
        except PostrunEvidenceRed:
            _red(reason)
        keys = {
            "schema", "status", "source_path", "private_raw_size_bytes",
            "private_raw_sha256_unattested", "synthetic_fixture_only",
            "release_go", "real_orders_sent", "catalog_count",
            "ready_fixture_count", "required_generations",
        }
        if (type(document) is not dict or set(document) != keys or
                document.get("schema") != "RC6_R206_PUBLIC_JSON_V1" or
                document.get("status") != "PRIVATE_SOURCE_UNATTESTED" or
                document.get("source_path") != name or
                type(document.get("private_raw_size_bytes")) is not int or
                not 0 < document["private_raw_size_bytes"] <= MAX_PUBLIC_FILE_BYTES or
                not _hex64(document.get("private_raw_sha256_unattested")) or
                document.get("synthetic_fixture_only") is not True or
                document.get("release_go") is not False or
                type(document.get("real_orders_sent")) is not int or
                document["real_orders_sent"] != 0 or
                type(document.get("catalog_count")) is not int or
                document["catalog_count"] != 14611 or
                type(document.get("ready_fixture_count")) is not int or
                document["ready_fixture_count"] != 9902 or
                type(document.get("required_generations")) is not int or
                document["required_generations"] != PUBLIC_JSON_GENERATIONS[name]):
            _red(reason)
        expected = (json.dumps(document, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
        if raw != expected:
            _red(reason)
    else:
        _red(reason)


def _native_tar_files(raw: bytes) -> list[dict[str, Any]]:
    if (type(raw) is not bytes or not 1024 <= len(raw) <= MAX_TAR_BYTES or
            len(raw) % 512 or raw[-1024:] != bytes(1024)):
        _red("TAR_FORMAT_INVALID")
    found: dict[str, dict[str, Any]] = {}
    contents: dict[str, bytes] = {}
    cursor = total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for item in archive:
                if (item.offset != cursor or item.offset_data != cursor + 512 or
                        item.pax_headers):
                    _red("TAR_HEADER_UNSAFE")
                if type(item.size) is not int or not 0 < item.size <= MAX_TAR_FILE:
                    _red("TAR_SIZE_INVALID")
                cursor = item.offset_data + ((item.size + 511) // 512) * 512
                if cursor > len(raw):
                    _red("TAR_SIZE_INVALID")
                name = item.name[2:] if item.name.startswith("./") else item.name
                if (not _safe_path(name) or not item.isfile() or
                        name not in TAR_FILES or name in found or item.linkname):
                    _red("TAR_MEMBER_INVALID")
                if item.uid != 1000 or item.gid != 1000 or item.mode & 0o7133:
                    _red("TAR_OWNER_MODE_INVALID")
                total += item.size
                if total > MAX_TAR_PAYLOAD:
                    _red("TAR_PAYLOAD_LIMIT")
                stream = archive.extractfile(item)
                if stream is None:
                    _red("TAR_CONTENT_INVALID")
                data = stream.read(item.size + 1)
                if len(data) != item.size:
                    _red("TAR_CONTENT_INVALID")
                _public_native_schema(name, data)
                found[name] = {"path": name, "size_bytes": len(data),
                               "sha256": hashlib.sha256(data).hexdigest()}
                contents[name] = data
    except (tarfile.TarError, EOFError, OSError, OverflowError, ValueError) as exc:
        if isinstance(exc, PostrunEvidenceRed):
            raise
        _red("TAR_INVALID")
    if len(raw) - cursor < 1024 or any(raw[cursor:]):
        _red("TAR_TRAILING_DATA")
    if set(found) != TAR_FILES:
        _red("TAR_SET_INVALID")
    # The unprivileged collector's pack_collector_tar emits deterministic USTAR,
    # sorted exact paths, uid/gid 1000, mode 0600, mtime zero. Recreate from
    # newly initialized headers: never copy untrusted TAR metadata. Exact bytes
    # prohibit covert Env/Source in per-file padding, uname/gname, prefixes,
    # optional records or unclaimed trailing blocks.
    canonical = io.BytesIO()
    with tarfile.open(fileobj=canonical, mode="w",
                      format=tarfile.USTAR_FORMAT) as writer:
        for name in sorted(TAR_FILES):
            data = contents[name]
            info = tarfile.TarInfo("./" + name)
            info.uid = info.gid = 1000
            info.mode = 0o600
            info.mtime = 0
            info.size = len(data)
            writer.addfile(info, io.BytesIO(data))
    if canonical.getvalue() != raw:
        _red("TAR_CANONICAL_BYTES_MISMATCH")
    return [found[name] for name in sorted(found)]


def _anonymous_volume_declaration(declared: Any) -> bool:
    """Public V2 declaration: never serialize the private Docker Source key."""
    if type(declared) is not list or len(declared) != 1 or type(declared[0]) is not dict:
        return False
    item = declared[0]
    if (not set(item).issubset({"Type", "Target", "ReadOnly", "Consistency",
                                "BindOptions", "VolumeOptions", "TmpfsOptions"}) or
            item.get("Type") != "volume" or
            item.get("Target") != "/app/data" or
            ("ReadOnly" in item and item["ReadOnly"] is not False) or
            item.get("Consistency") not in (None, "", "default", "consistent") or
            item.get("BindOptions") is not None or
            item.get("TmpfsOptions") is not None):
        return False
    options = item.get("VolumeOptions")
    if options is None:
        return True
    return (type(options) is dict and
            set(options).issubset({"NoCopy", "Labels", "DriverConfig", "Subpath"}) and
            ("NoCopy" not in options or options["NoCopy"] is False) and
            options.get("Labels") in (None, {}) and
            options.get("DriverConfig") in (None, {}) and
            options.get("Subpath") in (None, ""))


def _validate_public_inspect(document: Any, *, cid: str, source_cid: str,
                             image_id: str, volume: str, sha: str, tree: str,
                             run_id: int, attempt: int, collector: bool) -> None:
    """Check only allowlisted public fields; NEVER assert private raw replay.

    A producer-controlled digest is a commitment to unavailable raw bytes.
    It does not prove that a private Docker daemon emitted those bytes.
    """
    if (type(document) is not dict or set(document) != PUBLIC_INSPECT_KEYS or
            document.get("schema") != PUBLIC_INSPECT_SCHEMA or
            type(document.get("schema_version")) is not int or
            document["schema_version"] != 2 or
            document.get("role") != ("bundle-collector" if collector else "bundle") or
            document.get("status") != "OFFHOST_PROJECTED_EVIDENCE_ONLY" or
            document.get("host_or_runtime_verified") is not False):
        _red("INSPECT_PROJECTION_SCHEMA_INVALID")
    raw = document["raw_inspect"]
    if (type(raw) is not dict or set(raw) != {"sha256", "size_bytes", "published"} or
            not _hex64(raw.get("sha256")) or
            type(raw.get("size_bytes")) is not int or
            not 0 < raw["size_bytes"] <= MAX_PUBLIC_INSPECT_BYTES or
            raw.get("published") is not False):
        _red("INSPECT_PRIVATE_DIGEST_UNVERIFIABLE")
    redaction = document["source_redaction"]
    if (type(redaction) is not dict or
            set(redaction) != {"hostconfig_declaration", "runtime_source_nonempty_observed"} or
            redaction.get("runtime_source_nonempty_observed") is not True or
            (redaction.get("hostconfig_declaration") != "NOT_APPLICABLE" if collector
             else redaction.get("hostconfig_declaration") not in ("ABSENT", "EMPTY"))):
        _red("INSPECT_SOURCE_REDACTION_INVALID")
    entry = document["record"]
    if type(entry) is not dict or set(entry) != PUBLIC_RECORD_KEYS:
        _red("INSPECT_PROJECTION_SCHEMA_INVALID")
    if entry.get("Id") != cid or entry.get("Image") != image_id:
        _red("INSPECT_IDENTITY_MISMATCH")
    config, host = entry["Config"], entry["HostConfig"]
    if (type(config) is not dict or set(config) != {"User", "Labels"} or
            type(host) is not dict or not set(host).issubset(PUBLIC_HOST_KEYS)):
        _red("INSPECT_PROJECTION_SCHEMA_INVALID")
    labels = {
        "porota.commit": sha, "porota.candidate_tree": tree,
        "porota.predeploy": "v2", "porota.native-proof": "v3",
        "porota.native-role": "bundle-collector" if collector else "bundle",
        "porota.run_id": str(run_id), "porota.run_attempt": str(attempt),
    }
    if config["User"] != "1000:1000" or config["Labels"] != labels:
        _red("INSPECT_CONFIG_UNSAFE")
    if (host.get("Privileged") is not False or
            host.get("ReadonlyRootfs") is not True or
            host.get("NetworkMode") != "none" or
            host.get("CapDrop") != ["ALL"] or
            host.get("CapAdd") not in (None, []) or
            host.get("Devices") not in (None, []) or
            host.get("Tmpfs") not in (None, {}) or
            host.get("PublishAllPorts") not in (None, False) or
            type(host.get("PublishAllPorts")) is int or
            host.get("PortBindings") not in (None, {}) or
            host.get("Links") not in (None, []) or
            host.get("ExtraHosts") not in (None, []) or
            host.get("SecurityOpt") != ["no-new-privileges"] or
            host.get("Binds") not in (None, [])):
        _red("INSPECT_CONFIG_UNSAFE")
    neutral = {
        "IpcMode": (None, "", "private"), "UTSMode": (None, "", "private"),
        "PidMode": (None, "", "private"), "UsernsMode": (None, ""),
        "CgroupnsMode": (None, "", "private"), "GroupAdd": (None, []),
        "DeviceRequests": (None, []), "DeviceCgroupRules": (None, []),
        "Runtime": (None, "", "runc"), "Sysctls": (None, {}),
        "CgroupParent": (None, ""),
    }
    if any(host.get(key) not in allowed for key, allowed in neutral.items()):
        _red("INSPECT_CONFIG_UNSAFE")
    if collector:
        if (host.get("VolumesFrom") != [source_cid + ":ro"] or
                host.get("Mounts") not in (None, [])):
            _red("INSPECT_CONFIG_UNSAFE")
    elif (host.get("VolumesFrom") not in (None, []) or
          not _anonymous_volume_declaration(host.get("Mounts"))):
        _red("INSPECT_CONFIG_UNSAFE")
    mounts = entry["Mounts"]
    if type(mounts) is not list or len(mounts) != 1 or type(mounts[0]) is not dict:
        _red("INSPECT_VOLUME_MISMATCH")
    mount = mounts[0]
    if set(mount) != PUBLIC_MOUNT_KEYS:
        _red("INSPECT_PROJECTION_SCHEMA_INVALID")
    if (mount.get("Type") != "volume" or mount.get("Name") != volume or
            mount.get("Driver") != "local" or
            mount.get("Destination") != "/app/data" or
            mount.get("Mode") != ("" if collector else "z") or
            mount.get("RW") is not (not collector)):
        _red("INSPECT_VOLUME_MISMATCH")


def _native_evidence(payload: Mapping[str, bytes], *, sha: str,
                     tree: str, run_id: int, attempt: int) -> int:
    native = load_unique_json(payload["native-manifest.json"])
    custody = load_unique_json(payload["custody.json"])
    if (type(native) is not dict or set(native) != NATIVE_KEYS or
            native.get("schema") != "RC6_R163_NATIVE_TAR_V1" or
            type(native.get("schema_version")) is not int or
            native["schema_version"] != 1 or
            native.get("status") != "OFFHOST_BYTES_ONLY" or
            native.get("host_or_runtime_verified") is not False):
        _red("NATIVE_MANIFEST_SCHEMA_INVALID")
    if (type(custody) is not dict or set(custody) != CUSTODY_KEYS or
            custody.get("status") != "OFFHOST_CONTRACT_ONLY" or
            custody.get("host_or_runtime_verified") is not False or
            type(custody.get("schema_version")) is not int or
            custody["schema_version"] != 1 or
            type(custody.get("uid")) is not int or custody["uid"] != 1000 or
            type(custody.get("gid")) is not int or custody["gid"] != 1000 or
            custody.get("mount_target") != "/app/data"):
        _red("CUSTODY_SCHEMA_INVALID")
    identity = {"candidate_sha": sha, "candidate_tree_sha": tree,
                "run_id": str(run_id), "run_attempt": str(attempt),
                "role": "bundle"}
    for key, value in identity.items():
        if native.get(key) != value or custody.get(key) != value:
            _red("NATIVE_IDENTITY_MISMATCH")
    for key, regex in (("source_cid", CID64), ("collector_cid", CID64),
                       ("volume_name", CID64), ("image_id", re.compile(r"sha256:[0-9a-f]{64}\Z"))):
        value = native.get(key)
        if (type(value) is not str or regex.fullmatch(value) is None or
                custody.get(key) != value):
            _red("NATIVE_IDENTITY_MISMATCH")
    if native["source_cid"] == native["collector_cid"]:
        _red("NATIVE_IDENTITY_MISMATCH")
    for filename, cid, writable in (("source-inspect.json", native["source_cid"], True),
                                     ("collector-inspect.json", native["collector_cid"], False)):
        if len(payload[filename]) > MAX_PUBLIC_INSPECT_BYTES:
            _red("INSPECT_PROJECTION_SIZE_INVALID")
        records = load_unique_json(payload[filename])
        _validate_public_inspect(
            records, cid=cid, source_cid=native["source_cid"],
            image_id=native["image_id"], volume=native["volume_name"],
            sha=sha, tree=tree, run_id=run_id, attempt=attempt,
            collector=not writable,
        )
    tar_bytes = payload["native.tar"]
    if (not _hex64(native.get("tar_sha256")) or
            native["tar_sha256"] != hashlib.sha256(tar_bytes).hexdigest() or
            type(native.get("tar_size_bytes")) is not int or
            native["tar_size_bytes"] != len(tar_bytes)):
        _red("NATIVE_TAR_DIGEST_MISMATCH")
    files = _native_tar_files(tar_bytes)
    claimed_files = native.get("files")
    if (type(claimed_files) is not list or len(claimed_files) != len(files) or
            any(type(row) is not dict or
                set(row) != {"path", "size_bytes", "sha256"} or
                type(row["path"]) is not str or
                type(row["size_bytes"]) is not int or
                not 0 < row["size_bytes"] <= MAX_TAR_FILE or
                not _hex64(row["sha256"])
                for row in claimed_files)):
        _red("NATIVE_FILE_LIST_MISMATCH")
    if (type(native.get("file_count")) is not int or
            native["file_count"] != len(files) or
            claimed_files != files):
        _red("NATIVE_FILE_LIST_MISMATCH")
    return len(files)


def verify_postrun_bytes(*, event: Mapping[str, Any], run: Mapping[str, Any],
                         attempt: Mapping[str, Any],
                         pr: Mapping[str, Any], commit: Mapping[str, Any],
                         artifact_list: Mapping[str, Any], artifact: Mapping[str, Any],
                         zip_bytes: bytes, expected_run_id: int,
                         expected_run_attempt: int) -> dict[str, Any]:
    """Authenticate caller-supplied REST receipts and exact bytes; no GO."""
    sha, tree, name = _identity(event, run, pr, commit,
                                expected_run_id, expected_run_attempt)
    window = _attempt_window(attempt, sha=sha, run_id=expected_run_id,
                             attempt_number=expected_run_attempt)
    artifact_id, digest, size = _artifact_identity(
        artifact_list, artifact, name=name, sha=sha, run_id=expected_run_id,
        attempt_window=window)
    payload = _zip_evidence(zip_bytes, sha=sha, tree=tree, name=name,
                            run_id=expected_run_id, attempt=expected_run_attempt,
                            digest=digest, expected_size=size)
    count = _native_evidence(payload, sha=sha, tree=tree,
                             run_id=expected_run_id, attempt=expected_run_attempt)
    return {
        "schema_version": 1, "status": "POSTRUN_ARTIFACT_BYTES_MATCH",
        "candidate_sha": sha, "candidate_tree_sha": tree,
        "run_id": expected_run_id, "run_attempt": expected_run_attempt,
        "artifact_id": artifact_id, "artifact_name": name,
        "artifact_digest": "sha256:" + digest, "native_file_count": count,
        "artifact_contents_verified_by_this_guard": True,
        "trusted_main_review_and_merge_verified_by_script": False,
        "native_host_custody_verified": False,
        "raw_inspect_contents_verified_by_this_guard": False,
        "automatic_go": False,
        "release_go": False, "runtime_verified": False,
    }


def allowed_storage_redirect(url: Any) -> bool:
    """Only GitHub Actions' HTTPS artifact storage, never token-bearing URLs."""
    if type(url) is not str or len(url) > 8192 or "\r" in url or "\n" in url:
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or not host or parsed.username is not None or
                parsed.password is not None or parsed.port not in (None, 443) or
                parsed.fragment):
            return False
        try:
            ipaddress.ip_address(host)
            return False
        except ValueError:
            pass
        return (host == "objects.githubusercontent.com" or
                host.endswith(".actions.githubusercontent.com") or
                host.endswith(".blob.core.windows.net") or
                host.endswith(".s3.amazonaws.com"))
    except ValueError:
        return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubReadOnlyREST:
    """Read authenticated metadata and one raw ZIP; never forward token off API."""

    def __init__(self, token: str):
        if type(token) is not str or not token or "\n" in token or "\r" in token:
            _red("GITHUB_TOKEN_MISSING")
        self._token = token
        self._opener = urllib.request.build_opener(_NoRedirect())

    @staticmethod
    def _read(response: Any, *, limit: int) -> bytes:
        if response.status != 200:
            _red("GITHUB_REST_HTTP_RED")
        data = response.read(limit + 1)
        if not data or len(data) > limit:
            _red("GITHUB_REST_SIZE_RED")
        return data

    def _api_request(self, path: str) -> urllib.request.Request:
        if (type(path) is not str or not path.startswith("/repos/" + REPOSITORY + "/") or
                ".." in path or "#" in path):
            _red("GITHUB_REST_PATH_RED")
        return urllib.request.Request(
            "https://api.github.com" + path,
            headers={"Authorization": "Bearer " + self._token,
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28",
                     "User-Agent": "rc6-r163-trusted-main-readonly"},
        )

    def get_json(self, path: str) -> Mapping[str, Any]:
        try:
            with self._opener.open(self._api_request(path), timeout=30) as response:
                raw = self._read(response, limit=MAX_JSON_BYTES)
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise PostrunEvidenceRed("GITHUB_REST_HTTP_RED") from exc
        result = load_unique_json(raw)
        if type(result) is not dict:
            _red("GITHUB_REST_JSON_RED")
        return result

    def get_zip(self, artifact_id: int) -> bytes:
        if not _positive(artifact_id):
            _red("EXPECTED_ARTIFACT_INVALID")
        endpoint = f"/repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip"
        try:
            with self._opener.open(self._api_request(endpoint), timeout=30) as response:
                # The GitHub REST contract normally returns a 302 to a
                # temporary authenticated storage URL; direct 200 is safe too.
                return self._read(response, limit=MAX_ZIP_BYTES)
        except urllib.error.HTTPError as redirect:
            if redirect.code != 302:
                _red("GITHUB_ZIP_HTTP_RED")
            location = redirect.headers.get("Location")
            if not allowed_storage_redirect(location):
                _red("GITHUB_ZIP_REDIRECT_UNSAFE")
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise PostrunEvidenceRed("GITHUB_ZIP_HTTP_RED") from exc
        # A REST-issued signed URL authorizes bytes, but never receives the
        # bearer token. All other redirects are blocked by _NoRedirect.
        try:
            request = urllib.request.Request(
                location, headers={"User-Agent": "rc6-r163-trusted-main-readonly"})
            with self._opener.open(request, timeout=60) as response:
                return self._read(response, limit=MAX_ZIP_BYTES)
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise PostrunEvidenceRed("GITHUB_ZIP_HTTP_RED") from exc


def audit_from_github(client: Any, *, event: Mapping[str, Any],
                      expected_run_id: int,
                      expected_run_attempt: int) -> dict[str, Any]:
    """REST-only collector; independently recheck receipts after ZIP download."""
    if not _positive(expected_run_id) or not _positive(expected_run_attempt):
        _red("EXPECTED_RUN_INVALID")
    prefix = f"/repos/{REPOSITORY}"
    run_path = f"{prefix}/actions/runs/{expected_run_id}"
    attempt_path = f"{run_path}/attempts/{expected_run_attempt}"
    pr_path = f"{prefix}/pulls/{PR_NUMBER}"
    list_path = f"{run_path}/artifacts?per_page=100"
    run = client.get_json(run_path)
    attempt = client.get_json(attempt_path)
    pr = client.get_json(pr_path)
    sha = run.get("head_sha")
    if not _sha(sha):
        _red("RUN_SHA_MISMATCH")
    commit = client.get_json(f"{prefix}/git/commits/{sha}")
    sha_checked, tree, name = _identity(event, run, pr, commit,
                                        expected_run_id, expected_run_attempt)
    window = _attempt_window(attempt, sha=sha_checked,
                             run_id=expected_run_id,
                             attempt_number=expected_run_attempt)
    artifact_list = client.get_json(list_path)
    artifact_id = _select_attempt_artifact(artifact_list, name)["id"]
    artifact_path = f"{prefix}/actions/artifacts/{artifact_id}"
    artifact = client.get_json(artifact_path)
    # Validate all metadata BEFORE accepting the potentially large download.
    _artifact_identity(artifact_list, artifact, name=name,
                       sha=sha_checked, run_id=expected_run_id,
                       attempt_window=window)
    raw = client.get_zip(artifact_id)
    verified = verify_postrun_bytes(
        event=event, run=run, attempt=attempt, pr=pr, commit=commit,
        artifact_list=artifact_list, artifact=artifact, zip_bytes=raw,
        expected_run_id=expected_run_id, expected_run_attempt=expected_run_attempt,
    )
    # A new PR push, rerun attempt, artifact replacement or deleted run during
    # the ZIP fetch invalidates the result. No stale-state or name fallback.
    run_after = client.get_json(run_path)
    attempt_after = client.get_json(attempt_path)
    pr_after = client.get_json(pr_path)
    list_after = client.get_json(list_path)
    artifact_after = client.get_json(artifact_path)
    immutable_attempt_keys = (
        "id", "run_attempt", "head_sha", "head_branch", "path", "name",
        "event", "status", "conclusion", "run_started_at", "created_at",
        "updated_at",
    )
    if any(attempt_after.get(key) != attempt.get(key)
           for key in immutable_attempt_keys):
        _red("REST_RACE_RED")
    try:
        reverified = verify_postrun_bytes(
            event=event, run=run_after, attempt=attempt_after,
            pr=pr_after, commit=commit,
            artifact_list=list_after, artifact=artifact_after, zip_bytes=raw,
            expected_run_id=expected_run_id,
            expected_run_attempt=expected_run_attempt,
        )
        before_row = _select_attempt_artifact(artifact_list, name)
        after_row = _select_attempt_artifact(list_after, name)
        for key in ("created_at", "updated_at"):
            if (artifact_after.get(key) != artifact.get(key) or
                    after_row.get(key) != before_row.get(key)):
                _red("REST_RACE_RED")
    except PostrunEvidenceRed:
        _red("REST_RACE_RED")
    if reverified != verified:
        _red("REST_RACE_RED")
    return verified


def verify_upstream_preupload_from_github(client: Any, *,
                                          event: Mapping[str, Any],
                                          expected_run_id: int,
                                          expected_run_attempt: int) -> dict[str, Any]:
    """Authenticate a currently failed-by-HOLD upstream before fresh B probe.

    This source receipt is not native proof: R205 deliberately caused exit79,
    and GitHub REST cannot attest why a run failed from run metadata alone.
    Trusted B must independently reproduce/validate all native tests before
    any public projection is sealed and uploaded. No U/M artifact is allowed.
    """
    prefix = f"/repos/{REPOSITORY}"
    run_path = f"{prefix}/actions/runs/{expected_run_id}"
    attempt_path = f"{run_path}/attempts/{expected_run_attempt}"
    pr_path = f"{prefix}/pulls/{PR_NUMBER}"
    list_path = f"{run_path}/artifacts?per_page=100"
    run = client.get_json(run_path)
    attempt = client.get_json(attempt_path)
    pr = client.get_json(pr_path)
    sha = run.get("head_sha") if type(run) is dict else None
    if not _sha(sha):
        _red("RUN_SHA_MISMATCH")
    commit = client.get_json(f"{prefix}/git/commits/{sha}")
    candidate_sha, candidate_tree, _ = _identity(
        event, run, pr, commit, expected_run_id, expected_run_attempt,
        expected_conclusion="failure")
    _attempt_window(attempt, sha=candidate_sha, run_id=expected_run_id,
                    attempt_number=expected_run_attempt,
                    expected_conclusion="failure")
    artifacts = client.get_json(list_path)
    if (type(artifacts) is not dict or
            artifacts.get("total_count") != 0 or
            artifacts.get("artifacts") != []):
        _red("PREUPLOAD_UPSTREAM_ARTIFACT_PRESENT")
    run_after = client.get_json(run_path)
    attempt_after = client.get_json(attempt_path)
    pr_after = client.get_json(pr_path)
    art_after = client.get_json(list_path)
    if (run_after != run or attempt_after != attempt or
            pr_after != pr or art_after != artifacts):
        _red("PREUPLOAD_UPSTREAM_REST_RACE")
    return {
        "status": "UPSTREAM_IDENTITY_ONLY_REPRODUCE_REQUIRED",
        "candidate_sha": candidate_sha,
        "candidate_tree_sha": candidate_tree,
        "upstream_run_id": expected_run_id,
        "upstream_attempt": expected_run_attempt,
        "upstream_ci_green_verified": False,
        "upstream_artifact_count": 0,
        "release_go": False,
        "runtime_verified": False,
    }


def audit_publisher_from_github(client: Any, *, event: Mapping[str, Any],
                                expected_publisher_run_id: int,
                                expected_publisher_attempt: int,
                                trusted_main_sha: str) -> dict[str, Any]:
    """C: authenticate B/P/N success and exact public ZIP, never self-attest B.

    The failed upstream U/M has no artifact. B/P/N independently reproduced
    a fresh OFFHOST probe, and its artifact has the U/M source identity in its
    fixed manifest. GitHub REST authenticates the run/attempt/bytes, but does
    NOT expose a separately signed B-to-U workflow_run webhook edge. That
    stronger event linkage, host/runtime custody, and release stay unverified.
    """
    if (not _positive(expected_publisher_run_id) or
            not _positive(expected_publisher_attempt) or
            not _sha(trusted_main_sha)):
        _red("POSTCOMPLETION_EXPECTED_IDENTITY_INVALID")
    if (type(event) is not dict or event.get("action") != "completed" or
            type(event.get("repository")) is not dict or
            event["repository"].get("full_name") != REPOSITORY or
            type(event.get("workflow_run")) is not dict):
        _red("POSTCOMPLETION_EVENT_MISMATCH")
    triggered = event["workflow_run"]
    if (triggered.get("id") != expected_publisher_run_id or
            triggered.get("run_attempt") != expected_publisher_attempt or
            triggered.get("event") != "workflow_run" or
            triggered.get("status") != "completed" or
            triggered.get("conclusion") != "success" or
            triggered.get("name") != TRUSTED_PUBLISHER_NAME or
            triggered.get("head_sha") != trusted_main_sha or
            not _workflow_path_exact(
                triggered.get("path"), TRUSTED_PUBLISHER_PATH,
                branch="main", sha=trusted_main_sha)):
        _red("POSTCOMPLETION_EVENT_MISMATCH")

    prefix = f"/repos/{REPOSITORY}"
    publisher_path = f"{prefix}/actions/runs/{expected_publisher_run_id}"
    publisher_attempt_path = f"{publisher_path}/attempts/{expected_publisher_attempt}"
    publisher_artifacts_path = f"{publisher_path}/artifacts?per_page=100"
    publisher = client.get_json(publisher_path)
    publisher_attempt = client.get_json(publisher_attempt_path)
    if publisher.get("head_sha") != trusted_main_sha:
        _red("POSTCOMPLETION_MAIN_SHA_MISMATCH")
    artifacts = client.get_json(publisher_artifacts_path)
    if (type(artifacts) is not dict or artifacts.get("total_count") != 1 or
            type(artifacts.get("artifacts")) is not list or
            len(artifacts["artifacts"]) != 1 or
            type(artifacts["artifacts"][0]) is not dict):
        _red("POSTCOMPLETION_ARTIFACT_AMBIGUOUS")
    name = artifacts["artifacts"][0].get("name")
    match = (re.fullmatch(
        r"rc6-r163-offhost-([1-9][0-9]*)-([1-9][0-9]*)-([0-9a-f]{40})",
        name) if type(name) is str else None)
    if match is None:
        _red("POSTCOMPLETION_ARTIFACT_AMBIGUOUS")
    upstream_id, upstream_number, expected_candidate_sha = (
        int(match.group(1)), int(match.group(2)), match.group(3))
    if upstream_id == expected_publisher_run_id:
        _red("PUBLISHER_UPSTREAM_ALIAS")
    upstream_path = f"{prefix}/actions/runs/{upstream_id}"
    upstream_attempt_path = f"{upstream_path}/attempts/{upstream_number}"
    upstream_artifacts_path = f"{upstream_path}/artifacts?per_page=100"
    pr_path = f"{prefix}/pulls/{PR_NUMBER}"
    upstream = client.get_json(upstream_path)
    upstream_attempt = client.get_json(upstream_attempt_path)
    upstream_artifacts = client.get_json(upstream_artifacts_path)
    pr = client.get_json(pr_path)
    if upstream.get("head_sha") != expected_candidate_sha:
        _red("POSTCOMPLETION_SOURCE_SHA_MISMATCH")
    commit = client.get_json(f"{prefix}/git/commits/{expected_candidate_sha}")
    # The upstream webhook was delivered to B, not C. A REST-derived event
    # asserts the same GitHub run identity but is NOT a webhook-edge proof.
    upstream_rest_event = {
        "action": "completed", "repository": {"full_name": REPOSITORY},
        "workflow_run": upstream,
    }
    identity = verify_trusted_publisher_identity(
        upstream_event=upstream_rest_event, upstream_run=upstream,
        upstream_attempt=upstream_attempt, pr=pr, commit=commit,
        publisher_run=publisher, publisher_attempt=publisher_attempt,
        expected_upstream_run_id=upstream_id,
        expected_upstream_attempt=upstream_number,
        expected_publisher_run_id=expected_publisher_run_id,
        expected_publisher_attempt=expected_publisher_attempt)
    if (type(upstream_artifacts) is not dict or
            upstream_artifacts.get("total_count") != 0 or
            upstream_artifacts.get("artifacts") != []):
        _red("PREUPLOAD_UPSTREAM_ARTIFACT_PRESENT")
    # Do not silently allow a publisher on a retired or changed main commit.
    if identity["publisher_main_sha"] != trusted_main_sha:
        _red("POSTCOMPLETION_MAIN_SHA_MISMATCH")
    publisher_window = _attempt_window(
        publisher_attempt, sha=trusted_main_sha,
        run_id=expected_publisher_run_id,
        attempt_number=expected_publisher_attempt,
        expected_branch="main", expected_path=TRUSTED_PUBLISHER_PATH,
        expected_name=TRUSTED_PUBLISHER_NAME,
        expected_event="workflow_run")
    if (publisher_window[0] <=
            _utc_timestamp(upstream_attempt.get("updated_at"),
                           "ATTEMPT_TIMESTAMP_INVALID")):
        _red("POSTCOMPLETION_SOURCE_ORDER_INVALID")
    artifact_id = _select_attempt_artifact(artifacts, name)["id"]
    artifact_path = f"{prefix}/actions/artifacts/{artifact_id}"
    artifact = client.get_json(artifact_path)
    artifact_id, digest, size = _artifact_identity(
        artifacts, artifact, name=name, sha=trusted_main_sha,
        run_id=expected_publisher_run_id, attempt_window=publisher_window)
    zip_bytes = client.get_zip(artifact_id)
    payload = _zip_evidence(
        zip_bytes, sha=expected_candidate_sha,
        tree=identity["candidate_tree_sha"], name=name,
        run_id=upstream_id, attempt=upstream_number,
        digest=digest, expected_size=size)
    native_count = _native_evidence(
        payload, sha=expected_candidate_sha,
        tree=identity["candidate_tree_sha"],
        run_id=upstream_id, attempt=upstream_number)
    # Every remote receipt can change during a ZIP download. Reject stale
    # PR heads, rerun attempts, artifact substitutions and mutable main.
    endpoints = {
        publisher_path: publisher,
        publisher_attempt_path: publisher_attempt,
        publisher_artifacts_path: artifacts,
        upstream_path: upstream,
        upstream_attempt_path: upstream_attempt,
        upstream_artifacts_path: upstream_artifacts,
        pr_path: pr,
        f"{prefix}/git/commits/{expected_candidate_sha}": commit,
        artifact_path: artifact,
    }
    if any(client.get_json(path) != receipt for path, receipt in endpoints.items()):
        _red("POSTCOMPLETION_REST_RACE")
    if (event["workflow_run"].get("head_sha") != trusted_main_sha or
            identity["upstream_ci_green_verified"] is not False or
            identity["publisher_to_upstream_event_link_verified"] is not False):
        _red("POSTCOMPLETION_IDENTITY_MISMATCH")
    return {
        "status": "POSTCOMPLETION_PUBLIC_PROBE_BYTES_MATCH",
        "candidate_sha": expected_candidate_sha,
        "candidate_tree_sha": identity["candidate_tree_sha"],
        "upstream_run_id": upstream_id, "upstream_attempt": upstream_number,
        "publisher_main_sha": trusted_main_sha,
        "publisher_run_id": expected_publisher_run_id,
        "publisher_attempt": expected_publisher_attempt,
        "artifact_id": artifact_id, "artifact_name": name,
        "artifact_digest": "sha256:" + digest,
        "native_file_count": native_count,
        "public_artifact_contents_verified": True,
        "upstream_ci_green_verified": False,
        "publisher_to_upstream_event_link_verified": False,
        "raw_inspect_contents_verified": False,
        "release_go": False, "runtime_verified": False,
    }


def _read_event_file(path: Path) -> Mapping[str, Any]:
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_JSON_BYTES:
                _red("EVENT_FILE_RED")
            data = stream.read(MAX_JSON_BYTES + 1)
            after = os.fstat(stream.fileno())
            named = path.lstat()
            if (len(data) != before.st_size or
                    (before.st_dev, before.st_ino, before.st_size,
                     before.st_mtime_ns, before.st_ctime_ns) !=
                    (after.st_dev, after.st_ino, after.st_size,
                     after.st_mtime_ns, after.st_ctime_ns) or
                    not stat.S_ISREG(named.st_mode) or
                    (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)):
                _red("EVENT_FILE_RED")
    except OSError:
        _red("EVENT_FILE_RED")
    event = load_unique_json(data)
    return _require_dict(event, "EVENT_FILE_RED")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("r206-upstream", "r206-seal", "r206-postcompletion"):
        mode = argv[0]
        parser = argparse.ArgumentParser(description="R206 trusted-main probe verification")
        parser.add_argument("--run-id", type=int, required=True)
        parser.add_argument("--run-attempt", type=int, required=True)
        if mode in ("r206-upstream", "r206-postcompletion"):
            parser.add_argument("--event", type=Path, required=True)
            if mode == "r206-postcompletion":
                parser.add_argument("--trusted-main-sha", required=True)
        else:
            parser.add_argument("--staging-root", type=Path, required=True)
            parser.add_argument("--input-root", type=Path, required=True)
            parser.add_argument("--candidate-sha", required=True)
            parser.add_argument("--candidate-tree-sha", required=True)
            parser.add_argument("--image-id", required=True)
            parser.add_argument("--source-cid", required=True)
            parser.add_argument("--collector-cid", required=True)
            parser.add_argument("--volume-name", required=True)
            parser.add_argument("--candidate-writer-uid", type=int, default=1000)
        args = parser.parse_args(argv[1:])
        try:
            if mode == "r206-upstream":
                client = GitHubReadOnlyREST(os.environ.get("GITHUB_TOKEN", ""))
                result = verify_upstream_preupload_from_github(
                    client, event=_read_event_file(args.event),
                    expected_run_id=args.run_id,
                    expected_run_attempt=args.run_attempt)
                print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            elif mode == "r206-postcompletion":
                client = GitHubReadOnlyREST(os.environ.get("GITHUB_TOKEN", ""))
                result = audit_publisher_from_github(
                    client, event=_read_event_file(args.event),
                    expected_publisher_run_id=args.run_id,
                    expected_publisher_attempt=args.run_attempt,
                    trusted_main_sha=args.trusted_main_sha)
                print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            else:
                seal_trusted_preupload_directory(
                    publisher_staging_root=args.staging_root,
                    input_root=args.input_root,
                    output_name="public",
                    candidate_sha=args.candidate_sha,
                    candidate_tree_sha=args.candidate_tree_sha,
                    run_id=args.run_id, run_attempt=args.run_attempt,
                    image_id=args.image_id, source_cid=args.source_cid,
                    collector_cid=args.collector_cid, volume_name=args.volume_name,
                    publisher_uid=os.getuid(),
                    candidate_writer_uid=args.candidate_writer_uid,
                    candidate_can_access_staging=False)
                # P/N is still in_progress. Only C can attest its REST bytes.
                print("RC6_R206_TRUSTED_PREUPLOAD=SEALED_PUBLIC_SIX|"
                      "PROBE_PUBLISHED_PENDING_POSTRUN|release_go=NO")
        except PostrunEvidenceRed as exc:
            print(f"RC6_R206_TRUSTED=HOLD79|reason={exc}|release_go=NO",
                  file=sys.stderr)
            return 79
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        client = GitHubReadOnlyREST(os.environ.get("GITHUB_TOKEN", ""))
        result = audit_from_github(
            client, event=_read_event_file(args.event),
            expected_run_id=args.run_id, expected_run_attempt=args.run_attempt,
        )
    except PostrunEvidenceRed as exc:
        print(f"RC6_R163_TRUSTED_POSTRUN=RED|reason={exc}|release_go=NO", file=sys.stderr)
        return 79
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
