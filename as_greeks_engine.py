"""
as_greeks_engine.py — Volatilidad implícita y griegas para opciones de BYMA

EL PENDIENTE ESTABA MAL PLANTEADO
---------------------------------------------------------------------------
La versión anterior dejó anotado que no se podían calcular griegas porque no
existía una fuente gratuita y confiable de volatilidad implícita para el
mercado local. Al investigarlo en serio, el planteo estaba invertido.

La volatilidad implícita no es un dato que haya que buscar afuera: es un dato
que se DESPEJA de la prima que el mercado ya está pagando. El modelo de
Black-Scholes toma cinco entradas —precio del subyacente, strike, tiempo al
vencimiento, tasa libre de riesgo y volatilidad— y devuelve una prima
teórica. Si uno tiene la prima real y le faltan la volatilidad, invierte la
fórmula y despeja: eso ES la volatilidad implícita. Literalmente significa
"la volatilidad implícita en el precio que se está pagando".

Y las cinco entradas ya las tiene el sistema:

  · precio del subyacente ....... PPI, en tiempo real
  · strike y vencimiento ........ del contrato
  · tasa libre de riesgo ........ la caución, que ya está en el contexto macro
  · prima ....................... PPI, en tiempo real

No hace falta ningún proveedor externo, ninguna suscripción y ninguna
credencial nueva. Solo aritmética sobre datos que ya llegan. El pendiente
queda cerrado, y no por conseguir una fuente sino por darse cuenta de que no
hacía falta.

LO QUE ESTO HABILITA, QUE ES LO QUE IMPORTA
---------------------------------------------------------------------------
Tener la volatilidad implícita permite responder la pregunta que un operador
de opciones se hace antes que cualquier otra: **¿esta prima está cara o
barata?** Y se responde comparándola contra la volatilidad que el subyacente
efectivamente tuvo:

  · IV bastante por ENCIMA de la histórica → la prima está cara. El mercado
    está cobrando por un movimiento mayor al que el papel viene teniendo.
    Comprar ahí es pagar de más aunque uno acierte la dirección.

  · IV por DEBAJO de la histórica → la prima está barata en términos
    relativos. Es el terreno donde comprar opciones tiene sentido.

Sin esta comparación, el sistema podía acertar la dirección del subyacente y
perder plata igual, porque pagó una prima inflada. Es uno de los errores más
caros y menos evidentes de operar opciones.

LÍMITES HONESTOS DEL MODELO
---------------------------------------------------------------------------
Black-Scholes asume mercados líquidos, continuos y sin dividendos. Las
opciones de BYMA son poco líquidas y sobre acciones que pagan dividendos.
Por eso:

  · Las griegas que salen de acá son ORIENTATIVAS, no exactas. Sirven para
    comparar dos opciones entre sí y para detectar primas absurdas, no para
    armar una cobertura fina.
  · Si la prima es tan baja que la opción no tiene valor temporal, la
    inversión no converge y se devuelve None en vez de un número inventado.
  · Se usa el modelo europeo. Las opciones sobre acciones en BYMA son de
    ejercicio americano, lo que hace que el valor real sea igual o mayor al
    calculado. Para un comprador eso significa que el modelo es conservador:
    subestima levemente lo que vale la opción, nunca lo contrario.

Esas tres limitaciones están anotadas en la salida de cada cálculo, para que
quien lea el número sepa qué tiene en la mano.
"""

import logging
import math
import os
from dataclasses import dataclass, asdict
from datetime import date
from typing import Optional

logger = logging.getLogger("greeks")

# Tasa libre de riesgo por default si no hay dato de caución ni TAMAR.
# TAMAR se usa como aproximación de mercado; el propio cálculo avisa cuando
# cayó a este valor en vez de usar la caución real.
TASA_LIBRE_RIESGO_DEFAULT = float(os.getenv("GREEKS_RISK_FREE_DEFAULT", "0.29"))

# Límites de la búsqueda de volatilidad implícita. Una IV del 500% anual no es
# un error de cálculo en el mercado argentino: es perfectamente posible en una
# opción muy corta y muy fuera del dinero. El techo está alto a propósito.
IV_MINIMA = 0.01
IV_MAXIMA = 6.0
TOLERANCIA = 1e-6
MAX_ITERACIONES = 100

# Lotes estándar de BYMA. IMPORTANTE: no son iguales para todo.
#   · Opciones sobre acciones: 100 nominales (Circular 3562 de BYMA).
#   · Opciones sobre CEDEARs:  10 nominales (Comunicado 17695).
# Usar 100 para un CEDEAR multiplicaría por diez el tamaño de la posición y la
# pérdida máxima calculada. Cuando la API informa el lote real, ese manda.
LOTE_ACCIONES = 100
LOTE_CEDEARS = 10


@dataclass
class ResultadoGriegas:
    volatilidad_implicita: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta_diario: Optional[float] = None
    vega: Optional[float] = None
    valor_intrinseco: float = 0.0
    valor_temporal: float = 0.0
    volatilidad_historica: Optional[float] = None
    prima_cara_o_barata: str = ""
    ratio_iv_historica: Optional[float] = None
    convergio: bool = False
    advertencias: list = None
    motivo: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["advertencias"] = self.advertencias or []
        return d


# ---------------------------------------------------------------------------
# Black-Scholes
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    """Distribución normal acumulada. Se usa math.erf en vez de scipy para no
    sumar una dependencia pesada por una sola función."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(S: float, K: float, T: float, r: float, sigma: float) -> tuple:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return None, None
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return d1, d1 - sigma * math.sqrt(T)


def precio_teorico(S: float, K: float, T: float, r: float, sigma: float,
                   es_call: bool = True) -> Optional[float]:
    """Prima teórica de una opción europea."""
    if T <= 0:
        return max(S - K, 0.0) if es_call else max(K - S, 0.0)
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    if d1 is None:
        return None
    if es_call:
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def volatilidad_implicita(prima: float, S: float, K: float, T: float, r: float,
                          es_call: bool = True) -> Optional[float]:
    """
    Despeja la volatilidad de la fórmula de Black-Scholes por bisección.

    Se usa bisección y no Newton-Raphson a propósito: Newton es más rápido
    pero puede divergir cuando vega es muy chica, que es exactamente lo que
    pasa en opciones muy dentro o muy fuera del dinero — o sea, buena parte de
    las series listadas en BYMA. La bisección es más lenta y siempre converge
    dentro del intervalo. Con cien iteraciones sobre un rango de 1% a 600%, la
    precisión sobra y el costo en tiempo es despreciable.

    Devuelve None cuando no hay solución posible. Ese None es información: si
    la prima de mercado está por debajo del valor intrínseco, no hay ninguna
    volatilidad que explique ese precio, y significa que el dato es malo o que
    hay un arbitraje. Inventar un número ahí sería lo peor que se puede hacer.
    """
    if prima <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None

    # Cota inferior de no arbitraje. OJO: para una opción europea NO es
    # max(S-K, 0) sino max(S - K·e^(-rT), 0), porque el strike se paga recién
    # al vencimiento y hay que descontarlo.
    #
    # Esta distinción, que en un mercado con tasas del 4% es casi irrelevante,
    # acá es enorme: con la caución al 29% anual, descontar un strike a dos
    # meses cambia la cota en varios puntos porcentuales del subyacente. Y
    # tiene una consecuencia práctica concreta: las opciones de BYMA son de
    # ejercicio AMERICANO, así que su prima de mercado puede ubicarse
    # legítimamente por debajo de la cota europea. Cuando eso pasa, no hay
    # ninguna volatilidad que explique el precio con este modelo — y hay que
    # decirlo, no devolver el piso del intervalo como si fuera una respuesta.
    if es_call:
        cota_inferior = max(S - K * math.exp(-r * T), 0.0)
    else:
        cota_inferior = max(K * math.exp(-r * T) - S, 0.0)

    if prima < cota_inferior - TOLERANCIA:
        logger.info("Prima %.4f por debajo de la cota europea %.4f (S=%.2f K=%.2f r=%.2f T=%.3f): "
                    "sin solución con el modelo europeo.", prima, cota_inferior, S, K, r, T)
        return None

    bajo, alto = IV_MINIMA, IV_MAXIMA
    precio_alto = precio_teorico(S, K, T, r, alto, es_call)
    if precio_alto is not None and prima > precio_alto:
        # Ni con 600% de volatilidad se explica esta prima.
        return None

    for _ in range(MAX_ITERACIONES):
        medio = (bajo + alto) / 2.0
        precio = precio_teorico(S, K, T, r, medio, es_call)
        if precio is None:
            return None
        if abs(precio - prima) < TOLERANCIA:
            return medio
        if precio > prima:
            alto = medio
        else:
            bajo = medio

    resultado = (bajo + alto) / 2.0
    return resultado if IV_MINIMA < resultado < IV_MAXIMA else None


def calcular(prima: float, precio_subyacente: float, strike: float,
             dias_al_vencimiento: int, es_call: bool = True,
             tasa_libre_riesgo: Optional[float] = None,
             volatilidad_historica: Optional[float] = None) -> ResultadoGriegas:
    """
    Cálculo completo: volatilidad implícita, griegas y el veredicto de si la
    prima está cara o barata contra la volatilidad histórica del subyacente.
    """
    resultado = ResultadoGriegas(advertencias=[])

    if tasa_libre_riesgo is None:
        tasa_libre_riesgo = _tasa_desde_macro()
        if tasa_libre_riesgo is None:
            tasa_libre_riesgo = TASA_LIBRE_RIESGO_DEFAULT
            resultado.advertencias.append(
                "Sin tasa de caución disponible: se usó la tasa por default. La volatilidad "
                "implícita es orientativa.")

    T = dias_al_vencimiento / 365.0
    if T <= 0:
        resultado.motivo = "La opción ya venció."
        return resultado

    resultado.valor_intrinseco = (max(precio_subyacente - strike, 0.0) if es_call
                                  else max(strike - precio_subyacente, 0.0))
    resultado.valor_temporal = prima - resultado.valor_intrinseco

    if resultado.valor_temporal <= 0:
        resultado.motivo = (
            "La prima no tiene valor temporal: está en o por debajo de su valor intrínseco. "
            "No hay volatilidad que despejar. Suele indicar un dato viejo o una punta sin "
            "profundidad real.")
        resultado.advertencias.append("Prima sin valor temporal.")
        return resultado

    iv = volatilidad_implicita(prima, precio_subyacente, strike, T, tasa_libre_riesgo, es_call)
    if iv is None:
        cota = (max(precio_subyacente - strike * math.exp(-tasa_libre_riesgo * T), 0.0) if es_call
                else max(strike * math.exp(-tasa_libre_riesgo * T) - precio_subyacente, 0.0))
        if prima < cota:
            resultado.motivo = (
                f"La prima (${prima:,.2f}) está por debajo de la cota inferior europea "
                f"(${cota:,.2f}). No es un error del dato: con la tasa local al "
                f"{tasa_libre_riesgo*100:.0f}% anual, descontar el strike sube mucho esa cota, y "
                f"como las opciones de BYMA son de ejercicio americano su precio de mercado "
                f"puede ubicarse legítimamente por debajo. Para esta serie no se pueden calcular "
                f"griegas con este modelo. Pasa sobre todo en opciones bien dentro del dinero.")
            resultado.advertencias.append("Fuera del alcance del modelo europeo.")
        else:
            resultado.motivo = ("No se pudo despejar la volatilidad implícita: ningún valor entre "
                                "1% y 600% explica esta prima. El dato de mercado es inconsistente.")
        return resultado

    resultado.volatilidad_implicita = round(iv, 4)
    resultado.convergio = True

    d1, d2 = _d1_d2(precio_subyacente, strike, T, tasa_libre_riesgo, iv)
    raiz_T = math.sqrt(T)

    resultado.delta = round(_norm_cdf(d1) if es_call else _norm_cdf(d1) - 1.0, 4)
    resultado.gamma = round(_norm_pdf(d1) / (precio_subyacente * iv * raiz_T), 6)
    resultado.vega = round(precio_subyacente * _norm_pdf(d1) * raiz_T / 100.0, 4)

    theta_anual = (-(precio_subyacente * _norm_pdf(d1) * iv) / (2 * raiz_T)
                   - (tasa_libre_riesgo * strike * math.exp(-tasa_libre_riesgo * T) *
                      (_norm_cdf(d2) if es_call else -_norm_cdf(-d2))))
    resultado.theta_diario = round(theta_anual / 365.0, 4)

    if volatilidad_historica is None:
        resultado.advertencias.append(
            "Sin volatilidad histórica del subyacente: no se puede decir si la prima está cara "
            "o barata, solo cuánta volatilidad implica.")
    else:
        resultado.volatilidad_historica = round(volatilidad_historica, 4)
        ratio = iv / volatilidad_historica if volatilidad_historica > 0 else None
        resultado.ratio_iv_historica = round(ratio, 3) if ratio else None
        resultado.prima_cara_o_barata = _veredicto_prima(ratio)

    resultado.advertencias.append(
        "Griegas orientativas: modelo europeo sin dividendos sobre un mercado poco líquido. "
        "Sirven para comparar opciones entre sí y detectar primas absurdas, no para cobertura fina.")

    return resultado


def _veredicto_prima(ratio: Optional[float]) -> str:
    """Traduce la relación entre volatilidad implícita e histórica a una
    conclusión operativa. Los cortes no son caprichosos: por debajo de 0,8 el
    mercado está cobrando menos movimiento del que el papel viene teniendo, y
    por encima de 1,3 está cobrando bastante más."""
    if ratio is None:
        return ""
    if ratio < 0.8:
        return ("BARATA — el mercado está cobrando menos volatilidad de la que el papel viene "
                "teniendo. Es el terreno donde comprar opciones tiene sentido.")
    if ratio < 1.3:
        return ("EN LÍNEA — la prima es coherente con la volatilidad reciente del subyacente. "
                "No hay ventaja ni desventaja por el lado del precio de la opción.")
    if ratio < 2.0:
        return ("CARA — el mercado cobra bastante más movimiento del que el papel viene "
                "teniendo. Aunque se acierte la dirección, el sobreprecio se come la ganancia.")
    return ("MUY CARA — la prima implica más del doble de la volatilidad reciente. Suele pasar "
            "antes de un evento conocido. Comprar acá es pagar la incertidumbre de otro.")


def _tasa_desde_macro() -> Optional[float]:
    """Toma la tasa de caución del contexto macro, que es la referencia libre
    de riesgo correcta para el mercado local — no la tasa de un bono del
    Tesoro estadounidense."""
    try:
        import ad_macro_history as macro
        ctx = macro.get_macro_context(30) or {}
        indicadores = ctx.get("indicadores") or {}
        # La caché macro devuelve metadatos por indicador. Se conserva la
        # lectura plana como compatibilidad con contextos anteriores.
        for clave in ("tasa_caucion_tna", "tasa_tamar_privados_tna"):
            dato = indicadores.get(clave, ctx.get(clave))
            tasa = dato.get("ultimo") if isinstance(dato, dict) else dato
            if tasa is not None:
                return float(tasa) / 100.0
    except Exception as e:
        logger.debug("Sin tasa macro disponible: %s", e)
    return None


def lote_por_subyacente(tipo_subyacente: str) -> int:
    """Lote correcto según el tipo de subyacente.

    Este detalle importa mucho más de lo que parece: las opciones sobre
    acciones tienen lote de 100 y las opciones sobre CEDEARs, de 10. Aplicar
    100 a un CEDEAR multiplica por diez el tamaño de la posición y, con él, la
    pérdida máxima real frente a la calculada.
    """
    return LOTE_CEDEARS if (tipo_subyacente or "").upper().startswith("CEDEAR") else LOTE_ACCIONES


def vencimientos_validos(anio: int) -> list:
    """Los vencimientos de opciones sobre renta variable en BYMA son el tercer
    viernes de los MESES PARES. Listar series de meses impares es esperar
    contratos que no existen."""
    return [(anio, mes) for mes in (2, 4, 6, 8, 10, 12)]
