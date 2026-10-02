
import json
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

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

def _store(tmp_path):
    store=PaperStore(str(tmp_path/"observer.db"))
    observer._support_schema(store)
    raw={"ticker":"PESOS7","description":"PESOS 7","currency":"Pesos",
         "type":"CAUCIONES","market":"BYMA","nominalInPrice":1,
         "_provider_instrument_type":"CAUCIONES","_discovery_source":"PPI_PRIMARY"}
    record=catalog.normalize_record(raw,"INMEDIATA",NOW.isoformat(),"test")
    with store.connect() as c:
        catalog.persist(c,record)
    return store

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
