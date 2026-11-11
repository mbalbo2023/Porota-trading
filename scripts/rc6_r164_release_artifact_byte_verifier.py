#!/usr/bin/env python3
"""R164 OFFLINE release ZIP byte-contract verifier, not a release authority.

The **caller**, not this module, must authenticate GitHub REST JSON and pin an
independently reviewed exact receipt. No HTTP, GITHUB_TOKEN, Docker, SSH,
deployment, or HMAC signing key is used. ``expected_receipt_sha256`` binds the
canonical JSON passed to this function but is NOT a digital signature: both
the receipt and its pin must arrive independently from a trusted-main caller.

Inputs are the REST-downloaded ZIP, REST artifact/run/PR receipts, and an
external expected receipt with exactly: schema_version=1, repository,
pr_number, base_ref, head_ref, candidate_sha, candidate_tree_sha, run_id,
run_attempt, artifact_id, artifact_name, artifact_digest (REST sha256:HEX64),
image_id, image_tar_sha256, and ordered file_paths. The v1 ZIP must contain
``porota-artifact-byte-manifest-v1.json`` plus every exactly declared payload;
mandatory release payloads include the frozen image, bundle and native proof.

This module reuses the R163 ZIP byte/manifest/REST primitives and the R163
native 13-file USTAR / exact UID1000 CID-volume proof validators. It never
confuses the **PROBE** artifact with the canonical build-once **RELEASE** ZIP.
The existing R144/HOLD79, COLD2 host/PAPER gates and release unlock are all
outside its scope, unverified, and remain closed. No script read from a PR is
trusted until separately reviewed/pinned under the default-main bootstrap.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import tarfile
import tempfile
import types
from typing import Any, Mapping
import xml.etree.ElementTree as ET
import zipfile

PINNED_DEPENDENCIES = {
    "rc6_r163_artifact_byte_verifier.py":
        "2018ddb6a5c569b27787ed7018bc9e21dca86e29bc2c5489cd5cca267afebfbc",
    "rc6_r163_native_volume_proof.py":
        "fc744c327a1bca006880629f3904dc972e4afddb6b3df94e396da2eb39d01d25",
}


def _load_pinned_dependency(name: str, *, directory: Path | None = None) -> types.ModuleType:
    """Compile only immutable expected bytes; never consult ambient sys.path.

    The source hashes are a reviewed-code *compatibility pin*, not proof that
    this as-yet-untrusted candidate module is itself a trusted bootstrap.
    """
    digest = PINNED_DEPENDENCIES[name]
    directory = directory or Path(__file__).resolve().parent
    root_fd = -1
    try:
        root_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY |
                          getattr(os, "O_NOFOLLOW", 0) |
                          getattr(os, "O_CLOEXEC", 0))
        with os.fdopen(os.open(name, os.O_RDONLY |
                               getattr(os, "O_NOFOLLOW", 0) |
                               getattr(os, "O_CLOEXEC", 0), dir_fd=root_fd), "rb") as fd:
            before = os.fstat(fd.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 128 * 1024:
                raise ImportError("R164_DEPENDENCY_UNSAFE")
            source = fd.read(128 * 1024 + 1)
            after = os.fstat(fd.fileno())
            named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            if (len(source) != before.st_size or
                    (before.st_dev, before.st_ino, before.st_size,
                     before.st_mtime_ns, before.st_ctime_ns) !=
                    (after.st_dev, after.st_ino, after.st_size,
                     after.st_mtime_ns, after.st_ctime_ns) or
                    (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)):
                raise ImportError("R164_DEPENDENCY_SWAPPED")
        if hashlib.sha256(source).hexdigest() != digest:
            raise ImportError("R164_DEPENDENCY_PIN_MISMATCH")
        module = types.ModuleType("rc6_r164_pinned_" + name[:-3])
        module.__file__ = str(directory / name)
        exec(compile(source, module.__file__, "exec"), module.__dict__)
        return module
    except OSError as exc:
        raise ImportError("R164_DEPENDENCY_IO_RED") from exc
    finally:
        if root_fd >= 0:
            os.close(root_fd)


byte_gate = _load_pinned_dependency("rc6_r163_artifact_byte_verifier.py")
native = _load_pinned_dependency("rc6_r163_native_volume_proof.py")


REPOSITORY = "mbalbo2023/Porota-trading"
PRODUCT_REF = "deploy/rc6-pr69-isolated-20260915"
PR_NUMBER = 1087
PR_HEAD_REF = "integration/rc6-a1115-r87-unified-paper-candidate-20261110"
WORKFLOW_PATH = ".github/workflows/porota-predeploy-v2.yml"
BYTE_MANIFEST = byte_gate.MANIFEST_NAME
REQUIRED_FILES = frozenset({
    "porota-frozen-candidate.json",
    "porota-predeploy-image.tar.gz",
    "porota-deploy-bundle-v2.tgz",
    "porota-deploy-bundle-v2-manifest.json",
    "porota-governed-tests.xml",
    "native.tar", "native-manifest.json", "custody.json",
    "source-inspect.json", "collector-inspect.json",
})
EXPECTED_KEYS = frozenset({
    "schema_version", "repository", "pr_number", "base_ref", "head_ref",
    "candidate_sha", "candidate_tree_sha", "run_id", "run_attempt",
    "artifact_id", "artifact_name", "artifact_digest", "image_id",
    "image_tar_sha256", "file_paths",
})
FROZEN_KEYS = frozenset({
    "schema_version", "candidate_sha", "candidate_tree_sha", "image_id",
    "image_tar_sha256", "image_config_sha256", "image_size_bytes",
    "paper_mode_required", "real_orders_sent_required",
    "real_order_capability_required", "build_once",
})
BUNDLE_KEYS = frozenset({
    "schema_version", "status", "bundle_sha256", "file_count", "files",
})
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
MAX_RECEIPT_BYTES = 512 * 1024
MAX_NATIVE_BYTES = 32 * 1024 * 1024
MAX_SMALL_MEMBER = 4 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024


class ReleaseArtifactEvidenceRed(ValueError):
    """A fixed fail-closed reason. Never echo attacker-controlled metadata."""


def _red(reason: str) -> None:
    raise ReleaseArtifactEvidenceRed(reason)


def _int(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _json(raw: bytes) -> dict[str, Any]:
    try:
        obj = byte_gate._parse_json(raw)
    except byte_gate.ArtifactByteEvidenceRed:
        _red("JSON_INVALID")
    if type(obj) is not dict:
        _red("JSON_OBJECT_REQUIRED")
    return obj


def _canonical_receipt(receipt: Mapping[str, Any]) -> bytes:
    try:
        return (json.dumps(receipt, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
    except (TypeError, ValueError, RecursionError, UnicodeError):
        _red("EXTERNAL_RECEIPT_INVALID")


def _check_expected(expected: Mapping[str, Any], pin: str) -> str:
    if type(expected) is not dict or set(expected) != EXPECTED_KEYS:
        _red("EXTERNAL_RECEIPT_INVALID")
    if type(pin) is not str or HEX64.fullmatch(pin) is None:
        _red("EXTERNAL_PIN_INVALID")
    if hashlib.sha256(_canonical_receipt(expected)).hexdigest() != pin:
        _red("EXTERNAL_PIN_MISMATCH")
    if (expected["schema_version"] != 1 or
            not _int(expected["schema_version"], 1) or
            expected["repository"] != REPOSITORY or
            expected["pr_number"] != PR_NUMBER or
            not _int(expected["pr_number"], 1) or
            expected["base_ref"] != PRODUCT_REF or
            expected["head_ref"] != PR_HEAD_REF):
        _red("EXTERNAL_PROVENANCE_INVALID")
    sha, tree = expected["candidate_sha"], expected["candidate_tree_sha"]
    if (type(sha) is not str or byte_gate.HEX40.fullmatch(sha) is None or
            type(tree) is not str or byte_gate.HEX40.fullmatch(tree) is None or
            not _int(expected["run_id"], 1) or
            not _int(expected["run_attempt"], 1) or
            not _int(expected["artifact_id"], 1) or
            expected["artifact_name"] != "porota-predeploy-v2-" + sha or
            type(expected["artifact_digest"]) is not str or
            not IMAGE_ID.fullmatch(expected["artifact_digest"]) or
            type(expected["image_id"]) is not str or
            not IMAGE_ID.fullmatch(expected["image_id"]) or
            type(expected["image_tar_sha256"]) is not str or
            not HEX64.fullmatch(expected["image_tar_sha256"])):
        _red("EXTERNAL_IDENTITY_INVALID")
    paths = expected["file_paths"]
    if (type(paths) is not list or not 1 <= len(paths) < byte_gate.MAX_MEMBERS or
            any(not byte_gate._safe_path(p) or p == BYTE_MANIFEST for p in paths) or
            paths != sorted(set(paths)) or
            not REQUIRED_FILES.issubset(paths)):
        _red("EXTERNAL_FILE_SET_INVALID")
    return expected["artifact_digest"][7:]


def _offline_test_hmac(receipt: Mapping[str, Any], signature: str | None,
                       key: bytes | None) -> bool:
    """Optional *test-only* MAC; never authenticate GitHub or grant release GO.

    No key is configured or consumed by the CLI. Supplying a fake test key
    proves just the local MAC math, not the issuer or chain of custody.
    """
    if signature is None and key is None:
        return False
    if (type(signature) is not str or
            re.fullmatch(r"hmac-sha256:[0-9a-f]{64}", signature) is None or
            type(key) is not bytes or not 16 <= len(key) <= 4096):
        _red("OFFLINE_HMAC_INVALID")
    actual = hmac.new(key, _canonical_receipt(receipt), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(actual, signature[12:]):
        _red("OFFLINE_HMAC_MISMATCH")
    return True


def _check_pr_and_run(run: Mapping[str, Any], pr: Mapping[str, Any],
                      expected: Mapping[str, Any]) -> None:
    if (type(pr) is not dict or
            type(pr.get("number")) is not int or pr["number"] != PR_NUMBER or
            pr.get("draft") is not False or pr.get("state") != "open"):
        _red("PR_NOT_RELEASE_ELIGIBLE")
    for side, values in (
        ("head", {"sha": expected["candidate_sha"], "ref": PR_HEAD_REF}),
        ("base", {"ref": PRODUCT_REF}),
    ):
        element = pr.get(side)
        if type(element) is not dict or any(element.get(k) != v for k, v in values.items()):
            _red("PR_REF_SHA_MISMATCH")
        repository = element.get("repo")
        if type(repository) is not dict or repository.get("full_name") != REPOSITORY:
            _red("PR_REPOSITORY_MISMATCH")
    if run.get("head_branch") != PR_HEAD_REF:
        _red("RUN_HEAD_REF_MISMATCH")
    associated = run.get("pull_requests")
    if (type(associated) is not list or len(associated) != 1 or
            type(associated[0]) is not dict or
            associated[0].get("number") != PR_NUMBER or
            type(associated[0].get("head")) is not dict or
            associated[0]["head"].get("sha") != expected["candidate_sha"]):
        _red("RUN_PR_ASSOCIATION_MISMATCH")


def _small(zf: zipfile.ZipFile, path: str, limit: int = MAX_SMALL_MEMBER) -> bytes:
    info = zf.getinfo(path)
    if info.file_size > limit:
        _red("RELEASE_MEMBER_OVERSIZED")
    with zf.open(info) as stream:
        raw = stream.read(limit + 1)
    if len(raw) != info.file_size or len(raw) > limit:
        _red("RELEASE_MEMBER_OVERSIZED")
    return raw


def _member_sha(zf: zipfile.ZipFile, path: str) -> str:
    digest = hashlib.sha256()
    with zf.open(path) as stream:
        while block := stream.read(CHUNK_BYTES):
            digest.update(block)
    return digest.hexdigest()


def _check_bundle(zf: zipfile.ZipFile) -> None:
    manifest = _json(_small(zf, "porota-deploy-bundle-v2-manifest.json"))
    if (set(manifest) != BUNDLE_KEYS or
            not _int(manifest["schema_version"], 1) or manifest["schema_version"] != 1 or
            manifest["status"] != "GREEN" or
            type(manifest["bundle_sha256"]) is not str or
            not HEX64.fullmatch(manifest["bundle_sha256"]) or
            _member_sha(zf, "porota-deploy-bundle-v2.tgz") != manifest["bundle_sha256"]):
        _red("BUNDLE_MANIFEST_MISMATCH")
    rows = manifest["files"]
    if (type(rows) is not list or not 0 < len(rows) <= 50000 or
            not _int(manifest["file_count"], 1) or
            manifest["file_count"] != len(rows)):
        _red("BUNDLE_MANIFEST_FILES_INVALID")
    previous = ""
    for item in rows:
        if (type(item) is not dict or set(item) != {"path", "sha256", "bytes"} or
                not byte_gate._safe_path(item["path"]) or
                item["path"] <= previous or
                type(item["sha256"]) is not str or
                not HEX64.fullmatch(item["sha256"]) or
                not _int(item["bytes"])):
            _red("BUNDLE_MANIFEST_FILES_INVALID")
        previous = item["path"]


def _check_frozen(zf: zipfile.ZipFile, expected: Mapping[str, Any]) -> None:
    frozen = _json(_small(zf, "porota-frozen-candidate.json"))
    if (set(frozen) != FROZEN_KEYS or
            not _int(frozen["schema_version"], 1) or frozen["schema_version"] != 1 or
            frozen["candidate_sha"] != expected["candidate_sha"] or
            frozen["candidate_tree_sha"] != expected["candidate_tree_sha"] or
            frozen["image_id"] != expected["image_id"] or
            frozen["image_tar_sha256"] != expected["image_tar_sha256"] or
            type(frozen["image_config_sha256"]) is not str or
            not HEX64.fullmatch(frozen["image_config_sha256"]) or
            not _int(frozen["image_size_bytes"], 1) or
            frozen["paper_mode_required"] != "PRODUCTION_PAPER" or
            not _int(frozen["real_orders_sent_required"]) or
            frozen["real_orders_sent_required"] != 0 or
            frozen["real_order_capability_required"] != "BLOCKED" or
            frozen["build_once"] is not True):
        _red("FROZEN_IMAGE_CONTRACT_MISMATCH")
    if _member_sha(zf, "porota-predeploy-image.tar.gz") != expected["image_tar_sha256"]:
        _red("IMAGE_BYTES_DIGEST_MISMATCH")


def _check_junit_xml(payload: bytes, reason: str) -> None:
    """A bounded JUnit result must include actual cases and no red/skipped ones."""
    if (not payload or len(payload) > native.MAX_FILE_BYTES or
            payload.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"))):
        _red(reason)
    try:
        # XML decoders can decode UTF-16 and expand an entity whose declaration
        # our old raw ASCII byte test did not see. Reject all DTD, entity,
        # comment/CDATA markup and non-UTF8 encodings before invoking expat.
        text = payload.decode("utf-8", "strict")
    except UnicodeError:
        _red(reason)
    if "\x00" in text or "\ufeff" in text or "<!" in text:
        _red(reason)
    if text.startswith("<?xml"):
        declaration = text.split("?>", 1)[0]
        encoding = re.search(r"\bencoding\s*=\s*(['\"])([^'\"]+)\1", declaration,
                             flags=re.IGNORECASE)
        if encoding and encoding.group(2).lower() != "utf-8":
            _red(reason)
    try:
        root = ET.fromstring(payload)
    except (ET.ParseError, ValueError, RecursionError):
        _red(reason)
    case_attributes = frozenset({
        "name", "classname", "time", "file", "line", "assertions",
        "status", "outcome", "result",
    })
    suite_attributes = frozenset({
        "name", "tests", "errors", "failures", "skipped", "time",
        "timestamp", "hostname", "id", "package", "file",
    })
    allowed_attributes = {
        "testsuite": suite_attributes, "testsuites": suite_attributes,
        "testcase": case_attributes,
        "properties": frozenset(), "property": frozenset({"name", "value"}),
        "system-out": frozenset(), "system-err": frozenset(),
    }
    for node in root.iter():
        if (node.tag not in allowed_attributes or
                not set(node.attrib).issubset(allowed_attributes[node.tag]) or
                (node.tag == "property" and any(
                    word in node.attrib.get("name", "").lower()
                    for word in ("status", "outcome", "result", "state",
                                 "xfail", "skip", "fail", "error")))):
            _red(reason)

    def counters(element: ET.Element) -> tuple[int, int, int, int]:
        values = []
        for field in ("tests", "failures", "errors", "skipped"):
            value = element.attrib.get(field)
            if (not isinstance(value, str) or len(value) > 12 or
                    re.fullmatch(r"0|[1-9][0-9]*", value) is None):
                _red(reason)
            values.append(int(value))
        return tuple(values)

    if root.tag == "testsuite":
        suites = [root]
    elif root.tag == "testsuites":
        suites = list(root)
    else:
        _red(reason)
    if not suites or any(suite.tag != "testsuite" for suite in suites):
        _red(reason)
    aggregate = [0, 0, 0, 0]
    for suite in suites:
        tests, failures, errors, skipped = counters(suite)
        for index, value in enumerate((tests, failures, errors, skipped)):
            aggregate[index] += value
        cases = list(suite.iter("testcase"))
        if (tests == 0 or tests != len(cases) or
                failures != 0 or errors != 0 or skipped != 0 or
                any(case.attrib.get(key, "passed").lower() not in
                    ("passed", "success", "run")
                    for case in cases for key in ("status", "outcome", "result")) or
                any(node.tag in ("failure", "error", "skipped") or
                    node is not suite and node.tag == "testsuite" or
                    isinstance(node.tag, str) and node.tag.startswith("{") or
                    "xfail" in str(node.attrib).lower()
                    for node in suite.iter())):
            _red(reason)
    if root.tag == "testsuites":
        fields = ("tests", "failures", "errors", "skipped")
        if any(field in root.attrib for field in fields):
            if counters(root) != tuple(aggregate):
                _red(reason)


def _check_native_junit(raw: bytes) -> None:
    """Check XML semantics after independent USTAR13 path/byte validation."""
    verified: set[str] = set()
    expected_xml = {name for name in native.NATIVE_EVIDENCE_PATHS
                    if name.endswith(".xml")}
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
            for member in tar:
                path = member.name.removeprefix("./")
                if path not in expected_xml:
                    continue
                stream = tar.extractfile(member)
                if stream is None:
                    _red("NATIVE_JUNIT_RED")
                _check_junit_xml(stream.read(native.MAX_FILE_BYTES + 1),
                                 "NATIVE_JUNIT_RED")
                verified.add(path)
    except (tarfile.TarError, OSError, EOFError, ValueError):
        _red("NATIVE_JUNIT_RED")
    if verified != expected_xml or len(verified) != 9:
        _red("NATIVE_JUNIT_RED")


def _check_native(zf: zipfile.ZipFile, expected: Mapping[str, Any]) -> int:
    try:
        custody = _json(_small(zf, "custody.json"))
        native._validate_custody_receipt(custody)
        if any(custody[key] != value for key, value in (
            ("candidate_sha", expected["candidate_sha"]),
            ("candidate_tree_sha", expected["candidate_tree_sha"]),
            ("run_id", str(expected["run_id"])),
            ("run_attempt", str(expected["run_attempt"])),
            ("image_id", expected["image_id"]),
            ("role", "bundle"),
        )):
            _red("NATIVE_IDENTITY_MISMATCH")
        proof = native.verify_volume_chain(
            producer_inspect_json=_small(zf, "source-inspect.json"),
            collector_inspect_json=_small(zf, "collector-inspect.json"),
            source_cid=custody["source_cid"], collector_cid=custody["collector_cid"],
            candidate_sha=expected["candidate_sha"],
            candidate_tree_sha=expected["candidate_tree_sha"],
            image_id=expected["image_id"], run_id=str(expected["run_id"]),
            run_attempt=str(expected["run_attempt"]), role="bundle",
        )
        if not native._strict_equal(proof, custody):
            _red("NATIVE_CUSTODY_MISMATCH")
        tar_bytes = _small(zf, "native.tar", MAX_NATIVE_BYTES)
        evidence = native.verify_collector_manifest(
            tar_bytes,
            _small(zf, "native-manifest.json"),
            expected_candidate_sha=expected["candidate_sha"],
            expected_candidate_tree_sha=expected["candidate_tree_sha"],
            expected_run_id=str(expected["run_id"]),
            expected_run_attempt=str(expected["run_attempt"]),
            expected_source_cid=custody["source_cid"],
            expected_collector_cid=custody["collector_cid"],
            expected_volume_name=custody["volume_name"],
            expected_image_id=expected["image_id"], expected_role="bundle",
        )
    except native.VolumeProofHold:
        _red("NATIVE_PROOF_RED")
    if evidence["file_count"] != 13:
        _red("NATIVE_FILE_COUNT_MISMATCH")
    _check_native_junit(tar_bytes)
    return evidence["file_count"]


def verify_release_artifact_bytes(
    archive: Path, *, expected: Mapping[str, Any],
    expected_receipt_sha256: str,
    artifact_receipt: Mapping[str, Any], run_receipt: Mapping[str, Any],
    pr_receipt: Mapping[str, Any],
    offline_test_hmac: str | None = None, offline_test_key: bytes | None = None,
) -> dict[str, Any]:
    """Match bytes + REST identities, while **never** declaring release GO.

    All receipts are untrusted function inputs. A separate default-main pinned
    bootstrap must obtain them from authenticated GitHub REST and independently
    pin the expected receipt; this function never claims to be that bootstrap.
    """
    digest = _check_expected(expected, expected_receipt_sha256)
    offline_hmac = _offline_test_hmac(expected, offline_test_hmac, offline_test_key)
    try:
        byte_gate._check_receipts(
            artifact_receipt, run_receipt, sha=expected["candidate_sha"],
            run_id=expected["run_id"], attempt=expected["run_attempt"],
            artifact_id=expected["artifact_id"], digest=digest,
            workflow_path=WORKFLOW_PATH,
        )
    except byte_gate.ArtifactByteEvidenceRed as exc:
        _red(str(exc))
    _check_pr_and_run(run_receipt, pr_receipt, expected)
    try:
        with tempfile.TemporaryFile(mode="w+b") as snapshot:
            actual_digest, size = byte_gate._snapshot_archive(
                Path(archive), snapshot, limit=byte_gate.MAX_ARCHIVE_BYTES)
            if actual_digest != digest:
                _red("ZIP_DIGEST_MISMATCH")
            if (not _int(artifact_receipt.get("size_in_bytes"), 1) or
                    artifact_receipt["size_in_bytes"] != size):
                _red("ARTIFACT_SIZE_MISMATCH")
            try:
                files, _ = byte_gate._verify_zip(
                    snapshot, sha=expected["candidate_sha"],
                    tree=expected["candidate_tree_sha"],
                    run_id=expected["run_id"],
                    attempt=expected["run_attempt"],
                    name=expected["artifact_name"],
                )
            except byte_gate.ArtifactByteEvidenceRed as exc:
                _red(str(exc))
            with zipfile.ZipFile(snapshot, "r") as zf:
                manifest = _json(_small(zf, BYTE_MANIFEST, byte_gate.MAX_MANIFEST_BYTES))
                paths = [row["path"] for row in manifest["files"]]
                if paths != expected["file_paths"] or files != len(paths):
                    _red("RELEASE_FILE_SET_MISMATCH")
                _check_frozen(zf, expected)
                _check_bundle(zf)
                _check_junit_xml(_small(zf, "porota-governed-tests.xml"),
                                 "GOVERNED_JUNIT_RED")
                native_count = _check_native(zf, expected)
            snapshot.seek(0)
            h = hashlib.sha256()
            while block := snapshot.read(CHUNK_BYTES):
                h.update(block)
            if h.hexdigest() != digest:
                _red("SNAPSHOT_CHANGED")
    except byte_gate.ArtifactByteEvidenceRed as exc:
        _red(str(exc))
    except (OSError, zipfile.BadZipFile, EOFError, OverflowError, RuntimeError):
        _red("RELEASE_ARCHIVE_IO_RED")
    return {
        "schema_version": 1,
        "status": "RELEASE_BYTES_MATCH_OFFLINE_ONLY",
        "candidate_sha": expected["candidate_sha"],
        "candidate_tree_sha": expected["candidate_tree_sha"],
        "run_id": expected["run_id"], "run_attempt": expected["run_attempt"],
        "artifact_id": expected["artifact_id"],
        "artifact_digest": expected["artifact_digest"],
        "image_id": expected["image_id"],
        "image_tar_sha256": expected["image_tar_sha256"],
        "release_file_count": files, "native_file_count": native_count,
        "receipt_pin_match": True,
        "offline_test_hmac_match": offline_hmac,
        "trusted_bootstrap_verified": False,
        "artifact_contents_verified_by_this_guard": True,
        "bundle_inner_tar_verified": False,
        "docker_image_load_verified": False,
        "fresh_rest_race_check_verified": False,
        "host_gate_verified": False,
        "owner_go_verified": False,
        "release_go": False, "automatic_go": False, "runtime_verified": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--expected-receipt", type=Path, required=True)
    parser.add_argument("--expected-receipt-sha256", required=True)
    parser.add_argument("--artifact-rest", type=Path, required=True)
    parser.add_argument("--run-rest", type=Path, required=True)
    parser.add_argument("--pr-rest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = verify_release_artifact_bytes(
            args.archive,
            expected=byte_gate._json_receipt(args.expected_receipt),
            expected_receipt_sha256=args.expected_receipt_sha256,
            artifact_receipt=byte_gate._json_receipt(args.artifact_rest),
            run_receipt=byte_gate._json_receipt(args.run_rest),
            pr_receipt=byte_gate._json_receipt(args.pr_rest),
        )
    except (ReleaseArtifactEvidenceRed, byte_gate.ArtifactByteEvidenceRed) as exc:
        print(f"RC6_R164_RELEASE_BYTES=RED|reason={exc}|release_go=NO", file=sys.stderr)
        return 79
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
