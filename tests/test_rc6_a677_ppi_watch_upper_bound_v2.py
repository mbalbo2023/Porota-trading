"""A677: WATCH V1 reserve alone never establishes a future request hard maximum.

All authority, signer, account, clock and market data are synthetic.  These
checks run OFFHOST with the A375 in-memory fake wire; zero PPI provider GET.
"""

from copy import deepcopy

import pytest

import rc6_ppi_global_budget as budget_module
from be_paper_engine import PaperStore
from rc6_dynamic_universe.promotion import DEFAULT_POLICY, RuntimeCapacityController
from rc6_ppi_global_budget import (
    BudgetBackpressure, RuntimePPIBudget, static_shared_capacity_state)
from tests.test_rc6_a667_static_quota_evidence_material import _signed_with_material
from tests.test_rc6_a586_ppi_missing_lease_guard import synthetic_budget_policy_ledger
from tests.test_rc6_ppi_capacity_benchmark import wire

pytestmark = pytest.mark.usefixtures("synthetic_budget_policy_ledger")


def test_signed_v1_reserve_without_owner_bound_denies_static_capacity(
    wire, tmp_path, monkeypatch,
):
    clock, provider_calls, _ = wire
    store = PaperStore(str(tmp_path / "a677-fail-first-paper.sqlite"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    environ, _ = _signed_with_material(
        tmp_path, monkeypatch, clock, legacy_watch_v1=True)
    with pytest.raises(BudgetBackpressure, match="PPI_STATIC_SHARED_AUTHORITY_UNVERIFIED"):
        static_shared_capacity_state(store.path, environ, as_of=clock.now())
    assert provider_calls == []


@pytest.mark.parametrize("change", [
    {"watch_current_over_max": 1},
    {"watch_global_over_max": 1},
    {"watch_valid_from_late": 1},
    {"watch_valid_until_short": 1},
    {"watch_window_basis": "UTC_BUCKET_SNAPSHOT_ONLY"},
    {"watch_callers_covered": "WATCH_ONLY_WITHOUT_OTHER_EXTERNAL_CLIENTS"},
    {"watch_hard_cap_kind": "RESERVE_ONLY_WITHOUT_HARD_STOP"},
    {"watch_owner_signature_tamper": True},
    {"watch_owner_is_reviewer": True},
])
def test_signed_external_claim_without_complete_independent_maximum_denies(
    wire, tmp_path, monkeypatch, change,
):
    clock, provider_calls, _ = wire
    store = PaperStore(str(tmp_path / "a677-adversarial-paper.sqlite"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    environ, _ = _signed_with_material(
        tmp_path, monkeypatch, clock, **change)
    with pytest.raises(BudgetBackpressure, match="PPI_STATIC_SHARED_AUTHORITY_UNVERIFIED"):
        static_shared_capacity_state(store.path, environ, as_of=clock.now())
    assert provider_calls == []


def test_unpinned_external_owner_does_not_become_positive_quota_authority(
    wire, tmp_path, monkeypatch,
):
    clock, provider_calls, _ = wire
    store = PaperStore(str(tmp_path / "a677-unpinned-paper.sqlite"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    environ, _ = _signed_with_material(tmp_path, monkeypatch, clock)
    monkeypatch.setattr(
        budget_module, "PINNED_EXTERNAL_PPI_CONSUMPTION_OWNER_KEY_SHA256", None)
    with pytest.raises(BudgetBackpressure, match="PPI_STATIC_SHARED_AUTHORITY_UNVERIFIED"):
        static_shared_capacity_state(store.path, environ, as_of=clock.now())
    assert provider_calls == []


def test_synthetic_independent_maximum_covers_rolling_window_tail_and_future(
    wire, tmp_path, monkeypatch,
):
    clock, provider_calls, _ = wire
    store = PaperStore(str(tmp_path / "a677-rolling-paper.sqlite"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    environ, _ = _signed_with_material(tmp_path, monkeypatch, clock)
    initial = static_shared_capacity_state(store.path, environ, as_of=clock.now())
    assert initial["status"] == "APPROVED_STATIC_SHARED"
    clock.advance(30)
    next_window = static_shared_capacity_state(store.path, environ, as_of=clock.now())
    assert next_window["status"] == "APPROVED_STATIC_SHARED"
    clock.advance(3600)
    with pytest.raises(BudgetBackpressure, match="PPI_STATIC_SHARED_AUTHORITY_UNVERIFIED"):
        static_shared_capacity_state(store.path, environ, as_of=clock.now())
    assert provider_calls == []


def test_revoked_external_owner_pin_blocks_lease_start_before_any_wire_send(
    wire, tmp_path, monkeypatch,
):
    clock, provider_calls, _ = wire
    store = PaperStore(str(tmp_path / "a677-pin-revoke-paper.sqlite"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    environ, _ = _signed_with_material(tmp_path, monkeypatch, clock)
    budget = RuntimePPIBudget(store.path,
        RuntimeCapacityController(
            store.path, environ=environ, policy=deepcopy(DEFAULT_POLICY)),
        clock=clock.now)
    lease = budget.acquire("book", consumer="EXIT_READER", priority="EXIT_CRITICAL")
    assert lease["allowed"] is True and lease["lease"]
    monkeypatch.setattr(
        budget_module, "PINNED_EXTERNAL_PPI_CONSUMPTION_OWNER_KEY_SHA256", None)
    with pytest.raises(BudgetBackpressure, match="PPI_STATIC_SHARED_AUTHORITY_UNVERIFIED"):
        budget.start(lease["lease"])
    assert provider_calls == []
