"""
tests/test_economics_costs.py — Tests unitarios de la lógica de costos

POR QUÉ ESTOS TESTS EXISTEN, Y QUÉ ENCONTRARON
---------------------------------------------------------------------------
Escribir estos tests era una de las dos mejoras de impacto alto pendientes.
Al escribirlos apareció, en la primera media hora, un error que llevaba
varias versiones adentro del sistema sin dar ninguna señal: la misma función
de costos se usaba con dos unidades distintas —fracción para calcular plata,
puntos porcentuales para el filtro de rentabilidad— y devolvía siempre la
fracción. El filtro que existe justamente para descartar operaciones que no
le ganan a la fricción venía descontando cien veces menos costo del real.

Eso explica por qué estos tests valen la pena: la lógica de costos no falla
ruidosamente. No tira una excepción, no escribe un error en el log, no rompe
nada. Simplemente devuelve un número plausible que está mal, y se toman
decisiones con ese número durante meses.

Cada test de acá abajo fija una propiedad que tiene que seguir siendo cierta.
No están para "cubrir líneas": están para que el día que alguien toque una
fórmula, algo se ponga en rojo antes de que lo descubra la cuenta.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import d_economics as economics  # noqa: E402
import au_fee_schedule as tarifario  # noqa: E402


TOLERANCIA = 1e-6


# ---------------------------------------------------------------------------
# Escalas: el bug que motivó todo esto
# ---------------------------------------------------------------------------

def test_las_dos_funciones_de_costo_difieren_exactamente_en_un_factor_100():
    """La fracción y el porcentaje tienen que describir el mismo costo.

    Este es EL test: si alguna vez vuelven a divergir, o alguien cambia una y
    no la otra, acá salta."""
    fraccion = economics.calculate_trade_costs_fraction()
    porcentaje = economics.calculate_trade_costs_pct()
    assert porcentaje == pytest.approx(fraccion * 100, abs=1e-4)


def test_el_costo_en_porcentaje_esta_en_la_escala_de_un_retorno():
    """Un costo redondo tiene que estar en el orden de 1 a 2 puntos, no de
    0,01. Si este test falla, el filtro de rentabilidad volvió a comparar
    peras con manzanas."""
    assert 1.0 < economics.calculate_trade_costs_pct() < 3.0


def test_costo_roundtrip_coincide_con_el_modelo_declarado():
    """v16.2 usa el tarifario por clase: acciones tienen derecho BYMA de
    0,05% por tramo, no el valor plano histórico de 0,08%."""
    a = tarifario.ARANCELES["ACCIONES"]
    esperado = 2 * (a.comision * (1 + economics.IVA_PCT)
                    + a.derecho * (1 + economics.IVA_PCT)) * 100
    assert economics.calculate_trade_costs_pct(0.0, "ACCIONES") == \
        pytest.approx(esperado, abs=0.001)


# ---------------------------------------------------------------------------
# Composición del costo
# ---------------------------------------------------------------------------

def test_el_iva_se_aplica_tanto_a_la_comision_como_a_los_derechos():
    """Antes de v16.0 el IVA se aplicaba solo a la comisión. Este test fija
    que los dos componentes lo lleven."""
    a = tarifario.ARANCELES["ACCIONES"]
    esperado_por_tramo = (a.comision * (1 + economics.IVA_PCT)
                          + a.derecho * (1 + economics.IVA_PCT))
    assert economics.calculate_trade_costs_fraction() == pytest.approx(
        esperado_por_tramo * 2, abs=TOLERANCIA)


def test_el_spread_se_suma_en_la_misma_escala_que_el_resto():
    """El spread llega en puntos porcentuales desde j_main. Sumar 1 punto de
    spread tiene que mover el costo en porcentaje exactamente 1 punto."""
    sin_spread = economics.calculate_trade_costs_pct(0.0)
    con_spread = economics.calculate_trade_costs_pct(1.0)
    assert con_spread - sin_spread == pytest.approx(1.0, abs=1e-3)


def test_el_costo_nunca_es_negativo_ni_cero():
    """Operar siempre cuesta algo. Un costo en cero significa que alguna
    variable de entorno quedó vacía y se está evaluando como gratis."""
    assert economics.calculate_trade_costs_fraction() > 0


# ---------------------------------------------------------------------------
# Retorno neto y piso de rentabilidad
# ---------------------------------------------------------------------------

def test_el_neto_descuenta_la_friccion_completa():
    bruto = 2.0
    neto = economics.calculate_net_return_pct(bruto, spread_pct=0.3)
    assert neto == pytest.approx(bruto - economics.calculate_trade_costs_pct(0.3), abs=1e-3)


def test_una_operacion_chica_da_neto_negativo():
    """Medio punto de retorno bruto no alcanza para pagar la ida y la vuelta.
    Antes de la corrección de unidades, esta operación daba neto POSITIVO y
    el sistema la aprobaba."""
    assert economics.calculate_net_return_pct(0.5, spread_pct=0.2) < 0


def test_el_piso_se_prorratea_por_los_dias_de_tenencia():
    """Un hurdle mensual del 3% son 0,5% para cinco días, no 3%."""
    resultado = economics.passes_hurdle(1.0, days_held_estimate=5, hurdle_monthly_pct=3.0)
    assert resultado["hurdle_prorated_pct"] == pytest.approx(0.5, abs=1e-4)
    assert resultado["approved"] is True


def test_el_piso_nunca_prorratea_por_menos_de_un_dia():
    """Con days=0, prorratear daría un piso de cero y aprobaría cualquier
    cosa. La función tiene que forzar el mínimo de un día."""
    resultado = economics.passes_hurdle(0.01, days_held_estimate=0, hurdle_monthly_pct=3.0)
    assert resultado["hurdle_prorated_pct"] > 0
    assert resultado["approved"] is False


def test_el_piso_no_rechaza_por_ser_demasiado_rentable():
    """Es un filtro de piso, no una banda. Un retorno muy alto pasa igual."""
    assert economics.passes_hurdle(50.0, 5, 3.0)["approved"] is True


def test_benchmark_no_mensualiza_tna_aislada_ni_consulta_proxy():
    class Client:
        def get_caucion_rate(self, **kwargs):
            raise AssertionError('No convertir una tasa sin contrato/costos a benchmark mensual')
    assert economics.get_caucion_benchmark_rate(Client()) is None


@pytest.mark.parametrize('ccl', [None, float('nan'), float('inf'), 0.0, 2.0])
def test_piso_incompleto_conserva_ausencias_sin_sustituir_caucion_por_cero(monkeypatch, ccl):
    import json
    monkeypatch.setattr(economics,'load_macro_config',lambda:economics.MacroConfig(3.0,'2026-08-28'))
    monkeypatch.setattr(economics,'get_ccl_devaluation_pct',lambda *_a,**_k:ccl)
    result = economics.get_dynamic_hurdle_rate_monthly(object())
    assert result['state'] == 'INCOMPLETE'
    assert result['hurdle_monthly_pct'] is None
    assert result['caucion_benchmark_monthly_pct'] is None
    assert 'tasa de caución' in result['missing_inputs']
    assert result['partial_floor_monthly_pct'] == 4.5
    assert economics.passes_hurdle(100, 1, result['hurdle_monthly_pct'])['approved'] is False
    json.dumps(result,allow_nan=False)


@pytest.mark.parametrize('net,days,floor', [(1,1,None),(float('inf'),1,3),(1,float('nan'),3),
    (1,1,float('-inf')),(1,1,-1),(True,1,3),(1,-1,3)])
def test_piso_no_aprueba_entradas_invalidas(net,days,floor):
    result = economics.passes_hurdle(net,days,floor)
    assert result['approved'] is False
    assert result['hurdle_prorated_pct'] is None


def test_piso_identifica_estimacion_ia_y_prima_invalidas(monkeypatch):
    monkeypatch.setattr(economics,'load_macro_config',lambda:economics.MacroConfig(3.0,'2026-08-28'))
    monkeypatch.setattr(economics,'get_ccl_devaluation_pct',lambda *_a,**_k:0.0)
    result = economics.get_dynamic_hurdle_rate_monthly(object(),'desconocida',risk_premium_pct=float('nan'))
    assert result['partial_floor_monthly_pct'] is None
    assert {'estimación IA inválida','prima de riesgo'} <= set(result['missing_inputs'])


# ---------------------------------------------------------------------------
# Dimensionamiento de posición
# ---------------------------------------------------------------------------

def test_el_tamano_respeta_el_porcentaje_de_riesgo():
    """La perdida al stop incluye distancia de precio y friccion redonda."""
    qty = economics.calculate_position_size(1_000_000, entry_price=100.0,
                                            stop_loss_price=90.0, risk_pct=1.0)
    perdida_unitaria = 10.0 + 100.0 * economics.calculate_trade_costs_fraction()
    assert qty == int(10_000 / perdida_unitaria)


def test_el_tamano_nunca_supera_el_capital_disponible():
    """Un stop muy cercano permitiría comprar más de lo que se puede pagar.
    El tope por capital tiene que ganar."""
    qty = economics.calculate_position_size(10_000, entry_price=100.0,
                                            stop_loss_price=99.9, risk_pct=1.0)
    assert qty * 100.0 <= 10_000


def test_stop_en_break_even_dimensiona_por_la_perdida_de_costos():
    """Un stop al precio de entrada no es perdida cero: quedan comisiones,
    derechos y spread. El tamano debe respetar ese costo."""
    qty = economics.calculate_position_size(1_000_000, 100.0, 100.0, 1.0)
    costo_unitario = 100.0 * economics.calculate_trade_costs_fraction()
    assert qty == int(10_000 / costo_unitario)


def test_sin_capital_no_se_dimensiona():
    assert economics.calculate_position_size(0, 100.0, 90.0, 1.0) == 0
    assert economics.calculate_position_size(-500, 100.0, 90.0, 1.0) == 0


def test_el_tamano_siempre_trunca_hacia_abajo():
    """Redondear para arriba significa arriesgar más del límite configurado.
    Siempre se trunca: es preferible arriesgar de menos."""
    qty = economics.calculate_position_size(1_000_000, entry_price=100.0,
                                            stop_loss_price=90.7, risk_pct=1.0)
    perdida_unitaria = 9.3 + 100.0 * economics.calculate_trade_costs_fraction()
    assert qty == int(10_000 / perdida_unitaria)
