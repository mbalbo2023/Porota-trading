from pathlib import Path

import es_shadow_binding_contract_rc6 as contract


def test_learning_policies_stay_shadow_except_authorized_sector_risk_guard():
    rows = {r.key: r for r in contract.current_learning_contracts()}
    assert set(rows) == set(contract.LEARNING_POLICIES)
    sector = rows["SECTOR_CONCENTRATION"]
    assert sector.stage == "BINDING_PAPER"
    assert sector.authority == "BINDING_PAPER"
    assert sector.can_block_paper is True
    for key, row in rows.items():
        if key == "SECTOR_CONCENTRATION":
            continue
        assert row.stage == "SHADOW"
        assert row.authority == "SHADOW"
        assert row.can_block_paper is False
    assert all(row.automatic_promotion is False for row in rows.values())

def test_hard_safety_blocks_are_separate_from_learning_policies():
    assert "REAL_ORDER_CAPABILITY_BLOCKED" in contract.HARD_SAFETY_BLOCKS
    assert "REAL_ORDERS_SENT_ZERO" in contract.HARD_SAFETY_BLOCKS
    assert "DERIVATIVES_REAL_EXECUTION_BLOCKED" in contract.HARD_SAFETY_BLOCKS
    assert not (set(contract.HARD_SAFETY_BLOCKS) & set(contract.LEARNING_POLICIES))


def test_binding_paper_requires_explicit_evidence_release_tests_and_authorization():
    base = dict(current_stage="ELIGIBLE_FOR_BINDING_DECISION",
                requested_stage="BINDING_PAPER",
                explicit_operator_authorization=True,
                versioned_release=True,
                evidence_sufficient=True,
                tests_green=True)
    assert contract.promotion_is_authorized(**base) is True
    for field in ("explicit_operator_authorization", "versioned_release",
                  "evidence_sufficient", "tests_green"):
        row = dict(base)
        row[field] = False
        assert contract.promotion_is_authorized(**row) is False


def test_no_function_can_auto_promote():
    snap = contract.contract_snapshot()
    assert snap["automatic_promotion"] is False
    assert snap["real_money_authorized"] is False


def test_counterfactual_outcomes_are_classified_for_learning():
    assert contract.counterfactual_class(would_block=True, realized_net_pnl=-10) == "TRUE_POSITIVE_LOSS_AVOIDED"
    assert contract.counterfactual_class(would_block=True, realized_net_pnl=10) == "FALSE_POSITIVE_GAIN_REMOVED"
    assert contract.counterfactual_class(would_block=False, realized_net_pnl=10) == "TRUE_NEGATIVE_GAIN_ALLOWED"
    assert contract.counterfactual_class(would_block=False, realized_net_pnl=-10) == "FALSE_NEGATIVE_LOSS_ALLOWED"


def test_summary_compares_actual_paper_with_counterfactual_binding():
    summary = contract.summarize_counterfactuals([
        {"would_block": True, "realized_net_pnl": -10},
        {"would_block": True, "realized_net_pnl": 20},
        {"would_block": False, "realized_net_pnl": 5},
        {"would_block": False, "realized_net_pnl": -2},
        {"would_block": True, "realized_net_pnl": None},
    ])
    assert summary["evaluated"] == 5
    assert summary["losses_avoided"] == 1
    assert summary["gains_removed"] == 1
    assert summary["gains_allowed"] == 1
    assert summary["losses_allowed"] == 1
    assert summary["outcome_pending"] == 1
    assert summary["actual_paper_pnl"] == 13
    assert summary["counterfactual_pnl_if_gate_bound"] == 3
    assert summary["block_precision"] == 0.5
    assert summary["real_money_authorized"] is False


def test_repository_candidate_declares_economic_gate_shadow():
    mode = Path("porota_mode_manager.py").read_text(encoding="utf-8")
    dashboard = Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    assert '"PAPER_ECONOMIC_GATE_MODE": "SHADOW"' in mode
    assert 'PAPER_ECONOMIC_GATE_MODE = os.getenv("PAPER_ECONOMIC_GATE_MODE", "SHADOW")' in dashboard
    assert '"PAPER_ECONOMIC_GATE_MODE": "BINDING"' not in mode
