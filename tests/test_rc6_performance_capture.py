import json
import sqlite3
import sys
import time
from pathlib import Path
from dataclasses import replace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from be_paper_engine import PaperStore, PaperBroker, D, Quote
from bm_exit_supervisor import PositionExitSupervisor
from rc6_performance.capture import BoundedCapture, EvidenceStore, ExitTelemetry
from datetime import datetime, timedelta, timezone


def quote(price="100", minute=0, at=None):
    at = at or (datetime(2026, 8, 25, 14, tzinfo=timezone.utc)+timedelta(minutes=minute)).isoformat()
    price = D(price)
    return Quote("GGAL", "ACCIONES", "A-24HS", price, price-D(".1"), price+D(".1"),
                 D(1000), D(1000), at, currency="ARS", market="BYMA", metadata_source="TEST_FIXTURE",
                 book_at=at, trade_at=at, last_kind="TRADE")


@pytest.fixture(autouse=True)
def isolate_execution_ledger_from_admission_policy(monkeypatch):
    # These synthetic cases test capture/exit execution, not admission policy.
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "OBSERVATION_ONLY")


def test_capture_is_read_only_incremental_atomic_and_does_not_backfill(tmp_path):
    source = PaperStore(str(tmp_path/"source.db"))
    source.add_quote(quote())
    target = EvidenceStore(tmp_path/"evidence.db")
    capture = BoundedCapture(source.path, target)
    start = capture.read_batch()
    assert start["rows"] == 0 and start["started_at_tail"]
    source.add_quote(quote(minute=1))
    a = capture.read_batch()
    assert a["rows"] == 1
    assert capture.read_batch()["rows"] == 0
    resumed = BoundedCapture(source.path, target)
    assert resumed.read_batch()["rows"] == 0
    with source.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 2
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='events'").fetchone()


def test_read_only_capture_survives_real_wal_writer_without_waiting_twenty_seconds(tmp_path):
    source = PaperStore(str(tmp_path/"source.db"))
    capture = BoundedCapture(source.path, EvidenceStore(tmp_path/"evidence.db"))
    capture.read_batch()
    blocker = sqlite3.connect(source.path)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        at = time.monotonic()
        assert capture.read_batch()["status"] == "CAPTURED"
        assert time.monotonic()-at < .5
    finally:
        blocker.rollback()
        blocker.close()


def test_source_paper_state_and_separate_database_are_required(tmp_path):
    source = PaperStore(str(tmp_path/"source.db"))
    with pytest.raises(ValueError, match="SEPARATE"):
        BoundedCapture(source.path, EvidenceStore(source.path))
    target = EvidenceStore(tmp_path/"evidence.db")
    capture = BoundedCapture(source.path, target)
    source.state(real_orders_sent=1)
    with pytest.raises(ValueError, match="SAFETY"): capture.read_batch()


def test_tampered_immutable_snapshot_does_not_advance_checkpoint(tmp_path):
    source = PaperStore(str(tmp_path/"source.db"))
    target = EvidenceStore(tmp_path/"evidence.db")
    capture = BoundedCapture(source.path, target)
    capture.read_batch()
    source.record_decision("k", quote(), "HOLD", D(".1"), "test", {})
    with source.connect() as c:
        c.execute("UPDATE decision_evidence_snapshots SET payload_sha256='bad'")
    with pytest.raises(ValueError, match="HASH"): capture.read_batch()
    with target.connect() as c:
        assert c.execute("SELECT last_rowid FROM cursors WHERE source_table='decision_evidence_snapshots'").fetchone()[0] == 0


def test_capture_storage_quota_is_explicit(tmp_path):
    source = PaperStore(str(tmp_path/"source.db"))
    target = EvidenceStore(tmp_path/"evidence.db", maximum_bytes=1)
    assert BoundedCapture(source.path, target).read_batch()["status"] == "EVIDENCE_CAPACITY_REACHED"


def test_exit_probe_tracks_commit_and_final_fill_and_does_not_rewrite(tmp_path):
    source = PaperStore(str(tmp_path/"source.db"))
    broker = PaperBroker(source)
    q = quote(at="2026-08-28T11:00:00-03:00")
    assert broker._open(q, D(".8"), {})[0]
    p = source.open_positions()[0]
    telemetry = ExitTelemetry(tmp_path/"evidence.db")
    at = "2026-08-28T11:01:00-03:00"
    supervisor = PositionExitSupervisor(broker, clock_fn=lambda: at, telemetry=telemetry)
    q = quote(price="90", at=at)
    assert supervisor.supervise(p, q, at).state == "CLOSED"
    with telemetry.store.connect() as c:
        rows = c.execute("SELECT stage,at FROM exit_probes ORDER BY stage").fetchall()
        assert {r[0] for r in rows} == {"CONDITION", "INTENT_COMMITTED", "FINAL_FILL"}
    telemetry.record(p["paper_id"], "CONDITION", "2026-08-28T12:00:00-03:00", "OTHER")
    with telemetry.store.connect() as c:
        assert c.execute("SELECT at FROM exit_probes WHERE stage='CONDITION'").fetchone()[0] == at


def test_diagnostic_failure_cannot_block_exit_or_fake_fill(tmp_path):
    source = PaperStore(str(tmp_path/"source.db"))
    broker = PaperBroker(source)
    q = quote(at="2026-08-28T11:00:00-03:00")
    assert broker._open(q, D(".8"), {})[0]
    p = source.open_positions()[0]
    class Broken:
        def record(self, *args): raise sqlite3.OperationalError("database is locked")
    at = "2026-08-28T11:01:00-03:00"
    sup = PositionExitSupervisor(broker, clock_fn=lambda: at, telemetry=Broken())
    assert sup.supervise(p, quote(price="90", at=at), at).state == "CLOSED"
    assert len(source.recent_closed()) == 1
