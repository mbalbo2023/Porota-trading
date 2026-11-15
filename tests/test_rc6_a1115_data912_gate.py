"""A1115-D912: disabling the legacy source must prevent hidden network writes."""

import ba_data912_history as data912


def test_ensure_symbol_disabled_never_opens_a_provider_session(monkeypatch):
    """The legacy liquidity fallback must obey the same OFF switch as refresh."""
    monkeypatch.setattr(data912, "DATA912_REFRESH_ENABLED", False)

    def forbid_network():
        raise AssertionError("DATA912_EGRESS_WHILE_DISABLED")

    monkeypatch.setattr(data912, "_session", forbid_network)

    assert data912.ensure_symbol("GGAL", "ACCIONES") == 0


def test_ensure_symbol_explicitly_enabled_preserves_historical_fallback(monkeypatch):
    """The OFF guard does not silently remove the opt-in archive repair path."""
    monkeypatch.setattr(data912, "DATA912_REFRESH_ENABLED", True)
    session = object()
    requested = []
    monkeypatch.setattr(data912, "_session", lambda: session)

    def fetch(client, path):
        requested.append((client, path))
        return ["provider-payload"]

    monkeypatch.setattr(data912, "_get_json", fetch)
    monkeypatch.setattr(data912, "_normalize", lambda raw, cutoff: ["valid-candle"])
    monkeypatch.setattr(
        data912, "_store_data912_candles", lambda symbol, family, candles: (3, 0)
    )

    assert data912.ensure_symbol("GGAL", "ACCIONES", days=5) == 3
    assert requested == [(session, "/historical/stocks/GGAL")]


def test_all_three_refresh_entrypoints_respect_the_off_switch(monkeypatch):
    """No disabled direct refresh route should reach provider network."""
    monkeypatch.setattr(data912, "DATA912_REFRESH_ENABLED", False)

    def forbid_network():
        raise AssertionError("DATA912_EGRESS_WHILE_DISABLED")

    monkeypatch.setattr(data912, "_session", forbid_network)
    assert data912.ensure_symbol("GGAL", "ACCIONES") == 0
    assert data912.refresh()["disabled"] is True
    assert data912.refresh_symbols([("GGAL", "ACCIONES")])["disabled"] is True
