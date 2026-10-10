"""Captured-format attacks only; no ROOT launch, approval or native G5 proof."""
import base64
import copy
import importlib.util
from pathlib import Path

import pytest

from scripts import rc6_g5a_custody_replay as replay
from scripts import rc6_private_pid_capture_replay as pid
from scripts import rc6_privileged_custody as custody


@pytest.fixture
def original_capture():
    path = Path(__file__).with_name("test_rc6_private_pid_capture_replay.py")
    spec = importlib.util.spec_from_file_location("rc6_private_pid_captured_format_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CaptureFormatFixture()


def joined(capture):
    capability = {"schema": "CAPTURED_FORMAT_ONLY_NOT_NATIVE", "source_sha": capture.binding["candidate_sha"],
        "source_tree": capture.binding["candidate_tree"], "native_custody_receipt": copy.deepcopy(capture.receipt)}
    pair = {"capability_receipt_sha256": replay.sha(custody.wire(capability)), "bootstrap_receipt_sha256": "d" * 64}
    files = {name: {"path": "g0-private-capture/" + name, "bytes": len(raw), "sha256": replay.sha(raw)}
             for name, raw in capture.raws.items()}
    member_bytes = {"g0-private-capture/" + name: raw for name, raw in capture.raws.items()}
    value = {"scope": "G0_NATIVE_BOUNDARY_PREREQUISITE", "receipt": copy.deepcopy(capture.receipt),
             "files": files, "capacity_receipt": capability}
    request = {"source_sha": capture.binding["candidate_sha"], "source_tree": capture.binding["candidate_tree"]}
    return value, pair, member_bytes, request


def verify_original(capture, value, pair, member_bytes, request):
    return replay._replay_private_capture(value, request=request, native_pair=pair,
        read_member=member_bytes.__getitem__, source_hashes=capture.hashes)


def test_source_joined_original_g0_corpus_replays_all_four_cases_without_authority(original_capture, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("captured replay cannot touch live ROOT/kernel/approval factories")
    for name in ("prove_custody", "kernel_process", "verify_live_controller"):
        monkeypatch.setattr(custody, name, forbidden)
    result = verify_original(original_capture, *joined(original_capture))
    assert result["root_cases"] == list(pid.CASES)
    assert result["binding"]["producer"] == "capacity-probe"
    assert not result["in_process_witness_minted"]
    assert not result["G0_GREEN"]
    assert not result["privileged_launch_authorized"]


@pytest.mark.parametrize("name", sorted({"manifest.json", *pid.REQUIRED}))
def test_every_original_adversarial_control_is_required(original_capture, name):
    value, pair, members, request = joined(original_capture)
    del value["files"][name]
    with pytest.raises(ValueError, match="COMPLETE_ORIGINAL_CONTROL_REFERENCES_REQUIRED"):
        verify_original(original_capture, value, pair, members, request)


@pytest.mark.parametrize("defect,error", [
    ("wrong_source", "SOURCE_LINKED_G0_PRIVATE_PID_CAPTURE_REQUIRED"),
    ("changed_capacity", "SOURCE_LINKED_G0_PRIVATE_PID_CAPTURE_REQUIRED"),
    ("no_native_pair", "SOURCE_LINKED_G0_PRIVATE_PID_CAPTURE_REQUIRED"),
    ("horizon_alias", "G0_ORIGINAL_BINDING_CANNOT_ALIAS_HORIZON"),
    ("changed_native_root_raw", "ACTUAL_ORIGINAL_BYTES_CHANGED"),
    ("source_hash_mismatch", "G0_REPLAY_EXACT_SOURCE_BYTES_REQUIRED"),
])
def test_g0_receipt_cannot_be_relabelled_or_replaced_by_source_flags(original_capture, defect, error):
    value, pair, members, request = joined(original_capture)
    if defect == "wrong_source":
        request["source_sha"] = "e" * 40
    elif defect == "changed_capacity":
        pair["capability_receipt_sha256"] = "e" * 64
    elif defect == "no_native_pair":
        pair = None
    elif defect == "horizon_alias":
        value["receipt"]["binding"]["producer"] = "Horizon-private-original-cas"
        value["capacity_receipt"]["native_custody_receipt"] = value["receipt"]
        pair["capability_receipt_sha256"] = replay.sha(custody.wire(value["capacity_receipt"]))
    elif defect == "changed_native_root_raw":
        members["g0-private-capture/root-success.native.log"] += b" "
    else:
        original_capture.hashes[custody.MEMBER] = "e" * 64
    with pytest.raises(ValueError, match=error):
        verify_original(original_capture, value, pair, members, request)


@pytest.mark.parametrize("reference,error", [
    ({"path": "../outside", "bytes": 1, "sha256": "a" * 64}, "RELATIVE_SOURCE_INVALID"),
    ({"path": "raw", "bytes": True, "sha256": "a" * 64}, "BOUNDED_RAW_REFERENCE_REQUIRED"),
    ({"path": "raw", "bytes": 20 * 1024**3, "sha256": "a" * 64}, "BOUNDED_RAW_REFERENCE_REQUIRED"),
    ({"path": "raw", "bytes": 1, "sha256": "a" * 64, "GREEN": True}, "BOUNDED_RAW_REFERENCE_REQUIRED"),
])
def test_authenticated_leaf_transport_does_not_allow_aliases_giant_streams_or_booleans(reference, error):
    with pytest.raises(ValueError, match=error):
        replay._references({"control": reference}, names={"control"}, read_member=lambda path: b"x")


def test_json_cannot_replace_actual_authenticated_actions_archive():
    with pytest.raises(ValueError, match="AUTHENTICATED_ACTIONS_CAPTURE_REQUIRED"):
        replay.verify_capture({"GREEN": True}, row={}, quota_control={}, archive_context={"authenticated": True},
                              source_root=Path(__file__).resolve().parents[1])


def test_original_unit_bytes_replay_exact_g5a_compute_and_security_properties(original_capture):
    request = copy.deepcopy(original_capture.probe)
    request.update(runtime_seconds=21590, resource_cpu=0,
        credential_metadata={"pid": 100, "start_ticks": 200, "boot_id": "11111111-1111-1111-1111-111111111111",
            "uid": 1001, "gid": 1001, "fd": 4, "name": "rc6-readonly-api", "bytes": 20})
    controller = copy.deepcopy(original_capture.receipt["native_root_proof"]["external_guardian"]["controller"])
    raw = ("[Service]\n" + "\n".join(custody.service_properties(request["namespace_receipt"]["path"],
        request["runtime_seconds"], resource_cpu=0, g5a=True)) + "\nLoadCredential="
        + custody._g5a_credential_value(request["credential_metadata"]) + "\n").encode()
    original = {"path": "/run/systemd/transient/" + controller["unit"], "raw_base64": base64.b64encode(raw).decode(),
        "sha256": replay.sha(raw), "identity": [33, 99, 0, 0, 0o100600]}
    controller["transient_unit"] = {**original, **custody.parse_unit_properties(raw)}
    snapshot = {"schema": "porota.rc6.root-owned-transient-unit-diagnostic.v1", "unit": controller["unit"],
        "binding": request["binding"], "source_sha": request["source_sha"], "source_tree": request["source_tree"],
        "original_unit": original}
    assert replay._unit(controller, snapshot, request=request) == controller["actor"]
    # Coherently rehashing a 2 GiB limit must still fail the fixed1 GiB Source contract.
    changed = raw.replace(b"MemoryMax=1073741824", b"MemoryMax=2147483648")
    original.update(raw_base64=base64.b64encode(changed).decode(), sha256=replay.sha(changed))
    controller["transient_unit"] = {**original, **custody.parse_unit_properties(changed)}
    with pytest.raises(ValueError, match="TRANSIENT_SECURITY_CHANGED:MemoryMax"):
        replay._unit(controller, snapshot, request=request)
