"""Prospective runtime economics/exit acceptance over the real SQLite schema."""
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from rc6_performance.common import canonical, digest
from rc6_performance.costs import expected_round_trip_cost, ledger_leg_cost, paper_fee_model
from rc6_shadow_runtime.lab import evaluate_runtime_lab

START = datetime(2026, 10, 5, 14, tzinfo=timezone.utc)
IDENTITY = {"symbol": "GGAL", "asset_class": "ACCIONES", "settlement": "A-24HS",
            "currency": "ARS", "market": "BYMA"}
CONFIG = {"slippage": "0", "participation": "0.1"}


def clock(seconds):
    return (START+timedelta(seconds=seconds)).isoformat()


def source(tmp_path):
    path = tmp_path/"paper.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript("""
          CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER);
          INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0);
          CREATE TABLE paper_positions(paper_id TEXT PRIMARY KEY,source TEXT,strategy_version TEXT,
            symbol TEXT,asset_class TEXT,settlement TEXT,currency TEXT,market TEXT,status TEXT,
            quantity TEXT,entry_price TEXT,entry_cost TEXT,stop_price TEXT,target_price TEXT,
            opened_at TEXT,closed_at TEXT,exit_price TEXT,exit_cost TEXT,net_pnl TEXT,features_json TEXT);
          CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,source TEXT,symbol TEXT,
            asset_class TEXT,settlement TEXT,currency TEXT,market TEXT,observed_at TEXT,book_at TEXT,
            trade_at TEXT,last_kind TEXT,last TEXT,bid TEXT,ask TEXT,bid_size TEXT,ask_size TEXT);
          CREATE TABLE decision_evidence_snapshots(decision_key TEXT PRIMARY KEY,captured_at TEXT,
            schema_version TEXT,payload_sha256 TEXT,payload_json TEXT);
        """)
    return path


def quote(at, bid="100", *, last=None, book_at=None, trade_at=None, size="1000"):
    return IDENTITY | {"source": "PPI_PRIMARY", "observed_at": at, "book_at": book_at or at,
        "trade_at": trade_at or at, "last_kind": "TRADE", "last": last or bid,
        "bid": bid, "ask": str(Decimal(bid)+Decimal(".1")), "bid_size": size, "ask_size": size}


def add_quote(path, book):
    with sqlite3.connect(path) as connection:
        columns = ",".join(book)
        connection.execute(f"INSERT INTO market_snapshots({columns}) VALUES({','.join('?' for _ in book)})", tuple(book.values()))


def add_entry(path, *, paper_id="PAPER-new", opened=clock(390), score=".7", volatility=True,
              entry_price="100", entry_book=None, features=None, evidence=True,
              committed=None, native=True, fee=None):
    at = datetime.fromisoformat(opened)
    intent = (at+timedelta(microseconds=100)).isoformat()
    decision_at = (at-timedelta(milliseconds=1)).isoformat()
    committed = committed or (at+timedelta(milliseconds=1)).isoformat()
    position_features = {"exit_policy": {"mode": "SIMULATED", "stop_loss_price": str(Decimal(entry_price)*Decimal(".98")),
        "take_profit_price": str(Decimal(entry_price)*Decimal("1.05")), "max_hold_minutes": 360,
        "end_of_day": True}, "contract_cash_multiplier": "1", "contract_quantity_step": "1"}
    if features:
        position_features.update(features)
    position = IDENTITY | {"paper_id": paper_id, "source": "PRODUCTION_PAPER", "strategy_version": "factual-test",
        "status": "OPEN", "quantity": "3", "entry_price": entry_price,
        "entry_cost": fee or str(ledger_leg_cost(entry_price, 3, "ACCIONES")),
        "stop_price": str(Decimal(entry_price)*Decimal(".98")), "target_price": str(Decimal(entry_price)*Decimal("1.05")),
        "opened_at": opened, "features_json": canonical(position_features)}
    book = entry_book or quote((at-timedelta(seconds=1)).isoformat(), "99.9")
    lineage = {"signal_at": (at-timedelta(milliseconds=2)).isoformat(), "decision_at": decision_at,
        "intent_at": intent, "entry_fill_committed_at": committed}
    payload = {"schema": "rc6.decision-inputs.v1", "captured_at": book["observed_at"],
        "decision_key": paper_id+":decision", **lineage, "decision": {"paper_id": paper_id,
        "final_result": "OPENED_SIMULATED", "action": "BUY", "score": score},
        "quote_used": book, "runtime": {**lineage, "clock_mode": "NATIVE" if native else "EVENT_TIME_SIMULATION_UNVERIFIED",
        "strategy_id": "SPOT_MOMENTUM_BASELINE", "configuration_fingerprint": "factual-config"},
        "inputs_used": position_features}
    with sqlite3.connect(path) as connection:
        columns = ",".join(position)
        connection.execute(f"INSERT INTO paper_positions({columns}) VALUES({','.join('?' for _ in position)})", tuple(position.values()))
        if evidence:
            text = canonical(payload)
            connection.execute("INSERT INTO decision_evidence_snapshots VALUES(?,?,?,?,?)", (
                payload["decision_key"], payload["captured_at"], payload["schema"], hashlib.sha256(text.encode()).hexdigest(), text))
    return position, payload


def tick(path, at, previous=None, **kwargs):
    return evaluate_runtime_lab(path, as_of=at, previous=previous, runtime_config=CONFIG, **kwargs)


def warm(path, checkpoint=None):
    if checkpoint is None:
        _, checkpoint = tick(path, clock(0))
    for seconds, price in ((30, "99"), (90, "100"), (150, "99.5"), (210, "100.5"), (270, "99.8"), (330, "100.1")):
        add_quote(path, quote(clock(seconds), price))
        _, checkpoint = tick(path, clock(seconds), checkpoint)
    return checkpoint


def dump(path):
    with sqlite3.connect(path) as connection:
        return digest(list(connection.iterdump()))


def registered(tmp_path):
    path = source(tmp_path)
    checkpoint = warm(path)
    position, payload = add_entry(path)
    report, checkpoint = tick(path, clock(391), checkpoint)
    assert report["new_registrations"] == 1, report
    return path, position, payload, report, checkpoint


def test_starts_at_tail_and_never_reconstructs_existing_positions(tmp_path):
    path = source(tmp_path)
    add_entry(path, opened=clock(-60))
    add_quote(path, quote(clock(-30), "105"))
    before = dump(path)
    report, checkpoint = tick(path, clock(0))
    assert report["status"] == "START_AT_CURRENT_TAIL"
    assert report["existing_open_entries_unverified"] == 1
    assert report["entries"] == [] and not checkpoint["history"]
    assert tick(path, clock(5), checkpoint)[0]["entries"] == []
    assert dump(path) == before


def test_prospective_runtime_preregisters_all_six_variants_with_exact_factual_inputs(tmp_path):
    path, position, payload, report, checkpoint = registered(tmp_path)
    entry = report["entries"][0]
    assert {item["policy"] for item in entry["variants"]} == {
        "FACTUAL_BASELINE", "VOLATILITY_SHADOW", "TIME_TO_EOD_SHADOW", "MAX_HOLD_SHADOW", "TRAILING_SHADOW", "BREAK_EVEN_SHADOW"}
    assert entry["baseline"]["stop_fraction"] == "0.02"
    assert entry["baseline"]["target_fraction"] == "0.05"
    assert entry["baseline"]["max_hold_seconds"] == 360*60
    assert entry["entry"]["score"] == payload["decision"]["score"]
    assert entry["native_clocks"]["intent_at"] == payload["intent_at"]
    assert entry["economics_shadow"]["status"] == "NO_VERIFICADO"
    assert entry["economics_shadow"]["reason"] == "expected_move_unverified"
    assert entry["volatility"]["observations"] == 6
    assert entry["volatility"]["available_at"] < entry["entry"]["decision_at"]
    assert report["real_orders_sent"] == 0 and report["real_routes"] == "NOT_CALLED"
    assert report["source_database_effect"] == "READ_ONLY" and report["provider_requests"] == 0
    assert not report["parameter_promotion"] and report["factual_exit_policy_effect"] == "NONE"
    assert entry["forward_label"]["censored"] and checkpoint["checkpoint_sha256"]


def test_restart_duplicate_native_timestamp_and_same_input_costs_are_deterministic(tmp_path):
    path, position, payload, report, checkpoint = registered(tmp_path)
    add_quote(path, quote(clock(400), "106", size="10"))
    before = dump(path)
    first, state = tick(path, clock(400), checkpoint)
    replayed, replay_state = tick(path, clock(400), json.loads(canonical(checkpoint)))
    assert first == replayed and state == replay_state
    assert dump(path) == before
    record = first["entries"][0]
    assert len({variant["input_sha256"] for variant in record["variants"]}) == 1
    baseline = record["variants"][0]["result"]
    assert baseline["remaining"] == "2" and baseline["reason"] == "TAKE_PROFIT_PAPER"
    fill = baseline["fills"][0]
    expected = expected_round_trip_cost("100", "106", 1, 1, paper_fee_model("ACCIONES"), intraday_eligible=True)
    assert Decimal(fill["costs"]) == expected["explicit_fees"]
    assert Decimal(fill["net"]) == Decimal("6")-expected["explicit_fees"]
    # A later receipt of the same source timestamp cannot create fresh depth.
    add_quote(path, quote(clock(405), "110", book_at=clock(400), size="1000"))
    repeated, state = tick(path, clock(405), state)
    repeated_record = repeated["entries"][0]
    assert repeated_record["path_observations"] == 1
    assert repeated_record["variants"][0]["result"]["remaining"] == "2"
    assert repeated_record["path_sha256"] == record["path_sha256"]
    add_quote(path, quote(clock(410), "104", size="1000"))
    completed, state = tick(path, clock(410), json.loads(canonical(state)))
    baseline = completed["entries"][0]["variants"][0]["result"]
    assert baseline["state"] == "CLOSED" and len(baseline["fills"]) == 2
    assert baseline["reason"] == "TAKE_PROFIT_PAPER"
    assert baseline["factual_entry"]["entry_price"] == position["entry_price"]


def test_future_quotes_and_late_preentry_observations_never_become_features(tmp_path):
    path = source(tmp_path)
    _, checkpoint = tick(path, clock(0))
    add_entry(path)
    # These quotes were never available to the worker before the native intent.
    for seconds, price in ((30, "99"), (90, "100"), (150, "99.5"), (210, "100.5"), (270, "99.8"), (330, "100.1")):
        add_quote(path, quote(clock(seconds), price))
    add_quote(path, quote(clock(391), "106", book_at=clock(900)))
    result, checkpoint = tick(path, clock(391), checkpoint)
    entry = result["entries"][0]
    assert entry["volatility"] is None
    assert entry["unverified_reasons"] == ["PRE_ENTRY_DISTINCT_VOLATILITY_UNAVAILABLE"]
    assert len(entry["variants"]) == 1 and entry["path_observations"] == 0
    assert result["rejections"]["FUTURE_QUOTE"] == 1
    assert all(variant["result"]["fills"] == [] for variant in entry["variants"])
    assert entry["forward_label"]["censored"]


@pytest.mark.parametrize("mutate,reason", [
    ({"native": False}, "ENTRY_NATIVE_CLOCKS_UNAVAILABLE"),
    ({"committed": clock(900)}, "ENTRY_FUTURE_OR_REVERSED_CLOCKS"),
    ({"fee": "0"}, "FACTUAL_ENTRY_COST_AUTHORITY_MISMATCH"),
    ({"entry_price": "101"}, "EXECUTED_ENTRY_COST_ANCHOR_MISMATCH"),
    ({"features": {"exit_policy": {}}}, "FACTUAL_EOD_POLICY_UNAVAILABLE"),
])
def test_missing_or_conflicting_factual_lineage_fails_closed_without_source_changes(tmp_path, mutate, reason):
    path = source(tmp_path)
    checkpoint = warm(path)
    add_entry(path, **mutate)
    before = dump(path)
    result, checkpoint = tick(path, clock(391), checkpoint)
    assert result["entries"] == []
    assert result["rejections"][reason] == 1
    assert dump(path) == before
    assert not checkpoint["active"]


def test_tampered_immutable_evidence_is_not_registered(tmp_path):
    path = source(tmp_path)
    checkpoint = warm(path)
    add_entry(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE decision_evidence_snapshots SET payload_sha256='bad'")
    before = dump(path)
    result, checkpoint = tick(path, clock(391), checkpoint)
    assert result["rejections"]["IMMUTABLE_DECISION_HASH_MISMATCH"] == 1
    assert result["entries"] == [] and dump(path) == before


def test_configuration_and_checkpoint_digest_changes_invalidate_without_backfill(tmp_path):
    path, position, payload, result, checkpoint = registered(tmp_path)
    changed, fresh = evaluate_runtime_lab(path, as_of=clock(400), previous=checkpoint,
        runtime_config=CONFIG | {"participation": "0.2"})
    assert changed["checkpoint_invalidation"] == "CONFIGURATION_CHANGED_CHECKPOINT_INVALIDATED"
    assert changed["status"] == "START_AT_CURRENT_TAIL" and not fresh["active"]
    corrupted = json.loads(canonical(checkpoint))
    corrupted["active"]["PAPER-new"]["entry"]["score"] = ".9"
    result, fresh = tick(path, clock(400), corrupted)
    assert result["checkpoint_invalidation"] == "CHECKPOINT_SCHEMA_OR_DIGEST_INVALID"
    assert result["entries"] == [] and result["provider_requests"] == 0


def test_eod_labels_mfe_mae_net_and_cohorts_require_actual_endpoint(tmp_path):
    path, position, payload, result, checkpoint = registered(tmp_path)
    for seconds, bid in ((400, "103"), (460, "97")):
        add_quote(path, quote(clock(seconds), bid))
        result, checkpoint = tick(path, clock(seconds), checkpoint)
    eod = "2026-10-05T19:50:00+00:00"
    add_quote(path, quote(eod, "101"))
    before = dump(path)
    result, checkpoint = tick(path, eod, checkpoint)
    entry = result["entries"][0]
    assert entry["forward_label"]["status"] == "MEDIDO"
    assert entry["forward_label"]["mfe_observed"] == "0.03"
    assert entry["forward_label"]["mae_observed"] == "-0.03"
    assert not entry["forward_label"]["censored"]
    assert all(cohort["complete_eod_labels"] == 1 for cohort in result["entry_hour_cohorts"])
    assert all(cohort["continuous_hit_probability"] == "NO_VERIFICADO" for cohort in result["entry_hour_cohorts"])
    assert dump(path) == before
    retired, checkpoint = tick(path, "2026-10-05T19:52:01+00:00", checkpoint)
    assert retired["active_entries"] == 0 and len(checkpoint["archive"]) == 1
    assert retired["entries"][0]["entry"]["score"] == ".7"
    assert checkpoint["archive"][0]["forward_label"] == entry["forward_label"]


def test_maxhold_and_eod_detect_without_executable_book_and_never_fill_overnight(tmp_path):
    path, position, payload, result, checkpoint = registered(tmp_path)
    result, checkpoint = tick(path, clock(390+1800), checkpoint)
    by_name = {variant["policy"]: variant["result"] for variant in result["entries"][0]["variants"]}
    assert by_name["MAX_HOLD_SHADOW"]["reason"] == "MAX_HOLD_PAPER"
    assert by_name["MAX_HOLD_SHADOW"]["state"] == "EXIT_PENDING"
    assert by_name["FACTUAL_BASELINE"]["state"] == "OPEN"
    result, checkpoint = tick(path, "2026-10-05T19:50:00+00:00", checkpoint)
    baseline = result["entries"][0]["variants"][0]["result"]
    assert baseline["reason"] == "EOD_PAPER" and baseline["fills"] == []
    add_quote(path, quote("2026-10-06T14:00:00+00:00", "110"))
    result, checkpoint = tick(path, "2026-10-06T14:00:00+00:00", checkpoint)
    assert result["entries"][0]["variants"][0]["result"]["fills"] == []
    assert result["entries"][0]["forward_label"]["censored"]


def test_real_orders_and_reversed_runtime_clock_fail_closed(tmp_path):
    path, position, payload, result, checkpoint = registered(tmp_path)
    with pytest.raises(ValueError, match="TIME_REVERSED"):
        tick(path, clock(330), checkpoint)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE observer_state SET real_orders_sent=1")
    with pytest.raises(ValueError, match="PAPER_SAFETY"):
        tick(path, clock(400), checkpoint)


def test_read_only_runtime_survives_live_wal_writer_and_never_initializes_paperstore(tmp_path, monkeypatch):
    path = source(tmp_path)
    checkpoint = warm(path)
    add_entry(path)
    import be_paper_engine
    monkeypatch.setattr(be_paper_engine.PaperStore, "__init__", lambda *_: pytest.fail("PaperStore initialization is forbidden"))
    before = dump(path)
    writer = sqlite3.connect(path)
    writer.execute("BEGIN IMMEDIATE")
    try:
        result, checkpoint = tick(path, clock(391), checkpoint)
        assert result["new_registrations"] == 1
        assert result["source_query_budget_seconds"] <= .5
    finally:
        writer.rollback()
        writer.close()
    assert dump(path) == before


def test_future_position_timestamp_does_not_register_when_clock_later_catches_up(tmp_path):
    path = source(tmp_path)
    checkpoint = warm(path)
    add_entry(path, opened=clock(900))
    report, checkpoint = tick(path, clock(391), checkpoint)
    assert report["rejections"]["ENTRY_BEFORE_WATERMARK_OR_IN_FUTURE"] == 1
    report, checkpoint = tick(path, clock(901), checkpoint)
    assert report["entries"] == [] and not checkpoint["pending_positions"]


def test_bounded_snapshot_read_censors_labels_and_empirical_touch_denominators(tmp_path):
    path = source(tmp_path)
    _, checkpoint = tick(path, clock(0), row_limit=2)
    for seconds, price in ((30, "99"), (90, "100"), (150, "99.5"), (210, "100.5"), (270, "99.8"), (330, "100.1")):
        add_quote(path, quote(clock(seconds), price))
        _, checkpoint = tick(path, clock(seconds), checkpoint, row_limit=2)
    add_entry(path)
    _, checkpoint = tick(path, clock(391), checkpoint, row_limit=2)
    for seconds in (400, 401, 402):
        add_quote(path, quote(clock(seconds), "106"))
    report, checkpoint = tick(path, clock(402), checkpoint, row_limit=2)
    assert report["source_read_truncated"]
    eod = "2026-10-05T19:50:00+00:00"
    add_quote(path, quote(eod, "101"))
    report, checkpoint = tick(path, eod, checkpoint, row_limit=2)
    entry = report["entries"][0]
    assert entry["forward_label"]["censored"]
    assert entry["forward_label"]["reason"] == "SOURCE_READ_TRUNCATED_PATH_CENSORED"
    assert all(cohort["sampled_target_touch_fraction"] is None for cohort in report["entry_hour_cohorts"])


def test_source_oversize_json_is_not_materialized_into_checkpoint(tmp_path):
    path = source(tmp_path)
    checkpoint = warm(path)
    add_entry(path, features={"huge": "x"*70000})
    report, checkpoint = tick(path, clock(391), checkpoint)
    assert report["entries"] == []
    assert report["rejections"]["SOURCE_ROW_OVERSIZE"] >= 1
    assert "huge" not in canonical(checkpoint)


def test_economics_model_consumes_only_new_completed_prospective_labels(tmp_path):
    """Thirty fresh synthetic sessions exercise the runtime's OOS model path.

    No history import/model bundle is supplied to the worker. The only source
    of its model is labels that this same API collected after preregistration.
    """
    path = source(tmp_path)
    _, checkpoint = tick(path, clock(0))
    for day in range(31):
        base = START+timedelta(days=day)
        for seconds, price in ((30, "99"), (90, "100"), (150, "99.5"), (210, "100.5"), (270, "99.8"), (330, "100.1")):
            at = (base+timedelta(seconds=seconds)).isoformat()
            add_quote(path, quote(at, price))
            _, checkpoint = tick(path, at, checkpoint)
        opened = (base+timedelta(seconds=390)).isoformat()
        add_entry(path, paper_id=f"PAPER-{day}", opened=opened)
        registration = (base+timedelta(seconds=391)).isoformat()
        report, checkpoint = tick(path, registration, checkpoint)
        current = next(entry for entry in report["entries"] if entry["entry"]["paper_id"] == f"PAPER-{day}")
        if day < 30:
            assert current["economics_shadow"]["status"] == "NO_VERIFICADO"
        else:
            economics = current["economics_shadow"]
            assert economics["status"] == "EVALUATED"
            assert economics["model_observations"] == 30
            assert datetime.fromisoformat(economics["training_cutoff"]) < datetime.fromisoformat(current["entry"]["decision_at"])
            assert economics["expected_gross_move"] == "0.06"
            assert not economics["economic_edge_validated"]
            assert current["entry"]["score"] == ".7"
            assert economics["decision_effect"] == "NONE"
            assert economics["real_order_routes"] == []
            break
        target = (base+timedelta(seconds=390+900)).isoformat()
        add_quote(path, quote(target, "106"))
        report, checkpoint = tick(path, target, checkpoint)
        current = next(entry for entry in report["entries"] if entry["entry"]["paper_id"] == f"PAPER-{day}")
        assert current["movement_label"]["status"] == "MEDIDO"
        assert current["movement_label"]["horizon_seconds"] == 900
        retirement = base.replace(hour=19, minute=52, second=1).isoformat()
        _, checkpoint = tick(path, retirement, checkpoint)
