"""OFFHOST: the real SDK adapter, Gateway, catalog and PAPER gate for ten families."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from ppi_client.ppi import PPI
import pytest
import requests

from bd_ppi_readonly_guard import ProductionMarketReader
from be_paper_engine import PaperStore
import bf_production_paper_observer as observer
import bu_instrument_catalog as catalog
import cp_contract_evidence_v2_hf6 as evidence
import cr_contract_evidence_v2_mass_hf6 as mass
import rc6_contract_bridge as bridge
import rc6_paper_admission_gate as admission
from rc6_ppi_gateway_reader_adapter import SharedPPIReaderAdapter
import rc6_ppi_native_request_receipt as settlement_proof
from rc6_ppi_shared_session_owner import (
    PPISharedSessionOwner, PrimaryPPICatalogAuthority, load_primary_ppi_catalog_snapshot,
)
from tests.test_rc6_a680_ppi_guard_wire_receipt import FakeBudget
from tests.test_rc6_ppi_native_request_receipt import PARTS, RELEASE, pin


AT = datetime(2026, 11, 24, 14, 30, tzinfo=timezone.utc)
CASES = [
    ("GGAL", "ACCIONES", "BYMA", "A-24HS"),
    ("AAPL", "CEDEARS", "BYMA", "A-24HS"),
    ("SPY", "ETFS", "BYMA", "A-24HS"),
    ("AL30", "BONOS", "BYMA", "A-24HS"),
    ("QA_LETRA", "LETRAS", "BYMA", "A-24HS"),
    ("QA_ON", "OBLIGACIONES", "BYMA", "A-24HS"),
    ("YPFC40500D", "OPCIONES", "BYMA", "INMEDIATA"),
    ("DLR/NOV26", "FUTUROS", "A3", "INMEDIATA"),
    ("PESOS1", "CAUCIONES", "BYMA", "INMEDIATA"),
    ("QA_FCI", "FCI", "FCI", "INMEDIATA"),
]
FIXED_INCOME = {"BONOS", "LETRAS", "OBLIGACIONES"}


class ReplayClock(datetime):
    value = AT

    @classmethod
    def now(cls, tz=None):
        return cls.value.astimezone(tz) if tz is not None else cls.value.replace(tzinfo=None)


class ReplayBudget(FakeBudget):
    def acquire(self, endpoint, *, consumer, priority):
        super().acquire(endpoint, consumer=consumer, priority=priority)
        return {"allowed": True, "lease": f"OFFHOST-LEASE-{len(self.calls)}"}


def _record(ticker, family, market, settlement):
    raw = {"ticker": ticker, "type": family, "market": market, "currency": "ARS"}
    if family == "OPCIONES":
        raw["description"] = "Opción compra YPFD AR$ 40500.00 Vto. 18/12/2026"
    if family in FIXED_INCOME:
        spec = {"market": market, "currency": "ARS", "settlement": settlement,
                "quantity_step": "1", "cash_multiplier": ".01",
                "metadata_source": "OFFHOST_FIXTURE_ECONOMIC_TERMS"}
        raw["financial_contract_v17"] = spec
    return catalog.normalize_record(raw, settlement, AT.isoformat(), "OFFHOST_FAMILY_TEST")


def _derivative_economics(store, family):
    with store.connect() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM financial_instrument_catalog")]
    planned, _, _ = mass.planned_records(rows)
    evidence.record_snapshots(store, planned)
    claim = next(c for c in bridge.complements_from_store(store, now=AT)
                 if c["instrument_type"] == family)
    primary = next(r for r in rows if r["instrument_type"] == family)
    primary["raw"] = json.loads(primary.pop("metadata_json"))
    with store.connect() as con:
        catalog.persist(con, catalog.complete_with_complement(primary, claim))
        catalog.sync_candidate_universe(con, AT.isoformat())


@pytest.mark.parametrize(("ticker", "family", "market", "term"), CASES, ids=[row[1] for row in CASES])
def test_one_instrument_per_family_through_gateway_and_engine(
        tmp_path, monkeypatch, ticker, family, market, term):
    key = (ticker, family, market, "ARS", term)
    store = PaperStore(str(tmp_path / "paper.db"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    observer._support_schema(store)
    monkeypatch.setenv("PAPER_V17_DB_PATH", store.path)
    monkeypatch.setenv("POROTA_BUILD_SHA", RELEASE[0])
    monkeypatch.setenv("POROTA_CANDIDATE_TREE_SHA", RELEASE[1])
    monkeypatch.setattr("bd_ppi_readonly_guard.datetime", ReplayClock)
    monkeypatch.setattr(catalog, "datetime", ReplayClock)
    monkeypatch.setattr(settlement_proof, "datetime", ReplayClock)
    monkeypatch.setattr(observer, "now_iso", lambda: ReplayClock.value.isoformat())
    monkeypatch.setattr(admission, "datetime", ReplayClock)
    # Isolate the instrument guard from the independently published live T-10.
    monkeypatch.setattr(admission, "admission_status", lambda: (True, ""))
    with store.connect() as con:
        if family == "OPCIONES":
            underlying = catalog.normalize_record(
                {"ticker": "YPFD", "type": "ACCIONES", "market": "BYMA", "currency": "ARS",
                 "settlement": "INMEDIATA"}, "INMEDIATA", AT.isoformat(), "OFFHOST_UNDERLYING")
            catalog.persist(con, underlying)
        record = _record(ticker, family, market, term)
        catalog.persist(con, record)
        con.commit()
        catalog.sync_candidate_universe(con, AT.isoformat(), bounded_batch=128)
    rows, keys, digest, literals = load_primary_ppi_catalog_snapshot(
        store.path, include_diagnostic_candidates=True)
    assert key in keys and key not in literals
    assert catalog.lookup(store, ticker, family, term, market=market, currency="ARS")[
        "_admission_reason"] == "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED"

    calls, budget = [], ReplayBudget()

    def send(_, request, **kwargs):
        endpoint = urlsplit(request.url).path.rsplit("/", 1)[-1]
        query = {k.lower(): v for k, v in parse_qs(urlsplit(request.url).query).items()}
        assert endpoint in {"Current", "Book"} and request.method == "GET"
        assert budget.in_wire_scope and query == {"ticker": [ticker], "type": [family], "settlement": [term]}
        calls.append(endpoint)
        price = 1500 if family == "FUTUROS" else 100
        body = {"date": ReplayClock.value.isoformat()}
        if endpoint == "Current":
            body.update(price=price, volume=1000, openingPrice=price, max=price, min=price)
        else:
            body.update(bids=[{"position": 0, "price": price - 1, "quantity": 1000}],
                        offers=[{"position": 0, "price": price + 1, "quantity": 1000}])
        response = requests.Response()
        response.status_code, response.url, response.request = 200, request.url, request
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(body).encode()
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    reader = ProductionMarketReader("OFFHOST_KEY", "OFFHOST_SECRET", budget=budget, consumer="SCANNER")
    # Only an offline authenticated-session fixture. Every market GET still
    # crosses the project's real SDK guard and the injected HTTPAdapter.send.
    reader._ProductionMarketReader__client = PPI(sandbox=False)
    reader._ProductionMarketReader__authenticated = True
    reader._ProductionMarketReader__guard.login_calls = 1
    signer = settlement_proof.SettlementReceiptSigner(RELEASE)
    pin(store.path, signer)
    gateway = PPISharedSessionOwner(
        reader_factory=lambda: reader, instrument_factory=lambda *args: args,
        stream_authority=SimpleNamespace(permits=lambda *_a, **_k: False),
        external_capacity_authority=SimpleNamespace(permits=lambda *_a, **_k: True),
        catalog_authority=PrimaryPPICatalogAuthority(literals, query_keys=keys),
        ppi_catalog_rows=rows, catalog_five_keys=keys, catalog_source_digest=digest,
        consumer_five_keys={"scanner": keys}, socket_path=Path(store.path).parent / "offline.sock",
        settlement_signer=signer,
    )
    gateway._native.clock = lambda: ReplayClock.value
    gateway.open_once()
    try:
        adapter = SharedPPIReaderAdapter(
            consumer="scanner", catalog_source_digest=digest,
            client=SimpleNamespace(owner_status=gateway.status, pull=lambda *_: pytest.fail("stream not expected"),
                                   acquire_native=lambda role, **kw:
                                   gateway.dispatch_native_acquire(consumer=role, **kw)))
        adapter.login_once()
        observed = observer._ppi_direct_rest_observe_only(
            adapter, store, family=family, confirm_settlement=True)
        assert not observed["paper_ready"]
        if family in {"CAUCIONES", "FCI"}:
            assert calls == ["Current"]
            assert observed["status"] == observer._PPI_REFERENCE_OBSERVED[family]
            assert catalog.lookup(store, ticker, family, term, market=market, currency="ARS")[
                "_admission_reason"] == "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED"
            return
        assert observed["status"] == "FRESH_OBSERVED"
        assert observed["settlement_attested"]
        assert calls == ["Current", "Book"]
        from bl_candle_engine import SampleMaterializer

        SampleMaterializer(store).tick(AT + timedelta(minutes=5))
        with store.connect() as connection:
            guarded_bars = connection.execute("""SELECT json_extract(s.identity_json,'$.resolution'),
                v.body_json FROM candle_versions v JOIN candle_series s USING(series_id)
                WHERE json_extract(s.identity_json,'$.source')='PPI_GUARDED_CURRENT'
                ORDER BY 1""").fetchall()
        assert [row[0] for row in guarded_bars] == ["1m", "5m"]
        for _, body in guarded_bars:
            candle = json.loads(body)
            assert candle["quality"] == "SAMPLED" and candle["volume"] is None
        if family in {"OPCIONES", "FUTUROS"}:
            _derivative_economics(store, family)
        item = catalog.lookup(store, ticker, family, term, market=market, currency="ARS")
        assert item["settlement_source"] == "REQUEST_CANDIDATE"
        assert item["raw"].get("settlement") is None
        assert settlement_proof.FIELD in item["raw"]
        assert "_admission_reason" not in item, item.get("_admission_reason")
        assert item["capability"].startswith("READY_PAPER_")
        assert observer._eligible_scanner_identities(store).get((ticker, family, term)) == key
        monkeypatch.setattr(ReplayClock, "value", AT + timedelta(seconds=1))
        live_quote = observer._read_scanner_quote(adapter, store, ticker, family, term, identity=key)
        assert calls == ["Current", "Book", "Current", "Book"]
        assert not live_quote.opening_block_reason
        assert not live_quote.time_error(ReplayClock.value.isoformat(), require_trade=True)
        assert admission.paper_admission_guard(live_quote, store) == (True, "")
        assert not store.open_positions()
    finally:
        gateway.close()
