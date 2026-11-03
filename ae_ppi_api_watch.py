"""
ae_ppi_api_watch.py — Vigilancia de cambios en la API de PPI (NUEVO EN v15.0)

EL PROBLEMA (pedido explícito de la v15.0)
==========================================
"Mitigar el problema de la API de PPI que pueda llegar a cambiar sin previo
aviso; implementar algo que monitoree constantemente los cambios de la API,
ya sea en GitHub o mediante algún otro canal."

Es un riesgo real y de los más caros: PPI no publica un changelog con
versionado semántico ni manda avisos de deprecación. El bot depende de tres
superficies que pueden moverse sin que nadie nos avise:

  1) La librería oficial `ppi-client` (PyPI). Si PPI publica una versión
     nueva con un cambio de firma, `pip install -U` en un rebuild puede
     romper el bot en el próximo deploy sin que hayamos tocado una línea.
  2) La documentación oficial (itatppi.github.io/ppi-official-api-docs).
     Es un GitHub Pages, o sea que TIENE historial de commits: si el repo
     cambia, casi siempre es porque la API cambió.
  3) El comportamiento observable de los endpoints: que un campo que
     siempre venía deje de venir, o aparezca uno nuevo.

CÓMO SE VIGILA CADA UNA (tres canales independientes)
=====================================================
  A) PyPI JSON API — https://pypi.org/pypi/ppi-client/json
     Compara la última versión publicada contra la que está instalada.
     Es el canal más confiable y el que da más tiempo de reacción.
  B) GitHub API — commits del repo de documentación oficial.
     Guarda el SHA del último commit visto; si cambia, avisa con la fecha
     y el mensaje del commit.
  C) FIRMA DE ESQUEMA (schema drift) — la más importante de las tres, y la
     que ninguna de las otras dos detecta: cada vez que el bot recibe una
     respuesta real de un endpoint crítico, se calcula un hash del CONJUNTO
     DE CLAVES de la respuesta (no de los valores). Si mañana PPI renombra
     "filledQuantity" a "quantityExecuted", el hash cambia y el bot avisa
     ANTES de que el cambio se traduzca en una posición mal contabilizada.
     Esto detecta cambios que no se anuncian en ningún lado.

QUÉ HACE Y QUÉ NO HACE
======================
AVISA. No se actualiza solo, no cambia el código, no toca la versión de la
librería. Un bot de trading no debe auto-actualizar la librería que le
manda órdenes al bróker: la mitigación correcta ante "la API puede cambiar
sin aviso" es enterarse temprano y decidir, no aplicar el cambio a ciegas.
Por eso requirements.txt pasa a fijar la versión exacta de ppi-client en
v15.0 (antes estaba abierta) y este módulo avisa cuando hay una nueva.
"""

import os
import json
import time
import hashlib
import logging
import threading
from datetime import datetime
from typing import Optional, Dict, Any

import requests
from dotenv import load_dotenv

import ac_db

load_dotenv()
logger = logging.getLogger("ppi_api_watch")

WATCH_ENABLED = os.getenv("PPI_API_WATCH_ENABLED", "true").lower() == "true"
CHECK_INTERVAL_HOURS = float(os.getenv("PPI_API_WATCH_INTERVAL_HOURS", "12"))
HTTP_TIMEOUT = float(os.getenv("PPI_API_WATCH_TIMEOUT", "10"))

PYPI_URL = "https://pypi.org/pypi/ppi-client/json"
DOCS_REPO_COMMITS = "https://api.github.com/repos/itatppi/ppi-official-api-docs/commits"

# Endpoints cuya forma de respuesta importa de verdad: si cambian, el bot
# calcula mal plata o estado de órdenes.
ENDPOINTS_CRITICOS = ("market_data", "order_detail", "confirm_order", "account_balance",
                      "book", "portfolio")


def _init_table():
    with ac_db.connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ppi_api_watch (
                canal TEXT NOT NULL,
                clave TEXT NOT NULL,
                valor TEXT,
                primera_vez TEXT DEFAULT CURRENT_TIMESTAMP,
                ultima_vez TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (canal, clave)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ppi_api_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                detectado_en TEXT DEFAULT CURRENT_TIMESTAMP,
                canal TEXT,
                clave TEXT,
                valor_anterior TEXT,
                valor_nuevo TEXT,
                notificado INTEGER DEFAULT 0
            )
        """)


def _leer_estado(canal: str, clave: str) -> Optional[str]:
    with ac_db.connect() as conn:
        row = conn.execute(
            "SELECT valor FROM ppi_api_watch WHERE canal = ? AND clave = ?", (canal, clave)
        ).fetchone()
    return row[0] if row else None


def _guardar_estado(canal: str, clave: str, valor: str):
    with ac_db.connect() as conn:
        conn.execute(
            "INSERT INTO ppi_api_watch (canal, clave, valor) VALUES (?, ?, ?) "
            "ON CONFLICT(canal, clave) DO UPDATE SET valor = excluded.valor, "
            "ultima_vez = CURRENT_TIMESTAMP",
            (canal, clave, valor),
        )


def _registrar_cambio(canal: str, clave: str, anterior: str, nuevo: str):
    with ac_db.connect() as conn:
        conn.execute(
            "INSERT INTO ppi_api_changes (canal, clave, valor_anterior, valor_nuevo) "
            "VALUES (?, ?, ?, ?)", (canal, clave, anterior, nuevo),
        )
    logger.warning("CAMBIO DETECTADO en la API de PPI [%s/%s]: %s -> %s",
                   canal, clave, anterior, nuevo)


# ============================================================================
# Canal C — firma de esquema (se alimenta desde c_ppi_client en caliente)
# ============================================================================
def _firma_de_claves(obj: Any, prefijo: str = "", profundidad: int = 0) -> list:
    """Camino de claves, ordenado y sin valores. Se limita la profundidad para
    que un book con 50 niveles no genere una firma gigante."""
    if profundidad > 3:
        return []
    claves = []
    if isinstance(obj, dict):
        for k in sorted(obj.keys()):
            ruta = f"{prefijo}.{k}" if prefijo else str(k)
            claves.append(ruta)
            claves.extend(_firma_de_claves(obj[k], ruta, profundidad + 1))
    elif isinstance(obj, list) and obj:
        claves.extend(_firma_de_claves(obj[0], f"{prefijo}[]", profundidad + 1))
    return claves


def registrar_respuesta(endpoint: str, respuesta: Any, notifier=None):
    """Se llama desde c_ppi_client.py con la respuesta cruda de un endpoint
    crítico. Barato: hash de una lista de strings, sin red y sin bloquear.
    Si el esquema cambió respecto de la última vez, deja el cambio grabado y
    avisa una sola vez (no en cada llamada)."""
    if not WATCH_ENABLED or endpoint not in ENDPOINTS_CRITICOS:
        return
    try:
        claves = _firma_de_claves(respuesta)
        if not claves:
            return
        firma = hashlib.sha256("|".join(claves).encode()).hexdigest()[:16]
        anterior = _leer_estado("schema", endpoint)
        if anterior is None:
            _guardar_estado("schema", endpoint, firma)
            # Se guarda también la lista legible, para poder diffear después.
            _guardar_estado("schema_keys", endpoint, json.dumps(claves)[:4000])
            return
        if anterior != firma:
            claves_viejas = set(json.loads(_leer_estado("schema_keys", endpoint) or "[]"))
            nuevas = sorted(set(claves) - claves_viejas)
            faltantes = sorted(claves_viejas - set(claves))
            detalle = f"campos nuevos: {nuevas or 'ninguno'} | campos que desaparecieron: {faltantes or 'ninguno'}"
            _registrar_cambio("schema", endpoint, anterior, f"{firma} ({detalle})")
            _guardar_estado("schema", endpoint, firma)
            _guardar_estado("schema_keys", endpoint, json.dumps(claves)[:4000])
            if notifier:
                gravedad = "🔴" if faltantes else "🟡"
                try:
                    notifier.send_telegram(
                        f"{gravedad} *CAMBIO EN LA API DE PPI*\n"
                        f"Endpoint: `{endpoint}`\n{detalle}\n\n"
                        + ("Desaparecieron campos que el bot usa: revisá antes de la próxima rueda."
                           if faltantes else
                           "Solo se agregaron campos: el bot sigue funcionando, pero conviene mirarlo.")
                    )
                except Exception:
                    pass
    except Exception as e:
        logger.debug("No se pudo registrar la firma de %s: %s", endpoint, e)


# ============================================================================
# Canales A y B — chequeo periódico contra internet
# ============================================================================
def _version_instalada() -> Optional[str]:
    try:
        from importlib.metadata import version
        return version("ppi-client")
    except Exception:
        return None


def check_pypi(notifier=None) -> Optional[dict]:
    try:
        r = requests.get(PYPI_URL, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        ultima = r.json()["info"]["version"]
        instalada = _version_instalada()
        anterior = _leer_estado("pypi", "ppi-client")
        _guardar_estado("pypi", "ppi-client", ultima)
        if anterior and anterior != ultima:
            _registrar_cambio("pypi", "ppi-client", anterior, ultima)
            if notifier:
                notifier.send_telegram(
                    f"🟡 *NUEVA VERSIÓN DE ppi-client*\n"
                    f"PyPI publicó la {ultima} (el bot tiene fijada la {instalada}).\n"
                    "El bot NO se actualiza solo, a propósito. Revisá el changelog antes "
                    "de subir la versión en requirements.txt."
                )
        return {"ultima_pypi": ultima, "instalada": instalada,
                "desactualizada": bool(instalada and ultima != instalada)}
    except Exception as e:
        logger.warning("No se pudo consultar PyPI: %s", e)
        return None


def check_docs(notifier=None) -> Optional[dict]:
    try:
        r = requests.get(DOCS_REPO_COMMITS, params={"per_page": 1},
                         timeout=HTTP_TIMEOUT,
                         headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
        commits = r.json()
        if not commits:
            return None
        sha = commits[0]["sha"][:10]
        mensaje = (commits[0].get("commit", {}).get("message") or "")[:200]
        fecha = commits[0].get("commit", {}).get("author", {}).get("date")
        anterior = _leer_estado("docs", "last_commit")
        _guardar_estado("docs", "last_commit", sha)
        if anterior and anterior != sha:
            _registrar_cambio("docs", "last_commit", anterior, f"{sha} — {mensaje}")
            if notifier:
                notifier.send_telegram(
                    f"🟡 *LA DOCUMENTACIÓN DE PPI CAMBIÓ*\n"
                    f"Commit nuevo ({fecha}): {mensaje}\n"
                    "Suele indicar un cambio real en la API. Vale la pena leerlo."
                )
        return {"ultimo_commit": sha, "fecha": fecha, "mensaje": mensaje}
    except Exception as e:
        logger.warning("No se pudo consultar el repo de documentación de PPI: %s", e)
        return None


def check_all(notifier=None) -> dict:
    _init_table()
    return {"pypi": check_pypi(notifier), "docs": check_docs(notifier),
            "chequeado_en": datetime.now().isoformat(timespec="seconds")}


def cambios_recientes(limite: int = 20) -> list:
    """Para la sección de monitoreo del panel."""
    try:
        _init_table()
        with ac_db.connect() as conn:
            filas = conn.execute(
                "SELECT detectado_en, canal, clave, valor_anterior, valor_nuevo "
                "FROM ppi_api_changes ORDER BY id DESC LIMIT ?", (limite,)
            ).fetchall()
        return [{"detectado_en": a, "canal": b, "clave": c, "anterior": d, "nuevo": e}
                for a, b, c, d, e in filas]
    except Exception as e:
        return [{"error": str(e)}]


class PPIAPIWatcher:
    """Hilo daemon que corre los canales A y B cada CHECK_INTERVAL_HOURS.
    El canal C (firma de esquema) no necesita hilo: se alimenta solo con el
    tráfico real del bot."""

    def __init__(self, notifier=None):
        self.notifier = notifier
        self._stop = threading.Event()
        self._thread = None

    def _loop(self):
        # Primer chequeo a los 60 segundos de arrancar: no compite con el
        # arranque del bot, pero tampoco espera 12 horas para la primera vez.
        if self._stop.wait(60):
            return
        while not self._stop.is_set():
            try:
                check_all(self.notifier)
            except Exception as e:
                logger.error("Error en el vigilante de la API de PPI: %s", e)
            self._stop.wait(CHECK_INTERVAL_HOURS * 3600)

    def start(self):
        if not WATCH_ENABLED:
            logger.info("PPI_API_WATCH_ENABLED=false — vigilancia de la API desactivada.")
            return
        _init_table()
        self._thread = threading.Thread(target=self._loop, name="ppi_api_watch", daemon=True)
        self._thread.start()
        logger.info("Vigilante de cambios de la API de PPI activo (cada %sh).", CHECK_INTERVAL_HOURS)

    def stop(self):
        self._stop.set()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(check_all(), indent=2, ensure_ascii=False))
