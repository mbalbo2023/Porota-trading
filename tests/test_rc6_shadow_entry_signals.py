"""Prospective same-snapshot entry runtime over real SQLite contracts."""
import copy
import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from contextlib import closing
import gc

from be_paper_engine import PaperStore, Quote, D
from bs_instrument_contracts import InstrumentContract
from rc6_performance.common import canonical, digest
from rc6_shadow_runtime import entry_signals as entry

FREEZE = datetime(2026, 10, 5, 13, 29, tzinfo=timezone.utc)


def native_book(at, *, symbol="A", currency="ARS", family="ACCIONES", bid="100", ask="100.2"):
    out = {"symbol": symbol, "asset_class": family, "settlement": "A-24HS", "currency": currency, "market": "BYMA",
        "last": bid, "bid": bid, "ask": ask, "bid_size": "200", "ask_size": "100",
        "observed_at": at.isoformat(), "book_at": at.isoformat(), "trade_at": at.isoformat(),
        "last_kind": "TRADE", "metadata_source": "PPI_NATIVE_FIXTURE"}
    if family == "FUTUROS":
        contract = InstrumentContract("DLR/OCT26", "FUTUROS", "ARS", "A3", "INMEDIATA", Decimal("1000"), Decimal("1"),
            "PPI_PRIMARY+A3_OFFICIAL:TEST:v1", expires_at="2026-10-30T15:00:00-03:00", minimum_quantity=Decimal(1),
            paper_margin_policy="CONSERVATIVE_NOTIONAL_RATE", paper_margin_rate=Decimal(1), underlying="DOLAR_A3500")
        out.update(symbol=contract.symbol, settlement=contract.settlement, market=contract.market,
            financial_contract=json.loads(canonical(asdict(contract))))
    return out


def native_inputs(at, *, rvol=True):
    points = [{"price": str(price), "source_at": (at - timedelta(seconds=(3-i)*10)).isoformat(),
        "received_at": (at - timedelta(seconds=(3-i)*10)).isoformat(), "source": "PPI_NATIVE_FIXTURE"}
        for i, price in enumerate((100, 101, 100.5, 102))]
    out = {"schema": entry.NATIVE_INPUT_SCHEMA, "price_samples": points, "samples": 4,
        "momentum": ".006", "spread": ".002", "eod_at": (at + timedelta(hours=3)).isoformat()}
    if rvol:
        out["rvol"] = {"value": "1.5", "source": "PPI_PROFILE_FIXTURE", "source_at": at.isoformat(),
            "available_at": at.isoformat(), "units": "RATIO", "basis": "SAME_MINUTE_HISTORICAL_PROFILE"}
    return out


def native_decision(at, *, key="d1", action="HOLD", score=".41", strategy="SPOT_MOMENTUM_BASELINE",
                    currency="ARS", family="ACCIONES", inputs=None):
    return entry.native_entry_snapshot(decision_key=key, quote=native_book(at, currency=currency, family=family),
        action=action, score=score, reason="NATIVE_FIXTURE", strategy_id=strategy, strategy_version="native-v1",
        signal_at=(at+timedelta(seconds=1)).isoformat(), decision_at=(at+timedelta(seconds=2)).isoformat(),
        configuration_fingerprint="frozen-native-config", entry_signal_inputs=inputs or native_inputs(at),
        git_sha="a" * 40)


def insert_decision(store, payload, *, corrupt=False):
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with closing(store.connect()) as c, c:
        c.execute("INSERT INTO decision_evidence_snapshots VALUES(?,?,?,?,?)", (payload["decision_key"],
            payload["captured_at"], payload["schema"], "bad" if corrupt else hashlib.sha256(raw.encode()).hexdigest(), raw))


def insert_book(store, value):
    q = Quote(value["symbol"], value["asset_class"], value["settlement"],
        D(value["last"]), D(value["bid"]), D(value["ask"]), D(value["bid_size"]), D(value["ask_size"]),
        value["observed_at"], currency=value["currency"], market=value["market"],
        metadata_source=value["metadata_source"], book_at=value["book_at"], trade_at=value["trade_at"], last_kind="TRADE")
    store.add_quote(q)
    # Finalize native writer/UDF cycles before the coherent-copy reader.
    gc.collect()


def boot(tmp_path, *, config=None):
    store = PaperStore(str(tmp_path / "source.db"))
    gc.collect()
    first, checkpoint = entry.evaluate_runtime_entry_signals(store.path, as_of=FREEZE,
        registry_config=config or {"horizons": [60]})
    assert first["status"] == "START_AT_CURRENT_TAIL"
    return store, checkpoint


def test_same_snapshot_registry_native_baseline_and_causal_candidates(tmp_path, monkeypatch):
    store, checkpoint = boot(tmp_path)
    at = FREEZE + timedelta(minutes=2)
    payload = native_decision(at)
    insert_decision(store, payload)
    calls = []
    original = entry.evaluate_same_snapshot
    def capture(snapshot, evaluators, *, decision_at):
        calls.append((snapshot, list(evaluators)))
        return original(snapshot, evaluators, decision_at=decision_at)
    monkeypatch.setattr(entry, "evaluate_same_snapshot", capture)
    with closing(sqlite3.connect(store.path)) as connection:
        before = connection.execute("SELECT count(*) FROM paper_decisions").fetchone()[0]
    report, checkpoint = entry.evaluate_runtime_entry_signals(store.path, as_of=at+timedelta(seconds=5), previous=checkpoint,
        registry_config={"horizons": [60]})
    variants = report["experiments"][0]["variants"]
    assert len(calls) == 1 and len(variants) == 5
    assert len({v["input_sha256"] for v in variants.values()}) == 1
    assert variants["native-baseline:v1"]["decision"]["score"] == ".41" or variants["native-baseline:v1"]["decision"]["score"] == "0.41"
    assert variants["native-baseline:v1"]["decision"]["accepted"] is False
    assert variants["momentum-volatility:v1"]["decision"]["accepted"] is True
    assert variants["momentum-activity:v1"]["decision"]["accepted"] is True
    assert variants["microstructure:v1"]["decision"]["accepted"] is True
    assert all(v["decision"]["entry_authority"] is False for v in variants.values())
    assert report["score_is_probability"] is False and report["provider_requests"] == 0
    with closing(store.connect()) as c, c:
        assert c.execute("SELECT count(*) FROM paper_decisions").fetchone()[0] == before
        assert c.execute("SELECT count(*) FROM paper_positions").fetchone()[0] == 0


def test_hold_candidate_feature_does_not_override_native_action_and_nullable_buy_preserved(tmp_path):
    store, cp = boot(tmp_path)
    at = FREEZE + timedelta(minutes=2)
    hold = native_decision(at)
    hold["inputs_used"]["candidate"] = {"action": "BUY", "score": ".41"}
    buy = native_decision(at+timedelta(seconds=10), key="buy", action="BUY", score=".72")
    buy["decision"].update(action=None, score=None)
    insert_decision(store, hold); insert_decision(store, buy)
    report, _ = entry.evaluate_runtime_entry_signals(store.path, as_of=at+timedelta(seconds=15), previous=cp,
        registry_config={"horizons": [60]})
    baseline = {r["decision_key"]: r["variants"]["native-baseline:v1"]["decision"] for r in report["experiments"]}
    assert baseline["d1"]["accepted"] is False
    assert baseline["buy"]["accepted"] is True and Decimal(baseline["buy"]["score"]) == Decimal(".72")


def test_future_outcomes_are_isolated_and_mature_oos_with_truthful_anchor_and_costs(tmp_path):
    store, cp = boot(tmp_path)
    at = FREEZE + timedelta(minutes=2)
    payload = native_decision(at)
    payload["future_books"] = [{"price": 1000000}]
    payload["inputs_used"]["net_pnl"] = 999999
    insert_decision(store, payload)
    first, cp = entry.evaluate_runtime_entry_signals(store.path, as_of=at+timedelta(seconds=5), previous=cp, registry_config={"horizons": [60]})
    inp = first["experiments"][0]["input"]
    assert "net_pnl" not in inp["features"] and "future_books" not in inp
    label_time = at+timedelta(seconds=63)
    insert_book(store, native_book(at+timedelta(seconds=20), bid="99", ask="99.2"))
    insert_book(store, native_book(label_time, bid="103", ask="103.2"))
    report, cp = entry.evaluate_runtime_entry_signals(store.path, as_of=label_time+timedelta(seconds=1), previous=cp, registry_config={"horizons": [60]})
    label = report["experiments"][0]["labels"][0]
    assert label["status"] == "MEDIDO" and label["out_of_sample"] is True
    assert label["price_anchor"] == "SHADOW_QUOTE_ASK_TO_FUTURE_BID"
    assert label["future_book_at"] == label_time.isoformat()
    assert label["future_received_at"] == label["label_available_at"]
    assert label["horizon_clock_basis"].startswith("NATIVE_RECEIPT_AVAILABILITY")
    assert Decimal(label["mfe_observed"]) > 0 and Decimal(label["mae_observed"]) < 0
    cost = label["economic_sensitivity"]
    assert cost["status"] == "MODELED_PAPER_SENSITIVITY"
    assert Decimal(cost["net"]) < Decimal(cost["gross"]) and cost["currency"] == "ARS"
    assert Decimal(cost["net_return"]) == Decimal(cost["net"]) / Decimal(cost["entry_notional"])
    assert cost["costs"]["spread"] == "0.0" or Decimal(cost["costs"]["spread"]) == 0
    assert all(row["entry_hour_art"] == 10 and row["currency"] == "ARS" and row["regime"] == "NO_VERIFICADO" for row in report["cohorts"])
    assert all(row["auc_gross"] is None for row in report["cohorts"])
    repeated, again = entry.evaluate_runtime_entry_signals(store.path, as_of=label_time+timedelta(seconds=2), previous=cp, registry_config={"horizons": [60]})
    assert repeated["registered"] == 1 and repeated["cohorts"] == report["cohorts"]


def test_currency_and_strategy_cohorts_do_not_mix_futures_cost_model(tmp_path):
    store, cp = boot(tmp_path)
    at = FREEZE+timedelta(minutes=2)
    for currency, key, family, strategy in (("ARS", "ars", "ACCIONES", "SPOT_MOMENTUM_BASELINE"),
            ("USD", "usd", "CEDEARS", "SCALPING_BASELINE"),
            ("ARS", "fut", "FUTUROS", "futures-dlr-paper-v1")):
        inputs = native_inputs(at)
        if family == "FUTUROS":
            inputs.update(cash_multiplier="1000", fee_rate=".001", fee_provenance="NATIVE_PAPER_FIXTURE_RATE")
        insert_decision(store, native_decision(at, key=key, currency=currency, family=family, strategy=strategy, inputs=inputs))
    _, cp = entry.evaluate_runtime_entry_signals(store.path, as_of=at+timedelta(seconds=5), previous=cp, registry_config={"horizons": [60]})
    for currency, family in (("ARS", "ACCIONES"), ("USD", "CEDEARS"), ("ARS", "FUTUROS")):
        insert_book(store, native_book(at+timedelta(seconds=63), currency=currency, family=family, bid="103", ask="103.2"))
    report, _ = entry.evaluate_runtime_entry_signals(store.path, as_of=at+timedelta(seconds=65), previous=cp, registry_config={"horizons": [60]})
    assert {c["currency"] for c in report["cohorts"]} == {"ARS", "USD"}
    fut = next(r for r in report["experiments"] if r["decision_key"] == "fut")
    assert fut["labels"][0]["economic_sensitivity"]["status"] == "MODELED_PAPER_SENSITIVITY"
    assert Decimal(fut["labels"][0]["economic_sensitivity"]["gross"]) > 1000


def test_native_versions_and_configurations_never_share_oos_cohort(tmp_path):
    store, cp = boot(tmp_path)
    at = FREEZE + timedelta(minutes=2)
    for i, (version, config) in enumerate((("native-v1", "config-1"), ("native-v2", "config-1"), ("native-v2", "config-2"))):
        value = native_decision(at, key="version-" + str(i))
        value["runtime"].update(strategy_version=version, configuration_fingerprint=config)
        insert_decision(store, value)
    _, cp = entry.evaluate_runtime_entry_signals(store.path, as_of=at+timedelta(seconds=5), previous=cp,
        registry_config={"horizons": [60]})
    insert_book(store, native_book(at+timedelta(seconds=63), bid="103", ask="103.2"))
    report, _ = entry.evaluate_runtime_entry_signals(store.path, as_of=at+timedelta(seconds=65), previous=cp,
        registry_config={"horizons": [60]})
    baseline = [g for g in report["cohorts"] if g["variant"] == "native-baseline:v1"]
    assert len(baseline) == 3
    assert all(g["observations"] == 1 and g["auc_gross"] is None for g in baseline)
    assert {(g["native_strategy_version"], g["native_configuration_fingerprint"]) for g in baseline} == {
        ("native-v1", "config-1"), ("native-v2", "config-1"), ("native-v2", "config-2")}


@pytest.mark.parametrize("fault,reason", [
    ("native_clock", "NATIVE_SIGNAL_CLOCKS_REQUIRED"), ("signal_future", "NATIVE_SIGNAL_CLOCK_ORDER_INVALID"),
    ("provider_future", "NATIVE_PROVIDER_RECEIPT_CLOCK_INVALID"), ("old_signal", "ENTRY_NOT_AFTER_PREREGISTRATION"),
    ("future_feature", "FUTURE_ENTRY_FEATURE"), ("price_receipt", "NATIVE_PRICE_VECTOR_CLOCK_INVALID"),
    ("price_identity", "NATIVE_PRICE_VECTOR_IDENTITY_MISMATCH"), ("unregistered", "STRATEGY_NOT_VALIDATED"),
    ("wrong_schema", "NATIVE_ENTRY_INPUT_SCHEMA_INVALID"), ("crossed", "CROSSED_BOOK"),
])
def test_native_integrity_and_no_lookahead_guards(tmp_path, fault, reason):
    store, cp = boot(tmp_path)
    at = FREEZE+timedelta(minutes=2)
    p = native_decision(at)
    if fault == "native_clock": p["runtime"]["clock_mode"] = "EVENT_TIME_SIMULATION_UNVERIFIED"
    elif fault == "signal_future": p["signal_at"] = (at+timedelta(seconds=20)).isoformat()
    elif fault == "provider_future": p["quote_used"]["book_at"] = (at+timedelta(seconds=1)).isoformat()
    elif fault == "old_signal": p["signal_at"] = FREEZE.isoformat()
    elif fault == "future_feature": p["inputs_used"]["entry_signal_inputs"]["rvol"]["available_at"] = (at+timedelta(seconds=3)).isoformat()
    elif fault == "price_receipt": p["inputs_used"]["entry_signal_inputs"]["price_samples"][-1]["received_at"] = (at+timedelta(seconds=3)).isoformat()
    elif fault == "price_identity": p["inputs_used"]["entry_signal_inputs"]["price_samples"][0]["currency"] = "USD"
    elif fault == "unregistered": p["runtime"]["strategy_id"] = "FIXED_INCOME_MOMENTUM"
    elif fault == "wrong_schema": p["inputs_used"]["entry_signal_inputs"]["schema"] = "unknown"
    elif fault == "crossed": p["quote_used"]["bid"] = "200"
    insert_decision(store, p)
    report, _ = entry.evaluate_runtime_entry_signals(store.path, as_of=at+timedelta(seconds=5), previous=cp, registry_config={"horizons": [60]})
    assert report["registered"] == 0 and reason in report["rejections"]


def test_missing_native_features_remain_unverified_without_fake_rvol(tmp_path):
    store, cp = boot(tmp_path)
    at = FREEZE+timedelta(minutes=2)
    p = native_decision(at, inputs={"schema": entry.NATIVE_INPUT_SCHEMA, "momentum": ".004", "samples": 4})
    insert_decision(store, p)
    report, _ = entry.evaluate_runtime_entry_signals(store.path, as_of=at+timedelta(seconds=5), previous=cp, registry_config={"horizons": [60]})
    variants = report["experiments"][0]["variants"]
    assert variants["momentum-activity:v1"]["decision"]["reason"] == "CAUSAL_RVOL_UNAVAILABLE"
    assert variants["momentum-volatility:v1"]["decision"]["reason"] == "CAUSAL_VOLATILITY_UNAVAILABLE"
    assert variants["time-horizon:v1"]["decision"]["reason"] == "NATIVE_EOD_HORIZON_UNAVAILABLE"
    assert report["experiments"][0]["input"]["features"]["rvol"] is None


def test_explicit_missing_exact_vector_does_not_reconstruct_or_drop_native_baseline(tmp_path):
    store, cp = boot(tmp_path)
    at = FREEZE+timedelta(minutes=2)
    inputs = {"schema": entry.NATIVE_INPUT_SCHEMA, "momentum": ".004", "samples": 8,
        "price_samples": [], "price_sample_status": "NO_VERIFICADO"}
    insert_decision(store, native_decision(at, inputs=inputs))
    report, _ = entry.evaluate_runtime_entry_signals(store.path, as_of=at+timedelta(seconds=5), previous=cp,
        registry_config={"horizons": [60]})
    assert report["registered"] == 1
    record = report["experiments"][0]
    assert record["input"]["native_price_samples"] is None
    assert record["variants"]["native-baseline:v1"]["decision"]["status"] == "EVALUATED"
    assert record["variants"]["momentum-volatility:v1"]["decision"]["reason"] == "CAUSAL_VOLATILITY_UNAVAILABLE"


def test_futures_entry_lab_requires_exact_specialized_contract(tmp_path):
    store, cp = boot(tmp_path)
    at = FREEZE+timedelta(minutes=2)
    p = native_decision(at, family="FUTUROS", strategy="futures-dlr-paper-v1")
    p["quote_used"]["financial_contract"]["symbol"] = "DLR/OCT26-SPREAD"
    insert_decision(store, p)
    report, _ = entry.evaluate_runtime_entry_signals(store.path, as_of=at+timedelta(seconds=5), previous=cp,
        registry_config={"horizons": [60]})
    assert report["registered"] == 0 and report["rejections"]["EXACT_FUTURES_CONTRACT_REQUIRED"] == 1


@pytest.mark.parametrize("fault", ["multiplier", "fee_provenance"])
def test_futures_cost_uncertainty_cannot_certify_modeled_net_edge(tmp_path, fault):
    store, cp = boot(tmp_path)
    at = FREEZE + timedelta(minutes=2)
    inputs = native_inputs(at)
    inputs.update(cash_multiplier="1000", fee_rate=".001", fee_provenance="NATIVE_PAPER_FIXTURE_RATE")
    if fault == "multiplier": inputs["cash_multiplier"] = "1"
    else: inputs.pop("fee_provenance")
    insert_decision(store, native_decision(at, family="FUTUROS", strategy="futures-dlr-paper-v1", inputs=inputs))
    _, cp = entry.evaluate_runtime_entry_signals(store.path, as_of=at+timedelta(seconds=5), previous=cp,
        registry_config={"horizons": [60]})
    insert_book(store, native_book(at+timedelta(seconds=63), family="FUTUROS", bid="103", ask="103.2"))
    report, _ = entry.evaluate_runtime_entry_signals(store.path, as_of=at+timedelta(seconds=65), previous=cp,
        registry_config={"horizons": [60]})
    label = report["experiments"][0]["labels"][0]
    assert label["status"] == "MEDIDO" and label["economic_sensitivity"]["status"] == "NO_VERIFICADO"
    assert all(g["net_economic_observations"] == 0 and g["auc_modeled_net"] is None for g in report["cohorts"])


def test_missing_native_depth_censors_only_microstructure_hypothesis(tmp_path):
    store, cp = boot(tmp_path)
    at = FREEZE + timedelta(minutes=2)
    value = native_decision(at)
    value["quote_used"]["ask_size"] = None
    insert_decision(store, value)
    report, _ = entry.evaluate_runtime_entry_signals(store.path, as_of=at+timedelta(seconds=5), previous=cp,
        registry_config={"horizons": [60]})
    variants = report["experiments"][0]["variants"]
    assert variants["native-baseline:v1"]["decision"]["status"] == "EVALUATED"
    assert variants["microstructure:v1"]["decision"]["reason"] == "NATIVE_DEPTH_UNAVAILABLE"


def test_hash_source_safety_restart_and_budgets_are_fail_closed(tmp_path, monkeypatch):
    store, cp = boot(tmp_path)
    at = FREEZE+timedelta(minutes=2)
    insert_decision(store, native_decision(at), corrupt=True)
    with pytest.raises(ValueError, match="HASH"):
        entry.evaluate_runtime_entry_signals(store.path, as_of=at+timedelta(seconds=5), previous=cp, registry_config={"horizons": [60]})
    assert cp["cursors"]["decision_evidence_snapshots"] == 0
    with closing(store.connect()) as c, c:
        c.execute("UPDATE observer_state SET real_orders_sent=1")
    with pytest.raises(ValueError, match="SAFETY"):
        entry.evaluate_runtime_entry_signals(store.path, as_of=at+timedelta(seconds=5), previous=cp, registry_config={"horizons": [60]})
    with closing(store.connect()) as c, c: c.execute("UPDATE observer_state SET real_orders_sent=0")
    with pytest.raises(ValueError, match="FUTURE"):
        entry.evaluate_runtime_entry_signals(store.path, as_of=FREEZE-timedelta(seconds=1), previous=cp, registry_config={"horizons": [60]})
    changed, reset = entry.evaluate_runtime_entry_signals(store.path, as_of=at+timedelta(seconds=5), previous=cp, registry_config={"horizons": [61]})
    assert changed["status"] == "START_AT_CURRENT_TAIL" and reset["registered"] == 0
    assert reset["cursors"]["decision_evidence_snapshots"] == 1
    monkeypatch.setattr(entry, "MAX_CHECKPOINT_BYTES", 20)
    with pytest.raises(ValueError, match="CAPACITY"):
        entry.evaluate_runtime_entry_signals(store.path, as_of=at+timedelta(seconds=6), registry_config={"horizons": [60]})


def test_backlog_bounds_and_trading_writer_do_not_create_false_complete_labels(tmp_path):
    store, cp = boot(tmp_path)
    at = FREEZE+timedelta(minutes=2)
    for index in range(3): insert_decision(store, native_decision(at, key=f"d{index}"))
    blocker = sqlite3.connect(store.path)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        # A changed read bound deliberately invalidates source compatibility;
        # start at current tail and append new records for the bounded reader.
        _, bounded = entry.evaluate_runtime_entry_signals(store.path, as_of=at+timedelta(seconds=5), row_limit=1)
    finally:
        blocker.rollback(); blocker.close()
    for index in range(3): insert_decision(store, native_decision(at+timedelta(seconds=10), key=f"new{index}"))
    report, _ = entry.evaluate_runtime_entry_signals(store.path, as_of=at+timedelta(seconds=15), previous=bounded, row_limit=1)
    assert report["source_read_truncated"] is True and len(report["experiments"]) == 1
    assert report["experiments"][0]["path_censored"] is True


def test_registry_digest_and_input_outcome_rejection(tmp_path):
    registry = entry.preregister_entry_evaluators(registered_at=FREEZE, strategy_id="CUSTOM_NATIVE", horizons=[60])
    p = native_decision(FREEZE+timedelta(minutes=2), strategy="CUSTOM_NATIVE")
    inp = entry._project(p, registry); inp["label_horizon_seconds"] = 60
    valid = entry.evaluate_entry_snapshot(inp, registry, decision_at=inp["decision_at"])
    assert len(valid) == 5
    bad = copy.deepcopy(inp); bad["labels"] = [{"win": True}]
    with pytest.raises(ValueError, match="OUTCOME"):
        entry.evaluate_entry_snapshot(bad, registry, decision_at=inp["decision_at"])
    registry["definitions"][0]["version"] = "posthoc"
    with pytest.raises(ValueError, match="DIGEST"):
        entry.evaluate_entry_snapshot(inp, registry, decision_at=inp["decision_at"])


def test_oos_auc_uses_two_matured_classes_without_posthoc_score_inversion(tmp_path):
    store, cp = boot(tmp_path)
    first_at = FREEZE+timedelta(minutes=2)
    insert_decision(store, native_decision(first_at, key="positive", score=".2"))
    _, cp = entry.evaluate_runtime_entry_signals(store.path, as_of=first_at+timedelta(seconds=5), previous=cp,
        registry_config={"horizons": [60]})
    insert_book(store, native_book(first_at+timedelta(seconds=63), bid="103", ask="103.2"))
    _, cp = entry.evaluate_runtime_entry_signals(store.path, as_of=first_at+timedelta(seconds=64), previous=cp,
        registry_config={"horizons": [60]})
    second_at = first_at+timedelta(seconds=120)
    insert_decision(store, native_decision(second_at, key="negative", score=".8"))
    _, cp = entry.evaluate_runtime_entry_signals(store.path, as_of=second_at+timedelta(seconds=5), previous=cp,
        registry_config={"horizons": [60]})
    book = native_book(second_at+timedelta(seconds=63), bid="99", ask="99.2")
    insert_book(store, book)
    duplicate = dict(book, observed_at=(second_at+timedelta(seconds=64)).isoformat())
    insert_book(store, duplicate)
    report, _ = entry.evaluate_runtime_entry_signals(store.path, as_of=second_at+timedelta(seconds=65), previous=cp,
        registry_config={"horizons": [60]})
    cohort = next(c for c in report["cohorts"] if c["variant"] == "native-baseline:v1")
    assert cohort["observations"] == 2 and cohort["hit_rate_gross"] == .5 and cohort["auc_gross"] == 0.0
    assert cohort["sample_is_independent"] is False and cohort["score_is_probability"] is False
    assert cohort["observed_excursions_only"] is True
    negative = next(r for r in report["experiments"] if r["decision_key"] == "negative")
    assert negative["labels"][0]["observations"] == 1
    assert Decimal(negative["variants"]["native-baseline:v1"]["decision"]["score"]) == Decimal(".8")


def test_pure_snapshot_variants_are_isolated_even_with_input_mutation(monkeypatch):
    registry = entry.preregister_entry_evaluators(registered_at=FREEZE, strategy_id="SPOT_MOMENTUM_BASELINE", horizons=[60])
    payload = native_decision(FREEZE+timedelta(minutes=2))
    inp = entry._project(payload, registry); inp["label_horizon_seconds"] = 60
    original = entry._evaluate
    def mutate_first(definition, snapshot, *, decision_at):
        out = original(definition, snapshot, decision_at=decision_at)
        if definition["kind"] == "native":
            snapshot["features"]["momentum"] = "-999"
            snapshot["quote"]["ask"] = "1"
        return out
    monkeypatch.setattr(entry, "_evaluate", mutate_first)
    results = entry.evaluate_entry_snapshot(inp, registry, decision_at=inp["decision_at"])
    assert results["momentum-volatility:v1"]["decision"]["accepted"] is True
    assert inp["features"]["momentum"] == ".006" and inp["quote"]["ask"] == "100.2"
