"""Native, private archive capacity measurement; source pin is mandatory.

No provider/live execution. Normal four-cut measurements are descriptive. A
ten-hour result requires all 1201 full native ticks, original-byte recovery,
restarts and both fixed byte/file policies; it is never inferred from a pair.
V2 requires the complete source index, canonical disk scratch and unchanged
source bytes AND metadata. The original V1 runner/107a RED receipt remain intact.
Run against a complete immutable git archive, not an overlay or a PYTHONPATH mix.
"""
import argparse
import gc
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import resource
import socket
import sqlite3
import stat
import subprocess
import sys
import sysconfig
import time
import traceback
from urllib.parse import unquote, urlsplit


def raw(path):
    path = Path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("NATIVE_PROFILE_SOURCE_ALIAS_INVALID")
    identity = tuple(getattr(before, key) for key in FIELDS)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK)
    try:
        if identity != tuple(getattr(os.fstat(descriptor), key) for key in FIELDS):
            raise ValueError("NATIVE_PROFILE_SOURCE_CHANGED_DURING_INVENTORY")
        chunks = []
        while data := os.read(descriptor, 65536):
            chunks.append(data)
        if (identity != tuple(getattr(os.fstat(descriptor), key) for key in FIELDS)
                or identity != tuple(getattr(path.lstat(), key) for key in FIELDS)):
            raise ValueError("NATIVE_PROFILE_SOURCE_CHANGED_DURING_INVENTORY")
        value = b"".join(chunks)
        if len(value) != before.st_size:
            raise ValueError("NATIVE_PROFILE_SOURCE_CHANGED_DURING_INVENTORY")
        return value
    finally:
        os.close(descriptor)


FIELDS = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size",
          "st_blocks", "st_atime_ns", "st_mtime_ns", "st_ctime_ns")
ORIGINAL_MEMBERS = frozenset(("report.json.gz", "checkpoint.json.gz", "status.json", "manifest.json", "projection.sqlite"))
PROFILE_PATH = "scripts/rc6_material_horizon_producer.py"
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("NATIVE_PROFILE_DUPLICATE_JSON_KEY")
        value[key] = item
    return value


def read_json(path):
    return json.loads(raw(path), object_pairs_hook=unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("NATIVE_PROFILE_NONFINITE_JSON")))


def inspect_private_archive(archive):
    """Bind this synthetic producer's admission to its actual fixture owner."""
    from rc6_shadow_runtime.archive_namespace import inspect_archive
    return inspect_archive(archive, owner_uid=os.geteuid())


def git_bytes(repository, *arguments, payload=None):
    environment = dict(os.environ, GIT_NO_LAZY_FETCH="1", GIT_NO_REPLACE_OBJECTS="1",
                       GIT_OPTIONAL_LOCKS="0")
    return subprocess.check_output(["git", "--no-replace-objects", "-C", str(repository), *arguments],
                                   input=payload, env=environment)


def git_objects(repository, identifiers, kind):
    identifiers = list(dict.fromkeys(identifiers))
    if any(not HEX40.fullmatch(value) for value in identifiers):
        raise ValueError("NATIVE_PROFILE_RAW_GIT_OBJECT_INVALID")
    wire = git_bytes(repository, "cat-file", "--batch", payload=("\n".join(identifiers)+"\n").encode())
    offset, objects = 0, {}
    for identifier in identifiers:
        end = wire.find(b"\n", offset)
        header = wire[offset:end].decode().split()
        if end < offset or len(header) != 3 or header[:2] != [identifier, kind]:
            raise ValueError("NATIVE_PROFILE_RAW_GIT_OBJECT_INVALID")
        size = int(header[2]); offset = end+1
        body = wire[offset:offset+size]; offset += size
        if size < 0 or len(body) != size or wire[offset:offset+1] != b"\n":
            raise ValueError("NATIVE_PROFILE_RAW_GIT_OBJECT_INVALID")
        offset += 1
        if hashlib.sha1(kind.encode()+b" "+str(size).encode()+b"\0"+body).hexdigest() != identifier:
            raise ValueError("NATIVE_PROFILE_RAW_GIT_OBJECT_INVALID")
        objects[identifier] = body
    if offset != len(wire):
        raise ValueError("NATIVE_PROFILE_RAW_GIT_OBJECT_INVALID")
    return objects


def code_inventory(root):
    """Every source blob, exact physical mode and Git object ID; no overlays."""
    files, modes, blobs = {}, {}, {}
    directories = set()
    queue = [root]
    while queue:
        directory = queue.pop()
        for path in paths_at(directory):
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                if stat.S_IMODE(info.st_mode) != 0o755:
                    raise ValueError("NATIVE_PROFILE_CODE_DIRECTORY_MODE_MISMATCH")
                directories.add(path.relative_to(root).as_posix())
                queue.append(path)
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("NATIVE_PROFILE_CODE_ALIAS_INVALID")
            mode = stat.S_IMODE(info.st_mode)
            if mode not in (0o644, 0o755):
                raise ValueError("NATIVE_PROFILE_COMPLETE_SOURCE_MODE_MISMATCH")
            hashed, git_blob, stable_info = file_hashes(path)
            if tuple(getattr(info, key) for key in FIELDS) != tuple(getattr(stable_info, key) for key in FIELDS):
                raise ValueError("NATIVE_PROFILE_SOURCE_CHANGED_DURING_INVENTORY")
            name = path.relative_to(root).as_posix()
            files[name], modes[name], blobs[name] = hashed, "100"+format(mode, "03o"), git_blob
    implicit = {parent.as_posix() for name in files for parent in PurePosixPath(name).parents if parent.as_posix() != "."}
    if directories != implicit:
        raise ValueError("NATIVE_PROFILE_CODE_DIRECTORY_CLOSURE_MISMATCH")
    return {"files": files, "modes": modes, "blob_ids": blobs}


def verify_source(repository, root, source_sha, source_tree, pin):
    """Derive the authority from raw Git, independently of the index claims."""
    if not HEX40.fullmatch(source_sha) or not HEX40.fullmatch(source_tree):
        raise ValueError("NATIVE_PROFILE_RAW_GIT_PIN_INVALID")
    if (not isinstance(pin, dict) or pin.get("schema") != "rc6.complete-archive-source-pin.v1"
            or pin.get("source_sha") != source_sha or pin.get("source_tree") != source_tree
            or type(pin.get("overlay_count")) is not int or pin["overlay_count"] != 0
            or any(not isinstance(pin.get(key), dict) for key in ("files", "modes", "blob_ids"))):
        raise ValueError("NATIVE_PROFILE_COMPLETE_SOURCE_PIN_REQUIRED")
    if git_bytes(repository, "for-each-ref", "--format=%(refname)", "refs/replace").strip():
        raise ValueError("NATIVE_PROFILE_GIT_REPLACE_REF_FORBIDDEN")
    commit = git_objects(repository, [source_sha], "commit")[source_sha]
    if (not commit.startswith(b"tree "+source_tree.encode()+b"\n")
            or pin.get("raw_git_commit_sha256") != hashlib.sha256(commit).hexdigest()):
        raise ValueError("NATIVE_PROFILE_RAW_GIT_COMMIT_OR_TREE_MISMATCH")
    expected, trees = {}, [source_tree]
    for row in git_bytes(repository, "ls-tree", "-r", "-t", "-z", "--full-tree", source_sha).split(b"\0"):
        if not row:
            continue
        metadata, encoded_name = row.split(b"\t", 1)
        mode, kind, identifier = metadata.decode().split()
        name = encoded_name.decode(); path = PurePosixPath(name)
        if (path.is_absolute() or ".." in path.parts or path.as_posix() != name
                or not HEX40.fullmatch(identifier)):
            raise ValueError("NATIVE_PROFILE_RAW_GIT_TREE_INVALID")
        if kind == "tree" and mode == "040000":
            trees.append(identifier)
        elif kind == "blob" and mode in ("100644", "100755") and name not in expected:
            expected[name] = {"mode": mode, "blob_id": identifier}
        else:
            raise ValueError("NATIVE_PROFILE_RAW_GIT_TREE_INVALID")
    git_objects(repository, trees, "tree")
    actual = code_inventory(root)
    if (actual != {key: pin[key] for key in actual}
            or set(actual["files"]) != set(expected)
            or actual["modes"] != {name: row["mode"] for name, row in expected.items()}
            or actual["blob_ids"] != {name: row["blob_id"] for name, row in expected.items()}):
        raise ValueError("NATIVE_PROFILE_COMPLETE_SOURCE_HASH_MODE_OR_BLOB_MISMATCH")
    return actual, {"scope": "RAW_GIT_COMMIT_ALL_TREES_ALL_BLOBS_PHYSICAL_MODES_NO_REPLACEMENTS",
                    "source_sha": source_sha, "source_tree": source_tree, "source_files": len(expected),
                    "tree_objects_rehashed": len(set(trees)), "raw_git_commit_sha256": hashlib.sha256(commit).hexdigest()}


def environment_qualification(root):
    """Exact installed157 versions and source-pinned locks, before fixtures."""
    from scripts.porota_dependency_repro_audit import (installed_distribution_audit, requirement_rows,
        _hash_gaps, current_platform_identity, platform_supported)
    policy_path = root / "ops/policy/rc6-supply-chain-v1.json"
    policy = read_json(policy_path)
    installed = installed_distribution_audit(policy)
    lock = root / "requirements.lock.txt"; build = root / "requirements.build.lock.txt"
    platform = current_platform_identity()
    gaps = {"runtime": _hash_gaps(requirement_rows(raw(lock).decode()), policy.get("packages", [])),
            "build": _hash_gaps(requirement_rows(raw(build).decode()), policy.get("build_tools", []))}
    if (policy.get("schema") != "rc6.hashed-distribution-lock.v1"
            or installed.get("status") != "GREEN"
            or any(type(installed.get(key)) is not int or installed[key] != 157
                   for key in ("expected_total", "installed_total", "installed_unique_total"))
            or any(gaps.values()) or platform["python_minor"] not in ("3.11", "3.12")
            or not platform_supported(platform, policy.get("platform", {})) or sys.prefix == sys.base_prefix):
        raise AssertionError("NATIVE_PROFILE_FROZEN157_ENVIRONMENT_REQUIRED_BEFORE_FIXTURES")
    return {"scope": "INSTALLED_METADATA_EXACT157_VERSIONS_AND_HASH_LOCKS_NOT_WHEEL_BYTES_OR_IMAGE_ID",
            "installed_closure": installed, "lock_hash_gaps": gaps, "platform": platform,
            "python_version": sys.version, "executable": sys.executable,
            "sys_prefix": sys.prefix, "sys_base_prefix": sys.base_prefix,
            "explicitly_permitted_python_minors": ["3.11", "3.12"],
            "source_lock_sha256": {path.relative_to(root).as_posix(): hashlib.sha256(raw(path)).hexdigest()
                                   for path in (policy_path, lock, build)}}


def import_inventory(root, pinned):
    """Actual module origins: pinned project, stdlib, or this real virtualenv."""
    stdlib = Path(sysconfig.get_path("stdlib")).resolve(strict=True)
    actual_sites = {Path(sysconfig.get_path(key)).resolve(strict=True) for key in ("purelib", "platlib")}
    if sys.prefix == sys.base_prefix or any(not path.is_relative_to(Path(sys.prefix).resolve()) for path in actual_sites):
        raise AssertionError("NATIVE_PROFILE_ACTUAL_VENV_REQUIRED")
    graph, invalid = {}, []
    for name, module in sorted(tuple(sys.modules.items())):
        filename = getattr(module, "__file__", None)
        origins = [filename] if filename else list(getattr(module, "__path__", ()))
        if not origins:
            continue
        for origin in origins:
            path = Path(origin).absolute().resolve(strict=True)
            if path.is_relative_to(root):
                relative = path.relative_to(root).as_posix()
                if filename and relative not in pinned["files"]:
                    invalid.append({"module": name, "path": str(path), "reason": "UNPINNED_PROJECT_IMPORT"})
                    continue
                graph.setdefault(name, []).append({"path": str(path), "origin": "PINNED_COMPLETE_SOURCE",
                    "relative_path": relative, "sha256": pinned["files"].get(relative),
                    "mode": pinned["modes"].get(relative), "blob_id": pinned["blob_ids"].get(relative)})
            elif any(path.is_relative_to(site) for site in actual_sites):
                graph.setdefault(name, []).append({"path": str(path), "origin": "ACTUAL_VENV"})
            elif path.is_relative_to(stdlib) and "site-packages" not in path.relative_to(stdlib).parts:
                graph.setdefault(name, []).append({"path": str(path), "origin": "STDLIB"})
            else:
                invalid.append({"module": name, "path": str(path), "reason": "ALIEN_IMPORT"})
    if invalid:
        raise AssertionError("NATIVE_PROFILE_ALIEN_OR_UNPINNED_IMPORT:"+json.dumps(invalid, sort_keys=True))
    return {"scope": "ACTUAL_MODULE_LOCATIONS_NOT_EXECUTION_COUNTS", "modules": graph,
            "module_count": len(graph), "unexpected": invalid,
            "stdlib": str(stdlib), "actual_venv_sites": sorted(map(str, actual_sites))}


class PrivateExecutionGuard:
    """Block transport and confine all SQLite connections to private fixtures."""
    def __init__(self, root, database):
        self.root, self.database = root, database
        self.active, self.primary_sealed = True, False
        self.network_attempts, self.sqlite_rejected = [], []
        self.network_attempt_count = self.sqlite_rejected_count = 0
        self.sqlite_allowed = self.sqlite_memory = self.sqlite_primary_setup = 0
        self.socket_originals, self.socket_module_originals = {}, {}
        for name in ("connect", "connect_ex", "send", "sendall", "sendto", "sendmsg"):
            if not hasattr(socket.socket, name):
                continue
            self.socket_originals[name] = getattr(socket.socket, name)
            def transport(sock, *arguments, _method=name, **keywords):
                if sock.family == socket.AF_UNIX:
                    return self.socket_originals[_method](sock, *arguments, **keywords)
                self.reject_network("socket."+_method)
            setattr(socket.socket, name, transport)
        for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
            self.socket_module_originals[name] = getattr(socket, name)
            def resolver(*arguments, _method=name, **keywords):
                self.reject_network("socket."+_method)
            setattr(socket, name, resolver)
        # The audit hook also covers retained native connect/sendto/sendmsg and
        # resolver references. Public send/sendall are interposed before any
        # product import; this is a Python harness barrier, not OS isolation.
        # This runner is one fresh process; the hook becomes inert on cleanup.
        sys.addaudithook(self.audit)

    def audit(self, event, arguments):
        if not self.active:
            return
        if event.startswith("socket.") and event in {
                "socket.connect", "socket.sendto", "socket.sendmsg", "socket.getaddrinfo",
                "socket.gethostbyname", "socket.gethostbyaddr"}:
            if event in {"socket.connect", "socket.sendto", "socket.sendmsg"}:
                if getattr(arguments[0], "family", None) == socket.AF_UNIX:
                    return
            self.reject_network(event)
        if event != "sqlite3.connect":
            return
        requested = os.fsdecode(arguments[0])
        if requested == ":memory:" or requested.startswith("file:") and (
                urlsplit(requested).path == ":memory:" or "mode=memory" in urlsplit(requested).query.split("&")):
            self.sqlite_memory += 1
            return
        if requested.startswith("file:"):
            uri = urlsplit(requested)
            if uri.netloc not in ("", "localhost"):
                self.reject_sqlite(requested)
            requested = unquote(uri.path)
        path = Path(requested).absolute()
        if (path.resolve() != path or not path.is_relative_to(self.root)
                or self.primary_sealed and path == self.database):
            self.reject_sqlite(requested)
        self.sqlite_allowed += 1
        self.sqlite_primary_setup += int(path == self.database)

    def reject_sqlite(self, requested):
        self.sqlite_rejected_count += 1
        if len(self.sqlite_rejected) < 64:
            self.sqlite_rejected.append(requested)
        raise AssertionError("NATIVE_PROFILE_SQLITE_OUTSIDE_PRIVATE_FIXTURES_OR_SEALED_PRIMARY")

    def reject_network(self, event):
        if len(self.network_attempts) < 64:
            self.network_attempts.append(event)
        self.network_attempt_count += 1
        raise AssertionError("NATIVE_PROFILE_NETWORK_FORBIDDEN")

    def close(self):
        self.active = False
        for name, original in self.socket_originals.items():
            setattr(socket.socket, name, original)
        for name, original in self.socket_module_originals.items():
            setattr(socket, name, original)

    def summary(self):
        return {"scope": "AUDITED_PRIVATE_SQLITE_AND_NO_NETWORK_NOT_PROVIDER_AUTHORITY",
                "sqlite_allowed": self.sqlite_allowed, "sqlite_memory": self.sqlite_memory,
                "sqlite_primary_setup": self.sqlite_primary_setup,
                "primary_sqlite_sealed_after_fixture": self.primary_sealed,
                "sqlite_rejected_count": self.sqlite_rejected_count, "network_attempt_count": self.network_attempt_count,
                "sqlite_rejected_first64": list(self.sqlite_rejected), "network_attempts_first64": list(self.network_attempts)}


def original_member_binding(members, *, pointer=None, receipt=None, expected_as_of=None):
    """Five original bytes bound to the real manifest, CURRENT and receipt."""
    exact_original_members(members)
    if any(type(value) is not bytes for value in members.values()):
        raise AssertionError("NATIVE_PROFILE_ORIGINAL_MEMBER_BYTES_REQUIRED")
    manifest = json.loads(members["manifest.json"], object_pairs_hook=unique_object,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("NATIVE_PROFILE_NONFINITE_JSON")))
    roles = {"report": "report.json.gz", "checkpoint": "checkpoint.json.gz",
             "status": "status.json", "projection": "projection.sqlite"}
    hashes = {name: hashlib.sha256(value).hexdigest() for name, value in members.items()}
    if (not isinstance(manifest, dict) or manifest.get("schema") != "rc6.shadow-evidence-generation.v2"
            or re.fullmatch(r"[0-9a-f]{32}", str(manifest.get("generation_id", ""))) is None
            or type(manifest.get("sequence")) is not int or manifest["sequence"] <= 0
            or not isinstance(manifest.get("files"), dict) or set(manifest["files"]) != set(roles)):
        raise AssertionError("NATIVE_PROFILE_ORIGINAL_MANIFEST_FOUR_ROLES_REQUIRED")
    for role, name in roles.items():
        row = manifest["files"][role]
        if not isinstance(row, dict) or row.get("name") != name or row.get("sha256") != hashes[name]:
            raise AssertionError("NATIVE_PROFILE_ORIGINAL_MANIFEST_MEMBER_HASH_MISMATCH")
    if expected_as_of is not None and manifest.get("as_of") != expected_as_of:
        raise AssertionError("NATIVE_PROFILE_ORIGINAL_MANIFEST_NATIVE_CLOCK_MISMATCH")
    for authority in (pointer, receipt):
        if authority is not None and (not isinstance(authority, dict) or type(authority.get("sequence")) is not int
                or authority.get("generation_id") != manifest.get("generation_id")
                or authority.get("sequence") != manifest.get("sequence")
                or authority.get("manifest_sha256") != hashes["manifest.json"]):
            raise AssertionError("NATIVE_PROFILE_ORIGINAL_MANIFEST_CURRENT_OR_RECEIPT_MISMATCH")
    return {"member_bytes": {name: len(value) for name, value in members.items()},
            "original_member_sha256": hashes, "original_manifest": manifest,
            "original_manifest_sha256": hashes["manifest.json"]}


class NativeRetentionObservation:
    """Observe existing method calls; never request additional GC or rotation.

    Counters cover every entered/completed call. Bounded first/last state samples
    are declared samples, not a fabricated complete per-operation transcript.
    """
    METHODS = ("_archive_generation", "_maintain_archive", "_compact_ack", "_rotate",
               "_pins", "_archive_pins", "_advance_archive_checkpoint", "_recover_archive_gc")

    def __init__(self, retention_class):
        self.retention_class = retention_class
        self.originals = {name: getattr(retention_class, name) for name in self.METHODS}
        self.counters = {name: {"entered": 0, "completed": 0, "failed": 0} for name in self.METHODS}
        self.samples = {name: [] for name in self.METHODS}
        self.last = {}
        self.expired_generations = self.deleted_archive_members = self.ack_compactions = 0
        self.rotations = self.actual_gc_intents_completed = 0

    def install(self):
        for name in self.METHODS:
            def observed(owner, *arguments, _method=name, **keywords):
                return self.observe(_method, owner, arguments, keywords)
            setattr(self.retention_class, name, observed)

    def close(self):
        for name, method in self.originals.items():
            setattr(self.retention_class, name, method)

    def observe(self, name, owner, arguments, keywords):
        counter = self.counters[name]; counter["entered"] += 1
        before = None
        try:
            if name == "_compact_ack":
                before = (owner.root / ("archive-ack-"+str(arguments[0])+".json")).exists()
            elif name == "_recover_archive_gc" and (owner.archive_root / "GC.json").exists():
                intent = read_json(owner.archive_root / "GC.json")
                before = {"intent": intent, "present_targets": sum(
                    (owner.archive_root / target["name"]).exists() for target in intent["targets"])}
            value = self.originals[name](owner, *arguments, **keywords)
            state = {"call_number": counter["entered"]}
            if name in ("_pins", "_archive_pins"):
                if not isinstance(value, set) or any(re.fullmatch(r"[0-9a-f]{32}", ident) is None for ident in value):
                    raise AssertionError("NATIVE_PROFILE_NATIVE_PINS_INVALID")
                state.update(pin_count=len(value), generation_ids=sorted(value),
                    pin_digest=hashlib.sha256(json.dumps(sorted(value), separators=(",", ":")).encode()).hexdigest())
            elif name == "_archive_generation":
                owner._receipt_header(value)
                external = owner.archive_root / (value["generation_id"]+".receipt.json")
                ack = owner.root / ("archive-ack-"+value["generation_id"]+".json")
                if read_json(external) != value or read_json(ack) != value:
                    raise AssertionError("NATIVE_PROFILE_REAL_ARCHIVE_ACK_AND_EXTERNAL_RECEIPT_REQUIRED")
                state.update(generation_id=value["generation_id"], receipt_sequence=value["receipt_sequence"],
                    external_receipt_sha256=hashlib.sha256(raw(external)).hexdigest(),
                    ack_sha256=hashlib.sha256(raw(ack)).hexdigest())
            elif name == "_maintain_archive":
                if any(type(value.get(key)) is not int or value[key] < 0
                       for key in ("expired_generations", "deleted_archive_members")):
                    raise AssertionError("NATIVE_PROFILE_NATIVE_GC_RESULT_INVALID")
                self.expired_generations += value["expired_generations"]
                self.deleted_archive_members += value["deleted_archive_members"]
                state.update(returned=dict(value), verified_archive_head=owner._archive_checkpoint())
            elif name == "_advance_archive_checkpoint":
                if value != owner._archive_checkpoint():
                    raise AssertionError("NATIVE_PROFILE_NATIVE_CHECKPOINT_RETURN_MISMATCH")
                state.update(verified_archive_head=dict(value))
            elif name == "_compact_ack" and before:
                ack = owner.root / ("archive-ack-"+str(arguments[0])+".json")
                checkpoint = owner.root / "archive-checkpoint.json"
                head = owner._archive_checkpoint()
                if ack.exists() or read_json(checkpoint) != head:
                    raise AssertionError("NATIVE_PROFILE_NATIVE_ACK_COMPACTION_NOT_DURABLE")
                self.ack_compactions += 1
                state.update(generation_id=str(arguments[0]), ack_absent=True,
                    live_checkpoint_sha256=hashlib.sha256(raw(checkpoint)).hexdigest(), verified_archive_head=head)
            elif name == "_rotate":
                ident = Path(arguments[0]).name.removeprefix("gen-")
                if any((owner.root / member).exists() for member in
                       ("gen-"+ident, ".deleting-"+ident, "delete-intent-"+ident+".json", "archive-ack-"+ident+".json")):
                    raise AssertionError("NATIVE_PROFILE_NATIVE_ROTATION_RESIDUE")
                self.rotations += 1
                state.update(generation_id=ident, native_source_generation_and_delete_controls_absent=True)
            elif name == "_recover_archive_gc" and before is not None:
                if type(value) is not int or value < 0 or (owner.archive_root / "GC.json").exists():
                    raise AssertionError("NATIVE_PROFILE_NATIVE_GC_INTENT_NOT_COMPLETED")
                targets = before["intent"]["targets"]
                if value != before["present_targets"] or any((owner.archive_root / target["name"]).exists() for target in targets):
                    raise AssertionError("NATIVE_PROFILE_NATIVE_GC_TARGET_DELETION_MISMATCH")
                self.actual_gc_intents_completed += 1
                state.update(deleted_members=value, gc_intent_absent=True,
                    deleted_target_names=[target["name"] for target in targets], verified_archive_head=owner._archive_checkpoint())
            counter["completed"] += 1
            self.last[name] = state
            if len(self.samples[name]) < 4:
                self.samples[name].append(state)
            return value
        except BaseException:
            counter["failed"] += 1
            raise

    def summary(self):
        return {"scope": "REAL_NATIVE_METHOD_CALLS_WITH_VERIFIED_RETURN_FILES_AND_BOUNDED_STATE_SAMPLES",
                "counters": {key: dict(value) for key, value in self.counters.items()},
                "first_four_completed_state_samples_per_method": {key: list(value) for key, value in self.samples.items()},
                "last_completed_state_per_method": dict(self.last),
                "native_expired_generations": self.expired_generations,
                "native_deleted_archive_members": self.deleted_archive_members,
                "native_ack_compactions": self.ack_compactions, "native_rotations": self.rotations,
                "native_gc_intents_completed": self.actual_gc_intents_completed,
                "additional_gc_rotation_or_pin_requests": 0}


def start_cycle(worker, *, index, ticks, factory, restarts):
    """The existing30s cycle includes native factory/checkpoint recovery."""
    wall, cpu = time.monotonic(), time.process_time()
    restarting = ticks == 1201 and index in (361, 841)
    if worker is None or restarting:
        worker = factory()
    if restarting:
        restarts.append(index)
    return worker, wall, cpu


def completion_flags(result):
    """Four descriptive cuts never constitute a measured full wheel."""
    native_ticks = len(result["cuts"])
    clean = bool(result["source_database_unchanged"] and result["code_source_unchanged"]
                 and not result["provider_requests"] and "error" not in result)
    execution = bool(result.get("execution_complete") and clean)
    horizon = bool(execution and result.get("private_comparison_active") is not True
                   and result["ticks_requested"] == 1201 and native_ticks == 1202
                   and result.get("native_horizon_contract_verified") is True)
    return {"native_ticks_executed": native_ticks, "execution_complete": execution,
            "horizon_complete": horizon, "complete": horizon, "acceptance_complete": horizon}


def exact_original_members(members):
    if not isinstance(members, dict) or set(members) != ORIGINAL_MEMBERS:
        raise AssertionError("NATIVE_PROFILE_EXACT_FIVE_ORIGINAL_MEMBERS_REQUIRED")


def signature(path):
    hashed, _, value = file_hashes(path)
    return {**{key: getattr(value, key) for key in FIELDS}, "sha256": hashed}


def file_hashes(path):
    """Stream both hashes without raising the profile's memory footprint."""
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise ValueError("NATIVE_PROFILE_SOURCE_ALIAS_INVALID")
    identity = tuple(getattr(value, key) for key in FIELDS)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK)
    try:
        if identity != tuple(getattr(os.fstat(descriptor), key) for key in FIELDS):
            raise ValueError("NATIVE_PROFILE_SOURCE_CHANGED_DURING_INVENTORY")
        sha256, blob = hashlib.sha256(), hashlib.sha1(b"blob "+str(value.st_size).encode()+b"\0")
        size = 0
        while block := os.read(descriptor, 65536):
            sha256.update(block); blob.update(block); size += len(block)
        if (size != value.st_size or identity != tuple(getattr(os.fstat(descriptor), key) for key in FIELDS)
                or identity != tuple(getattr(path.lstat(), key) for key in FIELDS)):
            raise ValueError("NATIVE_PROFILE_SOURCE_CHANGED_DURING_INVENTORY")
        return sha256.hexdigest(), blob.hexdigest(), value
    finally:
        os.close(descriptor)


def database_inventory(database):
    # This public helper is ROOT-owned and closes the same source-custody
    # contract used by the large concurrency runner. Its revision must be in
    # the complete source pin; no fallback to the old main-file-only hash.
    from scripts.rc6_issue465_stress import source_custody_snapshot
    snapshot = source_custody_snapshot(database)
    return {suffix: snapshot.get(suffix) for suffix in ("", "-wal", "-shm", "-journal")}


def inventory_changes(before, after, phase):
    changes = []
    for suffix in sorted(set(before) | set(after)):
        a, b = before.get(suffix), after.get(suffix)
        if a is None or b is None:
            if a != b:
                changes.append({"phase": phase, "member_suffix": suffix, "field": "presence",
                    "before": a is not None, "after": b is not None})
        else:
            for key in sorted(set(a) | set(b)):
                if a.get(key) != b.get(key):
                    changes.append({"phase": phase, "member_suffix": suffix, "field": key,
                        "before": a.get(key), "after": b.get(key)})
    return changes


def paths_at(root):
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME)
    try:
        before = root.lstat()
        names = os.listdir(descriptor)
        identity = tuple(getattr(before, key) for key in FIELDS)
        if (len(names) > 32768
                or identity != tuple(getattr(root.lstat(), key) for key in FIELDS)
                or identity != tuple(getattr(os.fstat(descriptor), key) for key in FIELDS)):
            raise ValueError("NATIVE_PROFILE_DIRECTORY_CHANGED_OR_UNBOUNDED")
        return [root / name for name in names]
    finally:
        os.close(descriptor)


def custody_at(root):
    info = root.lstat()
    return {"directory": [info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode,
        info.st_nlink, info.st_size, info.st_blocks, info.st_atime_ns, info.st_mtime_ns, info.st_ctime_ns],
        "members": {path.name: signature(path) for path in paths_at(root)}}


def residence(root):
    logical = allocated = files = temporaries = entries = 0
    queue = [root]
    while queue:
        path = queue.pop(); info = path.lstat()
        allocated += info.st_blocks * 512
        logical += info.st_size
        entries += int(path != root)
        if stat.S_ISDIR(info.st_mode):
            queue.extend(paths_at(path))
        else:
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("NATIVE_PROFILE_ALIAS_OR_NAMESPACE_INVALID")
            files += 1
            temporaries += int(path.name.endswith(".tmp") or path.name in {"BUILD.json", "GC.json"})
    return {"logical_bytes_including_directories": logical, "allocated_bytes_including_directories": allocated,
            "files": files, "entries_excluding_root": entries, "pending_temporaries_or_intents": temporaries}




def native_finalizer_closed(report):
    stages = ['nonnegative_stdlib_finalizers', 'join_owned_children', 'stop_owned_forkserver',
              'remaining_stdlib_finalizers', 'stop_owned_resource_tracker', 'verify_kernel_echild']
    return (type(report) is dict and report.get('status') == 'GREEN'
        and type(report.get('management_bound_seconds')) is int and report['management_bound_seconds'] == 5
        and report.get('finalization_thread_finished') is True
        and report.get('kernel_echild_before_phase_return') is True
        and report.get('signal_guard_installed_and_witnessed') is True
        and report.get('forced_termination_attempted') is False
        and report.get('forced_termination') is False and report.get('signal_vetoed') is False
        and report.get('termination_signal_attempts') == [] and report.get('errors') == []
        and report.get('unexpected_kernel_children') == []
        and type(report.get('wall_seconds')) in (int, float)
        and 0 <= report['wall_seconds'] <= 5
        and [row.get('stage') for row in report.get('protocol_steps', [])] == stages
        and all(row.get('completed') is True for row in report['protocol_steps']))


def publish_owned_fin(path, value):
    wire = (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)+"\n").encode()
    if len(wire) > 262144:
        raise ValueError("NATIVE_HORIZON_OWNED_FIN_CONTROL_SIZE_LIMIT")
    descriptor = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC, 0o600)
    try:
        offset = 0
        while offset < len(wire):
            written = os.write(descriptor, wire[offset:])
            if written <= 0:
                raise OSError("NATIVE_HORIZON_CONTROL_SHORT_WRITE")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    descriptor = os.open(path.parent, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def observe_original_cas_comparison(comparison, originals, node, archiver):
    """Share the native captured bytes; never rebuild a comparison fixture."""
    if comparison is None:
        return None
    return comparison.observe(originals, cut=node, native_pins=archiver._archive_pins())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-repo", required=True, help="Local Git object authority; no fetch or replace refs")
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--source-index", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--catalog-count", type=int, default=1200)
    parser.add_argument("--ticks", type=int, default=4)
    parser.add_argument("--owned-fin", required=True, help="Fresh external native lifecycle control; never a data result")
    parser.add_argument("--cas-comparison-manifest", help="Exact authenticated private comparison admission; never enables native V4 writes")
    args = parser.parse_args()
    source_root = Path(args.source_root).absolute()
    if source_root.resolve(strict=True) != source_root or any(parent.is_symlink() for parent in source_root.parents):
        raise ValueError("NATIVE_PROFILE_CODE_ALIAS_INVALID")
    repository = Path(args.source_repo).absolute()
    if repository.resolve(strict=True) != repository or any(parent.is_symlink() for parent in repository.parents):
        raise ValueError("NATIVE_PROFILE_GIT_REPOSITORY_ALIAS_INVALID")
    index_path = Path(args.source_index).absolute()
    if index_path.resolve(strict=True) != index_path:
        raise ValueError("NATIVE_PROFILE_SOURCE_INDEX_ALIAS_INVALID")
    index_original = raw(index_path)
    index_original_sha256 = hashlib.sha256(index_original).hexdigest()
    pin = json.loads(index_original, object_pairs_hook=unique_object,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("NATIVE_PROFILE_NONFINITE_JSON")))
    del index_original
    code_before, source_authority = verify_source(repository, source_root, args.source_sha, args.source_tree, pin)
    if Path(__file__).absolute().resolve(strict=True) != source_root / PROFILE_PATH:
        raise ValueError("NATIVE_PROFILE_RUNNER_MUST_BE_THE_PINNED_COMPLETE_SOURCE_FILE")
    source_tar = index_path.parent / "source.tar"
    if not HEX64.fullmatch(str(pin.get("tar_sha256", ""))) or signature(source_tar)["sha256"] != pin["tar_sha256"]:
        raise ValueError("NATIVE_PROFILE_SOURCE_TAR_INDEX_HASH_MISMATCH")
    root = Path(args.root).absolute()
    if (root.exists() or root == source_root or root in source_root.parents or source_root in root.parents
            or root == repository or root in repository.parents or repository in root.parents
            or root.parent.resolve() != root.parent or args.ticks not in (4, 1201)
            or args.catalog_count not in (5, 1200, 12000)
            or args.ticks == 1201 and args.catalog_count != 1200):
        raise ValueError("NATIVE_PROFILE_EMPTY_ROOT_AND_AUTHORIZED_SIZE_REQUIRED")
    sys.path.insert(0, str(source_root))
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    from scripts import rc6_controlled_governed_runner as lifecycle
    owned_fin_path = Path(args.owned_fin).absolute()
    if (owned_fin_path.exists() or owned_fin_path.is_symlink()
            or owned_fin_path.parent.resolve(strict=True) != owned_fin_path.parent
            or any(parent.is_symlink() for parent in owned_fin_path.parents)
            or owned_fin_path.is_relative_to(source_root) or owned_fin_path.is_relative_to(repository)
            or owned_fin_path.is_relative_to(root)):
        raise ValueError("FRESH_EXTERNAL_HORIZON_OWNED_FIN_CONTROL_REQUIRED")
    control_parent = owned_fin_path.parent.lstat()
    if (not stat.S_ISDIR(control_parent.st_mode) or control_parent.st_uid != os.geteuid()
            or stat.S_IMODE(control_parent.st_mode) != 0o700):
        raise ValueError("OWN_PRIVATE_HORIZON_FIN_PARENT_REQUIRED")
    comparison = None
    if args.cas_comparison_manifest is not None:
        if args.ticks != 1201 or args.catalog_count != 1200:
            raise ValueError("CAS_COMPARISON_ORIGINAL1202_AND1200X5_REQUIRED")
        from scripts.rc6_cas_original_comparison import authenticate_comparison
        comparison = authenticate_comparison(args.cas_comparison_manifest,
            source_sha=args.source_sha, source_tree=args.source_tree, source_root=source_root,
            output_root=owned_fin_path.parent / "original-cas-comparison", producer_root=root)
    lifecycle_initial = lifecycle.child_infrastructure_snapshot()
    lifecycle.require(all(value is None for value in lifecycle_initial.values()),
                      "FRESH_NATIVE_HORIZON_INFRASTRUCTURE_REQUIRED")
    lifecycle_capability = lifecycle.restrict_inet_creation()
    lifecycle_observations = {'inet_socket_attempts': [], 'inet_socket_constructor_requests': [],
                              'subprocess_executable_counts': {}}
    lifecycle_witness = lifecycle.install_phase_audit(lifecycle_observations)
    lifecycle.require(lifecycle_witness is True, "NATIVE_HORIZON_INET_AUDIT_NOT_WITNESSED")
    data = root / "data"; database = data / "paper_v17" / "observer_v17.db"
    guard = PrivateExecutionGuard(root, database)
    # Dependency/platform/lock checks happen before even the fixture root exists.
    qualification = environment_qualification(source_root)
    import_before = import_inventory(source_root, code_before)
    root.mkdir(mode=0o700, parents=True)
    for name in tuple(os.environ):
        if name.startswith("POROTA_CAPACITY_") and name.endswith("_PATH"):
            os.environ.pop(name)
    for name in ("HIST_DB_PATH", "POROTA_DYNAMIC_SHADOW_ROOT", "POROTA_SHADOW_RUNTIME_ROOT",
                 "POROTA_DYNAMIC_SHADOW_ARCHIVE_ROOT", "POROTA_DYNAMIC_SHADOW_ARCHIVE_MAX_BYTES",
                 "POROTA_IOL_SHADOW_ROOT", "POROTA_IOL_SHADOW_CACHE_PATH"):
        os.environ.pop(name, None)
    database.parent.mkdir(mode=0o700, parents=True)
    os.environ.update(DATA_DIR=str(data), PAPER_V17_DB_PATH=str(database), POROTA_DYNAMIC_CAPACITY_MODE="OFF")
    from cg_paper_workspace import artifact_root
    from scripts.rc6_sqlite_scratch_guard import runtime_settings
    from rc6_audit_evidence import sqlite_scratch
    scratch = artifact_root(database) / "sqlite-read-scratch"
    scratch.mkdir(mode=0o700, parents=True)
    environment = {"DATA_DIR": str(data), "PAPER_V17_DB_PATH": str(database), "POROTA_DYNAMIC_CAPACITY_MODE": "OFF",
                   **runtime_settings(str(scratch))}
    os.environ.update(environment)
    # The existing native disk guard rejects tmpfs, wrong ownership, aliases
    # or weakened budgets. No fall back to unconfigured TemporaryDirectory.
    scratch_admission = sqlite_scratch.inspect_scratch(scratch)
    from datetime import timedelta
    from scripts.rc6_issue465_stress import fixture_database, PRE, AT
    from rc6_shadow_runtime.worker import ShadowRuntime
    import rc6_shadow_runtime.worker as worker_module
    from rc6_shadow_runtime.retention import EvidenceRetention
    from rc6_shadow_runtime.persistence import read_committed_projection, shadow_evidence_root, shadow_archive_root

    store = fixture_database(database, catalog_count=args.catalog_count, observations_per_identity=5)
    del store; gc.collect()
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    del connection; gc.collect()
    guard.primary_sealed = True
    before = database_inventory(database)
    result = {"schema": "rc6.native-archive-v3-profile.v2", "source_sha": args.source_sha, "source_tree": args.source_tree,
        "scope": "OFFLINE_SYNTHETIC_CANONICAL_FACTORY_NO_OPEN_AUTHORITY", "catalog_count": args.catalog_count,
        "input_observation_rows": args.catalog_count * 5, "ticks_requested": args.ticks, "tick_seconds": 30,
        "native_ticks_planned_including_preopen": 4 if args.ticks == 4 else args.ticks + 1,
        "preopen_seed_ticks": 1, "scope_is_descriptive_four_cuts": args.ticks == 4,
        "fixture_environment": environment, "scratch_admission": scratch_admission,
        "environment_qualification_before_fixtures": qualification,
        "source_authority": source_authority,
        "import_graph_before_fixtures": import_before,
        "as_of_clock_scope": "ACTUAL_NATIVE_WORKER_CALLS_AT30SECOND_ASOF_CUTS_ACCELERATED_WITHOUT_WALLCLOCK_SLEEP",
        "business_source_scope": "FIXED_SYNTHETIC_SOURCE_NO_REFRESH; NATIVE_FRESHNESS_AND_PHASES_ONLY; NO_PROVIDER_SESSION_OR_EDGE_AUTHORITY",
        "full_horizon_contract": {"postpre_native_ticks": 1201, "preopen_native_ticks": 1,
            "tick_seconds": 30, "contracted_retention_seconds": 32400, "recovery_margin_seconds": 3600,
            "first_to_last_operational_asof_seconds": 36000, "restart_indices": [361, 841],
            "restart_kind": "SAME_PROCESS_CANONICAL_FACTORY_RECONSTRUCTION_WITH_REAL_CHECKPOINT_REUSE_NOT_SIGKILL",
            "full_cycle_deadline_seconds": 30},
        "source_index_sha256": index_original_sha256,
        "source_tar_sha256": pin["tar_sha256"],
        "source_file_count": len(code_before["files"]), "overlay_count": 0,
        "source_database_before": before, "source_database_changes": [],
        "peak_scratch_native_policy_occupied_bytes": 0, "native_scratch_measure_calls": 0, "real_fsync_calls": 0,
        "source_database": str(database), "execution_complete": False, "horizon_complete": False,
        "complete": False, "acceptance_complete": False, "native_horizon_contract_verified": False,
        "completion_contract": "DESCRIPTIVE_EXECUTION_IS_DISTINCT_FROM_ACTUAL1202_NATIVE_TICKS_FOR9H_PLUS1H_RECOVERY",
        "source_database_unchanged": False, "code_source_unchanged": False, "provider_requests": 0,
        "peak_archive": {"logical_bytes_including_directories": 0, "allocated_bytes_including_directories": 0, "files": 0,
                         "entries_excluding_root": 0, "pending_temporaries_or_intents": 0},
        "peak_live": {"logical_bytes_including_directories": 0, "allocated_bytes_including_directories": 0, "files": 0,
                      "entries_excluding_root": 0, "pending_temporaries_or_intents": 0},
        "peak_authority_control": {"logical_bytes_including_directories": 0, "allocated_bytes_including_directories": 0, "files": 0,
                      "entries_excluding_root": 0, "pending_temporaries_or_intents": 0},
        "authority_control_scope": "SEPARATE_NATIVE_BOUNDED_LINEAGE_HEAD_NOT_EXTERNAL_AUTHENTICATION; OBSERVED_OUTSIDE_NATIVE_LIVE128M_NAMESPACE",
        "filesystem_reserve_observation": {"reserve_bytes": sqlite_scratch.RESERVE_BYTES,
            "minimum_free_inode_percent": sqlite_scratch.MIN_FREE_INODE_PERCENT,
            "minimum_observed_free_bytes": None, "minimum_observed_free_inode_percent": None, "samples": 0},
        "cuts": [], "restarts": [], "archive_restore_count": 0, "native_input_reads": [],
        "import_graph_verified": False, "import_graph_union": dict(import_before["modules"]),
        "factual_exit_wire_exercised": False, "actual_wallclock10hours_elapsed_required_or_claimed": False}
    destination = root / "result.json"
    result['native_source_derivation'] = {'entry_member': PROFILE_PATH,
        'original_member': 'docs/audits/rc6-convergence-persistence-evidence/native_archive_v3_profile_probe_v2.py',
        'original_sha256': 'cd39d440b36e5f35fbc23ad210fdf7370b728b761ce36020c9c88de3fa75f8da',
        'business_budgets_unchanged': True, 'native_finalizer_management_bound_seconds': 5}
    pending_error = None
    begin, cpu = time.monotonic(), time.process_time()
    worker = None
    evidence, archive = shadow_evidence_root(database), shadow_archive_root(database)
    authority = evidence.parent / (evidence.name+".authority")
    result.update(evidence_root=str(evidence), archive_root=str(archive), authority_control_root=str(authority))
    result["private_comparison_active"] = comparison is not None
    if comparison is not None:
        comparison.bind_native_archive(evidence, archive)
        result["private_source_rotation_contract"] = "REVIEWED_PRIVATE_CANDIDATE_ACK_ONLY_NOT_G5"
    native_retention = NativeRetentionObservation(EvidenceRetention)

    def source_unchanged(phase):
        after = database_inventory(database)
        changes = inventory_changes(before, after, phase)
        result["source_database_changes"].extend(changes)
        if changes:
            raise AssertionError("NATIVE_PROFILE_SOURCE_DATABASE_BYTES_OR_METADATA_CHANGED")

    def peaks():
        for name, path in (("archive", archive), ("live", evidence), ("authority_control", authority)):
            if path.exists():
                current = residence(path)
                for metric in result["peak_" + name]:
                    result["peak_" + name][metric] = max(result["peak_" + name][metric], current[metric])
        filesystem = os.statvfs(root)
        if filesystem.f_files <= 0:
            raise AssertionError("NATIVE_PROFILE_FILESYSTEM_INODE_CAPACITY_UNKNOWN")
        free, inodes = filesystem.f_bavail*filesystem.f_frsize, filesystem.f_favail/filesystem.f_files*100
        observation = result["filesystem_reserve_observation"]
        observation["samples"] += 1
        for key, value in (("minimum_observed_free_bytes",free),("minimum_observed_free_inode_percent",inodes)):
            observation[key] = value if observation[key] is None else min(observation[key],value)
        if free < observation["reserve_bytes"] or inodes < observation["minimum_free_inode_percent"]:
            raise AssertionError("NATIVE_PROFILE_EXISTING_FILESYSTEM_RESERVE_EXHAUSTED")
    original_fsync = os.fsync
    original_measure = sqlite_scratch._Lease.measure
    original_runtime_read = worker_module.read_runtime
    def measured_runtime_read(*arguments, **keywords):
        value = original_runtime_read(*arguments, **keywords)
        confirmed = sum(row.get("intraday_confirmed") is True for row in value["observations"])
        fixed_contract_stale = keywords["as_of"] > AT+timedelta(seconds=120)
        if fixed_contract_stale and confirmed:
            raise AssertionError("NATIVE_PROFILE_FIXED_SOURCE_CONTRACT_CANNOT_REMAIN_FRESH_AFTER120SECONDS")
        result["native_input_reads"].append({"as_of": keywords["as_of"].isoformat(),
            "row_limit": keywords["row_limit"], "query_budget_seconds": keywords["query_budget_seconds"],
            "catalog_returned": len(value["catalog"]), "observations_returned": len(value["observations"]),
            "observation_read_truncated": value["observation_read_truncated"],
            "intraday_confirmed_observations": confirmed,
            "fixed_source_contract_must_be_stale": fixed_contract_stale,
            "intraday_unverified_observations": sum(row.get("endpoint") == "intraday" and
                row.get("intraday_confirmed") is not True for row in value["observations"]),
            "entry_authorized_observations": sum(row.get("entry_authority") is True for row in value["observations"]),
            "source_database_effect": value["source_database_effect"]})
        return value
    worker_module.read_runtime = measured_runtime_read
    def measured_scratch(lease):
        occupied = original_measure(lease)
        result["native_scratch_measure_calls"] += 1
        result["peak_scratch_native_policy_occupied_bytes"] = max(
            result["peak_scratch_native_policy_occupied_bytes"], occupied)
        return occupied
    sqlite_scratch._Lease.measure = measured_scratch
    def measured_fsync(descriptor):
        # Observe simultaneous temporary/control/original residence before
        # the real fsync and again after it; never replace its durability.
        peaks(); original_fsync(descriptor); result["real_fsync_calls"] += 1; peaks()
    os.fsync = measured_fsync
    native_retention.install()
    try:
        clocks = ([PRE, AT, AT+timedelta(seconds=30), AT+timedelta(seconds=60)] if args.ticks == 4
                  else [PRE, *[AT+timedelta(seconds=30*index) for index in range(args.ticks)]])
        for index, as_of in enumerate(clocks):
            counters_before = {key: dict(value) for key, value in native_retention.counters.items()}
            worker, cycle_start, process = start_cycle(worker, index=index, ticks=args.ticks,
                factory=lambda: ShadowRuntime.from_environment(database), restarts=result["restarts"])
            if (worker.root != evidence or worker.files.archive_root != archive
                    or worker.files.maximum_files != 512 or worker.files.maximum_bytes != 128*1024**2
                    or worker.files.archive_maximum_bytes != 512*1024**2 or worker.files.archive_format != "COMPONENT_V3"):
                raise AssertionError("NATIVE_PROFILE_FIXED_CANONICAL_FACTORY_POLICY_MISMATCH")
            result.update(archive_format=worker.files.archive_format,
                live_maximum_bytes=worker.files.maximum_bytes, live_maximum_files=worker.files.maximum_files,
                archive_maximum_bytes=worker.files.archive_maximum_bytes)
            start = cycle_start
            report = worker.tick(as_of)
            source_unchanged("pipeline_tick_" + str(index))
            node = {"tick_index": index, "preopen_seed": index == 0, "as_of": as_of.isoformat(), "generation_id": report["generation_id"],
                "sequence": report["sequence"], "phase": report["phase"], "checkpoint_reused": report["checkpoint_reused"],
                "pipeline_wall_seconds": time.monotonic()-start, "pipeline_cpu_seconds": time.process_time()-process,
                "configuration_fingerprint": report["configuration_fingerprint"], "retention_status": report["evidence_retention"]["status"],
                "native_evidence_retention_return": dict(report["evidence_retention"]),
                "catalog_ready_count": len(report["catalog_ready"]), "provider_requests": report["provider_requests"],
                "real_orders_sent": report["real_orders_sent"], "real_routes": report["real_routes"]}
            generation = worker.root / ("gen-" + report["generation_id"])
            current = read_json(worker.root / "CURRENT.json")
            if (current["generation_id"], current["sequence"]) != (node["generation_id"], node["sequence"]):
                raise AssertionError("NATIVE_PROFILE_RETURNED_WITHOUT_ACTUAL_CURRENT_COMMIT")
            originals = {path.name: raw(path) for path in paths_at(generation)}
            binding = original_member_binding(originals, pointer=current, expected_as_of=as_of.isoformat())
            original_custody = custody_at(generation)
            del report; gc.collect()
            archive_policy = dict(archive_format=worker.files.archive_format,
                maximum_bytes=worker.files.maximum_bytes, maximum_files=worker.files.maximum_files,
                archive_maximum_bytes=worker.files.archive_maximum_bytes)
            archiver = (EvidenceRetention(worker.root, archive_root=archive, **archive_policy) if comparison is None
                else comparison.native_ack_owner(worker.root, archive, **archive_policy))
            result["archive_maximum_files"] = archiver.archive_maximum_files
            node.update(member_bytes=binding["member_bytes"], original_member_sha256=binding["original_member_sha256"],
                        original_manifest_sha256=binding["original_manifest_sha256"],
                        original_manifest_clock=binding["original_manifest"]["as_of"])
            # Both private branches receive these very same bytes BEFORE any
            # native archive call. The private V3 RED cannot truncate V4/input.
            # The reviewed candidate receipt is the sole private source ACK;
            # a missing/failed candidate must never fall back to build().
            comparison_cut = observe_original_cas_comparison(comparison, originals, node, archiver)
            start, process = time.monotonic(), time.process_time()
            receipt = archiver.archive_generation(generation)
            node.update(archive_wall_seconds=time.monotonic()-start, archive_cpu_seconds=time.process_time()-process,
                recipe_bytes=(archive / (receipt["generation_id"] + ".recipe.gz")).stat().st_size)
            restored = archiver.restore_generation(receipt["generation_id"])
            restored_binding = original_member_binding(restored["members"], receipt=restored["receipt"],
                expected_as_of=as_of.isoformat())
            if restored["members"] != originals or restored["receipt"] != receipt or restored_binding != binding:
                raise AssertionError("NATIVE_PROFILE_ORIGINAL_MEMBER_RESTORE_MISMATCH")
            if original_custody != custody_at(generation):
                raise AssertionError("NATIVE_PROFILE_ORIGINAL_SOURCE_CUSTODY_CHANGED")
            source_unchanged("archive_restore_tick_" + str(index))
            result["archive_restore_count"] += 1
            node.update(member_bytes=binding["member_bytes"], original_member_sha256=binding["original_member_sha256"],
                        original_manifest_sha256=binding["original_manifest_sha256"],
                        original_manifest_clock=binding["original_manifest"]["as_of"],
                        original_generation_custody_unchanged=True,
                        original_generation_custody_sha256=hashlib.sha256(
                            json.dumps(original_custody, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                        native_receipt=dict(receipt), verified_native_archive_head=archiver._archive_checkpoint(),
                        archive_residence=residence(archive),
                        live_residence=residence(worker.root), archive_verification_level=restored["verification_level"])
            if comparison_cut is not None:
                node["private_cas_comparison"] = {"cut_index": comparison_cut["cut_index"],
                    "comparison_wall_seconds": comparison_cut["comparison_wall_seconds"],
                    "branch_status": {key: value["status"] for key, value in comparison_cut["branches"].items()},
                    "scope": "SAME_ORIGINAL_FIVE_MEMBERS_PRIVATE_DEVELOPMENT_NOT_G5"}
            graph = import_inventory(source_root, code_before)
            result["import_graph_union"].update(graph["modules"])
            node["native_retention_call_deltas"] = {name: {key: value-counters_before[name][key]
                for key, value in count.items()} for name, count in native_retention.counters.items()}
            del originals, restored, binding, restored_binding, original_custody, current, graph; gc.collect()
            result["cuts"].append(node); peaks()
            if any(node[key] != value for key, value in (("catalog_ready_count", args.catalog_count), ("provider_requests", 0),
                ("real_orders_sent", 0), ("real_routes", "NOT_CALLED"))):
                raise AssertionError("NATIVE_PROFILE_CARDINALITY_OR_SAFETY_MISMATCH")
            for name, byte_limit, file_limit in (("archive", worker.files.archive_maximum_bytes, archiver.archive_maximum_files),
                                               ("live", worker.files.maximum_bytes, worker.files.maximum_files)):
                peak = result["peak_" + name]
                if max(peak["allocated_bytes_including_directories"], peak["logical_bytes_including_directories"]) > byte_limit:
                    raise AssertionError("NATIVE_PROFILE_" + name.upper() + "_BYTE_QUOTA_EXCEEDED")
                if peak["entries_excluding_root"] > file_limit:
                    raise AssertionError("NATIVE_PROFILE_" + name.upper() + "_FILE_QUOTA_EXCEEDED")
            if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024 > 2*1024**3:
                raise AssertionError("NATIVE_PROFILE_RSS_EXCEEDED")
            if index in result["restarts"] and not node["checkpoint_reused"]:
                raise AssertionError("NATIVE_PROFILE_NATIVE_RESTART_DID_NOT_RECOVER_COMMITTED_CHECKPOINT")
            destination.write_text(json.dumps(result, sort_keys=True, indent=2)+"\n")
            if index < 4 or index % 100 == 0:
                print(json.dumps({"tick": index, "sequence": node["sequence"], "phase": node["phase"],
                    "pipeline_seconds": node["pipeline_wall_seconds"], "archive_seconds": node["archive_wall_seconds"],
                    "archive_allocated_bytes": node["archive_residence"]["allocated_bytes_including_directories"]}), flush=True)
            # Include result publication, instrumented source checks and cleanup;
            # never restart this clock after constructor or archive/restore.
            node["full_cycle_wall_seconds"] = time.monotonic()-cycle_start
            if args.ticks > 4 and index > 0 and node["full_cycle_wall_seconds"] > 30:
                raise AssertionError("NATIVE_PROFILE_WHEEL_PIPELINE_ARCHIVE_RESTORE_CANNOT_SUSTAIN_30SECOND_CADENCE")
        final = read_committed_projection(worker.root, limit=1)
        # The postPRE as_of horizon is ten hours inclusive. Native later phases
        # may be POST/PRE; it is not a ten-hour OPEN/provider-session claim.
        # The initial operational cut is at the final GC cutoff; a preceding
        # PRE seed may expire or remain a legitimate transitive page-base pin.
        retained_index = 0 if args.ticks == 4 else 1
        first = archiver.restore_generation(result["cuts"][retained_index]["generation_id"])
        retained_binding = original_member_binding(first["members"], receipt=first["receipt"],
            expected_as_of=result["cuts"][retained_index]["as_of"])
        if retained_binding["original_member_sha256"] != result["cuts"][retained_index]["original_member_sha256"]:
            raise AssertionError("NATIVE_PROFILE_FULL_WHEEL_FIRST_ORIGINAL_CUT_UNAVAILABLE_OR_CHANGED")
        if final["manifest"]["configuration_fingerprint"] != worker.configuration_fingerprint(clocks[-1]):
            raise AssertionError("NATIVE_PROFILE_CURRENT_AND_CANONICAL_FACTORY_CONFIGURATION_MISMATCH")
        source_unchanged("final_committed_projection_and_first_original_restore")
        if (worker.files.maximum_files != 512 or worker.files.maximum_bytes != 128*1024**2
                or worker.files.archive_maximum_bytes != 512*1024**2
                or result["peak_scratch_native_policy_occupied_bytes"] > sqlite_scratch.MAX_BYTES):
            raise AssertionError("NATIVE_PROFILE_FIXED_CANONICAL_BUDGET_MISMATCH")
        horizon_seconds = (clocks[-1] - clocks[retained_index]).total_seconds()
        if args.ticks == 1201 and horizon_seconds != 10*60*60:
            raise AssertionError("NATIVE_PROFILE_FULL_WHEEL_ACTUAL_CLOCK_HORIZON_MISMATCH")
        if (result["archive_restore_count"] != len(clocks) or result["real_fsync_calls"] <= 0
                or result["native_scratch_measure_calls"] <= 0
                or any(value["failed"] or value["completed"] != value["entered"] for value in native_retention.counters.values())
                or (comparison is None and native_retention.counters["_archive_generation"]["completed"] < len(clocks))
                or (comparison is not None and comparison.native_ack_count != len(clocks))
                or native_retention.counters["_maintain_archive"]["completed"] <= 0
                or native_retention.counters["_pins"]["completed"] <= 0):
            raise AssertionError("NATIVE_PROFILE_ACTUAL_DURABILITY_SCRATCH_RETENTION_OBSERVATIONS_REQUIRED")
        if args.ticks == 1201 and (len(result["cuts"]) != 1202 or result["restarts"] != [361, 841]
                or native_retention.rotations <= 0 or native_retention.ack_compactions <= 0
                or any(cut["full_cycle_wall_seconds"] > 30 for cut in result["cuts"][1:])
                or len({cut["generation_id"] for cut in result["cuts"]}) != 1202):
            raise AssertionError("NATIVE_PROFILE_FULL_WHEEL_ACTUAL_RESTART_ROTATION_AND_CADENCE_REQUIRED")
        final_head = archiver._archive_checkpoint()
        if (final_head.get("source_as_of_max") != clocks[-1].isoformat()
                or final_head.get("contracted_horizon_seconds") != 32400
                or final_head.get("recovery_margin_seconds") != 3600):
            raise AssertionError("NATIVE_PROFILE_NATIVE_ARCHIVE_HORIZON_HEAD_MISMATCH")
        result.update(execution_complete=True, native_horizon_contract_verified=args.ticks == 1201 and comparison is None,
            final_sequence=final["pointer"]["sequence"], final_as_of=final["manifest"]["as_of"],
            final_configuration_fingerprint=worker.configuration_fingerprint(clocks[-1]), archive_admission=inspect_private_archive(archive),
            archive_final=residence(archive), live_final=residence(worker.root), authority_control_final=residence(authority),
            measured_horizon_seconds=horizon_seconds, first_restored_tick_index=retained_index,
            first_original_public_restore_completed=True, verified_final_archive_head=final_head,
            native_retention_observations=native_retention.summary(),
            gc_observed_outcome=("NATIVE_EXPIRED_PREFIX_DELETED" if native_retention.expired_generations else
                "NATIVE_GC_EVALUATED_NO_EXPIRED_UNPINNED_PREFIX_DELETED; DEPENDENCY_PINS_AND_CUTOFF_PRESERVED"))
        del first, retained_binding, final; gc.collect()
    except BaseException as error:
        pending_error = (type(error), error, error.__traceback__)
        result["error"] = {"class": type(error).__name__, "reason": str(error),
                           "traceback_withheld_until_native_fin": True}
        raise
    finally:
        os.fsync = original_fsync
        sqlite_scratch._Lease.measure = original_measure
        worker_module.read_runtime = original_runtime_read
        native_retention.close()
        try:
            lifecycle_finalization = lifecycle.finalize_child_infrastructure(lifecycle_initial, limit=5)
        except BaseException as error:
            lifecycle_finalization = {'status': 'RED', 'management_bound_seconds': 5,
                'finalization_thread_finished': False, 'kernel_echild_before_phase_return': False,
                'errors': [{'class': type(error).__name__, 'reason': 'NATIVE_HORIZON_FINALIZER_EXCEPTION'}]}
        native_fin_closed = native_finalizer_closed(lifecycle_finalization)
        lifecycle_receipt = {'schema': 'rc6.native-horizon-owned-fin.v1',
            'pid': os.getpid(), 'parent_pid': os.getppid(), 'entry_module': __name__,
            'source_sha': args.source_sha, 'source_tree': args.source_tree,
            'source_root': str(source_root), 'source_index_sha256': index_original_sha256,
            'entry_member': PROFILE_PATH, 'infrastructure_before': lifecycle_initial,
            'offline_ipv6_creation_capability': lifecycle_capability,
            'operation_audit_witness_verified': lifecycle_witness,
            'operation_audit': lifecycle_observations,
            'child_infrastructure_finalization': lifecycle_finalization,
            'native_finalizer_closed': native_fin_closed,
            'business_qualification_claim': False, 'artifact_validated': False, 'runtime_validated': False}
        # Publish control only before any final Source/DB/index/TAR/import payload capture.
        publish_owned_fin(owned_fin_path, lifecycle_receipt)
        result['native_child_infrastructure'] = lifecycle_receipt
        try:
            if (not native_fin_closed or lifecycle_observations['inet_socket_attempts']
                    or lifecycle_capability['status'] != 'INSTALLED_AND_KERNEL_WITNESSED'
                    or lifecycle_witness is not True):
                raise AssertionError("NATIVE_HORIZON_INFRASTRUCTURE_NOT_FINALIZED_NO_PAYLOAD_POSTCAPTURE")
            if pending_error is not None:
                result['error']['traceback'] = ''.join(traceback.format_exception(*pending_error))
                result['error']['traceback_withheld_until_native_fin'] = False
            peaks()
            after = database_inventory(database)
            # Custody evidence remains factual even if a later independent
            # code/index/import proof fails; do not relabel it as DB mutation.
            result.update(source_database_after=after, source_database_unchanged=before == after)
            result["source_database_changes"].extend(inventory_changes(before, after, "final"))
            if (read_json(index_path) != pin or signature(source_tar)["sha256"] != pin["tar_sha256"]
                    or hashlib.sha256(raw(index_path)).hexdigest() != result["source_index_sha256"]):
                raise AssertionError("NATIVE_PROFILE_SOURCE_INDEX_OR_TAR_CHANGED")
            code_after, authority_after = verify_source(repository, source_root, args.source_sha, args.source_tree, pin)
            graph_after = import_inventory(source_root, code_before)
            result["import_graph_union"].update(graph_after["modules"])
            result.update(code_source_unchanged=code_before == code_after, source_authority_after=authority_after,
                source_index_and_tar_unchanged=True,
                source_code_inventory_after_sha256=hashlib.sha256(
                    json.dumps(code_after, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                import_graph_after=graph_after, import_graph_verified=True,
                scratch_final=sqlite_scratch.inspect_scratch(scratch))
        except BaseException as error:
            result["execution_complete"] = False
            result["finalization_error"] = {"class": type(error).__name__, "reason": str(error),
                "traceback": traceback.format_exc() if native_fin_closed else None,
                "source_postcapture_refused_on_unclosed_native_fin": not native_fin_closed}
        result.update(elapsed_wall_seconds=time.monotonic()-begin, elapsed_cpu_seconds=time.process_time()-cpu,
            peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
            provider_requests=guard.network_attempt_count, private_execution_guard=guard.summary(),
            native_retention_observations=native_retention.summary())
        guard.close()
        if result["peak_rss_bytes"] > 2*1024**3:
            result["execution_complete"] = False
            result.setdefault("error", {"class": "AssertionError", "reason": "NATIVE_PROFILE_RSS_EXCEEDED"})
        if (not result["source_database_unchanged"] or not result["code_source_unchanged"]
                or not result["import_graph_verified"] or guard.network_attempts or guard.sqlite_rejected):
            result["execution_complete"] = False
            result.setdefault("error", {"class": "AssertionError", "reason": "NATIVE_PROFILE_FINAL_SOURCE_OR_SAFETY_CHANGED"})
        if not result["execution_complete"]:
            result["native_horizon_contract_verified"] = False
        result.update(completion_flags(result))
        if comparison is not None:
            if native_fin_closed:
                from scripts.rc6_cas_original_comparison import publish_result
                comparison_result = comparison.finish(original_complete=result["execution_complete"])
                comparison_path = owned_fin_path.parent / "original-cas-comparison" / "comparison-result.json.gz"
                publication = publish_result(comparison_path, comparison_result)
                result["private_cas_comparison"] = {**publication, "status": comparison_result["status"],
                    "actual_original_cuts": comparison_result["actual_original_cuts"],
                    "qualification_claimed": False, "native_v4_write_enabled": False}
            else:
                result["private_cas_comparison"] = {"status": "UNKNOWN_FIN_NO_PAYLOAD_POSTCAPTURE",
                    "qualification_claimed": False, "native_v4_write_enabled": False}
        destination.write_text(json.dumps(result, sort_keys=True, indent=2)+"\n")
        print(json.dumps({"path": str(destination), "execution_complete": result["execution_complete"],
                         "complete": result["complete"], "acceptance_complete": result["acceptance_complete"],
                         "wall_seconds": result["elapsed_wall_seconds"], "peak_rss_bytes": result["peak_rss_bytes"]}), flush=True)
    if not result["execution_complete"]:
        raise AssertionError("NATIVE_PROFILE_FINAL_SOURCE_OR_SAFETY_CHANGED")


if __name__ == "__main__":
    main()
