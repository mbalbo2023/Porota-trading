"""Guardas de configuración declarativa de RC6."""

from collections import Counter
from pathlib import Path
import re

RAIZ = Path(__file__).resolve().parents[1]
_ASIGNACION = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _asignaciones_env_example():
    filas = []
    for numero, linea in enumerate(
        (RAIZ / ".env.example").read_text(encoding="utf-8").splitlines(), 1
    ):
        match = _ASIGNACION.match(linea)
        if match:
            filas.append((match.group(1), numero, linea))
    return filas


def test_env_example_no_declara_ninguna_clave_dos_veces():
    filas = _asignaciones_env_example()
    por_clave = Counter(clave for clave, _numero, _linea in filas)
    duplicadas = {
        clave: [numero for candidata, numero, _linea in filas if candidata == clave]
        for clave, cantidad in por_clave.items()
        if cantidad > 1
    }
    assert not duplicadas


def test_env_example_conserva_la_politica_canonica_de_modelos_y_propuestas():
    valores = {
        clave: linea.partition("=")[2]
        for clave, _numero, linea in _asignaciones_env_example()
    }
    assert valores["GEMINI_MODEL"] == ""
    assert valores["GEMINI_MODEL_CHAIN"] == ""
    assert valores["PROPOSALS_DIR"] == "data/proposals"


def test_metadata_del_dashboard_no_declara_claves_repetidas():
    from v_config_metadata import CONFIG_METADATA
    claves = [fila[0] for fila in CONFIG_METADATA]
    duplicadas = sorted(
        clave for clave, cantidad in Counter(claves).items() if cantidad > 1
    )
    assert not duplicadas
