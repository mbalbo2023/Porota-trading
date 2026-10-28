"""
tests/test_v161.py — Tests de las funcionalidades nuevas de v16.1

Cubren las griegas, los comandos de emergencia y el guardián posinferencia.
Cada test fija una propiedad y explica por qué importa.
"""

import math
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import as_greeks_engine as greeks  # noqa: E402
import ai_derivatives_engine as deriv  # noqa: E402

HOY = date(2026, 8, 20)


def _confirmar_strike_api(spec):
    spec.strike_source = "api"
    return spec


# ---------------------------------------------------------------------------
# Volatilidad implícita
# ---------------------------------------------------------------------------

def test_la_volatilidad_implicita_es_reversible():
    """LA propiedad central: si despejo la volatilidad de una prima y después
    valúo con esa volatilidad, tengo que recuperar la misma prima. Si esto se
    rompe, todo lo que dependa de la IV está mal y nada lo avisaría."""
    prima, S, K, T, r = 180.0, 7100.0, 7500.0, 57 / 365, 0.29
    iv = greeks.volatilidad_implicita(prima, S, K, T, r, es_call=True)
    assert iv is not None
    assert greeks.precio_teorico(S, K, T, r, iv, True) == pytest.approx(prima, abs=0.01)


def test_una_prima_por_debajo_de_la_cota_no_devuelve_un_numero_inventado():
    """Con tasas locales altas, la cota inferior europea sube mucho y las
    opciones americanas dentro del dinero pueden cotizar por debajo. Ahí no
    hay solución: devolver el piso del intervalo sería peor que devolver nada."""
    assert greeks.volatilidad_implicita(180.0, 7100.0, 7000.0, 57 / 365, 0.29, True) is None


def test_una_prima_imposiblemente_alta_tampoco_converge():
    assert greeks.volatilidad_implicita(9000.0, 7100.0, 7000.0, 0.1, 0.29, True) is None


def test_delta_de_un_call_esta_entre_cero_y_uno():
    r = greeks.calcular(180.0, 7100.0, 7500.0, 57, True, 0.29, 0.33)
    assert 0.0 <= r.delta <= 1.0


def test_delta_de_un_put_es_negativo():
    """Propiedad de signo: un put gana cuando el subyacente baja."""
    r = greeks.calcular(250.0, 7100.0, 7000.0, 57, False, 0.29, 0.33)
    assert r.delta < 0


def test_theta_siempre_juega_en_contra_del_comprador():
    """El valor temporal se consume con el paso de los días. Un theta positivo
    para una opción comprada significaría que el tiempo la beneficia, que es
    justo al revés."""
    r = greeks.calcular(180.0, 7100.0, 7500.0, 57, True, 0.29, 0.33)
    assert r.theta_diario < 0


def test_una_prima_inflada_se_marca_como_cara():
    barata = greeks.calcular(180.0, 7100.0, 7500.0, 57, True, 0.29, 0.33)
    cara = greeks.calcular(700.0, 7100.0, 7500.0, 57, True, 0.29, 0.33)
    assert cara.volatilidad_implicita > barata.volatilidad_implicita
    assert cara.prima_cara_o_barata.startswith("CARA")
    assert barata.prima_cara_o_barata.startswith("BARATA")


def test_sin_valor_temporal_no_hay_griegas():
    r = greeks.calcular(50.0, 7100.0, 7000.0, 30, True, 0.29)
    assert r.convergio is False
    assert "valor temporal" in r.motivo


# ---------------------------------------------------------------------------
# Lotes y vencimientos
# ---------------------------------------------------------------------------

def test_el_lote_de_un_cedear_es_diez_y_el_de_una_accion_cien():
    """Aplicar 100 a un CEDEAR multiplica por diez la posición y la pérdida
    máxima real frente a la calculada."""
    assert greeks.lote_por_subyacente("CEDEARS") == 10
    assert greeks.lote_por_subyacente("ACCIONES") == 100


def test_el_ticker_de_un_cedear_usa_el_lote_correcto():
    spec = deriv.parse_option_ticker("AAPC1000O", today=HOY, tipo_subyacente="CEDEARS")
    assert spec.lot_size == 10


def test_los_vencimientos_de_renta_variable_son_meses_pares():
    assert all(mes % 2 == 0 for _, mes in greeks.vencimientos_validos(2026))


def test_un_mes_impar_bloquea_hasta_confirmacion_api():
    spec = deriv.parse_option_ticker("GFGC7500E", today=HOY)
    assert spec.capable is False
    assert spec.blocking_code == "CAPACIDAD_VENCIMIENTO_NO_CONFIRMADO"
    assert "impar" in spec.blocking_reason


# ---------------------------------------------------------------------------
# Integración: una prima muy cara descarta la opción
# ---------------------------------------------------------------------------

def test_una_prima_al_doble_de_la_volatilidad_historica_se_descarta(monkeypatch):
    """Se puede acertar la dirección y perder plata igual por haber pagado una
    prima inflada. Es el error más caro y menos evidente de operar opciones."""
    monkeypatch.setattr(deriv, "_volatilidad_historica", lambda x: 0.20)
    spec = _confirmar_strike_api(deriv.parse_option_ticker(
        "GFGC7500O", today=HOY, tipo_subyacente="ACCIONES"))
    spec = deriv.assess_option(spec, premium=700.0, underlying_price=7100.0, spread_pct=1.0)
    assert spec.capable is False
    assert spec.blocking_code == "PRIMA_MUY_CARA_VS_VOLATILIDAD"


def test_si_las_griegas_no_convergen_la_opcion_sigue_siendo_operable(monkeypatch):
    """La prueba de capacidad exige poder calcular la pérdida máxima, y la
    prima la acota siempre. Perder la señal de las griegas degrada la
    información, no la elegibilidad."""
    monkeypatch.setattr(deriv, "_volatilidad_historica", lambda x: None)
    spec = _confirmar_strike_api(deriv.parse_option_ticker(
        "GFGC7500O", today=HOY, tipo_subyacente="ACCIONES"))
    spec = deriv.assess_option(spec, premium=180.0, underlying_price=7100.0, spread_pct=1.0)
    assert spec.capable is True


# ---------------------------------------------------------------------------
# Guardián posinferencia
# ---------------------------------------------------------------------------

def test_el_guardian_revierte_una_aprobacion_con_neto_negativo():
    import t_model_guardian as guardian
    v = guardian.validate_against_catastrophe(
        {"veto_risk": False, "reason_for_voice": "ok"},
        {"net_expected_return_pct": -0.4, "ticker": "GGAL"})
    assert v["veto_risk"] is True
    assert v["guardian_override"] is True


def test_el_guardian_revierte_por_exceso_de_concentracion():
    import t_model_guardian as guardian
    v = guardian.validate_against_catastrophe(
        {"veto_risk": False, "reason_for_voice": "ok"},
        {"net_expected_return_pct": 2.0, "ticker_exposure_pct": 55})
    assert v["veto_risk"] is True


def test_el_guardian_no_toca_una_aprobacion_valida():
    """Un guardián que interviene siempre deja de ser un guardián: pasa a ser
    la regla, y el criterio de la IA deja de importar."""
    import t_model_guardian as guardian
    v = guardian.validate_against_catastrophe(
        {"veto_risk": False, "reason_for_voice": "ok"},
        {"net_expected_return_pct": 2.0, "ticker_exposure_pct": 10})
    assert v["veto_risk"] is False
    assert "guardian_override" not in v


# ---------------------------------------------------------------------------
# Comandos de emergencia
# ---------------------------------------------------------------------------

class NotificadorFalso:
    def __init__(self):
        self.mensajes = []

    def send(self, texto, **kw):
        self.mensajes.append(texto)
        return True


def test_la_parada_de_emergencia_no_frena_nada_en_el_primer_paso(monkeypatch):
    """Mostrar antes de ejecutar no es burocracia: una parada se manda con
    adrenalina, y el peor momento para elegir entre liquidar a mercado y
    esperar los stops es antes de saber cuánta plata hay adentro."""
    import ar_telegram_commands as cmd
    monkeypatch.setattr(cmd, "tomar_foto", lambda ppi=None: cmd.FotoOperativa(
        posiciones=[{"ticker": "GGAL", "quantity": 100, "entry_price": 7000,
                     "stop_loss_price": 6800, "take_profit_price": 7400}],
        capital_expuesto_ars=700000.0))
    n = NotificadorFalso()
    r = cmd.comando_parada_emergencia(n, None)
    assert r["codigo"]
    assert len(n.mensajes) == 1
    for opcion in ("ORDENADA", "LIQUIDAR", "CORTE"):
        assert opcion in n.mensajes[0]


def test_el_corte_seco_explica_el_riesgo_con_numeros_concretos(monkeypatch):
    """«Podés perder plata» no ayuda a decidir. «3 posiciones por $4.200.000
    quedan sin stop» sí."""
    import ar_telegram_commands as cmd
    foto = cmd.FotoOperativa(
        posiciones=[{"ticker": "GGAL", "quantity": 100, "entry_price": 7000,
                     "stop_loss_price": 6800}],
        capital_expuesto_ars=700000.0)
    texto = cmd._riesgo_del_corte_seco(foto)
    assert "700,000" in texto
    assert "sin vigilancia de stop-loss" in texto
    assert "20,000" in texto  # (7000-6800) x 100


def test_un_codigo_vencido_no_ejecuta_una_parada(monkeypatch):
    import time
    import ar_telegram_commands as cmd
    monkeypatch.setattr(cmd, "CONFIRM_TTL_SECONDS", 0)
    with cmd._lock:
        cmd._confirmaciones["12345"] = (None, time.time() - 10, cmd.FotoOperativa())
    assert cmd._codigo_valido("12345") is False


def test_un_comando_desconocido_no_hace_nada():
    import ar_telegram_commands as cmd
    assert cmd.procesar_mensaje("hola qué tal", NotificadorFalso(), None) is None
