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
    assert merged["raw"]["_availability_source"]=="PPI_PRIMARY+IOL_COMPLEMENTARY"
    assert merged["last_seen_at"]=="2026-09-01T12:00:00+00:00"
    assert merged["raw"]["_freshness_by_source"]["IOL_COMPLEMENTARY"]==comp["observed_at"]
    assert merged["raw"]["_source_precedence"]=="PPI_PRIMARY>IOL_COMPLEMENTARY>BYMA_PUBLIC_COMPLEMENTARY"


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
      ("GGAL","ACCIONES","BYMA","ARS","A-24HS","PPI_FIELD","","2026-09-25T20:00:00+00:00","r","AVAILABLE","READY_PAPER_SPOT",'{"_discovery_source":"PPI_PRIMARY"}'),
      ("GD30","BONOS","BYMA","ARS","A-24HS","PPI_FIELD","","2026-09-25T20:00:00+00:00","r","STALE","NEEDS_NOMINAL_UNITS",'{"_discovery_source":"PPI_PRIMARY"}'),
    ]
    c.executemany("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",rows)
    c.executemany("INSERT INTO candidate_universe VALUES(?,?,?,?,?,?,?,?)", [
        ("GD30","BONOS","A-24HS","BYMA",1,"AVAILABLE","OLD_VALUE","old"),
        ("SPY","ETF","A-24HS","BYMA",1,"AVAILABLE","OLD_GHOST","old"),
    ])
    catalog.sync_candidate_universe(c,"2026-09-25T21:00:00+00:00")
    result={r[0]:(r[4],r[5],r[6]) for r in c.execute("SELECT * FROM candidate_universe")}
    assert result["GGAL"]==(1,"AVAILABLE","READY_PAPER_SPOT")
    assert result["GD30"]==(0,"STALE","NEEDS_NOMINAL_UNITS")
    assert "SPY" not in result


def _candidate_projection_db():
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
      CREATE TABLE complementary_contract_retry(
        ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,
        source TEXT,observed_at TEXT,state TEXT,reason TEXT,last_attempt_at TEXT,
        attempts INTEGER);
    """)
    return c


def _catalog_identity(ticker="GGAL", family="ACCIONES", currency="ARS",
                      settlement="A-24HS", settlement_source="PPI_FIELD",
                      last_seen="2026-09-25T20:30:00+00:00", status="AVAILABLE",
                      capability="READY_PAPER_SPOT", metadata=None):
    metadata = metadata or {"_discovery_source":"PPI_PRIMARY"}
    return (ticker,family,"BYMA",currency,settlement,settlement_source,"",last_seen,
            "run",status,capability,json.dumps(metadata))


def test_candidate_projection_fails_closed_for_multi_identity_and_is_idempotent():
    c=_candidate_projection_db()
    c.executemany("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", [
        _catalog_identity(currency="ARS"),
        _catalog_identity(currency="USD_CCL"),
    ])
    catalog.sync_candidate_universe(c,"2026-09-25T21:00:00+00:00")
    first=c.execute("SELECT * FROM candidate_universe").fetchall()
    assert len(first)==1
    assert first[0][4:7]==(0,"AVAILABLE","IDENTITY_AMBIGUOUS")
    catalog.sync_candidate_universe(c,"2026-09-25T21:00:00+00:00")
    assert c.execute("SELECT * FROM candidate_universe").fetchall()==first


def test_complementary_shadow_does_not_make_unique_ppi_identity_ambiguous():
    c=_candidate_projection_db()
    c.executemany("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", [
        _catalog_identity(ticker="GGAL",currency="ARS"),
        _catalog_identity(ticker="GGAL",currency="UNKNOWN",settlement_source="IOL_COMPLEMENTARY",
                          status="OBSERVED_SHADOW",capability="MISSING_CURRENCY_OR_MARKET",
                          metadata={"_discovery_source":"IOL_COMPLEMENTARY"}),
    ])
    catalog.sync_candidate_universe(c,"2026-09-25T21:00:00+00:00")
    row=c.execute("SELECT can_simulate,status,detail FROM candidate_universe").fetchone()
    assert row==(1,"AVAILABLE","READY_PAPER_SPOT")


def test_candidate_projection_removes_reclassified_family_ghost():
    c=_candidate_projection_db()
    c.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
              _catalog_identity(ticker="DIA",family="CEDEARS"))
    c.execute("INSERT INTO candidate_universe VALUES(?,?,?,?,?,?,?,?)",
              ("DIA","ETF","A-24HS","BYMA",1,"AVAILABLE","OLD","old"))
    catalog.sync_candidate_universe(c,"2026-09-25T21:00:00+00:00")
    assert c.execute("SELECT instrument_type FROM candidate_universe").fetchall()==[("CEDEARS",)]


def test_candidate_projection_rejects_stale_non_primary_and_retry_ambiguity():
    c=_candidate_projection_db()
    c.executemany("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", [
        _catalog_identity(ticker="STALE",last_seen="2026-09-10T20:30:00+00:00"),
        _catalog_identity(ticker="IOL",settlement_source="IOL_COMPLEMENTARY",
                          metadata={"_discovery_source":"IOL_COMPLEMENTARY"}),
        _catalog_identity(ticker="RETRY"),
    ])
    c.execute("INSERT INTO complementary_contract_retry VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              ("RETRY","ACCIONES","BYMA","ARS","A-24HS","PPI",None,
               "PENDING_RETRY","IDENTITY_AMBIGUOUS","2026-09-25T20:40:00+00:00",1))
    catalog.sync_candidate_universe(c,"2026-09-25T21:00:00+00:00")
    result={row[0]:(row[4],row[6]) for row in c.execute(
        "SELECT * FROM candidate_universe ORDER BY ticker")}
    assert result["STALE"]==(0,"PPI_FRESHNESS_STALE")
    assert result["IOL"]==(0,"PPI_PRIMARY_IDENTITY_NOT_VERIFIED")
    assert result["RETRY"]==(0,"RETRY_IDENTITY_AMBIGUOUS")


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
    assert merged["last_seen_at"]=="2026-09-01T12:00:00+00:00"
    assert merged["raw"]["_freshness_by_source"]["IOL_COMPLEMENTARY"]==comp["observed_at"]
    assert merged["raw"]["_availability_source"]=="PPI_PRIMARY+IOL_COMPLEMENTARY"


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


def test_byma_missing_currency_can_fill_freshness_but_never_overwrite_ppi_identity():
    primary={
        "ticker":"AAPL","instrument_type":"CEDEARS","market":"BYMA","currency":"ARS",
        "settlement":"A-24HS","settlement_source":"PPI_FIELD","description":"Apple CEDEAR PPI",
        "last_seen_at":"2026-09-25T18:00:00+00:00","run_id":"PPI","status":"AVAILABLE",
        "capability":"READY_PAPER_SPOT","raw":{"_discovery_source":"PPI_PRIMARY"},
    }
    byma={
        "ticker":"AAPL","instrument_type":"CEDEARS","market":"BYMA",
        "currency":None,"settlement":"A-24HS","source":"BYMA_PUBLIC_COMPLEMENTARY",
        "observed_at":"2026-09-25T21:00:00+00:00",
        "identity_evidence":{"market_explicit":True,"currency_explicit":False,"settlement_explicit":True},
        "description":"BYMA public row",
    }
    merged=catalog.complete_with_complement(primary,byma)
    assert merged["currency"]=="ARS"
    assert merged["description"]=="Apple CEDEAR PPI"
    assert merged["last_seen_at"]=="2026-09-25T18:00:00+00:00"
    assert merged["raw"]["_freshness_by_source"]["BYMA_PUBLIC_COMPLEMENTARY"]==byma["observed_at"]


def test_iol_contract_wins_and_later_byma_contract_cannot_replace_it():
    primary=_primary()
    iol=_complement()
    after_iol=catalog.complete_with_complement(primary,iol)
    byma=dict(iol)
    byma["source"]="BYMA_PUBLIC_COMPLEMENTARY"
    byma["observed_at"]="2026-09-25T21:01:00+00:00"
    byma["financial_contract_v17"]=dict(iol["financial_contract_v17"],cash_multiplier="999")
    after_byma=catalog.complete_with_complement(after_iol,byma)
    assert after_byma["raw"]["financial_contract_v17"]["cash_multiplier"]=="0.01"
    assert after_byma["raw"]["_contract_complement_source"]=="IOL_COMPLEMENTARY"
    assert "BYMA_PUBLIC_COMPLEMENTARY" in after_byma["raw"]["_ignored_lower_priority_contract_sources"]


def test_explicit_lower_priority_identity_conflict_is_rejected():
    primary={
        "ticker":"AAPL","instrument_type":"CEDEARS","market":"BYMA","currency":"ARS",
        "settlement":"A-24HS","settlement_source":"PPI_FIELD","description":"PPI",
        "last_seen_at":"2026-09-25T18:00:00+00:00","run_id":"PPI","status":"AVAILABLE",
        "capability":"READY_PAPER_SPOT","raw":{"_discovery_source":"PPI_PRIMARY"},
    }
    iol={
        "ticker":"AAPL","instrument_type":"CEDEARS","market":"BYMA","currency":"USD_CCL",
        "settlement":"A-24HS","source":"IOL_COMPLEMENTARY",
        "observed_at":"2026-09-25T21:00:00+00:00",
        "identity_evidence":{"market_explicit":True,"currency_explicit":True,"settlement_explicit":True},
    }
    merged=catalog.complete_with_complement(primary,iol)
    assert merged==primary
