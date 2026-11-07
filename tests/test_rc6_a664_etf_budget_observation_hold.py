"""A664: an ETF operator HOLD must not spend scarce lower PPI GET leases.

All stores and identities are synthetic. Any HTTP uses only A375 fake wire.
Catalog/history/offline observations remain available; existing PAPER ETF
positions retain OPENED/EXIT supervision even after the operator HOLD.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
import requests

import bf_production_paper_observer as observer
import bu_instrument_catalog as catalog
import cf_intraday_scalping as scalping
from iol_shadow_collector_rc6 import is_verified_late_contract_window
import rc6_dynamic_universe.promotion as promotion
from bd_ppi_readonly_guard import ReadOnlyTransportGuard
from be_paper_engine import PaperStore
from rc6_dynamic_universe.common import identity
from rc6_dynamic_universe.orchestrator import UniverseOrchestrator
from rc6_dynamic_universe.promotion import DEFAULT_POLICY, RuntimeCapacityController
from rc6_ppi_global_budget import BudgetBackpressure, RuntimePPIBudget
from tests.test_rc6_a586_ppi_missing_lease_guard import static_authority_file
from tests.test_rc6_dynamic_orchestrator import OPEN, capacity, frozen
from tests.test_rc6_dynamic_tradeability import instrument
from tests.test_rc6_ppi_capacity_benchmark import wire


EQ = ("A664_EQUITY", "ACCIONES", "BYMA", "ARS", "A-24HS")
CDR = ("A664_CEDEAR", "CEDEARS", "BYMA", "ARS", "A-24HS")
ETF = ("A664_ETF", "ETFS", "BYMA", "ARS", "A-24HS")
BOOK_URL = "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Book"


def _store(tmp_path):
    store = PaperStore(str(tmp_path / "a664_synthetic.sqlite"))
    observer._support_schema(store)
    catalog.init_schema(store)
    scalping.init_schema(store)
    with store.connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS candidate_identity_v2(
            ticker TEXT, instrument_type TEXT, market TEXT, currency TEXT,
            settlement TEXT, can_simulate INTEGER, status TEXT, detail TEXT,
            checked_at TEXT)""")
        for key in (EQ, CDR, ETF):
            c.execute("""INSERT INTO financial_instrument_catalog
              (ticker,instrument_type,market,currency,settlement,settlement_source,
               description,last_seen_at,run_id,status,capability,metadata_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              (*key, "PPI_FIELD", "synthetic-a664", OPEN.isoformat(),
               "A664_NO_PROVIDER", "AVAILABLE", "READY_PAPER_SPOT", "{}"))
            c.execute("""INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?,?,?)""",
                      (*key, 1, "AVAILABLE", "SYNTHETIC", OPEN.isoformat()))
    return store


def _insert_open_etf(store):
    with store.connect() as c:
        c.execute("""INSERT INTO paper_positions
          (paper_id,source,strategy_version,symbol,asset_class,settlement,status,
           quantity,entry_price,entry_cost,stop_price,target_price,opened_at,
           features_json,currency,market,currency_source)
          VALUES(?,?,?,?,?,?,'OPEN',?,?,?,?,?,?,?,?,?,?)""",
          ("A664_OPEN_ETF", "SYNTHETIC_TEST", "A664", ETF[0], ETF[1], ETF[4],
           "1", "100", "0", "90", "110", OPEN.isoformat(), "{}", ETF[3], ETF[2], "SYNTHETIC"))


def _assets():
    return [{**instrument(key[0], family=key[1]),
             "status": "AVAILABLE", "capability": "READY_PAPER_SPOT"}
            for key in (EQ, CDR, ETF)]


def test_dynamic_planner_keeps_etf_metadata_but_never_queues_lower_ppi_current_book():
    assets = _assets()
    plan = UniverseOrchestrator(assets).plan(at=OPEN, session_open=OPEN,
        frozen=frozen(assets), capacity=capacity(OPEN, limit=3))
    assert plan["catalog_ready_count"] == 3
    assert plan["strategy_eligible_count"] == 2
    assert plan["discovery_count"] <= 2
    assert ETF not in list(map(tuple, plan["selected"]))
    assert EQ in list(map(tuple, plan["selected"]))
    assert CDR in [tuple(row["identity"]) for row in plan["telemetry"]]
    held = next(row for row in plan["telemetry"] if tuple(row["identity"]) == ETF)
    assert "ETF_OPERATOR_HOLD" in held["rejection_reason"]
    assert not held["pipeline"]["STRATEGY_ELIGIBLE"]
    assert held["entry_authority"] is False
    # An existing inventory position still gets priority even on capacity loss.
    opened = UniverseOrchestrator(assets).plan(at=OPEN, session_open=OPEN,
        frozen=frozen(assets), capacity={"status": "NO_VERIFICADO", "safe_limit": 0}, opened=[ETF])
    assert ETF in list(map(tuple, opened["selected"]))
    assert opened["opened_priority"] == [ETF]


def test_dynamic_promotion_event_cannot_override_etf_hold():
    assets = _assets()
    observation = {"identity": ETF, "source_at": OPEN.isoformat(),
        "received_at": OPEN.isoformat(), "source": "PPI_MARKETDATA_CURRENT",
        "endpoint": "current", "useful": True}
    event = {"identity": ETF, "at": OPEN.isoformat(),
        "promotion_candidate": True, "reason_codes": ["SYNTHETIC_SIGNAL"]}
    plan = UniverseOrchestrator(assets).plan(at=OPEN, session_open=OPEN,
        frozen=frozen(assets), capacity=capacity(OPEN, limit=3),
        observations=[observation], events=[event])
    held = next(row for row in plan["telemetry"] if tuple(row["identity"]) == ETF)
    assert held["state"] == "DISCOVERY"
    assert ETF not in list(map(tuple, plan["selected"]))


def test_observer_baseline_and_opened_exit_preserve_catalog_without_etf_discovery(tmp_path):
    store = _store(tmp_path)
    all_catalog = observer._eligible_symbols(store)
    assert {key[1] for key in all_catalog} == {"ACCIONES", "CEDEARS", "ETFS"}
    selected, total, _, _, plan = observer._cycle_plan(store)
    assert total == 3
    assert selected and all(item[1] != "ETFS" for item in selected)
    # Metadata is still in its original table, with no source rewrite.
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM financial_instrument_catalog").fetchone()[0] == 3
    _insert_open_etf(store)
    opened_selected, _, _, _, _ = observer._cycle_plan(store)
    assert (ETF[0], ETF[1], ETF[4]) in opened_selected


def test_com19031_late_source_never_admits_etf_get_even_with_opened_position(tmp_path):
    # The superseding COM19031 path is IOL source-only and independently
    # contract-gated. Opening/EXIT priority is handled by the observer above.
    store = _store(tmp_path)
    _insert_open_etf(store)
    opened_selected, _, _, _, _ = observer._cycle_plan(store)
    assert (ETF[0], ETF[1], ETF[4]) in opened_selected
    late_time = datetime(2026, 11, 5, 20, 30, tzinfo=timezone.utc)  # 17:30 ART
    for held in (ETF,):
        assert not is_verified_late_contract_window(
            late_time, family=held[1], market=held[2], settlement=held[4],
            settlement_source="PPI_FIELD")
    for allowed in (EQ, CDR):
        assert is_verified_late_contract_window(
            late_time, family=allowed[1], market=allowed[2], settlement=allowed[4],
            settlement_source="PPI_FIELD")


def test_observer_approved_dynamic_selector_rejects_held_etf_unless_open(tmp_path):
    store = _store(tmp_path)
    selected = {"selected": [ETF, EQ, CDR], "opened_priority": [],
        "entry_identities": [ETF, EQ, CDR],
        "rows": {key: {"state": "HOT"} for key in (ETF, EQ, CDR)},
        "policy_state": {"fingerprint": "SYNTHETIC_APPROVAL"},
        "cadence_seconds": 30, "limit": 3}
    symbols, plan = observer._approved_equity_selection(store, selected, {})
    assert set(symbols) == {(EQ[0], EQ[1], EQ[4]), (CDR[0], CDR[1], CDR[4])}
    assert all(scope["identity"] != ETF for scope in plan["dynamic_scopes"])
    _insert_open_etf(store)
    selected["opened_priority"] = [ETF]
    symbols, plan = observer._approved_equity_selection(store, selected, {})
    assert (ETF[0], ETF[1], ETF[4]) in symbols
    assert any(scope["identity"] == ETF and scope["priority"] == "OPENED_CRITICAL"
               for scope in plan["dynamic_scopes"])


def test_scalping_baseline_and_approved_shadow_never_consume_etf_intraday(tmp_path):
    store = _store(tmp_path)
    rows, _, count = scalping.select_batch(store, limit=8)
    assert count == 2 and {row["instrument_type"] for row in rows} == {"ACCIONES", "CEDEARS"}

    class SyntheticApproved:
        def state(self, _at):
            return {"status": "APPROVED_DYNAMIC"}

        def selection(self, _engine, _baseline, **_kwargs):
            # Even a previously approved shadow selector may retain an ETF.
            return {"dynamic": True, "selected": [ETF, EQ, CDR],
                "opened_priority": [], "rows": {key: {"state": "HOT"} for key in (ETF, EQ, CDR)},
                "entry_identities": [ETF, EQ, CDR], "limit": 3, "cadence_seconds": 30}

    selected, _, _, result = scalping.select_runtime_batch(
        store, limit=8, at=OPEN, controller=SyntheticApproved())
    assert result["dynamic"]
    assert {row["instrument_type"] for row in selected} == {"ACCIONES", "CEDEARS"}
    _insert_open_etf(store)
    baseline, _, _ = scalping.select_batch(store, limit=8)
    assert ETF in [tuple(row[field] for field in
        ("ticker", "instrument_type", "market", "currency", "settlement")) for row in baseline]
    dynamic, _, _, _ = scalping.select_runtime_batch(
        store, limit=8, at=OPEN, controller=SyntheticApproved())
    assert ETF in [tuple(row[field] for field in
        ("ticker", "instrument_type", "market", "currency", "settlement")) for row in dynamic]


def test_signed_static_budget_spends_only_nonheld_quote_leases(wire, tmp_path, monkeypatch):
    clock, requests_seen, _ = wire  # A375 fake HTTP adapter; no real provider.
    store = _store(tmp_path)
    environ, _ = static_authority_file(tmp_path, monkeypatch, clock)
    controller = RuntimeCapacityController(store.path, environ=environ,
        policy=deepcopy(DEFAULT_POLICY))
    runtime = RuntimePPIBudget(store.path, controller, clock=clock.now)
    assert controller.state(clock.now())["status"] == "OFF_BASELINE"
    assert runtime.budget is None
    selected = observer._cycle_plan(store)[0]
    assert selected and all(family != "ETFS" for _, family, _ in selected)
    guard = ReadOnlyTransportGuard(budget=runtime, consumer="SCANNER").install()
    try:
        for _identity in selected:
            with guard.read_scope(priority="DISCOVERY"):
                assert requests.get(BOOK_URL).status_code == 200
        assert requests_seen and len(requests_seen) == len(selected)
        assert runtime.budget.policy["authority"] == "STATIC_SHARED_EXTERNAL_WATCH_RESERVED_V1"
        assert runtime.budget.metrics()["by_endpoint"]["book"]["used"] == len(selected)
    finally:
        guard.restore()


def test_absent_sidecar_never_grants_live_source_even_non_etf(wire, tmp_path):
    _, requests_seen, _ = wire
    store = _store(tmp_path)
    selected = observer._cycle_plan(store)[0]
    assert selected and all(family != "ETFS" for _, family, _ in selected)
    guard = ReadOnlyTransportGuard(budget=None, consumer="SCANNER").install()
    try:
        with guard.read_scope(priority="DISCOVERY"):
            with pytest.raises(BudgetBackpressure, match="PPI_SHARED_BUDGET_UNAVAILABLE"):
                requests.get(BOOK_URL)
        assert not requests_seen
    finally:
        guard.restore()
