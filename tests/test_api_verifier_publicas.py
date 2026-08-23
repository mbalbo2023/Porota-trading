"""Contratos del verificador de fuentes públicas.

Estas pruebas evitan que el verificador vuelva a llamar a los fetchers con
firmas obsoletas o marque como correcta una fuente que registró un fallo en
``macro_fetch_log``.
"""

import sqlite3
import sys
from types import SimpleNamespace

import pytest

import ap_api_verifier as verifier


class _DBTemporal:
    def __init__(self, path):
        self.path = path

    def connect(self):
        return sqlite3.connect(self.path)


def _macro_falso(tmp_path, *, fallar=None):
    db = _DBTemporal(tmp_path / "macro.sqlite3")
    llamadas = []

    def init_table():
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS macro_fetch_log (
                    serie TEXT PRIMARY KEY,
                    ultimo_intento TEXT,
                    ultimo_exito TEXT,
                    filas INTEGER,
                    error TEXT
                )
                """
            )

    def registrar(serie, argumentos):
        llamadas.append((serie, argumentos))
        init_table()
        with db.connect() as conn:
            if serie == fallar:
                conn.execute(
                    "INSERT OR REPLACE INTO macro_fetch_log "
                    "(serie, ultimo_intento, ultimo_exito, filas, error) "
                    "VALUES (?, 'ahora', NULL, 0, 'proveedor no disponible')",
                    (serie,),
                )
                return 0
            conn.execute(
                "INSERT OR REPLACE INTO macro_fetch_log "
                "(serie, ultimo_intento, ultimo_exito, filas, error) "
                "VALUES (?, 'ahora', 'ahora', 5, NULL)",
                (serie,),
            )
        return 5

    macro = SimpleNamespace(
        ac_db=db,
        _init_table=init_table,
        BCRA_VARIABLES={"reservas": 1, "tasa": 6, "base": 15},
        DATOS_AR_SERIES={"ipc_var_mensual": "serie-ipc"},
    )
    macro._fetch_bcra_variable = lambda nombre, variable_id: registrar(
        f"bcra_{nombre}", (nombre, variable_id)
    )
    macro._fetch_datos_ar = lambda nombre, serie_id: registrar(
        f"indec_{nombre}", (nombre, serie_id)
    )
    macro._fetch_dolar_historico = lambda casa: registrar(
        f"dolar_{casa}", (casa,)
    )
    return macro, llamadas


def test_fuentes_publicas_usan_firmas_actuales_y_confirman_datos(tmp_path, monkeypatch):
    macro, llamadas = _macro_falso(tmp_path)
    noticias = SimpleNamespace(fetch_latest_headlines=lambda: ["titular"])
    monkeypatch.setitem(sys.modules, "ad_macro_history", macro)
    monkeypatch.setitem(sys.modules, "g_news_feed", noticias)

    vf = verifier.Verificador()
    verifier.verificar_fuentes_publicas(vf)

    assert [resultado.estado for resultado in vf.resultados] == ["OK"] * 4
    assert ("indec_ipc_var_mensual", ("ipc_var_mensual", "serie-ipc")) in llamadas
    assert ("dolar_bolsa", ("bolsa",)) in llamadas
    assert ("dolar_contadoconliqui", ("contadoconliqui",)) in llamadas


def test_fetch_macro_convierte_error_persistido_en_falla(tmp_path):
    macro, _ = _macro_falso(tmp_path, fallar="dolar_bolsa")

    with pytest.raises(RuntimeError, match="proveedor no disponible"):
        verifier._probar_fetch_macro(
            macro,
            "dolar_bolsa",
            lambda: macro._fetch_dolar_historico("bolsa"),
        )
