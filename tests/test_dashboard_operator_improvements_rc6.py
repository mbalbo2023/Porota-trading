from __future__ import annotations

from datetime import datetime, timezone

import bg_paper_dashboard as bg
import ez_dashboard_operator_improvements_rc6 as ux


def test_latest_five_operational_days_excludes_weekend_and_holiday():
    rows = [
        {"day":"2026-09-07"},
        {"day":"2026-09-06"},  # Sunday
        {"day":"2026-09-05"},  # Saturday
        {"day":"2026-09-04"},
        {"day":"2026-08-17"},  # BYMA holiday
        {"day":"2026-09-03"},
        {"day":"2026-09-02"},
        {"day":"2026-09-01"},
        {"day":"2026-08-31"},
    ]
    got = [r["day"] for r in ux.filter_operational_days(rows, 5)]
    assert got == ["2026-09-07","2026-09-04","2026-09-03","2026-09-02","2026-09-01"]


def test_system_menu_is_horizontal_sticky_and_tables_are_not_cards():
    css = ux.SYSTEM_TOP_NAV_CSS
    assert "flex-direction:row!important" in css
    assert "position:sticky!important" in css
    assert "overflow-x:auto!important" in css
    assert "mobile-cards" not in css


def test_current_market_panel_uses_latest_snapshot_rows(monkeypatch):
    monkeypatch.setattr(bg, "_table", lambda name: name == "market_snapshots")
    monkeypatch.setattr(bg, "_rows", lambda sql, params=(): [{
        "symbol":"CEPU", "asset_class":"ACCIONES", "market":"BYMA",
        "currency":"ARS", "settlement":"A-24HS", "last":"2202",
        "bid":"2201", "ask":"2202", "bid_size":"929", "ask_size":"319",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "book_at": datetime.now(timezone.utc).isoformat(),
        "trade_at": datetime.now(timezone.utc).isoformat(), "last_kind":"TRADE",
    }])
    html = ux._current_market_panel()
    assert "Spot PAPER — mercado actual" in html
    assert "CEPU" in html
    assert "2201" in html and "2202" in html
    assert "ACTUAL" in html
    assert "<table" in html


def test_motor_declutter_targets_are_presentation_only(monkeypatch):
    # Installing this module may replace render helpers, but it must not alter
    # any market/trading engine function. Verify only the five requested panels.
    monkeypatch.setattr(ux, "_installed", False)
    ux.install()
    assert bg._daily_risk_panel() == ""
    assert bg._exit_supervision_panel() == ""
    assert bg._rejection_funnel() == ""
    assert bg._universe_execution_panel() == ""
    assert bg._balances_panel() == ""


def test_static_operator_ux_invariants():
    ux.assert_operator_ux_invariants()
