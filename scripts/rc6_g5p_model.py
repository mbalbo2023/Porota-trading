"""Non-certifying V3 acquisition model from a complete original Horizon run.

The model is a measured admission envelope for one later, independent native
run.  It cannot predict an unobserved peak and never grants a gate receipt.
"""
from __future__ import annotations

import hashlib
from scripts.rc6_g5p_frames import parse, verify_frames, wire

SCHEMA = "rc6.g5p-v3-acquisition-model.v1"
DOCUMENT_SHA256 = "c348fce9ae75fd179310d70cc084509264cc3e4bd8786f6d5b367ef6af7c8970"
LIMITS = {"live_maximum_bytes": 128 * 1024**2, "live_maximum_files": 512,
          "archive_maximum_bytes": 512 * 1024**2, "archive_maximum_files": 32768}


def require(value, reason):
    if not value:
        raise ValueError(reason)


def build_model(native_wire, manifest_wire, *, read_frame, owner_session,
                writer_binding_sha256):
    """Run only after the original FIN and full RAW capture have closed."""
    native, manifest = parse(native_wire), parse(manifest_wire)
    require(native.get("schema") == "rc6.native-archive-v3-profile.v2"
            and native.get("archive_format") == "COMPONENT_V3"
            and native.get("private_comparison_active") is False
            and native.get("ticks_requested") == 1201
            and native.get("native_ticks_executed") == 1202
            and native.get("archive_restore_count") == 1202
            and native.get("restarts") == [361, 841]
            and native.get("execution_complete") is True
            and native.get("native_horizon_contract_verified") is True
            and native.get("native_child_infrastructure", {}).get("native_finalizer_closed") is True
            and native.get("g5p_original_frames_captured") == 1202
            and native.get("g5p_frames_manifest_sha256") == hashlib.sha256(manifest_wire).hexdigest()
            and native.get("g5p_writer_binding_sha256") == writer_binding_sha256
            and all(native.get(key) == value for key, value in LIMITS.items()),
            "G5P_MODEL_REQUIRES_COMPLETE_GREEN_ORIGINAL_V3_ACQUISITION")
    source_sha, source_tree = native["source_sha"], native["source_tree"]
    run_id, run_attempt = manifest.get("run_id"), manifest.get("run_attempt")
    frames = verify_frames(manifest, read_frame=read_frame, cuts=native["cuts"],
                           source_sha=source_sha, source_tree=source_tree,
                           owner_session=owner_session, run_id=run_id,
                           run_attempt=run_attempt,
                           writer_binding_sha256=writer_binding_sha256)
    peaks = {}
    for name, ceiling in (("live", LIMITS["live_maximum_bytes"]),
                          ("archive", LIMITS["archive_maximum_bytes"])):
        observed = native.get("peak_" + name)
        require(type(observed) is dict
                and all(type(observed.get(key)) is int and 0 <= observed[key] <= ceiling
                        for key in ("logical_bytes_including_directories",
                                    "allocated_bytes_including_directories"))
                and type(observed.get("entries_excluding_root")) is int
                and 0 <= observed["entries_excluding_root"] <= LIMITS[name + "_maximum_files"],
                "G5P_MODEL_ACTUAL_NATIVE_PHYSICAL_PEAK_RED")
        peaks[name] = {key: observed[key] for key in (
            "logical_bytes_including_directories", "allocated_bytes_including_directories",
            "entries_excluding_root")}
    reserve = native.get("filesystem_reserve_observation", {})
    require(reserve.get("reserve_bytes") == 2 * 1024**3
            and reserve.get("minimum_free_inode_percent") == 10
            and type(reserve.get("minimum_observed_free_bytes")) is int
            and reserve["minimum_observed_free_bytes"] >= 2 * 1024**3
            and type(reserve.get("minimum_observed_free_inode_percent")) in (int, float)
            and reserve["minimum_observed_free_inode_percent"] >= 10,
            "G5P_MODEL_ACTUAL_NATIVE_RESERVE_RED")
    require(type(native.get("peak_rss_bytes")) is int
            and 0 <= native["peak_rss_bytes"] <= 2 * 1024**3
            and type(native.get("peak_scratch_native_policy_occupied_bytes")) is int
            and 0 <= native["peak_scratch_native_policy_occupied_bytes"] <= 512 * 1024**2,
            "G5P_MODEL_ACTUAL_PROCESS_OR_SCRATCH_RED")
    return {"schema": SCHEMA, "status": "ACQUISITION_COMPLETE_NOT_CERTIFICATION",
            "source_sha": source_sha, "source_tree": source_tree,
            "owner_session": owner_session, "run_id": run_id, "run_attempt": run_attempt,
            "native_result_sha256": hashlib.sha256(native_wire).hexdigest(),
            "frames_manifest_sha256": hashlib.sha256(manifest_wire).hexdigest(),
            "writer_binding_sha256": writer_binding_sha256,
            "original_prefix_sha256": frames["original_prefix_sha256"],
            "actual_original_cuts": frames["verified_original_cuts"],
            "measured_native_peaks": peaks, "measured_native_reserve": reserve,
            "peak_rss_bytes": native["peak_rss_bytes"],
            "peak_scratch_native_policy_occupied_bytes": native["peak_scratch_native_policy_occupied_bytes"],
            **LIMITS, "material_qualification_claimed": False,
            "G5P_GREEN_claimed": False, "G7_authorized": False,
            "DEPLOY_OWNER": "NOT_ACQUIRED", "real_orders_sent": 0}


def model_wire(model):
    require(type(model) is dict and model.get("schema") == SCHEMA,
            "G5P_MODEL_SCHEMA_REQUIRED")
    return wire(model)
