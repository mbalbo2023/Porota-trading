#!/usr/bin/env python3
"""R163 proposal: read-only, default-main POSTRUN artifact byte authenticator.

This source is NOT a trusted bootstrap until independently reviewed, merged via
an authorized main-targeted PR, and pinned by the corresponding workflow. It
never executes PR code, extracts archives, builds images, sends orders or
changes any GitHub/host state. Bytes and metadata come from authenticated
GitHub REST; actual host/runtime custody remains NO_VERIFICADO.

The same-run R163 proof is untrusted REVIEW_ONLY. This verifier requires the
entire upstream run to have completed successfully, the source PR #1087 to
remain at that exact SHA, and the original artifact ZIP byte digest to match
GitHub's REST artifact digest. No R144/HOLD79/release gate is lifted.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import stat
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
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
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


def _identity(event: Mapping[str, Any], run: Mapping[str, Any],
              pr: Mapping[str, Any], commit: Mapping[str, Any],
              expected_run_id: int, expected_run_attempt: int) -> tuple[str, str, str]:
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
            event_run.get("conclusion") != "success" or
            event_run.get("event") != "pull_request" or
            event_run.get("name") != UPSTREAM_NAME):
        _red("EVENT_UPSTREAM_NOT_SUCCESS")
    if not isinstance(run, dict) or run.get("id") != expected_run_id or not _positive(run.get("id")):
        _red("RUN_ID_MISMATCH")
    if (run.get("run_attempt") != expected_run_attempt or
            not _positive(run.get("run_attempt"))):
        _red("RUN_ATTEMPT_MISMATCH")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        _red("RUN_NOT_SUCCESS")
    if run.get("event") != "pull_request":
        _red("RUN_EVENT_MISMATCH")
    if run.get("path") != UPSTREAM_PATH or run.get("name") != UPSTREAM_NAME:
        _red("RUN_WORKFLOW_MISMATCH")
    if (not isinstance(run.get("repository"), dict) or
            run["repository"].get("full_name") != REPOSITORY or
            not isinstance(run.get("head_repository"), dict) or
            run["head_repository"].get("full_name") != REPOSITORY):
        _red("RUN_REPO_MISMATCH")
    sha = run.get("head_sha")
    if not _sha(sha) or event_run.get("head_sha") != sha:
        _red("RUN_SHA_MISMATCH")
    if event_run.get("path") != UPSTREAM_PATH:
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
                       *, name: str, sha: str, run_id: int) -> tuple[int, str, int]:
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
    return artifact["id"], digest[7:], artifact["size_in_bytes"]


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
            ValueError, zlib.error) as exc:
        if isinstance(exc, PostrunEvidenceRed):
            raise
        _red("ZIP_INVALID")


def _native_tar_files(raw: bytes) -> list[dict[str, Any]]:
    if (type(raw) is not bytes or not 1024 <= len(raw) <= MAX_TAR_BYTES or
            len(raw) % 512 or raw[-1024:] != bytes(1024)):
        _red("TAR_FORMAT_INVALID")
    found: dict[str, dict[str, Any]] = {}
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
                found[name] = {"path": name, "size_bytes": len(data),
                               "sha256": hashlib.sha256(data).hexdigest()}
    except (tarfile.TarError, EOFError, OSError, OverflowError, ValueError) as exc:
        if isinstance(exc, PostrunEvidenceRed):
            raise
        _red("TAR_INVALID")
    if len(raw) - cursor < 1024 or any(raw[cursor:]):
        _red("TAR_TRAILING_DATA")
    if set(found) != TAR_FILES:
        _red("TAR_SET_INVALID")
    return [found[name] for name in sorted(found)]


def _anonymous_volume_declaration(declared: Any) -> bool:
    if type(declared) is not list or len(declared) != 1 or type(declared[0]) is not dict:
        return False
    item = declared[0]
    if (not set(item).issubset({"Type", "Source", "Target", "ReadOnly", "Consistency",
                                "BindOptions", "VolumeOptions", "TmpfsOptions"}) or
            item.get("Type") != "volume" or item.get("Source", "") != "" or
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


def _validate_docker_inspect(records: Any, *, cid: str, source_cid: str,
                             image_id: str, volume: str, sha: str, tree: str,
                             run_id: int, attempt: int, collector: bool) -> None:
    if (type(records) is not list or len(records) != 1 or
            type(records[0]) is not dict):
        _red("INSPECT_IDENTITY_MISMATCH")
    entry = records[0]
    if entry.get("Id") != cid or entry.get("Image") != image_id:
        _red("INSPECT_IDENTITY_MISMATCH")
    config, host = entry.get("Config"), entry.get("HostConfig")
    if type(config) is not dict or type(host) is not dict:
        _red("INSPECT_CONFIG_UNSAFE")
    labels = {
        "porota.commit": sha, "porota.candidate_tree": tree,
        "porota.predeploy": "v2", "porota.native-proof": "v3",
        "porota.native-role": "bundle-collector" if collector else "bundle",
        "porota.run_id": str(run_id), "porota.run_attempt": str(attempt),
    }
    if config.get("User") != "1000:1000" or config.get("Labels") != labels:
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
    mounts = entry.get("Mounts")
    if type(mounts) is not list or len(mounts) != 1 or type(mounts[0]) is not dict:
        _red("INSPECT_VOLUME_MISMATCH")
    mount = mounts[0]
    if (mount.get("Type") != "volume" or mount.get("Name") != volume or
            type(mount.get("Source")) is not str or not mount["Source"] or
            mount.get("Driver") != "local" or
            mount.get("Destination") != "/app/data" or
            mount.get("Mode") != ("ro" if collector else "") or
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
        records = load_unique_json(payload[filename])
        _validate_docker_inspect(
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
                         pr: Mapping[str, Any], commit: Mapping[str, Any],
                         artifact_list: Mapping[str, Any], artifact: Mapping[str, Any],
                         zip_bytes: bytes, expected_run_id: int,
                         expected_run_attempt: int) -> dict[str, Any]:
    """Authenticate caller-supplied REST receipts and exact bytes; no GO."""
    sha, tree, name = _identity(event, run, pr, commit,
                                expected_run_id, expected_run_attempt)
    artifact_id, digest, size = _artifact_identity(
        artifact_list, artifact, name=name, sha=sha, run_id=expected_run_id)
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
        "native_host_custody_verified": False, "automatic_go": False,
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
    pr_path = f"{prefix}/pulls/{PR_NUMBER}"
    list_path = f"{run_path}/artifacts?per_page=100"
    run = client.get_json(run_path)
    pr = client.get_json(pr_path)
    sha = run.get("head_sha")
    if not _sha(sha):
        _red("RUN_SHA_MISMATCH")
    commit = client.get_json(f"{prefix}/git/commits/{sha}")
    sha_checked, tree, name = _identity(event, run, pr, commit,
                                        expected_run_id, expected_run_attempt)
    artifact_list = client.get_json(list_path)
    artifact_id = _select_attempt_artifact(artifact_list, name)["id"]
    artifact_path = f"{prefix}/actions/artifacts/{artifact_id}"
    artifact = client.get_json(artifact_path)
    # Validate all metadata BEFORE accepting the potentially large download.
    _artifact_identity(artifact_list, artifact, name=name,
                       sha=sha_checked, run_id=expected_run_id)
    raw = client.get_zip(artifact_id)
    verified = verify_postrun_bytes(
        event=event, run=run, pr=pr, commit=commit,
        artifact_list=artifact_list, artifact=artifact, zip_bytes=raw,
        expected_run_id=expected_run_id, expected_run_attempt=expected_run_attempt,
    )
    # A new PR push, rerun attempt, artifact replacement or deleted run during
    # the ZIP fetch invalidates the result. No stale-state or name fallback.
    run_after = client.get_json(run_path)
    pr_after = client.get_json(pr_path)
    list_after = client.get_json(list_path)
    artifact_after = client.get_json(artifact_path)
    try:
        reverified = verify_postrun_bytes(
            event=event, run=run_after, pr=pr_after, commit=commit,
            artifact_list=list_after, artifact=artifact_after, zip_bytes=raw,
            expected_run_id=expected_run_id,
            expected_run_attempt=expected_run_attempt,
        )
    except PostrunEvidenceRed:
        _red("REST_RACE_RED")
    if reverified != verified:
        _red("REST_RACE_RED")
    return verified


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
