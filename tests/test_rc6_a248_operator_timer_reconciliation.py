"""A248: no automatic lease; finalizer alone reconciles the owner PAPER timer.

Actual operator content and release checks are exercised using temporary
files; root ownership, mutex and Docker/systemd commands are simulated. No host,
network, PPI Watch, PAPER ledger or real broker is touched.
"""

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts import rc6_operator_paper_rearm as operator
from scripts import porota_apply_host_control_plane_v2 as host


CANDIDATE = "a" * 40
TREE = "c" * 40
IMAGE = "sha256:" + "d" * 64
ROOT = Path(__file__).resolve().parents[1]
REAL_MUTEX_GUARD = host._require_inherited_finalizer_mutex


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    data = tmp_path / "data" / "deploy"
    data.mkdir(parents=True)
    marker = data / "CURRENT_STATE_V2.json"
    frozen = data / "porota-frozen-candidate.json"
    release = {"candidate_sha": CANDIDATE, "validation_status": "VALIDATED_RUNTIME",
               "image_id": IMAGE, "mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
               "real_order_routes": "NOT_CALLED", "ppi_watch_untouched": True}
    bundle = {"candidate_sha": CANDIDATE, "candidate_tree_sha": TREE,
              "real_order_capability_required": "BLOCKED",
              "paper_mode_required": "PRODUCTION_PAPER", "build_once": True}
    marker.write_text(json.dumps(release))
    frozen.write_text(json.dumps(bundle))
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir(mode=0o755)
    receipt_path = operator_dir / operator.RECEIPT_NAME
    monkeypatch.setattr(operator, "MARKER_HOST", marker)
    monkeypatch.setattr(operator, "FROZEN_HOST", frozen)
    monkeypatch.setattr(operator, "OPERATOR_DIR_HOST", operator_dir)
    monkeypatch.setattr(host.os, "geteuid", lambda: 0)
    monkeypatch.setattr(host, "_require_inherited_finalizer_mutex", lambda: None)
    monkeypatch.setattr(operator, "_root_owned_receipt",
                        lambda directory: json.loads(receipt_path.read_text()))
    # evaluate_rearm binds the real root-only reader in its default argument
    # at import time.  Inject the simulated root-owned reader explicitly so
    # the full host transition is reproducible on non-root GitHub runners too;
    # all real release, schema, provenance and PAPER/0 checks still execute.
    real_evaluate_rearm = operator.evaluate_rearm

    def evaluate_with_simulated_root_receipt(**kwargs):
        return real_evaluate_rearm(
            **kwargs, read_receipt=operator._root_owned_receipt)

    monkeypatch.setattr(operator, "evaluate_rearm", evaluate_with_simulated_root_receipt)
    state = {"enabled": "disabled", "active": "inactive", "docker_image": IMAGE}
    calls = []

    def fake_run(*args, check=True):
        calls.append(args)
        if args[:3] == ("docker", "inspect", "-f"):
            return SimpleNamespace(returncode=0, stdout="true|" + state["docker_image"])
        assert args[0] == "systemctl" and args[-1] == host.PAPER_PREOPEN_TIMER, args
        action = args[1]
        if action == "is-enabled":
            return SimpleNamespace(returncode=0, stdout=state["enabled"])
        if action == "is-active":
            return SimpleNamespace(returncode=0, stdout=state["active"])
        if action in {"enable", "start", "disable"}:
            if state.get("mutation_fails"):
                if check:
                    raise subprocess.CalledProcessError(1, args)
                return SimpleNamespace(returncode=1, stdout="failed")
            if action == "enable" and state.get("partial_enable_fails"):
                state.update(enabled="enabled", active="active")
                raise subprocess.CalledProcessError(1, args)
            if action == "enable":
                assert args[2] == "--now"
                state.update(enabled="enabled", active="active")
            elif action == "start":
                state["active"] = "active"
            else:
                assert args[2] == "--now"
                state.update(enabled="disabled", active="inactive")
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError(args)

    monkeypatch.setattr(host, "_run", fake_run)
    yield marker, release, bundle, receipt_path, state, calls


def write_intent(path, *, status=operator.REARM_STATUS, schema=None,
                 historical=False):
    issued = datetime(2026, 10, 1, 14, 0, tzinfo=operator.TZ)
    row = {"schema": schema or (operator.LEGACY_REARM_SCHEMA if historical
                               else operator.REARM_SCHEMA),
           "status": status, "candidate_sha": "b" * 40,
           "candidate_tree_sha": "e" * 40,
           "operator_comment_id": 6551222297, "operator_github_login": "mbalbo2023",
           "rearm_run_id": 40970763381,
           "issued_at_ar": issued.isoformat(timespec="seconds")}
    if status == operator.REARM_STATUS:
        row.update(image_id="sha256:" + "f" * 64,
                   deploy_run_id=40970600000, deploy_run_attempt=1,
                   runtime_validation_run_id=40970600000,
                   paper_mode="PRODUCTION_PAPER", real_orders_sent=0,
                   real_order_routes="NOT_CALLED", ppi_watch_untouched=True)
        if historical:
            row["expires_at_ar"] = (issued + timedelta(days=14)).isoformat()
    path.write_text(json.dumps(row, sort_keys=True) + "\n")
    return row


def systemd_mutations(calls):
    return [call for call in calls if call[0] == "systemctl" and
            call[1] in {"enable", "start", "disable"}]


def test_expired_legacy_active_intent_recovers_preopen_after_new_release(scenario):
    _, _, _, path, state, calls = scenario
    write_intent(path, historical=True)  # Old V1 expires 15-Oct; current 1-Nov.
    result = host.reconcile_finalized_operator_paper_timer(CANDIDATE, TREE)
    assert result["status"] == "GREEN"
    assert result["intent"] == operator.REARM_STATUS
    assert result["timer_before"] == "disabled|inactive"
    assert result["timer_after"] == "enabled|active"
    assert (state["enabled"], state["active"]) == ("enabled", "active")
    assert systemd_mutations(calls) == [
        ("systemctl", "enable", "--now", host.PAPER_PREOPEN_TIMER)]
    assert result["real_orders_sent"] == 0 and result["ppi_watch_mutated"] is False
    assert not any("ppi" in " ".join(call).lower() for call in calls)
    calls.clear()
    again = host.reconcile_finalized_operator_paper_timer(CANDIDATE, TREE)
    assert again["timer_before"] == "enabled|active"
    assert systemd_mutations(calls) == []


def test_owner_pause_revokes_old_active_timer(scenario):
    _, _, _, path, state, calls = scenario
    write_intent(path, status=operator.PAUSE_STATUS)
    state.update(enabled="enabled", active="active")
    proof = host.reconcile_finalized_operator_paper_timer(CANDIDATE, TREE)
    assert proof["intent"] == operator.PAUSE_STATUS
    assert proof["timer_after"] == "disabled|inactive"
    assert systemd_mutations(calls) == [
        ("systemctl", "disable", "--now", host.PAPER_PREOPEN_TIMER)]


def test_missing_or_untrusted_intent_never_activates_timer(scenario):
    _, _, _, path, state, calls = scenario
    proof = host.reconcile_finalized_operator_paper_timer(CANDIDATE, TREE)
    assert proof["intent"] == "UNKNOWN_NO_GO"
    assert systemd_mutations(calls) == []
    path.write_text("{not json")
    state.update(enabled="enabled", active="active")
    calls.clear()
    proof = host.reconcile_finalized_operator_paper_timer(CANDIDATE, TREE)
    assert proof["intent"] == "UNKNOWN_NO_GO"
    assert systemd_mutations(calls) == [
        ("systemctl", "disable", "--now", host.PAPER_PREOPEN_TIMER)]


@pytest.mark.parametrize("changed", [
    {"validation_status": "DEPLOYED_VALIDATION_PENDING"},
    {"real_orders_sent": 1}, {"real_order_routes": "CALLED"},
    {"mode": "REAL"}, {"candidate_sha": "f" * 40},
])
def test_release_safety_red_never_touches_timer(scenario, changed):
    marker, release, _, path, _, calls = scenario
    write_intent(path, historical=True)
    marker.write_text(json.dumps({**release, **changed}))
    with pytest.raises(ValueError, match="OPERATOR_REARM_"):
        host.reconcile_finalized_operator_paper_timer(CANDIDATE, TREE)
    assert systemd_mutations(calls) == []


def test_wrong_tree_or_docker_image_never_touches_timer(scenario):
    _, _, _, path, state, calls = scenario
    write_intent(path, historical=True)
    with pytest.raises(ValueError, match="OPERATOR_REARM_HOST_CANDIDATE_MISMATCH"):
        host.reconcile_finalized_operator_paper_timer(CANDIDATE, "f" * 40)
    state["docker_image"] = "sha256:" + "9" * 64
    with pytest.raises(RuntimeError, match="RUNTIME_IMAGE_RED"):
        host.reconcile_finalized_operator_paper_timer(CANDIDATE, TREE)
    assert systemd_mutations(calls) == []


def test_mutex_is_mandatory_before_any_systemd_mutation(scenario, monkeypatch):
    _, _, _, path, _, calls = scenario
    write_intent(path, historical=True)
    monkeypatch.setattr(host, "_require_inherited_finalizer_mutex", REAL_MUTEX_GUARD)
    monkeypatch.delenv("RC6_DEPLOY_MUTEX_FD", raising=False)
    with pytest.raises(RuntimeError, match="MUTEX_REQUIRED"):
        host.reconcile_finalized_operator_paper_timer(CANDIDATE, TREE)
    assert calls == []


def test_failed_timer_action_fails_deploy_instead_of_claiming_go(scenario):
    _, _, _, path, state, calls = scenario
    write_intent(path, historical=True)
    state["mutation_fails"] = True
    with pytest.raises(RuntimeError, match="QUARANTINE_RED"):
        host.reconcile_finalized_operator_paper_timer(CANDIDATE, TREE)
    assert state["enabled"] == "disabled" and state["active"] == "inactive"
    assert [call[1] for call in systemd_mutations(calls)] == ["enable", "disable"]


def test_partial_enable_failure_is_quarantined_without_revoking_operator(scenario):
    _, _, _, path, state, calls = scenario
    original = write_intent(path, historical=True)
    state["partial_enable_fails"] = True
    with pytest.raises(RuntimeError, match="QUARANTINED_NO_GO"):
        host.reconcile_finalized_operator_paper_timer(CANDIDATE, TREE)
    assert (state["enabled"], state["active"]) == ("disabled", "inactive")
    assert json.loads(path.read_text()) == original
    assert [call[1] for call in systemd_mutations(calls)] == ["enable", "disable"]


def test_finalizer_reconciles_only_after_validated_marker_under_existing_mutex():
    workflow = yaml.safe_load((ROOT / ".github/workflows/porota-deploy-v2-promote.yml")
                              .read_text(encoding="utf-8"))
    steps = workflow["jobs"]["promote"]["steps"]
    final = next(s for s in steps if s.get("id") == "finalize_runtime")
    postfinal = next(s for s in steps if s.get("id") == "postfinal_unstaged")
    assert steps.index(final) < steps.index(postfinal)
    code = final["run"]
    assert "--phase finalize python3 - '$CANDIDATE_SHA' '$DEPLOY_SHA' '$CANDIDATE_TREE'" in code
    assert (code.index("state['validation_status'] = 'VALIDATED_RUNTIME'") <
            code.index("os.replace(tmp, path)") <
            code.index("reconcile_finalized_operator_paper_timer(candidate, tree)") <
            code.index("CURRENT_STATE_V2=GREEN|validation_status=VALIDATED_RUNTIME"))
    assert "--keep-active-timers-paused" in next(
        s["run"] for s in steps if s.get("id") == "promotion")
    assert "--action restore" in next(
        s["run"] for s in steps if s.get("id") == "timers_restore")
    assert "reconcile_finalized_operator_paper_timer" not in postfinal["run"]
