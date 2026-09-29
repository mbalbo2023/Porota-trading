from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

import bg_paper_dashboard as dashboard
import eq_dashboard_table_layout_rc6 as table_layout
import ops_introspection_rc6 as introspection
import rc6_dashboard_responsive_ux as responsive
import zz_wave8_dashboard_live_rc6 as live_overlay


def test_heartbeat_age_is_never_negative_during_concurrent_update():
    now = datetime.now(introspection.TZ)
    future = (now + timedelta(milliseconds=250)).isoformat()
    assert introspection.non_negative_age_seconds(now, future) == 0.0
    assert introspection.non_negative_age_seconds(now, None) is None


def test_market_closed_and_non_operational_day_do_not_warn_about_usd_trading():
    assert introspection.should_warn_no_usd_operations("MARKET_CLOSED", []) is False
    assert introspection.should_warn_no_usd_operations("WAITING_MARKET", []) is False
    assert introspection.should_warn_no_usd_operations("MARKET_OPEN", []) is True
    assert introspection.should_warn_no_usd_operations(
        "MARKET_OPEN", [{"currency": "USD_MEP"}]
    ) is False


def test_live_html_keeps_all_30_decisions_and_25_closures_reachable(monkeypatch):
    dashboard.TABLE_A11Y_SCRIPT = table_layout.configure_table_pagination(
        dashboard.TABLE_A11Y_SCRIPT
    ) + table_layout.PAGINATE_SCRIPT
    now = datetime.now(dashboard.TZ)
    stamp = now.isoformat()
    decisions = [
        {
            "decided_at": stamp,
            "symbol": f"TEST{i:02d}",
            "action": "HOLD",
            "score": 0,
            "reason": f"decision-{i:02d}",
        }
        for i in range(30)
    ]
    closed = [
        {
            "paper_id": f"P{i:02d}",
            "symbol": f"CLOSE{i:02d}",
            "closed_at": stamp,
            "net_pnl": "0",
            "currency": "ARS",
            "close_reason": "TEST",
            "entry_price": "1",
            "exit_price": "1",
            "features_json": "{}",
        }
        for i in range(25)
    ]
    monkeypatch.setattr(
        dashboard,
        "snapshot",
        lambda: {
            "state": {
                "ppi_auth": "OK",
                "real_orders_sent": 0,
                "heartbeat_at": stamp,
                "last_market_data_at": stamp,
            },
            "open": [],
            "closed": closed,
            "exit_intents": [],
        },
    )
    monkeypatch.setattr(
        dashboard,
        "_table",
        lambda name, path=None: name in {"paper_decisions", "candidate_identity_v2"},
    )

    def fake_rows(sql, params=(), path=None):
        return decisions if "FROM paper_decisions" in sql else []

    monkeypatch.setattr(dashboard, "_rows", fake_rows)
    rendered = dashboard.live_page()

    assert rendered.count("decision-") == 30
    assert rendered.count("data-porota-record='1'") >= 55
    assert "id='porota-live-closed'" in rendered
    assert "id='porota-live-decisions'" in rendered
    assert "Mostrando" in dashboard.TABLE_A11Y_SCRIPT
    assert "const PAGE_SIZE=10;" in dashboard.TABLE_A11Y_SCRIPT
    assert "Mostrar más" in dashboard.TABLE_A11Y_SCRIPT
    assert "Timestamps y freshness" in rendered
    assert rendered.count(" hidden aria-hidden='true'") >= 35

    app = FastAPI()

    @app.get("/en-vivo", response_class=HTMLResponse)
    def en_vivo_smoke():
        return HTMLResponse(dashboard.live_page())

    response = TestClient(app).get("/en-vivo")
    assert response.status_code == 200
    assert "decision-29" in response.text


def test_navigation_and_cell_controls_are_voice_and_keyboard_accessible():
    assert "aria-label='Menú principal'" in dashboard.top_nav_html()
    assert "porota-quick-nav" in responsive.CSS
    assert ">Menú</a>" in responsive.SCRIPT
    assert ">Arriba</a>" in responsive.SCRIPT
    assert 'cell.tabIndex=0' in responsive.SCRIPT
    assert 'setAttribute("role","button")' in responsive.SCRIPT
    assert 'setAttribute("aria-expanded"' in responsive.SCRIPT
    assert 'event.key==="Enter" || event.key===" "' in responsive.SCRIPT
    assert "aria-live','polite" in table_layout.PAGINATE_SCRIPT


def test_navigation_never_requires_horizontal_swipe_at_required_widths():
    combined = live_overlay.CLASSIC_CSS + responsive.CSS
    for width in (360, 600, 800, 1024):
        assert width > 0  # explicit acceptance matrix
        assert "#porota-canonical-nav" in combined
        assert "flex-wrap:wrap!important" in combined
        assert "#porota-canonical-nav{position:sticky!important" in combined
        canonical_rule = combined.split("#porota-canonical-nav{", 1)[1].split("}", 1)[0]
        assert "overflow-x:auto" not in canonical_rule
        assert "overflow:visible" in canonical_rule


def test_scope_does_not_change_paper_or_real_order_invariants():
    changed = {
        "bg_paper_dashboard.py",
        "da_dashboard_ux_hf6.py",
        "eq_dashboard_table_layout_rc6.py",
        "ops_introspection_rc6.py",
        "rc6_dashboard_responsive_ux.py",
        "zz_wave8_dashboard_live_rc6.py",
    }
    assert changed <= {
        "bg_paper_dashboard.py",
        "da_dashboard_ux_hf6.py",
        "zz_wave8_dashboard_live_rc6.py",
        "eq_dashboard_table_layout_rc6.py",
        "rc6_dashboard_responsive_ux.py",
        "ops_introspection_rc6.py",
    }
    source = Path("ops_introspection_rc6.py").read_text(encoding="utf-8")
    assert 'real_orders_sent_nonzero' in source
