"""A237: only new synthetic PAPER cauciones use conservative BYMA fee V2.

No PPI session, account, network, broker receipt, real order or host path.
The PPI BID/current/history fixture is fabricated inside a temporary SQLite DB.
"""
from dataclasses import replace
from decimal import Decimal

import pytest

import rc6_paper_admission_gate as admission
from au_fee_schedule import CAUCION_PAPER_FEE_AUTHORITY as HISTORICAL_AUTHORITY
from bt_caucion_paper import CaucionOffer
from di_caucion_cash_sweep_runtime_hf6 import fee_authorized_offers, offers_from_store
from ca_caucion_allocator import _paper_tariff_authorized
from rc6_caucion_fee_model import (
    ACCOUNT_FEES_VERIFIED, PAPER_FEE_AUTHORITY_V2,
    conservative_public_modeled_fee,
)
from tests.test_rc6_a232_fund_caucion_market_entry import _live_ppi_fixture, _runtime


MONDAY = "2026-10-05T16:40:00-03:00"
AMOUNT = Decimal("100000")


def _live(store, at=MONDAY, *, fraction=".50"):
    old = _live_ppi_fixture(store, at)
    if fraction != ".50":
        with store.connect() as db:
            for table in ("paper_caucion_live_book", "paper_caucion_live_book_history"):
                db.execute(f"UPDATE {table} SET tna_fraction=? WHERE ticker='PESOS3'", (fraction,))
    offers, errors = offers_from_store(store, now=at)
    assert not errors and len(offers) == 1
    return offers[0]


def test_new_live_offer_pins_v2_but_catalog_legacy_receipt_remains_unchanged(
        tmp_path, monkeypatch):
    store, _ = _runtime(tmp_path, monkeypatch)
    offer = _live(store)
    assert offer.fee_authority == PAPER_FEE_AUTHORITY_V2
    assert ACCOUNT_FEES_VERIFIED is False
    assert fee_authorized_offers([offer]) == [offer]
    assert _paper_tariff_authorized(offer)
    old_live = replace(offer, fee_authority=HISTORICAL_AUTHORITY)
    assert fee_authorized_offers([old_live]) == []
    assert not _paper_tariff_authorized(old_live)
    with store.connect() as db:
        from json import loads
        policy = loads(db.execute("SELECT metadata_json FROM financial_instrument_catalog "
                                  "WHERE ticker='PESOS3'").fetchone()[0])["paper_caucion_contract_v1"]
        assert policy["fee_authority"] == HISTORICAL_AUTHORITY


def test_marginal_positive_legacy_net_is_a_v2_negative_paper_hold(tmp_path, monkeypatch):
    store, broker = _runtime(tmp_path, monkeypatch)
    offer = _live(store, fraction=".025")
    monkeypatch.setattr(admission, "admission_status", lambda: (True, ""))
    old = replace(offer, fee_authority=HISTORICAL_AUTHORITY)
    old_interest, old_fee, old_net = old.economics(AMOUNT)
    interest, fee, net = offer.economics(AMOUNT)
    assert (old_interest, old_fee, old_net) == (
        Decimal("20.55"), Decimal("20.34"), Decimal("0.21"))
    assert fee == conservative_public_modeled_fee(AMOUNT, interest, 3) == Decimal("23.52")
    assert net == Decimal("-2.97")
    with pytest.raises(ValueError, match="retorno neto positivo"):
        broker.place_caucion(offer, AMOUNT, "A237:NET_NEGATIVE", as_of=MONDAY)
    # Reusing the old fee token is not a route to bypass the new PAPER gate.
    with pytest.raises(ValueError, match="CAUCION_RUNTIME_PPI_LIVE_CONTRACT_REQUIRED"):
        broker.place_caucion(old, AMOUNT, "A237:OLD_TOKEN", as_of=MONDAY)
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_cauciones").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM paper_events").fetchone()[0] == 0
        assert tuple(db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1")
                     .fetchone()) == ("PRODUCTION_PAPER", 0)
    assert broker._cash(MONDAY, "ARS") == Decimal("2000000")


def test_live_monday_positive_fee_v2_and_idempotent_cash(tmp_path, monkeypatch):
    store, broker = _runtime(tmp_path, monkeypatch)
    offer = _live(store)
    monkeypatch.setattr(admission, "admission_status", lambda: (True, ""))
    gross, fees, net = offer.economics(AMOUNT)
    assert gross > fees > 0 and net > 0
    assert fees == conservative_public_modeled_fee(AMOUNT, gross, 3)
    first = broker.place_caucion(offer, AMOUNT, "A237:V2_POSITIVE", as_of=MONDAY)
    again = broker.place_caucion(offer, AMOUNT, "A237:V2_POSITIVE", as_of=MONDAY)
    assert first["paper_id"] == again["paper_id"]
    assert Decimal(first["total_fees"]) == fees
    assert broker._cash(MONDAY, "ARS") == Decimal("1900000")
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_cauciones").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM paper_events").fetchone()[0] == 1


def test_v2_allocator_receipt_replays_without_repricing_historical_token(
        tmp_path, monkeypatch):
    from be_paper_engine import PaperBroker, PaperStore
    from ca_caucion_allocator import CaucionPolicy
    from cb_caucion_audit import allocation_history

    # Standalone simulator: production runtime PPI source binding intentionally
    # never claimed by this end-to-end decision/audit arithmetic fixture.
    monkeypatch.delenv("PAPER_V17_DB_PATH", raising=False)
    path = tmp_path / "a237_v2_allocations.sqlite"
    broker = PaperBroker(PaperStore(str(path)), initial_cash="1000000")
    base = CaucionOffer(
        instrument_id="PESOS3", currency="ARS", annual_rate_fraction=Decimal(".50"),
        start_date="2026-10-05", maturity_at="2026-10-08T17:00:00-03:00",
        quoted_at=MONDAY, available_principal=Decimal("1000000"),
        minimum_principal=Decimal("100000"), principal_step=Decimal("1"),
        day_count_basis=365, fee_payment="MATURITY",
        metadata_source="PPI_PRIMARY_LIVE_BOOK+PAPER_POLICY:v1;SYNTHETIC_ALLOCATION",
        paper_fill_policy="LIVE_PPI_BID_PARTICIPATION_CAP",
        fee_authority=PAPER_FEE_AUTHORITY_V2)
    policy = CaucionPolicy(
        frozen_at="2026-10-05T09:30:00-03:00", currency="ARS",
        reserve_cash=Decimal("100000"), maximum_cash_fraction=Decimal("0.5"),
        maximum_principal=AMOUNT, liquidity_deadline="2026-10-08T18:00:00-03:00",
        maximum_quote_age_seconds=Decimal("60"), participation=Decimal("0.1"),
        minimum_net_profit=Decimal("0"), ranking="NET_PROFIT",
        session_open_at="2026-10-05T10:30:00-03:00",
        session_close_at="2026-10-05T17:00:00-03:00",
        session_source="SYNTHETIC_PAPER_SESSION_NOT_BROKER")
    decision = broker.allocate_caucion([base], policy, "A237:V2_ALLOCATION", as_of=MONDAY)
    assert decision["status"] == "PLACED_SIMULATED"
    assert Decimal(decision["selected"]["fees"]) == base.economics(AMOUNT)[1]
    audit = allocation_history(path)
    assert audit["state"] == "READABLE" and audit["total"] == 1
    assert audit["records"][0]["issue"] is None
    assert audit["records"][0]["decision"] == decision
    assert audit["records"][0]["placement"]["status"] == "OPEN"


def test_v2_rejects_unsupported_term_without_backdoor_legacy_fee(tmp_path, monkeypatch):
    store, _ = _runtime(tmp_path, monkeypatch)
    offer = _live(store)
    extended = replace(offer, maturity_at="2027-01-04T17:00:00-03:00")
    assert extended.interest_days == 91
    with pytest.raises(ValueError, match="CAUCION_PUBLIC_FEE_TERM_UNSUPPORTED"):
        extended.economics(AMOUNT)
    with pytest.raises(ValueError, match="Autoridad de costos PAPER incompatible"):
        replace(offer, paper_fill_policy="CONSERVATIVE_NOTIONAL_CAP")


def test_stricter_configured_historical_paper_cost_cannot_be_bypassed_by_v2(
        tmp_path, monkeypatch):
    from dataclasses import replace as fee_replace
    import au_fee_schedule as tariff

    store, _ = _runtime(tmp_path, monkeypatch)
    offer = _live(store)
    historical = tariff.arancel_de("CAUCIONES")
    monkeypatch.setitem(tariff.ARANCELES, "CAUCIONES",
                        fee_replace(historical, comision=0.0500))
    old_fee = replace(offer, fee_authority=HISTORICAL_AUTHORITY).economics(AMOUNT)[1]
    v2_fee = offer.economics(AMOUNT)[1]
    assert v2_fee == old_fee
    assert v2_fee > conservative_public_modeled_fee(
        AMOUNT, offer.economics(AMOUNT)[0], 3)


@pytest.mark.parametrize("day", ["2026-10-31", "2026-12-25"])
def test_weekend_or_holiday_v2_fee_never_rearms_entry(tmp_path, monkeypatch, day):
    store, broker = _runtime(tmp_path, monkeypatch)
    offer = _live(store)
    local = day + "T16:40:00-03:00"
    synthetic = replace(offer, start_date=day, quoted_at=local,
                        maturity_at="2026-12-28T17:00:00-03:00")
    monkeypatch.setattr(admission, "admission_status", lambda: (True, ""))
    with pytest.raises(ValueError, match="CAUCION_RUNTIME_MARKET_CLOSED_OR_CALENDAR_UNKNOWN"):
        broker.place_caucion(synthetic, AMOUNT, "A237:OFFHOURS:" + day, as_of=local)
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_cauciones").fetchone()[0] == 0
        assert db.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0] == 0
