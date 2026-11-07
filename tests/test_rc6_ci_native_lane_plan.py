"""A764: lossless native fanout policy and exact-image join contract OFFHOST."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from scripts.rc6_ci_parallel_runner import (
    ParallelRunnerRed,
    verify_native_lane_policy,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "ops/policy/test-policy.yaml"
WORKFLOW = ROOT / ".github/workflows/porota-predeploy-v2.yml"


def policy() -> dict:
    return yaml.safe_load(POLICY.read_text(encoding="utf-8"))


def test_a764_native_dag_has_one_ordered_cadence_dependency():
    proof = verify_native_lane_policy(policy())
    assert proof["independent_lanes"] == ("cases", "catalog", "session")
    assert proof["ordered_lanes"] == ("cadence-fresh", "cadence-legacy")
    assert proof["dependencies"] == (("cadence-fresh", "cadence-legacy"),)
    assert proof["automatic_go"] is False
    assert proof["droplet_access"] is False


@pytest.mark.parametrize("field,replacement", [
    ("independent_lanes", ["session", "catalog"]),
    ("ordered_lanes", ["cadence-legacy", "cadence-fresh"]),
    ("dependencies", []),
    ("source_identity", "head_only"),
    ("mandatory_receipts", "partial"),
    ("scope", "PRODUCTIVE"),
    ("automatic_go", True),
    ("droplet_access", True),
])
def test_a764_dag_policy_mutation_fail_closed(field, replacement):
    variant = deepcopy(policy())
    variant["ci_parallel_experiment"]["native_proofs"][field] = replacement
    with pytest.raises(ParallelRunnerRed, match="RC6_PARALLEL_RUNNER_RED"):
        verify_native_lane_policy(variant)


def test_a764_workflow_retains_required_frozen_native_proofs():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["artifact-gate"]["steps"]
    native = next(x["run"] for x in steps
                  if x["name"] == "Exact image native sizing and generated catalog proof")
    assert native.count("run_native_proof cases python") == 1
    assert native.count("run_native_proof catalog python") == 1
    assert native.count("run_native_proof session python") == 1
    assert native.count("run_native_proof cadence-fresh python") == 1
    assert native.count("run_native_proof cadence-legacy python") == 1
    assert native.index("run_native_proof cadence-fresh python") < native.index(
        "run_native_proof cadence-legacy python")
    assert native.count('wait "$rc6_pid"') >= 2
    assert "test \"$(git rev-parse HEAD)\" = \"$CANDIDATE_SHA\"" in native
    assert "test \"$(git rev-parse 'HEAD^{tree}')\" = \"$CANDIDATE_TREE_SHA\"" in native
    assert "rc6_image_id_start" in native
    gate = next(x["run"] for x in steps
                if x["name"] == "Verify required published native evidence")
    assert "dashboard_auth_52.xml" in gate
    assert "native-bound.xml" in gate
    assert "cadence-fresh.json" in gate
    assert "cadence-legacy.json" in gate


def test_a764_no_new_pipeline_or_promotion_authority():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert list(workflow["jobs"]) == ["artifact-gate"]
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count("uses: docker/build-push-action@v7") == 1
    assert "DROPLET_TOUCHED=NO" in text
