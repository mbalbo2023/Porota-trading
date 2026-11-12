#!/usr/bin/env python3
"""OFFHOST ZIP byte verification for a future, independently trusted RC6 gate.

The legacy PR modes are deliberately not release authorities. The R251
trusted-main workflow may invoke the additional *release-byte* mode without
lifting release/deploy gates. A fixed-SHA trusted
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
import io
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from typing import Any, Mapping
import xml.etree.ElementTree as ET
import zipfile


REPOSITORY = "mbalbo2023/Porota-trading"
RELEASE_PR = 1087
RELEASE_CANDIDATE_BRANCH = "integration/rc6-a1115-r87-unified-paper-candidate-20261110"
RELEASE_PRODUCT_BRANCH = "deploy/rc6-pr69-isolated-20260915"
R206_AUDIT_WORKFLOW = ".github/workflows/rc6-r206-trusted-postcompletion.yml"
R206_AUDIT_NAME = "RC6 R206 Trusted Postcompletion Public Byte Audit (READ ONLY)"
PREDEPLOY_WORKFLOW = ".github/workflows/porota-predeploy-v2.yml"
OFFHOST_PROOF_WORKFLOW = ".github/workflows/rc6-r163-offhost-guard79-proof.yml"
RELEASE_WORKFLOW = ".github/workflows/rc6-r251-trusted-release-build.yml"
RELEASE_WORKFLOW_NAME = "RC6 R251 Trusted Release Build (OFFHOST ONLY)"
ALLOWED_WORKFLOWS = frozenset({PREDEPLOY_WORKFLOW, OFFHOST_PROOF_WORKFLOW, RELEASE_WORKFLOW})
MANIFEST_NAME = "porota-artifact-byte-manifest-v1.json"
MANIFEST_KEYS = frozenset({
    "schema_version", "candidate_sha", "candidate_tree_sha", "run_id",
    "run_attempt", "artifact_name", "file_count", "total_size_bytes", "files",
})
FILE_KEYS = frozenset({"path", "type", "size_bytes", "sha256"})
RELEASE_MANIFEST_KEYS = MANIFEST_KEYS | {"publisher_main_sha"}
RELEASE_PAYLOAD_FILES = frozenset({
    "porota-deploy-bundle-v2.tgz", "porota-deploy-bundle-v2-manifest.json",
    "porota-predeploy-image.tar.gz", "porota-frozen-candidate.json",
    "porota-image-build-receipt.json", "porota-host-manifest-v2.json",
    "porota-host-policy-v2.json",
})
RELEASE_SMALL_JSON_FILES = frozenset({
    "porota-frozen-candidate.json", "porota-image-build-receipt.json",
    "porota-deploy-bundle-v2-manifest.json", "porota-host-manifest-v2.json",
    "porota-host-policy-v2.json",
})
RELEASE_NATIVE_XML_PATHS = frozenset({
    "native-bound.xml", "session/dashboard_auth_52.xml",
    "session/publication_io_identity.xml", "session/scalping_capacity.xml",
    "session/scheduler_monitor_backup.xml", "session/signals_risk_exit_ledger.xml",
    "session/sources_readiness_network.xml", "session/sql_shadow_wal.xml",
    "session/treasury_caucion.xml",
})
RELEASE_NATIVE_GENERATIONS = {
    "catalog/full-catalog.json": 2, "session/stress.json": 2,
    "cadence/cadence-fresh.json": 12, "cadence/cadence-legacy.json": 12,
}
RELEASE_NATIVE_STAGES = frozenset({
    "cases", "catalog", "session", "cadence-fresh", "cadence-legacy",
})
RELEASE_NATIVE_PROOF_KEYS = frozenset({
    "schema", "schema_version", "status", "candidate_sha",
    "candidate_tree_sha", "trusted_main_sha", "image_id", "run_id",
    "run_attempt", "source_cid", "collector_cid", "native_tar_sha256",
    "native_tar_size_bytes", "native_file_count", "native_case_counts",
    "native_generations", "stage_exit_codes", "private_raw_uploaded",
    "paper_only", "release_go", "host_runtime_verified",
    "real_orders_physical_verified",
})
POSTCOMPLETION_RECEIPT_NAME = "rc6-r251-release-postcompletion-v1.json"
POSTCOMPLETION_RECEIPT_KEYS = frozenset({
    "schema_version", "status", "repository", "candidate_sha",
    "candidate_tree_sha", "publisher_main_sha", "publisher_workflow_path",
    "publisher_run_id", "publisher_run_attempt", "release_artifact_id",
    "release_artifact_name", "release_artifact_digest",
    "release_artifact_size_bytes", "file_count", "total_size_bytes",
    "release_manifest_verified", "r144_release_go", "runtime_verified",
    "real_orders_physical_verified",
})
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


def _require_release_native_proof(
    proof: Any, *, image_id: str, candidate_sha: str,
    candidate_tree_sha: str, trusted_main_sha: str,
    run_id: int, run_attempt: int,
) -> None:
    """E and R252 validate D's trusted native attestation, not a B PROBE.

    Private raw reports remain on D's ephemeral runner; the seven-member ZIP
    contains a bounded native summary sealed inside its image-build receipt.
    An authenticated D workflow run is required for the execution claim.
    """
    if type(proof) is not dict or set(proof) != RELEASE_NATIVE_PROOF_KEYS:
        _red("RELEASE_INNER_CLAIM_RED")
    expected = {
        "schema": "RC6_R251_RELEASE_NATIVE_V1", "schema_version": 1,
        "status": "SAME_IMAGE_TESTS_PASS", "image_id": image_id,
        "candidate_sha": candidate_sha,
        "candidate_tree_sha": candidate_tree_sha,
        "trusted_main_sha": trusted_main_sha,
        "run_id": run_id, "run_attempt": run_attempt,
        "native_file_count": 13, "private_raw_uploaded": False,
        "paper_only": True, "release_go": False,
        "host_runtime_verified": False, "real_orders_physical_verified": False,
    }
    if any(type(proof.get(key)) is not type(value) or proof[key] != value
           for key, value in expected.items()):
        _red("RELEASE_INNER_CLAIM_RED")
    source_cid, collector_cid = proof["source_cid"], proof["collector_cid"]
    if (not all(type(cid) is str and HEX64.fullmatch(cid)
                for cid in (source_cid, collector_cid)) or
            source_cid == collector_cid or
            type(proof["native_tar_sha256"]) is not str or
            HEX64.fullmatch(proof["native_tar_sha256"]) is None or
            not _integer(proof["native_tar_size_bytes"], minimum=1024) or
            proof["native_tar_size_bytes"] > 32 * 1024 * 1024):
        _red("RELEASE_INNER_CLAIM_RED")
    cases = proof["native_case_counts"]
    if (type(cases) is not dict or set(cases) != RELEASE_NATIVE_XML_PATHS or
            any(not _integer(value, minimum=1) or value > 100000
                for value in cases.values()) or cases["native-bound.xml"] < 8):
        _red("RELEASE_INNER_CLAIM_RED")
    generations = proof["native_generations"]
    if (type(generations) is not dict or
            any(type(generations.get(key)) is not int or
                generations[key] != expected
                for key, expected in RELEASE_NATIVE_GENERATIONS.items()) or
            set(generations) != set(RELEASE_NATIVE_GENERATIONS)):
        _red("RELEASE_INNER_CLAIM_RED")
    stages = proof["stage_exit_codes"]
    if (type(stages) is not dict or set(stages) != RELEASE_NATIVE_STAGES or
            any(type(code) is not int or code != 0 for code in stages.values())):
        _red("RELEASE_INNER_CLAIM_RED")


def _read_private_release_junit(root: Path, relative: str) -> bytes:
    """Read only a fixed private JUnit from D's Docker-copied CID, no symlinks."""
    if relative not in RELEASE_NATIVE_XML_PATHS:
        _red("RELEASE_NATIVE_TAR_RED")
    try:
        parts = relative.split("/")
        for directory in (root, root / parts[0]) if len(parts) > 1 else (root,):
            meta = directory.lstat()
            if (not stat.S_ISDIR(meta.st_mode) or meta.st_uid != os.getuid() or
                    meta.st_mode & 0o077):
                _red("RELEASE_NATIVE_TAR_RED")
        target = root.joinpath(*parts)
        named = target.lstat()
        if (not stat.S_ISREG(named.st_mode) or named.st_uid != os.getuid() or
                named.st_nlink != 1 or
                not 0 < named.st_size <= 8 * 1024 * 1024):
            _red("RELEASE_NATIVE_TAR_RED")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        with os.fdopen(os.open(target, flags), "rb") as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or
                    before.st_nlink != 1 or
                    (before.st_dev, before.st_ino, before.st_size) !=
                    (named.st_dev, named.st_ino, named.st_size)):
                _red("RELEASE_NATIVE_TAR_RED")
            # docker cp can preserve the source's 0644 umask, even though the
            # destination is inside an owned 0700 runner-only directory.
            # Normalize ONLY the verified local regular inode via descriptor;
            # never chmod a path that might have changed into a symlink.
            if stat.S_IMODE(before.st_mode) != 0o600:
                os.fchmod(stream.fileno(), 0o600)
            secured = os.fstat(stream.fileno())
            if (stat.S_IMODE(secured.st_mode) != 0o600 or
                    (secured.st_dev, secured.st_ino, secured.st_size) !=
                    (before.st_dev, before.st_ino, before.st_size)):
                _red("RELEASE_NATIVE_TAR_RED")
            raw = stream.read(8 * 1024 * 1024 + 1)
            after = os.fstat(stream.fileno())
            if (len(raw) != secured.st_size or
                    (secured.st_mtime_ns, secured.st_ctime_ns) !=
                    (after.st_mtime_ns, after.st_ctime_ns)):
                _red("RELEASE_NATIVE_TAR_RED")
        return raw
    except OSError:
        _red("RELEASE_NATIVE_TAR_RED")


def _count_private_release_junit(raw: bytes, *, require_eight_modules: bool) -> int:
    """Independently recount real private testcases; reject false zero skips."""
    if (not 0 < len(raw) <= 8 * 1024 * 1024 or b"\x00" in raw or
            raw.count(b"<") > 400000 or
            any(marker in raw.upper() for marker in
                (b"<!DOCTYPE", b"<!ENTITY", b"<![CDATA"))):
        _red("RELEASE_NATIVE_TAR_RED")
    try:
        root = ET.fromstring(raw)
        if root.tag not in {"testsuites", "testsuite"}:
            _red("RELEASE_NATIVE_TAR_RED")
        suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
        if not suites or len(suites) != len(list(root.iter("testsuite"))):
            _red("RELEASE_NATIVE_TAR_RED")
        cases_total = 0
        modules: set[str] = set()
        for suite in suites:
            attrs = {}
            for key in ("tests", "failures", "errors", "skipped"):
                literal = suite.get(key)
                if (type(literal) is not str or
                        re.fullmatch(r"[0-9]{1,9}", literal) is None):
                    _red("RELEASE_NATIVE_TAR_RED")
                attrs[key] = int(literal)
            cases = suite.findall("testcase")
            if (not 0 < attrs["tests"] <= 100000 or
                    attrs["tests"] != len(cases) or
                    any(attrs[key] != 0 for key in ("failures", "errors", "skipped"))):
                _red("RELEASE_NATIVE_TAR_RED")
            cases_total += len(cases)
            if cases_total > 100000:
                _red("RELEASE_NATIVE_TAR_RED")
            for case in cases:
                classname = case.get("classname")
                if type(classname) is str:
                    modules.add(classname)
        if (len(list(root.iter("testcase"))) != cases_total or
                any(next(root.iter(tag), None) is not None
                    for tag in ("failure", "error", "skipped"))):
            _red("RELEASE_NATIVE_TAR_RED")
        if require_eight_modules:
            required = (
                "test_rc6_shadow_native_bound", "test_rc6_shadow_streaming_cost",
                "test_rc6_shadow_shared_audit", "test_rc6_shadow_owned_state",
                "test_rc6_shadow_report_lifetime", "test_rc6_shadow_cold_audit",
                "test_rc6_owned_funnel_details", "test_rc6_physical_cadence",
            )
            if not all(any(name in classname.split(".") for classname in modules)
                       for name in required):
                _red("RELEASE_NATIVE_TAR_RED")
        return cases_total
    except (ET.ParseError, ValueError, TypeError, RecursionError):
        _red("RELEASE_NATIVE_TAR_RED")


def build_release_native_proof(
    native_tar: Path, *, private_junit_root: Path,
    candidate_sha: str, candidate_tree_sha: str,
    trusted_main_sha: str, image_id: str, run_id: int, run_attempt: int,
    source_cid: str, collector_cid: str,
) -> dict[str, Any]:
    """Project R206's already-vetted public 13-member collector TAR, not raw XML.

    D calls this only after every same-image docker exec returns zero and the
    container/collector CIDs were authenticated by its trusted shell. Neither
    a second Docker image nor an extra release member is created.
    """
    if (not all(type(value) is str and HEX40.fullmatch(value)
                for value in (candidate_sha, candidate_tree_sha, trusted_main_sha)) or
            type(image_id) is not str or
            re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None or
            not _integer(run_id, minimum=1) or not _integer(run_attempt, minimum=1)):
        _red("RELEASE_NATIVE_INPUT_RED")
    try:
        named = native_tar.lstat()
        if (not stat.S_ISREG(named.st_mode) or named.st_nlink != 1 or
                named.st_uid != os.getuid() or
                not 1024 <= named.st_size <= 32 * 1024 * 1024):
            _red("RELEASE_NATIVE_TAR_RED")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        with os.fdopen(os.open(native_tar, flags), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_size) != (
                    named.st_dev, named.st_ino, named.st_size):
                _red("RELEASE_NATIVE_TAR_RED")
            raw = stream.read(32 * 1024 * 1024 + 1)
            after = os.fstat(stream.fileno())
            if (len(raw) != opened.st_size or
                    (opened.st_mtime_ns, opened.st_ctime_ns) !=
                    (after.st_mtime_ns, after.st_ctime_ns)):
                _red("RELEASE_NATIVE_TAR_RED")
        r206 = _load_trusted_r206_module()
        files = r206._native_tar_files(raw)
        if len(files) != 13:
            _red("RELEASE_NATIVE_TAR_RED")
        case_counts: dict[str, int] = {}
        generations: dict[str, int] = {}
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for item in archive:
                name = item.name.removeprefix("./")
                stream = archive.extractfile(item)
                if stream is None:
                    _red("RELEASE_NATIVE_TAR_RED")
                content = stream.read(item.size + 1)
                if name in RELEASE_NATIVE_XML_PATHS:
                    match = re.search(rb' tests="([1-9][0-9]{0,5})" ', content)
                    if match is None:
                        _red("RELEASE_NATIVE_TAR_RED")
                    raw_junit = _read_private_release_junit(private_junit_root, name)
                    # Public projection has fixed schema after R206 parser.
                    # Unlike R206's source-side hash claim, D independently
                    # re-hashes private bytes copied from its owned source CID.
                    public = ET.fromstring(content)
                    raw_hash = hashlib.sha256(raw_junit).hexdigest()
                    if (public.get("private_raw_sha256_unattested") != raw_hash or
                            public.get("private_raw_size_bytes") != str(len(raw_junit))):
                        _red("RELEASE_NATIVE_TAR_RED")
                    verified_cases = _count_private_release_junit(
                        raw_junit, require_eight_modules=name == "native-bound.xml")
                    if verified_cases != int(match[1]):
                        _red("RELEASE_NATIVE_TAR_RED")
                    case_counts[name] = verified_cases
                elif name in RELEASE_NATIVE_GENERATIONS:
                    generations[name] = _parse_json(content)["required_generations"]
                else:
                    _red("RELEASE_NATIVE_TAR_RED")
        proof = {
            "schema": "RC6_R251_RELEASE_NATIVE_V1", "schema_version": 1,
            "status": "SAME_IMAGE_TESTS_PASS", "candidate_sha": candidate_sha,
            "candidate_tree_sha": candidate_tree_sha,
            "trusted_main_sha": trusted_main_sha, "image_id": image_id,
            "run_id": run_id, "run_attempt": run_attempt,
            "source_cid": source_cid, "collector_cid": collector_cid,
            "native_tar_sha256": hashlib.sha256(raw).hexdigest(),
            "native_tar_size_bytes": len(raw), "native_file_count": len(files),
            "native_case_counts": case_counts,
            "native_generations": generations,
            "stage_exit_codes": {name: 0 for name in RELEASE_NATIVE_STAGES},
            "private_raw_uploaded": False, "paper_only": True,
            "release_go": False, "host_runtime_verified": False,
            "real_orders_physical_verified": False,
        }
        _require_release_native_proof(
            proof, image_id=image_id, candidate_sha=candidate_sha,
            candidate_tree_sha=candidate_tree_sha,
            trusted_main_sha=trusted_main_sha,
            run_id=run_id, run_attempt=run_attempt,
        )
        return proof
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as exc:
        if isinstance(exc, ArtifactByteEvidenceRed) and str(exc) == "RELEASE_NATIVE_INPUT_RED":
            raise
        _red("RELEASE_NATIVE_TAR_RED")


def _cross_bind_release_inner_claims(
    documents: Mapping[str, bytes], hashes: Mapping[str, str], *,
    candidate_sha: str, candidate_tree_sha: str, trusted_main_sha: str,
    run_id: int, run_attempt: int,
) -> None:
    """Independent, bounded PAPER/frozen-hash cross-bind of already hashed ZIP.

    The canonical full tar validator remains PRODUCT's pinned
    ci_frozen_candidate_contract.py, which D runs before upload. This extra
    check detects a same-UID file swap after D's validation but before upload
    without re-reading the large image or introducing another ZIP parser.
    """
    try:
        if set(documents) != RELEASE_SMALL_JSON_FILES:
            _red("RELEASE_INNER_CLAIM_RED")
        parsed = {name: _parse_json(raw) for name, raw in documents.items()
                  if type(raw) is bytes and len(raw) <= MAX_MANIFEST_BYTES}
        if set(parsed) != RELEASE_SMALL_JSON_FILES or any(
                type(item) is not dict for item in parsed.values()):
            _red("RELEASE_INNER_CLAIM_RED")
        frozen = parsed["porota-frozen-candidate.json"]
        image = parsed["porota-image-build-receipt.json"]
        bundle = parsed["porota-deploy-bundle-v2-manifest.json"]
        host = parsed["porota-host-manifest-v2.json"]
        policy = parsed["porota-host-policy-v2.json"]
        image_hash = hashes["porota-predeploy-image.tar.gz"]
        bundle_hash = hashes["porota-deploy-bundle-v2.tgz"]
        if (frozen.get("schema_version") != 1 or
                frozen.get("candidate_sha") != candidate_sha or
                frozen.get("candidate_tree_sha") != candidate_tree_sha or
                frozen.get("image_tar_sha256") != image_hash or
                type(frozen.get("image_config_sha256")) is not str or
                HEX64.fullmatch(frozen["image_config_sha256"]) is None or
                type(frozen.get("image_id")) is not str or
                re.fullmatch(r"sha256:[0-9a-f]{64}", frozen["image_id"]) is None or
                not _integer(frozen.get("image_size_bytes"), minimum=1) or
                frozen.get("paper_mode_required") != "PRODUCTION_PAPER" or
                type(frozen.get("real_orders_sent_required")) is not int or
                frozen["real_orders_sent_required"] != 0 or
                frozen.get("real_order_capability_required") != "BLOCKED" or
                frozen.get("build_once") is not True or
                bundle.get("schema_version") != 1 or
                bundle.get("status") != "GREEN" or
                bundle.get("bundle_sha256") != bundle_hash or
                not _integer(bundle.get("file_count"), minimum=1) or
                image.get("schema_version") != 1 or
                image.get("candidate_sha") != candidate_sha or
                image.get("candidate_tree_sha") != candidate_tree_sha or
                image.get("trusted_main_sha") != trusted_main_sha or
                image.get("builder_product_sha") !=
                    "7ac6e368bd42137817e3271839c35f579bdbbbbe" or
                image.get("builder_blob_sha") !=
                    "0fe454dcdddc445d719288ed0673f3bfa279526f" or
                image.get("image_id") != frozen["image_id"] or
                image.get("image_size_bytes") != frozen["image_size_bytes"] or
                image.get("image_tar_sha256") != image_hash or
                image.get("bundle_sha256") != bundle_hash or
                type(image.get("native_proof")) is not dict or
                image.get("paper_only") is not True or
                image.get("release_go") is not False or
                image.get("host_runtime_verified") is not False or
                host.get("schema_version") != 2 or
                host.get("status") != "GREEN" or
                host.get("source_git_sha") != candidate_sha or
                policy.get("status") != "GREEN"):
            _red("RELEASE_INNER_CLAIM_RED")
        _require_release_native_proof(
            image["native_proof"], image_id=frozen["image_id"],
            candidate_sha=candidate_sha, candidate_tree_sha=candidate_tree_sha,
            trusted_main_sha=trusted_main_sha,
            run_id=run_id, run_attempt=run_attempt,
        )
    except (ArtifactByteEvidenceRed, KeyError, TypeError, ValueError):
        _red("RELEASE_INNER_CLAIM_RED")


def seal_release_directory(
    stage: Path, *, candidate_sha: str, candidate_tree_sha: str,
    publisher_main_sha: str, run_id: int, run_attempt: int,
) -> dict[str, Any]:
    """Seal exact trusted-owned staging files; never inspect untrusted PR code.

    The uploader may race the sealed files until it finishes. This intrarun
    manifest is *not* evidence of postupload immutability: the separate E
    workflow fetches and rehashes the completed GitHub artifact raw ZIP.
    """
    if (not all(type(x) is str and HEX40.fullmatch(x)
                for x in (candidate_sha, candidate_tree_sha, publisher_main_sha)) or
            not _integer(run_id, minimum=1) or not _integer(run_attempt, minimum=1)):
        _red("SEAL_IDENTITY_INVALID")
    stage = Path(stage)
    try:
        parent = stage.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_mode & 0o077 or
                parent.st_uid != os.getuid()):
            _red("STAGE_ROOT_UNSAFE")
        children = {path.name: path for path in stage.iterdir()}
    except OSError:
        _red("STAGE_ROOT_UNSAFE")
    if set(children) != RELEASE_PAYLOAD_FILES:
        _red("STAGE_EXTRA_FILE")
    rows: list[dict[str, Any]] = []
    total = 0
    for name in sorted(RELEASE_PAYLOAD_FILES):
        path = children[name]
        try:
            named = path.lstat()
            if (not stat.S_ISREG(named.st_mode) or named.st_nlink != 1 or
                    named.st_uid != os.getuid()):
                _red("STAGE_FILE_UNSAFE")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            with os.fdopen(os.open(path, flags), "rb") as stream:
                opened = os.fstat(stream.fileno())
                if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
                        (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
                    _red("STAGE_FILE_UNSAFE")
                digest = hashlib.sha256()
                size = 0
                while chunk := stream.read(CHUNK_BYTES):
                    digest.update(chunk)
                    size += len(chunk)
                    if total + size > MAX_UNCOMPRESSED_BYTES:
                        _red("STAGE_SIZE_LIMIT")
                after = os.fstat(stream.fileno())
                if ((opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns,
                     opened.st_ctime_ns) !=
                    (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                     after.st_ctime_ns) or size != opened.st_size):
                    _red("STAGE_FILE_CHANGED")
        except OSError:
            _red("STAGE_FILE_UNSAFE")
        total += size
        rows.append({"path": name, "type": "file", "size_bytes": size,
                     "sha256": digest.hexdigest()})
    documents = {}
    for name in RELEASE_SMALL_JSON_FILES:
        try:
            if (stage / name).stat().st_size > MAX_MANIFEST_BYTES:
                _red("RELEASE_INNER_CLAIM_RED")
            documents[name] = (stage / name).read_bytes()
        except OSError:
            _red("RELEASE_INNER_CLAIM_RED")
    _cross_bind_release_inner_claims(
        documents, {row["path"]: row["sha256"] for row in rows},
        candidate_sha=candidate_sha, candidate_tree_sha=candidate_tree_sha,
        trusted_main_sha=publisher_main_sha,
        run_id=run_id, run_attempt=run_attempt,
    )
    result = {
        "schema_version": 1, "candidate_sha": candidate_sha,
        "candidate_tree_sha": candidate_tree_sha,
        "publisher_main_sha": publisher_main_sha,
        "run_id": run_id, "run_attempt": run_attempt,
        "artifact_name": f"rc6-r251-release-{run_id}-{run_attempt}-{candidate_sha}",
        "file_count": len(rows), "total_size_bytes": total, "files": rows,
    }
    serialized = (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(serialized) > MAX_MANIFEST_BYTES:
        _red("MANIFEST_MISSING_OR_OVERSIZED")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        with os.fdopen(os.open(stage / MANIFEST_NAME, flags, 0o600), "wb") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        _red("STAGE_EXTRA_FILE")
    except OSError:
        _red("STAGE_MANIFEST_IO_RED")
    return result


def verify_release_owner_go_comment(
    comment: Mapping[str, Any], *, expected_comment_id: int,
    candidate_sha: str, candidate_tree_sha: str, trusted_main_sha: str,
    r206_publisher_run_id: int, r206_publisher_attempt: int,
    r206_audit_run_id: int, r206_audit_attempt: int,
) -> None:
    """Bind a specific, independently fetched, unedited GitHub owner decision.

    GitHub issue comments are not immutable in general. Even a matching comment
    is an OFFHOST build-only GO, NEVER a promotion or host/runtime release GO.
    """
    expected = {
        "operation": "OFFHOST_BUILD_ONCE_NO_DEPLOY", "issue": 1093,
        "candidate_sha": candidate_sha, "candidate_tree_sha": candidate_tree_sha,
        "trusted_main_sha": trusted_main_sha,
        "r206_publisher_run_id": r206_publisher_run_id,
        "r206_publisher_attempt": r206_publisher_attempt,
        "r206_audit_run_id": r206_audit_run_id,
        "r206_audit_attempt": r206_audit_attempt,
    }
    if (not isinstance(comment, Mapping) or
            not _integer(expected_comment_id, minimum=1) or
            comment.get("id") != expected_comment_id or
            not isinstance(comment.get("user"), Mapping) or
            comment["user"].get("login") != "mbalbo2023" or
            comment.get("author_association") != "OWNER" or
            comment.get("issue_url") !=
            "https://api.github.com/repos/mbalbo2023/Porota-trading/issues/1093" or
            not isinstance(comment.get("created_at"), str) or
            comment.get("updated_at") != comment.get("created_at")):
        _red("OWNER_GO_INVALID")
    body = comment.get("body")
    marker = "RC6_R251_BUILD_ONCE_GO_V1="
    if (type(body) is not str or not body.startswith(marker) or
            len(body) > MAX_MANIFEST_BYTES):
        _red("OWNER_GO_INVALID")
    try:
        payload = _parse_json(body[len(marker):].encode("utf-8"))
    except ArtifactByteEvidenceRed:
        _red("OWNER_GO_INVALID")
    if payload != expected or type(payload) is not dict:
        _red("OWNER_GO_INVALID")


def _load_trusted_r206_module() -> Any:
    """Reuse R206's trusted REST/probe auditor; do not fork its ZIP parser."""
    source = Path(__file__).with_name("rc6_r163_trusted_postrun_byte_audit.py")
    spec = importlib.util.spec_from_file_location("rc6_r206_release_audit", source)
    if spec is None or spec.loader is None:
        _red("R206_TRUSTED_AUDITOR_MISSING")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _strict_main_workflow_path(value: Any, path: str, main_sha: str) -> bool:
    return type(value) is str and value in {
        path, path + "@main", path + "@refs/heads/main", path + "@" + main_sha,
    }


def authorize_release_source(
    client: Any, *, candidate_sha: str, candidate_tree_sha: str,
    trusted_main_sha: str, owner_comment_id: int,
    r206_publisher_run_id: int, r206_publisher_attempt: int,
    r206_audit_run_id: int, r206_audit_attempt: int,
) -> dict[str, Any]:
    """Authenticate owner freeze, exact PR head, R206 B bytes and C status.

    It only authorizes an OFFHOST build once. It does not prove the webhook
    B→C edge or host/runtime safety, and never returns a release GO.
    """
    if (not all(type(x) is str and HEX40.fullmatch(x)
                for x in (candidate_sha, candidate_tree_sha, trusted_main_sha)) or
            not all(_integer(x, minimum=1) for x in (
                owner_comment_id, r206_publisher_run_id, r206_publisher_attempt,
                r206_audit_run_id, r206_audit_attempt))):
        _red("SOURCE_EXPECTED_IDENTITY_INVALID")
    prefix = f"/repos/{REPOSITORY}"
    comment_path = f"{prefix}/issues/comments/{owner_comment_id}"
    pr_path = f"{prefix}/pulls/{RELEASE_PR}"
    commit_path = f"{prefix}/git/commits/{candidate_sha}"
    audit_path = f"{prefix}/actions/runs/{r206_audit_run_id}"
    attempt_path = f"{audit_path}/attempts/{r206_audit_attempt}"
    comment = client.get_json(comment_path)
    verify_release_owner_go_comment(
        comment, expected_comment_id=owner_comment_id,
        candidate_sha=candidate_sha, candidate_tree_sha=candidate_tree_sha,
        trusted_main_sha=trusted_main_sha,
        r206_publisher_run_id=r206_publisher_run_id,
        r206_publisher_attempt=r206_publisher_attempt,
        r206_audit_run_id=r206_audit_run_id,
        r206_audit_attempt=r206_audit_attempt,
    )
    pr = client.get_json(pr_path)
    commit = client.get_json(commit_path)
    if (pr.get("number") != RELEASE_PR or pr.get("state") != "open" or
            not isinstance(pr.get("head"), Mapping) or
            not isinstance(pr.get("base"), Mapping) or
            pr["head"].get("sha") != candidate_sha or
            pr["head"].get("ref") != RELEASE_CANDIDATE_BRANCH or
            pr["base"].get("ref") != RELEASE_PRODUCT_BRANCH or
            not isinstance(pr["head"].get("repo"), Mapping) or
            pr["head"]["repo"].get("full_name") != REPOSITORY or
            not isinstance(pr["base"].get("repo"), Mapping) or
            pr["base"]["repo"].get("full_name") != REPOSITORY or
            commit.get("sha") != candidate_sha or
            not isinstance(commit.get("tree"), Mapping) or
            commit["tree"].get("sha") != candidate_tree_sha):
        _red("SOURCE_CANDIDATE_IDENTITY_RED")
    audit = client.get_json(audit_path)
    attempt = client.get_json(attempt_path)
    for receipt in (audit, attempt):
        if (not isinstance(receipt, Mapping) or
                receipt.get("id") != r206_audit_run_id or
                receipt.get("run_attempt") != r206_audit_attempt or
                receipt.get("name") != R206_AUDIT_NAME or
                receipt.get("event") != "workflow_run" or
                receipt.get("head_sha") != trusted_main_sha or
                receipt.get("head_branch") != "main" or
                not _strict_main_workflow_path(
                    receipt.get("path"), R206_AUDIT_WORKFLOW,
                    trusted_main_sha) or
                receipt.get("status") != "completed" or
                receipt.get("conclusion") != "success" or
                not isinstance(receipt.get("repository"), Mapping) or
                receipt["repository"].get("full_name") != REPOSITORY):
            _red("R206_C_IDENTITY_RED")
    r206 = _load_trusted_r206_module()
    publisher_path = f"{prefix}/actions/runs/{r206_publisher_run_id}"
    publisher = client.get_json(publisher_path)
    # A constructed REST event is NOT the signed original webhook edge. R206
    # re-audits B's public bytes using exact REST identity/attempt artifacts.
    rest_event = {"action": "completed",
                  "repository": {"full_name": REPOSITORY},
                  "workflow_run": publisher}
    try:
        audited = r206.audit_publisher_from_github(
            client, event=rest_event,
            expected_publisher_run_id=r206_publisher_run_id,
            expected_publisher_attempt=r206_publisher_attempt,
            trusted_main_sha=trusted_main_sha,
        )
    except (r206.PostrunEvidenceRed, ValueError):
        _red("R206_B_REAUDIT_RED")
    if (audited.get("status") != "POSTCOMPLETION_PUBLIC_PROBE_BYTES_MATCH" or
            audited.get("candidate_sha") != candidate_sha or
            audited.get("candidate_tree_sha") != candidate_tree_sha or
            audited.get("publisher_run_id") != r206_publisher_run_id or
            audited.get("publisher_attempt") != r206_publisher_attempt or
            audited.get("publisher_main_sha") != trusted_main_sha or
            audited.get("public_artifact_contents_verified") is not True):
        _red("R206_B_REAUDIT_IDENTITY_RED")
    # Re-fetch mutable receipts after R206's independent raw-ZIP read.
    for path, first in ((comment_path, comment), (pr_path, pr),
                        (commit_path, commit), (audit_path, audit),
                        (attempt_path, attempt), (publisher_path, publisher)):
        if client.get_json(path) != first:
            _red("SOURCE_REST_RACE")
    return {
        "status": "OFFHOST_BUILD_ONCE_PREREQUISITES_VERIFIED",
        "candidate_sha": candidate_sha, "candidate_tree_sha": candidate_tree_sha,
        "trusted_main_sha": trusted_main_sha,
        "owner_comment_id": owner_comment_id,
        "r206_publisher_run_id": r206_publisher_run_id,
        "r206_publisher_attempt": r206_publisher_attempt,
        "r206_audit_run_id": r206_audit_run_id,
        "r206_audit_attempt": r206_audit_attempt,
        "public_probe_bytes_reaudited": True,
        "r206_c_completed_success": True,
        "r206_c_to_b_original_webhook_edge_verified": False,
        "release_go": False, "runtime_verified": False,
    }


def _check_receipts(
    artifact: Mapping[str, Any], run: Mapping[str, Any], *,
    sha: str, run_id: int, attempt: int, artifact_id: int, digest: str,
    workflow_path: str, publisher_main_sha: str | None,
) -> str:
    if not isinstance(artifact, Mapping) or not isinstance(run, Mapping):
        _red("REST_RECEIPT_INVALID")
    if not _integer(artifact.get("id"), minimum=1) or artifact["id"] != artifact_id:
        _red("ARTIFACT_ID_MISMATCH")
    if workflow_path == PREDEPLOY_WORKFLOW:
        name = f"porota-predeploy-v2-{sha}"
    elif workflow_path == OFFHOST_PROOF_WORKFLOW:
        name = f"rc6-r163-offhost-{run_id}-{attempt}-{sha}"
    elif workflow_path == RELEASE_WORKFLOW:
        name = f"rc6-r251-release-{run_id}-{attempt}-{sha}"
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
    expected_run_sha = publisher_main_sha if workflow_path == RELEASE_WORKFLOW else sha
    if workflow.get("head_sha") != expected_run_sha:
        _red("ARTIFACT_SHA_MISMATCH")
    if not _integer(run.get("id"), minimum=1) or run["id"] != run_id:
        _red("RUN_ID_MISMATCH")
    if not _integer(run.get("run_attempt"), minimum=1) or run["run_attempt"] != attempt:
        _red("RUN_ATTEMPT_MISMATCH")
    if run.get("head_sha") != expected_run_sha:
        _red("RUN_SHA_MISMATCH")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        _red("RUN_NOT_SUCCESS")
    if workflow_path == RELEASE_WORKFLOW:
        allowed_paths = frozenset({workflow_path, workflow_path + "@main",
                                   workflow_path + "@refs/heads/main",
                                   workflow_path + "@" + publisher_main_sha})
        if (run.get("event") != "workflow_dispatch" or
                run.get("head_branch") != "main" or
                run.get("name") != RELEASE_WORKFLOW_NAME or
                run.get("path") not in allowed_paths):
            _red("RUN_WORKFLOW_MISMATCH")
    elif run.get("event") != "pull_request" or run.get("path") != workflow_path:
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
                attempt: int, name: str,
                publisher_main_sha: str | None = None) -> tuple[int, int]:
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
            expected_keys = (RELEASE_MANIFEST_KEYS if publisher_main_sha is not None
                             else MANIFEST_KEYS)
            if not isinstance(manifest, dict) or set(manifest) != expected_keys:
                _red("MANIFEST_SCHEMA_INVALID")
            if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
                _red("MANIFEST_SCHEMA_INVALID")
            if manifest["candidate_sha"] != sha:
                _red("MANIFEST_SHA_MISMATCH")
            if manifest["candidate_tree_sha"] != tree:
                _red("MANIFEST_TREE_MISMATCH")
            if (publisher_main_sha is not None and
                    manifest["publisher_main_sha"] != publisher_main_sha):
                _red("MANIFEST_PUBLISHER_SHA_MISMATCH")
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
            if publisher_main_sha is not None and expected_paths != RELEASE_PAYLOAD_FILES:
                _red("RELEASE_FILES_INVALID")
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
            if publisher_main_sha is not None:
                documents = {}
                for path in RELEASE_SMALL_JSON_FILES:
                    info = members[path]
                    if info.file_size > MAX_MANIFEST_BYTES:
                        _red("RELEASE_INNER_CLAIM_RED")
                    with archive.open(info, "r") as stream:
                        documents[path] = stream.read(MAX_MANIFEST_BYTES + 1)
                _cross_bind_release_inner_claims(
                    documents, {row["path"]: row["sha256"] for row in rows},
                    candidate_sha=sha, candidate_tree_sha=tree,
                    trusted_main_sha=publisher_main_sha,
                    run_id=run_id, run_attempt=attempt,
                )
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
    expected_publisher_sha: str | None = None,
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
    if expected_workflow_path == RELEASE_WORKFLOW:
        if (type(expected_publisher_sha) is not str or
                HEX40.fullmatch(expected_publisher_sha) is None):
            _red("EXPECTED_PUBLISHER_INVALID")
    elif expected_publisher_sha is not None:
        _red("EXPECTED_PUBLISHER_INVALID")
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
        publisher_main_sha=expected_publisher_sha,
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
                publisher_main_sha=expected_publisher_sha,
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
        **({"publisher_main_sha": expected_publisher_sha} if expected_publisher_sha else {}),
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


def verify_postcompletion_receipt_zip(
    archive: Path, *, expected_digest: str, expected_size_bytes: int,
) -> dict[str, Any]:
    """R251 E one-member receipt ZIP reader for downstream R252/R253 gates.

    Caller MUST independently authenticate the completed E REST run/attempt,
    artifact ID/name/digest and freshness before using the returned document.
    This does not validate the D RELEASE ZIP; use verify_artifact_bytes for D.
    """
    digest = _digest(expected_digest)
    if digest is None or not _integer(expected_size_bytes, minimum=1):
        _red("POST_RECEIPT_EXPECTED_INVALID")
    max_bytes = min(MAX_MANIFEST_BYTES * 2, MAX_ARCHIVE_BYTES)
    with tempfile.TemporaryFile(mode="w+b") as snapshot:
        actual_digest, size = _snapshot_archive(Path(archive), snapshot,
                                                limit=max_bytes)
        if actual_digest != digest or size != expected_size_bytes:
            _red("POST_RECEIPT_ZIP_DIGEST_MISMATCH")
        try:
            with zipfile.ZipFile(snapshot, "r") as zf:
                infos = zf.infolist()
                if len(infos) != 1 or infos[0].filename != POSTCOMPLETION_RECEIPT_NAME:
                    _red("POST_RECEIPT_ZIP_MEMBERS_INVALID")
                entry = infos[0]
                kind = stat.S_IFMT(entry.external_attr >> 16)
                if (entry.is_dir() or kind not in (0, stat.S_IFREG) or
                        entry.flag_bits & 1 or entry.compress_type not in
                        (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) or
                        not 0 < entry.file_size <= MAX_MANIFEST_BYTES or
                        not 0 < entry.compress_size or
                        entry.file_size > MAX_ZIP_EXPANSION_RATIO * entry.compress_size):
                    _red("POST_RECEIPT_ZIP_MEMBER_UNSAFE")
                with zf.open(entry, "r") as stream:
                    raw = stream.read(MAX_MANIFEST_BYTES + 1)
                if len(raw) != entry.file_size:
                    _red("POST_RECEIPT_ZIP_MEMBER_UNSAFE")
        except (OSError, zipfile.BadZipFile, RuntimeError, EOFError, ValueError) as exc:
            if isinstance(exc, ArtifactByteEvidenceRed):
                raise
            _red("POST_RECEIPT_ZIP_INVALID")
    receipt = _parse_json(raw)
    if type(receipt) is not dict or set(receipt) != POSTCOMPLETION_RECEIPT_KEYS:
        _red("POST_RECEIPT_SCHEMA_INVALID")
    if (type(receipt["schema_version"]) is not int or
            receipt["schema_version"] != 1 or
            receipt["status"] != "RELEASE_BYTES_VERIFIED_POSTCOMPLETION" or
            receipt["repository"] != REPOSITORY or
            any(type(receipt[name]) is not str or HEX40.fullmatch(receipt[name]) is None
                for name in ("candidate_sha", "candidate_tree_sha", "publisher_main_sha")) or
            receipt["publisher_workflow_path"] != RELEASE_WORKFLOW or
            any(not _integer(receipt[name], minimum=1) for name in (
                "publisher_run_id", "publisher_run_attempt", "release_artifact_id",
                "release_artifact_size_bytes", "file_count", "total_size_bytes")) or
            receipt["release_artifact_name"] != (
                f"rc6-r251-release-{receipt['publisher_run_id']}-"
                f"{receipt['publisher_run_attempt']}-{receipt['candidate_sha']}") or
            _digest(receipt["release_artifact_digest"], require_prefix=True) is None or
            receipt["file_count"] != len(RELEASE_PAYLOAD_FILES) or
            receipt["release_manifest_verified"] is not True or
            receipt["r144_release_go"] is not False or
            receipt["runtime_verified"] is not False or
            receipt["real_orders_physical_verified"] is not False):
        _red("POST_RECEIPT_SCHEMA_INVALID")
    return receipt


def download_release_zip(client: Any, artifact_id: int, destination: Path) -> None:
    """Copy bounded GitHub REST raw artifact ZIP; bearer stays at api.github.com.

    Reuse the R206 trusted storage redirect allowlist and no-redirect client,
    streaming to disk instead of its 64 MiB probe-only in-memory getter.
    """
    if not _integer(artifact_id, minimum=1):
        _red("EXPECTED_ARTIFACT_INVALID")
    r206 = _load_trusted_r206_module()
    endpoint = f"/repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip"
    try:
        response = client._opener.open(client._api_request(endpoint), timeout=30)
        if response.status == 200:
            source = response
            storage = None
        else:
            response.close()
            _red("RELEASE_ZIP_HTTP_RED")
    except urllib.error.HTTPError as redirect:
        if redirect.code != 302:
            _red("RELEASE_ZIP_HTTP_RED")
        storage = redirect.headers.get("Location")
        if not r206.allowed_storage_redirect(storage):
            _red("RELEASE_ZIP_REDIRECT_UNSAFE")
    except (OSError, urllib.error.URLError, TimeoutError):
        _red("RELEASE_ZIP_HTTP_RED")
    if storage is not None:
        request = urllib.request.Request(
            storage, headers={"User-Agent": "rc6-r251-trusted-release-postcompletion"})
        try:
            source = client._opener.open(request, timeout=60)
        except (OSError, urllib.error.URLError, TimeoutError):
            _red("RELEASE_ZIP_HTTP_RED")
    try:
        with source:
            if source.status != 200:
                _red("RELEASE_ZIP_HTTP_RED")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            with os.fdopen(os.open(destination, flags, 0o600), "wb") as out:
                size = 0
                while chunk := source.read(CHUNK_BYTES):
                    size += len(chunk)
                    if size > MAX_ARCHIVE_BYTES:
                        _red("RELEASE_ZIP_SIZE_LIMIT")
                    out.write(chunk)
                if size == 0:
                    _red("ARCHIVE_EMPTY")
                out.flush()
                os.fsync(out.fileno())
    except (OSError, urllib.error.URLError, TimeoutError):
        _red("RELEASE_ZIP_DOWNLOAD_RED")


def audit_release_postcompletion(
    client: Any, *, event: Mapping[str, Any],
    publisher_run_id: int, publisher_run_attempt: int,
    expected_publisher_sha: str,
) -> dict[str, Any]:
    """E: independently authenticate D completed success and exact raw ZIP.

    E's own run status is still PENDING; its output must be audited in a later
    R252/R253 successful-run REST pass using verify_postcompletion_receipt_zip.
    """
    if (not _integer(publisher_run_id, minimum=1) or
            not _integer(publisher_run_attempt, minimum=1) or
            type(expected_publisher_sha) is not str or
            HEX40.fullmatch(expected_publisher_sha) is None):
        _red("POST_EXPECTED_INVALID")
    triggered = event.get("workflow_run") if isinstance(event, Mapping) else None
    if (not isinstance(triggered, Mapping) or
            event.get("action") != "completed" or
            not isinstance(event.get("repository"), Mapping) or
            event["repository"].get("full_name") != REPOSITORY or
            triggered.get("id") != publisher_run_id or
            triggered.get("run_attempt") != publisher_run_attempt or
            triggered.get("head_sha") != expected_publisher_sha or
            triggered.get("event") != "workflow_dispatch" or
            triggered.get("name") != RELEASE_WORKFLOW_NAME or
            triggered.get("status") != "completed" or
            triggered.get("conclusion") != "success" or
            not _strict_main_workflow_path(triggered.get("path"),
                                           RELEASE_WORKFLOW, expected_publisher_sha)):
        _red("POST_EVENT_IDENTITY_RED")
    prefix = f"/repos/{REPOSITORY}"
    run_path = f"{prefix}/actions/runs/{publisher_run_id}"
    attempt_path = f"{run_path}/attempts/{publisher_run_attempt}"
    artifacts_path = f"{run_path}/artifacts?per_page=100"
    run = client.get_json(run_path)
    attempt = client.get_json(attempt_path)
    artifacts = client.get_json(artifacts_path)
    if (not isinstance(attempt, Mapping) or
            attempt.get("id") != publisher_run_id or
            attempt.get("run_attempt") != publisher_run_attempt or
            attempt.get("head_sha") != expected_publisher_sha or
            attempt.get("status") != "completed" or
            attempt.get("conclusion") != "success" or
            attempt.get("event") != "workflow_dispatch" or
            attempt.get("head_branch") != "main" or
            attempt.get("name") != RELEASE_WORKFLOW_NAME or
            not _strict_main_workflow_path(attempt.get("path"),
                                           RELEASE_WORKFLOW, expected_publisher_sha)):
        _red("POST_ATTEMPT_IDENTITY_RED")
    r206 = _load_trusted_r206_module()
    try:
        attempt_start, attempt_finish = r206._attempt_window(
            attempt, sha=expected_publisher_sha,
            run_id=publisher_run_id, attempt_number=publisher_run_attempt,
            expected_branch="main", expected_path=RELEASE_WORKFLOW,
            expected_name=RELEASE_WORKFLOW_NAME,
            expected_event="workflow_dispatch",
        )
    except (r206.PostrunEvidenceRed, ValueError):
        _red("POST_ATTEMPT_WINDOW_RED")
    if (not isinstance(artifacts, Mapping) or artifacts.get("total_count") != 1 or
            not isinstance(artifacts.get("artifacts"), list) or
            len(artifacts["artifacts"]) != 1):
        _red("POST_ARTIFACT_AMBIGUOUS")
    listed = artifacts["artifacts"][0]
    if not isinstance(listed, Mapping):
        _red("POST_ARTIFACT_AMBIGUOUS")
    name = listed.get("name")
    match = (re.fullmatch(
        rf"rc6-r251-release-{publisher_run_id}-{publisher_run_attempt}-([0-9a-f]{{40}})",
        name) if type(name) is str else None)
    if match is None or not _integer(listed.get("id"), minimum=1):
        _red("POST_ARTIFACT_AMBIGUOUS")
    try:
        created = r206._utc_timestamp(listed.get("created_at"), "POST_ARTIFACT_TIMESTAMP_RED")
    except (r206.PostrunEvidenceRed, ValueError):
        _red("POST_ARTIFACT_TIMESTAMP_RED")
    if not attempt_start < created <= attempt_finish:
        _red("POST_ARTIFACT_ATTEMPT_REPLAY")
    candidate_sha = match.group(1)
    artifact_id = listed["id"]
    artifact_path = f"{prefix}/actions/artifacts/{artifact_id}"
    artifact = client.get_json(artifact_path)
    pr_path = f"{prefix}/pulls/{RELEASE_PR}"
    commit_path = f"{prefix}/git/commits/{candidate_sha}"
    pr = client.get_json(pr_path)
    commit = client.get_json(commit_path)
    if (not isinstance(pr.get("head"), Mapping) or
            pr.get("number") != RELEASE_PR or pr.get("state") != "open" or
            pr["head"].get("sha") != candidate_sha or
            pr["head"].get("ref") != RELEASE_CANDIDATE_BRANCH or
            not isinstance(pr.get("base"), Mapping) or
            pr["base"].get("ref") != RELEASE_PRODUCT_BRANCH or
            not isinstance(commit.get("tree"), Mapping) or
            commit.get("sha") != candidate_sha or
            type(commit["tree"].get("sha")) is not str or
            HEX40.fullmatch(commit["tree"]["sha"]) is None):
        _red("POST_CANDIDATE_STALE")
    digest = _digest(artifact.get("digest"), require_prefix=True)
    if digest is None:
        _red("POST_ARTIFACT_DIGEST_MISSING")
    expected = {
        "artifact_receipt": artifact, "run_receipt": run,
        "expected_candidate_sha": candidate_sha,
        "expected_tree_sha": commit["tree"]["sha"],
        "expected_run_id": publisher_run_id,
        "expected_run_attempt": publisher_run_attempt,
        "expected_artifact_id": artifact_id, "expected_digest": digest,
        "expected_workflow_path": RELEASE_WORKFLOW,
        "expected_publisher_sha": expected_publisher_sha,
    }
    if artifact != listed:
        _red("POST_ARTIFACT_REST_MISMATCH")
    with tempfile.TemporaryDirectory(prefix="rc6-r251-e-") as directory:
        archive = Path(directory) / "release.zip"
        download_release_zip(client, artifact_id, archive)
        verified = verify_artifact_bytes(archive, **expected)
    for path, first in ((run_path, run), (attempt_path, attempt),
                        (artifacts_path, artifacts), (artifact_path, artifact),
                        (pr_path, pr), (commit_path, commit)):
        if client.get_json(path) != first:
            _red("POST_REST_RACE")
    return {
        "schema_version": 1,
        "status": "RELEASE_BYTES_VERIFIED_POSTCOMPLETION",
        "repository": REPOSITORY,
        "candidate_sha": candidate_sha,
        "candidate_tree_sha": commit["tree"]["sha"],
        "publisher_main_sha": expected_publisher_sha,
        "publisher_workflow_path": RELEASE_WORKFLOW,
        "publisher_run_id": publisher_run_id,
        "publisher_run_attempt": publisher_run_attempt,
        "release_artifact_id": artifact_id,
        "release_artifact_name": name,
        "release_artifact_digest": "sha256:" + digest,
        "release_artifact_size_bytes": artifact["size_in_bytes"],
        "file_count": verified["file_count"],
        "total_size_bytes": verified["total_size_bytes"],
        "release_manifest_verified": True,
        "r144_release_go": False,
        "runtime_verified": False,
        "real_orders_physical_verified": False,
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


def _release_command(argv: list[str]) -> int:
    """Narrow, stdlib-only CLI for trusted-main D/E orchestration."""
    command = argv[0]
    parser = argparse.ArgumentParser(description=command)
    if command == "release-preflight":
        parser.add_argument("--candidate-sha", required=True)
        parser.add_argument("--candidate-tree-sha", required=True)
        parser.add_argument("--trusted-main-sha", required=True)
        parser.add_argument("--owner-comment-id", type=int, required=True)
        parser.add_argument("--r206-publisher-run-id", type=int, required=True)
        parser.add_argument("--r206-publisher-attempt", type=int, required=True)
        parser.add_argument("--r206-audit-run-id", type=int, required=True)
        parser.add_argument("--r206-audit-attempt", type=int, required=True)
    elif command == "seal-release":
        parser.add_argument("--staging-root", type=Path, required=True)
        parser.add_argument("--candidate-sha", required=True)
        parser.add_argument("--candidate-tree-sha", required=True)
        parser.add_argument("--trusted-main-sha", required=True)
        parser.add_argument("--run-id", type=int, required=True)
        parser.add_argument("--run-attempt", type=int, required=True)
    elif command == "native-proof":
        parser.add_argument("--native-tar", type=Path, required=True)
        parser.add_argument("--private-junit-root", type=Path, required=True)
        parser.add_argument("--candidate-sha", required=True)
        parser.add_argument("--candidate-tree-sha", required=True)
        parser.add_argument("--trusted-main-sha", required=True)
        parser.add_argument("--image-id", required=True)
        parser.add_argument("--run-id", type=int, required=True)
        parser.add_argument("--run-attempt", type=int, required=True)
        parser.add_argument("--source-cid", required=True)
        parser.add_argument("--collector-cid", required=True)
        parser.add_argument("--receipt-out", type=Path, required=True)
    elif command == "release-postcompletion":
        parser.add_argument("--event", type=Path, required=True)
        parser.add_argument("--publisher-run-id", type=int, required=True)
        parser.add_argument("--publisher-run-attempt", type=int, required=True)
        parser.add_argument("--expected-publisher-sha", required=True)
        parser.add_argument("--receipt-out", type=Path, required=True)
    else:
        _red("COMMAND_INVALID")
    args = parser.parse_args(argv[1:])
    try:
        if command == "release-preflight":
            r206 = _load_trusted_r206_module()
            client = r206.GitHubReadOnlyREST(os.environ.get("GITHUB_TOKEN", ""))
            result = authorize_release_source(
                client, candidate_sha=args.candidate_sha,
                candidate_tree_sha=args.candidate_tree_sha,
                trusted_main_sha=args.trusted_main_sha,
                owner_comment_id=args.owner_comment_id,
                r206_publisher_run_id=args.r206_publisher_run_id,
                r206_publisher_attempt=args.r206_publisher_attempt,
                r206_audit_run_id=args.r206_audit_run_id,
                r206_audit_attempt=args.r206_audit_attempt,
            )
        elif command == "seal-release":
            result = seal_release_directory(
                args.staging_root, candidate_sha=args.candidate_sha,
                candidate_tree_sha=args.candidate_tree_sha,
                publisher_main_sha=args.trusted_main_sha,
                run_id=args.run_id, run_attempt=args.run_attempt,
            )
        elif command == "native-proof":
            result = build_release_native_proof(
                args.native_tar, private_junit_root=args.private_junit_root,
                candidate_sha=args.candidate_sha,
                candidate_tree_sha=args.candidate_tree_sha,
                trusted_main_sha=args.trusted_main_sha,
                image_id=args.image_id, run_id=args.run_id,
                run_attempt=args.run_attempt,
                source_cid=args.source_cid, collector_cid=args.collector_cid,
            )
            raw = (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode()
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            with os.fdopen(os.open(args.receipt_out, flags, 0o600), "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        else:
            r206 = _load_trusted_r206_module()
            client = r206.GitHubReadOnlyREST(os.environ.get("GITHUB_TOKEN", ""))
            result = audit_release_postcompletion(
                client, event=_json_receipt(args.event),
                publisher_run_id=args.publisher_run_id,
                publisher_run_attempt=args.publisher_run_attempt,
                expected_publisher_sha=args.expected_publisher_sha,
            )
            raw = (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode()
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            with os.fdopen(os.open(args.receipt_out, flags, 0o600), "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        if command == "native-proof":
            print("RC6_R251_SAME_IMAGE_NATIVE=VALIDATED|private_raw_uploaded=NO")
        else:
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        print("RC6_R251_RELEASE=OFFHOST_EVIDENCE_ONLY|R144=HOLD|release_go=NO")
        return 0
    except ArtifactByteEvidenceRed as exc:
        print(f"RC6_R251_RELEASE=HOLD79|reason={exc}|release_go=NO", file=sys.stderr)
        return 79
    except Exception:
        # Fail closed without disclosing exception payloads, URLs or tokens.
        print("RC6_R251_RELEASE=HOLD79|reason=INTERNAL_OR_REST_RED|release_go=NO",
              file=sys.stderr)
        return 79


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in {"release-preflight", "seal-release", "release-postcompletion", "native-proof"}:
        return _release_command(argv)
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
    parser.add_argument("--expected-publisher-sha")
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
            expected_publisher_sha=args.expected_publisher_sha,
        )
    except ArtifactByteEvidenceRed as exc:
        print(f"RC6_ARTIFACT_BYTE_GATE=RED|reason={exc}|automatic_go=NO", file=sys.stderr)
        return 79
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
