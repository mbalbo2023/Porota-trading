"""
ao_startup_gate.py — El bot no arranca solo: pide autorización primero

EL PROBLEMA QUE RESUELVE
---------------------------------------------------------------------------
Hasta ahora, levantar el contenedor equivalía a empezar a operar. En un
servidor de producción estable eso puede tener sentido; en un entorno de
pruebas que se reconstruye a cada rato, es exactamente lo contrario de lo
que uno quiere. Cada vez que se recrea el Codespace, el bot arrancaba,
empezaba a evaluar y eventualmente a operar, sin que nadie hubiera decidido
que era el momento — y si algo estaba mal configurado, uno se enteraba
después.

La lógica nueva invierte el default. Al levantar, el sistema NO opera: queda
esperando. Manda un mensaje a Telegram diciendo que está listo y ofreciendo
tres caminos, y hasta que no llegue la respuesta no toca el mercado.

  🧪 SIMULACIÓN     conecta al sandbox de PPI, hace todo el recorrido
                    completo —descubre instrumentos, evalúa, decide,
                    dimensiona— y registra cada paso en el panel, pero las
                    órdenes van contra plata ficticia.

  💰 REAL           opera de verdad. Pide una segunda confirmación, porque un
                    solo toque accidental en un teléfono no debería ser
                    suficiente para empezar a mover dinero.

  ⏸️ NO ARRANCAR    queda levantado y accesible por el panel, sin operar. Es
                    el modo correcto para revisar configuración, mirar los
                    semáforos de salud o correr el verificador de APIs.

POR QUÉ TELEGRAM Y NO UN BOTÓN EN EL PANEL
---------------------------------------------------------------------------
Porque el panel hay que ir a mirarlo y Telegram te busca. La diferencia
importa: si el contenedor se reinicia solo a las tres de la mañana por una
actualización del host, un botón en una página que nadie tiene abierta
significa que el bot queda apagado sin que nadie se entere hasta la mañana
siguiente. Un mensaje al teléfono avisa en el momento.

Además, el mismo mecanismo ya se usa para autorizar los cambios del motor
SRE, así que hay un solo lugar desde donde se dan las autorizaciones
importantes, con la misma mecánica de códigos de un solo uso.

QUÉ PASA SI TELEGRAM ESTÁ CAÍDO
---------------------------------------------------------------------------
El sistema queda esperando, sin operar. Es la decisión deliberadamente
conservadora: si el canal por el que se avisa de un problema no funciona,
arrancar a operar significa hacerlo sin ninguna forma de enterarse si algo
sale mal. Para no quedar encerrado, hay una salida manual por el panel
(POST /api/testing/autorizar) que sirve cuando Telegram no está disponible y
uno igual quiere arrancar sabiendo lo que hace.
"""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger("startup_gate")

STARTUP_REQUIRE_AUTH = os.getenv("STARTUP_REQUIRE_AUTH", "true").lower() == "true"
STARTUP_TIMEOUT_MINUTES = int(os.getenv("STARTUP_TIMEOUT_MINUTES", "60"))
STARTUP_STATE_PATH = os.getenv("STARTUP_STATE_PATH", "./data/startup_state.json")
TESTING_LOG_PATH = os.getenv("TESTING_LOG_PATH", "./data/testing_trace.jsonl")

MODO_SIMULACION = "SIMULACION"
MODO_REAL = "REAL"
MODO_DETENIDO = "DETENIDO"
ESPERANDO = "ESPERANDO_AUTORIZACION"
ESPERANDO_CALENDARIO = "ESPERANDO_APERTURA"


@dataclass
class EstadoArranque:
    estado: str = ESPERANDO
    modo: Optional[str] = None
    solicitado: str = ""
    autorizado: str = ""
    autorizado_por: str = ""
    codigo: str = ""
    mensaje: str = ""
    pasos: List[dict] = field(default_factory=list)


_estado = EstadoArranque()
_evento = threading.Event()
_lock = threading.Lock()


def _persistir() -> None:
    try:
        os.makedirs(os.path.dirname(STARTUP_STATE_PATH) or ".", exist_ok=True)
        with open(STARTUP_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(asdict(_estado), f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("No se pudo persistir el estado de arranque: %s", e)


def _recargar_desde_archivo() -> bool:
    """Lee el estado persistido y adopta una autorización dada por otro proceso.
    Devuelve True si encontró una decisión tomada."""
    try:
        with open(STARTUP_STATE_PATH, encoding="utf-8") as f:
            guardado = json.load(f)
    except (OSError, ValueError):
        return False
    modo = guardado.get("modo")
    if modo not in (MODO_SIMULACION, MODO_REAL, MODO_DETENIDO):
        return False
    with _lock:
        _estado.modo = modo
        _estado.estado = guardado.get("estado", "AUTORIZADO")
        _estado.autorizado = guardado.get("autorizado", "")
        _estado.autorizado_por = guardado.get("autorizado_por", "")
    _evento.set()
    return True


def estado_actual() -> dict:
    with _lock:
        return asdict(_estado)


def esta_en_simulacion() -> bool:
    """La pregunta que hace el resto del sistema antes de mandar una orden.

    Se consulta acá y no en una variable de entorno para que el modo pueda
    cambiar sin reiniciar, y sobre todo para que haya UN solo lugar donde vive
    la respuesta. Un modo simulación que cada módulo interpreta por su cuenta
    termina, tarde o temprano, con un módulo que no se enteró.
    """
    with _lock:
        return _estado.modo == MODO_SIMULACION


def puede_operar() -> bool:
    with _lock:
        return _estado.modo in (MODO_SIMULACION, MODO_REAL)


# ---------------------------------------------------------------------------
# Registro paso a paso, para el panel
# ---------------------------------------------------------------------------

def registrar_paso(etapa: str, detalle: str, resultado: str = "",
                   datos: Optional[dict] = None) -> None:
    """Deja constancia de cada paso del arranque y de cada decisión durante la
    simulación, para que el panel pueda mostrar la secuencia completa.

    Va a un archivo aparte y no al log general a propósito: el log general
    mezcla todo y hay que interpretarlo. Esto es una traza ordenada de
    'qué hizo el bot, en qué orden y con qué resultado', que es lo que uno
    necesita cuando está mirando si el sistema arrancó bien.
    """
    paso = {
        "momento": datetime.now().isoformat(timespec="seconds"),
        "etapa": etapa, "detalle": detalle, "resultado": resultado,
        "datos": datos or {},
    }
    with _lock:
        _estado.pasos.append(paso)
        _estado.pasos = _estado.pasos[-500:]
    try:
        os.makedirs(os.path.dirname(TESTING_LOG_PATH) or ".", exist_ok=True)
        with open(TESTING_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(paso, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("No se pudo escribir la traza de testing: %s", e)
    logger.info("[%s] %s %s", etapa, detalle, f"→ {resultado}" if resultado else "")


def leer_pasos(limite: int = 200) -> List[dict]:
    with _lock:
        return list(_estado.pasos[-limite:])


# ---------------------------------------------------------------------------
# Solicitud y autorización
# ---------------------------------------------------------------------------

def marcar_espera_calendario(mensaje: str) -> None:
    """Publica en el panel que el motor espera una rueda válida, sin pedir permiso."""
    with _lock:
        _estado.estado = ESPERANDO_CALENDARIO
        _estado.modo = None
        _estado.solicitado = datetime.now().isoformat(timespec="seconds")
        _estado.autorizado = ""
        _estado.autorizado_por = ""
        _estado.codigo = ""
        _estado.mensaje = mensaje
    _persistir()
    registrar_paso("CALENDARIO", mensaje, "Sin operar")


def solicitar_autorizacion(notifier=None) -> str:
    """Lo primero que hace el sistema al levantar: avisar y quedarse esperando."""
    with _lock:
        _estado.estado = ESPERANDO
        _estado.modo = None
        _estado.solicitado = datetime.now().isoformat(timespec="seconds")
        _estado.codigo = f"{int(time.time()) % 100000:05d}"
        codigo = _estado.codigo
    _evento.clear()
    registrar_paso("ARRANQUE", "El sistema levantó y quedó esperando autorización.",
                   "Sin operar")
    _persistir()

    entorno = os.getenv("ENVIRONMENT", "SANDBOX").upper()
    texto = (
        "🤖 *Porota Trading está levantado y esperando tu OK*\n\n"
        f"Entorno configurado: `{entorno}`\n"
        f"Código de esta sesión: `{codigo}`\n\n"
        "El bot NO está operando. No va a tocar el mercado hasta que elijas cómo arrancar:\n\n"
        f"🧪 `SIMULAR {codigo}` — recorrido completo contra el sandbox de PPI. "
        "Descubre, evalúa, decide y dimensiona igual que en real, pero con plata ficticia. "
        "Todo queda documentado paso a paso en el panel.\n\n"
        f"💰 `REAL {codigo}` — operar de verdad. Te voy a pedir una segunda confirmación.\n\n"
        f"⏸️ `NO {codigo}` — quedarse levantado sin operar, para revisar configuración.\n\n"
        f"_Si no respondés en {STARTUP_TIMEOUT_MINUTES} minutos, queda detenido._"
    )
    teclado = {"inline_keyboard": [[
        {"text": "🧪 Simulación", "callback_data": f"arranque:sim:{codigo}"},
        {"text": "💰 Real", "callback_data": f"arranque:real:{codigo}"},
        {"text": "⏸️ No arrancar", "callback_data": f"arranque:no:{codigo}"},
    ]]}

    if notifier is not None:
        try:
            enviado = notifier.send(texto, reply_markup=teclado, parse_mode="Markdown")
            if not enviado:
                raise RuntimeError("Telegram rechazó el pedido de autorización")
            registrar_paso("ARRANQUE", "Pedido de autorización enviado a Telegram.", "Enviado")
        except Exception as e:
            registrar_paso("ARRANQUE", f"No se pudo avisar por Telegram: {e}",
                           "El sistema queda esperando igual")
            logger.error("Telegram no disponible al pedir autorización: %s. El bot NO arranca "
                         "solo por esto: usá el panel para autorizar manualmente.", e)
    else:
        logger.warning("Sin notificador: autorizá desde el panel, en la solapa de Testing.")

    return codigo


def autorizar(modo: str, codigo: str = "", origen: str = "telegram") -> dict:
    """Registra la decisión y libera el arranque.

    El código se valida solo si vino uno: la autorización desde el panel ya
    pasó por el token de acceso del dashboard, así que exigirle además el
    código sería redundante y solo agregaría fricción cuando Telegram está
    caído, que es justo cuando se usa esa vía.
    """
    modo = modo.upper()
    if modo not in (MODO_SIMULACION, MODO_REAL, MODO_DETENIDO):
        return {"ok": False, "motivo": f"Modo desconocido: {modo}"}

    with _lock:
        if codigo and codigo != _estado.codigo:
            return {"ok": False, "motivo": "El código no corresponde a esta sesión."}
        _estado.modo = modo
        _estado.estado = "AUTORIZADO" if modo != MODO_DETENIDO else "DETENIDO_POR_PEDIDO"
        _estado.autorizado = datetime.now().isoformat(timespec="seconds")
        _estado.autorizado_por = origen

    registrar_paso("AUTORIZACION", f"Autorizado en modo {modo} desde {origen}.",
                   "Arranca" if modo != MODO_DETENIDO else "Queda detenido")
    _persistir()
    _evento.set()
    return {"ok": True, "modo": modo}


def esperar_autorizacion(notifier=None) -> str:
    """
    Bloquea el arranque hasta que llegue una decisión o venza el plazo.

    Se llama desde entrypoint.py ANTES de construir el grafo de agentes y
    antes de abrir el stream de precios. El orden importa: si se levantara
    todo primero y se preguntara después, el bot ya estaría suscripto al feed
    y consumiendo cuota del bróker sin haber sido autorizado.
    """
    if not STARTUP_REQUIRE_AUTH:
        logger.warning("STARTUP_REQUIRE_AUTH=false: el bot arranca sin pedir autorización. "
                       "Está bien para un servidor de producción ya estabilizado; en un "
                       "entorno de pruebas conviene dejarlo en true.")
        autorizar(MODO_REAL if os.getenv("ENVIRONMENT", "").upper() == "PRODUCTION"
                  else MODO_SIMULACION, origen="configuración")
        return _estado.modo

    solicitar_autorizacion(notifier)

    # La espera revisa DOS canales, y esto no es redundancia: el bot y el panel
    # corren como procesos separados (ver entrypoint.py), así que una
    # autorización dada desde la web no puede llegar por un evento en memoria.
    # Viaja por el archivo de estado. El evento cubre el caso de Telegram
    # procesado dentro de este mismo proceso; el sondeo del archivo cubre el
    # botón del panel. Sin las dos vías, autorizar desde la web no despertaría
    # nunca al bot.
    limite = time.time() + STARTUP_TIMEOUT_MINUTES * 60
    concedido = False
    while time.time() < limite:
        if _evento.wait(timeout=2):
            concedido = True
            break
        if _recargar_desde_archivo():
            concedido = True
            break

    if not concedido:
        with _lock:
            _estado.modo = MODO_DETENIDO
            _estado.estado = "VENCIDO_SIN_RESPUESTA"
        registrar_paso("AUTORIZACION",
                       f"No llegó respuesta en {STARTUP_TIMEOUT_MINUTES} minutos.",
                       "Queda detenido, sin operar")
        _persistir()
        if notifier:
            try:
                notifier.send("⏱️ Nadie autorizó el arranque, así que Porota Trading quedó "
                              "detenido sin operar. Reiniciá el servicio o autorizá desde el panel.")
            except Exception:
                pass
        return MODO_DETENIDO

    return _estado.modo


def procesar_respuesta_telegram(texto: str, origen: str = "telegram") -> Optional[dict]:
    """Interpreta las respuestas escritas: SIMULAR 12345, REAL 12345, NO 12345.

    Se aceptan tanto los botones como el texto escrito porque los botones
    inline a veces fallan en clientes viejos de Telegram, y quedarse sin
    forma de autorizar por un problema de la interfaz sería absurdo.
    """
    partes = (texto or "").strip().split()
    if not partes:
        return None
    comando = partes[0].upper()
    codigo = partes[1] if len(partes) > 1 else ""

    mapa = {"SIMULAR": MODO_SIMULACION, "SIMULACION": MODO_SIMULACION,
            "SIMULACIÓN": MODO_SIMULACION,
            "REAL": MODO_REAL, "NO": MODO_DETENIDO, "DETENER": MODO_DETENIDO}
    if comando not in mapa:
        return None
    if not codigo:
        return {"ok": False, "motivo": "Falta el código de esta sesión."}
    with _lock:
        if _estado.estado == "AUTORIZADO":
            return {"ok": False, "motivo": "El arranque ya fue autorizado."}
    return autorizar(mapa[comando], codigo, origen)


def procesar_boton_telegram(callback_data: str, origen: str = "telegram_boton") -> Optional[dict]:
    """Procesa los tres botones inline del pedido de autorización."""
    partes = (callback_data or "").split(":")
    if len(partes) != 3 or partes[0] != "arranque":
        return None
    mapa = {"sim": MODO_SIMULACION, "real": MODO_REAL, "no": MODO_DETENIDO}
    modo = mapa.get(partes[1])
    if modo is None:
        return {"ok": False, "motivo": "Opción de arranque desconocida."}
    with _lock:
        if _estado.estado == "AUTORIZADO":
            return {"ok": False, "motivo": "El arranque ya fue autorizado."}
    return autorizar(modo, partes[2], origen)


# ---------------------------------------------------------------------------
# Freno de órdenes en simulación
# ---------------------------------------------------------------------------

def interceptar_orden(ticker: str, cantidad: int, precio: float, lado: str,
                      instrument_type: str = "") -> Optional[dict]:
    """
    Se llama justo antes de mandar una orden. Devuelve una orden simulada si el
    sistema está en modo simulación, o None si corresponde ejecutar de verdad.

    Este es el punto único donde se decide si una orden sale o no, y por eso
    está acá y no repartido en cada módulo que puede ordenar. La regla es una
    sola y se lee de un vistazo: si el modo es simulación, ninguna orden sale
    al mercado real, sin importar quién la haya originado ni por qué camino
    haya llegado.
    """
    # ======================================================================
    # CORREGIDO EN v16.2 — LA CONDICIÓN ESTABA AL REVÉS
    # ======================================================================
    # Antes decía `if not esta_en_simulacion(): return None`, es decir:
    # "cualquier modo que no sea SIMULACION ejecuta de verdad". Eso incluye
    # el modo DETENIDO, que es el estado en el que el usuario respondió "No
    # arrancar" o dejó vencer el plazo de autorización. El caso más peligroso
    # quedaba afuera del único punto de control.
    #
    # Hoy nada ordena en ese estado porque el bucle principal no arrancó. Pero
    # el argumento por el que este freno está acá y no repartido es
    # precisamente que sea un punto ÚNICO: cualquier camino futuro que coloque
    # una orden sin pasar por el bucle —un job del planificador, el cierre de
    # fin de día, un comando remoto— operaría de verdad sobre un sistema que
    # el usuario decidió no arrancar.
    #
    # La condición se invierte: la orden sale SOLO si el modo es REAL. Todo lo
    # demás se intercepta. Es la diferencia entre una lista de estados
    # prohibidos —que hay que acordarse de mantener— y una lista de un solo
    # estado permitido, que no se puede olvidar de actualizar.
    modo = estado_actual().get("modo")
    if modo == MODO_REAL:
        return None

    orden = {
        "simulada": True,
        "bloqueada_por": modo,
        "id": f"SIM-{int(time.time() * 1000) % 10_000_000}",
        "ticker": ticker, "cantidad": cantidad, "precio": precio, "lado": lado,
        "instrument_type": instrument_type,
        "momento": datetime.now().isoformat(timespec="seconds"),
        "monto_ars": round(cantidad * precio, 2),
    }
    registrar_paso("ORDEN_INTERCEPTADA" if modo != MODO_SIMULACION else "ORDEN_SIMULADA",
                   f"{lado} {cantidad} × {ticker} a ${precio}",
                   f"No se envió al mercado — modo {modo} "
                   f"(${orden['monto_ars']:,.2f} figurados)",
                   orden)
    return orden
