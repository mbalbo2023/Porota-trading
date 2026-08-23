"""Contrato de compatibilidad con Estadísticas Monetarias v4.0 del BCRA."""

import sys
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import ad_macro_history as macro


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

    macro._fetch_bcra_variable("tasa", 6)

    assert guardado["puntos"] == [("2026-08-21", 12.5)]
