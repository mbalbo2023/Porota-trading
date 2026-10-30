
import json
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

import bf_production_paper_observer as observer
import bu_instrument_catalog as catalog
import cr_contract_evidence_v2_mass_hf6 as mass
import dj_caucion_live_ppi_rc6 as live
import rc6_contract_bridge as bridge
from be_paper_engine import PaperStore
from bt_caucion_paper import automatic_liquidity_cap
from di_caucion_cash_sweep_runtime_hf6 import offers_from_store

TZ=ZoneInfo("America/Argentina/Buenos_Aires")
NOW=datetime(2026,10,2,16,59,57,tzinfo=TZ)

def _store(tmp_path, *, ticker="PESOS7", at=NOW):
    store=PaperStore(str(tmp_path/"observer.db"))
    observer._support_schema(store)
    raw={"ticker":ticker,"description":ticker,"currency":"Pesos",
         "type":"CAUCIONES","market":"BYMA","nominalInPrice":1,
         "_provider_instrument_type":"CAUCIONES","_discovery_source":"PPI_PRIMARY"}
    record=catalog.normalize_record(raw,"INMEDIATA",at.isoformat(),"test")
    with store.connect() as c:
        catalog.persist(c,record)
    return store


def test_live_collector_uses_same_bounded_terms_as_ppi_discovery(tmp_path, monkeypatch):
    # The primary PPI catalogue can contain an exact non-default tenor after
    # its official, read-only discovery configuration is expanded. The live
    # collector must not silently drop that already-verified identity.
    store = _store(tmp_path, ticker="PESOS3")
    monkeypatch.setenv("PPI_CAUCION_DISCOVERY_DAYS", "1,2,3")
    rows = live._catalog(store)
    assert [(row["ticker"], row["term_days"]) for row in rows] == [("PESOS3", 3)]

    # A narrower configuration remains a hard limit; changing the catalogue
    # reader never grants a trading/settlement permission by itself.
    monkeypatch.setenv("PPI_CAUCION_DISCOVERY_DAYS", "1,2")
    assert live._catalog(store) == []

    monkeypatch.setenv("PPI_CAUCION_DISCOVERY_DAYS", "3,366")
    with pytest.raises(ValueError, match="PPI_CAUCION_DISCOVERY_DAYS_OUT_OF_RANGE"):
        live._catalog(store)


def test_mass_policy_marks_exact_ars_caucion_static_ready(tmp_path):
    store=_store(tmp_path)
    result=mass.collect(store,run_id="caucion-static")
    assert result["real_routes_used"]==[]
    complements=bridge.complements_from_store(store,now=NOW)
    claim=next(x for x in complements if x["ticker"]=="PESOS7")
    assert claim["paper_caucion_contract_v1"]["paper_fill_policy"]=="LIVE_PPI_BID_PARTICIPATION_CAP"
    primary=None
    with store.connect() as c:
        row=c.execute("select * from financial_instrument_catalog where ticker='PESOS7'").fetchone()
        primary=dict(row); primary["raw"]=json.loads(primary.pop("metadata_json"))
    completed=catalog.complete_with_complement(primary,claim)
    assert completed["capability"]=="READY_PAPER_CAUCION_PLACING"

def test_live_book_is_separate_dynamic_state_and_participation_is_conservative(tmp_path,monkeypatch):
    store=_store(tmp_path)
    mass.collect(store,run_id="caucion-static")
    for claim in bridge.complements_from_store(store,now=NOW):
        with store.connect() as c:
            rows=[dict(r) for r in c.execute(
                "select * from financial_instrument_catalog where ticker=?",(claim["ticker"],))]
        for primary in rows:
            primary["raw"]=json.loads(primary.pop("metadata_json"))
            completed=catalog.complete_with_complement(primary,claim)
            with store.connect() as c: catalog.persist(c,completed)

    class Reader:
        def book(self,ticker,kind,settlement):
            assert (ticker,kind,settlement)==("PESOS7","CAUCIONES","INMEDIATA")
            return {"date":NOW.isoformat(),
                    "bids":[{"price":19.1,"quantity":30000000}],"offers":[]}
    monkeypatch.setattr(live,"collection_window",lambda at:True)
    result=live.refresh(Reader(),store,now=NOW)
    assert result["ready"]==1 and result["real_routes"]==[]
    with store.connect() as c:
        assert c.execute("select count(*) from paper_caucion_live_book").fetchone()[0]==1
        # Dynamic quote must never enter static Contract Evidence v2.
        payloads=[r[0] for r in c.execute(
            "select evidence_json from contract_evidence_v2_snapshots where family='CAUCIONES'")]
        assert all('"annual_rate_fraction"' not in p for p in payloads)
    offers,errors=offers_from_store(store,now=NOW)
    assert not errors and len(offers)==1
    offer=offers[0]
    assert offer.annual_rate_fraction==Decimal("0.191")
    assert offer.available_principal==Decimal("30000000")
    assert automatic_liquidity_cap(offer,Decimal("0.10"))==Decimal("3000000")
    assert offer.fee_authority
    assert offer.paper_fill_policy=="LIVE_PPI_BID_PARTICIPATION_CAP"

def test_no_placing_bid_never_fabricates_rate_or_depth(tmp_path,monkeypatch):
    store=_store(tmp_path)
    class Reader:
        def book(self,*_):
            return {"date":NOW.isoformat(),"bids":[],"offers":[{"price":20,"quantity":999999}]}
    monkeypatch.setattr(live,"collection_window",lambda at:True)
    result=live.refresh(Reader(),store,now=NOW)
    assert result["ready"]==0
    with store.connect() as c:
        row=c.execute("select state,tna_fraction,available_principal from paper_caucion_live_book").fetchone()
    assert tuple(row)==("NO_PLACED_BID",None,None)

def test_live_collector_has_no_client_constructor_or_order_route():
    from pathlib import Path
    source=Path("dj_caucion_live_ppi_rc6.py").read_text(encoding="utf-8")
    assert "ProductionMarketReader(" not in source
    assert ".order" not in source.lower()
    assert ".budget" not in source.lower()
    assert "contract_evidence_v2" not in source


@pytest.mark.parametrize("ticker,at,expected", [
    ("PESOS1", datetime(2026, 10, 23, 16, 59, 57, tzinfo=TZ),
     "CAUCION_MATURITY_NON_SETTLEMENT_DAY"),  # Saturday 24 Oct
    ("PESOS7", datetime(2026, 10, 30, 16, 59, 57, tzinfo=TZ),
     "CAUCION_MATURITY_NON_SETTLEMENT_DAY"),  # 6 Nov: no settlement
    ("PESOS120", datetime(2026, 10, 27, 16, 59, 57, tzinfo=TZ),
     "CAUCION_MATURITY_CALENDAR_UNVERIFIED"),  # 2027 not audited
    ("PESOS7", datetime(2026, 10, 23, 16, 59, 57, tzinfo=TZ), None),
])
def test_theoretical_maturity_never_rolls_to_unverified_settlement(
        tmp_path, monkeypatch, ticker, at, expected):
    store = _store(tmp_path, ticker=ticker, at=at)
    mass.collect(store, run_id="caucion-maturity")
    for claim in bridge.complements_from_store(store, now=at):
        with store.connect() as c:
            records = [dict(r) for r in c.execute(
                "SELECT * FROM financial_instrument_catalog WHERE ticker=?", (claim["ticker"],))]
        for primary in records:
            primary["raw"] = json.loads(primary.pop("metadata_json"))
            with store.connect() as c:
                catalog.persist(c, catalog.complete_with_complement(primary, claim))

    class Reader:
        def book(self, symbol, family, settlement):
            assert (symbol, family, settlement) == (ticker, "CAUCIONES", "INMEDIATA")
            return {"date": at.isoformat(),
                    "bids": [{"price": 19.1, "quantity": 30_000_000}], "offers": []}

    monkeypatch.setattr(live, "collection_window", lambda _at: True)
    assert live.refresh(Reader(), store, now=at)["ready"] == 1
    offers, errors = offers_from_store(store, now=at)
    if expected:
        assert offers == []
        assert errors == [f"{ticker}:{expected}"]
    else:
        assert errors == [] and len(offers) == 1
        assert offers[0].maturity_at.startswith("2026-10-30T17:00:00")
        assert "THEORETICAL_MATURITY_BYMA_CALENDAR_CHECKED" in offers[0].metadata_source


def test_no_bid_does_not_create_theoretical_maturity_offer(tmp_path, monkeypatch):
    at = datetime(2026, 10, 23, 16, 59, 57, tzinfo=TZ)
    store = _store(tmp_path, ticker="PESOS7", at=at)

    class Reader:
        def book(self, *_args):
            return {"date": at.isoformat(), "bids": [],
                    "offers": [{"price": 30, "quantity": 40_000_000}]}

    monkeypatch.setattr(live, "collection_window", lambda _at: True)
    assert live.refresh(Reader(), store, now=at)["ready"] == 0
    offers, errors = offers_from_store(store, now=at)
    assert offers == []
    assert errors == ["PESOS7:NO_PLACED_BID"]


@pytest.mark.parametrize("rate", [None, 0])
def test_null_or_zero_live_rate_never_becomes_a_positive_caucion(tmp_path, monkeypatch, rate):
    at = datetime(2026, 10, 23, 16, 59, 57, tzinfo=TZ)
    store = _store(tmp_path, ticker="PESOS7", at=at)
    mass.collect(store, run_id="caucion-null-rate")
    for claim in bridge.complements_from_store(store, now=at):
        with store.connect() as c:
            row = dict(c.execute("SELECT * FROM financial_instrument_catalog WHERE ticker='PESOS7'").fetchone())
        row["raw"] = json.loads(row.pop("metadata_json"))
        with store.connect() as c:
            catalog.persist(c, catalog.complete_with_complement(row, claim))

    class Reader:
        def book(self, *_args):
            return {"date": at.isoformat(),
                    "bids": [{"price": rate, "quantity": 30_000_000}], "offers": []}

    monkeypatch.setattr(live, "collection_window", lambda _at: True)
    assert live.refresh(Reader(), store, now=at)["ready"] == 0
    offers, errors = offers_from_store(store, now=at)
    assert offers == [] and len(errors) == 1
