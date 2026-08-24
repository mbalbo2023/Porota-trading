"""
test_propiedades_costos.py — Tests de propiedades (v16.2)

POR QUÉ TESTS DE PROPIEDADES Y NO MÁS CASOS DE EJEMPLO
-------------------------------------------------------
Un test de ejemplo dice "con entrada 100 y stop 97, la cantidad tiene que ser
X". Sirve, pero solo prueba el caso que a alguien se le ocurrió escribir. El
bug más caro de la historia de este proyecto —la misma función devolviendo
fracción donde se esperaba porcentaje, cien veces menos fricción en el filtro
de rentabilidad— no era un caso raro: era TODOS los casos, y no se veía
porque nadie había escrito la propiedad que lo hubiera delatado.

La lógica de costos y de dimensionamiento de este sistema es determinista y
numérica: entra un puñado de números, sale un número. Es el caso ideal para
generación automática de casos límite. Hypothesis genera cientos de
combinaciones por propiedad, incluyendo las que a una persona no se le
ocurren (precios de un centavo, stops pegados a la entrada, capital que
alcanza para media unidad), y cuando encuentra una falla la reduce al caso
mínimo que la reproduce.

CÓMO LEER ESTE ARCHIVO
----------------------
Cada test enuncia una PROPIEDAD que tiene que valer siempre, no un resultado
esperado. "Comprar no genera pérdida diaria" es una propiedad. "Con estos
números da 47" es un ejemplo. Las propiedades sobreviven a los cambios de
implementación; los ejemplos hay que reescribirlos cada vez.
"""

import math
import os
import sys

import pytest
from hypothesis import assume

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings, assume, HealthCheck  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

import d_economics as economics  # noqa: E402
import au_fee_schedule as tarifario  # noqa: E402

PERFIL = settings(max_examples=200, deadline=None,
                  suppress_health_check=[HealthCheck.function_scoped_fixture])

CLASES = st.sampled_from(["ACCIONES", "CEDEARS", "BONOS", "OPCIONES",
                          "FUTUROS", "OBLIGACIONES", "LETRAS"])

precios = st.floats(min_value=0.01, max_value=1_000_000, allow_nan=False,
                    allow_infinity=False)
montos = st.floats(min_value=1.0, max_value=5_000_000_000, allow_nan=False,
                   allow_infinity=False)
spreads = st.floats(min_value=0.0, max_value=15.0, allow_nan=False,
                    allow_infinity=False)


# ===========================================================================
# GRUPO 1 — Las dos unidades de costo
# ===========================================================================
# Esta es LA propiedad que habría atajado el bug histórico. Se escribe primero
# a propósito.

class TestUnidadesDeCosto:

    @PERFIL
    @given(clase=CLASES, spread=spreads)
    def test_porcentaje_es_exactamente_cien_veces_la_fraccion(self, clase, spread):
        """La relación entre las dos unidades es fija y no admite excepción.

        Si algún día alguien "arregla" una de las dos funciones sin tocar la
        otra, esta propiedad se rompe antes de que el filtro de rentabilidad
        empiece a aprobar operaciones que pierden plata.
        """
        fraccion = tarifario.costo_redondo(clase, spread)
        porcentaje = tarifario.costo_redondo_pct(clase, spread)
        assert math.isclose(porcentaje, fraccion * 100, rel_tol=1e-6), (
            f"{clase}: {porcentaje} no es cien veces {fraccion}")

    @PERFIL
    @given(clase=CLASES, spread=spreads)
    def test_la_fraccion_nunca_se_parece_a_un_porcentaje(self, clase, spread):
        """Una fracción de costo por encima de 1.0 significaría que operar
        cuesta más del 100% del monto. Es el síntoma exacto de que alguien
        confundió las unidades en el sentido inverso."""
        assert 0 < tarifario.costo_redondo(clase, spread) < 1.0

    @PERFIL
    @given(clase=CLASES, spread=spreads)
    def test_las_dos_puertas_de_entrada_coinciden(self, clase, spread):
        """d_economics delega en el tarifario. Si la delegación se rompiera,
        el sistema tendría dos modelos de costo distintos según por dónde se
        entre — que es como empezó el problema original."""
        assert math.isclose(
            economics.calculate_trade_costs_fraction(spread, clase),
            tarifario.costo_redondo(clase, spread),
            rel_tol=1e-9)


# ===========================================================================
# GRUPO 2 — Coherencia del tarifario por clase
# ===========================================================================

class TestTarifario:

    @PERFIL
    @given(clase=CLASES, spread=spreads)
    def test_el_costo_crece_con_el_spread(self, clase, spread):
        """Monotonía: más spread nunca puede costar menos."""
        base = tarifario.costo_redondo(clase, 0.0)
        con_spread = tarifario.costo_redondo(clase, spread)
        assert con_spread >= base - 1e-12

    @PERFIL
    @given(spread=spreads)
    def test_los_bonos_cuestan_menos_que_las_acciones(self, spread):
        """No es una preferencia: los títulos públicos tienen la comisión
        exenta de IVA y un derecho de mercado cinco veces menor. Si esta
        propiedad se rompe, el tarifario volvió a ser plano y el sistema está
        descartando operaciones de renta fija que sí eran rentables."""
        assert tarifario.costo_redondo("BONOS", spread) < \
            tarifario.costo_redondo("ACCIONES", spread)

    @PERFIL
    @given(spread=spreads)
    def test_las_opciones_cuestan_mas_que_las_acciones(self, spread):
        """El lado peligroso del costo plano: subestimar el costo de una
        opción aprueba operaciones cuyo neto real es negativo."""
        assert tarifario.costo_redondo("OPCIONES", spread) > \
            tarifario.costo_redondo("ACCIONES", spread)

    @PERFIL
    @given(clase=st.text(min_size=0, max_size=20), spread=spreads)
    def test_una_clase_desconocida_no_rompe_y_no_abarata(self, clase, spread):
        """Ante un tipo que el bróker informe con un nombre nuevo, el sistema
        tiene que seguir funcionando y tiene que errar CARO. Errar por caro
        descarta de más, que no cuesta plata; errar por barato aprueba lo que
        pierde."""
        normalizada = str(clase).strip().upper().replace("_", "").replace("-", "")
        assume(normalizada not in tarifario.ARANCELES)
        assume(normalizada not in tarifario._ALIAS)
        costo = tarifario.costo_redondo(clase, spread)
        assert costo >= tarifario.costo_redondo("BONOS", spread)

    @PERFIL
    @given(monto=montos, dias=st.integers(min_value=1, max_value=365))
    def test_la_caucion_se_prorratea_por_plazo(self, monto, dias):
        """El arancel de caución es ANUAL. Aplicarlo como si fuera de
        transacción sobrestima el costo unas 365 veces y hace que ninguna
        caución pase nunca el filtro: el bot dejaría de colocar liquidez
        ociosa por un error de unidades."""
        costo = tarifario.costo_caucion(monto, dias)
        anual = tarifario.costo_caucion(monto, 365)
        assert 0 <= costo <= anual + 1e-6
        # La funcion devuelve centavos. Para montos diminutos, dos plazos
        # distintos pueden redondear al mismo centavo sin violar el prorrateo.
        if dias < 365 and anual - costo >= 0.01:
            assert costo < anual

    @PERFIL
    @given(monto=montos, clase=CLASES, spread=spreads)
    def test_el_desglose_suma_el_total(self, monto, clase, spread):
        """El panel muestra el detalle línea por línea. Si las líneas no suman
        el total, la reconciliación diaria contra el back-office no se puede
        auditar: no habría forma de saber qué componente se estimó mal."""
        d = tarifario.desglose(clase, monto, spread)
        suma = (d["comision_ars"] + d["iva_comision_ars"] +
                d["derecho_mercado_ars"] + d["iva_derecho_ars"] +
                d["derecho_prima_ars"])
        assert math.isclose(suma, d["total_por_tramo_ars"], rel_tol=1e-6, abs_tol=0.02)
        assert math.isclose(d["total_redondo_ars"],
                            d["total_por_tramo_ars"] * 2 + d["spread_ars"],
                            rel_tol=1e-6, abs_tol=0.02)


# ===========================================================================
# GRUPO 3 — Dimensionamiento
# ===========================================================================

class TestDimensionamiento:

    @PERFIL
    @given(capital=montos, entrada=precios,
           distancia_pct=st.floats(min_value=0.1, max_value=50.0),
           riesgo=st.floats(min_value=0.1, max_value=5.0),
           clase=CLASES)
    def test_nunca_se_arriesga_mas_de_lo_declarado(self, capital, entrada,
                                                   distancia_pct, riesgo, clase):
        """LA propiedad del sistema. Todo el diseño se deriva de "se arriesga
        un porcentaje fijo del capital por operación": si esta no vale, no
        vale ninguna otra.

        La pérdida al stop incluye la fricción de la operación redonda. Antes
        de v16.2 no la incluía, y con un stop a 1 ATR del 3% la pérdida real
        era 4,65% contra 3% dimensionado: un 55% más de riesgo del declarado.
        """
        stop = entrada * (1 - distancia_pct / 100)
        assume(stop > 0)
        cantidad = economics.calculate_position_size(
            capital, entrada, stop, riesgo, spread_pct=0.0, asset_class=clase)
        if cantidad == 0:
            return
        friccion_unitaria = entrada * economics.calculate_trade_costs_fraction(0.0, clase)
        perdida_real = cantidad * ((entrada - stop) + friccion_unitaria)
        permitido = capital * (riesgo / 100)
        # Se admite el redondeo hacia abajo de una unidad, nunca hacia arriba.
        assert perdida_real <= permitido + 1e-6, (
            f"Arriesga {perdida_real:.2f} contra un permitido de {permitido:.2f}")

    @PERFIL
    @given(capital=montos, entrada=precios,
           distancia_pct=st.floats(min_value=0.1, max_value=50.0),
           riesgo=st.floats(min_value=0.1, max_value=5.0))
    def test_la_friccion_reduce_o_iguala_la_cantidad(self, capital, entrada,
                                                     distancia_pct, riesgo):
        """Incorporar la fricción no puede hacer que se compre MÁS. Si lo
        hiciera, el signo estaría invertido en la fórmula."""
        stop = entrada * (1 - distancia_pct / 100)
        assume(stop > 0)
        con_spread = economics.calculate_position_size(
            capital, entrada, stop, riesgo, spread_pct=5.0, asset_class="ACCIONES")
        sin_spread = economics.calculate_position_size(
            capital, entrada, stop, riesgo, spread_pct=0.0, asset_class="ACCIONES")
        assert con_spread <= sin_spread

    @PERFIL
    @given(capital=montos, entrada=precios,
           distancia_pct=st.floats(min_value=0.1, max_value=50.0))
    def test_nunca_se_compra_mas_de_lo_que_alcanza_el_efectivo(
            self, capital, entrada, distancia_pct):
        """La base del riesgo es el patrimonio, pero el tope de compra es el
        EFECTIVO. Son dos cosas distintas y tienen que seguir siéndolo: se
        arriesga un porcentaje de lo que la cuenta vale, pero no se puede
        comprar con plata inmovilizada en otra posición."""
        stop = entrada * (1 - distancia_pct / 100)
        assume(stop > 0)
        efectivo = capital / 3
        cantidad = economics.calculate_position_size(
            capital, entrada, stop, 1.0, asset_class="ACCIONES",
            max_affordable_ars=efectivo)
        assert cantidad * entrada <= efectivo + entrada

    @PERFIL
    @given(capital=montos, entrada=precios, riesgo=st.floats(min_value=0.1, max_value=5.0))
    def test_un_stop_invalido_no_dimensiona(self, capital, entrada, riesgo):
        """Stop por encima de la entrada en una compra: la distancia es
        negativa y la división no tiene sentido. Devolver una cantidad ahí
        sería inventar una posición."""
        assert economics.calculate_position_size(
            capital, entrada, entrada * 1.5, riesgo) == 0

    @PERFIL
    @given(entrada=precios, distancia_pct=st.floats(min_value=0.1, max_value=50.0))
    def test_sin_capital_no_hay_posicion(self, entrada, distancia_pct):
        stop = entrada * (1 - distancia_pct / 100)
        assume(stop > 0)
        assert economics.calculate_position_size(0, entrada, stop, 1.0) == 0
        assert economics.calculate_position_size(-1000, entrada, stop, 1.0) == 0

    @PERFIL
    @given(capital=montos, entrada=precios,
           distancia_pct=st.floats(min_value=0.5, max_value=40.0))
    def test_mas_riesgo_permitido_nunca_compra_menos(self, capital, entrada,
                                                     distancia_pct):
        """Monotonía en el parámetro de riesgo. Una inversión de signo acá
        sería invisible en producción hasta el día que alguien suba el
        porcentaje y compre menos."""
        stop = entrada * (1 - distancia_pct / 100)
        assume(stop > 0)
        chico = economics.calculate_position_size(capital, entrada, stop, 1.0)
        grande = economics.calculate_position_size(capital, entrada, stop, 2.0)
        assert grande >= chico


# ===========================================================================
# GRUPO 4 — Retorno neto
# ===========================================================================

class TestRetornoNeto:

    @PERFIL
    @given(bruto=st.floats(min_value=-50, max_value=200, allow_nan=False),
           spread=spreads, clase=CLASES)
    def test_el_neto_siempre_es_menor_que_el_bruto(self, bruto, spread, clase):
        """No hay operación en la que la fricción sume. Si esta propiedad se
        rompe, el signo del descuento está invertido y el sistema cree ganar
        con los costos."""
        neto = economics.calculate_net_return_pct(bruto, spread, clase)
        assert neto < bruto

    @PERFIL
    @given(bruto=st.floats(min_value=0, max_value=200, allow_nan=False), clase=CLASES)
    def test_el_descuento_es_de_puntos_porcentuales_no_de_centesimas(
            self, bruto, clase):
        """El bug histórico, enunciado con precisión.

        HISTORIA DE ESTA PROPIEDAD, porque ilustra para qué sirven los tests
        de propiedades. La primera versión decía `assert descuento > 0.5`, un
        umbral fijo calibrado mentalmente sobre renta variable (1,6456%
        redondo). Al correrla, falló sobre código sano: LETRAS cuesta 0,402%
        redondo de forma legítima —comisión 0,20% sin IVA y derecho de
        0,001%— y quedaba por debajo del umbral.

        El generador encontró en segundos una clase que a mano no se me
        había ocurrido revisar. La invariante REAL no es "el descuento supera
        medio punto": es que el descuento sea el valor en PUNTOS y no la
        fracción, o sea exactamente cien veces el otro. Esa versión no
        depende de la clase y no hay que recalibrarla cada vez que se agrega
        un arancel al tarifario.

        La tolerancia absoluta cubre el redondeo: calculate_net_return_pct
        redondea a cuatro decimales y costo_redondo a ocho.
        """
        descuento = bruto - economics.calculate_net_return_pct(bruto, 0.0, clase)
        fraccion = tarifario.costo_redondo(clase, 0.0)
        assert math.isclose(descuento, fraccion * 100, rel_tol=1e-3, abs_tol=1e-3), (
            f"{clase}: el descuento {descuento:.6f} no es cien veces la "
            f"fracción {fraccion:.6f}")
        assert descuento > fraccion * 10, (
            f"{clase}: el descuento {descuento:.6f} está en escala de fracción, "
            f"no de puntos porcentuales.")
