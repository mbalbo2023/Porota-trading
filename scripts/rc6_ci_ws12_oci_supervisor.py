"""Trusted-main OCI QA supervisor primitives, with release authority disabled.

Executes on an ephemeral GitHub-hosted runner only, never the shared Droplet.
Docker is constrained to exact owned container identity; no prune, broad list,
global Docker service changes or candidate code execution in runner Python.

This module cannot attest custody by itself.  The signed GitHub Actions run,
reviewed QA image digest, fresh physical policy inspection and complete safe
teardown are prerequisites before a release certificate can be considered.
"""
from __future__ import annotations

import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import stat
import tarfile
from xml.etree import ElementTree as ET

from scripts.rc6_ci_ws12_trusted_receipt import EvidenceRejected, review_evidence


_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_IMAGE = re.compile(r"ghcr[.]io/mbalbo2023/[a-z0-9._-]+@sha256:[0-9a-f]{64}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class OCIHOLD(ValueError):
    """OCI or evidence input failed a fail-closed protected-main gate."""


def _mount_source(raw: str) -> Path:
    """Approve only existing canonical directory paths with no symlink parent."""
    if (not isinstance(raw, str) or not raw.startswith("/") or
            any(ch in raw for ch in ",\n\r\x00") or
            any(part in ("", ".", "..") for part in raw.split("/")[1:])):
        raise OCIHOLD("MOUNT_PATH_UNVERIFIED")
    path = Path(raw)
    current = Path("/")
    try:
        for part in path.parts[1:]:
            current = current / part
            mode = current.lstat().st_mode
            if not stat.S_ISDIR(mode):
                raise OCIHOLD("MOUNT_TYPE_UNVERIFIED")
        if path.resolve(strict=True) != path:
            raise OCIHOLD("MOUNT_RESOLVE_UNVERIFIED")
    except (OSError, RuntimeError):
        raise OCIHOLD("MOUNT_MISSING") from None
    return path


def admitted_image(image: str, *, approved: frozenset[str], docker=None) -> bool:
    """Only a main-reviewed immutable QA image, never a supplied mutable tag.

    Docker is deliberately not consulted for an unapproved input.  This
    function does not itself prove image availability or build provenance.
    """
    return (isinstance(image, str) and _IMAGE.fullmatch(image) is not None and
            image in approved)


def container_args(*, candidate: str, control: str, image: str, sha: str,
                   tree: str, run_id: str, attempt: str, nonce: str,
                   approved: frozenset[str]) -> list[str]:
    """Construct an exact Docker create argv, no shell or host-writable mount."""
    if (not admitted_image(image, approved=approved) or
            any(_SHA40.fullmatch(value) is None for value in (sha, tree)) or
            _HEX64.fullmatch(nonce * 2) is None or
            not re.fullmatch(r"[1-9][0-9]{0,19}", run_id) or
            not re.fullmatch(r"[1-9][0-9]{0,19}", attempt)):
        raise OCIHOLD("SOURCE_OR_IMAGE_UNVERIFIED")
    candidate_path = _mount_source(candidate)
    control_path = _mount_source(control)
    if (candidate_path == control_path or
            candidate_path in control_path.parents or
            control_path in candidate_path.parents or
            os.stat(candidate_path).st_ino == os.stat(control_path).st_ino and
            os.stat(candidate_path).st_dev == os.stat(control_path).st_dev):
        raise OCIHOLD("MOUNT_PATH_UNVERIFIED")
    labels = ["--label", "rc6.qa.custody=" + nonce,
              "--label", "rc6.qa.candidate=" + sha]
    return ["create", "--pull", "never", *labels,
            "--user", "65532:65532", "--read-only", "--network", "none",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "256", "--memory", "1g", "--memory-swap", "1g",
            "--cpus", "2", "--ipc", "none", "--cgroupns", "private",
            "--log-driver", "none", "--restart", "no", "--no-healthcheck",
            "--tmpfs", "/work:rw,nosuid,nodev,noexec,size=512m,mode=1777",
            "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
            "--mount", "type=bind,src=" + candidate + ",dst=/src,readonly,bind-recursive=disabled",
            "--mount", "type=bind,src=" + control + ",dst=/control,readonly,bind-recursive=disabled",
            "--workdir", "/src", "--entrypoint", "/usr/bin/env", image,
            "-i", "PATH=/usr/local/bin:/usr/bin:/bin", "HOME=/work",
            "PYTHONDONTWRITEBYTECODE=1", "RC6_CANDIDATE_SHA=" + sha,
            "RC6_CANDIDATE_TREE_SHA=" + tree,
            "PYTHONPATH=/control", "python3", "-B",
            "/control/scripts/rc6_ci_ws12_oci_inner.py"]


def read_docker_cp_tar(tar_bytes: bytes, *, expected: str, max_bytes: int) -> bytes:
    """Read a single exact regular file from Docker cp; never extract paths."""
    if (not isinstance(tar_bytes, bytes) or type(max_bytes) is not int or
            not 1 <= max_bytes <= 64 * 1024 * 1024 or
            len(tar_bytes) > max_bytes + 1024 * 1024 or
            not isinstance(expected, str) or
            not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,70}", expected)):
        raise OCIHOLD("DOCKER_CP_UNVERIFIED")
    try:
        with tarfile.open(fileobj=BytesIO(tar_bytes), mode="r:") as archive:
            members = archive.getmembers()
            if len(members) != 1:
                raise OCIHOLD("DOCKER_CP_MEMBERSHIP")
            member = members[0]
            if (member.name not in (expected, "./" + expected) or
                    not member.isfile() or member.size > max_bytes or
                    member.pax_headers):
                raise OCIHOLD("DOCKER_CP_TYPE")
            data = archive.extractfile(member)
            if data is None:
                raise OCIHOLD("DOCKER_CP_MISSING")
            result = data.read(max_bytes + 1)
            if len(result) != member.size:
                raise OCIHOLD("DOCKER_CP_SIZE")
            return result
    except (tarfile.TarError, OSError, ValueError):
        raise OCIHOLD("DOCKER_CP_REJECTED") from None


def _json(raw: bytes) -> object:
    if not isinstance(raw, bytes) or len(raw) > 32 * 1024 * 1024:
        raise OCIHOLD("JSON_BOUNDS")
    try:
        return json.loads(raw)
    except (UnicodeError, ValueError):
        raise OCIHOLD("JSON_INVALID") from None


def _cases(raw: bytes, *, expected: int) -> list[dict[str, str]]:
    if (not isinstance(raw, bytes) or len(raw) > 16 * 1024 * 1024 or
            b"<!DOCTYPE" in raw or b"<!ENTITY" in raw):
        raise OCIHOLD("JUNIT_BOUNDS")
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, UnicodeError, ValueError):
        raise OCIHOLD("JUNIT_INVALID") from None
    if root.tag not in ("testsuite", "testsuites") or not 1 <= expected <= 100000:
        raise OCIHOLD("JUNIT_ROOT")
    suites = [root] if root.tag == "testsuite" else list(root)
    if not 1 <= len(suites) <= 32 or any(suite.tag != "testsuite" for suite in suites):
        raise OCIHOLD("JUNIT_SUITE_STRUCTURE")
    result = []
    for suite in suites:
        try:
            declared = {key: int(suite.attrib[key])
                        for key in ("tests", "failures", "errors", "skipped")}
        except (KeyError, ValueError):
            raise OCIHOLD("JUNIT_SUMMARY") from None
        if (any(value < 0 or value > expected for value in declared.values()) or
                any(suite.attrib[key] != str(value) for key, value in declared.items()) or
                any(declared[key] for key in ("failures", "errors", "skipped"))):
            raise OCIHOLD("JUNIT_SUMMARY")
        cases = suite.findall("./testcase")
        if declared["tests"] != len(cases) or len(result) + len(cases) > expected:
            raise OCIHOLD("JUNIT_SUMMARY_COUNT")
        for test in cases:
            properties = [item.get("value") for item in test.findall("./properties/property")
                          if item.get("name") == "rc6_nodeid_sha256"]
            if len(properties) != 1:
                raise OCIHOLD("JUNIT_PROPERTY")
            failures = [tag for tag in ("failure", "error", "skipped")
                        if test.find(tag) is not None]
            if failures:
                raise OCIHOLD("JUNIT_NOT_PASS")
            result.append({"nodeid_sha256": properties[0], "outcome": "passed"})
    if len(result) != expected:
        raise OCIHOLD("JUNIT_EXACT_COUNT")
    return result


def review_private_files(*, full_collected: bytes, subset_collected: bytes,
                         full_executed: bytes, junit: bytes, pytest_exit_code: int,
                         trusted_main_sha: str, candidate_sha: str, tree: str,
                         qa_image_digest: str, run_id: str, attempt: str,
                         expected_full: int = 8125, expected_subset: int = 8102) -> dict:
    """Trusted outer process independently checks JUnit against both scopes.

    Input raw bytes remain private on ephemeral runner; output is digest-only,
    still without physical or runtime release authority until a GHA signer.
    """
    if type(pytest_exit_code) is not int or pytest_exit_code != 0:
        raise OCIHOLD("PYTEST_EXIT")
    full = _json(full_collected)
    executed = _json(full_executed)
    if full != executed:
        raise OCIHOLD("EXECUTED_COLLECTION_DRIFT")
    raw = {"full_manifest": full, "subset_manifest": _json(subset_collected),
           "cases": _cases(junit, expected=expected_full),
           "pytest_exit_code": pytest_exit_code,
           "junit_sha256": hashlib.sha256(junit).hexdigest()}
    try:
        return review_evidence(
            raw, trusted_main_sha=trusted_main_sha, candidate_sha=candidate_sha,
            candidate_tree_sha=tree, qa_image_digest=qa_image_digest,
            run_id=run_id, attempt=attempt,
            expected_full=expected_full, expected_subset=expected_subset,
        )
    except EvidenceRejected:
        raise OCIHOLD("RECEIPT_REJECTED") from None
