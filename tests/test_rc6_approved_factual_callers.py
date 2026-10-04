"""Approved policy reaches native callers; OFF retains their original bytes.

All PPI traffic uses the actual SDK over the existing fake HTTP adapter. Native
signals, quotes, snapshots and positions live only in isolated fixture SQLite.
"""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

import bd_ppi_readonly_guard as readonly
from be_paper_engine import PaperStore, Quote
import bf_production_paper_observer as observer
import bu_instrument_catalog as catalog_module
import cf_intraday_scalping as scalping
import rc6_dynamic_universe.promotion as promotion
from rc6_dynamic_universe.common import digest, identity
from rc6_dynamic_universe.orchestrator import EnginePolicy, UniverseOrchestrator
from rc6_ppi_global_budget import RuntimePPIBudget
from tests.test_rc6_capacity_promotion import approved, controller, shadow_selection
from tests.test_rc6_dynamic_orchestrator import OPEN, frozen
from tests.test_rc6_ppi_capacity_benchmark import wire


def native_store(tmp_path, assets, at):
    store = PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    catalog_module.init_schema(store)
    scalping.init_schema(store)
    with store.connect() as c:
        for a in assets:
            row = dict(a, settlement_source="PPI_FIELD", description="isolated fixture",
                last_seen_at=at.isoformat(), run_id="native-test", raw={"_discovery_source": "PPI_PRIMARY"})
            catalog_module.persist(c, row)
        catalog_module.sync_candidate_universe(c, at.isoformat())
    return store


def cold_shadow(values, warm_shadow, assets, at):
    report = deepcopy(warm_shadow)
    for engine, profile in values[1]["engines"].items():
        spec = profile["profile"]
        policy = EnginePolicy(engine=engine, **{k: v for k, v in spec.items() if k != "endpoints"})
        report["engines"][engine] = UniverseOrchestrator(assets, policy=policy).plan(
            at=at, session_open=OPEN, frozen=frozen(assets), capacity=profile["capacity"])
    return report


def seed_native_signal(store, a, at):
    record = dict(a)
    initial = [{"date": (at - timedelta(minutes=15 - i)).isoformat(),
        "price": 60 + i * 2, "volume": 20 if i % 2 == 0 else 10} for i in range(15)]
    first_at = at - timedelta(minutes=1)
    first = scalping.normalize_payload(initial[:14], received_at=first_at.isoformat())
    scalping.persist_payload(store, record, first, received_at=first_at.isoformat())
    second = scalping.normalize_payload(initial, received_at=at.isoformat())
    assert scalping.persist_payload(store, record, second, received_at=at.isoformat())["state"] == "CONFIRMED_INTERVAL_VOLUME"
    q = Quote(symbol=a["ticker"], asset_class=a["instrument_type"], settlement=a["settlement"],
        market=a["market"], currency=a["currency"], last=Decimal("100"), bid=Decimal("99.9"),
        ask=Decimal("100"), bid_size=Decimal("20"), ask_size=Decimal("20"),
        observed_at=at.isoformat(), book_at=at.isoformat(), trade_at=at.isoformat(), last_kind="TRADE",
        metadata_source="PPI_PRIMARY_FIXTURE")
    store.add_quote(q)
    return record, q


@pytest.mark.parametrize("mode", ["OFF", "SHADOW"])
def test_disabled_factual_scalping_batch_is_byte_equivalent(wire, tmp_path, mode):
    values = approved(wire)
    shadow, assets, at = shadow_selection(values)
    store = native_store(tmp_path, assets, at)
    values[0]["mode"] = mode
    before = hashlib.sha256(Path(store.path).read_bytes()).hexdigest()
    baseline = scalping.select_batch(store, limit=40, cursor=7)
    actual = scalping.select_runtime_batch(store, limit=40, cursor=7, at=at,
        controller=controller(values, shadow=shadow))
    assert actual[:3] == baseline and actual[3] == {"dynamic": False}
    assert hashlib.sha256(Path(store.path).read_bytes()).hexdigest() == before
    assert len(actual[0]) == len(assets) and scalping.DEFAULT_INTRADAY_BATCH_LIMIT == 40
    assert promotion.DEFAULT_POLICY["baseline_limits"] == {"EQUITY_SPOT": 20, "SCALPING": 40}
    with pytest.raises(ValueError, match="INTRADAY_BATCH_LIMIT_OUT_OF_RANGE"):
        scalping.select_batch(store, limit=41)


def test_approved_full_key_policy_reaches_native_scanner_and_scalping(wire, tmp_path, monkeypatch):
    values = approved(wire)
    shadow, assets, at = shadow_selection(values)
    store = native_store(tmp_path, assets, at)
    ctl = controller(values, shadow=shadow)
    monkeypatch.setattr(promotion, "capacity_controller_from_environment", lambda database=None: ctl)
    monkeypatch.setattr(observer, "now_iso", lambda: at.isoformat())
    selected, cursor, universe, selection = scalping.select_runtime_batch(store, limit=40, cursor=7, at=at)
    assert selection["dynamic"] and selection["limit"] == 15 and selection["cadence_seconds"] == 30
    assert 0 < len(selected) <= 15 and len(selected) == len(selection["selected"])
    assert cursor == 7 and universe == len(assets)
    assert [identity(a) for a in selected] == selection["selected"]
    symbols, total, _, _, plan = observer._cycle_plan(store)
    assert 0 < len(symbols) <= 15 and total == len(assets)
    assert len(symbols) == len(ctl.selection("EQUITY_SPOT", [], as_of=at)["selected"])
    assert plan["dynamic_selection"] and plan["cadence_seconds"] == 120 and plan["approved_limit"] == 15
    assert plan["configuration_fingerprint"] == ctl.state(at)["fingerprint"]
    focus, _, _, _, _, _ = observer._runtime_readiness(store)
    assert focus["allow_new_openings"] and focus["matched"] == list(plan["opening_identities"])
    assert focus["source"] == "POINT_IN_TIME_TRADEABILITY_APPROVED_CAPACITY" and not plan["family_quota"]
    ctl.shadow = cold_shadow(values, shadow, assets, at)
    focus, _, _, _, _, _ = observer._runtime_readiness(store)
    assert not focus["allow_new_openings"] and focus["state"] == "AMARILLO"
    assert focus["matched"] == [] and all(r["reason"] == "NATIVE_SIGNAL_WINDOW_NOT_READY" for r in focus["missing"])
    values[3]["approved"] = False
    baseline = scalping.select_batch(store, limit=40, cursor=7)
    assert scalping.select_runtime_batch(store, limit=40, cursor=7, at=at)[:3] == baseline
    assert not observer._cycle_plan(store)[-1].get("dynamic_selection")


def test_actual_scalping_worker_uses_shared_wire_budget_and_cold_hypothesis_cannot_open(wire, tmp_path, monkeypatch):
    values = approved(wire)
    warm, assets, at = shadow_selection(values)
    store = native_store(tmp_path, assets, at)
    native_record, _ = seed_native_signal(store, assets[0], at)
    # Native input qualifies; the newly promoted basket still lacks its dense
    # strategic warmup. This must not bypass the actual native admission path.
    assert scalping.evaluate_candidate(store, native_record, at=at) == "BUY_CANDIDATE"
    ctl = controller(values, shadow=cold_shadow(values, warm, assets, at))
    scheduled = ctl.selection("SCALPING", [], as_of=at)["selected"]
    assert 0 < len(scheduled) <= values[1]["engines"]["SCALPING"]["capacity"]["safe_limit"]
    clock, calls, _ = wire
    clock.advance((at - clock.now()).total_seconds())
    arbiter = RuntimePPIBudget(store.path, ctl, clock=clock.now)
    actual_reader = readonly.ProductionMarketReader
    readers = []
    def factory(*args, **kwargs):
        reader = actual_reader(*args, **kwargs, budget=arbiter)
        readers.append(reader)
        return reader
    class Stop:
        stopped = False
        waits = []
        def is_set(self):
            return self.stopped
        def wait(self, seconds):
            self.waits.append(seconds)
            if seconds >= 30:
                self.stopped = True
    stop = Stop()
    monkeypatch.setattr(promotion, "capacity_controller_from_environment", lambda database=None: ctl)
    monkeypatch.setattr(readonly, "ProductionMarketReader", factory)
    monkeypatch.setattr(observer, "_secret", lambda: ("LOCAL_FAKE_KEY", "LOCAL_FAKE_SECRET"))
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    def forbidden_broker(*args, **kwargs):
        raise AssertionError("cold promoted basket invoked the PAPER broker")
    import bv_paper_runtime
    monkeypatch.setattr(bv_paper_runtime, "broker_from_environment", forbidden_broker)
    before = len(calls)
    scalping.run_worker(store, stop, clock_fn=clock.now)
    assert len(readers) == 1 and stop.waits[-1] == 30
    native_wire = calls[before:]
    assert len(native_wire) == len(scheduled) + 1  # original login and only native due tasks
    assert sum("/Intraday?" in url for _, url in native_wire) == len(scheduled)
    metrics = arbiter.budget.metrics()
    assert metrics["global"] == {"requested": len(scheduled), "allowed": len(scheduled), "used": len(scheduled), "dropped": 0}
    assert {r["consumer"] for r in metrics["by_scope"]} == {"SCALPING"}
    assert {r["priority"] for r in metrics["by_scope"]} == {"WARM", "DISCOVERY"}
    assert all(r["used"] == sum(ctl.shadow["engines"]["SCALPING"]["telemetry"][assets.index(a)]["state"] == r["priority"]
        for a in assets if identity(a) in scheduled) for r in metrics["by_scope"])
    with store.connect() as c:
        events = list(c.execute("SELECT detail FROM paper_events WHERE event_type='SCALPING_PAPER_PROMOTION'"))
        assert any("DISCOVERY_OR_WARMUP_NO_ENTRY_AUTHORITY" in r[0] for r in events)
        assert c.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
        assert c.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0] == 0


def test_native_scalping_snapshot_freezes_exact_decision_and_input_clocks(wire, tmp_path):
    values = approved(wire)
    _, assets, at = shadow_selection(values)
    store = native_store(tmp_path, assets[:1], at)
    record, quote = seed_native_signal(store, assets[0], at)
    with store.connect() as c:
        exact = [dict(r) for r in c.execute("SELECT * FROM ppi_intraday_points ORDER BY event_at")]
    action = scalping.evaluate_candidate(store, record, at=at)
    assert action == "BUY_CANDIDATE"
    with store.connect() as c:
        candidate = dict(c.execute("SELECT * FROM scalping_candidates").fetchone())
        row = dict(c.execute("SELECT * FROM decision_evidence_snapshots").fetchone())
    payload = json.loads(row["payload_json"])
    assert hashlib.sha256(row["payload_json"].encode()).hexdigest() == row["payload_sha256"]
    assert payload["decision"]["action"] == candidate["action"] == action
    assert payload["decision"]["score"] == candidate["score"] and payload["decision"]["reason"] == candidate["reason"]
    assert payload["runtime"]["clock_mode"] == "NATIVE" and payload["runtime"]["strategy_id"] == "SCALPING_BASELINE"
    assert payload["signal_at"] == scalping._stamp(at) == payload["decision_at"]
    inputs = payload["inputs_used"]["entry_signal_inputs"]
    assert inputs["samples"] == len(exact)
    assert inputs["price_samples"] == [{"price": r["price"], "source_at": r["event_at"],
        "received_at": r["last_verified_at"], "first_received_at": r["first_received_at"], "source": r["source"]} for r in exact]
    economics = json.loads(candidate["economics_json"])
    assert inputs["momentum"] == economics["momentum"] and inputs["spread_fraction"] == economics["spread_fraction"]
    assert inputs["rvol"] is None and inputs["activity"]["volume_unit"] == "NO_VERIFICADO"
    assert payload["quote_used"]["bid"] == str(quote.bid) and payload["quote_used"]["book_at"] == quote.book_at
    from bq_exit_policy import PaperSessionPolicy
    session = PaperSessionPolicy()
    _, close_at = session.bounds(at, payload["quote_used"])
    assert inputs["eod_at"] == scalping._stamp(close_at - timedelta(minutes=session.exit_minutes))
    assert len(payload["runtime"]["configuration_fingerprint"]) == 64
    assert scalping.evaluate_candidate(store, record, at=at) == action
    with store.connect() as c:
        c.execute("UPDATE market_snapshots SET bid='1',ask='2'")
        c.execute("UPDATE ppi_intraday_points SET price='1'")
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM decision_evidence_snapshots").fetchone()[0] == 1
        assert dict(c.execute("SELECT * FROM decision_evidence_snapshots").fetchone()) == row
        assert c.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
