"""A341: first-page modal regression over all 52 canonical dashboard routes.

This uses a disposable synthetic SQLite fixture and the real read-only page
renderer. It is NOT a live Droplet, Android Voice Access, or browser acceptance
receipt, and empty fixture pages cannot prove their live first-row modals.
"""

from bs4 import BeautifulSoup
import pytest

from rc6_trader_dashboard.navigation import CANONICAL_PATHS
from rc6_trader_dashboard.routes import build_page
from tests.test_ws_dash_trader_terminal_08 import database as synthetic_database_fixture


@pytest.fixture(scope="module")
def synthetic_database(tmp_path_factory):
    # Reuse the established #467 fixture instead of constructing an unrelated
    # schema that might make an invalid renderer appear healthy.
    return synthetic_database_fixture.__wrapped__(
        tmp_path_factory.mktemp("a341-route-modal")
    )


def _inspect(path, database):
    html, headers = build_page(path, {}, str(database))
    soup = BeautifulSoup(html, "html.parser")
    assert headers["X-Porota-Read-Queries"].isdigit()
    assert soup.select_one("#terminal-content") is not None

    # Every dialog and trigger must refer to one, and only one, valid target.
    dialogs = soup.select("dialog.row-dialog")
    ids = [dialog.get("id") for dialog in dialogs]
    assert all(ids) and len(ids) == len(set(ids)), path
    by_id = {dialog["id"]: dialog for dialog in dialogs}
    triggers = soup.select("button[data-detail-dialog]")
    assert len(triggers) == len(dialogs), path
    for trigger in triggers:
        target = trigger["data-detail-dialog"]
        assert target in by_id, (path, target)
        assert trigger.get("aria-haspopup") == "dialog"
        assert trigger.get("aria-controls") == target
        assert trigger.get_text(strip=True), (path, target)
        dialog = by_id[target]
        title = dialog.get("aria-labelledby")
        assert title and dialog.select_one("[id='" + title + "']"), (path, target)
        assert dialog.select_one("form[method='dialog'] button[type='submit']")
        assert "Cerrar detalle" in dialog.get_text(" ", strip=True)

    rows = soup.select(".terminal-table tr[data-row]")
    assert len(rows) <= 10
    for row in rows:
        assert row.select_one("button.detail-trigger[data-detail-dialog]")
        assert row.select_one("dialog.row-dialog")
        assert row.select_one("td[data-label='Detalle']")
    return len(rows), len(dialogs)


@pytest.mark.parametrize("path", sorted(CANONICAL_PATHS))
def test_first_page_row_modal_or_explicit_no_row(path, synthetic_database):
    """All routes render; real first row is tested wherever fixture has one."""
    assert len(CANONICAL_PATHS) == 52
    rows, dialogs = _inspect(path, synthetic_database)
    # A status-detail dialog is always present when a terminal table exists.
    assert dialogs >= (1 if rows else 0)


def test_coverage_denominator_is_not_silently_called_live_52_of_52(synthetic_database):
    """At least one populated view; empty ones remain unverified for live data."""
    by_route = {path: _inspect(path, synthetic_database) for path in sorted(CANONICAL_PATHS)}
    with_rows = {path: rows for path, (rows, _) in by_route.items() if rows}
    assert len(by_route) == 52
    assert with_rows, "synthetic fixture has no populated pages; modal exercise lost"
    # This is deliberately a denominator, not an assertion that every one of
    # the 52 live pages and every real first-row dialog was observed.
    print(f"A341_SYNTHETIC_ROUTES=52|WITH_FIRST_ROW={len(with_rows)}|"
          f"WITHOUT_FIRST_ROW={52 - len(with_rows)}|RUNTIME_VOICE_ACCESS=NO_VERIFICADO")
