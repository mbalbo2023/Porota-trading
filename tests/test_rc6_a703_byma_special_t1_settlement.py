"""A703 OFFHOST: 06–11 Nov BYMA special T+1 dates never release cash early."""

from datetime import datetime
import sqlite3

import pytest

from cf_sale_settlement import (
    conservative_unconfirmed_availability,
    modeled_sale_settlement_date,
    validated_sale_settlement,
)


@pytest.mark.parametrize("traded", [
    "2026-11-05T16:45:00-03:00",
    "2026-11-06T16:45:00-03:00",
])
def test_cedear_t1_settlement_and_cash_release_are_nov12_and_nov13(traded):
    contract = dict(family="CEDEARS", market="BYMA", currency="ARS")
    assert modeled_sale_settlement_date("T+1", traded, **contract) == "2026-11-12"
    assert conservative_unconfirmed_availability("T+1", traded, **contract) == datetime.fromisoformat(
        "2026-11-13T00:00:00-03:00")


@pytest.mark.parametrize("traded", [
    "2026-11-05T16:45:00-03:00",
    "2026-11-06T16:45:00-03:00",
])
def test_usd_cable_t1_settlement_also_skips_us_holiday(traded):
    contract = dict(family="BONOS", market="BYMA", currency="USD_CCL")
    assert modeled_sale_settlement_date("A-24HS", traded, **contract) == "2026-11-12"


@pytest.mark.parametrize("family,currency", [
    ("ACCIONES", "ARS"),
    ("BONOS", "ARS"),
    ("OBLIGACIONES", "USD_MEP"),
])
def test_known_nonexception_t1_may_settle_nov11(family, currency):
    contract = dict(family=family, market="BYMA", currency=currency)
    traded = "2026-11-05T16:45:00-03:00"
    assert modeled_sale_settlement_date("T+1", traded, **contract) == "2026-11-11"
    assert conservative_unconfirmed_availability("T+1", traded, **contract) == datetime.fromisoformat(
        "2026-11-12T00:00:00-03:00")


@pytest.mark.parametrize("contract", [
    {},
    dict(family="CEDEARS"),
    dict(family="CEDEARS", market="BYMA"),
    dict(family="CEDEARS", market="UNKNOWN", currency="ARS"),
    dict(family="ACCIONES", market="BYMA", currency="USD"),
    dict(family="UNKNOWN", market="BYMA", currency="ARS"),
    dict(family="CAUCIONES", market="BYMA", currency="ARS"),
])
def test_unknown_contract_is_hold_during_special_t1_period(contract):
    traded = "2026-11-05T16:45:00-03:00"
    assert modeled_sale_settlement_date("T+1", traded, **contract) is None
    assert conservative_unconfirmed_availability("T+1", traded, **contract) is None


def test_explicit_policy_requires_exact_contract_and_releases_after_full_nov12(monkeypatch):
    monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE", "true")
    traded = "2026-11-05T16:45:00-03:00"
    contract = dict(family="CEDEARS", market="BYMA", currency="USD_MEP")
    expected = datetime.fromisoformat("2026-11-13T00:00:00-03:00")
    assert validated_sale_settlement("T+1", traded, None, "PENDING_CONFIRMATION", **contract) == expected
    assert validated_sale_settlement("T+1", traded, None, "PENDING_CONFIRMATION") is None


def test_normal_nonexception_date_preserves_prior_generic_behavior():
    traded = "2026-11-04T16:45:00-03:00"
    assert modeled_sale_settlement_date("T+1", traded) == "2026-11-05"
    assert conservative_unconfirmed_availability("T+1", traded) == datetime.fromisoformat(
        "2026-11-06T00:00:00-03:00")


def test_nov11_new_cedear_t1_trade_settles_thursday_and_releases_friday():
    traded = "2026-11-11T16:45:00-03:00"
    contract = dict(family="CEDEARS", market="BYMA", currency="ARS")
    assert modeled_sale_settlement_date("T+1", traded, **contract) == "2026-11-12"
    assert conservative_unconfirmed_availability("T+1", traded, **contract) == datetime.fromisoformat(
        "2026-11-13T00:00:00-03:00")


def test_spot_pending_cash_keeps_cedear_receivable_through_nov12(monkeypatch):
    import cd_spot_ledger as spot

    monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE", "true")
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE paper_positions (
        paper_id TEXT, asset_class TEXT, market TEXT, currency TEXT,
        settlement TEXT, currency_source TEXT)""")
    db.executemany("INSERT INTO paper_positions VALUES(?,?,?,?,?,?)", [
        ("A", "ACCIONES", "BYMA", "ARS", "A-24HS",
         "PPI_CATALOG:2026-11-05T19:00:00+00:00"),
        ("C", "CEDEARS", "BYMA", "ARS", "A-24HS",
         "PPI_CATALOG:2026-11-05T19:00:00+00:00"),
    ])
    monkeypatch.setattr(spot, "partition", lambda *_: None)
    monkeypatch.setattr(spot, "sales", lambda _db, _pid: [
        dict(filled_at="2026-11-05T16:45:00-03:00", net_proceeds="100",
             available_at=None, basis="PENDING_CONFIRMATION")])
    assert spot.pending(db, "2026-11-12T00:00:00-03:00", "ARS") == 100
    assert spot.pending(db, "2026-11-13T00:00:00-03:00", "ARS") == 0




@pytest.mark.parametrize("origin", [
    "LEGACY_ASSUMED_ARS", None, "", "UNKNOWN", "EXPLICIT_QUOTE",
    "PPI_CATALOG:", "PPI_CATALOG:\n2026-11-05",
    "PPI_CATALOG:not-a-time", "PPI_CATALOG:2026-11-05T15:00:00",
])
def test_migration_default_byma_ars_never_launders_t1_cash(monkeypatch, origin):
    """The migration's BYMA/ARS defaults are not a real five-key source."""
    import cd_spot_ledger as spot

    monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE", "true")
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE paper_positions (
        paper_id TEXT, asset_class TEXT, market TEXT, currency TEXT,
        settlement TEXT, currency_source TEXT)""")
    db.execute("INSERT INTO paper_positions VALUES(?,?,?,?,?,?)",
               ("L", "ACCIONES", "BYMA", "ARS", "A-24HS", origin))
    monkeypatch.setattr(spot, "partition", lambda *_: None)
    monkeypatch.setattr(spot, "sales", lambda _db, _pid: [
        dict(filled_at="2026-11-05T16:45:00-03:00", net_proceeds="100",
             available_at=None, basis="PENDING_CONFIRMATION")])
    assert spot.pending(db, "2026-11-12T00:00:00-03:00", "ARS") == 100
    assert spot.pending(db, "2026-11-13T00:00:00-03:00", "ARS") == 100


def test_t1_missing_currency_source_column_cannot_inherit_ars_default(monkeypatch):
    import cd_spot_ledger as spot

    monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE", "true")
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE paper_positions (
        paper_id TEXT, asset_class TEXT, market TEXT, currency TEXT, settlement TEXT)""")
    db.execute("INSERT INTO paper_positions VALUES(?,?,?,?,?)",
               ("L", "ACCIONES", "BYMA", "ARS", "A-24HS"))
    monkeypatch.setattr(spot, "partition", lambda *_: None)
    monkeypatch.setattr(spot, "sales", lambda _db, _pid: [
        dict(filled_at="2026-11-05T16:45:00-03:00", net_proceeds="100",
             available_at=None, basis="PENDING_CONFIRMATION")])
    assert spot.pending(db, "2026-11-13T00:00:00-03:00", "ARS") == 100


def test_t1_legacy_origin_holds_even_outside_special_calendar(monkeypatch):
    import cd_spot_ledger as spot

    monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE", "true")
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE paper_positions (
        paper_id TEXT, asset_class TEXT, market TEXT, currency TEXT,
        settlement TEXT, currency_source TEXT)""")
    db.execute("INSERT INTO paper_positions VALUES(?,?,?,?,?,?)",
               ("L", "ACCIONES", "BYMA", "ARS", "A-24HS", "LEGACY_ASSUMED_ARS"))
    monkeypatch.setattr(spot, "partition", lambda *_: None)
    monkeypatch.setattr(spot, "sales", lambda _db, _pid: [
        dict(filled_at="2026-11-02T15:00:00-03:00", net_proceeds="100",
             available_at=None, basis="PENDING_CONFIRMATION")])
    assert spot.pending(db, "2026-11-06T00:00:00-03:00", "ARS") == 100
