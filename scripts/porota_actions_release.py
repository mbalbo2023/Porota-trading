#!/usr/bin/env python3
"""Resolve and download one authenticated Predeploy V2 artifact; never build."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

REPOSITORY = "mbalbo2023/Porota-trading"
WORKFLOW = ".github/workflows/porota-predeploy-v2.yml"
MEMBERS = frozenset({"release.json", "porota-predeploy-image.tar.gz",
                     "porota-deploy-bundle-v2.tgz", "porota-deploy-bundle-v2-manifest.json",
                     "code-tests.json", "functional.json"})


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_run(run, candidate_sha):
    require(re.fullmatch(r"[0-9a-f]{40}", candidate_sha) is not None, "SOURCE_SHA_INVALID")
    require(type(run.get("id")) is int and run["id"] > 0, "RUN_ID_INVALID")
    require(type(run.get("run_attempt")) is int and run["run_attempt"] > 0, "RUN_ATTEMPT_INVALID")
    require(run.get("status") == "completed" and run.get("conclusion") == "success", "PREDEPLOY_NOT_SUCCESSFUL")
    require(run.get("head_sha") == candidate_sha, "PREDEPLOY_SOURCE_MISMATCH")
    require(run.get("head_repository", {}).get("full_name") == REPOSITORY, "PREDEPLOY_REPOSITORY_MISMATCH")
    require(run.get("path") == WORKFLOW, "PREDEPLOY_WORKFLOW_MISMATCH")
    require(run.get("event") in {"pull_request", "workflow_dispatch"}, "PREDEPLOY_EVENT_INVALID")


def validate_artifact(artifact, run, candidate_sha):
    require(type(artifact.get("id")) is int and artifact["id"] > 0, "ARTIFACT_ID_INVALID")
    require(artifact.get("name") == "porota-predeploy-v2-" + candidate_sha, "ARTIFACT_NAME_MISMATCH")
    require(artifact.get("expired") is False, "ARTIFACT_EXPIRED")
    require(type(artifact.get("size_in_bytes")) is int and artifact["size_in_bytes"] > 0, "ARTIFACT_EMPTY")
    require(isinstance(artifact.get("digest"), str) and
            re.fullmatch(r"sha256:[0-9a-f]{64}", artifact["digest"]) is not None, "ARTIFACT_DIGEST_MISSING")
    identity = artifact.get("workflow_run", {})
    require(identity.get("id") == run["id"] and identity.get("head_sha") == candidate_sha,
            "ARTIFACT_RUN_MISMATCH")


def validate_release_run(release, run):
    require(release.get("source_sha") == run["head_sha"], "RELEASE_RUN_SOURCE_MISMATCH")
    for field, key in (("build_run_id", "id"), ("build_run_attempt", "run_attempt")):
        require(type(release.get(field)) is int and release[field] == run[key], "RELEASE_RUN_IDENTITY_MISMATCH")


def extract_artifact(archive, destination, digest):
    require("sha256:" + sha256(archive) == digest, "ARTIFACT_BYTES_MISMATCH")
    with zipfile.ZipFile(archive) as zipped:
        entries = zipped.infolist()
        require(len(entries) == len(MEMBERS) and {x.filename for x in entries} == MEMBERS,
                "ARTIFACT_MEMBERS_MISMATCH")
        for entry in entries:
            mode = entry.external_attr >> 16
            require(not entry.is_dir() and not stat.S_ISLNK(mode) and
                    (stat.S_IFMT(mode) in {0, stat.S_IFREG}), "ARTIFACT_MEMBER_NOT_REGULAR")
        destination = Path(destination)
        require(not destination.exists(), "ARTIFACT_DESTINATION_EXISTS")
        destination.mkdir(mode=0o700, parents=True)
        for entry in entries:
            with zipped.open(entry) as source, (destination / entry.filename).open("xb") as target:
                shutil.copyfileobj(source, target)


def extract_source_bundle(release_dir):
    """Called only after verify_release checked the bundle and per-file manifest."""
    release_dir = Path(release_dir)
    destination = release_dir.parent / "source"
    require(not destination.exists(), "SOURCE_DESTINATION_EXISTS")
    manifest = json.loads((release_dir / "porota-deploy-bundle-v2-manifest.json").read_text())
    expected = {item["path"] for item in manifest["files"]} | {"POROTA_DEPLOY_BUNDLE_MANIFEST.json"}
    with tarfile.open(release_dir / "porota-deploy-bundle-v2.tgz", "r:gz") as bundle:
        entries = bundle.getmembers()
        require(len(entries) == len(expected) and {x.name for x in entries} == expected,
                "SOURCE_BUNDLE_MEMBERS_MISMATCH")
        for entry in entries:
            path = PurePosixPath(entry.name)
            require(entry.isfile() and not path.is_absolute() and ".." not in path.parts,
                    "SOURCE_BUNDLE_UNSAFE_PATH")
        destination.mkdir(mode=0o700)
        for entry in entries:
            target = destination / entry.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(entry) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(0o700 if entry.mode & 0o111 else 0o600)
    return destination


def api(path):
    # gh owns authentication and redirect handling; no credential is printed.
    data = subprocess.run(["gh", "api", "repos/" + REPOSITORY + path],
                          capture_output=True, check=True).stdout
    return json.loads(data)


def git(repository, *args):
    return subprocess.check_output(["git", "-C", str(repository), *args], text=True).strip()


def promotion_source(repository, requested_sha=None):
    parents = git(repository, "rev-list", "--parents", "-n", "1", "HEAD").split()
    require(requested_sha or len(parents) == 3, "PROMOTION_REQUIRES_MERGE_OR_EXPLICIT_SOURCE")
    source = requested_sha or parents[2]
    require(re.fullmatch(r"[0-9a-f]{40}", source) is not None, "SOURCE_SHA_INVALID")
    subprocess.run(["git", "-C", str(repository), "merge-base", "--is-ancestor", source, "HEAD"], check=True)
    tree = git(repository, "rev-parse", source + "^{tree}")
    require(tree == git(repository, "rev-parse", "HEAD^{tree}"), "PROMOTION_TREE_CHANGED_AFTER_TEST")
    return source, tree


def acquire(directory, repository, candidate_sha=None, run_id=None):
    source, tree = promotion_source(repository, candidate_sha)
    if run_id:
        run = api("/actions/runs/" + str(run_id))
    else:
        runs = api("/actions/workflows/porota-predeploy-v2.yml/runs?head_sha=" + source +
                   "&status=success&per_page=100")["workflow_runs"]
        valid = []
        for item in runs:
            try:
                validate_run(item, source)
            except ValueError:
                continue
            valid.append(item)
        require(len(valid) == 1, "SELECT_EXACT_PREDEPLOY_RUN")
        run = valid[0]
    validate_run(run, source)
    artifacts = api("/actions/runs/" + str(run["id"]) + "/artifacts?per_page=100")["artifacts"]
    matches = [item for item in artifacts if item.get("name") == "porota-predeploy-v2-" + source]
    require(len(matches) == 1, "EXACT_ARTIFACT_NOT_UNIQUE")
    artifact = matches[0]
    validate_artifact(artifact, run, source)
    directory = Path(directory)
    archive = directory.parent / (directory.name + ".zip")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with archive.open("xb") as stream:
        subprocess.run(["gh", "api", "repos/" + REPOSITORY + "/actions/artifacts/" +
                        str(artifact["id"]) + "/zip"], stdout=stream, stderr=subprocess.PIPE, check=True)
    require(archive.stat().st_size == artifact["size_in_bytes"], "ARTIFACT_SIZE_MISMATCH")
    extract_artifact(archive, directory, artifact["digest"])
    try:
        from scripts.porota_release import verify_release
    except ModuleNotFoundError:
        from porota_release import verify_release
    release = verify_release(directory, expected_sha=source, expected_tree=tree, repository=Path(repository))
    validate_release_run(release, run)
    extract_source_bundle(directory)
    receipt = {"schema": "porota.release.download.v1", "source_sha": source, "source_tree": tree,
               "image_id": release["image_id"], "run_id": run["id"], "run_attempt": run["run_attempt"],
               "artifact_id": artifact["id"], "artifact_digest": artifact["digest"],
               "deployment_sha": git(repository, "rev-parse", "HEAD")}
    (directory.parent / "download.json").write_text(json.dumps(receipt, sort_keys=True) + "\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--candidate-sha")
    parser.add_argument("--run-id", type=int)
    args = parser.parse_args()
    print(json.dumps(acquire(args.directory, args.repository, args.candidate_sha, args.run_id), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError, KeyError, zipfile.BadZipFile, tarfile.TarError) as error:
        reason = str(error) if isinstance(error, ValueError) else type(error).__name__
        print(json.dumps({"status": "BLOQUEADO", "reason": reason}))
        raise SystemExit(1)
