import rc6_validation_dynamic as dynamic


def test_m11_always_remains_blocked(monkeypatch):
    monkeypatch.setenv("POROTA_MODE", "PRODUCTION_PAPER")
    monkeypatch.setenv("EXECUTION", "SIMULATED")
    rows = dynamic.evaluate([])
    assert rows["M11"]["state"] == "RED"
    assert rows["M11"]["dynamic"] is True


def test_summary_is_descriptive_only():
    rows = {
        "M0": {"state": "GREEN"},
        "M1": {"state": "GRAY"},
        "M11": {"state": "RED"},
    }
    summary = dynamic.summary(rows)
    assert summary["milestones_green"] == 1
    assert summary["milestones_total"] == 3
    assert summary["critical_red"] == ["M11"]
