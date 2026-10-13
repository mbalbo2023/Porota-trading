"""Post-FIN Source RAW packaging by same-filesystem rename, never qualification.

Only the issued RawCapture can enter the procedural packer. Documentary plans
and package manifests cannot mint FIN, cleanup, an Actions artifact or G5 GREEN.
The original native stream and namespace evidence remain outside these parts.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
from datetime import datetime, timezone

from scripts import rc6_capacity_calibration as calibration

MIB = 1024**2
GIB = 1024**3
MAX_LEAF = 16 * MIB
MAX_PART = 128 * MIB
MAX_MEMBERS = 4096
MAX_PARTS = 160
MAX_RAW = 20 * GIB
MAX_PROOFS = 128 * MIB
MAX_MANIFEST = 16 * MIB
RESERVE = 4 * GIB
CHUNK = 512 * 1024
FAMILY_SCHEMA = "rc6.g5b-frame-parts-source-preupload.v2"
PACKAGE_SCHEMA = "rc6.g5a-original-source-parts-package.v2"
PROOF_SCHEMA = "rc6.g5a-own-rename-part-proof.v2"
IDENTITY_FIELDS = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink",
    "st_size", "st_blocks", "st_atime_ns", "st_mtime_ns", "st_ctime_ns")
UNCHANGED_ON_RENAME = tuple(key for key in IDENTITY_FIELDS if key != "st_ctime_ns")
SHA = re.compile(r"[0-9a-f]{64}\Z")
FLAT = re.compile(r"[0-9]{6}\.raw\Z")
FRAME_PREFIX = "control/original-cas-comparison/original-frames/"
SOURCE_ROLES = {
    "native_Horizon": "project/result.json",
    "g5a_quota": "control/original-cas-comparison/g5a-quota.json.gz",
    "g5a_ledger": "control/original-cas-comparison/g5a-ledger.json.gz",
    "g5b_original_comparison": "control/original-cas-comparison/comparison-result.json.gz",
    "g5b_writer_binding": "control/original-cas-comparison/g5b-writer-binding.json.gz",
    "g5b_original_frames_manifest": "control/original-cas-comparison/g5b-original-frames-manifest.json.gz",
}
OFFICIAL_UPLOAD_STEPS = {"frames": "Upload original G5A frame parts (official)",
    "runtime": "Upload original G5A runtime parts (official)"}
_ISSUED = {}
_ATTEMPTS = {}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def clone(value):
    return calibration.decode(calibration.wire(value))


def identity(info):
    return {field: getattr(info, field) for field in IDENTITY_FIELDS}


def _birth():
    return {"pid": os.getpid(), "uid": os.geteuid(), "gid": os.getegid(),
        "start_ticks": Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19],
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}


def _literal_path(value):
    require(type(value) in (str, Path) or isinstance(value, Path), "G5A_PARTS_LITERAL_PATH_REQUIRED")
    text = str(value)
    path = Path(text)
    require(path.is_absolute() and str(path) == text and "\x00" not in text
        and "\\" not in text and not any(part in (".", "..") for part in path.parts)
        and not any(node.is_symlink() for node in (path, *path.parents)),
        "G5A_PARTS_NOFOLLOW_LITERAL_PATH_REQUIRED")
    return path


def _owned_directory(path):
    path = _literal_path(path)
    info = path.lstat()
    require(os.getuid() == os.geteuid() > 0 and os.getgid() == os.getegid()
        and stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700
        and (info.st_uid, info.st_gid) == (os.geteuid(), os.getegid()),
        "G5A_PARTS_NONROOT_OWN0700_DIRECTORY_REQUIRED")
    return info


def _relative_source(name):
    require(type(name) is str and 0 < len(name) <= 4096 and "\x00" not in name and "\\" not in name,
        "G5A_PARTS_LITERAL_SOURCE_MEMBER_REQUIRED")
    path = PurePosixPath(name)
    require(not path.is_absolute() and path.as_posix() == name
        and not any(part in (".", "..") for part in path.parts)
        and (path.parts[0] in ("project", "control", "edquot-probe") or name == "g5a-worker-report.json"),
        "G5A_PARTS_ONLY_ORIGINAL_SOURCE_MEMBERS_REQUIRED")


def plan_documentary(documentary):
    """Pure planning of declared metadata; this function grants no authority."""
    require(type(documentary) is dict and documentary.get("schema") == "rc6.g5a-post-fin-lossless-capture.v2"
        and documentary.get("complete_original_1202_frame_set_observed") is True
        and type(documentary.get("actual_original_cuts")) is int and documentary["actual_original_cuts"] == 1202
        and documentary.get("worker_report", {}).get("report", {}).get("status") == "EVIDENCE_ACQUIRED_ONLY_NO_G5_GREEN"
        and documentary.get("G5_GREEN") is False and documentary.get("qualification_claimed") is False
        and documentary.get("artifact_ids_invented") is False,
        "G5A_PARTS_LOGICAL_RED_OR_INCOMPLETE_CAPTURE_CANNOT_CREATE_FAMILY")
    require(all(type(documentary.get(field)) is str and re.fullmatch(r"[0-9a-f]{40}", documentary[field])
        for field in ("source_sha", "source_tree"))
        and type(documentary.get("writer_codec_restorer_binding_sha256")) is str
        and SHA.fullmatch(documentary["writer_codec_restorer_binding_sha256"])
        and type(documentary.get("binding")) is dict,
        "G5A_PARTS_EXACT_SOURCE_WRITER_BINDING_REQUIRED")
    files = documentary.get("files")
    frames = documentary.get("original_frame_members")
    require(type(files) is list and 1202 <= len(files) <= 100000 and type(frames) is list and len(frames) == 1202,
        "G5A_PARTS_FULL1202_BOUNDED_SOURCE_INVENTORY_REQUIRED")
    by_name, names, total = {}, set(), 0
    for row in files:
        require(type(row) is dict and set(row) == {"relative_source", "capture_file", "bytes", "sha256", "original_stream_offset"},
            "G5A_PARTS_EXACT_SOURCE_COPY_ROW_REQUIRED")
        _relative_source(row["relative_source"])
        require(type(row["capture_file"]) is str and FLAT.fullmatch(row["capture_file"])
            and row["capture_file"] not in names and row["relative_source"] not in by_name
            and type(row["bytes"]) is int and 0 <= row["bytes"] <= MAX_LEAF
            and type(row["original_stream_offset"]) is int and row["original_stream_offset"] > 0
            and type(row["sha256"]) is str and SHA.fullmatch(row["sha256"]),
            "G5A_PARTS_ORIGINAL16MIB_LEAF_HASH_OFFSET_OR_DUPLICATE_RED")
        names.add(row["capture_file"]); by_name[row["relative_source"]] = clone(row); total += row["bytes"]
    require(total <= MAX_RAW, "G5A_PARTS_RAW20GIB_TOTAL_RED")
    groups, frame_names = [], set()
    for cut, frame in enumerate(frames):
        require(type(frame) is dict and set(frame) == {"cut_index", "path", "bytes", "sha256", "capture_file"}
            and type(frame["cut_index"]) is int and frame["cut_index"] == cut
            and frame["path"] == "cut-" + str(cut).zfill(4) + ".json.gz",
            "G5A_PARTS_EXACT1202_ORIGINAL_FRAME_SEQUENCE_REQUIRED")
        name = FRAME_PREFIX + frame["path"]; row = by_name.get(name)
        require(type(row) is dict and 0 < row["bytes"] <= MAX_LEAF
            and all(row[key] == frame[key] for key in ("bytes", "sha256", "capture_file")),
            "G5A_PARTS_ORIGINAL_FRAME_CAPTURE_BYTES_REBOUND")
        frame_names.add(name)
        groups.append({**row, "cut_index": cut, "member": frame["path"]})
    require({name for name in by_name if name.startswith(FRAME_PREFIX)} == frame_names,
        "G5A_PARTS_ADDITIONAL_ORIGINAL_FRAME_VETO")
    roles = documentary.get("Source_RAW_roles")
    require(type(roles) is dict and set(roles) == set(SOURCE_ROLES), "G5A_PARTS_ALL_SIX_ACTUAL_SOURCE_ROLES_REQUIRED")
    for role, name in SOURCE_ROLES.items():
        row = by_name.get(name); reference = roles[role]
        require(type(row) is dict and type(reference) is dict
            and set(reference) == {"relative_source", "capture_file", "bytes", "sha256"}
            and reference == {key: row[key] for key in reference}, "G5A_PARTS_SOURCE_ROLE_REFERENCE_REBOUND")
    primary_names = set(SOURCE_ROLES.values())
    primary = [{**by_name[name], "role": role,
        "member": role + (".json.gz" if name.endswith(".gz") else ".json")}
        for role, name in sorted(SOURCE_ROLES.items())]
    runtime = [{**by_name[name], "member": by_name[name]["capture_file"]}
        for name in sorted(set(by_name) - frame_names - primary_names)]
    parts = []
    for category, members in (("frames", groups), ("runtime", runtime)):
        current = None
        for member in members:
            if current is None or len(current["members"]) == MAX_MEMBERS or current["bytes"] + member["bytes"] > MAX_PART:
                require(len(parts) < MAX_PARTS, "G5A_PARTS_FAMILY160_PART_BOUND_RED")
                index = len(parts)
                current = {"category": category, "part_index": index,
                    "directory": ("parts/" if category == "frames" else "runtime-parts/") + "part-" + str(index).zfill(3),
                    "bytes": 0, "members": []}
                parts.append(current)
            current["members"].append(member); current["bytes"] += member["bytes"]
    return {"schema": "rc6.g5a-declared-source-parts-plan.v2", "parts": parts, "primary": primary,
        "raw_bytes": total, "source_members": len(files), "actual_original_cuts": 1202,
        "native_authority_granted": False, "G5_GREEN": False, "qualification_claimed": False}


def require_publication_capacity(observation, *, bytes_count, additional_inodes):
    """Charge known controls now; future unlink or compression grants no credit."""
    require(type(observation) is dict and all(type(observation.get(key)) is int for key in
        ("fragment_bytes", "available_bytes", "free_inodes", "total_inodes"))
        and 0 < observation["fragment_bytes"] <= 65536 and observation["total_inodes"] > 0
        and type(bytes_count) is int and bytes_count >= 0 and type(additional_inodes) is int and additional_inodes >= 0,
        "G5A_PARTS_ACTUAL_KNOWN_PUBLICATION_ENVELOPE_REQUIRED")
    rounding = (bytes_count + observation["fragment_bytes"] - 1) // observation["fragment_bytes"] * observation["fragment_bytes"]
    require(observation["available_bytes"] >= rounding + RESERVE,
        "G5A_PARTS_ACTUAL_FREE4GIB_RESERVE_RED")
    require((observation["free_inodes"] - additional_inodes) * 10 >= observation["total_inodes"],
        "G5A_PARTS_ACTUAL_INODE10_PERCENT_RESERVE_RED")


def _leaf_info(info, *, device, expected_bytes):
    require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
        and info.st_nlink == 1 and info.st_dev == device
        and (info.st_uid, info.st_gid) == (os.geteuid(), os.getegid())
        and info.st_size == expected_bytes and 0 <= info.st_size <= MAX_LEAF,
        "G5A_PARTS_OWN_SAME_FS_SINGLELINK600_LEAF_REQUIRED")


def _hash_fd(descriptor, count):
    total = 0; digest = hashlib.sha256()
    while total < count:
        block = os.read(descriptor, min(CHUNK, count - total))
        require(bool(block), "G5A_PARTS_SOURCE_LEAF_TRUNCATED")
        total += len(block); digest.update(block)
    require(os.read(descriptor, 1) == b"", "G5A_PARTS_SOURCE_LEAF_TRAILING_BYTES")
    return digest.hexdigest()


def _rename_no_replace(source_directory, source_name, target_directory, target_name):
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renameat2", None)
    require(rename is not None, "G5A_PARTS_NATIVE_RENAME_NOREPLACE_NOT_SUPPORTED")
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(source_directory, os.fsencode(source_name), target_directory, os.fsencode(target_name), 1) != 0:
        reason = ctypes.get_errno()
        raise OSError(reason, "G5A_PARTS_NATIVE_RENAME_NOREPLACE_FAILED")


def _move_owned_leaf(source, destination, *, expected_bytes, expected_sha256):
    """Own NONROOT IO primitive, independently insufficient to claim native FIN."""
    source, destination = _literal_path(source), _literal_path(destination)
    source_parent, target_parent = _owned_directory(source.parent), _owned_directory(destination.parent)
    require(source_parent.st_dev == target_parent.st_dev and not os.path.lexists(destination)
        and type(expected_bytes) is int and 0 <= expected_bytes <= MAX_LEAF
        and type(expected_sha256) is str and SHA.fullmatch(expected_sha256),
        "G5A_PARTS_FRESH_SAME_FS_BOUND_TARGET_REQUIRED")
    source_directory = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    target_directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    descriptor = None
    try:
        require(identity(os.fstat(source_directory)) == identity(source_parent)
            and identity(os.fstat(target_directory)) == identity(target_parent),
            "G5A_PARTS_PARENT_DIRECTORY_REBOUND")
        descriptor = os.open(source.name, os.O_RDONLY | os.O_NOATIME | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=source_directory)
        before_info = os.fstat(descriptor); _leaf_info(before_info, device=source_parent.st_dev, expected_bytes=expected_bytes)
        before = identity(before_info)
        require(before == identity(os.stat(source.name, dir_fd=source_directory, follow_symlinks=False)),
            "G5A_PARTS_PINNED_SOURCE_INODE_REBOUND")
        require(_hash_fd(descriptor, expected_bytes) == expected_sha256, "G5A_PARTS_ORIGINAL_SOURCE_SHA_REBOUND")
        require(identity(os.fstat(descriptor)) == before
            == identity(os.stat(source.name, dir_fd=source_directory, follow_symlinks=False)),
            "G5A_PARTS_ORIGINAL_ALL11_CHANGED_DURING_HASH")
        # Kernel RENAME_NOREPLACE prevents overwriting a raced foreign target.
        # No hardlink, copy, truncate, chmod or time restoration.
        require(not os.path.lexists(destination), "G5A_PARTS_EXISTING_DESTINATION_VETO")
        _rename_no_replace(source_directory, source.name, target_directory, destination.name)
        after = identity(os.fstat(descriptor))
        require(all(before[key] == after[key] for key in UNCHANGED_ON_RENAME)
            and after["st_ctime_ns"] >= before["st_ctime_ns"]
            and after == identity(os.stat(destination.name, dir_fd=target_directory, follow_symlinks=False))
            and not os.path.lexists(source), "G5A_PARTS_ACTUAL_RENAME_ALL11_REBOUND")
        os.fsync(source_directory); os.fsync(target_directory)
        return {"identity_before": before, "identity_after": after,
            "ctime_changed_by_actual_rename": before["st_ctime_ns"] != after["st_ctime_ns"],
            "ctime_restored": False, "same_physical_inode_preserved": True,
            "additional_payload_copy_bytes": 0, "hardlinks_created": 0}
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(target_directory); os.close(source_directory)


def _read_leaf(path, *, expected_bytes, expected_sha256):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_CLOEXEC)
    try:
        before = identity(os.fstat(descriptor))
        _leaf_info(os.fstat(descriptor), device=path.parent.stat().st_dev, expected_bytes=expected_bytes)
        require(_hash_fd(descriptor, expected_bytes) == expected_sha256
            and before == identity(os.fstat(descriptor)) == identity(path.lstat()),
            "G5A_PARTS_OWN_CONTROL_SHA_OR_ALL11_REBOUND")
        return before
    finally:
        os.close(descriptor)


def _publish(root, name, document, *, budget):
    raw = calibration.wire(document)
    require(0 < len(raw) <= MAX_MANIFEST and budget + len(raw) <= MAX_PROOFS,
        "G5A_PARTS_ACTUAL_CONTROL16MIB_OR_TOTAL128MIB_RED")
    path = root / name
    require(not os.path.lexists(path), "G5A_PARTS_FRESH_CONTROL_PUBLICATION_REQUIRED")
    observation = calibration.filesystem(root)
    require_publication_capacity(observation, bytes_count=len(raw), additional_inodes=1)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(raw); stream.flush(); os.fsync(descriptor)
    finally:
        os.close(descriptor)
    digest = hashlib.sha256(raw).hexdigest()
    info = _read_leaf(path, expected_bytes=len(raw), expected_sha256=digest)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return {"path": name, "bytes": len(raw), "sha256": digest, "identity": info}, budget + len(raw)


class PackedSourceParts:
    __slots__ = ()

    def __new__(cls, *args, **kwargs):
        raise ValueError("G5A_PARTS_AUTHENTIC_POST_FIN_CAPTURE_FACTORY_REQUIRED")

    @property
    def path(self):
        return _package_state(self)["root"]

    @property
    def documentary(self):
        return validate_package(self)

    @property
    def frame_family(self):
        state = _package_state(self)
        validate_package(self)
        return clone(state["family"])

    @property
    def frames_root(self):
        return self.path / "parts"

    @property
    def runtimeparts_root(self):
        return self.path / "runtime-parts"

    @property
    def proofs_root(self):
        return self.path / "proofs"

    @property
    def primary_root(self):
        return self.path / "primary"

    @property
    def source_primary(self):
        return validate_package(self)["Source_RAW_roles"]

    def __reduce__(self):
        raise TypeError("G5A_PARTS_OPAQUE_SAME_PROCESS_PACKAGE_CANNOT_BE_SERIALIZED")


def _package_state(value):
    state = _ISSUED.get(id(value))
    require(type(value) is PackedSourceParts and state is not None and state["object"] is value
        and state["birth"] == _birth(), "G5A_PARTS_CURRENT_OPAQUE_ISSUED_PACKAGE_REQUIRED")
    from scripts import rc6_g5a_raw_capture as captured
    captured.validate_capture(state["capture"], require_complete_original_1202=True)
    return state


def validate_package(value):
    state = _package_state(value); root = state["root"]
    require(identity(_owned_directory(root)) == state["root_identity"], "G5A_PARTS_PACKAGED_ROOT_ALL11_REBOUND")
    expected = state["inventory"]
    observed = _tree_inventory(root)
    require(set(observed) <= set(expected), "G5A_PARTS_PACKAGED_GHOST_OR_ALIAS_VETO")
    require(observed == expected, "G5A_PARTS_PACKAGED_COMPLETE_ALL11_INVENTORY_REBOUND")
    return clone(state["documentary"])


def _tree_inventory(root):
    """NOATIME directory census preserves the eleven-field package evidence."""
    observed = {}; pending = [root]
    while pending:
        current = pending.pop()
        before = identity(_owned_directory(current))
        descriptor = os.open(current, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME | os.O_CLOEXEC)
        try:
            require(identity(os.fstat(descriptor)) == before, "G5A_PARTS_CENSUS_DIRECTORY_REBOUND")
            with os.scandir(descriptor) as entries:
                names = []
                for entry in entries:
                    require(len(names) < 101000, "G5A_PARTS_BOUNDED_PACKAGED_TREE_REQUIRED")
                    names.append(entry.name)
                names.sort()
            require(len(observed) + len(names) <= 101000, "G5A_PARTS_BOUNDED_PACKAGED_TREE_REQUIRED")
            for name in names:
                info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                path = current / name
                require(not stat.S_ISLNK(info.st_mode) and info.st_dev == root.stat().st_dev,
                    "G5A_PARTS_PACKAGED_GHOST_OR_ALIAS_VETO")
                observed[path.relative_to(root).as_posix()] = identity(info)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path)
            require(identity(os.fstat(descriptor)) == before == identity(current.lstat()),
                "G5A_PARTS_CENSUS_DIRECTORY_CHANGED")
        finally:
            os.close(descriptor)
    return observed


def package(capture):
    """Move issued copied RAW after BOTH FIN; never retire the original log."""
    from scripts import rc6_g5a_raw_capture as captured
    documentary = captured.validate_capture(capture, require_complete_original_1202=True)
    plan = plan_documentary(documentary)
    require(id(capture) not in _ATTEMPTS, "G5A_PARTS_ONE_SHOT_SOURCE_PACKAGE_REQUIRED")
    _ATTEMPTS[id(capture)] = capture
    root = _literal_path(capture.path)
    require(str(root) == documentary.get("payload_root"), "G5A_PARTS_ISSUED_CAPTURE_PATH_REBOUND")
    root_info = _owned_directory(root)
    _owned_directory(root.parent)
    expected_flat = {row["capture_file"] for row in documentary["files"]} | {"manifest.json"}
    require({path.name for path in root.iterdir()} == expected_flat,
        "G5A_PARTS_EXACT_FLAT_CAPTURE_INVENTORY_REQUIRED")
    for row in documentary["files"]:
        _leaf_info((root / row["capture_file"]).lstat(), device=root_info.st_dev, expected_bytes=row["bytes"])
    manifest_raw = calibration.wire(documentary)
    original_manifest = _read_leaf(root / "manifest.json", expected_bytes=len(manifest_raw),
        expected_sha256=hashlib.sha256(manifest_raw).hexdigest())
    measured = calibration.filesystem(root)
    directories_needed = len(plan["parts"]) + 4
    require_publication_capacity(measured, bytes_count=MAX_PROOFS + directories_needed * measured["fragment_bytes"],
        additional_inodes=directories_needed + len(plan["parts"]) + 3)
    for name in ("parts", "runtime-parts", "proofs", "primary"):
        (root / name).mkdir(mode=0o700)
    part_records, frame_parts, roles = [], [], {}
    whole_metadata = hashlib.sha256(); whole_metadata.update(b"[")
    moved_count = 0; physical_bytes = 0
    budget = 0
    primary_proof_rows = []
    for row in plan["primary"]:
        transition = _move_owned_leaf(root / row["capture_file"], root / "primary" / row["member"],
            expected_bytes=row["bytes"], expected_sha256=row["sha256"])
        member = {"relative_source": row["relative_source"], "path": "primary/" + row["member"],
            "original_capture_file": row["capture_file"], "bytes": row["bytes"], "sha256": row["sha256"],
            "original_stream_offset": row["original_stream_offset"], **transition}
        primary_proof_rows.append(member)
        roles[row["role"]] = {key: member[key] for key in ("relative_source", "path", "bytes", "sha256", "original_stream_offset")}
        if moved_count:
            whole_metadata.update(b",")
        whole_metadata.update(calibration.wire(member)[:-1]); moved_count += 1
        physical_bytes += transition["identity_after"]["st_blocks"] * 512
    require(physical_bytes <= 96 * MIB, "G5A_PARTS_SIX_PRIMARY_ROLES96MIB_ACTUAL_BOUND_RED")
    primary_proof = {"schema": PROOF_SCHEMA, "source_sha": documentary["source_sha"],
        "source_tree": documentary["source_tree"], "binding": documentary["binding"], "category": "primary",
        "directory": "primary", "directory_identity": identity((root / "primary").lstat()),
        "raw_bytes": sum(row["bytes"] for row in plan["primary"]),
        "members": primary_proof_rows, "G5_GREEN": False, "qualification_claimed": False,
        "additional_payload_copy_bytes": 0, "future_cleanup_credit_bytes": 0}
    primary_ref, budget = _publish(root, "proofs/primary.json", primary_proof, budget=budget)
    for part in plan["parts"]:
        part_root = root / part["directory"]; part_root.mkdir(mode=0o700)
        proof_rows = []; family_members = []; part_physical_bytes = 0
        for row in part["members"]:
            source = root / row["capture_file"]; target = part_root / row["member"]
            transition = _move_owned_leaf(source, target, expected_bytes=row["bytes"], expected_sha256=row["sha256"])
            target_relative = target.relative_to(root).as_posix()
            member = {"relative_source": row["relative_source"], "path": target_relative,
                "original_capture_file": row["capture_file"], "bytes": row["bytes"], "sha256": row["sha256"],
                "original_stream_offset": row["original_stream_offset"], **transition}
            proof_rows.append(member)
            if moved_count:
                whole_metadata.update(b",")
            whole_metadata.update(calibration.wire(member)[:-1]); moved_count += 1
            physical_bytes += transition["identity_after"]["st_blocks"] * 512
            part_physical_bytes += transition["identity_after"]["st_blocks"] * 512
            require(physical_bytes <= MAX_RAW, "G5A_PARTS_ACTUAL_PHYSICAL20GIB_RAW_BOUND_RED")
            require(part_physical_bytes <= MAX_PART, "G5A_PARTS_ACTUAL_PHYSICAL128MIB_PART_BOUND_RED")
            if part["category"] == "frames":
                family_members.append({"cut_index": row["cut_index"], "path": row["member"],
                    "bytes": row["bytes"], "sha256": row["sha256"]})
            for role, source_name in SOURCE_ROLES.items():
                if row["relative_source"] == source_name:
                    roles[role] = {key: member[key] for key in ("relative_source", "path", "bytes", "sha256", "original_stream_offset")}
        proof = {"schema": PROOF_SCHEMA, "source_sha": documentary["source_sha"],
            "source_tree": documentary["source_tree"], "binding": documentary["binding"],
            "part_index": part["part_index"], "category": part["category"], "directory": part["directory"],
            "directory_identity": identity(part_root.lstat()),
            "raw_bytes": part["bytes"], "members": proof_rows, "G5_GREEN": False, "qualification_claimed": False,
            "additional_payload_copy_bytes": 0, "future_cleanup_credit_bytes": 0}
        proof_name = "proofs/part-" + str(part["part_index"]).zfill(3) + ".json"
        proof_ref, budget = _publish(root, proof_name, proof, budget=budget)
        part_records.append({"part_index": part["part_index"], "category": part["category"],
            "directory": part["directory"], "raw_bytes": part["bytes"], "members": len(part["members"]),
            "directory_identity": proof["directory_identity"],
            "proof": proof_ref, "exact_member_metadata_sha256": calibration.digest(calibration.wire(proof_rows))})
        if part["category"] == "frames":
            frame_parts.append({"part_index": part["part_index"], "directory": part["directory"],
                "raw_bytes": part["bytes"], "members": family_members,
                "original_offsets_and_all11_proof": proof_ref})
    require(set(roles) == set(SOURCE_ROLES), "G5A_PARTS_MOVED_ALL_SIX_SOURCE_ROLES_REQUIRED")
    family = {"schema": FAMILY_SCHEMA, "source_sha": documentary["source_sha"], "source_tree": documentary["source_tree"],
        "binding": documentary["binding"], "writer_codec_restorer_binding_sha256": documentary["writer_codec_restorer_binding_sha256"],
        "actual_original_cuts": 1202, "parts": frame_parts,
        "authenticated_actions_parts_schema_required": "rc6.g5b-authenticated-original-frame-parts.v2",
        "artifact_origin_authenticated": False, "artifact_ids_invented": False,
        "G5_GREEN": False, "qualification_claimed": False, "cleanup_authority_granted": False}
    family_ref, budget = _publish(root, "g5b-frame-parts.preupload.json", family, budget=budget)
    whole_metadata.update(b"]\n")
    result = {"schema": PACKAGE_SCHEMA, "source_sha": documentary["source_sha"], "source_tree": documentary["source_tree"],
        "binding": documentary["binding"], "payload_root": str(root), "actual_original_cuts": 1202,
        "raw_bytes": plan["raw_bytes"], "source_members": plan["source_members"], "parts": part_records,
        "Source_RAW_roles": roles, "primary_proof": primary_ref, "frame_family": family_ref,
        "original_capture_manifest": {"path": "manifest.json", "bytes": len(manifest_raw),
            "sha256": hashlib.sha256(manifest_raw).hexdigest(), "identity": original_manifest},
        "whole_renamed_member_metadata_sha256": whole_metadata.hexdigest(),
        "actual_physical_raw_bytes": physical_bytes,
        "bounds": {"part_bytes": MAX_PART, "part_members": MAX_MEMBERS, "parts": MAX_PARTS,
            "raw_bytes": MAX_RAW, "leaf_bytes": MAX_LEAF, "free_reserve_bytes": RESERVE, "free_inode_fraction": "1/10"},
        "actual_pre_package_capacity": measured, "actual_controls_bytes_before_manifest": budget,
        "packing_method": "SAME_FILESYSTEM_OWN_SINGLELINK_RENAME", "additional_payload_copy_bytes": 0,
        "future_cleanup_credit_bytes": 0, "original_native_stream_retired": False,
        "original_namespace_retired": False, "artifact_ids_invented": False,
        "G5_GREEN": False, "qualification_claimed": False, "cleanup_authority_granted": False, "real_orders_sent": 0}
    package_ref, budget = _publish(root, "g5a-source-parts-manifest.json", result, budget=budget)
    result = {**result, "package_manifest": package_ref, "actual_controls_bytes": budget}
    inventory = _tree_inventory(root)
    value = object.__new__(PackedSourceParts)
    _ISSUED[id(value)] = {"object": value, "birth": _birth(), "capture": capture,
        "root": root, "root_identity": identity(root.lstat()), "inventory": inventory,
        "documentary": result, "family": family}
    validate_package(value)
    return value


def artifact_name(category, *, source_sha, run_id, part_index):
    require(category in ("frames", "runtime") and type(source_sha) is str
        and re.fullmatch(r"[0-9a-f]{40}", source_sha) and type(run_id) is int and run_id > 0
        and type(part_index) is int and 0 <= part_index < MAX_PARTS,
        "G5A_PARTS_SOURCE_FIXED_ARTIFACT_NAME_REQUIRED")
    return "rc6-g5a-" + category + "-" + source_sha + "-" + str(run_id) + "-1-" + str(part_index).zfill(3)


def _read_document(path, *, reference=None, expected_sha256=None):
    """Own documentary read; read-atime changes never reconstruct native proof."""
    path = _literal_path(path); _owned_directory(path.parent)
    info = path.lstat(); _leaf_info(info, device=path.parent.stat().st_dev, expected_bytes=info.st_size)
    require(0 < info.st_size <= MAX_MANIFEST, "G5A_PARTS_DOCUMENTARY16MIB_BOUND_REQUIRED")
    before = identity(info)
    if reference is not None:
        require(type(reference) is dict and reference.get("bytes") == info.st_size
            and type(reference.get("identity")) is dict and set(reference["identity"]) == set(IDENTITY_FIELDS)
            and all(before[key] == reference["identity"][key] for key in IDENTITY_FIELDS if key != "st_atime_ns")
            and before["st_atime_ns"] >= reference["identity"]["st_atime_ns"],
            "G5A_PARTS_DOCUMENTARY_PINNED10_FIELDS_OR_RECORDED_ATIME_REBOUND")
        expected_sha256 = reference.get("sha256")
    require(type(expected_sha256) is str and SHA.fullmatch(expected_sha256), "G5A_PARTS_DOCUMENTARY_SOURCE_POINTER_SHA_REQUIRED")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_CLOEXEC)
    try:
        require(identity(os.fstat(descriptor)) == before, "G5A_PARTS_DOCUMENTARY_OPEN_INODE_REBOUND")
        raw = bytearray()
        while len(raw) < info.st_size:
            block = os.read(descriptor, min(CHUNK, info.st_size - len(raw)))
            require(bool(block), "G5A_PARTS_DOCUMENTARY_TRUNCATED")
            raw.extend(block)
        require(os.read(descriptor, 1) == b"" and hashlib.sha256(raw).hexdigest() == expected_sha256
            and identity(os.fstat(descriptor)) == before == identity(path.lstat()),
            "G5A_PARTS_DOCUMENTARY_EXACT_SHA_AND_LIVE_ALL11_REBOUND")
    finally:
        os.close(descriptor)
    document = calibration.decode(raw)
    require(calibration.wire(document) == raw, "G5A_PARTS_DOCUMENTARY_CANONICAL_SOURCE_BYTES_REQUIRED")
    return document, before


def verify_documentary_package(manifest_path, *, expected_manifest_sha256):
    """Late-step byte verification only; no RawCapture factory or native claim."""
    path = _literal_path(manifest_path)
    require(path.name == "g5a-source-parts-manifest.json", "G5A_PARTS_FIXED_DOCUMENTARY_MANIFEST_REQUIRED")
    manifest, manifest_identity = _read_document(path, expected_sha256=expected_manifest_sha256)
    root = path.parent
    require(type(manifest) is dict and manifest.get("schema") == PACKAGE_SCHEMA and manifest.get("payload_root") == str(root)
        and manifest.get("actual_original_cuts") == 1202 and type(manifest.get("actual_original_cuts")) is int
        and manifest.get("packing_method") == "SAME_FILESYSTEM_OWN_SINGLELINK_RENAME"
        and manifest.get("additional_payload_copy_bytes") == manifest.get("future_cleanup_credit_bytes") == 0
        and all(manifest.get(key) is False for key in ("G5_GREEN", "qualification_claimed", "cleanup_authority_granted",
            "artifact_ids_invented", "original_native_stream_retired", "original_namespace_retired"))
        and type(manifest.get("parts")) is list and 0 < len(manifest["parts"]) <= MAX_PARTS,
        "G5A_PARTS_DOCUMENTARY_ONLY_COMPLETE_SOURCE_PACKAGE_REQUIRED")
    family, _ = _read_document(root / "g5b-frame-parts.preupload.json", reference=manifest["frame_family"])
    require(type(family) is dict and family.get("schema") == FAMILY_SCHEMA
        and all(family.get(key) == manifest.get(key) for key in ("source_sha", "source_tree", "binding", "actual_original_cuts"))
        and type(family.get("parts")) is list and family.get("artifact_origin_authenticated") is False
        and family.get("G5_GREEN") is False and family.get("qualification_claimed") is False,
        "G5A_PARTS_DOCUMENTARY_FRAME_FAMILY_REBOUND")
    proof_refs = [manifest["primary_proof"]] + [part["proof"] for part in manifest["parts"]]
    total = physical = count = 0; whole = hashlib.sha256(); whole.update(b"[")
    actual_read_observations, by_source, by_part = [], {}, {}
    for position, reference in enumerate(proof_refs):
        expected_path = "proofs/primary.json" if position == 0 else "proofs/part-" + str(position - 1).zfill(3) + ".json"
        require(reference.get("path") == expected_path, "G5A_PARTS_DOCUMENTARY_FIXED_PROOF_PATH_REQUIRED")
        proof, _ = _read_document(root / expected_path, reference=reference)
        require(type(proof) is dict and proof.get("schema") == PROOF_SCHEMA
            and all(proof.get(key) == manifest.get(key) for key in ("source_sha", "source_tree", "binding"))
            and proof.get("G5_GREEN") is False and proof.get("qualification_claimed") is False
            and proof.get("additional_payload_copy_bytes") == proof.get("future_cleanup_credit_bytes") == 0
            and type(proof.get("members")) is list and 0 < len(proof["members"]) <= MAX_MEMBERS,
            "G5A_PARTS_DOCUMENTARY_SOURCE_RENAME_PROOF_REQUIRED")
        if position == 0:
            require(proof.get("category") == "primary" and proof.get("directory") == "primary" and len(proof["members"]) == 6,
                "G5A_PARTS_DOCUMENTARY_SIX_PRIMARY_ROLES_REQUIRED")
        else:
            part = manifest["parts"][position - 1]
            require(part.get("part_index") == position - 1 and type(part.get("part_index")) is int
                and part.get("category") in ("frames", "runtime")
                and part.get("directory") == ("parts/" if part["category"] == "frames" else "runtime-parts/") + "part-" + str(position - 1).zfill(3)
                and all(part.get(key) == proof.get(key) for key in ("part_index", "category", "directory", "raw_bytes"))
                and part.get("members") == len(proof["members"])
                and part.get("exact_member_metadata_sha256") == calibration.digest(calibration.wire(proof["members"])),
                "G5A_PARTS_DOCUMENTARY_PART_BOUND_SOURCE_ORDER_REBOUND")
            by_part[position - 1] = proof
        directory = root / proof["directory"]
        directory_actual = identity(_owned_directory(directory)); directory_before = proof.get("directory_identity")
        require(type(directory_before) is dict and set(directory_before) == set(IDENTITY_FIELDS)
            and all(directory_actual[key] == directory_before[key] for key in IDENTITY_FIELDS if key != "st_atime_ns")
            and directory_actual["st_atime_ns"] >= directory_before["st_atime_ns"],
            "G5A_PARTS_DOCUMENTARY_OWN_PART_DIRECTORY10_FIELDS_REBOUND")
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME | os.O_CLOEXEC)
        try:
            with os.scandir(descriptor) as leaves:
                actual_names = {leaf.name for leaf in leaves}
            require(actual_names == {PurePosixPath(member["path"]).name for member in proof["members"]}
                and directory_actual == identity(os.fstat(descriptor)) == identity(directory.lstat()),
                "G5A_PARTS_DOCUMENTARY_UNEXPECTED_UPLOADED_LEAF_OR_DIRECTORY_REBOUND")
        finally:
            os.close(descriptor)
        part_total = part_physical = 0
        for member in proof["members"]:
            _relative_source(member.get("relative_source"))
            relative = member.get("path")
            require(type(relative) is str and PurePosixPath(relative).as_posix() == relative
                and str(PurePosixPath(relative).parent) == proof["directory"]
                and PurePosixPath(relative).name not in ("", ".", "..")
                and member["relative_source"] not in by_source and type(member.get("bytes")) is int
                and 0 <= member["bytes"] <= MAX_LEAF and type(member.get("sha256")) is str and SHA.fullmatch(member["sha256"])
                and type(member.get("original_stream_offset")) is int and member["original_stream_offset"] > 0,
                "G5A_PARTS_DOCUMENTARY_ORIGINAL_MEMBER_OFFSET_HASH_REBOUND")
            expected = member.get("identity_after")
            require(type(expected) is dict and set(expected) == set(IDENTITY_FIELDS), "G5A_PARTS_DOCUMENTARY_ALL11_RENAME_IDENTITY_REQUIRED")
            current = identity((root / relative).lstat())
            require(all(current[key] == expected[key] for key in IDENTITY_FIELDS if key != "st_atime_ns")
                and current["st_atime_ns"] >= expected["st_atime_ns"],
                "G5A_PARTS_DOCUMENTARY_RENAME10_FIELDS_OR_ACTUAL_READ_ATIME_REBOUND")
            _read_leaf(root / relative, expected_bytes=member["bytes"], expected_sha256=member["sha256"])
            require(identity((root / relative).lstat()) == current, "G5A_PARTS_DOCUMENTARY_LIVE_ALL11_CHANGED")
            actual_read_observations.append({"path": relative, "identity_after_rename": expected,
                "identity_at_late_source_hash": current, "allowedReadAtimeRecorded": current["st_atime_ns"] != expected["st_atime_ns"],
                "other10_fields_unchanged": True, "atime_restored": False})
            if count: whole.update(b",")
            whole.update(calibration.wire(member)[:-1]); count += 1
            by_source[member["relative_source"]] = member
            total += member["bytes"]; physical += current["st_blocks"] * 512
            part_total += member["bytes"]; part_physical += current["st_blocks"] * 512
        require(part_total == proof.get("raw_bytes") and part_total <= (96 * MIB if position == 0 else MAX_PART)
            and part_physical <= (96 * MIB if position == 0 else MAX_PART),
            "G5A_PARTS_DOCUMENTARY_ACTUAL_PART128MIB_BOUND_RED")
    whole.update(b"]\n")
    require(total == manifest.get("raw_bytes") <= MAX_RAW and physical == manifest.get("actual_physical_raw_bytes") <= MAX_RAW
        and count == manifest.get("source_members") and whole.hexdigest() == manifest.get("whole_renamed_member_metadata_sha256"),
        "G5A_PARTS_DOCUMENTARY_WHOLE_ORIGINAL_METADATA_SHA_REBOUND")
    require(type(manifest.get("Source_RAW_roles")) is dict and set(manifest["Source_RAW_roles"]) == set(SOURCE_ROLES),
        "G5A_PARTS_DOCUMENTARY_EXACT_PRIMARY_SOURCE_ROLES_REQUIRED")
    for role, source in SOURCE_ROLES.items():
        member = by_source.get(source); reference = manifest["Source_RAW_roles"][role]
        require(type(member) is dict and reference == {key: member[key] for key in
            ("relative_source", "path", "bytes", "sha256", "original_stream_offset")},
            "G5A_PARTS_DOCUMENTARY_PRIMARY_SOURCE_REFERENCE_REBOUND")
    observed_frames = []
    for entry in family["parts"]:
        proof = by_part.get(entry.get("part_index"))
        require(type(proof) is dict and proof["category"] == "frames" and entry.get("directory") == proof["directory"]
            and entry.get("raw_bytes") == proof["raw_bytes"] and type(entry.get("members")) is list,
            "G5A_PARTS_DOCUMENTARY_FRAME_PART_REFERENCE_REBOUND")
        expected_members = []
        for member in proof["members"]:
            cut = len(observed_frames)
            expected_members.append({"cut_index": cut, "path": "cut-" + str(cut).zfill(4) + ".json.gz",
                "bytes": member["bytes"], "sha256": member["sha256"]})
            require(member["relative_source"] == FRAME_PREFIX + expected_members[-1]["path"]
                and PurePosixPath(member["path"]).name == expected_members[-1]["path"],
                "G5A_PARTS_DOCUMENTARY_EXACT_ORIGINAL_FRAME_SEQUENCE_REQUIRED")
            observed_frames.append(member)
        require(entry["members"] == expected_members, "G5A_PARTS_DOCUMENTARY_EXACT_FRAME_LEAF_REFERENCE_REBOUND")
    require(len(observed_frames) == 1202, "G5A_PARTS_DOCUMENTARY_FULL1202_FRAME_COVERAGE_REQUIRED")
    return {"manifest": manifest, "family": family, "root": root, "manifest_identity": manifest_identity,
        "read_observations": actual_read_observations, "by_part": by_part,
        "native_authority_granted": False, "G5_GREEN": False, "qualification_claimed": False}


def seal_documentary_uploaded_parts(manifest_path, *, expected_manifest_sha256, get, run_id, run_attempt=1):
    """Join real current-run upload metadata; G5B authenticates completed origin later."""
    from scripts import rc6_g5a_actions as actions
    from scripts import rc6_architectural_gates as gates
    actions.issuer()
    require(type(run_id) is int and run_id > 0 and str(run_id) == os.environ.get("GITHUB_RUN_ID")
        and type(run_attempt) is int and run_attempt == 1 and os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
        and os.environ.get("GITHUB_WORKFLOW_REF") == gates.REPOSITORY + "/" + gates.WORKFLOW + "@" + os.environ.get("GITHUB_REF", "")
        and type(getattr(get, "__self__", None)) is actions.ParentAPI and get.__func__ is actions.ParentAPI.request
        and get.__self__._closed is False, "G5A_PARTS_CURRENT_SOURCE_READONLY_API_REQUIRED")
    checked = verify_documentary_package(manifest_path, expected_manifest_sha256=expected_manifest_sha256)
    manifest, family = checked["manifest"], checked["family"]
    require(os.environ.get("GITHUB_SHA") == manifest["source_sha"], "G5A_PARTS_CURRENT_WORKFLOW_SOURCE_SHA_REQUIRED")
    run = get("/actions/runs/" + str(run_id)); attempt = get("/actions/runs/" + str(run_id) + "/attempts/1")
    for row in (run, attempt):
        require(type(row) is dict and row.get("id") == run_id and row.get("run_attempt") == 1
            and row.get("head_sha") == manifest["source_sha"] and row.get("status") == "in_progress"
            and row.get("conclusion") is None and row.get("path") == gates.WORKFLOW and row.get("event") == "workflow_dispatch"
            and row.get("repository", {}).get("id") == row.get("head_repository", {}).get("id") == gates.REPOSITORY_ID
            and row.get("repository", {}).get("full_name") == gates.REPOSITORY
            and row.get("head_branch") == os.environ.get("GITHUB_REF_NAME")
            and str(row.get("actor", {}).get("id")) == os.environ.get("GITHUB_ACTOR_ID")
            and row.get("actor", {}).get("login") == os.environ.get("GITHUB_ACTOR"),
            "G5A_PARTS_CURRENT_IN_PROGRESS_EXACT_SOURCE_RUN_REQUIRED")
    jobs = get("/actions/runs/" + str(run_id) + "/attempts/1/jobs?per_page=100")
    require(type(jobs) is dict and type(jobs.get("jobs")) is list
        and jobs.get("total_count") == len(jobs["jobs"]) <= 100,
        "G5A_PARTS_ACTUAL_JOBS_PAGINATION_REQUIRED")
    active = [job for job in jobs["jobs"] if job.get("status") == "in_progress"]
    require(len(active) == 1 and active[0].get("run_id") == run_id and active[0].get("labels") == ["ubuntu-24.04"]
        and active[0].get("runner_group_name") == "GitHub Actions" and type(active[0].get("runner_name")) is str
        and active[0]["runner_name"].startswith("GitHub Actions "), "G5A_PARTS_ACTUAL_RUNNING_CANONICAL_JOB_REQUIRED")
    steps = active[0].get("steps")
    require(type(steps) is list, "G5A_PARTS_ACTUAL_OFFICIAL_UPLOAD_STEPS_REQUIRED")
    for category in {part["category"] for part in manifest["parts"]}:
        matches = [step for step in steps if step.get("name") == OFFICIAL_UPLOAD_STEPS[category]]
        require(len(matches) == 1 and matches[0].get("status") == "completed" and matches[0].get("conclusion") == "success"
            and gates.stamp(matches[0].get("started_at")) <= gates.stamp(matches[0].get("completed_at")),
            "G5A_PARTS_OFFICIAL_UPLOAD_NOT_ACTUALLY_COMPLETED")
    rows = []; total_count = None
    for page in (1, 2):
        collection = get("/actions/runs/" + str(run_id) + "/artifacts?per_page=100&page=" + str(page))
        require(type(collection) is dict and type(collection.get("total_count")) is int
            and 0 < collection["total_count"] <= MAX_PARTS + 16 and type(collection.get("artifacts")) is list
            and len(collection["artifacts"]) <= 100 and (total_count is None or total_count == collection["total_count"]),
            "G5A_PARTS_ACTUAL_ARTIFACT_PAGINATION_BOUND_REQUIRED")
        total_count = collection["total_count"]; rows.extend(collection["artifacts"])
        if len(rows) >= total_count: break
    require(len(rows) == total_count and len({row.get("id") for row in rows}) == len(rows),
        "G5A_PARTS_COMPLETE_ACTUAL_ARTIFACT_LIST_REQUIRED")
    uploaded_frames, uploaded_runtime, upload_total = [], [], 0
    for part in manifest["parts"]:
        expected_name = artifact_name(part["category"], source_sha=manifest["source_sha"], run_id=run_id, part_index=part["part_index"])
        matches = [row for row in rows if row.get("name") == expected_name]
        require(len(matches) == 1, "G5A_PARTS_ACTUAL_UNIQUE_PART_ARTIFACT_REQUIRED")
        artifact = get("/actions/artifacts/" + str(matches[0].get("id")))
        require(artifact == matches[0] and type(artifact.get("id")) is int and artifact["id"] > 0
            and artifact.get("expired") is False and type(artifact.get("digest")) is str
            and re.fullmatch(r"sha256:[0-9a-f]{64}", artifact["digest"])
            and type(artifact.get("size_in_bytes")) is int and 0 < artifact["size_in_bytes"] <= 256 * MIB
            and artifact.get("workflow_run", {}).get("id") == run_id
            and artifact["workflow_run"].get("head_sha") == manifest["source_sha"]
            and artifact["workflow_run"].get("repository_id") == artifact["workflow_run"].get("head_repository_id") == gates.REPOSITORY_ID
            and gates.stamp(attempt.get("run_started_at")) <= gates.stamp(artifact.get("created_at")) <= datetime.now(timezone.utc)
            and gates.stamp(artifact.get("expires_at")) > datetime.now(timezone.utc),
            "G5A_PARTS_ACTUAL_PART_DIGEST_SIZE_SOURCE_ORIGIN_REBOUND")
        upload_total += artifact["size_in_bytes"]
        require(upload_total <= MAX_RAW, "G5A_PARTS_ACTUAL_UPLOADED20GIB_ZIP_TOTAL_RED")
        common = {key: artifact[key] for key in ("name", "digest", "size_in_bytes")}
        common.update({"artifact_id": artifact["id"], "run_id": run_id, "run_attempt": 1})
        if part["category"] == "frames":
            entry = next(row for row in family["parts"] if row["part_index"] == part["part_index"])
            uploaded_frames.append({**common, "members": entry["members"]})
        else:
            proof = checked["by_part"][part["part_index"]]
            uploaded_runtime.append({**common, "members": [{"path": PurePosixPath(row["path"]).name,
                **{key: row[key] for key in ("relative_source", "bytes", "sha256", "original_stream_offset")}} for row in proof["members"]]})
    frame_manifest = {"schema": "rc6.g5b-authenticated-original-frame-parts.v2", "source_sha": manifest["source_sha"],
        "source_tree": manifest["source_tree"], "run_id": run_id, "run_attempt": 1,
        "writer_codec_restorer_binding_sha256": family["writer_codec_restorer_binding_sha256"],
        "actual_original_cuts": 1202, "parts": uploaded_frames, "G5_GREEN": False, "qualification_claimed": False}
    runtime_manifest = {"schema": "rc6.g5a-source-runtime-parts.v2", "source_sha": manifest["source_sha"],
        "source_tree": manifest["source_tree"], "run_id": run_id, "run_attempt": 1, "parts": uploaded_runtime,
        "Source_primary_roles": manifest["Source_RAW_roles"], "G5_GREEN": False, "qualification_claimed": False}
    return {"schema": "rc6.g5a-current-official-upload-documentary-seal.v2", "frame_manifest": frame_manifest,
        "runtime_manifest": runtime_manifest, "source_package_manifest_sha256": expected_manifest_sha256,
        "binding": manifest["binding"], "actual_uploaded_zip_bytes": upload_total,
        "allowedReadAtimeRecorded": checked["read_observations"], "original_native_stream_all11_relaxed": False,
        "origin_phase": "CURRENT_RUN_IN_PROGRESS_OFFICIAL_UPLOAD_STEPS_COMPLETED",
        "later_completed_successful_origin_authentication_required": True, "native_authority_granted": False,
        "cleanup_authority_granted": False, "G5_GREEN": False, "qualification_claimed": False, "artifact_ids_invented": False}


def seal_uploaded_parts(package, **kwargs):
    document = validate_package(package)
    return seal_documentary_uploaded_parts(package.path / "g5a-source-parts-manifest.json",
        expected_manifest_sha256=document["package_manifest"]["sha256"], **kwargs)
