import sys
import types

import rc6_macro_risk_shadow as shadow


def test_collect_compacts_cached_bcra_context(monkeypatch):
    module = types.SimpleNamespace(
        get_macro_context=lambda dias: {
            "indicadores": {
                "reservas_bcra_usd_mn": {
                    "ultimo": 41000,
                    "fecha_ultimo": "2026-09-15",
                    "tendencia": "A_LA_BAJA",
                    "percentil_actual": 12.0,
                    "fuente": "BCRA",
                    "ignored": "not persisted",
                }
            }
        }
    )
    monkeypatch.setitem(sys.modules, "ad_macro_history", module)

    result = shadow.collect()

    assert result["mode"] == "SHADOW"
    assert result["decision_effect"] == "OBSERVE_ONLY"
    assert result["state"] == "READY"
    assert result["indicators"]["reservas_bcra_usd_mn"]["ultimo"] == 41000
    assert "ignored" not in result["indicators"]["reservas_bcra_usd_mn"]


def test_collect_fails_open_for_shadow(monkeypatch):
    module = types.SimpleNamespace(
        get_macro_context=lambda dias: (_ for _ in ()).throw(RuntimeError("cache unavailable"))
    )
    monkeypatch.setitem(sys.modules, "ad_macro_history", module)

    result = shadow.collect()

    assert result["mode"] == "SHADOW"
    assert result["state"] == "UNAVAILABLE"
    assert result["decision_effect"] == "OBSERVE_ONLY"
