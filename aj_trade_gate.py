"""
aj_trade_gate.py — Catálogo único de los casos en que el sistema NO opera

POR QUÉ ESTE MÓDULO EXISTE
---------------------------------------------------------------------------
Las razones para no operar estaban repartidas por todo el código: algunas
en el bucle principal, otras adentro del motor de contexto, otras en el
guardián de riesgo. Funcionaban, pero tenían dos problemas prácticos. El
primero es que nadie podía responder la pregunta "¿en qué casos exactos
este bot se abstiene?" sin leer cuatro archivos. El segundo, más serio, es
que todas las abstenciones pesaban igual: un feed de noticias caído
frenaba el sistema con la misma dureza que un kill switch financiero,
cuando son cosas de naturaleza completamente distinta.

Acá están todas, enumeradas, con código propio, y —esto es lo que cambia
de fondo— ORDENADAS POR NIVEL. El nivel define qué se bloquea:

  NIVEL 0 — PARADA TOTAL. No se evalúa nada. Ni siquiera se mira el
      mercado. Son fallas de las que el sistema no puede razonar hacia
      afuera: si no hay sesión con el bróker, cualquier análisis es
      literatura.

  NIVEL 1 — NO SE ABRE, SE CUIDA. Las posiciones abiertas se siguen
      vigilando y se pueden cerrar; no se abre nada nuevo. Es el nivel
      correcto para los límites de exposición y de pérdida: cerrar tiene
      que seguir siendo posible siempre, porque prohibir cerrar es la
      única forma segura de convertir una pérdida chica en una grande.

  NIVEL 2 — DESCARTE DEL INSTRUMENTO. El sistema sigue operando con
      normalidad; este instrumento puntual no califica hoy.

  NIVEL 3 — DEGRADACIÓN DE CONTEXTO. No es un bloqueo binario. El
      sistema pierde calidad de información y responde reduciendo el
      tamaño de las posiciones y subiendo las exigencias, en vez de
      apagarse. Este nivel es nuevo y es el que responde a la pregunta
      de fondo: "no operar si no hay noticias confiables" era demasiado
      grueso, y abajo está explicado exactamente por qué.

EL PROBLEMA CON "SI NO HAY NOTICIAS CONFIABLES, NO SE OPERA"
---------------------------------------------------------------------------
Esa regla mezclaba, en una sola bandera, tres situaciones que no se
parecen en nada:

  (a) Los feeds RSS no respondieron. Es una falla de INFRAESTRUCTURA
      NUESTRA. El mercado está abierto, los precios llegan, la operación
      es viable — lo único que pasó es que un servidor de noticias está
      caído. Tratar eso como "riesgo de mercado" es atribuirle al mercado
      un problema que es de nuestro lado del cable.

  (b) Los feeds respondieron bien, pero no trajeron ningún titular nuevo
      en la ventana de frescura. Eso significa MERCADO SIN NOVEDADES, que
      es el contexto más común que existe y uno de los más operables. En
      el código anterior este caso levantaba la misma bandera que el (a):
      la línea era `news_unavailable = not any_feed_ok or not headlines`,
      y ese `or not headlines` convertía cualquier día tranquilo en un
      veto total. Es el hallazgo más importante de esta revisión, porque
      no producía ningún error visible: el bot simplemente no operaba, y
      el log decía "sin noticias confiables", que suena razonable.

  (c) Hay titulares y alguno describe un riesgo real. ESE sí es un veto
      legítimo, y es el único de los tres que debería frenar por completo.

Esta versión los separa. (c) sigue vetando. (b) no bloquea nada: se opera
normal, registrando que el día venía sin novedades. (a) degrada: se puede
operar, pero con la mitad del tamaño y exigiendo más margen sobre el piso
de rentabilidad, y solo si hay contexto alternativo razonablemente fresco
(las series macro en caché) y ningún evento de calendario a la vista. Si
la falta de noticias se extiende más allá de NEWS_BLACKOUT_MAX_MINUTES,
ahí sí se frena: una cosa es un hueco, otra es estar operando a ciegas
toda la rueda.
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from typing import Optional

logger = logging.getLogger("trade_gate")

NEWS_BLACKOUT_MAX_MINUTES = int(os.getenv("NEWS_BLACKOUT_MAX_MINUTES", "90"))
MACRO_CACHE_MAX_AGE_HOURS = int(os.getenv("MACRO_CACHE_MAX_AGE_HOURS", "36"))
DEGRADED_SIZE_FACTOR = float(os.getenv("DEGRADED_SIZE_FACTOR", "0.5"))
DEGRADED_HURDLE_MULT = float(os.getenv("DEGRADED_HURDLE_MULT", "1.5"))
NO_OPEN_MINUTES_BEFORE_CLOSE = int(os.getenv("NO_OPEN_MINUTES_BEFORE_CLOSE", "20"))
CLOCK_DRIFT_MAX_SECONDS = int(os.getenv("CLOCK_DRIFT_MAX_SECONDS", "120"))

NIVEL_PARADA_TOTAL = 0
NIVEL_NO_ABRIR = 1
NIVEL_DESCARTE_INSTRUMENTO = 2
NIVEL_DEGRADACION = 3


# ---------------------------------------------------------------------------
# El catálogo. Cada entrada: código, nivel, y la explicación en criollo de
# qué situación describe. Esta estructura es la que consume el panel para
# mostrar, en cualquier momento, por qué el bot no está operando.
# ---------------------------------------------------------------------------
CATALOGO = {
    # --- NIVEL 0 — parada total -------------------------------------------
    "KILL_SWITCH_ACTIVO": (NIVEL_PARADA_TOTAL,
        "El kill switch está activo. Según la causa, el supervisor lo levanta solo "
        "(cortes técnicos) o espera confirmación tuya (cortes financieros)."),
    "SIN_SESION_BROKER": (NIVEL_PARADA_TOTAL,
        "No hay sesión válida con PPI: el login falló o el circuit breaker del cliente "
        "está abierto por errores repetidos. Sin sesión, cualquier análisis es teórico."),
    "MERCADO_CERRADO": (NIVEL_PARADA_TOTAL,
        "Fuera del horario de rueda. Se sigue registrando contexto, no se evalúan entradas."),
    "SECRETOS_FALTANTES": (NIVEL_PARADA_TOTAL,
        "Falta alguna credencial crítica o quedó vacía. El arranque se interrumpe antes "
        "de levantar el resto del sistema."),
    "BASE_DE_DATOS_CAIDA": (NIVEL_PARADA_TOTAL,
        "SQLite no responde. Operar sin poder registrar la operación deja posiciones "
        "que después nadie puede reconciliar."),
    "RELOJ_DESFASADO": (NIVEL_PARADA_TOTAL,
        f"El reloj del sistema difiere más de {CLOCK_DRIFT_MAX_SECONDS}s de la hora del "
        "bróker. Con el reloj corrido, el chequeo de 'cotización vieja' deja de servir y "
        "se puede operar contra precios que ya no existen."),

    # --- NIVEL 1 — no se abre, se cuida lo abierto ------------------------
    "MAX_POSICIONES_ABIERTAS": (NIVEL_NO_ABRIR,
        "Se alcanzó el límite de posiciones simultáneas configurado."),
    "LIMITE_PERDIDA_DIARIA": (NIVEL_NO_ABRIR,
        "La pérdida del día llegó al tope. Las posiciones abiertas se siguen vigilando "
        "y se pueden cerrar; no se abre nada nuevo hasta la rueda siguiente."),
    "LIMITE_DRAWDOWN": (NIVEL_NO_ABRIR,
        "La caída acumulada desde el máximo llegó al tope configurado."),
    "CAPITAL_INSUFICIENTE": (NIVEL_NO_ABRIR,
        "El saldo disponible, descontado lo comprometido en propuestas pendientes, no "
        "alcanza para una posición del tamaño mínimo."),
    "CIERRE_DE_RUEDA": (NIVEL_NO_ABRIR,
        f"Faltan menos de {NO_OPEN_MINUTES_BEFORE_CLOSE} minutos para el cierre. No se abre "
        "lo que no se va a poder gestionar hoy."),
    "MOTOR_IA_SIN_RESPALDO": (NIVEL_NO_ABRIR,
        "Cayeron el modelo principal y el de respaldo. Sin evaluación de contexto no se "
        "abren posiciones nuevas; el cuidado de lo abierto no depende de la IA."),
    "FRENO_POR_COSTOS_SCALPING": (NIVEL_NO_ABRIR,
        "Las últimas operaciones de scalping cerraron con margen neto negativo después de "
        "costos reales. Se frena antes de seguir acumulando fricción."),
    "TOPE_DERIVADOS_ALCANZADO": (NIVEL_NO_ABRIR,
        "La exposición total en opciones y futuros llegó al tope de concentración."),

    # --- NIVEL 2 — este instrumento, hoy, no ------------------------------
    "DATOS_TECNICOS_INSUFICIENTES": (NIVEL_DESCARTE_INSTRUMENTO,
        "No hay suficiente historial para calcular los indicadores con sentido."),
    "COTIZACION_VIEJA": (NIVEL_DESCARTE_INSTRUMENTO,
        "El último precio tiene más de 30 segundos. Se descarta antes que operar contra "
        "un precio que probablemente ya cambió."),
    "SCORE_TECNICO_BAJO": (NIVEL_DESCARTE_INSTRUMENTO,
        "La señal técnica no llega al mínimo configurado."),
    "SPREAD_EXCESIVO": (NIVEL_DESCARTE_INSTRUMENTO,
        "La diferencia entre punta compradora y vendedora se lleva la ventaja esperada."),
    "LIQUIDEZ_INSUFICIENTE": (NIVEL_DESCARTE_INSTRUMENTO,
        "El volumen del instrumento o la profundidad del book no alcanzan para entrar y "
        "salir sin mover el precio."),
    "EXPOSICION_POR_TICKER": (NIVEL_DESCARTE_INSTRUMENTO,
        "Ya hay demasiado capital en este mismo papel."),
    "NETO_NO_SUPERA_PISO": (NIVEL_DESCARTE_INSTRUMENTO,
        "El retorno esperado, después de comisiones, derechos de mercado e IVA, no supera "
        "el piso de rendimiento (inflación y tasa). Ganarle al mercado pero perderle a la "
        "inflación no es ganar."),
    "INSTRUMENTO_SIN_MODELO_DE_RIESGO": (NIVEL_DESCARTE_INSTRUMENTO,
        "El motor de derivados no pudo establecer la pérdida máxima de este instrumento. "
        "El motivo puntual (falta el vencimiento, la garantía, el multiplicador) queda "
        "registrado: no es una categoría prohibida, es un dato que falta."),
    "VENCIMIENTO_DEMASIADO_CERCA": (NIVEL_DESCARTE_INSTRUMENTO,
        "Al derivado le queda menos vida que el horizonte de la operación."),
    "GRAFO_RECHAZO": (NIVEL_DESCARTE_INSTRUMENTO,
        "El grafo multi-agente rechazó la oportunidad (solo bloquea si está en modo gating)."),

    # --- NIVEL 3 — degradación de contexto --------------------------------
    "VETO_MACRO_IA": (NIVEL_DEGRADACION,
        "La IA detectó en los titulares un riesgo concreto que desaconseja entrar. Este es "
        "un veto real y bloquea la operación."),
    "SCORE_MACRO_BAJO": (NIVEL_DEGRADACION,
        "El contexto no es negativo, pero tampoco alcanza el mínimo de convicción."),
    "APAGON_DE_NOTICIAS": (NIVEL_DEGRADACION,
        "Los feeds no respondieron. Es una falla de infraestructura nuestra, no una señal "
        "del mercado: se opera con tamaño reducido y piso de rentabilidad más exigente, "
        f"y solo hasta {NEWS_BLACKOUT_MAX_MINUTES} minutos. Pasado ese plazo, se frena."),
    "APAGON_PROLONGADO": (NIVEL_NO_ABRIR,
        "El apagón de noticias superó el máximo tolerado. Ahí sí se deja de abrir: un hueco "
        "es tolerable, una rueda entera a ciegas no."),
    "EVENTO_DE_CALENDARIO": (NIVEL_DEGRADACION,
        "Hay un evento macro programado en la ventana (dato de inflación del INDEC, "
        "licitación del Tesoro, decisión de tasa). No se abre en la ventana previa: el "
        "precio de los minutos anteriores no contiene la información que está por salir."),
}


@dataclass
class GateResult:
    """Veredicto del portón. `allow` responde si se puede seguir adelante;
    `size_factor` y `hurdle_multiplier` son los que aplican la degradación
    cuando se puede operar pero con menos información de la deseable."""
    allow: bool = True
    level: Optional[int] = None
    code: str = ""
    reason: str = ""
    size_factor: float = 1.0
    hurdle_multiplier: float = 1.0
    notes: list = field(default_factory=list)

    def block(self, code: str) -> "GateResult":
        level, reason = CATALOGO.get(code, (NIVEL_DESCARTE_INSTRUMENTO, code))
        self.allow = False
        self.level = level
        self.code = code
        self.reason = reason
        return self

    def degrade(self, code: str, size_factor: float = DEGRADED_SIZE_FACTOR,
                hurdle_multiplier: float = DEGRADED_HURDLE_MULT) -> "GateResult":
        _, reason = CATALOGO.get(code, (NIVEL_DEGRADACION, code))
        self.size_factor = min(self.size_factor, size_factor)
        self.hurdle_multiplier = max(self.hurdle_multiplier, hurdle_multiplier)
        self.notes.append(f"{code}: {reason}")
        return self


FEEDS_DOWN_POLICY = os.getenv("NEWS_FEEDS_DOWN_POLICY", "operar_normal").strip().lower()


def evaluate_news_context(news_result: dict, macro_age_hours: Optional[float] = None,
                          blackout_minutes: float = 0.0,
                          calendar_event_active: bool = False) -> GateResult:
    """
    Traduce el estado de las noticias a un veredicto operativo.

    `news_result` es lo que devuelve g_news_feed.fetch_latest_headlines(). A
    partir de esta versión ese dict distingue dos banderas que antes venían
    fundidas en una: `feeds_down` (los servidores no respondieron) y
    `no_headlines` (respondieron bien, sin novedades). Ver el encabezado de
    este archivo para el detalle de por qué esa distinción importa tanto.
    """
    result = GateResult()

    if calendar_event_active:
        return result.block("EVENTO_DE_CALENDARIO")

    feeds_down = news_result.get("feeds_down", news_result.get("news_unavailable", False))
    no_headlines = news_result.get("no_headlines", False)

    if not feeds_down:
        if no_headlines:
            # Mercado tranquilo. No es un problema: es el contexto habitual.
            result.notes.append(
                "Sin titulares nuevos en la ventana de frescura, con los feeds respondiendo "
                "normalmente: día sin novedades, se opera con criterio normal."
            )
        return result

    # A partir de acá, los feeds fallaron de verdad.
    if blackout_minutes > NEWS_BLACKOUT_MAX_MINUTES:
        return result.block("APAGON_PROLONGADO")

    if macro_age_hours is None or macro_age_hours > MACRO_CACHE_MAX_AGE_HOURS:
        # Sin noticias Y sin contexto macro fresco no queda ninguna fuente de
        # información sobre el mundo: eso sí es operar a ciegas.
        result.notes.append(
            f"Además del apagón de noticias, las series macro en caché tienen más de "
            f"{MACRO_CACHE_MAX_AGE_HOURS} horas: no queda ninguna fuente de contexto viva."
        )
        return result.block("APAGON_PROLONGADO")

    # ======================================================================
    # POLÍTICA DE FEEDS CAÍDOS (nueva en v16.2, por decisión del titular)
    # ======================================================================
    # Llegado acá se sabe tres cosas a la vez: los feeds de noticias no
    # respondieron, el apagón lleva menos de NEWS_BLACKOUT_MAX_MINUTES, y las
    # series macro están frescas.
    #
    # Qué significaba "degradar" hasta esta versión: el sistema seguía
    # operando pero con la posición a la mitad (DEGRADED_SIZE_FACTOR=0,5) y el
    # piso de rentabilidad un 50% más exigente (DEGRADED_HURDLE_MULT=1,5). En
    # la práctica eso descarta casi todo: una oportunidad tiene que rendir un
    # 50% más de lo normal para pasar, y si pasa se opera con la mitad del
    # tamaño. Es una parada encubierta.
    #
    # El argumento del titular es correcto y se adopta: que NUESTROS
    # servidores de RSS no contesten es una falla de infraestructura propia,
    # no un evento de mercado. El mercado está abierto, los precios llegan
    # por PPI, el contexto macro está fresco y el análisis técnico funciona.
    # Castigar el tamaño de la posición por un problema del lado de acá es
    # atribuirle al mercado algo que no pasó en el mercado.
    #
    # DEFAULT: operar normal. Las protecciones que SÍ quedan en pie son las
    # que miden impacto real, y son tres:
    #   · el corte a los 90 minutos de apagón sigue vigente (arriba);
    #   · si las series macro también están viejas, se bloquea (arriba);
    #   · si hay un evento de calendario programado, se bloquea (arriba).
    # Lo único que se deja de hacer es achicar por precaución cuando no hay
    # ningún indicio de que haya pasado algo.
    #
    # Se deja configurable por si el criterio cambia. Poner
    # NEWS_FEEDS_DOWN_POLICY=degradar restaura el comportamiento anterior.
    if FEEDS_DOWN_POLICY == "degradar":
        return result.degrade("APAGON_DE_NOTICIAS")

    result.notes.append(
        "Los feeds de noticias no respondieron, pero el apagón lleva menos de "
        f"{NEWS_BLACKOUT_MAX_MINUTES:.0f} minutos y las series macro están frescas "
        f"({macro_age_hours:.0f} h). Es una falla de infraestructura propia, no un "
        "evento de mercado: se opera con criterio normal. El corte por apagón "
        "prolongado sigue activo."
    )
    return result


def check_session_health(*, kill_switch_active: bool, broker_session_ok: bool,
                         db_ok: bool, market_open: bool,
                         clock_drift_seconds: float = 0.0) -> GateResult:
    """Las condiciones de nivel 0. Se corren una vez por ciclo, antes de mirar
    ningún instrumento: si alguna falla, no tiene sentido gastar el resto."""
    result = GateResult()
    if kill_switch_active:
        return result.block("KILL_SWITCH_ACTIVO")
    if not db_ok:
        return result.block("BASE_DE_DATOS_CAIDA")
    # Fuera de rueda no hace falta una sesión con el bróker para concluir
    # que no se opera. Priorizar MERCADO_CERRADO evita clasificar un fin de
    # semana como una falla de autenticación y evita reintentos inútiles.
    if not market_open:
        return result.block("MERCADO_CERRADO")
    if not broker_session_ok:
        return result.block("SIN_SESION_BROKER")
    if abs(clock_drift_seconds) > CLOCK_DRIFT_MAX_SECONDS:
        return result.block("RELOJ_DESFASADO")
    return result


def check_open_conditions(*, open_positions: int, max_open_positions: int,
                          daily_loss_pct: Optional[float] = None,
                          max_daily_loss_pct: Optional[float] = None,
                          drawdown_pct: Optional[float] = None,
                          max_drawdown_pct: Optional[float] = None,
                          available_capital_ars: float = 0.0,
                          min_capital_ars: float = 0.0,
                          minutes_to_close_value: Optional[float] = None,
                          no_open_minutes_before_close: float = 20.0,
                          ai_engine_available: bool = True,
                          scalping_cost_brake: bool = False,
                          derivatives_cap_reached: bool = False) -> GateResult:
    """NUEVO EN v16.2 — las condiciones de NIVEL 1: no se abre nada nuevo,
    pero lo que ya está abierto se sigue vigilando y se puede cerrar.

    Esa asimetría es lo más importante de este nivel y no es un detalle de
    implementación: prohibir cerrar es la única forma segura de convertir una
    pérdida chica en una grande. Cualquier condición de acá frena la apertura;
    ninguna frena el cuidado de lo abierto.

    La función existía como catálogo —los nueve códigos de nivel 1 estaban
    enumerados y documentados— pero no había ninguna función que los
    evaluara: los controles vivían desparramados por j_main con sus propios
    ifs, y tres de ellos directamente no existían en runtime. Con esto, el
    panel muestra el código exacto del catálogo y el documento describe algo
    que el sistema hace.
    """
    result = GateResult()

    if daily_loss_pct is not None and max_daily_loss_pct is not None \
            and daily_loss_pct <= -abs(max_daily_loss_pct):
        return result.block("LIMITE_PERDIDA_DIARIA")

    if drawdown_pct is not None and max_drawdown_pct is not None \
            and drawdown_pct >= abs(max_drawdown_pct):
        return result.block("LIMITE_DRAWDOWN")

    if open_positions >= max_open_positions:
        return result.block("MAX_POSICIONES_ABIERTAS")

    if min_capital_ars and available_capital_ars < min_capital_ars:
        return result.block("CAPITAL_INSUFICIENTE")

    if minutes_to_close_value is not None and \
            minutes_to_close_value <= no_open_minutes_before_close:
        return result.block("CIERRE_DE_RUEDA")

    if not ai_engine_available:
        return result.block("MOTOR_IA_SIN_RESPALDO")

    if scalping_cost_brake:
        return result.block("FRENO_POR_COSTOS_SCALPING")

    if derivatives_cap_reached:
        return result.block("TOPE_DERIVADOS_ALCANZADO")

    return result


def clock_drift_seconds(reference_epoch: Optional[float] = None) -> float:
    """Desfasaje del reloj del servidor contra una referencia externa.

    Importa más de lo que parece: el control de "cotización vieja" compara el
    timestamp del tick contra la hora local. Con el reloj corrido, ese control
    deja de servir y el bot puede operar contra precios de hace minutos
    creyendo que son de hace segundos.

    Si no se le pasa una referencia, devuelve 0.0 —no se puede medir— en vez
    de inventar un número. El llamador decide qué hacer con eso.
    """
    if reference_epoch is None:
        return 0.0
    import time as _time
    return _time.time() - reference_epoch


def minutes_to_close(now: Optional[datetime] = None,
                     close_time: dtime = dtime(17, 0)) -> float:
    """Minutos que faltan para el cierre de la rueda de contado en BYMA."""
    now = now or datetime.now()
    close_dt = now.replace(hour=close_time.hour, minute=close_time.minute,
                           second=0, microsecond=0)
    return (close_dt - now).total_seconds() / 60.0


def describe_catalog() -> list:
    """Devuelve el catálogo completo, ordenado por nivel, para que el panel lo
    muestre tal cual y el documento maestro lo genere sin duplicar el texto."""
    filas = []
    for code, (level, reason) in CATALOGO.items():
        filas.append({"codigo": code, "nivel": level, "explicacion": reason})
    return sorted(filas, key=lambda f: (f["nivel"], f["codigo"]))
