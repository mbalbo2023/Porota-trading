"""Versioned complete G5B captured-evidence verifier; no launch or writer authority.

The caller must first authenticate Actions ZIP/run/attempt/Source bytes. A
versioned receipt requires owner acceptance AND all the native evidence below.
G5A acquisition output by itself cannot become a qualified G5 receipt.
"""
from __future__ import annotations

import math
import errno
import base64
import hashlib
import os
import re
import stat
import time
import zipfile
from pathlib import Path

from rc6_shadow_runtime import archive_components as components
from rc6_shadow_runtime.archive_format_contract import SOURCE_MEMBERS, writer_codec_restorer_binding
from scripts import rc6_g5_contract as contract


SCHEMA = "rc6.g5b-complete-original-qualification.v2"
REQUIRED_ROLES = frozenset({
    "Horizon", "native_Horizon", "g5b_complete", "g5a_quota", "g5a_ledger",
    "g5b_original_comparison", "g5b_writer_binding", "g5b_reader_review",
    "g5b_recovery_collection", "g5b_recovery_execution", "g5b_recovery_junit",
    "g5b_original_frames_manifest", "g5b_frame_parts",
    "g5b_private_ack_review", "g5b_reader_collection_kernel", "g5b_reader_execution_kernel",
    "g5a_root_custody",
})
PARTS_SCHEMA = "rc6.g5b-authenticated-original-frame-parts.v2"
MAX_CAPTURE_BYTES = 20 * 1024**3
_ARCHIVES = {}
_PARTS = {}
_QUALIFIED = {}
_COMPLETED_REPLAYS = {}
_ISSUER = object()


def require(value, signature):
    if not value:
        raise ValueError(signature)


def capture_document(raw, *, maximum=128 * 1024**2):
    """Decode bounded authentic RAW; gzip is storage only, never provenance."""
    require(type(raw) is bytes and 0 < len(raw) <= 16 * 1024**2, "G5B_CAPTURED_RAW_MEMBER_BOUND")
    if raw[:2] == b"\x1f\x8b":
        raw = components.inflate(raw, maximum=maximum)
    require(len(raw) <= maximum, "G5B_CAPTURED_EXPANDED_RAW_BOUND")
    return components.loads(raw)


def build_capture_envelope(captured, *, source_sha, source_tree, owner_revision_decision=None):
    """Join actual RAW roles after FIN; this document grants no qualification.

    The outer Source actor authenticates each Actions/run/attempt/Source/member
    first. The verifier independently replays all bytes, admission and custody;
    a caller-supplied envelope or hash dictionary cannot authorize a gate.
    """
    roles = REQUIRED_ROLES - {"g5b_complete"}
    require(type(captured) is dict and set(captured) == roles
        and all(type(value) is bytes and 0 < len(value) <= 16 * 1024**2 for value in captured.values()),
        "G5B_COMPLETE_ACTUAL_BOUNDED_RAW_ROLES_REQUIRED_FOR_ENVELOPE")
    require(type(source_sha) is str and type(source_tree) is str
        and re.fullmatch(r"[0-9a-f]{40}", source_sha) and re.fullmatch(r"[0-9a-f]{40}", source_tree),
        "G5B_EXACT_SOURCE_REQUIRED_FOR_CAPTURE_ENVELOPE")
    result = {"schema": SCHEMA, "source_sha": source_sha, "source_tree": source_tree,
        "contract_sha256": contract.CONTRACT_SHA256, "original_cuts": 1202,
        "archive_limit_bytes": contract.CAP_BYTES, "maximum_dependency_depth": 32,
        "contracted_retention_seconds": 32400, "recovery_margin_seconds": 3600,
        "captured_roles_sha256": {role: components.sha(captured[role]) for role in sorted(roles)},
        "writer_codec_restorer_binding_sha256": capture_document(captured["g5b_writer_binding"])["binding_sha256"],
        "candidate_native_writer": "CANDIDATE_V2_NATIVE_COMPONENT_V4",
        "exact_artifact_join_required_at_G8": True, "native_v4_write_enabled": False,
        "qualification_claimed": False, "real_orders_sent": 0}
    if owner_revision_decision is not None:
        require(type(owner_revision_decision) is dict and set(owner_revision_decision) == {
            "approval_url", "approval_body_sha256", "paired_comment_id", "paired_body_sha256", "accepted_utc"},
            "G5B_ORIGINAL_IMMUTABLE_DECISION_POINTER_REQUIRED")
        quota = capture_document(captured["g5a_quota"])
        require(owner_revision_decision["accepted_utc"] == quota.get("owner_decision_lifecycle", {}).get("accepted_utc"),
            "G5B_NATIVE_ACCEPTANCE_UTC_POINTER_REBOUND")
        result["owner_revision_decision"] = dict(owner_revision_decision)
    return result


def _file_identity(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) == 0o600, "G5B_OWN_SINGLELINK_CAPTURE_REQUIRED")
    return tuple(getattr(info, key) for key in ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink",
        "st_size", "st_blocks", "st_mtime_ns", "st_ctime_ns"))


def _owned_capture_root(root):
    root = Path(root).absolute()
    require(not os.path.lexists(root) and not any(path.is_symlink() for path in root.parents)
        and root.parent.lstat().st_uid == os.getuid(), "G5B_FRESH_OWN_CAPTURE_ROOT_REQUIRED")
    root.mkdir(mode=0o700)
    return root


def _download_authentic(reference, *, source_sha, root, get, download, expected_name=None):
    from scripts import rc6_architectural_gates as gates
    artifact = get("/actions/artifacts/" + str(reference.get("artifact_id")))
    run = get("/actions/runs/" + str(reference.get("run_id")))
    attempt = get("/actions/runs/" + str(reference.get("run_id")) + "/attempts/1")
    gates.artifact_origin(reference, artifact, run, attempt, source_sha=source_sha)
    gates.actions_runner_origin(get("/actions/runs/" + str(reference["run_id"]) + "/attempts/1/jobs?per_page=100"),
        run_id=reference["run_id"])
    require(expected_name is None or artifact.get("name") == expected_name, "G5B_ACTUAL_PART_ARTIFACT_NAME_REBOUND")
    unit = os.statvfs(root)
    upper = 6 * (((artifact["size_in_bytes"] + 4095) // 4096 + 37) * 4096) + 128 * 1024**2
    require(unit.f_frsize == 4096 and unit.f_bavail * unit.f_frsize >= upper + 4 * 1024**3
        and unit.f_favail - 1 >= unit.f_files / 10, "G5B_CAPTURE_LIVE_FREE_RESERVE_RED")
    path = root / (str(reference["artifact_id"]) + ".zip")
    require(not os.path.lexists(path), "G5B_ACTUAL_ARCHIVE_CAPTURE_MUST_BE_NEW")
    download(reference["artifact_id"], path, expected_size=artifact["size_in_bytes"],
        expected_digest=artifact["digest"], deadline=time.monotonic() + 300)
    downloaded = path.lstat()
    require(stat.S_ISREG(downloaded.st_mode) and downloaded.st_nlink == 1
        and downloaded.st_uid == os.getuid() and not path.is_symlink(),
        "G5B_OWN_DOWNLOADED_REGULAR_CAPTURE_REQUIRED")
    # The existing transport creates with the process umask. Narrow only this
    # new file inside the fresh0700 own directory; never alter input artifacts.
    os.chmod(path, 0o600)
    identity = _file_identity(path)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    require(path.stat().st_size == artifact["size_in_bytes"] and "sha256:" + digest == artifact["digest"],
        "G5B_AUTHENTICATED_ACTIONS_ZIP_BYTES_REBOUND")
    gates.archive_members(path)
    return path, identity, digest, run, attempt, artifact


class AuthenticatedG5Archive:
    """Actual Source/Actions ZIP; not native or owner authority by itself."""
    def __init__(self, issuer, *, archive, identity, digest, row, reference, run, attempt):
        require(issuer is _ISSUER, "G5B_ACTIONS_ARCHIVE_FACTORY_REQUIRED")
        self.archive = archive
        state = {"archive": str(archive), "identity": identity, "archive_sha256": digest,
            "row": row, "reference": reference, "run": run, "attempt": attempt}
        _ARCHIVES[id(self)] = (self, os.getpid(), components.canonical(state))

    def capture(self, role):
        from scripts import rc6_architectural_gates as gates
        state = registered_archive(self)
        reference = state["row"].get("native_evidence", {}).get(role)
        require(type(reference) is dict and type(reference.get("path")) is str
            and re.fullmatch(r"[0-9a-f]{64}", reference.get("sha256", "")), "G5B_ACTUAL_NATIVE_RAW_ROLE_REQUIRED")
        raw = gates.capture_member(self.archive, reference["path"])
        require(components.sha(raw) == reference["sha256"], "G5B_AUTHENTICATED_NATIVE_RAW_ROLE_REBOUND")
        return raw


def registered_archive(context):
    require(type(context) is AuthenticatedG5Archive and id(context) in _ARCHIVES,
        "G5B_AUTHENTICATED_ACTIONS_CAPTURE_REQUIRED")
    original, pid, wire = _ARCHIVES[id(context)]
    state = components.loads(wire)
    require(original is context and pid == os.getpid() and str(context.archive) == state["archive"]
        and list(_file_identity(context.archive)) == state["identity"], "G5B_CURRENT_ACTIONS_CAPTURE_REBOUND")
    return state


class VerifiedG5Source:
    """Same-process qualification, never a deserializable release capability."""
    def __init__(self, issuer, *, archive_context, report, historical_certification):
        require(issuer is _ISSUER, "G5B_SOURCE_QUALIFICATION_FACTORY_REQUIRED")
        self.archive_context = archive_context
        self.historical_certification = historical_certification
        self._wire = components.canonical(report)
        _QUALIFIED[id(self)] = (self, os.getpid(), self._wire, archive_context, historical_certification)

    def public_report(self):
        return components.loads(self._wire)


def qualify_verified_source(archive_context, report, *, historical_certification):
    from scripts.rc6_g5_historical_certification import validate_historical_certification
    state = registered_archive(archive_context)
    validate_historical_certification(historical_certification, row=state["row"], archive_context=archive_context)
    completed = _COMPLETED_REPLAYS.get(id(archive_context))
    require(completed is not None and completed[0] is archive_context and completed[1] == os.getpid()
        and all(report.get(key) == value for key, value in components.loads(completed[2]).items()),
        'G5B_SOURCE_FACTORY_REQUIRES_ACTUAL_COMPLETE_REGISTERED_REPLAY')
    require(type(report) is dict and report.get("status") ==
        "G5B_CANDIDATE_V2_NATIVE_V4_SOURCE_QUALIFIED_G7_G8_ARTIFACT_JOIN_REQUIRED"
        and report.get("all1202_original_frame_replay", {}).get("actual_original_frames_replayed") == 1202
        and report.get("all1202_original_frame_replay", {}).get("restored_original_bytes_by_writer", {}).get("V4") == 1202,
        "G5B_FULL_ORIGINAL_SOURCE_QUALIFICATION_REQUIRED")
    return VerifiedG5Source(_ISSUER, archive_context=archive_context, report=report,
        historical_certification=historical_certification)


def validate_verified_source(context, *, source_sha, source_tree):
    require(type(context) is VerifiedG5Source and id(context) in _QUALIFIED,
        "G5B_AUTHENTICATED_FULL_SOURCE_QUALIFICATION_CONTEXT_REQUIRED")
    original, pid, wire, capture, certification = _QUALIFIED[id(context)]
    state = registered_archive(capture)
    require(original is context and pid == os.getpid() and context._wire == wire
        and context.archive_context is capture and context.historical_certification is certification
        and state["row"]["source_sha"] == source_sha and state["row"]["source_tree"] == source_tree,
        "G5B_QUALIFIED_SOURCE_OR_CAPTURE_REBOUND")
    from scripts.rc6_g5_historical_certification import validate_historical_certification
    validate_historical_certification(certification, row=state["row"], archive_context=capture)
    return components.loads(wire)


def authenticate_g5_archive(reference, *, source_sha, source_tree, evidence_root, get, download):
    from scripts import rc6_architectural_gates as gates
    require(type(reference) is dict and type(reference.get("receipt_member")) is str
        and re.fullmatch(r"[0-9a-f]{64}", reference.get("receipt_sha256", "")), "G5B_EXACT_SOURCE_RECEIPT_REFERENCE_REQUIRED")
    root = _owned_capture_root(evidence_root)
    archive, identity, digest, run, attempt, artifact = _download_authentic(reference,
        source_sha=source_sha, root=root, get=get, download=download)
    raw = gates.capture_member(archive, reference["receipt_member"], maximum=gates.MAX_GATE_RECEIPT_BYTES)
    require(components.sha(raw) == reference["receipt_sha256"], "G5B_ACTUAL_RECEIPT_BYTES_REBOUND")
    row = components.loads(raw)
    require(type(row) is dict and row.get("gate") == "G5" and row.get("contract_revision") == contract.REVISION
        and row.get("source_sha") == source_sha and row.get("source_tree") == source_tree
        and row.get("run_id") == reference["run_id"] and row.get("run_attempt") == 1
        and row.get("status") in ("GREEN", "EVIDENCE_ACQUIRED_NOT_QUALIFIED"),
        "G5B_ACQUISITION_SOURCE_RUN_RECEIPT_REQUIRED")
    return AuthenticatedG5Archive(_ISSUER, archive=archive, identity=identity, digest=digest,
        row=row, reference=reference, run=run, attempt=attempt)


def validate_parts_manifest(manifest, *, source_sha, source_tree, run_id, run_attempt, writer_binding_sha256):
    require(type(manifest) is dict and manifest.get("schema") == PARTS_SCHEMA
        and manifest.get("source_sha") == source_sha and manifest.get("source_tree") == source_tree
        and type(manifest.get("run_id")) is int and manifest.get("run_id") == run_id
        and type(manifest.get("run_attempt")) is int and manifest.get("run_attempt") == run_attempt == 1
        and manifest.get("writer_codec_restorer_binding_sha256") == writer_binding_sha256
        and manifest.get("actual_original_cuts") == 1202 and type(manifest.get("parts")) is list
        and 0 < len(manifest["parts"]) <= 1202, "G5B_ALL1202_SOURCE_PARTS_MANIFEST_REQUIRED")
    total, names, ids, frames, last = 0, set(), set(), {}, -1
    for part in manifest["parts"]:
        require(type(part) is dict and type(part.get("artifact_id")) is int and part["artifact_id"] > 0
            and part["artifact_id"] not in ids and type(part.get("run_id")) is int and part.get("run_id") == run_id
            and type(part.get("run_attempt")) is int and part.get("run_attempt") == 1
            and type(part.get("name")) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", part["name"])
            and part["name"] not in names and type(part.get("size_in_bytes")) is int
            and 0 < part["size_in_bytes"] <= 256 * 1024**2
            and type(part.get('digest')) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", part['digest'])
            and type(part.get("members")) is list and 0 < len(part["members"]) <= 4096,
            "G5B_PART_EXACT_BOUNDED_ACTIONS_REFERENCE_REQUIRED")
        total += part["size_in_bytes"]; names.add(part["name"]); ids.add(part["artifact_id"])
        require(total <= MAX_CAPTURE_BYTES, "G5B_ACTUAL_PARTS_20GIB_ZIP_COST_RED")
        for member in part["members"]:
            require(type(member) is dict and set(member) == {"cut_index", "path", "bytes", "sha256"}
                and type(member["cut_index"]) is int and member["cut_index"] == last + 1 and member["cut_index"] < 1202
                and member["path"] == "cut-" + str(member["cut_index"]).zfill(4) + ".json.gz"
                and type(member["bytes"]) is int and 0 < member["bytes"] <= 16 * 1024**2
                and type(member['sha256']) is str and re.fullmatch(r"[0-9a-f]{64}", member["sha256"]),
                "G5B_CANONICAL_ALL1202_PART_FRAME_MEMBER_REQUIRED")
            last = member["cut_index"]; frames[last] = (part, member)
    require(last == 1201 and len(frames) == 1202
        and sum(member["bytes"] for _, member in frames.values()) <= MAX_CAPTURE_BYTES,
        "G5B_ALL1202_PART_FRAME_COVERAGE_OR_PHYSICAL_COST_RED")
    return frames


class AuthenticatedFrameParts:
    """Sequential genuine Actions supplier, one bounded ZIP on disk at a time."""
    def __init__(self, issuer, *, manifest, frames, root, get, download):
        require(issuer is _ISSUER, "G5B_AUTHENTICATED_PARTS_FACTORY_REQUIRED")
        self.root, self.manifest, self.frames = root, manifest, frames
        self.get, self.download, self.next = get, download, 0
        self.active = None
        _PARTS[id(self)] = (self, os.getpid(), str(root), components.canonical(manifest), get, download)

    def __call__(self, reference):
        from scripts import rc6_architectural_gates as gates
        require(_PARTS.get(id(self)) == (self, os.getpid(), str(self.root), components.canonical(self.manifest), self.get, self.download)
            and type(reference) is dict and reference.get("cut_index") == self.next,
            "G5B_ACTUAL_PART_SUPPLIER_SOURCE_SEQUENCE_REBOUND")
        part, member = self.frames[self.next]
        require(all(reference.get(key) == member[key] for key in member), "G5B_ACTUAL_PART_FRAME_REFERENCE_REBOUND")
        if self.active is None or self.active[0] != part["artifact_id"]:
            self.close_active()
            ref = {"artifact_id": part["artifact_id"], "artifact_digest": part["digest"],
                "run_id": part["run_id"], "run_attempt": 1}
            archive, identity, digest, _, _, actual = _download_authentic(ref, source_sha=self.manifest["source_sha"],
                root=self.root, get=self.get, download=self.download, expected_name=part["name"])
            require(actual["size_in_bytes"] == part["size_in_bytes"], "G5B_ACTUAL_PART_ZIP_SIZE_REBOUND")
            inventory = gates.archive_members(archive)
            require(set(inventory) == {entry["path"] for entry in part["members"]}
                and all(inventory[entry["path"]].file_size == entry["bytes"] for entry in part["members"]),
                "G5B_ACTUAL_PART_ZIP_MEMBER_INVENTORY_REBOUND")
            self.active = (part["artifact_id"], archive, identity, digest)
        _, archive, identity, _ = self.active
        require(_file_identity(archive) == identity, "G5B_LIVE_PART_ZIP_CUSTODY_REBOUND")
        raw = gates.capture_member(archive, member["path"])
        require(len(raw) == member["bytes"] and components.sha(raw) == member["sha256"],
            "G5B_AUTHENTICATED_ACTUAL_FRAME_LEAF_BYTES_REBOUND")
        self.next += 1
        return raw

    def close_active(self):
        if self.active is not None:
            _, path, identity, _ = self.active
            require(_file_identity(path) == identity, "G5B_ONLY_OWN_AUTHENTICATED_PART_REMOVAL_REQUIRED")
            path.unlink(); self.active = None


def authenticate_frame_parts(manifest, *, archive_context, writer_binding_sha256, evidence_root, get, download):
    from scripts import rc6_architectural_gates as gates
    state = registered_archive(archive_context); row = state["row"]
    frames = validate_parts_manifest(manifest, source_sha=row["source_sha"], source_tree=row["source_tree"],
        run_id=row["run_id"], run_attempt=row["run_attempt"], writer_binding_sha256=writer_binding_sha256)
    for part in manifest["parts"]:
        artifact = get("/actions/artifacts/" + str(part["artifact_id"]))
        reference = {"artifact_id": part["artifact_id"], "artifact_digest": part["digest"], "run_id": part["run_id"], "run_attempt": 1}
        gates.artifact_origin(reference, artifact, state["run"], state["attempt"], source_sha=row["source_sha"])
        require(artifact.get("name") == part["name"] and artifact.get("size_in_bytes") == part["size_in_bytes"],
            "G5B_ACTUAL_PART_METADATA_REBOUND")
    return AuthenticatedFrameParts(_ISSUER, manifest=manifest, frames=frames,
        root=_owned_capture_root(evidence_root), get=get, download=download)


def verify_original_frame_stream(manifest, *, capture_frame, comparison_result, writer_binding,
                                 source_sha, source_tree, scratch_root):
    """Replay all actual input and writer bytes from authenticated ZIP parts.

    The caller authenticates every part's Actions/run/attempt/Source and member
    hashes before supplying capture_frame. This function streams one bounded
    frame at a time, retains immutable output on its own scratch filesystem and
    actually restores the five original bytes with the Source-bound reader.
    """
    from rc6_shadow_runtime.retention import EvidenceRetention
    require(callable(capture_frame) and type(manifest) is dict
            and manifest.get("schema") == "rc6.g5a-all1202-original-byte-frames.v2"
            and manifest.get("source_sha") == source_sha and manifest.get("source_tree") == source_tree
            and manifest.get("writer_codec_restorer_binding_sha256") == writer_binding["binding_sha256"]
            and manifest.get("actual_original_cuts") == 1202 and type(manifest.get("frames")) is list
            and len(manifest["frames"]) == 1202 and comparison_result.get("original_lossless_frames") == manifest
            and manifest.get("selected_future_writer") == writer_binding.get("effective_product_writer_choice")
                == "CANDIDATE_V2_NATIVE_COMPONENT_V4"
            and manifest.get("native_entrypoint") == writer_binding.get("effective_native_writer") == "ComponentArchive.build",
            "G5B_ALL1202_AUTHENTIC_ORIGINAL_FRAME_STREAM_REQUIRED")
    scratch = Path(scratch_root).absolute()
    require(not os.path.lexists(scratch) and not any(path.is_symlink() for path in scratch.parents)
            and scratch.parent.lstat().st_uid == os.getuid(), "G5B_FRESH_OWN_STREAM_REPLAY_SCRATCH_REQUIRED")
    available = os.statvfs(scratch.parent)
    require(available.f_bavail * available.f_frsize >= 512 * 1024**2 + 4 * 1024**3
            and available.f_favail >= available.f_files / 10 + 32768,
            "G5B_STREAM_REPLAY_ORIGINAL_FREE_RESERVE_REQUIRED")
    scratch.mkdir(mode=0o700)
    owners = {name: EvidenceRetention(scratch / name / "live", archive_root=scratch / name / "archive",
        archive_maximum_bytes=512 * 1024**2, archive_maximum_files=32768, archive_format="COMPONENT_V3")
        for name in ("V3", "V4")}
    for owner in owners.values():
        owner.root.mkdir(mode=0o700, parents=True); owner.archive_root.mkdir(mode=0o700, parents=True)
    seen, counts = set(), {"V3": 0, "V4": 0}
    scratch_allocated = sum(path.lstat().st_blocks * 512 for path in (scratch, *scratch.rglob('*')))
    for index, (reference, cut) in enumerate(zip(manifest["frames"], comparison_result["cuts"])):
        require(type(reference) is dict and reference.get("cut_index") == index
                and reference.get("path") == "cut-" + str(index).zfill(4) + ".json.gz"
                and type(reference.get("bytes")) is int and 0 < reference["bytes"] <= 16 * 1024**2,
                "G5B_RAW_FRAME_EXACT_SEQUENCE_AND_BOUND_REQUIRED")
        raw = capture_frame(reference)
        require(type(raw) is bytes and len(raw) == reference["bytes"] and components.sha(raw) == reference["sha256"],
                "G5B_ACTUAL_ORIGINAL_FRAME_BYTES_REBOUND")
        reservation, after = reference.get("original_workload_before_publication"), reference.get("original_workload_after_publication")
        require(type(reservation) is dict and type(after) is dict and reservation.get("known_encoded_bytes") == len(raw)
                and reservation.get("future_gc_credit_bytes") == 0 and reservation.get("additional_inodes") == 1,
                "G5B_ORIGINAL_RAW_FRAME_ACTUAL20GIB_PUBLICATION_RESERVATION_REQUIRED")
        before, upper = reservation.get("current_allocation", {}), reservation.get("simultaneous_physical_upper_bytes")
        require(type(upper) is int and upper == 6 * (((len(raw) + 4095) // 4096 + 37) * 4096) + 128 * 1024**2
                and all(value.get("hard_bytes") == 20 * 1024**3 and value.get("hard_inodes") == 100000
                    and value.get("archive_and_probe_subprojects_included_physically") is True
                    and value.get("future_gc_credit_bytes") == 0 for value in (before, after))
                and before["physical_allocated_bytes"] + upper <= before["hard_bytes"]
                and before["free_bytes"] >= upper + 4 * 1024**3
                and before["free_inodes"] - 1 >= before["total_inodes"] / 10
                and before["physical_allocated_bytes"] + ((len(raw) + 4095) // 4096 * 4096)
                    <= after["physical_allocated_bytes"] <= before["physical_allocated_bytes"] + upper,
                "G5B_ORIGINAL_RAW_FRAME_PHYSICAL_COST_OR_RESERVES_REBOUND")
        frame = capture_document(raw)
        require(frame.get("schema") == "rc6.g5a-original-five-writer-output-frame.v2"
                and frame.get("source_sha") == source_sha and frame.get("source_tree") == source_tree
                and frame.get("cut_index") == index and frame.get("as_of") == cut["as_of"]
                and frame.get("writer_codec_restorer_binding_sha256") == writer_binding["binding_sha256"]
                and type(frame.get("originals")) is dict and set(frame["originals"]) == components.MEMBERS,
                "G5B_ACTUAL_RAW_FRAME_SOURCE_OR_ORIGINAL_ROLES_REBOUND")
        originals = {}
        for name, details in frame["originals"].items():
            try: value = base64.b64decode(details["raw_base64"], validate=True)
            except (ValueError, TypeError, KeyError) as error: raise ValueError("G5B_ORIGINAL_FRAME_BYTES_INVALID") from error
            require(len(value) == details["bytes"] == cut["member_bytes"][name]
                    and components.sha(value) == details["sha256"] == cut["original_member_sha256"][name],
                    "G5B_ORIGINAL_FIVE_RAW_BYTES_HASH_OR_LENGTH_REBOUND")
            originals[name] = value
        require(type(frame.get("new_immutable_objects")) is list and len(frame["new_immutable_objects"]) <= 32768,
                "G5B_CAPTURED_ACTUAL_WRITER_OBJECTS_REQUIRED")
        for member in frame["new_immutable_objects"]:
            name, branch = member.get("path"), member.get("branch")
            from rc6_shadow_runtime.archive_namespace import is_archive_member
            require(branch in owners and type(name) is str and is_archive_member(name)
                    and (branch, name) not in seen, "G5B_RAW_WRITER_OBJECT_PATH_OR_DUPLICATE_REBOUND")
            try: value = base64.b64decode(member["raw_base64"], validate=True)
            except (ValueError, TypeError, KeyError) as error: raise ValueError("G5B_RAW_WRITER_OBJECT_ENCODING_INVALID") from error
            require(len(value) == member.get("bytes") and components.sha(value) == member.get("sha256"),
                    "G5B_RAW_WRITER_OBJECT_BYTES_REBOUND")
            path = owners[branch].archive_root / name
            available = os.statvfs(scratch)
            known_upper = 6 * (((len(value) + 4095) // 4096 + 37) * 4096) + 128 * 1024**2
            require(available.f_bavail * available.f_frsize >= known_upper + 4 * 1024**3
                and available.f_favail - 1 >= available.f_files / 10
                and scratch_allocated + known_upper <= MAX_CAPTURE_BYTES,
                "G5B_RAW_REPLAY_CURRENT_PHYSICAL_FREE_RESERVE_REQUIRED")
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            with os.fdopen(descriptor, "wb") as destination: destination.write(value)
            scratch_allocated += path.lstat().st_blocks * 512 + 4096
            seen.add((branch, name))
        for branch, output in frame.get("writer_outputs", {}).items():
            require(branch in owners and output.get("status") == cut["branches"][branch]["status"],
                    "G5B_RAW_EFFECTIVE_WRITER_BRANCH_STATUS_REBOUND")
            if output["status"] != "EXACT_ORIGINAL_BYTES_RESTORED": continue
            require(output.get("effective_entrypoint") == ("ComponentArchive.build" if branch == "V4"
                    else "ComponentArchive.publish_original_v3"),
                    "G5B_RAW_ACTUAL_PLANNER_WRITER_ENTRYPOINT_REBOUND")
            require(output.get("receipt") == cut["branches"][branch]["receipt"],
                    "G5B_RAW_ACTUAL_WRITER_NATIVE_RECEIPT_REBOUND")
            restored, _ = components.ComponentArchive(owners[branch]).restore(output["receipt"])
            require(restored == originals, "G5B_ALL1202_RAW_WRITER_OUTPUT_ORIGINAL_BYTES_RESTORE_FAILED")
            counts[branch] += 1
    require(counts["V4"] == 1202 and manifest.get("actual_immutable_objects") == len(seen),
            "G5B_ALL1202_EFFECTIVE_V4_WRITER_RAW_REPLAY_REQUIRED")
    return {"actual_original_frames_replayed": 1202, "restored_original_bytes_by_writer": counts,
        "effective_v4_entrypoint": "ComponentArchive.build",
        "candidate_native_default_writer": "V4", "future_artifact_choice_verified": False,
        "real_orders_sent": 0}


def verify_native_quota_capture(record, *, row):
    """Cross-bind real quota operations, Seal and 1-MiB EDQUOT RAW."""
    from scripts import rc6_native_namespace_filter as native_filter
    require(type(record) is dict and record.get("schema") == "rc6.g5a-native-physical-control.v2"
            and record.get("source_sha") == row["source_sha"] and record.get("source_tree") == row["source_tree"]
            and record.get("contract_sha256") == contract.CONTRACT_SHA256,
            "G5B_ACTUAL_QUOTA_CONTROL_EXACT_SOURCE_REQUIRED")
    readback = record.get("quota_initial_readback", {})
    require(type(readback) is dict, "G5B_NATIVE_QUOTA_READBACK_REQUIRED")
    operations = readback.get("native_operations", [])
    require(readback.get("hard_bytes") == contract.CAP_BYTES and readback.get("hard_inodes") == contract.CAP_INODES
            and type(readback.get("format_id")) is int and readback["format_id"] > 0
            and type(operations) is list and all(type(item) is dict for item in operations)
            and [item.get("operation") for item in operations] == ["Q_GETFMT", "Q_GETQUOTA"]
            and all(type(item.get("return")) is int and item["return"] == 0
                    and type(item.get("errno")) is int and item["errno"] == 0 for item in operations),
            "G5B_NATIVE_PROJECT_QUOTA_READBACK_NOT_512MIB_32768")
    enforcement = record.get("enforcement_proof", {})
    probe = record.get("probe_initial_readback", {})
    binding = record.get("binding")
    require(type(enforcement) is dict and type(probe) is dict and type(binding) is dict,
            "G5B_NATIVE_ENFORCEMENT_PROBE_AND_BINDING_REQUIRED")
    probe_operations = probe.get("native_operations", [])
    require(probe.get("hard_bytes") == 1024**2 and probe.get("hard_inodes") == contract.CAP_INODES
            and probe.get("format_id") == readback["format_id"]
            and type(probe_operations) is list
            and all(type(item) is dict for item in probe_operations)
            and [item.get("operation") for item in probe_operations] == ["Q_GETFMT", "Q_GETQUOTA"]
            and all(type(item.get("return")) is int and item["return"] == 0
                    and type(item.get("errno")) is int and item["errno"] == 0 for item in probe_operations),
            "G5B_SAME_FILESYSTEM_1MIB_PROBE_READBACK_REQUIRED")
    require(enforcement.get("schema") == "rc6.g5a-native-quota-enforcement.v2"
            and enforcement.get("quota_initial_readback_sha256") == components.sha(components.canonical(readback))
            and enforcement.get("probe_initial_readback_sha256") == components.sha(components.canonical(probe))
            and enforcement.get("native_edquot", {}).get("errno") == errno.EDQUOT
            and enforcement.get("project_mutation", {}).get("errno") == errno.EPERM
            and enforcement.get("quota_mutation") == {"return": -1, "errno": errno.EPERM}
            and type(enforcement.get("directory_device")) is int
            and enforcement["directory_device"] == enforcement.get("probe_device")
            and enforcement.get("binding") == record.get("binding")
            and binding.get("candidate_sha") == row["source_sha"]
            and binding.get("candidate_tree") == row["source_tree"]
            and enforcement.get("g5_qualification_allowed") is False
            and enforcement.get("native_v4_write_enabled") is False,
            "G5B_NATIVE_EDQUOT_OR_SEALED_MUTATION_PROOF_REBOUND")
    current = record.get("current_native_project")
    require(type(current) is dict and current.get("measurement_method") == "LIVE_SEALED_PROJINHERIT_STATVFS_NOT_A_SAMPLED_PEAK"
            and current.get("hard_bytes") == contract.CAP_BYTES and current.get("hard_inodes") == contract.CAP_INODES
            and current.get("native_initial_readback_sha256") == components.sha(components.canonical(readback))
            and all(type(current.get(key)) is int and current[key] > 0 for key in (
                "project_id", "directory_device", "directory_inode", "mount_id", "allocation_unit", "quota_format_id"))
            and all(current[key] == enforcement.get(key) for key in ("directory_device", "directory_inode", "mount_id"))
            and current["quota_format_id"] == readback["format_id"]
            and all(item.get("project_id") == current["project_id"] for item in operations)
            and all(item.get("project_id") == enforcement.get("probe_project_id") for item in probe_operations)
            and current["project_id"] != enforcement.get("probe_project_id"),
            "G5B_LIVE_QUOTA_PROJECTION_DEVICE_MOUNT_PROJECT_REBOUND")
    contract.reserve_state(current, state="admission", physical_costs={name: 0 for name in contract.RESERVATION_CLASSES},
                           additional_inodes=0)
    envelope = contract.validate_publication_envelope(record.get("source_publication_envelope"), snapshot=current,
        source_sha=row["source_sha"], source_tree=row["source_tree"])
    require(record.get("native_ext4_geometry") == envelope.get("native_ext4_geometry"),
            "G5B_ACTUAL_FILESYSTEM_GEOMETRY_AND_CLOSED_WRITE_BOUND_REBOUND")
    from scripts import rc6_private_pid_boundary as boundary
    descriptors = record.get("native_descriptor_composition", {})
    require(type(descriptors) is dict and descriptors.get("schema") == "rc6.g5a-native-own-quota-fd-composition.v2"
            and descriptors.get("binding") == binding and descriptors.get("privileged_bridge_retained") is False
            and type(descriptors.get("owned_readonly_directories")) is list
            and len(descriptors["owned_readonly_directories"]) == 2,
            "G5B_NATIVE_DESCRIPTOR_COMPOSITION_REQUIRED")
    base = boundary.validate_descriptors(descriptors.get("baseline_after_seal_before_own_open"))
    full, own = descriptors.get("current_descriptors"), descriptors["owned_readonly_directories"]
    require(type(full) is list and len(full) == 5 and full[:3] == base
            and {item.get("descriptor") for item in full} == {0, 1, 2, *[item.get("descriptor") for item in own]}
            and all(type(item) is dict and type(item.get("identity")) is list and len(item["identity"]) == 6
                    and item["identity"][2] > 0 and item["mount_id"] == current["mount_id"]
                    and item["identity"][0] == current["directory_device"]
                    and item["open_flags"] & 3 == 0 for item in own),
            "G5B_EXTRA_DESCRIPTOR_NOT_EXACT_NONROOT_OWN_READONLY_DIRECTORY")
    require({item["project_id"] for item in own} == {current["project_id"], enforcement["probe_project_id"]},
            "G5B_DESCRIPTOR_QUOTA_OR_PROBE_PROJECT_REBOUND")
    filter_binding = record.get("filter_binding")
    require(type(filter_binding) is dict and filter_binding.get("source_sha") == row["source_sha"]
            and filter_binding.get("source_tree") == row["source_tree"] and "plan_sha256" in filter_binding,
            "G5B_EXACT_FILTER_SOURCE_AND_PLAN_REQUIRED")
    native_filter.validate_evidence(record.get("sealed_filter"), source_binding=filter_binding)
    require(enforcement.get("seal_module_sha256") == record["sealed_filter"]["module_sha256"],
            "G5B_PROBE_AND_ACTIVE_SEAL_PROGRAM_REBOUND")
    return record


def verify_g5b_capture(row, captured, *, approval=None, source_root, frame_capture=None, scratch_root=None,
                       historical_certification=None, archive_context=None):
    """Return Source qualification only after complete authentic captured evidence.

    Future artifact completeness/byte matching is independently mandatory at
    G8. Requiring that later artifact before G5 would recreate the cycle. The
    exact writer/codec/restorer hashes here are the join obligation for G8.
    """
    from scripts import rc6_architectural_gates as gates
    from scripts import rc6_archive_reader_review as readers
    from scripts import rc6_cas_original_comparison as comparison
    from scripts.rc6_contract_revisions import validate_revision_approval
    if historical_certification is None:
        validate_revision_approval(approval, revision=contract.REVISION, scope=contract.SCOPE,
            source_sha=row["source_sha"], source_tree=row["source_tree"], contract_sha256=contract.CONTRACT_SHA256)
    else:
        from scripts.rc6_g5_historical_certification import validate_historical_certification
        validate_historical_certification(historical_certification, row=row, archive_context=archive_context)
        require(archive_context is not None and type(frame_capture) is AuthenticatedFrameParts,
            "G5B_FINISHED_CERTIFICATION_AUTHENTICATED_FRAME_SUPPLIER_REQUIRED")
        registered_archive(archive_context)
    require(row.get("gate") == "G5" and row.get("contract_revision") == contract.REVISION
            and REQUIRED_ROLES <= set(captured), "G5B_COMPLETE_NATIVE_RAW_ROLES_REQUIRED")
    document = capture_document(captured["g5b_complete"])
    require(type(document) is dict and document.get("schema") == SCHEMA and document.get("source_sha") == row["source_sha"]
            and document.get("source_tree") == row["source_tree"]
            and document.get("contract_sha256") == contract.CONTRACT_SHA256
            and document.get("original_cuts") == 1202 and document.get("archive_limit_bytes") == contract.CAP_BYTES
            and document.get("maximum_dependency_depth") == 32
            and document.get("contracted_retention_seconds") == 32400 and document.get("recovery_margin_seconds") == 3600
            and document.get("native_v4_write_enabled") is False
            and document.get("exact_artifact_join_required_at_G8") is True,
            "G5B_EXACT_VERSIONED_ORIGINAL_CONTRACT_REQUIRED")
    references = document.get("captured_roles_sha256")
    require(type(references) is dict and set(references) == REQUIRED_ROLES - {"g5b_complete"}
            and all(references[name] == components.sha(captured[name]) for name in references),
            "G5B_COMPLETE_NATIVE_RAW_DIGEST_JOIN_REBOUND")
    quota_control = verify_native_quota_capture(capture_document(captured["g5a_quota"]), row=row)
    root_replayer = getattr(gates, 'verify_g5a_captured_custody', None)
    require(callable(root_replayer) and archive_context is not None,
        'G5B_ACTUAL_ROOT_GUARDIAN_PRIVATE_PID_CUSTODY_REPLAY_REQUIRED')
    custody_replay = root_replayer(capture_document(captured['g5a_root_custody']), row=row,
        quota_control=quota_control, archive_context=archive_context, source_root=source_root)
    require(type(custody_replay) is dict and custody_replay.get('scope') == 'DOCUMENTARY_G5A_CUSTODY_REPLAY_ONLY'
        and custody_replay.get('source_sha') == row['source_sha'] and custody_replay.get('source_tree') == row['source_tree']
        and custody_replay.get('binding') == quota_control['binding']
        and custody_replay.get('actual_root_and_outer_FIN_replayed') is True
        and custody_replay.get('full_private_PID_adversarial_capture_replayed') is True
        and custody_replay.get('new_launch_authorization_granted') is False,
        'G5B_COMPLETE_NATIVE_ROOT_AND_PRIVATE_PID_REPLAY_REBOUND')
    native = components.loads(captured["native_Horizon"])
    original = capture_document(captured["g5b_original_comparison"])
    require(native.get("source_sha") == row["source_sha"] and native.get("source_tree") == row["source_tree"]
            and native.get("archive_format") == "COMPONENT_V4"
            and native.get("ticks_requested") == 1201 and native.get("native_ticks_executed") == 1202
            and native.get("archive_restore_count") == 1202 and native.get("restarts") == [361, 841]
            and native.get("catalog_count") == 1200 and native.get("input_observation_rows") == 6000
            and native.get("provider_requests") == 0 and "error" not in native
            and native.get("private_comparison_active") is True
            and all(native.get(name) is True for name in ("execution_complete", "source_database_unchanged",
                "code_source_unchanged", "import_graph_verified", "source_index_and_tar_unchanged"))
            and type(native.get("cuts")) is list and len(native["cuts"]) == 1202,
            "G5B_NATIVE_COMPLETE_ORIGINAL_SOURCE_RESTART_RESTORE_REQUIRED")
    fin = native.get("native_child_infrastructure", {})
    require(fin.get("source_sha") == row["source_sha"] and fin.get("source_tree") == row["source_tree"]
            and fin.get("native_finalizer_closed") is True
            and gates.finalization_closed(fin.get("child_infrastructure_finalization", {})),
            "G5B_ACTUAL_NATIVE_FIN_REQUIRED_BEFORE_PAYLOAD_CERTIFICATION")
    head = native.get("verified_final_archive_head", {})
    observation = native.get("native_retention_observations", {})
    require(head.get("contracted_horizon_seconds") == 32400 and head.get("recovery_margin_seconds") == 3600
            and native.get("first_restored_tick_index") == 1
            and native.get("first_original_public_restore_completed") is True
            and observation.get("additional_gc_rotation_or_pin_requests") == 0
            and all(type(observation.get(key)) is int and observation[key] > 0
                    for key in ("native_ack_compactions", "native_rotations")),
            "G5B_ORIGINAL_RETENTION_NATIVE_ACK_ROTATION_AND_FIRST_RESTORE_REQUIRED")
    source_root = Path(source_root).absolute()
    inventory = readers.source_inventory(source_root, source_sha=row["source_sha"], source_tree=row["source_tree"])
    require(quota_control["sealed_filter"]["module_sha256"] == components.sha(
        comparison._read(source_root / "scripts/rc6_native_namespace_filter.py", maximum=comparison.MAX_INDEX_BYTES)),
        "G5B_QUOTA_ACTIVE_FILTER_MODULE_EXACT_SOURCE_BYTES_REQUIRED")
    binding = writer_codec_restorer_binding({name: comparison._read(source_root / name, maximum=comparison.MAX_INDEX_BYTES)
                                            for name in SOURCE_MEMBERS})
    parts_manifest = capture_document(captured["g5b_frame_parts"])
    validate_parts_manifest(parts_manifest, source_sha=row["source_sha"], source_tree=row["source_tree"],
        run_id=row["run_id"], run_attempt=row["run_attempt"], writer_binding_sha256=binding["binding_sha256"])
    if historical_certification is not None:
        require(frame_capture.manifest == parts_manifest,
            "G5B_AUTHENTICATED_PART_SUPPLIER_CAPTURE_MANIFEST_REBOUND")
    require(capture_document(captured["g5b_writer_binding"]) == binding
            and document.get("writer_codec_restorer_binding_sha256") == binding["binding_sha256"],
            "G5B_ACTUAL_WRITER_CODEC_RESTORER_SOURCE_BYTES_REBOUND")
    require(quota_control["source_publication_envelope"]["writer_codec_restorer_binding"] == binding,
            "G5B_ACTUAL_QUOTA_ENVELOPE_CLOSED_WRITER_SOURCE_REBOUND")
    collection, execution = (components.loads(captured[name]) for name in ("g5b_recovery_collection", "g5b_recovery_execution"))
    require(all(type(value) is dict and value.get("source_sha") == row["source_sha"]
            and value.get("source_tree") == row["source_tree"] for value in (collection, execution)),
            "G5B_COMPLETE_RECOVERY_COLLECTION_EXECUTION_EXACT_SOURCE_REQUIRED")
    gates.validate_junit(collection, execution, captured["g5b_recovery_junit"])
    from scripts.rc6_controlled_native_child_manager import managed_phase_green
    for observed, role in ((collection, 'g5b_reader_collection_kernel'), (execution, 'g5b_reader_execution_kernel')):
        kernel = capture_document(captured[role])
        require(managed_phase_green(kernel)
            and kernel.get('process_group_absent_at_main_reap') is True
            and observed.get('execution_stage') == 'reader-review'
            and observed.get('literal_focal_files') == sorted(readers.CAS_REVIEW_MODULES)
            and observed.get('scope') == 'EXACT_ORIGINAL_CAS_REVIEW_COLLECTION_EXECUTION_NO_G1_OR_G6_CLAIM'
            and observed.get('source_namespace_exact_before_after') is True
            and observed.get('phase_validation_errors') == []
            and observed.get('inet_socket_attempts') == [] and observed.get('unexpected_product_imports') == []
            and gates.finalization_closed(observed.get('child_infrastructure_finalization')),
            'G5B_ORIGINAL_SOURCE_FOUR_READER_MODULES_AND_NATIVE_FIN_REQUIRED')
    readers.validate_review(components.loads(captured["g5b_reader_review"]), inventory, execution,
                            captured["g5b_recovery_execution"], source_sha=row["source_sha"], source_tree=row["source_tree"])
    comparison.validate_private_ack_review(capture_document(captured['g5b_private_ack_review']), execution,
        captured['g5b_recovery_execution'], source_sha=row['source_sha'], source_tree=row['source_tree'])
    names = comparison._executed_case_names(execution["items"], "tests/test_rc6_cas_original_comparison.py::")
    require(comparison.ACK_CASES <= names, "G5B_COMPLETE_ACTUAL_NATIVE_ACK_RECOVERY_CASES_REQUIRED")
    ledger = capture_document(captured["g5a_ledger"])
    require(ledger.get("schema") == "rc6.g5a-all1202-physical-ledger.v2"
            and ledger.get("source_sha") == row["source_sha"] and ledger.get("source_tree") == row["source_tree"]
            and ledger.get("contract_sha256") == contract.CONTRACT_SHA256
            and ledger.get("pending_original_cut") is None
            and ledger.get("all1202_completed") is True and ledger.get("actual_completed_cuts") == 1202,
            "G5B_ALL1202_ACTUAL_PHYSICAL_LEDGER_SOURCE_REQUIRED")
    ledger = contract.expand_ledger_capture(ledger)
    result = contract.validate_complete_original_stream(original, quota_trace=ledger.get("cuts"), writer_binding=binding)
    require(frame_capture is not None and scratch_root is not None,
            "G5B_ALL1202_ORIGINAL_FRAME_PARTS_AUTHENTICATED_STREAM_REQUIRED")
    frame_replay = verify_original_frame_stream(capture_document(captured["g5b_original_frames_manifest"]),
        capture_frame=frame_capture, comparison_result=original, writer_binding=binding,
        source_sha=row["source_sha"], source_tree=row["source_tree"], scratch_root=scratch_root)
    project = quota_control["current_native_project"]
    for cut_ledger in ledger["cuts"]:
        for reservation in cut_ledger["states"].values():
            measured = reservation["current_allocation"]
            require(all(measured.get(key) == project[key] for key in (
                "project_id", "directory_device", "directory_inode", "mount_id", "allocation_unit", "quota_format_id",
                "native_initial_readback_sha256", "measurement_method")),
                "G5B_ALL1202_NATIVE_STATE_QUOTA_PROJECT_OR_MOUNT_REBOUND")
    for actual, cut in zip(native["cuts"], original["cuts"]):
        require(type(actual) is dict, "G5B_ORIGINAL_NATIVE_CUT_REQUIRED")
        duration = actual.get("full_cycle_wall_seconds")
        branch = cut["branches"]["V4"]
        require(actual.get("tick_index") == cut["cut_index"] and actual.get("as_of") == cut["as_of"]
                and actual.get("original_member_sha256") == cut["original_member_sha256"]
                and actual.get("member_bytes") == cut["member_bytes"] and actual.get("native_receipt") == branch["receipt"]
                and actual.get("original_generation_custody_unchanged") is True
                and type(duration) in (int, float) and math.isfinite(duration) and 0 <= duration <= 30
                and (cut["cut_index"] not in (361, 841) or actual.get("checkpoint_reused") is True),
                "G5B_EXACT_NATIVE_SOURCE_ACK_ORIGINAL_CUT_OR_CADENCE_REBOUND")
    horizon = components.loads(captured["Horizon"])
    require(horizon.get("source_sha") == row["source_sha"] and horizon.get("source_tree") == row["source_tree"]
            and horizon.get("native_result_sha256") == components.sha(captured["native_Horizon"])
            and horizon.get("native_result_bytes") == len(captured["native_Horizon"])
            and horizon.get("native_finalizer5_green_before_payload_reads") is True
            and horizon.get("physical_custody_closed") is True and horizon.get("errors") == [],
            "G5B_ACTUAL_WRAPPER_CUSTODY_AND_RAW_DIGEST_REQUIRED")
    qualified = {**result, "status": "G5B_CANDIDATE_V2_NATIVE_V4_SOURCE_QUALIFIED_G7_G8_ARTIFACT_JOIN_REQUIRED",
            "all1202_original_frame_replay": frame_replay,
            "writer_codec_restorer_binding_sha256": binding["binding_sha256"],
            "writer_codec_restorer_binding": binding,
            "actual_root_guardian_private_pid_custody_replay": custody_replay,
            "native_source_unchanged": True, "native_fin_closed": True,
            "exact_artifact_join_required_at_G8": True, "native_v4_write_enabled": False}
    if historical_certification is not None:
        require(frame_capture.next == 1202, 'G5B_SOURCE_FACTORY_ACTUAL_FRAME_SUPPLIER_NOT_COMPLETE')
        _COMPLETED_REPLAYS[id(archive_context)] = (archive_context, os.getpid(), components.canonical(qualified))
    return qualified
