"""
au_fee_schedule.py — Tarifario por clase de activo (v16.2)

QUÉ RESUELVE
------------
El modelo de costos aplicaba una sola comisión (0,60%) y un solo derecho de
mercado (0,08%) a acciones, CEDEARs, bonos, ETFs, opciones y futuros por
igual. En el mercado local los aranceles difieren de forma sustancial entre
clases, y el error no es simétrico:

  · Se SOBRESTIMA el costo de operar bonos. Efecto: se descartan operaciones
    que sí eran rentables. Cuesta plata que no se gana, y no se ve nunca —
    el sistema no registra las oportunidades que descartó de más.

  · Se SUBESTIMA el costo de operar opciones. Efecto: se aprueban
    operaciones cuyo neto real es negativo. Este es el lado peligroso: el
    filtro de rentabilidad neta, que es el corazón declarado del diseño,
    trabaja con un número que no corresponde al instrumento que evalúa.

Además, el derecho de mercado de 0,08% no era correcto para ninguna clase:
en acciones y CEDEARs es 0,05% y en títulos públicos 0,01%.

FUENTE Y FECHA DE LOS VALORES
-----------------------------
Tarifario comercial de Portfolio Personal Inversiones vigente desde el
1/07/2026, y grilla de derechos de mercado de BYMA vigente. Verificados el
21/08/2026.

Los aranceles comerciales cambian. Por eso NO son constantes de código: cada
número de este módulo es sobreescribible por variable de entorno, y
`conciliar_contra_broker()` permite contrastar el modelo contra el costo que
informa PPI en el presupuesto de la orden ANTES de operar, no solo después.
Un modelo de costos que solo se audita contra el resumen de cuenta se entera
del desvío cuando ya operó.

TRATAMIENTO DEL IVA — no es uniforme, y esa es la parte que se suele errar:
  · Comisión del agente: tributa IVA 21% en todas las clases.
  · Derechos de mercado: tributan IVA, EXCEPTO en títulos públicos y
    obligaciones negociables, donde están exentos.

CUSTODIA: PPI no cobra cargo de apertura, administración ni custodia de Caja
de Valores. Verificado contra el tarifario vigente. Por eso este módulo no
modela un costo de custodia: agregarlo "por las dudas" sería inventar un
costo que no existe y endurecer el piso de rentabilidad sin motivo.
"""

import logging
import os
from dataclasses import dataclass
from typing import Optional, Dict

logger = logging.getLogger("fee_schedule")

IVA_PCT = float(os.getenv("IVA_PCT", "0.21"))


@dataclass(frozen=True)
class Arancel:
    """Aranceles de UNA clase de activo, expresados en fracción por tramo.

    `comision_iva` y `derecho_iva` son banderas separadas a propósito: hay
    clases donde la comisión tributa y el derecho no.
    """
    comision: float
    derecho: float
    comision_iva: bool = True
    derecho_iva: bool = True
    # Costo adicional específico sobre la prima (opciones). Se aplica sobre
    # el monto operado igual que el derecho, pero se declara aparte para que
    # el desglose del panel muestre de dónde sale cada peso.
    derecho_sobre_prima: float = 0.0
    prima_iva: bool = True
    nota: str = ""


# ---------------------------------------------------------------------------
# Grilla vigente. Los valores son POR TRAMO (una punta de la operación).
# La operación redonda es el doble, salvo el spread, que se paga una vez.
# ---------------------------------------------------------------------------
ARANCELES: Dict[str, Arancel] = {
    "ACCIONES": Arancel(
        comision=0.0060, derecho=0.0005,
        comision_iva=True, derecho_iva=True,
        nota="Comisión 0,60% + IVA. Derecho BYMA 0,05% (0,03% negociación + "
             "0,02% post-trade) + IVA."),
    "CEDEARS": Arancel(
        comision=0.0060, derecho=0.0005,
        comision_iva=True, derecho_iva=True,
        nota="Mismo tratamiento que acciones."),
    "ETFS": Arancel(
        comision=0.0060, derecho=0.0005,
        comision_iva=True, derecho_iva=True,
        nota="Mismo tratamiento que acciones."),
    "BONOS": Arancel(
        comision=0.0060, derecho=0.0001,
        comision_iva=False, derecho_iva=False,
        nota="Títulos públicos: comisión 0,60% SIN IVA y derecho de mercado "
             "0,01% exento. Es la clase donde el costo plano erraba más: el "
             "modelo anterior cobraba 1,6456% redondo contra un real de "
             "1,22%."),
    "TITULOSPUBLICOS": Arancel(
        comision=0.0060, derecho=0.0001,
        comision_iva=False, derecho_iva=False,
        nota="Alias de BONOS."),
    "OBLIGACIONES": Arancel(
        comision=0.0060, derecho=0.0001,
        comision_iva=True, derecho_iva=False,
        nota="Renta fija privada (ON, FF, VCP): la comisión tributa IVA, el "
             "derecho de mercado está exento."),
    "LETRAS": Arancel(
        comision=0.0020, derecho=0.00001,
        comision_iva=False, derecho_iva=False,
        nota="Letras y notas en pesos."),
    "OPCIONES": Arancel(
        comision=0.0100, derecho=0.0005,
        comision_iva=True, derecho_iva=True,
        derecho_sobre_prima=0.0020, prima_iva=True,
        nota="Comisión 1% + IVA (casi el doble que renta variable) más un "
             "derecho de 0,20% sobre la prima. Es la clase donde el costo "
             "plano subestimaba, que es el lado que aprueba operaciones "
             "malas."),
    "FUTUROS": Arancel(
        comision=0.0050, derecho=0.00015,
        comision_iva=True, derecho_iva=True,
        nota="Hasta 0,50% + IVA. Los derechos de mercado de futuros incluyen "
             "componentes fijos en dólares que este modelo no captura: para "
             "futuros, conciliar SIEMPRE contra el presupuesto del bróker "
             "antes de operar."),
    "CAUCIONES": Arancel(
        comision=0.0200, derecho=0.00045,
        comision_iva=True, derecho_iva=True,
        nota="ATENCIÓN: la comisión de caución es ANUALIZADA (2% + IVA por "
             "año), no por operación. Usar costo_caucion(), no las funciones "
             "genéricas: aplicar 2% a una caución de un día sobrestima el "
             "costo unas 365 veces."),
    "FCI": Arancel(
        comision=0.0, derecho=0.0,
        comision_iva=False, derecho_iva=False,
        nota="Sin comisión de suscripción/rescate por el agente; los costos "
             "están dentro del fondo."),
}

CLASE_POR_DEFECTO = "ACCIONES"

# Alias que devuelve la API del bróker con nombres distintos al canónico.
_ALIAS = {
    "ACCION": "ACCIONES", "ACCIONS": "ACCIONES", "EQUITY": "ACCIONES",
    "CEDEAR": "CEDEARS", "ETF": "ETFS",
    "BONO": "BONOS", "RENTAFIJA": "BONOS", "PUBLICOS": "TITULOSPUBLICOS",
    "TITULOS PUBLICOS": "TITULOSPUBLICOS", "TITULOS-PUBLICOS": "TITULOSPUBLICOS",
    "ON": "OBLIGACIONES", "OBLIGACIONESNEGOCIABLES": "OBLIGACIONES",
    "OPCION": "OPCIONES", "OPTIONS": "OPCIONES",
    "FUTURO": "FUTUROS", "FUTURES": "FUTUROS",
    "CAUCION": "CAUCIONES",
}


def normalizar_clase(clase: Optional[str]) -> str:
    """Mapea lo que informe el bróker a una clave de la grilla.

    Ante un tipo desconocido devuelve ACCIONES y lo AVISA por log. Es
    deliberado que el default sea la clase más cara de renta variable y no la
    más barata: si el sistema se equivoca de clase, conviene que se equivoque
    sobrestimando el costo —descarta de más— y no subestimándolo, que es el
    error que aprueba operaciones que pierden plata.
    """
    if not clase:
        return CLASE_POR_DEFECTO
    c = str(clase).strip().upper().replace("_", "").replace("-", "")
    if c in ARANCELES:
        return c
    if c in _ALIAS:
        return _ALIAS[c]
    logger.warning("Clase de activo desconocida para el tarifario: %r. "
                   "Se usa %s (el criterio conservador).", clase, CLASE_POR_DEFECTO)
    return CLASE_POR_DEFECTO


def _override(clase: str, campo: str, valor: float) -> float:
    """Permite pisar cualquier número de la grilla desde el .env sin tocar
    código, con la forma FEE_<CLASE>_<CAMPO>. Ejemplo:
    FEE_OPCIONES_COMISION=0.008. Existe porque los aranceles comerciales se
    negocian: un cliente con volumen puede tener una grilla distinta, y eso
    no debería obligar a un deploy."""
    env = os.getenv(f"FEE_{clase}_{campo.upper()}")
    if env:
        try:
            return float(env)
        except ValueError:
            logger.warning("FEE_%s_%s no es un número: %r. Se ignora.",
                           clase, campo.upper(), env)
    return valor


def arancel_de(clase: Optional[str]) -> Arancel:
    c = normalizar_clase(clase)
    base = ARANCELES[c]
    return Arancel(
        comision=_override(c, "comision", base.comision),
        derecho=_override(c, "derecho", base.derecho),
        comision_iva=base.comision_iva,
        derecho_iva=base.derecho_iva,
        derecho_sobre_prima=_override(c, "derecho_sobre_prima", base.derecho_sobre_prima),
        prima_iva=base.prima_iva,
        nota=base.nota,
    )


def costo_por_tramo(clase: Optional[str], stress_factor: float = 1.0) -> float:
    """Fracción del monto que se lleva UNA punta de la operación."""
    a = arancel_de(clase)
    comision = (a.comision * stress_factor) * (1 + IVA_PCT if a.comision_iva else 1)
    derecho = a.derecho * (1 + IVA_PCT if a.derecho_iva else 1)
    prima = a.derecho_sobre_prima * (1 + IVA_PCT if a.prima_iva else 1)
    return round(comision + derecho + prima, 8)


def costo_redondo(clase: Optional[str], spread_pct: float = 0.0,
                  stress_factor: float = 1.0) -> float:
    """Fracción del monto que se lleva la operación completa (ida y vuelta).

    El spread se suma UNA sola vez, no dos: es la diferencia entre puntas que
    se paga al cruzar, no un arancel por tramo.
    """
    return round(costo_por_tramo(clase, stress_factor) * 2 + (spread_pct / 100.0), 8)


def costo_redondo_pct(clase: Optional[str], spread_pct: float = 0.0,
                      stress_factor: float = 1.0) -> float:
    """El mismo costo en PUNTOS PORCENTUALES (1.6456, no 0.016456).

    Las dos unidades siguen separadas y con nombres explícitos. Es la lección
    del bug más caro de la historia del proyecto: una sola función que
    devolvía fracción se usaba también donde hacía falta el porcentaje, y el
    filtro de rentabilidad restaba cien veces menos fricción de la real
    durante versiones enteras, sin que nada fallara ni apareciera en el log.
    """
    return round(costo_redondo(clase, spread_pct, stress_factor) * 100, 6)


def costo_caucion(monto_ars: float, dias: int, stress_factor: float = 1.0) -> float:
    """Costo en PESOS de una caución colocadora, prorrateado por plazo.

    Se saca de las funciones genéricas porque su arancel es anualizado.
    Aplicarle 2% a una caución de un día como si fuera una comisión de
    transacción sobrestima el costo unas 365 veces y hace que ninguna caución
    pase nunca el filtro — el bot dejaría de colocar liquidez ociosa por un
    error de unidades, sin que nadie entendiera por qué.
    """
    a = arancel_de("CAUCIONES")
    tasa_anual = a.comision * stress_factor * (1 + IVA_PCT if a.comision_iva else 1)
    comision = monto_ars * tasa_anual * (dias / 365.0)
    derecho = monto_ars * a.derecho * (dias / 365.0) * (1 + IVA_PCT if a.derecho_iva else 1)
    return round(comision + derecho, 2)


def desglose(clase: Optional[str], monto_ars: float, spread_pct: float = 0.0,
             stress_factor: float = 1.0) -> dict:
    """Detalle línea por línea, para el panel y para el reporte de cierre.

    El sistema tiene que poder explicar de dónde sale cada peso de fricción:
    cuando la reconciliación diaria muestre un desvío contra el back-office,
    la pregunta va a ser qué componente se estimó mal, y un total agregado no
    la responde.
    """
    c = normalizar_clase(clase)
    a = arancel_de(c)
    comision_tramo = monto_ars * a.comision * stress_factor
    iva_comision = comision_tramo * (IVA_PCT if a.comision_iva else 0)
    derecho_tramo = monto_ars * a.derecho
    iva_derecho = derecho_tramo * (IVA_PCT if a.derecho_iva else 0)
    prima_tramo = monto_ars * a.derecho_sobre_prima
    iva_prima = prima_tramo * (IVA_PCT if a.prima_iva else 0)
    total_tramo = comision_tramo + iva_comision + derecho_tramo + iva_derecho + prima_tramo + iva_prima
    spread_ars = monto_ars * (spread_pct / 100.0)
    return {
        "clase": c,
        "comision_ars": round(comision_tramo, 2),
        "iva_comision_ars": round(iva_comision, 2),
        "derecho_mercado_ars": round(derecho_tramo, 2),
        "iva_derecho_ars": round(iva_derecho, 2),
        "derecho_prima_ars": round(prima_tramo + iva_prima, 2),
        "total_por_tramo_ars": round(total_tramo, 2),
        "total_redondo_ars": round(total_tramo * 2 + spread_ars, 2),
        "spread_ars": round(spread_ars, 2),
        "costo_redondo_pct": costo_redondo_pct(c, spread_pct, stress_factor),
        "nota": a.nota,
    }


# ---------------------------------------------------------------------------
# Conciliación contra el bróker, ANTES de operar
# ---------------------------------------------------------------------------

TOLERANCIA_CONCILIACION_PCT = float(os.getenv("FEE_RECONCILE_TOLERANCE_PCT", "5.0"))


def conciliar_contra_broker(ppi_client, account_number: str, ticker: str,
                            clase: str, cantidad: int, precio: float,
                            settlement: str = "A-24HS") -> dict:
    """Compara el costo estimado por este módulo contra el que informa PPI en
    el presupuesto de la orden.

    `budget_order()` simula y devuelve el costo SIN colocar nada, así que es
    seguro llamarlo en el camino de evaluación. Que exista esta función es lo
    que convierte el tarifario de arriba en algo verificable: los números de
    la grilla salen de un PDF comercial que puede cambiar sin aviso, y este
    es el control que detecta que cambió.

    Un desvío por encima de la tolerancia no bloquea por sí solo: devuelve
    `dentro_de_tolerancia=False` y quien llama decide. Bloquear acá
    convertiría un cambio de arancel en una parada total del sistema.
    """
    monto = cantidad * precio
    estimado = desglose(clase, monto)["total_por_tramo_ars"]
    resultado = {"estimado_ars": estimado, "broker_ars": None,
                 "desvio_pct": None, "dentro_de_tolerancia": None, "motivo": ""}
    try:
        presupuesto = ppi_client.budget_order(
            account_number=account_number, quantity=cantidad, price=precio,
            ticker=ticker, instrument_type=normalizar_clase(clase),
            settlement=settlement)
    except Exception as e:
        resultado["motivo"] = f"No se pudo pedir el presupuesto: {type(e).__name__}: {e}"
        return resultado
    if not presupuesto:
        resultado["motivo"] = "El bróker no devolvió presupuesto."
        return resultado

    real = None
    for campo in ("commission", "comision", "costs", "costos", "fees", "totalCosts"):
        v = presupuesto.get(campo) if isinstance(presupuesto, dict) else None
        if v is not None:
            try:
                real = float(v)
                break
            except (TypeError, ValueError):
                continue
    if real is None:
        resultado["motivo"] = "El presupuesto no trae un campo de costos reconocible."
        return resultado

    resultado["broker_ars"] = round(real, 2)
    if estimado > 0:
        desvio = (real - estimado) / estimado * 100
        resultado["desvio_pct"] = round(desvio, 2)
        resultado["dentro_de_tolerancia"] = abs(desvio) <= TOLERANCIA_CONCILIACION_PCT
        if not resultado["dentro_de_tolerancia"]:
            logger.warning(
                "Desvío del modelo de costos en %s (%s): estimado $%.2f vs "
                "bróker $%.2f (%.1f%%). Revisar el tarifario.",
                ticker, normalizar_clase(clase), estimado, real, desvio)
    return resultado


def tabla_para_documentacion() -> list:
    """Filas listas para renderizar en el panel y en el documento maestro.
    Se genera desde la grilla real para que la tabla del documento no pueda
    desincronizarse del código: la inconsistencia de inventario fue un
    hallazgo propio de la auditoría anterior."""
    filas = []
    for clase in ("ACCIONES", "CEDEARS", "BONOS", "OBLIGACIONES", "OPCIONES",
                  "FUTUROS", "LETRAS", "CAUCIONES"):
        a = arancel_de(clase)
        filas.append({
            "clase": clase,
            "comision_pct": round(a.comision * 100, 4),
            "iva_comision": "sí" if a.comision_iva else "exento",
            "derecho_pct": round(a.derecho * 100, 4),
            "iva_derecho": "sí" if a.derecho_iva else "exento",
            "redondo_pct": costo_redondo_pct(clase),
            "nota": a.nota,
        })
    return filas
