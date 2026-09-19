import json
from pathlib import Path

import rc6_decision_evidence_view as view


def _write(root: Path, payload: dict) -> None:
    path = view.snapshot_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_missing_artifact_is_honest_and_read_only(tmp_path: Path):
    result = view.read(tmp_path)
    assert result["available"] is False
    assert result["total_decisions"] == 0
    assert result["source"] == "SIN_PUBLICACION"
    assert result["policy"] == "READ_ONLY_DASHBOARD_NO_DECISION_OR_ORDER_CHANGE"


def test_normalises_profiles_and_bounds_dashboard_rows(tmp_path: Path):
    decisions = [
        {
            "decision_key": f"d-{index}", "symbol": "ggal",
            "evidence_status": "VERIFIED" if index == 0 else "PENDING",
            "decision": "HOLD", "sources": {"PPI": {"freshness": "FRESH"}},
            "profiles": {"SHADOW_BALANCED_V1": {"decision": "OPEN", "state": "PENDING"}},
        }
        for index in range(12)
    ]
    _write(tmp_path, {"source": "rc6-postclose", "decisions": decisions})
    result = view.read(tmp_path)
    assert result["available"] is True
    assert result["total_decisions"] == 12
    assert len(result["rows"]) == 10
    assert result["rows"][0]["symbol"] == "GGAL"
    assert result["rows"][0]["state"] == "VERIFIED"
    assert "PPI: FRESH" in result["rows"][0]["sources"]
    assert {item["name"] for item in result["rows"][0]["profiles"]} >= {
        "BASELINE_CONSERVATIVE_V1", "SHADOW_BALANCED_V1", "SHADOW_AGGRESSIVE_V1",
    }


def test_unknown_or_invalid_evidence_fails_closed(tmp_path: Path):
    _write(tmp_path, {"decisions": [{
        "decision_id": "x", "symbol": "YPFD", "evidence_status": "made_up",
        "profiles": [{"profile": "SHADOW_AGGRESSIVE_V1", "state": "WRONG"}],
    }]})
    row = view.read(tmp_path)["rows"][0]
    assert row["state"] == "INSUFFICIENT_EVIDENCE"
    aggressive = next(item for item in row["profiles"] if item["name"] == "SHADOW_AGGRESSIVE_V1")
    assert aggressive["state"] == "INSUFFICIENT_EVIDENCE"
