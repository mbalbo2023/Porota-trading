"""A717 R2: shared PPI budget authority defaults to no new market-data wire.

All policies, clocks, ledgers and positions are local synthetic fixtures.
No PPI SDK, credentials, network, production DB or PPI Watch are used.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

import rc6_ppi_global_budget as budget_module
from be_paper_engine import PaperStore
from cg_paper_workspace import artifact_root
from rc6_dynamic_universe.promotion import DEFAULT_POLICY, RuntimeCapacityController
from rc6_ppi_global_budget import (
    BudgetBackpressure,
    GlobalPPIBudget,
    RuntimePPIBudget,
    SCHEMA,
    budget_from_environment,
)


class FakeClock:
    def __init__(self):
        self.value = datetime(2026, 11, 6, 16, 0, tzinfo=timezone.utc)

    def now(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


def _paper(tmp_path):
    store = PaperStore(str(tmp_path / "r2-synthetic-paper.sqlite"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    return store.path


def _previous_policy(clock):
    """Synthetic committed policy, not real PPI quota or Watch authority."""
    return {
        "schema": SCHEMA,
        "version": "A717_SYNTHETIC_PREVIOUS_POLICY_ONLY",
        "recommendation_digest": "a" * 64,
        "configuration_fingerprint": "b" * 64,
        "window_seconds": 30,
        "endpoint_limits": {"current": 3, "book": 3, "intraday": 3},
        "global_limit": 9,
        "max_parallel_requests": 1,
        "safety_reserve": "TEST_ONLY_NOT_PROVIDER_CAPACITY",
        "priority_reserves": {"EXIT_CRITICAL": {"book": 1}},
        "expires_at": (clock.now() + timedelta(minutes=30)).isoformat(),
        "critical_book_seconds": 5,
        "lease_seconds": 60,
        "breaker_seconds": 60,
        "session_breaker_seconds": 900,
        "server_error_threshold": 2,
        "maximum_bytes": 8 * 1024**2,
    }


def _prior_ledger(tmp_path, clock):
    database = _paper(tmp_path)
    path = artifact_root(database) / "ppi-budget/global.sqlite"
    earlier = GlobalPPIBudget(path, _previous_policy(clock), clock=clock.now)
    lease = earlier.acquire("book", consumer="EXIT_READER", priority="EXIT_CRITICAL")
    assert lease["allowed"] and lease["lease"]
    earlier.start(lease["lease"])
    earlier.finish(lease["lease"])
    clock.advance(31)
    return database, path


def _receipts(path):
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        return conn.execute("SELECT COUNT(*) FROM budget_requests").fetchone()[0]


@pytest.mark.parametrize("mode", ["OFF", "SHADOW"])
def test_absent_sidecar_denies_direct_acquisition_and_preserves_absence(tmp_path, mode):
    clock = FakeClock()
    database = _paper(tmp_path)
    policy = deepcopy(DEFAULT_POLICY)
    policy["mode"] = mode
    controller = RuntimeCapacityController(database, environ={}, policy=policy)
    budget = RuntimePPIBudget(database, controller, clock=clock.now)
    assert budget.acquire("current", consumer="SCANNER", priority="DISCOVERY") == {
        "allowed": False, "reason": "PPI_BUDGET_LEASE_UNVERIFIED", "lease": None,
    }
    with pytest.raises(BudgetBackpressure, match="PPI_BUDGET_LEASE_UNVERIFIED"):
        budget.coalesced_book(
            ("FAKE", "ACCIONES", "BYMA", "ARS", "A-24HS"),
            lambda: pytest.fail("NO_UNLEASED_BOOK_FETCH"),
            consumer="SCANNER", priority="DISCOVERY",
        )
    assert not budget.path.exists()


def test_budget_factory_does_not_bootstrap_off_without_authority(tmp_path, monkeypatch):
    from rc6_dynamic_universe import promotion

    clock = FakeClock()
    database = tmp_path / "r2-never-bootstrap.sqlite"
    policy = deepcopy(DEFAULT_POLICY)
    policy["mode"] = "OFF"
    controller = RuntimeCapacityController(database, environ={}, policy=policy)
    monkeypatch.setattr(promotion, "capacity_controller_from_environment", lambda _db: controller)
    assert budget_from_environment(database=database, clock=clock.now) is None
    assert not (artifact_root(database) / "ppi-budget/global.sqlite").exists()


def test_static_authority_without_independent_productive_pins_cannot_activate(tmp_path):
    clock = FakeClock()
    database = _paper(tmp_path)
    assert budget_module.PINNED_STATIC_SHARED_SIGNER_KEY_SHA256 is None
    assert budget_module.PINNED_EXTERNAL_PPI_CONSUMPTION_OWNER_KEY_SHA256 is None
    assert budget_module.STATIC_WATCH_EVIDENCE_SCHEMA == (
        "RC6_PPI_EXTERNAL_WATCH_MAXIMUM_GUARANTEE_V2"
    )
    # Even an explicitly selected authority path is never enough to create
    # a shared budget when no approved independent reviewer key exists.
    environment = {
        budget_module.STATIC_SHARED_AUTHORITY_ENV: str(tmp_path / "absent-authority.json"),
        "POROTA_BUILD_SHA": "c" * 40,
    }
    controller = RuntimeCapacityController(database, environ=environment, policy=deepcopy(DEFAULT_POLICY))
    budget = RuntimePPIBudget(database, controller, clock=clock.now)
    with pytest.raises(BudgetBackpressure, match="PPI_STATIC_SHARED_SIGNER_UNPINNED"):
        budget.acquire("book", consumer="SCANNER", priority="DISCOVERY")
    assert not budget.path.exists()


@pytest.mark.parametrize("mode", ["OFF", "SHADOW", "APPROVED"])
@pytest.mark.parametrize("endpoint", ["current", "book", "intraday"])
def test_retained_previous_ledger_denies_all_new_lower_reads(tmp_path, mode, endpoint):
    clock = FakeClock()
    database, path = _prior_ledger(tmp_path, clock)
    policy = deepcopy(DEFAULT_POLICY)
    policy["mode"] = mode
    controller = RuntimeCapacityController(database, environ={}, policy=policy)
    assert controller.state(clock.now())["status"] != "APPROVED_DYNAMIC"
    before = _receipts(path)
    budget = RuntimePPIBudget(database, controller, clock=clock.now)
    assert budget.acquire(endpoint, consumer="SCANNER", priority="DISCOVERY") == {
        "allowed": False, "reason": "PPI_CAPACITY_LOWER_UNAPPROVED", "lease": None,
    }
    assert _receipts(path) == before


def test_existing_exit_lease_continuity_does_not_grant_scanner_exit_priority(tmp_path):
    clock = FakeClock()
    database, path = _prior_ledger(tmp_path, clock)
    budget = RuntimePPIBudget(database,
        RuntimeCapacityController(database, environ={}, policy=deepcopy(DEFAULT_POLICY)),
        clock=clock.now)
    before = _receipts(path)
    assert budget.acquire("book", consumer="SCANNER", priority="EXIT_CRITICAL") == {
        "allowed": False, "reason": "PPI_CAPACITY_LOWER_UNAPPROVED", "lease": None,
    }
    assert _receipts(path) == before
    # Previous genuine EXIT continuity is not a fresh SCANNER permission.
    exit_lease = budget.acquire("book", consumer="EXIT_READER", priority="EXIT_CRITICAL")
    assert exit_lease["allowed"] and exit_lease["lease"]
    budget.start(exit_lease["lease"])
    budget.finish(exit_lease["lease"])
    assert _receipts(path) == before + 1


def test_old_baseline_coalesced_book_cannot_call_lower_fetch(tmp_path):
    clock = FakeClock()
    database, path = _prior_ledger(tmp_path, clock)
    budget = RuntimePPIBudget(database,
        RuntimeCapacityController(database, environ={}, policy=deepcopy(DEFAULT_POLICY)),
        clock=clock.now)
    before = _receipts(path)
    with pytest.raises(BudgetBackpressure, match="PPI_CAPACITY_LOWER_UNAPPROVED"):
        budget.coalesced_book(
            ("FAKE", "ACCIONES", "BYMA", "ARS", "A-24HS"),
            lambda: pytest.fail("LOWER_BOOK_FALLBACK_MUST_NOT_RUN"),
            consumer="SCANNER", priority="WARM",
        )
    assert _receipts(path) == before
