"""A657: weak IOL option-chain retry cannot override strong PPI identity.

Entirely in-memory SQLite. No provider SDK, host, PPI Watch or order routes.
The synthetic PPI full key is *not* a claim about the live SearchInstrument
response, whose settlement is not proven by the discovery request alone.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3

import pytest

import bu_instrument_catalog as catalog


def _connection():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
    CREATE TABLE financial_instrument_catalog(
      ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,
      settlement_source TEXT,description TEXT,last_seen_at TEXT,run_id TEXT,
      status TEXT,capability TEXT,metadata_json TEXT,
      PRIMARY KEY(ticker,instrument_type,market,currency,settlement));
    CREATE TABLE candidate_universe(
      ticker TEXT,instrument_type TEXT,settlement TEXT,market TEXT,
      can_simulate INTEGER,status TEXT,detail TEXT,checked_at TEXT,
      PRIMARY KEY(ticker,instrument_type,market));
    CREATE TABLE complementary_contract_retry(
      ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,
      source TEXT,observed_at TEXT,state TEXT,reason TEXT,last_attempt_at TEXT,
      attempts INTEGER,
      PRIMARY KEY(ticker,instrument_type,market,currency,settlement,source));
    """)
    return conn


def _metadata(*, ticker="OPTTEST", settlement="INMEDIATA"):
    observed = datetime.now(timezone.utc).isoformat()
    proof = {"source_class": "DERIVED_OFFICIAL_RULE",
             "source_ref": "PPI_FULL_KEY+BYMA_OPTION_CONTRACT:PAPER_ONLY",
             "evidence_hash": "a" * 64, "observed_at": observed}
    return {
        "_discovery_source": "PPI_PRIMARY", "ticker": ticker,
        "type": "OPCIONES", "market": "BYMA", "currency": "ARS",
        "settlement": settlement,
        "financial_contract_v17": {
            "family": "OPCIONES", "market": "BYMA", "currency": "ARS",
            "settlement": settlement, "cash_multiplier": "100",
            "quantity_step": "1", "minimum_quantity": "1",
            "expires_at": "2026-12-18T15:30:00-03:00",
            "underlying": "YPFD", "strike": "100",
            "option_right": "CALL", "metadata_source": "CONTRACT_EVIDENCE_V2_BOUND",
            "field_provenance": {
                name: dict(proof) for name in (
                    "cash_multiplier", "quantity_step", "minimum_quantity",
                    "expires_at", "underlying", "strike", "option_right")},
        },
        "_contract_bridge": {"status": "NORMALIZED", "gaps": []},
    }


def _add_primary(conn, *, ticker="OPTTEST", settlement="INMEDIATA",
                 source="PPI_FIELD", capability="READY_PAPER_OPTION_LONG",
                 metadata=None):
    observed = datetime.now(timezone.utc).isoformat()
    raw = _metadata(ticker=ticker, settlement=settlement) if metadata is None else metadata
    conn.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                 (ticker, "OPCIONES", "BYMA", "ARS", settlement, source,
                  "Strict synthetic PPI option", observed, "A657_OFFHOST_ONLY",
                  "AVAILABLE", capability, json.dumps(raw, sort_keys=True)))


def _add_retry(conn, *, ticker="OPTTEST", settlement="INMEDIATA",
               source="IOL_COMPLEMENTARY",
               reason="COMPLEMENTARY_IDENTITY_AMBIGUOUS:matches=2"):
    stamp = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO complementary_contract_retry VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 (ticker, "OPCIONES", "BYMA", "ARS", settlement, source,
                  stamp, "PENDING_SPECIAL", reason, stamp, 1))


def _full_projection(conn, *, ticker="OPTTEST", settlement="INMEDIATA"):
    catalog.sync_candidate_universe(conn, datetime.now(timezone.utc).isoformat())
    full = conn.execute("""SELECT can_simulate,status,detail FROM candidate_identity_v2
      WHERE ticker=? AND settlement=?""", (ticker, settlement)).fetchone()
    legacy = conn.execute("""SELECT can_simulate,status,detail FROM candidate_universe
      WHERE ticker=?""", (ticker,)).fetchone()
    assert full is not None and legacy is not None
    return full, legacy


def test_advisory_iol_ambiguity_cannot_poison_verified_ppi_v17_option():
    conn = _connection()
    _add_primary(conn)
    _add_retry(conn)
    full, legacy = _full_projection(conn)
    assert full[:2] == (1, "AVAILABLE")
    assert legacy[:2] == (1, "AVAILABLE")


@pytest.mark.parametrize("change", [
    "missing_contract", "review_capability", "blocked_bridge",
    "ppi_request_only", "provider_negative", "iol_contract_provenance",
    "broken_contract", "source_conflict", "wrong_contract_key",
])
def test_weak_iol_retry_still_blocks_incomplete_or_conflicted_ppi_option(change):
    conn = _connection()
    raw = _metadata()
    source, capability = "PPI_FIELD", "READY_PAPER_OPTION_LONG"
    if change == "missing_contract":
        raw.pop("financial_contract_v17")
    elif change == "review_capability":
        capability = "CONTRACT_EVIDENCE_REVIEW_REQUIRED"
    elif change == "blocked_bridge":
        raw["_contract_bridge"] = {"status": "BLOCKED", "gaps": ["CHANGE_REVIEW_REQUIRED"]}
    elif change == "ppi_request_only":
        source = "REQUEST_CANDIDATE"
        raw.pop("settlement")
    elif change == "provider_negative":
        raw["isTradable"] = False
    elif change == "iol_contract_provenance":
        raw["financial_contract_v17"]["field_provenance"]["strike"] = {
            "source_class": "IOL_STRUCTURED_API", "source_ref": "iol:chain",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "evidence_hash": "b" * 64}
    elif change == "broken_contract":
        raw["financial_contract_v17"]["strike"] = "NaN"
    elif change == "source_conflict":
        raw["_contract_conflicts"] = {"fields": ["strike"], "source": "IOL_COMPLEMENTARY"}
    elif change == "wrong_contract_key":
        raw["financial_contract_v17"]["settlement"] = "A-24HS"
    _add_primary(conn, source=source, capability=capability, metadata=raw)
    _add_retry(conn)
    full, legacy = _full_projection(conn)
    assert full[0] == legacy[0] == 0
    assert "PAUSED_EXPLICIT" == full[1] == legacy[1]


@pytest.mark.parametrize("source,reason", [
    ("BYMA_PUBLIC_COMPLEMENTARY", "COMPLEMENTARY_IDENTITY_AMBIGUOUS:matches=2"),
    ("IOL_COMPLEMENTARY", "IOL_ASSET_IDENTITY_AMBIGUOUS"),
    ("IOL_COMPLEMENTARY", "COMPLEMENTARY_IDENTITY_AMBIGUOUS:matches=1"),
    ("IOL_COMPLEMENTARY", "COMPLEMENTARY_IDENTITY_AMBIGUOUS:matches=UNVERIFIED"),
    ("CONTRACT_EVIDENCE_V2", "COMPLEMENTARY_IDENTITY_AMBIGUOUS:matches=2"),
])
def test_other_ambiguities_remain_hard_vetoes(source, reason):
    conn = _connection()
    _add_primary(conn)
    _add_retry(conn, source=source, reason=reason)
    full, legacy = _full_projection(conn)
    assert full[0] == legacy[0] == 0


def test_mixed_iol_and_other_ambiguous_sources_remain_blocked():
    conn = _connection()
    _add_primary(conn)
    _add_retry(conn)
    _add_retry(conn, source="BYMA_PUBLIC_COMPLEMENTARY")
    full, legacy = _full_projection(conn)
    assert full[0] == legacy[0] == 0


def test_legacy_short_key_with_two_valid_ppi_options_stays_ambiguous():
    conn = _connection()
    _add_primary(conn)
    _add_primary(conn, settlement="A-24HS")
    _add_retry(conn)
    _add_retry(conn, settlement="A-24HS")
    full, legacy = _full_projection(conn)
    assert full[0] == 1
    assert legacy[0] == 0
    assert "IDENTITY_AMBIGUOUS" in legacy[2]
    assert conn.execute("SELECT COUNT(*) FROM candidate_identity_v2 WHERE can_simulate=1").fetchone()[0] == 2
