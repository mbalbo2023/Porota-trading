import importlib
import json
import os
import sqlite3
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_v17_dashboard_separa_plazos_y_muestra_caucion_real_del_simulador(tmp_path, monkeypatch):
    from dataclasses import replace
    from be_paper_engine import D, PaperBroker, PaperStore, Quote
    from bt_caucion_paper import CaucionOffer
    import bg_paper_dashboard as dashboard
    store = PaperStore(str(tmp_path / "paper.db"))
    monkeypatch.setattr(dashboard, "DB_PATH", store.path)
    q = Quote("GGAL", "ACCIONES", "INMEDIATA", D(100), D(99), D(101), D(100), D(100), "2026-08-28T11:00:00-03:00", currency="ARS", market="BYMA")
    store.add_quote(q)
    store.add_quote(replace(q, settlement="A-24HS", last=D(102)))
    offer = CaucionOffer("CONTRATO-PRUEBA", "ARS", D("0.365"), "2026-08-28",
                         "2026-08-31T15:00:00-03:00", q.observed_at,
                         D(100000), D(100), D(1), 365, "MATURITY", "TEST_NOT_BROKER",
                         quoted_total_fees=D(1), fee_quote_principal=D(1000))
    broker = PaperBroker(store)
    broker.place_caucion(offer, "1000", "panel", q.observed_at)
    snapshot = dashboard.snapshot()
    assert {row["settlement"] for row in snapshot["quotes"]} == {"INMEDIATA", "A-24HS"}
    assert snapshot["cauciones"][0]["principal"] == "1000"
    page = dashboard.motor_page()
    assert "CONTRATO-PRUEBA" in page
    assert "Cauciones colocadoras" in page
    assert "Capital inmovilizado hasta el vencimiento" in page
    assert "no confirman movimientos en PPI" in page


def test_dashboard_no_suma_dolares_como_pesos_y_muestra_cajas(tmp_path, monkeypatch):
    import bg_paper_dashboard as dashboard
    from be_paper_engine import PaperBroker, PaperStore
    pnl, _, _ = dashboard._trade_metrics([
        {"net_pnl": "100", "currency": "ARS"}, {"net_pnl": "25", "currency": "USD_MEP"},
        {"net_pnl": "20", "currency": "USD_CCL"}])
    assert pnl == 100
    store = PaperStore(str(tmp_path / "paper.db"))
    monkeypatch.setattr(dashboard, "DB_PATH", store.path)
    broker = PaperBroker(store, initial_cash="1000", initial_cash_by_currency={"USD_MEP": "25", "USD_CCL": "50"})
    broker.mark_equity({})
    balances = {r["currency"]: r for r in dashboard.snapshot()["balances_by_currency"]}
    assert balances["ARS"]["equity"] == "1000"
    assert balances["USD_MEP"]["equity"] == "25"
    assert balances["USD_CCL"]["equity"] == "50"
    page = dashboard.motor_page()
    assert "Caja y patrimonio por moneda" in page
    assert "USD_MEP" in page and "USD_CCL" in page


def test_ganancia_en_pesos_no_prueba_superar_inflacion(tmp_path, monkeypatch):
    import bg_paper_dashboard as dashboard
    from be_paper_engine import PaperStore
    import bi_operational_services as services
    store = PaperStore(str(tmp_path / "paper.db"))
    services.init_schema(store)
    monkeypatch.setattr(dashboard, "DB_PATH", store.path)
    with store.connect() as c:
        c.execute("""INSERT INTO paper_positions(paper_id,source,strategy_version,symbol,asset_class,
            settlement,status,quantity,entry_price,entry_cost,stop_price,target_price,opened_at,closed_at,
            exit_price,exit_cost,gross_pnl,net_pnl,features_json)
            VALUES('X','PRODUCTION_PAPER','fixture','GGAL','ACCIONES','INMEDIATA','CLOSED','1','100','1','98','104',
            '2026-08-27T11:00:00-03:00','2026-08-27T12:00:00-03:00','103','1','3','1','{}')""")
        for side,at,price in (('BUY_SIMULATED','2026-08-27T11:00:00-03:00','100'),
                              ('SELL_SIMULATED','2026-08-27T12:00:00-03:00','103')):
            c.execute('INSERT INTO paper_fills VALUES(NULL,?,?,?,?,?,?,?,?)',
                ('X','PRODUCTION_PAPER',side,at,'1',price,'1','0'))
        from bt_caucion_paper import record_sale
        record_sale(c,'X','INMEDIATA','2026-08-27T12:00:00-03:00','102')
    page = dashboard.financial_page()
    assert "SUPERÓ EN PESOS" not in page
    assert "NO COMPARABLE: falta rentabilidad porcentual" in page


def test_dashboard_paper_no_pide_telegram(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE", "PRODUCTION_PAPER")
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(tmp_path / "paper.db"))
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
        assert cliente.get("/api/paper/caucion-allocations").status_code == 401
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
        history = cliente.get('/api/paper/caucion-allocations')
        assert history.status_code == 200
        assert history.json()['state'] == 'EMPTY'
        for query in ('limit=0','limit=101','offset=-1','offset=100001'):
            assert cliente.get('/api/paper/caucion-allocations?'+query).status_code == 422
        assert cliente.post('/api/paper/caucion-allocations').status_code == 405
        from test_production_paper_v1634 import caucion_offer, caucion_policy
        broker = be_paper_engine.PaperBroker(be_paper_engine.PaperStore(db), initial_cash='10000', daily_loss_pct='1')
        original = _decision(broker, [caucion_offer()], caucion_policy())
        response = cliente.get('/api/paper/caucion-allocations')
        assert response.status_code == 200
        assert response.json()['records'][0]['decision'] == original
        assert 'Colocación simulada registrada' in cliente.get('/motor-trading').text
        with broker.store.connect() as c:
            c.execute('DROP TABLE paper_caucion_allocations')
            c.execute('CREATE TABLE paper_caucion_allocations(x)')
        response = cliente.get('/api/paper/caucion-allocations')
        assert response.status_code == 503 and response.json()['state'] == 'READ_ERROR'
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


def test_motor_muestra_trazabilidad_matematica_sin_porton_ia(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE", "PRODUCTION_PAPER")
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(tmp_path / "paper.db"))
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
    assert "la IA no participa de la rueda" in rendered
    assert "Economía matemática" in rendered
    assert "IA Gemini" not in rendered
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
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(tmp_path / "paper.db"))
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


def test_gemini_no_figura_como_api_critica_aunque_haya_salud_historica(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATION_MODE", "PRODUCTION_PAPER")
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(tmp_path / "paper.db"))
    import be_paper_engine
    import bf_production_paper_observer as observer
    store = be_paper_engine.PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    observer._health(store, "GEMINI_DECISION", "VERDE",
                     "Modelo activo y contrato JSON correcto.", "Google Gemini", success=True)
    import bg_paper_dashboard
    bg_paper_dashboard = importlib.reload(bg_paper_dashboard)
    page = bg_paper_dashboard.health_page()
    assert "Portón crítico, seguido del portón patrimonial" not in page
    assert "Modelo activo y contrato JSON correcto" not in page
    assert "Google Gemini" not in page


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
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(tmp_path / "paper.db"))
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


@pytest.fixture
def allocation_case(tmp_path, monkeypatch):
    from test_production_paper_v1634 import caucion_offer, caucion_policy
    from be_paper_engine import PaperBroker, PaperStore
    import bg_paper_dashboard as dashboard
    broker = PaperBroker(PaperStore(str(tmp_path/'allocation.db')), initial_cash='10000', daily_loss_pct='1')
    monkeypatch.setattr(dashboard, 'DB_PATH', broker.store.path)
    return broker, caucion_offer, caucion_policy


def _decision(broker, offers, policy, request='audit'):
    return broker.allocate_caucion(offers, policy, request, as_of='2026-08-28T11:00:00-03:00')


@pytest.mark.parametrize('currency', ['ARS','USD_MEP','USD_CCL'])
@pytest.mark.parametrize('fee_payment', ['MATURITY','UPFRONT'])
def test_historial_concilia_importes_moneda_y_costos(allocation_case, currency, fee_payment):
    from be_paper_engine import PaperBroker
    from cb_caucion_audit import allocation_history
    import bg_paper_dashboard as dashboard
    base, offer, policy = allocation_case
    broker = PaperBroker(base.store, initial_cash='10000', initial_cash_by_currency={currency:'10000'}, daily_loss_pct='1')
    offers = [offer(instrument_id='ELEGIDA',currency=currency,fee_payment=fee_payment),
              offer(instrument_id='TASA_MENOR',currency=currency,annual_rate_fraction='.30'),
              offer(instrument_id='LIBRO_VIEJO',currency=currency,quoted_at='2026-08-28T10:00:00-03:00')]
    decision = _decision(broker, offers, policy(currency=currency,ranking='NET_RETURN_PER_DAY'))
    history = allocation_history(broker.store.path)
    assert history['state'] == 'READABLE'
    record = history['records'][0]
    assert record['decision'] == decision
    assert record['placement']['status'] == 'OPEN'
    assert record['decision']['selected']['instrument_id'] == 'ELEGIDA'
    page = dashboard._caucion_allocations_panel()
    assert 'Caja liquidada evaluada: 10000 '+currency in page
    assert 'Capital colocado: 1000' in page
    assert 'Débito inicial: '+('1001' if fee_payment=='UPFRONT' else '1000') in page
    assert 'Elegible; no elegida por criterio o desempate' in page
    assert 'Cotización vencida o posterior a la decisión' in page
    assert 'no son el saldo actual' in page
    assert 'No certifica datos de PPI' in page
    assert 'Mayor retorno neto por día sobre el débito inicial' in page


def test_historial_hold_no_inventa_colocacion_y_no_reintenta(allocation_case):
    from cb_caucion_audit import allocation_history
    import bg_paper_dashboard as dashboard
    broker, offer, policy = allocation_case
    original = _decision(broker, [offer()], policy(reserve_cash='10000'))
    assert original['status'] == 'HOLD'
    for _ in range(2):
        record = allocation_history(broker.store.path)['records'][0]
        assert record['decision'] == original and record['placement'] is None
        page = dashboard.motor_page()
        assert 'Abstención' in page and 'Sin inversión' in page
        assert 'Excede la fracción disponible después de la reserva' in page
    assert broker.cauciones.positions() == []


@pytest.mark.parametrize('fee_payment', ['UPFRONT','MATURITY'])
def test_panel_muestra_costos_redondeados_del_simulador(allocation_case, fee_payment):
    import bg_paper_dashboard as dashboard
    broker, offer, policy = allocation_case
    decision = _decision(broker, [offer(quoted_total_fees='1.234',fee_payment=fee_payment)], policy())
    assert decision['selected']['fees'] == '1.23'
    page = dashboard._caucion_allocations_panel()
    assert '1.23 · '+fee_payment in page
    assert '1.234 · '+fee_payment not in page


def test_historial_oferta_no_canonica_no_rompe_el_panel(allocation_case):
    from bl_candle_engine import fingerprint
    from cb_caucion_audit import allocation_history
    import bg_paper_dashboard as dashboard
    broker, offer, policy = allocation_case
    decision = _decision(broker, [offer()], policy())
    decision['manifest']['offers'][0]['available_principal'] = '100000.00'
    decision['plan_id'] = fingerprint({k:v for k,v in decision.items() if k not in {'plan_id','paper_id','status'}})
    with broker.store.connect() as c:
        c.execute('UPDATE paper_caucion_allocations SET decision_json=?, request_hash=?',
                  (json.dumps(decision),fingerprint(decision['manifest'])))
    report = allocation_history(broker.store.path)
    assert report['state'] == 'PARTIAL'
    assert report['records'][0]['issue'] == 'NON_CANONICAL_OFFER'
    assert 'Registro inconsistente' in dashboard.motor_page()


def test_historial_maduro_no_confunde_decision_con_saldo_actual(allocation_case):
    from cb_caucion_audit import allocation_history
    import bg_paper_dashboard as dashboard
    broker, offer, policy = allocation_case
    original = _decision(broker, [offer()], policy())
    broker.cauciones.settle_due('2026-09-01T11:00:00-03:00')
    record = allocation_history(broker.store.path)['records'][0]
    assert record['state'] == 'CONSISTENT'
    assert record['decision'] == original
    assert record['placement']['status'] == 'MATURED'
    assert 'Estado guardado: MATURED' in dashboard._caucion_allocations_panel()
    assert 'Acreditación simulada: 01/09/2026 11:00:00' in dashboard._caucion_allocations_panel()


@pytest.mark.parametrize('payload', ['{','[]','null','{"x": NaN}','{"x": Infinity}'])
def test_historial_json_invalido_no_aparece_como_vacio(allocation_case, payload):
    from cb_caucion_audit import allocation_history
    import bg_paper_dashboard as dashboard
    broker, offer, policy = allocation_case
    _decision(broker, [offer()], policy())
    with broker.store.connect() as c:
        c.execute('UPDATE paper_caucion_allocations SET decision_json=?', (payload,))
    history = allocation_history(broker.store.path)
    assert history['state'] == 'PARTIAL' and history['total'] == 1
    assert history['records'][0]['decision'] is None
    page = dashboard._caucion_allocations_panel()
    assert 'Registro inconsistente' in page and 'Capital colocado:' not in page


@pytest.mark.parametrize('sql,issue', [
    ("DELETE FROM paper_cauciones", 'MISSING_PLACEMENT'),
    ("UPDATE paper_cauciones SET principal='999'", 'LEDGER_AMOUNT_MISMATCH'),
    ("UPDATE paper_cauciones SET currency='USD_CCL'", 'LEDGER_TERMS_MISMATCH'),
    ("UPDATE paper_cauciones SET request_id='otra'", 'LEDGER_TERMS_MISMATCH'),
    ("UPDATE paper_cauciones SET total_fees='NaN'", 'valor fuera de rango'),
    ("UPDATE paper_cauciones SET terms_json='{}'", 'INVALID_RECORD_SHAPE'),
    ("UPDATE paper_cauciones SET status='MATURED'", 'LEDGER_STATE_MISMATCH'),
    ("UPDATE paper_cauciones SET status='MATURED',settled_at='2026-08-28T15:00:00-03:00'", 'LEDGER_STATE_MISMATCH'),
    ("UPDATE paper_caucion_allocations SET paper_id=NULL", 'PLACEMENT_LINK_MISMATCH'),
    ("UPDATE paper_caucion_allocations SET evaluated_at='2026-08-28T15:00:00-03:00'", 'EVALUATION_TIME_MISMATCH'),
    ("UPDATE paper_caucion_allocations SET request_hash='otro'", 'REQUEST_MANIFEST_MISMATCH'),
])
def test_historial_detecta_desacuerdos_con_ledger(allocation_case, sql, issue):
    from cb_caucion_audit import allocation_history
    broker, offer, policy = allocation_case
    _decision(broker, [offer()], policy())
    # Simula un registro roto de una restauración/importación externa.
    with sqlite3.connect(broker.store.path) as c:
        c.execute(sql)
    history = allocation_history(broker.store.path)
    record = history['records'][0]
    assert history['state'] == 'PARTIAL'
    assert issue in record['issue']
    assert record['placement'] is None and record['decision'] is None


@pytest.mark.parametrize('field,value', [('cash','Infinity'),('selected',[]),('candidates',{}),
    ('status','HOLD'),('promotion_allowed',True),('policy_version','otra'),('cash_budget','1')])
def test_historial_rechaza_formas_invalidas_aunque_manifiesto_coincida(allocation_case, field, value):
    from bl_candle_engine import fingerprint
    from cb_caucion_audit import allocation_history
    broker, offer, policy = allocation_case
    decision = _decision(broker, [offer()], policy())
    decision[field] = value
    decision['plan_id'] = fingerprint({k:v for k,v in decision.items() if k not in {'plan_id','paper_id','status'}})
    with broker.store.connect() as c:
        c.execute('UPDATE paper_caucion_allocations SET decision_json=?', (json.dumps(decision),))
    assert allocation_history(broker.store.path)['state'] == 'PARTIAL'


def test_historial_pagina_por_instante_y_conserva_registros_validos(allocation_case):
    from cb_caucion_audit import allocation_history
    broker, offer, policy = allocation_case
    # HOLD permite varios eventos sin consumir caja; la zona no cambia el instante.
    for request in ('a','b','c'):
        _decision(broker, [], policy(), request)
    with broker.store.connect() as c:
        c.execute("UPDATE paper_caucion_allocations SET evaluated_at='2026-08-28T11:00:00-03:00' WHERE request_id='c'")
        c.execute("UPDATE paper_caucion_allocations SET decision_json='{}' WHERE request_id='a'")
    page = allocation_history(broker.store.path, limit=2)
    assert page['state'] == 'READABLE' and page['has_more'] and page['total'] == 3
    assert [r['request_id'] for r in page['records']] == ['c','b']
    last = allocation_history(broker.store.path, limit=2, offset=2)
    assert last['state'] == 'PARTIAL' and not last['has_more']
    assert allocation_history(broker.store.path, offset=3)['state'] == 'EMPTY_PAGE'


def test_historial_distingue_faltantes_vacio_y_error_sin_crear_base(tmp_path, monkeypatch):
    from cb_caucion_audit import allocation_history
    from be_paper_engine import PaperBroker, PaperStore
    import bg_paper_dashboard as dashboard
    path = tmp_path/'sin-base.db'
    monkeypatch.setattr(dashboard,'DB_PATH',str(path))
    assert allocation_history(path)['state'] == 'MISSING_DATABASE'
    assert 'Base no disponible' in dashboard.motor_page()
    assert not path.exists()
    with sqlite3.connect(path):
        pass
    assert allocation_history(path)['state'] == 'MISSING_TABLE'
    PaperBroker(PaperStore(str(path)))
    assert allocation_history(path)['state'] == 'EMPTY'
    with sqlite3.connect(path) as c:
        c.execute('DROP TABLE paper_caucion_allocations')
        c.execute('CREATE TABLE paper_caucion_allocations(x)')
    assert allocation_history(path)['state'] == 'READ_ERROR'


def test_historial_y_panel_son_solo_lectura(allocation_case):
    from cb_caucion_audit import allocation_history
    import bg_paper_dashboard as dashboard
    broker, offer, policy = allocation_case
    _decision(broker, [offer()], policy())
    c = broker.store.connect()
    c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    c.close()
    path = Path(broker.store.path)
    before = path.read_bytes()
    assert allocation_history(path)['state'] == 'READABLE'
    assert 'Decisiones de caución' in dashboard.motor_page()
    with dashboard._conn() as c:
        with pytest.raises(sqlite3.OperationalError,match='readonly'):
            c.execute('DELETE FROM paper_cauciones')
    c.close()
    assert path.read_bytes() == before


def test_historial_usa_una_fotografia_aunque_acrediten_durante_lectura(allocation_case, monkeypatch):
    import cb_caucion_audit as audit
    broker, offer, policy = allocation_case
    _decision(broker, [offer()], policy())
    original = audit._validated
    def concurrent(row, connection):
        broker.cauciones.settle_due('2026-09-01T11:00:00-03:00')
        return original(row, connection)
    monkeypatch.setattr(audit, '_validated', concurrent)
    report = audit.allocation_history(broker.store.path)
    assert report['state'] == 'READABLE'
    assert report['records'][0]['placement']['status'] == 'OPEN'
    monkeypatch.setattr(audit, '_validated', original)
    assert audit.allocation_history(broker.store.path)['records'][0]['placement']['status'] == 'MATURED'
