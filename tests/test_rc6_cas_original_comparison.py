"""Economic native five-member controls, never the 1200/6000 material run."""
from datetime import datetime
import gzip
from pathlib import Path

import pytest

from rc6_shadow_runtime import archive_components as components
from scripts import rc6_cas_original_comparison as comparison
from scripts import rc6_material_horizon_producer as producer
from tests.test_rc6_component_archive import native, members, policy, tree_custody


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
