"""Declared plans and actual own NONROOT IO; no native FIN or ROOT fixture."""
import copy
import errno
import inspect
import os
from pathlib import Path
import pickle

import pytest

from scripts import rc6_capacity_calibration as calibration
from scripts import rc6_g5a_parts as parts
from scripts import rc6_g5a_raw_capture as captured


def declared_documentary(frame_bytes=1, runtime_count=6):
    """Pure metadata only; never a issued RawCapture or a native proof."""
    files, frames, roles = [], [], {}
    for cut in range(1202):
        name = "cut-" + str(cut).zfill(4) + ".json.gz"; flat = str(cut).zfill(6) + ".raw"
        files.append({"relative_source": parts.FRAME_PREFIX + name, "capture_file": flat,
            "bytes": frame_bytes, "sha256": "a" * 64, "original_stream_offset": 4096 + cut * 32})
        frames.append({"cut_index": cut, "path": name, "capture_file": flat, "bytes": frame_bytes, "sha256": "a" * 64})
    for index, (role, name) in enumerate(parts.SOURCE_ROLES.items()):
        row = {"relative_source": name, "capture_file": str(1202 + index).zfill(6) + ".raw",
            "bytes": 1, "sha256": "b" * 64, "original_stream_offset": 80000 + index * 32}
        files.append(row); roles[role] = {key: row[key] for key in row if key != "original_stream_offset"}
    for index in range(6, runtime_count):
        files.append({"relative_source": "project/runtime-" + str(index),
            "capture_file": str(1202 + index).zfill(6) + ".raw", "bytes": 1,
            "sha256": "c" * 64, "original_stream_offset": 90000 + index * 32})
    return {"schema": "rc6.g5a-post-fin-lossless-capture.v2", "source_sha": "a" * 40, "source_tree": "b" * 40,
        "binding": {"candidate_sha": "a" * 40}, "writer_codec_restorer_binding_sha256": "c" * 64,
        "actual_original_cuts": 1202, "complete_original_1202_frame_set_observed": True,
        "files": files, "original_frame_members": frames, "Source_RAW_roles": roles,
        "worker_report": {"report": {"status": "EVIDENCE_ACQUIRED_ONLY_NO_G5_GREEN"}},
        "G5_GREEN": False, "qualification_claimed": False, "artifact_ids_invented": False}


def test_declared_full1202_plan_preserves_offsets_hashes_original_sequence_and_six_roles():
    doc = declared_documentary(); before = copy.deepcopy(doc); plan = parts.plan_documentary(doc)
    assert doc == before and plan["native_authority_granted"] is False
    assert plan["G5_GREEN"] is False and plan["qualification_claimed"] is False
    assert plan["raw_bytes"] == 1208 and len(plan["parts"]) == 1
    frames = plan["parts"][0]
    assert frames["directory"] == "parts/part-000"
    assert [row["cut_index"] for row in frames["members"]] == list(range(1202))
    assert {row["relative_source"] for row in plan["primary"]} == set(parts.SOURCE_ROLES.values())
    for row in frames["members"]:
        assert all(row[key] == doc["files"][row["cut_index"]][key] for key in doc["files"][row["cut_index"]])
    assert "artifact_id" not in calibration.wire(plan).decode()


def test_declared_exact128mib_boundary_uses151_ordered_frame_parts():
    plan = parts.plan_documentary(declared_documentary(parts.MAX_LEAF))
    frames = [row for row in plan["parts"] if row["category"] == "frames"]
    assert len(frames) == 151 and len(plan["parts"]) == 151
    assert all(row["bytes"] == parts.MAX_PART and len(row["members"]) == 8 for row in frames[:-1])
    assert [row["cut_index"] for part in frames for row in part["members"]] == list(range(1202))


def test_declared_runtime4096_member_boundary_is_fixed():
    plan = parts.plan_documentary(declared_documentary(runtime_count=4103))
    assert [len(row["members"]) for row in plan["parts"] if row["category"] == "runtime"] == [4096, 1]


@pytest.mark.parametrize("field,value", [("actual_original_cuts", 1201), ("actual_original_cuts", True),
    ("complete_original_1202_frame_set_observed", False), ("complete_original_1202_frame_set_observed", 1),
    ("G5_GREEN", True), ("qualification_claimed", True), ("artifact_ids_invented", True)])
def test_declared_partial_or_green_claim_cannot_create_family(field, value):
    doc = declared_documentary(); doc[field] = value
    with pytest.raises(ValueError, match="LOGICAL_RED_OR_INCOMPLETE"): parts.plan_documentary(doc)


def test_full1202_original_logical_red_cannot_create_family():
    doc = declared_documentary(); doc["worker_report"]["report"]["status"] = "BLOQUEADO"
    with pytest.raises(ValueError, match="LOGICAL_RED_OR_INCOMPLETE"): parts.plan_documentary(doc)


@pytest.mark.parametrize("field,value", [("bytes", parts.MAX_LEAF + 1), ("bytes", -1), ("bytes", True),
    ("original_stream_offset", 0), ("original_stream_offset", True), ("sha256", "wrong"),
    ("capture_file", "../000001.raw"), ("capture_file", "/000001.raw"), ("capture_file", "000000.raw/extra"),
    ("relative_source", "../foreign"), ("relative_source", "project/../foreign"),
    ("relative_source", "project//alias"), ("relative_source", "project/./alias"),
    ("relative_source", "/foreign"), ("relative_source", "foreign/file")])
def test_declared_capture_metadata_attacks(field, value):
    doc = declared_documentary(); doc["files"][0][field] = value
    with pytest.raises(ValueError): parts.plan_documentary(doc)


@pytest.mark.parametrize("fault", ["duplicate_file", "duplicate_source", "extra_frame", "missing_frame",
    "frame_sequence", "frame_path", "frame_sha", "frame_file", "missing_role", "role_source", "role_sha"])
def test_declared_frame_and_role_rebinding_attacks(fault):
    doc = declared_documentary()
    if fault == "duplicate_file": doc["files"][1]["capture_file"] = doc["files"][0]["capture_file"]
    elif fault == "duplicate_source": doc["files"][1]["relative_source"] = doc["files"][0]["relative_source"]
    elif fault == "extra_frame": doc["files"].append({**doc["files"][0], "relative_source": parts.FRAME_PREFIX + "cut-1202.json.gz", "capture_file": "004321.raw"})
    elif fault == "missing_frame": doc["original_frame_members"].pop()
    elif fault == "frame_sequence": doc["original_frame_members"][1]["cut_index"] = 0
    elif fault == "frame_path": doc["original_frame_members"][0]["path"] = "../cut-0000.json.gz"
    elif fault == "frame_sha": doc["original_frame_members"][0]["sha256"] = "d" * 64
    elif fault == "frame_file": doc["original_frame_members"][0]["capture_file"] = "004321.raw"
    elif fault == "missing_role": doc["Source_RAW_roles"].pop("g5a_quota")
    elif fault == "role_source": doc["Source_RAW_roles"]["g5a_quota"]["relative_source"] = "project/foreign"
    elif fault == "role_sha": doc["Source_RAW_roles"]["g5a_quota"]["sha256"] = "e" * 64
    with pytest.raises(ValueError): parts.plan_documentary(doc)


def test_declared20gib_total_is_not_raised():
    doc = declared_documentary(parts.MAX_LEAF, 90)
    for row in doc["files"][1202:]: row["bytes"] = parts.MAX_LEAF
    with pytest.raises(ValueError, match="RAW20GIB_TOTAL"): parts.plan_documentary(doc)


def test_declared160_parts_rejects_fragmentation_without_smaller_original_frames():
    doc = declared_documentary(9 * parts.MIB, 600)
    for row in doc["files"][1202:]: row["bytes"] = parts.MAX_LEAF
    for row in doc["Source_RAW_roles"].values(): row["bytes"] = parts.MAX_LEAF
    assert sum(row["bytes"] for row in doc["files"]) < parts.MAX_RAW
    with pytest.raises(ValueError, match="FAMILY160_PART_BOUND"): parts.plan_documentary(doc)


def test_capacity_charges_actual_controls_before4gib_and10percent_reserves():
    observed = {"fragment_bytes": 4096, "available_bytes": parts.RESERVE + 4096, "free_inodes": 12, "total_inodes": 100}
    parts.require_publication_capacity(observed, bytes_count=1, additional_inodes=2)
    with pytest.raises(ValueError, match="FREE4GIB"):
        parts.require_publication_capacity({**observed, "available_bytes": parts.RESERVE + 4095}, bytes_count=1, additional_inodes=2)
    with pytest.raises(ValueError, match="INODE10_PERCENT"):
        parts.require_publication_capacity({**observed, "free_inodes": 11}, bytes_count=1, additional_inodes=2)


@pytest.mark.parametrize("field,value", [("fragment_bytes", 0), ("fragment_bytes", 65537),
    ("fragment_bytes", True), ("total_inodes", 0), ("available_bytes", "unmeasured")])
def test_capacity_requires_actual_measured_metadata(field, value):
    observed = {"fragment_bytes": 4096, "available_bytes": parts.RESERVE, "free_inodes": 100, "total_inodes": 100}
    with pytest.raises(ValueError, match="ACTUAL_KNOWN"):
        parts.require_publication_capacity({**observed, field: value}, bytes_count=0, additional_inodes=0)


@pytest.fixture
def own_leaf(tmp_path):
    source_root, target_root = tmp_path / "owned-source", tmp_path / "owned-target"
    source_root.mkdir(mode=0o700); target_root.mkdir(mode=0o700)
    source, target = source_root / "000000.raw", target_root / "cut-0000.json.gz"
    raw = b"OWN_NONROOT_RENAME_ONLY\0\xff" * 30000; source.write_bytes(raw); source.chmod(0o600)
    return source, target, raw


def test_actual_own_nonroot_rename_preserves_bytes_and_all11_with_explicit_ctime(own_leaf):
    source, target, raw = own_leaf; before = parts.identity(source.lstat())
    proof = parts._move_owned_leaf(source, target, expected_bytes=len(raw), expected_sha256=calibration.digest(raw))
    assert not source.exists() and proof["identity_before"] == before
    after = proof["identity_after"]
    assert all(after[key] == before[key] for key in parts.UNCHANGED_ON_RENAME)
    assert after["st_ctime_ns"] >= before["st_ctime_ns"] and proof["ctime_restored"] is False
    assert proof["additional_payload_copy_bytes"] == proof["hardlinks_created"] == 0
    assert target.read_bytes() == raw and "native_FIN" not in proof and "G5_GREEN" not in proof


@pytest.mark.parametrize("fault", ["sha", "size", "oversized", "mode", "hardlink", "symlink", "existing_target", "parent_mode"])
def test_actual_leaf_attacks_preserve_source_and_foreign_targets(own_leaf, fault):
    source, target, raw = own_leaf; size = len(raw); digest = calibration.digest(raw)
    if fault == "sha": digest = "0" * 64
    elif fault == "size": size -= 1
    elif fault == "oversized": size = parts.MAX_LEAF + 1
    elif fault == "mode": source.chmod(0o644)
    elif fault == "hardlink": os.link(source, source.parent / "alias")
    elif fault == "symlink":
        original = source.with_name("original"); source.rename(original); source.symlink_to(original)
    elif fault == "existing_target": target.write_bytes(b"FOREIGN_PRESERVED")
    elif fault == "parent_mode": target.parent.chmod(0o755)
    with pytest.raises(ValueError): parts._move_owned_leaf(source, target, expected_bytes=size, expected_sha256=digest)
    assert source.exists()
    if fault == "existing_target": assert target.read_bytes() == b"FOREIGN_PRESERVED"
    else: assert not target.exists()


def test_kernel_noreplace_rejects_target_race_without_overwrite(own_leaf):
    source, target, raw = own_leaf; target.write_bytes(b"FOREIGN_KERNEL_VETO")
    source_fd = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY)
    target_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(OSError) as error: parts._rename_no_replace(source_fd, source.name, target_fd, target.name)
        assert error.value.errno == errno.EEXIST
    finally:
        os.close(target_fd); os.close(source_fd)
    assert source.read_bytes() == raw and target.read_bytes() == b"FOREIGN_KERNEL_VETO"


def test_noatime_census_preserves_all_directory_and_file_fields(own_leaf):
    source, _, _ = own_leaf; child = source.parent / "nested"; child.mkdir(mode=0o700)
    leaf = child / "document.json"; leaf.write_bytes(b"OWN_SOURCE"); leaf.chmod(0o600)
    nodes = (source.parent, child, source, leaf); stale = 1_700_000_000_000_000_000
    for path in nodes: os.utime(path, ns=(stale, stale + 1))
    before = {path: parts.identity(path.lstat()) for path in nodes}; inventory = parts._tree_inventory(source.parent)
    assert inventory["nested/document.json"] == before[leaf] and inventory["nested"] == before[child]
    assert before == {path: parts.identity(path.lstat()) for path in nodes}


def test_census_rejects_actual_symlink_alias(own_leaf):
    source, _, _ = own_leaf; (source.parent / "alias").symlink_to(source)
    with pytest.raises(ValueError, match="GHOST_OR_ALIAS"): parts._tree_inventory(source.parent)


@pytest.mark.parametrize("fake", [None, {}, {"both_FIN": True}, {"complete_original_1202_frame_set_observed": True}, object.__new__(captured.RawCapture)])
def test_json_or_unissued_capture_cannot_enter_procedural_packer(fake):
    with pytest.raises(ValueError, match="CURRENT_OPAQUE_PINNED_STREAM"): parts.package(fake)


def test_package_constructor_and_unissued_object_cannot_mint_fin_authority():
    with pytest.raises(ValueError, match="AUTHENTIC_POST_FIN_CAPTURE_FACTORY"): parts.PackedSourceParts()
    fake = object.__new__(parts.PackedSourceParts)
    with pytest.raises(ValueError, match="CURRENT_OPAQUE_ISSUED"): parts.validate_package(fake)
    with pytest.raises(TypeError, match="CANNOT_BE_SERIALIZED"): pickle.dumps(fake)


def test_source_authenticates_capture_and_capacity_before_rename_without_rootlog_cleanup():
    source = inspect.getsource(parts.package)
    assert source.index("captured.validate_capture") < source.index("plan_documentary") < source.index(".mkdir")
    assert source.index("require_publication_capacity") < source.index(".mkdir")
    for forbidden in ("unlink(", "rmdir(", "root-worker-native.log", "chown(", "chmod(", "utime(", "os.link("):
        assert forbidden not in source
    primitive = inspect.getsource(parts._move_owned_leaf)
    assert primitive.index("_hash_fd") < primitive.index("_rename_no_replace")
    assert "O_NOATIME" in primitive and "UNCHANGED_ON_RENAME" in primitive


def test_source_limits_and_documentary_schema_preserve_no_qualification():
    assert parts.MAX_PARTS == 160 and parts.MAX_PART == 128 * parts.MIB
    assert parts.MAX_LEAF == 16 * parts.MIB and parts.MAX_RAW == 20 * parts.GIB and parts.RESERVE == 4 * parts.GIB
    assert parts.FAMILY_SCHEMA != "rc6.g5b-authenticated-original-frame-parts.v2"


def test_primary_six_roles_are_renamed_outside_frame_and_runtime_parts_without_copy():
    plan = parts.plan_documentary(declared_documentary(runtime_count=9))
    assert len(plan["primary"]) == 6
    assert {row["role"] for row in plan["primary"]} == set(parts.SOURCE_ROLES)
    runtime = [row for row in plan["parts"] if row["category"] == "runtime"]
    assert len(runtime) == 1 and len(runtime[0]["members"]) == 3
    assert not set(parts.SOURCE_ROLES.values()).intersection(row["relative_source"] for row in runtime[0]["members"])
    assert next(row for row in plan["primary"] if row["role"] == "native_Horizon")["member"] == "native_Horizon.json"


@pytest.mark.parametrize("fault", ["category", "source", "run", "index", "bool_run", "bool_index"])
def test_artifact_names_are_source_run_attempt_one_and_bounded_global_index(fault):
    args = {"category": "frames", "source_sha": "a" * 40, "run_id": 123, "part_index": 0}
    if fault == "category": args["category"] = "foreign"
    elif fault == "source": args["source_sha"] = "a" * 39
    elif fault == "run": args["run_id"] = 0
    elif fault == "index": args["part_index"] = 160
    elif fault == "bool_run": args["run_id"] = True
    elif fault == "bool_index": args["part_index"] = True
    with pytest.raises(ValueError, match="SOURCE_FIXED_ARTIFACT_NAME"): parts.artifact_name(**args)


def test_exact_names_match_official_composite_source_shape():
    assert parts.artifact_name("frames", source_sha="a" * 40, run_id=123, part_index=0) == "rc6-g5a-frames-" + "a" * 40 + "-123-1-000"
    assert parts.artifact_name("runtime", source_sha="b" * 40, run_id=456, part_index=159).endswith("-456-1-159")


def test_actual_documentary_control_read_records_kernel_read_atime_without_reset(tmp_path):
    root = tmp_path / "documentary-only"; root.mkdir(mode=0o700); path = root / "owned.json"
    raw = calibration.wire({"declared_documentary_only": True, "native_authority_granted": False})
    path.write_bytes(raw); path.chmod(0o600)
    stale = 1_700_000_000_000_000_000; os.utime(path, ns=(stale, stale + 1))
    before = parts.identity(path.lstat())
    reference = {"bytes": len(raw), "sha256": calibration.digest(raw), "identity": before}
    # Ordinary uploader-like read legitimately changes atime only.
    assert path.read_bytes() == raw
    uploaded_read_identity = parts.identity(path.lstat())
    assert uploaded_read_identity["st_atime_ns"] > before["st_atime_ns"]
    assert all(uploaded_read_identity[key] == before[key] for key in parts.IDENTITY_FIELDS if key != "st_atime_ns")
    doc, observed = parts._read_document(path, reference=reference)
    assert doc["native_authority_granted"] is False and observed == uploaded_read_identity
    assert parts.identity(path.lstat()) == uploaded_read_identity


@pytest.mark.parametrize("fault", ["ctime", "mtime", "inode", "mode", "nlink", "bytes", "sha"])
def test_read_atime_exception_does_not_admit_any_other_control_rebinding(tmp_path, fault):
    root = tmp_path / "control-only"; root.mkdir(mode=0o700); path = root / "owned.json"
    raw = calibration.wire({"no_native_authority": True}); path.write_bytes(raw); path.chmod(0o600)
    reference = {"bytes": len(raw), "sha256": calibration.digest(raw), "identity": parts.identity(path.lstat())}
    if fault == "ctime": reference["identity"]["st_ctime_ns"] -= 1
    elif fault == "mtime": reference["identity"]["st_mtime_ns"] -= 1
    elif fault == "inode": reference["identity"]["st_ino"] += 1
    elif fault == "mode": path.chmod(0o644)
    elif fault == "nlink": os.link(path, root / "alias")
    elif fault == "bytes": reference["bytes"] += 1
    elif fault == "sha": reference["sha256"] = "0" * 64
    with pytest.raises(ValueError): parts._read_document(path, reference=reference)


def test_source_late_sealer_never_reconstructs_raw_capture_native_fin_or_green():
    source = inspect.getsource(parts.seal_documentary_uploaded_parts)
    assert source.index("actions.issuer") < source.index("verify_documentary_package") < source.index("run = get")
    assert "ParentAPI.request" in source and "GITHUB_WORKFLOW_REF" in source
    assert '"in_progress"' in source and '"conclusion") is None' in source
    assert 'matches[0].get("status") == "completed"' in source and "OFFICIAL_UPLOAD_STEPS" in source
    assert "_ISSUED" not in source and "_CAPTURES" not in source and "object.__new__" not in source
    assert '"later_completed_successful_origin_authentication_required": True' in source
    assert '"native_authority_granted": False' in source and '"G5_GREEN": False' in source
    verifier = inspect.getsource(parts.verify_documentary_package)
    assert "_read_leaf" in verifier and 'if key != "st_atime_ns"' in verifier
    assert '"allowedReadAtimeRecorded"' in verifier and '"atime_restored": False' in verifier


def test_non_actions_or_synthetic_api_cannot_seal_any_uploaded_parts(tmp_path):
    calls = []
    def synthetic_get(path): calls.append(path); return {"fake": True}
    with pytest.raises(ValueError, match="ACTUAL_FIRST_CANONICAL_MANUAL_ACTIONS|CURRENT_SOURCE_READONLY_API"):
        parts.seal_documentary_uploaded_parts(tmp_path / "g5a-source-parts-manifest.json",
            expected_manifest_sha256="a" * 64, get=synthetic_get, run_id=1)
    assert calls == []
