import sqlite3

from dd_history_metrics_hf6 import effective_store_metrics, observer_history_metrics, v2_store_metrics


OPERATIONAL = ("ACCIONES", "CEDEARS")


def test_history_metrics_only_count_explicit_operational_families(tmp_path):
    observer = tmp_path / "paper.db"
    history = tmp_path / "market_history.db"
    with sqlite3.connect(observer) as c:
        c.executescript("""
            CREATE TABLE candidate_universe (
              ticker TEXT, instrument_type TEXT, market TEXT, settlement TEXT, status TEXT
            );
            INSERT INTO candidate_universe VALUES
              ('AAPL', 'CEDEARS', 'BCBA', 'CI', 'AVAILABLE'),
              ('YPFD', 'ACCIONES', 'BCBA', 'CI', 'AVAILABLE'),
              ('GD41', 'BONOS', 'BCBA', 'CI', 'AVAILABLE');
        """)
        scoped = observer_history_metrics(c, families=OPERATIONAL)
        assert scoped["target_total"] == 2
        assert scoped["target_by_family"] == {"ACCIONES": 1, "CEDEARS": 1}
    with sqlite3.connect(history) as c:
        c.executescript("""
            CREATE TABLE history_versions_v2 (id INTEGER);
            CREATE TABLE history_canonical_v2 (
              symbol TEXT, instrument_type TEXT, market TEXT, settlement TEXT, date TEXT
            );
            INSERT INTO history_canonical_v2 VALUES
              ('AAPL', 'CEDEARS', 'BCBA', 'CI', '2026-09-15'),
              ('YPFD', 'ACCIONES', 'BCBA', 'CI', '2026-09-15'),
              ('GD41', 'BONOS', 'BCBA', 'CI', '2026-09-15');
        """)
    store = v2_store_metrics(history, families=OPERATIONAL)
    assert store["identities"] == 2
    assert store["canonical_rows"] == 2
    assert set(store["by_family"]) == {"ACCIONES", "CEDEARS"}
    with sqlite3.connect(observer) as c:
        effective = effective_store_metrics(c, path=history, families=OPERATIONAL)
    assert effective["canonical_rows"] == 2


def test_dashboard_passes_operational_scope_to_all_history_summary_metrics():
    source = open("bg_paper_dashboard.py", encoding="utf-8").read()

    assert "observer_history_metrics(c,families=operational_families)" in source
    assert "effective_store_metrics(c,families=operational_families)" in source
    assert "freshness_qualified_metrics(c,families=operational_families)" in source
    assert "Cobertura histórica operativa" in source


def test_dashboard_explains_dual_calendar_for_cedear_freshness():
    source = open("bg_paper_dashboard.py", encoding="utf-8").read()

    assert "Para CEDEAR cada día faltante exige rueda BYMA y rueda del subyacente US." in source
    assert "calendar_source" in source
