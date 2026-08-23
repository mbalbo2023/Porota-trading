"""
ap_api_verifier.py — Verificación end-to-end de todas las APIs externas

QUÉ ES ESTO Y POR QUÉ SE EJECUTA DEL LADO TUYO
---------------------------------------------------------------------------
Este módulo recorre TODAS las llamadas que el bot le hace a servicios
externos, las ejecuta una por una, y documenta qué se envía, qué se pide y
qué devuelve cada una. Al terminar genera un informe en Markdown y un JSON
con el detalle completo, listos para leer o archivar.

Se ejecuta desde la terminal, en tu Codespace o en tu servidor:

    python ap_api_verifier.py                 # todas las verificaciones
    python ap_api_verifier.py --solo iol      # una sola familia
    python ap_api_verifier.py --dry           # muestra qué haría, sin llamar

Corre del lado tuyo por una razón que no es de preferencia: el entorno donde
se escribió este código no tiene salida a Internet, así que ninguna de estas
llamadas puede ejecutarse ahí. El código está escrito, probado en su lógica
con respuestas simuladas, y listo — pero el veredicto sobre si las
credenciales funcionan solo puede darlo una corrida real contra los
servidores. Cualquier informe que dijera lo contrario sería inventado.

CÓMO LEE LAS CREDENCIALES
---------------------------------------------------------------------------
Únicamente desde variables de entorno. Ninguna credencial está escrita en
este archivo ni en ningún otro del paquete, y el informe que genera enmascara
todo lo que parezca una llave. Eso no es una traba burocrática: el informe es
justamente el archivo que uno termina mandando por chat o adjuntando a un
ticket, así que es el lugar donde una credencial se filtra más fácil.

SEGURIDAD DE LAS PRUEBAS
---------------------------------------------------------------------------
Ninguna verificación manda una orden real. Las que tocan el circuito de
órdenes usan el presupuesto/estimación, que simula y devuelve costos sin
ejecutar nada. Las órdenes de verdad se prueban en el entorno SANDBOX de PPI,
que es plata ficticia, y solo si ENVIRONMENT=SANDBOX.
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any, Callable, List, Optional

PATRON_SECRETO = re.compile(
    r"(?i)(token|password|secret|api[_-]?key|authorization|bearer|refresh_token)"
    r"['\"\s:=]+([A-Za-z0-9._\-+/=]{6,})"
)


def enmascarar(texto: str) -> str:
    """Deja visible el tipo de dato y los últimos 4 caracteres, nada más."""
    def _reemplazo(m):
        valor = m.group(2)
        return f"{m.group(1)}: ***{valor[-4:]}"
    return PATRON_SECRETO.sub(_reemplazo, str(texto))


@dataclass
class Verificacion:
    """Una llamada, documentada de punta a punta."""
    familia: str
    nombre: str
    proposito: str                  # para qué sirve dentro del bot
    metodo: str = ""
    endpoint: str = ""
    envia: str = ""                 # qué se manda
    espera: str = ""                # qué se espera recibir
    modulo_del_bot: str = ""        # quién la usa en el código
    estado: str = "NO_EJECUTADA"    # OK | FALLA | OMITIDA | NO_EJECUTADA
    codigo_http: Optional[int] = None
    latencia_ms: Optional[int] = None
    respuesta_resumida: str = ""
    error: str = ""
    oportunidad: str = ""           # qué de esta API no estamos aprovechando


class Verificador:
    def __init__(self, dry: bool = False):
        self.dry = dry
        self.resultados: List[Verificacion] = []

    def ejecutar(self, v: Verificacion, accion: Callable[[], Any]) -> Verificacion:
        """Corre una verificación midiendo tiempo y capturando cualquier error.

        Ningún fallo interrumpe la corrida: si IOL está caído, igual queremos
        el veredicto de PPI, Gemini y Telegram en el mismo informe. Un
        verificador que se detiene en el primer error obliga a correrlo cinco
        veces para ver los cinco problemas.
        """
        if self.dry:
            v.estado = "OMITIDA"
            v.respuesta_resumida = "Modo --dry: no se ejecutó ninguna llamada real."
            self.resultados.append(v)
            return v

        inicio = time.perf_counter()
        try:
            resultado = accion()
            v.latencia_ms = int((time.perf_counter() - inicio) * 1000)
            if isinstance(resultado, dict) and "_http" in resultado:
                v.codigo_http = resultado.pop("_http")
            v.estado = "OK" if resultado not in (None, False, [], {}) else "FALLA"
            if v.estado == "FALLA" and not v.error:
                v.error = "La llamada respondió, pero devolvió un resultado vacío."
            v.respuesta_resumida = enmascarar(json.dumps(resultado, ensure_ascii=False,
                                                         default=str)[:600])
        except Exception as e:
            v.latencia_ms = int((time.perf_counter() - inicio) * 1000)
            v.estado = "FALLA"
            v.error = enmascarar(f"{type(e).__name__}: {e}")[:400]
        self.resultados.append(v)
        estado_visible = {"OK": "🟢", "FALLA": "🔴", "OMITIDA": "⚪"}.get(v.estado, "⚪")
        print(f"  {estado_visible} {v.familia}/{v.nombre} — {v.estado}"
              + (f" ({v.latencia_ms} ms)" if v.latencia_ms else "")
              + (f"\n      {v.error}" if v.error else ""))
        return v


# ===========================================================================
# IOL — Invertir Online
# ===========================================================================

def verificar_iol(vf: Verificador) -> None:
    """
    IOL usa OAuth2 con credenciales directas. El access_token dura 20 minutos
    y viene con refresh_token, así que la sesión se mantiene renovando y no
    volviendo a mandar usuario y contraseña en cada ciclo.

    NOTA SOBRE EL ENDPOINT DEL TOKEN: la documentación de IOL muestra el token
    en /api/v2/token, mientras que buena parte de los ejemplos y wrappers
    públicos usan /token. Como no se puede probar cuál responde sin salida a
    red, el cliente prueba primero el documentado y cae al otro si devuelve
    404. Es un caso donde adivinar sería peor que intentar los dos.
    """
    print("\n[IOL] Invertir Online")
    import ak_iol_client

    cliente = ak_iol_client.IOLClient()
    if not cliente.enabled:
        print("  ⚪ IOL desactivado (IOL_ENABLED != true o faltan credenciales). Se omite.")
        return

    vf.ejecutar(Verificacion(
        familia="IOL", nombre="autenticacion",
        proposito="Obtener el bearer token que autoriza todas las demás llamadas.",
        metodo="POST", endpoint="/api/v2/token",
        envia="username, password y grant_type=password, como formulario codificado.",
        espera="access_token, refresh_token y expires_in (1200 s).",
        modulo_del_bot="ak_iol_client.IOLClient._authenticate()",
    ), lambda: bool(cliente._headers()))

    vf.ejecutar(Verificacion(
        familia="IOL", nombre="estado_cuenta",
        proposito="Liquidez disponible en pesos y dólares, y saldo comprometido en órdenes.",
        metodo="GET", endpoint="/api/v2/estadocuenta",
        envia="Solo la cabecera Authorization: Bearer.",
        espera="Disponible ARS y USD, comprometido, liquidez a 24/48 h y valuación total.",
        modulo_del_bot="ak_iol_client.IOLClient.get_estado_cuenta()",
        oportunidad="La liquidez a 24 y 48 horas no se estaba usando en ningún lado. "
                    "Sirve para saber cuánto capital va a estar realmente disponible al "
                    "momento de liquidar, en vez de dimensionar contra un saldo que "
                    "todavía está comprometido.",
    ), lambda: cliente.get_estado_cuenta())

    vf.ejecutar(Verificacion(
        familia="IOL", nombre="portafolio",
        proposito="Tenencias con precio promedio de compra y resultado no realizado.",
        metodo="GET", endpoint="/api/v2/portafolio/argentina",
        envia="Solo la cabecera Authorization.",
        espera="Símbolo, cantidad, precio promedio, cotización actual y ganancia sin realizar.",
        modulo_del_bot="ak_iol_client.IOLClient.get_portafolio()",
        oportunidad="Este endpoint devuelve también las OPCIONES en cartera. Es una segunda "
                    "fuente para reconciliar posiciones de derivados, justo la clase de "
                    "instrumento donde un descuadre es más caro de descubrir tarde.",
    ), lambda: cliente.get_portafolio())

    vf.ejecutar(Verificacion(
        familia="IOL", nombre="cotizacion_tiempo_real",
        proposito="Precio y puntas de un instrumento, para validación cruzada contra PPI.",
        metodo="GET", endpoint="/api/v2/bcba/Titulos/GGAL/Cotizacion",
        envia="Símbolo y mercado en la ruta.",
        espera="Último precio, puntas de compra y venta, volumen, máximo y mínimo.",
        modulo_del_bot="ak_iol_client.IOLClient.get_cotizacion()",
    ), lambda: cliente.get_cotizacion("GGAL"))

    vf.ejecutar(Verificacion(
        familia="IOL", nombre="serie_historica",
        proposito="Velas diarias ajustadas para el archivo histórico y el backtesting.",
        metodo="GET",
        endpoint="/api/v2/bcba/Titulos/GGAL/Cotizacion/seriehistorica/{desde}/{hasta}/true",
        envia="Fechas desde y hasta, y el flag de ajustada en true.",
        espera="Lista de velas con fecha, apertura, máximo, mínimo, cierre y monto operado.",
        modulo_del_bot="ak_iol_client.IOLClient.get_serie_historica()",
    ), lambda: {"velas": len(cliente.get_serie_historica("GGAL", dias=30))})

    vf.ejecutar(Verificacion(
        familia="IOL", nombre="estimacion_de_costos",
        proposito="Simular una orden para conocer aranceles reales sin ejecutarla.",
        metodo="POST", endpoint="/api/v2/operar/estimar",
        envia="Mercado, símbolo, cantidad, precio y tipo de operación.",
        espera="Desglose de comisión, derechos de mercado e IVA.",
        modulo_del_bot="ak_iol_client.estimar_operacion() + reconciliar_costos()",
        oportunidad="Permite auditar el modelo de costos propio contra un bróker real sin "
                    "gastar un peso. Es la única forma de detectar que falta un componente "
                    "de costo antes de que aparezca en el resumen de cuenta.",
    ), lambda: cliente.estimar_operacion("GGAL", cantidad=1, precio=1.0))


# ===========================================================================
# PPI
# ===========================================================================

class _NotificadorSilencioso:
    """Evita que una prueba de conectividad genere alertas operativas."""

    def notify_recovery(self, *args, **kwargs):
        return None

    def notify_error(self, *args, **kwargs):
        return None


def verificar_ppi(vf: Verificador, ejecutar_orden_sandbox: bool = False) -> None:
    print("\n[PPI] Portfolio Personal Inversiones")
    import c_ppi_client

    entorno = os.getenv("ENVIRONMENT", "SANDBOX").upper()
    print(f"  Entorno: {entorno}")
    cliente = c_ppi_client.ResilientPPIClient(_NotificadorSilencioso())

    login = vf.ejecutar(Verificacion(
        familia="PPI", nombre="login",
        proposito="Abrir sesión: sin esto no hay precios ni órdenes.",
        metodo="POST", endpoint="/api/1.0/Account/Login",
        envia="AuthorizedClient, ClientKey y el par de llaves pública/privada en cabeceras.",
        espera="accessToken con su vencimiento y refreshToken.",
        modulo_del_bot="c_ppi_client.ResilientPPIClient.login()",
    ), cliente.login)

    if login.estado != "OK":
        print("  ⚪ Pruebas PPI restantes OMITIDAS: el login no fue válido.")
        return

    vf.ejecutar(Verificacion(
        familia="PPI", nombre="saldos",
        proposito="Capital disponible: es el denominador del dimensionamiento de posición.",
        metodo="GET", endpoint="/api/1.0/Account/Balances",
        envia="Número de cuenta y token.",
        espera="Disponible en pesos y en dólares por plazo de liquidación.",
        modulo_del_bot="c_ppi_client.get_available_balance()",
    ), lambda: cliente.get_available_balance())

    vf.ejecutar(Verificacion(
        familia="PPI", nombre="cotizacion",
        proposito="Precio con el que se decide y se dimensiona toda operación.",
        metodo="GET", endpoint="/api/1.0/MarketData/Current",
        envia="Ticker, tipo de instrumento y plazo de liquidación.",
        espera="Último precio con su marca de tiempo.",
        modulo_del_bot="c_ppi_client.get_market_data()",
    ), lambda: cliente.get_market_data("AL30", "BONOS", "A-24HS"))

    vf.ejecutar(Verificacion(
        familia="PPI", nombre="libro_de_ofertas",
        proposito="Profundidad del book: define el spread y el tope de cantidad por liquidez.",
        metodo="GET", endpoint="/api/1.0/MarketData/Book",
        envia="Ticker, tipo y plazo.",
        espera="Puntas de compra y venta con sus volúmenes.",
        modulo_del_bot="c_ppi_client.get_book() → economics.cap_by_book_liquidity()",
    ), lambda: cliente.get_book("AL30", "BONOS", "A-24HS"))

    vf.ejecutar(Verificacion(
        familia="PPI", nombre="presupuesto_de_orden",
        proposito="Simular el costo de una orden y, de paso, detectar qué tipos de "
                  "instrumento tiene habilitados la cuenta.",
        metodo="POST", endpoint="/api/1.0/Order/Budget",
        envia="Cuenta, ticker, tipo, cantidad, precio y lado.",
        espera="Costo estimado. No coloca ninguna orden.",
        modulo_del_bot="c_ppi_client.budget_order() → m_instrument_universe.detect_account_permissions()",
    ), lambda: cliente.budget_order(os.getenv("PPI_ACCOUNT_NUMBER", ""), 1, 1.0, "AL30", "BONOS"))

    for clase in ("ACCIONES", "CEDEARS", "BONOS", "OPCIONES", "FUTUROS"):
        vf.ejecutar(Verificacion(
            familia="PPI", nombre=f"instrumentos_{clase.lower()}",
            proposito=f"Descubrir qué instrumentos de tipo {clase} ve la cuenta.",
            metodo="GET", endpoint="/api/1.0/MarketData/SearchInstrument",
            envia="Tipo de instrumento.",
            espera="Lista de tickers disponibles.",
            modulo_del_bot="m_instrument_universe.build_universe()",
        ), lambda c=clase: _conteo_instrumentos(cliente, c))

    if entorno == "SANDBOX" and ejecutar_orden_sandbox:
        vf.ejecutar(Verificacion(
            familia="PPI", nombre="orden_sandbox",
            proposito="Probar el circuito completo de envío de orden con plata ficticia.",
            metodo="POST", endpoint="/api/1.0/Order",
            envia="Cuenta, ticker, tipo, plazo, cantidad, precio, lado y tipo de orden.",
            espera="Identificador de orden.",
            modulo_del_bot="c_ppi_client.confirm_order() + cancel_order()",
        ), lambda: _orden_sandbox_con_cancelacion(cliente))
    else:
        motivo = "falta --orden-sandbox" if entorno == "SANDBOX" else "ENVIRONMENT no es SANDBOX"
        print(f"  ⚪ Envío de orden OMITIDO: {motivo}. No se manda ninguna orden.")


def _orden_sandbox_con_cancelacion(cliente):
    cuenta = os.getenv("PPI_ACCOUNT_NUMBER", "")
    presupuesto = cliente.budget_order(cuenta, 1, 1.0, "AL30", "BONOS")
    if not presupuesto:
        raise RuntimeError("PPI no devolvió presupuesto; la orden Sandbox no se envió.")
    external_id = f"api-verifier-sandbox-{int(time.time())}"
    confirmacion = cliente.confirm_order(
        cuenta, 1, 1.0, "AL30", presupuesto.get("disclaimers", []),
        instrument_type="BONOS", order_type="PRECIO-LIMITE",
        operation="COMPRA", settlement="A-24HS", external_id=external_id,
        sandbox_probe=True,
    )
    if not confirmacion or not confirmacion.get("id"):
        raise RuntimeError("PPI no confirmó la creación de la orden Sandbox.")
    order_id = confirmacion["id"]
    cancelacion = cliente.cancel_order(order_id, external_id)
    if not cancelacion:
        raise RuntimeError(
            f"La orden Sandbox {order_id} fue creada pero PPI no confirmó su cancelación."
        )
    return {
        "order_id": order_id,
        "estado_inicial": confirmacion.get("status"),
        "cancelada": True,
        "estado_cancelacion": cancelacion.get("status") if isinstance(cancelacion, dict) else str(cancelacion),
    }


def _conteo_instrumentos(cliente, clase):
    instrumentos = cliente.search_instruments(clase)
    if instrumentos is None:
        return None
    return {"encontrados": len(instrumentos)}


# ===========================================================================
# Gemini, Telegram y fuentes públicas
# ===========================================================================

def verificar_gemini(vf: Verificador) -> None:
    print("\n[GEMINI] Motor de decisión")
    vf.ejecutar(Verificacion(
        familia="Gemini", nombre="inferencia_basica",
        proposito="Confirmar que la llave responde y el modelo configurado existe.",
        metodo="POST", endpoint="generateContent",
        envia="Un prompt mínimo pidiendo una respuesta de una palabra.",
        espera="Texto de respuesta.",
        modulo_del_bot="f_gemini_decision_engine.py",
    ), _inferencia_minima)

    vf.ejecutar(Verificacion(
        familia="Gemini", nombre="function_calling",
        proposito="Confirmar que el modelo puede invocar las herramientas del bot, que es "
                  "el mecanismo con el que consulta precios y contexto macro reales.",
        metodo="POST", endpoint="generateContent con tools",
        envia="Un prompt mínimo más una herramienta segura de verificación.",
        espera="Una llamada a función, no texto libre.",
        modulo_del_bot="google.genai (mismo mecanismo usado por ah_market_tools.HERRAMIENTAS)",
    ), _prueba_function_calling)


def _inferencia_minima():
    from google.genai import types
    import f_gemini_decision_engine as motor

    m = motor.GeminiDecisionEngine()
    respuesta = m._llamar_con_timeout_estricto(
        "Respondé solamente: OK",
        types.GenerateContentConfig(),
    )
    texto = (respuesta.text or "").strip()
    if not texto:
        raise RuntimeError("Gemini respondió sin texto en la inferencia básica.")
    return {"respuesta": texto[:100], "modelo": m.model}


def _prueba_function_calling():
    from google.genai import types
    import f_gemini_decision_engine as motor

    m = motor.GeminiDecisionEngine()
    declaracion = types.FunctionDeclaration(
        name="verificar_herramienta",
        description="Confirma que Gemini puede seleccionar una herramienta declarada.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "codigo": {
                    "type": "string",
                    "description": "Código de verificación; debe ser OK.",
                },
            },
            "required": ["codigo"],
        },
    )
    respuesta = m._llamar_con_timeout_estricto(
        "Invocá verificar_herramienta con codigo OK. No respondas texto libre.",
        types.GenerateContentConfig(
            tools=[types.Tool(function_declarations=[declaracion])],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="ANY")
            ),
        ),
    )
    llamadas = respuesta.function_calls or []
    if not llamadas:
        raise RuntimeError("Gemini no devolvió ninguna llamada a función.")
    llamada = llamadas[0]
    return {
        "funcion": llamada.name,
        "argumentos": dict(llamada.args or {}),
        "modelo": m.model,
    }


def verificar_telegram(vf: Verificador) -> None:
    print("\n[TELEGRAM] Notificaciones y control")
    vf.ejecutar(Verificacion(
        familia="Telegram", nombre="envio_mensaje",
        proposito="Confirmar que las alertas llegan al chat correcto.",
        metodo="POST", endpoint="/bot{token}/sendMessage",
        envia="chat_id y el texto del mensaje.",
        espera="ok=true con el mensaje publicado.",
        modulo_del_bot="b_notifiers.send_telegram_alert()",
    ), _mensaje_de_prueba)

    vf.ejecutar(Verificacion(
        familia="Telegram", nombre="botones_inline",
        proposito="Confirmar que los botones de confirmación funcionan: son el mecanismo "
                  "con el que se autoriza el arranque y se aplican los cambios del SRE.",
        metodo="POST", endpoint="/bot{token}/sendMessage con reply_markup",
        envia="chat_id, texto y el teclado inline.",
        espera="Mensaje con botones tocables.",
        modulo_del_bot="ao_startup_gate.py y an_sre_deploy.py",
    ), _mensaje_con_botones)


def _mensaje_de_prueba():
    import b_notifiers
    n = b_notifiers.MultiChannelNotifier()
    return bool(n.send_telegram(
        "🔍 Verificación de APIs de Porota Trading.\n"
        "Si estás viendo este mensaje, el canal de notificaciones funciona."))


def _mensaje_con_botones():
    import b_notifiers
    n = b_notifiers.MultiChannelNotifier()
    return bool(n.send_telegram_generic_confirmation(
        "Probando botones de confirmación.",
        confirm_data="VERIF_OK",
        cancel_data="VERIF_CANCEL",
        confirm_text="✅ Funciona",
        cancel_text="Cerrar prueba",
    ))


def _probar_fetch_macro(macro, serie: str, accion: Callable[[], int]) -> dict:
    """Fuerza una consulta real y convierte el resultado persistido en veredicto.

    Los fetchers de ``ad_macro_history`` son tolerantes a fallos por diseño:
    registran el error en SQLite y devuelven 0 para que una caída de un
    proveedor no detenga el bot. Para un verificador ese contrato no alcanza,
    porque 0 puede significar tanto "no había que refrescar" como "la red
    falló". Esta función invalida solamente el TTL de la serie probada, ejecuta
    la llamada y lee ``macro_fetch_log`` para distinguir ambos casos sin
    cambiar el comportamiento operativo del módulo.
    """
    macro._init_table()
    with macro.ac_db.connect() as conn:
        conn.execute(
            "UPDATE macro_fetch_log SET ultimo_exito = NULL, error = NULL WHERE serie = ?",
            (serie,),
        )

    filas = accion()
    with macro.ac_db.connect() as conn:
        registro = conn.execute(
            "SELECT ultimo_exito, filas, error FROM macro_fetch_log WHERE serie = ?",
            (serie,),
        ).fetchone()

    if not registro:
        raise RuntimeError(f"{serie}: la fuente no dejó resultado de verificación.")
    ultimo_exito, filas_registradas, error = registro
    if error:
        raise RuntimeError(f"{serie}: {error}")
    if not ultimo_exito:
        raise RuntimeError(f"{serie}: la fuente no confirmó una actualización exitosa.")

    cantidad = filas if isinstance(filas, int) else filas_registradas
    if not isinstance(cantidad, int) or cantidad <= 0:
        raise RuntimeError(f"{serie}: la respuesta no contenía datos utilizables.")
    return {"serie": serie, "filas": cantidad}


def _probar_bcra(macro) -> dict:
    series = {}
    for nombre, variable_id in macro.BCRA_VARIABLES.items():
        resultado = _probar_fetch_macro(
            macro,
            f"bcra_{nombre}",
            lambda n=nombre, i=variable_id: macro._fetch_bcra_variable(n, i),
        )
        series[nombre] = resultado["filas"]
    return {"series": series, "filas": sum(series.values())}


def _probar_dolares_publicos(macro) -> dict:
    series = {}
    for casa in ("bolsa", "contadoconliqui"):
        resultado = _probar_fetch_macro(
            macro,
            f"dolar_{casa}",
            lambda c=casa: macro._fetch_dolar_historico(c),
        )
        series[casa] = resultado["filas"]
    return {"series": series, "filas": sum(series.values())}


def verificar_fuentes_publicas(vf: Verificador) -> None:
    print("\n[PÚBLICAS] Macro, noticias y datos de mercado abiertos")
    import ad_macro_history as macro

    for nombre, proposito, accion in [
        ("bcra_variables", "Reservas, tasa de política monetaria y base monetaria.",
         lambda: _probar_bcra(macro)),
        ("datos_gob_ipc", "Inflación del INDEC para el piso de rentabilidad.",
         lambda: _probar_fetch_macro(
             macro,
             "indec_ipc_var_mensual",
             lambda: macro._fetch_datos_ar(
                 "ipc_var_mensual", macro.DATOS_AR_SERIES["ipc_var_mensual"]
             ),
         )),
        ("argentinadatos_dolar", "Serie histórica diaria de MEP y CCL.",
         lambda: _probar_dolares_publicos(macro)),
    ]:
        vf.ejecutar(Verificacion(
            familia="Públicas", nombre=nombre, proposito=proposito,
            metodo="GET", endpoint="(ver ad_macro_history.py)",
            envia="Rango de fechas. Ninguna requiere credenciales.",
            espera="Serie de valores por fecha.",
            modulo_del_bot="ad_macro_history.refresh()",
        ), accion)

    import g_news_feed
    vf.ejecutar(Verificacion(
        familia="Públicas", nombre="feeds_de_noticias",
        proposito="Titulares para la evaluación de contexto de la IA.",
        metodo="GET", endpoint="RSS de medios financieros",
        envia="Nada: son feeds públicos.",
        espera="Lista de titulares recientes.",
        modulo_del_bot="g_news_feed.fetch_latest_headlines()",
    ), lambda: g_news_feed.fetch_latest_headlines())


# ===========================================================================
# Informe
# ===========================================================================

def generar_informe(resultados: List[Verificacion], ruta_md: str, ruta_json: str) -> None:
    ok = [r for r in resultados if r.estado == "OK"]
    fallas = [r for r in resultados if r.estado == "FALLA"]
    omitidas = [r for r in resultados if r.estado == "OMITIDA"]

    lineas = [
        "# Informe de verificación de APIs externas",
        "",
        f"**Generado:** {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}  ",
        f"**Entorno:** {os.getenv('ENVIRONMENT', 'no declarado')}  ",
        f"**Resultado:** {len(ok)} correctas · {len(fallas)} con falla · {len(omitidas)} omitidas",
        "",
        "> Las credenciales aparecen enmascaradas en todo el informe. Este archivo está "
        "pensado para compartirse, así que no contiene ninguna llave completa.",
        "",
        "## Resumen",
        "",
        "| Estado | API | Verificación | Latencia |",
        "|---|---|---|---|",
    ]
    simbolo = {"OK": "🟢", "FALLA": "🔴", "OMITIDA": "⚪", "NO_EJECUTADA": "⚪"}
    for r in resultados:
        lineas.append(f"| {simbolo.get(r.estado,'⚪')} {r.estado} | {r.familia} | {r.nombre} | "
                      f"{str(r.latencia_ms) + ' ms' if r.latencia_ms else '—'} |")

    lineas += ["", "## Detalle de cada llamada", ""]
    for r in resultados:
        lineas += [
            f"### {simbolo.get(r.estado,'⚪')} {r.familia} — {r.nombre}",
            "",
            f"**Para qué sirve en el bot:** {r.proposito}",
            "",
            f"| | |", "|---|---|",
            f"| Método | `{r.metodo}` |",
            f"| Endpoint | `{r.endpoint}` |",
            f"| Qué se envía | {r.envia} |",
            f"| Qué se espera | {r.espera} |",
            f"| Módulo que la usa | `{r.modulo_del_bot}` |",
            f"| Estado | **{r.estado}** |",
        ]
        if r.codigo_http:
            lineas.append(f"| Código HTTP | {r.codigo_http} |")
        if r.latencia_ms:
            lineas.append(f"| Latencia | {r.latencia_ms} ms |")
        lineas.append("")
        if r.respuesta_resumida:
            lineas += ["**Respuesta recibida (recortada y enmascarada):**", "",
                       "```json", r.respuesta_resumida, "```", ""]
        if r.error:
            lineas += [f"**Error:** {r.error}", ""]
        if r.oportunidad:
            lineas += [f"**Oportunidad detectada:** {r.oportunidad}", ""]

    if fallas:
        lineas += ["## Qué revisar", ""]
        for r in fallas:
            lineas.append(f"- **{r.familia}/{r.nombre}**: {r.error or 'respuesta vacía'}")
        lineas.append("")

    with open(ruta_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas))
    with open(ruta_json, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in resultados], f, ensure_ascii=False, indent=2)

    print(f"\nInforme escrito en {ruta_md} y {ruta_json}")


def main():
    parser = argparse.ArgumentParser(description="Verifica todas las APIs externas del bot.")
    parser.add_argument("--solo", choices=["iol", "ppi", "gemini", "telegram", "publicas"],
                        help="Verificar una sola familia.")
    parser.add_argument("--dry", action="store_true",
                        help="Mostrar qué se haría, sin ejecutar ninguna llamada.")
    parser.add_argument("--orden-sandbox", action="store_true",
                        help="Enviar y cancelar una orden ficticia; exige ENVIRONMENT=SANDBOX.")
    parser.add_argument("--salida", default="./data/informe_apis",
                        help="Ruta base de los archivos de salida.")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.salida) or ".", exist_ok=True)
    vf = Verificador(dry=args.dry)

    familias = {
        "iol": verificar_iol,
        "ppi": lambda verificador: verificar_ppi(
            verificador, ejecutar_orden_sandbox=args.orden_sandbox),
        "gemini": verificar_gemini,
        "telegram": verificar_telegram, "publicas": verificar_fuentes_publicas,
    }
    elegidas = [familias[args.solo]] if args.solo else list(familias.values())

    print("=" * 70)
    print("VERIFICACIÓN DE APIs EXTERNAS — POROTA TRADING")
    print("=" * 70)
    for fn in elegidas:
        try:
            fn(vf)
        except Exception as e:
            print(f"  🔴 La familia {fn.__name__} no se pudo verificar: {e}")

    generar_informe(vf.resultados, f"{args.salida}.md", f"{args.salida}.json")
    fallas = sum(1 for r in vf.resultados if r.estado == "FALLA")
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())
