"""Byte-exact planner hashing equivalence and bounded work elimination."""
from copy import deepcopy
from datetime import timedelta

import pytest

from rc6_performance.common import canonical, digest, stamp
from rc6_shadow_runtime import funnel


AT = stamp("2026-10-05T13:35:00.000001+00:00")


def checkpoint():
    return {"started_at": (AT - timedelta(minutes=10)).isoformat(), "event_keys": {},
            "reaches": {}, "cohorts": {}, "details": [], "events_recorded": 0, "gaps": {}}


def planner(count=128):
    states, telemetry, opened = {}, [], []
    for index in range(count):
        ident = (f"Ñ{index:04d}", "ACCIONES", "BYMA", "ARS", "A-24HS")
        touched = (AT - timedelta(minutes=1)).isoformat()
        states[digest(ident)] = {"last_touched_at": touched, "promoted_at": touched,
                                "source_at": touched, "source": "PPI",
                                "attempts": [["intraday", touched, True]]}
        telemetry.append({"identity": ident, "pipeline": {stage: True for stage in
                          ("CATALOG_READY", "STRATEGY_ELIGIBLE", "TRADEABLE")},
                          "state": "HOT", "promoted_at": touched,
                          "promotion_reasons": ["NATIVE"], "rejection_reason": [],
                          "warmup_progress": {"distinct_samples": 1, "required": 3}})
        if index == 0:
            opened.append(ident)
    return {"session": "2026-10-05", "engines": {"SCALPING": {
            "instruments": states, "telemetry": telemetry, "opened_priority": opened}}}


@pytest.mark.parametrize("repeat", [False, True])
def test_planner_cache_preserves_complete_checkpoint_and_output_bytes(monkeypatch, repeat):
    report, actual = planner(), checkpoint()
    expected = deepcopy(actual)
    if repeat:
        funnel._planner_stages(actual, report, AT)
        expected = deepcopy(actual)
    current = funnel._planner_stages(actual, report, AT)
    monkeypatch.setattr(funnel._PlannerEventHashes, "keys", lambda *args: None)
    prior = funnel._planner_stages(expected, report, AT)
    assert canonical(current) == canonical(prior)
    assert canonical(actual) == canonical(expected)
    assert digest(actual) == digest(expected)


def test_every_expanded_stage_hash_stays_exact_and_cache_reduces_canonical_work(monkeypatch):
    report = planner()
    calls = []
    original = funnel.digest
    def counted(value):
        calls.append(value)
        return original(value)
    monkeypatch.setattr(funnel, "digest", counted)
    current = checkpoint()
    funnel._planner_stages(current, report, AT)
    fast_count = len(calls)
    calls.clear()
    monkeypatch.setattr(funnel._PlannerEventHashes, "keys", lambda *args: None)
    prior = checkpoint()
    funnel._planner_stages(prior, report, AT)
    assert canonical(current) == canonical(prior)
    assert fast_count < len(calls)
    assert current["events_recorded"] >= 4 * len(report["engines"]["SCALPING"]["telemetry"])


def test_cache_capacity_exhaustion_preserves_the_original_path(monkeypatch):
    cache = funnel._PlannerEventHashes()
    args = ("2026-10-05", ("Ñ", "ACCIONES", "A-24HS", "ARS", "BYMA"),
            "SCALPING_BASELINE", "NO_VERIFICADO", "NO_VERIFICADO", "NOT_APPLICABLE",
            "CATALOG_READY", "UNIVERSE_SHADOW", "first-prospective-reach", AT)
    result = cache.keys(*args)
    assert result[0] == digest([*args[:6], args[6], args[7], args[8]])
    monkeypatch.setattr(funnel, "MAX_IDENTITIES", 0)
    assert funnel._PlannerEventHashes().keys(*args) is None


def test_foreign_strings_do_not_gain_cache_authority():
    class Foreign(str):
        pass
    cache = funnel._PlannerEventHashes()
    assert cache.keys("2026-10-05", ("Ñ", "ACCIONES", "A-24HS", "ARS", "BYMA"),
                      Foreign("SCALPING_BASELINE"), "NO_VERIFICADO", "NO_VERIFICADO",
                      "NOT_APPLICABLE", "CATALOG_READY", "UNIVERSE_SHADOW", "reach", AT) is None
