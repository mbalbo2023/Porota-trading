"""A901: monthly PAPER PnL must never attest numbers from malformed ledger data."""

import re
import sqlite3

import pytest

import bg_paper_dashboard as dashboard


def _monthly_html(monkeypatch, tmp_path, rows):
    database = tmp_path / "isolated-monthly.db"
    with sqlite3.connect(database) as connection:
        connection.execute("""CREATE TABLE paper_positions(
            closed_at TEXT, status TEXT, currency TEXT, net_pnl TEXT)""")
        connection.executemany("INSERT INTO paper_positions VALUES (?,?,?,?)", rows)

    def read_only_connection(_path=None):
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(dashboard, "_conn", read_only_connection)
    monkeypatch.setattr(dashboard, "_porota_leaders_proxy", lambda: {
        "returns": [], "average": None, "median": None,
        "breadth": "s/d", "as_of": None,
    })
    html = dashboard.financial_page()

    # The view must never modify source history while masking bad records.
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0] == len(rows)
    return html


def _pnl_cell(page, month):
    match = re.search(rf"<tr><td>{re.escape(month)}</td><td>[^<]*</td><td[^>]*>(.*?)</td>", page)
    assert match, f"Missing monthly PnL row {month}"
    return match.group(1)


@pytest.mark.parametrize("bad_value", [
    "oops", "3 ARS", "2.5oops", "NaN", "Infinity", "-Infinity",
    "1e9999999999", "", "   ", None, sqlite3.Binary(b"2.00"),
])
def test_month_with_any_malformed_pnl_is_unknown_not_partial_sum(monkeypatch, tmp_path, bad_value):
    page = _monthly_html(monkeypatch, tmp_path, [
        ("2026-10-30T18:00:00Z", "CLOSED", "ARS", "3.00"),
        ("2026-10-30T19:00:00Z", "CLOSED", "ARS", bad_value),
        ("2026-10-30T20:00:00Z", "CLOSED", "USD", "oops"),
        ("2026-10-30T21:00:00Z", "OPEN", "ARS", "oops"),
        ("2026-09-30T18:00:00Z", "CLOSED", "ARS", "2.00"),
    ])
    assert _pnl_cell(page, "2026-10") == "NO_VERIFICADO ARS"
    assert _pnl_cell(page, "2026-09") == "$ 2,00 ARS"


def test_valid_decimal_pnl_keeps_zero_and_signed_exact_monthly_sum(monkeypatch, tmp_path):
    page = _monthly_html(monkeypatch, tmp_path, [
        ("2026-10-30T18:00:00Z", "CLOSED", "ARS", "3.00"),
        ("2026-10-30T19:00:00Z", "CLOSED", "ARS", "-0.50"),
        ("2026-10-30T20:00:00Z", "CLOSED", "ARS", "1e2"),
        ("2026-09-30T18:00:00Z", "CLOSED", "ARS", "0.00"),
    ])
    assert _pnl_cell(page, "2026-10") == "$ 102,50 ARS"
    assert _pnl_cell(page, "2026-09") == "$ 0,00 ARS"


def test_strict_monthly_validation_preserves_latest_24_month_limit(monkeypatch, tmp_path):
    rows = []
    for offset in range(25):
        year = 2026 + (9 - offset) // 12
        month = (9 - offset) % 12 + 1
        rows.append((f"{year}-{month:02d}-15T18:00:00Z", "CLOSED", "ARS", "1.00"))
    page = _monthly_html(monkeypatch, tmp_path, rows)
    assert _pnl_cell(page, "2026-10") == "$ 1,00 ARS"
    assert _pnl_cell(page, "2024-11") == "$ 1,00 ARS"
    assert "2024-10</td>" not in page
    assert page.count("NO COMPARABLE: falta rentabilidad porcentual") == 24


def test_corrupt_closed_timestamp_is_visible_as_unknown_not_dropped(monkeypatch, tmp_path):
    page = _monthly_html(monkeypatch, tmp_path, [
        (None, "CLOSED", "ARS", "8.00"),
        ("2026-10-30T18:00:00Z", "CLOSED", "ARS", "3.00"),
    ])
    assert _pnl_cell(page, "2026-10") == "$ 3,00 ARS"
    assert _pnl_cell(page, "—") == "NO_VERIFICADO ARS"
