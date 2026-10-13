"""Pure G5A schema/security ordering and real owned-file framing regressions.

These are NONROOT unit tests, not ROOT/private-PID/quota/G5 acceptance. They
never invoke an original material producer, authenticate an owner or start a
unit. Real small own files/pipes exercise transfer and deadline discriminators.
"""
import ast
import io
import os
from pathlib import Path
import signal
import stat
import struct
from types import SimpleNamespace

import pytest

from scripts import rc6_g5a_native_runner as runner


@pytest.fixture
def root_request(tmp_path):
    from scripts.rc6_material_carrier import workload_fingerprint
    source, repository, inputs = (tmp_path / name for name in ("exported-source", "git-repo", "inputs"))
    for path in (source, repository, inputs):
        path.mkdir(mode=0o700)
    namespace = tmp_path / "r6-AAAAAAAAAAAAAAAAAAAAAA"
    namespace.mkdir(mode=0o700)
    mount = namespace / "mount"
    mount.mkdir(mode=0o700)
    uid, gid = os.getuid(), os.getgid()
    assert uid > 0 and gid > 0, "This regression suite deliberately requires NONROOT"
    binding = {"candidate_sha": "a" * 40, "candidate_tree": "b" * 40,
        "producer": "Horizon-private-original-cas", "attempt_id": "unit-shape-only:1", "owner_id": "unit-owner",
        "workload_fingerprint": workload_fingerprint("Horizon-private-original-cas"),
        "runner_class": "github-hosted/ubuntu-24.04"}
    value = {key: None for key in runner.REQUEST_FIELDS}
    value.update(schema=runner.REQUEST_SCHEMA, operation_scope=runner.OPERATION_SCOPE,
        limits=dict(runner.LIMITS), g5a_quotas=dict(runner.QUOTAS), owner_uid=uid, owner_gid=gid,
        source_root=str(source), source_repo=str(repository), source_sha="a" * 40, source_tree="b" * 40,
        binding=binding, namespace_receipt={"schema": "porota.rc6.generated-fixture-consumer-binding.v1",
            "path": str(namespace), "namespace_nonce": "0" * 32, "binding": binding,
            "cleanup_authority_granted": False}, image=str(namespace / "image.ext4"),
        mountpoint=str(mount), loop_birth_path=str(namespace / "loop-birth.json"),
        original_workload_root=str(mount / "project"), mount_namespace_inode=1,
        image_identity={}, loop_birth_identity={}, loop_birth_pending_sha256="c" * 64,
        host_PID1={}, host_boundary={}, issuer={}, boundary_revision="PRIVATE_PID_BOUNDARY_V2",
        boundary_host_proc_device=1, require_quota_ancestor_seal=True,
        g5a_contract_sha256=runner.g5.CONTRACT_SHA256,
        g5a_revision_approval_url="https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-1",
        g5a_revision_owner="unit-owner", g5a_oracle_metadata_path=str(namespace / "g5a-owner-oracle.json"),
        source_index=str(inputs / "source.index.json"), source_index_sha256="d" * 64,
        python311=str(inputs / "venv/bin/python"),
        interpreter_manifest_path=str(inputs / "interpreter.json"), interpreter_manifest_sha256="e" * 64,
        cas_comparison_manifest_path=str(inputs / "comparison.json"), cas_comparison_manifest_sha256="f" * 64,
        g5a_prerequisites_manifest_path=str(inputs / "prerequisites.json"), g5a_prerequisites_manifest_sha256="1" * 64,
        filter_binding={"source_sha": "a" * 40, "source_tree": "b" * 40, "plan_sha256": "2" * 64},
        code_hashes={member: "3" * 64 for member in runner.CODE_MEMBERS})
    return value


def test_exact_schema_is_only_shape_and_does_not_mint_authority(root_request):
    paths = runner.validate_request_shape(root_request)
    assert paths["archive"] == paths["producer"] / "data/paper_v17/artifacts/observer_v17.db/dynamic-shadow-archive"
    assert paths["control"].parent == paths["producer"].parent
    assert not hasattr(paths, "approval")
    with pytest.raises(ValueError, match="ORIGINAL_SINGLE_ROOT_WORKER"):
        runner.acquire_same_process(root_request, {}, original_request_sha256="4" * 64)


@pytest.mark.parametrize("field", ["mode", "nonroot", "approved", "runtime", "root_actor", "argv", "GITHUB_TOKEN",
    "original_request_sha256", "privileged_custody_live", "skip_gate"])
def test_no_alternate_mode_environment_boolean_or_controller_json_admission(root_request, field):
    root_request[field] = True
    with pytest.raises(ValueError, match="EXACT_SOURCE_FIXED_ROOT_REQUEST"):
        runner.validate_request_shape(root_request)


@pytest.mark.parametrize("dictionary,field,delta", [
    ("limits", "backing_image_bytes", 1), ("limits", "project_hard_limit_bytes", -1),
    ("limits", "residual_reserve_bytes", -1), ("g5a_quotas", "workload_hard_bytes", 1),
    ("g5a_quotas", "workload_hard_inodes", -1), ("g5a_quotas", "archive_hard_bytes", 1),
    ("g5a_quotas", "archive_hard_inodes", 1), ("g5a_quotas", "probe_hard_bytes", 1),
    ("g5a_quotas", "probe_hard_inodes", -1)])
def test_no_limit_or_reserve_changes(root_request, dictionary, field, delta):
    root_request[dictionary][field] += delta
    with pytest.raises(ValueError, match="ORIGINAL_BOOTSTRAP_AND_ARCHIVE_LIMITS"):
        runner.validate_request_shape(root_request)


@pytest.mark.parametrize("field,value,signature", [
    ("owner_uid", True, "ACTUAL_NONROOT_ISSUER"), ("owner_gid", 0, "ACTUAL_NONROOT_ISSUER"),
    ("g5a_revision_owner", "foreign-owner", "ORIGINAL_WORKLOAD_OWNER"),
    ("source_sha", "e" * 40, "ORIGINAL_WORKLOAD_OWNER"),
    ("g5a_contract_sha256", "0" * 64, "EXACT_SOURCE_AND_PROPOSED_CONTRACT"),
    ("boundary_revision", "legacy", "ORIGINAL_NATIVE_PRIVATE_PID"),
    ("require_quota_ancestor_seal", False, "ORIGINAL_NATIVE_PRIVATE_PID"),
    ("mount_namespace_inode", True, "ORIGINAL_NATIVE_PRIVATE_PID"),
    ("g5a_revision_approval_url", "https://github.com/mbalbo2023/Porota-trading/issues/472#issuecomment-1", "CANONICAL_OWNER_DECISION"),
    ("g5a_revision_approval_url", "https://github.com/foreign/repo/issues/471#issuecomment-1", "CANONICAL_OWNER_DECISION")])
def test_typed_source_owner_and_native_boundary_guards(root_request, field, value, signature):
    root_request[field] = value
    with pytest.raises(ValueError, match=signature):
        runner.validate_request_shape(root_request)


@pytest.mark.parametrize("field", ["image", "mountpoint", "loop_birth_path", "g5a_oracle_metadata_path", "original_workload_root"])
def test_no_foreign_resources(root_request, field, tmp_path):
    root_request[field] = str(tmp_path / "foreign")
    with pytest.raises(ValueError, match="FOREIGN_RESOURCE_VETO|ORIGINAL_WORKLOAD_ROOT_REBOUND"):
        runner.validate_request_shape(root_request)


def test_filter_source_and_plan_binding_cannot_be_replaced(root_request):
    root_request["filter_binding"]["source_sha"] = "0" * 40
    with pytest.raises(ValueError, match="EXACT_NATIVE_FILTER_BINDING"):
        runner.validate_request_shape(root_request)


def test_closed_source_members_are_not_optional(root_request):
    del root_request["code_hashes"][runner.MEMBER]
    with pytest.raises(ValueError, match="CLOSED_SOURCE_CODE_HASHES"):
        runner.validate_request_shape(root_request)


def test_source_namespace_overlap_fails(root_request):
    root_request["source_root"] = root_request["namespace_receipt"]["path"]
    with pytest.raises(ValueError, match="SOURCE_REPOSITORY_NAMESPACE_OVERLAP"):
        runner.validate_request_shape(root_request)


def test_original_workload_and_timeout_constants(root_request):
    arguments = runner.producer_arguments(root_request)
    assert arguments[arguments.index("--catalog-count") + 1] == "1200"
    assert arguments[arguments.index("--ticks") + 1] == "1201"
    assert arguments[arguments.index("--owned-fin") + 1].endswith("/mount/control/producer-owned-fin.json")
    assert (runner.HORIZON_SECONDS, runner.HORIZON_CYCLE_SECONDS, runner.NATIVE_FIN_SECONDS) == (21600, 30, 5)
    assert (runner.TRUSTED_API_SECONDS, runner.ANCESTOR_SEAL_SECONDS) == (300, 5)
    assert runner.LIMITS == {"backing_image_bytes": 26 * 1024**3,
        "project_hard_limit_bytes": 20 * 1024**3, "residual_reserve_bytes": 4 * 1024**3}
    assert runner.MAX_STREAM_BYTES == 20 * 1024**3
    assert runner.STREAM_CHUNK == 512 * 1024
    assert runner.MAX_RAW == 24 * 1024**2


def _call_name(node):
    if isinstance(node, ast.Name): return node.id
    if isinstance(node, ast.Attribute): return _call_name(node.value) + "." + node.attr
    return "UNKNOWN"


def test_security_phase_order_guards_trusted_root_input_before_seal_and_fin_before_reads():
    module = ast.parse(Path(runner.__file__).read_text())
    acquire = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "acquire_same_process")
    calls = {_call_name(node.func): node.lineno for node in ast.walk(acquire) if isinstance(node, ast.Call)}
    ordered = ["validate_request_shape", "validate_source_inputs", "g5.observe_trusted_api_controller",
        "g5.RootOwnerOracleReader.open", "g5.prepare_g5a_trusted", "continuation_module.bind_continuation", "reader.close",
        "g5.validate_trusted_g5a_preparation", "wait_quota_ancestor_seal", "calibration.g5a_trusted_quota_setup",
        "g5.observe_live_g5a_custody", "g5.observe_root_workload_quota", "g5.open_native_project_quota",
        "quota.close_before_seal", "calibration.g5a_seal_quota_setup", "private_pid.quota_actor_observation",
        "quota.reopen_after_seal", "g5.prove_native_quota_enforcement", "continuation_module.activate_continuation", "g5.authenticate_g5a",
        "_write_native_control", "_run_original_producer", "capture_control_raw", "quota.close"]
    assert [calls[name] for name in ordered] == sorted(calls[name] for name in ordered)
    authenticate = next(node for node in ast.walk(acquire) if isinstance(node, ast.Call)
                        and _call_name(node.func) == "g5.authenticate_g5a")
    assert {item.arg for item in authenticate.keywords} >= {"trusted_preparation", "workload_quota", "continuation"}
    assert not {item.arg for item in authenticate.keywords} & {"get", "download"}
    assert "calibration.root_setup" not in calls and "calibration.trusted_quota_setup" not in calls
    assert not {"subprocess.run", "subprocess.Popen", "os.execve", "os.execv", "runpy.run_path"} & set(calls)
    capture = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "capture_control_raw")
    body_calls = [(node.lineno, _call_name(node.func)) for node in ast.walk(capture) if isinstance(node, ast.Call)]
    assert min(line for line, name in body_calls if name == "_own_echild") < min(
        line for line, name in body_calls if name == "read_input")
    producer = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "_run_original_producer")
    actual = next(node for node in ast.walk(producer) if isinstance(node, ast.Call) and _call_name(node.func) == "producer.main")
    assert {item.arg for item in actual.keywords} == {"g5a", "expected_binding"}


def test_api_budget_uses_real_finite_owned_alarm_and_restores_handler(monkeypatch):
    monkeypatch.setattr(runner, "TRUSTED_API_SECONDS", 0.02)
    read_descriptor, write_descriptor = os.pipe()
    previous = signal.getsignal(signal.SIGALRM)
    try:
        with pytest.raises(TimeoutError, match="TRUSTED_API300_DEADLINE_EXCEEDED"):
            with runner.trusted_api_deadline():
                os.read(read_descriptor, 1)
        assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
        assert signal.getsignal(signal.SIGALRM) == previous
    finally:
        os.close(read_descriptor); os.close(write_descriptor)


def test_foreign_timer_is_not_replaced():
    previous = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, signal.SIG_IGN)
    try:
        with pytest.raises(ValueError, match="FRESH_OWN_TRUSTED_INPUT_TIMER"):
            with runner.trusted_api_deadline():
                pytest.fail("foreign alarm was replaced")
        assert signal.getsignal(signal.SIGALRM) == signal.SIG_IGN
    finally:
        signal.signal(signal.SIGALRM, previous)


def test_actual_input_bytes_are_pinned_and_digest_rebound_fails(tmp_path):
    path = tmp_path / "control.json"
    path.write_bytes(b"{\"scope\":\"unit-only\"}\n"); path.chmod(0o600)
    assert runner.read_input(path, expected_sha256=runner.calibration.digest(path.read_bytes()),
        owner=(os.getuid(), os.getgid()), mode=0o600) == path.read_bytes()
    with pytest.raises(ValueError, match="INPUT_BYTES_REBOUND"):
        runner.read_input(path, expected_sha256="0" * 64)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_input_alias_and_fifo_attack_fail_before_read(tmp_path, kind):
    original = tmp_path / "original"; original.write_bytes(b"unit-data")
    attack = tmp_path / "attack"
    if kind == "symlink": attack.symlink_to(original)
    elif kind == "hardlink": os.link(original, attack)
    else: os.mkfifo(attack, 0o600)
    with pytest.raises(ValueError, match="PATH_ALIAS_FORBIDDEN|INPUT_FILE_CUSTODY"):
        runner.read_input(attack)


@pytest.fixture
def own_trees(root_request):
    paths = runner.expected_paths(root_request)
    for name in ("producer", "control", "probe"):
        paths[name].mkdir(mode=0o700)
    payload = b"original owned unit bytes\x00" + runner.STREAM_MAGIC + b"\xff\n"
    member = paths["producer"] / "original.bin"
    member.write_bytes(payload); member.chmod(0o600)
    return root_request, paths, member, payload


def decode_stream(raw):
    assert raw.startswith(runner.STREAM_MAGIC)
    offset, frames = len(runner.STREAM_MAGIC), []
    while offset < len(raw):
        size = struct.unpack(">I", raw[offset:offset + 4])[0]; offset += 4
        assert 0 < size <= runner.MAX_FRAME_HEADER
        header = runner.calibration.decode(raw[offset:offset + size]); offset += size
        payload = raw[offset:offset + header.get("bytes", 0)]; offset += len(payload)
        frames.append((header, payload))
        if header["kind"] == "END": break
    assert offset == len(raw)
    return frames


def test_actual_binary_stream_preserves_bytes_and_embedded_magic(own_trees):
    root_request, paths, member, payload = own_trees
    inventory = runner.source_stream_inventory(root_request)
    output = io.BytesIO()
    total = runner.stream_source_raw(root_request, inventory, {"schema": runner.WORKER_SCHEMA,
        "report": {"G5_GREEN": False, "qualification_claimed": False}}, output)
    frames = decode_stream(output.getvalue())
    header, captured = next(item for item in frames if item[0].get("name") == "project/original.bin")
    assert captured == payload and header["bytes"] == len(payload)
    assert header["sha256"] == runner.calibration.digest(payload)
    assert frames[-1][0]["kind"] == "END" and frames[-1][0]["G5_GREEN"] is False
    assert frames[-1][0]["external_ROOT_FIN_claimed"] is False
    assert inventory["payload_bytes"] == len(payload) and inventory["allocated_bytes"] >= len(payload)
    assert total == len(output.getvalue())


def test_stream_handles_actual_short_writes_without_dropping_bytes(own_trees):
    class Partial(io.BytesIO):
        def write(self, value): return super().write(value[:3])
    root_request, _, _, payload = own_trees
    output = Partial()
    runner.stream_source_raw(root_request, runner.source_stream_inventory(root_request), {}, output)
    assert next(data for header, data in decode_stream(output.getvalue()) if header.get("kind") == "FILE") == payload


def test_zero_write_is_discriminating_and_does_not_repeat():
    output = SimpleNamespace(write=lambda _: 0)
    with pytest.raises(ValueError, match="NATIVE_RAW_STREAM_SHORT_WRITE"):
        runner._write_all(output, b"small-unit-bytes")


@pytest.mark.parametrize("attack", ["symlink", "hardlink", "foreign_mode", "fifo"])
def test_stream_own_filesystem_custody_attacks_are_rejected(own_trees, attack):
    root_request, paths, member, _ = own_trees
    if attack == "symlink": (paths["control"] / "alias").symlink_to(member)
    elif attack == "hardlink": os.link(member, paths["control"] / "alias")
    elif attack == "foreign_mode": member.chmod(0o666)
    else: os.mkfifo(paths["control"] / "pipe", 0o600)
    with pytest.raises(ValueError, match="PATH_ALIAS|OWN_SINGLELINK|NATIVE_OWN_FILESYSTEM"):
        runner.source_stream_inventory(root_request)


def test_inventory_change_after_fin_does_not_stream(own_trees):
    root_request, _, member, _ = own_trees
    inventory = runner.source_stream_inventory(root_request)
    member.write_bytes(b"changed-after-inventory")
    output = io.BytesIO()
    with pytest.raises(ValueError, match="RAW_INVENTORY_REBOUND"):
        runner.stream_source_raw(root_request, inventory, {}, output)
    assert output.getvalue() == b""


def test_actual_file_mutation_after_hash_pass_cannot_be_labeled_exact(own_trees):
    root_request, _, member, _ = own_trees
    class Mutating(io.BytesIO):
        def write(self, value):
            data = bytes(value)
            if b'"kind":"FILE"' in data:
                member.write_bytes(b"mutated-during-source-stream")
            return super().write(value)
    with pytest.raises(ValueError, match="TRANSFER_BYTES_EXCEEDED|NATIVE_CONTENT_CHANGED"):
        runner.stream_source_raw(root_request, runner.source_stream_inventory(root_request), {}, Mutating())


def test_opaque_g5a_cannot_be_reconstructed_from_worker_json(root_request):
    with pytest.raises(ValueError, match="CURRENT_TYPED_TRUSTED_PREPARATION"):
        runner.g5.validate_trusted_g5a_preparation({"approved": True},
            binding=root_request["binding"], prerequisites_manifest={})
    with pytest.raises(ValueError, match="NATIVE_TRUSTED_PREPARATION_FACTORY"):
        runner.g5.TrustedG5APreparation()


def test_missing_original_controller_stops_before_root_or_quota(root_request, monkeypatch):
    paths = runner.expected_paths(root_request)
    path = paths["namespace"] / "root-request.json"
    path.write_bytes(runner.calibration.wire(root_request)); path.chmod(0o600)
    output = io.BytesIO()
    monkeypatch.setattr(runner.sys, "stdout", SimpleNamespace(buffer=output))
    assert runner.root_worker(path) == 1
    result = runner.calibration.decode(output.getvalue())
    assert result["schema"] == runner.WORKER_SCHEMA
    assert result["report"]["reason"] == "G5A_RUNNER_ORIGINAL_CONTROLLER_PROOF_REQUIRED"
    assert result["report"]["G5_GREEN"] is False and result["report"]["external_ROOT_FIN_claimed"] is False
    assert result["report"]["required_raw_complete"] is False
    assert not (paths["mount"] / "project").exists()
