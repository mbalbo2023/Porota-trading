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


def _seed_query_only(store, *, currency="ARS", primary=True):
    key = ("GGAL", "ACCIONES", "BYMA", currency, "INMEDIATA")
    with store.connect() as connection:
        connection.execute(
            "INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (*key, "REQUEST_CANDIDATE", "OFFHOST", "2026-11-19T15:00:00Z",
             "RUN_TEST", "STALE", "NOT_READY",
             json.dumps({"_discovery_source":
                         "PPI_PRIMARY" if primary else "IOL_COMPLEMENTARY",
                         "ticker": key[0], "type": key[1], "market": key[2],
                         "currency": key[3]})))
    return key


def test_gateway_query_identity_is_not_provider_contract(tmp_path):
    from pytest import raises

    store = PaperStore(str(tmp_path / "paper.sqlite"))
    init_schema(store)
    key = _seed_query_only(store)
    with raises(ValueError, match="PPI_PRIMARY_CATALOG_EMPTY_OR_UNVERIFIED"):
        load_primary_ppi_catalog_snapshot(store.path)
    rows, query_keys, digest, attested = load_primary_ppi_catalog_snapshot(
        store.path, include_diagnostic_candidates=True)
    assert rows == [{"ticker": "GGAL", "type": "ACCIONES",
                     "market": "BYMA", "currency": "ARS"}]
    assert query_keys == [key] and attested == [] and len(digest) == 64
    authority = PrimaryPPICatalogAuthority(attested, query_keys=query_keys)
    assert authority.permits("PPI_NATIVE_QUERY_IDENTITY_UNIQUE", five_key=key)
    assert authority.permits("PPI_FIVE_KEY_CATALOG_ATTESTED", five_key=key) is False
    assert authority.permits("PPI_FIVE_KEY_CATALOG_ATTESTED") is False
    with store.connect() as connection:
        assert tuple(connection.execute(
            "SELECT settlement_source,status FROM financial_instrument_catalog"
            ).fetchone()) == ("REQUEST_CANDIDATE", "STALE")
        assert connection.execute(
            "SELECT real_orders_sent FROM observer_state WHERE id=1"
            ).fetchone()[0] == 0


def test_gateway_query_rejects_three_key_collisions_and_nonprimary(tmp_path):
    from pytest import raises

    store = PaperStore(str(tmp_path / "paper.sqlite"))
    init_schema(store)
    _seed_query_only(store, currency="ARS")
    _seed_query_only(store, currency="USD")
    with raises(ValueError, match="PPI_PRIMARY_CATALOG_EMPTY_OR_UNVERIFIED"):
        load_primary_ppi_catalog_snapshot(
            store.path, include_diagnostic_candidates=True)

    other = PaperStore(str(tmp_path / "only_other.sqlite"))
    init_schema(other)
    _seed_query_only(other, primary=False)
    with raises(ValueError, match="PPI_PRIMARY_CATALOG_EMPTY_OR_UNVERIFIED"):
        load_primary_ppi_catalog_snapshot(
            other.path, include_diagnostic_candidates=True)
