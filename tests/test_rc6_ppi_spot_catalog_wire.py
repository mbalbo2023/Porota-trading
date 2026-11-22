"""Exercise an existing PAPER pipeline with the pinned SDK and an offline wire."""

from datetime import datetime, timedelta
from importlib.metadata import version
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from bd_ppi_readonly_guard import ProductionMarketReader
from be_paper_engine import PaperBroker, PaperStore
import bf_production_paper_observer as observer
import bu_instrument_catalog as catalog
from rc6_dynamic_universe import promotion
import rc6_paper_admission_gate as gate
from rc6_ppi_gateway_reader_adapter import SharedPPIReaderAdapter
from rc6_ppi_shared_session_owner import load_primary_ppi_catalog_snapshot
from tests.test_rc6_a680_ppi_guard_wire_receipt import FakeBudget


AT = datetime.fromisoformat("2026-11-24T12:01:00-03:00")
TERM = "A-24HS"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def replay(tmp_path, monkeypatch):
    from ppi_client.ppi import PPI

    assert version("ppi-client") == "1.3.0"
    observed_catalog = json.loads((FIXTURES / "ppi_catalog_20260827.json").read_text())
    aapl = next(record for record in observed_catalog["records"] if record["ticker"] == "AAPL")
    # The archived AAPL payload is unchanged. GGAL uses the documented
    # SearchInstrument shape because this archive has no GGAL payload.
    records = {"AAPL": aapl,
               "GGAL": {"ticker": "GGAL", "type": "ACCIONES", "market": "BYMA",
                        "currency": "Pesos"}}
    clock = SimpleNamespace(at=AT, price=100)
    calls, searches, budgets = [], {}, FakeBudget()

    class ReplayDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.at.astimezone(tz) if tz is not None else clock.at.replace(tzinfo=None)

    def adapter_send(_adapter, request, **_kwargs):
        url = urlsplit(request.url)
        endpoint = url.path.rsplit("/", 1)[-1]
        query = {name: values[0] for name, values in parse_qs(url.query).items()}
        assert request.method == "GET" and budgets.in_wire_scope
        calls.append((endpoint, query))
        if endpoint == "SearchInstrument":
            assert query.keys() == {"ticker", "name", "type", "market"}
            payload = searches[query["ticker"]]
        elif endpoint == "Current":
            assert query.keys() == {"ticker", "type", "settlement"}
            payload = {"date": clock.at.isoformat(), "price": clock.price}
        elif endpoint == "Book":
            assert query.keys() == {"ticker", "type", "settlement"}
            payload = {"date": clock.at.isoformat(),
                       "bids": [{"position": 1, "price": round(clock.price - .1, 2), "quantity": 100000}],
                       "offers": [{"position": 1, "price": round(clock.price + .1, 2), "quantity": 100000}]}
        else:
            pytest.fail(f"Unexpected SDK endpoint: {endpoint}")
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        response.request = request
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(payload).encode()
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", adapter_send)
    wire_reader = ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=budgets,
                                         consumer="SCANNER")
    wire_reader._ProductionMarketReader__client = PPI(sandbox=False)
    wire_reader._ProductionMarketReader__authenticated = True
    state = {"state": "OPEN", "owner_pid": 123, "session_generation": "b" * 64,
             "catalog_source_digest": "a" * 64, "login_invocations_by_owner": 1,
             "guard_login_wire_attempts": 1}
    public_config = json.loads((FIXTURES / "ppi_public_observation_20260828.json").read_text())

    def aux(_consumer, *, method, args):
        if method == "market_configuration":
            result = public_config["configuration"]
        else:
            assert method == "search_instruments"
            result = wire_reader.search_instruments(**args)
        return {"state": "OK", "result": result}

    scopes = []

    def acquire(_consumer, *, method, five_key, priority):
        catalog_rows, query_keys, _, attested = load_primary_ppi_catalog_snapshot(
            store.path, include_diagnostic_candidates=True)
        assert catalog_rows and five_key in query_keys
        scopes.append((method, five_key, priority, five_key in attested))
        receipt = wire_reader.capture_guarded_native(method, five_key, priority=priority)
        wire = receipt["guard_wire_receipt"]
        assert wire["adapter_response_completed"] is True
        assert datetime.fromisoformat(wire["received_at_utc"]) == clock.at
        return {"state": "OK", "receipt": {"raw_payload": receipt["native_payload"]}}

    reader = SharedPPIReaderAdapter(consumer="scanner", catalog_source_digest="a" * 64,
        client=SimpleNamespace(pull=lambda *_: pytest.fail("Unexpected stream use"),
                               owner_status=lambda: state, acquire_auxiliary=aux,
                               acquire_native=acquire))
    reader.login_once()
    store = PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    monkeypatch.setattr(observer, "now_iso", lambda: clock.at.isoformat())
    monkeypatch.setattr(catalog, "datetime", ReplayDateTime)
    monkeypatch.setattr("bd_ppi_readonly_guard.datetime", ReplayDateTime)
    monkeypatch.setattr(observer, "CATALOG_QUERY_SLEEP_SECONDS", 0)
    monkeypatch.setattr(observer, "complementary_discovery", lambda *_: ())
    monkeypatch.setattr(observer, "_reconcile_complementary_catalog", lambda _: 0)
    monkeypatch.setattr("cr_contract_evidence_v2_mass_hf6.collect", lambda *_, **__: {})
    monkeypatch.setattr(promotion, "capacity_controller_from_environment",
                        lambda *_: SimpleNamespace(state=lambda *_: {"status": "NOT_APPROVED"}))
    monkeypatch.setattr(gate, "admission_status", lambda: (True, ""))
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "OBSERVATION_ONLY")
    monkeypatch.setenv("PAPER_V17_DB_PATH", store.path)

    def refresh(family, payloads, *, term=TERM):
        searches.clear()
        searches.update({prefix: [raw] for prefix, raw in payloads})
        monkeypatch.setattr(observer, "_candidate_universe", lambda: [
            (prefix, family, term, "BYMA", 0, family) for prefix in searches])
        return observer._download_catalog(reader, store)

    try:
        yield store, records, reader, clock, calls, scopes, refresh
    finally:
        wire_reader.close()


def _ledger(store):
    with store.connect() as connection:
        return {tuple(row[:5]): (row[5], row[6]) for row in connection.execute(
            """SELECT ticker,instrument_type,market,currency,settlement,can_simulate,detail
               FROM candidate_identity_v2""")}


@pytest.mark.parametrize("symbol", ["GGAL", "AAPL"])
@pytest.mark.parametrize("partial_first", [False, True])
def test_spot_sdk_writer_selector_and_paper_admission(replay, symbol, partial_first):
    store, records, reader, clock, calls, scopes, refresh = replay
    raw = records[symbol]
    family = raw["type"]
    key = (symbol, family, "BYMA", "ARS", TERM)
    prefix = symbol[0]
    # Literal case is synthetic. Provider field presence is an explicit test
    # precondition and is never added to the archived four-field response.
    complete = dict(raw, settlement=TERM)
    payloads = [(prefix, raw), (symbol, complete)] if partial_first else [(prefix, complete), (symbol, raw)]
    assert refresh(family, payloads) == 1
    assert _ledger(store)[key] == (1, "READY_PAPER_SPOT")
    assert catalog.lookup(store, symbol, family, TERM, market="BYMA", currency="ARS")["raw"]["settlement"] == TERM

    focus, symbols, *_, feasibility = observer._runtime_readiness(store)
    request = (symbol, family, TERM)
    assert symbols == (request,)
    scope = feasibility["active_plan"]["read_scopes"][0]
    assert scope == {"request": request, "identity": key, "priority": "DISCOVERY"}
    quote = observer._read_scanner_quote(reader, store, *request, priority=scope["priority"], identity=key)
    assert quote.time_error(clock.at.isoformat(), require_trade=True) == ""
    assert [name for name, _ in calls] == ["SearchInstrument", "SearchInstrument", "Current", "Book"]
    assert scopes == [(method, key, "DISCOVERY", True) for method in ("current", "book")]
    assert observer._scanner_opening_capacity(request, focus=focus, feasibility=feasibility) == (True, "")
    assert observer.cedear_opening_gate(family, clock.at) == (True, "")
    if family == "CEDEARS":
        assert observer.cedear_opening_gate(family, clock.at - timedelta(hours=1)) == (
            False, "CEDEAR_UNDERLYING_US_PREOPEN")
    assert gate.paper_admission_guard(quote, store) == (True, "")

    broker = PaperBroker(store, initial_cash="1000000", require_supervisor=False, ai_mode="OFF",
                         target_gain_pct="0.05", economics_mode="SHADOW",
                         clock_fn=lambda: clock.at.isoformat(),
                         admission_guard=gate.paper_admission_guard)
    store.add_quote(quote)
    broker.on_quote(quote)
    for offset, price in enumerate([100.2, 100.4, 100.6, 100.8, 101, 101.4, 102], 1):
        clock.at = AT + timedelta(minutes=offset)
        clock.price = price
        quote = observer._read_scanner_quote(reader, store, *request, priority=scope["priority"], identity=key)
        store.add_quote(quote)
        broker.on_quote(quote)
    with store.connect() as connection:
        assert connection.execute("SELECT side FROM paper_fills").fetchone()[0] == "BUY_SIMULATED"
        assert connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1
        assert connection.execute("SELECT action FROM paper_decisions ORDER BY id DESC LIMIT 1").fetchone()[0] == "BUY"
        assert tuple(connection.execute("SELECT mode,real_orders_sent FROM observer_state").fetchone()) == (
            "PRODUCTION_PAPER", 0)


@pytest.mark.parametrize("symbol", ["GGAL", "AAPL"])
def test_spot_sdk_public_catalog_is_diagnostic_even_with_fresh_current_book(replay, symbol):
    store, records, reader, clock, calls, scopes, refresh = replay
    raw = records[symbol]
    family = raw["type"]
    key = (symbol, family, "BYMA", "ARS", TERM)
    assert "settlement" not in raw
    assert refresh(family, [(symbol, raw)]) == 1
    assert _ledger(store)[key] == (0, "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED")
    assert observer._runtime_readiness(store)[1] == ()
    assert observer._ppi_direct_rest_probe_candidates(store, family=family) == [key]
    quote = observer._read_scanner_quote(reader, store, symbol, family, TERM, identity=key)
    assert quote.time_error(clock.at.isoformat(), require_trade=True) == ""
    assert quote.opening_block_reason == "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED"
    assert [name for name, _ in calls] == ["SearchInstrument", "Current", "Book"]
    assert scopes == [(method, key, "DISCOVERY", False) for method in ("current", "book")]
    assert gate.paper_admission_guard(quote, store) == (False, "PAPER_EXACT_CONTRACT_NOT_READY")


def test_later_partial_republication_keeps_original_literal_clock(replay):
    store, records, _, clock, _, _, refresh = replay
    raw = records["AAPL"]
    family = raw["type"]
    key = ("AAPL", family, "BYMA", "ARS", TERM)
    assert refresh(family, [("AAPL", dict(raw, settlement=TERM))]) == 1
    clock.at += timedelta(days=1)
    assert refresh(family, [("AAPL", raw)]) == 1
    record = catalog.lookup(store, "AAPL", family, TERM, market="BYMA", currency="ARS")
    assert record["settlement_source"] == "PPI_FIELD"
    assert record["last_seen_at"] == AT.isoformat()
    assert _ledger(store)[key] == (1, "READY_PAPER_SPOT")

    clock.at += timedelta(days=14)
    assert refresh(family, [("AAPL", raw)]) == 1
    assert _ledger(store)[key] == (0, "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED")


@pytest.mark.parametrize("changed", [{"isin": "CHANGED"}, {"instrumentType": "BONOS"}])
def test_changed_partial_material_terms_do_not_hide_behind_old_literal(replay, changed):
    store, records, _, clock, _, _, refresh = replay
    raw = records["AAPL"]
    family = raw["type"]
    key = ("AAPL", family, "BYMA", "ARS", TERM)
    old = dict(raw, settlement=TERM, isin="ORIGINAL")
    assert refresh(family, [("AAPL", old)]) == 1
    clock.at += timedelta(days=1)
    assert refresh(family, [("AAPL", raw | changed)]) == 1
    assert _ledger(store)[key] == (0, "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED")


def test_material_currency_sibling_is_never_silently_merged(replay):
    store, records, _, _, _, _, refresh = replay
    raw = records["AAPL"]
    family = raw["type"]
    usd = dict(raw, currency="Dolares Billete | MEP")
    assert refresh(family, [("A", dict(raw, settlement=TERM)), ("AAPL", usd)]) == 2
    ledger = _ledger(store)
    assert {key[3] for key in ledger} == {"ARS", "USD_MEP"}
    assert sum(entry[0] for entry in ledger.values()) == 1
    assert observer._runtime_readiness(store)[1] == ()


def test_option_description_changes_are_contract_changes_even_without_settlement(replay):
    store, _, _, clock, _, _, refresh = replay
    raw = {"ticker": "GFGC6000DI", "type": "OPCIONES", "market": "BYMA", "currency": "Pesos",
           "description": "Opción compra GGAL AR$ 6000.00 Vto. 16/12/2026"}
    term = "INMEDIATA"
    assert refresh("OPCIONES", [("G", dict(raw, settlement=term))], term=term) == 1
    clock.at += timedelta(days=1)
    changed = dict(raw, description="Opción compra GGAL AR$ 6100.00 Vto. 16/12/2026")
    assert refresh("OPCIONES", [("G", changed)], term=term) == 1
    record = catalog.lookup(store, raw["ticker"], "OPCIONES", term, market="BYMA", currency="ARS")
    assert record["settlement_source"] == "REQUEST_CANDIDATE"
    assert record["raw"]["description"] == changed["description"]
    assert record["_admission_reason"] == "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED"
