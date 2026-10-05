"""Canonical native preopen freeze agrees with the complete planner caller."""
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from rc6_dynamic_universe.live import run_shadow
from rc6_dynamic_universe.runtime import read_runtime
from rc6_shadow_runtime.worker import session_context
from rc6_shadow_runtime.preopen import build_preopen_inputs
from scripts.rc6_issue465_stress import fixture_database, PRE, AT


def test_freeze_only_preserves_all_native_rankings_policy_identity_order_and_clocks(tmp_path):
    database = tmp_path / "source.db"
    fixture_database(database, catalog_count=53)
    inputs = read_runtime(database, as_of=PRE, row_limit=20000, query_budget_seconds=2)
    context = session_context(PRE)
    pre = build_preopen_inputs(database, None, as_of=PRE, session_open=context["opening"], cutoff=context["cutoff"])
    bundle = {**inputs, **pre, "as_of": PRE.isoformat(), "session_open": context["opening"].isoformat(),
              "preopen_cutoff": context["cutoff"].isoformat(), "frozen_at": PRE.isoformat(), "observations": []}
    complete = run_shadow(bundle)
    freeze = run_shadow(bundle, freeze_only=True)
    assert freeze["frozen"] == complete["frozen"]
    assert set(freeze["frozen"]) == {"SCALPING", "EQUITY_SPOT"}
    for member in freeze["frozen"].values():
        assert len(member["payload"]["rows"]) == 53
        assert member["payload"]["frozen_at"] == PRE.isoformat()
    assert freeze["real_orders_sent"] == 0 and freeze["real_routes"] == "NOT_CALLED"


def test_native_complete_previous_and_consumed_previous_match_every_output_and_resume(tmp_path):
    import rc6_dynamic_universe.live as live
    database = tmp_path / "source.db"
    fixture_database(database, catalog_count=31)
    context = session_context(PRE)
    inputs = read_runtime(database, as_of=PRE, row_limit=20000, query_budget_seconds=2)
    pre = build_preopen_inputs(database, None, as_of=PRE, session_open=context["opening"], cutoff=context["cutoff"])
    bundle = {**inputs, **pre, "as_of": PRE.isoformat(), "session_open": context["opening"].isoformat(),
              "preopen_cutoff": context["cutoff"].isoformat(), "frozen_at": PRE.isoformat(), "observations": []}
    previous = run_shadow(bundle)
    opened = read_runtime(database, as_of=AT, row_limit=20000, query_budget_seconds=2)
    bundle.update(opened, frozen=previous["frozen"], rankings=previous["frozen"]["SCALPING"]["payload"],
                  as_of=AT.isoformat(), frozen_at=PRE.isoformat(), sessions=bundle["sessions"])
    for clock in (AT, AT+timedelta(seconds=30), AT+timedelta(hours=5)):
        bundle["as_of"] = clock.isoformat(); prior = deepcopy(previous)
        with patch.object(live, "_plan_previous", lambda value: value):
            complete = live.run_shadow(bundle, previous=previous)
        optimized = live.run_shadow(bundle, previous=previous)
        assert optimized == complete
        assert previous == prior
        for engine in optimized["engines"].values():
            assert len(engine["instruments"]) == len(engine["telemetry"]) == 31
        previous = optimized


def test_unknown_and_malformed_previous_use_the_complete_original_contract():
    from rc6_dynamic_universe.live import _plan_previous
    for value in (None, {}, {"schema": "future", "configuration_fingerprint": "x", "planned_at": "x", "instruments": {}},
                  {"schema": "ws-perf-03-orchestrator-v1", "opaque": True}):
        assert _plan_previous(value) is value
