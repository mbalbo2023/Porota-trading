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
    assert "Telegram" in health
    assert "Último reporte" in health
    assert "PPI Sandbox" in health
    assert "OPENBYMADATA" in health


def test_inyeccion_del_banner_es_idempotente(monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE", "PRODUCTION_PAPER")
    import bg_paper_dashboard
    bg_paper_dashboard = importlib.reload(bg_paper_dashboard)
    source = "<html><head></head><body><h1>Inicio</h1></body></html>"
    first = bg_paper_dashboard._canonicalize(source)
    second = bg_paper_dashboard._canonicalize(first)
    assert second.count("id='porota-paper-mode'") == 1
    assert second.count("porota-paper-theme") == 1
    assert second.count("id='porota-canonical-nav'") == 1
    assert "Motor de trading" in second
    assert "Blog de aprendizaje" in second
    assert "← Volver" in second


def test_menu_unico_elimina_los_dos_menus_anteriores(monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE", "PRODUCTION_PAPER")
    import bg_paper_dashboard
    bg_paper_dashboard = importlib.reload(bg_paper_dashboard)
    source = ("<html><head></head><body><nav id='porota-top-nav'><a>viejo</a></nav>"
              "<div class='nav'><a>duplicado</a></div><h1>Contenido</h1></body></html>")
    rendered = bg_paper_dashboard._canonicalize(source)
    assert rendered.count("id='porota-canonical-nav'") == 1
    assert "porota-top-nav" not in rendered
    assert "duplicado" not in rendered


def test_menu_legacy_inyectado_despues_del_canonico_tambien_se_elimina(monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE", "PRODUCTION_PAPER")
    import bg_paper_dashboard
    bg_paper_dashboard = importlib.reload(bg_paper_dashboard)
    source = bg_paper_dashboard._document("x", "<h1>x</h1>")
    source = source.replace("<body>", "<body><nav id='porota-top-nav'><a>viejo</a></nav>")
    rendered = bg_paper_dashboard._canonicalize(source)
    assert rendered.count("id='porota-canonical-nav'") == 1
    assert "porota-top-nav" not in rendered


def test_vivo_paper_muestra_observador_y_no_motor_legacy(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE", "PRODUCTION_PAPER")
    monkeypatch.setenv("PAPER_DB_PATH", str(tmp_path / "paper.db"))
    import be_paper_engine
    store = be_paper_engine.PaperStore(str(tmp_path / "paper.db"))
    with store.connect() as connection:
        connection.execute("""UPDATE observer_state SET process_state='RUNNING',
          session_state='MARKET_OPEN',ppi_auth='OK',detail='19/20 instrumentos' WHERE id=1""")
        connection.execute("""INSERT INTO paper_decisions VALUES(NULL,'PRODUCTION_PAPER',
          'paper-momentum-v1','live-1','2026-08-26T14:32:31+00:00','ABEV','HOLD',
          '0.517','Score paper debajo del umbral adaptativo','{"paper_threshold":"0.62"}')""")
    import bg_paper_dashboard
    bg_paper_dashboard = importlib.reload(bg_paper_dashboard)
    rendered = bg_paper_dashboard.live_page()
    assert "Observador paper" in rendered
    assert "19/20 instrumentos" in rendered
    assert "26/08/2026" in rendered
    assert "Bot de trading detenido" not in rendered
    assert "REJECTED_TECH" not in rendered
    assert rendered.count("id='porota-canonical-nav'") == 1


def test_panel_consolidado_y_formato_argentino(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE", "PRODUCTION_PAPER")
    monkeypatch.setenv("PAPER_DB_PATH", str(tmp_path / "paper.db"))
    import be_paper_engine
    be_paper_engine.PaperStore(str(tmp_path / "paper.db"))
    import bg_paper_dashboard
    bg_paper_dashboard = importlib.reload(bg_paper_dashboard)
    page = bg_paper_dashboard.home_page()
    assert "Panel de simulación productiva" in page
    assert "Una sola vista para estado, actividad y simulación" in page
    assert "$ 1.000.000,00" in page
    assert "Próxima actualización" in page
    assert "details[open]" in page
    assert "href='/vivo'" not in page


def test_aprendizaje_es_pagina_principal_visible(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE", "PRODUCTION_PAPER")
    monkeypatch.setenv("PAPER_DB_PATH", str(tmp_path / "paper.db"))
    import be_paper_engine
    be_paper_engine.PaperStore(str(tmp_path / "paper.db"))
    import bg_paper_dashboard
    bg_paper_dashboard = importlib.reload(bg_paper_dashboard)
    page = bg_paper_dashboard.learning_page()
    assert "Aprendizaje del sistema" in page
    assert "Win rate acumulado" in page
    assert "Cómo aprende" in page
    assert "href='/aprendizaje'" in page


def test_motor_explica_hold_y_umbral_sin_exigir_gemini(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE", "PRODUCTION_PAPER")
    monkeypatch.setenv("PAPER_DB_PATH", str(tmp_path / "paper.db"))
    import be_paper_engine
    store = be_paper_engine.PaperStore(str(tmp_path / "paper.db"))
    with store.connect() as connection:
        connection.execute("""INSERT INTO paper_decisions VALUES(NULL,'PRODUCTION_PAPER',
          'paper-momentum-v1','hold-1','2026-08-26T14:32:31+00:00','ABEV','HOLD',
          '0.517','Score paper debajo del umbral adaptativo','{"paper_threshold":"0.62"}')""")
    import bg_paper_dashboard
    bg_paper_dashboard = importlib.reload(bg_paper_dashboard)
    rendered = bg_paper_dashboard.motor_page()
    assert "Decisiones y abstenciones actuales" in rendered
    assert "Gemini se consulta" in rendered
    assert "0.62" in rendered


def test_motor_muestra_trazabilidad_y_explica_porton_gemini(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE", "PRODUCTION_PAPER")
    monkeypatch.setenv("PAPER_DB_PATH", str(tmp_path / "paper.db"))
    import be_paper_engine
    store = be_paper_engine.PaperStore(str(tmp_path / "paper.db"))
    with store.connect() as connection:
        connection.execute("""INSERT INTO paper_decisions VALUES(
          NULL,'PRODUCTION_PAPER','paper-momentum-v1','k1','2026-08-25T14:00:00+00:00',
          'GGAL','BUY','0.74','Momentum positivo','{\"spread\":\"0.01\",\"samples\":8}')""")
        connection.execute("""INSERT INTO paper_positions(
          paper_id,source,strategy_version,symbol,asset_class,settlement,status,quantity,
          entry_price,entry_cost,stop_price,target_price,opened_at,features_json)
          VALUES('PAPER-TEST','PRODUCTION_PAPER','paper-momentum-v1','GGAL','ACCIONES',
          'A-24HS','OPEN','10','100','1','98','103.5','2026-08-25T14:01:00+00:00',
          '{\"spread\":\"0.01\",\"samples\":8}')""")
        connection.execute("""INSERT INTO paper_fills VALUES(
          NULL,'PAPER-TEST','PRODUCTION_PAPER','BUY_SIMULATED','2026-08-25T14:01:00+00:00',
          '10','100','1','0.02')""")
    import bg_paper_dashboard
    bg_paper_dashboard = importlib.reload(bg_paper_dashboard)
    rendered = bg_paper_dashboard.motor_page()
    assert "<details class='paper-trade'>" in rendered
    assert "Variables utilizadas" in rendered
    assert "no tiene evaluación Gemini asociada" in rendered
    assert "Últimos veredictos de Gemini" in rendered
    assert "PENDIENTE" in rendered
    assert "Todas las operaciones de esta página son simuladas" in rendered


def test_colores_de_estado_son_estandar(monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE", "PRODUCTION_PAPER")
    import bg_paper_dashboard
    bg_paper_dashboard = importlib.reload(bg_paper_dashboard)
    assert "--green:" in bg_paper_dashboard.THEME
    assert "--yellow:" in bg_paper_dashboard.THEME
    assert "--red:" in bg_paper_dashboard.THEME
    assert "--gray:" in bg_paper_dashboard.THEME
    assert "#5b21b6" not in bg_paper_dashboard.THEME


def test_home_heredada_queda_moderna_y_sin_menu_duplicado(monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE", "PRODUCTION_PAPER")
    import bg_paper_dashboard
    bg_paper_dashboard = importlib.reload(bg_paper_dashboard)
    source = ("<html><head><style>body{max-width:900px}</style></head><body>"
              "<h1>Inicio</h1><p><a href='/vivo'>Actividad</a> · "
              "<a href='/salud'>Salud</a> · <a href='/config'>Config</a></p>"
              "<table><tr><td>dato</td></tr></table></body></html>")
    rendered = bg_paper_dashboard._canonicalize(source, "/")
    assert rendered.count("id='porota-canonical-nav'") == 1
    assert rendered.count("href='/vivo'") == 0
    assert "id='porota-legacy-shell'" in rendered
    assert "legacy-shell" in rendered


def test_portada_y_logs_tienen_documento_moderno_sin_menu_repetido(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE", "PRODUCTION_PAPER")
    monkeypatch.setenv("PAPER_DB_PATH", str(tmp_path / "paper.db"))
    import be_paper_engine
    be_paper_engine.PaperStore(str(tmp_path / "paper.db"))
    import bg_paper_dashboard
    bg_paper_dashboard = importlib.reload(bg_paper_dashboard)
    for page in (bg_paper_dashboard.home_page(), bg_paper_dashboard.logs_page()):
        assert page.count("id='porota-canonical-nav'") == 1
        assert page.count("id='porota-paper-mode'") == 1
        assert page.count("href='/vivo'") == 0
        assert "porota-paper-theme" in page
    assert "Gestión de logs" in bg_paper_dashboard.logs_page()


def test_gemini_figura_como_porton_critico_y_lee_salud_persistida(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE", "PRODUCTION_PAPER")
    monkeypatch.setenv("PAPER_DB_PATH", str(tmp_path / "paper.db"))
    import be_paper_engine
    import bf_production_paper_observer as observer
    store = be_paper_engine.PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    observer._health(store, "GEMINI_DECISION", "VERDE",
                     "Modelo activo y contrato JSON correcto.", "Google Gemini", success=True)
    import bg_paper_dashboard
    bg_paper_dashboard = importlib.reload(bg_paper_dashboard)
    page = bg_paper_dashboard.health_page()
    assert "Portón crítico de cada compra simulada" in page
    assert "Modelo activo y contrato JSON correcto" in page
    assert "Desactivado en esta versión paper" not in page


def test_clave_error_null_no_convierte_reporte_en_rojo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = {"status": "OK", "error": None, "detail": "correcto"}
    Path("data").mkdir()
    Path("data/informe_api_gemini.json").write_text(
        __import__("json").dumps(data), encoding="utf-8")
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE", "PRODUCTION_PAPER")
    import bg_paper_dashboard
    bg_paper_dashboard = importlib.reload(bg_paper_dashboard)
    state, _detail, _checked, _success = bg_paper_dashboard._report_state("gemini")
    assert state == "VERDE"


def test_semáforo_salud_siempre_usa_etiquetas_estandar(monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE", "PRODUCTION_PAPER")
    import bg_paper_dashboard
    bg_paper_dashboard = importlib.reload(bg_paper_dashboard)
    assert ">ROJO<" in bg_paper_dashboard._health_status("ERROR")
    assert ">AMARILLO<" in bg_paper_dashboard._health_status("PARTIAL")
    assert ">GRIS<" in bg_paper_dashboard._health_status("OFF")


def test_approve_es_verde_y_el_motor_explica_su_efecto(monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE", "PRODUCTION_PAPER")
    import bg_paper_dashboard
    bg_paper_dashboard = importlib.reload(bg_paper_dashboard)
    assert "s-verde'>APPROVE" in bg_paper_dashboard._status("APPROVE")
    source = __import__("inspect").getsource(bg_paper_dashboard.motor_page)
    assert "Efecto real en paper" in source
    assert "Win rate al abrir" in source


def test_historicos_no_expone_boton_manual(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE", "PRODUCTION_PAPER")
    monkeypatch.setenv("PAPER_DB_PATH", str(tmp_path / "paper.db"))
    import be_paper_engine
    be_paper_engine.PaperStore(str(tmp_path / "paper.db"))
    import bg_paper_dashboard
    bg_paper_dashboard = importlib.reload(bg_paper_dashboard)
    page = bg_paper_dashboard.history_page()
    assert "Actualización automática" in page
    assert "Conectar y sincronizar datos" not in page
