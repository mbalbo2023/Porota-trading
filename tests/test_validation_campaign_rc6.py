import json
from pathlib import Path

import pytest

import em_validation_campaign_rc6 as campaign


def _payload(milestone="M0", state="GREEN", pct=100):
    return {
        "milestone": milestone,
        "state": state,
        "compliance_pct": pct,
        "objective": "objetivo",
        "expected_evidence": "esperado",
        "observed_evidence": "observado",
        "expected": "debería ocurrir",
        "observed": "ocurrió",
        "deviation": "ninguna",
        "root_cause": "n/a",
        "lesson": "lección",
        "decision": "continuar",
        "blocker": "",
        "next_action": "siguiente",
        "owner": "POROTA",
        "evidence_ref": "evidence://test",
        "commit_release": "test-sha",
        "critical_path": True,
        "source": "TEST",
    }


def test_milestone_path_is_m0_to_m11_and_real_money_is_last():
    codes = [m.code for m in campaign.MILESTONES]
    assert codes == [f"M{i}" for i in range(12)]
    assert campaign.MILESTONES[-1].name == "Real-money"


def test_append_only_hash_chain_and_summary(tmp_path: Path):
    first = campaign.append_record(_payload("M0", "GREEN", 100), root=tmp_path)
    second = campaign.append_record(_payload("M1", "GREEN", 100), root=tmp_path)
    records = campaign.load_records(root=tmp_path, verify=True)
    assert [r["seq"] for r in records] == [1, 2]
    assert second["previous_hash"] == first["record_hash"]
    summary = campaign.project_summary(records)
    assert summary["milestones_green"] == 2
    assert summary["real_money_authorized"] is False
    assert summary["real_money_state"] == "BLOCKED"
    assert summary["weighted_readiness_pct"] is None
    assert summary["weighted_readiness_reason"] == "MILESTONE_WEIGHTS_NOT_OPERATOR_APPROVED"


def test_tamper_is_detected(tmp_path: Path):
    campaign.append_record(_payload(), root=tmp_path)
    path = campaign.ledger_path(tmp_path)
    row = json.loads(path.read_text(encoding="utf-8").strip())
    row["observed"] = "alterado después de grabar"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        campaign.load_records(root=tmp_path, verify=True)


def test_invalid_state_milestone_and_percentage_fail_closed(tmp_path: Path):
    bad = _payload()
    bad["state"] = "BLUE"
    with pytest.raises(ValueError, match="STATE_INVALID"):
        campaign.append_record(bad, root=tmp_path)
    bad = _payload("M99")
    with pytest.raises(ValueError, match="MILESTONE_INVALID"):
        campaign.append_record(bad, root=tmp_path)
    bad = _payload()
    bad["compliance_pct"] = 101
    with pytest.raises(ValueError, match="PERCENT_INVALID"):
        campaign.append_record(bad, root=tmp_path)


def test_latest_record_wins_without_rewriting_history(tmp_path: Path):
    campaign.append_record(_payload("M0", "YELLOW", 50), root=tmp_path)
    campaign.append_record(_payload("M0", "GREEN", 100), root=tmp_path)
    records = campaign.load_records(root=tmp_path)
    assert len(records) == 2
    assert campaign.latest_by_milestone(records)["M0"]["state"] == "GREEN"
