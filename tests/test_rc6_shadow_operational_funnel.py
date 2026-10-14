"""Native prospective funnel, exact identity and separate specialized ledgers."""
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from be_paper_engine import PaperStore
from bs_instrument_contracts import InstrumentContract
from rc6_dynamic_universe.common import digest as planner_digest
from rc6_paper_family_lifecycle import FamilyPaperExecutor
from rc6_shadow_runtime.entry_signals import native_entry_snapshot
from rc6_shadow_runtime import funnel

START = datetime(2026, 10, 5, 13, 29, tzinfo=timezone.utc)
IDENTITY = ["A", "ACCIONES", "BYMA", "ARS", "A-24HS"]


def plan(at, *, state="DISCOVERY", touched=None, attempts=(), promoted=None, opened=False, currency="ARS"):
    raw = ["A", "ACCIONES", "BYMA", currency, "A-24HS"]
    native = {"identity": raw, "last_touched_at": touched.isoformat() if touched else None,
        "source_at": touched.isoformat() if touched else None, "source": "PPI_MARKETDATA_CURRENT",
        "promoted_at": promoted.isoformat() if promoted else None,
        "warmup_complete_at": promoted.isoformat() if promoted and state == "HOT" else None, "attempts": list(attempts)}
    telemetry = {"identity": raw, "state": state, "strategy": "EQUITY_SPOT_SHADOW", "source": native["source"],
        "pipeline": {"CATALOG_READY": True, "STRATEGY_ELIGIBLE": True, "TRADEABLE": True},
        "rejection_reason": ["WARMUP_INCOMPLETE"] if state != "HOT" else [],
        "promotion_reasons": ["NEW_TRADES"], "promoted_at": native["promoted_at"],
        "warmup_progress": {"distinct_samples": 3 if state == "HOT" else 1, "required": 3},
        "discovery_age": (at-(touched or START)).total_seconds(), "discovery_age_lower_bound": touched is None,
        "revisit_seconds": 120, "achieved_revisit_seconds": 60 if len(attempts) >= 2 else None,
        "time_to_warmup_seconds": (promoted-START).total_seconds() if promoted and state == "HOT" else None}
    return {"schema": "SHADOW_FIXTURE", "session": "2026-10-05", "real_orders_sent": 0, "real_routes": "NOT_CALLED",
        "engines": {"EQUITY_SPOT": {"telemetry": [telemetry], "instruments": {planner_digest(raw): native},
            "opened_priority": [raw] if opened else [], "preopen_digest": "frozen-fixture", "configuration_fingerprint": "planned-fixture"}},
        "family_routing": {"instruments": [], "families": []}}


def snapshot(at, key="native-1", *, economics=True, risk="APPROVE", action="BUY", currency="ARS"):
    quote = {"symbol": "A", "asset_class": "ACCIONES", "settlement": "A-24HS", "currency": currency, "market": "BYMA",
        "last": "100", "bid": "100", "ask": "100.2", "bid_size": "100", "ask_size": "100",
        "book_at": at.isoformat(), "trade_at": at.isoformat(), "observed_at": at.isoformat(), "metadata_source": "PPI_NATIVE_FIXTURE"}
    payload = native_entry_snapshot(decision_key=key, quote=quote, action=action, score=".72", reason="NATIVE_FIXTURE",
        strategy_id="SPOT_MOMENTUM_BASELINE", strategy_version="spot-v1", signal_at=at.isoformat(),
        decision_at=(at+timedelta(seconds=1)).isoformat(), configuration_fingerprint="native-config",
        entry_signal_inputs={"schema": "rc6.native-entry-signal-input.v1", "momentum": ".006", "samples": 8},
        economics={"passed": economics, "net_reward_risk": "2"} if economics is not None else None)
    payload["decision"].update(patrimonial_gate=risk, final_result="OPENED_SIMULATED", paper_id="p1")
    return payload


def record_snapshot(store, value, *, corrupt=False):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with store.connect() as c:
        c.execute("INSERT INTO decision_evidence_snapshots VALUES(?,?,?,?,?)", (value["decision_key"], value["captured_at"],
            value["schema"], "bad" if corrupt else hashlib.sha256(raw.encode()).hexdigest(), raw))


def position(store, at, *, paper_id="p1", currency="ARS"):
    data = {"paper_id": paper_id, "source": "PAPER_NATIVE_FIXTURE", "strategy_version": "spot-v1", "symbol": "A",
        "asset_class": "ACCIONES", "settlement": "A-24HS", "status": "OPEN", "quantity": "1", "entry_price": "100.2",
        "entry_cost": ".1", "stop_price": "99", "target_price": "110", "opened_at": at.isoformat(), "currency": currency,
        "market": "BYMA", "features_json": json.dumps({"performance_lineage": {"strategy_id": "SPOT_MOMENTUM_BASELINE"}})}
    with store.connect() as c:
        c.execute("INSERT INTO paper_positions("+",".join(data)+") VALUES("+",".join("?" for _ in data)+")", tuple(data.values()))


def fill(store, at, *, paper_id="p1", side="BUY_SIMULATED", quantity="1", price="100.2", costs=".1"):
    with store.connect() as c:
        c.execute("INSERT INTO paper_fills(paper_id,source,side,filled_at,quantity,price,costs,slippage) VALUES(?,?,?,?,?,?,?,'0')",
            (paper_id, "PAPER_NATIVE_FIXTURE", side, at.isoformat(), quantity, price, costs))


def close(store, at, *, paper_id="p1"):
    with store.connect() as c:
        c.execute("UPDATE paper_positions SET status='CLOSED',closed_at=?,exit_price='105',exit_cost='.1',gross_pnl='4.8',net_pnl='4.6',close_reason='EOD_PAPER' WHERE paper_id=?",
            (at.isoformat(), paper_id))


def bootstrap(tmp_path):
    store = PaperStore(str(tmp_path/"source.db"))
    FamilyPaperExecutor(store)
    _, cp = funnel.evaluate_runtime_funnel(store.path, as_of=START, planner_report=plan(START))
    return store, cp


def stages(report, channel="NATIVE_FACTUAL", currency="ARS"):
    return next((r["stages"] for r in report["by_currency_channel"] if r["channel"] == channel and r["currency"] == currency), {})


def test_full_runtime_funnel_stages_to_closed_spot_net_and_restart(tmp_path):
    store, cp = bootstrap(tmp_path)
    at = START+timedelta(minutes=2)
    attempts = [[at.isoformat(), at.isoformat(), True]]
    _, cp = funnel.evaluate_runtime_funnel(store.path, as_of=at, previous=cp, planner_report=plan(at, touched=at, attempts=attempts))
    warm = at+timedelta(seconds=30)
    _, cp = funnel.evaluate_runtime_funnel(store.path, as_of=warm, previous=cp,
        planner_report=plan(warm, state="WARM", touched=at, attempts=attempts, promoted=warm))
    hot = warm+timedelta(seconds=60)
    attempts.append([hot.isoformat(), hot.isoformat(), True])
    _, cp = funnel.evaluate_runtime_funnel(store.path, as_of=hot, previous=cp,
        planner_report=plan(hot, state="HOT", touched=hot, attempts=attempts, promoted=hot))
    signal = hot+timedelta(seconds=1)
    record_snapshot(store, snapshot(signal)); position(store, signal+timedelta(seconds=2)); fill(store, signal+timedelta(seconds=2))
    _, cp = funnel.evaluate_runtime_funnel(store.path, as_of=signal+timedelta(seconds=4), previous=cp,
        planner_report=plan(signal+timedelta(seconds=4), state="HOT", promoted=hot))
    closed = signal+timedelta(seconds=10)
    fill(store, closed, side="SELL_SIMULATED", price="105"); close(store, closed)
    report, cp = funnel.evaluate_runtime_funnel(store.path, as_of=closed+timedelta(seconds=1), previous=cp,
        planner_report=plan(closed+timedelta(seconds=1), state="HOT", promoted=hot))
    assert set(stages(report, "UNIVERSE_SHADOW")) >= {"CATALOG_READY", "STRATEGY_ELIGIBLE", "TRADEABLE", "DISCOVERY_TOUCHED", "WARM", "HOT"}
    assert set(stages(report)) >= {"SIGNAL_EVALUATED", "SIGNAL_CANDIDATE", "ECONOMICS_PASS", "RISK_PASS", "PAPER_OPENED", "EXIT_REASON", "NET_PNL"}
    assert report["by_currency_channel"][0]["currency"] == "ARS"
    native = next(r for r in report["by_currency_channel"] if r["channel"] == "NATIVE_FACTUAL")
    assert Decimal(native["net_pnl"]) == Decimal("4.6") and Decimal(native["costs"]) == Decimal(".2")
    assert report["denominators"]["native_evaluations"] == 1
    assert report["denominators"]["independent_opportunities"] == "NOT_CLAIMED"
    again, _ = funnel.evaluate_runtime_funnel(store.path, as_of=closed+timedelta(seconds=2), previous=cp,
        planner_report=plan(closed+timedelta(seconds=2), state="HOT", promoted=hot))
    assert again["denominators"] == report["denominators"]
    assert stages(again)["NET_PNL"] == 1


def test_native_rejections_and_repeat_inputs_have_separate_denominators(tmp_path):
    store, cp = bootstrap(tmp_path)
    at = START+timedelta(minutes=2)
    first = snapshot(at, economics=False, risk="NOT_EVALUATED")
    first["decision"].update(final_result="BLOCKED", paper_id=None)
    second = snapshot(at, key="native-2", economics=True, risk="BLOCKED")
    second["decision"].update(final_result="BLOCKED", paper_id=None)
    record_snapshot(store, first); record_snapshot(store, second)
    report, _ = funnel.evaluate_runtime_funnel(store.path, as_of=at+timedelta(seconds=5), previous=cp, planner_report=plan(at))
    native = stages(report)
    assert native["ECONOMICS_FAIL"] == 1 and native["RISK_FAIL"] == 1 and "PAPER_OPENED" not in native
    assert report["denominators"]["native_evaluations"] == 2
    assert report["denominators"]["distinct_frozen_native_inputs"] == 1
    assert report["source_gaps"]["RISK_NOT_EVALUATED_OR_UNAVAILABLE"] == 1
    assert all(r["entry_authority"] is False for r in report["lineage"])


def test_funnel_cohorts_keep_all_identity_parts_and_native_configuration(tmp_path):
    store, cp = bootstrap(tmp_path)
    at = START + timedelta(minutes=2)
    for key, settlement, market, version, config in (
            ("a", "A-24HS", "BYMA", "spot-v1", "config-1"),
            ("b", "A-CI", "BYMA", "spot-v1", "config-1"),
            ("c", "A-24HS", "OTC", "spot-v1", "config-1"),
            ("d", "A-24HS", "BYMA", "spot-v2", "config-2")):
        value = snapshot(at, key=key)
        value["quote_used"].update(settlement=settlement, market=market)
        value["runtime"].update(strategy_version=version, configuration_fingerprint=config)
        record_snapshot(store, value)
    report, _ = funnel.evaluate_runtime_funnel(store.path, as_of=at+timedelta(seconds=5), previous=cp, planner_report=plan(at))
    native = [g for g in report["cohorts"] if g["channel"] == "NATIVE_FACTUAL"]
    assert len(native) == 4
    assert len({tuple(g["identity"]) for g in native}) == 3
    assert {(g["settlement"], g["market"]) for g in native} == {("A-24HS", "BYMA"), ("A-CI", "BYMA"), ("A-24HS", "OTC")}
    assert all(g["stages"]["SIGNAL_EVALUATED"] == 1 for g in native)
    assert report["denominators"]["distinct_frozen_native_inputs"] == 4


def test_native_repeat_receipts_do_not_inflate_distinct_market_signal_inputs(tmp_path):
    store, cp = bootstrap(tmp_path)
    at = START + timedelta(minutes=2)
    first = snapshot(at, key="first")
    first["inputs_used"]["entry_signal_inputs"].update(available_at=at.isoformat(), price_samples=[{
        "price": "100", "source_at": at.isoformat(), "received_at": at.isoformat(),
        "source": "NATIVE", "source_row_id": 1}])
    second = json.loads(json.dumps(first))
    second["decision_key"] = "repeat"
    received = (at + timedelta(seconds=10)).isoformat()
    second["quote_used"]["observed_at"] = received
    second["decision_at"] = received
    second["signal_at"] = received
    second["runtime"].update(decision_at=received, signal_at=received)
    second["inputs_used"]["entry_signal_inputs"]["available_at"] = received
    second["inputs_used"]["entry_signal_inputs"]["price_samples"][0].update(received_at=received, source_row_id=2)
    record_snapshot(store, first); record_snapshot(store, second)
    report, _ = funnel.evaluate_runtime_funnel(store.path, as_of=at+timedelta(seconds=11), previous=cp, planner_report=plan(at))
    assert report["denominators"]["native_evaluations"] == 2
    assert report["denominators"]["distinct_frozen_native_inputs"] == 1
    evidence = [r["detail"]["source_payload_sha256"] for r in report["lineage"] if r["stage"] == "SIGNAL_EVALUATED"]
    assert len(set(evidence)) == 2  # Original immutable captures remain distinct.


def test_selection_intent_opened_hot_and_same_clock_do_not_fabricate_warmup(tmp_path):
    store, cp = bootstrap(tmp_path)
    at = START+timedelta(minutes=2)
    attempts = [[at.isoformat(), at.isoformat(), True], [(at+timedelta(seconds=30)).isoformat(), at.isoformat(), True]]
    report, _ = funnel.evaluate_runtime_funnel(store.path, as_of=at+timedelta(seconds=31), previous=cp,
        planner_report=plan(at+timedelta(seconds=31), state="HOT", attempts=attempts, opened=True, promoted=at))
    assert "HOT" not in stages(report, "UNIVERSE_SHADOW") and "WARM" not in stages(report, "UNIVERSE_SHADOW")
    assert "DISCOVERY_TOUCHED" not in stages(report, "UNIVERSE_SHADOW")
    metric = report["observation_metrics"][0]
    assert metric["attempts"] == 2 and metric["distinct_observations"] == 1 and metric["distinct_fraction"] == .5
    assert "PAPER_OPENED" not in stages(report)


def test_partial_fills_terminal_position_update_is_not_lost_or_double_counted(tmp_path):
    store, cp = bootstrap(tmp_path)
    at = START+timedelta(minutes=2)
    position(store, at); fill(store, at)
    _, cp = funnel.evaluate_runtime_funnel(store.path, as_of=at+timedelta(seconds=1), previous=cp, planner_report=plan(at))
    fill(store, at+timedelta(seconds=10), side="SELL_SIMULATED", quantity=".4", price="105", costs=".05")
    partial, cp = funnel.evaluate_runtime_funnel(store.path, as_of=at+timedelta(seconds=11), previous=cp, planner_report=plan(at))
    assert "NET_PNL" not in stages(partial) and "EXIT_REASON" not in stages(partial)
    fill(store, at+timedelta(seconds=20), side="SELL_SIMULATED", quantity=".6", price="105", costs=".05"); close(store, at+timedelta(seconds=20))
    final, _ = funnel.evaluate_runtime_funnel(store.path, as_of=at+timedelta(seconds=21), previous=cp, planner_report=plan(at))
    assert stages(final)["PAPER_OPENED"] == 1 and stages(final)["EXIT_REASON"] == 1 and stages(final)["NET_PNL"] == 1
    assert not final["unreconciled_spot_positions"]


def test_existing_entry_never_rebuilt_and_currencies_remain_separate(tmp_path):
    store = PaperStore(str(tmp_path/"source.db")); FamilyPaperExecutor(store)
    position(store, START-timedelta(seconds=60), paper_id="old"); fill(store, START-timedelta(seconds=60), paper_id="old")
    _, cp = funnel.evaluate_runtime_funnel(store.path, as_of=START, planner_report=plan(START))
    at = START+timedelta(minutes=2)
    fill(store, at, paper_id="old", side="SELL_SIMULATED", price="105"); close(store, at, paper_id="old")
    position(store, at, paper_id="usd", currency="USD"); fill(store, at, paper_id="usd")
    report, cp = funnel.evaluate_runtime_funnel(store.path, as_of=at+timedelta(seconds=1), previous=cp, planner_report=plan(at))
    assert "PAPER_OPENED" not in stages(report, currency="ARS") and "NET_PNL" not in stages(report, currency="ARS")
    assert report["unreconciled_spot_positions"][0]["reason"] == "FILL_QUANTITY_RECONCILIATION"
    assert stages(report, currency="USD")["PAPER_OPENED"] == 1
    assert report["currencies_added_together"] is False


def dlr():
    return InstrumentContract("DLR/OCT26", "FUTUROS", "ARS", "A3", "INMEDIATA", Decimal("1000"), Decimal("1"),
        "PPI_PRIMARY+A3_OFFICIAL:TEST:v1", expires_at="2026-10-30T15:00:00-03:00", minimum_quantity=Decimal(1),
        paper_margin_policy="CONSERVATIVE_NOTIONAL_RATE", paper_margin_rate=Decimal(1), underlying="DOLAR_A3500")


def test_specialized_futures_variation_reserve_cash_net_reconciles_and_restart(tmp_path):
    store, cp = bootstrap(tmp_path)
    executor = FamilyPaperExecutor(store)
    contract = dlr()
    at = START+timedelta(minutes=2)
    executor.open_future(contract, lifecycle_id="future-1", event_id="future-open", entry_price="1500", quantity="1", entry_cost="100",
        occurred_at=at.isoformat(), book_at=at.isoformat())
    opened, cp = funnel.evaluate_runtime_funnel(store.path, as_of=at+timedelta(seconds=1), previous=cp, planner_report=plan(at))
    assert stages(opened)["PAPER_OPENED"] == 1
    var_at = at+timedelta(minutes=30)
    executor.mark_future(contract, lifecycle_id="future-1", event_id="variation", mark_price="1510", book_at=var_at.isoformat(),
        occurred_at=var_at.isoformat(), settlement=True)
    _, cp = funnel.evaluate_runtime_funnel(store.path, as_of=var_at+timedelta(seconds=1), previous=cp, planner_report=plan(var_at))
    closed = at+timedelta(hours=1)
    args = dict(lifecycle_id="future-1", event_id="future-close", exit_price="1520", exit_cost="100", book_at=closed.isoformat(),
        occurred_at=closed.isoformat(), reason="EOD_PAPER")
    executor.close_future(contract, **args)
    report, cp = funnel.evaluate_runtime_funnel(store.path, as_of=closed+timedelta(seconds=1), previous=cp, planner_report=plan(closed))
    money = next(r["detail"] for r in report["lineage"] if r["stage"] == "NET_PNL")
    assert money["gross"] == "20000" and money["net"] == "19800" and money["costs"] == "200"
    assert money["spot_ledger_used"] is False and money["variation_double_counted"] is False
    executor.close_future(contract, **args)
    repeated, _ = funnel.evaluate_runtime_funnel(store.path, as_of=closed+timedelta(seconds=2), previous=cp, planner_report=plan(closed))
    assert stages(repeated)["NET_PNL"] == 1 and not repeated["unreconciled_futures_positions"]
    with store.connect() as c: assert c.execute("SELECT count(*) FROM paper_positions").fetchone()[0] == 0


def test_specialized_futures_wrong_native_event_identity_cannot_reach_paper_open(tmp_path):
    store, cp = bootstrap(tmp_path)
    at = START + timedelta(minutes=2)
    FamilyPaperExecutor(store).open_future(dlr(), lifecycle_id="future-1", event_id="future-open", entry_price="1500",
        quantity="1", entry_cost="100", occurred_at=at.isoformat(), book_at=at.isoformat())
    with store.connect() as c:
        c.execute("UPDATE paper_family_lifecycle SET currency='USD' WHERE lifecycle_id='future-1'")
    with pytest.raises(ValueError, match="FUNNEL_FUTURES_EVENT_IDENTITY_MISMATCH"):
        funnel.evaluate_runtime_funnel(store.path, as_of=at+timedelta(seconds=1), previous=cp, planner_report=plan(at))
    assert cp["cursors"]["paper_family_lifecycle_events"] == 0


def test_specialized_futures_open_event_contract_hash_is_checked(tmp_path):
    store, cp = bootstrap(tmp_path)
    at = START + timedelta(minutes=2)
    FamilyPaperExecutor(store).open_future(dlr(), lifecycle_id="future-1", event_id="future-open", entry_price="1500",
        quantity="1", entry_cost="100", occurred_at=at.isoformat(), book_at=at.isoformat())
    with store.connect() as c:
        detail = json.loads(c.execute("SELECT detail_json FROM paper_family_lifecycle_events WHERE event_id='future-open'").fetchone()[0])
        detail["financial_contract"]["currency"] = "USD"
        c.execute("UPDATE paper_family_lifecycle_events SET detail_json=? WHERE event_id='future-open'", (json.dumps(detail),))
    with pytest.raises(ValueError, match="FUNNEL_FUTURES_EVENT_CONTRACT_PROVENANCE_MISMATCH"):
        funnel.evaluate_runtime_funnel(store.path, as_of=at+timedelta(seconds=1), previous=cp, planner_report=plan(at))


def test_catalog_and_observe_only_families_are_explicit_without_preopen(tmp_path):
    store = PaperStore(str(tmp_path/"source.db")); FamilyPaperExecutor(store)
    families = ["ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI"]
    catalog = [{"ticker": name, "instrument_type": name, "market": "BYMA", "currency": "ARS", "settlement": "A-24HS",
        "status": "AVAILABLE", "capability": "READY_PAPER_SHADOW"} for name in families]
    family_rows = [{"identity": [r[k] for k in ("ticker", "instrument_type", "market", "currency", "settlement")],
        "strategy": "LIFECYCLE_"+r["instrument_type"], "reason_codes": ["STRATEGY_NOT_VALIDATED"], "entry_authority": False} for r in catalog]
    p = {"engines": {}, "catalog_ready": catalog, "family_routing": {"instruments": family_rows,
        "families": [{"family": n, "strategy_status": "OBSERVE_ONLY", "entry_authority": False} for n in families]}}
    report, _ = funnel.evaluate_runtime_funnel(store.path, as_of=START, planner_report=p)
    assert stages(report, channel="FAMILY_OBSERVE_ONLY")["CATALOG_READY"] == 10
    assert "STRATEGY_ELIGIBLE" not in stages(report, channel="FAMILY_OBSERVE_ONLY")
    assert report["exclusions"]["STRATEGY_NOT_VALIDATED"] == 10 and len(report["family_policies"]) == 10


def test_identity_adapter_and_source_clock_hash_safety(tmp_path):
    assert funnel.planner_identity(IDENTITY) == ("A", "ACCIONES", "A-24HS", "ARS", "BYMA")
    with pytest.raises(ValueError, match="IDENTITY"):
        funnel.planner_identity(["A", "ACCIONES", "BYMA", "", "A-24HS"])
    store, cp = bootstrap(tmp_path)
    at = START+timedelta(minutes=2)
    record_snapshot(store, snapshot(at), corrupt=True)
    with pytest.raises(ValueError, match="HASH"):
        funnel.evaluate_runtime_funnel(store.path, as_of=at+timedelta(seconds=2), previous=cp, planner_report=plan(at))
    assert cp["cursors"]["decision_evidence_snapshots"] == 0
    with store.connect() as c: c.execute("UPDATE observer_state SET real_orders_sent=1")
    with pytest.raises(ValueError, match="SAFETY"):
        funnel.evaluate_runtime_funnel(store.path, as_of=at+timedelta(seconds=2), previous=cp, planner_report=plan(at))


def test_source_readonly_lock_and_detail_retention_keep_exact_session_totals(tmp_path, monkeypatch):
    monkeypatch.setattr(funnel, "MAX_DETAILS", 2)
    store, cp = bootstrap(tmp_path)
    at = START+timedelta(minutes=2)
    record_snapshot(store, snapshot(at))
    with sqlite3.connect(store.path) as c: c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = Path(store.path).read_bytes()
    blocker = sqlite3.connect(store.path); blocker.execute("BEGIN IMMEDIATE")
    try:
        report, cp = funnel.evaluate_runtime_funnel(store.path, as_of=at+timedelta(seconds=2), previous=cp, planner_report=plan(at))
    finally:
        blocker.rollback(); blocker.close()
    assert Path(store.path).read_bytes() == before
    assert report["detail_scope"]["details_truncated"] is True and len(report["lineage"]) == 2
    assert stages(report)["SIGNAL_EVALUATED"] == 1 and stages(report)["ECONOMICS_PASS"] == 1 and stages(report)["RISK_PASS"] == 1
    assert report["detail_scope"]["session_aggregates_preserved"] is True


def test_bounds_clock_reversal_and_fingerprint_reset(tmp_path, monkeypatch):
    store, cp = bootstrap(tmp_path)
    with pytest.raises(ValueError, match="FUTURE"):
        funnel.evaluate_runtime_funnel(store.path, as_of=START-timedelta(seconds=1), previous=cp, planner_report=plan(START))
    with pytest.raises(ValueError, match="READ_BUDGET"):
        funnel.evaluate_runtime_funnel(store.path, as_of=START, row_limit=True, planner_report=plan(START))
    changed, _ = funnel.evaluate_runtime_funnel(store.path, as_of=START+timedelta(seconds=1), previous=cp, row_limit=499, planner_report=plan(START))
    assert changed["checkpoint_invalidation"] == "FUNNEL_CONFIGURATION_CHANGED"
    monkeypatch.setattr(funnel, "MAX_CHECKPOINT_BYTES", 20)
    with pytest.raises(ValueError, match="CAPACITY"):
        funnel.evaluate_runtime_funnel(store.path, as_of=START, planner_report=plan(START))


def test_causal_discovery_only_reports_late_missed_when_contract_proves_it():
    event = START+timedelta(minutes=2)
    identity = dict(symbol="A", asset_class="ACCIONES", settlement="A-24HS", currency="ARS", market="BYMA")
    row = {"schema": funnel.DISCOVERY_EVIDENCE_SCHEMA, "identity": identity, "strategy_id": "SPOT_MOMENTUM_BASELINE", "strategy_eligible": True,
        "event_at": event.isoformat(), "available_at": event.isoformat(), "policy_registered_at": START.isoformat(),
        "deadline_at": (event+timedelta(seconds=30)).isoformat(), "source": "PPI_NATIVE_FIXTURE",
        "policy_version": "predeclared-v1",
        "discovery_touched_at": (event+timedelta(seconds=40)).isoformat()}
    def seal(value):
        return {**value, "evidence_digest": funnel.digest({k: v for k, v in value.items() if k != "evidence_digest"})}
    row = seal(row)
    measured = funnel.measure_discovery_outcomes([row, row], as_of=event+timedelta(seconds=50))
    assert len(measured["measured"]) == 1 and measured["measured"][0]["status"] == "LATE_DISCOVERY"
    absent = seal(dict(row, discovery_touched_at=None))
    assert funnel.measure_discovery_outcomes([absent], as_of=event+timedelta(seconds=50))["measured"] == []
    absent.update(continuous_coverage_verified=True, coverage_available_at=(event+timedelta(seconds=45)).isoformat(),
        coverage_through_at=(event+timedelta(seconds=40)).isoformat(), coverage_started_at=event.isoformat(),
        coverage_digest="native-coverage-ledger-pointer")
    absent = seal(absent)
    result = funnel.measure_discovery_outcomes([absent], as_of=event+timedelta(seconds=50))
    assert result["measured"][0]["status"] == "MISSED_DISCOVERY_WITH_VERIFIED_COVERAGE"
    future = seal(dict(absent, coverage_through_at=(event+timedelta(seconds=60)).isoformat()))
    assert funnel.measure_discovery_outcomes([future], as_of=event+timedelta(seconds=50))["measured"] == []
    corrupt = dict(row, evidence_digest="posthoc-corruption")
    assert funnel.measure_discovery_outcomes([corrupt], as_of=event+timedelta(seconds=50))["measured"] == []


def test_mutable_position_payload_is_bounded_before_materialization(tmp_path):
    store, cp = bootstrap(tmp_path)
    at = START+timedelta(minutes=2)
    position(store, at); fill(store, at)
    with store.connect() as c:
        c.execute("UPDATE paper_positions SET features_json=?", ('{"oversize":"' + 'x'*140000 + '"}',))
    with pytest.raises(ValueError, match="OVERSIZE"):
        funnel.evaluate_runtime_funnel(store.path, as_of=at+timedelta(seconds=1), previous=cp, planner_report=plan(at))
    assert cp["cursors"]["paper_fills"] == 0


def test_scalping_unevaluated_economics_does_not_fabricate_failed_gate(tmp_path):
    store, cp = bootstrap(tmp_path)
    at = START+timedelta(minutes=2)
    p = snapshot(at, economics=None, risk="NOT_EVALUATED", action="HOLD")
    p["runtime"]["strategy_id"] = "SCALPING_BASELINE"
    p["inputs_used"]["economics"] = {"binding": True, "execution_enabled": False, "passed": False}
    record_snapshot(store, p)
    report, _ = funnel.evaluate_runtime_funnel(store.path, as_of=at+timedelta(seconds=2), previous=cp, planner_report=plan(at))
    assert "ECONOMICS_FAIL" not in stages(report)
    assert report["source_gaps"]["ECONOMICS_NOT_EVALUATED_OR_UNAVAILABLE"] == 1
