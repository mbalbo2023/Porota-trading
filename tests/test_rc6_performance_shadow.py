import sys
from pathlib import Path
from decimal import Decimal as D

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rc6_performance.shadow import (forward_labels, fit_movement, MovementModel,
                                    economics_gate, evaluate_same_snapshot)
from rc6_performance.replay import ExitPolicy, ExitReplay
from rc6_performance.metrics import decision_funnel, exit_latencies, signal_evaluation
from test_rc6_performance_costs import fees


def book(at="2026-09-09T14:00:00Z", bid="100", size="100"):
    return dict(symbol="GGAL", asset_class="ACCIONES", settlement="A-24HS", currency="ARS", market="BYMA",
                bid=bid, ask=str(D(bid)+1), bid_size=size, ask_size=size,
                observed_at=at, book_at=at, source="PPI_PRIMARY")


def entry():
    return {k: v for k, v in book().items() if k in {"symbol", "asset_class", "settlement", "currency", "market"}} | {
        "opened_at": "2026-09-09T14:00:00Z", "decision_at": "2026-09-09T14:00:00Z",
        "entry_price": "100", "quantity": "3", "score": ".7"}


def model():
    return MovementModel("2026-09-08T20:00:00Z", 600, "ACCIONES", "ARS", 50, D(".03"), "frozen-test")


def test_labels_require_real_horizon_and_do_not_fill_unobserved_time():
    future = book("2026-09-09T14:10:00Z", "102")
    result = forward_labels(entry(), [future], [600, 3600], as_of="2026-09-09T14:11:00Z")
    assert result[0]["gross_forward_return"] == D(".02")
    assert result[1]["status"] == "NO_VERIFICADO"
    assert not result[0]["execution_guaranteed"]
    assert signal_evaluation(result)["unmeasured"] == 1


def test_no_future_labels_or_naive_clocks_in_training():
    labels = forward_labels(entry(), [book("2026-09-09T14:10:00Z", "102")], [600], as_of="2026-09-09T14:11:00Z")
    with pytest.raises(ValueError, match="FUTURE_LABEL"):
        fit_movement(labels, training_cutoff="2026-09-09T14:05:00Z", horizon_seconds=600,
                     family="ACCIONES", currency="ARS", minimum_observations=1)
    with pytest.raises(ValueError, match="AWARE"):
        forward_labels(entry(), [], [600], as_of="2026-09-09T14:11:00")


def test_old_book_receipt_does_not_become_forward_label_and_duplicates_cannot_train():
    delayed = book("2026-09-09T14:01:00Z", "102") | {"book_at": "2026-09-09T13:59:30Z"}
    result = forward_labels(entry(), [delayed], [60], as_of="2026-09-09T14:02:00Z")
    assert result[0]["status"] == "NO_VERIFICADO"
    labels = forward_labels(entry(), [book("2026-09-09T14:10:00Z", "102")], [600], as_of="2026-09-09T14:11:00Z")
    assert labels[0]["observations"] == 1
    with pytest.raises(ValueError, match="DUPLICATE"):
        fit_movement(labels*2, training_cutoff="2026-09-09T14:12:00Z", horizon_seconds=600,
                     family="ACCIONES", currency="ARS", minimum_observations=2)


def gate(q=None, movement=None, **kwargs):
    return economics_gate(q or book(), decision_at="2026-09-09T14:00:00Z", eod_at="2026-09-09T19:50:00Z",
                          quantity=1, multiplier=1, model=movement, fees=fees(), **kwargs)


def test_missing_expected_move_is_not_an_invented_profit_forecast():
    result = gate()
    assert result["status"] == "NO_VERIFICADO" and result["accepted"] is None
    assert result["real_order_routes"] == []


def test_shadow_economics_is_out_of_sample_and_not_binding():
    result = gate(movement=model())
    assert result["accepted"] is True and result["decision_effect"] == "NONE"
    assert result["economic_edge_validated"] is False
    future = MovementModel("2026-09-10T00:00:00Z", 600, "ACCIONES", "ARS", 50, D(".03"), "bad")
    with pytest.raises(ValueError, match="FROZEN"): gate(movement=future)


@pytest.mark.parametrize("mutate,reason", [("stale", "stale_book"), ("future", "future_quote"),
                                           ("depth", "insufficient_depth"), ("crossed", "crossed_book")])
def test_economic_gate_preserves_freshness_and_depth(mutate, reason):
    q = book()
    if mutate == "stale": q["book_at"] = "2026-09-09T13:00:00Z"
    if mutate == "future": q["book_at"] = "2026-09-09T14:01:00Z"
    if mutate == "depth": q["bid_size"] = "0"
    if mutate == "crossed": q["ask"] = "99"
    assert gate(q, model())["reason"] == reason


def test_time_to_eod_does_not_extend_horizon_to_make_trade_fit():
    result = economics_gate(book(), decision_at="2026-09-09T14:00:00Z", eod_at="2026-09-09T14:05:00Z",
                            quantity=1, multiplier=1, model=model(), fees=fees())
    assert result["accepted"] is False and result["reason"] == "EOD_too_close"


def test_every_shadow_variant_gets_same_immutable_snapshot():
    original = book()
    def mutate(x, **_):
        x["bid"] = "0"
        return "candidate"
    result = evaluate_same_snapshot(original, {"A": mutate, "BASELINE": lambda x, **_: x["bid"]},
                                    decision_at=original["observed_at"])
    assert result["BASELINE"]["decision"] == "100" and original["bid"] == "100"
    assert result["A"]["input_sha256"] == result["BASELINE"]["input_sha256"]


def replay(policy=None):
    return ExitReplay(entry(), policy or ExitPolicy("BASELINE", D(".02"), D(".05"), 3600), fees(),
                      eod_at="2026-09-09T19:50:00Z", session_close_at="2026-09-09T20:00:00Z", slippage="0")


def test_same_entry_stop_partial_fills_and_repeated_book_budget():
    state = replay()
    q = book("2026-09-09T14:01:00Z", "97", "10")
    first = state.advance(q, as_of=q["observed_at"])
    assert first["state"] == "EXIT_PENDING" and first["remaining"] == 2
    assert state.advance(q, as_of=q["observed_at"])["remaining"] == 2
    second = book("2026-09-09T14:02:00Z", "103", "20")
    final = state.advance(second, as_of=second["observed_at"])
    assert final["state"] == "CLOSED" and final["reason"] == "STOP_PAPER"
    assert len(final["fills"]) == 2 and final["real_order_routes"] == []


def test_eod_precedes_stop_and_target_and_never_executes_overnight():
    state = replay(ExitPolicy("factual", D(".02"), D(".05"), 99999))
    q = book("2026-09-09T19:50:00Z", "90")
    assert state.advance(q, as_of=q["observed_at"])["reason"] == "EOD_PAPER"
    state = replay(ExitPolicy("factual", D(".02"), D(".05"), 99999))
    assert state.advance(None, as_of="2026-09-09T19:50:00Z")["state"] == "EXIT_PENDING"
    q = book("2026-09-10T14:00:00Z", "110")
    assert state.advance(q, as_of=q["observed_at"])["fills"] == []


def test_max_hold_detects_without_quote_and_future_book_cannot_execute():
    state = replay()
    result = state.advance(None, as_of="2026-09-09T15:00:00Z")
    assert result["reason"] == "MAX_HOLD_PAPER"
    q = book("2026-09-09T15:01:00Z", "110")
    assert state.advance(q, as_of="2026-09-09T15:00:00Z")["fills"] == []
    with pytest.raises(ValueError, match="TIME_REVERSED"):
        state.advance(None, as_of="2026-09-09T14:00:00Z")


def test_factual_and_candidate_are_deterministic_and_do_not_promote():
    policies = [ExitPolicy("BASELINE", D(".02"), D(".05"), 3600), ExitPolicy("TP_SHADOW", D(".02"), D(".01"), 3600)]
    def run():
        states = [replay(p) for p in policies]
        for q in [book("2026-09-09T14:10:00Z", "102"), book("2026-09-09T15:00:00Z", "101")]:
            for state in states: state.advance(q, as_of=q["observed_at"])
        return [s.result() for s in states]
    assert run() == run()
    factual, candidate = run()
    assert factual["reason"] == "MAX_HOLD_PAPER" and candidate["reason"] == "TAKE_PROFIT_PAPER"
    assert factual["factual_entry"] == candidate["factual_entry"]


def test_break_even_once_armed_survives_pullback_and_trailing_uses_only_observed_high():
    state = replay(ExitPolicy("BE_SHADOW", D(".02"), D(".05"), 3600, minimum_net_lock=D(".001")))
    q = book("2026-09-09T14:01:00Z", "102")
    assert state.advance(q, as_of=q["observed_at"])["state"] == "OPEN"
    q = book("2026-09-09T14:02:00Z", "100.5")
    result = state.advance(q, as_of=q["observed_at"])
    assert result["state"] == "CLOSED" and result["reason"] == "STOP_PAPER"
    # A gap through modeled break-even is not a guaranteed nonnegative net fill.
    assert result["net"] < 0 and not result["economic_edge_validated"]
    trailing = replay(ExitPolicy("TRAIL_SHADOW", D(".02"), D(".05"), 3600, trailing_fraction=D(".01")))
    q = book("2026-09-09T14:01:00Z", "102")
    assert trailing.advance(q, as_of=q["observed_at"])["state"] == "OPEN"
    q = book("2026-09-09T14:02:00Z", "100.9")
    assert trailing.advance(q, as_of=q["observed_at"])["reason"] == "STOP_PAPER"


def test_missing_lineage_is_exposed_without_quote_time_becoming_decision_time():
    snapshots = [{"decision_key": "a", "captured_at": book()["observed_at"], "quote_used": book(),
                  "decision": {"technical_gate": "APPROVE", "final_result": "BLOCKED", "reason": "risk_limit"},
                  "runtime": {"strategy_version": "old"}}]
    report = decision_funnel(snapshots * 2)
    assert report["decisions"] == 1 and report["stages"]["REJECTED"] == 1
    assert report["lineage"][0]["decision_at"] is None
    assert report["evidence_gaps"]["git_sha"] == 1


def test_exit_latencies_separate_phases_and_unknown_final_fill():
    probes = [dict(paper_id="p", stage="CONDITION", at="2026-09-09T14:00:00Z"),
              dict(paper_id="p", stage="INTENT_COMMITTED", at="2026-09-09T14:00:01Z"),
              dict(paper_id="p", stage="FINAL_FILL", at="2026-09-09T14:00:10Z")]
    fs = [dict(paper_id="p", side="SELL_SIMULATED", filled_at="2026-09-09T14:00:03Z")]
    result = exit_latencies(probes, fs)
    assert result["seconds"]["condition_to_intent"]["p99"] == 1
    assert result["seconds"]["first_fill_to_final_fill"]["max"] == 7
    assert result["causal_sqlite_attribution"] == "NO_VERIFICADO"
