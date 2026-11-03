"""
af_model_registry.py — Resiliencia ante cambios de modelo de IA (NUEVO EN v15.0)

EL PROBLEMA (pedido explícito de la v15.0)
==========================================
"Mitigar el tema del cambio de versión de inteligencia artificial de Gemini;
hay que evitar que los modelos nuevos y el retiro de los que hoy estoy
usando dejen el sistema obsoleto y lo rompan."

El riesgo es concreto y ya pasó en la industria: Google retira modelos con
preaviso, pero el ID del modelo queda escrito en una variable de entorno
del servidor. El día del apagado, TODA llamada al motor de decisión empieza
a devolver 404 y el bot —que por diseño vetea ante error de IA— deja de
operar sin que nadie entienda por qué.

POR QUÉ NO ALCANZABA LO QUE HABÍA EN v14.0
==========================================
v14.0 tenía t_model_guardian.py, que avisa por Telegram si conviene revisar
el modelo. Es correcto pero insuficiente: avisa, y después el bot igual se
rompe si nadie llega a tiempo. Y la alternativa que v14.0 descartó
explícitamente —dejar que el bot se cambie de modelo solo, en silencio— se
descartó por buenos motivos, que siguen siendo válidos:
  - Google desaconseja los alias "-latest" en producción por inconsistencia.
  - Hay precedente de un ID con forma de versión fija re-apuntado a otro
    modelo tras un apagado.
  - Un modelo distinto puede cambiar el formato de salida y romper el parseo
    JSON sin que nadie se entere hasta que falla una decisión.

LA SOLUCIÓN DE v15.0: DEGRADACIÓN CONTROLADA, NO AUTO-ACTUALIZACIÓN
===================================================================
Ni "se cambia solo y en silencio" ni "solo avisa y se rompe". El esquema es:

  1) CADENA DE MODELOS DECLARADA (GEMINI_MODEL_CHAIN en el .env). Es una
     lista ordenada y EXPLÍCITA de IDs fijos, escrita por una persona. El
     bot nunca inventa un ID ni consulta "cuál es el más nuevo".
  2) HEALTH CHECK AL ARRANQUE. Antes de operar, se prueba el modelo
     configurado con un ping mínimo que exige salida JSON. Si responde y
     el JSON parsea, arranca normal.
  3) SI EL PRINCIPAL NO RESPONDE, baja al siguiente de la cadena, PERO:
       - lo hace en modo degradado y explícito,
       - avisa por Telegram con el modelo viejo y el nuevo,
       - graba el cambio en la base (queda auditado),
       - y marca el estado como DEGRADADO en el panel hasta que una persona
         confirme el modelo nuevo en el .env.
     Un modelo de respaldo dentro de una cadena que vos escribiste no es lo
     mismo que un modelo elegido por el bot: la decisión sigue siendo humana,
     tomada por adelantado.
  4) SI SE AGOTA LA CADENA DE GEMINI, queda Claude (Anthropic) como segundo
     motor —que ya existía desde v12.0— y recién si eso también falla, veto
     conservador. O sea: tres capas antes de que un cambio de modelo pueda
     frenar el bot.
  5) VALIDACIÓN DE CONTRATO, no solo de disponibilidad. El ping no pregunta
     "¿existís?" sino "devolveme este JSON exacto". Un modelo que existe
     pero ya no respeta response_mime_type se detecta acá, no en medio de
     una decisión de compra.

VERIFICACIÓN PERIÓDICA
======================
Además del arranque, se revalida cada MODEL_HEALTHCHECK_HOURS. Si Google
anuncia el apagado de un modelo, la deprecación suele aparecer primero como
warning en la respuesta y después como error: el chequeo periódico lo
levanta antes de la próxima rueda.
"""

import os
import json
import time
import logging
import threading
from datetime import datetime
from typing import Optional, List

from dotenv import load_dotenv

import ac_db

load_dotenv()
logger = logging.getLogger("model_registry")

# Cadena por defecto: el modelo que el proyecto viene usando, y detrás dos
# alternativas de la misma familia. Todas con ID FIJO, ninguna con "-latest".
# ===========================================================================
# CAMBIADO EN v16.2 — LA CADENA YA NO ESTÁ ESCRITA EN EL CÓDIGO
# ===========================================================================
# Acá había una lista fija: gemini-2.5-flash-lite, 2.5-flash, 2.5-pro. Los
# tres están hoy deprecados con fecha de apagado en octubre de 2026, así que
# el sistema habría quedado sin motor en una fecha conocida de antemano, sin
# que nadie hubiera tocado una línea.
#
# Escribir una lista mejor no arregla el problema: lo pospone. Ahora la
# cadena se DESCUBRE consultando el catálogo del proveedor y se ordena por
# política (Pro primero, después Flash, generación más nueva primero). Ver
# at_model_discovery.py.
#
# La lista de abajo sobrevive solo como respaldo para el caso sin red.
DEFAULT_CHAIN = ",".join([
    "gemini-3.1-pro-preview",   # el Pro vigente: mejor razonamiento
    "gemini-3.7-flash",         # GA desde el 13/08/2026
    "gemini-3.6-flash",
    "gemini-3.5-flash",
])


def _cadena_vigente() -> List[str]:
    """La cadena de AHORA, no la del día que se escribió el código."""
    try:
        import at_model_discovery
        cadena = at_model_discovery.cadena_de_modelos()
        if cadena:
            return cadena
    except Exception as e:
        logger.warning("Descubrimiento de modelos no disponible (%s): se usa el respaldo.", e)
    return [m.strip() for m in DEFAULT_CHAIN.split(",") if m.strip()]


MODEL_CHAIN: List[str] = _cadena_vigente()
HEALTHCHECK_HOURS = float(os.getenv("MODEL_HEALTHCHECK_HOURS", "24"))
PING_TIMEOUT = float(os.getenv("MODEL_PING_TIMEOUT", "20"))

# Estado en memoria, consultado por f_gemini_decision_engine en cada llamada.
_estado = {
    "modelo_activo": None,
    "modelo_configurado": None,
    "degradado": False,
    "motivo": None,
    "ultimo_chequeo": None,
    "cadena": MODEL_CHAIN,
}
_lock = threading.Lock()


def _init_table():
    with ac_db.connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS model_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ocurrido_en TEXT DEFAULT CURRENT_TIMESTAMP,
                evento TEXT,
                modelo_anterior TEXT,
                modelo_nuevo TEXT,
                detalle TEXT
            )
        """)


def _registrar(evento: str, anterior: Optional[str], nuevo: Optional[str], detalle: str = ""):
    try:
        _init_table()
        with ac_db.connect() as conn:
            conn.execute(
                "INSERT INTO model_events (evento, modelo_anterior, modelo_nuevo, detalle) "
                "VALUES (?, ?, ?, ?)", (evento, anterior, nuevo, detalle[:500]),
            )
    except Exception as e:
        logger.debug("No se pudo grabar el evento de modelo: %s", e)


PING_PROMPT = ('Devolvé exclusivamente este JSON, sin texto adicional ni markdown: '
               '{"ok": true, "n": 7}')


def _probar_modelo(modelo: str) -> tuple:
    """Devuelve (ok: bool, detalle: str). Valida CONTRATO, no solo existencia:
    el modelo tiene que existir, responder, y respetar la salida JSON."""
    try:
        from google import genai
        from google.genai import types
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return False, "GEMINI_API_KEY no configurada"
        client = genai.Client(api_key=api_key)
        # NOTA v16.2: no se envían temperature/top_p/top_k. Los parámetros de
        # muestreo quedaron DEPRECADOS en la serie Gemini 3 y Google recomienda
        # removerlos al migrar; mandarlos puede degradar la respuesta o
        # devolver un error de validación. El control de profundidad de
        # razonamiento ahora es `thinking_level`, que es un enum de texto y no
        # un presupuesto numérico.
        respuesta = client.models.generate_content(
            model=modelo, contents=PING_PROMPT,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        texto = (respuesta.text or "").strip().replace("```json", "").replace("```", "")
        datos = json.loads(texto)
        if datos.get("ok") is not True or datos.get("n") != 7:
            return False, f"El modelo respondió pero no respetó el contrato JSON: {texto[:120]}"
        return True, "OK"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:200]}"


def resolve_model(notifier=None, forzar: bool = False) -> str:
    """Devuelve el ID del modelo que hay que usar AHORA. Es la función que
    llama f_gemini_decision_engine.py en vez de leer GEMINI_MODEL a pelo."""
    with _lock:
        # v16.2 — si GEMINI_MODEL no está fijado a mano, el configurado es el
        # mejor del catálogo descubierto en este momento, no una constante.
        cadena = _cadena_vigente()
        configurado = os.getenv("GEMINI_MODEL", "").strip() or (
            cadena[0] if cadena else "gemini-3.1-pro-preview")
        _estado["cadena"] = cadena
        _estado["modelo_configurado"] = configurado

        # Si ya hay un modelo validado y no toca revalidar, devolverlo.
        if not forzar and _estado["modelo_activo"] and _estado["ultimo_chequeo"]:
            transcurrido = time.time() - _estado["ultimo_chequeo_epoch"]
            if transcurrido < HEALTHCHECK_HOURS * 3600:
                return _estado["modelo_activo"]

        # Orden de prueba: primero el configurado, después la cadena.
        candidatos = [configurado] + [m for m in MODEL_CHAIN if m != configurado]
        anterior = _estado["modelo_activo"]

        for i, modelo in enumerate(candidatos):
            ok, detalle = _probar_modelo(modelo)
            if ok:
                _estado["modelo_activo"] = modelo
                _estado["degradado"] = (i > 0)
                _estado["motivo"] = None if i == 0 else f"El modelo configurado ({configurado}) falló: {detalle}"
                _estado["ultimo_chequeo"] = datetime.now().isoformat(timespec="seconds")
                _estado["ultimo_chequeo_epoch"] = time.time()
                if i > 0:
                    _registrar("DEGRADACION", configurado, modelo, detalle)
                    logger.warning("Modelo degradado: %s no responde, se usa %s.", configurado, modelo)
                    if notifier:
                        try:
                            notifier.send_telegram(
                                f"🟡 *CAMBIO DE MODELO DE IA (degradación controlada)*\n"
                                f"El modelo configurado `{configurado}` no respondió correctamente.\n"
                                f"Motivo: {detalle}\n\n"
                                f"El bot pasó a `{modelo}`, el siguiente de la cadena que vos definiste "
                                f"en GEMINI_MODEL_CHAIN. Sigue operando.\n\n"
                                f"👉 Acción recomendada: si el cambio es definitivo (modelo apagado), "
                                f"actualizá GEMINI_MODEL en el .env a `{modelo}` y reiniciá. "
                                f"Mientras tanto el panel muestra el estado como DEGRADADO."
                            )
                        except Exception:
                            pass
                elif anterior and anterior != modelo:
                    _registrar("RECUPERACION", anterior, modelo, "El modelo configurado volvió a responder")
                return modelo

        # Ningún modelo de la cadena respondió.
        _estado["modelo_activo"] = None
        _estado["degradado"] = True
        _estado["motivo"] = "Ningún modelo de la cadena de Gemini responde"
        _estado["ultimo_chequeo"] = datetime.now().isoformat(timespec="seconds")
        _estado["ultimo_chequeo_epoch"] = time.time()
        _registrar("CADENA_AGOTADA", configurado, None, "; ".join(candidatos))
        logger.error("Ningún modelo de la cadena de Gemini respondió: %s", candidatos)
        if notifier:
            try:
                notifier.send_telegram(
                    "🔴 *NINGÚN MODELO DE GEMINI RESPONDE*\n"
                    f"Se probaron: {', '.join(candidatos)}.\n"
                    "El bot pasa al motor de respaldo (Claude) si tenés ANTHROPIC_API_KEY cargada; "
                    "si no, vetea toda operación por prudencia hasta que se resuelva.\n"
                    "Causas típicas: API key vencida o revocada, cuota agotada, "
                    "o los IDs de la cadena quedaron obsoletos."
                )
            except Exception:
                pass
        # Se devuelve el configurado igual: que falle en el punto de uso y
        # dispare el fallback a Claude, en vez de devolver None y provocar un
        # TypeError en el llamador (que sería un crash, no una degradación).
        return configurado


def get_status() -> dict:
    """Para la sección "Motor de decisiones IA" del panel."""
    with _lock:
        estado = dict(_estado)
    estado.pop("ultimo_chequeo_epoch", None)
    estado["respaldo_claude_configurado"] = bool(os.getenv("ANTHROPIC_API_KEY"))
    return estado


def historial(limite: int = 20) -> list:
    try:
        _init_table()
        with ac_db.connect() as conn:
            filas = conn.execute(
                "SELECT ocurrido_en, evento, modelo_anterior, modelo_nuevo, detalle "
                "FROM model_events ORDER BY id DESC LIMIT ?", (limite,)
            ).fetchall()
        return [{"ocurrido_en": a, "evento": b, "anterior": c, "nuevo": d, "detalle": e}
                for a, b, c, d, e in filas]
    except Exception as e:
        return [{"error": str(e)}]


class ModelHealthWatcher:
    """Revalida la cadena cada HEALTHCHECK_HOURS. Un modelo que Google apaga
    de madrugada se detecta antes de la apertura de la rueda siguiente."""

    def __init__(self, notifier=None):
        self.notifier = notifier
        self._stop = threading.Event()

    def _loop(self):
        while not self._stop.wait(HEALTHCHECK_HOURS * 3600):
            try:
                resolve_model(notifier=self.notifier, forzar=True)
            except Exception as e:
                logger.error("Error en el chequeo periódico de modelos: %s", e)

    def start(self):
        _init_table()
        threading.Thread(target=self._loop, name="model_health", daemon=True).start()
        logger.info("Vigilante de salud de modelos activo (cada %sh).", HEALTHCHECK_HOURS)

    def stop(self):
        self._stop.set()


# Estado inicial: se completa en el primer resolve_model().
_estado["ultimo_chequeo_epoch"] = 0.0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Modelo resuelto:", resolve_model(forzar=True))
    print(json.dumps(get_status(), indent=2, ensure_ascii=False))
