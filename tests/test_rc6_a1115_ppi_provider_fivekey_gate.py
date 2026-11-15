"""Fail-closed native PPI identity witness, independent of request parameters.

Fixtures are synthetic: no broker access, network, database file or real orders.
"""

import json
import sqlite3
from datetime import datetime, timezone

import pytest

import bu_instrument_catalog as catalog


CHECKED = "2026-11-10T15:00:00+00:00"
PPI_RAW_FULL = {
    "ticker": "GGAL", "type": "ACCIONES", "market": "BYMA",
    "currency": "ARS", "settlement": "A-24HS", "_discovery_source": "PPI_PRIMARY",
}


def _database():
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE financial_instrument_catalog(
          ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,
          settlement_source TEXT,description TEXT,last_seen_at TEXT,run_id TEXT,
          status TEXT,capability TEXT,metadata_json TEXT,
          PRIMARY KEY(ticker,instrument_type,market,currency,settlement));
        CREATE TABLE candidate_universe(
          ticker TEXT,instrument_type TEXT,settlement TEXT,market TEXT,
          can_simulate INTEGER,status TEXT,detail TEXT,last_checked_at TEXT,
          PRIMARY KEY(ticker,instrument_type,market));
    """)
    return connection


def _insert(connection, *, source, raw, settlement="A-24HS"):
    connection.execute("""INSERT INTO financial_instrument_catalog
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
            "GGAL", "ACCIONES", "BYMA", "ARS", settlement, source, "GGAL",
            CHECKED, "synthetic", "AVAILABLE", "READY_PAPER_SPOT", json.dumps(raw),
        ))


@pytest.mark.parametrize("discovery", ["PPI_PRIMARY", "LEGACY_CATALOG", "REQUEST_ONLY"])
def test_requested_settlement_never_becomes_simulation_ready(discovery):
    """Break caught: treating a SearchInstrument request as native PPI settlement."""
    connection = _database()
    _insert(connection, source="REQUEST_CANDIDATE", raw={
        "ticker": "GGAL", "type": "ACCIONES", "market": "BYMA", "currency": "ARS",
        "_discovery_source": discovery,
        "_availability_source": "PPI_PRIMARY+IOL_COMPLEMENTARY",
    })
    catalog.sync_candidate_universe(connection, CHECKED)
    identity = connection.execute("SELECT can_simulate,detail FROM candidate_identity_v2").fetchone()
    summary = connection.execute("SELECT can_simulate,detail FROM candidate_universe").fetchone()
    assert identity[0] == 0
    assert summary[0] == 0
    assert "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED" in identity[1]


@pytest.mark.parametrize("raw", [
    {**PPI_RAW_FULL, "settlement": "A-CI"},
    {key: value for key, value in PPI_RAW_FULL.items() if key != "currency"},
    {**PPI_RAW_FULL, "_discovery_source": "IOL_COMPLEMENTARY"},
])
def test_native_field_marker_cannot_override_provider_full_key(raw):
    """Break caught: a PPI_FIELD flag alone upgrades mismatched/absent raw identity."""
    connection = _database()
    _insert(connection, source="PPI_FIELD", raw=raw)
    catalog.sync_candidate_universe(connection, CHECKED)
    ready, detail = connection.execute(
        "SELECT can_simulate,detail FROM candidate_identity_v2").fetchone()
    assert ready == 0
    assert "PPI_FULL_KEY_NOT_PROVIDER_ATTESTED" in detail


def test_literal_native_fivekey_remains_eligible_without_new_restrictions():
    """Break caught: fail-closed patch blocks a genuinely complete native key."""
    connection = _database()
    _insert(connection, source="PPI_FIELD", raw=PPI_RAW_FULL)
    catalog.sync_candidate_universe(connection, CHECKED)
    assert connection.execute("SELECT can_simulate FROM candidate_identity_v2").fetchone()[0] == 1
    assert connection.execute("SELECT can_simulate FROM candidate_universe").fetchone()[0] == 1


def test_quote_terms_rejects_request_only_even_before_projection_refresh():
    """Break caught: stale READY ledger bypasses live quote_terms admission."""
    record = {
        "ticker": "GGAL", "instrument_type": "ACCIONES", "market": "BYMA",
        "currency": "ARS", "settlement": "A-24HS",
        "settlement_source": "REQUEST_CANDIDATE", "capability": "READY_PAPER_SPOT",
        "last_seen_at": CHECKED, "status": "AVAILABLE",
        "raw": {key: value for key, value in PPI_RAW_FULL.items() if key != "settlement"},
    }
    terms = catalog.quote_terms(record, now=datetime.fromisoformat(CHECKED))
    assert "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED" in terms["opening_block_reason"]
