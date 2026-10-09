"""Native reviewed activation must account for retained EXIT receipt storage."""
from contextlib import closing
import sqlite3

import pytest

from rc6_dynamic_universe.promotion import RuntimeCapacityController
from rc6_ppi_global_budget import BudgetBackpressure, GlobalPPIBudget, RuntimePPIBudget, budget_policy
from tests.test_issue465_budget_adversarial import native_opened_store
from tests.test_rc6_budget_receipt_retention import debt_digest, historic_lower_receipts
from tests.test_rc6_exit_reader_cadence import reviewed_native_capacity
from tests.test_rc6_ppi_capacity_benchmark import wire
from tests.test_rc6_ppi_global_budget import use


def reviewed_controller(wire, tmp_path, monkeypatch):
    clock = wire[0]
    policy, recommendation, report, approval = reviewed_native_capacity(wire)
    store, _ = native_opened_store(tmp_path, clock, monkeypatch, count=5)
    controller = RuntimeCapacityController(store.path, environ={}, policy=policy,
        recommendation=recommendation, report=report, approval=approval)
    return clock, store, controller


def test_native_cold_controller_blocks_retained_full_ledger_before_selection_or_lower_wire(
        wire, tmp_path, monkeypatch):
    clock, store, controller = reviewed_controller(wire, tmp_path, monkeypatch)
    initial = controller.state(clock.now())
    assert initial["status"] == "APPROVED_DYNAMIC"
    runtime = RuntimePPIBudget(store.path, controller, clock=clock.now)
    budget = GlobalPPIBudget(runtime.path, budget_policy(initial, opened_count=5), clock=clock.now)
    historic_lower_receipts(budget, clock)
    before = debt_digest(budget.path), budget.path.read_bytes()
    observed = controller.state(clock.now())
    assert observed["status"] == "ACTIVATION_BLOCKED_EXIT_CAPACITY"
    assert observed["exit_capacity"]["status"] == "READY"
    assert observed["exit_receipt_capacity"]["source_status"] == "OBSERVED"
    assert observed["exit_receipt_capacity"]["retained_receipts"] == 20000
    assert "PPI_EXIT_RECEIPT_RESERVE_BACKPRESSURE" in observed["reason_codes"]
    assert observed["fingerprint"] != initial["fingerprint"]
    selected = controller.selection("SCALPING", ["preserved-baseline"], as_of=clock.now())
    assert not selected["dynamic"] and selected["entry_identities"] == []
    assert selected["selected"] == ["preserved-baseline"]
    cold = RuntimePPIBudget(store.path, controller, clock=clock.now)
    with pytest.raises(BudgetBackpressure, match="RECEIPT_RESERVE"):
        cold.acquire("current", consumer="SCANNER", priority="DISCOVERY")
    assert (debt_digest(budget.path), budget.path.read_bytes()) == before
    with closing(sqlite3.connect(budget.path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM budget_requests").fetchone()[0] == 20000


def test_native_healthy_retention_occupancy_is_telemetry_and_cannot_rekey_approved_configuration(
        wire, tmp_path, monkeypatch):
    clock, store, controller = reviewed_controller(wire, tmp_path, monkeypatch)
    initial = controller.state(clock.now())
    assert initial["status"] == "APPROVED_DYNAMIC"
    assert initial["exit_receipt_capacity"]["source_status"] == "ABSENT"
    runtime = RuntimePPIBudget(store.path, controller, clock=clock.now)
    budget = GlobalPPIBudget(runtime.path, budget_policy(initial, opened_count=5), clock=clock.now)
    empty = controller.state(clock.now())
    assert use(budget, "current", consumer="SCANNER", priority="DISCOVERY")["allowed"]
    used = controller.state(clock.now())
    assert empty["exit_receipt_capacity"]["retained_receipts"] == 0
    assert used["exit_receipt_capacity"]["retained_receipts"] == 1
    assert used["status"] == empty["status"] == initial["status"] == "APPROVED_DYNAMIC"
    assert used["exit_capacity"] == empty["exit_capacity"] == initial["exit_capacity"]
    assert used["fingerprint"] == empty["fingerprint"] == initial["fingerprint"]
    assert controller.validated_report(clock.now())["evidence_digest"] == used["engine_profiles"]["SCALPING"]["capacity"]["evidence_digest"]
