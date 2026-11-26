"""Run IOL through the real client, clock guards, PAPER broker and isolated SQLite."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
import json
from pathlib import Path
import subprocess
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from be_paper_engine import PaperBroker, PaperStore
from bf_production_paper_observer import _scanner_trade_temporal_rejection
from bl_candle_engine import SampleMaterializer, record_iol_trade_sample
from bm_exit_supervisor import PositionExitSupervisor
from cu_history_store_v2_hf6 import Candle, append_candle, read_as_of
from cv_history_store_adapter_hf6 import HistoricalStore
from iol_rest_readonly_rc6 import IOLRestError, IOLRestReadOnly
from rc6_iol_history_reference import ingest_daily_reference
from rc6_iol_market_data import IOLMarketReader
from scripts.rc6_history_import_source_reported import _source_store
from scripts.porota_actions_release import promotion_source
from scripts.porota_build_deploy_bundle_v2 import build_bundle
from scripts.porota_predeploy_impact import classify
from scripts.porota_testing_scope import RELEASE_BLOCKED, TESTING_ONLY_MARKER


AT = datetime(2026, 11, 24, 14, 30, tzinfo=timezone.utc)
GGAL = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
FAMILIES = (
    GGAL,
    ("AAPL", "CEDEARS", "BYMA", "ARS", "A-24HS"),
    ("QAETF", "ETFS", "BYMA", "ARS", "A-24HS"),
    ("AL30", "BONOS", "BYMA", "ARS", "A-24HS"),
    ("QALETRA", "LETRAS", "BYMA", "ARS", "A-24HS"),
    ("YMCJO", "OBLIGACIONES", "BYMA", "ARS", "A-24HS"),
    ("GFGC5000D", "OPCIONES", "BYMA", "ARS", "INMEDIATA"),
    ("DLR/NOV26", "FUTUROS", "A3", "ARS", "INMEDIATA"),
    ("PESOS1", "CAUCIONES", "BYMA", "ARS", "INMEDIATA"),
    ("IOLCAMA", "FCI", "BYMA", "ARS", "INMEDIATA"),
)
FAMILY_REASON = {
    "FUTUROS": "IOL_A3_MARKET_UNVERIFIED",
    "CAUCIONES": "IOL_REPO_BOOK_CLOCK_UNVERIFIED",
    "FCI": "IOL_FUND_TERMS_AND_NAV_UNVERIFIED",
}


class ReplayHTTP:
    def __init__(self, identity):
        self.symbol, self.family, _, self.currency, term = identity
        self.term = "T1" if term == "A-24HS" else "T0"
        self.now = AT
        self.price = 100.0
        self.calls = []
        self.overrides = {}
        self.reject = None

    def open(self, request, timeout):
        url = urlsplit(request.full_url)
        assert url.scheme == "https" and url.hostname == "api.iolinversiones.com"
        assert timeout == 8
        path = url.path.split("/")[-1]
        self.calls.append(path)
        if url.path == "/auth/token":
            assert request.get_method() == "POST"
            assert json.loads(request.data)["grant_type"] == "password"
            row = {"access_token": "OFFHOST_ACCESS_ONLY_FIXTURE", "refresh_token": "OFFHOST_REFRESH_ONLY_FIXTURE",
                   "expires_in": 900}
        else:
            assert request.get_method() == "GET"
            assert request.get_header("Authorization") == "Bearer OFFHOST_ACCESS_ONLY_FIXTURE"
            if path == self.reject:
                raise HTTPError(request.full_url, 429, "fixture", {"Retry-After": "7"}, BytesIO())
            base = {"symbol": self.symbol, "market": "BCBA", "settlement_term": self.term}
            if url.path.startswith("/broker/assets/"):
                assert parse_qs(url.query) == {"market": ["BCBA"]}
                row = {**base, "currency": self.currency, "asset_type": self.family}
            elif path in {"quote", "depth"}:
                assert parse_qs(url.query) == {"market": ["BCBA"], "term": [self.term]}
                row = {**base, "last_updated": (self.now - timedelta(seconds=1)).isoformat()}
                if path == "quote":
                    row.update(currency=self.currency, prices={"current": self.price})
                else:
                    row.update(bids=[{"price": self.price - .1, "size": 10000}],
                               asks=[{"price": self.price + .1, "size": 10000}])
            elif path == "terms":
                row = [{"term_days": 1, "term_id": 10, "due_date": "2026-11-25", "min_amount": 1000}]
            elif path == "quotes":
                row = [{"bid": {"rate": 30, "amount": 100000}}]
            elif path == "bars":
                assert parse_qs(url.query) == {"market": ["BCBA"], "from": ["2026-11-23 00:00:00"],
                                                "to": ["2026-11-23 23:59:59"]}
                at = AT - timedelta(days=1)
                row = {"symbol": self.symbol, "market": "BCBA", "total_bars": 1, "bars": [
                    {"timestamp": int(at.timestamp()), "datetime": at.isoformat(), "open": 100,
                     "high": 102, "low": 99, "close": 101, "volume": 200}]}
            else:
                pytest.fail(f"unexpected fixture route {url.path}")
        if path in self.overrides:
            row.update(self.overrides[path])
        return BytesIO(json.dumps({"data": row, "error": None}).encode())


def reader(tmp_path, identity=GGAL):
    transport = ReplayHTTP(identity)
    rest = IOLRestReadOnly(tmp_path / "iol-fixture", clock=lambda: transport.now.timestamp(),
                           opener=transport)
    # Credential acquisition is supplied by the private fixture. Auth, quote,
    # depth and asset still run through IOLRestReadOnly's real HTTP boundary.
    rest._credentials = lambda: {"grant_type": "password", "username": "offhost", "password": "fixture"}
    return IOLMarketReader(rest, trade_clock=lambda row: row["last_updated"],
                           clock=lambda: transport.now), transport


@pytest.mark.parametrize("identity", FAMILIES, ids=[case[1] for case in FAMILIES])
def test_iol_family_contract(identity, tmp_path):
    market, transport = reader(tmp_path, identity)
    family = identity[1]
    if family in FAMILY_REASON:
        with pytest.raises(IOLRestError, match="IOL_MARKET_IDENTITY_UNSUPPORTED"):
            market.observe(identity)
        assert transport.calls == []
        if family == "CAUCIONES":
            rates = market.rest.call("get_caucion_rates", {"currency": "ARS", "caucion_type": "colocadora"})
            assert rates["rates"][0]["reference_only"] is True
        elif family == "FCI":
            asset = market.rest.call("get_asset_info", {"market": "BCBA", "symbol": identity[0]})
            assert asset["type"] == "FCI" and asset["currency"] == "ARS"
        return

    sampled = market.observe(identity)
    quote = sampled.quote
    assert (quote.symbol, quote.asset_class, quote.market, quote.currency, quote.settlement) == identity
    assert quote.time_error(AT.isoformat(), require_trade=True) == ""
    assert sampled.current.provider_at == quote.trade_at
    assert sampled.book.provider_at == quote.book_at
    assert sampled.current.data_sha256 != sampled.book.data_sha256
    assert transport.calls == ["token", identity[0], "quote", "depth"]
    store = PaperStore(str(tmp_path / "sample.db"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    assert record_iol_trade_sample(store, sampled, as_of=AT.isoformat())
    assert not record_iol_trade_sample(store, sampled, as_of=AT.isoformat())
    SampleMaterializer(store).tick(AT + timedelta(minutes=5))
    with store.connect() as db:
        rows = db.execute("""SELECT s.identity_json, v.body_json FROM candle_series s
            JOIN candle_versions v USING(series_id) ORDER BY s.identity_json""").fetchall()
    sources = {json.loads(row[0])["source"] for row in rows}
    assert sources == {"IOL_REST_TRADE_SAMPLE"}
    assert {json.loads(row[0])["resolution"] for row in rows} == {"1m", "5m"}
    assert all(json.loads(row[1])["quality"] == "SAMPLED" and json.loads(row[1])["volume"] is None
               for row in rows)


def test_ggal_paper_ledger_and_exit(tmp_path, monkeypatch):
    import bf_production_paper_observer as observer
    import bu_instrument_catalog as catalog
    from bq_exit_policy import PaperSessionPolicy
    from rc6_paper_admission_gate import paper_admission_guard
    from tests.test_rc6_ppi_gateway_family_ready import ReplayClock, _preopen

    market, transport = reader(tmp_path)
    store = PaperStore(str(tmp_path / "paper.db"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    observer._support_schema(store)
    monkeypatch.setattr(ReplayClock, "value", AT)
    monkeypatch.setattr(catalog, "datetime", ReplayClock)
    monkeypatch.setattr(observer, "now_iso", lambda: transport.now.isoformat())
    _preopen(tmp_path, monkeypatch)
    request = GGAL[0], GGAL[1], GGAL[4]
    assert request not in observer._runtime_readiness(store)[1]
    primary = catalog.normalize_record({
        "ticker": GGAL[0], "type": GGAL[1], "market": GGAL[2], "currency": GGAL[3],
        "settlement": GGAL[4], "_discovery_source": "PPI_PRIMARY",
    }, None, AT.isoformat(), "OFFHOST_PPI_IDENTITY_FIXTURE")
    with store.connect() as db:
        catalog.persist(db, primary)
        catalog.sync_candidate_universe(db, AT.isoformat())
    contract = catalog.contract_for(catalog.lookup(store, *request, market=GGAL[2], currency=GGAL[3]))
    focus, selected, _, _, _, feasibility = observer._runtime_readiness(store)
    assert request in selected
    assert observer._scanner_opening_capacity(request, focus=focus, feasibility=feasibility) == (True, "")
    with pytest.raises(IOLRestError, match="IOL_EXACT_CONTRACT_MISMATCH"):
        market.observe(GGAL, contract=replace(contract, settlement="INMEDIATA"))
    assert transport.calls == []
    sector_map = tmp_path / "sector-map.csv"
    sector_map.write_text(
        "ticker,family,market,currency,settlement,sector,source,author,effective_at,reviewed\n"
        "GGAL,ACCIONES,BYMA,ARS,A-24HS,FINANCIERO,TEST_REVIEWED_FIXTURE,RC6_TEST,2026-09-10,true\n")
    monkeypatch.setenv("POROTA_SECTOR_MAP_PATH", str(sector_map))
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "BINDING")
    broker = PaperBroker(store, clock_fn=lambda: transport.now.isoformat(), ai_mode="OFF",
                         economics_mode="SHADOW", session_policy=PaperSessionPolicy(),
                         admission_guard=paper_admission_guard)
    for index, price in enumerate(range(95, 104)):
        transport.now = AT + timedelta(minutes=index)
        ReplayClock.value = transport.now
        transport.price = float(price)
        observed = market.observe(GGAL, contract=contract)
        assert observed.quote.metadata_source == "IOL_REST"
        store.add_quote(observed.quote)
        assert not _scanner_trade_temporal_rejection(
            observed.quote, broker, store, checked_at=transport.now.isoformat())
        if index == 0:
            action, _, _, fields = broker.decide(observed.quote)
            assert (action, fields["reason_code"]) == ("HOLD", "SIGNAL_WARMUP")
    broker.on_quote(observed.quote)
    assert store.open_positions()
    position = store.open_positions()[0]
    transport.price = float(Decimal(position["target_price"]) + Decimal("0.20"))
    transport.now += timedelta(minutes=5)
    closing = market.observe(GGAL, contract=contract, exit_only=True).quote
    assert transport.calls == ["token"] + ["GGAL", "quote", "depth"] * 9 + ["GGAL", "depth"]
    key = tuple(position[name] for name in ("symbol", "asset_class", "settlement", "currency", "market"))
    verdict = PositionExitSupervisor(broker, clock_fn=lambda: transport.now.isoformat()).tick({key: closing})[0]
    assert (verdict.state, verdict.cause) == ("CLOSED", "TAKE_PROFIT_PAPER")
    assert not store.open_positions()
    with store.connect() as db:
        assert [row[0] for row in db.execute("SELECT side FROM paper_fills ORDER BY id")] == [
            "BUY_SIMULATED", "SELL_SIMULATED"]
        assert db.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0] == 0


@pytest.mark.parametrize(("report", "fields", "reason"), [
    ("quote", {"currency": "USD"}, "IOL_QUOTE_CURRENCY_UNVERIFIED"),
    ("quote", {"settlement_term": "T0"}, "IOL_MARKET_IDENTITY_MISMATCH"),
    ("quote", {"last_updated": "2026-11-24T14:30:00"}, "IOL_MARKET_CLOCK_UNVERIFIED"),
    ("quote", {"last_updated": (AT - timedelta(seconds=121)).isoformat()}, "IOL_LAST_TRADE_STALE"),
    ("depth", {"last_updated": (AT + timedelta(seconds=1)).isoformat()}, "IOL_MARKET_CLOCK_IN_FUTURE"),
    ("depth", {"asks": []}, "IOL_BOOK_TWO_SIDED_UNVERIFIED"),
    ("depth", {"bids": [{"price": 101, "size": 1}]}, "IOL_BOOK_CROSSED"),
])
def test_iol_pair_rejects_unusable_data(tmp_path, report, fields, reason):
    market, transport = reader(tmp_path)
    transport.overrides[report] = fields
    with pytest.raises(IOLRestError, match=reason):
        market.observe(GGAL)
    assert ("depth" in transport.calls) == (report == "depth")


def test_iol_current_rejection_preserves_status_and_does_not_consume_depth(tmp_path):
    market, transport = reader(tmp_path)
    transport.reject = "quote"
    with pytest.raises(IOLRestError) as failure:
        market.observe(GGAL)
    assert (failure.value.status_code, failure.value.retry_after) == (429, 7)
    assert "depth" not in transport.calls
    assert market.observe(GGAL, exit_only=True).quote.last_kind == "REFERENCE"


def test_iol_response_update_clock_is_not_a_trade_clock(tmp_path):
    market, transport = reader(tmp_path)
    observation = IOLMarketReader(market.rest, clock=lambda: transport.now)
    with pytest.raises(IOLRestError, match="IOL_LAST_TRADE_CLOCK_UNVERIFIED"):
        observation.observe(GGAL)
    assert observation.observe(GGAL, exit_only=True).current is None


def test_iol_daily_ingest_preserves_canonical_history(tmp_path):
    market, transport = reader(tmp_path)
    history = HistoricalStore(str(tmp_path / "history.db"))
    kwargs = {"symbol": "GGAL", "instrument_type": "ACCIONES", "market": "BYMA", "currency": "ARS",
              "settlement": "A-24HS"}
    previous = AT - timedelta(days=4)
    append_candle(history, Candle(**kwargs, date=previous.date().isoformat(), open=98, high=102,
                                  low=97, close=101, volume=100, source="PPI_PRODUCTION_HISTORY",
                                  observed_at=previous.replace(hour=21, minute=1), volume_kind="QUANTITY"))
    with history.connect() as db:
        before = read_as_of(db, **kwargs, as_of=AT.isoformat())
    assert len(before) == 1
    reference = _source_store(tmp_path / "reference.db")
    day = (AT - timedelta(days=1)).date()
    query = {"symbol": "GGAL", "family": "ACCIONES", "market": "BCBA", "start": day, "end": day,
             "now": lambda: transport.now}
    with pytest.raises(ValueError, match="IOL_HISTORY_REFERENCE_STORE_REQUIRED"):
        ingest_daily_reference(market.rest, history, **query)
    assert transport.calls == []
    assert ingest_daily_reference(market.rest, reference, **query)["versions_appended"] == 1
    assert ingest_daily_reference(market.rest, reference, **query)["versions_appended"] == 0
    with reference.connect() as db:
        row = db.execute("SELECT source,price_basis,currency,settlement FROM history_versions_v2").fetchone()
        assert tuple(row) == ("IOL_REST", "SOURCE_REPORTED", "UNKNOWN", "UNKNOWN")
    with history.connect() as db:
        assert read_as_of(db, **kwargs, as_of=AT.isoformat()) == before


def test_predeploy_packaging_and_promotion_reject_testing_tree(tmp_path):
    repository = tmp_path / "project"
    repository.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repository), *args], text=True).strip()

    git("init", "-q")
    git("config", "user.email", "ci@example.invalid")
    git("config", "user.name", "CI")
    (repository / "fixture.py").write_text("pass\n")
    git("add", "fixture.py")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    marker = repository / TESTING_ONLY_MARKER
    marker.parent.mkdir(parents=True)
    marker.write_text('{"release_allowed":false}\n')
    git("add", TESTING_ONLY_MARKER)
    git("commit", "-qm", "test candidate")
    head = git("rev-parse", "HEAD")
    with pytest.raises(ValueError, match=RELEASE_BLOCKED):
        classify(repository, base, head)
    bundle = tmp_path / "release.tgz"
    with pytest.raises(ValueError, match=RELEASE_BLOCKED):
        build_bundle(repository, bundle)
    assert not bundle.exists()
    with pytest.raises(ValueError, match=RELEASE_BLOCKED):
        promotion_source(repository, head)
