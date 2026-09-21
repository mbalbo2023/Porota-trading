from pathlib import Path
import json

import rc6_counterfactual_learning as counterfactual


def test_missing_snapshot_is_fail_closed(tmp_path: Path):
    view = counterfactual.read(tmp_path)
    assert view["available"] is False
    assert view["counts"]["INSUFFICIENT_EVIDENCE"] == 0


def test_requires_contemporaneous_comparable_evidence(tmp_path: Path):
    path = counterfactual.snapshot_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"entries": [{
        "candidate_id": "c-1", "symbol": "YPFD", "original_decision": "OPENED_SIMULATED",
        "evidence_at": "2026-09-17T14:00:00+00:00", "compared_at": "2026-09-17T14:00:05+00:00",
        "comparable": False, "outcome": "WOULD_CONFIRM"
    }]}), encoding="utf-8")
    row = counterfactual.read(tmp_path)["rows"][0]
    assert row["outcome"] == "INSUFFICIENT_EVIDENCE"
    assert "comparable" in row["reason"]


def test_keeps_verified_counterfactual_observational(tmp_path: Path):
    path = counterfactual.snapshot_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"entries": [{
        "candidate_id": "c-2", "symbol": "GGAL", "original_decision": "BLOCKED",
        "evidence_at": "2026-09-17T14:00:00+00:00", "compared_at": "2026-09-17T14:00:05+00:00",
        "comparable": True, "outcome": "WOULD_WARN", "reason": "IOL stale"
    }]}), encoding="utf-8")
    view = counterfactual.read(tmp_path)
    assert view["counts"]["WOULD_WARN"] == 1
    assert view["policy"] == "READ_ONLY_NO_DECISION_OR_PARAMETER_CHANGE"


def test_falls_back_to_verified_decision_evidence_publication(tmp_path: Path):
    (tmp_path / "decision_evidence_latest.json").write_text(json.dumps({
        "source": "decision_evidence_snapshots/read-only",
        "generated_at": "2026-09-21T20:00:00+00:00",
        "decisions": [{
            "decision_key": "d-1", "symbol": "SUPV", "state": "VERIFIED",
            "factual_decision": "HOLD", "evidence_at": "2026-09-21T19:59:00+00:00",
            "profiles": [{
                "name": "SHADOW_BALANCED_V1", "state": "EVALUATED",
                "action": "CANDIDATE_OPEN", "reason": "PROFILE_ENTRY_CRITERIA_MET"
            }]
        }]
    }), encoding="utf-8")
    view = counterfactual.read(tmp_path)
    assert view["available"] is True
    assert view["counts"]["WOULD_CONFIRM"] == 1
    assert "decision_evidence_snapshots/read-only" in view["source"]
