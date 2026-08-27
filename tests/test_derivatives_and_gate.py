"""
tests/test_derivatives_and_gate.py — Tests de las funcionalidades nuevas

Cada test fija una propiedad de seguridad: algo que, si dejara de cumplirse,
significaría que el sistema puede tomar una decisión que no sabe evaluar.
"""

import os
import sys
from datetime import date, timedelta
from decimal import Decimal

import pytest
import sqlite3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai_derivatives_engine as deriv  # noqa: E402
import aj_trade_gate as gate  # noqa: E402
from bs_instrument_contracts import InstrumentContract, contract_from_metadata, family_name

HOY = date(2026, 8, 20)


@pytest.mark.parametrize("family,factor,expected", [
    ("ACCIONES", "1", "200"), ("CEDEARS", "1", "200"), ("ETF", "1", "200"),
    ("BONOS", "0.01", "2"), ("LETRAS", "0.01", "2"), ("ON", "0.01", "2"),
    ("FCI", "1", "200"),
])
def test_v17_contrato_conserva_unidades_de_cada_familia(family, factor, expected):
    spec = InstrumentContract("FIXTURE", family, "ARS", "BYMA", "INMEDIATA",
                              Decimal(factor), Decimal("1"), "TEST_NOT_BROKER")
    assert spec.notional("100", "2") == Decimal(expected)
    assert spec.cash_required("100", "2", "0.1") == Decimal(expected) + Decimal("0.1")
    assert spec.pnl("100", "110", "2") == Decimal(expected) / 10


def test_v17_opcion_calcula_prima_y_perdida_por_contrato_no_por_accion():
    spec = InstrumentContract("OPCION-FIXTURE", "OPCIONES", "ARS", "BYMA", "INMEDIATA",
                              Decimal("100"), Decimal("1"), "TEST_NOT_BROKER",
                              expires_at="2026-10-16T15:30:00-03:00", underlying="GGAL",
                              strike=Decimal("7000"), option_right="CALL")
    assert spec.cash_required("20", "3", "60") == 6060
    assert spec.option_max_loss("20", "3", "60") == 6060
    assert spec.pnl("20", "25", "3") == 1500
    with pytest.raises(ValueError, match="política de garantías"):
        spec.cash_required("20", "3", side="SHORT")


def test_v17_futuro_separa_nocional_garantia_y_ajuste_diario():
    spec = InstrumentContract("DLR-FIXTURE", "FUTUROS", "ARS", "A3", "INMEDIATA",
                              Decimal("1000"), Decimal("1"), "TEST_NOT_BROKER",
                              expires_at="2026-10-30T15:00:00-03:00",
                              initial_margin=Decimal("100000"), maintenance_margin=Decimal("80000"))
    assert spec.notional("1400", "2") == 2800000
    assert spec.cash_required("1400", "2", "150") == 200150
    assert spec.daily_variation("1400", "1410", "2") == 20000
    assert spec.daily_variation("1400", "1410", "2", side="SHORT") == -20000
    assert spec.margin_deficit("150000", "2") == 50000
    assert spec.margin_deficit("180000", "2") == 0


@pytest.mark.parametrize("family", ["CAUCIONES", "OPCIONES", "FUTUROS", "FCI", "ON", "LETRAS"])
def test_v17_familia_conocida_no_significa_metadatos_completos(family):
    assert family_name(family)
    with pytest.raises(ValueError, match="metadatos"):
        contract_from_metadata("FIXTURE", family, {"currency": "ARS"})


def test_v17_desconocido_nunca_se_disfraza_de_accion():
    with pytest.raises(ValueError, match="no reconocida"):
        family_name("INSTRUMENTO_NUEVO")
    spec = InstrumentContract("CAUCION", "CAUCIONES", "ARS", "BYMA", "INMEDIATA",
                              Decimal("1"), Decimal("1"), "TEST_NOT_BROKER")
    with pytest.raises(ValueError, match="capital, tasa y plazo"):
        spec.notional("40", "1000")


def _confirmar_strike_api(spec):
    """Simula el dato estructurado que entrega el endpoint del broker.

    Desde v16.2 el strike deducido del ticker es solo informativo y no puede
    habilitar una operacion. Los tests que ejercitan filtros posteriores deben
    confirmar explicitamente la fuente, igual que m_instrument_universe.py.
    """
    spec.strike_source = "api"
    return spec


# ---------------------------------------------------------------------------
# Opciones
# ---------------------------------------------------------------------------

def test_se_interpreta_un_ticker_de_opcion_de_byma():
    spec = deriv.parse_option_ticker("GFGC7000O", today=HOY)
    assert spec.underlying == "GFG"
    assert spec.right == "CALL"
    assert spec.strike == 7000.0
    assert spec.expiry.month == 10


def test_un_ticker_ilegible_no_pasa_como_operable():
    """Si no se puede leer el contrato, no se opera. Nunca se completa con
    valores por default: un strike inventado produce un dimensionamiento
    inventado."""
    spec = deriv.parse_option_ticker("ESTO-NO-ES-UNA-OPCION", today=HOY)
    assert spec.capable is False
    assert spec.blocking_code == "CAPACIDAD_TICKER_ILEGIBLE"


def test_una_opcion_lanzada_nunca_es_operable():
    """Propiedad de seguridad central: la pérdida de un lanzamiento en
    descubierto no tiene cota, así que la fórmula de dimensionamiento no tiene
    solución. No es criterio conservador, es aritmética."""
    spec = deriv.parse_option_ticker("GFGC7000O", today=HOY)
    spec = deriv.assess_option(spec, premium=180.0, underlying_price=7100.0, side="SHORT")
    assert spec.capable is False
    assert spec.blocking_code == "CAPACIDAD_PERDIDA_NO_ACOTADA"


def test_una_opcion_comprada_con_datos_completos_es_operable():
    spec = _confirmar_strike_api(deriv.parse_option_ticker("GFGC7000O", today=HOY))
    spec = deriv.assess_option(spec, premium=180.0, underlying_price=7100.0, spread_pct=2.0)
    assert spec.capable is True
    assert spec.max_loss_per_unit == 180.0 * spec.lot_size


def test_no_se_entra_con_el_vencimiento_encima():
    spec = _confirmar_strike_api(deriv.parse_option_ticker("GFGC7000O", today=HOY))
    spec.days_to_expiry = 3
    spec = deriv.assess_option(spec, premium=180.0, underlying_price=7100.0)
    assert spec.capable is False
    assert spec.blocking_code == "VENCIMIENTO_DEMASIADO_CERCA"


def test_se_descarta_una_opcion_demasiado_fuera_del_dinero():
    spec = _confirmar_strike_api(deriv.parse_option_ticker("GFGC7000O", today=HOY))
    spec = deriv.assess_option(spec, premium=5.0, underlying_price=3000.0)
    assert spec.capable is False
    assert spec.blocking_code == "MONEYNESS_EXCESIVO"


def test_el_tamano_de_una_opcion_respeta_el_riesgo_configurado():
    """Con $2.000.000, 1% de riesgo y una prima de $100 sobre lote de 100, cada
    contrato cuesta $10.000: entran 2."""
    spec = _confirmar_strike_api(deriv.parse_option_ticker("GFGC7000O", today=HOY))
    spec = deriv.assess_option(spec, premium=100.0, underlying_price=7100.0)
    assert deriv.size_long_option(2_000_000, spec, 100.0, risk_pct=1.0) == 2


def test_una_opcion_no_operable_nunca_se_dimensiona():
    spec = deriv.parse_option_ticker("BASURA", today=HOY)
    assert deriv.size_long_option(10_000_000, spec, 1.0, risk_pct=1.0) == 0


def test_se_fuerza_el_cierre_antes_del_vencimiento():
    spec = deriv.parse_option_ticker("GFGC7000O", today=HOY)
    spec.expiry = HOY + timedelta(days=2)
    assert deriv.must_close_for_expiry(spec, today=HOY) is True


# ---------------------------------------------------------------------------
# Futuros
# ---------------------------------------------------------------------------

def test_un_futuro_sin_multiplicador_no_es_operable():
    spec = deriv.describe_future("DLR/OCT26", {})
    assert spec.capable is False
    assert spec.blocking_code == "CAPACIDAD_SIN_MULTIPLICADOR"


def test_un_futuro_sin_garantia_no_es_operable():
    """Tiene multiplicador pero no garantía: se sabe cuánto vale un punto, no
    cuánto capital queda inmovilizado ni cuándo llega una llamada de margen."""
    spec = deriv.describe_future("DLR/OCT26", {"contractMultiplier": 1000})
    assert spec.capable is False
    assert spec.blocking_code == "CAPACIDAD_SIN_DATO_DE_GARANTIA"


def test_un_futuro_completo_es_operable():
    spec = deriv.describe_future("DLR/OCT26", {"contractMultiplier": 1000, "initialMargin": 50000})
    assert spec.capable is True


def test_la_garantia_limita_por_encima_del_riesgo():
    """Si el riesgo permite tres contratos pero la garantía alcanza para uno,
    manda la garantía."""
    spec = deriv.describe_future("DLR/OCT26", {"contractMultiplier": 1000, "initialMargin": 50000})
    qty = deriv.size_future(10_000_000, spec, entry_price=1300.0, stop_price=1290.0,
                            risk_pct=1.0, free_margin_ars=60_000,
                            initial_margin_per_contract=50_000)
    assert qty == 1


# ---------------------------------------------------------------------------
# El portón operativo — la reforma de las noticias
# ---------------------------------------------------------------------------

def test_un_dia_sin_novedades_no_frena_nada():
    """El caso que antes producía un veto total sin que nadie lo notara."""
    r = gate.evaluate_news_context({"feeds_down": False, "no_headlines": True})
    assert r.allow is True
    assert r.size_factor == 1.0


def test_un_apagon_corto_con_macro_fresca_opera_normal():
    r = gate.evaluate_news_context({"feeds_down": True, "no_headlines": True},
                                   macro_age_hours=6, blackout_minutes=20)
    assert r.allow is True
    assert r.size_factor == 1.0
    assert r.hurdle_multiplier == 1.0


def test_un_apagon_prolongado_si_frena():
    r = gate.evaluate_news_context({"feeds_down": True, "no_headlines": True},
                                   macro_age_hours=6, blackout_minutes=500)
    assert r.allow is False
    assert r.code == "APAGON_PROLONGADO"


def test_sin_noticias_y_sin_macro_fresca_se_frena():
    """Sin ninguna fuente de contexto viva, operar es hacerlo a ciegas."""
    r = gate.evaluate_news_context({"feeds_down": True, "no_headlines": True},
                                   macro_age_hours=200, blackout_minutes=10)
    assert r.allow is False


def test_un_evento_de_calendario_bloquea():
    r = gate.evaluate_news_context({"feeds_down": False, "no_headlines": False},
                                   calendar_event_active=True)
    assert r.allow is False
    assert r.code == "EVENTO_DE_CALENDARIO"


def test_el_kill_switch_es_de_nivel_cero():
    r = gate.check_session_health(kill_switch_active=True, broker_session_ok=True,
                                  db_ok=True, market_open=True)
    assert r.allow is False
    assert r.level == gate.NIVEL_PARADA_TOTAL


def test_el_reloj_desfasado_frena_todo():
    """Con el reloj corrido, el chequeo de cotización vieja deja de servir."""
    r = gate.check_session_health(kill_switch_active=False, broker_session_ok=True,
                                  db_ok=True, market_open=True, clock_drift_seconds=600)
    assert r.allow is False
    assert r.code == "RELOJ_DESFASADO"


def test_mercado_cerrado_precede_a_una_sesion_broker_caida():
    """Un domingo se explica como mercado cerrado y no fuerza al bróker."""
    r = gate.check_session_health(kill_switch_active=False, broker_session_ok=False,
                                  db_ok=True, market_open=False)
    assert r.allow is False
    assert r.code == "MERCADO_CERRADO"


def test_recuperar_ordenes_inicializa_una_base_nueva(tmp_path):
    """El primer arranque no cae si todavía nunca hubo una propuesta."""
    import ac_db
    import l_order_confirmation as orders

    original_path = ac_db.DB_PATH
    original_pragmas = ac_db._pragmas_applied
    db = tmp_path / "trading_system.db"
    try:
        ac_db.DB_PATH = str(db)
        ac_db._pragmas_applied = False
        orders.recover_orphaned_orders(object(), object(), object())
        with sqlite3.connect(db) as conn:
            tablas = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        assert "pending_orders" in tablas
    finally:
        ac_db.DB_PATH = original_path
        ac_db._pragmas_applied = original_pragmas


def test_los_limites_de_perdida_no_impiden_cerrar():
    """Propiedad crítica: los límites de riesgo son nivel 1, no nivel 0.
    Prohibir cerrar es la forma más segura de convertir una pérdida chica en
    una grande."""
    nivel, _ = gate.CATALOGO["LIMITE_PERDIDA_DIARIA"]
    assert nivel == gate.NIVEL_NO_ABRIR


def test_el_catalogo_esta_completo_y_ordenado():
    filas = gate.describe_catalog()
    assert len(filas) >= 25
    assert [f["nivel"] for f in filas] == sorted(f["nivel"] for f in filas)


# ---------------------------------------------------------------------------
# Portón de arranque
# ---------------------------------------------------------------------------

def test_el_bot_no_puede_operar_antes_de_ser_autorizado(tmp_path, monkeypatch):
    monkeypatch.setenv("STARTUP_STATE_PATH", str(tmp_path / "st.json"))
    monkeypatch.setenv("TESTING_LOG_PATH", str(tmp_path / "tr.jsonl"))
    import importlib
    import ao_startup_gate as g
    importlib.reload(g)

    g.solicitar_autorizacion(notifier=None)
    assert g.puede_operar() is False
    assert g.esta_en_simulacion() is False


def test_un_codigo_equivocado_no_autoriza(tmp_path, monkeypatch):
    monkeypatch.setenv("STARTUP_STATE_PATH", str(tmp_path / "st.json"))
    import importlib
    import ao_startup_gate as g
    importlib.reload(g)

    g.solicitar_autorizacion(notifier=None)
    assert g.procesar_respuesta_telegram("SIMULAR 00000")["ok"] is False
    assert g.puede_operar() is False


def test_telegram_exige_codigo_y_acepta_simulacion_con_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("STARTUP_STATE_PATH", str(tmp_path / "st.json"))
    monkeypatch.setenv("TESTING_LOG_PATH", str(tmp_path / "tr.jsonl"))
    import importlib
    import ao_startup_gate as g
    importlib.reload(g)

    codigo = g.solicitar_autorizacion(notifier=None)
    assert g.procesar_respuesta_telegram("SIMULAR")["ok"] is False
    assert g.procesar_respuesta_telegram(f"SIMULACIÓN {codigo}")["ok"] is True
    assert g.esta_en_simulacion() is True


def test_en_simulacion_ninguna_orden_sale(tmp_path, monkeypatch):
    monkeypatch.setenv("STARTUP_STATE_PATH", str(tmp_path / "st.json"))
    monkeypatch.setenv("TESTING_LOG_PATH", str(tmp_path / "tr.jsonl"))
    import importlib
    import ao_startup_gate as g
    importlib.reload(g)

    g.autorizar(g.MODO_SIMULACION, origen="test")
    orden = g.interceptar_orden("GGAL", 100, 7250.0, "BUY")
    assert orden is not None
    assert orden["simulada"] is True
    assert orden["id"].startswith("SIM-")


def test_en_modo_real_las_ordenes_no_se_interceptan(tmp_path, monkeypatch):
    monkeypatch.setenv("STARTUP_STATE_PATH", str(tmp_path / "st.json"))
    import importlib
    import ao_startup_gate as g
    importlib.reload(g)

    g.autorizar(g.MODO_REAL, origen="test")
    assert g.interceptar_orden("GGAL", 100, 7250.0, "BUY") is None
