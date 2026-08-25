import importlib
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_dashboard_paper_no_pide_telegram(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE", "PRODUCTION_PAPER")
    monkeypatch.setenv("PAPER_DB_PATH", str(tmp_path / "paper.db"))
    import be_paper_engine
    be_paper_engine.PaperStore(str(tmp_path / "paper.db"))
    import bg_paper_dashboard
    bg_paper_dashboard = importlib.reload(bg_paper_dashboard)
    page = bg_paper_dashboard.paper_page(compact=True)
    health = bg_paper_dashboard.health_page()
    assert page.count("id='porota-paper-mode'") == 1
    assert "MODO SIMULACIÓN PRODUCTIVA" in page
    assert "órdenes reales: NINGUNA" in page
    assert "Esperando tu autorización" not in page
    assert "Te mandé el pedido por Telegram" not in page
    assert "DESACTIVADO INTENCIONALMENTE" in health


def test_inyeccion_del_banner_es_idempotente(monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE", "PRODUCTION_PAPER")
    import bg_paper_dashboard
    bg_paper_dashboard = importlib.reload(bg_paper_dashboard)
    source = "<html><head></head><body><h1>Inicio</h1></body></html>"
    first = bg_paper_dashboard._inject(source)
    second = bg_paper_dashboard._inject(first)
    assert second.count("id='porota-paper-mode'") == 1
    assert second.count("porota-paper-theme") == 1
