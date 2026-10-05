#!/usr/bin/env python3
"""Replay the published primary ZIP once; export bounded evidence only."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import uuid

try:
    from scripts.porota_artifact_http import api_get, download_artifact
    from scripts.porota_artifact_provenance import (
        SOURCE_MANIFEST_NAME, canonical_bytes, canonical_file_mode, decode_json, sha256_file)
    from scripts.porota_predeploy_binding import (
        REPOSITORY, WORKFLOW_PATH, REQUIRED_FILES, artifact_name, positive_integer,
        safe_extract, validate_binding, verify_frozen_payload, verify_runtime_codec_smoke, codec_smoke)
except ModuleNotFoundError:
    from porota_artifact_http import api_get, download_artifact
    from porota_artifact_provenance import (
        SOURCE_MANIFEST_NAME, canonical_bytes, canonical_file_mode, decode_json, sha256_file)
    from porota_predeploy_binding import (
        REPOSITORY, WORKFLOW_PATH, REQUIRED_FILES, artifact_name, positive_integer,
        safe_extract, validate_binding, verify_frozen_payload, verify_runtime_codec_smoke, codec_smoke)

MAX_EVIDENCE_BYTES = 24 * 1024**2
MAX_SECONDARY_ARTIFACT_BYTES = 32 * 1024**2
PUBLISHED_EXTRA_RECEIPTS = ()  # FIP/Gov/JUnit are mandatory primary members.


def replay_remaining(deadline, *, clock=time.monotonic):
    """Return the existing total budget; the HTTP transport has its own 30s cap."""
    value = deadline - clock()
    if not math.isfinite(value) or value <= 0:
        raise ValueError("PUBLISHED_REPLAY_TOTAL_DEADLINE_EXCEEDED")
    return value


def upload_digest(value):
    if re.fullmatch(r"[0-9a-f]{64}", value): return "sha256:" + value
    if re.fullmatch(r"sha256:[0-9a-f]{64}", value): return value
    raise ValueError("PRIMARY_UPLOAD_DIGEST_INVALID")


def current_primary_binding(candidate_sha, artifact_id, digest, *, deadline, get=api_get):
    if (os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
            or os.environ.get("GITHUB_EVENT_NAME") != "pull_request"
            or os.environ.get("GITHUB_WORKFLOW_REF", "").split("@")[0] != REPOSITORY + "/" + WORKFLOW_PATH
            or re.fullmatch(r"[0-9a-f]{40}", candidate_sha) is None):
        raise ValueError("PRIMARY_EXECUTION_CONTEXT_MISMATCH")
    workflow = get("/actions/workflows/porota-predeploy-v2.yml", deadline=deadline)
    run_id = positive_integer(os.environ.get("GITHUB_RUN_ID"), "PRIMARY_RUN_ID_INVALID")
    attempt_id = positive_integer(os.environ.get("GITHUB_RUN_ATTEMPT"), "PRIMARY_ATTEMPT_INVALID")
    approval = {"repository": REPOSITORY, "workflow_path": WORKFLOW_PATH, "workflow_id": workflow["id"],
                "run_id": run_id, "run_attempt": attempt_id, "candidate_sha": candidate_sha,
                "event": "pull_request", "artifact_id": artifact_id, "artifact_digest": upload_digest(digest)}
    run = get(f"/actions/runs/{run_id}", deadline=deadline)
    attempt = get(f"/actions/runs/{run_id}/attempts/{attempt_id}", deadline=deadline)
    artifact = get(f"/actions/artifacts/{artifact_id}", deadline=deadline)
    inventory = get(f"/actions/runs/{run_id}/artifacts?per_page=100", deadline=deadline)
    if inventory.get("total_count", 0) > 100: raise ValueError("PRIMARY_ARTIFACT_LIST_TRUNCATED")
    return validate_binding(approval, workflow, run, attempt, artifact, inventory.get("artifacts", []),
                            now=datetime.now(timezone.utc), require_completed=False)


def verify_saved_app_rootfs(image_path, source, *, deadline=None):
    """Apply ordered layer deltas to /app without extracting paths or rebuilding."""
    rootfs, cache = {}, {}
    def drop(path, include_self=True):
        for key in list(rootfs):
            if key.startswith(path + "/") or include_self and key == path: del rootfs[key]
    with tarfile.open(image_path, "r:gz") as outer:
        manifest = decode_json(outer.extractfile("manifest.json").read())
        for reference in manifest[0]["Layers"]:
            if deadline is not None: replay_remaining(deadline)
            if reference not in cache:
                events = []
                with io.BufferedReader(outer.extractfile(reference)) as stored:
                    content = gzip.GzipFile(fileobj=stored) if stored.peek(2)[:2] == b"\x1f\x8b" else stored
                    with tarfile.open(fileobj=content, mode="r|") as layer:
                        for info in layer:
                            if deadline is not None: replay_remaining(deadline)
                            name = info.name.removeprefix("./").rstrip("/")
                            if name != "app" and not name.startswith("app/"): continue
                            parsed = PurePosixPath(name)
                            if (parsed.is_absolute() or ".." in parsed.parts or parsed.as_posix() != name
                                    or "\\" in name or any(ord(char) < 32 for char in name)):
                                raise ValueError("PUBLISHED_ROOTFS_PATH_INVALID")
                            if parsed.name == ".wh..wh..opq":
                                events.append(("opaque", str(parsed.parent), None)); continue
                            if parsed.name.startswith(".wh."):
                                events.append(("delete", str(parsed.parent / parsed.name[4:]), None)); continue
                            if info.isdir(): value = {"kind": "directory", "mode": stat.S_IMODE(info.mode)}
                            elif info.isfile():
                                digest = hashlib.sha256(); size = 0
                                with layer.extractfile(info) as data:
                                    while True:
                                        if deadline is not None: replay_remaining(deadline)
                                        chunk = data.read(1024**2)
                                        if not chunk: break
                                        size += len(chunk); digest.update(chunk)
                                if size != info.size: raise ValueError("PUBLISHED_ROOTFS_FILE_SIZE_MISMATCH")
                                value = {"kind": "file", "mode": stat.S_IMODE(info.mode), "sha256": digest.hexdigest(), "bytes": size}
                            else: value = {"kind": "alias_or_nonregular", "mode": stat.S_IMODE(info.mode)}
                            events.append(("put", name, value))
                    if content is not stored: content.close()
                cache[reference] = events
            # OCI whiteouts affect lower layers, independent of tar member order.
            for operation, name, value in cache[reference]:
                if operation == "opaque": drop(name, include_self=False)
                elif operation == "delete": drop(name)
            for operation, name, value in cache[reference]:
                if operation != "put": continue
                if value["kind"] != "directory" or rootfs.get(name, {}).get("kind") not in {None, "directory"}: drop(name)
                rootfs[name] = value
    verified = []
    expected = [("app/" + row["path"], row["sha256"], canonical_file_mode(row["git_mode"]))
                for row in source["files"] if row["image_required"]]
    expected.append(("app/" + SOURCE_MANIFEST_NAME, hashlib.sha256(canonical_bytes(source)).hexdigest(), 0o644))
    present = {name for name, value in rootfs.items() if name.startswith("app/") and value["kind"] != "directory"}
    if present != {name for name, _, _ in expected}:
        raise ValueError("PUBLISHED_ROOTFS_UNEXPECTED_OR_MISSING_SOURCE")
    for name, digest, mode in expected:
        value = rootfs.get(name, {})
        if value.get("kind") != "file" or value.get("sha256") != digest or value.get("mode") != mode:
            raise ValueError("PUBLISHED_ROOTFS_SOURCE_OR_MODE_MISMATCH")
        if any(rootfs.get(str(parent), {}).get("kind") not in {None, "directory"} for parent in PurePosixPath(name).parents if str(parent) != "."):
            raise ValueError("PUBLISHED_ROOTFS_PARENT_ALIAS")
        verified.append({"path": name.removeprefix("app/"), "sha256": digest, "mode": mode, "bytes": value["bytes"]})
    return {"schema": "porota.published-image-app-rootfs.v1", "status": "GREEN",
            "candidate_sha": source["candidate_sha"], "candidate_tree_sha": source["candidate_tree_sha"],
            "ordered_layer_references": len(manifest[0]["Layers"]), "unique_layer_blobs": len(cache),
            "source_files_and_generated_metadata_verified": len(verified), "files": verified}


def write_evidence(output, *, binding, payload, rootfs, download, loaded_image_id):
    frozen = decode_json((payload["extracted_root"] / "porota-frozen-candidate.json").read_bytes())
    if loaded_image_id != frozen["image_id"] or re.fullmatch(r"sha256:[0-9a-f]{64}", loaded_image_id) is None:
        raise ValueError("PUBLISHED_PRIMARY_LOADED_IMAGE_ID_MISMATCH")
    if output.is_symlink() or output.exists() and any(output.iterdir()): raise ValueError("EVIDENCE_OUTPUT_NOT_EMPTY")
    output.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_FILES:
        if name.endswith((".json", ".xml")): shutil.copyfile(payload["extracted_root"] / name, output / name)
    for name in payload.get("extra_receipts", ()):
        if name not in PUBLISHED_EXTRA_RECEIPTS: raise ValueError("EVIDENCE_EXTRA_RECEIPT_NOT_ALLOWED")
        if (payload["extracted_root"] / name).stat().st_size > MAX_EVIDENCE_BYTES:
            raise ValueError("EVIDENCE_EXTRA_RECEIPT_SIZE_LIMIT")
        raw = (payload["extracted_root"] / name).read_bytes()
        decode_json(raw)
        (output / name).write_bytes(raw)  # Preserve the downloaded primary's bytes exactly.
    report = {"schema": "porota.published-primary-evidence-only.v1", "status": "GREEN",
        "promotable": False, "role": "EVIDENCE_ONLY_NOT_PROMOTION_AUTHORITY",
        "primary": binding, "candidate_tree_sha": frozen["candidate_tree_sha"],
        "download": download, "byte_verification": payload["receipt"], "rootfs": rootfs,
        "actual_image_execution": payload.get("actual_image_execution"),
        "loaded_image_id": loaded_image_id, "frozen_image_id": frozen["image_id"],
        "image_rebuilt": False, "production_actions": "NOT_CALLED", "provider_requests": 0,
        "real_orders_sent": 0, "real_routes": "NOT_CALLED",
        "trust_boundary": "VERIFY_EXTERNAL_GITHUB_API_SOURCE_WORKFLOW_JOBS_LOGS_AND_MANIFESTS",
        "secondary_raw_limit_bytes": MAX_EVIDENCE_BYTES, "secondary_artifact_limit_bytes": MAX_SECONDARY_ARTIFACT_BYTES}
    (output / "published-primary-verification.json").write_bytes(canonical_bytes(report))
    inventory = {path.name: {"sha256": sha256_file(path), "bytes": path.stat().st_size}
                 for path in output.iterdir()}
    index = {"schema": "porota.secondary-evidence-inventory.v1", "promotable": False,
             "primary": binding, "files": inventory, "total_payload_bytes": sum(row["bytes"] for row in inventory.values())}
    (output / "evidence-inventory.json").write_bytes(canonical_bytes(index))
    total = sum(path.stat().st_size for path in output.iterdir())
    if total > MAX_EVIDENCE_BYTES: raise ValueError("SECONDARY_EVIDENCE_SIZE_LIMIT")
    for path in output.iterdir(): path.chmod(0o644)
    return {"status": "GREEN", "evidence_payload_bytes": total, "primary": binding,
            "promotable": False, "secondary_artifact_limit_bytes": MAX_SECONDARY_ARTIFACT_BYTES}


def verify_loaded_image_and_imports(extracted, *, candidate_sha, image_ref, deadline,
                                    run=subprocess.run, clock=time.monotonic):
    """Load once, then execute imports and tiny codec recovery on that same ID."""
    replay_remaining(deadline, clock=clock)
    frozen = decode_json((extracted / "porota-frozen-candidate.json").read_bytes())
    source = decode_json((extracted / "porota-source-provenance.json").read_bytes())
    primary_codec = verify_runtime_codec_smoke(extracted, frozen, source)
    if image_ref != "porota-predeploy-v2:" + candidate_sha:
        raise ValueError("PUBLISHED_IMAGE_REFERENCE_INVALID")
    image = extracted / "porota-predeploy-image.tar.gz"
    with tarfile.open(image, "r:gz") as archive:
        manifest = decode_json(archive.extractfile("manifest.json").read())
    if manifest[0].get("RepoTags") != [image_ref]: raise ValueError("PUBLISHED_IMAGE_TAG_ORIGIN_MISMATCH")
    def execute(argv, *, output_limit=1024**2, seconds=None, private_container=None, **kwargs):
        allowance = replay_remaining(deadline, clock=clock)
        try:
            result = run(argv, check=True, text=True, capture_output=True,
                         timeout=min(allowance, seconds) if seconds is not None else allowance, **kwargs)
            replay_remaining(deadline, clock=clock)
            if (type(result.returncode) is not int or result.returncode != 0
                    or not isinstance(result.stdout, str) or not isinstance(result.stderr, str)):
                raise ValueError("PUBLISHED_IMAGE_EXECUTION_NOT_GREEN")
            if len(result.stdout.encode()) + len(result.stderr.encode()) > output_limit:
                raise ValueError("PUBLISHED_IMAGE_EXECUTION_OUTPUT_LIMIT")
            return result
        except BaseException:
            if private_container is not None:
                # The CLI timeout alone cannot prove container termination.
                # This bounded failure teardown never grants additional PASS time.
                try:
                    cleaned = run(["docker", "rm", "--force", private_container], check=True, text=True,
                                  capture_output=True, timeout=5)
                    if type(cleaned.returncode) is not int or cleaned.returncode != 0:
                        raise ValueError("PUBLISHED_PRIVATE_CONTAINER_REMOVE_NOT_GREEN")
                except Exception as cleanup_error:
                    raise ValueError("PUBLISHED_PRIVATE_CONTAINER_CLEANUP_UNCONFIRMED") from cleanup_error
            raise
    loaded = execute(["docker", "load", "--input", str(image)])
    inspected = execute(["docker", "image", "inspect", "--format", "{{.Id}}", image_ref])
    loaded_id = inspected.stdout.strip()
    if loaded_id != frozen["image_id"] or re.fullmatch(r"sha256:[0-9a-f]{64}", loaded_id) is None:
        raise ValueError("PUBLISHED_PRIMARY_LOADED_IMAGE_ID_MISMATCH")
    script = """import json
from pathlib import Path
import bg_paper_dashboard, bf_production_paper_observer, bv_paper_runtime
import rc6_shadow_runtime.worker, rc6_dynamic_universe.promotion
import rc6_trader_dashboard.projection, scripts.rc6_shadow_health_gate
from scripts.porota_dependency_repro_audit import current_platform_identity, installed_distribution_audit, platform_supported
policy=json.loads(Path('ops/policy/rc6-supply-chain-v1.json').read_text())
assert installed_distribution_audit(policy)['status']=='GREEN'
assert platform_supported(current_platform_identity(),policy['platform'])
print('POROTA_PUBLISHED_EXACT_IMAGE_IMPORT_AND_CLOSURE=GREEN')
"""
    import_container = "porota-rc6-import-replay-" + uuid.uuid4().hex
    smoke = execute(["docker", "run", "--rm", "--name", import_container, "-i", "--pull", "never", "--network", "none",
                     "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                     "--entrypoint", "python", loaded_id, "-"], input=script, private_container=import_container)
    if "POROTA_PUBLISHED_EXACT_IMAGE_IMPORT_AND_CLOSURE=GREEN" not in smoke.stdout:
        raise ValueError("PUBLISHED_IMAGE_IMPORT_RECEIPT_MISSING")
    container_name = "porota-rc6-codec-replay-" + uuid.uuid4().hex
    command = ["docker", "run", "--rm", "--name", container_name, "--pull", "never",
        "--network", "none", "--read-only", "--user", "1000:1000", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--no-healthcheck",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=33554432,uid=1000,gid=1000,mode=1777",
        "--entrypoint", "python", loaded_id, "-m", "scripts.rc6_archive_v3_image_smoke",
        "--source-manifest", "/app/POROTA_SOURCE_PROVENANCE.json",
        "--candidate-sha", candidate_sha, "--tree-sha", frozen["candidate_tree_sha"], "--image-id", loaded_id]
    codec_execution = execute(command, output_limit=codec_smoke.MAX_OUTPUT,
                              seconds=codec_smoke.MAX_SECONDS, private_container=container_name)
    replay = codec_smoke.loads(codec_execution.stdout.encode())
    codec_smoke.validate_report(replay, candidate_sha=candidate_sha,
        tree_sha=frozen["candidate_tree_sha"], image_id=loaded_id,
        source_manifest_sha256=frozen["source_manifest_sha256"],
        source_sha256={row["path"]: row["sha256"] for row in source["files"]})
    replay_raw = codec_execution.stdout.encode()
    replay_remaining(deadline, clock=clock)
    return {"schema": "porota.published-exact-image-execution.v1", "status": "GREEN",
            "loaded_image_id": loaded_id, "frozen_image_id": frozen["image_id"],
            "load_exit_code": loaded.returncode, "inspect_exit_code": inspected.returncode,
            "import_and_installed_closure_exit_code": smoke.returncode,
            "import_script_sha256": hashlib.sha256(script.encode()).hexdigest(),
            "runtime_codec_smoke": {"schema": codec_smoke.SCHEMA, "status": "GREEN",
                "exit_code": codec_execution.returncode, "actual_image_id": loaded_id,
                "primary_receipt_sha256": primary_codec["receipt_sha256"],
                "replay_receipt_sha256": hashlib.sha256(replay_raw).hexdigest(), "replay_report": replay,
                "case_ids": list(codec_smoke.CASE_IDS), "timeout_seconds": codec_smoke.MAX_SECONDS,
                "output_limit_bytes": codec_smoke.MAX_OUTPUT, "user": "1000:1000", "read_only": True,
                "private_tmpfs_bytes": 33554432, "capabilities": "DROPPED_ALL",
                "no_new_privileges": True, "host_volume_mounts": [], "image_rebuilt": False,
                "receipt_scope": "OBSERVED_SUBPROCESS_ON_EXACT_LOADED_PRIMARY_IMAGE_ID"},
            "network": "NONE", "host_volume_mounts": [], "image_rebuilt": False,
            "execution_scope": "GITHUB_ACTIONS_RUNNER_EPHEMERAL_OFFLINE_CONTAINER"}


def main(argv=None, *, clock=time.monotonic):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--tree-sha", required=True)
    parser.add_argument("--artifact-id", type=int, required=True)
    parser.add_argument("--upload-digest", required=True)
    parser.add_argument("--image-ref", required=True)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--deadline-seconds", type=int, default=600)
    args = parser.parse_args(argv)
    if not 60 <= args.deadline_seconds <= 900: parser.error("bounded deadline must be 60..900 seconds")
    deadline = clock() + args.deadline_seconds
    previous_alarm = signal.getsignal(signal.SIGALRM)
    def deadline_expired(signum, frame):
        raise ValueError("PUBLISHED_REPLAY_TOTAL_DEADLINE_EXCEEDED")
    signal.signal(signal.SIGALRM, deadline_expired)
    signal.setitimer(signal.ITIMER_REAL, args.deadline_seconds)
    try:
        binding = current_primary_binding(args.candidate_sha, args.artifact_id, args.upload_digest, deadline=deadline)
        with tempfile.TemporaryDirectory(prefix="porota-published-replay-", dir=os.getenv("RUNNER_TEMP")) as temporary:
            root = Path(temporary); archive = root / "primary.zip"
            download = download_artifact(args.artifact_id, archive, expected_size=binding["artifact_size_bytes"],
                expected_digest=binding["artifact_digest"], deadline=deadline)
            extracted = root / "extracted"
            safe_extract(archive, extracted, binding, extra_required=PUBLISHED_EXTRA_RECEIPTS)
            replay_remaining(deadline, clock=clock)
            receipt = verify_frozen_payload(args.repo_root, extracted, binding, args.tree_sha)
            receipt["extra_receipt_files"] = {name: {"sha256": sha256_file(extracted / name),
                "bytes": (extracted / name).stat().st_size} for name in PUBLISHED_EXTRA_RECEIPTS}
            replay_remaining(deadline, clock=clock)
            source = decode_json((extracted / "porota-source-provenance.json").read_bytes())
            rootfs = verify_saved_app_rootfs(extracted / "porota-predeploy-image.tar.gz", source, deadline=deadline)
            replay_remaining(deadline, clock=clock)
            execution = verify_loaded_image_and_imports(extracted, candidate_sha=args.candidate_sha,
                image_ref=args.image_ref, deadline=deadline, clock=clock)
            result = write_evidence(args.evidence_root, binding=binding,
                payload={"extracted_root": extracted, "receipt": receipt, "actual_image_execution": execution,
                         "extra_receipts": PUBLISHED_EXTRA_RECEIPTS}, rootfs=rootfs,
                download=download, loaded_image_id=execution["loaded_image_id"])
            replay_remaining(deadline, clock=clock)
        # The same total deadline includes private context cleanup and stdout.
        replay_remaining(deadline, clock=clock)
        print(json.dumps(result, sort_keys=True), flush=True)
        replay_remaining(deadline, clock=clock)
        print('POROTA_PUBLISHED_PRIMARY_REPLAY=GREEN|secondary=EVIDENCE_ONLY|production_actions=NOT_CALLED', flush=True)
        replay_remaining(deadline, clock=clock)
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError, tarfile.TarError, EOFError,
            urllib.error.URLError, subprocess.SubprocessError):
        # Redirect URLs may contain signed credentials. No exception text,
        # response headers, request headers or token appears in workflow logs.
        print('POROTA_PUBLISHED_PRIMARY_REPLAY=RED|reason=PRIMARY_REPLAY_NOT_VERIFIED')
        return 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_alarm)


if __name__ == "__main__":
    raise SystemExit(main())
