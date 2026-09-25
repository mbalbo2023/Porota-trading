import rc6_validation_dynamic as dynamic


def test_m11_always_remains_blocked_by_policy(monkeypatch):
    monkeypatch.setenv("POROTA_MODE", "PRODUCTION_PAPER")
    monkeypatch.setenv("EXECUTION", "SIMULATED")
    rows = dynamic.evaluate([])
    assert rows["M11"]["claim_status"] == "BLOCKED_BY_POLICY"
    assert rows["M11"]["evidence_status"] == "POLICY_BLOCKED"
    assert rows["M11"]["work_status"] == "BLOCKED"
    assert rows["M11"]["dynamic"] is True


def test_summary_is_descriptive_only():
    rows = {
        "M0": {"state": "GREEN", "claim_status": "VERIFIED_CURRENT"},
        "M1": {"state": "GRAY", "claim_status": "PENDING"},
        "M11": {"state": "RED", "claim_status": "BLOCKED_BY_POLICY"},
    }
    summary = dynamic.summary(rows)
    assert summary["milestones_green"] == 1
    assert summary["milestones_total"] == 3
    assert summary["critical_red"] == []
