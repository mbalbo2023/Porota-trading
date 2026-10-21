"""An empty annual selector must explain missing exact source bases."""
from types import SimpleNamespace

from rc6_trader_dashboard.annual import render
from rc6_trader_dashboard.navigation import BY_KEY
from rc6_trader_dashboard.projection import Page


def test_annual_selector_discloses_non_selectable_historical_series():
    page = Page("production_history", [{"symbol": "EXAMPLE", "family": "ACCIONES",
                                        "currency": "ARS", "series_last_session": "2026-10-20"}],
                1, "AVAILABLE")
    projection = SimpleNamespace(filters={}, offset=0, history=lambda: page)
    html = render(projection, BY_KEY["analitica"], "anual")
    assert "ANNUAL_NO_SELECTABLE_CANONICAL_SERIES" in html
    assert "production_history" in html
    assert "/analitica/anual?identity=" not in html
