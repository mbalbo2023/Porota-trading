"""A703: synthetic OFFHOST settlement/cash guards, never broker evidence."""

from datetime import datetime
from decimal import Decimal
import sqlite3

import pytest

import cd_spot_ledger as spot
from bs_instrument_contracts import aware_datetime
from bt_caucion_paper import pending_proceeds
from cf_sale_settlement import (
    conservative_unconfirmed_availability,
    modeled_sale_settlement,
    modeled_sale_settlement_date,
    validated_sale_settlement,
)
from rc6_fixed_income_event_ledger import _entitlement
from rc6_fixed_income_event_reducer import EconomicEvent, PositionSeed


TRADED = "2026-11-05T16:45:00-03:00"
NOV12 = "2026-11-12T00:00:00-03:00"
NOV13 = "2026-11-13T00:00:00-03:00"


@pytest.mark.parametrize("traded", [TRADED, "2026-11-06T16:45:00-03:00"])
@pytest.mark.parametrize("family,currency", [
    ("CEDEARS", "ARS"), ("CEDEARS", "USD_MEP"), ("BONOS", "USD_CCL"),
])
def test_exception_waits_for_complete_nov12_settlement(traded, family, currency):
    contract = dict(family=family, market="BYMA", currency=currency)
    assert modeled_sale_settlement_date("T+1", traded, **contract) == "2026-11-12"
    assert conservative_unconfirmed_availability("T+1", traded, **contract) == datetime.fromisoformat(NOV13)


@pytest.mark.parametrize("family,currency", [
    ("ACCIONES", "ARS"), ("BONOS", "ARS"), ("LETRAS", "USD_MEP"),
    ("OBLIGACIONES", "USD_MEP"),
])
def test_nonexception_contract_keeps_local_nov11_settlement(family, currency):
    contract = dict(family=family, market="BYMA", currency=currency)
    assert modeled_sale_settlement_date("A-24HS", TRADED, **contract) == "2026-11-11"
    assert conservative_unconfirmed_availability("A-24HS", TRADED, **contract) == datetime.fromisoformat(NOV12)


@pytest.mark.parametrize("contract", [
    {}, dict(family="CEDEARS"), dict(family="CEDEARS", market="BYMA"),
    dict(family="CEDEARS", market="UNKNOWN", currency="ARS"),
    dict(family="ACCIONES", market="BYMA", currency="USD"),
    dict(family="CEDEARS", market="BYMA", currency="USD"),
    dict(family="UNKNOWN", market="BYMA", currency="ARS"),
    dict(family="CAUCIONES", market="BYMA", currency="ARS"),
])
def test_exception_without_unambiguous_identity_cannot_model_release(contract):
    assert modeled_sale_settlement_date("T+1", TRADED, **contract) is None
    assert conservative_unconfirmed_availability("T+1", TRADED, **contract) is None


def test_explicit_policy_still_requires_identity_and_no_fabricated_receipt(monkeypatch):
    monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE", "true")
    contract = dict(family="CEDEARS", market="BYMA", currency="USD_MEP")
    assert validated_sale_settlement("T+1", TRADED, None, "PENDING_CONFIRMATION", **contract) == datetime.fromisoformat(NOV13)
    assert validated_sale_settlement("T+1", TRADED, None, "PENDING_CONFIRMATION") is None
    with pytest.raises(ValueError, match="acreditación no confirmada"):
        validated_sale_settlement("T+1", TRADED, NOV12, "PENDING_CONFIRMATION", **contract)


def test_regular_dates_and_ci_do_not_gain_a_new_identity_requirement():
    regular = "2026-11-04T16:45:00-03:00"
    assert modeled_sale_settlement_date("T+1", regular) == "2026-11-05"
    assert conservative_unconfirmed_availability("T+1", regular) == datetime.fromisoformat("2026-11-06T00:00:00-03:00")
    assert modeled_sale_settlement("T+1", TRADED) is None
    assert modeled_sale_settlement("CI", TRADED) == TRADED


def _sale_db(*, partial, family, currency):
    """Reconciled persisted lot/fills: no mocks of the ledger under test."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
      CREATE TABLE paper_positions (
        paper_id TEXT, source TEXT, status TEXT, symbol TEXT, asset_class TEXT,
        market TEXT, currency TEXT, settlement TEXT, quantity TEXT,
        entry_price TEXT, entry_cost TEXT, opened_at TEXT, closed_at TEXT,
        exit_price TEXT, exit_cost TEXT, gross_pnl TEXT, net_pnl TEXT,
        close_reason TEXT, features_json TEXT);
      CREATE TABLE paper_fills (
        id INTEGER, paper_id TEXT, source TEXT, side TEXT, quantity TEXT,
        price TEXT, costs TEXT, filled_at TEXT);
      CREATE TABLE paper_spot_sales (
        fill_id INTEGER, paper_id TEXT, entry_cost TEXT, gross_pnl TEXT,
        net_pnl TEXT, net_proceeds TEXT, available_at TEXT, basis TEXT, reason TEXT);
      CREATE TABLE paper_sale_receivables (
        paper_id TEXT, currency TEXT, net_proceeds TEXT, available_at TEXT, basis TEXT);
    """)
    db.execute("INSERT INTO paper_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        "PAPER-SYNTHETIC", "PRODUCTION_PAPER", "CLOSED", "SYNTHETIC", family,
        "BYMA", currency, "A-24HS", "1", "100", "0",
        "2026-11-04T16:00:00-03:00", TRADED, "110", "0", "10", "10", "TEST", "{}"))
    db.executemany("INSERT INTO paper_fills VALUES (?,?,?,?,?,?,?,?)", [
        (1, "PAPER-SYNTHETIC", "PRODUCTION_PAPER", "BUY_SIMULATED", "1", "100", "0", "2026-11-04T16:00:00-03:00"),
        (2, "PAPER-SYNTHETIC", "PRODUCTION_PAPER", "SELL_SIMULATED", "1", "110", "0", TRADED),
    ])
    if partial:
        db.execute("INSERT INTO paper_spot_sales VALUES (?,?,?,?,?,?,?,?,?)", (
            2, "PAPER-SYNTHETIC", "0", "10", "10", "110", None, "PENDING_CONFIRMATION", "TEST"))
    else:
        db.execute("INSERT INTO paper_sale_receivables VALUES (?,?,?,?,?)", (
            "PAPER-SYNTHETIC", currency, "110", None, "PENDING_CONFIRMATION"))
    db.commit()
    db.execute("PRAGMA query_only=ON")
    return db


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("family,currency", [
    ("CEDEARS", "ARS"), ("BONOS", "USD_CCL"),
])
def test_persisted_partial_and_legacy_cash_wait_until_exact_nov13_boundary(monkeypatch, partial, family, currency):
    monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE", "true")
    db = _sale_db(partial=partial, family=family, currency=currency)
    try:
        before = list(db.iterdump())
        assert pending_proceeds(None, NOV12, currency, connection=db) == Decimal("110")
        assert pending_proceeds(None, "2026-11-12T23:59:59.999999-03:00", currency, connection=db) == Decimal("110")
        assert pending_proceeds(None, NOV13, currency, connection=db) == 0
        assert list(db.iterdump()) == before
    finally:
        db.close()


@pytest.mark.parametrize("partial", [False, True])
def test_persisted_local_cash_releases_without_an_extra_day(monkeypatch, partial):
    monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE", "true")
    db = _sale_db(partial=partial, family="ACCIONES", currency="USD_MEP")
    try:
        assert pending_proceeds(None, NOV12, "USD_MEP", connection=db) == 0
    finally:
        db.close()


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("policy", [None, "false", "ambiguous"])
def test_cash_policy_cannot_be_enabled_by_the_calendar_guard(monkeypatch, partial, policy):
    if policy is None:
        monkeypatch.delenv("PAPER_T1_FULL_DATE_RELEASE", raising=False)
    else:
        monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE", policy)
    db = _sale_db(partial=partial, family="CEDEARS", currency="ARS")
    try:
        assert pending_proceeds(None, NOV13, "ARS", connection=db) == Decimal("110")
    finally:
        db.close()


def _fi_entitlement(currency, settled):
    seed = PositionSeed("PAPER-FI", "SYNTHETIC", "BONOS", "BYMA", "A-24HS",
                        currency, "100", "VN", TRADED, "synthetic-buy", "a" * 64)
    event = EconomicEvent("E", "O", "PAPER-FI", "COUPON",
                          "2026-11-16T16:00:00-03:00", "2026-11-16T17:00:00-03:00",
                          "SETTLEMENT_PROOF", "SYNTHETIC", "b" * 64,
                          "2026-11-16T17:00:00-03:00", "ARS", "2", "100")
    receipt = {"cash_per_nominal": "0.02", "entitlement": {
        "rule": "TRADE_BEFORE_EX_AND_SETTLED_BY_RECORD", "source_sha256": "b" * 64,
        "source_reference": "SYNTHETIC", "ex_at": "2026-11-13T10:00:00-03:00",
        "record_at": "2026-11-13T14:00:00-03:00", "paper_settled_at": settled,
        "known_at": "2026-11-16T17:00:00-03:00",
        "settlement_basis": "PAPER_CONSERVATIVE_FULL_SETTLEMENT_DATE"}}
    return _entitlement(seed, event, receipt, aware_datetime("2026-11-16T18:00:00-03:00"), ())


def test_fi_cable_entitlement_rejects_premature_settlement():
    with pytest.raises(ValueError, match="FI_LEDGER_SETTLEMENT_MODEL_UNVERIFIED"):
        _fi_entitlement("USD_CCL", NOV12)


@pytest.mark.parametrize("currency,settled", [("USD_CCL", NOV13), ("ARS", NOV12), ("USD_MEP", NOV12)])
def test_fi_entitlement_uses_trading_currency_not_payment_currency(currency, settled):
    assert _fi_entitlement(currency, settled) is None


def test_fi_ambiguous_usd_cannot_establish_entitlement():
    with pytest.raises(ValueError, match="FI_LEDGER_SETTLEMENT_MODEL_UNVERIFIED"):
        _fi_entitlement("USD", NOV12)
