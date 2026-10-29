"""A180 adversarial operator authorization contract, no host or broker calls."""
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from scripts import rc6_operator_paper_rearm as operator
import rc6_paper_admission_gate as gate


TZ = ZoneInfo("America/Argentina/Buenos_Aires")
NOW = datetime(2026, 10, 30, 10, 21, tzinfo=TZ)
CANDIDATE = "a" * 40
TREE = "b" * 40
IMAGE = "sha256:" + "c" * 64


def release(tmp_path, candidate=CANDIDATE, tree=TREE):
    marker = tmp_path / "CURRENT_STATE_V2.json"
    frozen = tmp_path / "porota-frozen-candidate.json"
    marker.write_text(json.dumps({
        "candidate_sha": candidate, "image_id": IMAGE,
        "validation_status": "VALIDATED_RUNTIME", "mode": "PRODUCTION_PAPER",
        "real_orders_sent": 0, "real_order_routes": "NOT_CALLED",
        "ppi_watch_untouched": True,
    }))
    frozen.write_text(json.dumps({
        "candidate_sha": candidate, "candidate_tree_sha": tree,
        "real_order_capability_required": "BLOCKED",
        "paper_mode_required": "PRODUCTION_PAPER", "build_once": True,
    }))
    return marker, frozen


def prepared(tmp_path):
    directory = tmp_path / "operator"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    marker, frozen = release(tmp_path)
    if os.geteuid() == 0:
        receipt = operator.install_rearm(
            candidate_sha=CANDIDATE, candidate_tree_sha=TREE,
            deploy_run_id=40900000123, deploy_run_attempt=1,
            runtime_validation_run_id=40900000123,
            operator_comment_id=6500000001, rearm_run_id=40900000999,
            now=NOW, directory=directory, marker_path=marker, frozen_path=frozen)
    else:
        # Pure authorization tests can run as the non-root GitHub runner.
        # The runtime-owned-file proof is independently checked under root.
        receipt = {
            "schema": operator.REARM_SCHEMA, "status": operator.REARM_STATUS,
            "candidate_sha": CANDIDATE, "candidate_tree_sha": TREE,
            "image_id": IMAGE, "deploy_run_id": 40900000123,
            "deploy_run_attempt": 1, "runtime_validation_run_id": 40900000123,
            "operator_comment_id": 6500000001, "operator_github_login": "mbalbo2023",
            "rearm_run_id": 40900000999, "issued_at_ar": NOW.isoformat(timespec="seconds"),
            "expires_at_ar": (NOW + timedelta(days=14)).isoformat(timespec="seconds"),
            "paper_mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
            "real_order_routes": "NOT_CALLED", "ppi_watch_untouched": True,
        }
        (directory / operator.RECEIPT_NAME).write_text(json.dumps(receipt))
    return directory, marker, frozen, receipt


def check(directory, marker, frozen, *, now=NOW, candidate=CANDIDATE, tree=TREE):
    reader = (operator._root_owned_receipt if os.geteuid() == 0
              else lambda path: json.loads((path / operator.RECEIPT_NAME).read_text()))
    return operator.evaluate_rearm(
        now=now, context="runtime", directory=directory,
        marker_path=marker, frozen_path=frozen,
        candidate_sha=candidate, candidate_tree_sha=tree, read_receipt=reader)


def test_rearm_is_separate_from_real_paper_admission(tmp_path, monkeypatch):
    directory, marker, frozen, _ = prepared(tmp_path)
    assert check(directory, marker, frozen)[0:2] == (True, "OPERATOR_REARM_AUTHORIZED")
    monkeypatch.setattr(gate, "operator_pause_reason", lambda now, **_: "")
    missing = tmp_path / "missing-preopen-v2.json"
    # Even a valid operator authorization cannot fabricate T-10/PAPER GO.
    assert gate.admission_status(now=NOW, candidate_sha=CANDIDATE,
                                 receipt_path=missing)[0] is False


def test_identity_release_mode_or_real_route_mismatch_remains_no_go(tmp_path):
    directory, marker, frozen, _ = prepared(tmp_path)
    assert not check(directory, marker, frozen, candidate="d" * 40)[0]
    assert not check(directory, marker, frozen, tree="d" * 40)[0]
    data = json.loads(marker.read_text())
    for key, bad in (("validation_status", "DEPLOYED_VALIDATION_PENDING"),
                     ("real_orders_sent", 1), ("real_order_routes", "CALLED"),
                     ("ppi_watch_untouched", False), ("mode", "REAL")):
        current = dict(data, **{key: bad})
        marker.write_text(json.dumps(current))
        assert not check(directory, marker, frozen)[0], key
    marker.write_text(json.dumps(data))
    assert check(directory, marker, frozen)[0]


def test_replay_tamper_expiration_and_root_owned_file_guard(tmp_path):
    directory, marker, frozen, receipt = prepared(tmp_path)
    assert check(directory, marker, frozen, now=NOW + timedelta(days=14))[0] is False
    receipt_path = directory / operator.RECEIPT_NAME
    payload = json.loads(receipt_path.read_text())
    payload["operator_github_login"] = "unauthorized"
    receipt_path.write_text(json.dumps(payload))
    assert not check(directory, marker, frozen)[0]
    receipt_path.write_text(json.dumps(receipt))
    if os.geteuid() == 0:
        receipt_path.chmod(0o666)
        assert not check(directory, marker, frozen)[0]
        receipt_path.chmod(0o644)
    assert check(directory, marker, frozen)[0]
    # Two different approvals for the same frozen release cannot silently
    # extend the previously issued authority lifetime.
    if os.geteuid() != 0:
        return
    with pytest.raises(ValueError, match="DUPLICATE_CANDIDATE"):
        operator.install_rearm(
            candidate_sha=CANDIDATE, candidate_tree_sha=TREE,
            deploy_run_id=40900000123, deploy_run_attempt=1,
            runtime_validation_run_id=40900000123,
            operator_comment_id=6500000002, rearm_run_id=40900000998,
            now=NOW, directory=directory, marker_path=marker, frozen_path=frozen)


def test_no_sidecar_unsafe_parent_and_symlink_blocked(tmp_path):
    if os.geteuid() != 0:
        pytest.skip("root-owned mount proof tested by root-executed case")
    directory = tmp_path / "operator"
    directory.mkdir()
    marker, frozen = release(tmp_path)
    assert not check(directory, marker, frozen)[0]
    directory.chmod(0o777)
    assert not check(directory, marker, frozen)[0]
    directory.chmod(0o755)
    target = tmp_path / "forged.json"
    target.write_text("{}")
    (directory / operator.RECEIPT_NAME).symlink_to(target)
    assert not check(directory, marker, frozen)[0]


def test_host_install_is_exact_idempotent_and_pins_issuance(tmp_path):
    if os.geteuid() != 0:
        pytest.skip("root-only install mutation")
    directory, marker, frozen, original = prepared(tmp_path)
    second = operator.install_rearm(
        candidate_sha=CANDIDATE, candidate_tree_sha=TREE,
        deploy_run_id=40900000123, deploy_run_attempt=1,
        runtime_validation_run_id=40900000123,
        operator_comment_id=6500000001, rearm_run_id=40900000999,
        now=NOW + timedelta(hours=3), directory=directory,
        marker_path=marker, frozen_path=frozen)
    assert second == original


def test_observer_mount_is_root_owned_outside_data_and_read_only(tmp_path, monkeypatch):
    import porota_mode_manager as mode
    host = tmp_path / "operator"
    monkeypatch.setattr(mode, "OPERATOR_DIR_HOST", host)
    if os.geteuid() != 0:
        with pytest.raises(RuntimeError, match="ROOT_REQUIRED"):
            mode.operator_readonly_mount()
        return
    mounted = mode.operator_readonly_mount()
    assert mounted == ("--mount",
                       f"type=bind,source={host},target=/app/operator,readonly")
    assert host.exists()
    host.chmod(0o777)
    with pytest.raises(RuntimeError, match="DIRECTORY_UNTRUSTED"):
        mode.operator_readonly_mount()
