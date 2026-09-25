"""Invariantes críticas incorporadas por HF5 y preservadas por releases posteriores."""
import importlib
import json
from pathlib import Path


def test_dashboard_resuelve_modo_desde_manifiesto(tmp_path, monkeypatch):
    data=tmp_path/"data"; db=data/"paper_v17"/"observer_v17.db"
    db.parent.mkdir(parents=True)
    monkeypatch.setenv("PAPER_V17_DB_PATH",str(db))
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE","DETENIDO")
    import be_paper_engine
    be_paper_engine.PaperStore(str(db))
    (data/"operation_mode.json").write_text(json.dumps({"mode":"PRODUCTION_PAPER"}),encoding="utf-8")
    import bg_paper_dashboard
    dashboard=importlib.reload(bg_paper_dashboard)
    assert dashboard._effective_mode()=="PRODUCTION_PAPER"
    assert "MODO SIMULACIÓN PRODUCTIVA" in dashboard.mode_banner()
    assert "Configuración del contenedor=DETENIDO" in dashboard.mode_banner()


def test_refresh_es_parcial_y_no_meta_refresh(monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE","PRODUCTION_PAPER")
    monkeypatch.setenv("DASHBOARD_REFRESH_SECONDS","30")
    import bg_paper_dashboard
    dashboard=importlib.reload(bg_paper_dashboard)
    page=dashboard._document("Prueba","<h1>Prueba</h1>")
    assert "http-equiv='refresh'" not in page
    assert "window.setInterval" in page
    assert "details.paper-trade[open]" in page


def test_configuracion_hf5_bloquea_real_y_activa_scalping_paper():
    import porota_mode_manager as mode
    settings=mode.paper_settings({})
    assert settings["PAPER_SCALPING_MODE"]=="ACTIVE_OBSERVE"
    assert settings["PAPER_SCALPING_MODE"]!="ACTIVE_PAPER"
    assert settings["PAPER_SCALPING_RISK_PER_TRADE"]=="0.001"
    assert settings["PAPER_SCALPING_MAX_OPEN_POSITIONS"]=="1"
    assert settings["PPI_BACKGROUND_INGEST_SECONDS"]=="7200"
    assert settings["PAPER_NEWS_INGEST_ENABLED"]=="false"
    for currency in ("USD","USD_MEP","USD_CCL"):
        assert settings[f"PAPER_INITIAL_CAPITAL_{currency}"]=="1000"


def test_guard_clasifica_json_vacio():
    from json import JSONDecodeError
    from bd_ppi_readonly_guard import classify_read_error
    error=JSONDecodeError("Expecting value","",0)
    assert classify_read_error(error)=="PPI_EMPTY_OR_NON_JSON_AFTER_RETRY"


def test_release_identity_and_safety_invariants():
    """HF5's old version string is history; test the invariant, not a stale tag."""
    import _version as version
    assert version.VERSION.startswith("17.0.0-")
    assert version.IMAGE == f"porota-trading-bot:{version.VERSION}"
    assert version.MODE == "PRODUCTION_PAPER"
    assert version.EXECUTION == "SIMULATED"
    assert version.REAL_ORDER_CAPABILITY == "BLOCKED"
