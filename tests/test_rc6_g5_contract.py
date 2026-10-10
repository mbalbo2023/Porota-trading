"""Cheap contract/capacity counterexamples, never original Horizon qualification."""
from copy import deepcopy
import errno
import os
from pathlib import Path
import copy
import struct
import os
import time
from types import SimpleNamespace

import pytest

from rc6_shadow_runtime import archive_components as components
from rc6_shadow_runtime import archive_format_contract as formats
from scripts import rc6_architectural_gates as gates
from scripts import rc6_cas_original_comparison as comparison
from scripts import rc6_g5_contract as g5
from scripts import rc6_g5b_evidence as g5b
from scripts import rc6_material_horizon_producer as producer
from scripts.rc6_material_carrier import workload_fingerprint
from tests.test_rc6_component_archive import members, native, policy


ROOT = Path(__file__).resolve().parents[1]


def binding():
    return {"candidate_sha": "a" * 40, "candidate_tree": "b" * 40,
        "producer": "Horizon-private-original-cas", "attempt_id": "UNIT_ONLY", "owner_id": "UNIT_ONLY",
        "runner_class": "github-hosted/ubuntu-24.04",
        "workload_fingerprint": workload_fingerprint("Horizon-private-original-cas")}


def snapshot(**changes):
    return {"hard_bytes": g5.CAP_BYTES, "hard_inodes": g5.CAP_INODES, "used_bytes": 4096,
            "used_inodes": 1, "allocation_unit": 4096, "quota_format_id": 4, **changes}


def costs(**changes):
    return {name: changes.get(name, 0) for name in g5.RESERVATION_CLASSES}


def test_g5a_proposal_keeps_exact_limits_and_no_early_green(tmp_path):
    request = g5.quota_setup_request(binding(), archive_path=tmp_path / "archive")
    assert request["hard_bytes"] == 512 * 1024**2 and request["hard_inodes"] == 32768
    assert request["required_phases"] == ["TrustedQuotaSetup", "Seal", "NONROOT", "Custody_FIN"]
    assert request["native_v4_write_enabled"] is request["g5_qualification_allowed"] is False
    assert g5.CONTRACT["original_cuts"] == 1202 and g5.CONTRACT["maximum_dependency_depth"] == 32
    assert g5.CONTRACT["contracted_retention_seconds"] == 9 * 3600
    assert g5.CONTRACT["recovery_margin_seconds"] == 3600
    assert g5.CONTRACT_SHA256 == components.sha(components.canonical(dict(g5.CONTRACT)))
    with pytest.raises(TypeError):
        g5.CONTRACT["acquisition_grants_g5_green"] = True


def geometry_bytes():
    """Unit parser input only; never evidence of an actual filesystem."""
    raw = bytearray(1024)
    struct.pack_into("<H", raw, 0x38, 0xEF53)
    for offset, value in ((0x18, 2), (0x1C, 2), (0x60, 0x40), (0x64, 0x2100)):
        struct.pack_into("<I", raw, offset, value)
    return bytes(raw)


def test_closed_write_envelope_is_per_actual_encoded_write_and_cannot_issue_native_authority():
    files = {name: (ROOT / name).read_bytes() for name in formats.SOURCE_MEMBERS}
    writer = formats.writer_codec_restorer_binding(files)
    geometry = g5.decode_native_ext4_geometry(geometry_bytes())
    envelope = g5.source_publication_envelope(writer, geometry, source_sha="a" * 40,
        source_tree="b" * 40, quota_format_id=4)
    assert g5.validate_publication_envelope(envelope, snapshot=snapshot(), source_sha="a" * 40,
        source_tree="b" * 40) == envelope
    assert envelope["future_gc_credit_bytes"] == 0 and envelope["g5_qualification_allowed"] is False
    assert envelope["fixed_simultaneous_physical_costs"]["publication_temporaries"] == 128 * 1024**2
    with pytest.raises(ValueError, match="G5A_AUTHENTIC_CURRENT"):
        g5.validate_g5a_authority(envelope, expected_binding=binding())


@pytest.mark.parametrize("offset,flag", ((0x5C, 1), (0x60, 0x8000), (0x60, 0x4000),
    (0x60, 0x10000), (0x60, 0x20000), (0x64, 0x200)))
def test_actual_filesystem_features_requiring_another_bound_are_discriminated(offset, flag):
    raw = bytearray(geometry_bytes())
    struct.pack_into("<I", raw, offset, struct.unpack_from("<I", raw, offset)[0] | flag)
    with pytest.raises(ValueError, match="G5A_CURRENT_EXT4_GEOMETRY_NEEDS_DISTINCT_BOUND"):
        g5.decode_native_ext4_geometry(bytes(raw))


@pytest.mark.parametrize("operation", (b"stream.seek(1); ", b"os.posix_fallocate(fd, 0, len(raw)); "))
def test_source_operation_inventory_does_not_silently_inherit_random_or_preallocated_writes(operation):
    files = {name: (ROOT / name).read_bytes() for name in formats.SOURCE_MEMBERS}
    files["rc6_shadow_runtime/archive_components.py"] = files["rc6_shadow_runtime/archive_components.py"].replace(
        b"stream.write(raw);", operation + b"stream.write(raw);", 1)
    with pytest.raises(ValueError, match="ARCHIVE_FORMAT_SEQUENTIAL_WRITER_NEW_OPERATION"):
        formats.writer_codec_restorer_binding(files)


def unit_codec_record():
    allocation = snapshot(physical_allocated_bytes_including_directory=4096, actual_member_count=0,
                          actual_allocation_inventory_sha256="c" * 64)
    events, states = [], {}
    for index, state in enumerate(sorted(g5.STATE_CLASSES)):
        reservation = g5.reserve_state(allocation, state=state, physical_costs=costs(), additional_inodes=0)
        events.append({"sequence": index, "state": state, "operation": "UNIT_CODEC_DATA_ONLY",
            "status": "ACTUAL_ENTERED_STATE", "current_allocation": allocation, "reservation": reservation})
        states[state] = reservation
    cut = {"cut_index": 0, "original_member_sha256": {name: "d" * 64 for name in components.MEMBERS},
        "states": states, "operation_events": events, "not_entered_states": []}
    project = {key: value for key, value in allocation.items() if key not in g5.LEDGER_ALLOCATION_FIELDS}
    operations = []
    compact = g5.compact_ledger_cut(cut, project=project, operations=operations)
    return {"codec": "rc6.g5a-lossless-state-columns.v2", "project_invariants": project,
        "allocation_fields": list(g5.LEDGER_ALLOCATION_FIELDS), "cost_fields": sorted(g5.RESERVATION_CLASSES),
        "state_fields": sorted(g5.STATE_CLASSES), "operations": operations, "cuts": [compact]}


def test_lossless_state_codec_roundtrips_math_without_minting_a_native_g5_receipt():
    encoded = unit_codec_record()
    expanded = g5.expand_ledger_capture(encoded)
    assert len(expanded["cuts"]) == 1 and set(expanded["cuts"][0]["states"]) == g5.STATE_CLASSES
    assert all(event["current_allocation"]["used_bytes"] == 4096 for event in expanded["cuts"][0]["operation_events"])
    with pytest.raises(ValueError, match="G5B_ALL1202_ORIGINAL_RESTORES"):
        g5.validate_complete_original_stream({}, quota_trace=expanded["cuts"], writer_binding={})


@pytest.mark.parametrize("tamper", ("budget", "state", "not_entered", "boolean", "projection"))
def test_lossless_actual_state_columns_reject_budget_state_and_observation_rebinding(tamper):
    encoded = unit_codec_record(); cut = encoded["cuts"][0]
    if tamper == "budget": cut["events"][0][4][0] = g5.CAP_BYTES
    elif tamper == "state": cut["states"][0][1] = 1
    elif tamper == "not_entered": cut["not_entered_states"] = [0]
    elif tamper == "boolean": cut["events"][0][5] = False
    else: cut["events"][0][6] += 4096
    with pytest.raises(ValueError, match="G5[AB]_"):
        g5.expand_ledger_capture(encoded)


@pytest.mark.parametrize("path", ("", "/issues/comments/123", "/issues/471/comments?since=2026-10-10T00%3A00%3A00Z&per_page=100&page=1",
    "/actions/runs/1/attempts/1/jobs?per_page=100", "/git/commits/" + "a" * 40,
    "/git/ref/heads/fix/rc6-architecture", "/pulls/483",
    "/contents/docs/automation/rc6-night-20261005/state.json?ref=ops%2Frc6-night-supervisor-20261005"))
def test_root_oracle_accepts_only_bounded_existing_repository_control_reads(path):
    assert g5._oracle_api_path(path) == path


@pytest.mark.parametrize("path", ("https://other.example/secret", "/issues/999/comments", "/contents/private-key",
    "/pulls/483?token=secret", "/git/ref/heads/../foreign", "/actions/artifacts/1/zip", "//other/issue"))
def test_root_oracle_does_not_admit_other_origins_payloads_or_secret_queries(path):
    with pytest.raises(ValueError, match="G5A_ROOT_ORACLE_"):
        g5._oracle_api_path(path)


@pytest.mark.parametrize("context", (None, {"ROOT": True}, True, SimpleNamespace(role="worker")))
def test_root_oracle_json_and_flags_cannot_create_native_reader_or_preparation(context):
    with pytest.raises(ValueError, match="G5A_AUTHENTIC_CURRENT_TRUSTED_API_CONTROLLER_REQUIRED"):
        g5.RootOwnerOracleReader.open({}, trusted_controller=context)


def test_actual_bounded_pipe_frame_io_is_transport_only_and_handles_expired_deadline():
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        value = {"scope": "UNIT_TRANSPORT_NOT_NATIVE_AUTHORITY", "count": 5}
        g5._oracle_write(write_fd, components.canonical(value), time.monotonic() + 1)
        assert g5._oracle_read(read_fd, time.monotonic() + 1) == value
        with pytest.raises(ValueError, match="G5A_ROOT_ORACLE_TOTAL_DEADLINE_EXCEEDED"):
            g5._oracle_read(read_fd, time.monotonic() - 1)
    finally:
        os.close(read_fd); os.close(write_fd)


@pytest.mark.parametrize("change", ({"producer": "Horizon"}, {"workload_fingerprint": "c" * 64},
                                   {"runner_class": "UNOBSERVED"}, {"candidate_sha": "synthetic"}))
def test_g5a_setup_rejects_wrong_source_workload_or_runner_before_any_write(tmp_path, change):
    with pytest.raises(ValueError, match="G5A_EXACT_"):
        g5.quota_setup_request({**binding(), **change}, archive_path=tmp_path / "never-created")
    assert not (tmp_path / "never-created").exists()


def test_current_physical_reservation_accepts_only_known_simultaneous_budget_without_g5_credit():
    result = g5.reserve_state(snapshot(), state="cas_staging",
        physical_costs=costs(new_whole_packs=16384, publication_temporaries=4096,
                             directory_and_filesystem_metadata=8192), additional_inodes=3)
    assert result["projected_bytes"] == 4096 + 16384 + 4096 + 8192
    assert result["projected_inodes"] == 4 and result["future_gc_credit_bytes"] == 0
    assert result["g5_qualification_allowed"] is False


@pytest.mark.parametrize("attack", ("unknown", "missing", "boolean", "future_gc", "bytes", "inodes"))
def test_unknown_cost_future_gc_credit_and_budget_red_stop_before_publication(attack):
    allocation, envelope, future, inodes = snapshot(), costs(new_whole_packs=4096), 0, 1
    if attack == "unknown": envelope["recovery_temporaries"] = None
    elif attack == "missing": envelope.pop("directory_and_filesystem_metadata")
    elif attack == "boolean": envelope["controls"] = False
    elif attack == "future_gc": future = 4096
    elif attack == "bytes": allocation["used_bytes"] = g5.CAP_BYTES - 1
    elif attack == "inodes": allocation["used_inodes"] = g5.CAP_INODES
    with pytest.raises(ValueError, match="G5A_"):
        g5.reserve_state(allocation, state="cas_staging", physical_costs=envelope,
                         additional_inodes=inodes, future_gc_credit_bytes=future)


@pytest.mark.parametrize("authority", ({"approved": True, "hard_bytes": g5.CAP_BYTES}, True,
                                       SimpleNamespace(status="GREEN"), None))
def test_serialized_g5a_cannot_replace_the_hold_or_reach_a_producer(authority, tmp_path):
    if authority is None:
        reason = "HORIZON_ALL1202_PHYSICAL_MODEL_NO_VERIFICADO"
    else:
        reason = "G5A_AUTHENTIC_CURRENT_SCOPED_ADMISSION_REQUIRED"
    with pytest.raises(ValueError, match=reason):
        comparison.authenticate_comparison(tmp_path / "unread.json", source_sha="a" * 40,
            source_tree="b" * 40, source_root=ROOT, output_root=tmp_path / "no-output",
            producer_root=tmp_path / "no-producer", g5a=authority, expected_binding=binding())
    assert not (tmp_path / "no-output").exists() and not (tmp_path / "no-producer").exists()


def test_owner_acceptance_is_independent_of_live_quota_custody_and_all_prior_gates(tmp_path, monkeypatch):
    from scripts import rc6_contract_revisions as revisions
    from scripts import rc6_privileged_custody as custody
    monkeypatch.setattr(gates, "verify_manifest", lambda *args, **kwargs: pytest.fail("No accepted contract"))
    monkeypatch.setattr(custody, "verify_live_controller", lambda *args, **kwargs: pytest.fail("No accepted contract"))
    with pytest.raises(ValueError, match="CONTRACT_AUTHENTICATED_OWNER_ACCEPTANCE_REQUIRED"):
        g5.authenticate_g5a(approval={"approved": True}, quota=None, custody=None, binding=binding(),
            filter_binding={"source_sha": "a" * 40, "source_tree": "b" * 40, "plan_sha256": "c" * 64},
            prerequisites_manifest={}, publication_envelope_reference={}, evidence_root=tmp_path / "untouched")
    assert not (tmp_path / "untouched").exists()


def test_quota_and_custody_handles_cannot_be_fabricated_from_metadata():
    with pytest.raises(ValueError, match="SERIALIZED_OR_SYNTHETIC_QUOTA"):
        g5.NativeProjectQuota(object(), path=Path("/tmp"), descriptor=-1, project_id=1,
                             readback={"kernel_readback": True}, binding=binding())
    with pytest.raises(ValueError, match="NATIVE_CUSTODY_FACTORY_REQUIRED"):
        g5.NativeCustodyContext(object(), request={"g5a_contract_sha256": g5.CONTRACT_SHA256}, proof={})
    with pytest.raises(ValueError, match="SERIALIZED_OR_CALLER_ASSERTED_ADMISSION"):
        g5.G5AAuthority(object(), approval=None, quota=None, binding=binding(), prerequisites={}, custody=None,
            source_sha="a" * 40, source_tree="b" * 40, filter_binding={}, publication_envelope={})


def source_files():
    return {name: (ROOT / name).read_bytes() for name in formats.SOURCE_MEMBERS}


def test_writer_codec_restore_binding_matches_actual_artifact_members_without_v4_promotion():
    original = source_files()
    record = formats.writer_codec_restorer_binding(original)
    result = formats.verify_immutable_artifact_binding(record, source_files=original, artifact_files=dict(original))
    assert result["status"] == "EXACT_BYTES_MATCHED_NOT_PROMOTION"
    assert record["entrypoints"]["shared_writer"]["qualified_name"] == "ComponentArchive._publish_original_plan"
    assert record["entrypoints"]["native_ack"]["qualified_name"] == "EvidenceRetention._archive_generation"
    assert result["native_v4_write_enabled"] is False and components.NATIVE_WRITE_RECIPE_SCHEMA == components.RECIPE_SCHEMA


def test_candidate_native_v4_calls_same_source_writer_and_restores_after_legacy_v3(tmp_path, monkeypatch):
    from rc6_shadow_runtime.persistence import EvidenceFiles
    from rc6_shadow_runtime.retention import EvidenceRetention
    from tests.test_issue465_generations import publish
    from tests.test_rc6_component_archive import tree_custody
    root, archive, _, directories, receipts = native(tmp_path, count=2)
    with EvidenceFiles(root) as files:
        cut = publish(files, 3)
    generation = root / ("gen-" + cut["pointer"]["generation_id"])
    original, source_custody = members(generation), tree_custody(generation)
    calls, actual = [], components.ComponentArchive.build_v4
    def observe(self, source, manifest, digest):
        assert source == generation
        calls.append(source)
        return actual(self, source, manifest, digest)
    monkeypatch.setattr(components.ComponentArchive, "build_v4", observe)
    owner = EvidenceRetention(root, archive_root=archive, archive_format="COMPONENT_V4")
    receipt = owner.archive_generation(generation)
    assert calls == [generation] and (root / ("archive-ack-" + receipt["generation_id"] + ".json")).is_file()
    assert tree_custody(generation) == source_custody
    monkeypatch.setattr(components.ComponentArchive, "publish_original_v4", lambda *args: pytest.fail("Restore must not write"))
    assert owner.restore_generation(receipt["generation_id"])["members"] == original
    for path, historical in zip(directories, receipts):
        assert owner.restore_generation(historical["generation_id"])["members"] == members(path)
    assert formats.writer_codec_restorer_binding(source_files())["effective_native_writer"] == "ComponentArchive.build"


@pytest.mark.parametrize("target,old,new,signature", (
    ("rc6_shadow_runtime/archive_components.py", b"return self.build_v4(source, manifest, manifest_sha)",
        b"return self.build_v3(source, manifest, manifest_sha)", "CANDIDATE_NATIVE_V4_DISPATCH_REBOUND"),
    ("rc6_shadow_runtime/worker.py", b'kwargs.setdefault("archive_format", "COMPONENT_V4")',
        b'kwargs.setdefault("archive_format", "COMPONENT_V3")', "CANDIDATE_RUNTIME_FACTORY_REBOUND"),
    ("rc6_shadow_runtime/archive_components.py", b"writer=self.publish_original_v4)",
        b"writer=self.publish_original_v3)", "CANDIDATE_V4_PLANNER_REBOUND"),
))
def test_candidate_future_artifact_cannot_inherit_qualification_after_writer_or_factory_change(target, old, new, signature):
    actual = source_files()
    assert old in actual[target]
    actual[target] = actual[target].replace(old, new, 1)
    with pytest.raises(ValueError, match=signature):
        formats.writer_codec_restorer_binding(actual)


def test_all1202_raw_frames_require_candidate_selected_native_writer_before_scratch_creation(tmp_path):
    writer = formats.writer_codec_restorer_binding(source_files())
    manifest = {"schema": "rc6.g5a-all1202-original-byte-frames.v2", "source_sha": "a" * 40,
        "source_tree": "b" * 40, "writer_codec_restorer_binding_sha256": writer["binding_sha256"],
        "actual_original_cuts": 1202, "frames": [{}] * 1202,
        "selected_future_writer": "UNSELECTED", "native_entrypoint": "ComponentArchive.publish_original_v4"}
    scratch = tmp_path / "no-scratch"
    with pytest.raises(ValueError, match="ALL1202_AUTHENTIC_ORIGINAL_FRAME_STREAM_REQUIRED"):
        g5b.verify_original_frame_stream(manifest, capture_frame=lambda *args: pytest.fail("Unselected writer"),
            comparison_result={"original_lossless_frames": manifest}, writer_binding=writer,
            source_sha="a" * 40, source_tree="b" * 40, scratch_root=scratch)
    assert not scratch.exists()


def test_capture_envelope_is_a_byte_join_and_never_an_owner_or_native_authority(tmp_path):
    raw = {role: b"{}" for role in g5b.REQUIRED_ROLES - {"g5b_complete"}}
    raw["g5b_writer_binding"] = components.canonical(formats.writer_codec_restorer_binding(source_files()))
    envelope = g5b.build_capture_envelope(raw, source_sha="a" * 40, source_tree="b" * 40)
    assert envelope["captured_roles_sha256"] == {role: components.sha(value) for role, value in raw.items()}
    assert envelope["qualification_claimed"] is envelope["native_v4_write_enabled"] is False
    with pytest.raises(ValueError, match="CONTRACT_AUTHENTICATED_OWNER_ACCEPTANCE_REQUIRED"):
        g5b.verify_g5b_capture({"source_sha": "a" * 40, "source_tree": "b" * 40},
            {**raw, "g5b_complete": components.canonical(envelope)}, approval=envelope, source_root=tmp_path)
    with pytest.raises(ValueError, match="COMPLETE_ACTUAL_BOUNDED_RAW_ROLES_REQUIRED_FOR_ENVELOPE"):
        g5b.build_capture_envelope({**raw, "native_Horizon": b""}, source_sha="a" * 40, source_tree="b" * 40)


@pytest.mark.parametrize("path", formats.SOURCE_MEMBERS)
def test_artifact_change_in_any_writer_reader_ack_gc_or_codec_fails_exact_source_binding(path):
    original = source_files()
    record = formats.writer_codec_restorer_binding(original)
    artifact = {**original, path: original[path] + b"\n# altered exact artifact member\n"}
    with pytest.raises(ValueError, match="WRITER_CODEC_RESTORER_ARTIFACT_REBOUND"):
        formats.verify_immutable_artifact_binding(record, source_files=original, artifact_files=artifact)


def test_native_and_private_candidate_share_the_exact_publisher_and_five_original_bytes(tmp_path, monkeypatch):
    observed = []
    actual = components.ComponentArchive._publish_original_plan
    def observe(self, original, manifest, manifest_sha, plan):
        observed.append((set(original), components.sha(original["manifest.json"]), self.root))
        return actual(self, original, manifest, manifest_sha, plan)
    monkeypatch.setattr(components.ComponentArchive, "_publish_original_plan", observe)
    root, archive, _, directories, receipts = native(tmp_path, count=1)
    captured = members(directories[0])
    candidate = comparison._PrivateBranch(tmp_path / "candidate", slices=True)
    result = candidate.archive_originals(captured)
    assert len(observed) == 2 and all(row[0] == components.MEMBERS for row in observed)
    assert observed[0][1] == observed[1][1] == components.sha(captured["manifest.json"])
    assert policy(root, archive).restore_generation(receipts[0]["generation_id"])["members"] == captured
    assert candidate.owner.restore_generation(result["receipt"]["generation_id"])["members"] == captured


def test_quota_reservation_red_happens_before_any_recipe_or_pack_publication(tmp_path, monkeypatch):
    _, _, _, directories, _ = native(tmp_path, count=1)
    branch = comparison._PrivateBranch(tmp_path / "candidate", slices=True)
    class UnitDeny:
        def before_publication(self, **kwargs):
            raise ValueError("G5A_CURRENT_CONCURRENT_PHYSICAL_RESERVATION_RED")
    # A private economic API fixture; this stub cannot pass material admission.
    branch.owner.g5a, branch.owner.expected_binding = UnitDeny(), binding()
    monkeypatch.setattr(components.ComponentArchive, "_publish",
                        lambda *args, **kwargs: pytest.fail("Reservation must precede publication"))
    with pytest.raises(ValueError, match="PHYSICAL_RESERVATION_RED"):
        branch.archive_originals(members(directories[0]))
    assert not list(branch.archive.glob("*.recipe.gz")) and not list(branch.archive.glob("*.cas.pack"))


@pytest.mark.parametrize("result", ({"actual_original_cuts": 1049}, {"actual_original_cuts": 1202},
                                     {"actual_original_cuts": 1202, "storage_reduction_percent": 69.91}))
def test_partial_or_synthetic_reduction_cannot_satisfy_g5b(result):
    with pytest.raises(ValueError, match="G5B_ALL1202_ORIGINAL_RESTORES_AND_NATIVE_ACKS_REQUIRED"):
        g5.validate_complete_original_stream(result, quota_trace=[], writer_binding={})


def test_producer_rejects_serialized_context_before_original_source_or_fixtures(monkeypatch, tmp_path):
    monkeypatch.setattr(producer, "verify_source", lambda *args, **kwargs: pytest.fail("No valid G5A context"))
    monkeypatch.setattr("sys.argv", ["producer", "--source-repo", str(ROOT), "--source-root", str(ROOT),
        "--source-sha", "a" * 40, "--source-tree", "b" * 40, "--source-index", str(tmp_path / "unread.json"),
        "--root", str(tmp_path / "uncreated"), "--ticks", "1201", "--catalog-count", "1200",
        "--owned-fin", str(tmp_path / "uncreated-fin.json")])
    with pytest.raises(ValueError, match="G5A_AUTHENTIC_CURRENT_SCOPED_ADMISSION_REQUIRED"):
        producer.main(g5a={"approved": True}, expected_binding=binding())
    assert not (tmp_path / "uncreated").exists()


def test_g5b_rejects_forged_owner_acceptance_before_examining_raw_or_source(tmp_path, monkeypatch):
    monkeypatch.setattr(g5b.components, "loads", lambda *args: pytest.fail("Owner acceptance is missing"))
    with pytest.raises(ValueError, match="CONTRACT_AUTHENTICATED_OWNER_ACCEPTANCE_REQUIRED"):
        g5b.verify_g5b_capture({"gate": "G5", "contract_revision": g5.REVISION,
            "source_sha": "a" * 40, "source_tree": "b" * 40}, {},
            approval={"accepted": True}, source_root=tmp_path / "unread")


@pytest.mark.parametrize("missing", ("native_operations", "hard_bytes", "format_id"))
def test_g5b_missing_native_quota_readback_is_discriminated_without_green(missing):
    record = {"schema": "rc6.g5a-native-physical-control.v2", "source_sha": "a" * 40,
        "source_tree": "b" * 40, "contract_sha256": g5.CONTRACT_SHA256,
        "quota_initial_readback": {"hard_bytes": g5.CAP_BYTES, "hard_inodes": g5.CAP_INODES,
            "format_id": 4, "native_operations": []}}
    record["quota_initial_readback"].pop(missing)
    with pytest.raises(ValueError, match="G5B_NATIVE_PROJECT_QUOTA_READBACK_NOT_512MIB_32768"):
        g5b.verify_native_quota_capture(record, row={"source_sha": "a" * 40, "source_tree": "b" * 40})


def test_native_quota_readback_error_retains_actual_numeric_errno_without_module_inference():
    # Actual read-only syscall on a nonexistent device; no filesystem setup,
    # synthetic errno, root launch, or qualification is performed in this test.
    with pytest.raises(ValueError, match="G5A_NATIVE_Q_GETFMT_FAILED") as raised:
        g5._quota_readback("/dev/rc6-unit-nonexistent-owned-project", 1)
    details = raised.value.details
    assert details["operation"] == "Q_GETFMT" and details["return"] == -1
    assert type(details["errno"]) is int and details["errno"] > 0
    assert details["errno_name"] == errno.errorcode[details["errno"]]


@pytest.mark.parametrize("branch", ("main", "../foreign", "bad//branch", "refs/../heads/x", "x.lock", "x\nforeign", True))
def test_capability_artifact_invalid_authenticated_branch_fails_before_network_or_output(branch, tmp_path):
    with pytest.raises(ValueError, match="CAPABILITY_AUTHENTICATED_LITERAL_BRANCH_REQUIRED"):
        gates.verify_capacity_capability_artifact({}, source_sha="a" * 40, source_tree="b" * 40,
            read_contract_sha256="c" * 64, evidence_root=tmp_path / "untouched", authenticated_branch=branch,
            get=lambda *args: pytest.fail("Invalid literal branch"), download=lambda *args, **kwargs: pytest.fail("No download"))
    assert not (tmp_path / "untouched").exists()


def test_revised_private_pid_observation_cannot_be_an_enoent_capability_fallback():
    with pytest.raises(ValueError, match="CAPABILITY_PRIVATE_PID_V2_AUTHENTICATED_CAPTURE_GUARDIAN_ROOT_FIN_REPLAY_NOT_IMPLEMENTED"):
        gates.verify_capacity_probe_observations({"private_pid_boundary_v2": {"errno": errno.ENOENT}}, {})
