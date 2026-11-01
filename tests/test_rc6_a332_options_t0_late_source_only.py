"""A332: verified BYMA options T+0 quote observation; never PAPER admission."""
from contextlib import contextmanager
from datetime import datetime
import json
import sqlite3
from zoneinfo import ZoneInfo

from be_paper_engine import PaperStore
from be_paper_engine import Quote
from decimal import Decimal
import bf_production_paper_observer as observer
from iol_shadow_collector_rc6 import is_verified_late_contract_window


ART = ZoneInfo("America/Argentina/Buenos_Aires")


def clock(value):
    return datetime.fromisoformat(value).astimezone(ART)


NOW = clock("2026-11-02T17:30:00-03:00")


def option_metadata(expiry="2026-12-18T15:30:00-03:00", *, discovery="PPI_PRIMARY"):
    return {"_discovery_source": discovery,
            "financial_contract_v17": {
                "currency": "ARS", "market": "BYMA", "settlement": "INMEDIATA",
                "cash_multiplier": "100", "quantity_step": "1",
                "metadata_source": "PPI_PRIMARY", "expires_at": expiry,
                "underlying": "GGAL", "strike": "100", "option_right": "CALL",
            }}


def make_store(tmp_path):
    store=PaperStore(str(tmp_path / "options_t0.db"))
    observer._support_schema(store)
    with store.connect() as conn:
        conn.execute("""CREATE TABLE candidate_identity_v2(
            ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,
            settlement TEXT,can_simulate INTEGER,status TEXT,detail TEXT,checked_at TEXT)""")
    return store


def add_catalog(store,symbol,*,family="OPCIONES",currency="ARS",settlement="INMEDIATA",
                settlement_source="PPI_FIELD",expiry="2026-12-18T15:30:00-03:00",
                discovery="PPI_PRIMARY",candidate=True,capability=None):
    meta=option_metadata(expiry,discovery=discovery)
    if family == "ACCIONES":
        meta={"_discovery_source":"PPI_PRIMARY"}
    if family == "OPCIONES" and expiry is None:
        meta.pop("financial_contract_v17",None)
    cap=capability or ("READY_PAPER_OPTION_LONG" if family=="OPCIONES" else "READY_PAPER_SPOT")
    with store.connect() as conn:
        conn.execute("""INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (symbol,family,"BYMA",currency,settlement,settlement_source,
             symbol,NOW.isoformat(),"a332","AVAILABLE",cap,json.dumps(meta)))
        conn.execute("""INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?,?,?)""",
            (symbol,family,"BYMA",currency,settlement,int(candidate),
             "AVAILABLE","PIT",NOW.isoformat()))


def test_only_verified_future_expiry_options_t0_are_source_candidates(tmp_path):
    store=make_store(tmp_path)
    add_catalog(store,"VALID1")
    add_catalog(store,"VALID2")
    add_catalog(store,"VALID3")
    add_catalog(store,"VALID4")
    add_catalog(store,"EXPIRY_TODAY",expiry="2026-11-02T15:30:00-03:00")
    add_catalog(store,"EXPIRY_YESTERDAY",expiry="2026-10-30T15:30:00-03:00")
    add_catalog(store,"EXPIRY_UNKNOWN",expiry=None)
    add_catalog(store,"NAIVE_EXPIRY",expiry="2026-12-18T15:30:00")
    add_catalog(store,"WRONG_TERM",settlement="A-24HS")
    add_catalog(store,"SOURCE_UNKNOWN",settlement_source="REQUEST_CANDIDATE")
    add_catalog(store,"UNTRUSTED_CHAIN",discovery="IOL_COMPLEMENTARY")
    add_catalog(store,"GATE_DISABLED",candidate=False)
    add_catalog(store,"DUPLICATE",currency="ARS")
    add_catalog(store,"DUPLICATE",currency="USD")
    at_1730=clock("2026-11-02T17:30:00-03:00")
    first=observer._option_t0_late_readonly_identities(store,at_1730,limit=2)
    second=observer._option_t0_late_readonly_identities(
        store,clock("2026-11-02T17:31:00-03:00"),limit=2)
    assert len(first)==len(second)==2
    assert {x[0] for x in first+second}=={"VALID1","VALID2","VALID3","VALID4"}
    assert all(x[1:]==("OPCIONES","BYMA","ARS","INMEDIATA") for x in first+second)
    assert observer._option_t0_late_readonly_identities(store,at_1730,limit=0)==[]
    assert observer._option_t0_late_readonly_identities(store,clock("2026-10-30T17:30:00-03:00"))==[]
    assert observer._option_t0_late_readonly_identities(store,clock("2026-11-02T18:00:00-03:00"))==[]
    assert observer._option_t0_late_readonly_identities(store,clock("2026-11-07T17:30:00-03:00"))==[]


def test_late_ppi_option_t0_book_is_readonly_with_exact_wire_and_budget(tmp_path,monkeypatch):
    store=make_store(tmp_path)
    add_catalog(store,"GFGC100OC")
    add_catalog(store,"GGAL",family="ACCIONES",settlement="A-24HS")
    monkeypatch.setattr(observer,"now_iso",lambda: NOW.isoformat())
    class Reader:
        def __init__(self): self.calls=[]; self.scope=[]
        @contextmanager
        def read_scope(self,*,priority,identity):
            self.scope.append((priority,identity))
            yield
        def current(self,ticker,family,settlement):
            self.calls.append(("Current",ticker,family,settlement))
            return {"date":NOW.isoformat(),"price":4.8}
        def book(self,ticker,family,settlement):
            self.calls.append(("Book",ticker,family,settlement))
            return {"date":NOW.isoformat(),
                    "bids":[{"position":1,"price":4.7,"quantity":20}],
                    "offers":[{"position":1,"price":4.9,"quantity":20}]}
    reader=Reader()
    result=observer._equity_t1_readonly_tick(reader,store,now=NOW,clock_fn=lambda:NOW)
    assert result["state"]=="READONLY_T1"
    assert (result["identity_count"],result["option_t0_source_only"],
            result["read"],result["failed"],result["real_routes"])==(2,1,2,0,[])
    option_identity=("GFGC100OC","OPCIONES","BYMA","ARS","INMEDIATA")
    assert ("WARM",option_identity) in reader.scope
    assert ("Current","GFGC100OC","OPCIONES","INMEDIATA") in reader.calls
    assert ("Book","GFGC100OC","OPCIONES","INMEDIATA") in reader.calls
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]==2
        assert conn.execute("SELECT COUNT(*) FROM paper_decisions").fetchone()[0]==0
        assert conn.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0]==0


def test_same_day_expiry_and_iol_t1_mismatch_cannot_inherit_late_authority(tmp_path):
    store=make_store(tmp_path)
    add_catalog(store,"SAME_DAY",expiry="2026-11-02T23:59:00-03:00")
    assert observer._option_t0_late_readonly_identities(store,NOW)==[]
    assert not is_verified_late_contract_window(NOW,
        family="OPCIONES",market="BYMA",settlement="INMEDIATA",
        settlement_source="PPI_FIELD")
    # This documents an independent IOL term=t1 gap; A332 does not broaden
    # IOL's source contract or call it for a T+0 options premium.


def test_option_quote_monetary_mismatch_and_stale_book_fail_closed(tmp_path,monkeypatch):
    store=make_store(tmp_path)
    add_catalog(store,"GFGC100OC")
    class Reader:
        current=book=lambda *_:None
    def malformed(_reader,_store,symbol,family,settlement,*,priority,identity):
        return Quote(symbol=symbol,asset_class=family,settlement=settlement,
            last=Decimal("5"),bid=Decimal("4.8"),ask=Decimal("5.1"),
            bid_size=Decimal("10"),ask_size=Decimal("10"),
            observed_at=NOW.isoformat(),book_at=NOW.isoformat(),
            currency="USD",market="BYMA",metadata_source="PPI_PRIMARY")
    monkeypatch.setattr(observer,"_read_scanner_quote",malformed)
    mismatched=observer._equity_t1_readonly_tick(Reader(),store,now=NOW,clock_fn=lambda:NOW)
    assert (mismatched["read"],mismatched["failed"],mismatched["real_routes"])==(0,1,[])
    assert "EXACT_SOURCE_IDENTITY_MISMATCH" in mismatched["errors"]["GFGC100OC"]
    def stale(_reader,_store,symbol,family,settlement,*,priority,identity):
        return Quote(symbol=symbol,asset_class=family,settlement=settlement,
            last=Decimal("5"),bid=Decimal("4.8"),ask=Decimal("5.1"),
            bid_size=Decimal("10"),ask_size=Decimal("10"),
            observed_at=NOW.isoformat(),book_at="2026-11-02T17:20:00-03:00",
            currency="ARS",market="BYMA",metadata_source="PPI_PRIMARY")
    monkeypatch.setattr(observer,"_read_scanner_quote",stale)
    rejected=observer._equity_t1_readonly_tick(Reader(),store,now=NOW,clock_fn=lambda:NOW)
    assert (rejected["read"],rejected["failed"],rejected["real_routes"])==(0,1,[])
    assert "BOOK_STALE" in rejected["errors"]["GFGC100OC"]
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]==0
        assert conn.execute("SELECT COUNT(*) FROM paper_decisions").fetchone()[0]==0
