"""Deploy collector admission: only a newly observed IOL source can be live."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

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
    if field in {"last_known_good_at", "real_money_authorized"}:
        with pytest.raises(gate.RefreshGateError):
            gate.plan(payload, NOW)
    else:
        assert gate.plan(payload, NOW) == "REFRESH_REQUIRED"


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
