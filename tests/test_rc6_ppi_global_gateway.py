"""PAPER-only concrete owner authority. No PPI SDK, network or host state."""

import json

from be_paper_engine import PaperStore
from bu_instrument_catalog import init_schema
from rc6_ppi_global_budget import PaperDirectReadBudget
from rc6_ppi_shared_session_owner import (
    ObservedPaperCapacityAuthority, PrimaryPPICatalogAuthority,
    load_primary_ppi_catalog_snapshot,
)


FIVE = ("GGAL", "ACCIONES", "BYMA", "ARS", "INMEDIATA")


def _seed(store, *, settlement_source="PPI_FIELD", primary=True):
    with store.connect() as connection:
        connection.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (*FIVE, settlement_source, "OFFHOST", "2026-11-19T15:00:00Z",
             "RUN_TEST", "AVAILABLE", "NOT_READY", json.dumps({
                 "_discovery_source": "PPI_PRIMARY" if primary else "IOL_COMPLEMENTARY",
                 "ticker": FIVE[0], "type": FIVE[1], "market": FIVE[2],
                 "currency": FIVE[3], "settlement": FIVE[4]})))


def test_real_paper_budget_authority_never_claims_unknown_provider_quota(tmp_path):
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    budget = PaperDirectReadBudget(store.path)
    authority = ObservedPaperCapacityAuthority(budget)
    assert authority.permits("OWNER_AUTHENTICATE", five_key=None) is True
    assert authority.permits("REST_NATIVE_ACQUIRE", five_key=FIVE) is True
    assert authority.permits("STREAM_CONNECT", five_key=None) is False
    assert authority.provider_quota == "UNKNOWN"
    store.state(real_orders_sent=1)
    assert authority.permits("REST_NATIVE_ACQUIRE", five_key=FIVE) is False
    assert authority.permits("OWNER_AUTHENTICATE", five_key=None) is False


def test_catalog_authority_requires_exact_literal_ppi_primary_five_key(tmp_path):
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    init_schema(store)
    _seed(store)
    rows, keys, digest = load_primary_ppi_catalog_snapshot(store.path)
    assert keys == [FIVE]
    assert rows == [{"ticker": "GGAL", "type": "ACCIONES",
                    "market": "BYMA", "currency": "ARS"}]
    assert len(digest) == 64
    authority = PrimaryPPICatalogAuthority(keys)
    assert authority.permits("PPI_FIVE_KEY_CATALOG_ATTESTED", five_key=FIVE) is True
    assert authority.permits("PPI_FIVE_KEY_CATALOG_ATTESTED", five_key=None) is True
    assert authority.permits("PPI_FIVE_KEY_CATALOG_ATTESTED", five_key=(
        "GGAL", "ACCIONES", "BYMA", "USD", "INMEDIATA")) is False
    with store.connect() as connection:
        connection.execute("UPDATE financial_instrument_catalog SET settlement_source='REQUEST_CANDIDATE'")
    from pytest import raises
    with raises(ValueError, match="PPI_PRIMARY_CATALOG_EMPTY_OR_UNVERIFIED"):
        load_primary_ppi_catalog_snapshot(store.path)
