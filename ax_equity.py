"""
ax_equity.py — Patrimonio de la cuenta (v16.2)

QUÉ RESUELVE ESTE MÓDULO, EN UNA FRASE
--------------------------------------
Hasta esta versión el sistema no sabía cuánto valía la cuenta. Sabía cuánta
plata había suelta en el renglón de pesos, que es una cosa distinta, y sobre
ese número construyó tres controles: el límite de pérdida diaria, el
drawdown y la base del dimensionamiento de posiciones.

Por qué eso rompe, con números concretos:

  · Comprar no es perder. Una compra mueve plata de la columna "efectivo" a
    la columna "tenencia": el patrimonio no cambia, la caja sí. Con un límite
    de pérdida diaria del 1%, la primera orden que consumiera más del 1% del
    efectivo hacía que el sistema declarara "pérdida diaria" y activara el
    kill switch. Y como una pérdida es un corte FINANCIERO, el supervisor
    nunca lo auto-libera: hace falta una persona. En la práctica, el bot se
    apagaba solo con la primera operación de cada día.

  · Perder no mueve la caja. Una posición abierta que cae 30% deja el
    efectivo exactamente igual. La protección que justifica todo el diseño
    conservador del sistema no se enteraba.

  · Un retiro parece una pérdida. Sacar plata de la cuenta baja el
    patrimonio sin que haya existido ninguna operación mala, y activaba un
    corte financiero. Un depósito, al revés, elevaba el pico histórico de
    forma permanente y endurecía el umbral de drawdown para siempre.

La corrección es una sola pieza —esta— consumida desde tres lugares:
p_risk_guardian (pérdida diaria y drawdown), d_economics (base del riesgo por
operación) y h_daily_report (foto de apertura y cierre).

PRINCIPIO DE DISEÑO: este módulo NUNCA inventa un número. Si no puede
establecer el patrimonio con datos reales del bróker, devuelve None y dice
por qué. Un patrimonio estimado a ojo alimentando un kill switch es peor que
no tener kill switch, porque da una sensación de protección que no existe.
"""

import logging
import os
import datetime as _dt
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

import ac_db

logger = logging.getLogger("equity")

# Etiquetas con las que PPI identifica el renglón en pesos. Se comparan en
# mayúsculas y por contención, no por igualdad: el nombre exacto del campo ha
# cambiado entre versiones de la API y una comparación estricta se rompe en
# silencio (devolvería "no hay pesos" en vez de fallar).
_ETIQUETAS_PESOS = ("PESO", "ARS", "$")

# Campos candidatos donde PPI informa la valuación a mercado de una tenencia.
# Se prueban en orden. Es deliberado que sean varios: la API no es estable en
# el nombre y el sistema tiene que sobrevivir a que renombren uno.
_CAMPOS_VALUACION = ("amount", "marketValue", "valuation", "amountArs",
                     "montoValorizado", "valorizado")

_CAMPOS_MONEDA = ("currency", "moneda", "simbol", "symbol")


@dataclass
class Equity:
    """Foto del patrimonio en un instante.

    `parcial` es la bandera importante: True significa que se pudo leer el
    efectivo pero NO la tenencia. En ese caso `total` no es el patrimonio
    real y no debe alimentar ningún control de riesgo — el consumidor tiene
    que tratarlo como dato faltante, que en este sistema significa
    abstenerse, no seguir con lo que haya.
    """
    efectivo_ars: Optional[float] = None
    tenencia_ars: Optional[float] = None
    total: Optional[float] = None
    parcial: bool = False
    motivo: str = ""
    detalle_tenencia: List[Dict[str, Any]] = field(default_factory=list)
    tomado_en: str = ""

    @property
    def utilizable(self) -> bool:
        """True solo si el número sirve para decidir sobre dinero."""
        return self.total is not None and not self.parcial


def _a_float(valor) -> Optional[float]:
    try:
        if valor is None:
            return None
        return float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _es_pesos(fila: dict) -> bool:
    etiqueta = " ".join(str(fila.get(c, "")) for c in _CAMPOS_MONEDA).upper()
    etiqueta += " " + str(fila.get("name", "")).upper()
    return any(m in etiqueta for m in _ETIQUETAS_PESOS)


def saldo_pesos(ppi_client) -> Optional[float]:
    """Efectivo en pesos disponible. Devuelve None si no se pudo leer —
    nunca 0, que es un número que el resto del sistema interpretaría como
    'la cuenta está vacía' en vez de 'no sé cuánto hay'."""
    try:
        balances = ppi_client.get_available_balance()
    except Exception as e:
        logger.warning("No se pudo leer el saldo disponible: %s", e)
        return None
    if not balances:
        return None
    for b in balances:
        if not isinstance(b, dict):
            continue
        if _es_pesos(b):
            monto = _a_float(b.get("amount"))
            if monto is not None:
                return monto
    return None


def valuacion_tenencia(ppi_client) -> tuple:
    """(valuación_total_en_pesos, detalle, motivo_si_fallo).

    La valuación se toma tal como la informa el bróker. Deliberadamente NO se
    recalcula multiplicando cantidad por último precio: el bróker conoce el
    plazo de liquidación, los cortes de cupón y los ajustes que el bot no ve,
    y para un control de riesgo la cifra del bróker es la que va a discutirse
    después contra el resumen de cuenta.

    Las tenencias en dólares se EXCLUYEN del total en vez de convertirse a un
    tipo de cambio elegido por el bot. Convertirlas al CCL metería la
    volatilidad cambiaria dentro del cálculo de pérdida diaria: un salto del
    dólar dispararía el kill switch sin que ninguna posición se hubiera
    movido. Quedan listadas en el detalle para que el panel las muestre.
    """
    try:
        posiciones = ppi_client.get_portfolio()
    except Exception as e:
        return None, [], f"No se pudo leer la tenencia: {type(e).__name__}: {e}"
    if posiciones is None:
        return None, [], "El bróker no devolvió la tenencia (respuesta vacía)."
    if not posiciones:
        # Cuenta sin posiciones abiertas es un caso legítimo, no un error.
        return 0.0, [], ""

    total = 0.0
    detalle: List[Dict[str, Any]] = []
    sin_valuar = []
    for p in posiciones:
        if not isinstance(p, dict):
            continue
        ticker = p.get("ticker") or p.get("symbol") or p.get("instrument") or "?"
        monto = None
        for campo in _CAMPOS_VALUACION:
            monto = _a_float(p.get(campo))
            if monto is not None:
                break
        en_pesos = _es_pesos(p) or not any(
            str(p.get(c, "")).upper() in ("USD", "DOLAR", "DÓLAR", "U$S")
            for c in _CAMPOS_MONEDA)
        fila = {"ticker": ticker, "valuacion": monto, "en_pesos": en_pesos,
                "cantidad": _a_float(p.get("quantity")) or _a_float(p.get("cantidad"))}
        detalle.append(fila)
        if monto is None:
            sin_valuar.append(ticker)
            continue
        if en_pesos:
            total += monto

    if sin_valuar:
        # Una tenencia sin valuación conocida es un hueco en el patrimonio.
        # Se informa como fallo, no se suma como cero: sumar cero equivale a
        # afirmar que esa posición no vale nada.
        return None, detalle, ("El bróker no informó valuación para: "
                               + ", ".join(sin_valuar[:6]))
    return round(total, 2), detalle, ""


def patrimonio_actual(ppi_client) -> Equity:
    """Efectivo en pesos + valuación a mercado de la tenencia en pesos.

    Es LA función del módulo. Todo lo demás la rodea.
    """
    ahora = _dt.datetime.now().isoformat(timespec="seconds")
    efectivo = saldo_pesos(ppi_client)
    if efectivo is None:
        return Equity(parcial=True, tomado_en=ahora,
                      motivo="No se pudo leer el efectivo en pesos de la cuenta.")

    tenencia, detalle, motivo = valuacion_tenencia(ppi_client)
    if tenencia is None:
        # Se devuelve el efectivo para que el panel muestre algo, pero
        # marcado como parcial: no sirve para decidir.
        return Equity(efectivo_ars=efectivo, tenencia_ars=None, total=efectivo,
                      parcial=True, motivo=motivo, detalle_tenencia=detalle,
                      tomado_en=ahora)

    return Equity(efectivo_ars=efectivo, tenencia_ars=tenencia,
                  total=round(efectivo + tenencia, 2), parcial=False,
                  detalle_tenencia=detalle, tomado_en=ahora)


# ---------------------------------------------------------------------------
# Flujos de caja: depósitos y retiros
# ---------------------------------------------------------------------------
# Sin esto, mover plata hacia adentro o hacia afuera de la cuenta se lee como
# ganancia o pérdida del día. Es el defecto que P1-22 describe para el
# drawdown y que P0-01 describe para la pérdida diaria: la misma causa.

_TIPOS_DEPOSITO = ("DEPOSITO", "DEPÓSITO", "TRANSFERENCIA RECIBIDA",
                   "ACREDITACION", "ACREDITACIÓN", "INGRESO")
_TIPOS_RETIRO = ("RETIRO", "EXTRACCION", "EXTRACCIÓN", "TRANSFERENCIA ENVIADA",
                 "EGRESO")


def flujos_netos_del_dia(ppi_client, dia: Optional[_dt.date] = None) -> float:
    """Depósitos menos retiros del día, en pesos. Cero si no se puede
    determinar (que es el supuesto conservador: asumir que NO hubo flujos
    hace que un depósito real se lea como ganancia, lo cual relaja el corte;
    asumir que sí los hubo, al revés, podría apagar el corte de verdad. Entre
    los dos errores, este es el que no desactiva una protección).
    """
    dia = dia or _dt.date.today()
    try:
        movimientos = ppi_client.get_movements(
            _dt.datetime.combine(dia, _dt.time.min),
            _dt.datetime.combine(dia, _dt.time.max))
    except Exception as e:
        logger.warning("No se pudieron leer los movimientos del día: %s", e)
        return 0.0
    if not movimientos:
        return 0.0

    neto = 0.0
    for m in movimientos:
        if not isinstance(m, dict):
            continue
        tipo = str(m.get("type") or m.get("tipo") or m.get("description") or "").upper()
        monto = _a_float(m.get("amount")) or _a_float(m.get("monto")) or 0.0
        if any(t in tipo for t in _TIPOS_DEPOSITO):
            neto += abs(monto)
        elif any(t in tipo for t in _TIPOS_RETIRO):
            neto -= abs(monto)
    return round(neto, 2)


# ---------------------------------------------------------------------------
# Persistencia de las fotos de patrimonio
# ---------------------------------------------------------------------------
# Se guardan DOS columnas, patrimonio y efectivo, en vez de una sola. La razón
# es de auditoría: cuando el sistema corte por pérdida diaria, la primera
# pregunta va a ser si la pérdida fue de precio o de caja, y con una sola
# columna esa pregunta no se puede responder después de los hechos.

def _init_tabla():
    with ac_db.connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS equity_snapshot (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT NOT NULL,
                sesion TEXT NOT NULL,
                patrimonio_ars REAL,
                efectivo_ars REAL,
                tenencia_ars REAL,
                parcial INTEGER DEFAULT 0,
                tomado_en TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(fecha, sesion)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS equity_peak (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                pico_ars REAL,
                alcanzado_en TEXT
            )
        """)


def guardar_snapshot(equity: Equity, sesion: str = "open",
                     dia: Optional[_dt.date] = None) -> bool:
    """Graba la foto del día. Una foto PARCIAL no se guarda: escribirla
    convertiría un dato faltante en un dato malo, y mañana nadie sabría que
    ese número nunca fue el patrimonio real."""
    if equity.parcial or equity.total is None:
        logger.warning("Foto de patrimonio no guardada (parcial): %s", equity.motivo)
        return False
    dia = dia or _dt.date.today()
    _init_tabla()
    with ac_db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO equity_snapshot "
            "(fecha, sesion, patrimonio_ars, efectivo_ars, tenencia_ars, parcial) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (dia.isoformat(), sesion, equity.total, equity.efectivo_ars,
             equity.tenencia_ars))
    actualizar_pico(equity.total)
    return True


def leer_snapshot(sesion: str = "open", dia: Optional[_dt.date] = None) -> Optional[float]:
    dia = dia or _dt.date.today()
    _init_tabla()
    with ac_db.connect() as conn:
        fila = conn.execute(
            "SELECT patrimonio_ars FROM equity_snapshot WHERE fecha = ? AND sesion = ?",
            (dia.isoformat(), sesion)).fetchone()
    return fila[0] if fila and fila[0] else None


def actualizar_pico(patrimonio: float) -> float:
    """El pico se guarda en su propia fila, no se deriva de un MAX() sobre
    las fotos. Derivarlo mezclaba fotos de efectivo con fotos de patrimonio
    —magnitudes distintas— y el máximo salía de la que fuera más grande por
    accidente, no de la más alta de verdad."""
    _init_tabla()
    with ac_db.connect() as conn:
        fila = conn.execute("SELECT pico_ars FROM equity_peak WHERE id = 1").fetchone()
        pico = fila[0] if fila and fila[0] else None
        if pico is None or patrimonio > pico:
            conn.execute(
                "INSERT INTO equity_peak (id, pico_ars, alcanzado_en) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET pico_ars = excluded.pico_ars, "
                "alcanzado_en = excluded.alcanzado_en",
                (patrimonio, _dt.datetime.now().isoformat(timespec="seconds")))
            return patrimonio
    return pico


def leer_pico() -> Optional[float]:
    _init_tabla()
    with ac_db.connect() as conn:
        fila = conn.execute("SELECT pico_ars FROM equity_peak WHERE id = 1").fetchone()
    return fila[0] if fila and fila[0] else None


def ajustar_pico_por_flujo(flujo_neto_ars: float) -> None:
    """Un depósito no es una ganancia: sube el patrimonio sin que el sistema
    haya hecho nada bien. Si el pico no se corrige por el mismo monto, el
    umbral de drawdown queda endurecido de forma permanente. Un retiro es el
    caso simétrico."""
    if not flujo_neto_ars:
        return
    pico = leer_pico()
    if pico is None:
        return
    nuevo = max(pico + flujo_neto_ars, 0.0)
    _init_tabla()
    with ac_db.connect() as conn:
        conn.execute("UPDATE equity_peak SET pico_ars = ? WHERE id = 1", (nuevo,))
    logger.info("Pico de patrimonio ajustado por flujo de caja: %s → %s", pico, nuevo)


# ---------------------------------------------------------------------------
# Las dos cuentas que consume el kill switch
# ---------------------------------------------------------------------------

def perdida_diaria_pct(ppi_client, dia: Optional[_dt.date] = None) -> dict:
    """{'ok': bool, 'pct': float|None, 'motivo': str, 'equity': Equity}

    pct negativo = pérdida. El flujo de caja se descuenta ANTES de calcular
    el porcentaje: un depósito de mediodía no puede aparecer como ganancia
    del día, ni un retiro como pérdida.
    """
    equity = patrimonio_actual(ppi_client)
    if not equity.utilizable:
        return {"ok": False, "pct": None, "equity": equity,
                "motivo": equity.motivo or "Patrimonio no determinable."}

    apertura = leer_snapshot("open", dia)
    if not apertura:
        return {"ok": False, "pct": None, "equity": equity,
                "motivo": "Todavía no hay foto de apertura del patrimonio de hoy."}

    flujos = flujos_netos_del_dia(ppi_client, dia)
    pct = (equity.total - flujos - apertura) / apertura * 100
    return {"ok": True, "pct": round(pct, 4), "equity": equity,
            "apertura": apertura, "flujos": flujos, "motivo": ""}


def drawdown_pct(ppi_client) -> dict:
    """Caída desde el pico histórico de PATRIMONIO hasta el patrimonio ACTUAL.

    Dos correcciones respecto de la versión anterior, ambas del mismo
    hallazgo: se compara contra el valor actual y no contra el saldo de
    apertura del día (que es un número de la mañana, no de ahora), y el pico
    sale de su propia columna en vez de un MAX() sobre fotos heterogéneas.
    """
    equity = patrimonio_actual(ppi_client)
    if not equity.utilizable:
        return {"ok": False, "pct": None, "equity": equity,
                "motivo": equity.motivo or "Patrimonio no determinable."}
    pico = leer_pico()
    if not pico:
        actualizar_pico(equity.total)
        return {"ok": True, "pct": 0.0, "equity": equity, "pico": equity.total,
                "motivo": "Primer registro de pico."}
    actualizar_pico(equity.total)
    caida = (pico - equity.total) / pico * 100
    return {"ok": True, "pct": round(max(caida, 0.0), 4), "equity": equity,
            "pico": pico, "motivo": ""}


def base_de_riesgo(ppi_client, fallback_efectivo: Optional[float] = None) -> Optional[float]:
    """Capital sobre el que se calcula el porcentaje de riesgo por operación.

    Antes se usaba el efectivo DISPONIBLE, y eso tenía un efecto que nadie
    decidió: a medida que el capital se iba invirtiendo, el efectivo bajaba y
    el riesgo por operación se achicaba solo. La tercera posición del día
    arriesgaba bastante menos que la primera, sin que ninguna regla lo
    dijera. Con el patrimonio como base, el 1% es el 1% siempre.
    """
    equity = patrimonio_actual(ppi_client)
    if equity.utilizable:
        return equity.total
    logger.warning("Base de riesgo: patrimonio no disponible (%s).", equity.motivo)
    return fallback_efectivo
