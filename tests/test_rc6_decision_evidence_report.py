import json
import sqlite3

import rc6_decision_evidence_report as report


def _payload():
    return {
        "schema": "rc6.decision-inputs.v1", "decision_key": "d-1",
        "captured_at": "2026-09-19T14:00:00+00:00",
        "decision": {"symbol": "GGAL", "final_result": "OPENED_SIMULATED", "reason": "ok", "paper_id": "PAPER-1"},
        "quote_used": {"symbol": "GGAL", "last": "100"},
        "runtime": {"real_money_authorized": False}, "inputs_used": {},
    }


def _db(path, payload, digest):
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE decision_evidence_snapshots(decision_key TEXT PRIMARY KEY,captured_at TEXT,schema_version TEXT,payload_sha256 TEXT,payload_json TEXT)")
        c.execute("CREATE TABLE paper_positions(paper_id TEXT PRIMARY KEY,status TEXT,net_pnl TEXT,closed_at TEXT)")
        c.execute("INSERT INTO decision_evidence_snapshots VALUES(?,?,?,?,?)", ("d-1", "2026-09-19T14:00:00+00:00", "v1", digest, json.dumps(payload)))
        c.execute("INSERT INTO paper_positions VALUES(?,?,?,?)", ("PAPER-1", "CLOSED", "12.5", "2026-09-19T16:00:00+00:00"))


def test_report_verifies_hash_and_writes_bounded_projection(tmp_path):
    payload = _payload()
    database = tmp_path / "paper.db"
    _db(database, payload, report._canonical_hash(payload))
    result = report.build(db_path=database)
    assert result["read_only"] is True
    assert result["decisions"][0]["state"] == "VERIFIED"
    assert result["decisions"][0]["outcome"] == "CLOSED:12.5"
    assert result["decisions"][0]["profiles"][0]["name"] == "BASELINE_CONSERVATIVE_V1"
    target = report.write(result, root=tmp_path)
    assert json.loads(target.read_text(encoding="utf-8"))["decisions"][0]["decision_key"] == "d-1"


def test_bad_hash_is_insufficient_evidence(tmp_path):
    payload = _payload()
    database = tmp_path / "paper.db"
    _db(database, payload, "bad")
    result = report.build(db_path=database)
    assert result["decisions"][0]["state"] == "INSUFFICIENT_EVIDENCE"
    assert result["decisions"][0]["profiles"] == []
