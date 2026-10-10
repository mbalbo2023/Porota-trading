"""Economic native five-member controls, never the 1200/6000 material run."""
from datetime import datetime
import gzip
import json
from pathlib import Path

import pytest

from rc6_shadow_runtime import archive_components as components
from scripts import rc6_cas_original_comparison as comparison
from scripts import rc6_material_horizon_producer as producer
from tests.test_rc6_component_archive import native, members, policy, tree_custody
from rc6_shadow_runtime.persistence import EvidenceFiles, shadow_evidence_root, shadow_archive_root
from rc6_shadow_runtime.retention import RetentionPressure
from tests.test_issue465_generations import publish


ROOT = Path(__file__).resolve().parents[1]


def test_original_model_keeps_exact_fixture_source_clocks_anchors_and_contract_without_generating_rows():
    identity = comparison.canonical_fixture_identity(ROOT)
    assert identity["fixture_function_sha256"] == "448c6efbbaa5b270e3ae575ed006f6101016ad67cbadddfc9d9bfeb2f610815b"
    assert identity["catalog_count"] == 1200 and identity["observation_rows"] == 6000
    assert identity["source_rows_materialized_by_this_model"] == 0
    clocks = comparison.horizon_schedule(datetime.fromisoformat(comparison.CONTRACT["preopen"]).date())
    assert len(clocks) == 1202
    assert clocks[0].isoformat() == comparison.CONTRACT["preopen"]
    assert clocks[1].isoformat() == comparison.CONTRACT["first_operational"]
    assert all((later - earlier).total_seconds() == 30 for earlier, later in zip(clocks[1:], clocks[2:]))
    assert (clocks[-1] - clocks[1]).total_seconds() == 36000
    assert comparison.CONTRACT["restart_indices"] == (361, 841)
    assert comparison.CONTRACT["archive_limit_bytes"] == 512 * 1024**2
    assert comparison.CONTRACT["maximum_dependency_depth"] == 32
    assert comparison.CONTRACT["contracted_retention_seconds"] == 32400
    assert comparison.CONTRACT["recovery_margin_seconds"] == 3600


def test_material_comparison_honors_current_model_hold_before_network_namespaces_or_fixture(tmp_path, monkeypatch):
    from scripts import rc6_architectural_gates as gates
    monkeypatch.setenv("RC6_ENABLE_SLICE_WRITE", "true")
    monkeypatch.setattr(gates, "verify_manifest", lambda *args, **kwargs: pytest.fail("HEAVY_ADMISSION_BYPASSED"))
    with pytest.raises(ValueError, match="HORIZON_ALL1202_PHYSICAL_MODEL_NO_VERIFICADO_BEFORE_MATERIAL"):
        comparison.authenticate_comparison(tmp_path / "missing-manifest.json", source_sha="a" * 40,
            source_tree="b" * 40, source_root=ROOT, output_root=tmp_path / "attempt")
    assert not (tmp_path / "attempt").exists()


def test_hook_passes_the_same_captured_five_byte_objects_and_actual_native_pins():
    original = {name: name.encode() for name in components.MEMBERS}
    node, pins, seen = {"scope": "UNIT_API_CONTROL_ONLY"}, {"a" * 32}, []
    class NativePins:
        def _archive_pins(self):
            return pins
    class Observer:
        def observe(self, captured, *, cut, native_pins):
            assert captured is original and cut is node and native_pins is pins
            seen.append(captured)
            return {"scope": "UNIT_API_CONTROL_ONLY"}
    assert producer.observe_original_cas_comparison(None, original, node, NativePins()) is None
    assert producer.observe_original_cas_comparison(Observer(), original, node, NativePins())["scope"] == "UNIT_API_CONTROL_ONLY"
    assert seen == [original]


def test_private_comparison_uses_own_catalogs_and_native_exact_five_member_restore_with_observed_temps(tmp_path):
    root, archive, _, directories, receipts = native(tmp_path)
    custody = {str(path): tree_custody(path) for path in (root, archive, root.with_name(root.name + ".authority"))}
    branches = [comparison._PrivateBranch(tmp_path / name, slices=slices)
                for name, slices in (("baseline", False), ("candidate", True))]
    for directory, receipt in zip(directories, receipts):
        original = members(directory)
        for branch in branches:
            result = branch.archive_originals(original, native_pins=policy(root, archive)._archive_pins())
            assert branch.owner.restore_generation(receipt["generation_id"])["members"] == original
            assert result["source_payload_copies"] == 0
            assert result["archive"]["allocated_bytes_including_directories"] <= 512 * 1024**2
            assert result["physical_catalog_records"] > 0
            assert result["reachable_whole_pack_count"] > 0
            assert result["measurement_scope"] == "NATIVE_FAULT_AND_FSYNC_OBSERVATIONS_NOT_CONTINUOUS_PEAK"
            assert branch.observations["archive_before_publish_object"] > 0
    assert branches[0].archive != branches[1].archive
    packs = [next(branch.archive.glob("*.cas.pack")).stat().st_ino for branch in branches]
    assert packs[0] != packs[1]
    assert custody == {str(path): tree_custody(path) for path in (root, archive, root.with_name(root.name + ".authority"))}


def test_original_proposals_keep_native_sorted_layout_for_a_differently_ordered_captured_dict(tmp_path):
    _, _, _, directories, _ = native(tmp_path, count=1)
    original = members(directories[0])
    branch = comparison._PrivateBranch(tmp_path / "baseline", slices=False)
    manifest_sha = components.sha(original["manifest.json"])
    manifest = components.validate_materialized(original, expected_manifest_sha256=manifest_sha)
    context = components.ComponentArchive(branch.owner)
    first, catalog, _ = context._original_proposals(original, manifest)
    second, other_catalog, _ = context._original_proposals(dict(reversed(list(original.items()))), manifest)
    assert tuple(first) == tuple(second) == tuple(sorted(original))
    assert context._select_plan(first, catalog, manifest, manifest_sha) == context._select_plan(second, other_catalog, manifest, manifest_sha)


@pytest.mark.parametrize("stage", ["archive_after_build_intent", "archive_after_publish_components",
    "archive_after_publish_recipe", "archive_before_clear_build_intent"])
@pytest.mark.parametrize("slices", [False, True])
def test_private_comparison_interrupted_native_build_recovers_same_bytes_and_exact_receipt_once(tmp_path, stage, slices):
    root, archive, _, directories, _ = native(tmp_path, count=1)
    original, fired = members(directories[0]), []
    def interrupt(point):
        if point == stage and not fired:
            fired.append(point)
            raise OSError("PRIVATE_ECONOMIC_INTERRUPTION")
    branch = comparison._PrivateBranch(tmp_path / "branch", slices=slices, fault_inject=interrupt)
    with pytest.raises(OSError, match="PRIVATE_ECONOMIC_INTERRUPTION"):
        branch.archive_originals(original)
    assert branch.owner.current_originals is None
    result = branch.archive_originals(original)
    assert branch.owner.restore_generation(result["receipt"]["generation_id"])["members"] == original
    assert branch.owner._archive_checkpoint()["receipt_count"] == 1
    assert not (branch.archive / "BUILD.json").exists()
    again = branch.archive_originals(original)
    assert again["receipt"] == result["receipt"]
    assert branch.owner._archive_checkpoint()["receipt_count"] == 1


def test_private_comparison_fresh_public_restore_rejects_corruption_without_touching_original_source(tmp_path):
    root, archive, _, directories, _ = native(tmp_path, count=1)
    original = members(directories[0])
    branch = comparison._PrivateBranch(tmp_path / "candidate", slices=True)
    result = branch.archive_originals(original)
    source = tree_custody(root)
    path = next(branch.archive.glob("*.cas.pack"))
    wire = path.read_bytes()
    path.write_bytes(wire[:-1] + bytes([wire[-1] ^ 1]))
    with pytest.raises(ValueError, match="PACK_HASH_MISMATCH"):
        branch.owner.restore_generation(result["receipt"]["generation_id"])
    assert source == tree_custody(root)


def test_audit_rejects_changed_source_policy_or_index_before_accessing_any_payload(tmp_path):
    policy = ROOT / "ops/policy/rc6-actions-custody-20261009.json"
    missing = tmp_path / "never-opened.zip"
    altered = tmp_path / "policy.json"
    altered.write_bytes(b"{}")
    with pytest.raises(ValueError, match="SOURCE_PINNED_POLICY_CHANGED"):
        comparison.audit_original_artifact(altered, missing, missing, 11502637203)
    index = tmp_path / "index.csv"
    index.write_bytes(b"artifact_id,path,uncompressed_bytes,sha256\n")
    with pytest.raises(ValueError, match="GIT_MEMBER_INDEX_CHANGED"):
        comparison.audit_original_artifact(policy, index, missing, 11502637203)


def test_lossless_result_publication_is_exclusive_bounded_and_separate_from_fin(tmp_path):
    value = {"schema": comparison.SCHEMA, "classification": "UNIT_CONTROL_ONLY_NO_HORIZON_DATA", "payload": "x" * 300000}
    path = tmp_path / "comparison-result.json.gz"
    published = comparison.publish_result(path, value)
    assert published["bytes"] == path.stat().st_size
    assert components.loads(gzip.decompress(path.read_bytes())) == value
    with pytest.raises(FileExistsError):
        comparison.publish_result(path, value)
    with pytest.raises(ValueError, match="RESULT_BOUND_OR_CUSTODY_INVALID"):
        comparison.publish_result(tmp_path / "too-large.gz", {"payload": "x" * comparison.MAX_RESULT_BYTES})


def _private_ack_fixture(tmp_path):
    """Existing small native persistence fixture, never admission/Horizon data."""
    native_root, controls = tmp_path / "native", tmp_path / "review"
    native_root.mkdir(mode=0o700); controls.mkdir(mode=0o700)
    database = native_root / "data/paper_v17/observer_v17.db"
    live, archive = shadow_evidence_root(database, {}), shadow_archive_root(database, {})
    archive.parent.mkdir(mode=0o700, parents=True)
    attempt = comparison.OriginalComparison(controls / "attempt", {"scope": "UNIT_API_ONLY_NO_MATERIAL_AUTHORITY"}, ROOT,
        _ack_review={"scope": "UNIT_API_ONLY_NO_MATERIAL_AUTHORITY"}, _producer_root=native_root)
    attempt.bind_native_archive(live, archive)
    return attempt, live, archive


def _capture_unit_cut(attempt, live, archive, number):
    with EvidenceFiles(live, archive_root=archive, archive_format="COMPONENT_V3") as files:
        cut = publish(files, number)
    generation = live / ("gen-" + cut["pointer"]["generation_id"])
    original = members(generation)
    binding = producer.original_member_binding(original, pointer=cut["pointer"])
    results = attempt._archive_pair(original, native_pins=policy(live, archive)._archive_pins())
    # Unit API records deliberately do NOT invent catalog_count=1200 or claim
    # OriginalComparison.observe()'s financial/source/schedule admission.
    attempt.cuts.append({"cut_index": len(attempt.cuts), "original_member_sha256": binding["original_member_sha256"],
                         "branches": results, "scope": "UNIT_API_ONLY_NO_MATERIAL_AUTHORITY"})
    attempt.pending_originals = original
    return generation, original, results


def test_existing_candidate_ack_restores_same_five_bytes_and_preserves_native_v3_writer(tmp_path, monkeypatch):
    attempt, live, archive = _private_ack_fixture(tmp_path)
    generation, original, results = _capture_unit_cut(attempt, live, archive, 1)
    # Test a typed V4 recipe already published in the private namespace, with
    # unchanged component bytes/codecs. This is no new native write capability.
    context = components.ComponentArchive(attempt.branches["V4"].owner)
    receipt = results["V4"]["receipt"]
    from tests.test_rc6_component_archive import reseal_recipe
    def v4_recipe(recipe):
        rows = []
        for cid, location in context._indices(recipe):
            raw = context._component(cid, location)
            rows.append(components.SLICE_COMPONENT_RECORD.pack(recipe["packs"].index(location[0]), location[1],
                0, len(raw), bytes.fromhex(cid)))
        recipe["schema"] = components.SLICE_RECIPE_SCHEMA
        recipe["components"] = components._array(b"".join(rows), count=len(rows), codec=components.SLICE_ARRAY_CODEC)
    receipt = reseal_recipe(archive, receipt, v4_recipe)
    results["V4"]["receipt"] = receipt
    custody = tree_custody(generation)
    monkeypatch.setattr(components.ComponentArchive, "build", lambda *args: pytest.fail("NATIVE_NEW_RECIPE_BUILD_FORBIDDEN"))
    owner = attempt.native_ack_owner(live, archive, archive_format="COMPONENT_V3")
    assert owner.archive_generation(generation) == receipt
    assert owner.restore_generation(receipt["generation_id"])["members"] == original
    assert json.loads((live / ("archive-ack-" + receipt["generation_id"] + ".json")).read_bytes()) == receipt
    assert attempt.native_ack_count == 1 and attempt.pending_originals is None
    assert tree_custody(generation) == custody
    assert components.NATIVE_WRITE_RECIPE_SCHEMA == components.RECIPE_SCHEMA


def test_private_candidate_red_stops_before_any_native_build_or_ack(tmp_path, monkeypatch):
    attempt, live, archive = _private_ack_fixture(tmp_path)
    def capacity_red(stage):
        if stage == "archive_after_build_intent":
            raise RetentionPressure("RETENTION_ARCHIVE_CAPACITY_REACHED", {"limit": 512 * 1024**2})
    attempt.branches["V4"].owner.fault_inject = capacity_red
    generation, original, results = _capture_unit_cut(attempt, live, archive, 1)
    assert results["V4"]["status"] == "RED_ORIGINAL_QUOTA_PRESERVED"
    monkeypatch.setattr(components.ComponentArchive, "build", lambda *args: pytest.fail("RED_NATIVE_FALLBACK_FORBIDDEN"))
    with pytest.raises(ValueError, match="CANDIDATE_RECOVERY_REQUIRED_BEFORE_ACK|CANDIDATE_RED_NO_NATIVE_FALLBACK"):
        attempt.native_ack_owner(live, archive, archive_format="COMPONENT_V3").archive_generation(generation)
    ident = components.loads(original["manifest.json"])["generation_id"]
    assert not (live / ("archive-ack-" + ident + ".json")).exists()
    assert attempt.native_ack_count == 0


def test_private_baseline_quota_red_keeps_candidate_and_original_sequence_running(tmp_path, monkeypatch):
    attempt, live, archive = _private_ack_fixture(tmp_path)
    def capacity_red(stage):
        if stage == "archive_after_build_intent":
            raise RetentionPressure("RETENTION_ARCHIVE_CAPACITY_REACHED", {"limit": 512 * 1024**2})
    attempt.branches["V3"].owner.fault_inject = capacity_red
    monkeypatch.setattr(components.ComponentArchive, "build", lambda *args: pytest.fail("NATIVE_NEW_RECIPE_BUILD_FORBIDDEN"))
    for number in range(1, 4):
        generation, original, results = _capture_unit_cut(attempt, live, archive, number)
        assert results["V3"]["status"] == ("RED_ORIGINAL_QUOTA_PRESERVED" if number == 1 else "BLOCKED_AFTER_ORIGINAL_QUOTA_RED")
        assert results["V4"]["status"] == "EXACT_ORIGINAL_BYTES_RESTORED"
        owner = attempt.native_ack_owner(live, archive, archive_format="COMPONENT_V3")
        receipt = owner.archive_generation(generation)
        assert owner.restore_generation(receipt["generation_id"])["members"] == original
    assert attempt.native_ack_count == len(attempt.cuts) == 3
    assert attempt.failures["V3"]["cut_index"] == 0
    assert attempt.branches["V3"].owner.archive_maximum_bytes == 512 * 1024**2


def test_private_candidate_ack_rotation_and_recovery_keep_native_pins_and_original_limits(tmp_path):
    attempt, live, archive = _private_ack_fixture(tmp_path)
    ids, captured = [], {}
    for number in range(1, 7):
        generation, original, results = _capture_unit_cut(attempt, live, archive, number)
        ident = results["V4"]["receipt"]["generation_id"]
        fired = []
        def interrupted_ack(stage):
            if number == 6 and stage == "private_candidate_after_native_ack" and not fired:
                fired.append(stage); raise OSError("PRIVATE_ACK_INTERRUPTED")
        owner = attempt.native_ack_owner(live, archive, archive_format="COMPONENT_V3", fault_inject=interrupted_ack)
        if number == 6:
            with pytest.raises(OSError, match="PRIVATE_ACK_INTERRUPTED"):
                owner.archive_generation(generation)
        assert owner.archive_generation(generation)["generation_id"] == ident
        ids.append(ident); captured[ident] = original
    native = policy(live, archive, auto_archive=True)
    metrics = native.prepare(additional_files=375, pinned=[ids[-2]])
    assert metrics["rotated_archived_generations"] > 0
    assert (live / ("gen-" + ids[-1])).is_dir() and (live / ("gen-" + ids[-2])).is_dir()
    assert native.policy.maximum_bytes == 128 * 1024**2 and native.policy.maximum_files == 512
    assert native.archive_maximum_bytes == 512 * 1024**2
    head = native._archive_checkpoint()
    assert head["contracted_horizon_seconds"] == 32400 and head["recovery_margin_seconds"] == 3600
    for ident, original in captured.items():
        assert native.restore_generation(ident)["members"] == original
    assert attempt.native_ack_count == 6


def test_private_comparison_completion_never_satisfies_g5():
    result = {"cuts": [{}] * 1202, "ticks_requested": 1201, "execution_complete": True,
        "source_database_unchanged": True, "code_source_unchanged": True, "provider_requests": 0,
        "native_horizon_contract_verified": True, "private_comparison_active": True}
    flags = producer.completion_flags(result)
    assert flags["execution_complete"] is True
    assert flags["horizon_complete"] is flags["complete"] is flags["acceptance_complete"] is False


@pytest.mark.parametrize("attack", ["marker", "source", "receipt", "current"])
def test_private_candidate_ack_rejects_changed_marker_source_or_receipt(tmp_path, monkeypatch, attack):
    attempt, live, archive = _private_ack_fixture(tmp_path)
    generation, _, results = _capture_unit_cut(attempt, live, archive, 1)
    owner = attempt.native_ack_owner(live, archive, archive_format="COMPONENT_V3")
    receipt = results["V4"]["receipt"]
    if attack == "marker":
        attempt.marker.write_bytes(b"{}")
    elif attack == "source":
        (generation / "status.json").write_bytes(b"{}")
    elif attack == "current":
        (live / "CURRENT.json").write_bytes(b"{}")
    else:
        path = archive / (receipt["generation_id"] + ".receipt.json")
        path.write_bytes(components.canonical(dict(receipt, durable=False)))
    monkeypatch.setattr(components.ComponentArchive, "build", lambda *args: pytest.fail("INVALID_NATIVE_FALLBACK_FORBIDDEN"))
    with pytest.raises((ValueError, KeyError)):
        owner.archive_generation(generation)
    assert not (live / ("archive-ack-" + receipt["generation_id"] + ".json")).exists()


def test_authenticated_case_names_bind_real_parameterized_nodeids_without_inventing_test_execution():
    prefix = "tests/test_rc6_cas_original_comparison.py::"
    name = "test_private_candidate_ack_rejects_changed_marker_source_or_receipt"
    items = [{"name": name + "[" + value + "]", "nodeid": prefix + name + "[" + value + "]"}
             for value in ("marker", "source", "receipt", "current")]
    assert comparison._executed_case_names(items, prefix) == {name}
    assert comparison._executed_case_names(items, "tests/other.py::") == set()
    with pytest.raises(ValueError, match="ACTUAL_EXECUTION_CASE_ID_CHANGED"):
        comparison._executed_case_names([{**items[0], "name": "different_test[marker]"}], prefix)


def test_unreviewed_attempt_cannot_bind_native_archive_from_environment_optin(tmp_path, monkeypatch):
    monkeypatch.setenv("RC6_ENABLE_SLICE_WRITE", "true")
    monkeypatch.setenv("RC6_PRIVATE_CANDIDATE_ACK", "true")
    attempt = comparison.OriginalComparison(tmp_path / "attempt", {"scope": "UNIT_API_ONLY_NO_MATERIAL_AUTHORITY"}, ROOT)
    with pytest.raises(ValueError, match="AUTHENTICATED_PRIVATE_ACK_REVIEW_REQUIRED"):
        attempt.bind_native_archive(tmp_path / "source-live", tmp_path / "source-archive")
    assert components.NATIVE_WRITE_RECIPE_SCHEMA == components.RECIPE_SCHEMA
