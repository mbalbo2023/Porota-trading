"""A586: an expected global budget cannot silently become an unleased PPI sender.

All HTTP in these tests runs exclusively through the in-memory A375 fake wire:
no broker credentials, provider network, host filesystem or production sidecar.
"""

from copy import deepcopy

import pytest
import requests

from bd_ppi_readonly_guard import ReadOnlyTransportGuard
from rc6_dynamic_universe.promotion import DEFAULT_POLICY, RuntimeCapacityController
from rc6_ppi_global_budget import (
    BudgetBackpressure, RuntimePPIBudget, budget_from_environment,
)
from tests.test_rc6_capacity_promotion import approved, controller
from tests.test_rc6_ppi_capacity_benchmark import wire


BOOK_URL = "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Book"


@pytest.mark.parametrize("priority", ["DISCOVERY", "EXIT_CRITICAL"])
def test_approved_intent_without_authority_denies_before_any_provider_send(
    wire, tmp_path, priority,
):
    clock, provider_calls, _ = wire
    policy = deepcopy(DEFAULT_POLICY)
    policy["mode"] = "APPROVED"
    authority = RuntimeCapacityController(environ={}, policy=policy)
    state = authority.state(clock.now())
    assert state["mode"] == "APPROVED"
    assert state["status"] != "APPROVED_DYNAMIC"

    runtime = RuntimePPIBudget(tmp_path / "paper.sqlite", authority, clock=clock.now)
    sidecar = runtime.path
    assert not sidecar.exists()
    guard = ReadOnlyTransportGuard(budget=runtime, consumer="SCANNER").install()
    try:
        with guard.read_scope(priority=priority):
            with pytest.raises(BudgetBackpressure, match="PPI_BUDGET_LEASE_UNVERIFIED"):
                requests.get(BOOK_URL)
        assert guard.calls_allowed == 0
        assert not provider_calls
        assert runtime.budget is None
        assert not sidecar.exists()
    finally:
        guard.restore()


@pytest.mark.parametrize("mode", ["OFF", "SHADOW"])
def test_original_unbudgeted_baseline_is_not_changed_when_sidecar_absent(
    wire, tmp_path, monkeypatch, mode,
):
    clock, provider_calls, _ = wire
    policy = deepcopy(DEFAULT_POLICY)
    policy["mode"] = mode
    authority = RuntimeCapacityController(environ={}, policy=policy)
    monkeypatch.setattr(
        "rc6_dynamic_universe.promotion.capacity_controller_from_environment",
        lambda _database: authority,
    )
    database = tmp_path / "paper.sqlite"
    assert budget_from_environment(database=database, clock=clock.now) is None

    guard = ReadOnlyTransportGuard(budget=None, consumer="SCANNER").install()
    try:
        result = requests.get(BOOK_URL)
        assert result.status_code == 200
        assert guard.calls_allowed == 1
        assert len(provider_calls) == 1  # fake adapter only; existing baseline GET
        assert not (database.parent / "artifacts" / database.name /
                    "ppi-budget" / "global.sqlite").exists()
    finally:
        guard.restore()


def test_genuine_approved_global_budget_keeps_durable_book_lease(wire, tmp_path, monkeypatch):
    from be_paper_engine import PaperStore

    clock, provider_calls, _ = wire
    validated = approved(wire)  # capacity evidence from in-memory fake SDK
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    runtime = RuntimePPIBudget(store.path, controller(validated), clock=clock.now)
    assert not runtime.path.exists()

    admitted = []
    original_acquire = runtime.acquire

    def capture_admission(*args, **kwargs):
        result = original_acquire(*args, **kwargs)
        admitted.append(result)
        return result

    monkeypatch.setattr(runtime, "acquire", capture_admission)
    provider_calls.clear()  # excludes capacity fixture's fake SDK measurements
    guard = ReadOnlyTransportGuard(budget=runtime, consumer="EXIT_READER").install()
    try:
        with guard.read_scope(priority="EXIT_CRITICAL"):
            result = requests.get(BOOK_URL)
        assert result.status_code == 200
        assert len(provider_calls) == 1
        assert len(admitted) == 1
        assert admitted[0]["allowed"] is True
        assert isinstance(admitted[0]["lease"], str) and admitted[0]["lease"]
        assert runtime.path.is_file()
        assert runtime.path.stat().st_mode & 0o777 == 0o600
        assert runtime.budget.metrics()["by_endpoint"]["book"]["used"] == 1
    finally:
        guard.restore()
