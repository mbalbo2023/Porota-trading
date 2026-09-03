from decimal import Decimal

import da_dashboard_ux_hf6 as ux
import rc4_shadow_binding_validation as validation


def _complete_metrics():
    return {
        "economic_gate_mode": "SHADOW",
        "closed_operations": 65,
        "reconciled_operations": 60,
        "reconciliation_coverage_pct": "95",
        "median_gap_bp": "1.2",
        "p90_gap_bp": "7.0",
        "reconciled_winners": 2,
        "reconciled_losers": 40,
        "reconciled_families": 2,
        "unexplained_divergences": 0,
        "week3_week4_median_drift_bp": "1.0",
        "new_divergence_categories": 0,
        "divergence_alert_e2e_verified": True,
        "broker_cost_truth_source_available": True,
        "daily_reconciler_evidence": True,
        "execution_fidelity_report_available": True,
        "exit_cause_distribution_available": True,
        "slippage_gap_measured": True,
        "book_exceedance_measured": True,
        "median_close_vs_entry_pct": "0.4",
        "month_end_checked_clean": True,
        "counterfactual_reject_pct": "45",
        "wheels_elapsed": 20,
    }


def test_validacion_is_top_level():
    ux.assert_ux_invariants()
    assert "/validacion" in {item.href for item in ux.TOP_NAV}


def test_missing_evidence_never_passes():
    result = validation.evaluate({"economic_gate_mode": "SHADOW"})
    assert result.state == "VALIDATING"
    assert result.criteria_passed == 0
    assert result.reconciled_remaining == 60


def test_complete_window_only_becomes_review_eligible():
    result = validation.evaluate(
        _complete_metrics(), expected_fingerprint="same", current_fingerprint="same")
    assert result.state == "ELIGIBLE_FOR_BINDING_REVIEW"
    assert result.criteria_passed == result.criteria_total == 8
    assert result.counterfactual_assessment == "DISCRIMINACION_UTIL_PARA_REVISION"
    assert result.counterfactual_reject_pct == Decimal("45")


def test_window_invalidates_when_frozen_model_changes():
    result = validation.evaluate(
        _complete_metrics(), expected_fingerprint="before", current_fingerprint="after")
    assert result.state == "WINDOW_INVALIDATED_RESTART_REQUIRED"
    assert result.window_invalidated is True


def test_binding_never_self_certifies_the_window():
    metrics = _complete_metrics() | {"economic_gate_mode": "BINDING"}
    result = validation.evaluate(metrics)
    assert result.state == "SHADOW_REQUIRED_FOR_VALIDATION"


def test_lessons_are_evidence_only_not_generated():
    no_lesson = validation.evaluate({"economic_gate_mode": "SHADOW"})
    assert all(m.lesson_learned is None for m in no_lesson.milestones)
    with_lesson = validation.evaluate(
        {"economic_gate_mode": "SHADOW"},
        lessons={"H1": {"text": "Fuente de costo capturada", "source": "postclose:2026-09-11"}},
    )
    h1 = next(m for m in with_lesson.milestones if m.key == "H1")
    assert h1.lesson_learned == "Fuente de costo capturada"
    assert h1.lesson_source == "postclose:2026-09-11"
