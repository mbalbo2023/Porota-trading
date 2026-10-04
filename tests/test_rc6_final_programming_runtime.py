"""Acceptance of the final canonical callers over real synthetic PAPER ledgers."""
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from be_paper_engine import D, NativeSignalPrices, PaperBroker
from bq_exit_policy import PaperSessionPolicy
from rc6_shadow_runtime.worker import ShadowRuntime
from tests.test_rc6_shadow_runtime_wiring import make_store, quote, PRE, OPEN
from tests.test_rc6_future_programming_complete import broker as future_broker, quote as future_quote, OPEN_AT
from tests.test_rc6_ppi_capacity_benchmark import wire
from tests.test_rc6_capacity_promotion import approved


def _snapshots(store):
    with store.connect() as connection:
        rows = connection.execute("SELECT payload_json,payload_sha256 FROM decision_evidence_snapshots ORDER BY rowid").fetchall()
    for row in rows:
        assert hashlib.sha256(row[0].encode()).hexdigest() == row[1]
    return [(json.loads(row[0]), row[1]) for row in rows]


def test_price_vector_captures_same_native_rows_without_mutable_reconstruction(tmp_path):
    store, assets = make_store(tmp_path, count=1)
    at = OPEN + timedelta(minutes=2)
    first = quote(assets[0], at - timedelta(minutes=1), bid="100", ask="100.1")
    second = quote(assets[0], at, bid="101", ask="101.1")
    store.add_quote(first)
    store.add_quote(second)
    store.add_quote(replace(second, last=D("102"), observed_at=(at + timedelta(seconds=1)).isoformat()))
    # A later receipt for the same native trade cannot leak into this decision.
    store.add_quote(replace(second, currency="USD", last=D("999")))
    values = store.signal_prices(second, at)
    assert isinstance(values, NativeSignalPrices) and values == [D("100"), D("101")]
    assert [row["price"] for row in values.observations] == ["100", "101"]
    assert all(row["currency"] == "ARS" and row["source"] for row in values.observations)
    assert all(datetime.fromisoformat(row["source_at"]) <= datetime.fromisoformat(row["received_at"]) <= at
               for row in values.observations)
    with store.connect() as connection:
        connection.execute("UPDATE market_snapshots SET last='888'")
    assert values == [D("100"), D("101")]
    assert [row["price"] for row in values.observations] == ["100", "101"]


def test_specialized_future_native_clocks_and_vector_survive_decision_capture(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_HISTORICAL_CANDLE_SHADOW", "OFF")
    start = datetime.fromisoformat(OPEN_AT)
    clock = [start.isoformat()]
    b = future_broker(tmp_path, clock)
    for minute in range(8):
        when = (start + timedelta(minutes=minute)).isoformat()
        q = future_quote(when, bid=str(1450 + minute * 10), ask=str(1451 + minute * 10))
        b.store.add_quote(q)
    native = [datetime.fromisoformat(q.observed_at) + timedelta(seconds=1)]
    def actual_clock():
        native[0] += timedelta(microseconds=100)
        return native[0].isoformat()
    b.clock_fn = actual_clock
    b.on_quote(q)
    payload, original_hash = _snapshots(b.store)[0]
    assert payload["decision"]["final_result"] == "OPENED_SIMULATED"
    assert payload["runtime"]["strategy_id"] == "futures-dlr-paper-v1"
    assert payload["runtime"]["strategy_version"] == "futures-dlr-paper-v1"
    stages = [datetime.fromisoformat(payload[key]) for key in
              ("signal_at", "decision_at", "intent_at", "entry_fill_committed_at")]
    assert stages == sorted(stages) and len(set(stages)) == 4
    captured = payload["inputs_used"]["entry_signal_inputs"]
    assert captured["price_sample_status"] == "NATIVE_EXACT_VECTOR"
    assert [row["price"] for row in captured["price_samples"]] == [str(1450 + i * 10) for i in range(8)]
    assert captured["cash_multiplier"] == "1000" and captured["fee_provenance"].startswith("PAPER_ESTIMATE:")
    assert datetime.fromisoformat(captured["eod_at"]).hour == 14
    assert datetime.fromisoformat(captured["eod_at"]).minute == 50
    assert captured["quantity"] == payload["inputs_used"]["quantity"]
    with b.store.connect() as connection:
        connection.execute("UPDATE market_snapshots SET last='1'")
    assert _snapshots(b.store)[0][1] == original_hash
    assert b.store.open_positions() == []
    assert payload["runtime"]["real_money_authorized"] is False


def test_canonical_worker_prospectively_runs_entries_labels_and_native_net_funnel(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_HISTORICAL_CANDLE_SHADOW", "OFF")
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "OBSERVATION_ONLY")
    import bd_ppi_readonly_guard
    monkeypatch.setattr(bd_ppi_readonly_guard, "ProductionMarketReader",
                        lambda *a, **kw: pytest.fail("SHADOW created a provider client"))
    store, assets = make_store(tmp_path, count=1)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    initial = worker.tick(PRE)
    assert initial["entry_signal_lab"]["registered"] == 0
    tick = [OPEN + timedelta(minutes=2)]
    b = PaperBroker(store, clock_fn=lambda: tick[0].isoformat(), session_policy=PaperSessionPolicy(),
                    score_threshold=".50", signal_min_samples=6, ai_mode="OFF", initial_cash="1000000")
    for index, price in enumerate(("95", "96", "97", "98", "99", "100")):
        q = quote(assets[0], tick[0] - timedelta(seconds=150 - index * 30), bid=price, ask=str(D(price) + D(".01")))
        store.add_quote(q)
    b.on_quote(q)
    assert len(store.open_positions()) == 1
    current = worker.tick(tick[0] + timedelta(seconds=1))
    lab = current["entry_signal_lab"]
    assert lab["registered"] == 1 and lab["native_decisions_observed"] == 1
    assert lab["experiments"] and lab["entry_authority"] is False
    results = lab["experiments"][0]["variants"]
    assert len({row["input_sha256"] for row in results.values()}) == 1
    assert all(row["mode"] == "SHADOW" for row in results.values())
    for seconds in (301, 901):
        future_at = tick[0] + timedelta(seconds=seconds)
        store.add_quote(quote(assets[0], future_at, bid="103", ask="103.01"))
        current = worker.tick(future_at)
    matured = current["entry_signal_lab"]["experiments"][0]["labels"]
    assert {label["horizon_seconds"] for label in matured} == {300, 900}
    assert all(label["status"] == "MEDIDO" and label["out_of_sample"] for label in matured)
    assert all(label["price_anchor"] == "SHADOW_QUOTE_ASK_TO_FUTURE_BID" for label in matured)
    assert all(label["economic_sensitivity"]["status"] == "MODELED_PAPER_SENSITIVITY" for label in matured)
    tick[0] += timedelta(seconds=930)
    exit_quote = quote(assets[0], tick[0], bid="104", ask="104.01")
    store.add_quote(exit_quote)
    assert b._close(store.open_positions()[0], exit_quote, "PROGRAMMING_ACCEPTANCE")
    with store.connect() as connection:
        native_net = D(connection.execute("SELECT net_pnl FROM paper_positions").fetchone()[0])
    terminal = worker.tick(tick[0] + timedelta(seconds=1))
    funnel = terminal["operational_funnel"]
    assert {"CATALOG_READY", "SIGNAL_EVALUATED", "SIGNAL_CANDIDATE", "PAPER_OPENED", "EXIT_REASON", "NET_PNL"} <= {
        row["stage"] for row in funnel["lineage"]}
    nets = [row for row in funnel["lineage"] if row["stage"] == "NET_PNL"]
    assert len(nets) == 1 and D(nets[0]["detail"]["net"]) == native_net
    assert nets[0]["currency"] == "ARS" and funnel["currencies_added_together"] is False
    with store.connect() as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = Path(store.path).read_bytes()
    restarted = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    replay = restarted.tick(tick[0] + timedelta(seconds=2))
    assert replay["checkpoint_reused"] and replay["entry_signal_lab"]["registered"] == 1
    assert len([row for row in replay["operational_funnel"]["lineage"] if row["stage"] == "NET_PNL"]) == 1
    assert Path(store.path).read_bytes() == before
    assert replay["provider_requests"] == replay["real_orders_sent"] == 0
    assert replay["real_routes"] == "NOT_CALLED" and replay["production_limits_modified"] is False
    assert store.active_future_positions() == []


def test_capacity_config_inputs_cannot_alias_owned_shadow_outputs(tmp_path, monkeypatch):
    store, _ = make_store(tmp_path, count=1)
    root = tmp_path / "shadow"
    root.mkdir()
    primary_input = root / "latest.json.gz"
    primary_input.write_bytes(b"immutable capacity input")
    monkeypatch.setenv("POROTA_CAPACITY_REPORT_PATH", str(primary_input))
    monkeypatch.setenv("POROTA_CAPACITY_SHADOW_PATH", str(primary_input))
    with pytest.raises(ValueError, match="SEPARATE"):
        ShadowRuntime(store.path, evidence_root=root, source_roots=[])
    assert primary_input.read_bytes() == b"immutable capacity input"


def test_canonical_worker_consumes_reviewed_open_capacity_and_falls_back_without_provider_calls(tmp_path, monkeypatch, wire):
    policy, recommendation, measurement, approval, at = approved(wire)
    config = {
        "POLICY": policy, "RECOMMENDATION": recommendation,
        "REPORT": measurement, "APPROVAL": approval,
    }
    inputs = {}
    for name, payload in config.items():
        path = tmp_path / (name.lower() + ".json")
        path.write_text(json.dumps(payload))
        inputs[name] = path
        monkeypatch.setenv("POROTA_CAPACITY_" + name + "_PATH", str(path))
    monkeypatch.setenv("POROTA_DYNAMIC_CAPACITY_MODE", "APPROVED")
    root = tmp_path / "shadow"
    monkeypatch.setenv("POROTA_CAPACITY_SHADOW_PATH", str(root / "latest.json.gz"))
    import bd_ppi_readonly_guard
    monkeypatch.setattr(bd_ppi_readonly_guard, "ProductionMarketReader",
                        lambda *a, **kw: pytest.fail("SHADOW created a provider client"))
    provider_calls = len(wire[1])
    store, assets = make_store(tmp_path, count=1)
    worker = ShadowRuntime(store.path, evidence_root=root, source_roots=[])
    preopen = worker.tick(PRE)
    assert preopen["capacity_policy"]["status"] == "BASELINE_FAIL_CLOSED"
    assert preopen["production_limits_modified"] is False
    before = {name: path.read_bytes() for name, path in inputs.items()}
    initial = worker.tick(at)
    assert initial["capacity_policy"]["status"] == "APPROVED_DYNAMIC"
    for index in range(20):
        cut = at + timedelta(seconds=30 * (index + 1))
        store.add_quote(quote(assets[0], cut, bid=str(100 + index), ask=str(D(100 + index) + D(".01"))))
        report = worker.tick(cut)
    for engine, plan in report["engines"].items():
        expected = recommendation["engines"][engine]["capacity"]
        assert plan["capacity"]["safe_limit"] == expected["safe_limit"] == 15
        assert plan["capacity"]["configuration_fingerprint"] == expected["configuration_fingerprint"]
        assert plan["preopen_digest"] == preopen["engines"][engine]["preopen_digest"]
        assert all(row["entry_authority"] is False for row in plan["telemetry"])
    baseline = [("S0", "ACCIONES", "A-24HS")]
    selected = worker.capacity_controller.selection("EQUITY_SPOT", baseline, as_of=cut)
    assert selected["dynamic"] and selected["limit"] == 15 and selected["cadence_seconds"] == 120
    assert all(len(identity) == 5 for identity in selected["selected"])
    assert report["family_quota"] is False and selected["family_quota"] is False
    assert report["provider_requests"] == report["real_orders_sent"] == 0
    assert report["real_routes"] == "NOT_CALLED" and len(wire[1]) == provider_calls
    assert all(path.read_bytes() == before[name] for name, path in inputs.items())
    assert store.open_positions() == [] and store.active_future_positions() == []
    approval["approval_digest"] = "0" * 64
    inputs["APPROVAL"].write_text(json.dumps(approval))
    restarted = ShadowRuntime(store.path, evidence_root=root, source_roots=[])
    invalid = restarted.tick(cut + timedelta(seconds=1))
    assert invalid["capacity_policy"]["status"] == "BASELINE_FAIL_CLOSED"
    assert invalid["capacity_policy"]["baseline_limits"] == {"EQUITY_SPOT": 20, "SCALPING": 40}
    assert invalid["production_limits_modified"] is False
    assert not invalid["checkpoint_reused"]
    fallback = restarted.capacity_controller.selection("EQUITY_SPOT", baseline, as_of=cut + timedelta(seconds=1))
    assert fallback["selected"] == baseline and not fallback["dynamic"]
    assert len(wire[1]) == provider_calls
