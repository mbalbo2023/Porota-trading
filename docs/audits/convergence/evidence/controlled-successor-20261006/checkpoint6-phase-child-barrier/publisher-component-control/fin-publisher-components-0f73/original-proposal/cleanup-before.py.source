#!/usr/bin/env python3
"""Clean only the current Predeploy runner's proven private resources.

This is runner cleanup, never host deployment or global Docker housekeeping.
The candidate tag remains part of the immutable artifact contract. Ownership
requires this run's marker, UUID, complete labels and observed image identity.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
import uuid

SCHEMA = "rc6.predeploy-private-owner.v1"
RECEIPT_SCHEMA = "rc6.predeploy-scoped-cleanup.v1"
REPOSITORY = "mbalbo2023/Porota-trading"
WORKFLOW = ".github/workflows/porota-predeploy-v2.yml"
MARKER = ".porota-predeploy-owner.json"
SOURCE = "POROTA_SOURCE_PROVENANCE.json"
MIN_FREE_BYTES = 2 * 1024**3
MIN_FREE_INODE_PERCENT = 10
MAX_SECONDS = 180
MAX_FILES = 100_000
MAX_DOCKER_RESOURCES = 64
SHA = re.compile(r"[0-9a-f]{40}")
HASH = re.compile(r"[0-9a-f]{64}")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
OWNER = re.compile(r"[0-9a-f]{32}")
POSITIVE = re.compile(r"[1-9][0-9]*")


class CleanupRejected(ValueError):
    pass


def require(condition, signature):
    if not condition:
        raise CleanupRejected(signature)


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def decode(raw):
    def unique(pairs):
        output = {}
        for key, value in pairs:
            require(key not in output, "CONTROL_DUPLICATE_KEY")
            output[key] = value
        return output
    return json.loads(raw, object_pairs_hook=unique)


def safe_absolute(path):
    path = Path(path)
    require(path.is_absolute() and path.as_posix() != "/"
            and not any(part in {".", ".."} for part in path.parts),
            "PATH_NOT_CANONICAL_ABSOLUTE")
    require(os.path.normpath(str(path)) == str(path), "PATH_NOT_CANONICAL_ABSOLUTE")
    return path


@contextmanager
def directory(path, *, readable=False):
    """Resolve every component with NOFOLLOW; do not resolve symlink parents."""
    path = safe_absolute(path)
    descriptor = os.open("/", os.O_PATH | os.O_DIRECTORY)
    try:
        for index, part in enumerate(path.parts[1:]):
            last = index == len(path.parts) - 2
            flags = os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            flags |= os.O_RDONLY | os.O_NOATIME if last and readable else os.O_PATH
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        yield descriptor
    finally:
        os.close(descriptor)


def identity(value):
    return [value.st_dev, value.st_ino, value.st_uid, value.st_gid,
            stat.S_IMODE(value.st_mode), value.st_nlink, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns]


def mount_id(descriptor):
    # st_dev also matches same-filesystem bind mounts. Linux's fdinfo identifies
    # the actual mount of the opened object, independent of a pathname alias.
    information = os.open("/proc/self/fdinfo/" + str(descriptor),
                          os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        raw = os.read(information, 4096)
        require(len(raw) < 4096, "MOUNT_ID_INFORMATION_LIMIT")
        matches = re.findall(rb"^mnt_id:\s*([0-9]+)$", raw, re.MULTILINE)
        require(len(matches) == 1, "MOUNT_ID_UNAVAILABLE")
        return int(matches[0])
    finally:
        os.close(information)


def require_member_mount(parent, name, expected):
    descriptor = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
                         dir_fd=parent)
    try:
        require(mount_id(descriptor) == expected, "PRIVATE_NAMESPACE_MOUNT")
        return os.fstat(descriptor)
    finally:
        os.close(descriptor)


def private_stat(value, *, regular=True):
    require((stat.S_ISREG(value.st_mode) if regular else stat.S_ISDIR(value.st_mode))
            and value.st_uid == os.geteuid(), "RESOURCE_CUSTODY_INVALID")
    require(not regular or value.st_nlink == 1, "RESOURCE_HARDLINK")


def read_file(path, *, maximum=256 * 1024, mode=None):
    path = safe_absolute(path)
    with directory(path.parent) as parent:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW
                             | os.O_NOATIME | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        try:
            before = os.fstat(descriptor)
            require(mount_id(descriptor) == mount_id(parent), "CONTROL_FILE_MOUNT")
            private_stat(before)
            require(before.st_size <= maximum, "CONTROL_SIZE_LIMIT")
            require(mode is None or stat.S_IMODE(before.st_mode) == mode,
                    "CONTROL_MODE_INVALID")
            chunks, total = [], 0
            while True:
                part = os.read(descriptor, min(1024 * 1024, maximum + 1 - total))
                if not part:
                    break
                chunks.append(part)
                total += len(part)
                require(total <= maximum, "CONTROL_SIZE_LIMIT")
            require(identity(before) == identity(os.fstat(descriptor))
                    == identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)),
                    "CONTROL_CHANGED_DURING_READ")
            return b"".join(chunks), before
        finally:
            os.close(descriptor)


def publish(path, value, *, previous=None):
    path = safe_absolute(path)
    raw = canonical(value)
    with directory(path.parent) as parent:
        if previous is not None:
            require(identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False))
                    == identity(previous), "CONTROL_CHANGED_BEFORE_WRITE")
        name = ".porota-control-" + uuid.uuid4().hex
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if previous is None:
                os.link(name, path.name, src_dir_fd=parent, dst_dir_fd=parent,
                        follow_symlinks=False)
            else:
                require(identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False))
                        == identity(previous), "CONTROL_CHANGED_BEFORE_WRITE")
                os.replace(name, path.name, src_dir_fd=parent, dst_dir_fd=parent)
            if previous is None:
                os.unlink(name, dir_fd=parent)
            sync = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                           dir_fd=parent)
            try:
                os.fsync(sync)
            finally:
                os.close(sync)
        finally:
            try:
                os.unlink(name, dir_fd=parent)
            except FileNotFoundError:
                pass


def validate_context(context):
    require(type(context) is dict and set(context) == {
        "repository", "workflow_path", "event", "run_id", "run_attempt",
        "candidate_sha", "candidate_tree"}, "OWNER_CONTEXT_FIELDS")
    require(context["repository"] == REPOSITORY and context["workflow_path"] == WORKFLOW
            and context["event"] == "pull_request", "OWNER_WORKFLOW_MISMATCH")
    require(all(type(context[key]) is str and POSITIVE.fullmatch(context[key])
                for key in ("run_id", "run_attempt")), "OWNER_RUN_INVALID")
    require(all(type(context[key]) is str and SHA.fullmatch(context[key])
                for key in ("candidate_sha", "candidate_tree")), "OWNER_CANDIDATE_INVALID")


def expected_labels(control):
    context = control["context"]
    labels = {
        "porota.predeploy": "v2",
        "porota.predeploy.owner": control["owner_uuid"],
        "porota.predeploy.run-id": context["run_id"],
        "porota.predeploy.run-attempt": context["run_attempt"],
        "porota.commit": context["candidate_sha"],
        "porota.tree": context["candidate_tree"],
    }
    if control["source_manifest"] is not None:
        labels["porota.source-manifest-sha256"] = control["source_manifest"]["sha256"]
    return labels


def marker_payload(control):
    return {key: control[key] for key in (
        "schema", "context", "owner_uuid", "owner_uid", "repo_root",
        "runner_temp", "private_root", "receipt_path", "image_ref",
        "root_identity", "root_mount_id", "measurement_paths")}


def validate_marker(control):
    root = Path(control["private_root"])
    with directory(root) as descriptor:
        details = os.fstat(descriptor)
        private_stat(details, regular=False)
        require(stat.S_IMODE(details.st_mode) == 0o700
                and [details.st_dev, details.st_ino, details.st_uid, details.st_gid]
                == control["root_identity"], "PRIVATE_ROOT_CUSTODY_CHANGED")
        require(mount_id(descriptor) == control["root_mount_id"], "PRIVATE_ROOT_MOUNT")
    raw, details = read_file(root / MARKER, mode=0o600)
    require(raw == canonical(marker_payload(control))
            and hashlib.sha256(raw).hexdigest() == control["marker_sha256"]
            and identity(details) == control["marker_identity"],
            "PRIVATE_ROOT_MARKER_CHANGED")


def docker(*args, timeout):
    result = subprocess.run(
        ["docker", "--host", "unix:///var/run/docker.sock", *args],
        capture_output=True, text=True, timeout=timeout, check=False)
    require(result.returncode == 0, "DOCKER_COMMAND_FAILED")
    require(len(result.stdout.encode()) <= 2 * 1024**2, "DOCKER_OUTPUT_LIMIT")
    return result.stdout.strip()


def budget(deadline, clock):
    remaining = deadline - clock()
    require(remaining > 0, "CLEANUP_DEADLINE_EXCEEDED")
    return remaining


def call(run, deadline, clock, *args):
    return run(*args, timeout=min(10, budget(deadline, clock)))


def ids(raw, pattern):
    result = raw.splitlines() if raw else []
    require(len(result) <= MAX_DOCKER_RESOURCES and len(result) == len(set(result))
            and all(pattern.fullmatch(value) for value in result),
            "DOCKER_INVENTORY_INVALID")
    return result


def inspect_one(run, deadline, clock, kind, identifier):
    result = decode(call(run, deadline, clock, kind, "inspect", identifier))
    require(type(result) is list and len(result) == 1
            and type(result[0]) is dict, "DOCKER_INSPECTION_INVALID")
    return result[0]


def verify_image(control, inspected, identifier):
    require(inspected.get("Id") == identifier and IMAGE_ID.fullmatch(identifier),
            "OWNER_IMAGE_ID_MISMATCH")
    configuration = inspected.get("Config")
    require(type(configuration) is dict, "OWNER_IMAGE_CONFIG_INVALID")
    labels = configuration.get("Labels")
    require(type(labels) is dict
            and all(labels.get(key) == value for key, value in expected_labels(control).items()),
            "OWNER_IMAGE_LABEL_MISMATCH")
    tags = inspected.get("RepoTags")
    digests = inspected.get("RepoDigests")
    require(type(tags) is list and tags in ([], [control["image_ref"]])
            and digests in (None, []), "SHARED_IMAGE_RETAINED")


def list_images(control, run, deadline, clock):
    return ids(call(run, deadline, clock, "image", "ls", "--quiet", "--no-trunc",
                    "--filter", "label=porota.predeploy.owner=" + control["owner_uuid"]),
               IMAGE_ID)


def tag_images(control, run, deadline, clock):
    return ids(call(run, deadline, clock, "image", "ls", "--quiet", "--no-trunc",
                    "--filter", "reference=" + control["image_ref"]), IMAGE_ID)


def measure(paths):
    result = {}
    for path in paths:
        path = safe_absolute(path)
        with directory(path) as descriptor:
            details = os.fstat(descriptor)
            usage = os.fstatvfs(descriptor)
        key = str(details.st_dev)
        row = result.setdefault(key, {
            "filesystem_device": details.st_dev, "measured_paths": [],
            "free_bytes": usage.f_bavail * usage.f_frsize,
            "free_inodes": usage.f_favail, "total_inodes": usage.f_files})
        row["measured_paths"].append(str(path))
    return result


def capacity_ok(metrics):
    return bool(metrics) and all(
        type(row["free_bytes"]) is int and row["free_bytes"] >= MIN_FREE_BYTES
        and type(row["free_inodes"]) is int and type(row["total_inodes"]) is int
        and row["total_inodes"] > 0
        and row["free_inodes"] * 100 >= row["total_inodes"] * MIN_FREE_INODE_PERCENT
        for row in metrics.values())


def prepare(repo, runner_temp, context, *, run=docker, clock=time.monotonic,
            probe=measure):
    validate_context(context)
    repo, runner_temp = safe_absolute(repo), safe_absolute(runner_temp)
    require(repo != runner_temp and runner_temp not in repo.parents
            and repo not in runner_temp.parents, "PRIVATE_ROOT_OVERLAPS_SOURCE")
    for path in (repo, runner_temp):
        with directory(path) as descriptor:
            private_stat(os.fstat(descriptor), regular=False)
    require(not os.path.lexists(repo / SOURCE), "SOURCE_MANIFEST_ALREADY_PRESENT")
    owner = uuid.uuid4().hex
    stem = "porota-predeploy-" + context["run_id"] + "-" + context["run_attempt"]
    scope = runner_temp / (stem + ".owner.json")
    receipt = runner_temp / (stem + ".cleanup.json")
    root = runner_temp / (stem + "-" + owner)
    control = {
        "schema": SCHEMA, "context": context, "owner_uuid": owner,
        "owner_uid": os.geteuid(), "repo_root": str(repo),
        "runner_temp": str(runner_temp), "private_root": str(root),
        "receipt_path": str(receipt),
        "image_ref": "porota-predeploy-v2:" + context["candidate_sha"],
        "image_id": None, "build_phase": "NOT_STARTED", "source_manifest": None,
    }
    deadline = clock() + MAX_SECONDS
    require(not tag_images(control, run, deadline, clock), "CANDIDATE_TAG_ALREADY_PRESENT")
    docker_root = safe_absolute(call(run, deadline, clock, "info", "--format", "{{.DockerRootDir}}"))
    control["measurement_paths"] = [str(repo), str(runner_temp), str(docker_root)]
    # The policy's 2 GiB reserve is a post-cleanup residual requirement, never
    # a new fixed start gate. Keep factual initial measurements separate.
    control["filesystem_at_initialization"] = probe(control["measurement_paths"])
    with directory(runner_temp) as parent:
        os.mkdir(root.name, mode=0o700, dir_fd=parent)
    with directory(root) as descriptor:
        details = os.fstat(descriptor)
        control["root_mount_id"] = mount_id(descriptor)
    with directory(runner_temp) as parent:
        require(control["root_mount_id"] == mount_id(parent), "PRIVATE_ROOT_MOUNT")
    control["root_identity"] = [details.st_dev, details.st_ino, details.st_uid, details.st_gid]
    publish(root / MARKER, marker_payload(control))
    marker_raw, marker_stat = read_file(root / MARKER, mode=0o600)
    control["marker_sha256"] = hashlib.sha256(marker_raw).hexdigest()
    control["marker_identity"] = identity(marker_stat)
    publish(scope, control)
    return scope, control


def load(scope, *, context=None):
    raw, details = read_file(scope, mode=0o600)
    control = decode(raw)
    require(type(control) is dict and control.get("schema") == SCHEMA, "OWNER_SCHEMA_INVALID")
    validate_context(control.get("context"))
    require(context is None or control["context"] == context, "OWNER_EXECUTION_MISMATCH")
    require(type(control.get("owner_uuid")) is str and OWNER.fullmatch(control["owner_uuid"])
            and type(control.get("owner_uid")) is int and control["owner_uid"] == os.geteuid(),
            "OWNER_IDENTITY_INVALID")
    temporary = safe_absolute(control["runner_temp"])
    root = safe_absolute(control["private_root"])
    stem = "porota-predeploy-" + control["context"]["run_id"] + "-" + control["context"]["run_attempt"]
    require(safe_absolute(scope) == temporary / (stem + ".owner.json")
            and root == temporary / (stem + "-" + control["owner_uuid"])
            and safe_absolute(control["receipt_path"]) == temporary / (stem + ".cleanup.json"),
            "OWNER_PRIVATE_PATH_MISMATCH")
    require(control["image_ref"] == "porota-predeploy-v2:" + control["context"]["candidate_sha"]
            and control["build_phase"] in {"NOT_STARTED", "STARTED", "CAPTURED"},
            "OWNER_BUILD_STATE_INVALID")
    require(control["image_id"] is None or (
        type(control["image_id"]) is str and IMAGE_ID.fullmatch(control["image_id"])
        and control["build_phase"] == "CAPTURED"), "OWNER_IMAGE_STATE_INVALID")
    validate_marker(control)
    return control, details


def claim_source(scope, path, expected_sha, *, context=None):
    control, details = load(scope, context=context)
    path = safe_absolute(path)
    require(path == Path(control["repo_root"]) / SOURCE
            and type(expected_sha) is str and HASH.fullmatch(expected_sha)
            and control["source_manifest"] is None
            and control["build_phase"] == "NOT_STARTED", "SOURCE_CLAIM_INVALID")
    raw, source_stat = read_file(path, maximum=16 * 1024**2, mode=0o644)
    require(hashlib.sha256(raw).hexdigest() == expected_sha, "SOURCE_CLAIM_HASH_MISMATCH")
    control["source_manifest"] = {"path": str(path), "sha256": expected_sha,
                                  "identity": identity(source_stat)}
    publish(scope, control, previous=details)


def begin_build(scope, *, context=None, run=docker, clock=time.monotonic):
    control, details = load(scope, context=context)
    require(control["build_phase"] == "NOT_STARTED"
            and control["source_manifest"] is not None, "BUILD_PHASE_INVALID")
    require(not tag_images(control, run, clock() + MAX_SECONDS, clock),
            "CANDIDATE_TAG_ALREADY_PRESENT")
    control["build_phase"] = "STARTED"
    publish(scope, control, previous=details)


def claim_image(scope, identifier, *, context=None, run=docker, clock=time.monotonic):
    control, details = load(scope, context=context)
    require(control["build_phase"] == "STARTED"
            and type(identifier) is str and IMAGE_ID.fullmatch(identifier),
            "IMAGE_CLAIM_INVALID")
    deadline = clock() + MAX_SECONDS
    verify_image(control, inspect_one(run, deadline, clock, "image", identifier), identifier)
    require(list_images(control, run, deadline, clock) == [identifier]
            and tag_images(control, run, deadline, clock) == [identifier],
            "IMAGE_CLAIM_INVENTORY_MISMATCH")
    control["image_id"] = identifier
    control["build_phase"] = "CAPTURED"
    publish(scope, control, previous=details)


def inventory(control, *, deadline, clock):
    root = Path(control["private_root"])
    rows = {}
    with directory(root, readable=True) as descriptor:
        current = os.fstat(descriptor)
        private_stat(current, regular=False)
        require(stat.S_IMODE(current.st_mode) == 0o700
                and [current.st_dev, current.st_ino, current.st_uid, current.st_gid]
                == control["root_identity"], "PRIVATE_ROOT_CUSTODY_CHANGED")
        require(mount_id(descriptor) == control["root_mount_id"], "PRIVATE_ROOT_MOUNT")
        raw, marker_stat = read_file(root / MARKER, mode=0o600)
        require(hashlib.sha256(raw).hexdigest() == control["marker_sha256"]
                and identity(marker_stat) == control["marker_identity"],
                "PRIVATE_ROOT_MARKER_CHANGED")
        def visit(parent, relative):
            budget(deadline, clock)
            parent_before = identity(os.fstat(parent))
            names = os.listdir(parent)
            require(len(rows) + len(names) <= MAX_FILES, "PRIVATE_NAMESPACE_LIMIT")
            for name in sorted(names):
                budget(deadline, clock)
                child = os.stat(name, dir_fd=parent, follow_symlinks=False)
                require(identity(require_member_mount(parent, name, control["root_mount_id"]))
                        == identity(child), "PRIVATE_NAMESPACE_CHANGED")
                require(child.st_dev == current.st_dev and child.st_uid == os.geteuid(),
                        "PRIVATE_NAMESPACE_FOREIGN_OWNER_OR_MOUNT")
                row_name = (relative / name).as_posix()
                require(stat.S_ISREG(child.st_mode) or stat.S_ISDIR(child.st_mode),
                        "PRIVATE_NAMESPACE_ALIAS_OR_SPECIAL")
                if stat.S_ISREG(child.st_mode):
                    private_stat(child)
                rows[row_name] = {
                    "identity": identity(child), "directory": stat.S_ISDIR(child.st_mode),
                    "allocated_bytes": child.st_blocks * 512}
                if stat.S_ISDIR(child.st_mode):
                    nested = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                                     | os.O_NOATIME | os.O_CLOEXEC, dir_fd=parent)
                    try:
                        require(identity(os.fstat(nested)) == identity(child),
                                "PRIVATE_NAMESPACE_CHANGED")
                        visit(nested, relative / name)
                    finally:
                        os.close(nested)
                require(identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
                        == identity(child), "PRIVATE_NAMESPACE_CHANGED")
            require(identity(os.fstat(parent)) == parent_before, "PRIVATE_NAMESPACE_CHANGED")
        visit(descriptor, Path("."))
    return rows


def remove_private(control, rows, *, deadline, clock):
    root = Path(control["private_root"])
    # Reverify the entire namespace before deleting the first file.
    require(inventory(control, deadline=deadline, clock=clock) == rows,
            "PRIVATE_NAMESPACE_CHANGED_BEFORE_DELETE")
    with directory(root, readable=True) as root_fd:
        def remove(parent, relative):
            require(mount_id(parent) == control["root_mount_id"], "PRIVATE_NAMESPACE_MOUNT")
            for name in sorted(os.listdir(parent)):
                budget(deadline, clock)
                key = (relative / name).as_posix()
                require(key in rows, "PRIVATE_NAMESPACE_CHANGED_BEFORE_DELETE")
                row = rows[key]
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                require(identity(require_member_mount(parent, name, control["root_mount_id"]))
                        == identity(current), "PRIVATE_RESOURCE_CHANGED")
                require(identity(current) == row["identity"], "PRIVATE_RESOURCE_CHANGED")
                if row["directory"]:
                    nested = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                                     | os.O_NOATIME | os.O_CLOEXEC, dir_fd=parent)
                    try:
                        require(identity(os.fstat(nested)) == row["identity"],
                                "PRIVATE_RESOURCE_CHANGED")
                        remove(nested, relative / name)
                        require(not os.listdir(nested), "PRIVATE_DIRECTORY_NOT_EMPTY")
                        final = os.stat(name, dir_fd=parent, follow_symlinks=False)
                        require([final.st_dev, final.st_ino, final.st_uid,
                                 final.st_gid, stat.S_IMODE(final.st_mode)]
                                == row["identity"][:5], "PRIVATE_DIRECTORY_CHANGED")
                    finally:
                        os.close(nested)
                    os.rmdir(name, dir_fd=parent)
                else:
                    os.unlink(name, dir_fd=parent)
        remove(root_fd, Path("."))
        require(not os.listdir(root_fd), "PRIVATE_ROOT_NOT_EMPTY")
        with directory(root.parent) as parent:
            require([os.stat(root.name, dir_fd=parent, follow_symlinks=False).st_dev,
                     os.stat(root.name, dir_fd=parent, follow_symlinks=False).st_ino]
                    == control["root_identity"][:2], "PRIVATE_ROOT_CHANGED_BEFORE_DELETE")
            os.rmdir(root.name, dir_fd=parent)
    require(not os.path.lexists(root), "PRIVATE_ROOT_REMAINS")


def validate_source(control):
    claim = control["source_manifest"]
    path = Path(control["repo_root"]) / SOURCE
    if claim is None:
        require(not os.path.lexists(path), "UNCLAIMED_SOURCE_MANIFEST_RETAINED")
        return None
    require(claim["path"] == str(path), "SOURCE_MANIFEST_PATH_MISMATCH")
    raw, details = read_file(path, maximum=16 * 1024**2, mode=0o644)
    require(identity(details) == claim["identity"]
            and hashlib.sha256(raw).hexdigest() == claim["sha256"],
            "SOURCE_MANIFEST_CUSTODY_CHANGED")
    return details


def cleanup(scope, *, context=None, run=docker, clock=time.monotonic, probe=measure):
    started = clock()
    deadline = started + MAX_SECONDS
    report = {
        "schema": RECEIPT_SCHEMA, "status": "RED", "failures": [],
        "removed_container_ids": [], "removed_image_ids": [],
        "private_files_removed": 0, "private_directories_removed": 0,
        "source_manifest_removed": False,
        "global_prune": "NOT_CALLED", "ancestor_search": "NOT_CALLED",
        "foreign_resources": "NOT_INSPECTED_NOT_REMOVED",
        "scope": "CURRENT_RUN_PRIVATE_RUNNER_RESOURCES_ONLY",
        "free_space_measurement": "FILESYSTEM_FREE_DELTA_OBSERVED_NOT_CAUSAL_ATTRIBUTION",
    }
    control = None
    try:
        control, _ = load(scope, context=context)
        report["owner"] = {key: control[key] for key in (
            "context", "owner_uuid", "owner_uid", "image_ref", "image_id", "build_phase")}
        report["filesystem_before"] = probe(control["measurement_paths"])
        rows = inventory(control, deadline=deadline, clock=clock)
        report["private_allocated_bytes_before"] = sum(row["allocated_bytes"] for row in rows.values())
        source_details = validate_source(control)
        images = list_images(control, run, deadline, clock)
        require(len(images) <= 1, "MULTIPLE_OWN_IMAGES_RETAINED")
        if images:
            require(control["build_phase"] in {"STARTED", "CAPTURED"}
                    and control["source_manifest"] is not None, "UNEXPECTED_OWN_IMAGE_RETAINED")
            require(control["image_id"] is None or images == [control["image_id"]],
                    "OBSERVED_IMAGE_ID_MISMATCH")
            verify_image(control, inspect_one(run, deadline, clock, "image", images[0]), images[0])
        tagged = tag_images(control, run, deadline, clock)
        require(not tagged or tagged == images, "CANDIDATE_TAG_FOREIGN_OR_UNBOUND")
        containers = ids(call(run, deadline, clock, "container", "ls", "--all",
            "--quiet", "--no-trunc", "--filter",
            "label=porota.predeploy.owner=" + control["owner_uuid"]), CONTAINER_ID)
        require(not containers or bool(images), "OWN_CONTAINER_WITHOUT_BOUND_IMAGE")
        # Inspect every owned container before any removal. Never inspect a
        # foreign container merely because it references the same image.
        for identifier in containers:
            inspected = inspect_one(run, deadline, clock, "container", identifier)
            configuration = inspected.get("Config")
            require(type(configuration) is dict, "CONTAINER_OWNER_CONFIG_INVALID")
            labels = configuration.get("Labels")
            require(inspected.get("Id") == identifier and inspected.get("Image") == images[0]
                    and type(labels) is dict
                    and all(labels.get(key) == value for key, value in expected_labels(control).items())
                    and inspected.get("Mounts") == [], "CONTAINER_OWNER_BINDING_INVALID")
        for identifier in containers:
            call(run, deadline, clock, "container", "rm", "--force", identifier)
            report["removed_container_ids"].append(identifier)
        for identifier in images:
            # No --force and no pruning of parent images: references held by
            # another owner make Docker refuse, leaving shared data intact.
            call(run, deadline, clock, "image", "rm", "--no-prune", identifier)
            report["removed_image_ids"].append(identifier)
        require(not list_images(control, run, deadline, clock)
                and not tag_images(control, run, deadline, clock)
                and not call(run, deadline, clock, "container", "ls", "--all",
                    "--quiet", "--no-trunc", "--filter",
                    "label=porota.predeploy.owner=" + control["owner_uuid"]),
                "OWN_DOCKER_RESOURCES_REMAIN")
        if source_details is not None:
            require(identity(validate_source(control)) == identity(source_details),
                    "SOURCE_MANIFEST_CHANGED_BEFORE_DELETE")
            with directory(Path(control["repo_root"])) as parent:
                require(identity(os.stat(SOURCE, dir_fd=parent, follow_symlinks=False))
                        == identity(source_details), "SOURCE_MANIFEST_CHANGED_BEFORE_DELETE")
                os.unlink(SOURCE, dir_fd=parent)
            require(not os.path.lexists(Path(control["repo_root"]) / SOURCE),
                    "SOURCE_MANIFEST_REMAINS")
            report["source_manifest_removed"] = True
        remove_private(control, rows, deadline=deadline, clock=clock)
        report["private_files_removed"] = sum(not row["directory"] for row in rows.values())
        report["private_directories_removed"] = sum(row["directory"] for row in rows.values())
        report["private_root_absent"] = True
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        report["failures"].append(str(error) if isinstance(error, CleanupRejected)
                                   else type(error).__name__)
    if control is not None:
        try:
            report["filesystem_after"] = probe(control["measurement_paths"])
            before, after = report["filesystem_before"], report["filesystem_after"]
            require(set(before) == set(after), "MEASUREMENT_FILESYSTEM_CHANGED")
            report["filesystem_deltas"] = {
                key: {"free_byte_delta": after[key]["free_bytes"] - before[key]["free_bytes"],
                      "reclaimed_free_bytes_observed": max(
                          0, after[key]["free_bytes"] - before[key]["free_bytes"]),
                      "free_inode_delta": after[key]["free_inodes"] - before[key]["free_inodes"]}
                for key in after}
            require(capacity_ok(after), "POST_CLEANUP_CAPACITY_RED")
            budget(deadline, clock)
        except (ValueError, OSError, KeyError) as error:
            report["failures"].append(str(error) if isinstance(error, CleanupRejected)
                                       else type(error).__name__)
    report["elapsed_wall_seconds"] = clock() - started
    report["status"] = "GREEN" if not report["failures"] else "RED"
    return report


def execution_context(repo, environ):
    env = dict(os.environ, GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1",
               GIT_TERMINAL_PROMPT="0")
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    head = subprocess.check_output(["git", "--no-replace-objects", "-C", str(repo),
                                   "rev-parse", "HEAD"], text=True, env=env, timeout=10).strip()
    tree = subprocess.check_output(["git", "--no-replace-objects", "-C", str(repo),
                                   "rev-parse", "HEAD^{tree}"], text=True, env=env, timeout=10).strip()
    require(head == environ.get("CANDIDATE_SHA"), "CANDIDATE_CHECKOUT_MISMATCH")
    require(environ.get("GITHUB_WORKFLOW_REF", "").split("@")[0]
            == REPOSITORY + "/" + WORKFLOW, "OWNER_WORKFLOW_REF_MISMATCH")
    context = {
        "repository": environ.get("GITHUB_REPOSITORY"),
        "workflow_path": WORKFLOW, "event": environ.get("GITHUB_EVENT_NAME"),
        "run_id": environ.get("GITHUB_RUN_ID"), "run_attempt": environ.get("GITHUB_RUN_ATTEMPT"),
        "candidate_sha": head, "candidate_tree": tree}
    validate_context(context)
    require(environ.get("CANDIDATE_TREE", tree) == tree, "CANDIDATE_TREE_MISMATCH")
    return context


def cli_owner(scope, repo, context, environ):
    control, details = load(scope, context=context)
    require(Path(control["repo_root"]) == safe_absolute(repo)
            and Path(control["runner_temp"]) == safe_absolute(environ["RUNNER_TEMP"])
            and control["owner_uuid"] == environ.get("POROTA_PREDEPLOY_OWNER_UUID"),
            "OWNER_CLI_ROOT_OR_UUID_MISMATCH")
    require(safe_absolute(scope) == safe_absolute(environ["POROTA_PREDEPLOY_OWNER"]),
            "OWNER_CLI_SCOPE_MISMATCH")
    return control, details


def reserve_receipt(path, control):
    require(safe_absolute(path) == Path(control["receipt_path"]),
            "CLEANUP_RECEIPT_PATH_MISMATCH")
    # Exclusive, fsynced publication before mutations verifies output custody
    # and rejects aliases/existing files without deleting anything first.
    publish(path, {"schema": RECEIPT_SCHEMA, "status": "PENDING",
                   "context": control["context"], "owner_uuid": control["owner_uuid"]})
    _, details = read_file(path, mode=0o600)
    return details


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "claim-source", "begin-build", "claim-image", "cleanup"])
    parser.add_argument("--scope", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--image-id")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    started = time.monotonic()
    old_handler = signal.getsignal(signal.SIGALRM)
    def expired(_signum, _frame):
        raise CleanupRejected("CLEANUP_DEADLINE_EXCEEDED")
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(MAX_SECONDS)
    try:
        repo = Path.cwd()
        context = execution_context(repo, os.environ)
        if args.command == "init":
            scope, control = prepare(repo, Path(os.environ["RUNNER_TEMP"]), context)
            env_path = Path(os.environ["GITHUB_ENV"])
            with env_path.open("a", encoding="utf-8") as output:
                for key, value in {
                    "POROTA_PREDEPLOY_OWNER": scope, "POROTA_PREDEPLOY_TMP": control["private_root"],
                    "POROTA_PREDEPLOY_OWNER_UUID": control["owner_uuid"],
                    "POROTA_PREDEPLOY_CLEANUP_RECEIPT": control["receipt_path"],
                    "CANDIDATE_TREE": context["candidate_tree"]}.items():
                    output.write(key + "=" + str(value) + "\n")
            print("POROTA_PREDEPLOY_PRIVATE_OWNER=GREEN", flush=True)
        else:
            control, _ = cli_owner(args.scope, repo, context, os.environ)
            if args.command == "claim-source":
                claim_source(args.scope, args.source_manifest.absolute(), args.sha256, context=context)
            elif args.command == "begin-build":
                begin_build(args.scope, context=context)
            elif args.command == "claim-image":
                claim_image(args.scope, args.image_id, context=context)
            else:
                require(args.receipt is not None, "CLEANUP_RECEIPT_REQUIRED")
                reserved = reserve_receipt(args.receipt, control)
                report = cleanup(args.scope, context=context)
                budget(started + MAX_SECONDS, time.monotonic)
                publish(args.receipt, report, previous=reserved)
                budget(started + MAX_SECONDS, time.monotonic)
                print(json.dumps(report, sort_keys=True), flush=True)
                budget(started + MAX_SECONDS, time.monotonic)
                print("POROTA_PREDEPLOY_LOCAL_CLEANUP=" + report["status"], flush=True)
                budget(started + MAX_SECONDS, time.monotonic)
                return 0 if report["status"] == "GREEN" else 1
        budget(started + MAX_SECONDS, time.monotonic)
        return 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        signature = str(error) if isinstance(error, CleanupRejected) else type(error).__name__
        print("POROTA_PREDEPLOY_LOCAL_CLEANUP=RED|" + signature, flush=True)
        return 1
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


if __name__ == "__main__":
    raise SystemExit(main())
