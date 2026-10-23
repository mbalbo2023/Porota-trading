from types import SimpleNamespace

from rc6_trader_dashboard.components import fields, table


def page(row):
    return SimpleNamespace(rows=[row], total=1, offset=0, source="api_health", state="AVAILABLE", reason="", as_of=None)


def test_service_drawer_omits_inapplicable_instrument_identity_but_retains_check_state():
    row = {"component": "PAPER_ECONOMIC_GATE_SHADOW", "state": "NO_VERIFICADO",
           "observed_state": "VERDE", "check_freshness": "STALE",
           "checked_age_seconds": 184.6, "source": "api_health"}
    html = table(page(row), "Integraciones", fields("component|Componente;state|Estado|status"),
                 fields("observed_state|Último estado registrado|status;check_freshness|Frescura del chequeo|status;checked_age_seconds|Edad del chequeo (s)|number"),
                 identity=False)
    for label in ("Ticker", "Familia", "Mercado", "Moneda", "Settlement"):
        assert f"<dt>{label}</dt>" not in html
    assert "<dt>Último estado registrado</dt>" in html
    assert "<dt>Frescura del chequeo</dt>" in html
    assert "STALE" in html and "NO_VERIFICADO" in html


def test_instrument_drawer_retains_exact_identity_by_default():
    row = {"symbol": "AL30", "family": "BONOS", "market": "BYMA",
           "currency": "ARS", "settlement": "CI"}
    html = table(page(row), "Instrumentos", fields("symbol|Ticker"))
    for label in ("Ticker", "Familia", "Mercado", "Moneda", "Settlement"):
        assert f"<dt>{label}</dt>" in html
