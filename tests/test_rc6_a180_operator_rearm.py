"""A180/A245 adversarial durable owner intent, no host or broker calls."""
import json
import hashlib
import os
import subprocess
from datetime import datetime, timedelta
from types import SimpleNamespace
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


def test_replay_tamper_persistent_intent_and_root_owned_file_guard(tmp_path):
    directory, marker, frozen, receipt = prepared(tmp_path)
    assert check(directory, marker, frozen, now=NOW + timedelta(days=366))[0] is True
    assert "expires_at_ar" not in receipt
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
    # An older Actions run cannot replay or overwrite a newer owner state.
    if os.geteuid() != 0:
        return
    with pytest.raises(ValueError, match="OLD_OWNER_ACTION_REPLAY"):
        operator.install_rearm(
            candidate_sha=CANDIDATE, candidate_tree_sha=TREE,
            deploy_run_id=40900000123, deploy_run_attempt=1,
            runtime_validation_run_id=40900000123,
            operator_comment_id=6500000002, rearm_run_id=40900000998,
            now=NOW, directory=directory, marker_path=marker, frozen_path=frozen)


def test_no_sidecar_unsafe_parent_and_symlink_blocked(tmp_path):
    if os.geteuid() != 0:
        # CI runs as a non-root user. Verify that the privileged reader
        # rejects an untrusted user-owned directory; never skip a governed
        # regression merely because the runner is less privileged.
        directory = tmp_path / "operator"
        directory.mkdir()
        with pytest.raises(ValueError, match="DIRECTORY_UNTRUSTED"):
            operator._root_owned_receipt(directory)
        return
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
        # The non-root runner must prove install is forbidden; root tests
        # additionally exercise idempotence without rewriting expiry.
        directory = tmp_path / "operator"
        directory.mkdir()
        marker, frozen = release(tmp_path)
        with pytest.raises(ValueError, match="ROOT_REQUIRED"):
            operator.install_rearm(
                candidate_sha=CANDIDATE, candidate_tree_sha=TREE,
                deploy_run_id=40900000123, deploy_run_attempt=1,
                runtime_validation_run_id=40900000123,
                operator_comment_id=6500000001, rearm_run_id=40900000999,
                now=NOW, directory=directory, marker_path=marker,
                frozen_path=frozen)
        return
    directory, marker, frozen, original = prepared(tmp_path)
    second = operator.install_rearm(
        candidate_sha=CANDIDATE, candidate_tree_sha=TREE,
        deploy_run_id=40900000123, deploy_run_attempt=1,
        runtime_validation_run_id=40900000123,
        operator_comment_id=6500000001, rearm_run_id=40900000999,
        now=NOW + timedelta(hours=3), directory=directory,
        marker_path=marker, frozen_path=frozen)
    assert second == original
    # GitHub may re-run a completed owner action with a later Actions run ID;
    # existing ACTIVE state is already durable and is not regenerated.
    retry = operator.install_rearm(
        candidate_sha=CANDIDATE, candidate_tree_sha=TREE,
        deploy_run_id=40900000123, deploy_run_attempt=1,
        runtime_validation_run_id=40900000123,
        operator_comment_id=6500000001, rearm_run_id=40900001010,
        now=NOW + timedelta(days=180), directory=directory,
        marker_path=marker, frozen_path=frozen)
    assert retry == original


def test_legacy_v1_owner_proof_is_durable_not_expired_or_release_bound(tmp_path):
    directory, marker, frozen, receipt = prepared(tmp_path)
    legacy = dict(receipt, schema=operator.LEGACY_REARM_SCHEMA,
                  expires_at_ar=(NOW + timedelta(days=14)).isoformat(timespec="seconds"))
    (directory / operator.RECEIPT_NAME).write_text(json.dumps(legacy))
    years_later = NOW + timedelta(days=730)
    ok, reason, proof = check(directory, marker, frozen, now=years_later)
    assert (ok, reason) == (True, "OPERATOR_REARM_AUTHORIZED")
    assert proof["operator_intent_schema"] == operator.LEGACY_REARM_SCHEMA
    assert "expires_at_ar" not in proof

    # The original SHA/tree/image is audit provenance. A new exact validated
    # PAPER/0 installed release does not reset the operator's explicit intent.
    next_sha, next_tree = "d" * 40, "e" * 40
    release(tmp_path, candidate=next_sha, tree=next_tree)
    ok, reason, proof = check(directory, marker, frozen, now=years_later,
                              candidate=next_sha, tree=next_tree)
    assert (ok, reason) == (True, "OPERATOR_REARM_AUTHORIZED")
    assert proof["candidate_sha"] == next_sha
    assert proof["candidate_tree_sha"] == next_tree
    assert proof["operator_comment_id"] == receipt["operator_comment_id"]
    assert proof["image_id"] == IMAGE

    # V1 is accepted only if its historical envelope was genuinely valid.
    bad = dict(legacy, expires_at_ar=(NOW + timedelta(days=32)).isoformat(timespec="seconds"))
    (directory / operator.RECEIPT_NAME).write_text(json.dumps(bad))
    assert not check(directory, marker, frozen, now=years_later,
                     candidate=next_sha, tree=next_tree)[0]


def test_missing_receipt_or_current_release_proof_never_admits(tmp_path):
    marker, frozen = release(tmp_path)
    directory = tmp_path / "operator"
    directory.mkdir(mode=0o755)
    assert check(directory, marker, frozen)[0] is False
    active = tmp_path / "active"
    active.mkdir()
    directory, marker, frozen, receipt = prepared(active)
    assert check(directory, marker, frozen)[0]
    for path in (marker, frozen):
        original = path.read_bytes()
        path.unlink()
        assert not check(directory, marker, frozen)[0]
        path.write_bytes(original)
    marker_json = json.loads(marker.read_text())
    marker_json["real_orders_sent"] = 1
    marker.write_text(json.dumps(marker_json))
    assert not check(directory, marker, frozen)[0]


def test_root_owned_pause_rearm_transition_and_audit_history(tmp_path):
    if os.geteuid() != 0:
        marker, frozen = release(tmp_path)
        with pytest.raises(ValueError, match="ROOT_REQUIRED"):
            operator.install_pause(candidate_sha=CANDIDATE,
                                   candidate_tree_sha=TREE,
                                   operator_comment_id=6500000002,
                                   rearm_run_id=40900001000,
                                   directory=tmp_path / "operator")
        return
    directory, marker, frozen, active = prepared(tmp_path)
    original_bytes = (directory / operator.RECEIPT_NAME).read_bytes()
    paused = operator.install_pause(
        candidate_sha=CANDIDATE, candidate_tree_sha=TREE,
        operator_comment_id=6500000002, rearm_run_id=40900001000,
        now=NOW + timedelta(minutes=1), directory=directory)
    assert paused["status"] == operator.PAUSE_STATUS
    assert paused["operator_comment_id"] > active["operator_comment_id"]
    proof = operator.trusted_operator_pause(
        now=NOW + timedelta(minutes=2), directory=directory)
    assert proof["status"] == operator.PAUSE_STATUS
    assert proof["receipt_sha256"] == hashlib.sha256(
        (directory / operator.RECEIPT_NAME).read_bytes()).hexdigest()
    assert proof["operator_comment_id"] == paused["operator_comment_id"]
    assert check(directory, marker, frozen, now=NOW + timedelta(days=500))[0:2] == (
        False, "OPERATOR_NO_GO_PENDING_EXPLICIT_REARM")
    history = list((directory / operator.HISTORY_DIR_NAME).glob("*.json"))
    assert len(history) == 1 and history[0].read_bytes() == original_bytes

    # PAUSE remains effective with a degraded/absent runtime, and is exactly
    # idempotent even if Actions retries its own command.
    marker.unlink()
    assert operator.install_pause(
        candidate_sha=CANDIDATE, candidate_tree_sha=TREE,
        operator_comment_id=6500000002, rearm_run_id=40900001000,
        now=NOW + timedelta(minutes=3), directory=directory) == paused
    assert len(list((directory / operator.HISTORY_DIR_NAME).glob("*.json"))) == 1
    with pytest.raises(ValueError, match="OLD_OWNER_ACTION_REPLAY"):
        operator.install_pause(
            candidate_sha=CANDIDATE, candidate_tree_sha=TREE,
            operator_comment_id=6500000001, rearm_run_id=40900000999,
            now=NOW + timedelta(minutes=4), directory=directory)
    release(tmp_path)
    with pytest.raises(ValueError, match="OLD_OWNER_ACTION_REPLAY"):
        operator.install_rearm(
            candidate_sha=CANDIDATE, candidate_tree_sha=TREE,
            deploy_run_id=40900000123, deploy_run_attempt=1,
            runtime_validation_run_id=40900000123,
            operator_comment_id=6500000001, rearm_run_id=40900000999,
            now=NOW + timedelta(minutes=5), directory=directory,
            marker_path=marker, frozen_path=frozen)
    new_active = operator.install_rearm(
        candidate_sha=CANDIDATE, candidate_tree_sha=TREE,
        deploy_run_id=40900000123, deploy_run_attempt=1,
        runtime_validation_run_id=40900000123,
        operator_comment_id=6500000003, rearm_run_id=40900001001,
        now=NOW + timedelta(minutes=6), directory=directory,
        marker_path=marker, frozen_path=frozen)
    assert new_active["status"] == operator.REARM_STATUS
    assert "expires_at_ar" not in new_active
    assert operator.trusted_operator_pause(now=NOW + timedelta(days=1),
                                          directory=directory) is None
    assert check(directory, marker, frozen, now=NOW + timedelta(days=1000))[0]
    assert len(list((directory / operator.HISTORY_DIR_NAME).glob("*.json"))) == 2


def test_pause_can_preserve_and_recover_existing_partial_history(tmp_path):
    if os.geteuid() != 0:
        return
    directory, marker, frozen, _ = prepared(tmp_path)
    original = (directory / operator.RECEIPT_NAME).read_bytes()
    history = directory / operator.HISTORY_DIR_NAME
    history.mkdir(mode=0o755)
    damaged = history / (hashlib.sha256(original).hexdigest() + ".json")
    damaged.write_bytes(b"interrupted historical archive; preserve as evidence")
    orphan = history / ".history-staging-crash-evidence"
    orphan.write_bytes(b"interrupted temporary archive; never purge")
    paused = operator.install_pause(
        candidate_sha=CANDIDATE, candidate_tree_sha=TREE,
        operator_comment_id=6500000002, rearm_run_id=40900001000,
        now=NOW + timedelta(minutes=1), directory=directory)
    assert paused["status"] == operator.PAUSE_STATUS
    assert damaged.read_bytes().startswith(b"interrupted historical")
    assert orphan.read_bytes().startswith(b"interrupted temporary")
    recovery = list(history.glob("*-recovered-*.json"))
    assert len(recovery) == 1 and recovery[0].read_bytes() == original
    assert check(directory, marker, frozen, now=NOW + timedelta(days=500))[0] is False


def test_pause_same_owner_action_new_actions_run_is_safe_to_retry(tmp_path):
    if os.geteuid() != 0:
        return
    directory, marker, frozen, _ = prepared(tmp_path)
    first = operator.install_pause(
        candidate_sha=CANDIDATE, candidate_tree_sha=TREE,
        operator_comment_id=6500000002, rearm_run_id=40900001000,
        now=NOW + timedelta(minutes=1), directory=directory)
    second = operator.install_pause(
        candidate_sha=CANDIDATE, candidate_tree_sha=TREE,
        operator_comment_id=6500000002, rearm_run_id=40900001001,
        now=NOW + timedelta(minutes=2), directory=directory)
    assert first["rearm_run_id"] == 40900001000
    assert second["rearm_run_id"] == 40900001001
    assert second["status"] == operator.PAUSE_STATUS
    assert check(directory, marker, frozen, now=NOW + timedelta(days=500))[0] is False
    assert len(list((directory / operator.HISTORY_DIR_NAME).glob("*.json"))) == 2
    with pytest.raises(ValueError, match="OLD_OWNER_ACTION_REPLAY"):
        operator.install_pause(
            candidate_sha=CANDIDATE, candidate_tree_sha=TREE,
            operator_comment_id=6500000002, rearm_run_id=40900001000,
            now=NOW + timedelta(minutes=3), directory=directory)


def test_trusted_pause_rejects_unowned_or_tampered_receipts(tmp_path):
    directory, _, _, _ = prepared(tmp_path)
    assert operator.trusted_operator_pause(now=NOW, directory=directory) is None
    if os.geteuid() != 0:
        return
    operator.install_pause(
        candidate_sha=CANDIDATE, candidate_tree_sha=TREE,
        operator_comment_id=6500000002, rearm_run_id=40900001000,
        now=NOW + timedelta(minutes=1), directory=directory)
    assert operator.trusted_operator_pause(now=NOW + timedelta(minutes=2),
                                          directory=directory)
    target = directory / operator.RECEIPT_NAME
    target.chmod(0o666)
    assert operator.trusted_operator_pause(now=NOW + timedelta(minutes=2),
                                          directory=directory) is None
    target.chmod(0o644)
    forged = json.loads(target.read_text())
    forged["operator_github_login"] = "unknown"
    target.write_text(json.dumps(forged))
    assert operator.trusted_operator_pause(now=NOW + timedelta(minutes=2),
                                          directory=directory) is None


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


def test_rearm_positive_receipt_is_committed_only_after_timer_verified(monkeypatch):
    from scripts import rc6_deploy_host_mutex as mutex

    events = []
    monkeypatch.setattr(mutex, "open_lock", lambda: os.open(os.devnull, os.O_RDONLY))
    monkeypatch.setattr(mutex, "_read_barrier", lambda _: None)

    def fake_run(args, **_):
        events.append(tuple(args))
        answer = {"enable": "", "is-enabled": "enabled\n",
                  "is-active": "active\n"}[args[1]]
        return SimpleNamespace(returncode=0, stdout=answer)

    monkeypatch.setattr(operator.subprocess, "run", fake_run)
    monkeypatch.setattr(operator, "install_rearm", lambda **_: events.append("RECEIPT") or {
        "candidate_sha": CANDIDATE, "operator_comment_id": 6500000001,
        "rearm_run_id": 40900000999,
    })
    argv = ["--candidate-sha", CANDIDATE, "--candidate-tree-sha", TREE,
            "--deploy-run-id", "40900000123", "--deploy-run-attempt", "1",
            "--runtime-validation-run-id", "40900000123",
            "--operator-comment-id", "6500000001",
            "--rearm-run-id", "40900000999"]
    assert operator.command_line(argv) == 0
    assert events == [
        ("systemctl", "enable", "--now", "porota-preopen-rc6.timer"),
        ("systemctl", "is-enabled", "porota-preopen-rc6.timer"),
        ("systemctl", "is-active", "porota-preopen-rc6.timer"),
        "RECEIPT",
    ]

    events.clear()
    monkeypatch.setattr(operator.subprocess, "run", lambda args, **_: (
        events.append(tuple(args)) or SimpleNamespace(returncode=1, stdout="")))
    with pytest.raises(RuntimeError, match="PREOPEN_TIMER_ENABLE_RED"):
        operator.command_line(argv)
    assert events == [("systemctl", "enable", "--now", "porota-preopen-rc6.timer")]


def test_pause_revocation_precedes_timer_and_survives_timer_failure(monkeypatch):
    from scripts import rc6_deploy_host_mutex as mutex

    events = []
    monkeypatch.setattr(mutex, "open_lock", lambda: os.open(os.devnull, os.O_RDONLY))
    monkeypatch.setattr(mutex, "_read_barrier", lambda _: (_ for _ in ()).throw(
        AssertionError("PAUSE must not depend on runtime deploy barrier health")))
    monkeypatch.setattr(operator, "install_pause", lambda **_: events.append("PAUSED_RECEIPT") or {
        "candidate_sha": CANDIDATE, "operator_comment_id": 6500000002,
        "rearm_run_id": 40900001000,
    })
    monkeypatch.setattr(operator, "evaluate_rearm", lambda **_: (
        False, "OPERATOR_NO_GO_PENDING_EXPLICIT_REARM", None))
    monkeypatch.setattr(operator, "_root_owned_receipt", lambda _: {
        "status": operator.PAUSE_STATUS})

    def systemctl(args, **_):
        events.append(tuple(args))
        output = {"disable": (0, ""), "is-enabled": (1, "disabled\n"),
                  "is-active": (3, "inactive\n")}[args[1]]
        return SimpleNamespace(returncode=output[0], stdout=output[1])

    monkeypatch.setattr(operator.subprocess, "run", systemctl)
    argv = ["--pause", "--candidate-sha", CANDIDATE,
            "--candidate-tree-sha", TREE,
            "--operator-comment-id", "6500000002", "--rearm-run-id", "40900001000"]
    assert operator.command_line(argv) == 0
    assert events == [
        "PAUSED_RECEIPT",
        ("systemctl", "disable", "--now", "porota-preopen-rc6.timer"),
        ("systemctl", "is-enabled", "porota-preopen-rc6.timer"),
        ("systemctl", "is-active", "porota-preopen-rc6.timer"),
    ]

    events.clear()
    monkeypatch.setattr(operator.subprocess, "run", lambda args, **_: (
        events.append(tuple(args)) or SimpleNamespace(returncode=1, stdout="")))
    with pytest.raises(RuntimeError, match="DISABLE_RED_INTENT_PERSISTED"):
        operator.command_line(argv)
    assert events == ["PAUSED_RECEIPT",
                      ("systemctl", "disable", "--now", "porota-preopen-rc6.timer")]


def test_github_owner_pause_branch_is_distinct_exact_and_serialized():
    from pathlib import Path
    import yaml

    workflow = yaml.safe_load(Path(".github/workflows/rc6-paper-rearm.yml").read_text())
    assert workflow["concurrency"] == {
        "group": "rc6-unified-paper-deploy", "cancel-in-progress": False}
    branches = workflow[True]["push"]["branches"]  # PyYAML 1.1 parses `on` as True
    assert branches == ["rc6-operator-paper-rearm-v1-*", "rc6-operator-paper-pause-v2-*"]
    jobs = workflow["jobs"]
    assert "github.actor == 'mbalbo2023'" in jobs["rearm"]["if"]
    assert "rc6-operator-paper-rearm-v1-" in jobs["rearm"]["if"]
    assert "github.actor == 'mbalbo2023'" in jobs["pause"]["if"]
    assert "rc6-operator-paper-pause-v2-" in jobs["pause"]["if"]
    code = "\n".join(step.get("run", "") for step in jobs["pause"]["steps"])
    assert "RC6_624_PAUSE_PAPER candidate=" in code
    assert "OPERATOR_PAUSE_OWNER_SCOPE_MISMATCH" in code
    assert "comment.get('created_at')!=comment.get('updated_at')" in code
    assert "deploy/rc6-pr69-isolated-20260915" in code
    assert "OPERATOR_PAUSE_SOURCE_NOT_CURRENT_PRODUCT" in code
    assert "rc6_droplet_identity_guard.py" in code
    assert "PAUSE_SCRIPT_BUNDLE_ATTESTATION_RED" in code
    assert "--pause --candidate-sha" in code
    assert "OPERATOR_NO_GO_PENDING_EXPLICIT_REARM" in code
    assert "docker run" not in code and "real_order_routes" not in code
    # Parse the actual Python heredocs (including the remote SSH read-only
    # attestation), not just workflow YAML indentation and string markers.
    for job_name in ("rearm", "pause"):
        for step in jobs[job_name]["steps"]:
            script = step.get("run", "")
            if script:
                shell = subprocess.run(["bash", "-n"], input=script, text=True,
                                       capture_output=True, timeout=5, check=False)
                assert shell.returncode == 0, (job_name, step["name"], shell.stderr)
            if "<<'PY'\n" not in script:
                continue
            python_source = script.split("<<'PY'\n", 1)[1].split("\nPY", 1)[0]
            compile(python_source, f"{job_name}:{step['name']}", "exec")
