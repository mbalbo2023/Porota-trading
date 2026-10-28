"""Deploy collector admission: only a newly observed IOL source can be live."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts import rc6_iol_deploy_refresh_gate as gate


NOW = datetime(2026, 10, 28, 14, 0, tzinfo=timezone.utc)


def live_payload(age_seconds: int = 0) -> dict:
    stamp = (NOW - timedelta(seconds=age_seconds)).isoformat()
    return {
        "source": "IOL_MCP", "cache_state": "LIVE_FRESH",
        "decision_effect": "OBSERVE_ONLY", "real_money_authorized": False,
        "continuation_state": gate.CONTINUATION_STATE,
        "fallback_order": gate.FALLBACK_ORDER,
        "last_known_good_at": stamp, "refreshed_at": stamp,
        "live_sections": ["caucion:ARS"],
        "section_states": {"caucion:ARS": "LIVE_FRESH"},
    }


@pytest.mark.parametrize("age,expected", (
    (0, "FRESH"), (179, "FRESH"), (180, "REFRESH_REQUIRED"),
    (240, "REFRESH_REQUIRED"), (241, "REFRESH_REQUIRED"),
    (-1, "REFRESH_REQUIRED"),
))
def test_plan_reuses_recent_proof_without_repeating_collector_calls(age, expected):
    assert gate.plan(live_payload(age), NOW) == expected


@pytest.mark.parametrize("field,value", (
    ("last_known_good_at", None), ("refreshed_at", None),
    ("source", "MIGRATED"), ("decision_effect", "ROUTE"),
    ("real_money_authorized", True), ("live_sections", []),
    ("section_states", {"caucion:ARS": "CACHE_STALE"}),
))
def test_plan_cannot_reuse_unproven_live_claim(field, value):
    payload = live_payload()
    payload[field] = value
    if field in {"real_money_authorized", "decision_effect"}:
        with pytest.raises(gate.RefreshGateError):
            gate.plan(payload, NOW)
    else:
        assert gate.plan(payload, NOW) == "REFRESH_REQUIRED"


def test_optional_source_failure_has_only_safe_amber_fallback():
    payload = live_payload(65 * 3600)
    payload["last_known_good_at"] = None
    assert gate.plan(payload, NOW) == "REFRESH_REQUIRED"
    assert gate.fallback(payload, NOW) == "SOURCE_UNAVAILABLE"
    payload["last_known_good_at"] = payload["refreshed_at"]
    assert gate.fallback(payload, NOW) == "CACHE_STALE"
    payload["fallback_order"] = ["IOL_LAST_KNOWN_GOOD"]
    with pytest.raises(gate.RefreshGateError, match="IOL_FAILSAFE_CONTRACT_INVALID"):
        gate.fallback(payload, NOW)


def test_fallback_action_records_amber_without_faking_freshness(tmp_path, capsys, monkeypatch):
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz or timezone.utc)

    monkeypatch.setattr(gate, "datetime", FixedClock)
    cache = tmp_path / "reference.json"
    cache.write_text(json.dumps(live_payload(65 * 3600)))
    assert gate.main(["--action", "fallback", "--path", str(cache)]) == 0
    assert "RC6_IOL_DEPLOY_REFRESH=AMBER|effective=CACHE_STALE" in capsys.readouterr().out
    payload = live_payload(65 * 3600)
    payload["real_money_authorized"] = True
    cache.write_text(json.dumps(payload))
    assert gate.main(["--action", "fallback", "--path", str(cache)]) == 2
    assert "RC6_IOL_DEPLOY_REFRESH=RED|IOL_FAILSAFE_CONTRACT_INVALID" in capsys.readouterr().out


@pytest.mark.parametrize("state", ("CACHE_FRESH", "CACHE_STALE", "SOURCE_UNAVAILABLE"))
def test_deploy_requires_new_source_proof_even_with_historical_fallback(state):
    payload = live_payload(86_400)
    payload["cache_state"] = state
    if state == "SOURCE_UNAVAILABLE":
        payload.pop("last_known_good_at")
    assert gate.plan(payload, NOW) == "REFRESH_REQUIRED"
    with pytest.raises(gate.RefreshGateError, match="IOL_COLLECTOR_LIVE_SOURCE_UNPROVEN"):
        gate.verify(payload, NOW - timedelta(seconds=1), NOW)


def test_only_new_observation_from_current_one_shot_can_pass():
    started = NOW - timedelta(seconds=10)
    gate.verify(live_payload(1), started, NOW)
    with pytest.raises(gate.RefreshGateError, match="IOL_COLLECTOR_NEW_SAMPLE_UNPROVEN"):
        gate.verify(live_payload(30), started, NOW)


@pytest.mark.parametrize("field", ("last_known_good_at", "refreshed_at"))
def test_rewritten_timestamp_cannot_replace_provider_lkg(field):
    payload = live_payload(1)
    payload[field] = (NOW - timedelta(seconds=250)).isoformat()
    with pytest.raises(gate.RefreshGateError, match="IOL_COLLECTOR_NEW_SAMPLE_UNPROVEN"):
        gate.verify(payload, NOW - timedelta(seconds=10), NOW)


def test_collector_success_without_a_live_section_is_red():
    payload = live_payload(1)
    payload["live_sections"] = []
    with pytest.raises(gate.RefreshGateError, match="IOL_COLLECTOR_LIVE_SECTION_UNPROVEN"):
        gate.verify(payload, NOW - timedelta(seconds=10), NOW)


def test_workflow_refreshes_before_every_snapshot_without_arming_timers():
    source = (Path(__file__).parents[1] / ".github/workflows/porota-deploy-v2-promote.yml").read_text()
    migrated = source.index("IOL_REFERENCE_CONTRACT_MIGRATION=GREEN")
    immediate_refresh = source.index("ensure_iol_refresh immediate")
    immediate_audit = source.index("AUDIT_BASE=/app/data/deploy/zero-known-error-immediate.json")
    stability_loop = source.index("for cycle in 1 2 3; do", immediate_audit)
    stability_refresh = source.index('ensure_iol_refresh "stability-$cycle"', stability_loop)
    stability_audit = source.index('python /app/scripts/rc6_zero_known_error_runtime_audit.py', stability_loop)
    restored = source.index("id: timers_restore")
    assert migrated < immediate_refresh < immediate_audit < stability_refresh < stability_audit < restored
    body = source[source.index("ensure_iol_refresh() {"):immediate_refresh]
    assert "--action plan" in body and "--action verify" in body
    assert "systemctl start porota-iol-shadow-collector-rc6.service" in body
    assert "timeout 230" in body and "--started-at \"$started_at\"" in body
    assert "systemctl start porota-iol-shadow-collector-rc6.timer" not in body
    assert "--action fallback" in body
    assert "RC6_IOL_SOURCE_ALERT_$label=" in body
    assert "COLLECTOR_SERVICE_FAILED" in body
    assert "COLLECTOR_SAMPLE_UNPROVEN" in body


@pytest.mark.parametrize("collector,verify,fallback,expected", (
    ("failed", "unused", "safe", 0),
    ("success", "failed", "safe", 0),
    ("failed", "unused", "unsafe", 2),
))
def test_real_workflow_shell_continues_only_with_proven_safe_fallback(
        collector, verify, fallback, expected):
    workflow = (Path(__file__).parents[1] /
                ".github/workflows/porota-deploy-v2-promote.yml").read_text()
    block = workflow.split("          ensure_iol_refresh() {\n", 1)[1]
    block = "ensure_iol_refresh() {\n" + block.split(
        "\n          ensure_iol_refresh immediate", 1)[0]
    block = "\n".join(line.removeprefix("          ") for line in block.splitlines())
    script = """set -Eeuo pipefail
REPO=/tmp
sudo() {
  case " $* " in
    *' --action plan '*) printf '%s\\n' RC6_IOL_DEPLOY_REFRESH=REFRESH_REQUIRED; return 10 ;;
    *' --action verify '*) printf '%s\\n' RC6_IOL_DEPLOY_REFRESH=RED\\|IOL_COLLECTOR_NEW_SAMPLE_UNPROVEN; return 2 ;;
    *' --action fallback '*)
      if [ "$FALLBACK" = unsafe ]; then
        printf '%s\\n' 'RC6_IOL_DEPLOY_REFRESH=RED|IOL_FAILSAFE_CONTRACT_INVALID'
        return 2
      fi
      printf '%s\\n' 'RC6_IOL_DEPLOY_REFRESH=AMBER|effective=SOURCE_UNAVAILABLE|OPTIONAL_SOURCE_UNVERIFIED|REAL_ROUTES=[]'
      return 0 ;;
    *' systemctl start '*) [ "$COLLECTOR" = success ]; return $? ;;
    *) return 0 ;;
  esac
}
""" + block + "\nif ensure_iol_refresh immediate; then echo RESULT=0; else echo RESULT=$?; fi\n"
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                            env={**os.environ, "COLLECTOR": collector, "VERIFY": verify,
                                 "FALLBACK": fallback}, check=False)
    assert result.returncode == 0, result.stderr
    assert f"RESULT={expected}" in result.stdout
    if expected == 0:
        assert "RC6_IOL_SOURCE_ALERT_immediate=" in result.stdout
    else:
        assert "RC6_IOL_SOURCE_CONTRACT_immediate=RED" in result.stdout


def test_collector_unit_is_bounded_observational_and_no_real_route():
    unit = (Path(__file__).parents[1] / "systemd/porota-iol-shadow-collector-rc6.service").read_text()
    assert "Type=oneshot" in unit and "TimeoutStartSec=210s" in unit
    assert "POROTA_IOL_SHADOW_MODE=OBSERVE_ONLY" in unit
    assert "ExecStart=/usr/bin/python3 /opt/porota-trading/scripts/rc6_source_cycle_dispatch.py" in unit


@pytest.mark.parametrize("unsafe", ("symlink", "hardlink", "oversize"))
def test_gate_rejects_unowned_or_unbounded_cache_file(tmp_path, capsys, unsafe):
    target = tmp_path / "reference.json"
    target.write_text("{}", encoding="utf-8")
    cache = tmp_path / "iol_family_reference_latest.json"
    if unsafe == "symlink":
        cache.symlink_to(target)
    elif unsafe == "hardlink":
        os.link(target, cache)
    else:
        with cache.open("wb") as stream:
            stream.truncate(gate.MAX_REFERENCE_BYTES + 1)
    assert gate.main(["--action", "plan", "--path", str(cache)]) == 2
    output = capsys.readouterr().out
    assert "RC6_IOL_DEPLOY_REFRESH=RED|" in output
