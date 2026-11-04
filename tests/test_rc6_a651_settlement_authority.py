"""A651: a requested settlement is not an observed PPI five-key.

Pure local SQLite/PAPER fixtures: no market GET, host, order or deploy.
"""
from datetime import datetime, timezone

import pytest

import bu_instrument_catalog as catalog
import cr_contract_evidence_v2_mass_hf6 as mass
from be_paper_engine import PaperStore


NOW = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)


def _store(tmp_path):
    store = PaperStore(str(tmp_path / "a651-settlement-paper.db"))
    catalog.init_schema(store)
    with store.connect() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS candidate_universe(
            ticker TEXT NOT NULL, instrument_type TEXT NOT NULL,
            settlement TEXT NOT NULL, market TEXT NOT NULL,
            can_simulate INTEGER NOT NULL, status TEXT NOT NULL,
            detail TEXT NOT NULL, checked_at TEXT NOT NULL,
            PRIMARY KEY(ticker,instrument_type,market))""")
    return store


def _ppi_row(*, ticker="TEST", settlement=None, hint="A-24HS", discovery="PPI_PRIMARY"):
    payload = {"ticker": ticker, "type": "ACCIONES", "market": "BYMA",
               "currency": "ARS", "_discovery_source": discovery}
    if settlement is not None:
        payload["settlement"] = settlement
    return catalog.normalize_record(payload, hint, NOW.isoformat(), "A651-LOCAL-FIXTURE")


def _persist_project(store, *records):
    with store.connect() as connection:
        for record in records:
            catalog.persist(connection, record)
        catalog.sync_candidate_universe(connection, NOW.isoformat())


def _identity_row(store, record):
    with store.connect() as connection:
        return connection.execute("""SELECT can_simulate, detail FROM candidate_identity_v2
            WHERE ticker=? AND instrument_type=? AND market=? AND currency=? AND settlement=?""",
            (record["ticker"], record["instrument_type"], record["market"],
             record["currency"], record["settlement"])).fetchone()


def _catalog_row(record):
    import json
    return {**record, "metadata_json": json.dumps(record["raw"])}


def test_requested_settlement_remains_shadow_even_with_ready_spot_capability(tmp_path):
    record = _ppi_row()
    assert record["settlement_source"] == "REQUEST_CANDIDATE"
    assert record["capability"] == "READY_PAPER_SPOT"  # static catalog only
    store = _store(tmp_path)
    _persist_project(store, record)

    state = _identity_row(store, record)
    assert state[0] == 0
    assert "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED" in state[1]

    selected = catalog.lookup(store, "TEST", "ACCIONES", "A-24HS",
                              market="BYMA", currency="ARS")
    assert selected is not None  # Discovery/SHADOW catalog stays available.
    assert "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED" in selected["_admission_reason"]
    assert "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED" in catalog.quote_terms(
        selected, now=NOW)["opening_block_reason"]
    records, skipped, identities = mass.planned_records([_catalog_row(record)])
    assert records == []
    assert skipped == {"PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED": 1}
    assert identities == {}


def test_provider_native_settlement_keeps_existing_paper_catalog_path(tmp_path):
    record = _ppi_row(settlement="A-24HS")
    assert record["settlement_source"] == "PPI_FIELD"
    store = _store(tmp_path)
    _persist_project(store, record)
    state = _identity_row(store, record)
    assert state[0] == 1
    selected = catalog.lookup(store, "TEST", "ACCIONES", "A-24HS",
                              market="BYMA", currency="ARS")
    assert selected is not None and "_admission_reason" not in selected
    assert catalog.quote_terms(selected, now=NOW)["opening_block_reason"] == ""
    records, skipped, identities = mass.planned_records([_catalog_row(record)])
    assert [entry["source_class"] for entry in records] == [
        "PPI_STRUCTURED_API", "DERIVED_OFFICIAL_RULE"]
    assert skipped == {} and identities == {"ACCIONES": 1}


def test_sibling_same_ticker_and_market_cannot_promote_requested_settlement(tmp_path):
    native = _ppi_row(settlement="A-24HS")
    requested = _ppi_row(hint="INMEDIATA")
    store = _store(tmp_path)
    _persist_project(store, native, requested)
    assert _identity_row(store, native)[0] == 1
    assert _identity_row(store, requested)[0] == 0
    assert "_admission_reason" not in catalog.lookup(
        store, "TEST", "ACCIONES", "A-24HS", market="BYMA", currency="ARS")
    requested_found = catalog.lookup(
        store, "TEST", "ACCIONES", "INMEDIATA", market="BYMA", currency="ARS")
    assert "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED" in requested_found["_admission_reason"]


@pytest.mark.parametrize("discovery,settlement", [
    ("IOL_COMPLEMENTARY", "A-24HS"),
    ("PPI_PRIMARY", "INMEDIATA"),
])
def test_source_or_literal_provider_settlement_mismatch_cannot_attest(
        tmp_path, discovery, settlement):
    record = _ppi_row(settlement=settlement, discovery=discovery)
    # The SQL key is now different from the provider's literal settlement,
    # or its source is a complement masquerading as PPI_FIELD.
    record["settlement"] = "A-24HS"
    store = _store(tmp_path)
    _persist_project(store, record)
    assert _identity_row(store, record)[0] == 0
    assert "PPI_FULL_KEY_NOT_PROVIDER_ATTESTED" in catalog.quote_terms(
        record, now=NOW)["opening_block_reason"]
    records, skipped, identities = mass.planned_records([_catalog_row(record)])
    assert records == [] and identities == {}
