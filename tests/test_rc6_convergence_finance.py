"""U09/U10/U22/U23: real offline PAPER ledger callers and causal boundaries."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import hashlib
import json
import multiprocessing
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from be_paper_engine import PaperBroker, PaperStore
from bs_instrument_contracts import InstrumentContract, register_exact_time_sql, utc_microseconds
from dh_paper_dynamic_risk_gate_hf6 import portfolio_capacity
from rc6_paper_family_lifecycle import (FamilyPaperExecutor, PaperFundTerms,
    apply_paper_event, future_cash_effect, future_position_contract,
    future_positions, future_risk_snapshot)
from rc6_performance.costs import (FeeTier, ledger_leg_cost, paper_cost_contract)
from rc6_ppi_future_contract_policy import standard_dlr_terms


OPEN = "2026-10-05T12:59:00-03:00"
CUT = "2026-10-05T13:00:00.000000-03:00"


class Store:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


def dlr(symbol="DLR/OCT26"):
    terms = standard_dlr_terms(symbol)
    return InstrumentContract(
        symbol, "FUTUROS", "ARS", "A3", "INMEDIATA", D("1000"), D("1"),
        "PPI_PRIMARY+A3_OFFICIAL:OFFLINE_FIXTURE", expires_at=terms["expires_at"],
        minimum_quantity=D("1"), paper_margin_policy="CONSERVATIVE_NOTIONAL_RATE",
        paper_margin_rate=D("1"), underlying=terms["underlying"])


def opened(tmp_path, **changes):
    store = Store(tmp_path / "future.sqlite")
    executor = FamilyPaperExecutor(store)
    values = dict(lifecycle_id="FUT-1", event_id="OPEN", entry_price="1500",
                  entry_cost="100", quantity="1", occurred_at=OPEN, book_at=OPEN)
    values.update(changes)
    executor.open_future(dlr(), **values)
    return store, executor


@pytest.mark.parametrize("raw,side,expected", [
    ("1579.3158", "BUY", "1579.5"), ("1579.6840", "SELL", "1579.5"),
    ("1579.25", "BUY", "1579.5"), ("1579.25", "SELL", "1579.0"),
    ("1579.5", "BUY", "1579.5"), ("1579.5", "SELL", "1579.5"),
])
def test_u09_adverse_execution_grid_at_fill_boundary(raw, side, expected):
    contract = dlr()
    assert contract.executable_fill(raw, side=side) == D(expected)
    assert contract.execution_price_terms()["historical_effectivity"] == "NO_VERIFICADO"


@pytest.mark.parametrize("kind", ["QUOTE", "TRADE", "FILL", "BOOK_MARK"])
def test_u09_nonexecutable_grid_is_rejected_at_its_typed_boundary(kind):
    with pytest.raises(ValueError, match="OFF_GRID"):
        dlr().price("1500.123456", price_kind=kind)


def test_u09_official_settlement_preserves_precision_and_close_does_not_count_variation_twice(tmp_path):
    store, executor = opened(tmp_path)
    price = "1510.123456"
    rule = {"mode": "PRESERVE_PUBLISHED_DECIMAL", "version": "OFFLINE_RULE_1",
            "quantum": "0.000001", "known_at": OPEN, "effective_at": OPEN}
    result = executor.mark_future(dlr(), lifecycle_id="FUT-1", event_id="SETTLE",
        mark_price=price, book_at=CUT, occurred_at=CUT, settlement=True,
        price_kind="OFFICIAL_SETTLEMENT", price_source="DESIGNATED_SOURCE:OFFLINE_FIXTURE",
        price_rule=rule)
    assert D(result["variation_realized"]) == D("10123.456000")
    cut_row = future_positions(store, as_of=CUT)[0]
    assert D(cut_row["settlement_base_price"]) == D(price)
    assert D(cut_row["unrealized_pnl"]) == 0
    later = "2026-10-05T13:01:00-03:00"
    closed = executor.close_future(dlr(), lifecycle_id="FUT-1", event_id="CLOSE",
        exit_price="1511", exit_cost="100", book_at=later, occurred_at=later)
    assert D(closed["realized_pnl"]) == D("10800")
    snapshot = future_risk_snapshot(store, "ARS", later)
    assert snapshot["cash_effect"] == snapshot["realized"] == D("10800")
    assert snapshot["collateral"] == snapshot["unrealized"] == 0
    assert future_positions(store, as_of=CUT)[0] == cut_row


def test_u09_paper_settlement_is_explicit_assumption_and_official_rule_is_required(tmp_path):
    store, executor = opened(tmp_path)
    with pytest.raises(ValueError, match="OFFICIAL_PRICE_RULE_REQUIRED"):
        executor.mark_future(dlr(), lifecycle_id="FUT-1", event_id="OFFICIAL",
            mark_price="1500.123", book_at=CUT, occurred_at=CUT, settlement=True,
            price_kind="OFFICIAL_SETTLEMENT")
    executor.mark_future(dlr(), lifecycle_id="FUT-1", event_id="PAPER",
        mark_price="1500.123", book_at=CUT, occurred_at=CUT, settlement=True)
    row = future_positions(store, as_of=CUT)[0]
    assert json.loads(row["metadata_json"])["last_mark_price_kind"] == "PAPER_SETTLEMENT"
    assert D(row["daily_variation_cash"]) == D("123.000")


@pytest.mark.parametrize("delta_us", [-500, -499, -100, -1, 0, 1, 100, 499, 500])
@pytest.mark.parametrize("event", ["MARK", "SETTLEMENT", "CLOSE"])
def test_u10_events_and_marks_respect_exact_inclusive_cut_and_restart(tmp_path, delta_us, event):
    store, executor = opened(tmp_path)
    before = future_risk_snapshot(store, "ARS", CUT)
    cash_before = future_cash_effect(store, "ARS", CUT)
    point = (datetime.fromisoformat(CUT) + timedelta(microseconds=delta_us)).astimezone(timezone.utc).isoformat()
    if event == "CLOSE":
        executor.close_future(dlr(), lifecycle_id="FUT-1", event_id="CLOSE",
            exit_price="1520", exit_cost="100", book_at=point, occurred_at=point)
    else:
        executor.mark_future(dlr(), lifecycle_id="FUT-1", event_id=event,
            mark_price="1510", book_at=point, occurred_at=point, settlement=event == "SETTLEMENT")
    restarted = Store(store.path)
    after = future_risk_snapshot(restarted, "ARS", CUT)
    if delta_us > 0:
        assert after == before
        assert future_cash_effect(restarted, "ARS", CUT) == cash_before
    elif event == "CLOSE":
        assert after["active_count"] == 0 and after["collateral"] == 0
        assert after["cash_effect"] == after["realized"] == D("19800")
    else:
        assert after["realized"] + after["unrealized"] == D("9900")
    assert all(stamp is None or utc_microseconds(stamp) <= utc_microseconds(CUT)
               for stamp in after["mark_timestamps"])


def test_u10_exclusive_cash_and_risk_share_boundary_and_exact_offset_sql(tmp_path):
    store, executor = opened(tmp_path)
    executor.mark_future(dlr(), lifecycle_id="FUT-1", event_id="SETTLE",
        mark_price="1510", book_at="2026-10-05T16:00:00+00:00", occurred_at=CUT, settlement=True)
    assert future_cash_effect(store, "ARS", CUT, exclusive=True) == D("-1500100")
    snapshot = future_risk_snapshot(store, "ARS", CUT, exclusive=True)
    assert snapshot["cash_effect"] == D("-1500100") and snapshot["realized"] == D("-100")
    with store.connect() as connection:
        register_exact_time_sql(connection)
        a, b = connection.execute("SELECT rc6_instant_us(?),rc6_instant_us(?)",
                                 (CUT, "2026-10-05T16:00:00.000001Z")).fetchone()
    assert b - a == 1


def test_u10_same_timestamp_marks_have_durable_total_order_and_bounded_page_projection(tmp_path):
    store, executor = opened(tmp_path)
    native = (datetime.fromisoformat(CUT) - timedelta(microseconds=1)).isoformat()
    executor.mark_future(dlr(), lifecycle_id="FUT-1", event_id="Z", mark_price="1501",
                         book_at=native, occurred_at=CUT)
    executor.mark_future(dlr(), lifecycle_id="FUT-1", event_id="A", mark_price="1502",
                         book_at=CUT, occurred_at=CUT)
    row = future_positions(store, as_of=CUT, lifecycle_ids=["FUT-1"])[0]
    assert D(row["last_mark_price"]) == D("1502")
    assert future_positions(Store(store.path), as_of=CUT, lifecycle_ids=["FUT-1"])[0] == row
    assert future_positions(store, as_of=CUT, lifecycle_ids=[]) == []
    assert future_positions(store, as_of=CUT, lifecycle_ids=["UNKNOWN"]) == []


def test_future_nested_decimal_diagnostics_persist_and_exact_retry_survives_expiry(tmp_path, monkeypatch):
    import rc6_paper_family_lifecycle as lifecycle
    monkeypatch.setattr(lifecycle, "_now", lambda: OPEN)
    store, executor = opened(tmp_path, occurred_at=None, book_at=None,
                             detail={"execution_price_terms": {"multiplier": D("1000")},
                                     "score": D("0.8")})
    monkeypatch.setattr(lifecycle, "_now", lambda: "2026-11-01T12:00:00-03:00")
    repeated = executor.open_future(dlr(), lifecycle_id="FUT-1", event_id="OPEN",
        entry_price="1500", entry_cost="100", quantity="1", occurred_at=None, book_at=None,
        detail={"execution_price_terms": {"multiplier": D("1000")}, "score": D("0.8")})
    assert repeated["idempotent"]
    assert future_cash_effect(store, "ARS", CUT) == D("-1500100")


def test_known_corrupt_future_mark_cannot_resurrect_earlier_ready_mark(tmp_path):
    store, executor = opened(tmp_path)
    executor.mark_future(dlr(), lifecycle_id="FUT-1", event_id="MARK", mark_price="1510",
                         book_at=CUT, occurred_at=CUT)
    with store.connect() as connection:
        connection.execute("UPDATE paper_future_marks SET book_at=?",
                           ("2026-10-05T13:00:00.000001-03:00",))
    with pytest.raises(ValueError, match="SOURCE_CLOCK_INVALID"):
        future_risk_snapshot(store, "ARS", CUT)


def test_u10_dst_fold_instants_remain_distinct_at_writer_and_sql_boundaries(tmp_path):
    zone = ZoneInfo("America/New_York")
    first = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
    second = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)
    # Python wall-time comparison on one ZoneInfo ignores fold; UTC instants do not.
    assert utc_microseconds(second) - utc_microseconds(first) == 3600000000
    executor = FamilyPaperExecutor(Store(tmp_path / "fold.sqlite"))
    contract = dlr("DLR/NOV26")
    with pytest.raises(ValueError, match="FUTURES_OPEN_BOOK_STALE_OR_FUTURE"):
        executor.open_future(contract, lifecycle_id="BAD", event_id="BAD",
            entry_price="1500", quantity="1", occurred_at=first, book_at=second)
    executor.open_future(contract, lifecycle_id="FUT-1", event_id="OPEN",
        entry_price="1500", quantity="1", occurred_at=first, book_at=first)
    with pytest.raises(ValueError, match="FUTURES_BOOK_TIME_FUTURE"):
        executor.mark_future(contract, lifecycle_id="FUT-1", event_id="MARK",
            mark_price="1510", occurred_at=first, book_at=second)
    assert future_risk_snapshot(executor.store, "ARS", first)["active_count"] == 1


def test_u22_capacity_at_cut_is_unchanged_by_later_terminal_event(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "OBSERVATION_ONLY")
    monkeypatch.delenv("POROTA_RUNTIME_SCHEMA_READY", raising=False)
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.sqlite")), initial_cash="100000000",
        risk_pct="0.005", max_positions=10, participation="1", max_position_pct="1",
        max_total_exposure_pct="1", clock_fn=lambda: CUT, session_policy=None,
        require_supervisor=False, ai_mode="OFF", economics_mode="SHADOW", daily_loss_pct="5")
    broker.family_paper.open_future(dlr(), lifecycle_id="FUT-1", event_id="OPEN",
        entry_price="1500", entry_cost="100", quantity="1", occurred_at=OPEN, book_at=OPEN)
    before = portfolio_capacity(broker, "ARS", CUT)
    broker.family_paper.close_future(dlr(), lifecycle_id="FUT-1", event_id="CLOSE",
        exit_price="1520", exit_cost="100", book_at="2026-10-05T13:01:00-03:00",
        occurred_at="2026-10-05T13:01:00-03:00")
    after = portfolio_capacity(broker, "ARS", CUT)
    assert before == after and after.open_stop_risk == D("1500100")


def test_legacy_snapshot_digest_is_checked_on_captured_fields_before_new_defaults(tmp_path):
    store, _ = opened(tmp_path)
    with store.connect() as connection:
        row = dict(connection.execute("SELECT * FROM paper_future_positions").fetchone())
        metadata = json.loads(row["metadata_json"])
        snapshot = metadata["financial_contract"]
        for key in ("price_tick", "price_tick_source", "price_tick_known_at", "price_tick_effective_at"):
            snapshot.pop(key)
        metadata.pop("paper_request_v1")
        metadata["contract_snapshot_sha256"] = hashlib.sha256(json.dumps(
            snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        connection.execute("UPDATE paper_future_positions SET metadata_json=?", (json.dumps(metadata),))
        connection.execute("UPDATE paper_family_lifecycle_events SET detail_json=? WHERE to_state='OPEN'",
                           (json.dumps(metadata),))
    row = future_positions(Store(store.path))[0]
    contract = future_position_contract(row)
    assert contract.execution_price_terms()["price_tick"] == D("0.5")
    assert future_risk_snapshot(Store(store.path), "ARS", CUT)["cash_effect"] == D("-1500100")
    mutated = json.loads(row["metadata_json"])
    mutated["financial_contract"]["cash_multiplier"] = "500"
    with pytest.raises(ValueError, match="SNAPSHOT_DIGEST_MISMATCH"):
        future_position_contract(row | {"metadata_json": json.dumps(mutated)})


def request(store, **changes):
    values = dict(lifecycle_id="FCI-1", event_id="REQ-1", family="FCI", instrument="FUND",
                  currency="ARS", to_state="SUBSCRIBE_REQUESTED", amount="-1000",
                  occurred_at=CUT, detail={"purpose": "PAPER_FIXTURE"})
    values.update(changes)
    return apply_paper_event(store, **values)


@pytest.mark.parametrize("changes", [
    {"amount": "-2000"}, {"currency": "USD"}, {"instrument": "OTHER"},
    {"lifecycle_id": "FCI-2"}, {"family": "FUTUROS", "to_state": "OPEN"},
    {"to_state": "PENDING"}, {"occurred_at": "2026-10-05T13:00:00.000001-03:00"},
    {"detail": {"purpose": "CHANGED"}},
])
def test_u23_full_generic_intent_collision_is_rejected_without_mutation(tmp_path, changes):
    store = Store(tmp_path / "fund.sqlite")
    request(store)
    with pytest.raises(ValueError, match="EVENT_ID_COLLISION"):
        request(Store(store.path), **changes)
    with store.connect() as connection:
        assert connection.execute("SELECT ledger_total FROM paper_family_lifecycle").fetchone()[0] == "-1000"
        assert connection.execute("SELECT COUNT(*) FROM paper_family_lifecycle_events").fetchone()[0] == 1


def test_u23_equivalent_decimal_offset_and_implicit_time_retries_are_idempotent(tmp_path):
    store = Store(tmp_path / "fund.sqlite")
    request(store)
    assert request(Store(store.path), amount="-1000.00", occurred_at="2026-10-05T16:00:00Z")["idempotent"]
    implicit = dict(occurred_at=None, event_id="IMPLICIT", lifecycle_id="FCI-IMPLICIT")
    request(store, **implicit)
    assert request(Store(store.path), **implicit)["idempotent"]


def test_u23_generic_future_event_binds_complete_contract_terms(tmp_path):
    executor = FamilyPaperExecutor(Store(tmp_path / "generic-future.sqlite"))
    values = dict(lifecycle_id="FUT-1", event_id="OPEN", to_state="OPEN", occurred_at=OPEN)
    executor.future_event(dlr(), **values)
    with pytest.raises(ValueError, match="EVENT_ID_COLLISION"):
        executor.future_event(replace(dlr(), cash_multiplier=D("500")), **values)


def _race_subscription(path, amount, barrier, results):
    store = Store(path)
    FamilyPaperExecutor(store)
    barrier.wait(10)
    try:
        result = request(store, amount=amount)
        results.put(("OK", result["idempotent"]))
    except ValueError as exc:
        results.put(("BLOCKED", str(exc)))


def test_u23_real_multiprocess_conflicting_intents_serialize_to_one_event(tmp_path):
    context = multiprocessing.get_context("fork")
    store = Store(tmp_path / "race.sqlite")
    FamilyPaperExecutor(store)
    barrier, results = context.Barrier(2), context.Queue()
    workers = [context.Process(target=_race_subscription,
               args=(store.path, amount, barrier, results)) for amount in ("-1000", "-2000")]
    for worker in workers:
        worker.start()
    outputs = [results.get(timeout=15) for _ in workers]
    for worker in workers:
        worker.join(15)
        assert worker.exitcode == 0
    assert sorted(output[0] for output in outputs) == ["BLOCKED", "OK"]
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_family_lifecycle_events").fetchone()[0] == 1
        assert D(connection.execute("SELECT ledger_total FROM paper_family_lifecycle").fetchone()[0]) in {D(-1000), D(-2000)}


@pytest.mark.parametrize("operation,changed", [
    ("OPEN", {"detail": {"changed": True}}),
    ("MARK", {"occurred_at": "2026-10-05T13:00:00.000001-03:00"}),
    ("MARK", {"detail": {"changed": True}}),
    ("CLOSE", {"reason": "CHANGED"}),
    ("CLOSE", {"occurred_at": "2026-10-05T13:00:00.000001-03:00"}),
    ("CLOSE", {"detail": {"changed": True}}),
])
def test_u23_future_retries_bind_time_reason_and_full_detail(tmp_path, operation, changed):
    store, executor = opened(tmp_path)
    if operation == "OPEN":
        values = dict(lifecycle_id="FUT-1", event_id="OPEN", entry_price="1500", quantity="1",
                      entry_cost="100", occurred_at=OPEN, book_at=OPEN)
        caller = executor.open_future
    elif operation == "MARK":
        values = dict(lifecycle_id="FUT-1", event_id="MARK", mark_price="1510", occurred_at=CUT, book_at=CUT)
        caller = executor.mark_future
        caller(dlr(), **values)
    else:
        values = dict(lifecycle_id="FUT-1", event_id="CLOSE", exit_price="1520", exit_cost="100",
                      occurred_at=CUT, book_at=CUT, reason="EOD")
        caller = executor.close_future
        caller(dlr(), **values)
    with pytest.raises(ValueError, match="COLLISION|IDEMPOTENCY_MISMATCH"):
        caller(dlr(), **(values | changed))
    assert caller(dlr(), **values)["idempotent"]


def test_cost_contract_is_versioned_paper_with_separate_unknown_account_terms():
    contract = paper_cost_contract("ACCIONES", "ARS", CUT)
    assert contract.binding_error(CUT) == ""
    diagnostic = contract.diagnostic()
    assert diagnostic["scope"] == "EXPLICIT_PAPER_ASSUMPTIONS"
    assert diagnostic["authority"] == "EXPLICIT_PAPER_ASSUMPTIONS"
    assert diagnostic["account_authority"] == "NO_VERIFICADO"
    assert diagnostic["account_terms"] == "NO_VERIFICADO"
    assert diagnostic["components"]["clearing"] == "0"
    assert diagnostic["price_tick_used_for_fee_rounding"] is False
    assert len(diagnostic["policy_sha256"]) == 64
    assert contract.leg_cost("10000", decision_at=CUT) == ledger_leg_cost("100", "100", "ACCIONES")


@pytest.mark.parametrize("field", ["commission", "clearing", "minimum_commission", "tiers",
                                  "rebate_policy", "fx_rate", "fx_source", "fee_quantum", "rounding"])
def test_cost_binding_closes_for_each_unknown_required_component(field):
    contract = replace(paper_cost_contract("ACCIONES", "ARS", CUT), **{field: None})
    assert contract.binding_error(CUT) == "COST_COMPONENT_UNKNOWN:" + field


def test_cost_contract_known_time_currency_tiers_minima_and_rebate_are_independent():
    contract = paper_cost_contract("ACCIONES", "USD_MEP", CUT)
    assert contract.binding_error("2026-08-20T23:59:59-03:00") == "COST_POLICY_NOT_KNOWN_OR_EFFECTIVE"
    assert replace(contract, fee_currency="ARS").binding_error(CUT) == "COST_CROSS_CURRENCY_MODEL_NOT_VALIDATED"
    tiers = (FeeTier(D(0), D(".006"), D("2")), FeeTier(D("10000"), D(".005"), D("1")))
    contract = replace(contract, tiers=tiers)
    assert contract.leg_components("100", decision_at=CUT)["commission"] == D("2")
    assert contract.leg_components("10000", decision_at=CUT)["commission"] == D("50")
    rebated = contract.leg_components("100", decision_at=CUT, rebated=True)
    assert rebated["commission"] == 0 and rebated["rights"] == D(".05")
