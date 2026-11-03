"""CLOSED liveness is separate from a financial cut and native OPEN freshness."""
from datetime import timedelta
import sqlite3

import pytest

from be_paper_engine import PaperBroker
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


def test_closed_paper_clock_valuation_append_preserves_idle_only_if_economics_match(tmp_path):
    store, _ = make_store(tmp_path, count=2)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    columns = ("measured_at,currency,cash,exposure,pending_proceeds,"
               "caucion_principal,caucion_accrued,unrealized_pnl,realized_pnl,equity")
    currencies = ("ARS", "USD", "MEP", "CCL")

    def append(at, *, ars_cash="100", accrued="0"):
        with store.connect() as c:
            c.executemany(f"INSERT INTO paper_equity_by_currency({columns}) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?)",
                ((at.isoformat(), currency, ars_cash if currency == "ARS" else "50",
                  "0", "0", "0", accrued if currency == "ARS" else "0",
                  "0", "0", ars_cash if currency == "ARS" else "50")
                 for currency in currencies))

    append(CLOSED)
    before = closed_input_signature(worker, CLOSED + timedelta(seconds=1))
    assert before is not None
    deep_before = closed_input_signature(worker, CLOSED + timedelta(seconds=1),
                                         deep_catalog=True)
    assert deep_before is not None
    append(CLOSED + timedelta(seconds=30))
    # The four real clock append IDs and timestamps change, while the only
    # family input (the latest economic row per currency) does not.
    assert closed_input_signature(worker, CLOSED + timedelta(seconds=31)) == before
    assert closed_input_signature(worker, CLOSED + timedelta(seconds=31),
                                  deep_catalog=True) == deep_before

    append(CLOSED + timedelta(seconds=60), ars_cash="101")
    cash_change = closed_input_signature(worker, CLOSED + timedelta(seconds=61))
    assert cash_change is not None and cash_change != before
    append(CLOSED + timedelta(seconds=90), ars_cash="101", accrued="0.01")
    assert closed_input_signature(worker, CLOSED + timedelta(seconds=91)) != cash_change

    # A future observation is not an eligible current row. Fail closed until
    # it can be interpreted rather than hiding a later time transition.
    append(CLOSED + timedelta(days=1), ars_cash="101", accrued="0.01")
    assert closed_input_signature(worker, CLOSED + timedelta(seconds=92)) is None
    rollover = closed_input_signature(worker, CLOSED + timedelta(days=1, seconds=1))
    assert rollover is not None and rollover != cash_change
    # PREOPEN/OPEN never uses this idle shortcut, even with equal economics.
    assert closed_input_signature(worker, OPEN + timedelta(minutes=2)) is None


def test_real_paper_broker_clock_appends_four_equal_closed_valuations(tmp_path):
    store, _ = make_store(tmp_path, count=1)
    broker = PaperBroker(store)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    broker.mark_equity({}, as_of=CLOSED.isoformat())
    first = closed_input_signature(worker, CLOSED + timedelta(seconds=1))
    assert first is not None
    with store.connect() as c:
        count_before = c.execute("SELECT COUNT(*) FROM paper_equity_by_currency").fetchone()[0]
    broker.mark_equity({}, as_of=(CLOSED + timedelta(seconds=30)).isoformat())
    with store.connect() as c:
        count_after = c.execute("SELECT COUNT(*) FROM paper_equity_by_currency").fetchone()[0]
    assert count_after - count_before == 4
    assert closed_input_signature(worker, CLOSED + timedelta(seconds=31)) == first


def test_closed_risk_supervisor_refresh_preserves_latch_and_freshness_boundary(tmp_path):
    store, _ = make_store(tmp_path, count=1)
    broker = PaperBroker(store, daily_loss_pct="2.5")
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    broker.daily_risk.evaluate(CLOSED.isoformat())
    first = closed_input_signature(worker, CLOSED + timedelta(seconds=1))
    assert first is not None
    broker.daily_risk.evaluate((CLOSED + timedelta(seconds=30)).isoformat())
    refreshed = closed_input_signature(worker, CLOSED + timedelta(seconds=31))
    assert refreshed == first
    assert closed_input_signature(worker, CLOSED + timedelta(seconds=151)) != refreshed
    with store.connect() as c:
        c.execute("UPDATE paper_daily_risk SET state='LATCHED',latched_at=? "
                  "WHERE day=? AND currency='ARS'",
                  ((CLOSED + timedelta(seconds=30)).isoformat(),
                   CLOSED.astimezone(runtime.TZ).date().isoformat()))
    assert closed_input_signature(worker, CLOSED + timedelta(seconds=32)) != refreshed


def test_closed_equity_window_detects_currency_expelled_by_new_rows(tmp_path):
    store, _ = make_store(tmp_path, count=1)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    values = lambda currency: (CLOSED.isoformat(), currency, "10", "0", "0",
                               "0", "0", "0", "0", "10")
    with store.connect() as c:
        c.execute("INSERT INTO paper_equity_by_currency VALUES(NULL,?,?,?,?,?,?,?,?,?,?)",
                  values("ARS"))
        c.execute("INSERT INTO paper_equity_by_currency VALUES(NULL,?,?,?,?,?,?,?,?,?,?)",
                  values("USD"))
        c.executemany("INSERT INTO paper_equity_by_currency VALUES(NULL,?,?,?,?,?,?,?,?,?,?)",
                      (values("ARS") for _ in range(499)))
    before = closed_input_signature(worker, CLOSED + timedelta(seconds=1))
    assert before is not None
    with store.connect() as c:
        c.execute("INSERT INTO paper_equity_by_currency VALUES(NULL,?,?,?,?,?,?,?,?,?,?)",
                  values("ARS"))
    after = closed_input_signature(worker, CLOSED + timedelta(seconds=1))
    assert after is not None and after != before


def test_closed_equity_unknown_schema_fails_closed(tmp_path):
    store, _ = make_store(tmp_path, count=1)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    with store.connect() as c:
        c.execute('ALTER TABLE paper_equity_by_currency ADD COLUMN audit_hint TEXT')
    assert closed_input_signature(worker, CLOSED) is None


def test_closed_risk_unknown_schema_fails_closed(tmp_path):
    store, _ = make_store(tmp_path, count=1)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    with store.connect() as c:
        c.execute('ALTER TABLE paper_daily_risk ADD COLUMN unreviewed_policy TEXT')
    assert closed_input_signature(worker, CLOSED) is None


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
