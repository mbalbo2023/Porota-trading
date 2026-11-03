import json

import rc6_postclose_review_dashboard as view


def test_postclose_page_is_bounded_read_only(monkeypatch, tmp_path):
    report = tmp_path / "postclose_review_latest.json"
    report.write_text(json.dumps({
        "status": "VERIFIED", "generated_at": "2026-09-19T17:20:00-03:00",
        "source_snapshot_generated_at": "2026-09-19T17:15:00-03:00",
        "metrics": {"closed_operations": 12, "net_pnl_ars": 10, "win_rate_pct": 50, "decisions_observed": 20},
        "operations": [{"symbol": "GGAL", "net_pnl_ars": 1, "decision_reason": "EXIT"}] * 12,
        "findings": ["NO_AUTOMATIC_STRATEGY_CHANGE_RECOMMENDED"],
        "next_actions": ["REVIEW_RESULTS_WITH_OPERATOR_BEFORE_CHANGING_RULES"],
    }), encoding="utf-8")
    monkeypatch.setattr(view, "_path", lambda: report)

    html = view.page()

    assert "Revisión humana obligatoria" in html
    assert "Cierre de rueda" in html
    assert html.count("<b>GGAL</b>") == view.MAX_OPERATIONS
    assert "no cambia reglas" in html
