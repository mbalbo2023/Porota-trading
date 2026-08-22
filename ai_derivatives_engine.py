"""
ai_derivatives_engine.py — Motor de riesgo para instrumentos derivados
(OPCIONES y FUTUROS)

POR QUÉ EXISTE ESTE MÓDULO
---------------------------------------------------------------------------
Hasta ahora los derivados quedaban afuera del universo operable por una
DECISIÓN ESCRITA A MANO: una lista fija ("OBSERVATION_ONLY_TYPES") que
decía "opciones y futuros no se operan". El problema de esa forma de
resolverlo no es el criterio —el criterio era correcto— sino QUIÉN lo
toma: lo tomaba una constante en el código, de una vez y para siempre,
sin mirar el instrumento concreto que tenía adelante.

Este módulo invierte eso. Ya no hay ninguna clase de activo excluida por
nombre. Lo que hay es una PRUEBA DE CAPACIDAD que se corre instrumento
por instrumento y responde una sola pregunta:

    ¿Puede este sistema calcular, para ESTE instrumento en particular,
    cuánto puede perder como máximo y cuántas unidades comprar para que
    esa pérdida máxima sea el % de la cuenta que tengo configurado?

Si la respuesta es sí, el instrumento entra al universo operable y sigue
el mismo camino que un CEDEAR: técnico → contexto → costos → riesgo →
orden. Si la respuesta es no, no entra — pero NO por su categoría, sino
porque falta un dato concreto e identificable, que queda registrado y se
muestra en el panel. El día que ese dato aparezca (porque la API lo
empieza a devolver, o porque se habilita un permiso en la cuenta), el
mismo instrumento pasa la prueba solo, sin tocar una línea de código.

LA MATEMÁTICA, QUE ES LO QUE DE VERDAD DECIDE
---------------------------------------------------------------------------
El sistema entero está construido sobre una sola regla: se arriesga
RISK_PCT_PER_TRADE % de la cuenta por operación (1% por default). Para
una acción, la pérdida máxima es la distancia hasta el stop-loss. Para un
derivado, esa cuenta cambia — y ahí está todo el asunto:

OPCIÓN COMPRADA (call o put larga): la pérdida máxima está acotada por
    definición: es la prima pagada. No hace falta stop-loss para conocer
    el piso, no hay llamada de margen, no hay riesgo de que la posición
    valga menos que cero. El dimensionamiento sale directo:
        contratos = (capital × riesgo%) / (prima × lote)
    Esta es exactamente la misma aritmética que ya usa el sistema, con la
    prima ocupando el lugar de la distancia al stop. Encaja sin forzar
    nada. Por eso una opción comprada SÍ pasa la prueba de capacidad.

OPCIÓN LANZADA (vendida en descubierto): la pérdida máxima es, en un call
    descubierto, matemáticamente infinita. No existe ningún número que
    poner en "cuánto puedo perder", así que la fórmula de arriba no tiene
    solución. No pasa la prueba de capacidad — y no es una preferencia
    conservadora ni una lista negra: es que la ecuación no cierra. El
    motivo se registra como CAPACIDAD_PERDIDA_NO_ACOTADA, no como
    "tipo de instrumento prohibido".

FUTURO: la pérdida tampoco está acotada por la prima (no hay prima), pero
    sí es calculable si se conocen tres datos: el multiplicador del
    contrato, la garantía exigida y el saldo libre para responder a la
    marcación a mercado diaria. Con esos tres, el dimensionamiento es:
        contratos = (capital × riesgo%) / (distancia_al_stop × multiplicador)
    y además hay que verificar que las garantías de esos contratos entren
    en el saldo disponible. El multiplicador se puede inferir del propio
    contrato; la garantía y el saldo libre para garantías dependen de que
    el bróker los informe. Mientras falte ese dato, el futuro no pasa la
    prueba — con el motivo exacto (CAPACIDAD_SIN_DATO_DE_GARANTIA) visible
    en el panel, que es muy distinto a un "no soportado" opaco.

LO QUE ESTE MÓDULO NO INVENTA
---------------------------------------------------------------------------
No se calculan griegas con Black-Scholes. Se podría, pero requiere una
volatilidad implícita que ni PPI ni ninguna fuente gratuita local publican
de forma confiable, y una griega calculada con una volatilidad inventada
es peor que no tenerla: da una precisión falsa sobre la que después se
toman decisiones. En su lugar se usan medidas que salen de datos que sí
existen y se pueden verificar: moneyness (distancia del strike al precio
del subyacente), días al vencimiento, prima como % del subyacente,
spread y volumen negociado. Son groseras comparadas con un delta real,
pero son ciertas.
"""

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger("derivatives")

# ---------------------------------------------------------------------------
# Parámetros configurables. Todos tienen un default pensado y todos se
# explican uno por uno en la tabla de variables de entorno del documento.
# ---------------------------------------------------------------------------

# Días corridos mínimos hasta el vencimiento. Una opción a menos de dos
# semanas pierde valor temporal muy rápido: aunque el subyacente se mueva
# a favor, el decaimiento puede comerse la ganancia. No es una regla
# estética — es la razón por la que el horizonte del bot (TARGET_HOLD_DAYS,
# 5 días por default) tiene que caber holgado adentro de la vida que le
# queda al contrato.
MIN_DAYS_TO_EXPIRY = int(os.getenv("DERIV_MIN_DAYS_TO_EXPIRY", "15"))

# Cuántos días antes del vencimiento se cierra sí o sí una posición abierta,
# gane o pierda. Llegar al vencimiento con una opción comprada abierta
# significa entregarle la decisión al azar del ejercicio automático.
FORCE_CLOSE_DAYS_BEFORE_EXPIRY = int(os.getenv("DERIV_FORCE_CLOSE_DAYS", "3"))

# Moneyness máximo aceptado: qué tan lejos puede estar el strike del precio
# del subyacente. 15% significa que se descartan las opciones muy fuera del
# dinero — las que se compran "por si acaso" a centavos. Son baratas por una
# razón: la enorme mayoría vence sin valor. Comprarlas de forma sistemática
# no es operar, es comprar billetes de lotería con el capital de trading.
MAX_MONEYNESS_PCT = float(os.getenv("DERIV_MAX_MONEYNESS_PCT", "15.0"))

# Prima máxima como % del valor del subyacente. Una prima desproporcionada
# frente al precio del papel es la firma de una volatilidad implícita
# inflada: se está pagando caro el mismo movimiento.
MAX_PREMIUM_PCT_OF_UNDERLYING = float(os.getenv("DERIV_MAX_PREMIUM_PCT", "12.0"))

# Spread máximo tolerado en el book del derivado. Los derivados locales son
# mucho menos líquidos que las acciones: un spread de 8% se come de entrada
# cualquier ventaja estadística que haya detectado el análisis técnico.
MAX_SPREAD_PCT = float(os.getenv("DERIV_MAX_SPREAD_PCT", "6.0"))

# Tope de exposición del capital total en derivados, sumando todo lo abierto.
# Aunque cada posición individual arriesgue el 1% configurado, el conjunto
# puede concentrar demasiado en productos con vencimiento.
MAX_PORTFOLIO_PCT_IN_DERIVATIVES = float(os.getenv("DERIV_MAX_PORTFOLIO_PCT", "20.0"))

# Lote estándar de las opciones sobre acciones en BYMA. Es 100 en la enorme
# mayoría de las series, pero NO se asume ciegamente: si la API informa el
# lote real del contrato, se usa ese. Este número es solo el último recurso,
# y cuando se usa queda avisado en el log.
DEFAULT_OPTION_LOT = int(os.getenv("DERIV_DEFAULT_OPTION_LOT", "100"))

# Meses de vencimiento en la nomenclatura de opciones de BYMA. La letra final
# del ticker identifica el mes: E=enero, F=febrero, etc.
MONTH_CODES = {
    "E": 1, "F": 2, "M": 3, "A": 4, "Y": 5, "J": 6,
    "L": 7, "G": 8, "S": 9, "O": 10, "N": 11, "D": 12,
}


@dataclass
class DerivativeSpec:
    """Todo lo que el sistema logró establecer sobre un derivado concreto.

    `capable` es la única bandera que decide si el instrumento entra al
    universo operable. `blocking_reason` explica, cuando es False, qué dato
    exacto falta — para que el panel muestre un motivo accionable y no un
    "no soportado" que nadie sabe cómo destrabar.
    """
    ticker: str
    family: str                      # "OPCION" | "FUTURO"
    capable: bool = False
    blocking_reason: str = ""
    blocking_code: str = ""
    underlying: Optional[str] = None
    right: Optional[str] = None      # "CALL" | "PUT"
    side: str = "LONG"               # este motor solo dimensiona posiciones compradas
    strike: Optional[float] = None
    expiry: Optional[date] = None
    days_to_expiry: Optional[int] = None
    lot_size: int = DEFAULT_OPTION_LOT
    lot_size_source: str = "default"  # "api" | "default"
    # NUEVO EN v16.2 — de dónde salió el strike. Un dato provisional no
    # debería atravesar tres decisiones (moneyness, volatilidad implícita y
    # griegas) sin que nadie lo marque. Ahora se marca, y si no viene de la
    # API el instrumento queda no operable con código propio.
    strike_source: str = "ticker"     # "api" | "ticker"
    contract_multiplier: Optional[float] = None
    max_loss_per_unit: Optional[float] = None   # prima (opción) o riesgo al stop (futuro)
    warnings: list = field(default_factory=list)
    greeks: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 1. Lectura del contrato
# ---------------------------------------------------------------------------

def parse_option_ticker(ticker: str, today: Optional[date] = None,
                        tipo_subyacente: str = "") -> DerivativeSpec:
    """
    Interpreta el ticker de una opción de BYMA.

    La nomenclatura local arma el símbolo como: raíz del subyacente + letra
    del derecho (C = call, V = venta/put) + strike + letra del mes de
    vencimiento. Por ejemplo GFGC50000A es un call de Galicia, strike 50000,
    vencimiento de abril.

    ADVERTENCIA HONESTA, y es importante: esta lectura funciona sobre el
    patrón documentado, pero el strike aparece SIN separador decimal y su
    escala depende de la serie. Por eso el strike leído acá se marca como
    provisional: si la API devuelve el strike y el vencimiento como campos
    propios —que es lo esperable en un endpoint de derivados—, esos campos
    tienen prioridad absoluta sobre lo que se deduzca del texto del ticker.
    Deducir del nombre es el último recurso, nunca la fuente preferida.
    """
    today = today or date.today()
    spec = DerivativeSpec(ticker=ticker, family="OPCION")

    m = re.fullmatch(r"([A-Z]{2,5})([CV])(\d{2,8})([EFMAYJLGSOND])", ticker.strip().upper())
    if not m:
        spec.blocking_code = "CAPACIDAD_TICKER_ILEGIBLE"
        spec.blocking_reason = (
            f"No se pudo interpretar '{ticker}' como una serie de opciones de BYMA "
            f"(se esperaba raíz + C/V + strike + letra de mes). Sin strike ni "
            f"vencimiento no hay forma de calcular la pérdida máxima."
        )
        return spec

    root, right_code, strike_raw, month_code = m.groups()
    spec.underlying = root
    spec.right = "CALL" if right_code == "C" else "PUT"
    # El lote correcto depende del subyacente y no es un detalle menor: las
    # opciones sobre acciones son de 100 nominales y las de CEDEARs de 10.
    # Aplicar 100 a un CEDEAR multiplica por diez la posición y la pérdida
    # máxima real frente a la calculada.
    if tipo_subyacente:
        import as_greeks_engine
        spec.lot_size = as_greeks_engine.lote_por_subyacente(tipo_subyacente)
        spec.lot_size_source = "convención BYMA por tipo de subyacente"
    spec.strike = float(strike_raw)
    spec.warnings.append(
        "Strike leído del texto del ticker: escala provisional hasta que la API "
        "confirme el valor como campo propio."
    )

    month = MONTH_CODES[month_code]
    if month % 2 != 0:
        # CAMBIADO EN v16.2 — antes esto era solo una advertencia y la fecha
        # inventada seguía alimentando el filtro de vencimiento y el
        # dimensionamiento. Ahora bloquea, por coherencia con el principio
        # que ordena todo el sistema: un dato que no se puede establecer no
        # se completa con la mejor suposición disponible.
        #
        # MATIZ IMPORTANTE, verificado: BYMA lista series sobre renta
        # variable con vencimiento en los meses PARES, pero PUEDE habilitar
        # además un vencimiento en el mes impar más próximo. Así que un mes
        # impar no es imposible: es no confirmable desde el ticker. Por eso
        # el bloqueo es levantable con el dato de la API, no una prohibición
        # de categoría.
        spec.blocking_code = "CAPACIDAD_VENCIMIENTO_NO_CONFIRMADO"
        spec.blocking_reason = (
            f"El código de mes '{month_code}' apunta a un mes impar. BYMA lista las "
            f"series de renta variable en meses pares y solo a veces habilita el "
            f"impar más próximo, así que desde el ticker no se puede confirmar que "
            f"esta serie exista ni cuándo vence. Hace falta el campo de vencimiento "
            f"de la API.")
        return spec

    # Los vencimientos de opciones en BYMA son el tercer viernes del mes de
    # vencimiento.
    year = today.year if month >= today.month else today.year + 1
    spec.expiry = _third_friday(year, month)
    # CORREGIDO EN v16.2 — la resolución del año usaba `month >= today.month`,
    # que dentro del PROPIO mes de vencimiento puede producir una fecha ya
    # pasada: el 20 de agosto, una serie de agosto resolvía al tercer viernes
    # de agosto, que ya había ocurrido. El resultado era un "días al
    # vencimiento" negativo alimentando el filtro de vida mínima.
    if spec.expiry < today:
        spec.expiry = _third_friday(year + 1, month)
    spec.days_to_expiry = (spec.expiry - today).days
    return spec


def _third_friday(year: int, month: int) -> date:
    """Tercer viernes del mes: la convención de vencimiento de BYMA.

    CORREGIDO EN v16.1 — las series sobre renta variable vencen el tercer
    viernes de los MESES PARES únicamente (febrero, abril, junio, agosto,
    octubre y diciembre). La versión anterior aceptaba cualquier mes, lo que
    hacía que un ticker con letra de mes impar produjera una fecha de
    vencimiento para una serie que no existe — y con ella, un cálculo de días
    al vencimiento inventado que después alimentaba el dimensionamiento.
    """
    d = date(year, month, 1)
    # weekday(): lunes=0 ... viernes=4
    first_friday = 1 + ((4 - d.weekday()) % 7)
    return date(year, month, first_friday + 14)


def describe_future(ticker: str, api_payload: Optional[dict] = None) -> DerivativeSpec:
    """
    Arma la ficha de un futuro a partir de lo que informe la API.

    A diferencia de la opción, acá NO se deduce nada del nombre: un futuro sin
    multiplicador de contrato y sin garantía informada es un instrumento del
    que literalmente no se puede saber cuánto se arriesga por contrato. Se
    devuelve incapaz, con el motivo puntual, y el panel lo muestra como dato
    faltante en vez de como prohibición.
    """
    spec = DerivativeSpec(ticker=ticker, family="FUTURO")
    payload = api_payload or {}

    multiplier = payload.get("contractMultiplier") or payload.get("multiplicador")
    margin = payload.get("initialMargin") or payload.get("garantia")

    if not multiplier:
        spec.blocking_code = "CAPACIDAD_SIN_MULTIPLICADOR"
        spec.blocking_reason = (
            f"El futuro {ticker} no informa multiplicador de contrato. Sin ese número, "
            f"un movimiento de un punto puede valer cualquier cosa: no hay manera de "
            f"dimensionar la posición contra el 1% de riesgo configurado."
        )
        return spec

    spec.contract_multiplier = float(multiplier)

    if not margin:
        spec.blocking_code = "CAPACIDAD_SIN_DATO_DE_GARANTIA"
        spec.blocking_reason = (
            f"El futuro {ticker} informa multiplicador pero no garantía inicial exigida. "
            f"Operar un futuro sin saber la garantía es quedar expuesto a una llamada "
            f"de margen que el bot no puede anticipar ni cubrir solo."
        )
        return spec

    expiry_raw = payload.get("expirationDate") or payload.get("vencimiento")
    if expiry_raw:
        try:
            spec.expiry = datetime.fromisoformat(str(expiry_raw)[:10]).date()
            spec.days_to_expiry = (spec.expiry - date.today()).days
        except ValueError:
            spec.warnings.append(f"Fecha de vencimiento ilegible: {expiry_raw}")

    spec.capable = True
    return spec


# ---------------------------------------------------------------------------
# 2. La prueba de capacidad — el reemplazo de la lista blanca
# ---------------------------------------------------------------------------

def assess_option(spec: DerivativeSpec, premium: float, underlying_price: Optional[float],
                  spread_pct: float = 0.0, side: str = "LONG") -> DerivativeSpec:
    """
    Decide si una opción concreta es operable por este sistema, y por qué.

    El orden de los chequeos no es casual: primero lo que hace imposible el
    cálculo (y por lo tanto descalifica sin discusión), después lo que lo hace
    desaconsejable (y por lo tanto es un umbral configurable).
    """
    if side.upper() != "LONG":
        spec.capable = False
        spec.blocking_code = "CAPACIDAD_PERDIDA_NO_ACOTADA"
        spec.blocking_reason = (
            "Lanzar opciones en descubierto deja la pérdida máxima sin cota superior. "
            "El dimensionamiento del sistema resuelve 'cantidad = riesgo permitido / "
            "pérdida máxima por unidad'; con pérdida no acotada esa división no tiene "
            "resultado. No es una restricción de criterio: la ecuación no cierra."
        )
        return spec

    # ======================================================================
    # NUEVO EN v16.2 — EL STRIKE TIENE QUE VENIR CONFIRMADO POR LA API
    # ======================================================================
    # El strike se deducía de un grupo de dígitos del símbolo, sin separador
    # decimal, y el propio código lo marcaba como "escala provisional". Pese a
    # eso, alimentaba TRES decisiones seguidas: el filtro de moneyness, el
    # despeje de la volatilidad implícita y las griegas.
    #
    # Verificado en banco: con la escala corrida por cien, el motor converge a
    # una volatilidad implícita del 494% y descarta la serie por "prima muy
    # cara". Es decir que el error tiende a bloquear y no a aprobar, que es el
    # lado correcto para fallar. Pero el motivo que registraba el panel era
    # falso —la prima no era cara, el strike estaba mal— y con otra escala el
    # resultado podía caer del lado que aprueba.
    #
    # Un dato provisional no atraviesa tres decisiones sin que nadie lo marque.
    if getattr(spec, "strike_source", "ticker") != "api":
        spec.capable = False
        spec.blocking_code = "CAPACIDAD_STRIKE_NO_CONFIRMADO"
        spec.blocking_reason = (
            "El strike se dedujo del símbolo y su escala es provisional. Hace falta "
            "el campo de strike de la API para dimensionar: con la escala corrida, "
            "el moneyness y la volatilidad implícita quedan mal por dos órdenes de "
            "magnitud."
        )
        return spec

    if premium is None or premium <= 0:
        spec.capable = False
        spec.blocking_code = "CAPACIDAD_SIN_PRIMA"
        spec.blocking_reason = f"Sin prima válida para {spec.ticker}, no hay pérdida máxima que calcular."
        return spec

    if spec.days_to_expiry is None:
        spec.capable = False
        spec.blocking_code = "CAPACIDAD_SIN_VENCIMIENTO"
        spec.blocking_reason = f"No se pudo establecer el vencimiento de {spec.ticker}."
        return spec

    if spec.days_to_expiry < MIN_DAYS_TO_EXPIRY:
        spec.capable = False
        spec.blocking_code = "VENCIMIENTO_DEMASIADO_CERCA"
        spec.blocking_reason = (
            f"Quedan {spec.days_to_expiry} días hasta el vencimiento y el mínimo es "
            f"{MIN_DAYS_TO_EXPIRY}. El horizonte de la operación no entra en la vida "
            f"que le queda al contrato."
        )
        return spec

    if underlying_price and underlying_price > 0 and spec.strike:
        moneyness_pct = abs(spec.strike - underlying_price) / underlying_price * 100
        if moneyness_pct > MAX_MONEYNESS_PCT:
            spec.capable = False
            spec.blocking_code = "MONEYNESS_EXCESIVO"
            spec.blocking_reason = (
                f"El strike está a {moneyness_pct:.1f}% del precio del subyacente "
                f"(máximo {MAX_MONEYNESS_PCT}%). Una opción tan fuera del dinero vence "
                f"sin valor en la gran mayoría de los casos."
            )
            return spec
        premium_pct = premium / underlying_price * 100
        if premium_pct > MAX_PREMIUM_PCT_OF_UNDERLYING:
            spec.capable = False
            spec.blocking_code = "PRIMA_DESPROPORCIONADA"
            spec.blocking_reason = (
                f"La prima equivale al {premium_pct:.1f}% del subyacente "
                f"(máximo {MAX_PREMIUM_PCT_OF_UNDERLYING}%): se estaría pagando muy caro "
                f"el mismo movimiento."
            )
            return spec

    if spread_pct > MAX_SPREAD_PCT:
        spec.capable = False
        spec.blocking_code = "SPREAD_EXCESIVO"
        spec.blocking_reason = (
            f"Spread de {spread_pct:.1f}% contra un máximo de {MAX_SPREAD_PCT}%. "
            f"La fricción de entrada y salida se lleva la ventaja esperada."
        )
        return spec

    spec.capable = True
    spec.max_loss_per_unit = premium * spec.lot_size

    # NUEVO EN v16.1 — volatilidad implícita y griegas.
    #
    # Hasta acá el motor sabía si la opción era dimensionable; ahora también
    # sabe si la prima está cara o barata contra la volatilidad que el
    # subyacente viene teniendo. Es la diferencia entre acertar la dirección y
    # ganar plata: se puede acertar el movimiento y perder igual por haber
    # pagado una prima inflada.
    #
    # Si el cálculo no converge, la opción NO se descarta: sigue siendo
    # operable porque su pérdida máxima está acotada por la prima, que es lo
    # que la prueba de capacidad exige. Simplemente se pierde una señal
    # adicional, y eso queda anotado.
    try:
        import as_greeks_engine as greeks
        vol_hist = _volatilidad_historica(spec.underlying)
        g = greeks.calcular(prima=premium, precio_subyacente=underlying_price or 0,
                            strike=spec.strike or 0, dias_al_vencimiento=spec.days_to_expiry,
                            es_call=(spec.right == "CALL"), volatilidad_historica=vol_hist)
        spec.greeks = g.to_dict()
        if g.convergio:
            if g.ratio_iv_historica and g.ratio_iv_historica >= 2.0:
                spec.capable = False
                spec.blocking_code = "PRIMA_MUY_CARA_VS_VOLATILIDAD"
                spec.blocking_reason = (
                    f"La volatilidad implícita ({g.volatilidad_implicita:.0%}) es "
                    f"{g.ratio_iv_historica:.1f} veces la histórica del subyacente "
                    f"({g.volatilidad_historica:.0%}). Se estaría pagando más del doble de "
                    f"movimiento del que el papel viene teniendo: aun acertando la dirección, "
                    f"el sobreprecio se come la ganancia.")
                return spec
            spec.warnings.append(f"Prima {g.prima_cara_o_barata.split(' —')[0]}, "
                                 f"IV {g.volatilidad_implicita:.0%}, delta {g.delta}.")
        else:
            spec.warnings.append(f"Sin griegas para esta serie: {g.motivo[:120]}")
    except Exception as e:
        spec.warnings.append(f"No se pudieron calcular las griegas: {e}")

    return spec


def _volatilidad_historica(subyacente: Optional[str]) -> Optional[float]:
    """Volatilidad anualizada del subyacente, del archivo histórico propio."""
    if not subyacente:
        return None
    try:
        import al_historical_ingest as hist
        datos = hist.extraer_features(subyacente)
        if datos.get("estado_datos") == "OK":
            return datos.get("volatilidad_anualizada_30d")
    except Exception:
        pass
    return None


def size_long_option(capital_ars: float, spec: DerivativeSpec, premium: float,
                     risk_pct: float) -> int:
    """
    Cuántos contratos comprar. Es la misma regla del resto del sistema —
    arriesgar risk_pct% de la cuenta— con la prima como pérdida máxima.

    Se trunca hacia abajo siempre: es preferible arriesgar un poco menos del
    objetivo que un peso más.
    """
    if capital_ars <= 0 or premium <= 0 or not spec.capable:
        return 0
    risk_amount = capital_ars * (risk_pct / 100.0)
    cost_per_contract = premium * spec.lot_size
    if cost_per_contract <= 0:
        return 0
    contracts = int(risk_amount // cost_per_contract)
    return max(contracts, 0)


def size_future(capital_ars: float, spec: DerivativeSpec, entry_price: float,
                stop_price: float, risk_pct: float,
                free_margin_ars: Optional[float] = None,
                initial_margin_per_contract: Optional[float] = None) -> int:
    """
    Cuántos contratos de futuro. Acá la pérdida por contrato la fija la
    distancia al stop multiplicada por el multiplicador del contrato — y
    además hay que poder cubrir las garantías, que es una restricción
    independiente y frecuentemente más limitante que el riesgo.
    """
    if not spec.capable or not spec.contract_multiplier:
        return 0
    distance = abs(entry_price - stop_price)
    if distance <= 0 or capital_ars <= 0:
        return 0

    risk_amount = capital_ars * (risk_pct / 100.0)
    loss_per_contract = distance * spec.contract_multiplier
    by_risk = int(risk_amount // loss_per_contract)

    # La garantía manda cuando es más restrictiva que el riesgo: no sirve de
    # nada dimensionar tres contratos si solo alcanza para inmovilizar uno.
    if free_margin_ars is not None and initial_margin_per_contract:
        by_margin = int(free_margin_ars // initial_margin_per_contract)
        if by_margin < by_risk:
            logger.info("Futuro %s: la garantía limita a %d contratos (el riesgo permitía %d).",
                        spec.ticker, by_margin, by_risk)
        by_risk = min(by_risk, by_margin)

    return max(by_risk, 0)


def portfolio_room_for_derivatives(total_capital_ars: float,
                                   current_derivative_exposure_ars: float) -> float:
    """Cuántos pesos quedan disponibles para derivados sin pasar el tope de
    concentración. Devuelve 0 si ya se llegó."""
    if total_capital_ars <= 0:
        return 0.0
    cap = total_capital_ars * (MAX_PORTFOLIO_PCT_IN_DERIVATIVES / 100.0)
    return max(cap - current_derivative_exposure_ars, 0.0)


def must_close_for_expiry(spec: DerivativeSpec, today: Optional[date] = None) -> bool:
    """Si el vencimiento está encima, la posición se cierra sin importar el
    resultado. Dejar correr una opción hasta el ejercicio automático es
    delegarle la decisión al calendario."""
    if not spec.expiry:
        return False
    days_left = (spec.expiry - (today or date.today())).days
    return days_left <= FORCE_CLOSE_DAYS_BEFORE_EXPIRY
