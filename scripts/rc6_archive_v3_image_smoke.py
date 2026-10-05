#!/usr/bin/env python3
"""Bounded native codec/archive controls for an already built image.

The caller binds the supplied ImageID to Docker and the external GitHub tuple.
This CLI cannot authenticate its own arguments or grant runtime approval.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import resource
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch


SCHEMA = "rc6.archive-codec-tiny-image-smoke.v1"
SCOPE = "NATIVE_PRIVATE_SYNTHETIC_CODEC_AND_ARCHIVE_FIXTURES_ONLY"
MAX_SECONDS = 30
MAX_OUTPUT = 64 * 1024
MAX_FIXTURE = 4 * 1024**2
MAX_FILE = 8 * 1024**2
MAX_ENTRIES = 256
MAX_SCRATCH = 16 * 1024**2
MAX_RSS = 512 * 1024**2
SOURCE_SCHEMA = "porota.source-byte-provenance.v2"
V3_LEVEL = "ALL_ORIGINAL_MEMBER_BYTES_MANIFEST_CRC_AND_ROLE_WIRE"
V2_LEVEL = "LEGACY_V2_ALL_ORIGINAL_MEMBER_HASHES_AND_MANIFEST"
MEMBERS = frozenset({"manifest.json", "report.json.gz", "checkpoint.json.gz",
                     "status.json", "projection.sqlite"})
CODEC_FIXTURE = "scripts/fixtures/rc6_codec_legacy_v1.json"
ARCHIVE_FIXTURE = "scripts/fixtures/rc6_archive_legacy_v2.json"
FIXTURE_SHA256 = {
    CODEC_FIXTURE: "8d21e988caee08b405725a2f8b73e8b87e0d90c3e21db0a8cc11a715dbe0966c",
    ARCHIVE_FIXTURE: "1e8d0b1150909eda33ccd168e3f37eba3345ba2aaad0837a340e9daa684fc3be",
}
LEGACY_WIRE_SHA256 = "09581adadefdf045da221d6742e13beb12391989d108ded94da5eea40c9883f6"
LEGACY_MEMBER_SHA256 = {
    "manifest.json": "444b2a1981e327af3ec4cf232f434942458cd8e6e2c9ec3ceb4f86f93c04805c",
    "report.json.gz": "7b55a6df921e2bdb626143f6937ba1613f1084ea8848e26bf78800fcb2a21fa5",
    "checkpoint.json.gz": "80c1c40c31bafdbce0e74a20b1427780011c3183674a1eb6506ba966fe1b6bbe",
    "status.json": "ef3a28dbbddef336b508e4deec5ac43a288d48ad02a54ab1213c947153d55dd2",
    "projection.sqlite": "bbbbecf5b50c05681e0659e22eb3b95d13fd602e48d148602c1e8b1d8606a8e5",
}
SOURCE_PATHS = (
    "scripts/rc6_archive_v3_image_smoke.py", CODEC_FIXTURE, ARCHIVE_FIXTURE,
    "rc6_shadow_runtime/serialization.py", "rc6_shadow_runtime/packed_storage.py",
    "rc6_shadow_runtime/exact_page_storage.py", "rc6_shadow_runtime/archive_components.py",
    "rc6_shadow_runtime/archive_namespace.py", "rc6_shadow_runtime/retention.py",
    "rc6_shadow_runtime/persistence.py", "rc6_shadow_runtime/projection.py",
)
BASELINE = {
    "commit": "6ed979877b2bee8b210940d9601d3f92513f2f15",
    "tree": "141f3dd847f32684512f536f0004b944a44d705b",
    "source_sha256": {
        "rc6_shadow_runtime/serialization.py": "8f8b2be6c0ef1b27f2ca92ca0d2e3455ccba00c3bb7557a193964a4300030163",
        "rc6_shadow_runtime/retention.py": "b340577861cc59f811ffbae01cf72f81a90abc4a7099f11ae1057c8693a6d22b",
        "rc6_shadow_runtime/persistence.py": "989a8ffad5fe5875c298b43787792369de4bca99f23aa04d5c1fdeab1ecf400e",
        "rc6_shadow_runtime/projection.py": "a34fe0b34330a0243fce8aef97a58c3247ed339032f0d605a9cc8a562b2e1b07",
    },
}
LEGACY_REACHABLE_ANCHOR = "67e2b010cf5a209acf272caca42ab138499a4dd0"
LEGACY_GIT_BLOBS = {
    "rc6_shadow_runtime/serialization.py": "536f05829ae2bfa181722f990e5b3eb88244f12e",
    "rc6_shadow_runtime/retention.py": "e310c00f745d17f34d3dbca8047a3d96a7dde451",
    "rc6_shadow_runtime/persistence.py": "21793f6abf5790073e8d1a31dcfb6ff773999afd",
    "rc6_shadow_runtime/projection.py": "dd07d11679518d687a57d8a594f93d63b76e3238",
}
CASE_IDS = (
    "LEGACY_STORAGE_V1_ORIGINAL_BYTES", "PACKED_STORAGE_V2_EXACT_ROUNDTRIP",
    "PACKED_STORAGE_V2_RESEALED_CRC_REJECTED", "UNKNOWN_STORAGE_SCHEMA_REJECTED",
    "PAGE_PACK_FULL_AND_DELTA_EXACT", "PAGE_PACK_WRONG_BASE_REJECTED",
    "LEGACY_ARCHIVE_V2_ORIGINAL_BYTES", "NATIVE_ARCHIVE_V3_FIVE_MEMBERS_EXACT",
    "NATIVE_ARCHIVE_V3_CORRUPT_PACK_REJECTED", "NATIVE_ARCHIVE_V3_MISSING_PACK_REJECTED",
    "ISOLATED_STDLIB_NAMESPACE_LOADER",
)
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
_GEN = re.compile(r"[0-9a-f]{32}\Z")


class SmokeError(ValueError):
    pass


def require(condition, signature):
    if not condition:
        raise SmokeError(signature)


def guard(deadline):
    require(time.monotonic() < deadline, "SMOKE_DEADLINE")


def canonical(value, *, ascii=False):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=ascii, allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def loads(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, "SMOKE_DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    def constant(_value):
        raise SmokeError("SMOKE_NONFINITE_JSON")
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise SmokeError("SMOKE_JSON_INVALID") from error


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_blocks, info.st_mtime_ns,
            info.st_atime_ns, info.st_ctime_ns)


def directory(path):
    """Open every ancestor without following aliases or changing atime."""
    path = Path(path).absolute()
    # Traversal does not enumerate ancestors. O_NOATIME on root-owned / would
    # incorrectly require a capability that the UID1000 container drops.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open("/", flags)
    try:
        for part in path.parts[1:]:
            require(part not in {".", ".."}, "SMOKE_PATH_INVALID")
            next_fd = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_fd
        no_atime = os.open(".", flags | os.O_NOATIME, dir_fd=descriptor)
        require(identity(os.fstat(no_atime)) == identity(os.fstat(descriptor)), "SMOKE_INPUT_CHANGED")
        os.close(descriptor)
        return no_atime
    except BaseException:
        os.close(descriptor)
        raise


def read(path, *, limit, deadline):
    guard(deadline)
    path = Path(path).absolute()
    parent = directory(path.parent)
    descriptor = None
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK,
                             dir_fd=parent)
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, "SMOKE_INPUT_CUSTODY")
        require(0 <= before.st_size <= limit, "SMOKE_INPUT_LIMIT")
        chunks, count = [], 0
        while True:
            guard(deadline)
            chunk = os.read(descriptor, min(65536, limit + 1 - count))
            if not chunk:
                break
            chunks.append(chunk)
            count += len(chunk)
            require(count <= limit, "SMOKE_INPUT_LIMIT")
        require(count == before.st_size and identity(os.fstat(descriptor)) == identity(before)
                and identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) == identity(before),
                "SMOKE_INPUT_CHANGED")
        return b"".join(chunks), identity(before)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def snapshot(root, *, deadline):
    """Bounded bytes plus all observable source stats, including directory atime."""
    root = Path(root).absolute()
    if not root.exists():
        return {}
    result = {}
    def visit(path, relative):
        guard(deadline)
        require(len(result) < MAX_ENTRIES, "SMOKE_INVENTORY_LIMIT")
        fd = directory(path)
        try:
            before = os.fstat(fd)
            require(stat.S_ISDIR(before.st_mode), "SMOKE_INPUT_CUSTODY")
            result[relative] = {"identity": identity(before)}
            entries = []
            with os.scandir(fd) as iterator:
                for entry in iterator:
                    require(len(result) + len(entries) < MAX_ENTRIES, "SMOKE_INVENTORY_LIMIT")
                    entries.append(entry)
            entries.sort(key=lambda entry: entry.name)
            for entry in entries:
                child, name = path / entry.name, relative + "/" + entry.name
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    visit(child, name)
                else:
                    raw, proof = read(child, limit=MAX_FILE, deadline=deadline)
                    result[name] = {"identity": proof, "sha256": sha(raw)}
            require(identity(os.fstat(fd)) == identity(before)
                    and identity(path.lstat()) == identity(before), "SMOKE_INPUT_CHANGED")
        finally:
            os.close(fd)
    visit(root, ".")
    return result


def protected(live, archive, *, deadline):
    return {str(root): snapshot(root, deadline=deadline) for root in
            (Path(live), Path(str(live) + ".authority"), Path(archive))}


def write(path, raw):
    path = Path(path)
    require(type(raw) is bytes and len(raw) <= MAX_FILE, "SMOKE_PRIVATE_BYTES_LIMIT")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def decode_record(record):
    require(type(record) is dict and set(record) == {"payload", "sha256", "bytes"}, "SMOKE_FIXTURE_RECORD")
    require(type(record["bytes"]) is int and 0 <= record["bytes"] <= MAX_FILE
            and isinstance(record["sha256"], str) and _SHA.fullmatch(record["sha256"])
            and isinstance(record["payload"], str) and len(record["payload"]) <= MAX_FIXTURE,
            "SMOKE_FIXTURE_RECORD")
    try:
        encoded = record["payload"].encode("ascii")
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeError) as error:
        raise SmokeError("SMOKE_FIXTURE_BASE64") from error
    require(base64.b64encode(raw) == encoded and len(raw) == record["bytes"]
            and sha(raw) == record["sha256"], "SMOKE_FIXTURE_DIGEST")
    return raw


def typed_payload():
    row = {"identity": ["ÑANDÚ", "ACCIONES", "BYMA", "ARS", "A-24HS"],
           "typed": [False, 0, -0.0, None, "\\u0000", "\0", "observación/🚦"],
           "as_of": "2026-10-05T13:35:00+00:00", "padding": "P" * 8192,
           "nested": {"ratio": 1.25, "last_useful_observation_at": None}}
    return {"schema": "rc6.private-native-codec-control.v1", "rows": [row] * 40}


def float_boundary_payload():
    value = typed_payload()
    value["rows"][0]["typed"] = [False, 0, 0.0, -0.0, 1, -1,
        1.0000000000000002, 1.7976931348623157e308, 5e-324, None,
        "\\u0000", "\0", "observación/🚦"]
    return value


def source_binding(root, manifest_path, *, candidate_sha, tree_sha, deadline):
    require(isinstance(candidate_sha, str) and _GIT_SHA.fullmatch(candidate_sha)
            and isinstance(tree_sha, str) and _GIT_SHA.fullmatch(tree_sha), "SMOKE_CANDIDATE_INVALID")
    raw, _ = read(manifest_path, limit=32 * 1024**2, deadline=deadline)
    manifest = loads(raw)
    require(type(manifest) is dict and manifest.get("schema") == SOURCE_SCHEMA
            and manifest.get("status") == "GREEN" and manifest.get("schema_version") == 2
            and manifest.get("candidate_sha") == candidate_sha
            and manifest.get("candidate_tree_sha") == tree_sha,
            "SMOKE_SOURCE_BINDING")
    unsigned = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    source_canonical = (json.dumps(unsigned, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode()
    require(manifest.get("manifest_sha256") == sha(source_canonical), "SMOKE_SOURCE_MANIFEST_DIGEST")
    rows = manifest.get("files")
    require(type(rows) is list and len(rows) <= 20000
            and type(manifest.get("file_count")) is int and manifest["file_count"] == len(rows), "SMOKE_SOURCE_BINDING")
    indexed = {}
    for row in rows:
        require(type(row) is dict and isinstance(row.get("path"), str) and row["path"] not in indexed,
                "SMOKE_SOURCE_DUPLICATE")
        indexed[row["path"]] = row
    hashes = {}
    for relative in SOURCE_PATHS:
        row = indexed.get(relative)
        require(row is not None and row.get("image_required") is True and row.get("bundle_required") is True
                and row.get("git_mode") == "100644", "SMOKE_SOURCE_COMPONENT_REQUIRED")
        contents, info = read(Path(root) / relative, limit=MAX_FIXTURE, deadline=deadline)
        require(row.get("sha256") == sha(contents) and type(row.get("bytes")) is int
                and row["bytes"] == len(contents) and stat.S_IMODE(info[2]) == 0o644, "SMOKE_SOURCE_COMPONENT_MISMATCH")
        hashes[relative] = sha(contents)
    return {"source_manifest_sha256": sha(raw), "source_components": hashes, "_source_rows": indexed}


def fixture(root, relative, schema, *, deadline):
    raw, _ = read(Path(root) / relative, limit=MAX_FIXTURE, deadline=deadline)
    require(sha(raw) == FIXTURE_SHA256.get(relative), "SMOKE_LEGACY_FIXTURE_BYTES_CHANGED")
    value = loads(raw)
    require(type(value) is dict and value.get("schema") == schema
            and value.get("classification") == "PRIVATE_SYNTHETIC_NATIVE_LEGACY_BYTES"
            and value.get("baseline") == BASELINE, "SMOKE_LEGACY_FIXTURE_ORIGIN")
    return value


@contextmanager
def no_encoder():
    # Load aliases before patching their definitions. Importing the component
    # module while exact_page_storage is patched would retain the forbidden
    # function after context exit and incorrectly block the next native build.
    import rc6_shadow_runtime.archive_components
    def forbidden(*_args, **_kwargs):
        raise SmokeError("SMOKE_RESTORE_ENCODER_CALLED")
    with patch("gzip.compress", forbidden), patch("zlib.compressobj", forbidden), \
         patch("rc6_shadow_runtime.exact_page_storage.encode_page", forbidden), \
         patch("rc6_shadow_runtime.exact_page_storage.encode_page_pack", forbidden), \
         patch("rc6_shadow_runtime.archive_components.encode_page_pack", forbidden), \
         patch("rc6_shadow_runtime.serialization.encode_storage", forbidden), \
         patch("rc6_shadow_runtime.packed_storage.encode_packed_storage", forbidden):
        yield


def expected_failure(call, *, signatures=(), missing_path=None):
    try:
        call()
    except SmokeError:
        raise
    except ValueError as error:
        require(str(error) in signatures, "SMOKE_UNEXPECTED_NEGATIVE_SIGNATURE")
        return str(error)
    except FileNotFoundError as error:
        require(missing_path is not None and error.filename is not None
                and Path(error.filename).absolute() == Path(missing_path).absolute(), "SMOKE_UNEXPECTED_MISSING_SOURCE")
        return "FileNotFoundError:EXACT_DEPENDENCY"
    raise SmokeError("SMOKE_NEGATIVE_ACCEPTED")


def restore(retention, ident, original, *, level):
    require(callable(getattr(retention, "restore_generation", None)), "SMOKE_V3_RESTORE_API_UNAVAILABLE")
    with no_encoder():
        result = retention.restore_generation(ident)
    require(type(result) is dict and set(result) == {"receipt", "manifest", "members", "verification_level"}
            and result["verification_level"] == level and type(result["members"]) is dict
            and set(result["members"]) == MEMBERS and all(type(raw) is bytes for raw in result["members"].values())
            and result["members"] == original, "SMOKE_ORIGINAL_MEMBER_RESTORE_MISMATCH")
    require(type(result["manifest"]) is dict and result["manifest"]["generation_id"] == ident
            and sha(result["members"]["manifest.json"]) == result["receipt"].get("manifest_sha256"),
            "SMOKE_RESTORED_MANIFEST_MISMATCH")
    return result


def copy_namespace(source, target, *, deadline):
    source, target = Path(source), Path(target)
    target.mkdir(mode=0o700)
    for name, record in snapshot(source, deadline=deadline).items():
        if name == ".":
            continue
        destination = target / name[2:]
        if "sha256" not in record:
            destination.mkdir(mode=0o700)
        else:
            raw, _ = read(source / name[2:], limit=MAX_FILE, deadline=deadline)
            write(destination, raw)


def legacy_archive(root, work, retention_class, *, deadline):
    value = fixture(root, ARCHIVE_FIXTURE, "rc6.native-legacy-archive-fixture.v1", deadline=deadline)
    ident = value.get("generation_id")
    require(isinstance(ident, str) and _GEN.fullmatch(ident), "SMOKE_LEGACY_FIXTURE_SHAPE")
    members = value.get("members")
    require(type(members) is dict and set(members) == MEMBERS, "SMOKE_LEGACY_FIXTURE_SHAPE")
    original = {name: decode_record(record) for name, record in members.items()}
    require(value.get("manifest_sha256") == sha(original["manifest.json"]), "SMOKE_LEGACY_MANIFEST_DIGEST")
    files = value.get("archive_files")
    require(type(files) is dict and set(files) == {ident + ".tar.gz", ident + ".receipt.json",
            "CHECKPOINT.json", "archive.lock"}, "SMOKE_LEGACY_FIXTURE_SHAPE")
    live, archive = work / "legacy-live", work / "legacy-archive"
    live.mkdir(mode=0o700)
    archive.mkdir(mode=0o700)
    write(live / "writer.lock", b"")
    generation = live / ("gen-" + ident)
    generation.mkdir(mode=0o700)
    for name, contents in original.items():
        write(generation / name, contents)
    for name, record in files.items():
        write(archive / name, decode_record(record))
    before = protected(live, archive, deadline=deadline)
    retention = retention_class(live, archive_root=archive, archive_format="COMPONENT_V3")
    result = restore(retention, ident, original, level=V2_LEVEL)
    require(result["receipt"].get("schema") == "RC6_SHADOW_ARCHIVE_ACK_V2", "SMOKE_LEGACY_RECEIPT_SCHEMA")
    require(protected(live, archive, deadline=deadline) == before, "SMOKE_LEGACY_SOURCE_MUTATED")
    return {"status": "GREEN", "member_sha256": {name: sha(raw) for name, raw in original.items()},
            "verification_level": V2_LEVEL, "source_bytes_and_all_stats_unchanged": True}


def native_archive(work, files_class, retention_class, *, deadline):
    live, archive = work / "native-live", work / "native-archive"
    identities = []
    for number in (1, 2):
        at = (datetime(2026, 10, 5, 13, 35, tzinfo=timezone.utc) + timedelta(seconds=number)).isoformat()
        base = {"as_of": at, "mode": "SHADOW", "real_orders_sent": 0, "real_routes": "NOT_CALLED",
                "number": number, "typed_control": typed_payload()}
        with files_class(live) as files:
            bundle = files.commit_generation({**base, "source_reports": [], "source_audit": {}, "phase": "OPEN"},
                dict(base), {**base, "status": "SHADOW_OBSERVING"}, source_watermark={"as_of": at,
                "source_identity": "PRIVATE_SYNTHETIC_NATIVE_CODEC_FIXTURE"}, configuration_fingerprint="private-image-codec-v1")
        identities.append(bundle["pointer"]["generation_id"])
    original = {}
    for ident in identities:
        generation = live / ("gen-" + ident)
        original[ident] = {name: read(generation / name, limit=MAX_FILE, deadline=deadline)[0] for name in MEMBERS}
    retention = retention_class(live, archive_root=archive, archive_format="COMPONENT_V3")
    before = {str(root): snapshot(root, deadline=deadline) for root in (live, Path(str(live) + ".authority"))}
    receipts = [retention.archive_generation(live / ("gen-" + ident)) for ident in identities]
    for ident, receipt in zip(identities, receipts):
        require(receipt.get("schema") == "RC6_SHADOW_ARCHIVE_ACK_V3"
                and receipt.get("archiver_id") == "RC6_LOCAL_PRIVATE_COMPONENT_ARCHIVER_V3"
                and receipt.get("archive_uri") == "local-private://" + ident + ".recipe.gz"
                and receipt.get("archive_verified") is True and receipt.get("durable") is True,
                "SMOKE_V3_ARCHIVE_NOT_EXERCISED")
    # ACK/control additions are owned writes; the five original members remain immutable.
    for ident in identities:
        generation = live / ("gen-" + ident)
        previous = before[str(live)]
        require(previous["./gen-" + ident] == {"identity": identity(generation.lstat())},
                "SMOKE_ARCHIVE_ORIGINAL_DIRECTORY_MUTATED")
        for name in MEMBERS:
            raw, info = read(generation / name, limit=MAX_FILE, deadline=deadline)
            require(previous["./gen-" + ident + "/" + name] == {"identity": info, "sha256": sha(raw)},
                    "SMOKE_ARCHIVE_ORIGINAL_MUTATED")
    require(snapshot(Path(str(live) + ".authority"), deadline=deadline) == before[str(live) + ".authority"],
            "SMOKE_ARCHIVE_AUTHORITY_MUTATED")
    raw_current, current_info = read(live / "CURRENT.json", limit=MAX_FILE, deadline=deadline)
    require(before[str(live)]["./CURRENT.json"] == {"identity": current_info, "sha256": sha(raw_current)},
            "SMOKE_ARCHIVE_CURRENT_MUTATED")
    sealed = protected(live, archive, deadline=deadline)
    for ident in identities:
        result = restore(retention, ident, original[ident], level=V3_LEVEL)
        require(result["receipt"]["schema"] == "RC6_SHADOW_ARCHIVE_ACK_V3", "SMOKE_V3_RECEIPT_SCHEMA")
        restore(retention, ident, original[ident], level=V3_LEVEL)
    require(protected(live, archive, deadline=deadline) == sealed, "SMOKE_V3_RESTORE_SOURCE_MUTATED")
    return live, archive, identities, original, {"status": "GREEN", "generations": 2,
        "original_members_per_generation": 5, "verification_level": V3_LEVEL,
        "member_sha256": [{name: sha(raw) for name, raw in original[ident].items()} for ident in identities],
        "source_bytes_and_all_stats_unchanged": True, "repeated_restore_fresh_verification": True}


def broken_archive(work, live, archive, ident, original, mutation, retention_class, *, deadline):
    broken_live, broken_archive = work / (mutation + "-live"), work / (mutation + "-archive")
    copy_namespace(live, broken_live, deadline=deadline)
    copy_namespace(Path(str(live) + ".authority"), Path(str(broken_live) + ".authority"), deadline=deadline)
    copy_namespace(archive, broken_archive, deadline=deadline)
    ack = broken_live / ("archive-ack-" + ident + ".json")
    require(ack.is_file(), "SMOKE_NATIVE_ACK_NOT_CREATED")
    ack.unlink()  # Private negative-fixture preparation, before the invariant snapshot.
    retention = retention_class(broken_live, archive_root=broken_archive, archive_format="COMPONENT_V3")
    warmup = protected(broken_live, broken_archive, deadline=deadline)
    restore(retention, ident, original, level=V3_LEVEL)
    require(protected(broken_live, broken_archive, deadline=deadline) == warmup,
            "SMOKE_NEGATIVE_WARMUP_SOURCE_MUTATED")
    import gzip
    recipe_raw, _ = read(broken_archive / (ident + ".recipe.gz"), limit=MAX_FILE, deadline=deadline)
    recipe = loads(gzip.decompress(recipe_raw))
    packs = recipe.get("packs")
    require(type(packs) is list and packs and all(isinstance(name, str)
            and re.fullmatch(r"[0-9a-f]{64}\.cas\.pack", name) for name in packs), "SMOKE_V3_DEPENDENCY_NOT_EXERCISED")
    dependency = broken_archive / packs[-1]
    if mutation == "corrupt":
        raw, _ = read(dependency, limit=MAX_FILE, deadline=deadline)
        require(bool(raw), "SMOKE_V3_DEPENDENCY_NOT_EXERCISED")
        with dependency.open("r+b") as stream:
            stream.seek(len(raw) - 1)
            stream.write(bytes([raw[-1] ^ 1]))
            stream.flush()
            os.fsync(stream.fileno())
    else:
        require(mutation == "missing", "SMOKE_FIXTURE_MUTATION_INVALID")
        dependency.unlink()
    before = protected(broken_live, broken_archive, deadline=deadline)
    signatures = ("RETENTION_COMPONENT_PACK_HASH_MISMATCH",) if mutation == "corrupt" else ()
    missing = dependency if mutation == "missing" else None
    with no_encoder():
        restore_error = expected_failure(lambda: retention.restore_generation(ident), signatures=signatures, missing_path=missing)
        archive_error = expected_failure(lambda: retention.archive_generation(broken_live / ("gen-" + ident)),
                                         signatures=signatures, missing_path=missing)
    require(not ack.exists() and protected(broken_live, broken_archive, deadline=deadline) == before,
            "SMOKE_BROKEN_ARCHIVE_AUTHORIZED_OR_MUTATED")
    return {"status": "GREEN_EXPECTED_RED", "restore_signature": restore_error,
            "archive_signature": archive_error, "new_ack_written": False, "origin_deleted": False,
            "same_reader_success_before_mutation": True,
            "source_bytes_and_all_stats_unchanged": True}


def isolated_namespace(root, archive, *, deadline):
    source, _ = read(Path(root) / "rc6_shadow_runtime/archive_namespace.py", limit=MAX_FIXTURE, deadline=deadline)
    payload = {"source": source.decode("utf-8"), "sha256": sha(source), "root": str(archive), "owner_uid": os.geteuid()}
    program = (
        "import hashlib,json,sys,types,socket\n"
        "attempts=[]\n"
        "def no_network(*a,**k):attempts.append(1);raise AssertionError('NETWORK_FORBIDDEN')\n"
        "socket.socket.connect=no_network;socket.socket.connect_ex=no_network;socket.socket.sendto=no_network\n"
        "socket.create_connection=no_network;socket.getaddrinfo=no_network\n"
        "p=json.loads(sys.stdin.buffer.read(1048577));assert hashlib.sha256(p['source'].encode()).hexdigest()==p['sha256']\n"
        "m=types.ModuleType('rc6_candidate_archive_namespace');sys.modules[m.__name__]=m\n"
        "exec(compile(p['source'],'<frozen-archive-namespace>','exec'),vars(m))\n"
        "v=m.inspect_archive(p['root'],owner_uid=p['owner_uid'])\n"
        "assert not attempts\n"
        "assert not any(n=='rc6_shadow_runtime' or n.startswith('rc6_shadow_runtime.') for n in sys.modules)\n"
        "print(json.dumps(v,sort_keys=True,separators=(',',':')))\n"
    )
    before = snapshot(archive, deadline=deadline)
    result = subprocess.run([sys.executable, "-I", "-S", "-c", program], input=canonical(payload),
                            capture_output=True, timeout=max(.001, deadline - time.monotonic()), cwd="/tmp")
    require(result.returncode == 0 and len(result.stdout) + len(result.stderr) <= 8192,
            "SMOKE_NAMESPACE_NOT_AUTONOMOUS")
    observation = loads(result.stdout)
    require(observation.get("schema") == "rc6.shadow-archive-namespace-admission.v1"
            and observation.get("verification_level") == "BOUNDED_ARCHIVE_NAMESPACE_AND_CUSTODY_METADATA"
            and observation.get("state") == "WITHIN_QUOTA"
            and type(observation.get("allocated_bytes")) is int
            and type(observation.get("allocated_directory_bytes")) is int
            and observation["allocated_bytes"] + observation["allocated_directory_bytes"] < 512 * 1024**2
            and snapshot(archive, deadline=deadline) == before, "SMOKE_NAMESPACE_CONTRACT")
    return {"status": "GREEN", "source_sha256": sha(source), "isolated_python_flags": ["-I", "-S"],
            "verification_level": observation["verification_level"], "repo_package_imports": 0,
            "source_bytes_and_all_stats_unchanged": True}


def verify_imports(root, source_rows, *, deadline):
    imports = {}
    for name, module in tuple(sys.modules.items()):
        if not (name == "rc6_shadow_runtime" or name.startswith("rc6_shadow_runtime.")):
            continue
        filename = getattr(module, "__file__", None)
        require(filename is not None, "SMOKE_IMPORTED_MODULE_UNBOUND")
        path = Path(filename).absolute()
        require(path.is_relative_to(Path(root).absolute()), "SMOKE_FOREIGN_MODULE_IMPORTED")
        relative = path.relative_to(Path(root).absolute()).as_posix()
        # Every imported package source is bound, including transitive modules.
        row = source_rows.get(relative)
        require(type(row) is dict and row.get("image_required") is True
                and row.get("bundle_required") is True and row.get("git_mode") == "100644",
                "SMOKE_IMPORTED_MODULE_UNBOUND")
        raw, info = read(path, limit=MAX_FIXTURE, deadline=deadline)
        require(sha(raw) == row.get("sha256") and type(row.get("bytes")) is int
                and row["bytes"] == len(raw) and stat.S_IMODE(info[2]) == 0o644,
                "SMOKE_IMPORTED_MODULE_SOURCE_MISMATCH")
        imports[name] = {"path": relative, "sha256": sha(raw)}
    return imports


def validate_report(report, *, candidate_sha, tree_sha, image_id, source_manifest_sha256,
                    source_sha256, expected_uid=1000, expected_gid=1000):
    """Validate receipt bindings; external execution authority remains the caller's."""
    require(type(report) is dict and report.get("schema") == SCHEMA and report.get("status") == "GREEN"
            and report.get("scope") == SCOPE and report.get("runtime_approval") is False,
            "SMOKE_RECEIPT_NOT_GREEN")
    require(report.get("candidate_sha") == candidate_sha and report.get("candidate_tree_sha") == tree_sha
            and report.get("image_id_argument") == image_id
            and report.get("image_id_authority") == "CALLER_MUST_VERIFY_DOCKER_AND_EXTERNAL_GITHUB_TUPLE"
            and report.get("source_manifest_sha256") == source_manifest_sha256,
            "SMOKE_RECEIPT_BINDING")
    hashes = report.get("source_components")
    require(type(hashes) is dict and set(hashes) == set(SOURCE_PATHS)
            and all(hashes[name] == source_sha256.get(name) for name in SOURCE_PATHS), "SMOKE_RECEIPT_SOURCE_BINDING")
    require(all(hashes[name] == digest for name, digest in FIXTURE_SHA256.items()), "SMOKE_RECEIPT_LEGACY_FIXTURE_BINDING")
    imported = report.get("imported_source_modules")
    mandatory = {name[:-3].replace("/", ".") for name in SOURCE_PATHS[3:]}
    require(type(imported) is dict and mandatory <= set(imported), "SMOKE_RECEIPT_IMPORT_BINDING")
    for name, row in imported.items():
        require(isinstance(name, str) and (name == "rc6_shadow_runtime" or name.startswith("rc6_shadow_runtime."))
                and type(row) is dict and set(row) == {"path", "sha256"}
                and isinstance(row["path"], str)
                and row["path"] in {name.replace(".", "/") + ".py", name.replace(".", "/") + "/__init__.py"}
                and isinstance(row["sha256"], str) and _SHA.fullmatch(row["sha256"])
                and row["sha256"] == source_sha256.get(row["path"]),
                "SMOKE_RECEIPT_IMPORT_BINDING")
    require(report.get("legacy_baseline") == BASELINE and type(report.get("execution_uid")) is int
            and report["execution_uid"] == expected_uid and type(report.get("execution_gid")) is int
            and report["execution_gid"] == expected_gid,
            "SMOKE_RECEIPT_CUSTODY")
    for field in ("network_attempts", "provider_requests", "real_orders_sent"):
        require(type(report.get(field)) is int and report[field] == 0, "SMOKE_RECEIPT_SAFETY")
    require(report.get("real_routes") == "NOT_CALLED", "SMOKE_RECEIPT_SAFETY")
    require(type(report.get("elapsed_seconds")) in {int, float} and 0 <= report["elapsed_seconds"] < MAX_SECONDS
            and type(report.get("deadline_seconds")) is int and report["deadline_seconds"] == MAX_SECONDS
            and type(report.get("rss_peak_bytes")) is int and 0 < report["rss_peak_bytes"] <= MAX_RSS
            and type(report.get("scratch_allocated_bytes")) is int and 0 <= report["scratch_allocated_bytes"] <= MAX_SCRATCH
            and type(report.get("output_limit_bytes")) is int and report["output_limit_bytes"] == MAX_OUTPUT,
            "SMOKE_RECEIPT_BOUNDS")
    cases = report.get("cases")
    require(type(cases) is dict and set(cases) == set(CASE_IDS) and type(report.get("case_count")) is int
            and report["case_count"] == len(CASE_IDS), "SMOKE_RECEIPT_CASE_CLOSURE")
    negatives = {CASE_IDS[index] for index in (2, 3, 5, 8, 9)}
    for name, result in cases.items():
        require(type(result) is dict and result.get("status") == ("GREEN_EXPECTED_RED" if name in negatives else "GREEN"),
                "SMOKE_RECEIPT_CASE_STATUS")
    first, second = cases[CASE_IDS[0]], cases[CASE_IDS[1]]
    logical = canonical(typed_payload(), ascii=True)
    require(isinstance(first.get("wire_sha256"), str) and _SHA.fullmatch(first["wire_sha256"])
            and first["wire_sha256"] == LEGACY_WIRE_SHA256
            and isinstance(first.get("logical_sha256"), str) and _SHA.fullmatch(first["logical_sha256"])
            and first.get("logical_sha256") == second.get("logical_sha256")
            and first.get("logical_sha256") == sha(logical)
            and type(first.get("logical_bytes")) is int and 256 * 1024 < first["logical_bytes"] <= 8 * 1024**2
            and first["logical_bytes"] == len(logical)
            and type(second.get("logical_bytes")) is int and second["logical_bytes"] == first["logical_bytes"]
            and second.get("storage_schema") == "rc6.lossless-json-storage.v2", "SMOKE_RECEIPT_CODEC_BYTES")
    floating = canonical(float_boundary_payload(), ascii=True)
    require(second.get("signed_zero_and_finite_float_types_exact") is True
            and second.get("finite_float_control_sha256") == sha(floating)
            and type(second.get("finite_float_control_bytes")) is int
            and second["finite_float_control_bytes"] == len(floating), "SMOKE_RECEIPT_FLOAT_CONTROL")
    pages = cases[CASE_IDS[4]]
    require(type(pages.get("dependency_depth")) is int and pages["dependency_depth"] == 1
            and type(pages.get("delta_pages")) is int and pages["delta_pages"] == 2
            and type(pages.get("recovery_encoder_calls")) is int and pages["recovery_encoder_calls"] == 0
            and all(isinstance(pages.get(key), str) and _SHA.fullmatch(pages[key])
                    for key in ("full_sha256", "target_sha256")), "SMOKE_RECEIPT_PAGE_BYTES")
    require(cases[CASE_IDS[2]].get("signature") == "SHADOW_STORAGE_GZIP_INVALID"
            and cases[CASE_IDS[2]].get("public_hashes_resealed") is True
            and cases[CASE_IDS[3]].get("signature") == "SHADOW_STORAGE_SCHEMA_UNSUPPORTED"
            and cases[CASE_IDS[5]].get("signature") == "PACK_PREVIOUS_SHA256_MISMATCH", "SMOKE_RECEIPT_NEGATIVE_SIGNATURE")
    for index, signature in ((8, "RETENTION_COMPONENT_PACK_HASH_MISMATCH"), (9, "FileNotFoundError:EXACT_DEPENDENCY")):
        row = cases[CASE_IDS[index]]
        require(row.get("restore_signature") == signature and row.get("archive_signature") == signature
                and row.get("new_ack_written") is False and row.get("origin_deleted") is False
                and row.get("same_reader_success_before_mutation") is True
                and row.get("source_bytes_and_all_stats_unchanged") is True, "SMOKE_RECEIPT_NEGATIVE_INVARIANT")
    require(cases[CASE_IDS[6]].get("verification_level") == V2_LEVEL
            and cases[CASE_IDS[6]].get("source_bytes_and_all_stats_unchanged") is True
            and cases[CASE_IDS[7]].get("verification_level") == V3_LEVEL
            and type(cases[CASE_IDS[7]].get("generations")) is int and cases[CASE_IDS[7]]["generations"] == 2
            and type(cases[CASE_IDS[7]].get("original_members_per_generation")) is int
            and cases[CASE_IDS[7]]["original_members_per_generation"] == 5
            and cases[CASE_IDS[7]].get("source_bytes_and_all_stats_unchanged") is True
            and cases[CASE_IDS[7]].get("repeated_restore_fresh_verification") is True,
            "SMOKE_RECEIPT_ORIGINAL_BYTES")
    member_hashes = [cases[CASE_IDS[6]].get("member_sha256")]
    require(member_hashes[0] == LEGACY_MEMBER_SHA256, "SMOKE_RECEIPT_LEGACY_MEMBER_BINDING")
    modern = cases[CASE_IDS[7]].get("member_sha256")
    require(type(modern) is list and len(modern) == 2, "SMOKE_RECEIPT_ORIGINAL_BYTES")
    member_hashes.extend(modern)
    for hashes in member_hashes:
        require(type(hashes) is dict and set(hashes) == MEMBERS
                and all(isinstance(value, str) and _SHA.fullmatch(value) for value in hashes.values()),
                "SMOKE_RECEIPT_ORIGINAL_BYTES")
    require(cases[CASE_IDS[10]].get("verification_level") == "BOUNDED_ARCHIVE_NAMESPACE_AND_CUSTODY_METADATA"
            and cases[CASE_IDS[10]].get("source_sha256") == source_sha256.get("rc6_shadow_runtime/archive_namespace.py")
            and cases[CASE_IDS[10]].get("isolated_python_flags") == ["-I", "-S"]
            and type(cases[CASE_IDS[10]].get("repo_package_imports")) is int
            and cases[CASE_IDS[10]]["repo_package_imports"] == 0
            and cases[CASE_IDS[10]].get("source_bytes_and_all_stats_unchanged") is True,
            "SMOKE_RECEIPT_NAMESPACE_SCOPE")
    require(report.get("nine_hour_archive_capacity") == "PENDING_SEPARATE_NATIVE_GATE"
            and report.get("large_producer_health_browser") == "PENDING_SEPARATE_NATIVE_GATES",
            "SMOKE_RECEIPT_SCOPE_ESCALATION")
    require(len(canonical(report)) <= MAX_OUTPUT, "SMOKE_OUTPUT_LIMIT")
    return report


def _run_smoke(root, manifest_path, *, candidate_sha, tree_sha, image_id, deadline=None):
    started = time.monotonic()
    deadline = min(deadline, started + MAX_SECONDS) if deadline is not None else started + MAX_SECONDS
    require(isinstance(image_id, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", image_id), "SMOKE_IMAGE_ID_INVALID")
    binding = source_binding(root, manifest_path, candidate_sha=candidate_sha, tree_sha=tree_sha, deadline=deadline)
    source_rows = binding.pop("_source_rows")
    import gzip
    from rc6_shadow_runtime import serialization, packed_storage, exact_page_storage, archive_components
    from rc6_shadow_runtime.persistence import EvidenceFiles
    from rc6_shadow_runtime.retention import EvidenceRetention
    require(callable(getattr(EvidenceRetention, "restore_generation", None)), "SMOKE_V3_RESTORE_API_UNAVAILABLE")
    verify_imports(root, source_rows, deadline=deadline)
    cases = {}
    legacy = fixture(root, CODEC_FIXTURE, "rc6.native-legacy-codec-fixture.v1", deadline=deadline)
    logical, wire = typed_payload(), legacy.get("wire")
    logical_raw = canonical(logical, ascii=True)
    require(len(logical_raw) > 256 * 1024 and legacy.get("logical_sha256") == sha(logical_raw)
            and legacy.get("logical_bytes") == len(logical_raw)
            and legacy.get("wire_sha256") == sha(canonical(wire))
            and isinstance(wire, dict) and wire.get("schema") == "rc6.lossless-json-storage.v1",
            "SMOKE_LEGACY_CODEC_DIGEST")
    limits = {"durable_limit": 4 * 1024**2, "expansion_limit": 8 * 1024**2}
    with no_encoder():
        decoded = serialization.decode_storage(wire, **limits)
        legacy_proof = serialization.verify_storage_wire(wire, **limits, deadline=deadline)
    require(canonical(decoded, ascii=True) == logical_raw
            and legacy_proof.get("storage_schema") == "rc6.lossless-json-storage.v1", "SMOKE_LEGACY_CODEC_ROUNDTRIP")
    cases[CASE_IDS[0]] = {"status": "GREEN", "wire_sha256": legacy["wire_sha256"],
                          "logical_sha256": sha(logical_raw), "logical_bytes": len(logical_raw)}
    packed = packed_storage.encode_packed_storage(logical, **limits)
    require(isinstance(packed, dict) and packed.get("schema") == "rc6.lossless-json-storage.v2", "SMOKE_PACKED_V2_NOT_EXERCISED")
    with no_encoder():
        decoded = serialization.decode_storage(packed, **limits)
        proof = serialization.verify_storage_wire(packed, **limits, deadline=deadline)
    require(canonical(decoded, ascii=True) == logical_raw and proof.get("storage_schema") == "rc6.lossless-json-storage.v2"
            and decoded["rows"][0] is not decoded["rows"][1], "SMOKE_PACKED_V2_ROUNDTRIP")
    cases[CASE_IDS[1]] = {"status": "GREEN", "logical_sha256": sha(logical_raw),
                         "logical_bytes": len(logical_raw), "storage_schema": proof["storage_schema"]}
    floating = float_boundary_payload()
    floating_raw = canonical(floating, ascii=True)
    floating_wire = packed_storage.encode_packed_storage(floating, **limits)
    require(floating_wire.get("schema") == "rc6.lossless-json-storage.v2", "SMOKE_FLOAT_V2_NOT_EXERCISED")
    with no_encoder():
        floating_value = serialization.decode_storage(floating_wire, **limits)
        serialization.verify_storage_wire(floating_wire, **limits, deadline=deadline)
    require(canonical(floating_value, ascii=True) == floating_raw
            and len({id(row) for row in floating_value["rows"]}) == 40, "SMOKE_FLOAT_EXACT_ROUNDTRIP")
    cases[CASE_IDS[1]].update(signed_zero_and_finite_float_types_exact=True,
        finite_float_control_sha256=sha(floating_raw), finite_float_control_bytes=len(floating_raw))
    broken = deepcopy(packed)
    packet = broken["packets"][0]
    compressed = bytearray(base64.b64decode(packet["payload"], validate=True))
    compressed[-8] ^= 1
    packet.update(payload=base64.b64encode(compressed).decode("ascii"), sha256=sha(compressed))
    broken["storage_sha256"] = sha(canonical({key: value for key, value in broken.items() if key != "storage_sha256"}))
    signature = expected_failure(lambda: serialization.verify_storage_wire(broken, **limits, deadline=deadline),
                                 signatures=("SHADOW_STORAGE_GZIP_INVALID",))
    cases[CASE_IDS[2]] = {"status": "GREEN_EXPECTED_RED", "signature": signature, "public_hashes_resealed": True}
    unknown = deepcopy(packed)
    unknown["schema"] = "rc6.lossless-json-storage.v999"
    signature = expected_failure(lambda: serialization.decode_storage(unknown, **limits),
                                 signatures=("SHADOW_STORAGE_SCHEMA_UNSUPPORTED",))
    cases[CASE_IDS[3]] = {"status": "GREEN_EXPECTED_RED", "signature": signature}
    previous = random.Random(1).randbytes(4096) + random.Random(2).randbytes(4096)
    target = bytearray(previous)
    target[80:88], target[5000:5008] = b"newclock", b"newvalue"
    target = bytes(target)
    full, full_info = exact_page_storage.encode_page_pack(previous)
    delta, delta_info = exact_page_storage.encode_page_pack(target, previous)
    require(full_info["dependency_depth"] == 0 and delta_info["delta_pages"] == 2
            and delta_info["dependency_depth"] == 1, "SMOKE_PAGE_DELTA_NOT_EXERCISED")
    with no_encoder():
        restored_full = exact_page_storage.decode_page_pack(full, expected_pack_sha256=full_info["pack_sha256"],
                                                           expected_target_sha256=full_info["target_sha256"])
        restored_delta = exact_page_storage.decode_page_pack(delta, expected_pack_sha256=delta_info["pack_sha256"],
            expected_target_sha256=delta_info["target_sha256"], previous_bytes=restored_full, previous_depth=0)
    require(restored_full == previous and restored_delta == target, "SMOKE_PAGE_SOURCE_MISMATCH")
    cases[CASE_IDS[4]] = {"status": "GREEN", "full_sha256": sha(previous), "target_sha256": sha(target),
                         "dependency_depth": 1, "delta_pages": 2, "recovery_encoder_calls": 0}
    signature = expected_failure(lambda: exact_page_storage.decode_page_pack(delta,
        expected_pack_sha256=delta_info["pack_sha256"], expected_target_sha256=delta_info["target_sha256"],
        previous_bytes=b"x" * 8192, previous_depth=0), signatures=("PACK_PREVIOUS_SHA256_MISMATCH",))
    cases[CASE_IDS[5]] = {"status": "GREEN_EXPECTED_RED", "signature": signature}
    with tempfile.TemporaryDirectory(prefix="rc6-codec-image-smoke-", dir="/tmp") as temporary:
        work = Path(temporary)
        cases[CASE_IDS[6]] = legacy_archive(root, work, EvidenceRetention, deadline=deadline)
        live, archive, identifiers, _original, cases[CASE_IDS[7]] = native_archive(work, EvidenceFiles, EvidenceRetention, deadline=deadline)
        cases[CASE_IDS[8]] = broken_archive(work, live, archive, identifiers[-1], _original[identifiers[-1]],
                                          "corrupt", EvidenceRetention, deadline=deadline)
        cases[CASE_IDS[9]] = broken_archive(work, live, archive, identifiers[-1], _original[identifiers[-1]],
                                          "missing", EvidenceRetention, deadline=deadline)
        cases[CASE_IDS[10]] = isolated_namespace(root, archive, deadline=deadline)
        inventory = snapshot(work, deadline=deadline)
        allocated = sum(row["identity"][7] * 512 for row in inventory.values())
        require(allocated <= MAX_SCRATCH, "SMOKE_SCRATCH_LIMIT")
    guard(deadline)
    require(tuple(cases) == CASE_IDS, "SMOKE_CASE_CLOSURE")
    # Linux reports ru_maxrss in KiB; the canonical image targets Linux only.
    require(sys.platform == "linux", "SMOKE_PLATFORM_UNSUPPORTED")
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    require(rss <= MAX_RSS, "SMOKE_RSS_LIMIT")
    imported = verify_imports(root, source_rows, deadline=deadline)
    report = {"schema": SCHEMA, "status": "GREEN", "scope": SCOPE,
        "candidate_sha": candidate_sha, "candidate_tree_sha": tree_sha,
        "image_id_argument": image_id, "image_id_authority": "CALLER_MUST_VERIFY_DOCKER_AND_EXTERNAL_GITHUB_TUPLE",
        **binding, "imported_source_modules": imported, "legacy_baseline": BASELINE,
        "cases": cases, "case_count": len(cases), "elapsed_seconds": time.monotonic() - started,
        "deadline_seconds": MAX_SECONDS, "rss_peak_bytes": rss, "scratch_allocated_bytes": allocated,
        "output_limit_bytes": MAX_OUTPUT, "execution_uid": os.geteuid(), "execution_gid": os.getegid(),
        "network_attempts": 0, "provider_requests": 0, "real_orders_sent": 0, "real_routes": "NOT_CALLED",
        "runtime_approval": False, "nine_hour_archive_capacity": "PENDING_SEPARATE_NATIVE_GATE",
        "large_producer_health_browser": "PENDING_SEPARATE_NATIVE_GATES"}
    require(len(canonical(report)) <= MAX_OUTPUT, "SMOKE_OUTPUT_LIMIT")
    validate_report(report, candidate_sha=candidate_sha, tree_sha=tree_sha, image_id=image_id,
                    source_manifest_sha256=binding["source_manifest_sha256"],
                    source_sha256={name: row.get("sha256") for name, row in source_rows.items()},
                    expected_uid=os.geteuid(), expected_gid=os.getegid())
    return report


def run_smoke(root, manifest_path, *, candidate_sha, tree_sha, image_id, deadline=None):
    attempts = []
    def forbidden(*_args, **_kwargs):
        attempts.append(1)
        raise SmokeError("SMOKE_NETWORK_FORBIDDEN")
    with patch.object(socket.socket, "connect", forbidden), patch.object(socket.socket, "connect_ex", forbidden), \
         patch.object(socket.socket, "sendto", forbidden), patch.object(socket, "create_connection", forbidden), \
         patch.object(socket, "getaddrinfo", forbidden):
        result = _run_smoke(root, manifest_path, candidate_sha=candidate_sha, tree_sha=tree_sha,
                            image_id=image_id, deadline=deadline)
        require(not attempts, "SMOKE_NETWORK_ATTEMPTED")
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", type=Path, default=Path("/app/POROTA_SOURCE_PROVENANCE.json"))
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--tree-sha", required=True)
    parser.add_argument("--image-id", required=True)
    args = parser.parse_args(argv)
    sys.dont_write_bytecode = True
    started = time.monotonic()
    def alarm(_signum, _frame):
        raise SmokeError("SMOKE_DEADLINE")
    old_handler = signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, MAX_SECONDS)
    try:
        report = run_smoke(Path(__file__).absolute().parents[1], args.source_manifest,
            candidate_sha=args.candidate_sha, tree_sha=args.tree_sha, image_id=args.image_id,
            deadline=started + MAX_SECONDS)
        exit_code = 0
    except Exception as error:
        signature = str(error) if isinstance(error, SmokeError) else "SMOKE_NATIVE_CONTROL_FAILED:" + type(error).__name__
        report = {"schema": SCHEMA, "status": "RED", "scope": SCOPE, "signature": signature,
            "elapsed_seconds": time.monotonic() - started, "runtime_approval": False,
            "image_id_authority": "CALLER_MUST_VERIFY_DOCKER_AND_EXTERNAL_GITHUB_TUPLE"}
        exit_code = 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
    payload = canonical(report) + b"\n"
    if len(payload) > MAX_OUTPUT:
        payload = canonical({"schema": SCHEMA, "status": "RED", "signature": "SMOKE_OUTPUT_LIMIT", "runtime_approval": False}) + b"\n"
        exit_code = 1
    sys.stdout.buffer.write(payload)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
