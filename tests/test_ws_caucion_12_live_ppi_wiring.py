
import json
from contextlib import nullcontext
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest
import requests
from ppi_client.ppi import PPI

import bf_production_paper_observer as observer
import bu_instrument_catalog as catalog
import cr_contract_evidence_v2_mass_hf6 as mass
import dj_caucion_live_ppi_rc6 as live
import rc6_contract_bridge as bridge
import rc6_paper_admission_gate as admission
from bd_ppi_readonly_guard import ProductionMarketReader
from be_paper_engine import PaperBroker, PaperStore
from bt_caucion_paper import automatic_liquidity_cap
from cp_contract_evidence_v2_hf6 import record_snapshots
from di_caucion_cash_sweep_runtime_hf6 import (
    obligation_snapshot_from_ledger,
    offers_from_store,
    paper_schedule,
    run_paper_sweep,
)

TZ=ZoneInfo("America/Argentina/Buenos_Aires")
NOW=datetime(2026,10,2,16,59,57,tzinfo=TZ)

def _store(tmp_path, *, ticker="PESOS7", at=NOW):
    store=PaperStore(str(tmp_path/"observer.db"))
    observer._support_schema(store)
    raw={"ticker":ticker,"description":ticker,"currency":"Pesos",
         "settlement":"INMEDIATA",
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


@pytest.mark.parametrize("returns_native_settlement", [False, True])
def test_caucion_sdk_public_shape_cannot_substitute_request_for_native_settlement(
        tmp_path, monkeypatch, returns_native_settlement):
    # Public REST 1.0 response shapes with invented test values. Only the
    # positive control adds a literal to SearchInstrument's documented shape.
    at = datetime(2026, 11, 24, 16, 40, tzinfo=TZ)
    stamp = "2026-11-24T19:39:59.875Z"
    search_row = {"ticker": "PESOS1", "description": "1 DÍA", "currency": "Pesos",
                  "type": "CAUCIONES", "market": "BYMA"}
    current = {"date": stamp, "price": 18.9, "volume": 7_500_000,
               "openingPrice": 18.7, "max": 19.25, "min": 18.5}
    book = {"date": stamp, "bids": [{"position": 1, "price": 19.1, "quantity": 6_000_000}],
            "offers": [{"position": 1, "price": 19.3, "quantity": 7_000_000}]}
    calls = []

    def send(_adapter, request, **_kwargs):
        uri = urlsplit(request.url)
        endpoint = uri.path.rsplit("/", 1)[-1]
        query = parse_qs(uri.query)
        calls.append((endpoint, query.get("ticker")))
        assert request.method == "GET" and uri.path.startswith("/api/1.0/MarketData/")
        assert query["type"] == ["CAUCIONES"]
        if endpoint == "SearchInstrument":
            assert query["market"] == ["BYMA"] and query["name"] == ["1"]
            assert "settlement" not in query
            payload = [search_row] if query["ticker"] == ["PESOS1"] else []
            if payload and returns_native_settlement:
                payload = [search_row, dict(search_row, settlement="INMEDIATA")]
        else:
            assert query["ticker"] == ["PESOS1"] and query["settlement"] == ["INMEDIATA"]
            payload = {"Current": current, "Book": book}[endpoint]
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(payload).encode()
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    monkeypatch.setenv("PPI_CAUCION_DISCOVERY_DAYS", "1")
    monkeypatch.setattr(observer, "CATALOG_QUERY_SLEEP_SECONDS", 0)
    monkeypatch.setattr(observer, "_candidate_universe", lambda: [
        ("CAUCION", "CAUCIONES", "INMEDIATA", "BYMA", True, "CAUCIONES"),
    ])
    monkeypatch.setattr(observer, "complementary_discovery", lambda _root: [])
    monkeypatch.setattr(observer, "now_iso", lambda: at.isoformat())
    complements_from_store = bridge.complements_from_store
    monkeypatch.setattr(bridge, "complements_from_store", lambda store: complements_from_store(store, now=at))
    budget = SimpleNamespace(acquire=lambda *_a, **_k: {"allowed": True, "lease": "FIXTURE"},
                             start=lambda _lease: None, finish=lambda *_a, **_k: None,
                             wire_scope=lambda _lease: nullcontext())
    reader = ProductionMarketReader("TEST_KEY", "TEST_SECRET", budget=budget, consumer="SCANNER")
    # Inject an SDK behind the local transport. No authentication request.
    reader._ProductionMarketReader__client = PPI(sandbox=False)
    reader._ProductionMarketReader__authenticated = True
    monkeypatch.setattr(reader, "market_configuration", lambda: {
        "instrument_types": ["CAUCIONES"], "markets": ["BYMA"], "settlements": ["INMEDIATA"],
        "quantity_types": ["MONTO"], "operation_terms": ["INMEDIATA"],
        "operation_types": ["COLOCADORA"], "operations": ["COMPRA"],
    })
    store = PaperStore(str(tmp_path / "observer.db"))
    observer._support_schema(store)
    try:
        assert observer._download_catalog(reader, store) == 1
        with reader.read_scope(identity=("PESOS1", "CAUCIONES", "BYMA", "ARS", "INMEDIATA")):
            assert reader.current("PESOS1", "CAUCIONES", "INMEDIATA") == current
        assert live.refresh(reader, store, now=at, clock_fn=lambda: at)["states"] == {"PESOS1": "BOOK_READY"}
    finally:
        reader.close()

    assert calls == [("SearchInstrument", ["PESOS1"]), ("SearchInstrument", ["DOLAR1"]),
                     ("Current", ["PESOS1"]), ("Book", ["PESOS1"])]
    record = catalog.lookup(store, "PESOS1", "CAUCIONES", "INMEDIATA", market="BYMA", currency="ARS")
    offers, errors = offers_from_store(store, now=at)
    if returns_native_settlement:
        assert (record["settlement_source"], record["capability"]) == ("PPI_FIELD", "READY_PAPER_CAUCION_PLACING")
        assert not errors and len(offers) == 1
        assert offers[0].annual_rate_fraction == Decimal("0.191")
    else:
        assert (record["settlement_source"], record["capability"]) == ("REQUEST_CANDIDATE", "NEEDS_CAUCION_TERMS")
        assert record["_admission_reason"] == "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED"
        assert offers == [] and errors == ["PESOS1:CAUCION_STATIC_POLICY_NOT_READY"]


@pytest.mark.parametrize(("policy", "hold"), [
    ("OLD_STATIC", None),
    ("CURRENT_STATIC", None),
    ("OLD_QUOTE", "CAUCION_STATIC_POLICY_NOT_READY"),
    ("MISSING_POLICY", "CAUCION_STATIC_POLICY_NOT_READY"),
    ("REQUEST_ONLY", "CAUCION_STATIC_POLICY_NOT_READY"),
    ("MATERIAL_CHANGE", "CHANGED_REVIEW_REQUIRED"),
    ("WRONG_TERM", "CAUCION_STATIC_POLICY_NOT_READY"),
    ("FUTURE_POLICY", "CAUCION_STATIC_POLICY_NOT_READY"),
])
def test_caucion_reconcile_and_sweep_keep_static_terms_separate_from_live_bid(
        tmp_path, monkeypatch, policy, hold):
    # The same exact PPI identity is observed this session. Its contract can
    # already exist from a prior catalogue read. These responses are synthetic.
    at = datetime(2026, 11, 24, 16, 40, tzinfo=TZ)
    seen = at - (timedelta(minutes=2) if policy == "CURRENT_STATIC" else timedelta(days=3))
    store = _store(tmp_path, ticker="PESOS1", at=seen)
    if policy != "MISSING_POLICY":
        records, skipped, identities = mass.planned_records(mass._catalog_rows(store))
        assert not skipped and identities == {"CAUCIONES": 1}
        if policy == "OLD_QUOTE":
            records[-1]["evidence"].update(annual_rate_fraction="0.50", quoted_at=seen.isoformat())
        elif policy == "WRONG_TERM":
            records[-1]["evidence"]["term_days"] = "7"
        elif policy == "FUTURE_POLICY":
            records[-1]["effective_at"] = (at + timedelta(days=1)).isoformat()
        record_snapshots(store, records)
        if policy == "MATERIAL_CHANGE":
            records[-1]["evidence"]["minimum_principal"] = "150000"
            record_snapshots(store, records[-1:])

    with store.connect() as connection:
        row = dict(connection.execute(
            "SELECT metadata_json FROM financial_instrument_catalog WHERE ticker='PESOS1'"
        ).fetchone())
        raw = json.loads(row["metadata_json"])
        if policy == "REQUEST_ONLY":
            del raw["settlement"]
        catalog.persist(connection, catalog.normalize_record(raw, "INMEDIATA", at.isoformat(), "refresh"))
    if policy == "REQUEST_ONLY":
        assert mass.collect(store, run_id="request-only")["skipped"] == {
            "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED": 1,
        }

    monkeypatch.setenv("POROTA_MARKET_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("PPI_CAUCION_DISCOVERY_DAYS", "1")
    monkeypatch.setattr(observer, "now_iso", lambda: at.isoformat())
    complements = bridge.complements_from_store
    monkeypatch.setattr(bridge, "complements_from_store", lambda store: complements(store, now=at))
    is_fresh = catalog.complementary_is_fresh
    monkeypatch.setattr(catalog, "complementary_is_fresh", lambda raw, **kw: is_fresh(raw, now=at, **kw))
    expected_ready = hold is None
    assert observer._reconcile_complementary_catalog(store) == int(expected_ready)
    record = catalog.lookup(store, "PESOS1", "CAUCIONES", "INMEDIATA", market="BYMA", currency="ARS")
    assert bool(record.get("_admission_reason")) != expected_ready
    if expected_ready:
        # A repeated receipt of the identical version advances the clock and
        # must not invent a contradiction in the materialized terms.
        assert not mass.collect(store, run_id="same-policy-new-observation")["first_seen"]
        assert observer._reconcile_complementary_catalog(store) == 0
        record = catalog.lookup(store, "PESOS1", "CAUCIONES", "INMEDIATA", market="BYMA", currency="ARS")
        assert not record.get("_admission_reason")
        assert record["raw"]["paper_caucion_contract_v1"]["field_provenance"]["term_days"]["observed_at"] == at.isoformat()

    class Reader:
        def book(self, ticker, family, settlement):
            assert (ticker, family, settlement) == ("PESOS1", "CAUCIONES", "INMEDIATA")
            return {"date": at.isoformat(), "bids": [{"price": "19.1", "quantity": "6000000"}]}

    assert live.refresh(Reader(), store, now=at, clock_fn=lambda: at)["states"] == {"PESOS1": "BOOK_READY"}
    offers, errors = offers_from_store(store, now=at)
    if hold:
        assert offers == [] and errors == [f"PESOS1:{hold}"]
        return

    assert not errors and len(offers) == 1
    assert offers[0].annual_rate_fraction == Decimal("0.191")
    assert offers[0].maturity_at == "2026-11-25T17:00:00-03:00"
    assert offers_from_store(store, now=at + timedelta(seconds=91)) == (
        [], ["PESOS1:CAUCION_LIVE_BOOK_STALE"],
    )

    # Only a synthetic local test receives a GO receipt. Runtime allocation
    # rechecks the catalog, exact candidate, book history, risk and cash.
    monkeypatch.setenv("PAPER_V17_DB_PATH", store.path)
    monkeypatch.setattr(admission, "admission_status", lambda **_: (True, ""))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    broker = PaperBroker(store, initial_cash="2000000")
    schedule = paper_schedule(at)
    snapshot = obligation_snapshot_from_ledger(store, observed_at=at, liquidity_deadline=schedule["liquidity_deadline"])
    request_id = "paper-caucion-cash-sweep:2026-11-24:ARS:v1"
    options = dict(obligation_snapshot=snapshot, currency="ARS", as_of=at,
                   request_id=request_id, **schedule)
    first = run_paper_sweep(broker, offers, **options)
    retry = run_paper_sweep(broker, offers, **options)
    assert first["status"] == retry["status"] == "PLACED_SIMULATED"
    assert first["allocation"]["paper_id"] == retry["allocation"]["paper_id"]
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_cauciones").fetchone()[0] == 1
        assert connection.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0] == 0


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
    result=live.refresh(Reader(),store,now=NOW,clock_fn=lambda:NOW)
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
    result=live.refresh(Reader(),store,now=NOW,clock_fn=lambda:NOW)
    assert result["ready"]==0
    with store.connect() as c:
        row=c.execute("select state,tna_fraction,available_principal from paper_caucion_live_book").fetchone()
    assert tuple(row)==("NO_PLACED_BID",None,None)


@pytest.mark.parametrize("native_identity", [
    {"ticker": "PESOS1"},
    {"type": "BONOS"},
    {"market": "A3"},
    {"currency": "USD"},
    {"settlement_used": "A-24HS"},
    {"identity": {"ticker": "PESOS1"}},
    {"instrument": {"currency": "USD"}},
])
def test_caucion_live_book_rejects_native_identity_conflicts(
        tmp_path, monkeypatch, native_identity):
    """A valid Book depth does not certify a different PPI instrument."""
    store = _store(tmp_path)

    class Reader:
        def book(self, ticker, kind, settlement):
            assert (ticker, kind, settlement) == ("PESOS7", "CAUCIONES", "INMEDIATA")
            return {
                "date": NOW.isoformat(),
                "bids": [{"price": "19.1", "quantity": "30000000"}],
                "offers": [],
                **native_identity,
            }

    monkeypatch.setattr(live, "collection_window", lambda at: True)
    result = live.refresh(Reader(), store, now=NOW, clock_fn=lambda: NOW)
    assert result["ready"] == 0
    assert result["states"] == {"PESOS7": "READ_ERROR:ValueError"}
    assert result["real_routes"] == []
    with store.connect() as conn:
        assert tuple(conn.execute(
            "SELECT state,tna_fraction,available_principal "
            "FROM paper_caucion_live_book WHERE ticker='PESOS7'"
        ).fetchone()) == ("READ_ERROR:ValueError", None, None)
    offers, _errors = offers_from_store(store, now=NOW)
    assert offers == []


def test_caucion_live_book_accepts_matching_native_identity(tmp_path, monkeypatch):
    store = _store(tmp_path)

    class Reader:
        def book(self, *_):
            return {
                "date": NOW.isoformat(),
                "identity": {
                    "ticker": "PESOS7", "type": "CAUCIONES",
                    "market": "BYMA", "currency": "ARS",
                    "settlement_used": "INMEDIATA",
                },
                "bids": [{"price": "19.1", "quantity": "30000000"}],
                "offers": [],
            }

    monkeypatch.setattr(live, "collection_window", lambda at: True)
    result = live.refresh(Reader(), store, now=NOW, clock_fn=lambda: NOW)
    assert result["ready"] == 1
    assert result["states"] == {"PESOS7": "BOOK_READY"}
    assert result["real_routes"] == []

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
    assert live.refresh(Reader(), store, now=at, clock_fn=lambda:at)["ready"] == 1
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
    assert live.refresh(Reader(), store, now=at, clock_fn=lambda:at)["ready"] == 0
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
    assert live.refresh(Reader(), store, now=at, clock_fn=lambda:at)["ready"] == 0
    offers, errors = offers_from_store(store, now=at)
    assert offers == [] and len(errors) == 1


def test_quote_created_during_read_is_compared_to_receipt_clock(tmp_path, monkeypatch):
    # RCA: runtime run 40990729916 had batch start 16:54:59.976865 ART
    # and a PESOS7 provider quote at 16:55:01.537 ART, classified FUTURE_BOOK.
    # Reproduce network latency without sleeping or touching a live provider.
    started = datetime(2026, 10, 29, 16, 54, 59, 976865, tzinfo=TZ)
    quoted = datetime(2026, 10, 29, 16, 55, 1, 537000, tzinfo=TZ)
    received = quoted + timedelta(milliseconds=100)
    store = _store(tmp_path, ticker="PESOS7", at=started)
    mass.collect(store, run_id="caucion-receipt-clock")
    for claim in bridge.complements_from_store(store, now=started):
        with store.connect() as connection:
            primary = dict(connection.execute(
                "SELECT * FROM financial_instrument_catalog WHERE ticker='PESOS7'").fetchone())
            primary["raw"] = json.loads(primary.pop("metadata_json"))
            catalog.persist(connection, catalog.complete_with_complement(primary, claim))
    clock = [started]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0]

    class Reader:
        def book(self, symbol, family, settlement):
            assert (symbol, family, settlement) == ("PESOS7", "CAUCIONES", "INMEDIATA")
            clock[0] = received
            return {"date": quoted.isoformat(),
                    "bids": [{"price": "19.1", "quantity": "30000000"}]}

    monkeypatch.setattr(live, "datetime", Clock)
    result = live.refresh(Reader(), store, now=started)
    assert result["states"] == {"PESOS7": "BOOK_READY"}
    assert result["real_routes"] == []
    with store.connect() as connection:
        current = connection.execute("SELECT quoted_at,observed_at,state FROM paper_caucion_live_book").fetchone()
        history = connection.execute("SELECT quoted_at,observed_at,state FROM paper_caucion_live_book_history").fetchone()
    assert tuple(current) == tuple(history) == (quoted.isoformat(), received.isoformat(), "BOOK_READY")
    decision_at = received + timedelta(seconds=1)
    offers, errors = offers_from_store(store, now=decision_at)
    assert not errors and len(offers) == 1
    assert quoted <= received <= decision_at
    # Admission retains its separate stricter 90s TTL after collection.
    offers, errors = offers_from_store(store, now=received + timedelta(seconds=91))
    assert offers == [] and errors == ["PESOS7:CAUCION_LIVE_BOOK_STALE"]


@pytest.mark.parametrize("quote_offset,receipt_offset,expected", [
    (2, 1, "FUTURE_BOOK"),  # genuinely ahead of the response receipt
    (-119, 3, "STALE_BOOK"),  # expires during the read; batch time looked fresh
    (-118, 2, "BOOK_READY"),  # original 120-second collector boundary is intact
    (0, -1, "READ_ERROR:ValueError"),  # receipt clock regressed before batch
    (None, 2, "READ_ERROR:ValueError"),
    ("naive", 2, "READ_ERROR:ValueError"),
])
def test_receipt_clock_preserves_future_stale_and_timestamp_rejections(
        tmp_path, monkeypatch, quote_offset, receipt_offset, expected):
    store = _store(tmp_path)
    received = NOW + timedelta(seconds=receipt_offset)
    if quote_offset is None:
        stamp = None
    elif quote_offset == "naive":
        stamp = NOW.replace(tzinfo=None).isoformat()
    else:
        stamp = (NOW + timedelta(seconds=quote_offset)).isoformat()

    class Reader:
        def book(self, *_args):
            return {"date": stamp,
                    "bids": [{"price": "19.1", "quantity": "30000000"}]}

    monkeypatch.setattr(live, "collection_window", lambda _at: True)
    result = live.refresh(Reader(), store, now=NOW, clock_fn=lambda: received)
    assert result["states"] == {"PESOS7": expected}
    assert result["real_routes"] == []
    with store.connect() as connection:
        row = connection.execute("SELECT observed_at,state,tna_fraction,available_principal FROM paper_caucion_live_book").fetchone()
    assert row["observed_at"] == received.isoformat()
    assert row["state"] == expected
    if expected != "BOOK_READY":
        assert row["tna_fraction"] is row["available_principal"] is None


def test_each_sequential_response_has_its_own_receipt(tmp_path, monkeypatch):
    store = _store(tmp_path, ticker="PESOS1")
    _store(tmp_path, ticker="PESOS7")
    clock = [NOW]
    receipts = {}
    quotes = {}

    class Reader:
        def book(self, ticker, *_args):
            clock[0] += timedelta(seconds=2)
            receipts[ticker] = clock[0].isoformat()
            quotes[ticker] = (clock[0] - timedelta(milliseconds=100)).isoformat()
            return {"date": quotes[ticker],
                    "bids": [{"price": "19.1", "quantity": "30000000"}]}

    monkeypatch.setattr(live, "collection_window", lambda _at: True)
    result = live.refresh(Reader(), store, now=NOW, clock_fn=lambda: clock[0])
    assert result["ready"] == 2 and result["real_routes"] == []
    assert len(set(receipts.values())) == 2
    with store.connect() as connection:
        rows = connection.execute("SELECT ticker,quoted_at,observed_at FROM paper_caucion_live_book ORDER BY ticker").fetchall()
        history = connection.execute("SELECT ticker,quoted_at,observed_at FROM paper_caucion_live_book_history ORDER BY ticker").fetchall()
    assert [tuple(row) for row in rows] == [tuple(row) for row in history]
    for row in rows:
        assert row["quoted_at"] == quotes[row["ticker"]]
        assert row["observed_at"] == receipts[row["ticker"]]
