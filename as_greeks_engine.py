"""Analítica orientativa de opciones europeas con Black-Scholes-Merton.

El precio, solver y wrapper comparten cotas descontadas, tasa, dividend yield
continuo y tiempo ACT/365 declarados. La prima de un put europeo puede estar
por debajo de su intrínseco sin arbitraje. No se aplica este modelo a ejercicio
americano, estilo desconocido ni dividendos discretos sin un modelo adecuado.
Las griegas y la relación IV/histórica no prueban edge ni conceden autoridad
de entrada. El caller del contrato debe declarar el estilo de ejercicio.
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
    exercise_style: str = "EUROPEAN"
    model: str = "BLACK_SCHOLES_MERTON_EUROPEAN"
    dividend_yield: Optional[float] = None
    entry_authority: bool = False

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


def _d1_d2(S: float, K: float, T: float, r: float, sigma: float,
           dividend_yield: float = 0.0) -> tuple:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return None, None
    d1 = (math.log(S / K) + (r - dividend_yield + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return d1, d1 - sigma * math.sqrt(T)


def precio_teorico(S: float, K: float, T: float, r: float, sigma: float,
                   es_call: bool = True, dividend_yield: float = 0.0) -> Optional[float]:
    """Prima teórica de una opción europea."""
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
               for v in (S, K, T, r, sigma, dividend_yield)) or S <= 0 or K <= 0:
        return None
    if T <= 0:
        return max(S - K, 0.0) if es_call else max(K - S, 0.0)
    try:
        d1, d2 = _d1_d2(S, K, T, r, sigma, dividend_yield)
        if d1 is None:
            return None
        bounds = european_bounds(S, K, T, r, es_call, dividend_yield)
        if bounds is None:
            return None
        spot, strike = S * math.exp(-dividend_yield * T), K * math.exp(-r * T)
        price = (spot * _norm_cdf(d1) - strike * _norm_cdf(d2) if es_call
                 else strike * _norm_cdf(-d2) - spot * _norm_cdf(-d1))
        return max(0., price) if math.isfinite(price) else None
    except (OverflowError, ValueError, ZeroDivisionError):
        return None


def european_bounds(S, K, T, r, es_call=True, dividend_yield=0.0):
    """No-arbitrage bounds for the same European model as the IV solver."""
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
               for v in (S, K, T, r, dividend_yield)) or S <= 0 or K <= 0 or T <= 0:
        return None
    try:
        discounted_spot = S * math.exp(-dividend_yield * T)
        discounted_strike = K * math.exp(-r * T)
    except OverflowError:
        return None
    if not all(math.isfinite(value) for value in (discounted_spot, discounted_strike)):
        return None
    return ((max(discounted_spot - discounted_strike, 0.0), discounted_spot) if es_call
            else (max(discounted_strike - discounted_spot, 0.0), discounted_strike))


def volatilidad_implicita(prima: float, S: float, K: float, T: float, r: float,
                          es_call: bool = True, dividend_yield: float = 0.0) -> Optional[float]:
    """
    Despeja la volatilidad de la fórmula de Black-Scholes por bisección.

    Se usa bisección y no Newton-Raphson a propósito: Newton es más rápido
    pero puede divergir cuando vega es muy chica, que es exactamente lo que
    pasa en opciones muy dentro o muy fuera del dinero — o sea, buena parte de
    las series listadas en BYMA. La bisección es más lenta y siempre converge
    dentro del intervalo. Con cien iteraciones sobre un rango de 1% a 600%, la
    precisión sobra y el costo en tiempo es despreciable.

    Devuelve None fuera de las cotas europeas descontadas o del intervalo de
    búsqueda. La ausencia de solución no diagnostica la calidad del proveedor.
    """
    if (not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                for v in (prima, S, K, T, r, dividend_yield))
            or prima <= 0 or S <= 0 or K <= 0 or T <= 0):
        return None

    # European lower/upper bounds include both discount factors. Immediate
    # exercise intrinsic is not the lower bound of a European put.
    bounds = european_bounds(S, K, T, r, es_call, dividend_yield)
    if bounds is None:
        return None
    cota_inferior, cota_superior = bounds

    if prima < cota_inferior - TOLERANCIA or prima >= cota_superior:
        logger.info("Prima %.4f por debajo de la cota europea %.4f (S=%.2f K=%.2f r=%.2f T=%.3f): "
                    "sin solución con el modelo europeo.", prima, cota_inferior, S, K, r, T)
        return None

    bajo, alto = IV_MINIMA, IV_MAXIMA
    precio_bajo = precio_teorico(S, K, T, r, bajo, es_call, dividend_yield)
    precio_alto = precio_teorico(S, K, T, r, alto, es_call, dividend_yield)
    if precio_bajo is None or precio_alto is None or not precio_bajo - TOLERANCIA <= prima <= precio_alto + TOLERANCIA:
        # Ni con 600% de volatilidad se explica esta prima.
        return None

    for _ in range(MAX_ITERACIONES):
        medio = (bajo + alto) / 2.0
        precio = precio_teorico(S, K, T, r, medio, es_call, dividend_yield)
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
             volatilidad_historica: Optional[float] = None, *,
             exercise_style: str = "EUROPEAN", dividend_yield: Optional[float] = 0.0) -> ResultadoGriegas:
    """
    Cálculo completo: volatilidad implícita, griegas y el veredicto de si la
    prima está cara o barata contra la volatilidad histórica del subyacente.
    """
    style = str(exercise_style or "UNKNOWN").strip().upper()
    resultado = ResultadoGriegas(advertencias=[], exercise_style=style,
                                 dividend_yield=dividend_yield)
    if style != "EUROPEAN":
        resultado.model = "UNAVAILABLE_FOR_CONTRACT_STYLE"
        resultado.motivo = "Estilo de ejercicio contractual no europeo o no verificado; este modelo se abstiene."
        resultado.advertencias.append("No se aproxima una opción americana mediante una valoración europea.")
        return resultado
    if (dividend_yield is None or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v) for v in (prima, precio_subyacente, strike, dias_al_vencimiento, dividend_yield))
            or prima <= 0 or precio_subyacente <= 0 or strike <= 0):
        resultado.motivo = "Parámetros contractuales faltantes o no finitos; cálculo no disponible."
        return resultado

    if tasa_libre_riesgo is None:
        tasa_libre_riesgo = _tasa_desde_macro()
        if tasa_libre_riesgo is None:
            tasa_libre_riesgo = TASA_LIBRE_RIESGO_DEFAULT
            resultado.advertencias.append(
                "Sin tasa de caución disponible: se usó la tasa por default. La volatilidad "
                "implícita es orientativa.")

    T = dias_al_vencimiento / 365.0
    if not isinstance(tasa_libre_riesgo, (int, float)) or isinstance(tasa_libre_riesgo, bool) or not math.isfinite(tasa_libre_riesgo):
        resultado.motivo = "Tasa contractual no finita; cálculo no disponible."
        return resultado
    if T <= 0:
        resultado.motivo = "La opción ya venció."
        return resultado

    resultado.valor_intrinseco = (max(precio_subyacente - strike, 0.0) if es_call
                                  else max(strike - precio_subyacente, 0.0))
    resultado.valor_temporal = prima - resultado.valor_intrinseco

    bounds = european_bounds(precio_subyacente, strike, T, tasa_libre_riesgo, es_call, dividend_yield)
    if bounds is None:
        resultado.motivo = "Cotas del modelo fuera del rango numérico representable; cálculo no disponible."
        return resultado
    lower, upper = bounds
    if prima < lower - TOLERANCIA or prima >= upper:
        resultado.motivo = (
            "La prima no tiene valor temporal admisible para el modelo europeo: queda fuera "
            "de sus cotas descontadas con tasa, dividendos y tiempo declarados.")
        resultado.advertencias.append("Fuera de las cotas del modelo europeo; no se infiere calidad del dato.")
        return resultado

    iv = volatilidad_implicita(prima, precio_subyacente, strike, T, tasa_libre_riesgo, es_call, dividend_yield)
    if iv is None:
        resultado.motivo = ("No se pudo despejar IV dentro del intervalo declarado de 1% a 600%; "
                            "no se infiere que el dato de mercado sea incorrecto.")
        return resultado

    resultado.volatilidad_implicita = round(iv, 4)
    resultado.convergio = True

    d1, d2 = _d1_d2(precio_subyacente, strike, T, tasa_libre_riesgo, iv, dividend_yield)
    raiz_T = math.sqrt(T)
    discount = math.exp(-dividend_yield * T)

    resultado.delta = round(discount * (_norm_cdf(d1) if es_call else _norm_cdf(d1) - 1.0), 4)
    resultado.gamma = round(discount * _norm_pdf(d1) / (precio_subyacente * iv * raiz_T), 6)
    resultado.vega = round(precio_subyacente * discount * _norm_pdf(d1) * raiz_T / 100.0, 4)

    theta_anual = (-(precio_subyacente * discount * _norm_pdf(d1) * iv) / (2 * raiz_T)
                   - (tasa_libre_riesgo * strike * math.exp(-tasa_libre_riesgo * T) *
                      (_norm_cdf(d2) if es_call else -_norm_cdf(-d2)))
                   + dividend_yield * precio_subyacente * discount *
                     (_norm_cdf(d1) if es_call else -_norm_cdf(-d1)))
    resultado.theta_diario = round(theta_anual / 365.0, 4)

    if volatilidad_historica is None:
        resultado.advertencias.append(
            "Sin volatilidad histórica del subyacente: no se puede decir si la prima está cara "
            "o barata, solo cuánta volatilidad implica.")
    elif (isinstance(volatilidad_historica, (int, float)) and not isinstance(volatilidad_historica, bool)
          and math.isfinite(volatilidad_historica) and volatilidad_historica > 0):
        resultado.volatilidad_historica = round(volatilidad_historica, 4)
        ratio = iv / volatilidad_historica if volatilidad_historica > 0 else None
        resultado.ratio_iv_historica = round(ratio, 3) if ratio else None
        resultado.prima_cara_o_barata = _veredicto_prima(ratio)
    else:
        resultado.advertencias.append("Volatilidad histórica no verificada: comparación IV/histórica no disponible.")

    resultado.advertencias.append(
        "Griegas orientativas: modelo europeo con dividend yield declarado. No habilitan entradas "
        "ni sustituyen un modelo de ejercicio americano o flujos discretos.")

    return resultado


def _veredicto_prima(ratio: Optional[float]) -> str:
    """Traduce la relación entre volatilidad implícita e histórica a una
    conclusión operativa. Los cortes no son caprichosos: por debajo de 0,8 el
    mercado está cobrando menos movimiento del que el papel viene teniendo, y
    por encima de 1,3 está cobrando bastante más."""
    if ratio is None:
        return ""
    if ratio < 0.8:
        return "BARATA — IV menor que la volatilidad histórica declarada; comparación orientativa sin edge validado."
    if ratio < 1.3:
        return "EN LÍNEA — IV próxima a la volatilidad histórica declarada; comparación orientativa sin edge validado."
    if ratio < 2.0:
        return "CARA — IV mayor que la volatilidad histórica declarada; comparación orientativa sin edge validado."
    return "MUY CARA — IV supera el doble de la volatilidad histórica declarada; comparación orientativa sin edge validado."


def _tasa_desde_macro() -> Optional[float]:
    """BCRA/TAMAR cache retired; caller must use its explicit fallback.

    This function intentionally performs no network or cache read.
    """
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
