"""Verify exact PAPER V3 material captured by the original Horizon producer.

This is a verifier, not a producer or launch authority. Its caller must first
authenticate the Actions ZIP, job, run, attempt and every referenced member.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from rc6_shadow_runtime.archive_format_contract import (
    SOURCE_MEMBERS, writer_codec_restorer_binding,
)
from scripts.rc6_g5p_frames import parse, verify_frames

PROFILE = "PAPER_V3_ONLY"
REVISION = "PAPER_V3_ONLY_G5P_V1"
REQUIRED = frozenset({"Horizon", "native_Horizon", "g5p_frames_manifest",
                      "g5p_owned_fin", "g5p_acquisition_model", "ownership_index"})


def require(value, reason):
    if not value:
        raise ValueError(reason)


def verify_material(row, captured, *, read_frame, source_root, model_origin=None):
    require(type(row) is dict and row.get("gate") == "G5P"
            and row.get("release_profile") == PROFILE
            and row.get("contract_revision") == REVISION
            and type(captured) is dict and REQUIRED <= set(captured),
            "G5P_TYPED_SOURCE_AND_REQUIRED_RAW_EVIDENCE")
    horizon = parse(captured["Horizon"])
    native = parse(captured["native_Horizon"])
    fin = parse(captured["g5p_owned_fin"])
    manifest = parse(captured["g5p_frames_manifest"])
    model = parse(captured["g5p_acquisition_model"])
    require(horizon.get("source_sha") == row["source_sha"]
            and horizon.get("source_tree") == row["source_tree"]
            and horizon.get("native_result_sha256") == hashlib.sha256(captured["native_Horizon"]).hexdigest()
            and horizon.get("native_result_bytes") == len(captured["native_Horizon"])
            and horizon.get("native_horizon_validated") is True
            and horizon.get("physical_custody_closed") is True
            and horizon.get("native_finalizer5_green_before_payload_reads") is True
            and horizon.get("original_native_acceptance_checks_executed") is True
            and horizon.get("errors") == [],
            "G5P_ORIGINAL_HORIZON_FIN_OR_RESULT_RED")
    require(native.get("source_sha") == row["source_sha"]
            and native.get("source_tree") == row["source_tree"]
            and native.get("schema") == "rc6.native-archive-v3-profile.v2"
            and native.get("archive_format") == "COMPONENT_V3"
            and native.get("private_comparison_active") is False
            and native.get("ticks_requested") == 1201
            and native.get("native_ticks_executed") == 1202
            and native.get("archive_restore_count") == 1202
            and native.get("restarts") == [361, 841]
            and all(native.get(name) is True for name in (
                "execution_complete", "native_horizon_contract_verified", "horizon_complete",
                "complete", "acceptance_complete", "code_source_unchanged",
                "source_database_unchanged", "import_graph_verified",
                "source_index_and_tar_unchanged"))
            and "error" not in native and "finalization_error" not in native,
            "G5P_ORIGINAL_NATIVE_V3_1202_INCOMPLETE")
    require(fin == native.get("native_child_infrastructure")
            and fin.get("source_sha") == row["source_sha"]
            and fin.get("source_tree") == row["source_tree"]
            and fin.get("native_finalizer_closed") is True
            and native.get("g5p_original_frames_captured") == 1202
            and native.get("g5p_frames_manifest_sha256") ==
                hashlib.sha256(captured["g5p_frames_manifest"]).hexdigest(),
            "G5P_ORIGINAL_FIN_AND_FRAME_MANIFEST_NOT_CLOSED")
    root = Path(source_root)
    binding = writer_codec_restorer_binding({name: (root / name).read_bytes()
                                             for name in SOURCE_MEMBERS})
    require(binding.get("native_runtime_archive_format") == "COMPONENT_V3"
            and binding.get("native_v4_write_enabled") is False
            and native.get("g5p_writer_binding_sha256") == binding["binding_sha256"],
            "G5P_EFFECTIVE_SOURCE_WRITER_NOT_BYTE_BOUND_V3")
    frames = verify_frames(manifest, read_frame=read_frame, cuts=native.get("cuts"),
                           source_sha=row["source_sha"], source_tree=row["source_tree"],
                           owner_session=row.get("owner_session"), run_id=row["run_id"],
                           run_attempt=row["run_attempt"],
                           writer_binding_sha256=binding["binding_sha256"])
    # The acquisition is a distinct, earlier, non-certifying Actions run. The
    # caller must authenticate its ZIP/run/job/digest separately before setting
    # this marker; a report from the material run itself is not an admission.
    require(type(model_origin) is dict
            and model_origin.get("source_sha") == row["source_sha"]
            and model_origin.get("run_id") == model.get("run_id")
            and model_origin.get("run_attempt") == 1
            and model_origin.get("exact_model_sha256") ==
                hashlib.sha256(captured["g5p_acquisition_model"]).hexdigest()
            and model_origin.get("actions_zip_authenticated") is True
            and model.get("schema") == "rc6.g5p-v3-acquisition-model.v1"
            and model.get("status") == "ACQUISITION_COMPLETE_NOT_CERTIFICATION"
            and model.get("source_sha") == row["source_sha"]
            and model.get("source_tree") == row["source_tree"]
            and model.get("owner_session") == row.get("owner_session")
            and type(model.get("original_prefix_sha256")) is str
            and len(model["original_prefix_sha256"]) == 64
            and model.get("actual_original_cuts") == 1202
            and type(model.get("run_id")) is int and model["run_id"] != row["run_id"]
            and model.get("run_attempt") == 1
            and model.get("material_qualification_claimed") is False,
            "G5P_PRIOR_AUTHENTIC_NONCERT_ACQUISITION_MODEL_REQUIRED")
    # The acquisition and certification are independent native runs. Their
    # original bytes may differ; both complete byte sets are replayed.
    for key, value in (("live_maximum_bytes", 128 * 1024**2),
                       ("live_maximum_files", 512),
                       ("archive_maximum_bytes", 512 * 1024**2),
                       ("archive_maximum_files", 32768)):
        require(native.get(key) == value and model.get(key) == value,
                "G5P_V3_EFFECTIVE_LIMIT_MODEL_REBOUND")
    for name, maximum in (("archive", 512 * 1024**2), ("live", 128 * 1024**2)):
        peak = native.get("peak_" + name, {})
        require(type(peak) is dict and
                all(type(peak.get(metric)) is int and 0 <= peak[metric] <= maximum
                    for metric in ("logical_bytes_including_directories",
                                   "allocated_bytes_including_directories")),
                "G5P_V3_PHYSICAL_OR_LOGICAL_PEAK_RED")
    reserve = native.get("filesystem_reserve_observation", {})
    require(reserve.get("reserve_bytes") == 2 * 1024**3
            and reserve.get("minimum_free_inode_percent") == 10
            and type(reserve.get("minimum_observed_free_bytes")) is int
            and reserve["minimum_observed_free_bytes"] >= 2 * 1024**3
            and type(reserve.get("minimum_observed_free_inode_percent")) in (int, float)
            and reserve["minimum_observed_free_inode_percent"] >= 10
            and native.get("peak_scratch_native_policy_occupied_bytes", 10**20) <= 512 * 1024**2
            and native.get("peak_rss_bytes", 10**20) <= 2 * 1024**3,
            "G5P_V3_NATIVE_RESERVE_SCRATCH_OR_RSS_RED")
    return {"writer_codec_restorer_binding": binding,
            "original_frames": frames, "source_sha": row["source_sha"],
            "source_tree": row["source_tree"], "acquisition_run_id": model["run_id"],
            "G5P_material_verified": True, "G7_G8_native_execution_still_required": True,
            "deploy_authorization": "NOT_GRANTED"}
