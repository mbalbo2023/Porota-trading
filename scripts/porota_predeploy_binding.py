#!/usr/bin/env python3
"""Bind an approved merge tuple to GitHub execution and exact artifact bytes.

The merge commit trailers are the independent approval input. Artifact-authored
claims, a display name, and choosing the newest artifact cannot grant authority.
This module never calls SSH, Docker, a provider, or an order route.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import time
import types
import uuid
import zipfile

try:
    from scripts.porota_artifact_provenance import (
        canonical_bytes, decode_json, sha256_file, validate_bundle,
        validate_image_archive, verify_source_manifest,
    )
except ModuleNotFoundError:
    from porota_artifact_provenance import (
        canonical_bytes, decode_json, sha256_file, validate_bundle,
        validate_image_archive, verify_source_manifest,
    )

try:
    from scripts import rc6_archive_v3_image_smoke as codec_smoke
except ModuleNotFoundError:
    import rc6_archive_v3_image_smoke as codec_smoke

REPOSITORY = "mbalbo2023/Porota-trading"
WORKFLOW_PATH = ".github/workflows/porota-predeploy-v2.yml"
BASE_BRANCH = "deploy/rc6-pr69-isolated-20260915"
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
DENIED_RUNS = {37234866451, 37243206476, 37175265248}
DENIED_ARTIFACTS = {11315198085, 11317509383, 11293625514}
DENIED_HEADS = {
    "c27dfd963c4fe83465c0f2105347e974fbbe6356",
    "4a5fc6384260b31f9a07e791093add6ee83102a1",
    "caf9bc94b4a1f436ad01a84a9e9e9e7a4a9e9423",
}
TRAILERS = {
    "Porota-Predeploy-Workflow-ID": "workflow_id",
    "Porota-Predeploy-Run-ID": "run_id",
    "Porota-Predeploy-Run-Attempt": "run_attempt",
    "Porota-Predeploy-Artifact-ID": "artifact_id",
    "Porota-Predeploy-Artifact-Digest": "artifact_digest",
}
REQUIRED_FILES = (
    "porota-frozen-candidate.json", "porota-predeploy-image.tar.gz",
    "porota-deploy-bundle-v2.tgz", "porota-deploy-bundle-v2-manifest.json",
    "porota-host-manifest-v2.json", "porota-host-policy-v2.json",
    "porota-source-provenance.json",
    "porota-runtime-codec-smoke.json",
    "porota-final-input-provenance.json", "porota-governed-tests.json", "porota-governed-tests.xml",
)
MAX_ZIP_BYTES = 8 * 1024**3
CONVERGENCE_HANDOFF = "docs/audits/convergence/INPUT_MANIFEST_RC6_CONVERGENCIA.json"
CONVERGENCE_VERIFIER = "scripts/rc6_convergence_provenance.py"
FINAL_FIP_SCHEMA = "rc6.final-input-provenance.v1"


class BindingRejected(ValueError):
    pass


def positive_integer(value, signature):
    if isinstance(value, bool):
        raise BindingRejected(signature)
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value) is None:
        raise BindingRejected(signature)
    if not isinstance(value, (int, str)) or int(value) <= 0:
        raise BindingRejected(signature)
    return int(value)


def parse_merge_approval(message, candidate_sha):
    if not isinstance(candidate_sha, str) or SHA.fullmatch(candidate_sha) is None:
        raise BindingRejected("APPROVED_CANDIDATE_SHA_INVALID")
    if candidate_sha in DENIED_HEADS:
        raise BindingRejected("PRECONVERGENCE_CANDIDATE_DENIED")
    approval = {"candidate_sha": candidate_sha, "repository": REPOSITORY,
                "workflow_path": WORKFLOW_PATH, "event": "pull_request"}
    seen = set()
    for line in message.splitlines():
        if not line.startswith("Porota-Predeploy-"):
            continue
        name, separator, value = line.partition(":")
        if not separator or name not in TRAILERS or name in seen:
            raise BindingRejected("PREDEPLOY_APPROVAL_TRAILER_AMBIGUOUS")
        seen.add(name)
        approval[TRAILERS[name]] = value.strip()
    if seen != set(TRAILERS):
        raise BindingRejected("PREDEPLOY_APPROVED_TUPLE_MISSING")
    for name in ("workflow_id", "run_id", "run_attempt", "artifact_id"):
        approval[name] = positive_integer(approval[name], "PREDEPLOY_APPROVED_INTEGER_INVALID")
    if DIGEST.fullmatch(approval["artifact_digest"]) is None:
        raise BindingRejected("PREDEPLOY_APPROVED_DIGEST_INVALID")
    if approval["run_id"] in DENIED_RUNS or approval["artifact_id"] in DENIED_ARTIFACTS:
        raise BindingRejected("PRECONVERGENCE_ARTIFACT_DENIED")
    return approval


def artifact_name(approval):
    return (f"porota-predeploy-v2-{approval['candidate_sha']}-run-{approval['run_id']}"
            f"-attempt-{approval['run_attempt']}")


def _utc(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (AttributeError, ValueError) as exc:
        raise BindingRejected("PREDEPLOY_TIMESTAMP_INVALID") from exc


def validate_binding(approval, workflow, run, attempt, artifact, run_artifacts, *, now=None,
                     require_completed=True):
    """Pure fail-closed algorithm, exercised with wrong-origin negatives."""
    now = now or datetime.now(timezone.utc)
    if (workflow.get("id") != approval["workflow_id"] or workflow.get("path") != WORKFLOW_PATH
            or workflow.get("state") != "active"):
        raise BindingRejected("PREDEPLOY_WORKFLOW_ORIGIN_MISMATCH")
    for execution in (run, attempt):
        repository, head_repository = execution.get("repository", {}), execution.get("head_repository", {})
        if (execution.get("id") != approval["run_id"]
                or execution.get("workflow_id") != approval["workflow_id"]
                or execution.get("path") != WORKFLOW_PATH
                or execution.get("head_sha") != approval["candidate_sha"]
                or execution.get("event") != "pull_request"
                or type(execution.get("run_attempt")) is not int
                or execution.get("run_attempt") != approval["run_attempt"]
                or execution.get("status") != ("completed" if require_completed else "in_progress")
                or execution.get("conclusion") != ("success" if require_completed else None)
                or repository.get("full_name") != REPOSITORY or head_repository.get("full_name") != REPOSITORY
                or type(repository.get("id")) is not int or repository["id"] <= 0
                or type(head_repository.get("id")) is not int or head_repository["id"] != repository["id"]):
            raise BindingRejected("PREDEPLOY_RUN_ORIGIN_MISMATCH")
        requests = execution.get("pull_requests")
        if (not isinstance(requests, list) or len(requests) != 1
                or requests[0].get("head", {}).get("sha") != approval["candidate_sha"]
                or requests[0].get("base", {}).get("ref") != BASE_BRANCH
                or requests[0].get("base", {}).get("repo", {}).get("id") != execution["repository"].get("id")
                or requests[0].get("head", {}).get("repo", {}).get("id") != execution["head_repository"].get("id")):
            raise BindingRejected("PREDEPLOY_PULL_REQUEST_ORIGIN_MISMATCH")
    matches = [entry for entry in run_artifacts if entry.get("name") == artifact_name(approval)]
    if (len(matches) != 1 or matches[0].get("id") != approval["artifact_id"]
            or matches[0].get("digest") != approval["artifact_digest"]):
        raise BindingRejected("PREDEPLOY_RUN_ARTIFACT_NOT_UNIQUE")
    origin = artifact.get("workflow_run", {})
    created_at = _utc(artifact.get("created_at"))
    attempt_start = _utc(attempt.get("run_started_at"))
    attempt_end = _utc(attempt.get("updated_at")) if require_completed else now
    if (artifact.get("id") != approval["artifact_id"] or artifact.get("name") != artifact_name(approval)
            or artifact.get("expired") is not False
            or artifact.get("digest") != approval["artifact_digest"]
            or not 0 < positive_integer(artifact.get("size_in_bytes"), "PREDEPLOY_ARTIFACT_SIZE_INVALID") <= MAX_ZIP_BYTES
            or _utc(artifact.get("expires_at")) <= now
            or not attempt_start <= created_at <= attempt_end <= now
            or origin.get("id") != approval["run_id"]
            or origin.get("head_sha") != approval["candidate_sha"]
            or origin.get("repository_id") != run["repository"].get("id")
            or origin.get("head_repository_id") != run["head_repository"].get("id")):
        raise BindingRejected("PREDEPLOY_ARTIFACT_ORIGIN_MISMATCH")
    return {"schema": "porota.predeploy-approved-binding.v1", "status": "GREEN",
            **approval, "artifact_name": artifact["name"], "artifact_size_bytes": artifact["size_in_bytes"],
            "expires_at": artifact["expires_at"], "created_at": artifact["created_at"],
            "execution_state": "COMPLETED_SUCCESS" if require_completed else "IN_PROGRESS_EVIDENCE_ONLY",
            "promotion_authority": "APPROVED_MERGE_TRAILERS_REQUIRED",
            "real_orders_sent": 0, "real_routes": "NOT_CALLED"}


def safe_extract(zip_path, output_root, binding, *, extra_required=()):
    """Verify the external ZIP digest/size before reading any artifact claim."""
    if (zip_path.stat().st_size != binding["artifact_size_bytes"]
            or "sha256:" + sha256_file(zip_path) != binding["artifact_digest"]):
        raise BindingRejected("APPROVED_ARTIFACT_BYTES_MISMATCH")
    if output_root.is_symlink() or output_root.exists() and any(output_root.iterdir()):
        raise BindingRejected("ARTIFACT_OUTPUT_ROOT_NOT_EMPTY")
    required = set(REQUIRED_FILES) | set(extra_required)
    if any(PurePosixPath(name).name != name for name in required):
        raise BindingRejected("ARTIFACT_REQUIRED_BASENAME_INVALID")
    with zipfile.ZipFile(zip_path) as archive:
        inventory, paths, total = {}, set(), 0
        if len(archive.infolist()) > 50_000:
            raise BindingRejected("ZIP_FILE_COUNT_LIMIT")
        for entry in archive.infolist():
            name = entry.filename.rstrip("/")
            parsed = PurePosixPath(name)
            mode = entry.external_attr >> 16
            if (not name or parsed.is_absolute() or ".." in parsed.parts or "\\" in name
                    or parsed.as_posix() != name or any(ord(char) < 32 for char in name)
                    or name in paths or entry.flag_bits & 1
                    or stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}):
                raise BindingRejected("UNSAFE_ZIP_ARTIFACT_PATH")
            paths.add(name)
            total += entry.file_size
            if total > MAX_ZIP_BYTES:
                raise BindingRejected("ZIP_UNPACKED_SIZE_LIMIT")
            if not entry.is_dir() and parsed.name in required:
                if parsed.name in inventory:
                    raise BindingRejected("AMBIGUOUS_ZIP_REQUIRED_FILE")
                inventory[parsed.name] = entry
        if set(inventory) != required:
            raise BindingRejected("FROZEN_ARTIFACT_REQUIRED_FILE_MISSING")
        if archive.testzip() is not None:
            raise BindingRejected("FROZEN_ARTIFACT_CRC_MISMATCH")
        usage = shutil.disk_usage(output_root.parent)
        required_bytes = sum(entry.file_size for entry in inventory.values()) + 2 * 1024**3
        if usage.free < required_bytes:
            raise BindingRejected("RUNNER_EXTRACTION_CAPACITY_INSUFFICIENT")
        output_root.mkdir(parents=True, exist_ok=True)
        for name, entry in inventory.items():
            with archive.open(entry) as incoming, (output_root / name).open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
            (output_root / name).chmod(0o644)
    return inventory


def reject_git_replacements(repo_root):
    if os.environ.get("GIT_REPLACE_REF_BASE", "refs/replace/") != "refs/replace/":
        raise BindingRejected("APPROVED_GIT_REPLACE_REFS_FORBIDDEN")
    refs = subprocess.check_output(["git", "--no-replace-objects", "-C", str(repo_root),
                                    "for-each-ref", "--format=%(refname)", "refs/replace/"], text=True)
    if refs.strip():
        raise BindingRejected("APPROVED_GIT_REPLACE_REFS_FORBIDDEN")


def captured_final_file(extracted, name, limit):
    raw, proof = codec_smoke.read(Path(extracted) / name, limit=limit, deadline=time.monotonic() + 5)
    if stat.S_IMODE(proof[2]) != 0o644:
        raise BindingRejected("FINAL_RECEIPT_MODE_INVALID")
    return raw


def verify_final_input_provenance(repo_root, extracted_root, frozen, source):
    """Recompute final source closure from Git and one immutable JUnit capture."""
    rows = {row["path"]: row for row in source["files"]}
    if CONVERGENCE_HANDOFF not in rows:
        return {"status": "NOT_APPLICABLE", "scope": "NO_VERSIONED_CONVERGENCE_HANDOFF_IN_APPROVED_SOURCE",
                "runtime_approval": False}
    reject_git_replacements(repo_root)
    fip_raw = captured_final_file(extracted_root, "porota-final-input-provenance.json", 8 * 1024**2)
    gov_raw = captured_final_file(extracted_root, "porota-governed-tests.json", 256 * 1024)
    junit_raw = captured_final_file(extracted_root, "porota-governed-tests.xml", 16 * 1024**2)
    if frozen.get("final_input_provenance_sha256") != codec_smoke.sha(fip_raw):
        raise BindingRejected("FROZEN_FINAL_INPUT_PROVENANCE_DIGEST_MISMATCH")
    fip, gov = codec_smoke.loads(fip_raw), codec_smoke.loads(gov_raw)
    if (type(fip) is not dict or fip.get("schema") != FINAL_FIP_SCHEMA
            or fip.get("candidate_sha") != frozen["candidate_sha"]
            or fip.get("candidate_tree") != frozen["candidate_tree_sha"]
            or fip.get("software_status") != "EXECUTED_NATIVE_GREEN"):
        raise BindingRejected("FINAL_INPUT_PROVENANCE_CANDIDATE_MISMATCH")
    if (fip.get("final_candidate_eligible") is not True
            or fip.get("material_programming_gates_closed") is not True
            or type(fip.get("pending_material_programming_gates")) is not list
            or fip["pending_material_programming_gates"]):
        raise BindingRejected("FINAL_INPUT_PROVENANCE_MATERIAL_GATES_OPEN")
    execution = fip.get("test_execution")
    if (type(execution) is not dict or execution.get("junit_sha256") != codec_smoke.sha(junit_raw)
            or type(execution.get("executed_unique_cases")) is not int or execution["executed_unique_cases"] <= 0):
        raise BindingRejected("FINAL_INPUT_PROVENANCE_JUNIT_BINDING_INVALID")
    if (type(gov) is not dict or type(gov.get("schema_version")) is not int or gov["schema_version"] != 1
            or gov.get("status") != "GREEN" or gov.get("source_unchanged") is not True
            or gov.get("scope") != "repository-root automatic pytest discovery"
            or gov.get("candidate_sha") != frozen["candidate_sha"]
            or gov.get("candidate_tree") != frozen["candidate_tree_sha"]
            or gov.get("junit_sha256") != codec_smoke.sha(junit_raw)
            or type(gov.get("junit_bytes")) is not int or gov["junit_bytes"] != len(junit_raw)
            or any(type(gov.get(name)) is not int or gov[name] != 0
                   for name in ("pytest_exit_code", "failures", "errors", "skipped", "xfail"))
            or any(type(gov.get(name)) is not int or gov[name] != execution["executed_unique_cases"]
                   for name in ("discovered", "executed"))
            or type(gov.get("exclusions")) is not list
            or any(type(value) is not str for value in gov["exclusions"])):
        raise BindingRejected("FINAL_GOVERNED_RECEIPT_BINDING_INVALID")
    verifier_row = rows.get(CONVERGENCE_VERIFIER)
    if (type(verifier_row) is not dict or verifier_row.get("image_required") is not True
            or verifier_row.get("bundle_required") is not True or verifier_row.get("git_mode") != "100644"):
        raise BindingRejected("FINAL_CONVERGENCE_VERIFIER_SOURCE_REQUIRED")
    verifier_raw, mode = codec_smoke.read(Path(repo_root) / CONVERGENCE_VERIFIER,
                                         limit=4 * 1024**2, deadline=time.monotonic() + 5)
    if (codec_smoke.sha(verifier_raw) != verifier_row["sha256"]
            or len(verifier_raw) != verifier_row["bytes"] or stat.S_IMODE(mode[2]) != 0o644):
        raise BindingRejected("FINAL_CONVERGENCE_VERIFIER_SOURCE_CHANGED")
    source_path = Path(extracted_root) / "porota-source-provenance.json"
    before = verify_source_manifest(Path(repo_root), source_path, frozen["candidate_sha"], frozen["candidate_tree_sha"])
    if codec_smoke.canonical(before) != codec_smoke.canonical(source):
        raise BindingRejected("FINAL_CONVERGENCE_SOURCE_CHANGED")
    name = "rc6_frozen_convergence_verifier_" + uuid.uuid4().hex
    module = types.ModuleType(name); module.__file__ = str(Path(repo_root) / CONVERGENCE_VERIFIER)
    sys.modules[name] = module
    try:
        exec(compile(verifier_raw, module.__file__, "exec"), vars(module))
        receipt = module.JunitReceipt(junit_raw)
        _nodes, actual_count = module.executed_cases(receipt)
        if actual_count != execution["executed_unique_cases"]:
            raise BindingRejected("FINAL_GOVERNED_JUNIT_COUNT_MISMATCH")
        recomputed = module.verify(Path(repo_root), frozen["candidate_sha"], receipt, fetch_source_refs=False)
        if codec_smoke.canonical(recomputed) != codec_smoke.canonical(fip):
            raise BindingRejected("FINAL_INPUT_PROVENANCE_RECOMPUTATION_MISMATCH")
    finally:
        if sys.modules.get(name) is module:
            del sys.modules[name]
    reject_git_replacements(repo_root)
    after = verify_source_manifest(Path(repo_root), source_path, frozen["candidate_sha"], frozen["candidate_tree_sha"])
    if codec_smoke.canonical(after) != codec_smoke.canonical(before):
        raise BindingRejected("FINAL_CONVERGENCE_SOURCE_CHANGED")
    return {"schema": FINAL_FIP_SCHEMA, "status": "GREEN",
        "raw_fip_sha256": codec_smoke.sha(fip_raw), "raw_governed_sha256": codec_smoke.sha(gov_raw),
        "raw_junit_sha256": codec_smoke.sha(junit_raw), "raw_junit_bytes": len(junit_raw),
        "executed_unique_cases": actual_count, "verifier_source_sha256": codec_smoke.sha(verifier_raw),
        "candidate_sha": frozen["candidate_sha"], "candidate_tree": frozen["candidate_tree_sha"],
        "verification_scope": "RECOMPUTED_SOURCE_CLOSURE_BOUND_TO_DOWNLOADED_GOVERNED_AND_JUNIT_BYTES",
        "runtime_approval": False}


def verify_runtime_codec_smoke(extracted_root, frozen, source):
    """Bind the raw tiny-smoke receipt to independently verified source and ID.

    This checks published evidence. Docker execution and the external GitHub
    tuple remain separate caller obligations.
    """
    metadata = frozen.get("runtime_codec_smoke")
    keys = {"schema", "receipt_sha256", "script_sha256", "fixtures_sha256", "image_id"}
    if (type(metadata) is not dict or set(metadata) != keys
            or metadata["schema"] != codec_smoke.SCHEMA
            or metadata["image_id"] != frozen.get("image_id")
            or DIGEST.fullmatch(str(metadata["image_id"])) is None):
        raise BindingRejected("FROZEN_RUNTIME_CODEC_BINDING_INVALID")
    rows = {row["path"]: row for row in source["files"]}
    for name in codec_smoke.SOURCE_PATHS:
        row = rows.get(name)
        if (type(row) is not dict or row.get("image_required") is not True
                or row.get("bundle_required") is not True or row.get("git_mode") != "100644"):
            raise BindingRejected("FROZEN_RUNTIME_CODEC_SOURCE_MISSING")
    fixtures = {name: rows[name]["sha256"] for name in codec_smoke.SOURCE_PATHS[1:3]}
    if (metadata["script_sha256"] != rows[codec_smoke.SOURCE_PATHS[0]]["sha256"]
            or metadata["fixtures_sha256"] != fixtures):
        raise BindingRejected("FROZEN_RUNTIME_CODEC_SOURCE_MISMATCH")
    raw, proof = codec_smoke.read(Path(extracted_root) / "porota-runtime-codec-smoke.json",
                                 limit=codec_smoke.MAX_OUTPUT, deadline=time.monotonic() + 2)
    if stat.S_IMODE(proof[2]) != 0o644 or metadata["receipt_sha256"] != codec_smoke.sha(raw):
        raise BindingRejected("FROZEN_RUNTIME_CODEC_RECEIPT_DIGEST_MISMATCH")
    report = codec_smoke.loads(raw)
    codec_smoke.validate_report(report, candidate_sha=frozen["candidate_sha"],
        tree_sha=frozen["candidate_tree_sha"], image_id=frozen["image_id"],
        source_manifest_sha256=frozen["source_manifest_sha256"],
        source_sha256={name: row["sha256"] for name, row in rows.items()})
    return {"schema": codec_smoke.SCHEMA, "status": "GREEN",
        "receipt_sha256": codec_smoke.sha(raw), "receipt_bytes": len(raw),
        "script_sha256": metadata["script_sha256"], "fixtures_sha256": fixtures,
        "image_id": frozen["image_id"], "case_ids": list(codec_smoke.CASE_IDS),
        "verification_scope": "PUBLISHED_RECEIPT_SOURCE_AND_IMAGE_ID_BINDINGS_ONLY",
        "runtime_approval": False}


def verify_frozen_payload(repo_root, extracted_root, binding, tree_sha):
    reject_git_replacements(repo_root)
    frozen = decode_json((extracted_root / "porota-frozen-candidate.json").read_bytes())
    expected_origin = {name: binding[name] for name in (
        "repository", "workflow_path", "workflow_id", "run_id", "run_attempt", "event", "candidate_sha")}
    if (frozen.get("execution_origin") != expected_origin
            or frozen.get("candidate_sha") != binding["candidate_sha"]
            or frozen.get("candidate_tree_sha") != tree_sha
            or frozen.get("paper_mode_required") != "PRODUCTION_PAPER"
            or type(frozen.get("real_orders_sent_required")) is not int
            or frozen.get("real_orders_sent_required") != 0
            or frozen.get("real_order_capability_required") != "BLOCKED"
            or frozen.get("build_once") is not True
            or type(frozen.get("image_size_bytes")) is not int or frozen["image_size_bytes"] <= 0):
        raise BindingRejected("FROZEN_CANDIDATE_APPROVAL_MISMATCH")
    source_path = extracted_root / "porota-source-provenance.json"
    source = verify_source_manifest(repo_root, source_path, binding["candidate_sha"], tree_sha)
    if frozen.get("source_manifest_sha256") != sha256_file(source_path):
        raise BindingRejected("FROZEN_SOURCE_MANIFEST_DIGEST_MISMATCH")
    runtime_codec = verify_runtime_codec_smoke(extracted_root, frozen, source)
    final_provenance = verify_final_input_provenance(repo_root, extracted_root, frozen, source)
    bundle_receipt = validate_bundle(repo_root, extracted_root / "porota-deploy-bundle-v2.tgz",
        extracted_root / "porota-deploy-bundle-v2-manifest.json", source_path,
        binding["candidate_sha"], tree_sha)
    image_path = extracted_root / "porota-predeploy-image.tar.gz"
    if frozen.get("image_tar_sha256") != sha256_file(image_path):
        raise BindingRejected("FROZEN_IMAGE_TAR_DIGEST_MISMATCH")
    image_receipt = validate_image_archive(image_path, source, frozen["image_id"])
    try:
        from scripts.porota_host_manifest_v2 import build_manifest
        from scripts.porota_host_policy_v2 import validate_policy
    except ModuleNotFoundError:
        from porota_host_manifest_v2 import build_manifest
        from porota_host_policy_v2 import validate_policy
    host_policy = decode_json((repo_root / "ops/policy/host-control-plane-reconciliation-v2.json").read_bytes())
    host_manifest = build_manifest(repo_root, host_policy, binding["candidate_sha"])
    # The native host CLI emits indented JSON. Compare decoded canonical
    # content to Git-derived receipts; the external ZIP digest already binds
    # the exact serialized bytes, including whitespace, independently.
    if (decode_json((extracted_root / "porota-host-manifest-v2.json").read_bytes()) != host_manifest
            or decode_json((extracted_root / "porota-host-policy-v2.json").read_bytes()) != validate_policy(host_manifest, host_policy)):
        raise BindingRejected("FROZEN_HOST_CONTROL_PLANE_SOURCE_MISMATCH")
    return {"schema": "porota.approved-artifact-byte-verification.v1", "status": "GREEN",
            "binding": binding, "candidate_tree_sha": tree_sha,
            "bundle": bundle_receipt, "image": image_receipt, "runtime_codec_smoke": runtime_codec,
            "final_input_provenance": final_provenance,
            "files": {name: {"sha256": sha256_file(extracted_root / name),
                              "bytes": (extracted_root / name).stat().st_size} for name in REQUIRED_FILES}}


def api_get(path):
    try:
        from scripts.porota_artifact_http import api_get as bounded_get
    except ModuleNotFoundError:
        from porota_artifact_http import api_get as bounded_get
    return bounded_get(path)


def locate_approved(approval):
    workflow = api_get("/actions/workflows/porota-predeploy-v2.yml")
    run = api_get(f"/actions/runs/{approval['run_id']}")
    attempt = api_get(f"/actions/runs/{approval['run_id']}/attempts/{approval['run_attempt']}")
    artifact = api_get(f"/actions/artifacts/{approval['artifact_id']}")
    data = api_get(f"/actions/runs/{approval['run_id']}/artifacts?per_page=100")
    if data.get("total_count", 0) > 100:
        raise BindingRejected("PREDEPLOY_ARTIFACT_LIST_TRUNCATED")
    return validate_binding(approval, workflow, run, attempt, artifact, data.get("artifacts", []))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("locate", "verify"))
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--tree-sha")
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--zip", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    if args.operation == "locate":
        message = subprocess.check_output(["git", "show", "-s", "--format=%B", "HEAD"], text=True)
        result = locate_approved(parse_merge_approval(message, args.candidate_sha))
        args.binding.write_bytes(canonical_bytes(result))
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as target:
            for key, field in (("PREDEPLOY_ARTIFACT_ID", "artifact_id"), ("PREDEPLOY_RUN_ID", "run_id"),
                               ("PREDEPLOY_RUN_ATTEMPT", "run_attempt"), ("PREDEPLOY_ARTIFACT_DIGEST", "artifact_digest")):
                target.write(f"{key}={result[field]}\n")
    else:
        if not args.zip or not args.output_root or not args.receipt or not args.tree_sha:
            parser.error("verify requires ZIP/output-root/receipt/tree-sha")
        binding = decode_json(args.binding.read_bytes())
        if binding["candidate_sha"] != args.candidate_sha:
            raise BindingRejected("APPROVED_CANDIDATE_SHA_MISMATCH")
        safe_extract(args.zip, args.output_root, binding)
        result = verify_frozen_payload(args.repo_root, args.output_root, binding, args.tree_sha)
        args.receipt.write_bytes(canonical_bytes(result))
    print("POROTA_APPROVED_PREDEPLOY_BINDING=GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
