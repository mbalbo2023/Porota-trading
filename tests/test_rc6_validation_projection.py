from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import rc6_validation_projection as projection


def test_old_preopen_receipt_cannot_promote_history_green(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    receipt = {
        "source": "rc6-preopen-freshness-audit/read-only", "state": "READY",
        "generated_at": (now - timedelta(days=3)).isoformat(),
        "expected_session_date": "2026-10-20",
        "metrics": {"available": True, "expected_session_date": "2026-10-20",
                    "target_total": 2, "fresh_total": 2},
    }
    (tmp_path / "preopen_freshness_rc6.json").write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setenv("POROTA_VALIDATION_ROOT", str(tmp_path))
    monkeypatch.setenv("POROTA_PAPER_DB", str(tmp_path / "missing.db"))
    # A stale receipt requires a fresh DB read; when unavailable it fails closed.
    assert projection._freshness_pulse()["available"] is False


def test_old_operational_day_remains_historical(monkeypatch):
    rows = {code: {} for code in ("M2", "M3", "M4")}
    monkeypatch.setattr(projection.dynamic, "_iol_coverage", lambda: (0, 0))
    monkeypatch.setattr(projection, "_operational_pulse", lambda: {"days": [
        {"date_ar": "2026-09-10", "decisions": 1, "fills": 1, "closed": 1}]})
    monkeypatch.setattr(projection, "_freshness_pulse", lambda: {"available": False})
    result = projection._apply_daily_operational_audit(rows)
    assert result["M4"]["claim_status"] == "VERIFIED_HISTORICAL"
    assert result["M4"]["evidence_status"] == "HISTORICAL"
    assert projection.dynamic.summary(result)["milestones_green"] == 0


def test_daily_projection_latches_only_fully_verified_snapshot(tmp_path):
    path = projection.snapshot_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 3,
        "ledger_status": "FULLY_VERIFIED",
        "milestones": {"M2": {
            "state": "GREEN",
            "compliance_pct": 100,
            "observed_evidence": "evidence",
            "deviation": "none",
            "blocker": "none",
            "next_action": "retain",
        }},
    }), encoding='utf-8')

    payload = projection.refresh(tmp_path, full_verify=False)

    assert payload["ledger_status"] == "DAILY_COMPACT"
    assert payload["milestones"]["M2"]["state"] == "YELLOW"
    assert payload["milestones"]["M2"]["claim_status"] == "VERIFIED_HISTORICAL"
    assert payload["milestones"]["M2"]["observed_evidence"] == "evidence"


def test_daily_projection_does_not_trust_unverified_snapshot(tmp_path):
    path = projection.snapshot_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 2,
        "ledger_status": "DAILY_COMPACT",
        "milestones": {"M2": {"state": "GREEN", "compliance_pct": 100}},
    }), encoding='utf-8')

    payload = projection.refresh(tmp_path, full_verify=False)

    assert payload["milestones"]["M2"]["state"] == "GRAY"

def test_explicit_full_verify_is_the_only_path_that_requests_deep_db_check(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(projection.dynamic, "evaluate",
                        lambda records, *, deep_db_check: captured.setdefault("deep", deep_db_check) or {})
    monkeypatch.setattr(projection.dynamic, "summary", lambda rows: {"milestones_green": 0, "milestones_total": 0, "critical_red": [], "dynamic": True})

    projection.refresh(tmp_path, full_verify=False)

    assert captured["deep"] is False
