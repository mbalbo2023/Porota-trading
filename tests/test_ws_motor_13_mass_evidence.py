import json
import sqlite3
from datetime import datetime, timezone

import bu_instrument_catalog as catalog
import cp_contract_evidence_v2_hf6 as evidence
import cr_contract_evidence_v2_mass_hf6 as mass
import rc6_contract_bridge as bridge


NOW = datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection


def _store(tmp_path):
    store = Store(tmp_path / "ws13.db")
    catalog.init_schema(store)
    with store.connect() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS candidate_universe(
          ticker TEXT NOT NULL,instrument_type TEXT NOT NULL,settlement TEXT NOT NULL,
          market TEXT NOT NULL,can_simulate INTEGER NOT NULL,status TEXT NOT NULL,
          detail TEXT NOT NULL,checked_at TEXT NOT NULL,
          PRIMARY KEY(ticker,instrument_type,market))""")
    return store


def _persist(store, *, ticker, family, nominal):
    record = catalog.normalize_record({
        "ticker": ticker, "type": family, "market": "BYMA",
        "currency": "Pesos", "nominalInPrice": nominal,
    }, "A-24HS", NOW, "PPI-FULL-CATALOG")
    with store.connect() as connection:
        catalog.persist(connection, record)
    return record


def test_mass_collector_covers_every_available_ppi_identity_without_nominal_inference(tmp_path):
    store = _store(tmp_path)
    _persist(store, ticker="GGAL", family="ACCIONES", nominal=1)
    _persist(store, ticker="GD30", family="BONOS", nominal=100)

    result = mass.collect(store, run_id="ws13-first")
    assert result["catalog_available"] == 2
    assert result["identities_written"] == {"ACCIONES": 1, "BONOS": 1}
    assert result["real_routes_used"] == []

    rows = evidence.current_records(store)
    assert len(rows) == 3  # two PPI identities plus the BYMA spot-unit rule
    bond = [row for row in rows if row["ticker"] == "GD30"][0]
    assert bond["evidence"]["price_quote_unit"] == 100
    assert "cash_multiplier" not in bond["evidence"]
    assert "quantity_step" not in bond["evidence"]

    claims = {row["ticker"]: row for row in bridge.complements_from_store(store)}
    assert claims["GGAL"]["contract_bridge"]["status"] == "NORMALIZED"
    assert claims["GD30"]["contract_bridge"]["status"] == "BLOCKED"
    assert set(claims["GD30"]["contract_bridge"]["gaps"]) >= {
        "MISSING:cash_multiplier", "MISSING:quantity_step", "MISSING:minimum_quantity",
    }


def test_mass_collector_is_idempotent_and_does_not_create_change_review(tmp_path):
    store = _store(tmp_path)
    _persist(store, ticker="GGAL", family="ACCIONES", nominal=1)
    first = mass.collect(store, run_id="ws13-first")
    second = mass.collect(store, run_id="ws13-second")
    assert first["first_seen"] == 2
    assert second["first_seen"] == 0
    with store.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM contract_evidence_v2_snapshots").fetchone()[0] == 2
        assert connection.execute("""SELECT COUNT(*) FROM contract_evidence_v2_changes
          WHERE status='CHANGED_REVIEW_REQUIRED'""").fetchone()[0] == 0


def test_partial_v2_inventory_keeps_real_family_blocker_in_catalog(tmp_path):
    store = _store(tmp_path)
    primary = _persist(store, ticker="GD30", family="BONOS", nominal=100)
    mass.collect(store, run_id="ws13")
    claim = bridge.complements_from_store(store)[0]
    merged = catalog.complete_with_complement(primary, claim)
    assert merged["capability"] == "NEEDS_NOMINAL_UNITS"
    assert merged["raw"]["_contract_bridge"]["status"] == "BLOCKED"


def test_full_key_candidate_can_be_ready_while_legacy_selection_stays_closed(tmp_path):
    store = _store(tmp_path)
    with store.connect() as connection:
        for currency in ("ARS", "USD"):
            record = catalog.normalize_record({
                "ticker": "TEST", "type": "ACCIONES", "market": "BYMA",
                "currency": currency,
            }, "A-24HS", NOW, "PPI")
            catalog.persist(connection, record)
        catalog.sync_candidate_universe(connection, NOW)
        full = connection.execute("""SELECT currency,can_simulate,detail
          FROM candidate_identity_v2 ORDER BY currency""").fetchall()
        legacy = connection.execute("""SELECT can_simulate,detail
          FROM candidate_universe""").fetchone()
    assert [tuple(row) for row in full] == [
        ("ARS", 1, "READY_PAPER_SPOT"), ("USD", 1, "READY_PAPER_SPOT")]
    assert tuple(legacy) == (0, "IDENTITY_AMBIGUOUS")
    assert catalog.lookup(store, "TEST", "ACCIONES", "A-24HS") is None


def test_batch_ingest_is_atomic_on_invalid_row(tmp_path):
    store = _store(tmp_path)
    good = {
        "family": "ACCIONES", "ticker": "GGAL", "market": "BYMA",
        "currency": "ARS", "settlement": "A-24HS",
        "source_class": "PPI_STRUCTURED_API", "source_ref": "fixture",
        "observed_at": NOW, "evidence": {"currency": "ARS"},
    }
    bad = {**good, "ticker": "BAD", "evidence": {"cookie": "secret"}}
    try:
        evidence.record_snapshots(store, [good, bad])
    except ValueError as exc:
        assert "SENSITIVE_FIELD" in str(exc)
    else:
        raise AssertionError("invalid batch unexpectedly accepted")
    with store.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM contract_evidence_v2_snapshots").fetchone()[0] == 0
