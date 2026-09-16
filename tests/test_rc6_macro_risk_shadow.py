import sqlite3

import rc6_macro_risk_shadow as shadow


def _seed(path):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE macro_series (serie TEXT, fecha TEXT, valor REAL, fuente TEXT)"
        )
        for day, value in enumerate(range(40000, 41000, 100), start=1):
            connection.execute(
                "INSERT INTO macro_series VALUES (?,?,?,?)",
                ("bcra_reservas_usd_millones", f"2026-09-{day:02d}", value, "BCRA"),
            )


def test_collect_compacts_cached_bcra_context_read_only(tmp_path):
    database = tmp_path / "macro.db"
    _seed(database)

    result = shadow.collect(database, days=3650)

    assert result["mode"] == "SHADOW"
    assert result["decision_effect"] == "OBSERVE_ONLY"
    assert result["state"] == "READY"
    assert result["indicators"]["reservas_bcra_usd_mn"]["ultimo"] == 40900.0
    assert result["feature_version"].endswith("read-only")


def test_collect_does_not_create_missing_cache(tmp_path):
    database = tmp_path / "missing.db"

    result = shadow.collect(database)

    assert result["mode"] == "SHADOW"
    assert result["state"] == "UNAVAILABLE"
    assert result["decision_effect"] == "OBSERVE_ONLY"
    assert not database.exists()


def test_trading_dashboard_exposes_macro_shadow_without_trading_authority():
    source = open("bg_paper_dashboard.py", encoding="utf-8").read()

    assert "Riesgo macro BCRA — SHADOW" in source
    assert "no bloquea, no cambia tamaño y no habilita órdenes" in source
