import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from be_paper_engine import PaperStore, PaperBroker, D
from rc6_performance.capture import BoundedCapture, EvidenceStore, ExitTelemetry
from rc6_performance.report import evidence_report
from test_rc6_performance_capture import quote


def test_evidence_pipeline_has_immutable_rejections_without_inventing_clocks(tmp_path):
    source = PaperStore(str(tmp_path/"source.db"))
    target = EvidenceStore(tmp_path/"evidence.db")
    capture = BoundedCapture(source.path, target)
    capture.read_batch()
    q = quote()
    source.record_decision("hold", q, "HOLD", D(".1"), "Score paper debajo del umbral", {})
    source.record_decision("buy", q, "BUY", D(".8"), "candidate", {})
    source.record_gates(q, "buy", "APPROVE", "NOT_USED", "BLOCKED", "BLOCKED", "risk_limit")
    capture.read_batch()
    result = evidence_report(target.path)
    assert result["funnel"]["decisions"] == 2
    assert result["funnel"]["stages"]["BUY_CANDIDATE"] == 1
    assert result["funnel"]["stages"]["REJECTED"] == 1
    assert result["funnel"]["evidence_gaps"]["decision_at"] == 2
    assert result["shadow_gate"]["status"] == "NO_VERIFICADO"
    assert result["real_order_routes"] == []


def test_financial_pipeline_reconciles_complete_trades_and_not_missing_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "OBSERVATION_ONLY")
    source = PaperStore(str(tmp_path/"source.db"))
    broker = PaperBroker(source)
    target = EvidenceStore(tmp_path/"evidence.db")
    capture = BoundedCapture(source.path, target)
    capture.read_batch()
    q = quote()
    assert broker._open(q, D(".8"), {})[0]
    p = source.open_positions()[0]
    capture.read_batch()
    assert broker._close(p, quote(price="110", minute=1), "TEST")
    capture.read_batch()
    report = evidence_report(target.path)
    assert report["economic_cohorts"] and not report["unverified_positions"]
    assert {r["currency"] for r in report["economic_cohorts"]} == {"ARS"}
    assert report["economic_edge_validated"] is False
    # A capture beginning after the entry cannot create its missing BUY fill.
    trimmed = evidence_report(target.path, event_limit=1)
    assert trimmed["selection"]["truncated"] is True


def test_evidence_hash_mismatch_blocks_report(tmp_path):
    source = PaperStore(str(tmp_path/"source.db"))
    target = EvidenceStore(tmp_path/"evidence.db")
    capture = BoundedCapture(source.path, target)
    capture.read_batch()
    source.add_quote(quote())
    capture.read_batch()
    with target.connect() as c: c.execute("UPDATE events SET payload_sha256='bad'")
    with pytest.raises(ValueError, match="HASH"): evidence_report(target.path)


def test_telemetry_lock_is_bounded_and_drop_is_explicit(tmp_path, caplog):
    telemetry = ExitTelemetry(tmp_path/"evidence.db")
    blocker = telemetry.store.connect()
    blocker.execute("BEGIN IMMEDIATE")
    try:
        telemetry.record("p", "CONDITION", "2026-09-09T14:00:00Z", "STOP_PAPER")
        assert "EXIT_TELEMETRY_DROPPED" in caplog.text
    finally:
        blocker.rollback()
        blocker.close()
