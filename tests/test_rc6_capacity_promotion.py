"""Reviewed OPEN capacity can be consumed without a later programming phase."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path

import pytest

from rc6_dynamic_universe.common import digest, identity
from rc6_dynamic_universe.promotion import (APPROVAL_SCHEMA, DEFAULT_POLICY, POLICY_PATH,
    RuntimeCapacityController, build_recommendation, resolve_capacity_policy, runtime_policy_fingerprint)
from rc6_dynamic_universe.orchestrator import EnginePolicy, UniverseOrchestrator, reconcile_endpoint_slots
from tests.test_rc6_ppi_capacity_benchmark import wire, measure
from tests.test_rc6_dynamic_orchestrator import catalog, frozen, obs, OPEN


def approved(wire):
    report = measure(wire, cadence_seconds=30)
    at = wire[0].now()
    policy = deepcopy(DEFAULT_POLICY)
    rec = build_recommendation(report, policy=policy, as_of=at)
    assert rec["status"] == "SHADOW_RECOMMENDATION"
    approval = {"schema": APPROVAL_SCHEMA, "approved": True, "reviewer": "explicit-test-review",
        "reviewed_at": at.isoformat(), "recommendation_digest": rec["recommendation_digest"],
        "runtime_policy_fingerprint": rec["runtime_policy_fingerprint"]}
    approval["approval_digest"] = digest(approval)
    policy.update(mode="APPROVED", approved_recommendation_digest=rec["recommendation_digest"])
    return policy, rec, report, approval, at


def controller(values, **kwargs):
    policy, rec, report, approval, _ = values
    return RuntimeCapacityController(environ={}, policy=policy, recommendation=rec, report=report, approval=approval, **kwargs)


def shadow_selection(values, *, family_mix=False):
    policy, rec, _, _, at = values
    assets = catalog(30)
    if family_mix:
        assets[0]["instrument_type"] = "CEDEARS"
        assets[1]["instrument_type"] = "ETFS"
    plans = {}
    for engine, profile in rec["engines"].items():
        spec = profile["profile"]
        engine_policy = EnginePolicy(engine=engine, **{k: v for k, v in spec.items() if k != "endpoints"})
        previous = None
        for tick in [*range(16), 135]:
            cut = at + timedelta(seconds=tick)
            observations = [obs(a, cut, source="PPI_MARKETDATA_INTRADAY" if engine == "SCALPING" else "PPI_MARKETDATA_CURRENT",
                endpoint="intraday" if engine == "SCALPING" else "current") for a in assets]
            previous = UniverseOrchestrator(assets, policy=engine_policy).plan(at=cut, session_open=OPEN,
                frozen=frozen(assets), capacity=profile["capacity"], observations=observations, previous=previous)
        plans[engine] = previous
    end = at + timedelta(seconds=135)
    state = controller(values).state(end)
    return {"as_of": end.isoformat(), "phase": "OPEN", "mode": "SHADOW", "engines": plans,
        "capacity_policy": state, "real_orders_sent": 0, "real_routes": "NOT_CALLED"}, assets, end


def test_shipped_policy_off_has_stable_fingerprint_and_no_activation(tmp_path):
    assert json.loads(POLICY_PATH.read_text()) == DEFAULT_POLICY
    ctl = RuntimeCapacityController(tmp_path / "missing.db", environ={}, policy=DEFAULT_POLICY)
    state = ctl.state(OPEN)
    baseline = [("GGAL", "ACCIONES", "A-24HS")]
    assert state["status"] == "OFF_BASELINE" and state["baseline_limits"] == {"EQUITY_SPOT": 20, "SCALPING": 40}
    assert ctl.selection("EQUITY_SPOT", baseline, as_of=OPEN)["selected"] == baseline
    assert ctl.state(OPEN + timedelta(seconds=60))["fingerprint"] == state["fingerprint"]
    assert not list(tmp_path.iterdir())


def test_open_recommendation_requires_explicit_separate_review_and_exact_config(wire):
    values = approved(wire)
    policy, rec, report, approval, at = values
    original = deepcopy((report, rec, approval))
    state = resolve_capacity_policy(policy, rec, report, approval, as_of=at)
    assert state["status"] == "APPROVED_DYNAMIC"
    assert state["global_budget"]["endpoint_limits"] == {"current": 15, "book": 15, "intraday": 15}
    assert state["global_budget"]["global_limit"] == 45
    assert all(e["capacity"]["safe_limit"] == 15 for e in state["engine_profiles"].values())
    assert rec["automatic_activation"] is False and rec["production_limit_modified"] is False
    assert (report, rec, approval) == original
    assert controller(values).validated_report(at) == report
    assert controller(values).state(at + timedelta(seconds=1))["fingerprint"] == controller(values).state(at)["fingerprint"]
    assert runtime_policy_fingerprint(policy) == runtime_policy_fingerprint(DEFAULT_POLICY)


@pytest.mark.parametrize("change", ["approval_missing", "approval_false", "reviewer_missing", "approval_hash",
    "configured_hash", "recommendation_hash", "evidence_hash", "fingerprint", "unmeasured_concurrency",
    "faster_cadence", "future_approval", "stale", "market_closed"])
def test_invalid_capacity_or_approval_falls_to_safe_baseline(wire, change):
    policy, rec, report, approval, at = approved(wire)
    if change == "approval_missing": approval = None
    elif change == "approval_false": approval["approved"] = False
    elif change == "reviewer_missing": approval["reviewer"] = ""
    elif change == "approval_hash": approval["approval_digest"] = "0" * 64
    elif change == "configured_hash": policy["approved_recommendation_digest"] = "0" * 64
    elif change == "recommendation_hash": rec["recommendation_digest"] = "0" * 64
    elif change == "evidence_hash": report["evidence_digest"] = "0" * 64
    elif change == "fingerprint": policy["budget"]["breaker_seconds"] += 1
    elif change == "unmeasured_concurrency": policy["max_parallel_requests"] = 2
    elif change == "faster_cadence": policy["engines"]["SCALPING"]["hot_seconds"] = 15
    elif change == "future_approval": approval["reviewed_at"] = (at + timedelta(seconds=1)).isoformat(); approval["approval_digest"] = digest({k:v for k,v in approval.items() if k != "approval_digest"})
    elif change == "stale": at += timedelta(hours=1)
    elif change == "market_closed": at += timedelta(days=1)
    state = resolve_capacity_policy(policy, rec, report, approval, as_of=at)
    assert state["status"] == "BASELINE_FAIL_CLOSED"
    assert state["baseline_limits"] == {"EQUITY_SPOT": 20, "SCALPING": 40}
    assert state["production_limits_modified"] is False and state["real_orders_sent"] == 0
    assert state["reason_codes"]


def test_shadow_mode_retains_baseline_even_with_approved_evidence(wire):
    values = approved(wire)
    values[0]["mode"] = "SHADOW"
    state = controller(values).state(values[-1])
    assert state["status"] == "SHADOW_BASELINE" and not state["production_limits_modified"]
    assert controller(values).selection("SCALPING", [1, 2], as_of=values[-1])["selected"] == [1, 2]


def test_approved_selector_consumes_real_planner_limit_cadence_without_entry_authority(wire):
    values = approved(wire)
    shadow, assets, at = shadow_selection(values)
    ctl = controller(values, shadow=shadow)
    selected = ctl.selection("SCALPING", ["legacy"], as_of=at)
    assert selected["dynamic"] and selected["limit"] == 15 and selected["cadence_seconds"] == 30
    assert len(selected["selected"]) <= 15 and selected["entry_identities"]
    assert all(k in selected["selected"] for k in selected["entry_identities"])
    assert all(not r["entry_authority"] for r in shadow["engines"]["SCALPING"]["telemetry"])
    assert shadow["engines"]["SCALPING"]["catalog_ready_count"] == len(assets)
    stale = ctl.selection("SCALPING", ["legacy"], as_of=at + timedelta(seconds=31))
    assert stale["selected"] == ["legacy"] and not stale["dynamic"]
    shadow["capacity_policy"]["configuration_fingerprint"] = "wrong"
    assert not ctl.selection("SCALPING", ["legacy"], as_of=at)["dynamic"]


def test_family_fairness_cannot_force_illiquid_series_into_deep_basket(wire):
    values = approved(wire)
    _, rec, _, _, at = values
    assets = catalog(30)
    assets[-2]["instrument_type"] = "CEDEARS"
    assets[-1]["instrument_type"] = "ETFS"
    freeze = frozen(assets)
    for row in freeze["payload"]["rows"][-2:]:
        row.update(tradeable=False, tradeability_score=0)
    freeze["digest"] = digest(freeze["payload"])
    cap = rec["engines"]["SCALPING"]["capacity"]
    previous = None
    for tick in range(16):
        cut = at + timedelta(seconds=tick)
        previous = UniverseOrchestrator(assets).plan(at=cut, session_open=OPEN, frozen=freeze,
            capacity=cap, previous=previous, observations=[obs(a, cut) for a in assets])
    assert all(r["family"] == "ACCIONES" for r in previous["telemetry"] if r["state"] in {"HOT", "WARM"})
    assert all(r["state"] == "DISCOVERY" for r in previous["telemetry"][-2:])
    assert previous["catalog_ready_count"] == 30


def test_scalping_hot_precedes_equity_hot_while_opened_remains_first():
    assets = catalog(3)
    def plan(engine, selected, opened=()):
        return {"selected": list(map(identity, selected)), "opened_priority": list(map(identity, opened)),
            "telemetry": [{"identity": identity(a), "state": "HOT", "tradeability_score": .9} for a in assets]}
    scalp, equity = EnginePolicy(), EnginePolicy(engine="EQUITY_SPOT")
    schedule = reconcile_endpoint_slots([(plan("equity", [assets[0]]), equity),
        (plan("scalp", [assets[1], assets[2]], [assets[2]]), scalp)],
        {"current": 1, "book": 2, "intraday": 2}, global_slots=2)
    assert [t["priority"] for t in schedule["tasks"]] == ["OPENED_CRITICAL", "SCALPING_HOT"]
    assert schedule["family_quota"] is False and schedule["rejected"]


def test_benchmark_workflow_emits_review_artifact_without_activation():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts/rc6_ppi_capacity_benchmark.py").read_text()
    workflow = (root / ".github/workflows/rc6-ppi-capacity-shadow.yml").read_text()
    assert "--recommendation-output" in script and "--validate-report" in script
    assert "ppi-capacity-recommendation.json" in workflow
    assert "automatic_activation" in script
    assert "approved_recommendation_digest" not in workflow
