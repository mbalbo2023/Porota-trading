"""Actual native producer -> sealed four-role cut -> bounded read-only pages."""
from datetime import timedelta
import json
from pathlib import Path
import time

import pytest

from rc6_shadow_runtime import persistence
from rc6_shadow_runtime.persistence import read_committed_generation, read_committed_projection
from rc6_shadow_runtime.projection import cohort_id
from tests.rc6_dashboard_native_fixture import native_fixture


def custody_stats(root):
    paths = [*Path(root).rglob("*"), *Path(str(root)+".authority").rglob("*")]
    return {str(path): (path.stat().st_ino, path.stat().st_size, path.stat().st_mtime_ns, path.stat().st_atime_ns)
            for path in paths if path.is_file()}


def test_native_four_role_cut_projects_exact_counts_last_page_and_preserves_source_custody(tmp_path):
    native = native_fixture(tmp_path, count=25)
    before = custody_stats(native.root)
    page = read_committed_projection(native.root, offset=40, deadline=time.monotonic()+1)
    assert page["export_contract"]["schema"] == "rc6.shadow-ui-committed-projection.v1"
    assert page["export_contract"]["verification_level"] == "WIRE_AND_PROJECTION_SEMANTICS"
    assert set(page["manifest"]["files"]) == set(persistence.GENERATION_ROLES)
    assert page["pointer"] == native.cut["pointer"]
    expected = sum(len(plan["telemetry"]) for plan in native.cut["report"]["engines"].values())
    assert page["dataset_pages"]["opportunities"]["total"] == expected == 50
    assert len(page["dataset_pages"]["opportunities"]["rows"]) == 10
    assert len(json.dumps(page).encode()) < 4*1024**2
    for role, record in page["manifest"]["files"].items():
        assert page["export_contract"]["verified_payloads"][role]["payload_digest"] == record["payload_digest"]
    assert page["export_contract"]["derivation"] == {
        role: page["manifest"]["files"][role]["payload_digest"] for role in persistence.ROLES}
    assert custody_stats(native.root) == before
    full = read_committed_generation(native.root, roles=("report", "status"))
    assert "checkpoint" not in full and full["pointer"] == page["pointer"]
    assert full["export_contract"]["verification_level"] == "FULL_LOGICAL_SEMANTICS"


def test_native_atomic_admission_is_not_a_second_funnel_decision_and_preserves_entry(tmp_path):
    native = native_fixture(tmp_path, count=3, with_future=False)
    funnel = native.cut["report"]["operational_funnel"]
    with native.store.connect() as connection:
        snapshots = connection.execute("SELECT payload_json FROM decision_evidence_snapshots").fetchall()
    phases = [json.loads(row[0]).get("capture_phase", "NATIVE_DECISION") for row in snapshots]
    assert phases.count("ATOMIC_PAPER_ADMISSION") == 1
    assert phases.count("NATIVE_DECISION") >= 1
    assert funnel["denominators"]["lifetime_evaluations_since_watermark"] == phases.count("NATIVE_DECISION")
    assert len(native.cut["report"]["economic_exit_lab"]["entries"]) == 1
    native.worker.tick(native.as_of+timedelta(seconds=30))
    after = read_committed_generation(native.root)
    assert after["report"]["operational_funnel"]["denominators"]["lifetime_evaluations_since_watermark"] == phases.count("NATIVE_DECISION")


def test_native_cohort_pagination_and_filters_keep_selected_denominators_over_all_matches(tmp_path):
    native = native_fixture(tmp_path, count=25)
    all_groups = native.cut["report"]["operational_funnel"]["cohorts"]
    page = read_committed_projection(native.root, filters={"family": "ACCIONES", "funnel_offset": "10", "state": "HOT"})
    scope = page["funnel_scope"]
    expected = sum((row.get("family") or row["identity"][1]) == "ACCIONES" for row in all_groups)
    assert scope["state"] == "AVAILABLE" and scope["total_groups"] == expected
    assert scope["groups_offset"] == 10 and len(scope["groups"]) == 10
    selected_id = cohort_id(scope["selected"])
    only = read_committed_projection(native.root, filters={"cohort": selected_id})
    assert only["funnel_scope"]["total_groups"] == 1
    assert only["funnel_scope"]["selected"] == scope["selected"]
    assert only["dataset_pages"]["opportunities"]["total"] == 50
    assert scope["selected"]["currency"] in scope["label"] and scope["selected"]["channel"] in scope["label"]


def test_preopen_capacity_preserves_planned_revisits_with_unknown_achieved_clock(tmp_path):
    native = native_fixture(tmp_path, count=12, with_future=False, with_spot=False)
    page = read_committed_projection(native.root)
    assert page["dataset_pages"]["capacity"]["rows"]
    for row in page["dataset_pages"]["capacity"]["rows"]:
        original = native.cut["report"]["engines"][row["engine"]]["telemetry"][:10]
        assert row["telemetry"] == original


@pytest.mark.parametrize("role", tuple(persistence.GENERATION_ROLES))
def test_any_wire_member_corruption_blocks_projected_consumer(tmp_path, role):
    native = native_fixture(tmp_path, count=3)
    generation = native.root / ("gen-"+native.cut["pointer"]["generation_id"])
    member = generation / persistence.GENERATION_ROLES[role]
    raw = member.read_bytes(); member.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
    with pytest.raises(ValueError, match="FILE_HASH_MISMATCH"):
        read_committed_projection(native.root)


def test_projection_deadline_and_boolean_offset_are_rejected(tmp_path):
    native = native_fixture(tmp_path, count=3)
    with pytest.raises(ValueError, match="DEADLINE"):
        read_committed_projection(native.root, deadline=time.monotonic()-1)
    with pytest.raises(ValueError, match="QUERY_INVALID"):
        read_committed_projection(native.root, offset=False)
