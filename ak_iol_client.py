"""
ak_iol_client.py — Conector con la API de Invertir Online (IOL)

QUÉ APORTA IOL QUE NO TENÍAMOS
---------------------------------------------------------------------------
IOL cubre un hueco concreto: las series históricas de precios instrumento
por instrumento. Hasta ahora el sistema tenía historia MACRO muy sólida
(inflación, dólar, reservas, tasa) pero para el histórico de PRECIOS
dependía de dos fuentes flojas: lo que devolviera PPI en el momento, que no
está pensado como archivo histórico, y yfinance, que para el mercado local
entrega precios demorados y no cubre bonos ni opciones.

IOL publica en su documentación oficial que expone cotizaciones en tiempo
real y series históricas de acciones, bonos, opciones, cauciones, futuros,
monedas y cheques de pago diferido del mercado argentino. Eso incluye
exactamente los dos tipos que la Fase 1 dejó bloqueados por falta de datos.
La cuenta comitente y el acceso a la API no tienen costo de apertura ni
mantenimiento.

SEGUNDA UTILIDAD, MENOS OBVIA Y QUIZÁS MÁS VALIOSA
---------------------------------------------------------------------------
El endpoint /api/v2/operar/estimar simula una orden y devuelve el desglose
de aranceles, derechos de mercado e IVA ANTES de mandarla. El sistema hoy
calcula esos costos con una tabla propia (d_economics.py). Tener una segunda
fuente independiente permite reconciliar: si el costo estimado por nuestra
tabla y el que informa un bróker real difieren de forma sistemática, eso es
una alerta accionable sobre la tabla, no un misterio que aparece recién al
cerrar la operación. Es la única forma de auditar el modelo de costos sin
gastar plata real.

LO QUE NO SE HACE, Y POR QUÉ
---------------------------------------------------------------------------
IOL NO se conecta como segundo canal de EJECUCIÓN. Este módulo consulta;
nunca manda una orden. Dos brókers ejecutando contra el mismo capital es una
fuente de descuadres de posición que ningún beneficio de redundancia
justifica: si los dos creen tener la posición abierta, los stop-loss se
pisan. El bróker de ejecución sigue siendo PPI, solo y sin ambigüedad.

ESTADO: el módulo está completo pero arranca DESACTIVADO (IOL_ENABLED=false
por default) hasta que existan credenciales. Ninguna llamada de este archivo
se ejecuta sin ese interruptor en true, así que el sistema funciona igual
mientras la cuenta no esté abierta.
"""

import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("iol_client")

IOL_ENABLED = os.getenv("IOL_ENABLED", "false").lower() == "true"
IOL_BASE_URL = os.getenv("IOL_BASE_URL", "https://api.invertironline.com")
IOL_TIMEOUT = float(os.getenv("IOL_TIMEOUT_SECONDS", "12"))
IOL_RATE_LIMIT_SLEEP = float(os.getenv("IOL_RATE_LIMIT_SLEEP", "0.25"))

# El endpoint de estimación pertenece a la superficie de operación de IOL y
# requiere opt-in manual explícito; no forma parte de lecturas automáticas.
IOL_COST_ESTIMATE_OPT_IN_ENV = "IOL_COST_ESTIMATE_EXPLICIT_OPT_IN"

# El token de IOL vive unos minutos y viene con refresh_token. Se renueva con
# margen: pedir un token nuevo es barato, comerse un 401 en el medio de una
# descarga de 400 instrumentos no lo es.
TOKEN_REFRESH_MARGIN_SECONDS = 120


class IOLClient:
    """Cliente de solo lectura contra la API de IOL.

    Deliberadamente NO expone ningún método de envío de órdenes. Si en el
    futuro alguien quiere agregarlo, que sea una decisión explícita y
    discutida, no algo que quedó disponible porque el cliente lo heredaba.
    """

    def __init__(self, username: Optional[str] = None, password: Optional[str] = None):
        self.username = username or os.getenv("IOL_USERNAME", "")
        self.password = password or os.getenv("IOL_PASSWORD", "")
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._expires_at: float = 0.0
        self._lock = threading.Lock()
        self._last_call = 0.0
        self.enabled = IOL_ENABLED and bool(self.username and self.password)
        if IOL_ENABLED and not self.enabled:
            logger.warning("IOL_ENABLED=true pero faltan IOL_USERNAME o IOL_PASSWORD. "
                           "El conector queda inactivo en lugar de fallar en cada llamada.")

    # -- autenticación -----------------------------------------------------

    def _authenticate(self) -> bool:
        payload = {"username": self.username, "password": self.password,
                   "grant_type": "password"}
        return self._token_request(payload, "login")

    def _refresh(self) -> bool:
        if not self._refresh_token:
            return self._authenticate()
        payload = {"refresh_token": self._refresh_token, "grant_type": "refresh_token"}
        if self._token_request(payload, "refresh"):
            return True
        # Si el refresh falla —token revocado, sesión caída del otro lado— se
        # vuelve al login completo en vez de quedar sin sesión.
        logger.info("El refresh de IOL falló; se reintenta con login completo.")
        return self._authenticate()

    def _token_request(self, payload: dict, label: str) -> bool:
        # La documentación oficial de IOL ubica el token en /api/v2/token, pero
        # buena parte de los wrappers públicos usan /token. No se puede saber
        # cuál responde sin probar, así que se intenta el documentado primero y
        # se cae al alternativo solo ante un 404. Adivinar uno de los dos y
        # fallar sin explicación sería peor que hacer los dos intentos.
        rutas = ["/api/v2/token", "/token"]
        r = None
        for ruta in rutas:
            try:
                r = requests.post(f"{IOL_BASE_URL}{ruta}", data=payload,
                                  headers={"Content-Type": "application/x-www-form-urlencoded"},
                                  timeout=IOL_TIMEOUT)
            except requests.RequestException as e:
                logger.error("IOL %s en %s: fallo de red: %s", label, ruta, e)
                continue
            if r.status_code != 404:
                if ruta != rutas[0]:
                    logger.info("IOL: el token responde en %s, no en el documentado.", ruta)
                break

        if r is None:
            return False

        if r.status_code != 200:
            # El cuerpo de la respuesta puede traer el usuario; no se loguea.
            logger.error("IOL %s rechazado con HTTP %s.", label, r.status_code)
            return False

        data = r.json()
        self._access_token = data.get("access_token")
        self._refresh_token = data.get("refresh_token") or self._refresh_token
        expires_in = int(data.get("expires_in", 1200))  # IOL documenta 20 minutos
        self._expires_at = time.time() + expires_in
        logger.info("IOL %s exitoso; token válido por %d segundos.", label, expires_in)
        return bool(self._access_token)

    def _headers(self) -> Optional[dict]:
        with self._lock:
            if not self.enabled:
                return None
            if not self._access_token or time.time() > self._expires_at - TOKEN_REFRESH_MARGIN_SECONDS:
                if not self._refresh():
                    return None
            return {"Authorization": f"Bearer {self._access_token}"}

    # -- transporte --------------------------------------------------------

    def _get(self, path: str, label: str) -> Optional[Any]:
        """GET con autenticación, límite de tasa y un solo reintento ante 401.

        El límite de tasa es autoimpuesto y conservador. La documentación de
        IOL no publica un número explícito de peticiones por segundo, así que
        se elige un ritmo suave: en una descarga masiva de históricos, ganar
        unos minutos no justifica arriesgar un bloqueo de la cuenta.
        """
        headers = self._headers()
        if not headers:
            return None

        espera = IOL_RATE_LIMIT_SLEEP - (time.time() - self._last_call)
        if espera > 0:
            time.sleep(espera)

        url = f"{IOL_BASE_URL}{path}"
        try:
            r = requests.get(url, headers=headers, timeout=IOL_TIMEOUT)
            self._last_call = time.time()
            if r.status_code == 401:
                logger.info("IOL devolvió 401 en %s; renovando token y reintentando una vez.", label)
                with self._lock:
                    self._expires_at = 0
                headers = self._headers()
                if not headers:
                    return None
                r = requests.get(url, headers=headers, timeout=IOL_TIMEOUT)
                self._last_call = time.time()
            if r.status_code == 429:
                logger.warning("IOL respondió 429 (límite de tasa) en %s. Se corta la ronda actual.", label)
                return None
            if r.status_code != 200:
                logger.warning("IOL %s devolvió HTTP %s.", label, r.status_code)
                return None
            return r.json()
        except requests.RequestException as e:
            logger.warning("IOL %s: fallo de red: %s", label, e)
            return None

    # -- consultas ---------------------------------------------------------

    def get_panel(self, instrumento: str = "acciones", panel: str = "lideres",
                  pais: str = "argentina") -> List[str]:
        """Lista de símbolos de un panel. Es el punto de partida para saber
        qué instrumentos existen sin tener que adivinar tickers."""
        data = self._get(f"/api/v2/Cotizaciones/{instrumento}/{panel}/{pais}",
                         f"panel {instrumento}/{panel}")
        if not isinstance(data, dict):
            return []
        return [t.get("simbolo") for t in data.get("titulos", []) if t.get("simbolo")]

    def get_serie_historica(self, simbolo: str, mercado: str = "bcba",
                            dias: int = 365, ajustada: bool = True) -> List[dict]:
        """
        Serie diaria OHLCV.

        `ajustada=True` pide la serie corregida por splits y dividendos, y esto
        no es un detalle: una serie sin ajustar tiene saltos artificiales el día
        del split que cualquier indicador técnico lee como un derrumbe o un
        salto real. Un backtest sobre series sin ajustar produce resultados que
        parecen buenos y no significan nada.
        """
        hasta = datetime.now().strftime("%Y-%m-%d")
        desde = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d")
        ruta = (f"/api/v2/{mercado}/Titulos/{simbolo}/Cotizacion/seriehistorica/"
                f"{desde}/{hasta}/{'true' if ajustada else 'false'}")
        data = self._get(ruta, f"histórico {simbolo}")
        if isinstance(data, dict):
            return data.get("precios", []) or []
        if isinstance(data, list):
            return data
        return []

    def get_cotizacion(self, simbolo: str, mercado: str = "bcba") -> Optional[dict]:
        """Snapshot con puntas de compra y venta. Se usa SOLO como validación
        cruzada del precio de PPI, nunca para dimensionar una orden."""
        return self._get(f"/api/v2/{mercado}/Titulos/{simbolo}/Cotizacion",
                         f"cotización {simbolo}")

    def get_estado_cuenta(self) -> Optional[dict]:
        """Liquidez y valuación total de la cuenta.

        OPORTUNIDAD APROVECHADA: además del disponible inmediato, este endpoint
        informa la liquidez a 24 y 48 horas. El bot dimensionaba contra el saldo
        del momento, que puede incluir plata todavía comprometida en órdenes o
        en liquidación. Saber cuánto va a estar disponible REALMENTE al momento
        de liquidar evita dimensionar contra capital que no está.
        """
        return self._get("/api/v2/estadocuenta", "estado de cuenta")

    def get_portafolio(self, pais: str = "argentina") -> Optional[dict]:
        """Tenencias con precio promedio de compra y resultado no realizado.

        OPORTUNIDAD APROVECHADA: devuelve también las OPCIONES en cartera. Es
        una segunda fuente independiente para reconciliar posiciones de
        derivados, que es justo donde un descuadre se descubre tarde y caro.
        """
        return self._get(f"/api/v2/portafolio/{pais}", "portafolio")

    def get_estado_operacion(self, numero: int) -> Optional[dict]:
        """Seguimiento del ciclo de vida de una orden: iniciada, en proceso,
        ejecutada, cancelada o rechazada. Solo consulta; este cliente nunca
        envía ni cancela órdenes."""
        return self._get(f"/api/v2/operaciones/{numero}", f"operación {numero}")

    def estimar_operacion(self, simbolo: str, cantidad: int, precio: float,
                          mercado: str = "bcba", tipo: str = "Compra") -> Optional[dict]:
        """
        Simulación de orden: devuelve el desglose de costos sin ejecutar nada.

        Es la pieza que permite auditar el modelo de comisiones propio contra
        un bróker real. Ver reconciliar_costos() más abajo.
        """
        habilitado = os.getenv(IOL_COST_ESTIMATE_OPT_IN_ENV, "false").strip().lower()             in {"1", "true", "yes", "on"}
        if not habilitado:
            logger.info(
                "IOL estimar omitido: requiere %s=true en una corrida manual explícita.",
                IOL_COST_ESTIMATE_OPT_IN_ENV,
            )
            return None
        headers = self._headers()
        if not headers:
            return None
        cuerpo = {"mercado": mercado, "simbolo": simbolo, "cantidad": cantidad,
                  "precio": precio, "tipo": tipo}
        try:
            r = requests.post(f"{IOL_BASE_URL}/api/v2/operar/estimar",
                              json=cuerpo, headers=headers, timeout=IOL_TIMEOUT)
            if r.status_code != 200:
                logger.info("IOL estimar devolvió HTTP %s para %s.", r.status_code, simbolo)
                return None
            return r.json()
        except requests.RequestException as e:
            logger.warning("IOL estimar: fallo de red: %s", e)
            return None


def reconciliar_costos(estimacion_iol: dict, costo_propio_ars: float,
                       tolerancia_pct: float = 15.0) -> dict:
    """
    Compara lo que cobra un bróker real contra lo que predice d_economics.py.

    Aclaración importante para leer bien el resultado: IOL y PPI no tienen por
    qué cobrar lo mismo, así que una diferencia NO significa que la tabla
    propia esté mal. Lo que se busca es otra cosa: un desvío grande y
    ESTABLE en el tiempo, que es la firma de un componente de costo que
    directamente no está contemplado en el modelo (un impuesto provincial, un
    derecho de mercado con otra base de cálculo). Por eso el veredicto habla
    de "revisar", no de "corregir": es una señal para mirar, no un fallo.
    """
    if not estimacion_iol:
        return {"comparable": False, "motivo": "IOL no devolvió estimación."}

    total_iol = (estimacion_iol.get("montoComision", 0)
                 + estimacion_iol.get("montoDerechosMercado", 0)
                 + estimacion_iol.get("montoIVA", 0))
    if total_iol <= 0 or costo_propio_ars <= 0:
        return {"comparable": False, "motivo": "Alguno de los dos costos vino en cero."}

    desvio_pct = (costo_propio_ars - total_iol) / total_iol * 100
    return {
        "comparable": True,
        "costo_modelo_propio_ars": round(costo_propio_ars, 2),
        "costo_broker_referencia_ars": round(total_iol, 2),
        "desvio_pct": round(desvio_pct, 2),
        "dentro_de_tolerancia": abs(desvio_pct) <= tolerancia_pct,
        "veredicto": ("Coherente" if abs(desvio_pct) <= tolerancia_pct else
                      "Desvío por encima de la tolerancia: revisar si falta algún componente "
                      "de costo en el modelo propio. No implica que esté mal: dos brókers "
                      "cobran distinto. Lo que importa es si el desvío se repite."),
    }
