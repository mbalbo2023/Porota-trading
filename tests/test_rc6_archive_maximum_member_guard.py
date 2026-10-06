"""Two automatic native guards share one fresh near64MiB/depth32 execution.

These checks create actual producer members and archive receipts on every run.
They do not accept a previously passing report or claim business/horizon/image
coverage from the deliberately synthetic planner publication inputs.
"""
from pathlib import Path
import os
import shutil
import tempfile

import pytest

from scripts.rc6_archive_maximum_member_probe import (
    MAX_ARCHIVE, MAX_LIVE, MAX_MEMBER, MAX_RSS, MAX_SCRATCH, MAX_WALL,
    MIN_MEMBER, execute_governed_witness, safe_path, validate_completed_child_resources,
)


@pytest.fixture(scope="module")
def maximum_member_native_execution(request):
    repo = Path(__file__).absolute().parents[1]
    # The source capture and native generations reside on the checkout's real
    # disk, outside the repository. The default /tmp may be a32MiB tmpfs.
    parent = Path(tempfile.mkdtemp(prefix="rc6-maximum-member-", dir=repo.parent))
    assert parent.lstat().st_uid == os.geteuid() and parent.lstat().st_dev == repo.lstat().st_dev
    failures_before = request.session.testsfailed
    execution = execute_governed_witness(repo, parent / "execution")
    print({"shared_native_execution_id": execution["execution_id"],
           "native_executions": 1, "guard_nodes": 2, "raw_root": str(execution["raw_root"]),
           "native_receipt_sha256": execution["launch"]["native_receipt_sha256"],
           "source_sha": execution["launch"]["source_sha"],
           "source_tree": execution["launch"]["source_tree"],
           "native_wall_seconds": execution["report"]["elapsed_wall_seconds"],
           "real_peak_rss_bytes": execution["resources"]["real_peak_rss_bytes"]})
    yield execution
    # Keep the small exact raw receipts even on success. Failed executions keep
    # all private source/data for diagnosis; no shared runtime path is cleaned.
    if request.session.testsfailed == failures_before:
        for name in ("native-data", "frozen"):
            candidate = safe_path(execution["output_root"] / name)
            assert candidate.is_relative_to(parent) and candidate.lstat().st_uid == os.geteuid()
            shutil.rmtree(candidate)


def _record_shared_execution(execution, record_property, role):
    record_property("shared_native_execution_id", execution["execution_id"])
    record_property("shared_native_executions", "1")
    record_property("guard_nodes_in_shared_execution", "2")
    record_property("native_guard_role", role)
    record_property("native_receipt_sha256", execution["launch"]["native_receipt_sha256"])
    record_property("native_source_sha", execution["launch"]["source_sha"])
    record_property("native_source_tree", execution["launch"]["source_tree"])


def test_native_near64mib_original_ancestor_depth32_restore_has_real_bounded_rss_and_exact_members(
    maximum_member_native_execution, record_property,
):
    execution = maximum_member_native_execution
    _record_shared_execution(execution, record_property, "MAXIMUM_ORIGINAL_MEMBER_DEPTH32_MEMORY")
    report, resources, launch = execution["report"], execution["resources"], execution["launch"]
    assert launch["native_child_started"] is True and launch["prior_native_data_present"] is False
    assert launch["returncode"] == resources["returncode"] == 0 and resources["outer_timed_out"] is False
    assert resources["pid"] == report["native_pid"] == launch["native_pid"] > 0
    assert report["execution_id"] == resources["execution_id"] == execution["execution_id"]
    assert report["uid"] == os.geteuid() and report["installed_closure"]["status"] == "GREEN"
    assert report["installed_closure"]["installed_total"] == report["installed_closure"]["expected_total"] == 157
    assert report["overlay_count"] == 0 and report["source_files"] == launch["source_files"] > 0
    assert launch["whole_checkout_bytes_modes_blobs_unchanged"] is True
    assert report["complete_source_bytes_modes_blobs_unchanged"] is True
    assert report["raw_git_authority_unchanged"] is True
    assert report["unexpected_product_imports"] == [] and report["product_imports"]
    assert report["network_attempts"] == 0 and report["source_sqlite_attempts_after_custody_baseline"] == []
    assert 0 < report["peak_rss_bytes"] == resources["real_peak_rss_bytes"] <= MAX_RSS
    assert 0 < report["elapsed_wall_seconds"] <= MAX_WALL
    assert 0 < resources["elapsed_wall_seconds"] <= MAX_WALL and resources["cpu_total_seconds"] > 0
    validate_completed_child_resources(resources)
    # Protocol-negative controls over this fresh run's real kernel receipt;
    # no301s process is executed or claimed. Late output cannot use the330s
    # termination window, and bool/NaN cannot masquerade as a measured time.
    for altered_time in (MAX_WALL+1.0, True, float("nan")):
        with pytest.raises(AssertionError, match="NATIVE_KERNEL_FULL_ENVELOPE_EXCEEDS_300SECONDS"):
            validate_completed_child_resources({**resources, "elapsed_wall_seconds": altered_time})
    assert 1 <= len(report["calibration"]) <= 3 and report["real_fsync_calls"] > 0
    cuts, restored = report["cuts"], report["restore"]
    assert len(cuts) == 34 and [cut["sequence"] for cut in cuts] == list(range(1, 35))
    archived = [cut for cut in cuts if cut["archived"]]
    assert [cut["sequence"] for cut in archived] == [*range(1, 32), 33, 34]
    assert MIN_MEMBER <= cuts[0]["projection_bytes"] <= MAX_MEMBER
    assert all(0 < cut["projection_bytes"] <= 1024**2 for cut in cuts[1:])
    assert restored["dependency_depth"] == 32 and len(restored["decoded"]) == 33
    assert [node["previous_depth"] for node in restored["decoded"]] == [None, *range(32)]
    assert [node["actual_reconstructed_sha256"] for node in restored["decoded"]] == [
        cut["original_members"]["projection.sqlite"]["sha256"] for cut in archived
    ]
    assert restored["decoded"][0]["actual_reconstructed_bytes"] == cuts[0]["projection_bytes"]
    assert restored["exact_five_original_members"] is True and restored["encoder_attempts"] == 0
    assert restored["protected_roots_all_stats_and_hashes_unchanged"] is True
    assert restored["verification_level"] == "ALL_ORIGINAL_MEMBER_BYTES_MANIFEST_CRC_AND_ROLE_WIRE"
    assert report["large_ancestor_original_five_member_restore"] == {
        "encoder_attempts": 0, "exact_manifest": True, "exact_member_sizes_and_sha256": True,
        "protected_roots_all_stats_and_hashes_unchanged": True,
        "verification_level": "ALL_ORIGINAL_MEMBER_BYTES_MANIFEST_CRC_AND_ROLE_WIRE",
    }
    assert len(cuts[0]["original_members"]) == len(cuts[-1]["original_members"]) == 5
    assert len(report["data_custody"]) >= 3
    assert all(node["before"] == node["after"] and node["unchanged"] is True
               for node in report["data_custody"].values())
    for name, peak in report["peaks"].items():
        ceiling = MAX_LIVE if name.endswith(".live") else MAX_ARCHIVE if name.endswith(".archive") else MAX_SCRATCH
        assert max(peak["logical_bytes"], peak["allocated_bytes"]) <= ceiling
        assert peak["entries_excluding_root"] <= (512 if name.endswith(".live") else 32768)
    assert all(row["owner_uid"] == os.geteuid() and row["max_bytes"] == MAX_SCRATCH
               and row["reserve_bytes"] == 2*1024**3 and row["min_free_inode_percent"] == 10
               for row in report["final_scratch_namespace_admissions"].values())
    assert report["minimum_free_bytes_observed"] >= 2*1024**3
    assert report["physical_pre_destroy_observations"]
    assert report["full_business_ticks_executed"] == 0
    assert report["artifact_validated"] is report["runtime_validated"] is report["horizon_validated"] is False


def test_native_admission_rejects_three_observed_near64mib_images_under_original_live128mib_without_mutation(
    maximum_member_native_execution, record_property,
):
    execution = maximum_member_native_execution
    _record_shared_execution(execution, record_property, "NATIVE_THREE_LARGE_LIVE128MIB_DENIAL")
    report = execution["report"]
    denial = report["live_three_large_denial"]
    metrics = denial["metrics"]
    assert denial["kind"] == "NATIVE_ADMISSION_FORECAST_ONLY" and denial["status"] == "EXPECTED_RED"
    assert denial["admission_api"] == "EvidenceRetention.prepare"
    assert denial["reason"] == metrics["reason"] == "RETENTION_HARD_BYTES_CAPACITY_REACHED"
    assert denial["forecast_projection_bytes"] == 3*report["cuts"][0]["projection_bytes"] > MAX_LIVE
    assert metrics["maximum_bytes"] == MAX_LIVE and metrics["maximum_files"] == 512
    assert metrics["projected_bytes"] == metrics["bytes"] + denial["forecast_projection_bytes"] > MAX_LIVE
    assert metrics["projected_files"] < metrics["maximum_files"]
    assert metrics["factual_paths_effect"] == "NONE" and metrics["real_orders_sent"] == 0
    assert metrics["real_routes"] == "NOT_CALLED" and metrics["shadow_degraded"] is True
    assert denial["root_custody_before"] == denial["root_custody_after"]
    assert all(node["unchanged"] is True and node["before"] == node["after"]
               for node in report["data_custody"].values())
