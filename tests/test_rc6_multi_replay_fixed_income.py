"""Replay nine fixed-income identities with private clocks, receipts and ledgers."""

import json
from dataclasses import asdict, replace
from datetime import timedelta

import pytest

from be_paper_engine import PaperBroker, PaperStore
import bf_production_paper_observer as observer
import bu_instrument_catalog as catalog
from rc6_iol_market_gateway import IOLGatewayMarketReader
from rc6_paper_admission_gate import paper_admission_guard
from test_rc6_fi_market_sale_authority import (
    assert_reviewed_market_roundtrip, reviewed_market_entry,
)
from test_rc6_fixed_income_runtime import AT, assert_reviewed_letter_roundtrip, letter_broker
from test_rc6_iol_migration_offhost import open_fixture_iol_gateway, register
from tests import test_rc6_ppi_gateway_family_ready as readiness


INSTRUMENTS = {
    "BONOS": ("AL30", "GD30", "AE38"),
    "LETRAS": ("S31O6", "S30N6", "S29E7"),
    "OBLIGACIONES": ("YMCJO", "IRCFO", "TLCMO"),
}
TRADE_AT = (AT - timedelta(seconds=2)).isoformat()
BOOK_AT = (AT - timedelta(seconds=1)).isoformat()
IOL_HOLD = "IOL_SPECIALIZED_ENTRY_EVIDENCE_REQUIRED"


def assert_iol_scanner_hold(tmp_path, tmp_path_factory, store, opening):
    symbol, family, _, _, settlement = opening.contract.key
    with open_fixture_iol_gateway(tmp_path, tmp_path_factory, opening.contract.key) as (
            _, client, transport, _):
        transport.now = AT
        transport.overrides["quote"] = {"fixture_last_trade_at": TRADE_AT}
        register(client, "scanner")
        reader = IOLGatewayMarketReader(
            client, consumer="scanner", trade_clock=lambda row: row["fixture_last_trade_at"],
            clock=lambda: transport.now)
        quote = observer._read_scanner_quote(
            reader, store, symbol, family, settlement, identity=opening.contract.key, priority="HOT")
    assert quote.contract.key == opening.contract.key
    assert quote.metadata_source == "IOL_REST"
    assert quote.trade_at == TRADE_AT and quote.book_at == BOOK_AT
    assert quote.time_error(AT.isoformat(), require_trade=True) == ""
    assert quote.opening_block_reason == IOL_HOLD
    assert transport.calls == ["token", symbol, "quote", "depth"]

    broker = PaperBroker(store, clock_fn=lambda: AT.isoformat(), admission_guard=paper_admission_guard)
    action, _, reason, _ = broker.decide(quote)
    assert (action, reason) == ("HOLD", IOL_HOLD)
    broker.on_quote(quote)
    assert not store.open_positions()
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0


@pytest.mark.parametrize(("family", "symbol"), [
    pytest.param(family, symbol, id=f"{family}-{symbol}")
    for family, symbols in INSTRUMENTS.items()
    for symbol in symbols
])
def test_multi_instrument_fixed_income(tmp_path, tmp_path_factory, monkeypatch, family, symbol):
    if family == "LETRAS":
        opening, evidence, broker = letter_broker(
            tmp_path, monkeypatch, symbol=symbol, daily_soft_stop_pct="0.015",
            clock_fn=lambda: AT.isoformat(), admission_guard=paper_admission_guard)
        store = broker.store
    else:
        opening, key, book, clock = reviewed_market_entry(tmp_path, monkeypatch, family, symbol=symbol)
        store = PaperStore(str(tmp_path / "paper.sqlite"))
    opening = replace(opening, trade_at=TRADE_AT, book_at=BOOK_AT)
    assert opening.time_error(AT.isoformat(), require_trade=True) == ""
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    observer._support_schema(store)
    monkeypatch.setenv("PAPER_V17_DB_PATH", store.path)
    monkeypatch.setattr(readiness, "AT", AT)
    monkeypatch.setattr(readiness.ReplayClock, "value", AT)
    monkeypatch.setattr(catalog, "datetime", readiness.ReplayClock)
    monkeypatch.setattr(observer, "now_iso", lambda: AT.isoformat())
    readiness._preopen(tmp_path, monkeypatch)

    _, _, market, currency, settlement = opening.contract.key
    record = catalog.normalize_record({
        "ticker": symbol, "type": family, "market": market, "currency": currency,
        "settlement": settlement, "_discovery_source": "PPI_PRIMARY",
        "financial_contract_v17": json.loads(json.dumps(asdict(opening.contract), default=str)),
    }, None, AT.isoformat(), "OFFHOST_FI_REPLAY_IDENTITY")
    with store.connect() as db:
        catalog.persist(db, record)
        catalog.sync_candidate_universe(db, AT.isoformat())
    assert paper_admission_guard(opening, store) == (True, "")
    assert_iol_scanner_hold(tmp_path, tmp_path_factory, store, opening)

    # The reviewed FI engine still consumes the native-format fixture contract.
    if family == "LETRAS":
        assert_reviewed_letter_roundtrip(opening, evidence, broker)
    else:
        assert_reviewed_market_roundtrip(
            store, monkeypatch, opening, key, book, clock, admission_guard=paper_admission_guard)
