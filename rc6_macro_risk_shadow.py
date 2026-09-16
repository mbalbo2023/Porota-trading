"""Contexto macro BCRA en modo Shadow para el motor RC6.

Lee exclusivamente la caché SQLite existente en modo read-only. No hace red,
no crea tablas, no modifica decisiones y no puede habilitar órdenes.
"""
from __future__ import annotations

import os
import sqlite3
import statistics
from datetime import date, timedelta
from pathlib import Path

_SERIES = {
    "reservas_bcra_usd_mn": "bcra_reservas_usd_millones",
    "tasa_tamar_privados_tna": "bcra_tasa_tamar_privados_tna",
    "usd_oficial_bcra": "bcra_usd_oficial",
    "ipc_var_mensual_pct": "indec_ipc_var_mensual",
    "actividad_emae": "indec_emae_actividad",
    "dolar_ccl": "dolar_contadoconliqui",
    "dolar_mep": "dolar_bolsa",
    "dolar_blue": "dolar_blue",
}


def _trend(values: list[float]) -> str:
    if len(values) < 2:
        return "INSUFFICIENT_DATA"
    previous = statistics.fmean(values[:-1])
    if not previous:
        return "UNDEFINED"
    change = (values[-1] - previous) / abs(previous) * 100
    return "A_LA_ALZA" if change > 3 else "A_LA_BAJA" if change < -3 else "ESTABLE"


def _percentile(values: list[float]) -> float | None:
    if len(values) < 10:
        return None
    return round(sum(value <= values[-1] for value in values) / len(values) * 100, 1)


def _database_path(db_path: Path | str | None = None) -> Path:
    return Path(db_path or os.getenv("DB_PATH", "data/trading_system.db")).resolve()


def collect(db_path: Path | str | None = None, *, days: int = 180) -> dict:
    """Resume caché macro existente sin inicializar ni escribir SQLite."""
    target = _database_path(db_path)
    if not target.exists() or not target.is_file():
        return {
            "mode": "SHADOW", "state": "UNAVAILABLE",
            "decision_effect": "OBSERVE_ONLY", "source": "BCRA_CACHE",
            "reason": "CACHE_DB_UNAVAILABLE",
        }
    since = (date.today() - timedelta(days=max(1, int(days)))).isoformat()
    try:
        connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=5)
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT serie,fecha,valor,fuente FROM macro_series "
            "WHERE fecha>=? ORDER BY serie,fecha", (since,)
        ).fetchall()
        connection.close()
    except (sqlite3.Error, OSError, ValueError) as exc:
        return {
            "mode": "SHADOW", "state": "UNAVAILABLE",
            "decision_effect": "OBSERVE_ONLY", "source": "BCRA_CACHE",
            "reason": f"{type(exc).__name__}:{str(exc)[:180]}",
        }

    grouped: dict[str, list[tuple[str, float, str]]] = {}
    for serie, observed_at, value, source in rows:
        try:
            grouped.setdefault(str(serie), []).append(
                (str(observed_at), float(value), str(source or "UNKNOWN"))
            )
        except (TypeError, ValueError):
            continue

    indicators = {}
    for name, serie in _SERIES.items():
        samples = grouped.get(serie, [])
        if not samples:
            continue
        values = [sample[1] for sample in samples]
        indicators[name] = {
            "ultimo": values[-1],
            "fecha_ultimo": samples[-1][0],
            "tendencia": _trend(values),
            "percentil_actual": _percentile(values),
            "fuente": samples[-1][2],
        }
    return {
        "mode": "SHADOW",
        "state": "READY" if indicators else "INSUFFICIENT_DATA",
        "decision_effect": "OBSERVE_ONLY",
        "source": "BCRA_AND_OFFICIAL_MACRO_CACHE",
        "indicators": indicators,
        "feature_version": "rc6-macro-risk-shadow-v2-read-only",
    }
