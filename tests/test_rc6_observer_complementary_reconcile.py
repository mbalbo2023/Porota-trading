import json
from datetime import datetime, timezone

import bf_production_paper_observer as observer
import bu_instrument_catalog as catalog
from be_paper_engine import PaperStore


def _fresh():
    return datetime.now(timezone.utc).isoformat()


def _store(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    return store


def test_reconcile_matches_legacy_on_alias_without_discovery_marker(tmp_path, monkeypatch):
    store = _store(tmp_path)
    with store.connect() as c:
        c.execute("""INSERT INTO financial_instrument_catalog
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
            "YMC1O","ON","BYMA","ARS","A-24HS","PPI_FIELD","ON",
            "2026-09-01T00:00:00+00:00","legacy","STALE",
            "NEEDS_NOMINAL_UNITS",json.dumps({}),
        ))
    comp = {
        "ticker":"YMC1O","instrument_type":"OBLIGACIONES","market":"BYMA",
        "currency":"ARS","settlement":"A-24HS","source":"IOL_COMPLEMENTARY",
        "observed_at":_fresh(),
        "financial_contract_v17":{
            "family":"OBLIGACIONES","currency":"ARS","market":"BYMA",
            "settlement":"A-24HS","cash_multiplier":"0.01",
            "quantity_step":"100",
            "metadata_source":"IOL_EXACT_FIXED_CONTRACT",
        },
    }
    monkeypatch.setattr(observer, "complementary_discovery", lambda _root: [comp])
    assert observer._reconcile_complementary_catalog(store) == 1
    with store.connect() as c:
        row=c.execute("""SELECT status,capability,metadata_json FROM financial_instrument_catalog
                         WHERE ticker='YMC1O' AND instrument_type='ON'""").fetchone()
        candidate=c.execute("""SELECT can_simulate,status,detail FROM candidate_universe
                               WHERE ticker='YMC1O' AND instrument_type='ON'""").fetchone()
    assert tuple(row[:2]) == ("AVAILABLE","READY_PAPER_SPOT")
    meta=json.loads(row[2])
    assert meta["_contract_complement_source"]=="IOL_COMPLEMENTARY"
    assert tuple(candidate[:2]) == (1,"AVAILABLE")


def test_reconcile_revives_legacy_spot_from_exact_fresh_observation(tmp_path, monkeypatch):
    store = _store(tmp_path)
    with store.connect() as c:
        c.execute("""INSERT INTO financial_instrument_catalog
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
            "SPY","CEDEARS","BYMA","ARS","A-24HS","PPI_FIELD","SPY",
            "2026-09-01T00:00:00+00:00","legacy","STALE",
            "HISTORY_UNAVAILABLE_PPI",json.dumps({}),
        ))
    comp={
        "ticker":"SPY","instrument_type":"CEDEARS","market":"BYMA",
        "currency":"ARS","settlement":"A-24HS","source":"IOL_COMPLEMENTARY",
        "observed_at":_fresh(),
    }
    monkeypatch.setattr(observer, "complementary_discovery", lambda _root: [comp])
    assert observer._reconcile_complementary_catalog(store) == 1
    with store.connect() as c:
        row=c.execute("""SELECT status,capability FROM financial_instrument_catalog
                         WHERE ticker='SPY'""").fetchone()
    assert tuple(row)==("AVAILABLE","READY_PAPER_SPOT")


def test_complement_only_byma_etf_can_enter_only_when_normalized_paper_ready(tmp_path, monkeypatch):
    store = _store(tmp_path)
    comp={
        "ticker":"ETFTEST","instrument_type":"ETF","market":"BYMA",
        "currency":"ARS","settlement":"A-24HS","source":"IOL_COMPLEMENTARY",
        "observed_at":_fresh(),"description":"ETF Test",
    }
    monkeypatch.setattr(observer, "complementary_discovery", lambda _root: [comp])
    assert observer._reconcile_complementary_catalog(store) == 1
    with store.connect() as c:
        row=c.execute("""SELECT instrument_type,status,capability FROM financial_instrument_catalog
                         WHERE ticker='ETFTEST'""").fetchone()
    assert tuple(row)==("ETFS","AVAILABLE","READY_PAPER_SPOT")


def test_complement_only_non_ready_family_is_not_inserted(tmp_path, monkeypatch):
    store = _store(tmp_path)
    comp={
        "ticker":"OPTTEST","instrument_type":"OPCIONES","market":"BYMA",
        "currency":"ARS","settlement":"INMEDIATA","source":"IOL_COMPLEMENTARY",
        "observed_at":_fresh(),
    }
    monkeypatch.setattr(observer, "complementary_discovery", lambda _root: [comp])
    assert observer._reconcile_complementary_catalog(store) == 0
    with store.connect() as c:
        assert c.execute("""SELECT COUNT(*) FROM financial_instrument_catalog
                            WHERE ticker='OPTTEST'""").fetchone()[0] == 0


def test_observer_applies_iol_then_byma_without_overwriting_ppi_identity(tmp_path, monkeypatch):
    store=_store(tmp_path)
    ppi_last="2026-09-25T18:00:00+00:00"
    with store.connect() as db:
        db.execute("""INSERT INTO financial_instrument_catalog
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
            "AAPL","CEDEARS","BYMA","ARS","A-24HS","PPI_FIELD","Apple PPI",
            ppi_last,"PPI-RUN","STALE","HISTORY_UNAVAILABLE_PPI",
            json.dumps({"_discovery_source":"PPI_PRIMARY"}),
        ))
    iol={
        "ticker":"AAPL","instrument_type":"CEDEARS","market":"BYMA","currency":"ARS",
        "settlement":"A-24HS","source":"IOL_COMPLEMENTARY",
        "source_channel":"IOL_SHADOW","observed_at":_fresh(),
        "identity_evidence":{"market_explicit":True,"currency_explicit":True,"settlement_explicit":True},
    }
    byma={
        "ticker":"AAPL","instrument_type":"CEDEARS","market":"BYMA","currency":None,
        "settlement":"A-24HS","source":"BYMA_PUBLIC_COMPLEMENTARY",
        "source_channel":"BYMA_PUBLIC","observed_at":_fresh(),
        "identity_evidence":{"market_explicit":True,"currency_explicit":False,"settlement_explicit":True},
    }
    monkeypatch.setattr(observer,"complementary_discovery",lambda _root:[iol,byma])
    assert observer._reconcile_complementary_catalog(store)==1
    with store.connect() as db:
        row=dict(db.execute("""SELECT * FROM financial_instrument_catalog
                              WHERE ticker='AAPL' AND instrument_type='CEDEARS'""").fetchone())
    meta=json.loads(row["metadata_json"])
    assert row["currency"]=="ARS"
    assert row["settlement"]=="A-24HS"
    assert row["description"]=="Apple PPI"
    assert row["last_seen_at"]==ppi_last
    assert row["run_id"]=="PPI-RUN"
    assert row["status"]=="AVAILABLE"
    assert row["capability"]=="READY_PAPER_SPOT"
    assert meta["_source_precedence"]=="PPI_PRIMARY>IOL_COMPLEMENTARY>BYMA_PUBLIC_COMPLEMENTARY"
    assert meta["_applied_complement_sources"]==["IOL_COMPLEMENTARY","BYMA_PUBLIC_COMPLEMENTARY"]


def test_missing_contract_is_recorded_and_retried_when_no_complement_arrives(tmp_path, monkeypatch):
    store = _store(tmp_path)
    with store.connect() as c:
        c.execute("""INSERT INTO financial_instrument_catalog
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
            "GD30", "BONOS", "BYMA", "ARS", "A-24HS", "PPI_FIELD", "GD30",
            "2026-09-25T18:00:00+00:00", "PPI-RUN", "STALE",
            "NEEDS_NOMINAL_UNITS", json.dumps({"_discovery_source": "PPI_PRIMARY"}),
        ))
    monkeypatch.setattr(observer, "complementary_discovery", lambda _root: [])
    assert observer._reconcile_complementary_catalog(store) == 0
    with store.connect() as c:
        row = c.execute("""SELECT state,reason,attempts
          FROM complementary_contract_retry WHERE ticker='GD30'""").fetchone()
    assert tuple(row) == ("PENDING_RETRY", "NEEDS_NOMINAL_UNITS", 1)

    assert observer._reconcile_complementary_catalog(store) == 0
    with store.connect() as c:
        attempts = c.execute("""SELECT attempts FROM complementary_contract_retry
          WHERE ticker='GD30'""").fetchone()[0]
    assert attempts == 2


def test_ambiguous_complement_is_kept_as_special_retry_without_mutation(tmp_path, monkeypatch):
    store = _store(tmp_path)
    rows = [
        ("YMCIO", "OBLIGACIONES", "OBSERVED_SHADOW"),
        ("YMCIO", "ON", "STALE"),
    ]
    with store.connect() as c:
        for ticker, family, status in rows:
            c.execute("""INSERT INTO financial_instrument_catalog
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
                ticker, family, "BYMA", "ARS", "A-24HS", "PPI_FIELD", ticker,
                "2026-09-25T18:00:00+00:00", "PPI-RUN", status,
                "NEEDS_NOMINAL_UNITS", json.dumps({"_discovery_source": "PPI_PRIMARY"}),
            ))
    comp = {
        "ticker": "YMCIO", "instrument_type": "OBLIGACIONES", "market": "BYMA",
        "currency": "ARS", "settlement": "A-24HS", "source": "IOL_COMPLEMENTARY",
        "observed_at": _fresh(),
    }
    monkeypatch.setattr(observer, "complementary_discovery", lambda _root: [comp])
    assert observer._reconcile_complementary_catalog(store) == 0
    with store.connect() as c:
        pending = c.execute("""SELECT instrument_type,state,reason,attempts
          FROM complementary_contract_retry
          WHERE ticker='YMCIO' AND source='IOL_COMPLEMENTARY'
          ORDER BY instrument_type""").fetchall()
        unchanged = c.execute("""SELECT instrument_type,status,capability
          FROM financial_instrument_catalog WHERE ticker='YMCIO'
          ORDER BY instrument_type""").fetchall()
    assert [tuple(row) for row in pending] == [
        ("OBLIGACIONES", "PENDING_SPECIAL", "COMPLEMENTARY_IDENTITY_AMBIGUOUS:matches=2", 1),
        ("ON", "PENDING_SPECIAL", "COMPLEMENTARY_IDENTITY_AMBIGUOUS:matches=2", 1),
    ]
    assert [tuple(row) for row in unchanged] == [
        ("OBLIGACIONES", "OBSERVED_SHADOW", "NEEDS_NOMINAL_UNITS"),
        ("ON", "STALE", "NEEDS_NOMINAL_UNITS"),
    ]
