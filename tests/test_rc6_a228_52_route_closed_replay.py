"""A228: complete synthetic dashboard crawl independent of market hours.

This checks renderer wiring, not browser interaction or a live first-page
production modal.  A real hosted visual audit remains a separate observation.
"""
from __future__ import annotations

import re

import pytest

from be_paper_engine import PaperStore
from rc6_trader_dashboard import SCHEMA, components
from rc6_trader_dashboard.navigation import CANONICAL_PATHS
from rc6_trader_dashboard.projection import Page
from rc6_trader_dashboard.routes import build_page


@pytest.fixture(scope="module")
def closed_paper_book(tmp_path_factory):
    path = tmp_path_factory.mktemp("a228-52-routes") / "paper.sqlite"
    PaperStore(str(path))
    return str(path)


@pytest.mark.parametrize("path", sorted(CANONICAL_PATHS))
def test_every_canonical_page_renders_offhours_with_empty_paper_book(
        closed_paper_book, path):
    markup, headers = build_page(path, {}, closed_paper_book)
    assert isinstance(markup, str) and len(markup) > 1000
    assert "<!doctype html>" in markup.lower()
    assert headers["X-Porota-Projection"] == SCHEMA
    assert int(headers["X-Porota-Read-Queries"]) >= 0
    assert "Traceback (most recent call last)" not in markup
    assert "Internal Server Error" not in markup


def test_simulated_first_page_has_distinct_accessible_dialog_per_row():
    # Repeat the *same* monetary identity 20 times with separate versions.
    # The UI caps the first page at ten. A row index, not the repeated
    # ticker/market tuple alone, must own all ten displayed dialogs.
    identity = '["AL30","BONOS","BYMA","ARS","A-24HS"]'
    rows = [{"identity": identity, "symbol": "AL30", "last": str(i)}
            for i in range(20)]
    page = Page("SIMULATED_NOT_LIVE", rows, 20, "AVAILABLE")
    markup = components.table(
        page, "Primera página simulada", components.fields("symbol|Ticker;last|Último"),
        table_id="a228-market-first-page")
    ids = re.findall(r"<tr id='([^']+)' data-row='true'>", markup)
    assert len(ids) == len(set(ids)) == 10
    for row_id in ids:
        assert f"data-detail-dialog='{row_id}-dialog'" in markup
        assert f"<dialog id='{row_id}-dialog'" in markup
