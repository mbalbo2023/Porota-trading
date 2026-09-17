from __future__ import annotations

import rc6_gdelt_event_risk_job as job
import rc6_gdelt_shadow as gdelt


def test_collect_projects_structured_store_read_only(monkeypatch):
    monkeypatch.setattr(job, "latest_status", lambda: {
        "state": "GREEN",
        "freshness": "FRESH",
        "finished_at": "2026-09-17T10:00:00Z",
        "events_total": 12,
    })

    result = gdelt.collect()

    assert result["mode"] == "SHADOW"
    assert result["decision_effect"] == "OBSERVE_ONLY"
    assert result["state"] == "GREEN"
    assert result["freshness"] == "FRESH"
    assert result["articles_count"] == 12


def test_legacy_refresh_never_performs_network():
    result = gdelt.refresh(opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("network forbidden")
    ))

    assert result["state"] == "RETIRED_GENERIC_COLLECTOR"
    assert result["decision_effect"] == "OBSERVE_ONLY"
    assert result["reason"] == "USE_RC6_GDELT_EVENT_RISK_JOB"
