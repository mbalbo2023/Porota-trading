from __future__ import annotations

from pathlib import Path

import bg_paper_dashboard as bg
import fp_dashboard_eod_route_fix_rc6 as route_fix


def test_final_strategy_route_gets_eod_exactly_once(monkeypatch):
    original = bg.trading_page
    original_installed = route_fix._installed
    try:
        monkeypatch.setattr(bg, "trading_page", lambda section="": "<main><h1>Trading — Estrategias y evaluadores</h1></main>")
        monkeypatch.setattr(route_fix.eod_dashboard, "render", lambda: "<div><h2>EOD / Overnight</h2><b>CURRENT_EOD</b><b>OFF</b></div>")
        route_fix._installed = False
        route_fix.install()

        page = bg.trading_page("estrategias")
        assert "id='rc6-eod-overnight-final-route'" in page
        assert page.count("EOD / Overnight") == 1
        assert "CURRENT_EOD" in page

        # The wrapper is idempotent from the operator's point of view.
        page2 = bg.trading_page("estrategias")
        assert page2.count("EOD / Overnight") == 1
    finally:
        bg.trading_page = original
        route_fix._installed = original_installed


def test_non_strategy_routes_are_untouched(monkeypatch):
    original = bg.trading_page
    original_installed = route_fix._installed
    try:
        monkeypatch.setattr(bg, "trading_page", lambda section="": f"<main>{section}</main>")
        monkeypatch.setattr(route_fix.eod_dashboard, "render", lambda: "SHOULD_NOT_RENDER")
        route_fix._installed = False
        route_fix.install()
        page = bg.trading_page("cauciones")
        assert page == "<main>cauciones</main>"
        assert "SHOULD_NOT_RENDER" not in page
    finally:
        bg.trading_page = original
        route_fix._installed = original_installed


def test_install_order_is_post_wave8_and_safety_invariants_remain_static():
    dashboard = Path("o_dashboard.py").read_text(encoding="utf-8")
    assert dashboard.index("zz_wave8_dashboard_live_rc6.install") < dashboard.index("eq_dashboard_table_layout_rc6.install")

    layout = Path("eq_dashboard_table_layout_rc6.py").read_text(encoding="utf-8")
    assert "fp_dashboard_eod_route_fix_rc6" in layout
    assert "eod_route_fix.install()" in layout

    overlay = Path("fp_dashboard_eod_route_fix_rc6.py").read_text(encoding="utf-8").lower()
    for forbidden in ("requests.post(", "requests.put(", "requests.delete(", "send_order(", "place_order("):
        assert forbidden not in overlay

    eod = Path("fo_eod_overnight_dashboard_rc6.py").read_text(encoding="utf-8")
    assert "CURRENT_EOD" in eod
    assert "ACTIVE_PAPER" in eod
    assert "mode=ro" in eod
    assert "PRAGMA query_only=ON" in eod
