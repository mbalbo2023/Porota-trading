from copy import deepcopy

from rc6_shadow_profiles import (
    BASELINE_CONSERVATIVE_V1,
    SHADOW_AGGRESSIVE_V1,
    SHADOW_BALANCED_V1,
    evaluate_profiles,
)


def snapshot():
    return {
        "decision_key": "d-1",
        "factual": {"action": "HOLD", "score": "76", "reason": "baseline"},
        "hard_safety": {"market_open": True, "risk_limit": True, "real_orders_blocked": True},
        "candidate": {
            "action": "BUY",
            "score": "76",
            "score_threshold": "80",
            "spread_bps": "105",
            "max_spread_bps": "100",
            "confirmation_count": 2,
            "required_confirmations": 3,
        },
    }


def test_profiles_are_pure_and_baseline_is_exact_factual_reference():
    data = snapshot()
    before = deepcopy(data)
    result = evaluate_profiles(data)

    assert data == before
    assert result["writes"] is False and result["orders"] is False
    baseline = result["profiles"][BASELINE_CONSERVATIVE_V1]
    assert baseline["state"] == "FACTUAL_REFERENCE"
    assert baseline["action"] == "HOLD"


def test_profiles_use_only_explicit_hypothesis_parameters():
    result = evaluate_profiles(snapshot())["profiles"]

    assert result[SHADOW_BALANCED_V1]["state"] == "EVALUATED"
    assert result[SHADOW_BALANCED_V1]["action"] == "CANDIDATE_OPEN"
    assert result[SHADOW_AGGRESSIVE_V1]["action"] == "CANDIDATE_OPEN"
    assert result[SHADOW_AGGRESSIVE_V1]["can_affect_factual"] is False


def test_hard_safety_is_never_relaxed():
    data = snapshot()
    data["hard_safety"]["risk_limit"] = False
    result = evaluate_profiles(data)["profiles"]

    for profile in (SHADOW_BALANCED_V1, SHADOW_AGGRESSIVE_V1):
        assert result[profile]["state"] == "HARD_SAFETY_BLOCKED"
        assert result[profile]["action"] == "HOLD"


def test_missing_contemporaneous_data_fails_closed():
    data = snapshot()
    del data["candidate"]["spread_bps"]
    result = evaluate_profiles(data)["profiles"]

    assert result[SHADOW_BALANCED_V1]["state"] == "INSUFFICIENT_EVIDENCE"
    assert "SPREAD_EVIDENCE_MISSING" in result[SHADOW_BALANCED_V1]["reason_codes"]
