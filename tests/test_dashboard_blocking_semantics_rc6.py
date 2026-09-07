from __future__ import annotations

import importlib


def test_nonblocking_yellow_keeps_yellow(monkeypatch):
    import es_dashboard_go_live_ux_rc6 as go_live
    import eu_dashboard_blocking_semantics_rc6 as semantics
    go_live = importlib.reload(go_live)
    semantics = importlib.reload(semantics)
    go_live._original["health_components"] = lambda: [
        {"key":"PPI_PRODUCTION_HISTORY","state":"AMARILLO","detail":"parcial","applicable":True},
        {"key":"PPI_PRODUCTION_AUTH","state":"VERDE","detail":"ok","applicable":True},
        {"key":"TELEGRAM","state":"ROJO","detail":"bad","applicable":True},
    ]
    rows = semantics._truth_with_blocking_semantics()
    assert rows[0]["state"] == "AMARILLO"
    assert rows[0]["paper_blocking"] is False
    assert "No bloquea por sí solo" in rows[0]["detail"]
    assert rows[1]["state"] == "VERDE"
    assert rows[1]["paper_blocking"] is True
    assert rows[2]["state"] == "ROJO"
    assert rows[2]["paper_blocking"] is True
