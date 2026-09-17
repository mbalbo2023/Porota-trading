import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import rc6_validation_dynamic as dynamic
import rc6_validation_projection as projection


def test_historical_green_is_not_current_green():
    row = dynamic.evaluate([{"milestone": "M4", "state": "GREEN", "compliance_pct": 100, "observed_evidence": "old proof"}])["M4"]
    assert row["claim_status"] == "VERIFIED_HISTORICAL"
    assert row["state"] == "YELLOW"
    assert dynamic.summary({"M4": row})["milestones_green"] == 0


def test_policy_block_is_not_critical_failure():
    row = dynamic.evaluate([])["M11"]
    assert row["claim_status"] == "BLOCKED_BY_POLICY"
    assert row["state"] == "GRAY"
    assert "M11" not in dynamic.summary({"M11": row})["critical_red"]


def test_daily_projection_latches_only_verified_history(tmp_path):
    snapshot = {"schema_version": 3, "ledger_status": "FULLY_VERIFIED", "milestones": {"M4": {"state": "GREEN", "compliance_pct": 100, "observed_evidence": "proof"}}}
    projection.snapshot_path(tmp_path).write_text(json.dumps(snapshot), encoding="utf-8")
    payload = projection.refresh(tmp_path, full_verify=False)
    assert payload["milestones"]["M4"]["claim_status"] == "VERIFIED_HISTORICAL"
