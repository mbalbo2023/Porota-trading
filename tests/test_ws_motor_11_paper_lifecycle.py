import sqlite3

import pytest

import rc6_paper_family_lifecycle as lifecycle
from bs_instrument_contracts import contract_from_metadata


class Store:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection


def event(store, family, state, number, amount="0"):
    return lifecycle.apply_paper_event(
        store, lifecycle_id=f"{family}-1", event_id=f"{family}-{number}",
        family=family, instrument="TEST", currency="ARS", to_state=state,
        amount=amount, occurred_at=f"2026-09-28T18:{number:02d}:00+00:00")


def test_fci_paper_lifecycle_is_idempotent(tmp_path):
    store = Store(tmp_path / "fci.db")
    states = ["SUBSCRIBE", "PENDING", "NAV_APPLIED", "SETTLED",
              "REDEEM_REQUESTED", "REDEEMED"]
    for number, state in enumerate(states):
        result = event(store, "FCI", state, number, "10" if state == "NAV_APPLIED" else "0")
        assert result["paper_only"] is True and result["real_routes_used"] == []
    duplicate = event(store, "FCI", "NAV_APPLIED", 2, "10")
    assert duplicate == {"idempotent": True, "state": "NAV_APPLIED",
                         "real_routes_used": [], "paper_only": True}
    assert lifecycle.lifecycle_state(store, "FCI-1")["ledger_total"] == "10"


def test_futures_paper_margin_and_variation_cycle(tmp_path):
    store = Store(tmp_path / "futures.db")
    for number, state, amount in [
        (0, "OPEN", "0"), (1, "MARGIN_RESERVED", "-50000"),
        (2, "DAILY_VARIATION", "1500"), (3, "MARGIN_OK", "0"),
        (4, "DAILY_VARIATION", "-2500"), (5, "MARGIN_DEFICIT", "0"),
        (6, "CLOSE", "51000"),
    ]:
        event(store, "FUTUROS", state, number, amount)
    assert lifecycle.lifecycle_state(store, "FUTUROS-1")["state"] == "CLOSE"


def test_invalid_transition_and_identity_fail_closed(tmp_path):
    store = Store(tmp_path / "blocked.db")
    with pytest.raises(ValueError, match="INVALID_TRANSITION"):
        event(store, "FCI", "SETTLED", 0)
    event(store, "FUTUROS", "OPEN", 1)
    with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
        lifecycle.apply_paper_event(
            store, lifecycle_id="FUTUROS-1", event_id="different-identity",
            family="FUTUROS", instrument="OTHER", currency="ARS",
            to_state="MARGIN_RESERVED")


def test_module_has_no_real_route_surface():
    assert lifecycle.PAPER_ONLY is True
    assert lifecycle.REAL_ROUTES_USED == ()
    assert not any(name.startswith(("place_", "send_", "confirm_", "accept_"))
                   for name in vars(lifecycle))


def test_connected_fund_executor_enforces_ars_thousand_minimum(tmp_path):
    store = Store(tmp_path / "connected-fci.db")
    terms = lifecycle.fund_terms_from_metadata("ADCAP.AP.A", {
        "currency": "ARS", "market": "FCI", "settlement": "INMEDIATA",
        "subscription_min": "1000", "subscription_step": "1",
        "metadata_source": "PPI_OFFICIAL_DOCUMENTATION",
    })
    executor = lifecycle.FamilyPaperExecutor(store)
    with pytest.raises(ValueError, match="BELOW_MINIMUM"):
        executor.subscribe_fund(terms, lifecycle_id="fci-1", event_id="sub-1", amount="1")
    result = executor.subscribe_fund(
        terms, lifecycle_id="fci-1", event_id="sub-1000", amount="1000",
        occurred_at="2026-09-28T18:00:00+00:00")
    assert result["state"] == "SUBSCRIBE" and result["real_routes_used"] == []


def test_connected_futures_executor_requires_real_future_contract(tmp_path):
    store = Store(tmp_path / "connected-future.db")
    contract = contract_from_metadata("DLR/DIC26", "FUTUROS", {
        "currency": "ARS", "market": "ROFEX", "settlement": "INMEDIATA",
        "cash_multiplier": "1000", "quantity_step": "1", "minimum_quantity": "1",
        "expires_at": "2026-12-31T15:00:00-03:00",
        "initial_margin": "100000", "maintenance_margin": "80000",
        "underlying": "USDARS", "metadata_source": "fixture",
    })
    executor = lifecycle.FamilyPaperExecutor(store)
    opened = executor.future_event(
        contract, lifecycle_id="future-1", event_id="open-1", to_state="OPEN")
    reserved = executor.future_event(
        contract, lifecycle_id="future-1", event_id="margin-1",
        to_state="MARGIN_RESERVED", amount="-100000")
    assert opened["paper_only"] is True and reserved["ledger_total"] == "-100000"
