"""
tests/test_derivatives_and_gate.py — Tests de las funcionalidades nuevas

Cada test fija una propiedad de seguridad: algo que, si dejara de cumplirse,
significaría que el sistema puede tomar una decisión que no sabe evaluar.
"""

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai_derivatives_engine as deriv  # noqa: E402
import aj_trade_gate as gate  # noqa: E402

HOY = date(2026, 8, 20)


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
