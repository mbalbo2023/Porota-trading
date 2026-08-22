"""
ar_telegram_commands.py — Control remoto por Telegram

QUÉ RESUELVE
---------------------------------------------------------------------------
Hasta ahora Telegram servía para dos cosas: autorizar el arranque y confirmar
órdenes. Faltaba lo más importante en un mal momento — poder frenar el
sistema desde el teléfono, sin abrir la computadora, sin buscar la URL del
panel, sin acordarse del token.

Una parada de emergencia es exactamente el escenario donde uno no tiene la
computadora a mano: estás en la calle, ves una noticia, y querés cortar. Si
para cortar hay que llegar a un dashboard, la funcionalidad no existe cuando
más se necesita.

LAS TRES FORMAS DE FRENAR, Y POR QUÉ SON TRES
---------------------------------------------------------------------------
Frenar un bot que tiene posiciones abiertas no es una sola cosa. Hay tres
maneras, con consecuencias muy distintas, y elegir mal cuesta plata:

  1. ORDENADA — no abre nada nuevo, cuida lo abierto hasta que cierre solo
     por stop-loss o take-profit. Es la más segura y la más lenta: las
     posiciones pueden tardar días en cerrarse. Sirve cuando el problema es
     "no quiero más exposición", no "sacame de acá ya".

  2. LIQUIDAR — vende todo a mercado, ahora. Sale rápido, y el costo es el
     precio: vender a mercado significa aceptar la punta compradora que haya,
     que en instrumentos poco líquidos puede estar bastante peor que el
     último operado. Sirve cuando el riesgo de quedarse adentro supera el
     costo de salir mal.

  3. CORTE SECO — apaga el bot y deja las posiciones como están, sin nadie
     vigilándolas. Es la más peligrosa y existe igual, porque hay
     situaciones donde apagar es lo correcto: si el bot está tomando
     decisiones erráticas, dejarlo operar para "salir ordenadamente" puede
     ser peor que congelarlo. El sistema te explica el riesgo con números
     concretos —cuántas posiciones, cuánta plata, qué stop-loss deja de
     vigilarse— y pide una confirmación aparte.

Ninguna de las tres es "la correcta" en abstracto. Por eso el bot no elige:
te muestra el estado real y las tres opciones con su consecuencia, y elegís
vos con la información a la vista.
"""

import logging
import os
import threading
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger("telegram_commands")

COMMAND_POLL_SECONDS = int(os.getenv("TELEGRAM_COMMAND_POLL_SECONDS", "5"))
CONFIRM_TTL_SECONDS = int(os.getenv("TELEGRAM_CONFIRM_TTL_SECONDS", "300"))

MODO_ORDENADA = "ORDENADA"
MODO_LIQUIDAR = "LIQUIDAR"
MODO_CORTE_SECO = "CORTE_SECO"

_confirmaciones: Dict[str, tuple] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Foto del estado antes de decidir
# ---------------------------------------------------------------------------

@dataclass
class FotoOperativa:
    """Lo que hay abierto en este momento. Es lo que se le muestra a la
    persona antes de que elija cómo frenar: nadie puede decidir bien sin
    saber qué está en juego."""
    posiciones: List[dict] = field(default_factory=list)
    ordenes_pendientes: List[dict] = field(default_factory=list)
    propuestas_sin_confirmar: List[dict] = field(default_factory=list)
    capital_expuesto_ars: float = 0.0
    resultado_no_realizado_ars: float = 0.0
    mercado_abierto: bool = False
    error: str = ""


def tomar_foto(ppi_client=None) -> FotoOperativa:
    """Reúne el estado real. Cada bloque va en su propio try: si falla la
    consulta de órdenes pendientes, igual queremos poder informar las
    posiciones abiertas. Una foto parcial con el hueco marcado es mucho más
    útil que un error genérico."""
    foto = FotoOperativa()

    try:
        import k_position_manager as pm
        foto.posiciones = pm.get_open_positions() or []
    except Exception as e:
        foto.error += f"No se pudieron leer las posiciones: {e}. "

    try:
        import l_order_confirmation as oc
        if hasattr(oc, "get_pending_proposals"):
            foto.propuestas_sin_confirmar = oc.get_pending_proposals() or []
    except Exception as e:
        foto.error += f"No se pudieron leer las propuestas: {e}. "

    if ppi_client is not None:
        try:
            from datetime import date
            hoy = date.today().isoformat()
            ordenes = ppi_client.get_orders(hoy, hoy) or []
            abiertas = {"PENDING", "PENDIENTE", "PARTIALLYFILLED", "SENT", "INPROGRESS"}
            foto.ordenes_pendientes = [
                o for o in ordenes
                if str(o.get("status", o.get("estado", ""))).upper().replace(" ", "") in abiertas
            ]
        except Exception as e:
            foto.error += f"No se pudieron leer las órdenes en el bróker: {e}. "

    for p in foto.posiciones:
        entrada = p.get("entry_price") or 0
        cantidad = p.get("quantity") or 0
        foto.capital_expuesto_ars += entrada * cantidad
        precio_actual = _precio_actual(ppi_client, p)
        if precio_actual:
            foto.resultado_no_realizado_ars += (precio_actual - entrada) * cantidad

    try:
        import j_main
        foto.mercado_abierto = j_main.is_market_open()
    except Exception:
        pass

    return foto


def _precio_actual(ppi_client, posicion: dict) -> Optional[float]:
    if ppi_client is None:
        return None
    try:
        datos = ppi_client.get_market_data(
            posicion.get("ticker"), posicion.get("instrument_type", "ACCIONES"),
            posicion.get("settlement", "A-24HS"))
        return (datos or {}).get("price")
    except Exception:
        return None


def _formatear_foto(foto: FotoOperativa) -> str:
    if foto.error:
        cabecera = f"⚠️ Estado parcial — {foto.error}\n\n"
    else:
        cabecera = ""

    if not foto.posiciones and not foto.ordenes_pendientes and not foto.propuestas_sin_confirmar:
        return cabecera + "✅ No hay nada abierto: ni posiciones, ni órdenes, ni propuestas pendientes."

    lineas = [cabecera + "📊 *Situación actual*\n"]

    if foto.posiciones:
        lineas.append(f"*{len(foto.posiciones)} posición(es) abierta(s):*")
        for p in foto.posiciones:
            monto = (p.get("entry_price") or 0) * (p.get("quantity") or 0)
            lineas.append(
                f"  • {p.get('ticker')} — {p.get('quantity')} un. a ${p.get('entry_price')} "
                f"(${monto:,.0f})\n"
                f"    stop ${p.get('stop_loss_price')} · objetivo ${p.get('take_profit_price')}")
        lineas.append(f"\n💰 Capital expuesto: *${foto.capital_expuesto_ars:,.0f}*")
        signo = "+" if foto.resultado_no_realizado_ars >= 0 else ""
        lineas.append(f"📈 Resultado no realizado: *{signo}${foto.resultado_no_realizado_ars:,.0f}*")

    if foto.ordenes_pendientes:
        lineas.append(f"\n*{len(foto.ordenes_pendientes)} orden(es) en el mercado sin ejecutar:*")
        for o in foto.ordenes_pendientes[:10]:
            lineas.append(f"  • {o.get('ticker', o.get('simbolo', '?'))} — "
                          f"{o.get('quantity', o.get('cantidad', '?'))} un.")

    if foto.propuestas_sin_confirmar:
        lineas.append(f"\n*{len(foto.propuestas_sin_confirmar)} propuesta(s) esperando tu confirmación.*")

    lineas.append(f"\n🕐 Mercado: {'abierto' if foto.mercado_abierto else 'cerrado'}")
    return "\n".join(lineas)


# ---------------------------------------------------------------------------
# /parada_emergencia
# ---------------------------------------------------------------------------

def comando_parada_emergencia(notifier, ppi_client=None) -> dict:
    """
    Primer paso: NO frena nada todavía. Muestra qué hay en juego y las tres
    opciones con su consecuencia.

    Que el comando no ejecute de una es deliberado. Una parada de emergencia
    se manda con adrenalina, y el momento de menos adrenalina posible para
    decidir entre "liquidar a mercado" y "esperar los stops" es antes de saber
    cuánta plata hay adentro. Mostrar primero cuesta diez segundos y evita la
    decisión equivocada.
    """
    foto = tomar_foto(ppi_client)
    codigo = _nuevo_codigo()
    with _lock:
        _confirmaciones[codigo] = (None, time.time(), foto)

    cuerpo = _formatear_foto(foto)

    if not foto.posiciones and not foto.ordenes_pendientes:
        texto = (f"🛑 *Parada de emergencia*\n\n{cuerpo}\n\n"
                 "No hay nada que liquidar. Si confirmás, el bot deja de operar y queda "
                 f"detenido.\n\nRespondé `DETENER {codigo}` para frenar, o ignorá este mensaje.")
        teclado = {"inline_keyboard": [[
            {"text": "🛑 Detener el bot", "callback_data": f"parada:ordenada:{codigo}"},
            {"text": "✖️ Cancelar", "callback_data": f"parada:nada:{codigo}"},
        ]]}
    else:
        texto = (
            f"🛑 *Parada de emergencia*\n\n{cuerpo}\n\n"
            "*¿Cómo querés frenar?*\n\n"
            f"1️⃣ `ORDENADA {codigo}`\n"
            "No se abre nada más. Las posiciones abiertas se siguen vigilando y cierran "
            "solas por stop-loss o take-profit. Es la más segura y la más lenta: pueden "
            "tardar días.\n\n"
            f"2️⃣ `LIQUIDAR {codigo}`\n"
            "Vende todo a mercado, ahora. Salís rápido y el costo es el precio: a mercado "
            "se acepta la punta compradora que haya, que en papeles poco líquidos puede "
            "estar bastante por debajo del último operado.\n\n"
            f"3️⃣ `CORTE {codigo}`\n"
            "⚠️ Apaga el bot y deja todo como está, *sin nadie vigilando los stop-loss*. "
            "Te voy a pedir una confirmación aparte con el riesgo detallado.\n\n"
            f"_El código vence en {CONFIRM_TTL_SECONDS // 60} minutos._")
        teclado = {"inline_keyboard": [
            [{"text": "1️⃣ Ordenada", "callback_data": f"parada:ordenada:{codigo}"},
             {"text": "2️⃣ Liquidar todo", "callback_data": f"parada:liquidar:{codigo}"}],
            [{"text": "3️⃣ Corte seco", "callback_data": f"parada:corte:{codigo}"},
             {"text": "✖️ Cancelar", "callback_data": f"parada:nada:{codigo}"}],
        ]}

    _enviar(notifier, texto, teclado)
    return {"codigo": codigo, "foto": foto}


def _riesgo_del_corte_seco(foto: FotoOperativa) -> str:
    """Explica, con los números concretos de este momento, qué se está
    aceptando al apagar en seco. Genérico no sirve: 'podés perder plata' no
    ayuda a decidir; '3 posiciones por $4.200.000 quedan sin stop' sí."""
    if not foto.posiciones:
        return "No hay posiciones abiertas, así que el corte seco no deja nada sin vigilancia."

    peor = 0.0
    for p in foto.posiciones:
        entrada = p.get("entry_price") or 0
        stop = p.get("stop_loss_price") or 0
        cantidad = p.get("quantity") or 0
        if entrada and stop:
            peor += (entrada - stop) * cantidad

    lineas = [
        f"⚠️ *Lo que estás aceptando:*\n",
        f"• {len(foto.posiciones)} posición(es) por *${foto.capital_expuesto_ars:,.0f}* quedan "
        f"abiertas y *sin vigilancia de stop-loss*.",
        f"• Los stop-loss configurados dejan de existir: son órdenes que el bot ejecuta cuando "
        f"se toca el precio, no órdenes puestas en el mercado. Si el bot está apagado, nadie "
        f"las dispara.",
        f"• Si cada posición llegara a su stop y nadie la cerrara, la pérdida podría superar "
        f"los *${peor:,.0f}* — ese número es el piso, no el techo: sin stop no hay piso real.",
    ]
    if foto.ordenes_pendientes:
        lineas.append(f"• {len(foto.ordenes_pendientes)} orden(es) siguen vivas en el mercado y "
                      f"pueden ejecutarse después de que el bot se apague.")
    lineas.append("\n*Cuándo tiene sentido igual:* si el bot está tomando decisiones erráticas, "
                  "dejarlo operar para 'salir ordenadamente' puede ser peor que congelarlo. "
                  "Vos sabés cuál es el caso.")
    lineas.append("\n*Qué hacer después:* entrá al bróker por la app o la web y gestioná esas "
                  "posiciones a mano.")
    return "\n".join(lineas)


def confirmar_corte_seco(codigo: str, notifier) -> dict:
    """Segundo paso del corte seco: mostrar el riesgo y pedir una confirmación
    distinta. Dos toques no son burocracia — es la diferencia entre elegir esto
    y tocarlo sin querer."""
    with _lock:
        dato = _confirmaciones.get(codigo)
    if not dato:
        return {"ok": False, "motivo": "Código vencido o desconocido."}

    foto = dato[2]
    codigo2 = _nuevo_codigo()
    with _lock:
        _confirmaciones[codigo2] = (MODO_CORTE_SECO, time.time(), foto)

    _enviar(notifier,
            f"🔴 *Corte seco — confirmación final*\n\n{_riesgo_del_corte_seco(foto)}\n\n"
            f"Si entendés y querés seguir, respondé:\n`CONFIRMO CORTE {codigo2}`",
            {"inline_keyboard": [[
                {"text": "🔴 Sí, cortar igual", "callback_data": f"corte_final:{codigo2}"},
                {"text": "✖️ No, volver", "callback_data": f"parada:nada:{codigo2}"},
            ]]})
    return {"ok": True, "codigo": codigo2}


def ejecutar_parada(modo: str, notifier, ppi_client=None) -> dict:
    """Ejecuta la parada elegida. Cada modo hace algo distinto y lo informa."""
    import p_risk_guardian as rg

    foto = tomar_foto(ppi_client)
    resultado = {"modo": modo, "momento": datetime.now().isoformat(timespec="seconds")}

    if modo == MODO_ORDENADA:
        rg.trigger_halt("PARADA_ORDENADA_POR_USUARIO")
        resultado["detalle"] = (
            f"El bot dejó de abrir posiciones. Las {len(foto.posiciones)} abiertas se siguen "
            f"vigilando y van a cerrar solas por stop-loss o take-profit.")
        _enviar(notifier,
                f"🟡 *Parada ordenada activada*\n\n{resultado['detalle']}\n\n"
                f"Capital todavía expuesto: ${foto.capital_expuesto_ars:,.0f}\n\n"
                f"Para volver a operar: `REARRANCAR`", None)

    elif modo == MODO_LIQUIDAR:
        rg.trigger_halt("LIQUIDACION_TOTAL_POR_USUARIO")
        cerradas, fallidas = _liquidar_todo(ppi_client, notifier)
        resultado["cerradas"] = cerradas
        resultado["fallidas"] = fallidas
        detalle = f"Se enviaron órdenes de venta a mercado para {len(cerradas)} posición(es)."
        if fallidas:
            detalle += (f"\n\n⚠️ *{len(fallidas)} no se pudieron cerrar:* "
                        + ", ".join(f"{f['ticker']} ({f['motivo']})" for f in fallidas)
                        + "\nEsas quedaron abiertas: revisalas en el bróker.")
        resultado["detalle"] = detalle
        _enviar(notifier, f"🔴 *Liquidación total*\n\n{detalle}\n\n"
                          f"Para volver a operar: `REARRANCAR`", None)

    elif modo == MODO_CORTE_SECO:
        rg.trigger_halt("CORTE_SECO_POR_USUARIO")
        try:
            import ao_startup_gate as gate
            gate.autorizar(gate.MODO_DETENIDO, origen="parada de emergencia")
        except Exception as e:
            logger.warning("No se pudo marcar el portón como detenido: %s", e)
        resultado["detalle"] = (
            f"Bot detenido. {len(foto.posiciones)} posición(es) por "
            f"${foto.capital_expuesto_ars:,.0f} quedaron abiertas SIN vigilancia.")
        _enviar(notifier,
                f"⛔ *Corte seco ejecutado*\n\n{resultado['detalle']}\n\n"
                f"Gestioná esas posiciones desde la app del bróker.\n\n"
                f"Para volver a operar: `REARRANCAR` — pero antes revisá qué quedó abierto, "
                f"porque al rearrancar el bot va a encontrarse con posiciones que no abrió "
                f"en esta sesión.", None)

    _registrar(resultado)
    return resultado


def _liquidar_todo(ppi_client, notifier) -> tuple:
    """Manda a mercado la venta de cada posición. No se detiene ante un fallo:
    si una no se puede cerrar, se sigue con las demás y se informa cuál quedó.
    Abortar la liquidación entera porque falló un papel dejaría abiertas
    también las que sí se podían cerrar."""
    import k_position_manager as pm
    import d_economics as economics

    cerradas, fallidas = [], []
    for posicion in (pm.get_open_positions() or []):
        ticker = posicion.get("ticker")
        try:
            datos = ppi_client.get_market_data(
                ticker, posicion.get("instrument_type", "ACCIONES"),
                posicion.get("settlement", "A-24HS"))
            precio = (datos or {}).get("price")
            if not precio:
                fallidas.append({"ticker": ticker, "motivo": "sin precio de referencia"})
                continue

            pm._close_position(posicion, precio, "LIQUIDACION_EMERGENCIA",
                               economics.calculate_trade_costs_fraction())
            cerradas.append({"ticker": ticker, "precio": precio,
                             "cantidad": posicion.get("quantity")})
        except Exception as e:
            fallidas.append({"ticker": ticker, "motivo": str(e)[:80]})
            logger.error("No se pudo liquidar %s: %s", ticker, e)

    return cerradas, fallidas


# ---------------------------------------------------------------------------
# /rearrancar y /estado
# ---------------------------------------------------------------------------

def comando_rearrancar(notifier, ppi_client=None) -> dict:
    """
    Vuelve a habilitar la operación. No es automático: primero verifica que
    las condiciones estén dadas y lo informa.

    Un rearranque a ciegas después de una parada de emergencia es cómo se
    repite el problema que provocó la parada. Si el corte fue financiero
    —tocaste el límite de pérdida— el sistema te dice que ese límite sigue
    ahí y que rearrancar sin entender qué pasó es volver a la misma situación.
    """
    import p_risk_guardian as rg

    motivo = rg.halt_reason() if rg.is_halted() else ""
    foto = tomar_foto(ppi_client)
    codigo = _nuevo_codigo()
    with _lock:
        _confirmaciones[codigo] = ("REARRANCAR", time.time(), foto)

    advertencias = []
    if motivo and "FINANCIER" in motivo.upper() or "PERDIDA" in (motivo or "").upper():
        advertencias.append("El corte fue *financiero*: el sistema tocó un límite de pérdida. "
                            "Rearrancar sin haber entendido qué pasó es volver a la misma "
                            "situación con menos capital.")
    if foto.posiciones:
        advertencias.append(f"Hay {len(foto.posiciones)} posición(es) abierta(s) que el bot va a "
                            f"retomar y vigilar. Verificá que sigan teniendo sentido.")
    if not foto.mercado_abierto:
        advertencias.append("El mercado está cerrado. El bot va a quedar esperando la apertura.")

    texto = ["🔄 *Rearranque*\n"]
    texto.append(f"Motivo del corte: `{motivo or 'sin corte activo'}`\n")
    if advertencias:
        texto.append("*Antes de seguir:*")
        texto += [f"• {a}" for a in advertencias]
        texto.append("")
    texto.append(f"Respondé `CONFIRMO REARRANCAR {codigo}` para volver a operar.")

    _enviar(notifier, "\n".join(texto), {"inline_keyboard": [[
        {"text": "🔄 Rearrancar", "callback_data": f"rearrancar:{codigo}"},
        {"text": "✖️ Cancelar", "callback_data": f"parada:nada:{codigo}"},
    ]]})
    return {"codigo": codigo, "advertencias": advertencias}


def ejecutar_rearranque(notifier) -> dict:
    import p_risk_guardian as rg
    try:
        rg.clear_halt()
        import ao_startup_gate as gate
        gate.autorizar(gate.MODO_SIMULACION if gate.esta_en_simulacion() else gate.MODO_REAL,
                       origen="rearranque por Telegram")
        _enviar(notifier, "✅ *Bot rearrancado.* Vuelve a evaluar el mercado en el próximo ciclo.", None)
        _registrar({"modo": "REARRANQUE", "momento": datetime.now().isoformat(timespec="seconds")})
        return {"ok": True}
    except Exception as e:
        _enviar(notifier, f"❌ No se pudo rearrancar: {e}", None)
        return {"ok": False, "motivo": str(e)}


def comando_estado(notifier, ppi_client=None) -> dict:
    """Consulta sin efectos: qué hay abierto y en qué estado está el sistema."""
    import p_risk_guardian as rg
    foto = tomar_foto(ppi_client)
    detenido = rg.is_halted()
    estado = (f"🔴 *Detenido* — {rg.halt_reason()}" if detenido else "🟢 *Operando*")
    _enviar(notifier, f"{estado}\n\n{_formatear_foto(foto)}", None)
    return {"detenido": detenido, "posiciones": len(foto.posiciones)}


def comando_ayuda(notifier) -> None:
    _enviar(notifier,
            "*Comandos disponibles*\n\n"
            "`ESTADO` — qué hay abierto y en qué estado está el bot\n"
            "`PARADA` — parada de emergencia, con las tres formas de frenar\n"
            "`REARRANCAR` — volver a operar después de una parada\n"
            "`PENDIENTES` — órdenes que esperan tu confirmación\n"
            "`AYUDA` — esta lista", None)


# ---------------------------------------------------------------------------
# Enrutador
# ---------------------------------------------------------------------------

def procesar_mensaje(texto: str, notifier, ppi_client=None,
                     remitente_id=None) -> Optional[dict]:
    """
    Interpreta un mensaje de Telegram y ejecuta el comando si corresponde.
    Devuelve None si el texto no era un comando conocido.

    Se aceptan comandos escritos además de los botones porque los botones
    inline fallan en clientes viejos, y quedarse sin forma de frenar el bot
    por un problema de interfaz sería exactamente el peor momento.
    """
    if not _remitente_autorizado(remitente_id):
        return {"ok": False, "motivo": "remitente no autorizado"}

    partes = (texto or "").strip().lstrip("/").split()
    if not partes:
        return None
    comando = partes[0].upper()
    codigo = partes[-1] if len(partes) > 1 else ""

    if comando in ("AYUDA", "HELP", "START"):
        comando_ayuda(notifier)
        return {"comando": "AYUDA"}

    if comando == "ESTADO":
        return comando_estado(notifier, ppi_client)

    if comando in ("PARADA", "PARADA_EMERGENCIA", "EMERGENCIA", "STOP"):
        return comando_parada_emergencia(notifier, ppi_client)

    if comando == "PENDIENTES":
        foto = tomar_foto(ppi_client)
        _enviar(notifier, _formatear_foto(foto), None)
        return {"comando": "PENDIENTES"}

    if comando in ("ORDENADA", "LIQUIDAR", "DETENER"):
        if not _codigo_valido(codigo):
            _enviar(notifier, "El código no es válido o ya venció. Mandá `PARADA` de nuevo.", None)
            return {"ok": False}
        modo = MODO_LIQUIDAR if comando == "LIQUIDAR" else MODO_ORDENADA
        return ejecutar_parada(modo, notifier, ppi_client)

    if comando == "CORTE":
        if not _codigo_valido(codigo):
            _enviar(notifier, "El código no es válido o ya venció. Mandá `PARADA` de nuevo.", None)
            return {"ok": False}
        return confirmar_corte_seco(codigo, notifier)

    if comando == "CONFIRMO" and len(partes) >= 3:
        sub = partes[1].upper()
        if sub == "CORTE" and _codigo_valido(codigo):
            return ejecutar_parada(MODO_CORTE_SECO, notifier, ppi_client)
        if sub == "REARRANCAR" and _codigo_valido(codigo):
            return ejecutar_rearranque(notifier)
        _enviar(notifier, "El código no es válido o ya venció.", None)
        return {"ok": False}

    if comando == "REARRANCAR":
        return comando_rearrancar(notifier, ppi_client)

    return None


def procesar_boton(callback_data: str, notifier, ppi_client=None,
                   remitente_id=None) -> Optional[dict]:
    """Traduce el toque de un botón al mismo camino que el comando escrito."""
    if not _remitente_autorizado(remitente_id):
        return {"ok": False, "motivo": "remitente no autorizado"}

    partes = (callback_data or "").split(":")
    if len(partes) < 2:
        return None
    accion = partes[0]
    codigo = partes[-1]

    if accion == "parada":
        eleccion = partes[1]
        if eleccion == "nada":
            _enviar(notifier, "Cancelado. No se tocó nada.", None)
            return {"ok": True, "cancelado": True}
        if not _codigo_valido(codigo):
            _enviar(notifier, "El código venció. Mandá `PARADA` de nuevo.", None)
            return {"ok": False}
        if eleccion == "ordenada":
            return ejecutar_parada(MODO_ORDENADA, notifier, ppi_client)
        if eleccion == "liquidar":
            return ejecutar_parada(MODO_LIQUIDAR, notifier, ppi_client)
        if eleccion == "corte":
            return confirmar_corte_seco(codigo, notifier)

    if accion == "corte_final" and _codigo_valido(codigo):
        return ejecutar_parada(MODO_CORTE_SECO, notifier, ppi_client)

    if accion == "rearrancar" and _codigo_valido(codigo):
        return ejecutar_rearranque(notifier)

    return None


# ---------------------------------------------------------------------------
# Códigos de confirmación (endurecidos en v16.2)
# ---------------------------------------------------------------------------
# Los códigos se generaban como int(time.time()) % 100000: cualquiera que
# supiera la hora los reproducía. Aislado no era explotable, porque los
# mensajes de texto ni se leían — pero la corrección natural de ese defecto
# (empezar a leer message.text, que es justamente lo que hace esta versión)
# abría el camino completo: un tercero manda PARADA, calcula el código a
# partir del reloj y responde LIQUIDAR <código>. El resultado sería la venta
# a mercado de toda la cartera por alguien que no es el dueño.
#
# Por eso las dos correcciones salieron JUNTAS y en la misma versión. Arreglar
# la escucha sin arreglar la autorización habría abierto una puerta que hasta
# hoy estaba cerrada por accidente.

MAX_INTENTOS_FALLIDOS = int(os.getenv("TELEGRAM_MAX_INTENTOS_CODIGO", "3"))
_intentos_fallidos = {"cuenta": 0, "desde": 0.0}


def _nuevo_codigo() -> str:
    """Código de cinco dígitos criptográficamente aleatorio."""
    return f"{secrets.randbelow(100000):05d}"


def _remitente_autorizado(remitente_id) -> bool:
    """Segunda barrera, independiente del lector.

    El lector de b_notifiers ya filtra por remitente, pero esta validación se
    repite acá a propósito: es el intérprete de comandos el que ejecuta
    acciones irreversibles, y una protección que vive en un solo lugar deja de
    existir el día que alguien refactoriza ese lugar. Si no viene remitente
    (llamada interna o test), no se bloquea: el filtro es contra un remitente
    EQUIVOCADO, no contra la ausencia de dato.
    """
    autorizado = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not autorizado or remitente_id is None:
        return True
    if str(remitente_id) != autorizado:
        logger.warning("Comando de Telegram NO AUTORIZADO rechazado (id=%s).", remitente_id)
        return False
    return True


def _registrar_intento_fallido(notifier) -> None:
    """Tres códigos inválidos seguidos avisan al chat autorizado.

    Un código inválido suelto es alguien que tardó y se le venció. Tres
    seguidos es otra cosa, y el dueño tiene que enterarse en el momento, no
    cuando revise los logs.
    """
    ahora = time.time()
    if ahora - _intentos_fallidos["desde"] > 900:
        _intentos_fallidos["cuenta"] = 0
        _intentos_fallidos["desde"] = ahora
    _intentos_fallidos["cuenta"] += 1
    if _intentos_fallidos["cuenta"] >= MAX_INTENTOS_FALLIDOS:
        _intentos_fallidos["cuenta"] = 0
        logger.error("Se superó el límite de códigos inválidos de Telegram.")
        _enviar(notifier,
                f"⚠️ *Atención*: {MAX_INTENTOS_FALLIDOS} códigos de confirmación "
                "inválidos en los últimos minutos. Si no fuiste vos, rotá el token "
                "del bot con BotFather.", None)


def _codigo_valido(codigo: str) -> bool:
    with _lock:
        dato = _confirmaciones.get(codigo)
        if not dato:
            return False
        if time.time() - dato[1] > CONFIRM_TTL_SECONDS:
            _confirmaciones.pop(codigo, None)
            return False
        return True


def _enviar(notifier, texto: str, teclado: Optional[dict]) -> None:
    if notifier is None:
        logger.info("Telegram (sin notificador): %s", texto[:200])
        return
    try:
        if teclado:
            notifier.send(texto, reply_markup=teclado, parse_mode="Markdown")
        else:
            notifier.send(texto, parse_mode="Markdown")
    except Exception as e:
        logger.error("No se pudo enviar el mensaje a Telegram: %s", e)


def _registrar(evento: dict) -> None:
    """Toda parada y todo rearranque quedan grabados. Si algo salió mal, la
    primera pregunta va a ser 'quién frenó esto y cuándo'."""
    try:
        import ac_db
        conn = ac_db.connect_raw()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS emergency_actions (
                momento TEXT, modo TEXT, detalle TEXT
            )""")
        conn.execute("INSERT INTO emergency_actions (momento, modo, detalle) VALUES (?, ?, ?)",
                     (evento.get("momento"), evento.get("modo"), evento.get("detalle", "")))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("No se pudo registrar la acción de emergencia: %s", e)


# ---------------------------------------------------------------------------
# ELIMINADO EN v16.2 — arrancar_escucha()
# ---------------------------------------------------------------------------
# Acá vivía un segundo bucle que llamaba a notifier.get_telegram_button_taps()
# con su propio offset. Tenía dos defectos, y el segundo era peor que el
# primero:
#
#  1) Estaba roto por un error de contrato. get_telegram_button_taps()
#     devuelve la TUPLA (taps, next_offset), y el bucle hacía
#     `for act in notifier.get_telegram_button_taps(offset)` y después
#     `act.get("update_id")`. Reproducido en banco: AttributeError en cada
#     vuelta ('list' object has no attribute 'get'), absorbido por el except
#     del propio bucle. La parada de emergencia no respondía a nada.
#
#  2) Aunque hubiera estado bien escrito, competía con el lector de
#     l_order_confirmation por las mismas actualizaciones. Telegram las
#     consume al entregarlas: dos lectores con offsets independientes se
#     comen los mensajes del otro de forma no determinística. El síntoma no
#     es un error visible sino botones de confirmación de orden que a veces
#     funcionan y a veces no.
#
# Ahora hay UN SOLO lector en todo el sistema —b_notifiers.get_telegram_
# button_taps()— y UN SOLO consumidor —l_order_confirmation.process_button_
# taps()—, que despacha por prefijo hacia procesar_mensaje() y
# procesar_boton() de este módulo. No hay hilo que arrancar: el sondeo ocurre
# en el bucle principal, donde ya estaba.
#
# Si algún módulo necesita reaccionar a Telegram, la forma correcta es sumar
# un prefijo al despachador, nunca abrir un getUpdates propio.
