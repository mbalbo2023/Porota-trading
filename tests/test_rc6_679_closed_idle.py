"""CLOSED liveness is separate from a financial cut and native OPEN freshness."""
from datetime import timedelta
import sqlite3

import pytest

from rc6_shadow_runtime.persistence import read_committed_generation
from rc6_shadow_runtime.worker import (
    ShadowRuntime, closed_input_signature, publish_closed_idle)
from rc6_shadow_runtime import worker as runtime
from rc6_shadow_runtime.persistence import EvidenceFiles
from rc6_trader_dashboard.generation import read_shadow
from tests.test_rc6_shadow_runtime_wiring import OPEN, make_store, quote


CLOSED = OPEN + timedelta(hours=7, minutes=35)  # 18:05 ART.
NEXT_PREOPEN = OPEN + timedelta(days=1, minutes=-10)


def test_closed_signature_ignores_observer_heartbeat_but_detects_source_edits(tmp_path):
    store, assets = make_store(tmp_path, count=3)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    first = closed_input_signature(worker, CLOSED)
    assert isinstance(first, str) and len(first) == 64
    with store.connect() as c:
        c.execute("UPDATE observer_state SET heartbeat_at=? WHERE id=1", (CLOSED.isoformat(),))
    assert closed_input_signature(worker, CLOSED + timedelta(seconds=30)) == first
    store.add_quote(quote(assets[0], CLOSED))
    changed = closed_input_signature(worker, CLOSED + timedelta(seconds=31))
    assert changed != first and changed is not None
    deep_before = closed_input_signature(worker, CLOSED + timedelta(seconds=31),
                                         deep_catalog=True)
    with store.connect() as c:
        c.execute("""UPDATE financial_instrument_catalog SET metadata_json='{"changed":true}'
          WHERE ticker=?""", (assets[0]["ticker"],))
    assert closed_input_signature(worker, CLOSED + timedelta(seconds=32)) == changed
    assert closed_input_signature(worker, CLOSED + timedelta(seconds=32),
                                  deep_catalog=True) != deep_before
    with store.connect() as c:
        c.execute("""INSERT OR REPLACE INTO financial_instrument_catalog
          SELECT ticker,instrument_type,market,currency,settlement,settlement_source,
            description,last_seen_at,run_id,status,capability,'{"native":"replacement"}'
          FROM financial_instrument_catalog WHERE ticker=?""", (assets[0]["ticker"],))
    assert closed_input_signature(worker, CLOSED + timedelta(seconds=33)) != changed
    assert closed_input_signature(worker, OPEN + timedelta(minutes=2)) is None


def test_closed_signature_handles_a_large_append_only_event_history(tmp_path):
    store, _ = make_store(tmp_path, count=3)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    with store.connect() as c:
        c.executemany("""INSERT INTO paper_events
          (event_at,source,event_type,paper_id,detail) VALUES(?,?,?,?,?)""",
            ((CLOSED.isoformat(), "PRODUCTION_PAPER", "OBSERVED", None, str(i))
             for i in range(4000)))
    first = closed_input_signature(worker, CLOSED)
    assert first is not None  # The full 4,000 row history does not force FULL.
    with store.connect() as c:
        c.execute("""INSERT INTO paper_events
          (event_at,source,event_type,paper_id,detail) VALUES(?,?,?,?,?)""",
          (CLOSED.isoformat(), "PRODUCTION_PAPER", "OBSERVED", None, "late"))
    assert closed_input_signature(worker, CLOSED + timedelta(seconds=30)) != first


def test_idle_marker_revokes_live_cut_and_preserves_historical_cut(tmp_path):
    store, _ = make_store(tmp_path, count=2)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    worker.tick(CLOSED)
    old = read_committed_generation(worker.root)
    ident = old["pointer"]["generation_id"]
    assert publish_closed_idle(worker, CLOSED + timedelta(seconds=30), ident)
    with pytest.raises(ValueError, match="GENERATION_IDLE_MARKET_CLOSED"):
        read_committed_generation(worker.root)
    assert read_shadow(worker.root)["state"] == "SHADOW_IDLE_MARKET_CLOSED"
    history = read_committed_generation(worker.root, require_current_status=False)
    assert history["pointer"] == old["pointer"]
    assert history["status"]["as_of"] == old["status"]["as_of"]
    with worker.files as files:
        idle = files.read("idle.json")
        assert idle["last_source_as_of"] == old["status"]["as_of"]
        assert idle["source_freshness_renewed"] is False
        assert idle["real_orders_sent"] == 0
    # A genuinely new preopen cut invalidates the old idle marker by identity.
    worker.tick(NEXT_PREOPEN)
    assert read_committed_generation(worker.root)["pointer"]["generation_id"] != ident


def test_idle_never_claims_safety_if_status_or_pointer_changes(tmp_path):
    store, _ = make_store(tmp_path, count=1)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    worker.tick(CLOSED)
    with worker.files as files:
        ident = files.read("CURRENT.json")["generation_id"]
    assert not publish_closed_idle(worker, CLOSED, "0" * 64)
    with worker.files as files:
        assert files.read("idle.json") is None
        status = files.read("status.json")
        files.write("status.json", {**status, "real_orders_sent": 1})
    assert not publish_closed_idle(worker, CLOSED, ident)
    with worker.files as files:
        assert files.read("idle.json") is None


def test_run_loop_bootstrap_then_idles_without_new_current_or_full_tick(tmp_path, monkeypatch):
    class FakeWorker:
        defer_audit = True
        source_inputs = []
        def __init__(self):
            self.files = EvidenceFiles(tmp_path / "shadow")
            self.ticks = 0
        def tick(self, as_of):
            self.ticks += 1
            ident = f"{self.ticks:064x}"
            with self.files as files:
                files.write("status.json", {"phase": "CLOSED", "status": "SHADOW_OBSERVING",
                    "as_of": as_of, "real_orders_sent": 0, "real_routes": "NOT_CALLED"})
                files.write("CURRENT.json", {"generation_id": ident})
            return {"status": "SHADOW_OBSERVING", "phase": "CLOSED"}
    instance = FakeWorker()
    class Stop:
        waits = 0
        def is_set(self):
            return self.waits >= 14
        def wait(self, seconds):
            self.waits += 1
    monkeypatch.setattr(runtime, "ShadowRuntime", lambda *_args, **_kwargs: instance)
    monkeypatch.setattr(runtime, "closed_input_signature", lambda *_args, **_kwargs: "same")
    monkeypatch.setattr(runtime.cold_audit, "drain", lambda *_args: None)
    monkeypatch.setattr(runtime.cold_audit, "prune_completed", lambda *_args: None)
    monkeypatch.setattr(runtime, "release_idle_heap", lambda: {})
    monkeypatch.setattr(runtime.os, "nice", lambda _value: None)
    runtime.run_worker(tmp_path / "paper.db", Stop(), clock_fn=lambda: CLOSED.isoformat())
    assert instance.ticks == 12
    with instance.files as files:
        assert files.read("CURRENT.json")["generation_id"] == f"{12:064x}"
        idle = files.read("idle.json")
        assert idle["state"] == "SHADOW_IDLE_MARKET_CLOSED"
        assert idle["last_generation_id"] == f"{12:064x}"
