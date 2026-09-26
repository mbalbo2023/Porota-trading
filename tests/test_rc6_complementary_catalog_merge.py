import json
import sqlite3
from datetime import datetime, timezone

import bu_instrument_catalog as catalog


def _primary(status="STALE"):
    return {
        "ticker":"GD30","instrument_type":"BONOS","market":"BYMA","currency":"ARS",
        "settlement":"A-24HS","settlement_source":"PPI_FIELD","description":"GD30",
        "last_seen_at":"2026-09-01T12:00:00+00:00","run_id":"PPI","status":status,
        "capability":"NEEDS_NOMINAL_UNITS",
        "raw":{"_discovery_source":"PPI_PRIMARY"},
    }


def _complement(observed_at="2026-09-25T21:00:00+00:00"):
    return {
        "ticker":"GD30","instrument_type":"BONOS","market":"BYMA","currency":"ARS",
        "settlement":"A-24HS","source":"IOL_COMPLEMENTARY","observed_at":observed_at,
        "financial_contract_v17":{
            "currency":"ARS","market":"BYMA","settlement":"A-24HS",
            "cash_multiplier":"0.01","quantity_step":"100",
            "metadata_source":"IOL_FIXED_INCOME_SIMULATION",
        },
    }


def test_fresh_exact_complement_promotes_fixed_income_primary_to_paper():
    now=datetime(2026,9,25,21,5,tzinfo=timezone.utc)
    comp=_complement()
    assert catalog.complementary_is_fresh(comp,max_age_seconds=86400,now=now)
    merged=catalog.complete_with_complement(_primary(),comp)
    assert merged["status"]=="AVAILABLE"
    assert merged["capability"]=="READY_PAPER_SPOT"
    assert merged["raw"]["_discovery_source"]=="PPI_PRIMARY"
    assert merged["raw"]["_contract_complement_source"]=="IOL_COMPLEMENTARY"
    assert merged["raw"]["_availability_source"]=="PPI_IDENTITY_PLUS_IOL_COMPLEMENTARY"
    assert merged["last_seen_at"]==comp["observed_at"]


def test_mismatched_identity_never_completes_primary():
    comp=_complement()
    comp["currency"]="USD"
    merged=catalog.complete_with_complement(_primary(),comp)
    assert merged["status"]=="STALE"
    assert merged["capability"]=="NEEDS_NOMINAL_UNITS"
    assert "financial_contract_v17" not in merged["raw"]


def test_stale_complement_is_not_fresh():
    now=datetime(2026,9,25,21,5,tzinfo=timezone.utc)
    assert not catalog.complementary_is_fresh(
        _complement("2026-09-20T21:00:00+00:00"),max_age_seconds=86400,now=now)


def test_candidate_universe_is_rebuilt_from_catalog_readiness():
    c=sqlite3.connect(":memory:")
    c.executescript("""
      CREATE TABLE financial_instrument_catalog(
        ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,
        settlement_source TEXT,description TEXT,last_seen_at TEXT,run_id TEXT,
        status TEXT,capability TEXT,metadata_json TEXT,
        PRIMARY KEY(ticker,instrument_type,market,currency,settlement));
      CREATE TABLE candidate_universe(
        ticker TEXT NOT NULL,instrument_type TEXT NOT NULL,settlement TEXT NOT NULL,
        market TEXT NOT NULL,can_simulate INTEGER NOT NULL,status TEXT NOT NULL,
        detail TEXT NOT NULL,last_checked_at TEXT NOT NULL,
        PRIMARY KEY(ticker,instrument_type,market));
    """)
    rows=[
      ("GGAL","ACCIONES","BYMA","ARS","A-24HS","PPI","","2026-09-25T20:00:00+00:00","r","AVAILABLE","READY_PAPER_SPOT","{}"),
      ("GD30","BONOS","BYMA","ARS","A-24HS","PPI","","2026-09-25T20:00:00+00:00","r","STALE","NEEDS_NOMINAL_UNITS","{}"),
    ]
    c.executemany("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",rows)
    c.execute("INSERT INTO candidate_universe VALUES(?,?,?,?,?,?,?,?)",
              ("GD30","BONOS","A-24HS","BYMA",1,"AVAILABLE","OLD_GHOST","old"))
    catalog.sync_candidate_universe(c,"2026-09-25T21:00:00+00:00")
    result={r[0]:(r[4],r[5],r[6]) for r in c.execute("SELECT * FROM candidate_universe")}
    assert result["GGAL"]==(1,"AVAILABLE","READY_PAPER_SPOT")
    assert result["GD30"]==(0,"STALE","NEEDS_NOMINAL_UNITS")


def test_fresh_exact_complement_revives_stale_spot_without_contract_payload():
    primary={
        "ticker":"SPY","instrument_type":"CEDEARS","market":"BYMA","currency":"ARS",
        "settlement":"A-24HS","settlement_source":"PPI_FIELD","description":"SPY",
        "last_seen_at":"2026-09-01T12:00:00+00:00","run_id":"PPI","status":"STALE",
        "capability":"HISTORY_UNAVAILABLE_PPI","raw":{},
    }
    comp={
        "ticker":"SPY","instrument_type":"CEDEARS","market":"BYMA","currency":"ARS",
        "settlement":"A-24HS","source":"IOL_COMPLEMENTARY",
        "observed_at":"2026-09-25T21:00:00+00:00",
    }
    merged=catalog.complete_with_complement(primary,comp)
    assert merged["status"]=="AVAILABLE"
    assert merged["capability"]=="READY_PAPER_SPOT"
    assert merged["last_seen_at"]==comp["observed_at"]
    assert merged["raw"]["_availability_source"]=="PPI_IDENTITY_PLUS_IOL_COMPLEMENTARY"


def test_complement_does_not_revive_spot_when_currency_identity_differs():
    primary={
        "ticker":"SPY","instrument_type":"CEDEARS","market":"BYMA","currency":"ARS",
        "settlement":"A-24HS","settlement_source":"PPI_FIELD","description":"SPY",
        "last_seen_at":"2026-09-01T12:00:00+00:00","run_id":"PPI","status":"STALE",
        "capability":"READY_PAPER_SPOT","raw":{},
    }
    comp={
        "ticker":"SPY","instrument_type":"CEDEARS","market":"BYMA","currency":"USD_CCL",
        "settlement":"A-24HS","source":"IOL_COMPLEMENTARY",
        "observed_at":"2026-09-25T21:00:00+00:00",
    }
    merged=catalog.complete_with_complement(primary,comp)
    assert merged["status"]=="STALE"
    assert merged["last_seen_at"]=="2026-09-01T12:00:00+00:00"
