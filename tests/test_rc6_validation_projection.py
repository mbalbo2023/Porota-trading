from __future__ import annotations

import json

import rc6_validation_projection as projection


def test_daily_projection_latches_only_fully_verified_snapshot(tmp_path):
    path = projection.snapshot_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 2,
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
    assert payload["milestones"]["M2"]["state"] == "GREEN"
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