"""Contrato de compatibilidad con Estadísticas Monetarias v4.0 del BCRA."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import ad_macro_history as macro
import as_greeks_engine as greeks


class _Respuesta:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_bcra_v4_usa_endpoint_vigente_y_desanida_detalle(monkeypatch):
    llamada = {}
    guardado = {}

    def get(url, **kwargs):
        llamada["url"] = url
        llamada["kwargs"] = kwargs
        return _Respuesta({
            "status": 200,
            "results": [{
                "idVariable": 1,
                "detalle": [
                    {"fecha": "2026-08-21", "valor": 41000},
                    {"fecha": "2026-08-20", "valor": 40950.5},
                ],
            }],
        })

    def guardar(serie, puntos, fuente):
        guardado.update(serie=serie, puntos=puntos, fuente=fuente)
        return len(puntos)

    monkeypatch.setattr(macro, "_necesita_refresco", lambda serie: True)
    monkeypatch.setattr(macro.requests, "get", get)
    monkeypatch.setattr(macro, "_guardar", guardar)

    filas = macro._fetch_bcra_variable("reservas", 1)

    assert filas == 2
    assert "/estadisticas/v4.0/monetarias/1" in llamada["url"]
    assert guardado == {
        "serie": "bcra_reservas",
        "puntos": [("2026-08-21", 41000.0), ("2026-08-20", 40950.5)],
        "fuente": "BCRA",
    }


def test_bcra_mantiene_compatibilidad_con_respuesta_plana(monkeypatch):
    guardado = {}
    monkeypatch.setattr(macro, "_necesita_refresco", lambda serie: True)
    monkeypatch.setattr(
        macro.requests,
        "get",
        lambda *args, **kwargs: _Respuesta({
            "results": [{"fecha": "2026-08-21", "valor": "12.5"}],
        }),
    )
    monkeypatch.setattr(
        macro,
        "_guardar",
        lambda serie, puntos, fuente: guardado.setdefault("puntos", puntos) or len(puntos),
    )

    macro._fetch_bcra_variable("tasa_tamar_privados_tna", 44)

    assert guardado["puntos"] == [("2026-08-21", 12.5)]


def test_bcra_v4_usa_tamar_privada_tna_vigente():
    assert macro.BCRA_VARIABLES == {
        "reservas_usd_millones": 1,
        "tasa_tamar_privados_tna": 44,
        "base_monetaria": 15,
    }


def test_greeks_lee_tamar_desde_contexto_macro(monkeypatch):
    macro_falso = SimpleNamespace(
        get_macro_context=lambda dias: {
            "indicadores": {
                "tasa_tamar_privados_tna": {"ultimo": 29.5},
            }
        }
    )
    monkeypatch.setitem(sys.modules, "ad_macro_history", macro_falso)

    assert greeks._tasa_desde_macro() == pytest.approx(0.295)
