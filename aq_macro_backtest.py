"""
aq_macro_backtest.py — Backtest del filtro macroeconómico

QUÉ PREGUNTA RESPONDE
---------------------------------------------------------------------------
El sistema descarta operaciones cuyo retorno neto esperado no le gana al
piso de rentabilidad —inflación más tasa, prorrateado por los días de
tenencia—. Es un filtro razonable en teoría. La pregunta que nunca se había
respondido con datos es si además funciona: ¿las operaciones que el filtro
descartó habrían perdido plata, o descartó buenas oportunidades?

Un filtro puede fallar de dos maneras opuestas, y las dos cuestan:

  DEMASIADO PERMISIVO: deja pasar operaciones que terminan por debajo del
  piso. El costo es visible en la cuenta.

  DEMASIADO EXIGENTE: descarta operaciones que habrían sido rentables. El
  costo es invisible —no aparece en ningún resumen, porque una operación que
  no se hizo no deja rastro— y por eso es el error más fácil de mantener
  durante años sin enterarse.

Este módulo mide las dos. Recorre el histórico archivado, reconstruye para
cada fecha el contexto macro que existía ESE día, aplica el filtro con esos
valores y compara contra lo que efectivamente pasó después.

LA REGLA QUE NO SE PUEDE ROMPER: NO MIRAR EL FUTURO
---------------------------------------------------------------------------
El error clásico de un backtest es usar, para decidir en una fecha, un dato
que recién se conoció después. Con series macro argentinas es especialmente
fácil de cometer, porque la inflación de un mes se publica a mediados del
mes siguiente: usar el IPC de marzo para decidir el 5 de marzo produce
resultados excelentes y completamente falsos.

Acá se aplica un desfasaje explícito (MACRO_PUBLICATION_LAG_DAYS): para
decidir en la fecha D solo se usan valores publicados hasta D menos el
desfasaje. Los resultados van a ser peores que sin esa precaución. Esa es
justamente la señal de que la precaución sirve.
"""

RC4_MODULE_ROLE = 'OPS_CLI_OFFLINE'
RC4_MODULE_ROLE_REASON = 'Backtest macroeconómico offline; no forma parte del hot path.'

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger("macro_backtest")

HIST_DB_PATH = os.getenv("HIST_DB_PATH", "./data/market_history.db")
MACRO_PUBLICATION_LAG_DAYS = int(os.getenv("MACRO_PUBLICATION_LAG_DAYS", "20"))
BACKTEST_HOLD_DAYS = int(os.getenv("BACKTEST_HOLD_DAYS", "5"))
BACKTEST_SPREAD_PCT = float(os.getenv("BACKTEST_ASSUMED_SPREAD_PCT", "0.3"))
BACKTEST_ATR_MULT = float(os.getenv("TAKE_PROFIT_ATR_MULT", "2.0"))


@dataclass
class ResultadoBacktest:
    instrumentos: int = 0
    oportunidades_evaluadas: int = 0
    aprobadas: int = 0
    descartadas: int = 0
    # Las cuatro celdas de la matriz de aciertos y errores:
    aprobadas_ganadoras: int = 0
    aprobadas_perdedoras: int = 0
    descartadas_que_hubieran_ganado: int = 0
    descartadas_que_hubieran_perdido: int = 0
    retorno_medio_aprobadas: float = 0.0
    retorno_medio_descartadas: float = 0.0
    hurdle_medio_aplicado: float = 0.0
    veredicto: str = ""
    detalle: List[dict] = field(default_factory=list)

    @property
    def precision(self) -> float:
        """De lo que el filtro aprobó, qué proporción terminó ganando."""
        total = self.aprobadas_ganadoras + self.aprobadas_perdedoras
        return self.aprobadas_ganadoras / total if total else 0.0

    @property
    def costo_de_oportunidad(self) -> float:
        """De lo que descartó, qué proporción habría ganado. Este es el número
        que no aparece en ningún lado si no se lo mide a propósito."""
        total = self.descartadas_que_hubieran_ganado + self.descartadas_que_hubieran_perdido
        return self.descartadas_que_hubieran_ganado / total if total else 0.0


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(HIST_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _macro_disponible_en(fecha: date, series_macro: Dict[str, List[tuple]]) -> Optional[float]:
    """
    Inflación mensual conocida en `fecha`, respetando el desfasaje de
    publicación. Devuelve None si en esa fecha todavía no había ningún dato
    publicado — y en ese caso la oportunidad se omite en vez de rellenarse con
    un promedio. Rellenar sería inventar información que el bot no tenía.
    """
    limite = fecha - timedelta(days=MACRO_PUBLICATION_LAG_DAYS)
    candidatos = [(f, v) for f, v in series_macro.get("ipc_var_mensual", []) if f <= limite]
    if not candidatos:
        return None
    return candidatos[-1][1]


def _cargar_series_macro(ruta_db: str = None) -> Dict[str, List[tuple]]:
    """Lee las series macro archivadas por ad_macro_history.py."""
    series: Dict[str, List[tuple]] = {}
    try:
        import ac_db
        conn = ac_db.connect_raw()
        conn.row_factory = sqlite3.Row
        filas = conn.execute(
            "SELECT serie, fecha, valor FROM macro_history ORDER BY fecha ASC").fetchall()
        conn.close()
        for f in filas:
            series.setdefault(f["serie"], []).append(
                (datetime.fromisoformat(f["fecha"][:10]).date(), float(f["valor"])))
    except Exception as e:
        logger.warning("No se pudieron leer las series macro: %s", e)
    return series


def _atr_simple(velas: List[sqlite3.Row], i: int, periodo: int = 14) -> Optional[float]:
    """ATR clásico sobre las velas anteriores a i. Solo mira hacia atrás."""
    if i < periodo:
        return None
    rangos = []
    for k in range(i - periodo, i):
        anterior = velas[k - 1]["close"] if k > 0 else velas[k]["close"]
        rangos.append(max(velas[k]["high"] - velas[k]["low"],
                          abs(velas[k]["high"] - anterior),
                          abs(velas[k]["low"] - anterior)))
    return sum(rangos) / len(rangos) if rangos else None


def correr(simbolos: Optional[List[str]] = None,
           hold_days: int = BACKTEST_HOLD_DAYS) -> ResultadoBacktest:
    """
    Recorre el archivo histórico y evalúa el filtro macro día por día.

    Para cada fecha con historia suficiente se calcula el retorno bruto que el
    sistema habría esperado (ATR × múltiplo de take-profit, la misma fórmula
    que usa j_main), se le descuenta la fricción real, se lo compara contra el
    piso vigente ese día, y se anota qué pasó realmente en los días siguientes.
    """
    import d_economics as economics

    resultado = ResultadoBacktest()
    series_macro = _cargar_series_macro()
    if not series_macro.get("ipc_var_mensual"):
        resultado.veredicto = ("No hay serie de inflación archivada. Corré primero el refresco "
                               "de ad_macro_history.py: sin el dato macro no hay filtro que probar.")
        return resultado

    try:
        conn = _conn()
        if simbolos:
            marcadores = ",".join("?" * len(simbolos))
            tickers = [r[0] for r in conn.execute(
                f"SELECT DISTINCT symbol FROM market_historical_ohlcv WHERE symbol IN ({marcadores})",
                simbolos)]
        else:
            tickers = [r[0] for r in conn.execute(
                "SELECT DISTINCT symbol FROM market_historical_ohlcv")]
    except sqlite3.Error as e:
        resultado.veredicto = f"No se pudo leer el archivo histórico: {e}"
        return resultado

    retornos_aprobadas, retornos_descartadas, hurdles = [], [], []

    for ticker in tickers:
        velas = conn.execute(
            "SELECT date, open, high, low, close FROM market_historical_ohlcv "
            "WHERE symbol=? ORDER BY date ASC", (ticker,)).fetchall()
        if len(velas) < 40 + hold_days:
            continue
        resultado.instrumentos += 1

        for i in range(20, len(velas) - hold_days):
            fecha = datetime.fromisoformat(velas[i]["date"]).date()
            inflacion = _macro_disponible_en(fecha, series_macro)
            if inflacion is None:
                continue

            atr = _atr_simple(velas, i)
            precio = velas[i]["close"]
            if not atr or not precio or precio <= 0:
                continue

            # Misma fórmula que el sistema en vivo, en puntos porcentuales.
            bruto_esperado = (atr / precio) * 100 * BACKTEST_ATR_MULT
            neto_esperado = economics.calculate_net_return_pct(bruto_esperado, BACKTEST_SPREAD_PCT)
            veredicto = economics.passes_hurdle(neto_esperado, hold_days, inflacion)
            hurdles.append(veredicto["hurdle_prorated_pct"])

            precio_salida = velas[i + hold_days]["close"]
            retorno_real_bruto = (precio_salida - precio) / precio * 100
            retorno_real_neto = retorno_real_bruto - economics.calculate_trade_costs_pct(BACKTEST_SPREAD_PCT)
            gano = retorno_real_neto > veredicto["hurdle_prorated_pct"]

            resultado.oportunidades_evaluadas += 1
            if veredicto["approved"]:
                resultado.aprobadas += 1
                retornos_aprobadas.append(retorno_real_neto)
                if gano:
                    resultado.aprobadas_ganadoras += 1
                else:
                    resultado.aprobadas_perdedoras += 1
            else:
                resultado.descartadas += 1
                retornos_descartadas.append(retorno_real_neto)
                if gano:
                    resultado.descartadas_que_hubieran_ganado += 1
                else:
                    resultado.descartadas_que_hubieran_perdido += 1

    conn.close()

    if retornos_aprobadas:
        resultado.retorno_medio_aprobadas = round(sum(retornos_aprobadas) / len(retornos_aprobadas), 4)
    if retornos_descartadas:
        resultado.retorno_medio_descartadas = round(sum(retornos_descartadas) / len(retornos_descartadas), 4)
    if hurdles:
        resultado.hurdle_medio_aplicado = round(sum(hurdles) / len(hurdles), 4)

    resultado.veredicto = _interpretar(resultado)
    return resultado


def _interpretar(r: ResultadoBacktest) -> str:
    """Traduce los números a una conclusión accionable.

    Se dice explícitamente qué hacer con el resultado, porque un backtest que
    devuelve seis porcentajes y ninguna conclusión termina archivado sin que
    nadie cambie nada.
    """
    if r.oportunidades_evaluadas == 0:
        return ("No hubo oportunidades evaluables. Falta historia archivada o falta "
                "serie macro con la antigüedad suficiente.")

    partes = [
        f"Se evaluaron {r.oportunidades_evaluadas} oportunidades sobre {r.instrumentos} "
        f"instrumentos. El filtro aprobó {r.aprobadas} y descartó {r.descartadas}.",
        f"De las aprobadas, {r.precision:.0%} superaron el piso realmente "
        f"(retorno neto medio: {r.retorno_medio_aprobadas:+.2f}%).",
        f"De las descartadas, {r.costo_de_oportunidad:.0%} lo habrían superado "
        f"(retorno neto medio: {r.retorno_medio_descartadas:+.2f}%).",
    ]

    if r.retorno_medio_aprobadas <= r.retorno_medio_descartadas:
        partes.append(
            "CONCLUSIÓN: el filtro NO está separando bien. Lo que aprueba rinde igual o peor "
            "que lo que descarta, o sea que no está aportando información: está filtrando casi "
            "al azar. Revisar la fórmula del retorno esperado antes que el umbral, porque el "
            "problema no parece ser dónde está puesta la vara sino qué se está midiendo.")
    elif r.costo_de_oportunidad > 0.45:
        partes.append(
            "CONCLUSIÓN: el filtro separa bien, pero está demasiado exigente: casi la mitad de "
            "lo que descarta habría sido rentable. Conviene bajar el piso o revisar si el "
            "prorrateo por días de tenencia no está castigando de más las operaciones cortas.")
    elif r.precision < 0.5:
        partes.append(
            "CONCLUSIÓN: el filtro está demasiado permisivo: menos de la mitad de lo que aprueba "
            "termina superando el piso. Subir el margen exigido sobre el hurdle.")
    else:
        partes.append(
            "CONCLUSIÓN: el filtro está razonablemente calibrado. Aprueba mayoritariamente "
            "operaciones que superan el piso y lo que descarta rinde menos que lo que aprueba. "
            "No hay motivo para tocar el umbral con esta evidencia.")

    return " ".join(partes)


def guardar_informe(resultado: ResultadoBacktest,
                    ruta: str = "./data/backtest_macro.json") -> str:
    os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
    salida = asdict(resultado)
    salida["precision"] = round(resultado.precision, 4)
    salida["costo_de_oportunidad"] = round(resultado.costo_de_oportunidad, 4)
    salida["generado"] = datetime.now().isoformat(timespec="seconds")
    salida["desfasaje_publicacion_dias"] = MACRO_PUBLICATION_LAG_DAYS
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)
    return ruta


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print("Corriendo backtest del filtro macro...\n")
    r = correr()
    print(r.veredicto)
    print(f"\nInforme guardado en {guardar_informe(r)}")
