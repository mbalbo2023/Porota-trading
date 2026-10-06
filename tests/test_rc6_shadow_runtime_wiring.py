"""Run the canonical worker against actual SQLite inputs and private evidence."""
from datetime import datetime, timedelta, timezone
from dataclasses import replace
import json
from pathlib import Path
import sqlite3

import pytest

from be_paper_engine import PaperStore, Quote, D
from bf_production_paper_observer import _support_schema
from cf_intraday_scalping import init_schema
from rc6_dynamic_universe.common import digest, identity
from rc6_shadow_runtime.persistence import EvidenceFiles
from rc6_shadow_runtime.worker import ShadowRuntime, session_context, run_worker

OPEN = datetime(2026, 10, 5, 13, 30, tzinfo=timezone.utc)
PRE = OPEN - timedelta(minutes=10)


def make_store(tmp_path, count=25):
    store = PaperStore(str(tmp_path / "paper.db"))
    _support_schema(store)
    init_schema(store)
    assets = [dict(ticker=f"S{n}", instrument_type="ACCIONES", market="BYMA",
        currency="ARS", settlement="A-24HS", status="AVAILABLE", capability="READY_PAPER_SPOT")
        for n in range(count)]
    with store.connect() as c:
        for a in assets:
            c.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (*identity(a), "FIXTURE", "fixture", PRE.isoformat(), "test", "AVAILABLE", "READY_PAPER_SPOT", "{}"))
    return store, assets


def quote(asset, at, bid="100", ask="100.2"):
    return Quote(asset["ticker"], asset["instrument_type"], asset["settlement"],
        D(bid), D(bid), D(ask), D(1000), D(1000), at.isoformat(),
        currency="ARS", market="BYMA", metadata_source="TEST_FIXTURE",
        book_at=at.isoformat(), trade_at=at.isoformat(), last_kind="TRADE")


def capacity_stub(at, **kwargs):
    return {"status": "SHADOW_RECOMMENDATION", "safe_limit": 8,
        "configuration_fingerprint": "fixture-contract", "evidence_digest": "fixture-not-OPEN-claim",
        "slots_by_endpoint": {name: 8 for name in kwargs["endpoints"]},
        "shared_global_slots": 8, "generated_at": OPEN.isoformat(),
        "expires_at": (OPEN + timedelta(hours=6)).isoformat()}


def snapshot(worker):
    with worker.files as files:
        return files.read("checkpoint.json.gz"), files.read("preopen-2026-10-05.json.gz")


def test_worker_real_runtime_calls_framework_freezes_and_restarts_without_source_writes(tmp_path, monkeypatch):
    store, assets = make_store(tmp_path)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    # Finish fixture writers/checkpoint before byte comparison: a WAL reader
    # must not be mistaken for a prior writer's delayed automatic checkpoint.
    import gc
    gc.collect()
    with sqlite3.connect(store.path) as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = Path(store.path).read_bytes()
    report = worker.tick(PRE)
    assert set(report["engines"]) == {"SCALPING", "EQUITY_SPOT"}
    assert all(plan["catalog_ready_count"] == len(assets) for plan in report["engines"].values())
    assert report["source_database_effect"] == "READ_ONLY"
    assert report["provider_requests"] == 0
    assert Path(store.path).read_bytes() == before
    _, frozen = snapshot(worker)
    restarted = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    again = restarted.tick(PRE + timedelta(seconds=30))
    assert again["checkpoint_reused"] and snapshot(restarted)[1] == frozen
    assert not list((tmp_path / "shadow").glob("*.tmp"))


def test_outside_preopen_native_discovery_promotes_warm_then_hot_never_opens(tmp_path, monkeypatch):
    from rc6_dynamic_universe import live
    store, assets = make_store(tmp_path)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[],
        policies={"SCALPING": {"warmup_samples": 3}})
    worker.tick(PRE)
    _, frozen = snapshot(worker)
    target = assets[-1]
    assert not next(r for r in frozen["frozen"]["SCALPING"]["payload"]["rows"]
                    if r["ticker"] == target["ticker"])["tradeable"]
    # Capacity is a test-only contract: these inputs never claim real OPEN.
    monkeypatch.setattr(live, "safe_capacity", lambda report, **kw: capacity_stub(OPEN, **kw))
    t0 = OPEN + timedelta(minutes=1)
    store.add_quote(quote(target, t0, ask="101"))
    first = worker.tick(t0)
    assert next(r for r in first["engines"]["SCALPING"]["telemetry"]
                if r["identity"][0] == target["ticker"])["state"] == "DISCOVERY"
    t1 = t0 + timedelta(minutes=5)
    store.add_quote(quote(target, t1))
    promoted = worker.tick(t1)
    row = next(r for r in promoted["engines"]["SCALPING"]["telemetry"] if r["identity"][0] == target["ticker"])
    assert row["state"] == "WARM" and row["promoted_at"] == t1.isoformat()
    assert {"NEW_TRADES", "SPREAD_COMPRESSION"} <= set(row["promotion_reasons"])
    assert row["warmup_progress"]["distinct_samples"] == 0
    for minute in range(3):
        t = t1 + timedelta(minutes=minute + 1)
        store.add_quote(quote(target, t))
        with store.connect() as c:
            c.execute("INSERT INTO ppi_intraday_points VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (*identity(target), t.isoformat(), "100", "10", t.isoformat(), t.isoformat(), "PPI_MARKETDATA_INTRADAY"))
            c.execute("INSERT OR REPLACE INTO ppi_intraday_contract_state VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (*identity(target), "CONFIRMED_INTERVAL_VOLUME", 2, 5, 0, 1, t.isoformat(), t.isoformat(), "fixture"))
        result = worker.tick(t)
    row = next(r for r in result["engines"]["SCALPING"]["telemetry"] if r["identity"][0] == target["ticker"])
    assert row["state"] == "HOT" and row["time_to_warmup_seconds"] is not None
    assert row["pipeline"]["SIGNAL_READY_SHADOW"]
    assert row["pipeline"]["ECONOMICS_SHADOW"] == "NO_VERIFICADO"
    assert datetime.fromisoformat(row["economics_shadow"]["eod_at"]).astimezone(timezone.utc).hour == 19
    assert datetime.fromisoformat(row["economics_shadow"]["eod_at"]).minute == 50
    repeated = worker.tick(t + timedelta(seconds=30))
    again = next(r for r in repeated["engines"]["SCALPING"]["telemetry"] if r["identity"][0] == target["ticker"])
    assert again["warmup_progress"]["distinct_samples"] == 3
    assert not again["entry_authority"] and again["factual_execution"] == "NOT_CALLED"
    assert snapshot(worker)[1] == frozen
    with store.connect() as c:
        assert c.execute("SELECT count(*) FROM paper_positions").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM paper_decisions").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM scalping_candidates").fetchone()[0] == 0
    assert result["real_orders_sent"] == 0 and result["real_routes"] == "NOT_CALLED"


def test_closed_worker_never_claims_open_capacity_or_calls_provider(tmp_path, monkeypatch):
    import bd_ppi_readonly_guard
    monkeypatch.setattr(bd_ppi_readonly_guard, "ProductionMarketReader",
        lambda *a, **kw: pytest.fail("No provider client may be created by SHADOW"))
    store, assets = make_store(tmp_path)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    result = worker.tick("2026-10-04T00:30:00+00:00")
    assert result["phase"] == "CLOSED" and result["provider_requests"] == 0
    assert result["capacity_open_status"] == "NO_VERIFICADO"
    assert all(p["selected"] == [] and p["capacity"]["safe_limit"] == 0 for p in result["engines"].values())
    assert result["provider_additional_budget"] == {"current": 0, "book": 0, "intraday": 0}


def test_missing_inwheel_preopen_fail_closed_never_backdated(tmp_path):
    store, assets = make_store(tmp_path)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    result = worker.tick(OPEN + timedelta(minutes=1))
    assert result["status"] == "PREOPEN_SNAPSHOT_REQUIRED_DURING_SESSION"
    assert not result["engines"] and not (tmp_path / "shadow" / "preopen-2026-10-05.json.gz").exists()


def test_readonly_worker_does_not_wait_for_trading_write_lock(tmp_path):
    store, _ = make_store(tmp_path)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    blocker = sqlite3.connect(store.path)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        assert worker.tick(PRE)["source_database_effect"] == "READ_ONLY"
    finally:
        blocker.rollback()
        blocker.close()


def test_source_or_configuration_invalidates_checkpoint_and_future_rejected(tmp_path):
    store, _ = make_store(tmp_path)
    root = tmp_path / "shadow"
    worker = ShadowRuntime(store.path, evidence_root=root, source_roots=[])
    worker.tick(PRE)
    with pytest.raises(ValueError, match="FUTURE"):
        worker.tick(PRE - timedelta(seconds=1))
    changed = ShadowRuntime(store.path, evidence_root=root, source_roots=[], row_limit=19999)
    assert not changed.tick(PRE + timedelta(seconds=1))["checkpoint_reused"]


def test_exclusive_evidence_lock_alias_and_quota_never_overwrite_input(tmp_path):
    source = tmp_path / "source.json"
    source.write_text('{"private":"input"}')
    with EvidenceFiles(tmp_path / "out", protected=[source]) as files:
        files.write("freeze.json.gz", {"rank": 1}, immutable=True)
        with pytest.raises(BlockingIOError):
            with EvidenceFiles(tmp_path / "out"):
                pass
        with pytest.raises(ValueError, match="IMMUTABLE"):
            files.write("freeze.json.gz", {"rank": 2}, immutable=True)
    with EvidenceFiles(tmp_path / "small", maximum_bytes=10) as files:
        with pytest.raises(ValueError, match="CAPACITY"):
            files.write("report.json", {"rank": 1})
    assert source.read_text() == '{"private":"input"}'
    with pytest.raises(ValueError, match="SEPARATE"):
        EvidenceFiles(tmp_path, protected=[source])
    target = tmp_path / "out" / "aliased.json"
    target.symlink_to(source)
    with EvidenceFiles(tmp_path / "out") as files:
        with pytest.raises(ValueError, match="ALIAS"):
            files.write("aliased.json", {})


def test_canonical_child_entry_never_constructs_trading_store(tmp_path, monkeypatch):
    import bv_paper_runtime as runtime
    import rc6_shadow_runtime.worker as worker_module
    store, _ = make_store(tmp_path)
    monkeypatch.setenv("PAPER_V17_DB_PATH", store.path)
    monkeypatch.setattr(runtime, "runtime_store", lambda *a, **kw: pytest.fail("Trading init forbidden"))
    calls = []
    monkeypatch.setattr(worker_module, "run_worker", lambda path, stop, **kw: calls.append(Path(path)))
    assert runtime.main(["--dynamic-shadow-worker"]) == 0
    assert calls == [Path(store.path)]


def test_canonical_supervisor_installs_shadow_child_with_existing_restart_control(tmp_path, monkeypatch):
    import bv_paper_runtime as runtime
    store, _ = make_store(tmp_path)
    monkeypatch.setattr(runtime, "runtime_store", lambda: store)
    monkeypatch.setenv("POROTA_RUNTIME_SCHEMA_READY", "")
    monkeypatch.setenv(runtime.DB_ENV, store.path)
    children = []
    monkeypatch.setattr(runtime, "run_clock", lambda s, c, stop: children.append(c))
    assert runtime.main([]) == 0
    assert children[0].commands["dynamic_shadow"][-1] == "--dynamic-shadow-worker"
    assert children[0].next_start["dynamic_shadow"] > children[0].next_start["scanner"]


def test_audited_session_uses_next_operational_day_and_previous_close():
    saturday = session_context("2026-10-03T22:00:00+00:00")
    assert saturday["opening"] == OPEN and saturday["cutoff"].isoformat() == "2026-10-02T20:00:00+00:00"
    with pytest.raises(ValueError, match="CALENDAR"):
        session_context("2027-01-01T12:00:00+00:00")


def test_opened_priority_and_scoped_native_ppi_failure_survive_runtime_capacity_loss(tmp_path):
    store, assets = make_store(tmp_path, count=2)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    worker.tick(PRE)
    at = OPEN + timedelta(minutes=1)
    with store.connect() as c:
        c.execute("""INSERT INTO paper_positions(paper_id,source,strategy_version,symbol,asset_class,
            settlement,status,quantity,entry_price,entry_cost,stop_price,target_price,opened_at,
            features_json,currency,market) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("already-open", "FIXTURE", "unchanged", assets[0]["ticker"], "ACCIONES", "A-24HS",
             "OPEN", "1", "100", ".1", "98", "105", PRE.isoformat(), "{}", "ARS", "BYMA"))
        c.execute("INSERT INTO paper_events(event_at,source,event_type,detail) VALUES(?,?,?,?)",
            (at.isoformat(), "FIXTURE", "INTRADAY_SCALPING_UNSUPPORTED",
             f'{assets[0]["ticker"]}: PPI_INSTRUMENT_NOT_FOUND;shadow_identity=' +
             json.dumps(identity(assets[0]), separators=(",", ":"))))
        before = tuple(c.execute("SELECT * FROM paper_positions").fetchone())
    store.add_quote(quote(assets[1], at))
    result = worker.tick(at)
    for engine in result["engines"].values():
        assert engine["selected"] == [identity(assets[0])]
        assert engine["opened_priority"] == [identity(assets[0])]
        rows = {r["identity"][0]: r for r in engine["telemetry"]}
        assert "PPI_INSTRUMENT_NOT_FOUND" in rows[assets[0]["ticker"]]["rejection_reason"]
        assert "PPI_INSTRUMENT_NOT_FOUND" not in rows[assets[1]["ticker"]]["rejection_reason"]
        assert rows[assets[1]["ticker"]]["last_useful_observation_at"] == at.isoformat()
    with store.connect() as c:
        assert tuple(c.execute("SELECT * FROM paper_positions").fetchone()) == before
        assert c.execute("SELECT count(*) FROM paper_fills").fetchone()[0] == 0


def test_existing_byma_scraper_cache_is_observe_only_and_cannot_backfill_on_config_restart(tmp_path):
    store, assets = make_store(tmp_path, count=1)
    root = tmp_path / "shadow"
    sources = tmp_path / "market"
    sources.mkdir()
    worker = ShadowRuntime(store.path, evidence_root=root, source_roots=[sources])
    worker.tick(PRE)
    at = OPEN + timedelta(minutes=1)
    source = sources / "rc6_public_sources_latest.json"
    def publish(when):
        source.write_text(json.dumps({"sources": [{"source": "BYMA", "records": [{
            "symbol": assets[0]["ticker"], "family": "ACCIONES", "market": "BYMA",
            "currency": "ARS", "term": "T1", "timestamp": when.isoformat(),
            "captured_at": (when + timedelta(seconds=1)).isoformat(),
            "last": 100, "bid": 100, "ask": 100.2, "price_unit": "PER_SHARE"}]}]}))
    publish(OPEN)
    restarted = ShadowRuntime(store.path, evidence_root=root, source_roots=[sources], row_limit=19999)
    result = restarted.tick(at)
    ingestion = result["source_reports"][0]["runtime_ingestion"]
    assert ingestion["accepted"] == 0 and ingestion["excluded_before_watermark"] == 1
    assert snapshot(restarted)[0]["radar"]["points"] == []
    publish(at + timedelta(minutes=1))
    fresh = restarted.tick(at + timedelta(minutes=1, seconds=2))
    assert fresh["source_reports"][0]["runtime_ingestion"]["accepted"] == 1
    observation = fresh["source_reports"][0]["observations"][0]
    assert observation["decision_effect"] == "OBSERVE_ONLY" and not observation["live_decision_authority"]
    for plan in fresh["engines"].values():
        row = plan["telemetry"][0]
        assert row["last_useful_observation_at"] is not None
        assert row["warmup_progress"]["distinct_samples"] == 0 and not row["entry_authority"]
    repeated = restarted.tick(at + timedelta(minutes=1, seconds=32))
    assert repeated["source_reports"][0]["runtime_ingestion"]["accepted"] == 0
    assert len(snapshot(restarted)[0]["radar"]["points"]) == 1
    assert repeated["provider_requests"] == 0


def test_atomic_replacement_quota_bounds_peak_and_preserves_prior_audit_file(tmp_path):
    root = tmp_path / "shadow"
    with EvidenceFiles(root) as files:
        files.write("latest.json", {"evidence": "a" * 120})
    target = root / "latest.json"
    original = target.read_bytes()
    with EvidenceFiles(root, maximum_bytes=2 * len(original) - 1) as files:
        with pytest.raises(ValueError, match="CAPACITY"):
            files.write("latest.json", {"evidence": "b" * 120})
    assert target.read_bytes() == original
    assert not list(root.glob("*.tmp"))


def test_configured_iol_cache_is_reused_and_protected_as_an_input(tmp_path, monkeypatch):
    store, _ = make_store(tmp_path, count=1)
    # Finish fixture writers before a capture that requires a quiescent source.
    # sqlite3's transaction context does not close its connection; a delayed
    # collector can otherwise checkpoint the main file during the capture.
    import gc
    from contextlib import closing
    gc.collect()
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    cache_dir = tmp_path / "custom-market"
    cache_dir.mkdir()
    cache = cache_dir / "quotes.json"
    cache.write_text('{"symbols":[],"status":"SOURCE_UNAVAILABLE"}')
    monkeypatch.setenv("POROTA_IOL_SHADOW_CACHE_PATH", str(cache))
    with pytest.raises(ValueError, match="SEPARATE"):
        ShadowRuntime(store.path, evidence_root=cache_dir, source_roots=[])
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    worker.tick(PRE)
    result = worker.tick(OPEN + timedelta(minutes=1))
    assert result["source_reports"][0]["source"] == "IOL"
    assert result["source_reports"][0]["status"] == "SOURCE_UNAVAILABLE"
    assert cache.read_text() == '{"symbols":[],"status":"SOURCE_UNAVAILABLE"}'
    assert result["provider_requests"] == 0
