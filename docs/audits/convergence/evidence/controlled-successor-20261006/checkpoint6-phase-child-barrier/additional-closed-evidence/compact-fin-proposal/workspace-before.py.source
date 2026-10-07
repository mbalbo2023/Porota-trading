#!/usr/bin/env python3
"""Construct a fresh native venv and retain adversarial pytest fixtures explicitly.

The strict Predeploy cleanup namespace continues to prohibit every alias and
special member. Only CPython's known lib64 -> lib link is removed during this
helper's own new-venv construction. Pytest fixtures live in a separate, owned
namespace, are measured without reading payloads or following links, and are
retained for the ephemeral Actions runner's teardown. This helper never cleans
that namespace, Docker resources, a host, or an existing environment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import runpy
import shutil
import stat
import subprocess
import sys
import threading
import time
import venv

ROOT = Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import porota_predeploy_cleanup as cleanup

SCHEMA = "rc6.predeploy-test-workspace.v1"
RETENTION_SCHEMA = "rc6.predeploy-retained-pytest-fixtures.v1"
CLAIM = "porota-test-workspace.json"
MARKER = ".porota-pytest-owner.json"
RETAINED = "porota-pytest-retained.json"
FIN = "porota-pytest-owned-fin.json"
LAUNCH = "porota-pytest-launch-intent.json"
PROGRESS = "porota-pytest-progress.json"
NATIVE_LOG = "porota-pytest-native.log"
POSTREAD = "porota-pytest-postread.json"
DRIVER = "scripts/rc6_controlled_native_child_manager.py"
ORIGINAL_DRIVER_SHA256 = "55325b3108e175a42b87ebe544fd307fa45ffd29b6f7ab471443803ad9ba53b8"
FIELDS = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size",
          "st_blocks", "st_atime_ns", "st_mtime_ns", "st_ctime_ns")


def phase_controls(phase):
    cleanup.require(phase in ("collection", "execution"), "PYTEST_PRODUCER_PHASE_REQUIRED")
    if phase == "execution":
        return {"launch": LAUNCH, "fin": FIN, "progress": PROGRESS,
                "log": NATIVE_LOG, "retained": RETAINED, "postread": POSTREAD}
    return {"launch": "porota-pytest-collection-launch-intent.json",
            "fin": "porota-pytest-collection-owned-fin.json",
            "progress": "porota-pytest-collection-progress.json",
            "log": "porota-test-collection.txt",
            "retained": "porota-pytest-collection-retained.json",
            "postread": "porota-pytest-collection-postread.json"}


def attributes(value):
    return {field: getattr(value, field) for field in FIELDS}


def bound_directory(descriptor, control, *, mode):
    value = os.fstat(descriptor)
    cleanup.require(stat.S_ISDIR(value.st_mode) and value.st_uid == control["owner_uid"]
                    and value.st_gid == control["root_identity"][3]
                    and stat.S_IMODE(value.st_mode) == mode
                    and value.st_dev == control["root_identity"][0]
                    and cleanup.mount_id(descriptor) == control["root_mount_id"],
                    "TEST_WORKSPACE_DIRECTORY_CUSTODY_INVALID")
    return value


def root_identity(value):
    return [value.st_dev, value.st_ino, value.st_uid, value.st_gid, stat.S_IMODE(value.st_mode)]


def bind_owner(scope, repo, context, environ):
    """Use the original marker/context/CLI binding before any construction."""
    control, _ = cleanup.cli_owner(scope, repo, context, environ)
    return control


def seal_fresh_lib64_link(descriptor, control, construction_identity):
    """Called only for the venv directory just created by this invocation."""
    current = bound_directory(descriptor, control, mode=0o755)
    cleanup.require(root_identity(current) == construction_identity,
                    "FRESH_VENV_CONSTRUCTION_IDENTITY_CHANGED")
    alias = os.stat("lib64", dir_fd=descriptor, follow_symlinks=False)
    cleanup.require(stat.S_ISLNK(alias.st_mode) and alias.st_uid == control["owner_uid"]
                    and alias.st_gid == current.st_gid and alias.st_dev == current.st_dev
                    and alias.st_nlink == 1 and alias.st_size == 3,
                    "FRESH_NATIVE_LIB64_LINK_CUSTODY_INVALID")
    alias_fd = os.open("lib64", os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor)
    lib_fd = None
    try:
        lib_fd = os.open("lib", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                         | os.O_NOATIME | os.O_CLOEXEC, dir_fd=descriptor)
        cleanup.require(attributes(os.fstat(alias_fd)) == attributes(alias)
                        and cleanup.mount_id(alias_fd) == control["root_mount_id"],
                        "FRESH_NATIVE_LIB64_LINK_IDENTITY_CHANGED")
        lib = bound_directory(lib_fd, control, mode=0o755)
        cleanup.require(attributes(lib) == attributes(os.stat("lib", dir_fd=descriptor,
                                                            follow_symlinks=False)),
                        "FRESH_NATIVE_LIB_DIRECTORY_CHANGED")
        target = os.readlink("lib64", dir_fd=descriptor)
        cleanup.require(target == "lib", "FRESH_NATIVE_LIB64_UNEXPECTED_TARGET")
        # readlink can advance this new link's atime. Record that construction
        # observation; never restore it or conceal a later custody change.
        observed = os.fstat(alias_fd)
        cleanup.require(cleanup.identity(observed) == cleanup.identity(alias)
                        and attributes(observed) == attributes(os.stat(
                            "lib64", dir_fd=descriptor, follow_symlinks=False))
                        and root_identity(os.fstat(descriptor)) == construction_identity,
                        "FRESH_NATIVE_LIB64_LINK_CHANGED_BEFORE_UNLINK")
        os.unlink("lib64", dir_fd=descriptor)
        cleanup.require(attributes(os.fstat(lib_fd)) == attributes(lib)
                        and attributes(os.stat("lib", dir_fd=descriptor, follow_symlinks=False))
                        == attributes(lib), "FRESH_NATIVE_LIB_DIRECTORY_CHANGED_AFTER_UNLINK")
        try:
            os.stat("lib64", dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise cleanup.CleanupRejected("FRESH_NATIVE_LIB64_ALIAS_REMAINS")
        os.fsync(descriptor)
        return {"target": target, "alias_before_own_read": attributes(alias),
                "alias_after_own_read_before_unlink": attributes(observed),
                "lib_directory_all11_unchanged": True,
                "removed_only_native_link_from_this_new_construction": True,
                "existing_environment_modified": False}
    finally:
        if lib_fd is not None:
            os.close(lib_fd)
        os.close(alias_fd)


def construct_private_venv(control):
    """A preexisting venv is refused before EnvBuilder can modify anything."""
    cleanup.require(sys.platform == "linux" and platform.machine() == "x86_64"
                    and sys.version_info[:2] in ((3, 11), (3, 12)),
                    "REVIEWED_NATIVE_VENV_PLATFORM_REQUIRED")
    private = Path(control["private_root"])
    with cleanup.directory(private) as parent:
        bound_directory(parent, control, mode=0o700)
        os.mkdir("venv", mode=0o755, dir_fd=parent)  # Exclusive, no reuse.
        descriptor = os.open("venv", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                             | os.O_NOATIME | os.O_CLOEXEC, dir_fd=parent)
        try:
            created = bound_directory(descriptor, control, mode=0o755)
            construction_identity = root_identity(created)
            venv.EnvBuilder(symlinks=False, with_pip=True).create(private / "venv")
            cleanup.require(root_identity(os.stat("venv", dir_fd=parent, follow_symlinks=False))
                            == construction_identity, "FRESH_VENV_DIRECTORY_REBOUND")
            removed = seal_fresh_lib64_link(descriptor, control, construction_identity)
        finally:
            os.close(descriptor)
    # The original guard, unmodified, must accept the complete strict namespace.
    rows = cleanup.inventory(control, deadline=time.monotonic() + cleanup.MAX_SECONDS,
                             clock=time.monotonic)
    return {"path": str(private / "venv"), "native_envbuilder_copies": True,
            "fresh_directory_identity": construction_identity, "native_lib64": removed,
            "original_strict_inventory_accepted": True, "strict_entries": len(rows)}


def workspace_path(control):
    context = control["context"]
    return Path(control["runner_temp"]) / ("porota-pytest-" + context["run_id"] + "-"
                                         + context["run_attempt"] + "-" + control["owner_uuid"])


def create_workspace(scope, repo, context, environ):
    control = bind_owner(scope, repo, context, environ)
    private, fixture = Path(control["private_root"]), workspace_path(control)
    claim_path = private / CLAIM
    cleanup.require(not os.path.lexists(claim_path) and not os.path.lexists(fixture)
                    and not os.path.lexists(private / "venv"), "TEST_WORKSPACE_ALREADY_EXISTS")
    cleanup.require(fixture != private and not fixture.is_relative_to(private)
                    and not private.is_relative_to(fixture), "TEST_WORKSPACE_OVERLAPS_STRICT_ROOT")
    native = construct_private_venv(control)
    with cleanup.directory(Path(control["runner_temp"])) as parent:
        # The original RUNNER_TEMP may be 755; require its actual owner/mount,
        # without changing it or inventing a new parent-mode policy.
        current = os.fstat(parent)
        cleanup.private_stat(current, regular=False)
        cleanup.require(current.st_dev == control["root_identity"][0]
                        and cleanup.mount_id(parent) == control["root_mount_id"],
                        "TEST_WORKSPACE_PARENT_MOUNT_CHANGED")
        os.mkdir(fixture.name, mode=0o700, dir_fd=parent)
    with cleanup.directory(fixture) as descriptor:
        created = bound_directory(descriptor, control, mode=0o700)
        claim = {"schema": SCHEMA, "context": context, "owner_uuid": control["owner_uuid"],
                 "owner_uid": control["owner_uid"], "strict_private_root": str(private),
                 "runner_temp": control["runner_temp"], "fixture_root": str(fixture),
                 "pytest_basetemp": str(fixture / "pytest"),
                 "pytest_phases": ["collection", "execution"],
                 "fixture_root_identity": root_identity(created),
                 "fixture_root_mount_id": cleanup.mount_id(descriptor), "venv": native,
                 "retention_policy": "EXPLICIT_RETAINED_FOR_EPHEMERAL_RUNNER_TEARDOWN",
                 "fixture_root_removed": False, "link_targets_followed": False,
                 "real_orders_sent": 0}
    cleanup.publish(fixture / MARKER, claim)
    marker_raw, marker_stat = cleanup.read_file(fixture / MARKER, mode=0o600)
    claim.update(marker_sha256=hashlib.sha256(marker_raw).hexdigest(),
                 marker_identity=cleanup.identity(marker_stat))
    cleanup.publish(claim_path, claim)
    return claim


def load_workspace(scope, repo, context, environ):
    control = bind_owner(scope, repo, context, environ)
    raw, _ = cleanup.read_file(Path(control["private_root"]) / CLAIM, mode=0o600)
    claim = cleanup.decode(raw)
    fixture = workspace_path(control)
    cleanup.require(type(claim) is dict and claim.get("schema") == SCHEMA
                    and claim.get("context") == context
                    and claim.get("owner_uuid") == control["owner_uuid"]
                    and claim.get("owner_uid") == control["owner_uid"]
                    and claim.get("strict_private_root") == control["private_root"]
                    and claim.get("runner_temp") == control["runner_temp"]
                    and claim.get("fixture_root") == str(fixture)
                    and claim.get("pytest_basetemp") == str(fixture / "pytest")
                    and claim.get("pytest_phases") == ["collection", "execution"]
                    and claim.get("fixture_root_mount_id") == control["root_mount_id"]
                    and claim.get("retention_policy") == "EXPLICIT_RETAINED_FOR_EPHEMERAL_RUNNER_TEARDOWN"
                    and claim.get("fixture_root_removed") is False,
                    "TEST_WORKSPACE_CLAIM_MISMATCH")
    with cleanup.directory(fixture) as descriptor:
        value = bound_directory(descriptor, control, mode=0o700)
        cleanup.require(root_identity(value) == claim["fixture_root_identity"],
                        "TEST_WORKSPACE_ROOT_REBOUND")
    marker_raw, marker_stat = cleanup.read_file(fixture / MARKER, mode=0o600)
    marker = cleanup.decode(marker_raw)
    expected = {key: value for key, value in claim.items()
                if key not in ("marker_sha256", "marker_identity")}
    cleanup.require(marker == expected and hashlib.sha256(marker_raw).hexdigest() == claim["marker_sha256"]
                    and cleanup.identity(marker_stat) == claim["marker_identity"],
                    "TEST_WORKSPACE_MARKER_CHANGED")
    return control, claim


def measure_retained_workspace(scope, repo, context, environ):
    """Caller must be the parent that has just proved its owned pytest FIN."""
    control, claim = load_workspace(scope, repo, context, environ)
    fixture = Path(claim["fixture_root"])
    seen, allocated, logical, entries, aliases = set(), 0, 0, 0, []
    counts = {"symlinks": 0, "hardlinked_regular_entries": 0, "special_entries": 0,
              "regular_entries": 0, "directory_entries": 0}

    def record(value, relative):
        nonlocal allocated, logical, entries
        entries += 1
        cleanup.require(entries <= cleanup.MAX_FILES, "RETAINED_TEST_NAMESPACE_LIMIT")
        key = (value.st_dev, value.st_ino)
        if key not in seen:
            seen.add(key)
            allocated += value.st_blocks * 512
            logical += value.st_size
        kind = None
        if stat.S_ISDIR(value.st_mode):
            counts["directory_entries"] += 1
        elif stat.S_ISREG(value.st_mode):
            counts["regular_entries"] += 1
            if value.st_nlink > 1:
                counts["hardlinked_regular_entries"] += 1
                kind = "hardlink"
        elif stat.S_ISLNK(value.st_mode):
            counts["symlinks"] += 1
            kind = "symlink"
        else:
            counts["special_entries"] += 1
            kind = "special"
        if kind is not None and len(aliases) < 64:
            aliases.append({"relative_path": relative, "kind": kind, "stat_fields": attributes(value),
                            "target_followed": False, "removed": False})

    def visit(parent, relative):
        before = attributes(os.fstat(parent))
        names = sorted(os.listdir(parent))
        cleanup.require(entries + len(names) <= cleanup.MAX_FILES, "RETAINED_TEST_NAMESPACE_LIMIT")
        for name in names:
            child = os.stat(name, dir_fd=parent, follow_symlinks=False)
            observed = cleanup.require_member_mount(parent, name, control["root_mount_id"])
            cleanup.require(attributes(child) == attributes(observed)
                            and child.st_dev == control["root_identity"][0]
                            and child.st_uid == control["owner_uid"],
                            "RETAINED_TEST_MEMBER_FOREIGN_OR_CHANGED")
            path = name if relative == "." else relative + "/" + name
            record(child, path)
            if stat.S_ISDIR(child.st_mode):
                nested = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                                 | os.O_NOATIME | os.O_CLOEXEC, dir_fd=parent)
                try:
                    cleanup.require(attributes(os.fstat(nested)) == attributes(child),
                                    "RETAINED_TEST_DIRECTORY_REBOUND")
                    visit(nested, path)
                finally:
                    os.close(nested)
            cleanup.require(attributes(os.stat(name, dir_fd=parent, follow_symlinks=False))
                            == attributes(child), "RETAINED_TEST_MEMBER_CHANGED")
        cleanup.require(attributes(os.fstat(parent)) == before, "RETAINED_TEST_DIRECTORY_CHANGED")

    with cleanup.directory(fixture, readable=True) as descriptor:
        value = bound_directory(descriptor, control, mode=0o700)
        record(value, ".")
        visit(descriptor, ".")
        usage = os.fstatvfs(descriptor)  # Fresh, actual filesystem of the bound root.
        free_bytes = usage.f_bavail * usage.f_frsize
        cleanup.require(usage.f_files > 0, "RETAINED_TEST_INODE_CAPACITY_UNKNOWN")
        capacity = (free_bytes >= cleanup.MIN_FREE_BYTES
                    and usage.f_favail * 100 >= usage.f_files * cleanup.MIN_FREE_INODE_PERCENT)
    adversarial = any(counts[key] for key in ("symlinks", "hardlinked_regular_entries", "special_entries"))
    return {"schema": RETENTION_SCHEMA, "context": context, "owner_uuid": claim["owner_uuid"],
            "owner_uid": claim["owner_uid"], "fixture_root": str(fixture),
            "fixture_root_identity": claim["fixture_root_identity"],
            "fixture_root_mount_id": claim["fixture_root_mount_id"],
            "classification": "RETAINED_ADVERSARIAL_FIXTURES" if adversarial else "RETAINED_PRIVATE_TEST_FIXTURES",
            "retention_policy": claim["retention_policy"], "fixture_root_removed": False,
            "allocated_bytes_unique_physical_inodes": allocated, "logical_bytes_unique_physical_inodes": logical,
            "unique_physical_inodes": len(seen), "namespace_entries_including_root": entries,
            "counts": counts, "first_64_adversarial_members": aliases,
            "hardlinks_deduplicated_by": "ST_DEV_ST_INO", "payload_files_read": 0,
            "link_targets_followed": False, "resources_removed": 0,
            "fresh_filesystem": {"filesystem_device": value.st_dev, "free_bytes": free_bytes,
                                 "free_inodes": usage.f_favail, "total_inodes": usage.f_files},
            "original_capacity_policy": {"min_free_bytes": cleanup.MIN_FREE_BYTES,
                                         "min_free_inode_percent": cleanup.MIN_FREE_INODE_PERCENT},
            "capacity_status": "GREEN" if capacity else "RED", "real_orders_sent": 0,
            "scope": "MEASURED_RETAINED_TEST_FIXTURES_ONLY; NOT_CLEANUP_OR_GLOBAL_CUSTODY"}


def native_manager(repo, context):
    """Load the reviewed Git-pinned manager, never a source overlay."""
    path = Path(repo) / DRIVER
    raw, _ = cleanup.read_file(path, maximum=128 * 1024)
    env = dict(os.environ, GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1",
               GIT_TERMINAL_PROMPT="0")
    blob = subprocess.check_output(["git", "--no-replace-objects", "-C", str(repo),
                                   "rev-parse", context["candidate_sha"] + ":" + DRIVER],
                                  env=env, timeout=10).decode().strip()
    actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    cleanup.require(actual == blob and hashlib.sha256(raw).hexdigest() == ORIGINAL_DRIVER_SHA256,
                    "ORIGINAL_OWNED_FIN_DRIVER_BYTES_MISMATCH")
    return runpy.run_path(str(path)), {"member": DRIVER, "git_blob": blob,
                                      "sha256": hashlib.sha256(raw).hexdigest()}


def publish_launch_intent(control, context, command, provenance, initial, management_seconds, *, phase="execution"):
    """The durable, exclusive intent must exist even if Popen later crashes."""
    private = Path(control["private_root"])
    names = phase_controls(phase)
    intent = {"schema": "rc6.predeploy-pytest-launch-intent.v1", "context": context,
              "owner_uuid": control["owner_uuid"], "owner_uid": control["owner_uid"],
              "supervisor_pid": os.getpid(), "supervisor_native_tid": threading.get_native_id(),
              "supervisor_real_uid": os.getuid(), "supervisor_effective_uid": os.geteuid(),
              "phase": phase,
              "command": command, "original_manager_source": provenance,
              "management_seconds": management_seconds, "original_management_maximum_seconds": 5400,
              "terminate_grace_seconds": 2, "progress_poll_seconds": 5,
              "failed_cleanup_maximum_seconds": 5,
              "initial_actual_own_kernel": initial,
              "classification": "PYTEST_PRODUCER_LAUNCH_ATTEMPTED_NOT_ACCEPTED",
              "real_orders_sent": 0}
    raw = cleanup.canonical(intent)
    with cleanup.directory(private) as parent:
        descriptor = os.open(names["launch"], os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
        with os.fdopen(descriptor, "wb") as stream:
            current = os.fstat(stream.fileno())
            cleanup.private_stat(current)
            cleanup.require(stat.S_IMODE(current.st_mode) == 0o600
                            and current.st_dev == control["root_identity"][0]
                            and cleanup.mount_id(stream.fileno()) == control["root_mount_id"],
                            "PYTEST_LAUNCH_INTENT_CUSTODY_INVALID")
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        sync = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                       | os.O_NOATIME | os.O_CLOEXEC, dir_fd=parent)
        try:
            os.fsync(sync)
        finally:
            os.close(sync)
    observed, _ = cleanup.read_file(private / names["launch"], mode=0o600)
    cleanup.require(observed == raw, "PYTEST_LAUNCH_INTENT_CHANGED")
    return intent, hashlib.sha256(raw).hexdigest()


def recorded_original_custody_closed(kernel):
    """The exact V2 documentary predicate; no new/global kernel observation.

    The actual manager was bound before launch. Revalidating its owned control
    receipt must not open Source or any producer payload when FIN is unknown.
    """
    return (type(kernel) is dict and type(kernel.get("pid")) is int and kernel["pid"] > 0
            and kernel.get("actual_child_reaped") is True
            and kernel.get("wait4_reaped_pid") == kernel["pid"]
            and kernel.get("kernel_pre_popen_echild_verified") is True
            and kernel.get("owned_children_exhaustion_verified") is True
            and kernel.get("process_group_absent_after_reap") is True
            and kernel.get("subreaper_activation_readback_verified") is True
            and kernel.get("subreaper_restore_attempted") is True
            and kernel.get("subreaper_restoration_readback_verified") is True
            and kernel.get("remaining_owned_children") == [])


def recorded_original_phase_green(kernel):
    return (recorded_original_custody_closed(kernel) and kernel.get("returncode") == 0
            and kernel.get("timed_out") is False
            and kernel.get("late_observed_main_reap_irreversible_red") is False
            and kernel.get("kernel_wait4_zero_observed_irreversible_red") is False
            and kernel.get("residual_descendants_observed") == []
            and kernel.get("owned_group_signal_observations") == []
            and kernel.get("supervisor_errors") == []
            and type(kernel.get("wall_seconds")) in (int, float)
            and math.isfinite(kernel["wall_seconds"]) and 0 <= kernel["wall_seconds"] <= 21600
            and type(kernel.get("adopted_descendants_reaped")) is list
            and all(type(row) is dict and type(row.get("exit_code")) is int and row["exit_code"] == 0
                    for row in kernel["adopted_descendants_reaped"]))


def recorded_management_accepted(kernel, management_seconds):
    return (recorded_original_phase_green(kernel)
            and all(type(kernel.get(name)) in (int, float) and math.isfinite(kernel[name])
                    and 0 <= kernel[name] <= management_seconds
                    for name in ("wall_seconds", "actual_launch_to_pid_reap_wall_seconds"))
            and kernel.get("launcher_management_deadline_seconds") == management_seconds
            and kernel.get("owned_cleanup_management_bound_seconds") == 5)


def postread_receipt_path(control, phase="all"):
    context = control["context"]
    suffix = {"all": ".pytest-producers-postread.json", "collection": ".pytest-collection-postread.json",
              "execution": ".pytest-postread.json"}[phase]
    return Path(control["runner_temp"]) / ("porota-predeploy-" + context["run_id"] + "-"
                                         + context["run_attempt"] + suffix)


def safe_phase_postread(scope, repo, context, environ, *, phase, expected_management_seconds=5400):
    """Revalidate bounded owned controls, never Source/JUnit/DATA/log payloads."""
    control = bind_owner(scope, repo, context, environ)
    names = phase_controls(phase)
    cleanup.require(type(expected_management_seconds) is int and 60 <= expected_management_seconds <= 5400,
                    "POSTREAD_ORIGINAL_MANAGEMENT_BIND_REQUIRED")
    private = Path(control["private_root"])
    result = {"schema": "rc6.predeploy-pytest-postread-safety.v1", "context": context,
              "owner_uuid": control["owner_uuid"], "owner_uid": control["owner_uid"],
              "safe_postread": False, "classification": "UNKNOWN_OR_UNCLOSED",
              "pytest_producer_witness": False, "errors": [], "control_observations": {},
              "source_junit_data_log_payloads_read": 0, "cleanup_performed": False,
              "new_or_global_kernel_fin_asserted": False, "real_orders_sent": 0}
    result["expected_management_seconds"] = expected_management_seconds
    result["phase"] = phase

    def captured(name):
        raw, details = cleanup.read_file(private / name, maximum=256 * 1024, mode=0o600)
        value = cleanup.decode(raw)
        cleanup.require(type(value) is dict and value.get("context") == context
                        and value.get("owner_uuid") == control["owner_uuid"],
                        "PYTEST_FIN_CONTROL_CONTEXT_OR_OWNER_MISMATCH")
        # Only bounded, validated own control documents are durable here. The
        # artifact never follows a still-running producer's namespace path.
        result["control_observations"][name] = {
            "stat_fields": attributes(details), "sha256": hashlib.sha256(raw).hexdigest(),
            "raw_utf8": raw.decode("utf-8"), "payload_read": False}
        return value, hashlib.sha256(raw).hexdigest()

    path = postread_receipt_path(control, phase)
    previous = None
    try:
        if os.path.lexists(path):
            raw, details = cleanup.read_file(path, maximum=1024 * 1024, mode=0o600)
            old = cleanup.decode(raw)
            cleanup.require(type(old) is dict and old.get("schema") == result["schema"]
                            and old.get("context") == context
                            and old.get("phase") == phase
                            and old.get("owner_uuid") == control["owner_uuid"],
                            "PYTEST_POSTREAD_RECEIPT_REBOUND")
            previous = details
            if old.get("safe_postread") is not True:
                result["control_observations"] = old.get("control_observations", {})
                result["errors"] = list(old.get("errors", []))
                result["trace_presence_lstat_only"] = old.get("trace_presence_lstat_only", {})
            cleanup.require(old.get("safe_postread") is True,
                            "PREVIOUS_UNKNOWN_OR_UNCLOSED_IRREVERSIBLE_RED")
        with cleanup.directory(private) as parent:
            traces = {}
            for name in (names["launch"], names["fin"], names["progress"], names["log"]):
                try:
                    value = os.stat(name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    traces[name] = None
                else:
                    traces[name] = attributes(value)
            result["trace_presence_lstat_only"] = traces
        if all(value is None for value in traces.values()):
            result.update(safe_postread=True, classification="NO_PYTEST_LAUNCH_ATTEMPTED",
                          witness_scope="NO_PYTEST_PRODUCER_WITNESS; ORIGINAL_SCOPED_CLEANUP_ONLY")
        else:
            cleanup.require(traces[names["launch"]] is not None, "PYTEST_FIN_WITHOUT_DURABLE_LAUNCH_INTENT")
            intent, intent_sha = captured(names["launch"])
            provenance = intent.get("original_manager_source")
            cleanup.require(intent.get("schema") == "rc6.predeploy-pytest-launch-intent.v1"
                            and intent.get("classification") == "PYTEST_PRODUCER_LAUNCH_ATTEMPTED_NOT_ACCEPTED"
                            and intent.get("owner_uid") == control["owner_uid"]
                            and intent.get("phase") == phase
                            and type(intent.get("supervisor_pid")) is int and intent["supervisor_pid"] > 0
                            and type(intent.get("supervisor_native_tid")) is int and intent["supervisor_native_tid"] > 0
                            and type(intent.get("supervisor_real_uid")) is int and intent["supervisor_real_uid"] >= 0
                            and intent.get("supervisor_effective_uid") == control["owner_uid"]
                            and type(intent.get("command")) is list
                            and intent["command"][:3] == ["python", "-m", "pytest"]
                            and all(type(item) is str for item in intent["command"])
                            and ((phase == "execution" and "--collect-only" not in intent["command"]
                                  and "--basetemp=" + str(workspace_path(control) / "pytest") in intent["command"])
                                 or (phase == "collection" and "--collect-only" in intent["command"]))
                            and type(intent.get("management_seconds")) is int
                            and intent["management_seconds"] == expected_management_seconds
                            and intent.get("original_management_maximum_seconds") == 5400
                            and intent.get("terminate_grace_seconds") == 2
                            and intent.get("progress_poll_seconds") == 5
                            and intent.get("failed_cleanup_maximum_seconds") == 5
                            and type(provenance) is dict and provenance.get("member") == DRIVER
                            and provenance.get("sha256") == ORIGINAL_DRIVER_SHA256
                            and type(provenance.get("git_blob")) is str
                            and cleanup.SHA.fullmatch(provenance["git_blob"]),
                            "PYTEST_LAUNCH_INTENT_INVALID")
            cleanup.require(traces[names["fin"]] is not None, "PYTEST_LAUNCH_WITHOUT_CLOSED_FIN")
            custody, _ = captured(names["fin"])
            kernel = custody.get("kernel")
            cleanup.require(custody.get("schema") == "rc6.predeploy-pytest-owned-fin.v1"
                            and custody.get("supervisor_pid") == intent["supervisor_pid"]
                            and custody.get("phase") == phase
                            and custody.get("launch_intent_sha256") == intent_sha
                            and custody.get("original_manager_source") == provenance
                            and custody.get("owned_fin_closed") is True
                            and recorded_original_custody_closed(kernel)
                            and kernel.get("supervisor_pid") == intent["supervisor_pid"]
                            and kernel.get("supervisor_native_tid") == intent["supervisor_native_tid"]
                            and kernel.get("command") == intent["command"]
                            and custody.get("management_seconds") == expected_management_seconds
                            and kernel.get("launcher_management_deadline_seconds") == expected_management_seconds
                            and kernel.get("owned_cleanup_management_bound_seconds") == 5
                            and kernel.get("supervisor_kernel_identity_before_spawn", {}).get("Tgid")
                            == str(intent["supervisor_pid"])
                            and kernel.get("supervisor_kernel_identity_before_spawn", {}).get("Pid")
                            == str(intent["supervisor_native_tid"])
                            and kernel.get("supervisor_kernel_identity_before_spawn", {}).get("Uid", "").split()[:2]
                            == [str(intent["supervisor_real_uid"]), str(intent["supervisor_effective_uid"])],
                            "PYTEST_OWNED_FIN_NOT_CLOSED_OR_REBOUND")
            initial = custody.get("initial_actual_own_kernel")
            final = custody.get("final_actual_own_kernel")
            cleanup.require(initial == intent.get("initial_actual_own_kernel")
                            and type(initial) is dict and type(final) is dict
                            and initial.get("kernel_echild_verified") is True
                            and final.get("kernel_echild_verified") is True
                            and type(initial.get("subreaper_state_before")) is int
                            and type(initial.get("subreaper_state_after")) is int
                            and type(final.get("subreaper_state_before")) is int
                            and type(final.get("subreaper_state_after")) is int
                            and initial.get("subreaper_state_before") in (0, 1)
                            and initial.get("subreaper_state_before") == initial.get("subreaper_state_after")
                            == final.get("subreaper_state_before") == final.get("subreaper_state_after")
                            and custody.get("phase_green") is recorded_original_phase_green(kernel)
                            and custody.get("management_acceptance") is recorded_management_accepted(
                                kernel, expected_management_seconds),
                            "PYTEST_FIN_KERNEL_READBACK_OR_PHASE_MISMATCH")
            result.update(safe_postread=True, classification="FIN_REAL_CLOSED", pytest_producer_witness=True,
                          pytest_phase_green=custody["phase_green"], producer_post_fin_errors=custody["post_fin_errors"],
                          management_acceptance=custody["management_acceptance"],
                          witness_scope="REVALIDATED_ACTUAL_SAME_SUPERVISOR_RAW_FIN; NO_NEW_KERNEL_WITNESS")
    except (ValueError, OSError, KeyError, TypeError) as error:
        result["errors"].append(str(error) if isinstance(error, cleanup.CleanupRejected)
                                else type(error).__name__)
    if result["safe_postread"]:
        try:
            mirror = private / names["postread"]
            mirror_previous = None
            if os.path.lexists(mirror):
                raw, details = cleanup.read_file(mirror, maximum=1024 * 1024, mode=0o600)
                prior = cleanup.decode(raw)
                cleanup.require(type(prior) is dict and prior.get("context") == context
                                and prior.get("owner_uuid") == control["owner_uuid"] and prior.get("phase") == phase,
                                "PYTEST_POSTREAD_PRIMARY_CONTROL_REBOUND")
                mirror_previous = details
            cleanup.publish(mirror, result, previous=mirror_previous)
        except (ValueError, OSError, KeyError, TypeError) as error:
            result.update(safe_postread=False, classification="UNKNOWN_OR_UNCLOSED")
            result["errors"].append(str(error) if isinstance(error, cleanup.CleanupRejected)
                                    else type(error).__name__)
    cleanup.require(len(cleanup.canonical(result)) <= 1024 * 1024, "PYTEST_POSTREAD_CONTROL_RECEIPT_SIZE")
    cleanup.publish(path, result, previous=previous)
    result["control_receipt"] = str(path)
    return result


def safe_postread(scope, repo, context, environ, *, phase="all", expected_management_seconds=5400):
    """Both pytest producers must be closed before any later payload read."""
    if phase != "all":
        return safe_phase_postread(scope, repo, context, environ, phase=phase,
                                  expected_management_seconds=expected_management_seconds)
    control = bind_owner(scope, repo, context, environ)
    cleanup.require(type(expected_management_seconds) is int and 60 <= expected_management_seconds <= 5400,
                    "POSTREAD_ORIGINAL_MANAGEMENT_BIND_REQUIRED")
    path = postread_receipt_path(control, "all")
    previous = None
    old = None
    if os.path.lexists(path):
        raw, details = cleanup.read_file(path, maximum=1024 * 1024, mode=0o600)
        old = cleanup.decode(raw)
        cleanup.require(type(old) is dict and old.get("schema") == "rc6.predeploy-pytest-postread-safety.v1"
                        and old.get("phase") == "all" and old.get("context") == context
                        and old.get("owner_uuid") == control["owner_uuid"], "PYTEST_POSTREAD_RECEIPT_REBOUND")
        previous = details
    if old is not None and old.get("safe_postread") is not True:
        result = old
        result["errors"].append("PREVIOUS_UNKNOWN_OR_UNCLOSED_IRREVERSIBLE_RED")
    else:
        phases = {name: safe_phase_postread(scope, repo, context, environ, phase=name,
                                          expected_management_seconds=expected_management_seconds)
                  for name in ("collection", "execution")}
        safe = all(row["safe_postread"] for row in phases.values())
        witnessed = any(row["pytest_producer_witness"] for row in phases.values())
        result = {"schema": "rc6.predeploy-pytest-postread-safety.v1", "phase": "all", "context": context,
                  "owner_uuid": control["owner_uuid"], "owner_uid": control["owner_uid"],
                  "expected_management_seconds": expected_management_seconds,
                  "safe_postread": safe,
                  "classification": ("UNKNOWN_OR_UNCLOSED" if not safe else
                                     "FIN_REAL_CLOSED" if witnessed else "NO_PYTEST_LAUNCH_ATTEMPTED"),
                  "pytest_producer_witness": witnessed, "phase_results": phases,
                  "errors": [error for row in phases.values() for error in row["errors"]],
                  "trace_presence_lstat_only": {key: value for row in phases.values()
                                                for key, value in row.get("trace_presence_lstat_only", {}).items()},
                  "control_observations": {}, "source_junit_data_log_payloads_read": 0,
                  "cleanup_performed": False, "new_or_global_kernel_fin_asserted": False,
                  "witness_scope": ("REVALIDATED_ALL_DECLARED_PYTEST_PHASES; NO_GLOBAL_KERNEL_WITNESS" if witnessed
                                    else "NO_PYTEST_PRODUCER_WITNESS; ORIGINAL_SCOPED_CLEANUP_ONLY"),
                  "real_orders_sent": 0}
        # Keep complete bounded raw controls once, in each phase record. These
        # convenience values never imply full Gov or a different phase's FIN.
        execution = phases["execution"]
        if execution["pytest_producer_witness"]:
            result["pytest_phase_green"] = execution["pytest_phase_green"]
            result["management_acceptance"] = execution["management_acceptance"]
    if result["safe_postread"]:
        mirror = Path(control["private_root"]) / "porota-pytest-producers-postread.json"
        try:
            prior_details = None
            if os.path.lexists(mirror):
                raw, details = cleanup.read_file(mirror, maximum=1024 * 1024, mode=0o600)
                prior = cleanup.decode(raw)
                cleanup.require(type(prior) is dict and prior.get("context") == context
                                and prior.get("owner_uuid") == control["owner_uuid"] and prior.get("phase") == "all",
                                "PYTEST_POSTREAD_PRIMARY_CONTROL_REBOUND")
                prior_details = details
            cleanup.publish(mirror, result, previous=prior_details)
        except (ValueError, OSError, KeyError, TypeError) as error:
            result.update(safe_postread=False, classification="UNKNOWN_OR_UNCLOSED")
            result["errors"].append(str(error) if isinstance(error, cleanup.CleanupRejected)
                                    else type(error).__name__)
    cleanup.require(len(cleanup.canonical(result)) <= 1024 * 1024, "PYTEST_POSTREAD_CONTROL_RECEIPT_SIZE")
    cleanup.publish(path, result, previous=previous)
    result["control_receipt"] = str(path)
    return result


def execute_pytest_owned(scope, repo, context, environ, command, *, timeout_seconds=5400, phase="execution"):
    control, claim = load_workspace(scope, repo, context, environ)
    names = phase_controls(phase)
    cleanup.require(type(timeout_seconds) is int and 60 <= timeout_seconds <= 5400
                    and command[:3] == ["python", "-m", "pytest"]
                    and ((phase == "collection" and "--collect-only" in command)
                         or (phase == "execution" and "--collect-only" not in command)),
                    "ORIGINAL_PYTEST_COMMAND_AND_MANAGEMENT_CAP_REQUIRED")
    cleanup.require(phase == "collection" or "--basetemp=" + claim["pytest_basetemp"] in command,
                    "BOUND_EXTERNAL_PYTEST_BASETEMP_REQUIRED")
    interpreter = shutil.which("python", path=environ.get("PATH"))
    expected = Path(control["private_root"]) / "venv/bin/python"
    cleanup.require(interpreter == str(expected), "ORIGINAL_PRIVATE_TEST_INTERPRETER_REQUIRED")
    aggregate_path = postread_receipt_path(control, "all")
    if os.path.lexists(aggregate_path):
        raw, _ = cleanup.read_file(aggregate_path, maximum=1024 * 1024, mode=0o600)
        aggregate = cleanup.decode(raw)
        cleanup.require(type(aggregate) is dict and aggregate.get("context") == context
                        and aggregate.get("owner_uuid") == control["owner_uuid"]
                        and aggregate.get("phase") == "all" and aggregate.get("safe_postread") is True,
                        "PYTEST_PREVIOUS_AGGREGATE_UNKNOWN_NO_NEW_LAUNCH")
    current = safe_postread(scope, repo, context, environ, phase=phase,
                            expected_management_seconds=timeout_seconds)
    cleanup.require(current["safe_postread"] and current["classification"] == "NO_PYTEST_LAUNCH_ATTEMPTED",
                    "PYTEST_PHASE_ALREADY_ATTEMPTED_OR_UNKNOWN")
    if phase == "execution":
        collection = safe_postread(scope, repo, context, environ, phase="collection",
                                   expected_management_seconds=timeout_seconds)
        cleanup.require(collection["safe_postread"] and collection["classification"] == "FIN_REAL_CLOSED"
                        and collection.get("pytest_phase_green") is True
                        and collection.get("management_acceptance") is True
                        and collection.get("producer_post_fin_errors") == [],
                        "PYTEST_COLLECTION_NOT_CLOSED_GREEN_EXECUTION_NOT_LAUNCHED")
    else:
        execution = safe_postread(scope, repo, context, environ, phase="execution",
                                  expected_management_seconds=timeout_seconds)
        cleanup.require(execution["safe_postread"] and execution["classification"] == "NO_PYTEST_LAUNCH_ATTEMPTED",
                        "PYTEST_EXECUTION_ALREADY_ATTEMPTED_COLLECTION_NOT_LAUNCHED")
    manager, provenance = native_manager(repo, context)
    initial = manager["pre_capture_kernel_state"]()
    private = Path(control["private_root"])
    _, intent_sha = publish_launch_intent(control, context, command, provenance, initial, timeout_seconds, phase=phase)
    progress_stat, last_print = None, 0

    def progress(phase, pid, entered, deadline, log_fd):
        nonlocal progress_stat, last_print
        elapsed = time.monotonic() - entered
        progress_path = private / names["progress"]
        cleanup.publish(progress_path, {"classification": "IN_PROGRESS_NOT_ACCEPTED", "pytest_pid": pid,
                        "management_elapsed_seconds": elapsed,
                        "management_seconds_remaining": max(0, deadline - time.monotonic()),
                        "native_stdout_bytes": os.fstat(log_fd).st_size,
                        "observation_scope": "SUPERVISOR_OWNED_LOG_FD_FSTAT_ONLY_NO_PAYLOAD_READS"},
                        previous=progress_stat)
        progress_stat = progress_path.lstat()
        if phase == "started" or elapsed - last_print >= 30:
            print("POROTA_OWNED_PYTEST=IN_PROGRESS|pid=" + str(pid)
                  + "|elapsed_seconds=" + str(round(elapsed, 3)), flush=True)
            last_print = elapsed

    child_environment = dict(environ, RUNNER_TEMP=str(private), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    kernel = manager["managed_native_child"](command, Path(repo), private / names["log"],
                                               child_environment, timeout_seconds,
                                               terminate_grace=2, progress_poll=5, progress=progress)
    custody = {"schema": "rc6.predeploy-pytest-owned-fin.v1", "context": context,
               "owner_uuid": control["owner_uuid"], "original_manager_source": provenance,
               "supervisor_pid": os.getpid(), "launch_intent_sha256": intent_sha,
               "phase": phase,
               "management_seconds": timeout_seconds, "management_acceptance": False,
               "initial_actual_own_kernel": initial, "kernel": kernel,
               "owned_fin_closed": False, "phase_green": False, "post_fin_errors": [],
               "scope": "ACTUAL_PYTEST_MAIN_WAIT4_AND_ADOPTED_WAIT4; MAX_RSS_NOT_SUM; NO_TRANSITIVE_BINARY_NETWORK_ATTESTATION"}
    # FIN permits measurement, never converts a failed/late/signalled phase
    # into GREEN. In particular a post-main wait4-zero remains irreversible.
    try:
        if manager["managed_custody_closed"](kernel):
            after = manager["pre_capture_kernel_state"]()
            cleanup.require(after["subreaper_state_after"] == initial["subreaper_state_after"],
                            "PYTEST_SUPERVISOR_SUBREAPER_NOT_RESTORED")
            custody.update(owned_fin_closed=True, final_actual_own_kernel=after,
                           phase_green=manager["managed_phase_green"](kernel),
                           management_acceptance=recorded_management_accepted(kernel, timeout_seconds))
            retained = measure_retained_workspace(scope, repo, context, environ)
            retained.update(measured_by_same_owned_fin_supervisor_pid=os.getpid(),
                            actual_pytest_pid=kernel["pid"], owned_fin_closed=True,
                            pytest_phase_green=custody["phase_green"])
            retained["phase"] = phase
            cleanup.publish(private / names["retained"], retained)
            custody["retention_receipt"] = str(private / names["retained"])
            custody["retained_capacity_status"] = retained["capacity_status"]
        else:
            custody["measurement_refused"] = "ACTUAL_OWNED_FIN_NOT_CLOSED_NO_FIXTURE_PAYLOAD_OR_NAMESPACE_POSTREAD"
    except (ValueError, OSError, KeyError, TypeError) as error:
        custody["post_fin_errors"].append(str(error) if isinstance(error, cleanup.CleanupRejected)
                                          else type(error).__name__)
    finally:
        cleanup.publish(private / names["fin"], custody)
    print(json.dumps({"owned_fin_closed": custody["owned_fin_closed"],
                      "pytest_phase_green": custody["phase_green"],
                      "phase": phase, "retained_fixtures_removed": False,
                      "receipt": str(private / names["fin"])}, sort_keys=True), flush=True)
    if (not custody["post_fin_errors"] and custody["owned_fin_closed"] and custody["phase_green"]
            and custody["management_acceptance"]
            and custody.get("retained_capacity_status") == "GREEN"):
        return 0
    return kernel["returncode"] if type(kernel["returncode"]) is int and kernel["returncode"] > 0 else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("create", "supervise-pytest", "safe-postread", "require-owned-fin"))
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=5400)
    parser.add_argument("--phase", choices=("all", "collection", "execution"), default="all")
    args, command = parser.parse_known_args(argv)
    old_mask = os.umask(0o022)
    try:
        repo = Path.cwd()
        context = cleanup.execution_context(repo, os.environ)
        if args.command == "create":
            cleanup.require(not command, "CREATE_WORKSPACE_UNEXPECTED_ARGUMENTS")
            claim = create_workspace(args.scope, repo, context, os.environ)
            # Only the GitHub-provided current step environment file is appended.
            with Path(os.environ["GITHUB_ENV"]).open("a", encoding="utf-8") as output:
                output.write("POROTA_PREDEPLOY_PYTEST_BASETEMP=" + claim["pytest_basetemp"] + "\n")
            print("POROTA_PREDEPLOY_TEST_WORKSPACE=CREATED|EXTERNAL_FIXTURES_EXPLICITLY_RETAINED", flush=True)
            return 0
        if args.command in ("safe-postread", "require-owned-fin"):
            cleanup.require(not command, "POSTREAD_UNEXPECTED_ARGUMENTS")
            result = safe_postread(args.scope, repo, context, os.environ,
                                   phase=args.phase,
                                   expected_management_seconds=args.timeout_seconds)
            if "GITHUB_OUTPUT" in os.environ:
                with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
                    output.write("safe_postread=" + str(result["safe_postread"]).lower() + "\n")
                    output.write("control_receipt_available=true\n")
                    output.write("control_receipt=" + result["control_receipt"] + "\n")
            print("POROTA_PYTEST_POSTREAD=" + result["classification"] + "|safe="
                  + str(result["safe_postread"]).lower(), flush=True)
            return 0 if result["safe_postread"] else 1
        if command[:1] == ["--"]:
            command = command[1:]
        return execute_pytest_owned(args.scope, repo, context, os.environ, command,
                                    timeout_seconds=args.timeout_seconds,
                                    phase="execution" if args.phase == "all" else args.phase)
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        signature = str(error) if isinstance(error, cleanup.CleanupRejected) else type(error).__name__
        print("POROTA_PREDEPLOY_TEST_WORKSPACE=RED|" + signature, flush=True)
        return 1
    finally:
        os.umask(old_mask)


if __name__ == "__main__":
    raise SystemExit(main())
