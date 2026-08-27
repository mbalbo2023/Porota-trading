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
    assert "Panel de simulación productiva" in page
    assert "Una sola vista para estado, actividad y simulación" in page
    assert page.count("id='porota-canonical-nav'") == 1
    assert page.count("id='porota-paper-mode'") == 1
    assert "MODO SIMULACIÓN PRODUCTIVA" in page
    assert "órdenes reales: NINGUNA" in page
    assert "Esperando tu autorización" not in page
    assert "Te mandé el pedido por Telegram" not in page
    assert "Telegram" in health
    assert "Último reporte" in health
    assert "PPI Sandbox" in health
    assert "OPENBYMADATA" in health


def test_vivo_esta_enrutado_al_panel_consolidado(tmp_path, monkeypatch):
    # Ejecuta la aplicación real, incluidos los middleware y la autenticación;
    # encontrar el nombre de una ruta en el fuente no valida su respuesta.
    from fastapi.testclient import TestClient
    import ay_dashboard_auth as auth
    import o_dashboard as dashboard
    import bg_paper_dashboard as paper
    import be_paper_engine

    monkeypatch.chdir(tmp_path)
    db = str(tmp_path / "paper.db")
    be_paper_engine.PaperStore(db)
    monkeypatch.setattr(paper, "DB_PATH", db)
    monkeypatch.setattr(paper, "MODE", "PRODUCTION_PAPER")
    monkeypatch.setattr(auth, "TOKEN_BEARER", "R" * 40)
    monkeypatch.setattr(auth, "ENTORNO", "SANDBOX")
    monkeypatch.setattr(auth, "SESSION_STORE_PATH", str(tmp_path / "sessions.json"))
    monkeypatch.setattr(auth, "_sesiones", {})
    monkeypatch.setattr(dashboard, "DASHBOARD_ACCESS_TOKEN", "R" * 40)
    consultas = []

    def query(sql, params=()):
        consultas.append(sql)
        return []

    monkeypatch.setattr(dashboard, "_query", query)
    with TestClient(dashboard.app) as cliente:
        assert cliente.get("/vivo").status_code == 401
        assert cliente.get("/observacion").status_code == 401
        entrada = cliente.get("/vivo?token=" + "R" * 40, follow_redirects=False)
        assert entrada.status_code == 303
        assert entrada.headers["location"] == "/vivo"
        assert "porota_dashboard_session=" in entrada.headers["set-cookie"]
        assert "HttpOnly" in entrada.headers["set-cookie"]
        for path in ("/vivo", "/observacion", "/testing"):
            respuesta = cliente.get(path)
            assert respuesta.status_code == 200
            assert "Panel de simulación productiva" in respuesta.text
            assert "Una sola vista para estado, actividad y simulación" in respuesta.text
            assert respuesta.text.count("id='porota-canonical-nav'") == 1
            assert respuesta.text.count("id='porota-paper-mode'") == 1
            assert "MODO SIMULACIÓN PRODUCTIVA" in respuesta.text
            assert "órdenes reales: NINGUNA" in respuesta.text
            assert "R" * 40 not in respuesta.text
            assert "Esperando tu autorización" not in respuesta.text
    assert consultas
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in consultas)
    assert any("FROM signals" in sql and "__SISTEMA__" in sql for sql in consultas)


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
    assert "Aprendizaje" in second
    assert "← Volver" not in second
    assert "↑ Ir al principio" in second
    assert "Información financiera" in second
    assert "Reportes" in second


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
    assert "Secuencia de portones" in rendered
    assert "Gemini no es el último filtro absoluto" in rendered
    assert "Decisiones bloqueadas o aprobadas" in rendered
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
        assert page.count("href='/motor-trading'") == 1
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
    assert "Portón crítico, seguido del portón patrimonial" in page
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


def test_no_hay_dos_contadores_y_todas_las_paginas_tienen_subir(monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE", "PRODUCTION_PAPER")
    import bg_paper_dashboard
    bg_paper_dashboard = importlib.reload(bg_paper_dashboard)
    page = bg_paper_dashboard.motor_page()
    assert page.count("Actualización visual única") == 1
    assert "Próxima actualización:" not in page
    assert page.count("↑ Ir al principio") == 1
    assert "← Volver" not in page


def test_sre_reportes_finanzas_y_colores_explicitos(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE", "PRODUCTION_PAPER")
    monkeypatch.setenv("PAPER_DB_PATH", str(tmp_path / "paper.db"))
    import be_paper_engine
    store = be_paper_engine.PaperStore(str(tmp_path / "paper.db"))
    import bf_production_paper_observer as observer
    observer._support_schema(store)
    import bg_paper_dashboard
    bg_paper_dashboard = importlib.reload(bg_paper_dashboard)
    assert "Backups y restauración" in bg_paper_dashboard.sre_page("backups")
    assert "Inflación vs performance" in bg_paper_dashboard.financial_page()
    assert "Paquete IA" in bg_paper_dashboard.reports_page()
    assert "card-green" in bg_paper_dashboard.home_page()
    assert "Próximo chequeo" in bg_paper_dashboard.health_page()
