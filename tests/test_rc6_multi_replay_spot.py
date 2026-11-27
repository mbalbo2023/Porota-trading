"""Synthetic market clocks and GO, with an isolated PAPER ledger per instrument."""

from datetime import timedelta

import pytest

import bf_production_paper_observer as observer
from rc6_iol_market_gateway import IOLGatewayMarketReader
from tests.test_rc6_iol_migration_offhost import (
    open_fixture_iol_gateway, register, replay_iol_spot_paper,
)
from tests.test_rc6_ppi_gateway_family_ready import (
    AT, gateway_socket, replay_native_family,
)


@pytest.mark.parametrize(("family", "ticker", "price_scale"), [
    pytest.param(family, ticker, scale, id=f"{family}-{ticker}")
    for family, specimens in (
        ("ACCIONES", (("GGAL", 1.0), ("PAMP", 1.3), ("YPFD", 1.6))),
        ("CEDEARS", (("AAPL", 1.05), ("MSFT", .85), ("KO", .70))),
        ("ETFS", (("SPY", 1.2), ("QQQ", 1.45), ("IWM", .95))),
    )
    for ticker, scale in specimens
])
def test_iol_spot_gateway_paper_cycle(family, ticker, price_scale, tmp_path, tmp_path_factory, monkeypatch):
    identity = (ticker, family, "BYMA", "ARS", "A-24HS")
    with open_fixture_iol_gateway(tmp_path, tmp_path_factory, identity) as gateway:
        replay_iol_spot_paper(tmp_path, monkeypatch, gateway, identity, price_scale=price_scale)


@pytest.mark.parametrize(("ticker", "underlying", "strike"), [
    pytest.param("YPFC40500D", "YPFD", "40500.00", id="OPCIONES-YPFC40500D"),
    pytest.param("YPFC40750D", "YPFD", "40750.00", id="OPCIONES-YPFC40750D"),
    pytest.param("GFGC5000D", "GGAL", "5000.00", id="OPCIONES-GFGC5000D"),
])
def test_option_iol_hold_and_native_paper_cycle(ticker, underlying, strike, tmp_path, tmp_path_factory,
                                                monkeypatch, gateway_socket):
    identity = (ticker, "OPCIONES", "BYMA", "ARS", "INMEDIATA")
    store = replay_native_family(tmp_path, monkeypatch, gateway_socket, ticker, "OPCIONES", "BYMA",
                                 "INMEDIATA", 0, 0, False,
                                 option_underlying=(underlying, "ACCIONES", "BYMA", "ARS", "INMEDIATA"),
                                 option_strike=strike)

    with open_fixture_iol_gateway(tmp_path, tmp_path_factory, identity) as (_, client, transport, _):
        register(client, "scanner")
        trade_at = (AT - timedelta(seconds=3)).isoformat()
        transport.overrides["quote"] = {"fixture_last_trade_at": trade_at}
        reader = IOLGatewayMarketReader(client, consumer="scanner",
                                       trade_clock=lambda row: row["fixture_last_trade_at"],
                                       clock=lambda: transport.now)
        monkeypatch.setattr(observer, "now_iso", lambda: transport.now.isoformat())
        quote = observer._read_scanner_quote(reader, store, ticker, "OPCIONES", "INMEDIATA",
                                             identity=identity, priority="HOT")
        assert quote.metadata_source == "IOL_REST"
        assert quote.trade_at == trade_at
        assert quote.book_at == (AT - timedelta(seconds=1)).isoformat()
        assert quote.opening_block_reason == "IOL_SPECIALIZED_ENTRY_EVIDENCE_REQUIRED"
        assert transport.calls == ["token", ticker, "quote", "depth"]
        assert not store.open_positions()
