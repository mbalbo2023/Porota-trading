import ez_iol_shadow_validation_view_rc6 as view


def test_view_states_observation_only_when_cache_is_missing(monkeypatch):
    monkeypatch.setattr(view.observation, "collect", lambda: {
        "source": "IOL_MCP", "mode": "SHADOW", "state": "UNAVAILABLE",
        "decision_effect": "OBSERVE_ONLY", "live_decision_authority": False,
        "real_money_authorized": False, "symbols": [],
        "reason": "CACHE_MISSING_OR_INVALID",
    })

    html = view.render()

    assert "IOL — evidencia SHADOW" in html
    assert "PPI conserva la autoridad primaria" in html
    assert "no puede cambiar READY/HOLD" in html
    assert "Collector pendiente" in html
    assert "CACHE_MISSING_OR_INVALID" not in html


def test_view_renders_bounded_escaped_cached_rows(monkeypatch):
    rows = [{
        "symbol": "<YPFD>", "market": "BCBA", "state": "READY",
        "quote": {"last": 100, "spread_pct": 1.5},
        "primary_comparison": "BACKGROUND_DIVERGENCE",
        "reason": "<untrusted>",
    }] * 25
    monkeypatch.setattr(view.observation, "collect", lambda: {
        "source": "IOL_MCP", "mode": "SHADOW", "state": "READY",
        "decision_effect": "OBSERVE_ONLY", "refreshed_at": "2026-09-17T12:00:00Z",
        "symbols": rows,
    })

    html = view.render()

    assert html.count("&lt;YPFD&gt;") == view.MAX_ROWS
    assert "&lt;untrusted&gt;" in html
    assert "<untrusted>" not in html
    assert "0 alineados · 25 divergentes · 0 sin base comparable" in html
    assert "Aporte de IOL al análisis del motor" in html
    assert "Efecto en el motor" in html
    assert "Divergencia = diagnóstico background, nunca HOLD/READY" in html


def test_view_never_uses_refresh_or_trading_authority(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("render must remain cache-only")

    monkeypatch.setattr(view.observation, "refresh", forbidden)
    monkeypatch.setattr(view.observation, "collect", lambda: {
        "source": "IOL_MCP", "mode": "SHADOW", "state": "READY",
        "decision_effect": "OBSERVE_ONLY", "symbols": [],
    })

    html = view.render()

    assert "OBSERVE_ONLY" in html
    assert "órdenes" in html


def test_view_exposes_iol_decision_context_and_per_symbol_field_coverage(monkeypatch):
    from datetime import datetime, timezone
    captured = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(view.observation, "collect", lambda: {
        "source": "IOL_MCP", "mode": "SHADOW", "state": "READY",
        "decision_effect": "OBSERVE_ONLY", "refreshed_at": captured,
        "progress": {"scheduled": 1, "completed": 1, "fresh": 1, "universe_source": "OBSERVER_OPERATIONAL_CATALOG"},
        "symbols": [{
            "symbol": "GGAL", "market": "BCBA", "state": "READY", "captured_at": captured,
            "asset_type": "ACCIONES", "currency": "ARS", "units_per_lot": 1,
            "quote": {"last": 100, "bid": 99, "ask": 101, "spread_pct": 2.02,
                      "variation_pct": 1.5, "cash_volume": 250000},
        }],
    })
    html = view.render()
    assert "ACCIONES · ARS · lote 1.00" in html
    assert "Último 100.00" in html and "Bid 99.00 · Ask 101.00" in html
    assert "Spread 2.02% · Var. 1.50%" in html and "Vol. dinero 250,000.00" in html
    assert "9/9 campos IOL" in html
    assert "frescos para decisión: 1/1" in html
    assert "no altera señales ni gates PAPER" in html
