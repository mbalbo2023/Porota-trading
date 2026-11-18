"""P0: exact PPI catalog observation even with zero simulation-ready rows."""

import json
from datetime import datetime, timezone

import pytest

from be_paper_engine import PaperStore
from bu_instrument_catalog import init_schema
from bf_production_paper_observer import (
    _ppi_direct_rest_probe_candidates, _ppi_direct_rest_observe_only,
)


@pytest.fixture
def catalog_store(tmp_path):
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    init_schema(store)

    def add(ticker, family, market, currency, settlement, *, primary=True,
            status="AVAILABLE"):
        with store.connect() as c:
            c.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (ticker, family, market, currency, settlement, "PPI_FIELD",
                 "OFFHOST", "2026-11-17T17:00:00Z", "OFFHOST", status,
                 "NOT_READY", json.dumps({"_discovery_source":
                     "PPI_PRIMARY" if primary else "IOL_COMPLEMENTARY"})))

    add("GGAL", "ACCIONES", "BYMA", "ARS", "INMEDIATA")
    add("GGAL", "ACCIONES", "BYMA", "USD", "INMEDIATA")
    add("AL30", "BONOS", "BYMA", "ARS", "INMEDIATA")
    add("AAPL", "CEDEARS", "BYMA", "ARS", "INMEDIATA", primary=False)
    add("ZZZ", "ACCIONES", "BYMA", "ARS", "INMEDIATA", status="UNAVAILABLE")
    return store


def test_observational_candidates_do_not_require_ready_but_reject_ambiguity(catalog_store):
    assert _ppi_direct_rest_probe_candidates(catalog_store, limit=3) == [
        ("AL30", "BONOS", "BYMA", "ARS", "INMEDIATA")]


def test_documented_ppi_search_without_settlement_can_be_probed_observe_only(catalog_store):
    """SearchInstrument can omit settlement; an explicit request remains probe-only.

    A successful native Current+Book GET must verify the actual request at
    runtime.  Neither the catalog settlement hint nor this selection grants
    readiness or the capability to simulate a trade.
    """
    with catalog_store.connect() as c:
        c.execute("""UPDATE financial_instrument_catalog
                     SET settlement_source='REQUEST_CANDIDATE',status='STALE'
                     WHERE ticker='AL30'""")
    assert _ppi_direct_rest_probe_candidates(catalog_store, limit=3) == [
        ("AL30", "BONOS", "BYMA", "ARS", "INMEDIATA")]


@pytest.mark.parametrize("settlement_source,status", [
    ("PPI_FIELD", "AVAILABLE"),
    ("REQUEST_CANDIDATE", "STALE"),
])
def test_probe_uses_exact_current_and_book_but_never_simulates(
        catalog_store, settlement_source, status):
    stamp = datetime.now(timezone.utc).isoformat()
    with catalog_store.connect() as c:
        c.execute("""UPDATE financial_instrument_catalog
                     SET settlement_source=?,status=? WHERE ticker='AL30'""",
                  (settlement_source, status))

    class SyntheticReader:
        def __init__(self):
            self.calls = []

        def capture_guarded_native(self, endpoint, five_key, *, priority):
            self.calls.append((endpoint, tuple(five_key), priority))
            payload = (
                {"date": stamp, "price": 101} if endpoint == "current" else
                {"date": stamp,
                 "bids": [{"position": 1, "price": 100, "quantity": 1}],
                 "offers": [{"position": 1, "price": 102, "quantity": 1}]})
            return {"native_payload": payload, "guard_wire_receipt": {
                "schema": "RC6_PPI_NATIVE_WIRE_RECEIPT_V1",
                "endpoint": endpoint, "http_status_code": 200,
                "http_method": "GET", "adapter_send_invoked": True,
                "adapter_response_completed": True,
                "request_identity_3key": [five_key[0], five_key[1], five_key[4]],
                "claimed_five_key": list(five_key),
                "raw_body_sha256": "a" * 64,
                "guard_budget_receipt_sha256": "b" * 64}}

    reader = SyntheticReader()
    observed = _ppi_direct_rest_observe_only(reader, catalog_store)
    assert observed["status"] == "FRESH_OBSERVED"
    assert observed["identity"] == ["AL30", "BONOS", "BYMA", "ARS", "INMEDIATA"]
    assert observed["paper_ready"] is False
    assert [x[0] for x in reader.calls] == ["current", "book"]
    assert all(call[1] == ("AL30", "BONOS", "BYMA", "ARS", "INMEDIATA")
               and call[2] == "DISCOVERY" for call in reader.calls)
    with catalog_store.connect() as c:
        quotes = c.execute("SELECT opening_block_reason,market,currency FROM market_snapshots").fetchall()
    assert len(quotes) == 1 and tuple(quotes[0]) == (
        "PPI_DIRECT_REST_OBSERVE_ONLY", "BYMA", "ARS")


def test_no_primary_exact_catalog_stays_off_wire(catalog_store):
    with catalog_store.connect() as c:
        c.execute("UPDATE financial_instrument_catalog SET status='UNAVAILABLE' WHERE ticker='AL30'")

    class NoProviderReader:
        def current(self, *args):
            raise AssertionError("UNATTESTED_PROVIDER_GET_FORBIDDEN")

    observed = _ppi_direct_rest_observe_only(NoProviderReader(), catalog_store)
    assert observed["status"] == "CATALOG_IDENTITY_UNAVAILABLE"
    assert observed["paper_ready"] is False
