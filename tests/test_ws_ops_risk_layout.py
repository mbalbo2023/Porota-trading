import zz_wave8_dashboard_live_rc6 as live


def test_risk_page_overrides_global_ellipsis_for_operator_evidence(monkeypatch):
    def rows(sql, *_args, **_kwargs):
        if "observer_state" in sql:
            return [{
                "mode": "PRODUCTION_PAPER",
                "process_state": "WAITING_MARKET",
                "session_state": "MARKET_CLOSED",
                "ppi_auth": "OK",
                "real_orders_sent": 0,
                "heartbeat_at": "2026-09-30T23:00:00+00:00",
            }]
        if "sqlite_master" in sql:
            return [{"name": "daily_risk"}, {"name": "paper_events"}, {"name": "trade_gate_evaluations"}]
        if "COUNT(*)" in sql:
            return [{"n": 3}]
        return []

    monkeypatch.setattr(live.bg, "_rows", rows)
    monkeypatch.setattr(live.bg, "top_nav_html", lambda: "<nav></nav>")
    monkeypatch.setattr(live, "_learning_section", lambda: "<section id='learning'></section>")

    html = live._risk_html()

    assert "paper-page porota-risk-page" in html
    assert "porota-rc6-risk-readable" in html
    assert "table-layout:auto!important" in html
    assert "max-width:none!important" in html
    assert "white-space:normal!important" in html
    assert "overflow-wrap:anywhere!important" in html
    assert "data-wrap='true'" in html
    assert "PRODUCTION_PAPER" in html
    assert ">0<" in html


def test_risk_specific_css_does_not_remove_global_table_safety_contract():
    assert "max-width:0!important" in live.CLASSIC_CSS
    assert "white-space:nowrap!important" in live.CLASSIC_CSS
    assert "max-width:none!important" in live.RISK_READABLE_CSS
    assert "white-space:normal!important" in live.RISK_READABLE_CSS
