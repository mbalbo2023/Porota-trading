"""A653: OFF/expired PPI authority must never grant speculative wire reads.

All evidence is synthetic and local. No PPI SDK, network or credentials are used.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from be_paper_engine import PaperStore
from cg_paper_workspace import artifact_root
from rc6_dynamic_universe.promotion import DEFAULT_POLICY, RuntimeCapacityController
from rc6_ppi_global_budget import (
    BudgetBackpressure, GlobalPPIBudget, RuntimePPIBudget, SCHEMA,
)


class FakeClock:
    def __init__(self):
        self.value = datetime(2026, 11, 4, 21, 30, tzinfo=timezone.utc)

    def now(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


def _previous_policy(clock):
    return {
        "schema": SCHEMA,
        "version": "FAKE_PREVIOUS_AUTHORITY_TEST_ONLY",
        "recommendation_digest": "a" * 64,
        "configuration_fingerprint": "b" * 64,
        "window_seconds": 30,
        "endpoint_limits": {"current": 3, "book": 3, "intraday": 3},
        "global_limit": 9,
        "max_parallel_requests": 1,
        "safety_reserve": "FAKE_TEST_ONLY_NOT_PROVIDER_CAPACITY",
        "priority_reserves": {"EXIT_CRITICAL": {"book": 1}},
        "expires_at": (clock.now() + timedelta(minutes=30)).isoformat(),
        "critical_book_seconds": 5,
        "lease_seconds": 60,
        "breaker_seconds": 60,
        "session_breaker_seconds": 900,
        "server_error_threshold": 2,
        "maximum_bytes": 8 * 1024**2,
    }


def _seed_prior_budget(tmp_path, clock):
    workspace = PaperStore(str(tmp_path / "paper.sqlite"))
    workspace.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    budget_path = artifact_root(workspace.path) / "ppi-budget/global.sqlite"
    prior = GlobalPPIBudget(budget_path, _previous_policy(clock), clock=clock.now)
    exit_read = prior.acquire("book", consumer="EXIT_READER", priority="EXIT_CRITICAL")
    assert exit_read["allowed"] and exit_read["lease"]
    prior.start(exit_read["lease"])
    prior.finish(exit_read["lease"])
    clock.advance(31)  # first wire receipt leaves the 30-second rolling window
    return workspace.path, budget_path


def _actual_wires(path):
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        return connection.execute("SELECT COUNT(*) FROM budget_requests").fetchone()[0]


@pytest.mark.parametrize("mode", ["OFF", "SHADOW", "APPROVED"])
@pytest.mark.parametrize("endpoint", ["current", "book", "intraday"])
def test_prior_ledger_never_grants_new_lower_reads_without_current_approval(tmp_path, mode, endpoint):
    clock = FakeClock()
    db_path, ledger_path = _seed_prior_budget(tmp_path, clock)
    config = deepcopy(DEFAULT_POLICY)
    config["mode"] = mode  # APPROVED lacks actual report/approval by design.
    controller = RuntimeCapacityController(db_path, environ={}, policy=config)
    assert controller.state(clock.now())["status"] != "APPROVED_DYNAMIC"
    before = _actual_wires(ledger_path)
    runtime = RuntimePPIBudget(db_path, controller, clock=clock.now)
    denied = runtime.acquire(endpoint, consumer="SCANNER", priority="DISCOVERY")
    assert denied == {"allowed": False, "reason": "PPI_CAPACITY_LOWER_UNAPPROVED", "lease": None}
    assert _actual_wires(ledger_path) == before


def test_previous_exit_round_keeps_safe_fallback_while_lower_is_denied(tmp_path):
    clock = FakeClock()
    db_path, _ = _seed_prior_budget(tmp_path, clock)
    controller = RuntimeCapacityController(db_path, environ={}, policy=DEFAULT_POLICY)
    runtime = RuntimePPIBudget(db_path, controller, clock=clock.now)
    denied = runtime.acquire("current", consumer="SCANNER", priority="WARM")
    assert not denied["allowed"]
    exit_read = runtime.acquire("book", consumer="EXIT_READER", priority="EXIT_CRITICAL")
    assert exit_read["allowed"] and exit_read["lease"]
    runtime.start(exit_read["lease"])
    runtime.finish(exit_read["lease"])
    assert runtime.budget.policy["version"] == "rc6-global-ppi-safe-factual-baseline-v1"


def test_coalesced_book_does_not_call_unbudgeted_lower_fetch(tmp_path):
    clock = FakeClock()
    db_path, ledger_path = _seed_prior_budget(tmp_path, clock)
    controller = RuntimeCapacityController(db_path, environ={}, policy=DEFAULT_POLICY)
    runtime = RuntimePPIBudget(db_path, controller, clock=clock.now)
    before = _actual_wires(ledger_path)

    with pytest.raises(BudgetBackpressure, match="PPI_CAPACITY_LOWER_UNAPPROVED"):
        runtime.coalesced_book(
            ("FAKE", "ACCIONES", "BYMA", "ARS", "A-24HS"),
            lambda: pytest.fail("UNBUDGETED_LOWER_BOOK_FETCH_MUST_NOT_RUN"),
            consumer="SCANNER", priority="WARM",
        )
    assert _actual_wires(ledger_path) == before


def test_scanner_cannot_claim_exit_priority_on_unapproved_fallback(tmp_path):
    clock = FakeClock()
    db_path, _ = _seed_prior_budget(tmp_path, clock)
    runtime = RuntimePPIBudget(db_path,
        RuntimeCapacityController(db_path, environ={}, policy=DEFAULT_POLICY), clock=clock.now)
    assert runtime.acquire("book", consumer="SCANNER", priority="EXIT_CRITICAL") == {
        "allowed": False, "reason": "PPI_CAPACITY_LOWER_UNAPPROVED", "lease": None,
    }


def test_explicit_approved_dynamic_still_admits_lower_with_durable_lease(tmp_path):
    """Synthetic approved state proves this guard does not blanket-veto reads."""
    clock = FakeClock()
    db_path, ledger_path = _seed_prior_budget(tmp_path, clock)

    class SyntheticController:
        database = db_path
        environ = {}
        input_paths = []

        def state(self, _as_of):
            return {
                "status": "APPROVED_DYNAMIC", "mode": "APPROVED",
                "recommendation_digest": "c" * 64,
                "configuration_fingerprint": "d" * 64,
                "expires_at": (clock.now() + timedelta(minutes=30)).isoformat(),
                "global_budget": {
                    "window_seconds": 30,
                    "endpoint_limits": {"current": 3, "book": 3, "intraday": 3},
                    "global_limit": 9, "safety_reserve": "SYNTHETIC_TEST_ONLY",
                },
                "budget_settings": deepcopy(DEFAULT_POLICY["budget"]),
                "engine_profiles": {},
            }

        def shadow_report(self):
            return {}

    runtime = RuntimePPIBudget(db_path, SyntheticController(), clock=clock.now)
    before = _actual_wires(ledger_path)
    admitted = runtime.acquire("current", consumer="SCANNER", priority="DISCOVERY")
    assert admitted["allowed"] and admitted["lease"]
    runtime.start(admitted["lease"])
    runtime.finish(admitted["lease"])
    assert _actual_wires(ledger_path) == before + 1


def test_fresh_process_cannot_borrow_expired_approval_to_send(tmp_path):
    clock = FakeClock()
    db_path, ledger_path = _seed_prior_budget(tmp_path, clock)
    clock.advance(31 * 60)  # original approval expires, even if last lease was valid.
    config = deepcopy(DEFAULT_POLICY)
    config["mode"] = "APPROVED"
    controller = RuntimeCapacityController(db_path, environ={}, policy=config)
    before = _actual_wires(ledger_path)
    first = RuntimePPIBudget(db_path, controller, clock=clock.now)
    second = RuntimePPIBudget(db_path, controller, clock=clock.now)
    for reader in (first, second):
        result = reader.acquire("current", consumer="SCANNER", priority="DISCOVERY")
        assert result == {"allowed": False, "reason": "PPI_CAPACITY_LOWER_UNAPPROVED", "lease": None}
    assert _actual_wires(ledger_path) == before
