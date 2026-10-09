"""Cheap real-Git/ZIP counterexamples; no native quota or RC6 GREEN claim."""
import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import zipfile

import pytest

from scripts import rc6_capacity_comparison as comparison
from scripts import rc6_heavy_test_preflight as preflight
from scripts import rc6_material_pr_admission as admission


def git(repo, *arguments):
    return subprocess.run(["git", "--no-replace-objects", "-C", str(repo), *arguments], check=True,
                          capture_output=True, env={**os.environ, "GIT_NO_LAZY_FETCH": "1"}).stdout


def commit(repo):
    git(repo, "add", ".")
    git(repo, "-c", "user.name=RC6 proof unit", "-c", "user.email=unit@example.invalid",
        "commit", "-q", "--no-gpg-sign", "-m", "Controlled cheap proof fixture")
    return git(repo, "rev-parse", "HEAD").decode().strip(), git(repo, "rev-parse", "HEAD^{tree}").decode().strip()


def uri(sha, member):
    return "https://github.com/" + comparison.REPOSITORY + "/blob/" + sha + "/" + member


class LocalAuthenticatedApi:
    """The injected transport serves real immutable Git objects, not receipts."""
    def __init__(self, repo):
        self.repo, self.calls = repo, []

    def __call__(self, path):
        self.calls.append(path)
        _, _, kind, sha = path.split("/")
        if kind == "commits":
            raw = git(self.repo, "cat-file", "commit", sha)
            assert hashlib.sha1(b"commit " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == sha
            return {"sha": sha, "tree": {"sha": raw.splitlines()[0].split()[1].decode()}}
        if kind == "trees":
            rows = []
            for raw in git(self.repo, "ls-tree", "-z", sha).rstrip(b"\0").split(b"\0"):
                header, name = raw.split(b"\t")
                mode, member_type, oid = header.decode().split()
                rows.append({"path": name.decode(), "mode": mode, "type": member_type, "sha": oid})
            return {"sha": sha, "truncated": False, "tree": rows}
        assert kind == "blobs"
        raw = git(self.repo, "cat-file", "blob", sha)
        return {"sha": sha, "size": len(raw), "encoding": "base64", "content": base64.b64encode(raw).decode()}


def pack(raw, binding, tmp_path):
    original = tmp_path / "original-measurement.json"
    original.write_bytes(raw)
    details = original.stat()
    fields = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size", "st_blocks",
              "st_atime_ns", "st_mtime_ns", "st_ctime_ns")
    member = "raw/measurement.json"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
        entry = zipfile.ZipInfo(member, date_time=(1980, 1, 1, 0, 0, 0))
        entry.create_system = 3
        entry.external_attr = (stat.S_IFREG | 0o600) << 16
        archive.writestr(entry, raw)
    container = stream.getvalue()
    index = {"schema": "rc6.phase-diagnostics-lossless-pack.v1", "binding": binding,
        "member_count": 1, "uncompressed_bytes": len(raw), "archive_sha256": comparison.digest(container),
        "members": [{"path": member, "bytes": len(raw), "sha256": comparison.digest(raw),
                     "original_all11": {key: getattr(details, key) for key in fields}}]}
    return container, comparison.canonical(index)


def payload_cost(inventory):
    return sum((row["bytes"] + 4095) // 4096 * 4096 for row in inventory["files"])


def graph(inventory, producer="proof-only"):
    amount = payload_cost(inventory)
    return {"schema": comparison.GRAPH_SCHEMA, "source_sha": inventory["source_sha"],
        "source_tree": inventory["source_tree"], "producer": producer,
        "scope": "DIAGNOSTIC_IMMUTABLE_PAYLOADS_ONLY", "unknown_components": [],
        "premature_cleanup_credit_bytes": 0,
        "allocation_model": {"filesystem_type": "ext4", "allocation_unit_bytes": 4096,
                             "metric": "REGULAR_PAYLOAD_CEIL4096_PLUS_SEPARATELY_BOUNDED_COSTS"},
        "nodes": [{"name": "source-payloads", "model": "EXACT_FROZEN_SOURCE_REGULAR_PAYLOAD",
            "source_manifest_sha256": comparison.digest(comparison.canonical(inventory)), "quantity": 1,
            "unit_bound_bytes": amount, "allocated_bound_bytes": amount}]}


@pytest.fixture
def proof(tmp_path):
    repo = tmp_path / "git"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "fixed.bin").write_bytes(b"fixed payload\n")
    old_sha, old_tree = commit(repo)
    old = admission.frozen_source_inventory(repo, old_sha, old_tree)
    (repo / "added.bin").write_bytes(b"additional payload\n")
    sha, tree = commit(repo)
    source = admission.frozen_source_inventory(repo, sha, tree)
    # This is an actual observed sum of regular payload allocations only. Its
    # original scope and DIAGNOSTIC origin are retained; it cannot qualify RC6.
    observed = tmp_path / "closed-observed-components"
    observed.mkdir()
    for row in old["files"]:
        path = observed / row["path"]
        path.write_bytes(git(repo, "cat-file", "blob", row["git_blob"]))
        with path.open("rb") as stream:
            os.fsync(stream.fileno())
    allocated = sum(path.stat().st_blocks * 512 for path in observed.iterdir())
    assert allocated >= payload_cost(old)
    measurement = {"allocated_bytes": allocated, "retained_entries": len(old["files"]),
        "origin_runner_class": "DIAGNOSTIC", "scope": "OWN_CLOSED_RETAINED_NAMESPACE_OR_OBSERVED_COMPONENTS",
        "assertion_scope": "ACTUAL_CHEAP_IMMUTABLE_REGULAR_PAYLOAD_COMPONENTS_ONLY"}
    container, index = pack(comparison.canonical(measurement),
                            {"candidate_sha": old_sha, "candidate_tree": old_tree}, tmp_path)
    (repo / "measurement.raw").write_bytes(container)
    (repo / "measurement.index.json").write_bytes(index)
    old_graph, new_graph = graph(old), graph(source)
    (repo / "reference.graph.json").write_bytes(comparison.canonical(old_graph))
    (repo / "target.graph.json").write_bytes(comparison.canonical(new_graph))
    evidence_sha, _ = commit(repo)
    def ref(name):
        raw = (repo / name).read_bytes()
        return {"uri": uri(evidence_sha, name), "sha256": comparison.digest(raw)}
    cheap = ["tests/test_rc6_capacity_comparison.py"]
    source_hash = comparison.digest(comparison.canonical(source))
    cheap_hash = comparison.digest(comparison.canonical(cheap))
    delta = comparison.source_delta(old, source)
    extra = payload_cost(source) - payload_cost(old)
    peak = {"schema": preflight.ENVELOPE_SCHEMA,
        "target": {"producer": "proof-only", "runner_class": "DIAGNOSTIC", "workload_fingerprint": "1" * 64},
        "four_GiB_residual_reserve_excluded_from_envelope": True,
        "local_measurement_relabelled_as_target_runner": False,
        "reference_retention_GREEN_claimed": False, "temporal_peak_guarantee_claimed": False,
        "reference": {"measurement_kind": "OBSERVED_ALLOCATED_HIGH_WATER",
            "measurement_scope": measurement["scope"], "measurement_complete": True,
            "runner_class": "DIAGNOSTIC", "producer": "actual-cheap-components", "source_sha": old_sha,
            "source_tree": old_tree, "allocated_bytes": allocated, "retained_entries": len(old["files"]),
            "evidence": {"uri": ref("measurement.raw")["uri"], "container_sha256": comparison.digest(container),
                "index": ref("measurement.index.json"), "raw_member": "raw/measurement.json",
                "raw_member_sha256": comparison.digest(comparison.canonical(measurement)),
                "measurement": measurement, "measurement_sha256": preflight.digest(measurement)}},
        "comparison": {"schema": "porota.rc6.capacity-comparison-proof.v1", "candidate_sha": sha,
            "candidate_tree": tree, "source_manifest_sha256": source_hash, "cheap_files_sha256": cheap_hash,
            "producer_graph_sha256": ref("target.graph.json")["sha256"],
            "reference_graph_evidence": ref("reference.graph.json"), "target_graph_evidence": ref("target.graph.json"),
            "source_delta": delta, "source_delta_sha256": comparison.digest(comparison.canonical(delta)),
            "dominance": "REFERENCE_GRAPH_PLUS_ENUMERATED_BOUNDED_ADDITIONS",
            "checks": {key: True for key in preflight.COMPARISON_CHECKS}, "unknown_components": [],
            "additional_bound_components": [{"name": "source-payloads", "quantity": 1, "unit_bound_bytes": extra,
                "allocated_bound_bytes": extra, "model": "CEIL4096_REGULAR_PAYLOAD_OR_EXACT_CODE_WRITE_BOUND",
                "assumptions": ["DIAGNOSTIC_EXACT_REGULAR_PAYLOADS_ONLY"], "evidence": ref("target.graph.json")}]},
        "admission_envelope_bytes": allocated + extra}
    records = []
    for name in ("reference.graph.json", "target.graph.json"):
        records.append({**ref(name), "raw_utf8": (repo / name).read_text()})
    manifest = {"schema": admission.CAPACITY_COMPARISON_MANIFEST_SCHEMA, "status": "VERIFIED", "unknown_components": [],
        "source_sha": sha, "source_tree": tree, "source_manifest_sha256": source_hash,
        "cheap_files_sha256": cheap_hash, "records": records}
    return {"repo": repo, "source": source, "old": old, "manifest": manifest, "peaks": {"proof-only": peak},
            "cheap": cheap, "api": LocalAuthenticatedApi(repo), "evidence_sha": evidence_sha}


def verify(value):
    return comparison.verify_comparison_proof(value["manifest"], value["peaks"], value["source"],
        value["cheap"], repo=value["repo"], get=value["api"])


def test_authentic_diagnostic_payload_proof_cannot_be_used_as_rc6_admission(proof,monkeypatch):
    from scripts import rc6_architectural_gates as gates
    # Keep this explicitly tiny corpus a diagnostic input; even after real
    # Git/ZIP/index/bounds authentication it must fail the RC6 scope predicate.
    monkeypatch.setattr(gates,'validate_g1_files',lambda names:names)
    monkeypatch.setattr(comparison,'authenticated_get',proof['api'])
    fields={'CAPACITY_PEAKS_JSON':json.dumps(proof['peaks']),
        'CAPACITY_COMPARISON_MANIFEST_JSON':json.dumps(proof['manifest']),
        'SOURCE_MANIFEST_SHA256':proof['manifest']['source_manifest_sha256'],
        'CHEAP_FILES_JSON':json.dumps(proof['cheap'])}
    with pytest.raises(ValueError,match='CAPACITY_DIAGNOSTIC_PAYLOADS_NOT_RC6_ADMISSION'):
        admission.verify_capacity_comparison_manifest(fields,proof['source']['source_sha'],
            proof['source']['source_tree'],repo=proof['repo'])


def test_real_git_container_index_crc_and_measured_component_bounds_have_positive_proof(proof):
    original = copy.deepcopy(proof["peaks"])
    result = verify(proof)
    assert result["status"] == "AUTHENTICATED_PROOF_BYTES_VERIFIED"
    assert result["scope"] == "DIAGNOSTIC_PAYLOAD_BOUND_NO_RC6_ELIGIBILITY"
    assert result["residual_reserve_bytes"] == 4 * 1024**3
    assert result["minimum_free_inode_ratio"] == 0.1 and result["live_filesystem_recheck_required"] is True
    assert result["RC6_eligibility_claimed"] is result["G0_GREEN_claimed"] is result["launch_authorized"] is False
    assert result["native_quota_or_privileged_custody_proved"] is False
    assert proof["peaks"] == original
    assert result["producers"]["proof-only"]["reference_runner_class"] == "DIAGNOSTIC"
    assert result["producers"]["proof-only"]["recomputed_additional_bytes"] == 4096
    assert proof["api"].calls


def test_frozen_source_ignores_dirty_workspace_but_rejects_substituted_exact_inventory(proof):
    (proof["repo"] / "fixed.bin").write_bytes(b"DIRTY_UNCOMMITTED_WORKSPACE")
    assert verify(proof)["source_sha"] == proof["source"]["source_sha"]
    proof["source"]["files"][0]["bytes"] += 1
    with pytest.raises(ValueError, match="EXACT_GIT_SOURCE_MANIFEST_DIGEST_MISMATCH"):
        verify(proof)


def test_unknown_comparison_blocks_before_authenticated_api_or_payload_read(proof):
    proof["manifest"]["unknown_components"] = ["sdist-backend-temporaries"]
    with pytest.raises(ValueError, match="UNKNOWN_COMPONENTS_BEFORE_BOOTSTRAP"):
        verify(proof)
    assert proof["api"].calls == []


def test_hash_consistent_inline_graph_does_not_replace_original_git_bytes(proof):
    row = proof["manifest"]["records"][0]
    row["raw_utf8"] += "\n"
    row["sha256"] = comparison.digest(row["raw_utf8"].encode())
    with pytest.raises(ValueError, match="AUTHENTICATED_ORIGINAL_RAW_DIGEST_MISMATCH"):
        verify(proof)


@pytest.mark.parametrize("fault", ["tree", "blob", "truncated", "duplicate"])
def test_git_transport_metadata_cannot_override_cryptographic_tree_and_blob_identity(proof, fault):
    original = proof["api"]
    def corrupt(path):
        value = original(path)
        if "/trees/" in path:
            if fault == "tree":
                value["tree"][0]["sha"] = "a" * 40
            if fault == "truncated":
                value["truncated"] = True
            if fault == "duplicate":
                value["tree"].append(copy.deepcopy(value["tree"][0]))
        elif "/blobs/" in path and fault == "blob":
            raw = bytearray(base64.b64decode(value["content"]))
            raw[0] ^= 1
            value["content"] = base64.b64encode(raw).decode()
        return value
    proof["api"] = corrupt
    with pytest.raises(ValueError, match="CAPACITY_ORIGINAL_GIT"):
        verify(proof)


@pytest.mark.parametrize("fault", ["container", "index", "member", "measurement", "origin", "count"])
def test_original_container_index_member_measurement_and_origin_are_independent(proof, fault):
    peak = proof["peaks"]["proof-only"]
    evidence = peak["reference"]["evidence"]
    if fault == "container":
        evidence["container_sha256"] = "0" * 64
    elif fault == "index":
        evidence["index"]["sha256"] = "0" * 64
    elif fault == "member":
        evidence["raw_member"] = "raw/foreign.json"
    elif fault == "measurement":
        evidence["measurement"]["assertion_scope"] = "FAKE_COMPLETE_NAMESPACE"
        evidence["measurement_sha256"] = preflight.digest(evidence["measurement"])
    elif fault == "origin":
        evidence["measurement"]["origin_runner_class"] = peak["reference"]["runner_class"] = "LOCAL_MANAGED_WORKSPACE"
        evidence["measurement_sha256"] = preflight.digest(evidence["measurement"])
    else:
        evidence["measurement"]["retained_entries"] = peak["reference"]["retained_entries"] = 100000
        evidence["measurement_sha256"] = preflight.digest(evidence["measurement"])
    with pytest.raises(ValueError, match="CAPACITY_.*(?:ORIGINAL|CONTAINER)"):
        verify(proof)


def test_incomplete_source_delta_cannot_be_hidden_by_true_checks(proof):
    value = proof["peaks"]["proof-only"]["comparison"]
    value["source_delta"]["added"] = []
    value["source_delta_sha256"] = comparison.digest(comparison.canonical(value["source_delta"]))
    with pytest.raises(ValueError, match="EXACT_COMPLETE_SOURCE_DELTA_REQUIRED"):
        verify(proof)


def test_self_declared_extra_cost_arithmetic_cannot_replace_recomputed_graph(proof):
    peak = proof["peaks"]["proof-only"]
    row = peak["comparison"]["additional_bound_components"][0]
    row["unit_bound_bytes"] = row["allocated_bound_bytes"] = 8192
    peak["admission_envelope_bytes"] += 4096
    with pytest.raises(ValueError, match="DECLARED_ADDITION_NOT_AUTHENTIC_GRAPH_DELTA"):
        verify(proof)


@pytest.mark.parametrize("producer", sorted(comparison.CANONICAL_PRODUCERS))
def test_diagnostic_leaf_graph_cannot_qualify_any_original_rc6_stage(proof, producer):
    value = graph(proof["source"], producer)
    with pytest.raises(ValueError, match="BOOTSTRAP_OR_PRODUCER_COST_GRAPH_INCOMPLETE"):
        comparison.recompute_costs(value, proof["source"], reader=comparison.GitEvidenceReader(proof["api"]))


def test_complete_names_and_hashes_cannot_certify_unbounded_sdist_and_recovery_costs(proof):
    value = graph(proof["source"], "bootstrap")
    value["scope"] = "ORIGINAL_RC6_STAGE_COMPLETE"
    amount = payload_cost(proof["source"])
    value["nodes"] = [{"name": name, "quantity": 1, "unit_bound_bytes": amount,
        "allocated_bound_bytes": amount, "model": "EXACT_FROZEN_SOURCE_REGULAR_PAYLOAD",
        "source_manifest_sha256": comparison.digest(comparison.canonical(proof["source"]))}
        for name in sorted(comparison.BOOTSTRAP_COSTS)]
    with pytest.raises(ValueError, match="UNPROVED_DYNAMIC_GRAPH_COSTS:.*sdist-build.*temporary"):
        comparison.recompute_costs(value, proof["source"], reader=comparison.GitEvidenceReader(proof["api"]))


@pytest.mark.parametrize("uri_value", ["file:///tmp/raw", "https://example.invalid/raw",
    "https://github.com/mbalbo2023/Porota-trading/blob/main/raw.json",
    "https://github.com/mbalbo2023/Porota-trading/blob/" + "a" * 40 + "/../raw.json"])
def test_original_git_uri_must_be_exact_immutable_repository_without_aliases(uri_value):
    calls = []
    def forbidden(path):
        calls.append(path)
        raise AssertionError("Invalid URI must not reach transport")
    with pytest.raises(ValueError):
        comparison.GitEvidenceReader(forbidden).read(uri_value)
    assert calls == []


def test_duplicate_json_and_nonfinite_numbers_are_rejected():
    for raw in (b'{"bytes":1,"bytes":1}', b'{"bytes":NaN}', b'{"bytes":Infinity}'):
        with pytest.raises(ValueError, match="CAPACITY_DUPLICATE_JSON_KEY|CAPACITY_NONFINITE_JSON"):
            comparison.document(raw)


def test_canonical_targets_require_compound_bootstrap_before_any_api_read(proof):
    peak = proof["peaks"].pop("proof-only")
    peak["target"]["producer"] = "predeploy"
    proof["peaks"]["predeploy"] = peak
    with pytest.raises(ValueError, match="COMPOUND_BOOTSTRAP_ENVELOPE_REQUIRED"):
        verify(proof)
    assert proof["api"].calls == []


def test_git_member_symlink_is_rejected_even_with_authentic_tree_identity(proof):
    (proof["repo"] / "alias.json").symlink_to("target.graph.json")
    sha, _ = commit(proof["repo"])
    with pytest.raises(ValueError, match="ALIAS_OR_NONREGULAR_MEMBER"):
        comparison.GitEvidenceReader(proof["api"]).read(uri(sha, "alias.json"))


def test_cached_git_blob_cannot_bypass_a_smaller_consumer_read_bound(proof):
    reader = comparison.GitEvidenceReader(proof["api"])
    address = uri(proof["evidence_sha"], "target.graph.json")
    raw = reader.read(address)
    with pytest.raises(ValueError, match="BLOB_BOUND_OR_IDENTITY"):
        reader.read(address, maximum_bytes=len(raw) - 1)


def test_recovery_and_directory_costs_cannot_take_future_gc_credit(proof):
    value = graph(proof["source"])
    value["premature_cleanup_credit_bytes"] = 4096
    with pytest.raises(ValueError, match="PREMATURE_CLEANUP_CREDIT_BLOCKED"):
        comparison.recompute_costs(value, proof["source"], reader=comparison.GitEvidenceReader(proof["api"]))


@pytest.mark.parametrize("invalid", [True, False, 0, -1, float("nan"), "1"])
def test_quantity_requires_a_real_positive_integer_not_truthiness_or_a_forecast(proof, invalid):
    value = graph(proof["source"])
    value["nodes"][0]["quantity"] = invalid
    with pytest.raises(ValueError, match="INTEGER_BOUND_REQUIRED"):
        comparison.recompute_costs(value, proof["source"], reader=comparison.GitEvidenceReader(proof["api"]))


def test_authentic_self_declared_component_sum_is_recomputed_from_frozen_payload_bytes(proof):
    value = graph(proof["source"])
    value["nodes"][0]["unit_bound_bytes"] = value["nodes"][0]["allocated_bound_bytes"] = 4096
    with pytest.raises(ValueError, match="COMPONENT_BOUND_NOT_DERIVED_FROM_AUTHENTIC_BYTES"):
        comparison.recompute_costs(value, proof["source"], reader=comparison.GitEvidenceReader(proof["api"]))


def test_original_member_crc_is_checked_after_authenticating_new_container_and_index(proof):
    repo = proof["repo"]
    container = bytearray((repo / "measurement.raw").read_bytes())
    raw = comparison.canonical(proof["peaks"]["proof-only"]["reference"]["evidence"]["measurement"])
    position = container.index(raw)
    container[position] ^= 1
    index = json.loads((repo / "measurement.index.json").read_bytes())
    index["archive_sha256"] = comparison.digest(container)
    (repo / "measurement.raw").write_bytes(container)
    (repo / "measurement.index.json").write_bytes(comparison.canonical(index))
    sha, _ = commit(repo)
    evidence = proof["peaks"]["proof-only"]["reference"]["evidence"]
    evidence["uri"] = uri(sha, "measurement.raw")
    evidence["container_sha256"] = comparison.digest(container)
    evidence["index"] = {"uri": uri(sha, "measurement.index.json"),
                         "sha256": comparison.digest(comparison.canonical(index))}
    with pytest.raises(ValueError, match="CONTAINER_MEMBER_OR_CRC_INVALID"):
        verify(proof)


def test_original_index_cannot_be_rebound_to_a_different_immutable_commit(proof):
    (proof["repo"] / "new-note.txt").write_bytes(b"second evidence cut\n")
    sha, _ = commit(proof["repo"])
    proof["peaks"]["proof-only"]["reference"]["evidence"]["index"]["uri"] = uri(sha, "measurement.index.json")
    with pytest.raises(ValueError, match="CONTAINER_INDEX_COMMIT_REBOUND"):
        verify(proof)


def test_an_observed_closed_prefix_cannot_dominate_an_unproved_full_graph(proof):
    repo = proof["repo"]
    value = graph(proof["old"])
    value["nodes"][0]["quantity"] = 2
    value["nodes"][0]["allocated_bound_bytes"] *= 2
    raw = comparison.canonical(value)
    (repo / "reference.graph.json").write_bytes(raw)
    sha, _ = commit(repo)
    evidence = {"uri": uri(sha, "reference.graph.json"), "sha256": comparison.digest(raw)}
    proof["peaks"]["proof-only"]["comparison"]["reference_graph_evidence"] = evidence
    proof["manifest"]["records"][0] = {**evidence, "raw_utf8": raw.decode()}
    with pytest.raises(ValueError, match="OBSERVED_SAMPLE_DOES_NOT_DOMINATE_REFERENCE_GRAPH"):
        verify(proof)


def test_missing_original_pack_index_never_falls_back_to_measurement_metadata(proof):
    del proof["peaks"]["proof-only"]["reference"]["evidence"]["index"]
    with pytest.raises(ValueError, match="ORIGINAL_RAW_REFERENCE_REQUIRED"):
        verify(proof)


def test_remote_candidate_commit_must_authenticate_the_exact_frozen_source_tree(proof):
    transport = proof["api"]
    def rebound(path):
        value = transport(path)
        if path == "/git/commits/" + proof["source"]["source_sha"]:
            value["tree"]["sha"] = proof["old"]["source_tree"]
        return value
    proof["api"] = rebound
    with pytest.raises(ValueError, match="AUTHENTICATED_SOURCE_TREE_REBOUND"):
        verify(proof)


def test_component_evidence_must_cite_the_authentic_target_graph_not_a_random_git_member(proof):
    value = proof["peaks"]["proof-only"]["comparison"]
    value["additional_bound_components"][0]["evidence"] = value["reference_graph_evidence"].copy()
    with pytest.raises(ValueError, match="ADDITION_EVIDENCE_NOT_AUTHENTIC_TARGET_GRAPH"):
        verify(proof)


@pytest.mark.parametrize("path", ["/actions/runs/1", "/git/blobs/" + "a" * 40 + "?scope=foreign",
                                  "https://foreign.invalid/git/blobs/" + "a" * 40])
def test_authenticated_transport_rejects_any_nonreadonly_git_api_scope_before_credentials(path):
    with pytest.raises(ValueError, match="AUTHENTICATED_API_SCOPE_REQUIRED"):
        comparison.authenticated_get(path)


def test_authenticated_transport_never_forwards_credentials_through_redirect():
    with pytest.raises(ValueError, match="AUTHENTICATED_API_REDIRECT_FORBIDDEN"):
        comparison._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://foreign.invalid/raw")


def test_predeploy_graph_requires_docker_and_artifact_costs_as_well_as_bootstrap():
    expected = comparison.required_costs("predeploy")
    assert comparison.BOOTSTRAP_COSTS <= expected and comparison.PRODUCER_COSTS <= expected
    assert {"docker-daemon-build-layers", "artifact-export", "artifact-tests", "artifact-custody"} <= expected


def storage(**overrides):
    arguments = {"free_bytes": 14 * 1024**3, "nominal_storage_bytes": 14 * 1024**3,
                 "total_inodes": 1000, "free_inodes": 100, "allocation_unit_bytes": 4096}
    return comparison.evaluate_bootstrap_storage(**{**arguments, **overrides})


def test_bootstrap_26_gib_backing_4_gib_reserve_and_controls_cannot_fit_the_14_gib_floor():
    from scripts.rc6_capacity_calibration import limits_for, outer_control_peak_bound
    actual = storage()
    assert actual["backing_image_bytes"] == limits_for("bootstrap")["backing_image_bytes"] == 26 * 1024**3
    assert actual["project_hard_limit_bytes"] == 20 * 1024**3
    assert actual["residual_reserve_bytes"] == 4 * 1024**3
    assert actual["required_minimum_bytes"] == 30 * 1024**3 + outer_control_peak_bound(4096)["total_bytes"]
    assert actual["required_minimum_bytes"] > 30 * 1024**3
    assert actual["status"] == "BLOCKED_NECESSARY_STORAGE"
    assert actual["blockers"] == ["CAPACITY_BOOTSTRAP_BACKING_RESERVE_CONTROLS_INSUFFICIENT"]


def test_one_runner_observed_with_extra_space_never_upgrades_platform_guarantee_or_quota_custody():
    actual = storage(free_bytes=91_698_458_624)
    assert actual["status"] == "NECESSARY_STORAGE_ONLY" and actual["blockers"] == []
    assert actual["nominal_storage_guarantees_necessary_floor"] is False
    assert actual["full_compound_peak_proved"] is actual["G0_GREEN_claimed"] is actual["launch_authorized"] is False
    assert actual["native_quota_or_privileged_custody_proved"] is False


def test_bootstrap_necessary_byte_boundary_is_exact_and_does_not_spend_the_reserve():
    minimum = storage()["required_minimum_bytes"]
    for delta in (-1, 0, 1):
        actual = storage(free_bytes=minimum + delta)
        assert actual["status"] == ("BLOCKED_NECESSARY_STORAGE" if delta < 0 else "NECESSARY_STORAGE_ONLY")
        assert actual["residual_reserve_bytes"] == 4 * 1024**3
        assert actual["launch_authorized"] is False


def test_bootstrap_inode_reserve_remains_independent_of_abundant_bytes():
    for free_inodes in (99, 100):
        actual = storage(free_bytes=100 * 1024**3, free_inodes=free_inodes)
        assert actual["blockers"] == (["CAPACITY_INODES_INSUFFICIENT"] if free_inodes == 99 else [])
        assert actual["minimum_free_inode_ratio"] == 0.1


def test_project_hard_cap_is_never_substituted_for_the_larger_physical_backing():
    from scripts.rc6_capacity_calibration import limits_for, outer_control_peak_bound
    limits = limits_for("bootstrap")
    wrong_floor = limits["project_hard_limit_bytes"] + limits["residual_reserve_bytes"] + outer_control_peak_bound(4096)["total_bytes"]
    actual = storage(free_bytes=wrong_floor)
    assert actual["status"] == "BLOCKED_NECESSARY_STORAGE"
    assert actual["required_minimum_bytes"] - wrong_floor == 6 * 1024**3


def test_private_admission_hook_reuses_fresh_target_inventory_without_a_second_source_hash(proof, monkeypatch):
    original = admission.frozen_source_inventory
    calls = []
    def observed(repo, sha, tree):
        calls.append((sha, tree))
        return original(repo, sha, tree)
    monkeypatch.setattr(admission, "frozen_source_inventory", observed)
    result = comparison._verify_with_fresh_inventory(proof["manifest"], proof["peaks"], proof["source"],
        proof["cheap"], repo=proof["repo"], get=proof["api"])
    assert result["RC6_eligibility_claimed"] is False
    assert calls == [(proof["old"]["source_sha"], proof["old"]["source_tree"])]


def test_public_proof_wrapper_has_no_inventory_authentication_bypass_flag():
    import inspect
    assert set(inspect.signature(comparison.verify_comparison_proof).parameters) == {
        "manifest", "peaks", "inventory", "cheap", "repo", "get"}


def stub_observation(monkeypatch):
    from scripts import rc6_capacity_calibration as calibration
    measured = {"device": 77, "mount_id": 22, "fragment_bytes": 4096,
        "available_bytes": 41 * 1024**3, "total_bytes": 100 * 1024**3,
        "total_inodes": 1000, "free_inodes": 150, "measured_monotonic_ns": 123456}
    live = {"filesystem_device": 77, "mount_id": 22, "allocation_unit_bytes": 4096,
        "filesystem_type": "ext4", "free_bytes": 40 * 1024**3, "total_inodes": 1000, "free_inodes": 140}
    kernel = {"schema": "porota.rc6.readonly-kernel-quota-prerequisites.v1",
        "status": "PREREQUISITES_PRESENT", "actual_capability_proved": False,
        "module_loading_attempted": False, "qualification_claimed": False, "kernel_unsupported_claimed": False}
    calls = []
    def measure(path):
        calls.append(("filesystem", str(path)))
        return measured.copy()
    def observe(mode):
        calls.append(("kernel", mode))
        return kernel.copy()
    monkeypatch.setattr(calibration, "filesystem", measure)
    monkeypatch.setattr(calibration, "kernel_prerequisites", observe)
    monkeypatch.setattr(preflight, "measure_filesystem", lambda _path: live.copy())
    monkeypatch.setattr(preflight, "machine_snapshot", lambda: {"cpu_count": 4, "MemTotal_kib": 16 * 1024**2})
    return calibration, calls, measured, live, kernel


def test_readonly_runner_observation_preserves_raw_measurements_and_never_starts_an_actor(tmp_path, monkeypatch):
    calibration, calls, measured, live, kernel = stub_observation(monkeypatch)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Readonly metadata must never launch an actor")
    monkeypatch.setattr(calibration.subprocess, "run", forbidden)
    monkeypatch.setattr(calibration.subprocess, "Popen", forbidden)
    actual = comparison.readonly_runner_observation(tmp_path)
    assert actual["filesystem"] == measured and actual["live_filesystem"] == live
    assert actual["kernel_prerequisites"] == kernel
    assert calls == [("filesystem", str(tmp_path)), ("kernel", "bootstrap")]
    assert actual["storage_floor"]["measured_free_bytes"] == live["free_bytes"]
    assert actual["storage_floor"]["nominal_storage_guarantees_necessary_floor"] is False
    assert actual["status"] == "READ_ONLY_OBSERVATIONS_RECORDED"
    assert set(actual["generation_operations"].values()) == {"NOT_CALLED"}
    assert actual["G0_status"] == "BLOQUEADO" and actual["G0_GREEN_claimed"] is False
    assert actual["actual_capability_proved"] is actual["launch_authorized"] is False
    assert actual["privileged_signal_custody"] == dict(calibration.PRIVILEGED_SIGNAL_CUSTODY)


def test_failed_kernel_metadata_read_is_unknown_and_never_universal_incompatibility(tmp_path, monkeypatch):
    calibration, *_ = stub_observation(monkeypatch)
    def unavailable(_mode):
        raise PermissionError(13, "Controlled kernel configuration read denied")
    monkeypatch.setattr(calibration, "kernel_prerequisites", unavailable)
    actual = comparison.readonly_runner_observation(tmp_path)
    assert actual["kernel_prerequisites"]["status"] == "NO_VERIFICADO"
    assert actual["kernel_prerequisites"]["observer_error"]["errno"] == 13
    assert actual["kernel_unsupported_claimed"] is actual["kernel_prerequisites"]["kernel_unsupported_claimed"] is False
    assert actual["platform_compatibility_proved"] is False
    assert actual["status"] == "READ_ONLY_OBSERVATIONS_WITH_UNKNOWNS"


@pytest.mark.parametrize("claim", ["actual_capability_proved", "module_loading_attempted",
                                   "qualification_claimed", "kernel_unsupported_claimed"])
def test_readonly_metadata_cannot_be_relabelled_as_native_proof_or_kernel_incompatibility(tmp_path, monkeypatch, claim):
    _, _, _, _, kernel = stub_observation(monkeypatch)
    kernel[claim] = True
    actual = comparison.readonly_runner_observation(tmp_path)
    assert actual["kernel_prerequisites"]["status"] == "NO_VERIFICADO"
    assert actual["kernel_prerequisites"][claim] is False
    assert actual["G0_GREEN_claimed"] is False


def test_missing_filesystem_observation_never_fabricates_a_zero_or_successful_capacity(tmp_path, monkeypatch):
    calibration, *_ = stub_observation(monkeypatch)
    def missing(_path):
        raise FileNotFoundError(2, "Controlled missing measured path")
    monkeypatch.setattr(calibration, "filesystem", missing)
    actual = comparison.readonly_runner_observation(tmp_path)
    assert actual["filesystem"] is actual["storage_floor"] is None
    assert actual["observer_errors"][0]["component"] == "filesystem_and_storage"
    assert actual["kernel_prerequisites"]["status"] == "PREREQUISITES_PRESENT"
    assert actual["G0_GREEN_claimed"] is actual["launch_authorized"] is False


def test_filesystem_device_or_mount_rebound_during_observation_blocks_storage_formula(tmp_path, monkeypatch):
    _, _, _, live, _ = stub_observation(monkeypatch)
    live["mount_id"] += 1
    actual = comparison.readonly_runner_observation(tmp_path)
    assert actual["filesystem"] and actual["live_filesystem"]
    assert actual["storage_floor"] is None
    assert actual["observer_errors"][0]["reason"] == "CAPACITY_READONLY_FILESYSTEM_REBOUND_DURING_OBSERVATION"


def test_readonly_observation_reports_machine_probe_failure_as_unknown(tmp_path, monkeypatch):
    stub_observation(monkeypatch)
    def unavailable():
        raise OSError(13, "Controlled proc metadata denied")
    monkeypatch.setattr(preflight, "machine_snapshot", unavailable)
    actual = comparison.readonly_runner_observation(tmp_path)
    assert actual["machine"]["status"] == "NO_VERIFICADO" and actual["machine"]["observer_error"]["errno"] == 13
    assert actual["runtime_validated"] is False
