import bg_paper_dashboard as dashboard


def test_decision_evidence_panel_is_standard_read_only_and_bounded(monkeypatch):
    rows = [
        {
            "decision_key": f"d-{index}", "symbol": "GGAL", "state": "PENDING",
            "factual_decision": "HOLD", "reason": "factual",
            "sources": "PPI: FRESH, IOL: FRESH", "at": "2026-09-19T15:00:00-03:00",
            "profiles": [
                {"name": "BASELINE_CONSERVATIVE_V1", "decision": "HOLD", "state": "PENDING"},
                {"name": "SHADOW_BALANCED_V1", "decision": "OPEN", "state": "PENDING"},
                {"name": "SHADOW_AGGRESSIVE_V1", "decision": "OPEN", "state": "PENDING"},
            ],
        }
        for index in range(10)
    ]
    monkeypatch.setattr(dashboard.decision_evidence_view, "read", lambda: {
        "available": True, "total_decisions": 12, "rows": rows,
        "counts": {"VERIFIED": 0, "PENDING": 12, "NOT_APPLICABLE": 0, "INSUFFICIENT_EVIDENCE": 0},
        "aggregate": {"profiles_compared": 12}, "source": "test-cache",
        "generated_at": "2026-09-19T15:10:00-03:00",
        "policy": "READ_ONLY_DASHBOARD_NO_DECISION_OR_ORDER_CHANGE",
    })
    rendered = dashboard._decision_evidence_panel()
    assert "Evidencia por decisión y perfiles SHADOW" in rendered
    assert "SHADOW_AGGRESSIVE_V1" in rendered
    assert "READ_ONLY_DASHBOARD_NO_DECISION_OR_ORDER_CHANGE" in rendered
    assert "ninguno puede alterar ni autorizar órdenes" in rendered
    assert rendered.count("<code>d-") == 10
    assert "12" in rendered


def test_decision_evidence_panel_explains_absence_without_claiming_failure(monkeypatch):
    monkeypatch.setattr(dashboard.decision_evidence_view, "read", lambda: {
        "available": False, "total_decisions": 0, "rows": [],
        "counts": {"VERIFIED": 0, "PENDING": 0, "NOT_APPLICABLE": 0, "INSUFFICIENT_EVIDENCE": 0},
        "aggregate": {}, "source": "SIN_PUBLICACION", "generated_at": None,
        "policy": "READ_ONLY_DASHBOARD_NO_DECISION_OR_ORDER_CHANGE",
    })
    rendered = dashboard._decision_evidence_panel()
    assert "Aún no hay evidencia por decisión publicada" in rendered
    assert "no equivale a una decisión negativa" in rendered
