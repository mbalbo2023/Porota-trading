from pathlib import Path


def test_history_dashboard_separates_daily_history_from_intraday_candles():
    source=Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    assert "Última barra intradiaria" in source
    assert "Última muestra de mercado" in source
    assert "Worker de velas" in source
    assert "Histórico diario y velas intradiarias son capas distintas" in source
    assert "MAX(bar_end) last_bar" in source
    assert "MAX(event_at) last_event" in source


def test_history_dashboard_exposes_rotation_feasibility_without_increasing_limit():
    source=Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    assert "Foco intradiario" in source
    assert "Rotación universo completo" in source
    assert "rotation_feasible" in source
    assert "no aumentar el lote a ciegas" in source
    assert "último recomendado" in source


def test_history_coverage_uses_same_current_target_scope(tmp_path):
    import sqlite3
    import dd_history_metrics_hf6 as metrics

    observer=sqlite3.connect(":memory:")
    observer.row_factory=sqlite3.Row
    observer.execute("""CREATE TABLE candidate_universe(
      ticker TEXT,instrument_type TEXT,settlement TEXT,market TEXT,
      can_simulate INTEGER,status TEXT,detail TEXT,last_checked_at TEXT)""")
    observer.executemany("INSERT INTO candidate_universe VALUES(?,?,?,?,?,?,?,?)",[
      ("OPT-A","OPCIONES","INMEDIATA","BYMA",1,"AVAILABLE","", "2026-10-02"),
      ("OPT-B","OPCIONES","INMEDIATA","BYMA",1,"AVAILABLE","", "2026-10-02"),
    ])
    db=tmp_path/"history.db"
    h=sqlite3.connect(db)
    h.execute("""CREATE TABLE history_canonical_v2(
      symbol TEXT,instrument_type TEXT,market TEXT,settlement TEXT,
      trading_date TEXT,open TEXT,high TEXT,low TEXT,close TEXT,volume TEXT,
      source_class TEXT,source_ref TEXT,observed_at TEXT,known_at TEXT,
      quality_state TEXT,source_version_id INTEGER,PRIMARY KEY(
      symbol,instrument_type,market,settlement,trading_date))""")
    values=("2026-10-01","1","1","1","1","1","TEST","TEST","2026-10-02","2026-10-02","FULL_OHLC",1)
    h.execute("INSERT INTO history_canonical_v2 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              ("OPT-A","OPCIONES","BYMA","INMEDIATA",*values))
    h.execute("INSERT INTO history_canonical_v2 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              ("OLD-ONLY","OPCIONES","BYMA","INMEDIATA",*values))
    h.commit(); h.close()
    result=metrics.target_store_coverage_metrics(observer,path=db,families=("OPCIONES",))
    family=result["by_family"]["OPCIONES"]
    assert family == {"target":2,"covered_target":1,"historical_identities":2}
    assert result["covered_target_total"] <= result["target_total"]
    observer.close()


def test_history_dashboard_labels_separate_catalog_from_history():
    source=Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    assert "Último intento de historia PPI" in source
    assert "Último éxito de historia PPI" in source
    assert "SIN OBJETIVO ACTUAL" in source
    assert "Cobertura objetivo actual" in source
    assert "Identidades/objetivo" not in source
