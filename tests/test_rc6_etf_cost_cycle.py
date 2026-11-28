"""MERV's provider identity and exact synthetic PAPER fees through one ledger."""

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal as D, ROUND_HALF_EVEN
import json

import pytest

import be_paper_engine as engine
import cf_intraday_scalping as scalping
from bq_exit_policy import PaperSessionPolicy
from dh_paper_dynamic_risk_gate_hf6 import position_stop_risk
from rc6_performance import costs
from tests.test_rc6_convergence_core_admission import points


AT = datetime.fromisoformat("2026-11-24T12:00:00-03:00")


def synthetic_terms(*, rebate="NONE", minimum="8", commission=".001"):
    unresolved = costs.paper_cost_contract("ACCIONES", "ARS", AT, symbol="MERV",
                                           market="BYMA", settlement="A-24HS")
    return replace(unresolved, source="SYNTHETIC_FIXTURE_ONLY", commission=D(commission),
                   commission_vat=True, rights=D(".0005"), rights_vat=False,
                   clearing=D(".0002"), clearing_vat=False, premium_rights=D(0),
                   premium_rights_vat=False, minimum_commission=D(minimum), tiers=(),
                   rebate_policy=rebate)


def quote(at=AT, *, bid="99.9", ask="100", size="1000"):
    return engine.Quote("MERV", "ACCIONES", "A-24HS", D(bid), D(bid), D(ask), D(size), D(size),
                        at.isoformat(), currency="ARS", market="BYMA", metadata_source="SYNTHETIC",
                        book_at=at.isoformat(), trade_at=at.isoformat(), last_kind="TRADE")


def broker(store, clock, **settings):
    config = dict(initial_cash="10000", risk_pct=".02", max_position_pct="1", max_total_exposure_pct="1",
                  session_policy=PaperSessionPolicy(), slippage_bps="0", ai_mode="OFF",
                  economics_mode="BINDING", target_gain_pct=".10", stop_loss_pct=".02",
                  daily_soft_stop_pct="8", daily_loss_pct="10")
    return engine.PaperBroker(store, clock_fn=lambda: clock[0].isoformat(), **(config | settings))


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "OBSERVATION_ONLY")
    store = engine.PaperStore(str(tmp_path / "synthetic.db"))
    return store, [AT]


def fill_entry(store, clock, term, monkeypatch):
    monkeypatch.setattr(costs, "paper_cost_contract", lambda *args, **kwargs: term)
    active = broker(store, clock, intraday_fee_rebate=True)
    q = quote()
    store.add_quote(q)
    assert active._economic_diagnostics(q)["quantity_pending"] is True
    features = {}
    assert active.admit_paper_candidate(q, D(".9"), features)[0]
    return active, store.open_position("MERV"), features


@pytest.mark.parametrize("rebate_policy", ["NONE", "COMMISSION_ON_SMALLER_INTRADAY_LEG"])
def test_exact_etf_sizing_atomic_entry_and_partial_exit_use_frozen_policy(book, monkeypatch, rebate_policy):
    store, clock = book
    policy = synthetic_terms(rebate=rebate_policy, minimum="10")
    active, position, features = fill_entry(store, clock, policy, monkeypatch)
    quantity = D(position["quantity"])
    assert position["asset_class"] == "ACCIONES"
    buy_cost = policy.leg_cost(D(100) * quantity, decision_at=AT)
    stop_cost = policy.leg_cost(D(98) * quantity, decision_at=AT)
    expected_risk = D(2) * quantity + buy_cost + stop_cost
    assert expected_risk <= D(200) < (D(2) * (quantity + 1) +
        policy.leg_cost(D(100) * (quantity + 1), decision_at=AT) +
        policy.leg_cost(D(98) * (quantity + 1), decision_at=AT))
    assert D(features["candidate_stop_risk"]) == position_stop_risk(active, position) == expected_risk
    economics = features["economics"]
    assert D(economics["sized_quantity"]) == quantity and D(economics["entry_cost"]) == buy_cost
    assert economics["cost_contract"]["policy_sha256"] == policy.diagnostic()["policy_sha256"]

    # An operator change or a future tariff update cannot reprice an old fill.
    monkeypatch.setattr(costs, "paper_cost_contract", lambda *a, **k: pytest.fail("new tariff at exit"))
    restarted = broker(engine.PaperStore(store.path), clock, intraday_fee_rebate=False)
    monkeypatch.setattr(restarted, "_cost", lambda *a, **k: pytest.fail("legacy ETF fee"))
    clock[0] += timedelta(minutes=1)
    first = quote(clock[0], bid="110", ask="110.1", size="200")
    assert restarted._close(position, first, "SYNTHETIC_PARTIAL")
    remaining = restarted.store.open_position("MERV")
    assert D(remaining["quantity"]) == quantity - 20
    clock[0] += timedelta(minutes=1)
    final = quote(clock[0], bid="97", ask="97.1")
    assert restarted._close(remaining, final, "SYNTHETIC_LAST_FILL")

    first_cost = policy.leg_cost(D(2200), decision_at=AT)
    second_cost = policy.leg_cost(D(97) * (quantity - 20), decision_at=AT)
    if rebate_policy != "NONE":
        full_entry_rebate = buy_cost - policy.leg_cost(D(100) * quantity, decision_at=AT, rebated=True)
        first_cost -= (full_entry_rebate * 20 / quantity).quantize(D(".01"), rounding=ROUND_HALF_EVEN)
        second_cost = policy.leg_cost(D(97) * (quantity - 20), decision_at=AT, rebated=True)
    with store.connect() as db:
        fills = [dict(row) for row in db.execute("SELECT * FROM paper_fills ORDER BY id")]
        closed = dict(db.execute("SELECT * FROM paper_positions WHERE paper_id=?", (position["paper_id"],)).fetchone())
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0
    assert [(row["side"], D(row["costs"])) for row in fills] == [
        ("BUY_SIMULATED", buy_cost), ("SELL_SIMULATED", first_cost), ("SELL_SIMULATED", second_cost)]
    assert costs.realized_round_trip_cost(closed, fills)["costs"] == buy_cost + first_cost + second_cost


def test_etf_joint_sizer_finds_affordable_tier_after_an_unaffordable_gap():
    policy = replace(synthetic_terms(), tiers=(costs.FeeTier(0, 0, 1000), costs.FeeTier(1000, 0, 1)))
    # Nine lots are too expensive. Ten reach the reviewed tier on both legs.
    assert policy.affordable_quantity(entry_unit=D(100), stop_unit=D(100), lot=D(1),
        quantity_limit=D(10), cash=D(1100), risk=D(10), decision_at=AT) == 10


@pytest.mark.parametrize("change", ["missing_receipt", "modified_tariff", "modified_fill"])
def test_existing_etf_does_not_price_with_missing_or_changed_evidence(book, monkeypatch, change):
    store, clock = book
    policy = synthetic_terms()
    active, position, features = fill_entry(store, clock, policy, monkeypatch)
    with store.connect() as db:
        if change == "missing_receipt":
            db.execute("DELETE FROM decision_evidence_snapshots WHERE decision_key=?",
                       (features["financial_admission_snapshot_key"],))
        elif change == "modified_tariff":
            altered = json.loads(position["features_json"])
            altered["economics"]["cost_contract"] = replace(policy, commission=D(".005")).diagnostic()
            db.execute("UPDATE paper_positions SET features_json=? WHERE paper_id=?",
                       (json.dumps(altered), position["paper_id"]))
        else:
            db.execute("UPDATE paper_fills SET costs='1' WHERE paper_id=?", (position["paper_id"],))
    clock[0] += timedelta(minutes=1)
    assert not active._close(store.open_position("MERV"), quote(clock[0]), "SYNTHETIC_EXIT")
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0] == 0
        assert db.execute("SELECT detail FROM paper_events WHERE event_type='EXIT_PENDING_COST_CONTRACT'").fetchone()[0] == (
            "PAPER_ETF_COST_RECEIPT_INVALID")


def test_etf_policy_cannot_change_between_sizing_and_the_fill_transaction(book, monkeypatch):
    store, clock = book
    terms = synthetic_terms()
    responses = iter((terms, terms, replace(terms, minimum_commission=D(9))))
    monkeypatch.setattr(costs, "paper_cost_contract", lambda *a, **k: next(responses))
    hold = broker(store, clock)
    store.add_quote(quote())
    assert hold.admit_paper_candidate(quote(), D(".9"), {}) == (
        False, "PAPER_COST_POLICY_CHANGED_BEFORE_ADMISSION", None)
    assert store.open_position("MERV") is None


def test_iol_spot_fills_preserve_paper_cost_after_configuration_changes(book, monkeypatch):
    store, clock = book
    active = broker(store, clock, intraday_fee_rebate=False)
    entry = replace(quote(), symbol="GGAL", metadata_source="IOL_REST")
    store.add_quote(entry)
    features = {}
    assert active.admit_paper_candidate(entry, D(".9"), features)[0]
    position = store.open_position(entry.symbol)
    original = costs.VersionedPaperCostContract.from_diagnostic(features["economics"]["cost_contract"])
    assert original.diagnostic()["tariff_provenance"]["iol_tariff_evidence"] == "NO_VERIFICADO"

    monkeypatch.setenv("FEE_ACCIONES_COMISION", "0.2")
    restarted = broker(engine.PaperStore(store.path), clock, intraday_fee_rebate=True)
    prospective = restarted._economic_diagnostics(entry)
    assert not prospective["passed"]
    assert D(prospective["net_reward_per_unit"]) < D(features["economics"]["net_reward_per_unit"])
    assert prospective["cost_contract"]["tariff_provenance"]["assumption_basis"] == "PAPER_COMPONENT_OVERRIDES"
    assert prospective["cost_contract"]["tariff_provenance"]["tariff_provider"] == "NO_VERIFICADO"

    exit_cases = [("110", "200"), ("98", "1000")]
    for price, size in exit_cases:
        clock[0] += timedelta(minutes=1)
        exit_q = replace(quote(clock[0], bid=price, ask=str(D(price) + D("0.1")), size=size),
                         symbol=entry.symbol, metadata_source="IOL_REST")
        assert restarted._close(restarted.store.open_position(entry.symbol), exit_q, "SYNTHETIC_EXIT")
    with store.connect() as db:
        fills = [dict(row) for row in db.execute("SELECT * FROM paper_fills ORDER BY id")]
        closed = dict(db.execute("SELECT * FROM paper_positions WHERE paper_id=?", (position["paper_id"],)).fetchone())
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0
    assert [row["side"] for row in fills] == ["BUY_SIMULATED", "SELL_SIMULATED", "SELL_SIMULATED"]
    for fill in fills:
        notional = D(fill["price"]) * D(fill["quantity"])
        assert D(fill["costs"]) == original.leg_cost(notional, decision_at=fill["filled_at"])
    assert costs.realized_round_trip_cost(closed, fills)["costs"] == sum(D(row["costs"]) for row in fills)


@pytest.mark.parametrize("commission,expected", [(".001", "OPENED_SIMULATED"),
    (".05", "BLOCKED:ECONOMICS_BINDING_RANGE_BELOW_COST")])
def test_scalping_etf_range_waits_for_exact_sized_cost(book, monkeypatch, commission, expected):
    import bv_paper_runtime as runtime
    import rc6_paper_admission_gate as gate

    store, clock = book
    policy = synthetic_terms(commission=commission)
    monkeypatch.setattr(costs, "paper_cost_contract", lambda *a, **k: policy)
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setattr(gate, "paper_admission_guard", lambda q, store: (True, ""))
    monkeypatch.setattr(runtime, "broker_from_environment", lambda store, **kwargs:
        broker(store, clock, admission_guard=kwargs["admission_guard"], economics_mode="SHADOW"))
    record = {"ticker": "MERV", "instrument_type": "ACCIONES", "currency": "ARS", "market": "BYMA",
              "settlement": "A-24HS", "capability": "READY_PAPER_SPOT", "status": "AVAILABLE"}
    scalping.init_schema(store)
    previous = AT - timedelta(minutes=1)
    scalping.persist_payload(store, record, points(previous, 15), received_at=previous)
    scalping.persist_payload(store, record, points(AT), received_at=AT)
    store.add_quote(quote(bid="107.50", ask="107.55"))
    assert scalping.evaluate_candidate(store, record, at=AT) == "BUY_CANDIDATE"
    assert scalping.promote_paper_candidate(store, record, at=AT) == expected
    with store.connect() as c:
        evidence = c.execute("SELECT detail_json FROM trade_gate_evaluations").fetchone()
        observed = json.loads(evidence[0])["scalping_range_screen"]
        assert observed["quantity_pending"] is False
        assert observed["passed"] == (expected == "OPENED_SIMULATED")
        assert c.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0
